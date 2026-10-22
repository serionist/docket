"""The Staging page's JSON routes, signed in (DR-0380, DR-0382, DR-0383, DR-0784, DR-0785): what waits by source and
age, a record opened and shown as promotion would write it, cuts previewed and classified, private dropped, promotion,
the held part's commands, the Guard, the session and the body on every write, and nothing of a record where it
shouldn't be. The page itself is the app's document; its links are the URLs these answers carry."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from docket_webkit import OURS, TOKEN
from staging_kit import PHONE, TALK, key_of, mail, signed_in, staging_vault
from starlette.testclient import TestClient

from docket.errors import DocketError
from docket.ledger import calendar
from docket.service import state as service_state
from docket.service.app import ServiceApp
from docket.sources import local
from docket.vault import holds, releaselog
from docket.vault.config import VaultConfig
from docket.vault.cuts import CUT_MARKER, Cut
from docket.vault.gate import Gate
from docket.vault.releaselog import LoggedCut, Release
from docket.vault.staging import Staging
from docket.vault.vault import Vault
from docket.web import staging as staging_routes
from docket.web import webapi

API = "/api/web/staging"
CLASSIFY = f"{API}/classify"
PROMOTE = f"{API}/promote"
SAME = {"Origin": OURS, "Sec-Fetch-Site": "same-origin"}
TRANSCRIPT = "calls:rec-1/transcript"
MAIL_RID = "calls:mail-1"


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return staging_vault(tmp_path)


@pytest.fixture
def client(vault: Vault) -> Iterator[TestClient]:
    with signed_in(vault) as found:
        yield found


def listed(client: TestClient, query: str = "", status: int = 200) -> dict[str, Any]:
    got = client.get(f"{API}?{query}" if query else API)
    assert got.status_code == status, got.text
    assert got.headers["cache-control"] == "no-store"
    return got.json()


def opened(client: TestClient, vault: Vault, rid: str, extra: str = "", status: int = 200) -> dict[str, Any]:
    got = client.get(f"{API}/panel?open={key_of(vault, rid)}{extra}")
    assert got.status_code == status, got.text
    return got.json()


def panel_of(client: TestClient, vault: Vault, rid: str, extra: str = "") -> dict[str, Any]:
    found = opened(client, vault, rid, extra)["panel"]
    assert found is not None
    return found


def rows(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for g in view["groups"] for r in g["rows"]]


def body(panel: dict[str, Any], label: str = "work", **chosen: list[str]) -> dict[str, Any]:
    """A classification as the page posts it: the panel's record and what it showed, with what is ticked and typed."""
    picks: dict[str, list[str]] = {"u": [], "line": [], "cut": []} | chosen
    return {"key": panel["key"], "label": label, **picks, "seen": panel["seen"]}


def classify(client: TestClient, sent: dict[str, Any], status: int = 200) -> dict[str, Any]:
    got = client.post(CLASSIFY, json=sent, headers=SAME)
    assert got.status_code == status, got.text
    return got.json()


def staged(vault: Vault) -> dict[str, dict[str, Any]]:
    root = vault.root / "staging"
    return {
        json.loads(p.read_text(encoding="utf-8"))["record"]["rid"]: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(root.glob("*/*.json"))
    }


def raw(vault: Vault) -> dict[str, Any]:
    return {r.rid: r for _, _, r in vault.rawlog.iter_records()}


# -- the list --------------------------------------------------------------------------------------------------------


def test_what_waits_shows_by_source_and_age_with_titles_and_no_text(client: TestClient) -> None:
    page = client.get("/staging")
    assert page.status_code == 200 and 'id="root"' in page.text
    read = listed(client)
    view = read["view"]
    assert read["canonical"] == "/staging" and read["notice"] is None
    assert view["lead"] == "3 records wait at the gate: 2 for you to classify, 1 ready to promote"
    assert [r["title"] for r in rows(view)] == ["Supplier call", "The offer", "Day rates"]
    assert [r["line"] for r in rows(view)] == [
        "a mixed source: work or private?",
        "a mixed source: work or private?",
        "a work source: it goes at the next promotion",
    ]
    assert rows(view)[0]["when"] == "7 Oct 2026 10:00"
    assert rows(view)[0]["url"] == f"/staging?open={rows(view)[0]['key']}"
    text = json.dumps(read)
    for said in ("weekend", "Ada Example", "10 million", "Northwind", "U0ADA"):
        assert said not in text
    assert view["ready"] == [{"source": "memo", "n": 1}]
    assert [c["label"] for c in view["source_chips"]] == ["All sources", "calls", "memo"]
    assert [r["title"] for r in rows(listed(client, "source=memo")["view"])] == ["Day rates"]
    narrowed = listed(client, "state=pending&k=1")
    assert [r["title"] for r in rows(narrowed["view"])] == ["Supplier call"]
    assert narrowed["view"]["more"] == "/staging?state=pending&k=51"
    assert narrowed["canonical"] == "/staging?state=pending&k=1"


