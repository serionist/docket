"""Where Docket keeps its per-user state, found with the standard library and platformdirs only: the capture hook reads
it on every MCP call of every session, before anything heavier is imported (DR-0242)."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_state_path


def state_dir() -> Path:
    """Where the service keeps its pid file, its lock, its open runs' marker and `docket open`'s redirect page."""
    env = os.environ.get("DOCKET_STATE_DIR")
    return Path(env) if env else user_state_path("docket", appauthor=False)
