"""The rim guard of the code intake: a rim the contrast matte cannot see is 'unknown', never 'rimless'.

test-pilot-001 read a clear crystal Tom Ford on a white background as 'rimless' from an empty contrast matte (every rim
width 0.0) although 85 % of the outline points are frame-typed and the front raised rimless_review + rimless_low_confidence.
The class then became the manifest tag rim_rimless and drove calibration coverage. The guard lives in ``modeler.intake``
(bsa/front.py is untouched): rimless + (low-confidence flag or frame-typed fraction >= 0.5) -> 'unknown' with the flag
``rim_invisible_to_matte``; the per-lens rim fields are nulled with a note. A true rimless read stays 'rimless'.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import numpy as np

from modeler import intake, tags

PILOT = Path(__file__).resolve().parents[1] / "data/modeler/agentic/test-pilot-001"   # not cwd-relative
PILOT_EVIDENCE = PILOT / "evidence" / "evidence.json"
PILOT_FRONT = PILOT / "inputs" / "front.jpeg"


def _lens(side: str, rim_class: str, frame: float, rimless: float, widths: list[float], median: float) -> dict:
    free = round(1.0 - frame - rimless, 4)
    return {"side": side, "outline_mm": [[0, 0], [1, 0], [1, 1]], "outline_px": [[0, 0], [1, 0], [1, 1]],
            "edge_type": ["frame_bounded"] * len(widths), "rim_width_mm": list(widths),
            "box_mm": {"width_A": 46.0, "height_B": 38.0, "x_range": [8.0, 54.0], "y_range": [-19.0, 19.0]},
            "rim_class": rim_class, "rim_w_median_mm": median,
            "type_fractions": {"frame": frame, "free": free, "rimless": rimless}}


def _front(rim_class: str, flags: list[str], lenses: list[dict], pooled_frame: float) -> dict:
    return {"layout": "pair", "rim_class": rim_class, "flags": list(flags), "lenses": lenses,
            "thickness_mm": [{"side": "R", "brow_mm": 1.7, "bottom_rim_mm": 2.9, "endpiece_mm": 4.2},
                             {"side": "L", "brow_mm": 1.7, "bottom_rim_mm": 2.9, "endpiece_mm": 4.2}],
            "refinement": {"median_bevel_offset_mm": None, "low_contrast_share": 0.1,
                           "point_type_fractions": {"frame": pooled_frame, "free": round(1.0 - pooled_frame, 4), "rimless": 0.0},
                           "offset_mm": None}}


def synthetic_rimless() -> dict:
    """A true rimless read: rimless-typed points, the ridge supported the class, no low-confidence flag."""
    lenses = [_lens("R", "rimless", 0.05, 0.85, [0.0, 0.0, 0.0, 0.0], 0.0), _lens("L", "rimless", 0.05, 0.85, [0.0, 0.0, 0.0, 0.0], 0.0)]
    f = _front("rimless", ["rimless_review"], lenses, 0.05)
    for l in f["lenses"]:
        f["refinement"]["point_type_fractions"] = {"frame": 0.05, "free": 0.10, "rimless": 0.85}
    return f


def synthetic_invisible_rim(flags=("low_contrast_high", "rimless_review", "rimless_low_confidence"), frame=0.85) -> dict:
    """The pilot's shape: the matte was empty, so every rim width is 0 and the class fell to 'rimless'."""
    lenses = [_lens("R", "rimless", frame, 0.0, [0.0, 0.12, 0.0, 0.3], 0.601), _lens("L", "rimless", frame, 0.0, [0.0, 0.12, 0.0, 0.3], 0.601)]
    return _front("rimless", list(flags), lenses, frame)


