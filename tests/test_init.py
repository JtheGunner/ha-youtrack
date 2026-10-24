"""Smoke tests for the integration package."""

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.youtrack import DOMAIN

MANIFEST = Path(__file__).parent.parent / "custom_components" / "youtrack" / "manifest.json"


async def test_integration_is_discoverable(hass: HomeAssistant) -> None:
    """Home Assistant finds the integration in custom_components."""
    integration = await async_get_integration(hass, DOMAIN)

    assert integration.domain == DOMAIN
    assert integration.name == "YouTrack"


def test_manifest_domain_matches_constant() -> None:
    """The manifest domain and the DOMAIN constant stay in sync."""
    assert json.loads(MANIFEST.read_text())["domain"] == DOMAIN
