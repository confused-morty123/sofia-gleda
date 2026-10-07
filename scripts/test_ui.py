#!/usr/bin/env python3
"""Sofia Gleda UI regression suite for Wave A1 and beyond.

Usage:
  python3 scripts/test_ui.py           # run all waves against index.dev.html
  python3 scripts/test_ui.py A1        # run wave A1 only
  SOFIA_HTML=index.html python3 scripts/test_ui.py  # use custom HTML file

Playwright sync API, headless Chromium, timezone Europe/Sofia, locale bg-BG.
Fixed clock 2026-10-07 14:45 Europe/Sofia for reproducible testing.
Screenshots go to /tmp/sg-shots/<wave>/<name>.png (viewport only).
"""
import sys
import os
import pathlib
from pathlib import Path
from playwright.sync_api import sync_playwright

# Constants matching qa_cinema.py
FIXED_1445 = 1791373500000  # 2026-10-07 14:45 Europe/Sofia
CLOCK = """
(() => { const _D = Date, F = %d;
  class FakeDate extends _D { constructor(...a){ a.length? super(...a): super(F); } static now(){ return F; } }
  window.Date = FakeDate; })();
""" % FIXED_1445

# Determine HTML file: env SOFIA_HTML or index.dev.html, resolved relative to webapp root
html_env = os.environ.get("SOFIA_HTML", "index.dev.html")
webapp_root = pathlib.Path(__file__).parent.parent.resolve()
html_path = webapp_root / html_env
HTML = html_path.as_uri()

# Results tracking
results = []

