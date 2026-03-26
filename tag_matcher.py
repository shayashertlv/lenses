"""Tag-based matching engine — cascading filter + weighted scoring.

Pipeline:
  1. Filter by gender
  2. Sport isolation (hard filter — no graceful fallback)
  3. Filter by lenses.shape
  4. Filter by lenses.type
  5. Filter by lenses.color
  6. Score remaining pool on soft fields (0.0–1.0)

If a query doesn't specify a filter field, that stage is skipped.
If a filter stage (except the sport filter) would eliminate ALL remaining
products, it is relaxed (skipped) so we still return results.

Value normalisation (applied to both query AND product tags before comparison):
  Colours:
    dark-grey / dark-gray → black
    havana / havana-brown  → tortoiseshell
    gunmetal               → silver
    amber                  → brown
    grey                   → gray
    adjective-color        → color  (matte-black→black, mirrored-silver→silver, …)
  Shapes:
    butterfly → cat-eye
"""

import re

# ── Soft-scoring weights (only fields NOT used as filters) ───────────────
SCORE_WEIGHTS = {
    "frame": {
        "material": 3.0,
        "color":    3.0,
        "thickness": 1.5,
        "finish":   1.0,
        "rim_type": 2.5,
    },
    "lenses": {
        "size": 2.0,
    },
    "style": {
        "aesthetic":      2.5,
        "face_shape_fit": 1.5,
        "occasion":       1.5,
    },
}

# ── Filter stages (applied in order, with graceful fallback) ─────────────
FILTER_STAGES = [
    [("style",  "gender_target")],   # Stage 1: gender
    [("lenses", "shape")],           # Stage 2: lens shape
    [("lenses", "type")],            # Stage 3: lens type
    [("lenses", "color")],           # Stage 4: lens color
]

# ── Value normalisation ───────────────────────────────────────────────────

# Explicit full-value overrides (checked BEFORE adjective stripping)
_EXPLICIT_COLOR_MAP: dict[str, str] = {
    "dark-grey":      "black",
    "dark grey":      "black",
    "dark-gray":      "black",
    "dark gray":      "black",
    "havana-brown":   "tortoiseshell",
    "havana":         "tortoiseshell",
    "gunmetal":       "silver",
    "amber":          "brown",
    "grey":           "gray",          # British → American spelling
}

# Adjective prefixes that don't change the base colour identity
_ADJ_PREFIX = re.compile(
    r"^(?:matte[-\s]|gradient[-\s]|mirrored[-\s]|polarized[-\s]|"
    r"polished[-\s]|glossy[-\s]|rubber[-\s]|solid[-\s]|bright[-\s]|"
    r"deep[-\s]|satin[-\s]|G-15[-\s]|g-15[-\s])",
    re.IGNORECASE,
)

# Shape equivalences (maps variant → canonical)
_SHAPE_MAP: dict[str, str] = {
    "butterfly": "cat-eye",
}


def _norm_color(val: str) -> str:
    """Return the canonical colour for *val*."""
    v = val.lower().strip()
    if v in _EXPLICIT_COLOR_MAP:
        return _EXPLICIT_COLOR_MAP[v]
    stripped = _ADJ_PREFIX.sub("", v).strip()
    if stripped and stripped in _EXPLICIT_COLOR_MAP:
        return _EXPLICIT_COLOR_MAP[stripped]
    return stripped if stripped else v


def _norm_shape(val: str) -> str:
    v = val.lower().strip()
    return _SHAPE_MAP.get(v, v)


def _norm(category: str, field: str, val: str) -> str:
    """Normalise a single tag value given its context."""
    v = str(val).lower().strip()
    if field == "color":
        return _norm_color(v)
    if field == "shape":
        return _norm_shape(v)
    return v


def _to_set(value) -> set[str]:
    """Normalise a tag value (string or list) to a plain lowercase set."""
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(v).lower() for v in value}
    return {str(value).lower()}


def _to_set_norm(category: str, field: str, value) -> set[str]:
    """Like _to_set but applies canonical normalisation."""
    if value is None:
        return set()
    if isinstance(value, list):
        return {_norm(category, field, str(v)) for v in value}
    return {_norm(category, field, str(value))}


def _field_matches_norm(category: str, field: str,
                        query_val, product_val) -> bool:
    """Return True if product has ANY overlap with the query (after normalisation)."""
    q = _to_set_norm(category, field, query_val)
    p = _to_set_norm(category, field, product_val)
    if not q:
        return True   # query doesn't specify → automatic pass
    return bool(q & p)


def _apply_filter_stage(products: list[dict], stage: list[tuple[str, str]],
                        query_tags: dict) -> list[dict]:
    """
    Apply one filter stage.  A product passes if it matches ALL fields
    in the stage that the query specifies.

    If the query doesn't specify any field in this stage → pool unchanged.
    If filtering would empty the pool → pool unchanged (graceful fallback).
    """
    active_fields = []
    for category, field in stage:
        q_val = query_tags.get(category, {}).get(field)
        if q_val is not None and q_val != "" and q_val != []:
            active_fields.append((category, field))

    if not active_fields:
        return products

    filtered = []
    for product in products:
        ptags = product["tags"]
        passes = True
        for category, field in active_fields:
            q_val = query_tags[category][field]
            p_val = ptags.get(category, {}).get(field)
            if not _field_matches_norm(category, field, q_val, p_val):
                passes = False
                break
        if passes:
            filtered.append(product)

    if not filtered:
        return products   # would empty pool → skip this stage

    return filtered


