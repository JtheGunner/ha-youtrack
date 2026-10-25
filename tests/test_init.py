"""Tests for setting up and unloading the integration."""

import logging
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import DOMAIN
from custom_components.youtrack.exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackPermissionError,
)
from custom_components.youtrack.models import Issue


async def test_integration_is_discoverable(hass: HomeAssistant) -> None:
    """Home Assistant finds the integration in custom_components."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == DOMAIN
    assert integration.name == "YouTrack"


async def test_setup_and_unload(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A reachable instance loads, exposes the client and unloads cleanly."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.client is mock_client

    await hass.config_entries.async_unload(mock_config_entry.entry_id)

    entry = hass.config_entries.async_get_entry(mock_config_entry.entry_id)
    assert entry is not None
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("exception", [YouTrackAuthError("x"), YouTrackPermissionError("x")])
async def test_setup_starts_reauth_for_rejected_token(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    exception: Exception,
) -> None:
    """A rejected token puts the entry into the reauth state."""
    mock_client.get_current_user.side_effect = exception
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(mock_config_entry.async_get_active_flows(hass, {"reauth"}))


async def test_setup_retries_when_server_unreachable(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Connection problems are retried later."""
    mock_client.get_current_user.side_effect = YouTrackConnectionError("down")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_skips_project_removed_during_setup(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A project deleted while the entry is still loading is not set up."""
    issues: list[Issue] = mock_client.get_issues.return_value

    async def get_issues_then_remove_project(*args: object, **kwargs: object) -> list[Issue]:
        hass.config_entries.async_remove_subentry(mock_project_entry, "sub-demo")
        return issues

    mock_client.get_issues.side_effect = get_issues_then_remove_project
    mock_project_entry.add_to_hass(hass)

    with caplog.at_level(logging.ERROR):
        await hass.config_entries.async_setup(mock_project_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_project_entry.state is ConfigEntryState.LOADED
    assert mock_project_entry.runtime_data.coordinators == {}
    assert "unknown subentry" not in caplog.text
