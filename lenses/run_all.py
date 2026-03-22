"""Orchestrator — run scrape + download in sequence."""

import subprocess
import sys
from pathlib import Path


def run_script(name: str) -> bool:
    """Run a Python script and return True if it succeeded."""
    script = Path(__file__).parent / name
    print(f"\n{'=' * 60}")
    print(f"Running: {name}")
    print(f"{'=' * 60}\n")

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(script.parent),
    )
    return result.returncode == 0


def main() -> None:
    print("Erroca Men's Eyeglasses — Full Pipeline")
    print("Step 1: Scrape image URLs")
    print("Step 2: Download all images")

    if not run_script("scrape_images.py"):
        print("\nScraping failed. Aborting.")
        sys.exit(1)

    if not run_script("download_images.py"):
        print("\nDownloading failed.")
        sys.exit(1)

    print("\n\nAll done! Check lenses/downloaded_images/ for the images.")


if __name__ == "__main__":
    main()
