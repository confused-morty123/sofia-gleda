#!/usr/bin/env python3
"""Render the 1200×630 site share-preview image for Sofia Gleda.

Generates:
    share/share-preview.jpg   — the OG image for the site homepage

Uses Playwright to screenshot a purpose-built HTML template so the result
is reproducible and matches the app's visual identity exactly.

Screenshot is also copied to /tmp/p3c_shots/share-preview.jpg for review.

    python3 scripts/gen_share_preview.py
"""
import base64
import io
import pathlib
import shutil
import sys

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOTS_DIR = pathlib.Path("/tmp/p3c_shots")
FONTS_DIR = ROOT / "share" / "fonts"

# Four posters mixing film and theatre — current programme
STRIP_POSTERS = [
    "posters/feyata-vanilia.png",       # theatre
    "posters/golemiyat-sin.png",        # theatre
    "posters/spasyavaneto-na-dzheysi.png",  # theatre
    "posters/urok-po-balgarski.jpg",    # film
]


def _resize_data_uri(path: pathlib.Path, target_w: int, target_h: int) -> str:
    """Open image, crop to centre top, resize to target, return JPEG data URI."""
    img = Image.open(path).convert("RGB")
    iw, ih = img.size
    target_ar = target_w / target_h
    if iw / ih > target_ar:
        # Too wide — crop sides
        new_w = round(ih * target_ar)
        left = (iw - new_w) // 2
        img = img.crop((left, 0, left + new_w, ih))
    else:
        # Too tall — crop top/bottom using upper-biased crop (show top of poster)
        new_h = round(iw / target_ar)
        img = img.crop((0, 0, iw, new_h))

    img = img.resize((target_w, target_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def _font_data_uri(path: pathlib.Path) -> str:
    """Return base64 data URI for a woff2 font file."""
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:font/woff2;base64,{b64}"


def _logo_data_uri() -> str:
    """Return data URI for logo.svg, metadata stripped to reduce size."""
    import re
    svg_text = (ROOT / "logo.svg").read_text(encoding="utf-8")
    # Strip C2PA metadata block (large, not needed for rendering)
    svg_text = re.sub(r"<metadata>.*?</metadata>", "", svg_text, flags=re.DOTALL)
    svg_text = svg_text.replace(' xmlns:c2pa="http://c2pa.org/manifest"', "")
    b64 = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _build_html() -> str:
    logo_uri = _logo_data_uri()

    # Embed Onest font subsets as data URIs — covers cyrillic, latin
    font_faces = ""
    for fname, subset in [
        ("onest-400-cyrillic-ext.woff2", "cyrillic-ext"),
        ("onest-400-cyrillic.woff2", "cyrillic"),
        ("onest-400-latin.woff2", "latin"),
    ]:
        fpath = FONTS_DIR / fname
        if fpath.exists():
            uri = _font_data_uri(fpath)
            font_faces += f"""@font-face{{
  font-family:'Onest';font-style:normal;font-weight:400;
  src:url({uri}) format('woff2');
}}
"""

    # Poster strip: 4 posters each 2× pixel-density for sharpness
    # Strip width ~490px, each poster ~120px rendered → 240×630px target at 2×
    poster_imgs_parts = []
    for p in STRIP_POSTERS:
        uri = _resize_data_uri(ROOT / p, 240, 630)
        poster_imgs_parts.append(f'<img class="poster" src="{uri}" alt="">')
    poster_imgs = "\n".join(poster_imgs_parts)

    # Logo: SVG viewBox="147.02 628.77 1246.58 242.36"
    # Display at 480px wide → height = 480 * 242.36 / 1246.58 ≈ 93px
    logo_w = 480
    logo_h = round(logo_w * 242.36 / 1246.58)  # ≈ 93px

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
{font_faces}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{
  width:1200px;height:630px;overflow:hidden;
  background:#0C0A0F;
  font-family:'Onest',sans-serif;
  color:#F4EFF6;
  position:relative;
}}

/* ── Left content area ── */
.left{{
  position:absolute;
  left:60px;top:0;
  width:640px;height:630px;
  display:flex;flex-direction:column;
  justify-content:center;
}}

.logo-wrap{{
  width:{logo_w}px;
  height:{logo_h}px;
  margin-bottom:20px;
  flex-shrink:0;
}}
.logo-wrap img{{
  width:{logo_w}px;
  height:{logo_h}px;
  display:block;
}}

.tagline-sub{{
  font-size:22px;
  font-weight:400;
  line-height:1.5;
  color:#F4EFF6;
  opacity:0.5;
}}

/* ── Poster strip ── */
.poster-strip{{
  position:absolute;
  right:0;top:0;
  width:500px;height:630px;
  display:flex;
  flex-direction:row;
  gap:6px;
  align-items:stretch;
  overflow:hidden;
}}
.poster{{
  flex:1;
  object-fit:cover;
  object-position:center top;
  height:630px;
  min-width:0;
  display:block;
}}

/* Gradient fade on left edge of poster strip */
.strip-fade{{
  position:absolute;
  right:455px;top:0;
  width:220px;height:630px;
  background:linear-gradient(to right, #0C0A0F 0%, rgba(12,10,15,0) 100%);
  z-index:10;
  pointer-events:none;
}}

/* Gentle radial vignette */
.vignette{{
  position:absolute;inset:0;
  background:radial-gradient(ellipse 70% 80% at 28% 50%,
    transparent 30%,
    rgba(12,10,15,0.35) 100%);
  pointer-events:none;z-index:5;
}}
</style>
</head>
<body>

<div class="poster-strip">
{poster_imgs}
</div>
<div class="strip-fade"></div>
<div class="vignette"></div>

<div class="left">
  <div class="logo-wrap">
    <img src="{logo_uri}" alt="Sofia Gleda">
  </div>
  <div class="tagline-sub">Афишът на кината и театрите в София</div>
</div>

</body>
</html>
"""


def main():
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    # Playwright screenshots as PNG first, then we save as JPEG — no palette quantisation.
    _tmp_png = ROOT / "share" / "_share-preview-tmp.png"
    out = ROOT / "share" / "share-preview.jpg"

    html = _build_html()

    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(html)
        tmp_html = pathlib.Path(fh.name)

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 630})
            page.goto(tmp_html.as_uri())
            page.wait_for_timeout(600)

            # Verify Onest is actually applied (not a fallback)
            font_check = page.evaluate("""() => {
                const el = document.querySelector('.tagline-sub');
                if (!el) return 'element not found';
                return getComputedStyle(el).fontFamily;
            }""")
            print(f"gen_share_preview: computed font-family: {font_check!r}")
            if "Onest" not in font_check:
                print("warning: Onest not found in computed font-family",
                      file=sys.stderr)

            page.screenshot(path=str(_tmp_png), clip={"x": 0, "y": 0,
                                                       "width": 1200, "height": 630})
            browser.close()
    finally:
        tmp_html.unlink(missing_ok=True)

    # Convert to JPEG at quality 85 — no palette reduction, photo gradients stay smooth.
    img = Image.open(_tmp_png).convert("RGB")
    img.save(out, format="JPEG", quality=85, optimize=True)
    _tmp_png.unlink(missing_ok=True)

    # Remove the old PNG if it still exists from a previous run.
    _old_png = ROOT / "share" / "share-preview.png"
    _old_png.unlink(missing_ok=True)

    size_kb = out.stat().st_size // 1024
    if size_kb > 200:
        print(f"warning: share-preview.jpg is {size_kb} KB (target ≤200 KB)",
              file=sys.stderr)

    shutil.copy(out, SHOTS_DIR / "share-preview.jpg")
    print(f"gen_share_preview: wrote share/share-preview.jpg ({size_kb} KB), "
          f"copy at /tmp/p3c_shots/share-preview.jpg")


if __name__ == "__main__":
    main()
