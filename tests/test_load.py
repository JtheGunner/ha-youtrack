"""Tests that document how many YouTrack requests a poll costs."""

import asyncio
from datetime import timedelta
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.api import MAX_CONCURRENT_REQUESTS, YouTrackClient
from custom_components.youtrack.const import (
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    DOMAIN,
    SUBENTRY_PROJECT,
)
from custom_components.youtrack.models import Project

from .conftest import TOKEN, URL

# total, open, resolved, unassigned, created and resolved in 7 days, critical open
COUNTS_PER_PROJECT = 7
METHODS = ("count_issues", "get_issues", "get_activities", "get_all_articles")


def _entry(projects: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data={"url": URL, "api_token": TOKEN, "verify_ssl": True},
        subentries_data=[
            {
                "data": {CONF_PROJECT_ID: f"0-{n}", CONF_PROJECT_KEY: f"P{n}"},
                "subentry_id": f"sub-{n}",
                "subentry_type": SUBENTRY_PROJECT,
                "title": f"Project {n}",
                "unique_id": f"0-{n}",
            }
            for n in range(projects)
        ],
    )


async def _setup(hass: HomeAssistant, mock_client: MagicMock, projects: int) -> MockConfigEntry:
    mock_client.get_projects.return_value = [
        Project(id=f"0-{n}", name=f"Project {n}", short_name=f"P{n}", archived=False)
        for n in range(projects)
    ]
    entry = _entry(projects)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize("projects", [1, 5, 20])
async def test_steady_state_poll_cost(
    hass: HomeAssistant, mock_client: MagicMock, freezer: FrozenDateTimeFactory, projects: int
) -> None:
    """A poll costs one request batch per project plus one shared activity request."""
    entry = await _setup(hass, mock_client, projects)
    coordinators = list(entry.runtime_data.coordinators.values())
    freezer.tick(timedelta(minutes=5))
    await asyncio.gather(*(coordinator.async_refresh() for coordinator in coordinators))
    before = {name: getattr(mock_client, name).call_count for name in METHODS}

    freezer.tick(timedelta(minutes=5))
    await asyncio.gather(*(coordinator.async_refresh() for coordinator in coordinators))

    cost = {name: getattr(mock_client, name).call_count - before[name] for name in METHODS}
    assert cost == {
        "count_issues": COUNTS_PER_PROJECT * projects,
        "get_issues": projects,  # one delta request each, no custom queries
        "get_activities": 1,  # shared by all projects
        "get_all_articles": 0,  # the knowledge base polls on its own, slower schedule
    }


async def test_knowledge_base_costs_one_listing_per_project(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Each knowledge base refresh reads the project's articles once."""
    entry = await _setup(hass, mock_client, 3)
    mock_client.get_all_articles.reset_mock()

    for knowledge_base in entry.runtime_data.knowledge_bases.values():
        await knowledge_base.async_refresh()

    assert mock_client.get_all_articles.await_count == 3


async def test_requests_in_flight_are_capped() -> None:
    """Many projects polling together never open more than the allowed connections."""
    session = _CountingSession()
    client = YouTrackClient(session, URL, TOKEN)  # type: ignore[arg-type]

    await asyncio.gather(*(client.get_server_info() for _ in range(50)))

    assert session.requests == 50
    assert session.peak == MAX_CONCURRENT_REQUESTS


class _CountingSession:
    """Just enough of an aiohttp session to observe how many requests overlap."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.requests = 0

    def request(self, *args: object, **kwargs: object) -> _CountingRequest:
        return _CountingRequest(self)


class _CountingRequest:
    status = 200
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, session: _CountingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _CountingRequest:
        self._session.requests += 1
        self._session.in_flight += 1
        self._session.peak = max(self._session.peak, self._session.in_flight)
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._session.in_flight -= 1

    async def json(self, **kwargs: object) -> dict[str, str]:
        return {"version": "2026.2", "build": "1"}
