"""Download all product images listed in image_urls.txt.

Downloads into downloaded_images/ with sequential naming.
Converts all images to JPG format.
Implements retry logic, polite delays, and skip-if-exists.
"""

import io
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image

INPUT_FILE = "image_urls.txt"
OUTPUT_DIR = Path("downloaded_images")
MAX_RETRIES = 3
DELAY_BETWEEN_DOWNLOADS = 0.8  # seconds — be polite
RETRY_DELAY = 2.0  # seconds between retries
REQUEST_TIMEOUT = 30
JPEG_QUALITY = 95  # high quality conversion
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://erroca.co.il/",
}


def load_urls(path: str) -> list[str]:
    """Load image URLs from the text file."""
    if not os.path.exists(path):
        print(f"Error: {path} not found. Run scrape_images.py first.")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        print(f"Error: {path} is empty. Run scrape_images.py first.")
        sys.exit(1)

    return urls


def build_filename(index: int) -> str:
    """Build sequential filename — always .jpg."""
    return f"erroca_mens_{index:03d}.jpg"


def get_existing_files(output_dir: Path) -> set[str]:
    """Get set of already-downloaded filenames."""
    if not output_dir.exists():
        return set()
    return {f.name for f in output_dir.iterdir() if f.is_file()}


def convert_to_jpg(raw_bytes: bytes) -> bytes:
    """Convert any image format (webp, png, etc.) to JPEG bytes."""
    img = Image.open(io.BytesIO(raw_bytes))

    # Convert RGBA/P (transparency) to RGB with white background
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1])  # use alpha as mask
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def download_one(url: str, dest: Path) -> bool:
    """Download a single image, convert to JPG, with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()

            # Verify we got image content
            content_type = resp.headers.get("Content-Type", "")
            if "image" not in content_type and "octet-stream" not in content_type:
                print(f"    Warning: unexpected content type '{content_type}' for {url}")

            raw_bytes = resp.content
            if not raw_bytes:
                raise ValueError("Downloaded file is empty")

            # Convert to JPG
            jpg_bytes = convert_to_jpg(raw_bytes)

            with open(dest, "wb") as f:
                f.write(jpg_bytes)

            return True

        except (requests.RequestException, ValueError, OSError, Exception) as e:
            if attempt < MAX_RETRIES:
                print(f"    Retry {attempt}/{MAX_RETRIES} for {dest.name}: {e}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"    FAILED after {MAX_RETRIES} attempts: {dest.name} — {e}")
                # Clean up partial download
                if dest.exists():
                    dest.unlink()
                return False

    return False


def main() -> None:
    print("=" * 60)
    print("Erroca Men's Eyeglasses — Image Downloader")
    print("=" * 60)

    urls = load_urls(INPUT_FILE)
    print(f"Loaded {len(urls)} image URL(s) from {INPUT_FILE}")
    print(f"All images will be saved as JPG.\n")

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    existing = get_existing_files(OUTPUT_DIR)
    downloaded = 0
    skipped = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        filename = build_filename(i)
        dest = OUTPUT_DIR / filename

        # Skip if already downloaded
        if filename in existing:
            print(f"  [{i}/{len(urls)}] Skipping {filename} (already exists)")
            skipped += 1
            continue

        print(f"  Downloading [{i}/{len(urls)}] {filename}...")
        if download_one(url, dest):
            size_kb = dest.stat().st_size / 1024
            print(f"    Saved ({size_kb:.1f} KB)")
            downloaded += 1
        else:
            failed += 1

        # Polite delay between downloads
        if i < len(urls):
            time.sleep(DELAY_BETWEEN_DOWNLOADS)

    print(f"\n{'=' * 60}")
    print(f"Done!")
    print(f"  Downloaded: {downloaded}")
    print(f"  Skipped:    {skipped}")
    print(f"  Failed:     {failed}")
    print(f"  Total:      {len(urls)}")
    print(f"  Output dir: {OUTPUT_DIR.resolve()}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
