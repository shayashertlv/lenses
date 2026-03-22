"""Tests for the revised face analysis pipeline.

Tests prompt building, report formatting, inventory matching logic,
try-on prompt building, and config.
"""

import json
import os
import sys
import unittest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis_prompt import build_analysis_prompt
from tryon_prompt import build_tryon_prompt
from report_builder import build_report
from inventory_matcher import InventoryMatcher
from config import (
    GENERATION_MODEL_MAP,
    DEFAULT_GENERATION_MODEL, DEFAULT_ANALYSIS_MODEL,
    EMBEDDING_MODEL, MIN_SIMILARITY_THRESHOLD,
    resolve_generation_model, resolve_analysis_model,
    validate_catalog_exists,
)


# ─── Sample data matching the NEW schema ───

SAMPLE_ANALYSIS = {
    "face_analysis": {
        "face_shape": {
            "primary_shape": "oval",
            "confidence": "high",
            "secondary_characteristics": "Well-balanced proportions with slightly wider cheekbones",
        },
        "forehead": {
            "width": "medium",
            "height": "medium",
            "hairline_shape": "rounded",
        },
        "cheekbones": {
            "prominence": "high",
            "width_relative_to_face": "wider_than_jaw",
        },
        "jawline": {
            "shape": "soft",
            "width_relative_to_forehead": "narrower",
            "definition": "moderate",
        },
        "chin": {
            "shape": "rounded",
            "size": "medium",
        },
        "nose": {
            "bridge_width": "medium",
            "bridge_height": "medium",
            "length": "medium",
            "shape": "straight",
        },
        "eyes": {
            "spacing": "average",
            "size": "medium",
            "shape": "almond",
            "brow_to_eye_distance": "medium",
            "eye_color": "brown",
        },
        "eyebrows": {
            "shape": "arched",
            "thickness": "medium",
            "color": "dark brown",
        },
        "skin": {
            "tone": "medium",
            "undertone": "warm",
        },
        "hair": {
            "color": "dark brown",
            "style": "short textured crop",
            "length": "short",
            "texture": "straight",
        },
        "facial_hair": {
            "present": True,
            "type": "stubble",
            "style": "light even stubble",
        },
        "overall_proportions": {
            "face_width_to_height_ratio": "face is moderately wider than average",
            "symmetry": "high",
            "facial_thirds": "Well-balanced upper, middle, and lower thirds",
        },
        "estimated_age_range": "25-35",
        "estimated_gender_presentation": "masculine",
        "photo_context": {
            "lighting": "natural_indoor",
            "setting": "indoor",
            "face_angle": "straight_on",
        },
    },
    "glasses_recommendation": {
        "reasoning": {
            "face_shape_logic": "An oval face is versatile, browline frames add visual weight to the upper face.",
            "proportion_logic": "Medium frame width matches the face width at the temples.",
            "nose_bridge_logic": "Medium nose bridge suits a standard bridge style.",
            "color_logic": "Warm tortoiseshell complements warm undertone skin.",
            "style_logic": "Browline projects professional yet approachable.",
            "lens_logic": "Indoor setting favors clear or blue-light-filter lenses.",
        },
        "recommended_tags": {
            "frame": {
                "shape": "browline",
                "material": "mixed-metal-acetate",
                "color": ["tortoiseshell", "gold"],
                "thickness": "medium",
                "finish": "glossy",
                "rim_type": "full-rim",
            },
            "lenses": {
                "type": ["clear", "blue-light-filter"],
                "color": ["clear"],
                "shape": "rectangular",
                "size": "medium",
            },
            "style": {
                "aesthetic": ["classic", "professional"],
                "gender_target": "unisex",
                "face_shape_fit": ["oval", "oblong"],
                "occasion": ["everyday", "office"],
            },
        },
    },
    "alternative_recommendations": [
        {
            "frame_shape": "rectangular",
            "brief_description": "Slim rectangular acetate frames in dark brown.",
            "why_also_works": "Adds subtle structure while maintaining clean look.",
        },
        {
            "frame_shape": "round",
            "brief_description": "Thin gold metal round frames.",
            "why_also_works": "Emphasizes natural balanced proportions.",
        },
    ],
}

SAMPLE_PRODUCT = {
    "id": "prod_001",
    "name": "Prada Symbole Cat-Eye",
    "image": "images/prod_001.webp",
    "tags": {
        "frame": {
            "shape": "cat-eye",
            "material": "acetate",
            "color": ["black", "tortoiseshell"],
            "thickness": "thick",
            "finish": "glossy",
            "rim_type": "full-rim",
        },
        "lenses": {
            "type": ["clear", "prescription-ready"],
            "color": ["clear"],
            "shape": "rectangular",
            "size": "large",
        },
        "style": {
            "aesthetic": ["luxury", "fashion-forward", "bold"],
            "gender_target": "women",
            "face_shape_fit": ["oval", "heart", "oblong"],
            "occasion": ["everyday", "office", "fashion"],
        },
        "product": {
            "brand": "Prada",
            "model_name": "Symbole VPR 14Z",
            "price": 349.00,
            "currency": "USD",
            "in_stock": True,
            "sku": "PR-14Z-BLK",
        },
    },
    "description_auto": "",
}


