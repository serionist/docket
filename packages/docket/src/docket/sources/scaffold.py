"""`docket source new`: a source's skill, mapper and tests written into the consuming project, from Docket's blank
template or a reference source copied whole, and the source registered in vault.yaml (DR-0108, DR-0124).

The folder is the one the runner takes a mapper from (DR-0123), checked after links before anything is written, and
never written over. Its files land through a temp folder and one rename. vault.yaml is rewritten only when its text is
what Docket would write for it, so no comment or layout of the user's is lost; otherwise the entry comes back to paste.
A failure on the way leaves neither a folder nor a changed vault.yaml.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from docket import userconfig
from docket.errors import DocketError
from docket.sources.runner import SKILLS, consuming_repo, skill_folder
from docket.vault.config import CONFIG_FILE, SourceConfig, VaultConfig, write_config
from docket.vault.git import toplevel
from docket.vault.records import source_name_problem
from docket.vault.staging import retry
from docket.vault.vault import Vault, remove_tree
from docket.vault.wizard import KNOWN_SOURCES

Sensitivity = Literal["work", "mixed", "private"]
PLACEHOLDER = "{{source}}"
# Template files carry it so no linter, type checker or test runner takes them for the package's own code.
SUFFIX = ".tmpl"
BLANK = "blank"


@dataclass(frozen=True)
class Reference:
    """A reference source the wheel carries, copied whole under its own name and registered as its SKILL.md says."""

    sensitivity: Sensitivity
    tools: tuple[str, ...]


REFERENCES: dict[str, Reference] = {
    "plaud": Reference("mixed", ("list_files", "get_file", "get_transcript", "get_note")),
}


@dataclass(frozen=True)
class NewSource:
    name: str
    folder: Path
    files: list[str]
    example: bool
    entry: SourceConfig
    # vault.yaml rewritten (and committed when it changed), or the entry handed back as `block` to paste.
    registered: bool
    block: str | None
    commit: str | None


def template_files(kind: str) -> dict[str, bytes]:
    """A template's files by their path in the new folder, the suffix dropped: `blank`, or a reference's name."""
    root = resources.files("docket.sources").joinpath("templates", kind)
    if not root.is_dir():
        raise DocketError(f"no template {kind!r}")
    out: dict[str, bytes] = {}
    _walk(root, "", out)
    return out


def _walk(node: Traversable, prefix: str, out: dict[str, bytes]) -> None:
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        if child.is_dir():
            _walk(child, f"{prefix}{child.name}/", out)
        else:
            out[prefix + child.name.removesuffix(SUFFIX)] = child.read_bytes()


def new_source(
    vault: Vault,
    name: str,
    *,
    example: bool = False,
    sensitivity: Sensitivity | None = None,
    tools: list[str] | None = None,
) -> NewSource:
    repo = consuming_repo(vault.root)
    # Checks the name first (DR-0125), then the folder after links.
    folder = skill_folder(repo, vault.root, name)
    reference = REFERENCES.get(name) if example else None
    if example and reference is None:
        raise DocketError(f"no reference source named {name}; --example copies one of: {', '.join(sorted(REFERENCES))}")
    if folder.exists() or folder.is_symlink():
        raise DocketError(f"{folder} already exists; a source's folder is never written over")
    entry = _entry(name, vault.config.sources.get(name), reference, sensitivity, tools or [])
    files = template_files(name if example else BLANK)
    if not example:
        files = {rel: data.decode("utf-8").replace(PLACEHOLDER, name).encode("utf-8") for rel, data in files.items()}
    _write_folder(folder, files)
    try:
        registered, block, commit = _register(vault, name, entry)
    except BaseException:
        remove_tree(folder)
        raise
    return NewSource(name, folder, sorted(files), example, entry, registered, block, commit)


@dataclass(frozen=True)
class NewFolderSource:
    name: str
    entry: SourceConfig
    registered: bool
    block: str | None
    commit: str | None


def new_folder_source(vault: Vault, name: str, given: Path) -> NewFolderSource:
    """A folder source in vault.yaml (DR-0481): the folder as every machine reads it, relative to the repo that holds
    the vault or under ~/. A folder elsewhere is refused with the fix, a machine's own folder in the user's config."""
    problem = source_name_problem(name)
    if problem is not None:
        raise DocketError(problem)
    existing = vault.config.sources.get(name)
    if existing is not None and existing.folder is None and (existing.mapper or existing.tools):
        raise DocketError(f"source {name} in vault.yaml has a mapper or tools; a folder source is a source of its own")
    folder = written_folder(vault, given)
    # What vault.yaml already says stays: a source the user made mixed or private is refused with the validator's
    # own fix, never turned to work and committed.
    kept = existing or SourceConfig()
    try:
        entry = SourceConfig(
            enabled=kept.enabled,
            sensitivity=kept.sensitivity,
            media_hosts=kept.media_hosts,
            folder=folder,
            exclude=kept.exclude,
        )
    except ValidationError as e:
        raise DocketError(f"source {name}: " + "; ".join(str(err["msg"]) for err in e.errors())) from None
    registered, block_text, commit = _register(vault, name, entry)
    return NewFolderSource(name, entry, registered, block_text, commit)


def written_folder(vault: Vault, given: Path) -> str:
    """A folder as vault.yaml holds it: `~` and what is under the home folder as ~/..., else its path from the repo
    that holds the vault."""
    text = str(given).replace("\\", "/")
    if text == "~" or text.startswith("~/"):
        return text
    folder = given.expanduser().resolve()
    home = Path.home().resolve()
    repo = toplevel(vault.root.parent)
    if repo is not None and folder.is_relative_to(repo):
        rel = folder.relative_to(repo).as_posix()
        return "." if rel == "." else rel
    if folder.is_relative_to(home):
        rel = folder.relative_to(home).as_posix()
        return "~" if rel == "." else f"~/{rel}"
    raise DocketError(
        f"{folder} is outside the repo that holds the vault and outside your home folder, so vault.yaml can't name it "
        "the same way on every machine; add the source with a folder under ~/, then give this machine's own folder "
        f"in {userconfig.config_path()}, under folders"
    )


def _entry(
    name: str,
    existing: SourceConfig | None,
    reference: Reference | None,
    sensitivity: Sensitivity | None,
    tools: list[str],
) -> SourceConfig:
    """The source's entry: what the flags give, else what vault.yaml already has, else the reference's, else Docket's
    guess for a source it knows. Checked here, before anything is written."""
    kept_level = existing.sensitivity if existing is not None else None
    level = sensitivity or kept_level or (reference.sensitivity if reference else KNOWN_SOURCES.get(name, "mixed"))
    kept_tools = existing.tools if existing is not None else []
    try:
        return SourceConfig(
            enabled=existing.enabled if existing is not None else True,
            sensitivity=level,
            mapper="/".join([*SKILLS, name, "mapper.py"]),
            tools=tools or kept_tools or list(reference.tools if reference else ()),
            media_hosts=existing.media_hosts if existing is not None else [],
        )
    except ValidationError as e:
        raise DocketError(f"source {name}: " + "; ".join(str(err["msg"]) for err in e.errors())) from None


def _write_folder(folder: Path, files: dict[str, bytes]) -> None:
    """Every file into a temp folder beside the new one, then one rename, so a failure leaves no folder behind."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = folder.parent / f".{folder.name}.new-{secrets.token_hex(4)}"
    tmp.mkdir()
    try:
        for rel, data in files.items():
            target = tmp.joinpath(*rel.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        retry(lambda: tmp.rename(folder))
    except BaseException:
        remove_tree(tmp)
        raise


def _register(vault: Vault, name: str, entry: SourceConfig) -> tuple[bool, str | None, str | None]:
    """`sources.<name>` in vault.yaml, rewritten and committed only when the file is exactly what Docket would write
    for it, now or in the form before the redaction switches were split; otherwise the entry, to paste."""
    path = vault.root / CONFIG_FILE
    with vault.writing():
        text = path.read_bytes().decode("utf-8")
        config = VaultConfig.load(path)
        if not config.written_by_docket(text):
            return False, block(name, entry), None
        updated = config.model_copy(update={"sources": {**config.sources, name: entry}}).dump()
        if updated == text:
            return True, None, None
        write_config(vault.home, path, updated)
        try:
            commit = vault.commit([CONFIG_FILE], f"Config: source {name}")
        except BaseException:
            write_config(vault.home, path, text)
            raise
        return True, None, commit


def block(name: str, entry: SourceConfig) -> str:
    """The entry as the lines to paste under `sources:` in vault.yaml."""
    body = yaml.safe_dump({name: entry.model_dump(mode="json")}, sort_keys=False, default_flow_style=False)
    return "".join(f"  {line}\n" for line in body.splitlines())
