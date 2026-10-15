from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest
from docket_testkit import index_template, make_record
from docket_webkit import a11y_problems, html_problems, parse, signed_in
from starlette.testclient import TestClient

from docket.vault.vault import Vault

CALL = "r:calls:rec-0100@01:50-03:20"
OFFER = "r:mail:<offer-1@northwind.example>#1-4"
MESSAGE = "r:chat:C0OPS/1791400800.000100"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


def panel(client: TestClient, hit: str, extra: str = "", status: int = 200) -> str:
    answer = client.get(f"/search/panel?hit={quote(hit)}{extra}")
    assert answer.status_code == status, answer.text
    assert not html_problems(answer.text)
    assert not a11y_problems(answer.text, whole=False)
    return answer.text


def test_a_transcript_hit_puts_its_summary_next_to_its_raw(client: TestClient) -> None:
    tree = parse(panel(client, CALL))
    summary = tree.find("section", class_="summary-column")
    assert "minutes:2026-10-07-supplier-call, minutes, rev 1" in summary.text()
    cited = summary.find_all("div", class_="cited")
    assert [line.attrs["data-line"] for line in cited] == ["14"]
    assert "points at this hit" in cited[0].text()
    anchor = cited[0].find("a", class_="anchor")
    assert anchor.text() == "@01:50-02:30"
    assert "hit=r:calls:rec-0100@01:50-02:30" in anchor.attrs["href"]
    assert "Also pointing here:" in summary.text()
    assert "recording-note:2026-10-07-supplier-call rev 1, line 16" in summary.text()
    raw = tree.find("section", class_="raw-column")
    segments = raw.find_all("li", class_="segment")
    marked = [s.find("button", class_="time").text() for s in segments if "mark" in s.attrs["class"].split()]
    assert marked[0] == "01:50"
    assert all("01:50" <= t <= "03:20" for t in marked)
    assert any(s.find("button").text() < "01:50" for s in segments if s not in raw.find_all("li", class_="mark"))
    assert segments[0].find("button").attrs["data-locator"].startswith("r:calls:rec-0100@")
    assert "1 min" in tree.find("span", class_="around").text()
    assert tree.find("a", data_key="v").text() == "Verify the summary"


def test_around_widens_the_raw(client: TestClient) -> None:
    def count(around: int) -> int:
        return len(parse(panel(client, CALL, f"&around={around}")).find_all("li", class_="segment"))

    assert count(0) < count(1) < count(3)
    tree = parse(panel(client, CALL, "&around=0"))
    assert not tree.find_all("a", data_key="[")
    # One minute is the default, so the URL leaves it out.
    assert tree.find("a", data_key="]").attrs["href"] == f"/search?hit={CALL}"
    top = parse(panel(client, CALL, "&around=30"))
    assert not top.find_all("a", data_key="]")
    assert top.find("a", data_key="[").attrs["hx-get"].endswith("&around=29")


def test_a_message_hit_shows_its_neighbours_with_itself_marked(client: TestClient) -> None:
    tree = parse(panel(client, MESSAGE))
    spans = tree.find_all("article", class_="span")
    assert len(spans) == 3
    assert [("mark" in s.attrs["class"].split()) for s in spans] == [False, True, False]
    assert "cold-rated" in spans[1].text()
    assert "1 message" in tree.find("span", class_="around").text()
    assert "2 messages" in parse(panel(client, MESSAGE, "&around=2")).find("span", class_="around").text()


def test_a_mail_paragraph_hit_numbers_the_paragraphs_and_marks_its_own(client: TestClient) -> None:
    tree = parse(panel(client, OFFER, "&around=0"))
    paras = tree.find_all("li", class_="paragraph")
    assert [p.attrs["value"] for p in paras] == ["1", "2", "3", "4"]
    assert all("mark" in p.attrs["class"].split() for p in paras)
    assert "0 paragraphs" in tree.find("span", class_="around").text()
    assert "day rate" in tree.text()


def test_a_doc_hit_verifies_its_lines_and_then_every_line(client: TestClient) -> None:
    doc = "recording-note:2026-10-07-supplier-call"
    tree = parse(panel(client, doc, "&lines=21-22"))
    pairs = tree.find_all("li", class_="pair")
    assert [p.find("div", class_="line").attrs["data-line"] for p in pairs] == ["21", "22"]
    assert all(p.find_all("article", class_="span") for p in pairs)
    toggle = tree.find("a", data_key="v")
    assert toggle.text() == "Verify every line"
    every = parse(panel(client, doc, "&lines=21-22&verify=1"))
    assert len(every.find_all("li", class_="pair")) > 2
    assert every.find("a", data_key="v").text() == "Only the hit's lines"
    whole = parse(panel(client, doc))
    assert len(whole.find_all("li", class_="pair")) == len(every.find_all("li", class_="pair"))
    assert not whole.find_all("a", data_key="v")


