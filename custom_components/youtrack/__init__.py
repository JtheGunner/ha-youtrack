"""The YouTrack integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .activity import ActivityFeed
from .api import YouTrackClient
from .const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from .coordinator import YouTrackProjectCoordinator
from .data import YouTrackRuntimeData
from .exceptions import YouTrackAuthError, YouTrackError, YouTrackPermissionError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR, Platform.TODO]


async def async_setup_entry(hass: HomeAssistant, entry: YouTrackConfigEntry) -> bool:
    """Connect to YouTrack and store the client for the platforms."""
    verify_ssl: bool = entry.data[CONF_VERIFY_SSL]
    client = YouTrackClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        entry.data[CONF_URL],
        entry.data[CONF_API_TOKEN],
        verify_ssl=verify_ssl,
    )
    try:
        await client.get_current_user()
    except (YouTrackAuthError, YouTrackPermissionError) as err:
        raise ConfigEntryAuthFailed("YouTrack rejected the stored token") from err
    except YouTrackError as err:
        raise ConfigEntryNotReady(f"Cannot reach YouTrack: {err}") from err

    entry.runtime_data = YouTrackRuntimeData(client=client, feed=ActivityFeed(client))
    for subentry in entry.subentries.values():
        coordinator = YouTrackProjectCoordinator(hass, entry, subentry)
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data.coordinators[subentry.subentry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YouTrackConfigEntry) -> bool:
    """Unload the config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_update(hass: HomeAssistant, entry: YouTrackConfigEntry) -> None:
    """Reload the entry when projects are added or removed."""
    await hass.config_entries.async_reload(entry.entry_id)
