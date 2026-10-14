"""Docket: a personal memory and task manager that Claude works in."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("docket")
except PackageNotFoundError:  # pragma: no cover - only in a source tree that is not installed
    __version__ = "0.0.0"
