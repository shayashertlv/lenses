"""Build the virtual try-on prompt, adapted to each product's properties."""


def _join(value) -> str:
    """Join a value that may be a list or a single string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def build_tryon_prompt(product: dict) -> str:
    """
    Build a detailed virtual try-on prompt for Nano Banana.

    The prompt references two images:
    - IMAGE 1: The user's portrait photograph
    - IMAGE 2: The product glasses photograph

    The prompt adapts based on the matched product's actual properties.
    """
    tags = product["tags"]
    frame = tags["frame"]
    lenses = tags["lenses"]

    frame_colors = _join(frame["color"])
    lens_types = _join(lenses["type"])
    lens_colors = _join(lenses["color"])

    prompt = f"""I am providing two images:
- IMAGE 1 (first image): A portrait photograph of a person who wants to try on glasses.
- IMAGE 2 (second image): A product photograph of glasses/eyewear to be placed on the person.

YOUR TASK: Create a new version of IMAGE 1 (the portrait) where the person is wearing the glasses shown in IMAGE 2. The output must look like a real photograph — as if the person was actually wearing these glasses when the photo was taken.

GLASSES DETAILS (from IMAGE 2 — faithfully reproduce these):
- Frame: {frame.get("shape", "classic")} shape, {frame.get("material", "")} material, {frame_colors} color, {frame.get("thickness", "")} thickness, {frame.get("finish", "")} finish, {frame.get("rim_type", "")}
- Lenses: {lens_types} type, {lens_colors} color, {lenses.get("size", "")} size, {lenses.get("shape", "")} shape

GLASSES PLACEMENT RULES:
- Position the glasses naturally on the person's face — bridge of the nose, temples extending toward or behind the ears
- The glasses must match the person's face angle, tilt, and perspective EXACTLY — if the face is slightly turned, the glasses must follow the same 3D rotation
- Scale the glasses proportionally to the person's face — they should look like they genuinely fit
- The temple arms should follow the natural path behind/over the ears, partially hidden by hair if applicable
- If the person's ears are visible, the temple arms should rest naturally on them

CRITICAL PRESERVATION RULES FOR THE PORTRAIT (IMAGE 1):
- The person's face, skin tone, skin texture, facial features, expression, and makeup must remain COMPLETELY IDENTICAL
- The person's eyes should be visible through the lenses at the appropriate opacity for {lens_types} lenses with {lens_colors} tint
- The person's hair (color, style, texture, position, stray hairs) must remain IDENTICAL — where hair overlaps the temple arms, render this naturally
- The person's clothing, accessories, jewelry must remain IDENTICAL
- The background must remain COMPLETELY IDENTICAL
- The overall lighting, shadows, and color grading must remain IDENTICAL

FRAMING / ZOOM — THIS IS CRITICAL:
- Maintain the exact same head size, framing, zoom level, and camera distance as IMAGE 1
- Do not crop, zoom in, or re-center the subject's face
- Do NOT zoom out, pull back, or reveal more of the scene than IMAGE 1 shows
- The person's head, shoulders, and body must occupy the EXACT same area and proportion of the frame as in IMAGE 1
- If IMAGE 1 shows the person from the chest up, the output must show the person from the chest up at the same scale
- The edges of the frame must show the same content as IMAGE 1 — do not add extra background, body, or space
- The output must be a 1:1 compositional match to the original image, only applying the described glasses

REALISM REQUIREMENTS:
- Add natural shadows where the glasses frame touches the face (bridge of nose, temples)
- If there is directional lighting in the portrait, the glasses should cast consistent shadows
- The lenses should show appropriate reflections based on the lighting conditions in the portrait
- Where the frame overlaps skin, render the boundary cleanly and naturally
- The glasses should look like they have physical weight and presence — not like a flat overlay

VISUAL FIDELITY TO IMAGE 2 — THIS IS CRITICAL:
- The glasses in the output must be a faithful reproduction of the EXACT glasses shown in IMAGE 2 — use IMAGE 2 as your primary visual reference, not just the text description above
- Copy the frame design, proportions, decorative details, temple arm style, and nose pad type directly from IMAGE 2
- Frame color, material texture, and finish must match IMAGE 2 exactly
- Lens color, tint level, and finish type must match IMAGE 2 exactly
- Do NOT redesign, simplify, or stylize the glasses — they must look like the same physical pair from IMAGE 2
- If IMAGE 2 shows specific details (brand logos, hinge style, screw details, engraving), reproduce them

IDENTITY PRESERVATION — DO NOT ALTER THE PERSON:
- Do NOT smooth, beautify, reshape, or enhance the person's facial features in any way
- The person's face must be pixel-identical to IMAGE 1 in every area not occluded by the glasses
- Skin texture, pores, blemishes, wrinkles, and any imperfections must remain exactly as they are
- Do NOT change eye color, lip color, eyebrow shape, or any other feature

OUTPUT: Return ONLY the edited portrait photograph with the glasses applied. Maintain the EXACT same dimensions, quality, and format as IMAGE 1. The result must be completely photorealistic."""

    return prompt
