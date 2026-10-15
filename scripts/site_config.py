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
