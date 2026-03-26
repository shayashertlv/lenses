"""Tag-based matching engine — cascading filter + weighted scoring.

Pipeline:
  1. Filter by gender
  2. Filter by lenses.shape
  3. Filter by lenses.type
  4. Filter by lenses.color
  5. Score remaining pool on soft fields (0.0–1.0)

If a query doesn't specify a filter field, that stage is skipped.
If a filter stage would eliminate ALL remaining products, it is relaxed
(skipped) so we still return results.
"""

# ── Soft-scoring weights (only fields NOT used as filters) ───────────────
# These rank products AFTER the filter pipeline has narrowed the pool.
SCORE_WEIGHTS = {
    "frame": {
        "material": 3.0,
        "color": 3.0,
        "thickness": 1.5,
        "finish": 1.0,
        "rim_type": 2.5,
    },
    "lenses": {
        "size": 2.0,
    },
    "style": {
        "aesthetic": 2.5,
        "face_shape_fit": 1.5,
        "occasion": 1.5,
    },
}

# ── Filter stages (applied in order) ────────────────────────────────────
# Each entry is (category, field).  Stage 2 groups two fields together.
FILTER_STAGES = [
    [("style", "gender_target")],           # Stage 1: gender
    [("lenses", "shape")],                       # Stage 2: lens shape
    [("lenses", "type")],                   # Stage 3: lens type
    [("lenses", "color")],                  # Stage 4: lens color
]


def _to_set(value) -> set[str]:
    """Normalize a tag value (string or list) to a lowercase set."""
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(v).lower() for v in value}
    return {str(value).lower()}


def _field_matches(query_val, product_val) -> bool:
    """Return True if product has ANY overlap with the query value."""
    q = _to_set(query_val)
    p = _to_set(product_val)
    if not q:
        return True  # query doesn't specify → automatic pass
    return bool(q & p)


def _apply_filter_stage(products: list[dict], stage: list[tuple[str, str]],
                        query_tags: dict) -> list[dict]:
    """
    Apply one filter stage.  A product passes if it matches ALL fields
    in the stage that the query specifies.

    If the query doesn't specify any field in this stage, returns pool unchanged.
    If filtering would empty the pool, returns pool unchanged (graceful fallback).
    """
    # Check if query specifies any field in this stage
    active_fields = []
    for category, field in stage:
        q_val = query_tags.get(category, {}).get(field)
        if q_val is not None and q_val != "" and q_val != []:
            active_fields.append((category, field))

    if not active_fields:
        return products  # nothing to filter on

    filtered = []
    for product in products:
        ptags = product["tags"]
        passes = True
        for category, field in active_fields:
            q_val = query_tags[category][field]
            p_val = ptags.get(category, {}).get(field)
            if not _field_matches(q_val, p_val):
                passes = False
                break
        if passes:
            filtered.append(product)

    if not filtered:
        return products  # would empty the pool → skip this stage

    return filtered


def compute_tag_score(query_tags: dict, product_tags: dict) -> float:
    """
    Score a product on soft fields only (0.0–1.0).

    Filter fields (gender, shapes, lens type, lens color) are NOT scored
    here — they are handled by the filter pipeline.  This scores how well
    the product matches on material, frame color, rim type, aesthetic, etc.
    """
    total_weight = 0.0
    weighted_score = 0.0

    for category, fields in SCORE_WEIGHTS.items():
        q_cat = query_tags.get(category, {})
        p_cat = product_tags.get(category, {})

        for field, weight in fields.items():
            q_val = q_cat.get(field)
            if q_val is None or q_val == "" or q_val == []:
                continue  # query doesn't specify → skip

            total_weight += weight

            p_val = p_cat.get(field)
            if p_val is None:
                continue  # product missing → 0

            q_set = _to_set(q_val)
            p_set = _to_set(p_val)
            if not q_set:
                continue

            overlap = len(q_set & p_set)
            weighted_score += weight * (overlap / len(q_set))

    if total_weight == 0.0:
        return 1.0  # no soft fields specified → all filtered products equal

    return weighted_score / total_weight


def rank_products(query_tags: dict, products: list[dict],
                  top_k: int = 3, min_score: float = 0.0,
                  filters: dict | None = None) -> list[tuple[dict, float]]:
    """
    Filter then score products.

    Hard filters (in_stock, max_price) are applied first, then the
    cascading tag filter pipeline, then soft scoring on remaining fields.

    Args:
        query_tags: Structured query tags (frame/lenses/style dicts).
        products: List of product dicts from catalog.
        top_k: Max results to return.
        min_score: Minimum score threshold.
        filters: Business filters: {"in_stock_only", "max_price", "gender"}.
                 Note: gender in filters is redundant with query_tags
                 style.gender_target but kept for backward compat.

    Returns:
        List of (product, score) sorted by score descending.
    """
    # ── Pre-filter: business rules (stock, price) ────────────────────────
    pool = []
    for product in products:
        ptags = product["tags"]
        prod_meta = ptags.get("product", {})
        style_tags = ptags.get("style", {})

        if filters:
            if filters.get("in_stock_only") and not prod_meta.get("in_stock", False):
                continue
            if filters.get("max_price") is not None:
                if prod_meta.get("price", 0) > filters["max_price"]:
                    continue
            # gender in filters → inject into query_tags if not already there
            if filters.get("gender") and not query_tags.get("style", {}).get("gender_target"):
                query_tags.setdefault("style", {})["gender_target"] = filters["gender"]

        pool.append(product)

    # ── Cascading tag filters ────────────────────────────────────────────
    for stage in FILTER_STAGES:
        pool = _apply_filter_stage(pool, stage, query_tags)

    # ── Soft scoring on remaining pool ───────────────────────────────────
    scored = []
    for product in pool:
        score = compute_tag_score(query_tags, product["tags"])
        if score >= min_score:
            scored.append((product, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def preferences_to_query_tags(prefs: dict) -> dict:
    """
    Convert UI preference selections (flat keys) into structured query tags.

    Maps Free Search UI fields like "frame_shape", "frame_color", etc.
    to the nested tag structure used by the filter + score pipeline.
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
