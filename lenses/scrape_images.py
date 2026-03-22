"""Scrape product image URLs from Erroca men's eyeglasses category.

Handles pagination, lazy-loaded images, srcset, and data-src attributes.
Saves all discovered image URLs to image_urls.txt.
"""

import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://erroca.co.il/product-category/eyeglasses/men-eyeglasses/"
OUTPUT_FILE = "image_urls.txt"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
}
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_PAGES = 1.5  # seconds — be polite


def fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch a page and return parsed BeautifulSoup, or None on failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except requests.RequestException as e:
        print(f"  Error fetching {url}: {e}")
        return None


def get_total_pages(soup: BeautifulSoup) -> int:
    """Detect the total number of paginated pages."""
    # WooCommerce pagination: look for page number links
    page_links = soup.select("a.page-number, a.page-numbers, ul.page-numbers a")
    max_page = 1
    for link in page_links:
        text = link.get_text(strip=True)
        if text.isdigit():
            max_page = max(max_page, int(text))

    # Also check for "next" arrow that hints at more pages
    next_link = soup.select_one("a.next.page-number, a.next.page-numbers")
    if next_link:
        href = next_link.get("href", "")
        match = re.search(r"/page/(\d+)/", href)
        if match:
            max_page = max(max_page, int(match.group(1)))

    return max_page


def extract_image_urls(soup: BeautifulSoup) -> set[str]:
    """Extract all product image URLs from a single page.

    Looks at multiple sources:
    - img src
    - img data-src / data-lazy-src (lazy loading)
    - img srcset (all resolutions)
    - background-image in style attributes
    """
    urls: set[str] = set()

    # Find product containers — WooCommerce classes
    product_items = soup.select(
        ".product-small, .product.type-product, "
        ".products .product, .product-grid-item"
    )

    # If specific selectors don't find products, broaden search
    if not product_items:
        product_items = soup.select(".products li, .product-loop")

    # Collect img tags from product items
    img_tags = []
    for item in product_items:
        img_tags.extend(item.find_all("img"))

    # Fallback: if no product containers found, scan all images in main content
    if not img_tags:
        main_content = soup.select_one(
            ".products, #main, .main-content, .shop-container, "
            ".woocommerce, main, #content"
        )
        if main_content:
            img_tags = main_content.find_all("img")
        else:
            img_tags = soup.find_all("img")

    for img in img_tags:
        # Standard src
        src = img.get("src", "")
        if src and _is_product_image(src):
            urls.add(_clean_url(src))

        # Lazy-load attributes
        for attr in ("data-src", "data-lazy-src", "data-original", "data-lazy"):
            val = img.get(attr, "")
            if val and _is_product_image(val):
                urls.add(_clean_url(val))

        # srcset — contains multiple resolutions
        srcset = img.get("srcset", "") or img.get("data-srcset", "")
        if srcset:
            for entry in srcset.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                parts = entry.split()
                if parts and _is_product_image(parts[0]):
                    urls.add(_clean_url(parts[0]))

    return urls


def _is_product_image(url: str) -> bool:
    """Filter: keep only actual product images, skip placeholders/icons."""
    url_lower = url.lower()
    # Must be from wp-content uploads
    if "wp-content/uploads" not in url_lower:
        return False
    # Skip tiny thumbnails, placeholders, logos
    skip_patterns = [
        "placeholder", "logo", "banner", "icon", "woocommerce-placeholder",
        "spinner", "loading", "empty", "no-image",
    ]
    for pat in skip_patterns:
        if pat in url_lower:
            return False
    # Must be an image file
    if not re.search(r"\.(jpe?g|png|webp|gif|bmp|avif)", url_lower):
        return False
    return True


def _clean_url(url: str) -> str:
    """Ensure URL is absolute and clean."""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = urljoin(BASE_URL, url)
    return url


def _get_best_resolution(urls: set[str]) -> list[str]:
    """For each base image, keep only the highest resolution version.

    WP image URLs look like: filename-510x340.webp, filename-300x200.webp, etc.
    We prefer the largest or the original (no dimensions suffix).
    """
    # Group by base filename (strip the -WIDTHxHEIGHT suffix)
    groups: dict[str, list[str]] = {}
    dim_pattern = re.compile(r"-\d+x\d+(\.\w+)$")

    for url in urls:
        base = dim_pattern.sub(r"\1", url)
        groups.setdefault(base, []).append(url)

    best: list[str] = []
    for base, variants in groups.items():
        # Prefer the original (no dimension suffix) if available
        originals = [v for v in variants if not re.search(r"-\d+x\d+\.\w+$", v)]
        if originals:
            best.append(originals[0])
        else:
            # Pick the largest resolution
            def _get_area(u: str) -> int:
                m = re.search(r"-(\d+)x(\d+)\.\w+$", u)
                return int(m.group(1)) * int(m.group(2)) if m else 0

            variants.sort(key=_get_area, reverse=True)
            best.append(variants[0])

    best.sort()
    return best


def scrape_all() -> list[str]:
    """Scrape all pages and return deduplicated, best-resolution image URLs."""
    print(f"Fetching page 1: {BASE_URL}")
    soup = fetch_page(BASE_URL)
    if soup is None:
        print("Failed to fetch the first page. Aborting.")
        sys.exit(1)

    total_pages = get_total_pages(soup)
    print(f"Found {total_pages} page(s) to scrape.")

    all_urls: set[str] = set()

    # Page 1
    page_urls = extract_image_urls(soup)
    print(f"  Page 1: found {len(page_urls)} image URL(s)")
    all_urls.update(page_urls)

    # Remaining pages
    for page_num in range(2, total_pages + 1):
        time.sleep(DELAY_BETWEEN_PAGES)
        page_url = f"{BASE_URL}page/{page_num}/"
        print(f"Fetching page {page_num}: {page_url}")
        soup = fetch_page(page_url)
        if soup is None:
            print(f"  Skipping page {page_num} (fetch failed)")
            continue
        page_urls = extract_image_urls(soup)
        print(f"  Page {page_num}: found {len(page_urls)} image URL(s)")
        all_urls.update(page_urls)

    # Deduplicate and keep best resolution per image
    best_urls = _get_best_resolution(all_urls)
    return best_urls


def main() -> None:
    print("=" * 60)
    print("Erroca Men's Eyeglasses — Image Scraper")
    print("=" * 60)

    urls = scrape_all()

    if not urls:
        print("\nNo image URLs found. The page structure may have changed.")
        sys.exit(1)

    # Save to file
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for url in urls:
            f.write(url + "\n")

    print(f"\n{'=' * 60}")
    print(f"Done! Found {len(urls)} unique product image URL(s).")
    print(f"Saved to: {OUTPUT_FILE}")
    print(f"{'=' * 60}")

    # Preview first 5
    print("\nFirst 5 URLs:")
    for i, url in enumerate(urls[:5], 1):
        print(f"  {i}. {url}")


if __name__ == "__main__":
    main()
