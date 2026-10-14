"""Importing an existing notes folder: each markdown note becomes a legacy-note doc that keeps its old path.

The folder is only read. In a git repo it is listed through git's own ignore rules, with git told not to refresh that
repo's index, so a note the user kept out of git never reaches the vault and nothing in the repo changes; outside a
repo it is walked, dot folders left out. Every listed path is screened against the never-store keywords before
anything logs, reports or reads it, then each note's text is screened and redacted.

A legacy doc follows its note: `import_hash` is the hash of the body the import last wrote, so a re-run writes a new
rev only when the note changed, new covers were found, or a new redaction rule changes the body. An edit made in the
vault is kept until the note itself changes; when both changed, the doc is kept and reported with the `--take` that
settles it. Covers come from the records the note's head names, matched on generic record fields only (DR-0108).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from docket.errors import DocketError
from docket.vault.anchors import normalize_newlines
from docket.vault.config import NeverStore
from docket.vault.docstore import DocStore
from docket.vault.git import Git
from docket.vault.legacy import normalize
from docket.vault.neverstore import fold, matches_text
from docket.vault.processed import (
    DEVICE_NAMES,
    SLUG_MAX,
    DocMeta,
    DocRefusedError,
    ProcessedDoc,
    doc_path,
    old_path_problem,
    parse_doc,
)
from docket.vault.records import RawRecord
from docket.vault.redact import Redactor
from docket.vault.segments import segments
from docket.vault.vault import Vault

log = logging.getLogger(__name__)

BY = "docket import"
MAX_BYTES = 2 * 1024 * 1024
HEAD_LINES = 40
MIN_TOKEN = 6
MIN_RUN = 12
CLASH_STEM = SLUG_MAX - 7
SHORT_HASH = 8
# A git command run in the notes' repo must not refresh that repo's index file, which plain status-like calls may do.
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0"}

_MARKDOWN = (".md", ".markdown")
_SPLIT = re.compile(r"[\s()\[\]<>{},;\"'`]+")
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_SUBHEADING = re.compile(r"^ {0,3}#{2,6}(?:[ \t]|$)")
_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*)$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_RUNS = re.compile(r"[A-Za-z0-9]+")
_WORDS = re.compile(r"[^\W_]+")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")

Side = Literal["note", "vault"]


class Take(BaseModel):
    """Which side wins for a note reported as changed both in its folder and in the vault, and the note's short hash
    the report printed, so a note changed since isn't taken blind."""

    side: Side
    hash: str
    old_path: str

    @staticmethod
    def parse(text: str) -> Take:
        """`<side>:<hash>:<old path>`; an old path holds no `:`, so the split is never ambiguous."""
        side, _, rest = text.partition(":")
        short, _, old_path = rest.partition(":")
        if side not in ("note", "vault") or not short or not old_path:
            raise DocketError(f"--take {text}: write it as note:<hash>:<old path> or vault:<hash>:<old path>")
        return Take(side="note" if side == "note" else "vault", hash=short, old_path=normalize(old_path))


class Kept(BaseModel):
    """A note the import didn't write, with its doc and the note's short hash for `--take`."""

    old_path: str
    doc: str
    hash: str


class ImportReport(BaseModel):
    prefix: str
    dry_run: bool
    sample: str | None = None
    new: int = 0
    updated: int = 0
    covers: int = 0
    same: int = 0
    gone: int = 0
    covered: int = 0
    kept: list[Kept] = Field(default_factory=list[Kept])
    deleted: list[Kept] = Field(default_factory=list[Kept])
    refused: list[str] = Field(default_factory=list[str])
    reported: list[str] = Field(default_factory=list[str])
    skipped: dict[str, int] = Field(default_factory=dict[str, int])
    commit: str | None = None

    @property
    def ok(self) -> bool:
        """False while a note is kept or refused, so a script notices."""
        return not self.kept and not self.refused


@dataclass(frozen=True)
class Listed:
    old_path: str
    path: Path


