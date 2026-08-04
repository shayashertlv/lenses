"""Nano Banana Pro is the only model that may render an image, anywhere.

Four independent config modules each carry their own model map — face_analysis,
lens_recolor, optimal_configuration, and UI — because each subsystem was built
to run standalone. Four copies of a decision is four chances to drift, and a
drift here is invisible: nothing errors, nothing logs, the renders just come
back worse. So the decision is asserted in one place instead of trusted in four.

Text models are a separate question and deliberately not covered here. Face
analysis and query interpretation read; they do not draw.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The one model. Alias as the product says it, id as the API takes it.
ALIAS = "nano-banana-pro"
MODEL_ID = "gemini-3-pro-image-preview"

# Text-only models, allowed to exist and allowed to differ from the above.
TEXT_MODEL_KEYS = {"DEFAULT_ANALYSIS_MODEL", "QUERY_INTERPRETER_MODEL"}


def _maps():
    """Every image-generation model map in the repo, by module path."""
    from face_analysis.config import GENERATION_MODEL_MAP as fa
    import importlib.util

    def load(rel, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    lr = load("lens_recolor/config.py", "_lr_cfg").MODEL_MAP
    oc = load("optimal_configuration/config.py", "_oc_cfg").MODEL_MAP
    from UI.config import FS_MODEL_MAP as ui
    return {
        "face_analysis/config.py": fa,
        "lens_recolor/config.py": lr,
        "optimal_configuration/config.py": oc,
        "UI/config.py": ui,
    }


class TestOnlyNanoBananaProGenerates(unittest.TestCase):

    def test_every_map_offers_exactly_one_model(self):
        for where, mapping in _maps().items():
            with self.subTest(module=where):
                self.assertEqual(
                    dict(mapping), {ALIAS: MODEL_ID},
                    f"{where} offers something other than Nano Banana Pro: {dict(mapping)}",
                )

    def test_the_ui_defaults_point_at_it(self):
        from UI.config import (DEFAULT_GENERATION_MODEL, FS_DEFAULT_MODEL,
                               RECOLOR_MODEL_ALIAS, RECOLOR_MODEL_NAME)
        self.assertEqual(DEFAULT_GENERATION_MODEL, ALIAS)
        self.assertEqual(FS_DEFAULT_MODEL, ALIAS)
        self.assertEqual(RECOLOR_MODEL_ALIAS, ALIAS)
        self.assertEqual(RECOLOR_MODEL_NAME, MODEL_ID)

    def test_a_resolver_refuses_any_other_model(self):
        """The passthrough that used to sit in all three resolvers accepted any
        string starting with "gemini-", so a lesser image model could reach the
        try-on with no map entry and no error."""
        import importlib.util

        def load(rel, name):
            spec = importlib.util.spec_from_file_location(name, ROOT / rel)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        from face_analysis.config import resolve_generation_model
        resolvers = {
            "face_analysis": resolve_generation_model,
            "lens_recolor": load("lens_recolor/config.py", "_lr_r").resolve_model,
            "optimal_configuration": load("optimal_configuration/config.py", "_oc_r").resolve_model,
        }
        rejected = ["gemini-2.5-flash-image", "gemini-2.0-flash-exp-image-generation",
                    "gemini-2.5-flash", "nano-banana", "nano-banana-2", "imagen-3.0"]
        for name, resolve in resolvers.items():
            with self.subTest(resolver=name):
                self.assertEqual(resolve(ALIAS), MODEL_ID)
                self.assertEqual(resolve(MODEL_ID), MODEL_ID)
                for bad in rejected:
                    with self.assertRaises(ValueError, msg=f"{name} accepted {bad}"):
                        resolve(bad)

    def test_no_generate_call_names_a_model_inline(self):
        """A hardcoded model= at a call site bypasses every map above it."""
        offenders = []
        pattern = re.compile(r"""model\s*=\s*["']([^"']+)["']""")
        skip = {"tests", "__pycache__", ".git", ".claude", "node_modules"}
        for path in ROOT.rglob("*.py"):
            if any(part in skip for part in path.parts):
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                m = pattern.search(line)
                if not m:
                    continue
                named = m.group(1)
                if named == MODEL_ID:
                    continue
                # A text model named at a call site is fine; an image one is not.
                if "image" in named or named.startswith("imagen"):
                    offenders.append(f"{path.relative_to(ROOT)}:{i} -> {named}")
        self.assertEqual(offenders, [],
                         f"image models named inline, bypassing the maps: {offenders}")

    def test_config_modules_name_no_other_image_model(self):
        """Catches a stray constant that no map references yet but something
        could start using."""
        offenders = []
        for rel in ("face_analysis/config.py", "lens_recolor/config.py",
                    "optimal_configuration/config.py", "UI/config.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            for found in re.findall(r"['\"](gemini-[a-z0-9.\-]*image[a-z0-9.\-]*)['\"]", text):
                if found != MODEL_ID:
                    offenders.append(f"{rel} -> {found}")
        self.assertEqual(offenders, [],
                         f"a second image model is declared somewhere: {offenders}")


if __name__ == "__main__":
    unittest.main()
