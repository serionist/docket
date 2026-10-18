"""Two clones' ops merge into one ledger (DR-0610, DR-0612): any order of lines folds the same, and real clones merged
or rebased by git meet no conflict, a clock behind and a shared machine name included."""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, git, make_config, sh
from ledger_kit import ADD_FIELDS, DAY, NOW

from docket.ledger import ops, store
from docket.ledger.fold import Ledger, fold
from docket.ledger.ops import Line, Op
from docket.vault.vault import Vault

MACHINES = ("desk", "laptop", "phone")
KINDS = ("log", "triage", "set", "snooze", "done", "reopen", "material", "undo", "drop", "archive")


def snapshot(ledger: Ledger) -> list[tuple[Any, ...]]:
    """Everything an item holds, its log included, as plain values."""
    return [
        (i.id, i.number, i.state, i.title, i.due, i.triage, i.until, i.closed, i.archived, i.notes,
         tuple(i.materials), tuple((e.op.id, e.applied, e.undone_by, e.line) for e in i.log))
        for i in sorted(ledger.items.values(), key=lambda i: i.id)
    ]  # fmt: skip


def history(seed: int) -> list[Op]:
    """Three machines' ops over a fortnight, each minted on its own clock after what it had seen: a clock behind, ops
    on one item from two machines, undos, closes from both sides."""
    rng = random.Random(seed)  # noqa: S311 - a seeded test history, not secrets
    made: list[Op] = []
    items: list[str] = []
    seen = dict.fromkeys(MACHINES, "")
    for step in range(120):
        machine = rng.choice(MACHINES)
        skew = timedelta(minutes=-90 if machine == "phone" else 0)
        when = NOW - timedelta(days=14) + timedelta(minutes=step * 7) + skew
        (op_id,) = ops.mint(seen[machine] or None, when, 1, rand=rng.randbytes)
        kind = "add" if not items or rng.random() < 0.2 else rng.choice(KINDS)
        item = op_id if kind == "add" else rng.choice(items)
        keys: dict[str, Any] = {
            "add": {"number": len(items) + 1, "fields": {**ADD_FIELDS, "title": f"Item {step}"}},
            "log": {"note": f"step {step}"},
            "triage": {"verdict": rng.choice(["chase", "me", None])},
            "set": {"fields": {"due": f"2026-11-{rng.randint(1, 28):02d}", "notes": rng.choice(["a", None])}},
            "snooze": {"until": DAY + timedelta(days=rng.randint(1, 9))},
            "material": {"ref": rng.choice(["a.md", "b.md"]), "remove": rng.random() < 0.3},
            "undo": {"of": rng.choice([o.id for o in made if o.item == item] or [item])},
            "drop": {"note": "why"},
        }.get(kind, {})
        made.append(Op(schema_version=1, id=op_id, item=item, op=kind, day=when.date(), machine=machine, by="user",
                       via="page", **keys))  # fmt: skip
        if kind == "add":
            items.append(item)
        # What a machine has seen: its own ops, and now and then everyone's, as a pull brings them.
        seen[machine] = op_id
        if rng.random() < 0.3:
            for m in MACHINES:
                seen[m] = max(seen[m], *(o.id for o in made))
    return made


