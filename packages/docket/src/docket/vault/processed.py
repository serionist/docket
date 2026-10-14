"""Processed docs: markdown with YAML frontmatter that says which raw records a summary covers.

The frontmatter, in the order Docket writes it:

    id: recording-note:2026-10-14-supplier-call     <type>:<slug>
    type: recording-note                            one of DOC_TYPES, the same as the id's prefix
    rev: 1                                          the revision, set by doc_put
    covers: [the rids it summarizes]                at least one, except in a legacy note
    by: claude-opus-5.5, sync 2026-10-14T16:07      <who>[, <run>]
    supersedes: [other docs it replaces]
    entities: [entity slugs]
    old_paths: [paths other files cite it by]       a legacy note only
    import_hash: sha256:...                         a legacy note only: the body the import last wrote

A doc lives at processed/<type>/<yyyy>/<mm>/<slug>.md when its slug starts with a date, else processed/<type>/<slug>.md.
The frontmatter is read with YAML's base loader, so every value is text (`no` and `2026` stay slugs), and written by
the renderer here. Writing is strict; reading is lenient, so an older machine still reads a newer one's docs. This
format is a one-way door (DR-0109): docs in users' vaults depend on it.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator

from docket.errors import DocketError
from docket.vault.anchors import is_blank, normalize_newlines
from docket.vault.records import RID

log = logging.getLogger(__name__)

DOC_TYPES = ("recording-note", "thread-summary", "digest", "minutes", "brief", "correction", "legacy-note")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 80
# Windows can't create a file with one of these names, whatever its extension.
DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
DATED = re.compile(r"^(\d{4})-(\d{2})-\d{2}(?:-|$)")
BY_MAX = 200
NULLS = frozenset({"", "~", "null", "Null", "NULL"})
IMPORT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_BREAK = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_TABLE_SEP = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")

Role = Literal["content", "free", "skip"]


@dataclass(frozen=True)
class DocProblem:
    """One thing wrong with a doc: its line in the text as given (None for the doc as a whole) and what is wrong."""

    line: int | None
    message: str
    excerpt: str = ""

    def __str__(self) -> str:
        where = "doc" if self.line is None else f"line {self.line}"
        if self.excerpt:
            short = self.excerpt.strip()
            where += f' ("{short[:40]}{"..." if len(short) > 40 else ""}")'
        return f"{where}: {self.message}"


class DocRefusedError(DocketError):
    """A doc that can't be stored, with every problem found."""

    def __init__(self, problems: list[DocProblem]) -> None:
        self.problems = problems
        first = str(problems[0]) if problems else "refused"
        more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        super().__init__(f"the doc was refused: {first}{more}")


# -- ids and paths ------------------------------------------------------------------------------------------------


def id_problem(doc_id: str) -> str | None:
    """What is wrong with a doc id, or None. The type isn't checked here: a lenient read takes unknown types."""
    doc_type, sep, slug = doc_id.partition(":")
    if not sep or not doc_type or not SLUG.match(doc_type):
        return f"{doc_id!r} isn't <type>:<slug>"
    if not SLUG.match(slug):
        return f"the slug {slug!r} is lowercase letters, digits and inner hyphens"
    if len(slug) > SLUG_MAX:
        return f"the slug is {len(slug)} characters; at most {SLUG_MAX}"
    if slug in DEVICE_NAMES:
        return f"the slug {slug!r} is a name Windows reserves for a device"
    return None


def doc_path(doc_id: str) -> PurePosixPath:
    """Where a doc lives, from its id alone."""
    doc_type, _, slug = doc_id.partition(":")
    dated = DATED.match(slug)
    folder = PurePosixPath("processed", doc_type)
    if dated:
        folder = folder / dated.group(1) / dated.group(2)
    return folder / f"{slug}.md"


# -- the frontmatter ----------------------------------------------------------------------------------------------


