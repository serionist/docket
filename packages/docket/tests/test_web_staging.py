"""The Staging page over HTTP, signed in (DR-0380 to DR-0384): what waits by source and age, a record opened and shown
as promotion would write it, cuts previewed and classified, private dropped, promotion, the held part's commands, the
Guard on every post, and nothing of a record where it shouldn't be."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from docket_webkit import OURS, a11y_problems, html_problems, parse
from staging_kit import PHONE, TALK, key_of, mail, memo, signed_in, staging_vault
from starlette.testclient import TestClient

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
from docket.web.templating import PAGE_CSP

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


def page(client: TestClient, path: str = "/staging", status: int = 200) -> str:
    answer = client.get(path)
    assert answer.status_code == status, answer.text
    assert answer.headers["Content-Security-Policy"] == PAGE_CSP
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.headers["Referrer-Policy"] == "same-origin"
    assert not html_problems(answer.text), path
    assert not a11y_problems(answer.text, whole=not path.startswith("/staging/panel")), path
    return answer.text


def text_of(html: str, cls: str) -> list[str]:
    return [el.text() for el in parse(html).find_all(class_=cls)]


def form(html: str, cls: str = "cut-form") -> dict[str, Any]:
    """A form's hidden fields, as the browser would post them."""
    found = parse(html).find("form", class_=cls)
    return {i.attrs["name"]: i.attrs["value"] for i in found.find_all("input") if i.attrs.get("type") == "hidden"}


def as_shown(html: str) -> dict[str, Any]:
    """The panel's form as the browser posts it untouched: its hidden fields, what is checked, and the typed cuts."""
    found = parse(html).find("form", class_="cut-form")
    fields = form(html)
    fields["u"] = [i.attrs["value"] for i in found.find_all("input", name="u") if "checked" in i.attrs]
    fields["cut"] = found.find("input", name="cut").attrs["value"]
    return fields


def staged(vault: Vault) -> dict[str, dict[str, Any]]:
    root = vault.root / "staging"
    return {
        json.loads(p.read_text(encoding="utf-8"))["record"]["rid"]: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(root.glob("*/*.json"))
    }


def raw(vault: Vault) -> dict[str, Any]:
    return {r.rid: r for _, _, r in vault.rawlog.iter_records()}


def opened(vault: Vault, rid: str, extra: str = "") -> str:
    return f"/staging?open={key_of(vault, rid)}{extra}"


# -- the list --------------------------------------------------------------------------------------------------------


def test_what_waits_shows_by_source_and_age_with_titles_and_no_text(client: TestClient) -> None:
    html = page(client)
    tree = parse(html)
    assert tree.find("h1").text() == "Staging"
    assert tree.find("a", aria_current="page").attrs["href"] == "/staging"
    assert text_of(html, "lead") == ["3 records wait at the gate: 2 for you to classify, 1 ready to promote"]
    assert text_of(html, "record-title") == ["Supplier call", "The offer", "Day rates"]
    assert text_of(html, "why") == [
        "a mixed source: work or private?",
        "a mixed source: work or private?",
        "a work source: it goes at the next promotion",
    ]
    for said in ("weekend", "Ada Example", "10 million", "Northwind", "U0ADA"):
        assert said not in html
    assert text_of(html, "promote-counts") == ["memo 1"]
    assert [b.text() for b in tree.find_all("button") if "Promote" in b.text()] == ["Promote memo"]
    assert text_of(page(client, "/staging?source=memo"), "record-title") == ["Day rates"]
    assert text_of(page(client, "/staging?state=pending&k=1"), "record-title") == ["Supplier call"]
    assert parse(page(client, "/staging?state=pending&k=1")).find("a", class_="button").attrs["href"].endswith("k=51")
    # Its keys are Search's list keys: no key writes.
    dialog = tree.find("dialog", id="keys-dialog")
    assert [k.attrs["data-key"] for k in dialog.find_all("kbd") if "data-key" in k.attrs] == [
        "j",
        "k",
        "Enter",
        "o",
        "Escape",
        "?",
    ]


def test_a_value_the_url_cant_hold_is_the_pages_error(client: TestClient) -> None:
    html = page(client, "/staging?open=nope", status=400)
    assert text_of(html, "notice")[0].startswith("open is a staged record's key")
    assert "nothing staged" not in html.lower()
    panel = page(client, "/staging/panel?u=0", status=400)
    assert text_of(panel, "notice")[0].startswith("u is a number from 1")


