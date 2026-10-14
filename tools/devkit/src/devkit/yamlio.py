"""Reading entry files strictly, and writing them in the regime's one canonical form.

PyYAML reads; it never writes. The emitter is hand-written so the same data always gives the same bytes: a diff shows
what changed and nothing else, and `git blame` stays useful.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

import yaml
from yaml.constructor import ConstructorError

from devkit.repo import KitError

FLOW_LIST_WIDTH = 100

# Words YAML 1.1 reads as booleans or null, and shapes it reads as numbers or dates: written plain they would come
# back as something other than the string that was written.
_SPECIAL = re.compile(r"^(?:y|yes|n|no|true|false|on|off|null|~)$", re.IGNORECASE)
_NUMBERISH = re.compile(
    r"^[-+]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?|\.[0-9_]+(?:[eE][-+]?[0-9]+)?"
    r"|0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|\.inf|\.nan|[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?)$",
    re.IGNORECASE,
)
_DATEISH = re.compile(r"^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}")
_INDICATORS = set("-?:,[]{}#&*!|>'\"%@`")
_FLOW_CHARS = set(",[]{}")
_BLOCK_HEADER = re.compile(r"(?:^|[:\-]\s+)[|>][-+]?[0-9]?\s*$")


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a duplicate key, which plain safe_load would let the last one win silently."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)  # type: ignore[no-untyped-call]
            if key in seen:
                raise ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep)


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=StrictLoader)  # noqa: S506 - StrictLoader is a SafeLoader


def as_map(value: object) -> dict[str, Any]:
    """A parsed mapping with text keys; anything that isn't a mapping gives an empty one."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in cast(dict[object, Any], value).items()}


def as_list(value: object) -> list[Any]:
    """A parsed list; anything that isn't a list gives an empty one."""
    return list(cast(list[Any], value)) if isinstance(value, list) else []


@dataclass
class Comments:
    """Where a file's comments sit. Only the header and comments right above a top-level key survive a rewrite."""

    header: list[str] = field(default_factory=list[str])
    above_key: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    stray: list[int] = field(default_factory=list[int])


def _inline_comment_at(line: str) -> int:
    """The index where an inline comment starts in a line of YAML outside a block scalar, or -1."""
    quote: str | None = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote == '"':
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = None
        elif quote == "'":
            if c == "'":
                if i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif c in "\"'" and (i == 0 or line[i - 1] in " \t[{,:-"):
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return i
        i += 1
    return -1


def scan_comments(text: str) -> Comments:
    """Find the comments in an entry file, skipping the contents of block scalars."""
    result = Comments()
    lines = text.split("\n")
    n = 0
    while n < len(lines) and (lines[n].lstrip().startswith("#") or lines[n].strip() == ""):
        n += 1
    # The comment block right above the first key (after the last blank line) belongs to that key, not the header.
    blanks = [i for i in range(n) if lines[i].strip() == ""]
    pending: list[tuple[int, str]] = []
    n_header = n
    if blanks and n < len(lines) and blanks[-1] < n - 1:
        pending = [(i + 1, lines[i]) for i in range(blanks[-1] + 1, n)]
        n_header = blanks[-1]
    result.header = list(lines[:n_header])
    while result.header and result.header[-1].strip() == "":
        result.header.pop()
    block_indent: int | None = None
    for i in range(n, len(lines)):
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block_indent is not None:
            if stripped == "" or indent > block_indent:
                continue
            block_indent = None
        if stripped == "":
            result.stray.extend(no for no, _ in pending)
            pending = []
            continue
        if stripped.startswith("#"):
            pending.append((i + 1, line))
            continue
        top_key = re.match(r"^([A-Za-z_][\w-]*):", line)
        if pending:
            if top_key:
                result.above_key[top_key.group(1)] = [c for _, c in pending]
            else:
                result.stray.extend(no for no, _ in pending)
            pending = []
        cut = _inline_comment_at(line)
        if cut >= 0:
            result.stray.append(i + 1)
            line = line[:cut].rstrip()
        if _BLOCK_HEADER.search(line):
            block_indent = indent
    result.stray.extend(no for no, _ in pending)
    return result


