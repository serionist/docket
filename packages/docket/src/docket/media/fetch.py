"""Where media may come from (DR-0604), and the reader that hands its bytes to the encryption as they arrive.

| From | Allowed when |
|---|---|
| a path | after every link, a regular file under a `media_folders` entry or the drop folder; no placeholder |
| a URL | https, to any host, every address it resolves to allowed |

An address is allowed when it is global and isn't an IPv6 form of an IPv4 address (mapped, compatible, NAT64, 6to4,
Teredo), which `is_global` passes for some on Python 3.12 and which can reach an IPv4 address the check never saw. One
address that isn't allowed refuses the host. The connection goes to a checked address with the host as SNI, so there
is no second lookup to rebind, and environment proxies are never used. At most 3 redirects, each checked the same way;
`Accept-Encoding: identity`, and any other `Content-Encoding` refused; the cap on `Content-Length` and while reading.

A URL is never logged, stored or put in an error: a signed URL is a credential. Errors name the host only.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urljoin, urlsplit

from docket import __version__, folders
from docket.errors import DocketError
from docket.media.layout import HEAD
from docket.vault.git import toplevel

MIB = 1024 * 1024
MAX_BYTES = 512 * MIB
CHUNK = 64 * 1024
REDIRECTS = 3
CONNECT_SECONDS = 15.0
IDLE_SECONDS = 60.0
TOTAL_SECONDS = 15 * 60.0
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
# IPv6 forms that carry an IPv4 address: mapped, compatible, NAT64 (well-known and local-use), 6to4, Teredo.
IPV4_IN_IPV6 = tuple(
    ipaddress.IPv6Network(net)
    for net in ("::ffff:0:0/96", "::/96", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)

Resolver = Callable[[str, int], list[str]]
Allowed = Callable[[str], bool]


@dataclass(frozen=True)
class Limits:
    max_bytes: int = MAX_BYTES
    connect: float = CONNECT_SECONDS
    idle: float = IDLE_SECONDS
    total: float = TOTAL_SECONDS
    redirects: int = REDIRECTS


def resolve(host: str, port: int) -> list[str]:
    """Every address the system resolves a host to, once each."""
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise DocketError(f"{host} doesn't resolve") from None
    return list(dict.fromkeys(str(info[4][0]) for info in found))


def allowed(address: str) -> bool:
    """Whether media may be fetched from an address: global, and no IPv6 form of an IPv4 address."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and any(ip in net for net in IPV4_IN_IPV6):
        return False
    # is_global leaves multicast in: a fetch is to one host, never a group.
    return ip.is_global and not ip.is_multicast


class Capped:
    """A file-like reader pyrage pulls from: it keeps the first bytes for the type, counts the cap and the time, and
    says why it stopped. A timeout on the socket stops it too."""

    def __init__(self, stream: BinaryIO, limits: Limits, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.stream = stream
        self.limits = limits
        self.clock = clock
        self.deadline = clock() + limits.total
        self.head = b""
        self.count = 0
        self.failure: str | None = None

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1, /) -> bytes:
        try:
            if self.clock() > self.deadline:
                raise DocketError(f"the media took longer than {int(self.limits.total // 60)} minutes")
            chunk = self.stream.read(CHUNK if size < 0 else min(size, CHUNK))
        except TimeoutError:
            self.failure = f"nothing came for {int(self.limits.idle)} seconds"
            raise
        except DocketError as e:
            self.failure = str(e)
            raise
        self.count += len(chunk)
        if self.count > self.limits.max_bytes:
            self.failure = f"the media is over the cap of {self.limits.max_bytes // MIB} MiB"
            raise DocketError(self.failure)
        if len(self.head) < HEAD:
            self.head += chunk[: HEAD - len(self.head)]
        return chunk


# -- a path --------------------------------------------------------------------------------------------------------


def media_folder(vault_root: Path, value: str) -> Path | None:
    """A source's media folder on this machine, its links followed: under the home folder, or relative to the repo
    that holds the vault; None when it isn't there."""
    if value == "~" or value.startswith("~/"):
        base = Path.home()
        folder = base / value[2:]
    else:
        repo = toplevel(vault_root.parent)
        if repo is None:
            return None
        base = repo
        folder = repo / value
    if not Path(os.path.normpath(folder)).is_relative_to(base) or not folder.is_dir():
        return None
    return folder.resolve()


