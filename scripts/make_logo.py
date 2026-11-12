#!/usr/bin/env python3
"""Export the header logo, logo.webp, from logo.svg.

logo.svg is a Canva export: the two words are vector paths, the emblem between them
is four embedded PNGs (507 x 263 px), so the file weighs about 154 KB while the header
draws it 30 to 42 px tall. The header shows logo.webp instead: the same artwork at
648 x 126 px (126 px = 42 px, the tallest the header draws it, at three device pixels
per CSS pixel; 648 = 126 x 1246.58 / 242.36, logo.svg's own proportions), transparent
background. logo.svg stays for the legal pages and the share preview.

How: headless Chromium draws logo.svg four times larger than needed (2592 x 504) on a
transparent page; Pillow averages each 4 x 4 block into one pixel, reduces the picture
to at most 256 colours and writes a lossless WebP. The artwork is two flat colours with
soft edges, so a 256-colour lossless file keeps the edges as drawn (a lossy WebP blurs
the red letters' edges and needed 17 to 36 KB; this is about 8 KB). The two fill colours
stay exact, solid pixels stay solid and empty pixels stay empty. No network. Run once,
after logo.svg changes, or with the owner's true vector file as --svg:

    python3 scripts/make_logo.py                 # logo.svg -> logo.webp
    python3 scripts/make_logo.py --svg PATH --out PATH --png PATH   # other files; --png keeps the master
"""
import argparse, base64, collections, io, pathlib, sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 648, 126        # 126 px tall, ratio 1246.58:242.36
OVERSAMPLE = 4                  # drawn at 2592 x 504, then averaged down
MAX_BYTES = 15000


def render(svg_path, scale=OVERSAMPLE):
    """logo.svg drawn by headless Chromium, WIDTH x HEIGHT CSS px at `scale` device
    pixels each, on a transparent background. Returns an RGBA image."""
    from playwright.sync_api import sync_playwright
    uri = "data:image/svg+xml;base64," + base64.b64encode(pathlib.Path(svg_path).read_bytes()).decode("ascii")
    html = ("<!doctype html><html><body style='margin:0;background:transparent'>"
            f"<img id='l' src='{uri}' style='display:block;width:{WIDTH}px;height:{HEIGHT}px'></body></html>")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=scale)
        page.set_content(html)
        page.wait_for_function("(() => { const i = document.getElementById('l'); return i.complete && i.naturalWidth > 0; })()")
        page.evaluate("document.getElementById('l').decode()")
        png = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
        browser.close()
    return Image.open(io.BytesIO(png)).convert("RGBA")


def reduce(img, colors=256):
    """The picture in at most about `colors` colours. A fill colour (5% or more of the
    solid pixels) keeps its exact value, a solid pixel stays solid, an empty one empty."""
    out = img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).convert("RGBA")
    src, dst = img.load(), out.load()
    solid = collections.Counter(src[x, y][:3] for y in range(img.height) for x in range(img.width)
                                if src[x, y][3] == 255)
    fills = {c for c, n in solid.items() if n >= sum(solid.values()) * 0.05}
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = src[x, y]
            if a == 255:
                dst[x, y] = (r, g, b, 255) if (r, g, b) in fills else dst[x, y][:3] + (255,)
            elif a == 0:
                dst[x, y] = (0, 0, 0, 0)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--svg", default=str(ROOT / "logo.svg"))
    ap.add_argument("--out", default=str(ROOT / "logo.webp"))
    ap.add_argument("--png", help="also save the full-colour master (before the colour reduction) as a PNG here")
    a = ap.parse_args(argv)
    big = render(a.svg)
    master = big.resize((WIDTH, HEIGHT), Image.Resampling.BOX)
    if a.png:
        master.save(a.png)
    img = reduce(master)
    buf = io.BytesIO()
    img.save(buf, "WEBP", lossless=True, quality=100, method=6)
    if buf.tell() > MAX_BYTES:
        sys.exit(f"make_logo: {buf.tell()} bytes is over the {MAX_BYTES} byte budget; nothing written")
    pathlib.Path(a.out).write_bytes(buf.getvalue())
    print(f"make_logo: {a.out} {WIDTH}x{HEIGHT} {buf.tell()} bytes "
          f"(lossless, {len(img.getcolors(maxcolors=1 << 24))} colours); drawn at {big.size[0]}x{big.size[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
