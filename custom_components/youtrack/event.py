"""Event entity that reports what happens to the issues of a project."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.event import EventEntity

from .coordinator import event_attributes
from .entity import YouTrackProjectEntity
from .models import ActivityKind

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the activity event entity of every project."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([YouTrackActivityEvent(coordinator)], config_subentry_id=subentry_id)


class YouTrackActivityEvent(YouTrackProjectEntity, EventEntity):
    """Fires when an issue of the project is created, updated, resolved, reopened or commented."""

    _attr_translation_key = "issue_activity"

    def __init__(self, coordinator: YouTrackProjectCoordinator) -> None:
        """Create the entity."""
        super().__init__(coordinator, "activity")
        self._attr_event_types = [kind.value for kind in ActivityKind]

    def _handle_coordinator_update(self) -> None:
        """Trigger one event per new activity."""
        base_url = self.coordinator.config_entry.data["url"]
        for activity in self.coordinator.data.new_events:
            self._trigger_event(activity.kind.value, event_attributes(activity, base_url))
        super()._handle_coordinator_update()
