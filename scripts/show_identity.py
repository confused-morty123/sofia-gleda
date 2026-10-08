#!/usr/bin/env python3
"""Sofia Gleda — which catalogue show does a theatre's published title mean?

The theatre counterpart of film_identity.py, with one deliberate difference:
a show is only ever looked for INSIDE THE SAME THEATRE. "Трите прасенца" is a
different production at Театър Възраждане, the Youth Theatre and Сити Марк, and
the old global title lookup filed all three under one card — and under one
theatre's address. A performance attached to the wrong production sends people
to the wrong door, so the rules are built for zero wrong merges:

  1. alias   scripts/show_aliases.json — curated groups of titles verified to
             be one production at one theatre.
  2. exact   a normalised key of the title equals a key of a SHOWS title or
             titleEn of the same theatre. The key (official_theatres
             .normalise_show_title) drops what venues add around a title
             ("| ПРЕМИЕРА", "предпремиера", "гостуване", "- Гостува ДТ-Русе",
             "(ОТМЕНЕНО)", "16+", "- представление 200"), quotes, case and
             punctuation, and folds Latin look-alike letters typed inside a
             Cyrillic word (the Latin "a" in tba.art.bg's "Бaлдахинът").
There is no fuzzy rule: an unknown title is a new production (the caller
mints it). Near misses inside a theatre are listed as suspected duplicates for
a human — never merged automatically.

show_aliases.json "merge" entries retire a duplicate catalogue record: every
reference to the dropped id resolves to the kept one, and the pipeline moves
its performances and removes the record.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import Counter, defaultdict

import official_theatres as O

ALIASES_PATH = pathlib.Path(__file__).resolve().parent / "show_aliases.json"

# Latin letters that print exactly like a Cyrillic one. Lower-case b/h/k/m/t do
# not look like в/н/к/м/т, so a display title only folds the true twins.
_LAT2CYR = dict(zip("ABCEHKMOPTXYaceopxy", "АВСЕНКМОРТХУасеорху"))
_HAS_CYR = re.compile(r"[А-Яа-яЁёЀ-ӿ]")
_WORD = re.compile(r"[^\W\d_]+")


def fold_homoglyphs(text):
    """'Бaлдахинът' → 'Балдахинът', 'Xензел и Гретел' → 'Хензел и Гретел': a
    word that mixes scripts gets its Latin twins replaced — but only when EVERY
    Latin letter in it has a Cyrillic twin, so a stylised 'Neoдачници' stays
    exactly as the theatre wrote it."""
    def fix(m):
        w = m.group(0)
        if not _HAS_CYR.search(w):
            return w
        lat = [c for c in w if c.isascii()]
        if not lat or not all(c in _LAT2CYR for c in lat):
            return w
        return "".join(_LAT2CYR.get(c, c) for c in w)
    return _WORD.sub(fix, text or "")


def display_title(title):
    """The title a minted card prints: the venue's decorations removed (the
    same ones normalise_show_title ignores), wrapping quotes and stray edge
    punctuation trimmed, look-alike letters folded. Case and wording are
    otherwise exactly as published — never "corrected"."""
    t = O.clean(title)
    t = re.sub(r"^,,", "„", t)                       # ',,ГОЛЕМИЯТ СИН"' — commas as a „
    t = O.clean(O._DECOR.sub(" ", t)).strip(" -–—|/:,")
    t = O.strip_quotes(t)
    if t[:1] in "„“\"«" and t[-1:] not in "“”\"»" and not any(q in t[1:] for q in "„“”\"«»"):
        t = t[1:].strip()                            # an opening quote never closed
    t = fold_homoglyphs(t)
    return t or O.clean(title)


def choose_display(titles):
    """Among the spellings a theatre used for one new production: one typed in
    normal case beats an ALL-CAPS one, then the most frequent, then the first."""
    shown = [display_title(t) for t in titles]
    c = Counter(shown)
    return sorted(c, key=lambda t: (t.upper() == t, -c[t], shown.index(t)))[0]


def _richness(rec):
    return sum(bool(rec.get(k)) for k in ("synBg", "synEn", "author", "director", "cast",
                                          "titleEn", "duration", "genres", "reviews"))


class ShowIndex:
    """Every known show (catalogue + previously minted), theatre-scoped."""

    def __init__(self, shows=(), minted_ids=(), aliases=None):
        self.shows = {}
        self.minted = set(minted_ids)
        self.by_key = defaultdict(lambda: defaultdict(set))   # theatre -> key -> {ids}
        self.groups = []                                      # alias groups
        self.merged = {}                                      # dropped id -> kept id
        self.merge_reason = {}
        self.log = []                                         # every non-trivial decision
        self.suspects = {}                                    # (id, other id) -> reason
        for rec in shows:
            self.add(rec, minted=rec.get("id") in self.minted)
        self.load_aliases(aliases)

    # ------------------------------------------------------------ building
    def add(self, rec, minted=False):
        sid, th = rec.get("id"), rec.get("theatre")
        if not sid or not th:
            return
        self.shows[sid] = rec
        if minted:
            self.minted.add(sid)
        for t in (rec.get("title"), rec.get("titleEn")):
            for k in (O.title_keys(t) if t else ()):
                self.by_key[th][k].add(sid)

    def load_aliases(self, aliases=None):
        if aliases is None:
            try:
                aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                aliases = {}
        for g in (aliases or {}).get("groups", []):
            keys = set()
            for t in g.get("titles", []):
                keys |= O.title_keys(t)
            if keys and g.get("theatre") and g.get("show"):
                self.groups.append({"theatre": g["theatre"], "show": g["show"], "keys": keys,
                                    "reason": g.get("reason", "")})
        for m in (aliases or {}).get("merge", []):
            if m.get("drop") and m.get("keep") and m["drop"] != m["keep"]:
                self.merged[m["drop"]] = m["keep"]
                self.merge_reason[m["drop"]] = m.get("reason", "")

    # ------------------------------------------------------------- helpers
    def canonical(self, sid):
        """The id a show is known by after merges (a retired duplicate → the
        record it was merged into)."""
        seen = set()
        while sid in self.merged and sid not in seen:
            seen.add(sid)
            sid = self.merged[sid]
        return sid

    def theatre_of(self, sid):
        return (self.shows.get(self.canonical(sid)) or self.shows.get(sid) or {}).get("theatre")

    def _keys_of(self, sid):
        rec = self.shows.get(sid) or {}
        out = set()
        for t in (rec.get("title"), rec.get("titleEn")):
            out |= O.title_keys(t) if t else set()
        return out

    def _record(self, title, theatre, sid, rule, detail):
        entry = {"title": title, "theatre": theatre, "show": sid, "rule": rule, "detail": detail}
        if entry not in self.log:                 # the same title is resolved once per row
            self.log.append(entry)

    # ------------------------------------------------------------- resolve
    def resolve(self, title, theatre):
        """(show id or None, rule) for `title` as published by `theatre`."""
        keys = O.title_keys(title)
        if not keys or not theatre:
            return None, "empty"
        for g in self.groups:                                       # 1. aliases
            if g["theatre"] == theatre and keys & g["keys"]:
                sid = self.canonical(g["show"])
                if sid in self.shows and self.shows[sid].get("theatre") == theatre:
                    if not keys & self._keys_of(sid):
                        self._record(title, theatre, sid, "alias", g["reason"])
                    return sid, "alias"
        cands = set()                                               # 2. exact key
        for k in keys:
            cands |= self.by_key.get(theatre, {}).get(k, set())
        merged_from = {c: self.canonical(c) for c in cands if self.canonical(c) != c}
        cands = {self.canonical(c) for c in cands}
        cands = {c for c in cands if (self.shows.get(c) or {}).get("theatre") == theatre}
        if not cands:
            return None, "unmatched"
        if len(cands) == 1:
            sid = next(iter(cands))
            for old, new in merged_from.items():
                if new == sid:
                    self._record(title, theatre, sid, "merged",
                                 f"{old} was merged into {sid}: {self.merge_reason.get(old, '')}")
            return sid, "exact"
        ranked = sorted(cands, key=lambda c: (c in self.minted, -_richness(self.shows[c]), c))
        sid = ranked[0]
        self._record(title, theatre, sid, "duplicate",
                     f"{len(cands)} records of this theatre carry the title ({', '.join(sorted(cands))}); "
                     f"kept the one with data — add a merge to show_aliases.json")
        for c in ranked[1:]:
            self.suspects[(sid, c)] = ("same title at the same theatre — a duplicate record "
                                       "(listed for a merge, not merged)")
        return sid, "duplicate"

    # -------------------------------------------------------------- report
    def suspected_duplicates(self, sids):
        """Unmerged pairs inside one theatre that look alike — never acted on.
        `sids` are the shows that matter this run (on stage, or just minted);
        each is compared with every other show of its theatre. Pairs whose
        numbers differ ("Отчаяни съпрузи" / "Отчаяни съпрузи 2") are different
        productions, not suspects."""
        out = dict(self.suspects)
        wanted = {self.canonical(s) for s in sids}
        by_th = defaultdict(list)
        for sid, rec in self.shows.items():
            if self.canonical(sid) != sid:
                continue
            k = O.normalise_show_title(rec.get("title") or "")
            if k:
                by_th[rec.get("theatre")].append((sid, k))
        for th, items in by_th.items():
            for sid, k in items:
                if sid not in wanted:
                    continue
                for oid, ok in items:
                    if oid == sid or k == ok or (sid, oid) in out or (oid, sid) in out:
                        continue
                    why = _looks_alike(k, ok)
                    if why:
                        out[(sid, oid)] = why
        res = []
        for (a, b), why in sorted(out.items()):
            ra, rb = self.shows.get(a) or {}, self.shows.get(b) or {}
            res.append({"theatre": ra.get("theatre"), "show": a, "title": ra.get("title"),
                        "other": b, "other_title": rb.get("title"), "why": why})
        return res


def _digits(k):
    return re.findall(r"\d+", k)


def _looks_alike(a, b):
    ta, tb = a.split(), b.split()
    n = min(len(ta), len(tb))
    if n and ta[:n] == tb[:n] and _digits(a) == _digits(b) and any(len(w) >= 4 for w in ta[:n]):
        return "one title extends the other — not merged"
    la, lb = sum(c.isalpha() for c in a), sum(c.isalpha() for c in b)
    if (la >= 6 and lb >= 6 and abs(len(a) - len(b)) <= 2 and _digits(a) == _digits(b)
            and _edit_distance(a, b) <= 2):
        return "spelled almost the same — not merged"
    return None


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
