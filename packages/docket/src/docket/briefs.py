"""Which briefs are stale, and why (DR-0556): what `docket brief stale` prints and the `briefs` MCP tool answers.

A brief is stale when records that mention its entity sit in run files its last rev's commit didn't have, or when a
line of it cites a doc line that changed since (#166's flags). Which run files a brief could have seen is read from
git: the tree of that commit, `git ls-tree` over `raw/`, read once a process for each commit, since a commit's tree
never changes, and never fetched (`GIT_NO_LAZY_FETCH`). No
clock is compared, so a record written elsewhere before the brief and pulled after it still counts. A brief whose
commit or tree isn't on this clone is stale, its history not here, never current. A brief whose entity is gone is
orphaned, and names the entity that holds its name now when exactly one does. A brief a never-store list holds by its
entity is left out and counted as hidden.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from docket.index import flags
from docket.index import update as index_update
from docket.index.build import open_index
from docket.vault import brief_form, decision_form
from docket.vault.config import NeverStore
from docket.vault.docstore import DocStore
from docket.vault.entities import Entity, load_entities
from docket.vault.git import Git
from docket.vault.neverstore import fold, listed, slug_listed, spelled
from docket.vault.processed import ProcessedDoc
from docket.vault.vault import Vault

State = Literal["stale", "current", "orphaned"]
HISTORY_NOT_HERE = "its history isn't here"
NEWEST = 5
# Git never reaches for a missing object over the network while Docket asks what a commit holds.
NO_FETCH = {"GIT_NO_LAZY_FETCH": "1"}
# Each commit's run files under raw/, by its id: a commit's tree never changes, so each is read once a process, which
# keeps the Briefs page's read from one git call a brief. A tree that didn't read isn't kept: a pull may bring it.
_TREES: dict[str, frozenset[str]] = {}
TREES_KEPT = 2000
# Each commit's time, by its id, which never changes either.
_TIMES: dict[str, str] = {}


class NewRecords(BaseModel):
    count: int = 0
    newest: list[str] = Field(default_factory=list[str])


class BriefState(BaseModel):
    """One brief: its entity, its last rev, and what makes it stale (DR-0556, the fields the MCP test pins)."""

    id: str
    entity: str
    name: str
    type: str
    rev: int
    rev_time: str | None = None
    new: NewRecords = Field(default_factory=NewRecords)
    flagged: int = 0
    orphaned: str | None = None
    state: State
    why: str | None = None


class BriefsReport(BaseModel):
    """The briefs asked for, how many a never-store list hides, and the entities asked for that have none yet."""

    briefs: list[BriefState] = Field(default_factory=list[BriefState])
    hidden: int = 0
    missing: list[str] = Field(default_factory=list[str])


def report(
    vault: Vault, entities: list[str] | None = None, *, stale_only: bool = True, update: bool = True
) -> BriefsReport:
    """Every brief's state, or the named entities' briefs; with `stale_only`, the current ones left out. The index is
    brought up to date first, as a search does, unless the caller did (`update`: the service waits for its own)."""
    if update:
        index_update(vault)
    store = DocStore(vault)
    docs = [d for d in store.all() if d.meta.type == "brief"]
    known = load_entities(vault.root).entities
    by_id = {e.id: e for e in known}
    lists = vault.config.never_store
    languages = vault.config.owner.languages
    wanted = set(entities) if entities is not None else None
    out = BriefsReport()
    shown: list[ProcessedDoc] = []
    for doc in docs:
        entity_id = doc.meta.id.partition(":")[2]
        if wanted is not None and entity_id not in wanted:
            continue
        entity = by_id.get(entity_id)
        holders = _holders(doc, entity_id, known) if entity is None else []
        if _held(entity, entity_id, _title(doc), lists, holders):
            out.hidden += 1
            continue
        shown.append(doc)
    if wanted is not None:
        have = {d.meta.id.partition(":")[2] for d in docs}
        out.missing = sorted(e for e in wanted if e not in have and not _held(by_id.get(e), e, None, lists))
    if not shown:
        return out
    git = Git(vault.root, home=vault.home)
    histories = store.histories([d.meta.id for d in shown])
    commits = {d.meta.id: histories[d.meta.id][0][1] for d in shown if histories.get(d.meta.id)}
    times = commit_times(git, sorted(set(commits.values())))
    standing = decision_form.standings(store.decisions())
    trees: dict[str, frozenset[str] | None] = {}
    conn = open_index(vault)
    try:
        reader = _Reader(by_id, known, languages, commits, times, trees, git, conn, flags.from_index(conn), standing)
        for doc in shown:
            state = reader.state(doc)
            if not stale_only or state.state != "current":
                out.briefs.append(state)
    finally:
        conn.close()
    return out


def _held(
    entity: Entity | None, entity_id: str, title: str | None, lists: NeverStore, holders: Sequence[Entity] = ()
) -> bool:
    """A brief a never-store list holds by its entity, as doc_put holds it (`brief_form.held`); for a gone entity, by
    its id, its title, or an entity that holds its name now, so an orphan never shows a listed survivor."""
    if entity is not None:
        return brief_form.held(entity, lists)
    return (
        slug_listed(entity_id, lists)
        or (title is not None and listed(title, lists) is not None)
        or any(brief_form.held(e, lists) for e in holders)
    )


def _title(doc: ProcessedDoc) -> str | None:
    return next((line[2:].strip() for _, line in doc.lines() if line.startswith("# ")), None)


def entity_name(entity: Entity, languages: list[str]) -> str:
    first = next((entity.names[lang] for lang in languages if lang in entity.names), None)
    return first or entity.names[min(entity.names)]


def _holders(doc: ProcessedDoc, entity_id: str, known: list[Entity]) -> list[Entity]:
    """The entities that hold a gone entity's name now, its title or the name its id spells, among their names or
    aliases."""
    gone = {fold(n) for n in (_title(doc), spelled(entity_id)) if n}
    return [
        e
        for e in known
        if e.id != entity_id and gone & {fold(v) for v in (*e.names.values(), *(a.text for a in e.aliases))}
    ]


def commit_times(git: Git, commits: list[str]) -> dict[str, str]:
    """Each commit's time (UTC, ISO), one call for all that aren't known yet, or one each when some aren't on this
    clone."""
    known = {commit: _TIMES[commit] for commit in commits if commit in _TIMES}
    asked = [commit for commit in commits if commit not in known]
    if asked:
        found = _times(git, asked)
        if len(_TIMES) + len(found) > TREES_KEPT:
            _TIMES.clear()
        _TIMES.update(found)
        known |= found
    return known


def _times(git: Git, commits: list[str]) -> dict[str, str]:
    proc = git.run("log", "--no-walk=unsorted", "--format=%H%x1f%cI", *commits, check=False, env=NO_FETCH)
    if proc.returncode != 0:
        found: dict[str, str] = {}
        for commit in commits:
            one = git.run("log", "--no-walk", "--format=%H%x1f%cI", commit, check=False, env=NO_FETCH)
            if one.returncode == 0:
                found.update(_parse_times(one.stdout))
        return found
    return _parse_times(proc.stdout)


def _parse_times(out: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in out.splitlines():
        sha, sep, when = line.partition("\x1f")
        if sep:
            found[sha] = when
    return found


def _tree(git: Git, commit: str, trees: dict[str, frozenset[str] | None]) -> frozenset[str] | None:
    """The run files a commit's tree holds under raw/, as vault paths; None when it can't be read here."""
    if commit not in trees:
        kept = _TREES.get(commit)
        if kept is None:
            proc = git.run("ls-tree", "-r", "--name-only", commit, "--", "raw/", check=False, env=NO_FETCH)
            if proc.returncode == 0:
                kept = frozenset(proc.stdout.splitlines())
                if len(_TREES) >= TREES_KEPT:
                    _TREES.clear()
                _TREES[commit] = kept
        trees[commit] = kept
    return trees[commit]


_MENTIONS = """
SELECT DISTINCT c.rid, r.path, r.ts FROM mentions m
JOIN chunks c ON c.id = m.chunk
JOIN records r ON r.rid = c.rid
WHERE m.entity = ? AND c.rid IS NOT NULL
"""


@dataclass
class _Reader:
    """What one report reads each brief against: the entities, each brief's rev commit and its time, the trees read so
    far, git, the index and its flags."""

    by_id: dict[str, Entity]
    known: list[Entity]
    languages: list[str]
    commits: dict[str, str]
    times: dict[str, str]
    trees: dict[str, frozenset[str] | None]
    git: Git
    conn: sqlite3.Connection
    found: list[flags.Flag]
    standing: dict[str, decision_form.Standing]

    def state(self, doc: ProcessedDoc) -> BriefState:
        entity_id = doc.meta.id.partition(":")[2]
        entity = self.by_id.get(entity_id)
        commit = self.commits.get(doc.meta.id)
        base = BriefState(
            id=doc.meta.id,
            entity=entity_id,
            name=entity_name(entity, self.languages) if entity is not None else (_title(doc) or entity_id),
            type=entity.type if entity is not None else "",
            rev=doc.meta.rev,
            rev_time=self.times.get(commit) if commit is not None else None,
            flagged=len({f.line for f in self.found if f.doc == doc.meta.id and f.flagged}),
            state="current",
        )
        if entity is None:
            # The report holds back an orphan any of whose holders a list holds, so a survivor named here never is.
            holders = _holders(doc, entity_id, self.known)
            survivor = holders[0].id if len(holders) == 1 else None
            return base.model_copy(update={"state": "orphaned", "orphaned": survivor})
        unconfirmed = bool(decision_form.unconfirmed(doc, self.standing))
        tree = _tree(self.git, commit, self.trees) if commit is not None and commit in self.times else None
        if tree is None:
            why = f"{HISTORY_NOT_HERE}; {decision_form.NOT_CONFIRMED}" if unconfirmed else HISTORY_NOT_HERE
            return base.model_copy(update={"state": "stale", "why": why})
        seen = [(rid, ts) for rid, path, ts in self.conn.execute(_MENTIONS, (entity_id,)) if path not in tree]
        seen.sort(key=lambda pair: (pair[1], pair[0]), reverse=True)
        new = NewRecords(count=len(seen), newest=[rid for rid, _ in seen[:NEWEST]])
        stale = bool(new.count or base.flagged or unconfirmed)
        why = decision_form.NOT_CONFIRMED if unconfirmed else None
        return base.model_copy(update={"new": new, "state": "stale" if stale else "current", "why": why})
