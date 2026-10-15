"""The `docket` command."""

from __future__ import annotations

import os
import sys
import webbrowser
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from docket import __version__, index, recall, render, userconfig
from docket.errors import DocketError
from docket.imports import notes
from docket.render import printable
from docket.service.platforms import Installer
from docket.service.token import Tokens
from docket.sources.contract import Manifest
from docket.sources.ingest import IngestReport, ingest_run
from docket.sources.runner import MapperFailedError
from docket.vault import convert, cuts
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
    storage: Annotated[
        str | None, typer.Option(help="folder (a folder of the repo around it) or repo (a repo of its own).")
    ] = None,
    remote: Annotated[str | None, typer.Option(help="none, a path or URL, or github[:name].")] = None,
    remote_is_private: Annotated[bool, typer.Option(help="Confirm a remote Docket can't check is private.")] = False,
    allow_shared: Annotated[bool, typer.Option(help="Keep a folder vault in a repo others commit to.")] = False,
    allow_dirty_parent: Annotated[bool, typer.Option(help="Nest in a repo with uncommitted changes.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Take the defaults and don't ask.")] = False,
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
        storage=storage,
        remote=remote,
        remote_is_private=remote_is_private,
        allow_shared=allow_shared,
        allow_dirty_parent=allow_dirty_parent,
        yes=yes,
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
    s = find_vault(vault).status()
    typer.echo(f"vault    {s.root}")
    typer.echo(f"storage  {f'a folder of {s.host}' if s.host else 'a repo of its own'}, on {s.branch}")
    if s.branch_warning:
        typer.echo(f"warning  {s.branch_warning}")
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


def _ingest_report(report: IngestReport) -> None:
    """Counts, ids and short reasons: never a title, so a never-store recording's name stays out of the terminal."""
    if report.holds_added:
        say(f"holds      {report.source}: {report.holds_added} added")
    for unseen in report.holds_unseen:
        say(f"holds      {unseen} isn't in this import; check hold.txt for a typo")
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
        where = run.run.relative_to(found.root).as_posix() if run.run is not None else "nothing new"
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
def index_rebuild(vault: VaultOption = None) -> None:
    """Build this machine's index from the vault, from scratch."""
    _report(index.rebuild(find_vault(vault)))


@index_app.command("update")
def index_update(vault: VaultOption = None) -> None:
    """Index what changed in the vault since the last build; rebuild when it has to."""
    _report(index.update(find_vault(vault)))


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
    vault: VaultOption = None,
) -> None:
    """Search everything kept: hits grouped by recording or thread, each with its locator for docket doc read."""
    v = find_vault(vault)
    if not no_update:
        report = index.update(v)
        if report.rebuilt:
            say(f"(the index was rebuilt: {report.reason})")
            for problem in report.problems:
                if problem.startswith("vault.yaml"):
                    say(f"! {problem}")
    result = index.search(
        v,
        " ".join(query),
        people=person or [],
        entities=entity or [],
        sources=source or [],
        since=index.parse_when(since, "--since"),
        until=index.parse_when(until, "--until"),
        k=k,
    )
    say_all(render.search(result, ZoneInfo(v.config.owner.time_zone)))


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


@app.command("open")
def open_app() -> None:
    """Open the web app in your browser, through a link good once and for a minute."""
    from docket.service import client, sessions  # noqa: PLC0415

    token = _tokens().read()
    answer = client.call("POST", "/api/session", token=token)
    if answer is None or answer.status != 200:
        raise DocketError("the service isn't answering; `docket service status` says why")
    url = str(answer.body["url"])
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
