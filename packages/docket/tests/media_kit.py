"""What the media tests share: a vault with a Plaud-shaped call, a keychain in memory, audio-like bytes, real audio
made here (a tone, through the same FFmpeg Docket cuts with), and a local HTTPS server with a certificate made here
for an invented host. Nothing reaches the network or the real keychain, and no real recording is used."""

from __future__ import annotations

import datetime as dt
import http.server
import io
import math
import os
import ssl
import threading
from array import array
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from docket_testkit import make_config, make_record

from docket.media.fetch import Fetcher, Limits
from docket.media.keystore import KeyStore
from docket.vault import cursors
from docket.vault.config import SourceConfig
from docket.vault.gate import Gate
from docket.vault.records import RawRecord
from docket.vault.vault import Vault

CALL = "plaud:rec-a"
POLISHED = f"{CALL}/transaction_polish"
HOST = "media.example"
SAID = [
    {"start_ms": 0, "end_ms": 9000, "speaker": "Ada Example", "text": "The crane passed its load test."},
    {"start_ms": 10_000, "end_ms": 19_000, "speaker": "Ben Sample", "text": "The brake pad comes on Monday."},
]


class Keychain:
    """A keychain in memory, for the tests that mean the keychain."""

    def __init__(self, *, fails: bool = False) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.fails = fails

    def get_password(self, service: str, username: str) -> str | None:
        if self.fails:
            raise RuntimeError("no keychain")
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fails:
            raise RuntimeError("no keychain")
        self.items[(service, username)] = password


def audio(size: int = 4096, head: bytes = b"ID3\x04\x00") -> bytes:
    """Bytes that read as an mp3 by their first bytes; the rest is noise."""
    return head + os.urandom(size - len(head))


# Each input format the tests make: the container and the encoder.
FORMATS = {
    "mp3": ("mp3", "libmp3lame"),
    "m4a": ("ipod", "aac"),
    "wav": ("wav", "pcm_s16le"),
    "flac": ("flac", "flac"),
    "opus": ("ogg", "libopus"),
}
RATE = 16_000


def tone(seconds: float = 20.0, *, fmt: str = "mp3", start: float = 0.0) -> bytes:
    """A 440 Hz tone at half scale, silent before `start` seconds, encoded as `fmt`: what the cut tests put."""
    import av

    container, codec = FORMATS[fmt]
    out = io.BytesIO()
    with av.open(out, "w", format=container) as written:
        stream = cast(Any, written.add_stream(codec, rate=RATE, layout="mono"))
        wants = stream.codec_context.codec.audio_formats
        resampler = av.AudioResampler(format=wants[0].name if wants else "s16", layout="mono", rate=RATE)
        total, first, at = int(seconds * RATE), int(start * RATE), 0
        while at < total:
            n = min(1024, total - at)
            samples = array(
                "h",
                (int(16384 * math.sin(2 * math.pi * 440 * i / RATE)) if i >= first else 0 for i in range(at, at + n)),
            )
            frame = av.AudioFrame(format="s16", layout="mono", samples=n)
            frame.sample_rate = RATE
            plane = frame.planes[0]
            plane.update(samples.tobytes() + bytes(plane.buffer_size - n * 2))
            frame.pts, frame.time_base = at, Fraction(1, RATE)
            for done in resampler.resample(frame):
                for packet in stream.encode(done):
                    written.mux(packet)
            at += n
        for done in resampler.resample(None):
            for packet in stream.encode(done):
                written.mux(packet)
        for packet in stream.encode(None):
            written.mux(packet)
    return out.getvalue()


def decoded(data: bytes) -> array[int]:
    """Audio decoded to 48 kHz mono samples, in the test's own process: only for checking what was written."""
    import av

    out: array[int] = array("h")
    with av.open(io.BytesIO(data), "r") as found:
        stream = next(s for s in found.streams if s.type == "audio")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
        for packet in found.demux(stream):
            for frame in cast(Any, packet).decode():
                for done in resampler.resample(frame):
                    out.frombytes(bytes(done.planes[0])[: done.samples * 2])
        for done in resampler.resample(None):
            out.frombytes(bytes(done.planes[0])[: done.samples * 2])
    return out