def compute_tag_score(query_tags: dict, product_tags: dict) -> float:
    """
    Score a product on soft fields only (0.0–1.0).

    Filter fields (gender, shapes, lens type, lens color) are NOT scored
    here — they are handled by the filter pipeline.  This scores how well
    the product matches on material, frame color, rim type, aesthetic, etc.
    """
    total_weight   = 0.0
    weighted_score = 0.0

    for category, fields in SCORE_WEIGHTS.items():
        q_cat = query_tags.get(category, {})
        p_cat = product_tags.get(category, {})

        for field, weight in fields.items():
            q_val = q_cat.get(field)
            if q_val is None or q_val == "" or q_val == []:
                continue

            total_weight += weight

            p_val = p_cat.get(field)
            if p_val is None:
                continue

            q_set = _to_set_norm(category, field, q_val)
            p_set = _to_set_norm(category, field, p_val)
            if not q_set:
                continue

            overlap = len(q_set & p_set)
            weighted_score += weight * (overlap / len(q_set))

    if total_weight == 0.0:
        return 1.0   # no soft fields → all filtered products equal

    return weighted_score / total_weight


def rank_products(query_tags: dict, products: list[dict],
                  top_k: int = 3, min_score: float = 0.0,
                  filters: dict | None = None) -> list[tuple[dict, float]]:
    """
    Filter then score products.

    Hard filters (in_stock, max_price, sport isolation) are applied first,
    then the cascading tag filter pipeline, then soft scoring.

    Args:
        query_tags: Structured query tags (frame/lenses/style dicts).
        products:   List of product dicts from catalog.
        top_k:      Max results to return.
        min_score:  Minimum score threshold.
        filters:    Business filters: {"in_stock_only", "max_price", "gender"}.

    Returns:
        List of (product, score) sorted by score descending.
    """
    # ── Pre-filter: business rules (stock, price) ────────────────────────
    pool: list[dict] = []
    for product in products:
        ptags    = product["tags"]
        prod_meta = ptags.get("product", {})

        if filters:
            if filters.get("in_stock_only") and not prod_meta.get("in_stock", False):
                continue
            if filters.get("max_price") is not None:
                if prod_meta.get("price", 0) > filters["max_price"]:
                    continue
            if filters.get("gender") and not query_tags.get("style", {}).get("gender_target"):
                query_tags.setdefault("style", {})["gender_target"] = filters["gender"]

        pool.append(product)

    # ── Sport isolation (HARD filter — no graceful fallback) ─────────────
    # Products tagged 'sport' are shown ONLY when the user explicitly
    # requests sport.  If the user didn't request sport, they are excluded
    # entirely so lifestyle frames aren't polluted with sport frames.
    q_lens_type_raw  = query_tags.get("lenses", {}).get("type")
    q_lens_type_set  = _to_set(q_lens_type_raw)
    user_wants_sport = "sport" in q_lens_type_set

    sport_pool     = [p for p in pool if "sport" in _to_set(p["tags"].get("lenses", {}).get("type"))]
    non_sport_pool = [p for p in pool if "sport" not in _to_set(p["tags"].get("lenses", {}).get("type"))]

    pool = sport_pool if user_wants_sport else non_sport_pool
    # Intentionally NO fallback: sport products must stay isolated.

    # ── Cascading tag filters (with graceful fallback) ────────────────────
    for stage in FILTER_STAGES:
        pool = _apply_filter_stage(pool, stage, query_tags)

    # ── Soft scoring ─────────────────────────────────────────────────────
    scored: list[tuple[dict, float]] = []
    for product in pool:
        score = compute_tag_score(query_tags, product["tags"])
        if score >= min_score:
            scored.append((product, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def preferences_to_query_tags(prefs: dict) -> dict:
    """
    Convert UI preference selections (flat keys) into structured query tags.

    Maps Free Search / Smart Fit UI fields to the nested tag structure
    used by the filter + score pipeline.
    """
    tags: dict = {"frame": {}, "lenses": {}, "style": {}}

    if prefs.get("frame_shape"):
        tags["lenses"]["shape"] = prefs["frame_shape"]
    if prefs.get("frame_color"):
        tags["frame"]["color"] = prefs["frame_color"]
    if prefs.get("frame_material"):
        tags["frame"]["material"] = prefs["frame_material"]
    if prefs.get("frame_thickness"):
        tags["frame"]["thickness"] = prefs["frame_thickness"]
    if prefs.get("rim_type"):
        tags["frame"]["rim_type"] = prefs["rim_type"]

    if prefs.get("lens_type"):
        tags["lenses"]["type"] = prefs["lens_type"]
    if prefs.get("lens_color"):
        tags["lenses"]["color"] = prefs["lens_color"]
    if prefs.get("lens_size"):
        tags["lenses"]["size"] = prefs["lens_size"]

    if prefs.get("aesthetic"):
        tags["style"]["aesthetic"] = prefs["aesthetic"]
    if prefs.get("gender"):
        tags["style"]["gender_target"] = prefs["gender"]
    if prefs.get("occasion"):
        tags["style"]["occasion"] = prefs["occasion"]

    return tags
