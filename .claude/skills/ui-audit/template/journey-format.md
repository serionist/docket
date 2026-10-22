# Journey file format

One journey per file: `journeys/J-nn-<slug>.md` in the run folder. The journey file holds the definition (frontmatter
and flow), the evidence and judgment of every pass (`## Pass pN` sections) and the machine status (frontmatter).
Journey files and `journal.md` are the run's source of truth. `J-nn` is a stable id, not an order: the order is in
`_index.md`.

Who writes what (`reference.md`, One writer per file per phase): the inventory and the critic write the definition
(the orchestrating session versions it on an intended change); passlog writes `### Shots`; one judge per pass writes
`### Findings`; the consolidator writes `### Decisions` and the status block. Nobody else.

## Frontmatter

```yaml
id: J-07                      # stable; never renumbered
title: Queue - filter by stream and make a call
pages: [queue]                # the pages' routes, without the slash
order: 7.0                    # execution position; _index.md shows the sequence
requires: week                # the world, one of ui_world.py's; the segment that runs this journey serves it
replay_prefix: []             # the fewest journeys that rebuild what this one needs, for a re-run of it alone
personas: [owner]             # owner, or stranger
routes: [/queue]              # feeds impact-map.json
secrets: []                   # states that show a secret (a sign-in link): captured with secrets:true, never blessed
lenses: [cardinality, interruption, concurrency, sends]
driveMode: grid               # grid | canonical-only (under the run's matrix=sampled, for journeys that are not key)
effects: none                 # none | UNDONE(u) | LASTING(<what>; a segment of its own)
forms:
  queue-find:
    selector: "input[placeholder^='Find']"
    submit: null              # null: the filter applies as you type; the battery presses Enter instead
    fields:
      - { name: q, selector: "input[placeholder^='Find']", type: text, required: false, role: normal, maxlength: 200, trimPolicy: trim }
    battery: [B3, B5, B6]     # the cases that apply to this form
    seedStates: {}            # B9: the sign-in link's states, from lib/docket.mjs's sessionLinks
# the status block: written only by the consolidator
status: pending               # pending | in-progress | completed | wontfix | blocked(env) | blocked(question)
pass: 0
openFindings: []
```

Field roles (drive `runBattery()`): `normal`, `email`, `number`, `key` (an id shape), `select`, `date`, and
`password`, `confirm-of:<field>`, `code`, which no Docket form has today. `trimPolicy`: `trim`, `reject` or
`preserve`.

## `## Flow`: typed story points

One bullet per point, in this grammar:

```markdown
## Flow
- `J-07.1` **navigate** /queue as owner → expect: the untriaged first, then a group per call, the counts saying what
  the groups hold · shots: grid · state: queue-list
- `J-07.2` **fill** form=queue-find {q: "zeppelin"} → expect: filtered empty, "no matches", a way to clear the find
  box · shots: canonical · state: queue-filtered-empty
- `J-07.3` **battery** form=queue-find cases=[B3,B5,B6] → expect: the validation display contract ·
  shots: per-battery
- `J-07.4` **navigate** /queue?open=A-0012 as owner → expect: the panel on A-0012, the URL as the tasks skill gives it ·
  shots: grid · state: queue-panel
- `J-07.5` **click** key=2 → expect: the call made, the row under claude, the notice with Undo · shots: canonical ·
  state: queue-called
- `J-07.6` **click** key=u → expect: the call undone, the row back under Untriaged · shots: none
- `J-07.7` **fault** fail 500 /api/web/queue → expect: the failure in the writer's words, a way to ask again that
  keeps the filters · expectedStatuses: [500] · shots: canonical · state: queue-read-fails
```

- **Point id** `J-nn.k`: stable within the journey; findings anchor to it.
- **Types:** `navigate`, `fill`, `click` (a selector or `key=<key>`), `scroll`, `resize`, `assert` (an expectation, no
  action), `validate` (a check the script decides itself), `battery` (the form's cases), `fault` (`fail`, `slow`,
  `down`, `lapse`, `clear`: `faults` in `lib/part.mjs`), `other` (a write from another tab or the CLI, for
  concurrency).
- **`expect:`** is the judge's intent for the point: what the page's text of record says this state shows. Add
  `expectedStatuses: [...]` wherever a 400, 401 or 409 is designed, so it is never filed as a fault.
- **`shots:`** `canonical` (the default for actions), `grid` (key and terminal states), `none` (transitions),
  `per-battery`. Overlays, inline validation, notices, refusals and empty states are `canonical` by nature: shot in
  place, their variants from the sweeps.
- **`state:`** the shot's base name, required when `shots:` is not `none`. Use the state names `devkit ui` gives the
  page where it has them (`list`, `panel`, `ticked`, `notice`, `refusal`, `empty`).

## `## Pass pN`: the history, appended, never rewritten

```markdown
## Pass p1

<!-- passlog:p1:start -->
### Shots (written by passlog; do not edit)
- `queue-filtered-empty` ![queue-filtered-empty](../screens/p1/j-07/queue-filtered-empty-1440-light-en-clip.png) ·
  [full](...) · [all cells](...) · console: 0 · budget: clean · cls 0.012
<!-- passlog:p1:end -->

### Findings (judge, p1)
- `F1` **major** · states · point `J-07.2` · cell `queue-filtered-empty-1440-light-en` · claim: the filtered list
  says "Nothing to work through" · detail: the owner thinks the queue is empty and stops looking · evidence:
  ![](...-clip.png) · fix: the filtered-empty state with a way to clear the find box · cluster: empty-flavour+lists
- capture-request: none
- proposal: none

### Decisions (consolidator, p1)
- F1 → CL-03 (systemic, with J-12/F2 and J-19/F1) · severity major, confirmed
- status → in-progress · openFindings: [F1]
```

- Finding ids are local to the journey (`F1`, `F2`, ...), never reused within the file; the journal clusters them as
  `CL-nn`. An improve carries `user-cost:`; a new capability is tagged `SPEC-CHANGE`.
- Judges propose `completed` or `wontfix`; only the consolidator sets them.
- Shots are markdown image links, never embedded data.

## `_index.md`

```markdown
# Segments, worlds, personas
## Segments   in order: each a world and the parts it runs (node runner.mjs run --world <w> --parts <ids>); a write
              nothing undoes in a segment of its own
## Worlds     per world: what of ui_world.py's it holds that the journeys need (the records by id), and each
              journey's replay prefix
## Personas   the owner (signed in per segment by the runner) and a stranger
```

## Rules

- Never renumber a journey or a story point; add new ones with the next free id.
- A fix that redesigns a flow is an intended change: the orchestrating session versions the flow before the next
  script patch; the history stays in the pass sections.
- `completed` is re-driven every pass; any pixel delta reopens it.
