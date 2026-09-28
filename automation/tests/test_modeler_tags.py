"""Asset tags and calibration coverage by kind: an uncovered tag withholds ``accepted`` even with a calibrated bar."""
import unittest

from modeler import calibration, evaluate as mevaluate, tags


def _eval(overall="accept"):
    return mevaluate.validate_evaluation({"discrepancies": [], "identity_checklist": [{"feature": "f", "verdict": "present"}],
                                          "resemblance_0_10": {"mirror_overall": 8}, "overall": overall, "summary": "s"})


class Tags(unittest.TestCase):
    def test_kind_and_modifier_tags(self):
        evidence = {"front": {"layout": "single", "rim_class": "mixed"}}
        materials = {"materials": {"crystal": {"kind": "translucent"}, "lens": {"kind": "lens", "lens": {"mirror": True}}, "gold": {"kind": "metal"}}}
        self.assertEqual(tags.asset_tags(evidence, materials), ["single", "rim_mixed", "translucent", "mirrored"])
        self.assertEqual(tags.asset_tags({"front": {"layout": "pair", "rim_class": "full"}}, None), ["pair", "rim_full"])
        self.assertEqual(tags.asset_tags(None, {"materials": {"a": {"kind": "acetate"}}}), [])
        self.assertEqual(tags.modifier_tags({"crystal": {"kind": "translucent"}}), ["translucent"])   # bare material map too

    def test_row_tags_are_taken_from_the_row_when_recorded(self):
        self.assertEqual(tags.tags_for_row({"tags": ["pair", "rim_full"], "job": "x"}), ["pair", "rim_full"])

    def test_modifier_tags_come_from_the_glb_when_no_material_record_exists(self):
        import tempfile
        from pathlib import Path
        from bsa import export as bsa_export
        from modeler import export as mexport
        parts, materials = bsa_export.synthetic_parts()
        materials["lens"] = mexport.material_spec("lens", {"lens": {"transmission_top_rgb": [0.3, 0.3, 0.3], "transmission_bottom_rgb": [0.6, 0.6, 0.6],
                                                                    "profile": "smooth", "reflectance_rgb": [0.5, 0.2, 0.4], "mirror": True,
                                                                    "angular": None, "roughness": 0.05}}, lens=True)
        materials["frame"] = mexport.material_spec("crystal", {"kind": "translucent", "base_color_linear": [0.9, 0.9, 0.9], "roughness": 0.08,
                                                               "translucent": {"thickness_mm": 4.0, "attenuation_rgb_linear": [0.9, 0.9, 0.9],
                                                                               "attenuation_distance_mm": 4.0, "ior": 1.49, "transmission": 1.0}}, lens=False)
        with tempfile.TemporaryDirectory() as tmp:
            glb = Path(tmp) / "a.glb"
            bsa_export.write_glb(parts, materials, glb)
            self.assertEqual(tags.modifier_tags_from_glb(glb), ["translucent", "mirrored"])
            self.assertEqual(tags.asset_tags({"front": {"layout": "pair", "rim_class": "full"}}, None, glb), ["pair", "rim_full", "translucent", "mirrored"])
            # a material record wins over the GLB when present
            self.assertEqual(tags.asset_tags(None, {"materials": {"a": {"kind": "acetate"}}}, glb), [])


