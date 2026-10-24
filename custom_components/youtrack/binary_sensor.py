"""Binary sensors for YouTrack projects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass

from .entity import YouTrackProjectEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class YouTrackFlagDescription(BinarySensorEntityDescription):
    """A flag that is on while a count is above zero."""

    count_key: str


FLAGS: tuple[YouTrackFlagDescription, ...] = (
    YouTrackFlagDescription(
        key="has_overdue",
        translation_key="has_overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        count_key="overdue",
    ),
    YouTrackFlagDescription(
        key="has_critical_open",
        translation_key="has_critical_open",
        device_class=BinarySensorDeviceClass.PROBLEM,
        count_key="critical_open",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the flags whose count the project supports."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        supported = set(coordinator.active_counts)
        async_add_entities(
            [
                YouTrackFlag(coordinator, description)
                for description in FLAGS
                if description.count_key in supported
            ],
            config_subentry_id=subentry_id,
        )


class YouTrackFlag(YouTrackProjectEntity, BinarySensorEntity):
    """On while the underlying count is above zero."""

    entity_description: YouTrackFlagDescription

    def __init__(
        self, coordinator: YouTrackProjectCoordinator, description: YouTrackFlagDescription
    ) -> None:
        """Create the flag."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return True if at least one issue matches; None while unknown."""
        count = self.coordinator.data.counts.get(self.entity_description.count_key)
        return None if count is None else count > 0
