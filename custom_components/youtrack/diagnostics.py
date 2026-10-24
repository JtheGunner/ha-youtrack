"""Diagnostics for the YouTrack integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_API_TOKEN, CONF_URL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry

# The title and unique ID are derived from the server URL.
TO_REDACT = {CONF_API_TOKEN, CONF_URL, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: YouTrackConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "projects": {
            coordinator.project_key: {
                "last_update_success": coordinator.last_update_success,
                "active_counts": coordinator.active_counts,
                "counts": dict(coordinator.data.counts) if coordinator.data else None,
            }
            for coordinator in entry.runtime_data.coordinators.values()
        },
    }
