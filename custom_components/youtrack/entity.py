"""Base entity for the YouTrack integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import YouTrackProjectCoordinator
from .kb_coordinator import KnowledgeBaseCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry


def remove_stale_entities(
    hass: HomeAssistant,
    entry: YouTrackConfigEntry,
    platform: str,
    prefix: str,
    wanted: set[str],
) -> None:
    """Remove registry entries of ``platform`` that start with ``prefix`` but are not wanted."""
    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = registered.unique_id
        if (
            registered.domain == platform
            and unique_id.startswith(prefix)
            and unique_id not in wanted
        ):
            registry.async_remove(registered.entity_id)


def project_device_info(
    coordinator: YouTrackProjectCoordinator | KnowledgeBaseCoordinator,
) -> DeviceInfo:
    """Return the device of the project a coordinator belongs to."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{coordinator.project_id}")},
        name=coordinator.project_name,
        manufacturer="JetBrains",
        model="YouTrack project",
        configuration_url=f"{coordinator.config_entry.data['url']}/projects/{coordinator.project_key}",
    )


class YouTrackProjectEntity(CoordinatorEntity[YouTrackProjectCoordinator]):
    """An entity that belongs to the device of one YouTrack project."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YouTrackProjectCoordinator, key: str) -> None:
        """Attach the entity to the project device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.subentry_id}_{key}"
        self._attr_device_info = project_device_info(coordinator)


class YouTrackKnowledgeBaseEntity(CoordinatorEntity[KnowledgeBaseCoordinator]):
    """An entity about the knowledge base of a project, on the project's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KnowledgeBaseCoordinator, key: str) -> None:
        """Attach the entity to the project device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.subentry_id}_{key}"
        self._attr_device_info = project_device_info(coordinator)
