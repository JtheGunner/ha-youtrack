"""Tests for the issue activity event entity and the bus events."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.youtrack import activity
from custom_components.youtrack.models import Activity, ActivityKind, ActivityPage

from .conftest import URL

ENTITY = "event.demo_project_issue_activity"


@pytest.fixture(autouse=True)
def no_poll_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let every coordinator refresh reach the feed."""
    monkeypatch.setattr(activity, "MIN_POLL_GAP", timedelta(0))


def _activity(
    kind: ActivityKind, key: str, *, project: str = "DEMO", field: str | None = None
) -> Activity:
    return Activity(
        id=f"{kind}-{key}",
        timestamp=dt_util.utcnow() + timedelta(minutes=1, seconds=int(key)),
        kind=kind,
        issue_id=f"{project}-{key}",
        summary=f"Issue {key}",
        project=project,
        author="example.user",
        field=field,
        old_value="Open" if field else None,
        new_value="In Progress" if field else None,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _publish(client: MagicMock, *items: Activity) -> None:
    """Make the next feed requests return ``items``."""

    async def get(start, *, top, skip=0, newest_first=False):  # type: ignore[no-untyped-def]
        if newest_first:
            return ActivityPage([], 0)
        page = [a for a in items if a.timestamp >= start][skip : skip + top]
        return ActivityPage(page, len(page))

    client.get_activities.side_effect = get


async def test_no_events_for_existing_history(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Whatever happened before setup stays silent."""
    _publish(mock_client, _activity(ActivityKind.CREATED, "1"))
    events = async_capture_events(hass, "youtrack_issue_created")

    await _setup(hass, mock_project_entry)

    assert events == []
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["event_type"] is None


async def test_new_activity_fires_bus_event_and_entity(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """An issue created after setup triggers both the bus event and the event entity."""
    await _setup(hass, mock_project_entry)
    created = async_capture_events(hass, "youtrack_issue_created")
    created_issue = _activity(ActivityKind.CREATED, "5")
    _publish(mock_client, created_issue)

    await mock_project_entry.runtime_data.coordinators["sub-demo"].async_refresh()
    await hass.async_block_till_done()

    (event,) = created
    assert event.data == {
        "entry_id": mock_project_entry.entry_id,
        "issue_id": "DEMO-5",
        "summary": "Issue 5",
        "project": "DEMO",
        "author": "example.user",
        "url": f"{URL}/issue/DEMO-5",
    }
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["event_type"] == "created"
    assert state.attributes["issue_id"] == "DEMO-5"


async def test_polling_again_does_not_repeat_events(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The same activity is reported once, however often the feed is read."""
    await _setup(hass, mock_project_entry)
    commented = async_capture_events(hass, "youtrack_issue_commented")
    _publish(mock_client, _activity(ActivityKind.COMMENTED, "1"))
    coordinator = mock_project_entry.runtime_data.coordinators["sub-demo"]

    for _ in range(3):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(commented) == 1


async def test_field_change_carries_old_and_new_value(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Updates say which field changed."""
    await _setup(hass, mock_project_entry)
    updated = async_capture_events(hass, "youtrack_issue_updated")
    _publish(mock_client, _activity(ActivityKind.UPDATED, "2", field="State"))

    await mock_project_entry.runtime_data.coordinators["sub-demo"].async_refresh()

    (event,) = updated
    assert (event.data["field"], event.data["old_value"], event.data["new_value"]) == (
        "State",
        "Open",
        "In Progress",
    )


async def test_other_projects_do_not_trigger_events(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Activity in a project that is not set up is ignored."""
    await _setup(hass, mock_project_entry)
    events = async_capture_events(hass, "youtrack_issue_created")
    _publish(mock_client, _activity(ActivityKind.CREATED, "1", project="OPS"))

    await mock_project_entry.runtime_data.coordinators["sub-demo"].async_refresh()

    assert events == []
