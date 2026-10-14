"""`docket init`: the questions that set up a vault, then `Vault.init`.

Every answer can also come from a flag, so `docket init --yes --path ... --owner ...` runs unattended. Nothing is
written before the summary is confirmed. Inside a git repo the vault is a folder of it by default; the host checks
decide whether that is allowed, and a repo of its own is always offered.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from docket import userconfig
from docket.errors import DocketError
from docket.vault import host
from docket.vault import machine as machines
from docket.vault.config import NeverStore, Owner, SourceConfig, VaultConfig
from docket.vault.git import Git, toplevel
from docket.vault.home import Storage
from docket.vault.host import HostReport, Visibility, remote_visibility
from docket.vault.vault import Vault, attribute_mismatches

__all__ = ["Given", "Plan", "Visibility", "Wizard", "nest_in_parent", "remote_visibility"]

# The sources Docket knows, and how private each can be: "mixed" ones can carry private content, so their records
# wait in staging until classified.
KNOWN_SOURCES: dict[str, Literal["work", "mixed", "private"]] = {
    "plaud": "mixed",
    "slack": "work",
    "outlook": "work",
    "teams": "work",
    "calendar": "work",
    "gmail": "mixed",
    "claude-sessions": "work",
    "documents": "work",
}


class Prompter(Protocol):
    def ask(self, question: str, default: str | None = None) -> str: ...
    def confirm(self, question: str, default: bool = False) -> bool: ...
    def say(self, text: str) -> None: ...


@dataclass
class Given:
    """What the flags already answered; None means ask (or take the default under --yes)."""

    path: Path | None = None
    owner: str | None = None
    emails: list[str] = field(default_factory=list[str])
    languages: list[str] = field(default_factory=list[str])
    time_zone: str | None = None
    machine: str | None = None
    sources: list[str] = field(default_factory=list[str])
    storage: str | None = None
    remote: str | None = None
    remote_is_private: bool = False
    allow_shared: bool = False
    allow_dirty_parent: bool = False
    yes: bool = False


@dataclass
class Plan:
    path: Path
    parent: Path | None
    config: VaultConfig
    machine: str
    remote: str | None
    new_github_repo: str | None
    host: HostReport | None = None


def _git_config(key: str) -> str:
    proc = subprocess.run(["git", "config", key], capture_output=True, text=True, encoding="utf-8", check=False)
    return proc.stdout.strip()


def local_time_zone() -> str | None:
    try:
        from tzlocal import get_localzone_name  # noqa: PLC0415 - only the wizard needs it

        return get_localzone_name()
    except Exception:
        return None


def _gh_ready() -> bool:
    if shutil.which("gh") is None:
        return False
    return subprocess.run(["gh", "auth", "status"], capture_output=True, check=False).returncode == 0


def _parse_sources(items: list[str]) -> dict[str, SourceConfig]:
    out: dict[str, SourceConfig] = {}
    for item in items:
        name, _, sensitivity = item.strip().partition("=")
        name = name.strip()
        if not name:
            continue
        level = sensitivity.strip() or KNOWN_SOURCES.get(name, "mixed")
        if level not in ("work", "mixed", "private"):
            raise DocketError(f"source {name}: sensitivity is work, mixed or private, not {level!r}")
        out[name] = SourceConfig(sensitivity=level)  # pyright: ignore[reportArgumentType]
    return out


def confirm_private(prompter: Prompter, urls: list[str], given_private: bool, yes: bool) -> None:
    """Each remote Docket can't check anonymously needs the user's word that it is private."""
    for url in urls:
        if given_private:
            continue
        if yes or not prompter.confirm(f"Docket can't check {url} itself. Private?", False):
            raise DocketError(f"can't confirm {url} is private; pass --remote-is-private if it is")


class Wizard:
    def __init__(self, prompter: Prompter, given: Given, visibility: Callable[[str], Visibility] = remote_visibility):
        self.p = prompter
        self.given = given
        self.visibility = visibility

    def _answer(self, value: str | None, question: str, default: str | None) -> str:
        if value is not None:
            return value
        if self.given.yes:
            if default is None:
                raise DocketError(f"{question}: no default; give it as a flag")
            return default
        return self.p.ask(question, default).strip()

    def plan(self) -> Plan:
        g = self.given
        here = Path.cwd()
        repo = toplevel(here)
        default_path = str((repo or here) / "vault")
        path = Path(self._answer(str(g.path) if g.path else None, "Where should the vault live?", default_path))
        path = path.expanduser().resolve()
        around = toplevel(path.parent)
        if around is not None:
            _refuse_tracked(around, path)

        owner = self._answer(g.owner, "Your name", _git_config("user.name") or os.environ.get("USERNAME") or None)
        email_default = _git_config("user.email") or None
        emails = g.emails or [
            e for e in re.split(r"[,\s]+", self._answer(None, "Your email addresses", email_default) or "") if e
        ]
        languages = g.languages or [
            x for x in re.split(r"[,\s]+", self._answer(None, "Language codes you work in, such as en hu", "en")) if x
        ]
        zone = self._answer(g.time_zone, "Your time zone (IANA, such as Europe/Budapest)", local_time_zone())
        machine = machines.validate(self._answer(g.machine, "This machine's name", machines.resolve(None)))
        source_default = ",".join(KNOWN_SOURCES)
        source_items = g.sources or self._answer(
            None, "Sources to enable (name or name=work|mixed|private)", source_default
        ).split(",")
        storage, report = self._storage(path, around, emails)
        try:
            config = VaultConfig(
                storage=storage,
                owner=Owner(name=owner, emails=emails, languages=languages, time_zone=zone),
                primary_machine=machine,
                sources=_parse_sources(source_items),
                never_store=NeverStore(),
            )
        except ValueError as e:
            raise DocketError(str(e)) from e
        if storage == "folder":
            return Plan(path, None, config, machine, None, None, report)
        parent = self._check_dirty(around) if around is not None else None
        remote, new_repo = self._remote()
        return Plan(path, parent, config, machine, remote, new_repo)

    def _storage(self, path: Path, around: Path | None, emails: list[str]) -> tuple[Storage, HostReport | None]:
        """Folder or repo: the host checks set the default, refuse folder mode outright, or warn."""
        g = self.given
        if g.storage not in (None, "folder", "repo"):
            raise DocketError(f"--storage is folder or repo, not {g.storage!r}")
        if around is None:
            if g.storage == "folder":
                raise DocketError(
                    f"{path.parent} isn't inside a git repo; a folder vault needs one (or --storage repo)"
                )
            return "repo", None
        prefix = path.relative_to(around).as_posix() + "/"
        report = host.check(around, prefix, emails, self.visibility)
        blocking = report.blocking()
        for line in blocking:
            self.p.say(f"Folder mode isn't possible here: {line}.")
        if report.shared:
            self.p.say(
                f"{around} looks shared: {', '.join(report.shared)} also commit to it. A folder vault there is seen by "
                "everyone who can read the repo; a repo of its own keeps it apart."
            )
        default = "repo" if blocking or report.shared else "folder"
        question = f"Keep the vault as a folder of {around} (folder), or as a repo of its own (repo)?"
        storage = self._answer(g.storage, question, default)
        if storage not in ("folder", "repo"):
            raise DocketError(f"the vault is a folder or a repo, not {storage!r}")
        if storage == "repo":
            return "repo", None
        if blocking:
            raise DocketError(f"a folder of {around} isn't possible: {'; '.join(blocking)}")
        if report.shared and g.yes and not g.allow_shared:
            raise DocketError(
                f"{around} looks shared ({', '.join(report.shared)}); pass --allow-shared to keep the vault there "
                "anyway, or --storage repo"
            )
        confirm_private(self.p, report.unknown, g.remote_is_private, g.yes)
        return "folder", report

    def _check_dirty(self, parent: Path) -> Path:
        dirty = Git(parent).out("status", "--porcelain").strip()
        if dirty and not self.given.allow_dirty_parent:
            raise DocketError(
                f"{parent} has uncommitted changes. Commit them first, so the vault's arrival is a clean point in its "
                "history (or pass --allow-dirty-parent)."
            )
        return parent

    def _remote(self) -> tuple[str | None, str | None]:
        g = self.given
        default = "none"
        answer = self._answer(g.remote, "A remote: none, a path or URL, or github to create a private repo", default)
        if answer in ("", "none"):
            return None, None
        if answer == "github" or answer.startswith("github:"):
            if not _gh_ready():
                raise DocketError("creating a GitHub repo needs the GitHub CLI (gh), installed and signed in")
            name = answer.partition(":")[2] or self._answer(None, "Name of the new private repo", "docket-vault")
            return None, name
        visibility = self.visibility(answer)
        if visibility == "public":
            raise DocketError(f"{answer} can be read without signing in; a vault never goes to a public repo")
        if visibility == "unknown":
            confirm_private(self.p, [answer], g.remote_is_private, g.yes)
        return answer, None

    def run(self) -> Vault:
        plan = self.plan()
        self._summary(plan)
        if not self.given.yes and not self.p.confirm("Create it?", True):
            raise DocketError("nothing created")
        vault = Vault.init(plan.path, plan.config, plan.machine)
        if plan.host is not None:
            note = host.setup_lfs(plan.host.top, plan.host.lfs)
            if note:
                self.p.say(f"git-lfs: {note}")
            for rel, message in attribute_mismatches(vault.root):
                self.p.say(f"{rel}: {message}")
        elif plan.parent is not None:
            nest_in_parent(plan.parent, plan.path)
        if plan.remote:
            vault.git.run("remote", "add", "origin", plan.remote)
        elif plan.new_github_repo:
            proc = subprocess.run(
                [
                    "gh",
                    "repo",
                    "create",
                    plan.new_github_repo,
                    "--private",
                    "--source",
                    str(plan.path),
                    "--remote",
                    "origin",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            if proc.returncode != 0:
                self.p.say(f"The vault is created, but the GitHub repo isn't: {proc.stderr.strip()}")
        if userconfig.default_vault() is None:
            userconfig.set_default_vault(plan.path)
        self.p.say(f"Created the vault at {plan.path}.")
        return vault

    def _summary(self, plan: Plan) -> None:
        if plan.host is not None:
            rel = plan.path.relative_to(plan.host.top).as_posix()
            where = [
                f"storage:  a folder of {plan.host.top}, on {plan.host.branch}: Docket commits only {rel}/, locally; "
                "your own git moves it",
                *(f"          {line}" for line in plan.host.lines()),
            ]
        else:
            new_repo = f"new private GitHub repo {plan.new_github_repo}" if plan.new_github_repo else "none"
            where = [
                "storage:  a repo of its own",
                f"inside:   {plan.parent} (its .gitignore and .ignore get the vault's lines)"
                if plan.parent
                else "inside:   no other repo",
                f"remote:   {plan.remote or new_repo}",
            ]
        lines = [
            f"vault:    {plan.path}",
            *where,
            f"owner:    {plan.config.owner.name} <{', '.join(plan.config.owner.emails) or 'no email'}>",
            f"time:     {plan.config.owner.time_zone}, languages {', '.join(plan.config.owner.languages)}",
            f"machine:  {plan.machine} (primary)",
            f"sources:  {', '.join(f'{k} ({v.sensitivity})' for k, v in plan.config.sources.items()) or 'none yet'}",
        ]
        self.p.say("\n".join(lines))


def _refuse_tracked(parent: Path, path: Path) -> None:
    rel = path.relative_to(parent).as_posix()
    if Git(parent).out("ls-files", "--", rel).strip():
        raise DocketError(f"{parent} already tracks files under {rel}/; pick a new folder name for the vault")


def nest_in_parent(parent: Path, vault: Path) -> None:
    """Keep the parent repo out of the vault, but let search tools (ripgrep, Claude Code, editors) see its processed
    docs: a folder the .gitignore excludes can only be re-included through .ignore with these three lines."""
    rel = vault.relative_to(parent).as_posix()
    _append_lines(parent / ".gitignore", [f"/{rel}/"])
    _append_lines(parent / ".ignore", [f"!/{rel}/", f"/{rel}/*", f"!/{rel}/processed/"])


def _append_lines(path: Path, lines: list[str]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    have = set(existing.splitlines())
    missing = [line for line in lines if line not in have]
    if not missing:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(prefix + "".join(f"{line}\n" for line in missing))
