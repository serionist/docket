"""The gate on a real temp vault: intake, classification, promotion, the sweep, crashes, and leaving no trace."""

from __future__ import annotations

import ast
import logging
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import make_record, sh

from docket.errors import DocketError
from docket.vault.config import RedactionPattern, SourceConfig, VaultConfig
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.gate import Gate
from docket.vault.rawlog import RawLog
from docket.vault.records import RawRecord
from docket.vault.redact import Redactor
from docket.vault.staging import Staging
from docket.vault.vault import Vault

SECRET = "hunter2-" + "Example"
PRIVATE_WORD = "zebrafinch"


@pytest.fixture
def vault(tmp_path: Path, config: VaultConfig) -> Vault:
    config.sources["notes"] = SourceConfig()
    config.sources["diary"] = SourceConfig(sensitivity="private")
    config.sources["old"] = SourceConfig(enabled=False)
    return Vault.init(tmp_path / "vault", config, "test-box")


@pytest.fixture
def gate(vault: Vault) -> Gate:
    return Gate(vault)


def call(n: int, **fields: Any) -> RawRecord:
    return make_record(n, rid=f"calls:rec-{n}", source="calls", kind="recording", **fields)


def note(n: int, **fields: Any) -> RawRecord:
    return make_record(n, rid=f"notes:n-{n}", source="notes", kind="note", **fields)


def rewrite(vault: Vault, change: Callable[[VaultConfig], object]) -> None:
    config = VaultConfig.load(vault.root / "vault.yaml")
    change(config)
    (vault.root / "vault.yaml").write_text(config.dump(), encoding="utf-8", newline="\n")


def in_raw(vault: Vault) -> list[RawRecord]:
    return [record for _, _, record in RawLog(vault.root, vault.machine, vault.lock).iter_records()]


def raw_rids(vault: Vault) -> Counter[str]:
    return Counter(r.rid for r in in_raw(vault))


def key_of(gate: Gate, rid: str) -> str:
    return next(e.key for e in gate.list().entries if e.staged.record.rid == rid)


# -- intake and promotion ----------------------------------------------------------------------------------------


def test_work_goes_through_mixed_waits_and_each_source_gets_one_committed_run(gate: Gate, vault: Vault) -> None:
    result = gate.stage([make_record(1), make_record(2), note(3), call(4)])
    assert (result.ready, result.pending, result.skipped) == (3, 1, 0)
    promoted = gate.promote()
    assert [(r.source, r.written) for r in promoted.runs] == [("chat", 2), ("notes", 1)]
    assert all(r.commit for r in promoted.runs)
    assert raw_rids(vault) == Counter(
        {"chat:C0TEST/1791000001.000100": 1, "chat:C0TEST/1791000002.000100": 1, "notes:n-3": 1}
    )
    assert vault.git.untracked("raw") == []
    listing = gate.list()
    assert [e.staged.record.rid for e in listing.entries] == ["calls:rec-4"]
    assert listing.counts() == Counter({"pending": 1})


def test_promote_can_take_one_source(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1), note(2)])
    assert [r.source for r in gate.promote("notes").runs] == ["notes"]
    assert raw_rids(vault) == Counter({"notes:n-2": 1})
    assert gate.list().counts() == Counter({"ready": 1})


def test_a_private_source_is_dropped_and_only_counted(gate: Gate, vault: Vault) -> None:
    diary = make_record(1, rid="diary:d-1", source="diary")
    result = gate.stage([diary])
    assert result.dropped == Counter({"private": 1})
    assert not (vault.root / "staging" / "diary").exists()


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (make_record(1, rid="unknown:u-1", source="unknown"), "isn't in vault.yaml"),
        (make_record(1, rid="old:o-1", source="old"), "is disabled"),
    ],
)
def test_an_unknown_or_disabled_source_refuses_the_whole_batch(gate: Gate, record: RawRecord, message: str) -> None:
    with pytest.raises(DocketError, match=message):
        gate.stage([make_record(2), record])
    assert gate.list().entries == []


