"""Sofia Gleda — single source for the public site URL.

Change this one constant when the domain moves to sofiagleda.com.

Within scripts/ import as:
    from site_config import SITE_URL

From the repo root:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent / "scripts"))
    from site_config import SITE_URL
"""
SITE_URL = "https://confused-morty123.github.io/sofia-gleda"
