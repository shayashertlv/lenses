"""Tests for tag schema, description generation, and search engine components."""

import json
import os
import sys
import unittest

# Add parent directory and lenses/ to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tag_schema import generate_product_description, validate_tags, TAG_SCHEMA
from tryon_prompt_builder import build_tryon_prompt
from config import MODEL_MAP, resolve_model, DEFAULT_MODEL, EMBEDDING_MODEL


# Sample product for testing
SAMPLE_PRODUCT = {
    "id": "prod_test",
    "name": "Test Aviator Silver",
    "image": "images/prod_test.png",
    "tags": {
        "frame": {
            "shape": "aviator",
            "material": "metal",
            "color": ["silver"],
            "thickness": "thin",
            "finish": "glossy",
            "rim_type": "full-rim",
        },
        "lenses": {
            "type": ["sunglasses", "polarized"],
            "color": ["G-15-green"],
            "shape": "teardrop",
            "size": "large",
        },
        "style": {
            "aesthetic": ["classic", "bold"],
            "gender_target": "unisex",
            "face_shape_fit": ["oval", "square"],
            "occasion": ["everyday", "outdoor"],
        },
        "product": {
            "brand": "TestBrand",
            "model_name": "Aviator One",
            "price": 89.99,
            "currency": "USD",
            "in_stock": True,
        },
    },
    "description_auto": "",
}


class TestTagSchema(unittest.TestCase):

    def test_schema_has_all_categories(self):
        self.assertIn("frame", TAG_SCHEMA)
        self.assertIn("lenses", TAG_SCHEMA)
        self.assertIn("style", TAG_SCHEMA)
        self.assertIn("product", TAG_SCHEMA)

    def test_frame_shapes_exist(self):
        shapes = TAG_SCHEMA["frame"]["shape"]
        for s in ["round", "square", "aviator", "cat-eye", "oval"]:
            self.assertIn(s, shapes)

    def test_lens_types_exist(self):
        types = TAG_SCHEMA["lenses"]["type"]
        for t in ["clear", "sunglasses", "polarized", "photochromic"]:
            self.assertIn(t, types)


class TestDescriptionGenerator(unittest.TestCase):

    def test_basic_description(self):
        desc = generate_product_description(SAMPLE_PRODUCT)
        self.assertIn("Test Aviator Silver", desc)
        self.assertIn("TestBrand", desc)
        self.assertIn("aviator", desc)
        self.assertIn("metal", desc)
        self.assertIn("silver", desc)

    def test_description_contains_lens_info(self):
        desc = generate_product_description(SAMPLE_PRODUCT)
        self.assertIn("sunglasses", desc)
        self.assertIn("G-15-green", desc)
        self.assertIn("teardrop", desc)

    def test_description_contains_style(self):
        desc = generate_product_description(SAMPLE_PRODUCT)
        self.assertIn("classic", desc)
        self.assertIn("unisex", desc)
        self.assertIn("everyday", desc)

    def test_description_contains_price(self):
        desc = generate_product_description(SAMPLE_PRODUCT)
        self.assertIn("89.99", desc)
        self.assertIn("USD", desc)

    def test_description_in_stock(self):
        desc = generate_product_description(SAMPLE_PRODUCT)
        self.assertIn("in stock", desc)

    def test_description_out_of_stock(self):
        product = json.loads(json.dumps(SAMPLE_PRODUCT))
        product["tags"]["product"]["in_stock"] = False
        desc = generate_product_description(product)
        self.assertIn("out of stock", desc)

    def test_description_list_colors(self):
        product = json.loads(json.dumps(SAMPLE_PRODUCT))
        product["tags"]["frame"]["color"] = ["black", "gold"]
        desc = generate_product_description(product)
        self.assertIn("black", desc)
        self.assertIn("gold", desc)


class TestTagValidation(unittest.TestCase):

    def test_valid_tags(self):
        warnings = validate_tags(SAMPLE_PRODUCT["tags"])
        self.assertEqual(warnings, [])

    def test_missing_category(self):
        tags = {"frame": SAMPLE_PRODUCT["tags"]["frame"]}
        warnings = validate_tags(tags)
        self.assertTrue(any("Missing tag category: lenses" in w for w in warnings))

    def test_unknown_value(self):
        tags = json.loads(json.dumps(SAMPLE_PRODUCT["tags"]))
        tags["frame"]["shape"] = "hexaflexagon"
        warnings = validate_tags(tags)
        self.assertTrue(any("hexaflexagon" in w for w in warnings))


class TestTryOnPrompt(unittest.TestCase):

    def test_prompt_references_two_images(self):
        prompt = build_tryon_prompt(SAMPLE_PRODUCT)
        self.assertIn("IMAGE 1", prompt)
        self.assertIn("IMAGE 2", prompt)

    def test_prompt_contains_frame_details(self):
        prompt = build_tryon_prompt(SAMPLE_PRODUCT)
        self.assertIn("aviator", prompt)
        self.assertIn("metal", prompt)
        self.assertIn("silver", prompt)
        self.assertIn("full-rim", prompt)

    def test_prompt_contains_lens_details(self):
        prompt = build_tryon_prompt(SAMPLE_PRODUCT)
        self.assertIn("sunglasses", prompt)
        self.assertIn("G-15-green", prompt)

    def test_prompt_contains_preservation_rules(self):
        prompt = build_tryon_prompt(SAMPLE_PRODUCT)
        self.assertIn("COMPLETELY IDENTICAL", prompt)
        self.assertIn("photorealistic", prompt)

    def test_prompt_contains_realism_requirements(self):
        prompt = build_tryon_prompt(SAMPLE_PRODUCT)
        self.assertIn("natural shadows", prompt)
        self.assertIn("physical weight", prompt)


class TestConfig(unittest.TestCase):

    def test_model_map(self):
        self.assertIn("nano-banana-pro", MODEL_MAP)
        self.assertNotIn("nano-banana-2", MODEL_MAP)

    def test_resolve_model(self):
        self.assertEqual(resolve_model("nano-banana-pro"), "gemini-3-pro-image-preview")

    def test_resolve_invalid_model(self):
        with self.assertRaises(ValueError):
            resolve_model("invalid-model")

    def test_resolve_raw_model(self):
        self.assertEqual(
            resolve_model("gemini-3-pro-image-preview"),
            "gemini-3-pro-image-preview",
        )

    def test_embedding_model(self):
        self.assertEqual(EMBEDDING_MODEL, "gemini-embedding-001")

    def test_defaults(self):
        self.assertEqual(DEFAULT_MODEL, "nano-banana-pro")


if __name__ == "__main__":
    unittest.main()
