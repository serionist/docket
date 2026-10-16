"""The Settings page's parts on invented settings (DR-0505, DR-0506, DR-0510 to DR-0512): the URL, each source's
changes stricter first with their lines, the never-store values only when asked, the rules' commands, the models."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from docket_testkit import make_config

from docket.index.build import VectorCoverage
from docket.index.query import Reach
from docket.models.catalogue import E5_SMALL
from docket.models.config import Choice
from docket.models.provider import Off
from docket.models.report import Environment, Report
from docket.vault import redaction
from docket.vault.config import NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.web.settings_view import (
    Facts,
    SettingsState,
    SettingsStateError,
    SettingsView,
    SourceFacts,
    build,
    build_confirm,
    build_source_confirm,
    done_line,
)

OFF = Report(Off(), "off (docket models install turns them on)")
ROOT = Path("/work/vault")


def facts(config: VaultConfig, *sources: SourceFacts, **more: object) -> Facts:
    base: dict[str, object] = {
        "root": ROOT,
        "machine": "test-box",
        "config": config,
        "docket_form": True,
        "sources": list(sources) or [SourceFacts(n, e) for n, e in sorted(config.sources.items())],
        "models": OFF,
        "config_path": Path("/home/ada/.config/docket/config.yaml"),
    }
    return Facts(**(base | more))  # pyright: ignore[reportArgumentType]


def view(
    config: VaultConfig, *sources: SourceFacts, state: SettingsState | None = None, **more: object
) -> SettingsView:
    return build(facts(config, *sources, **more), state or SettingsState())


def test_the_url_holds_show_and_nothing_else() -> None:
    assert SettingsState.read({}).url() == "/settings"
    shown = SettingsState.read({"show": "lists", "other": "kept out"})
    assert shown.url() == "/settings?show=lists"
    assert shown.values
    assert not SettingsState.read({"show": " "}).values
    with pytest.raises(SettingsStateError, match="show is lists"):
        SettingsState.read({"show": "all"})


def test_each_source_s_changes_come_stricter_first_each_with_its_line() -> None:
    config = make_config()
    config.sources = {
        "memo": SourceConfig(),
        "notes": SourceConfig(sensitivity="private"),
        "dormant": SourceConfig(enabled=False, media_hosts=["media.example"]),
        "documents": SourceConfig(folder="docs"),
    }
    sources = {s.name: s for s in view(config, *[SourceFacts(n, e) for n, e in config.sources.items()]).sources}
    memo = sources["memo"].changes
    assert [(c.what, c.line, c.looser) for c in memo] == [
        ("Make it private", "sensitivity: private", False),
        ("Make it mixed", "sensitivity: mixed", False),
        ("Turn it off", "enabled: false", False),
    ]
    assert {c.where for c in memo} == {"sources.memo"}
    notes = sources["notes"].changes
    assert [(c.what, c.looser) for c in notes] == [
        ("Turn it off", False),
        ("Make it mixed", True),
        ("Make it work", True),
    ]
    assert notes[2].effect == "lets it in: every record is kept without a look"
    dormant = sources["dormant"]
    assert [(c.what, c.line, c.looser) for c in dormant.changes][-1] == ("Turn it on", "enabled: true", True)
    assert ("Media hosts", "media.example") in dormant.facts
    assert ("Tools", "none: nothing syncs live") in dormant.facts
    documents = sources["documents"]
    assert [c.what for c in documents.changes] == ["Turn it off"]
    assert documents.facts == [("Folder", "docs"), ("Exclude", "nothing")]
    assert yaml.safe_load("sources:\n" + documents.entry) == {
        "sources": {
            "documents": {
                "enabled": True,
                "sensitivity": "work",
                "mapper": None,
                "tools": [],
                "media_hosts": [],
                "folder": "docs",
            }
        }
    }


def test_a_folder_source_shows_this_machine_s_folder_and_the_mapper_s_problem() -> None:
    config = make_config()
    here = SourceFacts("documents", SourceConfig(folder="docs", exclude=["archive/*"]), here="E:/Work")
    lost = SourceFacts("calls", SourceConfig(sensitivity="mixed"), mapper_problem="source 'calls' has no mapper")
    found = view(config, here, lost)
    assert found.sources[0].facts == [("Folder", "docs"), ("Here", "E:/Work"), ("Exclude", "archive/*")]
    assert found.sources[1].problem == "source 'calls' has no mapper"


def test_the_never_store_values_are_in_the_view_only_when_asked() -> None:
    config = make_config()
    config.never_store = NeverStore(people=["Dora #2"], keywords=["Project: Phoenix", "Cleo"])
    hidden = view(config)
    assert [(n.name, n.count, n.values) for n in hidden.lists] == [
        ("people", 1, []),
        ("containers", 0, []),
        ("keywords", 2, []),
    ]
    assert "Dora #2" not in repr(hidden)
    assert yaml.safe_load(hidden.lists[0].example) == ["Dora Example"]
    shown = view(config, state=SettingsState(show="lists"))
    assert [(v.value, yaml.safe_load(v.line)) for v in shown.lists[0].values] == [("Dora #2", ["Dora #2"])]
    assert shown.shown


def test_each_rule_names_the_command_that_turns_it_the_other_way() -> None:
    config = make_config()
    config.redaction = Redaction(
        credentials=False, phone=True, patterns=[RedactionPattern(name="ticket", pattern="TCK")]
    )
    found = view(config)
    rules = {r.name: r for r in found.rules}
    assert rules["credentials"].command == f'docket redaction set credentials on --vault "{ROOT}"'
    assert (rules["credentials"].note, rules["credentials"].looser) == (redaction.TURNED_ON, False)
    assert rules["phone"].command.startswith("docket redaction set phone off --vault ")
    assert rules["phone"].looser
    assert rules["ticket"].command.startswith('docket redaction set "ticket" off --vault ')
    assert rules["ticket"].what == "TCK"
    assert found.credentials_off == redaction.CREDENTIALS_OFF
    assert found.names.startswith("Names are never redacted")
    assert all(r.line is None for r in found.rules)
    by_hand = view(config, docket_form=False)
    assert {r.name: r.line for r in by_hand.rules}["phone"] == "phone: false, under redaction:"
    assert "never rewritten" in by_hand.form


def test_a_value_a_shell_would_act_on_stays_its_placeholder() -> None:
    config = make_config()
    config.redaction = Redaction(patterns=[RedactionPattern(name="cost$", pattern="x")])
    found = view(config, root=Path("/work/$HOME/vault"))
    rules = {r.name: r for r in found.rules}
    assert rules["cost$"].command == 'docket redaction set "<pattern>" off --vault "<vault>"'
    assert ("pattern", "cost$") in found.unfilled
    assert ("vault", str(Path("/work/$HOME/vault"))) in found.unfilled


SHELL_NAMES = ['a"; rm -rf ~; "', "$(id)", "`id`", "x\u201c; calc; \u201d", "--vault", "a\\", "%PATH%", "!!", "a'b"]


def test_no_pattern_name_a_shell_would_act_on_reaches_a_command() -> None:
    """The reviewer's probe: each name a shell reads more into stays its placeholder in the command, listed beside."""
    config = make_config()
    config.redaction = Redaction(patterns=[RedactionPattern(name=n, pattern="x") for n in SHELL_NAMES])
    found = view(config)
    commands = [r.command for r in found.rules if r.pattern]
    for command in commands:
        for bad in ("rm -rf", "$(id)", "`id`", "calc", "%PATH%", "!!", "a'b", "a\\"):
            assert bad not in command, command
    assert {value for name, value in found.unfilled if name == "pattern"} == set(SHELL_NAMES) - {"--vault"}
    # A name that only looks like a flag is safe inside its quotes.
    assert 'docket redaction set "--vault" off' in " ".join(commands)


