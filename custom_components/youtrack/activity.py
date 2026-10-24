"""Shared reader of the YouTrack activity feed.

The feed covers the whole instance and cannot be filtered by project, so one reader polls it and
hands each project coordinator the events of its own project.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .models import ActivityKind

if TYPE_CHECKING:
    from .api import YouTrackClient
    from .models import Activity

_LOGGER = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 5
BUFFER_SIZE = 500
# Polls closer together than this share one request (several projects poll at about the same time).
MIN_POLL_GAP = timedelta(seconds=20)
BASELINE_LOOKBACK = timedelta(days=1)
# Field changes this close to a creation, resolution or reopening belong to it.
ECHO_WINDOW = timedelta(seconds=5)
_ANCHORS = {ActivityKind.CREATED, ActivityKind.RESOLVED, ActivityKind.REOPENED}


def drop_echoes(batch: list[Activity]) -> list[Activity]:
    """Remove field changes that merely echo a creation, resolution or reopening.

    Creating an issue writes its initial field values and resolving it changes the state; both would
    otherwise show up as an extra "updated" event.
    """
    anchors: dict[str, list[datetime]] = {}
    for activity in batch:
        if activity.kind in _ANCHORS:
            anchors.setdefault(activity.issue_id, []).append(activity.timestamp)
    return [
        activity
        for activity in batch
        if activity.kind is not ActivityKind.UPDATED
        or not any(
            abs(activity.timestamp - stamp) <= ECHO_WINDOW
            for stamp in anchors.get(activity.issue_id, ())
        )
    ]


class ActivityFeed:
    """Polls the activity feed and keeps recent events for the project coordinators."""

    def __init__(self, client: YouTrackClient) -> None:
        """Create a feed that has not looked at YouTrack yet."""
        self._client = client
        self._lock = asyncio.Lock()
        self._cursor: datetime | None = None
        self._seen: set[str] = set()
        self._last_poll: datetime | None = None
        self._buffer: deque[tuple[int, Activity]] = deque(maxlen=BUFFER_SIZE)
        self._sequence = 0

    @property
    def sequence(self) -> int:
        """Return the number of events seen so far; a consumer starts from this value."""
        return self._sequence

    def events_since(self, sequence: int, project: str) -> tuple[list[Activity], int]:
        """Return the events of ``project`` after ``sequence`` and the new sequence."""
        events = [
            activity
            for number, activity in self._buffer
            if number > sequence and activity.project == project
        ]
        return events, self._sequence

    async def poll(self, now: datetime) -> None:
        """Fetch new events unless another project did it a moment ago.

        The first call only sets the baseline, so existing history never turns into events.
        """
        async with self._lock:
            if self._last_poll and now - self._last_poll < MIN_POLL_GAP:
                return
            if self._cursor is None:
                await self._set_baseline(now)
            else:
                await self._read_new()
            self._last_poll = now

    async def _set_baseline(self, now: datetime) -> None:
        page = await self._client.get_activities(now - BASELINE_LOOKBACK, top=1, newest_first=True)
        newest = page.items
        self._cursor = newest[0].timestamp if newest else now
        self._seen = {activity.id for activity in newest}

    async def _read_new(self) -> None:
        assert self._cursor is not None
        batch: list[Activity] = []
        read = 0
        for _ in range(MAX_PAGES):
            page = await self._client.get_activities(self._cursor, top=PAGE_SIZE, skip=read)
            batch.extend(page.items)
            read += page.size
            if page.size < PAGE_SIZE:
                break
        else:
            _LOGGER.warning(
                "More than %s new activities; the rest follows with the next poll", read
            )
        fresh = [activity for activity in batch if activity.id not in self._seen]
        if not fresh:
            return
        newest = max(activity.timestamp for activity in fresh)
        if newest > self._cursor:
            self._cursor = newest
            self._seen = set()
        self._seen.update(activity.id for activity in fresh if activity.timestamp == newest)
        for activity in drop_echoes(fresh):
            self._sequence += 1
            self._buffer.append((self._sequence, activity))
