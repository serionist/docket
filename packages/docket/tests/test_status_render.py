"""Each stream's STATUS.md rendered from the ledger (#208, DR-0673 to DR-0675): the frontmatter in todo.py's layout
with Docket's hash line, the body kept byte for byte, todo.py's files taken over and read back by todo.py unchanged, a
hand edit left and reported, a render racing another, rendered after every write that changes a stream and by `docket
render`, and read by `docket vault check`. Invented streams on a folder vault of the test kit's."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from docket_testkit import best_of, copied, empty_vault, folder_vault, git, host_repo, make_config, sh, traced
from ledger_kit import NOW

from docket.cli import main
from docket.errors import DocketError
from docket.ledger import batch, calendar, status
from docket.ledger.fold import load
from docket.ledger.meetings import Stream
from docket.ledger.status import Fields
from docket.ledger.store import Written, write
from docket.vault import git as git_module
from docket.vault import rendered, staging
from docket.vault.config import CONFIG_FILE, RenderedFile, VaultConfig
from docket.vault.home import Home
from docket.vault.host import Probe
from docket.vault.staging import write_replacing
from docket.vault.vault import Vault

PATH = "projects/pilot/STATUS.md"
BODY = "# Pilot\n\nNotes the owner keeps.\n"
BOM = chr(0xFEFF)
PILOT: dict[str, Any] = {"name": "pilot", "status": "waiting", "where": "PR #12 merged; waiting on: review",
                         "next": "yes", "deadline": "2026-10-30"}  # fmt: skip
# The stream as todo.py's frontmatter holds it, and as the ledger does once PILOT is added on NOW's day.
TODO_FM: dict[str, Any] = {
    "stream": "pilot", "status": "waiting", "where": "PR #12 merged; waiting on: review", "next": "yes",
    "deadline": dt.date(2026, 10, 30), "updated": dt.date(2026, 10, 10),
}  # fmt: skip
HELD = Fields("pilot", "waiting", "PR #12 merged; waiting on: review", "yes", dt.date(2026, 10, 30),
              dt.date(2026, 10, 10))  # fmt: skip
ADDED = Fields(HELD.stream, HELD.status, HELD.where, HELD.next, HELD.deadline, dt.date(2026, 10, 18))
GOLDEN = """\
---
stream: pilot
status: waiting
where: "PR #12 merged; waiting on: review"
next: "yes"
deadline: 2026-10-30
updated: 2026-10-10
# Rendered by Docket from the ledger; change the stream, not these lines. sha256:220e6eae14829ac1
---
"""


# -- todo.py's own writer, reader and check, as code only: the bar is that it reads Docket's file unchanged ----------

TODO_FIELDS = ["stream", "status", "where", "next", "deadline", "updated"]
TODO_UNSAFE = re.compile("[\x7f-\x9f" + "".join(chr(c) for c in (0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF)) + "]")


def todo_quoted(text: str) -> str:
    out = json.dumps(str(text), ensure_ascii=False)
    return TODO_UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", out)


def todo_scalar(value: Any) -> str:
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value)
    try:
        plain_ok = text != "" and "\n" not in text and yaml.safe_load("k: " + text) == {"k": text}
    except yaml.YAMLError:
        plain_ok = False
    return text if plain_ok else todo_quoted(text)


def todo_write(fm: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for f in TODO_FIELDS:
        if fm.get(f) not in (None, ""):
            lines.append(f"{f}: {todo_scalar(fm[f])}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body


def todo_read(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError("no frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("not closed")
    fm = yaml.safe_load(text[3:end]) or {}
    body = text[end + 4 :].lstrip("\n")
    return fm, body


def todo_problems(text: str) -> list[str]:
    fm, _ = todo_read(text)
    return [f"missing '{f}'" for f in ("stream", "status", "where", "next", "updated") if not fm.get(f)]


def after_closing(text: str) -> str:
    """Everything after the closing --- line, its line ending included in the cut."""
    i = text.index("\n") + 1
    while True:
        j = text.index("\n", i)
        if text[i:j].rstrip("\r") == "---":
            return text[j + 1 :]
        i = j + 1


# -- the vault --------------------------------------------------------------------------------------------------------


def top(vault: Vault) -> Path:
    return vault.home.top


def status_file(stream: str = "pilot", path: str = PATH) -> RenderedFile:
    return RenderedFile(kind="status", stream=stream, path=path)


def with_files(vault: Vault, *files: RenderedFile, commit: bool = True) -> Vault:
    """vault.yaml with these files under rendered, committed by hand unless told not to: the owner's yes (DR-0673)."""
    path = vault.root / CONFIG_FILE
    config = VaultConfig.load(path).model_copy(update={"rendered": list(files)})
    path.write_text(config.dump(), encoding="utf-8", newline="\n")
    if commit:
        git(top(vault), "commit", "-qm", "Render the streams", "--", f"{vault.home.prefix}{CONFIG_FILE}")
    return Vault.open(vault.root)


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts as a new process would: no remote's answer or list kept."""
    monkeypatch.setattr(rendered, "_PROBED", {})
    monkeypatch.setattr(rendered, "_UNREACHED", {})
    monkeypatch.setattr(git_module, "_CONFIGURED", {})


def _build_vault(folder: Path) -> Path:
    made = folder_vault(folder)
    (top(made) / "projects" / "pilot").mkdir(parents=True)
    return with_files(made, status_file()).root


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    """A folder vault rendering the pilot's STATUS.md: a copy of one built once per session."""
    return Vault.open(copied(tmp_path, _build_vault, f"{__name__}.vault"))


def the_file(vault: Vault) -> Path:
    return top(vault) / PATH


def add_pilot(vault: Vault, **fields: Any) -> Written:
    return write(vault, [{"op": "stream", "fields": {**PILOT, **fields}}], now=NOW)


