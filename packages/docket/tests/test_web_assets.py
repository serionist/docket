"""What /static/ serves (DR-0705): the icon and the app's build, each with its type from a fixed table, an ETag and
no-cache, needing no session; anything else a plain 404 that names nothing."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from pathlib import Path

import pytest
from docket_testkit import index_template
from docket_webkit import signed_in
from starlette.testclient import TestClient

from docket.web import assets, frontend


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return index_template(tmp_path_factory)


@pytest.fixture
def client(template: Path, tmp_path: Path) -> Iterator[TestClient]:
    with signed_in(template, tmp_path) as found:
        yield found


@pytest.mark.parametrize(
    ("path", "kind"),
    [("icon.svg", "image/svg+xml"), ("app/app.css", "text/css; charset=utf-8"), ("app/build.json", "application/json")],
)
def test_a_static_file_has_its_type_an_etag_and_no_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, kind: str
) -> None:
    # Windows' registry can tell mimetypes a script is text/plain; the table ignores it.
    monkeypatch.setattr(mimetypes, "guess_type", lambda *_a, **_k: ("text/plain", None))
    client.cookies.clear()
    answer = client.get(f"/static/{path}")
    assert answer.status_code == 200
    assert answer.headers["Content-Type"] == kind
    assert answer.headers["Cache-Control"] == "no-cache"
    assert answer.headers["X-Content-Type-Options"] == "nosniff"
    data = assets.ASSETS[path].data if path in assets.ASSETS else frontend.BUILD.files[path.removeprefix("app/")].data
    assert answer.content == data
    again = client.get(f"/static/{path}", headers={"If-None-Match": answer.headers["ETag"]})
    assert again.status_code == 304
    assert again.content == b""
    head = client.head(f"/static/{path}")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["Content-Length"] == str(len(data))


@pytest.mark.parametrize(
    "path", ["/static/nope.css", "/static/%2E%2E/web/assets.py", "/static/css/%2E%2E/%2E%2E/assets.py", "/static/css"]
)
def test_anything_outside_the_list_is_a_plain_404(client: TestClient, path: str) -> None:
    answer = client.get(path)
    assert answer.status_code == 404
    assert answer.headers["Content-Type"].startswith("text/plain")
    assert answer.text == "not found"


def test_a_path_the_client_folds_never_reaches_a_file(client: TestClient) -> None:
    answer = client.get("/static/../web/assets.py")
    assert answer.status_code == 404
    assert "ASSETS" not in answer.text


def test_a_file_without_a_known_type_stops_the_load(tmp_path: Path) -> None:
    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "a.css").write_text("a {}", encoding="utf-8")
    (tmp_path / "notes.md").write_text("# x", encoding="utf-8")
    with pytest.raises(ValueError, match=r"static/notes\.md has no content type"):
        assets.load(tmp_path)
    (tmp_path / "notes.md").unlink()
    (tmp_path / "Makefile").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="static/Makefile"):
        assets.load(tmp_path)
    (tmp_path / "Makefile").unlink()
    assert set(assets.load(tmp_path)) == {"css/a.css"}