def test_a_value_the_url_can_t_hold_is_refused_in_its_words(client: TestClient) -> None:
    assert listed(client, "k=0", status=400)["error"].startswith("k is a whole number")
    assert listed(client, "state=done", status=400)["error"].startswith("state is pending or ready")
    bad = client.get(f"{API}/panel?open=nope")
    assert bad.status_code == 400 and bad.json()["error"].startswith("open is a staged record's key")
    zero = client.get(f"{API}/panel?u=0")
    assert zero.status_code == 400 and zero.json()["error"].startswith("u is a number from 1")


def test_a_key_nothing_is_staged_under_says_so(client: TestClient) -> None:
    got = client.get(f"{API}/panel?open={'0' * 32}")
    assert got.status_code == 200
    read = got.json()
    assert read["panel"] is None
    assert read["missing"]["note"].startswith("Nothing is staged as 000000000000 any more")
    assert read["missing"]["close"] == "/staging"


def test_the_url_reads_one_canonical_form_and_says_a_write_once(client: TestClient, vault: Vault) -> None:
    key = key_of(vault, TRANSCRIPT)
    read = opened(client, vault, TRANSCRIPT, "&u=3&cut=0:50-0:55&u=1-2&source=calls")
    assert read["canonical"] == f"/staging?source=calls&open={key}&u=1-2&u=3&cut=0:50-0:55"
    done = listed(client, f"done=work-{key[:12]}&state=pending")
    assert done["canonical"] == "/staging?state=pending"
    assert done["notice"] == f"{key[:12]} classified work: it goes to raw at the next promotion."


# -- an open record --------------------------------------------------------------------------------------------------


def test_a_transcript_opens_with_its_utterances_and_a_mail_with_its_lines(client: TestClient, vault: Vault) -> None:
    panel = panel_of(client, vault, TRANSCRIPT)
    assert (panel["title"], panel["kind"], panel["when"]) == ("Supplier call", "transcript", "7 Oct 2026 10:00")
    assert [u["span"] for u in panel["utterances"]] == [
        "00:00-00:12",
        "00:12-00:31",
        "00:30-00:46",
        "00:45-00:46",
        "00:46-01:00",
    ]
    assert (panel["utterances"][1]["speaker"], panel["utterances"][1]["text"]) == (
        "Ben Sample",
        "First the weekend plans.",
    )
    assert panel["close"] == "/staging" and panel["back"] == f"/staging?open={panel['key']}"
    assert panel["confirm_url"] == f"/staging?open={panel['key']}&confirm=private"
    lines = panel_of(client, vault, MAIL_RID)["lines"]
    assert [line["offset"] for line in lines][:3] == [0, 11, 12]


def test_the_version_in_raw_that_was_cut_shows_beside_a_new_one(client: TestClient, vault: Vault) -> None:
    gate = Gate(vault)
    gate.classify(key_of(vault, MAIL_RID), "work", [Cut(start=0, end=10)])
    gate.promote()
    gate.stage([mail(text="Hello again,\nthe offer is now 9 million.")])
    panel = panel_of(client, vault, MAIL_RID)
    assert panel["why_line"].startswith("an earlier version was decided by hand")
    assert CUT_MARKER in panel["cut_before"]["text"]


def test_a_record_on_hold_shows_no_title_and_no_text(client: TestClient, vault: Vault) -> None:
    holds.add(vault, "calls", ["rec-1"])
    read = listed(client)
    assert "Supplier call" not in json.dumps(read)
    held = next(r for r in rows(read["view"]) if r["rid"] == TRANSCRIPT)
    assert held["on_hold"] and held["title"] == ""
    assert held["line"].startswith("on hold: it stops every ingest of calls")
    panel = panel_of(client, vault, TRANSCRIPT)
    assert "Supplier call" not in json.dumps(panel) and "weekend" not in json.dumps(panel)
    assert (panel["on_hold"], panel["utterances"], panel["can_work"]) == (True, None, False)
    assert read["held"][0]["on_hold"] == 1


