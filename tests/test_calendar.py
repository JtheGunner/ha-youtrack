"""Tests for the due date calendar."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.youtrack.models import Issue, ProjectField

from .common import load_json_fixture
from .conftest import URL

ENTITY = "calendar.demo_project_due_dates"


def _issue(issue_id: str, due: datetime | None, *, name: str = "Due Date") -> Issue:
    payload = load_json_fixture("issues.json")[0]
    fields = [*payload["customFields"]]
    if due is not None:
        fields.append({"name": name, "value": int(due.timestamp() * 1000)})
    return Issue.from_json(
        {**payload, "idReadable": issue_id, "summary": f"Task {issue_id}", "customFields": fields}
    )


async def _setup(
    hass: HomeAssistant,
    mock_client: MagicMock,
    entry: MockConfigEntry,
    issues: list[Issue],
    *,
    due_field: bool = True,
) -> None:
    fields = list(mock_client.get_project_fields.return_value)
    if due_field:
        fields.append(ProjectField(name="Due Date", field_type="date"))
    mock_client.get_project_fields.return_value = fields
    mock_client.get_issues.return_value = issues
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _events(
    hass_client: ClientSessionGenerator, start: str, end: str
) -> list[dict[str, Any]]:
    client = await hass_client()
    response = await client.get(f"/api/calendars/{ENTITY}?start={start}&end={end}")
    assert response.status == 200
    return list(await response.json())


async def test_due_dates_become_all_day_events(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """Issues with a due date show up on that day; issues without one do not."""
    due = datetime(2026, 10, 14, tzinfo=UTC)
    await _setup(
        hass,
        mock_client,
        mock_project_entry,
        [_issue("DEMO-5", due), _issue("DEMO-6", due + timedelta(days=2)), _issue("DEMO-7", None)],
    )

    events = await _events(hass_client, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")

    assert [(event["summary"], event["start"]["date"]) for event in events] == [
        ("DEMO-5 Task DEMO-5", "2026-10-14"),
        ("DEMO-6 Task DEMO-6", "2026-10-16"),
    ]
    assert f"{URL}/issue/DEMO-5" in events[0]["description"]
    assert "State: In Progress" in events[0]["description"]


async def test_range_filter(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """Only events overlapping the requested range are returned."""
    due = datetime(2026, 10, 14, tzinfo=UTC)
    await _setup(hass, mock_client, mock_project_entry, [_issue("DEMO-5", due)])

    assert await _events(hass_client, "2026-10-16T00:00:00Z", "2026-10-20T00:00:00Z") == []
    assert len(await _events(hass_client, "2026-10-14T12:00:00Z", "2026-10-14T13:00:00Z")) == 1


async def test_state_is_the_next_due_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The entity shows the soonest issue that is due today or later."""
    freezer.move_to("2026-10-15 09:00:00+00:00")
    await _setup(
        hass,
        mock_client,
        mock_project_entry,
        [
            _issue("DEMO-1", datetime(2026, 10, 10, tzinfo=UTC)),
            _issue("DEMO-3", datetime(2026, 10, 20, tzinfo=UTC)),
            _issue("DEMO-2", datetime(2026, 10, 15, tzinfo=UTC)),
        ],
    )

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.attributes["message"] == "DEMO-2 Task DEMO-2"
    assert state.attributes["all_day"] is True
    assert date.fromisoformat(state.attributes["start_time"][:10]) == date(2026, 10, 15)


async def test_no_upcoming_issue_has_no_event(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Past due dates alone leave the calendar idle."""
    freezer.move_to("2026-10-15 09:00:00+00:00")
    await _setup(
        hass,
        mock_client,
        mock_project_entry,
        [_issue("DEMO-1", datetime(2026, 10, 10, tzinfo=UTC))],
    )

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "off"


async def test_projects_without_due_field_have_no_calendar(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The calendar is only created when the project can have due dates."""
    await _setup(hass, mock_client, mock_project_entry, [], due_field=False)

    assert hass.states.get(ENTITY) is None
    assert not hass.states.async_entity_ids("calendar")


def test_issue_due_date_is_read_from_common_field_names() -> None:
    """Both common names are recognised; non-numbers are ignored."""
    due = datetime(2026, 10, 14, tzinfo=UTC)

    assert _issue("A-1", due).due == due
    assert _issue("A-1", due, name="due").due == due
    assert _issue("A-1", due, name="Deadline").due is None
    assert (
        Issue.from_json(
            {
                **load_json_fixture("issues.json")[0],
                "customFields": [{"name": "Due Date", "value": None}],
            }
        ).due
        is None
    )
