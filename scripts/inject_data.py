#!/usr/bin/env python3
"""Inline the poster maps into index.html between the SOFIA-POSTERS markers.

  TMDBART: id -> {p:poster_path, b:backdrop_path, en:"English title"}  (from tmdb_films.json)
  SHOWART: theatre show id -> full poster URL                          (from theatre_posters.json)

Idempotent: regenerates the whole block each run. Missing ids keep the app's
generated SVG artwork.

    python3 scripts/inject_data.py            # edits index.html in place
"""
import json, os, re, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
TMDB_JSON = ROOT / "tmdb_films.json"
SHOW_JSON = ROOT / "theatre_posters.json"

tmdb = json.load(open(TMDB_JSON, encoding="utf-8")) if TMDB_JSON.exists() else {}
shows = json.load(open(SHOW_JSON, encoding="utf-8")) if SHOW_JSON.exists() else {}

art = {}
for fid, v in tmdb.items():
    if not v.get("matched"):
        continue
    rec = {}
    if v.get("poster_path"):   rec["p"] = v["poster_path"]
    if v.get("backdrop_path"): rec["b"] = v["backdrop_path"]
    if v.get("en"):            rec["en"] = v["en"]
    if rec:
        art[fid] = rec

showart = {k: v for k, v in shows.items() if v}

header = ('/* Sofia Gleda — real poster artwork.\n'
    '   POSTERS: id -> data: URI override (base64, any web format). Highest priority.\n'
    '   TMDBART: id -> {p:poster_path, b:backdrop_path, en:"English title"} from TMDB;\n'
    '            paths resolve to https://image.tmdb.org/t/p/<size><path> at render time,\n'
    '            so posters load on any hosted/live web-app or mobile webview.\n'
    '   SHOWART: theatre show id -> full poster URL (best-effort, theatre.art.bg).\n'
    '   Missing ids keep the generated SVG artwork. */')

block = ("<script>/* SOFIA-POSTERS-START */\n"
    + header + "\n"
    + "const POSTERS = {};\n"
    + "const TMDBART = " + json.dumps(art, ensure_ascii=False, separators=(",", ":")) + ";\n"
    + "const SHOWART = " + json.dumps(showart, ensure_ascii=False, separators=(",", ":")) + ";\n"
    + "/* SOFIA-POSTERS-END */</script>")

data = HTML.read_text(encoding="utf-8")

MARKERS = r"<script>/\* SOFIA-POSTERS-START \*/.*?/\* SOFIA-POSTERS-END \*/</script>"
found = re.search(MARKERS, data, flags=re.S)
assert found, "marker block not found in " + str(HTML)

# This block is REGENERATED WHOLESALE, so anything hand-written inside it is
# destroyed on every run. That is exactly how `const PRICES` was lost in the
# 2026-09-16 refresh and every film became unclickable. Refuse to run rather
# than silently delete app code again.
GENERATED = {"POSTERS", "TMDBART", "SHOWART"}
declared = set(re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=", found.group(0), flags=re.M))
stray = declared - GENERATED
if stray:
    raise SystemExit(
        "refusing to overwrite the poster block: it also declares "
        + ", ".join(sorted(stray))
        + ".\nThose are not generated here and would be erased. Move them into "
          "their own <script> outside the SOFIA-POSTERS markers, then re-run.")

new = re.sub(MARKERS, lambda m: block, data, count=1, flags=re.S)
HTML.write_text(new, encoding="utf-8")
print(f"injected TMDBART: {len(art)} films ({sum(1 for r in art.values() if 'p' in r)} with posters), "
      f"SHOWART: {len(showart)} shows")
