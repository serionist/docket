# The Docket plugin for Claude Code

The plugin connects Claude Code to the Docket service on the same machine: its MCP tools (search, read, verify and
write the vault), a status line at each session's start, and the skills that set Docket up and answer from it.

## Install

The plugin runs the `docket` command, so install Docket first and keep it on your PATH:

```
uv tool install "git+https://github.com/serionist/docket#subdirectory=packages/docket"
```

Then add this repo as a marketplace and install the plugin, in Claude Code:

```
/plugin marketplace add serionist/docket
/plugin install docket@docket
```

`/docket:setup` then makes your vault, adds your sources and offers to start the service.

## What it brings

| Part | What it does |
|---|---|
| the MCP server `docket` | the service at `http://127.0.0.1:8741/mcp`; its tools are `search`, `read`, `verify` and `doc_put`, shown as `mcp__plugin_docket_docket__<tool>`; the token comes from `docket service token --header` on each connect |
| the session-start status | `docket hook session-start`: Claude learns whether the service runs, on which vault, and what waits in staging; you see a line only when something needs you |
| `/docket:recall` | finds what was said or written, reads the summary, checks every fact against its raw, and answers with locators; Claude also uses it on its own when you ask what someone said |
| `/docket:setup` | checks Docket is installed, asks which sources to set up, where the vault lives and your languages, creates the vault, and writes each source's skill and mapper with `docket source new` |

Sync, tasks and curate arrive in later phases, and the hook that captures a source's tool results with #41.

## When it doesn't connect

- `docket: not running`: start the service with `docket service install --vault <folder>`, then `/mcp` to reconnect.
- The service on another port than 8741 (`service.port` in Docket's config): set `DOCKET_PORT` to it in the
  environment Claude Code starts in. The status line says when the two differ.
- `docket` not found: the hook's notice and the failed server in `/mcp` both mean Docket isn't on PATH; install it as
  above.
- The token's store is gone: `docket service token --rotate`. A keyring chosen through `PYTHON_KEYRING_BACKEND` isn't
  seen by the token helper, whose environment drops names holding KEY; Docket's default store needs no variable.

A change under `plugin/` bumps the version in `.claude-plugin/plugin.json` (CV-0012): Claude Code keeps you on a
plugin's set version until it changes.
