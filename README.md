# Sofia Gleda 🎬🎭

A Netflix-style browser for everything showing in Sofia — cinema and theatre —
in Bulgarian, with an English toggle. One self-contained web page that installs
like an app and refreshes its own listings every three days.

**To publish it as a website, follow [SETUP.md](SETUP.md).** No coding needed.

---

## What's in this folder

| File / folder | What it is |
|---|---|
| `index.html` | **The app.** A single self-contained page — this is what visitors see. |
| `src/sofia-screen.artifact.html` | Dev source of the UI/logic (~125 KB). Edit this, not `index.html`. |
| `src/data.html` | Seed data constants (JSON). Contains very long lines — edit only via Python scripts, never by hand. |
| `manifest.webmanifest` | Makes it installable as a phone/desktop app (PWA). |
| `sw.js` | Service worker — instant repeat loads and offline use. |
| `icons/` | App icons (the red projector-flare mark). |
| `scripts/` | The data pipeline (Python). See below. |
| `requirements.txt` | Python packages the robot installs automatically. |
| `.github/workflows/refresh.yml` | The refresh robot (GitHub Actions, every 3 days). |
| `SETUP.md` | Step-by-step hosting guide for a non-technical user. |

Generated data files that appear after the first refresh:
`tmdb_films.json`, `film_info.json`, `theatre_posters.json`, `changes.json`, `build_report.json`.

---

## The pipeline

Run in order by `scripts/refresh_all.py` (which the robot calls every 3 days):

1. **`scrape_programs.py`** — re-reads cinema (programata.bg + venue sites) and
   theatre (theatre.art.bg, artvent.bg + venue sites) programmes and edits the
   showtimes/performances and per-venue ticket links (`VLINKS`) into `index.html`.
   A dead or stale source **keeps the previous data** for that venue rather than
   emptying it.
2. **`fetch_film_info.py`** — for films that are still missing a synopsis, director,
   or cast after the seed, this fetches their own programme page (programata,
   Vlaikova, NDK) and parses the details. Writes `film_info.json`; inlined as
   `FILMINFO`.
3. **`inject_films.py`** / **`inject_shows.py`** — merge synthesised arthouse film
   and theatre show entries.
4. **`fetch_tmdb.py`** — matches each film to TMDB for real posters, English titles,
   director, cast, and Bulgarian overview. Needs the `TMDB_TOKEN` secret.
5. **`fetch_theatre_posters.py`** — best-effort theatre posters from theatre.art.bg.
6. **`inject_data.py`** — inlines the poster and details maps into `index.html`.

Every step is best-effort and isolated: one failure never empties the app, and
the run is logged to `build_report.json`.

### Making a UI change

Edit `src/sofia-screen.artifact.html`. To preview without a full scrape:

```bash
cd webapp
python3 scripts/dev_build.py        # -> index.dev.html (offline, no tokens needed)
```

This builds with today's UI source and the listings from the last committed
`index.html`. Never deploy `index.dev.html`.

Once you are happy, build for real and run the full pipeline:

```bash
python3 scripts/build_index.py
TMDB_TOKEN=your_token OMDB_TOKEN=your_key python3 scripts/refresh_all.py
# must end "all checks passed — safe to publish"
```

`scripts/make_icons.py` regenerates the PWA icons.

### Running it on your own Mac (optional)

```bash
cd webapp
pip install -r requirements.txt
python3 -m playwright install chromium   # for the verify gate
TMDB_TOKEN=your_token OMDB_TOKEN=your_key python3 scripts/refresh_all.py
```

---

## Design notes for whoever works on this next

- **One file, no blocking external requests.** The dataset is inlined in
  `index.html` between the `SOFIA-DATA` markers; posters between the
  `SOFIA-POSTERS` markers. A stalled external script must never be able to hang
  first paint — see the fuller briefing in `HANDOVER.md` (kept outside the repo).
- **The UI source is split.** `src/sofia-screen.artifact.html` (~125 KB) holds the
  CSS and JS; `src/data.html` holds the seed data constants (SOFIA-DATA +
  SOFIA-POSTERS blocks). `build_index.py` inlines `data.html` via
  `<!-- @include data.html -->` before assembling `index.html`. `src/data.html`
  contains single lines up to 77 KB — edit it only via Python scripts, never by
  hand or with a text editor.
