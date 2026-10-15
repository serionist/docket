---
title: "Add the Staging page: classify, cut, drop and promote in the browser"
issue: "#71"
status: draft
approved_by:
approved_at:
---

# Add the Staging page: classify, cut, drop and promote in the browser

## Goal

The user works through what waits at the staging gate in the browser: each record by source and age with why it
waits, opened to read, cut, classified work or private, and promoted, each act through the gate's own calls. Held
recordings are listed with the command that releases each at the owner's console; the release itself stays where
DR-0202 puts it (Release, below).

| Part | The issue's "done when" | This plan |
|---|---|---|
| List | pending and ready records by source and age, with kind, title and why they wait | as asked, with counts per source and state; the reason comes from the gate |
| Act | classify work or private, cut by time or characters, drop; the CLI's gate calls | as asked: `Gate.classify` for all three (private is the gate's drop), and promote through `Gate.promote` |
| Held | held recordings listed; a release asked through the browser's session, the code typed back | listed, with the commands; **no release in the browser**, a change to this row the lead is asked to agree (Open questions) |
| Safety | every write checks Origin and the session; nothing private shown before it is opened | the Guard on every post, as on every write; text only in an opened record; a fix for the `Origin: null` a same-origin post carries |

- In scope: `/staging`, its panel fragment and two post routes; the gate saying why each record waits; the held
  part; the shell's bits for a third page; tests over HTTP and a browser flow at desktop and phone width.
