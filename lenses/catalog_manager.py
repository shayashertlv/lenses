"""Catalog management — generate descriptions, build embeddings, validate.

This manages the Erroca men's eyeglasses catalog in lenses/catalog/.
"""

import json
import os
import sys
from pathlib import Path

import numpy as np

# Paths
BASE_DIR = Path(__file__).parent
CATALOG_DIR = BASE_DIR / "catalog"
CATALOG_JSON = CATALOG_DIR / "catalog.json"
EMBEDDINGS_NPY = CATALOG_DIR / "embeddings.npy"
EMBEDDING_INDEX_JSON = CATALOG_DIR / "embedding_index.json"
CATALOG_IMAGES_DIR = CATALOG_DIR / "images"

EMBEDDING_MODEL = "gemini-embedding-001"

from tag_schema import generate_product_description, validate_tags


def get_api_key() -> str:
    """Get the Gemini API key from os.environ, Windows registry, or .env file."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rk:
                key, _ = winreg.QueryValueEx(rk, "GEMINI_API_KEY")
        except (FileNotFoundError, OSError):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ) as rk:
                    key, _ = winreg.QueryValueEx(rk, "GEMINI_API_KEY")
            except (FileNotFoundError, OSError):
                pass
        if key:
            os.environ["GEMINI_API_KEY"] = key
    if not key:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            load_dotenv(BASE_DIR.parent / ".env")
            key = os.environ.get("GEMINI_API_KEY", "")
        except ImportError:
            pass
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set.\n"
            "Get your API key from: https://aistudio.google.com/apikey\n"
            "Then set it:\n"
            "  setx GEMINI_API_KEY your-key-here       (Windows — permanent)\n"
            "  export GEMINI_API_KEY='your-key-here'    (Linux/Mac)"
        )
    return key


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
        print(f"    Brand: {prod.get('brand', '?')} | Shape: {frame['shape']} | "
              f"Colors: {colors} | Price: {prod['price']} {prod.get('currency', 'ILS')} | {stock}")

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
