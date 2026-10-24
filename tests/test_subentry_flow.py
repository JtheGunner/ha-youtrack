"""Tests for the project subentry flow."""

from types import MappingProxyType
from unittest.mock import MagicMock

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import CONF_PROJECT_ID, CONF_PROJECT_KEY, SUBENTRY_PROJECT
from custom_components.youtrack.exceptions import YouTrackConnectionError
from custom_components.youtrack.models import Project


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _start(hass: HomeAssistant, entry: MockConfigEntry):  # type: ignore[no-untyped-def]
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROJECT), context={"source": config_entries.SOURCE_USER}
    )


async def test_add_project(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Archived projects are not offered; the chosen project becomes a subentry."""
    await _setup(hass, mock_config_entry)

    result = await _start(hass, mock_config_entry)
    assert result["type"] is FlowResultType.FORM
    offered = result["data_schema"].schema[CONF_PROJECT_ID].config["options"]
    assert [option["label"] for option in offered] == ["Demo Project (DEMO)", "Operations (OPS)"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_PROJECT_ID: "0-1"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = mock_config_entry.subentries.values()
    assert subentry.title == "Demo Project"
    assert subentry.unique_id == "0-1"
    assert dict(subentry.data) == {CONF_PROJECT_ID: "0-1", CONF_PROJECT_KEY: "DEMO"}
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_client.get_current_user.await_count == 2  # entry reloaded after the change


async def test_added_projects_are_not_offered_again(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A project that is already a subentry disappears from the list."""
    mock_config_entry.subentries = {
        "sub1": config_entries.ConfigSubentry(
            data=MappingProxyType({CONF_PROJECT_ID: "0-1", CONF_PROJECT_KEY: "DEMO"}),
            subentry_id="sub1",
            subentry_type=SUBENTRY_PROJECT,
            title="Demo Project",
            unique_id="0-1",
        )
    }
    await _setup(hass, mock_config_entry)

    result = await _start(hass, mock_config_entry)

    offered = result["data_schema"].schema[CONF_PROJECT_ID].config["options"]
    assert [option["value"] for option in offered] == ["0-2"]


async def test_abort_when_no_projects_left(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Without selectable projects the flow aborts with a clear reason."""
    mock_client.get_projects.return_value = [
        Project(id="0-3", name="Old", short_name="OLD", archived=True)
    ]
    await _setup(hass, mock_config_entry)

    result = await _start(hass, mock_config_entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_projects_available"


async def test_abort_when_api_fails(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A failing project request aborts the flow."""
    await _setup(hass, mock_config_entry)
    mock_client.get_projects.side_effect = YouTrackConnectionError("down")

    result = await _start(hass, mock_config_entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_abort_when_entry_not_loaded(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The flow needs a working connection."""
    mock_client.get_current_user.side_effect = YouTrackConnectionError("down")
    await _setup(hass, mock_config_entry)

    result = await _start(hass, mock_config_entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"


async def test_abort_when_project_vanished(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A project that disappears between form and submit is reported."""
    await _setup(hass, mock_config_entry)
    result = await _start(hass, mock_config_entry)
    mock_client.get_projects.return_value = [
        Project(id="0-2", name="Operations", short_name="OPS", archived=False)
    ]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_PROJECT_ID: "0-1"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "project_unavailable"
