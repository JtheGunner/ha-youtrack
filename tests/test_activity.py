"""Tests for activity parsing, the API call and the shared feed reader."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.youtrack import activity
from custom_components.youtrack.activity import ECHO_WINDOW, ActivityFeed, drop_echoes
from custom_components.youtrack.exceptions import YouTrackResponseError
from custom_components.youtrack.models import Activity, ActivityKind, ActivityPage

from .common import load_json_fixture
from .conftest import URL as BASE
from .conftest import ClientFactory

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def activity_at(
    stamp: datetime,
    kind: ActivityKind,
    *,
    key: str = "1",
    issue: str = "DEMO-1",
    project: str = "DEMO",
) -> Activity:
    """Build an activity."""
    return Activity(
        id=f"{kind}-{issue}-{key}",
        timestamp=stamp,
        kind=kind,
        issue_id=issue,
        summary="Summary",
        project=project,
        author="example.user",
    )


def test_parse_every_kind_of_the_feed() -> None:
    """Every kind of event is understood; deletions and unknown types are dropped."""
    parsed = [Activity.from_json(item) for item in load_json_fixture("activity_feed.json")]

    assert [a.kind if a else None for a in parsed] == [
        ActivityKind.CREATED,
        ActivityKind.UPDATED,
        ActivityKind.UPDATED,
        ActivityKind.COMMENTED,
        ActivityKind.RESOLVED,
        ActivityKind.REOPENED,
        None,  # deleted comment
        None,  # unknown type
    ]
    state_change = parsed[2]
    assert state_change is not None
    assert (state_change.field, state_change.old_value, state_change.new_value) == (
        "State",
        "Open",
        "In Progress",
    )
    comment = parsed[3]
    assert comment is not None
    assert (comment.issue_id, comment.project, comment.author) == ("DEMO-1", "DEMO", "example.user")


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "1", "timestamp": 1, "$type": "IssueCreatedActivityItem"},
        {"id": "1", "timestamp": 1, "target": {}, "$type": "CommentActivityItem", "added": [{}]},
        {"id": "1", "target": {"idReadable": "A-1"}, "$type": "IssueCreatedActivityItem"},
        {
            "id": "1",
            "timestamp": 1,
            "target": {"idReadable": "A-1"},
            "$type": "IssueResolvedActivityItem",
        },
    ],
)
def test_unusable_entries_are_dropped(payload: dict[str, object]) -> None:
    """Entries without a target issue, timestamp or direction yield nothing."""
    assert Activity.from_json(payload) is None


async def test_get_activities(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """The request asks for the four categories from a start time and keeps the raw page size."""
    aioclient_mock.get(
        f"{BASE}/api/activities",
        json=load_json_fixture("activity_feed.json"),
        headers={"Content-Type": "application/json"},
    )

    page = await make_client().get_activities(T0, top=10, skip=5, newest_first=True)

    assert page.size == 8
    assert len(page.items) == 6
    query = aioclient_mock.mock_calls[0][1].query
    assert query["start"] == str(int(T0.timestamp() * 1000))
    assert (query["$top"], query["$skip"], query["reverse"]) == ("10", "5", "true")
    assert "CommentsCategory" in query["categories"]


async def test_activities_must_be_a_list(
    make_client: ClientFactory, aioclient_mock: AiohttpClientMocker
) -> None:
    """A non-list answer is a response error."""
    aioclient_mock.get(
        f"{BASE}/api/activities", json={}, headers={"Content-Type": "application/json"}
    )

    with pytest.raises(YouTrackResponseError):
        await make_client().get_activities(T0)


def test_field_changes_that_echo_a_creation_are_dropped() -> None:
    """Initial field values and the state change of a resolution are no updates of their own."""
    created = activity_at(T0, ActivityKind.CREATED)
    echo = activity_at(T0 + timedelta(milliseconds=100), ActivityKind.UPDATED)
    later = activity_at(T0 + ECHO_WINDOW + timedelta(seconds=1), ActivityKind.UPDATED, key="2")
    other_issue = activity_at(T0, ActivityKind.UPDATED, issue="DEMO-2")

    assert drop_echoes([created, echo, later, other_issue]) == [created, later, other_issue]


class FakeFeed:
    """A scripted activity feed that honours ``start`` like YouTrack."""

    def __init__(self, items: list[Activity]) -> None:
        self.items = items
        self.client = MagicMock()
        self.client.get_activities = AsyncMock(side_effect=self._get)

    async def _get(
        self, start: datetime, *, top: int, skip: int = 0, newest_first: bool = False
    ) -> ActivityPage:
        found = sorted((a for a in self.items if a.timestamp >= start), key=lambda a: a.timestamp)
        if newest_first:
            found.reverse()
        page = found[skip : skip + top]
        return ActivityPage(page, len(page))


async def test_first_poll_is_a_baseline_without_events() -> None:
    """History never becomes events."""
    old = activity_at(T0 - timedelta(hours=1), ActivityKind.CREATED)
    server = FakeFeed([old])
    feed = ActivityFeed(server.client)

    await feed.poll(T0)

    assert feed.events_since(0, "DEMO") == ([], 0)


async def test_new_events_are_delivered_once_per_project() -> None:
    """Events are filtered by project and not repeated by later polls."""
    old = activity_at(T0 - timedelta(hours=1), ActivityKind.CREATED)
    server = FakeFeed([old])
    feed = ActivityFeed(server.client)
    await feed.poll(T0)

    fresh = activity_at(T0 + timedelta(minutes=1), ActivityKind.COMMENTED, issue="DEMO-2")
    elsewhere = activity_at(
        T0 + timedelta(minutes=2), ActivityKind.CREATED, issue="OPS-1", project="OPS"
    )
    server.items += [fresh, elsewhere]
    await feed.poll(T0 + timedelta(minutes=5))
    await feed.poll(T0 + timedelta(minutes=10))  # nothing new, must not repeat

    events, sequence = feed.events_since(0, "DEMO")
    assert events == [fresh]
    assert feed.events_since(sequence, "DEMO") == ([], sequence)
    assert feed.events_since(0, "OPS")[0] == [elsewhere]


async def test_same_timestamp_is_not_reported_twice() -> None:
    """The cursor is inclusive, so items at the cursor time must be deduplicated by id."""
    server = FakeFeed([])
    feed = ActivityFeed(server.client)
    await feed.poll(T0)
    first = activity_at(T0 + timedelta(minutes=1), ActivityKind.COMMENTED, key="a")
    server.items.append(first)
    await feed.poll(T0 + timedelta(minutes=5))
    second = activity_at(T0 + timedelta(minutes=1), ActivityKind.COMMENTED, key="b")
    server.items.append(second)
    await feed.poll(T0 + timedelta(minutes=10))

    events, _ = feed.events_since(0, "DEMO")
    assert events == [first, second]


async def test_polls_in_quick_succession_share_one_request() -> None:
    """Several projects polling together cause a single feed request."""
    server = FakeFeed([])
    feed = ActivityFeed(server.client)
    await feed.poll(T0)
    await feed.poll(T0 + timedelta(minutes=5))
    calls = server.client.get_activities.await_count

    await feed.poll(T0 + timedelta(minutes=5, seconds=1))

    assert server.client.get_activities.await_count == calls


async def test_pages_are_read_until_the_feed_is_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Large bursts span several pages."""
    monkeypatch.setattr(activity, "PAGE_SIZE", 2)
    server = FakeFeed([])
    feed = ActivityFeed(server.client)
    await feed.poll(T0)
    server.items += [
        activity_at(T0 + timedelta(seconds=n), ActivityKind.COMMENTED, key=str(n))
        for n in range(1, 6)
    ]

    await feed.poll(T0 + timedelta(minutes=5))

    assert len(feed.events_since(0, "DEMO")[0]) == 5


