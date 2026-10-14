"""The `docket` command."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from docket import __version__, index, recall, userconfig
from docket.errors import DocketError
from docket.imports import notes
from docket.vault import cuts
from docket.vault.anchors import format_time
from docket.vault.gate import Gate, Label
from docket.vault.processed import DocRefusedError
from docket.vault.records import RawRecord
from docket.vault.spans import WINDOW, Span
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


# C0 and C1 control characters and DEL, but newline and tab: raw text printed as it is could drive the terminal
# (OSC 52 sets the clipboard).
CONTROL = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f]")


def printable(text: str) -> str:
    return CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def say(text: str) -> None:
    typer.echo(printable(text))


def _span(s: Span, indent: str) -> None:
    head = f"{indent}{s.anchor}"
    if s.ts is not None:
        head += f"  {s.kind} {s.ts:%Y-%m-%d %H:%M}Z"
    if s.author:
        head += f" {s.author}"
    if s.versions > 1:
        head += f" ({s.versions} versions; this is the latest)"
    say(head)
    if s.problem is not None:
        say(f"{indent}  ! {s.problem}")
    elif s.segments:
        for seg in s.segments:
            say(f"{indent}  {format_time(seg.start_ms // 1000)}  {seg.speaker or '?'}: {seg.text}")
    else:
        for line in s.text.split("\n"):
            say(f"{indent}  {line}")


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
        for problem in e.problems:
            say(str(problem))
        say(f"refused: {len(e.problems)} problem(s)")
        raise typer.Exit(1) from None
    if result.taken_in:
        say(f"a hand edit went in first, as rev {result.taken_in}")
    say(f"{'put' if result.changed else 'unchanged'} {result.id} rev {result.rev}  {result.path}")


@doc_app.command("read")
def doc_read(
    locator: Annotated[str, typer.Argument(help="A doc's id or path, or a record: a rid, or r:<rid>@mm:ss or #n.")],
    view: Annotated[str, typer.Option(help="summary, raw or both.")] = "summary",
    around: Annotated[int, typer.Option(help="Minutes, paragraphs or messages either side.")] = 0,
    rev: Annotated[int | None, typer.Option(help="An earlier revision of a doc.")] = None,
    vault: VaultOption = None,
) -> None:
    """A doc, or a record and the doc lines that point at it: the summary first, the raw for accuracy."""
    result = recall.read(find_vault(vault), locator, view=view, around=around, rev=rev)
    if result.resolved is not None and result.resolved != locator.strip():
        say(f"read as {result.resolved}")
    for problem in result.problems:
        say(f"! {problem}")
    if result.doc is not None:
        say(f"{result.doc.id} rev {result.doc.rev}  {result.doc.path}")
        if result.doc.superseded_by:
            say(f"superseded by {', '.join(result.doc.superseded_by)}")
        for line in result.doc.lines:
            say(f"{line.n:>4}  {line.text}")
    elif result.view != "raw" and not result.lines and not result.legacy:
        say("no current doc points at it yet")
    for cited in result.lines:
        say(f"{cited.doc} rev {cited.rev} line {cited.n}: {cited.text}")
    for ref in result.legacy:
        say(f"{ref.id}, a legacy note, covers it: {', '.join(ref.old_paths)}")
    for s in result.spans:
        _span(s, "")


@doc_app.command("verify")
def doc_verify(
    doc: Annotated[str, typer.Argument(help="A doc's id or path.")],
    line: Annotated[list[int] | None, typer.Option("--line", help="A line to check, by its number (repeat).")] = None,
    window: Annotated[int, typer.Option(help="Seconds of transcript shown after a point in time.")] = WINDOW,
    vault: VaultOption = None,
) -> None:
    """Each line of a doc next to the raw its anchors point at."""
    result = recall.verify(find_vault(vault), doc, lines=line or None, window=window)
    say(f"{result.doc} rev {result.rev}")
    for checked in result.lines:
        say(f"{checked.n:>4}  {checked.text}")
        for problem in checked.problems:
            say(f"        ! {problem}")
        for s in checked.spans:
            _span(s, "        ")


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


def _when(text: str | None, what: str) -> date | datetime | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text) if len(text) == len("2026-10-14") else datetime.fromisoformat(text)
    except ValueError:
        raise DocketError(f"{what} is a date or a time, such as 2026-10-14 or 2026-10-14T09:30") from None


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
        since=_when(since, "--since"),
        until=_when(until, "--until"),
        k=k,
    )
    tz = ZoneInfo(v.config.owner.time_zone)
    reading = result.query
    if reading.since is not None or reading.until is not None:
        span = [
            f"{word} {at.astimezone(tz):%Y-%m-%d %H:%M}"
            for word, at in (("from", reading.since), ("to", reading.until))
            if at is not None
        ]
        said = f" ({', '.join(reading.time_words)})" if reading.time_words else ""
        say(" ".join(span) + said)
    for found in reading.entities:
        say(f"{found.words!r} is {found.id}")
    if reading.unknown:
        say(f"not found as a person or entity: {', '.join(reading.unknown)}")
    if reading.left_out:
        say(f"too common to search for: {', '.join(reading.left_out)}")
    if not result.groups:
        say("nothing found")
    for group in result.groups:
        when = f"  {group.first.astimezone(tz):%Y-%m-%d}" if group.first else ""
        say(f"\n{group.kind}  {group.title}  ({group.source or 'docs'}{when})")
        if group.summary is not None:
            say(f"  summary  {group.summary.id} rev {group.summary.rev}: {group.summary.title}")
        for hit in group.hits:
            at = f"  {hit.ts.astimezone(tz):%Y-%m-%d %H:%M}" if hit.ts else ""
            who = ", ".join(hit.speakers or hit.authors)
            lines = f"  lines {hit.lines[0]}-{hit.lines[1]}" if hit.lines else ""
            say(f"  {hit.rank:>3}. {hit.locator}{lines}{at}{f'  {who}' if who else ''}")
            say(f"       {hit.snippet}")
            for anchor in hit.anchors:
                say(f"       -> {anchor}")
            if hit.also:
                say(f"       also {', '.join(hit.also)}")


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
