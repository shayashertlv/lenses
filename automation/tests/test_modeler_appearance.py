"""modeler.appearance: every metal and crystal material measured against the author photos at matched runtime poses.

Unit tests on synthetic data and on the numbers the instrument measured on the real assets (2026-09-28, the local AR
harness): test-pilot-002 r0006, which the owner says reads clearer crystal, heavier hinge blocks and yellower gold than
the photos, and the owner-accepted vb-astra1 c0004 and miu-astra2 c0002 gold, which must not flag. The evaluator and
author wiring (evaluation.py sheet lists and task text, author RULES) are checked here too.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from modeler import appearance as ap

# a short root for the harness output (Windows path limit): LENSES_TEST_TMP when set, else the system temp folder;
# created in setUp, never at import
LAG = Path(os.environ.get("LENSES_TEST_TMP") or tempfile.gettempdir()) / "lag-appearance"

# the fitted cameras of test-pilot-002 r0006's author views (observation.json)
R0006_CAMERAS = {"front": {"yaw": 0.7122674991018114, "pitch": 16.89876642736875, "roll": 0.5966955654747943},
                 "left": {"yaw": 86.7747877818181, "pitch": 21.369302059274197, "roll": -2.193265356860154},
                 "back": {"yaw": 186.00999396695008, "pitch": 0.758634659984526, "roll": 0.15721177434175604}}
R0006_CENTRE_CM = [0.0, -1.0, -0.25]


def srgb_linear(rgb):
    return [float(x) for x in ap.srgb_to_linear(np.asarray(rgb, float))]


class HarnessPoses(unittest.TestCase):
    def test_the_camera_model_reproduces_the_recorded_harness_cameras(self):
        # r0006's report.json: camera_origin_in_asset (m) at pose 0 and at yaw 35
        np.testing.assert_allclose(ap.harness_camera_cm(0, 0, 0), [0.0, -3.3, 16.5], atol=0.05)
        np.testing.assert_allclose(ap.harness_camera_cm(35, 0, 0), [-13.2, -3.3, 12.3], atol=0.05)

    def test_the_asset_back_inspection_turns_about_the_face_pivot(self):
        # r0006's recorded back camera_origin_in_asset: (0, -0.033, -0.295) m
        d = ap.asset_back_direction([0.0, 0.0, 0.0])
        np.testing.assert_allclose(d, np.array([0.0, -3.271, -29.53]) / np.linalg.norm([0.0, -3.271, -29.53]), atol=1e-3)

    def test_r0006_views_are_matched_within_a_degree_or_by_the_back_inspection(self):
        front = ap.harness_pose(R0006_CAMERAS["front"], R0006_CENTRE_CM)
        left = ap.harness_pose(R0006_CAMERAS["left"], R0006_CENTRE_CM)
        back = ap.harness_pose(R0006_CAMERAS["back"], R0006_CENTRE_CM)
        self.assertLess(front["direction_error_deg"], 0.5)
        self.assertLess(left["direction_error_deg"], 1.5)
        self.assertEqual(back["view"], {"type": "asset-back"})
        self.assertLess(back["direction_error_deg"], ap.MAX_DIRECTION_ERROR_DEG)
        # the side is reached by rolling the face (the harness pitches about the face's own ear axis)
        self.assertLess(left["view"]["yaw_degrees"], -60)
        for pose in (front, left):
            for key, limit in ap.POSE_LIMITS.items():
                self.assertLessEqual(abs(pose["view"][key]), limit)
        # the pose's direction really is the photo's
        d, _ = ap.harness_axes(*(left["view"][k] for k in ("yaw_degrees", "pitch_degrees", "roll_degrees")), R0006_CENTRE_CM)
        t, _ = ap.photo_axes(R0006_CAMERAS["left"])
        self.assertLess(math.degrees(math.acos(float(np.clip(d @ t, -1, 1)))), 1.5)

    def test_a_rear_three_quarter_view_is_out_of_the_harness_reach(self):
        # the harness turns a face: from behind it only has the asset-back inspection, 55 degrees off a camera at yaw 125
        self.assertGreater(ap.harness_pose({"yaw": 125.0, "pitch": 0.0, "roll": 0.0}, R0006_CENTRE_CM)["direction_error_deg"], ap.MAX_DIRECTION_ERROR_DEG)


class PlanViews(unittest.TestCase):
    def setUp(self):
        LAG.mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(dir=LAG))
        self.addCleanup(shutil.rmtree, self.d, True)

    def evidence(self, views):
        rows = []
        for vid, _ in views:
            p = self.d / f"{vid}.png"
            Image.new("RGB", (40, 30), (255, 255, 255)).save(p)
            rows.append({"id": vid, "view": vid, "path": str(p), "held_out": False})
        return {"inputs": rows, "views": {vid: {"view": vid, "backdrop_rgb": [255, 255, 255]} for vid, _ in views}}

    def test_front_then_one_side_then_the_back_and_never_a_view_without_a_camera(self):
        views = {"back": {"view": "back", "camera": R0006_CAMERAS["back"]}, "left": {"view": "left", "camera": R0006_CAMERAS["left"]},
                 "right": {"view": "right", "camera": dict(R0006_CAMERAS["left"], yaw=-86.0)}, "front": {"view": "front", "camera": R0006_CAMERAS["front"]},
                 "rear_angled": {"view": "rear_angled"}}
        ev = self.evidence([(k, v) for k, v in views.items() if k != "rear_angled"])
        planned, skipped = ap.plan_views(views, ev, R0006_CENTRE_CM)
        self.assertEqual([p["vid"] for p in planned], ["front", "left", "back"])
        self.assertLessEqual(len(planned), ap.MAX_VIEWS)
        self.assertIn("right", skipped)
        self.assertIn("rear_angled", skipped)
        self.assertEqual(len({p["harness_id"] for p in planned}), len(planned))

    def test_a_held_out_photo_is_never_planned(self):
        views = {"front": {"view": "front", "camera": R0006_CAMERAS["front"]}}
        ev = self.evidence([("front", None)])
        ev["inputs"][0]["held_out"] = True
        planned, skipped = ap.plan_views(views, ev, R0006_CENTRE_CM)
        self.assertEqual(planned, [])
        self.assertIn("front", skipped)


class Regions(unittest.TestCase):
    def test_regions_follow_the_geometry(self):
        # a lens at |x| < 50, an endpiece block at x 60..66 near the front, a temple to z -150, its last 28% the tip
        tris, parts = [], []

        def box(x0, x1, z0, z1, part):
            V = np.array([[x0, 0, z0], [x1, 0, z0], [x1, 1, z1]], float)
            tris.append(V)
            parts.append(part)
        box(10, 50, -1, 0, "lens_R")
        box(-60, -10, -1, 0, "frame")
        box(55, 66, -8, -3, "frame")
        box(60, 66, -60, -40, "temple_R")
        box(60, 66, -150, -130, "temple_R")
        box(-66, -60, -60, -40, "temple_L")
        V = np.concatenate(tris)
        F = np.arange(len(V)).reshape(-1, 3)
        region, side = ap.face_regions(V, F, np.array(parts, dtype=object))
        self.assertEqual(list(region), ["", "", "endpiece", "temple", "tip", "temple"])
        self.assertEqual(list(side), [1, -1, 1, 1, 1, -1])


class MetalSelection(unittest.TestCase):
    GOLD = (215, 186, 150)          # the r0006 photo's rose-champagne gold
    CRYSTAL = (238, 236, 229)       # a crystal's own faint warm tint
    NAVY = (30, 44, 70)

    def scene(self, context):
        img = np.full((60, 60, 3), 255.0)
        window = np.zeros((60, 60), bool)
        window[20:40, 20:40] = True
        ring = np.zeros((60, 60), bool)
        ring[10:50, 10:50] = True
        ring &= ~window
        img[ring] = context
        img[window] = context
        img[25:35, 25:35] = self.GOLD
        return img, window, ring

    def test_the_metal_is_what_the_ring_does_not_have(self):
        for context in (self.CRYSTAL, self.NAVY):
            img, window, ring = self.scene(context)
            sel = ap.metal_pixels(ap.rgb_to_hsv(img), window, ring)
            self.assertEqual(int(sel.sum()), 100, context)
            self.assertTrue(sel[25:35, 25:35].all())

    def test_a_colour_as_common_in_the_ring_is_not_metal(self):
        img, window, ring = self.scene(self.CRYSTAL)
        img[ring] = self.GOLD                      # the whole neighbourhood is that colour: nothing stands out
        img[window] = self.GOLD
        self.assertEqual(int(ap.metal_pixels(ap.rgb_to_hsv(img), window, ring).sum()), 0)

    def test_hue_helpers(self):
        self.assertAlmostEqual(ap.circular_median(np.array([0.98, 0.99, 0.01, 0.02, 0.0])), 0.0, places=3)
        self.assertAlmostEqual(ap.hue_difference(0.09, 0.117), -0.027, places=6)
        self.assertAlmostEqual(ap.hue_difference(0.99, 0.01), -0.02, places=6)
        hsv = ap.rgb_to_hsv(np.array([[218.0, 192.0, 132.0]]))[0]
        self.assertAlmostEqual(hsv[0], 0.1163, places=3)
        self.assertAlmostEqual(hsv[1], 0.3945, places=3)


def metal_view(hue, sat, rgb=(200, 180, 150), px=500):
    return {"status": "measured", "hue": hue, "saturation": sat, "value": 0.8, "rgb": list(rgb), "pixels": px}


GOLD_R0006 = {"kind": "metal", "base_color_linear": srgb_linear((218, 192, 132))}
GOLD_MIU = {"kind": "metal", "base_color_linear": srgb_linear((211, 181, 108))}
GOLD_VB = {"kind": "metal", "base_color_linear": srgb_linear((222, 194, 121))}
NICKEL = {"kind": "metal", "base_color_linear": srgb_linear((185, 184, 169))}


class MetalColour(unittest.TestCase):
    """Measured 2026-09-28 (photo vs runtime at the matched poses): r0006 front 0.0904 / 0.1167, left 0.0904 / 0.1212, back
    0.0915 / 0.1162; miu front 0.1078 / 0.1193, left 0.1081 / 0.1209; vb left 0.1155 / 0.1235."""

    def test_r0006_gold_is_off_toward_rose_and_gets_a_rosier_base(self):
        rows = [("front", metal_view(0.0904, 0.2229), metal_view(0.1167, 0.401)), ("left", metal_view(0.0904, 0.2399), metal_view(0.1212, 0.3646)),
                ("back", metal_view(0.0915, 0.40), metal_view(0.1162, 0.1858))]
        r = ap.compare_metal("Pale champagne gold", GOLD_R0006, rows)
        self.assertEqual(r["status"], "measured")
        self.assertEqual(r["flags"], ["metal_hue_off"])
        self.assertAlmostEqual(r["deltas"]["hue"], -0.0263, places=3)
        self.assertLess(r["recommended"]["hue"], 0.095)
        rec = r["recommended"]["base_color_srgb"]
        self.assertEqual(rec[0], 218)                       # value kept (brightness is not compared)
        self.assertLess(rec[1], 192)                        # toward rose
        self.assertEqual(r["base_color_srgb"], [218, 192, 132])

    def test_the_accepted_golds_do_not_flag(self):
        miu = ap.compare_metal("Polished pale gold", GOLD_MIU, [("front", metal_view(0.1078, 0.315), metal_view(0.1193, 0.514)),
                                                               ("left", metal_view(0.1081, 0.3824), metal_view(0.1209, 0.5269))])
        vb = ap.compare_metal("Warm pale gold", GOLD_VB, [("left", metal_view(0.1155, 0.2318), metal_view(0.1235, 0.4969))])
        for r in (miu, vb):
            self.assertEqual(r["status"], "measured")
            self.assertEqual(r["flags"], [])
            self.assertNotIn("recommended", r)
            self.assertTrue(ap.METAL_SATURATION_RANGE[0] <= r["deltas"]["saturation_ratio"] <= ap.METAL_SATURATION_RANGE[1])

    def test_the_hue_tolerance_sits_between_the_accepted_and_r0006(self):
        self.assertGreater(ap.METAL_HUE_TOLERANCE, 0.0128 * 1.3)          # miu left, the largest accepted
        self.assertLess(ap.METAL_HUE_TOLERANCE, 0.0247 / 1.2)             # r0006 back, the smallest r0006

    def test_a_neutral_metal_is_reported_never_flagged(self):
        r = ap.compare_metal("Nickel silver", NICKEL, [("front", metal_view(0.05, 0.3), metal_view(0.15, 0.09))])
        self.assertEqual(r["status"], "neutral")
        self.assertEqual(r["flags"], [])

    def test_no_measured_view_says_why(self):
        r = ap.compare_metal("Gold", GOLD_VB, [("front", {"status": "hidden"}, {"status": "hidden"})])
        self.assertEqual(r["status"], "unmeasured")
        self.assertIn("front: photo hidden", r["reason"])


CRYSTAL = {"kind": "translucent", "base_color_linear": [0.982, 0.982, 0.965],
           "translucent": {"thickness_mm": 5.5, "attenuation_rgb_linear": [0.9822505503331171, 0.9822505503331171, 0.9646862478944651],
                           "attenuation_distance_mm": 4.0}}


def zone(mean, median, p10, p90, medlin, share, pixels=None):
    d = {"visible_share": share, "mean_delta": mean, "median_delta": median, "p10_delta": p10, "p90_delta": p90, "pixels": 1000,
         "median_linear": medlin, "zone": "rim_band"}
    if pixels is not None:
        d["_pixels"] = pixels
    return d


class Crystal(unittest.TestCase):
    """r0006 front, measured: photo mean 47.3 / median 31 / p10 8 / p90 111, linear median (0.807, 0.791, 0.745); runtime mean
    4.9 / median 4 / p10 3 / p90 10, linear median (0.991, 0.991, 0.965). The r0006 crystal variants through the harness (same
    pose, white): roughness 0.5 and ior 1.9 read mean 4.2 / 3.9 (no gain), a 4x attenuation or thickness 21.0, tint 0.7 49.0
    with p90 - p10 still 8, transmission 0.5 0.0 (the lit white base on white)."""

    def runtime_pixels(self):
        rng = np.random.default_rng(1)
        base = np.array([253.0, 253.0, 250.0])
        return np.clip(base[None] - rng.uniform(0, 7, (2000, 1)), 0, 255)

    def test_r0006_is_too_clear_and_the_runtime_cannot_reach_the_photo(self):
        r = ap.compare_crystal("Colourless polished crystal", CRYSTAL,
                               [("front", zone(47.3, 31, 8, 111, [0.807, 0.791, 0.745], 0.89), zone(4.9, 4, 3, 10, [0.991, 0.991, 0.965], 0.13, self.runtime_pixels()),
                                 True, [255, 255, 255])])
        self.assertEqual(r["status"], "measured")
        self.assertEqual(r["flags"], ["crystal_too_clear", "crystal_clarity_runtime_limited"])
        self.assertLess(r["deltas"]["visibility_ratio"], 0.15)
        tint = r["recommended"]["tint_srgb"]
        self.assertTrue(all(200 < c < 250 for c in tint), tint)            # a light warm grey, not a grey acetate
        self.assertGreaterEqual(tint[0], tint[2])
        self.assertIn("roughness", r["note"])
        self.assertEqual(r["knobs"], ap.CRYSTAL_KNOBS)

    def test_the_tint_matches_the_crystal_body_not_its_edges(self):
        # r0006 front: mean 47.3 but median 31 (spread 103): the mean is the edge and refraction structure. The same body with
        # no edges (mean = median = 31) must get the same tint, and the recommendation names the body as its target.
        rt = zone(4.9, 4, 3, 10, [0.991, 0.991, 0.965], 0.13, self.runtime_pixels())
        edged = ap.compare_crystal("C", CRYSTAL, [("front", zone(47.3, 31, 8, 111, [0.807, 0.791, 0.745], 0.89), rt, True, [255, 255, 255])])
        body = ap.compare_crystal("C", CRYSTAL, [("front", zone(31, 31, 27, 35, [0.807, 0.791, 0.745], 0.89), dict(rt), True, [255, 255, 255])])
        self.assertEqual(edged["recommended"]["tint_srgb"], body["recommended"]["tint_srgb"])
        rec = edged["recommended"]
        self.assertEqual(rec["target"], "body_median")
        self.assertEqual(rec["target_median_delta"], 31)
        self.assertLess(abs(rec["predicted_median_delta"] - 31), 8)
        self.assertIn("crystal_clarity_runtime_limited", edged["flags"])
        for word in ("BODY", "median", "do not chase", "edge"):
            self.assertIn(word, rec["note"])
        self.assertNotIn("crystal_clarity_runtime_limited", body["flags"])

    def test_a_uniformly_tinted_photo_is_reachable_by_the_tint(self):
        # a translucent acetate: the photo's rim is evenly darker, no refraction lines (spread small)
        px = self.runtime_pixels()
        medlin = [float(x) for x in np.median(ap.srgb_to_linear(px), 0)]
        r = ap.compare_crystal("Smoke", CRYSTAL, [("front", zone(40, 40, 36, 44, [0.68, 0.68, 0.68], 1.0),
                                                   zone(4.9, 4, 3, 10, medlin, 0.13, px), True, [255, 255, 255])])
        self.assertIn("crystal_too_clear", r["flags"])
        self.assertNotIn("crystal_clarity_runtime_limited", r["flags"])
        self.assertLess(abs(r["recommended"]["predicted_mean_delta"] - 40), 12)

    def test_a_matching_crystal_does_not_flag_and_a_foreign_backdrop_is_not_compared(self):
        same = zone(20, 18, 10, 30, [0.9, 0.9, 0.9], 0.9)
        r = ap.compare_crystal("C", CRYSTAL, [("front", same, dict(same, _pixels=self.runtime_pixels()), True, [255, 255, 255])])
        self.assertEqual(r["flags"], [])
        self.assertNotIn("recommended", r)
        r = ap.compare_crystal("C", CRYSTAL, [("back", same, same, False, [255, 255, 255])])
        self.assertEqual(r["status"], "unmeasured")
        self.assertIn("back", r["reason"])

    def test_the_knob_table_follows_the_twin_shader(self):
        src = (Path(__file__).resolve().parents[2] / "ar" / "src" / "render" / "translucent-twin.ts")
        if src.is_file():
            text = src.read_text(encoding="utf-8")
            self.assertIn("twinAttenuation", text)                          # tint ^ (thickness / distance): works
            self.assertIn("Refraction offsets and roughness blur are not reproduced", text)
        for knob in ("roughness", "ior", "coat"):
            self.assertIn("no effect", ap.CRYSTAL_KNOBS[knob])
        for knob in ("tint_srgb", "thickness_mm", "transmission"):
            self.assertIn("works", ap.CRYSTAL_KNOBS[knob])


def side(region_px, metal_px, footprint_px=0, embedded_px=0):
    return {"region_px": region_px, "metal_px": metal_px, "share": round(metal_px / max(region_px, 1), 4), "footprint_px": footprint_px,
            "embedded_px": embedded_px}


def regions(**by_region):
    """{region: {'-1': side(...), '+1': side(...)}} -> the analyse_image regions record (aggregate + per side)."""
    out = {}
    for rname in ap.REGIONS:
        sides = by_region.get(rname) or {}
        agg = {k: sum(v[k] for v in sides.values()) for k in ("region_px", "metal_px", "footprint_px", "embedded_px")}
        agg["share"] = round(agg["metal_px"] / max(agg["region_px"], 1), 4)
        out[rname] = dict(agg, sides=dict(sides))
    return out


# test-pilot-002 r0006 at MATCHED resolution (the photo box-filtered to the runtime's px/mm; 2026-09-28, the local AR harness):
# per view and side (region px, selected metal px, the model's area-metal footprint px, of it seen through the crystal)
R0006_AREAS = [
    ("front", "front", regions(endpiece={"-1": side(1286, 197, 243, 169), "+1": side(1301, 182, 243, 170)}),
     regions(endpiece={"-1": side(1063, 236, 245, 193), "+1": side(1072, 239, 242, 188)}), 2.41),
    ("left", "left", regions(endpiece={"+1": side(878, 129, 204, 183)}, temple={"+1": side(3116, 356, 550, 508)}, tip={"+1": side(1577, 81, 217, 158)}),
     regions(endpiece={"+1": side(1472, 350, 368, 323)}, temple={"+1": side(3683, 642, 722, 649)}, tip={"+1": side(0, 0)}), 2.04),
    ("back", "back", regions(endpiece={"-1": side(188, 2, 7, 0), "+1": side(302, 28, 44, 8)}),
     regions(endpiece={"-1": side(339, 31, 50, 2), "+1": side(333, 33, 50, 2)}), 1.34),
]


class HardwareArea(unittest.TestCase):
    """Hardware size: the metal's share of each region, photo vs runtime, per view and side at MATCHED resolution, only where
    the metal is exposed (its first surface) and both images show the same hardware. Metal seen through a crystal is not
    compared: the photo's crystal refracts and magnifies it outside the model's footprint (r0006's temple core wire, a pale
    band about 2 mm wide in the photo, read 1.99 'heavy' at full resolution although a fixed warm-gold rule reads 0.96)."""

    def test_r0006_embedded_temple_wire_is_never_heavy(self):
        a = ap.compare_regions(R0006_AREAS)
        t = a["temple"]
        self.assertIsNone(t["flag"])
        self.assertEqual(t["status"], "embedded")
        self.assertNotIn("ratio", t)
        self.assertIn("crystal", t["reason"])
        self.assertIn("left:+1", t["reason"])
        self.assertNotIn("hardware_heavy", json.dumps(a["temple"]))

    def test_r0006_endpiece_survives_only_where_the_metal_is_exposed_and_reads_matching(self):
        a = ap.compare_regions(R0006_AREAS)
        e = a["endpiece"]
        # the front and the side see the T and the hinge through the crystal (70-90 %): not compared; the back sees the
        # hinge blocks directly, but one of them falls outside the photo camera fit's labels (footprint 7 vs 50 px)
        self.assertEqual(set(e["views"]), {"back:+1"})
        self.assertAlmostEqual(e["ratio"], 1.07, places=2)
        self.assertIsNone(e["flag"])
        self.assertIn("front:-1", e["not_compared"])
        self.assertIn("seen through", e["not_compared"]["front:-1"])
        self.assertIn("show different amounts", e["not_compared"]["back:-1"])
        self.assertEqual(a["tip"]["status"], "unmeasured")
        self.assertIn("does not draw", a["tip"]["reason"])

    def test_before_the_rule_the_same_numbers_read_heavy(self):
        # the full-resolution, all-metal shares the previous instrument compared (endpiece 1.45 / 1.90 / 1.65, temple 1.99)
        # are the ones the embedded views carry: dropping the embedded rule would flag them again
        emb = [(v, vw, p, r, ppm) for v, vw, p, r, ppm in R0006_AREAS if v != "back"]
        old = ap.EMBEDDED_SHARE
        try:
            ap.EMBEDDED_SHARE = 1.01
            a = ap.compare_regions(emb)
        finally:
            ap.EMBEDDED_SHARE = old
        self.assertEqual(a["endpiece"]["flag"], "hardware_heavy")
        self.assertEqual(a["temple"]["flag"], "hardware_heavy")

    def test_an_exposed_block_drawn_heavier_still_flags(self):
        heavy = [("back", "back", regions(endpiece={"+1": side(300, 30, 44, 0)}), regions(endpiece={"+1": side(300, 60, 46, 0)}), 1.34)]
        a = ap.compare_regions(heavy)
        self.assertEqual(a["endpiece"]["flag"], "hardware_heavy")
        self.assertAlmostEqual(a["endpiece"]["ratio"], 2.0, places=2)
        light = [("left", "left", regions(temple={"+1": side(3000, 400, 500, 0)}), regions(temple={"+1": side(3000, 100, 520, 0)}), 2.04)]
        self.assertEqual(ap.compare_regions(light)["temple"]["flag"], "hardware_light")

    def test_the_accepted_assets_do_not_flag(self):
        # matched resolution: miu front endpieces are 88-91 runtime px a side at 2.44 px/mm (about 15 mm2: too small), its
        # left endpiece 217 / 363 px and temple 1382 / 1622 px at 2.04; vb's temple 9513 / 15243 px at 1.86
        miu = ap.compare_regions([
            ("front", "front", regions(endpiece={"-1": side(146, 77, 146), "+1": side(144, 67, 144)}),
             regions(endpiece={"-1": side(88, 86, 88), "+1": side(91, 89, 91)}), 2.44),
            ("left", "left", regions(endpiece={"+1": side(217, 228, 217)}, temple={"+1": side(1382, 1367, 1029)}),
             regions(endpiece={"+1": side(363, 361, 363)}, temple={"+1": side(1622, 1362, 1401)}), 2.04)])
        vb = ap.compare_regions([("left", "left", regions(temple={"+1": side(9513, 207, 196)}), regions(temple={"+1": side(15243, 343, 355)}), 1.86)])
        self.assertEqual(set(miu["endpiece"]["views"]), {"left:+1"}, "a region under MIN_REGION_MM2 in the runtime is not compared")
        self.assertAlmostEqual(miu["endpiece"]["ratio"], 0.95, places=2)
        self.assertAlmostEqual(miu["temple"]["ratio"], 0.85, places=2)
        self.assertAlmostEqual(vb["temple"]["ratio"], 1.03, places=2)
        for a in (miu, vb):
            self.assertTrue(all(v.get("flag") is None for v in a.values()), a)

    def test_the_thresholds_sit_between_the_measurements(self):
        self.assertGreater(ap.AREA_RATIO_HIGH, 1.07)                     # r0006's exposed hinge, and the accepted assets (<= 1.03)
        self.assertLess(ap.AREA_RATIO_LOW, 0.85)                         # miu's temple
        self.assertGreater(ap.FOOTPRINT_AGREEMENT, 1.81)                 # vb's temple, the largest accepted disagreement
        self.assertLess(ap.FOOTPRINT_AGREEMENT, 7.1)                     # r0006's back hinge the photo's camera fit cuts off
        self.assertGreater(ap.EMBEDDED_SHARE, 0.18)                      # r0006's back hinge: exposed
        self.assertLess(ap.EMBEDDED_SHARE, 0.70)                         # r0006's front endpiece: the T inside the crystal
        self.assertGreater(ap.MIN_REGION_MM2, 88 / 2.44 ** 2)             # miu's front endpiece side, too small to compare
        self.assertLess(ap.MIN_REGION_MM2, 217 / 2.04 ** 2)               # miu's left endpiece, compared


class MatchedResolution(unittest.TestCase):
    def test_the_photo_is_box_filtered_and_its_labels_sampled_at_pixel_centres(self):
        img = np.zeros((70, 70, 3))
        img[:, 35:] = 200.0
        fid = np.arange(70 * 70).reshape(70, 70)
        mask = np.zeros((70, 70), bool)
        mask[10:20, 10:20] = True
        small, labels = ap.match_resolution(img, {"fid": fid, "mask": mask}, 0.2)
        self.assertEqual(small.shape, (14, 14, 3))
        self.assertEqual(labels["fid"].shape, (14, 14))
        self.assertEqual(int(labels["fid"][0, 0]), 2 * 70 + 2)          # the centre of the first 5x5 block
        self.assertEqual(int(labels["mask"].sum()), 4)                   # a 10x10 block is 2x2 at a fifth
        self.assertAlmostEqual(float(small[0, 6, 0]), 0.0)
        self.assertAlmostEqual(float(small[0, 7, 0]), 200.0)

    def test_a_coarser_photo_is_never_upsampled(self):
        img = np.zeros((10, 10, 3))
        small, labels = ap.match_resolution(img, {"fid": np.zeros((10, 10), int)}, 1.4)
        self.assertEqual(small.shape, (10, 10, 3))


class AnalyseImage(unittest.TestCase):
    def test_a_metal_block_inside_crystal_is_measured_and_counted_in_its_region(self):
        H = W = 80
        fid = np.full((H, W), -1)
        fid[20:60, 10:70] = 0            # crystal face (endpiece region)
        fid[30:50, 30:50] = 1            # lens face
        kind = np.array(["translucent", "lens", "metal"], dtype=object)
        material = np.array(["Crystal", "Lens", "Gold"], dtype=object)
        part = np.array(["frame", "lens_R", "frame"], dtype=object)
        region = np.array(["endpiece", "", "endpiece"], dtype=object)
        side = np.array([1, 1, 1])
        fp = np.zeros((H, W), bool)
        fp[22:30, 12:22] = True          # the block behind the crystal
        img = np.full((H, W, 3), 255.0)
        img[20:60, 10:70] = (240, 238, 232)
        img[30:50, 30:50] = (190, 170, 150)
        img[22:30, 12:22] = (215, 186, 150)
        out = ap.analyse_image(img, fid, kind=kind, material=material, region=region, side=side, part=part, metal_footprints={"Gold": fp},
                               lens_footprint=None, ppm=2.0, tol_px=0, backdrop=[255, 255, 255], regions_side=None, source="render")
        g = out["metal"]["Gold"]
        self.assertEqual(g["status"], "measured")
        self.assertEqual(g["pixels"], 80)
        self.assertAlmostEqual(g["hue"], float(ap.rgb_to_hsv(np.array([[215.0, 186.0, 150.0]]))[0, 0]), places=3)
        self.assertEqual(out["regions"]["endpiece"]["metal_px"], 80)
        self.assertEqual(out["regions"]["endpiece"]["region_px"], 40 * 60 - 400)
        # the block is the model's area metal (footprint), all of it seen through the crystal (embedded), on side +1
        self.assertEqual(out["regions"]["endpiece"]["footprint_px"], 80)
        self.assertEqual(out["regions"]["endpiece"]["embedded_px"], 80)
        self.assertEqual(out["regions"]["endpiece"]["sides"]["+1"]["embedded_px"], 80)
        self.assertEqual(out["regions"]["endpiece"]["sides"]["-1"]["region_px"], 0)
        c = out["crystal"]["Crystal"]
        self.assertAlmostEqual(c["mean_delta"], 23.0, places=5)
        self.assertEqual(c["zone"], "rim_band")

    def test_neutral_metals_do_not_count_in_the_areas(self):
        H = W = 40
        fid = np.full((H, W), 0)
        img = np.full((H, W, 3), 255.0)
        img[10:20, 10:20] = (215, 186, 150)
        fp = np.zeros((H, W), bool)
        fp[10:20, 10:20] = True
        kw = dict(kind=np.array(["metal"], dtype=object), material=np.array(["Gold"], dtype=object), region=np.array(["temple"], dtype=object),
                  side=np.array([1]), part=np.array(["temple_R"], dtype=object), metal_footprints={"Gold": fp}, lens_footprint=None, ppm=2.0,
                  tol_px=0, backdrop=[255, 255, 255], regions_side=None, source="render")
        self.assertEqual(ap.analyse_image(img, fid, **kw)["regions"]["temple"]["metal_px"], 100)
        self.assertEqual(ap.analyse_image(img, fid, **kw)["regions"]["temple"]["footprint_px"], 100)
        self.assertEqual(ap.analyse_image(img, fid, **kw)["regions"]["temple"]["embedded_px"], 0, "the metal is its own first surface: exposed")
        none = ap.analyse_image(img, fid, area_metals=set(), **kw)["regions"]["temple"]
        self.assertEqual((none["metal_px"], none["footprint_px"]), (0, 0))


class SummaryInterface(unittest.TestCase):
    def test_the_shared_interface(self):
        record = {"status": "measured", "reliable": True, "reason": None, "flags": ["metal_hue_off:Gold", "hardware_heavy:endpiece"],
                  "views": [{"vid": "front"}],
                  "materials": {"Gold": {"role": "metal", "status": "measured", "photo": {"hue": 0.09, "saturation": 0.24, "rgb": [1, 2, 3], "pixels": 9},
                                         "render": {"hue": 0.117, "saturation": 0.36, "rgb": [1, 2, 3], "pixels": 9},
                                         "deltas": {"hue": -0.026, "saturation_ratio": 0.66, "per_view": {"front": {}}},
                                         "recommended": {"base_color_srgb": [218, 178, 132]}, "flags": ["metal_hue_off"], "views": ["front"], "note": "x"},
                                "Lens": {"role": "lens", "status": "see summary.lens_colour", "flags": []}},
                  "hardware_area": {"endpiece": {"photo_share": 0.12, "render_share": 0.22, "ratio": 1.65, "flag": "hardware_heavy", "views": {"front": 1.4}}}}
        s = ap.summary_of(record)
        self.assertEqual(set(s) >= {"materials", "hardware_area", "flags", "reliable", "reason"}, True)
        g = s["materials"]["Gold"]
        self.assertEqual(g["role"], "metal")
        self.assertEqual(g["deltas"], {"hue": -0.026, "saturation_ratio": 0.66})
        self.assertEqual(g["recommended"], {"base_color_srgb": [218, 178, 132]})
        self.assertNotIn("pixels", g["photo"])
        self.assertEqual(s["hardware_area"]["endpiece"], {"photo_share": 0.12, "render_share": 0.22, "ratio": 1.65, "flag": "hardware_heavy"})
        self.assertEqual(s["flags"], record["flags"])
        self.assertEqual(s["views"], ["front"])
        json.dumps(s)

    def neutral_record(self):
        # rayban-astra1 c0005 (owner-accepted), the local AR harness 2026-09-28: the nickel's selected pixels differ in hue by
        # -0.0805 and in saturation 2.24x, numbers without meaning on a colourless metal
        return {"status": "measured", "reliable": True, "reason": None, "flags": [],
                "materials": {"Nickel silver": {"role": "metal", "status": "neutral", "photo": {"hue": 0.07, "saturation": 0.19, "rgb": [1, 2, 3]},
                                                "render": {"hue": 0.15, "saturation": 0.085, "rgb": [1, 2, 3]},
                                                "deltas": {"hue": -0.0805, "saturation_ratio": 2.243, "per_view": {}}, "flags": [],
                                                "note": "a neutral metal (authored saturation 0.09): no hue to compare"},
                              "Dark steel": {"role": "metal", "status": "unmeasured", "flags": [], "reason": "no view shows this metal"},
                              "Tortoise": {"role": "acetate", "status": "not_measured", "flags": []}},
                "hardware_area": {}}

    def test_a_neutral_or_unmeasured_metal_carries_no_deltas_and_says_why(self):
        s = ap.summary_of(self.neutral_record())
        n = s["materials"]["Nickel silver"]
        self.assertNotIn("deltas", n)
        self.assertEqual(n["status"], "neutral")
        self.assertEqual(n["flags"], ["neutral_metal"])
        self.assertIn("no hue", n["reason"])
        u = s["materials"]["Dark steel"]
        self.assertEqual(u["flags"], ["unmeasured"])
        self.assertNotIn("deltas", u)
        self.assertEqual(s["flags"], [], "status flags are not problems: the summary's flag list stays empty")

    def test_the_build_reply_digest_is_self_explanatory_for_a_neutral_metal(self):
        from modeler.agentic.tools import appearance_digest
        d = appearance_digest(ap.summary_of(self.neutral_record()))
        self.assertEqual(d["materials"]["Nickel silver"], {"role": "metal", "flags": ["neutral_metal"]})
        self.assertNotIn("hue", json.dumps(d))
        self.assertEqual(d["flags"], [])

    def test_private_arrays_never_reach_the_record(self):
        self.assertEqual(ap._strip_private({"a": {"_pixels": np.zeros(3), "masks": {}, "b": 1}, "c": [{"_x": 1, "y": 2}]}),
                         {"a": {"b": 1}, "c": [{"y": 2}]})


class OneHarnessRun(unittest.TestCase):
    """The measurement's cost: ONE harness run per build (about 17-19 s on the calibration assets, plus 2-5 s of analysis),
    at most MAX_VIEWS views, over the front photo's own backdrop; no run at all without a metal or crystal material."""

    def setUp(self):
        LAG.mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(dir=LAG))
        self.addCleanup(shutil.rmtree, self.d, True)

    def call(self, materials, calls):
        objects = {"front": {"V": np.zeros((3, 3)), "F": np.array([[0, 1, 2]]), "part": "frame", "materials": list(materials)[:1], "M": np.zeros(1, int)}}
        photos = []
        for vid, colour in (("front", (241, 241, 241)), ("left", (255, 255, 255))):
            p = self.d / f"{vid}.png"
            Image.new("RGB", (40, 30), colour).save(p)
            photos.append({"id": vid, "view": vid, "path": str(p), "held_out": False})
        evidence = {"inputs": photos, "views": {"front": {"view": "front", "backdrop_rgb": [241, 241, 241]}, "left": {"view": "left"}}}
        views = {"front": {"view": "front", "camera": R0006_CAMERAS["front"], "px_per_mm": 7.0}, "left": {"view": "left", "camera": R0006_CAMERAS["left"], "px_per_mm": 7.0}}

        class StubModel:
            def __init__(self, *a, **k):
                pass

            def centre_cm(self):
                return np.array(R0006_CENTRE_CM)

        def runner(models, out, **kw):
            calls.append(kw)
            return {"validation": {"ok": False, "reasons": ["stub"]}}
        orig = ap.RenderModel
        ap.RenderModel = StubModel
        try:
            return ap.appearance_metric(self.d, self.d / "model.glb", views=views, frame=None, V=objects["front"]["V"], F=objects["front"]["F"],
                                        part=np.array(["frame"], dtype=object), objects=objects, materials=materials, evidence=evidence, width_mm=140.0,
                                        runner=runner)
        finally:
            ap.RenderModel = orig

    def test_one_run_over_the_front_photos_backdrop(self):
        calls = []
        r = self.call({"Gold": GOLD_R0006}, calls)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["background"], "solid")
        self.assertEqual(calls[0]["background_color"], "#f1f1f1")
        self.assertEqual([v["id"] for v in calls[0]["ar_views"]], ["app_front", "app_left"])
        self.assertLessEqual(len(calls[0]["ar_views"]), ap.MAX_VIEWS)
        self.assertEqual(r["status"], "harness_failed")
        self.assertFalse(r["reliable"])
        self.assertIn("stub", r["reason"])

    def test_no_metal_or_crystal_costs_no_run(self):
        calls = []
        r = self.call({"Black": {"kind": "acetate", "base_color_linear": [0.02, 0.02, 0.02]}}, calls)
        self.assertEqual(calls, [])
        self.assertEqual(r["status"], "not_applicable")


