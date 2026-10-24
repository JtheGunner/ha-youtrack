"""Tests for repair issues and device cleanup."""

from unittest.mock import MagicMock

from homeassistant.components.repairs import DOMAIN as REPAIRS_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.youtrack import async_remove_config_entry_device
from custom_components.youtrack.const import CONF_PROJECT_KEY, DOMAIN
from custom_components.youtrack.exceptions import YouTrackConnectionError
from custom_components.youtrack.models import Project

ISSUE_ID = "project_missing_sub-demo"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _projects(*keep: str) -> list[Project]:
    known = {
        "0-1": Project(id="0-1", name="Demo Project", short_name="DEMO", archived=False),
        "0-2": Project(id="0-2", name="Operations", short_name="OPS", archived=False),
    }
    return [project for project_id, project in known.items() if project_id in keep]


async def test_deleted_project_raises_a_repair_and_does_not_block_the_entry(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The entry loads without the vanished project and points the user to the fix."""
    mock_client.get_projects.return_value = _projects("0-2")
    await _setup(hass, mock_project_entry)

    assert mock_project_entry.state is ConfigEntryState.LOADED
    assert mock_project_entry.runtime_data.coordinators == {}
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {"project": "DEMO", "instance": "yt.example.com"}
    assert hass.states.get("sensor.demo_project_open_issues") is None


async def test_repair_clears_when_the_project_is_back(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Reloading after access was restored removes the repair."""
    mock_client.get_projects.return_value = _projects("0-2")
    await _setup(hass, mock_project_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    mock_client.get_projects.return_value = _projects("0-1", "0-2")
    await hass.config_entries.async_reload(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
    assert hass.states.get("sensor.demo_project_open_issues") is not None


async def test_fixing_the_repair_removes_the_project(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """Confirming the repair deletes the subentry and the issue."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    mock_client.get_projects.return_value = _projects("0-2")
    await _setup(hass, mock_project_entry)
    http = await hass_client()

    response = await http.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": ISSUE_ID}
    )
    flow = await response.json()
    assert flow["step_id"] == "confirm"
    response = await http.post(f"/api/repairs/issues/fix/{flow['flow_id']}")
    assert (await response.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    assert "sub-demo" not in mock_project_entry.subentries
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_fixing_after_the_project_was_removed_is_harmless(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
) -> None:
    """A repair for a subentry the user already deleted just closes."""
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    mock_client.get_projects.return_value = _projects("0-2")
    await _setup(hass, mock_project_entry)
    hass.config_entries.async_remove_subentry(mock_project_entry, "sub-demo")
    await hass.async_block_till_done()
    http = await hass_client()

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="project_missing",
        data={"entry_id": mock_project_entry.entry_id, "subentry_id": "sub-demo"},
    )
    response = await http.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": ISSUE_ID}
    )
    flow = await response.json()
    response = await http.post(f"/api/repairs/issues/fix/{flow['flow_id']}")

    assert (await response.json())["type"] == "create_entry"


async def test_renamed_project_updates_the_stored_key(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The project id is stable, so a new key is adopted without user action."""
    mock_client.get_projects.return_value = [
        Project(id="0-1", name="Demo Renamed", short_name="NEW", archived=False)
    ]
    await _setup(hass, mock_project_entry)

    subentry = mock_project_entry.subentries["sub-demo"]
    assert subentry.data[CONF_PROJECT_KEY] == "NEW"
    assert subentry.title == "Demo Renamed"
    assert mock_project_entry.runtime_data.coordinators["sub-demo"].project_key == "NEW"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_project_check_failure_does_not_block_setup(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """If the project list cannot be read, the configured projects are used as they are."""
    mock_client.get_projects.side_effect = YouTrackConnectionError("down")
    await _setup(hass, mock_project_entry)

    assert mock_project_entry.state is ConfigEntryState.LOADED
    assert "sub-demo" in mock_project_entry.runtime_data.coordinators


async def test_only_inactive_project_devices_can_be_deleted(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The device of a running project stays; any other device may go."""
    await _setup(hass, mock_project_entry)
    registry = dr.async_get(hass)
    active = registry.async_get_device_by_identifier(
        (DOMAIN, f"{mock_project_entry.entry_id}_0-1"), mock_project_entry.entry_id
    )
    stale = registry.async_get_or_create(
        config_entry_id=mock_project_entry.entry_id,
        identifiers={(DOMAIN, f"{mock_project_entry.entry_id}_0-9")},
    )
    assert active is not None

    assert not await async_remove_config_entry_device(hass, mock_project_entry, active)
    assert await async_remove_config_entry_device(hass, mock_project_entry, stale)


async def test_repairs_of_other_entries_and_domains_are_left_alone(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """Cleaning up after a reload only touches this entry's project repairs."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        "project_missing_other",
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="project_missing",
        data={"entry_id": "other-entry", "subentry_id": "other"},
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        "unrelated",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="project_missing",
    )
    ir.async_create_issue(
        hass,
        "other_domain",
        "project_missing_x",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="x",
    )
    await _setup(hass, mock_project_entry)

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "project_missing_other") is not None
    assert registry.async_get_issue(DOMAIN, "unrelated") is not None
    assert registry.async_get_issue("other_domain", "project_missing_x") is not None
