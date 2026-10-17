"""Checks of the user's repo a folder vault lives in (the host), run by init and the convert before a vault goes there:
who can read its remotes, who else commits to it, whether its ignore rules would hide the vault, its state, and
git-lfs. Nothing here writes to the host except `setup_lfs`, after the user's yes."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from docket.childproc import console_flags
from docket.vault.git import Git
from docket.vault.vault import ignored

Visibility = Literal["public", "unknown", "local"]
# How far back the history is read for other people's commits.
COMMITTERS_LOOKBACK = 1000
# Addresses that commit on everyone's behalf: GitHub's web merges, and bots.
_NOT_PEOPLE = re.compile(r"^noreply@github\.com$|\[bot\]@")
_SSH = re.compile(r"^ssh://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+)$")
_SCP = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]+):(?!//)(?P<path>.+)$")


def probe_url(url: str) -> str:
    """The URL an anonymous check reads: an SSH remote's host asked over https, where hosting sites answer for a
    public repo; anything else as it is."""
    for pattern in (_SSH, _SCP):
        m = pattern.match(url)
        if m:
            return f"https://{m.group('host')}/{m.group('path')}"
    return url


def is_local(url: str) -> bool:
    return bool(
        Path(url).exists() or re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith(("/", "./", "../", "file://"))
    )


def remote_visibility(url: str) -> Visibility:
    """Whether anyone can read a remote: an anonymous `git ls-remote` that works means the repo is public. It runs
    outside any repo with an empty git config, so the user's own URL rewrites (insteadOf) and credentials never reach
    it. Fails closed: anything that can't be checked anonymously is "unknown", and the user has to say it's private."""
    if is_local(url):
        return "local"
    with tempfile.TemporaryDirectory(prefix="docket-probe-") as empty:
        config = Path(empty) / "gitconfig"
        config.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": str(config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(Path(empty).parent),
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
        }
        try:
            proc = subprocess.run(
                ["git", "-c", "credential.helper=", "ls-remote", "--heads", probe_url(url)],
                cwd=empty,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env=env,
                timeout=30,
                creationflags=console_flags(),
            )
        except subprocess.TimeoutExpired:
            return "unknown"
    return "public" if proc.returncode == 0 else "unknown"


def remote_urls(top: Path) -> list[str]:
    """Every fetch and push URL of every remote, as git will use them: `get-url` expands insteadOf and
    pushInsteadOf."""
    git = Git(top)
    urls: list[str] = []
    for name in git.out("remote").split():
        for flags in (("--all",), ("--push", "--all")):
            for url in git.run("remote", "get-url", *flags, name, check=False).stdout.splitlines():
                if url and url not in urls:
                    urls.append(url)
    return urls


def others(top: Path, own: Iterable[str]) -> list[str]:
    """Who else committed to the host lately: author and committer addresses that aren't the user's."""
    git = Git(top)
    mine = {e.lower() for e in own}
    mine.add(git.run("config", "user.email", check=False).stdout.strip().lower())
    out = git.run("log", f"-n{COMMITTERS_LOOKBACK}", "--format=%aE%n%cE", "HEAD", check=False).stdout
    found = {line.strip().lower() for line in out.splitlines() if line.strip()}
    return sorted(e for e in found if e not in mine and not _NOT_PEOPLE.search(e))


def state_problems(top: Path) -> list[str]:
    """What stops Docket from committing to the host now: no commit yet, no branch, an operation in progress."""
    git = Git(top)
    if git.run("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
        return [f"{top} has no commit yet; make one first"]
    problems: list[str] = []
    if git.run("symbolic-ref", "-q", "HEAD", check=False).returncode != 0:
        problems.append(f"{top} isn't on a branch (a detached HEAD)")
    git_dir = Path(git.out("rev-parse", "--absolute-git-dir").strip())
    for name, what in (
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("MERGE_HEAD", "a merge"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
        ("REVERT_HEAD", "a revert"),
    ):
        if (git_dir / name).exists():
            problems.append(f"{what} is in progress in {top}")
    return problems


@dataclass(frozen=True)
class Lfs:
    installed: bool
    filter_set: bool
    # in-tree: the repo's hooks live in its own tree (core.hooksPath, such as husky's .husky), as tracked files.
    hook: Literal["ok", "missing", "foreign", "in-tree"]

    def summary(self) -> str:
        if not self.installed:
            return "not installed: media waits until it is (https://git-lfs.com)"
        if self.filter_set and self.hook == "ok":
            return "set up"
        if self.hook == "foreign":
            return 'your own pre-push hook is there: add `git lfs pre-push "$@"` to it, so your push carries media'
        if self.hook == "in-tree":
            return (
                "your repo's hooks are files in its tree (core.hooksPath). Docket doesn't install git-lfs's hooks "
                "there, but git-lfs adds them itself when its filter runs: commit them, or add `git lfs pre-push "
                '"$@"` to your own pre-push hook, so your push carries media'
            )
        return "Docket runs `git lfs install --local` in the repo, so your push carries media"


def hooks_dir(top: Path) -> tuple[Path, bool]:
    """The repo's hooks folder, and whether it is in its own tree (core.hooksPath, such as husky's .husky), where
    every hook is a file the user tracks."""
    git = Git(top)
    hooks = Path(git.out("rev-parse", "--path-format=absolute", "--git-path", "hooks").strip()).resolve()
    git_dir = Path(git.out("rev-parse", "--absolute-git-dir").strip()).resolve()
    return hooks, hooks.is_relative_to(top.resolve()) and not hooks.is_relative_to(git_dir)


def new_hooks(top: Path) -> list[str]:
    """Files git-lfs left in a hooks folder in the repo's own tree, untracked or changed, as git names them."""
    hooks, in_tree = hooks_dir(top)
    if not in_tree:
        return []
    out = Git(top).out("status", "--porcelain", "-z", "--untracked-files=all", "--", str(hooks))
    return sorted(entry[3:] for entry in out.split("\0") if entry)


