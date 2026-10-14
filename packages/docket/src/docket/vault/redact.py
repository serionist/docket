"""Redaction: credentials, keys and phone numbers out of everything a record says, before it is written anywhere.

A match becomes `[redacted:<rule>]`, so what is stored says which rule fired and never what it hid. Ids (`rid`,
`container`, `thread`, `author`, `to`) are left alone: they are how a record is found, matched and deduplicated.
`fidelity: verbatim` means not retyped by Claude, never unredacted, so `native` is redacted too, keys and values.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from docket.vault.config import Redaction
from docket.vault.neverstore import strings
from docket.vault.records import Attachment, RawRecord

MARKER = re.compile(r"\[redacted:[a-z0-9-]+\]")
_SPLIT = re.compile(rf"({MARKER.pattern})")


def marker(rule: str) -> str:
    return f"[redacted:{rule}]"


def slug(name: str) -> str:
    """A pattern's name as it can stand in a marker: lowercase letters, digits and hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "pattern"


@dataclass(frozen=True)
class Rule:
    """A named set of patterns. A match is replaced whole, or only the groups whose name starts with `secret`."""

    name: str
    patterns: tuple[re.Pattern[str], ...]

    def apply(self, text: str, counts: Counter[str]) -> str:
        for pattern in self.patterns:
            text = _outside_markers(text, partial(self._sub, pattern, counts))
        return text

    def _sub(self, pattern: re.Pattern[str], counts: Counter[str], text: str) -> str:
        out: list[str] = []
        last = 0
        for m in pattern.finditer(text):
            secrets = [m.span(g) for g, v in m.groupdict().items() if g.startswith("secret") and v is not None]
            for start, end in secrets or [m.span()]:
                if start == end:
                    continue
                out += [text[last:start], marker(self.name)]
                last = end
                counts[self.name] += 1
        out.append(text[last:])
        return "".join(out)


def _outside_markers(text: str, fn: Callable[[str], str]) -> str:
    """Apply `fn` to the text between markers only, so a marker is never matched again and redacting twice is once."""
    parts = _SPLIT.split(text)
    return "".join(part if i % 2 else fn(part) for i, part in enumerate(parts))


# -- the built-in rules -------------------------------------------------------------------------------------------

# A label starts a word, or a word inside an identifier in any case style: DB_PASSWORD, client-secret, clientSecret.
_LABEL_START = r"(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))"
# Between a label and its value: spaces, HTML tags and entities (`<b>Password:</b>&nbsp;x`).
_FILLER = r"(?:[ \t]|<[^<>\n]{0,200}>|&[A-Za-z0-9#]{2,10};)*"
_VALUE = r"(?:\"(?P<secret>[^\"\n]+)\"|'(?P<secret2>[^'\n]+)'|(?P<secret3>[^\s;,<>\"'&]+))"

_SEP = r"[ _.\-]?"
API_KEY_LABELS = [
    rf"api{_SEP}key",
    rf"client{_SEP}secret",
    rf"secret{_SEP}access{_SEP}key",
    rf"access{_SEP}key",
    r"token",
]
PASSWORD_LABELS = [
    r"pass(?:word|wd|code|phrase)",
    r"pwd",
    r"secret",
    r"passwort",
    r"jelsz[oó]",
    rf"mot{_SEP}de{_SEP}passe",
    r"contrase[nñ]a",
    r"senha",
    r"wachtwoord",
    r"has[lł]o",
    r"heslo",
    r"parola",
    r"l[oö]senord",
    r"salasana",
]
PHONE_LABELS = [r"phone", r"telephone", r"tel", r"mobile", r"mobil", r"mob", r"cell", r"telefon", r"fax"]


def _labelled(labels: list[str]) -> re.Pattern[str]:
    """A label that ends its key (an optional closing quote), then `:`, `=`, `:=` or `=>`, then the value."""
    return re.compile(
        _LABEL_START
        + rf"(?i:{'|'.join(labels)})"
        + r"(?![A-Za-z0-9])[\"']?"
        + _FILLER
        + r"(?::=|=>|[:=])"
        + _FILLER
        + _VALUE
    )


def _key_label(labels: list[str]) -> re.Pattern[str]:
    """A `native` key that is a label: its last word is one, in any case style."""
    return re.compile(_LABEL_START + rf"(?i:{'|'.join(labels)})$")


_EDGE_L = r"(?<![A-Za-z0-9_\-])"
_EDGE_R = r"(?![A-Za-z0-9_\-])"
_TOKEN_SHAPES = [
    r"sk-[A-Za-z0-9_\-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{22,}",
    r"xox[abposr]-[A-Za-z0-9\-]{10,}",
    r"(?:AKIA|ASIA)[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_\-]{35}",
    r"eyJ[A-Za-z0-9_\-]{5,}\.eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}",
]
# Built from parts so the source never reads as a key block itself.
_KEY_BLOCK = "PRIVATE KEY(?: BLOCK)?-----"
_PRIVATE_KEY = re.compile(
    "-----" + "BEGIN [A-Z0-9 ]*" + _KEY_BLOCK + r"[\s\S]*?(?:-----" + "END [A-Z0-9 ]*" + _KEY_BLOCK + r"|\Z)"
)

