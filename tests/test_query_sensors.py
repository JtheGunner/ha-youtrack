"""Tests for the custom query sensors and the flow that manages them."""

import logging
from unittest.mock import MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_QUERIES,
    CONF_QUERY_ID,
    CONF_QUERY_NAME,
    CONF_QUERY_TEXT,
    CONF_QUERY_TOP,
    DOMAIN,
    SUBENTRY_PROJECT,
)
from custom_components.youtrack.exceptions import (
    YouTrackConnectionError,
    YouTrackInvalidQueryError,
)

from .conftest import TOKEN, URL

ENTITY = "sensor.demo_project_my_issues"
QUERY = {
    CONF_QUERY_ID: "q1",
    CONF_QUERY_NAME: "My issues",
    CONF_QUERY_TEXT: "for: me #Unresolved",
    CONF_QUERY_TOP: 10,
}


def _entry(queries: list[dict[str, object]]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data={"url": URL, "api_token": TOKEN, "verify_ssl": True},
        subentries_data=[
            {
                "data": {CONF_PROJECT_ID: "0-1", CONF_PROJECT_KEY: "DEMO", CONF_QUERIES: queries},
                "subentry_id": "sub-demo",
                "subentry_type": SUBENTRY_PROJECT,
                "title": "Demo Project",
                "unique_id": "0-1",
            }
        ],
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry):  # type: ignore[no-untyped-def]
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_PROJECT),
        context={"source": config_entries.SOURCE_RECONFIGURE, "subentry_id": "sub-demo"},
    )


async def _choose(hass: HomeAssistant, result, option: str):  # type: ignore[no-untyped-def]
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


def _stored(entry: MockConfigEntry) -> list[dict[str, object]]:
    return [dict(item) for item in entry.subentries["sub-demo"].data[CONF_QUERIES]]


async def test_query_sensor_state_and_attributes(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """The sensor shows the count and lists the first issues."""
    mock_client.count_issues.side_effect = None
    mock_client.count_issues.return_value = 7
    entry = _entry([QUERY])
    await _setup(hass, entry)

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "7"
    assert state.attributes["query"] == "for: me #Unresolved"
    assert state.attributes["issues"][0] == {
        "id": "DEMO-2",
        "summary": "Second issue",
        "state": "In Progress",
        "priority": "Major",
        "assignee": "example.user",
        "url": f"{URL}/issue/DEMO-2",
    }
    assert len(state.attributes["issues"]) == 2
    mock_client.get_issues.assert_any_await("project: {DEMO} and (for: me #Unresolved)", top=10)


async def test_long_summaries_are_truncated(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Attribute size stays bounded."""
    mock_client.get_issues.return_value[0] = mock_client.get_issues.return_value[0].__class__(
        **{**vars_of(mock_client.get_issues.return_value[0]), "summary": "x" * 500}
    )
    await _setup(hass, _entry([QUERY]))

    state = hass.states.get(ENTITY)
    assert state is not None
    assert len(state.attributes["issues"][0]["summary"]) == 120


def vars_of(issue: object) -> dict[str, object]:
    """Return the fields of a slotted dataclass."""
    return {name: getattr(issue, name) for name in issue.__slots__}  # type: ignore[attr-defined]


async def test_rejected_query_keeps_last_value(
    hass: HomeAssistant, mock_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A query YouTrack starts rejecting keeps the previous result and warns once."""
    entry = _entry([QUERY])
    await _setup(hass, entry)
    before = hass.states.get(ENTITY)
    assert before is not None
    coordinator = entry.runtime_data.coordinators["sub-demo"]

    mock_client.count_issues.side_effect = YouTrackInvalidQueryError("bad field")
    with caplog.at_level(logging.WARNING):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    after = hass.states.get(ENTITY)
    assert after is not None
    assert after.state == before.state
    assert caplog.text.count("rejected the custom query") == 1


async def test_query_still_computing_yields_unknown(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A count YouTrack has not computed leaves the sensor unknown."""
    mock_client.count_issues.side_effect = None
    mock_client.count_issues.return_value = None
    await _setup(hass, _entry([QUERY]))

    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state == "unknown"


async def test_add_query(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A valid query is stored and its sensor appears without a restart."""
    entry = _entry([])
    await _setup(hass, entry)
    assert hass.states.get(ENTITY) is None

    result = await _reconfigure(hass, entry)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["add_query"]
    result = await _choose(hass, result, "add_query")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_QUERY_NAME: " My issues ", CONF_QUERY_TEXT: "for: me", CONF_QUERY_TOP: 5},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    (stored,) = _stored(entry)
    assert stored[CONF_QUERY_NAME] == "My issues"
    assert stored[CONF_QUERY_TEXT] == "for: me"
    assert stored[CONF_QUERY_TOP] == 5
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ENTITY) is not None


async def test_add_query_rejected_by_youtrack(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The server's error text is shown and nothing is stored."""
    entry = _entry([])
    await _setup(hass, entry)
    result = await _choose(hass, await _reconfigure(hass, entry), "add_query")
    mock_client.count_issues.side_effect = YouTrackInvalidQueryError("Unknown field: foo")

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_QUERY_NAME: "Bad", CONF_QUERY_TEXT: "foo: bar", CONF_QUERY_TOP: 10},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_QUERY_TEXT: "invalid_query"}
    assert result["description_placeholders"] == {"error": "Unknown field: foo"}
    assert _stored(entry) == []


