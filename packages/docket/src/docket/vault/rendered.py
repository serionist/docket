"""Files Docket writes in the user's repo from the entities and never commits (DR-0552 to DR-0554): the people
directory and the glossary, at the paths `vault.yaml`'s `rendered` names in the consuming repo.

Each is a markdown table. Its first line says it was rendered from the vault, names the last commit that changed an
entity file, and holds a hash of the rest, so a hand edit is told apart from Docket's own text on any machine, and left
as it is. The same entities give the same bytes, so an unchanged set rewrites nothing. A repo whose remote anyone can
read gets no file. An entity whose id or name a never-store list holds now is left out, and so is a listed alias, id
or role, as Curate and `docket curate show` hide them.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from docket.errors import DocketError
from docket.vault.anchors import normalize_newlines
from docket.vault.config import NeverStore
from docket.vault.entities import Entity, load_entities
from docket.vault.git import Git, toplevel
from docket.vault.host import Visibility, remote_urls, remote_visibility
from docket.vault.neverstore import listed, slug_listed
from docket.vault.staging import write_replacing
from docket.vault.vault import Vault

HEADER = re.compile(
    r"^<!-- Rendered by Docket from the vault's entities at (\S+); a hand edit is reported, never kept\. "
    r"sha256:([0-9a-f]{16}) -->$"
)
# The entity files, never the curate log beside them, so a logged rejection rewrites nothing.
ENTITY_FILES = (":(glob)entities/*.yaml", ":(glob)entities/*/*.yaml")
State = Literal["written", "unchanged", "edited", "refused"]


@dataclass(frozen=True)
class Outcome:
    """What a render did with one file, by its path in `vault.yaml`."""

    path: str
    state: State
    why: str = ""

    @property
    def line(self) -> str:
        if self.state == "written":
            return f"rendered   {self.path}"
        if self.state == "unchanged":
            return f"unchanged  {self.path}"
        if self.state == "edited":
            return (
                f"left       {self.path}: it was edited by hand, or Docket didn't write it; move what it holds into "
                "the entities, then delete it to have it rendered"
            )
        return f"refused    {self.path}: {self.why}"


def render_all(vault: Vault, visibility: Callable[[str], Visibility] = remote_visibility) -> list[Outcome]:
    """Every file `vault.yaml` names, rendered from the entities as they are in the vault's folder. The consuming
    repo's remotes are checked only when a file would change, once for the call."""
    files = vault.config.rendered
    if not files:
        return []
    repo = toplevel(vault.root.parent)
    if repo is None:
        why = f"{vault.root} isn't inside a git repo, so there is no repo to render into"
        return [Outcome(f.path, "refused", why) for f in files]
    repo = repo.resolve()
    entities = [e for e in load_entities(vault.root).entities if e.status == "active"]
    commit = Git(vault.root, home=vault.home).out("log", "-1", "--format=%h", "--abbrev=12", "--", *ENTITY_FILES)
    texts = {"people": people, "glossary": glossary}
    languages = vault.config.owner.languages
    lists = vault.config.never_store
    public: list[str] | None = None
    out: list[Outcome] = []
    for file in files:
        target = (repo / file.path).resolve()
        if not target.is_relative_to(repo) or target.is_relative_to(vault.root.resolve()):
            why = f"it must stay in {repo} and outside the vault, after links are followed"
            out.append(Outcome(file.path, "refused", why))
            continue
        text = with_header(commit.strip() or "none", texts[file.kind](entities, languages, lists))
        existing = normalize_newlines(target.read_text(encoding="utf-8")) if target.is_file() else None
        if existing == text:
            out.append(Outcome(file.path, "unchanged"))
            continue
        if existing is not None and not written_by_docket(existing):
            out.append(Outcome(file.path, "edited"))
            continue
        if public is None:
            public = [url for url in remote_urls(repo) if visibility(url) == "public"]
        if public:
            why = f"{public[0]} can be read without signing in; Docket renders nothing into a public repo"
            out.append(Outcome(file.path, "refused", why))
            continue
        if not target.parent.is_dir():
            out.append(Outcome(file.path, "refused", f"its folder {target.parent} doesn't exist"))
            continue
        write_replacing(vault.home, target, text, f"rendered-{file.kind}")
        out.append(Outcome(file.path, "written"))
    return out


