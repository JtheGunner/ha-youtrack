"""Tests for the per-project count sensors and flags."""

import logging
from unittest.mock import MagicMock

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.exceptions import YouTrackInvalidQueryError
from custom_components.youtrack.models import ProjectField

from .conftest import fake_count

PREFIX = "sensor.demo_project_"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return None if state is None else state.state


async def test_all_counts_for_a_full_project(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Every supported count has a sensor with the value of its query."""
    await _setup(hass, mock_project_entry)

    assert _state(hass, f"{PREFIX}issues") == "42"
    assert _state(hass, f"{PREFIX}open_issues") == "10"
    assert _state(hass, f"{PREFIX}resolved_issues") == "30"
    assert _state(hass, f"{PREFIX}unassigned_issues") == "3"
    assert _state(hass, f"{PREFIX}created_in_the_last_7_days") == "5"
    assert _state(hass, f"{PREFIX}resolved_in_the_last_7_days") == "4"
    assert _state(hass, f"{PREFIX}critical_open_issues") == "2"
    # No due date field in this project: nothing to report as overdue.
    assert _state(hass, f"{PREFIX}overdue_issues") is None
    assert _state(hass, "binary_sensor.demo_project_has_overdue_issues") is None

    queried = {call.args[0] for call in mock_client.count_issues.await_args_list}
    assert "project: {DEMO} #Unresolved Priority: {Show-stopper}, Critical" in queried
    assert "project: {DEMO} created: {minus 7d} .. *" in queried
    assert "project: {DEMO} resolved date: {minus 7d} .. *" in queried


async def test_project_fields_are_loaded_once(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The field list is fetched on the first poll and reused afterwards."""
    await _setup(hass, mock_project_entry)
    coordinator = next(iter(mock_project_entry.runtime_data.coordinators.values()))

    await coordinator.async_refresh()

    assert mock_client.get_project_fields.await_count == 1


async def test_overdue_with_due_date_field(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A date field called Due Date enables the overdue sensor and its flag."""
    mock_client.get_project_fields.return_value = [
        ProjectField(name="Priority", field_type="enum[1]"),
        ProjectField(name="Due Date", field_type="date"),
    ]

    await _setup(hass, mock_project_entry)

    assert _state(hass, f"{PREFIX}overdue_issues") == "1"
    assert _state(hass, "binary_sensor.demo_project_has_overdue_issues") == STATE_ON
    mock_client.count_issues.assert_any_await(
        "project: {DEMO} #Unresolved {Due Date}: * .. {minus 1d}"
    )


async def test_non_date_field_called_due_is_ignored(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Only date fields count as due date fields."""
    mock_client.get_project_fields.return_value = [
        ProjectField(name="Due Date", field_type="string")
    ]

    await _setup(hass, mock_project_entry)

    assert _state(hass, f"{PREFIX}overdue_issues") is None


async def test_counts_requiring_missing_fields_are_skipped(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A project without Assignee and Priority fields gets no unassigned/critical sensors."""
    mock_client.get_project_fields.return_value = [
        ProjectField(name="State", field_type="state[1]")
    ]

    await _setup(hass, mock_project_entry)

    assert _state(hass, f"{PREFIX}issues") == "42"
    assert _state(hass, f"{PREFIX}unassigned_issues") is None
    assert _state(hass, f"{PREFIX}critical_open_issues") is None
    assert _state(hass, "binary_sensor.demo_project_has_critical_open_issues") is None


@pytest.mark.parametrize(
    ("critical", "expected"), [(0, STATE_OFF), (4, STATE_ON), (None, STATE_UNKNOWN)]
)
async def test_critical_flag(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    critical: int | None,
    expected: str,
) -> None:
    """The flag follows the critical count and is unknown while it is not computed."""

    async def counts(query: str) -> int | None:
        return critical if "Priority:" in query else await fake_count(query)

    mock_client.count_issues.side_effect = counts

    await _setup(hass, mock_project_entry)

    assert _state(hass, "binary_sensor.demo_project_has_critical_open_issues") == expected


async def test_rejected_query_only_affects_its_own_sensor(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A query YouTrack rejects (for example unknown priority names) is logged once."""

    async def counts(query: str) -> int | None:
        if "Priority:" in query:
            raise YouTrackInvalidQueryError("unknown value")
        return await fake_count(query)

    mock_client.count_issues.side_effect = counts
    caplog.set_level(logging.WARNING)

    await _setup(hass, mock_project_entry)
    coordinator = next(iter(mock_project_entry.runtime_data.coordinators.values()))
    await coordinator.async_refresh()

    assert _state(hass, f"{PREFIX}issues") == "42"
    assert _state(hass, f"{PREFIX}critical_open_issues") == STATE_UNKNOWN
    warnings = [r for r in caplog.records if "rejected the critical_open count" in r.getMessage()]
    assert len(warnings) == 1
