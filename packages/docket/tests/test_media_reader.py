"""The one reader (DR-0551): a rid's media in memory, or the state that says why not, and what the Recording page says
for each state."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from docket_webkit import OURS, PORT, TOKEN, parse
from media_kit import CALL, audio, in_raw, media_vault
from starlette.testclient import TestClient

from docket.errors import DocketError
from docket.media import crypto, layout, reader
from docket.media import put as media
from docket.media.keystore import KeyStore
from docket.service.app import create_app
from docket.service.sessions import COOKIE, Sessions
from docket.service.state import Settings
from docket.vault import holds
from docket.vault.vault import Vault
from docket.web import recording as recording_page
from docket.web.recording_view import NO_AUDIO


def put(vault: Vault) -> bytes:
    drop = media.drop_folder()
    drop.mkdir(parents=True, exist_ok=True)
    data = audio(5000)
    (drop / "take").write_bytes(data)
    media.put(vault, CALL, path=str(drop / "take"))
    return data


def test_each_state_and_the_bytes_when_they_can_be_read(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    assert reader.state_of(vault, CALL) == "not-fetched"
    assert reader.read(vault, CALL) == reader.Media("not-fetched")
    data = put(vault)
    assert reader.state_of(vault, CALL) == "staged"
    in_raw(vault)
    found = reader.read(vault, CALL)
    assert (found.state, found.data, found.ext, found.type) == ("ok", data, "mp3", "audio/mpeg")
    assert reader.Media("held").type is None
    [stored] = layout.stored_files(vault.root, CALL)
    keys = KeyStore(vault.home)
    # The seal gone from this machine: the key is there, and can't be opened.
    info = keys.info()
    assert info is not None
    seal = Path(os.environ["DOCKET_STATE_DIR"]) / "media-stores" / info.id
    hidden = seal.with_name("hidden")
    seal.rename(hidden)
    assert reader.read(vault, CALL).state == "no-key"
    hidden.rename(seal)
    keys.delete(crypto.key_id(stored))
    assert reader.state_of(vault, CALL) == "no-key"
    assert reader.read(vault, CALL).data is None
    # An LFS pointer in place of the file: stored, not downloaded here.
    stored.write_bytes(crypto.LFS_POINTER + b"\noid sha256:0\nsize 5000\n")
    assert reader.state_of(vault, CALL) == "not-downloaded"
    shutil.copy2(stored, stored.with_name(stored.name.replace(".mp3.", ".wav.")))
    with pytest.raises(DocketError, match="has 2 stored media files"):
        reader.state_of(vault, CALL)


def test_a_held_recording_s_audio_waits(tmp_path: Path) -> None:
    vault = media_vault(tmp_path)
    holds.add(vault, "plaud", ["rec-a"])
    assert reader.state_of(vault, CALL) == "held"


@pytest.mark.parametrize(
    "state", ["not-fetched", "staged", "held", "recut", "cut-by-characters", "not-downloaded", "no-key", "stored"]
)
def test_the_recording_page_says_why_it_plays_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    vault = media_vault(tmp_path)
    in_raw(vault)
    named = {shown: found for found, shown in recording_page.AUDIO.items()}
    monkeypatch.setattr(reader, "state_of", lambda found, rid, keys=None, raw=None: named[state])
    sessions = Sessions()
    app = create_app(Settings(port=PORT, vault=vault.root, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS, follow_redirects=False) as client:
        started = sessions.redeem(sessions.new_code())
        assert started is not None
        client.cookies.set(COOKIE, started)
        page = parse(client.get(f"/recording?rid={CALL}").text)
    shown = page.find("section", class_="audio")
    assert shown.attrs["data-why"] == state
    assert NO_AUDIO[state] in shown.text()  # pyright: ignore[reportArgumentType]
