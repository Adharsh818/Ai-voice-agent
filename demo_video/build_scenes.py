"""
Writes the demo video's HTML (index.html and compositions/*.html) from assets/audio/manifest.json,
which holds every clip's length and the timing of every spoken word (Deepgram, matched to the script).
Run again after re-making any audio or word timings:

    python build_scenes.py

Storyline A (STORYLINES.md): the real call a7e2b542 re-enacted (the number swapped for the demo one).
The call's words pop into chat bubbles as they're spoken while a booking card fills in; mid-call a cut
to the Live call tab; then a narrated tour of every tab where the camera and a spotlight move to each
part exactly when the narrator names it; narration captions pop word by word; the end card's words
pop with the voice.
"""

import html
import json
import math
from pathlib import Path

HERE = Path(__file__).parent
M = json.loads((HERE / "assets/audio/manifest.json").read_text())
CALL, NAR = M["call"], M["narration"]
W, H = 1920, 1080

GAP = 0.55                 # between call lines (the real replies took ~1.6 s; shortened, and said so on screen)
LIVE_AFTER = 13            # cut to the Live call tab after "Whitefield."
INTRO = 8.0
T0 = INTRO                 # the call scene starts as the intro ends


def esc(s):
    return html.escape(s, quote=True)


def r(x):
    return round(x, 3)


def anchor(key, word_index):
    """Seconds into narration `key` when its word number `word_index` is spoken."""
    return NAR[key]["words"][word_index]["s"]


def find(key, word):
    """Index of the first word in narration `key` that starts with `word`."""
    for i, w in enumerate(NAR[key]["words"]):
        if w["w"].lower().strip(".,:;'?!").startswith(word.lower()):
            return i
    raise KeyError(f"{word!r} not in {key}")


# ------------------------------------------------------------------ timing of the call
lines, t = [], 0.7
live = None
for i, c in enumerate(CALL):
    lines.append({**c, "i": i, "start": r(t), "end": r(t + c["seconds"])})
    t += c["seconds"] + GAP
    if i == LIVE_AFTER:
        live = {"start": r(t + 0.1), "voice": 0.9}
        live["dur"] = r(live["voice"] + NAR["n_live"]["seconds"] + 1.0)
        t = live["start"] + live["dur"] + 0.4
CALL_DUR = r(t + 1.4)

# ------------------------------------------------------------------ the tour: regions named by the narration
# (narration, screenshot, label, [(word the narrator says, (x, y, w, h) in the 1920x1080 screenshot)])
TOUR = [
    ("n_today", "05_today", "Today", [
        ("live", (350, 165, 720, 417)), ("appointments", (1090, 165, 720, 417)), ("open", (350, 602, 350, 304)),
        ("recent", (720, 602, 350, 304)), ("doctors", (1090, 602, 350, 304)), ("whether", (1460, 602, 350, 304))]),
    ("n_calendar", "06_appointments", "Appointments", [
        ("doctor's", (418, 196, 1464, 62)), ("booking", (1396, 552, 240, 46)), ("Reddy", (1394, 196, 246, 860))]),
    ("n_tasks", "07_tasks", "Tasks", [
        ("task", (350, 165, 1080, 192)), ("callback", (350, 245, 1080, 112))]),
    ("n_calls", "08_calls", "Calls", [
        ("Every", (350, 240, 456, 106)), ("transcript", (838, 620, 1012, 420)), ("outcome", (838, 372, 1012, 228)),
        ("appointment", (848, 556, 640, 34))]),
    ("n_recovery", "11_recovery_preview", "Recovery", [
        ("doctor", (382, 266, 306, 68)), ("block", (382, 467, 1257, 70)), ("affected", (400, 585, 1220, 142)),
        ("Emma", (1356, 740, 266, 56))]),
    ("n_system", "10_system", "System", [
        ("how", (697, 137, 1093, 625)), ("voice", (350, 193, 312, 113)), ("doctors", (350, 382, 312, 56)),
        ("holidays", (350, 438, 312, 58)), ("staff", (350, 496, 312, 56)), ("patient", (350, 610, 312, 56))]),
]
LIVE_REGIONS = [("watch", (350, 135, 1150, 915)), ("every", (1523, 137, 358, 475))]