def lfs_state(top: Path) -> Lfs:
    git = Git(top)
    installed = shutil.which("git-lfs") is not None
    filter_set = bool(git.run("config", "--get", "filter.lfs.process", check=False).stdout.strip())
    hooks, in_tree = hooks_dir(top)
    pre_push = hooks / "pre-push"
    text = pre_push.read_text(encoding="utf-8", errors="replace") if pre_push.is_file() else ""
    if "git lfs" in text or "git-lfs" in text:
        return Lfs(installed, filter_set, "ok")
    if in_tree:
        return Lfs(installed, filter_set, "in-tree")
    return Lfs(installed, filter_set, "foreign" if text else "missing")


def setup_lfs(top: Path, lfs: Lfs) -> str | None:
    """After the user's yes: set git-lfs up in the host when its filter or hook is missing. What the user still has
    to do, or None. For hooks that are files in the repo's own tree only the filter is set; git-lfs may still add
    its hooks there when the filter runs, which `new_hooks` finds."""
    if not lfs.installed:
        return lfs.summary()
    if lfs.filter_set and lfs.hook == "ok":
        return None
    install = ["lfs", "install", "--local", *(["--skip-repo"] if lfs.hook == "in-tree" else [])]
    done = Git(top).run(*install, check=False).returncode == 0
    if lfs.hook in ("foreign", "in-tree"):
        return lfs.summary()
    return None if done else f"`git lfs install --local` failed in {top}; run it there yourself"


def same_rule(line: str, rule: str) -> bool:
    """Whether a .gitignore line is this rule, as git reads it: surrounding spaces don't count."""
    return line.strip() == rule.strip()


