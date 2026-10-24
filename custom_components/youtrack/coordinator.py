"""Data update coordinator for one YouTrack project."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_QUERIES,
    CONF_SCAN_INTERVAL,
    CONF_WATCH,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    DUE_DATE_FIELD_NAMES,
    EVENT_PREFIX,
    MAX_WATCHED,
    WATCH_QUERY_ID,
)
from .exceptions import (
    YouTrackAuthError,
    YouTrackError,
    YouTrackInvalidQueryError,
    YouTrackPermissionError,
    YouTrackRateLimitError,
)
from .issue_cache import IssueCache
from .queries import COUNT_QUERIES, CountQuery, CustomQuery, watched_text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry
    from .models import Activity, Issue

_LOGGER = logging.getLogger(__name__)


def event_attributes(activity: Activity, base_url: str) -> dict[str, str | None]:
    """Return what automations get to know about an issue event."""
    attributes: dict[str, str | None] = {
        "issue_id": activity.issue_id,
        "summary": activity.summary,
        "project": activity.project,
        "author": activity.author,
        "url": f"{base_url}/issue/{activity.issue_id}",
    }
    if activity.field:
        attributes.update(
            field=activity.field, old_value=activity.old_value, new_value=activity.new_value
        )
    return attributes


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Outcome of a custom query: the total and the first issues."""

    count: int
    issues: tuple[Issue, ...]


@dataclass(frozen=True, slots=True)
class ProjectData:
    """Snapshot of one project. A count of None means YouTrack has not computed it yet."""

    counts: Mapping[str, int | None]
    queries: Mapping[str, QueryResult]
    open_issues: Mapping[str, Issue]
    new_events: tuple[Activity, ...] = ()


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
        self._feed = entry.runtime_data.feed
        self._event_sequence: int | None = None
        self._cache = IssueCache(self._client, self.project_key, dt_util.get_default_time_zone())
        self.custom_queries: tuple[CustomQuery, ...] = tuple(
            CustomQuery.from_data(item) for item in subentry.data.get(CONF_QUERIES, ())
        )
        watch = str(subentry.data.get(CONF_WATCH, "")).strip()
        self.watch_query: CustomQuery | None = (
            CustomQuery(
                id=WATCH_QUERY_ID,
                name="Watched issues",
                text=watched_text(watch),
                top=MAX_WATCHED,
            )
            if watch
            else None
        )
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
    def _queries_to_run(self) -> tuple[CustomQuery, ...]:
        if self.watch_query is None:
            return self.custom_queries
        return (*self.custom_queries, self.watch_query)

    @property
    def watched_issues(self) -> dict[str, Issue] | None:
        """Return the watched issues by id, or None while there are no results to go by."""
        if self.watch_query is None or not self.data:
            return None
        result = self.data.queries.get(WATCH_QUERY_ID)
        return {issue.id: issue for issue in result.issues} if result else None

    @property
    def due_field(self) -> str | None:
        """Return the name of the project's due date field, if it has one."""
        return self._due_field

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

    async def _run_custom(self, query: CustomQuery) -> QueryResult | None:
        """Run one custom query; a query YouTrack rejects yields None."""
        text = query.build(self.project_key)
        try:
            count, issues = await asyncio.gather(
                self._client.count_issues(text), self._client.get_issues(text, top=query.top)
            )
        except YouTrackInvalidQueryError as err:
            if query.id not in self._invalid_queries:
                self._invalid_queries.add(query.id)
                _LOGGER.warning(
                    "YouTrack rejected the custom query %s for %s (%s): %s",
                    query.name,
                    self.project_key,
                    text,
                    err,
                )
            return None
        if count is None:
            return None
        return QueryResult(count=count, issues=tuple(issues))

    async def _async_update_data(self) -> ProjectData:
        """Fetch all supported counts of the project."""
        try:
            if self._field_names is None:
                await self._load_fields()
            queries = self._supported_queries()
            values, results, *_ = await asyncio.gather(
                asyncio.gather(*(self._count(query) for query in queries)),
                asyncio.gather(*(self._run_custom(query) for query in self._queries_to_run)),
                self._cache.refresh(dt_util.utcnow()),
                self._feed.poll(dt_util.utcnow()),
            )
        except (YouTrackAuthError, YouTrackPermissionError) as err:
            raise ConfigEntryAuthFailed("YouTrack rejected the stored token") from err
        except YouTrackRateLimitError as err:
            raise UpdateFailed(str(err), retry_after=err.retry_after) from err
        except YouTrackError as err:
            raise UpdateFailed(f"Error fetching {self.project_key}: {err}") from err

        previous_counts = self.data.counts if self.data else {}
        previous_queries = self.data.queries if self.data else {}
        custom = {
            query.id: result if result is not None else previous_queries.get(query.id)
            for query, result in zip(self._queries_to_run, results, strict=True)
        }
        new_events = self._take_events()
        return ProjectData(
            counts={
                query.key: value if value is not None else previous_counts.get(query.key)
                for query, value in zip(queries, values, strict=True)
            },
            queries={key: result for key, result in custom.items() if result is not None},
            open_issues=dict(self._cache.issues),
            new_events=new_events,
        )

    def _take_events(self) -> tuple[Activity, ...]:
        """Collect the project's new activity and announce it on the event bus.

        Activity that happened before the first poll is never reported.
        """
        if self._event_sequence is None:
            self._event_sequence = self._feed.sequence
            return ()
        events, self._event_sequence = self._feed.events_since(
            self._event_sequence, self.project_key
        )
        base_url = self.config_entry.data["url"]
        for activity in events:
            self.hass.bus.async_fire(
                f"{EVENT_PREFIX}{activity.kind}",
                {
                    "entry_id": self.config_entry.entry_id,
                    **event_attributes(activity, base_url),
                },
            )
        return tuple(events)
