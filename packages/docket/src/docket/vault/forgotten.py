"""The forgotten list (DR-0579): what a forget took out of the vault for good, by hash, on every clone.

`state/forgotten.jsonl` is committed, one JSON line per forget: the rid hashes forgotten whole, each version forgotten
alone by its rid's hash and staging's key, a folder file's identity as `native` holds it, the rids no media is stored on
again, the media key ids every clone deletes, a removed legacy note's old paths' hashes and `import_hash`, and the
ids of the ledger ops it masked, written only when it masked one (DR-0695); when, and on which machine. Hashes and op
ids only, never a rid, a path or a key: a history rewrite takes the raw lines out of history, and the list stays in it
for good. An op id holds a time and random bits, never words, and its masked line keeps it anyway. Two machines can
append at once, so the list merges by the vault's own `*.jsonl merge=union`, and a merge can only list more.

In force are HEAD's lines and the disk's together, as the settings in force read vault.yaml (DR-0521): a line on disk
that HEAD lacks acts at once, and a line HEAD has that the disk lacks still acts and is named, and the list is never
committed while it lacks one. It fails closed like a release log: a line that isn't one, or the list or `state/` a link,
stops every reader, since skipping it would read what was forgotten.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from docket.documents import records as documents
from docket.errors import DocketError
from docket.vault.git import Git
from docket.vault.home import Home
from docket.vault.records import RawRecord, canonical_json, version
from docket.vault.staging import digest, key_of, rid_hash, write_replacing

if TYPE_CHECKING:  # the vault imports this module, to keep the list's cache by HEAD
    from docket.vault.vault import Vault

FORGOTTEN = "state/forgotten.jsonl"
# HEAD's list by the commit that holds it, so no git call is made while HEAD stays. Disposable: one that doesn't read
# is a miss.
CACHE = "committed-forgotten.json"

Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
KeyId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identity = Annotated[str, StringConstraints(pattern=r"^(?:sha256|redacted_sha256):[0-9a-f]{64}$")]
ImportHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
# A ledger op's id, a ULID as the op line holds it (`docket.ledger.ops.ULID`, which imports this module).
OpId = Annotated[str, StringConstraints(pattern=r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Version(_Model):
    """One version forgotten alone: its rid's hash and staging's key of rid and version."""

    rid: Hash
    key: Hash


class Forget(_Model):
    """One forget, as one line of the list."""

    schema_version: Literal[1] = 1
    rids: list[Hash] = Field(default_factory=list[Hash])
    versions: list[Version] = Field(default_factory=list[Version])
    files: list[Identity] = Field(default_factory=list[Identity])
    media: list[Hash] = Field(default_factory=list[Hash])
    keys: list[KeyId] = Field(default_factory=list[KeyId])
    old_paths: list[Hash] = Field(default_factory=list[Hash])
    import_hashes: list[ImportHash] = Field(default_factory=list[ImportHash])
    # The ledger ops masked (DR-0695): a Docket from before #212 fails closed on the key, as it can't mask them.
    ops: list[OpId] = Field(default_factory=list[OpId])
    at: datetime
    machine: str = Field(min_length=1)

    @field_validator("at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("a forget's time needs its time zone")
        return value.astimezone(UTC)

    def line(self) -> str:
        """The line, `ops` left out when it masked none: an older Docket still reads each forget that masks no op."""
        data = self.model_dump(mode="json")
        if not self.ops:
            del data["ops"]
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def identity_of(record: RawRecord) -> str | None:
    """A folder file record's identity as the list holds it, the key `native` keeps it under and its value (DR-0483):
    `sha256:<hex>` or `redacted_sha256:<hex>`; None for any other record, a part or a gone version. The folder source's
    names are read here, not when this module loads: importing them first imports the vault, which imports this."""
    if not documents.is_folder(record) or record.kind != documents.FILE_KIND or documents.is_gone(record):
        return None
    for key in (documents.BYTES_HASH, documents.TEXT_HASH):
        value = record.native.get(key)
        if isinstance(value, str):
            return f"{key}:{value}"
    return None


def old_path_hash(path: str) -> str:
    """An old path's hash, as the list keeps a removed legacy note's: staging's 32 hex of sha256."""
    return digest(path)


