"""The service's log: one line per request, and no content.

The user's words never reach the log: no query string, body, tool argument or result, and no exception's message,
since an error can quote a record. Library loggers stay at WARNING (the MCP SDK logs a tool error's text at INFO), and
an exception is written as its type and frames, for it and every cause and context in its chain.
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docket.service.security import HEADERS

log = logging.getLogger("docket.service")

LIBRARIES = ("mcp", "uvicorn", "uvicorn.error", "uvicorn.access", "starlette", "fastapi", "httpx2", "sse_starlette")
LOG_BYTES = 5 * 1024 * 1024
LOG_FILES = 3
INTERNAL = {"error": "internal error; see the service log"}


def describe(error: BaseException) -> str:
    """An exception's type and frames, and those of each exception in its chain: never a message."""
    lines: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        lines.append(f"{type(current).__module__}.{type(current).__qualname__}")
        tb = current.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            lines.append(f"  at {code.co_filename}:{tb.tb_lineno} in {code.co_name}")
            tb = tb.tb_next
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if current is not None:
            lines.append("from")
    return "\n".join(lines)


class NoMessages(logging.Formatter):
    """Writes a record's own line and, for an exception, only its type and frames."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        record.asctime = self.formatTime(record)
        line = self.formatMessage(record)
        error = record.exc_info[1] if record.exc_info else None
        if error is not None:
            line += "\n" + describe(error)
        return line


def configure(log_file: Path) -> logging.Handler:
    """The service's logging: a rotated file, Docket at INFO, the libraries at WARNING, the formatter above."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=LOG_BYTES, backupCount=LOG_FILES, encoding="utf-8")
    handler.setFormatter(NoMessages())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    logging.getLogger("docket").setLevel(logging.INFO)
    for name in LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler


def _route(scope: Scope) -> str:
    """The route's template, not the path: a path can carry an id, and a query string the user's words."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template
    path: str = scope.get("path", "")
    return path if path in ("/mcp", "/session", "/") else "<other>"


class Requests:
    """The outermost layer: a line per request, and any exception turned into a 500 that isn't raised again, so no
    server logs it with its message."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 0

        async def sending(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, sending)
        except Exception as e:
            log.error("%s %s failed", scope["method"], _route(scope), exc_info=e)
            if status == 0:
                status = 500
                await JSONResponse(INTERNAL, status_code=500, headers=HEADERS)(scope, receive, send)
        finally:
            ms = round((time.monotonic() - started) * 1000)
            log.info("%s %s %d %d ms", scope["method"], _route(scope), status, ms)