def _as_list(value: Any) -> Any:
    """A scalar where a list belongs: empty for a YAML null, else a list of one."""
    if isinstance(value, str):
        return [] if value in NULLS else [value]
    return value


def _no_repeats(values: list[str], what: str) -> list[str]:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            raise ValueError(f"{v} is listed twice in {what}")
        seen.add(v)
    return values


def old_path_problem(path: str) -> str | None:
    """What is wrong with an old path as a legacy note keeps it, or None. It is relative, with `/` separators, in NFC,
    with no empty, `.` or `..` part, no `:` and no control character, so it reads the same on every system and can't
    be taken for a rid or a drive."""
    if not path:
        return "an old path can't be empty"
    if "\\" in path:
        return f"{path!r} has a \\; an old path is written with /"
    if path.startswith("/"):
        return f"{path!r} starts with /; an old path is relative to its repo"
    if ":" in path:
        return f"{path!r} has a ':', which would read as a source or a drive"
    if any(not ch.isprintable() for ch in path):
        return f"{path!r} has a control character"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return f"{path!r} has an empty, . or .. part"
    if unicodedata.normalize("NFC", path) != path:
        return f"{path!r} isn't in NFC"
    return None


class DocMeta(BaseModel):
    """A processed doc's frontmatter."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    rev: int = Field(default=0, ge=0)
    covers: list[str] = Field(default_factory=list[str])
    by: str
    supersedes: list[str] = Field(default_factory=list[str])
    entities: list[str] = Field(default_factory=list[str])
    old_paths: list[str] = Field(default_factory=list[str])
    import_hash: str | None = None

    @field_validator("rev", mode="before")
    @classmethod
    def _rev(cls, value: Any) -> Any:
        return 0 if isinstance(value, str) and value in NULLS else value

    @field_validator("covers", "supersedes", "entities", "old_paths", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return _as_list(value)

    @field_validator("import_hash", mode="before")
    @classmethod
    def _null_hash(cls, value: Any) -> Any:
        return None if isinstance(value, str) and value in NULLS else value

    @field_validator("old_paths")
    @classmethod
    def _old_paths(cls, value: list[str]) -> list[str]:
        for path in value:
            problem = old_path_problem(path)
            if problem:
                raise ValueError(problem)
        return _no_repeats(value, "old_paths")

    @field_validator("import_hash")
    @classmethod
    def _import_hash(cls, value: str | None) -> str | None:
        if value is not None and not IMPORT_HASH.match(value):
            raise ValueError("sha256: and 64 lowercase hex digits")
        return value

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        problem = id_problem(value)
        if problem:
            raise ValueError(problem)
        return value

    @field_validator("covers")
    @classmethod
    def _covers(cls, value: list[str]) -> list[str]:
        for rid in value:
            if not RID.match(rid):
                raise ValueError(f"{rid!r} isn't a rid: <source>:<id>")
        return _no_repeats(value, "covers")

    @field_validator("supersedes")
    @classmethod
    def _supersedes(cls, value: list[str]) -> list[str]:
        for doc_id in value:
            problem = id_problem(doc_id)
            if problem:
                raise ValueError(problem)
        return _no_repeats(value, "supersedes")

    @field_validator("entities")
    @classmethod
    def _entities(cls, value: list[str]) -> list[str]:
        for slug in value:
            if not SLUG.match(slug):
                raise ValueError(f"the entity {slug!r} isn't a slug: lowercase letters, digits and inner hyphens")
        return _no_repeats(value, "entities")

    @field_validator("by")
    @classmethod
    def _by(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("say who or what wrote it: <who>[, <run>]")
        if len(value) > BY_MAX:
            raise ValueError(f"at most {BY_MAX} characters")
        if any(ch.isspace() and ch != " " for ch in value) or any(not ch.isprintable() for ch in value):
            raise ValueError("one line of text")
        return value

    @model_validator(mode="after")
    def _whole(self, info: ValidationInfo) -> DocMeta:
        lenient = bool(info.context and info.context.get("lenient"))
        prefix = self.id.partition(":")[0]
        if self.type != prefix:
            raise ValueError(f"type is {self.type!r} but the id starts with {prefix!r}")
        if self.type not in DOC_TYPES:
            if not lenient:
                raise ValueError(f"type {self.type!r} isn't one of {', '.join(DOC_TYPES)}")
            log.warning("%s has a type this Docket doesn't know: %s", self.id, self.type)
        if not self.covers and self.type != "legacy-note" and not lenient:
            raise ValueError("covers names at least one rid")
        if self.type != "legacy-note" and (self.old_paths or self.import_hash is not None):
            if not lenient:
                raise ValueError("old_paths and import_hash belong to a legacy note only")
            log.warning("%s has legacy-note fields this Docket keeps only on legacy notes", self.id)
        if self.id in self.supersedes:
            raise ValueError("a doc can't supersede itself")
        return self

    @property
    def who(self) -> str:
        return self.by.partition(",")[0].strip()

    @property
    def run(self) -> str | None:
        return self.by.partition(",")[2].strip() or None


# -- the doc ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessedDoc:
    """A doc: its frontmatter, its body (LF line endings), how many lines the frontmatter took in the text it was read
    from (both --- lines included), on a lenient read the keys this Docket doesn't know, and the text it was read
    from with LF line endings."""

    meta: DocMeta
    body: str
    head_lines: int
    extra: dict[str, Any] = field(default_factory=dict[str, Any])
    source: str = ""

    def lines(self) -> list[tuple[int, str]]:
        """The body's lines with their line numbers in the file."""
        return [(self.head_lines + 1 + i, line) for i, line in enumerate(self.body.split("\n"))]

    def roles(self) -> list[tuple[int, str, Role]]:
        numbered = self.lines()
        roles = line_roles([line for _, line in numbered])
        return [(n, line, role) for (n, line), role in zip(numbered, roles, strict=True)]

    def render(self, rev: int | None = None) -> str:
        m = self.meta
        head = [
            "---",
            f"id: {_scalar(m.id, 'value')}",
            f"type: {_scalar(m.type, 'value')}",
            f"rev: {m.rev if rev is None else rev}",
            *_block("covers", m.covers),
            f"by: {_scalar(m.by, 'value')}",
            *_block("supersedes", m.supersedes),
            "entities: [" + ", ".join(_scalar(e, "flow") for e in m.entities) + "]",
            *(_block("old_paths", m.old_paths) if m.old_paths else []),
            *([f"import_hash: {m.import_hash}"] if m.import_hash is not None else []),
            "---",
        ]
        rest = self.body.rstrip("\n")
        return "\n".join(head) + "\n" + (rest + "\n" if rest else "")


