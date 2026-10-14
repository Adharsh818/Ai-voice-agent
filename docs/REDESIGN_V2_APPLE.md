# Redesign v2: apple.com, measured (14 Oct)

## Hand-off for the next session (read first)

The owner wants this continued **with REA's tools**, invoked as `/reverse-engineer-anything`.

- **Why REA wasn't fully used on 14 Oct:** REA is installed (`rea-agents` 6.4.0, global npm) and
  registered in `C:\Users\adhar\.claude.json` as server `rea`, but the session that wrote this plan
  started before registration, so it had no REA tools. A new session should have them. Check that
  tools such as `list_browser_targets`, `capture_browser_scenario` and `inspect_web_network_capture`
  are listed. If they aren't, read the skill's `references/connection-and-recovery.md`; don't
  repeat setup.
- **What REA should add:**
  1. Re-capture apple.com evidence through REA (browser scenario or a saved HAR of apple.com/,
     /iphone/ and /store), with Evidence IDs for the ledger below.
  2. Settle the unknowns: **F9** (the staggered reveal's easing, set by script) and **F17** (the
     hover scale `--card-element-scale`, set at runtime). Also the Store card hover rules, whose
     stylesheets were cross-origin.
  3. Observe the global nav's mobile menu below 834 px, and the hover/press states frame by frame.
- **Where the work is:**
  - **PR #6** (`claude/dashboard-emma-redesign-905e8e`): v1, finished and tested, plus this plan.
  - **Branch `claude/dashboard-v2-apple-wip`**: v2 build in progress, **not working yet**.
    - Done: `static/dashboard/dashboard.css` (apple.com tokens, global nav, menus, curtain, local nav,
      reveal), `static/dashboard/index.html` (global nav markup), `static/dashboard/dashboard.js`
      (nav controller, status lines, frosted section bar, reveal), `login.html` and `login.js`.
    - Started: `static/index.html` (talk page hero markup).
    - Not done: `static/talk.css` (still v1, so the talk page is broken on that branch); `static/app.js`
      hooks (wire `#call-start`, set `body[data-call]`, keep **Type instead** visible when idle).
    - Not done: browser checks, the test suite, the light-only pass on the talk page, and a real call
      on the voice copy (port 8002, `.claude/launch.json` entry `emma-voice`, local only).
- **Owner's decisions:** see the decisions table below. Apple blue, top nav, light only, full
  motion.

Status: **plan for the owner's approval. Nothing built yet.** It builds on PR #6 (v1, `docs/REDESIGN_PLAN.md`).

Owner's brief: study apple.com in detail (every section, button, flow, pop, the glass and the scroll
feel) and replicate that design for the talk page, the dashboard and the sign-in page, as close to
the website as possible. Use the REA skill.

## How this was studied, and its limits

- **REA:** the skill is installed and REA is registered in Claude Code (`.claude.json`, permanent
  path), but this session started before registration, so its tools aren't connected. Its guide's
  remedy is a new session. REA's browser tools are also passive by design: they can't click,
  hover or scroll. So the observation used the built-in browser, following REA's method: measured
  evidence, observations kept apart from inferences, a findings ledger, and unknowns stated.
- **Evidence:** computed styles and the complete readable stylesheets of apple.com (home: 10 sheets,
  5,198 rules, none blocked), apple.com/iphone, and apple.com/store (6 sheets cross-origin and
  unreadable). A real hover opened the global nav's menu. Screenshots in this pane were unreliable,
  so values are read from the live page, not estimated from images.
- **Not covered:** every page on apple.com. The site shares one component system (the global nav,
  `sk-` buttons, tiles, the staggered fade-in), so three representative pages cover it. Other
  limits: checkout and sign-in were deliberately not touched; card hover scales are set by
  JavaScript at runtime and weren't captured; the Store's card hover rules are in unreadable sheets.

## Findings ledger

O = observed (measured on the page), I = inferred, U = unknown.

