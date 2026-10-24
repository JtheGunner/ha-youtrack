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
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .exceptions import (
    YouTrackAuthError,
    YouTrackError,
    YouTrackInvalidQueryError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
)
from .queries import COUNT_QUERIES, DUE_DATE_FIELD_NAMES, CountQuery

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
        self._field_names: set[str] | None = None
        self._due_field: str | None = None
        self._invalid_queries: set[str] = set()
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.project_key}",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    @property
    def active_counts(self) -> list[str]:
        """Return the keys of the counts this project supports."""
        return [query.key for query in self._supported_queries()]

    def _supported_queries(self) -> list[CountQuery]:
        fields = self._field_names or set()
        return [
            query
            for query in COUNT_QUERIES
            if query.requires is None
            or (query.requires == "due" and self._due_field)
            or query.requires in fields
        ]

    async def _load_fields(self) -> None:
        """Remember which custom fields the project has (needed to pick valid queries)."""
        fields = await self._client.get_project_fields(self.project_id)
        self._field_names = {field.name for field in fields}
        self._due_field = next(
            (
                field.name
                for field in fields
                if field.field_type.startswith("date")
                and field.name.lower() in DUE_DATE_FIELD_NAMES
            ),
            None,
        )

    async def _count(self, query: CountQuery) -> int | None:
        """Run one count; a query YouTrack rejects yields None instead of failing the poll."""
        text = query.build(self.project_key, self._due_field)
        try:
            return await self._client.count_issues(text)
        except YouTrackInvalidQueryError as err:
            if query.key not in self._invalid_queries:
                self._invalid_queries.add(query.key)
                _LOGGER.warning(
                    "YouTrack rejected the %s count for %s (%s): %s",
                    query.key,
                    self.project_key,
                    text,
                    err,
                )
            return None

    async def _async_update_data(self) -> ProjectData:
        """Fetch all supported counts of the project."""
        try:
            if self._field_names is None:
                await self._load_fields()
            queries = self._supported_queries()
            values = await asyncio.gather(*(self._count(query) for query in queries))
        except (YouTrackAuthError, YouTrackPermissionError) as err:
            raise ConfigEntryAuthFailed("YouTrack rejected the stored token") from err
        except YouTrackRateLimitError as err:
            raise UpdateFailed(str(err), retry_after=err.retry_after) from err
        except YouTrackError as err:
            raise UpdateFailed(f"Error fetching {self.project_key}: {err}") from err

        previous = self.data.counts if self.data else {}
        return ProjectData(
            counts={
                query.key: value if value is not None else previous.get(query.key)
                for query, value in zip(queries, values, strict=True)
            }
        )