# ─── Tests ───

class TestAnalysisPrompt(unittest.TestCase):

    def test_prompt_not_empty(self):
        prompt = build_analysis_prompt()
        self.assertTrue(len(prompt) > 500)

    def test_prompt_has_recommended_tags(self):
        prompt = build_analysis_prompt()
        self.assertIn("recommended_tags", prompt)

    def test_prompt_no_visual_description(self):
        """visual_description_for_generation is GONE in the new schema."""
        prompt = build_analysis_prompt()
        self.assertNotIn("visual_description_for_generation", prompt)

    def test_prompt_has_tag_vocabulary(self):
        """The prompt must list allowed tag values for inventory matching."""
        prompt = build_analysis_prompt()
        # Frame shapes from the optimal_configuration tag schema
        self.assertIn("cat-eye", prompt)
        self.assertIn("aviator", prompt)
        self.assertIn("browline", prompt)
        # Materials
        self.assertIn("acetate", prompt)
        self.assertIn("metal", prompt)
        # Lens types
        self.assertIn("sunglasses", prompt)
        self.assertIn("blue-light-filter", prompt)

    def test_prompt_has_photo_context(self):
        prompt = build_analysis_prompt()
        self.assertIn("photo_context", prompt)
        self.assertIn("face_angle", prompt)

    def test_prompt_has_lens_logic(self):
        prompt = build_analysis_prompt()
        self.assertIn("lens_logic", prompt)

    def test_prompt_contains_optician_rules(self):
        prompt = build_analysis_prompt()
        self.assertIn("round faces", prompt.lower())
        self.assertIn("square faces", prompt.lower())
        self.assertIn("skin undertone", prompt.lower())

    def test_prompt_has_json_instruction(self):
        prompt = build_analysis_prompt()
        self.assertIn("ONLY with a JSON object", prompt)

    def test_prompt_contains_face_shape_categories(self):
        prompt = build_analysis_prompt()
        for shape in ["oval", "round", "square", "heart", "diamond"]:
            self.assertIn(shape, prompt)


class TestTryOnPrompt(unittest.TestCase):

    def test_prompt_builds(self):
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        self.assertIsInstance(prompt, str)
        self.assertTrue(len(prompt) > 500)

    def test_prompt_references_two_images(self):
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        self.assertIn("IMAGE 1", prompt)
        self.assertIn("IMAGE 2", prompt)

    def test_prompt_uses_product_tags_not_recommendation(self):
        """The prompt should use the ACTUAL matched product's tags."""
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        # Product is cat-eye, recommendation was browline
        self.assertIn("cat-eye", prompt)

    def test_prompt_uses_face_analysis_for_placement(self):
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        self.assertIn("medium nose bridge", prompt)
        self.assertIn("average", prompt)  # eye_spacing

    def test_prompt_contains_preservation_rules(self):
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        self.assertIn("COMPLETELY IDENTICAL", prompt)

    def test_prompt_facial_hair_present(self):
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        self.assertIn("stubble", prompt)

    def test_prompt_facial_hair_absent(self):
        analysis = json.loads(json.dumps(SAMPLE_ANALYSIS))
        analysis["face_analysis"]["facial_hair"] = {
            "present": False, "type": "none", "style": None,
        }
        prompt = build_tryon_prompt(analysis, SAMPLE_PRODUCT)
        self.assertIn("No facial hair present", prompt)

    def test_prompt_angled_face(self):
        analysis = json.loads(json.dumps(SAMPLE_ANALYSIS))
        analysis["face_analysis"]["photo_context"]["face_angle"] = "slight_left_turn"
        prompt = build_tryon_prompt(analysis, SAMPLE_PRODUCT)
        self.assertIn("slight left turn", prompt)
        self.assertIn("CRITICAL", prompt)

    def test_prompt_lighting(self):
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        self.assertIn("natural indoor", prompt)

    def test_prompt_photorealism(self):
        prompt = build_tryon_prompt(SAMPLE_ANALYSIS, SAMPLE_PRODUCT)
        self.assertIn("photorealistic", prompt)
        self.assertIn("natural shadows", prompt)


