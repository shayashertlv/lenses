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

import hashlib
import re
import time
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_UI_DIR = Path(__file__).parent

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

# Only unversioned refs; a URL that already carries a query is left alone.
_ASSET_REF = re.compile(r'\b(href|src)="(/static/[^"?#]+)"')


# url_path -> ((mtime_ns, size), digest)
_asset_versions: dict[str, tuple[tuple[int, int], str]] = {}


def _asset_version(url_path: str) -> str | None:
    """A stamp over the file's bytes.

    Content, not mtime: a redeploy rewrites every mtime, and stamping those
    would bust every cache on every ship whether or not anything changed. The
    hash is memoised against stat, so it is recomputed only when the file
    could have moved — with the same settle rule the template cache uses,
    since two writes inside one filesystem tick share a stat signature.
    """
    path = _UI_DIR / url_path.lstrip("/")
    try:
        st = path.stat()
    except OSError:
        return None
    signature = (st.st_mtime_ns, st.st_size)
    age_ns = time.time_ns() - st.st_mtime_ns
    unsettled = 0 <= age_ns < _MTIME_SETTLE_NS

    hit = _asset_versions.get(url_path)
    if hit is not None and hit[0] == signature and not unsettled:
        return hit[1]
    try:
        digest = hashlib.blake2b(path.read_bytes(), digest_size=6).hexdigest()
    except OSError:
        return None
    _asset_versions[url_path] = (signature, digest)
    return digest


def version_assets(html: str) -> str:
    """Stamp every /static/ reference with its file's version.

    A deploy that changes a stylesheet but not the page that loads it leaves
    every browser holding the old stylesheet, and the page looks unchanged no
    matter how many times it is shipped. Cache-Control alone did not settle it
    — the bytes were right on the server and wrong in the browser, twice. A
    changed URL cannot be answered from any cache, browser or edge, so the
    stamp goes in the URL.

    Applied per request rather than baked into the cached template: the
    template's own mtime does not move when a stylesheet changes, so a stamp
    cached with the HTML would be exactly as stale as the problem it solves.
    Costs one stat() per reference.
    """
    def stamp(m: "re.Match[str]") -> str:
        version = _asset_version(m.group(2))
        return f'{m.group(1)}="{m.group(2)}?v={version}"' if version else m.group(0)

    return _ASSET_REF.sub(stamp, html)


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
    return version_assets(_cache[name][1])


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
