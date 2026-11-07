#!/usr/bin/env python3
"""Tests: the committed page must carry the code src/ builds (check_page_source.py),
and the refresh rebuilds the page shell from src/ before it publishes (refresh_all.py
step 0, dev_build.py --from-file).

Offline, no network, nothing in the work tree is changed (everything is built in a
temporary folder from the work tree's index.html and sidecar files).

    python3 scripts/test_page_source.py
"""
import json, os, pathlib, re, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import page_source as PS  # noqa: E402
import refresh_all as RF  # noqa: E402
from csp import stamp_file  # noqa: E402

fails = []


def check(name, ok):
    print(("  ok    " if ok else "  FAIL  ") + name)
    if not ok:
        fails.append(name)


def line_of(text, name):
    m = re.search(rf"^const {name}\s*=.*$", text, re.M)
    return m.group(0) if m else None


def set_line(text, name, new):
    m = re.search(rf"^const {name}\s*=.*$", text, re.M)
    return text[:m.start()] + new + text[m.end():]


def dev_build(from_file, out):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dev_build.py"),
                        "--from-file", str(from_file), "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def cli(*args):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_page_source.py"), *args],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


tmp = pathlib.Path(tempfile.mkdtemp(prefix="sg-page-source-"))
built = PS.build_src()
committed = (ROOT / "index.html").read_text(encoding="utf-8")

# A page as a release that forgot the rebuild leaves it: last night's listings, but
# the code of an older src/ (a CSS rule and an app line src/ no longer has).
OLD_CSS = ".old-release-rule{color:red}"
OLD_APP = "var OLD_RELEASE_CODE=1;"
stale = committed.replace("<style>\n", "<style>\n" + OLD_CSS + "\n", 1).replace(
    "<script>/* SOFIA-APP */\n", "<script>/* SOFIA-APP */\n" + OLD_APP + "\n", 1)
assert OLD_CSS in stale and OLD_APP in stale
(tmp / "stale.html").write_text(stale, encoding="utf-8")

print("refresh: the page shell is rebuilt from src/ first")
check("refresh_all's first step is dev_build.py --from-file index.html --out index.html",
      RF.STEPS[0][1] == "dev_build.py" and RF.STEPS[0][2] == ["--from-file", "index.html", "--out", "index.html"])
check("…and its publish gate includes check_page_source.py", RF.PAGE_SOURCE[1] == "check_page_source.py")

# The refresh's offline steps on the stale page, in a temporary file: step 0, then
# the inject steps the nightly runs (the scrape and the downloads need the network),
# then the CSP stamp, as refresh_all.py does last.
rc, out = dev_build(tmp / "stale.html", tmp / "night.html")
check("step 0 (dev_build --from-file) builds on a stale page", rc == 0)
env = dict(os.environ, SOFIA_HTML=str(tmp / "night.html"))
offline = [s for s in RF.STEPS if s[1] in ("inject_films.py", "inject_shows.py", "inject_data.py")]
ran = [subprocess.run([sys.executable, str(ROOT / "scripts" / s[1])], cwd=ROOT, env=env,
                      capture_output=True, text=True).returncode for s in offline]
check("…the refresh's offline inject steps run on it", len(ran) == 3 and not any(ran))
stamp_file(tmp / "night.html", kind="page")
night = (tmp / "night.html").read_text(encoding="utf-8")
check("…the published page has none of the stale code", OLD_CSS not in night and OLD_APP not in night)
check("…it carries the code src/ builds (check_page_source --html passes)",
      cli("--html", str(tmp / "night.html"))[0] == 0)
check("…the committed listings are carried over unchanged (SNAPSHOT, SHOWTIMES, PERFORMANCES, PLINKS, PRELIM_ROWS)",
      all(line_of(night, n) == line_of(stale, n) and line_of(stale, n)
          for n in ("SNAPSHOT", "SHOWTIMES", "PERFORMANCES", "PLINKS", "PRELIM_ROWS")))
check("…FILMS and SHOWS keep the synthesised records the night before had",
      len(json.loads(line_of(night, "FILMS").split("=", 1)[1].rstrip(";")))
      == len(json.loads(line_of(stale, "FILMS").split("=", 1)[1].rstrip(";"))))

print("check: a stale page fails, a refreshed one passes")
rc, out = cli("--html", str(tmp / "stale.html"))
check("the stale page fails with the plain message",
      rc == 1 and out.startswith(PS.FIX) and "the CSS" in out)
app_only = night.replace("<script>/* SOFIA-APP */\n", "<script>/* SOFIA-APP */\n" + OLD_APP + "\n", 1)
check("…an app-code difference names the SOFIA-APP script",
      any("SOFIA-APP script" in p for p in PS.compare(built, app_only)))
bk = line_of(night, "BOOKING")
check("…a static data block of src/data.html (BOOKING) that differs fails",
      any("const BOOKING" in p for p in PS.compare(built, set_line(night, "BOOKING", bk[:-1] + " ;"))))
films = json.loads(line_of(night, "FILMS").split("=", 1)[1].rstrip(";"))
seed0 = json.loads(line_of(built, "FILMS").split("=", 1)[1].rstrip(";"))[0]["id"]
edited = [dict(f, bg=f["bg"] + " (стар)") if f["id"] == seed0 else f for f in films]
check("…a seed FILMS record that differs from src/data.html fails",
      any("seed record(s) of FILMS differ" in p
          for p in PS.compare(built, set_line(night, "FILMS", "const FILMS=" + json.dumps(edited, ensure_ascii=False) + ";"))))
