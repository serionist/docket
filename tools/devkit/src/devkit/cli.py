"""`uv run devkit`: setting up a clone, and the commit-msg hook."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from devkit import commits, content, pins
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


# GitHub's Projects API answers this now and then and succeeds on the next try.
TRANSIENT = ("Something went wrong while executing your query", "502 Bad Gateway", "secondary rate limit")
RETRY_WAITS = (2.0, 5.0, 10.0)


def _gh(*args: str) -> str:
    for wait in (*RETRY_WAITS, None):
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
        if proc.returncode == 0:
            return proc.stdout
        message = proc.stderr.strip() or proc.stdout.strip()
        if wait is None or not any(t in message for t in TRANSIENT):
            raise KitError(f"gh {' '.join(args[:3])}: {message}")
        time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


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


# The models' worker environments' requirements (DR-0332), compiled with hashes from versions Dependabot's cooldown
# would take: at least a week old (DR-0157).
MODELS = pins.MODELS
COOLDOWN_DAYS = 7
# Each round leaves out at least one release that lacks a wheel, so this bounds a resolution that never settles.
LOCK_ROUNDS = 20
WHEELS_COMMENT = (
    "Each pin of the models' requirements files and its files by hash, from the same resolution, written by "
    "`uv run devkit models-lock`, never by hand. The gate's pins check holds each compiled file's hashes to these and "
    "each pin to a wheel for every system its marker names (#109)."
)


def _today() -> date:
    return date.today()


def _compile(source: Path, cutoff: str, constraints: Sequence[Path], out: Path) -> None:
    held = [a for c in constraints for a in ("-c", str(c))]
    proc = subprocess.run(
        [
            "uv", "pip", "compile", str(source), "--universal", "--generate-hashes", "--python-version", "3.12",
            "--exclude-newer", cutoff, "--no-header", "--no-annotate", *held, "-o", str(out),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )  # fmt: skip
    if proc.returncode != 0:
        raise KitError(f"uv pip compile {source.name}: {proc.stderr.strip() or proc.stdout.strip()}")


def _lock_one(source: Path, cutoff: str) -> dict[str, dict[str, str]]:
    """Compile one requirements file, leaving out each release that has no wheel for a system its marker names until
    every pin has one, and return each pin's files by sha256 from a pylock.toml of the same resolution."""
    target = source.with_suffix(".txt")
    left_out: list[str] = []
    with tempfile.TemporaryDirectory(prefix="docket-models-lock-") as tmp:
        constraints, lock = Path(tmp) / "constraints.txt", Path(tmp) / "pylock.models.toml"
        for _ in range(LOCK_ROUNDS):
            constraints.write_text("".join(f"{c}\n" for c in left_out), encoding="utf-8")
            # uv keeps an output file's pins where it can, so the .txt moves only as far as it must; the pylock, a new
            # file each round, is held to the .txt's pins to describe the same resolution.
            _compile(source, cutoff, [constraints], target)
            _compile(source, cutoff, [constraints, target], lock)
            files = pins.lock_files(lock.read_text(encoding="utf-8"))
            pinned = pins.compiled(target.read_text(encoding="utf-8"))
            apart = sorted({p.key for p in pinned} ^ set(files))
            if apart:
                raise KitError(f"{source.name}: the requirements and the pylock compiles differ on {', '.join(apart)}")
            short = [(p, s) for p in pinned for s in pins.missing_systems(p, files[p.key])]
            if not short:
                return {p.key: files[p.key] for p in pinned}
            for pin, system in short:
                print(f"{source.name}: {pin.name} {pin.version} has no wheel for {system}, so it is left out")
            left_out += sorted({f"{p.name}!={p.version}" for p, _ in short})
    raise KitError(f"{source.name}: a pin still lacks a wheel for a system after {LOCK_ROUNDS} rounds")


def cmd_models_lock(_: argparse.Namespace) -> int:
    """Compile each requirements-<device>.in beside the models' worker into its .txt, with hashes, for every system,
    and write each pin's files to tools/devkit/models-wheels.json for the gate's pins check."""
    root = repo_root()
    cutoff = (_today() - timedelta(days=COOLDOWN_DAYS)).isoformat()
    sources = sorted((root / MODELS).glob("requirements-*.in"))
    if not sources:
        raise KitError(f"no requirements-*.in in {MODELS}")
    files: dict[str, object] = {"$comment": WHEELS_COMMENT}
    for source in sources:
        files[source.stem.removeprefix("requirements-")] = _lock_one(source, cutoff)
        print(f"{source.with_suffix('.txt').relative_to(root).as_posix()}: from versions published before {cutoff}")
    (root / pins.WHEELS).write_text(json.dumps(files, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"{pins.WHEELS}: each pin's files, for the gate's pins check")
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
    sub.add_parser(
        "models-lock", help="compile the models' worker requirements with hashes, from versions a week old"
    ).set_defaults(func=cmd_models_lock)
    args = p.parse_args(argv)
    try:
        return int(args.func(args))
    except KitError as e:
        print(f"devkit: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
