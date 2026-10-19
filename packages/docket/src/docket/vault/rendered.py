"""Files Docket writes in the user's repo and never commits (DR-0552 to DR-0554): the people directory and the
glossary from the entities, at the paths `vault.yaml`'s `rendered` names in the consuming repo; and what a stream's
STATUS.md render shares with them (`docket.ledger.status`, DR-0673 to DR-0676): the repo, found by a stat, the path
check, the remotes and their probe, `Outcome`, and the write that reads the file again right before its rename.

Each entity file is a markdown table. Its first line says it was rendered from the vault, names the last commit that
changed an entity file, and holds a hash of the rest, so a hand edit is told apart from Docket's own text on any
machine, and left as it is. The same entities give the same bytes, so an unchanged set rewrites nothing. A repo whose
remote anyone can read gets no file. An entity whose id or name a never-store list holds now is left out, and so is a
person the people list names by any of their values; a keyword drops only the alias, id or role it is in, as Curate and
`docket curate show` hide them.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from docket.errors import DocketError
from docket.vault.anchors import normalize_newlines
from docket.vault.config import NeverStore, RenderedFile
from docket.vault.entities import Entity, load_entities
from docket.vault.git import Git
from docket.vault.home import is_git_dir
from docket.vault.host import Probe, probe_remote, remote_urls
from docket.vault.neverstore import listed, person_listed, slug_listed
from docket.vault.staging import write_replacing
from docket.vault.vault import Vault

HEADER = re.compile(
    r"^<!-- Rendered by Docket from the vault's entities at (\S+); a hand edit is reported, never kept\. "
    r"sha256:([0-9a-f]{16}) -->$"
)
# The entity files, never the curate log beside them, so a logged rejection rewrites nothing.
ENTITY_FILES = (":(glob)entities/*.yaml", ":(glob)entities/*/*.yaml")
ENTITY_KINDS = ("people", "glossary")
State = Literal["written", "unchanged", "edited", "refused"]
# Each remote's answer, kept for the process once it is sure (DR-0554).
_PROBED: dict[str, Probe] = {}
# When a remote couldn't be reached, kept for UNREACHED_FOR seconds and then asked again: a probe of one that drops
# packets waits about 20 s, and every ledger write that changes a stream would wait on it. A file stays unwritten
# meanwhile (DR-0676).
_UNREACHED: dict[str, float] = {}
UNREACHED_FOR = 300.0
# Each repo's remote URLs, kept for the process while its git config file keeps its size and time: three git calls a
# render saved, a tenth of a second on Windows, after every ledger write that changes a stream.
_REMOTES: dict[Path, tuple[tuple[int, int], list[str]]] = {}


@dataclass(frozen=True)
class Outcome:
    """What a render did with one file, by its path in `vault.yaml`: a left one says why when it isn't an entity
    file's hand edit."""

    path: str
    state: State
    why: str = ""

    @property
    def line(self) -> str:
        if self.state == "written":
            return f"rendered   {self.path}"
        if self.state == "unchanged":
            return f"unchanged  {self.path}"
        if self.state == "edited" and self.why:
            return f"left       {self.path}: {self.why}"
        if self.state == "edited":
            return (
                f"left       {self.path}: it was edited by hand, or Docket didn't write it; move what it holds into "
                "the entities, then delete it to have it rendered"
            )
        return f"refused    {self.path}: {self.why}"


def render_all(vault: Vault, probe: Callable[[str], Probe] = probe_remote) -> list[Outcome]:
    """Every people and glossary file `vault.yaml` names, rendered from the entities as they are in the vault's folder.
    The consuming repo's remotes are checked whenever a file Docket wrote sits at a path or one would be written, each
    once per process while its answer is sure: a public one is reported even when nothing changes, and one that
    couldn't be reached keeps a file from being written, and is asked again after five minutes."""
    files = [f for f in vault.config.rendered if f.kind in ENTITY_KINDS]
    if not files:
        return []
    repo = consuming_repo(vault)
    if repo is None:
        return [Outcome(f.path, "refused", no_repo(vault)) for f in files]
    entities = [e for e in load_entities(vault.root).entities if e.status == "active"]
    commit = Git(vault.root, home=vault.home).out("log", "-1", "--format=%h", "--abbrev=12", "--", *ENTITY_FILES)
    texts = {"people": people, "glossary": glossary}
    languages = vault.config.owner.languages
    lists = vault.config.never_store
    remotes = Remotes(repo, probe)
    out: list[Outcome] = []
    for file in files:
        target, why = target_of(vault, repo, file)
        if target is None:
            out.append(Outcome(file.path, "refused", why))
            continue
        try:
            existing = _existing(target)
        except OSError as e:
            out.append(Outcome(file.path, "refused", unreadable(e)))
            continue
        if existing is not None and (existing is _NOT_TEXT or not written_by_docket(existing)):
            out.append(Outcome(file.path, "edited"))
            continue
        public = remotes.public()
        if public:
            out.append(Outcome(file.path, "refused", public))
            continue
        text = with_header(commit.strip() or "none", texts[file.kind](entities, languages, lists))
        if existing == text:
            out.append(Outcome(file.path, "unchanged"))
            continue
        unreached = remotes.unreached()
        if unreached:
            out.append(Outcome(file.path, "refused", unreached))
            continue
        if not target.parent.is_dir():
            out.append(Outcome(file.path, "refused", no_folder(target)))
            continue
        write_replacing(vault.home, target, text, f"rendered-{file.kind}")
        out.append(Outcome(file.path, "written"))
    return out


# -- what a status file's render shares ------------------------------------------------------------------------------


