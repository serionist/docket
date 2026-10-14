# Security

Docket holds a person's private life: their calls, mail and chat. A vulnerability here is serious even before the
first release.

## Reporting

Report it privately through a [security advisory](https://github.com/serionist/docket/security/advisories/new), never
in an issue. Say what an attacker can do, how to reproduce it, and which version or commit you tested. You'll get an
answer within a week.

## What counts

- Anything that lets another user on the machine, a web page in the browser, or content Docket ingests (a message, a
  transcript, a document) read or change the vault, call the MCP tools, or run code.
- A secret or key written where it shouldn't be: the vault's git history, logs, the index, a model's prompt.
- A way to make Docket send something without the user's click.

## Supported versions

Docket is not released yet. Until it is, fixes land on `main` only.
