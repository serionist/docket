# Reality lenses

The one list of what a page of Docket's web app is checked against beyond its happy path. `ui-audit` threads every
lens through its journeys; `check-ui` runs the ones that apply to the pages a change touches. Both read this file;
neither keeps a copy.

Each lens says what to exercise, what passes, and what to write when it could not be exercised. A lens that could not
run is never skipped in silence: it is `BLOCKED(env)` with the reason (no world for it, not driven live), in the
coverage table and in the reply. A lens that does not apply to a page is `n/a` with why.

The text of record decides what a page must show in each state: its rows and "Built" notes in
`docs/plans/2026-10-20-252-react-app.md`, its row in `docs/design.md`'s web app section, and its decisions, with
`build-ui`'s bar for every page. A lens finds the states; the text of record says what they should look like. All data
is invented: the worlds of `packages/docket/tests/ui_world.py`, never a real vault, message or recording.

## Cardinality

- **Exercise:** every list, group, picker and count at 0, 1, 2 and many. The empty flavours, each on its own: truly
  empty (a new vault: the `empty` world), filtered empty (nothing matches the find box or the filters), waiting (the
  index still being built answers `waiting` and the page asks again every 5 s), and error-empty (the read failed). A
  thing this vault doesn't have at all: a source never synced, a recording with no audio stored, a brief with no
  decisions.
- **Passes:** each flavour reads differently and offers its way out (clear the filters, the command to run, Retry);
  filtered empty says "no matches", never "nothing here"; error-empty never poses as empty; counts say what the groups
  hold and pluralise; an absent thing is said plainly, with why.
- **Not exercised:** `BLOCKED(env): no world for <count>`, per flavour.

## Volume

- **Exercise:** real-sized data: the perf ledger's 5,000 items and 2,000 meetings, 300 staged records, 120 briefs, a
  recording of 360 lines; long titles, long names and long paths, Hungarian accents, many chips and facets.
- **Passes:** a long list scrolls with its group heads in place; Today draws at most 100 due rows with the rest a link
  away; long text truncates with the full value reachable (the panel, a tooltip, Copy); nothing pushes the primary
  action off screen; a page opened from the sidebar shows its rows with its keys live within 0.25 s, warm, best of
  three (DR-0712); a world that loaded nothing is caught before it is judged (assert the row count).