def test_the_lead_counts_in_words() -> None:
    config = make_config()
    config.sources = {"memo": SourceConfig()}
    config.never_store = NeverStore(people=["Cleo Test"])
    assert view(config).lead == (
        "1 source, 1 on; 1 never-store value; credentials redacted, phone numbers not; models off on test-box."
    )


def test_the_models_part_shows_the_report_the_vectors_and_the_service() -> None:
    config = make_config()
    off = view(config, service=["off"]).models
    assert not off.on
    assert off.rows == [("Models", "off (docket models install turns them on)"), ("The service", "off")]
    assert off.off_line == "provider: none"
    assert "docket models install --no-reranker" in [c.text for c in off.commands]
    choice = Choice("cpu", E5_SMALL)
    on = Report(
        choice,
        "e5-small on cpu",
        [Environment("cpu", "ready, batches of 8,192 characters", Path("/models/env-cpu"))],
        files="all here",
        search="uses the vectors",
        reranker="none (docket models install adds one)",
    )
    found = view(config, models=on, service_errors=["OSError: gone"]).models
    assert found.on
    assert ("Environment, cpu", f"ready, batches of 8,192 characters ({Path('/models/env-cpu')})") in found.rows
    assert ("Vectors", "none yet: the service embeds after its next index update") in found.rows
    assert found.errors == ["OSError: gone"]
    counted = view(config, models=on, vectors=[VectorCoverage(model="e5-small", have=4213, total=4213)]).models
    assert ("Vectors", "e5-small: 4,213 of 4,213 chunks") in counted.rows