def test_a_record_is_redacted_before_it_is_staged(gate: Gate, vault: Vault) -> None:
    result = gate.stage([make_record(1, text=f"password: {SECRET}", native={"password": SECRET})])
    assert result.redactions == Counter({"password": 2})
    staged = (vault.root / "staging").rglob("*.json")
    assert all(SECRET not in p.read_text(encoding="utf-8") for p in staged)
    gate.promote()
    assert in_raw(vault)[0].text == "password: [redacted:password]"


def test_the_same_version_is_staged_once(gate: Gate) -> None:
    gate.stage([call(1)])
    assert gate.stage([call(1)]).skipped == 1
    assert len(gate.list().entries) == 1


# -- classification ------------------------------------------------------------------------------------------------


def test_classify_work_with_a_cut_then_promote(gate: Gate, vault: Vault) -> None:
    gate.stage([call(1, text="budget review. my holiday plans. next steps.", native={"raw": "my holiday plans"})])
    staged = gate.classify("calls:rec-1", "work", [Cut(start=15, end=33)], by="claude")
    assert staged is not None
    assert (staged.state, staged.classified_by) == ("ready", "claude")
    gate.promote()
    [record] = in_raw(vault)
    assert record.text == f"budget review. {CUT_MARKER}next steps."
    assert record.native == {}
    assert len(gate.staging.decided().keys) == 1


def test_classify_private_drops_it_and_remembers_only_hashes(gate: Gate, vault: Vault) -> None:
    gate.stage([call(1, text=f"about the {PRIVATE_WORD}")])
    assert gate.classify(key_of(gate, "calls:rec-1")[:8], "private") is None
    assert gate.list().entries == []
    assert PRIVATE_WORD not in (vault.root / "staging" / "_decided.jsonl").read_text(encoding="utf-8")
    assert "calls:rec-1" not in (vault.root / "staging" / "_decided.jsonl").read_text(encoding="utf-8")
    assert gate.promote().runs == []


def test_classify_refuses_what_it_can_t_do(gate: Gate) -> None:
    gate.stage([call(1, text="short")])
    with pytest.raises(DocketError, match="cuts go with work"):
        gate.classify("calls:rec-1", "private", [Cut(start=0, end=1)])
    with pytest.raises(DocketError, match="work or private"):
        gate.classify("calls:rec-1", "maybe")  # type: ignore[arg-type]
    with pytest.raises(DocketError, match="has no segments to cut by time"):
        gate.classify("calls:rec-1", "work", [Cut(start=0, end=1000, unit="ms")])
    with pytest.raises(DocketError, match="past the end"):
        gate.classify("calls:rec-1", "work", [Cut(start=0, end=99)])
    assert gate.list().counts() == Counter({"pending": 1})


def test_classify_refuses_a_character_cut_on_a_transcript(gate: Gate, vault: Vault) -> None:
    said = [{"start_ms": 0, "end_ms": 4000, "speaker": "Ben Sample", "text": f"the {PRIVATE_WORD} trip"}]
    gate.stage([call(1, text=f"Ben Sample: the {PRIVATE_WORD} trip", segments=said)])
    with pytest.raises(DocketError, match=r"calls:rec-1 is a transcript.*cut a time"):
        gate.classify("calls:rec-1", "work", [Cut(start=16, end=26)])
    assert gate.list().counts() == Counter({"pending": 1})
    assert gate.promote().runs == []
    assert in_raw(vault) == []


def test_a_ref_is_a_rid_or_a_long_enough_key_prefix(gate: Gate) -> None:
    gate.stage([call(1, text="first"), call(2)])
    gate.stage([call(1, text="second version")])
    with pytest.raises(DocketError, match="at least 6"):
        gate.show("abc")
    with pytest.raises(DocketError, match="nothing staged as calls:rec-9"):
        gate.show("calls:rec-9")
    with pytest.raises(DocketError, match="nothing staged as ffffff"):
        gate.show("ffffff")
    with pytest.raises(DocketError, match="names 2 staged versions"):
        gate.show("calls:rec-1")
    key = key_of(gate, "calls:rec-2")
    assert gate.show(key[:6].upper()).entry.key == key
    assert gate.show(key).cut_before is None


