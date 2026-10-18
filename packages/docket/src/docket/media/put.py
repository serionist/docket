"""`media_put` (DR-0547): a record's media, fetched within the limits, encrypted while it is read, and staged beside its
record. Three steps, so the service runs the two that write on its write queue and the download beside it:

| Step | What |
|---|---|
| `check` | a source this clone ingests (its cursor file holds it); the key store; the four checks; the ranges to cut |
| `fetch` | outside the lock: the file or the URL, encrypted into a private temp file as the bytes come |
| `finish` | under the lock: the checks and the ranges again; then the key, the sidecar and the rename into staging |

The withheld marks are this clone's alone, hence the cursor. The checks run again because a fetch can take minutes.
A file taken from the drop folder is deleted once it is stored; one from a media folder is left as it is. Audio with
no ranges is stored as it came; audio with ranges is cut before it is encrypted, and its plain files go on every path
(DR-0559 to DR-0561).
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from docket import paths, userconfig
from docket.errors import DocketError
from docket.media import crypto, cutter, layout
from docket.media import ranges as cut_ranges
from docket.media import staged as media_staged
from docket.media.fetch import CHUNK, MAX_BYTES, Capped, Fetcher, Limits, media_folder, open_path
from docket.media.keystore import KeyStore
from docket.media.ranges import Needed, Range
from docket.vault import cursors
from docket.vault.gate import Gate
from docket.vault.records import RID, is_native_id
from docket.vault.vault import Vault

DROP = "drop"


@dataclass(frozen=True)
class Stored:
    """What a put staged: the rid, the file's type and size."""

    rid: str
    ext: str
    bytes: int
    # For audio that was cut: how many stretches, and what came in.
    cut: str | None = None

    @property
    def line(self) -> str:
        mib = self.bytes / (1024 * 1024)
        cut = f", {self.cut}" if self.cut else ""
        return f"staged {self.ext} media for {self.rid} ({mib:.1f} MiB){cut}; it is stored once its record is in raw"


@dataclass(frozen=True)
class Fetched:
    """A fetched file, encrypted in a temp file, with what staging it needs. Its key lives in memory only until
    `finish` writes it."""

    rid: str
    tmp: Path
    secret: str
    key: str
    ext: str
    bytes: int
    taken: Path | None
    cuts: tuple[Range, ...] = ()
    cut: str | None = None


def drop_folder() -> Path:
    """Docket's own drop folder on this machine: any source's media may be put from it."""
    return paths.state_dir() / DROP


def limits() -> Limits:
    return Limits(max_bytes=userconfig.media_max_bytes(MAX_BYTES))


def check(vault: Vault, rid: str, keys: KeyStore) -> Needed:
    """Refuse a put that can't go in, before anything is fetched; the ranges its media must be cut by."""
    if RID.match(rid) is None or not is_native_id(rid.partition(":")[2]):
        raise DocketError(f"{rid!r} isn't a rid: <source>:<id>")
    source = layout.source_of(rid)
    if not crypto.available():  # pragma: no cover - every system CI runs on has a wheel
        raise DocketError(crypto.MISSING)
    keys.ensure()
    if source not in cursors.read(vault.root, vault.machine):
        raise DocketError(f"this machine hasn't ingested {source}; put its media where it runs")
    return _planned(Gate(vault).media_plan(rid))


def fetch(
    vault: Vault,
    rid: str,
    *,
    path: str | None,
    url: str | None,
    fetcher: Fetcher | None = None,
    needed: Needed | None = None,
) -> Fetched:
    """The media, encrypted while it is read into a temp file; with ranges to cut, cut first in the child, its plain
    files only in this process's cut folder and gone on every path. On a failure the temp files are gone and the error
    says why: the cap, the time, the host's answer, the cutter's."""
    if (path is None) == (url is None):
        raise DocketError("media_put takes a path or a url: one of them")
    bounds = fetcher.limits if fetcher is not None else limits()
    # The settings in force as the vault was opened; the checks after the fetch read them again, through the gate.
    entry = vault.config.sources.get(layout.source_of(rid))
    if entry is None:
        raise DocketError(f"source {layout.source_of(rid)} isn't in vault.yaml in force")
    taken: Path | None = None
    stream: BinaryIO
    if path is not None:
        roots = [f for f in (media_folder(vault.root, value) for value in entry.media_folders) if f is not None]
        if drop_folder().is_dir():
            roots.append(drop_folder().resolve())
        stream, taken = open_path(path, roots, bounds)
        closing = stream.close
    else:
        response, conn = (fetcher or Fetcher(bounds)).open(url or "")
        stream = response

        def closing() -> None:
            response.close()
            conn.close()

    if needed is not None and needed.ranges:
        return _fetch_cut(vault, rid, Capped(stream, bounds), closing, taken=taken, needed=needed)
    secret, recipient = crypto.new_identity()
    tmp = media_staged.temp_file(vault.home)
    reader = Capped(stream, bounds)
    try:
        with tmp.open("wb") as out:
            crypto.encrypt(reader, out, recipient)
            out.flush()
            os.fsync(out.fileno())
        return Fetched(rid, tmp, secret, crypto.key_id(tmp), layout.sniff(reader.head), reader.count, taken)
    except DocketError as e:
        tmp.unlink(missing_ok=True)
        raise DocketError(reader.failure or str(e)) from None
    finally:
        closing()


