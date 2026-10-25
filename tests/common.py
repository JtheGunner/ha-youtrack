"""Helpers shared by the tests."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.youtrack import RELOAD_DELAY

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def load_json_fixture(name: str) -> Any:
    """Return the decoded JSON fixture ``name``."""
    return json.loads((FIXTURES / name).read_text())


def load_text_fixture(name: str) -> str:
    """Return the raw text of the fixture ``name``."""
    return (FIXTURES / name).read_text()


async def let_reload_run(hass: HomeAssistant) -> None:
    """Wait out the delay after a project change, then let the entry reload."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=RELOAD_DELAY + 1))
    await hass.async_block_till_done()