# -- what was decided before ---------------------------------------------------------------------------------------


def test_a_re_pull_of_what_was_promoted_cut_or_dropped_is_skipped(gate: Gate) -> None:
    work = make_record(1, text="call +1 555 010 0199")
    cut = call(2, text="work bit. private bit.")
    private = call(3)
    gate.stage([work, cut, private])
    gate.classify("calls:rec-2", "work", [Cut(start=10, end=22)])
    gate.classify("calls:rec-3", "private")
    gate.promote()
    again = gate.stage([work, cut, private])
    assert (again.skipped, again.ready, again.pending) == (3, 0, 0)
    assert gate.list().entries == []


def test_a_new_version_of_a_record_decided_by_hand_waits_whatever_its_source(gate: Gate) -> None:
    gate.stage([make_record(1, text="work bit. private bit.")])
    gate.classify("chat:C0TEST/1791000001.000100", "work", [Cut(start=10, end=22)])
    gate.promote()
    result = gate.stage([make_record(1, text="work bit. private bit. edited")])
    assert (result.pending, result.ready) == (1, 0)


def test_another_machine_sees_a_cut_through_the_marker_in_raw(gate: Gate, vault: Vault) -> None:
    record = make_record(1, text="work bit. private bit.")
    gate.stage([record])
    gate.classify("chat:C0TEST/1791000001.000100", "work", [Cut(start=10, end=22)])
    gate.promote()
    (vault.root / "staging" / "_decided.jsonl").unlink()
    elsewhere = Gate(Vault.open(vault.root))
    assert elsewhere.stage([record]).pending == 1
    shown = elsewhere.show("chat:C0TEST/1791000001.000100")
    assert shown.cut_before is not None
    assert shown.cut_before.text == f"work bit. {CUT_MARKER}"


def test_a_ready_version_waits_when_another_version_of_it_is_cut(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1, text="work bit. private bit.")])
    gate.stage([make_record(1, text="work bit. private bit. edited")])
    first = next(e for e in gate.list().entries if not e.staged.record.text.endswith("edited"))
    gate.classify(first.key, "work", [Cut(start=10, end=22)])
    gate.promote()
    assert [r.text for r in in_raw(vault)] == [f"work bit. {CUT_MARKER}"]
    [left] = gate.list().entries
    assert (left.staged.state, left.staged.record.text) == ("pending", "work bit. private bit. edited")


def test_a_cut_lands_where_the_user_chose_when_the_rules_change_before_promotion(gate: Gate, vault: Vault) -> None:
    gate.stage([call(1, text="PRJ-1234 notes. my holiday. done.")])
    gate.classify("calls:rec-1", "work", [Cut(start=16, end=28)])
    rewrite(vault, lambda c: c.redaction.patterns.append(RedactionPattern(name="project", pattern=r"PRJ-\d+")))
    gate.promote()
    assert in_raw(vault)[0].text == f"[redacted:project] notes. {CUT_MARKER}done."


# -- the redaction settings ----------------------------------------------------------------------------------------

PHONE = "+1 555 010 0199"


def phone(on: bool) -> Callable[[VaultConfig], object]:
    return lambda c: setattr(c.redaction, "phone", on)


def test_the_defaults_redact_a_password_and_keep_a_phone_number(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1, text=f"password: {SECRET}, call {PHONE}", native={"password": SECRET, "mobile": PHONE})])
    gate.promote()
    [record] = in_raw(vault)
    assert record.text == f"password: [redacted:password], call {PHONE}"
    assert record.native == {"password": "[redacted:password]", "mobile": PHONE}


