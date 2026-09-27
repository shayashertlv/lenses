import json
from pathlib import Path
import tempfile
import unittest

from audit_saved_runs import ANGLES, VERDICT_KEYS, collect, lens_evidence, sha256


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def job(self, identifier="one", name="Frame One"):
        folder = self.root / identifier
        (folder / "references").mkdir(parents=True)
        job = {"id": identifier, "name": name, "status": "waiting", "stage": "review",
               "lens_layout": "shield", "references": [], "artifacts": {}, "current": None,
               "notes": "PRIVATE NOTES", "operations": [{"secret": "PRIVATE RECEIPT"}]}
        for angle in ANGLES:
            data = f"normalized-{angle}".encode()
            (folder / "references" / f"{angle}.jpg").write_bytes(data)
            job["references"].append({"angle": angle, "url": f"/files/{angle}"})
            job["artifacts"][angle] = {"sha256": sha256(data), "path": "ignored"}
        self.save(identifier, job)
        return job

    def save(self, identifier, job):
        (self.root / identifier / "job.json").write_text(json.dumps(job), encoding="utf-8")

    def quality(self):
        return {"inspection": {"lens_quality": {"verdict": dict.fromkeys(VERDICT_KEYS, True),
                "seam": {"Lens": {"loops": 2, "outline_vertices": 40, "tooth_mm": 0.1,
                                   "gap_mm": 0, "gap_vertices": 0}}}}}

    def test_missing_and_empty_measurements_never_qualify_reported_pass(self):
        for current in ({}, {"inspection": {}}, {"inspection": {"lens_quality": {}}}):
            evidence = lens_evidence(current, "shield")
            self.assertEqual(evidence["reported_status"], "unknown")
            self.assertFalse(evidence["eligible_reported_pass"])
        current = self.quality()
        current["inspection"]["lens_quality"]["seam"] = {}
        evidence = lens_evidence(current, "shield")
        self.assertEqual(evidence["reported_status"], "pass")
        self.assertEqual(evidence["boundary_coverage"], "unknown")
        self.assertFalse(evidence["eligible_reported_pass"])

    def test_zero_or_partial_boundaries_cannot_pass(self):
        for key in ("loops", "outline_vertices"):
            for value in (0, None, -1, "40", True):
                current = self.quality()
                current["inspection"]["lens_quality"]["seam"]["Lens"][key] = value
                evidence = lens_evidence(current, "shield")
                self.assertFalse(evidence["eligible_reported_pass"], (key, value))
                self.assertEqual(evidence["boundary_coverage"], "incomplete")
        self.assertFalse(lens_evidence(self.quality(), "pair")["eligible_reported_pass"])

    def test_positive_boundaries_only_qualify_complete_reported_verdict(self):
        current = self.quality()
        self.assertTrue(lens_evidence(current, "shield")["eligible_reported_pass"])
        current["inspection"]["lens_quality"]["verdict"].pop("see_through")
        self.assertFalse(lens_evidence(current, "shield")["eligible_reported_pass"])
        current["inspection"]["lens_quality"]["verdict"]["all"] = False
        self.assertEqual(lens_evidence(current, "shield")["reported_status"], "fail")

    def test_verified_duplicates_group_by_images_not_product_name(self):
        self.job("one", "Frame A")
        self.job("two", "Different Name")
        report = collect(self.root)
        self.assertEqual(report["duplicate_reference_set_groups"][0]["job_ids"], ["one", "two"])
        self.assertEqual(len(report["product_name_groups"]), 2)
        self.assertEqual(report["counts"]["verified_reference_jobs"], 2)
        self.assertEqual(report["counts"]["distinct_verified_reference_sets"], 1)
        self.assertEqual(report["jobs"][0]["references"]["status"], "verified")
        self.assertEqual(len(report["jobs"][0]["source"]["sha256"]), 64)
        serialized = json.dumps(report)
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("normalized-front", serialized)

    def test_tampered_or_unverifiable_reference_rejects_grouping(self):
        self.job("one")
        job = self.job("two")
        (self.root / "two" / "references" / "front.jpg").write_bytes(b"tampered")
        report = collect(self.root)
        self.assertFalse(report["duplicate_reference_set_groups"])
        self.assertEqual(report["counts"]["verified_reference_jobs"], 1)
        self.assertEqual(report["counts"]["distinct_verified_reference_sets"], 1)
        second = report["jobs"][1]["references"]
        self.assertEqual(second["status"], "rejected")
        self.assertIn({"angle": "front", "reason": "sha256_mismatch"}, second["issues"])
        del job["artifacts"]["front"]["sha256"]
        self.save("two", job)
        self.assertIsNone(collect(self.root)["jobs"][1]["references"]["reference_set_sha256"])

    def test_malformed_jobs_isolated_and_nested_receipts_ignored(self):
        self.job()
        for identifier, data in (("bad", b"{broken"), ("array", b"[]")):
            folder = self.root / identifier
            folder.mkdir()
            (folder / "job.json").write_bytes(data)
        nested = self.root / "one" / "receipts"
        nested.mkdir()
        (nested / "job.json").write_bytes(b"do not read")
        report = collect(self.root)
        self.assertEqual(report["counts"]["readable_jobs"], 1)
        self.assertEqual(report["counts"]["malformed_jobs"], 2)
        self.assertEqual(report["counts"]["job_files"], 3)
        self.assertTrue(all(item["sha256"] for item in report["malformed_jobs"]))


if __name__ == "__main__":
    unittest.main()
