"""The entry files: finding them, checking them against the regime, and writing them back in canonical form."""

from __future__ import annotations

import copy
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devkit.regime import SOFT_DELETE, Kind, Regime
from devkit.repo import KitError, listed_files
from devkit.yamlio import Comments, emit_file, load_yaml, scan_comments

DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
CITATION = re.compile(r"\b(?:DR|CV)-[0-9]{4}\b")


@dataclass(frozen=True, order=True)
class Problem:
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class EntryFile:
    """One index.yaml or table file, as read."""

    rel: str
    folder: str | None
    raw: str
    comments: Comments = field(default_factory=Comments)
    sections: dict[str, list[dict[str, Any]]] = field(default_factory=dict[str, list[dict[str, Any]]])
    error: str | None = None
    created: bool = False
    dirty: bool = False


@dataclass
class Located:
    kind: Kind
    file: EntryFile
    entry: dict[str, Any]

    @property
    def key(self) -> str:
        return str(self.entry.get(self.kind.key, "?"))

    @property
    def living(self) -> bool:
        """Not superseded and not removed: what `list`, `search` and `door` show without `--all`."""
        return "removed" not in self.entry and self.entry.get("status") != "superseded"

    @property
    def where(self) -> str:
        return f"{self.file.rel}: {self.kind.name} {self.key}"


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A path glob as a regex: `**` crosses folders, `*` and `?` don't."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