tour, t = [], 0.0
for key, shot, label, regions in TOUR:
    voice = r(t + 0.9)
    dur = r(0.9 + NAR[key]["seconds"] + 1.2)
    tour.append({"key": key, "shot": shot, "label": label, "start": r(t), "dur": dur, "voice": voice,
                 "focus": [(r(voice + anchor(key, find(key, word))), box) for word, box in regions]})
    t += dur
TOUR_DUR = r(t)
TOUR_T0 = r(T0 + CALL_DUR)
OUTRO_T0 = r(TOUR_T0 + TOUR_DUR)
OUTRO_VOICE = 0.5
OUTRO_DUR = r(OUTRO_VOICE + NAR["n_outro"]["seconds"] + 3.0)
TOTAL = r(OUTRO_T0 + OUTRO_DUR)

# Booking card: (line, field, value) - the value lands as the line finishes.
FIELDS = [(5, "Name", "Adharsh"), (9, "Phone", "98450 12345"), (11, "Visit", "Check-up"),
          (13, "Branch", "Whitefield"), (16, "Day", "Wed 28 Oct"), (18, "Doctor", "Dr Reddy"),
          (19, "Time", "12 noon")]
BOOKED_AT = lines[22]["start"]

# ------------------------------------------------------------------ helpers shared by scenes
HEAD = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
"""


def scene(cid, css, body, js, dur, bg="#f5f5f7"):
    return f"""<template>
  <style>
    #root {{ position: absolute; inset: 0; font-family: "Inter"; color: #1d1d1f; background: {bg}; overflow: hidden; }}
{css}
  </style>
  <div id="root" data-composition-id="{cid}" data-width="1920" data-height="1080" data-duration="{dur}">
{body}
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
{js}
    window.__timelines["{cid}"] = tl;
  </script>
