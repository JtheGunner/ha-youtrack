"""Tests for the knowledge base coordinator and its sensors."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    DOMAIN,
    SUBENTRY_PROJECT,
)
from custom_components.youtrack.exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
)
from custom_components.youtrack.models import Article

from .common import load_json_fixture
from .conftest import TOKEN, URL

COUNT = "sensor.demo_project_articles"
LAST = "sensor.demo_project_last_updated_article"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data={"url": URL, "api_token": TOKEN, "verify_ssl": True},
        subentries_data=[
            {
                "data": {CONF_PROJECT_ID: "0-1", CONF_PROJECT_KEY: "DEMO"},
                "subentry_id": "sub-demo",
                "subentry_type": SUBENTRY_PROJECT,
                "title": "Demo Project",
                "unique_id": "0-1",
            }
        ],
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _articles() -> list[Article]:
    return [Article.from_json(item) for item in load_json_fixture("articles.json")]


async def test_sensors_show_count_and_latest_article(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """The count and the most recently changed article are exposed."""
    await _setup(hass)

    count = hass.states.get(COUNT)
    assert count is not None
    assert count.state == "2"
    assert count.attributes["capped"] is False

    last = hass.states.get(LAST)
    assert last is not None
    assert datetime.fromisoformat(last.state) == datetime.fromtimestamp(1790000900, UTC)
    assert last.attributes["id"] == "DEMO-A-2"
    assert last.attributes["title"] == "Heating manual"
    assert last.attributes["author"] == "example.user"
    assert last.attributes["url"] == f"{URL}/articles/DEMO-A-2"
    assert [item["id"] for item in last.attributes["recent"]] == ["DEMO-A-2", "DEMO-A-1"]
    mock_client.get_all_articles.assert_awaited_with(query="project: {DEMO}")


async def test_empty_knowledge_base(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A project without articles counts zero and has no latest article."""
    mock_client.get_all_articles.return_value = ([], False)
    await _setup(hass)

    assert hass.states.get(COUNT).state == "0"  # type: ignore[union-attr]
    last = hass.states.get(LAST)
    assert last is not None
    assert last.state == "unknown"
    assert last.attributes["recent"] == []


async def test_articles_of_other_projects_are_ignored(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """The count does not rely on the query alone to filter by project."""
    foreign = Article.from_json(
        {**load_json_fixture("articles.json")[0], "id": "9-9", "idReadable": "OPS-A-1"}
        | {"project": {"shortName": "OPS"}}
    )
    mock_client.get_all_articles.return_value = ([*_articles(), foreign], False)
    await _setup(hass)

    assert hass.states.get(COUNT).state == "2"  # type: ignore[union-attr]


async def test_capped_listing_is_flagged(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A listing cut short by the reading limit says so."""
    mock_client.get_all_articles.return_value = (_articles(), True)
    await _setup(hass)

    assert hass.states.get(COUNT).attributes["capped"] is True  # type: ignore[union-attr]


async def test_articles_without_date_sort_last(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Undated articles never win over dated ones."""
    undated = Article.from_json({**load_json_fixture("articles.json")[0], "updated": None})
    mock_client.get_all_articles.return_value = ([undated, *_articles()[1:]], False)
    await _setup(hass)

    last = hass.states.get(LAST)
    assert last is not None
    assert last.attributes["id"] == "DEMO-A-1"


@pytest.mark.parametrize(
    "error",
    [
        YouTrackConnectionError("down"),
        YouTrackPermissionError("forbidden"),
        YouTrackRateLimitError("slow down", retry_after=30),
    ],
)
async def test_knowledge_base_failure_does_not_block_the_entry(
    hass: HomeAssistant, mock_client: MagicMock, error: Exception
) -> None:
    """Issues keep working when the articles cannot be read."""
    mock_client.get_all_articles.side_effect = error
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.demo_project_open_issues") is not None
    assert hass.states.get(COUNT).state == "unavailable"  # type: ignore[union-attr]


async def test_knowledge_base_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A rejected token is a reauth, not a silent outage."""
    mock_client.get_all_articles.side_effect = YouTrackAuthError("nope")
    entry = await _setup(hass)

    assert any(
        flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress()
    )
    assert entry.state is ConfigEntryState.LOADED


async def test_knowledge_base_recovers(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The sensors come back once the articles are readable again."""
    mock_client.get_all_articles.side_effect = YouTrackConnectionError("down")
    entry = await _setup(hass)
    assert hass.states.get(COUNT).state == "unavailable"  # type: ignore[union-attr]

    mock_client.get_all_articles.side_effect = None
    await entry.runtime_data.knowledge_bases["sub-demo"].async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(COUNT).state == "2"  # type: ignore[union-attr]
