"""How the command line talks to a running service, and finds it: `service.json` in the state folder, then the
service's own answer to /api/status, which names its pid."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from docket import userconfig

DEFAULT_PORT = 8741
SERVICE_FILE = "service.json"
TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Answer:
    status: int
    body: dict[str, Any]


def service_file() -> Path:
    return userconfig.state_dir() / SERVICE_FILE


def recorded() -> dict[str, Any] | None:
    """What the running service wrote about itself, or None when no service left a file."""
    try:
        data: Any = json.loads(service_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def port() -> int:
    """The port the service listens on: its own record, then the user's config, then the default."""
    found = recorded()
    if found is not None and isinstance(found.get("port"), int):
        return int(found["port"])
    return userconfig.service().port or DEFAULT_PORT


def call(method: str, path: str, *, token: str | None = None, at: int | None = None) -> Answer | None:
    """A request to the service on this machine; None when nothing answers. Proxies are never used: a corporate
    proxy from the environment would carry a loopback request, and the token, off the machine."""
    return _send(method, path, b"" if method == "POST" else None, token=token, at=at, timeout=TIMEOUT_SECONDS)


def call_json(
    method: str, path: str, body: Any, *, token: str | None = None, at: int | None = None, timeout: float = 25.0
) -> Answer | None:
    """A request with a JSON body, such as the capture hook's: under the hook's own 30 seconds."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    return _send(method, path, data, headers=headers, token=token, at=at, timeout=timeout)


def _send(
    method: str,
    path: str,
    data: bytes | None,
    *,
    headers: dict[str, str] | None = None,
    token: str | None,
    at: int | None,
    timeout: float,
) -> Answer | None:
    url = f"http://127.0.0.1:{at or port()}{path}"
    request = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return Answer(response.status, _json(response.read()))
    except urllib.error.HTTPError as e:
        return Answer(e.code, _json(e.read()))
    except (urllib.error.URLError, OSError):
        return None


def _json(data: bytes) -> dict[str, Any]:
    try:
        value: Any = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}
