"""`uv run devkit`: setting up a clone, and the commit-msg hook."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from devkit import commits, content
from devkit.repo import KitError, git, repo_root, utf8_stdio
from devkit.yamlio import as_list, as_map

# The board Docket's work is tracked on (AGENTS.md, How work runs).
BOARD_OWNER = "serionist"
BOARD_NUMBER = 3


def cmd_setup(_: argparse.Namespace) -> int:
    root = repo_root()
    git(root, "config", "core.hooksPath", ".githooks")
    print("hooks: git uses .githooks/ (commit-msg strips tool attribution, pre-push runs `gate quick`)")
    missing = False
    for tool, why, how in (
        ("gitleaks", "the gate's secret scan", "winget install gitleaks / brew install gitleaks"),
        ("claude", "the full gate validates the plugin with Claude Code", "https://claude.com/claude-code"),
    ):
        found = shutil.which(tool)
        print(f"{tool}: {'found' if found else 'MISSING'} ({why}){'' if found else ': ' + how}")
        missing = missing or not found
    deny = content.list_path()
    if deny is None:
        print(f"deny list: none at {content.DEFAULT_PATH}; the content check skips locally (maintainers: add yours)")
    else:
        names = content.load(deny) if deny.is_file() else []
        print(f"deny list: {deny} ({len(names)} names)")
    return 1 if missing else 0


def cmd_commit_msg(args: argparse.Namespace) -> int:
    """Strip tool attribution from a commit message before git records it."""
    path = Path(args.file)
    message = path.read_text(encoding="utf-8")
    found = commits.attribution_lines(message)
    if found:
        path.write_text(commits.strip_attribution(message), encoding="utf-8", newline="\n")
        print(f"commit-msg: removed {len(found)} tool attribution line(s); main carries none", file=sys.stderr)
    return 0


def _gh(*args: str) -> str:
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if proc.returncode != 0:
        raise KitError(f"gh {' '.join(args[:3])}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def cmd_board(args: argparse.Namespace) -> int:
    """Put an issue on the board and set its fields; the phase defaults to the issue's milestone."""
    if shutil.which("gh") is None:
        raise KitError("the GitHub CLI (gh) isn't on PATH")
    issue = as_map(json.loads(_gh("issue", "view", str(args.issue), "--json", "url,milestone,title")))
    owner, number = BOARD_OWNER, str(BOARD_NUMBER)
    item = _gh("project", "item-add", number, "--owner", owner, "--url", str(issue["url"]), "--format", "json")
    item_id = str(as_map(json.loads(item)).get("id"))
    project_id = str(as_map(json.loads(_gh("project", "view", number, "--owner", owner, "--format", "json"))).get("id"))
    fields = {
        str(f.get("name")): f
        for f in (
            as_map(x)
            for x in as_list(
                as_map(json.loads(_gh("project", "field-list", number, "--owner", owner, "--format", "json"))).get(
                    "fields"
                )
            )
        )
    }
    wanted: dict[str, str] = {}
    if args.status:
        wanted["Status"] = args.status
    phase = args.phase
    if phase is None:
        m = re.match(r"Phase (\d+)", str(as_map(issue.get("milestone")).get("title") or ""))
        phase = m.group(1) if m else None
    if phase is not None:
        wanted["Phase"] = str(phase)
    if args.size:
        wanted["Size"] = args.size
    for name, value in wanted.items():
        field = fields.get(name)
        if field is None:
            raise KitError(f"the board has no field {name!r}")
        options = {str(as_map(o).get("name")): str(as_map(o).get("id")) for o in as_list(field.get("options"))}
        if value not in options:
            raise KitError(f"{name} has no option {value!r}; it has {', '.join(options)}")
        _gh(
            "project", "item-edit", "--id", item_id, "--project-id", project_id,
            "--field-id", str(field.get("id")), "--single-select-option-id", options[value],
        )  # fmt: skip
    shown = ", ".join(f"{k} {v}" for k, v in wanted.items()) or "added"
    print(f"#{args.issue} {issue.get('title')}: {shown}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    utf8_stdio()
    p = argparse.ArgumentParser(prog="devkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="point git at the hooks and check the tools the gate needs").set_defaults(
        func=cmd_setup
    )
    s = sub.add_parser("commit-msg", help="the commit-msg hook")
    s.add_argument("file")
    s.set_defaults(func=cmd_commit_msg)
    s = sub.add_parser("board", help="put an issue on the Docket board and set its status, phase and size")
    s.add_argument("issue", type=int)
    s.add_argument("--status", choices=["Todo", "In Progress", "Done"])
    s.add_argument("--phase", choices=[str(n) for n in range(6)])
    s.add_argument("--size", choices=["S", "M", "L"])
    s.set_defaults(func=cmd_board)
    args = p.parse_args(argv)
    try:
        return int(args.func(args))
    except KitError as e:
        print(f"devkit: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
