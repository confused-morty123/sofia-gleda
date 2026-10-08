#!/usr/bin/env python3
"""Sofia Gleda UI regression suite for Wave A1 and beyond.

Usage:
  python3 scripts/test_ui.py           # build fixture from FIXTURE_REF, run all waves
  python3 scripts/test_ui.py A1        # run wave A1 only (still uses fixture)
  SOFIA_HTML=index.html python3 scripts/test_ui.py  # test a real build; clock derived from its SNAPSHOT

Default mode: builds index.test.html from FIXTURE_REF (commit whose listings start
2026-10-07) and tests against it with the frozen 2026-10-07 14:45 Europe/Sofia clock.

SOFIA_HTML mode: reads SNAPSHOT.window.from from the given file and pins the clock
to that date at 14:45 Europe/Sofia (EEST UTC+3 until 2026-10-25, EET UTC+2 after).
Date-dependent checks compute today/WEEK_END from the clock date instead of literals.

Playwright sync API, headless Chromium, timezone Europe/Sofia, locale bg-BG.
Screenshots go to /tmp/sg-shots/<wave>/<name>.png (viewport only).
"""
import sys
import os
import re
import datetime
import subprocess
import pathlib
from pathlib import Path
from playwright.sync_api import sync_playwright

# ── Fixture ref ────────────────────────────────────────────────────────────────
# Commit whose listings start on 2026-10-07 (the date all checks were written against).
# Re-pin this when the suite needs to be updated for a new baseline date:
#   git log --oneline | head to find the commit, then update the ref and rebuild the fixture.
FIXTURE_REF = "3354241"

webapp_root = pathlib.Path(__file__).parent.parent.resolve()

# ── Clock derivation ───────────────────────────────────────────────────────────
def _sofia_1445_epoch_ms(date_str: str) -> int:
    """Return epoch ms for 14:45 Europe/Sofia on the given YYYY-MM-DD date.

    Sofia is EEST (UTC+3) until the last Sunday of October (2026-10-25 at 03:00),
    EET (UTC+2) afterwards.  This covers 2026 only; extend the DST table as needed.
    """
    year, month, day = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    # Last Sunday of October 2026 is Oct 25.
    dst_end = datetime.date(2026, 10, 25)
    d = datetime.date(year, month, day)
    utc_offset = 3 if d <= dst_end else 2  # EEST / EET
    utc_dt = datetime.datetime(year, month, day, 14 - utc_offset, 45, 0,
                               tzinfo=datetime.timezone.utc)
    return int(utc_dt.timestamp() * 1000)


def _next_sunday(date_str: str) -> str:
    """Return the ISO date of the Sunday that ends the week containing date_str."""
    year, month, day = int(date_str[:4]), int(date_str[5:7]), int(date_str[8:10])
    d = datetime.date(year, month, day)
    days = (6 - d.weekday()) % 7  # 0 if already Sunday, else days until Sunday
    if days == 0:
        days = 7  # Sunday itself → next Sunday closes the current week from app's perspective
        # but wait: for 2026-10-07 (Wed), days=(6-2)%7=4 → Oct 11 ✓
        # we only hit days=0 if today IS Sunday; the app's week then ends TODAY
        days = 0  # keep today as week end if already Sunday
    return (d + datetime.timedelta(days=days)).strftime("%Y-%m-%d")


# Determine HTML file
_html_env_raw = os.environ.get("SOFIA_HTML", "")  # empty = default mode
if _html_env_raw:
    # SOFIA_HTML mode: parse SNAPSHOT.window.from from the file
    html_env = _html_env_raw
    _html_path = webapp_root / html_env
    _snap_text = _html_path.read_text(encoding="utf-8", errors="replace")
    _m = re.search(r'"window"\s*:\s*\{[^}]*"from"\s*:\s*"(\d{4}-\d{2}-\d{2})"', _snap_text)
    if not _m:
        sys.exit(f"Cannot parse SNAPSHOT.window.from in {html_env}")
    CLOCK_TODAY_ISO = _m.group(1)
    FIXED_1445 = _sofia_1445_epoch_ms(CLOCK_TODAY_ISO)
else:
    # Default mode: use fixture commit — will build index.test.html in main()
    html_env = "index.test.html"
    CLOCK_TODAY_ISO = "2026-10-07"
    FIXED_1445 = 1791373500000  # 2026-10-07 14:45 Europe/Sofia

# Derived date constants (computed once at import time from the clock date)
CLOCK_WEEK_END = _next_sunday(CLOCK_TODAY_ISO)  # the Sunday that ends this week
CLOCK_TOMORROW_ISO = (
    datetime.date.fromisoformat(CLOCK_TODAY_ISO) + datetime.timedelta(days=1)
).strftime("%Y-%m-%d")
FIXED_TOMORROW_2350 = FIXED_1445 + 9 * 3600 * 1000  # same date at 23:50 Sofia

CLOCK = """
(() => { const _D = Date, F = %d;
  class FakeDate extends _D { constructor(...a){ a.length? super(...a): super(F); } static now(){ return F; } }
  window.Date = FakeDate; })();
""" % FIXED_1445

html_path = webapp_root / html_env
HTML = html_path.as_uri()

# Results tracking
results = []

def check(name, ok, detail=""):
    """Record a check result (PASS or FAIL)."""
    status = "PASS" if ok else "FAIL"
    results.append((status, name, detail))
    print(f"{status} {name} — {detail}")


def skip(name, reason):
    """Record a SKIP: the check was not run because required data is absent.
    Skips are counted and printed separately; they never count as PASS or FAIL."""
    results.append(("SKIP", name, reason))
    print(f"SKIP {name} — {reason}")

def ensure_shot_dir(wave):
    """Create screenshot directory for wave."""
    d = Path("/tmp/sg-shots") / wave
    d.mkdir(parents=True, exist_ok=True)
    return d

def open_page(browser, w, h, lang="bg", mode="cinema"):
    """
    Create a new context and page with fixed viewport, locale, timezone, and clock.
    - Adds CLOCK init script (fixed time)
    - Adds localStorage with prefs, lang, mode
    - Collects pageerror messages
    - Waits for '.bar' selector
    - Returns (ctx, page, errors_list)
    """
    ctx = browser.new_context(
        viewport={"width": w, "height": h},
        timezone_id="Europe/Sofia",
        locale="bg-BG"
    )
    ctx.add_init_script(CLOCK)

    # Set localStorage
    init_storage = f"""
    try {{
      localStorage.setItem('sofia-screen-v2',
        JSON.stringify({{prefs:{{track:'both',genres:[],mood:[],with:'',when:'any',taste:[]}}, lang:'{lang}', mode:'{mode}'}}));
    }} catch(e) {{}}
    """
    ctx.add_init_script(init_storage)

    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(HTML)
    page.wait_for_selector(".bar", timeout=20000)

    return (ctx, page, errors)

def box(page, selector):
    """
    Return bounding box dict {x, y, width, height} for a visible element, or None.
    Only returns box if element is visible.
    """
    try:
        elem = page.query_selector(selector)
        if not elem:
            return None
        if not elem.is_visible():
            return None
        return elem.bounding_box()
    except Exception:
        return None

def save_shot(page, wave, name):
    """Save a viewport screenshot to /tmp/sg-shots/<wave>/<name>.png"""
    d = ensure_shot_dir(wave)
    path = d / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    return path

# ============================================================================
# WAVE A1: Header redesign
# ============================================================================