class Guard(unittest.TestCase):
    def assert_guarded(self, front: dict) -> None:
        self.assertEqual(front["rim_class"], "unknown")
        self.assertIn("rim_invisible_to_matte", front["flags"])
        self.assertEqual(front["flags"].count("rim_invisible_to_matte"), 1)
        for l in front["lenses"]:
            self.assertEqual(l["rim_class"], "unknown")
            self.assertIsNone(l["rim_width_mm"])
            self.assertIsNone(l["rim_w_median_mm"])
            self.assertIn("matte", l["rim_note"])
        self.assertIn("rim_guard", front)
        self.assertEqual(front["rim_guard"]["measured_rim_class"], "rimless")

    def test_real_pilot_evidence_reads_unknown_with_the_flag(self):
        if not PILOT_EVIDENCE.is_file():
            self.skipTest(f"{PILOT_EVIDENCE} is not present")
        ev = json.loads(PILOT_EVIDENCE.read_text(encoding="utf-8"))
        front = ev["front"]
        # the recorded state of the paid run: the defect this guard exists for
        self.assertEqual(front["rim_class"], "rimless")
        self.assertIn("rimless_low_confidence", front["flags"])
        self.assertGreaterEqual(min(l["type_fractions"]["frame"] for l in front["lenses"]), 0.5)
        self.assertEqual(tags.kind_tags(ev), ["pair", "rim_rimless"])
        before = copy.deepcopy(front)
        out = intake.guard_rim_class(front)
        self.assertIs(out, front)
        self.assert_guarded(front)
        self.assertEqual(front["rim_guard"]["reasons"], ["rimless_low_confidence", "frame_typed_points>=0.5"])
        self.assertEqual(front["rim_guard"]["frame_fraction"], 0.8456)
        self.assertEqual(tags.kind_tags(ev), ["pair"])
        # only the rim fields change: the outlines, boxes, types, thickness and every other measurement are untouched
        for l0, l1 in zip(before["lenses"], front["lenses"]):
            for k in ("outline_mm", "outline_px", "outline_mm_64", "edge_type", "box_mm", "type_fractions", "side"):
                self.assertEqual(l0[k], l1[k], k)
        for k in before:
            if k not in ("rim_class", "flags", "lenses"):
                self.assertEqual(before[k], front[k], k)
        self.assertEqual(sorted(set(front) - set(before)), ["rim_guard"])

    def test_low_confidence_flag_alone_guards(self):
        front = synthetic_invisible_rim(flags=("rimless_review", "rimless_low_confidence"), frame=0.2)
        intake.guard_rim_class(front)
        self.assert_guarded(front)
        self.assertEqual(front["rim_guard"]["reasons"], ["rimless_low_confidence"])

    def test_frame_typed_majority_alone_guards(self):
        front = synthetic_invisible_rim(flags=("rimless_review",), frame=0.5)
        intake.guard_rim_class(front)
        self.assert_guarded(front)
        self.assertEqual(front["rim_guard"]["reasons"], ["frame_typed_points>=0.5"])
        # the pooled refinement fraction stands in when the lenses carry no type fractions
        front = synthetic_invisible_rim(flags=("rimless_review",), frame=0.7)
        for l in front["lenses"]:
            del l["type_fractions"]
        intake.guard_rim_class(front)
        self.assertEqual(front["rim_class"], "unknown")
        self.assertEqual(front["rim_guard"]["frame_fraction"], 0.7)

    def test_a_true_rimless_frame_still_reads_rimless(self):
        front = synthetic_rimless()
        before = copy.deepcopy(front)
        intake.guard_rim_class(front)
        self.assertEqual(front, before)
        self.assertEqual(front["rim_class"], "rimless")
        self.assertNotIn("rim_invisible_to_matte", front["flags"])
        self.assertEqual(tags.kind_tags({"front": front}), ["pair", "rim_rimless"])
        # below the half-way mark of frame-typed points the class stands too
        front = synthetic_invisible_rim(flags=("rimless_review",), frame=0.49)
        intake.guard_rim_class(front)
        self.assertEqual(front["rim_class"], "rimless")
        self.assertEqual(front["lenses"][0]["rim_width_mm"], [0.0, 0.12, 0.0, 0.3])

    def test_other_classes_are_left_alone(self):
        for rc in ("full", "half", "mixed"):
            front = synthetic_invisible_rim()
            front["rim_class"] = rc
            for l in front["lenses"]:
                l["rim_class"] = rc
            before = copy.deepcopy(front)
            intake.guard_rim_class(front)
            self.assertEqual(front, before, rc)
        self.assertIsNone(intake.guard_rim_class(None))
        self.assertEqual(intake.guard_rim_class({}), {})

    def test_idempotent(self):
        front = synthetic_invisible_rim()
        intake.guard_rim_class(front)
        once = copy.deepcopy(front)
        intake.guard_rim_class(front)
        self.assertEqual(front, once)


