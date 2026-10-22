# Apple-style redesign: talk page, dashboard, login

Status: **built and click-tested on branch `claude/rea-availability-b99f59` (14 Oct 2026), fresh from `main`.** Owner's choices: this session's decisions, built fresh, not on PR #6 or the v2 WIP branch. See "8. As built" at the end.

**Owner's decisions (14 Oct):**
- **Colours (changed 14 Oct, evening):** all three pages are light only, exactly apple.com's colours. They ignore the device's dark mode. This replaces the earlier "talk page follows dark mode".
- **Fonts:** SF Pro and the other Apple fonts are installed on the owner's PC, so the pages use them by name.
- **Glass:** the apple.com recipe, without iOS 26 refraction.
- **Dashboard:** a full rebuild with a glass sidebar, a Today tile grid, Live call with the transcript in the centre, and Appointments exactly like Apple Calendar.

Scope: the three web pages only, which are `static/index.html` (talk to Emma), `static/dashboard/index.html` (staff dashboard) and `static/dashboard/login.html`. Emma's voice, call logic and APIs don't change.

## 1. What was studied, and how

These sources were studied on 14 Oct 2026:

- apple.com home page
- the iPhone product page
- the global nav (hover menus and search)
- the Apple Account sign-in page

Tools:

- **REA** (`list_browser_targets`, `inspect_web_page`, `capture_web_screenshot`) on a Chrome instance with a debug port. This gave the page inventory: 41 resources, the stylesheets and scripts, and the SF Pro font files. Evidence IDs: `ev_7be7db90…` (home page capture) and `ev_dbda65b5…` (sign-in screenshot). REA is passive: it never clicked or scrolled.
- **The in-app browser** for the clicking, hovering, scrolling and measuring: it read computed styles from the live DOM.
- **Apple's public stylesheets** (`globalheader.css`, `main.built.css`, `homepage.built.css`, `ac-localnav`, `ac-globalfooter`), which were read for exact values.

Observed (O) means measured on the live site. Inferred (I) means read from a screenshot or reasoned from the CSS.

## 2. Apple's design system, measured

### Type
| Role | Size / weight / tracking / line height | Source |
|---|---|---|
| Page hero (product page h1) | 64px / 600 / -0.009em / 68px, SF Pro Display | O |
| Section headline | 48px / 600 / -0.003em / 1.083 | O |
| Hero subhead | 24px / 400 / +0.009em | O |
| Tile headline | 40px / 600 / 0, also 32px / 600 / +0.002em | O (CSS) |
| Card title | 28px / 600 / +0.007em, also 21px / 600 / +0.011em | O (CSS) |
| Body | 17px / 400 / -0.022em / 1.47 (25px), SF Pro Text | O |
| Secondary body | 14px / 400 / -0.016em / 1.286 | O |
| Caption, nav menu headers | 12px / 400 / -0.01em / 1.333 | O |

Rules: Display above 20px and Text at 20px and below. Headlines are 600, never 700. Tracking is negative on body text and slightly positive on mid-size headings.

### Colour
| Token | Value | Used for |
|---|---|---|
| ink | `#1d1d1f` | all primary text |
| ink-2 | `#6e6e73` | secondary text ("Available starting…") |
| ink-3 | `#86868b` | tertiary text, field borders |
| canvas | `#ffffff` | page |
| canvas-alt | `#f5f5f7` | alternate sections and rounded tiles |
| canvas-index | `#fafafc` | footer and index blocks |
| hairline | `#d2d2d7` | accordion and table dividers |
| blue | `#0071e3` (hover `#0077ed`, active `#006edb`) | primary button fill |
| link-blue | `#0066cc` (dark mode `#2997ff`) | links, outline buttons |
| dark ink | `#161617` / `rgba(22,22,23,.8)` | dark nav and dark sections |

### Shape and spacing
- Pill buttons: `border-radius: 980px`, padding `11px 21px`, 17px text, height about 44px. There are two variants. Filled is blue with white text. Outline has a 1px `#0066cc` border and blue text. (O)
- Store-style buttons: radius 12px, sometimes 10px, and 5px for small ones. (O)
- Rounded tiles and cards: radius **28px**, fill `#f5f5f7` or black, no shadow. The flatness is part of the look. (O)
- Home page tiles: full-bleed sections with about 12px gutters between tiles. (I)
- Global nav: 44px tall, links 12px at 80% black, padding 0 8px. (O)

### Glass ("liquid glass")
| Element | Recipe | Source |
|---|---|---|
| Global nav bar | `background: rgba(255,255,255,.8)`; `backdrop-filter: saturate(180%) blur(20px)`; dark `rgba(22,22,23,.8)` | O |
| Menu/search curtain over the page | `rgba(232,232,237,.4)` + `blur(20px)`, fades in over 320ms after 80ms | O |
| Floating pill ("all-access pass") | 56px high, radius 28px, `rgba(232,232,237,.72)`, `blur(7px)` that strengthens as it settles, inner glow `rgba(0,0,0,.11)`; hover `rgba(223,223,227,.7)`; dark `rgba(51,51,54,.7)` | O |
| Local sub-nav when stuck | `rgba(22,22,23,.8)` (dark) with the same saturate and blur | O |

### Motion
| Interaction | Values | Source |
|---|---|---|
| Standard easing | `cubic-bezier(.4,0,.6,1)` (used about 120 times) | O |
| Layout and transform easing | `cubic-bezier(.25,.1,.3,1)` at 300ms | O |
| Colour and hover easing | `cubic-bezier(.28,.11,.32,1)` at 240ms | O |
| Nav link hover | colour over 320ms | O |
| Nav menu open | the panel grows over 320ms; each column header fades in and drops from -4px to 0 over 320ms, with delays of 80, 160 and 200ms | O |
| Search open | the field fades in and slides down over 240ms after a 200ms delay; the page behind blurs | O |
| Flyout switch | the content slides 8px sideways over 240ms | O |
| Scroll reveal ("StaggeredFadeIn") | each item fades in over 0.9s and rises 30px over 0.7s, with 0.15s between items; it fires when the group enters the viewport | O |
| Card hover | `scale(var(--card-element-scale))` (about 1.016) over 300ms, `cubic-bezier(0,0,.5,1)`, only on devices with a mouse (`html.no-touch`) | O |
| Press | `scale(.95)` on round icon buttons | O |
| Reduced motion | every reveal is turned off and the content is shown immediately | O |

### Sign-in page
Everything sits in one centred column on white, about 480px wide (I):

- the brand mark at the top
- a 40px semibold title ("Apple Account")
- a 17px subtitle
- one large rounded field with a **floating label** inside it ("Email or Phone Number"), which gets a 2px blue ring on focus
- a short privacy note with an icon
- two equal buttons side by side: Continue (pale blue while disabled) and a dark secondary button

The global nav and a light footer frame the page.

## 3. What we will not copy (legal and practical)

The aim is Apple's **design language**: proportions, type scale, colour logic, glass and motion. Their **property** stays out:

- **No Apple logo, product photos, video or marketing copy.** Emma gets her own mark.
- **SF Pro is used from the device, never served.** The owner has SF Pro installed, so the font stack names it first: `"SF Pro Text"` (body) and `"SF Pro Display"` (20px and up), then `-apple-system, BlinkMacSystemFont`, then **Inter**, then `"Segoe UI", sans-serif`. The owner's PC, every iPhone and every Mac show real SF Pro. Any other staff PC or Android phone without it gets self-hosted Inter, the closest free match. The SF Pro files are never copied into the repo or served, because Apple's licence doesn't allow that.
- **No copied CSS or JavaScript files.** The values in section 2 are re-implemented in our own small stylesheet. Copying Apple's built bundles would be a copyright problem and would also bring in about 1MB we don't need.
- **The page security rules stay.** The dashboard's Content-Security-Policy (`style-src 'self'`, `script-src 'self'`) rules out font CDNs and inline `<style>`, so everything is self-hosted.

## 4. The design, page by page

### 4.1 Shared foundation (new file `static/emma-ui.css`)
- Design tokens from section 2: type scale, colours, radii and the three easings, with a dark set under `prefers-color-scheme: dark` and `[data-theme]`.
- Components:
  - `.glass-bar` (the nav recipe)
  - `.glass-pill` (the floating pill recipe, with a 1px inner highlight `inset 0 1px 0 rgba(255,255,255,.5)` and the 0.11 inner glow)
  - `.btn-pill` and `.btn-pill.outline`
  - `.tile` (radius 28px)
  - `.reveal` (stagger)
  - `.field-float` (floating-label input)
  - `.segmented` (the Day / Upcoming / Past / All control)
