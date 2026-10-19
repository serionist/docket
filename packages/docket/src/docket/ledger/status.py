"""Each stream's STATUS.md (DR-0673 to DR-0675): its frontmatter rendered from the ledger at the path `vault.yaml`'s
`rendered` names with kind status, in the consuming repo, never committed. The body below it is the owner's.

    ---
    stream: pilot
    status: waiting
    where: "PR #12 merged; waiting on: review"
    next: Ada signs the order
    deadline: 2026-10-30
    updated: 2026-10-19
    # Rendered by Docket from the ledger; change the stream, not these lines. sha256:<16 hex>
    ---

The field lines are todo.py's `write_stream` layout, so todo.py reads them unchanged; the comment line hashes every
other line between the two `---` lines, joined with LF, so any other key or line fails it (DR-0674). The ledger wins
(DR-0675): a frontmatter the hash holds is rewritten when its stream moves; one without it, or with a failing one, that
holds only the field lines and the five the owner sets as the ledger has them is taken over, as todo.py's files are
after the import; any other is left and reported, never overwritten, and never read into the ledger. The body's bytes,
its line endings and a BOM are kept, and the frontmatter takes the file's line ending.

A write that changed a stream renders its file once its lock is let go, from the write's own fold while no other write
has landed since, else from the ledger as it stands; `docket render` renders every file. The file is read again right
before the rename: changed since, it is left, unless another render put Docket's own valid hash there first, when the
stream is read again and rendered once more. `docket vault check` reads the files and writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import yaml

from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.ledger import ops, store
from docket.ledger.meetings import Stream
from docket.vault import rendered
from docket.vault.config import RenderedFile, UniqueKeyLoader, yaml_problem
from docket.vault.host import Probe, probe_remote
from docket.vault.rendered import Outcome, Remotes
from docket.vault.vault import Vault

FIELDS = ("stream", "status", "where", "next", "deadline", "updated")
# The five the owner sets: the ledger derives updated, the day of the stream's latest change (DR-0625).
SET = FIELDS[:5]
COMMENT = "# Rendered by Docket from the ledger; change the stream, not these lines. sha256:"
HASHED = re.compile(re.escape(COMMENT) + r"([0-9a-f]{16})")
FIELD_LINE = re.compile(r"(?:stream|status|where|next|deadline|updated):(?: .*)?")
# todo.py's: characters a JSON-quoted value writes as \u escapes, since an editor or a terminal would act on them.
UNSAFE = re.compile("[\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff]")
BOM = "\ufeff"
# A value a report shows is cut to this.
SHOWN = 40
# What a status file's op files looked like when a write committed: each file's name, size and time.
Mark = tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class Fields:
    """A stream's six fields as the ledger holds them."""

    stream: str
    status: str
    where: str | None
    next: str | None
    deadline: date | None
    updated: date

    @staticmethod
    def of(stream: Stream) -> Fields:
        return Fields(stream.name, stream.status, stream.where, stream.next, stream.deadline, stream.updated)

    def held(self, name: str) -> str | None:
        """A field as text, as the writer takes it: a day as YYYY-MM-DD."""
        value = getattr(self, name)
        return value.isoformat() if isinstance(value, date) else value


# -- the frontmatter (DR-0674) -----------------------------------------------------------------------------------------


def scalar(value: str | date) -> str:
    """todo.py's: a day bare, text plain when YAML reads it back as the same text, else JSON-quoted."""
    if isinstance(value, date):
        return value.isoformat()
    try:
        plain = value != "" and "\n" not in value and yaml.safe_load("k: " + value) == {"k": value}
    except yaml.YAMLError:
        plain = False
    if plain:
        return value
    return UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", json.dumps(value, ensure_ascii=False))


def field_lines(fields: Fields) -> list[str]:
    """The field lines in todo.py's order, an unset one left out."""
    return [f"{name}: {scalar(value)}" for name in FIELDS if (value := getattr(fields, name)) not in (None, "")]


def digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def frontmatter(fields: Fields, eol: str = "\n") -> str:
    """The frontmatter Docket writes for a stream, each line ending in `eol`."""
    lines = field_lines(fields)
    return eol.join(["---", *lines, COMMENT + digest(lines), "---"]) + eol


@dataclass(frozen=True)
class File:
    """A STATUS.md as read: its BOM, the line ending of its first line, the lines between its two `---` lines without
    their endings (None when it has no frontmatter), whether a `---` line closes them, and the rest: what follows the
    closing line, or the whole text after a BOM."""

    bom: str
    eol: str
    head: tuple[str, ...] | None
    rest: str
    closed: bool = True


def read_file(text: str) -> File:
    bom = BOM if text.startswith(BOM) else ""
    body = text[len(bom) :]
    end = body.find("\n")
    first = body if end == -1 else body[:end]
    eol = "\r\n" if first.endswith("\r") else "\n"
    if first.removesuffix("\r") != "---":
        return File(bom, eol, None, body)
    head: list[str] = []
    pos = len(body) if end == -1 else end + 1
    while pos < len(body):
        nl = body.find("\n", pos)
        after = len(body) if nl == -1 else nl + 1
        line = body[pos:after].removesuffix("\n").removesuffix("\r")
        if line == "---":
            return File(bom, eol, tuple(head), body[after:])
        head.append(line)
        pos = after
    return File(bom, eol, tuple(head), "", closed=False)


def holds(head: Sequence[str]) -> bool:
    """Whether Docket's comment line is there, once, and hashes every other line."""
    found = [m.group(1) for line in head if (m := HASHED.fullmatch(line))]
    return len(found) == 1 and digest(line for line in head if not HASHED.fullmatch(line)) == found[0]


def planned(file: File | None, fields: Fields) -> tuple[str | None, str]:
    """A file's text once rendered, from the file as read (None when there is none); or None and why it is left."""
    if file is None:
        return frontmatter(fields), ""
    if file.head is None:
        return file.bom + frontmatter(fields, file.eol) + file.rest, ""
    if not file.closed:
        return None, "its frontmatter has no closing --- line; close it, or take the opening one out"
    if not holds(file.head):
        why = hand_edit(file.head, fields)
        if why:
            return None, why
    return file.bom + frontmatter(fields, file.eol) + file.rest, ""


def hand_edit(head: Sequence[str], fields: Fields) -> str:
    """Why a frontmatter Docket's hash doesn't hold is left, or "" when it holds only the field lines, and Docket's
    comment line at most once, with the five the owner sets as the ledger has them: the render takes that one over."""
    seen = False
    for n, line in enumerate(head, start=2):
        comment = HASHED.fullmatch(line) is not None
        if (comment and seen) or not (comment or FIELD_LINE.fullmatch(line)):
            return f"its frontmatter holds a line besides the stream's fields, line {n}; move it below the closing ---"
        seen = seen or comment
    try:
        # A line for the opening ---, so a problem's line is the file's, as the stray line's above is.
        found: object = yaml.load("\n" + "\n".join(head), Loader=UniqueKeyLoader)  # noqa: S506 - a SafeLoader
    except yaml.YAMLError as e:
        return f"its frontmatter isn't YAML Docket reads ({yaml_problem(e)}); fix it, or take it out to render it"
    given = cast(dict[str, object], found or {})
    differ = [name for name in SET if _as_stored(name, given.get(name)) != fields.held(name)]
    if not differ:
        return ""
    said = "; ".join(f"{name} {_shown(given.get(name))} in the file, {_shown(fields.held(name))} in the ledger"
                     for name in differ)  # fmt: skip
    return f"a hand edit holds its frontmatter: {said}; set the stream to the file's, or put the ledger's back"


def _as_stored(name: str, value: object) -> object:
    """A file's value as the writer would store it: unset as None, a day as YYYY-MM-DD, `where` and `next` on one line.
    A value that isn't text the writer refuses, so it never equals the ledger's."""
    if value is None or value in ("", "-"):
        return None
    if name == "deadline" and isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and name in ("where", "next"):
        # Read when called: the writer imports this module, so it may still be loading when this one is.
        return store.one_line(value) or None
    return value