def consuming_repo(vault: Vault) -> Path | None:
    """The repo the files go in, resolved: a folder vault's host; for a repo of its own, the first folder above it
    holding a .git git would take, found by a stat, so a write's render runs no git process."""
    if vault.home.folder:
        return vault.home.top.resolve()
    for folder in vault.root.resolve().parents:
        dot = folder / ".git"
        # git skips a .git folder that isn't a git dir and looks further up, and a .git file names a worktree's.
        if dot.is_file() or is_git_dir(dot):
            return folder
    return None


def no_repo(vault: Vault) -> str:
    return f"{vault.root} isn't inside a git repo, so there is no repo to render into"


def target_of(vault: Vault, repo: Path, file: RenderedFile) -> tuple[Path | None, str]:
    """A file's place after links are followed, or None and why it is refused: outside the repo, in the vault, or a
    folder (DR-0552)."""
    target = (repo / file.path).resolve()
    if not target.is_relative_to(repo) or target.is_relative_to(vault.root.resolve()):
        return None, f"it must stay in {repo} and outside the vault, after links are followed"
    if target.is_dir():
        return None, f"{file.path} is a folder"
    return target, ""


def unreadable(e: OSError) -> str:
    return f"it can't be read: {e.strerror or type(e).__name__}"


def no_folder(target: Path) -> str:
    return f"its folder {target.parent} doesn't exist"


class Remotes:
    """The consuming repo's remotes, listed and probed on the first ask, each answer kept for the process while it is
    sure (DR-0554), and five minutes when a remote couldn't be reached (DR-0676)."""

    def __init__(self, repo: Path, probe: Callable[[str], Probe]) -> None:
        self.repo = repo
        self.probe = probe
        self._found: dict[str, Probe] | None = None

    def found(self) -> dict[str, Probe]:
        if self._found is None:
            self._found = {url: _probed(url, self.probe) for url in remotes(self.repo)}
        return self._found

    def public(self) -> str:
        """Why nothing is rendered when a remote anyone can read is among them, else ""."""
        public = [url for url, found in self.found().items() if found == "public"]
        if not public:
            return ""
        return f"{public[0]} can be read without signing in; Docket renders nothing into a public repo"

    def unreached(self) -> str:
        """Why nothing is written this time when a remote couldn't be checked, else ""."""
        unreached = [url for url, found in self.found().items() if found == "unreachable"]
        return f"couldn't check the remote {unreached[0]}; not written" if unreached else ""


def remotes(repo: Path) -> list[str]:
    """The repo's remote URLs as git will use them, from this process's memory while `.git/config` keeps its size and
    time; a repo whose .git is a file, a worktree's, is asked each time."""
    try:
        stat = (repo / ".git" / "config").stat()
    except OSError:
        return remote_urls(repo)
    key = (stat.st_size, stat.st_mtime_ns)
    held = _REMOTES.get(repo)
    if held is not None and held[0] == key:
        return list(held[1])
    urls = remote_urls(repo)
    _REMOTES[repo] = (key, urls)
    return list(urls)


def file_bytes(target: Path) -> bytes | None:
    """A rendered file's bytes, or None when there is none."""
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return None


def write_unless_changed(vault: Vault, target: Path, text: str, before: bytes | None) -> tuple[bool, bytes | None]:
    """A status file written through a temp file and a rename, unless it changed since it was read as `before`: it is
    read again right before each try at the rename, so a save made while another program held it open is kept. Whether
    it was written, and when it wasn't, what was found there (None for no file, or one that couldn't be read)."""
    found: list[bytes | None] = [None]

    def still() -> bool:
        try:
            found[0] = file_bytes(target)
        except OSError:
            found[0] = None
            return False
        return found[0] == before

    # A temp name of its own: two renders in one process never share one.
    if write_replacing(vault.home, target, text, f"rendered-status-{secrets.token_hex(4)}", still):
        return True, None
    return False, found[0]


# A file at a path that isn't UTF-8 text: one kept by hand, never Docket's.
_NOT_TEXT = "\x00not text"


def _existing(target: Path) -> str | None:
    """The file at a rendered path, LF-normalized; None when there is none, and `_NOT_TEXT` when it isn't UTF-8, as a
    people.md a text editor saved as UTF-16 isn't."""
    if not target.is_file():
        return None
    try:
        return normalize_newlines(target.read_bytes().decode("utf-8"))
    except UnicodeDecodeError:
        return _NOT_TEXT


def _probed(url: str, probe: Callable[[str], Probe]) -> Probe:
    """A remote's answer, from this process's memory when it was sure, or for five minutes when it couldn't be
    reached."""
    found = _PROBED.get(url)
    if found is not None:
        return found
    since = _UNREACHED.get(url)
    if since is not None and time.monotonic() - since < UNREACHED_FOR:
        return "unreachable"
    found = probe(url)
    if found == "unreachable":
        _UNREACHED[url] = time.monotonic()
    else:
        _PROBED[url] = found
        _UNREACHED.pop(url, None)
    return found


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
    # A person the people list names by any value loses the whole row, since the rest would still say who they are.
    shown = [e for e in entities if e.type == "person" and not _hidden(e, lists) and not person_listed(e, lists)]
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
    """`render_all` after a curate write: a render that fails says so and never fails the write (DR-0554). Any other
    error is named by its type only, so no value it carries reaches the write's result."""
    try:
        return rendered_lines(render_all(vault))
    except (DocketError, OSError) as e:
        return [f"render failed: {e}"]
    except Exception as e:  # the write is committed: a render's failure is reported, never raised
        return [f"render failed ({type(e).__name__})"]
