"""Tests for the incremental issue cache."""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.youtrack import issue_cache
from custom_components.youtrack.issue_cache import CURSOR_MARGIN, IssueCache
from custom_components.youtrack.models import Issue

T0 = datetime(2026, 10, 24, 12, 0, 30, tzinfo=UTC)
CEST = timezone(timedelta(hours=2))
MINUTE = "%Y-%m-%dT%H:%M"
FULL_QUERY = "project: {DEMO} #Unresolved sort by: updated desc"


def make_issue(key: str, updated: datetime | None, *, resolved: bool = False) -> Issue:
    """Build a minimal issue."""
    return Issue(
        id=key,
        summary=f"Issue {key}",
        created=T0,
        updated=updated,
        resolved=updated if resolved else None,
        project="DEMO",
        reporter=None,
        state=None,
        priority=None,
        type=None,
        assignee=None,
    )


class FakeYouTrack:
    """Answers the requests the cache makes from an in-memory issue list."""

    def __init__(self, issues: list[Issue], zone: timezone = UTC) -> None:
        self.issues = issues
        self.zone = zone
        self.queries: list[str] = []
        self.client = MagicMock()
        self.client.get_issues = AsyncMock(side_effect=self._get_issues)
        self.client.count_issues = AsyncMock(side_effect=self._count)

    def _parse(self, minute: str) -> datetime:
        return datetime.strptime(minute, MINUTE).replace(tzinfo=self.zone)

    async def _get_issues(self, query: str, *, top: int, skip: int = 0) -> list[Issue]:
        self.queries.append(query)
        if "#Unresolved" in query:
            found = [i for i in self.issues if not i.is_resolved]
        else:
            since = self._parse(query.split("updated: ")[1].split(" ..")[0])
            found = [i for i in self.issues if i.updated and i.updated >= since]
        found.sort(key=lambda i: i.updated or T0, reverse=True)
        return found[skip : skip + top]

    async def _count(self, query: str) -> int:
        start, end = query.split("updated: ")[1].split(" .. ")
        low = self._parse(start)
        high = self._parse(end) + timedelta(minutes=1)
        return sum(1 for i in self.issues if i.updated and low <= i.updated < high)


def cache_for(server: FakeYouTrack, local: timezone = UTC, **kwargs: int) -> IssueCache:
    """Create a cache that talks to ``server``."""
    return IssueCache(server.client, "DEMO", local, **kwargs)


def delta_query(since: datetime, zone: timezone = UTC) -> str:
    """Return the delta query for a cursor."""
    minute = since.astimezone(zone).strftime(MINUTE)
    return f"project: {{DEMO}} updated: {minute} .. * sort by: updated desc"


async def test_first_refresh_loads_all_unresolved_issues() -> None:
    """The initial load reads the unresolved issues, not the resolved ones."""
    server = FakeYouTrack(
        [
            make_issue("DEMO-1", T0 - timedelta(hours=2)),
            make_issue("DEMO-2", T0 - timedelta(hours=1), resolved=True),
        ]
    )
    cache = cache_for(server)

    await cache.refresh(T0)

    assert list(cache.issues) == ["DEMO-1"]
    assert server.queries == [FULL_QUERY]


async def test_second_refresh_requests_only_the_delta() -> None:
    """After the initial load a poll asks for changes since the previous poll, minus a margin."""
    server = FakeYouTrack([make_issue("DEMO-1", T0 - timedelta(hours=2))])
    cache = cache_for(server)
    await cache.refresh(T0)
    server.queries.clear()

    await cache.refresh(T0 + timedelta(minutes=5))

    assert server.queries == [delta_query(T0 - CURSOR_MARGIN)]