class Store:
    """Every entry file in the repo, loaded."""

    def __init__(self, regime: Regime, files: dict[str, EntryFile]) -> None:
        self.regime = regime
        self.files = files

    @property
    def root(self) -> Path:
        return self.regime.root

    @staticmethod
    def load(regime: Regime) -> Store:
        files: dict[str, EntryFile] = {}
        for rel in _discover(regime):
            files[rel] = _read(regime, rel)
        return Store(regime, files)

    # -- reading -------------------------------------------------------------------------------------------------

    def entries(self, kind: str | None = None) -> Iterator[Located]:
        for f in self.files.values():
            for kname, items in f.sections.items():
                if kind is not None and kname != kind:
                    continue
                k = self.regime.kinds.get(kname)
                if k is None:
                    continue
                for e in items:
                    yield Located(k, f, e)

    def find(self, key: str, *, kind: str | None = None, folder: str | None = None) -> Located:
        hits: list[Located] = []
        for loc in self.entries(kind):
            value = str(loc.entry.get(loc.kind.key, ""))
            same = value.casefold() == key.casefold() if loc.kind.name == "terms" else value == key
            if same and (folder is None or loc.file.folder == folder):
                hits.append(loc)
        if not hits:
            raise KitError(f"no entry {key!r}" + (f" in {folder or '(root)'}" if folder is not None else ""))
        if len(hits) > 1:
            places = ", ".join(h.file.rel for h in hits)
            raise KitError(f"{key!r} is in several files ({places}); name one with --in <folder>")
        return hits[0]

    def chain(self, path: str) -> list[str]:
        """The folders from the root down to `path` (a folder, or a file's folder), as repo-relative posix paths."""
        rel = Path(path).as_posix().strip("/")
        if rel in ("", "."):
            return [""]
        target = self.root / rel
        parts = rel.split("/")
        if target.is_file() or (not target.exists() and "." in parts[-1]):
            parts = parts[:-1]
        return ["", *["/".join(parts[: i + 1]) for i in range(len(parts))]]

    def folder_file(self, folder: str) -> str:
        return f"{folder}/{self.regime.folder_file}" if folder else self.regime.folder_file

    # -- checking ------------------------------------------------------------------------------------------------

    def check(self, *, citations: bool = True) -> list[Problem]:
        problems: list[Problem] = []
        for f in self.files.values():
            problems.extend(self._check_file(f))
        problems.extend(self._check_keys())
        problems.extend(self._check_links())
        if citations:
            problems.extend(self._check_citations())
        return sorted(set(problems))

    def _check_file(self, f: EntryFile) -> list[Problem]:
        if f.error:
            return [Problem(f.rel, f.error)]
        out: list[Problem] = []
        for line in f.comments.stray:
            out.append(Problem(f"{f.rel}:{line}", "a comment here is lost by the canonical form; move it into a field"))
        for kname, items in f.sections.items():
            kind = self.regime.kinds[kname]
            for e in items:
                out.extend(Problem(Located(kind, f, e).where, m) for m in self._check_entry(kind, f, e))
        if not out:
            try:
                rendered = self.render(f)
            except KitError as e:
                out.append(Problem(f.rel, str(e)))
            else:
                if rendered != f.raw and not f.dirty:
                    out.append(Problem(f.rel, "not in canonical form; run `uv run docs format`"))
        return out

    def _check_entry(self, kind: Kind, f: EntryFile, e: dict[str, Any]) -> list[str]:
        out: list[str] = []
        allowed = set(kind.fields)
        out.extend(f"unknown field {k}" for k in e if k not in allowed)
        out.extend(f"missing {k}" for k in kind.required if k not in e)
        if kind.one_of and sum(1 for k in kind.one_of if k in e) != 1:
            out.append(f"needs exactly one of {', '.join(kind.one_of)}")
        for k, v in e.items():
            if k not in allowed:
                continue
            if k in kind.flags:
                if not isinstance(v, bool):
                    out.append(f"{k} must be true or false")
                continue
            if k in kind.list_fields:
                if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):  # pyright: ignore[reportUnknownVariableType]
                    out.append(f"{k} must be a list of text")
                continue
            if not isinstance(v, str):
                fix = f"uv run docs set {e.get(kind.key)} --set {k}={v}"
                out.append(f"{k} was read as a {type(v).__name__}, not text; write it again with `{fix}`")
                continue
            if v.strip() == "":
                out.append(f"{k} is empty")
            if (k in kind.dates or k == "removed") and not DATE.match(v):
                out.append(f"{k} must be a date, YYYY-MM-DD")
            if k in kind.values and v not in kind.values[k]:
                out.append(f"{k} must be one of {', '.join(kind.values[k])}")
        if ("removed" in e) != ("removed_why" in e):
            out.append("removed and removed_why go together")
        key = e.get(kind.key)
        if kind.id_pattern and isinstance(key, str) and not kind.id_pattern.match(key):
            out.append(f"id {key} doesn't match {kind.id_pattern.pattern}")
        if kind.name == "reading" and isinstance(e.get("path"), str):
            target = self.root / str(e["path"])
            if not target.exists() and "removed" not in e:
                out.append(f"path {e['path']} doesn't exist")
        return out

    def _check_keys(self) -> list[Problem]:
        out: list[Problem] = []
        seen: dict[tuple[str, str, str], str] = {}
        for loc in self.entries():
            raw_key = loc.entry.get(loc.kind.key)
            if not isinstance(raw_key, str):
                continue
            scope = loc.file.rel if loc.kind.name == "reading" else ""
            norm = raw_key.casefold() if loc.kind.name == "terms" else raw_key
            ident = (loc.kind.name, scope, norm)
            if ident in seen:
                out.append(Problem(loc.where, f"also in {seen[ident]}; a key is used once"))
            else:
                seen[ident] = loc.file.rel
        return out

    def _check_links(self) -> list[Problem]:
        out: list[Problem] = []
        decisions = {loc.key: loc for loc in self.entries("decisions")}
        for loc in decisions.values():
            e = loc.entry
            after: list[str] = list(e.get("superseded_by") or [])
            before: list[str] = list(e.get("supersedes") or [])
            if (e.get("status") == "superseded") != bool(after):
                out.append(Problem(loc.where, "status superseded and superseded_by go together"))
            for other in after:
                o = decisions.get(other)
                if o is None:
                    out.append(Problem(loc.where, f"superseded_by names {other}, which doesn't exist"))
                elif loc.key not in (o.entry.get("supersedes") or []):
                    out.append(Problem(loc.where, f"{other} doesn't list {loc.key} in supersedes"))
            for other in before:
                o = decisions.get(other)
                if o is None:
                    out.append(Problem(loc.where, f"supersedes names {other}, which doesn't exist"))
                elif loc.key not in (o.entry.get("superseded_by") or []):
                    out.append(Problem(loc.where, f"{other} doesn't list {loc.key} in superseded_by"))
        for loc in self.entries("conventions"):
            d = loc.entry.get("decision")
            if isinstance(d, str) and d not in decisions:
                out.append(Problem(loc.where, f"decision {d} doesn't exist"))
        return out

    def citation_targets(self) -> dict[str, Located]:
        return {loc.key: loc for loc in self.entries() if loc.kind.numbered}

    def citations(self) -> Iterator[tuple[str, int, str]]:
        """Every DR- and CV- id cited in the repo's text files: (file, line, id)."""
        for rel in listed_files(self.root):
            path = self.root / rel
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8192]:
                continue
            text = data.decode("utf-8", errors="replace")
            for n, line in enumerate(text.split("\n"), start=1):
                for m in CITATION.finditer(line):
                    yield rel, n, m.group(0)

    def is_record(self, rel: str) -> bool:
        """A file where a citation is history, not a living reference: records, the changelog, the entry files."""
        if rel in self.files or rel == "docs/docs-regime.yaml":
            return True
        return any(glob_regex(g).match(rel) for g in self.regime.citation_free)

    def _check_citations(self) -> list[Problem]:
        out: list[Problem] = []
        targets = self.citation_targets()
        for rel, line, ident in self.citations():
            t = targets.get(ident)
            if t is None:
                out.append(Problem(f"{rel}:{line}", f"cites {ident}, which doesn't exist"))
            elif not t.living and not self.is_record(rel):
                state = "removed" if "removed" in t.entry else "superseded"
                out.append(Problem(f"{rel}:{line}", f"cites {ident}, which is {state}; cite what replaced it"))
        return out

    # -- writing -------------------------------------------------------------------------------------------------

    def render(self, f: EntryFile) -> str:
        sections: list[tuple[str, list[dict[str, Any]], Any]] = []
        names = self.regime.folder_kinds if f.folder is not None else list(f.sections)
        for name in names:
            if f.sections.get(name):
                sections.append((name, f.sections[name], self.regime.kinds[name].shape))
        return emit_file(sections, f.comments, where=f.rel)

    def snapshot(self) -> Store:
        return Store(self.regime, copy.deepcopy(self.files))

    def save(self, changed: set[str]) -> list[str]:
        """Write each changed file in canonical form through a temp file and a rename; refuse one changed on disk."""
        written: list[str] = []
        for rel in sorted(changed):
            f = self.files[rel]
            path = self.root / rel
            on_disk = path.read_text(encoding="utf-8") if path.exists() else None
            if (on_disk if on_disk is not None else "") != (f.raw if not f.created else ""):
                raise KitError(f"{rel} changed on disk since it was read; run the command again")
            if f.comments.stray:
                raise KitError(f"{rel} has comments the canonical form would lose; move them into fields first")
            text = self.render(f)
            if text == f.raw:
                f.dirty = False
                continue
            self.verify_round_trip(f, text)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".docs-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
                    out.write(text)
                Path(tmp).replace(path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            f.raw = text
            f.created = False
            f.dirty = False
            written.append(rel)
        return written

    def verify_round_trip(self, f: EntryFile, text: str) -> None:
        """Refuse to write text that doesn't read back as exactly the data it was rendered from."""
        back: Any = load_yaml(text) or {}
        expected = {k: v for k, v in f.sections.items() if v}
        if back != expected:
            raise KitError(f"{f.rel}: the canonical form would change a value; nothing was written")


def _discover(regime: Regime) -> list[str]:
    root = regime.root
    excluded = [glob_regex(g) for g in regime.exclude] + [glob_regex(f"{g}/**") for g in regime.exclude]
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [d for d in dirnames if not any(x.match(f"{rel_dir}/{d}".lstrip("/")) for x in excluded)]
        if regime.folder_file in filenames:
            found.add(f"{rel_dir}/{regime.folder_file}".lstrip("/"))
    for table in regime.table_files:
        if (root / table).is_file():
            found.add(table)
    return sorted(found)


def _read(regime: Regime, rel: str) -> EntryFile:
    raw = (regime.root / rel).read_text(encoding="utf-8")
    table_kind = regime.table_files.get(rel)
    folder = None if table_kind else rel.rpartition("/")[0]
    f = EntryFile(rel=rel, folder=folder, raw=raw, comments=scan_comments(raw))
    try:
        data: Any = load_yaml(raw)
    except Exception as e:
        f.error = f"not valid YAML: {str(e).splitlines()[0]}"
        return f
    if data is None:
        data = {}
    if not isinstance(data, dict):
        f.error = "must be a mapping of kinds to lists of entries"
        return f
    allowed = [table_kind] if table_kind else regime.folder_kinds
    for key, items in data.items():  # pyright: ignore[reportUnknownVariableType]
        if key not in allowed:
            f.error = f"top-level key {key!r} isn't one of {', '.join(allowed)}"
            return f
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):  # pyright: ignore[reportUnknownVariableType]
            f.error = f"{key} must be a list of entries"
            return f
        f.sections[str(key)] = [dict(x) for x in items]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return f


__all__ = ["SOFT_DELETE", "EntryFile", "Located", "Problem", "Store"]
