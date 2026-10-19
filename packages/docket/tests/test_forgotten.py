"""The forgotten list (DR-0579): its line and how it fails closed, what it takes (a rid with everything nested under
it, a version unless a release keeps it, a folder file by its identity with its parts), HEAD's lines and the disk's
in force together, a union merge in a real git merge, and the digest the index is built under (DR-0593). Invented
data only; lines go in through the list's own append."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from docket_testkit import PLAUD, POLISHED, copy_vault, git, git_calls, make_record, plaud_records, template_vault
from forget_kit import AT, KEY, forget, line, stored

from docket.documents import records as documents
from docket.errors import DocketError
from docket.vault import forgotten, releaselog
from docket.vault.forgotten import FORGOTTEN, Forget, Forgotten, Version
from docket.vault.records import canonical_json
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.vault import Vault

BYTES = "c0" * 32
TEXT = "d1" * 32
# A ledger op's id, as the list's ops hold it.
OP_ID = "01M574M6WG8AN9QJRCB9EM7JMK"


@pytest.fixture
def vault(tmp_path_factory: pytest.TempPathFactory, tmp_path: Path) -> Vault:
    return copy_vault(template_vault(tmp_path_factory), tmp_path)


def listed(vault: Vault) -> Forgotten:
    return forgotten.load(vault.root, vault.home)


def folder_file(rid: str = "docs:0123456789ab", **native: object) -> dict[str, object]:
    return {"rid": rid, "source": "docs", "kind": documents.FILE_KIND, "via": documents.VIA, "native": native}


# -- the line --------------------------------------------------------------------------------------------------------


def test_a_line_holds_hashes_only_and_reads_back(vault: Vault) -> None:
    forget(vault, rids=[PLAUD], media=[PLAUD], keys=[KEY], old_paths=["notes/2026-10-14-call.md"])
    text = (vault.root / FORGOTTEN).read_text(encoding="utf-8")
    assert PLAUD not in text and "notes/" not in text
    (found,) = forgotten.parse(text, FORGOTTEN)
    assert found.machine == "test-box" and found.at == AT
    assert json.loads(found.line())["schema_version"] == 1


def test_a_line_names_the_ledger_ops_it_masked_only_when_it_masked_one(vault: Vault) -> None:
    """A forget that masks no op writes the line a Docket from before #212 reads; one that does, the line it fails
    closed on, as it can't mask (DR-0695)."""
    forget(vault, rids=[PLAUD])
    forget(vault, ops=[OP_ID])
    text = (vault.root / FORGOTTEN).read_text(encoding="utf-8")
    first, second = text.splitlines()
    assert "ops" not in json.loads(first) and json.loads(second)["ops"] == [OP_ID]
    assert [found.line() for found in forgotten.parse(text, FORGOTTEN)] == [first, second]
    assert listed(vault).ops == {OP_ID}


def test_a_time_needs_its_zone_and_is_kept_in_utc() -> None:
    with pytest.raises(ValueError, match="time zone"):
        Forget(at=datetime(2026, 10, 18, 9, 0), machine="m")
    assert Forget(at=datetime.fromisoformat("2026-10-18T11:00:00+02:00"), machine="m").at.hour == 9


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        '{"schema_version": 2, "at": "2026-10-18T09:00:00Z", "machine": "m"}',
        '{"rids": ["plaud:of_0123"], "at": "2026-10-18T09:00:00Z", "machine": "m"}',
        '{"files": ["md5:00"], "at": "2026-10-18T09:00:00Z", "machine": "m"}',
        '{"at": "2026-10-18T09:00:00Z", "machine": "m", "later": true}',
        '{"at": "2026-10-18T09:00:00Z", "machine": ""}',
        '{"ops": ["A-0001"], "at": "2026-10-18T09:00:00Z", "machine": "m"}',
    ],
)
def test_a_line_that_isn_t_a_forget_stops_every_reader_named_by_its_number(vault: Vault, bad: str) -> None:
    forget(vault, rids=[PLAUD])
    path = vault.root / FORGOTTEN
    path.write_text(path.read_text(encoding="utf-8") + bad + "\n", encoding="utf-8")
    with pytest.raises(DocketError, match=r"state/forgotten.jsonl: line 2 isn't a forget") as raised:
        listed(vault)
    assert "plaud" not in str(raised.value)
    # A newer Docket's line or a damaged one: the advice names both, and deleting the line is never it.
    said = str(raised.value)
    assert "Written by a newer Docket: update this one" in said
    assert f"damaged: git checkout -- {FORGOTTEN}" in said and "delete" not in said
    with pytest.raises(DocketError, match="line 2"):
        vault.rawlog.read(PLAUD)


