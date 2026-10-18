"""A vault.yaml laid out by hand, edited in place (DR-0602): each change between the settings before and after spliced
into the text where PyYAML's composed nodes say the value stands, so every comment, every key's order and the file's
own indentation stay as they were.

`patch` gives the new text, or None when a change can't be placed: an alias anywhere in the file, a block sequence of
mappings that grows or shrinks, a key written twice, a block scalar (`|` or `>`) the change touches. The caller reads
the result back and writes it only when it reads as exactly the settings asked for; otherwise the user gets the line,
as before any of this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from docket.vault.config import VaultConfig


class _UnplacedError(Exception):
    """A change the text has no place for: the caller falls back to the line."""


@dataclass(frozen=True)
class _Splice:
    start: int
    end: int
    text: str


def patch(text: str, before: VaultConfig, after: VaultConfig) -> str | None:
    """The text with the change from `before` (what it reads as) to `after` made in place, or None."""
    try:
        root = cast(Node | None, yaml.compose(text, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]
    except yaml.YAMLError:
        return None
    if not isinstance(root, MappingNode) or _shared(root):
        return None
    edits = _Edits(text, _step(root))
    try:
        edits.mapping(root, before.model_dump(mode="json"), after.model_dump(mode="json"))
    except _UnplacedError:
        return None
    return edits.applied()


class _Edits:
    def __init__(self, text: str, step: int) -> None:
        self.text = text
        self.step = step
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.splices: list[_Splice] = []

    def applied(self) -> str:
        """The text with every splice made, from the end back, so each one's place still holds."""
        out = self.text
        for s in sorted(self.splices, key=lambda s: (s.start, s.end), reverse=True):
            out = out[: s.start] + s.text + out[s.end :]
        return out

    # -- the walk ------------------------------------------------------------------------------------------------------

    def mapping(self, node: MappingNode, old: Mapping[str, Any], new: Mapping[str, Any]) -> None:
        if node.flow_style:
            # One line, such as {}: written again whole, as it reads now.
            self.splice(node, dict(new))
            return
        pairs = _pairs(node)
        for key in [*old, *(k for k in new if k not in old)]:
            if key in old and key in new and old[key] == new[key]:
                continue
            found = pairs.get(key)
            if key not in new:
                # A key Docket no longer writes when it is empty, such as media_folders: emptied where it stands.
                if found is not None:
                    self.value(*found, old[key], [])
            elif found is None:
                self.add_key(node, key, new[key])
            else:
                self.value(*found, old.get(key), new[key])

    def value(self, key: Node, node: Node, old: Any, new: Any) -> None:
        """A value changed, under its key's node."""
        if isinstance(node, MappingNode) and isinstance(old, dict) and isinstance(new, dict):
            self.mapping(node, cast(dict[str, Any], old), cast(dict[str, Any], new))
        elif isinstance(node, SequenceNode) and isinstance(old, list) and isinstance(new, list):
            self.sequence(key, node, cast(list[Any], old), cast(list[Any], new))
        else:
            self.replace(key, node, new)

    def sequence(self, key: Node, node: SequenceNode, old: list[Any], new: list[Any]) -> None:
        if node.flow_style or not node.value or len(node.value) != len(old):
            self.replace(key, node, new)
            return
        if isinstance(node.value[0], MappingNode):
            # Patterns, matched by place: one added or taken off has no place here.
            if len(old) != len(new):
                raise _UnplacedError
            # Each item reads as a pattern's mapping, so each is a mapping node.
            for item, was, now in zip(node.value, old, new, strict=True):
                if was != now:
                    self.mapping(cast(MappingNode, item), was, now)
            return
        removed, added = _diff(old, new)
        if len(removed) == len(old) and not added:
            self.replace(key, node, [])
            return
        for i in removed:
            item = _touched(node.value[i])
            self.splices.append(
                _Splice(self._line_start(_dash(self.text, item)), self._next_line(item.end_mark.index), "")
            )
        if added:
            first = node.value[0]
            indent = self.text[self._line_start(_dash(self.text, first)) : _dash(self.text, first)]
            at = self._next_line(_touched(node.value[-1]).end_mark.index)
            lines = "".join(f"{indent}- {_flow(v)}{self.newline}" for v in added)
            self.splices.append(_Splice(at, at, self._ending(at) + lines))

    def add_key(self, node: MappingNode, key: str, value: Any) -> None:
        indent = " " * node.value[0][0].start_mark.column
        at = self._next_line(_touched(_last(node)).end_mark.index)
        block = yaml.safe_dump(
            {key: value}, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096, indent=self.step
        )
        lines = "".join(f"{indent}{line}{self.newline}" for line in block.splitlines())
        self.splices.append(_Splice(at, at, self._ending(at) + lines))

    def replace(self, key: Node, node: Node, value: Any) -> None:
        """The value under a key set: a block collection becomes a flow one, a key with none written gets one, anything
        else is written again whole where it stands."""
        _touched(node)
        if isinstance(node, (MappingNode, SequenceNode)) and not node.flow_style and node.value:
            # A block collection becomes a flow one on its key's line: the key keeps its place, and a comment there
            # stays, after the new value.
            colon = self._colon(key)
            rest = self.text[colon + 1 : self._end_of_line(colon)]
            note = rest.lstrip()
            if note and not note.startswith("#"):
                # An anchor or a tag on the key's line: no place for a value beside it.
                raise _UnplacedError
            end = self._end_of_line(_last(node).end_mark.index)
            self.splices.append(_Splice(colon + 1, end, f" {_flow(value)}{rest if note else ''}"))
            return
        if isinstance(node, ScalarNode) and node.start_mark.index == node.end_mark.index:
            # A key with no value written: the value goes right after its colon.
            colon = self._colon(key)
            self.splices.append(_Splice(colon + 1, colon + 1, f" {_flow(value)}"))
            return
        self.splice(node, value)

    def splice(self, node: Node, value: Any) -> None:
        """A scalar or a flow collection written again whole, where it stands."""
        self.splices.append(_Splice(node.start_mark.index, node.end_mark.index, _flow(value)))

    def _colon(self, key: Node) -> int:
        """Where the colon after a key stands: the first after the key's own text, never one in a comment."""
        return self.text.index(":", key.end_mark.index)

    # -- the text --------------------------------------------------------------------------------------------------

    def _line_start(self, at: int) -> int:
        return self.text.rfind("\n", 0, at) + 1

    def _end_of_line(self, at: int) -> int:
        end = self.text.find("\n", at)
        end = len(self.text) if end < 0 else end
        return end - 1 if end > 0 and self.text[end - 1] == "\r" else end

    def _next_line(self, at: int) -> int:
        """Where the line after the one holding `at` starts; the end of the text on the last line."""
        end = self.text.find("\n", at)
        return len(self.text) if end < 0 else end + 1

    def _ending(self, at: int) -> str:
        """A line break first, where the text ends without one."""
        return self.newline if at == len(self.text) and self.text and not self.text.endswith("\n") else ""


