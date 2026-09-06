"""Tag-based search engine for glasses catalog."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tag_matcher import rank_products


class GlassesSearchEngine:

    def __init__(self, catalog_path):
        """Load the catalog for tag-based search."""
        self.catalog = self._load_catalog(catalog_path)

    def _load_catalog(self, path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def search(
        self,
        query_tags: dict,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list:
        """
        Cascading filter + soft-score search against the product catalog.

        Args:
            query_tags: Structured tags dict with frame/lenses/style sub-dicts.
            top_k: Number of results to return.
            filters: Optional business filters:
                     {"max_price": 100, "in_stock_only": True, "gender": "men"}

        Returns:
            List of tag_matcher.Match(product, score, components), score desc.
        """
        return rank_products(
            query_tags=query_tags,
            products=self.catalog["products"],
            top_k=top_k,
            filters=filters,
        )
