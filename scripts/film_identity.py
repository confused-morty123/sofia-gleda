#!/usr/bin/env python3
"""Sofia Gleda — which catalogue film does a published title mean?

Every cinema spells the same film its own way: "Падане 2" (Кино Арена) and
"Падане 2: Мъртва точка" (Cinema City), "КАПИТАН САБЛЕЗЪБ…" (Cine Grand) and
"Капитан Съблезъб…" (programata), "NAZA" (Одеон) and "НАЗА" (Дом на киното),
"Акира Куросава: Сънища (1990)" (НДК) and "Сънища" (KinoCult). A screening
attached to the wrong film is a wrong listing, so the rules below are built for
ZERO wrong merges; a missed merge only costs a second card, and every near miss
is written to the run report as a "suspected duplicate" for a human to judge.

Rules, in order (first hit wins):
  1. alias      scripts/film_aliases.json — curated groups of titles verified to
                be one film (direction-agnostic; optionally pinned to an id and
                scoped to venues).
  2. exact      normalised key equal to a catalogue film's bg or en key.
  3. original   the source's own original/English title equals a film's en key.
  4. translit   Latin vs Cyrillic spelling of the same key ("NAZA" == "НАЗА").
  5. subtitle   "X" vs "X: Y" — ONLY with corroboration: the same venue, date and
                time in two sources, or a matching runtime/year in the official
                meta. Never on its own ("Аватар" is not "Аватар: Огън и пепел").
  6. spelling   one edit apart, keys of at least 8 letters, equal token counts,
                identical numbers ("Падане" is never "Падане 2").
A year conflict (both known, more than a year apart) vetoes any candidate, of
every rule, curated aliases included ("Сънища" at Люмиер is Kurosawa's 1990
film, not a 2024 "Сънища" the same hall might show).

Duplicate RECORDS — a title minted on a day no rule could place it, which from
then on matched its own record — are merged by detect_duplicates() (below) at the
start of every scrape: the same normalised title, the same IMDb / confirmed TMDB
id, or a title extension corroborated by facts; the merged-away id becomes a
redirect (film_merges.json, FILM_REDIRECT) and its titles resolve to the kept film.

Every resolution stands on its own evidence. There is no run-wide memory of a
fuzzy decision (2026-10-09 audit, CODE-07: Cinema City's "Аватар", corroborated
as "Аватар: Огън и пепел", was reused for Одеон's retro "Аватар (2009)" with no
year check — wrong poster, synopsis and runtime). A title placed by the
subtitle or spelling rule at one venue is placed again, at another venue or
for another title, only by that rule's own evidence there.

The normalised key: case, ё, quotes, dashes and punctuation folded; format and
language tags dropped (2D/3D/IMAX/4DX/D-BOX/ScreenX, "бг аудио", "дублиран",
"с бг субтитри", "(БГ)", "Premiere", "Предпремиера"…); a known festival or
series prefix dropped (СИНЕЛИБРИ 2026 –, ЕВРОПЕЙСКИ КИНОКЛАСИКИ:, Sofia
Documental:…), and the same names as a suffix (" - СИНЕЛИБРИ"); a "Name Surname:" director prefix dropped only when the title
ends in a "(YYYY)" year; that trailing year dropped.

Festival prefixes are an explicit list on purpose: a generic "ALL-CAPS words
followed by a colon" rule would also eat "ПАДАНЕ 2:" from Cine Grand's
all-capitals "ПАДАНЕ 2: МЪРТВА ТОЧКА".
"""
from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from collections import defaultdict

ALIASES_PATH = pathlib.Path(__file__).resolve().parent / "film_aliases.json"

# Format / language / presentation tags. Removed wherever they stand as whole
# words; "премиера"/"premiere" only at either end of the title, because a title
# may legitimately contain the word ("Колите: 20 години от премиерата").
_TAGS = [
    r"imax\s*3d", r"imax", r"4dx", r"4d", r"3d", r"2d", r"d[\s-]?box", r"screen\s*x",
    r"dolby\s+atmos", r"бг\s+аудио", r"bg\s+audio", r"бг\s+дублаж", r"бг\s+звук",
    r"дублиран[аио]?", r"с\s+бг\s+субтитри", r"бг\s+субтитри", r"с\s+субтитри",
    r"bg\s+subs?", r"bg\s+subtitles", r"subtitled", r"dubbed", r"предпремиера",
    r"avant[\s-]premiere", r"pre[\s-]?premiere",
]
_TAG_RE = re.compile(r"(?<!\w)(?:" + "|".join(_TAGS) + r")(?!\w)", re.I)
_BRACKET_TAG_RE = re.compile(r"[\(\[]\s*(?:бг|bg|" + "|".join(_TAGS) + r")\s*[\)\]]", re.I)
_EDGE_PREMIERE_RE = re.compile(
    r"^\s*(?:premiere|премиера)(?!\w)\s*[:\-–—|]?\s*"
    r"|\s*[:\-–—|,]?\s*(?<!\w)(?:premiere|премиера)\s*$", re.I)
_EMPTY_BRACKETS_RE = re.compile(r"[\(\[][\s,;/\-–—+]*[\)\]]")

