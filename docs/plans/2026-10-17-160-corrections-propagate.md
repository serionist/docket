---
title: Flag the lines built from a doc line that changed
issue: "#160"
status: approved
approved_by: claude, the lead (autonomy grant)
approved_at: "2026-10-17"
---

# Flag the lines built from a doc line that changed

## Goal

A derived line says which doc lines it was built from. When one of those lines changes in a later rev, the derived
line is flagged until its doc's next rev cites the line afresh or drops the ref (design §3.3). Today lines anchor only
to raw, so a corrected recording note leaves every brief and decision built on it saying the old thing, and nothing
shows it.

- In scope: a doc-line reference in the anchor grammar, what doc_put checks and pins, redaction that never touches an
  anchor run, the `derived_from` table in the index, the flag and clear rules, flags in `read` and `verify`, `docket
  doc stale`, a count at session start, and the `correction` doc that a verify mismatch becomes.
- Out of scope: briefs (#161) and decisions (#162), which are the first docs to cite lines; ledger items (phase 3); a
  machine judging whether a line matches its raw, which stays Claude's call (DR-0104).

## Design

### A line cites a doc line (DR-0540)

The grammar kept `[<letters>:...]` for later anchor kinds (DR-0198). The new kind is `[d:...]`, a reference to one line
of another doc:

| Form | Who writes it | Means |
|---|---|---|
| `[d:<doc id>@<rev>#<n>]` | the writer, through doc_put | line n of that rev of the doc; `verify` hands out this token as each line's `cite` |
| `[d:<doc id>#<n>]` | a person, in a hand edit only | line n of the doc as it reads now; the take-in pins it |
| `[d:<doc id>@<rev>#<n>=<hash>]` | Docket | pinned: the rev and line cited, and the first 8 hex digits of the sha256 of that line's text |

- n is the line number in the file, as `read` and `verify` show it. A citable line is a body line that isn't blank and
  isn't in fenced code or a comment. In a legacy note, any non-blank line counts, so briefs can build on the 203 old
  notes.
- doc_put refuses a bare `[d:<doc id>#<n>]`, however it is called (MCP, the service's API, `docket doc put`), and
  points at the `cite` token: a writer names the rev it read, so a doc that changed in between can't be cited by a
  line that moved. Only a hand edit's take-in pins bare refs, against the file as it stands.
- Pinning reads the cited rev's text from the file when it is the current rev, else from git, once per doc and rev. The
  put result names the lines it pinned, as it names the lines it redacted.
- A pinned ref is kept as written; doc_put checks only its shape and that its doc exists. A hand-edited hash can only
  make a line flagged, never hide one.
- Refused: a ref to a doc that doesn't exist, to a rev it never had, to a line that isn't citable, to the doc itself,
  and to a line whose text also stands at another line of that rev: "line 7's text also stands at line 9; cite a line
  whose text is its own".
- `trailing_anchors` returns refs in a list of their own next to the anchors, so the code that reads `anchors` is
  unchanged.

The hashed text is exactly this:

| Rule | What it means |
|---|---|
| The line | the cited rev's file text, LF-normalized as anchors count lines (CRLF and a lone CR read as LF), cut into lines at LF |
| The run | on a content line of an anchored doc, the line's text as `trailing_anchors` returns it: the trailing anchors, refs and anchor-like error tokens are dropped, and a table row's closing pipe is left out, as `trailing_anchors` leaves it out. A legacy note's line, or any line that isn't a content line, is hashed whole, so "Meeting moved @14:30" changed to "@15:30" flags its dependents |
| Spaces | leading whitespace is kept; only trailing spaces and tabs are stripped. A trailing no-break space is text, so it stays in the hash, and an edit that drops it flags |
| Bytes | encoded as UTF-8 with no Unicode normalization. An editor that re-saves a doc in NFD flags its dependents; accepted, since a re-cite clears them |
| Digest | sha256, the first 8 hex digits, lowercase |

A fixed time anchor changes what a line points at, not its text, so it flags nothing. Any edit to the text flags.

A content line ends with raw anchors, refs, or both, and needs at least one of them. `covers` lists the rids the doc's
raw anchors use. It may be empty only when no line has a raw anchor, so a brief built only from note lines need not
list every recording behind them. `verify` follows a ref one level: it shows the cited line as it reads now, with that
line's own raw spans.

Not taken:
- **A frontmatter key mapping line numbers to sources.** It drifts from the lines it describes on every edit, and
  nobody reading the line sees it.
- **Rev and line without a hash.** Every flag would then need the cited doc's history from git and a diff of the two
  revs, and the index would no longer build from the working tree alone (DR-0115).

### Redaction never touches an anchor run (DR-0541)

Today `derived.split` reads a line with any token it can't parse as all text. So `docket doc redact` on today's Docket
would redact inside a pinned ref or a rid and commit that as the next rev, and the damage syncs to every clone. The
review probe shows it: `[r:calls:call-[redacted:pin]@12:08]` and `...#7=[redacted:hex]]`.

**C, in this PR, for every later format change too:**
- On content lines of an anchored doc, as today, `derived.split` always splits off the line's trailing anchor-like
  run, whether its tokens parse or not, and redaction never changes the run's bytes. A legacy note's lines, and any
  other line, stay whole text: they are redacted as text and never stop a write, so a legacy note ending in `@2pm` or
  `[x:y]` redacts as before.
- A run holding a token this Docket doesn't parse stops any write that would redact the doc: `docket doc redact`
  leaves the doc as it stands and names it, the take-in refuses and leaves the hand edit on disk, and doc_put refuses.
  For a reserved form it doesn't read (`[<letters>:...]` other than `[r:`, `[d:` and a redaction marker), the message
  is "line N holds an anchor this Docket doesn't read; update Docket, or move it before the line's anchors", since
  `[todo:later]` is likelier a typo than a newer Docket. For a malformed `[r:` or `@` token, it is the
  anchor's own problem, to fix the line.
- No line goes into a commit unredacted: the doc is left whole rather than written with one line skipped, so a secret
  typed into a token that only looks like an anchor never reaches git unredacted either.
- Tested with an invented `[zz:...]` form.

**B, for the Dockets already installed, which read `[d:` as such a form today but split the line as text:**
- Accepted, with these mitigations:
  - the CHANGELOG and the README say to update every machine before docs cite lines, and never to run `docket doc
    redact` on an older one;
  - `verify` and `docket doc stale` name a damaged token, a token in a content line's trailing run that starts with
    `[r:` or `[d:` and holds `[redacted:`, with the newest earlier rev where that line's run was whole, to restore it
    from;
  - a test covers the detection.
- Today's only vaults sit on machines that are updated together.

**Not chosen, A: a vault format key an older Docket refuses.** Under #134, a new `vault.yaml` key counts as loosening,
so it would need its own case in `effective`. It would also refuse every older command, not only the one that harms.

### Flags are computed, never stored (DR-0542)

| Part | Rule |
|---|---|
| Index | schema 9, so the next search rebuilds the index. `derived_from(doc, line, ordinal, src, rev, src_line, hash)` holds each pinned ref, with an index on `src`. `doc_lines(doc, hash, line)` holds each citable line of every doc, keyed in that order, so the join's `(doc, hash)` lookup is the key's prefix. Both are loaded with the doc's other rows and dropped with them |
| Flagged | a ref is flagged when its doc is gone, when that doc is superseded, or when no citable line of its current text has the hash. One SQL join, so two rebuilds give the same flags and an update needs no extra pass |
| Direct only | only the lines citing the changed line are flagged. When one of them is cited afresh, its own dependents are flagged in turn, rev by rev, so a cycle can't loop |
| Clearing | a flag clears only when the dependent's next rev cites the line afresh (a new `@<rev>` ref, which doc_put pins to that rev) or drops the ref. Rewriting the line's text while keeping the old ref leaves it flagged. Re-citing is how a writer keeps a line after checking it |
| `read` | each line carries its flags, worked out from the vault rather than the index: "cites line 7 of rev 2 of X, which changed in rev 3", or "X is superseded by Y" |
| `docket doc stale` | flagged lines grouped by doc (the line, the ref and why), then the corrections not applied yet, then any damaged tokens (DR-0541). It brings the index up to date first, as a search does |
| Session start | the service's index status gains two counts, which the hook prints as one line Claude reads: "docs: 3 line(s) cite lines that changed, 1 correction not applied; `docket doc stale`". With no service running, the line is left out |

### Corrections (DR-0543)

`verify` stays read-only. Each line it returns carries a `cite` token (`[d:<doc>@<rev>#<n>]`). When Claude finds a
mismatch, it writes a `correction` doc with doc_put (the type exists), then puts the corrected doc's next rev. The
second put is what flags the dependents.

| Part | Rule |
|---|---|
| Lines | each content line of a correction holds raw anchors, the evidence, and at least one ref, the line it corrects. `by` says who found it |
| State | a correction whose cited line still reads the same is not applied yet. Once that line changes, it is applied. Its refs count toward the "not applied" number, never toward the flagged one |
| The skill | the recall skill and the MCP server's instructions say: on a mismatch, a correction doc first, then the doc's next rev |

Not taken: `verify` writing the correction itself. It would make a read-only tool a writer, and the judgement is
Claude's. The correction goes through doc_put's gate like every other doc.

## Doors and data

| Door | What changes | Existing vaults |
|---|---|---|
| The anchor grammar (DR-0198) | `[d:...]`, in the slot the grammar kept for later kinds | no doc holds one today, so nothing moves |
| The doc format (DR-0109) | `covers` may be empty when no line has a raw anchor | docs as they are stay valid. An older Docket reads such a doc leniently and refuses only to write one |
| Redaction (DR-0197) | a line's anchor run is never redacted; a doc whose run holds a token this Docket doesn't parse is left whole | docs as they are redact as before, except that a doc with a malformed anchor run is now left whole and named instead of redacted as text |
| MCP results | `DocLine.flags`, `VerifiedLine.cite`, a ref's cited line in `verify`, the put result's pinned lines. All are added fields | none |
| The index | schema 9 | a cache, so the next search rebuilds it (DR-0115) |

The bare-ref refusal in doc_put is an input rule, not a door. An older Docket reads `[d:...]` as a reserved form it
doesn't know: its index leaves the token out of the chunk text, its `verify` names the token as a problem, its doc_put
refuses a doc holding one, and its `docket doc redact` damages it (DR-0541, B).

## Steps

1. `anchors.py`: `DocRef`, its parse and format, and `Trailing.refs`. Tested by `test_anchors.py`: round trips, the
   three forms, every refusal, and a redaction marker staying text.
2. `derived.py`: the anchor run always split off and never redacted; a doc whose run holds a token this Docket doesn't
   parse stops the take-in and `docket doc redact`. Tested by `test_derived.py` with an invented `[zz:...]` form, and by
   `test_docstore.py` for the take-in and the redact pass leaving such a doc whole.
3. `processed.py`: citable lines and the line hash, byte for byte as above. Tested by `test_processed.py`: CRLF, a
   table row, leading spaces, a trailing no-break space, NFD against NFC.
4. `docstore.py`: the ref checks, pinning (from the file, or from git for an earlier rev), the bare-ref refusal, the
   duplicate-text refusal, the content-line rule and empty `covers`, on put, put_many and the take-in. Tested by
   `test_docstore.py`: a pinned put, an earlier rev cited, each refusal, and a take-in that pins a bare ref.
5. The index: schema 9, the two tables, their keys, and the flag query in `index/flags.py`. Tested by
   `test_index_flags.py`: a corrected note flags the line built on it, an unrelated edit flags nothing, gone and
   superseded docs flag, and two rebuilds give the same flags.
6. `recall.read` and `verify`: flags on lines, `cite`, refs followed one level, damaged tokens named, and the text
   render. Tested by `test_recall.py`.
7. `docket doc stale`, and the counts in `IndexStatus` and the session-start hook. Tested by `test_cli_doc.py` and
   `test_hooks.py`.
8. The `correction` rules, then the recall skill, the MCP instructions, the README and the CHANGELOG. Tested by
   `test_docstore.py` (a correction without a ref or without raw evidence is refused) and `test_index_flags.py` (not
   applied, then applied).

## Tests

| Test | What it proves |
|---|---|
| a corrected recording note | a brief line citing the changed line is flagged in `read`, `docket doc stale` and the count; a line citing an unchanged line of the same note isn't |
| an unrelated edit | a new line added above the cited one shifts the numbers and flags nothing |
| clearing | the brief's next rev rewrites the line's text but keeps the old ref: still flagged. A rev that cites the line afresh clears it and holds the new rev. A rev that drops the ref leaves no flag |
| rebuilds | two rebuilds, and an update after the edit, give the same flags |
| pinning | `[d:X@2#7]` comes back as `[d:X@2#7=<hash>]`, an earlier rev read from git once; a bare `[d:X#7]` is refused by doc_put and pinned by a take-in |
| refusals | missing doc, missing rev, blank or fenced line, self-cite, a line whose text stands twice, malformed token |
| the hash | CRLF and LF hash alike; a table row's closing pipe and trailing spaces don't count; leading spaces and a trailing no-break space do; NFD differs from NFC; a legacy note line "Meeting moved @14:30" changed to "@15:30" flags its dependents |
| redaction | `docket doc redact` on a doc with refs leaves every ref's bytes as they were; a doc holding `[zz:...]` is left whole by the redact pass and the take-in, and refused by doc_put, each with the update message; a malformed `[r:` token holding an invented password is never committed, redacted or not; a legacy note line ending in `@2pm` is redacted as text and not refused |
| damage | a line whose `[r:` or `[d:` token holds `[redacted:` is named by `verify` and `docket doc stale`, with the newest earlier rev where the run was whole |
| corrections | not applied, then applied once the corrected doc's line changes; never counted as flagged |
| older readers | a lenient parse of a doc with refs, as an older index does it, loads without a problem row |

All of these run on the synthetic fixture vault, with invented docs.

## Docs

- DR-0540 to DR-0543, proposed with the build.
- The design: §3.3 (refs, flags, corrections), §5.3 (the content-line rule, `covers`, and redaction leaving anchor runs
  alone), §5.4 (`derived_from`, `doc_lines`).
- `anchors.py`'s module docstring with the new kind, the recall skill, and the MCP server's instructions.
- The README and a `CHANGELOG.md` line: lines can cite doc lines and are flagged when those change; update every
  machine before docs cite lines, never run `docket doc redact` on an older one, and run `docket doc redact --dry-run`
  once after updating, to see any doc the redact pass would now leave.

## Risks

| Risk | Answer |
|---|---|
| an older Docket's `docket doc redact` damages refs and rids, and the damage syncs | accepted for the Dockets already installed (DR-0541, B): the CHANGELOG and README warning, the damage named with the rev to restore from, and machines updated together today. From this PR on, a Docket never redacts inside an anchor run and leaves a doc whole when it holds a form it doesn't read (C) |
| tokens are long in a doc an editor shows | accepted: they sit after the text, as anchors do, and `read` names the flag in words |
| a doc changes between reading it and citing it | resolved: every doc_put cites with `@<rev>`, and only a hand edit pins a bare ref, against the file it was edited in |
| a cited line's text stands twice in its doc, so one copy changing hides the flag | refused when citing. A later rev that copies the cited text to another line and then changes the original still hides it; accepted, as that is rare and a re-cite after review finds it |
| an 8-hex hash collides within one doc | about one in four billion per pair of lines in the same doc; accepted |
| a whitespace-only edit inside a line, or a re-save in NFD, flags its dependents | accepted: a review that finds nothing to change re-cites the line |
| staleness spreads only one rev at a time | by design, since a person or Claude checks each step; `docket doc stale` shows the next layer as soon as it appears |
| the count needs the service | accepted: `docket doc stale` answers without it |

## Plan review

| Round | Verdict | Findings |
|---|---|---|
| 1, at cff01f8 | changes needed: one major, four minors | H1 (major): an older Docket's `docket doc redact` reads a line with `[d:` as text and redacts inside its anchors, and the damage syncs. Now DR-0541: C in this PR (never redact inside an anchor run; a doc holding an unread reserved form is left whole and named), B for installed Dockets (warnings, damage named with the rev to restore from), A not chosen. H2: a ref to a line whose text stands twice is refused, with a Risks row. H3: the hashed text spelled out byte for byte. H4: clearing needs a fresh ref or the ref dropped; a rewrite under the old ref stays flagged. H5: doc_put refuses a bare ref and points at `cite`; only the take-in pins one. Also: the index keys named. H1 waits for its re-check |
| 2, the H1 re-check at 85b5d52 | resolved, two minors | H1a: C splits the run only on content lines of an anchored doc, as today; a legacy note's lines and any other line stay whole text, tested with a legacy line ending in `@2pm`. H1b: a legacy note's line, or any non-content line, is hashed whole, tested with "@14:30" changed to "@15:30". Wording: the damage check names a content line's trailing run; the reserved-form message adds "or move it before the line's anchors"; the CHANGELOG asks for `docket doc redact --dry-run` once after updating. The stricter take-in (a malformed token stops the write) confirmed. Approved by the lead after it |
