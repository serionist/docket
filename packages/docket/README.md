# docket

The Docket app: the vault, the service (web app, MCP endpoint, scheduler) and the `docket` command. The repo's
[README](../../README.md) says what Docket is.

| Command | What it does |
|---|---|
| `docket init` | set up a vault: asks where, who you are, this machine's name, the sources and a remote; every answer is also a flag, and `--yes` takes the defaults |
| `docket vault status` | records per source, runs, run files not committed yet, the last commit |
| `docket vault check` | whether the raw log is intact: every file parses, nothing written once was changed |
| `docket staging list` | what waits for the gate, oldest first: `pending` waits for you or Claude, `ready` goes to raw at the next promotion |
| `docket staging show <ref>` | one staged record, its cuts, and the version in raw that was cut, when there is one |
| `docket staging classify <ref> work\|private` | work makes it ready, `--cut 120-340` (characters) or `--cut 12:30-14:05` (a time) cuts a stretch; private drops it |
| `docket staging promote` | writes every ready record to the raw log, cut and redacted, one run per source, and commits |
| `docket doc put <file or ->` | store a processed doc as its next rev; refused, with every problem and its line, when the frontmatter is wrong, an anchor doesn't resolve, or the doc changed since the rev it carries |
| `docket doc read <locator>` | a doc, or a record and the doc lines that point at it; `--view raw` or `both` for the raw, `--around` for minutes, paragraphs or messages either side, `--rev` for an earlier revision |
| `docket doc verify <doc>` | each line of a doc next to the raw its anchors point at; `--line` for chosen lines |
| `docket search <query>` | hits grouped by recording or thread, each with its locator and the group's summary; `--since`, `--until`, `--source`, `--person`, `--entity`, `-k`; it updates the index first unless `--no-update` |
| `docket index rebuild` | build this machine's index from the vault, from scratch |
| `docket index update` | index what changed since; it rebuilds when it has to |
| `docket index status` | whether the index is there and current, its languages, counts, what is new, and its problems |
| `docket import notes <folder>` | each markdown note in an existing folder becomes a legacy note that keeps its old path, so `doc read notes/x.md` or `notes/x.md@12:08` still finds it; the folder is only read; run it again to pick up changes; `--dry-run` first, `--as` for what old paths start with |

A command finds its vault from `--vault`, `DOCKET_VAULT`, the working folder upward, a `vault/` folder in it, or the
default the wizard recorded. A `<ref>` is a record's rid, or at least 6 characters of the key `staging list` shows.

## The gate

Every record reaches the raw log through the gate. It reads `vault.yaml` on every call, so an edit applies at once.

| `vault.yaml` | What it does |
|---|---|
| `sources.<name>.sensitivity` | `work` goes through, `mixed` waits in staging to be classified, `private` is dropped |
| `never_store.people` | drops a record whose author, any recipient, or any id in its source's payload equals an entry: an exact address (`kid@example.invalid`, also inside `Kid <kid@example.invalid>`) or the source's user id (`U0KID`); case doesn't matter |
| `never_store.containers` | drops a record from a channel, chat or folder: its id, with or without the source's prefix (`slack:C0FAMILY` or `C0FAMILY`) |
| `never_store.keywords` | drops a record that says the words anywhere, title, text, attachment names or payload, as whole words: case, accents and curly apostrophes don't matter (`kovacs` matches `Kovács`, not `kovacsfalva`), and any punctuation may join the words; a keyword of two or more words also matches them joined by a case change or by nothing (`dora example` matches `dora-example`, `dora_example`, `DoraExample` and `doraexample`), while a one-word keyword never matches inside a word (`Max` leaves `maxRetries` alone) |
| `redaction.builtin` | `true` (the default) redacts passwords and secrets after a label (also in bold, in a table cell, or on the next line of an HTML mail), API keys and tokens, private key blocks, and phone numbers written with `+` or after a label; a `+` amount with its currency (`+12 500 000 Ft`) stays |
| `redaction.patterns` | your own: each has a `name` (it shows in the marker) and a `pattern`, a regular expression; a group named `secret` redacts only that part (`door code (?P<secret>\d+)`) |

- A hyphen or other punctuation in a keyword also matches a space: `check-up` drops "check up" too.
- A dropped record leaves no file and no log line, only a count.
- What is redacted becomes `[redacted:<name>]`, and a cut stretch becomes `[cut]`; the secret itself is kept nowhere.
- Staging lives in the vault's `staging/` folder, on this machine only, never committed.

## Processed docs

A processed doc's lines end with anchors: `@12:08` in a doc covering one record, `[r:<rid>@12:08]`, `[r:<rid>#2]` or
`[r:<rid>]` across records (DR-0110). A locator is a doc's id or path, `r:<rid>@12:08`, or a rid. The same three
operations are `docket.recall.doc_put`, `read` and `verify` in Python. A time resolves against the record's `segments`
field, which the source's mapper fills (DR-0111); a record without segments takes paragraph and whole-record anchors.

## Search

A query is words, `"quoted phrases"`, names and time words: `docket search "tízmillió forint" múlt héten --person
ben-sample`. Words match as written, accents or not, and through their stems in each of your languages; names expand to
their aliases; time words ("last week", "múlt héten", "yesterday", "2026-10") become a filter; `--person` and
`--entity` lift what mentions them and never hide the rest. In Python it is `docket.index.search`.

The index lives in `index/` in the vault, on each machine, never committed. `docket search` keeps it up to date, and
`docket index rebuild` makes it again from scratch.

Your languages are `owner.languages` in `vault.yaml`, as codes such as `en` and `hu`; English is always searched. Write
Norwegian as `"no"` in quotes, or YAML reads it as false. Adding or removing a language rebuilds the index the next
time you search.

## Entities

An entity is one YAML file, `entities/<type>/<id>.yaml`, that tells search who or what a name means:

```yaml
id: northwind
type: customer
name: {en: Northwind, hu: Northwind}
aliases:
  - {text: Northwind Traders}
  - {text: Nortwin, kind: asr}
  - {text: Norton, kind: asr, needs: [ben-sample]}
related: {person: [ben-sample]}
ids: {email_domain: northwind.example}
```

| Field | What |
|---|---|
| `id`, `type` | the file's name and its folder: person, org, customer, project, site, device, product, topic, stream, meeting-series, document, repo, place or term |
| `name` | the name per language code |
| `aliases` | other ways it is written; `kind: asr` for how speech recognition mangles it (matched within an edit or two); `needs` when it counts only next to one of those entities |
| `ids` | a source's user id (`chat: U0EXAMPLE`), `email` or `email_domain`, so what they wrote counts as theirs |
| `related`, `status`, `role`, `note` | kept for the entity view; `status` is active or archived |

A file with a mistake still loads where it can; `docket index status` lists what was wrong (DR-0113).