def test_a_key_nothing_is_staged_under_says_so(client: TestClient) -> None:
    html = page(client, "/staging?open=" + "0" * 32)
    assert "Nothing is staged as 000000000000 any more" in html
    assert "Nothing is staged as 000000000000 any more" in page(client, "/staging/panel?open=" + "0" * 32)


def test_content_is_data(client: TestClient, vault: Vault) -> None:
    Gate(vault).stage(
        [memo("m-2", title="<script>alert(1)</script>", text="<img src=x onerror=alert(1)> javascript:alert(1)")]
    )
    html = page(client)
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    panel = page(client, opened(vault, "memo:m-2"))
    assert "<img src=x" not in panel and "&lt;img src=x onerror=alert(1)&gt;" in panel


# -- an open record --------------------------------------------------------------------------------------------------


def test_a_transcript_opens_with_its_utterances_and_a_mail_with_its_lines(client: TestClient, vault: Vault) -> None:
    html = page(client, opened(vault, TRANSCRIPT))
    tree = parse(html)
    assert tree.find("h2", id="panel-title").text() == "Supplier call"
    rows = tree.find_all("li", class_="utterance")
    assert [r.find("span", class_="time").text() for r in rows] == [
        "00:00-00:12",
        "00:12-00:31",
        "00:30-00:46",
        "00:45-00:46",
        "00:46-01:00",
    ]
    assert rows[1].text().endswith("Ben Sample First the weekend plans.")
    fragment = page(client, opened(vault, TRANSCRIPT).replace("/staging?", "/staging/panel?"))
    assert parse(fragment).find("h2", id="panel-title").text() == "Supplier call"
    lines = parse(page(client, opened(vault, MAIL_RID))).find_all("li", class_="text-line")
    assert [li.find("span", class_="offset").text() for li in lines][:3] == ["0", "11", "12"]


def test_the_version_in_raw_that_was_cut_shows_beside_a_new_one(client: TestClient, vault: Vault) -> None:
    gate = Gate(vault)
    gate.classify(key_of(vault, MAIL_RID), "work", [Cut(start=0, end=10)])
    gate.promote()
    gate.stage([mail(text="Hello again,\nthe offer is now 9 million.")])
    html = page(client, opened(vault, MAIL_RID))
    assert "an earlier version was decided by hand" in html
    cut_before = parse(html).find("details", class_="cut-before")
    assert CUT_MARKER in cut_before.text()


def test_a_record_on_hold_shows_no_title_and_doesnt_open(client: TestClient, vault: Vault) -> None:
    holds.add(vault, "calls", ["rec-1"])
    html = page(client)
    assert "Supplier call" not in html
    record = parse(html).find("li", class_="why-on-hold")
    assert record.find_all("a") == []
    assert TRANSCRIPT in record.text()
    assert "on hold: it stops every ingest of calls" in record.text()
    panel = page(client, opened(vault, TRANSCRIPT))
    assert "Supplier call" not in panel and "weekend" not in panel
    assert parse(panel).find("h2", id="panel-title").text() == "on hold: transcript"
    assert parse(panel).find_all("form", class_="cut-form") == []
    assert "1 staged record on hold" in page(client)


def test_a_rule_turned_on_since_intake_applies_in_the_list_and_the_panel(client: TestClient, vault: Vault) -> None:
    Gate(vault).stage([mail("mail-2", title=f"Ring {PHONE}", text=f"My number: {PHONE}\nUntil Friday.")])
    config = VaultConfig.load(vault.root / "vault.yaml")
    config.redaction.phone = True
    (vault.root / "vault.yaml").write_text(config.dump(), encoding="utf-8", newline="\n")
    html = page(client)
    assert PHONE not in html and "Ring [redacted:phone]" in html
    panel = page(client, opened(vault, "calls:mail-2"))
    assert PHONE not in panel
    assert "The redaction rules changed since this was staged" in panel
    assert "[redacted:phone]" in parse(panel).find("ol", class_="text-lines").text()
    assert PHONE in staged(vault)["calls:mail-2"]["record"]["text"]


# -- cutting and classifying -----------------------------------------------------------------------------------------


