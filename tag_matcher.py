"""Tag-based matching engine — cascading filter + unified weighted scoring.

Pipeline:
  1. Shape fold: query frame.shape → lenses.shape (this catalog stores the
     silhouette under lenses.shape; the query producers emit it under
     frame.shape, so we fold it in or the shape signal is lost).
  2. Business pre-filters (in_stock, max_price)
  3. Sport isolation (hard filter — no graceful fallback) keyed on
     style.occasion == "sport".
  4. Cascading tag filters with graceful fallback (see FILTER_STAGES):
     a. style.gender_target
     b. lenses.shape
     c. lenses.type
     d. lenses.color
     e. frame.color
  5. Unified scoring on remaining pool (0–100)
  6. Per-model + visual-signature dedup, with low-confidence fallback
     (including a pre-business-filter fallback) so we never return empty.

If a query doesn't specify a filter field, that stage is skipped.
If a filter stage (except the sport filter) would reduce the pool below
min_pool, it is relaxed (skipped) so we still return results.

Value normalisation (applied to both query AND product tags before comparison):
  Colours:    70+ aliases mapped to canonical names
              (e.g. navy→blue, charcoal→gray, havana→tortoiseshell)
              Adjective prefixes stripped (matte-black→black, brushed-silver→silver, etc.)
  Shapes:     variant→canonical (butterfly→cat-eye, pilot→aviator, etc.)
  Materials:  family grouping (stainless-steel→metal, mixed-metal-*→metal,
              recycled-acetate→acetate, bio-nylon→nylon, propionate→plastic, etc.)

Scoring details:
  - All user-specified fields (filter + soft) contribute to one unified score.
  - Filter fields: products that match earn full weight, whether the filter
    was strictly enforced or relaxed for the pool.  Products that don't match
    earn 0 on that field.
  - Soft fields earn proportional credit based on tag overlap.
  - Missing product tags on soft fields receive partial credit (0.25× weight).
  - Final score = earned_weight / active_weight × 100 (range 0–100).
  - Fields left as "any" (not specified) are excluded from scoring entirely.
  - compute_component_scores() provides per-dimension sub-scores
    (fit, style, color) for UI display.
  - query_tags are never mutated (defensive deep-copy).
"""

import copy
import re

# ── Unified field weights (filter + soft, used for scoring) ──────────────
FIELD_WEIGHTS: dict[tuple[str, str], int] = {
    # Filter fields (high weight — primary user selections)
    ("style",  "gender_target"): 10,
    ("lenses", "shape"):         10,
    ("lenses", "type"):          8,
    ("lenses", "color"):         7,
    ("frame",  "color"):         7,
    # Soft fields (lower weight — secondary preferences)
    ("frame",  "material"):       6,
    ("frame",  "rim_type"):       5,
    ("style",  "aesthetic"):      5,
    ("lenses", "size"):           4,
    ("style",  "face_shape_fit"): 3,
    ("style",  "occasion"):       3,
    ("frame",  "thickness"):      3,
    ("frame",  "finish"):         2,
}

# ── Filter stages (applied in order, with graceful fallback) ─────────────
FILTER_STAGES = [
    [("style",  "gender_target")],   # Stage 1: gender
    [("lenses", "shape")],           # Stage 2: lens shape
    [("lenses", "type")],            # Stage 3: lens type
    [("lenses", "color")],           # Stage 4: lens color
    [("frame",  "color")],           # Stage 5: frame color
]

# Set of all fields that participate in hard filtering (for scoring logic)
_FILTER_FIELD_SET: set[tuple[str, str]] = {
    (cat, field)
    for stage in FILTER_STAGES
    for cat, field in stage
}

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
    r"^(?:matte[-\s]|brushed[-\s]|gradient[-\s]|mirrored[-\s]|polarized[-\s]|"
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


