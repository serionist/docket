"""The `docket` command."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import sys
import time
import webbrowser
from collections.abc import Callable, Generator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import typer
from pydantic import ValidationError

from docket import __version__, index, questions, recall, render, userconfig
from docket.curate import batch as curate
from docket.documents import scan as documents_scan
from docket.errors import DocketError
from docket.imports import notes
from docket.index.build import db_path
from docket.index.embedpass import fill
from docket.models.client import Worker
from docket.models.provider import Models, Off
from docket.models.runtime import open_models
from docket.plaintext import printable
from docket.service import client
from docket.service.platforms import Installer
from docket.service.token import Tokens
from docket.service.writer import BUILDING, UPDATE_WAIT_SECONDS
from docket.sources import release, scaffold
from docket.sources.contract import Manifest
from docket.sources.ingest import IngestReport, ingest_run
from docket.sources.local import record as record_import_folder
from docket.sources.runner import MapperFailedError
from docket.vault import convert, curatelog, cuts, holds, inforce, redaction, releaselog
from docket.vault.docstore import DocStore
from docket.vault.entitystore import EntityStore, RefusedError, Written, load_changes
from docket.vault.gate import Gate, Label
from docket.vault.processed import DocRefusedError
from docket.vault.records import RawRecord, source_name_problem
from docket.vault.spans import WINDOW
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
doc_app = typer.Typer(help="Processed docs: put one with its anchors, read it, verify it.", no_args_is_help=True)
app.add_typer(doc_app, name="doc")
index_app = typer.Typer(help="The index: built from the vault on this machine, for search.", no_args_is_help=True)
app.add_typer(index_app, name="index")
service_app = typer.Typer(
    help="The service: started at your logon, serving Claude and the web app on 127.0.0.1.", no_args_is_help=True
)
app.add_typer(service_app, name="service")


import_app = typer.Typer(help="Bring what already exists into the vault: a notes folder first.", no_args_is_help=True)
app.add_typer(import_app, name="import")
source_app = typer.Typer(
    help="A source's skill, mapper and tests in the project that holds the vault, and its entry in vault.yaml.",
    no_args_is_help=True,
)
app.add_typer(source_app, name="source")
hold_app = typer.Typer(
    help="Held records: list them, show one from its import folder, release one with the cuts you confirm.",
    no_args_is_help=True,
)
app.add_typer(hold_app, name="hold")
redaction_app = typer.Typer(
    help="Which redaction rules run: credentials (on by default), phone numbers (off), your own patterns.",
)
app.add_typer(redaction_app, name="redaction")
SENSITIVITIES = ("work", "mixed", "private")
# The Claude Code plugin's hooks run these; they print what Claude Code reads, not what a person does.
hook_app = typer.Typer(help="What the Claude Code plugin's hooks run.", no_args_is_help=True)
app.add_typer(hook_app, name="hook", hidden=True)
models_app = typer.Typer(
    help="The models on this machine: an embedder that lets search find what a question means. Optional.",
    no_args_is_help=True,
)
app.add_typer(models_app, name="models")
curate_app = typer.Typer(
    help="Entities and aliases: changes from a file, undo, and the log of every change.", no_args_is_help=True
)
app.add_typer(curate_app, name="curate")

# How often the embedding pass says how far it got.
PROGRESS_SECONDS = 10.0
# How long docket search waits for the service: its own wait for an index update, then the search. Past it the
# command searches by itself.
SERVICE_SEARCH_SECONDS = UPDATE_WAIT_SECONDS + 10.0
VaultOption = Annotated[Path | None, typer.Option("--vault", help="The vault; default: DOCKET_VAULT, here, ./vault")]
NoModels = Annotated[bool, typer.Option("--no-models", help="Keyword search alone: start no models' worker.")]


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
    return locate_vault(explicit)[0]


def locate_vault(explicit: Path | None) -> tuple[Vault, str]:
    """The vault a command works on, and how it was found: a vault found in another project's config must be
    recognisable as such before anything writes to it."""
    env = os.environ.get("DOCKET_VAULT")
    for candidate, how in ((explicit, "--vault"), (Path(env) if env else None, "DOCKET_VAULT")):
        if candidate is not None:
            return Vault.open(candidate), how
    here = Path.cwd()
    for candidate, how in (
        (here, "this folder or one above it"),
        (here / "vault", "./vault"),
        (userconfig.default_vault(), f"the default vault in {userconfig.config_path()}"),
    ):
        if candidate is not None and Vault.find(candidate) is not None:
            return Vault.open(candidate), how
    raise DocketError("no vault here; pass --vault, set DOCKET_VAULT, or create one with `docket init`")


def _vault_root(explicit: Path | None) -> Path:
    """The vault's folder, found the way `find_vault` finds it but without opening it: a convert works on a vault
    that open would refuse. A path the user named is the only one tried: falling through to ./vault or the default
    vault would convert one they never named."""
    env = os.environ.get("DOCKET_VAULT")
    named = explicit or (Path(env) if env else None)
    if named is not None:
        root = Vault.find(named)
        if root is None:
            raise DocketError(f"no vault at or above {named}")
        return root
    here = Path.cwd()
    for candidate in (here, here / "vault", userconfig.default_vault()):
        root = Vault.find(candidate) if candidate is not None else None
        if root is not None:
            return root
    raise DocketError("no vault here; pass --vault")


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
    language: Annotated[
        list[str] | None, typer.Option(help="Language codes you work in, such as en or hu (repeat).")
    ] = None,
    time_zone: Annotated[str | None, typer.Option(help="An IANA time zone, such as Europe/Budapest.")] = None,
    machine: Annotated[str | None, typer.Option(help="This machine's name.")] = None,
    source: Annotated[
        list[str] | None, typer.Option(help="A source to enable: name, or name=work|mixed|private (repeat).")
    ] = None,
    no_sources: Annotated[
        bool, typer.Option("--no-sources", help="Start with no sources; add each with `docket source new`.")
    ] = False,
    storage: Annotated[
        str | None, typer.Option(help="folder (a folder of the repo around it) or repo (a repo of its own).")
    ] = None,
    remote: Annotated[str | None, typer.Option(help="none, a path or URL, or github[:name].")] = None,
    remote_is_private: Annotated[bool, typer.Option(help="Confirm a remote Docket can't check is private.")] = False,
    allow_shared: Annotated[bool, typer.Option(help="Keep a folder vault in a repo others commit to.")] = False,
    allow_dirty_parent: Annotated[bool, typer.Option(help="Nest in a repo with uncommitted changes.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Take the defaults and don't ask.")] = False,
    redact_credentials: Annotated[
        bool,
        typer.Option(
            "--redact-credentials/--no-redact-credentials",
            help="Redact passwords, keys and tokens before anything is stored; on by default.",
        ),
    ] = True,
    redact_phone: Annotated[
        bool, typer.Option("--redact-phone/--no-redact-phone", help="Redact phone numbers too; off by default.")
    ] = False,
) -> None:
    """Set up a vault: a folder of the repo you work in, or a repo of its own."""
    given = Given(
        path=path,
        owner=owner,
        emails=email or [],
        languages=language or [],
        time_zone=time_zone,
        machine=machine,
        sources=source or [],
        no_sources=no_sources,
        storage=storage,
        remote=remote,
        remote_is_private=remote_is_private,
        allow_shared=allow_shared,
        allow_dirty_parent=allow_dirty_parent,
        yes=yes,
        redact_credentials=redact_credentials,
        redact_phone=redact_phone,
    )
    Wizard(TyperPrompter(), given).run()


@app.command()
def sync(vault: VaultOption = None) -> None:
    """Move the vault with its remote: in a repo of its own, fetch and rebase; in a folder, your own git does it."""
    result = find_vault(vault).sync()
    typer.echo(result.message)
    if "resolve it by hand" in result.message:
        raise typer.Exit(1)


@vault_app.command("status")
def vault_status(vault: VaultOption = None) -> None:
    """Records per source, runs, and what isn't committed yet."""
    found, how = locate_vault(vault)
    s = found.status()
    typer.echo(f"vault    {s.root}")
    typer.echo(f"found    by {how}")
    typer.echo(f"storage  {f'a folder of {s.host}' if s.host else 'a repo of its own'}, on {s.branch}")
    if s.branch_warning:
        typer.echo(f"warning  {s.branch_warning}")
    typer.echo(f"machine  {s.machine}")
    if found.waiting:
        typer.echo(f"settings {inforce.NOTICE}")
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


