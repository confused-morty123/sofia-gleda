#!/usr/bin/env python3
"""Merge the synthesised theatre shows into the SHOWS catalogue in index.html.

The scraper (scrape_programs.py) reads venue programmes whose productions are
mostly shows the hand-curated SHOWS catalogue never carried — which is why
Театър 199, Топлоцентрала, Зад канала, Сфумато and Сити Марк looked empty. Rather
than drop those titles, it mints a minimal, source-faithful record for each one
and persists them to theatre_shows.json (keep-previous: a title survives a week
when its source is briefly down). This step merges those records into the SHOWS
array inside index.html so the app can render them.

Rules:
  * Append only — a show whose id is already in SHOWS is left untouched, so a
    hand-curated catalogue entry is never overwritten by a thinner synthesised
    one, and re-running never duplicates.
  * Never invents metadata: a minted entry carries only what the scraper could
    read (id, title, venue, genres, a placeholder gradient). Missing author /
    director / cast / duration / synopsis are simply absent; the UI guards for it.

    python3 scripts/inject_shows.py            # edits index.html in place
"""
import json, os, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
THEATRE_SHOWS = ROOT / "theatre_shows.json"

# Fields the SHOWS catalogue entries carry; a minted record is normalised to this
# shape so a thin entry never trips a renderer that reads a field unguarded.
SHOW_FIELDS = ("id", "title", "titleEn", "theatre", "author", "director", "cast",
               "genres", "g", "duration", "synBg", "synEn")


def extract_array(src, name):
    """Return (start, end, literal) of `const <name>=[ ... ]`, matching brackets
    while ignoring any that fall inside string literals."""
    m = re.search(rf"const\s+{name}\s*=\s*\[", src)
    if not m:
        raise KeyError(name)
    start = m.end() - 1
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(src):
        c = src[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == quote: in_str = False
        elif c in "\"'": in_str, quote = True, c
        elif c == "[": depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return start, i + 1, src[start:i + 1]
        i += 1
    raise ValueError(f"unterminated array {name}")


def normalise(rec):
    out = {}
    for k in SHOW_FIELDS:
        v = rec.get(k)
        if k == "genres":
            out[k] = v if isinstance(v, list) else []
        elif k == "g":
            out[k] = v if isinstance(v, list) and len(v) == 2 else ["#241B33", "#45275F"]
        else:
            out[k] = v if v is not None else ""
    return out


def main():
    if not THEATRE_SHOWS.exists():
        print(f"no {THEATRE_SHOWS.name} — nothing to merge")
        return
    try:
        minted = json.loads(THEATRE_SHOWS.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"could not read {THEATRE_SHOWS.name}: {e}")
    if not minted:
        print(f"{THEATRE_SHOWS.name} is empty — nothing to merge")
        return

    src = HTML.read_text(encoding="utf-8")
    start, end, literal = extract_array(src, "SHOWS")
    shows = json.loads(literal)                      # SHOWS is strict JSON
    have = {s.get("id") for s in shows}

    added = 0
    for sid, rec in minted.items():
        if sid in have:
            continue
        shows.append(normalise(rec))
        have.add(sid)
        added += 1

    if not added:
        print(f"SHOWS already carries all {len(minted)} theatre shows — no change")
        return

    new_literal = json.dumps(shows, ensure_ascii=False, separators=(",", ":"))
    HTML.write_text(src[:start] + new_literal + src[end:], encoding="utf-8")
    print(f"merged {added} theatre show(s) into SHOWS "
          f"({len(shows)} total; {len(minted)} synthesised)")


if __name__ == "__main__":
    main()
