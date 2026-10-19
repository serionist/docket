"""Writing the ledger (DR-0610, DR-0612, DR-0613, DR-0618): the one writer of ops, a whole write or none of it.

A write runs under the vault lock. It brings the index's ledger tables up to date from the op files, or folds them in
memory when the index can't be had (DR-0616, DR-0619), and reads each item it touches from them; gives a renumber to
each item a clash left without a number; checks each change against the items as the write leaves them, the way
todo.py's verbs check them; mints the ids and numbers; screens the text; then writes this machine's month file through
a temp file and a rename and commits it, with any op file a stopped write left. A change that can't be made refuses
the whole write, naming the change by its place and, for the text rules, the field, never the value.

    write(vault, [{"op": "add", "fields": {"kind": "reply", "title": "Send Ada the dates", "who": "Ada",
                   "stream": "pilot", "source": "chat:C0X/1"}},
                  {"op": "triage", "item": "A-0302", "verdict": "chase"}], by="claude", via="cli")

Changes name an item by its A- number or its id. A number more than one item has held names none: the item is named
by its id until the clash is behind it. Undo takes `of`, an op's id, or `batch`, a write's first op id; an undo also
undoes what the op it undoes left idle, done and drop counting as one kind (DR-0614).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NoReturn, cast
from zoneinfo import ZoneInfo

from docket import console
from docket.errors import DocketError
from docket.index.build import ledger_tables
from docket.index.ledger import Held
from docket.ledger import check, ops
from docket.ledger.fold import CLOSED, NUMBER, Folded, Item, family, fold_item, number_text
from docket.ledger.ops import DAYS, FIELDS, KINDS, OPS_DIR, REQUIRED, VERDICTS, By, Line, Op, Via
from docket.vault.neverstore import matches_text, names_person
from docket.vault.redact import Redactor
from docket.vault.staging import write_replacing
from docket.vault.vault import UnmergedError, Vault

# The changes a caller asks for, and the keys each takes besides `op`. A renumber is the writer's own.
CHANGES: dict[str, tuple[str, ...]] = {
    "add": ("fields", "note"),
    "set": ("item", "fields", "note"),
    "log": ("item", "note"),
    "triage": ("item", "verdict", "note"),
    "snooze": ("item", "until", "note"),
    "done": ("item", "note"),
    "drop": ("item", "note"),
    "reopen": ("item", "note"),
    "material": ("item", "ref", "label", "remove", "note"),
    "archive": ("item", "note"),
    "undo": ("of", "batch", "note"),
}
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
ONE_LINE = ("title", "who", "source")
TEXTS = ("description", "notes", "outcome")
# Text is written redacted. Any other value can't hold a marker: a locator would stop finding its thing, and a kind,
# a stream, a verdict or a day would stop being one, so a redaction rule's match there refuses the write (DR-0618).
REDACTED = ("title", "who", "note", "notes", "description", "outcome", "label")
LOCATORS = ("source", "ref")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ufeff\ufffe\uffff]")
SUMMARY = 60


class LedgerError(DocketError):
    """A write refused: nothing of it was written."""


@dataclass(frozen=True)
class Written:
    """What a write wrote, or with a dry run would write: its ops in order, its commit, and each item's number."""

    ops: tuple[Op, ...]
    commit: str | None
    numbers: dict[str, int] = field(default_factory=dict[str, int])


def one_line(text: str) -> str:
    return " ".join(CONTROL.sub("", text).split())


