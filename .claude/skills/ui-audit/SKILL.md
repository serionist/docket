---
name: ui-audit
description: Use when pages of Docket's web app under packages/docket/web need a full UI and UX audit before a release or the maintainer's look, or after a large UI change ("audit the UI", "UI review of the day pages", "is the app ready to show", "polish these pages", "the web app looks wrong"). Drives every journey through every state the text of record names and every reality lens, checks mechanically first (axe, contrast, CSP, targets, overflow, console and network budgets, pixel diffs), judges design and conformance, fixes systemic problems first and loops until a full pass is clean, then lists what is left as issues. Runs on a throwaway service it starts per invented world. Portable by hand; in Claude Code, a multi-agent engine. While building one page, use check-ui instead.
---

# Audit the UI

A full audit of a set of pages: every journey through them, every state their text of record names, and every lens in
`reality-lenses.md` (this folder). Machines check what is mechanical; judgment goes only to canonical shots and pixel
deltas; fixes land systemic-first; the loop runs until a full pass is clean. `check-ui` runs the same lenses on one
page while it is built; this is the pass over many pages. A file named here without a folder (`reference.md`,
`agents/judge.md`, `template/`) is in this skill's folder, `.claude/skills/ui-audit/`.

**The text of record** for a page is its rows and "Built" notes in `docs/plans/2026-10-20-252-react-app.md`, its row in
`docs/design.md`'s web app section, and its decisions (`uv run docs list decisions --path packages/docket/web`), with
`build-ui`'s bar. The links Claude and the plugin's skills give into the app are part of it.

## Inputs

Ask the developer once, before preflight, for every input not given: one message, the suggested value first. "Use the
defaults" skips the question. Write the answers into the run's `STATE.md`.

| Input | Meaning | Suggested |
|---|---|---|
| scope | the pages, by route (`/queue`, `/staging`) | required |
| issue | the issue `#<n>` the audit and its fixes run under, on its branch from `start-work` | required |
| tier | `portable` (by hand), `standard` or `deep` (Claude Code, below) | standard; deep above five pages or before a release |
| matrix | `full`: every grid state shoots the whole grid; `sampled`: only key journeys do, the rest the canonical cell | full |
| redesign | how far an improve may go: `conservative`, `improve`, `overhaul` (reworks what exists; never adds what the text of record lacks) | improve |
| lenses | `standard`, or `extended` (adds the extended lenses in `reality-lenses.md`) | standard |
| mode | `autonomous`: questions wait in the journal and go out as one batch; `interactive`: ask as they come | autonomous |
| cap | fix-and-verify iterations before stopping | 6 |

## Where things go

- **The run and its report stay local**, in `.local/ui-audit/<run>/`, `<run>` being `<date>-<issue>-<slug>`:
  `STATE.md` (the resume ledger), `journeys/` (one file per journey, the source of truth), `journal.md`,
  `impact-map.json`, `e2e/` (the runner), `screens/` (shots) and `report.md`. Layout and formats: `reference.md`,
  `template/README.md`, `template/journey-format.md`.
- **Nothing of the run is committed.** The fixes are app code, committed on the issue's branch as they land and merged
  through `finish-work`. The blessed baselines stay in `.local/ui-audit/baselines/<page>/` on this machine: DR-0712
  chose no pixel baselines in the repo.
- **What still needs work** becomes issues, one per open cluster or finding, with the maintainer's yes or under an
  autonomy grant (`reference.md`, The report and the open items).

## Touch points, in order (any tool)

1. **Ground truth.** For each page: its text of record, the kept behaviours through it, its `/api/web` reads and
   writes in `openapi.json`, its decisions, and the worlds of `packages/docket/tests/ui_world.py` that show its
   states. Start the run folder.
2. **Preflight**, each box into `STATE.md`. A box that can't be ticked makes the lenses that need it `BLOCKED(env)`,
   named, and the audit goes on without them.
   - The checkout is the branch under audit, the web app built from it (`npm run build` in `packages/docket/web` leaves
     no diff), so the service serves what is audited.
   - `template/` is copied to `.local/ui-audit/<run>/e2e/`, `package.template.json` copied to `package.json` there,
     then `npm install --ignore-scripts`, `config.example.json` copied to `config.json`, and `node runner.mjs check`
     passes: it starts a throwaway service on the `empty` world, identifies it, signs the owner in, finds the shell and
     the fonts, sees a stranger refused, and stops it. Chrome is the machine's own; nothing is downloaded.
   - Each journey's world exists in `ui_world.py`, or is added there in the audit's first change with its states
     (invented people only). A world the page needs that isn't there is a gap, named.