def check(name, ok, detail=""):
    """Record a check result."""
    status = "PASS" if ok else "FAIL"
    results.append((status, name, detail))
    print(f"{status} {name} — {detail}")

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
    selectors = [".brand", ".seg", ".seg button", "[data-lang]", ".burger", "[data-search-open]", "#q"]
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
    burger_box = box(page, ".burger")
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

    # Seg, lang, search, burger on one row (within 4px center_y)
    controls = []
    if seg_box:
        controls.append(("seg", seg_box["y"] + seg_box["height"]/2))
    if lang_box:
        controls.append(("lang", lang_box["y"] + lang_box["height"]/2))
    if search_box:
        controls.append(("search", search_box["y"] + search_box["height"]/2))
    if burger_box:
        controls.append(("burger", burger_box["y"] + burger_box["height"]/2))

    if len(controls) >= 2:
        centres = [c[1] for c in controls]
        max_spread = max(centres) - min(centres)
        aligned = max_spread <= 4
        check("controls_aligned", aligned,
              f"spread={max_spread:.1f}px" if not aligned else "")

        # All inside 0..375
        inside = all(c["x"] >= 0 and c["x"] + c["width"] <= 375
                     for c in [seg_box, lang_box, search_box, burger_box] if c)
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

    # Check all header elements fit
    header_elems = [".brand", ".seg", "[data-lang]", ".burger", "[data-search-open]"]
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
    check("desktop_q_visible", q_box is not None, "")

    search_open_box = box(page, "[data-search-open]")
    check("desktop_no_search_open", search_open_box is None, "")

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

    sticky_info = page.evaluate("""() => {
        const seg = document.querySelector('.seg');
        const brand = document.querySelector('.brand');
        const segBB = seg ? seg.getBoundingClientRect() : null;
        const brandBB = brand ? brand.getBoundingClientRect() : null;
        return {
            segTop: segBB ? segBB.top : null,
            brandBottom: brandBB ? brandBB.bottom : null
        };
    }""")

    seg_top = sticky_info["segTop"]
    brand_bottom = sticky_info["brandBottom"]

    seg_ok = seg_top is not None and 4 <= seg_top <= 12
    brand_ok = brand_bottom is not None and brand_bottom <= 0

    sticky_works = seg_ok and brand_ok
    check("sticky_visible", sticky_works,
          f"segTop={seg_top}, brandBottom={brand_bottom} (need segTop in [4,12] and brandBottom<=0)"
          if not sticky_works else f"segTop={seg_top:.1f}, brandBottom={brand_bottom:.1f}")

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

    # [data-search-open] should not be visible on desktop
    search_open_final_elem = page.query_selector("[data-search-open]")
    desktop_final_no_search = search_open_final_elem is None or not search_open_final_elem.is_visible()
    check("desktop_final_no_search_open", desktop_final_no_search,
          f"[data-search-open] visible={search_open_final_elem is not None and search_open_final_elem.is_visible()}")

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
        env={**os.environ, "SOFIA_HTML": "index.dev.html"}
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
    week_info = page.evaluate("""() => ({
        WEEK_END: typeof WEEK_END !== 'undefined' ? WEEK_END : null,
        day: typeof S !== 'undefined' ? S.day : null,
        inRange_11: typeof inRange !== 'undefined' ? inRange("2026-10-11") : null,
        inRange_12: typeof inRange !== 'undefined' ? inRange("2026-10-12") : null
    })""")
    check("b1_week_end", week_info["WEEK_END"] == "2026-10-11",
          f"WEEK_END={week_info['WEEK_END']}")
    check("b1_day_week", week_info["day"] == "week",
          f"S.day={week_info['day']}")
    check("b1_inRange_sunday", week_info["inRange_11"] is True,
          f"inRange('2026-10-11')={week_info['inRange_11']}")
    check("b1_inRange_monday_out", week_info["inRange_12"] is False,
          f"inRange('2026-10-12')={week_info['inRange_12']}")
    ctx.close()

    # ── Check B1-4: Period banner ──
    print("\n=== B1-4: Period banner ===")
    # BG, default week
    ctx, page, errs = open_page(browser, 375, 812, lang="bg", mode="cinema")
    page.wait_for_timeout(500)
    banner_info = page.evaluate("""() => {
        const b = document.querySelector('.pbanner');
        if (!b) return {exists:false};
        const mainEl = document.querySelector('main');
        const firstChild = mainEl ? mainEl.firstElementChild : null;
        const isAboveHero = firstChild && firstChild.classList.contains('pbanner');
        const text = b.textContent;
        const bb = b.getBoundingClientRect();
        return {exists:true, text, isAboveHero, top:bb.top, mainTop: mainEl ? mainEl.getBoundingClientRect().top : null};
    }""")
    check("b1_banner_exists", banner_info.get("exists") is True, ".pbanner not found")
    if banner_info.get("exists"):
        check("b1_banner_above_hero", banner_info.get("isAboveHero") is True,
              "pbanner is not first child of main" if not banner_info.get("isAboveHero") else "")
        banner_text = banner_info.get("text", "")
        check("b1_banner_bg_tazi_sedmitsa",
              "тази седмица" in banner_text.lower(),
              f"text snippet: {banner_text[:80]!r}")
        check("b1_banner_bg_11", "11" in banner_text,
              f"'11' not in banner text: {banner_text[:80]!r}")
    else:
        check("b1_banner_above_hero", False, "banner not found")
        check("b1_banner_bg_tazi_sedmitsa", False, "banner not found")
        check("b1_banner_bg_11", False, "banner not found")

    # Open drawer via the banner change button
    change_btn = page.query_selector(".pbanner-btn[data-drawer]")
    if change_btn:
        change_btn.click()
        page.wait_for_timeout(400)
        drawer_visible = page.evaluate("!!document.querySelector('.drawer')")
        check("b1_banner_btn_opens_drawer", drawer_visible, "drawer not opened")

        # Choose today using JS to avoid calendar overlay blocking click
        today_set = page.evaluate("""() => {
            const btn = document.querySelector(".drawer [data-range='2026-10-07']");
            if (!btn) return false;
            btn.click();
            return true;
        }""")
        page.wait_for_timeout(400)
        if today_set:
            banner_today_text = page.evaluate("""() => {
                const b = document.querySelector('.pbanner');
                return b ? b.textContent : '';
            }""")
            check("b1_banner_today_bg",
                  "днес" in banner_today_text.lower(),
                  f"'днес' not found in: {banner_today_text[:80]!r}")
        else:
            check("b1_banner_today_bg", False, "no [data-range='2026-10-07'] in drawer")

        # Open drawer again via JS click on the banner button, then choose month via JS
        reopen_and_month = page.evaluate("""() => {
            const btn = document.querySelector('.pbanner-btn[data-drawer]');
            if (!btn) return 'no-pbanner-btn';
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
                banner_month_text = page.evaluate("""() => {
                    const b = document.querySelector('.pbanner');
                    return b ? b.textContent : '';
                }""")
                check("b1_banner_month_bg",
                      "месец" in banner_month_text.lower(),
                      f"'месец' not found in: {banner_month_text[:80]!r}")
            else:
                check("b1_banner_month_bg", False, "no [data-range='month'] in drawer")
        else:
            check("b1_banner_month_bg", False, f"could not re-open drawer: {reopen_and_month}")
    else:
        check("b1_banner_btn_opens_drawer", False, "no .pbanner-btn[data-drawer]")
        check("b1_banner_today_bg", False, "skipped")
        check("b1_banner_month_bg", False, "skipped")
    ctx.close()

    # EN version: "this week" + "11"
    ctx, page, errs = open_page(browser, 375, 812, lang="en", mode="cinema")
    page.wait_for_timeout(500)
    banner_en = page.evaluate("""() => {
        const b = document.querySelector('.pbanner');
        return b ? b.textContent : '';
    }""")
    check("b1_banner_en_this_week",
          "this week" in banner_en.lower(),
          f"'this week' not found: {banner_en[:80]!r}")
    check("b1_banner_en_11", "11" in banner_en,
          f"'11' not in EN banner: {banner_en[:80]!r}")
    save_shot(page, wave, "m-en-cinema-top")
    ctx.close()

    # EN: today + month labels
    ctx, page, errs = open_page(browser, 1280, 800, lang="en", mode="cinema")
    page.wait_for_timeout(500)
    # Open drawer via JS to avoid overlay intercept
    open_drawer_en = page.evaluate("""() => {
        const btn = document.querySelector('.pbanner-btn[data-drawer]');
        if (!btn) return false;
        btn.click();
        return true;
    }""")
    page.wait_for_timeout(400)
    if open_drawer_en:
        today_set_en = page.evaluate("""() => {
            const btn = document.querySelector(".drawer [data-range='2026-10-07']");
            if (!btn) return false;
            btn.click();
            return true;
        }""")
        page.wait_for_timeout(400)
        if today_set_en:
            banner_today_en = page.evaluate("document.querySelector('.pbanner')?.textContent || ''")
            check("b1_banner_today_en", "today" in banner_today_en.lower(),
                  f"'today' not in: {banner_today_en[:80]!r}")
        else:
            check("b1_banner_today_en", False, "no today button in EN drawer")
        # Re-open drawer for month
        reopen_en = page.evaluate("""() => {
            const btn = document.querySelector('.pbanner-btn[data-drawer]');
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
                banner_month_en = page.evaluate("document.querySelector('.pbanner')?.textContent || ''")
                check("b1_banner_month_en", "month" in banner_month_en.lower(),
                      f"'month' not in: {banner_month_en[:80]!r}")
                save_shot(page, wave, "d-en-period-month")
            else:
                check("b1_banner_month_en", False, "no month button in EN drawer")
        else:
            check("b1_banner_month_en", False, "could not re-open EN drawer for month")
    else:
        check("b1_banner_today_en", False, "no .pbanner-btn in EN")
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

    # Check genre toggle aria-expanded="true" comes before venue toggle aria-expanded="false"
    sections_info = page.evaluate("""() => {
        const toggles = Array.from(document.querySelectorAll('[data-sectoggle]'));
        return toggles.map(t => ({key:t.dataset.sectoggle, expanded:t.getAttribute('aria-expanded')}));
    }""")
    genre_toggle = next((t for t in sections_info if t["key"] == "Genres"), None)
    venue_toggle = next((t for t in sections_info if t["key"] == "Venues"), None)
    check("b1_genre_toggle_open", genre_toggle is not None and genre_toggle["expanded"] == "true",
          f"genre toggle: {genre_toggle}")
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

        # Collapse genre section → its rails disappear
        genre_toggle_btn = page.query_selector("[data-sectoggle='Genres']")
        if genre_toggle_btn:
            genre_toggle_btn.click()
            page.wait_for_timeout(600)
            genre_content_hidden = page.evaluate("""() => {
                const c = document.getElementById('sec-Genres-content');
                return !c || c.hidden || window.getComputedStyle(c).display === 'none';
            }""")
            check("b1_genre_collapse_hides_rails", genre_content_hidden,
                  "sec-Genres-content still visible after collapse")
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
        env={**os.environ, "SOFIA_HTML": "index.dev.html"}
    )
    gate_passes = "all checks passed" in result.stdout
    check("b1_gate_passes", gate_passes,
          result.stdout[:120] if not gate_passes else "")


# ============================================================================
# Registry and main
# ============================================================================

WAVES = {
    "A1": wave_a1,
    "B1": wave_b1,
}

def main():
    """Parse command line, run selected waves, print summary."""
    selected = sys.argv[1:] if len(sys.argv) > 1 else list(WAVES.keys())

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        for wave_name in selected:
            if wave_name not in WAVES:
                print(f"Unknown wave: {wave_name}")
                continue

            print(f"\n{'='*70}")
            print(f"WAVE {wave_name}")
            print(f"{'='*70}")
            results.clear()
            WAVES[wave_name](browser)

        browser.close()

    # Print summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")

    for status, name, detail in results:
        detail_str = f" — {detail}" if detail else ""
        print(f"{status} {name}{detail_str}")

    failed = sum(1 for s, _, _ in results if s == "FAIL")
    total = len(results)
    print(f"\n{total - failed}/{total} passed")

    sys.exit(1 if failed > 0 else 0)

if __name__ == "__main__":
    main()
