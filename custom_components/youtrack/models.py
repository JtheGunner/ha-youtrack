"""Typed models for YouTrack API payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
        )