class AuthorRules(unittest.TestCase):
    def rule(self, needle):
        from modeler import author
        hits = [r for r in author.RULES if needle in r]
        self.assertEqual(len(hits), 1, needle)
        return hits[0]

    def test_the_appearance_rule_says_how_to_read_the_sheet_and_the_numbers(self):
        r = self.rule("summary.appearance")
        for word in ("material_match", "Match every material", "recommended.base_color_srgb", "metal_hue_off", "brightness", "visibility_ratio",
                     "recommended.tint_srgb", "crystal_clarity_runtime_limited", "roughness", "hardware_area", "hardware_heavy", "reliable"):
            self.assertIn(word, r)

    def test_the_appearance_rule_names_the_new_statuses_and_the_body_target(self):
        r = self.rule("summary.appearance")
        for word in ("neutral_metal", "embedded", "median", "body", "edge", "not compared"):
            self.assertIn(word, r)
        self.assertNotIn("often a block the photo shows dim through the crystal", r, "embedded metal is no longer compared")

    def test_planar_front_is_defined_so_an_accepted_near_flat_front_is_not_rebuilt(self):
        r = self.rule("planar_front")
        self.assertTrue(r.startswith("Export audit"))
        for word in ("400 mm", "owner accepted", "top photo", "slab", "summary.lens_reflection", "may not edit"):
            self.assertIn(word, r)
        self.assertLess(len(r), 1400)

    def test_the_lens_rule_points_at_the_lens_backdrop_sheet(self):
        self.assertIn("lens_backdrop sheet", self.rule("Lens appearance comes from the photographs"))


