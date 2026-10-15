"""Hold lists on temp vaults: reading them failing closed, matching records, adding ids, and two machines adding."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from docket_testkit import empty_vault, folder_vault, make_record, sh

from docket.errors import DocketError
from docket.vault import holds, releaselog
from docket.vault.config import VaultConfig
from docket.vault.holds import HOLDS, UNION, Holds, covered
from docket.vault.releaselog import LoggedCut, Release
from docket.vault.vault import GITATTRIBUTES, Vault

HELD = "of_00c0ffee00c0ffee00c0ffee00c0ffee"
OTHER = "of_00beef0000beef0000beef0000beef00"
PRIVATE_TITLE = "Dentist with Cleo"


@pytest.fixture
def vault(tmp_path: Path, config: VaultConfig) -> Vault:
    return empty_vault(tmp_path, config)


def write_list(vault: Vault, source: str, data: bytes) -> Path:
    path = vault.root / HOLDS / f"{source}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def link(target: Path, at: Path, *, folder: bool = False) -> None:
    try:
        at.symlink_to(target, target_is_directory=folder)
    except OSError:
        pytest.skip("this system can't make a symlink here")


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def test_a_list_reads_with_a_bom_crlf_spaces_blanks_any_order_and_duplicates(vault: Vault) -> None:
    text = f"﻿{OTHER}  \r\n\r\n   \r\n{HELD}\r\n{OTHER}\r\n"
    write_list(vault, "plaud", text.encode("utf-8"))
    assert holds.read(vault.root, "plaud") == {HELD, OTHER}
    assert holds.read_all(vault.root).of("plaud") == {HELD, OTHER}
    assert holds.read(vault.root, "calls") == frozenset()


def test_a_bad_line_is_named_by_its_number_never_its_text(vault: Vault) -> None:
    write_list(vault, "plaud", f"{HELD}\n{PRIVATE_TITLE}\n".encode())
    for attempt in (lambda: holds.read(vault.root, "plaud"), lambda: holds.read_all(vault.root)):
        with pytest.raises(DocketError, match=r"state/holds/plaud.txt: line 2 isn't an id") as e:
            attempt()
        assert PRIVATE_TITLE not in str(e.value)
        assert "Dentist" not in str(e.value)


def test_a_list_that_isn_t_utf8_or_can_t_be_read_fails_closed(vault: Vault, tmp_path: Path) -> None:
    write_list(vault, "plaud", b"of_\xff\xfe\n")
    with pytest.raises(DocketError, match="isn't UTF-8"):
        holds.read(vault.root, "plaud")
    with pytest.raises(DocketError, match="can't read it"):
        holds.read_file(tmp_path / "missing.txt", "hold.txt")


def test_a_list_whose_name_isn_t_a_source_s_fails_closed(vault: Vault) -> None:
    write_list(vault, "plaud", f"{HELD}\n".encode())
    (vault.root / HOLDS / ".gitkeep").write_text("", encoding="utf-8")
    (vault.root / HOLDS / "notes.d.txt").mkdir()
    assert holds.read_all(vault.root).of("plaud") == {HELD}
    (vault.root / HOLDS / "Bad Name.txt").write_text(f"{HELD}\n", encoding="utf-8")
    with pytest.raises(DocketError, match=r"state/holds/Bad Name.txt: .*isn't a source name"):
        holds.read_all(vault.root)
    with pytest.raises(DocketError, match="isn't a source name"):
        holds.read(vault.root, "../..")


def test_no_folder_means_nothing_held(vault: Vault) -> None:
    assert holds.read_all(vault.root) == Holds()


def test_a_hold_covers_the_rid_the_thread_the_container_and_what_is_under_the_id() -> None:
    ids = frozenset({HELD, "C0TEAM/1791000000.000100"})
    assert covered(f"plaud:{HELD}", "plaud", ids)
    assert covered(f"plaud:{HELD}/transaction_polish", "plaud", ids)
    assert covered(f"plaud:{HELD}/summary/x", "plaud", ids)
    assert not covered(f"plaud:{HELD}x", "plaud", ids)
    assert not covered(f"plaud:{HELD}x/summary", "plaud", ids)
    assert not covered(f"calls:{HELD}", "plaud", ids)
    assert not covered(None, "plaud", ids)
    assert covered("plaud:C0TEAM/1791000000.000100/reply", "plaud", ids)
    assert not covered("plaud:C0TEAM", "plaud", ids)
    lists = Holds({"plaud": frozenset({HELD})})
    base = {"source": "plaud", "kind": "transcript", "container": None}
    assert lists.covers(make_record(rid=f"plaud:{HELD}/transaction", **base))
    assert lists.covers(make_record(rid="plaud:other-shape", thread=f"plaud:{HELD}", **base))
    assert lists.covers(make_record(rid="plaud:other-shape", **{**base, "container": f"plaud:{HELD}"}))
    assert not lists.covers(make_record(rid="plaud:another", **base))
    assert not lists.covers(make_record(rid=f"calls:{HELD}", source="calls", container=None))


def test_adding_sorts_drops_duplicates_and_commits_without_an_id(vault: Vault) -> None:
    assert holds.add(vault, "plaud", [OTHER, HELD, HELD]) == 2
    assert (vault.root / HOLDS / "plaud.txt").read_bytes() == f"{OTHER}\n{HELD}\n".encode()
    assert subjects(vault)[0] == "Holds: plaud, 2 added on test-box"
    log = sh(vault.root, "git", "log", "-1", "--format=%B")
    assert HELD not in log
    assert OTHER not in log
    assert holds.add(vault, "plaud", [HELD]) == 0
    assert len(subjects(vault)) == 2
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""
    with pytest.raises(DocketError, match="isn't an id"):
        holds.add(vault, "plaud", ["two words"])


def test_a_new_vault_merges_hold_lists_by_union(vault: Vault) -> None:
    assert UNION in GITATTRIBUTES
    assert UNION in (vault.root / ".gitattributes").read_text(encoding="utf-8")
    assert holds.add(vault, "plaud", [HELD]) == 1
    changed = sh(vault.root, "git", "show", "--name-only", "--format=", "HEAD").split()
    assert changed == ["state/holds/plaud.txt"]


def test_an_older_vault_gets_the_union_line_in_the_commit_that_first_creates_a_list(vault: Vault) -> None:
    attrs = vault.root / ".gitattributes"
    attrs.write_text(attrs.read_text(encoding="utf-8").replace(UNION + "\n", "").rstrip("\n"), encoding="utf-8")
    sh(vault.root, "git", "-c", "user.name=Ada", "-c", "user.email=ada@example.invalid", "commit", "-qam", "older")
    assert holds.add(vault, "plaud", [HELD]) == 1
    changed = sorted(sh(vault.root, "git", "show", "--name-only", "--format=", "HEAD").split())
    assert changed == [".gitattributes", "state/holds/plaud.txt"]
    lines = attrs.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == UNION
    assert holds.add(vault, "plaud", [OTHER]) == 1
    assert sh(vault.root, "git", "show", "--name-only", "--format=", "HEAD").split() == ["state/holds/plaud.txt"]


def test_a_list_whose_commit_failed_is_committed_by_the_next_add(vault: Vault) -> None:
    # An older vault, so the merge rule is left uncommitted too.
    attrs = vault.root / ".gitattributes"
    attrs.write_text(attrs.read_text(encoding="utf-8").replace(UNION + "\n", ""), encoding="utf-8")
    sh(vault.root, "git", "-c", "user.name=Ada", "-c", "user.email=ada@example.invalid", "commit", "-qam", "older")
    # Another git process (an editor's status refresh) holds the index for a moment.
    lock = vault.root / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    with pytest.raises(DocketError):
        holds.add(vault, "plaud", [HELD])
    lock.unlink()
    assert holds.read(vault.root, "plaud") == {HELD}
    # The same import again: nothing new, and the list still reaches git.
    assert holds.add(vault, "plaud", [HELD]) == 0
    assert subjects(vault)[0] == "Holds: plaud, the list committed on test-box"
    tracked = sh(vault.root, "git", "ls-tree", "-r", "--name-only", "HEAD", "--", "state/holds").split()
    assert tracked == ["state/holds/plaud.txt"]
    assert UNION in sh(vault.root, "git", "show", "HEAD:.gitattributes")
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""


def test_a_user_s_own_gitattributes_edit_stays_theirs(vault: Vault) -> None:
    attrs = vault.root / ".gitattributes"
    attrs.write_text(attrs.read_text(encoding="utf-8") + "*.pdf binary\n", encoding="utf-8")
    assert holds.add(vault, "plaud", [HELD]) == 1
    assert sh(vault.root, "git", "show", "--name-only", "--format=", "HEAD").split() == ["state/holds/plaud.txt"]
    assert sh(vault.root, "git", "status", "--porcelain").strip() == "M .gitattributes"


def test_adding_nothing_to_no_list_writes_nothing(vault: Vault) -> None:
    assert holds.add(vault, "plaud", []) == 0
    assert not (vault.root / HOLDS).exists()
    assert len(subjects(vault)) == 1


def test_two_clones_add_at_once_and_both_read_both_ids(tmp_path: Path, vault: Vault) -> None:
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    vault.git.run("remote", "add", "origin", str(tmp_path / "remote.git"))
    vault.git.run("push", "-q", "origin", "main")
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(tmp_path / "remote.git"), str(tmp_path / "other"))
    other = Vault.open(tmp_path / "other")
    assert holds.add(vault, "plaud", [HELD]) == 1
    assert holds.add(other, "plaud", [OTHER]) == 1
    vault.git.run("push", "-q", "origin", "main")
    result = other.sync()
    assert result.rebased, result.message
    other.git.run("push", "-q", "origin", "main")
    assert vault.sync().rebased
    for clone in (vault, other):
        assert holds.read(clone.root, "plaud") == {HELD, OTHER}
        assert sh(clone.root, "git", "status", "--porcelain").strip() == ""


def test_a_list_that_is_a_link_is_refused_on_read_and_on_add(vault: Vault, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text(f"{OTHER}\n", encoding="utf-8")
    (vault.root / HOLDS).mkdir(parents=True)
    link(elsewhere, vault.root / HOLDS / "plaud.txt")
    with pytest.raises(DocketError, match="is a link"):
        holds.read(vault.root, "plaud")
    with pytest.raises(DocketError, match="is a link"):
        holds.read_all(vault.root)
    with pytest.raises(DocketError, match="is a link"):
        holds.add(vault, "plaud", [HELD])
    assert elsewhere.read_text(encoding="utf-8") == f"{OTHER}\n"


def test_a_holds_folder_that_is_a_link_is_refused_on_read_and_on_add(vault: Vault, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link(outside, vault.root / HOLDS, folder=True)
    with pytest.raises(DocketError, match="state/holds is a link"):
        holds.read_all(vault.root)
    with pytest.raises(DocketError, match="state/holds is a link"):
        holds.add(vault, "plaud", [HELD])
    assert list(outside.iterdir()) == []


def released(vault: Vault, native: str) -> None:
    at = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)
    releaselog.add(vault, "plaud", Release(id=native, cuts=[LoggedCut(start=0, end=1000)], at=at, machine="test-box"))


def test_removing_takes_released_ids_off_and_commits_without_an_id(vault: Vault) -> None:
    holds.add(vault, "plaud", [HELD, OTHER])
    released(vault, HELD)
    # One id without a release line refuses the whole removal: nothing comes off.
    with pytest.raises(DocketError, match=rf"^{OTHER}: no release line, so the hold stays"):
        holds.remove(vault, "plaud", [HELD, OTHER])
    assert holds.read(vault.root, "plaud") == {HELD, OTHER}
    assert holds.remove(vault, "plaud", [HELD]) == 1
    assert holds.read(vault.root, "plaud") == {OTHER}
    assert subjects(vault)[0] == "Holds: plaud, 1 released on test-box"
    assert HELD not in sh(vault.root, "git", "log", "-1", "--format=%B")
    count = len(subjects(vault))
    assert holds.remove(vault, "plaud", [HELD]) == 0
    assert len(subjects(vault)) == count
    assert holds.commit_pending(vault, "calls") is None


def test_an_add_never_holds_a_released_id_again(vault: Vault) -> None:
    holds.add(vault, "plaud", [HELD])
    released(vault, HELD)
    holds.remove(vault, "plaud", [HELD])
    assert holds.add(vault, "plaud", [HELD, OTHER]) == 1
    assert holds.read(vault.root, "plaud") == {OTHER}


def test_a_release_merged_against_another_clone_s_add_brings_the_hold_back_until_the_next_add(
    tmp_path: Path, vault: Vault
) -> None:
    holds.add(vault, "plaud", [HELD])
    sh(tmp_path, "git", "init", "-q", "--bare", "remote.git")
    vault.git.run("remote", "add", "origin", str(tmp_path / "remote.git"))
    vault.git.run("push", "-q", "origin", "main")
    sh(tmp_path, "git", "clone", "-q", "-b", "main", str(tmp_path / "remote.git"), str(tmp_path / "other"))
    other = Vault.open(tmp_path / "other")
    released(vault, HELD)
    assert holds.remove(vault, "plaud", [HELD]) == 1
    # The other clone adds an id that sorts just before the released one, so the two changes meet in one hunk.
    assert holds.add(other, "plaud", [OTHER]) == 1
    vault.git.run("push", "-q", "origin", "main")
    result = other.sync()
    assert result.rebased, result.message
    assert holds.read(other.root, "plaud") == {HELD, OTHER}
    assert set(releaselog.read(other.root, "plaud")) == {HELD}
    assert holds.add(other, "plaud", []) == 0
    assert holds.read(other.root, "plaud") == {OTHER}
    assert subjects(other)[0] == "Holds: plaud, the list committed on test-box"
    assert sh(other.root, "git", "status", "--porcelain").strip() == ""


# -- a hold never comes off in git before its release line ---------------------------------------------------------


def line_on_disk(vault: Vault, source: str, native: str) -> None:
    """A release line written, its commit never made: a stale index.lock, a kill."""
    at = datetime(2026, 10, 15, 9, 12, tzinfo=UTC)
    path = vault.root / releaselog.RELEASES / f"{source}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = Release(id=native, cuts=[LoggedCut(start=0, end=1000)], at=at, machine="test-box").line()
    path.write_text(f"{line}\n", encoding="utf-8")


def in_head(vault: Vault, rel: str) -> str:
    return sh(vault.root, "git", "show", f"HEAD:./{rel}", check=False)


def hold_follows_its_release(vault: Vault, source: str, native: str) -> bool:
    """Whenever HEAD's list lacks the id, HEAD's log has its line."""
    return native in in_head(vault, f"{HOLDS}/{source}.txt") or native in in_head(
        vault, f"state/releases/{source}.jsonl"
    )


def test_an_add_commits_a_release_line_left_on_disk_before_its_hold_comes_off(vault: Vault) -> None:
    holds.add(vault, "plaud", [HELD])
    line_on_disk(vault, "plaud", HELD)
    assert holds.add(vault, "plaud", [OTHER]) == 1
    assert HELD not in in_head(vault, f"{HOLDS}/plaud.txt")
    assert hold_follows_its_release(vault, "plaud", HELD)
    assert subjects(vault)[:2] == [
        "Holds: plaud, 1 added on test-box",
        "Releases: plaud, the log committed on test-box",
    ]


def test_an_add_whose_log_commit_fails_takes_no_hold_off(vault: Vault) -> None:
    holds.add(vault, "plaud", [HELD])
    line_on_disk(vault, "plaud", HELD)
    lock = vault.root / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    with pytest.raises(DocketError):
        holds.add(vault, "plaud", [OTHER])
    lock.unlink()
    assert holds.read(vault.root, "plaud") == {HELD}
    assert HELD in in_head(vault, f"{HOLDS}/plaud.txt")
    assert hold_follows_its_release(vault, "plaud", HELD)


def test_a_log_that_doesn_t_read_is_never_committed_by_an_add_or_a_removal(vault: Vault) -> None:
    holds.add(vault, "plaud", [HELD, OTHER])
    released(vault, HELD)
    rel = f"{releaselog.RELEASES}/plaud.jsonl"
    before = in_head(vault, rel)
    # Broken by hand on disk: every machine that pulled it would fail closed.
    (vault.root / rel).write_text(f"{before}{PRIVATE_TITLE}\n", encoding="utf-8")
    for change in (lambda: holds.add(vault, "plaud", ["of_new"]), lambda: holds.remove(vault, "plaud", [HELD])):
        with pytest.raises(DocketError, match="line 2 isn't a release"):
            change()
        assert in_head(vault, rel) == before
    assert holds.read(vault.root, "plaud") == {HELD, OTHER}
    assert not any("committed" in s for s in subjects(vault)[:1])


def test_a_removal_commits_the_release_line_first(vault: Vault) -> None:
    holds.add(vault, "plaud", [HELD])
    line_on_disk(vault, "plaud", HELD)
    assert holds.remove(vault, "plaud", [HELD]) == 1
    assert subjects(vault)[:2] == [
        "Holds: plaud, 1 released on test-box",
        "Releases: plaud, the log committed on test-box",
    ]
    assert hold_follows_its_release(vault, "plaud", HELD)


def test_a_log_or_a_list_an_ignore_rule_hides_fails_loudly_and_no_hold_comes_off(tmp_path: Path) -> None:
    vault = folder_vault(tmp_path)
    top = vault.home.top
    holds.add(vault, "calls", [HELD])
    # A host's build-output rule hides the vault's release logs.
    (top / ".gitignore").write_text("releases/\n", encoding="utf-8")
    line = Release(id=HELD, cuts=[LoggedCut(start=0, end=1000)], at=datetime.now(UTC), machine="test-box")
    with pytest.raises(DocketError, match="ignored"):
        releaselog.add(vault, "calls", line)
    for take_off in (lambda: holds.remove(vault, "calls", [HELD]), lambda: holds.add(vault, "calls", [OTHER])):
        with pytest.raises(DocketError, match="ignored"):
            take_off()
    assert holds.read(vault.root, "calls") == {HELD}
    assert hold_follows_its_release(vault, "calls", HELD)
    # A hold list such a rule hides doesn't stay out of git unseen either.
    (top / ".gitignore").write_text("holds/\n", encoding="utf-8")
    with pytest.raises(DocketError, match="ignored"):
        holds.add(vault, "chat", [OTHER])
