"""Calendar of the unresolved issues of a project that have a due date."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.util import dt as dt_util

from .entity import YouTrackProjectEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import YouTrackProjectCoordinator
    from .data import YouTrackConfigEntry
    from .models import Issue

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YouTrackConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a calendar for every project that has a due date field."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        if coordinator.due_field:
            async_add_entities([YouTrackDueCalendar(coordinator)], config_subentry_id=subentry_id)


def _due_day(issue: Issue) -> date | None:
    """Return the day an issue is due, or None if it has no due date."""
    return issue.due.date() if issue.due else None


def _midnight(day: date | datetime) -> datetime:
    """Return the start of ``day`` in the Home Assistant time zone."""
    return dt_util.start_of_local_day(day)


class YouTrackDueCalendar(YouTrackProjectEntity, CalendarEntity):
    """Shows the due dates of open issues as all-day events."""

    _attr_translation_key = "due_dates"

    def __init__(self, coordinator: YouTrackProjectCoordinator) -> None:
        """Create the calendar."""
        super().__init__(coordinator, "due_dates")

    def _events(self) -> list[CalendarEvent]:
        base_url = self.coordinator.config_entry.data["url"]
        events = []
        for issue in self.coordinator.data.open_issues.values():
            day = _due_day(issue)
            if day is None:
                continue
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
            events.append(
                CalendarEvent(
                    start=day,
                    end=day + timedelta(days=1),
                    summary=f"{issue.id} {issue.summary}",
                    description="\n".join(details),
                    uid=issue.id,
                )
            )
        return sorted(events, key=lambda event: (event.start, event.uid or ""))

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next event that is due today or later."""
        today = dt_util.now().date()
        return next((event for event in self._events() if event.end > today), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the events that overlap the requested range."""
        return [
            event
            for event in self._events()
            if _midnight(event.start) < end_date and _midnight(event.end) > start_date
        ]
