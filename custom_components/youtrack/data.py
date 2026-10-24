"""Runtime data shared by the platforms of a config entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import YouTrackClient
    from .coordinator import YouTrackProjectCoordinator


@dataclass
class YouTrackRuntimeData:
    """Objects created while setting up a config entry."""

    client: YouTrackClient
    # One coordinator per project subentry, keyed by subentry id.
    coordinators: dict[str, YouTrackProjectCoordinator] = field(default_factory=dict)


type YouTrackConfigEntry = ConfigEntry[YouTrackRuntimeData]
