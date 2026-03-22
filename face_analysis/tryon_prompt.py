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
- Frame: {pf["shape"]} shape, {pf["material"]} material, {pf_colors} color, {pf["thickness"]} thickness, {pf["finish"]} finish, {pf["rim_type"]}
- Lenses: {pl_types} type, {pl_colors} color, {pl["size"]} size, {pl["shape"]} lens shape

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
- The photo composition, framing, angle, and resolution must remain IDENTICAL

REALISM REQUIREMENTS:
- Add natural shadows where the glasses frame contacts the face — subtle shadow under the bridge on the nose, faint shadow from frame on cheeks
- The shadow direction must be consistent with the existing lighting in the photo ({lighting.replace("_", " ")})
- Frame material must look realistic: {pf["material"]} with {pf["finish"]} finish should show appropriate surface properties
- Lenses must interact with light realistically for {pl_types} lenses
- The glasses must appear to have physical weight and three-dimensional presence
- Faithfully reproduce the exact frame design, proportions, and details from IMAGE 2

OUTPUT: Return ONLY the edited portrait with the glasses applied. Maintain the EXACT same dimensions, quality, and format as IMAGE 1. The result must be photorealistic — indistinguishable from a real photograph."""

    return prompt