def dbfs(samples: array[int], start_ms: int, end_ms: int) -> float:
    """The level of a stretch, in dB below full scale; -200 for digital silence."""
    chunk = samples[start_ms * 48 : end_ms * 48]
    rms = math.sqrt(math.sumprod(chunk, chunk) / max(1, len(chunk)))
    return 20 * math.log10(rms / 32768) if rms else -200.0


def call(
    rid: str = CALL, *, text: str = "The crane passed its load test.", ts: dt.datetime | None = None
) -> list[RawRecord]:
    """A recording and its transcript, as Plaud's mapper keeps them."""
    when = ts or dt.datetime(2026, 10, 12, 8, 30, tzinfo=dt.UTC)
    under = {"source": "plaud", "container": rid, "thread": rid, "author": None, "ts": when}
    said = [{**s, "text": text if i == 0 else s["text"]} for i, s in enumerate(SAID)]
    lines = "\n".join(f"{s['speaker']}: {s['text']}" for s in said)
    return [
        make_record(rid=rid, kind="recording", title="Crane check", text="", **under),
        make_record(rid=f"{rid}/transaction_polish", kind="transcript", segments=said, text=lines, **under),
    ]


def media_vault(
    tmp_path: Path,
    *,
    sensitivity: str = "work",
    hosts: tuple[str, ...] = (HOST,),
    folders: tuple[str, ...] = (),
    store: bool = True,
) -> Vault:
    """A vault with a plaud source this machine ingests, a file key store, and the call staged."""
    config = make_config()
    config.sources = {
        "plaud": SourceConfig(
            sensitivity=sensitivity,  # pyright: ignore[reportArgumentType]
            media_hosts=list(hosts),
            media_folders=list(folders),
        ),
        "chat": SourceConfig(),
    }
    vault = Vault.init(tmp_path / "vault", config, "test-box")
    cursors.save(vault, "plaud", cursors.Cursor())
    cursors.save(vault, "chat", cursors.Cursor())
    if store:
        KeyStore(vault.home).init("file")
    Gate(vault).stage(call())
    return vault


def in_raw(vault: Vault) -> None:
    """What waits, promoted: the call in raw."""
    Gate(vault).promote()


# -- a local HTTPS server ------------------------------------------------------------------------------------------


def _certificate(folder: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST), x509.DNSName("other.example")]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_path, key_path


@dataclass
class Server:
    port: int
    context: ssl.SSLContext
    body: bytes
    routes: dict[str, tuple[int, dict[str, str], bytes]]
    seen: list[dict[str, str]]

    def url(self, path: str, host: str = HOST) -> str:
        return f"https://{host}:{self.port}{path}"

    def fetcher(self, *, max_bytes: int = 1024 * 1024, redirects: int = 3, address: str = "127.0.0.1") -> Fetcher:
        """A fetcher that finds the test hosts at the local server; every address allowed, as only a test sets."""
        return Fetcher(
            Limits(max_bytes=max_bytes, redirects=redirects),
            resolver=lambda host, port: [address],
            is_allowed=lambda found: True,
            context=self.context,
        )


@contextmanager
def https_server(tmp_path: Path, body: bytes) -> Iterator[Server]:
    """An HTTPS server on a free port of 127.0.0.1, for `media.example`; each route answers as `routes` says, `/audio`
    with `body`."""
    cert, key = _certificate(tmp_path)
    routes: dict[str, tuple[int, dict[str, str], bytes]] = {"/audio": (200, {}, body)}
    seen: list[dict[str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self) -> None:
            seen.append({k.lower(): v for k, v in self.headers.items()})
            status, headers, data = routes.get(self.path.split("?", 1)[0], (404, {}, b"no"))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            if "Content-Length" not in headers and status == 200 and headers.get("X-No-Length") is None:
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    class Quiet(http.server.ThreadingHTTPServer):
        def handle_error(self, request: Any, client_address: Any) -> None:
            # A client that refuses the test certificate ends its handshake: expected, and not worth a traceback.
            return

    server = Quiet(("127.0.0.1", 0), Handler)
    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    serving.minimum_version = ssl.TLSVersion.TLSv1_2
    serving.load_cert_chain(cert, key)
    server.socket = serving.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = ssl.create_default_context(cafile=str(cert))
    try:
        yield Server(server.server_address[1], client, body, routes, seen)
    finally:
        server.shutdown()
        server.server_close()
