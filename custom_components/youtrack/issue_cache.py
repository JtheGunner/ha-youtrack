"""Cache of the unresolved issues of one project, refreshed with an ``updated:`` cursor."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .api import YouTrackClient
    from .models import Issue

_LOGGER = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_ISSUES = 1000
# Every nth refresh reloads everything; that is how deleted and moved issues leave the cache.
RECONCILE_EVERY = 12
# Delta windows start this long before the previous poll to absorb clock skew and slow requests.
CURSOR_MARGIN = timedelta(minutes=5)
_MINUTE = "%Y-%m-%dT%H:%M"


class IssueCache:
    """Keeps the unresolved issues of a project up to date with small delta requests.

    YouTrack reads the timestamps of ``updated:`` in the time zone of the token's user, which the
    API does not reveal. It is therefore detected once per full load by asking for the newest issue
    with each candidate zone. If none fits, every refresh stays a full load, which is slower but
    always correct.
    """

    def __init__(
        self,
        client: YouTrackClient,
        project_key: str,
        local_zone: tzinfo,
        *,
        reconcile_every: int = RECONCILE_EVERY,
    ) -> None:
        """Create an empty cache."""
        self._client = client
        self._project = project_key
        self._zones: tuple[tzinfo, ...] = (UTC,) if local_zone is UTC else (UTC, local_zone)
        self._reconcile_every = reconcile_every
        self._issues: dict[str, Issue] = {}
        self._cursor: datetime | None = None
        self._zone: tzinfo | None = None
        self._deltas_since_full = 0

    @property
    def issues(self) -> Mapping[str, Issue]:
        """Return the cached unresolved issues by id."""
        return self._issues

    async def refresh(self, now: datetime) -> None:
        """Bring the cache up to date; ``now`` is the start of the current poll."""
        if (
            self._cursor is None
            or self._zone is None
            or self._deltas_since_full >= self._reconcile_every
        ):
            await self._full_load(now)
        else:
            await self._delta_load(now)

    async def _full_load(self, now: datetime) -> None:
        issues = await self._fetch(f"project: {{{self._project}}} #Unresolved")
        if len(issues) >= MAX_ISSUES:
            _LOGGER.warning(
                "Project %s has at least %s unresolved issues; keeping the most recently updated",
                self._project,
                MAX_ISSUES,
            )
        self._issues = {issue.id: issue for issue in issues}
        self._deltas_since_full = 0
        if self._zone is None:
            self._zone = await self._detect_zone(issues)
        self._cursor = now - CURSOR_MARGIN

    async def _delta_load(self, now: datetime) -> None:
        assert self._cursor is not None
        assert self._zone is not None
        since = self._cursor.astimezone(self._zone).strftime(_MINUTE)
        changed = await self._fetch(f"project: {{{self._project}}} updated: {since} .. *")
        for issue in changed:
            if issue.is_resolved:
                self._issues.pop(issue.id, None)
            else:
                self._issues[issue.id] = issue
        self._deltas_since_full += 1
        self._cursor = now - CURSOR_MARGIN

    async def _fetch(self, query: str) -> list[Issue]:
        """Read all pages of ``query``, newest update first, up to the cache limit."""
        found: list[Issue] = []
        while len(found) < MAX_ISSUES:
            page = await self._client.get_issues(
                f"{query} sort by: updated desc", top=PAGE_SIZE, skip=len(found)
            )
            found.extend(page)
            if len(page) < PAGE_SIZE:
                break
        return found

    async def _detect_zone(self, issues: list[Issue]) -> tzinfo | None:
        """Find the zone YouTrack uses for ``updated:`` by looking up the newest issue."""
        stamps = [issue.updated for issue in issues if issue.updated]
        if not stamps:
            return None
        newest = max(stamps)
        for zone in self._zones:
            minute = newest.astimezone(zone).strftime(_MINUTE)
            count = await self._client.count_issues(
                f"project: {{{self._project}}} updated: {minute} .. {minute}"
            )
            if count:
                return zone
        _LOGGER.debug("Cannot determine the time zone of YouTrack queries; polling in full")
        return None
