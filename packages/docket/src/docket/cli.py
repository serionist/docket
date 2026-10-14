"""The `docket` command."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from docket import __version__, userconfig
from docket.errors import DocketError
from docket.vault import cuts
from docket.vault.gate import Gate, Label
from docket.vault.staging import Classifier, Entry, State
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
staging_app = typer.Typer(help="Records waiting for the gate: list, show, classify, promote.", no_args_is_help=True)
app.add_typer(staging_app, name="staging")

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


RefArgument = Annotated[str, typer.Argument(help="A rid, or at least 6 characters of a key from `staging list`.")]
SourceOption = Annotated[str | None, typer.Option("--source", help="Only this source.")]


@staging_app.command("list")
def staging_list(
    vault: VaultOption = None,
    source: SourceOption = None,
    state: Annotated[str | None, typer.Option("--state", help="Only pending or ready.")] = None,
) -> None:
    """What waits in staging, oldest first."""
    listing = Gate(find_vault(vault)).list(source, _state(state))
    for entry in listing.entries:
        typer.echo(_entry_line(entry))
    for path, message in listing.unreadable:
        typer.echo(f"unreadable    {path}: {message}")
    counts = listing.counts()
    typer.echo(f"{counts['pending']} pending, {counts['ready']} ready" if listing.entries else "nothing waits")


@staging_app.command("show")
def staging_show(ref: RefArgument, vault: VaultOption = None) -> None:
    """One staged record, its state and its cuts, and the version in raw that was cut, when there is one."""
    shown = Gate(find_vault(vault)).show(ref)
    staged = shown.entry.staged
    typer.echo(f"key       {shown.entry.key}")
    typer.echo(f"state     {staged.state}")
    typer.echo(f"staged    {staged.staged_at:%Y-%m-%d %H:%M} UTC")
    typer.echo(f"by        {staged.classified_by or '-'}")
    if staged.cuts:
        typer.echo("cuts      " + ", ".join(f"{c.start}-{c.end} {c.unit}" for c in staged.cuts))
    if staged.problem:
        typer.echo(f"problem   {staged.problem}")
    typer.echo(staged.record.model_dump_json(indent=2))
    if shown.cut_before is not None:
        typer.echo(f"cut before, in raw run {shown.cut_before.run}:")
        typer.echo(shown.cut_before.text)


@staging_app.command("classify")
def staging_classify(
    ref: RefArgument,
    label: Annotated[str, typer.Argument(help="work or private.")],
    cut: Annotated[
        list[str] | None,
        typer.Option("--cut", help="A stretch to cut: 120-340 (characters) or 12:30-14:05 (a time). Repeat."),
    ] = None,
    by: Annotated[str, typer.Option("--by", help="Who classified it: user or claude.")] = "user",
    vault: VaultOption = None,
) -> None:
    """Classify a staged record: work makes it ready, with its cuts; private drops it."""
    staged = Gate(find_vault(vault)).classify(ref, _label(label), [cuts.parse(c) for c in cut or []], _by(by))
    typer.echo("dropped" if staged is None else f"ready     {staged.key[:12]}")


@staging_app.command("promote")
def staging_promote(source: SourceOption = None, vault: VaultOption = None) -> None:
    """Write every ready record to the raw log, cut and redacted, one run per source, and commit."""
    found = find_vault(vault)
    result = Gate(found).promote(source)
    for run in result.runs:
        where = run.run.relative_to(found.root).as_posix() if run.run is not None else "nothing new"
        redacted = ", ".join(f"{rule} {n}" for rule, n in sorted(run.redactions.items()))
        line = f"{run.source}: {run.written} written ({where}), {run.skipped} already in raw"
        typer.echo(line + (f"; redacted: {redacted}" if redacted else ""))
    for held in result.held:
        typer.echo(f"held      {held.key[:12]} ({held.source}): {held.problem}")
    if result.dropped:
        typer.echo("dropped   " + ", ".join(f"{reason} {n}" for reason, n in sorted(result.dropped.items())))
    if not result.runs and not result.held:
        typer.echo("nothing ready")


def _entry_line(entry: Entry) -> str:
    staged = entry.staged
    record = staged.record
    about = " ".join((record.title or record.text).split())[:60]
    problem = f"  held: {staged.problem}" if staged.problem else ""
    when = f"{record.ts:%Y-%m-%d %H:%M}"
    return f"{entry.key[:12]}  {staged.state:<7}  {record.source}  {when}  {record.kind}  {about}{problem}"


def _state(value: str | None) -> State | None:
    if value is None or value in ("pending", "ready"):
        return value
    raise DocketError(f"a state is pending or ready, not {value!r}")


def _label(value: str) -> Label:
    if value in ("work", "private"):
        return value
    raise DocketError(f"a record is work or private, not {value!r}")


def _by(value: str) -> Classifier:
    if value in ("user", "claude"):
        return value
    raise DocketError(f"--by is user or claude, not {value!r}")


def utf8_console() -> None:
    """Read and write the console as UTF-8: on Windows a pipe gets the ANSI code page, which can't hold every name."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; return its exit code. A DocketError is one line on stderr and exit code 2."""
    utf8_console()
    try:
        app(args=list(argv) if argv is not None else None, standalone_mode=True)
    except DocketError as e:
        typer.echo(f"docket: {e}", err=True)
        return 2
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0  # pragma: no cover - Typer's standalone mode always ends in SystemExit
