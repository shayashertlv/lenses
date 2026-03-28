"""Build the 2-image try-on prompt from face analysis + matched product.

Enhanced with face analysis data for more precise glasses placement compared
to optimal_configuration's generic try-on prompt.
"""


def _join(value) -> str:
    """Join a value that may be a list or a single string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


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

    # Product tags (from the ACTUAL matched product, not the recommendation)
    prod_tags = matched_product["tags"]
    pf = prod_tags["frame"]
    pl = prod_tags["lenses"]

    pf_colors = _join(pf["color"])
    pl_types = _join(pl["type"])
    pl_colors = _join(pl["color"])

    # Face context for placement precision
    nose_bridge = fa["nose"]["bridge_width"]
    nose_height = fa["nose"]["bridge_height"]
    eye_spacing = fa["eyes"]["spacing"]
    face_angle = fa.get("photo_context", {}).get("face_angle", "straight_on")
    lighting = fa.get("photo_context", {}).get("lighting", "natural_indoor")

    angle_instruction = ""
    if face_angle != "straight_on":
        angle_instruction = (
            f"\nCRITICAL: The person's face is at a "
            f"{face_angle.replace('_', ' ')} angle. "
            f"The glasses MUST follow this exact 3D rotation — both the frame "
            f"and lenses must match the perspective of the face precisely. "
            f"Do NOT place the glasses straight if the face is turned."
        )

    # Facial hair instruction
    if fa["facial_hair"]["present"]:
        facial_hair_note = (
            f'Facial hair ({fa["facial_hair"]["type"]}) must remain identical'
        )
    else:
        facial_hair_note = "No facial hair present — keep face clean"

    prompt = f"""I am providing two images:
- IMAGE 1 (first image): A portrait photograph of a person. This is the person who will try on the glasses.
- IMAGE 2 (second image): A product photograph of a specific pair of glasses. These are the exact glasses to place on the person.

YOUR TASK: Create a new version of IMAGE 1 where the person is wearing the glasses from IMAGE 2. The output must look like a real photograph — as if the person was actually wearing these exact glasses when the photo was taken.

GLASSES DETAILS (from IMAGE 2 — reproduce these faithfully):
- Frame: {pf.get("shape", "classic")} shape, {pf.get("material", "")} material, {pf_colors} color, {pf.get("thickness", "")} thickness, {pf.get("finish", "")} finish, {pf.get("rim_type", "")}
- Lenses: {pl_types} type, {pl_colors} color, {pl.get("size", "")} size, {pl.get("shape", "")} lens shape

FACE-SPECIFIC PLACEMENT (based on facial analysis of this person):
- This person has a {nose_bridge} nose bridge with {nose_height} bridge height — position the glasses bridge accordingly, ensuring natural contact
- Eye spacing is {eye_spacing} — center each lens over each eye accounting for this spacing
- The frame width should align with this person's face width at the temples
{angle_instruction}

GLASSES PLACEMENT RULES:
- Position the glasses centered on the person's face with the bridge resting naturally on the nose
- The temple arms should extend from the frame hinges toward the ears, following the natural path along the side of the head
- If hair overlaps where temple arms would be, render the overlap naturally with hair partially covering the arms
- Scale the glasses proportionally to the person's face — they should look like they genuinely fit this person
- Both lenses must sit level with the person's eye line

CRITICAL PRESERVATION RULES — DO NOT CHANGE ANY OF THE FOLLOWING:
- The person's face, skin tone, skin texture, facial features, expression, and any makeup must remain COMPLETELY IDENTICAL
- The person's eyes must be visible through the lenses at the appropriate opacity for {pl_types} lenses with {pl_colors} tint
- The person's hair must remain IDENTICAL
- {facial_hair_note}
- The person's clothing and accessories must remain IDENTICAL
- The background must remain COMPLETELY IDENTICAL
- The overall lighting ({lighting.replace("_", " ")}), shadows, and color grading must remain IDENTICAL

FRAMING / ZOOM — THIS IS CRITICAL:
- Maintain the exact same head size, framing, zoom level, and camera distance as IMAGE 1
- Do not crop, zoom in, or re-center the subject's face
- Do NOT zoom out, pull back, or reveal more of the scene than IMAGE 1 shows
- The person's head, shoulders, and body must occupy the EXACT same area and proportion of the frame as in IMAGE 1
- If IMAGE 1 shows the person from the chest up, the output must show the person from the chest up at the same scale
- The edges of the frame must show the same content as IMAGE 1 — do not add extra background, body, or space
- The output must be a 1:1 compositional match to the original image, only applying the described glasses

REALISM REQUIREMENTS:
- Add natural shadows where the glasses frame contacts the face — subtle shadow under the bridge on the nose, faint shadow from frame on cheeks
- The shadow direction must be consistent with the existing lighting in the photo ({lighting.replace("_", " ")})
- Frame material must look realistic: {pf["material"]} with {pf["finish"]} finish should show appropriate surface properties
- Lenses must interact with light realistically for {pl_types} lenses
- The glasses must appear to have physical weight and three-dimensional presence

VISUAL FIDELITY TO IMAGE 2 — THIS IS CRITICAL:
- The glasses in the output must be a faithful reproduction of the EXACT glasses shown in IMAGE 2 — use IMAGE 2 as your primary visual reference, not just the text description above
- Copy the frame design, proportions, decorative details, temple arm style, and nose pad type directly from IMAGE 2
- Do NOT redesign, simplify, or stylize the glasses — they must look like the same physical pair from IMAGE 2
- If IMAGE 2 shows specific details (brand logos, hinge style, screw details, engraving), reproduce them

IDENTITY PRESERVATION — DO NOT ALTER THE PERSON:
- Do NOT smooth, beautify, reshape, or enhance the person's facial features in any way
- The person's face must be pixel-identical to IMAGE 1 in every area not occluded by the glasses
- Skin texture, pores, blemishes, wrinkles, and any imperfections must remain exactly as they are
- Do NOT change eye color, lip color, eyebrow shape, or any other feature

OUTPUT: Return ONLY the edited portrait with the glasses applied. Maintain the EXACT same dimensions, quality, and format as IMAGE 1. The result must be photorealistic — indistinguishable from a real photograph."""

    return prompt
