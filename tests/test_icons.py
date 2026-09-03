"""The favicon set: that it exists, resolves, and is this application's own.

The icons and the web manifest arrived by being copied wholesale from a
neighbouring TEN Capital project, which is how the manifest came to describe a
different product. That is the failure these tests exist to catch — an icon that
404s is obvious the first time anyone opens the app, but a manifest naming the
wrong tool is invisible until someone installs it to a home screen.

So the checks are: every icon the page declares actually resolves, every icon the
manifest declares actually resolves, the manifest describes *this* product, and
the pixels are the TEN Capital mark rather than some other project's artwork.
"""

from __future__ import annotations

import json
import re
import struct
from collections import Counter

import pytest

flask = pytest.importorskip("flask")
Image = pytest.importorskip("PIL.Image", reason="Pillow is needed to inspect the icons")

from PIL import Image as PILImage  # noqa: E402

from app import config  # noqa: E402
from web import server  # noqa: E402

STATIC = config.PROJECT_ROOT / "web" / "static"

# The three figures of the mark, sampled from the source artwork.
BRAND_COLOURS = ((0xED, 0x56, 0x44), (0xF1, 0xA3, 0x1F), (0x4F, 0xC4, 0xD6))
TOLERANCE = 26


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    server.flask_app.config["TESTING"] = True
    return server.flask_app.test_client()


def near_brand(rgb: tuple[int, int, int]) -> bool:
    return any(
        all(abs(rgb[index] - brand[index]) <= TOLERANCE for index in range(3))
        for brand in BRAND_COLOURS
    )


def dominant(path, count: int = 3) -> list[tuple[int, int, int]]:
    """The most common opaque colours in an image."""
    tally: Counter = Counter()
    for pixel in pixels(PILImage.open(path)):
        if pixel[3] > 200:
            tally[pixel[:3]] += 1
    return [rgb for rgb, _ in tally.most_common(count)]


def pixels(image) -> list[tuple[int, int, int, int]]:
    """Flat RGBA pixel list, across the Pillow versions that renamed the accessor."""
    image = image.convert("RGBA")
    reader = getattr(image, "get_flattened_data", None) or image.getdata
    return list(reader())


class TestTheSourceMark:
    def test_the_brand_mark_is_in_the_public_directory(self):
        assert (STATIC / "brand-mark.png").exists()

    def test_it_is_square_and_transparent(self):
        mark = PILImage.open(STATIC / "brand-mark.png").convert("RGBA")
        assert mark.width == mark.height
        assert mark.getchannel("A").getextrema()[0] == 0, "the mark must sit on transparency"

    def test_the_build_script_regenerates_the_set_from_it(self):
        """The icons must be reproducible, not hand-made artefacts nobody can rebuild."""
        script = (config.PROJECT_ROOT / "tools_build_icons.py").read_text(encoding="utf-8")
        assert "brand-mark.png" in script
        assert "favicon.ico" in script


class TestDeclaredIcons:
    def test_every_icon_the_page_declares_resolves(self, client):
        body = client.get("/").get_data(as_text=True)
        hrefs = re.findall(r'<link rel="[^"]*(?:icon|manifest)"[^>]*href="([^"]+)"', body)
        assert hrefs, "the page declares no icons at all"
        for href in hrefs:
            response = client.get(href)
            assert response.status_code == 200, href
            assert response.data, href

    def test_the_page_declares_the_sizes_browsers_look_for(self, client):
        body = client.get("/").get_data(as_text=True)
        for size in ("16x16", "32x32", "48x48", "96x96"):
            assert f'sizes="{size}"' in body, size
        assert 'rel="apple-touch-icon"' in body
        assert 'rel="manifest"' in body

    def test_the_root_favicon_path_is_served(self, client):
        """Browsers and crawlers probe /favicon.ico regardless of what the head says."""
        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.data[:4] == b"\x00\x00\x01\x00"


class TestTheIcoFile:
    def test_it_is_a_real_multi_resolution_icon(self):
        with open(STATIC / "favicon.ico", "rb") as handle:
            _, kind, count = struct.unpack("<HHH", handle.read(6))
            assert kind == 1, "not an icon file"
            sizes = set()
            for _ in range(count):
                width, height, *_rest = struct.unpack("<BBBBHHII", handle.read(16))
                sizes.add(width or 256)
        assert {16, 32, 48} <= sizes, f"missing a size browsers ask for: {sorted(sizes)}"


class TestTheManifest:
    @pytest.fixture
    def manifest(self, client) -> dict:
        return json.loads(client.get("/static/site.webmanifest").data)

    def test_it_describes_this_application(self, manifest):
        """It described a neighbouring project once; that is what this catches."""
        blob = json.dumps(manifest).lower()
        assert "valuation" in manifest["name"].lower()
        assert "upside" not in blob, "the manifest still describes another TEN Capital tool"

    def test_it_carries_the_fields_an_install_needs(self, manifest):
        for key in ("name", "short_name", "start_url", "display", "icons"):
            assert manifest.get(key), key
        assert manifest["background_color"].upper() == "#0B1526"
        assert manifest["theme_color"].upper() == "#0B1526"

    def test_every_manifest_icon_resolves(self, client, manifest):
        for icon in manifest["icons"]:
            response = client.get(icon["src"])
            assert response.status_code == 200, icon["src"]

    def test_the_declared_sizes_match_the_actual_files(self, manifest):
        for icon in manifest["icons"]:
            path = STATIC / icon["src"].split("/")[-1]
            width, height = PILImage.open(path).size
            assert f"{width}x{height}" == icon["sizes"], icon["src"]

    def test_a_maskable_icon_is_offered_with_a_safe_zone(self, manifest):
        """Android crops a maskable icon to a circle; art at the edge gets cut."""
        maskable = [i for i in manifest["icons"] if i.get("purpose") == "maskable"]
        assert maskable, "no maskable icon for an installed home-screen shortcut"
        path = STATIC / maskable[0]["src"].split("/")[-1]
        image = PILImage.open(path).convert("RGBA")
        inset = round(image.width * 0.16)
        edge = image.crop((0, 0, image.width, inset))
        assert not any(near_brand(p[:3]) for p in pixels(edge)[::7]), (
            "brand artwork reaches the crop zone of the maskable icon"
        )


class TestThePixelsAreOurs:
    @pytest.mark.parametrize(
        "name",
        [
            "favicon-16x16.png",
            "favicon-32x32.png",
            "favicon-48x48.png",
            "favicon-96x96.png",
            "icon.png",
            "icon-192.png",
            "icon-512.png",
        ],
    )
    def test_each_icon_is_the_ten_capital_mark(self, name):
        """Guards against inheriting another project's artwork a second time."""
        found = dominant(STATIC / name)
        assert any(near_brand(rgb) for rgb in found), f"{name}: {found}"

    def test_the_tab_icons_keep_their_transparency(self):
        """A tab icon sits on light and dark browser chrome, so it must not bake a ground."""
        for size in (16, 32, 48, 96):
            image = PILImage.open(STATIC / f"favicon-{size}x{size}.png").convert("RGBA")
            assert image.getchannel("A").getextrema()[0] == 0, size

    def test_the_apple_icon_is_opaque(self):
        """iOS composites transparency onto white, which would strand the mark."""
        image = PILImage.open(STATIC / "apple-touch-icon.png").convert("RGBA")
        assert image.getchannel("A").getextrema()[0] == 255