@dataclass(frozen=True)
class Light:
    """What a cover is matched on: generic record fields only, never a source's own."""

    source: str
    ts_day: str
    title: frozenset[str]
    timed: bool


@dataclass
class Plan:
    """What the run decided for one note: the doc to write, and how to count it."""

    listed: Listed
    doc_id: str
    rev: int
    text: str
    count: str


# -- the folder ---------------------------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return Git(cwd).run(*args, check=False, env=GIT_ENV)


def repo_root(folder: Path) -> Path | None:
    """The git repo the folder is in, or None. A repo git refuses to read stops the import: reading it without its
    ignore rules could take in what the user kept out of git."""
    if not any((p / ".git").exists() for p in (folder, *folder.parents)):
        return None
    proc = _git(folder, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        why = (proc.stderr or proc.stdout).strip()
        raise DocketError(f"git can't read the repo {folder} is in, so its ignore rules can't be kept: {why}")
    return Path(proc.stdout.strip()).resolve()


def default_prefix(folder: Path, repo: Path | None) -> str:
    """The part of an old path before the path inside the folder: the folder from its repo's root, else its name."""
    if repo is None:
        return folder.name
    rel = folder.relative_to(repo).as_posix()
    return "" if rel == "." else rel


def clean_prefix(given: str) -> str:
    value = normalize(given).strip("/")
    if value == ".":
        return ""
    if ":" in value or (value and old_path_problem(value)):
        raise DocketError(f"--as {given}: a prefix is a relative path such as notes/old, or . for none")
    return value


def list_folder(folder: Path, prefix: str, repo: Path | None, vault_root: Path, skipped: Counter[str]) -> list[Listed]:
    """Every file in the folder as (old path, path), in old-path order; links and nested repos are counted only."""
    inner: list[tuple[str, Path]] = []
    if repo is not None:
        rel = folder.relative_to(repo).as_posix()
        proc = _git(repo, "ls-files", "-z", "-co", "--exclude-standard", "--", rel)
        if proc.returncode != 0:
            raise DocketError(f"git couldn't list {folder}: {(proc.stderr or proc.stdout).strip()}")
        base = "" if rel == "." else f"{rel}/"
        for entry in sorted({e.rstrip("/") for e in proc.stdout.split("\0") if e}):
            inner.append((entry.removeprefix(base), repo / entry))
    else:
        for dirpath, dirnames, filenames in os.walk(folder):
            here = Path(dirpath)
            for name in list(dirnames):
                sub = here / name
                if sub == vault_root:
                    dirnames.remove(name)
                elif name.startswith(".") or sub.is_symlink() or (sub / ".git").exists():
                    dirnames.remove(name)
                    if sub.is_symlink():
                        skipped["links"] += 1
                    elif not name.startswith("."):
                        skipped["nested repos"] += 1
            inner.extend(
                ((here / n).relative_to(folder).as_posix(), here / n) for n in filenames if not n.startswith(".")
            )
    out: list[Listed] = []
    for rel_inner, path in inner:
        if path.is_symlink():
            skipped["links"] += 1
        elif path == vault_root or vault_root in path.parents:
            continue
        elif path.is_dir():
            skipped["nested repos"] += 1
        elif path.is_file():
            old = unicodedata.normalize("NFC", f"{prefix}/{rel_inner}" if prefix else rel_inner)
            out.append(Listed(old, path))
    return sorted(out, key=lambda item: item.old_path)


# -- one note -----------------------------------------------------------------------------------------------------


def screened_path(old_path: str, lists: NeverStore) -> bool:
    """Whether a never-store keyword is in a file's whole old path, its prefix included, or in the slug its doc would
    get: either would carry the name into an id, a vault path and a commit. The id is `legacy-note:` and the slug, and
    that fixed prefix isn't screened, so a keyword such as `note` doesn't drop every note."""
    return matches_text(old_path, lists) or matches_text(slug_for(Path(old_path).stem), lists)


def slug_for(stem: str) -> str:
    """A doc slug from a file's stem: folded to ASCII, lowercased, other runs made `-`, trimmed, cut to 80."""
    decomposed = unicodedata.normalize("NFKD", stem)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    slug = _NOT_SLUG.sub("-", ascii_only).strip("-")[:SLUG_MAX].strip("-") or "note"
    return f"{slug}-note" if slug in DEVICE_NAMES else slug


def clash_slug(slug: str, old_path: str) -> str:
    """A slug taken by another note, made the old path's own: cut to 73, then `-` and 6 hex of the old path's hash."""
    stem = slug[:CLASH_STEM].strip("-") or "note"
    return f"{stem}-{hashlib.sha256(old_path.encode('utf-8')).hexdigest()[:6]}"


def canonical(body: str) -> str:
    """A body as a doc gives it back once written: #6's render trims trailing newlines and adds one, and an empty body
    writes nothing. Hashing this, never the raw note, keeps a note ending in a blank line from looking edited."""
    meta = DocMeta(id="legacy-note:x", type="legacy-note", by=BY)
    return parse_doc(ProcessedDoc(meta, body, 0).render()).body


def body_hash(body: str) -> str:
    return "sha256:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def short(full: str) -> str:
    return full.removeprefix("sha256:")[:SHORT_HASH]


def head_of(body: str) -> list[str]:
    """The lines before the first heading of level two or deeper after the first line, at most 40: where a note says
    what it is about. A recording it only mentions further down isn't one it covers."""
    lines = body.split("\n")
    end = next((i for i, line in enumerate(lines) if i > 0 and _SUBHEADING.match(line)), len(lines))
    return lines[: min(end, HEAD_LINES)]


def head_tokens(head: list[str]) -> set[str]:
    text = "\n".join(head)
    tokens = {m.group(1).strip() for m in _BACKTICKED.finditer(text)}
    tokens |= {word.rstrip(".:!?") for word in _SPLIT.split(text)}
    return {t for t in tokens if len(t) >= MIN_TOKEN}


def first_heading(head: list[str]) -> str:
    return next((m.group(1) for line in head if (m := _HEADING.match(line))), "")


def words(text: str) -> frozenset[str]:
    """Title words worth matching: folded, three letters or more, not a number."""
    return frozenset(w for w in _WORDS.findall(fold(text)) if len(w) >= 3 and not w.isdigit())


class Names:
    """The raw log seen through the generic fields a note can name a record by: its rid, its id, the id's last long
    run of letters and digits; and for the fallback its source, the day it happened and its title's words."""

    def __init__(self, records: Iterable[RawRecord], zone: ZoneInfo) -> None:
        self.by_name: dict[str, set[str]] = {}
        self.light: dict[str, Light] = {}
        for record in records:
            day = record.ts.astimezone(zone).date().isoformat()
            self.light[record.rid] = Light(record.source, day, words(record.title or ""), bool(segments(record)))
        for rid in self.light:
            ident = rid.partition(":")[2]
            runs = _RUNS.findall(ident)
            names = {rid, ident, *([runs[-1]] if runs and len(runs[-1]) >= MIN_RUN else [])}
            for name in names:
                self.by_name.setdefault(name, set()).add(rid)

    def covers(self, listed: Listed, body: str) -> list[str]:
        head = head_of(body)
        named = sorted({rid for token in head_tokens(head) for rid in self.by_name.get(token, set())})
        if named:
            return named
        stem = Path(listed.old_path).stem
        m = _DATE.match(stem)
        if m is None:
            return []
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return []
        folders = {fold(part) for part in listed.old_path.split("/")[:-1]}
        said = words(f"{stem} {first_heading(head)}")
        found = [
            rid
            for rid, light in self.light.items()
            if light.timed and light.ts_day == day and fold(light.source) in folders and light.title <= said
            and light.title
        ]  # fmt: skip
        return found if len(found) == 1 else []


# -- the run ------------------------------------------------------------------------------------------------------


def import_notes(
    vault: Vault,
    folder: Path,
    prefix: str | None = None,
    *,
    dry_run: bool = False,
    takes: list[Take] | None = None,
) -> ImportReport:
    """Import a notes folder's markdown notes as legacy-note docs, or say what would happen with `dry_run`."""
    folder = folder.resolve()
    if not folder.is_dir():
        raise DocketError(f"no folder {folder}")
    vault_root = vault.root.resolve()
    if folder == vault_root or vault_root in folder.parents:
        raise DocketError(f"{folder} is inside the vault; import a notes folder from outside it")
    repo = repo_root(folder)
    the_prefix = default_prefix(folder, repo) if prefix is None else clean_prefix(prefix)
    report = ImportReport(prefix=the_prefix, dry_run=dry_run)
    skipped: Counter[str] = Counter()
    lists: NeverStore = vault.config.never_store
    redactor = Redactor.from_config(vault.config.redaction)
    store = DocStore(vault)
    wanted = {t.old_path: t for t in takes or []}
    used: set[str] = set()

    listed = list_folder(folder, the_prefix, repo, vault_root, skipped)
    log.debug("import: %d file(s) listed", len(listed))
    docs = [d for d in store.all() if d.meta.type == "legacy-note"]
    revs = store.latest_revs("legacy-note")
    by_old: dict[str, list[ProcessedDoc]] = {}
    for d in docs:
        for path in d.meta.old_paths:
            by_old.setdefault(path.casefold(), []).append(d)
    deleted = _deleted(store, revs, {d.meta.id for d in docs})
    dirty = set(vault.git.changed_against_head("processed/legacy-note")) | set(
        vault.git.untracked("processed/legacy-note")
    )
    names = Names(_latest(vault), ZoneInfo(vault.config.owner.time_zone))
    taken: set[str] = set(revs) | {d.meta.id for d in docs}
    seen: set[str] = set()
    plans: list[Plan] = []

    for item in listed:
        if screened_path(item.old_path, lists):
            skipped["never-store"] += 1
            continue
        if not item.old_path.casefold().endswith(_MARKDOWN):
            skipped[Path(item.old_path).suffix.lower() or "no extension"] += 1
            continue
        slug = slug_for(Path(item.old_path).stem)
        seen.add(item.old_path)
        why = old_path_problem(item.old_path)
        if why is not None:
            report.refused.append(f"{item.old_path}: {why}")
            continue
        body = _read(item, report)
        if body is None:
            continue
        if matches_text(body, lists):
            skipped["never-store"] += 1
            continue
        new_body = canonical(redactor.text(body))
        new_hash = body_hash(new_body)
        found = names.covers(item, new_body)
        report.covered += bool(found)
        report.sample = report.sample or item.old_path
        take = wanted.get(item.old_path)
        doc = _match(by_old, item.old_path)
        if take is not None:
            used.add(item.old_path)
        if doc is None:
            gone_id = deleted.get(item.old_path)
            if gone_id is not None:
                if take is not None and take.side == "note" and take.hash == short(new_hash):
                    meta = _meta(gone_id, found, [item.old_path], new_hash)
                    plans.append(Plan(item, gone_id, revs[gone_id], _text(meta, new_body, revs[gone_id]), "updated"))
                    continue
                if take is not None:
                    why = "only note brings back a deleted doc" if take.side == "vault" else "the note changed since"
                    report.refused.append(f"--take {take.side}:{take.hash}:{item.old_path}: {why}")
                report.deleted.append(Kept(old_path=item.old_path, doc=gone_id, hash=short(new_hash)))
                continue
            doc_id = _new_id(slug, item.old_path, taken, vault.root)
            if doc_id is None:
                report.refused.append(f"{item.old_path}: its doc id and the fallback id are both taken")
                continue
            taken.add(doc_id)
            meta = _meta(doc_id, found, [item.old_path], new_hash)
            plans.append(Plan(item, doc_id, 0, _text(meta, new_body, 0), "new"))
            continue
        plan = _decide(
            item,
            doc=doc,
            revs=revs,
            new_body=new_body,
            new_hash=new_hash,
            found=found,
            take=take,
            report=report,
            dirty=dirty,
        )
        if plan is not None:
            plans.append(plan)

    for path, take in wanted.items():
        if path not in used:
            report.refused.append(f"--take {take.side}:{take.hash}:{path}: no note there")
    report.gone = sum(
        1
        for d in docs
        if not any(p in seen for p in d.meta.old_paths)
        and any(p.startswith(f"{the_prefix}/") or not the_prefix for p in d.meta.old_paths)
    )
    report.skipped = dict(sorted(skipped.items()))
    _write(store, plans, report, the_prefix)
    return report


def _read(item: Listed, report: ImportReport) -> str | None:
    if item.path.stat().st_size > MAX_BYTES:
        report.reported.append(f"{item.old_path}: over 2 MB, skipped")
        return None
    try:
        return normalize_newlines(item.path.read_bytes().removeprefix(b"\xef\xbb\xbf").decode("utf-8"))
    except UnicodeDecodeError:
        report.reported.append(f"{item.old_path}: not UTF-8, skipped")
        return None


def _match(by_old: dict[str, list[ProcessedDoc]], old_path: str) -> ProcessedDoc | None:
    """The doc keeping this old path: exactly, else the one doc that keeps it in another case."""
    candidates = by_old.get(old_path.casefold(), [])
    exact = [d for d in candidates if old_path in d.meta.old_paths]
    if len(exact) == 1:
        return exact[0]
    return candidates[0] if len(candidates) == 1 else None


def _deleted(store: DocStore, revs: dict[str, int], on_disk: set[str]) -> dict[str, str]:
    """The old paths of legacy docs deleted in the vault, from each one's last rev: those notes stay out."""
    out: dict[str, str] = {}
    for doc_id in sorted(set(revs) - on_disk):
        try:
            last = store.at_rev(doc_id, revs[doc_id])
        except DocketError:
            continue
        for path in last.meta.old_paths:
            out[path] = doc_id
    return out


def _latest(vault: Vault) -> list[RawRecord]:
    latest: dict[str, RawRecord] = {}
    for _, _, record in vault.rawlog.iter_records():
        latest[record.rid] = record
    return list(latest.values())


def _new_id(slug: str, old_path: str, taken: set[str], root: Path) -> str | None:
    """A new note's id: its slug, or when another note holds that (a file or any `Doc:` commit), the old path's own."""
    for candidate in (slug, clash_slug(slug, old_path)):
        doc_id = f"legacy-note:{candidate}"
        if doc_id not in taken and not (root / doc_path(doc_id)).exists():
            return doc_id
    return None


def _meta(doc_id: str, covers: list[str], old_paths: list[str], import_hash: str, **keep: object) -> DocMeta:
    return DocMeta.model_validate(
        {"id": doc_id, "type": "legacy-note", "covers": covers, "by": BY, "old_paths": old_paths,
         "import_hash": import_hash, **keep}
    )  # fmt: skip


def _text(meta: DocMeta, body: str, rev: int) -> str:
    return ProcessedDoc(meta.model_copy(update={"rev": rev}), body, 0).render()


def _decide(
    item: Listed,
    *,
    doc: ProcessedDoc,
    revs: dict[str, int],
    new_body: str,
    new_hash: str,
    found: list[str],
    take: Take | None,
    report: ImportReport,
    dirty: set[str],
) -> Plan | None:
    """The Re-running table, for a note whose doc exists: what to write, or None."""
    meta = doc.meta
    rev = revs.get(meta.id, 0)
    stored_body = canonical(doc.body)
    stored_hash = body_hash(stored_body)
    covers = [*meta.covers, *(c for c in found if c not in meta.covers)]
    keep = {"entities": meta.entities, "supersedes": meta.supersedes}

    def plan(body: str, by: str, import_hash: str, count: str) -> Plan:
        fields = {**keep, "by": by}
        updated = _meta(meta.id, covers, meta.old_paths, import_hash, **fields)
        return Plan(item, meta.id, rev, _text(updated, body, rev), count)

    if new_hash == meta.import_hash:
        if covers != meta.covers:
            return plan(stored_body, meta.by, new_hash, "covers")
        if doc_path(meta.id).as_posix() in dirty and stored_hash == new_hash:
            # A run killed before its commit left this doc written and uncommitted; this run commits it.
            return plan(stored_body, meta.by, new_hash, "same")
        report.same += 1
        return None
    if stored_hash == meta.import_hash:
        return plan(new_body, BY, new_hash, "updated")
    if stored_hash == new_hash:
        return plan(stored_body, meta.by, new_hash, "updated")
    if take is not None and take.hash == short(new_hash):
        if take.side == "note":
            return plan(new_body, BY, new_hash, "updated")
        return plan(stored_body, meta.by, new_hash, "updated")
    if take is not None:
        now = short(new_hash)
        report.refused.append(
            f"--take {take.side}:{take.hash}:{item.old_path}: the note changed since; it is {now} now"
        )
    report.kept.append(Kept(old_path=item.old_path, doc=meta.id, hash=short(new_hash)))
    return None


def _write(store: DocStore, plans: list[Plan], report: ImportReport, prefix: str) -> None:
    """Write every planned doc in one commit. A doc refused because the vault moved on meanwhile (#6 took in an edit
    on disk first) is decided once more at its new rev; a second refusal is reported."""
    counts = Counter(p.count for p in plans)
    if report.dry_run or not plans:
        _tally(report, counts)
        return
    subject = f"Import notes: {counts['new']} new, {len(plans) - counts['new']} updated from {prefix or '.'}"
    results = store.put_many([p.text for p in plans], subject)
    retry: list[Plan] = []
    for p, result in zip(plans, results, strict=True):
        if isinstance(result, DocRefusedError):
            if any(problem.message.startswith("rev:") for problem in result.problems):
                retry.append(p)
            else:
                counts[p.count] -= 1
                report.refused.append(f"{p.listed.old_path}: {result}")
        elif result.commit is not None:
            report.commit = result.commit
    for p in retry:
        counts[p.count] -= 1
    again = _again(store, retry, report)
    for p, result in zip(again, store.put_many([p.text for p in again], subject) if again else [], strict=True):
        if isinstance(result, DocRefusedError):
            report.refused.append(f"{p.listed.old_path}: {result}")
            continue
        counts[p.count] += 1
        report.commit = result.commit or report.commit
    _tally(report, counts)


def _again(store: DocStore, retry: list[Plan], report: ImportReport) -> list[Plan]:
    """Plans for docs refused as stale, decided again against the doc as the vault now has it."""
    out: list[Plan] = []
    for p in retry:
        doc = store.get(p.doc_id)
        if doc is None:
            report.refused.append(f"{p.listed.old_path}: {p.doc_id} changed in the vault during the import")
            continue
        planned = parse_doc(p.text)
        revs = {p.doc_id: max((n for n, _ in store.history(p.doc_id)), default=0)}
        new_body = canonical(planned.body)
        decided = _decide(
            p.listed,
            doc=doc,
            revs=revs,
            new_body=new_body,
            new_hash=body_hash(new_body),
            found=planned.meta.covers,
            take=None,
            report=report,
            dirty=set(),
        )
        if decided is not None:
            out.append(decided)
    return out


def _tally(report: ImportReport, counts: Counter[str]) -> None:
    report.new += counts["new"]
    report.updated += counts["updated"]
    report.covers += counts["covers"]
    report.same += counts["same"]
