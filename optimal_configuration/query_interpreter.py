"""LLM-based query understanding — parse user intent into structured tags."""

import json
import re

from config import QUERY_INTERPRETER_MODEL


def interpret_query(user_query: str, api_key: str) -> dict:
    """
    Use Gemini Flash to extract structured tags and filters from a freeform
    glasses query.

    Args:
        user_query: User's natural language query in any language.
        api_key: Gemini API key.

    Returns:
        {
            "search_tags": {
                "frame": {"shape": "round", "color": "gold", ...},
                "lenses": {"type": "sunglasses", ...},
                "style": {"aesthetic": "vintage", "gender_target": "women", ...}
            },
            "filters": {
                "max_price": <number or None>,
                "in_stock_only": True,
                "gender": <"men"/"women"/None>
            },
            "priorities": ["frame_shape", "price", ...],
            "original_language": "en",
            "search_text": "English description (kept for logging)"
        }
    """
    from google import genai

    client = genai.Client(api_key=api_key)

    prompt = f"""You are a glasses shopping assistant. A customer described the glasses they want. Extract their intent into structured tags for catalog matching.

CUSTOMER QUERY: "{user_query}"

Respond ONLY with a JSON object (no markdown, no backticks, no explanation):
{{
    "search_tags": {{
        "frame": {{
            "shape": "<one of: round, square, rectangular, oval, cat-eye, aviator, wayfarer, browline, hexagonal, octagonal, geometric, butterfly, oversized-round, oversized-square, wrap, shield, pilot — or null if not mentioned>",
            "material": "<one of: metal, acetate, titanium, plastic, TR90, wood, mixed-metal-acetate, stainless-steel, aluminum, carbon-fiber — or null>",
            "color": "<one or more of: black, silver, gold, rose-gold, gunmetal, tortoiseshell, havana-brown, transparent, white, blue, red, green, pink, purple, multicolor, matte-black, brushed-silver, champagne — as a list, or null>",
            "thickness": "<thin/medium/thick or null>",
            "finish": "<glossy/matte/brushed/textured/rubberized or null>",
            "rim_type": "<full-rim/semi-rimless/rimless or null>"
        }},
        "lenses": {{
            "type": "<one or more of: clear, prescription-ready, sunglasses, photochromic, blue-light-filter, polarized, mirrored, gradient — as a list, or null>",
            "color": "<one or more of: clear, gray, brown, green, blue, yellow, pink, orange, red, smoke, amber, rose, G-15-green, B-15-brown, mirrored-silver, mirrored-blue, mirrored-gold, gradient-gray, gradient-brown — as a list, or null>",
            "shape": "<round/rectangular/teardrop/square/oval/flat-top/curved-wrap or null>",
            "size": "<small/medium/large/oversized or null>"
        }},
        "style": {{
            "aesthetic": "<one or more of: classic, modern, vintage, retro, sporty, luxury, minimalist, bold, fashion-forward, professional, casual, streetwear, artsy, preppy, bohemian, industrial — as a list, or null>",
            "gender_target": "<unisex/men/women or null>",
            "face_shape_fit": "<one or more of: oval, round, square, heart, oblong, diamond, universal — as a list, or null>",
            "occasion": "<one or more of: everyday, office, outdoor, sport, driving, fashion, formal, beach — as a list, or null>"
        }}
    }},
    "filters": {{
        "max_price": null,
        "in_stock_only": true,
        "gender": null
    }},
    "priorities": ["list what matters most to the user, in order. Choose from: frame_shape, frame_color, frame_material, frame_thickness, lens_type, lens_color, price, style, brand"],
    "original_language": "<ISO 639-1 code of the input language>",
    "search_text": "<English description of what they want, for logging>"
}}

IMPORTANT:
- Only populate tag fields the user actually mentioned. Set others to null.
- "max_price" should be a number if the user mentions a price constraint, otherwise null.
- "gender" in filters should be "men" or "women" only if explicitly stated, otherwise null.
- All tag values MUST come from the allowed lists above — do not invent values.
- For fields that accept lists (color, type, aesthetic, etc.), use a JSON array even for single values."""

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
        # Fallback: empty tags — will return low scores but won't crash
        result = {
            "search_tags": {"frame": {}, "lenses": {}, "style": {}},
            "filters": {"max_price": None, "in_stock_only": True, "gender": None},
            "priorities": [],
            "original_language": "unknown",
            "search_text": user_query,
        }

    # Normalize filters
    filters = result.get("filters", {})
    if filters.get("max_price") is not None:
        try:
            filters["max_price"] = float(filters["max_price"])
        except (ValueError, TypeError):
            filters["max_price"] = None

    # Strip null values from search_tags so tag_matcher skips them cleanly
    search_tags = result.get("search_tags", {})
    for category in ("frame", "lenses", "style"):
        cat = search_tags.get(category, {})
        search_tags[category] = {k: v for k, v in cat.items() if v is not None}
    result["search_tags"] = search_tags

    return result
