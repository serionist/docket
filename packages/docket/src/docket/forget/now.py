"""`forget()`: the forget's yes and its writes (DR-0577, DR-0581). Only the `docket forget --now` command calls it, and
it takes no answer as a parameter, so a Python caller, a pipe or Claude Code's shell gets a refusal, not a forget.

| Step | What |
|---|---|
| 1 | refused, before anything is read, without the owner at a console (`console.problem`) |
| 2 | the plan and its screen, with ids; one question for the whole forget: the OS prompt, or a code typed back |
| 3 | under the vault lock: the plan worked out again, stopped with nothing written on a difference from the screen |
| 4 | the list's line, then its commit alone; then the apply, in one commit (DR-0581) |
| 5 | the lock let go; the history rewrite's files, written under it instead when the list's commit or the apply stops |
| 6 | the index rebuilt under its own lock; the rewrite's lines printed |

None of it stops a process running as the user on purpose: it makes a forget a deliberate act, which the skills forbid
Claude.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from docket import console, index
from docket.errors import DocketError
from docket.forget import apply as forget_apply
from docket.forget import plan as planning
from docket.forget import rewrite
from docket.forget.plan import Kind, Plan
from docket.media.keystore import KeyStore
from docket.vault import forgotten
from docket.vault.vault import Vault

CHANGED = "the vault changed since the screen; nothing was forgotten, so run it again"
# What the OS prompt names when it asks (DR-0572).
ACT = "forget records for good"
IN_FORCE = (
    "the forget's line is on the list and in force: every read skips what it takes, and the next gate call commits "
    "and finishes the rest"
)


@dataclass
class Forgot:
    """What a forget did: the plan it showed, whether the owner said yes, what the apply took, the rewrite's files,
    and why the index wasn't rebuilt, if it wasn't."""

    plan: Plan
    answer: console.Answer | None = None
    applied: forget_apply.Applied | None = None
    written: rewrite.Written | None = None
    index_problem: str | None = None
    lines: list[str] = field(default_factory=list[str])


def refuse() -> None:
    """Refuse a forget without the owner at a console: in Claude Code's shell, from a pipe or to a redirect. The
    command asks before it opens the vault, so nothing is read first."""
    problem = console.problem(console.FORGET)
    if problem is not None:
        raise DocketError(problem)


def forget(vault: Vault, kind: Kind, rids: list[str], source: str | None = None) -> Forgot:
    """Forget for good, at the owner's console only, after the OS prompt's yes or, where this machine has none, the
    code they type back. Refused before anything is read without the owner at a console."""
    refuse()
    # What a crash left reaches git first, and a conflict refuses here, before the screen (DR-0528).
    with vault.writing():
        vault.refuse_unmerged()
        forgotten.commit_pending(vault)
    shown = planning.make(vault, kind, rids, source)
    done = Forgot(shown)
    screen = planning.lines(shown, screen=True)
    if not shown.taken:
        done.lines = screen
        return done
    if shown.stops:
        raise DocketError("the forget can't go on:\n" + "\n".join(screen))
    done.answer = console.confirm(
        screen, "forget all of it for good", "keep everything", "nothing was forgotten", act=ACT
    )
    if done.answer != "yes":
        return done
    at = datetime.now(UTC)
    with vault.writing():
        again = planning.make(vault, kind, rids, source, stays=False)
        if again.signature() != shown.signature():
            raise DocketError(CHANGED)
        held = rewrite.before(vault, again)
        forgotten.write_line(vault, again.line(vault.machine, at))
        # From here the line is in force, so the forget can't be stopped: whatever stops the list's commit or the
        # apply, the rewrite's files are written, since nothing writes them later, and the error says so.
        try:
            forgotten.commit_pending(vault, f"Forget: the list from {vault.machine}")
            done.applied = forget_apply.apply(vault, KeyStore(vault.home), first=True)
        except BaseException as e:
            written, problem = _write(vault, again, held, at)
            said = f"{IN_FORCE}; " + (f"the history rewrite's files are in {written.folder}" if written else problem)
            if isinstance(e, DocketError | KeyboardInterrupt):
                raise DocketError(f"{str(e) or 'stopped'}\n{said}") from e
            e.add_note(said)
            raise
    # After the lock: it only reads git objects, every doc blob on every ref, which can outlast the lock's waiters.
    done.written, problem = _write(vault, again, held, at)
    done.lines = rewrite.history_lines(vault, done.written) if done.written else [f"history   {problem}"]
    # The index after the lock, under its own: the vault lock's waiters give up after 60 s (DR-0581). Until it lands,
    # its digest keeps the old index refused (DR-0593).
    try:
        index.rebuild(vault)
    except DocketError as e:
        done.index_problem = str(e)
    except KeyboardInterrupt:
        done.index_problem = "the rebuild was stopped; docket index rebuild finishes it"
    return done


def _write(vault: Vault, plan: Plan, held: rewrite.Before, at: datetime) -> tuple[rewrite.Written | None, str]:
    """The rewrite's files, or why they couldn't be written: never an error of its own, so what stopped the apply is
    the one said."""
    try:
        return rewrite.write(vault, plan, held, at), ""
    except (DocketError, OSError) as e:
        return None, f"the history rewrite's files couldn't be written: {e}"
