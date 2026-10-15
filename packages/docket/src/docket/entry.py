"""The `docket` command's entry. The capture hook runs on every MCP call of every Claude Code session, so its command
is answered here before the CLI and everything it imports load (DR-0242); every other command goes to the CLI."""

from __future__ import annotations

import sys
from collections.abc import Sequence

HOOK = ("hook", "post-tool-use")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if tuple(args) == HOOK:
        from docket import capture  # noqa: PLC0415 - the hook's path imports nothing else

        return capture.main()
    from docket.cli import main as cli  # noqa: PLC0415

    return cli(args)