- Out of scope:
  - a release from any page or route, and a held recording's text in the page (`docket hold show` in a terminal);
  - a drop that leaves no hash in `_decided.jsonl`: the gate has none;
  - several records in one classification: the gate takes one ref a call (#5);
  - deleting a staged file a hold covers: the ingest's message names the files;
  - never-store lists and redaction rules, which are Settings' (#73);
  - `docket staging list` printing the reason.

## Design

### What it builds on

| From | What |
|---|---|
| #9, #72 | the shell, `page()`, the macros and tokens, the list-and-panel pattern and its keys, `templating.history` and `safely`, per-page keys, the HTML and a11y checks (DR-0170 to DR-0180, DR-0300) |
| #5, #10, #31 | `Gate.list`, `show`, `classify`, `promote`; staging's files (DR-0024); hold lists (DR-0121, DR-0122); the release log and the console's yes (DR-0200 to DR-0204) |
| #8, #41 | the Guard (DR-0152), the writer thread and the vault lock (DR-0153), a log with no content (DR-0155); `local.imported`, the folders `docket ingest` read (DR-0248) |

### Modules

| Module | Change |
|---|---|
| `vault/gate.py` | `Listing` gains each entry's reason, worked out in `list` under the lock from the sweep's own facts (holds, releases, `_decided.jsonl`, cut rids); nothing else changes |
| `web/staging_view.py`, new | `StagingState`, the URL read and written back; the list, the panel redacted at view, selections turned into cuts, the preview, the held part |
| `web/staging.py`, new | the four routes; every gate call through `service.writer.run` |
| `web/pages.py`, `web/templating.py` | Staging built, its keys; `page()` takes the referrer policy a page with a form needs |
| `templates/staging/`, `static/css/staging.css` | `page.html`, `_panel.html`; the `paste` macro moves from `sync/macros.html` to `macros.html` |

### The page

Conclusion first: what is ready to promote, then what waits for the user, then what is held.

```
+--------------------------------------------------------------------------------------------------+
| Docket  Today Queue Ledger Meetings Outbox | Search Briefs Recording | Staging Curate Sync Set.  |
+--------------------------------------------------------------------------------------------------+
| Staging                                         All  calls  memo        All  pending  ready      |
| 14 records wait at the gate: 11 for you, 3 ready to promote; 2 recordings held                   |
| READY   calls 2, memo 1                                       [Promote calls] [Promote all]      |
| WAITING                                       | recording  Supplier call             calls       |
|  calls                                        | 7 Oct 10:00, 31:12; waiting 2 days; mixed source |
|   recording  Supplier call  7 Oct  2 d  mixed |  [ ] 12:16-12:28  Ada Example: ...               |
|   recording  Weekly sync    8 Oct  1 d  mixed |  [x] 12:28-12:41  Ben Sample: ...                |
|   mail       Pallet labels  8 Oct  1 d  hand  |  [x] 12:41-12:55  Ada Example: ...               |
|  memo                                         |  also cut [ 30:02-31:12        ]                 |
|   note       Day rates      9 Oct  ready      |  [Preview]  [Work, with 2 cuts]  Private...      |
| HELD                                          |                                                  |
|  calls  rec-cut-01, rec-cut-03 (held again)                                                      |
|   docket hold release calls <id> --from ~/exports/calls --cut <mm:ss-mm:ss>        [copy]        |
+--------------------------------------------------------------------------------------------------+
| work-vault on laptop-1  -  4,213 chunks ...                     j k move  Enter open  ? keys     |
+--------------------------------------------------------------------------------------------------+
```

- The lead line counts what waits, what is ready and what is held.
- Ready: counts per source, a Promote button per source and one for all.
- Waiting: by source, oldest first; each record's kind (icon and word), title, time, how long it waited, its state
  and why. Each is a link that opens it in the panel.
- Held: per source, the held ids and the commands (Release, below).
- Files in staging that don't read: their path and the parser's first line, as `docket staging list` prints them.
- Each part has its empty state. No vault, or a hold list or release log that doesn't read: the `DocketError`'s
  message, as on Search and Sync.

### Why a record waits

The gate already knows; `Gate.list` now says it, so the page never guesses from a record's fields.

| Reason | When | The line |
|---|---|---|
| on hold | a hold list covers it | stops every ingest of its source; delete its file, or release the hold |
| held back | the last promotion held it | its problem |
| released | pending under a released recording | released with its cuts; classify what is left |
| by hand | an earlier version was classified private or cut | an earlier version was decided by hand |
| mixed | pending from a mixed source | work or private? |
| work | ready by its source's rule | goes at the next promotion |
| classified | ready, classified | classified work by you (or by Claude), with 2 cuts |

### What shows where

Titles and counts first; a record's text only once it is opened.

| Where | Shows | Never |
|---|---|---|
| the list | kind, title (one line, at most 80 characters), source, times, state, reason | text, speakers, recipients, `native`, the first words `staging list` prints |
| the list, a record on hold | key, rid, kind, time | its title (DR-0121) or an open link; `open=` with its key says it is on hold |
| the panel | rid, kind, title, source, times, state, who classified, cuts, problem; the transcript or the text; author, recipients, container, thread, attachment names; `native` folded; the version in raw that was cut before | |
| the held part | ids, "held again after its release", counts, the commands, this machine's import folders | anything from an import folder |

- Shown as promotion would write it: the panel runs the staged record through the redactor with the current rules,
  as `intake_view` does for `hold show`, so a rule turned on since intake applies in the page too, and a notice says
  so when it changes the record. The staged file stays as it is; promotion redacts again (DR-0025).
- Every value is autoescaped; nothing from a record goes through `|safe` or becomes a link; content gets
  `dir="auto"` (DR-0170).

### Cutting

A cut is chosen on what the panel shows, previewed, then classified with it.

| Record | Chosen by | Becomes |
|---|---|---|
| a transcript | a checkbox per utterance (`u`) | a time cut at that segment's start and end in milliseconds; neighbours made one by `cuts.merge` |
| a text | a checkbox per line (`line`), each line's first character shown beside it | that line's characters in the staged text; lines next to each other one cut, the line break between included |
| either | a typed range (`cut`), as `--cut` takes it | `cuts.parse` |

- The preview, a GET that writes nothing, cuts and then redacts, promotion's order: it names the utterances that go,
  including any an overlapping one takes along (`cuts.removes`), or shows the text with `[cut]` in place. The Work
  button names the number of cuts.
- Exact times, not `mm:ss` read off the screen: rounded to the second, a cut would take the neighbouring utterance.
- A text the current rules would join lines in (a match across a line break) takes no character cut in the page,
  since its line numbers would no longer be the staged text's; the panel names `docket staging show` and `classify
  --cut`. Time cuts aren't affected.

### Writes

Each act is the CLI's own gate call, run on the service's writer thread under the vault lock (DR-0153).

| Act | Route | Gate call |
|---|---|---|
| list, open, preview | `GET /staging`, `GET /staging/panel` | `Gate.list(source, state)`, `Gate.show(key)`; the preview's cut and redaction in memory |
| work | `POST /staging/classify`, `label=work` | `Gate.classify(key, "work", cuts, by="user")` |
| private, the drop | `POST /staging/classify`, `label=private`, from the confirm view | `Gate.classify(key, "private", by="user")`: the file deleted, its hash in `_decided.jsonl` |
| promote | `POST /staging/promote` | `Gate.promote(source)`, then the index updater poked |

- Reads go through the writer too: every gate call sweeps staging first, and the sweep deletes and rewrites files.
- Plain HTML forms, so a write works without JavaScript; each answers a 303 to the page with `done`, so a reload
  never posts again. Private asks once more on a view of its own (`confirm=private`).
- The routes read their urlencoded bodies with the standard library, at most 64 KB, and check them before the writer
  runs: the key 32 hex characters, the label, numbers in range. A bad body, or a `DocketError` (a record the CLI
  promoted meanwhile, a lock not given within 15 seconds), renders the page with its message and writes nothing.
- `classified_by` is `user` for every classification the page makes, as the CLI's default.

### Safety

The Guard already checks every write; the page adds no route it could skip.

| Check | Where |
|---|---|
| the Host; the Strict session cookie; Origin or Sec-Fetch-Site from this origin, and one of the two present | the Guard, on every post (DR-0152) |
| a page with a form sends `Referrer-Policy: same-origin` | `page()`. Under the floor's `no-referrer`, Fetch's origin rule makes a browser send `Origin: null` on a same-origin post, which the Guard refuses as cross-origin |
| no release from the web | #31's scan (only `cli.py` imports `sources.release`), and a new one: only `sources/release.py` calls `releaselog.add` and `holds.remove` |
| pages `no-store`, the CSP, no handler or inline script | `page()` and the HTML check (DR-0172) |
| nothing of a record in the log | route templates only (DR-0155); the gate logs counts |

With `same-origin`, our pages' URLs go as `Referer` to the service alone, which logs route templates only; another
origin still gets nothing.

### Release

The page lists held recordings and hands the owner the command; the release stays at the console (DR-0202).

What the issue asks, a code shown in the page and typed back there, stops nothing: reading a code off a page and
typing it is what a browser agent does in normal use, and Claude reaches a session through `docket open` or the token
(DR-0152). At the console it has to clear `CLAUDECODE` and fake a console on purpose, which the cuts skill forbids.
That gap is the point of DR-0202.

| Option | What stops Claude releasing | Verdict |
|---|---|---|
| the code shown and typed back in the page | nothing: Claude in Chrome or Playwright reads and types it in normal use; curl gets a session from `docket open` | no |
| the code in a desktop notification, typed in the page | a browser tool can't see it; a shell can read the notification history (Windows keeps it in a file the user's processes read), and a screen-reading agent sees it. Three platforms' APIs, and none on a headless Linux session | no: no stronger than the console check, more code, and it fails where nothing shows |
| the page asks; a console command shows the code (`docket hold confirm`) | the console check, as today | no: the console release with one more command and a pending request in the service's memory |
| the release at the console; the page lists and names the command | the console check, `CLAUDECODE` and the code (DR-0202) | **yes** |
| an OS prompt (Windows Hello, Touch ID, polkit) | a human-only factor | later: the strongest, for the console too; three native APIs, an issue of its own |

The held part, per source:

- the held ids from `state/holds/<source>.txt`, ids only; one the release log also has is marked "held again after
  its release";
- how many staged records a hold covers: they stop that source's ingests (the reason "on hold" above);
- `docket hold show <source> <id> --from <folder>`, to read one in a terminal, and `docket hold release <source> <id>
  --from <folder> --cut <mm:ss-mm:ss>`, to release it, each with a copy button; for one held again, the release
  without `--cut`, which only asks to take the hold off;
- `<folder>` filled when `docket ingest` read one folder for the source on this machine (`local.imported`), else
  those folders listed beside it;
- the words that hand the cuts to Claude: `Go through my held calls recordings with /docket:cuts.`

### The URL

| Parameter | What | Default |
|---|---|---|
| `source` | narrow to one source | all |
| `state` | `pending` or `ready` | both |
| `k` | how many records; Show more adds 50, up to 1,000 | 50 |
| `open` | a staged record's whole key, open in the panel | none |
| `u` | utterances of the open transcript to cut, numbered from 1: repeated, each one or a range (`u=4-9`) | none |
| `line` | lines of the open text to cut, the same way | none |
| `cut` | a typed cut, as `docket staging classify --cut` takes it; repeated | none |
| `confirm` | `private`: the drop's own question | none |
| `done` | what the last post did, ids and counts only (`work-3f9a1c2e4b5d`, `promoted-12-1`) | none |

- One canonical form: this order, defaults left out, repeated values sorted; `u`, `line` and `cut` only with `open`;
  `done` never in a link the page makes.
- A malformed value is the page's own error, status 400; a key nothing is staged under any more (promoted, dropped,
  swept) says so, status 200; unknown parameters are ignored, and parameters only gain.
- `/staging` is always a whole page, `/staging/panel` always the panel, which names the canonical URL by DR-0170's
  rule through `templating.history`. A record's link has the page as `href` and the panel as `hx-get`, as on Search.

### Look, keys and phone width

- Search's list and panel: under 900 px the panel covers the list; under 600 px records and utterance rows stack,
  the checkbox first, and buttons and commands wrap with their copy buttons below. No wide tables.
- Search's key handlers, no new JS: `j` and `k` between records (their links are `hit-link`), `Enter` or `o` to open,
  `Esc` to close, `?`. No key writes.
- Colour never alone: every state and reason has its word.

### Decided

Taken under the autonomy grant; the Entry column names the decision each folds into.

| Decided | Not taken | Entry |
|---|---|---|
| the release stays at the console; the page names the command | a code typed back in the page, a desktop notification, a confirm command (Release) | DR-0382 |
| titles in the list, the text only once opened | titles hidden until asked: the list is for telling records apart; the first words `staging list` prints: content before a click | DR-0380 |
| a record on hold shows key, rid, kind and time, and doesn't open | its title: DR-0121's bind | DR-0380 |
| the reason from `Gate.list` | worked out in the page from fields: holds, releases and hand decisions are the gate's facts, read under its lock | DR-0380 |
| shown redacted by the current rules, as promotion writes it | the staged text as `staging show` prints it: a rule turned on since intake wouldn't apply | DR-0383 |
| utterance and line checkboxes at exact positions, and typed ranges | times read off the screen: the neighbouring utterance goes too; a mouse selection: JavaScript, and offsets the redacted view doesn't keep | DR-0383 |
| plain form posts, answered with a 303 | htmx posts: nothing works without JavaScript | DR-0381 |
| every gate call, reads too, on the writer thread | reads beside it: every gate call sweeps staging, which writes | DR-0381 |
| the Guard's checks and the Strict cookie, no form token | a token per form: the Origin rule and the cookie already refuse another site's post | DR-0381 |
| `Referrer-Policy: same-origin` on pages with a form | the Guard taking `Origin: null` with `Sec-Fetch-Site: same-origin`: a looser check on every route | DR-0381 |
| bodies read with the standard library | `python-multipart`: the package has it only through the MCP SDK | DR-0381 |
| promote on the page | promote only in the CLI: the page would stop halfway | DR-0381 |
| drop is classify private | a drop of its own: the gate has none, and one without the hash is asked again on the next pull | DR-0381 |
| private asks once more | one click: a work record dropped for good by a slip | DR-0384 |
| a list-and-panel page with Search's keys | a page per record: back and forth for each of a backfill's records | DR-0384 |
| `k` of 50, Show more adds 50 | everything at once: a backfill's hundreds of rows | DR-0384 |
| `paste` moves to `macros.html` | a copy per page | - |

## Doors and data

No one-way door changes. Named because the plan touches them:

| Door | What this plan does |
|---|---|
| the release's yes (DR-0202) | recorded `medium` and not on AGENTS.md's list, but it guards a one-way act: what a release lets in reaches raw, every clone and git history, and only `docket forget` (phase 2) and a history rewrite take it back. This plan treats it as a door and leaves it shut: no route releases, and a scan keeps the web away from the release's writers |
| staging's files (DR-0024) | read and written only through `Gate.list`, `show`, `classify` and `promote`; the format doesn't change |
| hold lists (DR-0121), release logs (DR-0200) | read only; a record on hold shows no title |
| the MCP tools (DR-0150, DR-0241), the plugin's names | unchanged; no tool added |

- Users' data: nothing new on disk. Records the page classifies say `classified_by: user`, as the CLI's default.
  Nothing migrates.
- The page's URL isn't a door: parameters only gain, as Search's and Sync's do.

The decisions, `proposed`, by Claude (autonomy grant), added with this plan in `packages/docket/index.d/`:

| Id | Decision | Cost |
|---|---|---|
| DR-0380 | The Staging page lists what waits at the staging gate by source and age, and shows a record's text only once it is opened | cheap |
| DR-0381 | The Staging page writes only through the staging gate's calls, on the service's writer thread, as same-origin form posts | medium |
| DR-0382 | The Staging page lists held recordings and names the commands that release them at the owner's console; no page or route releases | medium |
| DR-0383 | A staged record shows in the page as promotion would write it, and is cut by its utterances, its lines or typed ranges | cheap |
| DR-0384 | The Staging page's URL holds source, state, k, open, u, line, cut, confirm and done | cheap |

## Steps

Each step builds and passes `uv run gate` on its own.

1. The gate's reasons. `Listing` gains each entry's reason, filled in `Gate.list` under the lock. In `test_gate.py`: a
   record for each reason gives it; `test_cli.py` passes unchanged.
2. The view. `web/staging_view.py`. `test_web_staging_view.py`: `StagingState` written back canonical, its errors,
   `u`, `line` and `cut` dropped without `open`, unknown parameters ignored; utterances 2 and 3 become one cut at
   their segments' exact milliseconds; two lines next to each other one character cut, two apart two; a typed `cut`
   beside checked utterances merged; the redacted view and its notice; the lines-joined refusal; the held part's
   folder filled for one import folder and listed for two; each reason's line.
3. The shell. Staging built in `pages.py` with its keys; `paste` moved to `macros.html`; `page()` takes the
   referrer policy. `test_web_shell.py` (counting from `pages.NAV`) and the Sync page's tests pass unchanged.
4. The routes and templates. `web/staging.py`, `templates/staging/`, `staging.css`. `test_web_staging.py` (below);
   the Staging states join the CSP, HTML and a11y lists in `test_web_shell.py` and `test_web_a11y.py`.
5. Safety. The post cases in `test_service_security.py`; the release-writer scan in `test_release.py`.
6. The browser. A Staging flow in `test_web_browser.py` (below): the only test that sees what a real browser sends
   as `Origin`.
7. The docs below, then `finish-work`: `uv run gate full`, a draft PR, `review-code` until it is clean.

## Tests

On a temp vault from the test kit with a mixed `calls` source and a work `memo` source; records made with
`make_record` (a transcript of Ada Example and Ben Sample with two overlapping segments, a five-line mail) and staged
through `Gate.stage`; a hold list, a release line and `folders.json` written through `holds`, `releaselog` and
`local`; a phone number from the fictional +1 555 01xx range. Temp config and state folders, the failing keyring,
`DOCKET_VAULT` unset, TestClient or port 0, never the running service.

| File | What it proves |
|---|---|
| `test_gate.py`, `test_web_staging_view.py` | steps 1 and 2 above |
| `test_web_staging.py` | signed in over HTTP: pending and ready by source, oldest first, with titles, and no text, speaker or `native` string in the list; `source=`, `state=` and `k=` narrow; a transcript opens with its utterances and a mail with its lines and offsets; the raw version cut before; a record on hold has no open link, and `open=` with its key says it is on hold without its title; a phone number staged before the phone rule was turned on shows `[redacted:phone]` and the notice, while the staged file still holds it; the preview of `u=2&u=3` names those utterances and the one an overlap takes along; a post of work with `u` answers 303 with `done`, and the staged file is ready, `classified_by: user`, its cuts the segments' milliseconds; private through its confirm view leaves no file and one line in `_decided.jsonl`, and neither the response nor the service log holds the record's title or text; promote writes and commits a run and pokes the updater; a record held back shows its problem; the held ids per source, held again marked, the commands with `--from` filled, the words to paste, and no title of a held recording anywhere (one planted in the import folder); a hold list with a bad line gives the page's error naming its line; a key the CLI promoted between the page and the post, and a vault lock held past a short patched wait, each give the page's error with nothing written; `<script>`, `<img onerror>` and a `javascript:` URL in a title and a text render escaped; GET on the post routes answers 405 |
| `test_service_security.py` | posts to `/staging/classify`: without a session 401; `Origin` of another site 403; `Sec-Fetch-Site: cross-site` 403; neither header 403; `Origin: null` 403; this origin 303 |
| `test_release.py` | the scan: `releaselog.add` and `holds.remove` are called only in `sources/release.py`, beside the scan that only `cli.py` imports `sources.release` |
| `test_web_shell.py`, `test_web_a11y.py` | the Staging states under the CSP, HTML and a11y checks; its keys dialog lists `j`, `k`, `Enter`, `o`, `Esc` and `?` |
| `test_web_browser.py` | behind `DOCKET_TEST_BROWSER`, the real service on port 0: open a pending transcript, check two utterances, Preview, Work, and the record shows ready, so a real browser's post passes the Guard; Promote empties the list; no console error; at 390 by 844 px no horizontal scroll and the Work button reachable; screenshots at both widths |

- Coverage stays at the `docket` floor of 100 %.
- No test writes outside its temp folders.

## Docs

- DR-0380 to DR-0384, added with this plan.
- `docs/design.md`, the web app section: the Staging row becomes "records waiting for the gate: classify, cut, drop,
  promote; held recordings listed with the command that releases each at the owner's console", and a bullet on the
  page with its ids.
- `packages/docket/README.md`, the web app section: the Staging page, its URL, and that releases stay at the console.
- `CHANGELOG.md` under Added: the Staging page.
- Glossary and `AGENTS.md`: nothing, unless the second open question is answered yes, in a change of its own.

## Risks

| Risk | Status |
|---|---|
| Claude drives the page (Claude in Chrome, Playwright, curl with a session) to classify, drop or promote | accepted: the CLI gives it the same calls; the page records the user, as the CLI's default `--by user` does |
| Claude drives the page to release | resolved: no route releases; the command it can copy is refused in its shell (DR-0202) |
| a pending record's title says what a private recording is about | accepted: the list needs something to tell records apart, as `staging list` shows; the text waits for a click |
| a same-origin post carries `Origin: null` and the Guard refuses it | resolved: pages with a form send `same-origin`; the browser flow posts through a real browser |
| a slip on Private drops a work record for good | resolved: the drop asks once more on a view that names the record |
| the rules changed since intake, and the staged text holds what they now redact | resolved: shown redacted, promotion redacts again; character cuts off where lines join |
| the page and the CLI act on one record at once | resolved: the vault lock; a record gone meanwhile is the page's error, nothing written |
| a long CLI promotion holds the lock | accepted: the page waits 15 seconds, then says so |
| a backfill of hundreds of transcripts makes every load read every staged file | accepted: as `staging list` does; `k` bounds what renders |
| the owner wanted the release in the browser | open: the first question below |
| #73 changes `pages.py` and the shell at the same time | accepted: the tests count from `pages.NAV`; whichever merges later rebases |

## Open questions

For the lead, each with the recommendation.

| Question | Recommended | Why |
|---|---|---|
| The issue's Held row: amend it to "held recordings listed, with the command that releases each at the owner's console"? | yes | a code in the page is no check against a browser agent (Release) |
| Should the console's yes (DR-0202) join AGENTS.md's doors, so no later page or tool loosens it under the autonomy grant? | yes, in a harness change of its own | the act it guards can't be undone; DR-0012 already holds a looser check for the maintainer, but the list is where reviewers look |
| Promote on the page, which the brief has and the issue doesn't? | yes | without it the user classifies here and promotes in a terminal |
| A held recording's text in the page, to pick its cuts there, the release still at the console? | later, an issue of its own, after the console path has had a week of use | a third place that prints held text (DR-0204), and a folder mapped by the service |

## Plan review

Not reviewed yet.