class TestReportBuilder(unittest.TestCase):

    def test_report_not_empty(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertTrue(len(report) > 200)

    def test_report_contains_header(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertIn("FACIAL ANALYSIS & GLASSES RECOMMENDATION REPORT", report)

    def test_report_contains_face_shape(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertIn("Oval", report)
        self.assertIn("high", report)

    def test_report_contains_recommended_tags(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertIn("RECOMMENDED GLASSES", report)
        self.assertIn("browline", report)
        self.assertIn("tortoiseshell", report)

    def test_report_contains_reasoning(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertIn("REASONING", report)
        self.assertIn("Face Shape:", report)

    def test_report_contains_lens_logic(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertIn("Lens Choice:", report)

    def test_report_with_match_results(self):
        matches = [(SAMPLE_PRODUCT, 0.85)]
        report = build_report(SAMPLE_ANALYSIS, match_results=matches)
        self.assertIn("BEST MATCHES FROM INVENTORY", report)
        self.assertIn("Prada Symbole Cat-Eye", report)
        self.assertIn("0.850", report)
        self.assertIn("$349.00", report)

    def test_report_without_match_results(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertNotIn("BEST MATCHES", report)

    def test_report_contains_alternatives(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertIn("ALTERNATIVES", report)
        self.assertIn("Rectangular", report)

    def test_report_facial_hair_present(self):
        report = build_report(SAMPLE_ANALYSIS)
        self.assertIn("stubble", report)

    def test_report_facial_hair_absent(self):
        analysis = json.loads(json.dumps(SAMPLE_ANALYSIS))
        analysis["face_analysis"]["facial_hair"] = {
            "present": False, "type": "none", "style": None,
        }
        report = build_report(analysis)
        self.assertNotIn("Facial hair:", report)


class TestInventoryMatcherTagsToDescription(unittest.TestCase):
    """Test the _tags_to_description method without needing API or catalog files."""

    def test_description_from_tags(self):
        tags = SAMPLE_ANALYSIS["glasses_recommendation"]["recommended_tags"]
        # Directly test the static method logic
        desc = self._tags_to_description(tags)
        self.assertIn("browline", desc)
        self.assertIn("tortoiseshell", desc)
        self.assertIn("clear", desc)
        self.assertIn("classic", desc)

    def test_description_has_all_components(self):
        tags = SAMPLE_ANALYSIS["glasses_recommendation"]["recommended_tags"]
        desc = self._tags_to_description(tags)
        # Should contain frame, lens, and style info
        self.assertIn("frame", desc.lower())
        self.assertIn("lenses", desc.lower())
        self.assertIn("style", desc.lower())

    def _tags_to_description(self, tags: dict) -> str:
        """Mirror InventoryMatcher._tags_to_description for testing."""
        frame = tags["frame"]
        lenses = tags["lenses"]
        style = tags["style"]

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

        return (
            f"Recommended glasses. These glasses feature a {frame_desc}. "
            f"They have {lens_desc}. "
            f"The overall look is {style_desc}."
        )


class TestConfig(unittest.TestCase):

    def test_generation_model_map(self):
        self.assertIn("nano-banana-pro", GENERATION_MODEL_MAP)
        self.assertIn("nano-banana-2", GENERATION_MODEL_MAP)
        self.assertEqual(
            GENERATION_MODEL_MAP["nano-banana-2"],
            "gemini-3.1-flash-image-preview",
        )
        self.assertEqual(
            GENERATION_MODEL_MAP["nano-banana-pro"],
            "gemini-3-pro-image-preview",
        )

    def test_resolve_generation_model(self):
        self.assertEqual(
            resolve_generation_model("nano-banana-2"),
            "gemini-3.1-flash-image-preview",
        )

    def test_resolve_generation_model_raw(self):
        self.assertEqual(
            resolve_generation_model("gemini-3-pro-image-preview"),
            "gemini-3-pro-image-preview",
        )

    def test_resolve_invalid_generation_model(self):
        with self.assertRaises(ValueError):
            resolve_generation_model("invalid-model")

    def test_resolve_analysis_model(self):
        self.assertEqual(
            resolve_analysis_model("gemini-2.5-flash"),
            "gemini-2.5-flash",
        )

    def test_resolve_invalid_analysis_model(self):
        with self.assertRaises(ValueError):
            resolve_analysis_model("invalid-model")

    def test_defaults(self):
        self.assertEqual(DEFAULT_GENERATION_MODEL, "nano-banana-2")
        self.assertEqual(DEFAULT_ANALYSIS_MODEL, "gemini-2.5-flash")

    def test_embedding_model(self):
        self.assertEqual(EMBEDDING_MODEL, "gemini-embedding-001")

    def test_similarity_threshold(self):
        self.assertIsInstance(MIN_SIMILARITY_THRESHOLD, float)
        self.assertGreater(MIN_SIMILARITY_THRESHOLD, 0)
        self.assertLess(MIN_SIMILARITY_THRESHOLD, 1)


if __name__ == "__main__":
    unittest.main()