class MeasureFrontAppliesTheGuard(unittest.TestCase):
    def test_pilot_front_photo_measures_unknown(self):
        """The real front photo of the paid run through ``measure_front`` (no network, no Blender): the guard is applied
        where the measurement is adopted, so the evidence never carries the empty-matte 'rimless'."""
        if not PILOT_FRONT.is_file():
            self.skipTest(f"{PILOT_FRONT} is not present")
        from PIL import Image
        from bsa import intake as bintake
        rgb = np.asarray(Image.open(PILOT_FRONT).convert("RGB"))
        fg, lens, info = bintake.intake_view(rgb, "front", front_width_mm=140.0)
        pure = info.pop("_matte")
        info.pop("_openings", None)
        front = intake.measure_front(rgb, fg, lens, pure, info, 140.0)
        self.assertEqual(front["rim_guard"]["measured_rim_class"], "rimless")
        self.assertEqual(front["rim_class"], "unknown")
        self.assertIn("rim_invisible_to_matte", front["flags"])
        self.assertTrue(all(l["rim_class"] == "unknown" and l["rim_width_mm"] is None for l in front["lenses"]))
        self.assertEqual(tags.kind_tags({"front": front}), ["pair"])


class TagsOfUnknown(unittest.TestCase):
    def test_unknown_rim_class_yields_no_rim_tag(self):
        self.assertEqual(tags.kind_tags({"front": {"layout": "pair", "rim_class": "unknown"}}), ["pair"])
        self.assertEqual(tags.asset_tags({"front": {"layout": "single", "rim_class": "unknown"}}, {"materials": {"c": {"kind": "translucent"}}}),
                         ["single", "translucent"])
        # the vision reading's class still wins over an unknown measurement, and an unknown reading falls back to it
        self.assertEqual(tags.kind_tags({"front": {"layout": "pair", "rim_class": "unknown"}, "intake_reading": {"product": {"rim_class": "full"}}}),
                         ["pair", "rim_full"])
        self.assertEqual(tags.kind_tags({"front": {"layout": "pair", "rim_class": "unknown"}, "intake_reading": {"product": {"rim_class": "unknown"}}}),
                         ["pair"])
        self.assertNotIn("rim_unknown", tags.TAG_ORDER)


def guarded_evidence(n: int = 40) -> dict:
    """Author-ready evidence around a guarded front: real closed outlines (``n`` points each), rim widths nulled."""
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    front = synthetic_invisible_rim()
    for l, cx in zip(front["lenses"], (33.0, -33.0)):
        l["outline_mm"] = np.round(np.c_[cx + 23.0 * np.cos(t), 19.0 * np.sin(t)], 3).tolist()
        l["outline_px"] = l["outline_mm"]
        l["edge_type"] = ["frame_bounded"] * n
        l["rim_width_mm"] = [0.0] * n
    front |= {"front_width_mm": 140.0, "front_height_mm": 46.0, "bridge_dbl_mm": 20.0, "lens_share": 0.6}
    intake.guard_rim_class(front)
    return {"product_id": "crystal_x", "notes": "listing", "scale": {"front_width_mm": 140.0, "source": "assumed_default"},
            "dimensions_stated": {}, "conventions": {"units": "mm"}, "front": front, "sides": {},
            "views": {"front": {"id": "front", "view": "front", "size": [1500, 1000], "flags": []}},
            "held_out": {"angled": {"id": "angled", "view": "angled"}}}


CRYSTAL_READING = {
    "photos": [{"id": "front", "view": "front", "straight_on": True, "folded_temples_visible_above_front": False, "floor_reflection_present": False, "note": ""}],
    "product": {"layout": "pair", "rim_class": "full", "frame_material": "crystal", "frame_colour": "clear crystal", "rim_thickness_mm": 5.0,
                "front_shape": "round panto", "bridge": "saddle", "endpieces": "compact", "temples": "crystal", "hardware": "gold inlay",
                "branding": "print on the right lens", "lens_finish": "solid", "lens_colour": "brown", "mirror_colour": None, "lens_print_location": None},
    "size_marking": {"lens_mm": None, "bridge_mm": None, "temple_mm": None, "source": "none", "confidence": 0.0},
    "symmetric_product": True, "photos_consistent": True,
    "identity_features": ["round panto crystal front", "gold inlay at the hinges", "brown lenses"],
    "product_description": "Round crystal acetate sunglasses.", "author_cautions": ["the crystal rim is invisible on the white backdrop"],
}


