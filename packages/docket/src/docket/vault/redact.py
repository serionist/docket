"""Redaction: credentials, keys and phone numbers out of everything a record says, before it is written anywhere.

A match becomes `[redacted:<rule>]`, so what is stored says which rule fired and never what it hid. Ids (`rid`,
`container`, `thread`, `author`, `to`) are left alone: they are how a record is found, matched and deduplicated.
`fidelity: verbatim` means not retyped by Claude, never unredacted, so `native` is redacted too, keys and values.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import accumulate
from typing import Any, cast

from docket.vault.config import Redaction
from docket.vault.neverstore import strings
from docket.vault.records import Attachment, RawRecord

MARKER = re.compile(r"\[redacted:[a-z0-9-]+\]")


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
            text = self.replace(text, self.spans(pattern, text), counts)
        return text

    def spans(self, pattern: re.Pattern[str], text: str) -> list[tuple[int, int]]:
        """What a pattern replaces, in order: each match whole, or only its secret groups. It reads the text between
        markers only, so a marker is never matched again and redacting twice is once."""
        out: list[tuple[int, int]] = []
        start = 0
        for m in [*MARKER.finditer(text), None]:
            end = len(text) if m is None else m.start()
            for found in pattern.finditer(text[start:end]):
                groups = found.groupdict().items()
                secrets = [found.span(g) for g, v in groups if g.startswith("secret") and v is not None]
                out += [(start + a, start + b) for a, b in secrets or [found.span()] if a != b]
            if m is not None:
                start = m.end()
        return out

    def replace(self, text: str, spans: list[tuple[int, int]], counts: Counter[str]) -> str:
        """The text with each span, in order, made this rule's marker."""
        out: list[str] = []
        last = 0
        for start, end in spans:
            out += [text[last:start], marker(self.name)]
            last = end
            counts[self.name] += 1
        out.append(text[last:])
        return "".join(out)


# -- the built-in rules -------------------------------------------------------------------------------------------

# A label starts a word, or a word inside an identifier in any case style: DB_PASSWORD, client-secret, clientSecret.
_LABEL_START = r"(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))"
# Between a label and its value: any horizontal space (the no-break spaces Word and Outlook put in too) and line
# breaks, emphasis and table marks (`*Password:* x`, `| Password: |`), HTML tags, even long ones a mail client wrapped
# over lines (`<td>Password:</td>\r\n<td>x`), and entities. Possessive, so a long run of it is read once and never
# backtracked into.
_SPACE = r"[^\S\r\n]"
_FILLER = rf"(?:{_SPACE}|[*_`~|]|\r?\n|<[^<>]{{0,2000}}>|&[A-Za-z0-9#]{{2,10}};)*+"
# An unquoted value has a letter or a digit, so a mark that closes the label is never taken for the secret.
_VALUE = (
    r"(?:\"(?P<secret>[^\"\n]+)\"|'(?P<secret2>[^'\n]+)'"
    r"|(?P<secret3>(?=[^\s;,<>\"'&|]*?[A-Za-z0-9])[^\s;,<>\"'&|]+))"
)

_SEP = rf"(?:[_.\-]|{_SPACE})?"
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
        re.compile(r"(?i:bearer)" + _SPACE + r"+(?P<secret>[A-Za-z0-9._~+/\-]{16,}=*)"),
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
# numbers Docket exists to keep. A + number followed by a currency or a percent is a change in an amount; its digits
# are read possessively, so a long amount can't be cut back to a group boundary the currency check doesn't see.
_AMOUNT = rf"(?!{_SPACE}*(?:(?i:ft|huf|eur|usd|gbp|chf)(?![A-Za-z])|[$€£%]))"
PHONE = Rule(
    "phone",
    (
        re.compile(r"(?<![\w+])\+(?:[ .\-()]{0,2}\d){8,15}+(?!\d)" + _AMOUNT),
        re.compile(
            r"(?<![A-Za-z0-9])"
            + rf"(?i:{'|'.join(PHONE_LABELS)})"
            + rf"(?![A-Za-z0-9])[\"']?(?:{_SPACE}|\.)*+(?:[:=#])?"
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
# The marker names of the built-in rules: Docket's own words, whatever the settings.
BUILTIN_NAMES = frozenset(rule.name for rule in BUILTIN)
_PHONE_DIGITS = 7


class Redactor:
    def __init__(self, rules: Sequence[Rule], key_labels: Sequence[tuple[str, re.Pattern[str]]] = ()) -> None:
        self.rules = tuple(rules)
        self.key_labels = tuple(key_labels)

    @classmethod
    def from_config(cls, config: Redaction) -> Redactor:
        """The rules vault.yaml turns on: the credential rules (password, api-key, private-key) and the phone rule as
        two switches, each with its native key labels, and each of the user's patterns that is enabled."""

        def on(name: str) -> bool:
            return config.phone if name == PHONE.name else config.credentials

        rules = [r for r in BUILTIN if on(r.name)]
        rules += [Rule(slug(p.name), (re.compile(p.pattern),)) for p in config.patterns if p.enabled]
        return cls(rules, [(name, label) for name, label in KEY_LABELS if on(name)])

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
        # A transcript's segments say again what its text says, so they are redacted too.
        segments = None
        if record.segments is not None:
            said = self.segment_texts([s.text for s in record.segments], counts)
            segments = [
                s.model_copy(
                    update={"speaker": self.text(s.speaker, counts) if s.speaker is not None else None, "text": t}
                )
                for s, t in zip(record.segments, said, strict=True)
            ]
        update: dict[str, Any] = {
            "title": self.text(record.title, counts) if record.title is not None else None,
            "text": self.text(record.text, counts),
            "attachments": attachments,
            "segments": segments,
            "native": self._native(record.native, counts, None),
        }
        return record.model_copy(update=update), counts

    def segment_texts(self, texts: list[str], counts: Counter[str]) -> list[str]:
        """A transcript's segment texts redacted with their neighbours as context: joined by line breaks, so a label at
        the end of one utterance and its value at the start of the next still match. A match that takes a boundary
        with it (a key block read out over two utterances) leaves no way to tell what was whose, so each segment it
        touches becomes that rule's marker whole."""
        text = "\n".join(texts)
        # Where each boundary between two segments is in the text as it is now; a taken one keeps the rule that took it.
        bounds = [end - 1 for end in accumulate(len(t) + 1 for t in texts[:-1])]
        taken: dict[int, str] = {}
        for rule in self.rules:
            for pattern in rule.patterns:
                spans = rule.spans(pattern, text)
                for start, end in reversed(spans):
                    shift = len(marker(rule.name)) - (end - start)
                    for k, at in enumerate(bounds):
                        if k in taken:
                            continue
                        if start <= at < end:
                            taken[k] = rule.name
                        elif at >= end:
                            bounds[k] = at + shift
                text = rule.replace(text, spans, Counter[str]())
        out: list[str] = []
        first = last = 0
        for k in range(len(texts)):
            if k in taken:
                continue
            end = bounds[k] if k < len(bounds) else len(text)
            out += [text[last:end]] if first == k else [marker(taken[first])] * (k - first + 1)
            first, last = k + 1, end + 1
        counts.update(_rules(out) - _rules(texts))
        return out

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
    texts += [t for s in record.segments or [] for t in (s.speaker or "", s.text)]
    texts += strings(record.native)
    return _rules(texts)


def _rules(texts: Iterable[str]) -> Counter[str]:
    """How many markers of each rule the texts hold."""
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
