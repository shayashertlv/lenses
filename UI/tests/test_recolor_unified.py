"""Regression guard: the UI recolor and the root ``lens_recolor`` feature must
share ONE prompt builder so the two recolor paths can never diverge again.

Run from the repo root:  python -m unittest UI.tests.test_recolor_unified -v
"""

import inspect
import re
import unittest
from pathlib import Path

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


def _js_function(src, name):
    """The body of a top-level `function name(...) { ... }`, brace-matched."""
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", src)
    if not m:
        return None
    depth = 0
    for j in range(m.end() - 1, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.end():j]
    return None


class TestRecolorProvenance(unittest.TestCase):
    """A recolour must carry the identity of the frame it was made from.

    It once assembled itself at Apply time from four independent globals —
    lastTryonB64, currentProductId, pendingProduct and the colour. openJobResult
    wrote the first three together, but openTryon, openSmartFit and showJobError
    each rewrote pendingProduct alone. Touching a product card between opening a
    result and applying a colour therefore produced a ticket carrying one
    frame's pixels under another frame's name, thumbnail and id: a recolour of a
    pair of glasses the user never selected, and a "Shop this frame" pointing at
    a third.

    The fix is structural rather than defensive — one captured object, read
    whole — so these tests guard the structure, not a symptom.
    """

    IDENTITY_FIELDS = ("productId", "productName", "thumbSrc", "sourceB64", "sourceJobId")
    # Globals that describe "some product, somewhere" rather than the result on
    # screen. The recolour path reading any of them is the original defect.
    AMBIENT = ("pendingProduct", "currentProductId", "lastTryonB64")

    @classmethod
    def setUpClass(cls):
        cls.js = (Path(__file__).resolve().parent.parent
                  / "static" / "js" / "storefront.js").read_text(encoding="utf-8")
        # The retired names are still named in the comment that explains why
        # they were retired, which is worth keeping. Assert against code only.
        cls.code = re.sub(r"/\*.*?\*/|//[^\n]*", "", cls.js, flags=re.S)

    def test_every_identity_field_comes_from_one_object(self):
        """The heart of it: if all fields resolve to the same root identifier,
        no interleaving can split pixels from identity."""
        body = _js_function(self.js, "startRecolor")
        self.assertIsNotNone(body, "startRecolor not found")
        call = re.search(r"createJob\(\{(.*?)\}\)", body, re.S)
        self.assertIsNotNone(call, "startRecolor no longer builds a job with createJob({...})")

        roots = {}
        for field in self.IDENTITY_FIELDS:
            m = re.search(re.escape(field) + r"\s*:\s*([^,\n]+)", call.group(1))
            self.assertIsNotNone(m, f"recolour job no longer sets {field}")
            roots[field] = set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\.", m.group(1)))

        every = set().union(*roots.values())
        self.assertEqual(
            len(every), 1,
            f"recolour identity is assembled from {len(every)} sources {sorted(every)}; "
            f"per field: { {k: sorted(v) for k, v in roots.items()} }",
        )

    def test_recolor_path_reads_no_ambient_product_state(self):
        for fn in ("startRecolor", "showRecolorPicker"):
            body = _js_function(self.js, fn)
            self.assertIsNotNone(body, f"{fn} not found")
            for name in self.AMBIENT:
                self.assertNotIn(
                    name, body,
                    f"{fn} reads {name}, which any other flow can rewrite independently",
                )

    def test_retired_globals_are_gone_entirely(self):
        """Left in the file they would invite the same assembly-at-use-time."""
        for name in ("lastTryonB64", "currentProductId"):
            self.assertNotIn(name, self.code, f"{name} still exists in code")

    def test_picker_refuses_without_a_captured_result(self):
        body = _js_function(self.js, "showRecolorPicker")
        self.assertRegex(
            body, r"if\s*\(\s*!\s*openResult",
            "the colour picker opens without checking that a result was captured",
        )

    def test_capture_is_never_written_piecemeal(self):
        """`openResult.thumbSrc = x` would reintroduce the split write the whole
        fix exists to prevent."""
        self.assertIsNone(
            re.search(r"openResult\s*\.\s*\w+\s*=(?!=)", self.js),
            "openResult is mutated field-by-field somewhere; capture it whole or not at all",
        )


if __name__ == "__main__":
    unittest.main()
