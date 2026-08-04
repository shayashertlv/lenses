"""
HTML templates for the landing page, free search page, lens recolor page,
and storefront demo page.

Templates are read from UI/templates/*.html on demand and cached, so editing a
template is visible on the next request without restarting the server. Call
get_template(); the module-level constants are kept for backwards compatibility
but bind a snapshot at import time and do not pick up edits.

Invalidation is deliberately paranoid, because the obvious version of it is
wrong. Filesystem timestamps advance in ticks rather than continuously —
measured on this machine at up to 16ms between distinct values — so two writes
inside one tick are stamped identically and a cache keyed on mtime alone cannot
tell them apart. It keeps serving the first one, and the promise in the
paragraph above quietly stops being true until the next edit. Size catches
nearly every such pair; for the remainder, a file whose timestamp is younger
than one tick is re-read rather than trusted, since that timestamp has not
settled yet and may still be reused by a subsequent write.
"""

import time
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"

TEMPLATE_FILES = {
    "landing": "landing.html",
    "free-search": "free-search.html",
    "lens-recolor": "lens-recolor.html",
    "storefront": "storefront.html",
}

# name -> ((mtime_ns, size), text)
_cache: dict[str, tuple[tuple[int, int], str]] = {}

# One filesystem tick, with room to spare. Below this age a template's mtime is
# still a value the next write could be stamped with, so it proves nothing.
_MTIME_SETTLE_NS = 50_000_000   # 50ms


def get_template(name: str) -> str:
    """Return a template's HTML, re-reading it whenever the file has changed."""
    try:
        filename = TEMPLATE_FILES[name]
    except KeyError:
        raise KeyError(f"unknown template {name!r}; expected one of {sorted(TEMPLATE_FILES)}") from None

    path = _TEMPLATES_DIR / filename
    st = path.stat()
    signature = (st.st_mtime_ns, st.st_size)
    # A negative age means an mtime in the future — a copied file or a skewed
    # clock, not a write that just happened — so cache that normally.
    age_ns = time.time_ns() - st.st_mtime_ns
    unsettled = 0 <= age_ns < _MTIME_SETTLE_NS

    cached = _cache.get(name)
    if cached is None or cached[0] != signature or unsettled:
        _cache[name] = (signature, path.read_text(encoding="utf-8"))
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