| # | Finding | Type | Where |
| --- | --- | --- | --- |
| F1 | Global nav: 44 px tall, `rgba(255,255,255,0.8)` + `backdrop-filter: saturate(180%) blur(20px)`, links 12 px #1d1d1f at 80% | O | home `#globalnav` |
| F2 | Opening a nav menu: the bar turns solid #fafafc, a panel drops down (528 px here) with a 12 px #6e6e73 group label over 24 px/600 #333336 links | O | hover on Store |
| F3 | The page behind an open menu gets a "curtain": `rgba(232,232,237,0.4)` + `blur(20px)`, fading over 0.32 s after an 80 ms delay | O | `.globalnav-curtain` |
| F4 | Menu motion: height 0.24 s; content slides 8 px forward or back with a fade; on close the height waits 0.12 s; nav chevrons slide in from −4 px on hover | O | keyframes `globalnav-flyout-slide-*`, `chevron-slide-in-hover` |
| F5 | House easing `cubic-bezier(0.4, 0, 0.6, 1)` in 146 rules; content easing `cubic-bezier(0.25, 0.1, 0.3, 1)` and `(0.28, 0.11, 0.32, 1)`; hover lifts `cubic-bezier(0, 0, 0.5, 1)` | O | home stylesheet census |
| F6 | Durations used most: 0.24 s (82), 0.32 s (42), 80 ms (32, as delays), 250 ms, 0.3 s, 160 ms, 140 ms | O | census |
| F7 | Section sub-nav ("local nav"): `saturate(180%) blur(20px)` while sticking; its menu tray opens over 320 ms with a 240 ms delay | O | `#ac-localnav` rules |
| F8 | Scroll reveal: an item fades in (0.9 s) and rises 30 px (0.7 s), 0.15 s after the previous item, triggered when its top reaches 70% of the viewport | O | `StaggeredFadeIn` variables, `data-anim-keyframe {"start":"t - 70vh"}` |
| F9 | The stagger's easing curve is set by script at runtime | U | not in CSS |
| F10 | Scrolling is native (`scroll-behavior: auto`); no scroll hijacking. The "glass" feel is content passing under the frosted sticky bars | O / I | iPhone page |
| F11 | Buttons: pill (radius 980 px), 17 px text, 11 × 21 px padding, 44 px tall; fill #0071e3, hover #0077ED, pressed #006EDB; secondary is a 0.8 px outline in link blue (#0066cc light, #2997ff on dark) | O | `.button`, `--sk-button-*` |
| F12 | Focus ring: 2 px #0071e3, offset 3 px | O | `.button:focus-visible` |
| F13 | Type (SF Pro): 80/84 600 −0.015em hero; 56/60 600 −0.005em section; 28/32 600 +0.007em shelf titles; 24/28 600 +0.009em menu links; 17/25 −0.022em body; 12/16 −0.01em labels and nav | O | iPhone, Store, home |
| F14 | Colour: #1d1d1f text; #6e6e73 secondary; #86868b tertiary; #f5f5f7 tiles and alternate sections; #fff page; #fafafc open menus; black sections with 92%-white text | O | across pages |
| F15 | Content tiles: radius 28 px, full-bleed colour (#f5f5f7, #000, #fff), no shadow, 48 px top / 56 px bottom padding | O | iPhone `.tile-rounded` |
| F16 | Store cards: radius 18 px, white, shadow `2px 4px 12px rgba(0,0,0,0.08)`, transition 0.3 s `cubic-bezier(0,0,0.5,1)` | O | `.rf-ccard-content` |
| F17 | Hoverable tiles scale on hover (`.card-hover:hover { transform: scale(var(--card-element-scale)) }`, 0.3 s); the scale is set at runtime | O / U | value not captured |
| F18 | Horizontal shelves: `scroll-snap-type: x mandatory`; round 36 px arrow buttons (#86868b) fade in over 0.1 s on hover | O | `.rf-cards-scroller-content`, `.paddlenav-arrow` |
| F19 | Reduced motion: `html.reduced-motion` turns the staggered reveal off | O | stylesheet |
| F20 | The site has no Liquid Glass. Its glass is the classic frosted blur (F1, F7); Liquid Glass is Apple's OS material, not used on the web | O / I | backdrop census |

## The plan, revised three times

### Draft 1: copy the surfaces

Restyle v1 with apple.com's tokens (F11–F16) and add its motion (F3–F8). Keep the sidebar.

**Critique:** this misses the brief. The most recognisable apple.com experience is the *top* glass
bar with drop-down menus and the blurred curtain (F1–F4); a sidebar makes it feel like a Mac app,
not apple.com. It also says nothing about the talk page, which should feel like an apple.com
product hero. And "exact" needs a line drawn: Apple's logo, product photos, copy and font files
can't be reused.

### Draft 2: apple.com structure

- **Dashboard:** a 44 px frosted global bar (F1) with **Pearl Dental** on the left and section
  links in 12 px. Hovering or focusing **Today**, **Follow-up** or **Clinic** drops an apple.com
  menu (F2): a group label and large 24 px links, plus a one-line live status (e.g. "Live call ·
  Emma is speaking", "3 open tasks"). The page behind gets the curtain (F3), with the exact timings
  and slides (F4–F6). Under it, a sticky local nav (F7): the section title on the left, the
  section's main action on the right as a small blue pill (like the iPhone page's "Buy").
- **Content:** sections alternate white and #f5f5f7 (F14). Lists sit in 18 px store-style cards
  (F16); headline summaries sit in 28 px tiles (F15). Items rise in with the staggered reveal (F8)
  only on first load of a section, never on every refresh.
- **Talk page:** an apple.com hero: a big headline, a subhead, the pearl as the "product", and two
  pills: **Call Emma** (filled) and **Type instead** (outline) (F11). Captions sit under the pearl
  in body type.
- **Sign-in:** an apple.com page section: a 48–56 px headline, a single 18 px card holding the
  fields, and a blue pill.

**Critique:** closer, but three problems.
1. **Hover menus are risky on a staff tool.** apple.com's menus open on hover, but staff on a
   touch laptop or tablet would get no menu at all. apple.com itself switches to a full-screen menu
   with staggered rows below 834 px (from its own CSS).
2. **Scroll reveals on working screens become friction.** apple.com animates marketing content
   once. A receptionist reopening Appointments 50 times a day mustn't wait 0.9 s for rows to fade
   in (`motion.md`: avoid motion on frequent interactions).
3. **Exact means exact values, not Apple's identity.** Copying Apple's nav labels, logo position
   with an Apple-style mark, or "Learn more" copy reads as impersonating Apple; it also breaks the
   honesty line in North Star §5 for a clinic product.

### Draft 3 (final): apple.com, faithful where it counts

**Copied exactly from the measurements:**
- Global bar: 44 px, `rgba(255,255,255,0.8)` + `saturate(180%) blur(20px)`, 12 px links. Opening a
  menu turns it #fafafc (F1, F2).
- Menus: open **on hover after 80 ms and on click or tap** (so touch works). The curtain is
  `rgba(232,232,237,0.4)` + `blur(20px)`, fading 0.32 s after an 80 ms delay. Height 0.24 s;
  content slides 8 px in the direction of travel; chevrons slide in from −4 px. All on
  `cubic-bezier(0.4,0,0.6,1)` (F2–F6). Esc and clicking the curtain close them.
- Local nav: sticky under the global bar; goes frosted (`saturate(180%) blur(20px)`) once it
  sticks, with the title left and the main action as a small pill right (F7).
- Buttons: pill 980 px, 17 px, 11 × 21 px, 44 px; fill, hover and pressed blues; outline
  secondary; focus 2 px offset 3 px (F11, F12).
- Type: apple.com's scale and letter-spacing (F13), in SF Pro (already working).
- Colour: apple.com's neutrals (F14); black for the talk page in dark appearance.
- Cards: 18 px with the store shadow for lists; 28 px no-shadow tiles for summaries; hover
  transition 0.3 s `cubic-bezier(0,0,0.5,1)`. The hover scale is unknown (F17), so I'd use 1.01
  and state that it's chosen, not measured.
- Scrolling: native, no hijacking (F10). Shelves (for example today's appointments on the talk
  page's staff view) snap with fading arrow buttons (F18).
- Reduced motion turns off reveals and slides (F19), as apple.com does.

**Adapted, with the reason:**
- Staggered reveal (F8) only on the **talk page and sign-in** (one-time, marketing-like moments).
  The dashboard's lists appear instantly, because they're working screens.
- Below 834 px, apple.com's full-screen menu with staggered rows (from its CSS) replaces the
  hover menus, as on apple.com.
- Pearl Dental's own name and voice everywhere. No Apple logo, Apple product images or Apple
  copy, and no SF Pro font files shipped (SF Pro only where it's installed, as now).

**Talk page (final):** white page (black in dark mode). The 44 px glass bar shows Pearl Dental only.
The hero has "Emma." at 80 px, the subhead "Pearl Dental reception" at 28 px, then **Call Emma**
(filled pill) and **Type instead** (outline pill), then the pearl, larger than now, as the hero
"product". In a call, the hero text cross-fades to the status line and live captions, and the
buttons become **End call** (filled red) and **Type instead**. The hero rises in with the
staggered reveal on load.

**Dashboard (final):**

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Pearl Dental     Today ▾   Follow-up ▾   Clinic ▾                  ◯ owner ▾ │ 44 px glass bar
├──────────────────────────────────────────────────────────────────────────────┤
│ Appointments                       ‹ Wed 14 Oct ›   Filter   ( + New )       │ local nav, frosted when sticking
├──────────────────────────────────────────────────────────────────────────────┤
│  #f5f5f7 band                                                                │
│  ┌ 18px card ─────────────────────────────────────────────────────────────┐ │
│  │ 09:00  Rahul Kamath   Tooth filling · Dr Meera Rao   Needs reschedule ⋯│ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
Hovering or clicking "Today ▾" opens:
┌──────────────────────────────────────────────────────────────────────────────┐
│ Today                                                                        │ #fafafc panel
│ Live call            (12 px label: "Emma is speaking · 2:14")                │ 24 px/600 links
│ Appointments         (12 px label: "4 today, 3 need a new time")             │
│ Tasks                (12 px label: "3 open, 1 high")                         │
└──────────────────────────────────────────────────────────────────────────────┘
          page underneath: curtain rgba(232,232,237,.4) + blur(20px)
```

**Sign-in (final):** the glass bar; then on #f5f5f7, "Sign in to Pearl Dental." at 48 px with the
staggered reveal; one white 18 px card with the two fields (44 px, 12 px radius) and a full-width
blue pill.

**Kept from v1:** routing and Back/Forward, sections and their features, contrast checks,
reduced motion, transparency and contrast handling, and SF Pro. v2 is a re-skin and re-structure
of the shell and chrome, not of the features.

## Owner's decisions (14 Oct)

| Question | Decision | What changes in Draft 3 |
| --- | --- | --- |
| Accent | **Apple blue, exact**: #0071e3 / hover #0077ED / pressed #006EDB; links #0066cc | Replaces v1's Pearl teal everywhere. The pearl stays the only clinic-specific colour. |
| Navigation | **apple.com top bar + drop-down menus** | As in Draft 3. |
| Dark mode | **Light only, like apple.com** | v1's dark mode is removed from all three pages; the talk page's hero can still use a black band. This overrides `dark-mode.md`'s "follow the system"; that's the owner's call. |
| Motion | **Everything, like apple.com** | The staggered reveal (F8) also runs on dashboard lists and cards each time a section opens, not only on the talk page and sign-in. Two guards stay: reduced motion turns it off (F19, as apple.com does), and rows that arrive while you're already looking (a new call line, a task added) don't re-run it. |

## Build sequence (after approval)

1. Tokens from F11–F16 into both stylesheets.
2. Global bar, menus and curtain, with the exact timings, hover plus click, Esc, and the compact
   full-screen menu.
3. Local nav per section (title + main action), frosted when sticking.
4. Re-skin the views (cards, tiles, buttons, typography).
5. Talk page hero and in-call state; sign-in.
6. Motion pass, timed frame by frame against the values above; reduced-motion pass.
7. Checks: tests, light/dark/834 px/375 px, keyboard-only, a real call on the voice copy.
   Then push to PR #6 (or a new PR if you prefer v1 merged first).
