"""Sensors for YouTrack projects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from .entity import YouTrackProjectEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class YouTrackCountSensorDescription(SensorEntityDescription):
    """Describes a sensor that exposes one issue count."""


def _count(key: str) -> YouTrackCountSensorDescription:
    return YouTrackCountSensorDescription(
        key=key,
        translation_key=f"issues_{key}",
        state_class=SensorStateClass.MEASUREMENT,
    )


COUNT_SENSORS: tuple[YouTrackCountSensorDescription, ...] = tuple(
    _count(key)
    for key in (
        "total",
        "open",
        "resolved",
        "unassigned",
        "overdue",
        "created_7d",
        "resolved_7d",
        "critical_open",
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the count sensors every project supports."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        supported = set(coordinator.active_counts)
        async_add_entities(
            [
                YouTrackCountSensor(coordinator, description)
                for description in COUNT_SENSORS
                if description.key in supported
            ],
            config_subentry_id=subentry_id,
        )


class YouTrackCountSensor(YouTrackProjectEntity, SensorEntity):
    """An issue count of a project."""

    entity_description: YouTrackCountSensorDescription

    def __init__(
        self,
        coordinator: YouTrackProjectCoordinator,
        description: YouTrackCountSensorDescription,
    ) -> None:
        """Create the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        """Return the current count."""
        return self.coordinator.data.counts.get(self.entity_description.key)
