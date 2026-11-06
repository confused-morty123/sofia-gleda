#!/usr/bin/env python3
"""How many films on the page show a genuine score, per source and in all.

Owner, 2026-11-04: the dashboard's "Оценки от IMDb" read 47.1 %: films with an IMDb
rating out of every title with an upcoming screening, concerts, e-sports finals and
short-film blocks included, a film listed twice counted twice, and the TMDB, Rotten
Tomatoes and Metacritic scores the page also shows left out.

Definition (the dashboard and verify_build check 5c4 both read it from here):

  films     every title with a screening from today on (SHOWTIMES, the page's FILMS
            ids after merges) that is a film: not a live broadcast or concert
            recording, an event (a ceremony, a master class, a secret screening, a
            presentation with a screening) or a block of short films (classify()).
            Those are counted on their own, never in the denominator. Two records of
            one film (the same TMDB match) count once.
  a score   what the page shows (src/sofia-screen.artifact.html): a genuine IMDb
            rating (OMDb, or the curated seed) from at least IMDB_SHOW_MIN votes (or
            an unknown vote count) that TMDB does not contradict (imdb_shown: under
            IMDB_STALE_BELOW votes, a gap of IMDB_STALE_GAP or more to a TMDB score
            from IMDB_STALE_TMDB_MIN votes is a stale OMDb copy), TMDB's vote_average with at least
            TMDB_SHOW_MIN votes, Rotten Tomatoes' critics' score, Metacritic's score.
            Nothing is estimated and TMDB's number is never counted as IMDb.
  any       films with at least one of the four; the target is TARGET %.
  ratable   films somebody has rated anywhere we can see: a score shown, or TMDB
            votes below the display minimum, or IMDb votes without a published
            rating. "any / ratable" says how much of what exists the page shows.

ratings_coverage() returns a JSON-able dict; verify_build prints it as one line
"RATINGS_COVERAGE {json}" and refresh_all.py keeps it in build_report.json.
"""
import re

TMDB_SHOW_MIN = 10   # = TMDB_SHOW_MIN in src/sofia-screen.artifact.html (test_verify checks they agree)
IMDB_SHOW_MIN = 100  # = IMDB_SHOW_MIN in src/sofia-screen.artifact.html (test_verify checks they agree)
# The staleness guard (owner, 2026-11-06; = IMDB_STALE_* in src/): OMDb lags IMDb, and a
# low-vote rating moves fast (Синелибри: Историите на нощта, OMDb 3.6 from 10 votes, IMDb 6.4).
IMDB_STALE_BELOW = 1000
IMDB_STALE_TMDB_MIN = 20
IMDB_STALE_GAP = 2.0
# Identifies the display rules in the report, so lost_scores() never mistakes a rule
# change (scores hidden on purpose) for a rating fetch that broke.
RULES = f"imdb>={IMDB_SHOW_MIN};stale<{IMDB_STALE_BELOW}:tmdb>={IMDB_STALE_TMDB_MIN}:gap>={IMDB_STALE_GAP:g};tmdb>={TMDB_SHOW_MIN}"
TARGET = 95.0
SOURCES = ("imdb", "tmdb", "rt", "mc")
FLAG = {"imdb": "i", "tmdb": "t", "rt": "r", "mc": "m"}

# What is not a film, from the title the venue prints (BG and EN). Checked on the
# 2026-11-04 listings: 15 such titles among 210, no film among them.
# A screening of a feature with live dubbing or a live score ("LIVE ЕКШЪН ДУБЛАЖ - Жени
# кечистки срещу ацтешката мумия", "live score", "съпровод на живо") is a film: the
# phrase is taken out of the title before _LIVE looks for a broadcast or concert
# (skeptic, 2026-11-05; on the 2026-11-04 listings it changes that one title only).
_LIVE_TO_FILM = re.compile(r"\blive[\s-]+(?:[а-яa-z]+\s+)?(?:дублаж|dub(?:bing|bed)?|score)\b"
                           r"|\bдублаж на живо\b|\bозвучаване на живо\b"
                           r"|\b(?:музикален\s+)?съпровод на живо\b|\blive (?:musical )?accompaniment\b", re.I)
_LIVE = re.compile(r"\blive\b|\bна живо\b|концерт|concert|\bworld tour\b|\bthe [a-z]+ tour\b"
                   r"|royal ballet|royal opera|\bmet opera\b|\bnt live\b|national theatre live"
                   r"|\bбалет\b|\bопера\b|\bcs2\b|counter-strike|league of legends|\bdota ?2\b|\bvalorant\b",
                   re.I)
_SPORT_FINAL = re.compile(r"финал|\bfinals?\b", re.I)
_EVENT = re.compile(r"церемония|награждаван|ceremony|майсторски(?:я|ят)? клас|master ?class|\bлекция\b"
                    r"|secret screening|тайна прожекция|\bи прожекция\b|\band screening\b", re.I)
_SHORTS = re.compile(r"къси филми|късометраж|\bshort films?\b|\bshorts\b", re.I)