def test_a_list_that_isn_t_utf8_fails_closed(vault: Vault) -> None:
    (vault.root / FORGOTTEN).write_bytes(b"\xff\xfe\n")
    with pytest.raises(DocketError, match="isn't UTF-8"):
        listed(vault)


def test_a_list_that_can_t_be_read_fails_closed(vault: Vault) -> None:
    (vault.root / FORGOTTEN).mkdir()
    with pytest.raises(DocketError, match="can't read it"):
        listed(vault)


def _link(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("this system can't make a symlink here")


def test_a_linked_list_or_state_folder_is_refused(vault: Vault, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text("", encoding="utf-8")
    _link(elsewhere, vault.root / FORGOTTEN)
    with pytest.raises(DocketError, match=r"forgotten\.jsonl is a link"):
        listed(vault)
    (vault.root / FORGOTTEN).unlink()
    (vault.root / "state" / ".gitkeep").unlink()
    (vault.root / "state").rmdir()
    real = tmp_path / "real-state"
    real.mkdir()
    _link(real, vault.root / "state")
    with pytest.raises(DocketError, match="state is a link"):
        listed(vault)


def test_a_list_that_doesn_t_read_is_never_added_to(vault: Vault) -> None:
    (vault.root / FORGOTTEN).write_text("not json\n", encoding="utf-8")
    with pytest.raises(DocketError, match="line 1"):
        forget(vault, rids=[PLAUD])
    assert (vault.root / FORGOTTEN).read_text(encoding="utf-8") == "not json\n"


# -- what it takes -----------------------------------------------------------------------------------------------------


def test_a_rid_takes_every_rid_nested_under_it_and_nothing_beside_it(vault: Vault) -> None:
    recording, polished, summary = (stored(r) for r in plaud_records("transaction_polish"))
    forget(vault, rids=[PLAUD])
    found = listed(vault)
    assert found.whole(PLAUD) and found.whole(POLISHED) and found.whole(f"{PLAUD}/whisper")
    assert all(found.covers(r) for r in (recording, polished, summary))
    assert not found.whole(f"{PLAUD}x") and not found.covers(stored(make_record(1)))


def test_a_reply_never_goes_with_its_thread_but_a_channel_takes_its_messages(vault: Vault) -> None:
    starter = stored(make_record(1))
    reply = stored(make_record(2, thread=starter.rid))
    forget(vault, rids=[starter.rid])
    assert listed(vault).covers(starter) and not listed(vault).covers(reply)
    forget(vault, rids=["chat:C0TEST"])
    assert listed(vault).covers(reply)


def test_a_version_goes_alone_and_a_release_keeping_it_beats_it(vault: Vault) -> None:
    first = stored(make_record(1, source="calls", rid="calls:rec-0900", container=None, text="as first sent"))
    second = stored(first.model_copy(update={"text": "sent again", "v": ""}))
    forget(vault, versions=[first])
    found = listed(vault)
    assert found.covers(first) and not found.covers(second)
    assert found.versioned(first.rid) and not found.whole(first.rid)
    # A record without a version is weighed by the version it would have.
    assert found.covers(first.model_copy(update={"v": ""}))
    keep = Release(
        id="rec-0900", cuts=[LoggedCut(start=0, end=1000)], keep=[Kept(rid=first.rid, v=first.v)], at=AT, machine="m"
    )
    releaselog.add(vault, "calls", keep)
    assert not listed(vault).covers(first)


def test_a_folder_file_s_identity_takes_it_at_any_path_and_its_parts_follow(vault: Vault) -> None:
    from docket.vault.records import RawRecord

    def record(fields: dict[str, object], **more: object) -> RawRecord:
        base = {"ts": AT, "fetched": AT, "text": "", **fields, **more}
        return stored(RawRecord.model_validate(base))

    copy = record(folder_file("docs:aaaaaaaaaaaa", sha256=BYTES, size=10))
    page = record({"rid": "docs:aaaaaaaaaaaa/page1", "source": "docs", "kind": "page", "via": documents.VIA})
    note = record(folder_file("docs:bbbbbbbbbbbb", redacted_sha256=TEXT))
    changed = record(folder_file("docs:aaaaaaaaaaaa", sha256="e2" * 32, size=11))
    later = record({"rid": "docs:aaaaaaaaaaaa/page1", "source": "docs", "kind": "page", "via": documents.VIA}, text="2")
    gone = record(folder_file("docs:bbbbbbbbbbbb", gone="2026-10-18"), text="Not read from the folder since today.")
    forget(vault, files=[f"sha256:{BYTES}", f"redacted_sha256:{TEXT}"])
    found = listed(vault)
    assert forgotten.identity_of(copy) == f"sha256:{BYTES}" and found.covers(copy) and found.covers(note)
    # A part carries no identity, so it goes only with the file line before it.
    assert not found.covers(page)
    reading = found.reading()
    taken = [reading.skips(r) for r in (copy, page, changed, later, note, gone)]
    assert taken == [True, True, False, False, True, False]
    assert forgotten.identity_of(gone) is None and forgotten.identity_of(page) is None
    assert forgotten.identity_of(record(folder_file("docs:cccccccccccc"))) is None
    assert forgotten.identity_of(stored(make_record(1))) is None


def test_nothing_listed_takes_nothing(vault: Vault) -> None:
    found = listed(vault)
    assert found == forgotten.EMPTY and not found.acts and found.digest == "" and found.problem is None
    assert not found.covers(stored(make_record(1))) and not found.reading().skips(stored(make_record(1)))
    forget(vault, keys=[KEY], media=[PLAUD])
    assert not listed(vault).acts and not listed(vault).covers(stored(plaud_records()[0]))


def test_media_stays_off_a_listed_rid_and_every_rid_it_sits_under(vault: Vault) -> None:
    forget(vault, rids=[POLISHED], media=[POLISHED, PLAUD])
    found = listed(vault)
    assert found.media_refused(PLAUD) and found.media_refused(POLISHED) and found.media_refused(f"{POLISHED}/x")
    assert not found.media_refused(f"{PLAUD}/summary") and not found.whole(PLAUD)


def test_a_legacy_note_is_known_by_an_old_path_or_its_body_s_hash(vault: Vault) -> None:
    body = "sha256:" + "f3" * 32
    forget(vault, old_paths=["notes/2026-09-13-call.md"], import_hashes=[body])
    found = listed(vault)
    assert found.legacy(["notes/elsewhere.md", "notes/2026-09-13-call.md"], None)
    assert found.legacy(["notes/renamed.md"], body)
    assert not found.legacy(["notes/renamed.md"], None) and not found.legacy([], "sha256:" + "00" * 32)


# -- in force: HEAD's lines and the disk's -----------------------------------------------------------------------------


def test_append_commits_the_list_alone(vault: Vault) -> None:
    commit = forget(vault, rids=[PLAUD])
    assert commit is not None
    assert git(vault.root, "show", "--name-only", "--format=%s", commit).split() == [
        "Forget:", "the", "list", "from", "test-box", FORGOTTEN,
    ]  # fmt: skip
    assert forgotten.commit_pending(vault) is None


def test_a_line_on_disk_head_lacks_acts_at_once_and_the_next_call_commits_it(vault: Vault) -> None:
    forgotten.write_line(vault, line(rids=[PLAUD]))
    assert listed(vault).whole(PLAUD)
    commit = forgotten.commit_pending(vault)
    assert commit is not None and FORGOTTEN in git(vault.root, "show", "--name-only", "--format=", commit)
    assert "Forget: the list committed on test-box" in git(vault.root, "log", "-1", "--format=%s")


def test_a_line_deleted_on_disk_still_acts_is_named_and_the_list_isn_t_committed(vault: Vault) -> None:
    forget(vault, rids=[PLAUD])
    forget(vault, rids=["chat:C0TEST"])
    path = vault.root / FORGOTTEN
    first, second = path.read_text(encoding="utf-8").splitlines()
    path.write_text(f"{second}\n", encoding="utf-8")
    found = listed(vault)
    assert found.whole(PLAUD) and found.whole("chat:C0TEST") and found.missing == 1
    assert found.problem is not None and "lacks 1 line(s)" in found.problem and "git checkout" in found.problem
    with pytest.raises(DocketError, match="lacks 1 line"):
        forgotten.commit_pending(vault)
    path.unlink()
    assert listed(vault).whole(PLAUD) and listed(vault).missing == 2
    assert forgotten.commit_pending(vault) is None
    assert first in git(vault.root, "show", f"HEAD:{FORGOTTEN}")


def test_two_lines_merged_by_union_in_a_real_merge_both_act(vault: Vault) -> None:
    git(vault.root, "checkout", "-q", "-b", "other-box")
    forget(vault, rids=[PLAUD], machine="other-box")
    git(vault.root, "checkout", "-q", "main")
    forget(vault, rids=["chat:C0TEST"])
    git(vault.root, "merge", "-q", "--no-edit", "other-box")
    assert "<<<<<<<" not in (vault.root / FORGOTTEN).read_text(encoding="utf-8")
    found = listed(vault)
    assert found.whole(PLAUD) and found.whole("chat:C0TEST") and found.missing == 0


def test_head_s_list_is_read_once_per_commit(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    with git_calls(monkeypatch) as calls:
        listed(vault)
    # The template's commit has its list cached: no git call while HEAD stays.
    assert calls == []
    forget(vault, rids=[PLAUD])
    forgotten._LOADED.clear()  # pyright: ignore[reportPrivateUsage] - another process, with nothing in memory
    with git_calls(monkeypatch) as calls:
        assert listed(vault).whole(PLAUD)
    head = vault.git.head_id()
    assert calls == [f"cat-file -e {head}:./{FORGOTTEN}", f"cat-file blob {head}:./{FORGOTTEN}"]
    forgotten._LOADED.clear()  # pyright: ignore[reportPrivateUsage]
    vault.write_run("chat", [make_record(40)])
    with git_calls(monkeypatch) as calls:
        assert listed(vault).whole(PLAUD)
    # A commit of Docket's that didn't name the list moves the cache with it.
    assert calls == []


def test_a_cache_that_doesn_t_read_is_a_miss(vault: Vault) -> None:
    forget(vault, rids=[PLAUD])
    (vault.home.private / forgotten.CACHE).write_text("{not json", encoding="utf-8")
    forgotten._LOADED.clear()  # pyright: ignore[reportPrivateUsage]
    assert listed(vault).whole(PLAUD)


def test_a_folder_that_isn_t_a_vault_s_reads_its_disk_alone(tmp_path: Path) -> None:
    root = tmp_path / "loose"
    (root / "state").mkdir(parents=True)
    (root / FORGOTTEN).write_text(line(rids=[PLAUD]).line() + "\n", encoding="utf-8")
    assert forgotten.at(root).whole(PLAUD)
    assert forgotten.load(root, None).missing == 0


def test_a_vault_reads_its_list_from_its_root_alone(vault: Vault) -> None:
    forget(vault, rids=[PLAUD])
    assert forgotten.at(vault.root).whole(PLAUD)


# -- the digest --------------------------------------------------------------------------------------------------------


def test_the_digest_holds_what_the_build_reads_and_never_the_keys(vault: Vault) -> None:
    assert listed(vault).digest == ""
    forget(vault, keys=[KEY])
    assert listed(vault).digest == ""
    forget(vault, rids=[PLAUD])
    digest = listed(vault).digest
    assert len(digest) == 64
    forget(vault, keys=["cd" * 32])
    assert listed(vault).digest == digest
    record = stored(make_record(3))
    for fields in ({"versions": [record]}, {"files": [f"sha256:{BYTES}"]}, {"media": [PLAUD]}, {"old_paths": ["a.md"]}):
        forget(vault, **fields)
        assert listed(vault).digest != digest
        digest = listed(vault).digest
    forget(vault, import_hashes=["sha256:" + "a1" * 32])
    assert listed(vault).digest != digest
    digest = listed(vault).digest
    forget(vault, ops=[OP_ID])
    assert listed(vault).digest != digest


def test_a_list_that_masks_no_op_keeps_the_digest_it_had_before_ops_were_listed() -> None:
    found = forgotten.fold([line(rids=[PLAUD], old_paths=["a.md"])])
    before = {"rids": sorted(found.rids), "versions": [], "files": [], "media": [],
              "old_paths": sorted(found.old_paths), "import_hashes": []}  # fmt: skip
    assert found.digest == hashlib.sha256(canonical_json(before).encode("utf-8")).hexdigest()
    assert forgotten.fold([line(ops=[OP_ID])]).digest != ""


def test_a_merge_that_only_reorders_lines_changes_no_digest(vault: Vault) -> None:
    forget(vault, rids=[PLAUD])
    forget(vault, rids=["chat:C0TEST"], keys=[KEY])
    before = listed(vault).digest
    path = vault.root / FORGOTTEN
    path.write_text("\n".join(reversed(path.read_text(encoding="utf-8").splitlines())) + "\n", encoding="utf-8")
    assert listed(vault).digest == before


def test_the_fold_keeps_each_field_over_every_line() -> None:
    record = stored(make_record(1))
    one, two = line(rids=[PLAUD]), line(versions=[record], keys=[KEY])
    folded = forgotten.fold([one, two])
    assert folded.rids == frozenset(one.rids) and folded.keys == frozenset({KEY})
    assert folded.versions == {(v.rid, v.key) for v in two.versions}
    assert all(isinstance(v, Version) for v in two.versions)
    # With no vault to look a release up in, a listed version is taken.
    assert folded.covers(record)


def test_a_blank_line_is_no_forget_and_a_line_without_its_newline_is_ended(vault: Vault) -> None:
    path = vault.root / FORGOTTEN
    path.write_text("\n" + line(rids=[PLAUD]).line(), encoding="utf-8")
    assert listed(vault).whole(PLAUD)
    forgotten.write_line(vault, line(rids=["chat:C0TEST"]))
    assert [n for n in path.read_text(encoding="utf-8").split("\n") if n][1:] == [line(rids=["chat:C0TEST"]).line()]
    assert listed(vault).whole("chat:C0TEST") and listed(vault).whole(PLAUD)


def test_the_list_s_mtime_and_size_and_head_say_when_to_read_again(vault: Vault) -> None:
    forget(vault, rids=[PLAUD])
    first = listed(vault)
    assert listed(vault) is first
    path = vault.root / FORGOTTEN
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert listed(vault) is not first and listed(vault) == first