@dataclass(frozen=True)
class Forgotten:
    """The list in force, as read on one call: each field folded over every line. `missing` counts the lines HEAD has
    that the disk lacks, which act all the same. `root` is where a version's release is looked up."""

    rids: frozenset[str] = frozenset()
    versions: frozenset[tuple[str, str]] = frozenset()
    files: frozenset[str] = frozenset()
    media: frozenset[str] = frozenset()
    keys: frozenset[str] = frozenset()
    old_paths: frozenset[str] = frozenset()
    import_hashes: frozenset[str] = frozenset()
    ops: frozenset[str] = frozenset()
    missing: int = 0
    # The lines on disk HEAD lacks: in force already, and committed by the next gate call.
    pending: int = 0
    root: Path | None = field(default=None, compare=False)

    @property
    def acts(self) -> bool:
        """Whether a reader of run files has anything to skip: a rid, a version or a file listed."""
        return bool(self.rids or self.versions or self.files)

    @cached_property
    def digest(self) -> str:
        """What the index is built under (DR-0593): the sha256 of what the build reads, sorted, so a merge that only
        reorders lines changes nothing; never the keys, which change no row. The masked ops count once there are any
        (DR-0695), so a list without them keeps the digest it had. Empty when it reads nothing."""
        read: dict[str, list[str] | list[list[str]]] = {
            "rids": sorted(self.rids),
            "versions": sorted([rid, key] for rid, key in self.versions),
            "files": sorted(self.files),
            "media": sorted(self.media),
            "old_paths": sorted(self.old_paths),
            "import_hashes": sorted(self.import_hashes),
        }
        if self.ops:
            read["ops"] = sorted(self.ops)
        if not any(read.values()):
            return ""
        return hashlib.sha256(canonical_json(read).encode("utf-8")).hexdigest()

    @property
    def problem(self) -> str | None:
        """What a line HEAD has and the disk lacks makes of the list: it still acts, and the list isn't committed."""
        if not self.missing:
            return None
        return (
            f"{FORGOTTEN} lacks {self.missing} line(s) its last commit holds, which still act; restore it with git "
            f"checkout -- {FORGOTTEN}"
        )

    def whole(self, rid: str) -> bool:
        """Whether a rid was forgotten whole: it, or a rid it sits under by nesting (`plaud:<id>/<block>` under
        `plaud:<id>`), is on the list's rids. Never by thread or container, so a reply never goes with its thread."""
        if not self.rids:
            return False
        # Here, not at the top: media's modules import the vault, which imports this.
        from docket.media.layout import owners  # noqa: PLC0415

        return any(rid_hash(owner) in self.rids for owner in owners(rid))

    def versioned(self, rid: str) -> bool:
        """Whether some version of a rid was forgotten alone: a held rid's or a folder file's."""
        hashed = rid_hash(rid)
        return any(found == hashed for found, _ in self.versions)

    def media_refused(self, rid: str) -> bool:
        """Whether media may never be stored on a rid again: it was forgotten whole, or the list's media holds it."""
        return self.whole(rid) or rid_hash(rid) in self.media

    def legacy(self, old_paths: Collection[str], import_hash: str | None) -> bool:
        """Whether a legacy note was removed by a forget: one of its old paths' hashes, or its body's hash, is listed,
        so a note renamed, moved or copied in its folder stays out too."""
        return any(old_path_hash(path) in self.old_paths for path in old_paths) or (
            import_hash is not None and import_hash in self.import_hashes
        )

    def covers(self, record: RawRecord, *, versions: bool = True) -> bool:
        """Whether the list takes a record: by its rid or a rid it sits under, by a folder file's identity, or by its
        version, unless a release keeps that exact version, the owner's later word at the console. A folder file's parts
        follow their file only in a `Reading`, which sees the file's line before theirs. Without `versions`, by its rid
        and identity alone: intake asks of a version only once a release has cut it and redaction made it."""
        if not self.acts:
            return False
        if self.whole(record.rid):
            return True
        found = identity_of(record) if self.files else None
        if found is not None and found in self.files:
            return True
        if not self.versions or not versions:
            return False
        v = record.v or version(record)
        return (rid_hash(record.rid), key_of(record.rid, v)) in self.versions and not self._kept(record, v)

    def _kept(self, record: RawRecord, v: str) -> bool:
        """Whether a release of the record's source keeps this exact version. Read only when a version is listed, and
        failing closed as the release log does."""
        if self.root is None:
            return False
        from docket.vault import releaselog  # noqa: PLC0415 - the release log imports the vault, which imports this

        granted = releaselog.read(self.root, record.source)
        return any((record.rid, v) in one.keep for one in granted.values())

    def reading(self, files: Iterable[str] = ()) -> Reading:
        """A pass in run order; `files` are the folder files whose parts an earlier pass found the list takes."""
        return Reading(self, files)


