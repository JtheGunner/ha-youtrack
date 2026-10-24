"""Data update coordinator for one YouTrack project."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_SCAN_INTERVAL,
    COUNT_QUERIES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .exceptions import (
    YouTrackAuthError,
    YouTrackError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectData:
    """Snapshot of one project. A count of None means YouTrack has not computed it yet."""

    counts: Mapping[str, int | None]


class YouTrackProjectCoordinator(DataUpdateCoordinator[ProjectData]):
    """Fetch the statistics of one project."""

    config_entry: YouTrackConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: YouTrackConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Create the coordinator for ``subentry``."""
        self.project_id: str = subentry.data[CONF_PROJECT_ID]
        self.project_key: str = subentry.data[CONF_PROJECT_KEY]
        self.project_name: str = subentry.title
        self.subentry_id = subentry.subentry_id
        self._client = entry.runtime_data.client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.project_key}",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    async def _async_update_data(self) -> ProjectData:
        """Fetch all counts of the project."""
        try:
            values = await asyncio.gather(
                *(
                    self._client.count_issues(query.format(project=self.project_key))
                    for query in COUNT_QUERIES.values()
                )
            )
        except (YouTrackAuthError, YouTrackPermissionError) as err:
            raise ConfigEntryAuthFailed("YouTrack rejected the stored token") from err
        except YouTrackRateLimitError as err:
            raise UpdateFailed(str(err), retry_after=err.retry_after) from err
        except YouTrackError as err:
            raise UpdateFailed(f"Error fetching {self.project_key}: {err}") from err

        previous = self.data.counts if self.data else {}
        return ProjectData(
            counts={
                key: value if value is not None else previous.get(key)
                for key, value in zip(COUNT_QUERIES, values, strict=True)
            }
        )