- `static/reveal.js`: about 30 lines. An IntersectionObserver adds `.is-in` to `[data-reveal]` groups and sets `--i` on each child, so the CSS can give each child a delay of `calc(var(--i) * 150ms)`. When `prefers-reduced-motion` is set, nothing is hidden or animated.
- Fonts: SF Pro Text and Display by local name (installed on the owner's PC, built into Apple devices). Inter is self-hosted under `static/fonts/` (variable woff2, about 300KB, `font-display: swap`) as the fallback for other machines.

### 4.2 Talk page (`static/index.html`)
Today: a dark page with a coloured orb and a caption. The Apple version is styled like a product hero:

- **A thin glass nav (44px):** Emma's mark on the left, "Pearl Dental" in the centre, and an outline-pill "Staff login" on the right (only when the deployment has one).
- **The hero:** "Emma." at 64px/600 with a 24px subhead ("Pearl Dental's front desk. Tap to talk."), animated in with the stagger.
- **The orb** stays the centre of the page and keeps all its states (idle, listening, thinking, speaking, busy, error). It is restyled as a **glass sphere**: a translucent fill with saturate and blur, a specular highlight, and the state colour as an inner glow that pulses with the audio level (`--lvl`, already driven by app.js). The press animation is `scale(.96)`.
- **The caption** is 21px/400 in ink-2, with the newest line in ink at 28px/600. Lines cross-fade over 320ms with the standard easing.
- **"Type instead" and "Try again"** become a **floating glass pill** (56px, bottom centre) like Apple's all-access pass. It expands into the text field and Send button using the pill's width animation (`.3s cubic-bezier(.25,.1,.3,1)`).
- **Light only** (white canvas, apple.com's colours). The device's dark mode is ignored, at the owner's request.

All element IDs (`orb`, `caption`, `action`, `type-instead`, `typed`, `typed-text`) stay the same, so `app.js` needs no logic changes, only class hooks.

### 4.3 Login page (`static/dashboard/login.html`)
This copies the Apple Account sign-in layout:

- The glass nav at the top with Emma's mark and "Pearl Dental".
- The centred column (max 480px):
  - Emma's mark, a 96px glass sphere that matches the orb
  - the title "Staff sign in" (40px/600)
  - the subtitle "Pearl Dental dashboard" (17px, ink-2)
- **Floating-label fields** for user name (optional) and password: 56px high, radius 12px, a 1px `#86868b` border, a 2px `#0071e3` ring on focus, and the label shrinking to 12px at the top. "Leave empty for the clinic password" becomes the field's helper text.
- A full-width **blue pill "Sign in"** button. It is pale while empty, matching Apple's disabled Continue.
- Errors show in red text under the field and the field gives a 6px horizontal shake (300ms). The "locked" box becomes a glass notice.
- A light footer line at the bottom (privacy note, clinic name).

The IDs `login-form`, `username`, `password`, `login-error`, `login-button`, `locked` and `locked-reason` stay the same, so `login.js` keeps working.

### 4.4 Dashboard (`static/dashboard/index.html`): a full rebuild
Owner's decision (14 Oct, changed from "reskin only"): **rebuild the layout too**, keeping every feature. The model is Apple's own apps (Mail, Settings, Calendar, Messages) with the apple.com finish from section 2.

**What stays fixed:**
- All API endpoints and their payloads (`dashboard.py` is untouched).
- Every action that exists today.
- The login and session flow.
- The CSP rules.

**What changes:**
- The markup and much of the rendering code in `dashboard.js`.
- `dashboard.js` is split into modules (`shell.js`, `today.js`, `live.js`, `appointments.js`, `tasks.js`, `calls.js`, `recovery.js`, `system.js`, `ui.js`), with the existing API calls moved over unchanged.
- `tests/test_dashboard.py` is updated wherever it checks markup.

**Shell: a glass sidebar (Mail/Settings style)**
- **The sidebar:**
  - Left, 260px, `saturate(180%) blur(20px)` over `rgba(250,250,252,.8)` (dark: `rgba(22,22,23,.8)`).
  - Rows have an icon and a label: Today, Live call, Appointments, Tasks, Calls, Recovery, System.
  - The selected row is a rounded 10px capsule in `#0071e3` with white text. Counts sit on the right in ink-2 (open tasks, today's appointments).
  - The clinic name and the signed-in staff member are at the top. Log out is at the bottom.
- **Live status in the sidebar:**
  - When a call is live, the Live call row shows a pulsing green dot and the call timer.
  - The calendar-sync and alerts states show as small badges.
- **Resizing:**
  - Below 1068px the sidebar collapses to a 72px icon rail.
  - Below 734px it becomes an iOS-style **glass tab bar** at the bottom (5 items: Today, Live, Appointments, Tasks, More).
- **Switching sections:** the content fades in and slides 8px (240ms). The URL hash updates (`#appointments`), so Back works and links can be shared.
- **Content area:** each section has a large title (34px/700, the iOS large-title style). On scroll it shrinks into a glass toolbar that holds the section's actions (iOS navigation-bar behaviour: the title shrinks to 17px/600 and the bar goes glass).

**Today (new first screen): an apple.com-style tile grid**
- Rounded 28px tiles on a 12px gutter: two large ones, then a 3-across row, staggered in 60ms apart.
- **Live call tile** (large): the caller's number, what Emma is doing ("Booking a cleaning"), and the last two transcript lines. Empty state: "No call right now" with today's call count.
- **Today's appointments tile** (large): the next 4 as a mini timeline, plus "N more", and the attended, booked and cancelled counts.
- **Open tasks:** the count and the top 3 items.
- **Recent calls:** the last 5 with their outcomes.
- **Recovery campaign:** progress (called, moved, declined, no answer) as a ring.
- **System health:** green when all is well, otherwise the failing item, plus cost today and calendar sync.
- Every tile opens its full section, with a scale(1.016) hover (300ms, `cubic-bezier(0,0,.5,1)`, mouse only).
- The data comes from the existing endpoints (calls, appointments, tasks, health, cost). No new APIs are needed.

**Live call: transcript centre stage (Messages style)**
- **The centre column:** the conversation as bubbles.
  - Emma's bubbles are on the left in `#e9e9eb` (dark `#262628`); the caller's are on the right in `#0071e3` with white text, like iMessage.
  - Radius 18px with tails. A typing indicator (three dots) shows while Emma is thinking. New bubbles spring in (opacity plus 8px rise over 240ms).
  - The view auto-scrolls and stops when staff scroll up, with a "Jump to latest" glass pill.
- **The right glass panel (320px):**
  - caller details (number, name once known, past visits)
  - what Emma has collected so far (service, date, doctor, branch)
  - the controls: **Take over**, **Hand back to Emma** and **End call + task** as full-width pills, with End in red
- The operator's "Say" box is a Messages-style input bar pinned under the transcript.
- No call: a calm empty state, plus the list of today's calls.

**Appointments: Apple Calendar, completely**
The owner asked for this to be "completely like Apple". Apple's own design for appointments is the Calendar app, so:
- **Toolbar:**
  - a segmented control: **Day | Week | Month | Year** (Calendar's own set), plus a List view for the existing table and CSV export
  - "Today", the ‹ › arrows, and a search field
  - **+** for a new appointment
- **Day and Week views:**
  - an hourly grid (clinic hours only) with 0.5px hairlines
  - a red current-time line with a dot, as in Calendar
  - appointments as rounded 6px blocks tinted in their doctor's colour (Calendar's "calendars" become doctors), each with a coloured left bar, the patient's name in 12px/600, and the service and branch in 11px
  - cancelled appointments struck through and faded; attended ones with a check
- **Month view:** a 7-column grid with coloured dots, and today in a red circle (Calendar's style).
- **Sidebar section:** the doctor list with coloured check circles to show and hide each doctor, plus a mini month calendar. This only appears inside Appointments, as in Calendar.
- **Clicking an appointment** opens a glass **popover** (Calendar's event inspector) with the details and the actions: Mark attended, Reschedule, Cancel.
- **New appointment:** the existing book flow (find free times, then book) in a Calendar-style sheet. Free times appear as selectable pills.
- **Rescheduling stays explicit:** pick from the free times. Drag-to-move is left out on purpose, because every booking change must go through Python's checks (section 4 of the north star). A drag that the backend then refuses would feel broken.

**Tasks: Reminders style.** Circular checkboxes, and ticking one animates the circle filling and the row sliding away (300ms). Open, Done and All become a segmented control. Each row shows the patient, the reason and the age ("2h ago").

**Calls: Mail style (list plus detail).** The list on the left shows time, number, outcome badge and a one-line summary. Selecting a call shows its transcript as bubbles and its booking outcome on the right. The search and filters sit in the glass toolbar.

**Recovery: a guided flow.** Three steps:
1. Block a doctor (form)
2. Preview the affected patients (selectable list)
3. Start the calls (a live progress ring with per-patient statuses: calling, moved, declined, no answer)

The call-window settings stay under a "Settings" disclosure.

**System: Settings style.** Grouped inset lists (rounded 12px groups on `#f2f2f7`), one group per area: Providers and health, Calendar sync, Doctors and hours, Holidays, Staff accounts, Do-not-call, Privacy (export or erase), Costs. Each row opens a detail pane. Destructive actions are red text rows that need confirmation.

**Shared pieces:**
- **Dialogs:** a sheet rises 16px and fades in (320ms) over the blurred curtain (`rgba(232,232,237,.4)` + `blur(20px)`). On phones they become bottom sheets with a grab handle.
- **Toasts and alerts:** a floating glass pill at the bottom centre.
- **Status colours:** green `#34c759`, orange `#ff9f0a`, red `#ff3b30`, blue `#0071e3`.
- **Dark mode:** `#000` canvas, `#1c1c1e` grouped backgrounds, `#2c2c2e` tiles.

### 4.5 Phones and accessibility
- Apple breakpoints are 1068px, 734px and 480px. Below 734px the dashboard uses the bottom glass tab bar, tables become stacked cards, dialogs become bottom sheets, and Appointments opens in Day view.
- The side gutter is at least 16px, with no horizontal scroll on the page.
- Focus rings are 2px `#0071e3`, offset 2px, and always visible for keyboard users.
- Contrast is AA throughout. ink-2 `#6e6e73` on white is 4.6:1, which passes.
- With `prefers-reduced-motion`, there's no stagger, slide or shake, only instant states. With `prefers-reduced-transparency`, glass becomes solid `#fafafc`.

## 5. Build order
1. **Foundation**: `emma-ui.css`, `reveal.js` and Inter, plus a hidden swatch page at `/static/ui-swatch.html` to check every component.
2. **Login page** (smallest, so it proves out the fields, buttons, glass nav and dark mode).
3. **Talk page** (glass orb, hero, floating pill), checked in a real browser call.
4. **Dashboard**, one section at a time, each one fully working before the next:
   1. the shell: sidebar, tab bar, hash routing, large titles and the module split
   2. Live call
   3. Appointments (Calendar)
   4. Today
   5. Tasks
   6. Calls
   7. Recovery
   8. System

   After each section, every action it had before is checked by hand on the 8001 review copy against a checklist of today's features (written before the work starts by listing every button and API call in the current `dashboard.js`).
5. **Verification at each step:**
   - the full test suite
   - in-app browser screenshots of every page in light, dark and phone widths, compared side by side with the matching apple.com screenshots
   - computed-style checks that the radii, easings and blur match section 2
   - one real booking call on the talk page, checked on the dashboard

The pages are previewed on the review copy (port 8001) so the owner's database is never touched.

## 6. Risks
- **Glass costs GPU.** Blur over a long, live-updating table can stutter on low-end Android. Glass is limited to the bars, pills and dialog backdrops; tables and cards stay solid, which is also what Apple does.
- **The cross-origin iframe on Apple's sign-in page** hid its exact field metrics, so the field sizes are inferred from screenshots (marked I).
- **The rebuild could silently drop a feature.** The feature checklist above is the guard: every current button, filter and API call is listed first and ticked off section by section.
- **Tests that check the markup** (`tests/test_dashboard.py`, `tests/test_clinic_name.py`) will need updating. API tests stay as they are and must keep passing.
- **The live call must not break mid-demo.** The Live call section is built and checked in a real browser call before anything else ships.

## 7. Revision log
- **Rev 1:** first draft. It copied Apple's CSS values directly and loaded SF Pro.
- **Rev 2:** checked against the code. Every ID and data hook is now kept, so the JavaScript and tests are unaffected. A CSS-first approach replaced the plan to rewrite the dashboard markup. Added the CSP limit (no CDNs, no inline styles), the 8001 review copy, and the reduced-motion and reduced-transparency rules.
- **Rev 5:** the owner changed the dashboard to a full rebuild. Added a glass sidebar (a bottom tab bar on phones), a new Today tile grid, Live call as Messages-style bubbles, Appointments as Apple Calendar (Day/Week/Month/Year), Tasks as Reminders, Calls as Mail, System as Settings, and Recovery as a guided flow. Added the feature checklist and the module split, and left out drag-to-move on purpose.
- **Rev 6:** the owner asked for apple.com's colours exactly, with no switching for the device. All dark-mode rules were removed from the three pages.
- **Rev 4:** the owner's answers. The talk page is light and follows dark mode. SF Pro is used by name from the installed fonts, with Inter only as a fallback. The glass is the apple.com recipe. The dashboard gets a full reskin with the same behaviour (superseded by rev 5).
- **Rev 3:** checked against legal and the north star. Dropped the SF Pro files (licence) in favour of the system SF plus self-hosted Inter, and dropped all Apple assets and copy. Faster dashboard motion (60ms stagger instead of 150ms) so staff aren't slowed down. Glass is kept off tables for performance. The talk page keeps the orb as the focus so the call stays the hero. Nothing on the page says "automated", in line with the north star's honesty rule. (The current `<meta description>` says "automated receptionist": see question Q5.)

## 8. As built (14 Oct)

**Files:**
- **Shared:** `static/ui/emma-ui.css` (tokens, glass, pills, floating-label fields, segmented control, reveal, toasts), `static/ui/emma-ui.js` (reveal, segmented thumb, toasts, shake), `static/fonts/inter-var-latin.woff2` (+ OFL licence).
- **Login:** `static/dashboard/login.html`, `login.css`, `login.js`.
- **Talk page:** `static/index.html`, `static/talk.css`, `static/talk.js`. `app.js` and `app.css` are unchanged, because the patient page still uses them. `talk.js` only listens to the `emma-call-state` event that `app.js` already sends.
- **Dashboard:** `static/dashboard/index.html`, `dashboard.css`, `dashboard.js` (rewritten; every API call and action from the old file is kept).

**Deviations from the plan, with reasons:**
- **`dashboard.js` stays one file** instead of the module split in 4.4. Moving 1,600 tested lines into nine modules risked breakage for no visible gain. It is sectioned the same way and can be split later.
- **Message bubbles:** the caller is grey on the left; Emma (blue) and staff (green) are on the right. From the staff's side, the clinic is "me", as in Messages. The plan had these the other way round.
- **Day view puts the doctors in columns,** each with its colour as an underline, so a busy clinic day doesn't stack into one column. Week view uses the days as columns, as in Calendar.
- **The dashboard's motion is faster than apple.com's on purpose:** the Today tiles stagger 60 ms apart and rise 12 px, rather than 150 ms and 30 px.

**Checked on the 8003 preview copy:**
- **Login:** light and dark, a wrong password, a real sign-in.
- **Today:** the tiles show live data.
- **Calendar:** Day/Week/Month/Year/List, and the event inspector.
- **Tasks, Calls, Recovery and System** all render.
- **A typed call:** the talk page's in-call states, the dashboard's live bubbles, Take over → Say → Hand back, and End.
- **Phone width (375 px):** tab bar, no horizontal scroll.

The preview copy used a copied database and a test login.

**REA evidence:**
- apple.com home capture: `ev_7be7db90…`
- Apple sign-in screenshot: `ev_dbda65b5…`
- our talk page capture: `ev_fe8d6f94…`. It shows only same-origin files (2 stylesheets, 4 scripts) and no console messages.

**Built in the second pass (14 Oct, evening):**
- System is now Settings-style: an index of groups on the left with one pane at a time. On a phone it is a list you tap into, with a "‹ System" back button, and it always opens on the list.
- Calls has Mail-style filters (All, Booked or changed, Hung up, Recovery, Live) and a search by number, call ID or outcome. Up and down arrows move through the list. Opening a call from a task or a recovery call clears a filter that would hide it.
- The talk page's "Staff" link shows only when this deployment has a staff login set up (`/dashboard/api/auth` → `configured`).
- The Today appointments tile's "N more" counts only the appointments still to come.

## 9. Feature checklist (old dashboard → new), tested 14 Oct on the 8003 preview copy

**Mechanical check:**
- **API calls:** all 34 distinct dashboard API calls in the old `dashboard.js` are in the new one; none are missing.
- **Element IDs:** every element ID the old code used still exists, except three replaced on purpose:
  - the top-bar chips `chip-call` and `chip-tasks` became the sidebar's live dot and counts;
  - `appt-day-controls` became the calendar's arrows.

**Clicked through by hand, each with its result checked:**

| Area | Action | Result |
|---|---|---|
| Sign-in | wrong password, then a real sign-in | the error shows and the fields shake; the sign-in redirects into the dashboard |
| Appointments | New appointment → Find free times → pick a slot → Book | toast "Appointment booked." |
| Appointments | Day/Week/Month/Year/List, ‹ › Today, mini month, doctor show/hide | each view renders the right range |
| Appointments | click an event → inspector → Move → Find free times → Move it | the toast appears and the event moves |
| Appointments | inspector → Cancel → Cancel appointment | the toast appears and the event shows struck through |
| Appointments | Past list → Attended, No-show | toasts "Completed." and "No-show." |
| Appointments | Export CSV (the button's URL fetched) | 200, `text/csv`, correct header row |
| Tasks | tick done, then reopen from Done | the task leaves the Open list, then comes back |
| Tasks | a task's "Call" link | opens Calls with that call selected (a call purged from the data shows "No such call.", as before) |
| Calls | filters, search, select a call | the counts are right, and the transcript shows as bubbles |
| Calls | Delete call data → confirm | toast; the transcript shows "(text removed)" |
| Live call | typed call: bubbles, Take over → Say → Hand back, End | all reflected live |
| Recovery | Block a doctor → preview → Start calls → confirm | "Ringing", toasts, a campaign card with a progress ring |
| Recovery | Pause → Resume → Stop → confirm | badge paused → running → stopped |
| Recovery | Lift block → confirm; campaign CSV | block removed; CSV 200 |
| Recovery | Patients' calling hours → Save | the row is added and a toast appears |
| System | closures add/remove, do-not-call add/remove | the lists update |
| System | staff add, switch off | the row is added, then shown greyed |
| System | doctor Edit → Save; Add doctor; Switch off | messages shown; the row is greyed when off |
| System | privacy export (API) and Erase → confirm | export 200; "Erased: 1 appointments…" |
| System | audit log | records every action above |
| Keyboard | Tab through the sidebar and Today tiles | every control is reachable in order, with a 2 px focus ring |
| Phone (375 px) | tab bar, Calendar Day view, System list → pane → back | no horizontal scroll |
| Fonts | SF Pro installed; Inter forced | SF Pro is used; Inter loads (47 KB, same origin) and renders |

**Not tested, with the reason:**
- **"Retry" and "Retry all failed" in Calendar sync:** calendar sync is off on the preview copy, so nothing can fail. The button is correctly disabled.
- **Privacy "Download their data" as a real browser download:** the endpoint behind it was checked instead.
- **A real microphone call on the talk page:** the preview copy has no ElevenLabs key, and a microphone call needs a person speaking. This is left for the owner on the main server.

`dashboard.js` stays one file, on purpose (see "Deviations").

## 10. REA deep pass: Apple's shipped JavaScript (14 Oct, evening)

**Method (the reverse-engineer-anything skill's staged workflow):**
1. A passive REA capture of apple.com/iphone with script sources (`ev_58895281…`).
2. The 109 scripts exported with their checksums verified (`ev_13fc516b…`).
3. A static application graph of them (`ev_ec850501…`): 109/109 parsed, no failures. Coverage is partial for four very large scripts, as REA reports.
4. Literal searches over the same exported bytes where the graph had no node.

Nothing was executed; these are static facts from the shipped files.

**Findings ledger.** O = observed in Apple's shipped code, I = inferred.

| # | Question | Answer | Type | Where |
|---|---|---|---|---|
| R1 | Scroll-in easing (was unknown F9) | `easeFunction:"easeInOutQuad"` for both the opacity and the translateY keyframes | O | `overview/main.built.js`, StaggeredFadeIn `_staggeredKeyframes` |
| R2 | When the scroll-in starts | `start:"t - 85vh"`: when the group's top reaches 85% of the viewport. The 70% in earlier notes was wrong | O | same, `_setupTimeline` |
| R3 | Scroll-in timings | read at runtime from the CSS variables `--staggered-delay` 0.15, `--staggered-opacity-duration` 0.9, `--staggered-translate-y` 30px, `--staggered-translate-y-duration` 0.7; off when `html.reduced-motion`; the group gets `staggered-start`, then `staggered-end` | O | same, plus the CSS from section 2 |
| R4 | Card hover scale (was unknown F17) | `--card-element-scale = (width + 6) / width`, set on mount and on resize, so a card grows by 6 px whatever its size; CSS `html.no-touch .card-hover:hover{transform:scale(var(--card-element-scale))}` | O | `overview/main.built.js` (Card), `overview.built.css` |
| R5 | Nav menu hover | opens after a **120 ms** debounced hover (`onMenuItemMouseOver`, `s=120`); skipped on touch; leaving the nav closes it; the curtain click closes it; focus returns to the trigger **300 ms** after closing | O | `globalheader.umd.js` |
| R6 | Other scroll effects | the scroll triggers are media preload (`t - 200vh`), play only while visible (`t - 100vh` → `b + 100vh`), will-change toggles and the chapter nav. No parallax and no scroll hijacking | O (triggers) / I (intent) | `overview/main.built.js` |
| R7 | No-JS safety | content that fades in stays visible without JavaScript (`html.no-js` rule) | O | CSS, section 2 |

**Applied to Emma's pages:**
- **Reveal curve:** now `cubic-bezier(0.455, 0.03, 0.515, 0.955)` (easeInOutQuad, R1).
- **Reveal trigger:** an IntersectionObserver with a −15% bottom margin, which is the 85vh trigger (R2).
- **End of the reveal:** marked `is-done`, so each element's own transitions apply again, like `staggered-end` (R3).
- **Card hover:** `.tile-hover` cards get `--card-element-scale = (offsetWidth + 6) / offsetWidth` from a ResizeObserver (R4). For example, a 495 px tile scales by 1.01212.
- **No-JS safety:** `static/ui/head.js`, a same-origin classic script in `<head>` (CSP-safe), sets `html.js`. Content is hidden for the reveal only when JavaScript runs (R7).
- **Focus return:** Escape on the appointment inspector or the phone "More" sheet returns focus to whatever opened it (R5's focus return). Our sidebar has no hover menus, so the 120 ms hover delay has nothing to apply to.

**Verified on our pages:**
- The REA capture of the sign-in page (`ev_0dba6c8b…`) shows `head.js` first in `<head>`, all five files same-origin, and no console messages.
- In the browser, the Today grid gets `is-in` then `is-done`, its transition becomes the 0.3 s lift curve again, and the scale is 1.01212.
- Tests: 1,015 passed, 1 skipped.

**Residual unknown, recorded in REA:** `unk_d588f18b…`. Static evidence can't prove that apple.com's runtime applies R1/R2 unchanged. The probe to settle it is `observe_web_execution` while scrolling a stagger group into view. It is low severity: the live DOM earlier showed the `staggered-start` classes being applied.

## 11. Deep pass: motion, depth and interaction (22 Oct)

**Why:** the owner wanted the redesign pushed much further, especially the motion and interaction details. Three researchers measured apple.com on 22 Oct 2026, each in its own browser tab. REA's MCP server timed out at the start, so the evidence comes from the built-in browser: computed styles, `document.styleSheets` rule text and `getAnimations()`.

**Pages covered:**
- AirPods: /airpods/, /airpods-pro/, /airpods-max/
- iPhone: /iphone-18-pro/ (there is no plain "iPhone 18" page), /iphone-air/, /iphone-duo/ (it **exists**, first in the iPhone nav)
- Apple Account sign-in: account.apple.com, with the form in an idmsa.apple.com iframe

**How to read the values:**
- O means observed: a computed style or Apple's shipped CSS rule. O(css) means the value comes from the CSS rule text rather than a live sample. I means inferred.
- Background tabs kept Apple's pages in `html.no-js`. So the modal open/close keyframes, live scroll sampling and the bubble selector's X/Y squash were **not** measured at runtime; the declared CSS values are used instead.

### 11.1 AirPods (A1-A28)

Scope: /airpods/, /airpods-max/, /airpods-pro/. Things already in APPLE_DESIGN_PLAN.md sections 2 and 10 are not repeated here (type scale, ink colours, nav glass, `cubic-bezier(.4,0,.6,1)`, card hover `+6px` scale, StaggeredFadeIn).
O = observed (computed style or a shipped CSS rule read from `document.styleSheets`), I = inferred.

**Method caveat:** my browser tab was a background tab. The page's `<html>` kept `no-js` and CSS transitions finished instantly, so I couldn't sample runtime animations frame by frame. All motion values below are read from the shipped CSS rules (O-CSS), not timed at runtime.

#### Page 1: /airpods/ (overview)

**A1. "+" tile overlay (the in-card "modal").** O-CSS
- It is not a page modal. A hidden checkbox `.tile-overlay-toggle` plus a `<label>` button flips the card in place.
- `.tile-overlay-content` covers the card (absolute, 100%, `border-radius: inherit`), fill `#f5f5f7`, padding `var(--global-card-padding)` = 28px. It goes from `opacity:0; visibility:hidden` to 1 with `transition: opacity .5s ease-in-out`.
- No backdrop and no scroll lock: body overflow stays visible. On narrow screens the overlay scrolls inside itself (`overflow-y: scroll` at <=734px).
- The "+" button rotates 45deg into an "x": `transform .5s cubic-bezier(0.66,0,0.2,1)`, while its colour and background change over 100ms linear.
- With reduced motion, transitions are set to none.
- Overlay text: 19px / 600 / +0.012em.
- Use on Emma: open an appointment's details inside its Today tile (a cross-fade over 0.5s, no scrim) rather than in a sheet; rotate the "+" to an "x" with `cubic-bezier(.66,0,.2,1)` 500ms.

**A2. Round "+" tile button.** O-CSS
- Sizes: wrapper 37px, button 33px at desktop; 34/30px at <=1068px; 29/25px at <=734px. Radius 50%, 16px from the bottom.
- Dark variant: bg `rgb(51,51,54)`, hover `#37373a`, active `#2f2f32`; icon `#d6d6d7`, white on hover.
- Solid light variant: bg `rgb(232,232,237)`, hover `#ececf0`, active `#dfdfe4`; icon `rgba(0,0,0,.56)`, `.64` on hover.
- Every state change is `background-color 100ms linear`. There is no scale on hover. The wrapper is a larger hit area than the button.
- Use on Emma: small round icon buttons in tiles and the transcript: 33px visual inside a 37px+ hit area, 100ms linear colour states, three greys (rest, hover lighter, active darker).

**A3. Gallery ("consider" section).** O
- `.scroll-container`: `scroll-snap-type: x mandatory`, `scroll-padding-inline: calc(50% - 0.5*min(1680px, 87.5%))`, which aligns snaps to the content column.
- `.card-set` is a grid: `grid-auto-flow: column; grid-auto-columns: min-content`, gap 20px. Cards are 344 x 632 at desktop, `scroll-snap-align: start`, radius 18px (`--sk-tile-border-radius`), `overflow:hidden; isolation:isolate`, fill `#f5f5f7`, no shadow.
- No dots on this gallery; paddles only.
- Section padding: 150/150px at desktop, 130/120 at <=1068px, 76/90 at <=734px. Headline width 700px; gallery 52px below the headline.
- Use on Emma: if the Today grid or the week strip scrolls sideways, use x-mandatory snap with scroll-padding equal to the content gutter, 20px gaps and 18px radius.

**A4. Paddle-nav (gallery arrows).** O
- Diameter 36px, fully round, placed 18px in from each edge, vertically centred.
- bg `rgba(210,210,215,.64)`, hover `rgba(223,223,227,.6976)`, active `rgba(193,193,198,.6544)`; arrow `rgba(0,0,0,.56)`.
- No `backdrop-filter`: the translucency is alpha only.
- Transition `background .1s linear, color .1s linear, opacity .1s linear`. Disabled state: `opacity: var(--sk-link-disabled-opacity, .42)` with `pointer-events:none`.
- Focus ring: 2px `#0071e3`, offset 3px. Variant `with-paddlenav-onhover` shows the arrows only while hovering on `no-touch` devices.
- Use on Emma: Calendar's prev/next week buttons: 36px circles in translucent grey, disabled at .42 opacity.

**A5. Media play/pause button.** O-CSS
- 36px circle, 20px in from the bottom-right.
- Light scrim: `rgba(210,210,215,.64)`, hover `rgba(223,223,227,.6976)`, active `rgba(193,193,198,.6544)`. Icon `rgba(0,0,0,.56)`, hover `.72`.
- Press is `transform: scale(0.95)` plus the active colour. It is hidden (`opacity:0`) until the media has loaded.
- Use on Emma: the recording play button in the call transcript: the same 36px translucent circle, `scale(.95)` on press, shown only after the audio is ready.

**A6. Chapter nav (the product strip under the local nav).** O
- Each item runs `@keyframes chapternav-slidein`: 350ms `ease`, `fill: backwards`. Opacity goes 0 to 1 while the item moves from `translateX(160px)` to 0 (the shift starts at the 1% keyframe).
- Paddles are 34px wide. Chevron opacity is .8 at rest and 1 on hover, over 200ms linear. Disabled paddles get `opacity:0`.
- Item padding 0 20px.
- Use on Emma: when the sidebar or a segmented strip first appears, slide the items in from +160px with opacity over 350ms ease, once, never on later navigation.

**A7. Sticky local nav, stuck state.** O-CSS
- Stuck bg `rgba(250,250,252,.92)` without backdrop-filter support, `.8` with it; dark `rgba(22,22,23,.88)` / `.8`.
- The background crossfades with `transition: background .24s cubic-bezier(.28,.11,.32,1)` when it sticks.
- A JS fallback (`no-css-sticky`) switches it to `position:fixed` and adds a placeholder of `--r-localnav-height`.
- Use on Emma: the dashboard top bar: transparent at scroll 0, then `rgba(250,250,252,.8)` with blur after it sticks, crossfading over 240ms.

**A8. Buttons.** O-CSS
- `.button-elevated`: 17px / 400, padding 12 x 22px, min-width basis 70px; the hover and active backgrounds come from `--sk-button-*-hover/active` variables (no transform).
- An inline white pill: `.learn-more` white bg, black text, hover `rgba(255,255,255,.9)`.
- Hero "learn more" link: 14px / 400 / -0.016em.
- Use on Emma: the Call / Type-instead pills change background only on hover; press feedback is a colour change (any scale belongs to round icon buttons only, A5).

**A9. Overview type.** O
- Page title 34px / 600 / -0.011em / 50px. Hero headline 48px / 600 / -0.003em / 52px. Feature line 21px / 600 / +0.011em / 25px.
- Compare headline 64px / 600 / -0.009em / 68px. Compare device name 21px / 600. Compare copy 14px / 400 / -0.016em / 18px.
- Use on Emma: the dashboard page title can sit at 34px / 600 (rather than 40+) for an app-like density; tile titles 21px / 600 / +0.011em.

**A10. Curves found on this page (excluding ones already in the plan).** O
- `cubic-bezier(0.66,0,0.2,1)` (icon rotation, 500ms).
- `ease-in-out` 0.5s (overlay fade).
- `ease` 350ms (strip slide-in).
- Also present: `cubic-bezier(.4,0,.6,1)` x28, `(.28,.11,.32,1)` x4, `(0,0,.5,1)` x1.
- Use on Emma: limit ourselves to these four curves.

#### Page 2: /airpods-pro/

**A11. Page modal (`.ric-modal`, opened from the "+" `card-control` on story cards).** O-CSS / computed vars
- Scrim: `::after` filled `rgba(0,0,0,.48)` plus `backdrop-filter: blur(20px)` (`ric-modal-scrim-blur`).
- Panel: white, radius **28px** on all corners, **no box-shadow**. Width 816px at desktop, 692px at <=1068px, 100% at <=734px; a wide variant is 1029px.
- Spacing: overlay padding-top 40px, content padding-top 76px.
- Close button: 44px hit area holding a **36px dark circle** `rgb(29,29,31)` (hover `#272729`, active `#18181a`) with a white glyph. Colour/background change over `100ms linear`. The wrapper is `position:sticky; top:0`, so the close button stays in view while the modal content scrolls.
- The modal root scrolls itself (`overflow:auto`), so the page behind doesn't scroll. That is a scroll lock by containment. `opacity:0` is the resting state.
- The open/close keyframes are JS-driven (no transition rules exist in the CSS) and couldn't be timed in a background tab.
- Use on Emma: appointment and patient sheets: a 48% black scrim with 20px blur, a white 28px panel with no shadow, a sticky 36px dark round close button inside a 44px target, and the panel scrolls itself.

**A12. "+" card control on story cards.** O
- A 44px wrapper holding a 36px `rgb(29,29,31)` circle (hover `#272729`) with a white plus. The card itself is the button (radius 28px).
- On dark tiles it inverts to `#f5f5f7` with a black glyph (hover white).
- Use on Emma: a "+" on a Today tile opens the detail sheet: a dark 36px disc, inverting on dark tiles.

**A13. Floating all-access pass (glass pill) tokens.** O, adds to section 2
- `--aap-stagger-fast:50` (ms per item), `--aap-stagger-slow:200`, `--aap-content-delay-fast:100ms`, `--aap-content-delay-slow:150ms`.
- `--aap-min-height:56px`, `--aap-margin:32px` (sticky top), `--aap-bubble-gap:16px`, `--aap-blur:7px`. The background blur is `blur(calc(var(--aap-blur)*(2 - var(--alpha))))`, so it starts at 14px and settles to 7px as alpha reaches 1.
- `--aap-accent-color:#0071e3`. Radius 28px.
- The intro element starts as a 56px circle at `scale(0.01)` and grows; the background is `translate(-50%,-50%) scale(var(--scale))` with its width animated from 56px. This is the "materialize" effect.
- At 678px wide: bg `rgba(232,232,237,.72)`, `blur(7px)` (computed).
- Use on Emma: the talk page's Call / Type-instead pill group: enter as a 56px dot that scales up and widens, its blur easing 14px to 7px, 28px radius.

**A14. Colour picker inside the pass (swatch expansion).** O-CSS
- Collapsed, the pass shows one 24px active swatch, margin-end 14px (19px on phones). It has a conic-gradient `::after` shading overlay (black .08 to .48 to .08), which is a fake specular bevel.
- Expanded, the swatch buttons go from `opacity:0; scale(<1)` to `opacity 1, scale(1)` over **400ms `cubic-bezier(0.3,2,0.5,1)`** (strong overshoot), delayed `index * 50ms`.
- Row gap 8px; labels hidden (`opacity:0`).
- Use on Emma: when the segmented control or reaction chips expand, pop items in with a 400ms (.3,2,.5,1) overshoot and a 50ms stagger. Overshoot only here, since it is a user-triggered expansion.

**A15. The stretching-pill progress dot (media card gallery).** O-CSS
- Dots are 8px (`--sk-dotnav-size`) with 8px inline margins, radius 10px.
- The active dot widens to `--progress-bar-active-width` (**48px**, 32px on small screens). Its width is a continuous function: `min-width = (48-8) * (1 - |index - autoplayProgress|) + 8`, so neighbours morph while scrolling rather than snapping.
- The current dot's `::after` fill bar grows from `min-width:15%` via `@keyframes aap-animate-progress` over `--animation-duration` (the autoplay length). It is paused when autoplay pauses.
- Colours: rest `rgba(0,0,0,.42)`, hover `.54`, current `.8`; dark `rgba(255,255,255,.36/.48/.8)`. Inside the pass the track uses `--aap-dotnav-background` with `--aap-active-fill-color`.
- Captions parallax: `translateX((p + 1.6p) * offset)`, `opacity = 1 - |p| * 3.2`, where p = progress minus the autoplay index, so a caption fades out within about 0.3 of a slide. With reduced motion: `transform:none; opacity:1`.
- Use on Emma: the call-progress or "listening" indicator, or an onboarding carousel: 8px dots and a 48px active pill with a filling bar for the time remaining; widths interpolate continuously.

**A16. Scroll-scrub sticky section ("audio performance").** O-CSS
- `.sticky-container` height `calc(150vh + 100vh)` (`150lvh + 100lvh` on phones). The `.sticky-element` is `position:sticky; top:0; height:100lvh`, only under `html.enhanced`.
- The image sequence is scrubbed by JS. The end frame is `opacity:0; visibility:hidden` until done, and without enhancement the static end frame shows.
- There is no CSS scroll-timeline: the scrubbing is JS-driven (I).
- Use on Emma: none needed. If the sign-in page gets a story, a 150vh sticky scrub is the pattern. Never on the dashboard.

**A17. Other sticky elements.** O
- The local nav uses `position:sticky` (`.css-sticky`).
- The gallery paddlenav sits in a `.sticky-element` of `--sticky-paddlenav-container-height`, so the arrows ride along while a tall gallery scrolls.
- On small screens the product-viewer close button is `sticky; top: 20px + localnav height`.
- Use on Emma: Calendar's prev/next buttons can be sticky within a tall week view.

**A18. Bevel and inner-glow shadows (glass edge).** O
- `.control-item-bg`: `box-shadow: inset 0 0 .5px rgba(0,0,0,.11)` (a hairline inner edge, not a drop shadow), `transform-origin: center bottom`, background-colour transition 250ms linear.
- Product-viewer close button: `inset 1.8px 1.8px .5px -1.4px #6e6e73, inset -1.5px -1.5px .5px -1.1px #6e6e73` (a two-sided bevel).
- `.tile-shadow`: `8px 8px 16px rgba(0,0,0,.08)` is the only drop shadow token.
- Toggle knob: `0 1px 3px rgba(0,0,0,.3)`, moving over `inset-inline-start 240ms (.4,0,.6,1)`.
- Use on Emma: the glass orb and pills: inner 0.5px glow at .11 alpha instead of borders; the toggle knob in Settings 14px with `0 1px 3px rgba(0,0,0,.3)`, 240ms.

**A19. New curves on Pro.** O
- `cubic-bezier(0,0,0.2,1)` x35 (decelerate, the most common after .4,0,.6,1).
- `(0.3,2,0.5,1)` and `(0.4,0,0.3,2)`: overshoot pops (swatches; a video DVR dot scale over 240ms after 80ms).
- `(0.34,2.16,0.64,1)` 0.7s after 0.25s (3D viewer canvas fade).
- `(0.33,1,0.68,1)` 300ms (gallery caption fade).
- `(0.4,0.1,0.4,1)` 1s (video controls fade in on hover).
- `(1,0.1,0,0.3)` 1s infinite (spinner bars, 7 x 18px, radius 27.5px).
- Local-nav curtain: opacity and backdrop-filter over `320ms (.4,0,.6,1)` with a 240ms delay when closing, 0 when opening; size is cut at 560ms.
- Use on Emma: `(0,0,.2,1)` for elements arriving (toasts, sheet content); delayed close and immediate open for scrims; video-control-style 1s fade for the orb caption idle state.

**A20. Pro type (desktop / at 678px, the <=734px breakpoint).** O
- Marquee eyebrow: 24/600/+0.009em/28, then 21/600/+0.011em/25.
- Marquee headline: 56/600/-0.005em/60, then 32/600/+0.004em/36.
- Section headline: 48/600/-0.003em/52 (max-width 75%), then 28/600/+0.007em/32.
- Gallery headline: 17/600. Pass item body: 17/400/-0.022em/25, then 14/400/-0.016em/20.
- On phones: content column 328px with 23.5px gutters; cards are radius 28px and 308px wide; section padding 96 / 196px.
- Use on Emma: phone talk page: a headline at 32/600, a caption at 17/400, 24px side gutters (rather than 16).

#### Page 3: /airpods-max/

**A21. Segmented tab nav (`.tabnav`), the closest match to our segmented controls.** O-CSS
- Track: a platter 44px high, padding 4px, bg `rgb(232,232,237)`, radius 999px, overflow hidden. Inner height 36px. Items have 16px side padding, 14px / 400 / -0.016em text.
- The indicator is a separate absolutely-positioned pill (`top/bottom: 4px`, radius 999px, bg `rgb(29,29,31)`). It animates **`left, width` over 320ms ease-out**, positioned through `--tabnav-indicator-start` / `--tabnav-indicator-width` set by JS.
- Item text colour goes black to white on the active item, with a **160ms delay, 160ms ease-in**, so the text flips as the dark pill arrives under it. Losing focus: 120ms ease-out. Link opacity .8 at rest (100ms ease-in-out).
- If the strip overflows, the items scroll via `translateX(--tabnav-items-offset)` over 320ms ease-out, and the edges fade out with a 22px `mask-image` linear gradient behind 36px round paddles. A paddle shows over 80ms with a 40ms delay and hides after a 200ms delay.
- `.tabnav-disable-transitions *` turns transitions off for the first layout.
- Use on Emma: our segmented controls (Day/Week, transcript filters): a `#e8e8ed` 44px platter with 4px padding, a dark sliding pill moving left/width over 320ms ease-out, and the label colour flipping after 160ms. Turn transitions off on first paint.

**A22. Glass "bubble" selector in the pass (size/toggle nav).** O-CSS
- Options are 48px-high links (min-width 48px, radius 28px) inside a 56px pass with 3px padding.
- One shared `__bubble` slides under the selected option: `transform 200ms ease-out`, 4px inset.
- Its inner layer takes `translate3d(var(--x),0,0) scaleX(var(--scale-x)) scaleY(var(--scale-y)) scale(var(--scale))` with `will-change: transform` and width `var(--bubble-width)`. JS sets separate X/Y scale, which is the liquid stretch-and-squish while it moves.
- Bubble colour `#1d1d1f` (dark theme `#f5f5f7`). Icon/label colour changes over `200ms cubic-bezier(0.53,-0.01,0.17,1)` (250ms to the checked state, 400ms for toggles). Label 16px / 600 / -0.35px.
- Focus ring: `0 0 0 3px #fff, 0 0 0 5px #0071e3`.
- Use on Emma: the Call / Type-instead switch on the talk page: one dark bubble sliding under the chosen pill, slightly stretched in X (about 1.1) and squashed in Y (about 0.92) mid-move, then settling. The label colour uses `(.53,-.01,.17,1)`.

**A23. Colour-nav swatches (the Max colours).** O-CSS
- Entering: `opacity 0 -> 1, scale(.5) -> 1` over 400ms `cubic-bezier(0.3,2,0.5,1)` with an `index * 50ms` stagger.
- Leaving (`.inactive`): back to `.5` over **200ms ease-out**, with no stagger.
- So entering is slower and bouncy, leaving is fast and plain.
- Swatch ring: `colorindicator` uses a conic-gradient border (black .08, .48 at 64.8-115.2deg, .08) to fake a lit metal rim; dark uses white at the same alphas.
- Use on Emma: dashboard chip/menu reveal: enter 400ms with overshoot and a stagger, exit 200ms ease-out together. A conic-gradient rim can give the orb a lit edge.

**A24. Masked glass scrim over hero media.** O-CSS
- `.marquee-scrim`: `background: rgb(255 255 255 / 56%)` (60% at <=734px, 50% at <=480px), `backdrop-filter: blur(20px) saturate(1)`.
- `mask-image: linear-gradient(180deg, transparent, #000 100%)` (the stop moves to 50% on phones), so the blur fades in from nothing at the top to full at the bottom. It is a progressive blur behind the text, not a hard-edged panel.
- Use on Emma: caption backing under the talk-page orb, and the dashboard's scroll-edge effect: white at 56% with 20px blur, masked by a 180deg gradient, instead of a solid card or a hairline.

**A25. Pass blur on enhanced pages.** O-CSS
- With `html.enhanced`, the per-element `__background` blur is unset and one `all-access-pass__global` layer carries `blur(var(--aap-blur))` (7px).
- So a single glass layer sits behind several pills; a stack of separate blurs would cost more and look worse.
- Use on Emma: put the Call and Type-instead pills on one shared glass layer, never blur on blur.

**A26. Paddle-nav for a tall gallery.** O-CSS
- `.scroll-gallery-paddlenav .sticky-element`: `position: sticky; top: calc(100% - var(--paddlenav-height, 60px))`, height `paddlenav-height + padding`.
- The arrows stick to the bottom of the viewport while a tall gallery passes.
- Use on Emma: on the phone Calendar, pin the prev/next arrows at the bottom of the viewport.

**A27. Max type (desktop).** O
- Marquee eyebrow: 28/600/+0.007em/32. Marquee headline: 64/600/-0.009em/68.
- Subsection L1: **80/600/-0.012em/84** with a 24/600/+0.009em/28 subhead. Subsection L2 and section headline: 48/600/-0.0014em/52.
- Gallery headline: 24/600/+0.009em/28. Body-large copy: 21/600/+0.0015em/29.
- Use on Emma: the sign-in page hero could use 64/600/-0.009em; don't go past 80px anywhere.

**A28. Spinner and loading.** O-CSS
- The local `progress-indicator-animating` keyframe steps opacity 1, .9, .7, .6, .4, .3, .2, .13 across 8 segments: the classic 8-spoke iOS spinner.
- The video loader bars are 7 x 18px, radius 27.5px, running `cubic-bezier(1,.1,0,.3)` over 1s, infinite.
- Use on Emma: the "connecting" state on the talk page: an 8-spoke spinner with that opacity ladder, rather than a ring spinner.

#### Gaps and unknowns
- The modal (A11) open/close keyframes, the tile overlay's live timing and the bubble's X/Y squash values are set by JS at runtime. The background tab never ran Apple's enhancement JS (`<html>` stayed `no-js`), so I couldn't sample them. The next probe is the same pages in a foreground tab, reading `document.getAnimations()` during a click.
- Sticky sampling (opacity/scale at scroll positions) wasn't possible for the same reason. The scroll-scrub sections are JS image sequences (A16).
- The "375px" pass ran at an effective `innerWidth` of 678px, which is the <=734px breakpoint. Phone values are the CSS breakpoint values, not a true 375px render.

### 11.2 iPhone 18 Pro, iPhone Air, iPhone Duo (P1-P35)

Scope: /iphone-18-pro/, /iphone-air/, /iphone-duo/. Measured in the built-in browser at 1024px wide (desktop) and 375px.
O = observed (computed style, CSS rule text or live DOM), I = inferred. Items already in APPLE_DESIGN_PLAN.md sections 2 and 10 are not repeated.

**iPhone nav (from /iphone/):** the lineup links are /iphone-duo/, /iphone-18-pro/, /iphone-air/, /iphone-17/, /iphone-17e/. **iPhone Duo exists** and is first in the list. There is no plain "iPhone 18" page; the flagship page is iPhone 18 Pro (title "iPhone 18 Pro and iPhone 18 Pro Max"). (O)

#### Page 1: /iphone-18-pro/ (dark-theme page; 6,606 rules in overview.built.css)

| # | Element | Measured | Type | Use on Emma |
|---|---|---|---|---|
| P1 | Local chapter nav is now a **floating glass capsule**, not a full-width bar | `#localnav` sticky top 0, height = 52px min + 10px padding x2 = 72px. Inner `.localnav-wrap`: inset 10px from viewport edges, max-width 1024px, `margin-top:10px`, `padding-block:12px`, `backdrop-filter: blur(20px)`, bg `rgba(18,18,18,.8)` (dark), ring `box-shadow: 0 0 0 1px #282828` (no drop shadow), `border-radius: calc(32px - 14px*(1 - var(--progress)))`, i.e. 32px collapsed, 18px when expanded | O | Dashboard top bar / talk-page header as an inset floating capsule: 10px inset, radius 32px, 1px ring instead of a shadow |
| P2 | Local nav show/hide | container `.theme-reveal-on-scroll`: wrap `transition: transform .23s`; the nav sits at `translateY(-72px)` (hidden) while scrolling down. Expand/collapse vars: `--localnav-anim-duration: .4s`, `--localnav-anim-fn: ease-out`, `--explore-in-duration .15s`, `--explore-out-duration .1s`; `html.reduced-motion` sets all to 0s | O (reveal-on-scroll-up direction: I) | Long Messages transcript / Calendar: hide the top capsule on scroll down, bring it back on scroll up, 230ms |
| P3 | Local nav "Explore" expand overlay | `.localnav-overlay-background`: fixed full-screen, `backdrop-filter: blur(20px)`, no tint, opacity 0 to 1 over .4s ease-out | O | Behind an expanded menu: plain blur(20px) scrim, no grey tint, 400ms ease-out |
| P4 | Local nav pills | "Explore" pill: radius 120px, 36px tall, bg = nav body, ring `0 0 0 1px #6e6e73`, hover bg `rgb(29,29,31)`; "Buy" pill `#0071e3`, radius 170px, padding 6px 10px, caption type | O | Secondary pill = 1px ring, primary = solid blue; both full radius |
| P5 | Floating "all-access pass" (AAP) gallery control, new details | `position: sticky; bottom: 32px` (`--aap-margin`), 56px tall, radius 28px. Blur is **tied to an alpha variable**: `backdrop-filter: blur(calc(7px*(2 - var(--alpha))))`, bg `rgba(232 232 237 / calc(1 - alpha*.28))`, so as it settles blur goes 14px to 7px and fill goes solid to 72%. Dark fill `rgba(42,42,45,.72)`. Intro: `.all-access-pass__intro-element` starts `translateX(-50%) scale(.01)` and grows; children stagger by `--aap-stagger-fast: 50`ms / `--aap-stagger-slow: 200`ms, content delay 100/150ms. Hidden for no-JS unless it holds a real link | O | Talk page Call / Type-instead pill bar: sticky 32px from bottom, 56px, radius 28; materialise from a dot (scale .01) and let blur fall 14 to 7px as it settles |
| P6 | Gallery progress indicator (the stretching pill) | Inside the AAP. Inactive dots 8px circles with 8px margins; current item stretches to a 48x8px pill, radius 10px, track `rgba(255,255,255,.36)` (dark); a `::after` fill runs `aap-animate-progress` (width 0 to 115%) **linear over 6.15s** (the slide's autoplay time), fill `#29292a` light / `rgb(245,245,247)` dark. Dot widths are computed from `--autoplay-progress` (`--item-expand-amount = min(1 - abs(index - progress), 1)`), so neighbours shrink/grow continuously | O | Onboarding/tips rotator: 8px dots, current one a 48px pill whose fill tracks time linearly |
| P7 | Generic dotnav (sk) | `--sk-dotnav-size: 8px`, margin 8px, hover 120ms linear, collapse 380ms, stagger 40ms, variable duration 320ms; light dots `rgba(0,0,0,.42)` / hover .54 / current .8; dark `rgba(255,255,255,.36)` / .48 / .8. Scrim variant: 56px tall, `saturate(180%) blur(20px)`, light `rgba(210,210,215,.64)`, dark `rgba(66,66,69,.72)`. Timed variant: current dot expands 8px to 32px over 250ms ease-in, then fills | O | Page dots anywhere: these alphas for light |
| P8 | Play/pause in the AAP | 56x56, radius 28px, hover `rgba(223,223,227,.698)`, active `rgba(193,193,198,.654)`; inline media play/pause `:active { transform: scale(.95) }` | O | Talk-page mute/pause control: 56px circle, these hover/active steps, press scale .95 |
| P9 | Highlights gallery layout | container `overflow-x: scroll; scroll-snap-type: x mandatory`; items `scroll-snap-align: center`, flex gap 20px, card 896x576 at a 1024 viewport (56px side peek), card `.tile-rounded` radius 28px; 5 slides | O | Calendar week strip / onboarding cards: centre snap, 20px gap, 56px peek |
| P10 | Paddle-nav buttons (sk) | 36px circle, `transition: background 100ms linear, color 100ms linear, opacity 100ms linear`; alpha light `rgba(210,210,215,.64)` / hover `rgba(223,223,227,.6976)` / active `rgba(193,193,198,.6544)`, arrow `rgba(0,0,0,.56)` / hover .64; solid `rgb(232,232,237)` / `#ececf0` / `#dfdfe4`; dark alpha `rgba(66,66,69,.72)`; **disabled opacity .42** + pointer-events none; `.with-paddlenav-onhover`: paddles opacity 0 until the gallery is hovered (only `hover:hover and pointer:fine`) | O | Calendar prev/next: 36px alpha circles, hidden until hover on mouse devices, .42 disabled |
| P11 | "+" tile button | 36px circle, `transition: background-color .1s linear`; light `rgba(210,210,215,.64)` / hover `rgba(223,223,227,.6976)` / active `rgba(193,193,198,.6544)`, icon `rgba(0,0,0,.56)` / .64; solid `rgb(232,232,237)` / `#ececf0` / `#dfdfe4`; high-contrast `#1d1d1f` / `#272729` / `#18181a`. No scale on hover | O | "More" on a Today tile: 36px circle, these three steps, colour only, 100ms linear |
| P12 | Hover/press inventory (31 hover and 24 active rules match visible elements) | Hover is almost always a background/colour step only, 100-120ms linear; transforms only on press (`scale(.95)` on play/pause). Button hover `#0077ed`, active `#006edb` (dark theme hover `#0076df`) | O | Hover = colour step 100ms linear; press = scale(.95) on round controls |
| P13 | Modal (ric-modal "page overlay") | Scrim fixed full-screen, `--modal-scrim-blur: 20px`; panel radius 28px top and bottom, content padding 76px top/bottom/inline (phone: 6.25% inline, 60px bottom, max-width 480px, 10px side inset, 28px top); close button **sticky**, 20px from top and end, 44px target; dark close bg `rgb(51,51,54)` / hover `#37373a` / active `#2f2f32`, icon `rgba(255,255,255,.8)` to white | O | Appointment sheet: 28px radius, 76px padding desktop, sticky 44px close at 20/20, blur(20px) scrim |
| P14 | Easing census (all sheets) | `cubic-bezier(.4,0,.6,1)` x155, `(0,0,.2,1)` x35, `(.25,.1,.3,1)` x13, `(0,0,.5,1)` x2, `(.33,1,.68,1)` (fade-gallery caption opacity 300ms), overshoot curves `(.3,2,.5,1)` (colour swatches expanding, 400ms, staggered by index x 50ms), `(.34,2.16,.64,1)` (product-viewer canvas fade .7s after .25s), `(.4,0,.3,2)` (video scrubber 240ms). Most common durations: 80ms, 240ms, .5s, .4s, 100ms, .3s, 320ms, .2s | O | Overshoot only for small pop-outs (swatches, chips): `cubic-bezier(.3,2,.5,1)` 400ms, 50ms stagger |
| P15 | backdrop-filter census | `blur(20px)` x11, `saturate(180%) blur(20px)` x2, `blur(15px)` x1, variable blurs (AAP 7px, modal 20px, ribbon 20px, tab-nav platter 20px, hero gradient scrim 20px). Global-nav curtain over this dark page `rgba(0,0,0,.4)` + `blur(20px)` | O | Standardise on blur(20px); 7px only for the small floating pill |
| P16 | Tab nav (sk tabnav, model/feature switcher) | 44px tall, inner 36px, platter padding 4px, platter `rgb(232,232,237)`, `--tabnav-platter-blur: 20px`; overflow edges masked with a 22px gradient; type 14px/400/-.016em | O | Segmented control: 44/36px, 4px platter padding, #e8e8ed, edge-fade mask when it scrolls |
| P17 | Local nav product title | 32px / 600 / +0.384px (.012em) / 38.7px, SF Pro Display, `#f5f5f7` on dark | O | - |
| P18 | Modal scrim colours and no-JS fallback | `--modal-scrim-fill: rgba(0,0,0,.48)`; light variant `.ric-modal-scrim-light` `rgba(232,232,237,.48)`; with `.ric-modal-scrim-blur` the scrim adds `backdrop-filter: blur(20px)`. Open state is `html.has-modal` (pointer cursor on close); `.modal-crossfade.modal-open { opacity:1; visibility:visible; transition-delay:0s }`. Without JS (`html.no-js`) every modal renders inline in the page, open, with its close button hidden. The second load of this page stayed `html.no-js` in the pane, so the open/close animation, scroll lock and focus move were **not observable** this run | O (animation: not measured) | Sheet scrim: light `rgba(232,232,237,.48)` + blur(20px) on the light dashboard; sheet content must stay readable inline without JS |
| P19 | Type scale, desktop (1024) vs 375 | hero eyebrow 28/600/+.007em/32 to 24/600/+.009em/28; hero headline 64/600/-.009em/68 to 40/600/0/44; section header 40/600/0/44 (max-width 75%) to 32/600/+.004em/36; lockup XL headline 48/600/-.003em/52 (stays at 375 for some); stat headline 56/600/-.005em/60; gallery caption 24/600/+.009em/28 to 17/600/-.022em/21; callout title 21/600/+.011em/25 to 19/600/+.012em/23; modal headline 48/600/-.003em/52, modal topic label 19/600/+.012em/27, modal body 19/400/+.012em/27; footnote body 14/400/-.016em/20; stat caption 12/400/-.01em/16 | O | Modal/sheet body at 19px (not 17) with +.012em is a new size for our sheets; section headers 40 desktop / 32 phone |
| P20b | Run caveat | On the first load of /iphone-18-pro/ JavaScript ran (the progress animation `aap-animate-progress` 6150ms linear was live in `document.getAnimations()`); later loads in this pane stayed `html.no-js`, so live scroll sampling, gallery positions (all items reported at the same x) and modal timing came from CSS rule text, not runtime | O | Treat P5/P6 numbers as CSS-declared, not runtime-sampled |
| P20 | Section padding and tiles | section padding-top 120px desktop, 112px at 375 (highlights bottom 176 to 144); tile radii seen 28px (cards) and 15px (small product tiles); highlights card 896px at 1024, 308px at 375 with 20px gap | O | Dashboard section rhythm 120/112px is page-scale; for in-app use half of it. 15px radius for small tiles |

#### Page 2: /iphone-air/ (light-theme page; 10,466 rules; uses BOTH the new floating localnav and the older ac-localnav bar CSS)

| # | Element | Measured | Type | Use on Emma |
|---|---|---|---|---|
| P21 | Floating localnav capsule, **light** values | `--localnav-body: #fff`, `--localnav-body-alpha: rgba(255,255,255,.8)`, ring `--localnav-outline: #e6e6e8`, Explore ring `rgb(134,134,139)`, Explore hover `#f2f2f2`, overlay blur bg `rgba(255,255,255,.4)`, paddle shadow `rgba(134,134,139,.07)`, disabled link `rgb(134,134,139)` | O | Our light glass header/sidebar: white at .8 + blur(20px) + 1px `#e6e6e8` ring, no drop shadow |
| P22 | Older bar-style local nav (ac-localnav), when stuck | `--localnav-background` transparent until it sticks; scrim state `rgba(250,250,252,.8)` + `saturate(180%) blur(20px)`; dark `rgba(22,22,23,.8)` (.88 variant); keyline `rgba(0,0,0,.16)` as an `::after` 1px line; background transition `background .24s cubic-bezier(.28,.11,.32,1)` and, for collapsible sizes, `320ms cubic-bezier(.4,0,.6,1) 240ms` (delayed so the glass appears just after it sticks); min-height 48px | O | Sticky section headers in Calendar/Messages: go glass only once stuck, 320ms with a 240ms delay, 16% black keyline |
| P23 | Promo ribbon entrance | ribbon drops `translateY(-100%) to 0` over .8s `cubic-bezier(.42,0,.58,1)` (plain ease-in-out); after 1.8s its background cross-fades from blue `#0071e3` to `#f5f5f7` and text from white to `#1d1d1f`, links to `#06c`, each over 1s | O | One-off "new" banner (e.g. a dashboard notice): drop in 800ms, then calm from accent colour to neutral after ~2s |
| P24 | Scroll gallery (feature cards) | `.scroll-container`: `overflow-x: scroll; scrollbar-width: none; scroll-snap-type: x mandatory; scroll-padding: var(--responsive-gutter-width)`, computed `scroll-padding-inline: calc(50% - .5*min(1260px, 87.5%))` (aligns the first card to the content column); vertical padding `10px 0 30px` with matching negative margin so card lift/hover never clips; items `scroll-snap-align: start`, last item `start end`; card width 883px at 1024 | O | Calendar day/week strip: snap start, scroll-padding equal to the content gutter, hide scrollbar, pad 10px top so hover lift is not clipped |
| P25 | Sticky paddle-nav for scroll galleries | `.scroll-gallery-paddlenav .sticky-element`: `position: sticky; bottom: 0`, container height 104px (`--sticky-paddlenav-container-height`); paddles stay in view while a tall gallery scrolls | O | Long Messages list: a sticky bottom control strip (104px zone) for prev/next or "jump to latest" |
| P26 | Pinned scroll section | `html.fusion-enhanced .section-cameras .subsection-fusion-camera .sticky-element { position: sticky; top: 10vh; height: 1000px; overflow: hidden }` (only with JS enhancement) | O | Not needed in-app; the orb hero on the talk page could pin at 10vh if we ever add a scroll story |
| P27 | Product tile image hover | `transform: scale(1.0161)` over `.3s cubic-bezier(0,0,.5,1)`, also on `:focus`; plus the known `.card-hover` `scale(var(--card-element-scale))` | O | Matches our 6px-grow tile hover; apply the same on keyboard focus |
| P28 | Air easing census differences | same top curves as 18 Pro plus `cubic-bezier(.42,0,.58,1)` x26 (ribbon) and `(.28,.11,.32,1)` x4; no CSS scroll-driven animations (`animation-timeline` only appears as empty longhands) | O | No CSS scroll-timeline needed; IntersectionObserver is what Apple uses |
| P29 | Air type (desktop 1024) | welcome eyebrow 34/600/-.011em/50 (`#1d1d1f`); welcome headline is **regular weight** 24/400/+.009em/28; section header 32/400/+.004em/36 (regular, not semibold); a tinted eyebrow 19/400/+.012em/23 in a product colour (`rgb(73,88,97)`) | O | A light, regular-weight headline (400) reads calmer than 600 for the sign-in subtitle and Today greeting |

#### Page 3: /iphone-duo/ (EXISTS: title "iPhone Duo - Apple", light theme, 11,211 rules, 19 sections incl. section-immersive, section-versatility, section-faq)

| # | Element | Measured | Type | Use on Emma |
|---|---|---|---|---|
| P30 | Page system | Same component kit as 18 Pro (identical backdrop-filter census and top easing curves; extra `cubic-bezier(.4,.1,.4,1)` x2 and `(.33,1,.68,1)` x2). Hero 64/600/-.009em/68, hero eyebrow 21/600/+.011em/21, section header 40/600/0/44, gallery caption 24/600/+.009em/28 in `rgba(0,0,0,.88)` (alpha ink, not `#1d1d1f`) | O | Captions over tinted tiles in `rgba(0,0,0,.88)` |
| P31 | Product-viewer colour picker (collapsed to expanded) | Collapsed shows one 24px active swatch (margin-end 14px) with a conic-gradient ring `::after` (`rgba(0,0,0,.08)` to `.48`). Expanding: each swatch button goes `opacity 0, scale <1` to `opacity 1, scale(1)` over **400ms `cubic-bezier(.3,2,.5,1)`** (overshoot), delayed `index x 50ms` (`--aap-stagger-fast`). Swatch 28px (24px reduced); swatch border is a 1.5px ring drawn by a radial-gradient mask; selected ring is a separate 32px `::after`, `2px solid #0071e3` (neutral variant `#1d1d1f`), animated via `--selector-scale` / `--selector-opacity`; the colour name label sits under the current swatch, 100px centred | O | Segmented control / status picker on appointment sheet: selected item gets a separate ring that scales in; options pop out with 50ms stagger and the .3,2,.5,1 overshoot |
| P32 | Product-viewer floating controls ("beats") | pills `rgb(232 232 237 / .72)` (dark `rgb(42 42 45 / .72)`), hover raises alpha to .92; bg change 250ms linear (`--app-pv-background-transition-duration`); phone close button 36px circle, `backdrop-filter: blur(20px)`, inner hairline `inset 0 0 .5px rgba(0,0,0,.11)`, sticky `top: 20px + nav`, `right: 20px`; it hides with `visibility 0ms linear 300ms` (after the fade) and shows with 0ms delay | O | Glass chips on the talk page: .72 alpha resting, .92 on hover, 250ms linear; 0.5px inset hairline instead of a border |
| P33 | FAQ accordion | `.accordion-tray` animates `height` over 400ms (`--accordion-transition-duration`) ease-in-out; off under `prefers-reduced-motion`; dividers `1px rgb(210,210,215)`; button padding 1em top/bottom; icon colour 100ms linear; title 28/600/+.007em/1.143 (24/600/+.009em at <=1068px) | O | Dashboard settings/help disclosure rows: 400ms ease-in-out height, 1px `#d2d2d7` dividers, no transition when reduced motion |
| P34 | Immersive video caption | `.section-immersive .hero-caption`: opacity 0 + visibility hidden, `.show-caption` fades in over .5s `cubic-bezier(.33,1,.68,1)` (easeOutCubic); only under `html.enhanced.inline-media.no-reduced-motion`; video container `transform-origin: center top` | O | Talk-page live caption under the orb: fade each new caption in over 500ms easeOutCubic; show instantly with reduced motion |
| P35 | Duo-specific motion | No fold/hinge/split-screen CSS: selectors with fold/hinge/split/duo returned nothing; the device story is told with video (`section-versatility` start-frame swap). No sticky pinned sections beyond the shared localnav/AAP/modal close | O | Nothing Duo-specific to copy for the web; for the dual-pane layout guidance use the HIG via apple-look |

#### Cross-page summary of curves and durations (O)

| Curve | Where | Duration |
|---|---|---|
| `cubic-bezier(.4,0,.6,1)` | default everywhere (143-171 uses per page) | 240-320ms |
| `cubic-bezier(0,0,.2,1)` | ease-out decelerate (35 uses) | 200-400ms |
| `cubic-bezier(.25,.1,.3,1)` | layout/transform (13) | 300ms |
| `cubic-bezier(.28,.11,.32,1)` | colour/background (ac-localnav) | 240ms |
| `cubic-bezier(0,0,.5,1)` | tile image hover scale | 300ms |
| `cubic-bezier(.33,1,.68,1)` | caption fades | 300-500ms |
| `cubic-bezier(.3,2,.5,1)` | swatch pop-out (overshoot) | 400ms, 50ms stagger |
| `cubic-bezier(.34,2.16,.64,1)` | product viewer canvas | 700ms after 250ms |
| `cubic-bezier(.42,0,.58,1)` | ribbon drop / colour calm-down | 800ms / 1s after 1.8s |
| `linear` | hovers on dots, paddles, tile "+" buttons, close buttons; progress fill | 100-120ms; 6.15s |
| `ease-out` | floating localnav expand, overlay blur | 400ms (150/100ms for Explore in/out) |
| `ease-in-out` | accordion height | 400ms |

Not observed this run: live modal open/close keyframes, scroll lock, focus move (page JS stayed off after the first load; see P20b).

### 11.3 Apple Account sign-in (S1-S20)

Measured 22 Oct 2026, built-in browser, viewport 1024 wide (desktop) and 375x812 (mobile).
O = observed (computed style / rect), I = inferred.
The form lives in a cross-origin iframe `#aid-auth-widget-iFrame` from
`https://idmsa.apple.com/appleauth/auth/authorize/signin?...&skVersion=7&authVersion=8.0.2`.
Outer page measured on account.apple.com; form measured by opening the iframe src directly.

#### Outer page (account.apple.com)

**S1. Page shell and stacking** (O)
- body bg `rgb(255,255,255)`; base text 17px / 25px line-height, letter-spacing -0.374px (-0.022em), colour `rgb(29,29,31)` (#1d1d1f), font "SF Pro Text", "Myriad Set Pro", "SF Pro Icons", "Apple Legacy"...
- Global nav 44px tall (y 0-44), then sticky local nav `.ac-localnav` 52px (y 44-96), content padding `.landing` 60px top. Document height 950px at 1024 wide.
- Use on Emma: white page, 17/25 body at -0.022em, #1d1d1f text; a single 52px top bar, content starts ~60px below it.

**S2. Local nav bar** (O)
- `.ac-localnav-content` padding 0 22px, height 52px; title "Apple Account" left (~21px semibold, I from screenshot), right side links "Sign In", "Create Your Apple Account", "FAQ" at ~12px.
- `.ac-localnav-background` transition `background 0.5s cubic-bezier(0.28, 0.11, 0.32, 1)` (Apple's standard "ease" curve for chrome).
- Use on Emma: 52px bar with "Pearl Dental" name left; animate its background with 0.5s cubic-bezier(0.28,0.11,0.32,1) if it ever changes.

**S3. Brand mark area** (O)
- `.landing__animation` 180x180px, centred (x 414 of 1009), margin-bottom -78px (the mark overlaps the column below), transition `transform 2s ease-in-out`. Behind it `.landing__rings` an SVG 828x971 of faint concentric rings (decorative, masked image).
- Visible logo glyph about 28px tall in the centre of the 180px box (I, screenshot).
- Column below: `.landing__top` padding 0 90px; `.landing__interaction` 829px wide; `.sign-in__auth` max-width 980px, margin 0 22px 50px; iframe 980 x 438px.
- Use on Emma: 180px square brand area with Emma's own small mark (~28-40px) centred, -78px bottom margin so the title tucks up under it; no rings needed (or a very faint static one).

**S4. Footer** (O)
- `footer` bg `rgb(245,245,247)` (#f5f5f7), text 12px / 16px, letter-spacing -0.12px (-0.01em), colour `rgb(110,110,115)` (#6e6e73); height 83px at desktop.
- Use on Emma: thin #f5f5f7 footer, 12/16 #6e6e73 text ("Pearl Dental staff only" + copyright).

#### The form (idmsa.apple.com iframe)

Method note: opening the iframe src directly loads Apple's stylesheets but the widget never renders standalone (it waits for a postMessage handshake from account.apple.com; body text stays "."). So the form was measured two ways:
(a) screenshots of the rendered iframe on account.apple.com (screenshot frame 799px for a 1024px viewport, so pixel sizes x1.28), and
(b) probe markup built with Apple's own class names (`form-textbox`, `form-textbox-input`, `form-textbox-label`, `button`, `tk-*`; class names confirmed in their identityWidget JS) inside the idmsa page, read with getComputedStyle. Values from (b) are Apple's live CSS, so marked O(css); which class the live widget uses is I unless stated.
There is no "Keep me signed in" checkbox and no "Forgot password?" link on the first step: the page is email-first (field, privacy note, Continue + Sign in with iPhone). Those appear only after Continue, which was not pressed.

**S5. Column width** (O screenshot, I exact)
- Field and button row span ~466px in the 1024 viewport; buttons are two equal ~228px blocks with a ~10px gap. Title/subtitle centred; privacy note left-aligned.
- Use on Emma: one centred 460px column (max-width 460px; full width minus 16px gutters on phones).

**S6. Title** (O screenshot + O(css) `.tk-headline`)
- "Apple Account": 40px, weight 600, line-height 44px (1.1), letter-spacing normal (0), #1d1d1f, "SF Pro Display". (`.tk-headline-elevated` = 48/52, 600, -0.144px; not used here.)
- Gap brand mark to title ~50px; title to subtitle ~12px (screenshot).
- Use on Emma: "Staff sign in" at 40/44 semibold, tracking 0, #1d1d1f, centred.

**S7. Subtitle** (I from screenshot, O(css) for the scale)
- "Manage your Apple Account": about 17px regular (renders ~220px wide), #1d1d1f, centred, i.e. body style 17/25, -0.022em. (`.tk-intro` would be 21/29 +0.011em, too wide for what is shown.)
- Use on Emma: one 17/25 line ("Pearl Dental dashboard"), #1d1d1f or #6e6e73 if it should recede.

**S8. Text field** (O(css), matches screenshot)
- `.form-textbox-input`: height 56px (3.294rem), width 100%, radius 12px, border 1px solid #86868b, background rgba(255,255,255,0.8), padding 18px 16px 0 (text sits low, leaving room for the floated label), 17px / 21px, weight 400, -0.374px (-0.022em), #1d1d1f, margin-bottom 14px, appearance none. No transition on the field itself.
- Focus (`:focus` / `.focused`): border-width 2px, colour #0071e3, padding-left 15px (compensates the thicker border so text does not shift); box-shadow none. The "ring" is the 2px border itself, not an outer shadow.
- Error (`.form-textbox.is-error`): border 1px #e30000, background #fff2f4, label #e30000.
- Use on Emma: 56px fields, 12px radius, 1px #86868b border; focus = 2px #0071e3 border with padding-left reduced 1px; error = #e30000 border on #fff2f4.

**S9. Floating label** (O(css); focused state O screenshot)
- Rest: absolute, top 18px, left 17px (1rem + 1px border), 17px / 21px, -0.374px, #6e6e73, pointer-events none, max-width calc(100% - 32px), nowrap + overflow hidden.
- Floated (input `:focus`, `.focused` or `.form-textbox-entered`, i.e. has a value): top 10px, 12px / 16px, -0.12px, colour stays #6e6e73 (#e30000 in error).
- Transition: `0.125s ease-in` on all properties (top, font-size, letter-spacing animate; no transform).
- The live page autofocuses the field on load, so the label is already floated and the blue border shows when the form appears.
- Use on Emma: floating labels "User name (optional)" / "Password": rest top 18px at 17px, floated top 10px at 12px, 125ms ease-in; autofocus the first field.

**S10. Primary button (Continue)** (O screenshot + O(css))
- Screenshot: ~228 x 38px, radius ~8px (block button, not a pill), pale blue while the field is empty.
- `.button` base: 17px / 20px, weight 400, -0.374px, padding 8px 15px, 1px transparent border (= 38px tall), min-width 30px, radius 980px (pill). `.button-block`: width 100%, radius 8px, height 38px, which matches the live Continue.
- Colours: rest #0071e3 with white text; hover and keyboard focus #0077ed; active #006edb; disabled = #0071e3 at opacity 0.42 (`--sk-link-disabled-opacity`), cursor default, pointer-events none.
- Focus ring (keyboard only; suppressed when `data-focus-method=mouse|touch`): `box-shadow: 0 0 0 3px #fff, 0 0 0 5px #0071e3` (3px white gap, then a 2px blue ring).
- No transition on the button (state changes are instant).
- Use on Emma: 38px, 8px-radius #0071e3 "Sign in" button, full column width; opacity 0.42 until the password has text; hover #0077ed, active #006edb; keyboard ring 3px white + 2px #0071e3.

**S11. Dark secondary button (Sign in with iPhone)** (O screenshot + O(css) `.button-neutral`)
- Same size/shape as Continue (~228 x 38, 8px radius), #1d1d1f, white text with a small glyph before the label; hover/focus #272729, active #18181a, disabled #1d1d1f at reduced opacity. Under it a centred 12px caption "Requires a device with iOS 17 or later."
- Other variants in CSS: `.button-secondary` #757579 (hover #79797d, active #747478, disabled opacity 0.56); outline variant 1px #06c border + #06c text.
- Use on Emma: not needed (one primary action). If a second action ever appears, #1d1d1f with the same metrics.

**S12. Arrow button inside the field** (O, from JS)
- The widget JS still ships the older round arrow control: SVG `#sign-in-button`, 50x50 viewBox (circle + arrow), as a bare `.continue.button` inside the field (`form-textbox-with-button`). On this account.apple.com flow it is NOT shown; the separate Continue button is used instead (O screenshot).
- Use on Emma: skip the in-field arrow; use a separate Sign in button.

**S13. Privacy note with icon** (O screenshot, I sizes)
- Left-aligned block ~30px under the field: a ~36px two-person "data and privacy" pictogram (blue + light blue) on its own line, then a paragraph ~12px / 16px #1d1d1f (matches `.tk-caption` 12/16, -0.12px) ending in an inline link "See how your data is managed..." in #06c. Gap note to buttons ~30px.
- Use on Emma: one 12/16 #6e6e73 line under the button ("Staff only. Sign-ins are recorded.") with a 16-20px lock glyph; no big pictogram.

**S14. Links** (O(css))
- #0066cc, no underline at rest, underline on hover (`a:hover, .link:hover {text-decoration: underline}`), no colour change. 17px in body, 12px in captions/footer.
- Use on Emma: any link (e.g. "Forgot password? Ask the clinic owner.") #06c, underline on hover only.

**S15. Error line** (O(css) `.form-message-wrapper`)
- 12px / 16px, -0.12px (-0.01em), #e30000, margin 8px 0 12px under the field, preceded by an SF Pro Icons exclamation glyph (::before/::after). Field goes red at the same time (S8). display none until there is an error.
- Use on Emma: "That password is not right." as a 12/16 #e30000 line with a small (!) icon, 8px under the password field, plus the red field state; aria-live polite.

**S16. "Keep me signed in", "Forgot password?", step transition** (not observed)
- Not on the email step, and the password step cannot be reached without submitting, so checkbox styling and the email-to-password reveal (height/opacity) were not measured. Apple's CSS has `.form-choice-checkbox` with a custom `.form-choice-indicator` (checked/mixed fill #06c, white glyph) and labels padded 26px left (O(css); my probe markup only partly matched).
- Use on Emma: show user name and password together (no two-step reveal, so no transition). If a "remember this device" box is added: 17px label, 26px left padding, custom indicator filled #06c when checked.

**S17. "Locked" notice** (I; no direct Apple equivalent on this page)
- Nearest pattern: the same 12/16 #e30000 message line, or the caption style for a neutral notice.
- Use on Emma: "Too many tries. Locked for 15 minutes." in 12/16 with a lock glyph, button at opacity 0.42, fields disabled.

#### Motion and mobile

**S18. Page-load sequence** (O)
- The page shows a centred spinner first, then the brand mark alone, then the form. Caught mid-load at 375px: the title, subtitle and field were at very low opacity, i.e. the form fades in (the fade lives inside the cross-origin iframe, so its duration/easing could not be read; I: roughly 0.5-1s).
- `.landing__animation` (brand area) has `transition: transform 2s ease-in-out` and settles at `matrix(0.6,0,0,0.6,0,-58.8)`: the mark starts large and centred, then scales to 0.6 and rises ~59px as the form arrives. The coloured dot ring (`.landing__rings`, a masked sprite image translated by -644px, i.e. a sprite sheet frame) appears around it after load. `.landing` carries a `landing--transition` class during this.
- Use on Emma: optional and subtle. Emma's mark can arrive at scale 1 and ease to 0.6 / -60px as the form fades in, but use a 0.4-0.6s critically damped ease (Apple's 2s is slow for a staff tool), and under prefers-reduced-motion show the end state with a 200ms fade only.

**S19. Email -> password step** (not observed)
- Could not be triggered without pressing Continue (which submits the account name), so the password field's reveal (height/opacity values) is unmeasured.
- Use on Emma: not needed; both fields are shown at once.

**S20. 375px layout** (O rects; I for inner sizes from screenshot)
- Global nav 48px, local nav 48px (y 48-96), local nav collapses its links into a chevron menu.
- `.landing__top` padding 0 30px (was 0 90px at 1024). Brand area 168x168 (was 180) with margin-bottom -80px. Iframe spans the full 375px width, 469px tall; inside it the field and button row have ~24px side gutters (field ~327px wide, still 56px tall, 12px radius), the two buttons stay side by side at ~146px each with a ~10px gap. Title still large (~32px, I), subtitle 17px; privacy paragraph wraps to ~8 lines.
- Footer grows to 184px; document 1060px tall.
- Use on Emma: at phone width keep the same 56px field and 38px button, column = 100% with 24px gutters, title can step down to ~32px; nothing else changes.

#### Summary for Emma's staff sign-in

White page, 52px top bar, 180px brand area (Emma's own mark) overlapping the column by -78px, 40/44 semibold title, 17/25 subtitle, 460px column. Fields: 56px, 12px radius, 1px #86868b border on rgba(255,255,255,.8), focus = 2px #0071e3 border (no outer shadow), floating label 17px at top 18px -> 12px at top 10px in 125ms ease-in, #6e6e73. Sign in button: 38px, 8px radius, #0071e3, hover #0077ed, active #006edb, disabled opacity .42, keyboard ring `0 0 0 3px #fff, 0 0 0 5px #0071e3`. Error: 12/16 #e30000 line 8px under the field + red field on #fff2f4. Links #06c, underline on hover. Footer #f5f5f7 with 12/16 #6e6e73.

### 11.4 Build checklist (ranked)

These are the patterns most worth building, as specs. Every one stays CSP-clean: no inline style or script, everything same-origin. Every one has a `prefers-reduced-motion` fallback, which is either the end state shown at once or a 200ms opacity fade.

| # | Pattern | Spec | Source | Where on Emma |
|---|---|---|---|---|
| B1 | Floating glass capsule nav | sticky, 10px inset from the edges, radius 32px, `rgba(255,255,255,.8)` + `blur(20px)`, 1px `#e6e6e8` ring (`box-shadow:0 0 0 1px`), no drop shadow; hides with `translateY(-100%)` over 230ms when scrolling down and returns on scroll up | P1, P2, P21 | Talk page header, sign-in header |
| B2 | Materialising pill bar | the Call and Type-instead group enters as a 56px dot at `scale(.01)`, grows, then widens to its natural width; children fade in 50ms apart after a 100ms delay; one shared glass layer `rgba(232,232,237,.72)`, blur easing from 14px to 7px; radius 28px; sticky 32px from the bottom on phones | P5, A13, A25 | Talk page CTA |
| B3 | Sliding bubble selector | one dark `#1d1d1f` bubble slides under the active option (`transform 200ms ease-out`, or a spring), stretching to `scaleX(1.08) scaleY(.92)` mid-move and settling to 1; label colour `200ms cubic-bezier(.53,-.01,.17,1)` | A22 | Talk page Call / Type switch, dashboard segmented controls |
| B4 | Segmented tab nav | platter 44px, 4px padding, `#e8e8ed`, radius 999px; the indicator pill animates `left,width` over 320ms ease-out; the label flips to white after a 160ms delay; no transitions on first layout | A21, P16 | Calendar Day/Week/Month/Year/List, Tasks Open/Done/All, Calls filters |
| B5 | Overshoot pop-in for small things | `opacity 0 to 1, scale(.5) to 1` over 400ms `cubic-bezier(.3,2,.5,1)`, `index x 50ms` stagger; exit 200ms ease-out, all together | A14, A23, P31 | Free-time slot pills, filter chips, toasts |
| B6 | Modal / sheet | scrim `rgba(232,232,237,.48)` + `blur(20px)` (light); white panel, radius 28px, no shadow, 76px padding on desktop (24px on phones); a sticky 36px dark round close button in a 44px target at 20/20; the panel scrolls itself; it enters rising 16px and fading in over 320ms `(0,0,.2,1)`; on phones it becomes a bottom sheet with a grab handle that can be dragged down to close | A11, P13, P18 | Appointment inspector, book/move sheet, confirms |
| B7 | Round icon buttons | 36px circle; alpha steps rest `rgba(210,210,215,.64)` / hover `rgba(223,223,227,.6976)` / active `rgba(193,193,198,.6544)`; icon `rgba(0,0,0,.56)` to .64; `100ms linear`; press `scale(.95)`; disabled opacity .42 | A2, A4, A5, P10, P11 | Calendar prev/next, close buttons, transcript play |
| B8 | Colour-only hover | buttons and pills change background only: `#0071e3` to `#0077ed`, active `#006edb`, 100-120ms linear; no lift. Scale is kept for round controls on press and for tiles on hover | P12, A8 | Every pill |
| B9 | Tile hover | `scale((w+6)/w)` over 300ms `cubic-bezier(0,0,.5,1)`, also on `:focus-visible`, mouse only | P27, section 10 R4 | Today tiles |
| B10 | Stretching progress pill | 8px dots; the current one is a 48x8 pill with a fill that runs linearly; neighbouring widths interpolate | P6, A15 | Talk page listening/speaking meter; Recovery progress |
| B11 | Masked progressive-blur scrim | `rgb(255 255 255/56%)` + `blur(20px)`, `mask-image:linear-gradient(180deg,transparent,#000)` | A24 | Talk page caption backing, dashboard scroll edge under the toolbar |
| B12 | Glass edge without borders | `inset 0 0 .5px rgba(0,0,0,.11)`; the only drop shadow token is `8px 8px 16px rgba(0,0,0,.08)` | A18 | Orb, pills, glass panels |
| B13 | Caption fade | each new caption line fades in over 500ms `cubic-bezier(.33,1,.68,1)` | P34 | Talk page caption |
| B14 | 8-spoke spinner | 8 bars with opacity steps 1, .9, .7, .6, .4, .3, .2, .13, rotating in steps over 1s | A28 | Talk page connecting state, sign-in submit, dashboard loading |
| B15 | Sticky header goes glass only once stuck | transparent at the top, then `rgba(250,250,252,.8)` + `saturate(180%) blur(20px)` + a 1px `rgba(0,0,0,.16)` keyline, background 320ms `(.4,0,.6,1)` with a 240ms delay | P22, A7 | Dashboard section toolbars |
| B16 | Strip slide-in | items go from `translateX(160px)`, opacity 0, to 0 over 350ms ease, staggered, on first paint only | A6 | Dashboard sidebar rows on load |
| B17 | Accordion | height over 400ms ease-in-out, 1px `#d2d2d7` dividers, a rotating chevron | P33 | System settings disclosures, Recovery settings |
| B18 | Sign-in field | 56px, radius 12px, 1px `#86868b` on `rgba(255,255,255,.8)`, padding 18px 16px 0; focus 2px `#0071e3` border (padding-left 15px), no shadow; floating label 17px/top 18 to 12px/top 10, 125ms ease-in, `#6e6e73`; error border `#e30000` on `#fff2f4` | S8, S9, S15 | Sign-in page |
| B19 | Sign-in button | block button, 38px, radius 8px, `#0071e3`, hover `#0077ed`, active `#006edb`, disabled opacity .42; keyboard ring `0 0 0 3px #fff,0 0 0 5px #0071e3` | S10 | Sign-in page (and keyboard focus everywhere) |
| B20 | Sign-in brand arrival | a 180px brand area with a -78px bottom margin; the mark arrives at scale 1 and eases to .6 and up 58px as the form fades in (shortened from Apple's 2s to about 600ms) | S3, S18 | Sign-in page |
| B21 | Ribbon drop and calm | a banner drops `translateY(-100%)` to 0 over 800ms `(.42,0,.58,1)`, then cross-fades from the accent colour to neutral after 1.8s | P23 | Dashboard notice (alerts, the demo role) |
| B22 | Rotating plus | 45 degrees over 500ms `cubic-bezier(.66,0,.2,1)` | A1 | New appointment and any plus that opens a sheet |
| B23 | Type scale additions | sheet body 19/400/+.012em/27; section headers 40 on desktop, 32 on phones; tile titles 21/600/+.011em; dashboard page title 34/600/-.011em | P19, A9, A20 | All |

**Easing set (only these):**
- `--ease: cubic-bezier(.4,0,.6,1)`, the default
- `--ease-out: cubic-bezier(0,0,.2,1)`, for arrivals
- `--ease-layout: cubic-bezier(.25,.1,.3,1)`
- `--ease-colour: cubic-bezier(.28,.11,.32,1)`
- `--ease-hover: cubic-bezier(0,0,.5,1)`
- `--ease-caption: cubic-bezier(.33,1,.68,1)`
- `--ease-pop: cubic-bezier(.3,2,.5,1)`
- `--ease-label: cubic-bezier(.53,-.01,.17,1)`
- `linear` for hover colour steps

## 12. As built: the deep pass (22 Oct)

**Process:** four roles, run as agents in rounds: researcher, builder, Apple web-head reviewer and consumer tester.
- **Research:** three researchers, one each for AirPods, iPhone and sign-in, wrote section 11.
- **Building:** two builders. One did the shared components, the talk page and the sign-in page; the other did the dashboard.
- **Rounds:**

| Page | Rounds | Reviewer | Tester |
|---|---|---|---|
| Talk | 3 | approved | 6 → 8 → 9/10 |
| Sign-in | 3 | approved | 7.5 → 8.5 → 9/10 |
| Dashboard | 3 | approved | 6.5 → 8.5 → 9/10 |

**What was copied from where (pattern and values, never assets):**
- **iPhone 18 Pro and Air:**
  - the floating glass capsule nav (B1; P1, P21);
  - the materialising pill bar with its 14px to 7px blur (B2; P5);
  - the sticky toolbar that turns glass once stuck, with a keyline (B15; P22);
  - the round alpha-step controls (B7; P10, P11);
  - the modal recipe: blurred light scrim, 28px panel, sticky 36px close (B6; P13, P18);
  - the overshoot pop-in at `cubic-bezier(.3,2,.5,1)` with a 50ms stagger (B5; P14, P31);
  - the caption fade (B13; P34);
  - the ribbon drop and calm (B21; P23);
  - the accordion (B17; P33).
- **AirPods Max:**
  - the segmented tab nav with a sliding pill and a 160ms label flip (B4; A21);
  - the bubble selector's stretch and squash (B3; A22);
  - the progressive-blur scrim (B11; A24);
  - one shared glass layer behind the pills (A25).
- **AirPods and AirPods Pro:**
  - the stretching progress pill, used as the talk-page level meter (B10; A15);
  - the inner 0.5px glass edge (B12; A18);
  - the 8-spoke spinner (B14; A28);
  - the strip slide-in (B16; A6);
  - the rotating plus (B22; A1).
- **Apple Account sign-in:**
  - the 56px floating-label fields with a 2px focus border (B18; S8, S9);
  - the 38px block button, its colours and the keyboard ring (B19; S10);
  - the error line and red field state (S15);
  - the brand-area arrival (B20; S3, S18);
  - the 52px bar and #f5f5f7 footer (S1, S4).

**What the reviewers rejected along the way, and how it was fixed:**
- **Talk page:**
  - It didn't fit the viewport. It is now a 100dvh grid, with the orb sized from the viewport height.
  - The pill bar snapped widths and showed clipped or squashed labels. The glass now glides; labels cross-fade in place, and a leaving pill fades out first.
  - The materialise steps overlapped. They now run in order: dot, then widen, then labels.
  - There was no hover or press feedback on the selected pill.
  - The caller's typed line vanished after 7ms. A two-line transcript now keeps it.
  - Captions were truncated on phones, and the page was 394px wide at 375.
  - The thinking ring flashed, the status headline flickered, and there was no thinking state. Statuses are now held for at least 700ms and cross-faded.
  - The error state was alarm-red. It is now calm amber.
  - "Call Emma" briefly showed on red. The red now arrives with the "End call" words.
  - The "Call ended" dimming never showed. Now it does, and the lines fade on out.
- **Sign-in:**
  - The placeholder-looking grey ball was replaced by a still 40px mark. A 28-dot ring that read as a spinner was tried and then removed.
  - The error line pushed the layout down. It now takes the help line's slot.
  - The empty user name field turned red. Now only the field at fault does.
  - The entrance took 4s to become readable.
- **Dashboard:**
  - Raw internal keys reached the screen ("Emma is ask name", "ask_intent", "tier0"). Plain labels replaced them.
  - Staff lines looked like Emma's. They are now green "You" bubbles, and the status reads "You're on the call".
  - The full-width red End pill is now tinted red text.
  - A two-line confirm used a full sheet. It is now a 360px alert, and the page behind it is inert.
  - The Calendar lost its date navigation on scroll and on phones. The toolbar is now sticky, and the hour grid scrolls in its own box.
  - The book sheet grew to 66 ragged pills. Slots are now grouped by time of day, with a sticky footer on desktop.
  - The forms mixed native and floating-label fields. There is now one field style.
  - The toolbar overlapped at 735-1068px. Below 1068px it has two rows.
  - Recovery was 390px wide on phones.
  - A grid class clash broke the "You're on the call" pill.
  - The System reply counts didn't add up. This was a labelling fix only.
  - Shadows on the tab bar and on event hover were removed.

**Left out on purpose:**
- Drag-to-move for appointments, so every booking change still goes through Python's checks.
- Apple's 2s brand animation, shortened to about 600ms.
- The B11 progressive blur under the dashboard toolbars, so the bars stay restrained.

**Follow-ups outside this redesign:**
- A talk-page call ended by staff with no phone number still creates an uncallable callback task.
- One greeting variant ("Hi, Emma at Pearl Dental. Would you like to book an appointment?") reads oddly.
- Emma doesn't say goodbye when a typed call is ended.
- The demo-role ribbon was checked by forcing it into view, not by signing in as a demo user, because the permission check stops agents from typing test passwords.

**Verified:**
- In the built-in browser at 1280x800, 1024, 900, 800x600 and 375x812, light theme, with the pane visible so the motion ran at real speed.
- No CSP violations; the only 404s are the ambience mp3s, which worktrees don't have.
- Tests: 1,056 OK, 1 skipped.

## 13. Liquid Glass (22 Oct)

The owner asked for Liquid Glass wherever it feels good. That reverses the 14 Oct "no iOS 26 refraction" decision. The research is below; what was built is in 13.x as-built.

Reverses the 14 Oct "no refraction" decision. Sources: the apple-look skill's HIG pages (materials.md, color.md, scroll-views.md, toolbars.md, tab-bars.md, sidebars.md, layout.md, app-icons.md, pointing-devices.md, live-activities.md and the curated liquid-glass.md), the apple-motion skill, developer.apple.com, apple.com (live measurements) and Emma's own CSP in `server.py`.
O = observed (quoted or measured), I = inferred or my judgment. Earlier items P1-P35 are in research-iphone.md and APPLE_DESIGN_PLAN.md section 11.

#### A. What Apple says (HIG)

| # | Topic | Quoted / measured | Type | Use on Emma |
|---|---|---|---|---|
| LG1 | Where it belongs | materials.md: Liquid Glass "forms a distinct functional layer for controls and navigation elements"; "Don't use Liquid Glass in the content layer." Exception: sliders and toggles take on glass only while being manipulated | O | Glass only on floating chrome: capsule nav, pill bars, tab bar, sheets, toasts, round buttons. Never on Today tiles, calendar cells, transcript bubbles, tables |
| LG2 | Restraint | materials.md: "Use Liquid Glass effects sparingly ... Limit these effects to the most important functional elements" | O | Budget: at most 2 refracting surfaces visible at once per page (judgment) |
| LG3 | Two variants | Regular "blurs and adjusts the luminosity of background content"; for "a significant amount of text, such as alerts, sidebars, or popovers". Clear "is highly translucent" and only "for components that appear over visually rich backgrounds"; over bright content "consider adding a dark dimming layer of 35% opacity" | O | Regular for sidebar, sheets, toasts, typed box. Clear only for the talk-page orb controls and pill bar over the clinic photo/gradient. Our light-only theme rarely needs the 35% dim; use it only if clear glass sits on a bright photo |
| LG4 | Colour and adaptive tint | color.md: glass "has no inherent color, and instead takes on colors from the content directly behind it". Small elements (toolbars, tab bars) "adapt ... between a light and dark appearance in response to the underlying content"; labels go "darker when the underlying content is light, and lighter when it's dark". Larger elements like sidebars are "more opaque". Primary actions: "apply color to the background rather than to symbols or text"; "Refrain from adding color to the background of multiple controls" | O | Tinted ("stained") glass only for the one primary action (Call). Sidebar uses a higher-opacity fill than the small pills |
| LG5 | Scroll edge effect | scroll-views.md: separates floating bars from scrolling content; "Prefer the automatic ... style" (more opaque under toolbars with many controls, text outside glass, pinned table headers); "Only use a scroll edge effect when a scroll view is behind floating interface elements"; "Apply one scroll edge effect per view" | O | Keep our progressive-blur band (B11) only under the dashboard toolbar and the transcript header; one per pane |
| LG6 | Concentric corners | toolbars.md: standard items "have corner radii that are concentric with bar corners. If you need to create a custom component, ensure that its corner radius is also concentric"; live-activities.md: "even, matching margins between rounded shapes and the edges" | O | inner radius = outer radius - inset. Capsule 32px with 6px inset holds 26px buttons; sheet 28px with 12px padding holds 16px controls |
| LG7 | Specular, refraction, translucency are the named attributes | app-icons.md: icons "take on Liquid Glass attributes like specular highlights, refraction, and translucency"; pointing-devices.md: iPad lift = scale up + shadow below + "a soft specular highlight on top" | O | The three things that separate Liquid Glass from frosted blur are (1) refraction at the rim, (2) a specular rim highlight, (3) translucency that adapts. Plain blur has only the third |
| LG8 | Accessibility states | liquid-glass.md checklist: "Reduce Transparency gets an opaque fallback, Increase Contrast gets stronger fills and borders, Reduce Motion drops morphing and refraction animation"; materials.md: variants change with the system's preferred look, Reduce Transparency and Increase Contrast | O | `prefers-reduced-transparency` = solid #f5f5f7/white with 1px border, no blur, no SVG filter. `prefers-contrast: more` = opaque fill + 1px `#86868b` border. `prefers-reduced-motion` = no morph, no pointer-follow highlight |
| LG9 | Targets | liquid-glass.md: glass never shrinks hit regions below 44x44 (mobile) / 28x28 (desktop) | O | Rim refraction must not eat into the hit area; keep 44px pills |

#### B. What Apple says (developer.apple.com: "Applying Liquid Glass to custom views" and WWDC25 "Meet Liquid Glass", session 219)

These are paraphrased; at most one short quote per item. Timestamps are the session's.

| # | Topic | Source finding | Type | Use on Emma |
|---|---|---|---|---|
| LG10 | Lensing is the defining property | Liquid Glass defines itself through **lensing** (1:55). Older materials scattered light, but this one bends and concentrates it in real time (2:29). That gives the edge definition, so the body can stay very transparent | O | This is the difference from frosted blur. Refract the backdrop at the rim, keep the centre nearly clear, and use only a light blur |
| LG11 | Materialize, don't fade | Objects appear and disappear by ramping the light-bending and lensing up or down, "instead of fading" (2:55). SwiftUI names this transition `materialize`; for shapes within the container spacing it uses `matchedGeometry` | O | On enter, animate displacement scale 0 to full together with blur and opacity, about 300ms. On exit, reverse it |
| LG12 | Press: flex and energise | It responds to touch by instantly flexing and lighting up (3:38), with a gel-like give (3:51). The material lights up from within, starting under the finger, and the glow spreads to nearby glass (12:05). The SwiftUI API is `.interactive()`. Elements can also take on glass only while touched (4:10) | O | On press, scale to 1.04-1.06 on a spring (damping about .7, response .3). Show a radial glow at the pointer that fades out over 400ms, and nudge the neighbouring pills' glow |
| LG13 | Layers | The material is built from layers: highlights, shadow, illumination (10:44). Highlights come from light sources in the environment and follow the geometry. During interactions the lights move around the rim, and sometimes they follow device motion | O | Web layers: (1) backdrop refraction + blur, (2) translucent fill, (3) a specular rim highlight that follows the geometry and can track the pointer, (4) an adaptive shadow, (5) a press glow |
| LG14 | Adaptive shadow | The shadow gets stronger over text and weaker over a plain light background (11:47) | O | Use a stronger drop shadow when the glass sits over the transcript or a table, and a lighter one over the plain #f5f5f7 page. Two classes, chosen by what lies underneath |
| LG15 | Adaptive tint and range | Tint and dynamic range shift so labels stay legible. Small elements can flip between light and dark (6:42). Large elements (menus, sidebars) don't flip because it would distract; they become more opaque instead (15:29) | O | The talk-page pills and capsule may flip ink colour based on what is under them. The sidebar never flips; give it a denser fill |
| LG16 | Thickness scales with size | When glass grows into a bigger surface, such as a menu opening from a toolbar button, it reads as a thicker material: deeper shadow, stronger lensing and refraction, softer light scatter (7:02) | O | Sheets and menus: stronger displacement, more blur and a deeper shadow than the small pills. Small chips: thin |
| LG17 | Coloured glass tint | Picking a tint colour produces a range of tones mapped to the brightness of the content underneath, like real stained glass (16:31). The colour stays close to the intended hue | O | Call button: a tint built with `color-mix()` of the accent into the glass fill at 70-85%, not a flat solid. Keep the blue recognisable |
| LG18 | No glass on glass, never mix variants | Avoid using the material on both layers when stacking. The top layer should use fills, transparency and vibrancy as a thin overlay (13:34). Regular and Clear "should never be mixed" (13:48) | O | Buttons inside a glass sheet or capsule are not glass. Use flat fills such as `rgba(0,0,0,.05)` / hover .08. One variant per page area |
| LG19 | When clear is allowed | Clear needs three conditions: media-rich content underneath, a dimming layer that doesn't hurt the content, and bold, bright content on top (14:54) | O | Only the talk page, and only if its background is a real image. Emma's light gradient backgrounds fail the first test, so default to Regular everywhere |
| LG20 | Merging and morphing | `GlassEffectContainer(spacing:)`: the larger the spacing, the sooner nearby glass shapes blend and merge. `glassEffectUnion` makes separate views render as one shape. `glassEffectID` morphs one shape into another. Apple also warns that too many glass effects or containers on screen at once hurt performance | O | Call / Type-instead pills share one glass layer (A25 already does this). Morph the pill bar into the typed box using one element that changes width and radius. Approximate merging with an SVG goo filter only if needed (see LG33) |
| LG21 | Accessibility states | Increase Contrast makes elements mostly black or white with a contrasting border. Reduce Motion lowers the intensity of some effects and turns off the elastic behaviour | O | Matches LG8. Reduced motion: no elastic press, no moving highlight, and materialize becomes a plain 150ms fade |

#### C. What apple.com actually ships (measured 22 Oct on /iphone-air/, JS running, Chromium 152)

| # | Element | Measured | Type | Use on Emma |
|---|---|---|---|---|
| LG22 | No SVG refraction anywhere | 0 `<filter>`/`feDisplacementMap`/`feTurbulence`/`feSpecularLighting`/`feImage` elements in the DOM; 0 CSS rules with `filter:` or `backdrop-filter:` containing `url()`; 0 selectors mentioning glass/liquid/specular/refract/lens; no `mix-blend-mode: plus-lighter/overlay/screen` and no backdrop `brightness()/contrast()` | O | apple.com itself is plain frosted blur. Any refraction we add goes beyond Apple's website and imitates iOS 26. Keep it subtle |
| LG23 | Floating localnav capsule, live values (corrects P1/P2) | stuck and visible: 52px tall, 663px wide when collapsed, **radius 18px collapsed** (`--progress: 0`), 32px when expanded; `rgba(255,255,255,.8)` + `blur(20px)`, ring `0 0 0 1px #e6e6e8`, 10px from the top. Sequence: from scrollY 0 to 900 the container got `sticky reveal` and the wrap went `translateY(-72px)` to 0. Scrolling back up to 700 removed `reveal` and it hid again (`-72px`). So it **shows while scrolling down past the hero and hides on scroll up** here, the reverse of what P2 inferred | O | Capsule nav: 52px, radius 18px at rest (not a full pill), 32px when it opens into a panel. Show/hide behaviour is up to us; Apple's choice is unusual, so don't copy it blindly |
| LG24 | The one "specular rim" Apple ships | video captions menu: `background: rgba(29,29,31,.92)`, radius 28px, `box-shadow: inset 1.8px 1.8px .5px -1.4px rgb(110,110,115), inset -1.5px -1.5px .5px -1.1px rgb(110,110,115)`. Two offset inset shadows with negative spread draw a thin rim lit on the top-left and bottom-right | O | Our rim highlight: the same geometry in white over light glass (recipe R2) |
| LG25 | Hairline inner edge | product-viewer pills, `.control-item-bg` and the phone close button all use `inset 0 0 .5px rgba(0,0,0,.11)`; beat fill `rgb(232 232 237/.72)`, hover .92 | O | Already built (B12/A18). Keep it as the dark half of the rim |
| LG26 | Moving gradient + blur on the product-viewer pill (a "materialize" analogue) | `.control-item-bg`: `backdrop-filter: blur(calc(var(--blur-amount)*(1 - var(--abs))*(2 - var(--alpha))))` with `--blur-amount: 20px`; `background-image` is a horizontal band `linear-gradient(90deg, transparent ..., var(--bg-color) 33%-36px ... 66%+36px, transparent ...)`, `background-size: 300% 100%`, slid by `background-position: calc(50% + var(--gradient-progress)*50%)`; `transform: scale(var(--scale))`, origin centre | O | Enter animation for glass: slide a fill band across while the blur ramps up and the scale settles. This is the closest thing on the site to Apple's "materialize" |

#### D. Building real refraction on the web (tested in this pane)

| # | Topic | Finding | Type | Use on Emma |
|---|---|---|---|---|
| LG27 | `backdrop-filter: url(#svg-filter)` works in Chromium | In Chromium 152, `CSS.supports('backdrop-filter','url(#x)')` is true. A test pill with `backdrop-filter: url(#lgtest)` (feImage data: map, blur 1.5, feDisplacementMap scale 48, R/G channels) **visibly bent the text beneath its edge**. The filter region used `filterUnits="userSpaceOnUse"` with x/y 0 and the element's own width/height, so coordinates are local to the element's border box, and the result was clipped to the border radius | O | Real refraction is possible for Chromium visitors (Chrome, Edge, the desktop app) |
| LG28 | Cost | 40 frames of programmatic scrolling with one 300x80 pill: refraction median 16.5ms / p90 18.1ms; `blur(20px)` 16.6 / 17.5; no pill 16.7 / 17.6. No measurable cost for one small surface at 60Hz on this PC | O (one machine) | Cheap for 1-3 small surfaces. Don't refract large ones (sidebar, sheet body) or many at once. Re-measure on a low-end phone |
| LG29 | Safari and Firefox | Safari (WebKit, incl. iOS 26 Safari) and Firefox support `backdrop-filter` with CSS functions but **do not render SVG `url()` references in `backdrop-filter`**. The declaration may still parse (so `@supports` can't be trusted) and then draws no backdrop at all | I (from known engine status, not tested here) | Never put `url()` in the only backdrop declaration. Turn refraction on with a class (`html.lg-refract`) that our same-origin head.js sets only when `navigator.userAgentData?.brands` includes "Chromium". WebKit and Gecko don't expose `userAgentData` |
| LG30 | Backdrop root trap | An element that has `backdrop-filter`, `filter`, `opacity<1`, `mask` or `will-change` on those becomes the backdrop root for its descendants, so a child's backdrop-filter only sees the parent, not the page | I (spec: Filter Effects 2) | Put each glass layer on the element itself or on its `::before`, and never nest a refracting layer inside another glass element (also LG18) |
| LG31 | Displacement map recipe | Map = neutral grey `rgb(128,128,128)` everywhere, plus a horizontal gradient band (left edge `rgb(255,128,128)` fading to transparent by 18%, right edge `rgb(0,128,128)` from 82%) and a vertical band (top `rgb(128,255,128)` to 30%, bottom `rgb(128,0,128)` from 70%). Pixels then shift inward at the rim and stay still in the centre: a lens edge. Test scale 48 smeared text into streaks; **20-28 reads as glass** for a 44-52px pill | O (map + 48 tested) / I (20-28) | Ship one map per size class as same-origin SVG files: pill 44, capsule 52, round button 44x44, sheet rim. Variable widths use `preserveAspectRatio="none"`, which stretches the bands; accept this or use two end-cap feImages |
| LG32 | CSP fit (Emma: `default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'`) | (a) An inline `<svg><filter>` in the HTML is markup; CSP doesn't block elements or SVG presentation attributes (`stdDeviation`, `scale`, `href`). Only `style=""` attributes and `<style>` elements are blocked, so hide the svg with a class (`.svg-defs{position:absolute;width:0;height:0}`) in the stylesheet, never `style=""`. (b) `feImage` fetches count as image loads, so `data:` maps need `img-src data:`. We have it, but a same-origin `/static/ui/lg/pill-44.svg` is cleaner. (c) `url(#lg-pill)` written in an external stylesheet: a fragment-only URL is defined to point into the current document (CSS Values 4), so it should resolve to the inline `<filter>`; **verify in our build**. (d) Setting `--lx/--ly` from JS with `el.style.setProperty` is CSSOM and allowed under `style-src 'self'` | I (verify a and c in the browser with the real headers) | Inline `<svg class="svg-defs" aria-hidden="true">` with the filters at the top of each page's body; maps as same-origin files |
| LG33 | Merging ("goo") | The SVG goo trick (`feGaussianBlur` 10 + `feColorMatrix` alpha `0 0 0 20 -9`) merges shapes, but only as a `filter` on the shapes' own layer; it can't be combined with a backdrop | I | Don't. Merge by animating one shared glass element's width and radius (A25 / the pill-to-typed-box morph). Use same-document View Transitions only where layout changes (Chromium and Safari 18+) |
| LG34 | Other platform hooks available here | `corner-shape: squircle` is supported in Chromium 152; `color-mix()` is supported; `prefers-reduced-transparency` is supported; `CSS.registerProperty` lets `--lx/--ly/--lg-scale` animate | O | Optional squircle corners (progressive). `@property` makes the displacement-intensity variable animatable for materialize (only the blur and highlight parts; SVG `scale` itself needs JS or SMIL) |

#### E. Ranked recipes for the builder

Order = value for effort. All tokens are light theme. Every recipe has the same reduced-transparency, contrast and motion fallbacks (R9).

**R1. Lens rim (real refraction, Chromium only). Capsule nav, call pill bar, typed box.**

```html
<svg class="svg-defs" aria-hidden="true" focusable="false">
  <filter id="lg-pill" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
    <feImage href="/static/ui/lg/pill-52.svg" x="0" y="0" width="100%" height="100%" preserveAspectRatio="none" result="map"/>
    <feGaussianBlur in="SourceGraphic" stdDeviation="6" result="soft"/>
    <feColorMatrix in="soft" type="saturate" values="1.5" result="sat"/>
    <feDisplacementMap in="sat" in2="map" scale="24" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
</svg>
```

```css
.svg-defs{position:absolute;width:0;height:0;overflow:hidden}
.glass{background:rgba(255,255,255,.62);-webkit-backdrop-filter:blur(16px) saturate(150%);backdrop-filter:blur(16px) saturate(150%)}
html.lg-refract .glass--lens{backdrop-filter:url(#lg-pill)}
```

The map is LG31 (bands 18% / 30%). Chromium gets the lens; everyone else keeps frosted blur. Note the lens fill is lighter (.62) than the old .8, because refraction now does the separating.

**R2. Specular rim (all browsers). Every glass surface.**

```css
.glass{box-shadow:
  inset 1.5px 1.5px .5px -1px rgba(255,255,255,.95),
  inset -1px -1px .5px -.5px rgba(255,255,255,.65),
  inset 0 0 .5px rgba(0,0,0,.11),
  0 0 0 .5px rgba(0,0,0,.06),
  var(--glass-drop, 0 4px 16px rgba(0,0,0,.08))}
```

This is Apple's LG24 geometry in white for light glass, plus LG25's hairline.

**R3. Pointer-following highlight (pointer:fine only).** `.glass::after{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;background:radial-gradient(140px circle at var(--lx,30%) var(--ly,0%),rgba(255,255,255,.45),transparent 65%);mix-blend-mode:plus-lighter;opacity:.6;transition:opacity .3s cubic-bezier(.4,0,.6,1)}`. JS writes `--lx/--ly` on `pointermove`, throttled to rAF, and only while the pointer is within 200px. At rest the highlight sits top-left, the light source. Off under reduced motion and on touch.

**R4. Press = swell and brighten (LG12).** On `pointerdown`: `transform: scale(1.05)` on a spring (Motion `bounce .25, duration .3`, or CSS `transition: transform .32s cubic-bezier(.3,1.6,.5,1)`). Add `filter: brightness(1.06)` on the glass layer and a press glow on `::after` (`radial-gradient(...)` at the pointer, `rgba(255,255,255,.6)`, opacity 1 instantly, then fade to 0 over 400ms after release). Nearby pills in the same bar get opacity .3 of that glow for 200ms. Reduced motion: no scale, brightness only. Caution: `filter` on the host makes it a backdrop root (LG30), so put brightness on the `::before` glass layer, not on the host.

**R5. Materialize, not fade (LG11, LG26).** On enter: `opacity 0 to 1` (200ms), `scale(.96) to 1` (spring), blur 0 to 16px and highlight 0 to .6 over 300ms `cubic-bezier(0,0,.2,1)`. In Chromium, step the lens `scale` attribute 0, 8, 16, 24 across four rAFs. Exit reverses over 200ms. Reduced motion: 150ms opacity only.

**R6. Concentric radii (LG6).** Tokens: `--r-outer`, `--inset`, `--r-inner: max(calc(var(--r-outer) - var(--inset)), 6px)`. Capsule 52px tall radius 26 (full) with a 6px inset gives 20px inner pills; sheet 28 with 12px padding gives 16px inner fields; toast 22 with 8px gives 14px. Optional `@supports (corner-shape: squircle){.glass{corner-shape:squircle}}`.

**R7. Adaptive shadow and tone (LG14, LG15).** `.glass` defaults to `--glass-drop: 0 4px 16px rgba(0,0,0,.08)`. `.glass.over-text` uses `0 8px 28px rgba(0,0,0,.16)` plus fill .74. Set the class with an IntersectionObserver against content tagged `data-under="text"`, such as the transcript list and tables. Small pills only: `.glass.over-dark` switches the ink to `#fff` and the fill to `rgba(40,40,42,.55)`. The sidebar never flips.

**R8. Tinted primary ("stained glass", LG17).** Call: `background: color-mix(in srgb, #0071e3 82%, rgba(255,255,255,.4))` with backdrop `blur(16px) saturate(180%)`, R2 rim at half strength and white label. One per view.

**R9. Accessibility fallbacks (LG8, LG21), required.**

```css
@media (prefers-reduced-transparency: reduce){.glass{background:#f5f5f7;backdrop-filter:none;-webkit-backdrop-filter:none}.glass::after{display:none}}
@media (prefers-contrast: more){.glass{background:#fff;backdrop-filter:none;box-shadow:0 0 0 1px #86868b}}
@media (prefers-reduced-motion: reduce){.glass{transition:opacity .15s linear}.glass::after{display:none}}
```

Also disable `html.lg-refract` under reduced transparency, in head.js via `matchMedia`.

**R10. Clear variant (only over the talk-page media, LG19).** `background: rgba(255,255,255,.18); backdrop-filter: blur(6px) saturate(160%)`, R2 rim. Use it only if the background behind it is a real image or video, with a local dim `rgba(0,0,0,.35)` under bright areas. Otherwise use Regular.

#### F. Where it feels good on Emma

| Page | Surface | Recipe |
|---|---|---|
| Talk | Floating capsule nav | R1 + R2 + R3, regular |
| Talk | Call / Type-instead pill bar (one shared glass layer) | R1 + R2 + R4 + R5; Call uses R8 |
| Talk | Typed box (morph from the pill bar) | R1 + R2, materialize via R5 |
| Talk | Orb | **Not glass.** The orb is content (Emma's presence). Give it R2's rim and a lens look painted in its own gradient, not backdrop glass. Glass on it would also stack under the pills |
| Sign-in | Capsule nav | R2 + blur only (no lens: one quiet page) |
| Sign-in | Continue button | R8 tinted, R4 press; the field stays a plain content field |
| Dashboard | Sidebar | Regular, denser fill .78, R2 rim, **no lens** (large surface, LG16), never flips |
| Dashboard | Phone tab bar | R1 + R2 + R4 (small, floating: the best place for it) |
| Dashboard | Sticky toolbars | Turn glass when stuck (B15) + R2; scroll edge band, one per pane |
| Dashboard | Segmented controls | Platter stays a flat fill. Only the **selected thumb** is glass while it is dragged or moving (the HIG slider/toggle exception), R4 swell |
| Dashboard | Sheets | Regular, R2, R7 deep shadow, materialize R5; controls inside are flat fills (LG18) |
| Dashboard | Toasts | R2 + R5, regular; R1 lens is optional (small) |
| Dashboard | Round buttons (close, more) | R2 + R4; lens only when they float over content |
| Dashboard | Live call input bar | R1 + R2 + R7 `over-text` (it sits over the transcript) |

**Where NOT:**
- Today tiles, calendar cells, appointment cards and transcript bubbles. These are the content layer (LG1).
- Anything behind or containing dense text or tables, e.g. the appointments table or a long transcript inside the glass. Use Regular at fill .78+ or solid.
- Glass on glass. No lens or blur on buttons inside a sheet, capsule or sidebar (LG18).
- The lens on large surfaces (sidebar, sheet body) or more than ~3 refracting surfaces on screen (LG2, LG28).
- Clear glass on our plain light backgrounds (LG19).
- Form fields on sign-in. Fields are content and need solid contrast.
- Under any reduce-transparency or increase-contrast setting (R9), and any moving highlight under reduced motion.

### 13.x As built: Liquid Glass (22-23 Oct)

**Process:** the same four agents worked in rounds:
- one researcher, for the material and where it belongs;
- Builder 1, for the shared material, the talk page and the sign-in page;
- Builder 2, for the dashboard;
- the reviewer and the consumer tester.

| Page | Reviewer | Tester |
|---|---|---|
| Talk | rejected in round 1, approved in round 2 | 8.5 → 9/10 |
| Sign-in | approved in round 1 | 8.5/10 |
| Dashboard | approved in round 1, then polished | 9/10 |

**The material:**
- `.e-liquid` in `static/ui/emma-ui.css` and `liquid()` in `emma-ui.js`.
- **Lens:** real `feDisplacementMap` refraction through `backdrop-filter: url(#…)`, in Chromium only (`html.lg-refract`, set by head.js). The bend is `min(8px, h*.14)` with an 8-12px convex rim, and a 2.5px blur runs after the bend.
- **Rim and highlight:** a specular rim with a 0.5px hairline, and a fixed top-left highlight.
- **Press:** a spring swell of about 1.04, on the pressed control only.
- **Entrance:** the lensing ramps up instead of fading in, and filters are built after first paint.
- **Shape and depth:** concentric radii (`--r-outer`/`--inset`/`--r-inner`), and an adaptive shadow (`.over-text`).
- **Fallbacks:** Safari and Firefox get frosted blur with the same rim and press. Reduced transparency and increased contrast give solid fills with the lens off. Reduced motion shows end states.
- **CSP:** an inline `<svg>` defs container, filters appended by same-origin JS, `url(#id)` from external CSS, and `feImage` data: maps. All work under `default-src 'self'` and `img-src 'self' data:`.

**Placement (the lens only where content passes under it):**
- **Lens:**
  - the talk page's call bar and typed box, over a state-coloured pool that rests at opacity 0 and rises with a call;
  - the dashboard's phone tab bar, over the Calendar, Tasks and the transcript;
  - the Live call input bar, which ramps in on Take over.
- **Rim and blur:** the talk and sign-in capsules, stuck toolbars, toasts, segmented platters, the "Jump to latest" pill and the phone More menu.
- **Rim, no blur:** sheets (no blur on blur), and the sidebar (nothing passes under it).
- **Not glass:** the orb, tiles, calendar cells and events, bubbles, tables, form fields, and anything nested inside other glass.

**Rejected along the way:**
- **Lens too strong:** the first lens warped instead of bending (a 23-25px bend with 28px linear ramps, which doubled text). Cut to about 8px.
- **Press on the whole bar:** pressing one pill swelled the whole call bar and blurred its labels.
- **Moving highlight:** a 180px highlight followed the pointer like a flashlight.
- **Candy pills:** a glossy red End call pill with a coloured glow.
- **Nothing to bend:** a lens sat over plain white.
- **Slow phone load:** about 3s, until the glass build was deferred.
- **Entrance stain:** a glow showed before the buttons.
- **Glass faults:** blur on blur in sheets; a fixed `--r-inner` that turned menu rows into pills; the dense fill lost to specificity.

**Frame time** (window visible, page at 60fps): the lens and plain blur both measured p50 16.6-16.7ms and p90 16.8-17.1ms, on the talk page during a typed call and on the dashboard scrolling the Calendar and Tasks at 375px.

**Bug found by the tester and fixed:** one typed caller line showed as two bubbles on Live call. The stored caller turn arrives after Emma's first reply, and it was matched against the last line.

**Follow-ups outside this work:**
- Each staff-ended talk-page call with no number still creates a callback task.
- One greeting variant reads oddly ("Hi, Emma at Pearl Dental…").
- Emma's "closed on Sundays" answer offers no alternative day.
- There is no goodbye when a typed call ends.