# Festival / series prefixes seen on Sofia programmes. Extend as new ones appear.
_FESTIVALS = [
    r"синелибри(?:\s+\d{4})?", r"cinelibri(?:\s+\d{4})?",
    r"европейски\s+кинокласики", r"кинокласики",
    r"kinocult(?:\s+\d{4})?", r"кинокулт(?:\s+\d{4})?",
    r"sofia\s+documental(?:\s+\d{4})?", r"софия\s+документал(?:\s+\d{4})?",
    r"киномания(?:\s+\d{4})?", r"kinomania(?:\s+\d{4})?",
    r"софия\s+филм\s+фест(?:\s+\d{4})?", r"sofia\s+film\s+fest(?:\s+\d{4})?",
    r"синелебри(?:\s+\d{4})?", r"sinelebri(?:\s+\d{4})?",   # NDK's own spelling, 2026-10
]
# A festival's edition number before its name: Дом на киното's "40.Киномания: Обич"
# (2026-10), "40. Киномания – …". Only in front of a listed name.
_EDITION = r"(?:\d{1,3}\s*\.?\s*(?:-?(?:то|ти|ми|ви)\s+)?)?"
# A cinema's own programme strand in front of a film's title, with or without a
# separator: Cinema City's "Хелоуин Селекция: Наследникът" and "Хелоуин Селекция
# Коварен капан: Бягство" (2026-10-31 screenings of films already on its bill).
_STRANDS = [r"хелоуин\s+селекция", r"halloween\s+selection"]
_FESTIVAL_RE = re.compile(
    r"^\s*(?:" + _EDITION + r"(?:" + "|".join(_FESTIVALS) + r")\s*[:\-–—|]"
    r"|(?:" + "|".join(_STRANDS) + r")(?:\s*[:\-–—|]|\s+(?=\S)))\s*", re.I)
# The same names written AFTER the title, behind a spaced dash or bar (Люмиер,
# 2026-10-21: "КУКЛА - СИНЕЛИБРИ", "LES MISÉRABLES - CINELIBRI"). The space before
# the separator keeps hyphenated words whole.
_FESTIVAL_SUFFIX_RE = re.compile(r"\s+[\-–—|]\s*(?:" + "|".join(_FESTIVALS) + r")\s*$", re.I)
_YEAR_PARENS_RE = re.compile(r"\s*[\(\[]\s*((?:18|19|20)\d{2})\s*[\)\]]\s*$")
_YEAR_BARS_RE = re.compile(r"\s*\|\s*((?:18|19|20)\d{2})\s*\|?\s*$")
# "Name Surname:" — 2 to 4 words of letters only (a person, not "Падане 2").
_PERSON_PREFIX_RE = re.compile(r"^([^\W\d_]+(?:[\s\-][^\W\d_]+){1,3})\s*[:：]\s*(.+)$")
_SUBTITLE_SPLIT_RE = re.compile(r"\s*(?::|\s[–—-]\s)\s*")

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y",
    "ю": "yu", "я": "ya",
}
_CYR = re.compile(r"[а-яё]", re.I)
_LAT = re.compile(r"[a-z]", re.I)


def clean_title(title, keep_prefix=False):
    """The title as a person would write it: format/language tags removed,
    whitespace collapsed, original case kept. The festival/series prefix is
    removed too unless keep_prefix (display of a minted film keeps the
    venue's own wording, "СИНЕЛИБРИ 2026 – ЕМБАРГО"); key() never keeps it."""
    t = unicodedata.normalize("NFC", title or "").replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if not keep_prefix:
        t = _FESTIVAL_RE.sub("", t)
        t = _FESTIVAL_SUFFIX_RE.sub("", t)
    t = _BRACKET_TAG_RE.sub(" ", t)
    t = _TAG_RE.sub(" ", t)
    t = _EMPTY_BRACKETS_RE.sub(" ", t)
    t = _EDGE_PREMIERE_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:,|")
    return t


def strip_prefix(title):
    """The title without a known festival/series prefix ("СИНЕЛИБРИ 2026 – ПЕПЕЛ",
    "Синелибри: Черното топче") or suffix ("КУКЛА - СИНЕЛИБРИ"); otherwise
    exactly as written."""
    t = unicodedata.normalize("NFC", title or "").replace("\xa0", " ").strip()
    t = _FESTIVAL_RE.sub("", t, count=1).strip()
    return _FESTIVAL_SUFFIX_RE.sub("", t, count=1).strip()


def title_year(title):
    """A year the title itself carries ("Сънища (1990)", "КОСА | 1979 |")."""
    t = clean_title(title)
    m = _YEAR_PARENS_RE.search(t) or _YEAR_BARS_RE.search(t)
    return int(m.group(1)) if m else None


def core_title(title):
    """clean_title minus the trailing year and, only for titles that carried
    such a year, a "Name Surname:" director prefix."""
    t = clean_title(title)
    m = _YEAR_PARENS_RE.search(t) or _YEAR_BARS_RE.search(t)
    if m:
        t = t[:m.start()].strip()
        pm = _PERSON_PREFIX_RE.match(t)
        if pm:
            t = pm.group(2).strip()
    return t


