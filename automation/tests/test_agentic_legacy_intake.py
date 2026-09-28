"""Regressions for the 2026-09-27 audit's intake findings. F13a: a constructed silhouette must refresh its compact
forms (``silhouette_mm_64``) so no program reads the outline it replaced. F13b: a front width stated in the request is
authoritative; a size marking read from a photo or the listing is recorded as a hypothesis, never applied. F7: the
stage record makes completion visible (``intake_stage.reading.status``, ``intake_stage.review.status``,
``intake_stage.finished``) on every return path, and a preview never touches evidence.json. Scripted drivers, synthetic
evidence and dummy photo files only: no photo set, no API."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from bsa.front import resample_closed
from modeler import intake_astra as ia
from modeler.request import Request

try:                                                     # ``python -m pytest`` from automation/ (the root on sys.path)
    from tests import test_modeler_intake_astra as fixtures
except ImportError:                                      # pytest's prepend mode (tests/ itself on sys.path)
    import test_modeler_intake_astra as fixtures

READING, REVIEW, synthetic_evidence = fixtures.READING, fixtures.REVIEW, fixtures.synthetic_evidence
STATUSES = {"complete", "failed", "skipped"}


def audit_front() -> dict:
    """The audit's exact reproduction: a fragmentary measured silhouette, a stale compact form, two lens outlines."""
    return {"front_width_mm": 140, "silhouette_mm": [[-60, 0], [-58, 1], [-55, 0]], "silhouette_mm_64": [["stale"]],
            "lenses": [{"outline_mm": [[-50, -10], [-10, -10], [-10, 10], [-50, 10]]}, {"outline_mm": [[10, -10], [50, -10], [50, 10], [10, 10]]}]}


def compact_of(sil: list, n: int) -> list:
    """What ``augment_evidence`` derives from a silhouette: the only consistent compact form."""
    return np.round(resample_closed(np.asarray(sil, float), n), 3).tolist()


class ConstructedSilhouetteRefreshesItsCompactForms(unittest.TestCase):
    def test_audit_reproduction_leaves_no_stale_compact_form(self):
        front, prov = audit_front(), []
        self.assertTrue(ia.construct_silhouette(front, 3, prov, force=True))
        self.assertEqual(front["silhouette_source"], "constructed")
        self.assertNotIn("stale", json.dumps(front["silhouette_mm_64"]))
        self.assertEqual(len(front["silhouette_mm_64"]), 64)
        self.assertEqual(front["silhouette_mm_64"], compact_of(front["silhouette_mm"], 64), "the compact form is derived from the NEW outline")
        self.assertEqual(front["code_measured"]["silhouette_mm"], [[-60, 0], [-58, 1], [-55, 0]], "the measured fragment is kept for the record")
        self.assertEqual(prov[-1]["field"], "front.silhouette_mm")
        self.assertEqual(prov[-1]["regenerated"], ["silhouette_mm_64"], "the provenance row says what was regenerated")

    def test_every_silhouette_derivative_is_refreshed_and_none_is_invented(self):
        front = audit_front()
        front["silhouette_mm_48"] = [["stale"]]
        front["lenses"][0]["outline_mm_64"] = [["lens cache, not the silhouette's"]]
        prov = []
        self.assertTrue(ia.construct_silhouette(front, 3, prov, force=True))
        self.assertEqual(front["silhouette_mm_48"], compact_of(front["silhouette_mm"], 48))
        self.assertEqual(sorted(prov[-1]["regenerated"]), ["silhouette_mm_48", "silhouette_mm_64"])
        self.assertEqual(front["lenses"][0]["outline_mm_64"], [["lens cache, not the silhouette's"]], "lens caches derive from the lens outlines, untouched")
        bare = audit_front()
        del bare["silhouette_mm_64"]
        self.assertTrue(ia.construct_silhouette(bare, 3, [], force=True))
        self.assertNotIn("silhouette_mm_64", bare, "no compact form is added where none existed")

    def test_refresh_helper_regenerates_or_deletes(self):
        front = {"silhouette_mm": [[0, 0], [10, 0], [10, 5], [0, 5]], "silhouette_mm_64": [["stale"]], "other_64": "kept"}
        self.assertEqual(ia.refresh_silhouette_derivatives(front), ["silhouette_mm_64"])
        self.assertEqual(front["silhouette_mm_64"], compact_of(front["silhouette_mm"], 64))
        self.assertEqual(front["other_64"], "kept")
        empty = {"silhouette_mm": None, "silhouette_mm_64": [["stale"]]}
        self.assertEqual(ia.refresh_silhouette_derivatives(empty), ["silhouette_mm_64"])
        self.assertNotIn("silhouette_mm_64", empty, "a compact form with no source is deleted, never left stale")

    def test_review_patch_path_keeps_the_compact_form_consistent(self):
        ev = synthetic_evidence()
        r, l = ev["front"]["lenses"]
        r["outline_mm"] = [[10, -15], [57, -15], [57, 9], [10, 9]]
        l["outline_mm"] = [[-57, -15], [-10, -15], [-10, 9], [-57, 9]]
        ev["front"]["silhouette_mm"] = [[5, -20], [70, -20], [70, 15], [5, 15]]            # the right half only: fragmentary
        ev["front"]["silhouette_mm_64"] = [["stale"]]
        ia.apply_reading(ev, ia.validate_reading(copy.deepcopy(READING)), listing_text="")
        ia.apply_review(ev, ia.validate_review(copy.deepcopy(REVIEW)))
        f = ev["front"]
        self.assertEqual(f["silhouette_source"], "constructed")
        self.assertEqual(f["silhouette_mm_64"], compact_of(f["silhouette_mm"], 64))
        xs = [p[0] for p in f["silhouette_mm_64"]]
        self.assertLess(abs(min(xs) + max(xs)), 5.0, "the compact form spans both halves like the constructed outline")


