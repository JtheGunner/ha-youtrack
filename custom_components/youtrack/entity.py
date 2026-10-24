"""Base entity for the YouTrack integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import YouTrackProjectCoordinator


class YouTrackProjectEntity(CoordinatorEntity[YouTrackProjectCoordinator]):
    """An entity that belongs to the device of one YouTrack project."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YouTrackProjectCoordinator, key: str) -> None:
        """Attach the entity to the project device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{coordinator.project_id}")},
            name=coordinator.project_name,
            manufacturer="JetBrains",
            model="YouTrack project",
            configuration_url=(
                f"{coordinator.config_entry.data['url']}/projects/{coordinator.project_key}"
            ),
        )
