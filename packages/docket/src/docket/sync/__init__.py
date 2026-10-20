"""The sync folder: what `/docket:sync`'s agents read and write, one folder per sync in Docket's state folder, never in
the vault and never committed (#258, DR-0720 to DR-0722).

| Module | What |
|---|---|
| `folder` | where sync folders live, their ids and `sync.json`; clearing them, over 7 days old or all at a forget |
| `start` | `docket sync start`, `context.md` and each agent's prompt; `docket sync notes`, a prompt per note |
| `block` | the block an agent writes, its labels and fields read back (DR-0722) |
| `agent.md` | the agent prompt `start` fills, the block's shape in it |
| `note.md` | the note prompt `notes` fills: #211's method |

The prompts ship beside the block's parser, so the prompt an agent follows and the parser that reads its block always
come from one Docket.
"""