def open_path(given: str, roots: Sequence[Path], limits: Limits) -> tuple[BinaryIO, Path]:
    """A local file under one of `roots` (the source's media folders and the drop folder, resolved), opened."""
    try:
        real = Path(given).expanduser().resolve(strict=True)
        info = real.stat()
    except OSError:
        raise DocketError(f"no file at {given}") from None
    if not real.is_file():
        raise DocketError(f"{given} isn't a file")
    if folders.placeholder(info):
        raise DocketError(f"{given} is a placeholder whose bytes aren't on this machine; open it once, then put it")
    if not any(real.is_relative_to(root) for root in roots):
        raise DocketError(
            f"{given} isn't under one of the source's media folders or Docket's drop folder; `docket media folder add` "
            "names one"
        )
    if info.st_size > limits.max_bytes:
        raise DocketError(f"{given} is over the cap of {limits.max_bytes // MIB} MiB")
    return real.open("rb"), real


# -- a URL ---------------------------------------------------------------------------------------------------------


class _Pinned(http.client.HTTPSConnection):
    """An https connection to one checked address, with the host as SNI and for the certificate."""

    def __init__(self, host: str, address: str, port: int, context: ssl.SSLContext, limits: Limits) -> None:
        super().__init__(host, port, timeout=limits.connect, context=context)
        self.address = address
        self.ssl_context = context
        self.idle = limits.idle

    def connect(self) -> None:
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            sock.settimeout(self.idle)
            self.sock = self.ssl_context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


@dataclass
class Fetcher:
    """Fetches over https within the limits. The resolver, the address rule and the TLS context are parameters only
    so tests can point a host at a local server; every caller in Docket uses the defaults."""

    limits: Limits
    resolver: Resolver = resolve
    is_allowed: Allowed = allowed
    context: ssl.SSLContext | None = None

    def open(self, url: str) -> tuple[http.client.HTTPResponse, http.client.HTTPConnection]:
        """The response whose body is the media, after at most `limits.redirects` redirects, each checked again."""
        context = self.context or ssl.create_default_context()
        for _ in range(self.limits.redirects + 1):
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if parts.scheme != "https":
                raise DocketError(f"media is fetched over https only, not {parts.scheme or 'a bare path'}")
            if not host:
                raise DocketError("that URL names no host")
            port = parts.port or 443
            addresses = self.resolver(host, port)
            if not addresses or not all(self.is_allowed(a) for a in addresses):
                raise DocketError(f"{host} resolves to an address media is never fetched from")
            target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            conn, response = self._get(host, addresses, port, context, target)
            if response.status in REDIRECT_CODES:
                target = response.getheader("Location") or ""
                _close(response, conn)
                if not target:
                    raise DocketError(f"{host} redirected nowhere")
                url = urljoin(url, target)
                continue
            if response.status != 200:
                _close(response, conn)
                raise DocketError(f"{host} answered {response.status}")
            encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding != "identity":
                _close(response, conn)
                raise DocketError(f"{host} sent the media encoded as {encoding}; Docket stores it as sent, unencoded")
            length = response.getheader("Content-Length")
            if length is not None and length.isdigit() and int(length) > self.limits.max_bytes:
                _close(response, conn)
                raise DocketError(f"the media from {host} is over the cap of {self.limits.max_bytes // MIB} MiB")
            return response, conn
        raise DocketError(f"more than {self.limits.redirects} redirects")

    def _get(
        self, host: str, addresses: list[str], port: int, context: ssl.SSLContext, target: str
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        failure: Exception | None = None
        for address in addresses:
            conn = _Pinned(host, address, port, context, self.limits)
            try:
                conn.request(
                    "GET", target, headers={"Accept-Encoding": "identity", "User-Agent": f"docket/{__version__}"}
                )
                return conn, conn.getresponse()
            except (OSError, http.client.HTTPException) as e:
                conn.close()
                failure = e
        raise DocketError(f"couldn't fetch from {host}: {type(failure).__name__}")


def _close(response: http.client.HTTPResponse, conn: http.client.HTTPConnection) -> None:
    """The response first: it holds the socket's file, which closing the connection alone leaves open."""
    response.close()
    conn.close()
