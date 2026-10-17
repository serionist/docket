"""The fetch over https against a local server (DR-0548): the hosts and addresses checked before connecting, each
redirect checked again, no compression, the cap on the header and while reading. The test hosts resolve to the local
server through the fetcher's resolver, and only the happy paths allow its loopback address."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from media_kit import HOST, Server, audio, https_server

from docket.errors import DocketError
from docket.media import fetch
from docket.media.fetch import MIB, Capped, Fetcher, Limits


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    with https_server(tmp_path, audio(50_000)) as found:
        yield found


def body(
    server: Server, url: str, hosts: tuple[str, ...] = (HOST,), *, max_bytes: int = MIB, redirects: int = 3
) -> bytes:
    response, conn = server.fetcher(max_bytes=max_bytes, redirects=redirects).open(url, hosts)
    try:
        reader = Capped(response, Limits(max_bytes=max_bytes))
        return b"".join(iter(reader.read, b""))
    finally:
        response.close()
        conn.close()


def test_a_listed_host_s_media_comes_unencoded_and_whole(server: Server) -> None:
    assert body(server, server.url("/audio")) == server.body
    assert server.seen[-1]["accept-encoding"] == "identity"
    assert server.seen[-1]["user-agent"].startswith("docket/")


def test_a_redirect_is_followed_after_the_same_checks(server: Server) -> None:
    server.routes["/one"] = (302, {"Location": "/audio"}, b"")
    server.routes["/away"] = (301, {"Location": server.url("/audio", "other.example")}, b"")
    assert body(server, server.url("/one")) == server.body
    assert body(server, server.url("/away"), (HOST, "other.example")) == server.body
    with pytest.raises(DocketError, match=r"other.example isn't one of the source's media hosts"):
        body(server, server.url("/away"))
    server.routes["/plain"] = (302, {"Location": f"http://{HOST}:{server.port}/audio"}, b"")
    with pytest.raises(DocketError, match="over https only, not http"):
        body(server, server.url("/plain"))
    server.routes["/nowhere"] = (302, {}, b"")
    with pytest.raises(DocketError, match="redirected nowhere"):
        body(server, server.url("/nowhere"))
    for n in range(4):
        server.routes[f"/r{n}"] = (307, {"Location": f"/r{n + 1}"}, b"")
    server.routes["/r4"] = (200, {}, b"late")
    with pytest.raises(DocketError, match="more than 3 redirects"):
        body(server, server.url("/r0"))
    assert body(server, server.url("/r0"), redirects=4) == b"late"


def test_a_redirect_to_a_private_address_is_refused_before_connecting(server: Server) -> None:
    server.routes["/inside"] = (302, {"Location": "https://inside.example/meta"}, b"")
    resolved = {HOST: ["127.0.0.1"], "inside.example": ["169.254.169.254"]}
    real = Fetcher(
        Limits(),
        resolver=lambda host, port: resolved[host],
        is_allowed=lambda address: address == "127.0.0.1" or fetch.allowed(address),
        context=server.context,
    )
    with pytest.raises(DocketError, match=r"inside.example resolves to an address media is never fetched from"):
        real.open(server.url("/inside"), (HOST, "inside.example"))
    assert len(server.seen) == 1


@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["10.0.0.7"], ["93.184.215.14", "192.168.1.9"], ["64:ff9b::a00:1"], ["::7f00:1"], []],
)
def test_a_host_with_one_address_that_isn_t_allowed_is_refused(server: Server, addresses: list[str]) -> None:
    refusing = Fetcher(Limits(), resolver=lambda host, port: addresses, context=server.context)
    with pytest.raises(DocketError, match="resolves to an address media is never fetched from"):
        refusing.open(server.url("/audio"), (HOST,))
    assert not server.seen


def test_only_https_to_a_listed_host(server: Server) -> None:
    with pytest.raises(DocketError, match="over https only, not http"):
        server.fetcher().open(f"http://{HOST}/audio", (HOST,))
    with pytest.raises(DocketError, match="over https only, not a bare path"):
        server.fetcher().open("/etc/passwd", (HOST,))
    with pytest.raises(DocketError, match=r"other.example isn't one of the source's media hosts"):
        server.fetcher().open(server.url("/audio", "other.example"), (HOST,))
    with pytest.raises(DocketError, match="that URL isn't one"):
        server.fetcher().open("https:///audio", (HOST,))


def test_compression_an_error_and_a_length_over_the_cap_are_refused(server: Server) -> None:
    server.routes["/gzip"] = (200, {"Content-Encoding": "gzip"}, b"\x1f\x8b")
    with pytest.raises(DocketError, match="encoded as gzip"):
        body(server, server.url("/gzip"))
    with pytest.raises(DocketError, match=f"{HOST} answered 404"):
        body(server, server.url("/missing"))
    server.routes["/big"] = (200, {"Content-Length": str(10 * 1024 * 1024)}, b"x")
    with pytest.raises(DocketError, match="over the cap of 1 MiB"):
        body(server, server.url("/big"))


def test_a_body_that_grows_past_the_cap_is_cut_off(server: Server) -> None:
    server.routes["/stream"] = (200, {"X-No-Length": "1"}, audio(3 * 1024 * 1024))
    with pytest.raises(DocketError, match="over the cap of 1 MiB"):
        body(server, server.url("/stream"))


def test_a_url_s_secret_never_reaches_an_error(server: Server) -> None:
    signed = server.url("/missing?X-Amz-Signature=deadbeefsecret")
    with pytest.raises(DocketError) as refused:
        body(server, signed)
    assert "deadbeef" not in str(refused.value)


def test_a_host_that_doesn_t_answer_is_named(server: Server, tmp_path: Path) -> None:
    closed = Fetcher(Limits(connect=2), resolver=lambda host, port: ["127.0.0.1"], is_allowed=lambda a: True)
    with pytest.raises(DocketError, match=f"couldn't fetch from {HOST}"):
        closed.open(f"https://{HOST}:1/audio", (HOST,))
    # The server's certificate isn't one the system trusts: refused like any other failure.
    untrusted = Fetcher(Limits(), resolver=lambda host, port: ["127.0.0.1"], is_allowed=lambda a: True)
    with pytest.raises(DocketError, match="SSLCertVerificationError"):
        untrusted.open(server.url("/audio"), (HOST,))