def test_a_rule_turned_on_since_intake_applies_in_the_list_and_the_panel(client: TestClient, vault: Vault) -> None:
    Gate(vault).stage([mail("mail-2", title=f"Ring {PHONE}", text=f"My number: {PHONE}\nUntil Friday.")])
    config = VaultConfig.load(vault.root / "vault.yaml")
    config.redaction.phone = True
    (vault.root / "vault.yaml").write_text(config.dump(), encoding="utf-8", newline="\n")
    read = listed(client)
    assert PHONE not in json.dumps(read)
    assert "Ring [redacted:phone]" in [r["title"] for r in rows(read["view"])]
    panel = panel_of(client, vault, "calls:mail-2")
    assert PHONE not in json.dumps(panel)
    assert panel["notes"][0].startswith("The redaction rules changed since this was staged")
    assert panel["lines"][0]["text"] == "My number: [redacted:phone]"
    assert PHONE in staged(vault)["calls:mail-2"]["record"]["text"]


# -- cutting and classifying -----------------------------------------------------------------------------------------


def test_preview_is_the_panel_s_own_read_and_writes_nothing(client: TestClient, vault: Vault) -> None:
    before = staged(vault)
    panel = panel_of(client, vault, TRANSCRIPT, "&u=2")
    assert panel["preview"]["summary"] == "Removes 2 utterances: 00:12-00:31."
    assert [u["n"] for u in panel["utterances"] if u["going"]] == [2, 3]
    assert panel["work_label"] == "Work, with 1 cut"
    assert staged(vault) == before


def test_work_with_ticked_utterances_cuts_at_their_exact_times(client: TestClient, vault: Vault) -> None:
    key = key_of(vault, TRANSCRIPT)
    done = classify(client, body(panel_of(client, vault, TRANSCRIPT), u=["2", "4"]))
    assert done == {
        "key": key,
        "label": "work",
        "notice": f"{key[:12]} classified work: it goes to raw at the next promotion.",
    }
    file = staged(vault)[TRANSCRIPT]
    assert (file["state"], file["classified_by"]) == ("ready", "user")
    assert file["cuts"] == [
        {"start": TALK[1]["start_ms"], "end": TALK[1]["end_ms"], "unit": "ms"},
        {"start": 45_700, "end": 45_701, "unit": "ms"},
    ]
    line = next(r["line"] for r in rows(listed(client)["view"]) if r["rid"] == TRANSCRIPT)
    assert line == "classified work by you, with 2 cuts: it goes at the next promotion"


def test_a_classification_from_a_page_the_record_changed_under_writes_nothing(client: TestClient, vault: Vault) -> None:
    sent = body(panel_of(client, vault, TRANSCRIPT))
    # Another tab, the CLI or Claude cut it since.
    Gate(vault).classify(key_of(vault, TRANSCRIPT), "work", [Cut(start=12_000, end=30_400, unit="ms")], by="claude")
    before = staged(vault)
    refused = classify(client, sent, status=409)
    assert refused["error"] == staging_routes.CHANGED
    assert staged(vault) == before
    # Read again, the record is as it stands: Claude's cut, ticked.
    again = panel_of(client, vault, TRANSCRIPT)
    assert again["why_line"] == "classified work by Claude, with 1 cut: it goes at the next promotion"
    assert [u["n"] for u in again["utterances"] if u["checked"]] == [2]


def test_a_record_promoted_meanwhile_is_refused(client: TestClient, vault: Vault) -> None:
    sent = body(panel_of(client, vault, MAIL_RID))
    gate = Gate(vault)
    gate.classify(key_of(vault, MAIL_RID), "work")
    gate.promote()
    commits = vault.git.run("rev-list", "--count", "HEAD").stdout
    assert "nothing staged as" in classify(client, sent, status=400)["error"]
    assert vault.git.run("rev-list", "--count", "HEAD").stdout == commits


def test_a_lock_another_process_holds_is_refused(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_state, "LOCK_SECONDS", 0.2)
    sent = body(panel_of(client, vault, MAIL_RID))
    before = staged(vault)
    with Vault.open(vault.root).lock:
        refused = classify(client, sent, status=400)
    assert "another docket process holds" in refused["error"]
    assert staged(vault) == before