def test_phone_on_redacts_both_and_credentials_off_keeps_a_password(gate: Gate, vault: Vault) -> None:
    rewrite(vault, phone(True))
    gate.stage([make_record(1, text=f"password: {SECRET}, call {PHONE}", native={"mobile": PHONE})])
    gate.promote()
    rewrite(vault, lambda c: setattr(c.redaction, "credentials", False))
    gate.stage([make_record(2, text=f"password: {SECRET}, call {PHONE}", native={"password": SECRET})])
    gate.promote()
    first, second = in_raw(vault)
    assert (first.text, first.native) == (
        "password: [redacted:password], call [redacted:phone]",
        {"mobile": "[redacted:phone]"},
    )
    assert (second.text, second.native) == (f"password: {SECRET}, call [redacted:phone]", {"password": SECRET})


def test_phone_turned_off_makes_a_record_sent_again_a_new_version_holding_the_number(gate: Gate, vault: Vault) -> None:
    rewrite(vault, phone(True))
    record = make_record(1, text=f"call {PHONE}")
    gate.stage([record])
    gate.promote()
    rewrite(vault, phone(False))
    again = gate.stage([record])
    assert (again.ready, again.skipped) == (1, 0)
    gate.promote()
    assert [r.text for r in in_raw(vault)] == ["call [redacted:phone]", f"call {PHONE}"]


def test_phone_turned_off_sends_classified_records_back_to_pending(gate: Gate) -> None:
    rewrite(gate.vault, phone(True))
    mixed = call(1, text=f"call {PHONE}")
    private = make_record(2, text=f"call {PHONE} about the holiday")
    gate.stage([mixed, private])
    gate.classify("calls:rec-1", "work")
    gate.classify("chat:C0TEST/1791000002.000100", "private")
    gate.promote()
    rewrite(gate.vault, phone(False))
    again = gate.stage([mixed, private])
    assert (again.pending, again.ready, again.skipped) == (2, 0, 0)


# -- the sweep -----------------------------------------------------------------------------------------------------


def test_the_sweep_applies_vault_yaml_as_it_is_now(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1), make_record(2, text=f"about {PRIVATE_WORD}"), note(3), call(4)])
    gate.classify("calls:rec-4", "work")
    rewrite(vault, lambda c: c.never_store.keywords.append(PRIVATE_WORD))
    rewrite(vault, lambda c: c.sources.__setitem__("notes", SourceConfig(sensitivity="private")))
    rewrite(vault, lambda c: c.sources.__setitem__("chat", SourceConfig(sensitivity="mixed")))
    rewrite(vault, lambda c: c.sources.__setitem__("calls", SourceConfig(sensitivity="mixed")))
    result = gate.promote()
    assert result.dropped == Counter({"never-store": 1, "private": 1})
    assert [(r.source, r.written) for r in result.runs] == [("calls", 1)]
    assert [(e.staged.record.rid, e.staged.state) for e in gate.list().entries] == [
        ("chat:C0TEST/1791000001.000100", "pending")
    ]


def test_a_vault_yaml_that_doesn_t_load_stops_the_gate_until_it_does(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1)])
    path = vault.root / "vault.yaml"
    good = path.read_text(encoding="utf-8")
    path.write_text(good.replace("never_store:", "never_stor:"), encoding="utf-8")
    for attempt in (
        lambda: gate.stage([make_record(2)]),
        gate.promote,
        gate.list,
        lambda: gate.show("chat:C0TEST/1791000001.000100"),
        lambda: gate.classify("chat:C0TEST/1791000001.000100", "private"),
    ):
        with pytest.raises(DocketError, match="never_stor"):
            attempt()
    path.unlink()
    with pytest.raises(DocketError, match="can't read it"):
        gate.promote()
    assert in_raw(vault) == []
    path.write_text(good, encoding="utf-8")
    assert gate.promote().runs[0].written == 1


