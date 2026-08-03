"""
HTML templates for the landing page, free search page, lens recolor page,
and storefront demo page.

Templates are read from UI/templates/*.html on demand and cached by mtime, so
editing a template is visible on the next request without restarting the server.
Call get_template(); the module-level constants are kept for backwards
compatibility but bind a snapshot at import time and do not pick up edits.
"""

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"

TEMPLATE_FILES = {
    "landing": "landing.html",
    "free-search": "free-search.html",
    "lens-recolor": "lens-recolor.html",
    "storefront": "storefront.html",
}

# name -> (mtime_ns, text)
_cache: dict[str, tuple[int, str]] = {}


def get_template(name: str) -> str:
    """Return a template's HTML, re-reading it whenever the file has changed."""
    try:
        filename = TEMPLATE_FILES[name]
    except KeyError:
        raise KeyError(f"unknown template {name!r}; expected one of {sorted(TEMPLATE_FILES)}") from None

    path = _TEMPLATES_DIR / filename
    mtime = path.stat().st_mtime_ns
    cached = _cache.get(name)
    if cached is None or cached[0] != mtime:
        _cache[name] = (mtime, path.read_text(encoding="utf-8"))
    return _cache[name][1]


_LEGACY_CONSTANTS = {
    "LANDING_HTML": "landing",
    "FREE_SEARCH_HTML": "free-search",
    "LENS_RECOLOR_HTML": "lens-recolor",
    "STOREFRONT_HTML": "storefront",
}


def __getattr__(attr: str) -> str:
    """Serve the legacy module-level constants lazily (PEP 562)."""
    try:
        return get_template(_LEGACY_CONSTANTS[attr])
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {attr!r}") from None
