"""Tests that the brand assets Home Assistant looks for are present and sized correctly."""

from pathlib import Path

import pytest
from PIL import Image

BRAND = Path(__file__).parent.parent / "custom_components" / "youtrack" / "brand"


@pytest.mark.parametrize("prefix", ["", "dark_"])
@pytest.mark.parametrize("scale", [1, 2])
def test_icon_is_square_and_logo_has_the_expected_height(prefix: str, scale: int) -> None:
    """Icons are 256 px squares and logos 128 px high, doubled for the ``@2x`` files."""
    suffix = "@2x" if scale == 2 else ""

    with Image.open(BRAND / f"{prefix}icon{suffix}.png") as icon:
        assert icon.size == (256 * scale, 256 * scale)
    with Image.open(BRAND / f"{prefix}logo{suffix}.png") as logo:
        assert logo.height == 128 * scale
        assert logo.width > logo.height
