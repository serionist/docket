---
title: Render the people directory and the glossary from entities
issue: "#163"
status: draft
approved_by:
approved_at:
---

# Render the people directory and the glossary from entities

## Goal

The people directory and the glossary in the user's repo are written from the entities, so curate rounds keep them up
and the hand-kept files can retire (design §12, step 3). Docket writes each file and never commits it.

- In scope: a `vault.yaml` key naming each file and its kind, the two renderers, the rules (a hand edit reported, a
  public repo refused, never-store values left out), `docket render`, and a render after each curate commit.
- Out of scope: seeding the entities from today's files and choosing the final columns, which need the maintainer;
  status files rendered from the ledger (phase 3), which will follow the same rules.

## Design

### The key (DR-0552)

```yaml
rendered:
  - {kind: people, path: docs/people.md}
  - {kind: glossary, path: docs/glossary.md}
```

- `path` is relative to the consuming repo, the git repo that holds the vault's folder, as a mapper's path is. It is
  written with `/`, has no `..` and ends in `.md`. After links are followed it must stay in that repo and outside the
  vault.
- The key is optional, so `schema_version` stays 1 and a file without it reads as before. An older Docket refuses a
  `vault.yaml` that has it, as it refused `media_hosts` (DR-0124).
- It falls under #134 as a later key, with no special case: `effective` keeps HEAD's `rendered`, so an edit adding a
  file waits for its commit, and a CLI change asks at the console. A test holds both.

### The files (DR-0553)

| Part | Rule |
|---|---|
| First line | `<!-- Rendered by Docket from the vault's entities at <commit>; a hand edit is reported, never kept. sha256:<hash> -->`: the last commit that changed `entities/`, and a hash of everything after the line |
| People | a table, one row per active person sorted by name: a name column per language in `owner.languages`, then Role, Chat (each ids key but email, as `key: value`), Mail and Aliases (an ASR form marked `(asr)`) |
| Glossary | a table, one row per active entity of any other type, sorted by type then name: Term (its first name in the owner's languages), Type, Other names, Spellings |
| Cells | a `|` in a value written `\|`, line breaks as spaces; archived entities left out |
| Stable | the same entities give the same bytes, so an unchanged set rewrites nothing |
| Never-store | an entity whose id or a name a list holds now is left out whole; a listed alias, id or role is left out of its row, as Curate and `curate show` hide them |

### The rules (DR-0554)

| Rule | What happens |
|---|---|
| Hand edit | the file is rewritten only when its first line is Docket's and the rest still hashes to it. Otherwise (a hand edit, or a hand-kept file already at that path) it is left as it is and reported: move the edit into the entities, then delete the file |
| Public repo | before writing, each remote of the consuming repo is checked as setup checks a host's (`host.remote_visibility`); one anyone can read stops the file, reported. One that can't be checked anonymously doesn't stop it: the owner named the path in a committed `vault.yaml` or at the console |
| Never committed | the file is outside the vault, and Docket's commits hold only the vault's paths |
| When | `docket render`, and after each curate write that committed something (apply, reject, lift, undo, a tier 1 round), once its lock is let go, so a remote's check never holds the vault. A render that fails is reported and never fails the curate write |

Not taken:
- **A record of what Docket last wrote in its private folder**, to tell a hand edit apart: another machine pulling the
  file would see no record and report it forever. The hash in the first line travels with the file.
- **Rendering inside the vault:** Docket would have to commit it, or leave an untracked file in its own folder.

## Doors and data

| Door | What changes | Existing vaults |
|---|---|---|
| `vault.yaml` | an optional `rendered` list | none: without the key nothing is rendered. Once the key is used, every machine needs this Docket |
| the user's repo | files Docket writes and never commits, only at the paths `vault.yaml` names | none until a path is named |

## Steps

1. Move `spelled` and `slug_listed` from `curate/batch.py` to `vault/neverstore.py`, so the vault layer can hide what
   Curate hides without importing curate. Existing curate tests prove it.
2. `RenderedFile` and the `rendered` key in `config.py`, with its path checks. Tested by `test_config.py`, and by a
   test in `test_rendered.py` that `effective` and `loosens` treat it as a later key.
3. `vault/rendered.py`: the two renderers, the header and its hash, the hand-edit check, the public check, the write.
   Tested by `test_rendered.py`.
4. `docket render`, and the render after curate writes, with its lines in `curate_written`. Tested by
   `test_rendered.py` and `test_cli_render.py`.

## Tests

| Test | What it proves |
|---|---|
| stable output | two renders of the same entities give the same bytes, and the second writes nothing |
| hand edit | an edited file, or a hand-kept file at the path, is left as it is and reported |
| public remote | a consuming repo whose remote anyone can read gets no file, and the report names the remote |
| removed entity | an entity removed by a curate undo drops its row at the next render |
| never-store | a person a list holds is left out whole; a listed alias is left out of its row |
| the key | an uncommitted `rendered` entry isn't in force; `loosens` says adding one loosens |
| curate | a curate write renders; a render failure doesn't fail it |

All on the test kit's vaults, with invented people.

## Docs

- DR-0552 to DR-0554, proposed with the build.
- The design: §5.1 (`vault.yaml`'s `rendered`), §12 step 3.
- The README: `docket render` and the key; a `CHANGELOG.md` line.

## Risks

| Risk | Answer |
|---|---|
| the default columns aren't what the maintainer keeps today | accepted: they are a start, and changing them changes only the renderer; the PR asks for the columns |
| a remote's check needs the network, up to its time limit | it runs only when a file would change, after the vault's lock is let go |
| a hand-kept file at the path blocks the render | by design: the report says to compare and delete it, or to point `path` elsewhere for the week of comparing |
| an older Docket can't read a `vault.yaml` with the key | the same as every optional key so far; the CHANGELOG says to update every machine first |

## Plan review

Not reviewed: built on the coordinator's go-ahead for an S issue, with the decisions proposed for review in the PR.
