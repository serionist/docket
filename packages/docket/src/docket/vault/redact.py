"""Redaction: credentials, keys and phone numbers out of everything a record says, before it is written anywhere.

A match becomes `[redacted:<rule>]`, so what is stored says which rule fired and never what it hid. Every rule reads
the same text, the text as given, so whatever any rule matches there is redacted whole, in whatever order the rules
come; spans that overlap become one marker (DR-0525, DR-0526). Ids (`rid`, `container`, `thread`, `author`, `to`) are
left alone: they are how a record is found, matched and deduplicated. `fidelity: verbatim` means not retyped by
Claude, never unredacted, so `native` is redacted too, keys and values.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import accumulate
from typing import Any, cast

from docket.vault.config import Redaction
from docket.vault.neverstore import strings
from docket.vault.records import Attachment, RawRecord

MARKER = re.compile(r"\[redacted:[a-z0-9-]+\]")
# How many times the rules read a text: once as given, then the gaps between what they found, as a second redaction
# would read them, and twice more for a match one of those finds at a marker. A pass runs only when the one before it
# found something; the cap keeps a pattern that finds a few more characters each pass (`\b[0-9a-f]{8}` over a long hex
# run after a marker) from taking a pass per match (DR-0525).
PASSES = 4


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

    def find(self, text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
        """What the rule replaces in `text[start:end]`, read as a text of its own, in `text`'s offsets: each match
        whole, or only its secret groups."""
        piece = text[start:end]
        for pattern in self.patterns:
            for found in pattern.finditer(piece):
                groups = found.groupdict().items()
                secrets = [found.span(g) for g, v in groups if g.startswith("secret") and v is not None]
                yield from ((start + a, start + b) for a, b in secrets or [found.span()] if a != b)


@dataclass(frozen=True, order=True)
class Span:
    """A stretch of a text one marker replaces, and the index of the rule that names it."""

    start: int
    end: int
    rule: int


def _gaps(size: int, cuts: Iterable[tuple[int, int]]) -> Iterator[tuple[int, int]]:
    """The stretches of a text of `size` characters outside the ones cut, in order."""
    last = 0
    for start, end in sorted(cuts):
        if start > last:
            yield last, start
        last = max(last, end)
    if last < size:
        yield last, size


def _merge(spans: Iterable[Span]) -> list[Span]:
    """Spans that overlap as one, named by the rule with the longest span in it, a tie by rule order; spans that only
    touch stay apart (DR-0526)."""
    out: list[Span] = []
    best = Span(0, 0, 0)
    for span in sorted(spans):
        if out and span.start < out[-1].end:
            if (span.end - span.start, -span.rule) > (best.end - best.start, -best.rule):
                best = span
            out[-1] = Span(out[-1].start, max(out[-1].end, span.end), best.rule)
        else:
            best = span
            out.append(span)
    return out


# -- the built-in rules -------------------------------------------------------------------------------------------

# A label starts a word, or a word inside an identifier in any case style: DB_PASSWORD, client-secret, clientSecret.
_LABEL_START = r"(?:(?<![A-Za-z0-9])|(?<=[a-z0-9])(?=[A-Z]))"
# Between a label and its value: any horizontal space (the no-break spaces Word and Outlook put in too) and line
# breaks, emphasis and table marks (`*Password:* x`, `| Password: |`), HTML tags, even long ones a mail client wrapped
# over lines (`<td>Password:</td>\r\n<td>x`), and entities. Possessive, so a long run of it is read once and never
# backtracked into.
_SPACE = r"[^\S\r\n]"
_FILLER = rf"(?:{_SPACE}|[*_`~|]|\r?\n|<[^<>]{{0,2000}}>|&[A-Za-z0-9#]{{2,10}};)*+"
# A word of an unquoted value: a quote, `&`, `<`, `>`, `|`, `,` and `;` end it, the marks of HTML, tables and lists.
_WORD = r"[^\s;,<>\"'&|]"
# An unquoted value has a letter or a digit, so a mark that closes the label is never taken for the secret.
_FIRST = rf"(?={_WORD}*?[A-Za-z0-9]){_WORD}++"
# A word of prose: letters, perhaps joined by hyphens, dots or apostrophes, after an opening bracket and before
# closing marks (`and`, `follow-up`, `e.g.`, `(see`, `more.`).
_PROSE = rf"\(?[^\W\d_]++(?:[-.\u2019][^\W\d_]++)*+[.!?:)]*+(?!{_WORD})"
# A password's value goes on along its line through each further word that holds a digit or a symbol: `Kq7 x!Lm 22`,
# a PIN read out in groups. A word of prose ends it, so a sentence after a one-word password stays, and so does a
# label (`Username:`, `user=`); a line break or a mark that ends a word ends it too. Possessive: a line is read once.
_MORE = rf"(?:{_SPACE}++(?!\w++[:=]|{_PROSE}){_WORD}++)*+"


def _value(unquoted: str) -> str:
    return rf"(?:\"(?P<secret>[^\"\n]+)\"|'(?P<secret2>[^'\n]+)'|(?P<secret3>{unquoted}))"


_VALUE = _value(_FIRST)
_PASSWORD_VALUE = _value(_FIRST + _MORE)

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
    # A dial-in PIN, in English and Hungarian; a whole word, so opinion, spinning and pinned stay.
    rf"pin{_SEP}k[oó]d",
    rf"pin{_SEP}code",
    r"pin",
]
PHONE_LABELS = [r"phone", r"telephone", r"tel", r"mobile", r"mobil", r"mob", r"cell", r"telefon", r"fax"]


def _labelled(labels: list[str], value: str = _VALUE) -> re.Pattern[str]:
    """A label that ends its key (an optional closing quote), then `:`, `=`, `:=` or `=>`, then the value."""
    return re.compile(
        _LABEL_START
        + rf"(?i:{'|'.join(labels)})"
        + r"(?![A-Za-z0-9])[\"']?"
        + _FILLER
        + r"(?::=|=>|[:=])"
        + _FILLER
        + value
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
        _labelled(PASSWORD_LABELS, _PASSWORD_VALUE),
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
        return self._write(text, self.spans(text), counts)

    def spans(self, text: str) -> list[Span]:
        """What markers replace in a text, in order, in its own offsets. Every rule reads the text as given, between
        the markers already in it, so a marker is never matched again; then the gaps between what they found, each as
        a text of its own, as a second redaction would read them, so redacting twice is once while PASSES suffice
        (DR-0525)."""
        found: list[Span] = []
        marked = [m.span() for m in MARKER.finditer(text)]
        for _ in range(PASSES):
            new = self._read(text, [*marked, *((s.start, s.end) for s in found)])
            if not new:
                break
            found = sorted([*found, *new])
        return found

    def _read(self, text: str, cuts: list[tuple[int, int]]) -> list[Span]:
        """Every rule's spans in the gaps the cuts leave, each gap read as a text of its own, overlaps merged."""
        return _merge(
            Span(a, b, i)
            for start, end in _gaps(len(text), cuts)
            for i, rule in enumerate(self.rules)
            for a, b in rule.find(text, start, end)
        )

    def _write(self, text: str, spans: list[Span], counts: Counter[str]) -> str:
        """The text with each span made its rule's marker."""
        out: list[str] = []
        last = 0
        for span in spans:
            name = self.rules[span.rule].name
            out += [text[last : span.start], marker(name)]
            counts[name] += 1
            last = span.end
        out.append(text[last:])
        return "".join(out)

    def record(self, record: RawRecord) -> tuple[RawRecord, Counter[str]]:
        """The record with everything it says redacted, and how many markers each rule added."""
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
        out = [self._write(t, spans, Counter[str]()) for t, spans in zip(texts, self.segment_spans(texts), strict=True)]
        counts.update(_rules(out) - _rules(texts))
        return out

    def segment_spans(self, texts: list[str]) -> list[list[Span]]:
        """What markers replace in each segment text, in its own offsets, read as `segment_texts` reads them: a segment
        a merged span ran into is one span, the whole segment, named by the first span that ran over a boundary of its
        run of such segments."""
        text = "\n".join(texts)
        starts = list(accumulate((len(t) + 1 for t in texts[:-1]), initial=0))[: len(texts)]
        breaks = [start - 1 for start in starts[1:]]
        # A boundary a span ran over, by its index, and that span's rule.
        taken: dict[int, int] = {}
        found: list[Span] = []
        marked = [m.span() for m in MARKER.finditer(text)]
        for _ in range(PASSES):
            whole = [(starts[k], starts[k] + len(t)) for k, t in enumerate(texts) if k in taken or k - 1 in taken]
            new = self._read(text, [*marked, *((s.start, s.end) for s in found), *whole])
            if not new:
                break
            for span in new:
                for k in range(bisect_left(breaks, span.start), bisect_left(breaks, span.end)):
                    taken.setdefault(k, span.rule)
            found = sorted([*found, *new])
        inside: list[list[Span]] = [[] for _ in texts]
        for span in found:
            k = bisect_right(starts, span.start) - 1
            inside[k].append(Span(span.start - starts[k], span.end - starts[k], span.rule))
        first = 0
        for k, t in enumerate(texts):
            if k - 1 not in taken:
                first = k
            if k in taken or k - 1 in taken:
                inside[k] = [Span(0, len(t), taken[first])]
        return inside

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