def _visual_signature(product: dict) -> tuple:
    """Finish-blind visual fingerprint used for cross-model dedup in top-K.

    Two products with the same signature render near-identically in the virtual
    try-on UI, so we want at most one of each signature in any result page.
    `finish` (matte vs glossy) is excluded because it collapses in face-render.
    `rim_type` is included because rimless vs full-rim is a major visual
    difference even after rendering.  In this catalog the silhouette lives in
    `lenses.shape` (frame has no shape field), so that is the shape key.  Shape,
    material and colours are normalized to canonical form so havana-brown/
    tortoiseshell and gunmetal/silver collapse correctly regardless of catalog
    drift; the remaining raw fields are lowercased/stripped for the same reason.
    Brand and model_name are excluded — the whole point is to catch cross-model
    twins (a tortoiseshell rectangular acetate appearing in two model lines).
    """
    f = product.get("tags", {}).get("frame", {}) or {}
    l = product.get("tags", {}).get("lenses", {}) or {}
    fc = tuple(sorted(_to_set_norm("frame", "color", f.get("color"))))
    lc = tuple(sorted(_to_set_norm("lenses", "color", l.get("color"))))
    return (
        _norm_shape(str(l.get("shape", ""))),
        _norm_material(str(f.get("material", ""))),
        str(f.get("thickness", "")).lower().strip(),
        str(f.get("rim_type", "")).lower().strip(),
        fc,
        lc,
        str(l.get("size", "")).lower().strip(),
    )


def _field_matches_norm(category: str, field: str,
                        query_val, product_val) -> bool:
    """Return True if product has ANY overlap with the query (after normalisation)."""
    q = _to_set_norm(category, field, query_val)
    p = _to_set_norm(category, field, product_val)
    if not q:
        return True   # query doesn't specify → automatic pass
    # Gender: "unisex" products match any gender query, and a "unisex"
    # query matches any product gender.
    if field == "gender_target":
        if "unisex" in p or "unisex" in q:
            return True
    return bool(q & p)