class GuardedEvidenceDownstream(unittest.TestCase):
    """The guard nulls the rim widths; the author's evidence (``author.compact_evidence`` / ``build_request``: the legacy
    route at ``job.py`` and the agentic route through ``tools.compact_evidence_safe``) and the intake stage's see-through
    patch carry that state instead of raising (TypeError on None, ValueError on [] until 2026-09-28)."""

    def assert_author_ready(self, ev: dict, expect_min_max) -> dict:
        from modeler import author
        from modeler.agentic import tools
        c = author.compact_evidence(ev)
        for l, l0 in zip(c["front"]["lenses"], ev["front"]["lenses"]):
            self.assertEqual(l["rim_width_mm_min_max"], expect_min_max)
            self.assertEqual(l.get("rim_note"), l0.get("rim_note"))
            self.assertEqual(len(l["outline_mm_64"]), 64)
        safe = tools.compact_evidence_safe(ev)
        self.assertNotIn("note", safe, "the agentic route must not fall back to the raw front")
        self.assertEqual(safe, c)
        req = author.build_request(job_meta={"job": "j", "product_id": ev["product_id"], "limits": {}, "protocol": "p"},
                                   evidence=ev, state={}, images=[])
        self.assertEqual(req["evidence"], c)
        json.dumps(req)
        return c

    def test_guarded_rim_widths_none_and_empty(self):
        ev = guarded_evidence()
        self.assertIsNone(ev["front"]["lenses"][0]["rim_width_mm"])
        c = self.assert_author_ready(ev, None)
        self.assertEqual(c["front"]["rim_class"], "unknown")
        self.assertIn("rim_invisible_to_matte", c["front"]["flags"])
        self.assertIn("matte", c["front"]["lenses"][0]["rim_note"])
        self.assertIsNone(c["front"]["lenses"][0]["rim_w_median_mm"])
        self.assertEqual(c["front"]["rim_guard"]["measured_rim_class"], "rimless")
        for l in ev["front"]["lenses"]:
            l["rim_width_mm"] = []
        self.assert_author_ready(ev, None)

    def test_see_through_reading_rebuilds_the_widths_from_the_outline(self):
        from modeler import intake_astra as ia
        for start in (None, []):
            ev = guarded_evidence(n=40)
            for l in ev["front"]["lenses"]:
                l["rim_width_mm"] = copy.deepcopy(start)
            ia.apply_reading(ev, ia.validate_reading(copy.deepcopy(CRYSTAL_READING)), listing_text="listing")
            f = ev["front"]
            self.assertEqual(f["rim_class"], "full")
            for l in f["lenses"]:
                self.assertEqual(l["rim_width_mm"], [5.0] * 40, "one width per outline point, as the code measures them")
                self.assertEqual(l["rim_w_median_mm"], 5.0)
                self.assertEqual(f["code_measured"][f"lens_{l['side']}_rim_width_mm"], start)
                self.assertIn("vision", l["rim_note"])
            self.assert_author_ready(ev, [5.0, 5.0])
        # a measured list keeps its own length
        ev = guarded_evidence(n=40)
        ev["front"]["lenses"][0]["rim_width_mm"] = [0.1, 0.2, 0.3]
        ia.apply_reading(ev, ia.validate_reading(copy.deepcopy(CRYSTAL_READING)), listing_text="")
        self.assertEqual(ev["front"]["lenses"][0]["rim_width_mm"], [5.0] * 3)

    def test_an_opaque_reading_leaves_the_guarded_front_author_ready(self):
        from modeler import intake_astra as ia
        ev = guarded_evidence()
        opaque = copy.deepcopy(CRYSTAL_READING)
        opaque["product"]["frame_material"] = "opaque_acetate"
        ia.apply_reading(ev, ia.validate_reading(opaque), listing_text="")
        self.assertEqual(ev["front"]["rim_class"], "unknown")
        self.assertIsNone(ev["front"]["lenses"][0]["rim_width_mm"])
        self.assert_author_ready(ev, None)

    def test_real_pilot_evidence_before_and_after_the_see_through_patch(self):
        if not PILOT_EVIDENCE.is_file():
            self.skipTest(f"{PILOT_EVIDENCE} is not present")
        from modeler import intake_astra as ia
        ev = json.loads(PILOT_EVIDENCE.read_text(encoding="utf-8"))
        intake.guard_rim_class(ev["front"])
        c = self.assert_author_ready(ev, None)
        self.assertLess(len(json.dumps(c)), 40_000, "the compact evidence, not the raw 544-point front")
        ia.apply_reading(ev, ia.validate_reading(copy.deepcopy(CRYSTAL_READING)), listing_text=ev.get("notes") or "")
        for l in ev["front"]["lenses"]:
            self.assertEqual(len(l["rim_width_mm"]), len(l["outline_mm"]))
        self.assertNotIn("rimless_low_confidence", ev["front"]["flags"])
        self.assertIn("rim_invisible_to_matte", ev["front"]["flags"])
        self.assert_author_ready(ev, [5.0, 5.0])
        self.assertEqual(tags.kind_tags(ev), ["pair", "rim_full"])


