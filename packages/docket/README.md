# docket

The Docket app: the vault, the service (web app, MCP endpoint, scheduler) and the `docket` command. The repo's
[README](../../README.md) says what Docket is.

| Command | What it does |
|---|---|
| `docket init` | set up a vault: asks where, who you are, this machine's name, the sources and a remote; every answer is also a flag, and `--yes` takes the defaults |
| `docket vault status` | records per source, runs, run files not committed yet, the last commit |
| `docket vault check` | whether the raw log is intact: every file parses, nothing written once was changed |

A command finds its vault from `--vault`, `DOCKET_VAULT`, the working folder upward, a `vault/` folder in it, or the
default the wizard recorded.
