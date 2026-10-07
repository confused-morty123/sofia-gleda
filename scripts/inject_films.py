#!/usr/bin/env python3
"""Merge the synthesised arthouse films into the FILMS catalogue in index.html.

The scraper (scrape_programs.py) screens arthouse cinemas whose programmes are
mostly films the hand-curated FILMS catalogue never carried. Rather than drop
those titles (the old behaviour, which left Одеон, Дом на киното and Влайкова
looking almost empty), it mints a minimal, source-faithful record for each one
and persists them to cinema_films.json (keep-previous: a title survives a week
when its source is briefly down). This step merges those records into the FILMS
array inside index.html so the app can render them.

Rules:
  * Append only — a film whose id is already in FILMS is left untouched, so a
    hand-curated catalogue entry is never overwritten by a thinner synthesised
    one, and re-running never duplicates.
  * Never invents metadata: a minted entry carries only what the scraper could
    read (id, Bulgarian title, genres, a placeholder gradient). Missing year /
    runtime / director / cast are simply absent; the UI guards for that.

    python3 scripts/inject_films.py            # edits index.html in place
"""
import json, os, re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = pathlib.Path(os.environ.get("SOFIA_HTML", ROOT / "index.html"))
CINEMA_FILMS = ROOT / "cinema_films.json"
FILM_INFO = ROOT / "film_info.json"


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


def main():
    if not CINEMA_FILMS.exists():
        print(f"no {CINEMA_FILMS.name} — nothing to merge")
        return
    try:
        cinema = json.loads(CINEMA_FILMS.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"could not read {CINEMA_FILMS.name}: {e}")
    if not cinema:
        print(f"{CINEMA_FILMS.name} is empty — nothing to merge")
        return

    # Load film_info.json to fill empty genres on synthesised films (never
    # overwrite a film that already has genres, and never invent metadata).
    filminfo: dict = {}
    if FILM_INFO.exists():
        try:
            filminfo = json.loads(FILM_INFO.read_text(encoding="utf-8"))
        except Exception:
            filminfo = {}

    src = HTML.read_text(encoding="utf-8")
    start, end, literal = extract_array(src, "FILMS")
    films = json.loads(literal)                      # FILMS is strict JSON
    have = {f.get("id") for f in films}

    # Build a lookup of synthesised film ids for the genre-patch pass.
    synth_ids = set(cinema.keys())

    added = 0
    for fid, rec in cinema.items():
        if fid in have:
            continue
        # Fill empty genres from film_info.json when inserting a new film.
        if not rec.get("genres") and fid in filminfo:
            genres = filminfo[fid].get("genres")
            if genres and isinstance(genres, list):
                rec = dict(rec)
                rec["genres"] = genres
        films.append(rec)
        have.add(fid)
        added += 1

    # Second pass: patch EMPTY genres on synthesised films that were already
    # present in FILMS (the common case on refresh-only runs).  Never touch
    # seed films (only ids from cinema_films.json), never overwrite a non-empty
    # genres list.
    patched = 0
    for entry in films:
        fid = entry.get("id")
        if fid not in synth_ids:
            continue                       # seed film — never touch
        if entry.get("genres"):
            continue                       # already has genres — leave as is
        fi = filminfo.get(fid, {})
        genres = fi.get("genres")
        if genres and isinstance(genres, list):
            entry["genres"] = genres
            patched += 1

    if not added and not patched:
        print(f"FILMS already carries all {len(cinema)} arthouse films — no change")
        return

    new_literal = json.dumps(films, ensure_ascii=False, separators=(",", ":"))
    HTML.write_text(src[:start] + new_literal + src[end:], encoding="utf-8")
    parts = []
    if added:
        parts.append(f"merged {added} arthouse film(s)")
    if patched:
        parts.append(f"patched genres on {patched} existing synthesised film(s)")
    print(f"{'; '.join(parts)} "
          f"({len(films)} total; {len(cinema)} synthesised)")


if __name__ == "__main__":
    main()