class VisionClassClearsTheRimlessFlags(unittest.TestCase):
    """A class set by the vision reading or review contradicts the matte's rimless flags: they leave ``front.flags``
    (the code's list is kept in ``code_measured.flags``); the guard's rim_invisible_to_matte stays as history."""

    def test_reading_override_drops_the_rimless_flags(self):
        from modeler import intake_astra as ia
        ev = guarded_evidence()
        before = list(ev["front"]["flags"])
        prov = ia.apply_reading(ev, ia.validate_reading(copy.deepcopy(CRYSTAL_READING)), listing_text="")
        f = ev["front"]
        self.assertEqual(f["flags"], ["low_contrast_high", "rim_invisible_to_matte"])
        self.assertEqual(f["code_measured"]["flags"], before)
        row = next(p for p in prov if p["field"] == "front.rim_class")
        self.assertEqual(row["flags_dropped"], ["rimless_review", "rimless_low_confidence"])

    def test_review_override_drops_the_rimless_flags(self):
        from modeler import intake_astra as ia
        ev = guarded_evidence()
        opaque = copy.deepcopy(CRYSTAL_READING)
        opaque["product"]["frame_material"] = "opaque_acetate"
        ia.apply_reading(ev, ia.validate_reading(opaque), listing_text="")
        self.assertIn("rimless_review", ev["front"]["flags"], "no class set by the reading: the flags stand")
        review = {"rim_class_matches": False, "rim_widths_trustworthy": False, "thickness_trustworthy": True,
                  "silhouette_includes_temple_tips": False, "silhouette_complete": True,
                  "lenses": [{"side": s, "outline_trust": {a: "trusted" for a in ia.ARCS}, "shape_family": "round", "height_over_width": 0.8} for s in ("R", "L")],
                  "notes": ""}
        prov = ia.apply_review(ev, ia.validate_review(review))
        f = ev["front"]
        self.assertEqual(f["rim_class"], "full")
        self.assertEqual(f["flags"], ["low_contrast_high", "rim_invisible_to_matte"])
        self.assertIn("rimless_low_confidence", f["code_measured"]["flags"])
        self.assertEqual(next(p for p in prov if p["field"] == "front.rim_class")["flags_dropped"], ["rimless_review", "rimless_low_confidence"])

    def test_a_vision_rimless_class_keeps_the_rimless_flags(self):
        from modeler import intake_astra as ia
        ev = guarded_evidence()
        rimless = copy.deepcopy(CRYSTAL_READING)
        rimless["product"]["rim_class"] = "rimless"
        ia.apply_reading(ev, ia.validate_reading(rimless), listing_text="")
        self.assertEqual(ev["front"]["rim_class"], "rimless")
        self.assertIn("rimless_review", ev["front"]["flags"])
        self.assertIn("rimless_low_confidence", ev["front"]["flags"])

    def test_the_review_prompt_explains_an_unknown_class(self):
        from modeler import intake_astra as ia
        text = ia.review_instructions_text()
        self.assertIn("full / half / rimless / mixed / unknown", text)
        self.assertIn("crystal", text[text.index("'unknown'"):])


class TempleSeeThroughRule(unittest.TestCase):
    def test_the_rule_compares_temple_and_front_as_a_direction(self):
        from modeler import author
        rule = next(r for r in author.RULES if "summary.temple_see_through" in r)
        for phrase in ("appears only when the temples are translucent", "near temple's visible pixels", "35-degree angled view",
                       "same two fixtures", "as a direction", "not as the same number", "summary.frame_see_through",
                       "tint_srgb is the colour seen through 4 mm"):
            self.assertIn(phrase, rule)
        self.assertNotIn("thicker, not a different tint", rule)


if __name__ == "__main__":
    unittest.main()
