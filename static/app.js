/**
 * Emma — single-circle talk page.
 *
 * Click the circle to start a call, click again to hang up. The circle shows
 * the call state (listening / thinking / speaking) and moves with whoever is
 * talking; one faint caption line shows the latest words.
 *
 * Audio: mic -> capture worklet (16 kHz, 20 ms frames) -> WebSocket.
 * Emma's PCM arrives as binary frames tagged with a turn id and goes straight
 * into the playback worklet; a `stop` event flushes it instantly (barge-in).
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

const VAD_RMS = 0.02;          // local "caller is talking" energy threshold
const VAD_FRAMES = 2;          // consecutive 20 ms frames above it
const DUCK_GAIN = 0.3;         // Emma's volume while a barge-in is being confirmed
const DUCK_RELEASE_MS = 600;   // restore if the server does not confirm

let ctx, micStream, capture, player, sink, ws, ambience;
let state = 'idle';
let minTurn = 0;
let micLevel = 0, playLevel = 0, shownLevel = 0;
let loudFrames = 0, duckTimer = null;
let speakingTurn = null;

function setState(next) {
  state = next;
  orb.dataset.state = next;
  const inCall = !['idle', 'error'].includes(next);
  orb.setAttribute('aria-pressed', String(inCall));
  orb.setAttribute('aria-label', inCall ? 'End call' : 'Start call');
}

function setCaption(text, who) {
  caption.textContent = text || '';
  caption.className = who === 'user' || who === 'error' ? who : '';
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// ------------------------------------------------------------------ call setup
async function startCall() {
  setState('connecting');
  setCaption('');
  minTurn = 0;
  speakingTurn = null;
  try {
    ctx = new AudioContext({ latencyHint: 'interactive' });
    await Promise.all([
      ctx.audioWorklet.addModule('/static/audio-worklet-processor.js'),
      ctx.audioWorklet.addModule('/static/playback-worklet.js'),
    ]);
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
  } catch (err) {
    console.error(err);
    fail(err.name === 'NotAllowedError' || err.name === 'NotFoundError'
      ? 'Microphone blocked. Allow mic access in your browser (icon in the address bar), then click again.'
      : 'Could not start audio.');
    return;
  }

  let cfg = {};
  try { cfg = await (await fetch('/client-config', { cache: 'no-cache' })).json(); } catch (_) {}
  // Everything Emma's side of the line produces goes through one output,
  // band-limited like a phone call when phone_line is on.
  const out = cfg.phone_line ? phoneLine(ctx, ctx.destination) : ctx.destination;

  player = new AudioWorkletNode(ctx, 'pcm-player', { outputChannelCount: [1] });
  player.connect(out);
  player.port.onmessage = (e) => onPlayer(e.data);
  ambience = cfg.ambience && cfg.ambience.enabled
    ? new Ambience(ctx, out, { eventDb: cfg.ambience.event_db })
    : null;

  capture = new AudioWorkletNode(ctx, 'pcm-capture-processor', {
    processorOptions: { nativeSampleRate: ctx.sampleRate },
  });
  // Keep the capture node pulled by the graph without making it audible.
  sink = ctx.createGain();
  sink.gain.value = 0;
  ctx.createMediaStreamSource(micStream).connect(capture);
  capture.connect(sink).connect(ctx.destination);
  capture.port.onmessage = (e) => onMicFrame(e.data);

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  // /?mode=listen (with DEV_CAPTURE_AUDIO=true): Emma only listens, for recording STT test audio.
  const mode = new URLSearchParams(location.search).get('mode') === 'listen' ? '?mode=listen' : '';
  ws = new WebSocket(`${proto}://${location.host}/ws/voice${mode}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {
    send({ type: 'hello', v: 2 });
    setState('listening');
    if (ambience) ambience.start().catch((err) => console.warn('ambience', err));
  };
  ws.onmessage = (e) => (typeof e.data === 'string' ? onEvent(JSON.parse(e.data)) : onAudio(e.data));
  ws.onclose = () => { if (state !== 'idle' && state !== 'error') teardown(); };
  ws.onerror = () => fail('Connection to Emma lost.');
}

function endCall() {
  send({ type: 'end' });
  teardown();
}

function teardown() {
  try { ambience && ambience.stop(); } catch (_) {}
  ambience = null;
  try { ws && ws.close(); } catch (_) {}
  try { micStream && micStream.getTracks().forEach((t) => t.stop()); } catch (_) {}
  try { ctx && ctx.close(); } catch (_) {}
  ws = ctx = micStream = capture = player = sink = null;
  micLevel = playLevel = 0;
  clearTimeout(duckTimer);
  if (state !== 'error') setState('idle');
}

function fail(message) {
  teardown();
  setState('error');
  setCaption(message, 'error');
  setTimeout(() => { if (state === 'error') setState('idle'); }, 2500);
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
        console.debug(`turn ${ev.turn} tier ${ev.tier}: perceived ${ev.perceived_ms} ms (${ev.first_audio_source})`);
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
      setTimeout(teardown, 300);
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

orb.addEventListener('click', () => {
  if (state === 'idle' || state === 'error') startCall();
  else endCall();
});

// Typed input for testing without a microphone: emma.say("book an appointment")
window.emma = { say: (text) => send({ type: 'text', text }) };
