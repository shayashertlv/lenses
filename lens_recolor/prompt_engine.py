"""The prompt builder for glasses lens recoloring — the critical file."""


def build_lens_recolor_prompt(
    target_color: str,
    intensity: str = "medium",
    finish: str = "standard",
    preserve_reflections: bool = True,
) -> str:
    """
    Build a precise prompt for Nano Banana to recolor only the glasses lenses.

    Args:
        target_color: Any natural-language color description (e.g., "ocean blue",
                      "rose gold", "classic aviator green (G-15)")
        intensity: Tint darkness — "light", "medium", or "dark"
        finish: Lens style — "standard", "gradient", "mirror", or "polarized"
        preserve_reflections: Whether to keep natural lens reflections

    Returns:
        The full prompt string to send to the model.
    """

    # Map intensity to descriptive opacity language
    intensity_map = {
        "light": (
            "a subtle, light tint — approximately 25-30% opacity, like lightly "
            "tinted fashion lenses where you can clearly see the eyes through them"
        ),
        "medium": (
            "a balanced, medium tint — approximately 50-60% opacity, like standard "
            "sunglasses where the eyes are partially visible"
        ),
        "dark": (
            "a deep, rich tint — approximately 80-90% opacity, like dark sunglasses "
            "where the eyes are barely visible through the lenses"
        ),
    }

    # Map finish to descriptive lens style
    finish_map = {
        "standard": "a uniform, smooth color tint across the entire lens surface",
        "gradient": (
            "a gradient tint that is darker at the top of each lens and gradually "
            "fades to lighter/clearer toward the bottom — a classic gradient sunglass look"
        ),
        "mirror": (
            "a reflective, mirrored finish that shows subtle environmental reflections "
            "on the lens surface while maintaining the base color — like mirrored aviator "
            "sunglasses"
        ),
        "polarized": (
            "a slightly reflective, polarized appearance with a subtle sheen on the "
            "lens surface while maintaining the base color"
        ),
    }

    intensity_desc = intensity_map.get(intensity, intensity_map["medium"])
    finish_desc = finish_map.get(finish, finish_map["standard"])

    if preserve_reflections:
        reflection_instruction = (
            "If the original lenses have natural light reflections, glare spots, "
            "or environmental reflections visible on the lens surface, preserve them "
            "naturally on top of the new color tint. The reflections should interact "
            "realistically with the new lens color."
        )
    else:
        reflection_instruction = (
            "Apply a clean tint without additional reflections — just the pure color "
            "on the lens surface."
        )

    prompt = f"""Edit this photo by changing ONLY the color of the glasses lenses. Apply the following lens modification:

TARGET LENS COLOR: {target_color}
TINT INTENSITY: {intensity_desc}
LENS FINISH STYLE: {finish_desc}

{reflection_instruction}

CRITICAL PRESERVATION RULES — do NOT change any of the following:
- The person's face, skin tone, skin texture, facial features, expression, and makeup must remain IDENTICAL
- The person's hair (color, style, texture, stray hairs) must remain IDENTICAL
- The glasses FRAME (shape, color, material, thickness, temple arms, nose pads, any frame details) must remain COMPLETELY UNCHANGED — only the lens area inside the frame changes
- The person's clothing, accessories, jewelry must remain IDENTICAL
- The background (color, texture, bokeh, lighting, objects) must remain IDENTICAL
- The overall lighting, shadows, and color grading of the photo must remain IDENTICAL
- The photo composition, framing, and resolution must remain IDENTICAL
- Any text, logos, or watermarks present must remain IDENTICAL

LENS COLOR APPLICATION GUIDANCE:
- Apply the {target_color} tint ONLY to the transparent/semi-transparent lens area bounded by the glasses frame
- The tint should look like a real, physical lens — it should follow the curvature and shape of the lens
- Where the lens overlaps the person's eyes and face, the {target_color} tint should blend naturally, as real tinted glass does — the skin and eye features behind the lens should show through at the appropriate opacity level
- The edge of the color change must PRECISELY follow the inner edge of the glasses frame — no color bleeding onto the frame or face
- Both lenses must have the SAME color treatment applied consistently

OUTPUT: Return the edited photo maintaining the EXACT same dimensions, quality, and format as the input. The result must be photorealistic — it should look like an actual photograph of someone wearing {target_color} tinted glasses, NOT like a digital color overlay was applied."""

    return prompt
