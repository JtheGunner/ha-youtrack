"""Helpers shared by the tests."""

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def load_json_fixture(name: str) -> Any:
    """Return the decoded JSON fixture ``name``."""
    return json.loads((FIXTURES / name).read_text())


def load_text_fixture(name: str) -> str:
    """Return the raw text of the fixture ``name``."""
    return (FIXTURES / name).read_text()
