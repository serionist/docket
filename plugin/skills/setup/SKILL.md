---
name: setup
description: Set up Docket for the user, or add a source to it - checks Docket is installed, asks which sources to set up, where the vault lives and which languages they work in, creates the vault, and scaffolds each source's skill and mapper. Use when the user asks to set up, install or configure Docket, add a source to it, or when the session-start status says Docket has no vault yet.
allowed-tools: Bash(docket --version) Bash(docket vault status) Bash(docket vault status *) Bash(docket service status) Bash(docket service status *)
---

# Set up Docket

One round of questions, then the `docket` command does the work, every answer passed as a flag: its own prompts can't
read from Claude's shell. Go through the sections below in order. Never assume an answer the user hasn't given, and
never pass a flag that overrides one of Docket's checks without the user's yes to that one check.

## Docket itself

Run `docket --version`. If it isn't found, tell the user to install it, and stop:

```
uv tool install "git+https://github.com/serionist/docket#subdirectory=packages/docket"
```

## What exists

From the session's project folder, run `docket vault status` and `docket service status`.

- `vault status` names the vault it found and how (`found    by ...`): `--vault`, `DOCKET_VAULT`, this folder or one
  above it, `./vault`, or the default vault in Docket's config.
- A vault inside this project's git repo is this project's: go on to add sources to it.
- A vault found anywhere else (another project's default vault, `DOCKET_VAULT`) isn't set up here. Ask the user
  whether it is the one to use, naming its path. Only on a yes, use it; otherwise make a new one here.
- `service status` names the vault the service serves; say so when it differs from the one chosen.

From here on, every command names the vault with `--vault <path>`: the one init makes, or the one the user confirmed.
Never rely on Docket finding it.

## What the user has

Look at your own tool list: an MCP server's tools show as `mcp__<server>__<tool>`, a plugin's as
`mcp__plugin_<plugin>_<server>__<tool>`. Match them against [sources.md](sources.md): the sources Docket knows, what
to look for, how private each is, and which have a reference.

## Ask, sources first

One AskUserQuestion round, your recommendation first in each question:

- **Sources**: the ones whose tools you found, picked already; the others on offer.
- **Where the vault lives**, unless What exists settled it: a folder of this repo (recommended; Docket commits only
  its own paths there), or a repo of its own. With any source chosen, the vault has to sit inside this project's git
  repo, because a source's skill and mapper live in the repo around the vault: offer only a folder of this repo, or a
  repo of its own nested in it. If the session isn't in a git repo at all, say so before going on: a vault there takes
  no sources.
- **Languages**: the ones they work in, such as `en` and `hu`.
- **Redaction**: redact credentials (passwords and secrets after a label, API keys and tokens, private keys) before
  anything is stored? Yes is recommended. As a second option, say that phone numbers can be redacted too: off unless
  they turn it on. Names are never redacted; a name to keep out goes on the never-store lists in `vault.yaml`.

Show what comes from git and the system, so the user can correct it: their name and emails (`git config user.name`,
`git config user.email`), the time zone, this machine's name.

## The vault

```
docket init --yes --no-sources --path <path> --storage <folder|repo> --remote none --owner <name> --email <email> --language <code> --time-zone <zone> --machine <name>
```

One `--email` and one `--language` per value. Add `--no-redact-credentials` only on the user's no to credentials,
and `--redact-phone` only on their yes to phone numbers. Show the user the summary init prints. Either can change
later: `docket redaction set <credentials|phone> <on|off> --vault <path>`.

**A refusal.** Init refuses a folder in a repo that looks shared, a remote it can't check, or a parent repo with
uncommitted changes. Show the refusal exactly as Docket wrote it and ask about that one check in its own
AskUserQuestion, quoting it. Only on a yes, run init again with exactly one flag added and nothing else changed:

| Refused because | The flag, only on the user's yes |
|---|---|
| the repo looks shared | `--allow-shared` |
| Docket can't check a remote is private | `--remote-is-private` |
| the parent repo has uncommitted changes | `--allow-dirty-parent` |

On a no, offer what Docket suggests instead: a repo of its own, or committing first. The summary init prints names
every override it took: show it to the user.

## Each source

For each chosen source, with the vault named:

```
docket source new <name> --vault <path> --sensitivity <work|mixed|private> --tool <tool>
docket source new plaud --vault <path> --example
```

One `--tool` per tool. `--example` where sources.md lists a reference; its sensitivity and tools come with it. Each
command prints the folder it wrote, under the project's `.claude/skills/sources/`. Claude Code doesn't load those
folders as skills: whatever runs a source reads its `SKILL.md` by path.

## The service

Offer `docket service install --vault <path>`, and ask first: it registers the user's one Docket service to start at
logon, replacing any registration there is, and starts it on that vault. After it runs, `/mcp` reconnects the
`docket` server, or a new session picks it up.

## What's next

Tell the user, briefly:

- each blank source needs its `SKILL.md` filled and its mapper written, from a saved payload; its tests run with
  `python -m pytest .claude/skills/sources/<name>/tests`;
- recordings and mail already fetched go in with `docket ingest <name> <folder> --vault <path>`;
- the new folders under `.claude/skills/sources/` are theirs to commit; Docket commits only the vault.

Run again later, this skill adds sources to the vault the user confirms.
