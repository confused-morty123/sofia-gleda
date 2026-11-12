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
| `.github/workflows/refresh.yml` | The refresh robot (GitHub Actions, every night at 03:05 Sofia time, see below). |
| `SETUP.md` | Step-by-step hosting guide for a non-technical user. |

Generated data files that appear after the first refresh:
`tmdb_films.json`, `person_names.json` (TMDB's Bulgarian spellings of the credited people, by person id), `film_info.json`, `theatre_posters.json`, `changes.json`, `build_report.json`.

Festival screenings' sale states in `FESTROWS` (sold out, not on sale yet) are read once per nightly refresh and stamped with the day they were read; between refreshes an on-sale screening keeps "Купи билет", and the seller's page shows the live availability. An epaygo.bg link is attached to a festival screening only after that epaygo page's own title was read as the screening's film (two festival films can share one hour at G8 and Euro Cinema); the title is stored in `FESTROWS` and the build gate checks it. When a festival screening's venue programme and its ticket seller disagree on the start time (Одеон 20:00, CineLibri and epaygo.bg 20:30), the app shows the seller's time (the epaygo page's „Начален час“ with its film and place checked, else the festival's own day page), never both, and only within 90 minutes of the venue's; the seller's time is stored in `FESTROWS`, each correction is listed in the build report (`festivals.<festival>.seller_time`), and the build gate fails on any row that shows another time than its seller's.

---

## When the refresh runs

The refresh starts at 03:05 Sofia time (summer and winter time alike) and takes
about 35 minutes. GitHub starts a repository's scheduled runs late, and on this
repository by hours: in October 2026 a 03:05 cron started at 08:00 to 09:00 Sofia
time, later each day. So `.github/workflows/refresh.yml` fires five times from the
evening before (16:17, 18:17, 20:17, 22:17 and 00:47 Sofia) and its first job,
`scripts/schedule_gate.py`, decides what each firing does:

- from 21:15 to 03:05 it sleeps until 03:05, then refreshes (a job may run 6 hours);
- from 03:05 to 21:15 it refreshes at once if tonight's refresh is not done
  (late is better than never; a refresh that failed the publish gate is retried);
- it stops when an earlier firing is already waiting, and the refresh job checks
  again before it starts whether the night the gate chose is already refreshed
  (only that, never the time of day), so one refresh runs per night ("done" = a
  bot commit "Scheduled refresh: ..." on main after 03:00 Sofia);
- the manual "Run workflow" button, and "Re-run jobs" on any run, always refresh
  at once.

With these five firings the refresh starts at 03:05 whenever GitHub's delay is
under about 10 h 45 min, before 04:00 up to 11 h 30 min, and still runs every
night for any delay under a day. Every time is Europe/Sofia, so both clock-change
nights work: on the last Sunday of October the first 03:05 (summer time) is used;
on the last Sunday of March 03:05 does not exist and the refresh starts at 04:05.
Tests: `python3 scripts/test_schedule.py`.

The cost: on every night one GitHub runner sleeps for up to 6 hours and four
short gate jobs (about 20 seconds each) start and stop, all free for a public
repository. If GitHub ever delays scheduled runs by more than half a day, the only
dependable fix is outside GitHub: an external cron service (for example
cron-job.org) that calls the workflow's `workflow_dispatch` API at 03:05 Sofia
with a fine-grained token limited to this repository's Actions (read and write).
That needs the owner to create the token; it is not set up.

## The pipeline

Run in order by `scripts/refresh_all.py` (which the robot calls every night):

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
6. **`fetch_events.py`** — festivals and limited runs that programata.bg announces
   (its festival categories, only posts with dates and in Sofia) into `events.json`;
   keeps the previous events when the site is down. Shown in "Лимитирано и
   ексклузивно" with a link to the source page.
7. **`fetch_event_posters.py`** — key art for the events shown (the festival's own
   site, else the programata post's featured image; logos, small images and strips
   refused), re-hosted as `posters/ev-<id>.jpg`; sources in `event_posters.json`.
8. **`inject_data.py`** — inlines the poster and details maps, and the auto events
   (`EVENTS_AUTO`, `EVENT_MEMBERS`, `EVENTART`), into `index.html`.
9. **`gen_share_pages.py`**, then **`csp.py`** — the share pages, then the
   Content-Security-Policy hashes of every page, after the last edit.

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

Once you are happy, build for real with live data and run the gate:

```bash
python3 scripts/dev_build.py --from HEAD --out index.html
python3 scripts/verify_build.py              # checks index.html (SOFIA_HTML=<file> for another file)
# must end "all checks passed — safe to publish"
```

> **Note:** `scripts/build_index.py` alone produces an empty-listing page because
> `src/data.html` no longer carries seed listings.  Always use `dev_build.py` (which
> reads listings from the last committed `index.html` or HEAD) when working locally.

`scripts/make_icons.py` regenerates the PWA icons.

### Visitor statistics (Google Analytics 4)

The Measurement ID lives in one place: `GA_MEASUREMENT_ID` in `scripts/site_config.py`
(today `G-SQWMGX9GL3`). Empty means analytics off: no script, no "Статистика" switch
in the cookie notice, and the policies keep saying the site keeps no statistics.
With an ID the app asks first (Consent Mode v2, everything denied, nothing sent to
Google before the visitor switches "Статистика" on), and the privacy and cookie
policies describe GA4. After changing the ID, rebuild in pipeline order:

```bash
python3 scripts/dev_build.py --from origin/main --out index.html
python3 scripts/gen_share_pages.py && python3 scripts/gen_legal_pages.py && python3 scripts/csp.py
SOFIA_HTML=index.html python3 scripts/verify_build.py
```

`SOFIA_GA_MEASUREMENT_ID=...` overrides the ID for a local test build (empty = off);
never commit a build made that way. In Google Analytics set Admin > Data collection >
Data retention to 2 months (the privacy policy says so) and leave Google signals off.
Also, in Admin > Data streams > the stream > Enhanced measurement, switch enhanced
measurement off (or at least, under Page views > advanced settings, "Page changes based
on browser history events"): the app sends its own page view per route, so with it on
every route change counts twice, and the other automatic events (scrolls, outbound
clicks, site search, form interactions, video, file downloads) are not in the privacy
policy's list. Keep Admin > Data collection > "User-provided data collection" off.
The four settings the privacy policy relies on (Admin, property column, "Data collection and modification"): Data streams > the stream > Enhanced measurement: OFF; Data collection > User-provided data collection: OFF; Data retention > Event data retention: 2 months (Save); Data collection > Google signals data collection: OFF.

### Running it on your own Mac (optional)

```bash
cd webapp
pip install -r requirements.txt
python3 -m playwright install chromium   # for the verify gate
TMDB_TOKEN=your_token OMDB_TOKEN=your_key python3 scripts/refresh_all.py
```

### Tests in GitHub Actions

`.github/workflows/tests.yml` runs the suites on GitHub's machines, so checking a
change no longer ties up the Mac for hours. It starts on every push to any branch,
on pull requests, and from the **Run workflow** button (Actions tab > Tests). It
never scrapes and never publishes: the page is built offline from the listings
already committed (`dev_build.py --from HEAD`, then the share pages, legal pages and
CSP), as in "Making a UI change". The nightly refresh's own commits do not start it,
and `[skip ci]` in a commit message skips it. A push that changes only `.md` files
is not tested.

Four jobs:

- **unit**: the offline build, the publish gate (`verify_build.py`, without the
  36-hour freshness check: the data is whatever was last committed), then
  `test_parsers`, `test_theatres`, `test_verify`, `test_schedule`, `test_deploy_guard`.
  Every test runs even when an earlier one failed, so one run shows all failures.
  A failure is shown, not hidden: the step goes red and so does the job. Only the
  publish-gate step skips the freshness check (`SOFIA_SKIP_FRESHNESS=1` on that
  step alone): `test_verify` checks that a stale build fails the gate, so the flag
  is not set for the whole job. About 4 minutes.
- **security (chromium)** and **security (webkit)**: `test_security`, one engine
  each (`SOFIA_BROWSER`), in parallel; about 6 minutes each.
- **plan**: `scripts/ci_waves.py` splits the waves of `scripts/test_ui.py` into
  shards of at most about 8 minutes of tests each (a shard takes about 10 minutes with
  setup; B24 and HF1 are longer and get a shard of their own).
- **ui (chromium n/N)** and **ui (webkit n/N)**: one runner per shard, all in
  parallel, real mode (`SOFIA_HTML=index.html SOFIA_SKIP_FRESHNESS=1`). Chromium
  runs every wave; WebKit runs the waves written for both engines (`WEBKIT_WAVES` in
  `ci_waves.py`: HF1, HF2, UX1, B24, BO1, DM1, GA1, HS1, LG1, PL1). Each wave is its
  own process; a wave that fails on the known 30-second page-load timeout is re-run
  once. A failing shard does not stop the others.

The runners are set up as the Mac: `TZ=Europe/Sofia` (the checks compute "today" and
midnights in Python), a UTF-8 locale, Playwright 1.62.0 from `requirements.txt` with
its browsers and their system libraries (apt gets network timeouts and three tries),
Chromium on Linux started with `--font-render-hinting=none` (unhinted text sets at the
Mac's widths). Each browser context still gets its own viewport, Europe/Sofia, bg-BG
and the frozen clock from the test. What differs and how the tests cope: headless
Chromium on Linux ignores synthesized touch gestures, so touch swipes are sent as CDP
touch events (`touch_swipe`); the machines are slower, so every page helper waits until
the page draws steadily (`wait_page_idle`) and animations are recorded frame by frame
in the page (`frames`), not polled from Python; WebKit on Linux snaps a rail that
Safari leaves between two cards, so UX1 asks for the off-a-card rest only on macOS.

**Reading the results.** Open the run (Actions tab, or the commit's check mark).
The run's summary page has the shard plan and, per shard, a table of waves with
pass counts and times, and the failed checks. A failed shard also keeps its logs
(one per wave) and screenshots as an artifact `ui-logs-<engine>-<n>` for 14 days.
Without a browser, read the same through the public API (no token needed for this
public repository):

```bash
R=https://api.github.com/repos/confused-morty123/sofia-gleda
curl -s "$R/actions/workflows/tests.yml/runs?branch=<branch>&per_page=1"   # status, conclusion, id
curl -s "$R/actions/runs/<run id>/jobs?per_page=100"                       # each job and step, pass or fail
curl -s "$R/commits/<sha>/check-runs"                                       # the same, by commit
curl -s "$R/check-runs/<job id>/annotations"                                # failed waves and their check names
```

Each failed wave leaves one annotation naming its failed checks and a second with
each failed check's detail (or, for a wave that crashed, the end of its log), and each
shard one with its waves' times, so the last call tells which checks broke and why.
Job logs and artifacts need a signed-in download (the API answers 403 without a
token); jobs, steps, conclusions and annotations are public.

**Running a subset (smoke run).**

- Run workflow button: fill **waves** (for example `HF1 HS1 B24`), pick the
  **browsers**, and untick **unit** to skip the unit and security jobs.
- From a push: add a line to the commit message, `CI-Waves: HF1 HS1` (and,
  if wanted, `CI-Browsers: webkit`). With both engines WebKit runs only the
  named waves that are in `WEBKIT_WAVES`; `CI-Browsers: webkit` runs every named
  wave in WebKit. The unit job always runs on a push.
- An unknown wave name fails the plan job at once (`test_ui.py` itself would
  skip it silently).

Locally, `python3 scripts/ci_waves.py list` shows every wave with its estimated
time, `python3 scripts/ci_waves.py matrix --waves "HF1 HS1"` shows the shards a
run would get, and `python3 scripts/ci_waves.py run --browser webkit HF1` runs
waves the way CI does. After a new wave lands, add its time (printed by `run` on
CI) to `ESTIMATES` in `ci_waves.py`; until then it counts as 2 minutes.

## The deploy guard

GitHub Pages publishes the site with GitHub's own workflow, **pages build and
deployment**, after every commit to `main`. Its *build* job turns the committed
`index.html` into the deployment and almost always works; its *deploy* job then
hands that to the CDN, and that step has twice stalled with "Timeout reached,
aborting!" although the build succeeded — on 2026-11-09 (fixed by hand with
**Re-run failed jobs**) and on 2026-11-10 (nobody re-ran it, so for 24 hours the
site served the previous day's programme). `.github/workflows/deploy-guard.yml`
does that re-run itself and then checks that the live site really caught up.

**What it does.** The guard wakes when a Pages run completes, once a day on a
timer (04:23 Sofia), and from its own **Run workflow** button. Each time it looks
at how things are *now*, never at the event that woke it, so a lost or late event
changes nothing:

- if `main` has moved on since, it does nothing — the newer commit's own
  deployment wakes the guard again (re-running an older commit's deployment would
  put *old* content live, so this is never allowed);
- if that commit's deploy job failed or timed out, it asks GitHub to re-run the
  failed jobs, **at most twice per commit** (whoever asks and however often), and
  only while that commit is still `main`'s tip — checked right before asking and
  again right after, so a commit landing mid-ask cancels the re-run;
- after a deployment succeeds (first try or a re-run), it fetches
  `https://sofiagleda.com/index.html` (a cache-busting query, politely, over about
  five minutes) and compares the live page's `SNAPSHOT.generated` stamp with the
  committed one.

**What the owner sees.** When everything is fine, nothing — the guard's run is
green and silent. When the guard gives up (both re-runs failed, or the site is
still stale after five minutes, or GitHub's API could not be read), its run goes
**red** and GitHub e-mails the person who owns the schedule, with a one-line
message in the run's summary saying exactly what to click — normally Actions >
*pages build and deployment* > the **top** run > *Re-run failed jobs*, or Actions
> *Daily refresh* > *Run workflow* to publish a fresh deployment. The guard holds
no secrets, installs nothing, reads only `main`'s own `deploy_guard.py` and
`index.html`, and the only writes it can ask GitHub for are "re-run failed jobs"
and "cancel". Its decision logic is pure and offline-tested:
`scripts/deploy_guard.py` (`decide(...)`), `python3 scripts/test_deploy_guard.py`.

**On release.** `workflow_run` only fires for a workflow file that is on the
default branch, so the guard cannot run at all — and this trigger cannot be
proven — until `deploy-guard.yml` is merged to `main`. Everything else (the
decision table, the retry bound, the live-stamp check, the YAML) is proven
offline by the tests. After the first commit to `main` that follows the release,
open Actions > **Deploy guard**: a run should appear shortly after that commit's
*pages build and deployment* finishes, and end green with a summary line like
"GitHub reports the deployment of `<sha>` as successful; https://sofiagleda.com
serves its data". If no Deploy guard run appears, check that the Pages workflow
is named *pages build and deployment* (the `workflows:` list in the trigger must
match its name) and that Actions are enabled for the repository.

## Play synopses: thin texts, fills and the owner's overrides

Many plays reach the listing with only a line or no synopsis. Each night
`scripts/synopsis_health.py` counts, per theatre and for films, the titles whose
Bulgarian synopsis is shorter than 280 characters (`synopses.THIN`) and writes
`synopsis_health.json` / `synopsis_health.md` (also `build_report.json["synopsis_health"]`).
It never blocks a publish.

Longer texts come only from the same production's own pages:

- `scripts/synopses.py` reads the production page on **theatre.art.bg** where the
  show's own link already points there (Театър 199, Ателие 313, Сити Марк). The
  text is taken only when the page's title equals the show's and the page names the
  show's theatre; it is kept in `synopsis_fills.json` with `source_url` and
  `fetched_on`, and replaces the shown text only while that is under 280 characters.
- **tickets.mlt.bg** (Младежки театър) has no description to read: its event links
  require sign-in. mlt.bg itself is behind a Cloudflare challenge, which we never bypass.
- **Owner overrides** — `scripts/synopsis_overrides.json`. For a play whose theatre's
  page we cannot or may not read, open that page in your own browser, copy the
  description and add:

  ```json
  "ee": {"bg": "…the theatre's own text…", "source_url": "https://mlt.bg/ee___8013",
         "copied_on": "2026-11-07"}
  ```

  `"en"` is optional (only a real English text, never a machine translation). An
  override always wins. Entries without `bg`, an https `source_url` or a
  `YYYY-MM-DD` `copied_on` are skipped and reported. Rebuild (or wait for the
  nightly refresh) to see it.

On the play's sheet, a synopsis that is still short gets a quiet link under it —
"Пълно описание на сайта на театъра ↗" / "Full description on the theatre's site ↗" —
when the show has its own page on its theatre's site (not an aggregator listing).

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
- **The page runs only its own scripts.** Each page carries a Content-Security-Policy
  `<meta>` that allows its inline scripts by sha256 hash (no `'unsafe-inline'`, no
  eval), images from any https host, one frame host (`www.youtube-nocookie.com`, the
  film trailer, inserted only when the visitor presses play), and fonts, styles and
  the service worker from the site only. The data script changes daily, so `scripts/csp.py` computes the
  hashes after the last step that edits a page (the pipeline's `csp` step; every
  writer stamps too), and the gate fails on a stale hash, a weakened directive or a
  `<script>` the build does not make. Data goes into a script only through
  `csp.json_for_script()`: a `</script>` in a scraped title or a TMDB overview
  (community-edited) can no longer end the script and run code. The webfonts are
  self-hosted (`fonts/`, SIL OFL), new-tab links are `noopener noreferrer`, and the
  app refuses to render inside another site's frame. `scripts/test_security.py`
  fuzzes every data field with hostile strings in every view (Chromium and WebKit);
  `notes/qa/SECURITY_REPORT.md` (outside the repo) has the findings.
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
  Playwright UI regression suite (run against `index.dev.html` or `index.html`). Its
  geometry checks measure against the layout viewport (`layout_width`), so they read
  the same with overlay scrollbars (phones, a Mac without a mouse) and with classic
  ones (Windows, Linux, a Mac with a mouse), where `html{scrollbar-gutter:stable}`
  reserves 15 px.
