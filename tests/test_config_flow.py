"""Tests for the instance config flow."""

from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import (
    CONF_API_TOKEN,
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
