"""Two clones' ops merge into one ledger (DR-0610, DR-0612): any order of lines folds the same, and real clones merged
or rebased by git meet no conflict, a clock behind and a shared machine name included; each clone's index follows
its merge as a rebuild would (DR-0616)."""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from docket_testkit import empty_vault, git, make_config, sh
from ledger_kit import ADD_FIELDS, DAY, NOW, catch_up, caught_up, rebuilt

from docket.index import rebuild
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
    want = snapshot(fold(Line(ops.read_op(t), t, rel, n) for n, (rel, t) in enumerate(texts, 1)))
    rng = random.Random(seed + 100)  # noqa: S311 - a seeded shuffle
    for _ in range(5):
        # A union merge can put the lines in any order and leave one twice.
        shuffled = texts + rng.sample(texts, k=len(texts) // 4)
        rng.shuffle(shuffled)
        got = fold(Line(ops.read_op(t), t, rel, n) for n, (rel, t) in enumerate(shuffled, 1))
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


def test_each_clone_s_index_follows_a_union_merge_as_a_rebuild_would(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two clones with one machine name both give out A-0002 and close A-0001. The second merges the first: its file
    only grew, its own lines then the first's. The first pulls that merge: its file gained lines in the middle, so it
    folds again whole. Each clone's index then holds what a rebuild of its files gives, the clash included."""
    first = empty_vault(tmp_path, make_config())
    store.write(first, [add("Before the clone")], now=NOW)
    second = clone(first, tmp_path / "second", "test-box", monkeypatch)
    for v in (first, second):
        rebuild(v)
    store.write(first, [add("From the first"), {"op": "done", "item": "A-0001"}], now=NOW + timedelta(minutes=1))
    store.write(second, [add("From the second"), {"op": "drop", "item": "A-0001", "note": "dup"}],
                now=NOW + timedelta(minutes=2))  # fmt: skip
    for v in (first, second):
        assert caught_up(v) == rebuilt(v.root)
    git(second.root, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    # The first's two ops, read after the second's own: the file only grew.
    assert catch_up(second) == 2
    merged = caught_up(second)
    assert merged == rebuilt(second.root)
    assert [row[1] for row in merged["items"]].count(None) == 1
    git(first.root, "pull", "-q", "--ff-only", str(second.root), "main")
    # The second's two ops came in before the first's own: every line is read again.
    assert catch_up(first) == 5
    assert caught_up(first) == rebuilt(first.root)


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


def test_clones_behind_a_clock_ahead_write_batches_clash_renumber_apart_and_still_merge_to_one_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two clones with one machine name pull from a laptop whose clock runs two hours ahead, and each writes a batch
    of three; both give out A-0002. Merged one way and rebased the other, then each renumbering the clash on its own
    next write and merging again, they hold one ledger with no problem left."""
    desk = empty_vault(tmp_path, make_config())
    store.write(desk, [add("Shared")], now=NOW)
    laptop = clone(desk, tmp_path / "laptop", "laptop", monkeypatch)
    (ahead,) = store.write(laptop, [{"op": "log", "item": "A-0001", "note": "ahead"}],
                           now=NOW + timedelta(hours=2)).ops  # fmt: skip
    a = clone(laptop, tmp_path / "a", "test-box", monkeypatch)
    b = clone(laptop, tmp_path / "b", "test-box", monkeypatch)
    same = NOW + timedelta(seconds=5)
    wa = store.write(a, [add("A1"), {"op": "log", "item": "A-0001", "note": "a"}, {"op": "done", "item": "A-0001"}],
                     now=same).ops  # fmt: skip
    wb = store.write(b, [add("B1"), {"op": "log", "item": "A-0001", "note": "b"},
                         {"op": "drop", "item": "A-0001", "note": "dup"}], now=same).ops  # fmt: skip
    ids = [o.id for o in (*wa, *wb)]
    assert len(set(ids)) == 6 and min(ids) > ahead.id
    assert [ops.decode(o.id) - ops.decode(wa[0].id) for o in wa] == [0, 1, 2]
    assert wa[0].number == wb[0].number == 2
    git(a.root, "pull", "-q", "--no-rebase", "--no-edit", str(b.root), "main")
    git(b.root, "pull", "-q", "--rebase", str(a.root), "main")
    assert snapshot(fold(ops.read_all(a.root).lines)) == snapshot(fold(ops.read_all(b.root).lines))
    assert len(fold(ops.read_all(a.root).lines).clashes()) == 1
    store.write(a, [], now=same + timedelta(seconds=1))
    store.write(b, [], now=same + timedelta(seconds=1))
    git(a.root, "pull", "-q", "--no-rebase", "--no-edit", str(b.root), "main")
    git(b.root, "pull", "-q", "--rebase", str(a.root), "main")
    one, two = (fold(ops.read_all(v.root).lines) for v in (a, b))
    assert snapshot(one) == snapshot(two)
    assert one.problems == () and one.clashes() == []
    assert sorted(i.number or 0 for i in one.items.values()) == [1, 2, 3]
    # The close that sorts first stands; the other clone's is idle.
    first_close = "done" if wa[2].id < wb[2].id else "dropped"
    assert next(i.state for i in one.items.values() if i.title == "Shared") == first_close
