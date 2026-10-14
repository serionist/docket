"""Commit messages and PR titles: what lands on main says what changed, and carries no tool attribution."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from devkit.repo import git
from devkit.yamlio import as_map

# Lines a tool adds to say it wrote the change. main carries none of them: the history says what changed and why.
ATTRIBUTION = [
    re.compile(r"^\s*co-authored-by:.*\b(claude|anthropic|copilot|cursor|codex|gemini|devin|aider)\b", re.I),
    re.compile(r"generated (with|by) .*\b(claude|copilot|cursor|codex|gemini|chatgpt|aider)\b", re.I),
    re.compile(r"\bclaude\.(ai|com)/(code|chat)\b", re.I),
    re.compile("\N{ROBOT FACE}"),
]
SUBJECT_MAX = 72


def attribution_lines(message: str) -> list[str]:
    return [ln for ln in message.split("\n") if any(p.search(ln) for p in ATTRIBUTION)]


def strip_attribution(message: str) -> str:
    """The message without attribution lines, and without the blank lines they leave at the end."""
    kept = [ln for ln in message.split("\n") if not any(p.search(ln) for p in ATTRIBUTION)]
    while kept and kept[-1].strip() == "":
        kept.pop()
    return "\n".join(kept) + "\n"


def title_problems(title: str) -> list[str]:
    """What's wrong with a PR title, which becomes the commit subject on main."""
    out: list[str] = []
    t = title.strip()
    if not t:
        return ["is empty"]
    if len(t) > SUBJECT_MAX:
        out.append(f"is {len(t)} characters; keep it to {SUBJECT_MAX}")
    if t.endswith("."):
        out.append("ends with a full stop")
    if not t[0].isupper():
        out.append("should start with a capital letter, as a plain imperative sentence")
    if re.match(r"^(wip|fixup!|squash!|draft)\b", t, re.I):
        out.append("is a work-in-progress title")
    return out


@dataclass
class PullRequest:
    title: str
    body: str
    base_sha: str


def pull_request() -> PullRequest | None:
    """The pull request CI runs for, read from GitHub's event file; None anywhere else."""
    if os.environ.get("GITHUB_EVENT_NAME") not in ("pull_request", "pull_request_target"):
        return None
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).is_file():
        return None
    event = as_map(json.loads(Path(path).read_text(encoding="utf-8")))
    pr = as_map(event.get("pull_request"))
    return PullRequest(
        title=str(pr.get("title") or ""),
        body=str(pr.get("body") or ""),
        base_sha=str(as_map(pr.get("base")).get("sha") or ""),
    )


def branch_messages(root: Path, base: str | None) -> list[tuple[str, str]]:
    """(sha, message) of each non-merge commit between base and HEAD."""
    if base is None:
        return []
    out = git(root, "log", "--no-merges", "--format=%H%x00%B%x01", f"{base}..HEAD")
    pairs: list[tuple[str, str]] = []
    for raw in out.split("\x01"):
        chunk = raw.strip("\n")
        if "\x00" in chunk:
            sha, _, msg = chunk.partition("\x00")
            pairs.append((sha.strip(), msg))
    return pairs
