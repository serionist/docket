"""The processed docs in a vault: putting one, reading one at any revision, and finding the docs that cover a record.

A rev is the commit that wrote it: a line `Doc: <id> rev <n>` in the commit's message, in its subject or its body (an
import commits many docs at once, one line each). The `rev:` line in a doc is a copy for the reader, so a hand commit
that keeps it never makes a second rev with the same number, and a doc deleted by hand goes on from its last rev
(DR-0112).

A put checks what the raw log decides without the lock (raw records are written once), through the lookup its caller
opens for that check alone, and `put_many` every doc of its batch through one: at the records' places in a current
index (DR-0522), closed before the lock is taken (DR-0516). Then, inside `Vault.writing()`, what the stored docs
decide: a hand edit is taken in as the next rev first, then the rev the put carries, `supersedes`, and whether anything
changed. A failed write or commit leaves the file and git's index as they were.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from docket import console
from docket.documents import records as documents
from docket.errors import DocketError
from docket.vault import brief_form, decision_form, derived
from docket.vault.anchors import format_ref, normalize_newlines, trailing_anchors
from docket.vault.entities import load_entities
from docket.vault.git import DOC_LINE
from docket.vault.home import make_private
from docket.vault.processed import (
    DocMeta,
    DocProblem,
    DocRefusedError,
    ProcessedDoc,
    doc_path,
    id_problem,
    key_line,
    line_roles,
    parse_doc,
)
from docket.vault.records import RawRecord
from docket.vault.redact import Redactor
from docket.vault.spans import RawLookup, problem
from docket.vault.vault import UnmergedError, Vault

log = logging.getLogger(__name__)
# A final decision's committed text, by its blob id: what every read of it takes (DR-0603), read once a process.
_BLOBS: dict[str, str] = {}
BLOBS_KEPT = 1000

# On Windows another program holding the doc open makes the rename fail for a moment.
REPLACE_TRIES = 5
REPLACE_WAIT_SECONDS = 0.2
# Below this many docs, one log each is no more calls than a rev-list and one log over their folders.
BATCHED_HISTORIES = 3
_MIDLINE = re.compile(r"(?:^|[ \t])(?:@\d|\[r:)")
_POSITION_TAIL = re.compile(r"[@#][\d:-]+$")

# What a put reads the raw log through for its anchor checks: a lookup open for the check alone (DR-0516).
RawOpener = Callable[[], AbstractContextManager[RawLookup]]


class PutResult(BaseModel):
    """What a put did: the doc's rev now, its path, whether anything was written, the commit, the rev a hand edit was
    taken in as first, if there was one, and the markers redaction added to the doc as given, per rule and by line,
    numbered as `read` numbers the stored doc (DR-0199). No redacted value is ever in it."""

    id: str
    rev: int
    path: str
    changed: bool
    commit: str | None = None
    taken_in: int | None = None
    redactions: dict[str, int] = Field(default_factory=dict[str, int])
    redacted_lines: list[int] = Field(default_factory=list[int])
    # What the put met that refused nothing: a covered folder record that went, read from before it did (DR-0491).
    notes: list[str] = Field(default_factory=list[str])
    # The lines whose doc-line refs Docket pinned to the text they cite, numbered as `read` numbers them (DR-0540).
    pinned_lines: list[int] = Field(default_factory=list[int])


class Redacting(BaseModel):
    """A doc the pass redacted (or would, in a dry run): its id, the rev it went in as, the markers per rule, and
    whether it went in through the take-in of a hand edit."""

    id: str
    rev: int
    redactions: dict[str, int]
    by_hand: bool = False


class RedactReport(BaseModel):
    """What `docket doc redact` did: the docs it redacted, the files it left with why (paths and line numbers, never
    a value), and the commit."""

    dry_run: bool
    docs: list[Redacting] = Field(default_factory=list[Redacting])
    left: list[str] = Field(default_factory=list[str])
    commit: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for doc in self.docs:
            for rule, n in doc.redactions.items():
                total[rule] = total.get(rule, 0) + n
        return dict(sorted(total.items()))


class DocStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self.root = vault.root
        # The rules vault.yaml has as the vault was opened: every text this store commits passes them (DR-0195).
        self.redactor = Redactor.from_config(vault.config.redaction)
        self.lists = vault.config.never_store
        # A batch's revs, read in one log while put_many holds the lock (DR-0529); None outside one.
        self._known: dict[str, list[tuple[int, str]]] | None = None

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
        """The doc as it is on disk, read leniently; None when there is no file. A decision as every read takes it: a
        final one whole from its last commit, a proposed one with its status and links from it (DR-0603)."""
        path = self.file(doc_id)
        if not path.is_file():
            return None
        doc = parse_doc(self.read_file(path), lenient=True)
        if doc.meta.type != "decision":
            return doc
        return decision_form.as_committed(doc, decision_form.committed_doc(self.committed_text(doc_id)))

    def committed_text(self, doc_id: str) -> str | None:
        """The doc's text as HEAD holds it, or None when HEAD doesn't."""
        rel = self.path(doc_id).as_posix()
        return self.vault.git.blob_text(self.vault.git.at_head(rel))

    def committed_texts(self, rels: list[str]) -> dict[str, str | None]:
        """Several docs' texts as HEAD holds them, by their paths in the vault: one listing of HEAD's tree for their
        folders, then each blob read once and kept by its id, so a final decision is read from git once a process."""
        folders = sorted({rel.rpartition("/")[0] or rel for rel in rels})
        proc = self.vault.git.run("ls-tree", "-r", "-z", "HEAD", "--", *folders, check=False)
        blobs: dict[str, str] = {}
        if proc.returncode == 0:
            for entry in proc.stdout.split("\0"):
                info, _, name = entry.partition("\t")
                fields = info.split()
                if len(fields) == 3 and fields[1] == "blob":
                    blobs[name] = fields[2]
        out: dict[str, str | None] = {}
        for rel in rels:
            sha = blobs.get(rel)
            if sha is None:
                out[rel] = None
                continue
            if sha not in _BLOBS:
                if len(_BLOBS) >= BLOBS_KEPT:
                    _BLOBS.clear()
                _BLOBS[sha] = self.vault.git.blob_out(sha)
            out[rel] = _BLOBS[sha]
        return out

    def decide(
        self, doc_id: str, shown: int, pick: str, subject: str | None = None, *, via: Literal["page", "cli"] = "page"
    ) -> PutResult:
        """The user's click on the Briefs page, or `docket decision set` on the owner's yes (DR-0603, DR-0566), and the
        only writer of a decision's status: the candidate's next rev, confirmed or rejected as `pick` says, written and
        committed under the vault's lock, its `by` and its commit saying who and where.
        Refused when it isn't a proposed decision, its file says otherwise than its last commit, or a never-store list
        holds its subject; `MovedError` when its rev isn't the one the page showed, or a decision it replaces or
        contradicts isn't confirmed and current any more (D3)."""
        moved = decision_form.MovedError
        if id_problem(doc_id) is not None or not doc_id.startswith("decision:"):
            raise DocketError(f"decision is a decision's id, such as decision:2026-10-13-first, not {doc_id!r}")
        with self.vault.writing():
            text = self.committed_text(doc_id)
            doc = decision_form.committed_doc(text)
            if doc is None or doc.meta.type != "decision":
                raise DocketError(f"no decision {doc_id}")
            if subject is not None and doc.meta.subject != subject:
                raise DocketError(f"{doc_id} isn't a decision on {subject}, the page it was clicked on")
            why = decision_form.pick_problem(doc, pick)
            if why is not None:
                raise DocketError(why)
            if doc.meta.status != "proposed":
                raise moved(f"{doc_id} is {doc.meta.status} already")
            if doc.meta.rev != shown:
                raise moved("the decision changed since the page showed it; here it is as it stands")
            path = self.file(doc_id)
            rel = self.path(doc_id).as_posix()
            if not path.is_file() or decision_form.edited(self.read_file(path), text):
                raise DocketError(f"{rel} was edited by hand; put it with the decision skill, or check it out again")
            entity = next((e for e in load_entities(self.root).entities if e.id == doc.meta.subject), None)
            if entity is None or brief_form.held(entity, self.lists):
                raise DocketError("the decision's subject is gone, or a never-store list holds it")
            standing = decision_form.standings(self.decisions())
            for key, targets in (("replaces", doc.meta.replaces), ("contradicts", doc.meta.contradicts)):
                if any(t not in standing or not standing[t].current for t in targets):
                    raise moved(f"the decision it {key} was replaced meanwhile; the candidate needs a new rev")
            owner = self.vault.config.owner.name
            if via == "page":
                by, where = f"{owner}, Briefs page", "on the Briefs page"
            else:
                by = f"Claude, with {owner}'s OK" if console.who() else f"{owner}, command line"
                where = "on the command line"
            new = decision_form.status_rev(doc, pick, by)
            rev = doc.meta.rev + 1
            status = "confirmed" if pick in decision_form.CONFIRMING else "rejected"
            message = f"Decision: {doc_id} {status} {where}\n\nDoc: {doc_id} rev {rev}"
            message = console.signed(message) if via == "cli" else message
            commit = self._write_all_and_commit([(rel, new)], message)
        (self._known or {}).pop(doc_id, None)
        return PutResult(id=doc_id, rev=rev, path=rel, changed=True, commit=commit)

    def decisions(self) -> list[ProcessedDoc]:
        """Every decision, as every read takes it."""
        folder = self.root / "processed" / "decision"
        return self.at_paths(sorted(folder.rglob("*.md")) if folder.is_dir() else [])

    def history(self, doc_id: str) -> list[tuple[int, str]]:
        """The doc's revs, newest commit first: (rev, commit) for every commit whose message has its `Doc:` line."""
        if self._known is not None and doc_id in self._known:
            return self._known[doc_id]
        # Signature lines a user's log.showSignature adds would read as commit ids.
        rel = self.path(doc_id).as_posix()
        out = self.vault.git.out("log", "--no-show-signature", "--format=%H%x1f%B%x1e", "--", rel)
        revs: list[tuple[int, str]] = []
        for entry in out.split("\x1e"):
            sha, _, message = entry.strip("\n").partition("\x1f")
            _add_rev(revs, doc_id, sha, message)
        return revs

    def histories(self, doc_ids: Sequence[str]) -> dict[str, list[tuple[int, str]]]:
        """Each doc's revs as `history` reads them, from one log over the docs' type folders (DR-0529): a commit counts
        for a doc when it touched the doc's file and its message has the doc's `Doc:` line. Across a merge a folder log
        isn't the union of the per-file logs, so when one touched those folders, or the batch is too small to gain,
        each doc's log is read on its own; one `rev-list --merges` decides."""
        ids = list(dict.fromkeys(doc_ids))
        if len(ids) < BATCHED_HISTORIES:
            return {doc_id: self.history(doc_id) for doc_id in ids}
        rels = {self.path(doc_id).as_posix(): doc_id for doc_id in ids}
        folders = sorted({f"processed/{doc_id.partition(':')[0]}" for doc_id in ids})
        git = self.vault.git
        merges = git.run("rev-list", "--merges", "-1", "HEAD", "--", *folders, check=False)
        if merges.returncode != 0 or merges.stdout.strip():
            return {doc_id: self.history(doc_id) for doc_id in ids}
        # NUL between names, so no name is quoted; names from the repo's top, so the prefix comes off.
        out = git.out(
            "log", "-z", "--no-renames", "--no-show-signature", "--format=%x1e%H%x1f%B", "--name-only", "--", *folders
        )
        revs: dict[str, list[tuple[int, str]]] = {doc_id: [] for doc_id in ids}
        sha, message = "", ""
        for field in out.split("\0"):
            if field.startswith("\x1e"):
                sha, _, message = field[1:].partition("\x1f")
                continue
            doc_id = rels.get(field.removeprefix("\n").removeprefix(git.prefix))
            if doc_id is not None and sha:
                _add_rev(revs[doc_id], doc_id, sha, message)
        return revs

    def exists(self, doc_id: str) -> bool:
        return self.file(doc_id).is_file() or bool(self.history(doc_id))

    def _blob(self, sha: str, doc_id: str) -> str:
        # Not `git show`: it also tries its argument as a file path, which fails past 260 characters on Windows. The
        # ./ makes git read the path from the vault's folder, not the top of a host repo the vault is a folder of.
        return self.vault.git.blob_out(f"{sha}:./{self.path(doc_id).as_posix()}")

    def earlier(self, doc_id: str, before: int) -> Iterator[tuple[int, ProcessedDoc]]:
        """The doc's revs before `before`, newest first, each as its commit left it, read one at a time."""
        for n, sha in self.history(doc_id):
            if n < before:
                yield n, parse_doc(self._blob(sha, doc_id), lenient=True)

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
        return self.at_paths(sorted(folder.rglob("*.md")) if folder.is_dir() else [])

    def at_paths(self, paths: list[Path]) -> list[ProcessedDoc]:
        """The docs in these files, in this order, read as `all` reads them: one that is gone, doesn't parse or isn't
        where its id says is skipped and logged."""
        docs: list[ProcessedDoc] = []
        for path in paths:
            rel = path.relative_to(self.root).as_posix()
            try:
                doc = parse_doc(self.read_file(path), lenient=True)
            except DocketError as e:
                log.warning("skipping %s: %s", rel, e)
                continue
            except OSError as e:
                log.warning("skipping %s: %s", rel, e.strerror or type(e).__name__)
                continue
            if doc_path(doc.meta.id).as_posix() != rel:
                log.warning("skipping %s: its id %s belongs at %s", rel, doc.meta.id, doc_path(doc.meta.id))
                continue
            docs.append(doc)
        rels = [doc_path(d.meta.id).as_posix() for d in docs if d.meta.type == "decision"]
        if not rels:
            return docs
        texts = self.committed_texts(rels)
        return [
            decision_form.as_committed(d, decision_form.committed_doc(texts.get(doc_path(d.meta.id).as_posix())))
            for d in docs
        ]

    def _safe_get(self, doc_id: str) -> ProcessedDoc | None:
        try:
            return self.get(doc_id)
        except DocketError:
            return None

    # -- putting -----------------------------------------------------------------------------------------------------

    def put(self, text: str, raw: RawOpener | None = None) -> PutResult:
        """Store a doc: refused with every problem when its frontmatter is wrong, an anchor or a doc-line ref doesn't
        resolve, or it was changed since the rev it carries; a no-op when nothing changed; else written as the next rev
        and committed, its refs pinned (DR-0540). `raw` opens what the anchors are checked through; without it the
        covered sources load whole."""
        head = normalize_newlines(text.removeprefix("\ufeff")).split("\n")
        doc = parse_doc(text)
        if not _reads_back(doc):
            message = "the frontmatter can't be written so that it reads back the same; a value holds what YAML can't"
            raise DocRefusedError([DocProblem(None, message)])
        doc, redacted = derived.check(doc, head[1 : doc.head_lines - 1], self.redactor, self.lists)
        doc, pinned, problems = self._pin_doc(doc)
        notes: list[str] = []
        problems += self._kind_problems(doc, notes)
        # Closed before the lock: the write never holds the index or waits behind its update (DR-0516).
        with raw() if raw is not None else nullcontext(RawLookup(self.root)) as lookup:
            problems += self._against_raw(doc, head, lookup, notes)
        if problems:
            raise DocRefusedError(problems)
        with self.vault.writing():
            result = self._put_locked(doc, head, redacted)
        return result.model_copy(update={"notes": notes, "pinned_lines": _pinned_lines(result, doc, pinned)})

    def _against_raw(
        self, doc: ProcessedDoc, head: list[str], lookup: RawLookup, notes: list[str] | None = None
    ) -> list[DocProblem]:
        """What the raw log decides: every covered rid is in it, and every content line's anchors resolve, all through
        one lookup, so a source loads once at most. Into `notes`, each covered folder record read from an earlier
        version than its newest (DR-0491)."""
        meta = doc.meta
        problems: list[DocProblem] = []
        records: dict[str, RawRecord] = {}
        for rid in meta.covers:
            versions = lookup.versions(rid)
            # A folder record that went resolves against its last version with text (DR-0491).
            record = documents.resolving(versions) if versions else None
            if record is None:
                line = next((i for i in range(1, doc.head_lines) if rid in head[i]), None)
                problems.append(DocProblem(None if line is None else line + 1, f"covers: {rid} isn't in the raw log"))
            else:
                records[rid] = record
                if notes is not None and record is not versions[-1]:
                    notes.append(f"{rid}: {documents.gone_note(versions[-1])}")
        if meta.type == "legacy-note":
            return problems
        for n, line, role in doc.roles():
            if role != "content":
                continue
            trailing = trailing_anchors(line)
            problems.extend(DocProblem(n, error, line) for error in trailing.errors)
            if meta.type == "correction" and not trailing.errors:
                if not trailing.anchors:
                    problems.append(
                        DocProblem(n, "a correction's line ends with the raw that shows it: [r:<rid>]", line)
                    )
                if not trailing.refs:
                    message = "a correction's line cites the line it corrects: [d:<doc id>@<rev>#<n>], verify's cite"
                    problems.append(DocProblem(n, message, line))
            if not trailing.anchors and not trailing.refs and not trailing.errors:
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
                why = problem(anchor, record, lookup) if record is not None else None
                if why is not None:
                    problems.append(DocProblem(n, f"{anchor.token}: {why}", line))
        return problems

    def _put_locked(self, doc: ProcessedDoc, head: list[str], redacted: derived.Redacted) -> PutResult:
        result, text = self._check_locked(doc, head, redacted)
        if text is None:
            return result
        commit = self._write_all_and_commit([(result.path, text)], f"Doc: {result.id} rev {result.rev}")
        return result.model_copy(update={"commit": commit})

    def put_many(
        self, texts: list[str], subject: str, raw: RawOpener | None = None
    ) -> list[PutResult | DocRefusedError]:
        """Store several docs in one commit: each is checked as `put` checks one, and refused on its own; the accepted
        ones are written and committed together, the commit's body holding a `Doc: <id> rev <n>` line for each. If a
        write or the commit fails, every file goes back as it was and nothing stays staged. Every doc's anchors are
        checked through the one lookup `raw` opens, so a source is read at its places or loaded once for the whole
        batch (DR-0522); without `raw`, each covered source loads once."""
        out: list[PutResult | DocRefusedError | None] = [None] * len(texts)
        ready: list[tuple[int, ProcessedDoc, list[str], derived.Redacted]] = []
        # Closed before the lock, as `put`'s is (DR-0516).
        with raw() if raw is not None else nullcontext(RawLookup(self.root)) as lookup:
            for i, text in enumerate(texts):
                head = normalize_newlines(text.removeprefix("﻿")).split("\n")
                try:
                    doc = parse_doc(text)
                    if not _reads_back(doc):
                        raise DocRefusedError([DocProblem(None, "the frontmatter can't be written back")])
                    doc, redacted = derived.check(doc, head[1 : doc.head_lines - 1], self.redactor, self.lists)
                    doc, _, problems = self._pin_doc(doc)
                    problems += self._kind_problems(doc, [])
                    problems += self._against_raw(doc, head, lookup)
                except DocRefusedError as e:
                    out[i] = e
                    continue
                if problems:
                    out[i] = DocRefusedError(problems)
                else:
                    ready.append((i, doc, head, redacted))
        writes: list[tuple[int, PutResult, str]] = []
        with self.vault.writing():
            seen: set[str] = set()
            # Every doc's revs in one log, under the lock (DR-0529); a take-in drops its own from them.
            self._known = self.histories([doc.meta.id for _, doc, _, _ in ready])
            try:
                for i, doc, head, redacted in ready:
                    if doc.meta.id in seen:
                        out[i] = DocRefusedError([DocProblem(None, f"{doc.meta.id} is in this batch twice")])
                        continue
                    seen.add(doc.meta.id)
                    try:
                        result, text = self._check_locked(doc, head, redacted)
                    except DocRefusedError as e:
                        out[i] = e
                        continue
                    if text is None:
                        out[i] = result
                    else:
                        writes.append((i, result, text))
            finally:
                self._known = None
            if writes:
                lines = "\n".join(f"Doc: {result.id} rev {result.rev}" for _, result, _ in writes)
                commit = self._write_all_and_commit([(r.path, t) for _, r, t in writes], f"{subject}\n\n{lines}")
                for i, result, _ in writes:
                    out[i] = result.model_copy(update={"commit": commit})
        return [r for r in out if r is not None]

    def latest_revs(self, doc_type: str) -> dict[str, int]:
        """The last rev of every doc of a type that git history knows, files deleted since included, from one log."""
        out = self.vault.git.out("log", "--no-show-signature", "--format=%B", "--", f"processed/{doc_type}")
        revs: dict[str, int] = {}
        for line in out.splitlines():
            m = DOC_LINE.match(line.strip())
            if m and m.group(1).startswith(f"{doc_type}:"):
                revs[m.group(1)] = max(revs.get(m.group(1), 0), int(m.group(2)))
        return revs

    def _check_locked(
        self, doc: ProcessedDoc, head: list[str], redacted: derived.Redacted
    ) -> tuple[PutResult, str | None]:
        """Under the lock: take in a hand edit, then check the rev and supersedes. The result, and the text to write at
        the next rev, or None when nothing changed. `doc` is redacted already, and `redacted` says what changed."""
        meta = doc.meta
        rel = doc_path(meta.id).as_posix()
        path = self.root / rel
        history = self.history(meta.id)
        stored = max((n for n, _ in history), default=0)
        taken_in: int | None = None
        counts, lines = _told(redacted, doc.render(rev=0))
        on_disk = self.read_file(path) if path.is_file() else None
        if on_disk is not None:
            written = normalize_newlines(self._blob(history[0][1], meta.id)) if history else None
            if normalize_newlines(on_disk) != written:
                taken_in, on_disk = self._take_in(meta.id, path, on_disk, stored, written)
                stored = taken_in
        if on_disk is not None and parse_doc(on_disk, lenient=True).render(rev=0) == doc.render(rev=0):
            return PutResult(
                id=meta.id,
                rev=stored,
                path=rel,
                changed=False,
                taken_in=taken_in,
                redactions=counts,
                redacted_lines=lines,
            ), None
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
        result = PutResult(
            id=meta.id, rev=rev, path=rel, changed=True, taken_in=taken_in, redactions=counts, redacted_lines=lines
        )
        return result, doc.render(rev=rev)

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

    def _take_in(self, doc_id: str, path: Path, on_disk: str, stored: int, last: str | None) -> tuple[int, str]:
        """Commit a hand edit as the next rev, so a put made before it can't overwrite it: its body redacted, its
        frontmatter as written with only the rev line set. Refused when it says a never-store keyword, or its
        frontmatter holds what redaction would hide, since no marker goes into YAML (DR-0195); the edit stays on disk.
        The rev is one past both the stored rev and the file's own, so the rev line always changes and the commit is
        never empty, even when the user raised it by hand. If the commit fails, the user's bytes go back as they were
        and nothing stays staged."""
        rel = path.relative_to(self.root).as_posix()
        try:
            edited = parse_doc(on_disk, lenient=True)
        except DocRefusedError:
            message = f"{rel} was edited by hand and its frontmatter no longer parses; fix it or check it out again"
            raise DocRefusedError([DocProblem(None, message)]) from None
        edit = self._hand_edit(edited, on_disk, last)
        if edit.stops:
            raise DocRefusedError(
                [
                    DocProblem(
                        None, f"{rel} was edited by hand, and its line {n} {what}; edit it or check it out again"
                    )
                    for n, what in edit.stops
                ]
            )
        # Its refs pinned to the text they cite, a bare one too: a hand edit cites a line as it reads now (DR-0540).
        lines = edit.text.split("\n")
        close = lines.index("---", 1)
        body, _, problems = self._pin(doc_id, edited.meta.type, lines[close + 1 :], close + 2, bare=True)
        if problems:
            raise DocRefusedError(
                [DocProblem(None, f"{rel} was edited by hand, and its line {p.line}: {p.message}") for p in problems]
            )
        rev = max(stored, edited.meta.rev) + 1
        original = path.read_bytes()
        text = _with_rev("\n".join([*lines[: close + 1], *body]), rev)
        self._replace(path, text.encode("utf-8"))
        try:
            self.vault.commit([rel], f"Doc: {doc_id} rev {rev} (edited by hand)")
        except BaseException as e:
            if not isinstance(e, UnmergedError):
                self._unstage(rel)
            self._replace(path, original)
            raise
        if self._known is not None:
            self._known.pop(doc_id, None)
        return rev, text

    def take_in(self, doc_id: str) -> int | None:
        """A hand edit of the doc taken in as its next rev, as a put takes it in first: the rev, or None when its file
        is as its last rev wrote it. Refused as a put's take-in refuses it, the edit left on disk. A forget rewrites a
        doc only once its edit is in (DR-0580)."""
        path = self.file(doc_id)
        history = self.history(doc_id)
        written = normalize_newlines(self._blob(history[0][1], doc_id)) if history else None
        on_disk = self.read_file(path)
        if normalize_newlines(on_disk) == written:
            return None
        rev, _ = self._take_in(doc_id, path, on_disk, max((n for n, _ in history), default=0), written)
        return rev

    def _kind_problems(self, doc: ProcessedDoc, notes: list[str]) -> list[DocProblem]:
        """What a brief's or a decision's own form refuses, on top of every doc's rules: a brief's sections, its
        Decisions lines and its never-store screen (DR-0555, DR-0558, DR-0565), with a section the form doesn't name
        added to `notes`; a decision's form, its status and links, and its never-store screen (DR-0563)."""
        if doc.meta.type == "brief":
            found, warned = brief_form.check(doc, self.root, self.lists)
            notes += warned
            return found + decision_form.brief_problems(doc, decision_form.standings(self.decisions()))
        if doc.meta.type == "decision":
            stored = decision_form.committed_doc(self.committed_text(doc.meta.id))
            return decision_form.check(doc, stored, self.decisions(), self.root, self.lists)
        return []

    def _hand_edit(self, edited: ProcessedDoc, text: str, last: str | None) -> derived.HandEdit:
        """A hand edit as `derived.hand_edit` reads it, and for a brief or a decision its own stops too: one that names
        a listed person stays on disk, as one that says a keyword does (DR-0558), and so does one that changes a
        decision's status or links, or a confirmed or rejected decision at all (DR-0603), or one that adds a brief's
        Decisions line no current decision backs (DR-0565)."""
        edit = derived.hand_edit(edited.meta.type, text, last, self.redactor, self.lists)
        if edited.meta.type == "brief":
            # A Decisions line the edit adds or changes is held to what a put holds it to (DR-0565); a line that also
            # names a listed person says that instead.
            standing = decision_form.standings(self.decisions())
            found = dict.fromkeys(decision_form.brief_stops(edited, last, standing), decision_form.DECISIONS_EDIT)
            found |= {p.line or 2: brief_form.NAMES_LISTED for p in brief_form.screen(edited, self.root, self.lists)}
        elif edited.meta.type == "decision":
            found = dict(decision_form.hand_stops(edited, text, last, self.root, self.lists))
        else:
            return edit
        return replace(edit, stops=sorted({**found, **dict(edit.stops)}.items()))

    # -- refs ------------------------------------------------------------------------------------------------------

    def _pin_doc(self, doc: ProcessedDoc) -> tuple[ProcessedDoc, list[int], list[DocProblem]]:
        """A doc for a put with its refs checked and pinned; a bare ref is refused."""
        body, pinned, problems = self._pin(doc.meta.id, doc.meta.type, doc.body.split("\n"), doc.head_lines + 1)
        return replace(doc, body="\n".join(body)), pinned, problems

    def _pin(
        self, doc_id: str, doc_type: str, body: list[str], first: int, *, bare: bool = False
    ) -> tuple[list[str], list[int], list[DocProblem]]:
        """Every doc-line ref on a content line checked and pinned (DR-0540): a ref with no hash gets the hash of the
        line it cites, read from the cited doc's file when that rev is its current one, else from git, once per doc
        and rev. A pinned ref is kept as written once its doc exists. A bare ref, which names no rev, is pinned only
        for a hand edit (`bare`); a put refuses it. `first` is the line number in the file of body[0]. The body with
        its refs pinned, the indexes of the lines that changed, and what is wrong."""
        if doc_type == "legacy-note":
            return body, [], []
        cache: dict[tuple[str, int | None], ProcessedDoc | str] = {}
        out = list(body)
        pinned: list[int] = []
        problems: list[DocProblem] = []
        for i, (line, role) in enumerate(zip(body, line_roles(body), strict=True)):
            trailing = trailing_anchors(line) if role == "content" else None
            if trailing is None or not trailing.refs:
                continue
            n = first + i
            swaps: dict[str, str] = {}
            for ref in trailing.refs:
                if ref.doc == doc_id:
                    problems.append(DocProblem(n, f"{ref.token}: a line can't cite its own doc", line))
                    continue
                if ref.rev is not None and ref.hash is not None:
                    if not self.exists(ref.doc):
                        problems.append(DocProblem(n, f"{ref.token}: there is no doc {ref.doc}", line))
                    continue
                cited = self._cited(ref.doc, ref.rev, cache)
                if isinstance(cited, str):
                    problems.append(DocProblem(n, f"{ref.token}: {cited}", line))
                    continue
                rev = cited.meta.rev
                if ref.rev is None and not bare:
                    token = format_ref(ref.doc, rev, ref.line)
                    message = f"{ref.token} names no rev; cite the rev you read, as verify's cite gives it: {token}"
                    problems.append(DocProblem(n, message, line))
                    continue
                citable = cited.citable()
                found = citable.get(ref.line)
                if found is None:
                    message = f"{ref.token}: line {ref.line} of {ref.doc} rev {rev} has no text to cite"
                    problems.append(DocProblem(n, message, line))
                    continue
                twin = next((m for m, other in sorted(citable.items()) if other == found and m != ref.line), None)
                if twin is not None:
                    message = (
                        f"{ref.token}: line {ref.line}'s text also stands at line {twin}; cite a line whose text is "
                        "its own"
                    )
                    problems.append(DocProblem(n, message, line))
                    continue
                swaps[ref.token] = format_ref(ref.doc, rev, ref.line, found)
            if swaps:
                text = line[: len(trailing.text)]
                parts = re.split(r"([ \t]+)", line[len(trailing.text) :])
                out[i] = text + "".join(swaps.get(part, part) for part in parts)
                pinned.append(i)
        return out, pinned, problems

    def _cited(
        self, doc_id: str, rev: int | None, cache: dict[tuple[str, int | None], ProcessedDoc | str]
    ) -> ProcessedDoc | str:
        """The doc a ref cites at its rev (the file's when the rev is its current one, or none is named), or why not."""
        key = (doc_id, rev)
        if key not in cache:
            current = self._safe_get(doc_id)
            if current is not None and (rev is None or rev == current.meta.rev):
                cache[key] = current
            elif rev is None or (current is None and not self.history(doc_id)):
                cache[key] = f"there is no doc {doc_id}"
            else:
                try:
                    cache[key] = self.at_rev(doc_id, rev)
                except DocketError as e:
                    cache[key] = str(e)
        return cache[key]

    # -- the pass ------------------------------------------------------------------------------------------------------

    def redact_all(self, dry_run: bool = False) -> RedactReport:
        """Redact the docs already stored with the settings as they are now (DR-0210): each doc redaction would change
        is written as its next rev, all in one commit, its body redacted in the file's own text and only its rev line
        set, so a doc from a newer Docket keeps what this one doesn't know. A hand edit goes in first through the
        take-in. A doc the never-store lists forbid, one whose frontmatter as written holds a keyword or what
        redaction would hide, and a file that doesn't parse or sits where its id doesn't say are left and named."""
        report = RedactReport(dry_run=dry_run)
        folder = self.root / "processed"
        # Each doc to write: its path, the bytes it was read as, the text to write, and what the report says of it.
        pending: list[tuple[str, bytes, str, Redacting]] = []
        with self.vault.writing():
            for path in sorted(folder.rglob("*.md")) if folder.is_dir() else []:
                rel = path.relative_to(self.root).as_posix()
                data = path.read_bytes()
                try:
                    text = data.decode("utf-8")
                    doc = parse_doc(text, lenient=True)
                except (UnicodeDecodeError, DocketError):
                    report.left.append(f"{rel}: it doesn't parse as a doc; fix it")
                    continue
                meta = doc.meta
                if doc_path(meta.id).as_posix() != rel:
                    report.left.append(f"{rel}: its id {meta.id} belongs at {doc_path(meta.id)}")
                    continue
                history = self.history(meta.id)
                last = normalize_newlines(self._blob(history[0][1], meta.id)) if history else None
                stored = max((n for n, _ in history), default=0)
                edit = self._hand_edit(doc, text, last)
                if edit.stops:
                    report.left += [f"{rel}: line {n} {what}" for n, what in edit.stops]
                    continue
                if not edit.redacted.lines:
                    continue
                counts = dict(sorted(edit.redacted.counts.items()))
                if normalize_newlines(text) != last:
                    rev = max(stored, meta.rev) + 1
                    if not dry_run:
                        rev, _ = self._take_in(meta.id, path, text, stored, last)
                    report.docs.append(Redacting(id=meta.id, rev=rev, redactions=counts, by_hand=True))
                    continue
                pending.append(
                    (
                        rel,
                        data,
                        _with_rev(edit.text, stored + 1),
                        Redacting(id=meta.id, rev=stored + 1, redactions=counts),
                    )
                )
            writes: list[tuple[str, str]] = []
            for rel, data, new, redacting in pending:
                # A hand edit saved while the pass ran isn't the text it redacted: leave it rather than write over it.
                if (self.root / rel).read_bytes() != data:
                    report.left.append(f"{rel}: it changed while the pass ran; run docket doc redact again")
                    continue
                report.docs.append(redacting)
                writes.append((rel, new))
            if writes and not dry_run:
                lines = "\n".join(f"Doc: {r.id} rev {r.rev}" for r in report.docs if not r.by_hand)
                report.commit = self._write_all_and_commit(writes, f"Redact docs: {len(writes)}\n\n{lines}")
        return report

    def _write_all_and_commit(self, files: list[tuple[str, str]], message: str) -> str | None:
        """Write each (path, text) and commit them together. If a write or the commit fails, every file written goes
        back to its old bytes (or away, when it is new) and nothing stays staged."""
        originals: list[tuple[str, bytes | None]] = []
        try:
            for rel, text in files:
                path = self.root / rel
                originals.append((rel, path.read_bytes() if path.is_file() else None))
                self._replace(path, text.encode("utf-8"))
            return self.vault.commit([rel for rel, _ in files], message)
        except BaseException as e:
            for rel, original in reversed(originals):
                if not isinstance(e, UnmergedError):
                    self._unstage(rel)
                if original is None:
                    (self.root / rel).unlink(missing_ok=True)
                else:
                    self._replace(self.root / rel, original)
            raise

    def _unstage(self, rel: str) -> None:
        self.vault.git.run("reset", "-q", "--", rel, check=False)

    def _replace(self, path: Path, data: bytes) -> None:
        """Write a file whole through a temp file and a rename; the temp file lives in .git, never in processed/."""
        tmp_dir = make_private(self.vault.home.git_dir, self.vault.home.tmp_dir)
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


def _add_rev(revs: list[tuple[int, str]], doc_id: str, sha: str, message: str) -> None:
    """The rev a commit's message gives a doc, its first `Doc:` line for it, when it has one."""
    for line in message.splitlines():
        m = DOC_LINE.match(line.strip())
        if m and m.group(1) == doc_id:
            revs.append((int(m.group(2)), sha))
            return


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


def _pinned_lines(result: PutResult, doc: ProcessedDoc, pinned: list[int]) -> list[int]:
    """The stored doc's lines whose refs a put pinned, numbered as `_told` numbers redacted ones; none when nothing was
    written."""
    if not result.changed or not pinned:
        return []
    close = normalize_newlines(doc.render(rev=0)).split("\n").index("---", 1)
    return [close + 2 + i for i in pinned]


def _told(redacted: derived.Redacted, text: str) -> tuple[dict[str, int], list[int]]:
    """What a put tells its writer about redaction: the markers added per rule, and the lines of the stored doc they
    are on, never what they hid."""
    close = normalize_newlines(text).split("\n").index("---", 1)
    # The body starts on the line after the closing ---: index close is line close + 1, so body line i is close + 2 + i.
    return dict(sorted(redacted.counts.items())), [close + 2 + i for i in redacted.lines]