def set_pilot(vault: Vault, **fields: Any) -> Written:
    return write(vault, [{"op": "set", "item": "pilot", "fields": fields}], now=NOW + dt.timedelta(days=1))


def stream_of(vault: Vault, name: str = "pilot") -> Stream:
    return next(s for s in load(vault.root).streams.values() if s.name == name)


def left(why: str) -> str:
    return f"left       {PATH}: {why}"


# -- the frontmatter (DR-0674) ----------------------------------------------------------------------------------------


def test_the_frontmatter_is_todo_py_s_field_lines_with_docket_s_hash_line() -> None:
    assert status.frontmatter(HELD) == GOLDEN
    assert status.frontmatter(HELD, "\r\n") == GOLDEN.replace("\n", "\r\n")
    # todo.py wrote the same field lines, and the hash is over them, joined with LF.
    assert todo_write(TODO_FM, "").splitlines()[1:7] == GOLDEN.splitlines()[1:7]
    assert status.digest(GOLDEN.splitlines()[1:7]) == "220e6eae14829ac1"


@pytest.mark.parametrize(
    "value",
    ["plain words", "a: b", "PR #12", "yes", "null", "", "2026-10-30", "'quoted'", '"double"', "- a list?", "[x]",
     "árvíztűrő tükörfúrógép", "\x85 a next line", "a" + chr(0x2028) + "b", chr(0xFEFF) + "a", "{x}", "@at"],
)  # fmt: skip
def test_each_scalar_is_written_as_todo_py_wrote_it(value: str) -> None:
    assert status.scalar(value) == todo_scalar(value)


def test_a_stream_without_where_or_next_leaves_them_out() -> None:
    bare = Fields("pilot", "active", None, None, None, dt.date(2026, 10, 18))
    assert status.field_lines(bare) == ["stream: pilot", "status: active", "updated: 2026-10-18"]
    # todo.py's check calls such a stream's where and next missing, which the ledger allows: noted for the week in step.
    assert todo_problems(status.frontmatter(bare)) == ["missing 'where'", "missing 'next'"]


# -- a write renders --------------------------------------------------------------------------------------------------


def test_a_write_that_adds_a_stream_renders_its_file_and_never_commits_it(vault: Vault) -> None:
    changes = [{"op": "stream", "fields": PILOT}]
    written = write(vault, changes, now=NOW)
    assert written.commit is not None
    assert written.rendered == (f"rendered   {PATH}",)
    text = the_file(vault).read_bytes().decode("utf-8")
    assert text == status.frontmatter(ADDED)
    assert todo_read(text) == ({**TODO_FM, "updated": dt.date(2026, 10, 18)}, "")
    assert git(top(vault), "status", "--porcelain", "--", PATH).split() == ["??", PATH]
    found = batch.result(changes, written, dry_run=False)
    assert found.rendered == [f"rendered   {PATH}"]
    assert batch.lines(found)[-2:] == [f"1 op(s) written, batch {found.batch}, commit {written.commit[:7]}",
                                        f"rendered   {PATH}"]  # fmt: skip


def test_a_set_rewrites_the_frontmatter_and_keeps_the_body_byte_for_byte(vault: Vault) -> None:
    add_pilot(vault)
    path = the_file(vault)
    body = "\n# Pilot\r\n\r\nmixed endings\rand a lone CR, kept: é\n\n---\nstream: not a field\n"
    path.write_bytes(path.read_bytes() + body.encode("utf-8"))
    written = set_pilot(vault, next="Ada signs the order")
    assert written.rendered == (f"rendered   {PATH}",)
    text = path.read_bytes().decode("utf-8")
    assert after_closing(text) == body
    fm, _ = todo_read(text)
    assert (fm["next"], fm["updated"]) == ("Ada signs the order", dt.date(2026, 10, 19))


def test_a_write_that_leaves_the_fields_as_they_are_rewrites_nothing_and_says_nothing(vault: Vault) -> None:
    add_pilot(vault)
    path = the_file(vault)
    before = path.stat().st_mtime_ns, path.read_bytes()
    written = write(vault, [{"op": "log", "item": "pilot", "note": "called Ada"}], now=NOW)
    assert written.commit is not None
    assert written.rendered == ()
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


def test_one_write_renders_each_stream_it_changed_and_no_other(vault: Vault) -> None:
    (top(vault) / "projects" / "harbour").mkdir()
    (top(vault) / "projects" / "depot").mkdir()
    made = with_files(vault, status_file(), status_file("harbour", "projects/harbour/STATUS.md"),
                      status_file("depot", "projects/depot/STATUS.md"))  # fmt: skip
    write(made, [{"op": "stream", "fields": {"name": "depot", "status": "active"}}], now=NOW)
    written = write(made, [{"op": "stream", "fields": PILOT}, {"op": "stream", "fields": {"name": "harbour",
                    "status": "active"}}, {"op": "add", "fields": {"kind": "task", "title": "Book the crane",
                    "who": "me", "stream": "depot", "source": "chat:C0TEST/1"}}], now=NOW)  # fmt: skip
    assert written.rendered == (f"rendered   {PATH}", "rendered   projects/harbour/STATUS.md")


def test_a_dry_run_and_a_file_not_yet_committed_render_nothing(vault: Vault) -> None:
    assert write(vault, [{"op": "stream", "fields": PILOT}], dry_run=True, now=NOW).rendered == ()
    waiting = with_files(vault, status_file(), status_file("harbour", "projects/pilot/HARBOUR.md"), commit=False)
    assert waiting.waiting
    written = write(waiting, [{"op": "stream", "fields": {"name": "harbour", "status": "active"}}], now=NOW)
    assert written.rendered == ()
    assert not the_file(vault).exists()
    assert not (top(vault) / "projects" / "pilot" / "HARBOUR.md").exists()


