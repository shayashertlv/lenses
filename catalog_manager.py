"""Catalog management — generate descriptions, validate, list.

This manages the Erroca men's eyeglasses catalog in lenses/catalog/.
"""

import json
import sys
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
CATALOG_DIR = BASE_DIR / "lenses" / "catalog"
CATALOG_JSON = CATALOG_DIR / "catalog.json"
CATALOG_IMAGES_DIR = CATALOG_DIR / "images"

from tag_schema import generate_product_description, validate_tags


def _load_catalog() -> dict:
    """Load catalog.json."""
    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_catalog(catalog: dict) -> None:
    """Save catalog.json."""
    with open(CATALOG_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


def build_descriptions() -> None:
    """
    Read catalog.json, regenerate each product's natural-language description
    from its tags, and save back to disk. Run this after adding/changing products.

    Descriptions are a pure template over the product tags — no API call.
    """
    catalog = _load_catalog()
    products = catalog["products"]

    if not products:
        print("Error: No products in catalog.")
        sys.exit(1)

    print(f"Generating descriptions for {len(products)} products...")

    for product in products:
        desc = generate_product_description(product)
        product["description_auto"] = desc
        print(f"  [{product['id']}] {product['name']}")
        print(f"    Description: {desc[:100]}...")

    _save_catalog(catalog)
    print(f"\nDescriptions saved to {CATALOG_JSON}")
    print("\nDone!")


def validate_catalog() -> None:
    """Check that all images exist and tags are valid."""
    catalog = _load_catalog()
    products = catalog["products"]
    all_ok = True

    for product in products:
        pid = product["id"]
        name = product["name"]
        print(f"\n[{pid}] {name}")

        # Check image exists
        image_path = CATALOG_DIR / product["image"]
        if image_path.exists():
            print(f"  Image: OK ({image_path})")
        else:
            print(f"  Image: MISSING ({image_path})")
            all_ok = False

        # Validate tags
        warnings = validate_tags(product["tags"])
        if warnings:
            for w in warnings:
                print(f"  Warning: {w}")
            all_ok = False
        else:
            print("  Tags: OK")

    if all_ok:
        print(f"\nAll {len(products)} products validated successfully.")
    else:
        print("\nSome issues found — see warnings above.")


def list_products() -> None:
    """List all products in the catalog."""
    catalog = _load_catalog()
    products = catalog["products"]

    print(f"Catalog v{catalog['catalog_version']} — {len(products)} products\n")

    for product in products:
        prod = product["tags"]["product"]
        frame = product["tags"]["frame"]
        stock = "In Stock" if prod.get("in_stock", False) else "Out of Stock"
        colors = ", ".join(frame["color"]) if isinstance(frame["color"], list) else frame["color"]
        print(f"  [{product['id']}] {product['name']}")
        print(f"    Brand: {prod.get('brand', '?')} | Shape: {frame.get('shape', '?')} | "
              f"Colors: {colors} | Price: {prod['price']} {prod.get('currency', 'ILS')} | {stock}")

        if product.get("description_auto"):
            print(f"    Desc: {product['description_auto'][:80]}...")
        print()


def main():
    if len(sys.argv) < 2:
        print("Usage: python catalog_manager.py <command>")
        print("Commands:")
        print("  build     — Regenerate product descriptions from tags")
        print("  validate  — Check images exist and tags are valid")
        print("  list      — List all products")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "build":
        build_descriptions()
    elif command == "validate":
        validate_catalog()
    elif command == "list":
        list_products()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
