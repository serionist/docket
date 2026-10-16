"""The Claude Code plugin's hooks, run as `docket hook <event>`, so their logic is typed and tested here and the plugin
holds no scripts.

SessionStart says what state Docket is in: the service's line (the one `docket service status --quiet` prints), the
vault's, whether Claude Code connects to the port the service listens on, and whether a curate round is due. Claude
gets every line; the user sees them only when one needs them. Counts and paths only, never a record's text or title.
The vault is read without `Vault.open`, whose checks run git: a hook in every session of every project stays cheap. It
never fails the session: whatever goes wrong becomes a line.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from docket import userconfig
from docket.curate import batch
from docket.errors import DocketError
from docket.render import printable
from docket.service import client
from docket.service.install import ServiceStatus
from docket.vault import curatelog
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.staging import STAGING
from docket.vault.vault import Vault

PORT_VARIABLE = "DOCKET_PORT"


@dataclass(frozen=True)
class Line:
    text: str
    # Shown to the user too, not only to Claude: something they should act on.
    needs_user: bool


def session_start(status: Callable[[], ServiceStatus], environ: Mapping[str, str]) -> dict[str, Any]:
    """The JSON Claude Code's SessionStart hook reads: every line as Claude's context, the ones that need the user as
    a message to them."""
    try:
        lines = _lines(status(), environ)
    except DocketError as e:
        lines = [Line(f"docket: {e}", True)]
    except Exception as e:
        # The type only: an exception's message can carry what it was reading.
        lines = [Line(f"docket: the status check failed ({type(e).__name__})", True)]
    out: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(printable(line.text) for line in lines),
        }
    }
    shown = [printable(line.text) for line in lines if line.needs_user]
    if shown:
        out["systemMessage"] = "\n".join(shown)
    return out


def _lines(st: ServiceStatus, environ: Mapping[str, str]) -> list[Line]:
    lines = [Line(st.line, not st.running)]
    if st.warning:
        lines.append(Line(f"docket: {st.warning}", True))
    # Before install there is no token yet, and the service's line already says to install.
    if st.store_problem and (st.installed or st.running):
        lines.append(Line(f"docket: the token can't be read: {st.store_problem}", True))
    wanted = environ.get(PORT_VARIABLE) or str(client.DEFAULT_PORT)
    if wanted != str(st.port):
        lines.append(
            Line(
                f"docket: Claude Code connects to port {wanted}, the service's port is {st.port}; set "
                f"{PORT_VARIABLE}={st.port} where Claude Code starts",
                True,
            )
        )
    if st.running:
        lines.extend(_service_vault(st.body))
    lines.extend(_vault())
    return lines


def _service_vault(body: dict[str, Any]) -> list[Line]:
    """What the running service says of its vault: a problem opening it (a stopped convert among them), its index."""
    lines: list[Line] = []
    problem = body.get("vault_problem")
    if problem:
        lines.append(Line(f"vault: {problem}", True))
    raw: Any = body.get("index")
    index = cast(dict[str, Any], raw) if isinstance(raw, dict) else None
    if index is not None and not index.get("exists"):
        lines.append(Line("index: not built yet; the first search builds it", False))
    elif index is not None and index.get("stale"):
        lines.append(Line(f"index: {index['stale']}; the next search rebuilds it", False))
    return lines


def _vault() -> list[Line]:
    """The service's vault, else the default vault: its config read and its staging counted, never opened."""
    configured = userconfig.service().vault or userconfig.default_vault()
    if configured is None:
        return [Line("docket: no vault yet; /docket:setup makes one", True)]
    root = Vault.find(configured)
    if root is None:
        return [Line(f"vault: none at {configured}; /docket:setup, or `docket service install --vault <folder>`", True)]
    try:
        VaultConfig.load(root / CONFIG_FILE)
    except DocketError as e:
        return [Line(f"vault: {e}", True)]
    waiting = _staged(root)
    if waiting:
        line = Line(f"vault: {root}, {waiting} record(s) wait in staging; `docket staging list --vault {root}`", True)
    else:
        line = Line(f"vault: {root}, nothing waiting in staging", False)
    return [line, *_curate(root)]


def _curate(root: Path) -> list[Line]:
    """Whether a curate round is due, a week after the last any machine logged, with the newest week's review minutes:
    for Claude only, to mention once. Nothing before any entity work, an entity file or a logged change (DR-0474)."""
    try:
        lines = curatelog.read_all(root).lines
    except DocketError as e:
        return [Line(f"curate: {e}", True)]
    if not lines and not any((root / "entities").glob("*/*.yaml")):
        return []
    last = batch.last_round(lines, None)
    now = batch.now_utc()
    if last is not None and now - last < batch.DUE:
        return []
    if last is None:
        since = "none yet"
    else:
        since = f"the last started {(now - last).days} days ago"
        week = next(iter(batch.minutes(lines).items()), None)
        since += f", {week[1]:.0f} min of review in {week[0]}" if week is not None else ""
    return [Line(f"curate: a round is due ({since}); mention it once, and run /docket:curate only when asked", False)]


def _staged(root: Path) -> int:
    """Records waiting in staging, one file each, counted by name: their content is never read here."""
    return sum(1 for _ in (root / STAGING).glob("*/*.json"))
