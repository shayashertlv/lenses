"""Tag-based matching engine — cascading filter + weighted scoring.

Pipeline:
  1. Business pre-filters (in_stock, max_price)
  2. Sport isolation (hard filter — no graceful fallback)
  3. Cascading tag filters with graceful fallback:
     a. gender
     b. lenses.shape
     c. lenses.type
     d. lenses.color
  4. Soft scoring on remaining pool (0.0–1.0)
  5. Minimum score gate (default 0.15)

If a query doesn't specify a filter field, that stage is skipped.
If a filter stage (except the sport filter) would eliminate ALL remaining
products, it is relaxed (skipped) so we still return results.

Value normalisation (applied to both query AND product tags before comparison):
  Colours:    70+ aliases mapped to canonical names
              (e.g. navy→blue, charcoal→gray, havana→tortoiseshell)
              Adjective prefixes stripped (matte-black→black, etc.)
  Shapes:     variant→canonical (butterfly→cat-eye, pilot→aviator, etc.)
  Materials:  family grouping (stainless-steel→metal, mixed-metal-*→metal,
              recycled-acetate→acetate, bio-nylon→nylon, propionate→plastic, etc.)

Scoring details:
  - Missing product tags receive partial credit (0.25) rather than zero.
  - compute_component_scores() provides per-dimension sub-scores
    (fit, style, color) for UI display.
  - query_tags are never mutated (defensive deep-copy).
"""

import copy
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
    # Dark variants
    "dark-grey":      "black",
    "dark grey":      "black",
    "dark-gray":      "black",
    "dark gray":      "black",
    "dark-black":     "black",
    "jet-black":      "black",
    "jet black":      "black",
    # Brown family
    "havana-brown":   "tortoiseshell",
    "havana":         "tortoiseshell",
    "amber":          "brown",
    "cognac":         "brown",
    "caramel":        "brown",
    "chocolate":      "brown",
    "espresso":       "brown",
    "tan":            "brown",
    "beige":          "brown",
    "mocha":          "brown",
    "honey":          "brown",
    "chestnut":       "brown",
    "walnut":         "brown",
    "bronze":         "brown",
    "copper":         "brown",
    # Silver / gray family
    "gunmetal":       "silver",
    "grey":           "gray",          # British → American spelling
    "charcoal":       "gray",
    "slate":          "gray",
    "pewter":         "silver",
    "chrome":         "silver",
    "steel":          "silver",
    "light-gray":     "gray",
    "light-grey":     "gray",
    "light gray":     "gray",
    "light grey":     "gray",
    "dark-silver":    "silver",
    # Gold family
    "champagne":      "gold",
    "brass":          "gold",
    "antique-gold":   "gold",
    "light-gold":     "gold",
    # Blue family
    "navy":           "blue",
    "navy-blue":      "blue",
    "navy blue":      "blue",
    "dark-blue":      "blue",
    "dark blue":      "blue",
    "cobalt":         "blue",
    "teal":           "blue",
    "royal-blue":     "blue",
    "sky-blue":       "blue",
    # Green family
    "olive":          "green",
    "dark-green":     "green",
    "dark green":     "green",
    "forest-green":   "green",
    "emerald":        "green",
    "sage":           "green",
    "khaki":          "green",
    # Red / pink family
    "burgundy":       "red",
    "wine":           "red",
    "maroon":         "red",
    "dark-red":       "red",
    "crimson":        "red",
    "coral":          "pink",
    "salmon":         "pink",
    "blush":          "pink",
    "fuchsia":        "pink",
    "magenta":        "pink",
    # Other
    "ivory":          "white",
    "cream":          "white",
    "bone":           "white",
    "off-white":      "white",
    "crystal":        "transparent",
    "clear":          "transparent",
    "nude":           "transparent",
    "violet":         "purple",
    "lavender":       "purple",
    "plum":           "purple",
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
    "butterfly":        "cat-eye",
    "oversized-round":  "round",
    "oversized-square": "square",
    "pilot":            "aviator",
    "teardrop":         "aviator",
    "flat-top":         "square",
    "curved-wrap":      "wrap",
    "shield":           "wrap",
}