check("…a seed FILMS record missing from the page fails (a new seed film not built)",
      any("not in the page" in p for p in PS.compare(
          built, set_line(night, "FILMS", "const FILMS=" + json.dumps([f for f in films if f["id"] != seed0],
                                                                    ensure_ascii=False) + ";"))))

# A seed SHOWS record whose synopsis the refresh's synopses step replaced (synopses.apply,
# from synopsis_overrides.json / synopsis_fills.json) is not a code change; any other
# synopsis change still is.
shows = json.loads(line_of(night, "SHOWS").split("=", 1)[1].rstrip(";"))
sseed = json.loads(line_of(built, "SHOWS").split("=", 1)[1].rstrip(";"))[0]
FILL = "Пълното описание от theatre.art.bg за теста, достатъчно дълго, за да мине като попълване."
def _shows_page(syn_bg, syn_en=None):
    recs = [dict(x, synBg=syn_bg, **({"synEn": syn_en} if syn_en else {})) if x.get("id") == sseed["id"] else x
            for x in shows]
    return set_line(night, "SHOWS", "const SHOWS=" + json.dumps(recs, ensure_ascii=False) + ";")
fills = {sseed["id"]: ({FILL}, set())}
check("…a seed SHOWS record carrying its synopsis_fills.json text passes",
      PS.compare(built, _shows_page(FILL), synopsis_texts=fills) == [])
check("…a seed SHOWS record carrying its override (bg + en) passes",
      PS.compare(built, _shows_page(FILL, "The owner's English text."),
                 synopsis_texts={sseed["id"]: ({FILL}, {"The owner's English text."})}) == [])
check("…a seed SHOWS record with any other synopsis still fails",
      any("seed record(s) of SHOWS differ" in p
          for p in PS.compare(built, _shows_page(FILL + " (променено)"), synopsis_texts=fills)))
check("…a fill for another show does not excuse it",
      any("seed record(s) of SHOWS differ" in p
          for p in PS.compare(built, _shows_page(FILL), synopsis_texts={"another-show": ({FILL}, set())})))
check("…the fill text excuses only the synopsis: another field changed with it still fails",
      any("seed record(s) of SHOWS differ" in p for p in PS.compare(
          built, set_line(night, "SHOWS", "const SHOWS=" + json.dumps(
              [dict(x, synBg=FILL, title=(x.get("title") or "") + " (стар)") if x.get("id") == sseed["id"] else x
               for x in shows], ensure_ascii=False) + ";"), synopsis_texts=fills)))

# What a refresh changes is not code: new listings, new synthesised records, a new
# poster block and new CSP hashes.
refreshed = set_line(night, "SHOWTIMES", 'const SHOWTIMES=[["x-film","cineland","2099-01-01",["20:00"]]];')
refreshed = set_line(refreshed, "SNAPSHOT", 'const SNAPSHOT={"window":{"from":"2099-01-01"}};')
refreshed = set_line(refreshed, "PLINKS", "const PLINKS=[];")
refreshed = set_line(refreshed, "FILMS", "const FILMS=" + json.dumps(films + [{"id": "x-film", "bg": "Х"}],
                                                                    ensure_ascii=False) + ";")
refreshed = re.sub(r"(const TMDBART = )\{", r'\1{"x-film":{"p":"/x.jpg"},', refreshed, count=1)
refreshed = re.sub(r"'sha256-[A-Za-z0-9+/=]+'", "'sha256-AAAA'", refreshed)
check("…the listings, synthesised records, poster block and CSP hashes a refresh injects are ignored",
      PS.compare(built, refreshed) == [])

# The home pages: index.html with their own address (gen_share_pages.py).
home = tmp / "root"
(home / "home" / "theatre").mkdir(parents=True)
for rel, path in (("home", "/home/"), ("home/theatre", "/home/theatre/")):
    page = night.replace(f'href="{PS.SITE_URL}/home/"', f'href="{PS.SITE_URL}{path}"', 1).replace(
        f'property="og:url" content="{PS.SITE_URL}/home/"', f'property="og:url" content="{PS.SITE_URL}{path}"', 1)
    (home / rel / "index.html").write_text(page, encoding="utf-8")
check("home pages rebuilt with index.html pass", PS.check_files(tmp / "night.html", home_root=home, built=built) == [])
(home / "home" / "theatre" / "index.html").write_text(stale, encoding="utf-8")
check("…a stale home/theatre/index.html fails, by name",
      any(p.startswith("home/theatre/index.html") for p in
          PS.check_files(tmp / "night.html", home_root=home, built=built)))

print("dev_build: a failed build leaves the output untouched")
(tmp / "keep.html").write_text("sentinel", encoding="utf-8")
rc, out = dev_build(tmp / "missing.html", tmp / "keep.html")
check("a dev_build that cannot read its listings fails and keeps the old page",
      rc != 0 and (tmp / "keep.html").read_text(encoding="utf-8") == "sentinel"
      and not (tmp / "keep.html.building").exists())

print()
if fails:
    print(f"{len(fails)} test(s) FAILED: " + ", ".join(fails))
    sys.exit(1)
print("all page-source tests passed")
