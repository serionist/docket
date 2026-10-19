"""The day at the session start (#207, DR-0670 to DR-0672): the hook asks the running service for it with the room its
own lines leave, prints it whole in a session whose own repo is the vault's and one line of counts anywhere else, a
repo nested in the vault's among them, shows the owner only what asks something of them, and says one line naming the
read that works when the service is down, slow or can't read it. A fake service answers; no request leaves the test."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docket import hooks
from docket.cli import main
from docket.ledger import day
from docket.service import client, install
from docket.service.client import Answer
from docket.service.install import ServiceStatus
from docket.vault.home import repo_at

LINES = [
    {"text": "day Tue 10-20; sync chat 2d; due: chat (ask Claude to sync them)", "owner": True},
    {"text": "status: projects/pilot/STATUS.md: a hand edit holds its frontmatter", "owner": True},
    {"text": "queue: 1 untriaged", "owner": False},
    {"text": "A-0001 [reply] Ada Example: Send Ada the dates (10d, due 10-18, 2d late) [pilot]", "owner": False},
]
COUNTS = "day: 1 overdue, 0 due today, 0 meetings today, 1 untriaged; sync due: chat"


def status(running: bool = True) -> ServiceStatus:
    return ServiceStatus("task Docket", running, 8741, {"vault": "D:/work/vault"} if running else {}, "file", None, {},
                         None)  # fmt: skip


def a_repo(folder: Path) -> Path:
    """A folder holding a .git git would take: HEAD, objects/ and refs/, made by hand, as a stat sees a repo."""
    for part in ("objects", "refs"):
        (folder / ".git" / part).mkdir(parents=True)
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return folder


class FakeService:
    """The day's answers, and what the hook asked."""

    def __init__(self, repo: Path, answer: Answer | Exception | None = None, *, slow: bool = False) -> None:
        self.asked: list[int] = []
        whole = Answer(200, {"today": "2026-10-20", "repo": str(repo), "counts": COUNTS, "lines": LINES})
        self.answer = None if slow else answer or whole

    def __call__(self, st: ServiceStatus, room: int) -> Answer | None:
        self.asked.append(room)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def start(service: FakeService, project: Path | None, st: ServiceStatus | None = None) -> dict[str, Any]:
    environ = {hooks.PROJECT_VARIABLE: str(project)} if project is not None else {}
    return hooks.session_start(lambda: st or status(), environ, service)


def context(out: dict[str, Any]) -> list[str]:
    return str(out["hookSpecificOutput"]["additionalContext"]).splitlines()


