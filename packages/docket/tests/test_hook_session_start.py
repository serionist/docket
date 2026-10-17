"""`docket hook session-start`: the service's line, the vault's and the port, as Claude Code's SessionStart JSON;
shown to the user only when a line needs them, never a record's content, never a git process, never a failure; and
for Claude alone, whether a curate round is due."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import typer.main
from docket_testkit import empty_vault, host_repo, make_config

from docket import hooks, userconfig
from docket.cli import app, main
from docket.curate import schedule
from docket.errors import DocketError
from docket.service import install
from docket.service.install import ServiceStatus
from docket.vault import curatelog
from docket.vault.curatelog import Line
from docket.vault.vault import Vault


def status(running: bool = True, port: int = 8741, **body: Any) -> ServiceStatus:
    return ServiceStatus(
        installed="task Docket",
        running=running,
        port=port,
        body={"vault": "D:/work/vault", **body} if running else {},
        store="file",
        store_problem=None,
        environment={},
        warning=None,
    )


def start(st: ServiceStatus, environ: dict[str, str] | None = None) -> dict[str, Any]:
    return hooks.session_start(lambda: st, environ or {})


def context(out: dict[str, Any]) -> str:
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    return str(out["hookSpecificOutput"]["additionalContext"])


def folder_vault(tmp_path: Path) -> Vault:
    tmp_path.mkdir(parents=True, exist_ok=True)
    vault = Vault.init(host_repo(tmp_path) / "vault", make_config("folder"), "test-box")
    userconfig.set_default_vault(vault.root)
    return vault


def stage_files(vault: Vault, n: int, text: str = "Morning, Ben.") -> None:
    folder = vault.root / "staging" / "calls"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"{i:032x}.json").write_text(json.dumps({"title": text}), encoding="utf-8")


def test_a_healthy_start_tells_claude_and_shows_the_user_nothing(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    out = start(status(index={"exists": True}))
    assert "systemMessage" not in out
    assert context(out).splitlines() == [
        "docket: running on 127.0.0.1:8741, vault D:/work/vault",
        f"vault: {vault.root}, nothing waiting in staging",
    ]


def test_the_service_down_and_no_vault_yet_both_reach_the_user() -> None:
    out = start(status(running=False))
    lines = [
        "docket: not running (installed as task Docket); see `docket service status`",
        "docket: no vault yet; /docket:setup makes one",
    ]
    assert context(out).splitlines() == lines
    assert out["systemMessage"].splitlines() == lines


def test_records_waiting_in_staging_are_counted_never_read(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    stage_files(vault, 3, text="Dentist with Cleo")
    out = start(status())
    waiting = f"vault: {vault.root}, 3 record(s) wait in staging; `docket staging list --vault {vault.root}`"
    assert waiting in out["systemMessage"].splitlines()
    assert "Dentist" not in json.dumps(out)


def test_the_service_s_vault_comes_before_the_default(tmp_path: Path) -> None:
    folder_vault(tmp_path / "a")
    served = empty_vault(tmp_path / "b", make_config())
    userconfig.set_service(vault=served.root)
    assert f"vault: {served.root}, nothing waiting" in context(start(status()))


def test_the_service_s_own_vault_problem_and_index_reach_claude(tmp_path: Path) -> None:
    folder_vault(tmp_path)
    stopped = "a convert of D:/work/vault stopped part way; run `docket vault convert` to finish it"
    out = start(status(vault_problem=stopped, index={"exists": False}))
    assert f"vault: {stopped}" in out["systemMessage"].splitlines()
    assert "index: not built yet; the first search builds it" in context(out).splitlines()
    stale = start(status(index={"exists": True, "stale": "the languages changed"}))
    assert "index: the languages changed; the next search rebuilds it" in context(stale).splitlines()
    assert "systemMessage" not in stale


def test_doc_lines_citing_changed_lines_are_counted_for_claude(tmp_path: Path) -> None:
    folder_vault(tmp_path)
    out = start(status(index={"exists": True, "flagged": 3, "unapplied": 1}))
    said = "docs: 3 line(s) cite lines that changed, 1 correction line(s) not applied; `docket doc stale`"
    assert said in context(out).splitlines()
    assert "systemMessage" not in out
    quiet = start(status(index={"exists": True, "flagged": 0, "unapplied": 0}))
    assert not any(line.startswith("docs:") for line in context(quiet).splitlines())
    # A body that isn't a count says nothing rather than something wrong.
    odd = start(status(index={"exists": True, "flagged": "3", "unapplied": True}))
    assert not any(line.startswith("docs:") for line in context(odd).splitlines())


def test_a_vault_yaml_that_won_t_load_and_a_vault_that_s_gone(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    (vault.root / "vault.yaml").write_text("primary_machine: [\n", encoding="utf-8")
    assert "not valid YAML" in start(status())["systemMessage"]
    userconfig.set_default_vault(tmp_path / "moved")
    gone = start(status())["systemMessage"].splitlines()
    assert f"vault: none at {tmp_path / 'moved'}; /docket:setup, or `docket service install --vault <folder>`" in gone


def test_the_port_claude_code_connects_to_is_checked(tmp_path: Path) -> None:
    folder_vault(tmp_path)
    wrong = start(status(port=8750))
    hint = "docket: Claude Code connects to port 8741, the service's port is 8750; set DOCKET_PORT=8750 where"
    assert any(line.startswith(hint) for line in wrong["systemMessage"].splitlines())
    assert "systemMessage" not in start(status(port=8750), {"DOCKET_PORT": "8750"})
    assert "systemMessage" in start(status(), {"DOCKET_PORT": "8750"})


def test_a_warning_and_a_token_that_can_t_be_read_reach_the_user(tmp_path: Path) -> None:
    folder_vault(tmp_path)
    locked, warning = "the keyring is locked; run `docket service token --rotate`", "what answers isn't ours"
    st = ServiceStatus("task Docket", False, 8741, {}, "keyring", locked, {}, warning)
    shown = start(st)["systemMessage"].splitlines()
    assert "docket: what answers isn't ours" in shown
    assert f"docket: the token can't be read: {locked}" in shown
    # Before install there is no token yet: the service's own line already says to install.
    fresh = ServiceStatus(None, False, 8741, {}, "file", "there is no service token yet", {}, None)
    assert "token" not in start(fresh)["systemMessage"]


def test_a_failure_is_a_line_with_its_type_never_its_message() -> None:
    def broken() -> ServiceStatus:
        raise RuntimeError("Kovács Ödön said the price")

    out = hooks.session_start(broken, {})
    assert out["systemMessage"] == "docket: the status check failed (RuntimeError)"
    assert "Kov" not in json.dumps(out)


def test_docket_s_own_error_is_said(tmp_path: Path) -> None:
    userconfig.config_path().parent.mkdir(parents=True, exist_ok=True)
    userconfig.config_path().write_text("service: 5\n", encoding="utf-8")

    def refused() -> ServiceStatus:
        raise DocketError("the service's config is broken")

    assert hooks.session_start(refused, {})["systemMessage"] == "docket: the service's config is broken"
    assert "service must be a map" in start(status())["systemMessage"]


def an_entity(vault: Vault) -> None:
    path = vault.root / "entities" / "person" / "cleo-test.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("id: cleo-test\ntype: person\nname: {en: Cleo Test}\n", encoding="utf-8")


def a_round(vault: Vault, days_ago: int) -> None:
    """A round started that many days ago on this machine, with a card decided one minute in and one three in."""
    begun = schedule.now_utc() - timedelta(days=days_ago)
    round_ = schedule.round_id(begun, "test-box")
    for n, minutes in enumerate((1, 3), start=1):
        at = begun + timedelta(minutes=minutes)
        line = Line.model_validate({
            "schema_version": 1, "id": f"{at:%Y%m%dT%H%M%S}-test-box-{n}", "op": "reject", "at": at,
            "machine": "test-box", "by": "user", "via": "cli", "key": f"new:-:name {n}", "round": round_,
        })  # fmt: skip
        path = vault.root / line.rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as log:
            log.write(line.text() + "\n")


def curate_lines(out: dict[str, Any]) -> list[str]:
    return [line for line in context(out).splitlines() if line.startswith("curate:")]


def test_a_round_due_tells_claude_alone_with_the_minutes(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    assert curate_lines(start(status())) == []
    an_entity(vault)
    due = "; mention it once, and run /docket:curate only when asked"
    assert curate_lines(start(status())) == [f"curate: a round is due (none yet){due}"]
    a_round(vault, 10)
    out = start(status())
    year, week, _ = (schedule.now_utc() - timedelta(days=10)).isocalendar()
    since = f"the last started 10 days ago, 3 min of review in {year}-W{week:02d}"
    assert curate_lines(out) == [f"curate: a round is due ({since}){due}"]
    assert "systemMessage" not in out
    a_round(vault, 2)
    assert curate_lines(start(status())) == []


def test_a_curate_log_that_can_t_be_read_is_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder_vault(tmp_path)

    def linked(root: Path) -> curatelog.LogRead:
        raise DocketError("entities/_log is a link")

    monkeypatch.setattr(curatelog, "read_all", linked)
    assert start(status())["systemMessage"].splitlines()[-1] == "curate: entities/_log is a link"


def test_control_characters_are_escaped() -> None:
    out = start(status(vault="D:/x\x1b]52;y", vault_problem="bad\x07"))
    text = json.dumps(out)
    assert "\\\\x1b]52;" in text
    assert "\\u001b" not in text
    assert "\\u0007" not in text


@pytest.mark.parametrize("storage", ["folder", "repo"])
def test_no_git_process_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, storage: str) -> None:
    if storage == "folder":
        vault = folder_vault(tmp_path)
    else:
        vault = empty_vault(tmp_path, make_config())
        userconfig.set_default_vault(vault.root)
    stage_files(vault, 2)
    an_entity(vault)
    a_round(vault, 10)
    started: list[Any] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def is_git(args: Any) -> bool:
        first = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
        return Path(str(first)).stem.lower() == "git"

    def run(args: Any, *a: Any, **k: Any) -> Any:
        if is_git(args):
            started.append(args)
        return real_run(args, *a, **k)

    class Popen(real_popen):  # type: ignore[valid-type, misc]
        def __init__(self, args: Any, *a: Any, **k: Any) -> None:
            if is_git(args):
                started.append(args)
            super().__init__(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    out = start(status())
    assert "2 record(s) wait in staging" in out["systemMessage"]
    assert "curate: a round is due" in context(out)
    assert started == []


def test_the_command_prints_the_json_and_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder_vault(tmp_path)
    monkeypatch.setattr(install, "status", lambda installer, tokens: status(running=False))
    assert main(["hook", "session-start"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["systemMessage"].startswith("docket: not running")

    def broken(installer: Any, tokens: Any) -> ServiceStatus:
        raise OSError("no such task")

    monkeypatch.setattr(install, "status", broken)
    assert main(["hook", "session-start"]) == 0
    assert json.loads(capsys.readouterr().out)["systemMessage"] == "docket: the status check failed (OSError)"


def test_the_hook_loads_no_index_renderer_or_miner() -> None:
    # It runs at the start of every session of every project; a fresh interpreter, so no other test's imports count.
    code = "import sys, docket.hooks; print(' '.join(m for m in sys.modules if m.startswith('docket')))"
    loaded = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert "docket.curate.schedule" in loaded
    heavy = ("docket.index", "docket.render", "docket.questions", "docket.curate.batch", "docket.vault.entitystore")
    assert [m for m in loaded if m.startswith(heavy)] == []


def test_the_hook_group_is_hidden_from_help() -> None:
    # Read from the command itself: the help text's colours and width depend on the terminal.
    group: Any = typer.main.get_command(app)
    assert group.commands["hook"].hidden
    assert not group.commands["source"].hidden