3. **Journeys** (`template/journey-format.md`): one file per journey, its flow as typed story points. Every state the
   text of record names is a shooting point; each lens that applies is a step or a sweep; a panel gets a richly
   filled record; empty states come from the `empty` world; writes nothing undoes run in a segment of their own;
   disruptive ones (failure, a lapsed session, concurrency) last in their segment. The grid is a full desktop (1440 by
   900), the narrowest full layout (1280 by 720) and a phone (390 by 844, coarse pointer), light and dark; the folds
   (1024, 900) and the short viewport are opt-in cells.
4. **Script:** one part per world under `e2e/parts/`, one module per journey, on `lib/part.mjs`; then a smoke part
   that asserts every hard capture case on real values (`template/README.md`). No drive starts on a capture layer that
   lies.
5. **Drive:** one segment per world, `node runner.mjs run --world <world> --parts <ids>`, in `_index.md`'s order.
   Each segment starts its own service and stops it at the end. The Tier 1 checks run inside every capture; a hit is
   a finding without judgment.
6. **Judge:** pixel-diff every shot against the run's last verified render and, where a page has blessed baselines,
   against those (`node lib/baseline-diff.mjs diff`); a zero-delta cell is verified for free. Judge the canonical cell
   (desktop, light, English) of every new or changed state, and once per pass the variant sweeps: dark, the laptop and
   the phone. By `agents/judge.md` and `agents/design-reference.md`; the first question: does the page match its text
   of record?
7. **Consolidate** (`agents/consolidator.md`): dedupe, cluster as systemic (one shared cause) or local, level each
   severity, and re-read the evidence of every blocker and major to confirm or refute it.
8. **Fix, systemic first** (`agents/fixer.md`): the shared layer, then a re-drive that closes the locals it fixed
   untouched, then the locals. Presentation only: a change to an `/api/web` route's shape or to what a write checks
   is a question. The sensitive areas (below) are fixed only with the maintainer's yes. `build-ui`'s checks once per
   batch; commits say what and why, `Refs #<n>`.
9. **Verify** is the next pass: re-drive what the impact map says changed, diff the identical cells, judge the deltas.
   Loop steps 5 to 9 until a pass is clean or the cap is reached.
10. **Interaction pass:** drive the fixed UI live, motion on, on a slow network (`reality-lenses.md`, Interaction), in
    headless Playwright against `serve.py`'s throwaway service. Stills cannot judge it; a sign-off without it is
    incomplete.
11. **Final pass** over every journey, outside the cap: no unexplained delta, no reopened journey, no fix after it.
12. **Close:** bless the canonical shots of each page that ended clean (`node lib/baseline-diff.mjs bless`, kept in
    `.local/`); write the run's `report.md` with its coverage table (each lens per page: checked, `n/a` with why, or
    `BLOCKED(env)` with why); file the open items; `finish-work` for the fixes. Name every `BLOCKED` row and every open
    item to the developer in the reply.

## Claude Code: standard tier

This session writes the journeys and the parts itself and starts the runner as a background Bash task, one segment at
a time. Each page's canonical shots, manifest slice and journey files go to one fresh judge agent (`agents/judge.md`),
in parallel, plus one sweep agent each for dark, the laptop and the phone. This session consolidates, fixes, and
drives the interaction pass with headless Playwright: the `playwright` plugin's MCP when its browser runs headless,
else a script on `e2e/lib/connect.mjs`. Recommend deep above five pages, before a release, or when a pass leaves more
than ten findings; the developer decides.

## Claude Code: deep tier (the engine)

Each role is a fresh agent on the strongest model unless the developer names another. A brief is its role file plus the
run's slice, never a rewritten prompt; agents return summaries and leave raw artifacts on disk. Forked fixers work in
this checkout on disjoint files.

| Role | Brief | Contract |
|---|---|---|
| Inventory | `agents/inventory.md` | builds `journeys/` and `impact-map.json` from the text of record and the source |
| Critic | `agents/critic.md` | re-derives coverage from the source; verdict CATALOG READY or NEEDS ANOTHER PASS |
| Scriptwright | `agents/scriptwright.md` | translates journeys into parts and the smoke; runs only plumbing |
| Script doctor | `agents/script-doctor.md` | triages `failures/`: app fault (a finding), script fault (repair), intended change (a journey delta) |
| Judge | `agents/judge.md` | conformance and design verdicts per journey batch; sweeps across pages |
| Consolidator | `agents/consolidator.md` | once per pass: clusters, severity, ratified statuses, the fix batches; sole writer of `journal.md` |
| Fixer | `agents/fixer.md` | lands one cluster, systemic first, code only; reports deltas |

