"""The sync folder: what `/docket:sync`'s agents read and write, one folder per sync in Docket's state folder, never in
the vault and never committed (#258, #267, DR-0722, DR-0746 to DR-0749).

| Module | What |
|---|---|
| `folder` | where sync folders live, their ids, `sync.json`, each source's recordings; clearing them |
| `start` | `docket sync start`, `context.md` and each source agent's prompt |
| `recordings` | `docket sync recordings`, each recording's files from raw and its agent's prompt |
| `block` | the block an agent writes, its labels and fields read back (DR-0722) |
| `reconcile` | `docket sync reconcile`, the batch drafted from every block |
| `agent.md` | the source agent's prompt `start` fills, its block's shape in it |
| `recording.md` | the recording agent's prompt `recordings` fills: its lines, then its note by #211's method |

The prompts ship beside the block's parser, so the prompt an agent follows and the parser that reads its block always
come from one Docket.
"""