def test_a_record_that_can_t_be_promoted_is_held_and_the_rest_go_on(gate: Gate, vault: Vault) -> None:
    gate.stage([call(1), call(2), make_record(3), note(4)])
    # A time cut on a record without segments, as a staged file from elsewhere could hold; classify would refuse it.
    entry = next(e for e in gate.list().entries if e.staged.record.rid == "calls:rec-1")
    timed = [Cut(start=0, end=1000, unit="ms")]
    gate.staging.write(entry.staged.model_copy(update={"state": "ready", "classified_by": "user", "cuts": timed}))
    gate.classify("calls:rec-2", "work")
    rewrite(vault, lambda c: c.sources.pop("notes"))
    result = gate.promote()
    assert sorted((h.source, h.problem.split(";")[0]) for h in result.held) == [
        ("calls", "calls:rec-1 has no segments to cut by time"),
        ("notes", "source 'notes' isn't in vault.yaml any more"),
    ]
    assert raw_rids(vault) == Counter({"calls:rec-2": 1, "chat:C0TEST/1791000003.000100": 1})
    assert {e.staged.problem is not None for e in gate.list().entries} == {True}
    assert len(gate.promote().held) == 2


# -- no trace ------------------------------------------------------------------------------------------------------


def test_a_never_store_record_leaves_no_trace(gate: Gate, vault: Vault, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rewrite(vault, lambda c: c.never_store.people.append("U0KID"))
    at_intake = make_record(1, author="U0KID", text=f"the {PRIVATE_WORD} said hi")
    later = make_record(2, text=f"a later {PRIVATE_WORD} kingfisher", rid="chat:C0TEST/zz-later")
    assert gate.stage([at_intake, later, make_record(3)]).dropped == Counter({"never-store": 1})
    # The list itself lives in vault.yaml, the user's own words; what it matched must be nowhere.
    rewrite(vault, lambda c: c.never_store.keywords.append("kingfisher"))
    assert gate.promote().dropped == Counter({"never-store": 1})
    for path in vault.root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            for mark in (PRIVATE_WORD, "1791000001", "zz-later"):
                assert mark.encode() not in data, f"{mark} in {path}"
    history = sh(vault.root, "git", "log", "-p", "--all")
    assert PRIVATE_WORD not in history
    assert "zz-later" not in history
    assert PRIVATE_WORD not in caplog.text
    assert "zz-later" not in caplog.text
    assert "1791000001" not in caplog.text


# -- crashes -------------------------------------------------------------------------------------------------------


def fail_once(monkeypatch: pytest.MonkeyPatch, owner: object, name: str, after: int = 0) -> None:
    """The call after `after` good ones stops the machine; the ones after it work again."""
    original = getattr(owner, name)
    calls: list[int] = []

    def crash(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == after + 1:
            raise RuntimeError("the machine stopped here")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, crash)


@pytest.mark.parametrize(
    "crash",
    [
        (Redactor, "record", 0),
        (RawLog, "_write_new", 0),
        # The first commit takes what an earlier promotion left; the second one commits this promotion's runs.
        (Vault, "commit", 0),
        (Vault, "commit", 1),
        (Staging, "remember", 0),
        (Staging, "delete", 0),
    ],
)
def test_a_crash_in_promotion_never_lets_a_pending_record_or_a_secret_into_raw(
    gate: Gate, vault: Vault, monkeypatch: pytest.MonkeyPatch, crash: tuple[object, str, int]
) -> None:
    gate.stage(
        [
            make_record(1, text=f"password: {SECRET}"),
            make_record(2, text="work bit. private bit."),
            call(3, text="still waiting"),
        ]
    )
    gate.classify("chat:C0TEST/1791000002.000100", "work", [Cut(start=10, end=22)])
    fail_once(monkeypatch, *crash)
    with pytest.raises(RuntimeError, match="stopped here"):
        gate.promote()
    raw_text = "".join(p.read_text(encoding="utf-8") for p in (vault.root / "raw").rglob("*.jsonl"))
    assert SECRET not in raw_text
    assert "private bit" not in raw_text
    assert "calls:rec-3" not in raw_text
    gate.promote()
    assert raw_rids(vault) == Counter({"chat:C0TEST/1791000001.000100": 1, "chat:C0TEST/1791000002.000100": 1})
    assert vault.git.untracked("raw") == []
    assert [e.staged.record.rid for e in gate.list().entries] == ["calls:rec-3"]


def test_a_crash_in_classify_private_never_lets_the_record_through(
    gate: Gate, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate.stage([make_record(1, text=f"about the {PRIVATE_WORD}")])
    fail_once(monkeypatch, Staging, "delete")
    with pytest.raises(RuntimeError):
        gate.classify("chat:C0TEST/1791000001.000100", "private")
    assert gate.promote().runs == []
    assert in_raw(vault) == []
    assert gate.list().entries == []


def test_a_repo_docket_can_t_commit_to_stops_promotion_before_anything_is_written(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1)])
    sh(vault.root, "git", "checkout", "-q", "--detach")
    with pytest.raises(DocketError, match="detached HEAD"):
        gate.promote()
    assert list((vault.root / "raw").rglob("*.jsonl")) == []
    assert gate.list().counts() == Counter({"ready": 1})


