"""Regression tests for the 'clear' lens bug.

Clear / transparent lenses must NOT be described as a tint in the try-on or
recolor prompts (which made the model render smoke-grey sunglasses). These tests
target the prompt builders the running web app actually uses.
"""

import os
import sys
import unittest

# repo root = .../lenses ; add face_analysis to path for its prompt builder
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "face_analysis"))

from UI.pipelines import _fs_build_tryon_prompt, _build_recolor_prompt, _lens_clauses
from tryon_prompt import build_tryon_prompt as fa_build_tryon_prompt


def _product(lens_type, lens_color):
    return {
        "tags": {
            "frame": {"shape": "round", "material": "metal", "color": ["silver"], "rim_type": "full-rim"},
            "lenses": {"type": [lens_type], "color": [lens_color]},
        }
    }


_ANALYSIS = {
    "face_analysis": {
        "nose": {"bridge_width": "medium"},
        "eyes": {"spacing": "average"},
        "photo_context": {"face_angle": "straight_on"},
    }
}


class TestLensClauses(unittest.TestCase):
    def test_clear_color_is_no_tint(self):
        desc, optical = _lens_clauses("clear", "clear")
        self.assertIn("no tint", desc.lower())
        self.assertIn("NOT sunglasses", optical)
        self.assertNotIn("clear tint", optical.lower())

    def test_empty_color_is_clear(self):
        _, optical = _lens_clauses("prescription-ready", "")
        self.assertIn("NOT sunglasses", optical)

    def test_tinted_color_keeps_tint(self):
        desc, optical = _lens_clauses("sunglasses", "dark-gray")
        self.assertIn("tint", optical.lower())
        self.assertNotIn("NOT sunglasses", optical)

    def test_sunglasses_type_overrides_clear_color(self):
        # defensive: a sunglasses type should never be treated as clear
        _, optical = _lens_clauses("sunglasses", "clear")
        self.assertIn("tint", optical.lower())


class TestFreeSearchTryonPrompt(unittest.TestCase):
    def test_clear_product_no_tint(self):
        p = _fs_build_tryon_prompt(_product("clear", "clear"))
        self.assertIn("NOT sunglasses", p)
        self.assertNotIn("with clear tint", p.lower())

    def test_sunglasses_product_has_tint(self):
        p = _fs_build_tryon_prompt(_product("sunglasses", "dark-gray"))
        self.assertIn("tint", p.lower())
        self.assertNotIn("COMPLETELY CLEAR", p)


class TestSmartFitTryonPrompt(unittest.TestCase):
    def test_clear_product_no_tint(self):
        p = fa_build_tryon_prompt(_ANALYSIS, _product("clear", "clear"))
        self.assertIn("NOT sunglasses", p)
        self.assertNotIn("with clear tint", p.lower())

    def test_sunglasses_product_has_tint(self):
        p = fa_build_tryon_prompt(_ANALYSIS, _product("sunglasses", "dark-gray"))
        self.assertIn("tint", p.lower())


class TestRecolorPrompt(unittest.TestCase):
    def test_clear_removes_tint(self):
        for c in ("Clear", "clear", "transparent", "none"):
            p = _build_recolor_prompt(c)
            self.assertIn("COMPLETELY CLEAR", p, c)
            self.assertNotIn("medium tint", p, c)

    def test_color_keeps_tint(self):
        p = _build_recolor_prompt("Ocean Blue")
        self.assertIn("Ocean Blue", p)
        self.assertIn("medium tint", p)


if __name__ == "__main__":
    unittest.main()
