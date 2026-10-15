# Writing a processed doc

`doc_put` takes a whole doc: frontmatter, then the body. It refuses a doc whose frontmatter is wrong or whose anchors
don't resolve, and lists every problem with its line, so fix them and put it again. A changed doc is stored as its next
revision; the earlier ones stay in git.

- What the user's settings redact (credentials, by default) becomes `[redacted:<rule>]` before the doc is stored, and
  the result says on which lines. Leave the markers as they are when you edit the doc.
- A doc that says something on the user's never-store list is refused, naming only the lines: leave that out and put
  it again.

## The frontmatter

```yaml
---
id: recording-note:2026-10-14-supplier-call
type: recording-note
covers:
  - plaud:of_00c0ffee00c0ffee00c0ffee00c0ffee
by: claude, recall 2026-10-15
supersedes: []
entities: [ben-sample, northwind]
---
```

| Key | What |
|---|---|
| `id` | `<type>:<slug>`; the slug lowercase letters, digits and inner hyphens, a date first when it has one |
| `type` | recording-note, thread-summary, digest, minutes, brief, correction or legacy-note: the id's prefix |
| `rev` | leave it out, or as `read` gave it: `doc_put` sets the next one |
| `covers` | the rids the doc summarizes; every anchor's rid is one of them |
| `by` | who wrote it, and the run or session |
| `supersedes`, `entities` | docs it replaces; entity ids it is about |

To fix a doc, `read` it, change the lines, keep its `id`, and put it back.

## The body

Every content line ends with its anchors, and nothing comes after them: no full stop, no comment.

```markdown
# Supplier call

- Ben asked for ten million for the whole quarter. @12:08
- Cleo wants the price in writing before Friday. @13:40-14:05
```

| Anchor | Points at |
|---|---|
| `@12:08`, `@1:02:03`, `@12:08-12:40` | a moment or a stretch of the one record the doc covers |
| `[r:<rid>@12:08]` | a moment of any record in `covers` |
| `[r:<rid>#2]`, `[r:<rid>#2-3]` | paragraphs of a mail or a message |
| `[r:<rid>]` | a whole record |

- One bullet a line: a wrapped bullet's second line has no anchor and is refused.
- Times are whole seconds from the record's start, as `read` shows them.
- Blank lines, headings, fenced code and a table's header rows need no anchor.