def test_a_selection_or_label_the_record_can_t_take_writes_nothing(client: TestClient, vault: Vault) -> None:
    panel = panel_of(client, vault, TRANSCRIPT)
    before = staged(vault)
    for sent, said in [
        (body(panel, "maybe"), "label is work or private"),
        (body(panel, u=["9"]), "u names utterance 9, and this transcript has 5"),
        (body(panel, cut=["1-4"]), "cut a time, such as 12:30-14:05, not characters"),
        (body(panel, cut=["12:30"]), "cut 12:30: a cut is <start>-<end>"),
        (body(panel, "private", u=["1"]), "cuts go with work"),
        (body(panel, u=["0"]), "u is a number from 1"),
        (body(panel) | {"key": "nope"}, "open is a staged record's key"),
    ]:
        assert said in classify(client, sent, status=400)["error"], sent
    assert staged(vault) == before


def test_a_body_the_page_didn_t_make_is_refused_before_the_writer(client: TestClient, vault: Vault) -> None:
    panel = panel_of(client, vault, TRANSCRIPT)
    before = staged(vault)
    for sent in (
        body(panel) | {"by": "claude"},
        {k: v for k, v in body(panel).items() if k != "seen"},
        body(panel) | {"seen": {"state": "pending"}},
        body(panel) | {"u": "2"},
    ):
        assert client.post(CLASSIFY, json=sent, headers=SAME).status_code == 422, sent
    assert client.post(PROMOTE, json={"source": "memo", "all": True}, headers=SAME).status_code == 422
    form = SAME | {"Content-Type": "application/x-www-form-urlencoded"}
    assert client.post(CLASSIFY, content=b"label=work", headers=form).status_code == 415
    big = SAME | {"Content-Type": "application/json"}
    assert client.post(CLASSIFY, content=b"x" * 300_000, headers=big).status_code == 413
    assert client.get(CLASSIFY).status_code == 405
    assert client.get(PROMOTE).status_code == 405
    # The form routes of the server's own page are gone with it.
    assert client.post("/staging/classify", data={"label": "work"}, headers=SAME).status_code == 404
    assert client.get("/staging/panel").status_code == 404
    assert staged(vault) == before