def _shown(value: object) -> str:
    if value is None or value in ("", "-"):
        return "unset"
    text = json.dumps(value.isoformat() if isinstance(value, date) else str(value), ensure_ascii=False)
    return text if len(text) <= SHOWN else text[: SHOWN - 2] + '\u2026"'


# -- the render (DR-0675) ----------------------------------------------------------------------------------------------


def entries(vault: Vault) -> list[RenderedFile]:
    return [f for f in vault.config.rendered if f.kind == "status"]


def read_streams(vault: Vault, names: Iterable[str]) -> dict[str, Fields]:
    """The streams by name as the ledger holds them now, a name no stream holds left out: the index's tables brought
    up to date, or the op files folded when the index can't be had (DR-0619). Never under the vault's lock."""
    wanted = sorted(set(names))
    if not wanted:
        return {}
    marks = ", ".join("?" * len(wanted))
    sql = f'SELECT name, status, "where", next, deadline, updated FROM streams WHERE name IN ({marks})'  # noqa: S608
    with ledger_tables(vault) as conn:
        rows = conn.execute(sql, wanted).fetchall()
    return {
        name: Fields(name, status, where, nxt, date.fromisoformat(deadline) if deadline else None,
                     date.fromisoformat(updated))
        for name, status, where, nxt, deadline, updated in rows
    }  # fmt: skip


def render_all(vault: Vault, probe: Callable[[str], Probe] = probe_remote) -> list[Outcome]:
    """Every status file `vault.yaml` names, from the ledger as it stands: `docket render`."""
    found = entries(vault)
    if not found:
        return []
    held = read_streams(vault, (cast(str, f.stream) for f in found))
    return render(vault, found, held, probe=probe)


def mark(vault: Vault, touched: Mapping[str, Stream | None]) -> Mark | None:
    """What the op files look like as a write that changed these streams commits, under its lock; None when no status
    file names one of them, so the write renders nothing."""
    if not any(f.stream in touched for f in entries(vault)):
        return None
    try:
        with os.scandir(ops.folder(vault.root)) as found:
            return tuple(sorted((e.name, (s := e.stat()).st_size, s.st_mtime_ns) for e in found if e.is_file()))
    except (DocketError, OSError):
        # Never the write's failure: an empty mark reads the ledger as it stands, as one another write moved does.
        return ()


def after_write(vault: Vault, touched: Mapping[str, Stream | None], marked: Mark | None) -> tuple[str, ...]:
    """The files of the streams a write changed, rendered once its lock is let go: from the write's own fold while the
    op files are as it left them, else from the ledger as it stands, so the last writer's state lands. What it says,
    each file written, left or refused; a render that fails says so and never fails the write (DR-0554)."""
    if marked is None:
        return ()
    try:
        found = [f for f in entries(vault) if f.stream in touched]
        if mark(vault, touched) == marked and marked:
            held = {name: Fields.of(stream) for name, stream in touched.items() if stream is not None}
        else:
            held = read_streams(vault, touched)
        return tuple(rendered.rendered_lines(render(vault, found, held)))
    except (DocketError, OSError) as e:
        return (f"render failed: {e}",)
    except Exception as e:  # the write is committed: a render's failure is reported, never raised
        return (f"render failed ({type(e).__name__})",)


def render(
    vault: Vault,
    files: Sequence[RenderedFile],
    held: Mapping[str, Fields],
    *,
    probe: Callable[[str], Probe] = probe_remote,
) -> list[Outcome]:
    """These status files rendered from the streams `held` gives, by name. The consuming repo's remotes are checked
    when a file would be written or Docket's sits at a path, as the entity files' are (DR-0554)."""
    repo = rendered.consuming_repo(vault)
    if repo is None:
        return [Outcome(f.path, "refused", rendered.no_repo(vault)) for f in files]
    remotes = Remotes(repo, probe)
    return [_one(vault, repo, f, held.get(cast(str, f.stream)), remotes) for f in files]


