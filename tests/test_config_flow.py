"""Tests for the instance config flow."""

from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import (
    CONF_API_TOKEN,
    CONF_LLM_CREATE,
    CONF_REOPENED_STATE,
    CONF_RESOLVED_STATE,
    CONF_SCAN_INTERVAL,
    CONF_TODO_CREATE,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.youtrack.exceptions import (
    YouTrackAuthError,
    YouTrackConnectionError,
    YouTrackError,
    YouTrackNotFoundError,
    YouTrackPermissionError,
    YouTrackResponseError,
)

from .conftest import TOKEN, URL

USER_INPUT = {CONF_URL: "yt.example.com/", CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True}


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """A valid URL and token create an entry with the normalised URL."""
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "yt.example.com"
    assert result["data"] == {CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == URL


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (YouTrackAuthError("x"), "invalid_auth"),
        (YouTrackPermissionError("x"), "no_permission"),
        (YouTrackResponseError("x"), "invalid_response"),
        (YouTrackConnectionError("x"), "cannot_connect"),
        (YouTrackError("x"), "unknown"),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant, mock_client: MagicMock, exception: Exception, error: str
) -> None:
    """Each client error maps to a form error and the user can retry."""
    mock_client.get_current_user.side_effect = exception
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_client.get_current_user.side_effect = None
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_server_info_error(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A failing version request is reported as well."""
    mock_client.get_server_info.side_effect = YouTrackNotFoundError("x")
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["errors"] == {"base": "unknown"}


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_invalid_url(hass: HomeAssistant) -> None:
    """An unusable URL is rejected before any request is made."""
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**USER_INPUT, CONF_URL: "ftp://yt.example.com"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_aborts_for_configured_instance(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The same instance cannot be added twice, even with a differently typed URL."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await _start(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A valid new token replaces the stored one and reloads the entry."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["url"] == URL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "perm:new"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_TOKEN] == "perm:new"


async def test_reauth_with_invalid_token_shows_error(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A token that is rejected keeps the form open."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    mock_client.get_current_user.side_effect = YouTrackAuthError("x")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "perm:wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_API_TOKEN] == TOKEN


async def test_reconfigure_changes_url_and_keeps_token(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The URL (and unique ID) can change; an empty token keeps the current one."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "https://new.example.com/", CONF_VERIFY_SSL: False}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {
        CONF_URL: "https://new.example.com",
        CONF_API_TOKEN: TOKEN,
        CONF_VERIFY_SSL: False,
    }
    assert mock_config_entry.unique_id == "https://new.example.com"


async def test_reconfigure_can_replace_token(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A provided token replaces the stored one."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: URL, CONF_API_TOKEN: "perm:new", CONF_VERIFY_SSL: True},
    )

    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_API_TOKEN] == "perm:new"


async def test_reconfigure_errors(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Invalid URLs and failing connections are reported and can be retried."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "ftp://x", CONF_VERIFY_SSL: True}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}

    mock_client.get_current_user.side_effect = YouTrackConnectionError("down")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_VERIFY_SSL: True}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.get_current_user.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_VERIFY_SSL: True}
    )
    assert result["reason"] == "reconfigure_successful"


async def test_reconfigure_rejects_url_of_another_entry(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Moving an entry onto the URL of another instance is refused."""
    mock_config_entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="https://other.example.com",
        data={CONF_URL: "https://other.example.com", CONF_API_TOKEN: "t", CONF_VERIFY_SSL: True},
    )
    other.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "https://other.example.com", CONF_VERIFY_SSL: True}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _interval_default(result: ConfigFlowResult) -> int:
    schema = result["data_schema"]
    assert schema is not None
    marker = next(key for key in schema.schema if key == CONF_SCAN_INTERVAL)
    return int(marker.default())


async def test_options_flow_sets_scan_interval(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The polling interval can be changed and defaults to five minutes."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert _interval_default(result) == 5

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_SCAN_INTERVAL: 15,
        CONF_RESOLVED_STATE: "Done",
        CONF_REOPENED_STATE: "Open",
        CONF_TODO_CREATE: False,
        CONF_LLM_CREATE: False,
    }


async def test_options_flow_sets_todo_behaviour(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The states used by the to-do lists and issue creation can be changed."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 5,
            CONF_RESOLVED_STATE: " Verified ",
            CONF_REOPENED_STATE: "Submitted",
            CONF_TODO_CREATE: True,
            CONF_LLM_CREATE: True,
        },
    )

    assert mock_config_entry.options[CONF_RESOLVED_STATE] == "Verified"
    assert mock_config_entry.options[CONF_REOPENED_STATE] == "Submitted"
    assert mock_config_entry.options[CONF_TODO_CREATE] is True
    assert mock_config_entry.options[CONF_LLM_CREATE] is True


async def test_options_flow_shows_current_interval(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The form is prefilled with the stored interval."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_SCAN_INTERVAL: 30})

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert _interval_default(result) == 30
