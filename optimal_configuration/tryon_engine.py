"""Nano Banana virtual try-on API integration."""

import time

from config import resolve_model
from tryon_prompt_builder import build_tryon_prompt
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
    product: dict,
    model_alias: str,
    api_key: str,
    retry_on_text_only: bool = True,
    portrait_part=None,
    aspect_ratio: str | None = None,
) -> TryOnResult:
    """
    Send portrait + glasses image to Nano Banana for virtual try-on.

    Args:
        portrait_path: Path to user's portrait photo.
        glasses_image_path: Path to product glasses image.
        product: Product dict with tags for prompt building.
        model_alias: "nano-banana-pro".
        api_key: Gemini API key.
        retry_on_text_only: Retry once if model returns text only.
        portrait_part: Pre-loaded portrait Part (skips loading from path if provided).

    Returns:
        TryOnResult with the try-on image bytes on success.
    """
    from google import genai
    from google.genai import types

    try:
        model_name = resolve_model(model_alias)
    except ValueError as e:
        return TryOnResult(success=False, error=str(e))

    # Build prompt
    prompt = build_tryon_prompt(product)

    # Load images
    try:
        if portrait_part is None:
            portrait_part = load_image_as_part(portrait_path)
        glasses_part = load_image_as_part(glasses_image_path)
    except (ValueError, FileNotFoundError) as e:
        return TryOnResult(success=False, error=f"Image loading error: {e}")

    # Initialize client
    client = genai.Client(api_key=api_key)

    # Make API call
    return _call_tryon_api(
        client=client,
        model_name=model_name,
        prompt=prompt,
        portrait_part=portrait_part,
        glasses_part=glasses_part,
        model_alias=model_alias,
        retry_on_text_only=retry_on_text_only,
        aspect_ratio=aspect_ratio,
    )


def _call_tryon_api(
    client,
    model_name: str,
    prompt: str,
    portrait_part,
    glasses_part,
    model_alias: str,
    retry_on_text_only: bool,
    is_retry: bool = False,
    aspect_ratio: str | None = None,
) -> TryOnResult:
    """Make the actual API call with retry logic."""
    from google.genai import types

    start_time = time.time()

    img_cfg = None
    if aspect_ratio:
        img_cfg = types.ImageConfig(aspect_ratio=aspect_ratio)

    try:
        response = client.models.generate_content(
            model=model_name,
            # Glasses first, portrait last — model adopts last image's aspect ratio
            contents=[prompt, glasses_part, portrait_part],
            config=types.GenerateContentConfig(
                temperature=0,
                response_modalities=["TEXT", "IMAGE"],
                image_config=img_cfg,
            ),
        )
    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = str(e)

        if "rate" in error_msg.lower() or "429" in error_msg:
            error_msg = (
                f"Rate limited. Please wait and try again.\nDetails: {error_msg}"
            )
        elif "safety" in error_msg.lower() or "blocked" in error_msg.lower():
            error_msg = (
                f"Image blocked by safety filters.\nDetails: {error_msg}"
            )
        elif "api key" in error_msg.lower() or "401" in error_msg or "403" in error_msg:
            error_msg = (
                f"API key issue. Ensure image generation is enabled.\nDetails: {error_msg}"
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

    # No image — retry once with simplified prompt
    if retry_on_text_only and not is_retry:
        print("  Model returned text only. Retrying...")
        retry_prompt = prompt + (
            "\n\nIMPORTANT: Return ONLY the edited portrait image with the "
            "glasses applied. Do not include any text in your response."
        )
        return _call_tryon_api(
            client=client,
            model_name=model_name,
            prompt=retry_prompt,
            portrait_part=portrait_part,
            glasses_part=glasses_part,
            model_alias=model_alias,
            retry_on_text_only=False,
            is_retry=True,
            aspect_ratio=aspect_ratio,
        )

    return TryOnResult(
        success=False,
        model_used=model_alias,
        elapsed_seconds=elapsed,
        error="The model did not return an image.",
        text_response=text_response,
    )