def _block(key: str, values: list[str]) -> list[str]:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:", *(f"  - {_scalar(v, 'item')}" for v in values)]


def _scalar(value: str, context: Literal["value", "item", "flow"]) -> str:
    """A value plain when every YAML reader gives it back unchanged where it stands, else double-quoted."""
    probe, expected = {
        "value": (f"k: {value}", {"k": value}),
        "item": (f"- {value}", [value]),
        "flow": (f"[{value}]", [value]),
    }[context]
    try:
        plain = (
            value == value.strip()
            and yaml.safe_load(probe) == expected
            and yaml.load(probe, Loader=yaml.BaseLoader) == expected  # noqa: S506 - the base loader builds no objects
        )
    except yaml.YAMLError:
        plain = False
    return value if plain else double_quoted(value)


def _printable(code: int) -> bool:
    """YAML's printable set, less the characters it reads as line breaks inside a quoted scalar."""
    return (
        0x20 <= code <= 0x7E
        or (0xA0 <= code <= 0xD7FF and code not in (0x2028, 0x2029))
        or 0xE000 <= code <= 0xFFFD
        or code >= 0x10000
    )


def double_quoted(value: str) -> str:
    """A YAML double-quoted scalar any reader gives back as the same text: a character outside YAML's printable set
    (DEL, C1 controls, U+FFFE, a lone surrogate) is written as an escape, since a reader refuses it raw."""
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif _printable(code):
            out.append(ch)
        else:
            out.append(f"\\u{code:04x}")
    out.append('"')
    return "".join(out)


