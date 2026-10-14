"""The gate honours hold lists: a record on hold is never staged, classified or promoted, whatever reached staging
first, and a list that doesn't read stops every gate call."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import make_record, sh

from docket.cli import main
from docket.errors import DocketError
from docket.vault import holds
from docket.vault.config import VaultConfig
from docket.vault.gate import Gate
from docket.vault.holds import HOLDS
from docket.vault.records import RawRecord
from docket.vault.vault import Vault


def call(n: int, **fields: Any) -> RawRecord:
    base: dict[str, Any] = {"rid": f"calls:rec-{n}", "source": "calls", "kind": "recording", "container": None}
    return make_record(n, **{**base, **fields})


def staged_files(vault: Vault) -> dict[str, bytes]:
    root = vault.root / "staging"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def raw_rids(vault: Vault) -> list[str]:
    return sorted(r.rid for _, _, r in vault.rawlog.iter_records())


def head(vault: Vault) -> str:
    return sh(vault.root, "git", "rev-parse", "HEAD").strip()


def test_a_record_staged_on_a_second_clone_before_its_hold_never_reaches_raw(
    tmp_path: Path, config: VaultConfig
) -> None:
    first = Vault.init(tmp_path / "first", config, "test-box")
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    first.git.run("remote", "add", "origin", str(tmp_path / "remote.git"))
    first.git.run("push", "-q", "origin", "main")
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(tmp_path / "remote.git"), str(tmp_path / "second"))
    second = Vault.open(tmp_path / "second")
    gate = Gate(second)
    assert gate.stage([call(1), call(2), call(3)]).pending == 3
    gate.classify("calls:rec-3", "work")
    holds.add(first, "calls", ["rec-1", "rec-3"])
    first.git.run("push", "-q", "origin", "main")
    assert second.sync().rebased
    before, at = staged_files(second), head(second)
    for label in ("work", "private"):
        with pytest.raises(DocketError, match=r"calls:rec-1 is on hold in state/holds/calls.txt.*work or private"):
            gate.classify("calls:rec-1", label)  # type: ignore[arg-type]
    assert staged_files(second) == before
    assert head(second) == at
    assert not (second.root / "staging" / "_decided.jsonl").exists()
    gate.classify("calls:rec-2", "work")
    result = gate.promote()
    assert result.on_hold == 1
    assert [r.written for r in result.runs] == [1]
    assert raw_rids(second) == ["calls:rec-2"]
    left = {e.staged.record.rid: e.staged.state for e in gate.list().entries}
    assert left == {"calls:rec-1": "pending", "calls:rec-3": "ready"}
    assert sh(second.root, "git", "log", "--format=%s").splitlines()[0] == "Raw: calls, 1 record(s) from test-box"


def test_a_held_record_is_never_staged_and_only_counted(tmp_path: Path, config: VaultConfig) -> None:
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    holds.add(vault, "calls", ["rec-1"])
    holds.add(vault, "chat", ["C0TEST/1791000002.000100"])
    gate = Gate(vault)
    result = gate.stage([call(1), call(11, rid="calls:rec-1x"), make_record(2), make_record(3)])
    assert (result.on_hold, result.pending, result.ready) == (2, 1, 1)
    assert {e.staged.record.rid for e in gate.list().entries} == {"calls:rec-1x", "chat:C0TEST/1791000003.000100"}
    assert "rec-1\n" not in "".join(p.read_text(encoding="utf-8") for p in (vault.root / "staging").rglob("*.json"))


def test_a_hold_list_that_doesn_t_read_stops_every_gate_call_until_it_does(tmp_path: Path, config: VaultConfig) -> None:
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    gate = Gate(vault)
    gate.stage([call(1)])
    path = vault.root / HOLDS / "calls.txt"
    path.parent.mkdir(parents=True)
    path.write_text("rec-1\na title with spaces\n", encoding="utf-8")
    calls: list[Callable[[], object]] = [
        lambda: gate.stage([call(2)]),
        lambda: gate.classify("calls:rec-1", "work"),
        gate.promote,
        gate.list,
        lambda: gate.show("calls:rec-1"),
    ]
    for attempt in calls:
        with pytest.raises(DocketError, match=r"calls.txt: line 2 isn't an id"):
            attempt()
    path.write_text("rec-9\n", encoding="utf-8")
    assert gate.classify("calls:rec-1", "work") is not None
    assert gate.promote().runs[0].written == 1


def test_promote_says_how_many_wait_on_hold(
    tmp_path: Path, config: VaultConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    gate = Gate(vault)
    gate.stage([call(1)])
    gate.classify("calls:rec-1", "work")
    holds.add(vault, "calls", ["rec-1"])
    capsys.readouterr()
    assert main(["staging", "promote", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "on hold   1 (left in staging until the hold is released)" in out
    assert "nothing ready" not in out
    assert "rec-1" not in out