class Coverage(unittest.TestCase):
    def _row(self, owner, tag_list, auto="accept", agree=True):
        return {"owner": owner, "tags": tag_list, "automatic": None if auto is None else {"verdict": auto}, "agree": agree}

    def test_two_agreeing_verdicts_cover_a_tag_and_a_disagreement_uncovers_it(self):
        table = [self._row("accept", ["pair", "rim_full"]), self._row("reject", ["pair", "rim_full"], auto="reject"),
                 self._row("accept", ["translucent"]), self._row("borderline", ["translucent"]),
                 self._row("accept", ["mirrored"]), self._row("accept", ["mirrored"], auto="reject", agree=False),
                 self._row("accept", ["rim_half"], auto=None)]
        cov = calibration.coverage(table)
        self.assertTrue(cov["pair"]["covered"]); self.assertTrue(cov["rim_full"]["covered"])
        self.assertFalse(cov["translucent"]["covered"]); self.assertEqual(cov["translucent"]["accepts"], 1); self.assertEqual(cov["translucent"]["borderline"], 1)
        self.assertFalse(cov["mirrored"]["covered"]); self.assertEqual(cov["mirrored"]["disagreements"], 1)
        self.assertFalse(cov["rim_half"]["covered"]); self.assertEqual(cov["rim_half"]["unevaluated"], 1)

    def test_uncovered_tag_withholds_accepted_with_a_named_reason(self):
        metrics = {"front_contour_mean_mm": 0.3, "lens_outline_mean_mm": 0.4, "ar_continuity_failure": None}
        s = mevaluate.decide_status(candidate_valid=True, metrics=metrics, heldout=None, evaluation=_eval(), input_flags=[],
                                    protocol_calibrated=True, tags=["pair", "rim_full", "translucent"], uncovered_tags=["translucent"])
        self.assertEqual(s["automatic_verdict"], "accept")
        self.assertEqual(s["status"], "best_effort")
        self.assertTrue(any("translucent" in r and "coverage" in r for r in s["reasons"]))
        self.assertEqual(s["tags"], ["pair", "rim_full", "translucent"]); self.assertEqual(s["uncovered_tags"], ["translucent"])
        covered = mevaluate.decide_status(candidate_valid=True, metrics=metrics, heldout=None, evaluation=_eval(), input_flags=[],
                                          protocol_calibrated=True, tags=["pair", "rim_full"], uncovered_tags=[])
        self.assertEqual(covered["status"], "accepted")
        uncalibrated = mevaluate.decide_status(candidate_valid=True, metrics=metrics, heldout=None, evaluation=_eval(), input_flags=[],
                                               protocol_calibrated=False, tags=["pair"], uncovered_tags=["pair"])
        self.assertEqual(uncalibrated["status"], "best_effort")
        self.assertTrue(any("not calibrated" in r for r in uncalibrated["reasons"]))
        # the tags travel on every status, including the early ones (a dry run without an evaluation, a failed job)
        unverified = mevaluate.decide_status(candidate_valid=True, metrics=metrics, heldout=None, evaluation=None, input_flags=[],
                                             protocol_calibrated=True, tags=["pair", "translucent"], uncovered_tags=["translucent"])
        self.assertEqual(unverified["status"], "quality_unverified"); self.assertEqual(unverified["tags"], ["pair", "translucent"])
        failed = mevaluate.decide_status(candidate_valid=False, metrics=None, heldout=None, evaluation=None, input_flags=[],
                                         protocol_calibrated=True, tags=["pair"], uncovered_tags=[])
        self.assertEqual(failed["status"], "execution_failed"); self.assertEqual(failed["tags"], ["pair"])


class AssetGlb(unittest.TestCase):
    """One asset_glb (modeler.tags); calibration uses it as is, evaluate_asset raises FileNotFoundError on None. Changed
    2026-09-29: calibration no longer raises on a malformed baseline.json (the asset reads as asset_missing) and
    evaluate_asset raises FileNotFoundError, not KeyError, for a baseline.json without ``glb``."""

    def _dir(self, tmp, baseline_text=None, model=False):
        from pathlib import Path
        d = Path(tmp) / "asset"
        d.mkdir()
        if model:
            (d / "model.glb").write_bytes(b"glb")
        if baseline_text is not None:
            (d / "baseline.json").write_text(baseline_text, encoding="utf-8")
        return d

    def test_one_implementation(self):
        from modeler import evaluate_asset
        self.assertIs(calibration.asset_glb, tags.asset_glb)
        self.assertIsNot(evaluate_asset.asset_glb, tags.asset_glb)

    def test_resolution_and_failure_modes(self):
        import json
        import tempfile
        from pathlib import Path
        from modeler import evaluate_asset
        from modeler.paths import AUTOMATION
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, json.dumps({"glb": "x.glb"}), model=True)
            self.assertEqual(tags.asset_glb(d), d / "model.glb")                  # model.glb wins over baseline.json
        with tempfile.TemporaryDirectory() as tmp:
            d = self._dir(tmp, json.dumps({"glb": "data/b.glb"}))
            self.assertEqual(tags.asset_glb(d), AUTOMATION / "data/b.glb")        # relative to automation/
            self.assertEqual(evaluate_asset.asset_glb(Path(tmp), d), AUTOMATION / "data/b.glb")
        for text in ("{not json", json.dumps({"other": 1}), json.dumps({"glb": ""}), json.dumps(["b.glb"]), None):
            with tempfile.TemporaryDirectory() as tmp:
                d = self._dir(tmp, text)
                self.assertIsNone(tags.asset_glb(d), text)
                self.assertIsNone(calibration.asset_glb(d), text)
                with self.assertRaises(FileNotFoundError, msg=text):
                    evaluate_asset.asset_glb(Path(tmp), d)

    def test_calibration_reads_a_malformed_baseline_as_asset_missing(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            jobs = Path(tmp)
            d = jobs / "j" / "baselines" / "b"
            (d / "observe").mkdir(parents=True)
            (d / "observe" / "observation.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
            (d / calibration.EVALUATION_DIR).mkdir()
            (d / calibration.EVALUATION_DIR / "evaluation.json").write_text(json.dumps(
                {"evaluation": _eval(), "asset_sha256": "ab" * 32, "meta": {"protocol": mevaluate.PROTOCOL}}), encoding="utf-8")
            (d / "baseline.json").write_text("{not json", encoding="utf-8")
            row = {"kind": "baseline", "job": "j", "baseline": "b"}
            self.assertEqual(calibration.automatic_verdict_status(row, jobs=jobs), {"verdict": None, "reason": "asset_missing"})


if __name__ == "__main__":
    unittest.main()
