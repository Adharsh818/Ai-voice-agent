# Redesign plan: talk page, dashboard and login (14 Oct)

Status: **approved and built (14 Oct)** on branch `claude/dashboard-emma-redesign-905e8e`.

Owner's decisions (14 Oct): accent **Pearl teal**; font **SF Pro** (system stack, no font file shipped:
Apple's licence limits SF Pro to Apple-platform design work, so the pages name it first and use it wherever
it's installed); the Recovery tab is now **Rebooking**.

Where the build differs from the plan below, and why:

- **No Ctrl or ⌘ + 1–8 section shortcuts.** In a browser those keys switch tabs, and `accessibility.md ›
  Speech` says not to override system shortcuts. Only `/` (go to search) is added.
- **Appointments are list rows, not a sortable table.** The two-line rows replaced the table, so columns
  can't be clicked to sort. Grouping by branch or doctor stays (under Filter).
- **Card titles are 17/22, not 20/25.** 20 px titles crowded the cards next to the 28 px view title.
- **The talk page has its own stylesheet, `static/talk.css`.** `app.css` is shared with the patient page,
  which is out of scope and keeps its old look. `app.js` gained optional hooks (status line, caller's
  line, End call button) that the patient page simply doesn't have.
- **Rebooking's steps stack below 1440 px wide**, so the blocked-doctors table never scrolls sideways.

Not yet verified: a real browser call on the new talk page. The review copy runs without the
ElevenLabs key, so it doesn't spend characters re-rendering the cache. Everything else was checked in
the browser in light, dark and at 375 px wide.

Scope (owner, 14 Oct): a full rethink of the staff dashboard (`static/dashboard/`), the dashboard
login, and Emma's talk page (`static/index.html`). The direction comes from Apple's own websites
and the Human Interface Guidelines (the `apple-look` and `apple-motion` skills). Every feature
stays. The patient page is out of scope.

Evidence used: screenshots of every tab on a review copy (port 8001, copied database, phone and
messages off), values measured from apple.com, the Siri page, iCloud.com and Apple Support, and
12 HIG pages. All contrast figures are WCAG ratios computed from the hex values.

---

## Part 1: Design review of what's there now

### Summary

**Rating: Critical issues.** The dashboard is a well-organised, honest working tool: good
semantic colour tokens, real light and dark modes, and copy written for clinic staff. Its
problems are structure and a few accessibility failures. The talk page has a strong idea (one
object that is the whole interface), but on screen it is an unlabelled grey ball. Its state is
carried by colour alone, and the caller's words are hard to read. Neither page has a point of
view that belongs to Pearl Dental: they would look the same for any SaaS product.

### Critical

1. **Talk page: call state is shown by colour alone.** Idle, listening, thinking and speaking
   differ only in the orb's tint (grey, teal, indigo, cream). Nothing visible says "listening".
   The `aria-label` only switches between Start and End call.
   *Why:* `accessibility.md › Vision` and `color.md › Inclusive color` say not to rely on colour
   alone. *Fix:* a visible status line under the orb ("Listening", "Emma is speaking"), with motion
   and colour only as reinforcement.
2. **Talk page: caption contrast.** Emma's caption is #e8ecf4 at 45% opacity on #0b0d12, which
   gives 3.99:1. The caller's caption at 30% gives **2.4:1**. Both are 16 px, so both need 4.5:1.
   They are also cut to one line with an ellipsis, so longer sentences are never fully shown.
   *Why:* `accessibility.md › Vision`, contrast table. *Fix:* see the type plan in Part 2: Emma's
   line at full label colour, the caller's at secondary colour (8.2:1 dark, 4.7:1 light), up to
   three lines.
