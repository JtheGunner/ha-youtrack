"""Tests for the to-do lists."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from unittest.mock import MagicMock

from homeassistant.components.todo.const import DOMAIN as TODO_DOMAIN
from homeassistant.components.todo.const import TodoServices
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.models import Issue
from custom_components.youtrack.todo import issue_to_item

from .conftest import URL
from .test_query_sensors import QUERY, _entry

OPEN_LIST = "todo.demo_project_open_issues"
QUERY_LIST = "todo.demo_project_my_issues"


def _issue(key: str, stamp: int, *, resolved: bool = False, assignee: str | None = None) -> Issue:
    when = datetime.fromtimestamp(stamp, UTC)
    return Issue(
        id=key,
        summary=f"Summary {key}",
        created=when,
        updated=when,
        resolved=when if resolved else None,
        project="DEMO",
        reporter=None,
        state="Open",
        priority="Major",
        type=None,
        assignee=assignee,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _items(hass: HomeAssistant, entity_id: str) -> list[dict[str, str]]:
    response = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    return cast("list[dict[str, str]]", response[entity_id]["items"])  # type: ignore[index,call-overload]


def test_item_mapping() -> None:
    """Summary, status and description follow the issue."""
    item = issue_to_item(_issue("DEMO-1", 100, assignee="example.user"), URL)

    assert item.uid == "DEMO-1"
    assert item.summary == "DEMO-1 Summary DEMO-1"
    assert item.status == "needs_action"
    assert item.description == (
        f"State: Open\nPriority: Major\nAssignee: example.user\n{URL}/issue/DEMO-1"
    )


def test_resolved_issue_is_completed_and_sparse_issue_has_only_a_link() -> None:
    """A resolved issue is completed; missing fields are left out of the description."""
    issue = replace(_issue("DEMO-2", 100, resolved=True), state=None, priority=None)

    item = issue_to_item(issue, URL)

    assert item.status == "completed"
    assert item.description == f"{URL}/issue/DEMO-2"


async def test_open_issues_list_is_sorted_by_last_update(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The native list shows the cached unresolved issues, newest update first."""
    mock_client.get_issues.return_value = [
        _issue("DEMO-1", 100),
        _issue("DEMO-3", 300),
        _issue("DEMO-2", 200),
    ]
    await _setup(hass, mock_project_entry)

    items = await _items(hass, OPEN_LIST)

    assert [item["uid"] for item in items] == ["DEMO-3", "DEMO-2", "DEMO-1"]
    assert items[0]["summary"] == "DEMO-3 Summary DEMO-3"
    state = hass.states.get(OPEN_LIST)
    assert state is not None
    assert state.state == "3"


async def test_list_follows_the_cache_on_refresh(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A refresh with a changed issue set updates the list."""
    mock_client.get_issues.return_value = [_issue("DEMO-1", 100)]
    await _setup(hass, mock_project_entry)
    mock_client.get_issues.return_value = [_issue("DEMO-1", 100), _issue("DEMO-2", 200)]
    coordinator = mock_project_entry.runtime_data.coordinators["sub-demo"]
    coordinator._cache._deltas_since_full = 99  # force a full reload
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert [item["uid"] for item in await _items(hass, OPEN_LIST)] == ["DEMO-2", "DEMO-1"]


async def test_query_gets_its_own_list_that_disappears_with_it(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A custom query has a list; removing the query removes it."""
    mock_client.get_issues.return_value = [_issue("DEMO-9", 900, resolved=True)]
    entry = _entry([QUERY])
    await _setup(hass, entry)

    (item,) = await _items(hass, QUERY_LIST)
    assert (item["uid"], item["status"]) == ("DEMO-9", "completed")

    hass.config_entries.async_update_subentry(
        entry,
        entry.subentries["sub-demo"],
        data={**entry.subentries["sub-demo"].data, "queries": []},
    )
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(QUERY_LIST) is None
    assert registry.async_get(OPEN_LIST) is not None