def test_private_asks_once_more_then_drops_with_no_trace_in_the_answer_or_the_log(
    client: TestClient, vault: Vault, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    key = key_of(vault, MAIL_RID)
    asked = panel_of(client, vault, MAIL_RID, "&confirm=private")
    assert asked["confirm"] and asked["kind"] == "mail"
    done = classify(client, body(asked, "private"))
    assert done["notice"] == f"{key[:12]} dropped as private: a later pull of the same version is skipped."
    assert MAIL_RID not in staged(vault)
    decided = (vault.root / "staging" / "_decided.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(decided) == 1 and key in decided[0]
    after = json.dumps(done) + json.dumps(listed(client))
    for said in ("The offer", "10 million"):
        assert said not in after
        assert said not in caplog.text


def test_promote_writes_and_commits_and_brings_the_index_up_to_date(client: TestClient, vault: Vault) -> None:
    app = cast(ServiceApp, client.app)
    pokes: list[int] = []
    poke = app.service.updater.poke
    app.service.updater.poke = lambda: pokes.append(1) or poke()  # type: ignore[method-assign]
    Gate(vault).classify(key_of(vault, MAIL_RID), "work")
    # The last promotion held this one back: it says why, in the list.
    gate = Gate(vault)
    shown = gate.show(key_of(vault, TRANSCRIPT))
    gate.classify(shown.entry.key, "work")
    Staging(vault.root, vault.home).write(
        gate.show(shown.entry.key).entry.staged.model_copy(update={"problem": "a cut didn't fit"})
    )
    lines = [r["line"] for r in rows(listed(client)["view"])]
    assert "held back at the last promotion: a cut didn't fit" in lines
    got = client.post(PROMOTE, json={"source": None}, headers=SAME)
    assert got.status_code == 200, got.text
    assert got.json() == {"written": 3, "held": 0, "notice": "Promoted: 3 records written to raw and committed."}
    assert set(raw(vault)) == {TRANSCRIPT, MAIL_RID, "memo:m-1"}
    assert vault.git.untracked("raw") == []
    assert pokes
    one = client.post(PROMOTE, json={"source": "Bad name!"}, headers=SAME)
    assert one.status_code == 400 and "source names a source" in one.json()["error"]


def test_a_promotion_whose_calendar_step_stopped_says_meetings_not_made(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(*_: Any, **__: Any) -> calendar.Stepped:
        raise DocketError("another docket process holds the vault; try again")

    monkeypatch.setattr(calendar, "step", busy)
    # A step that stopped earlier left a record pending: this promotion's step runs for it, and stops too.
    calendar.note_pending(vault, ["calendar:E1"])
    Gate(vault).classify(key_of(vault, MAIL_RID), "work")
    got = client.post(PROMOTE, json={}, headers=SAME)
    assert got.status_code == 200, got.text
    assert got.json()["notice"].endswith(
        "Meetings not made: the calendar step stopped, and its records wait for the next promotion."
    )


def test_promote_with_nothing_ready_or_the_lock_held_writes_nothing(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_state, "LOCK_SECONDS", 0.2)
    with Vault.open(vault.root).lock:
        busy = client.post(PROMOTE, json={"source": "memo"}, headers=SAME)
    assert busy.status_code == 400 and "another docket process holds" in busy.json()["error"]
    assert raw(vault) == {}
    calm = client.post(PROMOTE, json={"source": "calls"}, headers=SAME)
    assert calm.json()["notice"] == "Promoted: 0 records written to raw and committed."
    assert raw(vault) == {}


# -- held recordings -------------------------------------------------------------------------------------------------


def test_held_recordings_are_listed_with_quoted_commands_that_name_the_vault(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    folder = tmp_path / "My Exports" / "calls"
    folder.mkdir(parents=True)
    # What the import folder says never reaches the page.
    (folder / "rec-cut-01.json").write_text(json.dumps({"title": "Weekend at the cabin"}), encoding="utf-8")
    local.record(vault, "calls", folder)
    when = "2026-10-14T09:00:00Z"
    line = Release.model_validate(
        {"id": "rec-cut-03", "cuts": [LoggedCut(start=1000, end=2000)], "keep": [], "at": when, "machine": "laptop-1"}
    )
    releaselog.add(vault, "calls", line)
    # A merge brought rec-cut-03's hold back; an add would leave a released id out, so the list is written by hand.
    (vault.root / holds.HOLDS).mkdir(parents=True, exist_ok=True)
    (vault.root / holds.HOLDS / "calls.txt").write_text("rec-cut-01\nrec-cut-03\n", encoding="utf-8")
    read = listed(client)
    assert "Weekend at the cabin" not in json.dumps(read)
    assert read["view"]["lead"].endswith("; 2 recordings held")
    (held,) = read["held"]
    assert [i["id"] for i in held["ids"]] == ["rec-cut-01", "rec-cut-03"]
    assert held["ids"][1]["again"] == "released 2026-10-14 09:00 UTC on laptop-1, and held again after a merge"
    where = f'--from "{folder.resolve()}" --vault "{vault.root}"'
    assert held["paste"] == "Go through my held calls recordings with /docket:cuts."
    assert [c["text"] for i in held["ids"] for c in i["commands"]] == [
        f'docket hold show calls "rec-cut-01" {where}',
        f'docket hold release calls "rec-cut-01" {where} --cut <mm:ss-mm:ss>',
        f'docket hold release calls "rec-cut-03" {where}',
    ]


def test_a_folder_a_shell_would_act_on_is_left_as_its_placeholder(
    client: TestClient, vault: Vault, tmp_path: Path
) -> None:
    folder = tmp_path / "cost$HOME"
    folder.mkdir()
    local.record(vault, "calls", folder)
    holds.add(vault, "calls", ["rec-cut-01"])
    (held,) = listed(client)["held"]
    command = held["ids"][0]["commands"][0]["text"]
    assert command == f'docket hold show calls "rec-cut-01" --from "<folder>" --vault "{vault.root}"'
    assert held["unfilled"] == [{"name": "folder", "value": str(folder.resolve())}]


def test_a_hold_list_that_doesn_t_read_is_refused(client: TestClient, vault: Vault) -> None:
    (vault.root / holds.HOLDS).mkdir(parents=True, exist_ok=True)
    (vault.root / holds.HOLDS / "calls.txt").write_text("rec-1\nrec 2\n", encoding="utf-8")
    refused = listed(client, status=400)
    assert "line 2" in refused["error"] and "rec 2" not in refused["error"]


def test_no_route_releases_a_hold() -> None:
    """The page names the release's command; no route of the app's takes one (DR-0382)."""
    routes = list(webapi.openapi()["paths"])
    assert [r for r in routes if r.startswith(API)] == [API, f"{API}/panel", CLASSIFY, PROMOTE]
    assert not [r for r in routes if "release" in r or "hold" in r]


# -- the Guard, the session and the body on every write ---------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null", "Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "cross-site"},
        {},
        SAME | {"Authorization": f"Bearer {TOKEN}"},
    ],
)
def test_every_write_passes_the_guard_and_takes_the_session_only(
    client: TestClient, vault: Vault, headers: dict[str, str]
) -> None:
    sent = body(panel_of(client, vault, MAIL_RID))
    before = staged(vault)
    assert client.post(CLASSIFY, json=sent, headers=headers).status_code == 403
    assert client.post(PROMOTE, json={"source": None}, headers=headers).status_code == 403
    assert staged(vault) == before and raw(vault) == {}


def test_the_token_never_reads_the_page_and_a_write_without_a_session_is_refused(
    client: TestClient, vault: Vault
) -> None:
    assert client.get(API, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 403
    sent = body(panel_of(client, vault, MAIL_RID))
    client.cookies.clear()
    assert client.post(CLASSIFY, json=sent, headers=SAME).status_code == 401
    assert client.get(API).status_code == 401
    assert staged(vault)[MAIL_RID]["state"] == "pending"


def test_an_unexpected_failure_answers_500_and_names_nothing_of_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("Kovács Ödön's weekend")

    monkeypatch.setattr(staging_routes, "gather", broken)
    for path in (API, f"{API}/panel"):
        got = client.get(path)
        assert got.status_code == 500
        assert "Something failed in Docket" in got.json()["error"] and "Kovács" not in got.text


def test_a_classification_of_a_record_on_hold_writes_nothing(client: TestClient, vault: Vault) -> None:
    panel = panel_of(client, vault, TRANSCRIPT)
    holds.add(vault, "calls", ["rec-1"])
    before = staged(vault)
    for label in ("work", "private"):
        refused = classify(client, body(panel, label), status=400)
        assert "is on hold" in refused["error"], label
    assert staged(vault) == before
    assert not (vault.root / "staging" / "_decided.jsonl").exists()


def test_work_on_a_cut_record_keeps_its_cuts_unless_they_are_unticked(client: TestClient, vault: Vault) -> None:
    cuts = [Cut(start=12_000, end=30_400, unit="ms"), Cut(start=50_000, end=55_000, unit="ms")]
    Gate(vault).classify(key_of(vault, TRANSCRIPT), "work", cuts, by="claude")
    panel = panel_of(client, vault, TRANSCRIPT)
    assert panel["work_label"] == "Work, with 2 cuts"
    ticked = [str(u["n"]) for u in panel["utterances"] if u["checked"]]
    assert (ticked, panel["typed"]) == (["2"], "0:50-0:55")
    classify(client, body(panel, u=ticked, cut=[panel["typed"]]))
    file = staged(vault)[TRANSCRIPT]
    assert (file["classified_by"], [(c["start"], c["end"]) for c in file["cuts"]]) == (
        "user",
        [(12_000, 30_400), (50_000, 55_000)],
    )
    # Unticked on purpose and read with picked: nothing stays ticked, and Work is whole.
    bare = panel_of(client, vault, TRANSCRIPT, "&picked=1")
    assert not any(u["checked"] for u in bare["utterances"]) and bare["work_label"] == "Work, whole"


def test_a_version_staged_before_its_release_shows_the_released_stretch_as_gone(
    client: TestClient, vault: Vault
) -> None:
    line = Release.model_validate(
        {"id": "rec-1", "cuts": [LoggedCut(start=12_000, end=30_400)], "keep": [], "at": "2026-10-14T09:00:00Z",
         "machine": "laptop-1"}
    )  # fmt: skip
    releaselog.add(vault, "calls", line)
    panel = panel_of(client, vault, TRANSCRIPT)
    assert panel["why_line"].startswith("released with its cuts")
    assert [u["n"] for u in panel["utterances"] if u["released"]] == [2, 3]
    shown = json.dumps(panel)
    assert "First the weekend plans." not in shown and "The cabin by the lake" not in shown


def test_a_badly_typed_cut_is_the_panel_s_problem_with_the_ticks_kept(client: TestClient, vault: Vault) -> None:
    panel = panel_of(client, vault, TRANSCRIPT, "&u=2&cut=12.30-14.05")
    assert panel["problem"].startswith("cut 12.30-14.05: a cut")
    assert [u["n"] for u in panel["utterances"] if u["checked"]] == [2]
    assert panel["typed"] == "12.30-14.05" and not panel["can_work"] and panel["preview"] is None
