"""The settings in force (DR-0521): HEAD's vault.yaml with what an uncommitted edit only tightens. An edit that
loosens applies once committed; one that keeps more out applies at once; the cache answers with no git call while HEAD
stays. Invented people and values only: Dora Example, CL-1234, hunter2-Example."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, git, make_config, make_record, sh

from docket.cli import main
from docket.documents import scan as scanning
from docket.errors import DocketError
from docket.service import sync
from docket.sources import runner
from docket.vault import git as gitmod
from docket.vault import gitfiles, inforce
from docket.vault.config import CONFIG_FILE, NeverStore, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.gate import Gate
from docket.vault.vault import Vault

SECRET = "hunter2-" + "Example"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    config = make_config()
    config.never_store = NeverStore(people=["Dora Example"])
    config.sources["calls"] = SourceConfig(
        sensitivity="mixed", mapper=".claude/skills/sources/calls/mapper.py", tools=["list_calls", "get_call"]
    )
    config.sources["docs"] = SourceConfig(folder="docs", exclude=["archive/*"])
    return empty_vault(tmp_path, config)


def edit(vault: Vault, change: Callable[[VaultConfig], VaultConfig]) -> None:
    """An edit of vault.yaml by hand, not committed, kept in Docket's form."""
    path = vault.root / CONFIG_FILE
    path.write_text(change(VaultConfig.load(path)).dump(), encoding="utf-8", newline="\n")


def commit(vault: Vault) -> None:
    git(vault.root, "commit", "-qm", "Settings by hand", "--", CONFIG_FILE)


def lists(**update: list[str]) -> Callable[[VaultConfig], VaultConfig]:
    return lambda c: c.model_copy(update={"never_store": c.never_store.model_copy(update=update)})


def rules(**update: object) -> Callable[[VaultConfig], VaultConfig]:
    return lambda c: c.model_copy(update={"redaction": c.redaction.model_copy(update=update)})


def source(name: str, entry: SourceConfig | None) -> Callable[[VaultConfig], VaultConfig]:
    def change(config: VaultConfig) -> VaultConfig:
        kept = {k: v for k, v in config.sources.items() if k != name}
        return config.model_copy(update={"sources": kept if entry is None else {**kept, name: entry}})

    return change


def staged_texts(vault: Vault) -> list[str]:
    return [e.staged.record.text for e in Gate(vault).list().entries]


def test_a_person_taken_off_by_hand_stays_kept_out_until_the_edit_is_committed(vault: Vault) -> None:
    edit(vault, lists(people=[]))
    result = Gate(vault).stage([make_record(1, author="Dora Example")])
    assert result.dropped["never-store"] == 1
    assert Vault.open(vault.root).waiting
    commit(vault)
    assert Gate(vault).stage([make_record(1, author="Dora Example")]).ready == 1
    assert not Vault.open(vault.root).waiting


def test_credentials_turned_off_by_hand_still_redact_until_committed(vault: Vault) -> None:
    edit(vault, rules(credentials=False))
    Gate(vault).stage([make_record(1, text=f"password: {SECRET}")])
    assert SECRET not in staged_texts(vault)[0]
    commit(vault)
    Gate(vault).stage([make_record(2, text=f"password: {SECRET}")])
    assert any(SECRET in t for t in staged_texts(vault))


def test_what_only_keeps_more_out_applies_at_once(vault: Vault) -> None:
    pattern = RedactionPattern(name="client code", pattern=r"CL-\d{4}")
    edit(vault, rules(patterns=[pattern]))
    Gate(vault).stage([make_record(1, text="the order for CL-1234")])
    assert staged_texts(vault) == ["the order for [redacted:client-code]"]
    edit(vault, lists(people=["Dora Example"], keywords=["zebrafinch"]))
    assert Gate(vault).stage([make_record(2, text="the zebrafinch")]).dropped["never-store"] == 1
    edit(vault, source("chat", SourceConfig(sensitivity="private")))
    # The new record at intake, and the one staged earlier by the sweep.
    assert Gate(vault).stage([make_record(3)]).dropped["private"] == 2
    assert staged_texts(vault) == []


