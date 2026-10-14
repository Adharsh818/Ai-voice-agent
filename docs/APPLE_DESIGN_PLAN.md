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
