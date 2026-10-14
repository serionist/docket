"""The `docket` command."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from docket import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docket", description="A personal memory and task manager.")
    parser.add_argument("--version", action="version", version=f"docket {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