EMPTY = Forgotten()


class Reading:
    """One pass over run files in run order. A folder file's parts carry no identity of their own, so they go with the
    file line before them whose identity is listed; a later line of that file with another identity, a changed file or
    a new one at its path, lets the parts after it in. A pass over new run files alone starts from the files an earlier
    pass left marked, as an index update does."""

    def __init__(self, listed: Forgotten, files: Iterable[str] = ()) -> None:
        self.listed = listed
        self._files: set[str] = set(files)

    @property
    def files(self) -> frozenset[str]:
        """The folder files whose parts this pass takes from here on."""
        return frozenset(self._files)

    def skips(self, record: RawRecord, *, versions: bool = True) -> bool:
        listed = self.listed
        if not listed.acts:
            return False
        if listed.files and documents.is_folder(record):
            if record.kind == documents.FILE_KIND:
                if identity_of(record) in listed.files:
                    self._files.add(record.rid)
                else:
                    self._files.discard(record.rid)
            elif documents.file_of(record.rid) in self._files:
                return True
        return listed.covers(record, versions=versions)


def fold(lines: Sequence[Forget], missing: int = 0, root: Path | None = None, pending: int = 0) -> Forgotten:
    return Forgotten(
        rids=frozenset(rid for line in lines for rid in line.rids),
        versions=frozenset((v.rid, v.key) for line in lines for v in line.versions),
        files=frozenset(found for line in lines for found in line.files),
        media=frozenset(rid for line in lines for rid in line.media),
        keys=frozenset(key for line in lines for key in line.keys),
        old_paths=frozenset(path for line in lines for path in line.old_paths),
        import_hashes=frozenset(found for line in lines for found in line.import_hashes),
        ops=frozenset(op for line in lines for op in line.ops),
        missing=missing,
        pending=pending,
        root=root,
    )


# -- reading -----------------------------------------------------------------------------------------------------------


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def list_path(root: Path) -> Path:
    """The list's path, never through a link."""
    folder = root / "state"
    if _linked(folder):
        raise DocketError("state is a link; the forgotten list is read and written only as a plain file in the vault")
    path = root / FORGOTTEN
    if _linked(path):
        raise DocketError(f"{FORGOTTEN} is a link; the forgotten list is a plain file in the vault")
    return path


def parse(text: str, where: str) -> list[Forget]:
    """The forgets in the list's text. A bad line is named by its number only, like a release log's, with its two
    causes: a newer Docket's line, such as one naming the ops it masked to a Docket from before #212, which this one
    can't obey until it is updated, or a damaged one, which git gives back. Deleting a line is never the fix: a line
    HEAD holds acts all the same."""
    out: list[Forget] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(Forget.model_validate_json(line))
        except ValidationError:
            problem = (
                f"{where}: line {n} isn't a forget this Docket reads. Written by a newer Docket: update this one; "
                f"damaged: git checkout -- {FORGOTTEN} (the forgotten list fails closed)"
            )
            raise DocketError(problem) from None
    return out


def _text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocketError(f"{FORGOTTEN} isn't UTF-8") from None
    except OSError as e:
        raise DocketError(f"{FORGOTTEN}: can't read it: {e.strerror or type(e).__name__}") from None


# The list in force per vault root, by the disk file's size, modification time and file id, and HEAD's commit: every
# reader of raw asks on every call, and the list in force can't change without one of them moving. A write through a
# rename gives the file a new id, even inside one tick of the clock.
_LOADED: dict[Path, tuple[tuple[object, ...], Forgotten]] = {}


