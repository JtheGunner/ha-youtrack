"""To-do lists with the issues of a project and of its custom queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.components.todo.entity import TodoItem, TodoListEntity

from .const import (
    CONF_REOPENED_STATE,
    CONF_RESOLVED_STATE,
    CONF_TODO_CREATE,
    DEFAULT_REOPENED_STATE,
    DEFAULT_RESOLVED_STATE,
)
from .entity import YouTrackProjectEntity, remove_stale_entities
from .services import call_api, command_value, service_error

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry
    from .models import Issue
    from .queries import CustomQuery

PARALLEL_UPDATES = 0

QUERY_KEY_PREFIX = "todo_query_"
_EPOCH = datetime.fromtimestamp(0, UTC)


def issue_to_item(issue: Issue, base_url: str) -> TodoItem:
    """Map an issue to an item: ``ID summary`` plus state, priority, assignee and link."""
    details = [
        part
        for part in (
            f"State: {issue.state}" if issue.state else None,
            f"Priority: {issue.priority}" if issue.priority else None,
            f"Assignee: {issue.assignee}" if issue.assignee else None,
            f"{base_url}/issue/{issue.id}",
        )
        if part
    ]
    return TodoItem(
        uid=issue.id,
        summary=f"{issue.id} {issue.summary}",
        description="\n".join(details),
        status=TodoItemStatus.COMPLETED if issue.is_resolved else TodoItemStatus.NEEDS_ACTION,
    )


def _items(issues: Iterable[Issue], base_url: str) -> list[TodoItem]:
    ordered = sorted(
        issues, key=lambda issue: issue.updated or issue.created or _EPOCH, reverse=True
    )
    return [issue_to_item(issue, base_url) for issue in ordered]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the open-issues list of every project and one list per custom query."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities(
            [
                YouTrackOpenIssuesTodo(coordinator),
                *(YouTrackQueryTodo(coordinator, query) for query in coordinator.custom_queries),
            ],
            config_subentry_id=subentry_id,
        )
        remove_stale_entities(
            hass,
            entry,
            "todo",
            f"{subentry_id}_{QUERY_KEY_PREFIX}",
            {f"{subentry_id}_{QUERY_KEY_PREFIX}{query.id}" for query in coordinator.custom_queries},
        )


class YouTrackTodoBase(YouTrackProjectEntity, TodoListEntity):
    """Shared behaviour of the to-do lists: checking an item resolves the issue."""

    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Resolve or reopen the issue when the item is checked or unchecked."""
        current = next((todo for todo in self.todo_items or [] if todo.uid == item.uid), None)
        if current is None:
            raise service_error("not_found")
        if item.summary != current.summary:
            raise service_error("todo_edit_unsupported")
        if item.status == current.status:
            return
        options = self.coordinator.config_entry.options
        if item.status == TodoItemStatus.COMPLETED:
            state = options.get(CONF_RESOLVED_STATE, DEFAULT_RESOLVED_STATE)
        else:
            state = options.get(CONF_REOPENED_STATE, DEFAULT_REOPENED_STATE)
        command = f"State {command_value('state', state)}"
        entry = self.coordinator.config_entry
        issue_id = str(item.uid)
        await call_api(
            self.hass, entry, lambda: entry.runtime_data.client.run_command(issue_id, command)
        )
        await self.coordinator.async_request_refresh()


class YouTrackOpenIssuesTodo(YouTrackTodoBase):
    """The unresolved issues of a project."""

    _attr_translation_key = "open_issues"

    def __init__(self, coordinator: YouTrackProjectCoordinator) -> None:
        """Create the list."""
        super().__init__(coordinator, "todo")
        if coordinator.config_entry.options.get(CONF_TODO_CREATE, False):
            self._attr_supported_features |= (
                TodoListEntityFeature.CREATE_TODO_ITEM
                | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
            )

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create an issue from a new item."""
        entry = self.coordinator.config_entry
        client = entry.runtime_data.client
        summary = (item.summary or "").strip()
        if not summary:
            raise service_error("empty_summary")
        await call_api(
            self.hass,
            entry,
            lambda: client.create_issue(self.coordinator.project_id, summary, item.description),
        )
        await self.coordinator.async_request_refresh()

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the unresolved issues, most recently updated first."""
        base_url = self.coordinator.config_entry.data["url"]
        return _items(self.coordinator.data.open_issues.values(), base_url)


class YouTrackQueryTodo(YouTrackTodoBase):
    """The first issues of a custom query."""

    def __init__(self, coordinator: YouTrackProjectCoordinator, query: CustomQuery) -> None:
        """Create the list."""
        super().__init__(coordinator, f"{QUERY_KEY_PREFIX}{query.id}")
        self._query = query
        self._attr_name = query.name

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the issues that the query lists."""
        result = self.coordinator.data.queries.get(self._query.id)
        base_url = self.coordinator.config_entry.data["url"]
        return _items(result.issues if result else (), base_url)