</template>
"""


def camera_for(box, top_room=0.47):
    """x, y, scale that bring `box` to the middle of the frame (a little above it, clear of the captions)."""
    x, y, w, h = box
    s = max(1.0, min(1.55, 0.78 * W / w, 0.62 * H / h))
    cx, cy = x + w / 2, y + h / 2
    tx = min(0.0, max(W - W * s, W / 2 - s * cx))
    ty = min(0.0, max(H - H * s, H * top_room - s * cy))
    return r(tx), r(ty), r(s)


def spotlight(prefix, focus, end, js):
    """Rings over each region with the camera easing to it when the narrator names it. Returns the ring HTML."""
    rings = []
    js.append(f'    tl.set("#{prefix}-cam", {{ x: 0, y: 0, scale: 1 }}, 0);')
    for n, (at, box) in enumerate(focus):
        x, y, w, h = box
        rid = f"{prefix}-r{n}"
        rings.append(f'<div id="{rid}" class="ring" style="left: {x - 10}px; top: {y - 10}px; width: {w + 20}px; height: {h + 20}px"></div>')
        tx, ty, s = camera_for(box)
        nxt_at = focus[n + 1][0] if n + 1 < len(focus) else end
        move = r(max(0.3, min(0.75, nxt_at - at - 0.05)))
        js.append(f'    tl.to("#{prefix}-cam", {{ x: {tx}, y: {ty}, scale: {s}, duration: {move}, ease: "power2.inOut" }}, {r(at - 0.15)});')
        js.append(f'    tl.fromTo("#{rid}", {{ opacity: 0, scale: 1.04 }}, {{ opacity: 1, scale: 1, duration: 0.4, ease: "expo.out" }}, {r(at)});')
        nxt = focus[n + 1][0] if n + 1 < len(focus) else end
        js.append(f'    tl.to("#{rid}", {{ opacity: 0, duration: 0.2, ease: "power2.in" }}, {r(nxt - 0.1)});')
    return "".join(rings)


SHOT_CSS = """
    .shotwrap { position: absolute; inset: 0; }
    .cam { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; transform-origin: 0 0; }
    .shot { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; }
    .ring { position: absolute; opacity: 0; border: 5px solid #0071e3; border-radius: 22px;
            box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.24), 0 0 0 10px rgba(0, 113, 227, 0.18); }
    .chipbar { position: absolute; left: 0; right: 0; top: 30px; text-align: center; }
    .chip { display: inline-flex; align-items: center; gap: 14px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18);
            font-size: 30px; font-weight: 800; letter-spacing: -0.02em; color: #fff; background: #1d1d1f;
            padding: 12px 26px 12px 16px; border-radius: 999px; }
    .chip .n { font-size: 28px; font-weight: 700; color: #1d1d1f; background: #fff; border-radius: 999px; padding: 4px 12px; }
"""

out = HERE / "compositions"
out.mkdir(exist_ok=True)

# ------------------------------------------------------------------ captions (one host, every narration line)
CAPTION_LINES = [("n_intro", 0.6)] + [("n_live", T0 + live["start"] + live["voice"])] + \
                [(s["key"], TOUR_T0 + s["voice"]) for s in tour]


def groups_of(words, limit=5):
    out_, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_here = w["w"].endswith((".", ",", ":", "?", "!")) or len(cur) >= limit or \
            (nxt is not None and nxt["s"] - w["e"] >= 0.15)
        if end_here or nxt is None:
            out_.append(cur)
            cur = []
    return out_


cap_html, js = [], []
gid = 0
for li, (key, at) in enumerate(CAPTION_LINES):
    gs = groups_of(NAR[key]["words"])
    # A soft white fade along the bottom edge while the narrator speaks keeps black words readable over the screens.
    s0, s1 = r(at + gs[0][0]["s"] - 0.3), r(at + gs[-1][-1]["e"] + 0.5)
    cap_html.append(f'    <div id="sc{li}" class="scrim clip" data-start="{s0}" data-duration="{r(s1 - s0)}" data-track-index="8"></div>')
    js.append(f'    tl.fromTo("#sc{li}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.3, ease: "power1.out" }}, {s0});')
    js.append(f'    tl.to("#sc{li}", {{ opacity: 0, duration: 0.3, ease: "power1.in" }}, {r(s1 - 0.3)});')
    for n, g in enumerate(gs):
        start = r(at + g[0]["s"] - 0.06)
        nxt_start = at + gs[n + 1][0]["s"] - 0.08 if n + 1 < len(gs) else at + g[-1]["e"] + 0.5
        end = r(min(at + g[-1]["e"] + 0.35, nxt_start))
        words = []
        for k, w in enumerate(g):
            wid = f"c{gid}w{k}"
            words.append(f'<span id="{wid}" class="cw">{esc(w["w"])}</span>')
            # The word pops in on its spoken time and stays lifted and bolder until the next word starts.
            on = at + w["s"]
            off = at + g[k + 1]["s"] if k + 1 < len(g) else end - 0.14
            pop = max(0.08, min(0.22, off - on - 0.02))
            js.append(f'    tl.fromTo("#{wid}", {{ opacity: 0, scale: 0.8, y: 12, filter: "blur(6px)", webkitTextStrokeWidth: "0px" }}, '
                      f'{{ opacity: 1, scale: 1.1, y: -6, filter: "blur(0px)", webkitTextStrokeWidth: "1.3px", '
                      f'duration: {r(pop)}, ease: "back.out(2)" }}, {r(on)});')
            if r(off) >= r(on + pop) + 0.01:  # a last word with no room left just fades out with its group
                js.append(f'    tl.to("#{wid}", {{ scale: 1, y: 0, webkitTextStrokeWidth: "0px", duration: 0.14, ease: "power2.out" }}, '
                          f'{r(off)});')
        cap_html.append(f'    <div id="cg{gid}" class="cg clip" data-start="{start}" data-duration="{r(end - start)}" data-track-index="9">'
                        f'<span class="pill">{" ".join(words)}</span></div>')
        js.append(f'    tl.to("#cg{gid} .pill", {{ opacity: 0, duration: 0.12, ease: "power2.in" }}, {r(end - 0.12)});')
        js.append(f'    tl.set("#cg{gid} .pill", {{ opacity: 1 }}, {start});')
        gid += 1
captions = scene("captions", """
    #root { background: transparent; }
    .scrim { position: absolute; left: 0; right: 0; bottom: 0; height: 260px; opacity: 0;
             background: linear-gradient(to top, rgba(255, 255, 255, 0.92) 0%, rgba(255, 255, 255, 0.75) 45%, rgba(255, 255, 255, 0) 100%); }
    .cg { position: absolute; left: 0; right: 0; bottom: 64px; text-align: center; overflow: visible; }
    .pill { display: inline-block; max-width: 1600px; font-size: 50px; font-weight: 600; letter-spacing: -0.01em; line-height: 1.25; }
    .cw { display: inline-block; color: #111113; -webkit-text-stroke: 0 #111113; transform-origin: 50% 80%; margin: 0 6px;
          text-shadow: 0 0 2px #fff, 0 0 6px rgba(255, 255, 255, 0.95), 0 0 18px rgba(255, 255, 255, 0.8); }
""", "\n".join(cap_html), "\n".join(js), TOTAL, bg="transparent")
(out / "captions.html").write_text(captions, encoding="utf-8", newline="\n")

# ------------------------------------------------------------------ intro
intro = scene("intro", """
    #intro-wrap { position: absolute; left: 160px; top: 270px; width: 1600px; }
    #intro-kicker { font-size: 32px; font-weight: 700; color: #0071e3; letter-spacing: -0.01em; }
    #intro-title { font-size: 150px; font-weight: 900; letter-spacing: -0.05em; line-height: 1.0; margin-top: 18px; }
    #intro-sub { font-size: 44px; color: #6e6e73; margin-top: 30px; letter-spacing: -0.015em; }
    #intro-tag { position: absolute; left: 160px; top: 720px; font-size: 28px; color: #6e6e73;
                 padding: 12px 22px; border-radius: 999px; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.08); }