def test_classify_private_refuses_a_record_already_in_an_uncommitted_run(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1, text=f"about the {PRIVATE_WORD}"), make_record(2)])
    staged = [e.staged.record for e in gate.list().entries]
    (vault.root / "raw" / "stray.txt").write_text("not a run file", encoding="utf-8")
    run = vault.rawlog.write_run("chat", staged).path
    assert run is not None
    with run.open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n")
    with pytest.raises(DocketError, match="git hasn't committed yet: delete that file"):
        gate.classify("chat:C0TEST/1791000001.000100", "private")
    assert len(gate.list().entries) == 2
    run.unlink()
    assert gate.classify("chat:C0TEST/1791000001.000100", "private") is None
    gate.promote()
    assert raw_rids(vault) == Counter({"chat:C0TEST/1791000002.000100": 1})


def test_temp_files_a_crash_left_are_cleared_on_the_next_call(gate: Gate, vault: Vault) -> None:
    tmp = vault.root / ".git" / "docket" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "staging-0123.99.tmp").write_text("{}", encoding="utf-8")
    gate.list()
    assert not (tmp / "staging-0123.99.tmp").exists()


# -- listing -------------------------------------------------------------------------------------------------------


def test_list_filters_sorts_and_reports_unreadable_files(gate: Gate, vault: Vault) -> None:
    gate.stage([call(5), make_record(2), call(1)])
    (vault.root / "staging" / "calls" / "broken.json").write_text("{", encoding="utf-8")
    listing = gate.list()
    assert [e.staged.record.rid for e in listing.entries] == [
        "calls:rec-1",
        "chat:C0TEST/1791000002.000100",
        "calls:rec-5",
    ]
    assert [p.name for p, _ in listing.unreadable] == ["broken.json"]
    assert [e.staged.record.rid for e in gate.list(source="calls", state="pending").entries] == [
        "calls:rec-1",
        "calls:rec-5",
    ]
    assert gate.list(state="ready").counts() == Counter({"ready": 1})
    assert [r.written for r in gate.promote().runs] == [1]


def test_raw_titles_holding_the_marker_text_are_not_cuts(gate: Gate, vault: Vault) -> None:
    gate.stage([make_record(1, title=f"the {CUT_MARKER} button")])
    gate.promote()
    assert gate.stage([make_record(1, title=f"the {CUT_MARKER} button", text="edited")]).ready == 1


# -- the only door to raw ------------------------------------------------------------------------------------------


def test_only_the_gate_writes_the_raw_log() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "docket"
    callers: set[str] = set()
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write_run":
                callers.add(path.relative_to(src).as_posix())
    # vault.py's own Vault.write_run hands over to the raw log; nothing else may call either.
    assert callers == {"vault/gate.py", "vault/vault.py"}