@app.command("ingest")
def ingest(
    source: Annotated[str, typer.Argument(help="The source, as vault.yaml names it.")],
    folder: Annotated[Path, typer.Argument(help="A folder of payloads fetched earlier, and its hold.txt.")],
    *,
    hold: Annotated[
        list[Path] | None, typer.Option("--hold", help="A file of ids to hold, one a line, besides hold.txt (repeat).")
    ] = None,
    final: Annotated[bool, typer.Option("--final", help="Tell the mapper not to wait for anything more.")] = False,
    mapper_errors: Annotated[
        bool, typer.Option("--mapper-errors", help="Print the mapper's own errors, which can quote a payload.")
    ] = False,
    vault: VaultOption = None,
) -> None:
    """Import a folder of payloads through the source's mapper: every record goes to staging, nothing on hold."""
    problem = source_name_problem(source)
    if problem is not None:
        raise DocketError(problem)
    found = find_vault(vault)
    if folder.is_dir():
        # A live run's close may name this folder from now on (DR-0250).
        record_import_folder(found, source, folder)
    run = f"import-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    manifest = Manifest(run=run, source=source, captures=0, final=final, folder=str(folder))
    try:
        report = ingest_run(found, source, manifest, [], folder=folder, hold_files=hold or [])
    except MapperFailedError as e:
        if not mapper_errors:
            hint = "run it again with --mapper-errors to see the mapper's own errors"
            raise MapperFailedError(f"{e}; {hint}", e.stderr) from None
        if e.stderr:
            typer.echo(e.stderr, err=True)
        raise
    _ingest_report(report)


@app.command("scan")
def scan(
    source: Annotated[
        str | None, typer.Argument(help="A folder source, as vault.yaml names it; default: every one turned on.")
    ] = None,
    *,
    accept_gone: Annotated[
        bool,
        typer.Option(
            "--accept-gone", help="Take the files missing from the folder as gone, however many of them there are."
        ),
    ] = False,
    vault: VaultOption = None,
) -> None:
    """Read a folder source's folder, read-only: each new or changed document's text goes to staging."""
    found = find_vault(vault)
    names = [source] if source is not None else documents_scan.folder_sources(found)
    if not names:
        raise DocketError("no folder source in vault.yaml; add one with docket source new <name> --folder <folder>")
    reports = [documents_scan.scan(found, name, accept_gone=accept_gone) for name in names]
    for report in reports:
        say_all(scan_lines(report))
    if any(r.staged.ready for r in reports):
        say("next     docket staging promote")
    if any(r.guarded is not None for r in reports):
        raise typer.Exit(1)


def _counts(counts: dict[str, int], order: Sequence[str] = ()) -> str:
    keys = [k for k in order if counts.get(k)] + sorted(k for k in counts if k not in order and counts[k])
    return ", ".join(f"{k} {counts[k]:,}" for k in keys)


def scan_lines(report: documents_scan.ScanReport) -> list[str]:
    """A scan's report: counts and rids, never the path of a file it left out."""
    if report.missing:
        return [f"scan     {report.source}: no folder at {report.folder} on this machine, so nothing was scanned"]
    lines = [f"scan     {report.source}: {report.files:,} file(s) in {report.folder}"]
    if report.guarded is not None:
        return [
            *lines,
            f"stopped  {report.guarded:,} of the {report.known:,} files the vault holds are missing from the folder, "
            "so nothing was staged; is the folder all there (a share mounted, a sync finished)?",
            f"         if they were deleted or moved on purpose: docket scan {report.source} --accept-gone",
        ]
    outcomes = report.outcomes
    read = ("same", "touched", "new", "changed", "back", "moved")
    done = _counts({k: outcomes[k] for k in read}, read)
    lines.append(f"files    {done or 'none to read'}")
    if report.gone:
        lines.append(f"gone     {report.gone:,}")
    lines += [f"not read {rid}" for rid in report.not_read]
    if outcomes["kept out"]:
        lines.append(f"kept out {outcomes['kept out']:,}, by the never-store lists")
    if outcomes["retry"]:
        lines.append(f"retry    {outcomes['retry']:,}: the reader stopped or crashed; tried once more at the next scan")
    later = ("unreadable", "on hold", "placeholders", "lfs pointers", "folders not listed")
    waiting = _counts({k: outcomes[k] for k in later})
    if waiting:
        lines.append(f"later    {waiting}: they keep their last version")
    if report.unlisted:
        lines.append(f"         not listed now: {', '.join(report.unlisted)}")
    if report.skipped:
        lines.append(f"skipped  {_counts(report.skipped)}")
    staged = report.staged
    lines.append(
        f"staged   {report.source}: {staged.pending} pending, {staged.ready} ready, {staged.skipped} already staged or "
        "in raw"
    )
    if staged.dropped:
        lines.append(f"dropped  {_counts(staged.dropped)}")
    return lines


@source_app.command("new")
def source_new(
    name: Annotated[str, typer.Argument(help="The source's name: lowercase letters, digits, dots, _ and -.")],
    *,
    example: Annotated[
        bool, typer.Option("--example", help="Copy Docket's reference source of this name, such as plaud.")
    ] = False,
    sensitivity: Annotated[
        str | None, typer.Option(help="work, mixed or private; default: as vault.yaml has it, else the reference's.")
    ] = None,
    tool: Annotated[
        list[str] | None, typer.Option("--tool", help="A tool whose results it captures, by its own name (repeat).")
    ] = None,
    folder: Annotated[
        Path | None,
        typer.Option(
            "--folder", help="A folder source instead: the folder docket scan reads, in the repo or under your home."
        ),
    ] = None,
    vault: VaultOption = None,
) -> None:
    """Write a source's skill, mapper and tests into the project that holds the vault, and add it to vault.yaml; with
    --folder, add a folder source that docket scan reads."""
    if sensitivity is not None and sensitivity not in SENSITIVITIES:
        raise DocketError(f"--sensitivity is work, mixed or private, not {sensitivity!r}")
    if folder is not None and (example or tool or sensitivity not in (None, "work")):
        raise DocketError("a folder source is work, with no reference, mapper or tools: --folder goes alone")
    found, how = locate_vault(vault)
    # A vault found through the environment or the config can be another project's: the folder would land in its
    # repo, and the entry be committed there. Only one named, or found from here, is taken.
    if how == "DOCKET_VAULT" or how.startswith("the default vault"):
        raise DocketError(f"found {found.root} by {how}; pass --vault {found.root} to add a source there")
    if folder is not None:
        added = scaffold.new_folder_source(found, name, folder)
        if added.declined:
            say(f"source   {name} not added: the code wasn't typed back, so vault.yaml is as it was")
            raise typer.Exit(1)
        if added.registered:
            commit = f", committed {added.commit[:12]}" if added.commit else inforce.note(found.waiting)
            say(f"source   {name} in vault.yaml: a folder source, folder {added.entry.folder}{commit}")
        else:
            say("source   vault.yaml has comments or a layout of your own, so it is left as it is; add under sources:")
            say_all((added.block or "").splitlines())
        say(f"next     docket scan {name}")
        return
    made = scaffold.new_source(found, name, example=example, sensitivity=sensitivity, tools=tool or [])
    if made.declined:
        say(
            f"source   {name} not added: the code wasn't typed back, so vault.yaml is as it was and "
            f"{made.folder} is gone"
        )
        raise typer.Exit(1)
    say(f"wrote    {made.folder}" + (", Docket's reference" if made.example else ", from the blank template"))
    for rel in made.files:
        say(f"         {rel}")
    entry = made.entry
    tools = ", ".join(entry.tools) or "none yet"
    if made.registered:
        commit = f", committed {made.commit[:12]}" if made.commit else inforce.note(found.waiting)
        say(f"source   {name} in vault.yaml: {entry.sensitivity}, mapper {entry.mapper}, tools {tools}{commit}")
    else:
        say("source   vault.yaml has comments or a layout of your own, so it is left as it is; add under sources:")
        for line in (made.block or "").splitlines():
            say(line)
    say(f"tests    python -m pytest {made.folder / 'tests'}")
    say("next     the folder is yours to commit; Docket commits only the vault")