def _apply_filter_stage(
    products: list[dict],
    stage: list[tuple[str, str]],
    query_tags: dict,
    min_pool: int = 1,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Apply one filter stage.  A product passes if it matches ALL fields
    in the stage that the query specifies.

    If the query doesn't specify any field in this stage → pool unchanged.
    If filtering would reduce the pool below min_pool → pool unchanged
    (graceful fallback).

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

    # Relax filter if it would reduce pool below min_pool, but only when
    # the input pool is large enough to satisfy min_pool in the first place.
    effective_min = min_pool if len(products) >= min_pool else 1
    if len(filtered) < effective_min:
        return products, active_fields   # fallback → score these fields softly

    return filtered, []


# Partial credit when a product is missing a tag that the query specifies.
# 0.0 = treat missing same as mismatch; 0.25 = give 25% credit (neutral-ish).
_MISSING_TAG_CREDIT = 0.25


def compute_tag_score(
    query_tags: dict,
    product_tags: dict,
    active_filter_fields: list[tuple[str, str]] | None = None,
    relaxed_fields: list[tuple[str, str]] | None = None,
) -> float:
    """
    Unified score across all user-specified fields (0–100).

    Every field in FIELD_WEIGHTS that the user specified contributes:
      - Filter fields that passed exactly → full weight earned.
      - Filter fields that were relaxed (graceful fallback) → 0 earned.
      - Soft fields → proportional overlap × weight.
      - Missing product tags on soft fields → partial credit (0.25 × weight).
      - Fields the user left as "any" → excluded from both numerator & denominator.

    Returns score in range [0, 100].  100 = perfect match on all specified fields.
    """
    active_filters = set(active_filter_fields or [])
    relaxed = set(relaxed_fields or [])

    total_weight   = 0.0
    earned_weight  = 0.0

    for (category, field), weight in FIELD_WEIGHTS.items():
        q_val = query_tags.get(category, {}).get(field)
        if q_val is None or q_val == "" or q_val == []:
            continue  # user didn't specify → skip

        total_weight += weight

        # ── Filter field scoring ──────────────────────────────────────
        if (category, field) in _FILTER_FIELD_SET:
            if (category, field) in active_filters:
                # Whether the cascade enforced this filter or relaxed it for
                # the pool, check THIS product: if it matches, credit the full
                # weight; otherwise 0.  This preserves "perfect match → 100"
                # for individual products even when the filter relaxed pool-wide.
                p_val = product_tags.get(category, {}).get(field)
                if _field_matches_norm(category, field, q_val, p_val):
                    earned_weight += weight
            # else: filter field not active for this query → 0 credit
            continue

        # ── Soft field scoring ────────────────────────────────────────
        p_val = product_tags.get(category, {}).get(field)
        if p_val is None or p_val == "" or p_val == []:
            earned_weight += weight * _MISSING_TAG_CREDIT
            continue

        q_set = _to_set_norm(category, field, q_val)
        p_set = _to_set_norm(category, field, p_val)
        if not q_set:
            continue

        overlap = len(q_set & p_set)
        earned_weight += weight * (overlap / len(q_set))

    if total_weight == 0.0:
        return 100.0  # no fields specified → all products equally valid

    return (earned_weight / total_weight) * 100.0


# ── Component sub-score groups ───────────────────────────────────────────
# Maps a human-friendly component name → list of (category, field) pairs
# whose weights contribute to that component's sub-score.
_COMPONENT_GROUPS: dict[str, list[tuple[str, str]]] = {
    "fit":   [("lenses", "size"), ("style", "face_shape_fit")],
    "style": [("style", "aesthetic"), ("style", "occasion"),
              ("frame", "rim_type"), ("frame", "finish")],
    "color": [("lenses", "color"), ("frame", "color"),
              ("frame", "material"), ("frame", "thickness")],
}


def compute_component_scores(
    query_tags: dict,
    product_tags: dict,
    active_filter_fields: list[tuple[str, str]] | None = None,
    relaxed_fields: list[tuple[str, str]] | None = None,
) -> dict[str, float]:
    """
    Compute per-component sub-scores (fit, style, color) for a product.

    Each component aggregates weights from a specific subset of fields
    in FIELD_WEIGHTS.  If a component has no active query fields, it
    falls back to the overall score to avoid returning 100 for "no data".

    Returns:
        Dict with keys "fit", "style", "color" — each 0–100.
    """
    overall = compute_tag_score(query_tags, product_tags,
                                active_filter_fields, relaxed_fields)
    active_filters = set(active_filter_fields or [])
    relaxed = set(relaxed_fields or [])
    result: dict[str, float] = {}

    for component, fields in _COMPONENT_GROUPS.items():
        total_w = 0.0
        earned  = 0.0

        for category, field in fields:
            weight = FIELD_WEIGHTS.get((category, field))
            if weight is None:
                continue

            q_val = query_tags.get(category, {}).get(field)
            if q_val is None or q_val == "" or q_val == []:
                continue

            total_w += weight

            # Filter field scoring (relaxed filters still credit matches)
            if (category, field) in _FILTER_FIELD_SET:
                if (category, field) in active_filters:
                    p_val = product_tags.get(category, {}).get(field)
                    if _field_matches_norm(category, field, q_val, p_val):
                        earned += weight
                continue

            # Soft field scoring
            p_val = product_tags.get(category, {}).get(field)
            if p_val is None or p_val == "" or p_val == []:
                earned += weight * _MISSING_TAG_CREDIT
                continue

            q_set = _to_set_norm(category, field, q_val)
            p_set = _to_set_norm(category, field, p_val)
            if not q_set:
                continue
            overlap = len(q_set & p_set)
            earned += weight * (overlap / len(q_set))

        # Fall back to overall score when no fields in this group were queried
        result[component] = (earned / total_w * 100.0) if total_w > 0.0 else overall

    return result


# Occasions that mark a versatile lifestyle frame.  A frame tagged "sport"
# alongside any of these is sport-SUITABLE, not a dedicated performance frame.
_LIFESTYLE_OCCASIONS: set[str] = {"everyday", "office", "formal", "fashion"}


def _is_dedicated_sport(product: dict) -> bool:
    """True for a dedicated performance/sport frame, as opposed to a versatile
    everyday frame that merely also suits sport.

    Used only as a tie-break within a sports query so real sports glasses
    (sport lens tech, a wrap/shield silhouette, or a sport-only occasion) rank
    ahead of versatile everyday-and-sport frames when their scores tie.
    """
    tags = product.get("tags", {})
    occ = _to_set(tags.get("style", {}).get("occasion"))
    ltype = _to_set(tags.get("lenses", {}).get("type"))
    shape = {_norm_shape(s) for s in _to_set(tags.get("lenses", {}).get("shape"))}
    return (
        "sport" in ltype
        or bool(shape & {"wrap"})  # shield / curved-wrap → wrap (sport silhouette)
        or ("sport" in occ and occ.isdisjoint(_LIFESTYLE_OCCASIONS))
    )


def rank_products(query_tags: dict, products: list[dict],
                  top_k: int = 3,
                  max_per_model: int = 2,
                  min_score: float = 15,
                  filters: dict | None = None) -> list[tuple[dict, float]]:
    """
    Filter then score products.

    Hard filters (in_stock, max_price, sport isolation) are applied first,
    then the cascading tag filter pipeline, then unified scoring (0–100),
    then per-model + visual-signature dedup with low-confidence fallback.

    Args:
        query_tags:     Structured query tags (frame/lenses/style dicts).
        products:       List of product dicts from catalog.
        top_k:          Max results to return.
        max_per_model:  Max results sharing the same model_name (default 2).
        min_score:      Accepted for backward compatibility; NOT applied as a
                        hard cutoff (result quality is handled by the dedup +
                        fallback passes instead).
        filters:        Business filters: {"in_stock_only", "max_price", "gender"}.

    Returns:
        List of (product, score) sorted by score descending.
    """
    # Short-circuit pathological top_k values before doing any work
    if top_k <= 0:
        return []

    # ── Defensive copy — never mutate the caller's query_tags ───────────
    query_tags = copy.deepcopy(query_tags)

    # ── Shape fold: query frame.shape → lenses.shape ─────────────────────
    # This catalog stores the silhouette under lenses.shape (frame has no shape
    # field), but the query producers (query_interpreter, face-analysis
    # recommended_tags) emit the chosen silhouette under frame.shape.  Fold it
    # in (frame.shape wins) or the shape signal is silently dropped.  The
    # preferences_to_query_tags path writes lenses.shape and leaves frame.shape
    # empty, so it is unaffected.
    q_frame_shape = query_tags.get("frame", {}).get("shape")
    if q_frame_shape is not None and q_frame_shape != "" and q_frame_shape != []:
        query_tags.setdefault("lenses", {})["shape"] = q_frame_shape

    # ── Snapshot the full catalog before business filters ────────────────
    # Used by the ultimate fallback (Pass 5) so an over-tight max_price /
    # in_stock_only can never leave the user with an empty page.
    pool_unfiltered = list(products)

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

    # Force the detected gender onto query_tags (after copy, safe).  The
    # caller's `gender` filter — typically the gender detected from the
    # portrait — MUST win over whatever recommended_tags.style.gender_target
    # the LLM wrote, which is often "unisex" for a man (a styling suggestion)
    # that silently neutralises the filter (gender matching auto-passes when
    # "unisex" is on either side).  Overriding restricts the pool to
    # (men + unisex) for a male, (women + unisex) for a female.
    if filters and filters.get("gender"):
        query_tags.setdefault("style", {})["gender_target"] = filters["gender"]

    # ── Sport isolation (HARD filter — no graceful fallback) ─────────────
    # Products are 'sport' when style.occasion contains "sport".  They are
    # shown ONLY when the user explicitly requests sport (a sport lens type, a
    # sport-adjacent type like prizm/chromance, or a sport-only shape like
    # shield).  Otherwise they are excluded so lifestyle results aren't polluted.
    _SPORT_ADJACENT_TYPES = {"prizm", "chromance"}
    _SPORT_ONLY_SHAPES = {"shield"}

    q_occasion_set   = _to_set(query_tags.get("style", {}).get("occasion"))
    q_lens_type_set  = _to_set(query_tags.get("lenses", {}).get("type"))
    q_shape_set      = _to_set(query_tags.get("lenses", {}).get("shape"))
    user_wants_sport = (
        "sport" in q_occasion_set            # primary signal — Free Search and
                                             # face-analysis emit sport here, and
                                             # it matches the membership predicate
        or "sport" in q_lens_type_set
        or bool(q_lens_type_set & _SPORT_ADJACENT_TYPES)
        or bool(q_shape_set & _SPORT_ONLY_SHAPES)
    )

    # Sport membership uses style.occasion (canonical for this catalog) — the
    # lens-type "sport" tag is sparse here, while occasion="sport" reliably
    # marks sport-suitable frames.
    def _is_sport(p):
        return "sport" in _to_set(p["tags"].get("style", {}).get("occasion"))
    sport_pool     = [p for p in pool if _is_sport(p)]
    non_sport_pool = [p for p in pool if not _is_sport(p)]

    # Save the post-business pool for the low-confidence fallback (Pass 4),
    # used when the chosen pool collapses (e.g. sport isolation emptied it).
    pool_before_sport_isolation = pool
    pool = sport_pool if user_wants_sport else non_sport_pool
    # Intentionally NO graceful fallback for the sport/lifestyle split; Pass 4/5
    # below recover if the chosen pool can't fill top_k.

    # ── Cascading tag filters (with graceful fallback) ────────────────────
    active_filter_fields: list[tuple[str, str]] = []
    relaxed_fields: list[tuple[str, str]] = []
    for stage in FILTER_STAGES:
        # Track which filter fields the user actually specified
        for category, field in stage:
            q_val = query_tags.get(category, {}).get(field)
            if q_val is not None and q_val != "" and q_val != []:
                active_filter_fields.append((category, field))
        pool, relaxed = _apply_filter_stage(pool, stage, query_tags,
                                            min_pool=3)
        relaxed_fields.extend(relaxed)

    # ── Unified scoring (0–100) ──────────────────────────────────────────
    scored: list[tuple[dict, float]] = []
    for product in pool:
        score = compute_tag_score(query_tags, product["tags"],
                                  active_filter_fields=active_filter_fields,
                                  relaxed_fields=relaxed_fields)
        scored.append((product, score))

    if user_wants_sport:
        # Tie-break: dedicated performance frames lead versatile (everyday-and-
        # sport) frames at equal score, so a sports search surfaces real sports
        # glasses first.  (score dominates; this only orders ties.)  This order
        # propagates through the dedup passes and the final stable re-sort.
        scored.sort(key=lambda x: (x[1], _is_dedicated_sport(x[0])), reverse=True)
    else:
        scored.sort(key=lambda x: x[1], reverse=True)

    def _score_all(source):
        return sorted(
            [
                (p, compute_tag_score(query_tags, p["tags"],
                                      active_filter_fields=active_filter_fields,
                                      relaxed_fields=relaxed_fields))
                for p in source
            ],
            key=lambda x: x[1], reverse=True,
        )

    # ── Pass 1: per-model dedup, over-produce candidates ────────────────
    # Allow up to 3*top_k so the visual-diversity pass has options to pick from.
    candidates: list[tuple[dict, float]] = []
    model_counts: dict[str, int] = {}
    for product, score in scored:
        model = product["tags"].get("product", {}).get("model_name", "")
        if model_counts.get(model, 0) >= max_per_model:
            continue
        candidates.append((product, score))
        model_counts[model] = model_counts.get(model, 0) + 1
        if len(candidates) >= top_k * 3:
            break

    # ── Pass 2: visual-signature dedup across the top candidates ─────────
    # Products sharing a visual signature render near-identically in face-
    # rendered try-on.  Keep only the highest-scoring representative (candidates
    # are pre-sorted descending, so the first seen per signature is the best).
    results: list[tuple[dict, float]] = []
    seen_sigs: set = set()
    for product, score in candidates:
        sig = _visual_signature(product)
        if sig in seen_sigs:
            continue
        results.append((product, score))
        seen_sigs.add(sig)
        if len(results) == top_k:
            break

    # ── Pass 3: fill remaining slots ignoring the visual constraint ──────
    if len(results) < top_k:
        selected_ids = {p["id"] for p, _ in results}
        for product, score in candidates:
            if product["id"] in selected_ids:
                continue
            results.append((product, score))
            selected_ids.add(product["id"])
            if len(results) == top_k:
                break

    # ── Pass 4: low-confidence fallback from the scored / post-business pool
    # If still short (e.g. sport isolation or the per-model cap collapsed the
    # candidate pool), pull the next-best products, flagged low_confidence.
    if len(results) < top_k:
        selected_ids = {p["id"] for p, _ in results}
        fallback_source = scored if scored else _score_all(pool_before_sport_isolation)
        for product, score in fallback_source:
            if product["id"] in selected_ids:
                continue
            product_copy = copy.deepcopy(product)
            product_copy["_low_confidence"] = True
            results.append((product_copy, score))
            selected_ids.add(product["id"])
            if len(results) == top_k:
                break

    # ── Pass 5: ultimate fallback — relax the business filters ───────────
    # Only when business pre-filters (max_price / in_stock_only) left NOTHING
    # at all: score the unfiltered catalog and surface the closest matches,
    # flagged low_confidence plus WHY they were excluded so the UI can label
    # them ("closest match, above budget / unavailable").  Guarded on an empty
    # result so an under-budget pool of fewer than top_k is returned as-is
    # rather than padded with budget/stock violations.
    if not results:
        max_price = filters.get("max_price") if filters else None
        in_stock_only = filters.get("in_stock_only") if filters else False
        for product, score in _score_all(pool_unfiltered):
            product_copy = copy.deepcopy(product)
            product_copy["_low_confidence"] = True
            prod_meta = product.get("tags", {}).get("product", {})
            if max_price is not None and prod_meta.get("price", 0) > max_price:
                product_copy["_over_budget"] = True
            if in_stock_only and not prod_meta.get("in_stock", False):
                product_copy["_out_of_stock"] = True
            results.append((product_copy, score))
            if len(results) == top_k:
                break

    # Passes 3-5 can append a higher-scoring product after a lower-scoring one
    # already claimed a slot.  Re-sort so the final list is monotonically
    # descending by score regardless of which pass produced each entry.
    results.sort(key=lambda x: x[1], reverse=True)
    return results


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
