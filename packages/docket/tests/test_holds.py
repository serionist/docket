"""Hold lists on temp vaults: reading them failing closed, matching records, adding ids, and two machines adding."""

from __future__ import annotations

from pathlib import Path

import pytest
from docket_testkit import make_record, sh

from docket.errors import DocketError
from docket.vault import holds
from docket.vault.config import VaultConfig
from docket.vault.holds import HOLDS, UNION, Holds, covered
from docket.vault.vault import GITATTRIBUTES, Vault

HELD = "of_00c0ffee00c0ffee00c0ffee00c0ffee"
OTHER = "of_00beef0000beef0000beef0000beef00"
PRIVATE_TITLE = "Dentist with Cleo"


@pytest.fixture
def vault(tmp_path: Path, config: VaultConfig) -> Vault:
    return Vault.init(tmp_path / "vault", config, "test-box")


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