# Bytes that aren't UTF-8: a file kept by hand, never Docket's.
_NOT_TEXT = "\x00not text"


def _one(vault: Vault, repo: Path, file: RenderedFile, fields: Fields | None, remotes: Remotes) -> Outcome:
    target, why = rendered.target_of(vault, repo, file)
    if target is None:
        return Outcome(file.path, "refused", why)
    done = _attempt(vault, target, file, fields, remotes, last=False)
    if done is None:
        # Another render got there first: the stream read again and rendered once more, so the last writer's state
        # lands, with no lock taken.
        name = cast(str, file.stream)
        done = _attempt(vault, target, file, read_streams(vault, [name]).get(name), remotes, last=True)
    return cast(Outcome, done)


def _attempt(
    vault: Vault, target: Path, file: RenderedFile, fields: Fields | None, remotes: Remotes, *, last: bool
) -> Outcome | None:
    """One render of a file; None, unless it is the `last`, when the file changed before the rename to a frontmatter
    Docket's own hash holds."""
    try:
        before = rendered.file_bytes(target)
    except OSError as e:
        return Outcome(file.path, "refused", rendered.unreadable(e))
    text = _text(before)
    if text is _NOT_TEXT:
        return Outcome(file.path, "refused", "it isn't UTF-8 text, so its body can't be kept; save it as UTF-8")
    if fields is None:
        why = f"the ledger holds no stream {file.stream}; add it, or take the file out of vault.yaml's rendered"
        return Outcome(file.path, "edited", why)
    new, why = planned(read_file(text) if text is not None else None, fields)
    if new is None:
        return Outcome(file.path, "edited", why)
    public = remotes.public()
    if public:
        return Outcome(file.path, "refused", public)
    if new == text:
        return Outcome(file.path, "unchanged")
    unreached = remotes.unreached()
    if unreached:
        return Outcome(file.path, "refused", unreached)
    if before is None and not target.parent.is_dir():
        return Outcome(file.path, "refused", rendered.no_folder(target))
    written, now = rendered.write_unless_changed(vault, target, new, before)
    if written:
        return Outcome(file.path, "written")
    if not last and _docket(now):
        return None
    return Outcome(file.path, "edited", "it changed while Docket rendered it; docket render looks again")


def _text(data: bytes | None) -> str | None:
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return _NOT_TEXT


def _docket(data: bytes | None) -> bool:
    """Whether a file holds a frontmatter Docket's hash holds."""
    text = _text(data)
    if text is None or text is _NOT_TEXT:
        return False
    file = read_file(text)
    return file.head is not None and file.closed and holds(file.head)


# -- the check (DR-0675) -----------------------------------------------------------------------------------------------


def problems(vault: Vault, streams: Iterable[Stream]) -> list[tuple[str, str]]:
    """What `docket vault check` says of the status files, each as (path, what), read and never written, no remote
    probed: a stream the ledger doesn't hold, a frontmatter a hand edit holds, a file Docket wrote whose fields lag the
    ledger, and one not rendered yet. A path the render would refuse is the render's to report."""
    found = entries(vault)
    repo = rendered.consuming_repo(vault) if found else None
    if repo is None:
        return []
    held = {s.name: Fields.of(s) for s in streams}
    out: list[tuple[str, str]] = []
    for file in found:
        name = cast(str, file.stream)
        fields = held.get(name)
        if fields is None:
            out.append((file.path, f"the ledger holds no stream {name}; add it, or take the file out of vault.yaml"))
            continue
        target, _ = rendered.target_of(vault, repo, file)
        if target is None:
            continue
        try:
            text = _text(rendered.file_bytes(target))
        except OSError:
            continue
        if text is _NOT_TEXT or (text is None and not target.parent.is_dir()):
            continue
        read = read_file(text) if text is not None else None
        new, why = planned(read, fields)
        if new is None:
            out.append((file.path, why))
        elif new != text:
            docket = read is not None and read.head is not None and holds(read.head)
            out.append((file.path, "stale; docket render" if docket else "not rendered yet; docket render"))
    return out