# Material family mapping (variant → canonical parent)
_MATERIAL_MAP: dict[str, str] = {
    # Metal family
    "stainless-steel":      "metal",
    "mixed-metal":          "metal",
    "mixed-metal-acetate":  "metal",
    "mixed-metal-plastic":  "metal",
    "mixed-metal-nylon":    "metal",
    "mixed-metal-carbon":   "metal",
    "mixed-metal-injected": "metal",
    # Plastic / acetate family
    "propionate":           "plastic",
    "bio-injected":         "plastic",
    "recycled-injected":    "plastic",
    "o-matter":             "plastic",
    "recycled-acetate":     "acetate",
    # Nylon family
    "bio-nylon":            "nylon",
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


def _norm_material(val: str) -> str:
    v = val.lower().strip()
    return _MATERIAL_MAP.get(v, v)


def _norm(category: str, field: str, val: str) -> str:
    """Normalise a single tag value given its context."""
    v = str(val).lower().strip()
    if field == "color":
        return _norm_color(v)
    if field == "shape":
        return _norm_shape(v)
    if field == "material":
        return _norm_material(v)
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


def _apply_filter_stage(
    products: list[dict],
    stage: list[tuple[str, str]],
    query_tags: dict,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Apply one filter stage.  A product passes if it matches ALL fields
    in the stage that the query specifies.

    If the query doesn't specify any field in this stage → pool unchanged.
    If filtering would empty the pool → pool unchanged (graceful fallback).

    Returns:
        (filtered_products, relaxed_fields) — relaxed_fields lists
        (category, field) pairs that were skipped due to graceful fallback,
        so they can be scored softly instead.
    """
    active_fields = []
    for category, field in stage:
        q_val = query_tags.get(category, {}).get(field)
        if q_val is not None and q_val != "" and q_val != []:
            active_fields.append((category, field))

    if not active_fields:
        return products, []

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
        return products, active_fields   # fallback → score these fields softly

    return filtered, []


# Partial credit when a product is missing a tag that the query specifies.
# 0.0 = treat missing same as mismatch; 0.25 = give 25% credit (neutral-ish).
_MISSING_TAG_CREDIT = 0.25


# Default weight used when a relaxed filter field is scored softly.
# Filter fields aren't in SCORE_WEIGHTS (they're normally hard-filtered),
# so we assign them a weight when they fall back to soft scoring.
_RELAXED_FILTER_WEIGHT = 2.0


def compute_tag_score(query_tags: dict, product_tags: dict,
                      extra_fields: list[tuple[str, str]] | None = None) -> float:
    """
    Score a product on soft fields (0.0–1.0).

    By default, only fields listed in SCORE_WEIGHTS are scored.  When a
    hard-filter stage is relaxed (graceful fallback), the caller passes
    those fields via *extra_fields* so they contribute to the score too.

    Missing product tags receive partial credit (_MISSING_TAG_CREDIT) rather
    than a full zero, since missing data != known mismatch.
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
            if p_val is None or p_val == "" or p_val == []:
                weighted_score += weight * _MISSING_TAG_CREDIT
                continue

            q_set = _to_set_norm(category, field, q_val)
            p_set = _to_set_norm(category, field, p_val)
            if not q_set:
                continue

            overlap = len(q_set & p_set)
            weighted_score += weight * (overlap / len(q_set))

    # Score relaxed filter fields (normally hard-filtered, but fell back)
    for category, field in (extra_fields or []):
        q_val = query_tags.get(category, {}).get(field)
        if q_val is None or q_val == "" or q_val == []:
            continue

        weight = _RELAXED_FILTER_WEIGHT
        total_weight += weight

        p_val = product_tags.get(category, {}).get(field)
        if p_val is None or p_val == "" or p_val == []:
            weighted_score += weight * _MISSING_TAG_CREDIT
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


# ── Component sub-score groups ───────────────────────────────────────────
# Maps a human-friendly component name → list of (category, field) pairs
# whose weights contribute to that component's sub-score.
_COMPONENT_GROUPS: dict[str, list[tuple[str, str]]] = {
    "fit":   [("lenses", "size"), ("style", "face_shape_fit")],
    "style": [("style", "aesthetic"), ("style", "occasion"),
              ("frame", "rim_type"), ("frame", "finish")],
    "color": [("frame", "color"), ("frame", "material"),
              ("frame", "thickness")],
}


def compute_component_scores(query_tags: dict,
                             product_tags: dict) -> dict[str, float]:
    """
    Compute per-component sub-scores (fit, style, color) for a product.

    Each component aggregates weights from a specific subset of scored
    fields.  If a component has no active query fields, it falls back to
    the overall score to avoid returning 1.0 for "no data".

    Returns:
        Dict with keys "fit", "style", "color" — each 0.0–1.0.
    """
    overall = compute_tag_score(query_tags, product_tags)
    result: dict[str, float] = {}

    for component, fields in _COMPONENT_GROUPS.items():
        total_w = 0.0
        scored  = 0.0

        for category, field in fields:
            weight = SCORE_WEIGHTS.get(category, {}).get(field)
            if weight is None:
                continue

            q_val = query_tags.get(category, {}).get(field)
            if q_val is None or q_val == "" or q_val == []:
                continue

            total_w += weight

            p_val = product_tags.get(category, {}).get(field)
            if p_val is None or p_val == "" or p_val == []:
                scored += weight * _MISSING_TAG_CREDIT
                continue

            q_set = _to_set_norm(category, field, q_val)
            p_set = _to_set_norm(category, field, p_val)
            if not q_set:
                continue
            overlap = len(q_set & p_set)
            scored += weight * (overlap / len(q_set))

        # Fall back to overall score when no fields in this group were queried
        result[component] = (scored / total_w) if total_w > 0.0 else overall

    return result


def rank_products(query_tags: dict, products: list[dict],
                  top_k: int = 3, min_score: float = 0.15,
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
    # ── Defensive copy — never mutate the caller's query_tags ───────────
    query_tags = copy.deepcopy(query_tags)

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

        pool.append(product)

    # Inject gender from filters into query_tags (after copy, safe)
    if filters and filters.get("gender") and not query_tags.get("style", {}).get("gender_target"):
        query_tags.setdefault("style", {})["gender_target"] = filters["gender"]

    # ── Sport isolation (HARD filter — no graceful fallback) ─────────────
    # Products tagged 'sport' are shown ONLY when the user explicitly
    # requests sport (or a sport-adjacent type like prizm/chromance, or
    # a sport-only shape like shield).  Otherwise they are excluded so
    # lifestyle frames aren't polluted with sport frames.
    _SPORT_ADJACENT_TYPES = {"prizm", "chromance"}
    _SPORT_ONLY_SHAPES = {"shield"}

    q_lens_type_set  = _to_set(query_tags.get("lenses", {}).get("type"))
    q_shape_set      = _to_set(query_tags.get("lenses", {}).get("shape"))
    user_wants_sport = (
        "sport" in q_lens_type_set
        or bool(q_lens_type_set & _SPORT_ADJACENT_TYPES)
        or bool(q_shape_set & _SPORT_ONLY_SHAPES)
    )

    sport_pool     = [p for p in pool if "sport" in _to_set(p["tags"].get("lenses", {}).get("type"))]
    non_sport_pool = [p for p in pool if "sport" not in _to_set(p["tags"].get("lenses", {}).get("type"))]

    pool = sport_pool if user_wants_sport else non_sport_pool
    # Intentionally NO fallback: sport products must stay isolated.

    # ── Cascading tag filters (with graceful fallback) ────────────────────
    relaxed_fields: list[tuple[str, str]] = []
    for stage in FILTER_STAGES:
        pool, relaxed = _apply_filter_stage(pool, stage, query_tags)
        relaxed_fields.extend(relaxed)

    # ── Soft scoring ─────────────────────────────────────────────────────
    # Relaxed filter fields (if any) are included in soft scoring so that
    # products matching the original filter intent still rank higher.
    scored: list[tuple[dict, float]] = []
    for product in pool:
        score = compute_tag_score(query_tags, product["tags"],
                                  extra_fields=relaxed_fields)
        if score >= min_score:
            scored.append((product, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Fallback: if all products scored below min_score, return the best
    # available rather than an empty list.
    if not scored and pool:
        fallback = [
            (p, compute_tag_score(query_tags, p["tags"],
                                  extra_fields=relaxed_fields))
            for p in pool
        ]
        fallback.sort(key=lambda x: x[1], reverse=True)
        return fallback[:top_k]

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
