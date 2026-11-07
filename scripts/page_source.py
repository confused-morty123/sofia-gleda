#!/usr/bin/env python3
"""Is a page's code the code src/ builds?

index.html is generated (build_index.py wraps src/), but it is also COMMITTED, and
the nightly refresh injects the new listings into the committed page. On
2026-11-06 a release committed src/ without the rebuilt index.html: the refresh
kept serving the old code and the publish gate blocked the night's data. Since
then the refresh rebuilds the page shell from src/ first (refresh_all.py, step 0),
and this check fails a page whose code is not what src/ builds.

"Code" is everything except what the pipeline writes into the page:
  * the listings the scraper writes (dev_build.SCRAPED: SNAPSHOT, SHOWTIMES, ...);
  * FILMS and SHOWS beyond their seed records (the seed records of src/data.html
    must be there unchanged; a merged-away id may be missing, film_merges.json /
    show_aliases.json). One exception: a seed SHOWS record's synBg / synEn may carry
    the text scripts/synopsis_overrides.json or synopsis_fills.json gives for that id
    (the refresh's synopses step writes it, synopses.apply); any other change still fails;
  * the SOFIA-POSTERS block, regenerated wholesale by inject_data.py;
  * the CSP script hashes (they follow the data).
The CSS, every other script (SOFIA-APP, SOFIA-PRICES, ...), the head, and the
static data blocks of src/data.html (CINEMAS, THEATRES, EVENTS, BOOKING, ...) must
match exactly. home/index.html and home/theatre/index.html are index.html with
their own canonical address (gen_share_pages.py), so they are checked the same way.

    python3 scripts/check_page_source.py           # index.html + home pages vs src/
"""
import json, os, pathlib, re, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dev_build import SCRAPED  # noqa: E402  (the constants the scraper writes)
from site_config import SITE_URL  # noqa: E402

SEEDED = ("FILMS", "SHOWS")
DATA_CONSTS = tuple(SCRAPED) + SEEDED
FIX = "index.html is not built from src/: run dev_build --from origin/main and commit the page"

_HASH = re.compile(r"'sha256-[A-Za-z0-9+/=]+'")
_POSTERS = re.compile(r"<script>/\* SOFIA-POSTERS-START \*/.*?/\* SOFIA-POSTERS-END \*/</script>", re.S)
_CONST = re.compile(rf"^const ({'|'.join(DATA_CONSTS)})\s*=(.*)$", re.M)


def code_of(html):
    """(the page with the pipeline's data replaced by placeholders, {const: literal})."""
    html = _HASH.sub("'sha256-*'", html)
    html = _POSTERS.sub("<script>/* SOFIA-POSTERS: generated */</script>", html, count=1)
    data = {}

    def rep(m):
        data[m.group(1)] = m.group(2).strip().rstrip(";").strip()
        return f"const {m.group(1)}=*;"
    return _CONST.sub(rep, html), data


def home_as_index(html, path):
    """A home page with index.html's own address back (gen_share_pages.py swaps it)."""
    return html.replace(f'href="{SITE_URL}{path}"', f'href="{SITE_URL}/home/"', 1).replace(
        f'property="og:url" content="{SITE_URL}{path}"', f'property="og:url" content="{SITE_URL}/home/"', 1)


def _section(lines, i):
    """Where line i of the page is: the CSS, a named script, or the page around them."""
    where = "the page markup"
    for line in lines[:i + 1]:
        if line.startswith("<style"):
            m = re.search(r'id="([^"]+)"', line)
            where = f"the CSS ({m.group(1)})" if m else "the CSS"
        elif line.startswith("</style>") or line.startswith("/* SOFIA-") and line.endswith("</script>"):
            where = "the page markup"
        elif line.startswith("<script>"):
            m = re.match(r"<script>/\* (SOFIA-[A-Z0-9-]+)", line)
            name = m.group(1) if m else "an inline script"
            where = ("the static data (src/data.html)" if name == "SOFIA-DATA-START"
                     else f"the {name} script")
    if where.startswith("the static data"):
        c = re.match(r"const ([A-Za-z_$][\w$]*)", lines[i])
        if c:
            where += f", const {c.group(1)}"
    return where


def _short(s, n=90):
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def _merged_ids():
    """Seed ids the pipeline may drop: merged films and merged or renamed shows."""
    films, shows = set(), set()
    try:
        films = set((json.loads((ROOT / "film_merges.json").read_text(encoding="utf-8")).get("merged") or {}))
    except (OSError, ValueError, AttributeError):
        pass
    try:
        al = json.loads((ROOT / "scripts" / "show_aliases.json").read_text(encoding="utf-8"))
        shows |= {m.get("drop") for m in al.get("merge") or [] if isinstance(m, dict)}
        shows |= {m.get("from") for m in al.get("renamed") or [] if isinstance(m, dict)}
    except (OSError, ValueError, AttributeError):
        pass
    return {"FILMS": films, "SHOWS": shows}