def stated_evidence(width: float = 138.0) -> dict:
    ev = synthetic_evidence()
    ev["scale"] = {"front_width_mm": width, "source": "stated_in_request", "uncertainty_mm": 2.0}
    ev["dimensions_stated"] = {"front_width_mm": width}
    ev["front"]["front_width_mm"] = width
    return ev


class StatedWidthSurvivesASizeMarking(unittest.TestCase):
    def test_scale_from_marking_refuses_to_overwrite_a_stated_width(self):
        reading = ia.validate_reading(copy.deepcopy(READING))
        self.assertIsNone(ia.scale_from_marking(stated_evidence(), reading))
        hyp = ia.marking_hypothesis(stated_evidence(), reading)
        # lens + bridge over the lip-invariant span (66.97 mm at the stated 138) is what the marking WOULD give
        self.assertAlmostEqual(hyp["front_width_mm"], 138.0 * (49.0 + 22.0) / 66.97, places=1)
        self.assertEqual(hyp["marking_source"], "listing_text"); self.assertEqual(hyp["confidence"], 0.9)
        self.assertEqual(hyp["note"], "stated dimension kept"); self.assertEqual(hyp["stated_front_width_mm"], 138.0)
        self.assertEqual(hyp["method"], "lens_plus_bridge_over_span")

    def test_an_assumed_default_is_still_replaced_and_carries_no_hypothesis(self):
        reading = ia.validate_reading(copy.deepcopy(READING))
        ev = synthetic_evidence()
        self.assertEqual(ev["scale"]["source"], "assumed_default")
        s = ia.scale_from_marking(ev, reading)
        self.assertEqual(s["provenance"]["source"], "size_marking")
        self.assertAlmostEqual(s["front_width_mm"], 140.0 * (49.0 + 22.0) / 66.97, places=1)
        self.assertIsNone(ia.marking_hypothesis(ev, reading))

    def test_no_hypothesis_without_a_legible_marking(self):
        low = copy.deepcopy(READING); low["size_marking"]["confidence"] = 0.3
        self.assertIsNone(ia.marking_hypothesis(stated_evidence(), ia.validate_reading(low)))
        none = copy.deepcopy(READING); none["size_marking"]["source"] = "none"
        self.assertIsNone(ia.marking_hypothesis(stated_evidence(), ia.validate_reading(none)))


class FailingReading:
    name = "failing"

    def decide(self, *a, **k):
        raise RuntimeError("vision unavailable")


class StageJob:
    """A job folder the stage can run on without photos: dummy image files, a request.json and an evidence dir."""

    def __init__(self, tmp: str, evidence: dict, *, stated: float | None = None):
        self.dir = Path(tmp) / "job"
        (self.dir / "evidence").mkdir(parents=True)
        for name in ("a.jpg", "b.webp", "front.jpg", "p4.jpg"):
            (self.dir / name).write_bytes(name.encode())
        req = {"product_id": "stated_test", "notes": "listing 49 22 145",
               "photos": [{"path": str(self.dir / "a.jpg"), "view": "front"}, {"path": str(self.dir / "b.webp"), "view": "unknown"}],
               "dimensions": {"front_width_mm": stated} if stated else {}, "limits": {}, "author": {"driver": "scripted"}, "held_out_views": ["angled"]}
        (self.dir / "request.json").write_text(json.dumps(req), encoding="utf-8")
        self.request = Request.from_dict(req)
        self.evidence = evidence
        for vid, fname in (("front", "front.jpg"), ("photo04", "p4.jpg")):
            self.evidence["views"][vid]["author_photo"]["path"] = str(self.dir / fname)
        for row, fname in zip(self.evidence["inputs"], ("a.jpg", "b.webp")):
            row["source_path"] = str(self.dir / fname)

    def saved(self) -> dict:
        return json.loads((self.dir / "evidence" / "evidence.json").read_text(encoding="utf-8"))


def quiet_reading() -> dict:
    """The fixture reading with nothing that would re-measure: no folded tips above the front, views as labelled."""
    r = copy.deepcopy(READING)
    r["photos"][0]["folded_temples_visible_above_front"] = False
    return r


