"""vault.yaml's one writer and the door (DR-0506, DR-0507): `loosens` over every field, `rewrite` in Docket's form and
not, a page's change held to the list, and the lines to paste, quoted so each reads back as one value."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from docket_testkit import AtConsole, empty_vault, git, make_config, sh
from pydantic import Field

from docket.cli import main
from docket.errors import DocketError
from docket.vault import settings
from docket.vault.config import CONFIG_FILE, NeverStore, Owner, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.vault.inforce import effective
from docket.vault.neverstore import ListName
from docket.vault.settings import LooseningError, Rewritten, loosens
from docket.vault.vault import Vault

BASE = VaultConfig(
    owner=Owner(name="Ada Example", emails=["ada@example.invalid"], languages=["en"], time_zone="Europe/Budapest"),
    primary_machine="test-box",
    sources={
        "calls": SourceConfig(
            sensitivity="mixed",
            mapper=".claude/skills/sources/calls/mapper.py",
            tools=["list_calls"],
            media_hosts=["media.example"],
        ),
        "memo": SourceConfig(),
        "notes": SourceConfig(sensitivity="private"),
        "dormant": SourceConfig(enabled=False),
        "documents": SourceConfig(folder="docs", exclude=["archive/*"]),
    },
    never_store=NeverStore(people=["Cleo Test"], containers=["chat:garden"], keywords=["Project: Phoenix"]),
    redaction=Redaction(
        phone=False,
        patterns=[
            RedactionPattern(name="ticket", pattern="TCK"),
            RedactionPattern(name="code", pattern="X", enabled=False),
        ],
    ),
)
Change = Callable[[VaultConfig], VaultConfig]


def source(name: str, **update: object) -> Change:
    def change(config: VaultConfig) -> VaultConfig:
        return config.model_copy(
            update={"sources": {**config.sources, name: config.sources[name].model_copy(update=update)}}
        )

    return change


def sources(**named: SourceConfig | None) -> Change:
    def change(config: VaultConfig) -> VaultConfig:
        kept = {k: v for k, v in config.sources.items() if named.get(k, v) is not None}
        added = {k: v for k, v in named.items() if v is not None}
        return config.model_copy(update={"sources": {**kept, **added}})

    return change


def lists(**update: list[str]) -> Change:
    return lambda c: c.model_copy(update={"never_store": c.never_store.model_copy(update=update)})


def rules(**update: object) -> Change:
    return lambda c: c.model_copy(update={"redaction": c.redaction.model_copy(update=update)})


def patterns(*made: RedactionPattern) -> Change:
    return rules(patterns=list(made))


def top(**update: object) -> Change:
    return lambda c: c.model_copy(update=update)


def both(*changes: Change) -> Change:
    def change(config: VaultConfig) -> VaultConfig:
        for one in changes:
            config = one(config)
        return config

    return change


ON, OFF = RedactionPattern(name="ticket", pattern="TCK"), RedactionPattern(name="code", pattern="X", enabled=False)
CASES: list[tuple[str, Change, bool]] = [
    ("nothing changed", lambda c: c, False),
    ("a person added", lists(people=["Cleo Test", "Dora Example"]), False),
    ("a container added", lists(containers=["chat:garden", "chat:family"]), False),
    ("a keyword added", lists(keywords=["Project: Phoenix", "Dora #2"]), False),
    ("a person taken off", lists(people=[]), True),
    ("a container taken off", lists(containers=[]), True),
    ("a keyword changed", lists(keywords=["Project Phoenix"]), True),
    ("phone turned on", rules(phone=True), False),
    ("credentials turned off", rules(credentials=False), True),
    ("a pattern turned on", patterns(ON, OFF.model_copy(update={"enabled": True})), False),
    ("a pattern turned off", patterns(ON.model_copy(update={"enabled": False}), OFF), True),
    ("a pattern added after the others", patterns(ON, OFF, RedactionPattern(name="more", pattern="Y")), False),
    (
        "a pattern added, turned off",
        patterns(ON, OFF, RedactionPattern(name="more", pattern="Y", enabled=False)),
        False,
    ),
    ("a pattern added before the others", patterns(RedactionPattern(name="more", pattern="Y"), ON, OFF), False),
    ("a pattern added under a name it has", patterns(ON, OFF, RedactionPattern(name="code", pattern="Y")), False),
    ("the patterns reordered", patterns(OFF, ON), False),
    ("a pattern removed", patterns(ON), True),
    ("a pattern renamed", patterns(ON.model_copy(update={"name": "tickets"}), OFF), True),
    ("a pattern's expression changed", patterns(ON.model_copy(update={"pattern": "T"}), OFF), True),
    ("a pattern turned on and changed", patterns(ON, OFF.model_copy(update={"enabled": True, "pattern": "."})), True),
    ("work to mixed", source("memo", sensitivity="mixed"), False),
    ("work to private", source("memo", sensitivity="private"), False),
    ("mixed to private", source("calls", sensitivity="private"), False),
    ("mixed to work", source("calls", sensitivity="work"), True),
    ("private to mixed", source("notes", sensitivity="mixed"), True),
    ("private to work", source("notes", sensitivity="work"), True),
    ("a source turned off", source("memo", enabled=False), False),
    ("a folder source turned off", source("documents", enabled=False), False),
    ("a source turned on", source("dormant", enabled=True), True),
    ("a tool added", source("calls", tools=["list_calls", "get_call"]), True),
    ("a tool removed", source("calls", tools=[]), False),
    ("a tool swapped", source("calls", tools=["get_call"]), True),
    ("the mapper changed", source("calls", mapper=".claude/skills/sources/calls/other.py"), True),
    ("a media host added", source("calls", media_hosts=["media.example", "cdn.example"]), True),
    ("a media host removed", source("calls", media_hosts=[]), False),
    ("a media folder added", source("calls", media_folders=["~/Downloads"]), True),
    ("the folder changed", source("documents", folder="~/Work"), True),
    ("an exclude added", source("documents", exclude=["archive/*", "*.tmp"]), False),
    ("an exclude swapped", source("documents", exclude=["*.tmp"]), True),
    ("an exclude removed", source("documents", exclude=[]), True),
    ("a source added", sources(chat=SourceConfig(sensitivity="private")), True),
    ("a folder source added", sources(papers=SourceConfig(folder="papers")), True),
    ("a source removed", sources(notes=None), False),
    ("a source renamed", sources(notes=None, diary=SourceConfig(sensitivity="private")), True),
    ("a person swapped", lists(people=["Dora Example"]), True),
    ("the owner's name changed", top(owner=BASE.owner.model_copy(update={"name": "Ben Sample"})), True),
    ("the time zone changed", top(owner=BASE.owner.model_copy(update={"time_zone": "UTC"})), True),
    ("the storage changed", top(storage="folder"), True),
    ("the primary machine changed", top(primary_machine="other-box"), True),
    # A key added later (DR-0537): any change of it loosens, with no line of its own in the door.
    ("the api provider allowed", top(api_provider="voyage"), True),
    ("two stricter changes", both(source("calls", sensitivity="private"), lists(people=["Cleo Test", "Ben"])), False),
    ("a stricter change and a tool", both(source("calls", sensitivity="private"), source("memo", tools=["x"])), True),
]


@pytest.mark.parametrize(("what", "change", "loosening"), CASES, ids=[c[0] for c in CASES])
def test_only_the_stricter_changes_pass(what: str, change: Change, loosening: bool) -> None:
    assert loosens(BASE, change(BASE)) is loosening, what


def test_in_force_is_head_with_only_what_keeps_more_out() -> None:
    """The settings in force under an edit that mixes both kinds (DR-0521): every part that only keeps more out, and
    HEAD's for the rest, so they are never looser than HEAD."""
    more = RedactionPattern(name="more", pattern="Y")
    file = both(
        lists(people=["Dora Example"]),
        rules(credentials=False, phone=True),
        patterns(more, ON, OFF.model_copy(update={"enabled": True})),
        source("calls", sensitivity="work", tools=["get_call"]),
        source("memo", sensitivity="private"),
        source("documents", exclude=["*.tmp"], folder="~/Work"),
        sources(notes=None, chat=SourceConfig()),
        top(primary_machine="other-box"),
    )(BASE)
    found = effective(BASE, file)
    assert found.never_store.people == ["Dora Example", "Cleo Test"]
    assert (found.redaction.credentials, found.redaction.phone) == (True, True)
    assert found.redaction.patterns == [more, ON, OFF.model_copy(update={"enabled": True})]
    assert (found.sources["calls"].sensitivity, found.sources["calls"].tools) == ("mixed", [])
    assert found.sources["memo"].sensitivity == "private"
    assert (found.sources["documents"].folder, found.sources["documents"].exclude) == ("docs", ["*.tmp", "archive/*"])
    assert sorted(found.sources) == ["calls", "documents", "dormant", "memo"]
    assert found.primary_machine == "test-box"
    assert loosens(BASE, file)
    assert not loosens(BASE, found)
    assert effective(BASE, found) == found


