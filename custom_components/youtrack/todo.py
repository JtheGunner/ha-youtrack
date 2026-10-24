"""To-do lists with the issues of a project and of its custom queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.components.todo.const import TodoItemStatus
from homeassistant.components.todo.entity import TodoItem, TodoListEntity

from .entity import YouTrackProjectEntity, remove_stale_entities

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


class YouTrackOpenIssuesTodo(YouTrackProjectEntity, TodoListEntity):
    """The unresolved issues of a project."""

    _attr_translation_key = "open_issues"

    def __init__(self, coordinator: YouTrackProjectCoordinator) -> None:
        """Create the list."""
        super().__init__(coordinator, "todo")

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the unresolved issues, most recently updated first."""
        base_url = self.coordinator.config_entry.data["url"]
        return _items(self.coordinator.data.open_issues.values(), base_url)


class YouTrackQueryTodo(YouTrackProjectEntity, TodoListEntity):
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
