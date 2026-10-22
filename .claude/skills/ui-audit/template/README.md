# ui-audit runner (`template/`)

The Playwright capture core of the UI audit, for Docket's web app. A run copies this folder to
`.local/ui-audit/<run>/e2e/`, copies `package.template.json` to `package.json` and `config.example.json` to
`config.json` there, and adds its generated parts. Any tool can use it; the Claude Code tiers only orchestrate it.

## Layout

```
runner.mjs             check, and run one segment: start the world's service, identify it, sign in, run the parts
serve.py               the throwaway service: devkit ui's own, over a world of ui_world.py; also check-ui's
lib/docket.mjs         the service's record, identify(), the sign-in link, the battery's B9 links
lib/connect.mjs        a headless Chrome page: vitals and CSP watch, fixed clock, Recorder, CDP session; touch contexts
lib/capture.mjs        one state across the grid: two shots per cell, the Tier 1 checks, geometry read back
lib/battery.mjs        the invalid-input battery B1 to B10, from a form's spec
lib/manifest.mjs       the Recorder and the per-pass JSONL manifest with its budgets
lib/pixeldiff.mjs      the run-local diff against screens/baseline/
lib/baseline-diff.mjs  the blessed baselines in .local/ui-audit/baselines/<page>/ (diff, bless, list), never committed
lib/failures.mjs       a failed step's evidence, then carry on
lib/passlog.mjs        each journey file's shots block, from the manifest
lib/part.mjs           what every part shares: openPart(), part.journey(), shoot(), battery(), faults
lib/util.mjs           helpers and the battery's probes
package.template.json  the runner's package.json, kept under another name
config.example.json    every key, with comments
journey-format.md      the journey file format the parts are translated from
```

The run adds: `package.json`, `config.json`, `parts/<nn>-<name>.mjs` (thin sequencers: open the part on its world,
run the journeys), `parts/journeys/j-nn.mjs` (one module per journey, `run(ctx)`), `parts/00-smoke.mjs`, and `state/`
(the service's record and the owner's session). Parts run with `e2e/` as their working directory.

## Install

```bash
cp package.template.json package.json
cp config.example.json config.json
npm install --ignore-scripts
node runner.mjs check
```

Pinned: Playwright 1.64.0 (the Python tests' version), axe-core 4.14.0 (the web app's), pixelmatch 8.0.0, pngjs
7.0.0; Node as `packages/docket/web/.nvmrc` says. The browser is the machine's own Chrome (`config.browser.channel`),
so nothing is downloaded. The run folder gets its own `package-lock.json`, which stays there. A Chrome or Playwright
update changes the rendering, so blessed baselines made before it show as a platform mismatch and are re-blessed once.

`node runner.mjs check` proves the plumbing end to end: the config, the packages, `uv` and Chrome; then a throwaway
service on `checkWorld` (`empty`) started and identified, the owner signed in, the shell and the fonts found, a
stranger refused with the 401 sign-in page that names no vault, and the service stopped with its port closed.

## Running

```bash
node runner.mjs run --world week --parts 00            # the smoke, before any drive
node runner.mjs run --world week --parts 01,02         # one segment: the week world's parts
node runner.mjs run --world empty --parts 03 --run p2
node runner.mjs run --world briefs --parts 05 --journeys J-11   # a write nothing undoes, on a world of its own
```

- **A segment** is one world: the runner starts `serve.py <world>`, identifies the service, signs the owner in, runs
  the parts and stops the service, its sessions and its folders with it. The next segment starts on a fresh world, so
  a world a write changed for good is rebuilt by starting again.
- **Sentinels:** a part that exits 0 leaves `parts/<nn>.done`; anything else `parts/<nn>.failed` (send it to the
  script doctor; never judge its stale shots). Journey failures inside a part are in `failures/`.
- **Watchdog:** every `runner.watchIntervalS` the runner touches `parts/.heartbeat` and asks the service's
  `/api/health`; `envFailThreshold` misses in a row, or `serve.py` ending, write `ENV-DOWN` and stop the run.
  Environment failures never become findings; `service.log` says why.
- **Liveness:** `.runner.pid` while it runs and the heartbeat's mtime.

## The service, and the guard before every step

`serve.py` runs `devkit.ui.service`: the world built by `packages/docket/tests/ui_world.py` (`empty`, `week`,
`meetings`, `today`, `stale`, `search`, `recording`, `briefs`, `staging`, `curate`), then `docket serve` on port 0
with config, state and models folders of its own in a `docket-ui-` temp folder, a file token and the failing keyring.
It writes `state/service.json`: the port, the `localhost` origin, the world, the vault, the service's pid, its machine
(`ui-box`) and the token. Before any segment and again as each part opens, `identify()` in `lib/docket.mjs` refuses
unless the port is not 8741, the origin is `localhost` on that port, the vault sits in a `docket-ui-` folder, and
`/api/status` answers the record's token with the record's port, pid, vault and machine. The owner signs in through a
one-time link on `localhost`, never 127.0.0.1, where the owner's own session cookie lives.

