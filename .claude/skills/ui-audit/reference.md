# UI audit reference

The mechanics behind `SKILL.md`: the run folder, who writes what, the statuses, the two verification tiers, the deep
tier's loop, the phase exits, acceptance, baselines and the diff rules, the report and the open items. The lenses are
in `reality-lenses.md`; the runner and its file formats in `template/README.md` and `template/journey-format.md`.

## The run folder

`.local/ui-audit/<run>/`, `<run>` = `<date>-<issue>-<slug>`. Git ignores all of it (`.local/`).

| Path | What it holds |
|---|---|
| `STATE.md` | the resume ledger: the inputs, phase, pass, cursor, environment facts (the commit, Chrome's version, Playwright's, fonts, each segment's world and port), decisions, blockers. Written after every unit of work; every new session starts here |
| `journeys/J-nn-<slug>.md` | one per journey, the source of truth: frontmatter (definition and status), `## Flow` (story points), `## Pass pN` sections (shots, findings, decisions) |
| `journeys/_index.md` | the segments in order, each a world with its parts; the worlds (which of `ui_world.py`'s, what each holds, the minimal replay prefix per journey); the personas |
| `journal.md` | the cross-journey ledger, written by the consolidator only: counts, clusters, passes, wontfix, questions, decisions |
| `impact-map.json` | route, component and shared-file globs to journey ids; built by the inventory, extended from fixers' reports |
| `e2e/` | the copied template plus `package.json`, `config.json`, `parts/`, `state/` (the service's record and the owner's session), `manifest.<pass>.jsonl`, `failures/`, `service.log` |
| `screens/p<N>/<journey>/` | shots `<state>-<width>-<theme>-<lang>[-clip].png` per pass |
| `screens/baseline/<journey>/` | the last verified render per cell, the run-local pixel-diff reference |
| `report.md` | the run's report (The report, below), written at the close or the cap; never committed |

The blessed baselines live beside the runs, in `.local/ui-audit/baselines/<page>/`, on this machine only.

## One writer per file per phase

Machine state lives in YAML frontmatter and fixed subsections, written atomically (a temporary file, then a rename).

| Phase | File or section | The one writer |
|---|---|---|
| capture | a journey's `### Shots` block | code: `lib/passlog.mjs`, never an agent |
| judge | a journey's `### Findings (judge, pN)` | one judge per journey per pass (batches are disjoint) |
| triage | a failed journey's `### Findings (judge, pN)` | the script doctor; failed journeys are left out of judge batches that pass |
| consolidate | `### Decisions`, the status block, `journal.md` | the consolidator |
| fix | app code | fixers; they return deltas and never touch journeys or the journal |
| any | a journey's definition and flow | the orchestrating session, only as a versioned intended change |

Sweep judges (dark, the laptop, the phone; all forms, all empty states, all lists, all dialogs) write no journey
section: they return findings, and the consolidator files them.

## Statuses, findings, clusters

- **Journey status:** `pending`, `in-progress`, `completed`, `wontfix`, `blocked(env)`, `blocked(question)`. Judges
  propose `completed` or `wontfix`; only the consolidator sets them. `completed` means verified, not frozen: the
  journey is re-driven every pass and any pixel delta reopens it.
- **Finding** (journey-local `F1`, `F2`, ..., never reused within the file): severity (blocker, major, minor, polish,
  improve), category, the story point (`J-07.3`), the cell, the claim, what the owner experiences, the evidence shot,
  the fix idea, a `cluster:` tag, and for an improve a `user-cost:` line. A finding that comes back after it was
  verified is a new finding that cites the old one.
- **Cluster** (`CL-nn`, run-wide, never reused): the findings that share one cause, marked systemic (a shared part,
  token, the shell, the day pages' frame, the one place errors show: one fix, many pages) or local. The fix batches
  are clusters.
- **Severity anchors:** blocker = a kept behaviour is impossible, typed text or a write is lost, markup executes, a
  read sends what its page hides, or a page releases, forgets or turns credentials redaction off; major = a broken
  layout, wrong validation behaviour, a dead control or key, a missing loading, empty or error state, a state that
  lies, a Claude link that opens another state, a mismatch with the text of record the owner would act on; minor =
  misalignment, inconsistent words, the wrong empty flavour, truncation with no way to the full value; polish =
  refinement within the tokens; improve = a better design with a named cost to the owner. These are the audit's own
  levels; a code review still uses `REVIEW.md`'s.

## Verification: two tiers, machines first

- **Tier 1** runs inside `capture()`: the checks in `reality-lenses.md`. Determinism comes first: the clock fixed at the
  world's now, reduced motion forced, a settle (network idle, two frames, animations done) before each shot, dynamic
  regions masked, the viewport and DPR set per cell and read back.
- **Tier 2:** every shot is diffed against `screens/baseline/` (pixelmatch, by the diff rules under Baselines). Zero
  delta is verified without a judge. Judges get the journey's intent (its expectations, personas, effects, expected
  statuses), its manifest lines, and the canonical cell (`judgedCombos[0]`: desktop, light, English) per state;
  variant cells reach a judge only as deltas or through the sweeps.
- **Pass 1** has no run-local baselines: the canonical cell is judged and every variant cell is seeded as a baseline
  unjudged; variants are covered by the sweeps. A state seen for the first time in a later pass is judged in full.
- **A re-run** of pages with blessed baselines diffs their canonical cells against the blessed set first; clean cells
  seed the run-local baselines unjudged, and only changed or new cells are judged.
- **Promotion:** a shot becomes the run-local baseline when its journey's findings are verified or it goes `completed`.

## The deep tier's loop

1. Compute the pass set: journeys not `completed`, `wontfix` or `blocked(env)`, plus every journey the impact map ties
   to files fixed since the last pass, plus journeys with open capture requests. Pass 1 is everything.
2. Start the runner in the background, one segment per world (`node runner.mjs run --world <w> --parts ...`), in the
   order `_index.md` gives. The runner owns each segment's service; never start or stop one by hand while it runs.
3. Watch, in this order of priority:
   1. `e2e/parts/<nn>.done`: run passlog for its journeys, pixel-diff, and send its journeys to one judge batch (first
      pass: siblings together, for patterns across them; re-judging: one journey, scoped to what changed). Journeys
      with new `failures/` records go to the script doctor instead.
   2. `e2e/parts/<nn>.failed`: the script doctor only; never judge a failed part's stale states.
   3. `e2e/ENV-DOWN`: pause, read `service.log`, tell the developer, retry the segment. No findings from environment
      failures.
   4. Capture requests in judges' returns: close the ones the scripts already satisfy; batch the rest for one
      scriptwright patch at the next quiet point (a segment's end), re-capture those journeys, and re-judge only the
      journeys that asked. A request that survives two rounds escalates (a script or world gap).
   5. The last segment's exit: the last judge batches and the sweeps. When every judge is back, the consolidator.
4. The consolidator hands back the fix batches, systemic first; the fix phase follows; `STATE.md` records the cursor.

The runner's liveness: `e2e/.runner.pid` and the mtime of `e2e/parts/.heartbeat`. A new session adopts a live runner
or starts the segment again; it never assumes an old task handle still works. A segment's service dies with it, so a
segment is always started whole.

## Phases and their exits

- **0 Preflight:** every box of SKILL step 2 in `STATE.md`, or its lens marked `BLOCKED(env)`.
- **1 Inventory:** journeys, `_index.md` and `impact-map.json` written; the critic says CATALOG READY.
- **1.5 Script:** parts and the smoke written; the smoke passes, run by the orchestrating session, not the scriptwright.
  On a re-run the scriptwright patches the parts the changes touched and writes parts only for new pages.
- **2 Capture, judge, consolidate (pass N):** every journey in the pass set has its shots, findings and decisions for
  the pass; statuses set; the fix batches handed over.
- **3 Fix:** every admitted cluster fixed or decided. Barrier order: systemic fixes land and commit; the affected
  journeys are re-driven; locals that evaporated close untouched; then local fixers in parallel on disjoint files.
  Improves are admitted only up to iteration cap minus two; later ones become open items for the next run. A fix that
  redesigns a flow returns an intended-change delta, and the journey is versioned before the next script patch.
- **4 Verify** is the next pass of phase 2, over the impact map's set.
- **5 Interaction:** the live pass on the fixed UI; its findings feed one more fix and verify loop under the same cap.
- **6 Final and close:** every journey re-driven, outside the cap; acceptance below; then the close steps (SKILL step
  12) and the harvest: improvements to `e2e/lib/*.mjs`, `runner.mjs`, `serve.py` and `template/README.md` that are
  generic go back into `template/`, lessons into the role briefs, new traps into `SKILL.md`, each in its own reviewed
  change.

**Iteration accounting:** pass 1 is iteration 0; only a pass that follows a fix commit counts toward the cap; a
re-judge on pixel deltas alone and the final pass don't.

**Acceptance:** every journey `completed`, `wontfix` or `blocked(env)`; no `blocked(question)` left (the questions were
asked and answered before the final pass); no open blocker or major; every improve fixed or ratified wontfix; the final
pass clean with no reopened journey and no fix landed after it. At the cap instead: stop, write what is open into the
report, list the open items, tell the developer.

## Questions (autonomous mode)

The run never stops for a question. Anything decidable is decided and logged in the journal's decisions; under an
autonomy grant, one that outlives the run is also a proposed decision (`start-work`, Decisions on the way). What needs
the maintainer (a wontfix that changes what the owner gets, a change to an `/api/web` route's shape, a missing world,
a capability the text of record lacks, anything on a one-way door) goes to the journal's `## Questions`: the item, what
it blocks, the recommended answer. The journeys it blocks go `blocked(question)`; everything else continues. The
questions go out as one batch at the last moment: when nothing else can move, or before the final pass. In interactive
mode, ask as they come.

**Rework or new capability:** re-arranging what the page already shows (moving content, regrouping, splitting or
merging tabs, recomposing from data on the page) is a rework, in play at the run's `redesign` level. Showing or doing
something the text of record does not have is a new capability: it is a question, and it needs its own issue and plan
(`start-work`) before any code.

## Baselines

Two layers, both on this machine only:

- **Run-local** (`screens/baseline/`, `lib/pixeldiff.mjs`): the last verified render of every cell, promoted on
  verification, diffed every pass.
- **Blessed** (`.local/ui-audit/baselines/<page>/`, `lib/baseline-diff.mjs`): a few canonical cells per key state of
  each page that ended clean, never the whole grid, so the next audit on this machine re-verifies an unchanged page for
  free. `<page>` is the page's route name, lowercase, with a part after a hyphen (`queue`, `staging-held`). Bless only
  verified states of invented data, never one marked `secrets: true`. Never committed: DR-0712 chose no pixel
  baselines in the repo while the pages change PR by PR. A stale blessed shot shows up as a change to judge, never as a
  false pass.
- **Platform:** fonts and anti-aliasing differ between systems and Chrome builds. `bless` writes `baseline.json`
  (Playwright, browser, platform, pixelmatch major) next to the shots; `diff` on another platform reports the mismatch
  and treats every cell as new instead of changed. After a Chrome or Playwright update, re-bless once (`bless
  --replace-platform`) after a clean re-run.
- **The diff rules** (`config.json`, read by both layers through `diffOptions` in `lib/pixeldiff.mjs`):
  - `pixelThreshold` (0.1): how different two colours must be for a pixel to count, 0 to 1, per pixel, in OKLab with
    the HyAB distance (pixelmatch 8), 1 being black against white.
  - The ratio rule, the default: a cell changed when the share of differing pixels exceeds `pixelDiffRatioMax` (0).
  - The window rule, when `pixelWindowSize` N is set: a cell changed when the most differing pixels in any N by N
    square exceed `pixelWindowMax`. Scattered anti-aliasing never fills a square, while a real change is compact. Turn
    it on only when clean cells keep showing noise deltas, with the max below what one changed character makes at DPR
    1; a loosened rule is never the way to make a pass clean.
  - Mask rectangles a selector can't reach (`maskRects`) go to pixelmatch as its `ignoreMask`. `maskRegions`
    (selectors) are painted over at capture, through the CSSOM, so the page's CSP never sees a style of ours.

## The report and the open items

Nothing goes to `docs/reviews/`. The run's report is `.local/ui-audit/<run>/report.md`, written at the close (or at the
cap) and kept in the run folder with the rest of the run. What outlives the run is the fixes, merged through
`finish-work`, and the open items as issues.

The report's sections:

1. **Verdict:** three sentences: ready or not, and the top systemic problems.
2. **Summary:** the audited commit (full sha), tier, model, iterations and passes; findings by severity, opened and
   closed; journeys by status.
3. **Pages by severity:** one row per page, a column per severity.
4. **Systemic findings:** the cause, the shared fix (commit), the findings it closed.
5. **Findings** `F1`, `F2`, ...: severity, page and state, the cell, the journey point, the evidence shot in the run
   folder, the fix, its status (`open`, `question`, `fixed`, `verified`, `wontfix`), and its open item when open.
6. **Open items:** each one's title, the findings it carries, and its issue number once filed.
7. **Questions and answers** (verbatim).
8. **Wontfix,** each with who approved it and why.
9. **Coverage:** each lens per page: checked, `n/a` with why, `BLOCKED(env)` with why; the extended lenses not run.
10. **Graduation candidates:** journeys worth keeping as a page's browser test (`test_web_browser_<page>.py`).
11. **Environment:** commit, Chrome and Playwright, pixelmatch and the diff rules, platform, fonts, the grid, each
    segment's world (no tokens).

**Open items.** A finding that still needs work at the close (open at the cap, an improve recorded for the next run, a
question the maintainer deferred) becomes an open item: one per systemic cluster, carrying its findings; one per local
finding otherwise. Its body, written in the run folder, names the page, the state, the cell, what the owner
experiences, the fix idea and the run folder it came from; it cites shots by cell, never attaches them, and never
quotes a token. It is filed as an issue only with the maintainer's yes, or under an autonomy grant: `uv run gate
check-text <file>`, then `gh issue create --title "<title>" --body-file <file> --label area:web`, then `uv run devkit
board <n>`. A ratified wontfix gets no item.

## Live-vet: the developer drives

A lighter mode with no catalogue and no grid, for how a control or a flow feels. The developer drives the app on a
throwaway service (`serve.py <world> --link <page>`) and gives feedback; this session fixes and checks again live.

1. Number every item the developer gives, once, across the whole session, and ledger each in `STATE.md` with its
   round and state (queued, sent to the fixer, landed, re-checked yes or no). An item you disagree with is ledgered and
   argued, never dropped.
2. Batch a round's items into one fixer brief; fix at the shared part so every page using it inherits the fix.
3. Re-check live before handing back: measure (computed styles, element boxes, a short viewport), never trust "fixed".
4. If the developer doubts the whole approach, stop and compare with reference implementations before the next round.
5. On "it's good", sweep for sibling pages that should use the fixed pattern.
6. Close like a run: `build-ui`'s checks once per batch, the report in the run folder with the live-vet's coverage,
   and an open item for each item that did not land.

## Secrets and hygiene

- `e2e/state/` holds the service's record with its token and the owner's session: live credentials for a throwaway
  service that dies with its segment, still never copied out of the run folder or quoted.
- A state that shows a secret (a one-time sign-in link) carries `secrets: true`: its texts are suppressed in the
  manifest and its shots are never blessed.
- The worlds are invented (`ui_world.py`'s Ada Example, Ben Sample, Cleo Example); a real vault, message or recording
  never enters a run, not even as a shot.
- Before an open item is filed, `uv run gate check-text` runs over it; a hit blocks it.
