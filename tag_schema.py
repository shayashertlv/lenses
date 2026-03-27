"""Tag definitions, validation, and auto-description generator."""

TAG_SCHEMA = {
    "frame": {
        "shape": [
            "round", "square", "rectangular", "oval", "cat-eye",
            "aviator", "wayfarer", "browline", "hexagonal", "octagonal",
            "geometric", "butterfly", "oversized-round", "oversized-square",
            "wrap", "shield", "pilot",
        ],
        "material": [
            "metal", "acetate", "titanium", "plastic", "TR90",
            "wood", "mixed-metal-acetate", "stainless-steel",
            "aluminum", "carbon-fiber",
        ],
        "color": [
            "black", "silver", "gold", "rose-gold", "gunmetal",
            "tortoiseshell", "havana-brown", "transparent", "white",
            "blue", "red", "green", "pink", "purple", "multicolor",
            "matte-black", "brushed-silver", "champagne",
        ],
        "thickness": ["thin", "medium", "thick"],
        "finish": ["glossy", "matte", "brushed", "textured", "rubberized"],
        "rim_type": ["full-rim", "semi-rimless", "rimless"],
    },
    "lenses": {
        "type": [
            "clear", "prescription-ready", "sunglasses", "photochromic",
            "blue-light-filter", "polarized", "mirrored", "gradient",
        ],
        "color": [
            "clear", "gray", "brown", "green", "blue", "yellow",
            "pink", "orange", "red", "smoke", "amber", "rose",
            "G-15-green", "B-15-brown", "mirrored-silver",
            "mirrored-blue", "mirrored-gold", "gradient-gray",
            "gradient-brown",
        ],
        "shape": [
            "round", "rectangular", "teardrop", "square", "oval",
            "flat-top", "curved-wrap",
        ],
        "size": ["small", "medium", "large", "oversized"],
    },
    "style": {
        "aesthetic": [
            "classic", "modern", "vintage", "retro", "sporty",
            "luxury", "minimalist", "bold", "fashion-forward",
            "professional", "casual", "streetwear", "artsy",
            "preppy", "bohemian", "industrial",
        ],
        "gender_target": ["unisex", "men", "women"],
        "face_shape_fit": [
            "oval", "round", "square", "heart", "oblong", "diamond",
            "universal",
        ],
        "occasion": [
            "everyday", "office", "outdoor", "sport", "driving",
            "fashion", "formal", "beach",
        ],
    },
    "product": {
        "brand": "string",
        "model_name": "string",
        "price": "float",
        "currency": "string",
        "in_stock": "boolean",
        "stock_quantity": "integer",
        "sku": "string",
    },
}


def _join(value) -> str:
    """Join a value that may be a list or a single string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def generate_product_description(product: dict) -> str:
    """
    Convert structured product tags into a rich, natural-language description
    optimized for embedding-based semantic search.

    The description captures EVERY searchable attribute in natural language so
    that a user's freeform query (in any phrasing) will have high cosine
    similarity with the right products.
    """
    tags = product["tags"]
    frame = tags["frame"]
    lenses = tags["lenses"]
    style = tags["style"]
    prod = tags["product"]

    # Build frame description
    frame_colors = _join(frame["color"])
    frame_desc = (
        f"{frame.get('thickness', '')} {frame.get('finish', '')} {frame_colors} {frame.get('material', '')} "
        f"{frame.get('rim_type', '')} frame with a {frame.get('shape', '')} shape"
    )

    # Build lens description
    lens_types = _join(lenses["type"])
    lens_colors = _join(lenses["color"])
    lens_desc = (
        f"{lenses.get('size', '')} {lenses.get('shape', '')} {lens_colors} {lens_types} lenses"
    )

    # Build style description
    aesthetics = _join(style["aesthetic"])
    occasions = _join(style["occasion"])
    fit = _join(style["face_shape_fit"])
    style_desc = (
        f"{aesthetics} style, designed for {style['gender_target']}, "
        f"suitable for {occasions}, fits {fit} face shapes"
    )

    # Build price description
    price_desc = f"priced at {prod['price']} {prod.get('currency', 'USD')}"
    stock_desc = "currently in stock" if prod.get("in_stock", True) else "currently out of stock"

    # Combine into coherent paragraph
    full_desc = (
        f"{product['name']} by {prod.get('brand', 'Unknown')}. "
        f"These glasses feature a {frame_desc}. "
        f"They have {lens_desc}. "
        f"The overall look is {style_desc}. "
        f"{price_desc}, {stock_desc}."
    )

    return full_desc


def validate_tags(tags: dict) -> list[str]:
    """
    Validate product tags against the schema.
    Returns a list of warning messages (empty if all valid).
    """
    warnings = []

    for category in ("frame", "lenses", "style"):
        if category not in tags:
            warnings.append(f"Missing tag category: {category}")
            continue

        schema_cat = TAG_SCHEMA[category]
        tag_cat = tags[category]

        for field, allowed_values in schema_cat.items():
            if field not in tag_cat:
                warnings.append(f"Missing {category}.{field}")
                continue

            if isinstance(allowed_values, list):
                value = tag_cat[field]
                values = value if isinstance(value, list) else [value]
                for v in values:
                    if v not in allowed_values:
                        warnings.append(
                            f"Unknown value '{v}' for {category}.{field}"
                        )

    if "product" not in tags:
        warnings.append("Missing tag category: product")

    return warnings