FromOption = Annotated[Path, typer.Option("--from", help="The import folder the held recording is in.")]
IdArgument = Annotated[str, typer.Argument(metavar="ID", help="The held id, as the hold list has it.")]


@hold_app.command("list")
def hold_list(
    source: Annotated[str | None, typer.Argument(help="Only this source.")] = None, vault: VaultOption = None
) -> None:
    """The held ids, one a line; one released and held again (a merge brought the hold back) is marked."""
    if source is not None and (problem := source_name_problem(source)) is not None:
        raise DocketError(problem)
    found = find_vault(vault)
    lists = holds.read_all(found.root)
    released = releaselog.read_all(found.root)
    rows = [
        f"{name}  {native}" + ("  held again after its release" if native in released.of(name) else "")
        for name in sorted(lists.ids)
        if source is None or name == source
        for native in sorted(lists.of(name))
    ]
    say_all(rows or ["nothing held"])


@hold_app.command("show")
def hold_show(source: Annotated[str, typer.Argument(help="The source.")], native: IdArgument, from_: FromOption,
              vault: VaultOption = None) -> None:  # fmt: skip
    """A held recording as its mapper reads it from the folder, each record as intake would stage it. Writes nothing,
    and prints what was said: run it where you want it read."""
    say_all(release.show_lines(release.show(find_vault(vault), source, native, from_)))


@hold_app.command("release")
def hold_release(
    source: Annotated[str, typer.Argument(help="The source.")],
    native: Annotated[str | None, typer.Argument(metavar="[ID]", help="The held id; or give --proposals.")] = None,
    *,
    from_: FromOption,
    cut: Annotated[list[str] | None, typer.Option("--cut", help="A time to cut, such as 12:30-14:05. Repeat.")] = None,
    keep: Annotated[
        list[str] | None, typer.Option("--keep", help="A record without segments to keep as it reads, by rid. Repeat.")
    ] = None,
    proposals: Annotated[
        Path | None, typer.Option("--proposals", help="The cuts skill's proposals file, outside any repo.")
    ] = None,
    vault: VaultOption = None,
) -> None:
    """Release held recordings with their cuts: each shows what goes and what is kept, and is released only when you
    type back the code it shows. Only at your own console: Claude Code's shell, a pipe or a script is refused."""
    found = find_vault(vault)
    if proposals is not None:
        if native is not None or cut or keep:
            raise DocketError("give an id with --cut and --keep, or --proposals, not both")
        items = release.load_proposals(proposals, source)
    elif native is None:
        raise DocketError("name the held id to release, or give --proposals")
    else:
        items = [release.Proposal(native, tuple(cut or []), tuple(keep or []))]
    report = release.release(found, source, from_, items)
    rows = [f"released   {i}" for i in report.released]
    rows += [f"taken off  {i}  held again after its release; its records wait as pending" for i in report.taken_off]
    rows += [f"staged     {i}  released already; its records wait as pending" for i in report.restaged]
    rows += [f"held       {i}  {why}" for i, why in report.held]
    rows += [f"refused    {i}  {why}" for i, why in report.refused]
    if report.stopped:
        rows.append("stopped    the rest stay held")
    if report.taken_off or report.restaged:
        rows.append("next       docket staging list --state pending, and classify what waits, already cut")
    if report.released:
        rows.append("next       docket staging promote")
    say_all(rows or ["nothing to release"])


@redaction_app.callback(invoke_without_command=True)
def redaction_show(ctx: typer.Context, vault: VaultOption = None) -> None:
    """Which redaction rules run: credentials, phone numbers, your own patterns. Names are never redacted."""
    # `docket redaction --vault <path> set ...` names the vault before the subcommand: set takes it from here.
    ctx.obj = vault
    if ctx.invoked_subcommand is None:
        found = find_vault(vault)
        say_all(redaction.describe(found.config.redaction))
        if found.waiting:
            say(f"note     {inforce.NOTICE}")


@redaction_app.command("set")
def redaction_set(
    ctx: typer.Context,
    rule: Annotated[str, typer.Argument(help="credentials, phone, or one of your patterns by its name.")],
    state: Annotated[str, typer.Argument(help="on or off.")],
    vault: VaultOption = None,
) -> None:
    """Turn one redaction rule on or off in vault.yaml, and commit it."""
    if state not in ("on", "off"):
        raise DocketError(f"the setting is on or off, not {state!r}")
    named: Path | None = vault if vault is not None else ctx.obj
    found = find_vault(named)
    done = redaction.set_rule(found, rule, state == "on")
    if done.declined:
        say(f"redact   {rule} not changed: the code wasn't typed back")
        raise typer.Exit(1)
    if done.by_hand is not None:
        say(f"vault.yaml has comments or a layout of your own, so it is left as it is; set it there: {done.by_hand}")
    elif done.changed:
        say(f"redact   {rule} {state}" + (f", committed {done.commit[:12]}" if done.commit else ""))
    else:
        # As vault.yaml has it: an edit by hand not committed yet may have set it, and it isn't in force until then.
        say(f"redact   {rule} is {state} already{inforce.note(found.waiting)}")
    for note in done.notes:
        say(f"note     {note}")
    if done.by_hand is not None:
        raise typer.Exit(1)


def _ingest_report(report: IngestReport) -> None:
    """Counts, ids and short reasons: never a title, so a never-store recording's name stays out of the terminal."""
    if report.holds_added:
        say(f"holds      {report.source}: {report.holds_added} added")
    for unseen in report.holds_unseen:
        say(f"holds      {unseen} isn't in this import; check hold.txt for a typo")
    for released in report.holds_released:
        say(f"holds      {released} was released with its cuts; not held again")
    for again in report.held_again:
        say(
            f"holds      {again} is held again after its release; the next import with a hold file, or docket hold "
            f"release {report.source} {again} --from <folder>, takes it off"
        )
    staged = report.staged
    say(
        f"staged     {report.source}: {staged.pending} pending, {staged.ready} ready, "
        f"{staged.skipped} already staged or in raw"
    )
    if report.on_hold:
        say(f"on hold    {report.on_hold}")
    if staged.dropped:
        say("dropped    " + ", ".join(f"{reason} {n}" for reason, n in sorted(staged.dropped.items())))
    for entry in report.set_aside:
        where = " ".join(part for part in (entry.tool, entry.key) if part)
        say(f"set aside  {entry.id or '(no id)'}  {entry.reason}" + (f"  {where}" if where else ""))
    for waiting in report.waiting:
        say(f"waiting    {waiting.id}  for {waiting.for_}")
    for rid in report.held_in_raw:
        say(f"in raw     {rid} is on hold but already in raw; it waits for docket forget")
    if report.unreadable:
        places = ", ".join(str(n) for n in report.unreadable)
        say(f"skipped    file(s) {places} of {report.files} in name order: not JSON")


