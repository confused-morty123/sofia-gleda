"""Sofia Gleda: the single source for the public site URL (sofiagleda.com since
15 Oct 2026; GitHub redirects the old github.io address here).

Within scripts/ import as:
    from site_config import SITE_URL

From the repo root:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent / "scripts"))
    from site_config import SITE_URL
"""
SITE_URL = "https://sofiagleda.com"

# ------------------------------------------------------------------ Google Analytics 4
# The GA4 web stream's Measurement ID ("G-" and letters/digits, from Google Analytics:
# Admin > Data streams > the site's stream; the owner's, given 2026-10-29). Empty =
# analytics off: the page carries no analytics script, the cookie notice has no
# "Статистика" switch and the policies keep saying the site keeps no visitor statistics. Set it, then rebuild the pages (README:
# "Visitor statistics"): build_index.py (via dev_build.py), gen_share_pages.py,
# gen_legal_pages.py, csp.py, verify_build.py. verify_build.py fails while the built page,
# the legal pages and this value disagree.
GA_MEASUREMENT_ID = "G-SQWMGX9GL3"
# The date the policies' analytics text was written. While analytics is on, the cookie and
# privacy policies show it (or the app's CONSENT_VERSION, if later) as their date, and the
# cookie notice asks every visitor again. Set it to the day you switch analytics on.
GA_POLICY_DATE = "2026-10-29"

import os as _os
import re as _re

GA_ID_RE = _re.compile(r"G-[A-Z0-9]{4,16}")


def ga_measurement_id():
    """The Measurement ID in force: $SOFIA_GA_MEASUREMENT_ID when it is set (a test build;
    never committed), else GA_MEASUREMENT_ID. "" means analytics is off."""
    value = _os.environ.get("SOFIA_GA_MEASUREMENT_ID", GA_MEASUREMENT_ID).strip()
    if value and not GA_ID_RE.fullmatch(value):
        raise ValueError(f"GA Measurement ID {value!r} is not of the form G-XXXXXXXXXX")
    return value


def consent_version(app_version):
    """The policies' version (and the cookie notice's record version): the app's
    CONSENT_VERSION, or GA_POLICY_DATE while analytics is on and that date is later."""
    return max(app_version, GA_POLICY_DATE) if ga_measurement_id() else app_version
