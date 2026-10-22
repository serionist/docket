---
name: build-ui
description: Use when building or changing a page, a shared part, a style, a route, or a read or write of Docket's web app under packages/docket/web ("move Sync to the React app", "add a filter to the Queue", "fix this dialog", "a new shared part"). Holds the bar a page meets before check-ui (its states, writes, typed text, focus, keys, widths, and what its reads may send), how data reaches a page, where the stack's rules live, the rule for shared parts, and the traps the React move already paid for.
---

# Build UI

Build every page and shared part of the web app to the bar `check-ui` and `ui-audit` check, so `check-ui` finds
little and an audit has nothing left to fix. The app is React and TypeScript with shadcn/ui on Radix and Tailwind CSS
4, built by Vite with npm, served by the service from its committed build (DR-0703, DR-0705). This skill holds the
method and the bar; the stack's rules are the decisions and conventions on `packages/docket/web`, and a line here that
disagrees with one of them is wrong.

## Touch points, in order

1. **Read the door:** `uv run docs door packages/docket/web` and each folder the change touches, `uv run docs list
   decisions --path <path>` (then `show` each one the change touches), CV-0015, and `CONTRIBUTING.md`'s web app section.
2. **Text of record:** what the page shows, in which states: its rows, kept behaviours, "Built" notes and Review
   sections in `docs/plans/2026-10-20-252-react-app.md`, its row in `docs/design.md`'s web app section, and its
   decisions. The links Claude and the plugin's skills give into the app (`plugin/skills/tasks` opens `/queue`,
   `plugin/skills/decision` links `/briefs?brief=brief:<subject>`, Search's hits and the Recording page's `#at`) are
   part of it. Where these are silent, ask the maintainer; never invent a page's content.
3. **Design it** with the `frontend-design:frontend-design` skill (DR-0712), on the tokens and the shell (DR-0707,
   DR-0708). `/design` shows every shared part on invented data.
4. **Scan what exists** before writing a piece: `src/components/` (the shell, `doc`, `app-link`, `who-box`,
   `date-picker`, `stamp`, `kind`), `src/components/ui/` (shadcn's), `src/lib/` (`api`, `query`, `poll`, `drafts`,
   `keys`, `focus`, `nonce`, `prefs`, `search`, `session`, `build`, `dates`, `who`), and `src/pages/day/`, the frame the
   day pages share. Search for the behaviour you need, not a name: what is there is used, what is close is extended.
5. **Look APIs up** with Context7 (`.mcp.json`) before coding against React, TanStack Router or Query, Tailwind,
   shadcn/ui, Radix, Vite or Vitest; ask the language server about a symbol first.
6. **Wire the data** and **build to the bar**, both below.
7. **Test what behaves:** a Vitest with Testing Library for every user-reachable conditional; the route in Python
   (`test_web_<page>.py`); layout, sizing, focus, axe in both themes and the CSP in a real browser
   (`test_web_browser_<page>.py` on `browser_kit`), since jsdom passes them all; the 0.25 s budget as a `perf` test,
   `nightly` when it builds a big vault; every new read walked by `test_web_reads_hide.py`. A new test file goes in
   `tools/devkit/test-systems.json`.
8. **Run `check-ui`** on every page the change touches, and fix what it finds. A cosmetic fix is proven by its shot.
9. **Build and check once per batch:** after a change to an `/api/web` model, `uv run devkit web-schema` first; then in
   `packages/docket/web` `npm run lint && npm run typecheck && npm test`, `npm run build` (the build is committed with
   the change that made it), `npm run budget`; then `uv run gate` and the touched test files one at a time. Then
   `finish-work`, whose `gate full` runs the rest.

## Data

- **One way in:** reads and writes go through `/api/web` only, with the client typed from `openapi.json`
  (`src/lib/api.ts`); never a hand-written `fetch` or type. The session cookie only; a write takes a JSON body only
  and runs on the writer thread through the call the CLI makes (DR-0709).
- **A write's body** is a Pydantic model with `extra="forbid"`, naming exactly what the write needs.
- **A write on something that can move** (a decision, a staged record, an entity file) carries the rev or hash the
  page showed; a moved one answers 409, the page says what changed and reads itself again, typed text kept (DR-0781,
  DR-0785, DR-0786). A 400 reads nothing again: the text stays beside the writer's words.
- **What a read sends** is what its page shows, and nothing it hides. JSON sends the whole object even where a template
  only chose not to show a part: a held record's body, a cut stretch's words, a never-store value, a stoplist key's
  hash stay on the server (DR-0380, DR-0382, DR-0786). A new read joins `test_web_reads_hide.py`'s walk in the same
  change, and a refusal names no count or shape of what it refused.
