"""Tests for the coordinator, devices and sensors."""

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import CONF_SCAN_INTERVAL, DOMAIN
from custom_components.youtrack.exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackRateLimitError,
)

from .conftest import URL, fake_count

TOTAL = "sensor.demo_project_issues"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run one poll of the project coordinator and let the entities update."""
    coordinator = next(iter(entry.runtime_data.coordinators.values()))
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_project_device_and_sensor(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Each project becomes a device with its sensors attached to the subentry."""
    await _setup(hass, mock_project_entry)

    assert mock_project_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(TOTAL).state == "42"  # type: ignore[union-attr]
    mock_client.count_issues.assert_any_await("project: {DEMO}")

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{mock_project_entry.entry_id}_0-1"), mock_project_entry.entry_id
    )
    assert device is not None
    assert device.name == "Demo Project"
    assert device.manufacturer == "JetBrains"
    assert device.configuration_url == f"{URL}/projects/DEMO"

    entity = er.async_get(hass).async_get(TOTAL)
    assert entity is not None
    assert entity.unique_id == "sub-demo_total"
    assert entity.config_subentry_id == "sub-demo"


async def test_entry_without_projects_creates_no_devices(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An instance without projects loads but has nothing to show."""
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), mock_config_entry.entry_id)


async def test_values_refresh_on_poll(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """A later poll updates the state."""
    await _setup(hass, mock_project_entry)
    mock_client.count_issues.side_effect = None
    mock_client.count_issues.return_value = 43

    await _refresh(hass, mock_project_entry)

    assert hass.states.get(TOTAL).state == "43"  # type: ignore[union-attr]


async def test_unavailable_and_recovery(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """Connection failures make the sensor unavailable until the server answers again."""
    await _setup(hass, mock_project_entry)
    mock_client.count_issues.side_effect = YouTrackConnectionError("down")

    await _refresh(hass, mock_project_entry)
    assert hass.states.get(TOTAL).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    mock_client.count_issues.side_effect = fake_count
    await _refresh(hass, mock_project_entry)
    assert hass.states.get(TOTAL).state == "42"  # type: ignore[union-attr]


async def test_rate_limit_marks_update_failed(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """A 429 is treated as a failed update (retry_after is passed on)."""
    await _setup(hass, mock_project_entry)
    mock_client.count_issues.side_effect = YouTrackRateLimitError("slow down", retry_after=30)

    await _refresh(hass, mock_project_entry)

    assert hass.states.get(TOTAL).state == STATE_UNAVAILABLE  # type: ignore[union-attr]


async def test_auth_failure_during_poll_starts_reauth(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """A token that stops working triggers reauthentication."""
    await _setup(hass, mock_project_entry)
    mock_client.count_issues.side_effect = YouTrackAuthError("x")

    await _refresh(hass, mock_project_entry)

    assert any(mock_project_entry.async_get_active_flows(hass, {"reauth"}))


async def test_pending_count_keeps_previous_value(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """A count that YouTrack has not computed yet does not wipe the known value."""
    await _setup(hass, mock_project_entry)
    mock_client.count_issues.side_effect = None
    mock_client.count_issues.return_value = None

    await _refresh(hass, mock_project_entry)

    assert hass.states.get(TOTAL).state == "42"  # type: ignore[union-attr]


async def test_pending_count_on_first_refresh_is_unknown(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Without a previous value the sensor reports unknown."""
    mock_client.count_issues.side_effect = None
    mock_client.count_issues.return_value = None

    await _setup(hass, mock_project_entry)

    assert hass.states.get(TOTAL).state == STATE_UNKNOWN  # type: ignore[union-attr]


async def test_first_refresh_failure_retries_setup(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """If the first poll fails the entry is retried later."""
    mock_client.count_issues.side_effect = YouTrackConnectionError("down")

    await _setup(hass, mock_project_entry)

    assert mock_project_entry.state is ConfigEntryState.SETUP_RETRY


async def test_poll_interval_option(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The configured interval is used for polling."""
    mock_project_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_project_entry, options={CONF_SCAN_INTERVAL: 20})
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = next(iter(mock_project_entry.runtime_data.coordinators.values()))

    assert coordinator.update_interval == timedelta(minutes=20)