def test_preview_is_the_pages_own_state_and_writes_nothing(client: TestClient, vault: Vault) -> None:
    key = key_of(vault, TRANSCRIPT)
    before = staged(vault)
    fields = form(page(client, opened(vault, TRANSCRIPT))) | {"u": ["2"], "act": "preview"}
    answer = client.post("/staging/classify", data=fields, headers=SAME)
    assert answer.status_code == 303
    assert answer.headers["location"] == f"/staging?open={key}&u=2"
    html = page(client, answer.headers["location"])
    assert text_of(html, "preview")[0].startswith("After the cuts Removes 2 utterances: 00:12-00:31.")
    assert [li.find("input").attrs["value"] for li in parse(html).find_all("li", class_="going")] == ["2", "3"]
    assert staged(vault) == before


def test_work_with_checked_utterances_cuts_at_their_exact_times(client: TestClient, vault: Vault) -> None:
    key = key_of(vault, TRANSCRIPT)
    fields = form(page(client, opened(vault, TRANSCRIPT, "&state=pending"))) | {"u": ["2", "4"], "label": "work"}
    answer = client.post("/staging/classify", data=fields, headers=SAME)
    assert answer.status_code == 303, answer.text
    assert answer.headers["location"] == f"/staging?state=pending&done=work-{key[:12]}"
    file = staged(vault)[TRANSCRIPT]
    assert (file["state"], file["classified_by"]) == ("ready", "user")
    assert file["cuts"] == [
        {"start": TALK[1]["start_ms"], "end": TALK[1]["end_ms"], "unit": "ms"},
        {"start": 45_700, "end": 45_701, "unit": "ms"},
    ]
    html = page(client, answer.headers["location"])
    assert f"{key[:12]} classified work" in html
    html = page(client)
    assert "classified work by you, with 2 cuts: it goes at the next promotion" in text_of(html, "why")


def test_a_classification_from_a_page_the_record_changed_under_writes_nothing(client: TestClient, vault: Vault) -> None:
    fields = form(page(client, opened(vault, TRANSCRIPT))) | {"label": "work"}
    # Another tab, the CLI or Claude cut it since.
    Gate(vault).classify(key_of(vault, TRANSCRIPT), "work", [Cut(start=12_000, end=30_400, unit="ms")], by="claude")
    before = staged(vault)
    answer = client.post("/staging/classify", data=fields, headers=SAME)
    assert answer.status_code == 409
    assert "This record changed since you opened it: nothing was written." in answer.text
    assert "classified work by Claude, with 1 cut" in answer.text
    assert staged(vault) == before


def test_a_record_promoted_meanwhile_is_the_pages_error(client: TestClient, vault: Vault) -> None:
    fields = form(page(client, opened(vault, MAIL_RID))) | {"label": "work"}
    gate = Gate(vault)
    gate.classify(key_of(vault, MAIL_RID), "work")
    gate.promote()
    commits = vault.git.run("rev-list", "--count", "HEAD").stdout
    answer = client.post("/staging/classify", data=fields, headers=SAME)
    assert answer.status_code == 400
    assert "nothing staged as" in answer.text
    assert vault.git.run("rev-list", "--count", "HEAD").stdout == commits


def test_a_lock_another_process_holds_is_the_pages_error(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_state, "LOCK_SECONDS", 0.2)
    fields = form(page(client, opened(vault, MAIL_RID))) | {"label": "work"}
    before = staged(vault)
    with Vault.open(vault.root).lock:
        answer = client.post("/staging/classify", data=fields, headers=SAME)
    assert answer.status_code == 400
    assert "another docket process holds" in answer.text
    assert staged(vault) == before


