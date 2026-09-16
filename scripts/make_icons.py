#!/usr/bin/env python3
"""Generate Sofia Gleda's app icons: a red projector-flare mark on a near-black
field, echoing the app's own cinema open-animation. Writes PNGs (for installable
PWA / Apple touch) and a crisp SVG (for modern browsers). Run once:

    python3 scripts/make_icons.py
"""
import math, pathlib
from PIL import Image, ImageDraw

ICONS = pathlib.Path(__file__).resolve().parent.parent / "icons"
ICONS.mkdir(exist_ok=True)

BG   = (12, 10, 15)      # #0C0A0F
RED  = (224, 22, 58)     # #E0163A
WHITE = (255, 245, 246)


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def flare_color(t):
    """t in 0..1 from centre outward -> colour blended toward the background."""
    if t < 0.16:
        return WHITE
    if t < 0.42:
        return lerp(WHITE, RED, (t - 0.16) / 0.26)
    if t < 0.72:
        return lerp(RED, BG, (t - 0.42) / 0.30)
    return BG


def render(size, safe=1.0):
    """safe<1 shrinks the mark toward the centre (maskable safe zone)."""
    S = size * 4                                    # supersample for smoothness
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)
    cx = cy = S / 2
    R = (S / 2) * 0.92 * safe                       # glow radius
    # radial glow via concentric circles, outer -> inner
    steps = int(R)
    for i in range(steps, 0, -1):
        t = i / steps
        col = flare_color(t)
        r = t * R
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)

    # flare spikes on an alpha overlay, then composite
    ov = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    spike = R * 1.9
    halfw = R * 0.055
    for ang in range(0, 360, 45):
        long = ang % 90 == 0                        # N/S/E/W longer than diagonals
        L = spike if long else spike * 0.55
        a = math.radians(ang)
        tipx, tipy = cx + math.cos(a) * L, cy + math.sin(a) * L
        px, py = -math.sin(a) * halfw, math.cos(a) * halfw
        od.polygon([(cx + px, cy + py), (cx - px, cy - py), (tipx, tipy)],
                   fill=(255, 245, 246, 210 if long else 150))
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    return img.resize((size, size), Image.LANCZOS)


def main():
    render(192).save(ICONS / "icon-192.png")
    render(512).save(ICONS / "icon-512.png")
    render(512, safe=0.72).save(ICONS / "icon-maskable-512.png")

    # Hand-written SVG twin (crisp at any size).
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <rect width="512" height="512" fill="#0C0A0F"/>
  <defs>
    <radialGradient id="f" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#FFF5F6"/>
      <stop offset="16%" stop-color="#FFF5F6"/>
      <stop offset="40%" stop-color="#E0163A"/>
      <stop offset="72%" stop-color="#0C0A0F"/>
    </radialGradient>
  </defs>
  <g fill="#FFF5F6" opacity="0.82">
    <polygon points="256,40 246,256 266,256"/>
    <polygon points="256,472 246,256 266,256"/>
    <polygon points="40,256 256,246 256,266"/>
    <polygon points="472,256 256,246 256,266"/>
  </g>
  <circle cx="256" cy="256" r="216" fill="url(#f)"/>
</svg>
'''
    (ICONS / "icon.svg").write_text(svg, encoding="utf-8")
    print("wrote icon.svg, icon-192.png, icon-512.png, icon-maskable-512.png")


if __name__ == "__main__":
    main()