def classify(film):
    """("film" | "live" | "event" | "shorts", the rule that decided) for a FILMS record."""
    t = " ".join(x for x in (film.get("bg"), film.get("en"), film.get("originalTitle")) if isinstance(x, str))
    genres = film.get("genres") or []
    if _SHORTS.search(t):
        return "shorts", "short films"
    if _EVENT.search(t):
        return "event", "event"
    if _LIVE.search(_LIVE_TO_FILM.sub(" ", t)) or ("Спорт" in genres and _SPORT_FINAL.search(t)):
        return "live", "live broadcast / concert / e-sports"
    return "film", ""


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def imdb_votes(v):
    """IMDb's vote count as a number ("7", "950", "12,500", "1.2K", "2.0M"); None if
    unknown (= imdbVoteN in src/)."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KM]?)", re.sub(r"[,\s]", "", str(v or "")), re.I)
    if not m:
        return None
    return round(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6}[m.group(2).upper()])


def imdb_shown(film, art):
    """IMDb's rating as the page shows it, or None (= imdbShown in src/): TMDBART first,
    the seed second; none from fewer than IMDB_SHOW_MIN votes; none under
    IMDB_STALE_BELOW votes when TMDB (from IMDB_STALE_TMDB_MIN votes) differs by
    IMDB_STALE_GAP or more; an unknown vote count (curated seed) is shown."""
    a = art if isinstance(art, dict) else {}
    im = a.get("imdb") if a.get("imdb") is not None else film.get("imdb")
    if not _num(im):
        return None
    n = imdb_votes(a.get("imdbVotes") or film.get("imdbVotes"))      # = votesOf in src/
    if n is None:
        return im
    if n < IMDB_SHOW_MIN:
        return None
    tv, tc = a.get("tv"), a.get("tc")
    if (n < IMDB_STALE_BELOW and _num(tv) and _num(tc) and tc >= IMDB_STALE_TMDB_MIN
            and abs(im - tv) >= IMDB_STALE_GAP - 1e-9):
        return None
    return im


def scores(film, art):
    """{source: value} for the scores the page shows for this film (same rules as
    imdbShown / tmdbShown / rtOf / mcOf in src/): TMDBART first, the seed second."""
    a = art if isinstance(art, dict) else {}
    out = {}
    im = imdb_shown(film, a)
    if im is not None:
        out["imdb"] = im
    tv, tc = a.get("tv"), a.get("tc")
    if _num(tv) and _num(tc) and tc >= TMDB_SHOW_MIN:
        out["tmdb"] = tv
    rt = a.get("rt") if a.get("rt") is not None else film.get("rt")
    if _num(rt):
        out["rt"] = rt
    mc = a.get("mc") if a.get("mc") is not None else film.get("mc")
    if _num(mc):
        out["mc"] = mc
    return out


def _why_none(art, side):
    """Why a film shows no score, from what the sidecar knows."""
    a, s = art or {}, side or {}
    if not s.get("matched"):
        return "no TMDB match" + ("" if not s.get("imdb_id") else ", OMDb has no rating for the venue's IMDb id")
    tc = a.get("tc") if _num(a.get("tc")) else s.get("vote_count")
    if s.get("imdb_id"):
        if s.get("imdbVotes"):
            return f"IMDb publishes no rating yet ({s['imdbVotes']} votes)"
        if not tc:
            return "no votes anywhere yet (unreleased or brand new)"
        return f"OMDb has no IMDb rating; TMDB {tc} vote(s), below {TMDB_SHOW_MIN}"
    if not tc:
        return "TMDB match without an IMDb id, no votes yet"
    return f"TMDB match without an IMDb id; {tc} TMDB vote(s), below {TMDB_SHOW_MIN}"


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else 0.0


def ratings_coverage(films, showtimes, tmdbart, today, sidecar=None):
    """The coverage report (module docstring). films: FILMS; showtimes: SHOWTIMES
    rows [id, venue, date, times]; tmdbart: TMDBART; today: ISO date; sidecar:
    tmdb_films.json (TMDB ids to count one film's two records once, and the reason a
    film has no score)."""
    fby = {f.get("id"): f for f in films or [] if isinstance(f, dict)}
    art = tmdbart if isinstance(tmdbart, dict) else {}
    side = sidecar if isinstance(sidecar, dict) else {}
    up = sorted({r[0] for r in showtimes or []
                 if isinstance(r, list) and len(r) > 2 and isinstance(r[2], str) and r[2] >= today
                 and r[0] in fby})
    non = {"live": [], "event": [], "shorts": []}
    groups = {}                              # one film, however many records
    for fid in up:
        kind, _ = classify(fby[fid])
        if kind != "film":
            non[kind].append(fid)
            continue
        s = side.get(fid) if isinstance(side.get(fid), dict) else {}
        key = f"tmdb:{s['tmdb_id']}" if s.get("matched") and s.get("tmdb_id") else f"id:{fid}"
        groups.setdefault(key, []).append(fid)
    per = {k: 0 for k in SOURCES}
    any_n = ratable = 0
    scored, without = {}, []
    for key, ids in sorted(groups.items()):
        got = {}
        for fid in ids:
            for k, v in scores(fby[fid], art.get(fid)).items():
                got.setdefault(k, v)
        for k in got:
            per[k] += 1
        flags = "".join(FLAG[k] for k in SOURCES if k in got)
        for fid in ids:
            s = side.get(fid) if isinstance(side.get(fid), dict) else {}
            scored[fid] = [flags, s.get("tmdb_id") if s.get("matched") else None]
        if got:
            any_n += 1
            ratable += 1
            continue
        fid = ids[0]
        a, s = art.get(fid) or {}, side.get(fid) if isinstance(side.get(fid), dict) else {}
        tc = a.get("tc") if _num(a.get("tc")) else s.get("vote_count")
        if (_num(tc) and tc > 0) or s.get("imdbVotes"):
            ratable += 1
        f = fby[fid]
        without.append({"id": fid, "title": f.get("bg") or f.get("en") or fid,
                        "also": ids[1:], "why": _why_none(a, s)})
    n = len(groups)
    return {
        "metric": "films-any-genuine-score", "today": today, "target": TARGET,
        "tmdb_min_votes": TMDB_SHOW_MIN, "imdb_min_votes": IMDB_SHOW_MIN, "rules": RULES,
        "titles": len(up), "films": n, "records": sum(len(v) for v in groups.values()),
        "non_films": {k: sorted(v) for k, v in non.items()},
        "per_source": {k: {"with": per[k], "pct": _pct(per[k], n)} for k in SOURCES},
        "any": {"with": any_n, "pct": _pct(any_n, n)},
        "ratable": {"films": ratable, "with": any_n, "pct": _pct(any_n, ratable)},
        "without": without,
        "scored": scored,
    }


def lost_scores(prev, cur, min_films=3):
    """Scores a build lost that it can be blamed for: a film still listed, matched
    to the SAME TMDB film as last build, that had a source's score and now has none.
    A rating does not disappear (fetch_tmdb keeps last run's on an outage), so
    min_films or more such films for one source are a fetch that broke, not churn.
    Returns {source: [film ids]} for the sources at or above min_films."""
    p = (prev or {}).get("scored") if isinstance(prev, dict) else None
    c = (cur or {}).get("scored") if isinstance(cur, dict) else None
    if not isinstance(p, dict) or not isinstance(c, dict):
        return {}
    # a report made under other display rules (or before they were recorded) hid or
    # showed IMDb ratings on purpose: its IMDb flags are not comparable
    srcs = SOURCES if prev.get("rules") == cur.get("rules") else tuple(k for k in SOURCES if k != "imdb")
    lost = {k: [] for k in SOURCES}
    for fid, now in c.items():
        was = p.get(fid)
        if not (isinstance(was, list) and len(was) == 2 and isinstance(now, list) and len(now) == 2):
            continue
        if was[1] != now[1]:
            continue                         # matched to another film (or unmatched): not comparable
        for k in srcs:
            if FLAG[k] in (was[0] or "") and FLAG[k] not in (now[0] or ""):
                lost[k].append(fid)
    return {k: sorted(v) for k, v in lost.items() if len(v) >= min_films}


if __name__ == "__main__":                   # python3 scripts/ratings_coverage.py [index.html]
    import json, pathlib, sys
    root = pathlib.Path(__file__).resolve().parent.parent
    page = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else root / "index.html").read_text(encoding="utf-8")

    def const(name):
        m = re.search(rf"^const {name}\s*=\s*(.*?);\s*$", page, re.M)
        return json.loads(m.group(1)) if m else None
    snap = const("SNAPSHOT") or {}
    try:
        side = json.loads((root / "tmdb_films.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        side = {}
    # "today" as verify_build 5c4 takes it (Sofia's date), not the snapshot's window
    # start: a build made yesterday counts yesterday's last screenings otherwise
    # (2026-11-05: 192 films here against the gate's 187 from one index.html).
    try:
        sys.path.insert(0, str(root / "scripts"))
        import listing_window
        today = listing_window.sofia_today().isoformat()
    except Exception:                                   # pragma: no cover
        today = (snap.get("window") or {}).get("from") or "0000"
    rep = ratings_coverage(const("FILMS"), const("SHOWTIMES"), const("TMDBART"), today, side)
    print(f"{rep['films']} films on {today} ({rep['records']} records; not films: "
          + ", ".join(f"{k} {len(v)}" for k, v in rep["non_films"].items()) + ")")
    for k in SOURCES:
        print(f"  {k:5s} {rep['per_source'][k]['with']:4d}  {rep['per_source'][k]['pct']:5.1f} %")
    print(f"  any   {rep['any']['with']:4d}  {rep['any']['pct']:5.1f} %  (target {TARGET:g} %)"
          f"; of ratable {rep['ratable']['films']}: {rep['ratable']['pct']:.1f} %")
