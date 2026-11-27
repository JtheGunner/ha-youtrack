"""Tests that every translation file covers the same keys as the English source."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

TRANSLATIONS = Path(__file__).parent.parent / "custom_components" / "youtrack" / "translations"


def _keys(node: object, prefix: str = "") -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _keys(value, f"{prefix}{key}.")
    else:
        yield prefix.rstrip(".")


def _load(language: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))
    return data


@pytest.mark.parametrize("language", sorted(path.stem for path in TRANSLATIONS.glob("*.json")))
def test_translation_has_the_same_keys_as_english(language: str) -> None:
    """No key is missing or left over, so no text falls back to English."""
    english = set(_keys(_load("en")))
    translated = set(_keys(_load(language)))

    assert english - translated == set()
    assert translated - english == set()


def test_range_errors_are_translated() -> None:
    """The out-of-range messages exist and name the allowed range."""
    for language in ("en", "de"):
        data = _load(language)
        top = data["config_subentries"]["project"]["error"]["top_out_of_range"]  # type: ignore[index]
        interval = data["options"]["error"]["scan_interval_out_of_range"]  # type: ignore[index]
        assert "{min}" in top
        assert "{max}" in top
        assert "{min}" in interval
        assert "{max}" in interval
