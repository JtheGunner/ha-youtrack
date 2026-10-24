"""Runtime data shared by the platforms of a config entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import YouTrackClient


@dataclass
class YouTrackRuntimeData:
    """Objects created while setting up a config entry."""

    client: YouTrackClient


type YouTrackConfigEntry = ConfigEntry[YouTrackRuntimeData]
