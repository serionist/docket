---
title: Flag the lines built from a doc line that changed
issue: "#160"
status: draft
approved_by:
approved_at:
---

# Flag the lines built from a doc line that changed

## Goal

A derived line says which doc lines it was built from. When one of those lines changes in a later rev, the derived
line is flagged until its doc's next rev cites the line again or rewrites it (design §3.3). Today lines anchor only to
raw, so a corrected recording note leaves every brief and decision built on it saying the old thing, and nothing shows
it.

- In scope: a doc-line reference in the anchor grammar, what doc_put checks and pins, the `derived_from` table in the
  index, the flag and clear rules, flags in `read` and `verify`, `docket doc stale`, a count at session start, and the
  `correction` doc that a verify mismatch becomes.
- Out of scope: briefs (#161) and decisions (#162), which are the first docs to cite lines; ledger items (phase 3); a
  machine judging whether a line matches its raw, which stays Claude's call (DR-0104).

## Design

### A line cites a doc line (DR-0540)

The grammar kept `[<letters>:...]` for later anchor kinds (DR-0198). The new kind is `[d:...]`, a reference to one line
of another doc:

| Form | Who writes it | Means |
|---|---|---|
| `[d:<doc id>#<n>]` | the writer | line n of the doc as it reads now |
| `[d:<doc id>@<rev>#<n>]` | the writer | line n of that rev, for a writer that read an earlier rev or wants no race |
| `[d:<doc id>@<rev>#<n>=<hash>]` | doc_put | pinned: the rev and line cited, and the first 8 hex of the sha256 of that line's text |

- n is the line number in the file, as `read` and `verify` show it. A citable line is a body line that isn't blank and
  isn't in fenced code or a comment. In a legacy note, any non-blank line counts, so briefs can build on the 203 old
  notes.
- The hash covers the line's text without its trailing anchors and refs, with trailing spaces and tabs stripped. A
  fixed time anchor changes what a line points at, not what it says, so it flags nothing. Any edit to the words flags.
- doc_put pins every unpinned ref before it commits. The current rev's text is read from the file; an earlier rev's
  from git, once per doc and rev. The put result names the lines it pinned, as it names the lines it redacted.
- A pinned ref is kept as written; doc_put checks only its shape and that its doc exists. A hand-edited hash can only
  make a line flagged, never hide one.
- Refused: a ref to a doc that doesn't exist, to a rev it never had, to a line that isn't citable, and to the doc
  itself.
- `trailing_anchors` returns refs in a list of their own next to the anchors, so the code that reads `anchors` is
  unchanged. Redaction keeps the whole trailing run, refs included, byte for byte (DR-0197).

Not taken: a frontmatter key mapping line numbers to sources. It drifts from the lines it describes on every edit, and
nobody reading the line sees it. Also not taken: rev and line without a hash. Then every flag would need the cited
doc's history from git and a diff of the two revs, and the index would no longer build from files alone.

### What a content line needs (DR-0541)

A content line ends with raw anchors, refs, or both, and needs at least one of them. `covers` lists the rids the doc's
raw anchors use. It may be empty only when no line has a raw anchor, so a brief built only from note lines need not
list every recording behind them. `verify` follows a ref one level: it shows the cited line as it reads now, with that
line's own raw spans.

### Flags are computed, never stored (DR-0542)

| Part | Rule |
|---|---|
| Index | schema 9, so the next search rebuilds the index. `derived_from` holds `(doc, line, ordinal, src, rev, src_line, hash)` for each pinned ref, and `doc_lines` holds `(doc, hash, line)` for each citable line of every doc. Both are loaded with the doc's other rows and dropped with them |
| Flagged | a ref is flagged when its doc is gone, when that doc is superseded, or when no citable line of its current text has the hash. One SQL join, so two rebuilds give the same flags and an update needs no extra pass |
| Direct only | only the lines citing the changed line are flagged. When one of them is rewritten, its own dependents are flagged in turn, rev by rev, so a cycle can't loop |
| Clearing | the dependent's next rev cites the line again (doc_put pins it to the current rev), rewrites it, or drops the ref. Re-citing is how a writer keeps a line after checking it |
| `read` | each line carries its flags, worked out from the vault rather than the index: "cites line 7 of rev 2 of X, which changed in rev 3", or "X is superseded by Y" |
| `docket doc stale` | flagged lines grouped by doc: the line, the ref and why, then the corrections not applied yet. It brings the index up to date first, as a search does |
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
| MCP results | `DocLine.flags`, `VerifiedLine.cite`, a ref's cited line in `verify`, the put result's pinned lines. All are added fields | none |
| The index | schema 9 | a cache, so the next search rebuilds it (DR-0115) |

An older Docket reads `[d:...]` as a reserved form it doesn't know:
- its index leaves the token out of the chunk text;
- its `verify` names the token as a problem;
- its doc_put refuses a doc holding one;
- its `docket doc redact` reads that line's anchors as text.

So the CHANGELOG line says to update every machine before docs cite lines, as the redaction and index changes said.

## Steps

1. `anchors.py`: `DocRef`, its parse and format, and `Trailing.refs`. Tested by `test_anchors.py`: round trips, the
   three forms, every refusal, and a redaction marker staying text.
2. `processed.py` and `derived.py`: citable lines and the line hash, and redaction keeping refs byte for byte. Tested
   by `test_processed.py` and `test_derived.py`.
3. `docstore.py`: the ref checks, pinning (from the file, or from git for an earlier rev), the content-line rule and
   empty `covers`, on put, put_many and a hand edit's take-in. Tested by `test_docstore.py`: a pinned put, an earlier
   rev cited, each refusal, and a take-in that pins.
4. The index: schema 9, the two tables, and the flag query in `index/flags.py`. Tested by `test_index_flags.py`: a
   corrected note flags the line built on it, an unrelated edit flags nothing, gone and superseded docs flag, and two
   rebuilds give the same flags.
5. `recall.read` and `verify`: flags on lines, `cite`, refs followed one level, and the text render. Tested by
   `test_recall.py`.
6. `docket doc stale`, and the counts in `IndexStatus` and the session-start hook. Tested by `test_cli_doc.py` and
   `test_hooks.py`.
7. The `correction` rules, then the recall skill and the MCP instructions. Tested by `test_docstore.py` (a correction
   without a ref or without raw evidence is refused) and `test_index_flags.py` (not applied, then applied).

## Tests

| Test | What it proves |
|---|---|
| a corrected recording note | a brief line citing the changed line is flagged in `read`, `docket doc stale` and the count; a line citing an unchanged line of the same note isn't |
| an unrelated edit | a new line added above the cited one shifts the numbers and flags nothing |
| clearing | the brief's next rev re-cites the line, the flag goes, and the token holds the new rev |
| rebuilds | two rebuilds, and an update after the edit, give the same flags |
| pinning | `[d:X#7]` comes back as `[d:X@2#7=<hash>]`; an earlier rev is read from git once |
| refusals | missing doc, missing rev, blank or fenced line, self-cite, malformed token |
| redaction | `docket doc redact` on a doc with refs leaves every ref's bytes as they were |
| corrections | not applied, then applied once the corrected doc's line changes; never counted as flagged |
| older readers | a lenient parse of a doc with refs, as an older index does it, loads without a problem row |

All of these run on the synthetic fixture vault, with invented docs.

## Docs

- DR-0540 to DR-0543, proposed with the build.
- The design: §3.3 (refs, flags, corrections), §5.3 (the content-line rule and `covers`), §5.4 (`derived_from`,
  `doc_lines`).
- `anchors.py`'s module docstring with the new kind, the recall skill, and the MCP server's instructions.
- A `CHANGELOG.md` line: lines can cite doc lines and are flagged when those change; update every machine first.

## Risks

| Risk | Answer |
|---|---|
| tokens are long in a doc an editor shows | accepted: they sit after the text, as anchors do, and `read` names the flag in words |
| a race between reading a doc and citing it by its current line | the `@<rev>` form, which the skills use; the bare form is for a person typing |
| an 8-hex hash collides within one doc | about one in four billion per pair of lines in the same doc; accepted |
| a whitespace-only edit inside a line flags its dependents | accepted: a review that finds nothing to change re-cites the line |
| an older machine runs `docket doc redact` on a doc with refs | its anchors would be redacted as text; the CHANGELOG asks for every machine to update first, as earlier format changes did |
| staleness spreads only one rev at a time | by design, since a person or Claude checks each step; `docket doc stale` shows the next layer as soon as it appears |
| the count needs the service | accepted: `docket doc stale` answers without it |

## Plan review

Not reviewed yet.
