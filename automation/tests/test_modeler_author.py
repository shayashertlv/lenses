"""Author protocol validation, request composition and the candidate store (no Blender, no network)."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from modeler import author as mauthor
from modeler import evaluate as mevaluate
from modeler.candidates import CandidateStore


class DecisionValidation(unittest.TestCase):
    def test_submit_program_ok(self):
        d = mauthor.validate_decision({"decision": "submit_program", "modules": {"frame": "x = 1\n"}, "base": None, "rationale": "r"})
        self.assertEqual(d["modules"], {"frame": "x = 1\n"})
        self.assertIsNone(d["base"])

    def test_deliver_if_valid_flag(self):
        d = mauthor.validate_decision({"decision": "submit_program", "modules": {"materials": "x = 1\n"}, "base": "incumbent", "rationale": "r"})
        self.assertFalse(d["deliver_if_valid"])
        d2 = mauthor.validate_decision({"decision": "submit_program", "modules": {"materials": "x = 1\n"}, "base": "incumbent", "rationale": "r",
                                        "deliver_if_valid": True})
        self.assertTrue(d2["deliver_if_valid"])

    def test_submit_program_rejects_unknown_module_and_syntax(self):
        with self.assertRaises(ValueError):
            mauthor.validate_decision({"decision": "submit_program", "modules": {"rims": "x = 1"}})
        with self.assertRaises(ValueError):
            mauthor.validate_decision({"decision": "submit_program", "modules": {"frame": "def (:\n"}})
        with self.assertRaises(ValueError):
            mauthor.validate_decision({"decision": "submit_program", "modules": {"frame": "x" * 70_000}})

    def test_request_views_bounds(self):
        d = mauthor.validate_decision({"decision": "request_views", "views": [{"id": "a", "yaw": 30, "pitch": 10, "px_per_mm": 6}]})
        self.assertEqual(d["views"][0]["target"], "bbox")
        with self.assertRaises(ValueError):
            mauthor.validate_decision({"decision": "request_views", "views": []})
        with self.assertRaises(ValueError):
            mauthor.validate_decision({"decision": "request_views", "views": [{"id": "a", "px_per_mm": 99}]})

    def test_finish(self):
        d = mauthor.validate_decision({"decision": "finish", "deliver": "c0002", "status_claim": "improved", "note": "n"})
        self.assertEqual(d["deliver"], "c0002")
        with self.assertRaises(ValueError):
            mauthor.validate_decision({"decision": "finish", "deliver": "latest"})

    def test_unknown_decision(self):
        with self.assertRaises(ValueError):
            mauthor.validate_decision({"decision": "edit"})


class HelperReference(unittest.TestCase):
    def test_reference_lists_core_helpers(self):
        ref = mauthor.helper_reference()
        for name in ("gl.plate_with_holes(", "gl.lens_solid(", "gl.loft(", "gl.sweep_profile(", "gl.material_lens(", "gl.register("):
            self.assertIn(name, ref)
        self.assertLess(len(ref), 40_000)


class StoreInheritance(unittest.TestCase):
    def test_inherit_and_replace_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CandidateStore(Path(tmp))
            a = store.create({"frame": "A_FRAME\n", "temples": "A_TEMPLES\n"}, base=None, turn=0, rationale="", author_ref="t0")
            b = store.create({"temples": "B_TEMPLES\n"}, base=a, turn=1, rationale="", author_ref="t1")
            self.assertEqual(b.record["module_order"], ["frame", "temples"])
            self.assertEqual(b.modules()["frame"], "A_FRAME\n")
            self.assertEqual(b.modules()["temples"], "B_TEMPLES\n")
            self.assertEqual(b.record["inherited_modules"], {"frame": "c0000"})
            self.assertEqual(b.record["changed_modules"], ["temples"])
            self.assertNotEqual(a.record["program_set_sha256"], b.record["program_set_sha256"])
            self.assertEqual(a.status(), "unbuilt")
            self.assertIsNone(a.score())
            self.assertIsNone(store.incumbent())


class StatusRule(unittest.TestCase):
    def test_no_valid_candidate(self):
        s = mevaluate.decide_status(candidate_valid=False, metrics=None, heldout=None, evaluation=None, input_flags=[], protocol_calibrated=False)
        self.assertEqual(s["status"], "execution_failed")

    def test_uncalibrated_bar_never_accepts(self):
        ev = mevaluate.validate_evaluation({"discrepancies": [], "identity_checklist": [{"feature": "f", "verdict": "present"}],
                                            "resemblance_0_10": {"front": 9}, "overall": "accept", "summary": "s"})
        s = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.3, "lens_outline_mean_mm": 0.2},
                                    heldout={"summary": {"mean_contour_mm_all_fit_views": 0.5}}, evaluation=ev, input_flags=[], protocol_calibrated=False)
        self.assertEqual(s["status"], "best_effort")
        self.assertTrue(any("not calibrated" in r for r in s["reasons"]))
        s2 = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.3, "lens_outline_mean_mm": 0.2},
                                     heldout={"summary": {"mean_contour_mm_all_fit_views": 0.5}}, evaluation=ev, input_flags=[], protocol_calibrated=True)
        self.assertEqual(s2["status"], "accepted")

    def test_missing_evaluation_is_unverified(self):
        s = mevaluate.decide_status(candidate_valid=True, metrics={}, heldout=None, evaluation=None, input_flags=[], protocol_calibrated=False)
        self.assertEqual(s["status"], "quality_unverified")


if __name__ == "__main__":
    unittest.main()
