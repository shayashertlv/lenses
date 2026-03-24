"""Inventory matcher — embed recommendation tags, compare to catalog, find best match.

Takes the recommended_tags from face analysis, converts them into a description
string (using the same logic as optimal_configuration's generate_product_description),
embeds that description, and finds the closest product via cosine similarity.
"""

import json
import os
import time

import numpy as np

from config import EMBEDDING_MODEL, MIN_SIMILARITY_THRESHOLD


class MatchResult:
    """Result of inventory matching."""

    def __init__(
        self,
        success: bool,
        matches: list | None = None,
        description_used: str = "",
        elapsed_seconds: float = 0.0,
        error: str | None = None,
    ):
        self.success = success
        self.matches = matches or []
        self.description_used = description_used
        self.elapsed_seconds = elapsed_seconds
        self.error = error


class InventoryMatcher:

    def __init__(self, catalog_dir: str, api_key: str, *,
                 client=None, catalog_data: dict | None = None,
                 embeddings_normalized: np.ndarray | None = None,
                 index_map: dict | None = None):
        """
        Load the existing catalog, embeddings, and index from optimal_configuration.

        Args:
            catalog_dir: Path to optimal_configuration/catalog/
            api_key: Gemini API key
            client: Pre-created genai.Client (skips creating a new one if provided).
            catalog_data: Pre-loaded catalog dict (skips loading from disk if provided).
            embeddings_normalized: Pre-normalized embeddings array (skips loading if provided).
            index_map: Pre-loaded index mapping (skips loading from disk if provided).
        """
        from google import genai

        self.client = client if client is not None else genai.Client(api_key=api_key)
        self.catalog_dir = catalog_dir

        if catalog_data is not None:
            self.products = {p["id"]: p for p in catalog_data["products"]}
        else:
            catalog_path = os.path.join(catalog_dir, "catalog.json")
            with open(catalog_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            self.products = {p["id"]: p for p in raw["products"]}

        if embeddings_normalized is not None:
            self.embeddings_normalized = embeddings_normalized
        else:
            embeddings_path = os.path.join(catalog_dir, "embeddings.npy")
            embeddings = np.load(embeddings_path)
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            self.embeddings_normalized = embeddings / norms

        if index_map is not None:
            self.index_map = index_map
        else:
            index_path = os.path.join(catalog_dir, "embedding_index.json")
            with open(index_path, "r", encoding="utf-8") as f:
                self.index_map = json.load(f)

    def match(self, recommended_tags: dict, top_k: int = 3) -> MatchResult:
        """
        Find the best matching products for the recommended glasses tags.

        Args:
            recommended_tags: The recommended_tags dict from face analysis
                              (contains frame, lenses, style sub-dicts).
            top_k: Number of top matches to return.

        Returns:
            MatchResult with list of (product_dict, similarity_score) tuples.
        """
        start_time = time.time()

        # 1. Convert tags to a natural-language description
        description = self._tags_to_description(recommended_tags)

        # 2. Embed the description
        try:
            result = self.client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=description,
            )
        except Exception as e:
            elapsed = time.time() - start_time
            return MatchResult(
                success=False,
                description_used=description,
                elapsed_seconds=elapsed,
                error=f"Embedding API error: {e}",
            )

        query_embedding = np.array(result.embeddings[0].values)
        query_normalized = query_embedding / np.linalg.norm(query_embedding)

        # 3. Cosine similarity
        similarities = self.embeddings_normalized @ query_normalized

        # 4. Sort and collect top-K (expand search if needed to find in-stock)
        ranked_indices = np.argsort(similarities)[::-1]

        matches = []
        for idx in ranked_indices:
            product_id = self.index_map[str(idx)]
            product = self.products[product_id]
            score = float(similarities[idx])

            # Skip out-of-stock products
            if not product["tags"]["product"].get("in_stock", False):
                continue

            # Skip if product image is missing
            image_path = self.get_product_image_path(product)
            if not os.path.isfile(image_path):
                continue

            matches.append((product, score))
            if len(matches) >= top_k:
                break

        elapsed = time.time() - start_time

        if not matches:
            return MatchResult(
                success=False,
                description_used=description,
                elapsed_seconds=elapsed,
                error="No in-stock products with valid images found in catalog.",
            )

        return MatchResult(
            success=True,
            matches=matches,
            description_used=description,
            elapsed_seconds=elapsed,
        )

    def get_product_image_path(self, product: dict) -> str:
        """Get the full path to a product's image file."""
        return os.path.join(self.catalog_dir, product["image"])

    def _tags_to_description(self, tags: dict) -> str:
        """
        Convert recommended_tags into a natural-language description
        using the SAME logic as optimal_configuration's generate_product_description.

        This ensures the embedding lives in the same semantic space
        as the catalog embeddings.
        """
        frame = tags["frame"]
        lenses = tags["lenses"]
        style = tags["style"]

        # Frame description
        frame_colors = (
            ", ".join(frame["color"])
            if isinstance(frame["color"], list)
            else frame["color"]
        )
        frame_desc = (
            f"{frame['thickness']} {frame['finish']} {frame_colors} "
            f"{frame['material']} {frame['rim_type']} frame "
            f"with a {frame['shape']} shape"
        )

        # Lens description
        lens_types = (
            ", ".join(lenses["type"])
            if isinstance(lenses["type"], list)
            else lenses["type"]
        )
        lens_colors = (
            ", ".join(lenses["color"])
            if isinstance(lenses["color"], list)
            else lenses["color"]
        )
        lens_desc = (
            f"{lenses['size']} {lenses['shape']} "
            f"{lens_colors} {lens_types} lenses"
        )

        # Style description
        aesthetics = (
            ", ".join(style["aesthetic"])
            if isinstance(style["aesthetic"], list)
            else style["aesthetic"]
        )
        occasions = (
            ", ".join(style["occasion"])
            if isinstance(style["occasion"], list)
            else style["occasion"]
        )
        fit = (
            ", ".join(style["face_shape_fit"])
            if isinstance(style["face_shape_fit"], list)
            else style["face_shape_fit"]
        )
        style_desc = (
            f"{aesthetics} style, designed for {style['gender_target']}, "
            f"suitable for {occasions}, fits {fit} face shapes"
        )

        # Combine — mirroring optimal_configuration's format
        full_desc = (
            f"Recommended glasses. These glasses feature a {frame_desc}. "
            f"They have {lens_desc}. "
            f"The overall look is {style_desc}."
        )

        return full_desc
