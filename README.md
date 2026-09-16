# Sofia Gleda 🎬🎭

A Netflix-style browser for everything showing in Sofia — cinema and theatre —
in Bulgarian, with an English toggle. One self-contained web page that installs
like an app and refreshes its own listings every week.

**To publish it as a website, follow [SETUP.md](SETUP.md).** No coding needed.

---

## What's in this folder

| File / folder | What it is |
|---|---|
| `index.html` | **The app.** A single self-contained page — this is what visitors see. |
| `manifest.webmanifest` | Makes it installable as a phone/desktop app (PWA). |
| `sw.js` | Service worker — instant repeat loads and offline use. |
| `icons/` | App icons (the red projector-flare mark). |
| `scripts/` | The weekly data pipeline (Python). See below. |
| `requirements.txt` | Python packages the robot installs automatically. |
| `.github/workflows/refresh.yml` | The weekly-refresh robot (GitHub Actions). |
| `SETUP.md` | Step-by-step hosting guide for a non-technical user. |

Generated data files that appear after the first refresh:
`tmdb_films.json`, `theatre_posters.json`, `changes.json`, `build_report.json`.

---

## The weekly pipeline

Run in order by `scripts/refresh_all.py` (which the robot calls every Sunday):

1. **`scrape_programs.py`** — re-reads cinema (programata.bg + venue sites) and
   theatre (theatre.art.bg, artvent.bg + venue sites) programmes and edits the
   showtimes/performances into `index.html`. A dead or stale source **keeps last
   week's data** for that venue rather than emptying it.
2. **`fetch_tmdb.py`** — matches each film to TMDB for real posters and English
   titles. Needs the `TMDB_TOKEN` secret.
3. **`fetch_theatre_posters.py`** — best-effort theatre posters from
   theatre.art.bg.
4. **`inject_data.py`** — inlines the poster maps into `index.html`.

Every step is best-effort and isolated: one failure never empties the app, and
the run is logged to `build_report.json`.

### Running it on your own Mac (optional)

```bash
cd webapp
pip install -r requirements.txt
TMDB_TOKEN=your_token python3 scripts/refresh_all.py
```

`scripts/build_index.py` regenerates `index.html` from the unwrapped
`sofia-screen.artifact.html` if you change the app's UI; `scripts/make_icons.py`
regenerates the icons.

---

## Design notes for whoever works on this next

- **One file, no blocking external requests.** The dataset is inlined in
  `index.html` between the `SOFIA-DATA` markers; posters between the
  `SOFIA-POSTERS` markers. A stalled external script must never be able to hang
  first paint — see the fuller briefing in `HANDOFF.md` (kept with the dev source).
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
  real films and venues, that no dataset has collapsed, and then opens the page in
  a headless browser and actually clicks a film card and a performance. If any of
  that fails the workflow stops before the commit, the rejected build is uploaded
  as an Action artifact, and the live site keeps its last good version.
