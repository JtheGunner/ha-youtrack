"""Shared fixtures for the YouTrack integration tests."""

from collections.abc import Generator
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtrack.api import YouTrackClient
from custom_components.youtrack.const import (
    CONF_API_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.youtrack.models import Project, ServerInfo, User

URL = "https://yt.example.com"
TOKEN = "perm:test-token"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration from custom_components."""


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Replace the YouTrack client class and return the shared instance."""
    client_class = create_autospec(YouTrackClient)
    client = client_class.return_value
    client.get_server_info.return_value = ServerInfo(version="2026.2", build="1")
    client.get_current_user.return_value = User(login="example.user", name="Example User")
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
