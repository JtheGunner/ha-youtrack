"""Tests that the brand assets Home Assistant looks for are present and sized correctly."""

import re
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).parent.parent
BRAND = REPO_ROOT / "custom_components" / "youtrack" / "brand"


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


@pytest.mark.parametrize("name", ["icon", "logo", "dark_icon", "dark_logo"])
def test_every_png_has_an_svg_source(name: str) -> None:
    """The PNGs are exported from the SVG sources in ``docs/brand`` and must stay in sync."""
    assert (REPO_ROOT / "docs" / "brand" / f"{name}.svg").is_file()


def test_readme_header_uses_an_existing_brand_image() -> None:
    """The README header shows the icon from the brand folder, so the file must exist."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r'<img src="([^"]*brand/[^"]+)"', readme)

    assert match is not None
    assert (REPO_ROOT / match.group(1)).is_file()


@pytest.mark.parametrize("prefix", ["", "dark_"])
@pytest.mark.parametrize("name", ["icon", "logo"])
def test_one_x_image_shows_the_same_area_as_the_two_x_image(prefix: str, name: str) -> None:
    """HACS uses the 1x files, so they must not be a cropped part of the ``@2x`` artwork."""
    with (
        Image.open(BRAND / f"{prefix}{name}.png").convert("RGBA") as one_x,
        Image.open(BRAND / f"{prefix}{name}@2x.png").convert("RGBA") as two_x,
    ):
        one_x_box = one_x.getchannel("A").getbbox()
        two_x_box = two_x.getchannel("A").getbbox()

    assert one_x_box is not None
    assert two_x_box is not None
    assert all(
        abs(one_x_edge * 2 - two_x_edge) <= 4
        for one_x_edge, two_x_edge in zip(one_x_box, two_x_box, strict=True)
    )


@pytest.mark.parametrize("prefix", ["", "dark_"])
@pytest.mark.parametrize("scale", [1, 2])
def test_logo_shows_the_wordmark_and_is_not_cropped(prefix: str, scale: int) -> None:
    """A cropped export loses the wordmark, so the right part of the logo must contain pixels."""
    suffix = "@2x" if scale == 2 else ""

    with Image.open(BRAND / f"{prefix}logo{suffix}.png").convert("RGBA") as logo:
        right_half = logo.crop((logo.width // 2, 0, logo.width, logo.height))
        assert right_half.getchannel("A").getbbox() is not None
