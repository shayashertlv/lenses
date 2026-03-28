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

    intensity_map = {
        "light": "light tint (~25-30% opacity, eyes clearly visible)",
        "medium": "medium tint (~50-60% opacity, eyes partially visible)",
        "dark": "dark tint (~80-90% opacity, eyes barely visible)",
    }

    finish_map = {
        "standard": "uniform smooth tint",
        "gradient": "gradient tint, darker at top fading to lighter at bottom",
        "mirror": "reflective mirrored finish with subtle environmental reflections",
        "polarized": "slightly reflective polarized sheen",
    }

    intensity_desc = intensity_map.get(intensity, intensity_map["medium"])
    finish_desc = finish_map.get(finish, finish_map["standard"])

    reflection_note = (
        "Preserve any existing lens reflections naturally on top of the new color."
        if preserve_reflections
        else "Apply a clean tint without additional reflections."
    )

    prompt = f"""Create the EXACT same photo, but change ONLY the glasses lens color.

TARGET: {target_color}, {intensity_desc}, {finish_desc}
{reflection_note}

WHAT TO CHANGE:
- Apply {target_color} tint only to the lens area inside the glasses frame
- Both lenses must have the same color treatment
- The tint should look like real tinted glass — eyes and face visible through at appropriate opacity
- Color edges must follow the inner frame edge precisely — no bleeding onto frame or skin

CRITICAL RULES:
- The output must be a 1:1 compositional match — same head size, crop, zoom, framing, and camera distance
- Do NOT change the aspect ratio — the output must have the same aspect ratio as the input
- Do NOT crop, zoom in/out, re-center, reframe, or change what is visible at the edges — no close-up
- The edges of the output must show the EXACT same content as the input — same background, same body parts visible
- The glasses FRAME must remain completely unchanged — only the lens color changes
- The person's face, skin, hair, expression, clothing, background, and lighting must remain IDENTICAL

OUTPUT: Return the edited photo. Same dimensions and quality as the input. Photorealistic — real tinted glasses, not a digital overlay."""

    return prompt
