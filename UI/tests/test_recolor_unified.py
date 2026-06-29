"""Regression guard: the UI recolor and the root ``lens_recolor`` feature must
share ONE prompt builder so the two recolor paths can never diverge again.

Run from the repo root:  python -m unittest UI.tests.test_recolor_unified -v
"""

import inspect
import unittest

import UI.pipelines
import lens_recolor.prompt_engine


class TestRecolorPromptUnified(unittest.TestCase):
    def test_ui_uses_canonical_builder(self):
        # UI/pipelines imports build_lens_recolor_prompt from the root feature;
        # the two names must reference the exact same function object.
        self.assertIs(
            UI.pipelines.build_lens_recolor_prompt,
            lens_recolor.prompt_engine.build_lens_recolor_prompt,
        )

    def test_recolor_single_calls_canonical_builder(self):
        src = inspect.getsource(UI.pipelines._recolor_single)
        self.assertIn("build_lens_recolor_prompt(", src)
        # The old duplicate must no longer be referenced.
        self.assertNotIn("_build_recolor_prompt", src)

    def test_duplicate_builders_removed(self):
        # The drifted UI copies are gone — there is only one source of truth.
        self.assertFalse(hasattr(UI.pipelines, "_build_recolor_prompt"))
        self.assertFalse(hasattr(UI.pipelines, "_build_clear_lens_prompt"))

    def test_ui_and_cli_produce_identical_prompt(self):
        # Same color + the UI's fixed settings -> byte-identical prompt on both paths.
        color = "ocean blue"
        ui_prompt = UI.pipelines.build_lens_recolor_prompt(
            color, intensity="medium", finish="standard", preserve_reflections=True,
        )
        cli_prompt = lens_recolor.prompt_engine.build_lens_recolor_prompt(
            color, intensity="medium", finish="standard", preserve_reflections=True,
        )
        self.assertEqual(ui_prompt, cli_prompt)


if __name__ == "__main__":
    unittest.main()
