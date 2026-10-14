"""`docket init`: the questions that set up a vault, then `Vault.init`.

Every answer can also come from a flag, so `docket init --yes --path ... --owner ...` runs unattended. Nothing is
written before the summary is confirmed.
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
from docket.vault import machine as machines
from docket.vault.config import NeverStore, Owner, SourceConfig, VaultConfig
from docket.vault.git import Git, toplevel
from docket.vault.vault import Vault

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

Visibility = Literal["public", "unknown", "local"]


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
    remote: str | None = None
    remote_is_private: bool = False
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


def _git_config(key: str) -> str:
    proc = subprocess.run(["git", "config", key], capture_output=True, text=True, encoding="utf-8", check=False)
    return proc.stdout.strip()


def local_time_zone() -> str | None:
    try:
        from tzlocal import get_localzone_name  # noqa: PLC0415 - only the wizard needs it

        return get_localzone_name()
    except Exception:
        return None


def remote_visibility(url: str) -> Visibility:
    """Whether anyone can read a remote: an anonymous `git ls-remote` that works means the repo is public. Fails
    closed: anything that can't be checked anonymously is "unknown", and the user has to say it's private."""
    if Path(url).exists() or re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith(("/", "./", "../", "file://")):
        return "local"
    if url.startswith(("git@", "ssh://")) or re.match(r"^[\w.-]+@[\w.-]+:", url):
        return "unknown"
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
    }
    try:
        proc = subprocess.run(
            ["git", "-c", "credential.helper=", "ls-remote", "--heads", url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env=env,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "unknown"
    return "public" if proc.returncode == 0 else "unknown"


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
        parent = self._check_parent(path)

        owner = self._answer(g.owner, "Your name", _git_config("user.name") or os.environ.get("USERNAME") or None)
        email_default = _git_config("user.email") or None
        emails = g.emails or [
            e for e in re.split(r"[,\s]+", self._answer(None, "Your email addresses", email_default) or "") if e
        ]
        languages = g.languages or [
            x for x in re.split(r"[,\s]+", self._answer(None, "Languages you work in", "en")) if x
        ]
        zone = self._answer(g.time_zone, "Your time zone (IANA, such as Europe/Budapest)", local_time_zone())
        machine = machines.validate(self._answer(g.machine, "This machine's name", machines.resolve(None)))
        source_default = ",".join(KNOWN_SOURCES)
        source_items = g.sources or self._answer(
            None, "Sources to enable (name or name=work|mixed|private)", source_default
        ).split(",")
        try:
            config = VaultConfig(
                owner=Owner(name=owner, emails=emails, languages=languages, time_zone=zone),
                primary_machine=machine,
                sources=_parse_sources(source_items),
                never_store=NeverStore(),
            )
        except ValueError as e:
            raise DocketError(str(e)) from e
        remote, new_repo = self._remote()
        return Plan(path, parent, config, machine, remote, new_repo)

    def _check_parent(self, path: Path) -> Path | None:
        parent = toplevel(path.parent)
        if parent is None or parent == path:
            return None
        parent_git = Git(parent)
        rel = path.relative_to(parent).as_posix()
        if parent_git.out("ls-files", "--", rel).strip():
            raise DocketError(f"{parent} already tracks files under {rel}/; pick a new folder name for the vault")
        dirty = parent_git.out("status", "--porcelain").strip()
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
        unconfirmed = visibility == "unknown" and not g.remote_is_private
        if unconfirmed and (g.yes or not self.p.confirm(f"Docket can't check {answer} itself. Private?", False)):
            raise DocketError(f"can't confirm {answer} is private; pass --remote-is-private if it is")
        return answer, None

    def run(self) -> Vault:
        plan = self.plan()
        self._summary(plan)
        if not self.given.yes and not self.p.confirm("Create it?", True):
            raise DocketError("nothing created")
        vault = Vault.init(plan.path, plan.config, plan.machine)
        if plan.parent is not None:
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
        new_repo = f"new private GitHub repo {plan.new_github_repo}" if plan.new_github_repo else "none"
        lines = [
            f"vault:    {plan.path}",
            f"inside:   {plan.parent} (its .gitignore and .ignore get the vault's lines)"
            if plan.parent
            else "inside:   no other repo",
            f"owner:    {plan.config.owner.name} <{', '.join(plan.config.owner.emails) or 'no email'}>",
            f"time:     {plan.config.owner.time_zone}, languages {', '.join(plan.config.owner.languages)}",
            f"machine:  {plan.machine} (primary)",
            f"sources:  {', '.join(f'{k} ({v.sensitivity})' for k, v in plan.config.sources.items()) or 'none yet'}",
            f"remote:   {plan.remote or new_repo}",
        ]
        self.p.say("\n".join(lines))


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