def test_turning_a_rule_on_from_off_passes_and_back_doesn_t() -> None:
    off = rules(credentials=False)(BASE)
    assert not loosens(off, BASE)
    assert loosens(rules(phone=True)(BASE), BASE)


# A later schema, played by subclasses carrying one more field each: the door names none of them, so a change to any
# is a loosening until a decision moves it onto the list (DR-0506).


class LaterRedaction(Redaction):
    email: bool = True


class LaterPattern(RedactionPattern):
    ignore_case: bool = True


class LaterNeverStore(NeverStore):
    domains: list[str] = Field(default_factory=list[str])


class LaterSource(SourceConfig):
    attachments: bool = False


class Later(VaultConfig):
    retention_days: int = 0


TICKET = LaterPattern(name="ticket", pattern="TCK", enabled=False)
LATER = Later(
    owner=BASE.owner,
    primary_machine="test-box",
    sources={"memo": LaterSource(), "calls": LaterSource(sensitivity="mixed")},
    never_store=LaterNeverStore(people=["Cleo Test"], domains=["example.invalid"]),
    redaction=LaterRedaction(patterns=[TICKET]),
)
LATER_CASES: list[tuple[str, Change, bool]] = [
    ("a later top-level key", top(retention_days=30), True),
    ("a later source key", source("memo", attachments=True), True),
    ("a later rule turned off", rules(email=False), True),
    ("a later list emptied", lists(domains=[]), True),
    ("a later pattern key changed", patterns(TICKET.model_copy(update={"ignore_case": False})), True),
    (
        "a later pattern key changed as it is turned on",
        patterns(TICKET.model_copy(update={"ignore_case": False, "enabled": True})),
        True,
    ),
    ("a person added, the later keys kept", lists(people=["Cleo Test", "Dora #2"]), False),
    ("the pattern turned on, its later key kept", patterns(TICKET.model_copy(update={"enabled": True})), False),
    ("a source made private, its later key kept", source("calls", sensitivity="private"), False),
    ("a new pattern at the later key's default", patterns(TICKET, LaterPattern(name="more", pattern="Y")), False),
    (
        "a new pattern setting a later key",
        patterns(TICKET, LaterPattern(name="more", pattern="Y", ignore_case=False)),
        True,
    ),
]