The loop: inventory, then the critic until CATALOG READY; the scriptwright; this session runs the smoke; the runner
starts in the background, a segment per world; this session watches its sentinels (the Monitor tool with an
until-loop). A part's `.done` dispatches passlog, the pixel diff and its judge batch while later parts run; a
`.failed` part or new `failures/` records go to the script doctor only; `ENV-DOWN` pauses the run and never makes
findings; capture requests are served at a segment's end, never left to pile up. When every judge of the pass is
back, the consolidator, then the fixers in barrier order, then the next pass. Every phase writes `STATE.md`; a new
session resumes from it. The details (one writer per file per phase, statuses, the loop, the phase exits,
acceptance) are in `reference.md`.

## Safety rails

- Every segment runs on a throwaway service the run started: `serve.py` runs devkit ui's own service (its own config,
  state and models folders, a file token, the failing keyring, a free port). Before any part, `lib/docket.mjs`
  identifies it: loopback, never port 8741, and `/api/status` answering the token its world wrote with the recorded
  port, pid, vault and machine. A port or a name alone is not a guard. Nothing in a run touches port 8741, the
  owner's vault, Docket's folders, the keychain, the owner's cookie on 127.0.0.1 or the owner's own browser.
- The sensitive areas are fixed only with the maintainer's yes, or under an autonomy grant when the fix only makes a
  check stricter: the service's local surface (the Guard, the session, the CSP), what a read sends
  (`test_web_reads_hide.py`), and the acts that stay at the owner's console (releasing a held recording, forgetting,
  turning credentials redaction off).
- Worlds are invented; a real vault, message or recording never enters a run, not even as a shot.
- Screenshots the MCP takes land in `.playwright-mcp/` (git ignores it); move them into the run's `screens/` at once.

## Done means

- Every journey is `completed`, `wontfix` with the maintainer's yes, or `BLOCKED(env)` with its reason; no open blocker
  or major; every improve decided; the last full pass clean with no fix after it. Or the cap was reached, and the
  report lists what is open.
- The report has the coverage table; every open item is filed or listed for the maintainer; the fixes went through
  `finish-work`.

## Never

- Skip a lens in silence: what could not run is `BLOCKED(env)` with the reason, in the report and in the reply.
- Judge a list from its empty state, a page from its happy path, or a shot taken before the page settled.
- Loosen an assertion, widen an allow-list or re-bless a baseline to make a pass clean.
- Build what the text of record does not have: a new capability is a question and an issue first, never a fix.
- Drive a service the run didn't start and identify, or anything on port 8741.
- Commit the report, the run folder, sessions, the service record, manifests, baselines or shots.
- File an issue without `gate check-text`, or without the maintainer's yes outside an autonomy grant.

## Left out of the port

- **A fault hook in the service**: the service has none, and one would be a new surface for no other caller. Route
  interception in the browser (`faults` in `lib/part.mjs`) stands in, and the report says so.
- **Roles and permissions**: Docket has one owner; the personas are the owner and a stranger.
- **Committed baselines**: DR-0712.

## Traps

| What broke | When | What it cost | The guard |
|---|---|---|---|
| Phone cells turned touch emulation on, but the CSS still matched `pointer: fine`; a pass of "target under 44 px" findings were capture artifacts | another web app's audit | a pass of false findings | touch cells shoot in a context made with `isMobile` and `hasTouch`; every emulation check reads a rendered effect, never the flag |
| `page.screenshot` ignored the CDP device metrics, so every shot was the context's size | another web app's audit | a grid of unusable shots | shots through CDP `Page.captureScreenshot`; each cell's pixel size is read back and a mismatch is refused |
| A full-page CDP capture dropped the touch emulation, so every phone cell after the first rendered `pointer: fine` | another web app's port | caught by the smoke | `cdpShot` turns touch back on after a full-page capture; the smoke checks the coarse pointer on every phone cell |
| The audit's own mask style element was refused by the page's CSP and read as the page's violation | this port, 22 Oct (#299) | 24 false CSP hits on one state | masks paint through the CSSOM; the smoke asserts no CSP hit on a clean page |
| A success check guessed where a value sat in the answer, and every good write read as a failure | another web app's audit | a segment re-driven | checks are written against one observed answer, typed from `openapi.json` |
| The sign-in page was shot as the target page | another web app's audit | captures worth nothing | the owner signs in once per segment; `requireAuthed` before every signed-in shot |
| The smoke checked only the latest capture fix; capture bugs surfaced one drive at a time | another web app's audit | days of re-drives | the smoke asserts every hard capture case on real values before any drive |
| A state that writes was shot before its write answered | Meetings' first round (#252) | a shot of the state before | wait for the write's notice (`role=status`) before the shot |
| A round's world refused its first write, so the state it should show never came | the Recording page's first round (#252) | an empty round | assert each world's key record before its journeys; a refused setup is a script fault, not a page |
| Capture requests piled up until the run stalled | another web app's audit | a halted run | requests are served at a segment's end; one that survives two rounds escalates |