async def test_a_burst_beyond_the_page_limit_continues_next_poll(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Remaining items follow with the next poll; nothing is lost or repeated."""
    monkeypatch.setattr(activity, "PAGE_SIZE", 2)
    monkeypatch.setattr(activity, "MAX_PAGES", 2)
    server = FakeFeed([])
    feed = ActivityFeed(server.client)
    await feed.poll(T0)
    server.items += [
        activity_at(T0 + timedelta(seconds=n), ActivityKind.COMMENTED, key=str(n))
        for n in range(1, 8)
    ]

    await feed.poll(T0 + timedelta(minutes=5))
    assert len(feed.events_since(0, "DEMO")[0]) == 4
    assert "More than 4 new activities" in caplog.text
    await feed.poll(T0 + timedelta(minutes=10))
    await feed.poll(T0 + timedelta(minutes=15))

    assert len(feed.events_since(0, "DEMO")[0]) == 7


async def test_unusable_only_pages_do_not_move_the_cursor() -> None:
    """A page of dropped entries yields no events."""
    server = FakeFeed([])
    feed = ActivityFeed(server.client)
    await feed.poll(T0)
    server.client.get_activities.side_effect = None
    server.client.get_activities.return_value = ActivityPage([], 3)

    await feed.poll(T0 + timedelta(minutes=5))

    assert feed.events_since(0, "DEMO")[0] == []


def test_field_change_without_value_lists() -> None:
    """A field change whose values are not lists still parses, without old and new value."""
    parsed = Activity.from_json(
        {
            "id": "1",
            "timestamp": 1790000000000,
            "targetMember": "__CUSTOM_FIELD__Due Date_7",
            "added": 1790000000000,
            "removed": None,
            "target": {"idReadable": "DEMO-1", "summary": "S", "project": {"shortName": "DEMO"}},
            "$type": "CustomFieldActivityItem",
        }
    )

    assert parsed is not None
    assert (parsed.field, parsed.old_value, parsed.new_value) == ("Due Date", None, None)