@pytest.mark.parametrize(("what", "change", "loosening"), LATER_CASES, ids=[c[0] for c in LATER_CASES])
def test_a_key_the_door_doesn_t_name_is_a_loosening_at_every_level(what: str, change: Change, loosening: bool) -> None:
    after = change(LATER)
    assert after != LATER, what
    assert loosens(LATER, after) is loosening, what


# -- the writer ------------------------------------------------------------------------------------------------------


def make_vault(tmp_path: Path) -> Vault:
    config = make_config()
    config.sources["memo"] = SourceConfig()
    return empty_vault(tmp_path, config)


def text(vault: Vault) -> str:
    return (vault.root / "vault.yaml").read_text(encoding="utf-8")


def last_message(vault: Vault) -> str:
    return sh(vault.root, "git", "log", "-1", "--format=%B").strip()


def test_a_page_s_stricter_change_is_written_and_committed_saying_so(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    done = settings.rewrite(vault, source("memo", sensitivity="private"), "Config: source memo private", via="page")
    assert done.changed
    assert done.commit == sh(vault.root, "git", "rev-parse", "HEAD").strip()
    assert VaultConfig.load(vault.root / "vault.yaml").sources["memo"].sensitivity == "private"
    assert last_message(vault) == "Config: source memo private\n\nvia page"


def test_a_page_s_loosening_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    before, head = text(vault), sh(vault.root, "git", "rev-parse", "HEAD")
    with pytest.raises(LooseningError, match="a page makes only the changes that keep more out"):
        settings.rewrite(vault, source("calls", sensitivity="work"), "Config: source calls work", via="page")
    assert text(vault) == before
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def test_the_cli_may_loosen(tmp_path: Path, owner: Callable[..., AtConsole]) -> None:
    """At the owner's console, with the code typed back (DR-0517)."""
    owner("code")
    vault = make_vault(tmp_path)
    done = settings.rewrite(vault, source("calls", sensitivity="work"), "Config: source calls work", via="cli")
    assert done.changed
    assert last_message(vault) == "Config: source calls work\n\nconfirmed at the console"


def test_no_change_writes_nothing(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    assert settings.rewrite(vault, lambda c: c, "Config: nothing", via="page") == Rewritten()
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def test_a_hand_edited_file_is_left_as_it_is(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    mine = text(vault).replace("sources:\n", "sources:   # mine\n", 1)
    (vault.root / "vault.yaml").write_text(mine, encoding="utf-8", newline="\n")
    done = settings.rewrite(vault, source("memo", enabled=False), "Config: source memo off", via="page")
    assert done == Rewritten(by_hand=True)
    assert text(vault) == mine


def test_a_change_the_schema_refuses_is_never_written(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    before = text(vault)
    # A copy skips the validators: a folder source with a mapper reads back as a refusal.
    broken = source("memo", folder="docs", mapper=".claude/skills/sources/memo/mapper.py")
    with pytest.raises(DocketError, match=r"the change doesn.t fit vault.yaml: sources.memo"):
        settings.rewrite(vault, broken, "Config: broken", via="cli")
    assert text(vault) == before


# -- the lines -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["Project: Phoenix", "Dora #2", "Kovács Ödön", "- dash", "yes"])
def test_a_line_pastes_as_the_one_value_it_names(value: str) -> None:
    assert yaml.safe_load(settings.item_line(value)) == [value]
    assert yaml.safe_load(settings.key_line("value", value)) == {"value": value}
    assert "\n" not in settings.item_line(value * 200)


def test_a_key_line_is_the_yaml_vault_yaml_holds() -> None:
    assert settings.key_line("sensitivity", "private") == "sensitivity: private"
    assert settings.key_line("enabled", False) == "enabled: false"


def test_a_source_s_block_and_a_pattern_s_read_back_under_their_keys() -> None:
    entry = SourceConfig(folder="~/Munka", exclude=["Project: Phoenix/*"])
    pasted = yaml.safe_load("sources:\n" + settings.source_block("documents", entry))
    assert SourceConfig.model_validate(pasted["sources"]["documents"]) == entry
    block = settings.pattern_block("ticket-id", r"TCK-\d{6}: #")
    assert yaml.safe_load("patterns:\n" + block) == {"patterns": [{"name": "ticket-id", "pattern": r"TCK-\d{6}: #"}]}


def test_a_vault_yaml_that_doesnt_read_names_the_place_never_the_text(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_FILE
    path.write_text("never_store:\n  people:\n  - @dora.secret\n", encoding="utf-8")
    with pytest.raises(DocketError, match=r"at line 3, column 5$") as found:
        VaultConfig.load(path)
    assert "dora" not in str(found.value)
    assert found.value.__cause__ is None
    path.write_text('never_store:\n  people:\n  - "dora\x01secret"\n', encoding="utf-8")
    with pytest.raises(DocketError, match="a character YAML doesn't allow") as found:
        VaultConfig.load(path)
    assert "dora" not in str(found.value)


# -- the page's add, the hash and the take-in -------------------------------------------------------------------------


def shown(vault: Vault) -> str:
    return settings.file_hash((vault.root / CONFIG_FILE).read_bytes())


def subjects(vault: Vault) -> list[str]:
    return sh(vault.root, "git", "log", "--format=%s").splitlines()


def test_a_value_is_added_committed_by_its_list_and_count_and_never_named(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    added = settings.add_listed(vault, "people", "  Dora #2  ", shown=shown(vault))
    assert added.written.changed and not added.already
    assert VaultConfig.load(vault.root / CONFIG_FILE).never_store.people == ["Dora #2"]
    assert last_message(vault) == "Config: never-store people, 1 added\n\nvia page"
    assert "Dora" not in sh(vault.root, "git", "log", "--format=%B")


def test_a_value_the_list_holds_as_the_matcher_reads_it_writes_nothing(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    settings.add_listed(vault, "keywords", "Project: Phoenix", shown=shown(vault))
    settings.add_listed(vault, "people", "Dora Example", shown=shown(vault))
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    # A second post of the same form, with the hash it carried before the first one wrote: no write, no refusal.
    sent: list[tuple[ListName, str]] = [
        ("keywords", "project phoenix"),
        ("people", "DORA EXAMPLE"),
        ("people", "Dora Example"),
    ]
    for name, value in sent:
        again = settings.add_listed(vault, name, value, shown="the hash before")
        assert again.already and again.written == Rewritten(), value
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def test_a_file_changed_since_the_page_showed_it_is_refused(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    before = text(vault)
    with pytest.raises(settings.StaleError, match="changed since the page showed it"):
        settings.add_listed(vault, "people", "Dora Example", shown="0" * 64)
    assert text(vault) == before


def test_a_hand_edited_file_is_never_rewritten_by_an_add(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    mine = text(vault).replace("never_store:\n", "never_store:   # mine\n", 1)
    (vault.root / CONFIG_FILE).write_text(mine, encoding="utf-8", newline="\n")
    added = settings.add_listed(vault, "people", "Dora Example", shown=shown(vault))
    assert added.written == Rewritten(by_hand=True)
    assert text(vault) == mine


@pytest.mark.parametrize(
    ("value", "said"),
    [
        ("   ", "never empty"),
        ("x" * 201, "at most 200 characters"),
        ("Dora\x00Example", "no control characters"),
        ("Dora\nExample", "no control characters"),
        ("Dora\u2028Example", "no control characters"),
    ],
)
def test_a_value_a_list_can_t_hold_is_refused_naming_the_rule_never_the_value(
    tmp_path: Path, value: str, said: str
) -> None:
    vault = make_vault(tmp_path)
    with pytest.raises(DocketError, match=said) as found:
        settings.add_listed(vault, "people", value, shown=shown(vault))
    assert "Dora" not in str(found.value)
    assert "x" * 201 not in str(found.value)


def test_only_the_three_lists_take_a_value(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    with pytest.raises(DocketError, match="people, containers or keywords"):
        settings.add_listed(vault, "domains", "example.invalid", shown=shown(vault))  # pyright: ignore[reportArgumentType]


def test_an_edit_not_committed_yet_is_committed_on_its_own_first(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    path = vault.root / CONFIG_FILE
    path.write_text(text(vault).replace("phone: false", "phone: true", 1), encoding="utf-8", newline="\n")
    added = settings.add_listed(vault, "people", "Dora Example", shown=shown(vault))
    assert added.written.taken_in is not None
    assert subjects(vault)[:2] == ["Config: never-store people, 1 added", settings.TAKEN_IN]
    assert sh(vault.root, "git", "status", "--porcelain").strip() == ""
    # With nothing uncommitted, there is nothing to take in.
    again = settings.add_listed(vault, "people", "Cleo Test", shown=shown(vault))
    assert again.written.taken_in is None


def test_a_change_s_commit_that_fails_puts_vault_yaml_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = make_vault(tmp_path)
    before = text(vault)
    plain = Vault.commit

    def change_fails(self: Vault, paths: list[str], message: str) -> str | None:
        if message == settings.TAKEN_IN:
            return plain(self, paths, message)
        raise OSError("the disk is full")

    monkeypatch.setattr(Vault, "commit", change_fails)
    with pytest.raises(OSError, match="the disk is full"):
        settings.add_listed(vault, "people", "Dora Example", shown=shown(vault))
    assert text(vault) == before


# -- B1: no write takes in an edit that lets more in -----------------------------------------------------------------


def listing_vault(tmp_path: Path) -> Vault:
    config = make_config()
    config.never_store = NeverStore(people=["Cleo Test", "Ben Sample"])
    return empty_vault(tmp_path, config)


def without_cleo(text: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True) if line.strip() != "- Cleo Test")


LOOSENING_EDITS = {
    "credentials turned off": lambda t: t.replace("credentials: true", "credentials: false", 1),
    "a source made looser": lambda t: t.replace("sensitivity: mixed", "sensitivity: work", 1),
    "a never-store line removed": without_cleo,
}


@pytest.mark.parametrize("what", sorted(LOOSENING_EDITS))
def test_an_uncommitted_edit_that_lets_more_in_is_never_taken_in(
    tmp_path: Path, what: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reviewer's probe (B1): a one-value edit by hand, or by an agent's Edit tool, keeps Docket's form; a page's
    add or a CLI write must not commit it, or the next sync carries the loosening to every clone."""
    vault = listing_vault(tmp_path)
    path = vault.root / CONFIG_FILE
    edited = LOOSENING_EDITS[what](text(vault))
    assert edited != text(vault)
    path.write_text(edited, encoding="utf-8", newline="\n")
    assert VaultConfig.load(path).written_by_docket(edited), what
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    with pytest.raises(
        settings.UncommittedLooseningError, match="an edit that isn't in force until you commit it"
    ) as found:
        settings.add_listed(vault, "people", "Dora Example", shown=shown(vault))
    assert "Dora" not in str(found.value)
    assert main(["redaction", "set", "phone", "on", "--vault", str(vault.root)]) == 2
    assert settings.UNCOMMITTED_LOOSENING in capsys.readouterr().err
    assert text(vault) == edited
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


def test_a_committed_vault_yaml_that_is_gone_or_doesn_t_read_takes_nothing_in(tmp_path: Path) -> None:
    vault = listing_vault(tmp_path)
    path = vault.root / CONFIG_FILE
    good = text(vault)
    path.write_text(good + "never_store: [\n", encoding="utf-8", newline="\n")
    git(vault.root, "commit", "-qam", "a vault.yaml that doesn't read")
    path.write_text(good, encoding="utf-8", newline="\n")
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    with pytest.raises(settings.UncommittedLooseningError, match="isn't in the last commit as a file Docket can read"):
        settings.add_listed(vault, "people", "Dora Example", shown=shown(vault))
    git(vault.root, "rm", "-q", "--cached", CONFIG_FILE)
    git(vault.root, "commit", "-qm", "vault.yaml out of the commit")
    head = sh(vault.root, "git", "rev-parse", "HEAD")
    with pytest.raises(settings.UncommittedLooseningError, match="isn't in the last commit"):
        settings.add_listed(vault, "people", "Dora Example", shown=shown(vault))
    assert text(vault) == good
    assert sh(vault.root, "git", "rev-parse", "HEAD") == head


# -- PR 3: a rule on, a source stricter or off ------------------------------------------------------------------------


def rules_vault(tmp_path: Path) -> Vault:
    config = make_config()
    config.sources["memo"] = SourceConfig()
    config.sources["documents"] = SourceConfig(folder="docs")
    config.redaction = Redaction(
        patterns=[
            RedactionPattern(name="ticket", pattern="TCK", enabled=False),
            RedactionPattern(name="twin", pattern="A", enabled=False),
            RedactionPattern(name="twin", pattern="B", enabled=False),
            RedactionPattern(name="phone", pattern="P", enabled=False),
        ]
    )
    return empty_vault(tmp_path, config)


def test_a_rule_is_turned_on_and_never_off(tmp_path: Path) -> None:
    vault = rules_vault(tmp_path)
    done = settings.rule_on(vault, "credentials", shown=shown(vault))
    assert done == Rewritten()
    with pytest.raises(DocketError, match="no redaction rule by that name, or more than one"):
        settings.rule_on(vault, "phone", shown=shown(vault))
    on = settings.rule_on(vault, "ticket", shown=shown(vault))
    assert on.changed
    assert [p.enabled for p in VaultConfig.load(vault.root / CONFIG_FILE).redaction.patterns] == [
        True,
        False,
        False,
        False,
    ]
    assert last_message(vault) == "Config: redaction ticket on\n\nvia page"
    for name in ("twin", "nope", ""):
        with pytest.raises(DocketError, match="no redaction rule by that name, or more than one"):
            settings.rule_on(vault, name, shown=shown(vault))
    with pytest.raises(settings.StaleError):
        settings.turn_off(vault, "memo", shown="0" * 64)


def test_the_built_in_phone_rule_is_turned_on_by_its_key(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    assert settings.rule_on(vault, "phone", shown=shown(vault)).changed
    assert VaultConfig.load(vault.root / CONFIG_FILE).redaction.phone
    assert settings.rule_on(vault, "phone", shown="a page from before") == Rewritten()


def test_a_source_is_made_only_stricter_and_turned_only_off(tmp_path: Path) -> None:
    vault = rules_vault(tmp_path)
    assert settings.stricter(vault, "memo", "mixed", shown=shown(vault)).changed
    assert settings.stricter(vault, "memo", "mixed", shown="a page from before") == Rewritten()
    with pytest.raises(LooseningError, match="memo is mixed; a page makes a source only stricter"):
        settings.stricter(vault, "memo", "work", shown=shown(vault))
    assert settings.stricter(vault, "memo", "private", shown=shown(vault)).changed
    assert settings.turn_off(vault, "memo", shown=shown(vault)).changed
    assert settings.turn_off(vault, "memo", shown="a page from before") == Rewritten()
    memo = VaultConfig.load(vault.root / CONFIG_FILE).sources["memo"]
    assert (memo.sensitivity, memo.enabled) == ("private", False)
    assert subjects(vault)[:3] == [
        "Config: source memo off",
        "Config: source memo private",
        "Config: source memo mixed",
    ]
    with pytest.raises(DocketError, match=r"the change doesn.t fit vault.yaml"):
        settings.stricter(vault, "documents", "mixed", shown=shown(vault))
    with pytest.raises(DocketError, match="no source attic"):
        settings.turn_off(vault, "attic", shown=shown(vault))
    with pytest.raises(DocketError, match="work, mixed or private"):
        settings.stricter(vault, "memo", "secret", shown=shown(vault))


def test_the_take_in_rule_holds_for_a_rule_and_a_source(tmp_path: Path) -> None:
    vault = rules_vault(tmp_path)
    path = vault.root / CONFIG_FILE
    edited = text(vault).replace("credentials: true", "credentials: false", 1)
    path.write_text(edited, encoding="utf-8", newline="\n")
    for write in (
        lambda: settings.rule_on(vault, "ticket", shown=shown(vault)),
        lambda: settings.stricter(vault, "memo", "mixed", shown=shown(vault)),
        lambda: settings.turn_off(vault, "memo", shown=shown(vault)),
    ):
        with pytest.raises(settings.UncommittedLooseningError):
            write()
    assert text(vault) == edited


def test_api_provider_is_written_only_when_set_and_waits_for_its_commit_either_way() -> None:
    config = make_config()
    assert all(entry.mapper is None for entry in config.sources.values())
    written = config.dump()
    # A vault that never opts in keeps its vault.yaml byte for byte, a source with no mapper keeping `mapper: null`.
    assert "api_provider" not in written and "mapper: null" in written
    assert config.written_by_docket(written)
    allowed = config.model_copy(update={"api_provider": "voyage"})
    assert "\napi_provider: voyage\n" in allowed.dump()
    assert VaultConfig.parse(allowed.dump(), Path("vault.yaml")).api_provider == "voyage"
    with pytest.raises(DocketError, match="api_provider"):
        VaultConfig.parse(written + "api_provider: cloud\n", Path("vault.yaml"))
    # The settings in force keep HEAD's value until the edit is committed, whichever way it went.
    assert effective(config, allowed).api_provider is None
    assert effective(allowed, config).api_provider == "voyage"
    assert loosens(config, allowed) and loosens(allowed, config)
