"""Tests for the write actions."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant, ServiceResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import DOMAIN
from custom_components.youtrack.exceptions import (
    YouTrackAuthError,
    YouTrackBadRequestError,
    YouTrackConnectionError,
    YouTrackNotFoundError,
    YouTrackPermissionError,
)

from .conftest import URL


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Set up the entry and return the device id of project DEMO."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_0-1"), entry.entry_id
    )
    assert device is not None
    return device.id


async def _call(
    hass: HomeAssistant, service: str, data: dict[str, Any], *, return_response: bool = False
) -> ServiceResponse:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=return_response
    )


async def test_create_issue_returns_id_and_applies_fields(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The issue is created, type/priority/assignee are set with one command, the id is returned."""
    device_id = await _setup(hass, mock_project_entry)
    mock_client.create_issue.return_value = "DEMO-12"

    response = await _call(
        hass,
        "create_issue",
        {
            "device_id": device_id,
            "summary": " Pump is noisy ",
            "description": "Since Monday",
            "type": "Task",
            "priority": "Major",
            "assignee": "example.user",
        },
        return_response=True,
    )

    assert response == {"issue_id": "DEMO-12", "url": f"{URL}/issue/DEMO-12"}
    mock_client.create_issue.assert_awaited_once_with("0-1", "Pump is noisy", "Since Monday")
    mock_client.run_command.assert_awaited_once_with(
        "DEMO-12", "Type Task Priority Major Assignee example.user"
    )


