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

HEAD = """<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0C0A0F">
<meta name="color-scheme" content="dark">
<meta name="description" content="Sofia Gleda — what's on in Sofia: cinema and theatre in one place.">
<link rel="manifest" href="manifest.webmanifest">
<!-- icon.svg uses embedded data: PNG tiles inside masks; browsers block nested
     data: URIs in SVG favicons (opaque origin restriction) — SVG renders blank.
     make_icons.py composites those tiles correctly into real PNGs, used here. -->
<link rel="icon" href="icons/icon-192.png" sizes="192x192" type="image/png">
<link rel="icon" href="icons/icon-512.png" sizes="512x512" type="image/png">
<link rel="apple-touch-icon" href="icons/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Sofia Gleda">
"""

SW_REG = """
<script>
/* Register the service worker: instant repeat loads + offline. Non-fatal on
   failure — the app works without it. Registered after load so it never
   competes with first paint. */
if ("serviceWorker" in navigator) {
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

    # body content begins at the #app container; everything before it is head.
    marker = '<div id="app">'
    i = frag.find(marker)
    if i < 0:
        sys.exit('could not find <div id="app"> in the fragment')

    head_part = frag[:i].rstrip()
    body_part = frag[i:].rstrip()

    html = HEAD + head_part + "\n</head>\n<body>\n" + body_part + "\n" + SW_REG
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