- **Values ready to show:** times, durations and snippets come from the server as text and mark segments (DR-0709); the
  app formats no date but the date picker's.
- **Query:** in memory, no refetch on focus or reconnect, `staleTime: Infinity`, no retry on a 4xx; the 5 s poll is
  the only refetch (DR-0711). A write answers the version its page reads.
- **Failures:** the answer's own words (`HttpError`), shown as the writer wrote them; a 401 is the shell's session
  dialog, in one place (`lib/session.ts`), never per page.
- **The URL** is the page's state as a plain query string, encoded with the one `URL_SAFE` (`src/lib/search.ts`,
  `docket/web/urls.py`); the server's canonical string is written back with `router.history.replace`, never
  `navigate({ href })` (DR-0704). Parameter names only gain optional ones, so Claude's links open the same state byte
  for byte.
- **A cache a read adds** on the server is bounded and keyed by everything its answer depends on: the commit it read,
  never a HEAD read twice; the vault's folder; the bytes.
- **The browser keeps** the theme and keys cookies only (DR-0706): typed text never goes into localStorage or
  sessionStorage.

## The bar a page meets before check-ui

- **States:** loading that says what is loading, in the final layout; the empty flavours: truly empty (a new vault),
  filtered (no matches, and the way out), waiting (an index still being built, asked again every 5 s), and a failure
  that never poses as empty, with a way to ask again that keeps the URL's state; an absent thing said with why (no
  audio stored, a source never synced).
- **Writes:** each button one batch with its notice and Undo (DR-0710); a bulk through a dry run naming the skipped
  rows; the confirmed answer shown, never an optimistic flip; a refusal in the writer's words, the text as typed.
- **Typed text:** saves on change; unsaved text asks before it is dropped (the router's blocker and `beforeunload`,
  `lib/drafts.ts`); a read that answers later never replaces a panel that holds typed text; the poll holds while the
  owner types, ticks, or has a dialog open (DR-0711).
- **Busy:** a waiting button keeps its place and its focus (`aria-busy`, `aria-disabled`), never a native `disabled`
  on the focused control.
- **Focus:** a view swap moves focus into the new content; a dialog focuses its default action (Cancel on a
  destructive confirm), closes on Esc and gives focus back to its trigger or to what had it (`lib/focus.ts`); an Esc a
  popover took never also closes the panel. A control the owner can't use stays focusable, its reason as text tied by
  `aria-describedby`.
- **Keys:** through the one key layer (`lib/keys.ts`), each key running its button's command and listed in `?`; every
  flow works without a mouse with the focus ring visible; keys ignored in fields and while the keys switch is off.
- **Widths:** desktop first, but every page is shot at 1440x900 and 390x844, so the phone is a bar every page meets:
  44 px targets on a coarse pointer, 24 on a fine one; the shell's folds at 1280, 1024 and 768 (DR-0708), the notice
  and its Undo at the panel's foot where the panel covers the list; one scroller, no sideways scroll; strings 30 %
  longer still fit; `dvh`, never `vh`.
- **Look:** the tokens only (`src/styles/tokens.css`), both themes; in-text links underlined, never told apart by
  colour alone; headings in order.
- **CSP:** no inline script or style; a Radix part that inserts a style element takes the nonce (`lib/nonce.ts`,
  DR-0706).
- **Loading:** the first page's script stays under 250 KB gzipped (`npm run budget`); a page that would push it over
  is a chunk loaded through `freshPage` (`lib/build.tsx`), which asks the build's stamp first and loads the page whole
  when the service serves another build (DR-0784).
- **What no page does:** release a held recording, forget, or turn credentials redaction off: those stay at the
  owner's console, and a page names the command (DR-0382, DR-0577, DR-0601).
- **Data:** invented only, in tests, worlds and shots (CV-0006).

## Shared parts

shadcn/ui's components, copied into `src/components/ui/` by the pinned CLI (DR-0713), are the component layer:
rebuilding one is a defect. A shared part of our own needs, in the same PR:

1. a call site today: the page in the text of record that needs it;
2. the gap in shadcn/ui or Radix it fills, cited from their docs;
3. a doc comment naming the gap, its Vitest, and its place on `/design`.

"We might need it" is no reason, and "nicer than shadcn's" is a defect. A part one page owns that a second page needs
moves to `src/components/` or `src/pages/day/`; it is never copied.

## Never

- A literal colour or size outside the tokens, or a style override CV-0015's lint doesn't allow.
- `window.confirm`, `alert` or `prompt`.
- A hand-edited `src/api/schema.d.ts`, or a hand edit in the committed build.
- A page, route or button that releases, forgets or turns credentials redaction off.
- Typed text in browser storage, or a real person's data anywhere.

