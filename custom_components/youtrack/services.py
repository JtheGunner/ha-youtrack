"""Service actions that change data in YouTrack."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import probatio
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .exceptions import (
    YouTrackAuthError,
    YouTrackBadRequestError,
    YouTrackError,
    YouTrackNotFoundError,
    YouTrackPermissionError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry

ATTR_DEVICE_ID = "device_id"
ATTR_ISSUE_ID = "issue_id"
ATTR_SUMMARY = "summary"
ATTR_DESCRIPTION = "description"
ATTR_TYPE = "type"
ATTR_PRIORITY = "priority"
ATTR_ASSIGNEE = "assignee"
ATTR_TEXT = "text"
ATTR_STATE = "state"
ATTR_FIELD = "field"
ATTR_DURATION = "duration"
ATTR_DATE = "date"
ATTR_WORK_TYPE = "work_type"

SERVICE_CREATE_ISSUE = "create_issue"
SERVICE_ADD_COMMENT = "add_comment"
SERVICE_TRANSITION = "transition"
SERVICE_ASSIGN = "assign"
SERVICE_LOG_WORK = "log_work"

UNASSIGNED = "Unassigned"
DEFAULT_STATE_FIELD = "State"
_ISSUE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
_DURATION = re.compile(r"^(\d+\s*[wdhm]\s*)+$")
# YouTrack commands have no quoting; anything that could start a second command is refused.
_COMMAND_VALUE = re.compile(r"^[^\s{}\"\\][^{}\"\\\r\n]*$")

_DEVICE_IDS = probatio.Schema(probatio.All(cv.ensure_list, [cv.string]))
_ISSUE = {
    probatio.Required(ATTR_ISSUE_ID): cv.string,
    probatio.Optional(ATTR_DEVICE_ID): _DEVICE_IDS,
}

CREATE_ISSUE_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): _DEVICE_IDS,
        probatio.Required(ATTR_SUMMARY): cv.string,
        probatio.Optional(ATTR_DESCRIPTION): cv.string,
        probatio.Optional(ATTR_TYPE): cv.string,
        probatio.Optional(ATTR_PRIORITY): cv.string,
        probatio.Optional(ATTR_ASSIGNEE): cv.string,
    }
)
ADD_COMMENT_SCHEMA = probatio.Schema({**_ISSUE, probatio.Required(ATTR_TEXT): cv.string})
TRANSITION_SCHEMA = probatio.Schema(
    {
        **_ISSUE,
        probatio.Required(ATTR_STATE): cv.string,
        probatio.Optional(ATTR_FIELD, default=DEFAULT_STATE_FIELD): cv.string,
    }
)
ASSIGN_SCHEMA = probatio.Schema({**_ISSUE, probatio.Required(ATTR_ASSIGNEE): cv.string})
LOG_WORK_SCHEMA = probatio.Schema(
    {
        **_ISSUE,
        probatio.Required(ATTR_DURATION): cv.string,
        probatio.Optional(ATTR_TEXT): cv.string,
        probatio.Optional(ATTR_DATE): cv.datetime,
        probatio.Optional(ATTR_WORK_TYPE): cv.string,
    }
)


def service_error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
    )


def command_value(name: str, value: str) -> str:
    """Return ``value`` if it is safe inside a YouTrack command."""
    value = value.strip()
    if not _COMMAND_VALUE.match(value):
        raise service_error("invalid_command_value", field=name)
    return value


def _issue_id(value: str) -> str:
    issue_id = value.strip().upper()
    if not _ISSUE_ID.match(issue_id):
        raise service_error("invalid_issue_id", issue_id=value)
    return issue_id


def _project_coordinator(hass: HomeAssistant, device_ids: list[str]) -> YouTrackProjectCoordinator:
    """Return the coordinator of the project device that was selected."""
    if len(device_ids) != 1:
        raise service_error("select_one_project")
    device = dr.async_get(hass).async_get(device_ids[0])
    if device is not None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            for coordinator in entry.runtime_data.coordinators.values():
                if (DOMAIN, f"{entry.entry_id}_{coordinator.project_id}") in device.identifiers:
                    return coordinator  # type: ignore[no-any-return]
    raise service_error("unknown_project")


def _entry(hass: HomeAssistant, device_ids: list[str]) -> YouTrackConfigEntry:
    """Return the instance an issue action applies to."""
    if device_ids:
        return _project_coordinator(hass, device_ids).config_entry
    entries: list[YouTrackConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if len(entries) == 1:
        return entries[0]
    raise service_error("select_instance" if entries else "not_loaded")


async def call_api[T](
    hass: HomeAssistant, entry: YouTrackConfigEntry, operation: Callable[[], Awaitable[T]]
) -> T:
    """Run an API call and turn failures into errors Home Assistant can show."""
    try:
        return await operation()
    except YouTrackAuthError as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
    except YouTrackPermissionError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="permission_denied"
        ) from err
    except YouTrackNotFoundError as err:
        raise service_error("not_found") from err
    except YouTrackBadRequestError as err:
        raise service_error("rejected", reason=str(err)) from err
    except YouTrackError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="request_failed",
            translation_placeholders={"error": str(err)},
        ) from err


async def _refresh(entry: YouTrackConfigEntry) -> None:
    """Update the entities soon after a change."""
    for coordinator in entry.runtime_data.coordinators.values():
        await coordinator.async_request_refresh()


async def _create_issue(call: ServiceCall) -> ServiceResponse:
    hass, data = call.hass, call.data
    coordinator = _project_coordinator(hass, data[ATTR_DEVICE_ID])
    entry = coordinator.config_entry
    client = entry.runtime_data.client
    commands = [
        f"{field} {command_value(field, data[key])}"
        for key, field in (
            (ATTR_TYPE, "Type"),
            (ATTR_PRIORITY, "Priority"),
            (ATTR_ASSIGNEE, "Assignee"),
        )
        if key in data
    ]
    issue_id = await call_api(
        hass,
        entry,
        lambda: client.create_issue(
            coordinator.project_id, data[ATTR_SUMMARY].strip(), data.get(ATTR_DESCRIPTION)
        ),
    )
    if commands:
        try:
            await call_api(hass, entry, lambda: client.run_command(issue_id, " ".join(commands)))
        except HomeAssistantError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="created_without_fields",
                translation_placeholders={"issue_id": issue_id, "error": str(err)},
            ) from err
    await _refresh(entry)
    return {"issue_id": issue_id, "url": f"{entry.data['url']}/issue/{issue_id}"}


async def _add_comment(call: ServiceCall) -> None:
    hass, data = call.hass, call.data
    entry = _entry(hass, data.get(ATTR_DEVICE_ID, []))
    issue_id, text = _issue_id(data[ATTR_ISSUE_ID]), data[ATTR_TEXT]
    await call_api(hass, entry, lambda: entry.runtime_data.client.add_comment(issue_id, text))
    await _refresh(entry)


async def _transition(call: ServiceCall) -> None:
    hass, data = call.hass, call.data
    entry = _entry(hass, data.get(ATTR_DEVICE_ID, []))
    issue_id = _issue_id(data[ATTR_ISSUE_ID])
    command = (
        f"{command_value('field', data[ATTR_FIELD])} {command_value('state', data[ATTR_STATE])}"
    )
    await call_api(hass, entry, lambda: entry.runtime_data.client.run_command(issue_id, command))
    await _refresh(entry)


async def _assign(call: ServiceCall) -> None:
    hass, data = call.hass, call.data
    entry = _entry(hass, data.get(ATTR_DEVICE_ID, []))
    issue_id = _issue_id(data[ATTR_ISSUE_ID])
    command = f"Assignee {command_value(ATTR_ASSIGNEE, data[ATTR_ASSIGNEE])}"
    await call_api(hass, entry, lambda: entry.runtime_data.client.run_command(issue_id, command))
    await _refresh(entry)


async def _log_work(call: ServiceCall) -> None:
    hass, data = call.hass, call.data
    entry = _entry(hass, data.get(ATTR_DEVICE_ID, []))
    issue_id = _issue_id(data[ATTR_ISSUE_ID])
    duration = " ".join(data[ATTR_DURATION].lower().split())
    if not _DURATION.match(duration):
        raise service_error("invalid_duration", duration=data[ATTR_DURATION])
    date: datetime | None = data.get(ATTR_DATE)
    await call_api(
        hass,
        entry,
        lambda: entry.runtime_data.client.log_work(
            issue_id,
            duration,
            text=data.get(ATTR_TEXT),
            date=date,
            work_type=data.get(ATTR_WORK_TYPE),
        ),
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_ISSUE,
        _create_issue,
        schema=CREATE_ISSUE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    for name, handler, schema in (
        (SERVICE_ADD_COMMENT, _add_comment, ADD_COMMENT_SCHEMA),
        (SERVICE_TRANSITION, _transition, TRANSITION_SCHEMA),
        (SERVICE_ASSIGN, _assign, ASSIGN_SCHEMA),
        (SERVICE_LOG_WORK, _log_work, LOG_WORK_SCHEMA),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=schema)
