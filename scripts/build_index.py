#!/usr/bin/env python3
"""Wrap the unwrapped app fragment (sofia-screen.artifact.html) into a complete,
hostable index.html: adds the <!doctype>/<html>/<head>/<body> skeleton, the PWA
bits (manifest link, theme-colour, apple-touch-icon) and the service-worker
registration.

Run this ONCE to bootstrap index.html from the artifact. After that index.html
is the canonical served file and the weekly pipeline edits its data in place —
you only re-run this if you change the app's UI/logic in the artifact.

    python3 scripts/build_index.py            # artifact -> index.html
    python3 scripts/build_index.py --out X    # build somewhere else (dev preview)

The fragment starts at <title> and its body content starts at <div id="app">.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent          # webapp/
SRC  = ROOT / "src" / "sofia-screen.artifact.html"             # dev source (in repo)
OUT  = ROOT / "index.html"

import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).parent))
from site_config import SITE_URL as _SITE_URL  # noqa: E402
import csp  # noqa: E402

HEAD = """<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="theme-color" content="#0C0A0F">
<meta name="color-scheme" content="dark">
<meta name="description" content="Програмата на кината и театрите в София, обновявана всеки ден от сайтовете им.">
<link rel="manifest" href="manifest.webmanifest">
<!-- Open Graph -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="Sofia Gleda">
<link rel="canonical" href="{SITE_URL}/">
<meta property="og:url" content="{SITE_URL}/">
<meta property="og:title" content="Sofia Gleda">
<meta property="og:description" content="Афишът на кината и театрите в София">
<meta property="og:image" content="{SITE_URL}/share/share-preview.jpg">
<meta property="og:locale" content="bg_BG">
<meta property="og:locale:alternate" content="en_US">
<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Sofia Gleda">
<meta name="twitter:description" content="Афишът на кината и театрите в София">
<meta name="twitter:image" content="{SITE_URL}/share/share-preview.jpg">
<!-- icon.svg uses embedded data: PNG tiles inside masks; browsers block nested
     data: URIs in SVG favicons (opaque origin restriction) — SVG renders blank.
     make_icons.py composites those tiles correctly into real PNGs, used here. -->
<link rel="icon" href="icons/icon-192.png" sizes="192x192" type="image/png">
<link rel="icon" href="icons/icon-512.png" sizes="512x512" type="image/png">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Sofia Gleda">
""".replace("{SITE_URL}", _SITE_URL)

SW_REG = r"""
<script>/* SOFIA-SW */
/* Register the service worker: instant repeat loads + offline. Non-fatal on
   failure — the app works without it. Registered after load so it never
   competes with first paint. Not on the old github.io address: there the
   worker retires itself before the move to sofiagleda.com (see sw.js). */
if ("serviceWorker" in navigator && !/(^|\.)github\.io$/.test(location.hostname)) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("sw.js").catch(function () {});
  });
}
</script>
</body>
</html>
"""


INCLUDE = re.compile(r"^<!-- @include ([\w.-]+) -->\n", re.M)


def assemble(path):
    """Read the UI source and inline its `<!-- @include NAME -->` lines (files
    beside it in src/). The data constants live in src/data.html so the UI source
    stays small enough to read and edit; the assembled fragment is byte-identical
    to the old single-file artifact."""
    text = path.read_text(encoding="utf-8")
    def inc(m):
        part = path.parent / m.group(1)
        if not part.exists():
            sys.exit(f"include not found: {part}")
        body = part.read_text(encoding="utf-8")
        return body if body.endswith("\n") else body + "\n"
    return INCLUDE.sub(inc, text)


def main():
    out = OUT
    if "--out" in sys.argv:
        out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]).resolve()
    if not SRC.exists():
        sys.exit(f"source not found: {SRC}")
    frag = assemble(SRC)
    # Inject SITE_URL from site_config so the page always matches the canonical URL.
    frag = frag.replace('const SITE_URL="__SITE_URL__";',
                        f'const SITE_URL="{_SITE_URL}";')

    # body content begins at the #app container; everything before it is head.
    marker = '<div id="app">'
    i = frag.find(marker)
    if i < 0:
        sys.exit('could not find <div id="app"> in the fragment')

    head_part = frag[:i].rstrip()
    body_part = frag[i:].rstrip()

    html = HEAD + head_part + "\n</head>\n<body>\n" + body_part + "\n" + SW_REG
    # The Content-Security-Policy meta (script hashes): every later step that edits
    # the page stamps it again, and refresh_all.py does it last (scripts/csp.py).
    html = csp.stamp(html)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
