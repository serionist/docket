"""Builders the app's tests share: invented configs and records, a fixture vault, docs, and a command runner."""

from __future__ import annotations

import contextlib
import gc
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, overload

import pytest
from filelock import FileLock

from docket import osprompt
from docket.errors import DocketError
from docket.vault import forgotten, inforce, yamledit
from docket.vault.config import CONFIG_FILE, NeverStore, Owner, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.home import Home
from docket.vault.records import RawRecord, version
from docket.vault.vault import Vault, remove_tree

# The tests' global git config: empty but for git's own housekeeping after a commit, a fetch or a push (maintenance,
# and gc --auto where a git still runs it), which never finds work worth doing in a test repo and, run in the
# background, could repack a template while a test copies it (DR-0221).
# The files ref format pinned, so a git that defaults to reftable (Git 3.0) can't change which git calls a test sees.
TEST_GITCONFIG = "[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n[init]\n\tdefaultRefFormat = files\n"
# The session's own folder, which conftest.py makes before any fixture runs and names here.
HOME_VAR = "DOCKET_TEST_HOME"

# The fixture vault's records: a call over an hour long, a recording in a vendor's own shape, a chat thread, two
# side messages, and a mail whose rid holds @, % and ].
REC = "calls:rec-0042"
VENDOR = "vendor:rec-0007"
THREAD = [f"chat:C0TEST/{1791000000 + n}.000100" for n in (1, 2, 3)]
SIDE = [f"chat:C0TEST/{1791000000 + n}.000100" for n in (4, 5)]
MAIL = "mail:<50%off]x@example.invalid>"
CALL_SEGMENTS: list[dict[str, Any]] = [
    {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Morning, Cleo."},
    {"start_ms": 3000, "end_ms": 9000, "speaker": "Cleo Test", "text": "Morning. Shall we start with the consultant?"},
    # Nobody speaks from 0:09 to 0:20.
    {"start_ms": 20000, "end_ms": 35000, "speaker": "Ben Sample", "text": "The consultant is 10 million forints."},
    {"start_ms": 728000, "end_ms": 740500, "speaker": "Ben Sample", "text": "So ten million, for the whole quarter."},
    {
        "start_ms": 870000,
        "end_ms": 892000,
        "speaker": "Cleo Test",
        "text": "Please send the offer in writing by Friday.",
    },
    # The last to start isn't the last to end: the call runs to 1:02:10.
    {"start_ms": 3690000, "end_ms": 3730000, "speaker": "Cleo Test", "text": "Thanks both, talking over the goodbyes."},
    {"start_ms": 3700000, "end_ms": 3725000, "speaker": "Ben Sample", "text": "Bye for now."},
]
MAIL_TEXT = "Hello Ben,\r\n\r\nThe offer stands at 10 million.\r\nUntil Friday.\r\n \N{NO-BREAK SPACE}\r\nCleo\r\n"


class AtConsole:
    """The owner at the console: answers each question from a script, typing back the code when the script says
    "code"; every prompt is kept. The `owner` fixture puts one in docket.console's place."""

    def __init__(self, *answers: str | Callable[[], str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if self.answers else "q"
        if callable(answer):
            answer = answer()
        if answer == "code":
            found = re.search(r"Type ([A-Z]{4}) to", prompt)
            assert found is not None, prompt
            return found.group(1).lower()
        return answer


class FakePrompt:
    """An OS prompt set up on this machine, for the `os_prompt` fixture: answers each question from the script given,
    Verified() or Refused(why), and keeps each act it was asked about. Past the script it refuses."""

    def __init__(self, *answers: osprompt.Answer) -> None:
        self.answers = list(answers)
        self.acts: list[str] = []

    def __call__(self, act: str) -> osprompt.Answer:
        self.acts.append(act)
        return self.answers.pop(0) if self.answers else osprompt.Refused("the script ran out")


def sh(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)
    if check and proc.returncode != 0:
        raise AssertionError(f"{args}: {proc.stderr or proc.stdout}")
    return proc.stdout


def git_command(args: list[str]) -> str:
    """A git call as a call list shows it: what follows the -c settings in front, with a commit's message left out."""
    rest = [str(a) for a in args[1:]]
    while rest[:1] == ["-c"]:
        del rest[:2]
    if "-m" in rest:
        del rest[rest.index("-m") + 1]
    return " ".join(rest)


# What every commit of Docket's asks git, as `git_calls` lists it: run files git holds but HEAD doesn't, unmerged paths,
# then the add, the staged check, git's identity (once per opened vault) and the commit. The new commit's id is read
# from the branch's ref file (DR-0528 to DR-0530).
COMMIT_CALLS = [
    "status --porcelain=v1 -z --untracked-files=all --ignored=traditional -- raw",
    "ls-files -u",
    "add --pathspec-from-file=- --pathspec-file-nul",
    "diff --cached --name-only -z -- .",
    "config --get-regexp ^user[.](name|email)$",
    "commit -q --only --pathspec-from-file=- --pathspec-file-nul -m",
]


@contextmanager
def git_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The git commands the block starts, in order, as `git_command` shows them (#132)."""
    calls: list[str] = []
    real = subprocess.run

    def recording(args: Any, *rest: Any, **kwargs: Any) -> Any:
        if isinstance(args, list) and args and Path(str(args[0])).stem == "git":
            calls.append(git_command(args))
        return real(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording)
    try:
        yield calls
    finally:
        monkeypatch.setattr(subprocess, "run", real)


def cant_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """vault.yaml's in-place editor finds no place for any change, as for a file with an alias in it: the change is
    left to the user, with its line (DR-0602)."""
    monkeypatch.setattr(yamledit, "patch", lambda text, before, after: None)


def make_config(storage: Literal["repo", "folder"] = "repo", **owner: Any) -> VaultConfig:
    return VaultConfig(
        storage=storage,
        owner=Owner(
            name=owner.get("name", "Ada Example"),
            emails=owner.get("emails", ["ada@example.invalid"]),
            languages=["en", "hu"],
            time_zone="Europe/Budapest",
        ),
        primary_machine="test-box",
        sources={"chat": SourceConfig(), "calls": SourceConfig(sensitivity="mixed")},
    )


def make_record(n: int = 1, **fields: Any) -> RawRecord:
    base: dict[str, Any] = {
        "rid": f"chat:C0TEST/{1791000000 + n}.000100",
        "source": "chat",
        "kind": "message",
        "container": "chat:C0TEST",
        "ts": datetime(2026, 10, 14, 9, 30, n % 60, tzinfo=UTC),
        "author": "U0ADA",
        "text": f"message {n}",
        "fetched": datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
        "via": "test",
    }
    base.update(fields)
    return RawRecord.model_validate(base)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 14, hour, minute, tzinfo=UTC)


# A recording kept as Plaud's mapper keeps one: the recording with its title, its transcript with the segments and no
# title, and its notes, three records whose thread and container are the recording's rid.
PLAUD = "plaud:of_00feed0000feed0000feed0000feed00"
POLISHED = f"{PLAUD}/transaction_polish"
SAID: list[dict[str, Any]] = [
    {"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": "Morning, Cleo."},
    {"start_ms": 642_000, "end_ms": 650_000, "speaker": "Cleo Test", "text": "Ten million, signed by Friday."},
    {"start_ms": 900_000, "end_ms": 905_000, "speaker": "Ben Sample", "text": "Thanks, bye."},
]


def plaud_records(*blocks: str, title: str = "Supplier sync") -> list[RawRecord]:
    """The recording, a transcript per block and its notes, on 14 October."""
    under = {"source": "plaud", "container": PLAUD, "thread": PLAUD, "author": None}
    said = "\n".join(f"{s['speaker']}: {s['text']}" for s in SAID)
    return [
        make_record(rid=PLAUD, kind="recording", title=title, text="", **under),
        *(make_record(rid=f"{PLAUD}/{b}", kind="transcript", segments=SAID, text=said, **under) for b in blocks),
        make_record(rid=f"{PLAUD}/summary", kind="vendor-summary", text="## Summary\n\nPrice agreed.", **under),
    ]


def consuming_project(tmp_path: Path, mappers: dict[str, str]) -> Vault:
    """A git repo with a mapper per source under .claude/skills/sources/, and a vault nested in it whose vault.yaml
    names each mapper; each source is mixed."""
    project = tmp_path / "project"
    project.mkdir()
    sh(project, "git", "init", "-q", "-b", "main")
    config = make_config()
    for source, code in mappers.items():
        write_mapper(project, source, code)
        config.sources[source] = SourceConfig(sensitivity="mixed", mapper=f".claude/skills/sources/{source}/mapper.py")
    return Vault.init(project / "vault", config, "test-box")


def write_mapper(project: Path, source: str, code: str) -> Path:
    path = project / ".claude" / "skills" / "sources" / source / "mapper.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8", newline="\n")
    return path


def fixture_runs() -> list[tuple[str, str, list[RawRecord]]]:
    """The fixture's raw log as (source, run, records): one run per source at 10:00, and an edit at 11:00."""
    call = make_record(
        rid=REC, source="calls", kind="recording", container=None, title="Supplier call", text="Supplier call",
        segments=CALL_SEGMENTS,
    )  # fmt: skip
    # A recording whose transcript starts a second and a half in: a vendor's own shape, mapped to the core's.
    utterances = [{"t": 1.5, "w": "hello"}, {"t": 4.0, "w": "the price is fine"}]
    mapped = [{"start_ms": int(u["t"] * 1000), "end_ms": int(u["t"] * 1000) + 500, "text": u["w"]} for u in utterances]
    vendor = make_record(
        rid=VENDOR, source="vendor", kind="recording", container=None, segments=mapped, native={"utt": utterances}
    )
    thread = [make_record(n, thread=THREAD[0], text=f"thread message {n}") for n in (1, 2, 3)]
    side = [make_record(n, container="chat:C0SIDE", text=f"side message {n}") for n in (4, 5)]
    mail = make_record(
        rid=MAIL, source="mail", kind="mail", container="mail:thread-1", title="The offer",
        author="cleo@example.invalid", text=MAIL_TEXT,
    )  # fmt: skip
    edited = make_record(3, thread=THREAD[0], text="thread message 3, edited")
    first = "2026-10-14T1000-test-box"
    return [
        ("calls", first, [call]),
        ("vendor", first, [vendor]),
        ("chat", first, [*thread, *side]),
        ("mail", first, [mail]),
        ("chat", "2026-10-14T1100-test-box", [edited]),
    ]


def write_raw(root: Path) -> None:
    """The fixture's run files under root/raw, written directly: no git, no lock, so a module can share them."""
    for source, run, records in fixture_runs():
        path = root / "raw" / source / "2026" / "10" / f"{run}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [r.model_copy(update={"v": version(r), "run": run}).to_json() for r in records]
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8", newline="\n")


def fixture_vault(tmp_path: Path) -> Vault:
    """A vault with the fixture's raw log committed in one commit."""
    vault = Vault.init(tmp_path / "vault", make_config(), "test-box")
    write_raw(vault.root)
    vault.commit([], "Raw: the fixture")
    return vault


# -- templates built once per session ---------------------------------------------------------------------------------

# Copies of a template are what tests write; a reader's caches in a template are not a write.
_CACHES = re.compile(r"(-wal|-shm|-journal|\.pyc)$")
_FACTORY: list[pytest.TempPathFactory] = []
# The folder the gate names for both pytest runs of a lane without coverage, so the timed run finds the templates the
# parallel run built and only times (DR-0492). The gate makes it empty for each run, so no template outlives the code
# that built it.
TEMPLATES_ENV = "DOCKET_TEST_TEMPLATES"


def use_factory(factory: pytest.TempPathFactory) -> None:
    """conftest.py hands over the session's temp factory, so builders without one of their own find the templates."""
    _FACTORY[:] = [factory]


def shared_root(factory: pytest.TempPathFactory) -> Path:
    """The folder every worker of this session shares: under xdist, the one holding each worker's own base temp."""
    base = factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


def templates_folder(factory: pytest.TempPathFactory) -> Path:
    """Where the session's templates are built: the folder the gate names, or the session's own shared folder."""
    named = os.environ.get(TEMPLATES_ENV)
    return Path(named) if named else shared_root(factory) / "templates"


def digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:12]


def _template(build: Callable[[Path], Path], key: str, factory: pytest.TempPathFactory | None) -> tuple[Path, str]:
    """The folder a template was built in and the path in it `build` returned: built by the first worker to ask, under
    a lock, the tests' isolation and a git home of its own; the others wait and reuse it (DR-0531)."""
    # A readable tail, short enough for Windows' path limit, and the whole key's digest, so two keys never share one.
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", key)[-32:].lstrip(".-") + "-" + digest(key)
    templates = templates_folder(factory or _FACTORY[0])
    templates.mkdir(parents=True, exist_ok=True)
    folder, done = templates / name, templates / f"{name}.done"
    with FileLock(str(templates / f"{name}.lock")):
        if done.is_file():
            return folder, done.read_text(encoding="utf-8")
        # A build that failed part way left what it wrote; the next one starts clean and fails, if it does, on its own.
        if folder.exists():
            remove_tree(folder)
        folder.mkdir()
        with pytest.MonkeyPatch.context() as mp:
            isolated_git(mp, templates / f"{name}.home")
            made = build(folder)
            settle(folder)
            warm(folder)
        (templates / f"{name}.manifest.json").write_text(json.dumps(manifest(folder)), encoding="utf-8")
        rel = made.resolve().relative_to(folder.resolve()).as_posix()
        done.write_text(rel, encoding="utf-8")
        return folder, rel


def shared(build: Callable[[Path], Path], key: str, factory: pytest.TempPathFactory | None = None) -> Path:
    """What `build` makes in an empty folder, built once per session; never write into it, copy it."""
    folder, rel = _template(build, key, factory)
    return folder / rel


def copied(tmp_path: Path, build: Callable[[Path], Path], key: str) -> Path:
    """A copy of a shared template in tmp_path, ready as a fresh build would be: the path `build` returned, there."""
    folder, rel = _template(build, key, None)
    shutil.copytree(folder, tmp_path, dirs_exist_ok=True)
    for repo in _repos(folder):
        _fit(folder, tmp_path / repo.relative_to(folder), tmp_path)
    return tmp_path / rel


def _repos(folder: Path) -> list[Path]:
    """The repos with a work tree in a folder: those with a .git of their own. A bare origin has none."""
    return sorted({p.parent for p in folder.rglob(".git")})


def settle(folder: Path) -> None:
    """Each repo in a template refreshed a second after the build's last write, so no index entry is as new as its index
    (git hashes those again, and hashing media/.gitkeep starts git-lfs's filter), and set to ignore creation times,
    which a copy can't keep; size, modification times and inodes still count (DR-0221)."""
    repos = _repos(folder)
    if not repos:
        return
    time.sleep(1.05)
    for repo in repos:
        sh(repo, "git", "config", "core.trustctime", "false")
        sh(repo, "git", "update-index", "-q", "--refresh", check=False)


def warm(folder: Path) -> None:
    """Each vault in a template read once, as Docket reads one, so the settings in force find HEAD's vault.yaml in
    their cache, and the readers of raw HEAD's forgotten list in theirs, before the manifest is taken: a test that opens
    the template itself then writes nothing into it (DR-0521, DR-0579). A vault.yaml that isn't a vault's, or doesn't
    read, is left as it is."""
    for config in folder.rglob(CONFIG_FILE):
        if ".git" in config.parts:
            continue
        with contextlib.suppress(DocketError, OSError, ValueError):
            root = config.parent
            home = Home.resolve(root, VaultConfig.load(config).storage)
            inforce.load(root, home)
            forgotten.load(root, home)


def _fit(template: Path, repo: Path, copy: Path) -> None:
    """A copied repo made to look as a fresh build would: on Linux and macOS its index refreshed, since a copy's inodes
    differ there, so a test that compares the index's bytes sees git's first status leave them alone; on Windows git
    compares sizes and modification times only, which the copy keeps, so the index is fresh as copied (DR-0531). And
    each remote that led into the template pointed at the same place in the copy. The remote with git's own command: a
    Windows path in .git/config has its backslashes doubled, which a text replace would miss."""
    if sys.platform != "win32":
        sh(repo, "git", "update-index", "-q", "--refresh", check=False)
    if "[remote " not in (repo / ".git" / "config").read_text(encoding="utf-8"):
        return
    urls = sh(repo, "git", "config", "--get-regexp", "^remote[.].*[.]url$", check=False)
    for line in urls.splitlines():
        key, _, url = line.partition(" ")
        target = Path(url)
        if target.is_absolute() and target.resolve().is_relative_to(template.resolve()):
            moved = copy / target.resolve().relative_to(template.resolve())
            sh(repo, "git", "remote", "set-url", key.removeprefix("remote.").removesuffix(".url"), str(moved))


def manifest(folder: Path) -> dict[str, list[Any]]:
    """Every path under a template, links included and not followed: its kind, and a file's size and time."""
    found: dict[str, list[Any]] = {}
    for top, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for entry in [*dirs, *files]:
            path = Path(top) / entry
            rel = path.relative_to(folder).as_posix()
            if _CACHES.search(entry):
                continue
            if path.is_symlink():
                found[rel] = ["link", str(path.readlink())]
            elif path.is_dir():
                found[rel] = ["dir"]
            else:
                stat = path.stat()
                found[rel] = ["file", stat.st_size, stat.st_mtime_ns]
    return found


def template_changes(templates: Path) -> list[str]:
    """What tests wrote into the templates in this folder: each path added, removed or changed since its build."""
    problems: list[str] = []
    for recorded in sorted(templates.glob("*.manifest.json")) if templates.is_dir() else []:
        folder = templates / recorded.name.removesuffix(".manifest.json")
        before, after = json.loads(recorded.read_text(encoding="utf-8")), manifest(folder)
        for rel in sorted(set(before) | set(after)):
            if rel not in after:
                problems.append(f"{folder.name}/{rel}: removed")
            elif rel not in before:
                problems.append(f"{folder.name}/{rel}: added")
            elif before[rel] != after[rel]:
                problems.append(f"{folder.name}/{rel}: changed")
    return problems


def template_vault(factory: pytest.TempPathFactory) -> Path:
    """The fixture vault, built once per session; each test copies it instead of paying for git init and a commit."""
    return shared(lambda d: fixture_vault(d).root, "fixture-vault", factory)


def copy_vault(template: Path, tmp_path: Path) -> Vault:
    target = tmp_path / "vault"
    shutil.copytree(template, target)
    return Vault.open(target)


READ_ROUTES = ["parse", "index"]
# The calls a route takes: each asks the raw log through the index when it is current (DR-0446, DR-0522).
ROUTED = ("read", "verify", "doc_put", "resolves")
# And a notes import, which checks every note's covers through one lookup (DR-0522); it parses every legacy doc itself.
IMPORTS = ("import_notes",)


def read_route(route: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every `recall.read`, `verify`, `doc_put` and `resolves`, and every notes import, in a test through one route
    (DR-0321, DR-0446, DR-0522): `parse`, with no index, parses every doc and loads each source whole; `index`
    brings the index up to date before each call, and loading a source whole while the call runs fails the test, as
    does parsing every doc outside an import, so the index answers all that read asks and every record comes from its
    place. A test run both ways shows the two give the same result."""
    from docket import index, recall
    from docket.imports import notes
    from docket.index.build import db_path
    from docket.vault.docstore import DocStore
    from docket.vault.spans import RawLookup

    load = RawLookup._source  # pyright: ignore[reportPrivateUsage]
    parse_all = DocStore.all
    reading: list[str] = []

    def parsing(name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def call(vault: Vault, *args: Any, **kwargs: Any) -> Any:
            assert not db_path(vault).exists(), f"{name} on the parse route found an index"
            return real(vault, *args, **kwargs)

        return call

    def through_index(name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def call(vault: Vault, *args: Any, **kwargs: Any) -> Any:
            index.update(vault)
            reading.append(name)
            try:
                return real(vault, *args, **kwargs)
            finally:
                reading.pop()

        return call

    def every_doc(self: DocStore) -> list[Any]:
        # An import lists every legacy doc to match its notes; only a read, verify or doc_put must not need them all.
        if reading and reading[-1] in IMPORTS:
            return parse_all(self)
        raise AssertionError(f"read parsed every doc in {self.root}, though the index is current")

    def whole_source(self: RawLookup, source: str) -> dict[str, list[RawRecord]]:
        if reading:
            raise AssertionError(f"{reading[-1]} loaded {source}'s raw whole, though the index is current")
        return load(self, source)

    for name in IMPORTS:
        real = getattr(notes, name)
        monkeypatch.setattr(notes, name, parsing(name, real) if route == "parse" else through_index(name, real))
    for name in ROUTED:
        real = getattr(recall, name)
        monkeypatch.setattr(recall, name, parsing(name, real) if route == "parse" else through_index(name, real))
    if route != "parse":
        monkeypatch.setattr(DocStore, "all", every_doc)
        monkeypatch.setattr(RawLookup, "_source", whole_source)


def template_project(factory: pytest.TempPathFactory, mappers: dict[str, str]) -> Path:
    """A consuming project with its vault, built once per session for these mappers; each test copies it with
    copy_project."""
    key = "project-" + digest(repr(sorted(mappers.items())))
    return shared(lambda d: consuming_project(d, mappers).root.parent, key, factory)


def copy_project(template: Path, tmp_path: Path) -> Vault:
    target = tmp_path / "project"
    shutil.copytree(template, target)
    return Vault.open(target / "vault")


def git(cwd: Path, *args: str) -> str:
    """git as the user of a host repo: an invented identity, the user's own commits."""
    return sh(cwd, "git", "-c", "user.name=Ada Example", "-c", "user.email=ada@example.invalid", *args)


def commit_config(root: Path) -> None:
    """vault.yaml as edited by hand, committed as the user would, so what it loosens is in force (DR-0521). An
    edit that changed nothing commits nothing."""
    if sh(root, "git", "status", "--porcelain", "--", "vault.yaml").strip():
        git(root, "commit", "-qm", "Settings", "--", "vault.yaml")


def configure(
    root: Path,
    *,
    credentials: bool | None = None,
    phone: bool | None = None,
    keywords: list[str] | None = None,
    patterns: list[RedactionPattern] | None = None,
) -> None:
    """A vault's redaction and never-store settings changed in its vault.yaml and committed, as the user would."""
    path = root / "vault.yaml"
    config = VaultConfig.load(path)
    if credentials is not None:
        config.redaction.credentials = credentials
    if phone is not None:
        config.redaction.phone = phone
    if keywords is not None:
        config.never_store.keywords = keywords
    if patterns is not None:
        config.redaction.patterns = patterns
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    git(root, "commit", "-qm", "Settings", "--", "vault.yaml")


def host_repo(tmp_path: Path) -> Path:
    """A user's repo with two files in one commit and a bare origin it pushed to: the host a folder vault lives in. A
    copy of one built once per session."""
    return copied(tmp_path, _build_host_repo, "host-repo")


def _build_host_repo(tmp_path: Path) -> Path:
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", "origin.git")
    top = tmp_path / "host"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    (top / "README.md").write_text("hello\n", encoding="utf-8")
    (top / "other.txt").write_text("other\n", encoding="utf-8")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "Host start")
    git(top, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(top, "push", "-q", "origin", "main")
    return top


NORTHWIND_FILE = "entities/customer/northwind.yaml"
BEN_FILE = "entities/person/ben-sample.yaml"


def _build_entity_vault(folder: Path) -> Path:
    vault = Vault.init(folder / "vault", make_config(), "test-box")
    files = {
        NORTHWIND_FILE: "id: northwind\ntype: customer\nname: {en: Northwind}\n"
        "aliases:\n  - {text: Northwind Traders}\n",
        BEN_FILE: "id: ben-sample\ntype: person\nname: {en: Ben Sample}\n",
    }
    for rel, text in files.items():
        (vault.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault.root / rel).write_text(text, encoding="utf-8", newline="\n")
    vault.commit(list(files), "Two entities")
    return vault.root


def entity_vault(tmp_path: Path) -> Vault:
    """A vault holding two committed entities, Northwind and Ben Sample: a copy of one built once per session."""
    return Vault.open(copied(tmp_path, _build_entity_vault, "entity-vault"))


def folder_vault(tmp_path: Path) -> Vault:
    """A folder vault at notes/vault of a host repo, two levels down so a path bug can't hide, with the user's own
    work around it: a staged change, an unstaged one, and a staged hand-made file inside the vault. A copy of one built
    once per session."""
    return Vault.open(copied(tmp_path, _build_folder_vault, "folder-vault"))


def empty_vault(tmp_path: Path, config: VaultConfig, name: str = "vault") -> Vault:
    """A new vault of its own repo at tmp_path/name, as Vault.init makes it for this config: a copy of one built once
    per session. Tests about init itself call Vault.init."""
    assert config.storage == "repo", "a folder vault needs its host: folder_vault"
    key = f"empty-{name}-" + digest(config.dump())
    return Vault.open(copied(tmp_path, lambda d: Vault.init(d / name, config, "test-box").root, key))


def _build_folder_vault(tmp_path: Path) -> Path:
    top = _build_host_repo(tmp_path)
    vault = Vault.init(top / "notes" / "vault", make_config("folder"), "test-box")
    (top / "README.md").write_text("hello, staged\n", encoding="utf-8")
    git(top, "add", "README.md")
    (top / "other.txt").write_text("other, unstaged\n", encoding="utf-8")
    (vault.root / "processed" / "mine.md").write_text("my own note\n", encoding="utf-8")
    git(top, "add", "notes/vault/processed/mine.md")
    return vault.root


def users_work(top: Path) -> tuple[str, str]:
    """What the user staged and changed in a host, as git shows it."""
    return git(top, "diff", "--cached"), git(top, "diff")


def note(
    doc_id: str = "recording-note:2026-10-14-supplier-call",
    covers: list[str] | None = None,
    lines: list[str] | None = None,
    **fields: str,
) -> str:
    """A doc's text: the frontmatter from the id, covers and any other fields given, then a heading and the lines."""
    head = [f"id: {doc_id}", f"type: {doc_id.partition(':')[0]}"]
    head += [f"{k}: {v}" for k, v in fields.items()]
    head += ["covers:", *(f"  - {rid}" for rid in (covers if covers is not None else [REC]))]
    if "by" not in fields:
        head.append("by: claude-test, sync 2026-10-14T16:07")
    body = lines if lines is not None else ["- Ben put the consultant at 10 million forints. @12:08"]
    return "---\n" + "\n".join(head) + "\n---\n\n# A note\n\n" + "\n".join(body) + "\n"


# -- the index fixture ------------------------------------------------------------------------------------------------

INDEX_FIXTURE = Path(__file__).parent / "fixtures" / "index-vault"
# The index fixture's clock: Wednesday 14 October 2026, so "last week" is 5 to 11 October.
INDEX_NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
CALL = "calls:rec-0100"
SECOND = "calls:rec-0100-second"
FLEET = "calls:rec-0101"
PARENT = "chat:C0OPS/1791400000.000100"
OFFER = "mail:<offer-1@northwind.example>"


def index_config(languages: list[str] | None = None) -> VaultConfig:
    return VaultConfig(
        owner=Owner(
            name="Ada Example",
            emails=["ada@example.invalid"],
            languages=languages or ["en", "hu"],
            time_zone="Europe/Budapest",
        ),
        primary_machine="test-box",
        sources={
            "calls": SourceConfig(sensitivity="mixed"),
            "chat": SourceConfig(),
            "mail": SourceConfig(),
            "cal": SourceConfig(),
        },
    )


def fixture_records(source: str) -> list[RawRecord]:
    lines = (INDEX_FIXTURE / "records" / f"{source}.jsonl").read_text(encoding="utf-8").splitlines()
    return [RawRecord.model_validate(json.loads(line)) for line in lines if line.strip()]


def files_in(vault: Vault, folder: str) -> list[str]:
    """A folder's files as vault paths: `Vault.commit` takes files, and a folder given whole commits nothing."""
    return sorted(p.relative_to(vault.root).as_posix() for p in (vault.root / folder).rglob("*") if p.is_file())


def build_index_vault(root: Path) -> Vault:
    """The index fixture as a vault: its records written through write_run, its entity files copied in, its docs put
    through doc_put in file-name order. Built under the caller's git settings."""
    from docket import recall

    vault = Vault.init(root / "vault", index_config(), "test-box")
    for source in ("cal", "calls", "chat", "mail"):
        vault.write_run(source, fixture_records(source), datetime(2026, 10, 14, 7, 0, tzinfo=UTC))
    shutil.copytree(INDEX_FIXTURE / "entities", vault.root / "entities", dirs_exist_ok=True)
    vault.commit(files_in(vault, "entities"), "Entities")
    for path in sorted((INDEX_FIXTURE / "docs").glob("*.md")):
        recall.doc_put(vault, path.read_text(encoding="utf-8"))
    return vault


LOGICAL_TABLES = (
    "meta", "run_files", "records", "record_versions", "segments", "docs", "doc_files", "doc_covers", "doc_anchors",
    "old_paths", "entities", "entity_files", "aliases", "alias_terms", "entity_links", "external_ids", "problems",
    "items", "item_log", "item_numbers", "ledger_files",
)  # fmt: skip


def logical_dump(db: Path) -> dict[str, list[tuple[Any, ...]]]:
    """An index's rows keyed by what they mean, not by row id: chunks by owner and ordinal, the FTS5 index as its
    words per chunk. Two indexes with the same dump answer every query the same."""
    conn = sqlite3.connect(db)
    try:
        keys = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT id, owner, ordinal FROM chunks")}
        out = {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in LOGICAL_TABLES}  # noqa: S608
        out["chunks"] = sorted(tuple(row[1:]) for row in conn.execute("SELECT * FROM chunks"))
        # An op file's time is 0 or not by when it was read (RACY_NS): two reads a moment apart may differ there alone.
        out["ledger_files"] = [(p, size, sha, high) for p, size, _, sha, high in out["ledger_files"]]
        out["mentions"] = sorted((keys[c], e, h, a) for c, e, h, a in conn.execute("SELECT * FROM mentions"))
        # Vectors by the chunk they belong to, not its row id (DR-0336).
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'chunk_vec_%'"):
            out[name] = sorted((keys[c], *rest) for c, *rest in conn.execute(f"SELECT * FROM {name}"))  # noqa: S608
        conn.execute("CREATE VIRTUAL TABLE temp.words USING fts5vocab(main, chunks_fts, 'instance')")
        out["fts"] = sorted((t, keys[d], c, o) for t, d, c, o in conn.execute("SELECT * FROM temp.words"))
        return out
    finally:
        conn.close()


def template_with(factory: pytest.TempPathFactory, make: Callable[[Path], Vault]) -> Path:
    """A vault `make` builds, once per session, keyed by its module and name; tests copy it."""
    return shared(lambda d: make(d).root, f"{make.__module__}.{make.__qualname__}", factory)


def index_template(factory: pytest.TempPathFactory) -> Path:
    """The index fixture, built once per session; tests copy it."""
    return template_with(factory, build_index_vault)


# The name vault: a vault about one customer, where two invented names are in most chunks. Acme's stems differ from it
# (`acm`), Orbex's forms are all the word itself; both stay plain words, never entities. Rita Sample is the one entity.
WEEKLY = 160
KICK_OFF = "calls:rec-0200"
CRANE = "calls:rec-0201"
FILLER = (
    "The team walked through the schedule, the site plan and the open items, then agreed on the next steps for the "
    "week and who follows up with whom."
)
NAME_MESSAGES = {
    # Other customers' news, short and saying the query's words often: plain BM25 puts them first.
    "kick": "Borealis kick-off with Dana: kick-off agenda, kick-off notes, Dana runs the kick-off.",
    "orbex": "Orbex budget: budget budget budget.",
    "acme": "Acme budget, among the many other things we went through at length today, gets a second look later.",
    "approval": "Borealis budget approval: budget approved.",
    "crane": "Borealis crane audit: crane audit done, crane audit report sent, crane audit closed.",
    "both": "Crane audit approval granted.",
    "rita": "Rita: budget, budget, budget.",
    "granite": "Granite pump overhaul: pump overhaul, granite pump, overhaul today.",
    "granite2": "Granite pump overhaul starts, the pump overhaul crew is ready.",
}
NAME_DOCS = {
    "weekly-notes": ("Weekly notes", "The acme kick-off with dana went well."),
    "acme-budget-review": ("Acme budget review", f"{FILLER} {FILLER}"),
    "notes-from-the-week": ("Notes from the week", "Acme budget review went fine; acme budget numbers came in."),
    "mail-sweep": (
        "2026-10-12 - Mail sweep, received 11 Oct 23:44 to 12 Oct 22:50: the Acme kick-off ran, Dana sent the notes",
        " ".join([FILLER] * 6),
    ),
    "acme-kick-off-second-half": (
        "2026-10-12 - Acme kick-off with Dana (second half): no site in the plan yet",
        " ".join([FILLER] * 3),
    ),
    "acme-review-of-the-budget": ("Acme review of the budget", f"{' '.join([FILLER] * 4)} The budget review is due."),
    "acme-dontes": (
        "Acme döntés a határidőről",
        "A csapat megbeszélte a következő lépéseket, és pénteken újra összeül.",
    ),
    "heti-jegyzet": ("Heti jegyzet", "Az acme határidő kérdése nyitott, az acme határidő csúszik."),
}


def _segments(texts: list[str]) -> list[dict[str, Any]]:
    speakers = ("Ada Example", "Ben Sample")
    return [
        {"start_ms": n * 30_000, "end_ms": (n + 1) * 30_000, "speaker": speakers[n % 2], "text": text}
        for n, text in enumerate(texts)
    ]


def name_records() -> dict[str, list[RawRecord]]:
    """The name vault's records by source."""
    fetched = {"fetched": "2026-10-14T07:00:00Z", "via": "fixture"}
    mail = [
        {
            "rid": f"mail:<weekly-{n}@acme.example>", "source": "mail", "kind": "mail", "container": "mail:weekly",
            "ts": f"2026-{5 + n // 30:02d}-{1 + n % 28:02d}T09:00:00Z", "title": f"Weekly status {n}",
            "text": f"Acme and Orbex: routine status for week {n}. Deliveries arrived on time and the line ran well.",
        }
        for n in range(WEEKLY)
    ]  # fmt: skip
    mail.append(
        {
            "rid": "mail:<rita-1@acme.example>", "source": "mail", "kind": "mail", "container": "mail:rita",
            "ts": "2026-10-12T15:00:00Z", "title": "Budget call with Rita",
            "text": "A short call about the numbers for next quarter, nothing settled yet, more to follow next week.",
        }
    )  # fmt: skip
    # A reply: its subject's label ends in a colon, which must not end the title's lead.
    mail.append(
        {
            "rid": "mail:<granite-2@acme.example>", "source": "mail", "kind": "mail", "container": "mail:granite",
            "ts": "2026-10-13T11:00:00Z", "title": "RE: Granite pump overhaul",
            "text": "Agreed, we start on Monday with the crew from the yard, and the parts arrive on Tuesday.",
        }
    )  # fmt: skip
    chat = [
        {
            "rid": f"chat:C0NAMES/{1791500000 + n}.000100", "source": "chat", "kind": "message",
            "container": "chat:C0NAMES", "ts": f"2026-10-13T08:{n:02d}:00Z", "author": "U0BEN", "text": text,
        }
        for n, text in enumerate(NAME_MESSAGES.values())
    ]  # fmt: skip
    kick_off = [
        "Let us start with the site plan and the schedule for the first month.",
        "The cranes arrive in the second week, after the foundations are poured.",
        "Who owns the safety briefing for the night shift?",
        "I will draft it and send it round by Friday.",
        "Then the next call is in two weeks, same time.",
        "Thanks everyone, that is all for today.",
    ]
    crane = [
        "Good morning, this is the borealis crane audit for the north yard, starting with the hoist, the rails, the "
        "brakes and the load charts, then the logbooks and the operator cards.",
        "The hoist passed, the rails need new clamps on the east side.",
        "The brakes passed; the load charts are out of date and get reprinted.",
        "The logbooks are complete, two operator cards expire next month.",
    ]
    calls = [
        {"rid": KICK_OFF, "source": "calls", "kind": "recording", "ts": "2026-10-12T09:00:00Z",
         "title": "Acme kick-off with Dana", "segments": _segments(kick_off)},
        {"rid": CRANE, "source": "calls", "kind": "recording", "ts": "2026-10-13T10:00:00Z",
         "title": "Borealis crane audit", "segments": _segments(crane)},
    ]  # fmt: skip
    by_source = {"mail": mail, "chat": chat, "calls": calls}
    return {s: [RawRecord.model_validate({**r, **fetched}) for r in rs] for s, rs in by_source.items()}


def name_docs() -> list[str]:
    """The name vault's docs: a recording note of three sections under its recording's name, and legacy notes."""
    sections = [
        ("Site plan", ["- The site plan and the schedule come first. @00:00-00:30", f"- {FILLER} @00:30-01:00"]),
        ("Cranes", ["- The cranes arrive in the second week. @00:30-01:00", f"- {FILLER} @01:00-01:30"]),
        ("Safety", ["- Ada drafts the safety briefing by Friday. @01:30-02:00", f"- {FILLER} @02:00-02:30"]),
    ]
    body = "\n\n".join(f"## {heading}\n\n" + "\n".join(lines) for heading, lines in sections)
    note = (
        f"---\nid: recording-note:2026-10-12-acme-kick-off\ntype: recording-note\ncovers:\n  - {KICK_OFF}\n"
        f"by: claude-test, sync 2026-10-12T12:00\n---\n\n# Acme kick-off with Dana\n\n{body}\n"
    )
    legacy = [
        f"---\nid: legacy-note:{slug}\ntype: legacy-note\nby: import\n---\n\n# {title}\n\n{text}\n"
        for slug, (title, text) in NAME_DOCS.items()
    ]
    return [note, *legacy]


def build_name_vault(root: Path) -> Vault:
    """The name vault: about 160 weekly mails naming Acme and Orbex, short messages about other customers, two
    recordings (one with a recording note under its name, one without), docs titled with the queries' words and docs
    that say them only in their bodies, and Rita Sample with her alias. The docs go in one commit."""
    from docket.vault.docstore import DocStore, PutResult

    vault = Vault.init(root / "vault", index_config(), "test-box")
    for source, records in sorted(name_records().items()):
        vault.write_run(source, records, datetime(2026, 10, 14, 7, 0, tzinfo=UTC))
    person = vault.root / "entities" / "person"
    person.mkdir(parents=True, exist_ok=True)
    (person / "rita-sample.yaml").write_text(
        "id: rita-sample\ntype: person\nname: {en: Rita Sample}\naliases:\n  - {text: Rita}\n", encoding="utf-8"
    )
    vault.commit(["entities/person/rita-sample.yaml"], "Entities")
    stored = DocStore(vault).put_many(name_docs(), "Name vault docs")
    assert all(isinstance(r, PutResult) for r in stored), stored
    return vault


def name_template(factory: pytest.TempPathFactory) -> Path:
    """The name vault, built once per session; tests copy it."""
    return template_with(factory, build_name_vault)


# The compound vault (#59): each spelling of a few compounds, in English and Hungarian, and the shapes a split
# mistakes: two words that are in most chunks, a contraction, a word that splits into two others. Not `were` or
# `format`: their Hungarian stems are `we` and `for`, so they meet "we're" and "form at" without a split.
COMPOUND_KICK = "calls:rec-0300"
COMPOUND_FOLLOW = "calls:rec-0301"
COMPOUND_FILLERS = 40
EDITORS = 6
WE_LL_LONG = (
    "We'll be on site at seven with the second crew, and the pallets come off the first truck before the yard gets "
    "busy; the gate log, the badges and the safety walk take the first hour, the crane checks the second, and the "
    "cones go out along the north fence before lunch, as the plan from last week said, and it went well"
)
# Long, so plain BM25 ranks it below a short message saying kickoff twice: only its title's lead lifts it.
KICK_MAIL = (
    "The agenda for the first week on site: who brings the drawings, where the cabins stand, when the fence goes up, "
    "how the gate log works, which crew takes the night shift, who signs the permits, where the cones are kept, and "
    "what the yard needs before the first truck comes in on Monday morning."
)
COMPOUND_MESSAGES = {
    "kickoff": "The Orbex kickoff moves to Thursday.",
    "checklist": "Orbex kickoff checklist: the kickoff starts at nine.",
    "kickoffs": "The kick-offs went well.",
    "kicked": "We kicked off the crane work.",
    "followup": "followup: the crane quote is still open.",
    "emailben": "Küldd el e-mailben a darulistát.",
    "emailt": "Megkaptam az e-mailt.",
    "email": "The email with the crane list came.",
    "emailcim": "Írd meg az e-mail-címedet.",
    "online-hu": "Az on-line egyeztetés kedden lesz.",
    "online": "The online form for the crane permit.",
    "setup": "The setup took an hour.",
    "set-up": "We set up the crane by noon.",
    "well-short": "We'll be on site at seven.",
    "well": "It went well.",
    "well-long": WE_LL_LONG,
    **{f"editor-{n}": f"The editor of the crane list checked row {n}." for n in range(EDITORS)},
    **{f"edit-or-{n}": f"Edit or delete row {n} of the crane list." for n in range(2)},
    "visitor": "The visitor signed the crane log.",
    "visit-or": "Visit or call the yard before noon.",
    "to-day": "The pour moves to day two.",
    **{f"filler-{n}": f"We set the pallets up today, round {n}." for n in range(COMPOUND_FILLERS)},
}


def compound_records() -> dict[str, list[RawRecord]]:
    """The compound vault's records by source."""
    fetched = {"fetched": "2026-10-14T07:00:00Z", "via": "fixture"}
    mails = [
        ("kick-1", "Orbex kick-off with Dana", KICK_MAIL),
        ("follow-1", "Follow-up on the crane quote", "Two lines on the price and the delivery date."),
        ("edit-1", "Edit or delete the crane list", "Edit or delete the rows that are wrong."),
    ]
    mail = [
        {"rid": f"mail:<{key}@orbex.example>", "source": "mail", "kind": "mail", "container": "mail:orbex",
         "ts": f"2026-10-12T{8 + n:02d}:00:00Z", "title": title, "text": text}
        for n, (key, title, text) in enumerate(mails)
    ]  # fmt: skip
    chat = [
        {"rid": f"chat:C0COMPOUND/{1791600000 + n}.000100", "source": "chat", "kind": "message",
         "container": "chat:C0COMPOUND", "ts": f"2026-10-13T{8 + n // 60:02d}:{n % 60:02d}:00Z", "author": "U0BEN",
         "text": text}
        for n, text in enumerate(COMPOUND_MESSAGES.values())
    ]  # fmt: skip
    calls = [
        {"rid": COMPOUND_KICK, "source": "calls", "kind": "recording", "ts": "2026-10-12T14:00:00Z",
         "title": "Weekly site call", "segments": _segments(
             ["Good morning everyone.", "Then we kick off the Orbex work on Monday.", "Thanks, see you then."])},
        {"rid": COMPOUND_FOLLOW, "source": "calls", "kind": "recording", "ts": "2026-10-13T14:00:00Z",
         "title": "Quote review", "segments": _segments(
             ["The quote came in high.", "I will follow up on the crane quote tomorrow.", "Good, thanks."])},
    ]  # fmt: skip
    by_source = {"mail": mail, "chat": chat, "calls": calls}
    return {s: [RawRecord.model_validate({**r, **fetched}) for r in rs] for s, rs in by_source.items()}


def compound_chat(key: str) -> str:
    """The rid of one of the compound vault's messages."""
    return f"chat:C0COMPOUND/{1791600000 + list(COMPOUND_MESSAGES).index(key)}.000100"


def build_compound_vault(root: Path) -> Vault:
    vault = Vault.init(root / "vault", index_config(), "test-box")
    for source, records in sorted(compound_records().items()):
        vault.write_run(source, records, datetime(2026, 10, 14, 7, 0, tzinfo=UTC))
    return vault


def compound_template(factory: pytest.TempPathFactory) -> Path:
    """The compound vault, built once per session; tests copy it."""
    return template_with(factory, build_compound_vault)


def fts_and_check(vault: Vault, query: str, where: str = "") -> tuple[set[int], set[int]]:
    """The chunks the all-words stage finds for a query's words, and those the word check says hold every word, over
    the chunks `where` (SQL on `c`) keeps: the two must agree (DR-0282)."""
    from docket.index import query as search_module
    from docket.index.build import languages_of, open_index
    from docket.index.terms import Checker
    from docket.index.text import words_and_compounds

    conn = open_index(vault)
    try:
        s = search_module._Search(conn, vault, languages_of(vault))  # pyright: ignore[reportPrivateUsage]
        tokens, compounds = words_and_compounds(query)
        _, kept = s.find_entities(tokens)
        joins = search_module._joins(tokens, compounds, kept)  # pyright: ignore[reportPrivateUsage]
        groups, words, _ = s.word_groups([tokens[i] for i in kept], joins)
        found = set(s.fts(" AND ".join(g for _, g in groups), (where, []), 1_000_000))
        checker = Checker(s.analyzer, words, [])
        rows = conn.execute(f"SELECT c.id, c.text FROM chunks c WHERE 1 = 1{where}")  # noqa: S608 - the test's own SQL
        held = {chunk for chunk, text in rows if checker.count(text) == len(words)}
        return found, held
    finally:
        conn.close()


def query_words(
    analyzer: Any, texts: list[str], picked: list[str], *, seed: int, sample: int = 60, compounds: bool = False
) -> list[Any]:
    """Query words for the word check: those picked, and a seeded sample of the texts' own words, each with the forms
    search gives it (as written, then each language's forms). With compounds, the pool takes the texts' hyphenated
    compounds joined, and each word its next word's join and a split at every place a split may be."""
    import random

    from docket.index.terms import Word, phrase_of, tokens
    from docket.index.text import words_and_compounds
    from docket.plaintext import fold

    def forms(w: str) -> list[tuple[str, str]]:
        stemmed = [(lang.column, f) for lang in analyzer.languages for f in analyzer.forms(w, lang)]
        return [("folded", fold(w)), *stemmed]

    pool = {w for text in texts for w in tokens(text)}
    if compounds:
        pool |= {j.word for text in texts for j in words_and_compounds(text)[1]}
    chosen = list(dict.fromkeys([*picked, *random.Random(seed).sample(sorted(pool), min(sample, len(pool)))]))  # noqa: S311 - seeded test words
    out: list[Any] = []
    for i, w in enumerate(chosen):
        every = forms(w)
        splits: tuple[Any, ...] = ()
        if compounds:
            if i + 1 < len(chosen):
                every += forms(w + chosen[i + 1])
            splits = tuple(phrase_of(analyzer, f"{w[:at]} {w[at:]}") for at in range(3, len(w) - 1))
        out.append(Word(w, frozenset(every), splits))
    return out


def stem_every_word(
    analyzer: Any, words: list[Any], phrases: list[Any], text: str
) -> tuple[set[int], set[int], set[int]]:
    """Which words and phrases a text holds, and which words only through a split, found the slow way: every word of
    the text and every compound it joins folded and stemmed, and every split looked for."""
    from docket.index.text import words_and_compounds
    from docket.plaintext import fold

    def terms_of(w: str) -> set[tuple[str, str]]:
        return {("folded", fold(w)), *((lang.column, analyzer.stem(w, lang)) for lang in analyzer.languages)}

    ws, joined = words_and_compounds(text)
    terms = [terms_of(w) for w in ws]
    every: set[tuple[str, str]] = set().union(*terms, *(terms_of(j.word) for j in joined))

    def found(phrase: Any) -> bool:
        return any(
            all((col, seq[k]) in terms[start + k] for k in range(len(seq)))
            for col, seq in phrase.columns
            for start in range(len(ws) - len(seq) + 1)
        )

    held_words = {i for i, word in enumerate(words) if word.forms & every}
    held_phrases = {i for i, phrase in enumerate(phrases) if found(phrase)}
    split_only = {i for i, word in enumerate(words) if i not in held_words and any(found(s) for s in word.splits)}
    return held_words, held_phrases, split_only


# An invented notes folder, as an existing setup would have it: notes per source, a clash of stems, a note naming two
# recordings, one naming a recording not in the raw log yet, one with its own frontmatter, CRLF with a BOM, a
# credential, Latin-1, files that aren't notes, a folder git ignores, and two the never-store keywords catch: one by
# its text, one by a folder name that sorts first, so a path logged before the screen would show.
NEVER_STORE_KEYWORDS = ["dora example", "project falcon"]
NOTES: dict[str, bytes] = {
    "notes/0-people/dora-example/2026-09-01-catch-up.md": b"# Catch-up\n\nAll fine.\n",
    "notes/calls/2026-09-10-supplier-call.md": (
        b"# Supplier call\n\nSource: call recorder `rec-0042`, 45 min.\n\n## What was said\n\n"
        b"- Ben put the consultant at 10 million forints (12:08).\n- The follow-up is `rec-0099`.\n"
    ),
    "notes/calls/2026-09-12-two-parts.md": b"# Two parts\n\nRecorded as calls:rec-0042 and vendor:rec-0007.\n",
    "notes/calls/2026-09-14-later.md": b"# A later call\n\nSource: `calls:rec-0050`, not fetched yet.\n",
    "notes/calls/2026-10-14-supplier-call.md": b"# Supplier call again\n\nNo recorder id here.\n",
    "notes/chat/2026-09-10-supplier-call.md": b"# Chat about the supplier\n\nBen and Cleo agreed on Friday.\n",
    "notes/falcon.md": b"# Plans\n\nProject Falcon starts in spring.\n",
    "notes/latin1.md": b"caf\xe9\n",
    "notes/mail/2026-09-11-offer.md": (
        b"\xef\xbb\xbf# The offer\r\n\r\nCleo sent the offer.\r\npassword: hunter2-Example\r\n"
    ),
    "notes/own-frontmatter.md": b"---\ntags: [old]\n---\n# With its own frontmatter\n",
    "notes/photo.jpg": b"\xff\xd8\xff\xe0",
    "notes/private/diary.md": b"# Kept out of git\n",
    "notes/raw.json": b"{}\n",
    "notes/árvíztűrő-jegyzet.md": "# Árvíztűrő tükörfúrógép\n".encode(),
}
UNTRACKED_NOTE = "notes/calls/2026-09-13-untracked.md"
ADA = ("-c", "user.name=Ada Example", "-c", "user.email=ada@example.invalid")


def make_notes_repo(root: Path) -> Path:
    """The invented notes folder in a git repo of its own, committed, with one note not committed yet; returns the
    notes folder."""
    repo = root / "work"
    for rel, data in NOTES.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (repo / ".gitignore").write_text("notes/private/\n", encoding="utf-8")
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", *ADA, "commit", "-q", "-m", "notes")
    (repo / UNTRACKED_NOTE).write_text("# Not committed yet\n\nA new note.\n", encoding="utf-8")
    return repo / "notes"


def isolated_git(mp: pytest.MonkeyPatch, home: Path) -> None:
    """The git and Docket settings conftest gives each test, for a template built once per session."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "gitconfig").write_text(TEST_GITCONFIG, encoding="utf-8")
    mp.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
    mp.setenv("DOCKET_CONFIG_DIR", str(home / "docket"))
    mp.setenv("DOCKET_STATE_DIR", str(home / "docket-state"))
    mp.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    mp.setenv("DOCKET_MACHINE", "test-box")
    mp.delenv("DOCKET_VAULT", raising=False)
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        mp.delenv(name, raising=False)


def screening(vault: Vault) -> Vault:
    """The vault with the fixture's never-store keywords in its settings, in memory and in vault.yaml."""
    vault.config = vault.config.model_copy(update={"never_store": NeverStore(keywords=NEVER_STORE_KEYWORDS)})
    (vault.root / "vault.yaml").write_text(vault.config.dump(), encoding="utf-8", newline="\n")
    return vault


def notes_template(factory: pytest.TempPathFactory) -> Path:
    """The invented notes repo, built once per session; returns the repo (its notes folder is notes/)."""
    return shared(lambda d: make_notes_repo(d).parent, "notes-repo", factory)


def imported_template(factory: pytest.TempPathFactory, vault_template: Path, repo_template: Path) -> Path:
    """A vault after importing the invented notes folder, and the folder beside it, built once per session: returns
    the folder holding both (vault/ and work/)."""
    from docket.imports.notes import import_notes

    def build(base: Path) -> Path:
        vault = screening(copy_vault(vault_template, base))
        shutil.copytree(repo_template, base / "work")
        import_notes(vault, base / "work" / "notes")
        return base

    return shared(build, "imported-" + digest(str(vault_template), str(repo_template)), factory)


# -- a big source, for timing reads (#76) -----------------------------------------------------------------------------

BIG_OLD = "notes/calls/2026-05-10-supplier-call.md"
BIG_NOTE = "legacy-note:2026-05-10-supplier-call"
BIG_SEGMENTS = 600
BIG_WORDS = [
    "price", "offer", "friday", "supplier", "consultant", "quarter", "million",
    "signed", "budget", "review", "plan", "team", "call", "week",
]  # fmt: skip


def traced() -> bool:
    """Whether a tracer such as coverage runs, so elapsed times say nothing about the code."""
    if sys.gettrace() is not None:
        return True
    try:
        import coverage
    except ImportError:  # pragma: no cover - coverage comes with the dev tools
        return False
    return coverage.Coverage.current() is not None


@dataclass(frozen=True)
class Best[T]:
    """A timed measure's runs: the fastest, which the test asserts, each run's seconds in order, and the last run's
    result."""

    seconds: float
    times: tuple[float, ...]
    result: T

    def runs(self, unit: Literal["s", "ms"] = "s") -> str:
        """Each run's time, for the test's print: `0.63, 0.70, 0.81 s` or `21.4, 20.9, 23.0 ms`."""
        if unit == "ms":
            return ", ".join(f"{t * 1000:.1f}" for t in self.times) + " ms"
        return ", ".join(f"{t:.2f}" for t in self.times) + " s"


@overload
def best_of[T](n: int, fn: Callable[[], T]) -> Best[T]: ...
@overload
def best_of[S, T](n: int, fn: Callable[[S], T], setup: Callable[[], S]) -> Best[T]: ...
def best_of(n: int, fn: Callable[..., Any], setup: Callable[[], Any] | None = None) -> Best[Any]:
    """`fn` timed `n` times, for a timed test to assert the fastest: a shared CI runner's slow moment lands on one run,
    rarely on all of them. Three runs; two when one takes over about 3 s on CI, to keep its time; one under a tracer
    such as coverage, where no time is asserted. The garbage is collected before each run, so none pays for what came
    before it. With `setup`, each run takes what it returns, made outside the clock: a run that writes gets fresh
    state. A bound on a wait (a timeout kept, a lock given up, a worker's exit noticed) is no speed: it's timed once."""
    times: list[float] = []
    result: Any = None
    for _ in range(1 if traced() else n):
        given = () if setup is None else (setup(),)
        gc.collect()
        began = time.perf_counter()
        result = fn(*given)
        times.append(time.perf_counter() - began)
    return Best(min(times), tuple(times), result)


def _big_recording(n: int, when: datetime, rng: random.Random) -> list[RawRecord]:
    rid = f"plaud:of_{n:032x}"
    said: list[dict[str, Any]] = []
    start = 0
    for _ in range(BIG_SEGMENTS):
        length = rng.randint(2_000, 9_000)
        text = " ".join(rng.choice(BIG_WORDS) for _ in range(rng.randint(6, 16)))
        speaker = rng.choice(["Ben Sample", "Cleo Test", "Ada Example"])
        said.append({"start_ms": start, "end_ms": start + length, "speaker": speaker, "text": text})
        start += length
    under = {"source": "plaud", "container": rid, "thread": rid, "ts": when, "fetched": when, "via": "test"}
    return [
        RawRecord.model_validate(
            {**under, "rid": rid, "kind": "recording", "title": f"Call {n}", "container": None, "thread": None}
        ),
        RawRecord.model_validate(
            {
                **under,
                "rid": f"{rid}/transaction",
                "kind": "transcript",
                "segments": said,
                "text": "\n".join(f"{s['speaker']}: {s['text']}" for s in said),
            }
        ),
        RawRecord.model_validate(
            {**under, "rid": f"{rid}/summary", "kind": "vendor-summary", "text": "## Summary\n\nPrice agreed."}
        ),
    ]


def big_raw(root: Path, megabytes: float) -> tuple[Vault, str]:
    """A vault with about this many megabytes of invented Plaud raw in one source: recordings of 600 segments, each
    with its transcript and summary, two to a run, a run every 13 hours, written straight to raw/ and committed once;
    and a legacy note whose old path is BIG_OLD, on the middle recording, which is returned."""
    vault = Vault.init(root / "vault", index_config(), "test-box")
    rng = random.Random(76)  # noqa: S311 - seeded test text, not secrets
    first = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    size, n = 0, 0
    recordings: list[str] = []
    while size < megabytes * 1_000_000:
        when = first + timedelta(hours=13 * (n // 2))
        records = [*_big_recording(n, when, rng), *_big_recording(n + 1, when, rng)]
        recordings += [r.rid for r in records if r.kind == "recording"]
        stem = f"{when:%Y-%m-%dT%H%M}-test-box"
        path = vault.root / "raw" / "plaud" / f"{when:%Y}" / f"{when:%m}" / f"{stem}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = "".join(r.model_copy(update={"v": version(r), "run": stem}).to_json() + "\n" for r in records)
        path.write_text(data, encoding="utf-8", newline="\n")
        size += len(data.encode("utf-8"))
        n += 2
    vault.commit([], "Raw: plaud")
    middle = recordings[len(recordings) // 2]
    from docket.vault.docstore import DocStore

    DocStore(vault).put(
        f"---\nid: {BIG_NOTE}\ntype: legacy-note\ncovers:\n  - {middle}\nold_paths:\n  - {BIG_OLD}\n"
        "by: claude-test, import 2026-10-16T10:00\n---\n\n# Supplier call\n\nThe price was agreed.\n"
    )
    return vault, middle
