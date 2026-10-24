"""Typed models for YouTrack API payloads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .const import DUE_DATE_FIELD_NAMES
from .exceptions import YouTrackResponseError


def _timestamp(value: Any) -> datetime | None:
    """Convert epoch milliseconds to an aware datetime."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise YouTrackResponseError(f"Expected epoch milliseconds, got {value!r}")
    return datetime.fromtimestamp(value / 1000, UTC)


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise YouTrackResponseError(f"Missing or invalid '{key}' in payload")
    return value


def _field_value_name(value: Any) -> str | None:
    """Return a readable name for a custom field value."""
    if isinstance(value, dict):
        for key in ("login", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    return None


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Version information of a YouTrack server."""

    version: str
    build: str | None


@dataclass(frozen=True, slots=True)
class User:
    """The user a token belongs to."""

    login: str
    name: str | None


@dataclass(frozen=True, slots=True)
class Project:
    """A YouTrack project."""

    id: str
    name: str
    short_name: str
    archived: bool

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Project:
        """Build a project from an API payload."""
        return cls(
            id=_require_str(payload, "id"),
            name=_require_str(payload, "name"),
            short_name=_require_str(payload, "shortName"),
            archived=bool(payload.get("archived", False)),
        )


@dataclass(frozen=True, slots=True)
class ProjectField:
    """A custom field that is attached to a project."""

    name: str
    field_type: str

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> ProjectField:
        """Build a project field from an API payload."""
        field = payload.get("field")
        if not isinstance(field, dict):
            raise YouTrackResponseError("Missing 'field' in project custom field")
        field_type = field.get("fieldType")
        type_id = field_type.get("id") if isinstance(field_type, dict) else None
        return cls(name=_require_str(field, "name"), field_type=type_id or "")


def _due_date(fields: dict[str, Any]) -> datetime | None:
    """Return the value of the due date field, which holds epoch milliseconds."""
    for name, value in fields.items():
        if name.lower() in DUE_DATE_FIELD_NAMES and isinstance(value, int | float):
            return _timestamp(value)
    return None


@dataclass(frozen=True, slots=True)
class Issue:
    """A YouTrack issue reduced to the fields this integration uses."""

    id: str
    summary: str
    created: datetime | None
    updated: datetime | None
    resolved: datetime | None
    project: str | None
    reporter: str | None
    state: str | None
    priority: str | None
    type: str | None
    assignee: str | None
    due: datetime | None = None

    @property
    def is_resolved(self) -> bool:
        """Return True if the issue carries a resolution date."""
        return self.resolved is not None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Issue:
        """Build an issue from an API payload."""
        fields: dict[str, Any] = {}
        for field in payload.get("customFields") or []:
            if isinstance(field, dict) and isinstance(field.get("name"), str):
                fields[field["name"]] = field.get("value")
        project = payload.get("project")
        reporter = payload.get("reporter")
        return cls(
            id=_require_str(payload, "idReadable"),
            summary=_require_str(payload, "summary"),
            created=_timestamp(payload.get("created")),
            updated=_timestamp(payload.get("updated")),
            resolved=_timestamp(payload.get("resolved")),
            project=project.get("shortName") if isinstance(project, dict) else None,
            reporter=reporter.get("login") if isinstance(reporter, dict) else None,
            state=_field_value_name(fields.get("State")),
            priority=_field_value_name(fields.get("Priority")),
            type=_field_value_name(fields.get("Type")),
            assignee=_field_value_name(fields.get("Assignee")),
            due=_due_date(fields),
        )


class ActivityKind(StrEnum):
    """What happened to an issue."""

    CREATED = "created"
    UPDATED = "updated"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    COMMENTED = "commented"


_CUSTOM_FIELD_MEMBER = re.compile(r"^__CUSTOM_FIELD__(?P<name>.+)_\d+$")


def _value_names(values: Any) -> str | None:
    """Join the names of the values of a custom field change."""
    if not isinstance(values, list):
        return None
    names = [name for value in values if (name := _field_value_name(value))]
    return ", ".join(names) or None


@dataclass(frozen=True, slots=True)
class Activity:
    """One entry of the YouTrack activity feed, reduced to what events need."""

    id: str
    timestamp: datetime
    kind: ActivityKind
    issue_id: str
    summary: str
    project: str | None
    author: str | None
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Activity | None:
        """Build an activity; return None for entries that are not issue events."""
        kind = cls._kind(payload)
        target = payload.get("target")
        if kind is None or not isinstance(target, dict):
            return None
        issue = target.get("issue") if kind is ActivityKind.COMMENTED else target
        timestamp = _timestamp(payload.get("timestamp"))
        if not isinstance(issue, dict) or timestamp is None:
            return None
        project = issue.get("project")
        author = payload.get("author")
        field = None
        if kind is ActivityKind.UPDATED:
            member = _CUSTOM_FIELD_MEMBER.match(str(payload.get("targetMember")))
            field = member["name"] if member else None
        return cls(
            id=_require_str(payload, "id"),
            timestamp=timestamp,
            kind=kind,
            issue_id=_require_str(issue, "idReadable"),
            summary=str(issue.get("summary") or ""),
            project=project.get("shortName") if isinstance(project, dict) else None,
            author=author.get("login") if isinstance(author, dict) else None,
            field=field,
            old_value=_value_names(payload.get("removed")) if field else None,
            new_value=_value_names(payload.get("added")) if field else None,
        )

    @staticmethod
    def _kind(payload: dict[str, Any]) -> ActivityKind | None:
        match payload.get("$type"):
            case "IssueCreatedActivityItem":
                return ActivityKind.CREATED
            case "IssueResolvedActivityItem":
                if payload.get("added"):
                    return ActivityKind.RESOLVED
                return ActivityKind.REOPENED if payload.get("removed") else None
            case "CommentActivityItem":
                return ActivityKind.COMMENTED if payload.get("added") else None
            case "CustomFieldActivityItem":
                return ActivityKind.UPDATED
        return None


@dataclass(frozen=True, slots=True)
class ActivityPage:
    """A page of the activity feed; ``size`` counts entries before unusable ones were dropped."""

    items: list[Activity]
    size: int


@dataclass(frozen=True, slots=True)
class Article:
    """A knowledge base article. ``content`` is Markdown and only present when requested."""

    id: str
    readable_id: str
    summary: str
    created: datetime | None
    updated: datetime | None
    project: str | None
    author: str | None
    parent_id: str | None
    has_children: bool
    content: str | None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Article:
        """Build an article from an API payload."""
        project = payload.get("project")
        author = payload.get("reporter")
        parent = payload.get("parentArticle")
        content = payload.get("content")
        return cls(
            id=_require_str(payload, "id"),
            readable_id=_require_str(payload, "idReadable"),
            summary=_require_str(payload, "summary"),
            created=_timestamp(payload.get("created")),
            updated=_timestamp(payload.get("updated")),
            project=project.get("shortName") if isinstance(project, dict) else None,
            author=author.get("login") if isinstance(author, dict) else None,
            parent_id=parent.get("idReadable") if isinstance(parent, dict) else None,
            has_children=bool(payload.get("hasChildren", False)),
            content=content if isinstance(content, str) else None,
        )