def key_line(head: list[str], key: str) -> int | None:
    """The line number in the file of a frontmatter key, for a problem's location."""
    for i, line in enumerate(head, start=2):
        if line.startswith(f"{key}:"):
            return i
    return None


def parse_doc(text: str, *, lenient: bool = False) -> ProcessedDoc:
    """Read a doc. Strict (for writing) refuses unknown keys and types; lenient (for reading) keeps them and warns."""
    lines = normalize_newlines(text.removeprefix("\ufeff")).split("\n")
    if lines[0] != "---":
        raise DocRefusedError([DocProblem(1, "a doc starts with a --- line, then its frontmatter, then another ---")])
    try:
        close = lines.index("---", 1)
    except ValueError:
        raise DocRefusedError([DocProblem(1, "the frontmatter has no closing --- line")]) from None
    head = lines[1:close]
    try:
        data: Any = yaml.load("\n".join(head), Loader=yaml.BaseLoader)  # noqa: S506 - the base loader builds no objects
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 2 if mark is not None else None
        raise DocRefusedError([DocProblem(line, f"the frontmatter isn't YAML: {getattr(e, 'problem', e)}")]) from None
    if not isinstance(data, dict):
        raise DocRefusedError([DocProblem(2, "the frontmatter is keys and values: id, type, covers, by, ...")])
    fields = {str(k): v for k, v in cast(dict[Any, Any], data).items()}
    extra: dict[str, Any] = {}
    if lenient:
        extra = {k: v for k, v in fields.items() if k not in DocMeta.model_fields}
        fields = {k: v for k, v in fields.items() if k in DocMeta.model_fields}
        if extra:
            log.warning("a doc has keys this Docket doesn't know, kept as they are: %s", ", ".join(sorted(extra)))
    try:
        meta = DocMeta.model_validate(fields, context={"lenient": lenient})
    except ValidationError as e:
        problems: list[DocProblem] = []
        for err in e.errors():
            key = str(err["loc"][0]) if err["loc"] else ""
            message = str(err["msg"]).removeprefix("Value error, ")
            problems.append(DocProblem(key_line(head, key) if key else None, f"{key or 'frontmatter'}: {message}"))
        raise DocRefusedError(problems) from None
    return ProcessedDoc(meta, "\n".join(lines[close + 1 :]), close + 1, extra, "\n".join(lines))


# -- which lines need anchors -------------------------------------------------------------------------------------


def _table_separator(line: str) -> bool:
    return "|" in line and _TABLE_SEP.match(line) is not None


def line_roles(lines: list[str]) -> list[Role]:
    """For each line: `content` needs anchors at its end; `free` needs none (a blank line, a heading, a thematic break,
    a table's header and separator rows); `skip` isn't read for anchors (fenced code with its fences, HTML comments)."""
    roles: list[Role] = []
    fence: str | None = None
    comment = False
    for i, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if fence is not None:
            roles.append("skip")
            m = _FENCE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and is_blank(line[m.end() :]):
                fence = None
        elif comment:
            roles.append("skip")
            comment = "-->" not in line
        elif m := _FENCE.match(line):
            roles.append("skip")
            fence = m.group(1)
        elif stripped.startswith("<!--"):
            roles.append("skip")
            comment = "-->" not in stripped[4:]
        elif (
            is_blank(line)
            or _HEADING.match(line)
            or _BREAK.match(line)
            or _table_separator(line)
            or (i + 1 < len(lines) and _table_separator(lines[i + 1]))
        ):
            roles.append("free")
        else:
            roles.append("content")
    return roles
