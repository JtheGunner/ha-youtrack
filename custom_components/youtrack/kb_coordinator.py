"""Data update coordinator for the knowledge base of one YouTrack project."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KB_INTERVAL_FACTOR,
    KB_RECENT_ARTICLES,
)
from .exceptions import (
    YouTrackAuthError,
    YouTrackError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry
    from .models import Article

_LOGGER = logging.getLogger(__name__)
_EPOCH = datetime.fromtimestamp(0, UTC)


@dataclass(frozen=True, slots=True)
class KnowledgeBaseData:
    """The size of a project's knowledge base and its most recently changed articles."""

    count: int
    capped: bool
    recent: tuple[Article, ...]


class KnowledgeBaseCoordinator(DataUpdateCoordinator[KnowledgeBaseData]):
    """Fetch the articles of one project."""

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
            name=f"{DOMAIN} {self.project_key} knowledge base",
            update_interval=timedelta(
                minutes=KB_INTERVAL_FACTOR
                * entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    async def _async_update_data(self) -> KnowledgeBaseData:
        """Fetch the project's articles."""
        try:
            articles, capped = await self._client.get_all_articles(
                query=f"project: {{{self.project_key}}}"
            )
        except YouTrackAuthError as err:
            raise ConfigEntryAuthFailed("YouTrack rejected the stored token") from err
        except YouTrackPermissionError as err:
            # The token may be fine for issues but not allowed to read the knowledge base.
            raise UpdateFailed(f"No permission to read the articles of {self.project_key}") from err
        except YouTrackRateLimitError as err:
            raise UpdateFailed(str(err), retry_after=err.retry_after) from err
        except YouTrackError as err:
            raise UpdateFailed(f"Error fetching articles of {self.project_key}: {err}") from err

        # The query is not trusted to filter by project, so make sure of it here.
        mine = [article for article in articles if article.project in (None, self.project_key)]
        recent = sorted(mine, key=lambda article: article.updated or _EPOCH, reverse=True)
        return KnowledgeBaseData(
            count=len(mine), capped=capped, recent=tuple(recent[:KB_RECENT_ARTICLES])
        )