def test_an_undone_stream_leaves_its_file_and_says_so(vault: Vault) -> None:
    written = add_pilot(vault)
    text = the_file(vault).read_bytes().decode("utf-8")
    undone = write(vault, [{"op": "undo", "of": written.ops[0].id}], now=NOW)
    why = "the ledger holds no stream pilot; add it, or take the file out of vault.yaml's rendered"
    assert undone.rendered == (left(why),)
    assert the_file(vault).read_bytes().decode("utf-8") == text


def test_a_render_that_fails_never_fails_the_write(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    def full(*_args: object, **_kwargs: object) -> list[rendered.Outcome]:
        raise DocketError("the disk is full")

    def odd(*_args: object, **_kwargs: object) -> list[rendered.Outcome]:
        raise RuntimeError("a value never shown")

    monkeypatch.setattr(status, "render", full)
    written = add_pilot(vault)
    assert (written.commit is not None, written.rendered) == (True, ("render failed: the disk is full",))
    monkeypatch.setattr(status, "render", odd)
    written = set_pilot(vault, next="Ada signs the order")
    assert (written.commit is not None, written.rendered) == (True, ("render failed (RuntimeError)",))


def test_a_render_reads_the_ledger_once_another_write_landed_after_its_own(vault: Vault) -> None:
    add_pilot(vault)
    stale = {"pilot": stream_of(vault)}
    marked = status.mark(vault, stale)
    # Nothing landed since: the write's own fold renders, whatever the ledger says.
    assert marked
    the_file(vault).unlink()
    assert status.after_write(vault, stale, marked) == (f"rendered   {PATH}",)
    assert todo_read(the_file(vault).read_bytes().decode("utf-8"))[0]["next"] == "yes"
    # Another write landed: the ledger as it stands renders, never the first write's older state.
    set_pilot(vault, next="Ada signs the order")
    the_file(vault).unlink()
    assert status.after_write(vault, stale, marked) == (f"rendered   {PATH}",)
    assert todo_read(the_file(vault).read_bytes().decode("utf-8"))[0]["next"] == "Ada signs the order"


def test_op_files_that_can_t_be_listed_read_the_ledger(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    add_pilot(vault)
    touched = {"pilot": stream_of(vault)}
    assert status.mark(vault, {"harbour": None}) is None
    assert status.after_write(vault, touched, None) == ()

    def unlisted(_path: object) -> object:
        raise PermissionError(13, "Permission denied")

    with monkeypatch.context() as m:
        m.setattr(os, "scandir", unlisted)
        marked = status.mark(vault, touched)
    assert marked == ()
    the_file(vault).unlink()
    assert status.after_write(vault, touched, marked) == (f"rendered   {PATH}",)
    assert status.read_streams(vault, []) == {}


# -- the file at the path (DR-0675) -----------------------------------------------------------------------------------


def render_held(vault: Vault, fields: Fields = HELD, probe: Callable[[str], Probe] | None = None) -> rendered.Outcome:
    (outcome,) = status.render(vault, [status_file()], {"pilot": fields}.get, **({"probe": probe} if probe else {}))
    return outcome


def test_todo_py_s_file_is_taken_over_its_body_kept_and_todo_py_reads_it_back(vault: Vault) -> None:
    old = todo_write(TODO_FM, BODY)
    the_file(vault).write_bytes(old.encode("utf-8"))
    assert render_held(vault).state == "written"
    new = the_file(vault).read_bytes().decode("utf-8")
    assert new == GOLDEN + "\n" + BODY
    assert after_closing(new) == after_closing(old)
    assert todo_read(new) == (TODO_FM, BODY)
    assert todo_problems(new) == []
    assert render_held(vault).state == "unchanged"


def test_a_crlf_file_keeps_its_line_endings_and_todo_py_reads_it(vault: Vault) -> None:
    old = todo_write(TODO_FM, BODY).replace("\n", "\r\n")
    the_file(vault).write_bytes(old.encode("utf-8"))
    assert render_held(vault).state == "written"
    new = the_file(vault).read_bytes().decode("utf-8")
    assert new == (GOLDEN + "\n" + BODY).replace("\n", "\r\n")
    assert todo_read(new)[0] == TODO_FM
    assert todo_problems(new) == []


def test_a_bom_is_kept_and_todo_py_reads_the_file_as_utf8_sig(vault: Vault) -> None:
    the_file(vault).write_bytes((BOM + todo_write(TODO_FM, BODY)).encode("utf-8"))
    assert render_held(vault).state == "written"
    data = the_file(vault).read_bytes()
    assert data.startswith(b"\xef\xbb\xbf---\n")
    assert data.decode("utf-8") == BOM + GOLDEN + "\n" + BODY
    assert todo_read(data.decode("utf-8-sig")) == (TODO_FM, BODY)


def test_a_file_without_frontmatter_gets_one_above_its_body(vault: Vault) -> None:
    the_file(vault).write_bytes(b"# Pilot\r\n\r\nNotes.\r\n")
    assert render_held(vault).state == "written"
    assert the_file(vault).read_bytes().decode("utf-8") == GOLDEN.replace("\n", "\r\n") + "# Pilot\r\n\r\nNotes.\r\n"


def test_updated_alone_differing_is_taken_over(vault: Vault) -> None:
    the_file(vault).write_text(
        todo_write({**TODO_FM, "updated": dt.date(2026, 9, 1)}, BODY), encoding="utf-8", newline="\n"
    )
    assert render_held(vault).state == "written"
    assert the_file(vault).read_bytes().decode("utf-8") == GOLDEN + "\n" + BODY


def test_a_file_s_text_is_compared_on_one_line_as_the_writer_stores_it(vault: Vault) -> None:
    # The writer stores where on one line; the file's value, read the same way, is the ledger's.
    the_file(vault).write_text(todo_write({**TODO_FM, "where": "PR #12 merged;  waiting on: review "}, BODY),
                               encoding="utf-8", newline="\n")  # fmt: skip
    assert render_held(vault).state == "written"


def test_a_field_that_differs_is_left_and_each_one_reported(vault: Vault) -> None:
    edited = todo_write({**TODO_FM, "where": None, "next": "x" * 60, "deadline": "2026-11-02"}, BODY)
    the_file(vault).write_text(edited, encoding="utf-8", newline="\n")
    outcome = render_held(vault)
    assert (outcome.state, outcome.line) == (
        "edited",
        left(
            'a hand edit holds its frontmatter: where unset in the file, "PR #12 merged; waiting on: review" in the '
            f'ledger; next "{"x" * 37}…" in the file, "yes" in the ledger; deadline "2026-11-02" in the file, '
            "\"2026-10-30\" in the ledger; set the stream to the file's, or put the ledger's back"
        ),
    )
    assert the_file(vault).read_bytes().decode("utf-8") == edited


def test_a_key_added_to_docket_s_frontmatter_fails_its_hash_and_is_left(vault: Vault) -> None:
    add_pilot(vault)
    path = the_file(vault)
    added = path.read_bytes().decode("utf-8").replace(status.COMMENT, "tags: [x]\n" + status.COMMENT)
    path.write_text(added, encoding="utf-8", newline="\n")
    written = set_pilot(vault, next="Ada signs the order")
    assert written.rendered == (left("its frontmatter holds a line besides the stream's fields, line 8; move it below "
                                     "the closing ---"),)  # fmt: skip
    assert path.read_bytes().decode("utf-8") == added


def test_a_value_edited_in_docket_s_frontmatter_is_left_until_the_two_agree(vault: Vault) -> None:
    add_pilot(vault)
    path = the_file(vault)
    path.write_text(
        path.read_bytes().decode("utf-8").replace('next: "yes"', "next: Ben signs"), encoding="utf-8", newline="\n"
    )
    assert set_pilot(vault, status="active").rendered == (
        left('a hand edit holds its frontmatter: status "waiting" in the file, "active" in the ledger; next "Ben '
             'signs" in the file, "yes" in the ledger; set the stream to the file\'s, or put the ledger\'s back'),
    )  # fmt: skip
    # The owner's word: the stream takes the file's values, and the next render takes the file back.
    assert set_pilot(vault, status="waiting", next="Ben signs").rendered == (f"rendered   {PATH}",)
    assert status.holds(status.read_file(path.read_bytes().decode("utf-8")).head or ())


COMMENTED = 'where: "PR #12 merged; waiting on: review"  # Ben asked to keep 11 open'
STRAY_4 = "its frontmatter holds a line besides the stream's fields, line 4; move it below the closing ---"


def test_a_comment_after_a_field_s_value_is_left_never_dropped(vault: Vault) -> None:
    add_pilot(vault)
    path = the_file(vault)
    edited = path.read_bytes().decode("utf-8").replace('where: "PR #12 merged; waiting on: review"', COMMENTED)
    path.write_text(edited, encoding="utf-8", newline="\n")
    assert set_pilot(vault, next="Ada signs the order").rendered == (left(STRAY_4),)
    assert path.read_bytes().decode("utf-8") == edited


def test_a_comment_after_a_field_s_value_is_left_by_a_write_that_moves_only_updated(vault: Vault) -> None:
    add_pilot(vault)
    path = the_file(vault)
    edited = path.read_bytes().decode("utf-8").replace('where: "PR #12 merged; waiting on: review"', COMMENTED)
    path.write_text(edited, encoding="utf-8", newline="\n")
    assert [str(p) for p in vault.check()] == [f"{PATH}: {STRAY_4}"]
    logged = write(vault, [{"op": "log", "item": "pilot", "note": "called Ada"}], now=NOW + dt.timedelta(days=1))
    assert logged.rendered == (left(STRAY_4),)
    assert path.read_bytes().decode("utf-8") == edited


SECRET = "password: hunter2hunter2"  # noqa: S105 - an invented one
REDACTED = "password: [redacted:password]"


def test_a_value_the_writer_redacted_is_taken_over_as_the_ledger_holds_it(vault: Vault) -> None:
    # todo.py wrote the value as given; the writer stored it redacted, so the import's stream holds the marker.
    the_file(vault).write_text(todo_write({**TODO_FM, "next": f"send the {SECRET} to site"}, BODY), encoding="utf-8",
                               newline="\n")  # fmt: skip
    held = Fields(HELD.stream, HELD.status, HELD.where, f"send the {REDACTED} to site", HELD.deadline, HELD.updated)
    assert render_held(vault, held).state == "written"
    text = the_file(vault).read_bytes().decode("utf-8")
    assert text == status.frontmatter(held) + "\n" + BODY
    assert "hunter2" not in text


def test_a_report_shows_a_file_s_value_redacted_and_one_that_isn_t_text_as_not_text(vault: Vault) -> None:
    the_file(vault).write_text(todo_write({**TODO_FM, "where": SECRET}, BODY).replace('next: "yes"', "next: 42"),
                               encoding="utf-8", newline="\n")  # fmt: skip
    outcome = render_held(vault)
    assert (outcome.state, outcome.line) == ("edited", left(
        f'a hand edit holds its frontmatter: where "{REDACTED}" in the file, "PR #12 merged; waiting on: review" in '
        "the ledger; next not text in the file, \"yes\" in the ledger; set the stream to the file's, or put the "
        "ledger's back"))  # fmt: skip
    assert "hunter2" not in outcome.line


@pytest.mark.parametrize(
    ("old", "eol"),
    [
        ("--- " + todo_write(TODO_FM, BODY)[3:], "\n"),
        (todo_write(TODO_FM, BODY).replace("\n---\n", "\n---  \n"), "\n"),
        (todo_write(TODO_FM, BODY).replace("\n---\n", "\n---\t\n"), "\n"),
        (todo_write(TODO_FM, BODY).replace("\n", "\r\n").replace("\r\n---\r\n", "\r\n--- \r\n"), "\r\n"),
        ("".join(line + "  \n" if ": " in line else line + "\n" for line in todo_write(TODO_FM, "").splitlines())
         + BODY, "\n"),
    ],
    ids=["opening line", "closing line", "closing tab", "closing crlf", "field lines"],
)  # fmt: skip
def test_spaces_after_a_frontmatter_s_lines_are_read_as_todo_py_read_them(vault: Vault, old: str, eol: str) -> None:
    the_file(vault).write_bytes(old.encode("utf-8"))
    assert render_held(vault).state == "written"
    assert the_file(vault).read_bytes().decode("utf-8") == (GOLDEN + "\n" + BODY).replace("\n", eol)


NOT_CLOSED = "its frontmatter has no closing --- line; close it, or take the opening one out"


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("---\nstream: pilot\n" + BODY, NOT_CLOSED),
        ("---", NOT_CLOSED),
        ("---\nstream: pilot\nwhere: a: b\n---\n", "its frontmatter isn't YAML Docket reads (mapping values are not "
         "allowed here at line 3, column 9); fix it, or take it out to render it"),
        ("---\nstream: pilot\nstream: pilot\n---\n", "its frontmatter isn't YAML Docket reads ('stream' is given "
         "twice at line 3, column 1); fix it, or take it out to render it"),
        ("---\nstream: pilot\n\nstatus: waiting\n---\n", "its frontmatter holds a line besides the stream's fields, "
         "line 3; move it below the closing ---"),
        (f"---\nstream: pilot\n{status.COMMENT}0123456789abcdef\n{status.COMMENT}0123456789abcdef\n---\n",
         "its frontmatter holds a line besides the stream's fields, line 4; move it below the closing ---"),
        # A comment after a field's value, or after an empty one, would be dropped by the render.
        ("---\nstream: pilot\nwhere: PR 12  # kept open on purpose\n---\n", "its frontmatter holds a line besides "
         "the stream's fields, line 3; move it below the closing ---"),
        ("---\nstream: pilot\nstatus: waiting\nnext: # Ben's call\n---\n", "its frontmatter holds a line besides the "
         "stream's fields, line 4; move it below the closing ---"),
    ],
)  # fmt: skip
def test_a_frontmatter_that_doesn_t_close_or_parse_or_holds_more_is_left(vault: Vault, text: str, why: str) -> None:
    the_file(vault).write_text(text, encoding="utf-8", newline="\n")
    outcome = render_held(vault)
    assert (outcome.state, outcome.line) == ("edited", left(why))
    assert the_file(vault).read_bytes().decode("utf-8") == text


