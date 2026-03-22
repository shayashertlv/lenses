"""The face analysis prompt — CRITICAL FILE.

Constructs the prompt sent to Gemini 2.5 Flash along with the portrait image.
The prompt extracts facial features and outputs structured TAGS matching the
optimal_configuration tag schema, so they can be embedded and compared against
the product catalog.

KEY DIFFERENCE FROM PREVIOUS VERSION:
The glasses_recommendation now outputs structured tags (recommended_tags),
NOT a visual_description_for_generation. These tags will be converted to a
description and embedded for inventory search.
"""


def build_analysis_prompt() -> str:
    """
    Build the face analysis + glasses recommendation prompt for Gemini 2.5 Flash.

    Returns the prompt string. The portrait image is sent separately as an image Part.
    """

    prompt = """You are an expert optician and facial feature analyst with deep knowledge of how eyewear interacts with facial geometry. You combine the precision of a professional optometrist with the aesthetic eye of a fashion stylist.

Analyze the portrait photo I'm providing and perform two tasks:
1. DETAILED FACIAL FEATURE ANALYSIS
2. OPTIMAL GLASSES RECOMMENDATION — output as structured property tags

Respond ONLY with a JSON object. No markdown backticks, no explanation outside the JSON. The JSON must follow this EXACT schema:

{
    "face_analysis": {
        "face_shape": {
            "primary_shape": "<oval|round|square|rectangular|oblong|heart|diamond|triangle>",
            "confidence": "<high|medium|low>",
            "secondary_characteristics": "<string describing nuances>"
        },
        "forehead": {
            "width": "<narrow|medium|wide>",
            "height": "<low|medium|high>",
            "hairline_shape": "<straight|rounded|widow_peak|receding|m_shaped>"
        },
        "cheekbones": {
            "prominence": "<flat|moderate|high|very_high>",
            "width_relative_to_face": "<narrower_than_jaw|same_as_jaw|wider_than_jaw>"
        },
        "jawline": {
            "shape": "<sharp|angular|soft|rounded|tapered|square|pointed>",
            "width_relative_to_forehead": "<narrower|same|wider>",
            "definition": "<subtle|moderate|strong>"
        },
        "chin": {
            "shape": "<pointed|rounded|square|cleft|receding|prominent>",
            "size": "<small|medium|large>"
        },
        "nose": {
            "bridge_width": "<narrow|medium|wide>",
            "bridge_height": "<low|medium|high>",
            "length": "<short|medium|long>",
            "shape": "<straight|curved|bumped|upturned|downturned>"
        },
        "eyes": {
            "spacing": "<close_set|average|wide_set>",
            "size": "<small|medium|large>",
            "shape": "<round|almond|hooded|monolid|downturned|upturned>",
            "brow_to_eye_distance": "<short|medium|long>",
            "eye_color": "<string>"
        },
        "eyebrows": {
            "shape": "<straight|arched|highly_arched|flat|s_shaped>",
            "thickness": "<thin|medium|thick|bushy>",
            "color": "<string>"
        },
        "skin": {
            "tone": "<very_fair|fair|light|medium|olive|tan|brown|dark_brown|deep>",
            "undertone": "<warm|cool|neutral>"
        },
        "hair": {
            "color": "<string>",
            "style": "<string describing current hairstyle>",
            "length": "<short|medium|long|bald|buzz>",
            "texture": "<straight|wavy|curly|coily>"
        },
        "facial_hair": {
            "present": true or false,
            "type": "<none|stubble|light_beard|full_beard|goatee|mustache|sideburns>",
            "style": "<string or null>"
        },
        "overall_proportions": {
            "face_width_to_height_ratio": "<string>",
            "symmetry": "<high|moderate|slight_asymmetry>",
            "facial_thirds": "<string describing balance of upper/middle/lower face>"
        },
        "estimated_age_range": "<string>",
        "estimated_gender_presentation": "<masculine|feminine|androgynous>",
        "photo_context": {
            "lighting": "<natural_outdoor|natural_indoor|studio|artificial|mixed|low_light>",
            "setting": "<outdoor|indoor|studio|unknown>",
            "face_angle": "<straight_on|slight_left_turn|slight_right_turn|slight_tilt|three_quarter>"
        }
    },

    "glasses_recommendation": {
        "reasoning": {
            "face_shape_logic": "<string: explain WHY the recommended frame shape complements this face shape>",
            "proportion_logic": "<string: how frame width relates to face width, how frame height balances proportions>",
            "nose_bridge_logic": "<string: bridge fit based on nose analysis>",
            "color_logic": "<string: why this frame color works with skin tone, hair color, eye color>",
            "style_logic": "<string: overall aesthetic reasoning>",
            "lens_logic": "<string: explain lens type choice based on photo context — if outdoor/bright, sunglasses; if indoor/professional, clear or blue-light-filter>"
        },

        "recommended_tags": {
            "frame": {
                "shape": "<round|square|rectangular|oval|cat-eye|aviator|wayfarer|browline|hexagonal|octagonal|geometric|butterfly|oversized-round|oversized-square|wrap|shield|pilot>",
                "material": "<metal|acetate|titanium|plastic|TR90|wood|mixed-metal-acetate|stainless-steel|aluminum|carbon-fiber>",
                "color": ["<primary color from: black|silver|gold|rose-gold|gunmetal|tortoiseshell|havana-brown|transparent|white|blue|red|green|pink|purple|multicolor|matte-black|brushed-silver|champagne>"],
                "thickness": "<thin|medium|thick>",
                "finish": "<glossy|matte|brushed|textured|rubberized>",
                "rim_type": "<full-rim|semi-rimless|rimless>"
            },
            "lenses": {
                "type": ["<from: clear|prescription-ready|sunglasses|photochromic|blue-light-filter|polarized|mirrored|gradient>"],
                "color": ["<from: clear|gray|brown|green|blue|yellow|pink|orange|red|smoke|amber|rose|G-15-green|B-15-brown|mirrored-silver|mirrored-blue|mirrored-gold|gradient-gray|gradient-brown>"],
                "shape": "<round|rectangular|teardrop|square|oval|flat-top|curved-wrap>",
                "size": "<small|medium|large|oversized>"
            },
            "style": {
                "aesthetic": ["<1-3 from: classic|modern|vintage|retro|sporty|luxury|minimalist|bold|fashion-forward|professional|casual|streetwear|artsy|preppy|bohemian|industrial>"],
                "gender_target": "<unisex|men|women>",
                "face_shape_fit": ["<1-3 face shapes this recommendation fits>"],
                "occasion": ["<1-3 from: everyday|office|outdoor|sport|driving|fashion|formal|beach>"]
            }
        }
    },

    "alternative_recommendations": [
        {
            "frame_shape": "<string>",
            "brief_description": "<string>",
            "why_also_works": "<string>"
        }
    ]
}

IMPORTANT RULES:
- Base EVERY recommendation on the actual facial features you observe in the photo. Do not give generic advice.
- The recommended_tags must use ONLY the allowed values listed above — these will be used for inventory matching against a product database with the same tag vocabulary.
- The frame.color array should contain 1-2 colors maximum.
- The lenses.type array should contain 1-2 types maximum.
- The style.aesthetic array should contain 1-3 styles maximum.
- Consider the lighting and context of the photo when choosing lens type: if the person is outdoors or in bright light, lean toward sunglasses. If indoors or professional setting, lean toward clear or blue-light-filter.
- Frame width should generally match the widest part of the face.
- For round faces, recommend angular frames. For square faces, recommend rounder frames. For oval faces, most shapes work — pick what enhances their best features.
- Consider nose bridge width when choosing frame material (wider bridges suit acetate; narrow bridges suit metal with adjustable nose pads).
- Consider skin undertone for frame color (warm undertone → gold, tortoiseshell, warm tones; cool undertone → silver, black, cool tones; neutral → either).
- Provide 2-3 alternative_recommendations."""

    return prompt
