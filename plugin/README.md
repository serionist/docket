# The Docket plugin for Claude Code

The plugin connects Claude Code to a Docket service running on the same machine: its MCP tools (search, read, verify
and write to the vault), the skills that run syncs, the task queue and meeting prep, and the hooks that capture what
sync tools return as raw records.

It is being built: the first skills and the MCP configuration arrive with phase 0. Install it from this repo's
marketplace:

```
/plugin marketplace add serionist/docket
/plugin install docket@docket
```
