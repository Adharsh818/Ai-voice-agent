/**
 * The demo patient phone (/patient, staff login required).
 *
 * When Emma starts a recovery call, this tab rings: "Incoming call, Pearl
 * Dental". Answer opens the call over /ws/outbound (same audio path as the
 * talk page, so Emma sounds exactly the same); Decline, or no answer within
 * the clinic's ring time, ends the attempt and the front desk gets a task.
 * The page polls the server once a second for a ringing call.
 */
import { startCall } from './app.js';

const POLL_MS = 1000;
const incoming = document.getElementById('incoming');
const waiting = document.getElementById('waiting');
const answerBtn = document.getElementById('answer');
const declineBtn = document.getElementById('decline');

let ring = null;          // the call ringing now, from /patient/api/ring
let inCall = false;
let tone = null;          // ringtone (only once the page has had a click: browsers block sound before)

function show(r) {
  ring = r;
  document.getElementById('incoming-from').textContent = r.from || 'Pearl Dental';
  document.getElementById('incoming-to').textContent = r.to_name ? `to ${r.to_name} · ${r.to_masked}` : r.to_masked || '';
  answerBtn.disabled = declineBtn.disabled = false;
  incoming.hidden = false;
  waiting.hidden = true;
  startTone();
}

function hide() {
  ring = null;
  incoming.hidden = true;
  stopTone();
  waiting.hidden = inCall;
}

async function poll() {
  if (!inCall) {
    try {
      const res = await fetch('/patient/api/ring', { credentials: 'same-origin', cache: 'no-store' });
      if (res.status === 401 || res.status === 503) {
        location.replace('/dashboard/login');
        return;
      }
      const data = await res.json();
      const r = data.ringing;
      // A retried job rings again with the same id and a new token: compare both.
      if (r && (!ring || ring.job_id !== r.job_id || ring.token !== r.token)) show(r);
      else if (!r && ring) hide();
    } catch (err) {
      console.warn('ring poll failed', err);
    }
  }
}

function schedulePoll() {
  poll().finally(() => setTimeout(schedulePoll, POLL_MS));
}

// Answer or Decline for a ring the server no longer has: show what is ringing now instead.
function refreshRing() {
  ring = null;
  poll();
}

async function postRing(path, r) {
  const res = await fetch(path, {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: r.job_id, token: r.token }),
  });
  return res.ok;
}

let lastAnswered = null;    // the ring being answered, for "Type instead" if the microphone fails

answerBtn.addEventListener('click', async () => {
  if (!ring) return;
  const r = ring;
  answerBtn.disabled = declineBtn.disabled = true;
  // Take the ring before asking for the microphone, so a slow or refused microphone can't
  // let it time out as "no answer" while the patient switches to typing.
  let claimed = false;
  try { claimed = await postRing('/patient/api/answer', r); } catch (err) { console.warn('answer failed', err); }
  if (!claimed) {
    refreshRing();
    return;
  }
  lastAnswered = r;
  inCall = true;
  hide();
  startCall(`/ws/outbound?job=${encodeURIComponent(r.job_id)}&token=${encodeURIComponent(r.token)}`);
});

// The microphone failed after Answer: carry on typing (the call is still waiting to connect).
window.addEventListener('emma-type-instead', () => {
  if (!lastAnswered) return;
  const r = lastAnswered;
  inCall = true;
  startCall(`/ws/outbound?job=${encodeURIComponent(r.job_id)}&token=${encodeURIComponent(r.token)}`, { typed: true });
});

declineBtn.addEventListener('click', async () => {
  if (!ring) return;
  const r = ring;
  answerBtn.disabled = declineBtn.disabled = true;
  let declined = false;
  try { declined = await postRing('/patient/api/decline', r); } catch (err) { console.warn('decline failed', err); }
  if (declined) hide();
  else refreshRing();
});

window.addEventListener('emma-call-state', (e) => {
  // 'blocked' (no microphone) keeps the ringing screen away, so "Type instead" stays usable.
  if (['idle', 'lost', 'error', 'busy'].includes(e.detail.state) && inCall) {
    inCall = false;
    waiting.hidden = !incoming.hidden;
  }
});

// ------------------------------------------------------------------ ringtone
let audio = null;
document.addEventListener('click', () => {
  if (!audio) {
    try { audio = new AudioContext(); } catch (_) { audio = null; }
  }
}, { once: true });

function startTone() {
  if (!audio || tone) return;
  audio.resume().catch(() => {});
  const gain = audio.createGain();
  gain.gain.value = 0;
  gain.connect(audio.destination);
  const a = audio.createOscillator();
  const b = audio.createOscillator();
  a.frequency.value = 400;
  b.frequency.value = 450;
  a.connect(gain);
  b.connect(gain);
  a.start();
  b.start();
  // UK/India-style double ring: 0.4 s on, 0.2 s off, 0.4 s on, 2 s off.
  let t = audio.currentTime;
  for (let i = 0; i < 20; i++, t += 3) {
    for (const [on, off] of [[0, 0.4], [0.6, 1.0]]) {
      gain.gain.setValueAtTime(0.08, t + on);
      gain.gain.setValueAtTime(0, t + off);
    }
  }
  tone = { a, b, gain };
}

function stopTone() {
  if (!tone) return;
  try { tone.a.stop(); tone.b.stop(); tone.gain.disconnect(); } catch (_) {}
  tone = null;
}

schedulePoll();
