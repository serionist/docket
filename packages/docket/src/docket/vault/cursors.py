"""Each machine's sync cursors (DR-0245): where its syncs of each source reached, and what they still owe.

`state/cursors-<machine>.yaml` is committed, written only by its machine, and holds per source the `mark` (a quoted UTC
time), the `lookback_days`, the `retry` set (`id`, `reason`, `since`) and the `waiting` set (`id`, `for`, `since`). Ids
and times only, never a title. This machine's file fails closed: a bad line, an unknown key, an unquoted time, a merge
conflict's marker or a link refuses every run, named by file and line number. Another machine's file that doesn't read
is left out with a note, since its marks only ever shorten a window.

The rules a run follows (DR-0607, DR-0247):

- a run's window starts at the latest mark of any machine (a mark from a clock ahead of this one left out), less the
  source's lookback, vault.yaml's `lookback_days` when set and else this machine's, and ends when the run opened;
- a whole close that kept a capture, and whose window started no later than that, moves the mark to its open time; a
  final close never does;
- a close updates the retry and waiting sets by the tables in the plan, and expires what has waited too long.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import yaml

from docket.errors import DocketError
from docket.sources.contract import TOKEN
from docket.vault.machine import NAME as MACHINE_NAME
from docket.vault.records import is_native_id, source_name_problem
from docket.vault.staging import write_replacing
from docket.vault.vault import Vault

STATE = "state"
SCHEMA_VERSION = 1
LOOKBACK_DAYS = 7
MAX_LOOKBACK_DAYS = 366
# A retry id held back for the budget waits as long as it takes; any other reason, `gone` among them, a month.
LATER = "later"
GONE = "gone"
EXPIRES = timedelta(days=30)
# Ids waiting this long, and retry ids not held back for the budget, are due a final try.
FINAL_AFTER = timedelta(days=7)
# Another machine's sets are handed over while its mark is younger than this: a retired machine's ids stop.
OTHERS_FOR = timedelta(days=30)
FILE = re.compile(r"^cursors-(?P<machine>[^/\\]+)\.yaml$")
CONFLICT = ("<<<<<<<", "=======", ">>>>>>>")
HEADER = "# Where this machine's syncs reached. Docket writes it after each whole run; ids and times only.\n"
_STR = "tag:yaml.org,2002:str"
_INT = "tag:yaml.org,2002:int"
_QUOTED = ('"', "'")


@dataclass(frozen=True)
class Retry:
    id: str
    reason: str
    since: date


@dataclass(frozen=True)
class Waiting:
    id: str
    for_: str
    since: date


@dataclass
class Cursor:
    """One source's cursor on one machine."""

    mark: datetime | None = None
    lookback_days: int = LOOKBACK_DAYS
    retry: dict[str, Retry] = field(default_factory=dict[str, Retry])
    waiting: dict[str, Waiting] = field(default_factory=dict[str, Waiting])


Cursors = dict[str, Cursor]


# -- reading ---------------------------------------------------------------------------------------------------------


class _BadLineError(Exception):
    def __init__(self, line: int, why: str) -> None:
        super().__init__(why)
        self.line = line
        self.why = why


