"""Catalog management — add/remove products, rebuild embeddings."""

import json
import sys
import os

import numpy as np

from config import (
    CATALOG_JSON, EMBEDDINGS_NPY, EMBEDDING_INDEX_JSON,
    CATALOG_IMAGES_DIR, EMBEDDING_MODEL, get_api_key,
)
from tag_schema import generate_product_description, validate_tags


def _load_catalog() -> dict:
    """Load catalog.json."""
    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_catalog(catalog: dict) -> None:
    """Save catalog.json."""
    with open(CATALOG_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


def build_embeddings() -> None:
    """
    Read catalog.json, generate descriptions, embed them, save to disk.
    Run this after adding/changing products.
    """
    from google import genai

    api_key = get_api_key()
    client = genai.Client(api_key=api_key)

    catalog = _load_catalog()
    products = catalog["products"]

    if not products:
        print("Error: No products in catalog.")
        sys.exit(1)

    print(f"Building embeddings for {len(products)} products...")

    # Step 1: Generate descriptions from tags
    descriptions = []
    for product in products:
        desc = generate_product_description(product)
        product["description_auto"] = desc
        descriptions.append(desc)
        print(f"  [{product['id']}] {product['name']}")
        print(f"    Description: {desc[:100]}...")

    # Step 2: Save updated descriptions back to catalog
    _save_catalog(catalog)
    print(f"\nDescriptions saved to {CATALOG_JSON}")

    # Step 3: Embed all descriptions
    print(f"\nEmbedding {len(descriptions)} descriptions with {EMBEDDING_MODEL}...")
    all_embeddings = []
    for i, desc in enumerate(descriptions):
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=desc,
        )
        embedding = result.embeddings[0].values
        all_embeddings.append(embedding)
        print(f"  Embedded {i+1}/{len(descriptions)}: dim={len(embedding)}")

    # Step 4: Stack and save as numpy array
    embeddings_array = np.array(all_embeddings, dtype=np.float32)
    np.save(str(EMBEDDINGS_NPY), embeddings_array)
    print(f"\nEmbeddings saved: {EMBEDDINGS_NPY} (shape: {embeddings_array.shape})")

    # Step 5: Save index mapping
    index_map = {str(i): products[i]["id"] for i in range(len(products))}
    with open(EMBEDDING_INDEX_JSON, "w", encoding="utf-8") as f:
        json.dump(index_map, f, indent=2)
    print(f"Index saved: {EMBEDDING_INDEX_JSON}")

    print("\nDone! Embeddings are ready for search.")


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
        image_path = CATALOG_JSON.parent / product["image"]
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
        print("\nAll products validated successfully.")
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
        print(f"  [{product['id']}] {product['name']}")
        print(f"    Brand: {prod.get('brand', '?')} | Shape: {frame['shape']} | "
              f"Price: ${prod['price']} | {stock}")

        if product.get("description_auto"):
            print(f"    Desc: {product['description_auto'][:80]}...")
        print()


def main():
    if len(sys.argv) < 2:
        print("Usage: python catalog_manager.py <command>")
        print("Commands:")
        print("  build     — Generate descriptions and rebuild embeddings")
        print("  validate  — Check images exist and tags are valid")
        print("  list      — List all products")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "build":
        build_embeddings()
    elif command == "validate":
        validate_catalog()
    elif command == "list":
        list_products()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
