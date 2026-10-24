"""Shared fixtures for the YouTrack integration tests."""

from collections.abc import AsyncGenerator, Callable, Generator
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.api import YouTrackClient
from custom_components.youtrack.const import (
    CONF_API_TOKEN,
    CONF_PROJECT_ID,
    CONF_PROJECT_KEY,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    SUBENTRY_PROJECT,
)
from custom_components.youtrack.models import (
    ActivityPage,
    Article,
    Issue,
    Project,
    ProjectField,
    ServerInfo,
    User,
)

from .common import load_json_fixture

URL = "https://yt.example.com"
TOKEN = "perm:test-token"


async def fake_count(query: str) -> int | None:
    """Return a stable count per kind of query."""
    if "Assignee: Unassigned" in query:
        return 3
    if "Priority:" in query:
        return 2
    if "resolved date" in query:
        return 4
    if "created:" in query:
        return 5
    if "minus 1d}" in query and "#Unresolved" in query and ".." in query:
        return 1
    if query.endswith("#Unresolved"):
        return 10
    if query.endswith("#Resolved"):
        return 30
    return 42


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration from custom_components."""


@pytest.fixture(autouse=True)
async def settle_tasks(hass: HomeAssistant) -> AsyncGenerator[None]:
    """Let reloads started by a test finish before Home Assistant is torn down."""
    yield
    await hass.async_block_till_done()


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Replace the YouTrack client class and return the shared instance."""
    client_class = create_autospec(YouTrackClient)
    client = client_class.return_value
    client.get_server_info.return_value = ServerInfo(version="2026.2", build="1")
    client.get_current_user.return_value = User(login="example.user", name="Example User")
    client.count_issues.side_effect = fake_count
    client.get_activities.return_value = ActivityPage([], 0)
    client.get_all_articles.return_value = (
        [Article.from_json(item) for item in load_json_fixture("articles.json")],
        False,
    )
    client.get_issues.return_value = [
        Issue.from_json(item) for item in load_json_fixture("issues.json")
    ]
    client.get_project_fields.return_value = [
        ProjectField(name="Priority", field_type="enum[1]"),
        ProjectField(name="State", field_type="state[1]"),
        ProjectField(name="Assignee", field_type="user[1]"),
    ]
    client.get_projects.return_value = [
        Project(id="0-1", name="Demo Project", short_name="DEMO", archived=False),
        Project(id="0-2", name="Operations", short_name="OPS", archived=False),
        Project(id="0-3", name="Old", short_name="OLD", archived=True),
    ]
    with (
        patch("custom_components.youtrack.YouTrackClient", new=client_class),
        patch("custom_components.youtrack.config_flow.YouTrackClient", new=client_class),
    ):
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for a configured instance."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data={CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )


@pytest.fixture
def mock_project_entry() -> MockConfigEntry:
    """Return a config entry that has the project DEMO as a subentry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="yt.example.com",
        unique_id=URL,
        data={CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
        subentries_data=[
            {
                "data": {CONF_PROJECT_ID: "0-1", CONF_PROJECT_KEY: "DEMO"},
                "subentry_id": "sub-demo",
                "subentry_type": SUBENTRY_PROJECT,
                "title": "Demo Project",
                "unique_id": "0-1",
            }
        ],
    )


type ClientFactory = Callable[..., YouTrackClient]


@pytest.fixture
def make_client(hass: HomeAssistant) -> ClientFactory:
    """Return a factory for clients bound to the mocked session."""

    def factory(base_url: str = URL, **kwargs: object) -> YouTrackClient:
        kwargs.setdefault("count_retry_delay", 0)
        return YouTrackClient(
            async_get_clientsession(hass),
            base_url,
            TOKEN,
            **kwargs,  # type: ignore[arg-type]
        )

    return factory
