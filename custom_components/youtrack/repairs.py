"""Repair issues for projects that disappeared from YouTrack."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.helpers import issue_registry as ir

from .const import CONF_PROJECT_ID, CONF_PROJECT_KEY, DOMAIN
from .exceptions import YouTrackError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from .data import YouTrackConfigEntry

_LOGGER = logging.getLogger(__name__)

PROJECT_MISSING_PREFIX = "project_missing_"


class RemoveProjectRepairFlow(ConfirmRepairFlow):
    """Ask before removing a project that no longer exists in YouTrack."""

    def __init__(self, entry_id: str, subentry_id: str) -> None:
        """Remember which project the repair is about."""
        super().__init__()
        self._entry_id = entry_id
        self._subentry_id = subentry_id

    async def async_step_confirm(self, user_input: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        """Remove the project once the user confirmed."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None and self._subentry_id in entry.subentries:
                self.hass.config_entries.async_remove_subentry(entry, self._subentry_id)
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Create the flow that fixes a repair issue."""
    assert data is not None
    return RemoveProjectRepairFlow(str(data["entry_id"]), str(data["subentry_id"]))


async def async_sync_projects(hass: HomeAssistant, entry: YouTrackConfigEntry) -> set[str]:
    """Compare the stored projects with YouTrack and return the ids of subentries to skip.

    A renamed project keeps its id, so its stored key is updated. A deleted project gets a repair
    issue and is skipped until the user removes it or it reappears.
    """
    try:
        projects = {
            project.id: project for project in await entry.runtime_data.client.get_projects()
        }
    except YouTrackError as err:
        # Not being able to list projects must not stop the integration from loading.
        _LOGGER.debug("Could not check the configured projects: %s", err)
        return set()

    missing: set[str] = set()
    for subentry in list(entry.subentries.values()):
        project = projects.get(subentry.data[CONF_PROJECT_ID])
        if project is None:
            missing.add(subentry.subentry_id)
            _raise_missing(hass, entry, subentry)
            continue
        if project.short_name != subentry.data[CONF_PROJECT_KEY]:
            _LOGGER.info(
                "Project %s was renamed to %s", subentry.data[CONF_PROJECT_KEY], project.short_name
            )
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={**subentry.data, CONF_PROJECT_KEY: project.short_name},
                title=project.name,
            )
    _clear_resolved(hass, entry, missing)
    return missing


def _raise_missing(
    hass: HomeAssistant, entry: YouTrackConfigEntry, subentry: ConfigSubentry
) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{PROJECT_MISSING_PREFIX}{subentry.subentry_id}",
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="project_missing",
        translation_placeholders={
            "project": subentry.data[CONF_PROJECT_KEY],
            "instance": entry.title,
        },
        data={"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id},
    )


def _clear_resolved(hass: HomeAssistant, entry: YouTrackConfigEntry, missing: set[str]) -> None:
    """Delete repair issues of this entry whose project is back or was removed."""
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain != DOMAIN or not issue_id.startswith(PROJECT_MISSING_PREFIX):
            continue
        issue = registry.issues[(domain, issue_id)]
        if (issue.data or {}).get("entry_id") != entry.entry_id:
            continue
        if issue_id.removeprefix(PROJECT_MISSING_PREFIX) not in missing:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
