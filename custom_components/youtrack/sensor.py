"""Sensors for YouTrack projects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)

from .entity import YouTrackKnowledgeBaseEntity, YouTrackProjectEntity, remove_stale_entities

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry
    from .kb_coordinator import KnowledgeBaseCoordinator
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
        knowledge_base = entry.runtime_data.knowledge_bases[subentry_id]
        async_add_entities(
            [YouTrackArticleCountSensor(knowledge_base), YouTrackLastArticleSensor(knowledge_base)],
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


class YouTrackArticleCountSensor(YouTrackKnowledgeBaseEntity, SensorEntity):
    """The number of knowledge base articles of a project."""

    _attr_translation_key = "articles"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: KnowledgeBaseCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "articles")

    @property
    def native_value(self) -> int:
        """Return the number of articles."""
        return self.coordinator.data.count

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Say whether the count was cut short by the reading limit."""
        return {"capped": self.coordinator.data.capped}


class YouTrackLastArticleSensor(YouTrackKnowledgeBaseEntity, SensorEntity):
    """When the knowledge base of a project last changed, and what changed."""

    _attr_translation_key = "last_updated_article"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: KnowledgeBaseCoordinator) -> None:
        """Create the sensor."""
        super().__init__(coordinator, "last_updated_article")

    @property
    def native_value(self) -> datetime | None:
        """Return when the most recently changed article was changed."""
        recent = self.coordinator.data.recent
        return recent[0].updated if recent else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the latest article and the few before it."""
        base_url = self.coordinator.config_entry.data["url"]
        recent = [
            {
                "id": article.readable_id,
                "title": article.summary,
                "author": article.author,
                "updated": article.updated.isoformat() if article.updated else None,
                "url": f"{base_url}/articles/{article.readable_id}",
            }
            for article in self.coordinator.data.recent
        ]
        return {**(recent[0] if recent else {}), "recent": recent}
