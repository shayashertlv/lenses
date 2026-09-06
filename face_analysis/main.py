"""CLI entry point — face analysis + inventory matching + virtual try-on."""

import argparse
import json
import os
import sys

from config import (
    GENERATION_MODEL_MAP,
    DEFAULT_GENERATION_MODEL,
    DEFAULT_ANALYSIS_MODEL,
    CATALOG_DIR,
    MIN_SIMILARITY_THRESHOLD,
    get_api_key,
    validate_catalog_exists,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze facial features, find best-matching glasses from inventory, "
            "and generate a virtual try-on image."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python main.py -p selfie.jpg
  python main.py -p selfie.jpg -o result.png --save-report report.txt
  python main.py -p selfie.jpg --report-only --save-analysis analysis.json
  python main.py -p selfie.jpg -m nano-banana-pro --auto
  python main.py -p selfie.jpg --dry-run
  python main.py -p selfie.jpg -k 5 --report-only
        """,
    )

    parser.add_argument(
        "--portrait", "-p",
        required=True,
        help="Path to user's portrait photo",
    )
    parser.add_argument(
        "--output", "-o",
        default="face_analysis_result.png",
        help="Output image path (default: face_analysis_result.png)",
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_GENERATION_MODEL,
        choices=list(GENERATION_MODEL_MAP.keys()),
        help=f"Nano Banana model for try-on (default: {DEFAULT_GENERATION_MODEL})",
    )
    parser.add_argument(
        "--analysis-model",
        default=DEFAULT_ANALYSIS_MODEL,
        help=f"Model for face analysis (default: {DEFAULT_ANALYSIS_MODEL})",
    )
    parser.add_argument(
        "--top-k", "-k",
        type=int,
        default=3,
        help="Number of inventory matches to show (default: 3)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        default=False,
        help="Auto-select #1 match without user confirmation",
    )
    parser.add_argument(
        "--save-report",
        default=None,
        help="Save human-readable report to text file",
    )
    parser.add_argument(
        "--save-analysis",
        default=None,
        help="Save raw analysis JSON to file",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        default=False,
        help="Do analysis + matching only, skip try-on",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Do analysis only, skip matching + try-on",
    )

    return parser.parse_args()


def select_product(matches: list, auto: bool):
    """Let user select a product from matches, or auto-select #1."""
    if not matches:
        return None

    if auto:
        print(f"  Auto-selecting #1: {matches[0][0]['name']}")
        return matches[0]

    while True:
        try:
            choice = input(
                f"  Select a product (1-{len(matches)}) or 'q' to quit: "
            ).strip()
            if choice.lower() == "q":
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                return matches[idx]
            print(f"  Please enter a number between 1 and {len(matches)}")
        except ValueError:
            print(f"  Please enter a number between 1 and {len(matches)}")
        except (EOFError, KeyboardInterrupt):
            return None


def main() -> None:
    args = parse_args()

    # Step 1: Validate portrait
    from utils import validate_image
    info = validate_image(args.portrait)
    if not info["is_valid"]:
        print(f"Error: {info['error']}")
        sys.exit(1)

    print(
        f"Loading portrait: {args.portrait} "
        f"({info['width']}x{info['height']}, {info['format']})"
    )
    if info["needs_resize"]:
        print(f"  Note: Image will be resized to fit 1536px max side.")

    # Step 2: Validate catalog exists (unless dry-run)
    if not args.dry_run:
        catalog_error = validate_catalog_exists()
        if catalog_error:
            print(f"\nError: {catalog_error}")
            sys.exit(1)

    # Get API key
    try:
        api_key = get_api_key()
    except RuntimeError as e:
        print(f"\nError: {e}")
        sys.exit(1)

    # Pre-load portrait image in background (runs during face analysis)
    import threading
    _portrait_result = [None, None]  # [part, error]
    if not args.dry_run and not args.report_only:
        from utils import load_image_as_part
        def _preload_portrait():
            try:
                _portrait_result[0] = load_image_as_part(args.portrait)
            except Exception as e:
                _portrait_result[1] = e
        portrait_thread = threading.Thread(target=_preload_portrait, daemon=True)
        portrait_thread.start()

    # ─── STEP 1: Face analysis ───
    print(f"\nAnalyzing facial features with {args.analysis_model}...")

    from face_analyzer import FaceAnalyzer
    analyzer = FaceAnalyzer(api_key=api_key, model=args.analysis_model)
    result = analyzer.analyze(args.portrait)

    if not result.success:
        print(f"\nAnalysis failed: {result.error}")
        if result.raw_response:
            print(
                f"\nRaw response (first 500 chars):\n"
                f"{result.raw_response[:500]}"
            )
        sys.exit(1)

    analysis = result.analysis
    print(f"  Analysis complete ({result.elapsed_seconds:.1f}s)")

    # Optionally save raw analysis JSON
    if args.save_analysis:
        out_dir = os.path.dirname(args.save_analysis)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.save_analysis, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, ensure_ascii=False)
        print(f"  Analysis JSON saved to: {args.save_analysis}")

    # Dry run stops here
    if args.dry_run:
        from report_builder import build_report
        report = build_report(analysis)
        print(f"\n{report}")
        if args.save_report:
            from utils import save_text
            save_text(report, args.save_report)
            print(f"  Report saved to: {args.save_report}")
        print("\nDry run — skipping inventory matching and try-on.")
        return

    # ─── STEP 2: Inventory matching ───
    print("\nMatching against inventory...")

    from inventory_matcher import InventoryMatcher
    matcher = InventoryMatcher(CATALOG_DIR)
    recommended_tags = analysis["glasses_recommendation"]["recommended_tags"]
    detected_gender = analysis.get("gender")  # "men" or "women" — from face analysis
    match_result = matcher.match(recommended_tags, top_k=args.top_k, gender=detected_gender)

    if not match_result.success:
        print(f"\nMatching failed: {match_result.error}")
        # Still print analysis report without matches
        from report_builder import build_report
        report = build_report(analysis)
        print(f"\n{report}")
        sys.exit(1)

    matches = match_result.matches
    print(f"  Found {len(matches)} matches ({match_result.elapsed_seconds:.1f}s)")

    # Warn if low match scores
    best_score = matches[0][1] if matches else 0
    if best_score < MIN_SIMILARITY_THRESHOLD:
        print(
            f"\n  Warning: No glasses in inventory closely match the recommendation. "
            f"Best score: {best_score:.1f}. Showing best available."
        )

    # Build and print full report with match results
    from report_builder import build_report
    report = build_report(analysis, match_results=matches)
    print(f"\n{report}")

    # Optionally save report
    if args.save_report:
        from utils import save_text
        save_text(report, args.save_report)
        print(f"Report saved to: {args.save_report}")

    # Report-only stops here
    if args.report_only:
        print("\nReport-only mode — skipping try-on.")
        return

    # ─── STEP 3: Product selection ───
    selected = select_product(matches, args.auto)
    if selected is None:
        print("No product selected. Exiting.")
        return

    product, score, _ = selected
    glasses_image_path = matcher.get_product_image_path(product)

    # ─── STEP 4: Virtual try-on ───
    # Wait for portrait pre-loading (started before face analysis)
    portrait_thread.join()
    if _portrait_result[1] is not None:
        print(f"\nPortrait load error: {_portrait_result[1]}")
        sys.exit(1)

    print(
        f"\nGenerating try-on with {product['name']} "
        f"using {args.model}..."
    )

    from tryon_engine import virtual_tryon
    tryon_result = virtual_tryon(
        portrait_path=args.portrait,
        glasses_image_path=glasses_image_path,
        analysis=analysis,
        matched_product=product,
        model_alias=args.model,
        api_key=api_key,
        portrait_part=_portrait_result[0],
    )

    if not tryon_result.success:
        print(f"\nTry-on failed: {tryon_result.error}")
        if tryon_result.text_response:
            print(f"Model response: {tryon_result.text_response}")
        sys.exit(1)

    # Normalize aspect ratio to match the input portrait, then save
    from utils import normalize_tryon_aspect, save_image
    normalized = normalize_tryon_aspect(tryon_result.image_bytes, args.portrait)
    save_image(normalized, args.output)

    # Summary
    print(f"\nDone!")
    print(f"  Output: {args.output}")
    print(f"  Selected: {product['name']} (score: {score:.1f})")
    print(f"  Analysis model: {result.model_used}")
    print(f"  Generation model: {tryon_result.model_used}")
    print(f"  Analysis time: {result.elapsed_seconds:.1f}s")
    print(f"  Matching time: {match_result.elapsed_seconds:.1f}s")
    print(f"  Try-on time: {tryon_result.elapsed_seconds:.1f}s")
    total = (
        result.elapsed_seconds
        + match_result.elapsed_seconds
        + tryon_result.elapsed_seconds
    )
    print(f"  Total time: {total:.1f}s")


if __name__ == "__main__":
    main()