def _step(root: MappingNode) -> int:
    """How far the file indents a mapping under a key, from its first such mapping: 2 when it has none."""
    for _, value in root.value:
        if isinstance(value, MappingNode) and not value.flow_style and value.value:
            return min(max(value.value[0][0].start_mark.column, 2), 9)
    return 2


def _pairs(node: MappingNode) -> dict[str, tuple[Node, Node]]:
    """Each key's node and its value's, by the key's text."""
    out: dict[str, tuple[Node, Node]] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value in out or key.tag == "tag:yaml.org,2002:merge":
            raise _UnplacedError
        out[key.value] = (key, value)
    return out


def _touched(node: Node) -> Node:
    """A node a change splices at or takes out, refused when it holds a block scalar (`|` or `>`): its text runs on to
    the next line, so a splice there would take a line the change didn't ask for (DR-0602)."""
    todo = [node]
    while todo:
        found = todo.pop()
        if isinstance(found, ScalarNode) and found.style in ("|", ">"):
            raise _UnplacedError
        if isinstance(found, MappingNode):
            todo += [part for pair in found.value for part in pair]
        elif isinstance(found, SequenceNode):
            todo += found.value
    return node


def _shared(root: Node) -> bool:
    """Whether an alias shares a node: an edit of it would change what else reads it, and its text is in one place."""
    seen: set[int] = set()
    todo = [root]
    while todo:
        node = todo.pop()
        if id(node) in seen:
            return True
        seen.add(id(node))
        if isinstance(node, MappingNode):
            todo += [part for pair in node.value for part in pair]
        elif isinstance(node, SequenceNode):
            todo += node.value
    return False


def _last(node: Node) -> Node:
    """The node a collection's text ends with: its last scalar, or an empty flow collection."""
    while isinstance(node, (MappingNode, SequenceNode)) and node.value:
        last = cast(Node | tuple[Node, Node], node.value[-1])
        node = last[1] if isinstance(last, tuple) else last
    return node


def _dash(text: str, item: Node) -> int:
    """Where a block sequence item's dash stands."""
    return text.rfind("-", 0, item.start_mark.index)


def _diff(old: Sequence[Any], new: Sequence[Any]) -> tuple[list[int], list[Any]]:
    """The places of `old` taken out, and the values added after what stays, so `new` is what stays then those."""
    removed: list[int] = []
    j = 0
    for i, value in enumerate(old):
        if j < len(new) and new[j] == value:
            j += 1
        else:
            removed.append(i)
    return removed, list(new[j:])


def _flow(value: Any) -> str:
    """A value as one line of YAML, quoted where it must be, as vault.yaml would read it back."""
    dumped = yaml.safe_dump([value], default_flow_style=True, allow_unicode=True, sort_keys=False, width=1 << 20)
    return dumped.strip()[1:-1].strip()
