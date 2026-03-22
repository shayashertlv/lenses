"""Tests for prompt generation and configuration."""

import sys
import os
import unittest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_engine import build_lens_recolor_prompt
from config import MODEL_MAP, resolve_model, DEFAULT_MODEL, DEFAULT_INTENSITY, DEFAULT_FINISH


class TestPromptEngine(unittest.TestCase):

    def test_basic_prompt_contains_color(self):
        prompt = build_lens_recolor_prompt("ocean blue")
        self.assertIn("ocean blue", prompt)
        self.assertIn("TARGET LENS COLOR: ocean blue", prompt)

    def test_intensity_light(self):
        prompt = build_lens_recolor_prompt("red", intensity="light")
        self.assertIn("25-30% opacity", prompt)
        self.assertIn("light tint", prompt)

    def test_intensity_medium(self):
        prompt = build_lens_recolor_prompt("red", intensity="medium")
        self.assertIn("50-60% opacity", prompt)

    def test_intensity_dark(self):
        prompt = build_lens_recolor_prompt("red", intensity="dark")
        self.assertIn("80-90% opacity", prompt)

    def test_finish_standard(self):
        prompt = build_lens_recolor_prompt("blue", finish="standard")
        self.assertIn("uniform, smooth color tint", prompt)

    def test_finish_gradient(self):
        prompt = build_lens_recolor_prompt("blue", finish="gradient")
        self.assertIn("gradient tint", prompt)
        self.assertIn("darker at the top", prompt)

    def test_finish_mirror(self):
        prompt = build_lens_recolor_prompt("blue", finish="mirror")
        self.assertIn("mirrored finish", prompt)

    def test_finish_polarized(self):
        prompt = build_lens_recolor_prompt("blue", finish="polarized")
        self.assertIn("polarized appearance", prompt)

    def test_preserve_reflections_true(self):
        prompt = build_lens_recolor_prompt("green", preserve_reflections=True)
        self.assertIn("preserve them naturally", prompt)

    def test_preserve_reflections_false(self):
        prompt = build_lens_recolor_prompt("green", preserve_reflections=False)
        self.assertIn("clean tint without additional reflections", prompt)

    def test_preservation_rules_always_present(self):
        prompt = build_lens_recolor_prompt("pink")
        self.assertIn("CRITICAL PRESERVATION RULES", prompt)
        self.assertIn("glasses FRAME", prompt)
        self.assertIn("COMPLETELY UNCHANGED", prompt)
        self.assertIn("photorealistic", prompt)

    def test_creative_color_passthrough(self):
        colors = [
            "cotton candy pink",
            "electric blue",
            "sunset orange gradient",
            "classic aviator green (G-15)",
            "Ray-Ban brown (B-15)",
        ]
        for color in colors:
            prompt = build_lens_recolor_prompt(color)
            self.assertIn(color, prompt)

    def test_invalid_intensity_falls_back_to_medium(self):
        prompt = build_lens_recolor_prompt("blue", intensity="extreme")
        self.assertIn("50-60% opacity", prompt)

    def test_invalid_finish_falls_back_to_standard(self):
        prompt = build_lens_recolor_prompt("blue", finish="holographic")
        self.assertIn("uniform, smooth color tint", prompt)


class TestConfig(unittest.TestCase):

    def test_model_map_has_expected_models(self):
        self.assertIn("nano-banana-pro", MODEL_MAP)
        self.assertIn("nano-banana-2", MODEL_MAP)

    def test_resolve_model_alias(self):
        self.assertEqual(resolve_model("nano-banana-pro"), "gemini-3-pro-image-preview")
        self.assertEqual(resolve_model("nano-banana-2"), "gemini-3.1-flash-image-preview")

    def test_resolve_model_raw_string(self):
        self.assertEqual(resolve_model("gemini-3-pro-image-preview"), "gemini-3-pro-image-preview")

    def test_resolve_model_invalid(self):
        with self.assertRaises(ValueError):
            resolve_model("invalid-model")

    def test_defaults(self):
        self.assertEqual(DEFAULT_MODEL, "nano-banana-2")
        self.assertEqual(DEFAULT_INTENSITY, "medium")
        self.assertEqual(DEFAULT_FINISH, "standard")


if __name__ == "__main__":
    unittest.main()