def plain_ok(value: str, *, flow: bool) -> bool:
    """True when `value` reads back as the same string if written without quotes, in YAML 1.1 and 1.2 alike."""
    if not value or value != value.strip() or not value.isprintable():
        return False
    if _SPECIAL.match(value) or _NUMBERISH.match(value) or _DATEISH.match(value):
        return False
    if value[0] in _INDICATORS or ": " in value or " #" in value or value.endswith(":"):
        return False
    if flow and any(c in _FLOW_CHARS for c in value):
        return False
    try:
        parsed: Any = load_yaml(f"[{value}]") if flow else load_yaml(f"k: {value}")
        back: Any = parsed[0] if flow else parsed["k"]
    except (yaml.YAMLError, KeyError, IndexError, TypeError):
        return False
    return isinstance(back, str) and back == value


def quoted(value: str) -> str:
    """A double-quoted YAML scalar. JSON's string escapes are a subset of YAML's double-quoted ones."""
    return json.dumps(value, ensure_ascii=False)


def scalar(value: str, *, flow: bool = False) -> str:
    return value if plain_ok(value, flow=flow) else quoted(value)


def _block_ok(value: str) -> bool:
    """True when a multi-line value can be a `|-` block: it reads back byte for byte."""
    if "\n" not in value or value.endswith("\n") or value.startswith((" ", "\n")) or "\t" in value or "\r" in value:
        return False
    return all(ln == ln.rstrip() and (ln == "" or ln.isprintable()) for ln in value.split("\n"))


def _text(value: str, indent: int) -> list[str]:
    """A string value after `key:` or `- `: the first element continues that line, the rest are whole lines."""
    if _block_ok(value):
        pad = " " * indent
        return ["|-", *[(pad + ln) if ln else "" for ln in value.split("\n")]]
    return [scalar(value)]


@dataclass(frozen=True)
class Shape:
    """How the canonical form writes one kind's entries."""

    order: tuple[str, ...]
    flow: bool = False
    flags: frozenset[str] = frozenset()
    list_fields: frozenset[str] = frozenset()


def _value(v: Any, *, where: str) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    raise KitError(f"{where}: a {type(v).__name__} where text was expected; write it with `docs set`")


def emit_entry(entry: dict[str, Any], shape: Shape, *, where: str) -> list[str]:
    """One entry as lines of a block sequence under a top-level key (its first line starts with `  - `)."""
    unknown = [k for k in entry if k not in shape.order]
    if unknown:
        raise KitError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
    keys = [k for k in shape.order if k in entry]
    if shape.flow:
        parts: list[str] = []
        for k in keys:
            v = entry[k]
            if isinstance(v, list):
                raise KitError(f"{where}: field {k} holds a list, which a flow entry can't")
            text = _value(v, where=where)
            parts.append(f"{k}: {text if isinstance(v, bool) else scalar(text, flow=True)}")
        return ["  - { " + ", ".join(parts) + " }"]
    out: list[str] = []
    for i, k in enumerate(keys):
        lead = "  - " if i == 0 else "    "
        v = entry[k]
        if k in shape.list_fields and isinstance(v, list):
            items = [_value(x, where=f"{where}, {k}") for x in v]  # pyright: ignore[reportUnknownVariableType]
            out.extend(_emit_list(lead, k, items))
            continue
        if isinstance(v, list):
            raise KitError(f"{where}: field {k} holds a list, which only list fields may")
        if isinstance(v, bool):
            out.append(f"{lead}{k}: {'true' if v else 'false'}")
            continue
        lines = _text(_value(v, where=f"{where}, {k}"), 6)
        out.append(f"{lead}{k}: {lines[0]}")
        out.extend(lines[1:])
    return out


def _emit_list(lead: str, key: str, items: list[str]) -> list[str]:
    if not items:
        return [f"{lead}{key}: []"]
    if all("\n" not in x for x in items):
        line = f"{lead}{key}: [" + ", ".join(scalar(x, flow=True) for x in items) + "]"
        if len(line) <= FLOW_LIST_WIDTH:
            return [line]
    out = [f"{lead}{key}:"]
    for x in items:
        lines = _text(x, 8)
        out.append(f"      - {lines[0]}")
        out.extend(lines[1:])
    return out


def emit_file(sections: list[tuple[str, list[dict[str, Any]], Shape]], comments: Comments, *, where: str) -> str:
    """A whole entry file: the header, then each top-level key with the comments that sat above it and its entries."""
    out: list[str] = list(comments.header)
    if out:
        out.append("")
    for key, entries, shape in sections:
        out.extend(comments.above_key.get(key, []))
        if not entries:
            out.append(f"{key}: []")
            continue
        out.append(f"{key}:")
        for e in entries:
            out.extend(emit_entry(e, shape, where=f"{where}, {key}"))
    return "\n".join(out) + "\n"
