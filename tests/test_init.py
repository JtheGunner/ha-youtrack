"""Tests for setting up and unloading the integration."""

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
