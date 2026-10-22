---
name: check-ui
description: Use while building or changing a page of Docket's web app under packages/docket/web, and before calling UI work done ("check the UI", "how does it look", "screenshot it", "run the screenshot loop", the UI step of build-ui). Shoots the page's states with uv run devkit ui and its machine checks, drives what devkit ui can't (interaction, interruption, concurrency, a failing or slow network, a lapsed session, the shell's folds) in headless Playwright against a throwaway service, judges every shot against the page's text of record, improves in at most four rounds, and ends with a coverage table that names every lens it did not run.
---

# Check the UI while building it

A page gets the UI audit's scrutiny while it is being built, so an audit finds little. Every state and real data
shapes, not a demo of the happy path. The lenses are `ui-audit`'s, kept once in
`.claude/skills/ui-audit/reality-lenses.md`; this skill runs the ones that apply to the pages a change touches, and
says plainly which it could not run. Two tools do the driving:

| Tool | Drives |
|---|---|
| `uv run devkit ui <page>` | the page's states (`PAGES` in `tools/devkit/src/devkit/ui.py`) on invented worlds, light and dark at 1440x900 and 390x844, with axe, console, CSP, sideways and target checks that block the round (DR-0712) |
| headless Playwright on `serve.py`'s throwaway service | what still shots can't show: interaction, interruption, concurrency, failure, a lapsed session, a slow network, the folds |

Neither touches port 8741, the owner's vault, Docket's folders, the keychain, the owner's cookie on 127.0.0.1, or the
owner's own browser: both run devkit ui's own service (its own folders, a file token, the failing keyring, a free
port), signed in on `localhost`.

## Touch points, in order

1. **Name the pages** the change touches and read each one's text of record (`build-ui`, step 2).
2. **Give each new state its data**, in the same change: a `State` in `PAGES` (`tools/devkit/src/devkit/ui.py`) and,
   when no world holds it, the records in a world of `packages/docket/tests/ui_world.py`. Cover none, one, a few and
   many (long titles, long names, Hungarian accents), a refusal (`expects=(REFUSED,)`), a race from another tab
   (`other` or `post`, `expects=(MOVED,)`), a write's notice (`wait` for `role=status`, `after=` to undo it once shot),
   and `fresh=True` for a write nothing undoes. Invented people only. A list judged empty is a list not judged.
3. **Shoot:** `uv run devkit ui <page>` (once per checkout, `npm ci` in `packages/docket/web` first, for axe). It
   writes `.local/ui/<page>/r<n>/` with the shots and `checks.txt`; every check failing there blocks the round.
4. **Look at every shot** before judging (the Read tool shows a PNG). Compare each with the text of record and
   `build-ui`'s bar: the words, the layout, each state, both themes, the phone, density (20 rows at a desktop). List
   what is off.
5. **Drive the rest live**, headless. Start a service on the page's world:
   `uv run python .claude/skills/ui-audit/template/serve.py <world> --record .local/check-ui/service.json --link <page URL>`
   (in the background; it prints the origin and a one-time sign-in link, `serve.py link <record> <page>` makes
   another, and `<record>.stop` ends it). Drive it with the `playwright` plugin's MCP when its browser runs headless,
   else a short script on the runner's lib (`ui-audit/template/lib/connect.mjs` and `docket.mjs`, installed as
   `ui-audit`'s preflight says). At least:
   - **interaction:** menus, Select, the who combobox, the date picker and dialogs open, place, scroll and close on
     Esc and outside click; an Esc a popover took leaves the panel open; Tab order, focus trapped in dialogs and given
     back; each key against the button it stands for; busy states;
   - **interruption:** a refresh on each state, back and forward, the deep links Claude and the skills give
     (`/queue?open=A-0022`, `/search?query=...&hit=...`), leaving with unsaved text;
   - **concurrency:** another tab's write shown in place within the 5 s poll, the poll holding while the owner types;
     a moved record answered 409 with the typed text kept;
   - **failure:** a read answering 500 (`page.route`): the writer's words and a way to ask again that keeps the URL's
     state; the service gone: a write says so;
   - **a lapsed session:** the session cookie cleared mid-page: the dialog names `docket open`, typed text stays,
     Retry reads again;
   - **slow network:** a 2 s delay on `/api/web/**`: what loading says, no layout jump as rows land;
   - **environment:** the widths devkit ui doesn't shoot: 1280, 1024 and 900 with the panel open, a short 1280x600,
     125 % zoom, a dark cold reload (the server writes `data-theme`).
   Stop the service when done (`<record>.stop`).
6. **Improve and repeat** from step 3 for the states the fix touched. At most four rounds a page, fewer when a round
   finds only polish (DR-0712); what is left goes in the PR. A cosmetic fix is proven by its shot; a behaviour, data or
   accessibility fix gets a Vitest or a step in the page's browser test. Batch the fixes and build once at the end
   (`build-ui`, step 9).
7. **Report the coverage:** a table with one row per lens (per page when there are several): checked, `n/a` (why),
   or not checked (why). Warn about every "not checked" row by name, in the reply and in the PR. Each round goes into
   the PR, and into the plan's "Built" notes when there is a plan: its shots, what it found, what was fixed.

## Claude Code

- Read the shots before judging; while iterating, shoot one page and read its few changed states, then the whole round
  before calling the work done.
- Screenshots the MCP takes land in `.playwright-mcp/`, which git ignores; move what you keep to
  `.local/check-ui/<date>-<page>/`.
- Forked agents may each drive one page; each returns its coverage rows, and this session merges the table.

## Done means

- Every changed page was shot by `devkit ui` with every check passing, and driven through every lens that applies, or
  the gap is in the coverage table and the developer was told.
- The final round's shots exist under `.local/`, and none is committed.

## Never

- Call UI work done from reading the code.
- Judge a list from its empty state, or a page from its happy path.
- Leave a lens out of the coverage table because it could not be run.
- Point a browser, a script or a request at port 8741, the owner's vault, cookie or keychain, or drive the owner's own
  browser.
- Commit a shot, or use a real person's data, or a shot of it, to fill a state.

## Traps

| What broke | When | What it cost | The guard |
|---|---|---|---|
| A write's state was shot before its write answered | Meetings, round 1 (#252) | a shot of the state before | a `wait` step for the notice |
| A round's world refused its first write, so its folder held nothing to judge | Recording, round 1 (#252) | a round lost | read `checks.txt` and the shot count before judging |
| Targets under 44 px on a phone, round after round | the first round of nearly every page (#252) | a round each | the targets check at 390x844 with a coarse pointer; build with the coarse sizes from the start |
| From 768 to 1023 px the notice's Undo sat under the panel, which devkit ui's two sizes never show | Meetings' review (#252) | Undo out of reach since the Ledger | drive 900 px with the panel open |
| A test read the date picker's box before Radix placed it | the Queue's PR (#252) | a flaky CI run | wait for the placed popover before measuring |
| A failed read was shot during Query's retries and read as loading for good | this skill's first pass, 22 Oct (#299) | a false finding | wait out the three retries, about 7 s, before judging a failure |
