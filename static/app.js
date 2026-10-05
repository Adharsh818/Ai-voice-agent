/**
 * Emma — single-circle talk page.
 *
 * Click the circle to start a call, click again to hang up. The circle shows
 * the call state (connecting / listening / thinking / speaking) and moves with
 * whoever is talking; one faint caption line shows the latest words. When a
 * call can't start or stops unexpectedly the page says why in one line, with a
 * button to try again:
 *
 *   busy     Emma is already on a call (the server sends {"type":"busy"})
 *   lost     the line dropped mid-call, or the server couldn't be reached
 *   blocked  no microphone permission, or no microphone at all
 *
 * Audio: mic -> capture worklet (16 kHz, 20 ms frames) -> WebSocket.
 * Emma's PCM arrives as binary frames tagged with a turn id and goes straight
 * into the playback worklet; a `stop` event flushes it instantly (barge-in).
 *
 * Microphone processing (R3.3), chosen for Indian-accented English on a headset:
 *   echoCancellation  on   costs nothing on a headset, and on laptop speakers it
 *                          is what stops Emma's own voice coming back as words.
 *   noiseSuppression  off  a headset mic is close to the mouth, so there is
 *                          little noise to remove, and the suppressor's gating
 *                          clips soft consonants and word endings ("Rao" ->
 *                          "Ra", "fifteen" -> "fifty"); the recogniser is
 *                          trained on noisy audio and does better without it.
 *   autoGainControl   on   headsets differ a lot in level; a steady level keeps
 *                          the recogniser and the local barge-in cue reliable.
 * For quick comparisons without a code change: /?ns=1, /?agc=0, /?aec=0.
 *
 * Resampling: the AudioContext runs at 16 kHz where the browser supports it,
 * so the browser's own band-limited resampler converts the mic; otherwise the
 * capture worklet low-passes before decimating (no aliasing either way).
 *
 * Realism (docs/NORTH_STAR.md): Emma's voice and the clinic's sounds go through
 * an optional phone-line filter, so it sounds like a real call to a real
 * clinic. There is no background bed: only typing and an occasional door,
 * chair or footsteps while Emma's line is active; see ambience.js. Settings
 * come from /client-config.
 */
import { Ambience, phoneLine } from './ambience.js';

const orb = document.getElementById('orb');
const caption = document.getElementById('caption');
const action = document.getElementById('action');

const VAD_RMS = 0.02;          // local "caller is talking" energy threshold
const VAD_FRAMES = 2;          // consecutive 20 ms frames above it
const DUCK_GAIN = 0.3;         // Emma's volume while a barge-in is being confirmed
const DUCK_RELEASE_MS = 600;   // restore if the server does not confirm
const CAPTURE_RATE = 16000;

const IN_CALL = ['connecting', 'listening', 'thinking', 'speaking'];
const MESSAGES = {
  busy: 'Emma is on another call right now. Try again in a minute.',
  lost: 'The line dropped.',
  unreachable: "Couldn't reach Emma. Check the server is running, then try again.",
  blocked: 'Microphone blocked. Allow it from the icon in the address bar, then try again.',
  nomic: 'No microphone found. Plug in a headset, then try again.',
  audio: "Couldn't start audio in this browser.",
};

let ctx, micStream, capture, player, sink, ws, ambience;
let state = 'idle';
let minTurn = 0;
let micLevel = 0, playLevel = 0, shownLevel = 0;
let loudFrames = 0, duckTimer = null;
let speakingTurn = null;
let opened = false;        // the socket opened (a later close is a dropped line)
let ending = false;        // we hung up, or Emma said goodbye
let busyMessage = null;    // the server refused the call
let callSeq = 0;           // bumped on hang-up, so a call still starting up stops quietly

function setState(next) {
  const was = state;
  state = next;
  if (was !== next) window.dispatchEvent(new CustomEvent('emma-call-state', { detail: { state: next, was } }));
  orb.dataset.state = next;
  const inCall = IN_CALL.includes(next);
  orb.setAttribute('aria-pressed', String(inCall));
  orb.setAttribute('aria-label', inCall ? 'End call' : 'Start call');
}

function setCaption(text, who) {
  caption.textContent = text || '';
  caption.className = ['user', 'error', 'notice'].includes(who) ? who : '';
}

/** A one-line reason the call isn't running, with a button to try again. */
function showProblem(kind, message, button) {
  setState(kind);
  setCaption(message, kind === 'busy' ? 'notice' : 'error');
  action.textContent = button;
  action.hidden = false;
}