def test_done_holds_a_list_and_a_count_and_never_reaches_a_link() -> None:
    state = SettingsState.read({"done": "listed-keywords-1", "show": "lists"})
    assert state.url() == "/settings?show=lists"
    assert state.url(done=True) == "/settings?show=lists&done=listed-keywords-1"
    for bad in ("listed-keywords-2", "listed-Dora-1", "phone-maybe", "Calls-private", "calls-work", "-off"):
        with pytest.raises(SettingsStateError, match="done says what the last change did"):
            SettingsState.read({"done": bad})
    config = make_config()
    assert done_line("listed-people-1", config) == (
        "Added one value to people, and committed vault.yaml: what waits in staging that it names is dropped, and "
        "docket sync takes it to your other machines."
    )
    folder = make_config("folder")
    assert "your own push takes it" in (done_line("listed-people-1", folder) or "")
    assert done_line("listed-containers-0", config) == "The containers list held that already, so nothing changed."
    assert done_line(None, config) is None
    assert view(config, state=state).done == done_line("listed-keywords-1", config)


def test_the_confirm_view_names_what_a_value_drops_and_what_holds_it() -> None:
    counted = build_confirm(
        "keywords", "Project: Phoenix", staged=2, stored=Reach(3, 40, 400), why=None, shown="h", docket_form=True
    )
    assert counted.lines == [
        "It drops 2 records waiting in staging now.",
        "Already stored, at least 3 records and 40 of 400 chunks (10.0%) hold it. They stay in raw: forgetting them "
        "comes later, with docket forget.",
        "Docs that say it stay as they are; a later doc that says it is refused.",
        "From the next sync, a record it names is dropped before it is stored, with no trace.",
    ]
    assert counted.broad is not None and counted.broad.startswith("That is 10% of everything indexed")
    assert yaml.safe_load(counted.line) == ["Project: Phoenix"]
    narrow = build_confirm(
        "people", "Dora Example", staged=0, stored=Reach(1, 1, 400), why=None, shown="h", docket_form=True
    )
    assert narrow.broad is None
    assert narrow.lines[0] == "Nothing waiting in staging names it now."
    unknown = build_confirm(
        "containers", "chat:family", staged=1, stored=None, why="there is no index yet", shown="h", docket_form=False
    )
    assert unknown.lines[:2] == [
        "It drops 1 record waiting in staging now.",
        "What is already stored can't be counted: there is no index yet.",
    ]
    empty = build_confirm("people", "Cleo Test", staged=0, stored=Reach(0, 0, 0), why=None, shown="h", docket_form=True)
    assert empty.lines[1] == "Nothing already stored holds it, of 0 chunks."


def test_a_rule_s_and_a_source_s_done_say_how_things_stand() -> None:
    config = make_config()
    phone = done_line("phone-on", config) or ""
    assert phone.startswith("Phone numbers are redacted from now on. Run docket doc redact")
    assert (done_line("pattern-on", config) or "").startswith("Your pattern's matches are redacted from now on.")
    assert done_line("memo-private", config) == (
        "memo is private: nothing from it is stored, and what waited from it in staging is gone."
    )
    assert (
        done_line("calls.v2-off", config) == "calls.v2 is off: its syncs and scans stop, and what waits from it stays."
    )
    assert (done_line("memo-mixed", config) or "").startswith("memo is mixed: each record from it waits")


def test_a_rule_that_is_off_and_its_own_can_be_turned_on_from_the_page() -> None:
    config = make_config()
    config.redaction = Redaction(
        credentials=False,
        patterns=[
            RedactionPattern(name="ticket", pattern="T", enabled=False),
            RedactionPattern(name="twin", pattern="A", enabled=False),
            RedactionPattern(name="twin", pattern="B", enabled=False),
            RedactionPattern(name="phone", pattern="P", enabled=False),
            RedactionPattern(name="live", pattern="L"),
        ],
    )
    turned = [(r.name, r.can_turn_on) for r in view(config).rules]
    assert turned == [
        ("credentials", True),
        ("phone", True),
        ("ticket", True),
        ("twin", False),
        ("twin", False),
        ("phone", False),
        ("live", False),
    ]


def test_a_stricter_change_carries_what_its_button_posts() -> None:
    config = make_config()
    config.sources = {"memo": SourceConfig(), "notes": SourceConfig(sensitivity="private", enabled=False)}
    found = {s.name: s for s in view(config).sources}
    assert [(c.what, c.act) for c in found["memo"].changes] == [
        ("Make it private", "private"),
        ("Make it mixed", "mixed"),
        ("Turn it off", "off"),
    ]
    assert [c.act for c in found["notes"].changes] == [None, None, None]


def test_making_a_source_private_names_what_waits_and_what_goes_in_first() -> None:
    asked = build_source_confirm("memo", staged=2, shown="h", docket_form=True, takes_in=True)
    assert asked.lines == [
        "It drops 2 records of memo waiting in staging now.",
        "From the next sync, nothing from memo is stored; its records already stored stay in raw.",
        "Making it mixed or work again is a line to change in vault.yaml by hand: it lets more in.",
        "Your edit of vault.yaml that isn't committed yet goes in first, as a commit of its own.",
    ]
    assert asked.line == "sensitivity: private"
    calm = build_source_confirm("memo", staged=0, shown="h", docket_form=False)
    assert calm.lines[0] == "Nothing of memo waits in staging now."
    assert len(calm.lines) == 3
