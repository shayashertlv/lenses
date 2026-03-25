"""Tag-based search engine for glasses catalog."""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tag_matcher import rank_products


class GlassesSearchEngine:

    def __init__(self, catalog_path, embeddings_path=None, index_path=None,
                 api_key=None):
        """
        Load the catalog for tag-based search.

        embeddings_path, index_path, and api_key are kept for backward compat
        but are no longer used (no embeddings).
        """
        self.catalog = self._load_catalog(catalog_path)

    def _load_catalog(self, path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def search(
        self,
        query_tags: dict,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[tuple[dict, float]]:
        """
        Cascading filter + soft-score search against the product catalog.

        Args:
            query_tags: Structured tags dict with frame/lenses/style sub-dicts.
            top_k: Number of results to return.
            filters: Optional business filters:
                     {"max_price": 100, "in_stock_only": True, "gender": "men"}

        Returns:
            List of (product_dict, score) sorted by score desc.
        """
        return rank_products(
            query_tags=query_tags,
            products=self.catalog["products"],
            top_k=top_k,
            filters=filters,
        )
