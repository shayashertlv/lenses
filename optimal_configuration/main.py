"""CLI entry point — glasses finder + virtual try-on."""

import argparse
import sys

from config import (
    MODEL_MAP, DEFAULT_MODEL, DEFAULT_TOP_K,
    CATALOG_JSON, CATALOG_IMAGES_DIR, get_api_key,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find glasses from a catalog and virtually try them on.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python main.py -p selfie.jpg -q "round glasses with thin gold frame"
  python main.py -p selfie.jpg -q "cheap black sunglasses" --top-k 5
  python main.py -p selfie.jpg -q "cat-eye luxury frames" --auto -m nano-banana-pro
  python main.py -p selfie.jpg -q "משקפיים עגולות עם מסגרת זהב" --dry-run
        """,
    )

    parser.add_argument(
        "--portrait", "-p",
        required=True,
        help="Path to user's portrait photo",
    )
    parser.add_argument(
        "--query", "-q",
        required=True,
        help="Natural language description of desired glasses (any language)",
    )
    parser.add_argument(
        "--output", "-o",
        default="tryon_result.png",
        help="Output image path (default: tryon_result.png)",
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        choices=list(MODEL_MAP.keys()),
        help=f"Model for try-on (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--top-k", "-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Number of search results to show (default: {DEFAULT_TOP_K})",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        default=False,
        help="Auto-select the #1 match (skip interactive selection)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Search only — skip the try-on step",
    )
    parser.add_argument(
        "--results-dir",
        default="./results/",
        help="Directory to save output files (default: ./results/)",
    )

    return parser.parse_args()


def display_results(results: list[tuple[dict, float]]) -> None:
    """Print search results to terminal."""
    print(f"\n{'='*60}")
    print(f"  TOP {len(results)} MATCHES")
    print(f"{'='*60}\n")

    for i, (product, score) in enumerate(results, 1):
        prod = product["tags"]["product"]
        frame = product["tags"]["frame"]
        lenses = product["tags"]["lenses"]
        style = product["tags"]["style"]

        frame_colors = ", ".join(frame["color"]) if isinstance(frame["color"], list) else frame["color"]
        lens_types = ", ".join(lenses["type"]) if isinstance(lenses["type"], list) else lenses["type"]
        aesthetics = ", ".join(style["aesthetic"]) if isinstance(style["aesthetic"], list) else style["aesthetic"]

        stock = "In Stock" if prod.get("in_stock") else "Out of Stock"

        print(f"  #{i}  {product['name']}  (score: {score:.3f})")
        print(f"      Brand: {prod.get('brand', '?')} | ${prod['price']:.2f} | {stock}")
        print(f"      Frame: {frame.get('shape', '?')}, {frame_colors}, {frame.get('material', '?')}, {frame.get('rim_type', '?')}")
        print(f"      Lenses: {lens_types}")
        print(f"      Style: {aesthetics}")
        print()


def select_product(results: list[tuple[dict, float]], auto: bool) -> tuple[dict, float] | None:
    """Let user select a product from results, or auto-select #1."""
    if not results:
        return None

    if auto:
        print(f"  Auto-selecting #{1}: {results[0][0]['name']}")
        return results[0]

    while True:
        try:
            choice = input(f"  Select a product (1-{len(results)}) or 'q' to quit: ").strip()
            if choice.lower() == "q":
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                return results[idx]
            print(f"  Please enter a number between 1 and {len(results)}")
        except ValueError:
            print(f"  Please enter a number between 1 and {len(results)}")
        except (EOFError, KeyboardInterrupt):
            return None


def main() -> None:
    args = parse_args()

    # Validate portrait
    from utils import validate_image
    info = validate_image(args.portrait)
    if not info["is_valid"]:
        print(f"Error: {info['error']}")
        sys.exit(1)

    print(f"Portrait: {args.portrait} ({info['width']}x{info['height']})")

    # Check catalog exists
    if not CATALOG_JSON.exists():
        print("\nError: Catalog not found.")
        print("Run: cd ../lenses && python catalog_manager.py build")
        sys.exit(1)

    # Get API key
    try:
        api_key = get_api_key()
    except RuntimeError as e:
        print(f"\nError: {e}")
        sys.exit(1)

    # Pre-load portrait image in background (runs during query interpretation + search)
    import threading
    _portrait_result = [None, None]  # [part, error]
    if not args.dry_run:
        from utils import load_image_as_part
        def _preload_portrait():
            try:
                _portrait_result[0] = load_image_as_part(args.portrait)
            except Exception as e:
                _portrait_result[1] = e
        portrait_thread = threading.Thread(target=_preload_portrait, daemon=True)
        portrait_thread.start()

    # Step 1: Interpret user query
    print(f"\nQuery: \"{args.query}\"")
    print("Interpreting query...")

    from query_interpreter import interpret_query
    intent = interpret_query(args.query, api_key)

    print(f"  Language: {intent.get('original_language', '?')}")
    print(f"  Search text: {intent.get('search_text', '?')}")
    search_tags = intent.get("search_tags", {})
    if search_tags:
        for cat in ("frame", "lenses", "style"):
            if search_tags.get(cat):
                print(f"  Tags [{cat}]: {search_tags[cat]}")
    if intent["filters"].get("max_price"):
        print(f"  Max price: ${intent['filters']['max_price']}")
    if intent.get("priorities"):
        print(f"  Priorities: {', '.join(intent['priorities'])}")

    # Step 2: Tag-based search
    print("\nSearching catalog...")

    from search_engine import GlassesSearchEngine
    engine = GlassesSearchEngine(
        catalog_path=str(CATALOG_JSON),
    )

    filters = {"in_stock_only": True}   # always exclude out-of-stock by default
    if intent["filters"].get("max_price"):
        filters["max_price"] = intent["filters"]["max_price"]
    if intent["filters"].get("gender"):
        filters["gender"] = intent["filters"]["gender"]

    results = engine.search(
        query_tags=search_tags,
        top_k=args.top_k,
        filters=filters,
    )

    if not results:
        print("\nNo glasses match your description. Try broader terms.")
        sys.exit(0)

    # Display results
    display_results(results)

    # Dry run stops here
    if args.dry_run:
        print("Dry run — skipping try-on.")
        return

    # Step 3: Select product
    selected = select_product(results, args.auto)
    if selected is None:
        print("No product selected. Exiting.")
        return

    product, score = selected

    # Step 4: Virtual try-on
    glasses_image_path = str(CATALOG_JSON.parent / product["image"])

    from utils import validate_image as vi
    glasses_info = vi(glasses_image_path)
    if not glasses_info["is_valid"]:
        print(f"\nError: Product image not found: {glasses_image_path}")
        sys.exit(1)

    # Wait for portrait pre-loading (started before query interpretation)
    portrait_thread.join()
    if _portrait_result[1] is not None:
        print(f"\nPortrait load error: {_portrait_result[1]}")
        sys.exit(1)

    print(f"\nRunning virtual try-on with {args.model}...")
    print(f"  Glasses: {product['name']}")

    from tryon_engine import virtual_tryon
    result = virtual_tryon(
        portrait_path=args.portrait,
        glasses_image_path=glasses_image_path,
        product=product,
        model_alias=args.model,
        api_key=api_key,
        portrait_part=_portrait_result[0],
    )

    if not result.success:
        print(f"\nTry-on failed: {result.error}")
        if result.text_response:
            print(f"Model response: {result.text_response}")
        sys.exit(1)

    # Save result
    import os
    os.makedirs(args.results_dir, exist_ok=True)
    output_path = os.path.join(args.results_dir, args.output)

    from utils import save_image
    save_image(result.image_bytes, output_path)

    print(f"\nDone!")
    print(f"  Output: {output_path}")
    print(f"  Model: {result.model_used}")
    print(f"  Time: {result.elapsed_seconds:.1f}s")
    if result.text_response:
        print(f"  Note: {result.text_response}")


if __name__ == "__main__":
    main()
