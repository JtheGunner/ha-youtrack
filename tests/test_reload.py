"""Tests for how quickly the entry sets up and reloads."""

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack import CACHE_MAX_AGE
from custom_components.youtrack.const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    DOMAIN,
    SUBENTRY_PROJECT,
)
from custom_components.youtrack.exceptions import YouTrackAuthError, YouTrackConnectionError
from custom_components.youtrack.models import Issue, Project

from .conftest import TOKEN, URL

ARTICLES = "sensor.demo_project_articles"
FULL_LOAD = "#Unresolved sort by: updated desc"


def _entry(*keys: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data={"url": URL, "api_token": TOKEN, "verify_ssl": True},
        subentries_data=[
            {
                "data": {CONF_PROJECT_ID: f"id-{key}", CONF_PROJECT_KEY: key},
                "subentry_id": f"sub-{key}",
                "subentry_type": SUBENTRY_PROJECT,
                "title": f"Project {key}",
                "unique_id": f"id-{key}",
            }
            for key in keys
        ],
    )


def _list_projects(mock_client: MagicMock, *keys: str) -> None:
    mock_client.get_projects.return_value = [
        Project(id=f"id-{key}", name=f"Project {key}", short_name=key, archived=False)
        for key in keys
    ]


def _full_loads(mock_client: MagicMock) -> int:
    return sum(FULL_LOAD in call.args[0] for call in mock_client.get_issues.call_args_list)


async def test_projects_load_at_the_same_time(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """No project waits for another one to finish loading its issues."""
    issues: list[Issue] = mock_client.get_issues.return_value
    started: set[str] = set()
    both_started = asyncio.Event()

    async def get_issues(query: str, **kwargs: object) -> list[Issue]:
        started.add("A" if "{A}" in query else "B")
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=5)
        return issues

    mock_client.get_issues.side_effect = get_issues
    _list_projects(mock_client, "A", "B")
    entry = _entry("A", "B")
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert set(entry.runtime_data.coordinators) == {"sub-A", "sub-B"}
    assert set(entry.runtime_data.knowledge_bases) == {"sub-A", "sub-B"}


async def test_unreachable_project_keeps_the_entry_from_loading(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """When one project cannot be read the entry is retried, as before."""
    issues: list[Issue] = mock_client.get_issues.return_value

    async def get_issues(query: str, **kwargs: object) -> list[Issue]:
        if "{B}" in query:
            raise YouTrackConnectionError("down")
        return issues

    mock_client.get_issues.side_effect = get_issues
    _list_projects(mock_client, "A", "B")
    entry = _entry("A", "B")
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_token_of_a_project_starts_reauth(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """A project that rejects the token puts the entry into the reauth state."""
    mock_client.get_issues.side_effect = YouTrackAuthError("nope")
    _list_projects(mock_client, "A", "B")
    entry = _entry("A", "B")
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(entry.async_get_active_flows(hass, {"reauth"}))


async def test_knowledge_base_does_not_hold_back_the_entry(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The entry is loaded while the articles are still being read."""
    release = asyncio.Event()
    articles = mock_client.get_all_articles.return_value

    async def get_all_articles(**kwargs: object) -> object:
        await release.wait()
        return articles

    mock_client.get_all_articles.side_effect = get_all_articles
    mock_project_entry.add_to_hass(hass)

    try:
        await hass.config_entries.async_setup(mock_project_entry.entry_id)

        assert mock_project_entry.state is ConfigEntryState.LOADED
        assert hass.states.get("sensor.demo_project_open_issues") is not None
        assert hass.states.get(ARTICLES).state == "unavailable"  # type: ignore[union-attr]
    finally:
        release.set()
    await hass.async_block_till_done()

    assert hass.states.get(ARTICLES).state == "2"  # type: ignore[union-attr]


async def test_reload_reads_only_what_changed(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """After a reload the open issues are brought up to date with a delta request."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()
    assert _full_loads(mock_client) == 1

    await hass.config_entries.async_reload(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_project_entry.state is ConfigEntryState.LOADED
    assert _full_loads(mock_client) == 1
    assert len(mock_project_entry.runtime_data.coordinators["sub-demo"].data.open_issues) > 0


async def test_reload_keeps_every_entity_id(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The entities of a reloaded entry are the same ones, with the same ids and states."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()
    before = {
        entity.entity_id: entity.unique_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), mock_project_entry.entry_id
        )
    }
    states = {state.entity_id: state.state for state in hass.states.async_all()}
    assert before

    await hass.config_entries.async_reload(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    assert {
        entity.entity_id: entity.unique_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), mock_project_entry.entry_id
        )
    } == before
    assert {state.entity_id: state.state for state in hass.states.async_all()} == states


async def test_reload_reads_everything_again_after_a_long_pause(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A cache that was last refreshed long ago is not trusted."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(mock_project_entry.entry_id)

    freezer.tick(CACHE_MAX_AGE + timedelta(minutes=10))
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    assert _full_loads(mock_client) == 2


async def test_new_token_never_sees_issues_read_with_the_old_one(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """A different token may be allowed to see other issues, so nothing is carried over."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(mock_project_entry.entry_id)
    hass.config_entries.async_update_entry(
        mock_project_entry, data={**mock_project_entry.data, "api_token": "perm:other"}
    )

    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    assert _full_loads(mock_client) == 2


async def test_reload_reads_everything_again_for_a_renamed_project(
    hass: HomeAssistant, mock_client: MagicMock, mock_project_entry: MockConfigEntry
) -> None:
    """The cache of a project is only reused under the key it was filled with."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()
    _list_projects(mock_client, "RENAMED")
    mock_client.get_projects.return_value = [
        Project(id="0-1", name="Demo Project", short_name="RENAMED", archived=False)
    ]

    await hass.config_entries.async_reload(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    assert _full_loads(mock_client) == 2


@pytest.mark.parametrize("remove", [True, False])
async def test_removing_the_entry_forgets_its_cache(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_project_entry: MockConfigEntry,
    remove: bool,
) -> None:
    """Only a removed entry loses its cache; unloading keeps it for the next setup."""
    mock_project_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_project_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][mock_project_entry.entry_id].caches

    if remove:
        await hass.config_entries.async_remove(mock_project_entry.entry_id)
    else:
        await hass.config_entries.async_unload(mock_project_entry.entry_id)
    await hass.async_block_till_done()

    assert (mock_project_entry.entry_id in hass.data[DOMAIN]) is not remove
