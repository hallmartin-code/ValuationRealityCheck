"""Build the whole favicon set from the one brand mark.

Run after replacing `web/static/brand-mark.png`:

    python tools_build_icons.py

Every icon the app serves is derived here, so all of them have the same
provenance and none is inherited from a neighbouring project. The mark is a thin
ring of three figures, which loses its detail below about 24px, so the small
sizes get a light unsharp pass — 90% is the most that sharpens without putting
colour fringing on the ring.
"""

from pathlib import Path

from PIL import Image, ImageFilter

SOURCE = Path("web/static/brand-mark.png")
OUT = Path("web/static")

# The app's own ground, for surfaces that ignore transparency (--navy-950).
NAVY = (11, 21, 38, 255)

# Sizes at which the ring needs help surviving the downsample.
SHARPEN_BELOW = 64
SHARPEN_PERCENT = 90


def _mark() -> Image.Image:
    """The mark, trimmed to its true bounds so padding is chosen, not inherited."""
    mark = Image.open(SOURCE).convert("RGBA")
    return mark.crop(mark.getchannel("A").getbbox())


def transparent(mark: Image.Image, size: int, pad: float = 0.0) -> Image.Image:
    """The mark on transparency, so a tab icon sits on light and dark chrome alike."""
    inner = round(size * (1 - 2 * pad))
    art = mark.resize((inner, inner), Image.LANCZOS)
    if inner < SHARPEN_BELOW:
        art = art.filter(
            ImageFilter.UnsharpMask(radius=0.6, percent=SHARPEN_PERCENT, threshold=0)
        )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(art, ((size - inner) // 2, (size - inner) // 2), art)
    return canvas


def on_navy(mark: Image.Image, size: int, pad: float) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), NAVY)
    canvas.alpha_composite(transparent(mark, size, pad))
    return canvas


def build() -> list[str]:
    mark = _mark()
    written: list[str] = []

    for size in (16, 32, 48, 96):
        name = f"favicon-{size}x{size}.png"
        transparent(mark, size).save(OUT / name)
        written.append(name)

    # The canonical asset, full bleed, for anything that wants the mark itself.
    transparent(mark, 512).save(OUT / "icon.png")
    written.append("icon.png")

    # iOS composites on white and rounds the corners itself, so it gets the navy
    # ground and its own padding rather than being left to invent one.
    on_navy(mark, 180, pad=0.12).save(OUT / "apple-touch-icon.png")
    written.append("apple-touch-icon.png")

    on_navy(mark, 192, pad=0.10).save(OUT / "icon-192.png")
    on_navy(mark, 512, pad=0.10).save(OUT / "icon-512.png")
    written += ["icon-192.png", "icon-512.png"]

    # Android crops a maskable icon to a circle, so the art needs a safe zone.
    on_navy(mark, 512, pad=0.20).save(OUT / "icon-512-maskable.png")
    written.append("icon-512-maskable.png")

    # Multi-resolution .ico for bookmark bars and legacy Windows surfaces.
    transparent(mark, 256).save(
        OUT / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    written.append("favicon.ico")
    return written


if __name__ == "__main__":
    for name in build():
        print("wrote", OUT / name)