- **`VLINKS` is how film ticket links work.** `VLINKS = [[filmId, venueId, url], …]`
  holds film-specific links on each venue's own domain (allowlist enforced by the
  scraper and verified by the gate). `verify_build.py` fails if programata.bg
  appears in VLINKS, or if any upcoming (film, venue) has no ticket URL at all.
  The per-film `LINKS` array is the info-page fallback (and is used for theatre
  show links); it is not used for cinema ticket links.
- **All date maths is UTC.** Local-time parsing reintroduces a start-up hang in
  timezones east of Greenwich. Don't change this without testing.
- **Posters are remote TMDB URLs**, so they load on any hosted site; every card
  falls back to generated SVG art when unmatched.
- **Never put hand-written code between the `SOFIA-POSTERS` markers.**
  `scripts/inject_data.py` regenerates that whole block on every refresh, so
  anything living inside it is deleted. This is not hypothetical: the weekly
  refresh of 2026-09-16 swallowed `const PRICES` that way. The page still
  rendered — and threw `PRICES is not defined` the moment anyone clicked a film,
  so the listings looked frozen and no ticket could be bought. `PRICES` now lives
  in its own `<script>` just below the block, and `inject_data.py` refuses to run
  if it finds a stray declaration inside the markers.
- **Nothing is published until it has been clicked.** `scripts/verify_build.py`
  runs last in the pipeline and is a gate, not a report: it checks that every
  global the app reads is declared, that the JS parses, that showtimes resolve to
  real films and venues, that no dataset has collapsed, that VLINKS contains no
  disallowed hosts, and then opens the page in a headless browser and actually
  clicks a film card and a performance. If any of that fails the workflow stops
  before the commit, the rejected build is uploaded as an Action artifact, and the
  live site keeps its last good version.
- **All HTTP goes through `scripts/netfetch.py`.** Browser-shaped headers, retries
  with backoff, split connect/read timeouts, per-host politeness, a wall-clock
  budget, and a per-host report at the end of every run. One attempt per URL with
  a self-identifying bot User-Agent is what turned a single slow response into
  "13 sources unreachable".
- **Dates come in words.** programata.bg writes `17 септември |четвъртък|: 14:20 |
  19:30`, and every other Bulgarian site mixes that with `18.09`. `find_date()`
  reads all of it and infers the year from the snapshot window, so a December run
  rolls into January correctly. The per-film programata parser is separate and
  stricter — its time group must be an explicit run of `HH:MM`, or it runs past
  the line end and swallows the next date's day number.
- **"Unreachable" and "stale" are different facts.** `changes.json` reports them
  apart: `unreachable` means the page never arrived, `stale` means it arrived and
  nothing parsed — markup drift, not the network.
  `python3 scripts/scrape_programs.py --diagnose` prints the per-venue table
  (fetched / days / rows / title matches) and writes nothing.
- **One rule decides which poster may attach to which id** (`scripts/posterpolicy.py`).
  Provenance: a poster comes from a host that publishes one image per production,
  or else its filename says what it is; an opaque hash on an unknown host is
  refused. Identity: a cinema-scope event that mirrors a catalogued film uses that
  film's TMDB poster, never a separately harvested one. Every merge tier is
  filtered — last week's JSON, this week's scrape, and `SEED`. SEED still wins on
  precedence; it is no longer exempt from the check, which is exactly why one
  wrong hand-added URL survived every refresh and put another film's artwork on
  the Oasis screening.
- **`POSTER_ALIAS` is generated, not hand-written.** `inject_data.py` computes
  `SHOWALIAS` from the catalogue on every run, so a limited screening added next
  month is aliased the day it appears.
- **`scripts/test_parsers.py` pins all of this offline**, in about a second, and
  runs as a blocking workflow step before the refresh. `scripts/test_ui.py` is the
  Playwright UI regression suite (run against `index.dev.html` or `index.html`).