def test_an_exclude_added_and_a_tool_removed_apply_at_once(vault: Vault) -> None:
    edit(vault, source("docs", SourceConfig(folder="docs", exclude=["archive/*", "private/*"])))
    edit(
        vault,
        source(
            "calls",
            SourceConfig(sensitivity="mixed", mapper=".claude/skills/sources/calls/mapper.py", tools=["list_calls"]),
        ),
    )
    found = Vault.open(vault.root)
    assert found.config.sources["docs"].exclude == ["archive/*", "private/*"]
    assert found.config.sources["calls"].tools == ["list_calls"]
    assert not found.waiting


def test_a_media_host_or_folder_added_by_hand_waits_and_one_removed_applies_at_once(vault: Vault) -> None:
    calls = vault.config.sources["calls"]
    edit(vault, source("calls", calls.model_copy(update={"media_hosts": ["a.example"], "media_folders": ["~/In"]})))
    commit(vault)
    edit(
        vault,
        source(
            "calls",
            calls.model_copy(update={"media_hosts": ["a.example", "b.example"], "media_folders": ["~/In", "~/Out"]}),
        ),
    )
    found = Vault.open(vault.root)
    assert (found.config.sources["calls"].media_hosts, found.config.sources["calls"].media_folders) == (
        ["a.example"],
        ["~/In"],
    )
    assert found.waiting
    assert inforce.loosens(found.config, found.written)
    edit(vault, source("calls", calls.model_copy(update={"media_hosts": [], "media_folders": []})))
    found = Vault.open(vault.root)
    assert (found.config.sources["calls"].media_hosts, found.config.sources["calls"].media_folders) == ([], [])
    assert not found.waiting


def test_a_source_removed_by_hand_is_refused_at_once(vault: Vault) -> None:
    edit(vault, source("chat", None))
    with pytest.raises(DocketError, match=r"source 'chat' isn't in vault.yaml"):
        Gate(vault).stage([make_record(1)])


def test_what_loosens_waits_and_every_refusal_that_sends_you_there_says_so(vault: Vault) -> None:
    edit(vault, source("memo", SourceConfig(mapper=".claude/skills/sources/memo/mapper.py", tools=["get_memo"])))
    edit(vault, lambda c: c.model_copy(update={"primary_machine": "other-box"}))
    found = Vault.open(vault.root)
    assert "memo" not in found.config.sources
    assert found.config.primary_machine == "test-box"
    assert found.written.primary_machine == "other-box"
    notice = r"; vault.yaml has an edit that isn't in force until you commit it"
    with pytest.raises(DocketError, match=notice):
        Gate(found).stage([make_record(1, rid="memo:m-1", source="memo")])
    with pytest.raises(DocketError, match=notice):
        runner.find_mapper(found, "memo")
    with pytest.raises(DocketError, match=notice):
        sync._checked_source(found, "memo")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(DocketError, match=notice):
        scanning.scan(found, "papers")


