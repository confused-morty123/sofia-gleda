#!/usr/bin/env python3
"""Fail when index.html (or a home page) does not carry the code src/ builds.

The listings the refresh injects are ignored (scripts/page_source.py says what
counts as code). Run in the unit job of tests.yml on the COMMITTED page (before the
offline build overwrites it) and by refresh_all.py as part of the publish gate.

    python3 scripts/check_page_source.py                  # index.html + home pages
    python3 scripts/check_page_source.py --html PATH      # one page, no home pages
"""
import argparse, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from page_source import ROOT, FIX, check_files  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--html", help="check this page only (default: index.html and home/)")
    a = ap.parse_args(argv)
    if a.html:
        problems = check_files(a.html)
    else:
        problems = check_files(ROOT / "index.html", home_root=ROOT)
    if problems:
        print(FIX)
        for p in problems:
            print("  - " + p)
        return 1
    print("page source: index.html" + ("" if a.html else " and the home pages") + " carry the code src/ builds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