function hideProblem() {
  action.hidden = true;
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function micConstraints() {
  const q = new URLSearchParams(location.search);
  const flag = (name, fallback) => (q.has(name) ? q.get(name) === '1' : fallback);
  return {
    echoCancellation: flag('aec', true),
    // On (1 Oct voice test): on laptop speakers it removes the echo that
    // echo cancellation leaves behind; Emma was hearing her own greeting.
    noiseSuppression: flag('ns', true),
    autoGainControl: flag('agc', true),
    channelCount: 1,
  };
}

// ------------------------------------------------------------------ call setup
/**
 * An AudioContext at the device's own rate, with the worklet's anti-aliased
 * resampler. Chrome's echo cancellation worked worse with a forced 16 kHz
 * context (1 Oct voice test: Emma heard her own voice), so /?rate16=1 keeps
 * that only as an option to compare.
 */
async function openAudio(stream) {
  const rates = new URLSearchParams(location.search).has('rate16') ? [CAPTURE_RATE, undefined] : [undefined];
  for (const rate of rates) {
    let context;
    try {
      context = new AudioContext(rate ? { sampleRate: rate, latencyHint: 'interactive' }
        : { latencyHint: 'interactive' });
      const source = context.createMediaStreamSource(stream);
      await Promise.all([
        context.audioWorklet.addModule('/static/audio-worklet-processor.js'),
        context.audioWorklet.addModule('/static/playback-worklet.js'),
      ]);
      return { context, source };
    } catch (err) {
      try { context && context.close(); } catch (_) {}
      if (!rate) throw err;
      console.info('16 kHz audio context unavailable; resampling in the worklet', err);
    }
  }
  throw new Error('no audio context');
}

/**
 * Start a call. `path` is the call's WebSocket path: /ws/voice for the talk
 * page (the default), /ws/outbound?job=... when the patient page answers
 * Emma's recovery call.
 */
export async function startCall(path) {
  const call = ++callSeq;
  const cancelled = () => call !== callSeq;
  hideProblem();
  setState('connecting');
  setCaption('Connecting…', 'notice');
  minTurn = 0;
  speakingTurn = null;
  opened = ending = false;
  busyMessage = null;

  let source;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: micConstraints() });
    if (cancelled()) { stream.getTracks().forEach((t) => t.stop()); return; }
    micStream = stream;
  } catch (err) {
    if (cancelled()) return;
    console.error(err);
    teardown();
    const nomic = err.name === 'NotFoundError' || err.name === 'OverconstrainedError';
    showProblem('blocked', nomic ? MESSAGES.nomic : MESSAGES.blocked, 'Try again');
    return;
  }
  try {
    const audio = await openAudio(micStream);
    if (cancelled()) { audio.context.close(); return; }
    ({ context: ctx, source } = audio);
  } catch (err) {
    if (cancelled()) return;
    console.error(err);
    teardown();
    showProblem('lost', MESSAGES.audio, 'Try again');
    return;
  }

  let cfg = {};
  try { cfg = await (await fetch('/client-config', { cache: 'no-cache' })).json(); } catch (_) {}
  if (cancelled()) return;
  // Everything Emma's side of the line produces goes through one output,
  // band-limited like a phone call when phone_line is on.
  const out = cfg.phone_line ? phoneLine(ctx, ctx.destination) : ctx.destination;

  player = new AudioWorkletNode(ctx, 'pcm-player', { outputChannelCount: [1] });
  player.connect(out);
  player.port.onmessage = (e) => onPlayer(e.data);
  ambience = cfg.ambience && cfg.ambience.enabled
    ? new Ambience(ctx, out, { eventDb: cfg.ambience.event_db })
    : null;

  capture = new AudioWorkletNode(ctx, 'pcm-capture-processor');
  // Keep the capture node pulled by the graph without making it audible.
  sink = ctx.createGain();
  sink.gain.value = 0;
  source.connect(capture);
  capture.connect(sink).connect(ctx.destination);
  capture.port.onmessage = (e) => onMicFrame(e.data);

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  // /?mode=listen (with DEV_CAPTURE_AUDIO=true): Emma only listens, for recording STT test audio.
  const mode = new URLSearchParams(location.search).get('mode') === 'listen' ? '?mode=listen' : '';
  ws = new WebSocket(`${proto}://${location.host}${path || `/ws/voice${mode}`}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    opened = true;
    send({ type: 'hello', v: 2 });
    setState('listening');
    setCaption('');
    if (ambience) ambience.start().catch((err) => console.warn('ambience', err));
  };
  ws.onmessage = (e) => (typeof e.data === 'string' ? onEvent(JSON.parse(e.data)) : onAudio(e.data));
  ws.onclose = onSocketClosed;
  ws.onerror = () => {};       // onclose follows and says what happened
}

function onSocketClosed() {
  if (!IN_CALL.includes(state)) return;      // already torn down
  const wasOpen = opened;
  teardown();
  if (busyMessage !== null) {
    showProblem('busy', busyMessage || MESSAGES.busy, 'Try again');
  } else if (ending) {
    setState('idle');
  } else {
    showProblem('lost', wasOpen ? MESSAGES.lost : MESSAGES.unreachable, wasOpen ? 'Call again' : 'Try again');
  }
}

export function endCall() {
  callSeq++;
  ending = true;
  send({ type: 'end' });
  teardown();
  setState('idle');
  setCaption('');
}

function teardown() {
  try { ambience && ambience.stop(); } catch (_) {}
  ambience = null;
  if (ws) {
    ws.onclose = ws.onmessage = ws.onerror = null;
    try { ws.close(); } catch (_) {}
  }
  try { micStream && micStream.getTracks().forEach((t) => t.stop()); } catch (_) {}
  try { ctx && ctx.close(); } catch (_) {}
  ws = ctx = micStream = capture = player = sink = null;
  micLevel = playLevel = 0;
  speakingTurn = null;
  clearTimeout(duckTimer);
}

function fail(message) {
  teardown();
  showProblem('error', message, 'Try again');
}

// ------------------------------------------------------------------ mic
function onMicFrame({ buffer, rms }) {
  micLevel = rms;
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(buffer);

  // Local barge-in cue: duck Emma immediately; the server confirms with words.
  if (speakingTurn !== null && rms > VAD_RMS) {
    if (++loudFrames === VAD_FRAMES) {
      player && player.port.postMessage({ type: 'duck', gain: DUCK_GAIN });
      send({ type: 'vad', speaking: true });
      clearTimeout(duckTimer);
      duckTimer = setTimeout(() => player && player.port.postMessage({ type: 'duck', gain: 1 }), DUCK_RELEASE_MS);
    }
  } else if (rms <= VAD_RMS) {
    loudFrames = 0;
  }
}

// ------------------------------------------------------------------ server -> browser
function onAudio(buf) {
  const view = new DataView(buf);
  if (view.getUint8(0) !== 0x01) return;
  const turn = view.getUint32(1, true);
  if (turn < minTurn || !player) return;
  const pcm = new Int16Array(buf.slice(5));
  player.port.postMessage({ type: 'pcm', turn, pcm }, [pcm.buffer]);
}

function onEvent(ev) {
  switch (ev.type) {
    case 'busy':
      // The socket closes next; onSocketClosed shows this.
      busyMessage = ev.message || '';
      break;
    case 'state':
      if (ev.state === 'thinking' && state !== 'speaking') setState('thinking');
      if (ev.state === 'listening' && speakingTurn === null) setState('listening');
      break;
    case 'caption':
      setCaption(ev.text, ev.who);
      break;
    case 'turn':
      if (ev.phase === 'audio_done') player && player.port.postMessage({ type: 'end', turn: ev.turn });
      break;
    case 'stop':
      if (ambience) ambience.stopTyping();
      minTurn = Math.max(minTurn, ev.turn + 1);
      player && player.port.postMessage({ type: 'flush' });
      clearTimeout(duckTimer);
      speakingTurn = null;
      setState('listening');
      break;
    case 'metrics':
      if (ev.perceived_ms != null) {
        console.debug(`turn ${ev.turn} tier ${ev.tier}: perceived ${ev.perceived_ms} ms `
          + `(first audio ${ev.first_audio_ms} ms ${ev.first_audio_source}, pause ${ev.pause_ms ?? 0} ms`
          + `${ev.streamed ? ', streamed' : ''}, ${ev.detect || 'n/a'})`);
      }
      break;
    case 'error':
      fail(ev.message || 'Something went wrong.');
      break;
    case 'sfx':
      // Keyboard typing while Emma checks something (R6).
      if (ev.name === 'typing' && ambience) {
        ambience.typing(ev.after_ms || 0, ev.duration_ms || 1500, { turn: ev.turn || 0, untilSpeech: !!ev.until_speech });
      }
      break;
    case 'bye':
      ending = true;
      setTimeout(() => { if (IN_CALL.includes(state)) { teardown(); setState('idle'); } }, 300);
      break;
  }
}

function onPlayer(msg) {
  switch (msg.type) {
    case 'level':
      playLevel = msg.value;
      break;
    case 'started':
      speakingTurn = msg.turn;
      setState('speaking');
      if (ambience) ambience.speechStarted(msg.turn);   // the line opens; note-taking typing stops
      send({ type: 'playback', turn: msg.turn, event: 'started' });
      break;
    case 'ended':
      if (speakingTurn === msg.turn) speakingTurn = null;
      if (ambience) ambience.speaking(false);
      send({ type: 'playback', turn: msg.turn, event: 'ended', played_ms: msg.playedMs });
      if (ws) setState('listening');
      break;
    case 'flushed':
      if (ambience) ambience.speaking(false);
      send({ type: 'playback', turn: msg.turn, event: 'interrupted', played_ms: msg.playedMs });
      break;
  }
}

// ------------------------------------------------------------------ animation
function frame() {
  const target = Math.min(1, Math.max(micLevel * 6, playLevel * 5));
  // Fast attack, slow release.
  shownLevel += (target - shownLevel) * (target > shownLevel ? 0.5 : 0.08);
  orb.style.setProperty('--lvl', shownLevel.toFixed(3));
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// On the patient page (data-page="patient") calls only start from its Answer
// button; the circle just hangs up.
const PATIENT_PAGE = document.body.dataset.page === 'patient';
orb.addEventListener('click', () => {
  if (IN_CALL.includes(state)) endCall();
  else if (!PATIENT_PAGE) startCall();
});
action.addEventListener('click', () => {
  if (PATIENT_PAGE) { hideProblem(); setState('idle'); setCaption(''); } else startCall();
});

// Typed input for testing without a microphone: emma.say("book an appointment")
window.emma = { say: (text) => send({ type: 'text', text }) };
