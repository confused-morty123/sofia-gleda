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
A year conflict (both known, more than a year apart) vetoes any candidate.

The normalised key: case, ё, quotes, dashes and punctuation folded; format and
language tags dropped (2D/3D/IMAX/4DX/D-BOX/ScreenX, "бг аудио", "дублиран",
"с бг субтитри", "(БГ)", "Premiere", "Предпремиера"…); a known festival or
series prefix dropped (СИНЕЛИБРИ 2026 –, ЕВРОПЕЙСКИ КИНОКЛАСИКИ:, Sofia
Documental:…); a "Name Surname:" director prefix dropped only when the title
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
]
_FESTIVAL_RE = re.compile(r"^\s*(?:" + "|".join(_FESTIVALS) + r")\s*[:\-–—|]\s*", re.I)
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
    t = _BRACKET_TAG_RE.sub(" ", t)
    t = _TAG_RE.sub(" ", t)
    t = _EMPTY_BRACKETS_RE.sub(" ", t)
    t = _EDGE_PREMIERE_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:,|")
    return t


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
        self._fuzzy = {}                     # key -> fid decided this run by a fuzzy rule
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

    # ------------------------------------------------------------- helpers
    def film_year(self, fid):
        rec = self.films.get(fid) or {}
        return _int(rec.get("year")) or title_year(rec.get("bg") or "")

    def film_runtime(self, fid):
        return _int((self.films.get(fid) or {}).get("runtime"))

    def _veto(self, fid, year, runtime=None, runtime_tol=None):
        fy = self.film_year(fid)
        if year and fy and abs(fy - year) > 1:
            return f"year {year} vs {fy}"
        if runtime_tol is not None:
            fr = self.film_runtime(fid)
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
                keep.append(c)
        return keep

    def _record(self, title, venue, fid, rule, detail):
        self.log.append({"title": title, "venue": venue, "film": fid, "rule": rule,
                         "detail": detail})

    # ------------------------------------------------------------- resolve
    def resolve(self, title, venue=None, meta=None, corroborate=None, fuzzy=True):
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

        # 1. curated aliases
        for g in self.groups:
            if k in g["keys"] and (not g["venues"] or venue in g["venues"]):
                if g["film"] and g["film"] in self.films:
                    fid = g["film"]
                else:
                    cands = set()
                    for gk in g["keys"]:
                        cands |= self.by_key.get(gk, set())
                    if not cands:
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
        if k in self._fuzzy and self._fuzzy[k] in self.films:
            return self._fuzzy[k], "fuzzy-cached"

        # 5. subtitle extension, corroborated only
        sub = set(self.by_base.get(k, set()))               # we are "X", film is "X: Y"
        bk = base_key(title)
        if bk:
            sub |= self.by_key.get(bk, set())               # we are "X: Y", film is "X"
        for c in sorted(sub):
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
                self._fuzzy[k] = c
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
                    self._fuzzy[k] = fid
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
            if k:
                items.append((fid, t, k, base_key(t)))
        wanted = set(fids)
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