async def test_create_issue_with_summary_only_runs_no_command(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Without extra fields there is just the creation request."""
    device_id = await _setup(hass, mock_project_entry)
    mock_client.create_issue.return_value = "DEMO-13"

    await _call(hass, "create_issue", {"device_id": device_id, "summary": "Plain"})

    mock_client.run_command.assert_not_awaited()


async def test_create_issue_reports_failed_fields_with_the_new_id(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """If only the follow-up command fails, the error says the issue exists."""
    device_id = await _setup(hass, mock_project_entry)
    mock_client.create_issue.return_value = "DEMO-14"
    mock_client.run_command.side_effect = YouTrackBadRequestError("Priority expected: Nope")

    with pytest.raises(HomeAssistantError, match="DEMO-14 was created"):
        await _call(
            hass, "create_issue", {"device_id": device_id, "summary": "S", "priority": "Nope"}
        )


@pytest.mark.parametrize("devices", [[], ["a", "b"]])
async def test_create_issue_needs_exactly_one_project(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    devices: list[str],
) -> None:
    """The project comes from the selected device."""
    await _setup(hass, mock_project_entry)

    with pytest.raises(ServiceValidationError, match="exactly one"):
        await _call(hass, "create_issue", {"device_id": devices, "summary": "S"})


async def test_unknown_device_is_refused(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A device that is not a YouTrack project is not accepted."""
    await _setup(hass, mock_project_entry)

    with pytest.raises(ServiceValidationError, match="not a loaded YouTrack project"):
        await _call(hass, "create_issue", {"device_id": "nope", "summary": "S"})


async def test_foreign_device_is_refused(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A device that exists but does not belong to a YouTrack project is not accepted."""
    await _setup(hass, mock_project_entry)
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=mock_project_entry.entry_id, identifiers={("other", "x")}
    )

    with pytest.raises(ServiceValidationError, match="not a loaded YouTrack project"):
        await _call(hass, "create_issue", {"device_id": foreign.id, "summary": "S"})


@pytest.mark.parametrize("value", ["Done {x}", 'Done "x"', "Done\nAssignee me", " ", "a\\b"])
async def test_command_values_are_validated(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry, value: str
) -> None:
    """Values that could smuggle in more commands are refused before any request."""
    await _setup(hass, mock_project_entry)

    with pytest.raises(ServiceValidationError, match="characters"):
        await _call(hass, "transition", {"issue_id": "DEMO-1", "state": value})

    mock_client.run_command.assert_not_awaited()


async def test_add_comment(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A comment goes to the issue; the single instance is picked without a device."""
    await _setup(hass, mock_project_entry)

    await _call(hass, "add_comment", {"issue_id": "demo-1", "text": "Done."})

    mock_client.add_comment.assert_awaited_once_with("DEMO-1", "Done.")


async def test_transition(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The state change is a command on the state field."""
    device_id = await _setup(hass, mock_project_entry)

    await _call(
        hass, "transition", {"issue_id": "DEMO-1", "state": "In Progress", "device_id": device_id}
    )

    mock_client.run_command.assert_awaited_once_with("DEMO-1", "State In Progress")


async def test_transition_on_other_field(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Projects whose state field has another name can say so."""
    await _setup(hass, mock_project_entry)

    await _call(hass, "transition", {"issue_id": "DEMO-1", "state": "Review", "field": "Stage"})

    mock_client.run_command.assert_awaited_once_with("DEMO-1", "Stage Review")


async def test_assign(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Assigning is a command on the assignee field."""
    await _setup(hass, mock_project_entry)

    await _call(hass, "assign", {"issue_id": "DEMO-1", "assignee": "example.user"})

    mock_client.run_command.assert_awaited_once_with("DEMO-1", "Assignee example.user")


async def test_log_work(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Work items carry duration, text, date and type."""
    await _setup(hass, mock_project_entry)

    await _call(
        hass,
        "log_work",
        {
            "issue_id": "DEMO-1",
            "duration": "1H  30m",
            "text": "Fixed it",
            "date": "2026-10-24T10:00:00+00:00",
            "work_type": "Development",
        },
    )

    mock_client.log_work.assert_awaited_once_with(
        "DEMO-1",
        "1h 30m",
        text="Fixed it",
        date=datetime(2026, 10, 24, 10, 0, tzinfo=UTC),
        work_type="Development",
    )


@pytest.mark.parametrize("duration", ["soon", "1x", ""])
async def test_log_work_rejects_bad_durations(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry, duration: str
) -> None:
    """Only YouTrack duration syntax is accepted."""
    await _setup(hass, mock_project_entry)

    with pytest.raises(ServiceValidationError, match="not a duration"):
        await _call(hass, "log_work", {"issue_id": "DEMO-1", "duration": duration})

    mock_client.log_work.assert_not_awaited()


async def test_issue_ids_are_validated(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Free text cannot end up in a URL path."""
    await _setup(hass, mock_project_entry)

    with pytest.raises(ServiceValidationError, match="not an issue ID"):
        await _call(hass, "add_comment", {"issue_id": "../admin", "text": "x"})

    mock_client.add_comment.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "exception", "message"),
    [
        (YouTrackPermissionError("403"), HomeAssistantError, "denied the request"),
        (YouTrackNotFoundError("404"), ServiceValidationError, "could not find"),
        (YouTrackBadRequestError("State expected"), ServiceValidationError, "State expected"),
        (YouTrackConnectionError("down"), HomeAssistantError, "failed: down"),
    ],
)
async def test_api_errors_become_readable_errors(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    error: Exception,
    exception: type[Exception],
    message: str,
) -> None:
    """Permission, missing issue, rejection and network problems each get a clear message."""
    await _setup(hass, mock_project_entry)
    mock_client.add_comment.side_effect = error

    with pytest.raises(exception, match=message):
        await _call(hass, "add_comment", {"issue_id": "DEMO-1", "text": "x"})


async def test_rejected_token_starts_reauthentication(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A 401 during an action asks the user for a new token."""
    await _setup(hass, mock_project_entry)
    mock_client.add_comment.side_effect = YouTrackAuthError("401")

    with pytest.raises(HomeAssistantError, match="rejected the token"):
        await _call(hass, "add_comment", {"issue_id": "DEMO-1", "text": "x"})
    await hass.async_block_till_done()

    assert any(
        flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress()
    )


async def test_two_instances_need_a_project_to_choose(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Without a device the action is ambiguous when two instances are set up."""
    await _setup(hass, mock_project_entry)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="https://other.example.com",
        data={"url": "https://other.example.com", "api_token": "t", "verify_ssl": True},
    )
    other.add_to_hass(hass)
    await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError, match="more than one YouTrack instance"):
        await _call(hass, "add_comment", {"issue_id": "DEMO-1", "text": "x"})


async def test_no_instance_loaded(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Without a loaded entry the action cannot run."""
    from custom_components.youtrack.services import async_setup_services

    async_setup_services(hass)

    with pytest.raises(ServiceValidationError) as error:
        await _call(hass, "add_comment", {"issue_id": "DEMO-1", "text": "x"})

    assert error.value.translation_key == "not_loaded"
