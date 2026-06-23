"""Build the 2-image try-on prompt from face analysis + matched product.

Enhanced with face analysis data for more precise glasses placement compared
to optimal_configuration's generic try-on prompt.
"""


def _join(value) -> str:
    """Join a value that may be a list or a single string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


_CLEAR_LENS_COLORS = {"clear", "transparent", "none", "no tint", "untinted", "n/a", "na", ""}
_TINTED_LENS_TYPES = {"sunglasses", "gradient", "mirrored", "polarized", "prizm", "chromance", "sport", "tinted"}


def _lens_clauses(lens_types: str, lens_colors: str):
    """Return (lens_description, optical_instruction).

    Clear / untinted lenses get an explicit 'no tint, fully see-through'
    instruction so the model never renders them as smoke-grey sunglasses.
    """
    type_tokens = [t.strip().lower() for t in str(lens_types or "").split(",") if t.strip()]
    color_tokens = [c.strip().lower() for c in str(lens_colors or "").split(",") if c.strip()]
    has_tinted_type = any(t in _TINTED_LENS_TYPES for t in type_tokens)
    color_is_clear = (not color_tokens) or all(t in _CLEAR_LENS_COLORS for t in color_tokens)
    if color_is_clear and not has_tinted_type:
        desc = (lens_types + ", fully clear and transparent (no tint)") if lens_types else "clear, transparent (no tint)"
        optical = ("The lenses are COMPLETELY CLEAR and transparent — plain prescription glass, fully "
                   "see-through with NO tint, NO colour and NO darkening. These are NOT sunglasses. Keep the "
                   "eyes, eyebrows and skin fully visible through the lenses with zero darkening.")
    else:
        desc = f"{lens_types}, {lens_colors}"
        optical = ("Eyes visible through the lenses at the appropriate opacity for "
                   f"{lens_types} lenses with {lens_colors} tint")
    return desc, optical


def build_tryon_prompt(analysis: dict, matched_product: dict) -> str:
    """
    Build the Nano Banana try-on prompt using:
    - Face analysis data (for precise placement guidance)
    - Matched product tags (for glasses description)

    Args:
        analysis: Full analysis dict from face_analyzer.
        matched_product: The product dict from catalog that was matched.

    Returns:
        The try-on prompt string.
    """
    fa = analysis["face_analysis"]

    prod_tags = matched_product["tags"]
    pf = prod_tags["frame"]
    pl = prod_tags["lenses"]

    pf_colors = _join(pf["color"])
    pl_types = _join(pl["type"])
    pl_colors = _join(pl["color"])
    lens_desc, lens_optical = _lens_clauses(pl_types, pl_colors)

    nose_bridge = fa["nose"]["bridge_width"]
    eye_spacing = fa["eyes"]["spacing"]
    face_angle = fa.get("photo_context", {}).get("face_angle", "straight_on")

    angle_note = ""
    if face_angle != "straight_on":
        angle_note = (
            f"\nThe person's face is at a {face_angle.replace('_', ' ')} angle — "
            f"the glasses must follow this exact 3D rotation."
        )

    prompt = f"""I am providing two images:
- IMAGE 1: A product photo of glasses.
- IMAGE 2: A portrait photo of a person.

YOUR TASK: Create the EXACT same photo as IMAGE 2, but with the person wearing the glasses from IMAGE 1. The result must look like a real photograph — as if the person was already wearing these glasses when the photo was taken.

GLASSES FROM IMAGE 1:
- Frame: {pf.get("shape", "classic")}, {pf.get("material", "")}, {pf_colors}, {pf.get("rim_type", "")}
- Lenses: {lens_desc}
- Reproduce the glasses from IMAGE 1 faithfully — same design, proportions, and details.

PLACEMENT:
- Nose bridge: {nose_bridge}, eye spacing: {eye_spacing} — fit the glasses naturally to this face
- Scale proportionally to the face, bridge on nose, temples toward ears
- {lens_optical}{angle_note}

CRITICAL RULES:
- The output must be a 1:1 compositional match to IMAGE 2 — same head size, crop, zoom, framing, and camera distance
- Do NOT change the aspect ratio of IMAGE 2 — the output must have the same aspect ratio as IMAGE 2
- Do NOT crop, zoom in/out, re-center, reframe, or change what is visible at the edges — no close-up
- The edges of the output must show the EXACT same content as IMAGE 2 — same background, same body parts visible, same space above/below/around the head
- Keep everything else in the image exactly the same, preserving the original style, lighting, and composition
- The person's face, skin, hair, expression, clothing, background, and lighting must remain IDENTICAL
- Do NOT alter, smooth, or enhance any facial features
- Add realistic shadows from the glasses consistent with the existing lighting

OUTPUT: Return ONLY the edited photo. Same dimensions and quality as IMAGE 2. Photorealistic."""

    return prompt
