"""The Claude Code plugin's hooks, run as `docket hook <event>`, so their logic is typed and tested here and the plugin
holds no scripts.

SessionStart says what state Docket is in: the service's line (the one `docket service status --quiet` prints), the
vault's, whether Claude Code connects to the port the service listens on, whether a curate round is due, how many
doc lines cite a line that changed, as the running service's index counts them (DR-0542), and how many media keys no
export holds, as the running service counts them (DR-0550). Then the owner's day, as the running service reads it
(DR-0670 to DR-0672): whole, in at most 45 lines with the status lines, when the session's own repo is the vault's,
else one line of counts. Claude gets every line; the user sees them only when one needs them, of the day only a sync
due and a status file a hand edit holds. Outside the vault's repo, counts and paths only, never a record's text or
title (DR-0163, DR-0671). The vault is read without `Vault.open`, whose checks run git, and the day by the service:
a hook in every session of every project stays cheap. It never fails the session: whatever goes wrong becomes a line.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from docket import userconfig
from docket.curate import schedule
from docket.errors import DocketError
from docket.plaintext import printable
from docket.service import client
from docket.service.install import ServiceStatus
from docket.vault import curatelog
from docket.vault.config import CONFIG_FILE, VaultConfig
from docket.vault.home import repo_at
from docket.vault.staging import STAGING
from docket.vault.vault import Vault

PORT_VARIABLE = "DOCKET_PORT"
PROJECT_VARIABLE = "CLAUDE_PROJECT_DIR"
# The most lines the hook says, its own and the day's, as docket.ledger.day.ROOM, which the hook doesn't import: it
# loads no index module (DR-0670).
LINES = 45
# How long the hook waits for the day: with the status call's 5 s, 8 s at worst, under the hook's 15 (DR-0672).
DAY_SECONDS = 3.0
# Asks the running service for the day in so many lines: its answer, or None when nothing answered in time.
DayCall = Callable[[ServiceStatus, int], client.Answer | None]


@dataclass(frozen=True)
class Line:
    text: str
    # Shown to the user too, not only to Claude: something they should act on.
    needs_user: bool


def session_start(
    status: Callable[[], ServiceStatus], environ: Mapping[str, str], day: DayCall | None = None
) -> dict[str, Any]:
    """The JSON Claude Code's SessionStart hook reads: every line as Claude's context, the ones that need the user as
    a message to them; then, with `day`, the owner's day as the running service reads it."""
    st: ServiceStatus | None = None
    try:
        st = status()
        lines = _lines(st, environ)
    except DocketError as e:
        lines = [Line(f"docket: {e}", True)]
    except Exception as e:
        # The type only: an exception's message can carry what it was reading.
        lines = [Line(f"docket: the status check failed ({type(e).__name__})", True)]
    if day is not None and st is not None:
        lines += _day(st, day, environ, LINES - len(lines))
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


def _day(st: ServiceStatus, call: DayCall, environ: Mapping[str, str], room: int) -> list[Line]:
    """The owner's day from the running service: whole when this session's own repo is the vault's, else its counts;
    one line naming the read that works when the service is down, slow or can't read it (DR-0672). Nothing before a
    vault and a service are set up, or while the service can't open its vault: the status lines say what to do. The
    hook never folds the ledger itself."""
    try:
        if st.running and (not st.body.get("vault") or st.body.get("vault_problem")):
            return []
        if not st.running:
            configured = userconfig.service().vault or userconfig.default_vault()
            if st.installed is None or configured is None or Vault.find(configured) is None:
                return []
            return [Line("day: the service is down; docket ledger day reads the vault", False)]
        answer = call(st, max(room, 0))
        if answer is None:
            return [Line(f"day: no answer in {DAY_SECONDS:g} s; the ledger tool's snapshot reads it", False)]
        body = answer.body
        if answer.status != 200:
            why = body.get("error")
            return [Line(f"day: not read: {why}" if isinstance(why, str) else f"day: not read (HTTP {answer.status})",
                         False)]  # fmt: skip
        repo, counts, said = body.get("repo"), body.get("counts"), body.get("lines")
        if not (isinstance(repo, str) and isinstance(counts, str) and isinstance(said, list)):
            return [Line("day: not read (an answer this Docket doesn't know)", False)]
        if _own_repo(environ) != Path(repo):
            return [Line(counts, False)]
        found = [cast(dict[str, Any], line) for line in cast(list[Any], said) if isinstance(line, dict)]
        return [Line(str(line.get("text", "")), line.get("owner") is True) for line in found]
    except Exception as e:
        # The type only, as for the status check.
        return [Line(f"day: not read ({type(e).__name__})", False)]


def _own_repo(environ: Mapping[str, str]) -> Path | None:
    """This session's own repo: the first folder at or above its project folder that holds .git, found by a stat
    (DR-0671), so a separate repo nested in the vault's isn't the vault's."""
    given = environ.get(PROJECT_VARIABLE)
    return repo_at((Path(given) if given else Path.cwd()).resolve())


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
    unbacked = _count(body.get("media_unbacked"))
    if unbacked:
        lines.append(
            Line(
                f"media: {unbacked} key(s) no export holds yet; lose this clone and that media is gone for good. "
                "`docket media keys export <file>`, in your own terminal",
                True,
            )
        )
    if index is not None:
        flagged, unapplied = _count(index.get("flagged")), _count(index.get("unapplied"))
        if flagged or unapplied:
            # Worded as docket.index.flags.summary words it, which the hook doesn't import: it loads no index module.
            said = [f"{flagged} line(s) cite lines that changed"] if flagged else []
            said += [f"{unapplied} correction line(s) not applied"] if unapplied else []
            lines.append(Line(f"docs: {', '.join(said)}; `docket doc stale`", False))
    return lines


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


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
    last = schedule.last_round(lines)
    now = schedule.now_utc()
    if last is not None and now - last < schedule.DUE:
        return []
    if last is None:
        since = "none yet"
    else:
        since = f"the last started {(now - last).days} days ago"
        week = next(iter(schedule.minutes(lines).items()), None)
        since += f", {week[1]:.0f} min of review in {week[0]}" if week is not None else ""
    return [Line(f"curate: a round is due ({since}); mention it once, and run /docket:curate only when asked", False)]


def _staged(root: Path) -> int:
    """Records waiting in staging, one file each, counted by name: their content is never read here."""
    return sum(1 for _ in (root / STAGING).glob("*/*.json"))
