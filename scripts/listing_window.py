#!/usr/bin/env python3
"""Sofia Gleda — the listing window, decided in ONE place.

Every listing the app shows lies in SNAPSHOT.window: from TODAY in Sofia to
today + HORIZON_DAYS. Every script that reads or writes listings asks this
module, so the window rolls forward on every run instead of freezing (the
2026-10-09 audit, CODE-01: window.to stood at 2026-12-14 and nothing moved it,
so the horizon shrank by a day a day and the site would have gone empty on
15 Dec). "Today" is always Europe/Sofia, never the runner's clock (a GitHub
runner is on UTC: between 21:00 and 24:00 UTC its date is Sofia's yesterday).

The horizon is the one the app was built with: on 2026-10-09 the window ran
09 Oct .. 14 Dec, i.e. to = today + 66 days, 67 listing days.

    import listing_window as LW
    lo, hi = LW.listing_window()          # ("2026-10-09", "2026-12-14")
    days = LW.window_days()               # every ISO date lo..hi
    LW.generated_stamp()                  # "2026-10-09T18:40:12+03:00"
"""
from __future__ import annotations

import datetime as dt

TZ_NAME = "Europe/Sofia"
HORIZON_DAYS = 66          # window.to = today + 66 days -> 67 listing days

try:
    from zoneinfo import ZoneInfo
except ImportError:                                    # pragma: no cover
    ZoneInfo = None


def sofia_tz():
    """Europe/Sofia, or None when the tz database is missing."""
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(TZ_NAME)
    except Exception:
        return None


def _last_sunday(year, month):
    d = dt.date(year, month, 31)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def _eu_offset_hours(utc):
    """Sofia's UTC offset by the EU rule (EEST, +3, from 01:00 UTC on the last
    Sunday of March to 01:00 UTC on the last Sunday of October; else EET, +2).
    Only used when the tz database is missing."""
    y = utc.year
    start = dt.datetime.combine(_last_sunday(y, 3), dt.time(1), dt.timezone.utc)
    end = dt.datetime.combine(_last_sunday(y, 10), dt.time(1), dt.timezone.utc)
    return 3 if start <= utc < end else 2


def sofia_now(utc_now=None):
    """The current (or the given UTC) moment as an aware Sofia date-time."""
    utc = utc_now or dt.datetime.now(dt.timezone.utc)
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=dt.timezone.utc)
    tz = sofia_tz()
    if tz is not None:
        return utc.astimezone(tz)
    return utc.astimezone(dt.timezone(dt.timedelta(hours=_eu_offset_hours(utc))))


def sofia_today(utc_now=None):
    return sofia_now(utc_now).date()


def _as_date(today):
    if today is None:
        return sofia_today()
    if isinstance(today, str):
        return dt.date.fromisoformat(today)
    if isinstance(today, dt.datetime):
        return today.date()
    return today


def listing_window(today=None, horizon=HORIZON_DAYS):
    """(from, to) as ISO dates: today (Europe/Sofia) .. today + horizon."""
    d0 = _as_date(today)
    return d0.isoformat(), (d0 + dt.timedelta(days=horizon)).isoformat()


def window_days(today=None, horizon=HORIZON_DAYS):
    """Every ISO date of the listing window, in order (a list of horizon+1)."""
    d0 = _as_date(today)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(horizon + 1)]


def generated_stamp(utc_now=None):
    """SNAPSHOT.generated: this moment in Sofia, ISO 8601 with its offset."""
    return sofia_now(utc_now).replace(microsecond=0).isoformat()
