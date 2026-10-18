"""The redaction settings in vault.yaml: what `docket redaction` shows, and switching one rule on or off.

Credentials (passwords and secrets after a label, API keys and tokens, private key blocks) are on unless the user
turns them off; phone numbers are off unless the user turns them on; each of the user's own patterns is on unless its
`enabled` says otherwise. Names are never redacted: keeping one out of the vault is the never-store lists' job.
vault.yaml is rewritten only when its text is what Docket would write for it, so no comment of the user's is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from docket.errors import DocketError
from docket.vault import settings
from docket.vault.config import Redaction, VaultConfig
from docket.vault.vault import Vault

CREDENTIALS = "credentials"
PHONE = "phone"
BUILTIN = {
    CREDENTIALS: "passwords and secrets after a label, API keys and tokens, private key blocks",
    PHONE: "phone numbers written with + or after a label",
}
NAMES = "names are never redacted; keep one out of the vault with never_store in vault.yaml"
CREDENTIALS_OFF = (
    "from the next sync, passwords, keys and tokens are stored as written, in raw, docs and git history; "
    "turning it back on doesn't remove them from raw"
)
TURNED_ON = (
    "run docket doc redact to apply it to the docs already stored; raw records stored while it was off keep what "
    "they hold"
)


def word(on: bool) -> str:
    return "on" if on else "off"


def describe(settings: Redaction) -> list[str]:
    """The settings, a line each: the two built-in switches, the user's patterns, and that names never are."""
    lines = [
        f"{CREDENTIALS:<12} {word(settings.credentials):<4} {BUILTIN[CREDENTIALS]}",
        f"{PHONE:<12} {word(settings.phone):<4} {BUILTIN[PHONE]}",
    ]
    lines += [f"{'pattern':<12} {word(p.enabled):<4} {p.name}" for p in settings.patterns]
    if not settings.credentials:
        lines.append(f"warning      {CREDENTIALS_OFF}")
    lines.append(NAMES)
    return lines


@dataclass
class Switched:
    """What `set` did: the rule and its setting, whether vault.yaml changed and was committed, the line to change by
    hand when it couldn't be written, and what the user should know."""

    rule: str
    on: bool
    changed: bool = False
    commit: str | None = None
    by_hand: str | None = None
    notes: list[str] = field(default_factory=list[str])
    # Not made: the owner didn't confirm turning credentials redaction off at the console.
    declined: bool = False


def by_hand(rule: str, on: bool, pattern: bool) -> str:
    """The vault.yaml line that makes the change, for a file Docket won't rewrite."""
    value = "true" if on else "false"
    if pattern:
        return f"enabled: {value}, under the pattern named {rule} in redaction.patterns"
    old = " (in place of builtin:, if the file has it)" if rule == CREDENTIALS else ""
    return f"{rule}: {value}, under redaction:{old}"


def _target(settings: Redaction, rule: str, on: bool) -> bool:
    """Whether the rule is one of the user's patterns rather than a built-in switch; refused when the name is no rule,
    or could be either, or two patterns share it, with the vault.yaml lines that would set it `on` as asked."""
    named = [p for p in settings.patterns if p.name == rule]
    if rule in BUILTIN:
        if named:
            raise DocketError(
                f"{rule} names the built-in rule and a pattern of yours; change the one you mean in vault.yaml: "
                f"{by_hand(rule, on, False)}, or {by_hand(rule, on, True)}; or rename the pattern"
            )
        return False
    if len(named) > 1:
        raise DocketError(
            f"{len(named)} patterns are named {rule}; change the one you mean in vault.yaml "
            f"(enabled: {'true' if on else 'false'}), "
            "or give each its own name"
        )
    if not named:
        mine = ", ".join(p.name for p in settings.patterns)
        raise DocketError(f"no redaction rule {rule}: credentials, phone{', or ' + mine if mine else ''}")
    return True


def set_rule(vault: Vault, rule: str, on: bool) -> Switched:
    """Turn one rule on or off in vault.yaml and commit it, under the vault lock, through vault.yaml's one writer."""
    result = Switched(rule=rule, on=on)
    pattern = False

    def edit(config: VaultConfig) -> VaultConfig:
        nonlocal pattern
        now = config.redaction
        pattern = _target(now, rule, on)
        current = next(p.enabled for p in now.patterns if p.name == rule) if pattern else getattr(now, rule)
        if current == on:
            return config
        if not on and rule == CREDENTIALS:
            result.notes.append(CREDENTIALS_OFF)
        if on:
            result.notes.append(TURNED_ON)
        if pattern:
            patterns = [p.model_copy(update={"enabled": on}) if p.name == rule else p for p in now.patterns]
            updated = now.model_copy(update={"patterns": patterns})
        else:
            updated = now.model_copy(update={rule: on})
        return config.model_copy(update={"redaction": updated})

    warned = [CREDENTIALS_OFF] if rule == CREDENTIALS and not on else []
    done = settings.rewrite(vault, edit, f"Config: redaction {rule} {word(on)}", via="cli", warnings=warned)
    result.declined = done.declined
    if done.by_hand:
        result.by_hand = by_hand(rule, on, pattern)
    result.changed, result.commit = done.changed, done.commit
    return result