def test_verify_on_a_record_hit_verifies_its_summary(client: TestClient) -> None:
    tree = parse(panel(client, CALL, "&verify=1"))
    pairs = tree.find_all("li", class_="pair")
    assert [p.find("div", class_="line").attrs["data-line"] for p in pairs] == ["14"]
    assert tree.find("h2").text() == "Minutes: supplier call"
    assert tree.find("a", data_key="v").text() == "Back to the raw"


def test_a_line_whose_anchor_doesnt_resolve_shows_its_problem(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "vault" / "processed" / "minutes" / "2026" / "10"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-10-15-hand-written.md").write_text(
        "---\nid: minutes:2026-10-15-hand-written\ntype: minutes\ncovers:\n  - calls:rec-0101\nby: hand\n---\n\n"
        "# Hand written\n\n- Right here. @00:10\n- Far past the end. @59:00\n- No anchor at all.\n",
        encoding="utf-8",
        newline="\n",
    )
    tree = parse(panel(client, "minutes:2026-10-15-hand-written"))
    pairs = tree.find_all("li", class_="pair")
    flagged = [p for p in pairs if "has-problem" in p.attrs["class"].split()]
    assert len(pairs) == 3
    assert len(flagged) == 2
    assert "is outside calls:rec-0101" in flagged[0].text()
    assert "no anchors at the end of the line" in flagged[1].text()


def test_a_record_no_summary_covers(client: TestClient) -> None:
    hit = "r:chat:C0SIDE/1791500000.000100"
    tree = parse(panel(client, hit))
    assert "No summary covers this yet." in tree.text()
    assert not tree.find_all("a", data_key="v")
    verify = parse(panel(client, hit, "&verify=1"))
    assert "nothing to verify" in verify.text()


def test_a_legacy_note_by_id_and_a_doc_by_its_path(client: TestClient, tmp_path: Path) -> None:
    tree = parse(panel(client, "legacy-note:old-northwind-notes"))
    assert "A legacy note has no anchors" in tree.text()
    assert "Northwind's site lead prefers calls on Tuesdays." in tree.text()
    path = next((tmp_path / "vault" / "processed").rglob("2026-10-13-fleet-review.md"))
    rel = path.relative_to(tmp_path / "vault").as_posix()
    by_path = parse(panel(client, rel, "&lines=12"))
    assert by_path.find("p", class_="muted").text().startswith("Read as")
    assert by_path.find_all("div", class_="cited")
    assert by_path.find_all("article", class_="span")


def test_a_locator_that_doesnt_resolve_is_the_panel_s_own_error(client: TestClient) -> None:
    for hit in ("r:calls:rec-9999@00:10", "[r:broken", "minutes:2026-01-01-no-such-doc"):
        assert parse(panel(client, hit, status=400)).find("div", class_="error").text()
    page = client.get("/search?query=Nortwin&hit=r:calls:rec-9999@00:10")
    assert page.status_code == 200
    assert 'class="groups"' in page.text
    assert "no record calls:rec-9999" in page.text


def test_a_record_with_two_versions_says_the_latest_is_shown(client: TestClient, tmp_path: Path) -> None:
    vault = Vault.open(tmp_path / "vault")
    vault.write_run("chat", [make_record(3, container="chat:C0X", text="first words")])
    vault.write_run("chat", [make_record(3, container="chat:C0X", text="second words")])
    tree = parse(panel(client, "r:chat:C0TEST/1791000003.000100"))
    assert "2 versions; this is the latest" in tree.text()
    assert "second words" in tree.text()


def test_the_whole_page_opens_with_the_panel(client: TestClient) -> None:
    page = client.get(f"/search?query=Nortwin&hit={quote(CALL)}")
    assert page.status_code == 200
    tree = parse(page.text)
    assert tree.find("section", class_="panel")
    assert tree.find("li", class_="open").find("a").attrs["data-locator"] == CALL
    close = tree.find("a", data_key="Escape")
    assert close.attrs["href"] == "/search?query=Nortwin"
    assert close.attrs["hx-get"] == "/search/panel?query=Nortwin"
    assert not a11y_problems(page.text)
