"""Inventory matcher — find best catalog products by tag matching.

Takes the recommended_tags from face analysis and scores each catalog product
using weighted tag overlap.
"""

import json
import os
import time

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tag_matcher import rank_products


class MatchResult:
    """Result of inventory matching."""

    def __init__(
        self,
        success: bool,
        matches: list | None = None,
        elapsed_seconds: float = 0.0,
        error: str | None = None,
    ):
        self.success = success
        self.matches = matches or []
        self.elapsed_seconds = elapsed_seconds
        self.error = error


class InventoryMatcher:

    def __init__(self, catalog_dir: str, *, catalog_data: dict | None = None):
        """
        Load the existing catalog.

        Args:
            catalog_dir: Path to catalog/ directory.
            catalog_data: Pre-loaded catalog dict (skips loading from disk).
        """
        self.catalog_dir = catalog_dir

        if catalog_data is not None:
            self.products = catalog_data["products"]
            self.products_by_id = {p["id"]: p for p in self.products}
        else:
            catalog_path = os.path.join(catalog_dir, "catalog.json")
            with open(catalog_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.products = raw["products"]
            self.products_by_id = {p["id"]: p for p in self.products}

    def match(self, recommended_tags: dict, top_k: int = 3,
              gender: str | None = None) -> MatchResult:
        """
        Find the best matching products for the recommended glasses tags.

        Args:
            recommended_tags: The recommended_tags dict from face analysis
                              (contains frame, lenses, style sub-dicts).
            top_k: Number of top matches to return.
            gender: If provided ('men' or 'women'), only return products
                    whose gender_target matches or is 'unisex'.

        Returns:
            MatchResult whose .matches is a list of
            tag_matcher.Match(product, score, components).
        """
        start_time = time.time()

        # Pre-filter: only products with valid images on disk
        available = [
            p for p in self.products
            if os.path.isfile(self.get_product_image_path(p))
        ]

        # Gender is passed via filters — rank_products handles injection
        # without mutating recommended_tags.

        # Cascading filter → soft score via tag_matcher
        matches = rank_products(
            query_tags=recommended_tags,
            products=available,
            top_k=top_k,
            filters={"in_stock_only": True, "gender": gender},
        )

        elapsed = time.time() - start_time

        if not matches:
            return MatchResult(
                success=False,
                elapsed_seconds=elapsed,
                error="No in-stock products with valid images found in catalog.",
            )

        return MatchResult(
            success=True,
            matches=matches,
            elapsed_seconds=elapsed,
        )

    def get_product_image_path(self, product: dict) -> str:
        """Get the full path to a product's image file."""
        return os.path.join(self.catalog_dir, product["image"])
