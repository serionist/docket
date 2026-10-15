"""Docket: a personal memory and task manager that Claude works in."""

from __future__ import annotations


def __getattr__(name: str) -> str:
    # Read on first use, not at import: the capture hook imports this package on every MCP call of every session, and
    # reading the installed metadata costs more than the rest of its no-run path (DR-0242).
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

        try:
            return version("docket")
        except PackageNotFoundError:  # pragma: no cover - only in a source tree that is not installed
            return "0.0.0"
    raise AttributeError(f"module 'docket' has no attribute {name!r}")
