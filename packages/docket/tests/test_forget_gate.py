"""What the forgotten list keeps out from then on (DR-0579, DR-0595): intake drops a listed record before every other
rule and counts it, the sweep drops a listed staged version with its media, media's screen and reader refuse a listed
rid, the Recording page says it was forgotten, a listed key goes at the reader, at the service's start and from every
export and import, and a held id whose versions in raw were forgotten is released with its cut audio. Lines are written
by hand here, as `docket forget` writes them; invented data only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import AtConsole, copied_with_seals, make_record
from fastapi.testclient import TestClient
from forget_kit import AT, forget, line, stored
from media_kit import CALL, POLISHED, audio, call, in_raw, media_vault, tone

from docket import console, recall
from docket.cli import main
from docket.documents import records as documents
from docket.errors import DocketError
from docket.forget import apply as forget_apply
from docket.media import backup, cutlog, layout, reader
from docket.media import put as media
from docket.media import staged as media_staged
from docket.media.keystore import KeyStore
from docket.service.app import create_app
from docket.service.state import Service, Settings
from docket.vault import forgotten, holds, releaselog
from docket.vault.gate import Gate
from docket.vault.releaselog import Kept, LoggedCut, Release
from docket.vault.staging import Staging, rid_hash
from docket.vault.vault import Vault

PASSPHRASE = "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ"  # noqa: S105 - an invented passphrase
TOKEN = "gh" + "p_" + "a1B2" * 9


def put(vault: Vault, data: bytes | None = None) -> None:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "take").write_bytes(audio(4000) if data is None else data)
    media.put(vault, CALL, path=str(drop / "take"))


def _build_held(folder: Path) -> Path:
    vault = media_vault(folder)
    put(vault)
    in_raw(vault)
    assert layout.stored_files(vault.root, CALL)
    return vault.root


@pytest.fixture
def held(tmp_path: Path) -> Vault:
    """The call in raw with its audio stored and its key here: built once per run, each test a copy (DR-0531)."""
    return Vault.open(copied_with_seals(tmp_path, _build_held, f"{__name__}.held"))


def test_a_copy_of_the_held_template_plays_its_audio_as_a_vault_built_here_does(held: Vault, tmp_path: Path) -> None:
    # The store's seal is kept in the state folder, outside the vault: the copy gets it too, so its key opens.
    here = Vault.open(_build_held(tmp_path / "here"))
    assert reader.state_of(here, CALL) == reader.state_of(held, CALL) == "ok"
    assert reader.read(held, CALL).state == reader.read(here, CALL).state == "ok"
    again = Vault.open(copied_with_seals(tmp_path / "again", _build_held, f"{__name__}.held"))
    assert reader.read(again, CALL).state == "ok"


def stored_key(vault: Vault) -> str:
    (path,) = layout.stored_files(vault.root, CALL)
    key = media_staged.key_of(path)
    assert key is not None
    return key


def forget_call(vault: Vault, *, apply_it: bool = False) -> None:
    """The call forgotten whole, as the forget lists it: its rid, the rids it sits under on media, and its file's key.
    Without `apply_it`, nothing applies it: the line acts on its own."""
    forget(vault, rids=[CALL], media=[CALL], keys=[stored_key(vault)])
    if apply_it:
        forget_apply.apply(vault, KeyStore(vault.home))


# -- intake and the sweep ----------------------------------------------------------------------------------------------


def test_a_record_sent_again_is_dropped_at_intake_and_counted(held: Vault) -> None:
    forget_call(held)
    result = Gate(held).stage(call())
    assert result.dropped["forgotten"] == 2
    assert result.ready == result.pending == result.skipped == 0
    assert Staging(held.root, held.home).scan().entries == []
    # Its rid and the one it sits under stay out of media, as for never-store.
    assert {rid_hash(CALL), rid_hash(POLISHED)} <= media_staged.read_withheld(held.root)


def test_intake_drops_a_version_forgotten_alone_by_its_redacted_version(tmp_path: Path) -> None:
    vault = media_vault(tmp_path, store=False)
    Staging(vault.root, vault.home).delete(Staging(vault.root, vault.home).scan().entries[0].path)
    said = call(text=f"The token is {TOKEN}.")
    Gate(vault).stage(said)
    in_raw(vault)
    redacted = vault.rawlog.read(POLISHED)[-1]
    assert TOKEN not in redacted.text
    forget(vault, versions=[redacted])
    # Sent again as the source gives it: the version raw held is the one redaction made of it, known only once
    # redacted.
    result = Gate(vault).stage(said)
    assert result.dropped["forgotten"] == 1
    # A new version of it comes in.
    assert Gate(vault).stage(call(text="The crane failed its load test.")).dropped == {}


def test_a_staged_version_is_swept_with_its_media(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault)
    key = KeyStore(vault.home).ids()
    assert key and layout.staged_files(vault.root, CALL)
    forget(vault, rids=[CALL], media=[CALL])
    listing = Gate(vault).list()
    assert listing.entries == [] and listing.media == 0
    assert not layout.staged_files(vault.root, CALL)
    assert not layout.sidecar_path(vault.root, CALL).exists()
    assert KeyStore(vault.home).ids() == []
    assert rid_hash(CALL) in media_staged.read_withheld(vault.root)


def test_a_version_forget_sweeps_its_versions_and_withholds_nothing(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    staged = [e.staged.record for e in Staging(vault.root, vault.home).scan().entries]
    forget(vault, versions=staged)
    dropped = Gate(vault).promote().dropped
    assert dropped["forgotten"] == 2
    assert vault.rawlog.read(CALL) == []
    assert media_staged.read_withheld(vault.root) == frozenset()


def test_a_folder_file_s_parts_go_with_it_in_a_later_batch(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    native = {documents.BYTES_HASH: "cd" * 32}
    file = make_record(rid="plaud:aaaaaaaaaaaa", source="plaud", via=documents.VIA, kind="document", native=native)
    part = make_record(rid="plaud:aaaaaaaaaaaa/page1", source="plaud", via=documents.VIA, kind="page", text="Page one.")
    forget(vault, files=[f"{documents.BYTES_HASH}:{'cd' * 32}"])
    gate = Gate(vault)
    assert gate.stage([file]).dropped["forgotten"] == 1
    # The scan's batches break where they fill: a part after its file's batch goes with it all the same.
    assert gate.stage([part]).dropped["forgotten"] == 1
    other = make_record(rid="plaud:bbbbbbbbbbbb/page1", source="plaud", via=documents.VIA, kind="page", text="Other.")
    assert gate.stage([other]).dropped == {}


def test_intake_s_view_of_a_listed_record_says_it_was_forgotten(held: Vault) -> None:
    forget_call(held)
    recording, transcript = call()
    gate = Gate(held)
    assert gate.intake_view(recording) == "forgotten"
    assert gate.intake_view(transcript) == "forgotten"


# -- media ---------------------------------------------------------------------------------------------------------


def test_media_put_on_a_forgotten_rid_is_refused(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    forget(vault, rids=[POLISHED], media=[POLISHED, CALL])
    with pytest.raises(DocketError, match="plaud:rec-a was forgotten: no media is stored on it again"):
        put(vault)
    assert Gate(vault).media_check(CALL) == "plaud:rec-a was forgotten: no media is stored on it again"


def test_a_version_forget_leaves_its_rid_free_for_media(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    forget(vault, versions=vault.rawlog.read(POLISHED))
    assert Gate(vault).media_check(CALL) is None


def test_the_reader_deletes_a_listed_key_and_says_forgotten(held: Vault) -> None:
    key = stored_key(held)
    # Listed alone, as an apply on another clone lists one: the reader meets it before anything opens it.
    forget(held, keys=[key])
    assert reader.state_of(held, CALL) == "forgotten"
    assert not KeyStore(held.home).has(key)
    assert reader.read(held, CALL).state == "forgotten"


def test_the_reader_says_forgotten_for_a_rid_no_media_is_stored_on_again(held: Vault) -> None:
    forget(held, rids=[POLISHED], media=[POLISHED, CALL])
    assert reader.state_of(held, CALL) == "forgotten"
    # A key no line names stays until the apply reads it from the file and lists it.
    assert KeyStore(held.home).has(stored_key(held))


def test_the_service_deletes_every_listed_key_it_holds_at_its_start(held: Vault) -> None:
    key = stored_key(held)
    forget(held, keys=[key])
    app = create_app(Settings(port=8799, vault=held.root, token=lambda: "t" * 32))
    with TestClient(app):
        pass
    assert not KeyStore(held.home).has(key)
    assert Service(Settings(port=8799, vault=None, token=lambda: "t")).drop_forgotten_keys() == 0


def test_the_recording_page_says_the_recording_was_forgotten(held: Vault) -> None:
    from docket.web import recording
    from docket.web.recording_state import RecordingState
    from docket.web.recording_view import NO_AUDIO, build

    forget_call(held, apply_it=True)
    facts = recording.gather(held, RecordingState(rid=CALL))
    assert facts.forgotten
    view = build(facts, RecordingState(rid=CALL))
    assert view.mode == "missing"
    assert "This recording was forgotten" in view.message
    assert NO_AUDIO["forgotten"].startswith("This recording was forgotten")
    assert recording.AUDIO["forgotten"] == "forgotten"


# -- key exports -------------------------------------------------------------------------------------------------------


@pytest.fixture
def fixed(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(backup, "draw", lambda: PASSPHRASE)
    return PASSPHRASE


def test_an_export_leaves_a_listed_key_out_and_an_import_skips_one(
    tmp_path: Path,
    owner: Callable[..., AtConsole],
    fixed: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = media_vault(tmp_path / "a")
    put(first)
    in_raw(first)
    keys = KeyStore(first.home)
    kept = stored_key(first)
    # A second key this clone holds, as a recut's earlier file left one, then listed by a forget elsewhere.
    listed = "cd" * 32
    keys.add(listed, keys.get(kept).identity, "media/plaud/x")  # type: ignore[union-attr]
    target = tmp_path / "keys.age"
    owner("WXYZ")
    # The export before the forget holds both, as a backup made before one does.
    done = backup.export(first, target)
    assert (done.keys, done.forgotten, done.kept) == (2, 0, True)
    forget(first, keys=[listed])
    owner("WXYZ")
    capsys.readouterr()
    assert main(["media", "keys", "export", str(tmp_path / "after.age"), "--vault", str(first.root)]) == 0
    out = capsys.readouterr().out
    assert "keys     1 exported to" in out and "keys     1 forgotten key(s) left out: a forget listed them" in out
    assert keys.exported() == {kept}
    second = media_vault(tmp_path / "b")
    forget(second, keys=[listed])
    monkeypatch.setattr(console, "ask_hidden", lambda prompt: fixed)
    owner()
    assert main(["media", "keys", "import", str(target), "--vault", str(second.root)]) == 0
    assert "keys     1 added, 0 held already, 1 skipped: a forget listed them" in capsys.readouterr().out
    assert KeyStore(second.home).ids() == [kept]
    assert KeyStore(second.home).exported() == {kept}


# -- read and verify ---------------------------------------------------------------------------------------------------


def test_read_and_verify_say_a_listed_rid_was_forgotten(held: Vault) -> None:
    forget_call(held, apply_it=True)
    with pytest.raises(DocketError, match="plaud:rec-a was forgotten: nothing of it is kept"):
        recall.read(held, CALL)
    assert recall.absent(held.root, CALL) == "plaud:rec-a was forgotten"
    assert recall.absent(held.root, "plaud:rec-z") == "plaud:rec-z isn't in the raw log"


# -- a release after a forget --------------------------------------------------------------------------------------


def test_a_held_id_released_after_its_versions_went_takes_its_cut_audio(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    put(vault, tone(20.0))
    in_raw(vault)
    uncut = stored_key(vault)
    holds.add(vault, "plaud", ["rec-a"])
    # What --held lists for it: its versions in raw and its stored file's key, nothing on media.
    forgotten.append(vault, line(versions=[*vault.rawlog.read(CALL), *vault.rawlog.read(POLISHED)], keys=[uncut]))
    forget_apply.apply(vault, KeyStore(vault.home))
    assert vault.rawlog.read(POLISHED) == [] and not layout.stored_files(vault.root, CALL)
    assert not KeyStore(vault.home).has(uncut)
    # The uncut versions sent again while the hold stands are held, never staged.
    assert Gate(vault).stage(call()).on_hold == 2
    recording = stored(call()[0])
    releaselog.add(
        vault,
        "plaud",
        Release(
            id="rec-a",
            cuts=[LoggedCut(start=10_000, end=19_000)],
            keep=[Kept(rid=CALL, v=recording.v)],
            at=AT,
            machine="test-box",
        ),
    )
    holds.remove(vault, "plaud", ["rec-a"])
    result = Gate(vault).stage(call())
    assert result.ready + result.pending == 2 and result.dropped == {}
    for entry in Gate(vault).list(state="pending").entries:
        Gate(vault).classify(entry.key, "work")
    in_raw(vault)
    assert "[cut]" in vault.rawlog.read(POLISHED)[-1].text
    put(vault, tone(20.0))
    in_raw(vault)
    (path,) = layout.stored_files(vault.root, CALL)
    assert media_staged.key_of(path) != uncut
    [line_of] = [one for one in cutlog.read(vault.root, "plaud") if one.key == media_staged.key_of(path)]
    assert line_of.cuts
    assert reader.state_of(vault, CALL) == "ok"


def test_a_gate_call_with_no_list_keeps_nothing_of_the_apply_s(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    Gate(vault).list()
    assert not forget_apply.state_path(vault.home).exists()


def test_a_forgotten_family_is_kept_out_and_a_listed_version_reads_as_forgotten(held: Vault) -> None:
    forget(held, rids=[POLISHED], media=[POLISHED, CALL])
    staging = Staging(held.root, held.home)
    with recall.raw_lookup(held) as lookup:
        found = reader.kept_out(held, CALL, lookup, staging, staging.scan().entries)
    assert found == ("forgotten", f"{CALL} was forgotten: no media is stored on it again")
    recording = held.rawlog.read(CALL)[-1]
    forget(held, versions=[recording])
    assert Gate(held).intake_view(call()[0]) == "forgotten"
