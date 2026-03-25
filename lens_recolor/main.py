"""CLI entry point for the glasses lens recolor tool."""

import argparse
import sys

from config import MODEL_MAP, DEFAULT_MODEL, DEFAULT_INTENSITY, DEFAULT_FINISH
from prompt_engine import build_lens_recolor_prompt
from utils import validate_input_image, save_image, generate_output_path, create_comparison_image
from recolor import recolor_lenses


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Change the color of glasses lenses in a photo using Google Gemini (Nano Banana).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python main.py -i photo.jpg -c "ocean blue"
  python main.py -i photo.jpg -c "rose gold" --intensity light --finish gradient
  python main.py -i photo.jpg -c "emerald green" --intensity dark --finish mirror -m nano-banana-pro
  python main.py -i photo.jpg -c "amber" --compare
  python main.py -i photo.jpg -c "emerald green" --dry-run
        """,
    )

    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to input image (portrait with glasses)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path for output image (default: auto-generated with timestamp)",
    )
    parser.add_argument(
        "--color", "-c",
        required=True,
        help='Target lens color — any descriptive string (e.g., "ocean blue", "rose gold", "G-15 green")',
    )
    parser.add_argument(
        "--intensity",
        choices=["light", "medium", "dark"],
        default=DEFAULT_INTENSITY,
        help=f"Tint darkness level (default: {DEFAULT_INTENSITY})",
    )
    parser.add_argument(
        "--finish",
        choices=["standard", "gradient", "mirror", "polarized"],
        default=DEFAULT_FINISH,
        help=f"Lens finish style (default: {DEFAULT_FINISH})",
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        choices=list(MODEL_MAP.keys()),
        help=f"Model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--preserve-reflections",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Preserve natural lens reflections (default: True)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        default=False,
        help="Run BOTH models and create a side-by-side comparison",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the prompt without calling the API",
    )

    return parser.parse_args()


def run_single(args: argparse.Namespace) -> None:
    """Run recolor with a single model."""
    print(f"Recoloring lenses to '{args.color}' using {args.model}...")

    result = recolor_lenses(
        image_path=args.input,
        target_color=args.color,
        model_alias=args.model,
        intensity=args.intensity,
        finish=args.finish,
        preserve_reflections=args.preserve_reflections,
    )

    if not result.success:
        print(f"\nError: {result.error}")
        if result.text_response:
            print(f"Model response: {result.text_response}")
        sys.exit(1)

    output_path = args.output or generate_output_path(args.input)
    save_image(result.image_bytes, output_path)

    print(f"\nDone!")
    print(f"  Output: {output_path}")
    print(f"  Model: {result.model_used}")
    print(f"  Time: {result.elapsed_seconds:.1f}s")
    if result.text_response:
        print(f"  Model note: {result.text_response}")


def run_comparison(args: argparse.Namespace) -> None:
    """Run recolor with BOTH models and create comparison."""
    print(f"Running comparison mode — recoloring lenses to '{args.color}' with both models...\n")

    results = []
    for model_alias in MODEL_MAP:
        print(f"  Running {model_alias}...")
        result = recolor_lenses(
            image_path=args.input,
            target_color=args.color,
            model_alias=model_alias,
            intensity=args.intensity,
            finish=args.finish,
            preserve_reflections=args.preserve_reflections,
        )

        if result.success:
            # Save individual result
            individual_path = f"output_{model_alias}.png"
            save_image(result.image_bytes, individual_path)
            print(f"    Saved: {individual_path} ({result.elapsed_seconds:.1f}s)")
            results.append((model_alias, model_alias, result.image_bytes))
        else:
            print(f"    Failed: {result.error}")

    if not results:
        print("\nBoth models failed. No comparison to create.")
        sys.exit(1)

    # Create comparison image
    if len(results) >= 1:
        comparison_path = args.output or "comparison.png"
        create_comparison_image(args.input, results, comparison_path)
        print(f"\nComparison saved: {comparison_path}")


def run_dry_run(args: argparse.Namespace) -> None:
    """Print the prompt without calling the API."""
    # Validate image exists
    info = validate_input_image(args.input)
    if not info["is_valid"]:
        print(f"Warning: {info['error']}")
    else:
        print(f"Input image: {args.input} ({info['width']}x{info['height']} {info['format']}, {info['file_size_mb']}MB)")
        if info["needs_resize"]:
            print(f"  Note: Image will be resized (>{4096}px on one side)")

    print(f"\nModel: {args.model}")
    print(f"Color: {args.color}")
    print(f"Intensity: {args.intensity}")
    print(f"Finish: {args.finish}")
    print(f"Preserve reflections: {args.preserve_reflections}")
    print(f"\n{'='*60}")
    print("GENERATED PROMPT:")
    print(f"{'='*60}\n")

    prompt = build_lens_recolor_prompt(
        target_color=args.color,
        intensity=args.intensity,
        finish=args.finish,
        preserve_reflections=args.preserve_reflections,
    )
    print(prompt)


def main() -> None:
    args = parse_args()

    # Validate input image
    info = validate_input_image(args.input)
    if not info["is_valid"]:
        # For dry-run, just warn; for actual runs, fail
        if not args.dry_run:
            print(f"Error: {info['error']}")
            sys.exit(1)

    if args.dry_run:
        run_dry_run(args)
    elif args.compare:
        run_comparison(args)
    else:
        run_single(args)


if __name__ == "__main__":
    main()
