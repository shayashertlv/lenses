import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from modeler.owner_verdict import apply_verdict

GLB_BYTES = b"delivered glb bytes"
GLB_SHA = hashlib.sha256(GLB_BYTES).hexdigest()


def _deliver(job: Path) -> Path:
    """The delivered file on disk: a verdict is bound to its rehashed bytes (2026-09-27), not to the manifest's claim."""
    (job / "deliverable").mkdir(parents=True, exist_ok=True)
    glb = job / "deliverable" / "p.glb"
    glb.write_bytes(GLB_BYTES)
    return glb


def _manifest(status="best_effort", overall="reject", path="x/p.glb"):
    return {
        "job": "j1", "product_id": "p", "written": "2026-09-26T00:00:00", "status": status,
        "status_detail": {"status": status, "reasons": ["evaluator overall: reject"],
                          "provisional": {"front_contour_mean_mm": {"value": 0.1, "limit_mm": 0.6, "pass": True}}},
        "visual_bar_calibrated": False, "delivered_candidate": "c0001",
        "asset": {"path": str(path), "sha256": GLB_SHA, "bytes": len(GLB_BYTES), "triangles": 1, "contract": {"ok": True}},
        "measurements": {"author_visible": {"front_contour_mean_mm": 0.1}, "held_out": {"mean_contour_mm_all_fit_views": 0.9}},
        "evaluation": {"overall": overall, "discrepancies": [{"severity": "major"}], "identity_checklist": []},
        "scale": {"front_width_mm": 140.0, "source": "assumed_default"},
    }


class OwnerVerdictTest(unittest.TestCase):
    def test_accept_upgrades_and_records(self):
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "j1"
            job.mkdir()
            (job / "manifest.json").write_text(json.dumps(_manifest(path=_deliver(job))), encoding="utf-8")
            cal = Path(td) / "cal.jsonl"
            rec = apply_verdict(job, "accept", "live AR try-on", "perfect", sha256=GLB_SHA.upper(), when="2026-09-26T16:40:00+03:00",
                                calibration_file=cal, write_report=False)
            m = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(m["status"], "accepted")
            self.assertEqual(m["status_detail"]["accepted_by"], "owner")
            self.assertEqual(m["status_detail"]["previous_status"], "best_effort")
            self.assertIn("evaluator overall: reject", m["status_detail"]["previous_reasons"])
            self.assertFalse(m["visual_bar_calibrated"])
            self.assertEqual(m["owner_verdict"]["verdict"], "accept")
            self.assertTrue((job / "owner_verdict.json").exists())
            rows = [json.loads(l) for l in cal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["evaluator"]["overall"], "reject")
            self.assertEqual(rows[0]["evaluator"]["major_discrepancies"], 1)
            self.assertEqual(rows[0]["heldout_mean_mm"], 0.9)
            self.assertEqual(rec["status_before"], "best_effort")

    def test_wrong_digest_and_reject(self):
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "j1"
            job.mkdir()
            (job / "manifest.json").write_text(json.dumps(_manifest(overall="accept", path=_deliver(job))), encoding="utf-8")
            cal = Path(td) / "cal.jsonl"
            with self.assertRaises(ValueError):
                apply_verdict(job, "accept", "live", sha256="cd" * 32, calibration_file=cal, write_report=False)
            apply_verdict(job, "reject", "live", "temples too long", calibration_file=cal, write_report=False)
            m = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(m["status"], "best_effort")
            self.assertTrue(m["status_detail"]["owner_rejected"])
            self.assertEqual(m["owner_verdict"]["verdict"], "reject")
            self.assertEqual(len(cal.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