## Traps

| What broke | When | What it cost | The guard |
|---|---|---|---|
| TanStack Router's default search turned values into JSON, which broke Claude's links | the Ledger's move, #252 | links that opened another state | our `parseSearch` and `stringifySearch` with `URL_SAFE`, the canonical string written back; a Vitest round-trip byte for byte |
| Query's defaults would refetch on focus beside the poll | the Ledger's move, #252 | reads twice, a stale page jumping | no focus or reconnect refetch, `staleTime: Infinity`, no 4xx retry; a Vitest on the defaults |
| A save adopted its own version, which already held a CLI write that landed while the poll held | the Ledger's review, #252 | another writer's change never shown | a save answers the version from before its write too; when it isn't the page's, the page refetches |
| A write answered the op files' version while the page read more, so every save refetched and the poll refetched every 5 s | Today's review, #252 | a page that never sat still | each write answers the version its page reads |
| A read under a new key had no cache, so the panel unmounted while it came and dropped typed text | Meetings' review, #252 | lost text, no prompt | the same row's panel stays until its read answers; a Vitest types, asks, and finds the text |
| Enter in the who field took the top name, not the one typed | the Queue's review, #252 | the wrong person on an item | the typed name ranks first; nothing marked with no text (DR-0714) |
| An Esc that closed the date picker also closed the panel | the Ledger's move, #252 | the owner lost the panel mid-edit | the key layer leaves a key whose default a layer prevented |
| On a phone the month opened above the screen; from 768 to 1023 px Undo sat under the panel | the Ledger's and Meetings' reviews, #252 | controls nobody could reach | a sheet from the foot below 768, the popover capped above it; the notice at the panel's foot |
| The router's own hash scroll ran before the read came, so a played line jumped back | Search and Recording's review, #252 | the wrong line under the owner's eye | the router's hash scroll off; the page scrolls once its read answers |
| A non-canonical `lines=15-15` lost the row's selection when the server's URL came back; two rows shared a key | Search's move, #252 | `j` looping, a selection dropped | write the canonical form; key rows by place as well as locator |
| A pick's refusal stayed above another entity's decisions after the page moved | the Briefs page's review, #293 | a refusal shown against the wrong thing | a refusal goes with what it was about |
| Caches held a commit's whole listing per rev, keyed a history by a HEAD read twice, and shared one entry between two vaults | the Briefs page's review, #293 | hundreds of MB in a long-running service; another HEAD's answer | bounded caches, keyed by the commit, the vault's folder and the bytes |
| A held record's panel ran its cuts and preview, so the JSON sent its body; a stoplist key came with its hash | the Staging and Curate review, #297 | what the Jinja page only hid, sent to the browser | build a held record's panel from its key alone; no hash for a key that doesn't lift; `test_web_reads_hide.py` walks every read |
| A refusal of a held record counted its lines before refusing | the Staging and Curate re-check, #297 | the shape of what it hid | refuse the hold first, the same words for every selection |
| A tab open across an upgrade fetched a new chunk against its old `app.js` | the Staging and Curate review, #297 | a missing export, a page that never opened | `freshPage` asks the build's stamp before a chunk (DR-0784) |
| A Radix part's style element had no nonce, refused by the CSP | the shell, #252 | a Select with no styles | `useNonce()` for every inserting part; a browser test opens a Select |
| Buttons drawn from a link lost their border | Search's first round, #252 | controls that read as text | merge the button classes through `cn`; only shots catch it |
| A native `disabled` on the focused busy button threw focus to the page | another web app | keyboard users lost their place | `aria-disabled` on a busy button; axe per state |
| One state served loading and empty, so a slow load said "nothing here" under the spinner | another web app | a page that claimed nothing existed | a loading state of its own; the slow-network lens |
| A retry cleared the error flag without asking again | another web app | blank rows for good | the failure lens checks a request went out |
| Layout passed in a simulated DOM and broke in every browser | another web app, a whole phase | a broken table shipped | layout and sizing tested in Chrome, never in jsdom |
| A check that could not fail: a budget matched nothing, a dead locator stopped the suite testing | another web app, four times | green checks over untested pages | each new check is shown failing once on a planted fault |

## Done means

- The page matches its text of record in every state, and the bar above holds.
- `check-ui` ran on every changed page; its coverage table is in the PR, every "not checked" row named.
- Every new shared part has its call site, its cited gap, its tests and its place on `/design`.
- The build is committed and matches its sources, `uv run gate` passes, and `finish-work` follows.
