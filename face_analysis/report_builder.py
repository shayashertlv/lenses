"""Converts the raw analysis JSON into a formatted, human-readable report.

Includes face analysis, recommended tags, reasoning, and inventory match results.
"""


def _join(value) -> str:
    """Join a value that may be a list or a single string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def build_report(analysis: dict, match_results: list | None = None) -> str:
    """
    Build human-readable report from analysis + optional inventory match results.

    Args:
        analysis: Full analysis dict from face_analyzer.
        match_results: Optional list of (product_dict, score) from inventory_matcher.

    Returns:
        Formatted report string.
    """
    fa = analysis["face_analysis"]
    rec = analysis["glasses_recommendation"]

    report = []
    report.append("=" * 60)
    report.append("  FACIAL ANALYSIS & GLASSES RECOMMENDATION REPORT")
    report.append("=" * 60)

    # --- Face Shape ---
    report.append("\n--- FACE SHAPE ---")
    report.append(
        f"Primary: {fa['face_shape']['primary_shape'].replace('_', ' ').title()} "
        f"(confidence: {fa['face_shape']['confidence']})"
    )
    report.append(f"Notes: {fa['face_shape']['secondary_characteristics']}")

    # --- Key Features ---
    report.append("\n--- KEY FEATURES ---")
    report.append(
        f"Forehead: {fa['forehead']['width']}, "
        f"{fa['forehead']['height']} height"
    )
    report.append(f"Cheekbones: {fa['cheekbones']['prominence']}")
    report.append(
        f"Jawline: {fa['jawline']['shape']}, "
        f"{fa['jawline']['definition']} definition"
    )
    report.append(
        f"Chin: {fa['chin']['shape']}, {fa['chin']['size']} size"
    )
    report.append(
        f"Nose bridge: {fa['nose']['bridge_width']} width, "
        f"{fa['nose']['bridge_height']} height"
    )
    report.append(
        f"Nose: {fa['nose']['shape']}, {fa['nose']['length']} length"
    )
    report.append(
        f"Eyes: {fa['eyes']['spacing']} spacing, "
        f"{fa['eyes']['shape']}, {fa['eyes']['size']} size, "
        f"{fa['eyes'].get('eye_color', 'N/A')}"
    )
    report.append(
        f"Eyebrows: {fa['eyebrows']['shape']}, "
        f"{fa['eyebrows']['thickness']}"
    )

    # --- Skin & Hair ---
    report.append("\n--- SKIN & HAIR ---")
    report.append(
        f"Skin: {fa['skin']['tone'].replace('_', ' ').title()} "
        f"({fa['skin']['undertone']} undertone)"
    )
    report.append(
        f"Hair: {fa['hair']['color']}, {fa['hair']['style']}"
    )
    if fa["facial_hair"]["present"]:
        report.append(f"Facial hair: {fa['facial_hair']['type']}")

    # --- Photo Context ---
    photo_ctx = fa.get("photo_context", {})
    if photo_ctx:
        report.append(
            f"\nPhoto: {photo_ctx.get('lighting', '?').replace('_', ' ')} lighting, "
            f"{photo_ctx.get('setting', '?')} setting, "
            f"{photo_ctx.get('face_angle', '?').replace('_', ' ')} angle"
        )

    # --- Proportions ---
    proportions = fa.get("overall_proportions", {})
    if proportions:
        report.append(f"\nSymmetry: {proportions.get('symmetry', 'N/A')}")
        report.append(
            f"Facial thirds: {proportions.get('facial_thirds', 'N/A')}"
        )

    report.append(
        f"Age range: {fa.get('estimated_age_range', 'N/A')}"
    )

    # --- Recommendation ---
    report.append("\n" + "=" * 60)
    report.append("  RECOMMENDED GLASSES (ideal profile)")
    report.append("=" * 60)

    tags = rec["recommended_tags"]
    tf = tags["frame"]
    tl = tags["lenses"]
    ts = tags["style"]

    report.append(
        f"\nFrame: {tf['shape']}, {tf['material']}, "
        f"{_join(tf['color'])}, {tf['thickness']}, "
        f"{tf['finish']}, {tf['rim_type']}"
    )
    report.append(
        f"Lenses: {_join(tl['type'])}, {_join(tl['color'])}, "
        f"{tl['size']}"
    )
    report.append(
        f"Style: {_join(ts['aesthetic'])}, {ts['gender_target']}"
    )
    report.append(f"Occasion: {_join(ts['occasion'])}")

    # --- Reasoning ---
    report.append("\n--- REASONING ---")
    reasoning = rec["reasoning"]
    reasoning_keys = [
        ("face_shape_logic", "Face Shape"),
        ("proportion_logic", "Proportions"),
        ("nose_bridge_logic", "Nose Fit"),
        ("color_logic", "Color Choice"),
        ("style_logic", "Style"),
        ("lens_logic", "Lens Choice"),
    ]
    for key, label in reasoning_keys:
        if key in reasoning and reasoning[key]:
            report.append(f"\n{label}: {reasoning[key]}")

    # --- Inventory match results ---
    if match_results:
        report.append("\n" + "=" * 60)
        report.append("  BEST MATCHES FROM INVENTORY")
        report.append("=" * 60)
        for i, (product, score) in enumerate(match_results, 1):
            p = product["tags"]["product"]
            f = product["tags"]["frame"]
            stock = "In stock" if p.get("in_stock") else "Out of stock"
            report.append(
                f"\n#{i} [{score:.3f}] {product['name']}"
                f"\n    {f['shape']}, {_join(f['color'])}, "
                f"{f['material']}, {f['thickness']}"
                f"\n    ${p['price']:.2f} — {stock}"
            )

    # --- Alternatives ---
    alternatives = analysis.get("alternative_recommendations", [])
    if alternatives:
        report.append("\n--- ALTERNATIVES ---")
        for i, alt in enumerate(alternatives, 1):
            shape = alt["frame_shape"].replace("_", " ").title()
            report.append(
                f"{i}. {shape}: {alt['brief_description']}"
            )
            report.append(f"   Why: {alt['why_also_works']}")

    report.append("\n" + "=" * 60)

    return "\n".join(report)