def parse(text: str, where: str) -> Cursors:
    """A cursor file's text. A problem is named by its line number, never by the line's text."""
    for n, line in enumerate(text.splitlines(), start=1):
        if line.startswith(CONFLICT):
            raise DocketError(
                f"{where}: line {n} is a merge conflict's marker; keep one side, the one with the older mark is safe"
            )
    try:
        root = cast(yaml.Node | None, yaml.compose(text, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        raise DocketError(f"{where}: line {mark.line + 1 if mark else 1} isn't valid YAML") from None
    except yaml.reader.ReaderError as e:
        # A character YAML can't hold: the reader gives its place in the text, not its line.
        raise DocketError(f"{where}: line {text.count(chr(10), 0, e.position) + 1} isn't valid YAML") from None
    if root is None:
        return {}
    try:
        return _file(root)
    except _BadLineError as e:
        raise DocketError(f"{where}: line {e.line}: {e.why}") from None


def _line(node: yaml.Node) -> int:
    return node.start_mark.line + 1


def _mapping(node: yaml.Node, allowed: frozenset[str] | None) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        raise _BadLineError(_line(node), "a map is expected here")
    out: dict[str, yaml.Node] = {}
    for key, value in node.value:
        if not isinstance(key, yaml.ScalarNode) or key.tag != _STR:
            raise _BadLineError(_line(key), "a key is expected here")
        name = str(key.value)
        if allowed is not None and name not in allowed:
            raise _BadLineError(_line(key), "an unknown key; a cursor file fails closed")
        if name in out:
            raise _BadLineError(_line(key), "a key given twice")
        out[name] = value
    return out


def _scalar(node: yaml.Node, what: str) -> yaml.ScalarNode:
    if not isinstance(node, yaml.ScalarNode):
        raise _BadLineError(_line(node), f"{what} is expected here")
    return node


def _text(node: yaml.Node, what: str) -> str:
    scalar = _scalar(node, what)
    if scalar.tag != _STR:
        raise _BadLineError(_line(node), f"{what} is expected here")
    return str(scalar.value)


def _quoted(node: yaml.Node, what: str) -> str:
    scalar = _scalar(node, what)
    if scalar.style not in _QUOTED:
        raise _BadLineError(_line(node), f"{what} must be quoted; YAML reads an unquoted one as something else")
    return str(scalar.value)


def _time(node: yaml.Node) -> datetime:
    try:
        moment = datetime.fromisoformat(_quoted(node, "a time").replace("Z", "+00:00"))
    except ValueError:
        raise _BadLineError(_line(node), "not a time") from None
    if moment.tzinfo is None:
        raise _BadLineError(_line(node), "a time needs its zone")
    return moment.astimezone(UTC)


def _date(node: yaml.Node) -> date:
    try:
        return date.fromisoformat(_quoted(node, "a date"))
    except ValueError:
        raise _BadLineError(_line(node), "not a date") from None


def _int(node: yaml.Node, what: str) -> int:
    scalar = _scalar(node, what)
    if scalar.tag != _INT:
        raise _BadLineError(_line(node), f"{what} is expected here")
    return int(str(scalar.value))


def writable(native: str) -> bool:
    """An id this file can hold: a native id YAML can also read back. U+FFFE, U+FFFF and a lone surrogate pass as a
    source's id, but a file holding one would stop every run of this machine."""
    return is_native_id(native) and not yaml.reader.Reader.NON_PRINTABLE.search(native)


def _id(node: yaml.Node) -> str:
    value = _text(node, "an id")
    if not writable(value):
        raise _BadLineError(_line(node), "not an id")
    return value


def _token(node: yaml.Node, what: str) -> str:
    value = _text(node, what)
    if not TOKEN.match(value):
        raise _BadLineError(_line(node), f"{what} is a short token")
    return value


def _items(node: yaml.Node) -> list[yaml.Node]:
    if not isinstance(node, yaml.SequenceNode):
        raise _BadLineError(_line(node), "a list is expected here")
    return list(node.value)


def _file(root: yaml.Node) -> Cursors:
    top = _mapping(root, frozenset({"schema_version", "sources"}))
    if "schema_version" not in top or _int(top["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise _BadLineError(_line(top.get("schema_version", root)), f"schema_version is {SCHEMA_VERSION}")
    out: Cursors = {}
    if "sources" not in top:
        return out
    for name, node in _mapping(top["sources"], None).items():
        if source_name_problem(name) is not None:
            raise _BadLineError(_line(node), "a source's name is expected here")
        out[name] = _cursor(node)
    return out


def _cursor(node: yaml.Node) -> Cursor:
    fields = _mapping(node, frozenset({"mark", "lookback_days", "retry", "waiting"}))
    cursor = Cursor()
    if "mark" in fields:
        cursor.mark = _time(fields["mark"])
    if "lookback_days" in fields:
        days = _int(fields["lookback_days"], "lookback_days")
        if not 1 <= days <= MAX_LOOKBACK_DAYS:
            raise _BadLineError(_line(fields["lookback_days"]), f"lookback_days is 1 to {MAX_LOOKBACK_DAYS}")
        cursor.lookback_days = days
    for item in _items(fields["retry"]) if "retry" in fields else []:
        entry = _mapping(item, frozenset({"id", "reason", "since"}))
        if set(entry) != {"id", "reason", "since"}:
            raise _BadLineError(_line(item), "a retry entry is id, reason and since")
        retry = Retry(_id(entry["id"]), _token(entry["reason"], "a reason"), _date(entry["since"]))
        cursor.retry[retry.id] = retry
    for item in _items(fields["waiting"]) if "waiting" in fields else []:
        entry = _mapping(item, frozenset({"id", "for", "since"}))
        if set(entry) != {"id", "for", "since"}:
            raise _BadLineError(_line(item), "a waiting entry is id, for and since")
        waiting = Waiting(_id(entry["id"]), _token(entry["for"], "what it waits for"), _date(entry["since"]))
        cursor.waiting[waiting.id] = waiting
    return cursor


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def file_path(root: Path, machine: str) -> Path:
    if not MACHINE_NAME.match(machine):
        raise DocketError(f"{machine!r} isn't a machine's name")
    folder = root / STATE
    if _linked(folder):
        raise DocketError(f"{STATE}/ is a link; cursor files are read and written only as plain files in the vault")
    return folder / f"cursors-{machine}.yaml"


def read(root: Path, machine: str) -> Cursors:
    """A machine's cursor file, failing closed: no file means no cursor yet."""
    path = file_path(root, machine)
    where = f"{STATE}/{path.name}"
    if _linked(path):
        raise DocketError(f"{where} is a link; a cursor file is a plain file in the vault")
    if not path.exists():
        return {}
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocketError(f"{where} isn't UTF-8") from None
    return parse(text, where)


def read_others(root: Path, machine: str) -> tuple[dict[str, Cursors], list[str]]:
    """Every other machine's cursor file that reads, and a note for each one that doesn't."""
    folder = root / STATE
    found: dict[str, Cursors] = {}
    notes: list[str] = []
    if _linked(folder) or not folder.is_dir():
        return found, notes
    for path in sorted(folder.iterdir()):
        match = FILE.match(path.name)
        if match is None or match["machine"] == machine:
            continue
        other = match["machine"]
        try:
            found[other] = read(root, other)
        except DocketError:
            notes.append(f"the cursor file of {path.name} doesn't read, so its marks and sets are left out")
    return found, notes


# -- writing ---------------------------------------------------------------------------------------------------------


def _q(value: str) -> str:
    # JSON's double-quoted string is YAML's: an id may hold any character but whitespace and control characters.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dump(cursors: Cursors) -> str:
    """The file's one canonical form: sources sorted, the sets sorted by id, empty sets left out."""
    lines = [HEADER.rstrip("\n"), f"schema_version: {SCHEMA_VERSION}"]
    if cursors:
        lines.append("sources:")
    for name in sorted(cursors):
        cursor = cursors[name]
        lines.append(f"  {name}:")
        if cursor.mark is not None:
            lines.append(f"    mark: {_q(_stamp(cursor.mark))}")
        lines.append(f"    lookback_days: {cursor.lookback_days}")
        if cursor.retry:
            lines.append("    retry:")
            lines.extend(
                f"      - {{id: {_q(r.id)}, reason: {_q(r.reason)}, since: {_q(r.since.isoformat())}}}"
                for r in sorted(cursor.retry.values(), key=lambda r: r.id)
            )
        if cursor.waiting:
            lines.append("    waiting:")
            lines.extend(
                f"      - {{id: {_q(w.id)}, for: {_q(w.for_)}, since: {_q(w.since.isoformat())}}}"
                for w in sorted(cursor.waiting.values(), key=lambda w: w.id)
            )
    return "\n".join(lines) + "\n"


def save(vault: Vault, source: str, cursor: Cursor) -> str | None:
    """Write this machine's cursor of a source and commit the file when it changed or never reached HEAD: alone, with a
    message that names no id. Returns the commit, or None when nothing changed."""
    with vault.writing():
        path = file_path(vault.root, vault.machine)
        cursors = read(vault.root, vault.machine)
        cursors[source] = cursor
        text = dump(cursors)
        old = path.read_bytes().decode("utf-8-sig") if path.exists() else None
        if text != old:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_replacing(vault.home, path, text, f"cursors-{vault.machine}")
        rel = f"{STATE}/{path.name}"
        if not (vault.git.untracked(rel, ignored_too=True) or vault.git.changed_against_head(rel)):
            return None
        return vault.commit([rel], f"Cursors: {source} on {vault.machine}")


# -- a run's window and what it is handed ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    since: datetime
    until: datetime
    # Where the window would start with no since: a run that started later than this moves no mark.
    default_since: datetime
    notes: list[str]


def lookback(cursor: Cursor | None, setting: int | None) -> int:
    """A source's lookback in days: its `lookback_days` in vault.yaml when set (DR-0606), else the cursor's."""
    if setting is not None:
        return setting
    return cursor.lookback_days if cursor is not None else LOOKBACK_DAYS


def window(
    own: Cursor | None,
    others: dict[str, Cursor],
    now: datetime,
    since: datetime | None = None,
    *,
    lookback_days: int | None = None,
) -> Window:
    """The furthest mark any machine reached, a mark ahead of this machine's clock left out, less the source's lookback
    (`lookback_days`, vault.yaml's, else this machine's cursor's), to now; `since` starts it elsewhere (DR-0607)."""
    notes: list[str] = []
    marks: list[datetime] = []
    named = {"this machine": own} if own is not None else {}
    for machine, cursor in [*named.items(), *sorted(others.items())]:
        if cursor.mark is None:
            continue
        if cursor.mark > now:
            notes.append(f"the mark of {machine} is ahead of this machine's clock, so it is left out")
            continue
        marks.append(cursor.mark)
    start = (max(marks) if marks else now) - timedelta(days=lookback(own, lookback_days))
    return Window(since=since if since is not None else start, until=now, default_since=start, notes=notes)


@dataclass(frozen=True)
class Handover:
    """The retry and waiting entries a run is handed: this machine's, and other machines' by the rules below."""

    retry: dict[str, Retry] = field(default_factory=dict[str, Retry])
    waiting: dict[str, Waiting] = field(default_factory=dict[str, Waiting])


def _expired(entry: Retry, today: date) -> bool:
    return entry.reason != LATER and today - entry.since >= EXPIRES


def _due(since: date, today: date) -> bool:
    return today - since >= FINAL_AFTER


def handover(
    own: Cursor | None,
    others: dict[str, Cursor],
    now: datetime,
    held: frozenset[str],
    has_record: Callable[[Iterable[str]], set[str]],
    *,
    final: bool = False,
) -> Handover:
    """This machine's sets, and while another machine's mark is under 30 days old its waiting entries as its file lists
    them and its retry entries under which raw and staging hold no record; held ids never, expired retry ids never. A
    final run gets only the waiting ids, and the retry ids not held back for the budget, a week old or more."""
    today = now.date()
    retry: dict[str, Retry] = {}
    waiting: dict[str, Waiting] = {}
    if own is not None:
        retry.update({i: e for i, e in own.retry.items() if i not in held and not _expired(e, today)})
        waiting.update({i: e for i, e in own.waiting.items() if i not in held})
    theirs: dict[str, Retry] = {}
    for machine in sorted(others):
        cursor = others[machine]
        if cursor.mark is None or now - cursor.mark >= OTHERS_FOR:
            continue
        for i, w in cursor.waiting.items():
            if i not in held and i not in waiting:
                waiting[i] = w
        for i, r in cursor.retry.items():
            if i not in held and i not in retry and i not in theirs and not _expired(r, today):
                theirs[i] = r
    stored = has_record(theirs) if theirs else set[str]()
    retry.update({i: r for i, r in theirs.items() if i not in stored})
    if final:
        retry = {i: r for i, r in retry.items() if r.reason != LATER and _due(r.since, today)}
        waiting = {i: w for i, w in waiting.items() if _due(w.since, today)}
    return Handover(retry=retry, waiting=waiting)


# -- a close ---------------------------------------------------------------------------------------------------------


def covers(native: str, refs: Iterable[str]) -> bool:
    """Whether a native id is one of these native refs, or one sits under it: `<id>` and `<id>/summary`."""
    prefix = f"{native}/"
    return any(ref == native or ref.startswith(prefix) for ref in refs)


@dataclass(frozen=True)
class Close:
    """What a whole close knows: the run as it opened, and what came back from its mapper."""

    opened: datetime
    window_since: datetime | None
    default_since: datetime | None
    final: bool
    captures: int
    # The native ids raw and staging held in the window at open, and the sets the run was handed.
    kept: frozenset[str]
    handed: Handover
    skipped: list[tuple[str | None, str]]
    # Native refs (rid, thread, container, less `<source>:`) of the records the mapper returned.
    records: frozenset[str]
    waiting: list[tuple[str, str]]
    set_aside: frozenset[str]
    held: frozenset[str]


@dataclass(frozen=True)
class Closed:
    cursor: Cursor
    moved: bool
    expired: list[str]


def close(old: Cursor | None, facts: Close, today: date) -> Closed:
    """This machine's cursor after a whole close, by the retry and waiting tables (DR-0247) and the mark's rule
    (DR-0607)."""
    base = old or Cursor()
    skipped = {i: reason for i, reason in facts.skipped if i is not None and i not in facts.held and writable(i)}
    reported = {i: what for i, what in facts.waiting if i not in facts.held and writable(i)}
    expired: list[str] = []

    def reached(native: str) -> bool:
        return covers(native, facts.records) or native in reported or native in facts.set_aside

    retry: dict[str, Retry] = {}
    for i, entry in base.retry.items():
        if i in facts.held or (reached(i) and i not in skipped):
            continue
        retry[i] = entry
    for i, reason in skipped.items():
        first = retry.get(i) or facts.handed.retry.get(i)
        # An id held back for the budget waited without a clock; skipped now for another reason, its month starts.
        since = today if first is None or (first.reason == LATER and reason != LATER) else first.since
        retry[i] = Retry(i, reason, since)
    for i in sorted(retry):
        if _expired(retry[i], today):
            expired.append(i)
            del retry[i]

    # On a final close, the entries a week old at its open go, whatever they gave, and no report brings back an id the
    # run was handed: a recording the source no longer has would otherwise be asked for forever.
    due = (
        {i for i, w in base.waiting.items() if i in facts.handed.waiting and _due(w.since, facts.opened.date())}
        if facts.final
        else set[str]()
    )
    finalized = due | (set(facts.handed.waiting) if facts.final else set[str]())
    waiting: dict[str, Waiting] = {}
    for i, entry in base.waiting.items():
        if i in facts.held:
            continue
        if i in due:
            expired.append(i)
            continue
        if covers(i, facts.records) and i not in reported:
            continue
        waiting[i] = entry
    told_to_skip = {i for i in reported if covers(i, facts.kept) and i not in facts.handed.waiting}
    for i, what in reported.items():
        if i in skipped or i in told_to_skip or i in finalized:
            continue
        first = waiting.get(i) or facts.handed.waiting.get(i)
        waiting[i] = Waiting(i, what, first.since if first is not None else today)

    mark = base.mark
    moved = (
        not facts.final
        and facts.captures > 0
        and facts.window_since is not None
        and facts.default_since is not None
        and facts.window_since <= facts.default_since
    )
    if moved:
        mark = facts.opened
    cursor = Cursor(mark=mark, lookback_days=base.lookback_days, retry=retry, waiting=waiting)
    return Closed(cursor=cursor, moved=moved, expired=sorted(set(expired)))
