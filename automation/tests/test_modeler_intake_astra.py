"""The Astra intake stage: strict schemas, validators, the code actions taken from a reading and a review, and the
stage run offline with scripted answers on a real photo set (no paid call)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

import pytest

from modeler import evaluate as mevaluate, intake_astra as ia, tags
from modeler.request import Request
from reconstruction import segmented_astra_transport as transport

INPUTS = Path(__file__).resolve().parents[1] / "data/modeler/inputs/tomford_ft1123d"   # not cwd-relative

READING = {
    "photos": [{"id": "front", "view": "front", "straight_on": True, "folded_temples_visible_above_front": True, "floor_reflection_present": False, "note": "temple tips show above the brow"},
               {"id": "back", "view": "back", "straight_on": True, "folded_temples_visible_above_front": True, "floor_reflection_present": False, "note": ""},
               {"id": "left", "view": "left", "straight_on": True, "folded_temples_visible_above_front": False, "floor_reflection_present": True, "note": ""},
               {"id": "photo04", "view": "rear_angled", "straight_on": False, "folded_temples_visible_above_front": False, "floor_reflection_present": True, "note": "shows the temple print"}],
    "product": {"layout": "pair", "rim_class": "full", "frame_material": "crystal", "frame_colour": "clear champagne crystal", "rim_thickness_mm": 5.0,
                "front_shape": "round panto", "bridge": "low saddle", "endpieces": "compact, hinge visible through the crystal", "temples": "crystal, gold core wire",
                "hardware": "gold T inlay at the hinge, gold oval tip plaque", "branding": "TOM FORD grey print upper outer right lens", "lens_finish": "flash_mirror",
                "lens_colour": "light brown", "mirror_colour": "champagne gold", "lens_print_location": "right lens, upper outer"},
    "size_marking": {"lens_mm": 49.0, "bridge_mm": 22.0, "temple_mm": 145.0, "source": "listing_text", "confidence": 0.9},
    "symmetric_product": True, "photos_consistent": True,
    "identity_features": ["round panto crystal front", "flash mirror champagne lenses", "gold T inlay at the hinges", "gold core wire inside the clear temples"],
    "product_description": "Round panto sunglasses in clear crystal acetate with light brown flash-mirror lenses and gold accents.",
    "author_cautions": ["the lobes above the endpieces in the front photo are the folded temple tips, not the front"],
}
REVIEW = {"rim_class_matches": False, "rim_widths_trustworthy": False, "thickness_trustworthy": False, "silhouette_includes_temple_tips": True, "silhouette_complete": False,
          "lenses": [{"side": "R", "outline_trust": {"top": "clipped_by_reflection", "bottom": "trusted", "inner": "trusted", "outer": "trusted"}, "shape_family": "round", "height_over_width": 0.9},
                     {"side": "L", "outline_trust": {"top": "clipped_by_reflection", "bottom": "trusted", "inner": "trusted", "outer": "trusted"}, "shape_family": "round", "height_over_width": 0.9}],
          "notes": "the crystal rim is invisible to the matte; the lens tops stop under the flash reflection"}


def synthetic_evidence() -> dict:
    lens = lambda side, x0, x1: {"side": side, "outline_mm": [], "outline_px": [], "edge_type": [], "rim_width_mm": [0.5, 0.7, 0.6],
                                 "box_mm": {"width_A": 46.89, "height_B": 37.48, "x_range": [x0, x1], "y_range": [-28.95, 8.54]},
                                 "rim_class": "rimless", "rim_w_median_mm": 0.656, "type_fractions": {"frame": 0.86, "free": 0.14, "rimless": 0.0}}
    return {"product_id": "x", "notes": "listing", "scale": {"front_width_mm": 140.0, "source": "assumed_default", "uncertainty_mm": 14.0},
            "views": {"front": {"id": "front", "view": "front", "size": [1500, 1000], "flags": ["mirror_iou_low"], "author_photo": {"path": "front.jpg", "size": [964, 434]}},
                      "photo04": {"id": "photo04", "view": "unknown", "size": [1380, 776], "flags": [], "author_photo": {"path": "p4.jpg", "size": [1024, 431]}}},
            "held_out": {"angled": {"id": "angled", "view": "angled"}},
            "inputs": [{"id": "front", "view": "front", "source_path": "a.jpg"}, {"id": "photo04", "view": "unknown", "source_path": "b.webp"}],
            "front": {"front_width_mm": 140.0, "layout": "pair", "rim_class": "rimless", "flags": ["rimless_review"],
                      "thickness_mm": [{"side": "R", "brow_mm": 1.87, "bottom_rim_mm": 0.97, "endpiece_mm": 4.46}, {"side": "L", "brow_mm": 1.87, "bottom_rim_mm": 0.97, "endpiece_mm": 4.46}],
                      "lenses": [lens("R", 10.04, 56.93), lens("L", -56.93, -10.04)]}}


class Schemas(unittest.TestCase):
    def test_both_tools_pass_the_transport_strict_rules(self):
        for tool in (ia.READING_TOOL, ia.REVIEW_TOOL):
            for name, t in tool.items():
                transport.validate_tools_schema(t["parameters"])

    def test_validators_accept_the_fixtures_and_reject_bad_values(self):
        r = ia.validate_reading(copy.deepcopy(READING))
        self.assertEqual(r["product"]["rim_class"], "full"); self.assertEqual(r["size_marking"]["lens_mm"], 49.0)
        v = ia.validate_review(copy.deepcopy(REVIEW))
        self.assertEqual(v["lenses"][0]["outline_trust"]["top"], "clipped_by_reflection")
        bad = copy.deepcopy(READING); bad["product"]["rim_class"] = "wire"
        with self.assertRaises(ValueError):
            ia.validate_reading(bad)
        bad = copy.deepcopy(READING); bad["size_marking"]["lens_mm"] = 300
        with self.assertRaises(ValueError):
            ia.validate_reading(bad)
        none = copy.deepcopy(READING); none["size_marking"] = {"lens_mm": 49, "bridge_mm": None, "temple_mm": None, "source": "none", "confidence": 0.9}
        self.assertIsNone(ia.validate_reading(none)["size_marking"]["lens_mm"], "source none means nothing was read")


class Decisions(unittest.TestCase):
    def test_view_overrides_relabel_unknown_without_duplicates(self):
        ev = synthetic_evidence()
        reading = ia.validate_reading(copy.deepcopy(READING))
        req = Request("x", [], {}, "", {}, {}, ("angled",))
        overrides, notes = ia.view_overrides(req, ev, reading)
        self.assertEqual(overrides, {}, "rear_angled maps to unknown, which photo04 already is")
        reading["photos"][3]["view"] = "front"
        overrides, notes = ia.view_overrides(req, ev, reading)
        self.assertEqual(overrides, {}); self.assertTrue(any("already taken" in n for n in notes))
        reading["photos"][3]["view"] = "right"
        overrides, _ = ia.view_overrides(req, ev, reading)
        self.assertEqual(overrides, {"photo04": "right"})

    def test_scale_from_a_read_size_marking(self):
        ev = synthetic_evidence()
        # lens + bridge over the lip-invariant span (outer edge of R to the inner edge of L): 56.93 - (-10.04) = 66.97 mm at 140
        s = ia.scale_from_marking(ev, ia.validate_reading(copy.deepcopy(READING)))
        self.assertEqual(s["provenance"]["method"], "lens_plus_bridge_over_span")
        self.assertAlmostEqual(s["front_width_mm"], 140.0 * (49.0 + 22.0) / 66.97, places=1)
        self.assertEqual(s["provenance"]["source"], "size_marking")
        # the lens size alone falls back to the visible aperture with the wider uncertainty
        lens_only = copy.deepcopy(READING); lens_only["size_marking"]["bridge_mm"] = None
        s2 = ia.scale_from_marking(ev, ia.validate_reading(lens_only))
        self.assertEqual(s2["provenance"]["method"], "lens_over_aperture")
        self.assertAlmostEqual(s2["front_width_mm"], 140.0 * 49.0 / 46.89, places=1)
        self.assertGreater(s2["provenance"]["uncertainty_mm"], s["provenance"]["uncertainty_mm"])
        low = copy.deepcopy(READING); low["size_marking"]["confidence"] = 0.3
        self.assertIsNone(ia.scale_from_marking(ev, ia.validate_reading(low)))

    def test_band_rim_only_when_the_tips_show_above_the_front(self):
        ev = synthetic_evidence()
        self.assertEqual(ia.band_rim_mm(ev, ia.validate_reading(copy.deepcopy(READING))), 5.0)
        flat = copy.deepcopy(READING); flat["photos"][0]["folded_temples_visible_above_front"] = False
        self.assertIsNone(ia.band_rim_mm(ev, ia.validate_reading(flat)))

    def test_apply_reading_replaces_the_crystal_rim_values_and_keeps_the_code_values(self):
        ev = synthetic_evidence()
        prov = ia.apply_reading(ev, ia.validate_reading(copy.deepcopy(READING)), listing_text="listing")
        f = ev["front"]
        self.assertEqual(f["rim_class"], "full"); self.assertEqual(f["code_measured"]["rim_class"], "rimless")
        self.assertEqual(f["lenses"][0]["rim_w_median_mm"], 5.0); self.assertEqual(f["code_measured"]["lens_R_rim_w_median_mm"], 0.656)
        self.assertEqual(f["thickness_mm"][0]["brow_mm"], 5.0); self.assertEqual(f["code_measured"]["thickness_mm"][0]["brow_mm"], 1.87)
        self.assertNotIn("input_flags_downgraded", ev, "the mirror IoU flag never capped a status; nothing to downgrade")
        self.assertTrue(ev["notes"].startswith("listing")); self.assertEqual(len(ev["identity_features_vision"]), 4)
        self.assertTrue(any(p["field"] == "front.rim_class" for p in prov))
        self.assertEqual(tags.kind_tags(ev), ["pair", "rim_full"], "tags follow the reading")
        opaque = copy.deepcopy(READING); opaque["product"]["frame_material"] = "opaque_acetate"
        ev2 = synthetic_evidence()
        ia.apply_reading(ev2, ia.validate_reading(opaque), listing_text="")
        self.assertEqual(ev2["front"]["rim_class"], "rimless", "an opaque frame keeps the code's rim class")

    def test_apply_review_makes_the_lens_gate_report_only(self):
        ev = synthetic_evidence()
        ia.apply_reading(ev, ia.validate_reading(copy.deepcopy(READING)), listing_text="")
        ia.apply_review(ev, ia.validate_review(copy.deepcopy(REVIEW)))
        self.assertEqual(ev["gate_overrides"]["lens_outline_mean_mm"]["mode"], "report_only")
        self.assertEqual(ev["front"]["evidence_reliability"]["lens_outline_trust"]["R"]["top"], "clipped_by_reflection")
        ev_ok = synthetic_evidence()
        trusted = copy.deepcopy(REVIEW)
        for l in trusted["lenses"]:
            l["outline_trust"] = {k: "trusted" for k in ia.ARCS}
        ia.apply_review(ev_ok, ia.validate_review(trusted))
        self.assertNotIn("gate_overrides", ev_ok)

    def test_decide_status_honours_a_report_only_override(self):
        metrics = {"front_contour_mean_mm": 0.5, "lens_outline_mean_mm": 0.88, "ar_continuity_failure": None}
        ev = mevaluate.validate_evaluation({"discrepancies": [], "identity_checklist": [{"feature": "f", "verdict": "present"}],
                                            "resemblance_0_10": {"mirror_overall": 8}, "overall": "accept", "summary": "s"})
        gated = mevaluate.decide_status(candidate_valid=True, metrics=metrics, heldout=None, evaluation=ev, input_flags=[], protocol_calibrated=True)
        self.assertEqual(gated["automatic_verdict"], "reject")
        free = mevaluate.decide_status(candidate_valid=True, metrics=metrics, heldout=None, evaluation=ev, input_flags=[], protocol_calibrated=True,
                                       gate_overrides={"lens_outline_mean_mm": {"mode": "report_only", "reason": "clipped"}})
        self.assertEqual(free["automatic_verdict"], "accept")
        self.assertFalse(free["provisional"]["lens_outline_mean_mm"]["gate"])
        self.assertEqual(free["provisional"]["lens_outline_mean_mm"]["override"]["reason"], "clipped")


@pytest.mark.slow   # ~17 s
@unittest.skipIf(not INPUTS.is_dir(), "Tom Ford photos not present")
class StageOffline(unittest.TestCase):
    def test_scripted_stage_relabels_rescales_and_patches_the_real_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            job.mkdir()
            req = {"product_id": "tomford_test", "notes": "Tom Ford FT1123-D 26E 49",
                   "photos": [{"path": str((INPUTS / "front.jpg").resolve()), "view": "front"}, {"path": str((INPUTS / "back.webp").resolve()), "view": "back"},
                              {"path": str((INPUTS / "left.jpg").resolve()), "view": "left"}, {"path": str((INPUTS / "angled.jpg").resolve()), "view": "angled", "held_out": True},
                              {"path": str((INPUTS / "rear_angled.webp").resolve()), "view": "unknown"}],
                   "dimensions": {}, "limits": {}, "author": {"driver": "scripted"}, "held_out_views": ["angled"]}
            (job / "request.json").write_text(json.dumps(req), encoding="utf-8")
            request = Request.from_dict(req)
            from modeler.intake import run_intake
            width, prov = request.front_width_mm()
            evidence = run_intake(request, job, front_width_mm=width, width_provenance=prov)
            # the matte cannot see the crystal rim (the reference failure read it 'rimless'): the code's class is 'unknown'
            self.assertEqual(evidence["front"]["rim_class"], "unknown")
            self.assertEqual(evidence["front"]["rim_guard"]["measured_rim_class"], "rimless")
            self.assertIn("rim_invisible_to_matte", evidence["front"]["flags"])
            self.assertTrue(all(l["rim_width_mm"] is None for l in evidence["front"]["lenses"]))
            height_before = evidence["front"]["front_height_mm"]
            d = ia.ScriptedIntake({"reading": READING, "review": REVIEW})
            evidence, record = ia.run_intake_stage(request, job, evidence, d, d, log=lambda *a: None)
            self.assertEqual(record["reading"]["status"], "complete"); self.assertEqual(record["review"]["status"], "complete")
            self.assertTrue(record.get("remeasured"))
            f = evidence["front"]
            # the reviewed class after the vision stage; the matte's rimless flags go, the guard's flag stays as history
            self.assertEqual(f["rim_class"], "full"); self.assertEqual(f["thickness_mm"][0]["brow_mm"], 5.0)
            self.assertEqual(f["code_measured"]["rim_class"], "unknown")
            self.assertTrue(all(l["rim_class"] == "full" for l in f["lenses"]))
            self.assertIn("rim_invisible_to_matte", f["flags"])
            self.assertFalse({"rimless_review", "rimless_low_confidence"} & set(f["flags"]))
            for l in f["lenses"]:
                self.assertEqual(l["rim_width_mm"], [5.0] * len(l["outline_mm"]), "rebuilt from the outline after the guard nulled it")
            from modeler import author as mauthor
            self.assertEqual({l["rim_width_mm_min_max"] == [5.0, 5.0] for l in mauthor.compact_evidence(evidence)["front"]["lenses"]}, {True})
            self.assertEqual(evidence["scale"]["source"], "size_marking"); self.assertGreater(evidence["scale"]["front_width_mm"], 140.0)
            self.assertIsNotNone(f.get("height_band")); self.assertLess(f["front_height_mm"], height_before, "the temple tips no longer count as front height")
            self.assertEqual(evidence["gate_overrides"]["lens_outline_mean_mm"]["mode"], "report_only")
            self.assertEqual(f.get("silhouette_source"), "constructed", "the fragmentary crystal silhouette is replaced by a constructed one")
            xs = [p[0] for p in f["silhouette_mm"]]
            self.assertLess(abs(min(xs) + max(xs)), 5.0, "the constructed silhouette spans both halves")
            self.assertEqual(evidence["views"]["front"].get("fit_mask"), "lens band (folded temple tips cut)")
            saved = json.loads((job / "evidence" / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["intake_reading"]["product"]["frame_material"], "crystal")
            self.assertEqual(saved["intake_stage"]["stage"], ia.STAGE)
            import numpy as np
            with np.load(job / "evidence" / "masks.npz") as masks:      # closed before the temp folder is removed (Windows)
                self.assertLess(int(masks["fg_front"].sum()), int(masks["fg_front_full"].sum()), "the fit mask lost the temple tips")
            # a preview on a job with candidates writes beside the evidence, never over it
            (job / "candidates" / "c0000").mkdir(parents=True)
            before = (job / "evidence" / "evidence.json").read_bytes()
            ia.run_intake_stage(request, job, json.loads(before), d, d, log=lambda *a: None, write=False)
            self.assertEqual((job / "evidence" / "evidence.json").read_bytes(), before)
            self.assertTrue((job / "evidence" / "intake_astra" / "evidence.preview.json").exists())


if __name__ == "__main__":
    unittest.main()
