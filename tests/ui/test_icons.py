"""Static and node checks for the token and protocol icons (ui/icons.js, ui/icons.css)."""

import functools
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
TOKENS = UI / "assets" / "tokens"
ICONS_DOC = ROOT / "docs" / "design" / "webapp" / "icons.md"
SHA = "1a63530be6e374711a8554f31b17e4cb92c25fa5"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not available")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def icons_js_stems() -> list[str]:
    return re.findall(r'file: "([A-Za-z0-9_-]+)"', read(UI / "icons.js"))


def test_every_icon_file_in_icons_js_exists():
    stems = icons_js_stems()
    assert stems, "no token file names found in ui/icons.js"
    for stem in set(stems):
        assert (TOKENS / f"{stem}.svg").is_file(), stem


@pytest.mark.parametrize("svg", sorted(TOKENS.glob("*.svg")), ids=lambda p: p.name)
def test_svg_is_small_and_static(svg: pathlib.Path):
    text = read(svg)
    assert len(svg.read_bytes()) < 2048
    assert text.startswith("<svg")
    for banned in ("<script", "href=", "onload", "<image", "<foreignObject", "data:"):
        assert banned not in text, banned


def test_every_svg_and_the_commit_are_in_icons_doc():
    doc = read(ICONS_DOC)
    assert SHA in doc
    for svg in TOKENS.glob("*.svg"):
        assert svg.name in doc, svg.name


def test_icons_js_has_no_html_storage_or_urls():
    text = read(UI / "icons.js")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "localStorage",
        "http://",
        "https://",
    ):
        assert banned not in text, banned


def test_icons_css_has_no_import_remote_url_caps_or_shadow():
    text = read(UI / "icons.css")
    for banned in ("@import", "url(http", "text-transform", "box-shadow"):
        assert banned not in text, banned


@functools.lru_cache(maxsize=1)
def icons_check() -> dict:
    out = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui" / "icons_check.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(out.stdout)


@needs_node
def test_icon_fallback_colour_is_stable_and_in_palette():
    res = icons_check()
    assert res["fallbackStable"] is True
    assert res["fallbackInPalette"] is True


@needs_node
def test_icon_colours_meet_contrast():
    assert icons_check()["minContrast"] >= 4.5


@needs_node
def test_usdc_has_image_and_testnet_mark():
    res = icons_check()
    assert res["usdcImgSrc"] == "assets/tokens/usdc.svg"
    assert res["usdcTestnetMark"] is True


@needs_node
def test_weth_uses_eth_image_with_w_badge():
    res = icons_check()
    assert res["wethImgSrc"] == "assets/tokens/eth.svg"
    assert res["wethLetterBadge"] == "W"


@needs_node
def test_cbeth_is_a_letter_avatar():
    res = icons_check()
    assert res["cbethHasImg"] is False
    assert res["cbethLetter"] == "C"


@needs_node
def test_derived_token_has_base_image_and_protocol_badge():
    res = icons_check()
    assert res["aUsdcImgSrc"] == "assets/tokens/usdc.svg"
    assert res["aUsdcProtocolBadge"] == "A"


@needs_node
def test_testnet_mark_can_be_turned_off():
    assert icons_check()["usdcNoTestnetMark"] is False


@needs_node
def test_bad_symbols_do_not_throw():
    res = icons_check()
    assert res["noThrow42"] is True
    assert res["noThrowUndef"] is True


@needs_node
def test_no_created_attribute_starts_with_http():
    assert icons_check()["httpAttr"] is False


@needs_node
def test_display_names():
    res = icons_check()
    assert res["displayNameAUsdc"] == "USDC supplied on Aave"
    assert res["displayNameZzz"] == "ZZZ"


@needs_node
def test_protocol_avatar_letter():
    assert icons_check()["morphoLetter"] == "M"