def clean_text(text: str) -> str | None:
    """Free text as stored: line breaks as LF, no other control character, no trailing spaces, no blank lines at
    either end; None when nothing is left."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    rows = [CONTROL.sub("", row).rstrip() for row in text.split("\n")]
    out = "\n".join(rows).strip("\n")
    return out if out.strip() else None


def write(
    vault: Vault,
    changes: Sequence[Mapping[str, Any]],
    *,
    by: By = "claude",
    via: Via = "cli",
    dry_run: bool = False,
    now: datetime | None = None,
) -> Written:
    """Make the changes, all of them or none, as one write by `by` through `via`; with `dry_run`, check them and say
    what would be written, writing nothing."""
    if by not in ops.BY or via not in ops.VIA:
        raise LedgerError(f"by is one of {', '.join(ops.BY)}, via one of {', '.join(ops.VIA)}")
    moment = now or datetime.now(UTC)
    with vault.writing():
        ops.folder(vault.root)
        # The tables are asked only while the write is planned: its commit runs with the index free.
        with ledger_tables(vault) as conn:
            plan = _Write(vault, Held(conn), by, via, moment)
            plan.renumber()
            for n, change in enumerate(changes, start=1):
                plan.change(n, change)
            new = tuple(plan.new)
            numbers = plan.numbers()
            message = plan.message() if new else ""
        if dry_run or not new:
            return Written(new, None, numbers)
        return Written(new, commit_ops(vault, new, message), numbers)


class _Write:
    """One write as it is planned: the ledger's tables it reads, the lines of each item it touched with its own ops
    added, and the numbers it gave."""

    def __init__(self, vault: Vault, held: Held, by: By, via: Via, now: datetime) -> None:
        self.vault = vault
        self.held = held
        self.highest_id = held.highest_id()
        self.highest_number = held.highest_number()
        self.by: By = by
        self.via: Via = via
        self.now = now
        self.day = now.astimezone(ZoneInfo(vault.config.owner.time_zone)).date()
        self.at = now.astimezone(UTC).replace(microsecond=0)
        self.lines: dict[str, list[Line]] = {}
        self.new: list[Op] = []
        self.given: dict[int, str] = {}
        self.redactor = Redactor.from_config(vault.config.redaction)
        self.kind = ""

    # -- what stands ------------------------------------------------------------------------------------------------

    def lines_of(self, item_id: str) -> list[Line]:
        """An item's lines: as the tables hold them, read once, with this write's ops added."""
        if item_id not in self.lines:
            self.lines[item_id] = self.held.lines(item_id)
        return self.lines[item_id]

    def fold(self, item_id: str) -> Folded:
        return fold_item(item_id, self.lines_of(item_id))

    def number_of(self, item_id: str) -> int:
        mine = [number for number, named in self.given.items() if named == item_id]
        return max(mine) if mine else self.fold(item_id).claims[-1][1]

    def standing(self, item_id: str) -> Item:
        """An item `resolve` named, which stands."""
        return cast(Item, self.fold(item_id).item)

    def numbers(self) -> dict[str, int]:
        return {op.item: self.number_of(op.item) for op in self.new}

    def refuse(self, n: int, why: str) -> NoReturn:
        raise LedgerError(f"change {n} ({self.kind}): {why}; nothing written")

    def resolve(self, n: int, ref: object) -> str:
        """The item a change names, by its id or its A- number."""
        if not isinstance(ref, str) or not ref.strip():
            self.refuse(n, "it needs item, an A- number or an item's id")
        text = ref.strip()
        if ops.ULID.fullmatch(text):
            if self.fold(text).item is None:
                self.refuse(n, f"no item {text}")
            return text
        m = NUMBER.fullmatch(text)
        if m is None:
            self.refuse(n, "item is an A- number or an item's id")
        number = int(m.group(1))
        named = self.held.holders(number)
        if number in self.given and self.given[number] not in named:
            named.append(self.given[number])
        if not named:
            self.refuse(n, f"no item {number_text(number)}")
        if len(named) > 1:
            self.refuse(n, f"{number_text(number)} has been held by more than one item; name the item by its id")
        if self.fold(named[0]).item is None:
            self.refuse(n, f"{number_text(number)} was undone")
        return named[0]

    # -- making ops -------------------------------------------------------------------------------------------------

    def emit(self, kind: str, item_id: str | None, **keys: Any) -> Op:
        """The next op of this write, added to its item's lines so the next change sees it."""
        # The first op is minted on the clock; each next one is the one before plus one (DR-0612).
        op_id = ops.encode(ops.decode(self.new[-1].id) + 1) if self.new else ops.mint(self.highest_id, self.now, 1)[0]
        item = item_id or op_id
        mine = self.lines_of(item)
        op = Op(
            schema_version=1, id=op_id, item=item, op=kind, day=self.day, at=self.at, machine=self.vault.machine,
            by=self.by, via=self.via, batch=self.new[0].id if self.new else op_id,
            after=max(line.op.id for line in mine) if mine else None, **keys,
        )  # fmt: skip
        mine.append(Line(op, op.text(), ops.rel_of(op), 0))
        self.new.append(op)
        return op

    def next_number(self) -> int:
        return max(self.highest_number, *self.given, 0) + 1

    def renumber(self) -> None:
        """A new number for each item whose latest claim lost one to an earlier claim (DR-0613)."""
        self.kind = "renumber"
        for item_id in self.held.clashing():
            number = self.next_number()
            self.given[number] = item_id
            self.emit("renumber", item_id, number=number)

    # -- the text rules (DR-0618) -----------------------------------------------------------------------------------

    def screen(self, n: int, name: str, value: str) -> str:
        """A value as it may be written: text redacted, anything else as given. A never-store keyword, a person on the
        people list in who, or a redaction rule's match in a value that isn't text refuses the write."""
        lists = self.vault.config.never_store
        counts: Counter[str] = Counter()
        redacted = self.redactor.text(value, counts)
        if matches_text(value, lists) or matches_text(redacted, lists):
            self.refuse(n, f"{name} says something on the never-store keywords list; leave it out")
        if name == "who" and names_person(value, lists):
            self.refuse(n, "who names someone on the never-store people list; leave them out")
        if name in REDACTED:
            return redacted
        if counts:
            what = "locator" if name in LOCATORS else "day" if name in (*DAYS, "until") else name
            self.refuse(n, f"{name} matches the {min(counts)} redaction rule, and a {what} can't hold a marker")
        return value

    def note(self, n: int, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            self.refuse(n, "note is text")
        text = one_line(value)
        return self.screen(n, "note", text) if text else None

    def value(self, n: int, name: str, value: object) -> str | None:
        """A field's value as written, or None to clear it."""
        if value is None or value in ("", "-"):
            return None
        if not isinstance(value, str):
            self.refuse(n, f"{name} is text")
        if name == "kind" and value not in KINDS:
            self.refuse(n, f"kind is one of {', '.join(KINDS)}")
        if name == "stream" and not SLUG.fullmatch(value):
            self.refuse(n, "stream is a slug: lowercase letters, digits and inner hyphens")
        if name in DAYS and not _day(value):
            self.refuse(n, f"{name} is a day, YYYY-MM-DD")
        text = one_line(value) if name in ONE_LINE else clean_text(value) if name in TEXTS else value
        return self.screen(n, name, text) if text else None

    def fields(self, n: int, given: object, *, adding: bool) -> dict[str, str | None]:
        if not isinstance(given, Mapping) or not given:
            self.refuse(n, "it needs fields")
        given = cast(Mapping[object, object], given)
        out: dict[str, str | None] = {}
        for name, raw in given.items():
            if name not in FIELDS:
                self.refuse(n, f"there is no field {name}; the fields are {', '.join(FIELDS)}")
            out[str(name)] = self.value(n, str(name), raw)
        if adding:
            missing = [name for name in REQUIRED if out.get(name) is None]
            if missing:
                self.refuse(n, f"an add needs {missing[0]}")
            return {name: value for name, value in out.items() if value is not None}
        cleared = [name for name in (*REQUIRED, "opened") if name in out and out[name] is None]
        if cleared:
            self.refuse(n, f"{cleared[0]} can't be cleared")
        return out

    # -- the changes ------------------------------------------------------------------------------------------------

    def change(self, n: int, change: Mapping[str, Any]) -> None:
        kind = change.get("op")
        self.kind = str(kind)
        allowed = CHANGES.get(kind) if isinstance(kind, str) else None
        if allowed is None:
            self.refuse(n, f"op is one of {', '.join(CHANGES)}")
        extra = sorted(str(key) for key in change if key not in ("op", *allowed))
        if extra:
            self.refuse(n, f"{kind} takes only {', '.join(allowed)}, not {', '.join(extra)}")
        note = self.note(n, change.get("note"))
        if kind == "add":
            number = self.next_number()
            op = self.emit(
                "add", None, number=number, fields=self.fields(n, change.get("fields"), adding=True), note=note
            )
            self.given[number] = op.id
            return
        if kind == "undo":
            self.undo(n, change, note)
            return
        item_id = self.resolve(n, change.get("item"))
        item = self.standing(item_id)
        label = number_text(self.number_of(item_id))
        closed = item.state in CLOSED
        if kind == "set":
            asked = self.fields(n, change.get("fields"), adding=False)
            changed = {name: value for name, value in asked.items() if _text(getattr(item, name)) != value}
            if changed:
                self.emit("set", item_id, fields=changed, note=note)
        elif kind == "log":
            if note is None:
                self.refuse(n, "a log needs a note")
            self.emit("log", item_id, note=note)
        elif kind == "triage":
            verdict = change.get("verdict")
            verdict = None if verdict in (None, "", "-") else verdict
            if verdict is not None and verdict not in VERDICTS:
                self.refuse(n, f"verdict is one of {', '.join(VERDICTS)}, or - to clear it")
            if verdict is not None:
                self.screen(n, "verdict", verdict)
            if closed:
                self.refuse(n, f"{label} is {item.state}; reopen it first")
            if item.state != "open" or item.triage != verdict:
                self.emit("triage", item_id, verdict=verdict, note=note)
        elif kind == "snooze":
            until = change.get("until")
            if not isinstance(until, str) or not _day(until) or date.fromisoformat(until) <= self.day:
                self.refuse(n, "snooze needs until, a day after today (YYYY-MM-DD)")
            self.screen(n, "until", until)
            if closed:
                self.refuse(n, f"{label} is {item.state}; reopen it first")
            # A snooze to the day it is snoozed to already changes nothing, as a triage to its verdict doesn't.
            if item.state != "snoozed" or item.until != date.fromisoformat(until):
                self.emit("snooze", item_id, until=date.fromisoformat(until), note=note)
        elif kind in ("done", "drop"):
            if closed:
                self.refuse(n, f"{label} is already {item.state}")
            if kind == "drop" and note is None:
                self.refuse(n, "a drop needs a note: why")
            self.emit(kind, item_id, note=note)
        elif kind == "reopen":
            if item.state == "open":
                self.refuse(n, f"{label} is already open")
            self.emit("reopen", item_id, note=note)
        elif kind == "material":
            self.material(n, item_id, change, note)
        elif not closed or item.archived:
            now = "archived" if item.archived else item.state
            self.refuse(n, f"an archive takes a closed item that isn't archived, and {label} is {now}")
        else:
            self.emit("archive", item_id, note=note)

    def material(self, n: int, item_id: str, change: Mapping[str, Any], note: str | None) -> None:
        item = self.standing(item_id)
        raw, label, remove = change.get("ref"), change.get("label"), change.get("remove", False)
        if not isinstance(raw, str) or not raw.strip():
            self.refuse(n, "a material needs ref: a path, a URL or a locator")
        if not isinstance(remove, bool) or (label is not None and not isinstance(label, str)):
            self.refuse(n, "remove is true or false, and label is text")
        ref = self.screen(n, "ref", material_ref(self.vault, raw))
        text = self.screen(n, "label", one_line(label)) if label and one_line(label) else None
        held = next((m for m in item.materials if m.ref == ref), None)
        if remove and held is None:
            self.refuse(n, "the item has no material at that ref")
        if not remove and held is not None and held.label == text:
            self.refuse(n, "the item has that material already")
        self.emit("material", item_id, ref=ref, label=None if remove else text, remove=remove, note=note)

    def undo(self, n: int, change: Mapping[str, Any], note: str | None) -> None:
        of, batch = change.get("of"), change.get("batch")
        if (of is None) == (batch is None) or not isinstance(of or batch, str):
            self.refuse(n, "an undo needs of, an op's id, or batch, a write's first op id")
        if isinstance(of, str):
            found = self.op_line(of)
            if found is None:
                self.refuse(n, f"no op {of}")
            if found.op.op == "renumber":
                self.refuse(n, "a renumber can't be undone")
            folded = self.fold(found.op.item)
            if found not in folded.lines:
                self.refuse(n, f"op {of} is left out of the fold, so there is nothing to undo")
            if of in folded.undone:
                self.refuse(n, f"op {of} is undone already")
            self.undo_one(found.op, note)
            return
        for item_id in self.held.in_batch(cast(str, batch)):
            self.lines_of(item_id)
        targets = sorted(
            (line.op for item_id in sorted(self.lines) for line in self.lines[item_id] if line.op.batch == batch),
            key=lambda op: op.id, reverse=True,
        )  # fmt: skip
        done = 0
        for op in targets:
            if op.op != "renumber" and op.id not in self.fold(op.item).undone:
                self.undo_one(op, note)
                done += 1
        if not done:
            self.refuse(n, f"no op of batch {batch} to undo")

    def op_line(self, op_id: str) -> Line | None:
        """An op's line, from the items whose lines hold its id, by item id and then as each item's lines run."""
        for item_id in self.held.items_of(op_id):
            self.lines_of(item_id)
        return next(
            (line for item_id in sorted(self.lines) for line in self.lines[item_id] if line.op.id == op_id), None
        )

    def undo_one(self, target: Op, note: str | None) -> None:
        """Undo an op, then each op of its kind on its item that it had left idle and that would now apply, one at a
        time, so another machine's close doesn't take over once this one is gone (DR-0614)."""
        kinds = {line.op.id: line.op.op for line in self.lines_of(target.item)}
        prior = self.fold(target.item)
        of = target.id
        while True:
            self.emit("undo", target.item, of=of, note=note)
            later = self.fold(target.item)
            woke = sorted(
                op_id for op_id in prior.idle - later.idle
                if op_id not in later.undone and family(kinds[op_id]) == family(target.op)
            )  # fmt: skip
            if not woke:
                return
            prior, of, note = later, woke[0], None

    def message(self) -> str:
        said = list(dict.fromkeys(f"{op.op} {number_text(self.number_of(op.item))}" for op in self.new))
        summary = ", ".join(said)
        if len(summary) > SUMMARY:
            summary = f"{said[0]} and {len(self.new) - 1} more"
        return f"Ledger: {summary} on {self.vault.machine}"


def _day(value: str) -> bool:
    return bool(ops.DAY.fullmatch(value)) and ops.is_day(value)


def _text(value: object) -> str | None:
    return value.isoformat() if isinstance(value, date) else cast(str | None, value)


def material_ref(vault: Vault, ref: str) -> str:
    """A material's ref as stored: a URL or a locator as given; a path with forward slashes, relative to the host
    repo's top in a folder vault and to the vault in a repo of its own, absolute only outside it."""
    text = ref.strip().strip('"').strip()
    if SCHEME.match(text) and not DRIVE.match(text):
        return text
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(vault.home.top.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return text.replace("\\", "/").removeprefix("./")


# -- committing -------------------------------------------------------------------------------------------------


def pending(vault: Vault) -> list[str]:
    """Op files a write that stopped before its commit left: new ones, and tracked ones that only grew. One that lost
    or changed a committed line refuses the write, since its commit would make the change for good."""
    changed = vault.git.changed_against_head(OPS_DIR)
    for rel in changed:
        if not check.committed_kept(vault, rel):
            raise LedgerError(
                f"{rel} lost or changed a committed op line; ops are written once, so put it back from git "
                "(`docket vault check` names it); nothing written"
            )
    new = [rel for rel in vault.git.not_in_head(OPS_DIR) if ops.FILE_NAME.fullmatch(Path(rel).name)]
    return sorted({*changed, *new})


def commit_ops(vault: Vault, new: Sequence[Op], message: str) -> str | None:
    """Append ops to their file and commit it. A write's ops fall in one month on one machine, so it replaces one
    file through a temp file and a rename and lands whole or not at all (DR-0610); a commit that fails before it lands
    puts the file back."""
    rels = sorted({ops.rel_of(op) for op in new})
    if len(rels) != 1:
        raise LedgerError(f"a write's ops fall in one month, and these span {len(rels)} files; nothing written")
    rel = rels[0]
    ops.folder(vault.root).mkdir(parents=True, exist_ok=True)
    path = vault.root / rel
    if ops.linked(path):
        raise LedgerError(f"{rel} is a link; ops are written only as plain files in the vault; nothing written")
    before = ops.text_of(path, rel) if path.exists() else None
    also = pending(vault)
    text = before or ""
    sep = "\n" if text and not text.endswith("\n") else ""
    write_replacing(vault.home, path, text + sep + "".join(f"{op.text()}\n" for op in new), "ledger-ops")
    head = vault.git.head_id()
    try:
        return vault.commit(sorted({rel, *also}), console.signed(message))
    except BaseException as e:
        # A failure after the commit landed leaves its ops where HEAD holds them: putting the file back would make
        # every committed line of the write look gone.
        if vault.git.head_id() != head:
            raise
        if not isinstance(e, UnmergedError):
            vault.git.run("reset", "-q", "--", rel, check=False)
        if before is None:
            path.unlink(missing_ok=True)
        else:
            write_replacing(vault.home, path, before, "ledger-restore")
        raise