PRIVATE_KEY = Rule("private-key", (_PRIVATE_KEY,))
API_KEY = Rule(
    "api-key",
    (
        re.compile(_EDGE_L + "(?:" + "|".join(_TOKEN_SHAPES) + ")" + _EDGE_R),
        re.compile(r"(?i:bearer)[ \t]+(?P<secret>[A-Za-z0-9._~+/\-]{16,}=*)"),
        _labelled(API_KEY_LABELS),
    ),
)
PASSWORD = Rule(
    "password",
    (
        re.compile(r"(?<=://)[^\s:/@]+:(?P<secret>[^\s@/]+)@"),
        _labelled(PASSWORD_LABELS),
    ),
)
# A phone number needs a + or a label: a bare run of digits is as likely an amount, a ticket or a timestamp, the
# numbers Docket exists to keep.
PHONE = Rule(
    "phone",
    (
        re.compile(r"(?<![\w+])\+(?:[ .\-()]{0,2}\d){8,15}(?!\d)"),
        re.compile(
            r"(?<![A-Za-z0-9])"
            + rf"(?i:{'|'.join(PHONE_LABELS)})"
            + r"(?![A-Za-z0-9])[\"']?[ \t.]*(?:[:=#])?"
            + _FILLER
            + r"(?P<secret>\+?\(?\d(?:[ .\-/()]{0,2}\d){6,14})(?!\d)"
        ),
    ),
)
BUILTIN: tuple[Rule, ...] = (PRIVATE_KEY, API_KEY, PASSWORD, PHONE)
KEY_LABELS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api-key", _key_label(API_KEY_LABELS)),
    ("password", _key_label(PASSWORD_LABELS)),
    ("phone", _key_label(PHONE_LABELS)),
)
_PHONE_DIGITS = 7


class Redactor:
    def __init__(self, rules: Sequence[Rule], key_labels: Sequence[tuple[str, re.Pattern[str]]] = ()) -> None:
        self.rules = tuple(rules)
        self.key_labels = tuple(key_labels)

    @classmethod
    def from_config(cls, config: Redaction) -> Redactor:
        rules = list(BUILTIN) if config.builtin else []
        rules += [Rule(slug(p.name), (re.compile(p.pattern),)) for p in config.patterns]
        return cls(rules, KEY_LABELS if config.builtin else ())

    def text(self, text: str, counts: Counter[str] | None = None) -> str:
        counts = counts if counts is not None else Counter[str]()
        for rule in self.rules:
            text = rule.apply(text, counts)
        return text

    def record(self, record: RawRecord) -> tuple[RawRecord, Counter[str]]:
        """The record with everything it says redacted, and how many matches each rule had."""
        counts = Counter[str]()
        attachments = [
            Attachment(
                name=self.text(a.name, counts),
                kind=a.kind,
                ref=self.text(a.ref, counts) if a.ref is not None else None,
            )
            for a in record.attachments
        ]
        update: dict[str, Any] = {
            "title": self.text(record.title, counts) if record.title is not None else None,
            "text": self.text(record.text, counts),
            "attachments": attachments,
            "native": self._native(record.native, counts, None),
        }
        return record.model_copy(update=update), counts

    def _native(self, value: Any, counts: Counter[str], label: str | None) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in cast(dict[str, Any], value).items():
                key = self.text(str(k), counts)
                n = 2
                base = key
                while key in out:
                    key = f"{base}#{n}"
                    n += 1
                out[key] = self._native(v, counts, self._label_of(str(k)))
            return out
        if isinstance(value, list):
            return [self._native(v, counts, None) for v in cast(list[Any], value)]
        if label is not None and _is_secret_value(value, label):
            counts[label] += 1
            return marker(label)
        if isinstance(value, str):
            return self.text(value, counts)
        return value

    def _label_of(self, key: str) -> str | None:
        return next((rule for rule, pattern in self.key_labels if pattern.search(key)), None)


def markers(record: RawRecord) -> Counter[str]:
    """How many redaction markers of each rule a record holds: what a run reports, since intake redacted already."""
    texts = [record.title or "", record.text, *(a.name for a in record.attachments)]
    texts += [a.ref for a in record.attachments if a.ref is not None]
    texts += strings(record.native)
    return Counter(m.group(0)[len("[redacted:") : -1] for t in texts for m in MARKER.finditer(t))


def _is_secret_value(value: Any, label: str) -> bool:
    """Whether the value under a label key is the secret itself: text or a number, and for a phone label, digits
    enough to be a phone number."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return False
    text = str(value)
    if not text or MARKER.fullmatch(text):
        return False
    return label != "phone" or sum(c.isdigit() for c in text) >= _PHONE_DIGITS