async def test_add_query_when_youtrack_unreachable(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A connection problem is reported instead of storing an unchecked query."""
    entry = _entry([])
    await _setup(hass, entry)
    result = await _choose(hass, await _reconfigure(hass, entry), "add_query")
    mock_client.count_issues.side_effect = YouTrackConnectionError("down")

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_QUERY_NAME: "Q", CONF_QUERY_TEXT: "x", CONF_QUERY_TOP: 10},
    )

    assert result["errors"] == {CONF_QUERY_TEXT: "cannot_validate"}
    assert _stored(entry) == []


async def test_edit_query(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Editing replaces the stored query and keeps its id (and entity)."""
    entry = _entry([QUERY])
    await _setup(hass, entry)

    result = await _reconfigure(hass, entry)
    assert result["menu_options"] == ["add_query", "edit_query", "remove_query"]
    result = await _choose(hass, result, "edit_query")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_QUERY_ID: "q1"}
    )
    assert result["step_id"] == "edit_query_details"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_QUERY_NAME: "My issues", CONF_QUERY_TEXT: "#Resolved", CONF_QUERY_TOP: 3},
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    (stored,) = _stored(entry)
    assert stored[CONF_QUERY_ID] == "q1"
    assert stored[CONF_QUERY_TEXT] == "#Resolved"
    assert stored[CONF_QUERY_TOP] == 3
    mock_client.get_issues.assert_any_await("project: {DEMO} and (#Resolved)", top=3)


async def test_edit_query_rejected(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """An edit that YouTrack rejects keeps the old query."""
    entry = _entry([QUERY])
    await _setup(hass, entry)
    result = await _choose(hass, await _reconfigure(hass, entry), "edit_query")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_QUERY_ID: "q1"}
    )
    mock_client.count_issues.side_effect = YouTrackInvalidQueryError("nope")

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_QUERY_NAME: "My issues", CONF_QUERY_TEXT: "((", CONF_QUERY_TOP: 3},
    )

    assert result["errors"] == {CONF_QUERY_TEXT: "invalid_query"}
    assert _stored(entry) == [QUERY]


async def test_remove_query_removes_sensor(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Removing a query deletes its sensor and registry entry."""
    entry = _entry([QUERY])
    await _setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get(ENTITY) is not None

    result = await _choose(hass, await _reconfigure(hass, entry), "remove_query")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_QUERIES: ["q1"]}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert _stored(entry) == []
    assert hass.states.get(ENTITY) is None
    assert registry.async_get(ENTITY) is None


async def test_reconfigure_needs_loaded_entry(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The flow cannot validate queries without a connection."""
    mock_client.get_current_user.side_effect = YouTrackConnectionError("down")
    entry = _entry([])
    await _setup(hass, entry)

    result = await _reconfigure(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"
