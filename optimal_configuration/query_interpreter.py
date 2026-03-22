"""LLM-based query understanding — parse user intent from freeform text."""

import json
import re

from config import QUERY_INTERPRETER_MODEL


def interpret_query(user_query: str, api_key: str) -> dict:
    """
    Use Gemini Flash to extract structured intent from a freeform glasses query.

    Args:
        user_query: User's natural language query in any language.
        api_key: Gemini API key.

    Returns:
        {
            "search_text": "English description optimized for embedding search",
            "filters": {
                "max_price": <number or None>,
                "in_stock_only": True,
                "gender": <"men"/"women"/None>
            },
            "priorities": ["frame_shape", "price", ...],
            "original_language": "en"
        }
    """
    from google import genai

    client = genai.Client(api_key=api_key)

    prompt = f"""You are a glasses shopping assistant. A customer described the glasses they want. Your job is to extract their intent into structured data.

CUSTOMER QUERY: "{user_query}"

Respond ONLY with a JSON object (no markdown, no backticks, no explanation):
{{
    "search_text": "An English description of the glasses they want, optimized for semantic search against a product catalog. Include all visual and style preferences. Be descriptive.",
    "filters": {{
        "max_price": null,
        "in_stock_only": true,
        "gender": null
    }},
    "priorities": ["list what matters most to the user, in order. Choose from: frame_shape, frame_color, frame_material, frame_thickness, lens_type, lens_color, price, style, brand"],
    "original_language": "<ISO 639-1 code of the input language>"
}}

IMPORTANT:
- "max_price" should be a number if the user mentions a price constraint, otherwise null
- "gender" should be "men" or "women" only if explicitly stated, otherwise null
- "search_text" must be in ENGLISH regardless of the input language
- "search_text" should be rich and descriptive — include shape, color, material, style, occasion if mentioned"""

    response = client.models.generate_content(
        model=QUERY_INTERPRETER_MODEL,
        contents=[prompt],
    )

    raw_text = response.text.strip()

    # Strip markdown code fences if present
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: use the raw query as search text
        result = {
            "search_text": user_query,
            "filters": {"max_price": None, "in_stock_only": True, "gender": None},
            "priorities": [],
            "original_language": "unknown",
        }

    # Normalize filters
    filters = result.get("filters", {})
    if filters.get("max_price") is not None:
        try:
            filters["max_price"] = float(filters["max_price"])
        except (ValueError, TypeError):
            filters["max_price"] = None

    return result
