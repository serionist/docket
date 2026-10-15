from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from docket import userconfig
from docket.service import system
from docket.service.app import create_app
from docket.service.security import HEADERS, Guard
from docket.service.sessions import (
    CODE_SECONDS,
    COOKIE,
    SESSION_SECONDS,
    Sessions,
    open_command,
    safe_next,
    write_redirect,
)
from docket.service.state import Settings
from docket.vault.config import VaultConfig
from docket.vault.vault import Vault

PORT = 8741
TOKEN = "t0ken-for-tests-only"  # noqa: S105 - an invented token
OURS = f"http://127.0.0.1:{PORT}"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def echo(scope: Scope, receive: Receive, send: Send) -> None:
    """Stands for any route behind the guard: says what reached it."""
    request = Request(scope, receive)
    await PlainTextResponse(f"reached {request.method} {request.url.path}")(scope, receive, send)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def sessions(clock: Clock) -> Sessions:
    return Sessions(clock=clock)


@pytest.fixture
def guarded(sessions: Sessions) -> TestClient:
    return TestClient(Guard(echo, port=PORT, token=lambda: TOKEN, sessions=sessions), base_url=OURS)


def a_session(sessions: Sessions) -> dict[str, str]:
    started = sessions.redeem(sessions.new_code())
    assert started is not None
    return {"Cookie": f"{COOKIE}={started}"}


# -- the table of checks ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/api/status", "/mcp", "/session", "/api/health", "/static/css/base.css"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_forged_host_is_refused_everywhere(guarded: TestClient, path: str, method: str) -> None:
    for host in ("evil.example", f"evil.example:{PORT}", "127.0.0.1:9999", "127.0.0.1"):
        answer = guarded.request(method, path, headers={**BEARER, "Host": host})
        assert answer.status_code == 421, (host, answer.text)


def test_localhost_by_name_is_this_service_too(guarded: TestClient) -> None:
    assert guarded.get("/api/status", headers={**BEARER, "Host": f"localhost:{PORT}"}).status_code == 200


@pytest.mark.parametrize("origin", ["null", "http://evil.example", f"http://evil.example:{PORT}", "http://127.0.0.1:1"])
@pytest.mark.parametrize("path", ["/api/docs", "/mcp", "/"])
def test_a_write_from_another_origin_is_refused(guarded: TestClient, origin: str, path: str) -> None:
    assert guarded.post(path, headers={**BEARER, "Origin": origin}).status_code == 403


@pytest.mark.parametrize("site", ["cross-site", "same-site", "none"])
def test_a_write_another_site_started_is_refused(guarded: TestClient, site: str) -> None:
    assert guarded.post("/api/docs", headers={**BEARER, "Sec-Fetch-Site": site}).status_code == 403


def test_a_write_from_this_origin_with_the_cookie_passes(guarded: TestClient, sessions: Sessions) -> None:
    headers = {**a_session(sessions), "Origin": OURS, "Sec-Fetch-Site": "same-origin"}
    assert guarded.post("/api/docs", headers=headers).text == "reached POST /api/docs"


def test_a_cookie_write_with_neither_header_is_refused(guarded: TestClient, sessions: Sessions) -> None:
    assert guarded.post("/api/docs", headers=a_session(sessions)).status_code == 403
    assert guarded.post("/api/docs", headers={**a_session(sessions), "Sec-Fetch-Site": "same-origin"}).is_success


def test_a_bearer_write_without_either_header_passes(guarded: TestClient) -> None:
    assert guarded.post("/api/docs", headers=BEARER).text == "reached POST /api/docs"
    assert guarded.post("/mcp", headers=BEARER).text == "reached POST /mcp"


@pytest.mark.parametrize(
    "value", [f"Bearer {TOKEN[:-1]}x", f"Bearer {TOKEN}x", "Bearer ", f"Basic {TOKEN}", TOKEN, "Bearer wrong"]
)
def test_a_wrong_token_is_refused_even_beside_a_session(guarded: TestClient, sessions: Sessions, value: str) -> None:
    for path in ("/mcp", "/api/status"):
        answer = guarded.get(path, headers={**a_session(sessions), "Authorization": value})
        assert answer.status_code == 401
        assert answer.headers["WWW-Authenticate"] == "Bearer"


def test_mcp_takes_the_token_and_never_the_cookie(guarded: TestClient, sessions: Sessions) -> None:
    assert guarded.post("/mcp", headers={**a_session(sessions), "Origin": OURS}).status_code == 401
    assert guarded.post("/mcp", headers=BEARER).status_code == 200


