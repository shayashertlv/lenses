import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from modeler import calibration, evaluate as mevaluate


def _eval(overall="accept", majors=0):
    return mevaluate.validate_evaluation({"discrepancies": [{"severity": "major", "part": "frame", "view": "front", "description": "x"}] * majors,
                                          "identity_checklist": [{"feature": "f", "verdict": "present"}],
                                          "resemblance_0_10": {"mirror_overall": 8}, "overall": overall, "summary": "s"})


class Gates(unittest.TestCase):
    def test_continuity_failure_is_a_gate_and_heldout_is_not(self):
        s = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.1, "lens_outline_mean_mm": 0.2,
                                                                   "ar_continuity_failure": "An original posterior arm cross-section is missing."},
                                    heldout={"summary": {"mean_contour_mm_all_fit_views": 0.5}}, evaluation=_eval(), input_flags=[], protocol_calibrated=True)
        self.assertEqual(s["automatic_verdict"], "reject")
        self.assertFalse(s["provisional"]["ar_temple_continuity"]["pass"])
        s2 = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.1, "lens_outline_mean_mm": 0.2, "ar_continuity_failure": None},
                                     heldout={"summary": {"mean_contour_mm_all_fit_views": 2.0}}, evaluation=_eval(), input_flags=[], protocol_calibrated=True)
        self.assertEqual(s2["automatic_verdict"], "accept")
        self.assertEqual(s2["status"], "accepted")
        self.assertFalse(s2["provisional"]["heldout_contour_mean_mm"]["gate"])
        self.assertFalse(s2["provisional"]["front_contour_mean_mm"]["gate"])
        self.assertTrue(any("report-only" in r for r in s2["reasons"]))

    def test_front_contour_does_not_gate(self):
        # the owner accepted a shield at 0.71 mm front contour while rejected assets score 0.07-0.60: front is report-only
        s = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.71, "lens_outline_mean_mm": 0.75, "ar_continuity_failure": None},
                                    heldout=None, evaluation=_eval(), input_flags=[], protocol_calibrated=True)
        self.assertEqual(s["automatic_verdict"], "accept")

    def test_lens_gate(self):
        s = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.3, "lens_outline_mean_mm": 1.1, "ar_continuity_failure": None},
                                    heldout=None, evaluation=_eval(), input_flags=[], protocol_calibrated=True)
        self.assertEqual(s["automatic_verdict"], "reject")
        self.assertEqual(s["status"], "best_effort")

    def test_majors_are_reported_not_blocking(self):
        # the owner accepted three assets whose evaluators listed one major each while accepting overall (2026-09-26)
        s = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.5, "lens_outline_mean_mm": 0.7, "ar_continuity_failure": None},
                                    heldout=None, evaluation=_eval("accept", majors=1), input_flags=[], protocol_calibrated=True)
        self.assertEqual(s["automatic_verdict"], "accept")
        self.assertEqual(s["major_discrepancies"], 1)
        self.assertTrue(any("major" in r for r in s["reasons"]))
        s2 = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.5, "lens_outline_mean_mm": 0.7, "ar_continuity_failure": None},
                                     heldout=None, evaluation=_eval("reject", majors=0), input_flags=[], protocol_calibrated=True)
        self.assertEqual(s2["automatic_verdict"], "reject")

    def test_inconsistent_inputs_status_is_gone(self):
        # removed 2026-09-29: 'lens_count_mismatch' had no producer, so 'inconsistent_inputs' was unreachable. A legacy
        # flag of that name is now an ordinary input flag: it is listed nowhere and an unclean result is best_effort.
        from modeler import owner_verdict
        self.assertNotIn("inconsistent_inputs", mevaluate.STATUSES)
        s = mevaluate.decide_status(candidate_valid=True, metrics={"front_contour_mean_mm": 0.3, "lens_outline_mean_mm": 1.1, "ar_continuity_failure": None},
                                    heldout=None, evaluation=_eval(), input_flags=["lens_count_mismatch", "low_resolution"], protocol_calibrated=True)
        self.assertEqual(s["status"], "best_effort")
        self.assertIn("input flags: ['low_resolution']", s["reasons"])
        self.assertEqual(owner_verdict.non_owner_status({"status_detail": {"clean": False, "input_flags": ["lens_count_mismatch"]}}), "best_effort")