def ignored_without(top: Path, prefix: str, line: str) -> list[tuple[str, str, str]]:
    """The vault's paths the host's ignore rules hide, read as if `line` (the vault's own /<vault>/ line, which the
    convert takes out) weren't in the host's top .gitignore: while it is there it hides every other rule. The rules
    are copied into a scratch repo, so nothing of the host's changes; the host's own excludesFile is passed on."""
    top_git = Git(top)
    git_dir = Path(top_git.out("rev-parse", "--absolute-git-dir").strip())
    excludes = top_git.run("config", "--path", "--get", "core.excludesFile", check=False).stdout.strip()
    # A relative excludesFile is read from the repo's top; the scratch repo needs it whole.
    config = ("-c", f"core.excludesFile={(top / excludes).as_posix()}") if excludes else ()
    with tempfile.TemporaryDirectory(prefix="docket-ignore-") as scratch:
        shadow = Path(scratch)
        Git(shadow).run("init", "-q")
        parts = prefix.rstrip("/").split("/")
        for depth in range(len(parts) + 1):
            rel = Path(*parts[:depth])
            source = top / rel / ".gitignore"
            if not source.is_file():
                continue
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
            # Blanked rather than dropped, so every other rule keeps its line number in what the user is shown.
            kept = [x if depth or not same_rule(x, line) else "" for x in lines]
            (shadow / rel).mkdir(parents=True, exist_ok=True)
            (shadow / rel / ".gitignore").write_text("".join(f"{x}\n" for x in kept), encoding="utf-8")
        exclude = git_dir / "info" / "exclude"
        if exclude.is_file():
            (shadow / ".git" / "info").mkdir(parents=True, exist_ok=True)
            (shadow / ".git" / "info" / "exclude").write_bytes(exclude.read_bytes())
        return ignored(shadow, prefix, config)


@dataclass
class HostReport:
    top: Path
    branch: str
    public: list[str] = field(default_factory=list[str])
    unknown: list[str] = field(default_factory=list[str])
    shared: list[str] = field(default_factory=list[str])
    ignored: list[str] = field(default_factory=list[str])
    state: list[str] = field(default_factory=list[str])
    lfs: Lfs = field(default_factory=lambda: Lfs(False, False, "missing"))

    def blocking(self) -> list[str]:
        """What refuses folder mode outright."""
        public = [f"{url} can be read without signing in; a vault never goes to a public repo" for url in self.public]
        hidden = [f"the repo's ignore rules hide {line}" for line in self.ignored]
        return [*public, *hidden, *self.state]

    def lines(self) -> list[str]:
        """The checks' results for a summary or a dry run."""
        remotes = ", ".join(
            [
                *(f"{u} (public)" for u in self.public),
                *(f"{u} (can't tell; you confirm it's private)" for u in self.unknown),
            ]
        )
        return [
            f"remotes:  {remotes or 'none public'}",
            f"shared:   {', '.join(self.shared) if self.shared else 'no other committers'}",
            f"git-lfs:  {self.lfs.summary()}",
        ]


def check(
    top: Path,
    prefix: str,
    own_emails: Iterable[str],
    visibility: Callable[[str], Visibility] = remote_visibility,
    without: str | None = None,
) -> HostReport:
    """Every check of the host for a vault at `prefix` (its path in the host, with a trailing slash). With `without`
    (the convert's own /<vault>/ line, which it takes out), the ignore rules are read as if that line were gone."""
    git = Git(top)
    branch = git.run("symbolic-ref", "-q", "--short", "HEAD", check=False).stdout.strip()
    report = HostReport(top, branch or "(no branch)", lfs=lfs_state(top))
    for url in remote_urls(top):
        seen = visibility(url)
        if seen == "public":
            report.public.append(url)
        elif seen == "unknown":
            report.unknown.append(url)
    report.shared = others(top, own_emails)
    hidden = ignored(top, prefix) if without is None else ignored_without(top, prefix, without)
    report.ignored = [f"{path} ({rule})" for path, rule, _ in hidden]
    report.state = state_problems(top)
    return report
