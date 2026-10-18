"""A vault.yaml laid out by hand, edited in place (DR-0602): each change Docket's writers make lands where the value
stands, every other byte kept, comments, order, indentation and line endings; a change with no place, or one that
doesn't read back as asked, writes nothing and leaves the line. Invented settings only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from docket_testkit import empty_vault, git, make_config

from docket.vault import settings, yamledit
from docket.vault.config import CONFIG_FILE, RedactionPattern, RenderedFile, SourceConfig, VaultConfig
from docket.vault.vault import Vault

TEXT = """\
# my vault, laid out my way
storage: repo
owner:
    name: Ada Example   # me
    emails: [ada@example.invalid]
    languages:
        - en
    time_zone: Europe/Budapest
primary_machine: test-box

sources:
    # the calls
    calls:
        sensitivity: mixed
        tools: [list_calls]
        media_folders:
            - ~/Calls
    chat: {}

never_store:
    people:
        - Cleo Test    # a colleague
        - "Dora #2"
    containers: []
    keywords: []
redaction:
    credentials: true
    phone: false
    patterns:
        - name: ticket
          pattern: TCK-\\d+
"""

Change = Callable[[VaultConfig], VaultConfig]


def read(text: str) -> VaultConfig:
    return VaultConfig.parse(text, Path(CONFIG_FILE))


def source(name: str, **update: object) -> Change:
    return lambda c: c.model_copy(update={"sources": {**c.sources, name: c.sources[name].model_copy(update=update)}})


def lists(**update: object) -> Change:
    return lambda c: c.model_copy(update={"never_store": c.never_store.model_copy(update=update)})


def rules(**update: object) -> Change:
    return lambda c: c.model_copy(update={"redaction": c.redaction.model_copy(update=update)})


def ticket(enabled: bool) -> Change:
    return rules(patterns=[RedactionPattern(name="ticket", pattern=r"TCK-\d+", enabled=enabled)])


CHANGES: dict[str, tuple[Change, str, str]] = {
    "a scalar": (rules(phone=True), "    phone: false\n", "    phone: true\n"),
    "a key the file left out": (
        source("calls", enabled=False),
        "            - ~/Calls\n",
        "            - ~/Calls\n        enabled: false\n",
    ),
    "a value added to a block list": (
        lists(people=["Cleo Test", "Dora #2", "Ben: Sample"]),
        '        - "Dora #2"\n',
        "        - \"Dora #2\"\n        - 'Ben: Sample'\n",
    ),
    "a value taken off a block list": (
        lists(people=["Dora #2"]),
        "        - Cleo Test    # a colleague\n",
        "",
    ),
    "every value taken off": (
        lists(people=[]),
        '    people:\n        - Cleo Test    # a colleague\n        - "Dora #2"\n',
        "    people: []\n",
    ),
    "a flow list": (lists(keywords=["Project: Phoenix"]), "    keywords: []\n", "    keywords: ['Project: Phoenix']\n"),
    "a flow mapping": (
        source("chat", sensitivity="private"),
        "    chat: {}\n",
        "    chat: {enabled: true, sensitivity: private, mapper: null, tools: []}\n",
    ),
    "a pattern turned off": (
        ticket(False),
        "          pattern: TCK-\\d+\n",
        "          pattern: TCK-\\d+\n          enabled: false\n",
    ),
    "a list emptied that Docket no longer writes": (
        source("calls", media_folders=[]),
        "        media_folders:\n            - ~/Calls\n",
        "        media_folders: []\n",
    ),
    "a source added": (
        lambda c: c.model_copy(update={"sources": {**c.sources, "diary": SourceConfig(tools=["get_note"])}}),
        "    chat: {}\n",
        "    chat: {}\n    diary:\n        enabled: true\n        sensitivity: work\n        mapper: null\n"
        "        tools:\n        - get_note\n",
    ),
    "a key added at the top": (
        lambda c: c.model_copy(update={"api_provider": "voyage"}),
        "          pattern: TCK-\\d+\n",
        "          pattern: TCK-\\d+\napi_provider: voyage\n",
    ),
}


@pytest.mark.parametrize("what", sorted(CHANGES))
def test_each_change_lands_where_its_value_stands_and_keeps_every_other_byte(what: str) -> None:
    change, old, new = CHANGES[what]
    before = read(TEXT)
    after = change(before)
    out = yamledit.patch(TEXT, before, after)
    assert out is not None and out == TEXT.replace(old, new, 1), what
    assert read(out) == after


def test_the_file_s_own_line_endings_and_a_last_line_without_one_are_kept() -> None:
    crlf = TEXT.replace("\n", "\r\n").removesuffix("\r\n")
    before = read(crlf)
    after = lists(people=["Cleo Test", "Dora #2", "Ben Sample"])(before)
    out = yamledit.patch(crlf, before, after)
    assert out is not None and read(out) == after
    assert "\n" not in out.replace("\r\n", "")
    added = yamledit.patch(crlf, before, before.model_copy(update={"api_provider": "voyage"}))
    assert added is not None and added.endswith("TCK-\\d+\r\napi_provider: voyage\r\n")


def test_a_key_written_with_no_value_takes_one_after_its_colon() -> None:
    text = TEXT.replace("storage: repo\n", "storage: repo\napi_provider:   # not yet\n")
    before = read(text)
    out = yamledit.patch(text, before, before.model_copy(update={"api_provider": "voyage"}))
    assert out is not None and "api_provider: voyage   # not yet\n" in out


def test_a_file_whose_every_section_is_flow_indents_what_it_adds_by_two() -> None:
    flow = make_config()
    text = (
        "storage: repo\nowner: {name: Ada Example, emails: [ada@example.invalid], languages: [en, hu], "
        "time_zone: Europe/Budapest}\nprimary_machine: test-box\n"
        "sources: {chat: {}, calls: {sensitivity: mixed}}\n"
    )
    before = read(text)
    assert before.sources == flow.sources
    after = before.model_copy(update={"rendered": [RenderedFile(kind="people", path="people.md")]})
    out = yamledit.patch(text, before, after)
    assert out is not None and out.endswith("rendered:\n- kind: people\n  path: people.md\n")
    assert read(out) == after


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (
            TEXT.replace("    containers: []\n", "    containers: &none []\n").replace(
                "    keywords: []", "    keywords: *none"
            ),
            "an alias",
        ),
        ("- storage: repo\n", "not a mapping"),
        ("storage: [repo\n", "not YAML"),
        (TEXT.replace("storage: repo\n", "storage: repo\nstorage: repo\n"), "a key twice"),
        (TEXT.replace("storage: repo\n", "<<: {storage: repo}\n"), "a merge key"),
    ],
)
def test_a_file_the_editor_has_no_place_in_gets_no_text(text: str, why: str) -> None:
    before = read(TEXT)
    assert yamledit.patch(text, before, rules(phone=True)(before)) is None, why


def test_a_pattern_added_has_no_place() -> None:
    before = read(TEXT)
    two = [*before.redaction.patterns, RedactionPattern(name="account", pattern=r"ACCT-\d+")]
    assert yamledit.patch(TEXT, before, rules(patterns=two)(before)) is None


# -- through vault.yaml's writer ---------------------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    found = empty_vault(tmp_path, make_config())
    path = found.root / CONFIG_FILE
    path.write_text("# mine\n" + path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    git(found.root, "commit", "-qam", "A comment of mine")
    return found


def test_the_writer_edits_a_file_of_the_user_s_own_in_place(vault: Vault) -> None:
    done = settings.rewrite(vault, rules(phone=True), "Config: redaction phone on", via="cli")
    assert done.changed
    text = (vault.root / CONFIG_FILE).read_text(encoding="utf-8")
    assert text.startswith("# mine\n") and read(text).redaction.phone


@pytest.mark.parametrize(
    "patched", ["storage: [repo\n", "# mine\nstorage: repo\n"], ids=["not YAML", "not the settings asked for"]
)
def test_a_patch_that_doesn_t_read_back_as_asked_writes_nothing(
    vault: Vault, patched: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (vault.root / CONFIG_FILE).read_text(encoding="utf-8")
    monkeypatch.setattr(yamledit, "patch", lambda text, old, new: patched)
    done = settings.rewrite(vault, rules(phone=True), "Config: redaction phone on", via="cli")
    assert done == settings.Rewritten(by_hand=True)
    assert (vault.root / CONFIG_FILE).read_text(encoding="utf-8") == before
