"""Tests for the diagnostics download."""

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.diagnostics import async_get_config_entry_diagnostics
from custom_components.youtrack.exceptions import YouTrackConnectionError

from .conftest import TOKEN, URL


async def test_diagnostics_redact_secrets(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Token and server address never appear in the diagnostics."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_project_entry)

    assert result["entry"]["data"]["api_token"] == "**REDACTED**"
    assert result["entry"]["data"]["url"] == "**REDACTED**"
    assert "yt.example.com" not in str(result)
    assert TOKEN not in str(result)
    assert URL not in str(result)
    demo = result["projects"]["DEMO"]
    assert demo["last_update_success"] is True
    assert demo["counts"]["total"] == 42
    assert "overdue" not in demo["active_counts"]
    assert result["knowledge_bases"]["DEMO"] == {
        "last_update_success": True,
        "articles": 2,
        "capped": False,
    }


async def test_diagnostics_with_failing_knowledge_base(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A knowledge base that could not be read is reported without numbers."""
    mock_client.get_all_articles.side_effect = YouTrackConnectionError("down")
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_project_entry)

    assert result["knowledge_bases"]["DEMO"] == {
        "last_update_success": False,
        "articles": None,
        "capped": None,
    }


async def test_diagnostics_without_data(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An instance without projects still produces diagnostics."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert result["projects"] == {}
    assert result["knowledge_bases"] == {}