# -- the text ---------------------------------------------------------------------------------------------------------


def with_header(commit: str, body: str) -> str:
    """The first line, naming the commit and the hash of the body, then the body."""
    found = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return (
        f"<!-- Rendered by Docket from the vault's entities at {commit}; a hand edit is reported, never kept. "
        f"sha256:{found} -->\n{body}"
    )


def written_by_docket(text: str) -> bool:
    """Whether a file's first line is Docket's and the rest still hashes to it."""
    first, _, body = normalize_newlines(text).partition("\n")
    m = HEADER.match(first)
    return m is not None and hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] == m.group(2)


def _cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def _table(title: str, columns: list[str], rows: Iterable[list[str]]) -> str:
    lines = [f"# {title}", "", "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _hidden(entity: Entity, lists: NeverStore) -> bool:
    """An entity a never-store list holds by its id or one of its names: left out whole."""
    return slug_listed(entity.id, lists) or any(listed(name, lists) is not None for name in entity.names.values())


def _kept(values: Iterable[str], lists: NeverStore) -> list[str]:
    return [v for v in values if listed(v, lists) is None]


def _aliases(entity: Entity, lists: NeverStore) -> str:
    return ", ".join(
        f"{a.text} (asr)" if a.kind == "asr" else a.text for a in entity.aliases if listed(a.text, lists) is None
    )


def _first_name(entity: Entity, languages: list[str]) -> str:
    """Its name in the first of the owner's languages that has one, else in the first language it has."""
    return (
        next((entity.names[lang] for lang in languages if lang in entity.names), None)
        or entity.names[min(entity.names)]
    )


def people(entities: list[Entity], languages: list[str], lists: NeverStore) -> str:
    """The people directory: each active person a list doesn't hold, by name, with a name per owner language, role,
    chat and mail ids, and aliases."""
    shown = [e for e in entities if e.type == "person" and not _hidden(e, lists)]
    shown.sort(key=lambda e: (_first_name(e, languages).casefold(), e.id))
    rows = [
        [
            *(e.names.get(lang, "") for lang in languages),
            e.role if e.role is not None and listed(e.role, lists) is None else "",
            ", ".join(f"{key}: {v}" for key in sorted(e.ids) if key != "email" for v in _kept(e.ids[key], lists)),
            ", ".join(_kept(e.ids.get("email", ()), lists)),
            _aliases(e, lists),
        ]
        for e in shown
    ]
    columns = [*(f"Name ({lang})" for lang in languages), "Role", "Chat", "Mail", "Aliases"]
    return _table("People", columns, rows)


def glossary(entities: list[Entity], languages: list[str], lists: NeverStore) -> str:
    """The glossary: each active entity of any other type a list doesn't hold, by type and term, with its other names
    and its spellings."""
    shown = [e for e in entities if e.type != "person" and not _hidden(e, lists)]
    shown.sort(key=lambda e: (e.type, _first_name(e, languages).casefold(), e.id))
    rows: list[list[str]] = []
    for e in shown:
        term = _first_name(e, languages)
        others = sorted({name for name in e.names.values() if name != term})
        rows.append([term, e.type, ", ".join(others), _aliases(e, lists)])
    return _table("Glossary", ["Term", "Type", "Other names", "Spellings"], rows)


def rendered_lines(outcomes: list[Outcome]) -> list[str]:
    """What a curate write says of the render after it: each file written, left or refused; unchanged ones quiet."""
    return [o.line for o in outcomes if o.state != "unchanged"]


def safely(vault: Vault) -> list[str]:
    """`render_all` after a curate write: a render that fails says so and never fails the write."""
    try:
        return rendered_lines(render_all(vault))
    except (DocketError, OSError) as e:
        return [f"render failed: {e}"]