def _fetch_cut(
    vault: Vault, rid: str, reader: Capped, closing: Callable[[], None], *, taken: Path | None, needed: Needed
) -> Fetched:
    """The media into a plain file in this process's cut folder, cut in the child, and the cut audio encrypted into
    the temp file `finish` stages. Both plain files go on every path."""
    plain = cutter.plain_file(vault.home, ".in")
    done: cutter.Cut | None = None
    try:
        try:
            with plain.open("wb") as out:
                while chunk := reader.read(CHUNK):
                    out.write(chunk)
        except (DocketError, OSError) as e:
            raise DocketError(reader.failure or str(e)) from None
        finally:
            closing()
        ext = layout.sniff(reader.head)
        if ext not in layout.AUDIO:
            raise DocketError(f"{rid}'s media must be cut, and Docket cuts only audio, not a {ext} file")
        done = cutter.cut(vault.home, plain, ext, needed.ranges, max_bytes=reader.limits.max_bytes)
        problem = cutter.timeline_problem(rid, needed, done)
        if problem is not None:
            raise DocketError(problem)
        secret, recipient = crypto.new_identity()
        tmp = media_staged.temp_file(vault.home)
        try:
            with done.path.open("rb") as source, tmp.open("wb") as out:
                crypto.encrypt(source, out, recipient)
                out.flush()
                os.fsync(out.fileno())
            key = crypto.key_id(tmp)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        stretches = len(cut_ranges.merge(needed.ranges))
        note = f"cut at {stretches} stretch(es); {done.source}{cutter.onset_note(done)}"
        return Fetched(rid, tmp, secret, key, "webm", done.path.stat().st_size, taken, needed.ranges, note)
    finally:
        cutter.done_with(plain, done.path if done is not None else None)


def finish(vault: Vault, fetched: Fetched, keys: KeyStore) -> Stored:
    """Under the lock: the checks again, and the ranges, which the cut must hold; then the key, the sidecar and the
    file into staging, replacing what was staged for the rid. The temp file is gone either way."""
    rid = fetched.rid
    try:
        with vault.lock:
            needed = _planned(Gate(vault).media_plan(rid))
            if not cut_ranges.covers(fetched.cuts, needed.ranges):
                raise DocketError(f"{rid}'s cuts changed while its media was fetched; put it again")
            media_staged.delete(vault.root, keys, rid, vault.git.in_head)
            target = layout.staged_file(vault.root, rid, fetched.ext)
            keys.add(fetched.key, fetched.secret, target.relative_to(vault.root).as_posix())
            sidecar = layout.Sidecar(
                rid=rid,
                key=fetched.key,
                ext=fetched.ext,
                bytes=fetched.bytes,
                put_at=datetime.now(UTC),
                cuts=[layout.Span(start=start, end=end) for start, end in fetched.cuts],
            )
            media_staged.write_sidecar(vault.home, layout.sidecar_path(vault.root, rid), sidecar)
            media_staged.move(fetched.tmp, target)
    finally:
        fetched.tmp.unlink(missing_ok=True)
    if fetched.taken is not None and drop_folder().is_dir() and fetched.taken.is_relative_to(drop_folder().resolve()):
        with contextlib.suppress(OSError):
            fetched.taken.unlink()
    return Stored(rid, fetched.ext, fetched.bytes, fetched.cut)


def put(
    vault: Vault,
    rid: str,
    *,
    path: str | None = None,
    url: str | None = None,
    keys: KeyStore | None = None,
    fetcher: Fetcher | None = None,
) -> Stored:
    """The three steps in a row, as the CLI runs them."""
    store = keys or KeyStore(vault.home)
    needed = check(vault, rid, store)
    return finish(vault, fetch(vault, rid, path=path, url=url, fetcher=fetcher, needed=needed), store)


def _planned(found: Needed | str) -> Needed:
    if isinstance(found, str):
        raise DocketError(found)
    return found
