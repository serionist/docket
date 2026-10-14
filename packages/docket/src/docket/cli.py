"""The `docket` command."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from docket import __version__, userconfig
from docket.errors import DocketError
from docket.vault.vault import Vault
from docket.vault.wizard import Given, Wizard

app = typer.Typer(
    name="docket",
    help="A personal memory and task manager that Claude works in.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
vault_app = typer.Typer(help="The vault: its state and its integrity.", no_args_is_help=True)
app.add_typer(vault_app, name="vault")

VaultOption = Annotated[Path | None, typer.Option("--vault", help="The vault; default: DOCKET_VAULT, here, ./vault")]


class TyperPrompter:
    def ask(self, question: str, default: str | None = None) -> str:
        return str(typer.prompt(question, default=default))

    def confirm(self, question: str, default: bool = False) -> bool:
        return bool(typer.confirm(question, default=default))

    def say(self, text: str) -> None:
        typer.echo(text)


def find_vault(explicit: Path | None) -> Vault:
    """The vault a command works on: --vault, DOCKET_VAULT, the working folder upward, a vault/ child of it, or the
    default vault the wizard recorded."""
    env = os.environ.get("DOCKET_VAULT")
    for candidate in (explicit, Path(env) if env else None):
        if candidate is not None:
            return Vault.open(candidate)
    here = Path.cwd()
    for candidate in (here, here / "vault", userconfig.default_vault()):
        if candidate is not None and Vault.find(candidate) is not None:
            return Vault.open(candidate)
    raise DocketError("no vault here; pass --vault, set DOCKET_VAULT, or create one with `docket init`")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"docket {__version__}")
        raise typer.Exit


@app.callback()
def _root(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Print the version and exit.")
    ] = False,
) -> None:
    """A personal memory and task manager that Claude works in."""


@app.command()
def init(
    *,
    path: Annotated[Path | None, typer.Option(help="Where the vault goes.")] = None,
    owner: Annotated[str | None, typer.Option(help="Your name.")] = None,
    email: Annotated[list[str] | None, typer.Option(help="Your email addresses (repeat).")] = None,
    language: Annotated[list[str] | None, typer.Option(help="Languages you work in (repeat).")] = None,
    time_zone: Annotated[str | None, typer.Option(help="An IANA time zone, such as Europe/Budapest.")] = None,
    machine: Annotated[str | None, typer.Option(help="This machine's name.")] = None,
    source: Annotated[
        list[str] | None, typer.Option(help="A source to enable: name, or name=work|mixed|private (repeat).")
    ] = None,
    remote: Annotated[str | None, typer.Option(help="none, a path or URL, or github[:name].")] = None,
    remote_is_private: Annotated[bool, typer.Option(help="Confirm a remote Docket can't check is private.")] = False,
    allow_dirty_parent: Annotated[bool, typer.Option(help="Nest in a repo with uncommitted changes.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Take the defaults and don't ask.")] = False,
) -> None:
    """Set up a vault: anywhere, or nested in a working folder that is its own git repo."""
    given = Given(
        path=path,
        owner=owner,
        emails=email or [],
        languages=language or [],
        time_zone=time_zone,
        machine=machine,
        sources=source or [],
        remote=remote,
        remote_is_private=remote_is_private,
        allow_dirty_parent=allow_dirty_parent,
        yes=yes,
    )
    Wizard(TyperPrompter(), given).run()


@vault_app.command("status")
def vault_status(vault: VaultOption = None) -> None:
    """Records per source, runs, and what isn't committed yet."""
    s = find_vault(vault).status()
    typer.echo(f"vault    {s.root}")
    typer.echo(f"machine  {s.machine}")
    typer.echo(f"runs     {s.runs}")
    for source, count in sorted(s.records.items()):
        typer.echo(f"records  {source}: {count}")
    if s.seen_in_several_runs:
        typer.echo(f"records in more than one run (two machines overlapped): {s.seen_in_several_runs}")
    if s.uncommitted_runs:
        typer.echo(f"uncommitted run files: {len(s.uncommitted_runs)} (the next commit takes them)")
    typer.echo(f"last     {s.last_commit or 'no commit yet'}")


@vault_app.command("check")
def vault_check(vault: VaultOption = None) -> None:
    """Check that the raw log is intact: every file parses, and nothing written once was changed."""
    problems = find_vault(vault).check()
    for problem in problems:
        typer.echo(str(problem))
    if problems:
        typer.echo(f"{len(problems)} problem(s)")
        raise typer.Exit(1)
    typer.echo("vault ok")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; return its exit code. A DocketError is one line on stderr and exit code 2."""
    try:
        app(args=list(argv) if argv is not None else None, standalone_mode=True)
    except DocketError as e:
        typer.echo(f"docket: {e}", err=True)
        return 2
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0  # pragma: no cover - Typer's standalone mode always ends in SystemExit