def test_status_and_redaction_say_an_edit_waits_naming_no_value(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    edit(vault, lists(people=[]))
    assert main(["vault", "status", "--vault", str(vault.root)]) == 0
    assert main(["redaction", "--vault", str(vault.root)]) == 0
    out = capsys.readouterr().out
    assert "settings vault.yaml has an edit that isn't in force until you commit it" in out
    assert "note     vault.yaml has an edit that isn't in force until you commit it" in out
    assert "Dora" not in out
    commit(vault)
    assert main(["vault", "status", "--vault", str(vault.root)]) == 0
    assert "isn't in force" not in capsys.readouterr().out


def test_a_head_without_a_readable_vault_yaml_stops_docket(vault: Vault) -> None:
    path = vault.root / CONFIG_FILE
    good = path.read_text(encoding="utf-8")
    path.write_text(good + "never_store: [\n", encoding="utf-8", newline="\n")
    commit(vault)
    path.write_text(good, encoding="utf-8", newline="\n")
    with pytest.raises(DocketError, match="isn't in the last commit as a file Docket can read"):
        Vault.open(vault.root)
    git(vault.root, "rm", "-q", "--cached", CONFIG_FILE)
    git(vault.root, "commit", "-qm", "no settings")
    with pytest.raises(DocketError, match="isn't in the last commit as a file Docket can read"):
        Vault.open(vault.root)


def test_a_change_of_storage_not_committed_stops_docket(vault: Vault) -> None:
    """The git dir HEAD is read from depends on storage, so an edit of it can't be weighed until committed."""
    path = vault.root / CONFIG_FILE
    moved = VaultConfig.load(path).model_copy(update={"storage": "folder"})
    path.write_text(moved.dump(), encoding="utf-8", newline="\n")
    with pytest.raises(DocketError, match="isn't in force until you commit it: its storage"):
        inforce.load(vault.root, vault.home)


# -- the cache -------------------------------------------------------------------------------------------------------


def count_shows(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Each git call that reads a committed vault.yaml or asks git for HEAD."""
    calls: list[tuple[str, ...]] = []
    plain = gitmod.Git.run

    def counted(self: gitmod.Git, *args: str, **kwargs: Any) -> Any:
        shows = args[:1] == ("show",) and args[1].endswith(f":./{CONFIG_FILE}")
        if shows or args[:2] == ("rev-parse", "--verify"):
            calls.append(args)
        return plain(self, *args, **kwargs)

    monkeypatch.setattr(gitmod.Git, "run", counted)
    return calls


def test_no_git_call_while_head_stays_and_none_after_docket_s_own_commit(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit(vault, lists(people=["Dora Example", "Ben Sample"]))
    inforce.load(vault.root, vault.home)
    calls = count_shows(monkeypatch)
    gate = Gate(vault)
    for n in range(10):
        gate.stage([make_record(n + 1)])
    assert calls == []
    gate.promote()
    assert inforce.load(vault.root, vault.home).waiting is False
    assert calls == []
    (vault.root / "mine.txt").write_text("the owner's own file\n", encoding="utf-8")
    git(vault.root, "add", "mine.txt")
    git(vault.root, "commit", "-qm", "A file of mine")
    assert inforce.load(vault.root, vault.home).in_force.never_store.people == ["Dora Example", "Ben Sample"]
    assert len(calls) == 1


def test_a_miss_reads_the_commit_whose_id_it_read_never_head(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    first = sh(vault.root, "git", "rev-parse", "HEAD").strip()
    edit(vault, lists(people=["Dora Example", "Ben Sample"]))
    commit(vault)
    (vault.home.private / inforce.CACHE).unlink(missing_ok=True)
    monkeypatch.setattr(gitmod.Git, "head_id", lambda self: first)
    assert "Ben Sample" not in inforce.committed_text(vault.root, vault.home)
    assert json.loads((vault.home.private / inforce.CACHE).read_text(encoding="utf-8"))["commit"] == first


def test_a_cache_that_doesn_t_read_is_a_miss(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    (vault.home.private / inforce.CACHE).write_text("{not json", encoding="utf-8")
    calls = count_shows(monkeypatch)
    assert not inforce.load(vault.root, vault.home).waiting
    assert len(calls) == 1
    inforce.load(vault.root, vault.home)
    assert len(calls) == 1


def test_a_ref_layout_not_read_here_asks_git_for_head_once(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitfiles, "head_id", lambda git_dir: gitfiles.UNKNOWN)
    calls = count_shows(monkeypatch)
    assert not inforce.load(vault.root, vault.home).waiting
    assert [c[0] for c in calls] == ["rev-parse"]


def test_a_repo_with_no_commit_yet_has_no_settings_to_weigh(tmp_path: Path, vault: Vault) -> None:
    git(vault.root, "checkout", "-q", "--orphan", "fresh")
    with pytest.raises(inforce.UnreadableHeadError):
        inforce.committed_text(vault.root, vault.home)


def test_a_commit_of_vault_yaml_by_docket_leaves_a_miss_for_git_to_answer(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    inforce.load(vault.root, vault.home)
    edit(vault, lists(people=["Dora Example", "Ben Sample"]))
    vault.commit([CONFIG_FILE], "Config: by Docket")
    calls = count_shows(monkeypatch)
    assert inforce.load(vault.root, vault.home).in_force.never_store.people == ["Dora Example", "Ben Sample"]
    assert len(calls) == 1