@vault_app.command("convert")
def vault_convert(
    *,
    vault: VaultOption = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would change, and stop.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Convert without asking.")] = False,
    abort: Annotated[bool, typer.Option("--abort", help="Undo a convert that stopped before its commit.")] = False,
    remote_is_private: Annotated[bool, typer.Option(help="Confirm a remote Docket can't check is private.")] = False,
    allow_shared: Annotated[bool, typer.Option(help="Convert into a repo others commit to.")] = False,
) -> None:
    """Make a vault that is a repo of its own a folder of the repo around it, history included."""
    root = _vault_root(vault)
    options = convert.Options(dry_run, yes, abort, remote_is_private, allow_shared)
    convert.convert(root, TyperPrompter(), options)


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
    typer.echo(staged.record.model_dump_json(indent=2, exclude={"text"}))
    _echo_text("text, each line after the character it starts at; a cut is <start>-<end> in these", staged.record.text)
    if staged.cuts:
        _echo_cut(staged.record, staged.cuts)
    if shown.cut_before is not None:
        _echo_text(f"cut before, in raw run {shown.cut_before.run}", shown.cut_before.text)


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
    if staged is not None and staged.cuts:
        _echo_cut(staged.record, staged.cuts)


def _echo_text(heading: str, text: str) -> None:
    """Text as it is, never JSON-escaped, each line after the character offset it starts at, so a cut read off it
    lands where it was read."""
    typer.echo(f"{heading}:")
    offset = 0
    for line in text.split("\n"):
        shown = line.removesuffix("\r")
        typer.echo(f"{offset:>8}  {shown}")
        offset += len(line) + 1


def _echo_cut(record: RawRecord, chosen: list[cuts.Cut]) -> None:
    try:
        _echo_text("after the cuts", cuts.apply(record, chosen).text)
    except DocketError as e:
        typer.echo(f"after the cuts: {e}")


@staging_app.command("promote")
def staging_promote(source: SourceOption = None, vault: VaultOption = None) -> None:
    """Write every ready record to the raw log, cut and redacted, one run per source, and commit."""
    found = find_vault(vault)
    result = Gate(found).promote(source)
    for run in result.runs:
        where = ", ".join(f.relative_to(found.root).as_posix() for f in run.files) or "nothing new"
        redacted = ", ".join(f"{rule} {n}" for rule, n in sorted(run.redactions.items()))
        line = f"{run.source}: {run.written} written ({where}), {run.skipped} already in raw"
        typer.echo(line + (f"; redacted: {redacted}" if redacted else ""))
    for held in result.held:
        typer.echo(f"held      {held.key[:12]} ({held.source}): {held.problem}")
    if result.dropped:
        typer.echo("dropped   " + ", ".join(f"{reason} {n}" for reason, n in sorted(result.dropped.items())))
    if result.on_hold:
        typer.echo(f"on hold   {result.on_hold} (left in staging until the hold is released)")
    if not result.runs and not result.held and not result.on_hold:
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


def say(text: str) -> None:
    """Print a line with its control characters escaped: raw text printed as it is could drive the terminal."""
    typer.echo(printable(text))


def say_all(lines: list[str]) -> None:
    for line in lines:
        say(line)


@doc_app.command("put")
def doc_put(
    source: Annotated[str, typer.Argument(help="The doc's file, or - to read it from stdin.")],
    vault: VaultOption = None,
) -> None:
    """Store a processed doc as its next rev; refused, with every problem, when it can't be."""
    if source == "-":
        data = sys.stdin.buffer.read()
    elif Path(source).is_file():
        data = Path(source).read_bytes()
    else:
        raise DocketError(f"no file {source}")
    try:
        text = data.decode("utf-8").removeprefix("\ufeff")
    except UnicodeDecodeError:
        raise DocketError(f"{source} isn't UTF-8") from None
    try:
        result = recall.doc_put(find_vault(vault), text)
    except DocRefusedError as e:
        say_all(render.refused(e.problems))
        raise typer.Exit(1) from None
    say_all(render.put(result))


@doc_app.command("read")
def doc_read(
    locator: Annotated[str, typer.Argument(help="A doc's id or path, or a record: a rid, or r:<rid>@mm:ss or #n.")],
    view: Annotated[str, typer.Option(help="summary, raw or both.")] = "summary",
    around: Annotated[int, typer.Option(help="Minutes, paragraphs or messages either side.")] = 0,
    rev: Annotated[int | None, typer.Option(help="An earlier revision of a doc.")] = None,
    vault: VaultOption = None,
) -> None:
    """A doc, or a record and the doc lines that point at it: the summary first, the raw for accuracy."""
    say_all(render.read(recall.read(find_vault(vault), locator, view=view, around=around, rev=rev)))


@doc_app.command("verify")
def doc_verify(
    doc: Annotated[str, typer.Argument(help="A doc's id or path.")],
    line: Annotated[list[int] | None, typer.Option("--line", help="A line to check, by its number (repeat).")] = None,
    window: Annotated[int, typer.Option(help="Seconds of transcript shown after a point in time.")] = WINDOW,
    vault: VaultOption = None,
) -> None:
    """Each line of a doc next to the raw its anchors point at."""
    say_all(render.verify(recall.verify(find_vault(vault), doc, lines=line or None, window=window)))


@doc_app.command("redact")
def doc_redact(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Say what would change, and write nothing.")] = False,
    vault: VaultOption = None,
) -> None:
    """Redact the docs already stored, with vault.yaml's settings as they are now: once after upgrading, and again
    after turning a rule on or adding a pattern or a never-store keyword."""
    found = find_vault(vault)
    report = DocStore(found).redact_all(dry_run=dry_run)
    say_all(render.redacted(report, found.config.storage))
    if report.docs and not dry_run and db_path(found).exists():
        # A new file, so the old text isn't left in free pages or the full-text index's deleted entries.
        index.rebuild(found, "docs were redacted")
        say("index    rebuilt here; on another machine, run docket index rebuild after it pulls")
    if report.left:
        raise typer.Exit(1)


@curate_app.command("add")
def curate_add(
    file: Annotated[Path, typer.Argument(help="The changes file: creates and adds, YAML (docket curate --help).")],
    vault: VaultOption = None,
) -> None:
    """Make the entity changes a file lists, all in one commit or none, each logged and undoable:

    changes: [{create: {id: cleo-test, type: person, name: {en: Cleo Test}}},
    {entity: northwind, add: {aliases: [{text: Nortwin, kind: asr}]}}]"""
    if not file.is_file():
        raise DocketError(f"no file {file}")
    v = find_vault(vault)

    def write() -> Written:
        # A change naming a card settles it in this machine's round, and the card's spellings it leaves out are
        # rejected as that entity's aliases.
        planned, unticked = curate.settle(v, curate.load(v), load_changes(file))
        return EntityStore(v).apply(planned, rejects=unticked)

    _curate_write(v, write)


@curate_app.command("undo")
def curate_undo(
    ops: Annotated[list[str], typer.Argument(help="The ops to undo, by the ids docket curate log prints.")],
    vault: VaultOption = None,
) -> None:
    """Undo logged changes, the latest first, in one commit; refused whole when one can't be undone."""
    v = find_vault(vault)
    batch = curate.load(v)
    _curate_write(v, lambda: EntityStore(v).undo(ops, round_id=batch.round if batch is not None else None))


