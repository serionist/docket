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
| the MCP server `docket` | the service at `http://127.0.0.1:8741/mcp`; its tools are `search`, `read`, `verify`, `doc_put`, `run_open` and `ingest`, shown as `mcp__plugin_docket_docket__<tool>`; the token comes from `docket service token --header` on each connect |
| the session-start status | `docket hook session-start`: Claude learns whether the service runs, on which vault, and what waits in staging; you see a line only when something needs you |
| the capture hook | `docket hook post-tool-use` after every MCP call: during a sync run it hands Docket what the source's tools returned, and tells Claude which captures it kept; with no run open it reads one small file and returns |
| `/docket:recall` | finds what was said or written, reads the summary, checks every fact against its raw, and answers with locators; Claude also uses it on its own when you ask what someone said |
| `/docket:setup` | checks Docket is installed, asks which sources to set up, where the vault lives and your languages, creates the vault, and writes each source's skill and mapper with `docket source new` |
| `/docket:cuts` | for recordings you held back, turns your words for each left-out stretch into time ranges on the transcript, writes them to a proposals file outside any repo, and hands you `docket hold release` to run in your own terminal; it tells you first where the transcripts it reads stay in Claude Code's session files |

To sync a source, ask Claude to sync it: Claude calls `run_open`, follows the source's skill in your project, and
closes the run with `ingest`. Allow `mcp__plugin_docket_docket__run_open` and `mcp__plugin_docket_docket__ingest` in
your permission rules to skip the two prompts a run asks. The `sync`, `tasks` and `curate` skills arrive in later
phases.

Upgrading: update Docket first (the install line above, with `--reinstall`), then the plugin. An older `docket`
doesn't know `docket hook post-tool-use`, and Claude Code would show its error after every MCP call. Then copy each
source's `SKILL.md` and `mapper.py` from the reference over your project's copies: a live run refuses a `SKILL.md`
from before live runs.

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