@pytest.mark.parametrize("seed", range(6))
def test_any_order_of_any_machine_s_lines_folds_to_one_ledger(seed: int) -> None:
    made = history(seed)
    texts = [(ops.rel_of(o), o.text()) for o in made]
    want = snapshot(fold(Line(Op.model_validate_json(t), t, rel, n) for n, (rel, t) in enumerate(texts, 1)))
    rng = random.Random(seed + 100)  # noqa: S311 - a seeded shuffle
    for _ in range(5):
        # A union merge can put the lines in any order and leave one twice.
        shuffled = texts + rng.sample(texts, k=len(texts) // 4)
        rng.shuffle(shuffled)
        got = fold(Line(Op.model_validate_json(t), t, rel, n) for n, (rel, t) in enumerate(shuffled, 1))
        assert snapshot(got) == want
        assert not [p for p in got.problems if "texts" in p[1]]


def clone(source: Vault, folder: Path, machine: str, monkeypatch: pytest.MonkeyPatch) -> Vault:
    sh(folder.parent, "git", "clone", "-q", str(source.root), str(folder))
    monkeypatch.setenv("DOCKET_MACHINE", machine)
    return Vault.open(folder)


def add(title: str) -> dict[str, Any]:
    return {"op": "add", "fields": {**ADD_FIELDS, "title": title}}


def test_two_clones_with_one_machine_name_merge_and_rebase_with_no_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case two clones on one host meet: one machine name, so both write the same month file."""
    first = empty_vault(tmp_path, make_config())
    store.write(first, [add("Before the clone")], now=NOW)
    second = clone(first, tmp_path / "second", "test-box", monkeypatch)
    store.write(first, [add("From the first"), {"op": "done", "item": "A-0001"}], now=NOW + timedelta(minutes=1))
    store.write(second, [add("From the second"), {"op": "drop", "item": "A-0001", "note": "dup"}],
                now=NOW + timedelta(minutes=2))  # fmt: skip
    git(second.root, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    merged = fold(ops.read_all(second.root).lines)
    assert sorted(i.title for i in merged.items.values()) == ["Before the clone", "From the first", "From the second"]
    twos = sorted(i.id for i in merged.items.values() if i.claimed == 2)
    assert [i.id for i in merged.clashes()] == twos[1:]
    # The next write renumbers the clash. Then each clone writes the first ops of a new month, so both add one file,
    # and the second rebases onto the first.
    store.write(second, [{"op": "log", "item": "A-0001", "note": "after the merge"}], now=NOW + timedelta(minutes=3))
    git(first.root, "pull", "-q", "--no-rebase", "--no-edit", str(second.root), "main")
    november = NOW + timedelta(days=14)
    store.write(first, [{"op": "log", "item": "A-0003", "note": "first's"}], now=november)
    store.write(second, [{"op": "log", "item": "A-0003", "note": "second's"}], now=november + timedelta(minutes=1))
    git(second.root, "pull", "-q", "--rebase", "origin", "main")
    git(first.root, "pull", "-q", "--ff-only", str(second.root), "main")
    one, two = (fold(ops.read_all(v.root).lines) for v in (first, second))
    assert snapshot(one) == snapshot(two)
    assert one.problems == () and one.clashes() == []
    assert [e.line for e in one.items[twos[1]].log][-2:] == ["first's", "second's"]
    assert one.items[next(i for i in one.items if one.items[i].number == 1)].state == "done"
    assert [p for p in first.check() if p.where.startswith(("ledger", "A-"))] == []


def test_two_clones_that_pull_from_a_clock_ahead_still_mint_different_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A laptop whose clock runs three minutes ahead writes; the desk's two clones pull it and each writes before
    pulling the other. Their ids differ and sort after the laptop's, and the merge holds every op (DR-0612)."""
    desk = empty_vault(tmp_path, make_config())
    store.write(desk, [add("Shared")], now=NOW)
    laptop = clone(desk, tmp_path / "laptop", "laptop", monkeypatch)
    (ahead,) = store.write(laptop, [{"op": "log", "item": "A-0001", "note": "from the laptop"}],
                           now=NOW + timedelta(minutes=3)).ops  # fmt: skip
    live = clone(laptop, tmp_path / "live", "test-box", monkeypatch)
    git(desk.root, "pull", "-q", "--no-rebase", "--no-edit", str(laptop.root), "main")
    one = store.write(desk, [{"op": "log", "item": "A-0001", "note": "desk"}], now=NOW + timedelta(seconds=10)).ops
    two = store.write(live, [{"op": "log", "item": "A-0001", "note": "live"}], now=NOW + timedelta(seconds=10)).ops
    assert one[0].id != two[0].id
    assert min(one[0].id, two[0].id) > ahead.id
    assert one[0].after == two[0].after == ahead.id
    git(desk.root, "pull", "-q", "--no-rebase", "--no-edit", str(live.root), "main")
    merged = fold(ops.read_all(desk.root).lines)
    assert [e.line for e in merged.items[ahead.item].log] == [
        "added",
        "from the laptop",
        *sorted(["desk", "live"], key=lambda note: (one if note == "desk" else two)[0].id),
    ]
    assert merged.problems == ()
