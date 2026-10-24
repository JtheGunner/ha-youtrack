"""Runtime data shared by the platforms of a config entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .activity import ActivityFeed
    from .api import YouTrackClient
    from .coordinator import YouTrackProjectCoordinator
    from .kb_coordinator import KnowledgeBaseCoordinator


@dataclass
class YouTrackRuntimeData:
    """Objects created while setting up a config entry."""

    client: YouTrackClient
    feed: ActivityFeed
    # One coordinator per project subentry, keyed by subentry id.
    coordinators: dict[str, YouTrackProjectCoordinator] = field(default_factory=dict)
    # The knowledge base of each project, polled more slowly, keyed by subentry id.
    knowledge_bases: dict[str, KnowledgeBaseCoordinator] = field(default_factory=dict)


type YouTrackConfigEntry = ConfigEntry[YouTrackRuntimeData]
