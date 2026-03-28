"""
HTML templates for the landing page, free search page, lens recolor page,
and storefront demo page.

Templates are loaded from ui/templates/*.html files on import.
"""

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"

LANDING_HTML = (_TEMPLATES_DIR / "landing.html").read_text(encoding="utf-8")
FREE_SEARCH_HTML = (_TEMPLATES_DIR / "free-search.html").read_text(encoding="utf-8")
LENS_RECOLOR_HTML = (_TEMPLATES_DIR / "lens-recolor.html").read_text(encoding="utf-8")
STOREFRONT_HTML = (_TEMPLATES_DIR / "storefront.html").read_text(encoding="utf-8")
