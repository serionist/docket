"""The processed docs in a vault: putting one, reading one at any revision, and finding the docs that cover a record.

A rev is the commit that wrote it: a line `Doc: <id> rev <n>` in the commit's message, in its subject or its body (an
import commits many docs at once, one line each). The `rev:` line in a doc is a copy for the reader, so a hand commit
that keeps it never makes a second rev with the same number, and a doc deleted by hand goes on from its last rev
(DR-0112).

A put checks what the raw log decides without the lock (raw records are written once), then, inside
`Vault.writing()`, what the stored docs decide: a hand edit is taken in as the next rev first, then the rev the put
carries, `supersedes`, and whether anything changed. A failed write or commit leaves the file and git's index as they
were.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from docket.errors import DocketError
from docket.vault.anchors import normalize_newlines, trailing_anchors
from docket.vault.git import DOC_LINE
from docket.vault.processed import (
    DocMeta,
    DocProblem,
    DocRefusedError,
    ProcessedDoc,
    doc_path,
    id_problem,
    key_line,
    parse_doc,
)
from docket.vault.records import RawRecord
from docket.vault.spans import RawLookup, problem
from docket.vault.vault import Vault

log = logging.getLogger(__name__)

# On Windows another program holding the doc open makes the rename fail for a moment.
REPLACE_TRIES = 5
REPLACE_WAIT_SECONDS = 0.2
_MIDLINE = re.compile(r"(?:^|[ \t])(?:@\d|\[r:)")
_POSITION_TAIL = re.compile(r"[@#][\d:-]+$")


class PutResult(BaseModel):
    """What a put did: the doc's rev now, its path, whether anything was written, the commit, and the rev a hand
    edit was taken in as first, if there was one."""

    id: str
    rev: int
    path: str
    changed: bool
    commit: str | None = None
    taken_in: int | None = None


class DocStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.root = vault.root

    # -- reading ---------------------------------------------------------------------------------------------------

    @staticmethod
    def path(doc_id: str) -> PurePosixPath:
        """A doc's path inside the vault. An id that isn't one is refused before any path is built from it: a slug
        like `../../elsewhere` would otherwise reach files outside processed/ and outside the vault."""
        why = id_problem(doc_id)
        if why is not None:
            raise DocketError(f"{doc_id!r} isn't a doc id: {why}")
        return doc_path(doc_id)

    def file(self, doc_id: str) -> Path:
        return self.root / self.path(doc_id)

    def read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise DocketError(f"{path.relative_to(self.root).as_posix()} isn't UTF-8") from None

    def get(self, doc_id: str) -> ProcessedDoc | None:
        """The doc as it is on disk, read leniently; None when there is no file."""
        path = self.file(doc_id)
        return parse_doc(self.read_file(path), lenient=True) if path.is_file() else None

    def history(self, doc_id: str) -> list[tuple[int, str]]:
        """The doc's revs, newest commit first: (rev, commit) for every commit whose message has its `Doc:` line."""
        # Signature lines a user's log.showSignature adds would read as commit ids.
        rel = self.path(doc_id).as_posix()
        out = self.vault.git.out("log", "--no-show-signature", "--format=%H%x1f%B%x1e", "--", rel)
        revs: list[tuple[int, str]] = []
        for entry in out.split("\x1e"):
            sha, _, message = entry.strip("\n").partition("\x1f")
            for line in message.splitlines():
                m = DOC_LINE.match(line.strip())
                if m and m.group(1) == doc_id:
                    revs.append((int(m.group(2)), sha))
                    break
        return revs

    def exists(self, doc_id: str) -> bool:
        return self.file(doc_id).is_file() or bool(self.history(doc_id))

    def _blob(self, sha: str, doc_id: str) -> str:
        # Not `git show`: it also tries its argument as a file path, which fails past 260 characters on Windows.
        return self.vault.git.out("cat-file", "blob", f"{sha}:{self.path(doc_id).as_posix()}")

    def at_rev(self, doc_id: str, rev: int) -> ProcessedDoc:
        """The doc as the commit that wrote that rev left it."""
        history = self.history(doc_id)
        for n, sha in history:
            if n == rev:
                return parse_doc(self._blob(sha, doc_id), lenient=True)
        known = ", ".join(str(n) for n, _ in sorted(history)) or "none"
        raise DocketError(f"{doc_id} has no rev {rev}; its revs: {known}")

    def all(self) -> list[ProcessedDoc]:
        """Every doc under processed/ whose file is where its id says. One that doesn't parse is skipped and logged."""
        folder = self.root / "processed"
        docs: list[ProcessedDoc] = []
        for path in sorted(folder.rglob("*.md")) if folder.is_dir() else []:
            rel = path.relative_to(self.root).as_posix()
            try:
                doc = parse_doc(self.read_file(path), lenient=True)
            except DocketError as e:
                log.warning("skipping %s: %s", rel, e)
                continue
            if doc_path(doc.meta.id).as_posix() != rel:
                log.warning("skipping %s: its id %s belongs at %s", rel, doc.meta.id, doc_path(doc.meta.id))
                continue
            docs.append(doc)
        return docs

    def _safe_get(self, doc_id: str) -> ProcessedDoc | None:
        try:
            return self.get(doc_id)
        except DocketError:
            return None

    # -- putting -----------------------------------------------------------------------------------------------------

    def put(self, text: str) -> PutResult:
        """Store a doc: refused with every problem when its frontmatter is wrong, an anchor doesn't resolve, or it was
        changed since the rev it carries; a no-op when nothing changed; else written as the next rev and committed."""
        head = normalize_newlines(text.removeprefix("\ufeff")).split("\n")
        doc = parse_doc(text)
        if not _reads_back(doc):
            message = "the frontmatter can't be written so that it reads back the same; a value holds what YAML can't"
            raise DocRefusedError([DocProblem(None, message)])
        problems = self._against_raw(doc, head)
        if problems:
            raise DocRefusedError(problems)
        with self.vault.writing():
            return self._put_locked(doc, head)

    def _against_raw(self, doc: ProcessedDoc, head: list[str]) -> list[DocProblem]:
        """What the raw log decides: every covered rid is in it, and every content line's anchors resolve."""
        meta = doc.meta
        lookup = RawLookup(self.root)
        problems: list[DocProblem] = []
        records: dict[str, RawRecord] = {}
        for rid in meta.covers:
            record = lookup.latest(rid)
            if record is None:
                line = next((i for i in range(1, doc.head_lines) if rid in head[i]), None)
                problems.append(DocProblem(None if line is None else line + 1, f"covers: {rid} isn't in the raw log"))
            else:
                records[rid] = record
        if meta.type == "legacy-note":
            return problems
        for n, line, role in doc.roles():
            if role != "content":
                continue
            trailing = trailing_anchors(line)
            problems.extend(DocProblem(n, error, line) for error in trailing.errors)
            if not trailing.anchors and not trailing.errors:
                hint = " (anchors go at the very end, after the text)" if _MIDLINE.search(line) else ""
                problems.append(DocProblem(n, f"no anchors at the end of the line: @mm:ss or [r:<rid>]{hint}", line))
            for anchor in trailing.anchors:
                rid = anchor.rid
                if rid is None:
                    if len(meta.covers) != 1:
                        message = f"{anchor.token} needs a doc covering one record; write [r:<rid>{anchor.token}]"
                        problems.append(DocProblem(n, message, line))
                        continue
                    rid = meta.covers[0]
                elif rid not in meta.covers:
                    tail = _POSITION_TAIL.search(rid)
                    hint = f"; if {tail.group()} is meant as a position, it isn't one" if tail else ""
                    problems.append(DocProblem(n, f"{rid} isn't in covers; add it there{hint}", line))
                    continue
                record = records.get(rid)
                why = problem(anchor, record) if record is not None else None
                if why is not None:
                    problems.append(DocProblem(n, f"{anchor.token}: {why}", line))
        return problems

    def _put_locked(self, doc: ProcessedDoc, head: list[str]) -> PutResult:
        meta = doc.meta
        rel = doc_path(meta.id).as_posix()
        path = self.root / rel
        history = self.history(meta.id)
        stored = max((n for n, _ in history), default=0)
        taken_in: int | None = None
        on_disk = self.read_file(path) if path.is_file() else None
        if on_disk is not None:
            written = normalize_newlines(self._blob(history[0][1], meta.id)) if history else None
            if normalize_newlines(on_disk) != written:
                taken_in, on_disk = self._take_in(meta.id, path, on_disk, stored)
                stored = taken_in
        if on_disk is not None and parse_doc(on_disk, lenient=True).render(rev=0) == doc.render(rev=0):
            return PutResult(id=meta.id, rev=stored, path=rel, changed=False, taken_in=taken_in)
        problems: list[DocProblem] = []
        rev_line = key_line(head[1 : doc.head_lines - 1], "rev")
        if not stored and meta.rev:
            problems.append(DocProblem(rev_line, f"rev: there is no {meta.id} yet; leave rev out for a new doc"))
        elif stored and meta.rev != stored:
            after = f", after a hand edit taken in as rev {taken_in}" if taken_in else ""
            message = f"rev: {meta.id} is at rev {stored} now{after}; read it again and put your change on that"
            problems.append(DocProblem(rev_line, message))
        problems.extend(self._supersedes_problems(meta, head, doc.head_lines))
        if problems:
            raise DocRefusedError(problems)
        rev = stored + 1
        commit = self._write_and_commit(path, rel, doc.render(rev=rev), f"Doc: {meta.id} rev {rev}")
        return PutResult(id=meta.id, rev=rev, path=rel, changed=True, commit=commit, taken_in=taken_in)

    def _supersedes_problems(self, meta: DocMeta, head: list[str], head_lines: int) -> list[DocProblem]:
        line = key_line(head[1 : head_lines - 1], "supersedes")
        problems: list[DocProblem] = []
        for other in meta.supersedes:
            if not self.exists(other):
                problems.append(DocProblem(line, f"supersedes: {other} doesn't exist"))
            elif self._leads_to(other, meta.id):
                problems.append(DocProblem(line, f"supersedes: {other} already leads back to {meta.id}, a cycle"))
        return problems

    def _leads_to(self, start: str, target: str) -> bool:
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            found = self._safe_get(current)
            stack.extend(found.meta.supersedes if found is not None else [])
        return False

    def _take_in(self, doc_id: str, path: Path, on_disk: str, stored: int) -> tuple[int, str]:
        """Commit a hand edit as the next rev, changing only its rev line, so a put made before it can't overwrite it.
        The rev is one past both the stored rev and the file's own, so the rev line always changes and the commit is
        never empty, even when the user raised it by hand. If the commit fails, the user's bytes go back as they were
        and nothing stays staged."""
        rel = path.relative_to(self.root).as_posix()
        try:
            edited = parse_doc(on_disk, lenient=True)
        except DocRefusedError:
            message = f"{rel} was edited by hand and its frontmatter no longer parses; fix it or check it out again"
            raise DocRefusedError([DocProblem(None, message)]) from None
        rev = max(stored, edited.meta.rev) + 1
        original = path.read_bytes()
        text = _with_rev(on_disk, rev)
        self._replace(path, text.encode("utf-8"))
        try:
            self.vault.commit([rel], f"Doc: {doc_id} rev {rev} (edited by hand)")
        except BaseException:
            self._unstage(rel)
            self._replace(path, original)
            raise
        return rev, text

    def _write_and_commit(self, path: Path, rel: str, text: str, message: str) -> str | None:
        original = path.read_bytes() if path.is_file() else None
        self._replace(path, text.encode("utf-8"))
        try:
            return self.vault.commit([rel], message)
        except BaseException:
            self._unstage(rel)
            if original is None:
                path.unlink(missing_ok=True)
            else:
                self._replace(path, original)
            raise

    def _unstage(self, rel: str) -> None:
        self.vault.git.run("reset", "-q", "--", rel, check=False)

    def _replace(self, path: Path, data: bytes) -> None:
        """Write a file whole through a temp file and a rename; the temp file lives in .git, never in processed/."""
        tmp_dir = self.root / ".git" / "docket" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{path.name}.{os.getpid()}.tmp"
        try:
            with tmp.open("wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            for attempt in range(1, REPLACE_TRIES + 1):
                try:
                    tmp.replace(path)
                    return
                except PermissionError as e:
                    if attempt == REPLACE_TRIES:
                        rel = path.relative_to(self.root).as_posix()
                        message = f"couldn't write {rel}: another program has it open; close it and try again"
                        raise DocketError(message) from e
                    time.sleep(REPLACE_WAIT_SECONDS)
        finally:
            tmp.unlink(missing_ok=True)


def _reads_back(doc: ProcessedDoc) -> bool:
    """Whether the frontmatter Docket would write reads back as the same, so a stored doc can always be read again."""
    try:
        back = parse_doc(doc.render(rev=0)).meta
    except DocRefusedError:
        return False
    return back == doc.meta.model_copy(update={"rev": 0})


def _with_rev(text: str, rev: int) -> str:
    """A doc's text with its rev line set, and nothing else changed."""
    lines = normalize_newlines(text).split("\n")
    close = lines.index("---", 1)
    for i in range(1, close):
        if lines[i].startswith("rev:"):
            lines[i] = f"rev: {rev}"
            return "\n".join(lines)
    after = next((i for i in range(1, close) if lines[i].startswith("type:")), 0)
    lines.insert(after + 1, f"rev: {rev}")
    return "\n".join(lines)
