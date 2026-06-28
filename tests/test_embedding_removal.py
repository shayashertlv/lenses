"""Tests for the post-embedding-removal contract.

The catalog search used to embed text with `gemini-embedding-001` and compare
vectors via cosine similarity. That whole path is gone — search now runs on
local weighted tag overlap (`tag_matcher.rank_products`). These tests lock in
that the embedding machinery is fully removed and that every search entry point
still works without it, fully offline.

Behavioral coverage of the tag-matching engine itself lives in test_matching.py.
"""

import contextlib
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

# ── Path setup so all modules are importable ──────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "face_analysis"))
sys.path.insert(0, os.path.join(ROOT, "optimal_configuration"))

import catalog_manager
import optimal_configuration.config as opt_config
import face_analysis.config as face_config
from optimal_configuration.search_engine import GlassesSearchEngine
from face_analysis.inventory_matcher import InventoryMatcher

CATALOG_DIR = os.path.join(ROOT, "lenses", "catalog")
CATALOG_JSON = os.path.join(CATALOG_DIR, "catalog.json")

# Runtime modules that must contain no embedding calls.
RUNTIME_SOURCES = [
    "optimal_configuration/search_engine.py",
    "face_analysis/inventory_matcher.py",
    "UI/pipelines.py",
    "catalog_manager.py",
]
# Markers unique to the old embedding path.
EMBEDDING_MARKERS = ["embed_content", "gemini-embedding", "np.load", "embeddings.npy"]


# ═══════════════════════════════════════════════════════════════════════════
# 1. Embedding artifacts are gone from disk
# ═══════════════════════════════════════════════════════════════════════════

class TestEmbeddingArtifactsGone(unittest.TestCase):

    def test_embeddings_npy_absent(self):
        self.assertFalse(os.path.exists(os.path.join(CATALOG_DIR, "embeddings.npy")))

    def test_embedding_index_absent(self):
        self.assertFalse(os.path.exists(os.path.join(CATALOG_DIR, "embedding_index.json")))

    def test_catalog_assets_still_present(self):
        # The catalog the new search actually depends on must still be there.
        self.assertTrue(os.path.isfile(CATALOG_JSON))
        self.assertTrue(os.path.isdir(os.path.join(CATALOG_DIR, "images")))


# ═══════════════════════════════════════════════════════════════════════════
# 2. No embedding constants left in config
# ═══════════════════════════════════════════════════════════════════════════

class TestNoEmbeddingConfig(unittest.TestCase):

    def test_optimal_config_has_no_embedding_constants(self):
        for attr in ("EMBEDDING_MODEL", "EMBEDDINGS_NPY", "EMBEDDING_INDEX_JSON"):
            self.assertFalse(hasattr(opt_config, attr),
                             f"optimal_configuration.config still defines {attr}")

    def test_face_config_has_no_embedding_model(self):
        self.assertFalse(hasattr(face_config, "EMBEDDING_MODEL"))


# ═══════════════════════════════════════════════════════════════════════════
# 3. catalog_manager no longer embeds — build is offline description generation
# ═══════════════════════════════════════════════════════════════════════════