For a live pass by hand or through the MCP:

```bash
uv run python .claude/skills/ui-audit/template/serve.py week --record .local/check-ui/service.json --link /queue
uv run python .claude/skills/ui-audit/template/serve.py link .local/check-ui/service.json /queue   # another link
touch .local/check-ui/service.json.stop                                                           # stop it early
```

It holds the service for `--minutes` (30 by default), until the stop file appears, or, with `--stdin`, until stdin
closes; then it stops the service and removes its folders.

## Faults, without a fault hook

The service has no fault hook; route interception stands in, and every capture taken while one is on names it in its
flags. In a journey, `faults` from `part.journey()`:

| Call | What |
|---|---|
| `faults.fail('**/api/web/queue**', 500)` | a read or write answers that status with an error body |
| `faults.slow('**/api/web/**', 2000)` | every matching request waits first |
| `faults.down()` | every `/api` request is refused, as when the service is gone |
| `faults.lapse()` | the session cookie is cleared: the next read answers 401 |
| `faults.clear()` | all off; done for you at the journey's end |

TanStack Query asks a failed read again three times, about 7 s, before the page shows the failure: wait it out
before the shot. A touch cell opens the page afresh in a context of its own, with the same faults put on it (`prepare`), so it
shows that retry window, not the failure: shoot a failure state `canonical`, and its phone cell in a part that opens
the page in a touch context and waits.

## Shots

`screens/<pass>/<journey>/<state>-<width>-<theme>-<lang>[-clip].png`

- `-clip` is the viewport, the judges' main surface; without it, the full page.
- `<width>` 1440 is the full desktop, 1280 the narrowest full layout, 390 the phone (the grid); opt-in cells are
  `1280x600` (short), 1024 (fold) and 900 (tablet), a width a grid viewport already has written `<width>x<height>`;
  `<theme>` light or dark; `<lang>` en.
- `<state>-aria.txt`: the ARIA snapshot of the canonical cell; `<base>-scrollN-clip.png`: steps through the largest
  inner scroller.
- `screens/baseline/<journey>/<same name>`: the last verified render of each cell.

## The capture contract (the smoke asserts every line on real values)

- **Geometry:** each clip is read back at `width x dpr` by `height x dpr`; a touch cell must render `pointer: coarse`
  (and `coarseProbe`'s control at its coarse size). A mismatch refuses the cell (`flags.refused`), never records it.
  Touch cells shoot in a fresh context made with `isMobile` and `hasTouch`: CDP touch emulation alone flips
  `matchMedia` but not the rendered CSS.
- **Shots through CDP** (`Page.captureScreenshot`): `page.screenshot` ignores the device-metrics override.
- **Settle** before every shot: network idle, two frames, animations finished.
- **The CSP:** nothing the capture adds is an inline style or script. Masks paint through the CSSOM and axe goes in
  through the CDP, so every CSP hit in the manifest is the page's own.
- **Overlays** are opened and awaited (`config.overlaySelector`: Radix's open dialog, alert dialog, menu or listbox,
  and an inner element) or the step fails with evidence. An open overlay is shot in place, at the canonical cell;
  `capture()` never reloads under it.
- **Post-write states** pass `assertSelector` (the notice, `role=status`, or the refusal, `role=alert`): no marker, no
  shot (a `capture-skip` line).
- **Signed-in states** pass `requireAuthed`: shooting the sign-in page as the target is refused.
- **Theme:** the `docket_theme` cookie and the document's `data-theme` are set to follow the system for the grid and
  restored after.

## The manifest (`manifest.<pass>.jsonl`)

One line per capture, battery case or failure: `run`, `part`, `journey`, `step`, `state`, `url`, `action`
(`capture:`, `capture-skip:`, `battery:`, `FAILURE:`), `consoleErrors`, `failedRequests`, `texts` (suppressed for a
secrets state), `shots`, `clipShots`, `axe`, `contrast`, `csp`, `sideways`, `hitTargets`, `overflow`, `rawKeys`,
`bannedText`, `vitals`, `ariaSnapshotRef`, `budget` (`consoleNoise`, `networkNoise`, `foreignRequests`,
`vitalsHits`: only what the config does not expect), `ms`, `flags` (`canonicalCombo`, `prePaintError`, `suspect`,
`geometry`, `refused`, `errors`, `axeBlocked`, `inPlace`, `secrets`, `faults`, `battery`), `ts`.

It is the evidence log, not the ledger: journey files and `journal.md` are the source of truth. Tier 1 rows are
findings already; judges read them and never hunt them again.

## Harvest

At the close, generic improvements to `e2e/lib/*.mjs`, `runner.mjs`, `serve.py` and this README come back into
`template/`, in their own reviewed change. Never `package.json`, `config.json`, `state/`, `failures/`, manifests or
shots.
