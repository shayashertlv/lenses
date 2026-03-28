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
- IMAGE 1: A portrait photo of a person.
- IMAGE 2: A product photo of glasses.

YOUR TASK: Create the EXACT same photo as IMAGE 1, but with the person wearing the glasses from IMAGE 2. The result must look like a real photograph — as if the person was already wearing these glasses when the photo was taken.

GLASSES FROM IMAGE 2:
- Frame: {frame.get("shape", "classic")}, {frame.get("material", "")}, {frame_colors}, {frame.get("rim_type", "")}
- Lenses: {lens_types}, {lens_colors}
- Reproduce the glasses from IMAGE 2 faithfully — same design, proportions, and details.

PLACEMENT:
- Position naturally on the face — bridge on nose, temples toward ears
- Match the person's face angle and perspective exactly
- Scale proportionally to the face
- Eyes visible through lenses at appropriate opacity for {lens_types} lenses with {lens_colors} tint

CRITICAL RULES:
- The output must be a 1:1 compositional match to IMAGE 1 — same head size, crop, zoom, framing, and camera distance
- Do NOT change the aspect ratio of IMAGE 1 — the output must have the same aspect ratio as IMAGE 1
- Do NOT crop, zoom in/out, re-center, reframe, or change what is visible at the edges — no close-up
- The edges of the output must show the EXACT same content as IMAGE 1 — same background, same body parts visible, same space above/below/around the head
- Keep everything else in the image exactly the same, preserving the original style, lighting, and composition
- The person's face, skin, hair, expression, clothing, background, and lighting must remain IDENTICAL
- Do NOT alter, smooth, or enhance any facial features
- Add realistic shadows from the glasses consistent with the existing lighting

OUTPUT: Return ONLY the edited photo. Same dimensions and quality as IMAGE 1. Photorealistic."""

    return prompt