def _fold(t):
    t = t.lower().replace("ё", "е").replace("ѝ", "и")
    t = t.replace("&", " и " if _CYR.search(t) else " and ")
    t = re.sub(r"['’`ʼ]", "", t)
    t = re.sub(r"[„“”\"«»‘‚]", " ", t)
    t = re.sub(r"[‐‑‒–—―\-]", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = t.replace("_", " ")
    return re.sub(r"\s+", " ", t).strip()


def key(title):
    """The normalised identity key of a published title."""
    return _fold(core_title(title))


def base_key(title):
    """Key of the part before a subtitle separator (":" or a spaced dash), or
    None when the title has no subtitle. "Падане 2: Мъртва точка" -> "падане 2"."""
    t = core_title(title)
    parts = _SUBTITLE_SPLIT_RE.split(t, maxsplit=1)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        return None
    b = _fold(parts[0])
    return b if b and b != _fold(t) else None


def translit(k):
    return "".join(_TRANSLIT.get(ch, ch) for ch in k)


def _letters(k):
    return sum(ch.isalpha() for ch in k)


def _digits(k):
    return re.findall(r"\d+", k)


def levenshtein1(a, b):
    """True when a and b are exactly one insertion, deletion or substitution apart."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = 0
    edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1; j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1; j += 1
        else:
            j += 1
    edits += (len(a) - i) + (len(b) - j)
    return edits == 1


def _int(v):
    try:
        return int(str(v).strip()[:4]) if v not in (None, "") else None
    except ValueError:
        return None


class FilmIndex:
    """All known films (catalogue + previously minted) and the resolver."""

    def __init__(self, films=(), minted_ids=(), aliases=None):
        self.films = {}
        self.minted = set(minted_ids)
        self.by_key = defaultdict(set)
        self.by_tkey = defaultdict(set)
        self.by_base = defaultdict(set)      # base key -> films whose title has a subtitle
        self.groups = []                     # alias groups
        self.log = []                        # every non-trivial decision, with its reason
        self.suspects = {}                   # (title, fid) -> reason
        self.merged = {}                     # merged-away duplicate id -> the id kept
        self.merge_reason = {}
        for rec in films:
            self.add(rec, minted=rec.get("id") in self.minted)
        self.load_aliases(aliases)

    # ------------------------------------------------------------ building
    def add(self, rec, minted=False):
        fid = rec.get("id")
        if not fid:
            return
        self.films[fid] = rec
        if minted:
            self.minted.add(fid)
        for field in ("bg", "en", "originalTitle"):
            t = rec.get(field)
            if not t:
                continue
            k = key(t)
            if not k:
                continue
            self.by_key[k].add(fid)
            if _letters(k) >= 3:
                self.by_tkey[translit(k)].add((fid, bool(_CYR.search(k))))
            b = base_key(t)
            if b:
                self.by_base[b].add(fid)

    def load_aliases(self, aliases=None):
        if aliases is None:
            try:
                aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                aliases = {}
        for g in (aliases or {}).get("groups", []):
            keys = {key(t) for t in g.get("titles", []) if key(t)}
            if len(keys) >= 1:
                self.groups.append({"keys": keys, "film": g.get("film"),
                                    "venues": set(g.get("venues") or []),
                                    "reason": g.get("reason", "")})
        self.distinct = distinct_pairs(aliases)

    def apply_merges(self, merges):
        """Merged-away duplicates (film_merges.json, written by the pipeline):
        each dropped record joins the index again so its titles keep resolving,
        and every rule answers with the id it was merged into (canonical())."""
        for drop, m in (merges or {}).items():
            keep = (m or {}).get("into")
            if not drop or not keep or drop == keep:
                continue
            rec = dict((m or {}).get("record") or {"id": drop})
            rec["id"] = drop
            if drop not in self.films:
                self.add(rec, minted=True)
            self.minted.add(drop)            # a merged-away record is never a catalogue film
            self.merged[drop] = keep
            self.merge_reason[drop] = (m or {}).get("reason", "")

    def canonical(self, fid):
        """The id a film is known by after merges (a merged-away duplicate -> the
        record it was merged into)."""
        seen = set()
        while fid in self.merged and fid not in seen:
            seen.add(fid)
            fid = self.merged[fid]
        return fid

    # ------------------------------------------------------------- helpers
    def film_year(self, fid):
        rec = self.films.get(fid) or {}
        return _int(rec.get("year")) or title_year(rec.get("bg") or "")

    def film_runtime(self, fid):
        return _int((self.films.get(fid) or {}).get("runtime"))

    def _veto(self, fid, year, runtime=None, runtime_tol=None):
        # a merged-away record answers for the film it was merged into, so that
        # film's own year / runtime vetoes too
        for f in dict.fromkeys((fid, self.canonical(fid))):
            fy = self.film_year(f)
            if year and fy and abs(fy - year) > 1:
                return f"year {year} vs {fy}"
            if runtime_tol is not None:
                fr = self.film_runtime(f)
                if runtime and fr and abs(fr - runtime) > runtime_tol:
                    return f"runtime {runtime} vs {fr} min"
        return None

    def _choose(self, cands, title, year):
        cands = sorted(cands)
        if len(cands) == 1:
            return cands[0], ""
        seeds = [c for c in cands if c not in self.minted]
        if len(seeds) == 1:
            return seeds[0], f"preferred catalogue film over minted {', '.join(c for c in cands if c != seeds[0])}"
        pool = seeds or cands
        if year:
            same = [c for c in pool if self.film_year(c) == year]
            if len(same) == 1:
                return same[0], f"year {year} picks it among {', '.join(pool)}"
        cf = (title or "").casefold()
        same_t = [c for c in pool if (self.films[c].get("bg") or "").casefold() == cf]
        if len(same_t) == 1:
            return same_t[0], f"identical title among {', '.join(pool)}"
        return pool[0], f"AMBIGUOUS among {', '.join(pool)} — picked the first"

    def _filter(self, cands, title, year, runtime=None, runtime_tol=None, rule=""):
        keep = []
        for c in cands:
            why = self._veto(c, year, runtime, runtime_tol)
            if why:
                self.suspects[(title, c)] = f"{rule} candidate vetoed ({why})"
            else:
                keep.append(self.canonical(c))
        return sorted(set(keep))

    def _record(self, title, venue, fid, rule, detail):
        self.log.append({"title": title, "venue": venue, "film": fid, "rule": rule,
                         "detail": detail})

    # ------------------------------------------------------------- resolve
    def resolve(self, title, venue=None, meta=None, corroborate=None, fuzzy=True):
        """(film id or None, rule) — see _resolve. A title that resolves to a
        merged-away duplicate answers with the film it was merged into."""
        fid, rule = self._resolve(title, venue, meta, corroborate, fuzzy)
        if fid and fid in self.merged:
            fid = self.canonical(fid)
        return fid, rule

    def _resolve(self, title, venue=None, meta=None, corroborate=None, fuzzy=True):
        """Return (film_id or None, rule). `corroborate(fid)` answers whether
        another source lists THIS venue/date/time under that film. With
        fuzzy=False only alias / exact / original / translit apply (used for
        aggregator rows that only feed a comparison)."""
        meta = meta or {}
        k = key(title)
        if not k:
            return None, "empty"
        year = _int(meta.get("year")) or title_year(title)
        runtime = _int(meta.get("runtime"))

        # 1. curated aliases — a human's decision about TITLES; the year veto
        #    still applies (a remake or a retro screening shares the title)
        for g in self.groups:
            if k in g["keys"] and (not g["venues"] or venue in g["venues"]):
                if g["film"] and g["film"] in self.films:
                    cands = [self.canonical(g["film"])] if not self._veto(g["film"], year) else []
                else:
                    cands = set()
                    for gk in g["keys"]:
                        cands |= self.by_key.get(gk, set())
                    cands = self._filter(cands, title, year, rule="alias")
                if not cands:
                    if g["film"] and g["film"] in self.films:
                        self.suspects[(title, g["film"])] = (
                            f"alias candidate vetoed ({self._veto(g['film'], year)})")
                    continue
                fid, _ = self._choose(cands, title, year)
                if self.by_key.get(k) and fid in self.by_key[k]:
                    return fid, "exact"
                self._record(title, venue, fid, "alias", g["reason"])
                return fid, "alias"

        # 2. exact key
        cands = self._filter(self.by_key.get(k, set()), title, year, rule="exact")
        if cands:
            fid, why = self._choose(cands, title, year)
            if why:
                self._record(title, venue, fid, "exact", why)
            return fid, "exact"

        # 3. the source's own original / English title
        ot = meta.get("original_title")
        if ot and key(ot):
            cands = self._filter(self.by_key.get(key(ot), set()), title, year, rule="original")
            if cands:
                fid, why = self._choose(cands, title, year)
                self._record(title, venue, fid, "original",
                             f"source's original title {ot!r} equals the film's title" + (f"; {why}" if why else ""))
                return fid, "original"

        # 4. Latin vs Cyrillic spelling of the same key. Cross-script only: two
        #    Cyrillic spellings that merely transliterate alike ("ъ" and "а" both
        #    become "a") are left to the stricter spelling rule below.
        if _letters(k) >= 3:
            cyr = bool(_CYR.search(k))
            cross = {f for f, f_cyr in self.by_tkey.get(translit(k), set()) if f_cyr != cyr}
            cands = self._filter(cross, title, year, rule="translit")
            if cands:
                fid, why = self._choose(cands, title, year)
                self._record(title, venue, fid, "translit",
                             f"{title!r} and {self.films[fid].get('bg')!r} transliterate alike" + (f"; {why}" if why else ""))
                return fid, "translit"

        if not fuzzy:
            return None, "unmatched"

        # 5. subtitle extension, corroborated only
        sub = set(self.by_base.get(k, set()))               # we are "X", film is "X: Y"
        bk = base_key(title)
        if bk:
            sub |= self.by_key.get(bk, set())               # we are "X: Y", film is "X"
        for c in sorted({self.canonical(x) for x in sub}):
            why = self._veto(c, year, runtime, runtime_tol=10)
            if why:
                self.suspects[(title, c)] = f"subtitle variant, vetoed ({why})"
                continue
            evidence = None
            if corroborate and corroborate(c):
                evidence = "another source lists the same venue, date and time under it"
            else:
                fy, fr = self.film_year(c), self.film_runtime(c)
                if year and fy and year == fy and (not runtime or not fr or abs(runtime - fr) <= 10):
                    evidence = f"same year {year}"
                elif runtime and fr and abs(runtime - fr) <= 3 and not (year and fy and year != fy):
                    evidence = f"same runtime {runtime}≈{fr} min"
            if evidence:
                self._record(title, venue, c, "subtitle",
                             f"{title!r} ~ {self.films[c].get('bg')!r}: {evidence}")
                return c, "subtitle"
            self.suspects[(title, c)] = "subtitle variant without corroboration — not merged"

        # 6. one-edit spelling variant
        if _letters(k) >= 8:
            ntok, dig = len(k.split()), _digits(k)
            for fk, ids in self.by_key.items():
                if abs(len(fk) - len(k)) > 1 or len(fk.split()) != ntok or _digits(fk) != dig:
                    continue
                if _letters(fk) < 8 or not levenshtein1(k, fk):
                    continue
                cands = self._filter(ids, title, year, runtime, runtime_tol=20, rule="spelling")
                if cands:
                    fid, why = self._choose(cands, title, year)
                    self._record(title, venue, fid, "spelling",
                                 f"{title!r} is one letter from {self.films[fid].get('bg')!r}" + (f"; {why}" if why else ""))
                    return fid, "spelling"
        return None, "unmatched"

    # -------------------------------------------------------------- report
    def suspected_duplicates(self, fids):
        """Unmerged pairs that look alike — never acted on, listed for a human.
        `fids` are the films that matter this run (on screen, or just minted);
        each is compared with every other film in the index. Pairs whose leading
        numbers differ ("Падане" / "Падане 2") are different films, not suspects."""
        def curated(a, b):                   # a human already decided this pair
            ka, kb_ = key(a), key(b)
            return any(ka in g["keys"] and kb_ in g["keys"] for g in self.groups)

        out = {(t, c): w for (t, c), w in self.suspects.items()
               if not curated(t, (self.films.get(c) or {}).get("bg") or "")}
        items = []
        for fid, rec in self.films.items():
            t = rec.get("bg") or ""
            k = key(t)
            if k and fid not in self.merged:
                items.append((fid, t, k, base_key(t)))
        wanted = {self.canonical(f) for f in fids}
        for fid, t, k, kb in items:
            if fid not in wanted:
                continue
            for oid, ot, ok, ob in items:
                if oid == fid or ok == k or (ot, fid) in out or (t, oid) in out or curated(t, ot):
                    continue
                looks = False
                if kb == ok or ob == k or ok.startswith(k + " ") or k.startswith(ok + " "):
                    looks = _digits(kb or k) == _digits(ob or ok) or kb == ok or ob == k
                elif (_letters(k) >= 6 and _letters(ok) >= 6 and abs(len(k) - len(ok)) <= 2
                      and _digits(k) == _digits(ok)):
                    looks = _edit_distance(k, ok) <= 2
                elif translit(k) == translit(ok):
                    looks = True
                if looks:
                    out[(t, oid)] = f"looks like {ot!r} ({oid}) — not merged"
        res = []
        for (t, c), w in sorted(out.items()):
            res.append({"title": t, "film": c, "why": w})
        return res


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ======================================================== duplicate records
# The resolver above places a TITLE. A wrong second card can still exist: when no
# rule could place a venue's title on the day it first appeared, a film was minted
# for it, and from then on that title matches its own record exactly and never
# reaches the film it belongs to (Cine Grand's "НЕУДОБНАТА ИСТИНА" beside
# "Хроники за неудобната истина – Истината за българският Чернобил", which seven
# other cinemas list; Люмиер's "КУКЛА - СИНЕЛИБРИ" beside "Синелибри 2026: КУКЛА"
# after the 2026-10-21 key fix). detect_duplicates() compares the RECORDS that are
# on screen, with the evidence the run already holds, and merges a minted record
# into the film it duplicates. A merge is permanent and reversible only by a human
# (film_aliases.json "distinct"); the dropped id keeps working as a redirect.

MERGES_PATH = pathlib.Path(__file__).resolve().parent.parent / "film_merges.json"
_MERGES_ABOUT = (
    "Minted film records merged into the film they duplicate, written by "
    "scripts/scrape_programs.py (film_identity.detect_duplicates). 'merged': dropped id -> "
    "{into: kept id, rule, reason, since, record: the dropped record}. The dropped record's "
    "titles keep resolving to the kept film, the page maps the old id through FILM_REDIRECT and "
    "film/<old id>/ stays a share page of the kept film, so links already shared keep working. "
    "To undo a wrong merge, list the pair under 'distinct' in scripts/film_aliases.json: the "
    "next run restores the record and never merges the pair again.")

# A language version a venue lists as a film of its own ("… (с английски
# субтитри)"). The page has no per-screening mark for it yet, so such a listing is
# never merged with one that lacks the same note — it only lends its evidence
# (runtime, year, IMDb id) to the film it is a version of.
_VERSION_NOTES = [
    ("en-sub", r"(?:с\s+)?(?:английски|англ\.?)\s+субтитри|(?:with\s+)?english\s+subtitles?"
               r"|eng(?:lish)?\.?\s+subs?"),
]
_VERSION_RES = [(n, re.compile(r"[\(\[]?\s*(?<!\w)(?:" + rx + r")(?!\w)\s*[\)\]]?", re.I))
                for n, rx in _VERSION_NOTES]
# Words after a shared title that make it ANOTHER film: a sequel, a part, a season.
_SEQUEL_WORDS = {"ii", "iii", "iv", "vi", "vii", "viii", "ix", "іі", "ііі", "іv", "част", "part",
                 "chapter", "глава", "епизод", "episode", "сезон", "season", "vol", "том",
                 "завръщане", "завръщането", "returns", "reloaded", "продължение"}
_IMDB_IN_URL = re.compile(r"(?:imdb\.com/title/|imdb[\-_/]?)(tt\d{7,9})(?!\d)", re.I)


def version_note(title):
    """'en-sub' for a listing of the English-subtitled version, else ''."""
    t = clean_title(title)
    for name, rx in _VERSION_RES:
        if rx.search(t):
            return name
    return ""


def key_without_note(title):
    """key() of the title with a version note removed."""
    t = clean_title(title)
    for _n, rx in _VERSION_RES:
        t = rx.sub(" ", t)
    return key(t)


def main_key(title):
    """The key of the title before a subtitle ("Хроники за неудобната истина" of
    "Хроники за неудобната истина – Истината за…"), or the whole key."""
    return base_key(title) or key(title)


def distinct_pairs(aliases):
    """film_aliases.json "distinct": pairs of film ids a human checked are two
    different films — never merged, and an earlier merge of them is undone."""
    out = set()
    for d in (aliases or {}).get("distinct", []) or []:
        ids = [i for i in (d or {}).get("films", []) if isinstance(i, str) and i]
        for a in ids:
            for b in ids:
                if a != b:
                    out.add(frozenset((a, b)))
    return out


def load_merges(path=None):
    """{dropped id: {into, rule, reason, since, record}} from film_merges.json."""
    try:
        d = json.loads(pathlib.Path(path or MERGES_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    m = (d or {}).get("merged") if isinstance(d, dict) else None
    return {k: v for k, v in (m or {}).items()
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("into"), str)}


def save_merges(merges, path=None):
    body = {"_about": _MERGES_ABOUT, "merged": {k: merges[k] for k in sorted(merges)}}
    pathlib.Path(path or MERGES_PATH).write_text(
        json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def film_redirects(merges):
    """{dropped id: the id it finally resolves to} — the page's FILM_REDIRECT."""
    nxt = {k: v["into"] for k, v in (merges or {}).items()}
    out = {}
    for k in nxt:
        seen, f = {k}, nxt[k]
        while f in nxt and f not in seen:
            seen.add(f)
            f = nxt[f]
        if f != k:
            out[k] = f
    return out


def undo_distinct(merges, aliases=None):
    """Split merges a human vetoed (film_aliases.json "distinct"). Returns
    (merges kept, [dropped records to restore])."""
    if aliases is None:
        try:
            aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            aliases = {}
    pairs = distinct_pairs(aliases)
    red = film_redirects(merges)
    kept, restore = {}, []
    for drop, m in merges.items():
        if frozenset((drop, m["into"])) in pairs or frozenset((drop, red.get(drop, ""))) in pairs:
            if m.get("record"):
                restore.append(dict(m["record"], id=drop))
        else:
            kept[drop] = m
    return kept, restore


def _ints(*vals):
    out = set()
    for v in vals:
        i = _int(v)
        if i:
            out.add(i)
    return out


def _near(a, b, tol):
    return any(abs(x - y) <= tol for x in a for y in b)


def _overlap_dates(da, db):
    return bool(da and db and min(da) <= max(db) and min(db) <= max(da))


def _evidence(fid, rec, rows, link_imdb, film_info, tmdb):
    """What the run knows about one film: its keys, the source facts (year,
    runtime — the venues' and festivals' own, never TMDB's), the external ids it
    can be trusted for, and where and when it is on screen."""
    t = rec.get("bg") or ""
    fi = (film_info or {}).get(fid) or {}
    ev = {
        "keys": {key(x) for x in (t, rec.get("en"), rec.get("originalTitle")) if x and key(x)},
        "key": key(t), "main": main_key(t), "note": version_note(t),
        "bare": key_without_note(t),
        "years": _ints(rec.get("year"), fi.get("year"), title_year(t)),
        "runtimes": _ints(rec.get("runtime"), fi.get("runtime")),
        "ids": set(link_imdb.get(fid, ())),
        "venues": {}, "slots": set(), "minted": False,
    }
    for _f, v, d, times in rows:
        ev["venues"].setdefault(v, set()).add(d)
        for tm in times or ():
            ev["slots"].add((v, d, tm))
    ev["dates"] = set().union(*ev["venues"].values()) if ev["venues"] else set()
    # A TMDB match counts only where the source's own facts confirm it: Cine
    # Grand's "НЕУДОБНАТА ИСТИНА" (91 min) was matched to An Inconvenient Truth
    # (2006, 100 min) — that match proves nothing.
    tm = (tmdb or {}).get(fid) or {}
    if tm.get("matched") and tm.get("tmdb_id"):
        ty, tr, ok = _int(tm.get("tmdb_year")), _int(tm.get("runtime")), True
        confirmed = False
        if ev["runtimes"] and tr:
            ok &= _near(ev["runtimes"], {tr}, 5)
            confirmed = True
        if ev["years"] and ty:
            ok &= _near(ev["years"], {ty}, 1)
            confirmed = True
        imdb = (tm.get("imdb_id") or "").lower()
        if ev["ids"] and imdb:
            ok &= imdb in ev["ids"]
            confirmed = True
        if ok and confirmed:
            ev["ids"].add(f"tmdb:{tm['tmdb_id']}")
            if imdb:
                ev["ids"].add(imdb)
    return ev


def _conflict(a, b):
    if a["years"] and b["years"] and not _near(a["years"], b["years"], 1):
        return f"year {sorted(a['years'])} vs {sorted(b['years'])}"
    if a["runtimes"] and b["runtimes"] and not _near(a["runtimes"], b["runtimes"], 10):
        return f"runtime {sorted(a['runtimes'])} vs {sorted(b['runtimes'])} min"
    return None


def _title_guard(short, long_, ext_tokens):
    """The shorter title can stand for the longer one: two words or more, eight
    letters or more, the same numbers, and nothing after it that makes a sequel."""
    return (len(short.split()) >= 2 and _letters(short) >= 8 and _digits(short) == _digits(long_)
            and not (set(ext_tokens) & _SEQUEL_WORDS))


def _contained(a, b):
    """('prefix' | 'contained' | None): a's key at the start of b's key, or as a
    run of whole words inside b's main title."""
    for ka in a["keys"]:
        for kb in b["keys"] | {b["main"]}:
            if kb.startswith(ka + " ") and _title_guard(ka, kb, kb[len(ka) + 1:].split()):
                return "prefix", ka, kb
    ka, mb = a["key"], b["main"]
    if ka and mb and ka != mb and (" " + ka + " ") in (" " + mb + " ") and not mb.startswith(ka + " "):
        rest = (" " + mb + " ").replace(" " + ka + " ", " ", 1).split()
        if _title_guard(ka, mb, rest) and _letters(ka) >= 10:
            return "contained", ka, mb
    return None


# Short words a venue drops or adds without changing the title: Влайкова's
# "ЕВЕРЕСТ: ОТ ДРУГАТА СТРАНА" is Дом на киното's "Еверест: Другата страна".
_FUNCTION_WORDS = {"от", "на", "за", "в", "във", "и", "с", "със", "по", "до",
                   "the", "of", "a", "an", "and", "to", "in", "on"}


def _function_word_drop(a, b):
    """(longer key, shorter key) when one key is the other with ONE short function
    word removed and three words or more remain, else None."""
    for ka in a["keys"]:
        for kb in b["keys"]:
            for lo, sh in ((ka, kb), (kb, ka)):
                lw, sw = lo.split(), sh.split()
                if len(lw) != len(sw) + 1 or len(sw) < 3 or _digits(lo) != _digits(sh):
                    continue
                if any(w in _FUNCTION_WORDS and lw[:i] + lw[i + 1:] == sw for i, w in enumerate(lw)):
                    return lo, sh
    return None


def _shared_venue_dates(a, b):
    return any(_overlap_dates(a["venues"][v], b["venues"][v]) for v in set(a["venues"]) & set(b["venues"]))


def _same_screening(a, b, multi_hall=()):
    """The very same screening is listed under both records. At a venue with one
    hall, one shared venue / date / time is that. At a venue that runs several
    halls at once (a multiplex, G8) two films starting together is everyday
    chance: there it takes two shared screenings or more, and at those venues
    every screening of one record must be the other's too."""
    shared = a["slots"] & b["slots"]
    if not shared:
        return False
    if any(v not in multi_hall for v, _d, _t in shared):
        return True
    vs = {v for v, _d, _t in shared}
    mine_a = {x for x in a["slots"] if x[0] in vs}
    mine_b = {x for x in b["slots"] if x[0] in vs}
    return len(shared) >= 2 and (mine_a <= b["slots"] or mine_b <= a["slots"])


def _films_at(rows):
    """{(venue, date, time): the films listed then}."""
    at = {}
    for r in rows:
        for tm in r[3] or ():
            at.setdefault((r[1], r[2], tm), set()).add(r[0])
    return at


def _edge(a, b, multi_hall=()):
    """(rule, reason) when records a and b are one film, else None. Every rule
    needs a positive fact besides the titles; a year or runtime conflict vetoes all."""
    why = _conflict(a, b)
    if why:
        return None
    if a["keys"] & b["keys"]:
        k = sorted(a["keys"] & b["keys"])[0]
        return "same-key", f"the same title once normalised ({k!r})"
    shared = sorted(a["ids"] & b["ids"])
    if shared:
        return "same-id", f"the same {'TMDB' if shared[0].startswith('tmdb:') else 'IMDb'} id {shared[0]}"
    fw = _function_word_drop(a, b)
    if fw and _overlap_dates(a["dates"], b["dates"]) and not (set(a["venues"]) & set(b["venues"])):
        # the same words but one "от"/"на"/"the", in the same weeks, and no venue
        # lists both (one venue showing both titles is two films)
        return "function-word", (f"{fw[0]!r} is {fw[1]!r} with one short word more; the same weeks, "
                                 "no venue lists both")
    for s, l in ((a, b), (b, a)):
        c = _contained(s, l)
        if not c:
            continue
        kind, ks, kl = c
        rt = s["runtimes"] and l["runtimes"] and _near(s["runtimes"], l["runtimes"], 3)
        yr = s["years"] & l["years"]
        slot = _same_screening(s, l, multi_hall)
        if not _overlap_dates(s["dates"], l["dates"]):
            continue
        # A shared opening ("Последният ден" / "Последният ден на лятото") is not
        # enough, nor is one coincidence of runtime — not even at one venue: a
        # multiplex shows "Пес патрул: Филмът" (86 min) and "Пес патрул: Мега
        # филмът" (88 min) in the same weeks. Two facts, or the very same
        # screening under both titles.
        both = rt and yr
        if kind == "prefix":
            if slot:
                return "prefix", f"{ks!r} begins {kl!r}; the same screening is listed under both"
            if both:
                return "prefix", (f"{ks!r} begins {kl!r}; runtime {sorted(s['runtimes'])}≈"
                                  f"{sorted(l['runtimes'])} min and year {sorted(yr)[0]}")
        elif both and not (set(s["venues"]) & set(l["venues"])):
            # a title inside another one: the same runtime and year, the same
            # weeks, and no venue lists both
            return "contained", (f"{ks!r} is part of {kl!r}; runtime {sorted(s['runtimes'])}≈"
                                 f"{sorted(l['runtimes'])} min, year {sorted(yr)[0]}, no venue lists both")
    return None


def detect_duplicates(films, minted, rows, links=(), film_info=None, tmdb=None, distinct=(),
                      merged=(), multi_hall=()):
    """Records that are one film, among the films on screen. films: {id: record};
    minted: ids the pipeline minted (only those are ever dropped); rows: SHOWTIMES
    rows [id, venue, date, [times]]; links: (id, url) pairs from LINKS / VLINKS (a
    venue page that names an IMDb id, Cine Grand's /m/<slug>-imdbtt…, identifies
    the film); film_info, tmdb: the sidecar files; distinct: pairs never merged;
    merged: ids already merged away (skipped); multi_hall: venues that run several
    halls at once (the multiplexes; venues seen doing it in rows are added). Returns
    [{"drop", "into", "rule", "reason"}], each drop a minted record.

    Rules (a year or runtime conflict vetoes every one):
      same-key   the same normalised title;
      same-id    the same IMDb id (a venue's own film page) or a TMDB match the
                 source's own runtime / year confirms;
      prefix     "X Y" vs "X" (two words or more, the same numbers, no sequel
                 word after it), in the same weeks, with the same screening under
                 both (at a several-hall venue: two or more, all of one record's
                 there), or the same runtime (±3) and year;
      contained  "X" as whole words inside the main title "Z X: Y" (ten letters
                 or more): the same runtime (±3) and year, the same weeks, and no
                 venue lists both;
      function-word  the same title but for ONE short word ("от", "на", "the"…),
                 three words or more left, the same numbers, the same weeks, and no
                 venue lists both ("Еверест: От другата страна" / "Еверест: Другата
                 страна").
    A shorter title that fits two different films merges with neither. A listing
    of a language version ("(с английски субтитри)") is never merged with one
    without it; when its title is another film's main title at the same venue in
    the same weeks, its runtime, year and ids count for that film."""
    by_film = {}
    for r in rows:
        if r and r[0] in films and r[0] not in merged:
            by_film.setdefault(r[0], []).append(r)
    link_imdb = {}
    for fid, url in links:
        for m in _IMDB_IN_URL.finditer(url or ""):
            link_imdb.setdefault(fid, set()).add(m.group(1).lower())
    ev = {f: _evidence(f, films[f], rs, link_imdb, film_info, tmdb) for f, rs in by_film.items()}
    multi_hall = set(multi_hall or ())
    # a venue seen running two unrelated films at once (no word of their titles in
    # common) at two moments or more has several halls (G8), whatever it is called
    unrelated = {}
    for (v, _d, _t), fs in _films_at([r for rs in by_film.values() for r in rs]).items():
        fs = sorted(fs)
        if any(not (set(ev[x]["key"].split()) & set(ev[y]["key"].split()))
               for i, x in enumerate(fs) for y in fs[i + 1:]):
            unrelated[v] = unrelated.get(v, 0) + 1
    multi_hall |= {v for v, n in unrelated.items() if n >= 2}
    for f in ev:
        ev[f]["minted"] = f in minted
    distinct = set(distinct or ())

    # a version listing lends its facts to the film it is a version of
    for v, e in ev.items():
        if not e["note"] or not e["bare"]:
            continue
        hosts = [b for b, eb in ev.items() if not eb["note"] and eb["main"] == e["bare"]
                 and _title_guard(e["bare"], eb["main"], []) and _shared_venue_dates(e, eb)
                 and not _conflict(e, eb) and frozenset((v, b)) not in distinct]
        if len(hosts) == 1:
            h = ev[hosts[0]]
            h["years"] |= e["years"]
            h["runtimes"] |= e["runtimes"]
            h["ids"] |= e["ids"]

    ids = sorted(ev)
    edges = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if frozenset((a, b)) in distinct or ev[a]["note"] != ev[b]["note"]:
                continue
            if not (ev[a]["minted"] or ev[b]["minted"]):
                continue                       # two catalogue films: for a human
            e = _edge(ev[a], ev[b], multi_hall)
            if e:
                edges[(a, b)] = e
    # a shorter title that fits two films that are not otherwise one: neither
    fuzzy = {"prefix", "contained", "function-word"}
    partners = {}
    for (a, b), (rule, _r) in edges.items():
        if rule in fuzzy:
            short = a if len(ev[a]["key"]) <= len(ev[b]["key"]) else b
            partners.setdefault(short, set()).add(b if short == a else a)
    for short, ps in partners.items():
        if len(ps) > 1:
            for (a, b) in list(edges):
                if edges[(a, b)][0] in fuzzy and short in (a, b):
                    del edges[(a, b)]
    # components; each must be free of conflicts and hold at most one catalogue film
    parent = {f: f for f in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    comps = {}
    for f in ids:
        comps.setdefault(find(f), []).append(f)
    out = []
    for members in comps.values():
        if len(members) < 2:
            continue
        if sum(1 for f in members if not ev[f]["minted"]) > 1:
            continue
        if any(_conflict(ev[x], ev[y]) or frozenset((x, y)) in distinct
               for i, x in enumerate(members) for y in members[i + 1:]):
            continue
        # keep the film's own title over a strand-prefixed listing ("Обич", not
        # "40.Киномания: Обич"): its id carries the venue page, poster and credits
        keep = sorted(members, key=lambda f: (ev[f]["minted"], _prefixed(films[f].get("bg")),
                                              -len(ev[f]["venues"]), -len(ev[f]["slots"]),
                                              -(bool(ev[f]["years"]) + bool(ev[f]["runtimes"])),
                                              -len(films[f].get("bg") or ""), f))[0]
        for f in sorted(members):
            if f == keep:
                continue
            why = [r for (a, b), r in edges.items() if f in (a, b)]
            rule = why[0][0] if why else "via"
            reason = "; ".join(r for _x, r in why) or "through another record of the same film"
            out.append({"drop": f, "into": keep, "rule": rule, "reason": reason})
    return out


def _prefixed(title):
    """True when the title carries a festival / strand prefix or suffix."""
    return clean_title(title) != clean_title(title, keep_prefix=True)


def remap_scraped(name, value, redirect):
    """A scraped page constant with every merged-away film id replaced by the
    film it was merged into (rows of one film, venue and date joined). Used for
    the previous run's rows (keep-previous) and by dev_build.py's transplant."""
    if not redirect:
        return value
    R = lambda f: redirect.get(f, f)                         # noqa: E731
    if name == "SHOWTIMES":
        joined, order = {}, []
        for r in value:
            k = (R(r[0]), r[1], r[2])
            if k not in joined:
                joined[k] = [k[0], k[1], k[2], list(r[3])] + list(r[4:])
                order.append(k)
            else:
                joined[k][3] = sorted(set(joined[k][3]) | set(r[3]))
        return [joined[k] for k in order]
    if name in ("LINKS", "VLINKS"):
        own = {tuple(r[:-1]) for r in value}
        out, seen = [], set()
        for r in value:
            k = (R(r[0]),) + tuple(r[1:-1])
            if k in seen or (r[0] != k[0] and k in own):     # the kept film's own link wins
                continue
            seen.add(k)
            out.append(list(k) + [r[-1]])
        return out
    if name == "PRELIM_ROWS":
        out = []
        for r in value:
            x = [R(r[0])] + list(r[1:]) if isinstance(r, list) and r else r
            if x not in out:
                out.append(x)
        return out
    if name in ("SHOWATTR", "AGGLINKS"):
        out = {}
        for k in sorted(value, key=lambda k: k.split("|", 1)[0] in redirect):  # own keys first
            f, _, rest = k.partition("|")
            nk = R(f) + ("|" + rest if _ else "")
            if name == "SHOWATTR" and isinstance(value[k], dict):
                out[nk] = {**value[k], **out.get(nk, {})}
            else:
                out.setdefault(nk, value[k])
        return out
    if name == "PREMIERES":
        out = {}
        for k, v in value.items():
            nk = "film:" + R(k[5:]) if k.startswith("film:") else k
            out[nk] = sorted(set(out.get(nk, [])) | set(v)) if isinstance(v, list) else v
        return out
    if name == "FESTMARK":
        out = {}
        for fest, v in value.items():
            v = dict(v)
            if isinstance(v.get("films"), list):
                v["films"] = sorted({R(f) for f in v["films"]})
            out[fest] = v
        return out
    if name == "SNAPSHOT" and isinstance(value, dict):
        v = dict(value)
        if isinstance(v.get("changelog"), list):
            v["changelog"] = [dict(c, film=R(c["film"])) if isinstance(c, dict) and c.get("film") else c
                              for c in v["changelog"]]
        return v
    return value