@curate_app.command("log")
def curate_log(
    n: Annotated[int, typer.Option("-n", min=1, help="How many ops, newest first.")] = 20,
    entity: Annotated[str | None, typer.Option("--entity", help="Only this entity's ops, by id.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="The lines as JSON.")] = False,
    vault: VaultOption = None,
) -> None:
    """Every change Docket made to entities, and every rejection, undo and lift, newest first, with the ids undo
    takes. A line a stopped write left is marked: the next write finishes it. What a never-store list holds now is
    hidden, as curate show hides it."""
    v = find_vault(vault)
    read = curatelog.read_all(v.root)
    unfinished = {line.id for line in curatelog.unfinished(v)}
    # Filtered by the entity as logged, then shown by the lists as they are now (DR-0501); ids stay, for undo.
    kept = [line for line in reversed(read.lines) if entity is None or line.entity == entity][:n]
    lines = [curate.hidden_line(line, v.config.never_store) for line in kept]
    if as_json:
        # ASCII only: a name's text can't put a terminal code on the screen.
        dumped = [
            line.model_dump(mode="json", exclude_none=True) | ({"finished": False} if line.id in unfinished else {})
            for line in lines
        ]
        typer.echo(json.dumps(dumped, indent=2))
        return
    say_all(render.curate_log(lines, curatelog.fold(read.lines).undone_by, read.problems, unfinished))


def _curate_write(v: Vault, write: Callable[[], Written]) -> None:
    """Run a curate write and print what it did, or its refusal with exit 1; then measure the round again once its last
    card is decided."""
    try:
        written = write()
    except RefusedError as e:
        say_all(render.curate_refused(e.problems))
        raise typer.Exit(1) from None
    # Its own lines as curate log shows them: the store refuses a listed value, not an id whose slug spells one.
    lines = tuple(curate.hidden_line(line, v.config.never_store) for line in written.lines)
    say_all(render.curate_written(dataclasses.replace(written, lines=lines)))
    batch = curate.load(v)
    if batch is not None and batch.after is None:
        measured = curate.after(v, batch)
        if measured.after is not None:
            say_all(render.curate_measures(measured.before, measured.after))


@curate_app.command("round")
def curate_round(vault: VaultOption = None) -> None:
    """Start a round on this machine: the index brought up to date, at most 15 cards mined from it, ranked by what
    search misses, and your question set measured when curate.questions names one in Docket's config."""
    v = find_vault(vault)
    say_all(render.curate_round(curate.view(v, curate.start(v))))


@curate_app.command("show")
def curate_show(
    as_json: Annotated[bool, typer.Option("--json", help="The round as JSON, snippets included.")] = False,
    vault: VaultOption = None,
) -> None:
    """This machine's round: each card with its state, its spellings, its count and snippets, and what was applied
    without a card since it started."""
    v = find_vault(vault)
    batch = curate.load(v)
    if batch is None:
        raise DocketError("no round on this machine yet; start one with docket curate round")
    shown = curate.view(v, batch)
    if as_json:
        # ASCII only: a snippet's text can't put a terminal code on the screen.
        typer.echo(json.dumps(shown.model_dump(mode="json"), indent=2))
        return
    say_all(render.curate_round(shown))


CardsArg = Annotated[list[str], typer.Argument(help="Cards by id, or by number with --round.")]
RoundOption = Annotated[str | None, typer.Option("--round", help="The round the card numbers are from.")]


@curate_app.command("accept")
def curate_accept(
    cards: CardsArg,
    round_: RoundOption = None,
    entity_type: Annotated[str | None, typer.Option("--type", help="Another type for one new entity.")] = None,
    entity_id: Annotated[str | None, typer.Option("--id", help="Another id for one new entity.")] = None,
    vault: VaultOption = None,
) -> None:
    """Accept cards as they are: a new entity with the card's name and aliases, or new aliases for an entity. To
    change more than a new entity's type or id, write the change to a file naming the card and use docket curate
    add."""
    v = find_vault(vault)
    batch, chosen = curate.cards_for(curate.load(v), cards, round_)
    _curate_write(v, lambda: curate.accept(v, batch, chosen, entity_type=entity_type, entity_id=entity_id))


@curate_app.command("reject")
def curate_reject(cards: CardsArg, round_: RoundOption = None, vault: VaultOption = None) -> None:
    """Reject cards: a new card's name and the spellings that only joined it go on the stoplist, and an alias card's
    aliases for that entity; a label or a written name that stands on its own stays a candidate."""
    v = find_vault(vault)
    batch, chosen = curate.cards_for(curate.load(v), cards, round_)
    _curate_write(v, lambda: curate.reject(v, batch, chosen))


@curate_app.command("lift")
def curate_lift(
    keys: Annotated[list[str], typer.Argument(help="Stoplisted keys, as docket curate log prints them.")],
    vault: VaultOption = None,
) -> None:
    """Take keys off the stoplist, so curation may offer them again."""
    v = find_vault(vault)
    _curate_write(v, lambda: EntityStore(v).lift(keys))


@curate_app.command("status")
def curate_status(
    again: Annotated[bool, typer.Option("--eval", help="Measure your question set now, as the round's after.")] = False,
    vault: VaultOption = None,
) -> None:
    """Whether a round is due, this machine's round, the review minutes per week, the stoplist, and your question set
    before and after."""
    say_all(render.curate_status(curate.status(find_vault(vault), again=again), userconfig.config_path()))


@contextlib.contextmanager
def _query_models(
    no_models: bool, *, measure: bool = False, vault: Vault | None = None
) -> Generator[Models | Off | None]:
    """The models for a command that only asks queries, started for the command and stopped at its end: `docket
    search`'s on the CPU, `docket eval`'s where the reranker runs (DR-0457); None with --no-models."""
    if no_models:
        yield None
        return
    models = open_models(queries_only=not measure, measure=measure, vault=vault)
    if measure and isinstance(models, Models) and models.reranker is not None and models.reranker.device == "cuda":
        say("(the reranker runs on the GPU, so this worker took a few seconds to start)")
    try:
        yield models
    finally:
        if isinstance(models, Models):
            models.close()


def _service_token(vault: Vault | None = None) -> str | None:
    """This machine's token, when the running service recorded itself for this vault (any vault without one); None
    when it didn't or the token can't be read."""
    if vault is not None:
        found = client.recorded()
        named = found.get("vault") if found else None
        if not isinstance(named, str) or Path(named).resolve() != vault.root.resolve():
            return None
    try:
        return _tokens().read()
    except DocketError:
        return None


def _service_models(vault: Vault | None = None) -> dict[str, Any] | None:
    """The running service's models in its own words, when it runs for this vault (any vault without one); None when
    no Docket service answers with this machine's token."""
    token = _service_token(vault)
    if token is None:
        return None
    answer = client.call("GET", "/api/models", token=token, at=client.port())
    return answer.body if answer is not None and answer.status == 200 else None


def _embed(vault: Vault, no_models: bool) -> None:
    """The embedding pass after an update or a rebuild, with a progress line now and then (DR-0336); left to the
    service when it runs for the vault with the same model."""
    if no_models:
        return
    models = open_models(start=False, rerank=False, vault=vault)
    if isinstance(models, Off):
        if models.why:
            say(f"! {models.why}")
        return
    try:
        service = _service_models(vault)
        if service is not None:
            if service.get("state") != "off" and service.get("model") == models.embedder.name:
                say("vectors    the service embeds what is new")
                return
            # A service started before the install reads no models until it starts again (DR-0330).
            say(
                f"! the service runs without {models.embedder.name}, so this command embeds; docket service stop, "
                "then docket service install, and the service embeds after its updates"
            )
        models.worker.ensure()
        if models.why is not None:
            say(f"! {models.why}")
            return
        shown = [time.monotonic()]

        def progress(have: int, total: int) -> None:
            if time.monotonic() - shown[0] >= PROGRESS_SECONDS:
                shown[0] = time.monotonic()
                say(f"vectors    {have} of {total} chunks")

        report = fill(vault, models, progress=progress)
        held = f", {report.held} held back by the vault's settings" if report.held else ""
        say(f"vectors    {report.model}  {report.have} of {report.total} chunks ({report.embedded} embedded{held})")
        if report.why:
            say(f"! {report.why}")
    finally:
        models.close()


def _report(report: index.BuildReport) -> None:
    if report.rebuilt:
        say(f"rebuilt{f' ({report.reason})' if report.reason else ''} in {report.seconds:.1f} s")
    else:
        entities = ", the entity files" if report.entities else ""
        say(f"updated in {report.seconds:.1f} s: {report.runs} run file(s), {report.docs} doc(s){entities}")
    say("  ".join(f"{table} {n}" for table, n in report.counts.items()))
    for problem in report.problems:
        say(f"! {problem}")
    if report.problems:
        say(f"{len(report.problems)} problem(s); the rest is indexed")


@index_app.command("rebuild")
def index_rebuild(no_models: NoModels = False, vault: VaultOption = None) -> None:
    """Build this machine's index from the vault, from scratch, then embed what has no vector."""
    v = find_vault(vault)
    _report(index.rebuild(v))
    _embed(v, no_models)


@index_app.command("update")
def index_update(no_models: NoModels = False, vault: VaultOption = None) -> None:
    """Index what changed in the vault since the last build, rebuilding when it has to, then embed what is new."""
    v = find_vault(vault)
    _report(index.update(v))
    _embed(v, no_models)


@index_app.command("status")
def index_status(vault: VaultOption = None) -> None:
    """Whether the index is there and current, its languages, counts and problems."""
    st = index.status(find_vault(vault))
    say(f"index      {st.path}")
    if not st.exists:
        say("no index yet; run docket index rebuild")
        raise typer.Exit(1)
    if st.stale is not None:
        say(f"needs a rebuild: {st.stale}")
        raise typer.Exit(1)
    say(f"languages  {', '.join(st.languages)} (columns {', '.join(st.columns)})")
    say("counts     " + "  ".join(f"{table} {n}" for table, n in st.counts.items()))
    for coverage in st.vectors:
        say(f"vectors    {coverage.model}  {coverage.have} of {coverage.total} chunks")
    if st.new_runs or st.changed_docs or st.entities_changed:
        entities = ", the entity files" if st.entities_changed else ""
        say(f"new since  {st.new_runs} run file(s), {st.changed_docs} doc(s){entities}: docket index update")
    for problem in st.problems:
        say(f"! {problem}")
    if st.changed_runs:
        say(f"{st.changed_runs} indexed run file(s) changed or went: run docket index rebuild")
        raise typer.Exit(1)


@app.command("search")
def search_command(
    query: Annotated[list[str], typer.Argument(help='Words, "quoted phrases", names, and time words like last week.')],
    *,
    since: Annotated[str | None, typer.Option(help="From this date or time, such as 2026-10-01.")] = None,
    until: Annotated[str | None, typer.Option(help="Up to this date (the whole day) or time.")] = None,
    source: Annotated[list[str] | None, typer.Option("--source", help="Only this source (repeat).")] = None,
    person: Annotated[list[str] | None, typer.Option("--person", help="Lift what this person said (repeat).")] = None,
    entity: Annotated[list[str] | None, typer.Option("--entity", help="Lift what mentions this (repeat).")] = None,
    k: Annotated[int, typer.Option("-k", help="How many hits, 1 to 100.")] = 20,
    no_update: Annotated[bool, typer.Option("--no-update", help="Search the index as it is.")] = False,
    no_models: NoModels = False,
    vault: VaultOption = None,
) -> None:
    """Search everything kept: hits grouped by recording or thread, each with its locator for docket doc read."""
    v = find_vault(vault)
    text, people, entities, sources = " ".join(query), person or [], entity or [], source or []
    start, end = index.parse_when(since, "--since"), index.parse_when(until, "--until")
    # The service's search brings the index up to date and uses its models, so --no-update and --no-models stay here,
    # and the command updates the index itself only when it searches by itself.
    asked = {"query": text, "people": people, "entities": entities, "sources": sources, "since": since, "until": until}
    result = None if no_update or no_models else _service_search(v, {**asked, "k": k})
    if result is None:
        if not no_update:
            report = index.update(v)
            if report.rebuilt:
                say(f"(the index was rebuilt: {report.reason})")
                for problem in report.problems:
                    if problem.startswith("vault.yaml"):
                        say(f"! {problem}")
        with _query_models(no_models, vault=v) as models:
            result = index.search(
                v, text, people=people, entities=entities, sources=sources, since=start, until=end, k=k, models=models
            )
    say_all(render.search(result, ZoneInfo(v.config.owner.time_zone)))


def _service_search(vault: Vault, asked: dict[str, Any]) -> index.SearchResult | None:
    """The running service's answer, when it serves this vault, answers with this machine's token and searches with
    the embedder this command would (DR-0533): it keeps its worker and its reranker loaded. None for anything else, a
    refusal included, and the command searches by itself as before, so an error reads as it always did. A service
    still building the index after its wait is said, since the command's own update then waits for that build."""
    token = _service_token(vault)
    if token is None:
        return None
    at = client.port()
    status = client.call("GET", "/api/models", token=token, at=at)
    if status is None or status.status != 200 or not _same_embedder(status.body, vault):
        return None
    given = {key: value for key, value in asked.items() if value is not None}
    path = f"/api/search?{urlencode(given, doseq=True)}"
    answer = client.call("GET", path, token=token, at=at, timeout=SERVICE_SEARCH_SECONDS)
    if answer is not None and answer.body.get("error") == BUILDING:
        say("(the service is building the index; searching when it's done)")
    if answer is None or answer.status != 200:
        return None
    try:
        return index.SearchResult.model_validate(answer.body)
    except ValidationError:
        return None


def _same_embedder(status: dict[str, Any], vault: Vault) -> bool:
    """Whether the service's models, in its own words, search as this command's would: both off, or its worker up
    with the same embedder. A service started before an install or a remove, or whose worker is down, would answer
    without the vectors this command has, or with vectors it hasn't."""
    models = open_models(start=False, rerank=False, vault=vault)
    if isinstance(models, Off):
        return status.get("state") == "off"
    try:
        return status.get("state") == "up" and status.get("model") == models.embedder.name
    finally:
        models.close()


@app.command("eval")
def eval_command(
    file: Annotated[Path, typer.Argument(help="Your question file, YAML of schema 1. It is only read.")],
    *,
    k: Annotated[int, typer.Option("-k", "--k", help="Score the top k hits, 1 to 100.")] = questions.K,
    as_json: Annotated[bool, typer.Option("--json", help="The numbers and each question's ranks, as JSON.")] = False,
    no_models: NoModels = False,
    vault: VaultOption = None,
) -> None:
    """Measure search with your own questions: recall at k and MRR per kind of question. Writes nothing."""
    v = find_vault(vault)
    asked = questions.load(file, ZoneInfo(v.config.owner.time_zone))
    with _query_models(no_models, measure=True, vault=v) as models:
        report = questions.run(v, asked, k=k, models=models)
    if as_json:
        # ASCII only: a question's text can't put a terminal code on the screen.
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        say_all(render.eval_report(report))


@import_app.command("notes")
def import_notes(
    folder: Annotated[Path, typer.Argument(help="The notes folder. It is only read, never changed.")],
    as_: Annotated[
        str | None,
        typer.Option(
            "--as", help="What old paths start with; default: the folder's path from its repo's root; . for none."
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Say what would happen and write nothing.")] = False,
    take: Annotated[
        list[str] | None,
        typer.Option(
            help="For a note changed in the folder and in the vault: note:<hash>:<path> or vault:<hash>:<path>."
        ),
    ] = None,
    vault: VaultOption = None,
) -> None:
    """Import a notes folder's markdown notes as legacy notes that keep their old paths; run again for changes."""
    takes = [notes.Take.parse(t) for t in take or []]
    report = notes.import_notes(find_vault(vault), folder, as_, dry_run=dry_run, takes=takes)
    sample = f"  (old paths look like {report.sample})" if report.sample else ""
    say(f"prefix   {report.prefix or '.'}{sample}")
    for name in ("new", "updated", "covers", "same"):
        say(f"{name:<8} {getattr(report, name)}")
    if report.gone:
        say(f"gone     {report.gone} (their docs stay, so old references still resolve)")
    for kept in report.kept:
        say(f"kept     {kept.old_path}: edited in the vault and changed in the folder")
        say(f'         keep the note:  run again with --take "note:{kept.hash}:{kept.old_path}"')
        say(f'         keep the vault: run again with --take "vault:{kept.hash}:{kept.old_path}"')
    for gone in report.deleted:
        say(f"deleted  {gone.old_path}: its doc was deleted in the vault")
        say(f'         bring it back: run again with --take "note:{gone.hash}:{gone.old_path}"')
    for line in report.refused:
        say(f"refused  {line}")
    if report.skipped:
        say("skipped  " + ", ".join(f"{why} {n}" for why, n in report.skipped.items()))
    for line in report.reported:
        say(f"         {line}")
    if report.redactions:
        say("redacted " + ", ".join(f"{n} {rule}" for rule, n in report.redactions.items()))
    if report.dry_run:
        say("dry run: nothing written")
    elif report.commit:
        say(f"commit   {report.commit[:12]}")
    if not report.ok:
        raise typer.Exit(1)


PortOption = Annotated[int | None, typer.Option("--port", help="The port on 127.0.0.1; default: the config, or 8741.")]
ServiceVaultOption = Annotated[Path | None, typer.Option("--vault", help="The vault; default: the one install chose.")]


@app.command()
def serve(
    vault: ServiceVaultOption = None,
    port: PortOption = None,
    env_file: Annotated[
        Path | None, typer.Option("--env-file", help="The environment install captured, applied first.")
    ] = None,
) -> None:
    """Run the service in the foreground: the MCP endpoint, the REST API and the web app, on 127.0.0.1."""
    # The service's packages (FastAPI, the MCP SDK, uvicorn) load only for the commands that need them.
    from docket.service import install, server  # noqa: PLC0415

    if env_file is not None:
        install.apply_environment(env_file)
    server.serve(vault.resolve() if vault else None, port, said=typer.echo)


def _tokens() -> Tokens:
    return Tokens()


def _installer(tokens: Tokens) -> Installer:
    from docket.service import install  # noqa: PLC0415

    def stop_pid() -> None:
        try:
            token: str | None = tokens.read()
        except DocketError:
            token = None
        install.stop_recorded_pid(token)

    return install.installer_for(stop_pid=stop_pid)


@service_app.command("install")
def service_install(vault: ServiceVaultOption = None, port: PortOption = None) -> None:
    """Register the service to start at your logon, and start it; run again after an upgrade."""
    from docket.service import install  # noqa: PLC0415

    tokens = _tokens()
    done = install.install(
        vault.resolve() if vault else None, port, installer=_installer(tokens), tokens=tokens, environ=os.environ
    )
    say(f"installed as {done.how}")
    say(f"vault    {done.vault}")
    say(f"port     {done.port}")
    if done.token.moved_from is not None:
        say(f"token    moved from the {done.token.moved_from} store to {done.store}: the old one was gone")
    elif done.token.written:
        say(f"token    made, in {done.store}")
    else:
        say(f"token    in {done.store}")
    if done.problem is not None:
        say(f"! {done.problem}")
        raise typer.Exit(1)
    say("running")


@service_app.command("stop")
def service_stop() -> None:
    """Stop the service until `docket service install` starts it again."""
    _installer(_tokens()).stop()
    say("stopped")


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Stop the service and remove its registration; the token and the config stay."""
    _installer(_tokens()).uninstall()
    say("uninstalled")


@service_app.command("status")
def service_status(
    quiet: Annotated[bool, typer.Option("--quiet", help="One line, for a hook.")] = False,
) -> None:
    """Whether the service is installed and running, on which vault and port, and where its token lives."""
    from docket.service import install  # noqa: PLC0415

    tokens = _tokens()
    st = install.status(_installer(tokens), tokens)
    if quiet:
        say(st.line)
    else:
        say(f"installed  {st.installed or 'no'}")
        say(f"running    {'yes' if st.running else 'no'}, port {st.port}")
        for key in ("pid", "version", "started", "vault", "machine"):
            if key in st.body:
                say(f"{key:<10} {st.body[key]}")
        found: object = st.body.get("models")
        models = cast(dict[str, object], found) if isinstance(found, dict) else {}
        if models.get("state", "off") != "off" or models.get("why"):
            state = f"{models.get('model')}, {models.get('state')}" if models.get("model") else "off"
            why = models.get("why")
            say(f"models     {state}{f': {why}' if why else ''}")
        if st.body.get("vault_problem"):
            say(f"! {st.body['vault_problem']}")
        say(f"token      {st.store}")
        if st.store_problem:
            say(f"! {st.store_problem}")
        if "PATH" in st.environment:
            say(f"PATH       {st.environment['PATH']}")
        say(f"log        {userconfig.log_dir() / 'service.log'}")
        if st.warning:
            say(f"! {st.warning}")
    if not st.running:
        raise typer.Exit(1)


@service_app.command("token")
def service_token(
    header: Annotated[bool, typer.Option("--header", help="As the JSON Claude Code's headersHelper reads.")] = False,
    rotate: Annotated[bool, typer.Option("--rotate", help="Make a new token, or the first one.")] = False,
) -> None:
    """Print the service's token, for a client on this machine."""
    tokens = _tokens()
    if rotate:
        installer = _installer(tokens)
        registered = installer.installed() is not None
        if registered:
            # Stopped before the rotation: under the Run key, stopping confirms the service's pid with the old token.
            installer.stop()
        made = tokens.rotate()
        if made.moved_from is not None:
            say(f"the {made.moved_from} store was gone; the token now lives in {tokens.where()}")
        if registered:
            from docket.service import install  # noqa: PLC0415

            installer.install(install.launch_for())
            say("a new token; the service restarted with it")
        else:
            say("a new token; restart `docket serve` to use it")
        return
    typer.echo(tokens.header() if header else tokens.read())


@models_app.command("install")
def models_install(
    device: Annotated[str, typer.Option("--device", help="auto, cpu or cuda (an NVIDIA GPU).")] = "auto",
    no_reranker: Annotated[
        bool, typer.Option("--no-reranker", help="Leave the reranker out, and remove its files if they are here.")
    ] = False,
    api: Annotated[
        bool, typer.Option("--api", help="Use Voyage AI's API instead, at your own console; nothing is installed.")
    ] = False,
) -> None:
    """Build the models' environment, fetch the embedder's and the reranker's files, check they run, time the
    reranker, and turn models on here; or, with --api, choose Voyage AI's API for this machine."""
    if api:
        from docket.models import api as api_models  # noqa: PLC0415

        chosen = api_models.choose(reranker=not no_reranker)
        reranker = chosen.reranker.name if chosen.reranker is not None else "no reranker"
        say(f"models     {chosen.embedder.name} and {reranker} through Voyage AI's API, chosen for this machine")
        say(f"sends      at most {chosen.monthly_tokens:,} tokens a month (api_monthly_tokens in your config)")
        say(f"next       {api_models.KEY_HINT}; then docket models allow voyage in each vault, at your console")
        say("then       docket service install, so the service reads its models again")
        return
    from docket.models import install  # noqa: PLC0415

    install.install(device, rerank=not no_reranker, say=say)


@models_app.command("key")
def models_key(
    provider: Annotated[str, typer.Argument(help="The api provider: voyage.")],
    forget: Annotated[bool, typer.Option("--forget", help="Remove the key from this machine's keychain.")] = False,
) -> None:
    """Keep the api provider's key in this machine's keychain, typed at your own console and never shown; or forget
    it, which stops sending from this machine at once."""
    import getpass  # noqa: PLC0415

    from docket.models import api as api_models  # noqa: PLC0415
    from docket.models.voyage import KeyStore  # noqa: PLC0415

    if api_models.check_provider(provider) is None:
        raise DocketError("give the provider whose key this is: voyage")
    if forget:
        had = KeyStore().forget()
        say("key        forgotten: this machine sends nothing to Voyage AI" if had else "key        none was kept here")
        return
    api_models.keep_key(lambda: getpass.getpass("Voyage API key (not shown): "))
    say("key        kept in this machine's keychain, as docket / voyage-api-key")


@models_app.command("allow")
def models_allow(
    ctx: typer.Context,
    provider: Annotated[str, typer.Argument(help="voyage, or none to stop.")],
    vault: VaultOption = None,
) -> None:
    """Allow this vault's text to go to an api provider, or none, in vault.yaml; asked at your own console either way,
    and committed."""
    from docket.models import api as api_models  # noqa: PLC0415

    named: Path | None = vault if vault is not None else ctx.obj
    found = find_vault(named)
    wanted = api_models.check_provider(provider)
    done = api_models.allow(found, wanted)
    if done.declined:
        say("api        not changed: the code wasn't typed back")
        raise typer.Exit(1)
    if done.by_hand is not None:
        say(f"vault.yaml has comments or a layout of your own, so it is left as it is; set it there: {done.by_hand}")
        raise typer.Exit(1)
    what = f"allows {wanted}" if wanted is not None else "allows no api provider"
    if done.changed:
        say(f"api        this vault {what}" + (f", committed {done.commit[:12]}" if done.commit else ""))
    else:
        say(f"api        this vault {what} already{inforce.note(found.waiting)}")


@models_app.command("status")
def models_status(
    check: Annotated[bool, typer.Option("--check", help="Start the worker once and say what it runs on.")] = False,
    vault: VaultOption = None,
) -> None:
    """What this machine's models are: the choice, the environments, the files, the vault's vectors and the service's
    worker."""
    from docket.models import api as api_models  # noqa: PLC0415
    from docket.models import config, report  # noqa: PLC0415
    from docket.models.config import ApiChoice, RerankTiming  # noqa: PLC0415
    from docket.models.provider import Off  # noqa: PLC0415
    from docket.models.runtime import rerank_spec, timed, worker_for  # noqa: PLC0415

    found_report = report.read()
    choice = found_report.choice
    say_all(report.lines(found_report))
    if isinstance(choice, ApiChoice):
        opened = _vault_or_none(vault)
        say_all(api_models.status(opened))
    _models_vectors(vault, on=found_report.on)
    service = _service_models()
    if service is not None:
        said, errors = report.service_lines(service)
        say_all([f"service    {line}" for line in said] + errors)
    if check:
        if isinstance(choice, ApiChoice):
            _check_api(_vault_or_none(vault))
            return
        found = choice if isinstance(choice, Off) else config.installed(choice.device)
        if isinstance(choice, Off) or isinstance(found, (Off, str)):
            say(f"! {found if isinstance(found, str) else 'models are off'}")
            raise typer.Exit(1)
        timing = timed(choice, found)
        rerank = None
        if isinstance(timing, RerankTiming) and choice.reranker is not None:
            rerank = rerank_spec(choice.reranker, timing.batch_seconds)
        worker = worker_for(choice, choice.device, rerank)
        try:
            if not worker.ensure() or worker.hello is None:
                say(f"! {worker.why}")
                say_all(worker.stderr().strip().splitlines()[-10:])
                raise typer.Exit(1)
            say(f"worker     up: {worker.hello.provider}, {worker.hello.dims} dims")
            # A reranker the install couldn't run fails the check too; one only too slow here doesn't.
            ran, chosen = found.rerank, choice.reranker
            failed = ran is not None and chosen is not None and ran.fits(chosen) and ran.seconds is None
            if not _check_reranker(worker) or failed:
                raise typer.Exit(1)
        finally:
            worker.stop()


def _vault_or_none(vault: Path | None) -> Vault | None:
    try:
        return find_vault(vault)
    except DocketError:
        return None


def _check_api(vault: Vault | None) -> None:
    """--check for the api provider: one query's vector, sent only as any search's would be."""
    models = open_models(start=False, rerank=False, vault=vault)
    if isinstance(models, Off):
        say(f"! {models.why or 'models are off'}")
        raise typer.Exit(1)
    try:
        got = models.query_vector("a question")
    finally:
        models.close()
    if isinstance(got, str):
        say(f"! {got}")
        raise typer.Exit(1)
    say(f"api        up: {models.embedder.name}, {len(got) // 4} dims")


def _check_reranker(worker: Worker) -> bool:
    """--check's reranker line: one pair scored, or why the worker runs none; False when it should and can't."""
    hello = worker.hello
    if hello is None or hello.reranker is None:
        return True
    if hello.reranker.off is not None:
        say(f"reranker   ! off in the worker: {hello.reranker.off}")
        return False
    got = worker.rerank("a question", ["a passage"], None)
    say(f"reranker   up: {hello.reranker.provider}" if isinstance(got, list) else f"reranker   ! {got.why}")
    return isinstance(got, list)


def _models_vectors(vault: Path | None, *, on: bool) -> None:
    """Each model's vectors in the vault's index (DR-0332), when a vault resolves and its index reads."""
    try:
        st = index.status(find_vault(vault))
    except DocketError:
        if vault is not None:
            raise
        return
    for coverage in st.vectors:
        say(f"vectors    {coverage.model}  {coverage.have} of {coverage.total} chunks ({st.path})")
    if on and st.exists and not st.vectors:
        say(f"vectors    none yet in {st.path}; docket index update embeds")


@models_app.command("remove")
def models_remove() -> None:
    """Delete the models' environments, files and choice; keyword search goes on."""
    from docket.models import install  # noqa: PLC0415

    install.remove(say=say)


@hook_app.command("session-start")
def hook_session_start() -> None:
    """What state Docket is in, as JSON for Claude Code's SessionStart hook. It reads nothing from stdin and always
    exits 0: a failure is a line in the JSON, never a failed session."""
    from docket import hooks  # noqa: PLC0415
    from docket.service import install  # noqa: PLC0415

    def status() -> install.ServiceStatus:
        tokens = _tokens()
        return install.status(_installer(tokens), tokens)

    # ASCII on the wire, whatever the console's code page: JSON escapes every other character.
    typer.echo(json.dumps(hooks.session_start(status, os.environ)))


@hook_app.command("post-tool-use")
def hook_post_tool_use() -> None:
    """The capture hook, as JSON for Claude Code's PostToolUse hook: it ties a run to its session and posts the
    captures an open run wants. `docket.entry` answers it before the CLI loads; this is the same hook, for help and
    tests. It always exits 0."""
    from docket import capture  # noqa: PLC0415

    capture.main()


@app.command("open")
def open_app(
    path: Annotated[
        str | None,
        typer.Argument(
            help='A page of the web app to land on, such as "/search?query=offer", as its sign-in page names it.'
        ),
    ] = None,
) -> None:
    """Open the web app in your browser, through a link good once and for a minute."""
    from urllib.parse import quote  # noqa: PLC0415

    from docket.service import client, sessions  # noqa: PLC0415

    if path is not None and sessions.safe_next(path) != path:
        raise DocketError(f"{path!r} isn't a page of the web app; give its path, such as /search?query=offer")
    token = _tokens().read()
    answer = client.call("POST", "/api/session", token=token)
    if answer is None or answer.status != 200:
        raise DocketError("the service isn't answering; `docket service status` says why")
    url = str(answer.body["url"])
    if path is not None:
        url += "&next=" + quote(path, safe="")
    page = sessions.write_redirect(url)
    opened = webbrowser.open(page.as_uri())
    # Printed whether or not a browser opened: a snap or Flatpak browser can't open a file in a hidden folder.
    say(f"{'opened Docket in your browser; ' if opened else ''}the link, good once and for a minute: {url}")


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