class TestCatalogManagerOffline(unittest.TestCase):

    def test_embedding_api_removed(self):
        self.assertFalse(hasattr(catalog_manager, "build_embeddings"))
        self.assertFalse(hasattr(catalog_manager, "get_api_key"))
        self.assertFalse(hasattr(catalog_manager, "EMBEDDING_MODEL"))
        self.assertFalse(hasattr(catalog_manager, "EMBEDDINGS_NPY"))

    def test_offline_commands_present(self):
        for fn in ("build_descriptions", "validate_catalog", "list_products"):
            self.assertTrue(callable(getattr(catalog_manager, fn, None)),
                            f"catalog_manager.{fn} missing")

    def test_source_has_no_embedding_or_numpy(self):
        src = inspect.getsource(catalog_manager)
        self.assertNotIn("embed_content", src)
        self.assertNotIn("import numpy", src)
        self.assertNotIn("gemini-embedding", src)

    def test_build_descriptions_runs_with_no_api_key(self):
        """build_descriptions is a pure offline template pass — no API key needed."""
        with tempfile.TemporaryDirectory() as td:
            tmp_catalog = os.path.join(td, "catalog.json")
            shutil.copy(CATALOG_JSON, tmp_catalog)

            saved_path = catalog_manager.CATALOG_JSON
            saved_key = os.environ.pop("GEMINI_API_KEY", None)
            try:
                catalog_manager.CATALOG_JSON = tmp_catalog
                with contextlib.redirect_stdout(io.StringIO()):
                    catalog_manager.build_descriptions()
            finally:
                catalog_manager.CATALOG_JSON = saved_path
                if saved_key is not None:
                    os.environ["GEMINI_API_KEY"] = saved_key

            with open(tmp_catalog, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertTrue(data["products"])
            for p in data["products"]:
                self.assertTrue(p.get("description_auto"),
                                f"product {p['id']} got no description")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Search entry points work without embeddings (new signatures)
# ═══════════════════════════════════════════════════════════════════════════

class TestSearchWithoutEmbeddings(unittest.TestCase):

    def test_search_engine_signature_dropped_embedding_params(self):
        params = list(inspect.signature(GlassesSearchEngine.__init__).parameters)
        for p in ("embeddings_path", "index_path", "api_key"):
            self.assertNotIn(p, params)

    def test_inventory_matcher_signature_dropped_embedding_params(self):
        params = list(inspect.signature(InventoryMatcher.__init__).parameters)
        for p in ("api_key", "client", "embeddings_normalized", "index_map"):
            self.assertNotIn(p, params)

    def test_old_embeddings_kwarg_now_rejected(self):
        with self.assertRaises(TypeError):
            GlassesSearchEngine(catalog_path=CATALOG_JSON, embeddings_path="x")

    def test_old_positional_api_key_now_rejected(self):
        with self.assertRaises(TypeError):
            InventoryMatcher(CATALOG_DIR, "fake-api-key")

    def test_search_engine_returns_results(self):
        engine = GlassesSearchEngine(catalog_path=CATALOG_JSON)
        results = engine.search({"frame": {"shape": "round"}}, top_k=3)
        self.assertGreater(len(results), 0)
        self.assertLessEqual(len(results), 3)

    def test_inventory_matcher_returns_results(self):
        matcher = InventoryMatcher(CATALOG_DIR)
        result = matcher.match({"frame": {"shape": "round"}}, top_k=3, gender="men")
        self.assertTrue(result.success)
        self.assertGreater(len(result.matches), 0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. No leftover embedding dependencies in code or requirements
# ═══════════════════════════════════════════════════════════════════════════

class TestNoEmbeddingDependencies(unittest.TestCase):

    def test_requirements_drop_numpy(self):
        with open(os.path.join(ROOT, "requirements.txt"), "r", encoding="utf-8") as f:
            req = f.read().lower()
        self.assertNotIn("numpy", req)

    def test_runtime_sources_have_no_embedding_markers(self):
        for rel in RUNTIME_SOURCES:
            with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
                src = f.read()
            for marker in EMBEDDING_MARKERS:
                self.assertNotIn(marker, src, f"{rel} still references '{marker}'")


# ═══════════════════════════════════════════════════════════════════════════
# 6. The web pipelines search via tag matching, not embeddings
# ═══════════════════════════════════════════════════════════════════════════

class TestPipelinesUseTagMatching(unittest.TestCase):

    def test_free_search_uses_rank_products(self):
        from UI.pipelines import run_free_search_pipeline
        src = inspect.getsource(run_free_search_pipeline)
        self.assertIn("rank_products(", src)
        self.assertNotIn("embed_content", src)

    def test_smart_fit_uses_inventory_matcher(self):
        from UI.pipelines import run_pipeline
        src = inspect.getsource(run_pipeline)
        self.assertIn("InventoryMatcher", src)
        self.assertNotIn("embed_content", src)


if __name__ == "__main__":
    unittest.main()