def test_a_stream_the_ledger_doesn_t_hold_is_left(vault: Vault) -> None:
    (outcome,) = status.render(vault, [status_file("harbour", PATH)], {"pilot": HELD}.get)
    why = "the ledger holds no stream harbour; add it, or take the file out of vault.yaml's rendered"
    assert (outcome.state, outcome.why) == ("edited", why)
    assert not the_file(vault).exists()


def test_a_file_that_isn_t_utf8_a_folder_and_a_path_out_of_place_are_refused(vault: Vault) -> None:
    the_file(vault).write_bytes(todo_write(TODO_FM, BODY).encode("utf-16"))
    (top(vault) / "projects" / "folder.md").mkdir()
    files = [
        status_file(),
        status_file("folder", "projects/folder.md"),
        status_file("inside", f"{vault.home.prefix}STATUS.md"),
        status_file("nowhere", "no/such/STATUS.md"),
    ]
    held = {name: Fields(name, "active", None, None, None, dt.date(2026, 10, 18))
            for name in ("pilot", "folder", "inside", "nowhere")}  # fmt: skip
    assert [(o.state, o.why) for o in status.render(vault, files, held.get)] == [
        ("refused", "it isn't UTF-8 text, so its body can't be kept; save it as UTF-8"),
        ("refused", "projects/folder.md is a folder"),
        ("refused", f"it must stay in {top(vault).resolve()} and outside the vault, after links are followed"),
        ("refused", f"its folder {top(vault).resolve() / 'no' / 'such'} doesn't exist"),
    ]


