"""The Settings page's parts on invented settings (DR-0505, DR-0506, DR-0510 to DR-0512): the URL, each source's
changes stricter first with their lines, the never-store values only when asked, the rules' commands, the models."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from docket_testkit import make_config

from docket.index.build import VectorCoverage
from docket.models.catalogue import E5_SMALL
from docket.models.config import Choice
from docket.models.provider import Off
from docket.models.report import Environment, Report
from docket.vault import redaction
from docket.vault.config import NeverStore, Redaction, RedactionPattern, SourceConfig, VaultConfig
from docket.web.settings_view import Facts, SettingsState, SettingsStateError, SettingsView, SourceFacts, build

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