class CalibrationSet(unittest.TestCase):
    def _asset(self, jobs: Path, job: str, kind: str, name: str, *, lens: float, continuity, evaluation: dict | None):
        d = jobs / job / ("baselines" if kind == "baseline" else "candidates") / name
        (d / "observe").mkdir(parents=True)
        (d / "observe" / "observation.json").write_text(json.dumps({"summary": {"front_contour_mean_mm": 0.2, "lens_outline_mean_mm": lens,
                                                                                 "ar_runtime_compatible": True, "ar_continuity_failure": continuity}}), encoding="utf-8")
        # the asset's bytes: a stored evaluation counts only when bound to them (asset_sha256 + meta.protocol; 2026-09-27)
        glb = d / ("b.glb" if kind == "baseline" else "model.glb")
        glb.write_bytes(f"glb-{job}-{name}".encode())
        if kind == "baseline":
            (d / "baseline.json").write_text(json.dumps({"glb": str(glb)}), encoding="utf-8")
        if evaluation is not None:
            (d / "evaluation_v2").mkdir()
            (d / "evaluation_v2" / "evaluation.json").write_text(json.dumps({"evaluation": evaluation, "asset_sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
                                                                             "meta": {"protocol": mevaluate.PROTOCOL}}), encoding="utf-8")
        return {"kind": kind, "job": job, "product_id": "p", "candidate" if kind == "delivered" else "baseline": name,
                "asset_sha256": name * 8, "verdict": None, "note": ""}

    def test_summary_requires_both_classes_and_agreement(self):
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td)
            rows = []
            for i in range(3):
                r = self._asset(jobs, f"j{i}", "delivered", f"c000{i}", lens=0.3, continuity=None, evaluation=_eval("accept"))
                rows.append(dict(r, verdict="accept"))
            for i in range(2):
                r = self._asset(jobs, f"j{i}", "baseline", f"b{i}", lens=1.2, continuity=None, evaluation=_eval("reject"))
                rows.append(dict(r, verdict="reject"))
            s = calibration.summary(rows, jobs=jobs)
            self.assertFalse(s["calibrated"])
            self.assertEqual(s["owner_accepts_evaluated"], 3)
            self.assertEqual(s["owner_rejects_evaluated"], 2)
            r = self._asset(jobs, "j9", "baseline", "b9", lens=0.2, continuity="missing arm", evaluation=_eval("accept"))
            rows.append(dict(r, verdict="reject"))
            s = calibration.summary(rows, jobs=jobs)
            self.assertTrue(s["calibrated"], s["reasons"])
            # a disagreement breaks it
            r = self._asset(jobs, "j8", "delivered", "c0008", lens=0.2, continuity=None, evaluation=_eval("reject"))
            rows.append(dict(r, verdict="accept"))
            s = calibration.summary(rows, jobs=jobs)
            self.assertFalse(s["calibrated"])
            self.assertEqual(s["disagreements"], 1)
            # borderline rows are reported, never counted
            r = self._asset(jobs, "j7", "delivered", "c0007", lens=0.2, continuity=None, evaluation=_eval("reject"))
            rows.append(dict(r, verdict="borderline"))
            s = calibration.summary(rows, jobs=jobs)
            self.assertEqual(s["borderline"], 1)
            self.assertEqual(s["disagreements"], 1)

    def test_unevaluated_assets_are_reported(self):
        with tempfile.TemporaryDirectory() as td:
            jobs = Path(td)
            r = self._asset(jobs, "j0", "delivered", "c0000", lens=0.3, continuity=None, evaluation=None)
            s = calibration.summary([dict(r, verdict="accept")], jobs=jobs)
            self.assertEqual(s["unevaluated"], 1)
            self.assertFalse(s["calibrated"])


class ArchivePreviousRun(unittest.TestCase):
    def test_answered_package_is_kept_under_previous(self):
        from modeler.evaluate_asset import archive_previous_run
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "evaluation_v2"
            out.mkdir()
            for name in ("request.json", "response.json", "evaluation.json"):
                (out / name).write_text("{}", encoding="utf-8")
            dst = archive_previous_run(out)
            self.assertEqual(dst.name, "run-1")
            self.assertFalse((out / "response.json").exists())
            self.assertTrue((dst / "response.json").exists())
            (out / "response.json").write_text("{}", encoding="utf-8")
            self.assertEqual(archive_previous_run(out).name, "run-2")


if __name__ == "__main__":
    unittest.main()