def test_the_api_takes_either_and_a_page_only_the_cookie(guarded: TestClient, sessions: Sessions) -> None:
    assert guarded.get("/api/status").status_code == 401
    assert guarded.get("/api/status", headers=BEARER).status_code == 200
    assert guarded.get("/api/status", headers=a_session(sessions)).status_code == 200
    page = guarded.get("/")
    assert page.status_code == 401
    assert "docket open" in page.text
    assert guarded.get("/", headers=a_session(sessions)).text == "reached GET /"
    assert guarded.get("/", headers={COOKIE: "made-up"} | {"Cookie": f"{COOKIE}=made-up"}).status_code == 401


def test_the_session_link_takes_only_the_token(guarded: TestClient, sessions: Sessions) -> None:
    headers = {**a_session(sessions), "Origin": OURS}
    assert guarded.post("/api/session", headers=headers).status_code == 401
    assert guarded.post("/api/session", headers=BEARER).text == "reached POST /api/session"


def test_static_files_need_no_session_but_only_to_be_read(guarded: TestClient) -> None:
    assert guarded.get("/static/css/base.css").text == "reached GET /static/css/base.css"
    assert guarded.head("/static/css/base.css").status_code == 200
    assert guarded.post("/static/css/base.css").status_code == 401
    assert guarded.get("/staticky").status_code == 401
    assert guarded.get("/static").status_code == 401


def test_the_sign_in_page_names_the_command_for_the_page_asked_for(guarded: TestClient) -> None:
    page = guarded.get(r'/search?query=day rate $HOME `id` !x \y "z"&sources=calls')
    assert page.status_code == 401
    assert page.headers["Cache-Control"] == "no-store"
    command = (
        "docket open &quot;/search?query=day%20rate%20%24HOME%20%60id%60%20%21x%20%5Cy%20%22z%22"
        "&amp;sources=calls&quot;"
    )
    assert command in page.text
    assert "docket open</code>" in guarded.get("/search").text


@pytest.mark.parametrize(
    ("wanted", "command"),
    [
        ("/", "docket open"),
        ("/search", "docket open"),
        ("//evil.example", "docket open"),
        ("/search?query=a+b&hit=r:calls:rec-0100@02:30", 'docket open "/search?query=a+b&hit=r:calls:rec-0100@02:30"'),
        ("/search?query=Árvíz", 'docket open "/search?query=%C3%81rv%C3%ADz"'),
        ("/search?query='x'", 'docket open "/search?query=%27x%27"'),
    ],
)
def test_the_command_quotes_what_a_shell_would_act_on(wanted: str, command: str) -> None:
    assert open_command(wanted) == command


def test_health_and_the_session_page_need_nothing(guarded: TestClient) -> None:
    assert guarded.get("/api/health").is_success
    assert guarded.get("/session?code=x").is_success