def test_a_file_that_can_t_be_read_is_refused(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(_target: Path) -> bytes | None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(rendered, "file_bytes", locked)
    assert (render_held(vault).state, render_held(vault).why) == ("refused", "it can't be read: Permission denied")


def test_a_public_remote_gets_no_file_even_when_nothing_changes(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def public(url: str) -> Probe:
        asked.append(url)
        return "public"

    assert render_held(vault, probe=lambda _url: "sign-in").state == "written"
    # A new process, and the repo made public meanwhile: Docket's file is there, unchanged, and still refused.
    monkeypatch.setattr(rendered, "_PROBED", {})
    outcome = render_held(vault, probe=public)
    assert outcome.state == "refused"
    assert outcome.why.endswith("can be read without signing in; Docket renders nothing into a public repo")
    assert render_held(vault, probe=public).state == "refused"
    # Asked once in the process for the host's one remote, its answer sure.
    assert len(asked) == 1


def test_a_remote_that_couldn_t_be_checked_keeps_the_file_unwritten_and_is_asked_again_after_five_minutes(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def dropped(url: str) -> Probe:
        asked.append(url)
        return "unreachable"

    outcome = render_held(vault, probe=dropped)
    assert outcome.state == "refused"
    assert outcome.why.startswith("couldn't check the remote ")
    assert outcome.why.endswith("; not written")
    assert not the_file(vault).exists()
    # Within five minutes the answer is kept, so a write's render never waits on the probe again: still not written.
    assert render_held(vault, probe=lambda _url: "sign-in").why == outcome.why
    assert len(asked) == 1
    assert not the_file(vault).exists()
    # Five minutes on, the remote is asked again.
    monkeypatch.setattr(rendered, "_UNREACHED", {url: at - rendered.UNREACHED_FOR for url, at in
                                                 rendered._UNREACHED.items()})  # fmt: skip
    assert render_held(vault, probe=lambda _url: "sign-in").state == "written"
    assert rendered._UNREACHED == {}


def test_a_vault_in_no_repo_is_refused_and_checked_for_nothing(tmp_path: Path) -> None:
    alone = with_files(empty_vault(tmp_path, make_config()), status_file())
    written = write(alone, [{"op": "stream", "fields": PILOT}], now=NOW)
    assert written.rendered == (f"refused    {PATH}: {alone.root} isn't inside a git repo, so there is no repo to "
                                "render into",)  # fmt: skip
    assert alone.check() == []


def test_a_vault_of_its_own_renders_into_the_repo_around_it(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    sh(work, "git", "init", "-q")
    # A .git folder git wouldn't take, between the vault and the repo: git looks past it, and so does the render.
    (work / "notes" / ".git").mkdir(parents=True)
    alone = with_files(empty_vault(work / "notes", make_config()), status_file())
    (work / "projects" / "pilot").mkdir(parents=True)
    assert rendered.consuming_repo(alone) == work.resolve()
    written = write(alone, [{"op": "stream", "fields": PILOT}], now=NOW)
    assert written.rendered == (f"rendered   {PATH}",)
    assert (work / PATH).read_bytes().decode("utf-8") == status.frontmatter(ADDED)


def test_remotes_are_listed_again_when_the_repo_s_config_changes(tmp_path: Path) -> None:
    host = host_repo(tmp_path)
    first = rendered.remotes(host)
    assert len(first) == 1
    assert rendered.remotes(host) == first
    git(host, "remote", "add", "mirror", str(tmp_path / "mirror.git"))
    assert len(rendered.remotes(host)) == 2
    # A worktree's .git is a file: its remotes are asked each time.
    git(host, "worktree", "add", "-q", "--detach", str(tmp_path / "wt"))
    assert len(rendered.remotes(tmp_path / "wt")) == 2


# -- a render racing another, or a hand edit (DR-0675) ----------------------------------------------------------------


def racing(monkeypatch: pytest.MonkeyPatch, meanwhile: Callable[[Path, int], object]) -> list[str]:
    """write_replacing with `meanwhile` run on the file right before each rename is weighed; the texts it was asked
    to write."""
    real = write_replacing
    asked: list[str] = []

    def write_racing(home: Home, path: Path, text: str, tmp_name: str, still: Callable[[], bool]) -> bool:
        asked.append(text)
        meanwhile(path, len(asked))
        return real(home, path, text, tmp_name, still)

    monkeypatch.setattr(rendered, "write_replacing", write_racing)
    return asked


def test_a_hand_edit_made_while_rendering_is_left(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    add_pilot(vault)
    racing(monkeypatch, lambda path, _n: path.write_text("my notes\n", encoding="utf-8", newline="\n"))
    written = set_pilot(vault, next="Ada signs the order")
    assert written.rendered == (left("it changed while Docket rendered it; docket render looks again"),)
    assert the_file(vault).read_bytes().decode("utf-8") == "my notes\n"


def test_a_file_deleted_while_rendering_is_left(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    add_pilot(vault)
    racing(monkeypatch, lambda path, _n: path.unlink())
    assert set_pilot(vault, next="Ada signs the order").rendered == (
        left("it changed while Docket rendered it; docket render looks again"),
    )
    assert not the_file(vault).exists()


def test_a_file_that_can_t_be_read_again_before_the_rename_is_left(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_pilot(vault)
    real = rendered.file_bytes
    calls: list[Path] = []

    def second_read_fails(target: Path) -> bytes | None:
        calls.append(target)
        if len(calls) > 1:
            raise PermissionError(13, "Permission denied")
        return real(target)

    monkeypatch.setattr(rendered, "file_bytes", second_read_fails)
    assert set_pilot(vault, next="Ada signs the order").rendered == (
        left("it changed while Docket rendered it; docket render looks again"),
    )


def test_another_render_first_has_the_stream_read_again_and_rendered_once_more(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_pilot(vault)
    set_pilot(vault, next="Ada signs the order")
    now = status.read_streams(vault, ["pilot"])["pilot"]
    other = Fields("pilot", "active", "another render's", None, None, dt.date(2026, 10, 18))

    def other_render_first(path: Path, n: int) -> None:
        if n == 1:
            path.write_text(status.frontmatter(other), encoding="utf-8", newline="\n")

    asked = racing(monkeypatch, other_render_first)
    # This render holds an older state, HELD: the other render's lands first, so the ledger's is read and rendered.
    assert render_held(vault).state == "written"
    assert asked == [status.frontmatter(HELD), status.frontmatter(now)]
    assert the_file(vault).read_bytes().decode("utf-8") == status.frontmatter(now)


def test_a_second_render_racing_the_one_more_is_left(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    add_pilot(vault)

    def other_renders(path: Path, n: int) -> None:
        other = Fields("pilot", "active", f"render {n}", None, None, dt.date(2026, 10, 18))
        path.write_text(status.frontmatter(other), encoding="utf-8", newline="\n")

    asked = racing(monkeypatch, other_renders)
    outcome = render_held(vault)
    assert (outcome.state, outcome.why) == ("edited", "it changed while Docket rendered it; docket render looks again")
    assert len(asked) == 2


HARBOUR = "projects/harbour/STATUS.md"


def two_streams(vault: Vault) -> Vault:
    (top(vault) / "projects" / "harbour").mkdir()
    made = with_files(vault, status_file(), status_file("harbour", HARBOUR))
    write(made, [{"op": "stream", "fields": {"name": name, "status": "active"}} for name in ("pilot", "harbour")],
          now=NOW)  # fmt: skip
    return made


def a_later_write_while_probing(monkeypatch: pytest.MonkeyPatch, vault: Vault) -> list[Written]:
    """The process's first remote probe lets another write to harbour land and render its file, as another process's
    later write would while this render waits on the network."""
    real = rendered._probed
    landed: list[Written] = []
    fired: list[str] = []

    def probing(url: str, _probe: Callable[[str], Probe]) -> Probe:
        if not fired:
            fired.append(url)
            change = {"op": "set", "item": "harbour", "fields": {"next": "the later write"}}
            landed.append(write(vault, [change], now=NOW + dt.timedelta(days=2)))
        return real(url, lambda _url: "sign-in")

    monkeypatch.setattr(rendered, "_PROBED", {})
    monkeypatch.setattr(rendered, "_probed", probing)
    return landed


def harbour_next(vault: Vault) -> object:
    return todo_read((top(vault) / HARBOUR).read_bytes().decode("utf-8"))[0].get("next")


def test_a_write_s_render_decides_each_file_as_it_comes_to_it(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    made = two_streams(vault)
    landed = a_later_write_while_probing(monkeypatch, made)
    changes = [{"op": "set", "item": name, "fields": {"next": "this write"}} for name in ("pilot", "harbour")]
    written = write(made, changes, now=NOW + dt.timedelta(days=1))
    assert [w.rendered for w in landed] == [(f"rendered   {HARBOUR}",)]
    # Pilot from the write's own fold; harbour from the ledger, the later write in it, which its file already shows.
    assert written.rendered == (f"rendered   {PATH}",)
    assert harbour_next(made) == stream_of(made, "harbour").next == "the later write"


def test_docket_render_reads_a_stream_again_when_a_write_lands_while_it_renders(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = two_streams(vault)
    the_file(made).unlink()
    landed = a_later_write_while_probing(monkeypatch, made)
    assert [o.line for o in status.render_all(made)] == [f"rendered   {PATH}", f"unchanged  {HARBOUR}"]
    assert len(landed) == 1
    assert harbour_next(made) == "the later write"


IN_USE = "The file is in use by another program"


def held_open(
    monkeypatch: pytest.MonkeyPatch, path: Path, times: int | None, meanwhile: Callable[[], object] = lambda: None
) -> list[Path]:
    """A rename over `path` refused, as Windows refuses one over a file another program holds open: `times` times, or
    every time; `meanwhile` runs at the first refusal. Sharing modes can't be made the same way on every system."""
    real = Path.replace
    refused: list[Path] = []

    def replace(self: Path, target: str | os.PathLike[str]) -> Path:
        if Path(target).resolve() == path.resolve() and (times is None or len(refused) < times):
            refused.append(self)
            if len(refused) == 1:
                meanwhile()
            raise PermissionError(13, IN_USE)
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(staging, "RETRY_SECONDS", 0.2)
    return refused


def test_a_file_held_open_past_the_retries_is_refused_and_the_others_still_render(
    vault: Vault, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    made = two_streams(vault)
    held_open(monkeypatch, the_file(made), None)
    changes = [{"op": "set", "item": name, "fields": {"next": "this write"}} for name in ("pilot", "harbour")]
    written = write(made, changes, now=NOW + dt.timedelta(days=1))
    assert written.commit is not None
    assert written.rendered == (f"refused    {PATH}: it couldn't be written: {IN_USE}", f"rendered   {HARBOUR}")
    # docket render says the same and exits 1, never with a traceback.
    assert main(["render", "--vault", str(made.root)]) == 1
    assert capsys.readouterr().out.splitlines() == [f"refused    {PATH}: it couldn't be written: {IN_USE}",
                                                    f"unchanged  {HARBOUR}"]  # fmt: skip


def test_a_save_made_while_the_file_is_held_open_is_kept(vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
    add_pilot(vault)
    path = the_file(vault)
    saved = path.read_bytes().decode("utf-8") + "\nNotes the owner just saved.\n"
    # The owner's editor saves while it holds the file, after the check before the rename passed.
    refused = held_open(monkeypatch, path, 2, lambda: path.write_text(saved, encoding="utf-8", newline="\n"))
    assert set_pilot(vault, next="Ada signs the order").rendered == (f"rendered   {PATH}",)
    text = path.read_bytes().decode("utf-8")
    # Seen before the next try at the rename, so the save stands, its frontmatter rendered again from the ledger.
    assert after_closing(text) == "\nNotes the owner just saved.\n"
    assert todo_read(text)[0]["next"] == "Ada signs the order"
    assert len(refused) == 2


# -- docket render and docket vault check -----------------------------------------------------------------------------


def test_docket_render_renders_every_kind_in_vault_yaml_s_order(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    assert status.render_all(with_files(vault, RenderedFile(kind="people", path="people.md"))) == []
    made = with_files(vault, status_file(), RenderedFile(kind="people", path="people.md"))
    add_pilot(made)
    the_file(made).unlink()
    assert main(["render", "--vault", str(made.root)]) == 0
    assert capsys.readouterr().out.splitlines() == [f"rendered   {PATH}", "rendered   people.md"]
    assert the_file(made).read_bytes().decode("utf-8") == status.frontmatter(ADDED)
    the_file(made).write_text(todo_write({**TODO_FM, "next": "Ben signs"}, BODY), encoding="utf-8", newline="\n")
    assert main(["render", "--vault", str(made.root)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f'left       {PATH}: a hand edit holds its frontmatter: next "Ben signs" in the file')
    assert out[1] == "unchanged  people.md"


def test_vault_check_names_each_status_file_against_the_ledger_and_writes_nothing(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    names = ("pilot", "fleet", "depot", "quay", "harbour")
    for name in names[1:]:
        (top(vault) / "projects" / name).mkdir()
    made = with_files(vault, *(status_file(name, f"projects/{name}/STATUS.md") for name in names))
    write(made, [{"op": "stream", "fields": {**PILOT, "name": name}} for name in names[:4]], now=NOW)
    files = {name: top(made) / "projects" / name / "STATUS.md" for name in names}
    files["pilot"].write_text(todo_write({**TODO_FM, "where": "Waiting on Ben"}, BODY), encoding="utf-8", newline="\n")
    files["fleet"].write_text(status.frontmatter(Fields("fleet", "active", None, None, None, dt.date(2026, 10, 1))),
                              encoding="utf-8", newline="\n")  # fmt: skip
    files["depot"].unlink()
    before = {name: (p.stat().st_mtime_ns, p.read_bytes()) for name, p in files.items() if p.exists()}
    # A calendar step that stopped left a record for the next: the check says so beside the problems.
    calendar.note_pending(made, ["calendar:E1"])
    assert main(["vault", "check", "--vault", str(made.root)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        'projects/pilot/STATUS.md: a hand edit holds its frontmatter: where "Waiting on Ben" in the file, "PR #12 '
        "merged; waiting on: review\" in the ledger; set the stream to the file's, or put the ledger's back",
        "projects/fleet/STATUS.md: stale; docket render",
        "projects/depot/STATUS.md: not rendered yet; docket render",
        "projects/harbour/STATUS.md: the ledger holds no stream harbour; add it, or take the file out of vault.yaml",
        "1 calendar record(s) wait for their meetings; `docket ledger calendar`",
        "4 problem(s)",
    ]
    assert {name: (p.stat().st_mtime_ns, p.read_bytes()) for name, p in files.items() if p.exists()} == before


def test_vault_check_leaves_what_the_render_refuses_to_the_render(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    (top(vault) / "projects" / "utf16").mkdir()
    made = with_files(vault, status_file(), status_file("inside", f"{vault.home.prefix}STATUS.md"),
                      status_file("utf16", "projects/utf16/STATUS.md"),
                      status_file("nowhere", "no/such/STATUS.md"))  # fmt: skip
    write(made, [{"op": "stream", "fields": {**PILOT, "name": name}} for name in ("pilot", "inside", "utf16",
               "nowhere")], now=NOW)  # fmt: skip
    (top(made) / "projects" / "utf16" / "STATUS.md").write_bytes("---\n".encode("utf-16"))
    assert made.check() == []

    def locked(_target: Path) -> bytes | None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(rendered, "file_bytes", locked)
    assert made.check() == []


# -- vault.yaml's status kind (DR-0673) -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        ({"kind": "status", "path": PATH}, "a status file needs stream: the name of the stream it renders"),
        ({"kind": "people", "stream": "pilot", "path": "people.md"}, "stream goes with kind status"),
        ({"kind": "status", "stream": "Pilot run", "path": PATH}, "isn't a stream's name"),
        ({"kind": "status", "stream": "pilot", "path": "../STATUS.md"}, "a rendered file stays inside the repo"),
    ],
)
def test_a_status_entry_needs_its_stream_and_only_it_takes_one(entry: dict[str, str], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        RenderedFile.model_validate(entry)


def test_a_stream_gets_one_status_file_and_the_kind_round_trips(tmp_path: Path) -> None:
    config = folder_vault(tmp_path).config
    twice = [status_file().model_dump(), status_file(path="projects/OTHER.md").model_dump()]
    with pytest.raises(ValueError, match="stream pilot has two status files; give each stream one"):
        VaultConfig.model_validate({**config.model_dump(mode="json"), "rendered": twice})
    both = config.model_copy(update={"rendered": [RenderedFile(kind="people", path="people.md"), status_file()]})
    text = both.dump()
    assert text.endswith(
        "rendered:\n- kind: people\n  path: people.md\n"
        "- kind: status\n  stream: pilot\n  path: projects/pilot/STATUS.md\n"
    )
    assert VaultConfig.parse(text, tmp_path / CONFIG_FILE) == both


# -- the budget (DR-0635) ---------------------------------------------------------------------------------------------


@pytest.mark.perf
def test_a_write_s_render_takes_under_a_tenth_of_a_second_with_twenty_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write changing one of twenty streams, each with its STATUS.md, after one write that rendered them all, as a
    running service has: the remotes listed and probed already. Only the render after the write is timed."""
    made = folder_vault(tmp_path)
    names = [f"stream-{n:02d}" for n in range(20)]
    for name in names:
        (top(made) / "projects" / name).mkdir(parents=True)
    made = with_files(made, *(status_file(name, f"projects/{name}/STATUS.md") for name in names))
    first = write(made, [{"op": "stream", "fields": {**PILOT, "name": name}} for name in names], now=NOW)
    assert len(first.rendered) == 20
    timed: list[float] = []
    real = status.after_write

    def timing(*args: Any) -> tuple[str, ...]:
        began = time.perf_counter()
        said = real(*args)
        timed.append(time.perf_counter() - began)
        return said

    monkeypatch.setattr(status, "after_write", timing)
    # Each run a write of its own, each changing the stream, so each renders its file; the render's fastest is asserted.
    nexts = iter(("Ben confirms the date", "Ben confirms the room", "Ben confirms the hour"))
    run = best_of(
        3, lambda: write(made, [{"op": "set", "item": "stream-07", "fields": {"next": next(nexts)}}], now=NOW)
    )
    written, rendered = run.result, min(timed)
    assert written.rendered == ("rendered   projects/stream-07/STATUS.md",)
    print(
        f"a write's render: {rendered * 1000:.0f} ms ({', '.join(f'{t * 1000:.1f}' for t in timed)} ms) of the "
        f"write's {run.seconds * 1000:.0f} ms ({run.runs('ms')}), 20 streams"
    )
    if not traced():
        assert rendered < 0.1, f"the render after a write took {rendered:.2f} s, over 0.1 s"
