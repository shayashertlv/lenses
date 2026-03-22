"""Embedding-based semantic search engine for glasses catalog."""

import json

import numpy as np

from config import EMBEDDING_MODEL, MIN_SIMILARITY_THRESHOLD


class GlassesSearchEngine:

    def __init__(self, catalog_path, embeddings_path, index_path, api_key):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.catalog = self._load_catalog(catalog_path)
        self.embeddings = np.load(str(embeddings_path))
        self.index_map = self._load_index(index_path)

        # Pre-normalize embeddings for cosine similarity via dot product
        norms = np.linalg.norm(self.embeddings, axis=1, keepdims=True)
        self.embeddings_normalized = self.embeddings / norms

    def _load_catalog(self, path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_index(self, path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _get_product_by_id(self, product_id: str) -> dict | None:
        for product in self.catalog["products"]:
            if product["id"] == product_id:
                return product
        return None

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[tuple[dict, float]]:
        """
        Semantic search against the product catalog.

        Args:
            query: User's natural language description (any language).
            top_k: Number of results to return.
            filters: Optional hard filters:
                     {"max_price": 100, "in_stock_only": True, "gender": "men"}

        Returns:
            List of (product_dict, similarity_score) sorted by score desc.
        """
        # 1. Embed the query
        result = self.client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=query,
        )
        query_embedding = np.array(result.embeddings[0].values)
        query_normalized = query_embedding / np.linalg.norm(query_embedding)

        # 2. Cosine similarity via dot product (both normalized)
        similarities = self.embeddings_normalized @ query_normalized

        # 3. Sort by similarity descending
        ranked_indices = np.argsort(similarities)[::-1]

        # 4. Collect results, applying filters
        results = []
        for idx in ranked_indices:
            product_id = self.index_map[str(idx)]
            product = self._get_product_by_id(product_id)

            if product is None:
                continue

            score = float(similarities[idx])

            # Check minimum similarity threshold
            if score < MIN_SIMILARITY_THRESHOLD and len(results) > 0:
                break

            if filters:
                prod_tags = product["tags"]["product"]
                style_tags = product["tags"]["style"]

                if filters.get("in_stock_only") and not prod_tags.get("in_stock", False):
                    continue
                if filters.get("max_price") is not None:
                    if prod_tags.get("price", 0) > filters["max_price"]:
                        continue
                if filters.get("gender"):
                    target = style_tags.get("gender_target", "unisex")
                    if target != "unisex" and target != filters["gender"]:
                        continue

            results.append((product, score))
            if len(results) >= top_k:
                break

        return results
