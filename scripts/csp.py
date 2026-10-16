#!/usr/bin/env python3
"""Sofia Gleda: the Content-Security-Policy, in one place.

GitHub Pages sends no headers of ours, so each page carries its policy as
<meta http-equiv="Content-Security-Policy">. Scripts are allowed by the sha256 of
each inline <script>, never by 'unsafe-inline'. The data script changes every day,
so the hashes are computed HERE, after the last step that edits a page:

  refresh_all.py   runs this file as its last step before verify_build.py
                   (index.html and every film/<id>/ and show/<id>/ page);
  build_index.py, inject_data.py, dev_build.py and gen_share_pages.py stamp what
                   they write, so no page is left with stale hashes;
  verify_build.py  recomputes the hashes (check()) and fails the gate on any
                   mismatch, a missing directive or a script it does not know.

A page may carry only the inline scripts it is built with (PAGE_SCRIPTS /
SHARE_MARK). Anything else, e.g. markup that a scraped title smuggled out of the
data script, is refused rather than hashed and thereby allowed. json_for_script()
is how every writer puts data into a <script>, so that cannot happen ("<" leaves
as \\u003c, which JSON.parse and the JS parser read back as "<").

    python3 scripts/csp.py              # stamp index.html (or $SOFIA_HTML) + share pages
    python3 scripts/csp.py --check      # report problems, change nothing (exit 1 if any)
    python3 scripts/csp.py FILE...      # stamp these files (kind from the path)
"""
import base64, hashlib, json, os, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The policy, apart from script-src (the hashes). README ("The page runs only its own
# scripts") describes it; check() holds every page to exactly these directives.
DIRECTIVES = [
    ("default-src", "'self'"),
    ("script-src", None),                        # filled with the hashes
    ("style-src", "'self' 'unsafe-inline'"),     # style="" attributes are used throughout
    ("img-src", "'self' https: data: blob:"),    # posters come from the venues' own hosts
    ("font-src", "'self'"),                      # fonts/ (self-hosted, SIL OFL)
    ("connect-src", "'self'"),
    ("manifest-src", "'self'"),
    ("worker-src", "'self'"),                    # sw.js
    ("object-src", "'none'"),
    ("base-uri", "'self'"),                      # the page may set <base href="/"> (sheets' own paths)
    ("form-action", "'none'"),
    ("upgrade-insecure-requests", ""),
]

# The inline scripts of index.html, by how their text starts. Each may appear once.
# (prefix, required suffix or None)
PAGE_SCRIPTS = [
    ("/* SOFIA-DATA-START */", "/* SOFIA-DATA-END */"),
    ("/* SOFIA-POSTERS-START */", "/* SOFIA-POSTERS-END */"),
    ("/* SOFIA-PRICES", None),
    ("/* SOFIA-APP */", None),
    ("/* SOFIA-SW */", None),
]
# A share page (film/<id>/, show/<id>/) has exactly one: the script gen_share_pages.py
# writes, whatever it does (today a redirect into the app), marked by its first line.
# Pages written before the marker existed carry the bare redirect; that shape is
# accepted too (csp.py stamps them; gen_share_pages.py rewrites them when it can).
SHARE_MARK = "/* SOFIA-SHARE */"
LEGACY_SHARE_SCRIPT = re.compile(r'location\.replace\("https://(?:[^"<>\\\s]|\\u[0-9a-fA-F]{4})+"\);')

META_RE = re.compile(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">\n?', re.I)
CHARSET_RE = re.compile(r'<meta\s+charset=["\']?utf-8["\']?\s*/?>\n?', re.I)
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)


class CSPError(Exception):
    pass


def json_for_script(obj, **kw):
    """json.dumps for a value written into an inline <script>: compact, UTF-8 as is,
    and every "<" as \\u003c, so no string in it can close the script ("</script>")
    or open an HTML comment ("<!--"). JSON has no "<" outside strings, so the value
    reads back identically with JSON.parse, the JS parser and json.loads."""
    kw.setdefault("ensure_ascii", False)
    kw.setdefault("separators", (",", ":"))
    return json.dumps(obj, **kw).replace("<", "\\u003c")


def inline_scripts(html):
    """The text of every inline, executable <script>, in document order."""
    out = []
    for m in SCRIPT_RE.finditer(html):
        attrs = m.group(1)
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue
        t = re.search(r'\btype\s*=\s*["\']?([^"\'\s>]+)', attrs, re.I)
        if t and t.group(1).lower() not in ("text/javascript", "application/javascript", "module"):
            continue                                   # data blocks (ld+json) are not run
        out.append(m.group(2))
    return out


def sha256(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def policy(hashes):
    parts = []
    for name, value in DIRECTIVES:
        if name == "script-src":
            value = " ".join(hashes) if hashes else "'none'"
        parts.append(f"{name} {value}".strip())
    return "; ".join(parts)


def kind_of(path):
    p = pathlib.Path(path)
    return "share" if p.parent.parent.name in ("film", "show") else "page"


def _known_scripts(scripts, kind):
    """Problems with the set of inline scripts: unknown, duplicated, cut short."""
    problems = []
    for s in scripts:
        low = s.lower()
        if "<!--" in low and "<script" in low[low.index("<!--"):]:
            problems.append("a script holds '<!--' and then '<script': the browser would read past its end")
    if kind == "share":
        ok = len(scripts) == 1 and (scripts[0].strip().startswith(SHARE_MARK)
                                    or LEGACY_SHARE_SCRIPT.fullmatch(scripts[0].strip()))
        if not ok:
            problems.append(f"a share page must have exactly one script, the one gen_share_pages.py writes "
                            f"(first line {SHARE_MARK}), found {len(scripts)}: "
                            + "; ".join(repr(s.strip()[:60]) for s in scripts[:3]))
        return problems
    seen = {}
    for s in scripts:
        st = s.strip()
        hit = next(((p, e) for p, e in PAGE_SCRIPTS if st.startswith(p)), None)
        if not hit:
            problems.append(f"unknown inline script (not built by the app): {st[:80]!r}")
            continue
        seen[hit[0]] = seen.get(hit[0], 0) + 1
        if hit[1] and not st.endswith(hit[1]):
            problems.append(f"the {hit[0]} script ends before {hit[1]}: a value inside it closes the "
                            f"<script> early (ends with {st[-60:]!r})")
    for p, n in seen.items():
        if n > 1:
            problems.append(f"the {p} script appears {n} times")
    return problems


def stamp(html, kind="page", strict=True):
    """Return html with its CSP meta (re)written for its current inline scripts.
    strict: refuse (CSPError) a page whose scripts are not the known set."""
    scripts = inline_scripts(html)
    if strict:
        problems = _known_scripts(scripts, kind)
        if problems:
            raise CSPError("; ".join(problems))
    hashes = sorted(set(sha256(s) for s in scripts))
    meta = f'<meta http-equiv="Content-Security-Policy" content="{policy(hashes)}">\n'
    html = META_RE.sub("", html)
    m = CHARSET_RE.search(html)
    if m:
        return html[:m.end()] + ("" if html[m.end() - 1] == "\n" else "\n") + meta + html[m.end():]
    m = re.search(r"<head\b[^>]*>\n?", html, re.I)
    if not m:
        raise CSPError("no <meta charset> or <head> to put the policy after")
    return html[:m.end()] + meta + html[m.end():]


def stamp_file(path, kind=None, strict=True):
    p = pathlib.Path(path)
    old = p.read_text(encoding="utf-8")
    new = stamp(old, kind or kind_of(p), strict)
    if new != old:
        p.write_text(new, encoding="utf-8")
    return new != old


def check(html, kind="page"):
    """Problems with a page's policy, [] when it is sound: the meta is there, first in
    <head> before anything it governs, its script-src hashes are exactly the inline
    scripts', the scripts are the known ones, and every directive is as DIRECTIVES says."""
    problems = []
    metas = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', html, re.I)
    if len(metas) != 1:
        return [f"expected one Content-Security-Policy meta, found {len(metas)}"]
    head_end = html.lower().find("</head>")
    pos = html.find('<meta http-equiv="Content-Security-Policy"')
    first_res = re.search(r"<(script|link|style|img|base)\b", html, re.I)
    if head_end < 0 or pos > head_end:
        problems.append("the CSP meta is not in <head>")
    if first_res and first_res.start() < pos:
        problems.append(f"the CSP meta comes after a <{first_res.group(1)}> it should govern")
    got = {}
    for part in metas[0].split(";"):
        part = part.strip()
        if part:
            name, _, value = part.partition(" ")
            got[name] = value.strip()
    scripts = inline_scripts(html)
    want_hashes = sorted(set(sha256(s) for s in scripts))
    have_hashes = sorted(got.get("script-src", "").split())
    if have_hashes != (want_hashes or ["'none'"]):
        stale = sorted(set(have_hashes) - set(want_hashes))
        missing = sorted(set(want_hashes) - set(have_hashes))
        problems.append(f"script-src does not match the inline scripts ({len(missing)} not allowed, "
                        f"{len(stale)} stale): run scripts/csp.py after the last edit")
    for name, value in DIRECTIVES:
        if name == "script-src":
            continue
        if got.get(name) != value:
            problems.append(f"{name} is {got.get(name)!r}, expected {value!r}")
    extra = set(got) - {n for n, _ in DIRECTIVES}
    if extra:
        problems.append("unexpected directives: " + ", ".join(sorted(extra)))
    for bad in ("'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'", "'strict-dynamic'", "data:", "*"):
        if bad in got.get("script-src", "").split():
            problems.append(f"script-src allows {bad}")
    problems += _known_scripts(scripts, kind)
    return problems


def share_pages(root=ROOT):
    for d in ("film", "show"):
        yield from sorted((root / d).glob("*/index.html"))


def legal_pages(root=ROOT):
    """The terms, privacy and cookie pages (legal/, legal/en/): no script, so their
    policy narrows script-src to 'none'; the other directives are the app's."""
    yield from sorted((root / "legal").glob("**/index.html"))


def main(argv):
    check_only = "--check" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        files = ([os.environ.get("SOFIA_HTML") or str(ROOT / "index.html")] + [str(p) for p in share_pages()]
                 + [str(p) for p in legal_pages()])
    bad = 0
    changed = 0
    for f in files:
        kind = kind_of(f)
        try:
            if check_only:
                problems = check(pathlib.Path(f).read_text(encoding="utf-8"), kind)
                if problems:
                    bad += 1
                    print(f"  ✗ {f}: " + "; ".join(problems))
            else:
                changed += stamp_file(f, kind)
        except (OSError, CSPError) as e:
            bad += 1
            print(f"  ✗ {f}: {e}")
    pages = sum(1 for f in files if kind_of(f) == "page")
    print(f"csp: {'checked' if check_only else 'stamped'} {pages} page(s) and {len(files) - pages} share page(s)"
          + ("" if check_only else f", {changed} changed") + (f", {bad} refused" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
