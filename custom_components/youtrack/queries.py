"""YouTrack search queries behind the project count sensors."""

from __future__ import annotations

from dataclasses import dataclass
from string import Template
from typing import TYPE_CHECKING, Any

from .const import (
    CONF_QUERY_ID,
    CONF_QUERY_NAME,
    CONF_QUERY_TEXT,
    CONF_QUERY_TOP,
    DEFAULT_QUERY_TOP,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

ASSIGNEE_FIELD = "Assignee"
PRIORITY_FIELD = "Priority"
DUE_DATE_FIELD_NAMES = frozenset({"due date", "due"})


@dataclass(frozen=True, slots=True)
class CountQuery:
    """A count that is polled for every project.

    ``requires`` names a custom field that the project must have, or "due" for the
    project's due date field. Counts whose requirement is not met are skipped.
    """

    key: str
    template: Template
    requires: str | None = None

    def build(self, project: str, due_field: str | None) -> str:
        """Return the query for ``project``; ``$due`` is the due date field name."""
        return self.template.safe_substitute(project=project, due=due_field or "")


def _query(key: str, template: str, requires: str | None = None) -> CountQuery:
    return CountQuery(key, Template(template), requires)


COUNT_QUERIES: tuple[CountQuery, ...] = (
    _query("total", "project: {$project}"),
    _query("open", "project: {$project} #Unresolved"),
    _query("resolved", "project: {$project} #Resolved"),
    _query("unassigned", "project: {$project} #Unresolved Assignee: Unassigned", ASSIGNEE_FIELD),
    _query("overdue", "project: {$project} #Unresolved {$due}: * .. {minus 1d}", "due"),
    _query("created_7d", "project: {$project} created: {minus 7d} .. *"),
    _query("resolved_7d", "project: {$project} resolved date: {minus 7d} .. *"),
    _query(
        "critical_open",
        "project: {$project} #Unresolved Priority: {Show-stopper}, Critical",
        PRIORITY_FIELD,
    ),
)


@dataclass(frozen=True, slots=True)
class CustomQuery:
    """A named search the user added to a project; it becomes one sensor."""

    id: str
    name: str
    text: str
    top: int

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> CustomQuery:
        """Build a query from its stored form."""
        return cls(
            id=data[CONF_QUERY_ID],
            name=data[CONF_QUERY_NAME],
            text=data[CONF_QUERY_TEXT],
            top=data.get(CONF_QUERY_TOP, DEFAULT_QUERY_TOP),
        )

    def build(self, project: str) -> str:
        """Return the query restricted to ``project``."""
        return scoped_query(project, self.text)


def scoped_query(project: str, text: str) -> str:
    """Restrict a user query to one project.

    ``and`` is required: ``project: {X} (...)`` does not parse, and without the group an ``or``
    in the user query would escape the project filter.
    """
    return f"project: {{{project}}} and ({text})"