def test_a_selection_or_label_the_form_cant_hold_writes_nothing(client: TestClient, vault: Vault) -> None:
    fields = form(page(client, opened(vault, TRANSCRIPT)))
    before = staged(vault)
    for extra, said in [
        ({"label": "maybe"}, "label is work or private"),
        ({"label": "work", "u": "9"}, "u names utterance 9, and this transcript has 5"),
        ({"label": "work", "cut": "1-4"}, "cut a time, such as 12:30-14:05, not characters"),
        ({"label": "private", "u": "1"}, "cuts go with work"),
        ({"label": "work", "open": "nope"}, "open is a staged record&#39;s key"),
    ]:
        answer = client.post("/staging/classify", data=fields | extra, headers=SAME)
        assert answer.status_code == 400, extra
        assert said in answer.text, extra
    assert staged(vault) == before
    assert (
        client.post(
            "/staging/classify",
            content=b"x" * 70_000,
            headers=SAME | {"Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 413
    )
    assert client.post("/staging/classify", json={"label": "work"}, headers=SAME).status_code == 415
    assert client.get("/staging/classify").status_code == 405
    assert client.get("/staging/promote").status_code == 405


def test_private_asks_once_more_then_drops_with_no_trace_in_the_answer_or_the_log(
    client: TestClient, vault: Vault, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    key = key_of(vault, MAIL_RID)
    html = page(client, opened(vault, MAIL_RID, "&confirm=private"))
    assert "Drop this mail for good?" in html
    fields = form(html, "confirm-form")
    assert fields["label"] == "private"
    answer = client.post("/staging/classify", data=fields, headers=SAME)
    assert answer.status_code == 303
    assert answer.headers["location"] == f"/staging?done=private-{key[:12]}"
    assert MAIL_RID not in staged(vault)
    decided = (vault.root / "staging" / "_decided.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(decided) == 1 and key in decided[0]
    after = page(client, answer.headers["location"])
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
    html = page(client)
    assert "held back at the last promotion: a cut didn't fit" in text_of(html, "why")
    answer = client.post("/staging/promote", data=form(html, "promote-all"), headers=SAME)
    assert answer.status_code == 303
    assert answer.headers["location"] == "/staging?done=promoted-3-0"
    assert set(raw(vault)) == {TRANSCRIPT, MAIL_RID, "memo:m-1"}
    assert vault.git.untracked("raw") == []
    assert pokes
    assert "Promoted: 3 records written to raw and committed." in page(client, answer.headers["location"])
    one = client.post("/staging/promote", data={"promote": "Bad name!"}, headers=SAME)
    assert one.status_code == 400 and "promote names a source" in one.text


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
    html = page(client)
    assert "Weekend at the cabin" not in html
    assert "2 recordings held" in text_of(html, "lead")[0]
    held = parse(html).find("article", class_="held")
    assert [li.find("span", class_="locator").text() for li in held.find_all("li", class_="held-id")] == [
        "rec-cut-01",
        "rec-cut-03",
    ]
    assert "held again after its release" in held.find_all("li", class_="held-id")[1].text()
    where = f'--from "{folder.resolve()}" --vault "{vault.root}"'
    assert text_of(html, "paste-text") == [
        "Go through my held calls recordings with /docket:cuts.",
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
    html = page(client)
    assert f'docket hold show calls "rec-cut-01" --from "<folder>" --vault "{vault.root}"' in text_of(
        html, "paste-text"
    )
    assert str(folder.resolve()) in parse(html).find("dl", class_="unfilled").text()


def test_a_hold_list_that_doesnt_read_is_the_pages_error(client: TestClient, vault: Vault) -> None:
    (vault.root / holds.HOLDS).mkdir(parents=True, exist_ok=True)
    (vault.root / holds.HOLDS / "calls.txt").write_text("rec-1\nrec 2\n", encoding="utf-8")
    html = page(client, status=400)
    assert "line 2" in html
    assert "rec 2" not in html


# -- the Guard on every post -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "http://evil.example"}, 403),
        ({"Origin": "null", "Sec-Fetch-Site": "same-origin"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({}, 403),
        (SAME, 303),
    ],
)
def test_every_post_passes_the_guard(client: TestClient, vault: Vault, headers: dict[str, str], status: int) -> None:
    fields = form(page(client, opened(vault, MAIL_RID))) | {"act": "preview"}
    assert client.post("/staging/classify", data=fields, headers=headers).status_code == status
    assert client.post("/staging/promote", data={}, headers=headers).status_code == status


def test_a_form_the_page_didnt_make_is_refused_and_nothing_written(client: TestClient, vault: Vault) -> None:
    before = staged(vault)
    no_record = client.post("/staging/classify", data={"label": "work"}, headers=SAME)
    assert no_record.status_code == 400 and "the form names no record" in no_record.text
    form_type = {"Content-Type": "application/x-www-form-urlencoded"}
    not_utf8 = client.post("/staging/classify", content=b"label=\xff", headers=SAME | form_type)
    assert not_utf8.status_code == 400
    assert client.post("/staging/promote", json={"promote": "memo"}, headers=SAME).status_code == 415
    assert staged(vault) == before


def test_promote_with_nothing_ready_or_the_lock_held_writes_nothing(
    client: TestClient, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_state, "LOCK_SECONDS", 0.2)
    with Vault.open(vault.root).lock:
        busy = client.post("/staging/promote", data={"promote": "memo"}, headers=SAME)
    assert busy.status_code == 400 and "another docket process holds" in busy.text
    assert raw(vault) == {}
    calm = client.post("/staging/promote", data={"promote": "calls"}, headers=SAME)
    assert calm.headers["location"] == "/staging?done=promoted-0-0"
    assert raw(vault) == {}


def test_an_unexpected_failure_is_the_error_page_and_names_nothing_of_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("Kovács Ödön's weekend")

    monkeypatch.setattr(staging_routes, "gather", broken)
    answer = client.get("/staging")
    assert answer.status_code == 500
    assert "Something failed in Docket" in answer.text and "Kovács" not in answer.text
    panel = client.get("/staging/panel")
    assert panel.status_code == 500 and "Something failed in Docket" in panel.text
    assert "Kovács" not in panel.text


def test_a_classification_of_a_record_on_hold_writes_nothing(client: TestClient, vault: Vault) -> None:
    key = key_of(vault, TRANSCRIPT)
    holds.add(vault, "calls", ["rec-1"])
    before = staged(vault)
    shown = {"shown_state": "pending", "shown_by": "", "shown_cuts": ""}
    for label in ("work", "private"):
        answer = client.post("/staging/classify", data={"open": key, "label": label} | shown, headers=SAME)
        assert answer.status_code == 400, label
        assert "is on hold" in answer.text
    assert staged(vault) == before
    assert not (vault.root / "staging" / "_decided.jsonl").exists()


def test_work_on_a_cut_record_keeps_its_cuts_unless_they_are_unchecked(client: TestClient, vault: Vault) -> None:
    cuts = [Cut(start=12_000, end=30_400, unit="ms"), Cut(start=50_000, end=55_000, unit="ms")]
    Gate(vault).classify(key_of(vault, TRANSCRIPT), "work", cuts, by="claude")
    html = page(client, opened(vault, TRANSCRIPT))
    assert "Work, with 2 cuts" in html
    answer = client.post("/staging/classify", data=as_shown(html) | {"label": "work"}, headers=SAME)
    assert answer.status_code == 303, answer.text
    file = staged(vault)[TRANSCRIPT]
    assert (file["classified_by"], [(c["start"], c["end"]) for c in file["cuts"]]) == (
        "user",
        [(12_000, 30_400), (50_000, 55_000)],
    )


def test_a_version_staged_before_its_release_shows_the_released_stretch_as_gone(
    client: TestClient, vault: Vault
) -> None:
    line = Release.model_validate(
        {"id": "rec-1", "cuts": [LoggedCut(start=12_000, end=30_400)], "keep": [], "at": "2026-10-14T09:00:00Z",
         "machine": "laptop-1"}
    )  # fmt: skip
    releaselog.add(vault, "calls", line)
    html = page(client, opened(vault, TRANSCRIPT))
    assert "released with its cuts" in html
    assert "First the weekend plans." not in html and "The cabin by the lake" not in html
    assert text_of(html, "was-cut") == ["00:12-00:31 cut by its release", "00:30-00:46 cut by its release"]
    assert parse(html).find_all("input", name="u", value="2") == []


def test_a_badly_typed_cut_keeps_the_record_open_and_its_checks(client: TestClient, vault: Vault) -> None:
    before = staged(vault)
    fields = form(page(client, opened(vault, TRANSCRIPT))) | {"u": "2", "cut": "12:30", "label": "work"}
    answer = client.post("/staging/classify", data=fields, headers=SAME)
    assert answer.status_code == 400
    panel = parse(answer.text).find("section", class_="staged-record")
    assert "cut 12:30: a cut is <start>-<end>" in panel.text()
    assert [i.attrs["value"] for i in panel.find_all("input", name="u") if "checked" in i.attrs] == ["2"]
    assert staged(vault) == before
    html = page(client, opened(vault, TRANSCRIPT, "&u=2&cut=12.30-14.05"))
    assert "cut 12.30-14.05: a cut" in text_of(html, "notice")[0]
    assert parse(html).find("input", name="cut").attrs["value"] == "12.30-14.05"


def test_a_post_without_a_session_is_refused(client: TestClient, vault: Vault) -> None:
    fields = form(page(client, opened(vault, MAIL_RID))) | {"label": "work"}
    client.cookies.clear()
    assert client.post("/staging/classify", data=fields, headers=SAME).status_code == 401
    assert staged(vault)[MAIL_RID]["state"] == "pending"