- **Not exercised:** `BLOCKED(env): no volume world` (the browser tests' perf vaults are the place for it).

## Latency

- **Exercise:** a slow network (`faults.slow()` or `page.route` with a 2 s delay on `/api/web/**`), first load and
  every write; the Web Vitals per state.
- **Passes:** loading says what is loading, in the final layout, with no blank flash and no layout jump as data lands;
  a write's button answers at once (busy, still focusable); the vitals stay inside the run's budget (Tier 1).
- **Not exercised:** rarely blocked; how it feels is judged live (Interaction).

## Failure injection

The service has no fault hook: route interception stands in (`faults` in `lib/part.mjs`, or `page.route`).

- **Exercise:** a read answering 500; a write answering 400 with the writer's words, and 409 for a record, decision or
  entity file that moved since it was shown; the service gone (requests refused); a timeout on a write.
- **Passes:** a failure says what failed in the writer's words and what still works; Retry, or the page's way out,
  really asks again and keeps the URL's state; a refused write keeps every typed value beside the words; a 409 says
  what changed and reads the page again; no raw exception, stack or status code reaches the page; environment
  failures during a run are never filed as findings.
- **Not exercised:** `BLOCKED(env): not intercepted`, per failure kind.

## Invalid input

- **Exercise:** every form through the battery below, the cases its `forms` spec lists (`template/lib/battery.mjs`).
  Docket's forms: the new item, a panel's fields (who, due, stream, kind, the why, the notes), the snooze's day,
  Meetings' points and outcome, Staging's typed cut ranges, Curate's edit form, Search's box and dates.
  - B1 required fields empty and whitespace-only
  - B2 format: an id, a time range (`12:30-14:05`), a date (`since=soon`), with valid siblings filled
  - B3 length: one character, the maximum, one over, a 10,000-character paste
  - B4 leading and trailing spaces
  - B5 accents, other scripts and emoji (`Dóra`, `Ünüşöő`)
  - B6 markup and script text (`<b>x</b>"'<script>alert(1)</script>`)
  - B7 a secret with a confirmation field: n/a, no page takes one
  - B8 a code: n/a, no form takes one
  - B9 the sign-in link: tampered, expired, used (`sessionLinks` in `lib/docket.mjs`)
  - B10 a garbage route inside the app
- **Passes:** an error appears on blur or submit, never on first paint or the first keystroke, and clears the moment
  the value is valid; a refused write never wipes typed text; focus moves to the first invalid field; the writer's
  refusal lands beside the field it is about; limits are told, never a 5xx or a 413 for a sane value; spaces are
  trimmed or refused, never saved silently; B5 is stored and shown intact; B6 renders as text (any execution is a
  blocker); B9 gives the used-link page each time, with no hint which case it was; B10 gives the app's Not found.
- **Not exercised:** `BLOCKED(env): <case>` with why.

## Input reality

- **Exercise:** paste into every field (ids, rids, multi-line notes), Enter in every field, the date picker typed and
  picked, the who combobox typed, arrowed and pasted.
- **Passes:** Enter saves a field, drops from the why and snoozes from the date (DR-0710); paste is trimmed where the
  field says so; Enter in the who field takes the name typed, never a longer one (DR-0714); the date picker writes the
  app's own day text, never the browser's mm/dd/yyyy.
- **Not exercised:** rarely blocked.

## Interruption

- **Exercise:** back and forward, a refresh on every state, a deep link to every record, hit and line (the links Claude
  and the plugin's skills give: `/queue`, `/search?query=...&hit=...`, `/recording?rid=...&at=...#at`,
  `/briefs?brief=brief:<subject>`), a link opened in a new tab, leaving a field, panel or dialog with unsaved text, a
  tab open across an upgrade of the build.
- **Passes:** the page's state is in its URL, written back byte for byte as the server's canonical string (DR-0792); a
  refresh lands on the same thing; a link with `#at` lands on its line; unsaved text asks once before it is dropped
  (the router's blocker, `beforeunload`); a page loaded after the build changed loads whole, never a new chunk on the
  old app (DR-0784).
- **Not exercised:** rarely blocked; when it is, say which.

## Concurrency

- **Exercise:** the owner in two tabs; a write from the CLI, Claude or a sync while the page is open (the `post` and
  `other` steps, or a second tab); a record, decision or entity file that moved since the page showed it; a write
  landing while the owner types.
- **Passes:** the 5 s poll shows another writer's change in place, keeping the selection, the ticks, the scroll and
  typed text, and holds while the owner is mid-change (DR-0711); a save that raced another writer refetches; a moved
  record answers 409 with what changed and the typed text kept; live changes never move the row under the cursor.
- **Not exercised:** `BLOCKED(env): no second tab driven`.

## Sessions and access

Docket has one owner. The personas are the owner, signed in through a one-time link, and a stranger with no session.

- **Exercise:** each page as the owner; each page path as a stranger; the session lapsing mid-page (the cookie
  cleared, or the service restarted); a used or stale sign-in link.
- **Passes:** a stranger gets the 401 sign-in page naming `docket open` for that page, and neither the vault nor the
  machine (DR-0792); a lapsed session shows the shell's dialog naming `docket open`, keeps everything typed, and Retry
  asks again; no page offers to release a held recording, forget, or turn credentials redaction off: each names its
  command for the owner's console (DR-0382, DR-0577, DR-0601).
- **Not exercised:** rarely blocked.

## What a read sends

- **Exercise:** every `/api/web` read the page makes, in every state, with every selection a panel takes: the
  answer's whole JSON, not only what renders.
- **Passes:** a read sends nothing its page would not show: no held record's body or title, no cut stretch's words,
  no never-store value where the page hides it, no stoplist key's hash (DR-0380, DR-0382, DR-0786);
  `test_web_reads_hide.py` walks the read. A refusal names no count or shape of what it refused.
- **Not exercised:** never: a read not walked is a finding.

## Environment

Desktop first, but every page is shot at 1440x900 and 390x844 (DR-0712), so phone width is a bar every page meets.

- **Exercise:** a full desktop (1440 by 900), the narrowest full layout (1280 by 720), a phone (390 by 844, a real
  coarse pointer), light and dark; the shell's folds (DR-0708): 1024 (the sidebar as icons) and 900 (the sidebar a
  sheet, the panel over the list); a short viewport (1280 by 600); 125 % zoom; reduced motion; a cold reload in dark.
  Extended: forced colours, Firefox and WebKit at the canonical cells.
- **Passes:** one scroller per page, no sideways scroll, nothing clipped; the primary action and a write's notice with
  its Undo reachable at every width; dialogs and the date picker fit the short viewport and the phone (a sheet from the
  foot below 768); no white flash on a dark cold reload (the server writes `data-theme`); reduced motion honoured; no
  CSP violation (a Radix part's style element takes the nonce, DR-0706).
- **Not exercised:** `BLOCKED(env): <engine> not installed` for the extended engines.

## Time and locale

- **Exercise:** the clock fixed at the world's now (devkit ui's Tuesday 09:30 in Europe/Budapest); times at their
  extremes (seconds ago, yesterday, last year, a snooze ending today); a browser zone other than the owner's; the
  owner's midnight; English text with Hungarian names.
- **Passes:** times come from the server as text in the owner's zone (DR-0709); nothing counts down, and a list says
  when it goes out of date, which the poll then refetches (DR-0711); a sync reads `synced 09:10` or `yesterday 17:40`;
  nothing is dated after the world's now; accents sort and match as the server's rule says (`dora` finds Dóra).
- **Not exercised:** `BLOCKED(env): no clock control`.

## Notification stress

- **Exercise:** a write's notice, its Undo and Redo by batch, a refusal, a bulk's dry run naming skipped rows; the
  sidebar's counts at 0, 1, 9 and 99 or more; notices arriving while a dialog is open.
- **Passes:** the notice sits at the list's foot, or the panel's where the panel covers the list (below 1024 px); Undo
  is one key (u) and one click; refusals stay until the text changes; counts read at a glance; nothing steals focus
  from a field.
- **Not exercised:** `BLOCKED(env): no way to raise <n>`.

## Touch and hover

Every page, since every page is shot at phone width.

- **Exercise:** every control in the phone cell (390 px, a coarse pointer); every hover-only affordance (a row's quick
  buttons, a line that cites, tooltips).
- **Passes:** nothing essential by hover alone; row actions work by tap and by keyboard; targets meet 44 px on a coarse
  pointer and 24 on a fine one, inline text links aside (Tier 1); long-press does nothing surprising.
- **Not exercised:** `BLOCKED(env): no coarse-pointer context` is a capture bug: fix the capture, never skip.

## Interaction and usability, driven live

Stills cannot show this lens: a shot shows an interaction's result, never how it feels. It runs as its own pass, live,
with motion on, in headless Playwright against a throwaway service.

- **Exercise:** every menu, Select, combobox, date picker and dialog (open, place, dismiss on Esc and outside click,
  scroll when long); Tab through every form and dialog; the keys (`j`, `k`, `x`, `1` to `4`, `u`, `Enter`, `Esc`,
  `?` and each page's own) against the buttons they stand for; each kept behaviour of the plan end to end; every write
  on a slow network.
- **Passes:** the right control for the job; overlays never clip the viewport; tab order follows the reading order,
  the focus ring shows, focus is trapped in dialogs and given back after; an Esc that closes a popover never also
  closes the panel; one obvious primary action; busy states show at once and nothing jumps when data lands.
- **Not exercised:** `BLOCKED(env): not driven live` (a sign-off from stills is incomplete; say so).

## Tier 1: machine checks

Run inside every capture (`template/lib/capture.mjs`) with `uv run devkit ui`'s rules, so a cell the audit passes, a
round passes too; a hit is a finding without judgment. Judges read them and never hunt them again.

| Check | What runs | Passes |
|---|---|---|
| axe | axe-core per state, both themes, at the canonical viewport, inside the open modal when there is one; WCAG 2.1 A and AA and best practices | no violation |
| contrast | axe's `color-contrast` subset | 4.5:1 for text, 3:1 for large text and non-text |
| CSP | `securitypolicyviolation` events since the page loaded | none |
| sideways | the document scrolling sideways, per cell | never |
| hit targets | every control, per viewport, pointer-aware, inline text and hidden controls aside | 24 px on a fine pointer, 44 px on a coarse one |
| overflow | text nodes with `scrollWidth > clientWidth` that are not designed scrollers, per cell | none |
| raw keys | unrendered `{{ }}` (Docket has no dictionary of keys) | none |
| pre-paint error | `aria-invalid` on an untouched form | none |
| console budget | console errors and page errors not on the run's allow-list | none |
| network budget | responses of 400 and above not in the step's expected statuses; requests to any other origin | none |
| banned words | visible text against the run's `bannedText` (`undefined`, `NaN`, `[object Object]`, a traceback) | none |
| Web Vitals | CLS, LCP, INP per state against the run's budget | inside it |
| geometry | each shot's pixel size and pointer media read back against the cell it claims | matches, or the shot is refused |

A check that could not run (axe not loaded, no CDP session) is written in the manifest and the coverage table as
`BLOCKED(env)`, never read as a pass.

## Extended lenses

Only when the run's `lenses` is `extended`: forced colours, a screen reader pass by hand (NVDA or VoiceOver over one
kept behaviour per page), Firefox and WebKit. Under `standard` the report names them as not covered.