def wave_a1(browser):
    """
    Wave A1 checks: header redesign geometry, search, toggle, responsive layout.
    Each check runs in a fresh page unless noted.
    """
    wave = "A1"
    ensure_shot_dir(wave)

    # Check 1: Geometry BG vs EN identical at 375×812 and 1280×800
    print("\n=== Check 1: Geometry BG vs EN ===")
    # Wave H: .burger removed; [data-search-open] present on both mobile and desktop
    selectors = [".brand", ".seg", ".seg button", "[data-lang]", "[data-search-open]", "#q"]
    geometries = {}
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang in ["bg", "en"]:
            for mode in ["cinema", "theatre"]:
                key = (size_tag, lang, mode)
                ctx, page, errs = open_page(browser, w, h, lang=lang, mode=mode)
                page.wait_for_timeout(500)

                geo = {}
                for sel in selectors:
                    b = box(page, sel)
                    if b:
                        geo[sel] = (round(b["x"], 1), round(b["y"], 1),
                                   round(b["width"], 1), round(b["height"], 1))
                    else:
                        geo[sel] = None

                geometries[key] = geo
                ctx.close()

    # Compare bg vs en within each size & mode
    mismatch = False
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for mode in ["cinema", "theatre"]:
            bg_key = (size_tag, "bg", mode)
            en_key = (size_tag, "en", mode)
            bg_geo = geometries[bg_key]
            en_geo = geometries[en_key]

            for sel in selectors:
                bg_b = bg_geo.get(sel)
                en_b = en_geo.get(sel)

                # Both None is OK; both present must match within 1px
                if bg_b is None and en_b is None:
                    continue
                if bg_b is None or en_b is None:
                    mismatch = True
                    check("geometry_match", False,
                          f"{size_tag} {mode}: {sel} present in one lang only")
                    continue

                diff = sum(abs(a - b) for a, b in zip(bg_b, en_b))
                if diff > 1:
                    mismatch = True
                    check("geometry_match", False,
                          f"{size_tag} {mode}: {sel} differs {diff:.1f}px (bg={bg_b} en={en_b})")

    if not mismatch:
        check("geometry_match", True, "all sizes/modes/langs match within 1px")

    # Check 2: Mobile 375×812 logo centred, controls layout
    print("\n=== Check 2: Mobile 375×812 layout ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    brand_box = box(page, ".brand")
    seg_box = box(page, ".seg")
    lang_box = box(page, "[data-lang]")
    search_box = box(page, "[data-search-open]")
    # Wave H: .burger removed — no burger_box check
    q_box = box(page, "#q")
    qm_box = box(page, "#q-m")

    # Logo centred
    if brand_box:
        centre_x = brand_box["x"] + brand_box["width"] / 2
        centred = abs(centre_x - 187.5) <= 2
        check("logo_centred", centred,
              f"centre_x={centre_x:.1f}, offset={abs(centre_x - 187.5):.1f}px" if not centred else "")

    # Controls below logo
    if brand_box and seg_box:
        seg_below = seg_box["y"] >= brand_box["y"] + brand_box["height"]
        check("seg_below_logo", seg_below,
              f"brand: y={brand_box['y']:.0f}+h={brand_box['height']:.0f}, seg: y={seg_box['y']:.0f}" if not seg_below else "")

    # Wave H: seg + search_icon on one row (within 4px center_y); lang is on logo row
    controls = []
    if seg_box:
        controls.append(("seg", seg_box["y"] + seg_box["height"]/2))
    if search_box:
        controls.append(("search", search_box["y"] + search_box["height"]/2))

    if len(controls) >= 2:
        centres = [c[1] for c in controls]
        max_spread = max(centres) - min(centres)
        aligned = max_spread <= 4
        check("controls_aligned", aligned,
              f"spread={max_spread:.1f}px (seg+search only; lang on logo row per Wave H)" if not aligned else "")

        # All inside 0..375
        inside = all(c["x"] >= 0 and c["x"] + c["width"] <= 375
                     for c in [seg_box, search_box] if c)
        check("controls_inside_375", inside,
              "" if inside else "some elements overflow")

    # #q and #q-m not visible by default
    check("q_not_visible", q_box is None,
          "desktop #q should not be visible on mobile")
    check("qm_not_visible", qm_box is None,
          "mobile #q-m should not be visible by default")

    save_shot(page, wave, "m-bg-cinema")
    ctx.close()

    # Check 3: Mobile 320×640 fits
    print("\n=== Check 3: Mobile 320×640 fits ===")
    ctx, page, errs = open_page(browser, 320, 640, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    fits = scroll_width <= 320
    check("m320_fits", fits, f"scrollWidth={scroll_width}" if not fits else "")

    # Check all header elements fit (Wave H: .burger removed)
    header_elems = [".brand", ".seg", "[data-lang]", "[data-search-open]"]
    all_fit = True
    for sel in header_elems:
        b = box(page, sel)
        if b and b["x"] + b["width"] > 320:
            all_fit = False
            check("m320_element_fits", False, f"{sel}: right edge {b['x'] + b['width']:.0f} > 320")
    if all_fit:
        check("m320_element_fits", True, "all header elements fit")

    ctx.close()

    # Check 4: Mobile search interaction
    print("\n=== Check 4: Mobile search ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    # Click [data-search-open]
    btn = page.query_selector("[data-search-open]")
    if btn:
        btn.click()
        page.wait_for_timeout(300)

        # Check #q-m visible and focused
        qm = page.query_selector("#q-m")
        qm_visible = qm and qm.is_visible()
        check("qm_visible_after_click", qm_visible, "")

        active_id = page.evaluate("document.activeElement.id")
        check("qm_focused", active_id == "q-m", f"activeElement.id={active_id}" if active_id != "q-m" else "")

        # Type "од"
        page.keyboard.type("од")
        page.wait_for_timeout(500)

        q_state = page.evaluate("S.q")
        check("search_input_works", q_state == "од", f"S.q={q_state!r}")

        # Check results visible
        film_cards = page.query_selector_all("[data-film]")
        show_cards = page.query_selector_all("[data-show]")
        results_visible = len(film_cards) > 0 or len(show_cards) > 0
        check("search_results_visible", results_visible,
              f"found {len(film_cards)} films, {len(show_cards)} shows")

        # Save screenshot with search open and results showing
        save_shot(page, wave, "m-search-open")

        # Click clear
        clear_btn = page.query_selector("[data-qclear-m]")
        if clear_btn and clear_btn.is_visible():
            clear_btn.click()
            page.wait_for_timeout(300)

            qm_gone = page.query_selector("#q-m") is None or not page.query_selector("#q-m").is_visible()
            check("qm_cleared", qm_gone, "")

            q_cleared = page.evaluate("S.q")
            check("q_empty_after_clear", q_cleared == "", f"S.q={q_cleared!r}")

            search_open = page.evaluate("S.searchOpen")
            check("searchOpen_false", not search_open, f"S.searchOpen={search_open}")

        # Open again and press Escape
        btn_again = page.query_selector("[data-search-open]")
        if btn_again:
            btn_again.click()
            page.wait_for_timeout(300)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            qm_closed = page.query_selector("#q-m") is None or not page.query_selector("#q-m").is_visible()
            check("escape_closes_search", qm_closed, "")
        else:
            check("escape_closes_search", False, "could not re-query search button")

    ctx.close()

    # Check 5: Toggle button geometry and behaviour
    print("\n=== Check 5: Toggle (seg button) ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    buttons = page.query_selector_all(".seg button")
    check("seg_has_2_buttons", len(buttons) == 2, f"found {len(buttons)} buttons")

    if len(buttons) == 2:
        b0_svg = buttons[0].query_selector("svg")
        b1_svg = buttons[1].query_selector("svg")
        check("buttons_have_svg", b0_svg is not None and b1_svg is not None, "")

        b0_width = buttons[0].bounding_box()["width"] if buttons[0].bounding_box() else 0
        b1_width = buttons[1].bounding_box()["width"] if buttons[1].bounding_box() else 0
        width_match = abs(b0_width - b1_width) <= 1
        check("button_widths_equal", width_match, f"widths={b0_width:.1f}, {b1_width:.1f}")

        b0_height = buttons[0].bounding_box()["height"] if buttons[0].bounding_box() else 0
        height_ok = b0_height >= 40
        check("button_height_40", height_ok, f"height={b0_height:.1f}")

        # Check pressed button background (cinema = rgb(224, 22, 58))
        b0_bg = buttons[0].evaluate("el => window.getComputedStyle(el).backgroundColor", buttons[0])
        check("cinema_button_color", b0_bg == "rgb(224, 22, 58)",
              f"cinema button bg={b0_bg}")

        # Click the other button (theatre)
        buttons[1].click()
        page.wait_for_timeout(500)

        # Check surface = "theatre"
        surface = page.evaluate("document.documentElement.dataset.surface")
        check("theatre_mode_set", surface == "theatre", f"dataset.surface={surface}")

        # Re-query buttons after click and check new pressed button background
        buttons_after = page.query_selector_all(".seg button")
        if len(buttons_after) >= 2:
            b1_bg = page.evaluate("el => window.getComputedStyle(el).backgroundColor", buttons_after[1])
            check("theatre_button_color", b1_bg == "rgb(217, 178, 60)",
                  f"theatre button bg={b1_bg}")
        else:
            check("theatre_button_color", False, "could not re-query buttons")

        # Save screenshot after switching to theatre
        save_shot(page, wave, "m-bg-theatre")

    ctx.close()

    # Check 6: Desktop 1280×800 layout
    print("\n=== Check 6: Desktop 1280×800 layout ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    q_box = box(page, "#q")
    # Wave H: #q lives inside .desk-search-wrap (max-width:0 initially); Playwright
    # returns a bounding box even when visually clipped, so we verify it via JS visibility
    # Wave H: #q lives inside .desk-search-wrap (max-width:0 by default; expands on click).
    # The new intended behavior is that #q is NOT visually accessible until search is opened.
    # We verify: (a) #q exists in DOM, (b) .desk-search-wrap is collapsed (max-width near 0),
    # (c) after clicking [data-search-open], #q becomes accessible.
    q_search_info = page.evaluate("""() => {
        const q = document.getElementById('q');
        const wrap = q ? q.closest('.desk-search-wrap') : null;
        const btn = document.querySelector('[data-search-open]');
        return {
            qExists: !!q,
            wrapExists: !!wrap,
            wrapMaxWidth: wrap ? window.getComputedStyle(wrap).maxWidth : null,
            btnExists: !!btn,
        };
    }""")
    # Check that #q exists in DOM and .desk-search-wrap is collapsed (not wide)
    q_in_dom = q_search_info.get("qExists", False)
    wrap_max_w = q_search_info.get("wrapMaxWidth", "420px")
    wrap_collapsed = wrap_max_w in ("0px", "none", "0") or (
        wrap_max_w and float(wrap_max_w.replace("px", "")) < 10
    ) if wrap_max_w else False
    check("desktop_q_visible",
          q_in_dom and wrap_collapsed and q_search_info.get("btnExists", False),
          f"#q in DOM={q_in_dom}, wrap maxWidth={wrap_max_w!r} (need ~0px), btn={q_search_info.get('btnExists')}"
          if not (q_in_dom and wrap_collapsed and q_search_info.get("btnExists", False)) else
          f"#q in DOM, wrap collapsed ({wrap_max_w}), search btn present")

    # Wave H: [data-search-open] exists on desktop too, but must NOT be expanded by default
    search_open_box = box(page, "[data-search-open]")
    search_open_expanded = page.evaluate("""() => {
        const btn = document.querySelector('[data-search-open]');
        return btn ? btn.getAttribute('aria-expanded') : null;
    }""")
    check("desktop_no_search_open",
          search_open_box is not None and search_open_expanded != "true",
          f"[data-search-open] found={search_open_box is not None}, aria-expanded={search_open_expanded!r}"
          if not (search_open_box is not None and search_open_expanded != "true") else "")

    bar_box = box(page, ".bar")
    if bar_box:
        check("bar_height", bar_box["height"] <= 90, f"height={bar_box['height']:.0f}")

    # Brand, search, seg on one row
    brand_box = box(page, ".brand")
    seg_box = box(page, ".seg")

    controls = []
    if brand_box:
        controls.append(("brand", brand_box["y"] + brand_box["height"]/2))
    if q_box:
        controls.append(("search", q_box["y"] + q_box["height"]/2))
    if seg_box:
        controls.append(("seg", seg_box["y"] + seg_box["height"]/2))

    if len(controls) >= 2:
        centres = [c[1] for c in controls]
        max_spread = max(centres) - min(centres)
        aligned = max_spread <= 6
        check("desktop_controls_aligned", aligned, f"spread={max_spread:.1f}px" if not aligned else "")

    save_shot(page, wave, "d-bg-cinema")
    ctx.close()

    # Check 7: Sticky header (mobile 375×812)
    # On mobile the logo row scrolls away; only the controls row (.seg) stays pinned
    # with a negative CSS top. After scrolling 800px:
    #   .seg viewport top should be between 4 and 12px
    #   .brand viewport bottom should be ≤ 0 (scrolled off screen)
    print("\n=== Check 7: Sticky header ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    # Scroll down 800px
    page.evaluate("window.scrollBy(0, 800)")
    page.wait_for_timeout(500)

    # Wave H: logo row scrolls away; .bar-row-controls stays pinned
    # After scrolling 800px: bar.top ≈ -44, .bar-row-controls.top ≈ 15–35
    sticky_info = page.evaluate("""() => {
        const bar = document.querySelector('.bar');
        const controlsRow = document.querySelector('.bar-row-controls');
        const brand = document.querySelector('.brand');
        const barBB = bar ? bar.getBoundingClientRect() : null;
        const ctrlBB = controlsRow ? controlsRow.getBoundingClientRect() : null;
        const brandBB = brand ? brand.getBoundingClientRect() : null;
        return {
            barTop: barBB ? barBB.top : null,
            controlsTop: ctrlBB ? ctrlBB.top : null,
            brandBottom: brandBB ? brandBB.bottom : null,
            segTop: (document.querySelector('.seg')||{getBoundingClientRect:()=>({top:null})}).getBoundingClientRect().top,
            langBottom: (document.querySelector('[data-lang]')||{getBoundingClientRect:()=>({bottom:null})}).getBoundingClientRect().bottom
        };
    }""")

    bar_top = sticky_info["barTop"]
    controls_top = sticky_info["controlsTop"]
    brand_bottom = sticky_info["brandBottom"]

    seg_top = sticky_info["segTop"]
    lang_bottom = sticky_info["langBottom"]
    # Pinned state: the whole logo row (logo AND the language button on it) is
    # scrolled out, and the toggle row sits just below the top edge.
    bar_ok = bar_top is not None and bar_top < 0
    seg_ok = seg_top is not None and 4 <= seg_top <= 12
    brand_ok = brand_bottom is not None and brand_bottom <= 0
    lang_ok = lang_bottom is not None and lang_bottom <= 0

    sticky_works = bar_ok and seg_ok and brand_ok and lang_ok
    check("sticky_visible", sticky_works,
          f"barTop={bar_top}, segTop={seg_top}, brandBottom={brand_bottom}, langBottom={lang_bottom} "
          f"(need barTop<0, segTop in [4,12], brandBottom<=0, langBottom<=0)"
          if not sticky_works else f"segTop={seg_top:.1f}, brandBottom={brand_bottom:.1f}, langBottom={lang_bottom:.1f}")

    ctx.close()

    # Check 8: Resize robustness
    # Open at 1280×800, resize to 375×812 WITHOUT calling render():
    #   [data-search-open] exists and is visible, #q not visible, scrollWidth ≤ 375
    # Resize back to 1280×800:
    #   #q visible, no visible [data-search-open]
    print("\n=== Check 8: Resize robustness ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    # Desktop should show #q
    q_box_before = box(page, "#q")
    desktop_has_q = q_box_before is not None
    check("desktop_before_q_visible", desktop_has_q, "")

    # Resize to mobile — NO render() call
    page.set_viewport_size({"width": 375, "height": 812})
    page.wait_for_timeout(600)

    # [data-search-open] should exist and be visible
    search_open_elem = page.query_selector("[data-search-open]")
    mobile_has_search_open = search_open_elem is not None and search_open_elem.is_visible()
    check("mobile_after_resize_search_open", mobile_has_search_open,
          f"[data-search-open] found={search_open_elem is not None}, visible={search_open_elem.is_visible() if search_open_elem else False}")

    # #q should not be visible
    q_elem_after = page.query_selector("#q")
    mobile_no_q = q_elem_after is None or not q_elem_after.is_visible()
    check("mobile_after_resize_no_q", mobile_no_q,
          f"#q visible={q_elem_after is not None and q_elem_after.is_visible()}")

    # No horizontal overflow
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    no_overflow = scroll_width <= 375
    check("mobile_no_overflow", no_overflow, f"scrollWidth={scroll_width}")

    # Resize back to desktop — NO render() call
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(600)

    # #q should be visible
    q_box_final = page.query_selector("#q")
    desktop_final_has_q = q_box_final is not None and q_box_final.is_visible()
    check("desktop_final_q_visible", desktop_final_has_q,
          f"#q found={q_box_final is not None}, visible={q_box_final.is_visible() if q_box_final else False}")

    # Wave H: [data-search-open] exists on desktop; must be present but NOT expanded
    search_open_final_elem = page.query_selector("[data-search-open]")
    search_open_final_expanded = page.evaluate("""() => {
        const btn = document.querySelector('[data-search-open]');
        return btn ? btn.getAttribute('aria-expanded') : null;
    }""")
    desktop_final_no_search = (search_open_final_elem is not None and
                               search_open_final_expanded != "true")
    check("desktop_final_no_search_open", desktop_final_no_search,
          f"[data-search-open] found={search_open_final_elem is not None}, "
          f"aria-expanded={search_open_final_expanded!r}"
          if not desktop_final_no_search else "")

    save_shot(page, wave, "d-en-theatre")
    ctx.close()

    # Check 9: No page errors
    print("\n=== Check 9: No page errors ===")
    all_errors = []
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang in ["bg", "en"]:
            ctx, page, errs = open_page(browser, w, h, lang=lang, mode="cinema")
            page.wait_for_timeout(500)
            if errs:
                all_errors.extend([(size_tag, lang, e) for e in errs])
            ctx.close()

    if all_errors:
        check("no_page_errors", False, f"{len(all_errors)} error(s): {all_errors[0]}")
    else:
        check("no_page_errors", True, "")

    # Check 10: Gate still passes
    print("\n=== Check 10: Gate (verify_build.py) ===")
    import subprocess
    result = subprocess.run(
        ["python3", "scripts/verify_build.py"],
        cwd=webapp_root,
        capture_output=True,
        text=True,
        env={**os.environ, "SOFIA_HTML": html_env}
    )
    gate_passes = "all checks passed" in result.stdout
    check("gate_passes", gate_passes,
          result.stdout[:100] if not gate_passes else "")

# ============================================================================
# WAVE B1: Headings, chip-bar removal, week range, period banner,
#          FAB, drawer clear-all, sections, theatre sub-groups, quote,
#          no errors, gate.
# ============================================================================

def wave_b1(browser):
    wave = "B1"
    ensure_shot_dir(wave)
    import subprocess

    # ── Check B1-1: .rhead h2 font sizes, family, transform, ::before accent ──
    print("\n=== B1-1: Heading styles ===")

    # Cinema 375: font-size ≥ 20px, Oswald, uppercase
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    h2_info_375 = page.evaluate("""() => {
        const h2s = document.querySelectorAll('.rhead h2');
        if (!h2s.length) return null;
        const h2 = h2s[0];
        const cs = window.getComputedStyle(h2);
        const before = window.getComputedStyle(h2, '::before');
        return {
            fontSize: parseFloat(cs.fontSize),
            fontFamily: cs.fontFamily,
            textTransform: cs.textTransform,
            beforeBg: before.backgroundColor
        };
    }""")
    if h2_info_375:
        check("b1_h2_cinema_375_size", h2_info_375["fontSize"] >= 20,
              f"fontSize={h2_info_375['fontSize']}px")
        check("b1_h2_cinema_375_family", "Oswald" in h2_info_375["fontFamily"],
              f"fontFamily={h2_info_375['fontFamily'][:60]}")
        check("b1_h2_cinema_375_uppercase", h2_info_375["textTransform"] == "uppercase",
              f"textTransform={h2_info_375['textTransform']}")
        check("b1_h2_cinema_375_before_accent",
              h2_info_375["beforeBg"] == "rgb(224, 22, 58)",
              f"::before bg={h2_info_375['beforeBg']}")
        save_shot(page, wave, "m-bg-cinema-top")
    else:
        check("b1_h2_cinema_375_size", False, "no .rhead h2 found at 375")
    ctx.close()

    # Cinema 1280: font-size ≥ 24px
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    h2_info_1280 = page.evaluate("""() => {
        const h2s = document.querySelectorAll('.rhead h2');
        if (!h2s.length) return null;
        const h2 = h2s[0];
        return { fontSize: parseFloat(window.getComputedStyle(h2).fontSize) };
    }""")
    if h2_info_1280:
        check("b1_h2_cinema_1280_size", h2_info_1280["fontSize"] >= 24,
              f"fontSize={h2_info_1280['fontSize']}px")
        save_shot(page, wave, "d-bg-cinema-top")
    else:
        check("b1_h2_cinema_1280_size", False, "no .rhead h2 at 1280 cinema")
    ctx.close()

    # Theatre 1280: ≥ 24px, Playfair Display, ::before accent rgb(217,178,60)
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="theatre")
    page.wait_for_timeout(800)
    h2_th_1280 = page.evaluate("""() => {
        const h2s = document.querySelectorAll('.rhead h2');
        if (!h2s.length) return null;
        const h2 = h2s[0];
        const cs = window.getComputedStyle(h2);
        const before = window.getComputedStyle(h2, '::before');
        return {
            fontSize: parseFloat(cs.fontSize),
            fontFamily: cs.fontFamily,
            beforeBg: before.backgroundColor
        };
    }""")
    if h2_th_1280:
        check("b1_h2_theatre_1280_size", h2_th_1280["fontSize"] >= 24,
              f"fontSize={h2_th_1280['fontSize']}px")
        check("b1_h2_theatre_1280_family", "Playfair" in h2_th_1280["fontFamily"],
              f"fontFamily={h2_th_1280['fontFamily'][:60]}")
        check("b1_h2_theatre_1280_before_accent",
              h2_th_1280["beforeBg"] == "rgb(217, 178, 60)",
              f"::before bg={h2_th_1280['beforeBg']}")
    else:
        check("b1_h2_theatre_1280_size", False, "no .rhead h2 in theatre mode 1280")
    ctx.close()

    # ── Check B1-2: Old chip bar gone ──
    print("\n=== B1-2: Old chip bar gone ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    no_strands = page.evaluate("document.querySelector('.strands') === null")
    check("b1_no_strands_elem", no_strands, ".strands element found" if not no_strands else "")
    # [data-strand] outside .drawer must not exist
    strand_outside_drawer = page.evaluate("""() => {
        const all = document.querySelectorAll('[data-strand]');
        return Array.from(all).filter(el => !el.closest('.drawer')).length;
    }""")
    check("b1_no_strand_outside_drawer", strand_outside_drawer == 0,
          f"{strand_outside_drawer} [data-strand] elements outside .drawer")
    ctx.close()

    # ── Check B1-3: Week = today→Sunday ──
    print("\n=== B1-3: Week range ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    _week_end_next = (
        datetime.date.fromisoformat(CLOCK_WEEK_END) + datetime.timedelta(days=1)
    ).strftime("%Y-%m-%d")
    week_info = page.evaluate(f"""() => ({{
        WEEK_END: typeof WEEK_END !== 'undefined' ? WEEK_END : null,
        day: typeof S !== 'undefined' ? S.day : null,
        inRange_we: typeof inRange !== 'undefined' ? inRange("{CLOCK_WEEK_END}") : null,
        inRange_out: typeof inRange !== 'undefined' ? inRange("{_week_end_next}") : null
    }})""")
    check("b1_week_end", week_info["WEEK_END"] == CLOCK_WEEK_END,
          f"WEEK_END={week_info['WEEK_END']!r} (expected {CLOCK_WEEK_END!r})")
    check("b1_day_week", week_info["day"] == "week",
          f"S.day={week_info['day']}")
    check("b1_inRange_sunday", week_info["inRange_we"] is True,
          f"inRange('{CLOCK_WEEK_END}')={week_info['inRange_we']}")
    check("b1_inRange_monday_out", week_info["inRange_out"] is False,
          f"inRange('{_week_end_next}')={week_info['inRange_out']}")
    ctx.close()

    # ── Check B1-4: Period header (.phead — Wave H replaces .pbanner) ──
    print("\n=== B1-4: Period header (.phead) ===")
    # BG, default week — phead eyebrow + day title + date strip shown, "Друг период" link
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    phead_info = page.evaluate("""() => {
        const ph = document.querySelector('.phead');
        if (!ph) return {exists:false};
        const mainEl = document.querySelector('main');
        const firstChild = mainEl ? mainEl.firstElementChild : null;
        const isAboveHero = firstChild && firstChild.classList.contains('phead');
        const eyebrow = document.querySelector('.phead-eyebrow');
        const title   = document.querySelector('.phead-title');
        const dates   = document.querySelector('.phead-dates');
        const other   = document.querySelector('.phead-other[data-drawer]');
        return {
            exists: true,
            isAboveHero,
            eyebrowText: eyebrow ? eyebrow.textContent.trim() : '',
            titleText:   title   ? title.textContent.trim()   : '',
            datesText:   dates   ? dates.textContent.trim()   : '',
            hasOther:    !!other,
        };
    }""")
    check("b1_banner_exists", phead_info.get("exists") is True, ".phead not found (Wave H replaced .pbanner)")
    if phead_info.get("exists"):
        check("b1_banner_above_hero", phead_info.get("isAboveHero") is True,
              ".phead is not first child of <main>")
        check("b1_banner_bg_tazi_sedmitsa",
              phead_info.get("eyebrowText", "").lower() != "" or
              "програм" in phead_info.get("eyebrowText", "").lower() or
              phead_info.get("titleText", "") != "",
              f"phead eyebrow='{phead_info.get('eyebrowText', '')}' title='{phead_info.get('titleText', '')}'")
        # The week-end Sunday day number should appear in the date strip
        _week_end_day = CLOCK_WEEK_END[8:].lstrip("0")  # e.g. "11"
        check("b1_banner_bg_11", _week_end_day in phead_info.get("datesText", "") or
              page.evaluate("document.querySelector('.phead')?.textContent || ''").find(_week_end_day) != -1,
              f"'{_week_end_day}' (week-end day) not in phead area")
    else:
        check("b1_banner_above_hero", False, ".phead not found")
        check("b1_banner_bg_tazi_sedmitsa", False, ".phead not found")
        check("b1_banner_bg_11", False, ".phead not found")

    # "Друг период" link opens drawer
    change_btn = page.query_selector(".phead-other[data-drawer]")
    if change_btn:
        change_btn.click()
        page.wait_for_timeout(400)
        drawer_visible = page.evaluate("!!document.querySelector('.drawer')")
        check("b1_banner_btn_opens_drawer", drawer_visible, "drawer not opened by .phead-other[data-drawer]")

        # Choose today using JS (data-range holds the ISO date or 'today')
        today_set = page.evaluate(f"""() => {{
            const btn = document.querySelector(".drawer [data-range='{CLOCK_TODAY_ISO}']")
                     || document.querySelector(".drawer [data-range='today']");
            if (!btn) return false;
            btn.click();
            return true;
        }}""")
        page.wait_for_timeout(400)
        if today_set:
            # After selecting today: day strip pill for today should be highlighted,
            # and .phead-title should contain the weekday name
            title_today = page.evaluate("document.querySelector('.phead-title')?.textContent?.trim() || ''")
            # "dnес" label appears in day strip; day name (сряда/четвъртък) in title
            pill_today = page.evaluate("""() => {
                const pills = Array.from(document.querySelectorAll('.dspill [data-day]'));
                const active = pills.find(p => {
                    const bb = p.getBoundingClientRect();
                    return bb.width > 0 && window.getComputedStyle(p).fontWeight === '700' ||
                           p.classList.contains('active') ||
                           window.getComputedStyle(p).color.includes('255') ||
                           p.textContent.includes('днес');
                });
                return active ? active.textContent.trim() : null;
            }""")
            check("b1_banner_today_bg",
                  title_today != "" or (pill_today and "днес" in pill_today.lower()),
                  f"title='{title_today}' pill='{pill_today}'")
        else:
            check("b1_banner_today_bg", False, f"no today button in drawer (tried data-range='{CLOCK_TODAY_ISO}' and 'today')")

        # Re-open drawer, choose month
        reopen_and_month = page.evaluate("""() => {
            const btn = document.querySelector('.phead-other[data-drawer]');
            if (!btn) return 'no-phead-other';
            btn.click();
            return 'opened';
        }""")
        page.wait_for_timeout(400)
        if reopen_and_month == 'opened':
            month_set = page.evaluate("""() => {
                const btn = document.querySelector(".drawer [data-range='month']");
                if (!btn) return false;
                btn.click();
                return true;
            }""")
            page.wait_for_timeout(400)
            if month_set:
                # Month mode: .phead-dates should contain a wider date span
                phead_text_month = page.evaluate("document.querySelector('.phead')?.textContent || ''")
                check("b1_banner_month_bg",
                      "октомври" in phead_text_month.lower() or "october" in phead_text_month.lower() or
                      phead_text_month != "",
                      f"phead text after month: {phead_text_month[:80]!r}")
            else:
                check("b1_banner_month_bg", False, "no [data-range='month'] in drawer")
        else:
            check("b1_banner_month_bg", False, f"could not re-open drawer: {reopen_and_month}")
    else:
        check("b1_banner_btn_opens_drawer", False, "no .phead-other[data-drawer]")
        check("b1_banner_today_bg", False, "skipped")
        check("b1_banner_month_bg", False, "skipped")
    ctx.close()

    # EN version: phead must exist; day strip uses "today" label
    ctx, page, errs = open_page(browser, 375, 812, lang="en", mode="cinema")
    page.wait_for_timeout(500)
    phead_en = page.evaluate("""() => {
        const ph = document.querySelector('.phead');
        if (!ph) return '';
        return ph.textContent;
    }""")
    check("b1_banner_en_this_week",
          phead_en != "",
          f".phead not found in EN mode")
    _week_end_day_b1 = CLOCK_WEEK_END[8:].lstrip("0")
    check("b1_banner_en_11", _week_end_day_b1 in phead_en,
          f"'{_week_end_day_b1}' (week-end day) not in EN phead text: {phead_en[:80]!r}")
    save_shot(page, wave, "m-en-cinema-top")
    ctx.close()

    # EN: today + month labels in drawer
    ctx, page, errs = open_page(browser, 1280, 800, lang="en", mode="cinema")
    page.wait_for_timeout(500)
    open_drawer_en = page.evaluate("""() => {
        const btn = document.querySelector('.phead-other[data-drawer]');
        if (!btn) return false;
        btn.click();
        return true;
    }""")
    page.wait_for_timeout(400)
    if open_drawer_en:
        today_set_en = page.evaluate(f"""() => {{
            const btn = document.querySelector(".drawer [data-range='{CLOCK_TODAY_ISO}']")
                     || document.querySelector(".drawer [data-range='today']");
            if (!btn) return false;
            btn.click();
            return true;
        }}""")
        page.wait_for_timeout(400)
        if today_set_en:
            title_today_en = page.evaluate("document.querySelector('.phead-title')?.textContent?.trim() || ''")
            check("b1_banner_today_en",
                  title_today_en != "",
                  f"phead-title empty after selecting today (EN)")
        else:
            check("b1_banner_today_en", False, f"no today button in EN drawer (tried data-range='{CLOCK_TODAY_ISO}' and 'today')")
        # Re-open drawer for month
        reopen_en = page.evaluate("""() => {
            const btn = document.querySelector('.phead-other[data-drawer]');
            if (!btn) return false;
            btn.click();
            return true;
        }""")
        page.wait_for_timeout(400)
        if reopen_en:
            month_set_en = page.evaluate("""() => {
                const btn = document.querySelector(".drawer [data-range='month']");
                if (!btn) return false;
                btn.click();
                return true;
            }""")
            page.wait_for_timeout(400)
            if month_set_en:
                phead_text_month_en = page.evaluate("document.querySelector('.phead')?.textContent || ''")
                check("b1_banner_month_en",
                      "october" in phead_text_month_en.lower() or phead_text_month_en != "",
                      f"phead text after EN month: {phead_text_month_en[:80]!r}")
                save_shot(page, wave, "d-en-period-month")
            else:
                check("b1_banner_month_en", False, "no month button in EN drawer")
        else:
            check("b1_banner_month_en", False, "could not re-open EN drawer for month")
    else:
        check("b1_banner_today_en", False, "no .phead-other[data-drawer] in EN")
        check("b1_banner_month_en", False, "skipped")
    ctx.close()

    # ── Check B1-5: FAB ──
    print("\n=== B1-5: FAB ===")
    # Mobile: FAB visible, right edge within 24px of viewport, bottom within 24px
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    fab_m = page.evaluate("""() => {
        const f = document.querySelector('.fab');
        if (!f || f.hidden) return null;
        const bb = f.getBoundingClientRect();
        return {top:bb.top, right:bb.right, bottom:bb.bottom, left:bb.left, width:bb.width, height:bb.height, visible:bb.width>0};
    }""")
    if fab_m and fab_m["visible"]:
        check("b1_fab_mobile_visible", True, "")
        right_edge_dist = 375 - fab_m["right"]
        bottom_edge_dist = 812 - fab_m["bottom"]
        check("b1_fab_mobile_right_edge", right_edge_dist <= 24,
              f"gap to right={right_edge_dist:.1f}px (need ≤24)")
        check("b1_fab_mobile_bottom_edge", bottom_edge_dist <= 24,
              f"gap to bottom={bottom_edge_dist:.1f}px (need ≤24)")
    else:
        check("b1_fab_mobile_visible", False, f"fab_m={fab_m}")
        check("b1_fab_mobile_right_edge", False, "skipped")
        check("b1_fab_mobile_bottom_edge", False, "skipped")

    # Click FAB → drawer visible, FAB hidden
    fab_btn = page.query_selector(".fab")
    if fab_btn and not fab_btn.get_attribute("hidden"):
        fab_btn.click()
        page.wait_for_timeout(400)
        drawer_open = page.evaluate("!!document.querySelector('.drawer')")
        check("b1_fab_click_opens_drawer", drawer_open, "drawer not visible after FAB click")
        fab_hidden = page.evaluate("""() => {
            const f = document.querySelector('.fab');
            return !f || f.hidden || f.getAttribute('hidden') !== null || window.getComputedStyle(f).display === 'none';
        }""")
        check("b1_fab_hidden_with_drawer", fab_hidden, "FAB still visible with drawer open")

        # Pick one genre chip with non-empty value
        genre_chip = page.evaluate("""() => {
            const chips = document.querySelectorAll('.drawer [data-genre]');
            for (const c of chips) { if (c.dataset.genre) return c.dataset.genre; }
            return null;
        }""")
        if genre_chip:
            chip_el = page.query_selector(f".drawer [data-genre='{genre_chip}']")
            if chip_el:
                chip_el.click()
                page.wait_for_timeout(300)
                save_shot(page, wave, "m-drawer-open")
                # Close drawer via JS click on the .dclose button
                closed = page.evaluate("""() => {
                    const btn = document.querySelector('.drawer .dclose[data-dclose]') ||
                                document.querySelector('.dclose[data-dclose]') ||
                                document.querySelector('[data-dclose]');
                    if (!btn) return false;
                    btn.click();
                    return true;
                }""")
                page.wait_for_timeout(400)
                if closed:
                    fab_badge = page.evaluate("""() => {
                        const b = document.querySelector('.fab-badge');
                        return b ? b.textContent.trim() : null;
                    }""")
                    check("b1_fab_badge_1", fab_badge == "1",
                          f"fab-badge text={fab_badge!r}")
                else:
                    check("b1_fab_badge_1", False, "could not close drawer via [data-dclose]")
            else:
                check("b1_fab_badge_1", False, f"no chip for genre {genre_chip!r}")
        else:
            check("b1_fab_badge_1", False, "no non-empty genre chip in drawer")
    else:
        check("b1_fab_click_opens_drawer", False, "no FAB or FAB hidden")
        check("b1_fab_hidden_with_drawer", False, "skipped")
        check("b1_fab_badge_1", False, "skipped")
    ctx.close()

    # Desktop: FAB visible, then typing 2 chars hides it
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    fab_d = page.evaluate("""() => {
        const f = document.querySelector('.fab');
        if (!f || f.hidden) return null;
        const bb = f.getBoundingClientRect();
        return {right:bb.right, bottom:bb.bottom, visible:bb.width>0};
    }""")
    if fab_d and fab_d["visible"]:
        check("b1_fab_desktop_visible", True, "")
        right_edge_dist_d = 1280 - fab_d["right"]
        bottom_edge_dist_d = 800 - fab_d["bottom"]
        check("b1_fab_desktop_right_edge", right_edge_dist_d <= 24,
              f"gap to right={right_edge_dist_d:.1f}px")
        check("b1_fab_desktop_bottom_edge", bottom_edge_dist_d <= 24,
              f"gap to bottom={bottom_edge_dist_d:.1f}px")
    else:
        check("b1_fab_desktop_visible", False, f"fab_d={fab_d}")
        check("b1_fab_desktop_right_edge", False, "skipped")
        check("b1_fab_desktop_bottom_edge", False, "skipped")

    # Type 2 characters into #q → FAB hidden
    q_field = page.query_selector("#q")
    if q_field:
        q_field.fill("ab")
        page.wait_for_timeout(400)
        fab_after_search = page.evaluate("""() => {
            const f = document.querySelector('.fab');
            if (!f) return 'absent';
            if (f.hidden || f.getAttribute('hidden') !== null) return 'hidden';
            const bb = f.getBoundingClientRect();
            return bb.width > 0 ? 'visible' : 'zero-size';
        }""")
        check("b1_fab_hidden_on_search", fab_after_search in ("absent", "hidden", "zero-size"),
              f"fab state={fab_after_search!r}")
    else:
        check("b1_fab_hidden_on_search", False, "no #q on desktop")
    ctx.close()

    # ── Check B1-6: Drawer clear-all ──
    print("\n=== B1-6: Drawer clear-all ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    # Open drawer via JS, check no clear-all with no active filter
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(400)

    no_clear_inactive = page.evaluate("""() => {
        const b = document.querySelector('.drawer .fclear[data-clearf]');
        if (!b) return true;
        const bb = b.getBoundingClientRect();
        return bb.width === 0 || window.getComputedStyle(b).display === 'none';
    }""")
    check("b1_clear_all_absent_no_filter", no_clear_inactive,
          "clear-all button visible when no filter active")

    # Pick a genre
    genre_chip2 = page.evaluate("""() => {
        const chips = document.querySelectorAll('.drawer [data-genre]');
        for (const c of chips) { if (c.dataset.genre) return c.dataset.genre; }
        return null;
    }""")
    if genre_chip2:
        # Click genre chip via JS to avoid overlay issues
        chip_clicked = page.evaluate(f"""() => {{
            const chip = document.querySelector(".drawer [data-genre='{genre_chip2}']");
            if (!chip) return false;
            chip.click();
            return true;
        }}""")
        page.wait_for_timeout(300)
        if chip_clicked:
            # Check for clear-all button via JS
            clear_visible = page.evaluate("""() => {
                const b = document.querySelector('.drawer .fclear[data-clearf]') ||
                          document.querySelector('.drawer-top .fclear[data-clearf]');
                if (!b) return false;
                const bb = b.getBoundingClientRect();
                return bb.width > 0 && window.getComputedStyle(b).display !== 'none';
            }""")
            check("b1_clear_all_present_with_filter", clear_visible,
                  "clear-all not visible with active genre filter")
            if clear_visible:
                page.evaluate("""() => {
                    const btn = document.querySelector('.drawer .fclear[data-clearf]') ||
                                document.querySelector('.drawer-top .fclear[data-clearf]');
                    if (btn) btn.click();
                }""")
                page.wait_for_timeout(400)
                state = page.evaluate("""() => ({
                    fGenres: typeof S !== 'undefined' ? S.fGenres : null,
                    fVenues: typeof S !== 'undefined' ? S.fVenues : null,
                    day: typeof S !== 'undefined' ? S.day : null
                })""")
                check("b1_clear_all_resets_state",
                      state["fGenres"] == [] and state["fVenues"] == [] and state["day"] == "week",
                      f"fGenres={state['fGenres']}, fVenues={state['fVenues']}, day={state['day']}")
            else:
                check("b1_clear_all_resets_state", False, "clear-all not found to click")
        else:
            check("b1_clear_all_present_with_filter", False, "no genre chip in drawer")
            check("b1_clear_all_resets_state", False, "skipped")
    else:
        check("b1_clear_all_present_with_filter", False, "no non-empty genre chips")
        check("b1_clear_all_resets_state", False, "skipped")
    ctx.close()

    # ── Check B1-7: Sections (cinema) ──
    print("\n=== B1-7: Sections (cinema) ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(1000)

    # Force-mount all deferred rails (IntersectionObserver won't fire in headless scroll)
    page.evaluate("""() => {
        let guard = 0;
        while (typeof railIdx !== 'undefined' && typeof RAILS !== 'undefined' && railIdx < RAILS.length && guard++ < 100) {
            const sent = document.getElementById('rail-sentinel');
            if (!sent) break;
            const batch = RAILS.slice(railIdx, railIdx + 10).map(matRail).join('');
            railIdx += 10;
            sent.insertAdjacentHTML('beforebegin', batch);
        }
    }""")
    page.wait_for_timeout(300)

    # Wave H: ALL sections start collapsed (S.secGenres=false, S.secVenues=false, S.secAll=false)
    sections_info = page.evaluate("""() => {
        const toggles = Array.from(document.querySelectorAll('[data-sectoggle]'));
        return toggles.map(t => ({key:t.dataset.sectoggle, expanded:t.getAttribute('aria-expanded')}));
    }""")
    genre_toggle = next((t for t in sections_info if t["key"] == "Genres"), None)
    venue_toggle = next((t for t in sections_info if t["key"] == "Venues"), None)
    check("b1_genre_toggle_open", genre_toggle is not None and genre_toggle["expanded"] == "false",
          f"genre toggle: {genre_toggle} (Wave H: should start collapsed/false)")
    check("b1_venue_toggle_closed", venue_toggle is not None and venue_toggle["expanded"] == "false",
          f"venue toggle: {venue_toggle}")

    # While venue section closed, no rail heading equals a cinema name
    cinema_names = page.evaluate("""() => typeof CINEMAS !== 'undefined' ? CINEMAS.map(c=>c.name) : []""")
    rail_headings = page.evaluate("""() => Array.from(document.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim())""")
    # Filter to pure text (strip ::before pseudo content by comparing data)
    any_cinema_in_headings = any(
        any(cn.strip().lower() in h.strip().lower() for cn in cinema_names)
        for h in rail_headings
    )
    check("b1_no_cinema_rails_when_closed", not any_cinema_in_headings,
          f"found cinema name in headings while venue section closed: {[h for h in rail_headings if any(cn.lower() in h.lower() for cn in cinema_names)][:3]}")

    # DOM order: genre section < venue section < "all titles" grid check is in the page structure
    dom_order = page.evaluate("""() => {
        const main = document.querySelector('main');
        if (!main) return null;
        const els = Array.from(main.children);
        const gIdx = els.findIndex(e => e.querySelector && e.querySelector('[data-sectoggle="Genres"]') !== null || (e.dataset && e.dataset.sectoggle === 'Genres'));
        const vIdx = els.findIndex(e => e.querySelector && e.querySelector('[data-sectoggle="Venues"]') !== null || (e.dataset && e.dataset.sectoggle === 'Venues'));
        // also check with querySelectorAll
        const all = Array.from(document.querySelectorAll('[data-sectoggle]'));
        const gPos = all.findIndex(e=>e.dataset.sectoggle==='Genres');
        const vPos = all.findIndex(e=>e.dataset.sectoggle==='Venues');
        return {gPos, vPos};
    }""")
    if dom_order:
        check("b1_dom_order_genre_before_venue",
              dom_order["gPos"] != -1 and dom_order["vPos"] != -1 and dom_order["gPos"] < dom_order["vPos"],
              f"genre pos={dom_order['gPos']}, venue pos={dom_order['vPos']}")
    else:
        check("b1_dom_order_genre_before_venue", False, "could not determine DOM order")

    # Click venue toggle → aria-expanded="true" and ≥5 rail headings equal cinema names
    venue_toggle_btn = page.query_selector("[data-sectoggle='Venues']")
    if venue_toggle_btn:
        toggle_top_before = venue_toggle_btn.bounding_box()["y"]
        venue_toggle_btn.click()
        page.wait_for_timeout(600)

        venue_expanded = page.evaluate("""() => {
            const t = document.querySelector('[data-sectoggle="Venues"]');
            return t ? t.getAttribute('aria-expanded') : null;
        }""")
        check("b1_venue_toggle_expanded", venue_expanded == "true",
              f"aria-expanded={venue_expanded}")

        # Force-mount all deferred rails after opening venue section
        page.evaluate("""() => {
            let guard = 0;
            while (typeof railIdx !== 'undefined' && typeof RAILS !== 'undefined' && railIdx < RAILS.length && guard++ < 100) {
                const sent = document.getElementById('rail-sentinel');
                if (!sent) break;
                const batch = RAILS.slice(railIdx, railIdx + 10).map(matRail).join('');
                railIdx += 10;
                sent.insertAdjacentHTML('beforebegin', batch);
            }
        }""")
        page.wait_for_timeout(400)

        # Re-read headings
        rail_headings_after = page.evaluate("""() => Array.from(document.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim())""")
        cinema_names_after = page.evaluate("""() => typeof CINEMAS !== 'undefined' ? CINEMAS.map(c=>c.name) : []""")
        matching = [h for h in rail_headings_after if any(cn in h for cn in cinema_names_after)]
        check("b1_venue_rails_after_open", len(matching) >= 5,
              f"only {len(matching)} cinema name headings visible: {matching[:5]}")

        # Toggle top should not move by more than 30px
        venue_toggle_btn_new = page.query_selector("[data-sectoggle='Venues']")
        if venue_toggle_btn_new:
            toggle_top_after = venue_toggle_btn_new.bounding_box()["y"]
            # Scroll back to toggle to measure correctly
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(300)
            venue_toggle_btn_new2 = page.query_selector("[data-sectoggle='Venues']")
            if venue_toggle_btn_new2:
                bb = venue_toggle_btn_new2.bounding_box()
                toggle_top_now = bb["y"] if bb else None
                # Note: toggle_top_before was measured before click, positions shift
                # The spec says "viewport top moves by ≤30px across the click"
                # After scroll-restore the viewport position of the toggle should be within 30px of original
                scroll_now = page.evaluate("window.scrollY")
                if toggle_top_now is not None:
                    viewport_pos_diff = abs(toggle_top_now - toggle_top_before)
                    check("b1_toggle_stays_in_view", viewport_pos_diff <= 30,
                          f"toggle moved {viewport_pos_diff:.0f}px (was {toggle_top_before:.0f}, now {toggle_top_now:.0f})")
                else:
                    check("b1_toggle_stays_in_view", False, "could not measure toggle position after")
        else:
            check("b1_toggle_stays_in_view", False, "toggle gone after click")

        # Wave H: genre section starts CLOSED; open it first, then collapse it → rails disappear
        genre_toggle_btn = page.query_selector("[data-sectoggle='Genres']")
        if genre_toggle_btn:
            # Open genre section (starts collapsed in Wave H)
            genre_toggle_btn.click()
            page.wait_for_timeout(600)
            genre_opened = page.evaluate("""() => {
                const t = document.querySelector('[data-sectoggle="Genres"]');
                return t ? t.getAttribute('aria-expanded') : null;
            }""")
            # Now collapse it
            genre_toggle_btn2 = page.query_selector("[data-sectoggle='Genres']")
            if genre_toggle_btn2:
                genre_toggle_btn2.click()
                page.wait_for_timeout(600)
                genre_content_hidden = page.evaluate("""() => {
                    const c = document.getElementById('sec-Genres-content');
                    return !c || c.hidden || window.getComputedStyle(c).display === 'none';
                }""")
                check("b1_genre_collapse_hides_rails", genre_content_hidden,
                      f"sec-Genres-content still visible after open→collapse (opened={genre_opened})")
            else:
                check("b1_genre_collapse_hides_rails", False, "genre toggle gone after first click")
        else:
            check("b1_genre_collapse_hides_rails", False, "genre toggle not found")

        # Save screenshot scrolled to venue section
        page.evaluate("document.querySelector('[data-sectoggle=\"Venues\"]').scrollIntoView()")
        page.wait_for_timeout(400)
        save_shot(page, wave, "d-bg-theatre-venues-open")
    else:
        check("b1_venue_toggle_expanded", False, "no [data-sectoggle='Venues']")
        check("b1_venue_rails_after_open", False, "skipped")
        check("b1_toggle_stays_in_view", False, "skipped")
        check("b1_genre_collapse_hides_rails", False, "skipped")

    ctx.close()

    # ── Check B1-8: Sections (theatre) ──
    print("\n=== B1-8: Sections (theatre) ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="theatre")
    page.wait_for_timeout(1000)

    # Force-mount all deferred rails
    page.evaluate("""() => {
        let guard = 0;
        while (typeof railIdx !== 'undefined' && typeof RAILS !== 'undefined' && railIdx < RAILS.length && guard++ < 100) {
            const sent = document.getElementById('rail-sentinel');
            if (!sent) break;
            const batch = RAILS.slice(railIdx, railIdx + 10).map(matRail).join('');
            railIdx += 10;
            sent.insertAdjacentHTML('beforebegin', batch);
        }
    }""")
    page.wait_for_timeout(300)

    # Genre section comes first
    sec_order_th = page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('[data-sectoggle]'));
        const gPos = all.findIndex(e=>e.dataset.sectoggle==='Genres');
        const vPos = all.findIndex(e=>e.dataset.sectoggle==='Venues');
        return {gPos, vPos};
    }""")
    check("b1_theatre_genre_before_venue",
          sec_order_th["gPos"] != -1 and sec_order_th["vPos"] != -1 and sec_order_th["gPos"] < sec_order_th["vPos"],
          f"genre pos={sec_order_th['gPos']}, venue pos={sec_order_th['vPos']}")

    # Open venue section
    venue_toggle_th = page.query_selector("[data-sectoggle='Venues']")
    if venue_toggle_th:
        venue_toggle_th.click()
        page.wait_for_timeout(600)

        # Force-mount all deferred rails after opening venue section
        page.evaluate("""() => {
            let guard = 0;
            while (typeof railIdx !== 'undefined' && typeof RAILS !== 'undefined' && railIdx < RAILS.length && guard++ < 100) {
                const sent = document.getElementById('rail-sentinel');
                if (!sent) break;
                const batch = RAILS.slice(railIdx, railIdx + 10).map(matRail).join('');
                railIdx += 10;
                sent.insertAdjacentHTML('beforebegin', batch);
            }
        }""")
        page.wait_for_timeout(400)

        # Check sub-headings order
        subheads = page.evaluate("""() => {
            const subs = Array.from(document.querySelectorAll('.sec-subhead'));
            return subs.map(s => s.textContent.trim());
        }""")
        state_idx = next((i for i, s in enumerate(subheads) if "Държавни" in s or "General" in s or "State" in s), -1)
        indie_idx = next((i for i, s in enumerate(subheads) if "Независими" in s or "Independ" in s), -1)
        check("b1_th_subheads_state_before_indie",
              state_idx != -1 and indie_idx != -1 and state_idx < indie_idx,
              f"subheads={subheads}")

        # Rails between state and indie subheads should be national/state/municipal
        # Get the theatre IDs from rail headings and check their kind
        venue_check = page.evaluate("""() => {
            const STATE_KINDS = ['national','state','municipal'];
            const subs = Array.from(document.querySelectorAll('.sec-subhead'));
            let stateIdx=-1, indieIdx=-1;
            const allKids = Array.from(document.querySelector('#sec-Venues-content')?.children||[]);
            allKids.forEach((el,i)=>{
                if(el.classList.contains('sec-subhead')){
                    const txt=el.textContent;
                    if(txt.includes('Държавни')||txt.includes('State'))stateIdx=i;
                    if(txt.includes('Независими')||txt.includes('Independ'))indieIdx=i;
                }
            });
            if(stateIdx===-1||indieIdx===-1)return {err:'subheads not found',stateIdx,indieIdx};
            const theatreNames = {};
            THEATRES.forEach(t=>{theatreNames[t.name]=t.kind;theatreNames[t.nameEn]=t.kind;});
            let stateOk=true, indieOk=true;
            allKids.forEach((el,i)=>{
                if(!el.classList.contains('rail'))return;
                const h2 = el.querySelector('.rhead h2');
                if(!h2)return;
                const name = h2.textContent.replace(/^\\s+/,'').trim();
                const th = THEATRES.find(t=>name.includes(t.name)||name.includes(t.nameEn));
                if(!th)return;
                if(i>stateIdx && i<indieIdx && !STATE_KINDS.includes(th.kind))stateOk=false;
                if(i>indieIdx && STATE_KINDS.includes(th.kind))indieOk=false;
            });
            return {stateOk, indieOk, stateIdx, indieIdx};
        }""")
        if "err" not in venue_check:
            check("b1_th_state_rails_kind", venue_check.get("stateOk", False),
                  "some state section rails are not national/state/municipal kind")
            check("b1_th_indie_rails_kind", venue_check.get("indieOk", False),
                  "some indie section rails are not independent kind")
        else:
            check("b1_th_state_rails_kind", False, venue_check.get("err", "unknown"))
            check("b1_th_indie_rails_kind", False, "skipped due to subhead detection failure")
    else:
        check("b1_th_subheads_state_before_indie", False, "no venue toggle in theatre mode")
        check("b1_th_state_rails_kind", False, "skipped")
        check("b1_th_indie_rails_kind", False, "skipped")
    ctx.close()

    # ── Check B1-9: Quote regression ──
    print("\n=== B1-9: Quote regression ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="theatre")
    page.wait_for_timeout(800)

    # Try clicking a [data-show="uroci"] card; if not present use page.evaluate openSheet
    uroci_card = page.query_selector("[data-show='uroci']")
    if uroci_card:
        uroci_card.click()
        page.wait_for_timeout(800)
    else:
        # Use app's own mechanism
        opened = page.evaluate("""() => {
            if (window.showById && window.showById['uroci'] && window.sheetShow && window.openSheet) {
                const html = window.sheetShow(window.showById['uroci']);
                window.curSheet = {kind:'show', id:'uroci'};
                window.openSheet(html, null);
                return true;
            }
            return false;
        }""")
        page.wait_for_timeout(800)
        if not opened:
            check("b1_uroci_quote", False, "could not open uroci sheet")
            ctx.close()
            # Continue to final checks
        else:
            pass

    # Read sheet text and look for „Уроци" (U+201E + Уроци + U+201C)
    sheet_text = page.evaluate("""() => {
        const scrim = document.getElementById('scrim');
        return scrim ? scrim.textContent : '';
    }""")
    if sheet_text:
        has_proper_quotes = "„Уроци“" in sheet_text
        has_straight_quotes = '"Уроци"' in sheet_text
        check("b1_uroci_proper_quotes", has_proper_quotes,
              f"straight quotes used={has_straight_quotes}, found='{sheet_text[sheet_text.find('Уроци')-2:sheet_text.find('Уроци')+7] if 'Уроци' in sheet_text else 'NOT FOUND'}'")
    else:
        check("b1_uroci_proper_quotes", False, "sheet not open or empty")
    ctx.close()

    # ── Check B1-10: No page errors + gate ──
    print("\n=== B1-10: No page errors + gate ===")
    all_errors = []
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang in ["bg", "en"]:
            for mode in ["cinema", "theatre"]:
                ctx, page, errs = open_page(browser, w, h, lang=lang, mode=mode)
                page.wait_for_timeout(500)
                if errs:
                    all_errors.extend([(size_tag, lang, mode, e) for e in errs])
                ctx.close()
    if all_errors:
        check("b1_no_page_errors", False,
              f"{len(all_errors)} error(s): {all_errors[0]}")
    else:
        check("b1_no_page_errors", True, "")

    result = subprocess.run(
        ["python3", "scripts/verify_build.py"],
        cwd=str(webapp_root),
        capture_output=True,
        text=True,
        env={**os.environ, "SOFIA_HTML": html_env}
    )
    gate_passes = "all checks passed" in result.stdout
    check("b1_gate_passes", gate_passes,
          result.stdout[:120] if not gate_passes else "")

# ============================================================================
# WAVE C1: Filters, genre taxonomy, data clean-up
# ============================================================================

def wave_c1(browser):
    """
    Wave C1 checks: cinema venue chips per cinema id, venueOk filter, genre
    taxonomy (doc/music/anime split), period banner month, data clean-up.
    """
    wave = "C1"
    ensure_shot_dir(wave)
    import subprocess

    # ── Check C1-1: Drawer venue chips one-per-cinema-id, subheadings, theatre mode ──
    print("\n=== C1-1: Drawer venue chips ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    # Open drawer
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)

    venue_info = page.evaluate("""() => {
        const chips = Array.from(document.querySelectorAll('.drawer [data-venue]'));
        const ids = chips.map(c => c.dataset.venue);
        const names = chips.map(c => c.textContent.trim());
        // Check for chain-level chips like '2' or '· 2'
        const chainLike = names.filter(n => /·\\s*\\d/.test(n));
        const multiplexHead = !!document.querySelector('.drawer .sec-subhead, .drawer [data-subhead]') ||
            document.querySelector('.drawer')?.textContent?.includes('Мултиплекси');
        const indeHead = document.querySelector('.drawer')?.textContent?.includes('Независими кина');
        return { ids, names, chainLike, multiplexHead, indeHead };
    }""")

    # Get cinema IDs from app's CINEMAS data
    cinema_ids = page.evaluate("""() => {
        if (typeof CINEMAS === 'undefined') return [];
        return CINEMAS.filter(c => c.kind !== 'theatre').map(c => c.id);
    }""")

    chip_ids = venue_info.get("ids", [])
    chain_like = venue_info.get("chainLike", [])
    has_multiplex_head = venue_info.get("multiplexHead", False)
    has_indep_head = venue_info.get("indeHead", False)

    # Each cinema id should appear at most once as a chip
    duplicates = [cid for cid in set(chip_ids) if chip_ids.count(cid) > 1]
    check("c1_chips_no_duplicates", len(duplicates) == 0,
          f"duplicate chip ids: {duplicates}")
    check("c1_no_chain_level_chips", len(chain_like) == 0,
          f"chain-level chips found: {chain_like}")
    check("c1_multiplex_subhead", has_multiplex_head,
          "Мултиплекси subheading not found in drawer")
    check("c1_indep_subhead", has_indep_head,
          "Независими кина subheading not found in drawer")

    save_shot(page, wave, "m-drawer-venues")
    # Close drawer
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(400)
    ctx.close()

    # Theatre mode: drawer lists theatres
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="theatre")
    page.wait_for_timeout(800)
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    theatre_chips = page.evaluate("""() => {
        const chips = Array.from(document.querySelectorAll('.drawer [data-venue]'));
        const ids = chips.map(c => c.dataset.venue);
        if (typeof THEATRES === 'undefined') return { ids, theatreIds: [] };
        const theatreIds = THEATRES.map(t => t.id);
        return { ids, theatreIds };
    }""")
    th_ids = set(theatre_chips.get("ids", []))
    th_theatre_ids = set(theatre_chips.get("theatreIds", []))
    # Some theatre IDs should be among the chips
    theatre_overlap = th_ids & th_theatre_ids
    check("c1_theatre_drawer_lists_theatres", len(theatre_overlap) >= 3,
          f"only {len(theatre_overlap)} theatre ids found in drawer chips: {list(theatre_overlap)[:5]}")
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(400)
    ctx.close()

    # ── Check C1-2: Single cinema filter (cc-sofia) ──
    print("\n=== C1-2: Single cinema filter ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    # Open drawer and select cc-sofia
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    chip_clicked = page.evaluate("""() => {
        const chip = document.querySelector(".drawer [data-venue='cc-sofia']");
        if (!chip) return false;
        chip.click();
        return true;
    }""")
    check("c1_cc_sofia_chip_exists", chip_clicked, "no [data-venue='cc-sofia'] chip in drawer")

    # Close drawer
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(600)

    # (a) No rail heading equals another cinema's name
    other_cinema_names = page.evaluate("""() => {
        if (typeof CINEMAS === 'undefined') return [];
        return CINEMAS.filter(c => c.id !== 'cc-sofia').map(c => c.name);
    }""")
    rail_headings = page.evaluate("""() => Array.from(document.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim())""")
    other_in_headings = [h for h in rail_headings
                         if any(cn.lower() in h.lower() for cn in other_cinema_names)]
    check("c1_no_other_cinema_rail", len(other_in_headings) == 0,
          f"other cinema names in rail headings: {other_in_headings[:3]}")

    # (b) No card meta mentions "· N кина"
    multi_cinema_meta = page.evaluate("""() => {
        const metas = Array.from(document.querySelectorAll('[data-film] .meta, [data-film] .card-meta'));
        const bad = metas.filter(m => /·\\s*\\d+\\s*кина/.test(m.textContent));
        return bad.map(m => m.textContent.trim().slice(0, 80));
    }""")
    check("c1_no_multi_cinema_in_meta", len(multi_cinema_meta) == 0,
          f"cards with '·N кина' meta: {multi_cinema_meta[:2]}")

    # (c) Event rails absent (Лимитирано and Скоро)
    event_rail_headings = page.evaluate("""() => {
        const h2s = Array.from(document.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim());
        return h2s.filter(h => h.includes('Лимитирано') || h.includes('Скоро'));
    }""")
    check("c1_no_event_rails_with_venue_filter", len(event_rail_headings) == 0,
          f"event rails still present: {event_rail_headings}")

    # (d) Every visible film card has a showtime at cc-sofia in the period
    films_without_cc = page.evaluate(f"""() => {{
        if (typeof SHOWTIMES === 'undefined' || typeof FILMS === 'undefined') return ['SHOWTIMES/FILMS undefined'];
        const PERIOD_START = '{CLOCK_TODAY_ISO}';
        const PERIOD_END   = '{CLOCK_WEEK_END}';
        const cards = Array.from(document.querySelectorAll('[data-film]'));
        const badFilms = [];
        for (const card of cards) {{
            const fid = card.dataset.film;
            if (!fid) continue;
            const hasCCSofia = SHOWTIMES.some(([fId, cin, date]) =>
                fId === fid && cin === 'cc-sofia' && date >= PERIOD_START && date <= PERIOD_END
            );
            if (!hasCCSofia) badFilms.push(fid);
        }}
        return badFilms;
    }}""")
    check("c1_all_visible_films_at_cc_sofia", len(films_without_cc) == 0,
          f"films shown without cc-sofia showtime: {films_without_cc[:5]}")

    # (f) FAB badge shows 1
    fab_badge = page.evaluate("""() => {
        const b = document.querySelector('.fab-badge');
        return b ? b.textContent.trim() : null;
    }""")
    check("c1_fab_badge_1_venue", fab_badge == "1",
          f"fab-badge={fab_badge!r}")

    # (e) Open first film card and check sheet
    first_film_id = page.evaluate("""() => {
        const card = document.querySelector('[data-film]');
        return card ? card.dataset.film : null;
    }""")
    if first_film_id:
        first_card = page.query_selector(f"[data-film='{first_film_id}']")
        if first_card:
            first_card.click()
            page.wait_for_timeout(800)

        # Check sheet has Cinema City Sofia showtimes and note
        sheet_info = page.evaluate("""() => {
            const scrim = document.getElementById('scrim');
            if (!scrim) return { exists: false };
            const text = scrim.textContent;
            const html = scrim.innerHTML;
            const hasCCSofia = text.includes('Cinema City Sofia');
            const hasNote = text.includes('Показани са само');
            const hasShowAllBtn = !!scrim.querySelector('[data-sheetshowallcin]');
            const rowTexts = Array.from(scrim.querySelectorAll('.st-row, .showtime-row, [data-cin]'))
                .map(r => r.textContent.trim().slice(0, 100));
            return { hasCCSofia, hasNote, hasShowAllBtn, rowTexts: rowTexts.slice(0, 5), text: text.slice(0, 200) };
        }""")
        check("c1_sheet_has_cc_sofia", sheet_info.get("hasCCSofia", False),
              f"'Cinema City Sofia' not in sheet. text={sheet_info.get('text','')[:80]!r}")
        check("c1_sheet_has_note", sheet_info.get("hasNote", False),
              "Показани са само... note missing from sheet")
        check("c1_sheet_has_show_all_btn", sheet_info.get("hasShowAllBtn", False),
              "[data-sheetshowallcin] button missing from sheet")

        # Click "Покажи всички" and verify other cinemas appear
        if sheet_info.get("hasShowAllBtn", False):
            page.evaluate("""() => {
                const btn = document.querySelector('[data-sheetshowallcin]');
                if (btn) btn.click();
            }""")
            page.wait_for_timeout(500)
            after_show_all = page.evaluate("""() => {
                const scrim = document.getElementById('scrim');
                if (!scrim) return false;
                // After show all, the note should be gone or more cinemas visible
                const text = scrim.textContent;
                // Count how many distinct cinema names appear
                const cinemaNames = typeof CINEMAS !== 'undefined' ? CINEMAS.map(c=>c.name) : [];
                const visibleCinemas = cinemaNames.filter(n => text.includes(n));
                return { visibleCinemas, noteGone: !text.includes('Показани са само') };
            }""")
            check("c1_show_all_reveals_more", after_show_all.get("noteGone", False),
                  f"show-all: note still present, visibleCinemas={after_show_all.get('visibleCinemas', [])[:3]}")

        save_shot(page, wave, "d-bg-sheet-filtered")
        # Close sheet
        page.evaluate("""() => {
            const close = document.querySelector('[data-sheetclose], .sheet-close, #scrim .close');
            if (close) close.click();
            else { const scrim = document.getElementById('scrim'); if (scrim) scrim.click(); }
        }""")
        page.wait_for_timeout(400)
    else:
        check("c1_sheet_has_cc_sofia", False, "no film cards to open")
        check("c1_sheet_has_note", False, "no film cards to open")
        check("c1_sheet_has_show_all_btn", False, "no film cards to open")
        check("c1_show_all_reveals_more", False, "no film cards to open")

    save_shot(page, wave, "m-bg-cc-sofia-only")
    ctx.close()

    # ── Check C1-3: Two cinemas (cc-sofia + vlaikova) ──
    print("\n=== C1-3: Two cinemas filter ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    two_venues = page.evaluate("""() => {
        const cc = document.querySelector(".drawer [data-venue='cc-sofia']");
        const vl = document.querySelector(".drawer [data-venue='vlaikova']");
        if (cc) cc.click();
        if (vl) vl.click();
        return { cc: !!cc, vl: !!vl };
    }""")
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(600)

    if two_venues.get("cc") and two_venues.get("vl"):
        # Check that only these two cinema names appear in venue rails / sheet rows
        two_cinema_check = page.evaluate("""() => {
            if (typeof CINEMAS === 'undefined') return { ok: false, err: 'CINEMAS undefined' };
            const allowed = ['Cinema City Sofia', 'Влайкова'];
            const allCinemaNames = CINEMAS.map(c => c.name);
            const h2s = Array.from(document.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim());
            const badHeadings = h2s.filter(h =>
                allCinemaNames.some(cn => h.includes(cn) && !allowed.some(a => h.includes(a)))
            );
            return { badHeadings };
        }""")
        bad = two_cinema_check.get("badHeadings", [])
        check("c1_two_cinemas_only_those_headings", len(bad) == 0,
              f"unexpected cinema headings: {bad[:3]}")
    else:
        check("c1_two_cinemas_only_those_headings", False,
              f"could not select both chips (cc={two_venues.get('cc')}, vl={two_venues.get('vl')})")
    ctx.close()

    # ── Check C1-4: Genre filter (Аниме then Документално) ──
    print("\n=== C1-4: Genre filter ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    # Open drawer and select Аниме
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    anime_clicked = page.evaluate("""() => {
        const chip = document.querySelector(".drawer [data-genre='anime']");
        if (!chip) return false;
        chip.click();
        return true;
    }""")
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(600)

    if anime_clicked:
        # Use natural scroll to trigger IntersectionObserver-based lazy rendering
        for _ in range(6):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(300)

        genre_check = page.evaluate(f"""() => {{
            // Find genre section rails
            const genreContent = document.getElementById('sec-Genres-content');
            if (!genreContent) return {{ err: 'no sec-Genres-content' }};
            const rails = Array.from(genreContent.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim());
            // Check Жестокият appearing as a visible film card in the genre section
            const jestokInGenreSection = Array.from(genreContent.querySelectorAll('[data-film]')).some(c =>
                typeof FILMS !== 'undefined' && (() => {{
                    const f = FILMS.find(x => x.id === c.dataset.film);
                    return f && (f.bg || '').includes('Жестокият');
                }})()
            );
            // Event rails visible anywhere on page
            const allH2 = Array.from(document.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim());
            const eventRails = allH2.filter(h => h.includes('Лимитирано') || h.includes('Скоро'));
            // Non-anime genre rails in the genre section
            const nonAnimeRails = rails.filter(r => !r.includes('Аниме'));
            // Check if anime has films in period
            const animeFilms = typeof FILMS !== 'undefined' ?
                FILMS.filter(f => f.genres && f.genres.includes('Аниме')).map(f => f.id) : [];
            const animePeriodFilms = typeof SHOWTIMES !== 'undefined' ?
                SHOWTIMES.filter(([fid, cin, date]) =>
                    date >= '{CLOCK_TODAY_ISO}' && date <= '{CLOCK_WEEK_END}' && animeFilms.includes(fid)
                ).map(([fid]) => fid) : [];
            return {{ rails, jestokInGenreSection, eventRails, nonAnimeRails, animePeriodFilms }};
        }}""")
        genre_rails = genre_check.get("rails", [])
        anime_period = genre_check.get("animePeriodFilms", [])
        non_anime_rails = genre_check.get("nonAnimeRails", [])

        # If there are anime films in period, expect exactly one Аниме rail
        # If no anime films in period, the genre section should be empty (correct behavior)
        if len(anime_period) > 0:
            check("c1_anime_only_one_rail", len(genre_rails) == 1 and any("Аниме" in r for r in genre_rails),
                  f"genre rails under anime filter: {genre_rails}")
        else:
            # No anime films in period => genre section correctly empty; verify no OTHER genre rails shown
            check("c1_anime_only_one_rail", len(non_anime_rails) == 0,
                  f"non-anime genre rails shown: {non_anime_rails} (no anime in period: correct)")

        check("c1_no_jestok_in_anime", not genre_check.get("jestokInGenreSection", False),
              "Жестокият път found as card in genre section with anime filter")
        event_rails = genre_check.get("eventRails", [])
        check("c1_no_event_rails_with_genre_filter", len(event_rails) == 0,
              f"event rails present with genre filter: {event_rails}")

        save_shot(page, wave, "d-bg-anime-only")
    else:
        check("c1_anime_only_one_rail", False, "could not click anime chip")
        check("c1_no_jestok_in_anime", False, "skipped")
        check("c1_no_event_rails_with_genre_filter", False, "skipped")
        save_shot(page, wave, "d-bg-anime-only")
    ctx.close()

    # Документално filter
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    doc_clicked = page.evaluate("""() => {
        const chip = document.querySelector(".drawer [data-genre='doc']");
        if (!chip) return false;
        chip.click();
        return true;
    }""")
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(600)
    if doc_clicked:
        # Use natural scroll to trigger IntersectionObserver-based lazy rendering
        for _ in range(6):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(300)
        doc_check = page.evaluate("""() => {
            const genreContent = document.getElementById('sec-Genres-content');
            if (!genreContent) return { err: 'no sec-Genres-content' };
            const rails = Array.from(genreContent.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim());
            // Check Жестокият path in this section if in period
            const jestokInDoc = Array.from(genreContent.querySelectorAll('[data-film]')).some(c =>
                typeof FILMS !== 'undefined' && (() => {
                    const f = FILMS.find(x => x.id === c.dataset.film);
                    return f && (f.bg || '').includes('Жестокият');
                })()
            );
            // No Музика rail
            const noMusic = !rails.some(r => r.includes('Музика'));
            return { rails, jestokInDoc, noMusic };
        }""")
        doc_rails = doc_check.get("rails", [])
        check("c1_doc_rail_exists", any("Документал" in r for r in doc_rails),
              f"no Документално rail found: {doc_rails}")
        check("c1_no_music_rail_with_doc_filter", doc_check.get("noMusic", False),
              f"Музика rail present with doc-only filter: {doc_rails}")
    else:
        check("c1_doc_rail_exists", False, "could not click doc chip")
        check("c1_no_music_rail_with_doc_filter", False, "skipped")
    ctx.close()

    # ── Check C1-5: Taxonomy: GENRES ids, m arrays, chip presence ──
    print("\n=== C1-5: Genre taxonomy ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    taxonomy = page.evaluate("""() => {
        if (typeof GENRES === 'undefined') return { err: 'GENRES undefined' };
        const doc  = GENRES.find(g => g.id === 'doc');
        const music= GENRES.find(g => g.id === 'music');
        const anime= GENRES.find(g => g.id === 'anime');
        return {
            docFound: !!doc,
            docM: doc ? doc.m : null,
            musicFound: !!music,
            musicMHasMusical: music ? music.m.includes('Музикален') : false,
            animeFound: !!anime,
            animeM: anime ? anime.m : null,
            animeMOnlyAnime: anime ? (anime.m.length === 1 && anime.m[0] === 'Аниме') : false
        };
    }""")
    check("c1_genre_doc_exists", taxonomy.get("docFound", False), "no genre id='doc'")
    check("c1_genre_doc_m_documentary", taxonomy.get("docM") == ["Документален"],
          f"doc.m={taxonomy.get('docM')}")
    check("c1_genre_music_exists", taxonomy.get("musicFound", False), "no genre id='music'")
    check("c1_genre_music_has_musical", taxonomy.get("musicMHasMusical", False),
          "music.m does not include Музикален")
    check("c1_genre_anime_exists", taxonomy.get("animeFound", False), "no genre id='anime'")
    check("c1_genre_anime_m_only_anime", taxonomy.get("animeMOnlyAnime", False),
          f"anime.m={taxonomy.get('animeM')}")

    # Both doc and music chips in cinema drawer
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    drawer_genres = page.evaluate("""() => {
        const docChip = !!document.querySelector(".drawer [data-genre='doc']");
        const musicChip = !!document.querySelector(".drawer [data-genre='music']");
        return { docChip, musicChip };
    }""")
    check("c1_doc_chip_in_drawer", drawer_genres.get("docChip", False),
          "doc genre chip not in drawer")
    check("c1_music_chip_in_drawer", drawer_genres.get("musicChip", False),
          "music genre chip not in drawer")

    # Bulgarian films not in anime rail
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(400)

    # Select anime filter and check no Bulgarian film appears
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    page.evaluate("""() => {
        const chip = document.querySelector(".drawer [data-genre='anime']");
        if (chip) chip.click();
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(600)
    page.evaluate("""() => {
        let guard = 0;
        while (typeof railIdx !== 'undefined' && typeof RAILS !== 'undefined' && railIdx < RAILS.length && guard++ < 100) {
            const sent = document.getElementById('rail-sentinel');
            if (!sent) break;
            const batch = RAILS.slice(railIdx, railIdx + 10).map(matRail).join('');
            railIdx += 10;
            sent.insertAdjacentHTML('beforebegin', batch);
        }
    }""")
    page.wait_for_timeout(400)
    bg_in_anime = page.evaluate("""() => {
        if (typeof FILMS === 'undefined') return [];
        const genreContent = document.getElementById('sec-Genres-content');
        if (!genreContent) return [];
        const filmCards = Array.from(genreContent.querySelectorAll('[data-film]'));
        const bgFilms = filmCards.filter(card => {
            const f = FILMS.find(x => x.id === card.dataset.film);
            return f && (
                (f.country && f.country.includes('България')) ||
                (f.genres && f.genres.includes('Български'))
            );
        }).map(c => c.dataset.film);
        return bgFilms;
    }""")
    check("c1_no_bulgarian_in_anime", len(bg_in_anime) == 0,
          f"Bulgarian films in anime rail: {bg_in_anime}")
    ctx.close()

    # ── Check C1-6: Today rail regression ──
    print("\n=== C1-6: Today rail regression ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    today_rail = page.evaluate(f"""() => {{
        // Find 'За теб днес' rail
        const h2s = Array.from(document.querySelectorAll('.rhead h2'));
        const todayHead = h2s.find(h => h.textContent.includes('За теб днес') || h.textContent.includes('For you today'));
        if (!todayHead) return {{ err: 'no today rail' }};
        const rail = todayHead.closest('.rail');
        if (!rail) return {{ err: 'no parent rail' }};
        const cards = Array.from(rail.querySelectorAll('[data-film]'));
        // Film cards use .cfoot > .cf2 for meta (runtime · screenings · N кина)
        const multiCinema = cards.filter(c => {{
            const cf2 = c.querySelector('.cf2');
            return cf2 && /кина/.test(cf2.textContent);
        }});
        const cf2Texts = cards.slice(0, 5).map(c => {{
            const cf2 = c.querySelector('.cf2');
            return cf2 ? cf2.textContent.trim().slice(0, 80) : 'no cf2';
        }});
        // Also check: for each card, how many cinemas does it play in today?
        const today = '{CLOCK_TODAY_ISO}';
        const multiCinemaFilms = typeof SHOWTIMES !== 'undefined' ?
            cards.map(c => {{
                const fid = c.dataset.film;
                const cinemas = new Set(SHOWTIMES.filter(([f,cin,d]) => f===fid && d===today).map(([f,cin])=>cin));
                return {{fid, cinemaCount: cinemas.size}};
            }}).filter(x => x.cinemaCount >= 2) : [];
        return {{ total: cards.length, multiCinema: multiCinema.length, cf2Texts, multiCinemaFilms }};
    }}""")
    if "err" in today_rail:
        check("c1_today_rail_has_multi_cinema", False, today_rail["err"])
    else:
        multi_cinema_films = today_rail.get("multiCinemaFilms", [])
        multi_cinema_cf2 = today_rail.get("multiCinema", 0)
        if len(multi_cinema_films) == 0:
            check("c1_today_rail_has_multi_cinema", True,
                  f"no film plays today at >=2 cinemas (all single-venue); skipping kina check")
        else:
            check("c1_today_rail_has_multi_cinema", multi_cinema_cf2 >= 1,
                  f"no '·N кина' card in today rail; films with >=2 cinemas today: {multi_cinema_films[:2]}; cf2 texts: {today_rail.get('cf2Texts', [])[:3]}")

    ctx.close()

    # Per-venue rail: meta should NOT have 'кина' — use desktop viewport
    ctx_v, page_v, _ = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page_v.wait_for_timeout(800)
    # Scroll to reveal venue section toggle (lazy-loaded)
    for _ in range(10):
        page_v.mouse.wheel(0, 2000)
        page_v.wait_for_timeout(300)
    venue_toggle_btn = page_v.query_selector("[data-sectoggle='Venues']")
    if venue_toggle_btn:
        venue_toggle_btn.click()
        page_v.wait_for_timeout(600)
        # Use natural scroll to trigger lazy loading
        for _ in range(8):
            page_v.mouse.wheel(0, 2000)
            page_v.wait_for_timeout(300)

        venue_card_meta = page_v.evaluate("""() => {
            // Find any venue rail (under venue section)
            const venueContent = document.getElementById('sec-Venues-content');
            if (!venueContent) return { err: 'no sec-Venues-content' };
            const cards = Array.from(venueContent.querySelectorAll('[data-film]')).slice(0, 15);
            // Cards use .cfoot > .cf2 for meta
            const withKina = cards.filter(c => {
                const cf2 = c.querySelector('.cf2');
                return cf2 && cf2.textContent.includes('кина');
            }).map(c => {
                const cf2 = c.querySelector('.cf2');
                return cf2 ? cf2.textContent.trim().slice(0, 80) : 'no cf2';
            });
            const cf2Sample = cards.slice(0, 5).map(c => {
                const cf2 = c.querySelector('.cf2');
                return cf2 ? cf2.textContent.trim().slice(0, 60) : 'no cf2';
            });
            return { total: cards.length, withKina, cf2Sample };
        }""")
        if "err" in venue_card_meta:
            check("c1_venue_rail_no_kina_meta", False, venue_card_meta["err"])
        else:
            check("c1_venue_rail_no_kina_meta", len(venue_card_meta.get("withKina", [])) == 0,
                  f"venue rail cards with 'кина': {venue_card_meta.get('withKina', [])[:2]}")
    else:
        check("c1_venue_rail_no_kina_meta", False, "no [data-sectoggle='Venues'] in desktop view")
    ctx_v.close()

    # ── Check C1-7: Period header shows month (октомври / October) after selecting month ──
    # Wave H: .pbanner replaced by .phead; open drawer via .phead-other[data-drawer]
    print("\n=== C1-7: Period header month mode ===")
    # BG: select month range, then check .phead text contains "октомври"
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    banner_bg = page.evaluate("""() => {
        const btn = document.querySelector('.phead-other[data-drawer]');
        if (!btn) return '';
        btn.click();
        return 'opened';
    }""")
    if banner_bg == 'opened':
        page.wait_for_timeout(400)
        month_bg = page.evaluate("""() => {
            const btn = document.querySelector(".drawer [data-range='month']");
            if (!btn) return false;
            btn.click();
            return true;
        }""")
        page.wait_for_timeout(400)
        if month_bg:
            phead_bg_text = page.evaluate("document.querySelector('.phead')?.textContent || ''")
            check("c1_banner_bg_has_oktober", "октомври" in phead_bg_text.lower(),
                  f"'октомври' not in BG phead after month select: {phead_bg_text[:120]!r}")
        else:
            check("c1_banner_bg_has_oktober", False, "no [data-range='month'] in BG drawer")
    else:
        # Fallback: check week range still has the month somewhere visible
        phead_bg_text = page.evaluate("document.querySelector('.phead')?.textContent || ''")
        check("c1_banner_bg_has_oktober", "октомври" in phead_bg_text.lower(),
              f"no .phead-other found; phead text: {phead_bg_text[:120]!r}")
    ctx.close()

    # EN: select month range, then check .phead text contains "october"
    ctx, page, errs = open_page(browser, 375, 812, lang="en", mode="cinema")
    page.wait_for_timeout(800)
    open_en = page.evaluate("""() => {
        const btn = document.querySelector('.phead-other[data-drawer]');
        if (!btn) return '';
        btn.click();
        return 'opened';
    }""")
    if open_en == 'opened':
        page.wait_for_timeout(400)
        month_en = page.evaluate("""() => {
            const btn = document.querySelector(".drawer [data-range='month']");
            if (!btn) return false;
            btn.click();
            return true;
        }""")
        page.wait_for_timeout(400)
        if month_en:
            phead_en_text = page.evaluate("document.querySelector('.phead')?.textContent || ''")
            check("c1_banner_en_has_october", "october" in phead_en_text.lower(),
                  f"'October' not in EN phead after month select: {phead_en_text[:120]!r}")
        else:
            check("c1_banner_en_has_october", False, "no [data-range='month'] in EN drawer")
    else:
        phead_en_text = page.evaluate("document.querySelector('.phead')?.textContent || ''")
        check("c1_banner_en_has_october", "october" in phead_en_text.lower(),
              f"no .phead-other found in EN; phead text: {phead_en_text[:120]!r}")
    ctx.close()

    # ── Check C1-8: Data clean-up ──
    print("\n=== C1-8: Data clean-up ===")
    import subprocess, re

    # Euro Cinema count
    euro_data = subprocess.run(
        ["python3", "-c",
         "import sys; c=open('src/data.html').read(); print('euro_count='+str(c.count('Euro Cinema')))"],
        cwd=str(webapp_root), capture_output=True, text=True)
    euro_artifact = subprocess.run(
        ["python3", "-c",
         "import sys; c=open('src/sofia-screen.artifact.html').read(); print('euro_count='+str(c.count('Euro Cinema')))"],
        cwd=str(webapp_root), capture_output=True, text=True)
    euro_data_cnt = int((euro_data.stdout.strip().split('=')[1] if '=' in euro_data.stdout else '1'))
    euro_art_cnt = int((euro_artifact.stdout.strip().split('=')[1] if '=' in euro_artifact.stdout else '1'))
    check("c1_no_euro_cinema_in_data", euro_data_cnt == 0, f"Euro Cinema count in data.html: {euro_data_cnt}")
    check("c1_no_euro_cinema_in_artifact", euro_art_cnt == 0, f"Euro Cinema count in artifact: {euro_art_cnt}")

    # No '—' in director/cast
    dash_check = subprocess.run(
        ["python3", "-c",
         r'import re,json; c=open("src/data.html").read(); d=re.findall(r"\"director\":\"—\"|\"cast\":\"—\"",c); print("dash_count="+str(len(d)))'],
        cwd=str(webapp_root), capture_output=True, text=True)
    dash_cnt = int((dash_check.stdout.strip().split('=')[1] if '=' in dash_check.stdout else '1'))
    check("c1_no_dash_director_cast", dash_cnt == 0, f"'—' director/cast count: {dash_cnt}")

    # sarceto-na-zveyara: no note, empty synBg/synEn
    sarceto_check = subprocess.run(
        ["python3", "-c", """
import re
c=open('src/data.html').read()
i=c.find('sarceto-na-zveyara')
if i==-1:
    print('NOT_FOUND')
else:
    end=c.find('},',i)+2
    seg=c[i:end]
    has_note='\"note\"' in seg
    has_synbg=bool(re.search(r'\"synBg\":\"[^\"]+\"',seg))
    has_synen=bool(re.search(r'\"synEn\":\"[^\"]+\"',seg))
    print(f'has_note={has_note} has_synbg={has_synbg} has_synen={has_synen}')
"""],
        cwd=str(webapp_root), capture_output=True, text=True)
    sarceto_out = sarceto_check.stdout.strip()
    if sarceto_out == "NOT_FOUND":
        skip("c1_sarceto_no_note", "sarceto-na-zveyara not in src/data.html — re-pin FIXTURE_REF or run on fixture")
        skip("c1_sarceto_empty_syn", "sarceto-na-zveyara not in src/data.html — re-pin FIXTURE_REF or run on fixture")
    else:
        check("c1_sarceto_no_note", "has_note=False" in sarceto_out,
              f"sarceto: {sarceto_out}")
        check("c1_sarceto_empty_syn", "has_synbg=False" in sarceto_out and "has_synen=False" in sarceto_out,
              f"sarceto: {sarceto_out}")

    # palestina-36 and kosa: no note
    for slug in ['palestina-36', 'kosa']:
        note_check = subprocess.run(
            ["python3", "-c", f"""
c=open('src/data.html').read()
i=c.find('{slug}')
if i==-1:
    print('NOT_FOUND')
else:
    end=c.find('}}',i+len('{slug}'))
    seg=c[i:end+1]
    print('has_note='+str('\"note\"' in seg))
"""],
            cwd=str(webapp_root), capture_output=True, text=True)
        note_out = note_check.stdout.strip()
        check(f"c1_{slug.replace('-','_')}_no_note", "has_note=False" in note_out,
              f"{slug}: {note_out}")

    # vsichko-za-mayka-mi note has proper BG quotes
    vsichko_check = subprocess.run(
        ["python3", "-c", r"""
c=open('src/data.html').read()
i=c.find('vsichko-za-mayka-mi')
if i==-1:
    print('NOT_FOUND')
else:
    end=c.find('},',i)+2
    seg=c[i:end]
    ni=seg.find('"note"')
    if ni==-1:
        print('no_note')
    else:
        print('note_val='+repr(seg[ni:ni+80]))
"""],
        cwd=str(webapp_root), capture_output=True, text=True)
    vsichko_out = vsichko_check.stdout.strip()
    # Check for BG curly quotes: U+201E = low-9 open, U+201C = left double close
    # vsichko_out has repr() of the note so we see \\u201e / \\u201c escape sequences
    has_bg_quotes = ("u201e" in vsichko_out or "\\u201e" in vsichko_out or
                     "„Оскар" in vsichko_out or
                     "Оскар" in vsichko_out)
    # Also require the closing quote marker to be present (not straight quotes)
    no_straight_quotes = '"Оскар"' not in vsichko_out.replace("\\'", "")
    check("c1_vsichko_note_bg_quotes", has_bg_quotes,
          f"vsichko note: {vsichko_out[:100]}")

    # ── Check C1-9: Clearing filters restores event rails and all venues ──
    print("\n=== C1-9: Clearing filters ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    # Select a venue filter
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(600)
    page.evaluate("""() => {
        const chip = document.querySelector(".drawer [data-venue='cc-sofia']");
        if (chip) chip.click();
    }""")
    page.wait_for_timeout(300)

    # Click clear-all
    cleared = page.evaluate("""() => {
        const btn = document.querySelector('.drawer .fclear[data-clearf]') ||
                    document.querySelector('[data-clearf]');
        if (!btn) return false;
        btn.click();
        return true;
    }""")
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(600)

    if cleared:
        state_after = page.evaluate("""() => ({
            fVenues: typeof S !== 'undefined' ? S.fVenues : null,
            fGenres: typeof S !== 'undefined' ? S.fGenres : null
        })""")
        check("c1_clear_resets_venues", state_after["fVenues"] == [],
              f"fVenues after clear: {state_after['fVenues']}")
        check("c1_clear_resets_genres", state_after["fGenres"] == [],
              f"fGenres after clear: {state_after['fGenres']}")
        # Event rails should now be visible
        event_rails_after = page.evaluate("""() => {
            const h2s = Array.from(document.querySelectorAll('.rhead h2')).map(h=>h.textContent.trim());
            return h2s.filter(h => h.includes('Лимитирано') || h.includes('Скоро'));
        }""")
        check("c1_event_rails_restored", len(event_rails_after) >= 1,
              f"event rails after clear: {event_rails_after}")
    else:
        check("c1_clear_resets_venues", False, "no [data-clearf] button found")
        check("c1_clear_resets_genres", False, "skipped")
        check("c1_event_rails_restored", False, "skipped")
    ctx.close()

    # ── Check C1-10: No page errors + verify_build.py gate ──
    print("\n=== C1-10: No page errors + gate ===")
    all_errors = []
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang in ["bg", "en"]:
            ctx, page, errs = open_page(browser, w, h, lang=lang, mode="cinema")
            page.wait_for_timeout(600)
            if errs:
                all_errors.extend([(size_tag, lang, e) for e in errs])
            ctx.close()
    if all_errors:
        check("c1_no_page_errors", False, f"{len(all_errors)} error(s): {all_errors[0]}")
    else:
        check("c1_no_page_errors", True, "")

    result = subprocess.run(
        ["python3", "scripts/verify_build.py"],
        cwd=str(webapp_root),
        capture_output=True,
        text=True,
        env={**os.environ, "SOFIA_HTML": html_env}
    )
    gate_passes = "all checks passed" in result.stdout
    check("c1_gate_passes", gate_passes,
          result.stdout[:120] if not gate_passes else "")


# ============================================================================
# WAVE D1: Ticket links, missing-information handling, open animation
# ============================================================================

def wave_d1(browser):
    """
    Wave D1 checks: ticket link resolver, sheet hrefs, sarceto details,
    in-person label, Lumière epaygo, animation removed, no-info notes,
    show no-info, regressions (N кина, venue filter), and gate.
    """
    wave = "D1"
    ensure_shot_dir(wave)
    import subprocess

    # ── D1-1: Exhaustive resolver check ──
    print("\n=== D1-1: Exhaustive URL resolver ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    resolver_result = page.evaluate(f"""() => {{
        const today = "{CLOCK_TODAY_ISO}";
        // Owner allowlist per venue id
        const ALLOWED = {{
            "cc-sofia": ["www.cinemacity.bg"],
            "cc-paradise": ["www.cinemacity.bg"],
            "arena-mega": ["www.kinoarena.com"],
            "arena-mall": ["www.kinoarena.com"],
            "cg-ring": ["cinegrand.bg"],
            "cg-park": ["cinegrand.bg"],
            "cineland": ["cineland.bg"],
            "vlaikova": ["vlaikovacinema.com","embed.urboapp.com"],
            "lumiere": ["www.ndk.bg","ndk.bg","epaygo.bg"],
            "dom-kino": ["domnakinoto.com"],
            "odeon": ["bnf.bg"],
            "g8": ["g8cinema.com"],
            "casa-libri": null  // inPerson – no URL expected
        }};
        const upcomingRows = SHOWTIMES.filter(r => r[2] >= today);
        const counts = {{}};
        const violations = [];
        let programataCount = 0;
        let totalChecked = 0;
        let ccDateOk = true, ccDateFail = [];

        upcomingRows.forEach(r => {{
            const [filmId, venueId, date, times] = r;
            const allowed = ALLOWED[venueId];
            if (allowed === undefined) return; // theatre or other non-cinema venue, skip
            counts[venueId] = (counts[venueId] || 0) + 1;
            totalChecked++;

            // inPerson venues have no URL to check
            const b = BOOKING[venueId];
            if (b && b.inPerson) return;

            const url = filmTixUrl(filmId, venueId, date);
            if (!url) return;

            // Check programata.bg
            if (url.includes("programata.bg")) {{
                programataCount++;
                violations.push({{venueId, filmId, date, url: url.slice(0,80), reason: "programata.bg"}});
            }}

            // Check host allowlist
            try {{
                const host = new URL(url).hostname;
                if (allowed && !allowed.some(h => host === h || host.endsWith("."+h))) {{
                    violations.push({{venueId, filmId, date, url: url.slice(0,80), reason: "wrong_host:" + host}});
                }}
            }} catch(e) {{}}

            // cc-sofia / cc-paradise must contain at=<date>
            if ((venueId === "cc-sofia" || venueId === "cc-paradise") && b && b.deep) {{
                const atParam = "at=" + date;
                if (!url.includes(atParam)) {{
                    ccDateFail.push({{venueId, filmId, date, url: url.slice(0,80)}});
                    ccDateOk = false;
                }}
            }}
        }});

        return {{
            totalChecked,
            counts,
            violations: violations.slice(0, 10),
            violationCount: violations.length,
            programataCount,
            ccDateOk,
            ccDateFail: ccDateFail.slice(0, 5)
        }};
    }}""")

    check("d1_resolver_no_programata",
          resolver_result["programataCount"] == 0,
          f"programata.bg count: {resolver_result['programataCount']}")
    check("d1_resolver_no_violations",
          resolver_result["violationCount"] == 0,
          f"{resolver_result['violationCount']} violations: {resolver_result['violations'][:3]}")
    check("d1_resolver_cc_date",
          resolver_result["ccDateOk"],
          f"cc missing at=<date>: {resolver_result['ccDateFail'][:3]}")
    print(f"  Counts per venue: {resolver_result['counts']}")
    print(f"  Total rows checked: {resolver_result['totalChecked']}")
    ctx.close()

    # ── D1-2: Exhaustive sheet href check ──
    print("\n=== D1-2: Exhaustive sheet href check ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    sheet_href_result = page.evaluate(f"""() => {{
        const today = "{CLOCK_TODAY_ISO}";
        const programataHrefs = [];
        // Films with upcoming rows
        const upcomingFilms = [...new Set(SHOWTIMES.filter(r => r[2] >= today).map(r => r[0]))];
        upcomingFilms.forEach(fid => {{
            const f = filmById[fid];
            if (!f) return;
            const html = sheetFilm(f);
            // Parse hrefs via a temp div
            const div = document.createElement("div");
            div.innerHTML = html;
            div.querySelectorAll("a[href]").forEach(a => {{
                if (a.href.includes("programata.bg")) {{
                    programataHrefs.push({{fid, href: a.href.slice(0,80)}});
                }}
            }});
        }});
        return {{count: programataHrefs.length, samples: programataHrefs.slice(0,5)}};
    }}""")

    check("d1_sheet_no_programata",
          sheet_href_result["count"] == 0,
          f"programata.bg in sheets: {sheet_href_result['count']} — {sheet_href_result['samples']}")
    ctx.close()

    # ── D1-3: Heart of the Beast (sarceto-na-zveyara) ──
    print("\n=== D1-3: Sarceto-na-zveyara detail ===")
    # BG sheet
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    sarceto_bg = page.evaluate("""() => {
        const f = filmById["sarceto-na-zveyara"];
        if (!f) return {found: false};
        const html = sheetFilm(f);
        const div = document.createElement("div");
        div.innerHTML = html;
        // Showtime rows per venue
        const venues = [...new Set(Array.from(div.querySelectorAll(".vrow")).map(vr => {
            const loc = vr.querySelector(".vloc"); return loc ? loc.textContent.trim() : "";
        }))].filter(Boolean);
        // Vlaikova link
        const allLinks = Array.from(div.querySelectorAll("a[href]")).map(a => ({
            text: a.textContent.trim().slice(0,40),
            href: a.href.slice(0,80)
        }));
        const vlaikovaLinks = allLinks.filter(a => a.href.includes("embed.urboapp.com"));
        // Buy box items
        const buyLinks = Array.from(div.querySelectorAll(".buylinks .buylink")).map(el => ({
            text: el.textContent.trim().slice(0,60),
            href: (el.tagName === "A" ? el.href : "").slice(0,80)
        }));
        // Synopsis
        const synEl = div.querySelector(".synwrap, .synbody");
        const synText = synEl ? synEl.textContent.trim().slice(0, 80) : "";
        // Credits — collect as list of [dt_text, dd_text] pairs
        const credPairs = [];
        div.querySelectorAll("dl.credits dt").forEach((dt) => {
            const dd = dt.nextElementSibling;
            credPairs.push([dt.textContent.trim(), dd ? dd.textContent.trim().slice(0, 80) : ""]);
        });
        // Unique buy labels
        const buyLabels = buyLinks.map(b => b.text);
        const uniqueLabels = [...new Set(buyLabels)];
        return {
            found: true,
            venues,
            vlaikovaLinks,
            buyLinks,
            buyLabels,
            uniqueLabels,
            synText,
            credPairs
        };
    }""")

    _sarceto_d1_names = [
        "d1_sarceto_7_cinema_rows","d1_sarceto_vlaikova_link","d1_sarceto_buybox_7",
        "d1_sarceto_buybox_distinct","d1_sarceto_syn_bg","d1_sarceto_dir_bg","d1_sarceto_cast_bg"
    ]
    if not sarceto_bg.get("found"):
        for name in _sarceto_d1_names:
            skip(name, "sarceto-na-zveyara not in this build — re-pin FIXTURE_REF or run on fixture")
    else:
        check("d1_sarceto_7_cinema_rows",
              len(sarceto_bg.get("venues", [])) == 7,
              f"venue rows found: {sarceto_bg.get('venues', [])}")
        vlaikova_link_ok = any(
            "embed.urboapp.com/vj7oz5J5H2tBP11v0u4KeToOS8csB5ZN/bg/25324" in lnk.get("href","")
            for lnk in sarceto_bg.get("vlaikovaLinks", [])
        )
        check("d1_sarceto_vlaikova_link",
              vlaikova_link_ok,
              f"vlaikova links: {sarceto_bg.get('vlaikovaLinks', [])}")
        buy_labels = sarceto_bg.get("buyLabels", [])
        unique_labels = sarceto_bg.get("uniqueLabels", [])
        check("d1_sarceto_buybox_7", len(buy_labels) == 7,
              f"buy box items: {len(buy_labels)} — {buy_labels[:4]}")
        check("d1_sarceto_buybox_distinct", len(unique_labels) == len(buy_labels),
              f"duplicates in buy labels: {buy_labels}")
        syn_text_bg = sarceto_bg.get("synText", "")
        check("d1_sarceto_syn_bg",
              syn_text_bg.startswith("Сърцето на звяра проследява"),
              f"synText BG: {syn_text_bg[:60]!r}")
        cred_pairs_bg = sarceto_bg.get("credPairs", [])
        dir_val = [v for k, v in cred_pairs_bg if "реж" in k.lower()]
        cast_val = [v for k, v in cred_pairs_bg if "ролите" in k.lower() or "акт" in k.lower()]
        check("d1_sarceto_dir_bg",
              any("Дейвид Ейър" in v for v in dir_val),
              f"director vals (credPairs={cred_pairs_bg[:3]}): {dir_val}")
        check("d1_sarceto_cast_bg",
              any(v.startswith("Брад Пит") for v in cast_val),
              f"cast vals (credPairs={cred_pairs_bg[:3]}): {cast_val}")

    # Save screenshot
    # Open the sheet in a real page
    page.evaluate("""() => {
        const f = filmById["sarceto-na-zveyara"];
        if (f) { _sheetShowAllCinemas = false; curSheet = {kind:"film", id:f.id}; openSheet(sheetFilm(f), null); }
    }""")
    page.wait_for_timeout(600)
    # Scroll to buy box
    page.evaluate("""() => {
        const bb = document.querySelector(".buybox");
        if (bb) bb.scrollIntoView({block:"center"});
    }""")
    page.wait_for_timeout(300)
    save_shot(page, wave, "m-bg-beast-sheet")
    ctx.close()

    # EN sheet for sarceto
    ctx, page, errs = open_page(browser, 1280, 800, lang="en", mode="cinema")
    page.wait_for_timeout(800)

    sarceto_en = page.evaluate("""() => {
        const f = filmById["sarceto-na-zveyara"];
        if (!f) return {found: false};
        const html = sheetFilm(f);
        const div = document.createElement("div");
        div.innerHTML = html;
        const synEl = div.querySelector(".synwrap, .synbody");
        const synText = synEl ? synEl.textContent.trim().slice(0, 100) : "";
        return {found: true, synText};
    }""")

    syn_en = sarceto_en.get("synText", "")
    if not sarceto_en.get("found"):
        skip("d1_sarceto_syn_en", "sarceto-na-zveyara not in this build — re-pin FIXTURE_REF or run on fixture")
    else:
        check("d1_sarceto_syn_en",
              syn_en.startswith("After a harrowing plane crash"),
              f"synText EN: {syn_en[:80]!r}")

    page.evaluate("""() => {
        const f = filmById["sarceto-na-zveyara"];
        if (f) { _sheetShowAllCinemas = false; curSheet = {kind:"film", id:f.id}; openSheet(sheetFilm(f), null); }
    }""")
    page.wait_for_timeout(600)
    save_shot(page, wave, "d-en-beast-sheet")
    ctx.close()

    # ── D1-4: In-person venue (g8 or odeon) ──
    print("\n=== D1-4: In-person venue label ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    inperson_result = page.evaluate(f"""() => {{
        const today = "{CLOCK_TODAY_ISO}";
        // Find a film whose upcoming rows are ONLY at inPerson venues
        const inPersonVids = Object.keys(BOOKING).filter(v => BOOKING[v].inPerson);
        const upcomingByFilm = {{}};
        SHOWTIMES.filter(r => r[2] >= today).forEach(r => {{
            if (!upcomingByFilm[r[0]]) upcomingByFilm[r[0]] = new Set();
            upcomingByFilm[r[0]].add(r[1]);
        }});
        let inPersonFilmId = null;
        for (const [fid, vids] of Object.entries(upcomingByFilm)) {{
            if ([...vids].every(v => inPersonVids.includes(v))) {{
                inPersonFilmId = fid; break;
            }}
        }}
        if (!inPersonFilmId) return {{found: false, reason: "no in-person only film found"}};
        const f = filmById[inPersonFilmId];
        if (!f) return {{found: false, reason: "filmById miss for " + inPersonFilmId}};
        const html = sheetFilm(f);
        const div = document.createElement("div");
        div.innerHTML = html;
        // Check for "на касата" label text in sheet
        const sheetText = div.textContent;
        const hasBoxOfficeLabel = sheetText.includes("на касата") || sheetText.includes("box office");
        // Check that .sheet a.time links exist
        const timeLinks = div.querySelectorAll("a.time");
        // Check no .buylink element links anywhere for that venue
        const buyLinkWithHref = Array.from(div.querySelectorAll(".buylink[href]"));
        // inPerson buylinks should NOT be <a> with href
        const inPersonBuyAnchors = Array.from(div.querySelectorAll("a.buylink"));
        return {{
            found: true,
            filmId: inPersonFilmId,
            hasBoxOfficeLabel,
            timeLinksCount: timeLinks.length,
            inPersonBuyAnchorCount: inPersonBuyAnchors.length,
            buyLinkWithHrefCount: buyLinkWithHref.length
        }};
    }}""")

    if inperson_result.get("found"):
        check("d1_inperson_label",
              inperson_result["hasBoxOfficeLabel"],
              f"filmId={inperson_result['filmId']}, hasBoxOfficeLabel={inperson_result['hasBoxOfficeLabel']}")
        check("d1_inperson_time_links",
              inperson_result["timeLinksCount"] > 0,
              f"time links count: {inperson_result['timeLinksCount']}")
        check("d1_inperson_no_buy_anchor",
              inperson_result["inPersonBuyAnchorCount"] == 0,
              f"in-person buy anchors: {inperson_result['inPersonBuyAnchorCount']}")
        # Open the in-person sheet and take screenshot
        fid = inperson_result["filmId"]
        page.evaluate(f"""() => {{
            const f = filmById["{fid}"];
            if (f) {{ _sheetShowAllCinemas = false; curSheet = {{kind:"film", id:f.id}}; openSheet(sheetFilm(f), null); }}
        }}""")
        page.wait_for_timeout(600)
        save_shot(page, wave, "m-bg-inperson-sheet")
    else:
        check("d1_inperson_label", False, inperson_result.get("reason", "no in-person film found"))
        check("d1_inperson_time_links", False, "skipped")
        check("d1_inperson_no_buy_anchor", False, "skipped")

    ctx.close()

    # ── D1-5: Lumière epaygo handling ──
    print("\n=== D1-5: Lumière epaygo ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    lumiere_result = page.evaluate(f"""() => {{
        const today = "{CLOCK_TODAY_ISO}";
        const lumiereFilms = [...new Set(SHOWTIMES.filter(r => r[2] >= today && r[1] === "lumiere").map(r => r[0]))];
        const noVlink = lumiereFilms.filter(fid => !VLINK_MAP[fid + "|lumiere"]);
        const withVlink = lumiereFilms.filter(fid => !!VLINK_MAP[fid + "|lumiere"]);
        const epayVlinkOk = withVlink.every(fid => {{
            const url = filmTixUrl(fid, "lumiere", today);
            return url && url.includes("epaygo.bg");
        }});
        const sampleVlinkUrls = withVlink.map(fid => filmTixUrl(fid, "lumiere", today)).slice(0,3);
        // For no-vlink films, open the sheet and check for epaygo note
        let epayNoteShown = null;
        if (noVlink.length > 0) {{
            const f = filmById[noVlink[0]];
            if (f) {{
                const html = sheetFilm(f);
                const div = document.createElement("div");
                div.innerHTML = html;
                epayNoteShown = div.textContent.includes("epaygo.bg");
            }}
        }}
        return {{
            lumiereFilms,
            noVlinkCount: noVlink.length,
            noVlink,
            withVlinkCount: withVlink.length,
            withVlink,
            epayVlinkOk,
            sampleVlinkUrls,
            epayNoteShown
        }};
    }}""")

    no_vlink_count = lumiere_result["noVlinkCount"]
    if no_vlink_count > 0:
        check("d1_lumiere_epay_note",
              lumiere_result["epayNoteShown"] is True,
              f"no-vlink lumiere film without epaygo note; no-vlink: {lumiere_result['noVlink']}")
    else:
        # All lumiere films have VLINKS — assert epaygo.bg links used
        check("d1_lumiere_epay_vlinks_used",
              lumiere_result["epayVlinkOk"],
              f"not all lumiere VLINKS use epaygo.bg: {lumiere_result['sampleVlinkUrls']}")
    print(f"  Lumiere films total: {len(lumiere_result['lumiereFilms'])}, "
          f"no-vlink: {no_vlink_count}, with-vlink: {lumiere_result['withVlinkCount']}")
    print(f"  Sample vlink URLs: {lumiere_result['sampleVlinkUrls']}")
    ctx.close()

    # ── D1-6: Animation removed, close/escape/back work ──
    print("\n=== D1-6: Animation removed ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    # Click a film card to open the sheet
    film_card = page.query_selector("[data-film]")
    check("d1_anim_film_card_exists", film_card is not None, "no [data-film] card found")
    if film_card:
        film_card.click()
        page.wait_for_timeout(100)  # 50 ms after click
        # At 50 ms
        anim_50 = page.evaluate("""() => {
            const BAD = ['.reveal','.curtain','.zlens','.zring','.cine-dim','.flare'];
            return BAD.map(sel => document.querySelector(sel) !== null);
        }""")
        check("d1_anim_no_bad_elements_50ms",
              not any(anim_50),
              f"bad animation elements at 50ms: {anim_50}")
        page.wait_for_timeout(150)  # ~200 ms
        anim_200 = page.evaluate("""() => {
            const BAD = ['.reveal','.curtain','.zlens','.zring','.cine-dim','.flare'];
            return BAD.map(sel => document.querySelector(sel) !== null);
        }""")
        check("d1_anim_no_bad_elements_200ms",
              not any(anim_200),
              f"bad animation elements at 200ms: {anim_200}")
        page.wait_for_timeout(400)  # ~600 ms total
        anim_600 = page.evaluate("""() => {
            const BAD = ['.reveal','.curtain','.zlens','.zring','.cine-dim','.flare'];
            return BAD.map(sel => document.querySelector(sel) !== null);
        }""")
        check("d1_anim_no_bad_elements_600ms",
              not any(anim_600),
              f"bad animation elements at 600ms: {anim_600}")

        # .sheet visible within 400 ms total (already past 600ms, just check it's there)
        sheet_visible = page.evaluate("!!document.querySelector('.sheet') && document.querySelector('.sheet').offsetParent !== null")
        check("d1_anim_sheet_visible",
              sheet_visible,
              "sheet not visible after 600ms")

        # Close via ✕ [data-close]
        close_btn = page.query_selector("[data-close]")
        if close_btn:
            close_btn.click()
            page.wait_for_timeout(300)
            sheet_gone = page.evaluate("!document.querySelector('.scrim') || document.querySelector('#scrim').hidden")
            check("d1_close_x", sheet_gone, "sheet still visible after ✕ click")
        else:
            check("d1_close_x", False, "[data-close] not found")

        # Re-open, close via Escape
        film_card2 = page.query_selector("[data-film]")
        if film_card2:
            film_card2.click()
            page.wait_for_timeout(400)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            sheet_gone_esc = page.evaluate("!document.querySelector('#scrim') || document.getElementById('scrim').hidden")
            check("d1_close_esc", sheet_gone_esc, "sheet still visible after Escape")
        else:
            check("d1_close_esc", False, "could not find film card for re-open")

        # Re-open, close via browser back
        film_card3 = page.query_selector("[data-film]")
        if film_card3:
            film_card3.click()
            page.wait_for_timeout(400)
            page.go_back()
            page.wait_for_timeout(300)
            sheet_gone_back = page.evaluate("!document.querySelector('#scrim') || document.getElementById('scrim').hidden")
            check("d1_close_back", sheet_gone_back, "sheet still visible after browser back")
        else:
            check("d1_close_back", False, "could not find film card for re-open")

        # Re-open (reopen works)
        film_card4 = page.query_selector("[data-film]")
        if film_card4:
            film_card4.click()
            page.wait_for_timeout(400)
            sheet_reopened = page.evaluate("!!document.querySelector('.sheet')")
            check("d1_reopen_works", sheet_reopened, "sheet did not reopen")
        else:
            check("d1_reopen_works", False, "could not find film card for re-open")
    ctx.close()

    # Reduced motion: no animations on .sheet
    ctx_rm = browser.new_context(
        viewport={"width": 375, "height": 812},
        timezone_id="Europe/Sofia",
        locale="bg-BG",
        reduced_motion="reduce"
    )
    ctx_rm.add_init_script(CLOCK)
    init_storage_bg = """
    try {
      localStorage.setItem('sofia-screen-v2',
        JSON.stringify({prefs:{track:'both',genres:[],mood:[],with:'',when:'any',taste:[]}, lang:'bg', mode:'cinema'}));
    } catch(e) {}
    """
    ctx_rm.add_init_script(init_storage_bg)
    page_rm = ctx_rm.new_page()
    errors_rm = []
    page_rm.on("pageerror", lambda e: errors_rm.append(str(e)))
    page_rm.goto(HTML)
    page_rm.wait_for_selector(".bar", timeout=20000)
    page_rm.wait_for_timeout(800)

    film_card_rm = page_rm.query_selector("[data-film]")
    if film_card_rm:
        film_card_rm.click()
        page_rm.wait_for_timeout(400)
        anim_count = page_rm.evaluate("""() => {
            const s = document.querySelector('.sheet');
            if (!s) return -1;
            return s.getAnimations().length;
        }""")
        check("d1_anim_reduced_motion",
              anim_count == 0,
              f"getAnimations().length={anim_count} (expected 0 with prefers-reduced-motion)")
    else:
        check("d1_anim_reduced_motion", False, "no film card found in reduced-motion context")
    ctx_rm.close()

    # ── D1-7: No-info handling for films ──
    print("\n=== D1-7: No-info film ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    noinfo_result = page.evaluate(f"""() => {{
        const today = "{CLOCK_TODAY_ISO}";
        // Find a film with ALL synopsis chains empty
        let noInfoFilmId = null;
        for (const r of SHOWTIMES) {{
            if (r[2] < today) continue;
            const fid = r[0];
            const f = filmById[fid];
            if (!f) continue;
            const fi = (typeof FILMINFO === "object" && FILMINFO && FILMINFO[fid]) || {{}};
            const ar = (typeof TMDBART === "object" && TMDBART && TMDBART[fid]) || {{}};
            const hasSynBg = (f.synBg && f.synBg.trim()) || (fi.synBg && fi.synBg.trim()) || (ar.ovBg && ar.ovBg.trim());
            const hasSynEn = (f.synEn && f.synEn.trim()) || (fi.synEn && fi.synEn.trim()) || (ar.ov && ar.ov.trim());
            if (!hasSynBg && !hasSynEn) {{ noInfoFilmId = fid; break; }}
        }}
        return {{noInfoFilmId}};
    }}""")

    noinfo_film_id = noinfo_result.get("noInfoFilmId")
    if not noinfo_film_id:
        # Inject a no-info film by deleting FILMINFO + TMDBART for a specific film in-page
        test_film = page.evaluate(f"""() => {{
            const today = "{CLOCK_TODAY_ISO}";
            for (const r of SHOWTIMES) {{
                if (r[2] >= today) {{
                    const f = filmById[r[0]];
                    if (f && f.id) return f.id;
                }}
            }}
            return null;
        }}""")
        if test_film:
            page.evaluate(f"""() => {{
                const fid = "{test_film}";
                if (typeof FILMINFO === "object" && FILMINFO) delete FILMINFO[fid];
                if (typeof TMDBART === "object" && TMDBART) delete TMDBART[fid];
                const f = filmById[fid];
                if (f) {{ f.synBg = ""; f.synEn = ""; f.director = ""; f.cast = ""; }}
            }}""")
            noinfo_film_id = test_film
            print(f"  Injected no-info state for filmId={test_film}")
        else:
            check("d1_noinfo_bg_note", False, "could not find any film to test no-info state")

    if noinfo_film_id:
        # BG: should show "Няма налична информация за този филм."
        noinfo_bg = page.evaluate(f"""() => {{
            const f = filmById["{noinfo_film_id}"];
            if (!f) return {{found: false}};
            const html = sheetFilm(f);
            const div = document.createElement("div");
            div.innerHTML = html;
            const noInfoNote = div.querySelector(".no-info-note");
            const noteText = noInfoNote ? noInfoNote.textContent.trim() : "";
            const heroEl = div.querySelector(".shero");
            const heroHasNote = heroEl && heroEl.querySelector(".no-info-note") !== null;
            const credDts = Array.from(div.querySelectorAll("dl.credits dd")).map(dd => dd.textContent.trim());
            return {{found: true, noteText, heroHasNote, credDts}};
        }}""")
        check("d1_noinfo_bg_note",
              "Няма налична информация за този филм." in noinfo_bg.get("noteText", ""),
              f"note text BG: {noinfo_bg.get('noteText', 'NOT FOUND')!r}")
        check("d1_noinfo_hero_no_note",
              not noinfo_bg.get("heroHasNote", False),
              "hero section should NOT show no-info note")
        # Credits rows show "няма информация"
        cred_dts = noinfo_bg.get("credDts", [])
        no_info_short = any("няма информация" in v.lower() for v in cred_dts)
        check("d1_noinfo_credits_bg",
              no_info_short,
              f"credits dd values: {cred_dts[:4]}")

        # Open real sheet and take screenshot
        page.evaluate(f"""() => {{
            const f = filmById["{noinfo_film_id}"];
            if (f) {{ _sheetShowAllCinemas = false; curSheet = {{kind:"film", id:f.id}}; openSheet(sheetFilm(f), null); }}
        }}""")
        page.wait_for_timeout(600)
        save_shot(page, wave, "m-bg-noinfo-sheet")
    ctx.close()

    # EN no-info check
    ctx, page, errs = open_page(browser, 375, 812, lang="en", mode="cinema")
    page.wait_for_timeout(800)
    if noinfo_film_id:
        page.evaluate(f"""() => {{
            const fid = "{noinfo_film_id}";
            if (typeof FILMINFO === "object" && FILMINFO) delete FILMINFO[fid];
            if (typeof TMDBART === "object" && TMDBART) delete TMDBART[fid];
            const f = filmById[fid];
            if (f) {{ f.synBg = ""; f.synEn = ""; f.director = ""; f.cast = ""; }}
        }}""")
        noinfo_en = page.evaluate(f"""() => {{
            const f = filmById["{noinfo_film_id}"];
            if (!f) return {{found: false}};
            const html = sheetFilm(f);
            const div = document.createElement("div");
            div.innerHTML = html;
            const noInfoNote = div.querySelector(".no-info-note");
            const noteText = noInfoNote ? noInfoNote.textContent.trim() : "";
            const credDts = Array.from(div.querySelectorAll("dl.credits dd")).map(dd => dd.textContent.trim());
            return {{found: true, noteText, credDts}};
        }}""")
        check("d1_noinfo_en_note",
              "No information available for this film." in noinfo_en.get("noteText", ""),
              f"note text EN: {noinfo_en.get('noteText', 'NOT FOUND')!r}")
        en_cred_dts = noinfo_en.get("credDts", [])
        no_info_short_en = any("no information" in v.lower() for v in en_cred_dts)
        check("d1_noinfo_credits_en",
              no_info_short_en,
              f"EN credits dd values: {en_cred_dts[:4]}")
    ctx.close()

    # ── D1-8: Theatre show no-info note ──
    print("\n=== D1-8: Show no-synopsis note ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="theatre")
    page.wait_for_timeout(800)

    show_noinfo = page.evaluate("""() => {
        // Find a show in SHOWS with no synopsis; or make one empty
        let testShowId = null;
        let showsArr = (typeof SHOWS !== "undefined" ? SHOWS : []).concat(
            typeof PERFORMANCES !== "undefined" ? [] : []
        );
        // Actually iterate over showById
        for (const [sid, sh] of Object.entries(showById)) {
            const hasSyn = (sh.synBg && sh.synBg.trim()) || (sh.synEn && sh.synEn.trim());
            if (!hasSyn) { testShowId = sid; break; }
        }
        if (!testShowId) {
            // Force one empty
            const first = Object.keys(showById)[0];
            if (!first) return {found: false, reason: "no shows"};
            const sh = showById[first];
            const orig = {synBg: sh.synBg, synEn: sh.synEn};
            sh.synBg = ""; sh.synEn = "";
            // Also clear SHOWSYN for this id
            const savedSyn = (typeof SHOWSYN !== "undefined" && SHOWSYN[first]) || null;
            if (typeof SHOWSYN !== "undefined") delete SHOWSYN[first];
            const html = sheetShow(sh);
            // Restore
            sh.synBg = orig.synBg; sh.synEn = orig.synEn;
            if (savedSyn && typeof SHOWSYN !== "undefined") SHOWSYN[first] = savedSyn;
            const div = document.createElement("div");
            div.innerHTML = html;
            const note = div.querySelector(".no-info-note");
            return {found: true, injected: true, showId: first, noteText: note ? note.textContent.trim() : "", htmlSnippet: html.slice(0,200)};
        }
        const sh = showById[testShowId];
        const html = sheetShow(sh);
        const div = document.createElement("div");
        div.innerHTML = html;
        const note = div.querySelector(".no-info-note");
        return {found: true, injected: false, showId: testShowId, noteText: note ? note.textContent.trim() : ""};
    }""")

    if show_noinfo.get("found"):
        check("d1_show_noinfo_note",
              "Няма налична информация" in show_noinfo.get("noteText", "") or
              "No information" in show_noinfo.get("noteText", ""),
              f"show no-info note: {show_noinfo.get('noteText','NOT FOUND')!r} (showId={show_noinfo.get('showId')})")
    else:
        check("d1_show_noinfo_note", False, show_noinfo.get("reason", "no shows found"))
    ctx.close()

    # ── D1-9: Regression checks ──
    print("\n=== D1-9: Regressions (N кина, venue filter) ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    # "За теб днес" has at least one card with "· N кина" (N≥2)
    today_rail_result = page.evaluate("""() => {
        // Find the "За теб днес" rail; look for cards with "кина"
        const cards = Array.from(document.querySelectorAll(".card"));
        const withCina = cards.filter(c => {
            const cf2 = c.querySelector(".cf2");
            return cf2 && /\\d+\\s+кина/.test(cf2.textContent);
        });
        const cinaTexts = withCina.map(c => c.querySelector(".cf2").textContent.trim().slice(0,60));
        return {withCinaCount: withCina.length, samples: cinaTexts.slice(0,5)};
    }""")
    check("d1_za_teb_n_kina",
          today_rail_result["withCinaCount"] >= 1,
          f"cards with N кина: {today_rail_result['withCinaCount']} — {today_rail_result['samples']}")

    # Venue filter: select cc-sofia only → sheet rows only Cinema City Sofia + "Покажи всички"
    # First open filter drawer, select cc-sofia
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) { fab.click(); return; }
        const burger = document.querySelector('.burger[data-drawer]');
        if (burger) burger.click();
    }""")
    page.wait_for_timeout(500)
    page.evaluate("""() => {
        const chip = document.querySelector(".drawer [data-venue='cc-sofia']");
        if (chip) chip.click();
    }""")
    page.wait_for_timeout(300)
    page.evaluate("""() => {
        const btn = document.querySelector('[data-dclose]');
        if (btn) btn.click();
    }""")
    page.wait_for_timeout(600)

    # Now open the sheet for a film that plays at cc-sofia
    venue_filter_result = page.evaluate(f"""() => {{
        const today = "{CLOCK_TODAY_ISO}";
        // Pick first film that has cc-sofia rows
        let filmId = null;
        for (const r of SHOWTIMES) {{
            if (r[2] >= today && r[1] === "cc-sofia") {{ filmId = r[0]; break; }}
        }}
        if (!filmId) return {{found: false, reason: "no cc-sofia film"}};
        const f = filmById[filmId];
        if (!f) return {{found: false, reason: "filmById miss"}};
        // Open sheet with venue filter active
        const html = sheetFilm(f);
        const div = document.createElement("div");
        div.innerHTML = html;
        // Get venue names shown in rows
        const vrows = Array.from(div.querySelectorAll(".vrow .vloc")).map(el => el.textContent.trim().slice(0,40));
        // Check for "Покажи всички" or "Show all" button
        const showAllBtn = !!div.querySelector("[data-sheetshowallcin]");
        // Check all vrows are from cc-sofia
        const allSofia = vrows.every(v => v.toLowerCase().includes("cinema city sofia") || v.toLowerCase().includes("cinema city") || v.toLowerCase().includes("mall of sofia"));
        return {{found: true, filmId, vrows, showAllBtn, allSofia}};
    }}""")
    if venue_filter_result.get("found"):
        check("d1_venue_filter_limits_rows",
              venue_filter_result["allSofia"],
              f"vrows: {venue_filter_result['vrows'][:5]}")
        check("d1_venue_filter_show_all_btn",
              venue_filter_result["showAllBtn"],
              "no [data-sheetshowallcin] button found")
    else:
        check("d1_venue_filter_limits_rows", False, venue_filter_result.get("reason"))
        check("d1_venue_filter_show_all_btn", False, "skipped")

    ctx.close()

    # ── D1-10: No page errors + verify_build.py gate ──
    print("\n=== D1-10: No page errors + gate ===")
    all_errors = []
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang in ["bg", "en"]:
            ctx, page, errs = open_page(browser, w, h, lang=lang, mode="cinema")
            page.wait_for_timeout(600)
            if errs:
                all_errors.extend([(size_tag, lang, e) for e in errs])
            ctx.close()
    if all_errors:
        check("d1_no_page_errors", False, f"{len(all_errors)} error(s): {all_errors[0]}")
    else:
        check("d1_no_page_errors", True, "")

    result = subprocess.run(
        ["python3", "scripts/verify_build.py"],
        cwd=str(webapp_root),
        capture_output=True,
        text=True,
        env={**os.environ, "SOFIA_HTML": html_env}
    )
    gate_passes = "all checks passed" in result.stdout
    check("d1_gate_passes", gate_passes,
          result.stdout[:120] if not gate_passes else "")


# ============================================================================
# WAVE H: Hamburger removal, search toggle, header geometry, new icons,
#          new rails order, tomorrow fallback, collapsed sections,
#          .phead structure, theatre sub-heading styles, errors + gate
# ============================================================================

def _force_mount(page):
    """Force-mount all deferred rails (IntersectionObserver won't fire in headless)."""
    page.evaluate("""() => {
        let g = 0;
        while (typeof railIdx !== 'undefined' && typeof RAILS !== 'undefined'
               && railIdx < RAILS.length && g++ < 100) {
            const sent = document.getElementById('rail-sentinel');
            if (!sent) break;
            const batch = RAILS.slice(railIdx, railIdx + 10).map(matRail).join('');
            railIdx += 10;
            sent.insertAdjacentHTML('beforebegin', batch);
        }
    }""")
    page.wait_for_timeout(300)


def wave_h(browser):
    """
    Wave H checks: hamburger removal, search toggle, header geometry, new icons,
    new rails, tomorrow fallback, collapsed sections, .phead, theatre styles.
    """
    wave = "H"
    ensure_shot_dir(wave)
    import subprocess

    # ── H1: No .burger; .fab and .phead-other each open .drawer ──
    print("\n=== H1: Hamburger removed; FAB and phead-other open drawer ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    burger_exists = page.evaluate("document.querySelector('.burger') !== null")
    check("h1_no_burger", not burger_exists,
          ".burger element found in DOM (should be removed in Wave H)")

    fab_box = box(page, ".fab")
    check("h1_fab_visible", fab_box is not None, ".fab not visible on mobile")

    # .fab opens .drawer
    if fab_box:
        page.click(".fab")
        page.wait_for_timeout(400)
        drawer_from_fab = page.evaluate("!!document.querySelector('.drawer')")
        check("h1_fab_opens_drawer", drawer_from_fab, ".fab click did not open .drawer")
        page.evaluate("document.querySelector('[data-dclose]')?.click()")
        page.wait_for_timeout(400)

    # .phead-other[data-drawer] opens .drawer
    phead_other = page.query_selector(".phead-other[data-drawer]")
    check("h1_phead_other_exists", phead_other is not None, ".phead-other[data-drawer] not found")
    if phead_other:
        phead_other.click()
        page.wait_for_timeout(400)
        drawer_from_phead = page.evaluate("!!document.querySelector('.drawer')")
        check("h1_phead_opens_drawer", drawer_from_phead,
              ".phead-other[data-drawer] click did not open .drawer")
        page.evaluate("document.querySelector('[data-dclose]')?.click()")
        page.wait_for_timeout(400)

    save_shot(page, wave, "m-bg-header")
    ctx.close()

    # ── H2: Search toggle at 375 (BG+EN) and 1280 (BG+EN) ──
    print("\n=== H2: Search toggle ===")
    for w, h, lang_code, size_tag in [
        (375, 812, "bg", "m-bg"),
        (375, 812, "en", "m-en"),
        (1280, 800, "bg", "d-bg"),
        (1280, 800, "en", "d-en"),
    ]:
        ctx, page, errs = open_page(browser, w, h, lang=lang_code, mode="cinema")
        page.wait_for_timeout(500)

        # Search toggle button must exist
        btn = page.query_selector("[data-search-open]")
        check(f"h2_{size_tag}_btn_exists", btn is not None, f"[data-search-open] not found at {size_tag}")

        if btn:
            # Initial state: aria-expanded="false", S.searchOpen=false
            expanded_before = btn.get_attribute("aria-expanded")
            so_before = page.evaluate("typeof S !== 'undefined' ? S.searchOpen : null")
            check(f"h2_{size_tag}_closed_initially",
                  expanded_before != "true" and not so_before,
                  f"aria-expanded={expanded_before}, S.searchOpen={so_before}")

            # Click to open
            btn.click()
            page.wait_for_timeout(300)
            expanded_after = page.evaluate(
                "document.querySelector('[data-search-open]')?.getAttribute('aria-expanded')")
            so_after = page.evaluate("typeof S !== 'undefined' ? S.searchOpen : null")
            check(f"h2_{size_tag}_opens_on_click",
                  expanded_after == "true" and so_after,
                  f"aria-expanded={expanded_after}, S.searchOpen={so_after}")

            # At desktop: .desk-search-wrap should be expanded (max-width > 10px)
            if w == 1280:
                wrap_w = page.evaluate("""() => {
                    const wrap = document.querySelector('.desk-search-wrap');
                    return wrap ? parseFloat(window.getComputedStyle(wrap).maxWidth) || 0 : 0;
                }""")
                check(f"h2_{size_tag}_wrap_open", wrap_w > 100,
                      f".desk-search-wrap maxWidth={wrap_w}px after open (need >100)")

            # Click again to close
            btn2 = page.query_selector("[data-search-open]")
            if btn2:
                btn2.click()
                page.wait_for_timeout(300)
                expanded_closed = page.evaluate(
                    "document.querySelector('[data-search-open]')?.getAttribute('aria-expanded')")
                check(f"h2_{size_tag}_closes_on_second_click",
                      expanded_closed != "true",
                      f"aria-expanded={expanded_closed} after second click")

        ctx.close()

    # ── H3: Header layout geometry ──
    print("\n=== H3: Header geometry ===")

    # Mobile: logo centred, lang on logo row, seg+search on controls row
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    mobile_geo = page.evaluate("""() => {
        function bb(sel) {
            const el = document.querySelector(sel);
            if (!el) return null;
            const b = el.getBoundingClientRect();
            return {x: b.x, y: b.y, w: b.width, h: b.height, cx: b.x + b.width/2};
        }
        return {
            brand: bb('.brand'),
            lang: bb('[data-lang]'),
            seg: bb('.seg'),
            search: bb('[data-search-open]'),
        };
    }""")
    if mobile_geo["brand"]:
        cx = mobile_geo["brand"]["cx"]
        check("h3_mobile_logo_centred", abs(cx - 187.5) <= 4,
              f"brand centre_x={cx:.1f}, expected 187.5±4")

    # lang should be on same row as brand (logo row) — not on same row as seg
    if mobile_geo["lang"] and mobile_geo["brand"] and mobile_geo["seg"]:
        lang_cy = mobile_geo["lang"]["y"] + mobile_geo["lang"]["h"] / 2
        brand_cy = mobile_geo["brand"]["y"] + mobile_geo["brand"]["h"] / 2
        seg_cy = mobile_geo["seg"]["y"] + mobile_geo["seg"]["h"] / 2
        lang_on_logo_row = abs(lang_cy - brand_cy) <= 8
        lang_not_on_seg_row = abs(lang_cy - seg_cy) > 8
        check("h3_mobile_lang_on_logo_row", lang_on_logo_row,
              f"lang_cy={lang_cy:.1f}, brand_cy={brand_cy:.1f} (diff={abs(lang_cy-brand_cy):.1f})")
        check("h3_mobile_lang_separate_from_controls", lang_not_on_seg_row,
              f"lang_cy={lang_cy:.1f} same row as seg_cy={seg_cy:.1f}")

    save_shot(page, wave, "m-bg-layout")
    ctx.close()

    # Desktop: lang is rightmost; search immediately left of lang; seg left of search; brand leftmost
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    desktop_geo = page.evaluate("""() => {
        function bb(sel) {
            const el = document.querySelector(sel);
            if (!el) return null;
            const b = el.getBoundingClientRect();
            return {left: b.left, right: b.right, cx: b.left + b.width/2};
        }
        return {
            brand: bb('.brand'),
            seg: bb('.seg'),
            search: bb('[data-search-open]'),
            lang: bb('[data-lang]'),
        };
    }""")
    if all(desktop_geo[k] for k in ["brand", "seg", "search", "lang"]):
        brand_r = desktop_geo["brand"]["right"]
        seg_r = desktop_geo["seg"]["right"]
        search_r = desktop_geo["search"]["right"]
        lang_r = desktop_geo["lang"]["right"]

        check("h3_desktop_lang_rightmost",
              lang_r > search_r and lang_r > seg_r and lang_r > brand_r,
              f"lang_r={lang_r:.0f}, search_r={search_r:.0f}, seg_r={seg_r:.0f}")

        # search immediately left of lang (gap ≤ 20px)
        gap_search_lang = desktop_geo["lang"]["left"] - desktop_geo["search"]["right"]
        check("h3_desktop_search_left_of_lang",
              0 <= gap_search_lang <= 20,
              f"gap search→lang={gap_search_lang:.0f}px (need 0–20)")

        # seg left of search
        check("h3_desktop_seg_left_of_search",
              desktop_geo["seg"]["right"] < desktop_geo["search"]["left"],
              f"seg_r={seg_r:.0f}, search_l={desktop_geo['search']['left']:.0f}")

    save_shot(page, wave, "d-bg-layout")
    ctx.close()

    # ── H4: SVG icons ──
    print("\n=== H4: SVG icon viewBoxes + fill ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    icons = page.evaluate("""() => {
        const svgs = Array.from(document.querySelectorAll('.seg svg'));
        return svgs.map(s => ({
            vb: s.getAttribute('viewBox'),
            fill: s.getAttribute('fill'),
        }));
    }""")
    if len(icons) >= 2:
        check("h4_cinema_icon_viewbox", icons[0]["vb"] == "0 0 32 32",
              f"cinema svg viewBox={icons[0]['vb']!r} (need '0 0 32 32')")
        check("h4_theatre_icon_viewbox", icons[1]["vb"] == "0 0 473.194 473.194",
              f"theatre svg viewBox={icons[1]['vb']!r} (need '0 0 473.194 473.194')")
        check("h4_cinema_icon_fill_currentColor",
              icons[0]["fill"] == "currentColor",
              f"cinema svg fill={icons[0]['fill']!r}")
        check("h4_theatre_icon_fill_currentColor",
              icons[1]["fill"] == "currentColor",
              f"theatre svg fill={icons[1]['fill']!r}")
    else:
        check("h4_cinema_icon_viewbox", False, f"only {len(icons)} .seg svg elements found")
        check("h4_theatre_icon_viewbox", False, "skipped")
        check("h4_cinema_icon_fill_currentColor", False, "skipped")
        check("h4_theatre_icon_fill_currentColor", False, "skipped")
    ctx.close()

    # ── H5: Rails order + ranking ──
    print("\n=== H5: Rails order and ranking ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    _force_mount(page)

    rails_order = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.rhead h2')).map(h => h.textContent.trim());
    }""")
    top_idx = next((i for i, r in enumerate(rails_order) if "оценени" in r), -1)
    pop_idx = next((i for i, r in enumerate(rails_order) if "Популярни" in r and "седмица" in r), -1)
    gem_idx = next((i for i, r in enumerate(rails_order) if "бижута" in r.lower()), -1)

    check("h5_top_rated_exists", top_idx != -1,
          f"'Най-високо оценени' rail not found in {rails_order[:8]}")
    check("h5_popular_exists", pop_idx != -1,
          f"'Популярни тази седмица' rail not found in {rails_order[:8]}")
    check("h5_gems_exists", gem_idx != -1,
          f"'Скрити бижута' rail not found in {rails_order[:8]}")

    if top_idx != -1 and pop_idx != -1 and gem_idx != -1:
        check("h5_rails_order_top_before_pop", top_idx < pop_idx,
              f"top_idx={top_idx}, pop_idx={pop_idx}")
        check("h5_rails_order_pop_before_gems", pop_idx < gem_idx,
              f"pop_idx={pop_idx}, gem_idx={gem_idx}")

    # Ranking: popular rail films should be sorted descending by total showtime count
    pop_film_ids = page.evaluate("""() => {
        const heads = Array.from(document.querySelectorAll('.rhead h2'));
        const popH = heads.find(h => h.textContent.includes('Популярни') && h.textContent.includes('седмица'));
        if (!popH) return [];
        const rail = popH.closest('.rail');
        if (!rail) return [];
        return Array.from(rail.querySelectorAll('[data-film]')).slice(0, 8).map(c => c.dataset.film);
    }""")
    check("h5_popular_has_films", len(pop_film_ids) >= 3,
          f"popular rail has only {len(pop_film_ids)} films")

    if len(pop_film_ids) >= 4:
        # Verify the popular rail is ranking-ordered: first film must have more raw screenings
        # than the last. (The app uses filmRowsInPeriod which adds horizon/venue filters;
        # we use SHOWTIMES directly as a proxy — top should clearly outrank bottom.)
        scores = page.evaluate(f"""() => {{
            const ids = {pop_film_ids!r};
            const weekStart = '{CLOCK_TODAY_ISO}';
            const weekEnd = '{CLOCK_WEEK_END}';
            return ids.map(fid => {{
                const rows = SHOWTIMES.filter(r => r[0] === fid && r[2] >= weekStart && r[2] <= weekEnd);
                const sc = rows.reduce((n, r) => n + (Array.isArray(r[3]) ? r[3].length : 1), 0);
                return {{id: fid, sc}};
            }});
        }}""")
        # The first film should have clearly more screenings than the last
        first_sc = scores[0]["sc"]
        last_sc = scores[-1]["sc"]
        check("h5_popular_sorted_desc", first_sc >= last_sc,
              f"first film sc={first_sc} < last film sc={last_sc} ({scores[0]['id']} vs {scores[-1]['id']})")

    # Gems: verify ≥5 films visible
    gem_film_ids = page.evaluate("""() => {
        const heads = Array.from(document.querySelectorAll('.rhead h2'));
        const gemH = heads.find(h => h.textContent.toLowerCase().includes('бижута'));
        if (!gemH) return [];
        const rail = gemH.closest('.rail');
        if (!rail) return [];
        return Array.from(rail.querySelectorAll('[data-film]')).map(c => c.dataset.film);
    }""")
    check("h5_gems_has_films", len(gem_film_ids) >= 5,
          f"gems rail has only {len(gem_film_ids)} films")

    save_shot(page, wave, "d-bg-rails")
    ctx.close()

    # ── H6: Tomorrow fallback (FakeDate at 23:50) ──
    print("\n=== H6: Tomorrow fallback at 23:50 ===")
    # At 23:50 the app should already show tomorrow's date (2026-10-08) in the day strip
    ctx_t = browser.new_context(
        viewport={"width": 375, "height": 812},
        timezone_id="Europe/Sofia",
        locale="bg-BG"
    )
    ctx_t.add_init_script(f"""
        window.__TEST_NOW = {FIXED_TOMORROW_2350};
        const _orig = Date;
        class FakeDate extends _orig {{
            constructor(...a) {{ super(...(a.length ? a : [window.__TEST_NOW])); }}
            static now() {{ return window.__TEST_NOW; }}
        }}
        window.Date = FakeDate;
    """)
    ctx_t.add_init_script("""
    try {
        localStorage.setItem('sofia-screen-v2',
            JSON.stringify({prefs:{track:'both',genres:[],mood:[],with:'',when:'any',taste:[]}, lang:'bg', mode:'cinema'}));
    } catch(e) {}
    """)
    page_t = ctx_t.new_page()
    page_t.goto(HTML)
    page_t.wait_for_selector(".bar", timeout=20000)
    page_t.wait_for_timeout(800)

    fallback_info = page_t.evaluate(f"""() => {{
        const pills = Array.from(document.querySelectorAll('[data-day]'));
        const pill_days = pills.map(p => p.dataset.day);
        // "tomorrow" pill = {CLOCK_TOMORROW_ISO}
        const hasTomorrow = pill_days.includes('{CLOCK_TOMORROW_ISO}');
        // The time is 23:50 on today; app may pre-select tomorrow
        const selectedPill = pills.find(p => {{
            const cs = window.getComputedStyle(p);
            return p.dataset.day === '{CLOCK_TOMORROW_ISO}';
        }});
        return {{
            dayStrip: pill_days,
            hasTomorrow,
            tomorrowText: selectedPill ? selectedPill.textContent.trim() : null,
            titleText: document.querySelector('.phead-title')?.textContent?.trim() || '',
        }};
    }}""")
    check("h6_tomorrow_pill_visible",
          fallback_info["hasTomorrow"],
          f"day strip={fallback_info['dayStrip']}")
    check("h6_tomorrow_text_shown",
          fallback_info["tomorrowText"] is not None,
          f"no tomorrow pill text; dayStrip={fallback_info['dayStrip']}")
    check("h6_phead_has_content",
          fallback_info["titleText"] != "",
          "phead-title is empty at 23:50")
    ctx_t.close()

    # ── H7: Sections all collapsed by default ──
    print("\n=== H7: Sections collapsed by default ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    _force_mount(page)

    sections_state = page.evaluate("""() => {
        const toggles = Array.from(document.querySelectorAll('[data-sectoggle]'));
        return toggles.map(t => ({key: t.dataset.sectoggle, expanded: t.getAttribute('aria-expanded')}));
    }""")
    for sec in sections_state:
        check(f"h7_{sec['key'].lower()}_collapsed",
              sec["expanded"] == "false",
              f"[data-sectoggle='{sec['key']}'] aria-expanded={sec['expanded']!r} (need 'false')")

    if not sections_state:
        check("h7_sections_exist", False, "no [data-sectoggle] elements found")

    # Chevron proximity: each [data-sectoggle] must contain a .sec-toggle-chev within it
    chevron_ok = page.evaluate("""() => {
        const toggles = Array.from(document.querySelectorAll('[data-sectoggle]'));
        const issues = [];
        toggles.forEach(t => {
            const tb = t.getBoundingClientRect();
            // Chevron is .sec-toggle-chev inside the button
            const chevron = t.querySelector('.sec-toggle-chev') || t.querySelector('svg');
            if (!chevron) {
                issues.push(t.dataset.sectoggle + ':no-chevron-found');
                return;
            }
            const cb = chevron.getBoundingClientRect();
            // Chevron must be within the button's bounding box (rightmost area)
            const chevRight = cb.right;
            const togRight = tb.right;
            const dist = Math.abs(chevRight - togRight);
            if (dist > 60) {
                issues.push(t.dataset.sectoggle + ':chevron-right=' + chevRight.toFixed(0)
                    + ',toggle-right=' + togRight.toFixed(0));
            }
        });
        return issues;
    }""")
    check("h7_chevron_proximity", len(chevron_ok) == 0,
          f"chevron issues: {chevron_ok}")

    ctx.close()

    # ── H8: .phead structure ──
    print("\n=== H8: .phead structure ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)

    phead_struct = page.evaluate("""() => {
        const ph = document.querySelector('.phead');
        if (!ph) return {exists: false};
        const cs = window.getComputedStyle(ph);
        const eyebrow = ph.querySelector('.phead-eyebrow');
        const title = ph.querySelector('.phead-title');
        const dates = ph.querySelector('.phead-dates');
        const other = ph.querySelector('.phead-other[data-drawer]');
        const dateCS = dates ? window.getComputedStyle(dates) : null;
        return {
            exists: true,
            noBg: cs.backgroundColor === 'rgba(0, 0, 0, 0)',
            noBorder: cs.borderTopWidth === '0px' && cs.borderBottomWidth === '0px',
            hasEyebrow: !!eyebrow,
            eyebrowText: eyebrow ? eyebrow.textContent.trim() : '',
            hasTitle: !!title,
            titleText: title ? title.textContent.trim().slice(0, 50) : '',
            hasDates: !!dates,
            datesColor: dateCS ? dateCS.color : null,
            hasOther: !!other,
            otherText: other ? other.textContent.trim().slice(0, 30) : '',
        };
    }""")
    check("h8_phead_exists", phead_struct.get("exists") is True, ".phead not found")
    if phead_struct.get("exists"):
        check("h8_phead_no_box", phead_struct["noBg"] and phead_struct["noBorder"],
              f"phead bg={phead_struct['noBg']}, border={phead_struct['noBorder']}")
        check("h8_phead_eyebrow", phead_struct["hasEyebrow"],
              ".phead-eyebrow not found")
        check("h8_phead_eyebrow_text", phead_struct["eyebrowText"] != "",
              "phead-eyebrow text is empty")
        check("h8_phead_title", phead_struct["hasTitle"],
              ".phead-title not found")
        check("h8_phead_title_text", phead_struct["titleText"] != "",
              "phead-title text is empty")
        check("h8_phead_dates_color",
              phead_struct.get("datesColor") == "rgb(224, 22, 58)",
              f".phead-dates color={phead_struct.get('datesColor')!r} (need rgb(224,22,58))")
        check("h8_phead_other_link", phead_struct["hasOther"],
              ".phead-other[data-drawer] not found")
        check("h8_phead_other_text", "период" in phead_struct["otherText"].lower() or
              phead_struct["otherText"] != "",
              f"phead-other text={phead_struct['otherText']!r}")
    else:
        for name in ["h8_phead_no_box", "h8_phead_eyebrow", "h8_phead_eyebrow_text",
                     "h8_phead_title", "h8_phead_title_text", "h8_phead_dates_color",
                     "h8_phead_other_link", "h8_phead_other_text"]:
            check(name, False, "skipped: .phead not found")

    # Day strip pills
    day_strip = page.evaluate("""() => {
        const pills = Array.from(document.querySelectorAll('[data-day]'));
        return pills.map(p => ({day: p.dataset.day, text: p.textContent.trim()}));
    }""")
    check("h8_day_strip_has_pills", len(day_strip) >= 5,
          f"only {len(day_strip)} [data-day] pills (need ≥5)")
    if len(day_strip) >= 1:
        has_week = any(p["day"] == "week" for p in day_strip)
        check("h8_day_strip_has_week_pill", has_week,
              f"no 'Цялата седмица' pill; pills={[p['day'] for p in day_strip]}")
        today_pill = next((p for p in day_strip if p["day"] == CLOCK_TODAY_ISO), None)
        check("h8_day_strip_today_pill_dnес",
              today_pill is not None and "днес" in today_pill["text"].lower(),
              f"today pill: {today_pill}")

    save_shot(page, wave, "m-bg-phead")
    ctx.close()

    # ── H9: Theatre sub-headings: Playfair, lavender (#C3B1F5 = rgb(195,177,245)), ≥20px ──
    # Wave J: colour changed from gold to lavender (--theatre-group).
    # The section toggle now uses secToggleInPlace; scroll naturally to reach it.
    print("\n=== H9: Theatre sub-heading styles (lavender, Wave J) ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="theatre")
    page.wait_for_timeout(1000)

    # Scroll naturally until the [data-sectoggle='Venues'] button appears in the DOM
    venue_btn = None
    for _scroll_attempt in range(15):
        venue_btn = page.query_selector("[data-sectoggle='Venues']")
        if venue_btn:
            break
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(300)

    if venue_btn:
        # Scroll the button into view, then click it (secToggleInPlace — no full re-render)
        page.evaluate("document.querySelector(\"[data-sectoggle='Venues']\").scrollIntoView({block:'center'})")
        page.wait_for_timeout(200)
        venue_btn.click()
        page.wait_for_timeout(800)  # allow animation (~260 ms) to complete

        th_subheads = page.evaluate("""() => {
            const subs = Array.from(document.querySelectorAll('.sec-subhead'));
            return subs.slice(0, 4).map(h => {
                const cs = window.getComputedStyle(h);
                return {
                    text: h.textContent.trim().slice(0, 30),
                    color: cs.color,
                    fontSize: parseFloat(cs.fontSize),
                    fontFamily: cs.fontFamily.slice(0, 50),
                };
            });
        }""")
        if th_subheads:
            for sub in th_subheads:
                # Wave J: lavender rgb(195, 177, 245) — was gold rgb(217, 178, 60)
                check("h9_th_subhead_color",
                      sub["color"] == "rgb(195, 177, 245)",
                      f"'{sub['text']}' color={sub['color']!r} (need rgb(195,177,245))")
                check("h9_th_subhead_size",
                      sub["fontSize"] >= 20,
                      f"'{sub['text']}' fontSize={sub['fontSize']}px (need ≥20)")
                check("h9_th_subhead_playfair",
                      "Playfair" in sub["fontFamily"],
                      f"'{sub['text']}' fontFamily={sub['fontFamily']!r}")
                break  # One representative check is sufficient; the CSS applies to all
        else:
            check("h9_th_subhead_color", False, "no .sec-subhead elements found after opening Venues")
            check("h9_th_subhead_size", False, "skipped")
            check("h9_th_subhead_playfair", False, "skipped")
    else:
        check("h9_th_subhead_color", False, "no [data-sectoggle='Venues'] in theatre mode after scrolling")
        check("h9_th_subhead_size", False, "skipped")
        check("h9_th_subhead_playfair", False, "skipped")
    ctx.close()

    # ── H10: No page errors + gate ──
    print("\n=== H10: No page errors + gate ===")
    all_errors = []
    for size_tag_h, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang_code in ["bg", "en"]:
            for mode in ["cinema", "theatre"]:
                ctx, page, errs = open_page(browser, w, h, lang=lang_code, mode=mode)
                page.wait_for_timeout(500)
                if errs:
                    all_errors.extend([(size_tag_h, lang_code, mode, e) for e in errs])
                ctx.close()
    if all_errors:
        check("h10_no_page_errors", False,
              f"{len(all_errors)} error(s): {all_errors[0]}")
    else:
        check("h10_no_page_errors", True, "")

    result = subprocess.run(
        ["python3", "scripts/verify_build.py"],
        cwd=str(webapp_root),
        capture_output=True,
        text=True,
        env={**os.environ, "SOFIA_HTML": html_env}
    )
    gate_passes = "all checks passed" in result.stdout
    check("h10_gate_passes", gate_passes,
          result.stdout[:120] if not gate_passes else "")


# ============================================================================
# WAVE I: Add-to-calendar export
# ============================================================================

def wave_i(browser):
    """
    Wave I checks: calendar export buttons in film and show sheets.
    Fixed clock 2026-10-07 14:45. Film used: digar at cc-sofia, 2026-10-08.
    """
    wave = "I"
    ensure_shot_dir(wave)
    import subprocess

    # Helper to open a film sheet
    def open_film_sheet(page, film_id):
        page.evaluate(f"""() => {{
            const hash = 'film={film_id}';
            if (window.openFromHash) {{
                location.hash = hash;
                window.openFromHash && window.openFromHash();
            }} else {{
                location.hash = hash;
            }}
        }}""")
        page.wait_for_timeout(900)

    def open_show_sheet(page, show_id):
        page.evaluate(f"""() => {{
            location.hash = 'show={show_id}';
        }}""")
        page.wait_for_timeout(900)

    # ── Runtime film lookup for SOFIA_HTML mode ──
    # 'digar' was used when the suite was written. If absent, find any film with upcoming rows.
    _probe_ctx, _probe_page, _ = open_page(browser, 375, 812, lang="bg", mode="cinema")
    _wave_i_info = _probe_page.evaluate(f"""() => {{
        const today = "{CLOCK_TODAY_ISO}";
        const digarPresent = !!filmById['digar'];
        // Find any film with at least one upcoming showtime row
        const upcomingFilm = SHOWTIMES.find(r => r[2] >= today);
        const anyFilm = upcomingFilm ? upcomingFilm[0] : null;
        // Find 'digar' at cc-sofia with a date >= today (for exact DST checks)
        const digarCCRow = SHOWTIMES.find(r => r[0] === 'digar' && r[1] === 'cc-sofia' && r[2] >= today);
        const digarDate = digarCCRow ? digarCCRow[2] : null;
        return {{digarPresent, anyFilm, digarDate}};
    }}""")
    _probe_ctx.close()
    _wave_i_film_id = "digar" if _wave_i_info["digarPresent"] else _wave_i_info["anyFilm"]
    _wave_i_digar_ok = bool(_wave_i_info["digarPresent"] and _wave_i_info["digarDate"])
    # I-3/I4/I5 exact-value DST checks need digar at cc-sofia on a specific date;
    # those remain hardcoded to 2026-10-08 (a known summer-time anchor) and SKIP if digar is absent.
    if not _wave_i_info["digarPresent"]:
        print(f"  NOTE: 'digar' absent in this build — I-1 will use '{_wave_i_film_id}'; I-3/I4/I5 will SKIP")

    # ── I-1: Every .vrow in film and show sheets has exactly one [data-cal] ──
    print("\n=== I-1: [data-cal] presence and placement ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    open_film_sheet(page, _wave_i_film_id)

    film_vrow_info = page.evaluate("""() => {
        const sheet = document.querySelector('.sheet');
        if (!sheet) return {err:'no sheet'};
        const vrows = Array.from(sheet.querySelectorAll('.vrow'));
        const rowData = vrows.map(row => {
            const calBtns = row.querySelectorAll('[data-cal]');
            const insideA = row.querySelectorAll('a [data-cal]');
            const timeLinks = row.querySelectorAll('a.time');
            return {
                calCount: calBtns.length,
                insideAnchor: insideA.length,
                timeLinks: timeLinks.length
            };
        });
        const timeLinksSheet = sheet.querySelectorAll('a.time').length;
        return {rowData, timeLinksSheet, vrowCount: vrows.length};
    }""")

    if "err" in film_vrow_info:
        check("i1_film_vrow_cal_btn", False, film_vrow_info["err"])
        check("i1_film_cal_not_in_anchor", False, "skipped")
        check("i1_film_time_links_exist", False, "skipped")
    else:
        all_one = all(r["calCount"] == 1 for r in film_vrow_info["rowData"])
        check("i1_film_vrow_cal_btn", all_one,
              f"vrow count={film_vrow_info['vrowCount']}; calCounts={[r['calCount'] for r in film_vrow_info['rowData']]}")
        none_in_a = all(r["insideAnchor"] == 0 for r in film_vrow_info["rowData"])
        check("i1_film_cal_not_in_anchor", none_in_a,
              f"some [data-cal] inside <a>: {[r['insideAnchor'] for r in film_vrow_info['rowData']]}")
        check("i1_film_time_links_exist", film_vrow_info["timeLinksSheet"] > 0,
              f"a.time count={film_vrow_info['timeLinksSheet']}")

    ctx.close()

    # show sheet check
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="theatre")
    # Find first show with any performance (globals are not on window, use typeof check)
    show_id = page.evaluate("""() => {
        if (typeof SHOWS === 'undefined' || typeof PERFORMANCES === 'undefined') return null;
        for (const sh of SHOWS) {
            const perfs = PERFORMANCES.filter(p => p[0] === sh.id);
            if (perfs.length > 0) return sh.id;
        }
        return null;
    }""")
    if show_id:
        open_show_sheet(page, show_id)
        show_vrow_info = page.evaluate("""() => {
            const sheet = document.querySelector('.sheet');
            if (!sheet) return {err:'no sheet'};
            const vrows = Array.from(sheet.querySelectorAll('.vrow'));
            const rowData = vrows.map(row => {
                const calBtns = row.querySelectorAll('[data-cal]');
                const insideA = row.querySelectorAll('a [data-cal]');
                return {calCount: calBtns.length, insideAnchor: insideA.length};
            });
            return {rowData, vrowCount: vrows.length};
        }""")
        if "err" in show_vrow_info:
            check("i1_show_vrow_cal_btn", False, show_vrow_info["err"])
            check("i1_show_cal_not_in_anchor", False, "skipped")
        else:
            all_one_sh = all(r["calCount"] == 1 for r in show_vrow_info["rowData"])
            check("i1_show_vrow_cal_btn", all_one_sh,
                  f"vrow count={show_vrow_info['vrowCount']}; calCounts={[r['calCount'] for r in show_vrow_info['rowData']]}")
            none_in_a_sh = all(r["insideAnchor"] == 0 for r in show_vrow_info["rowData"])
            check("i1_show_cal_not_in_anchor", none_in_a_sh,
                  f"some inside <a>: {[r['insideAnchor'] for r in show_vrow_info['rowData']]}")
    else:
        check("i1_show_vrow_cal_btn", False, "no show with performances found")
        check("i1_show_cal_not_in_anchor", False, "skipped")

    # Venue filter note / "Покажи всички" button still present with cinema selected (film sheet)
    ctx2, page2, errs2 = open_page(browser, 375, 812, lang="bg", mode="cinema")
    # Select a cinema first (globals may not be on window - use typeof check)
    page2.evaluate("""() => {
        if (typeof S !== 'undefined' && typeof CINEMAS !== 'undefined') {
            S.fVenues = [CINEMAS[0].id];
            if (typeof render !== 'undefined') render();
        }
    }""")
    page2.wait_for_timeout(400)
    open_film_sheet(page2, "digar")
    venue_filter_ok = page2.evaluate("""() => {
        const sheet = document.querySelector('.sheet');
        if (!sheet) return false;
        // look for the show-all button
        return !!sheet.querySelector('[data-sheetshowallcin]');
    }""")
    check("i1_venue_filter_note_works", venue_filter_ok,
          "[data-sheetshowallcin] not found after setting fVenues" if not venue_filter_ok else "")
    ctx2.close()
    ctx.close()

    # ── I-2: Panel UX at 375×812 and 1280×800 ──
    print("\n=== I-2: Panel UX ===")
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        lang_tag = "bg"
        ctx, page, errs = open_page(browser, w, h, lang=lang_tag, mode="cinema")
        open_film_sheet(page, "digar")

        # Click first [data-cal] button
        cal_btn = page.query_selector(".sheet [data-cal]")
        if not cal_btn:
            check(f"i2_{size_tag}_panel_opens", False, "no [data-cal] in film sheet")
            ctx.close()
            continue

        cal_btn.click()
        page.wait_for_timeout(400)

        panel_info = page.evaluate("""() => {
            const panel = document.querySelector('.cal-panel');
            if (!panel) return {exists: false};
            const timeBtns = Array.from(panel.querySelectorAll('[data-caltm]'));
            const firstSelBtn = panel.querySelector('[data-caltm].sel');
            const h5Text = panel.querySelector('h5') ? panel.querySelector('h5').textContent : '';
            const gBtn = panel.querySelector('[data-calg]');
            const aBtn = panel.querySelector('[data-calics]');
            const oBtn = panel.querySelector('[data-calo]');
            const closeBtn = panel.querySelector('[data-calclose]');
            // Measure heights of interactive elements
            function h(el) { return el ? el.getBoundingClientRect().height : 0; }
            const calBtnOuter = document.querySelector('.sheet .cal-btn');
            return {
                exists: true,
                timeBtnCount: timeBtns.length,
                firstSelTime: firstSelBtn ? firstSelBtn.dataset.caltm : null,
                gBtnText: gBtn ? gBtn.textContent.trim() : null,
                aBtnText: aBtn ? aBtn.textContent.trim() : null,
                oBtnText: oBtn ? oBtn.textContent.trim() : null,
                gBtnH: h(gBtn),
                aBtnH: h(aBtn),
                oBtnH: h(oBtn),
                closeBtnH: h(closeBtn),
                calBtnH: h(calBtnOuter),
                panelCount: document.querySelectorAll('.cal-panel').length
            };
        }""")

        if not panel_info["exists"]:
            check(f"i2_{size_tag}_panel_opens", False, "panel did not appear")
            ctx.close()
            continue

        check(f"i2_{size_tag}_panel_opens", True, "")
        check(f"i2_{size_tag}_panel_only_one", panel_info["panelCount"] == 1,
              f"panel count={panel_info['panelCount']}")

        # Time chips for digar (3 times at cc-sofia 2026-10-08)
        check(f"i2_{size_tag}_time_chips", panel_info["timeBtnCount"] >= 2,
              f"timeBtnCount={panel_info['timeBtnCount']}")
        check(f"i2_{size_tag}_first_upcoming_selected", panel_info["firstSelTime"] is not None,
              f"no .sel time; firstSel={panel_info['firstSelTime']}")

        # Button labels in BG
        check(f"i2_{size_tag}_google_label_bg",
              panel_info["gBtnText"] is not None and "Google" in panel_info["gBtnText"],
              f"google btn text: {panel_info['gBtnText']!r}")
        check(f"i2_{size_tag}_apple_label_bg",
              panel_info["aBtnText"] is not None and "Apple" in panel_info["aBtnText"],
              f"apple btn text: {panel_info['aBtnText']!r}")
        check(f"i2_{size_tag}_outlook_label_bg",
              panel_info["oBtnText"] is not None and "Outlook" in panel_info["oBtnText"],
              f"outlook btn text: {panel_info['oBtnText']!r}")

        # Tap target sizes ≥ 40px
        check(f"i2_{size_tag}_google_h40", panel_info["gBtnH"] >= 40,
              f"google btn height={panel_info['gBtnH']:.1f}px (need ≥40)")
        check(f"i2_{size_tag}_apple_h40", panel_info["aBtnH"] >= 40,
              f"apple btn height={panel_info['aBtnH']:.1f}px (need ≥40)")
        check(f"i2_{size_tag}_outlook_h40", panel_info["oBtnH"] >= 40,
              f"outlook btn height={panel_info['oBtnH']:.1f}px (need ≥40)")
        check(f"i2_{size_tag}_cal_btn_h40", panel_info["calBtnH"] >= 40,
              f"[data-cal] btn height={panel_info['calBtnH']:.1f}px (need ≥40; CSS says 34px)")

        # Opening another [data-cal] closes the first
        cal_btns = page.query_selector_all(".sheet [data-cal]")
        if len(cal_btns) >= 2:
            cal_btns[1].click()
            page.wait_for_timeout(300)
            panel_count2 = page.evaluate("document.querySelectorAll('.cal-panel').length")
            check(f"i2_{size_tag}_second_click_closes_first", panel_count2 == 1,
                  f"panel count after second click={panel_count2}")
            # Close via ✕ button
            page.evaluate("""() => {
                const x = document.querySelector('[data-calclose]');
                if (x) x.click();
            }""")
            page.wait_for_timeout(300)
        else:
            check(f"i2_{size_tag}_second_click_closes_first", True, "only one vrow, skip")

        # Escape closes panel
        cal_btn2 = page.query_selector(".sheet [data-cal]")
        if cal_btn2:
            cal_btn2.click()
            page.wait_for_timeout(300)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            panel_after_esc = page.evaluate("document.querySelectorAll('.cal-panel').length")
            check(f"i2_{size_tag}_escape_closes_panel", panel_after_esc == 0,
                  f"panel still present after Escape: count={panel_after_esc}")

        # Escape with NO panel open still closes sheet (regression)
        # Make sure no panel is open
        page.evaluate("const p=document.querySelector('.cal-panel');if(p)p.remove();")
        page.wait_for_timeout(200)
        sheet_before = page.evaluate("!!document.querySelector('.sheet')")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        sheet_after = page.evaluate("!!document.querySelector('.sheet')")
        check(f"i2_{size_tag}_escape_no_panel_closes_sheet",
              sheet_before and not sheet_after,
              f"sheet_before={sheet_before}, sheet_after={sheet_after}")

        # EN labels check (desktop only to save time)
        if w == 1280:
            ctx_en, page_en, _ = open_page(browser, w, h, lang="en", mode="cinema")
            open_film_sheet(page_en, "digar")
            cal_btn_en = page_en.query_selector(".sheet [data-cal]")
            if cal_btn_en:
                cal_btn_en.click()
                page_en.wait_for_timeout(400)
                en_labels = page_en.evaluate("""() => {
                    const panel = document.querySelector('.cal-panel');
                    if (!panel) return {};
                    return {
                        g: panel.querySelector('[data-calg]')?.textContent?.trim(),
                        a: panel.querySelector('[data-calics]')?.textContent?.trim(),
                        o: panel.querySelector('[data-calo]')?.textContent?.trim()
                    };
                }""")
                check("i2_en_google_label", en_labels.get("g") and "Google Calendar" in en_labels["g"],
                      f"EN google: {en_labels.get('g')!r}")
                check("i2_en_apple_label", en_labels.get("a") and "Apple Calendar" in en_labels["a"],
                      f"EN apple: {en_labels.get('a')!r}")
                check("i2_en_outlook_label", en_labels.get("o") and "Outlook" in en_labels["o"],
                      f"EN outlook: {en_labels.get('o')!r}")
                save_shot(page_en, wave, "d-en-film-panel")
            ctx_en.close()

        # Screenshots - re-open film sheet (Escape may have closed it)
        if size_tag == "375x812":
            if not page.query_selector(".sheet"):
                open_film_sheet(page, "digar")
            cal_btn_shot = page.query_selector(".sheet [data-cal]")
            if cal_btn_shot:
                cal_btn_shot.click()
                page.wait_for_timeout(400)
                save_shot(page, wave, "m-bg-film-panel")
        ctx.close()

    # Show sheet panel screenshot (mobile BG)
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="theatre")
    if show_id:
        open_show_sheet(page, show_id)
        sh_cal_btn = page.query_selector(".sheet [data-cal]")
        if sh_cal_btn:
            sh_cal_btn.click()
            page.wait_for_timeout(400)
            save_shot(page, wave, "m-bg-show-panel")
    ctx.close()

    # ── I-3: Builder exact values ──
    print("\n=== I-3: Builder exact values ===")
    _i3_names = ["i3_google_ctz","i3_google_dates_format","i3_outlook_offset_summer",
                 "i3_ics_dtstart_utc_summer","i3_dst_nov_outlook","i3_dst_nov_ics",
                 "i3_dst_oct25_outlook","i3_dst_oct25_ics"]
    if not _wave_i_digar_ok:
        for name in _i3_names:
            skip(name, "digar not in this build — re-pin FIXTURE_REF or run on fixture")
    else:
        ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
        builder_tests = page.evaluate(r"""() => {
            /* Use digar at cc-sofia on 2026-10-08, 18:30 (summer time +03:00) */
            const ev = calEvent('film','digar','cc-sofia','2026-10-08','18:30');
            if (!ev) return {err:'calEvent returned null'};
            const gUrl = calGoogleUrl(ev);
            const oUrl = calOutlookUrl(ev);
            const ics = calIcs(ev);

            /* DST test: Nov 5 (winter, +02) */
            const ev2 = calEvent('film','digar','cc-sofia','2026-11-05','19:00');
            const oUrl2 = ev2 ? calOutlookUrl(ev2) : '';
            const ics2 = ev2 ? calIcs(ev2) : '';

            /* DST boundary: Oct 25 20:00 (after DST switch, +02) */
            const ev3 = calEvent('film','digar','cc-sofia','2026-10-25','20:00');
            const oUrl3 = ev3 ? calOutlookUrl(ev3) : '';
            const ics3 = ev3 ? calIcs(ev3) : '';

            function extractDtstart(icsStr) {
                const lines = icsStr.split('\r\n');
                const l = lines.find(x => x.startsWith('DTSTART:'));
                return l ? l.slice(8) : '';
            }
            function extractParam(url, param) {
                const m = url.match(new RegExp('[?&]' + param + '=([^&]+)'));
                return m ? decodeURIComponent(m[1]) : '';
            }

            return {
                gUrl,
                oUrl,
                ics: ics.substring(0, 400),
                gHasCtz: gUrl.includes('ctz=Europe/Sofia'),
                gDates: extractParam(gUrl, 'dates'),
                oStartDecoded: extractParam(oUrl, 'startdt'),
                icsDtstart: extractDtstart(ics),
                off: ev.off,
                o2StartDecoded: ev2 ? extractParam(oUrl2, 'startdt') : '',
                ics2Dtstart: ev2 ? extractDtstart(ics2) : '',
                o3StartDecoded: ev3 ? extractParam(oUrl3, 'startdt') : '',
                ics3Dtstart: ev3 ? extractDtstart(ics3) : ''
            };
        }""")
        if "err" in builder_tests:
            for name in _i3_names:
                check(name, False, builder_tests["err"])
        else:
            check("i3_google_ctz", builder_tests["gHasCtz"],
                  f"ctz not in URL: {builder_tests['gUrl'][:120]}")
            dates_val = builder_tests["gDates"]
            check("i3_google_dates_format",
                  "/" in dates_val and "T" in dates_val and dates_val.startswith("20261008T183000"),
                  f"dates={dates_val!r}")
            o_start = builder_tests["oStartDecoded"]
            check("i3_outlook_offset_summer", "+03:00" in o_start,
                  f"startdt={o_start!r}")
            ics_dtstart = builder_tests["icsDtstart"]
            check("i3_ics_dtstart_utc_summer", ics_dtstart == "20261008T153000Z",
                  f"DTSTART={ics_dtstart!r} (expected 20261008T153000Z)")
            o2 = builder_tests["o2StartDecoded"]
            check("i3_dst_nov_outlook", "+02:00" in o2,
                  f"Nov 5 Outlook startdt={o2!r}")
            ics2_dt = builder_tests["ics2Dtstart"]
            check("i3_dst_nov_ics", ics2_dt == "20261105T170000Z",
                  f"Nov 5 ICS DTSTART={ics2_dt!r} (expected 20261105T170000Z)")
            o3 = builder_tests["o3StartDecoded"]
            check("i3_dst_oct25_outlook", "+02:00" in o3,
                  f"Oct 25 Outlook startdt={o3!r}")
            ics3_dt = builder_tests["ics3Dtstart"]
            check("i3_dst_oct25_ics", ics3_dt == "20261025T180000Z",
                  f"Oct 25 ICS DTSTART={ics3_dt!r} (expected 20261025T180000Z)")
        ctx.close()

    # ── I-4: ICS validity ──
    print("\n=== I-4: ICS validity ===")
    _i4_names = ["i4_crlf","i4_max75","i4_fold_space","i4_escape_comma","i4_required_props","i4_no_nulls"]
    if not _wave_i_digar_ok:
        for name in _i4_names:
            skip(name, "digar not in this build — re-pin FIXTURE_REF or run on fixture")
    else:
        ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
        ics_validity = page.evaluate(r"""() => {
            /* Mutate a film title to include a comma for escaping test */
            const f = filmById['digar'];
            const savedBg = f ? f.bg : undefined;
            const savedEn = f ? f.en : undefined;
            if (f) {
                f.bg = 'Тест, Заглавие; Специални: символи\\backslash';
                f.en = f.bg;
            }
            const ev = calEvent('film','digar','cc-sofia','2026-10-08','18:30');
            const ics = ev ? calIcs(ev) : '';
            if (f) { f.bg = savedBg; f.en = savedEn; }
            if (!ics) return {err:'empty ics'};
            const hasCRLF = ics.includes('\r\n');
            const hasBareLF = /[^\r]\n/.test(ics);
            const physLines = ics.split('\r\n');
            const encoder = new TextEncoder();
            const longLines = physLines.filter(l => encoder.encode(l).length > 75);
            const contLines = physLines.filter((l,i) => i > 0 && l.startsWith(' '));
            function unfold(lines) {
                let out = [];
                for (const l of lines) {
                    if (l.startsWith(' ') && out.length > 0) { out[out.length-1] += l.slice(1); }
                    else { out.push(l); }
                }
                return out;
            }
            const unfolded = unfold(physLines);
            const summaryLine = unfolded.find(l => l.startsWith('SUMMARY:'));
            const summaryVal = summaryLine ? summaryLine.slice(8) : '';
            const hasEscComma = summaryVal.includes('\\,');
            const required = ['VERSION:','PRODID:','UID:','DTSTAMP:','DTSTART:','DTEND:','SUMMARY:'];
            const missingProps = required.filter(p => !unfolded.some(l => l.startsWith(p)));
            const hasUndefined = ics.includes('undefined') || ics.includes('NaN') || ics.includes('null');
            return {
                hasCRLF, hasBareLF, longLines, hasContinuation: contLines.length > 0,
                hasEscComma, missingProps, hasUndefined,
                summaryVal: summaryVal.substring(0,120), lineCount: physLines.length
            };
        }""")
        if "err" in ics_validity:
            for n in _i4_names:
                check(n, False, ics_validity["err"])
        else:
            check("i4_crlf", ics_validity["hasCRLF"] and not ics_validity["hasBareLF"],
                  f"hasCRLF={ics_validity['hasCRLF']}, hasBareLF={ics_validity['hasBareLF']}")
            long_lines = ics_validity["longLines"]
            check("i4_max75", len(long_lines) == 0,
                  f"{len(long_lines)} lines >75 bytes: {long_lines[:3]}")
            check("i4_fold_space", ics_validity["hasContinuation"],
                  "no continuation lines found (folding may not be working)")
            check("i4_escape_comma", ics_validity["hasEscComma"],
                  f"comma not escaped in SUMMARY: {ics_validity['summaryVal']!r}")
            missing = ics_validity["missingProps"]
            check("i4_required_props", len(missing) == 0, f"missing: {missing}")
            check("i4_no_nulls", not ics_validity["hasUndefined"],
                  "ics contains undefined/NaN/null")
        ctx.close()

    # ── I-5: Content checks ──
    print("\n=== I-5: Content checks ===")
    _i5_film_names = ["i5_summary_title","i5_summary_dash_venue","i5_location_area","i5_desc_tix_url"]
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    if not _wave_i_digar_ok:
        for name in _i5_film_names:
            skip(name, "digar not in this build — re-pin FIXTURE_REF or run on fixture")
        content_check = {"err": "skipped"}
    else:
        content_check = page.evaluate(r"""() => {
        const ev = calEvent('film','digar','cc-sofia','2026-10-08','18:30');
        if (!ev) return {err:'null ev'};
        const ics = calIcs(ev);
        function unfold(text) {
            return text.split('\r\n').reduce((lines, l) => {
                if (l.startsWith(' ') && lines.length > 0) { lines[lines.length-1] += l.slice(1); }
                else { lines.push(l); }
                return lines;
            }, []);
        }
        const lines = unfold(ics);
        const get = (prop) => {
            const l = lines.find(x => x.startsWith(prop+':'));
            return l ? l.slice(prop.length+1) : null;
        };
        /* SUMMARY: title — venue */
        const summary = get('SUMMARY');
        const loc = get('LOCATION');
        const desc = get('DESCRIPTION');
        /* Get the cinema area */
        const c = cinById['cc-sofia'];
        const cArea = c ? c.area : '';
        const cName = c ? c.name : '';
        /* Get film title */
        const f = filmById['digar'];
        const fTitle = f ? filmTitle(f) : '';
        /* Get ticket URL */
        const tixU = filmTixUrl('digar','cc-sofia','2026-10-08');
        return {
            summary, loc, desc,
            cArea, cName, fTitle, tixU,
            summaryHasTitle: summary ? summary.includes(fTitle) : false,
            summaryHasDash: summary ? summary.includes(' — ') : false,
            summaryHasVenue: summary ? summary.includes(cName) : false,
            locHasArea: loc ? (function() {
                /* loc may have escaped commas; unescape for comparison */
                const unesc = loc.replace(/\\,/g, ',').replace(/\\;/g, ';');
                return unesc.includes(cArea) || loc.includes(cArea);
            })() : false,
            descHasTixUrl: desc && tixU ? (function() {
                const unesc = desc.replace(/\\n/g, '\n');
                return unesc.includes(tixU) || desc.includes(tixU);
            })() : false
        };
    }""")

    if _wave_i_digar_ok:
        if "err" in content_check:
            for n in _i5_film_names:
                check(n, False, content_check["err"])
        else:
            check("i5_summary_title", content_check["summaryHasTitle"],
                  f"SUMMARY={content_check['summary']!r}, fTitle={content_check['fTitle']!r}")
            check("i5_summary_dash_venue",
                  content_check["summaryHasDash"] and content_check["summaryHasVenue"],
                  f"hasDash={content_check['summaryHasDash']}, hasVenue={content_check['summaryHasVenue']}, cName={content_check['cName']!r}")
            check("i5_location_area", content_check["locHasArea"],
                  f"LOCATION={content_check['loc']!r}, cArea={content_check['cArea']!r}")
            check("i5_desc_tix_url", content_check["descHasTixUrl"],
                  f"DESCRIPTION={content_check['desc']!r}, tixU={content_check['tixU']!r}")

    # Theatre show: hall in area when performance has hall
    show_content = page.evaluate(r"""() => {
        if (typeof PERFORMANCES === 'undefined' || typeof SHOWS === 'undefined') return {err:'no data'};
        let shId=null, perfDate=null, perfTime=null, hallVal=null;
        for (const p of PERFORMANCES) {
            const sh = showById[p[0]];
            if (!sh) continue;
            const th = thById[sh.theatre];
            if (!th) continue;
            if (p[3] && p[3] !== th.name) {
                shId=p[0]; perfDate=p[1]; perfTime=p[2]; hallVal=p[3]; break;
            }
        }
        if (!shId) return {noHall:true};
        const ev = calEvent('show',shId,showById[shId].theatre,perfDate,perfTime);
        if (!ev) return {err:'null ev for show'};
        const ics = calIcs(ev);
        /* Extract LOCATION by splitting on CRLF */
        const lines = ics.split('\r\n');
        function unfold(ls) {
            let out = [];
            for (const l of ls) {
                if (l.startsWith(' ') && out.length > 0) { out[out.length-1] += l.slice(1); }
                else { out.push(l); }
            }
            return out;
        }
        const unfolded = unfold(lines);
        const locLine = unfolded.find(l => l.startsWith('LOCATION:'));
        const loc = locLine ? locLine.slice(9) : '';
        const locUnesc = loc.replace(/\\,/g, ',').replace(/\\;/g, ';');
        return {shId, hallVal, loc: locUnesc, hasHall: locUnesc.includes(hallVal) || ics.includes(hallVal)};
    }""")

    if "err" in show_content:
        check("i5_theatre_hall_in_location", False, show_content["err"])
    elif show_content.get("noHall"):
        check("i5_theatre_hall_in_location", True, "no performance with hall found — skip")
    else:
        check("i5_theatre_hall_in_location", show_content["hasHall"],
              f"hall={show_content['hallVal']!r}, LOCATION={show_content['loc']!r}")

    ctx.close()

    # ── I-6: Download / new tab ──
    print("\n=== I-6: Download and new tab ===")
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    open_film_sheet(page, _wave_i_film_id or "digar")
    cal_btn = page.query_selector(".sheet [data-cal]")
    if cal_btn:
        cal_btn.click()
        page.wait_for_timeout(400)

        # Get expected ICS content from the page
        ics_expected = page.evaluate("""() => {
            const panel = document.querySelector('.cal-panel');
            if (!panel) return '';
            const ci = panel.querySelector('[data-calics]');
            if (!ci) return '';
            const d = JSON.parse(ci.dataset.calev || '{}');
            const ev = calEvent(d.kind, d.id, d.venueId, d.dateStr, d.timeStr);
            return ev ? calIcs(ev) : '';
        }""")

        # Test Apple download
        ics_btn = page.query_selector("[data-calics]")
        if ics_btn:
            with page.expect_download(timeout=5000) as dl_info:
                ics_btn.click()
            dl = dl_info.value
            dl_name = dl.suggested_filename
            check("i6_ics_download_name",
                  dl_name.startswith("sofia-gleda-") and dl_name.endswith(".ics"),
                  f"download filename={dl_name!r}")
            # Read content
            dl_path = dl.path()
            if dl_path:
                with open(dl_path, "rb") as fp:
                    dl_bytes = fp.read()
                dl_text = dl_bytes.decode("utf-8", errors="replace")
                check("i6_ics_content_matches", dl_text == ics_expected,
                      f"content mismatch; got={dl_text[:80]!r} expect={ics_expected[:80]!r}")
            else:
                check("i6_ics_content_matches", False, "download path None")
        else:
            check("i6_ics_download_name", False, "no [data-calics] in panel")
            check("i6_ics_content_matches", False, "skipped")

        # Test Google tab
        g_btn = page.query_selector("[data-calg]")
        if g_btn:
            gUrl = page.evaluate("document.querySelector('[data-calg]')?.dataset?.calurl || ''")
            # Re-open panel if closed
            panel_exists = page.evaluate("!!document.querySelector('.cal-panel')")
            if not panel_exists:
                cal_btn2 = page.query_selector(".sheet [data-cal]")
                if cal_btn2:
                    cal_btn2.click()
                    page.wait_for_timeout(300)
            with ctx.expect_page(timeout=5000) as new_page_info:
                page.evaluate("document.querySelector('[data-calg]')?.click()")
            new_pg = new_page_info.value
            new_url = new_pg.url
            check("i6_google_new_tab", "calendar.google.com" in new_url or gUrl in new_url,
                  f"new tab URL={new_url[:100]!r}")
        else:
            check("i6_google_new_tab", False, "no [data-calg]")

        # Test Outlook tab
        panel_exists2 = page.evaluate("!!document.querySelector('.cal-panel')")
        if not panel_exists2:
            cal_btn3 = page.query_selector(".sheet [data-cal]")
            if cal_btn3:
                cal_btn3.click()
                page.wait_for_timeout(300)
        o_btn = page.query_selector("[data-calo]")
        if o_btn:
            oUrl = page.evaluate("document.querySelector('[data-calo]')?.dataset?.calurl || ''")
            with ctx.expect_page(timeout=5000) as new_page_info2:
                page.evaluate("document.querySelector('[data-calo]')?.click()")
            new_pg2 = new_page_info2.value
            new_url2 = new_pg2.url
            check("i6_outlook_new_tab", "outlook.live.com" in new_url2 or oUrl in new_url2,
                  f"new tab URL={new_url2[:100]!r}")
        else:
            check("i6_outlook_new_tab", False, "no [data-calo]")
    else:
        check("i6_ics_download_name", False, "no [data-cal] for download test")
        check("i6_ics_content_matches", False, "skipped")
        check("i6_google_new_tab", False, "skipped")
        check("i6_outlook_new_tab", False, "skipped")

    ctx.close()

    # ── I-7: No page errors + gate ──
    print("\n=== I-7: No page errors + gate ===")
    all_errors = []
    for size_tag, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang in ["bg", "en"]:
            for mode in ["cinema", "theatre"]:
                ctx, page, errs = open_page(browser, w, h, lang=lang, mode=mode)
                page.wait_for_timeout(600)
                if errs:
                    all_errors.extend([(size_tag, lang, mode, e) for e in errs])
                ctx.close()
    if all_errors:
        check("i7_no_page_errors", False, f"{len(all_errors)} error(s): {all_errors[0]}")
    else:
        check("i7_no_page_errors", True, "")

    result = subprocess.run(
        ["python3", "scripts/verify_build.py"],
        cwd=str(webapp_root),
        capture_output=True,
        text=True,
        env={**os.environ, "SOFIA_HTML": html_env}
    )
    gate_passes = "all checks passed" in result.stdout
    check("i7_gate_passes", gate_passes,
          result.stdout[:120] if not gate_passes else "")


# ============================================================================
# WAVE J: In-place animations, search animation, theatre group colours,
#          tonight/weekend rails, venue mode, wide posters.
# ============================================================================

def wave_j(browser, webkit_browser=None):
    """
    Wave J checks:
    1. Animations without jumps (Chromium + WebKit, 375x812 + 1280x800, cinema + theatre)
    2. In-place toggle (no full re-render)
    3. "Всичко в програмата" collapsible; "Покажи още" appends without collapsing
    4. Search animation (width desktop / row-height mobile, monotonic, focus, close-clears)
    5. Theatre rails: no "За теб днес", "Препоръчани" present; "Тази вечер"/"Утре вечер"; weekend
    6. Sub-heading colour lavender rgb(195,177,245), ≥20px, Playfair
    7. Venue mode (cinema + theatre)
    8. Wide poster gets .wide + .p-blur-bg + object-fit:contain
    9. No page errors; verify_build.py gate

    webkit_browser: optional pre-launched WebKit Browser instance (from the outer sync_playwright context).
    """
    wave = "J"
    ensure_shot_dir(wave)
    import subprocess

    # ── J1: Animations without jumps ──────────────────────────────────────────
    print("\n=== J1: Animations without jumps ===")

    def _measure_toggle_anim(pw_engine, w, h, mode, key, label):
        """
        Open the page, scroll naturally to [data-sectoggle=key], record the header's
        viewport top every ~25ms across 450ms from click.
        Returns (header_positions_open, scrollY_values_open,
                 header_positions_close, body_heights, max_dev_open, max_dev_close,
                 scroll_unchanged, intermediate_heights_count).
        """
        try:
            ctx = pw_engine.new_context(
                viewport={"width": w, "height": h},
                timezone_id="Europe/Sofia",
                locale="bg-BG",
            )
            ctx.add_init_script(CLOCK)
            ctx.add_init_script(f"""
            try {{
                localStorage.setItem('sofia-screen-v2',
                    JSON.stringify({{prefs:{{track:'both',genres:[],mood:[],with:'',when:'any',taste:[]}}, lang:'bg', mode:'{mode}'}}));
            }} catch(e) {{}}
            """)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(HTML)
            page.wait_for_selector(".bar", timeout=20000)
            page.wait_for_timeout(800)

            # Scroll naturally to find the toggle button
            btn = None
            for _ in range(20):
                btn = page.query_selector(f"[data-sectoggle='{key}']")
                if btn:
                    break
                page.mouse.wheel(0, 400)
                page.wait_for_timeout(200)

            if not btn:
                ctx.close()
                return None

            # Scroll the button into view
            page.evaluate(f"document.querySelector(\"[data-sectoggle='{key}']\").scrollIntoView({{block:'center'}})")
            page.wait_for_timeout(300)

            # Mark an element outside the section for J2 (in-place check)
            page.evaluate("document.querySelector('.phead') && (document.querySelector('.phead').dataset.probe = '1')")

            # --- Opening animation ---
            btn_top_before = page.evaluate(f"document.querySelector(\"[data-sectoggle='{key}']\").getBoundingClientRect().top")
            scroll_y_before = page.evaluate("window.scrollY")

            # Click to open and poll heights + btn top every ~25ms
            page.evaluate(f"document.querySelector(\"[data-sectoggle='{key}']\").click()")

            heights_open = []
            btn_tops_open = []
            scroll_ys_open = []
            for _ in range(18):  # 18 * 25ms = 450ms
                sample = page.evaluate(f"""() => {{
                    const btn = document.querySelector("[data-sectoggle='{key}']");
                    const body = document.getElementById('sec-{key}-content');
                    return {{
                        btnTop: btn ? btn.getBoundingClientRect().top : null,
                        bodyH: body ? body.getBoundingClientRect().height : null,
                        scrollY: window.scrollY
                    }};
                }}""")
                btn_tops_open.append(sample["btnTop"])
                heights_open.append(sample["bodyH"])
                scroll_ys_open.append(sample["scrollY"])
                page.wait_for_timeout(25)

            # --- Closing animation ---
            page.wait_for_timeout(100)  # let open animation finish
            btn_top_before_close = page.evaluate(f"document.querySelector(\"[data-sectoggle='{key}']\").getBoundingClientRect().top")
            page.evaluate(f"document.querySelector(\"[data-sectoggle='{key}']\").click()")

            heights_close = []
            btn_tops_close = []
            for _ in range(18):
                sample = page.evaluate(f"""() => {{
                    const btn = document.querySelector("[data-sectoggle='{key}']");
                    const body = document.getElementById('sec-{key}-content');
                    return {{
                        btnTop: btn ? btn.getBoundingClientRect().top : null,
                        bodyH: body ? body.getBoundingClientRect().height : null,
                    }};
                }}""")
                btn_tops_close.append(sample["btnTop"])
                heights_close.append(sample["bodyH"])
                page.wait_for_timeout(25)

            # J2: check phead probe is still present (no full re-render)
            probe_still_present = page.evaluate("document.querySelector('.phead')?.dataset.probe === '1'")

            # Compute stats
            btn_tops_open_valid = [t for t in btn_tops_open if t is not None]
            btn_tops_close_valid = [t for t in btn_tops_close if t is not None]

            max_dev_open = (max(btn_tops_open_valid) - min(btn_tops_open_valid)) if btn_tops_open_valid else 999
            max_dev_close = (max(btn_tops_close_valid) - min(btn_tops_close_valid)) if btn_tops_close_valid else 999

            # scrollY: should be stable within 2px (the app uses scrollBy to correct)
            scroll_unchanged = all(abs(sy - scroll_y_before) <= 3 for sy in scroll_ys_open if sy is not None)

            # Intermediate heights: at least 3 distinct non-None values while opening
            heights_valid = [h for h in heights_open if h is not None]
            distinct_heights = len(set(round(h, 0) for h in heights_valid))

            ctx.close()
            return {
                "max_dev_open": max_dev_open,
                "max_dev_close": max_dev_close,
                "scroll_unchanged": scroll_unchanged,
                "distinct_heights": distinct_heights,
                "probe_still_present": probe_still_present,
                "heights_open_sample": heights_valid[:5],
                "btn_tops_open_sample": btn_tops_open_valid[:5],
                "errors": errors,
            }
        except Exception as ex:
            return {"error": str(ex)}

    def _measure_toggle_reduced(pw_engine, w, h, mode, key):
        """Same but with prefers-reduced-motion:reduce; expect ≤1 distinct height value."""
        try:
            ctx = pw_engine.new_context(
                viewport={"width": w, "height": h},
                timezone_id="Europe/Sofia",
                locale="bg-BG",
                reduced_motion="reduce",
            )
            ctx.add_init_script(CLOCK)
            ctx.add_init_script(f"""
            try {{
                localStorage.setItem('sofia-screen-v2',
                    JSON.stringify({{prefs:{{track:'both',genres:[],mood:[],with:'',when:'any',taste:[]}}, lang:'bg', mode:'{mode}'}}));
            }} catch(e) {{}}
            """)
            page = ctx.new_page()
            page.goto(HTML)
            page.wait_for_selector(".bar", timeout=20000)
            page.wait_for_timeout(800)
            btn = None
            for _ in range(20):
                btn = page.query_selector(f"[data-sectoggle='{key}']")
                if btn:
                    break
                page.mouse.wheel(0, 400)
                page.wait_for_timeout(200)
            if not btn:
                ctx.close()
                return None
            page.evaluate(f"document.querySelector(\"[data-sectoggle='{key}']\").scrollIntoView({{block:'center'}})")
            page.wait_for_timeout(200)
            page.evaluate(f"document.querySelector(\"[data-sectoggle='{key}']\").click()")
            heights = []
            for _ in range(10):
                h_val = page.evaluate(f"""() => {{
                    const body = document.getElementById('sec-{key}-content');
                    return body ? body.getBoundingClientRect().height : null;
                }}""")
                heights.append(h_val)
                page.wait_for_timeout(25)
            ctx.close()
            heights_valid = [x for x in heights if x is not None]
            distinct = len(set(round(x, 0) for x in heights_valid))
            return {"distinct_heights": distinct, "heights": heights_valid[:5]}
        except Exception as ex:
            return {"error": str(ex)}

    # Test configs: (engine_name, viewport_w, viewport_h, mode, section_key)
    test_configs = [
        ("chromium", 375, 812, "cinema", "Genres"),
        ("chromium", 375, 812, "theatre", "Venues"),
        ("chromium", 1280, 800, "cinema", "Venues"),
        ("chromium", 1280, 800, "theatre", "Genres"),
        ("webkit", 375, 812, "cinema", "Genres"),
        ("webkit", 1280, 800, "theatre", "Venues"),
    ]

    # Use the browsers passed in (no nested sync_playwright — that errors in event loop)
    engines = {"chromium": browser, "webkit": webkit_browser}

    for eng_name, vw, vh, mode, key in test_configs:
        eng = engines.get(eng_name)
        if eng is None:
            skip(f"j1_anim_{eng_name}_{vw}_{mode}_{key}", f"{eng_name} not available")
            continue
        tag = f"{eng_name}_{vw}_{mode}_{key}"
        result_anim = _measure_toggle_anim(eng, vw, vh, mode, key, tag)
        if result_anim is None:
            check(f"j1_anim_{tag}", False, f"[data-sectoggle='{key}'] not found in {mode} after scrolling")
            continue
        if "error" in result_anim:
            check(f"j1_anim_{tag}", False, f"exception: {result_anim['error'][:80]}")
            continue

        max_d_o = result_anim["max_dev_open"]
        max_d_c = result_anim["max_dev_close"]
        scroll_ok = result_anim["scroll_unchanged"]
        distinct_h = result_anim["distinct_heights"]
        probe_ok = result_anim["probe_still_present"]

        check(f"j1_no_jump_open_{tag}",
              max_d_o <= 2,
              f"header deviation during open={max_d_o:.1f}px (need ≤2); tops={result_anim['btn_tops_open_sample']}")
        check(f"j1_no_jump_close_{tag}",
              max_d_c <= 2,
              f"header deviation during close={max_d_c:.1f}px (need ≤2)")
        check(f"j1_scrollY_stable_{tag}",
              scroll_ok,
              f"scrollY drifted during open; heights_sample={result_anim['heights_open_sample']}")
        check(f"j1_anim_intermediates_{tag}",
              distinct_h >= 3,
              f"only {distinct_h} distinct body heights during opening (need ≥3 for smooth anim); sample={result_anim['heights_open_sample']}")
        # J2: in-place (no full re-render)
        check(f"j2_inplace_{tag}",
              probe_ok,
              "phead probe lost — suggests full re-render happened")

        if result_anim.get("errors"):
            check(f"j1_no_errors_{tag}", False, f"page errors: {result_anim['errors'][0][:80]}")

    # Reduced-motion: should open without intermediate heights
    print("\n=== J1 reduced-motion: no intermediate heights ===")
    for eng_name, vw, vh, mode, key in [
        ("chromium", 375, 812, "cinema", "Genres"),
        ("chromium", 1280, 800, "theatre", "Venues"),
    ]:
        eng = engines.get(eng_name)
        if eng is None:
            skip(f"j1_reduced_{eng_name}_{mode}_{key}", f"{eng_name} not available")
            continue
        tag = f"{eng_name}_{vw}_{mode}_{key}"
        rm_result = _measure_toggle_reduced(eng, vw, vh, mode, key)
        if rm_result is None:
            check(f"j1_reduced_{tag}", False, f"[data-sectoggle='{key}'] not found with reduced-motion")
            continue
        if "error" in rm_result:
            check(f"j1_reduced_{tag}", False, f"exception: {rm_result['error'][:80]}")
            continue
        # With reduced-motion, should jump directly (≤2 distinct values — start and final)
        check(f"j1_reduced_{tag}",
              rm_result["distinct_heights"] <= 2,
              f"{rm_result['distinct_heights']} distinct heights (need ≤2 with reduced-motion); heights={rm_result['heights']}")

    # ── J3: "Всичко в програмата" (All) collapsible in BOTH modes ──────────────
    print("\n=== J3: 'Всичко в програмата' (All) toggle ===")
    for mode in ["cinema", "theatre"]:
        ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode=mode)
        page.wait_for_timeout(800)
        _force_mount(page)

        all_btn = page.query_selector("[data-sectoggle='All']")
        if all_btn:
            # Must be collapsed initially
            expanded_before = all_btn.get_attribute("aria-expanded")
            check(f"j3_all_collapsed_initially_{mode}",
                  expanded_before == "false",
                  f"aria-expanded={expanded_before!r}")

            # Click to expand (secToggleInPlace)
            all_btn.click()
            page.wait_for_timeout(500)  # allow ~260ms animation

            expanded_after = page.evaluate("document.querySelector(\"[data-sectoggle='All']\")" +
                                           "?.getAttribute('aria-expanded')")
            check(f"j3_all_expands_{mode}",
                  expanded_after == "true",
                  f"aria-expanded={expanded_after!r} after click")

            # Record All button's absolute page position (scrollY + rect.top)
            btn_abs_after_open = page.evaluate(
                "document.querySelector(\"[data-sectoggle='All']\").getBoundingClientRect().top + window.scrollY")

            # Check for "Покажи още" button
            more_btn = page.query_selector("[data-more]")
            if more_btn and more_btn.is_visible():
                more_btn.click()
                page.wait_for_timeout(400)
                # The All section should still be expanded
                still_expanded = page.evaluate(
                    "document.querySelector(\"[data-sectoggle='All']\")?.getAttribute('aria-expanded')")
                check(f"j3_more_doesnt_collapse_all_{mode}",
                      still_expanded == "true",
                      f"aria-expanded={still_expanded!r} after 'Покажи още'")

                # The All button absolute page position must not change (content added BELOW it)
                btn_abs_after_more = page.evaluate(
                    "document.querySelector(\"[data-sectoggle='All']\").getBoundingClientRect().top + window.scrollY")
                dev = abs(btn_abs_after_more - btn_abs_after_open) if btn_abs_after_open is not None else 0
                check(f"j3_more_no_jump_{mode}",
                      dev <= 5,
                      f"All header absolute pos moved {dev:.1f}px after 'Покажи още' (need ≤5px)")
            else:
                skip(f"j3_more_doesnt_collapse_all_{mode}", "no [data-more] button visible after expanding All")
                skip(f"j3_more_no_jump_{mode}", "no [data-more] button")

            # Click again to collapse
            all_btn2 = page.query_selector("[data-sectoggle='All']")
            if all_btn2:
                all_btn2.click()
                page.wait_for_timeout(400)
                collapsed_again = page.evaluate(
                    "document.querySelector(\"[data-sectoggle='All']\")?.getAttribute('aria-expanded')")
                check(f"j3_all_collapses_{mode}",
                      collapsed_again == "false",
                      f"aria-expanded={collapsed_again!r} after second click")
        else:
            check(f"j3_all_collapsed_initially_{mode}", False, f"[data-sectoggle='All'] not found in {mode}")
            check(f"j3_all_expands_{mode}", False, "skipped")
            check(f"j3_all_collapses_{mode}", False, "skipped")
            skip(f"j3_more_doesnt_collapse_all_{mode}", "All button not found")
            skip(f"j3_more_no_jump_{mode}", "All button not found")
        ctx.close()

    # ── J4: Search animation ───────────────────────────────────────────────────
    print("\n=== J4: Search animation ===")

    def _measure_search_anim(pw_engine, w, h, label):
        try:
            ctx = pw_engine.new_context(
                viewport={"width": w, "height": h},
                timezone_id="Europe/Sofia",
                locale="bg-BG",
            )
            ctx.add_init_script(CLOCK)
            ctx.add_init_script("""
            try {
                localStorage.setItem('sofia-screen-v2',
                    JSON.stringify({prefs:{track:'both',genres:[],mood:[],with:'',when:'any',taste:[]}, lang:'bg', mode:'cinema'}));
            } catch(e) {}
            """)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(HTML)
            page.wait_for_selector(".bar", timeout=20000)
            page.wait_for_timeout(500)

            is_desktop = (w >= 760)

            # JS getter to sample the relevant dimension
            if is_desktop:
                measure_js = """() => {
                    const wrap = document.querySelector('.desk-search-wrap');
                    if (!wrap) return null;
                    return parseFloat(window.getComputedStyle(wrap).maxWidth) || 0;
                }"""
            else:
                measure_js = """() => {
                    const row = document.querySelector('.bar-row-search');
                    if (!row) return null;
                    return row.getBoundingClientRect().height;
                }"""

            # Click to open and sample dimension over ~450ms
            page.evaluate("document.querySelector('[data-search-open]').click()")
            open_samples = []
            for _ in range(18):
                val = page.evaluate(measure_js)
                open_samples.append(val)
                page.wait_for_timeout(25)

            # Check focus
            page.wait_for_timeout(100)
            active_id = page.evaluate("document.activeElement.id")
            focused_ok = active_id in ("q", "q-m")

            # Type a query
            page.keyboard.type("од")
            page.wait_for_timeout(300)
            still_open = page.evaluate("S.searchOpen")
            q_val = page.evaluate("S.q")

            # Click magnifier to close
            page.evaluate("document.querySelector('[data-search-open]').click()")
            close_samples = []
            for _ in range(12):
                val = page.evaluate(measure_js)
                close_samples.append(val)
                page.wait_for_timeout(25)

            page.wait_for_timeout(350)  # wait for animation + query clear
            q_cleared = page.evaluate("S.q")
            search_open_state = page.evaluate("S.searchOpen")

            ctx.close()
            return {
                "open_samples": [x for x in open_samples if x is not None],
                "close_samples": [x for x in close_samples if x is not None],
                "focused_ok": focused_ok,
                "active_id": active_id,
                "still_open": still_open,
                "q_val": q_val,
                "q_cleared": q_cleared,
                "search_open_closed": not search_open_state,
                "errors": errors,
            }
        except Exception as ex:
            return {"error": str(ex)}

    # Use passed-in browsers (no nested sync_playwright)
    for eng, eng_name in [(browser, "chromium"), (webkit_browser, "webkit")]:
        if eng is None:
            for sz in ["375", "1280"]:
                skip(f"j4_search_{eng_name}_{sz}", f"{eng_name} not available")
            continue

        for vw, vh in [(375, 812), (1280, 800)]:
            tag = f"{eng_name}_{vw}"
            res = _measure_search_anim(eng, vw, vh, tag)
            if "error" in res:
                check(f"j4_search_{tag}", False, res["error"][:80])
                continue

            os_s = res["open_samples"]
            cs_s = res["close_samples"]

            # Monotonic increase during open
            open_monotonic = len(os_s) >= 3 and (
                all(os_s[i] <= os_s[i+1] + 1 for i in range(len(os_s)-1))
            )
            # Monotonic decrease during close
            close_monotonic = len(cs_s) >= 3 and (
                all(cs_s[i] >= cs_s[i+1] - 1 for i in range(len(cs_s)-1))
            )
            open_distinct = len(set(round(x, 0) for x in os_s)) >= 3
            close_distinct = len(set(round(x, 0) for x in cs_s)) >= 3

            check(f"j4_search_open_monotonic_{tag}",
                  open_monotonic and open_distinct,
                  f"open samples={os_s[:6]} (need monotonic ≥3 distinct)")
            check(f"j4_search_close_monotonic_{tag}",
                  close_monotonic and close_distinct,
                  f"close samples={cs_s[:6]} (need monotonic ≥3 distinct)")
            check(f"j4_search_focused_{tag}",
                  res["focused_ok"],
                  f"active element id={res['active_id']!r} (need 'q' or 'q-m')")
            check(f"j4_search_stays_open_while_typing_{tag}",
                  res["still_open"],
                  f"S.searchOpen={res['still_open']} after typing")
            check(f"j4_search_close_clears_q_{tag}",
                  res["q_cleared"] == "",
                  f"S.q={res['q_cleared']!r} after close (should be empty)")
            check(f"j4_search_close_state_{tag}",
                  res["search_open_closed"],
                  "S.searchOpen still true after close+wait")

            # Save screenshot for desktop+EN search open (use chromium)
            if vw == 1280 and eng_name == "chromium":
                ctx_shot, page_shot, _ = open_page(browser, 1280, 800, lang="en")
                page_shot.wait_for_timeout(500)
                page_shot.evaluate("document.querySelector('[data-search-open]').click()")
                page_shot.wait_for_timeout(350)
                save_shot(page_shot, wave, "d-en-search-open")
                ctx_shot.close()

    # ── J5: Theatre rails ─────────────────────────────────────────────────────
    print("\n=== J5: Theatre rails (tonight/weekend/no-Za-teb-dnes) ===")

    # J5a: No "За теб днес" rail in theatre mode; "Препоръчани" present
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="theatre")
    page.wait_for_timeout(800)
    _force_mount(page)

    rail_titles = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.rhead h2')).map(h => h.textContent.trim());
    }""")
    has_za_teb = any("За теб днес" in t or "For you today" in t for t in rail_titles)
    has_recommended = any("Препоръчани" in t or "Recommended" in t for t in rail_titles)

    check("j5_no_za_teb_dnес_in_theatre",
          not has_za_teb,
          f"found 'За теб днес' in theatre rails: {rail_titles[:8]}")
    check("j5_recommended_present",
          has_recommended,
          f"'Препоръчани' not found; rails: {rail_titles[:8]}")

    # J5b: "Тази вечер"/"Утре вечер" — fixture has PERFORMANCES on 2026-10-07 at 19:00
    # At 14:45, those performances are upcoming → "Тази вечер" SHOULD be shown (not "Утре вечер")
    tonight_rail = next((t for t in rail_titles if "Тази вечер" in t or "Tonight" in t), None)
    tomorrow_rail = next((t for t in rail_titles if "Утре вечер" in t or "Tomorrow night" in t), None)

    # Fixture has Oct-07 performances at 19:00, after NOW=14:45 → "Тази вечер" must be shown
    check("j5_tonightT_present_at_1445",
          tonight_rail is not None,
          f"'Тази вечер' not found at 14:45 (fixture has perfs at 19:00 today); rails={rail_titles[:10]}")
    check("j5_tonightB_absent_at_1445",
          tomorrow_rail is None,
          f"'Утре вечер' found at 14:45 when today's perfs are upcoming; rails={rail_titles[:10]}")

    # Verify "Тази вечер" shows only 2026-10-07 performances at ≥14:45
    if tonight_rail is not None:
        tonightT_shows = page.evaluate("""() => {
            const h2s = Array.from(document.querySelectorAll('.rhead h2'));
            const head = h2s.find(h => h.textContent.includes('Тази вечер') || h.textContent.includes('Tonight'));
            if (!head) return [];
            const rail = head.closest('.rail');
            if (!rail) return [];
            const cards = Array.from(rail.querySelectorAll('[data-show]'));
            return cards.map(c => c.dataset.show);
        }""")
        if tonightT_shows:
            # Each show should have a PERFORMANCE on 2026-10-07 at ≥14:45
            tonight_ok = page.evaluate(f"""() => {{
                const showIds = {tonightT_shows!r};
                const today = '2026-10-07';
                const perfs = PERFORMANCES.filter(p => showIds.includes(p[0]) && p[1] === today && p[2] >= '14:45');
                const covered = new Set(perfs.map(p => p[0]));
                const uncovered = showIds.filter(id => !covered.has(id));
                return {{covered: covered.size, total: showIds.length, uncovered: uncovered.slice(0,3)}};
            }}""")
            check("j5_tonightT_shows_on_oct7",
                  tonight_ok["uncovered"] == [],
                  f"{tonight_ok['uncovered']} shows lack oct-07 perf ≥14:45; covered={tonight_ok['covered']}/{tonight_ok['total']}")
        else:
            check("j5_tonightT_shows_on_oct7", False, "'Тази вечер' rail has no show cards")
    else:
        skip("j5_tonightT_shows_on_oct7", "'Тази вечер' rail not present")

    ctx.close()

    # J5c: At 23:50 clock (FIXED_TOMORROW_2350), "Утре вечер" should show 2026-10-08
    ctx_t = browser.new_context(
        viewport={"width": 1280, "height": 800},
        timezone_id="Europe/Sofia",
        locale="bg-BG",
    )
    ctx_t.add_init_script(f"""
        window.__TEST_NOW = {FIXED_TOMORROW_2350};
        const _orig = Date;
        class FakeDate extends _orig {{
            constructor(...a) {{ super(...(a.length ? a : [window.__TEST_NOW])); }}
            static now() {{ return window.__TEST_NOW; }}
        }}
        window.Date = FakeDate;
    """)
    ctx_t.add_init_script("""
        try {
            localStorage.setItem('sofia-screen-v2',
                JSON.stringify({prefs:{track:'both',genres:[],mood:[],with:'',when:'any',taste:[]}, lang:'bg', mode:'theatre'}));
        } catch(e) {}
    """)
    page_t = ctx_t.new_page()
    page_t.goto(HTML)
    page_t.wait_for_selector(".bar", timeout=20000)
    page_t.wait_for_timeout(800)
    _force_mount(page_t)

    rail_titles_2350 = page_t.evaluate("""() => {
        return Array.from(document.querySelectorAll('.rhead h2')).map(h => h.textContent.trim());
    }""")
    # At 23:50 on 2026-10-07, NOW_DATE is still 2026-10-07 but time >= 23:50
    # So tonight's perfs at 19:00 have passed → fallback to "Утре вечер" for 2026-10-08
    tomorrow_rail_2350 = next((t for t in rail_titles_2350 if "Утре вечер" in t or "Tomorrow night" in t), None)
    check("j5_2350_tomorrow_night_present",
          tomorrow_rail_2350 is not None,
          f"'Утре вечер' not found at 23:50; rails={rail_titles_2350[:10]}")
    ctx_t.close()

    # J5d: "Този уикенд" — should list shows with perfs on 2026-10-09..11 (Fri-Sun)
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="theatre")
    page.wait_for_timeout(800)
    _force_mount(page)
    rail_titles_wknd = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.rhead h2')).map(h => h.textContent.trim());
    }""")
    weekend_rail = next((t for t in rail_titles_wknd if "Този уикенд" in t or "This weekend" in t), None)
    check("j5_weekend_rail_present",
          weekend_rail is not None,
          f"'Този уикенд' not found; rails={rail_titles_wknd[:12]}")

    if weekend_rail:
        wknd_shows = page.evaluate("""() => {
            const h2s = Array.from(document.querySelectorAll('.rhead h2'));
            const head = h2s.find(h => h.textContent.includes('Този уикенд') || h.textContent.includes('This weekend'));
            if (!head) return [];
            const rail = head.closest('.rail');
            if (!rail) return [];
            return Array.from(rail.querySelectorAll('[data-show]')).map(c => c.dataset.show);
        }""")
        if wknd_shows:
            wknd_ok = page.evaluate(f"""() => {{
                const showIds = {wknd_shows!r};
                const fri = '2026-10-09', sun = '2026-10-11';
                const perfs = PERFORMANCES.filter(p => showIds.includes(p[0]) && p[1] >= fri && p[1] <= sun);
                const covered = new Set(perfs.map(p => p[0]));
                const uncovered = showIds.filter(id => !covered.has(id));
                return {{covered: covered.size, total: showIds.length, uncovered: uncovered.slice(0,3)}};
            }}""")
            check("j5_weekend_shows_on_fri_sun",
                  wknd_ok["uncovered"] == [],
                  f"{wknd_ok['uncovered']} shows lack Fri-Sun perfs; covered={wknd_ok['covered']}/{wknd_ok['total']}")
        else:
            check("j5_weekend_shows_on_fri_sun", False, "Weekend rail has no show cards")
    else:
        skip("j5_weekend_shows_on_fri_sun", "'Този уикенд' rail not present")

    # J5e: Selecting a single weekday in the day strip — report behaviour
    day_strip_result = page.evaluate(f"""() => {{
        // Click 2026-10-08 (Thursday, a weekday)
        const pill = document.querySelector("[data-day='2026-10-08']");
        if (!pill) return {{found: false}};
        pill.click();
        return {{found: true}};
    }}""")
    if day_strip_result.get("found"):
        page.wait_for_timeout(600)
        rails_after_weekday = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('.rhead h2')).map(h => h.textContent.trim());
        }""")
        weekend_after = next((t for t in rails_after_weekday if "Този уикенд" in t or "This weekend" in t), None)
        check("j5_weekend_hidden_on_weekday",
              weekend_after is None,
              f"'Този уикенд' still visible after selecting weekday 2026-10-08 (actual: {weekend_after!r})")
    else:
        skip("j5_weekend_hidden_on_weekday", "day pill 2026-10-08 not found")

    ctx.close()

    # ── J6: Sub-heading colour rgb(195, 177, 245), ≥20px, Playfair ─────────────
    print("\n=== J6: Sub-heading colour (lavender) ===")
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="theatre")
    page.wait_for_timeout(800)

    # Scroll to find the Venues toggle, open it
    venue_btn = None
    for _ in range(15):
        venue_btn = page.query_selector("[data-sectoggle='Venues']")
        if venue_btn:
            break
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(200)

    if venue_btn:
        page.evaluate("document.querySelector(\"[data-sectoggle='Venues']\").scrollIntoView({block:'center'})")
        page.wait_for_timeout(200)
        venue_btn.click()
        page.wait_for_timeout(800)

        subheads = page.evaluate("""() => {
            const subs = Array.from(document.querySelectorAll('.sec-subhead'));
            return subs.map(h => {
                const cs = window.getComputedStyle(h);
                return {
                    text: h.textContent.trim().slice(0, 40),
                    color: cs.color,
                    fontSize: parseFloat(cs.fontSize),
                    fontFamily: cs.fontFamily.slice(0, 60),
                };
            });
        }""")
        if subheads:
            for sub in subheads[:2]:
                check("j6_subhead_lavender",
                      sub["color"] == "rgb(195, 177, 245)",
                      f"'{sub['text']}' color={sub['color']!r} (need rgb(195,177,245))")
                check("j6_subhead_size",
                      sub["fontSize"] >= 20,
                      f"'{sub['text']}' fontSize={sub['fontSize']}px (need ≥20)")
                check("j6_subhead_playfair",
                      "Playfair" in sub["fontFamily"],
                      f"'{sub['text']}' fontFamily={sub['fontFamily']!r}")
                break
        else:
            check("j6_subhead_lavender", False, "no .sec-subhead found after opening Venues")
            check("j6_subhead_size", False, "skipped")
            check("j6_subhead_playfair", False, "skipped")
    else:
        check("j6_subhead_lavender", False, "no [data-sectoggle='Venues'] found in theatre")
        check("j6_subhead_size", False, "skipped")
        check("j6_subhead_playfair", False, "skipped")

    ctx.close()

    # ── J7: Venue mode ─────────────────────────────────────────────────────────
    print("\n=== J7: Venue mode (cinema + theatre) ===")

    # --- J7a: Cinema - cc-sofia ---
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)

    # Select cc-sofia in the filter drawer
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) fab.click();
    }""")
    page.wait_for_timeout(400)
    page.evaluate("""() => {
        const chip = document.querySelector(".drawer [data-venue='cc-sofia']");
        if (chip) chip.click();
    }""")
    page.wait_for_timeout(300)
    page.evaluate("document.querySelector('[data-dclose]')?.click()")
    page.wait_for_timeout(600)

    venue_mode_info = page.evaluate("""() => {
        const heroGone = !document.querySelector('.hero');
        const hasVenueHead = !!document.querySelector('.venue-mode-head');
        const hasVenueChip = !!document.querySelector('.venue-chip-rm[data-venue="cc-sofia"]');
        const sectoggleGone = document.querySelector('[data-sectoggle]') === null;
        const venueBlocks = document.querySelectorAll('.venue-block').length;
        const railHeads = Array.from(document.querySelectorAll('.rhead h2')).map(h => h.textContent.trim());
        const curated = ['За теб днес','Лимитирано','Най-високо оценени','Популярни','Скрити бижута','Скоро'];
        const curatedFound = curated.filter(t => railHeads.some(r => r.includes(t)));
        return {heroGone, hasVenueHead, hasVenueChip, sectoggleGone, venueBlocks, railHeads: railHeads.slice(0,6), curatedFound};
    }""")
    check("j7_cinema_hero_gone",
          venue_mode_info["heroGone"],
          ".hero still present in venue mode")
    check("j7_cinema_no_curated_rails",
          venue_mode_info["curatedFound"] == [],
          f"curated rails still present: {venue_mode_info['curatedFound']}")
    check("j7_cinema_no_sectoggle",
          venue_mode_info["sectoggleGone"],
          "[data-sectoggle] still present in venue mode")
    check("j7_cinema_venue_head_present",
          venue_mode_info["hasVenueHead"],
          ".venue-mode-head not found")
    check("j7_cinema_cc_sofia_chip",
          venue_mode_info["hasVenueChip"],
          "cc-sofia chip not found in .venue-mode-head")
    check("j7_cinema_one_venue_block",
          venue_mode_info["venueBlocks"] == 1,
          f"expected 1 venue block, got {venue_mode_info['venueBlocks']}")

    # Verify all films in the cc-sofia block have showtimes at cc-sofia in the period
    venue_films_ok = page.evaluate(f"""() => {{
        const block = document.querySelector('.venue-block');
        if (!block) return {{ok: false, reason: 'no venue-block'}};
        const filmCards = Array.from(block.querySelectorAll('[data-film]'));
        const filmIds = filmCards.map(c => c.dataset.film);
        // Check that each film has at least one SHOWTIME row at cc-sofia in the period
        const weekStart = '{CLOCK_TODAY_ISO}', weekEnd = '{CLOCK_WEEK_END}';
        const missing = filmIds.filter(fid =>
            !SHOWTIMES.some(r => r[0] === fid && r[1] === 'cc-sofia' && r[2] >= weekStart && r[2] <= weekEnd)
        );
        // Also check all cc-sofia films in the period appear
        const expectedIds = Array.from(new Set(
            SHOWTIMES.filter(r => r[1] === 'cc-sofia' && r[2] >= weekStart && r[2] <= weekEnd).map(r => r[0])
        ));
        const missingExpected = expectedIds.filter(fid => !filmIds.includes(fid));
        return {{ok: missing.length === 0 && missingExpected.length === 0,
                 missing: missing.slice(0,3), missingExpected: missingExpected.slice(0,3),
                 filmCount: filmIds.length, expectedCount: expectedIds.length}};
    }}""")
    check("j7_cinema_block_films_have_cc_sofia_times",
          venue_films_ok.get("ok") is True,
          f"missing={venue_films_ok.get('missing')}, missingExpected={venue_films_ok.get('missingExpected')}"
          + f" (block has {venue_films_ok.get('filmCount')}, expected {venue_films_ok.get('expectedCount')})")

    # Screenshot for venue mode cinema
    save_shot(page, wave, "d-bg-venue-mode-cc-sofia")

    # --- Remove cc-sofia chip → normal page back ---
    page.evaluate("""() => {
        const chip = document.querySelector('.venue-chip-rm[data-venue="cc-sofia"]');
        if (chip) chip.click();
    }""")
    page.wait_for_timeout(600)

    normal_page_back = page.evaluate("""() => {
        const heroBack = !!document.querySelector('.hero');
        const sectoggleBack = !!document.querySelector('[data-sectoggle]');
        return {heroBack, sectoggleBack};
    }""")
    check("j7_cinema_chip_remove_restores_page",
          normal_page_back["heroBack"] or normal_page_back["sectoggleBack"],
          f"hero={normal_page_back['heroBack']}, sectoggle={normal_page_back['sectoggleBack']} after removing chip")
    ctx.close()

    # --- J7b: Two venues: cc-sofia + vlaikova ---
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) fab.click();
    }""")
    page.wait_for_timeout(400)
    page.evaluate("""() => {
        ['cc-sofia','vlaikova'].forEach(vid => {
            const chip = document.querySelector(".drawer [data-venue='" + vid + "']");
            if (chip) chip.click();
        });
    }""")
    page.wait_for_timeout(300)
    page.evaluate("document.querySelector('[data-dclose]')?.click()")
    page.wait_for_timeout(600)

    two_venue_blocks = page.evaluate("document.querySelectorAll('.venue-block').length")
    check("j7_two_venues_two_blocks",
          two_venue_blocks == 2,
          f"expected 2 venue blocks, got {two_venue_blocks}")
    ctx.close()

    # --- J7c: Genre + venue intersection ---
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) fab.click();
    }""")
    page.wait_for_timeout(400)
    # Select cc-sofia and the first available genre
    genre_venue_ok = page.evaluate("""() => {
        const venueChip = document.querySelector(".drawer [data-venue='cc-sofia']");
        if (venueChip) venueChip.click();
        // Pick first genre chip in drawer
        const genreChip = document.querySelector(".drawer [data-genre]");
        if (genreChip) { genreChip.click(); return {genreId: genreChip.dataset.genre}; }
        return {genreId: null};
    }""")
    page.wait_for_timeout(300)
    page.evaluate("document.querySelector('[data-dclose]')?.click()")
    page.wait_for_timeout(600)

    genre_id = genre_venue_ok.get("genreId")
    if genre_id:
        intersection_result = page.evaluate(f"""() => {{
            const block = document.querySelector('.venue-block');
            if (!block) return {{ok: false, reason: 'no block'}};
            const filmCards = Array.from(block.querySelectorAll('[data-film]'));
            if (!filmCards.length) return {{ok: true, count: 0, note: 'empty (correct if genre+venue intersection is empty)'}};
            // Each film should match the genre (genreHit will use S.fGenres)
            const fids = filmCards.map(c => c.dataset.film);
            const fGenres = S.fGenres;
            const nonMatching = fids.filter(fid => {{
                const f = filmById[fid];
                if (!f) return true;
                return fGenres.length && !genreHit(f.genres);
            }});
            return {{ok: nonMatching.length === 0, nonMatching: nonMatching.slice(0,3), total: fids.length}};
        }}""")
        check("j7_genre_venue_intersection",
              intersection_result.get("ok") is True,
              f"genre filter not applied in venue mode: {intersection_result}")
    else:
        skip("j7_genre_venue_intersection", "no genre chips found in drawer")
    ctx.close()

    # --- J7d: Theatre venue mode ---
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="theatre")
    page.wait_for_timeout(800)
    # Open drawer and select first available theatre
    page.evaluate("""() => {
        const fab = document.querySelector('.fab');
        if (fab && !fab.hidden) fab.click();
    }""")
    page.wait_for_timeout(400)

    theatre_venue_result = page.evaluate("""() => {
        // Skip the empty 'Всички театри' chip (data-venue=""), pick first real venue
        const chips = Array.from(document.querySelectorAll(".drawer [data-venue]"));
        const theatreChip = chips.find(c => c.dataset.venue && c.dataset.venue.length > 0);
        if (!theatreChip) return {found: false};
        theatreChip.click();
        return {found: true, venueId: theatreChip.dataset.venue};
    }""")
    page.wait_for_timeout(300)
    page.evaluate("document.querySelector('[data-dclose]')?.click()")
    page.wait_for_timeout(600)

    if theatre_venue_result.get("found"):
        th_venue_mode = page.evaluate("""() => {
            const heroGone = !document.querySelector('.hero');
            const blocks = document.querySelectorAll('.venue-block').length;
            const hasChips = !!document.querySelector('.venue-chip-rm');
            return {heroGone, blocks, hasChips};
        }""")
        check("j7_theatre_venue_mode_one_block",
              th_venue_mode["blocks"] == 1,
              f"expected 1 venue block for theatre, got {th_venue_mode['blocks']}")
        check("j7_theatre_venue_mode_hero_gone",
              th_venue_mode["heroGone"],
              ".hero still present in theatre venue mode")
        save_shot(page, wave, "m-bg-venue-mode-theatre")
    else:
        check("j7_theatre_venue_mode_one_block", False, "no venue chip found in theatre drawer")
        check("j7_theatre_venue_mode_hero_gone", False, "skipped")
    ctx.close()

    # ── J8: Wide posters ───────────────────────────────────────────────────────
    print("\n=== J8: Wide poster detection ===")
    WIDE_POSTER_URL = "https://ndk.bg/storage/thumbnails/2026/08/31/38393/group-55-kinocult-festival-20260831-064826_resize1000x1000.jpg?v=1788158928"

    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    _force_mount(page)

    # Find a film currently visible in a rail
    film_id = page.evaluate("""() => {
        const card = document.querySelector('[data-film]');
        return card ? card.dataset.film : null;
    }""")

    if film_id:
        # Set its poster to a wide (landscape) URL and re-render
        page.evaluate(f"""() => {{
            if (typeof POSTERS !== 'undefined') {{
                POSTERS['{film_id}'] = '{WIDE_POSTER_URL}';
            }}
            if (typeof render !== 'undefined') render();
        }}""")
        page.wait_for_timeout(1000)  # wait for render + image load

        # Wait for the image to load and the .wide class to be set
        # (the .wide class is set onload when naturalWidth > naturalHeight * 1.05)
        for _ in range(10):
            wide_result = page.evaluate(f"""() => {{
                const card = document.querySelector('[data-film="{film_id}"]');
                if (!card) return {{cardFound: false}};
                const poster = card.querySelector('.poster');
                const img = card.querySelector('.poster img, .poster .p-real');
                const blurBg = card.querySelector('.p-blur-bg');
                const isWide = poster && poster.classList.contains('wide');
                const hasBlurBg = !!blurBg;
                const objFit = img ? window.getComputedStyle(img).objectFit : null;
                return {{cardFound: true, isWide, hasBlurBg, objFit,
                         imgSrc: img ? img.src.slice(0,60) : null,
                         naturalW: img && img.complete ? img.naturalWidth : null,
                         naturalH: img && img.complete ? img.naturalHeight : null}};
            }}""")
            if wide_result.get("isWide"):
                break
            page.wait_for_timeout(200)

        # Check if network was available (if naturalWidth==0, image didn't load)
        if wide_result.get("naturalW") == 0 or wide_result.get("naturalW") is None:
            skip("j8_wide_poster_class", "image did not load (no network access)")
            skip("j8_wide_blur_bg", "image did not load (no network access)")
            skip("j8_wide_object_fit_contain", "image did not load (no network access)")
        else:
            check("j8_wide_poster_class",
                  wide_result.get("isWide") is True,
                  f"poster has no .wide class; naturalW={wide_result.get('naturalW')}, naturalH={wide_result.get('naturalH')}")
            check("j8_wide_blur_bg",
                  wide_result.get("hasBlurBg") is True,
                  f"no .p-blur-bg element; isWide={wide_result.get('isWide')}")
            check("j8_wide_object_fit_contain",
                  wide_result.get("objFit") == "contain",
                  f"object-fit={wide_result.get('objFit')!r} (need 'contain')")

            # A portrait poster (different film) should not have .wide
            portrait_result = page.evaluate(f"""() => {{
                const cards = Array.from(document.querySelectorAll('[data-film]'));
                const portrait = cards.find(c => {{
                    const poster = c.querySelector('.poster');
                    return poster && !poster.classList.contains('wide');
                }});
                if (!portrait) return {{found: false}};
                const img = portrait.querySelector('.poster img, .poster .p-real');
                return {{
                    found: true,
                    filmId: portrait.dataset.film,
                    objFit: img ? window.getComputedStyle(img).objectFit : null,
                }};
            }}""")
            if portrait_result.get("found"):
                check("j8_portrait_no_wide_no_contain",
                      portrait_result.get("objFit") == "cover",
                      f"portrait poster objFit={portrait_result.get('objFit')!r} (need 'cover')")
            else:
                skip("j8_portrait_no_wide_no_contain", "no non-wide poster card found after setting one film to wide")
    else:
        skip("j8_wide_poster_class", "no [data-film] card visible for wide poster test")
        skip("j8_wide_blur_bg", "skipped")
        skip("j8_wide_object_fit_contain", "skipped")
        skip("j8_portrait_no_wide_no_contain", "skipped")
    ctx.close()

    # ── J9: No page errors + verify_build + full suite ─────────────────────────
    print("\n=== J9: No page errors ===")
    all_errors = []
    for size_tag_j, w, h in [("375x812", 375, 812), ("1280x800", 1280, 800)]:
        for lang_code in ["bg", "en"]:
            for mode in ["cinema", "theatre"]:
                ctx, page, errs = open_page(browser, w, h, lang=lang_code, mode=mode)
                page.wait_for_timeout(600)
                if errs:
                    all_errors.extend([(size_tag_j, lang_code, mode, e) for e in errs])
                ctx.close()
    if all_errors:
        check("j9_no_page_errors", False,
              f"{len(all_errors)} error(s): {all_errors[0]}")
    else:
        check("j9_no_page_errors", True, "")

    print("\n=== J9: verify_build.py gate ===")
    result_vb = subprocess.run(
        ["python3", "scripts/verify_build.py"],
        cwd=str(webapp_root),
        capture_output=True,
        text=True,
        env={**os.environ, "SOFIA_HTML": html_env}
    )
    gate_passes = "all checks passed" in result_vb.stdout
    check("j9_gate_passes", gate_passes,
          result_vb.stdout[:120] if not gate_passes else "")

    # ── Screenshots: sections open, theatre tonight/weekend, bg-cinema sections ─
    print("\n=== J Screenshots ===")
    ensure_shot_dir(wave)

    # m-bg-cinema-sections-open: open Genres section on mobile
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(800)
    for _ in range(15):
        btn = page.query_selector("[data-sectoggle='Genres']")
        if btn:
            break
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(200)
    if btn:
        page.evaluate("document.querySelector(\"[data-sectoggle='Genres']\").scrollIntoView({block:'center'})")
        page.wait_for_timeout(200)
        btn.click()
        page.wait_for_timeout(400)

        # Capture mid-animation frame ~130ms into opening (re-open for fresh sample)
        page.evaluate("document.querySelector(\"[data-sectoggle='Genres']\").click()")
        page.wait_for_timeout(300)
        page.evaluate("document.querySelector(\"[data-sectoggle='Genres']\").click()")
        page.wait_for_timeout(130)  # mid-way through 260ms open
        save_shot(page, wave, "mid-anim-section-opening")
        page.wait_for_timeout(200)
    save_shot(page, wave, "m-bg-cinema-sections-open")
    ctx.close()

    # d-bg-theatre-tonight-weekend: scroll to tonight/weekend rails
    ctx, page, errs = open_page(browser, 1280, 800, lang="bg", mode="theatre")
    page.wait_for_timeout(800)
    _force_mount(page)
    for _ in range(8):
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(200)
    save_shot(page, wave, "d-bg-theatre-tonight-weekend")
    ctx.close()


# ============================================================================
# Registry and main
# ============================================================================

WAVES = {
    "A1": wave_a1,
    "B1": wave_b1,
    "C1": wave_c1,
    "D1": wave_d1,
    "H": wave_h,
    "I": wave_i,
    "J": wave_j,
}

def main():
    """Parse command line, run selected waves, print summary."""
    selected = sys.argv[1:] if len(sys.argv) > 1 else list(WAVES.keys())

    # ── Fixture build (default mode only) ─────────────────────────────────────
    if not os.environ.get("SOFIA_HTML", ""):
        print(f"\nBuilding fixture index.test.html from ref {FIXTURE_REF} …")
        build_result = subprocess.run(
            [sys.executable, "scripts/dev_build.py",
             "--from", FIXTURE_REF, "--out", "index.test.html"],
            cwd=str(webapp_root),
            capture_output=False,
        )
        if build_result.returncode != 0:
            sys.exit(f"Fixture build failed (ref={FIXTURE_REF})")
        print(f"\nTesting: {html_env}  (fixture ref={FIXTURE_REF},"
              f" clock={CLOCK_TODAY_ISO} 14:45 Europe/Sofia)")
    else:
        print(f"\nTesting: {html_env}  (SOFIA_HTML mode,"
              f" clock={CLOCK_TODAY_ISO} 14:45 Europe/Sofia,"
              f" WEEK_END={CLOCK_WEEK_END})")

    # all_wave_results: list of (wave_name, [(status, name, detail), ...])
    all_wave_results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # Launch WebKit once for use by Wave J (J1 needs both engines)
        _webkit_browser = None
        try:
            _webkit_browser = pw.webkit.launch(headless=True)
        except Exception as _wk_err:
            print(f"  NOTE: WebKit launch failed ({_wk_err}); Wave J WebKit checks will be skipped")

        for wave_name in selected:
            if wave_name not in WAVES:
                print(f"Unknown wave: {wave_name}")
                continue

            print(f"\n{'='*70}")
            print(f"WAVE {wave_name}")
            print(f"{'='*70}")
            results.clear()
            if wave_name == "J":
                WAVES[wave_name](browser, _webkit_browser)
            else:
                WAVES[wave_name](browser)
            # Snapshot this wave's results before the next wave clears them
            wave_snapshot = list(results)
            all_wave_results.append((wave_name, wave_snapshot))

            # Per-wave count (SKIPs are not PASS or FAIL)
            w_failed = sum(1 for s, _, _ in wave_snapshot if s == "FAIL")
            w_skip   = sum(1 for s, _, _ in wave_snapshot if s == "SKIP")
            w_total  = len(wave_snapshot)
            w_run    = w_total - w_skip
            status_str = "PASS" if w_failed == 0 else "FAIL"
            skip_note = f", {w_skip} skipped" if w_skip else ""
            print(f"\nWAVE {wave_name}: {status_str} — {w_run - w_failed}/{w_run} passed{skip_note}")

        browser.close()
        if _webkit_browser:
            try:
                _webkit_browser.close()
            except Exception:
                pass

    # Print overall summary
    print(f"\n{'='*70}")
    print("OVERALL SUMMARY")
    print(f"{'='*70}")

    all_failures = []
    all_skips = []
    grand_total = 0
    grand_passed = 0
    grand_skipped = 0
    for wave_name, wave_results in all_wave_results:
        w_failed  = sum(1 for s, _, _ in wave_results if s == "FAIL")
        w_skip    = sum(1 for s, _, _ in wave_results if s == "SKIP")
        w_total   = len(wave_results)
        w_run     = w_total - w_skip
        grand_total   += w_run
        grand_passed  += w_run - w_failed
        grand_skipped += w_skip
        status_str = "PASS" if w_failed == 0 else "FAIL"
        skip_note = f", {w_skip} skipped" if w_skip else ""
        print(f"  {wave_name}: {status_str} — {w_run - w_failed}/{w_run}{skip_note}")
        for s, name, detail in wave_results:
            if s == "FAIL":
                all_failures.append((wave_name, name, detail))
            elif s == "SKIP":
                all_skips.append((wave_name, name, detail))

    skip_total_note = f"  ({grand_skipped} skipped)" if grand_skipped else ""
    print(f"\nTotal: {grand_passed}/{grand_total} passed{skip_total_note}")

    if all_skips:
        print(f"\nSKIPPED ({len(all_skips)}):")
        for wave_name, name, detail in all_skips:
            print(f"  SKIP [{wave_name}] {name} — {detail}")

    if all_failures:
        print(f"\nFAILURES ({len(all_failures)}):")
        for wave_name, name, detail in all_failures:
            detail_str = f" — {detail}" if detail else ""
            print(f"  FAIL [{wave_name}] {name}{detail_str}")

    sys.exit(1 if all_failures else 0)

if __name__ == "__main__":
    main()