class EvaluatorInputs(unittest.TestCase):
    def test_the_critic_and_the_final_evaluator_are_told_about_the_sheet(self):
        from modeler.agentic import evaluation
        self.assertIn("material_match", evaluation.CRITIC_TASK)
        self.assertIn("material_match", evaluation.FINAL_TASK)
        self.assertIn("appearance", evaluation.MEASUREMENT_GLOSSARY)
        self.assertEqual(list(evaluation.FINAL_SHEET_LABELS)[:2], ["photo_match", "lens_backdrop"])
        self.assertIn("material_match", evaluation.FINAL_SHEET_LABELS)
        self.assertIn("runtime", evaluation.FINAL_SHEET_LABELS["material_match"])


try:
    from test_agentic_evaluation import RUN2_CALIBRATION, _JobBase, texts
except Exception:  # noqa: BLE001 - the sibling fixtures are optional here
    _JobBase = None

if _JobBase is not None:
    from modeler import evaluate as mevaluate
    from modeler.agentic.artifacts import AUTHOR_VISIBLE
    from modeler.agentic.evaluation import critic_blocks, final_blocks

    class MaterialMatchInTheMessages(_JobBase):
        PROTOCOL = {"identity_checklist": ["round panto front"], "checklist_source": "request_notes", "visual_bar_calibrated": False,
                    "calibration_summary": RUN2_CALIBRATION, "gate_thresholds_mm": mevaluate.GATE_THRESHOLDS_MM, "input_flags": []}
        EVIDENCE = {"notes": "fixture notes", "views": {"front": {"view": "front", "flags": []}}}

        def test_the_final_evaluator_gets_the_material_match_sheet_after_the_lens_colour(self):
            store = self.make_store()
            arts = ArtifactStore(store)
            self.reserve(store, arts)
            summary = {"appearance": {"flags": ["metal_hue_off:Gold"], "reliable": True}}
            rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256="ab" * 32, summary=summary, views={})
            backdrop = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="lens_backdrop")
            mm = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="material_match")
            blocks, bindings = final_blocks(store, arts, self.EVIDENCE, rev, self.PROTOCOL, revision_dir=self.rev_dir(store, rev))
            text = "\n".join(texts(blocks))
            self.assertIn(mm["id"], text)
            self.assertLess(text.index(backdrop["id"]), text.index(mm["id"]))
            self.assertIn(mm["id"], {i["id"] for i in bindings["images"]})
            self.assertIn("metal_hue_off:Gold", texts(blocks)[0])

        def test_the_critic_gets_it_too(self):
            store = self.make_store()
            arts = ArtifactStore(store)
            self.reserve(store, arts)
            rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256="ab" * 32, summary={}, views={})
            mm = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="material_match")
            self.assertIn(mm["id"], "\n".join(texts(critic_blocks(store, arts, self.EVIDENCE, rev, "materials?"))))

    from modeler.agentic.artifacts import ArtifactStore  # noqa: E402 - used by the class above


if __name__ == "__main__":
    unittest.main()