def test_a_preflight_gets_no_cors_header(guarded: TestClient) -> None:
    answer = guarded.options(
        "/api/status",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert answer.status_code == 405
    assert not [h for h in answer.headers if h.lower().startswith("access-control-")]


def test_every_answer_carries_the_headers(guarded: TestClient, sessions: Sessions) -> None:
    answers = {
        "/api/status": guarded.get("/api/status", headers=BEARER),
        "/": guarded.get("/", headers=a_session(sessions)),
        "/session": guarded.get("/session"),
        "refused": guarded.get("/", headers={"Host": "evil.example"}),
    }
    for name, answer in answers.items():
        for header, value in HEADERS.items():
            assert answer.headers[header] == value, (name, header)
    assert answers["/api/status"].headers["Cache-Control"] == "no-store"
    assert answers["/session"].headers["Cache-Control"] == "no-store"
    assert "Cache-Control" not in answers["/"].headers


def test_lifespan_and_other_scopes_pass_through(sessions: Sessions) -> None:
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    with TestClient(app, base_url=OURS) as client:
        assert client.get("/api/health").json() == {"service": "docket"}


# -- the browser's way in --------------------------------------------------------------------------------------------


@pytest.fixture
def app_client(sessions: Sessions) -> TestClient:
    app = create_app(Settings(port=PORT, vault=None, token=lambda: TOKEN), sessions=sessions)
    return TestClient(app, base_url=OURS, follow_redirects=False)


def code_from(client: TestClient) -> str:
    answer = client.post("/api/session", headers=BEARER)
    assert answer.status_code == 200
    url = answer.json()["url"]
    assert url.startswith(f"{OURS}/session?code=")
    return url.rsplit("=", 1)[1]


def test_a_code_starts_one_session_and_moves_on_with_a_page(app_client: TestClient) -> None:
    code = code_from(app_client)
    answer = app_client.get(f"/session?code={code}&next=/search")
    assert answer.status_code == 200
    assert '<meta http-equiv="refresh" content="0; url=/search">' in answer.text
    # It names its icon, so the browser doesn't ask for a /favicon.ico that isn't there.
    assert '<link rel="icon" href="/static/icon.svg" type="image/svg+xml">' in answer.text
    assert app_client.get("/static/icon.svg").status_code == 200
    cookie = answer.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    session = re.match(f"{COOKIE}=([^;]+)", cookie)
    assert session is not None
    # Signed in, `/` moves on to Search; without the session it is the sign-in page.
    assert app_client.get("/", headers={"Cookie": f"{COOKIE}={session.group(1)}"}).status_code == 303
    assert app_client.get(f"/session?code={code}").status_code == 401


def test_a_code_is_good_for_a_minute(app_client: TestClient, clock: Clock) -> None:
    code = code_from(app_client)
    clock.now += CODE_SECONDS + 1
    assert app_client.get(f"/session?code={code}").status_code == 401


def test_a_session_ends_after_twelve_hours(app_client: TestClient, sessions: Sessions, clock: Clock) -> None:
    headers = a_session(sessions)
    assert app_client.get("/", headers=headers).status_code == 303
    clock.now += SESSION_SECONDS - 1
    assert app_client.get("/", headers=headers).status_code == 303
    clock.now += 2
    assert app_client.get("/", headers=headers).status_code == 401
    assert not sessions.valid(headers["Cookie"].split("=", 1)[1])


def test_a_made_up_code_or_none_starts_nothing(app_client: TestClient) -> None:
    assert app_client.get("/session?code=made-up").status_code == 401
    assert "set-cookie" not in app_client.get("/session").headers


@pytest.mark.parametrize(
    ("given", "goes"),
    [
        ("/search?q=a", "/search?q=a"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("https://evil.example", "/"),
        ("search", "/"),
        ("/a\r\nSet-Cookie: x", "/"),
        (None, "/"),
    ],
)
def test_next_stays_on_this_origin(given: str | None, goes: str) -> None:
    assert safe_next(given) == goes


def test_the_next_target_is_escaped_in_the_page(app_client: TestClient) -> None:
    code = code_from(app_client)
    answer = app_client.get(f"/session?code={code}&next=/a%22%3E%3Cscript%3E")
    assert "<script>" not in answer.text
    assert "/a&quot;&gt;&lt;script&gt;" in answer.text


def test_status_says_when_there_is_no_vault(app_client: TestClient) -> None:
    body = app_client.get("/api/status", headers=BEARER).json()
    assert body["vault"] is None
    assert "no vault" in body["vault_problem"]
    assert body["pid"] == os.getpid()
    assert body["port"] == PORT


# -- docket open's redirect page -------------------------------------------------------------------------------------


def test_the_redirect_page_holds_the_link_and_only_its_owner_reads_it() -> None:
    first = write_redirect(f"{OURS}/session?code=first-code")
    assert first.parent == userconfig.state_dir()
    assert "first-code" in first.read_text(encoding="utf-8")
    if os.name == "nt":
        shown = system.run(["icacls", str(first)]).stdout
        assert len([line for line in shown.splitlines() if ":(" in line]) == 1, shown
    else:
        assert first.stat().st_mode & 0o777 == 0o600
    second = write_redirect(f"{OURS}/session?code=second-code")
    assert second == first
    text = second.read_text(encoding="utf-8")
    assert "second-code" in text
    assert "first-code" not in text
    assert [p.name for p in Path(userconfig.state_dir()).iterdir()] == [first.name]


def test_status_names_the_vault_and_its_machine(tmp_path: Path, config: VaultConfig, sessions: Sessions) -> None:
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    app = create_app(
        Settings(port=PORT, vault=vault.root, token=lambda: TOKEN, store="the system keychain"), sessions=sessions
    )
    body = TestClient(app, base_url=OURS).get("/api/status", headers=BEARER).json()
    assert (body["vault"], body["machine"], body["vault_problem"]) == (str(vault.root), "test-box", None)
    assert body["store"] == "the system keychain"


def test_status_says_when_the_vault_cant_be_read(
    tmp_path: Path, sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(path: Path, lock_timeout: float = 0) -> Vault:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Vault, "open", refused)
    app = create_app(Settings(port=PORT, vault=tmp_path, token=lambda: TOKEN), sessions=sessions)
    body = TestClient(app, base_url=OURS).get("/api/status", headers=BEARER).json()
    assert body["vault_problem"] == "can't read the vault: Operation not permitted"
    assert body["vault"] == str(tmp_path)


def test_a_header_a_route_sets_itself_is_kept(sessions: Sessions) -> None:
    async def own(scope: Scope, receive: Receive, send: Send) -> None:
        await PlainTextResponse("x", headers={"Content-Security-Policy": "default-src 'none'"})(scope, receive, send)

    client = TestClient(Guard(own, port=PORT, token=lambda: TOKEN, sessions=sessions), base_url=OURS)
    assert client.get("/api/health").headers["Content-Security-Policy"] == "default-src 'none'"