""", """    <div id="intro-wrap">
      <div id="intro-kicker">Pearl Dental · front desk</div>
      <div id="intro-title">Meet Emma.</div>
      <div id="intro-sub">A voice receptionist that books real appointments on the phone.</div>
    </div>
    <div id="intro-tag">Re-enacted from a real call on 26 Oct 2026 · Emma's real voice</div>""", f"""
    tl.fromTo("#intro-sub", {{ opacity: 0.2, filter: "blur(6px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.5, ease: "power2.out" }}, 0.3);
    tl.fromTo("#intro-tag", {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: "expo.out" }}, 1.1);
    tl.to("#intro-wrap, #intro-tag", {{ opacity: 0, filter: "blur(8px)", duration: 0.25, ease: "power2.in" }}, {r(INTRO - 0.3)});""",
              INTRO)
(out / "intro.html").write_text(intro, encoding="utf-8", newline="\n")

# ------------------------------------------------------------------ call: words pop into bubbles as they're spoken
CPL = 36
y, bubbles, moves = 0, [], []
VIEW_H = 700
for c in lines:
    n = max(1, math.ceil(len(c["text"]) / CPL))
    h = n * 38 + 26 + 8
    bubbles.append((c, y))
    bottom = y + h
    if bottom > VIEW_H:
        moves.append((c["start"], -(bottom - VIEW_H)))
    y = bottom + 14

bubble_html, js = [], []
for c, top in bubbles:
    side = "emma" if c["who"] == "emma" else "caller"
    bid = f"b{c['i']:02d}"
    spans = []
    for k, w in enumerate(c["words"]):
        wid = f"{bid}w{k}"
        spans.append(f'<span id="{wid}" class="w">{esc(w["w"])}</span>')
        js.append(f'    tl.fromTo("#{wid}", {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.16, ease: "power3.out" }}, {r(c["start"] + w["s"])});')
    lead = r(max(0.0, c["start"] - 0.18))         # the bubble lands just before its first word
    bubble_html.append(
        f'          <div id="{bid}" class="bubble {side} clip" data-start="{lead}" data-duration="{r(CALL_DUR - lead)}" '
        f'data-track-index="2" style="top: {top}px"><div class="say">{" ".join(spans)}</div></div>')
    js.append(f'    tl.fromTo("#{bid} .say", {{ opacity: 0, scale: 0.94 }}, {{ opacity: 1, scale: 1, duration: 0.16, ease: "back.out(1.6)" }}, {lead});')
js.append('    tl.set("#call-dot", { scale: 1, backgroundColor: "#8e8e93" }, 0);')
for c in lines:
    reps = max(1, int(c["seconds"] / 0.5))
    color = "#0071e3" if c["who"] == "emma" else "#8e8e93"
    js.append(f'    tl.set("#call-dot", {{ backgroundColor: "{color}" }}, {c["start"]});')
    js.append(f'    tl.fromTo("#call-dot", {{ scale: 1 }}, {{ scale: 1.5, duration: 0.25, yoyo: true, repeat: {reps * 2 - 1}, ease: "sine.inOut" }}, {c["start"]});')
    js.append(f'    tl.set("#who-now", {{ textContent: "{"Emma is speaking" if c["who"] == "emma" else "Caller is speaking"}" }}, {c["start"]});')
for at, dy in moves:
    js.append(f'    tl.to("#thread", {{ y: {dy}, duration: 0.4, ease: "power3.out" }}, {at});')

field_html = []
for n, (after, name, value) in enumerate(FIELDS):
    at = lines[after]["end"] + 0.15
    fid = f"f{n}"
    field_html.append(f'        <div class="field"><span class="k">{name}</span>'
                      f'<span id="{fid}" class="v clip" data-start="{r(at)}" data-duration="{r(CALL_DUR - at)}" '
                      f'data-track-index="3"><span class="val">{esc(value)}</span><span class="tick">✓</span></span></div>')
    js.append(f'    tl.fromTo("#{fid} .val", {{ opacity: 0, x: -14, filter: "blur(4px)" }}, {{ opacity: 1, x: 0, filter: "blur(0px)", duration: 0.35, ease: "expo.out" }}, {r(at)});')
    js.append(f'    tl.fromTo("#{fid} .tick", {{ opacity: 0, scale: 0.3 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: "back.out(2.2)" }}, {r(at + 0.12)});')
js.append(f'    tl.fromTo("#stamp .stamp-in", {{ opacity: 0, scale: 1.8, rotation: -8 }}, {{ opacity: 1, scale: 1, rotation: -8, duration: 0.4, ease: "expo.out" }}, {BOOKED_AT});')
js.append(f'    tl.fromTo("#status .s-in", {{ opacity: 1 }}, {{ opacity: 0, duration: 0.15 }}, {BOOKED_AT});')
js.append('    tl.fromTo("#call-panel", { opacity: 0, filter: "blur(8px)" }, { opacity: 1, filter: "blur(0px)", duration: 0.3, ease: "power2.out" }, 0);')
js.append('    tl.fromTo("#card", { opacity: 0, x: 40 }, { opacity: 1, x: 0, duration: 0.6, ease: "expo.out" }, 0.1);')

call = scene("call", """
    #call-panel { position: absolute; left: 110px; top: 70px; width: 860px; height: 940px; }
    #call-head { display: flex; align-items: center; gap: 18px; height: 110px; }
    #call-dot { width: 18px; height: 18px; border-radius: 50%; background: #8e8e93; }
    #call-head .t { font-size: 34px; font-weight: 700; letter-spacing: -0.02em; }
    #call-head .s { font-size: 28px; color: #6e6e73; margin-top: 4px; }
    #viewport { position: absolute; left: 0; top: 130px; width: 860px; height: 760px; overflow: hidden; }
    #thread { position: absolute; left: 0; top: 0; width: 860px; height: 3000px; }
    .bubble { position: absolute; max-width: 660px; }
    .bubble.caller { left: 0; }
    .bubble.emma { right: 0; text-align: right; }
    .bubble .say { display: inline-block; font-size: 28px; line-height: 38px; padding: 13px 22px; border-radius: 24px;
                   text-align: left; letter-spacing: -0.012em; transform-origin: 0 100%; }
    .bubble.emma .say { transform-origin: 100% 100%; }
    .bubble .w { display: inline-block; }
    .bubble.caller .say { background: #e9e9ed; color: #1d1d1f; }
    .bubble.emma .say { background: #0071e3; color: #fff; }
    #note { position: absolute; left: 110px; bottom: 34px; font-size: 28px; color: #6e6e73; }
    #card { position: absolute; left: 1050px; top: 130px; width: 760px; padding: 44px 48px; border-radius: 34px;
            background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.06), 0 30px 60px rgba(0,0,0,.06); }
    #card .title { font-size: 40px; font-weight: 800; letter-spacing: -0.03em; }
    #card .hint { font-size: 28px; color: #6e6e73; margin: 8px 0 26px; }
    .field { display: flex; align-items: center; height: 70px; border-top: 1px solid rgba(0,0,0,.08); font-size: 30px; }
    .field .k { width: 210px; color: #6e6e73; font-size: 28px; }
    .field .v { display: flex; align-items: center; gap: 16px; font-weight: 600; letter-spacing: -0.015em; }
    .field .val, .field .tick { display: inline-block; }
    .field .tick { color: #28a745; font-weight: 800; }
    #status { position: relative; margin-top: 26px; height: 64px; font-size: 28px; color: #6e6e73; }
    #stamp { position: absolute; left: 0; top: -6px; }
    #stamp .stamp-in { display: inline-block; font-size: 40px; font-weight: 900; color: #28a745; letter-spacing: 0.06em;
                       border: 5px solid #28a745; border-radius: 14px; padding: 4px 22px; }
""", f"""    <div id="call-panel">
      <div id="call-head"><div id="call-dot"></div><div><div class="t">Call with Emma</div><div class="s" id="who-now">Connecting…</div></div></div>
      <div id="viewport" data-layout-allow-overflow>
        <div id="thread">
{chr(10).join(bubble_html)}
        </div>
      </div>
    </div>
    <div id="card">
      <div class="title">The booking</div>
      <div class="hint">Python fills this in as Emma hears each detail.</div>
{chr(10).join(field_html)}
      <div id="status"><span class="s-in">Waiting for a yes to the summary…</span>
        <div id="stamp" class="clip" data-start="{BOOKED_AT}" data-duration="{r(CALL_DUR - BOOKED_AT)}" data-track-index="4"><span class="stamp-in">BOOKED</span></div></div>
    </div>
    <div id="note">Pauses between turns shortened · phone number replaced with a demo number</div>""",
             "\n".join(js), CALL_DUR)
(out / "call.html").write_text(call, encoding="utf-8", newline="\n")

# ------------------------------------------------------------------ live: the Live call tab, mid-call
js = []
focus = [(r(live["voice"] + anchor("n_live", find("n_live", word))), box) for word, box in LIVE_REGIONS]
rings = spotlight("lv", focus, live["dur"], js)
js.append('    tl.fromTo("#lv-wrap", { opacity: 0, scale: 1.04, filter: "blur(8px)" }, { opacity: 1, scale: 1, filter: "blur(0px)", duration: 0.45, ease: "expo.out" }, 0);')
js.append('    tl.fromTo("#lv-chip", { opacity: 0, y: -16 }, { opacity: 1, y: 0, duration: 0.45, ease: "expo.out" }, 0.25);')
js.append(f'    tl.to("#lv-wrap, #lv-chip", {{ opacity: 0, filter: "blur(8px)", duration: 0.25, ease: "power2.in" }}, {r(live["dur"] - 0.3)});')
live_html = scene("live", SHOT_CSS, f"""    <div id="lv-wrap" class="shotwrap" data-layout-allow-overflow><div id="lv-cam" class="cam">
      <img id="lv-shot" class="shot clip" src="assets/screens/02_live_midcall.png" data-start="0" data-duration="{live["dur"]}" data-track-index="5" />{rings}
    </div></div>
    <div id="lv-chip" class="chipbar"><span class="chip"><span class="n">During the call</span>Live call</span></div>""", "\n".join(js), live["dur"])
(out / "live.html").write_text(live_html, encoding="utf-8", newline="\n")

# ------------------------------------------------------------------ tour: camera and spotlight follow the narration
parts, js = [], []
for n, s in enumerate(tour):
    sid = f"t{n}"
    end = r(s["start"] + s["dur"])
    rings = spotlight(sid, s["focus"], end, js)       # focus times are already on the tour's clock
    parts.append(f'    <div id="{sid}" class="tab clip" data-start="{s["start"]}" data-duration="{s["dur"]}" data-track-index="6">'
                 f'<div id="{sid}-wrap" class="shotwrap" data-layout-allow-overflow><div id="{sid}-cam" class="cam">'
                 f'<img class="shot" src="assets/screens/{s["shot"]}.png" />{rings}</div></div>'
                 f'<div id="{sid}-chip" class="chipbar"><span class="chip"><span class="n">{n + 1} / {len(tour)}</span>{esc(s["label"])}</span></div></div>')
    js.append(f'    tl.fromTo("#{sid}-wrap", {{ opacity: 0, scale: 1.03, filter: "blur(8px)" }}, {{ opacity: 1, scale: 1, filter: "blur(0px)", duration: 0.4, ease: "expo.out" }}, {s["start"]});')
    js.append(f'    tl.fromTo("#{sid}-chip", {{ opacity: 0, y: -16 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: "expo.out" }}, {r(s["start"] + 0.2)});')
    # back to the full view before the next tab, so each tab opens on the whole screen
    js.append(f'    tl.to("#{sid}-cam", {{ x: 0, y: 0, scale: 1, duration: 0.6, ease: "power2.inOut" }}, {r(end - 0.75)});')
tour_html = scene("tour", SHOT_CSS + "    .tab { position: absolute; inset: 0; }\n", "\n".join(parts), "\n".join(js), TOUR_DUR)
(out / "tour.html").write_text(tour_html, encoding="utf-8", newline="\n")

# ------------------------------------------------------------------ outro: the end card's words pop with the voice
ow = NAR["n_outro"]["words"]
split = next(i for i, w in enumerate(ow) if w["w"].endswith("."))
spans1, spans2, js = [], [], []
for k, w in enumerate(ow):
    wid = f"ow{k}"
    (spans1 if k <= split else spans2).append(f'<span id="{wid}" class="ow">{esc(w["w"])}</span>')
    js.append(f'    tl.fromTo("#{wid}", {{ opacity: 0, y: 24, scale: 0.9, filter: "blur(8px)" }}, '
              f'{{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)", duration: 0.3, ease: "back.out(1.6)" }}, {r(OUTRO_VOICE + w["s"])});')
js.append(f'    tl.fromTo("#o-sign", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.6, ease: "power2.out" }}, {r(OUTRO_VOICE + ow[-1]["e"] + 0.4)});')
outro = scene("outro", """
    #o-wrap { position: absolute; left: 160px; top: 360px; width: 1600px; }
    .o-line { font-size: 100px; font-weight: 900; letter-spacing: -0.045em; line-height: 1.08; }
    #o-l2 { color: #0071e3; }
    .ow { display: inline-block; margin-right: 0.22em; }
    #o-sign { position: absolute; left: 160px; top: 650px; font-size: 32px; color: #6e6e73; letter-spacing: -0.01em; }
""", f"""    <div id="o-wrap"><div id="o-l1" class="o-line">{"".join(spans1)}</div><div id="o-l2" class="o-line">{"".join(spans2)}</div></div>
    <div id="o-sign">Emma · the voice receptionist for Pearl Dental</div>""", "\n".join(js), OUTRO_DUR)
(out / "outro.html").write_text(outro, encoding="utf-8", newline="\n")

# ------------------------------------------------------------------ index.html: scene hosts, captions, audio
audio = []
for c in lines:
    audio.append(f'      <audio id="a-call-{c["i"]:02d}" class="clip" src="{c["file"]}" data-start="{r(T0 + c["start"])}" '
                 f'data-duration="{c["seconds"]}" data-track-index="{10 if c["who"] == "emma" else 11}" data-volume="1"></audio>')
narr = [("n_intro", 0.6), ("n_live", T0 + live["start"] + live["voice"])] + \
       [(s["key"], TOUR_T0 + s["voice"]) for s in tour] + [("n_outro", OUTRO_T0 + OUTRO_VOICE)]
for key, at in narr:
    audio.append(f'      <audio id="a-{key}" class="clip" src="{NAR[key]["file"]}" data-start="{r(at)}" '
                 f'data-duration="{NAR[key]["seconds"]}" data-track-index="12" data-volume="1"></audio>')


def host(cid, start, dur, track, z, kind="graphics"):
    return (f'      <div id="{cid}" class="clip" data-composition-id="{cid}" data-composition-src="compositions/{cid}.html" '
            f'data-start="{start}" data-duration="{dur}" data-track-index="{track}" data-track-kind="{kind}" '
            f'data-width="1920" data-height="1080" style="z-index: {z}"></div>')


index = HEAD + f"""    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1920px; height: 1080px; overflow: hidden; background: #f5f5f7; }}
      #root {{ position: relative; width: 100%; height: 100%; font-family: "Inter"; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{TOTAL}" data-width="1920" data-height="1080">
{host("intro", 0, INTRO, 1, 1)}
{host("call", T0, CALL_DUR, 2, 2)}
{host("live", r(T0 + live["start"]), live["dur"], 3, 3)}
{host("tour", TOUR_T0, TOUR_DUR, 4, 4)}
{host("outro", OUTRO_T0, OUTRO_DUR, 5, 5)}
{host("captions", 0, TOTAL, 8, 9, kind="captions")}
{chr(10).join(audio)}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      window.__timelines["main"] = gsap.timeline({{ paused: true }});
    </script>
  </body>
</html>
"""
(HERE / "index.html").write_text(index, encoding="utf-8", newline="\n")

print(f"intro 0-{INTRO}  call {T0}-{r(T0 + CALL_DUR)} (live at {r(T0 + live['start'])})  tour {TOUR_T0}-{OUTRO_T0}  "
      f"outro -{TOTAL}  total {TOTAL}s  caption groups {gid}")
