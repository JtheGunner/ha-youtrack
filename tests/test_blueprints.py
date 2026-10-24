"""Tests that the example blueprints work in a real automation."""

import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

ROOT = Path(__file__).parent.parent
BLUEPRINTS = ROOT / "blueprints" / "automation" / "youtrack"
DASHBOARD = ROOT / "docs" / "examples" / "dashboard.yaml"
KNOWLEDGE_BASE_SCRIPT = ROOT / "docs" / "examples" / "knowledge-base-script.yaml"
CRITICAL = "sensor.demo_project_critical_open_issues"


@pytest.fixture(autouse=True)
def install_blueprints(hass: HomeAssistant) -> None:
    """Put the blueprints where Home Assistant looks for them."""
    target = Path(hass.config.path("blueprints/automation/youtrack"))
    target.mkdir(parents=True, exist_ok=True)
    for blueprint in BLUEPRINTS.glob("*.yaml"):
        shutil.copy(blueprint, target / blueprint.name)


async def test_notify_when_critical_issues_increase(hass: HomeAssistant) -> None:
    """The notification runs when the count goes up and not when it goes down."""
    notifications = async_mock_service(hass, "notify", "notify")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "youtrack/notify-critical-issues.yaml",
                    "input": {
                        "critical_count": CRITICAL,
                        "notify_action": [
                            {
                                "action": "notify.notify",
                                "data": {"message": "{{ project }}: {{ count }} critical"},
                            }
                        ],
                    },
                }
            }
        },
    )
    hass.states.async_set(CRITICAL, "1", {"friendly_name": "Demo Project Critical open issues"})
    await hass.async_block_till_done()
    assert notifications == []  # first state has no previous one

    hass.states.async_set(CRITICAL, "3", {"friendly_name": "Demo Project Critical open issues"})
    await hass.async_block_till_done()
    assert [call.data["message"] for call in notifications] == [
        "Demo Project Critical open issues: 3 critical"
    ]

    hass.states.async_set(CRITICAL, "2", {"friendly_name": "Demo Project Critical open issues"})
    hass.states.async_set(
        CRITICAL, "unknown", {"friendly_name": "Demo Project Critical open issues"}
    )
    await hass.async_block_till_done()
    assert len(notifications) == 1


@pytest.mark.parametrize(("priority", "expected"), [("", {}), ("Major", {"priority": "Major"})])
async def test_issue_for_unavailable_entity(
    hass: HomeAssistant, priority: str, expected: dict[str, str]
) -> None:
    """An entity unavailable for long enough creates one issue in the chosen project."""
    created = async_mock_service(hass, "youtrack", "create_issue")
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "youtrack/issue-for-unavailable-entity.yaml",
                    "input": {
                        "watched_entity": "sensor.boiler",
                        "wait_minutes": 30,
                        "project": "device-123",
                        "priority": priority,
                    },
                }
            }
        },
    )
    hass.states.async_set("sensor.boiler", "20", {"friendly_name": "Boiler"})
    await hass.async_block_till_done()

    hass.states.async_set("sensor.boiler", "unavailable", {"friendly_name": "Boiler"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=29))
    await hass.async_block_till_done()
    assert created == []

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=31))
    await hass.async_block_till_done()

    (call,) = created
    assert call.data["device_id"] == "device-123"
    assert call.data["summary"] == "Boiler is unavailable"
    assert "sensor.boiler has been unavailable for 0:30:00" in call.data["description"]
    assert {key: call.data[key] for key in expected} == expected
    assert ("priority" in call.data) is bool(expected)


async def test_example_dashboard_uses_entities_the_integration_creates(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Every entity of the example dashboard exists for a project called "Demo Project"."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()
    view = yaml.safe_load((DASHBOARD).read_text())

    used = {
        entity
        for card in _walk(view)
        for entity in ([card["entity"]] if "entity" in card else [])
        + [e for e in card.get("entities", []) if isinstance(e, str)]
    }

    assert used
    assert [entity for entity in sorted(used) if hass.states.get(entity) is None] == []


def _walk(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("cards", []):
        yield from _walk(child)


async def _run_knowledge_base_script(
    hass: HomeAssistant, articles: list[dict[str, Any]], content: str, truncated: bool
) -> list[ServiceCall]:
    """Run the example script against canned article actions and return the notifications."""
    searches: list[ServiceCall] = []

    async def search(call: ServiceCall) -> dict[str, Any]:
        searches.append(call)
        return {"articles": articles}

    async def get_article(call: ServiceCall) -> dict[str, Any]:
        return {
            "id": call.data["article_id"],
            "title": "Heating manual",
            "url": "https://yt.example.com/articles/DEMO-A-2",
            "content": content,
            "truncated": truncated,
        }

    for name, handler in (("search_articles", search), ("get_article", get_article)):
        hass.services.async_register(
            "youtrack", name, handler, supports_response=SupportsResponse.ONLY
        )
    notifications: list[ServiceCall] = async_mock_service(hass, "notify", "notify")
    assert await async_setup_component(
        hass, "script", {"script": yaml.safe_load(KNOWLEDGE_BASE_SCRIPT.read_text())}
    )
    await hass.services.async_call(
        "script", "youtrack_knowledge_base_lookup", {"topic": "heating"}, blocking=True
    )
    assert [call.data["query"] for call in searches] == ["heating"]
    return notifications


async def test_knowledge_base_script_sends_the_article(hass: HomeAssistant) -> None:
    """The first match is read and sent with its link; cut-off content is marked."""
    (notification,) = await _run_knowledge_base_script(
        hass, [{"id": "DEMO-A-2"}], "Turn the dial.", truncated=True
    )

    assert notification.data["title"] == "Heating manual"
    assert notification.data["message"] == (
        "Turn the dial. …\nhttps://yt.example.com/articles/DEMO-A-2"
    )


async def test_knowledge_base_script_reports_no_match(hass: HomeAssistant) -> None:
    """Without a match the script says so and stops."""
    (notification,) = await _run_knowledge_base_script(hass, [], "", truncated=False)

    assert notification.data["message"] == "No article found for heating."
