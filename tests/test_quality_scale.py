"""Tests that the quality scale file is complete and honest."""

from pathlib import Path

import yaml

QUALITY_SCALE = (
    Path(__file__).parent.parent / "custom_components" / "youtrack" / "quality_scale.yaml"
)
BRONZE = {
    "action-setup",
    "appropriate-polling",
    "brands",
    "common-modules",
    "config-flow-test-coverage",
    "config-flow",
    "dependency-transparency",
    "docs-actions",
    "docs-high-level-description",
    "docs-installation-instructions",
    "docs-removal-instructions",
    "entity-event-setup",
    "entity-unique-id",
    "has-entity-name",
    "runtime-data",
    "test-before-configure",
    "test-before-setup",
    "unique-config-entry",
}
SILVER = {
    "action-exceptions",
    "config-entry-unloading",
    "docs-configuration-parameters",
    "docs-installation-parameters",
    "entity-unavailable",
    "integration-owner",
    "log-when-unavailable",
    "parallel-updates",
    "reauthentication-flow",
    "test-coverage",
}


def _rules() -> dict[str, object]:
    rules: dict[str, object] = yaml.safe_load(QUALITY_SCALE.read_text())["rules"]
    return rules


def test_every_bronze_and_silver_rule_is_listed_once() -> None:
    """No rule is missing and nothing unknown is claimed."""
    assert set(_rules()) == BRONZE | SILVER


def test_statuses_are_valid_and_exemptions_are_explained() -> None:
    """``exempt`` and ``todo`` always say why."""
    for name, rule in _rules().items():
        if rule == "done":
            continue
        assert isinstance(rule, dict), name
        assert rule["status"] in {"done", "exempt", "todo"}, name
        if rule["status"] != "done":
            assert rule.get("comment"), name