def _synopsis_texts():
    """{show id: ({allowed synBg texts}, {allowed synEn texts})} from the owner's overrides
    and the nightly fills: the only synopsis edits a seed SHOWS record may carry."""
    try:
        import synopses
        ov, fills = synopses.load_overrides(), synopses.load_fills()
    except Exception:            # unreadable files: nothing is allowed, the check stays strict
        return {}
    out = {}
    for sid, rec in (ov or {}).items():
        bg, en = out.setdefault(sid, (set(), set()))
        if rec.get("bg"):
            bg.add(rec["bg"])
        if rec.get("en"):
            en.add(rec["en"])
    for sid, rec in (fills or {}).items():
        if isinstance(rec, dict) and isinstance(rec.get("bg"), str) and rec["bg"]:
            out.setdefault(sid, (set(), set()))[0].add(rec["bg"])
    return out


def _with_seed_synopsis(rec, seed, allowed):
    """rec with synBg / synEn put back to the seed's when they hold a text the overrides
    or fills give for this show (allowed: (bg texts, en texts))."""
    if not allowed:
        return rec
    rec = dict(rec)
    for key, texts in (("synBg", allowed[0]), ("synEn", allowed[1])):
        if rec.get(key) != seed.get(key) and rec.get(key) in texts:
            if key in seed:
                rec[key] = seed[key]
            else:
                rec.pop(key, None)
    return rec


def compare(built, page, label="index.html", synopsis_texts=None):
    """Problems (plain sentences) where `page`'s code differs from `built`, the page
    build_index.py makes from src/. Empty when the page is built from src/.
    synopsis_texts: as _synopsis_texts() (read from the files when None)."""
    code_b, data_b = code_of(built)
    code_p, data_p = code_of(page)
    out = []
    if code_b != code_p:
        lb, lp = code_b.splitlines(), code_p.splitlines()
        i = next((k for k, (a, b) in enumerate(zip(lb, lp)) if a != b), min(len(lb), len(lp)))
        a = lb[i] if i < len(lb) else "(end of page)"
        b = lp[i] if i < len(lp) else "(end of page)"
        out.append(f"{label}: its code differs from src/ from line {i + 1}, in {_section(lb, i)}: "
                   f"src/ builds {_short(a)!r}, the page has {_short(b)!r}")
    merged = _merged_ids()
    syn = _synopsis_texts() if synopsis_texts is None else synopsis_texts
    for name in SEEDED:
        if name not in data_b or name not in data_p:
            continue
        try:
            seed, have = json.loads(data_b[name]), json.loads(data_p[name])
        except ValueError:
            out.append(f"{label}: const {name} is not readable JSON")
            continue
        got = {r.get("id"): r for r in have if isinstance(r, dict)}
        changed = [r.get("id") for r in seed if isinstance(r, dict) and r.get("id") in got
                   and (got[r.get("id")] if name != "SHOWS"
                        else _with_seed_synopsis(got[r.get("id")], r, syn.get(r.get("id")))) != r]
        missing = [r.get("id") for r in seed if isinstance(r, dict) and r.get("id") not in got
                   and r.get("id") not in merged[name]]
        if changed:
            out.append(f"{label}: {len(changed)} seed record(s) of {name} differ from src/data.html: "
                       + ", ".join(map(str, changed[:6])))
        if missing:
            out.append(f"{label}: {len(missing)} seed record(s) of {name} in src/data.html are not in the page: "
                       + ", ".join(map(str, missing[:6])))
    return out


def build_src():
    """The page build_index.py makes from src/ (seed data), as text."""
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "shell.html"
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_index.py"), "--out", str(out)],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("build_index.py failed: " + (r.stdout + r.stderr).strip()[-500:])
        return out.read_text(encoding="utf-8")


def check_files(index_path, home_root=None, built=None):
    """Problems of index.html (and the home pages under home_root, when given)."""
    built = built if built is not None else build_src()
    index_path = pathlib.Path(index_path)
    problems = compare(built, index_path.read_text(encoding="utf-8"), index_path.name)
    if home_root is not None:
        index = index_path.read_text(encoding="utf-8")
        for rel, path in (("home/index.html", "/home/"), ("home/theatre/index.html", "/home/theatre/")):
            p = pathlib.Path(home_root) / rel
            if not p.exists():
                continue
            home = home_as_index(p.read_text(encoding="utf-8"), path)
            problems += compare(built, home, rel)
            if not problems and _HASH.sub("", home) != _HASH.sub("", index):
                problems.append(f"{rel} is not index.html with its own address: run gen_share_pages.py and csp.py")
    return problems
