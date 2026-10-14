"""`uv run docs`: read and change the structured docs (decisions, conventions, terms, reading lists).

Reads print one line per entry; `show` prints one entry in full. Writers change entries only through the regime:
each edit is checked in memory, refused when it adds a problem, and written in canonical form.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from devkit.regime import Kind, Regime
from devkit.repo import KitError, repo_root, user_name
from devkit.store import EntryFile, Located, Store
from devkit.yamlio import Comments, emit_entry

_ID_SHAPE = re.compile(r"^\^([A-Z]+-)\[0-9\]\{([0-9]+)\}\$$")


def _load() -> Store:
    return Store.load(Regime.load(repo_root()))


def _first_line(text: str, width: int = 100) -> str:
    line = text.strip().split("\n", 1)[0]
    return line if len(line) <= width else line[: width - 1] + "…"


def _line(loc: Located) -> str:
    e = loc.entry
    gone = (
        ""
        if loc.living
        else (" [removed]" if "removed" in e else f" [superseded by {', '.join(e.get('superseded_by') or [])}]")
    )
    match loc.kind.name:
        case "decisions":
            return f"{loc.key}  {e.get('status', '?'):<8}  {e.get('cost', '?'):<7}  {e.get('title', '')}{gone}"
        case "conventions":
            check = "review only" if e.get("review_only") else f"enforced by {e.get('enforced_by', '?')}"
            return f"{loc.key}  [{e.get('area', '?')}]  {e.get('rule', '')}  ({check}){gone}"
        case "terms":
            return f"{loc.key}: {_first_line(str(e.get('definition', '')))}{gone}"
        case "reading":
            note = f"  {e['note']}" if e.get("note") else ""
            return f"{e.get('path')}  ({e.get('role')}){note}{gone}"
        case _:
            return f"{loc.key}{gone}"


def _in_chain(store: Store, loc: Located, path: str | None) -> bool:
    if path is None:
        return True
    if loc.file.folder is None:
        return True
    return loc.file.folder in store.chain(path)


# -- reads ---------------------------------------------------------------------------------------------------------


def cmd_door(args: argparse.Namespace) -> int:
    store = _load()
    chain = store.chain(args.path)
    binding = {"decisions": 0, "proposed": 0, "conventions": 0}
    for folder in chain:
        f = store.files.get(store.folder_file(folder))
        if f is None:
            continue
        print(f"{f.rel}")
        for e in f.sections.get("reading", []):
            loc = Located(store.regime.kind("reading"), f, e)
            if loc.living:
                print(f"  {_line(loc)}")
        for e in f.sections.get("decisions", []):
            loc = Located(store.regime.kind("decisions"), f, e)
            if loc.living:
                binding["decisions"] += 1
                binding["proposed"] += e.get("status") == "proposed"
        binding["conventions"] += sum(
            1 for e in f.sections.get("conventions", []) if Located(store.regime.kind("conventions"), f, e).living
        )
    proposed = f" ({binding['proposed']} proposed, not yet confirmed)" if binding["proposed"] else ""
    print(f"binding here: {binding['decisions']} decisions{proposed}, {binding['conventions']} conventions")
    print(f"  uv run docs list decisions --path {args.path}   uv run docs list conventions --path {args.path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = _load()
    store.regime.kind(args.kind)
    n = 0
    for loc in store.entries(args.kind):
        if not (args.all or loc.living) or not _in_chain(store, loc, args.path):
            continue
        if args.status and loc.entry.get("status") != args.status:
            continue
        print(_line(loc))
        n += 1
    if n == 0:
        print(f"(no {args.kind})")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = _load()
    loc = store.find(args.key, folder=args.folder)
    state = "" if loc.living else ("  (removed)" if "removed" in loc.entry else "  (superseded)")
    print(f"# {loc.file.rel}: {loc.kind.name}{state}")
    for line in emit_entry(loc.entry, loc.kind.shape, where=loc.where):
        print(line[4:] if line.startswith(("  - ", "    ")) else line)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    store = _load()
    needle = args.text.casefold()
    n = 0
    for loc in store.entries():
        if not (args.all or loc.living):
            continue
        for field, value in loc.entry.items():
            values: list[Any] = value if isinstance(value, list) else [value]  # pyright: ignore[reportUnknownVariableType]
            for v in values:
                if isinstance(v, str) and needle in v.casefold():
                    i = v.casefold().index(needle)
                    snippet = v[max(0, i - 40) : i + len(needle) + 40].replace("\n", " ")
                    print(f"{loc.key}  ({loc.kind.name}, {loc.file.rel})  {field}: …{snippet}…")
                    n += 1
                    break
    if n == 0:
        print("(nothing found)")
    return 0


# -- writes --------------------------------------------------------------------------------------------------------


def _value(raw: str) -> str:
    if raw.startswith("@@"):
        return raw[1:]
    if raw == "-":
        return sys.stdin.read().rstrip("\n")
    if raw.startswith("@"):
        try:
            data = Path(raw[1:]).read_bytes()
            return data.decode("utf-8").replace("\r\n", "\n").rstrip("\n")
        except UnicodeDecodeError as e:
            raise KitError(f"{raw[1:]} isn't UTF-8") from e
        except OSError as e:
            raise KitError(f"can't read {raw[1:]}: {e.strerror}") from e
    return raw


def _pairs(items: Sequence[str] | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in items or []:
        name, sep, raw = item.partition("=")
        if not sep or not name:
            raise KitError(f"{item!r}: expected field=value")
        out.append((name, _value(raw)))
    return out


def _apply(kind: Kind, entry: dict[str, Any], args: argparse.Namespace) -> None:
    unset: list[str] = list(args.unset or [])
    for name in unset:
        if name == kind.key:
            raise KitError(f"{name} names the entry and can't be unset")
        entry.pop(name, None)
    for name, value in _pairs(args.set):
        if name == kind.key and name in entry and entry[name] != value:
            raise KitError(f"{name} names the entry and can't change; add a new entry instead")
        if name in kind.list_fields:
            raise KitError(f"{name} is a list: use --add {name}=..., or --unset it first to replace it")
        if name in kind.flags:
            if value not in ("true", "false"):
                raise KitError(f"{name} takes true or false")
            entry[name] = value == "true"
        else:
            entry[name] = value
    for name, value in _pairs(args.add):
        if name not in kind.list_fields:
            raise KitError(f"{name} isn't a list field of {kind.name}; use --set")
        current: list[str] = list(entry.get(name) or [])
        current.append(value)
        entry[name] = current
    ordered = {k: entry[k] for k in kind.fields if k in entry}
    ordered.update({k: v for k, v in entry.items() if k not in ordered})
    entry.clear()
    entry.update(ordered)


def _mutate(store: Store, change: Callable[[Store], set[str]]) -> list[str]:
    before = set(store.check(citations=False))
    work = store.snapshot()
    touched = change(work)
    for rel in touched:
        work.files[rel].dirty = True
    added = sorted(set(work.check(citations=False)) - before)
    if added:
        raise KitError("refused, the change would add problems:\n  " + "\n  ".join(str(p) for p in added))
    return work.save(touched)


def _next_id(store: Store, kind: Kind) -> str:
    if kind.id_pattern is None:
        raise KitError(f"{kind.name} have no ids to number")
    shape = _ID_SHAPE.match(kind.id_pattern.pattern)
    if not shape:
        raise KitError(f"{kind.name}: can't number ids of the shape {kind.id_pattern.pattern}; give --set id=...")
    prefix, width = shape.group(1), int(shape.group(2))
    numbers = [int(loc.key[len(prefix) :]) for loc in store.entries(kind.name) if loc.key.startswith(prefix)]
    return f"{prefix}{(max(numbers) + 1 if numbers else 1):0{width}d}"


def _target_file(store: Store, kind: Kind, folder: str | None) -> EntryFile:
    if kind.table:
        rel = kind.table
        folder_value: str | None = None
    else:
        if folder is None:
            raise KitError(f"{kind.name} bind a folder: give --in <folder> ('.' for the root)")
        folder_value = "" if folder in (".", "") else Path(folder).as_posix().strip("/")
        if not (store.root / folder_value).is_dir():
            raise KitError(f"{folder} isn't a folder")
        rel = store.folder_file(folder_value)
    f = store.files.get(rel)
    if f is None:
        place = f"`{folder_value}`" if folder_value else "the repo"
        what = f"The {kind.name} table" if kind.table else f"What binds {place}: reading list, decisions, conventions"
        header = [
            f"# {what}. Entries change only through `uv run docs`;",
            "# docs/docs-regime.md explains the schema.",
        ]
        f = EntryFile(rel=rel, folder=folder_value, raw="", comments=Comments(header=header), created=True)
        store.files[rel] = f
    return f


def cmd_add(args: argparse.Namespace) -> int:
    store = _load()
    kind = store.regime.kind(args.kind)

    def change(work: Store) -> set[str]:
        f = _target_file(work, kind, args.folder)
        entry: dict[str, Any] = {}
        if kind.numbered and not any(n == kind.key for n, _ in _pairs(args.set)):
            entry[kind.key] = _next_id(work, kind)
        for name, default in kind.defaults.items():
            entry[name] = {"today": date.today().isoformat(), "git-user": user_name(work.root)}.get(default, default)
        _apply(kind, entry, args)
        items = f.sections.setdefault(kind.name, [])
        if kind.name == "terms":
            items.append(entry)
            items.sort(key=lambda e: str(e.get("term", "")).casefold())
        else:
            items.append(entry)
        print(f"added {entry.get(kind.key)} to {f.rel}")
        return {f.rel}

    _report(_mutate(store, change))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    store = _load()

    def change(work: Store) -> set[str]:
        loc = work.find(args.key, folder=args.folder)
        _apply(loc.kind, loc.entry, args)
        return {loc.file.rel}

    _report(_mutate(store, change))
    return 0


def cmd_supersede(args: argparse.Namespace) -> int:
    store = _load()

    def change(work: Store) -> set[str]:
        old = work.find(args.old, kind="decisions")
        new = work.find(args.by, kind="decisions")
        if old.key == new.key:
            raise KitError("a decision can't supersede itself")
        old.entry["superseded_by"] = [*(old.entry.get("superseded_by") or []), new.key]
        old.entry["status"] = "superseded"
        new.entry["supersedes"] = [*(new.entry.get("supersedes") or []), old.key]
        for loc in (old, new):
            ordered = {k: loc.entry[k] for k in loc.kind.fields if k in loc.entry}
            loc.entry.clear()
            loc.entry.update(ordered)
        return {old.file.rel, new.file.rel}

    _report(_mutate(store, change))
    _living_citations(_load(), args.old)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    store = _load()

    def change(work: Store) -> set[str]:
        loc = work.find(args.key, folder=args.folder)
        loc.entry["removed"] = date.today().isoformat()
        loc.entry["removed_why"] = _value(args.why)
        return {loc.file.rel}

    _report(_mutate(store, change))
    _living_citations(_load(), args.key)
    return 0


def _living_citations(store: Store, ident: str) -> None:
    hits = [f"{rel}:{line}" for rel, line, cited in store.citations() if cited == ident and not store.is_record(rel)]
    if hits:
        print(f"{ident} is still cited where the gate will now flag it:")
        for h in hits:
            print(f"  {h}")


def _report(written: list[str]) -> None:
    print("\n".join(f"wrote {rel}" for rel in written) if written else "nothing changed")


def cmd_check(_: argparse.Namespace) -> int:
    problems = _load().check()
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} problem(s)")
        return 1
    print("docs ok")
    return 0


def cmd_format(_: argparse.Namespace) -> int:
    store = _load()
    refused: list[str] = []
    for f in store.files.values():
        if f.error:
            refused.append(f"{f.rel}: {f.error}")
            continue
        try:
            f.dirty = True
            store.save({f.rel})
        except KitError as e:
            refused.append(str(e))
    for r in refused:
        print(f"refused {r}")
    return 1 if refused else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="docs", description=__doc__.split("\n", 1)[0] if __doc__ else None)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("door", help="what to read before touching a path, and what binds it")
    s.add_argument("path", nargs="?", default=".")
    s.set_defaults(func=cmd_door)

    s = sub.add_parser("list", help="one line per entry of a kind")
    s.add_argument("kind")
    s.add_argument("--path", help="only entries that bind this path")
    s.add_argument("--status")
    s.add_argument("--all", action="store_true", help="include superseded and removed entries")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="one entry in full")
    s.add_argument("key")
    s.add_argument("--in", dest="folder")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("search", help="entries whose text contains a phrase")
    s.add_argument("text")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_search)

    def writer(name: str, help_text: str) -> argparse.ArgumentParser:
        w = sub.add_parser(name, help=help_text)
        w.add_argument("--set", action="append", metavar="FIELD=VALUE", help="VALUE, @file, - for stdin")
        w.add_argument("--add", action="append", metavar="FIELD=VALUE", help="append to a list field")
        w.add_argument("--unset", action="append", metavar="FIELD")
        w.add_argument("--in", dest="folder", help="the folder whose index.yaml holds it ('.' for the root)")
        return w

    s = writer("add", "add an entry")
    s.add_argument("kind")
    s.set_defaults(func=cmd_add)

    s = writer("set", "change an entry")
    s.add_argument("key")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("supersede", help="mark a decision superseded by a newer one")
    s.add_argument("old")
    s.add_argument("--by", required=True)
    s.set_defaults(func=cmd_supersede)

    s = sub.add_parser("remove", help="soft-delete an entry: it stays, with the date and why")
    s.add_argument("key")
    s.add_argument("--why", required=True)
    s.add_argument("--in", dest="folder")
    s.set_defaults(func=cmd_remove)

    sub.add_parser("check", help="check every entry file against the regime").set_defaults(func=cmd_check)
    sub.add_parser("format", help="rewrite every entry file in canonical form").set_defaults(func=cmd_format)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KitError as e:
        print(f"docs: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