def day_lines(out: dict[str, Any]) -> list[str]:
    """The lines after the status lines: the service's, the vault's and nothing else here, as no vault is set."""
    said = context(out)
    return said[said.index("docket: no vault yet; /docket:setup makes one") + 1 :]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The vault's repo, with the vault and the owner's work in folders of it."""
    top = a_repo(tmp_path / "work")
    (top / "vault").mkdir()
    (top / "projects" / "pilot").mkdir(parents=True)
    return top


def test_a_session_in_the_vault_s_repo_gets_the_whole_day_and_the_owner_what_asks_of_them(repo: Path) -> None:
    service = FakeService(repo)
    out = start(service, repo / "projects" / "pilot")
    assert day_lines(out) == [line["text"] for line in LINES]
    shown = out["systemMessage"].splitlines()
    assert shown[-2:] == [LINES[0]["text"], LINES[1]["text"]]
    assert "queue: 1 untriaged" not in shown
    # The room is what the hook's own lines leave of 45: the service's line and the vault's.
    assert service.asked == [hooks.LINES - 2]


def test_the_hook_s_lines_and_the_day_s_have_the_same_room() -> None:
    assert hooks.LINES == day.ROOM


@pytest.mark.parametrize("where", ["above it", "a repo nested in it", "another repo", "no repo"])
def test_a_session_anywhere_else_gets_one_line_of_counts(repo: Path, tmp_path: Path, where: str) -> None:
    """Above the vault's repo, in a separate repo inside it, in another repo, in no repo: counts only, never a title,
    a name or a subject, and nothing shown to the owner from the day."""
    project = {
        "above it": tmp_path,
        "a repo nested in it": a_repo(repo / "projects" / "pilot"),
        "another repo": a_repo(tmp_path / "elsewhere"),
        "no repo": tmp_path / "loose",
    }[where]
    project.mkdir(exist_ok=True)
    out = start(FakeService(repo), project)
    assert day_lines(out) == [COUNTS]
    assert "Ada" not in json.dumps(out)
    assert COUNTS not in out["systemMessage"]


def test_a_worktree_s_git_file_makes_its_own_repo(repo: Path, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {repo}/.git/worktrees/tree\n", encoding="utf-8")
    assert repo_at(tree) == tree
    assert day_lines(start(FakeService(repo), tree)) == [COUNTS]
    # A .git folder git wouldn't take is passed over, as git passes over it.
    (repo / "vault" / ".git").mkdir()
    assert repo_at(repo / "vault") == repo
    assert day_lines(start(FakeService(repo), repo / "vault"))[0] == LINES[0]["text"]


def test_without_a_project_folder_the_working_folder_is_the_session_s(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo / "projects")
    assert day_lines(start(FakeService(repo), None))[0] == LINES[0]["text"]


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (None, "day: no answer in 3 s; the ledger tool's snapshot reads it"),
        (Answer(400, {"error": "the service has no vault; run `docket service install --vault <folder>`"}),
         "day: not read: the service has no vault; run `docket service install --vault <folder>`"),
        (Answer(404, {"detail": "Not Found"}), "day: not read (HTTP 404)"),
        (Answer(200, {"lines": "not a list"}), "day: not read (an answer this Docket doesn't know)"),
        (RuntimeError("Kovács Ödön's day"), "day: not read (RuntimeError)"),
    ],
)  # fmt: skip
def test_a_slow_service_or_one_that_can_t_read_the_day_is_one_line_to_claude(
    repo: Path, answer: Answer | Exception | None, said: str
) -> None:
    """No answer in 3 s names the tool's read; a refusal says Docket's message, anything else its status or type, never
    an exception's message."""
    out = start(FakeService(repo, answer, slow=answer is None), repo)
    assert day_lines(out) == [said]
    assert said not in out["systemMessage"]
    assert "Kov" not in json.dumps(out)


def test_the_service_down_names_the_command_that_reads_the_vault(repo: Path) -> None:
    service = FakeService(repo)
    out = start(service, repo, status(running=False))
    assert context(out)[-1] == "day: the service is down; docket ledger day reads the vault"
    assert service.asked == []
    assert "docket ledger day" not in out["systemMessage"]


def test_a_failed_status_check_asks_no_day(repo: Path) -> None:
    def broken() -> ServiceStatus:
        raise OSError("no such task")

    service = FakeService(repo)
    out = hooks.session_start(broken, {}, service)
    assert context(out) == ["docket: the status check failed (OSError)"]
    assert service.asked == []


def test_more_status_lines_than_the_room_leave_the_day_none(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeService(repo)
    many = [hooks.Line(f"docket: line {n}", False) for n in range(50)]
    monkeypatch.setattr(hooks, "_lines", lambda st, environ: many)
    start(service, repo)
    assert service.asked == [0]


def test_the_command_asks_the_running_service_for_the_day_with_the_token_and_3_s(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command's own call, on a fake client: the day's route with the room, the token, the service's port and the
    hook's 3 s. The token that can't be read still asks, and the route answers 401."""
    monkeypatch.setattr(install, "status", lambda installer, tokens: status())
    monkeypatch.setenv(hooks.PROJECT_VARIABLE, str(repo))
    asked: list[tuple[str, str, str | None, int | None, float]] = []
    body = {"today": "2026-10-20", "repo": str(repo), "counts": COUNTS, "lines": LINES}

    def call(method: str, path: str, *, token: str | None = None, at: int | None = None, timeout: float = 5) -> Answer:
        asked.append((method, path, token, at, timeout))
        return Answer(200, body)

    monkeypatch.setattr(client, "call", call)
    assert main(["hook", "session-start"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert asked == [("GET", f"/api/day?room={hooks.LINES - 2}", None, 8741, hooks.DAY_SECONDS)]
    assert day_lines(out) == [line["text"] for line in LINES]
