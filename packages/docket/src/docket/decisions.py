"""Each decision and where it stands (DR-0565): what `docket decision list` prints and the `decisions` MCP tool answers.

A decision is read as every read takes it (DR-0603): a confirmed or rejected one from its last commit, a proposed one
with its status and links from it. It is current when confirmed and no confirmed decision supersedes it, and only a
current one gives the `cite` a brief's Decisions line takes. The links back, superseded and contradicted, come from
the other decisions. A decision on an entity a never-store list holds is left out and counted as hidden, as briefs
are (DR-0558).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from docket.briefs import commit_times
from docket.index import flags
from docket.index import update as index_update
from docket.index.build import open_index
from docket.vault import brief_form, decision_form
from docket.vault.anchors import format_ref
from docket.vault.docstore import DocStore
from docket.vault.entities import load_entities
from docket.vault.git import Git
from docket.vault.neverstore import slug_listed
from docket.vault.processed import ProcessedDoc, doc_path
from docket.vault.vault import Vault


class DecisionState(BaseModel):
    """One decision: what it says, where it stands, its links both ways (DR-0565, the fields the MCP test pins)."""

    id: str
    subject: str
    title: str
    status: str
    current: bool
    rev: int
    rev_time: str | None = None
    by: str
    replaces: list[str] = Field(default_factory=list[str])
    contradicts: list[str] = Field(default_factory=list[str])
    supersedes: list[str] = Field(default_factory=list[str])
    superseded_by: str | None = None
    contradicted_by: list[str] = Field(default_factory=list[str])
    flagged: int = 0
    cite: str | None = None
    note: str | None = None


class DecisionsReport(BaseModel):
    """The decisions asked for, newest first, and how many a never-store list hides."""

    decisions: list[DecisionState] = Field(default_factory=list[DecisionState])
    hidden: int = 0


def report(
    vault: Vault, entities: list[str] | None = None, status: str | None = None, *, update: bool = True
) -> DecisionsReport:
    """Every decision, or those on the named entities, or with one status; newest first, by the day in its id. The
    index is brought up to date first, as a search does, unless the caller did (`update`)."""
    if update:
        index_update(vault)
    store = DocStore(vault)
    docs = store.decisions()
    standing = decision_form.standings(docs)
    known = load_entities(vault.root).entities
    by_id = {e.id: e for e in known}
    lists = vault.config.never_store
    shapes = brief_form.phrases(known, lists)
    wanted = set(entities) if entities is not None else None
    contradicted: dict[str, list[str]] = {}
    for doc in sorted(docs, key=lambda d: d.meta.id):
        for target in doc.meta.contradicts:
            contradicted.setdefault(target, []).append(doc.meta.id)
    out = DecisionsReport()
    shown: list[ProcessedDoc] = []
    for doc in sorted(docs, key=lambda d: d.meta.id, reverse=True):
        meta, now = doc.meta, standing[doc.meta.id]
        named = [by_id[e] for e in (meta.subject or "", *meta.entities) if e in by_id]
        links = (meta.id, *meta.replaces, *meta.contradicts, *meta.supersedes, *contradicted.get(meta.id, []))
        if (
            slug_listed(meta.subject or "", lists)
            or any(brief_form.held(e, lists) for e in named)
            or decision_form.says_someone(now.title, shapes, lists)
            or any(decision_form.id_says_someone(i, shapes, lists) for i in (*links, now.superseded_by or "") if i)
        ):
            # A never-store list holds its subject, or its title or an id it shows names someone (DR-0558).
            out.hidden += 1
            continue
        if (wanted is None or meta.subject in wanted) and (status is None or standing[meta.id].status == status):
            shown.append(doc)
    if not shown:
        return out
    rels = [doc_path(d.meta.id).as_posix() for d in shown]
    committed = store.committed_texts(rels)
    histories = store.histories([d.meta.id for d in shown])
    commits = {d.meta.id: histories[d.meta.id][0][1] for d in shown if histories.get(d.meta.id)}
    times = commit_times(Git(vault.root, home=vault.home), sorted(set(commits.values())))
    conn = open_index(vault)
    try:
        found = [f for f in flags.from_index(conn) if f.flagged]
    finally:
        conn.close()
    for doc, rel in zip(shown, rels, strict=True):
        meta, now = doc.meta, standing[doc.meta.id]
        on_disk = store.read_file(vault.root / rel)
        final = now.status in decision_form.FINAL
        commit = commits.get(meta.id)
        out.decisions.append(
            DecisionState(
                id=meta.id,
                subject=meta.subject or "",
                title=now.title,
                status=now.status,
                current=now.current,
                rev=meta.rev,
                rev_time=times.get(commit) if commit is not None else None,
                by=meta.by,
                replaces=meta.replaces,
                contradicts=meta.contradicts,
                supersedes=meta.supersedes,
                superseded_by=now.superseded_by,
                contradicted_by=contradicted.get(meta.id, []),
                flagged=len({f.line for f in found if f.doc == meta.id}),
                cite=format_ref(meta.id, meta.rev, now.title_line) if now.current and now.title_line else None,
                note=_note(doc, edited=final and decision_form.edited(on_disk, committed.get(rel))),
            )
        )
    return out


def _note(doc: ProcessedDoc, *, edited: bool) -> str | None:
    """What the tool says of a decision beside its state: a final one edited on disk (`edited`), or one a forget left
    with no evidence (DR-0580)."""
    if edited:
        return decision_form.EDITED
    return decision_form.EVIDENCE_FORGOTTEN if decision_form.evidence_gone(doc) else None