def assert_finished(tc: unittest.TestCase, record: dict) -> None:
    tc.assertIs(record["finished"], True)
    tc.assertIn(record["reading"]["status"], STATUSES)
    tc.assertIn(record["review"]["status"], STATUSES)
    tc.assertIn("seconds", record)


class StageRecordShowsCompletion(unittest.TestCase):
    def test_stated_width_kept_through_the_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = StageJob(tmp, stated_evidence(138.0), stated=138.0)
            d = ia.ScriptedIntake({"reading": quiet_reading(), "review": REVIEW})
            evidence, record = ia.run_intake_stage(job.request, job.dir, job.evidence, d, d, log=lambda *a: None)
            self.assertEqual(record["reading"]["status"], "complete"); self.assertEqual(record["review"]["status"], "complete")
            assert_finished(self, record)
            self.assertFalse(record.get("remeasured"), "nothing to re-measure: no relabel, no band, and the marking is not adopted")
            self.assertEqual(evidence["scale"]["front_width_mm"], 138.0); self.assertEqual(evidence["scale"]["source"], "stated_in_request")
            hyp = evidence["scale"]["marking_hypothesis"]
            self.assertEqual(hyp["note"], "stated dimension kept"); self.assertAlmostEqual(hyp["front_width_mm"], 138.0 * 71.0 / 66.97, places=1)
            self.assertTrue(any("stated front width 138.0 mm kept" in a for a in record["actions"]), record["actions"])
            self.assertFalse(any(a.startswith("scale: 138.0 mm nominal") for a in record["actions"]), "no rescale action")
            saved = job.saved()
            self.assertEqual(saved["scale"]["front_width_mm"], 138.0); self.assertEqual(saved["scale"]["marking_hypothesis"]["note"], "stated dimension kept")
            self.assertIs(saved["intake_stage"]["finished"], True)
            self.assertEqual(saved["intake_stage"]["review"]["status"], "complete")

    def test_failed_reading_returns_a_finished_record_with_both_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = StageJob(tmp, synthetic_evidence())
            evidence, record = ia.run_intake_stage(job.request, job.dir, job.evidence, FailingReading(), FailingReading(), log=lambda *a: None)
            self.assertEqual(record["reading"]["status"], "failed"); self.assertIn("RuntimeError: vision unavailable", record["reading"]["error"])
            self.assertEqual(record["review"]["status"], "skipped")
            assert_finished(self, record)
            self.assertEqual(record["errors"], ["reading: RuntimeError: vision unavailable"])
            self.assertIs(evidence["intake_stage"], record)
            self.assertNotIn("intake_reading", evidence, "code-only evidence: nothing was patched")
            saved = job.saved()
            self.assertEqual(saved["intake_stage"]["reading"]["status"], "failed")
            self.assertEqual(saved["intake_stage"]["review"]["status"], "skipped")
            self.assertIs(saved["intake_stage"]["finished"], True)

    def test_failed_review_still_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = StageJob(tmp, synthetic_evidence())
            d = ia.ScriptedIntake({"reading": quiet_reading()})           # no review answer: the review driver raises
            evidence, record = ia.run_intake_stage(job.request, job.dir, job.evidence, d, d, log=lambda *a: None)
            self.assertEqual(record["reading"]["status"], "complete"); self.assertEqual(record["review"]["status"], "failed")
            assert_finished(self, record)
            self.assertEqual(evidence["front"]["rim_class"], "full", "the reading's patches are still applied")
            self.assertIs(job.saved()["intake_stage"]["finished"], True)

    def test_preview_never_touches_evidence_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = StageJob(tmp, synthetic_evidence())
            sentinel = json.dumps({"sentinel": True}).encode()
            (job.dir / "evidence" / "evidence.json").write_bytes(sentinel)
            request_before = (job.dir / "request.json").read_bytes()
            # the early return (failed reading) in preview mode
            _, record = ia.run_intake_stage(job.request, job.dir, copy.deepcopy(job.evidence), FailingReading(), FailingReading(), log=lambda *a: None, write=False)
            assert_finished(self, record)
            self.assertEqual((job.dir / "evidence" / "evidence.json").read_bytes(), sentinel)
            preview = job.dir / "evidence" / "intake_astra" / "evidence.preview.json"
            self.assertIs(json.loads(preview.read_text(encoding="utf-8"))["intake_stage"]["finished"], True)
            # the full path in preview mode, with a reading that would re-measure (folded tips): not applied, still finished
            d = ia.ScriptedIntake({"reading": READING, "review": REVIEW})
            _, record = ia.run_intake_stage(job.request, job.dir, copy.deepcopy(job.evidence), d, d, log=lambda *a: None, write=False)
            assert_finished(self, record)
            self.assertFalse(record.get("remeasured"))
            self.assertTrue(any(a.startswith("preview:") for a in record["actions"]))
            self.assertEqual((job.dir / "evidence" / "evidence.json").read_bytes(), sentinel)
            self.assertEqual((job.dir / "request.json").read_bytes(), request_before, "a preview leaves request.json alone too")
            self.assertEqual(json.loads(preview.read_text(encoding="utf-8"))["intake_stage"]["review"]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
