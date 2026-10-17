# The sources Docket knows

What to look for in the user's own tools, and how private each source can be. A `mixed` source can carry private
content, so its records wait in staging until they are classified. A source with a reference gets
`docket source new <name> --example`, a working skill and mapper; the others get the blank template, which the user
fills from a saved payload.

| Source | Sensitivity | What to look for | Reference |
|---|---|---|---|
| `plaud` | mixed | an MCP server with `list_files`, `get_file`, `get_transcript` and `get_note` (Plaud's) | yes |
| `slack` | work | the Slack plugin or an MCP server with Slack's read and search tools | no |
| `outlook` | work | a Microsoft 365 or Outlook MCP server or connector with mail tools, or mail read in the browser | no |
| `teams` | work | a Microsoft 365 or Teams MCP server or connector with chat tools, or Teams in the browser | no |
| `calendar` | work | a calendar MCP server or connector (Microsoft 365, Google Calendar) | no |
| `gmail` | mixed | a Gmail MCP server or connector | no |
| `claude-sessions` | work | Claude Code's own session logs on this machine; no tool needed | no |
| `documents` | work | a work folder of the user's own documents; no tool needed: a folder source, `--folder <folder>`, read by `docket scan` | no |

- A source's tools go in `vault.yaml` by their own names, without the host's prefix: `mcp__plaud__list_files` is
  `list_files`, `mcp__plugin_slack_slack__slack_read_channel` is `slack_read_channel`. Pass the ones that return the
  source's content, one `--tool` each; the user can change them later, in `vault.yaml`, where a tool added applies
  once they commit it.
- A source not listed here gets a name of its own (lowercase letters, digits, dots, `_` and `-`) and the blank
  template, `mixed` unless the user says it is all work.