async def test_new_changed_and_resolved_issues_are_applied() -> None:
    """A delta adds new issues, replaces changed ones and drops resolved ones."""
    server = FakeYouTrack(
        [
            make_issue("DEMO-1", T0 - timedelta(hours=2)),
            make_issue("DEMO-2", T0 - timedelta(hours=1)),
        ]
    )
    cache = cache_for(server)
    await cache.refresh(T0)

    later = T0 + timedelta(minutes=5)
    server.issues = [
        make_issue("DEMO-1", later - timedelta(minutes=1), resolved=True),
        make_issue("DEMO-2", later - timedelta(minutes=2)),
        make_issue("DEMO-3", later - timedelta(minutes=3)),
    ]
    await cache.refresh(later)

    assert sorted(cache.issues) == ["DEMO-2", "DEMO-3"]
    assert cache.issues["DEMO-2"].updated == later - timedelta(minutes=2)


async def test_deleted_and_moved_issues_leave_with_the_full_reconciliation() -> None:
    """Deltas cannot see removals; every nth refresh reloads everything."""
    server = FakeYouTrack(
        [
            make_issue("DEMO-1", T0 - timedelta(hours=2)),
            make_issue("DEMO-2", T0 - timedelta(hours=1)),
        ]
    )
    cache = cache_for(server, reconcile_every=2)
    await cache.refresh(T0)
    server.issues = [server.issues[1]]  # DEMO-1 was deleted or moved to another project

    await cache.refresh(T0 + timedelta(minutes=5))
    assert sorted(cache.issues) == ["DEMO-1", "DEMO-2"]
    await cache.refresh(T0 + timedelta(minutes=10))
    assert sorted(cache.issues) == ["DEMO-1", "DEMO-2"]
    server.queries.clear()
    await cache.refresh(T0 + timedelta(minutes=15))

    assert list(cache.issues) == ["DEMO-2"]
    assert server.queries == [FULL_QUERY]


async def test_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    """All pages are read."""
    monkeypatch.setattr(issue_cache, "PAGE_SIZE", 2)
    server = FakeYouTrack([make_issue(f"DEMO-{n}", T0 - timedelta(hours=n)) for n in range(1, 6)])
    cache = cache_for(server)

    await cache.refresh(T0)

    assert len(cache.issues) == 5
    assert server.client.get_issues.await_count == 3


async def test_limit_keeps_the_most_recently_updated(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Huge projects are truncated with a warning."""
    monkeypatch.setattr(issue_cache, "PAGE_SIZE", 2)
    monkeypatch.setattr(issue_cache, "MAX_ISSUES", 4)
    server = FakeYouTrack([make_issue(f"DEMO-{n}", T0 - timedelta(hours=n)) for n in range(1, 8)])
    cache = cache_for(server)

    await cache.refresh(T0)

    assert sorted(cache.issues) == ["DEMO-1", "DEMO-2", "DEMO-3", "DEMO-4"]
    assert "at least 4 unresolved issues" in caplog.text


async def test_detects_local_zone_of_the_query_language() -> None:
    """When YouTrack reads timestamps in the local zone, deltas use that zone."""
    server = FakeYouTrack([make_issue("DEMO-1", T0 - timedelta(hours=2))], zone=CEST)
    cache = cache_for(server, local=CEST)
    await cache.refresh(T0)
    server.queries.clear()

    await cache.refresh(T0 + timedelta(minutes=5))

    assert server.queries == [delta_query(T0 - CURSOR_MARGIN, CEST)]


async def test_unknown_zone_falls_back_to_full_loads() -> None:
    """If no candidate zone fits, every poll is a full load, which stays correct."""
    other = timezone(timedelta(hours=-5))
    server = FakeYouTrack([make_issue("DEMO-1", T0 - timedelta(hours=2))], zone=other)
    cache = cache_for(server, local=CEST)
    await cache.refresh(T0)
    server.queries.clear()

    await cache.refresh(T0 + timedelta(minutes=5))

    assert server.queries == [FULL_QUERY]


async def test_empty_project_is_reloaded_until_it_has_issues() -> None:
    """Without an issue there is nothing to calibrate against, so the next poll loads again."""
    server = FakeYouTrack([])
    cache = cache_for(server)
    await cache.refresh(T0)
    assert cache.issues == {}

    server.issues = [make_issue("DEMO-1", T0)]
    await cache.refresh(T0 + timedelta(minutes=5))

    assert list(cache.issues) == ["DEMO-1"]
    assert server.client.count_issues.await_count == 1
