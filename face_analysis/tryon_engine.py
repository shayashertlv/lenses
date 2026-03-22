"""Nano Banana 2-image virtual try-on engine.

Sends the user's portrait + matched product glasses photo to Nano Banana
for photorealistic virtual try-on.
"""

import time

from config import resolve_generation_model
from tryon_prompt import build_tryon_prompt
from utils import load_image_as_part


class TryOnResult:
    """Result of a virtual try-on operation."""

    def __init__(
        self,
        success: bool,
        image_bytes: bytes | None = None,
        model_used: str = "",
        elapsed_seconds: float = 0.0,
        error: str | None = None,
        text_response: str | None = None,
    ):
        self.success = success
        self.image_bytes = image_bytes
        self.model_used = model_used
        self.elapsed_seconds = elapsed_seconds
        self.error = error
        self.text_response = text_response


def virtual_tryon(
    portrait_path: str,
    glasses_image_path: str,
    analysis: dict,
    matched_product: dict,
    model_alias: str,
    api_key: str,
) -> TryOnResult:
    """
    Send portrait + glasses photo to Nano Banana for virtual try-on.

    Args:
        portrait_path: Path to user's portrait.
        glasses_image_path: Path to matched product's glasses photo.
        analysis: Full analysis dict (used for placement precision in prompt).
        matched_product: Matched product dict (used for glasses description).
        model_alias: "nano-banana-2" or "nano-banana-pro".
        api_key: Gemini API key.

    Returns:
        TryOnResult with the try-on image bytes on success.
    """
    from google import genai
    from google.genai import types

    try:
        model_name = resolve_generation_model(model_alias)
    except ValueError as e:
        return TryOnResult(success=False, error=str(e))

    # Build prompt from analysis + matched product
    prompt = build_tryon_prompt(analysis, matched_product)

    # Load images
    try:
        portrait_part = load_image_as_part(portrait_path)
        glasses_part = load_image_as_part(glasses_image_path)
    except (ValueError, FileNotFoundError) as e:
        return TryOnResult(success=False, error=f"Image loading error: {e}")

    client = genai.Client(api_key=api_key)

    # First attempt — full detailed prompt
    result = _call_nano_banana(
        client=client,
        model_name=model_name,
        prompt=prompt,
        portrait_part=portrait_part,
        glasses_part=glasses_part,
        model_alias=model_alias,
    )

    if result.success:
        return result

    # If no image returned, retry with simplified prompt
    if result.error and "No image returned" in result.error:
        retry_prompt = (
            "Place the glasses from the second image onto the person in "
            "the first image. Make it look like a real photo. Do not change "
            "anything else. Return ONLY the image."
        )
        result = _call_nano_banana(
            client=client,
            model_name=model_name,
            prompt=retry_prompt,
            portrait_part=portrait_part,
            glasses_part=glasses_part,
            model_alias=model_alias,
        )

    return result


def _call_nano_banana(
    client,
    model_name: str,
    prompt: str,
    portrait_part,
    glasses_part,
    model_alias: str,
) -> TryOnResult:
    """Make the actual Nano Banana API call."""
    from google.genai import types

    start_time = time.time()

    try:
        # Order: prompt first, then portrait (IMAGE 1), then glasses (IMAGE 2)
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt, portrait_part, glasses_part],
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )
    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = str(e)

        if "rate" in error_msg.lower() or "429" in error_msg:
            error_msg = (
                f"Rate limited. Please wait and try again.\n"
                f"Details: {error_msg}"
            )
        elif "safety" in error_msg.lower() or "blocked" in error_msg.lower():
            error_msg = (
                f"Image blocked by safety filters.\nDetails: {error_msg}"
            )
        elif "api key" in error_msg.lower() or "401" in error_msg or "403" in error_msg:
            error_msg = (
                f"API key issue. Ensure image generation is enabled.\n"
                f"Details: {error_msg}"
            )

        return TryOnResult(
            success=False,
            model_used=model_alias,
            elapsed_seconds=elapsed,
            error=error_msg,
        )

    elapsed = time.time() - start_time

    # Check for empty response
    if not response.candidates:
        return TryOnResult(
            success=False,
            model_used=model_alias,
            elapsed_seconds=elapsed,
            error="No response candidates. Image may have been blocked by safety filters.",
        )

    # Extract image from response
    output_image_bytes = None
    text_parts = []

    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            output_image_bytes = part.inline_data.data
        elif part.text:
            text_parts.append(part.text)

    text_response = "\n".join(text_parts) if text_parts else None

    if output_image_bytes is not None:
        return TryOnResult(
            success=True,
            image_bytes=output_image_bytes,
            model_used=model_alias,
            elapsed_seconds=elapsed,
            text_response=text_response,
        )

    return TryOnResult(
        success=False,
        model_used=model_alias,
        elapsed_seconds=elapsed,
        error="No image returned from Nano Banana API.",
        text_response=text_response,
    )