3. **Dashboard dark mode: white on red is 2.77:1.** This affects the "Cancel appointment" and
   "Erase their data" buttons (`.btn.danger.solid`) and the Tasks count badge (white on #f87171).
   *Fix:* destructive fill #e0242c with white text (4.71:1). Badges use tinted capsules instead
   (below).

### Improvements

- **High: six tabs and a ten-card System page.** System holds performance, messages, calendar
  sync, providers, do-not-call, staff, holidays, doctors and hours, privacy and audit on one
  scrolling page. `layout.md › Visual hierarchy` asks for progressive disclosure. On a desktop
  window `layout.md › Size classes` recommends switching from a tab bar to a sidebar, and
  `sidebars.md › Best practices` allows at most two levels. *Fix:* a sidebar with three groups
  (Part 2).
- **High: the phone-width header takes about 30% of the screen.** At 375 px the sticky header,
  tabs and alert banner fill roughly 250 of 812 px. The tabs scroll sideways with Recovery and
  System off-screen and nothing showing there is more. "Log out" wraps onto its own line. *Fix:* a
  bottom tab bar on compact widths, and the alert banner moves into the content.
- **High: the back button doesn't move between tabs.** `showTab` uses `history.replaceState` and
  never listens for `hashchange`, so Back leaves the dashboard. Changing the hash by hand does
  nothing. *Fix:* `pushState` plus a `popstate` listener.
- **Medium: one colour, two meanings.** Teal is the brand and link colour, and also the
  "listening" state pill, the selected segment and the live chip (`color.md › Best practices`:
  "Avoid using the same color to mean different things"). *Fix:* the accent is used only for
  interaction. States use the status set.
- **Medium: separators are almost invisible.** Borders are 1.27:1 (light) and 1.32:1 (dark).
  That's fine as decoration, but table rows and card edges are doing structural work. *Fix:*
  grouped inset surfaces separate content by background, not by hairlines.
- **Medium: engineering vocabulary in a staff tool.** "Last tier", "Emma's rules / language
  model", "End of speech", "Cal", raw call ids. *Fix:* keep them, but behind a "Details"
  disclosure on the Live call and Health views (`writing.md`: names come from what people
  recognise).
- **Medium: table headers are 11.7 px uppercase.** They pass contrast (5.32:1), but uppercase
  tracking at that size reads as shouting. Apple uses title-style column headings, not caps
  (`lists-and-tables.md › Content`).
- **Low: labels that don't name the action.** "End call + task" becomes "End call and add task".
  "Cal" becomes "Calendar". "(leave empty for the clinic password)" inside the label becomes hint
  text under the field.

### Craft notes

- **High: no point of view.** Grey-blue cards, a teal accent and a radial-gradient "brand mark" is
  the generic SaaS default. It isn't wrong, but nothing about it says *Pearl* Dental. The name
  hands us the signature for free (Part 2).
- The talk page is close to great: one object, nothing else. It needs a name, a status line,
  legible words, and an orb that looks like *something* instead of a gradient ball.

### What works (keep)

- Semantic colour tokens with `prefers-color-scheme` and no app-level theme switch
  (`dark-mode.md › Best practices`).
- Sentence-case, plain-English copy ("Every 'someone will call you back' Emma promises is
  here."). apple.com itself uses sentence case for buttons ("Learn more"), so we keep it.
- System fonts, no CDN, works offline.
- `prefers-reduced-motion` already handled on both pages.
- The talk page's "one object" idea, and the honest problem messages ("Microphone blocked.
  Allow it from the icon in the address bar, then try again.").

---

## Part 2: The plan

### Ground it in the subject

- **Product:** Pearl Dental's receptionist, Emma, plus the console the front desk uses to watch
  her, take over, and run the diary.
- **Audience:** front-desk staff and the owner, on a desktop at the desk and sometimes on a
  phone. Callers testing in a browser.
- **Single job:** *talk page:* call Emma and know at a glance what she's doing. *Dashboard:* see
  what needs a person right now, and do it.
- **Material:** a pearl. Nacre is white with shifting pink, lavender, mint and sky sheen. It reads
  as clean, calm and clinical but warm, which is what a dental front desk should feel like. It is
  also the clinic's name, so the signature is rooted in the product and not decoration.

### What Apple's sites actually use (measured 14 Oct)

| Thing | Value | Where |
| --- | --- | --- |
| Text | #1d1d1f, secondary #6e6e73 | apple.com |
| Page and tile grey | #f5f5f7, pill fill rgba(232,232,237,0.5) with a 0.8 px #d2d2d7 border | apple.com, Siri page |
| Link and primary blue | #0066cc text, #0071e3 fill | apple.com |
| Buttons | Pill (radius 980 px), 17 px, 11 px by 21 px padding, 44 px tall | apple.com |
| Global nav | 44 px, rgba(255,255,255,0.8) with `saturate(1.8) blur(20px)` | apple.com |
| Body type | 17 px, letter-spacing −0.374 px (−0.022 em) | apple.com |
| Section titles | 32 px / 600 / 36 px line, +0.004 em; hero 48 px / 600 | Support |
| Tiles | #f5f5f7, radius 18 px, shadow 8 px 8 px 16px rgba(0,0,0,0.08) | Support |
| Dark app surface | #1c1c1e, text 98% white, tertiary 66% white, cards radius 11 px | iCloud.com |

### Token system

**Colour.** One accent (Pearl teal) for interaction only, and four status colours that mean the
same thing everywhere. Badges are tinted capsules: the status colour as text on a 10% tint of
itself.

| Token | Role | Light | Dark | Contrast (text on its surface) |
| --- | --- | --- | --- | --- |
| `--bg` | grouped page background | #f5f5f7 | #000000 | n/a |
| `--surface` | cards, lists, sidebar | #ffffff | #1c1c1e | n/a |
| `--fill` | controls, selected row, segmented track | #e8e8ed | #2c2c2e | n/a |
| `--label` | primary text | #1d1d1f | #f5f5f7 | 16.8 / 15.6 |
| `--secondary` | secondary text | #636366 | #a1a1a6 | 5.99 / 6.61 (4.9 / 5.4 on fill) |
| `--separator` | hairlines (decorative only) | #d2d2d7 | #38383a | n/a |
| `--accent` | buttons, links, focus, selection | #0b7469 | #3cc8b4 | 5.65 / 8.19; on-accent text #fff 5.65 / #00201c 8.27 |
| `--red` | urgent, failed, cancelled, destructive | #c4000f | #ff6961 | 6.26 / 6.03; badge 5.22 / 4.93 |
| `--orange` | needs attention, needs reschedule | #b33a00 | #ff9f0a | 5.96 / 8.28; badge 5.11 / 6.39 |
| `--green` | booked, done, healthy | #1a7030 | #30d158 | 6.17 / 8.42; badge 5.35 / 6.51 |
| `--blue` | informational, completed | #0066cc | #4ba3ff | 5.57 / 6.47; badge 4.83 / 5.16 |
| destructive fill | Cancel and Erase buttons | #c4000f | #e0242c | white text 6.26 / 4.71 |
| `--pearl-*` | the pearl only, never UI | #f6f1ea base; sheen #f2d4d7, #d9d3f2, #cdeae2, #cfe2f5 | same | n/a |

`prefers-contrast: more` darkens `--secondary` to the label colour at 80%, makes separators 2 px
at #8e8e93, and gives every control a visible border.

**Type.** System font first, so Apple devices get SF Pro. Tracking tightens as the size grows
(apple-motion §15). Sizes are in `rem`, so browser zoom and font settings scale the layout.

| Style | Size / line | Weight | Tracking | Used for |
| --- | --- | --- | --- | --- |
| Large title | 28 / 34 | 700 | −0.021 em | view title in the toolbar |
| Title | 20 / 25 | 600 | −0.017 em | card and section titles |
| Headline | 15 / 20 | 600 | −0.01 em | row titles, patient names |
| Body | 15 / 22 | 400 | −0.01 em | everything else on the dashboard |
| Callout | 13 / 18 | 400 | 0 | secondary lines, hints |
| Caption | 12 / 16 | 500 | +0.01 em | badges, timestamps (the floor; desktop minimum is 10 pt) |
| Talk: Emma's line | 22 / 30 | 500 | −0.015 em | live caption |
| Talk: caller's line | 17 / 24 | 400 | −0.02 em | live caption |

Numbers use `font-variant-numeric: tabular-nums`. Ids use `ui-monospace`. On compact widths
the dashboard body goes to 17 px, the iOS default.

**Shape and depth.** Card radius 18 px, matching Apple Support's tiles. Controls 10 px, pills
for primary buttons and segmented controls. One shadow, for things that float (sheets, toasts,
popovers). Cards sit flat on `--bg` with no border.

**Material.** Glass appears in exactly two places, both floating chrome: the dashboard's toolbar
and the compact tab bar. Each uses `blur(20px) saturate(1.8)` over 80% `--surface` (Apple's own
nav value), with a scroll-edge fade instead of a border. With `prefers-reduced-transparency` it
becomes a solid `--surface`. Cards, rows and the pearl never use glass (`liquid-glass.md ›
Review checklist`).

### Signature: the pearl

On the **talk page**, Emma *is* a pearl: a nacre sphere with a soft sheen that drifts.

- In dark mode it sits on black like a pearl on velvet. In light mode it sits on #f5f5f7 like a
  pearl on satin, with a soft contact shadow.
- It does not change colour with state. State is carried by the status line and by *how* the
  sheen moves:
  - listening: the pearl swells with the caller's voice;
  - thinking: one slow highlight travels across it, like light turning on a pearl;
  - speaking: the sheen and scale follow Emma's voice.

On the **dashboard**, the same pearl at 10 px marks Emma's presence. It sits next to "Live call"
in the sidebar, and it glows while a call is on. It is the only decoration in the dashboard.
Everything else is quiet.

### Layouts

**Talk page**: one centred column, up to 560 px wide, the same at every width.

```text
 compact (phone)                         regular (desktop): same column, centred
┌─────────────────────────────┐        ┌──────────────────────────────────────────┐
│ Pearl Dental                │        │ Pearl Dental                             │
│ Emma · Reception            │        │ Emma · Reception                         │
│                             │        │                                          │
│            ◯                │        │                    ◯                     │
│         (pearl)             │        │                 (pearl)                  │
│        Listening            │        │                Listening                 │
│                             │        │                                          │
│  Sure, the 2nd. Can I get   │        │      Sure, the 2nd. Can I get your       │
│  your name first?           │        │      name first?                         │
│  You: on the second I want… │        │      You: on the second I want…          │
│                             │        │                                          │
│  [ Type instead ] [ End ●]  │        │          [ Type instead ]  [ End ]       │
└─────────────────────────────┘        └──────────────────────────────────────────┘
 idle: pearl + "Call Emma" pill under it; the pearl is also the button (as today)
```

The controls sit under the captions, mid-screen, not pinned to the window's bottom edge
(`layout.md › Desktop`). End call is a red pill with a label, 44 px tall. Problem states keep
today's one-line reason and the "Try again" button.

**Dashboard**: a sidebar with a content pane on desktop, and a tab bar on compact widths.

```text
 regular (≥ 900 px)
┌────────────────────┬────────────────────────────────────────────────────────────────┐
│ Pearl Dental    ⋯  │ Appointments                       ⌕ Search     [ + New ]     │ ← glass toolbar
│ owner              │ ( Day | Upcoming | Past | All )  ‹ Wed 14 Oct ›  Today  Filter ▾│
│                    ├────────────────────────────────────────────────────────────────┤
│ Today              │ ⚠ Emma's voice isn't set up, so calls can't work.   Health ›   │ ← banner in content
│  ◦ Live call       │ ┌────────────────────────────────────────────────────────────┐ │
│    Appointments    │ │ 09:00  Rahul Kamath          Tooth filling · Dr Meera Rao   │ │
│    Tasks        3  │ │        9000000014 · Nagarbhavi       ● Needs reschedule  ⋯ │ │
│ Follow-up          │ │ 10:00  Tony Stark            General check-up · Dr Rao  …  │ │
│    Calls           │ └────────────────────────────────────────────────────────────┘ │
│    Rebooking       │                                                                │
│ Clinic             │                                                                │
│    Health       !  │                                                                │
│    Setup           │                                                                │
│    Privacy         │                                                                │
└────────────────────┴────────────────────────────────────────────────────────────────┘

 compact (< 900 px)
┌─────────────────────────────┐
│ Appointments          + New │  ← glass title bar
│ (Day|Upcoming|Past|All)     │
│ ‹ Wed 14 Oct ›   Filter ▾   │
│ 09:00 Rahul Kamath          │
│ Tooth filling · Dr Rao  ●…  │
├─────────────────────────────┤
│ Live  Appts  Tasks³ Calls  ⋯│  ← glass tab bar; "Clinic" lists Rebooking/Health/Setup/Privacy
└─────────────────────────────┘
```

Sections (the sidebar holds two levels at most):

| Group | Item | Comes from |
| --- | --- | --- |
| Today | **Live call** | Live call tab |
| | **Appointments** | Appointments tab |
| | **Tasks** (count badge) | Tasks tab |
| Follow-up | **Calls** | Calls tab |
| | **Rebooking** | Recovery tab ("Doctor unavailable") |
| Clinic | **Health** (alert badge) | System: How Emma is doing, Providers, Calendar sync, Messages to patients |
| | **Setup** | System: Doctors and hours, Holidays and closures, Staff logins |
| | **Privacy** | System: Patient data requests, Do-not-call list, Audit log |

The header chips go. The call state lives on "Live call" in the sidebar, the task count on Tasks,
and calendar failures on Health. Sign out moves into the account menu at the top of the sidebar,
not the bottom (`sidebars.md › Desktop`).

### What changes in each view

- **Live call.** The transcript reads like Messages. Emma speaks for the clinic, so her lines are
  on the right in accent-tinted bubbles. The caller is on the left in grey, and staff lines are
  orange-tinted. Take over, Hand back and End call sit in the view's toolbar at the top. A right
  inspector shows the call (who, how long, what they want) with collected details as tokens. Turn
  timings and the tier go into a collapsed "Details" disclosure.
- **Appointments.** One inset list grouped by day, two-line rows: time, patient and service with
  the doctor, then phone and branch with a status badge. Move and Cancel live in a `⋯` row menu
  plus a right-click menu. Branch, doctor, status and grouping move into a Filter popover. Active
  filters show as removable tokens. Search stays in the toolbar. Columns can be sorted.
- **Tasks.** Modelled on Reminders. Round check circles, the note as the title, phone, created
  and due on a second line, and priority as "!!" plus a label plus colour (never colour alone).
  Checking an item off moves it out with a short fade, with Undo in a toast.
- **Calls.** A master and detail split like Mail. Two-line rows on the left (time and outcome
  badge, then phone, length and channel). The transcript and facts are on the right. On compact
  widths, the detail opens as its own screen with Back.
- **Rebooking.** The three steps stay, and the numbers stay because they really are a sequence:
  block a doctor, see who is affected, run calls. They show as a vertical stepper. Campaigns are
  grouped lists with progress shown as "3 of 5 reached" text plus a bar.
- **Health.** Settings-style grouped lists (label left, value and status right) for providers,
  sync and messages. "How Emma is doing" becomes three plain-language numbers, with the full
  table under "Details".
- **Setup and Privacy.** Grouped forms. Erasing a patient's data keeps its confirmation sheet
  (`alerts.md`: destructive and irreversible gets a warning and Cancel).
- **Login.** A centred card with the small pearl, "Sign in to Pearl Dental", User name with the
  hint "Leave blank to use the clinic password", Password, and a full-width pill "Sign in".
  "Log out" becomes "Sign out" everywhere to match.

### Motion (apple-motion)

A small hand-written spring helper (about 40 lines, critically damped closed form, no library,
works offline) drives everything that follows the voice or can be interrupted. CSS transitions
are only for hover and press colour.

| Moment | Behaviour | Values |
| --- | --- | --- |
| Pearl follows voice | the target scale tracks the mic or playback level every frame; a spring chases it, so it never jitters | damping 1.0, response 0.12 s rising, 0.35 s falling |
| Pearl press | scales to 0.96 on pointer-down, not on click | 100 ms ease-out, release is a spring 0.3 s |
| Call connects (the one orchestrated moment) | the pearl lifts slightly, the status line and captions fade up 8 px | damping 1.0, response 0.4 s |
| Thinking sheen | one highlight sweeps across the pearl, about 1.6 s, while waiting | stops the instant Emma speaks (interruptible) |
| New transcript line | slides up 6 px and fades in | spring 0.25 s, no bounce |
| Sheets and popovers | grow from the button that opened them and go back the same way | transform-origin at the trigger, response 0.3 s |
| Toasts | rise from the bottom-right and leave the same way | response 0.3 s |
| Task checked | the circle fills at once, the row fades out after 600 ms | Undo cancels mid-fade |

Not animated: sidebar selection, tab switches and table updates. These happen often, so they are
instant (`motion.md › Providing feedback`). Reduced motion: no scaling or travelling. State
changes cross-fade in 150 ms, and the pearl holds still with a static sheen. The status text still
says everything.

### Behaviour fixes that come with it

- Back and forward move between sections (`pushState` plus `popstate`). Each section has its own
  URL hash.
- Keyboard: Ctrl or ⌘ plus 1–8 switches section, `/` focuses search, Esc closes sheets. Every
  control has a visible focus ring in the accent colour (2 px, 2 px offset).
- Hit targets: 32 px minimum on desktop (above the 28 pt default), 44 px on compact.

### Sequence

1. Tokens, type, base components, focus states, and the three Critical fixes.
2. Dashboard shell: sidebar and tab bar, toolbar, routing and history, alert banner in content,
   System split into Health, Setup and Privacy.
3. Views: Live call, Appointments, Tasks, Calls, Rebooking, Health, Setup, Privacy.
4. Talk page: layout, the pearl, the status line and captions, light and dark, the spring helper.
5. Login.
6. Motion polish, reduced motion, reduced transparency and increased contrast.
7. Checks: the full test suite; screenshots of every view in light, dark and 375 px; a
   keyboard-only pass; 200% zoom; one real browser call on the review copy.

### Constraints kept

- Element ids that `dashboard.js` and the tests rely on stay, unless the view that owns them is
  rewritten in the same change.
- "Pearl Dental" stays literal in the HTML, so `pages.py` can swap in another clinic's name. The
  pearl itself is not a text mark, so another clinic gets a pearl too. That is a known trade-off;
  it could be swapped for a plain dot.
- No CDN. Everything works offline.
- No copy volunteers that Emma is automated (`docs/NORTH_STAR.md` §5). The talk page says
  "Emma · Reception".
- LF line endings.

### Critique of this plan

- *Would this plan fit another product?* The token system and sidebar would. That's on purpose:
  familiar components carry navigation (`branding.md › Best practices`). The pearl, the
  Messages-style transcript where Emma speaks for the clinic, and the Reminders-style task list
  would not. They come from this product.
- *One thing to remove:* the earlier draft also put the accent teal on the sidebar icons. Cut.
  Sidebar icons stay monochrome, and colour is spent on the pearl and on status only.