def load(root: Path, home: Home | None) -> Forgotten:
    """The list in force for the vault at `root`: the disk's lines and HEAD's. Without a home, the folder isn't a
    vault's, and only what its disk holds is read."""
    path = list_path(root)
    try:
        st = path.stat()
        stamp: tuple[int, int, int] | None = (st.st_size, st.st_mtime_ns, st.st_ino)
    except FileNotFoundError:
        stamp = None
    commit = Git(root, home=home).head_id() if home is not None else None
    key = (stamp, commit)
    held = _LOADED.get(root)
    if held is not None and held[0] == key:
        return held[1]
    disk = parse(_text(path), FORGOTTEN) if stamp is not None else []
    committed: list[Forget] = []
    if home is not None and commit is not None:
        committed = parse(committed_text(root, home, commit), f"HEAD:{FORGOTTEN}")
    have = {line.line() for line in disk}
    held = {line.line() for line in committed}
    missing = [line for line in committed if line.line() not in have]
    pending = sum(1 for line in disk if line.line() not in held) if home is not None else 0
    found = fold([*disk, *missing], len(missing), root, pending)
    _LOADED[root] = (key, found)
    return found


def at(root: Path) -> Forgotten:
    """The list in force for a reader that holds only the vault's root: its home found as the vault's own is, a repo
    of its own where the root has a .git, else a folder of the repo around it. A folder that is neither reads only
    what its disk holds."""
    try:
        home: Home | None = Home.resolve(root, "repo" if (root / ".git").exists() else "folder")
    except DocketError:
        home = None
    return load(root, home)


def committed_text(root: Path, home: Home, commit: str) -> str:
    """HEAD's list, empty when HEAD has none: from the cache when HEAD is the commit it was read at, else from git."""
    cached = _cached(home)
    if cached is not None and cached[0] == commit:
        return cached[1]
    git = Git(root, home=home)
    blob = f"{commit}:./{FORGOTTEN}"
    held = git.run("cat-file", "-e", blob, check=False).returncode == 0
    text = git.out("cat-file", "blob", blob).removeprefix("﻿").replace("\r\n", "\n") if held else ""
    remember(home, commit, text)
    return text


def carry(home: Home, commit: str, parents: Sequence[str], paths: Sequence[str]) -> None:
    """After a commit of Docket's own that didn't name the list, made on the cached commit: the cache moves to it, as
    the settings in force's does (DR-0521)."""
    if FORGOTTEN in paths:
        return
    cached = _cached(home)
    if cached is not None and list(parents) == [cached[0]]:
        remember(home, commit, cached[1])


def _cached(home: Home) -> tuple[str, str] | None:
    try:
        data = json.loads((home.private / CACHE).read_text(encoding="utf-8"))
        commit, text = data["commit"], data["text"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (commit, text) if isinstance(commit, str) and isinstance(text, str) else None


def remember(home: Home, commit: str, text: str) -> None:
    """File HEAD's list under its commit. A write that fails leaves a miss for the next read, never an error."""
    with contextlib.suppress(OSError, DocketError):
        body = json.dumps({"commit": commit, "text": text})
        write_replacing(home, home.private / CACHE, body, f"forgotten-cache.{secrets.token_hex(4)}")


# -- writing -----------------------------------------------------------------------------------------------------------


def write_line(vault: Vault, forget: Forget) -> None:
    """Add one line to the list on disk, through a temp file and a rename, fsynced: in force from here on. A list that
    doesn't read is never added to."""
    with vault.writing():
        path = list_path(vault.root)
        text = _text(path) if path.exists() else ""
        parse(text, FORGOTTEN)
        if text and not text.endswith("\n"):
            text += "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_replacing(vault.home, path, f"{text}{forget.line()}\n", "forgotten")


def append(vault: Vault, forget: Forget) -> str | None:
    """A forget's first two writes: its line, then the list's commit alone, naming nothing it took (DR-0581). A commit
    that fails leaves the line on disk, in force, for the next call to commit."""
    with vault.writing():
        write_line(vault, forget)
        return commit_pending(vault, f"Forget: the list from {vault.machine}")


def commit_pending(vault: Vault, message: str | None = None) -> str | None:
    """Commit the list when it differs from HEAD, never while it lacks a line HEAD has: an edit of it only adds. A list
    an ignore rule hides counts too, so its add fails loudly instead of the list staying out of git unseen."""
    if not (vault.root / FORGOTTEN).exists():
        return None
    problem = load(vault.root, vault.home).problem
    if problem is not None:
        raise DocketError(problem)
    git = vault.git
    if not (git.untracked(FORGOTTEN, ignored_too=True) or git.changed_against_head(FORGOTTEN)):
        return None
    return vault.commit([FORGOTTEN], message or f"Forget: the list committed on {vault.machine}")
