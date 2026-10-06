#!/usr/bin/env python3
"""Generate Sofia Gleda's raster app icons from the brand mark in icons/icon.svg
(the "Sofia Gleda" eye-with-play-button). Writes the PWA / Apple-touch PNGs:

    icons/icon-192.png
    icons/icon-512.png
    icons/icon-maskable-512.png

icon.svg itself is the hand-supplied source of truth and is left untouched; it is
served directly as the modern SVG favicon.

No system SVG rasteriser is installed, and the SVG is an Illustrator export that
stores each artwork layer as an RGB <image> plus a separate greyscale luminance
mask (the layer's alpha), composited through <mask>/feColorMatrix. Chromium
refuses to load those nested data: URIs, so instead of a browser we rebuild the
artwork directly with PIL: decode every embedded PNG, pair each colour tile with
its mask tile for the alpha channel, and place it under the same affine transform
the SVG applies (outer translate ∘ per-layer scale/offset). Run once:

    python3 scripts/make_icons.py
"""
import base64, io, re, pathlib
from PIL import Image

ICONS = pathlib.Path(__file__).resolve().parent.parent / "icons"
SVG = ICONS / "icon.svg"

BG = (12, 10, 15)            # #0C0A0F — the app's own near-black field
CANVAS = 1500               # the SVG viewBox is 0 0 1500 1500


def _matrix(s):
    return [float(x) for x in s.replace(" ", "").split(",")]


def build_artwork():
    """Composite icon.svg's embedded tiles into one RGBA image on a transparent
    field, honouring the SVG's transforms. Returns a CANVAS×CANVAS image."""
    svg = SVG.read_text(encoding="utf-8", errors="replace")

    # Masks are defined first (inside <defs>); each holds the greyscale alpha for
    # the colour layer that reuses the same transform in the body below.
    mask_b64 = re.findall(r"<mask\b.*?base64,([A-Za-z0-9+/=]+).*?</mask>", svg, re.S)

    body = svg[svg.rindex("</defs>"):]
    outer = (1, 0, 0, 1, 0, 0)
    mo = re.search(r'<g transform="matrix\(([-\d.,\s]+)\)">', body)
    if mo:
        outer = _matrix(mo.group(1))                       # outer translate for all layers

    # Each visible layer: its own scale/offset matrix wrapping the colour <image>.
    layers = re.findall(
        r'<g transform="matrix\(([-\d.,\s]+)\)">\s*'
        r'<image[^>]*?width="(\d+)"[^>]*?base64,([A-Za-z0-9+/=]+)[^>]*?height="(\d+)"',
        body, re.S)

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    for i, (mtx, w, b64, h) in enumerate(layers):
        a, b, c, d, e, f = _matrix(mtx)
        w, h = int(w), int(h)
        colour = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        layer = colour.convert("RGBA")
        if i < len(mask_b64):
            alpha = Image.open(io.BytesIO(base64.b64decode(mask_b64[i]))).convert("L")
            layer.putalpha(alpha.resize(colour.size, Image.LANCZOS))
        layer = layer.resize((round(w * a), round(h * d)), Image.LANCZOS)
        x = round(outer[4] + e)
        y = round(outer[5] + f)
        canvas.alpha_composite(layer, (x, y))
    if canvas.getbbox() is None:
        raise SystemExit("icon.svg produced no artwork — tiles/transforms not parsed")
    return canvas


def on_field(art, size, fill_frac):
    """Centre `art`'s content on a `size` square of BG, scaled so its longest side
    is `fill_frac` of the canvas (fill_frac < 1 leaves a maskable safe margin)."""
    content = art.crop(art.getbbox())
    scale = (size * fill_frac) / max(content.size)
    cw, ch = round(content.width * scale), round(content.height * scale)
    content = content.resize((cw, ch), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), BG + (255,))
    out.alpha_composite(content, ((size - cw) // 2, (size - ch) // 2))
    return out.convert("RGB")


def main():
    art = build_artwork()
    # Standard icons: generous fill. Maskable: pull inside the ~80% safe zone so
    # Android's circle/squircle crop never clips the eye.
    on_field(art, 192, 0.90).save(ICONS / "icon-192.png")
    on_field(art, 512, 0.90).save(ICONS / "icon-512.png")
    on_field(art, 512, 0.66).save(ICONS / "icon-maskable-512.png")
    print("wrote icon-192.png, icon-512.png, icon-maskable-512.png "
          "(icon.svg left as the source of truth)")


if __name__ == "__main__":
    main()
