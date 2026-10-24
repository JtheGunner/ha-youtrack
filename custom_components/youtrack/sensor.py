"""Sensors for YouTrack projects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from .entity import YouTrackProjectEntity, remove_stale_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry
    from .queries import CustomQuery

PARALLEL_UPDATES = 0

QUERY_KEY_PREFIX = "query_"
SUMMARY_LIMIT = 120


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
        async_add_entities(
            [YouTrackQuerySensor(coordinator, query) for query in coordinator.custom_queries],
            config_subentry_id=subentry_id,
        )
        remove_stale_entities(
            hass,
            entry,
            "sensor",
            f"{coordinator.subentry_id}_{QUERY_KEY_PREFIX}",
            {
                f"{coordinator.subentry_id}_{QUERY_KEY_PREFIX}{query.id}"
                for query in coordinator.custom_queries
            },
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


class YouTrackQuerySensor(YouTrackProjectEntity, SensorEntity):
    """The result of a user-defined search: the match count plus the first issues."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:filter-variant"

    def __init__(self, coordinator: YouTrackProjectCoordinator, query: CustomQuery) -> None:
        """Create the sensor."""
        super().__init__(coordinator, f"{QUERY_KEY_PREFIX}{query.id}")
        self._query = query
        self._attr_name = query.name

    @property
    def native_value(self) -> int | None:
        """Return the number of matching issues."""
        result = self.coordinator.data.queries.get(self._query.id)
        return result.count if result else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the query and its first issues."""
        base_url = self.coordinator.config_entry.data["url"]
        result = self.coordinator.data.queries.get(self._query.id)
        return {
            "query": self._query.text,
            "issues": [
                {
                    "id": issue.id,
                    "summary": issue.summary[:SUMMARY_LIMIT],
                    "state": issue.state,
                    "priority": issue.priority,
                    "assignee": issue.assignee,
                    "url": f"{base_url}/issue/{issue.id}",
                }
                for issue in (result.issues if result else ())
            ],
        }
