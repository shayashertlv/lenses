"""Core recoloring logic — API call + prompt construction."""

import time

from config import get_api_key, resolve_model
from prompt_engine import build_lens_recolor_prompt
from utils import load_image_bytes, save_image


class RecolorResult:
    """Result of a lens recolor operation."""

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


def recolor_lenses(
    image_path: str,
    target_color: str,
    model_alias: str = "nano-banana-2",
    intensity: str = "medium",
    finish: str = "standard",
    preserve_reflections: bool = True,
    retry_on_text_only: bool = True,
) -> RecolorResult:
    """
    Send an image to the Gemini API to recolor glasses lenses.

    Args:
        image_path: Path to the input image.
        target_color: Desired lens color (natural language).
        model_alias: Model to use ("nano-banana-2" or "nano-banana-pro").
        intensity: Tint level — "light", "medium", "dark".
        finish: Lens style — "standard", "gradient", "mirror", "polarized".
        preserve_reflections: Keep natural reflections on lenses.
        retry_on_text_only: If True, retry once if model returns text only.

    Returns:
        RecolorResult with image bytes on success, error info on failure.
    """
    # Resolve model
    try:
        model_name = resolve_model(model_alias)
    except ValueError as e:
        return RecolorResult(success=False, error=str(e))

    # Load image
    try:
        image_bytes, mime_type = load_image_bytes(image_path)
    except (ValueError, FileNotFoundError) as e:
        return RecolorResult(success=False, error=str(e))

    # Build prompt
    prompt_text = build_lens_recolor_prompt(
        target_color=target_color,
        intensity=intensity,
        finish=finish,
        preserve_reflections=preserve_reflections,
    )

    # Import Google GenAI SDK (lazy to allow dry-run without install)
    from google import genai
    from google.genai import types

    # Create image part
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    # Initialize client
    try:
        api_key = get_api_key()
    except RuntimeError as e:
        return RecolorResult(success=False, error=str(e))

    client = genai.Client(api_key=api_key)

    # Make API call
    return _call_api(
        client=client,
        model_name=model_name,
        prompt_text=prompt_text,
        image_part=image_part,
        model_alias=model_alias,
        retry_on_text_only=retry_on_text_only,
    )


def _call_api(
    client,
    model_name: str,
    prompt_text: str,
    image_part,
    model_alias: str,
    retry_on_text_only: bool,
    is_retry: bool = False,
) -> RecolorResult:
    """Make the actual API call, with optional retry logic."""
    from google.genai import types

    start_time = time.time()

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt_text, image_part],
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"]
            ),
        )
    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = str(e)

        # Check for common error patterns
        if "rate" in error_msg.lower() or "429" in error_msg:
            error_msg = (
                f"Rate limited by the API. Please wait a moment and try again.\n"
                f"Details: {error_msg}"
            )
        elif "safety" in error_msg.lower() or "blocked" in error_msg.lower():
            error_msg = (
                f"The image was blocked by safety filters. This can happen with "
                f"certain types of images.\nDetails: {error_msg}"
            )
        elif "api key" in error_msg.lower() or "401" in error_msg or "403" in error_msg:
            error_msg = (
                f"API key issue. Ensure your GEMINI_API_KEY is valid and has "
                f"image generation enabled (requires a paid plan).\nDetails: {error_msg}"
            )

        return RecolorResult(
            success=False,
            model_used=model_alias,
            elapsed_seconds=elapsed,
            error=error_msg,
        )

    elapsed = time.time() - start_time

    # Check for safety blocks in response
    if not response.candidates:
        return RecolorResult(
            success=False,
            model_used=model_alias,
            elapsed_seconds=elapsed,
            error="No response candidates returned. The image may have been blocked by safety filters.",
        )

    # Extract image from response parts
    output_image_bytes = None
    text_parts = []

    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            output_image_bytes = part.inline_data.data
        elif part.text:
            text_parts.append(part.text)

    text_response = "\n".join(text_parts) if text_parts else None

    if output_image_bytes is not None:
        return RecolorResult(
            success=True,
            image_bytes=output_image_bytes,
            model_used=model_alias,
            elapsed_seconds=elapsed,
            text_response=text_response,
        )

    # No image in response — retry once with modified prompt
    if retry_on_text_only and not is_retry:
        print("  Model returned text only. Retrying with image-focused prompt...")
        retry_prompt = prompt_text + "\n\nReturn ONLY the edited image, no text explanation needed."
        retry_part = types.Part.from_text(retry_prompt)
        # Rebuild contents with the modified prompt
        return _call_api(
            client=client,
            model_name=model_name,
            prompt_text=retry_prompt,
            image_part=image_part,
            model_alias=model_alias,
            retry_on_text_only=False,
            is_retry=True,
        )

    return RecolorResult(
        success=False,
        model_used=model_alias,
        elapsed_seconds=elapsed,
        error="The model did not return an image.",
        text_response=text_response,
    )
