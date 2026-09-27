"""S10 gate: synthetic maths (alignment, masks, contour distances, refine bounds, lens edges, seams, faults,
phantom, decisions) plus real-data smoke tests that skip when the m1 artifacts are missing."""
from __future__ import annotations

import json
import math
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

from bsa import gate, raster
from bsa import cameras as C
from bsa.core import NormFrame, PRODUCTS, run_dir, project_mm


# ------------------------------------------------------------------------------------------ fixtures
def _parts(tuck_mm: float = 0.6):
    from bsa.export import synthetic_parts
    parts, materials = synthetic_parts(tuck_mm=tuck_mm)
    return parts, materials


def _model(parts, name="synthetic") -> gate.Model:
    Vs, Fs, Ls, off = [], [], [], 0
    for n, p in parts.items():
        lab = gate.LENS if n.startswith("lens") else gate.TEMPLE if n.startswith("temple") else gate.FRAME
        Vs.append(np.asarray(p["V"], float)); Fs.append(np.asarray(p["F"]) + off)
        Ls.append(np.full(len(p["F"]), lab, np.int8)); off += len(p["V"])
    return gate.Model(name, np.vstack(Vs), np.vstack(Fs), np.concatenate(Ls))


def _frame(V: np.ndarray) -> NormFrame:
    lo, hi = V.min(0), V.max(0)
    return NormFrame(tuple(((lo + hi) / 2).tolist()), float((hi - lo).max()))


def _fake_gen(model: gate.Model, front_depth: float = 20.0):
    fr = _frame(model.V)
    zf = float(model.V[:, 2].max())
    return types.SimpleNamespace(frame=fr, result={"front_z_mm": zf, "front_depth_mm": front_depth},
                                 Vd=model.V, Fd=model.F)


class TestAlignment(unittest.TestCase):
    def test_similarity_fit_exact(self):
        rng = np.random.default_rng(3)
        P = rng.normal(size=(200, 3)) * 30
        a = math.radians(2.0)
        R = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
        Q = 0.97 * P @ R.T + np.array([1.0, -2.0, 60.0])
        s, R1, t = gate.similarity_fit(P, Q, np.ones(len(P)), True)
        self.assertAlmostEqual(s, 0.97, places=9)
        np.testing.assert_allclose(R1, R, atol=1e-9)
        np.testing.assert_allclose(t, [1.0, -2.0, 60.0], atol=1e-8)
        Q2 = 1.03 * P + np.array([0.5, 0.0, -3.0])
        s2, R2, t2 = gate.similarity_fit(P, Q2, np.ones(len(P)), False)
        self.assertAlmostEqual(s2, 1.03, places=9)
        np.testing.assert_allclose(R2, np.eye(3))

    def test_align_to_generator_recovers_a_metre_candidate(self):
        """A copy of the 'generator' in metres, shrunk 3 % and shifted to a bridge origin, comes back to the
        model frame with the right scale and translation and ~0 residual."""
        parts, _ = _parts()
        m = _model(parts)
        gen = _fake_gen(m)
        cand_m = (m.V * 0.97 + np.array([0.4, 6.0, -65.0])) / 1000.0
        s, R, t, rep = gate.align_to_generator(cand_m * 1000.0, m.F, gen, iters=25)
        V = s * (cand_m * 1000.0) @ R.T + t
        self.assertLess(np.abs(V - m.V).max(), 0.05)
        self.assertAlmostEqual(s, 1 / 0.97, delta=5e-4)
        self.assertLess(rep["residual_candidate_to_generator_mm"]["median"], 0.02)
        self.assertLess(rep["rotation_deg"], 0.05)


class TestMasksAndContours(unittest.TestCase):
    def test_temple_region(self):
        front = np.zeros((60, 100), bool)
        front[20:40, 10:50] = True
        behind = np.zeros_like(front)
        behind[28:32, 30:95] = True               # a temple leaving the front piece to the right
        M = gate.temple_region(front, behind, 2.0)
        self.assertTrue(M[30, 70] and M[30, 94])   # the visible temple
        self.assertTrue(M[5, 90])                  # nearer to the temple than to the front piece
        self.assertFalse(M[30, 20] or M[25, 45])   # the front piece itself (hidden temple part is not masked)
        self.assertFalse(M[30, 2])                 # beyond the front on the far side
        self.assertFalse(gate.temple_region(front, np.zeros_like(front), 2.0).any())

    def test_contour_pair_shift(self):
        a = np.zeros((80, 120), bool)
        a[20:60, 20:100] = True
        b = np.zeros_like(a)
        b[20:60, 23:103] = True                   # shifted 3 px in x
        Ea, Eb = C._edge(a), C._edge(b)
        d = gate._contour_pair(Ea, Eb, C._dist_to(Ea), C._dist_to(Eb), np.ones_like(a))
        self.assertGreater(d["mean_px"], 0.5)
        self.assertLess(d["mean_px"], 3.0)
        same = gate._contour_pair(Ea, Ea, C._dist_to(Ea), C._dist_to(Ea), np.ones_like(a))
        self.assertEqual(same["mean_px"], 0.0)
        keep = np.zeros_like(a)
        keep[:, :60] = True                       # the left half only: the shifted left edge counts fully
        half = gate._contour_pair(Ea, Eb, C._dist_to(Ea), C._dist_to(Eb), keep)
        self.assertLess(half["n"], d["n"])
        mm = gate._to_mm(d, 2.0, 140.0)
        self.assertAlmostEqual(mm["mean_mm"], d["mean_px"] / 2.0)
        self.assertAlmostEqual(mm["pct_w"], 100 * mm["mean_mm"] / 140.0)

    def test_extrude_is_closed(self):
        import shapely
        from bsa.contract import topology
        poly = shapely.Polygon([(0, 0), (40, 0), (40, 20), (0, 20)], [[(5, 5), (15, 5), (15, 15), (5, 15)]])
        V, F = gate._extrude(poly, lambda P: np.column_stack([P, np.zeros(len(P))]), 3.0, np.array([0, 0, 1.0]))
        top = topology(V / 1000.0, F)
        self.assertEqual(top["boundary_edges"], 0)
        self.assertEqual(top["nonmanifold_edges"], 0)
        np.testing.assert_allclose([V[:, 2].min(), V[:, 2].max()], [-3.0, 0.0])


class TestRefine(unittest.TestCase):
    def setUp(self):
        parts, _ = _parts()
        self.m = _model(parts)
        self.frame = _frame(self.m.V)
        self.scene = raster.RasterScene(self.m.V, self.m.F, self.frame)
        self.shape = (500, 700)
        self.cam = raster.view_camera(self.frame, 30.0, 10.0, 3.2, self.shape, perspective=0.15)

    def _matte(self, cam):
        return self.scene.render(cam, self.shape, 1)["mask"]

    def test_small_perturbation_is_recovered_and_bounded(self):
        true = gate.Camera(self.cam.yaw + 1.0, self.cam.pitch - 0.5, self.cam.roll, self.cam.perspective,
                           self.cam.scale * 1.01, self.cam.center_x + 3.0, self.cam.center_y)
        fg = self._matte(true)
        lv = C.Level(fg, 256.0, "t")
        c, info = gate.refine_camera(self.scene, lv, self.cam, None, maxfev=150)
        self.assertLess(info["loss_refined"], info["loss_frozen"])
        self.assertLess(abs(c.yaw - true.yaw), 0.6)
        self.assertLess(abs(c.scale / true.scale - 1), 0.006)
        for k in ("d_yaw", "d_pitch", "d_roll"):
            self.assertLessEqual(abs(info[k]), 3.0 + 1e-9)

    def test_large_zoom_cannot_be_bought(self):
        """A 6 % size error is absorbed only up to the 2 % bound."""
        true = gate.Camera(*[getattr(self.cam, k) for k in ("yaw", "pitch", "roll", "perspective")],
                           self.cam.scale * 1.06, self.cam.center_x, self.cam.center_y)
        lv = C.Level(self._matte(true), 256.0, "t")
        c, info = gate.refine_camera(self.scene, lv, self.cam, None, maxfev=150)
        self.assertLessEqual(info["d_scale_pct"], 2.0 + 1e-6)
        self.assertGreater(info["d_scale_pct"], 1.5)
        self.assertTrue(info["at_bound"][3])

    def test_masked_loss_ignores_masked_pixels(self):
        fg = self._matte(self.cam)
        lv = C.Level(fg, 256.0, "t")
        keep = np.ones(lv.ref.shape, bool)
        self.assertAlmostEqual(gate.masked_loss(lv, self.scene, self.cam, keep), lv.loss(self.scene, self.cam), places=6)
        self.assertEqual(gate.masked_loss(lv, self.scene, self.cam, None), lv.loss(self.scene, self.cam))


class TestLensAndSeam(unittest.TestCase):
    def _setup(self, tuck):
        parts, _ = _parts(tuck)
        m = _model(parts)
        gen = _fake_gen(m)
        shape = (400, 1000)
        cam = raster.view_camera(gen.frame, 0.0, 0.0, 6.0, shape, center_mm=np.array([0.0, 0.0, 0.0]))
        return parts, m, gen, shape, cam

    def test_visible_lens_edge_is_the_frame_hole(self):
        """Lenses tucked 0.6 mm under the rim: the visible lens edge is the hole in the plate, measured to
        ~0.05 mm through the render; a 1 mm-grown reference reads ~1 mm with the model inside (signed < 0)."""
        from shapely.geometry import Point
        from shapely import affinity
        parts, m, gen, shape, cam = self._setup(0.6)
        sc = raster.RasterScene(m.V, m.F, gen.frame)
        vis = gate.visible_lens(m, sc, cam, shape, gen.frame)
        self.assertEqual(vis["components"], 2)
        zf = lambda x: -(x ** 2) / 400.0
        rings, grown = [], []
        for cx in (32.0, -32.0):
            h = affinity.scale(Point(cx, 2).buffer(1.0, 256), 25, 18)
            for poly, out in ((h, rings), (h.buffer(1.0, quad_segs=32), grown)):
                P = np.asarray(poly.exterior.coords)[:-1]
                out.append(project_mm(np.column_stack([P, zf(P[:, 0])]), cam, gen.frame))
        d = gate.outline_distance(vis, rings)
        self.assertLess(d["symmetric_mean_mm"], 0.06)
        g = gate.outline_distance(vis, grown)
        self.assertAlmostEqual(g["symmetric_mean_mm"], 1.0, delta=0.08)
        self.assertLess(g["reference_to_model"]["signed_mean_mm"], -0.9)
        # typed reference: only 'frame' samples count
        types_ = [np.array(["frame"] * (len(r) // 2) + ["free"] * (len(r) - len(r) // 2)) for r in rings]
        t = gate.outline_distance(vis, rings, types_, None, "frame")
        self.assertLess(t["reference_to_model"]["n"], d["reference_to_model"]["n"])

    def test_seam_gap(self):
        for tuck, expect_gap in ((0.6, False), (-0.5, True)):
            parts, m, gen, shape, cam = self._setup(tuck)
            zc = gate.slab_cut_z(m, gen)
            sc = gate._scenes(m, zc, gen.frame)
            s = gate.seam_metrics(m, sc, cam, shape, gen.frame, gen)
            self.assertTrue(s["has_lens"])
            if expect_gap:
                self.assertGreater(s["gap_pixels"], 100)
                self.assertGreater(s["gap_area_mm2"], 20.0)       # ~0.5 mm ring around two ~150 mm-perimeter lenses
            else:
                self.assertEqual(s["gap_pixels"], 0)

    def test_seam_photo_rule(self):
        """A lens 0.5 mm short of its hole leaves a ring gap. It counts where the photo shows material and is
        a legitimate hole where the photo shows backdrop there too."""
        from scipy import ndimage
        parts, m, gen, shape, cam = self._setup(-0.5)
        zc = gate.slab_cut_z(m, gen)
        sc = gate._scenes(m, zc, gen.frame)
        A = sc["full"].render(cam, shape, 1)["mask"]
        solid = gate.seam_metrics(m, sc, cam, shape, gen.frame, gen, ndimage.binary_fill_holes(A))
        self.assertGreater(solid["gap_pixels"], 100)
        self.assertTrue(solid["photo_rule"])
        see_through = gate.seam_metrics(m, sc, cam, shape, gen.frame, gen, A)
        self.assertLess(see_through["gap_pixels"], 0.05 * solid["gap_pixels"])
        self.assertGreater(see_through["legit_holes_px"], 0.9 * solid["gap_pixels"])
        # a 2 mm ring is not a sliver: reported as a wide hole over photo material, not as a seam gap
        parts, m2, gen2, shape, cam2 = self._setup(-2.0)
        sc2 = gate._scenes(m2, gate.slab_cut_z(m2, gen2), gen2.frame)
        A2 = sc2["full"].render(cam2, shape, 1)["mask"]
        wide = gate.seam_metrics(m2, sc2, cam2, shape, gen2.frame, gen2, ndimage.binary_fill_holes(A2))
        self.assertEqual(wide["gap_pixels"], 0)
        self.assertGreater(wide["wide_holes_touching_lens_px"], 100)

    def test_phantom(self):
        parts, m, gen, shape, cam = self._setup(0.6)
        self.assertEqual(gate.phantom_share(m, 140.0)["pct"], 0.0)
        from bsa.export import box_tube
        hV, hF = box_tube(0.0, 0.0, -40.0, -80.0, 20.0, 20.0)       # a 'head' block behind the bridge
        m2 = gate.Model("p", np.vstack([m.V, hV]), np.vstack([m.F, hF + len(m.V)]),
                        np.concatenate([m.labels, np.zeros(len(hF), np.int8)]))
        ph = gate.phantom_share(m2, 140.0)
        self.assertGreater(ph["pct"], 1.0)
        self.assertEqual(ph["faces"], len(hF))


class TestFaults(unittest.TestCase):
    def test_fault_geometry(self):
        parts, _ = _parts()
        m = _model(parts)
        gen = _fake_gen(m)
        shape = (400, 1000)
        cam = raster.view_camera(gen.frame, 0.0, 0.0, 6.0, shape, center_mm=np.zeros(3))
        f = gate.make_faults(m, gen, {"front": cam}, {"front": shape})
        zc = gate.slab_cut_z(m, gen)
        t = f["temples_x085"].V
        self.assertAlmostEqual((zc - t[:, 2].min()) / (zc - m.V[:, 2].min()), 0.85, places=9)
        np.testing.assert_array_equal(t[m.V[:, 2] >= zc], m.V[m.V[:, 2] >= zc])
        s = f["front_stretch_x105"].V
        front = m.V[:, 2] >= zc
        np.testing.assert_allclose(s[front, 0], 1.05 * m.V[front, 0])
        self.assertAlmostEqual(np.ptp(f["scale_x105"].V[:, 0]) / np.ptp(m.V[:, 0]), 1.05, places=9)
        ld = f["lens_dilate_1mm"]
        self.assertGreater(int((ld.labels == gate.LENS).sum()), int((m.labels == gate.LENS).sum()))
        # geometry: each lens part grows 1 mm at its outline (x and y extremes), nothing else moves
        lens_v = np.zeros(len(m.V), bool)
        lens_v[m.F[m.labels == gate.LENS].ravel()] = True
        np.testing.assert_array_equal(ld.V[~lens_v], m.V[~lens_v])
        for sx in (1, -1):
            sel = lens_v & (np.sign(m.V[:, 0]) == sx)
            self.assertAlmostEqual(np.ptp(ld.V[sel, 0]) - np.ptp(m.V[sel, 0]), 2.0, delta=0.1)
            self.assertAlmostEqual(np.ptp(ld.V[sel, 1]) - np.ptp(m.V[sel, 1]), 2.0, delta=0.1)
        np.testing.assert_array_equal(ld.V[:, 2], m.V[:, 2])


class TestGlbAndDecision(unittest.TestCase):
    def test_bsa_glb_round_trip(self):
        from bsa.export import write_glb
        parts, materials = _parts()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.glb"
            write_glb(parts, materials, p)
            m = gate.load_glb_model("bsa", p, None, "bsa")
        self.assertEqual(m.info["placement"]["method"], "bsa_origin_mm_model")
        allV = np.vstack([q["V"] for q in parts.values()])
        np.testing.assert_allclose(m.V.min(0), allV.min(0), atol=2e-3)
        np.testing.assert_allclose(m.V.max(0), allV.max(0), atol=2e-3)
        self.assertEqual(int((m.labels == gate.TEMPLE).sum()), sum(len(parts[k]["F"]) for k in ("temple_R", "temple_L")))
        self.assertGreater(int((m.labels == gate.LENS).sum()), 0)

    def test_as_model_labels(self):
        V = np.eye(3)
        F = np.array([[0, 1, 2]])
        self.assertEqual(gate.as_model("x", (V, F, ["lens"])).labels.tolist(), [gate.LENS])
        with self.assertRaises(ValueError):
            gate.as_model("x", (V, F, [0, 1]))

    def test_decide(self):
        ok = {k: {"pass": True} for k in ("c1", "c2", "c3", "c4")}
        self.assertEqual(gate.decide(ok, [], True)["decision"], "READY")
        # informational flags never force REVIEW (the 2D front/back distance is parallax, not an error)
        for f in gate.INFORMATIONAL_FLAGS + ("front_back_disagree",):
            d = gate.decide(ok, [f], True)
            self.assertEqual(d["decision"], "READY", f)
            self.assertIn(f"info:{f}", d["reasons"])
        bad = dict(ok, c3={"pass": False})
        d = gate.decide(bad, [], True)
        self.assertEqual(d["decision"], "RETRY")
        self.assertIn("failed:c3", d["reasons"])
        # unevaluated (a missing artifact) -> RETRY; not applicable (no truth to compare with) -> does not block READY
        self.assertEqual(gate.decide(dict(ok, c2={"pass": None}), [], True)["decision"], "RETRY")
        d = gate.decide(dict(ok, c2={"pass": None, "applicable": False, "reason": "rimless"}), [], True)
        self.assertEqual(d["decision"], "READY")
        self.assertIn("n/a:c2", d["reasons"])
        self.assertEqual(gate.decide(None, [], False)["decision"], "REVIEW")
        # every documented REVIEW rule forces REVIEW, even over a failed criterion
        for f in ("rimless_low_confidence", "low_contrast_high", "lens_count_disagree", "back_registration_failed",
                  "lens_components_3", "lens_views_inconsistent", "gate_validation_failed", "previous_alignment_residual_high"):
            d = gate.decide(bad, [f], True)
            self.assertEqual(d["decision"], "REVIEW", f)
            self.assertIn(gate.review_rule(f), d["review_rules"])
        self.assertTrue(all(gate.review_rule(f) is None for f in gate.INFORMATIONAL_FLAGS))

    def test_criteria_applicability(self):
        """c2 applicability comes from the GROUND TRUTH alone (``gt_frame_truth``); a measurement failure stays
        applicable and unevaluated, so ``decide`` retries instead of passing (review finding: a near-plane error or an
        invisible lens used to become 'not applicable' and the product could be READY)."""
        base = {"kind": "bsa", "views": {"angled": {"front_piece": {"pct_w": 0.3}}}, "seam": {"has_lens": True, "gap_pixels": 0},
                "phantom": {"pct": 0.0}}
        prev = {"views": {"angled": {"front_piece": {"pct_w": 0.3}}}}
        ok = {"c1_contract_ar_lenses": {"pass": True}, "c3_heldout_angled_front_piece": {"pass": True},
              "c4_zero_seam_gaps": {"pass": True}}
        rimless = {"points_px": [[0, 0], [10, 0], [10, 10], [0, 10]], "segment_types": ["rimless"] * 4, "photo": "front"}
        framed = dict(rimless, segment_types=["frame"] * 4)
        # the truth has no non-occluded frame-bounded segment on the criterion photo (a rimless lens): n/a
        truth = gate.gt_frame_truth({"lenses": [rimless]}, "front")
        self.assertIs(truth["applicable"], False)
        rec = dict(base, lens={"has_lens": True, "gt": {"criterion_photo": "front", "frame_symmetric_mean_mm": None}},
                   gt_frame_truth=truth)
        c2 = gate.criteria(rec, prev)["c2_lens_edge_vs_gt"]
        self.assertIsNone(c2["pass"])
        self.assertIs(c2["applicable"], False)
        # occluded frame segments do not count either
        occl = dict(framed, occluded=[True] * 4)
        self.assertIs(gate.gt_frame_truth({"lenses": [occl]}, "front")["applicable"], False)
        # the frame segment on ANOTHER photo does not make the criterion photo applicable
        self.assertIs(gate.gt_frame_truth({"lenses": [dict(framed, photo="back")]}, "front")["applicable"], False)
        # no ground truth at all
        self.assertIs(gate.gt_frame_truth(None, "front")["applicable"], False)
        # truth with frame samples but the measurement failed (near plane / lens not visible): applicable -> RETRY
        truth = gate.gt_frame_truth({"lenses": [framed]}, "front")
        self.assertIs(truth["applicable"], True)
        for lens in ({"has_lens": True, "error": "Geometry crosses the camera near plane"},
                     {"has_lens": True, "visible": False}):
            c2 = gate.criteria(dict(base, lens=lens, gt_frame_truth=truth), prev)["c2_lens_edge_vs_gt"]
            self.assertIsNone(c2["pass"])
            self.assertIs(c2["applicable"], True)
            d = gate.decide(dict(ok, c2_lens_edge_vs_gt=c2), [], True)
            self.assertEqual(d["decision"], "RETRY")
            self.assertIn("unevaluated:c2_lens_edge_vs_gt", d["reasons"])
        # no previous candidate: c3 not applicable
        c3 = gate.criteria(dict(base, lens={"has_lens": True}), None)["c3_heldout_angled_front_piece"]
        self.assertIs(c3["applicable"], False)

    def test_new_review_rules(self):
        """S7/S8 harness fallbacks, a missing lens-edge criterion and the integrity checks force REVIEW."""
        ok = {"c1": {"pass": True}, "c2": {"pass": True}, "c3": {"pass": True}, "c4": {"pass": True}}
        for f in ("ar_fit_failed", "mirror_calibration_failed", "no_lens_edge_criterion", "floating_part",
                  "rough_silhouette", "donor_projection_failed", "lens_colour_mismatch", "lens_colour_check_failed",
                  "lens_colour_unchecked"):
            self.assertEqual(gate.decide(ok, [f], True)["decision"], "REVIEW", f)
        self.assertEqual(set(gate.STAGE_REVIEW_FLAGS), {"s5_temples", "s7_texture", "s8_lens"})
        self.assertIn("donor_projection_failed", gate.STAGE_REVIEW_FLAGS["s5_temples"])
        self.assertIn("lens_colour_check_failed", gate.STAGE_REVIEW_FLAGS["s8_lens"])

    def test_c3_sensitivity_fields(self):
        mk = lambda a: {"kind": "bsa", "views": {"angled": {"front_piece": {"pct_w": a}}}, "seam": {"has_lens": True, "gap_pixels": 0},
                        "lens": {}, "phantom": {"pct": 0.0}}
        prev = {"views": {"angled": {"front_piece": {"pct_w": 0.30}}}}
        c3 = gate.criteria(mk(0.38), prev, sensitivity=-0.05)["c3_heldout_angled_front_piece"]
        self.assertTrue(c3["pass"])
        self.assertAlmostEqual(c3["margin_pct_w"], 0.02, places=6)
        self.assertEqual(c3["temple_sensitivity_pct_w"], 0.05)
        self.assertFalse(c3["clean"])                        # margin 0.02 < sensitivity 0.05: not a clean pass
        self.assertTrue(gate.criteria(mk(0.20), prev, sensitivity=0.05)["c3_heldout_angled_front_piece"]["clean"])
        self.assertNotIn("clean", gate.criteria(mk(0.20), prev)["c3_heldout_angled_front_piece"])

    def test_floating_parts(self):
        """A component clear of every other one by more than FLOAT_GAP_MM is flagged; a lens never is."""
        def box(c, s=3.0):
            V = np.array([[x, y, z] for x in (0, s) for y in (0, s) for z in (0, s)], float) + c
            F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                          [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
            return V, F
        (V1, F1), (V2, F2), (V3, F3) = box([0, 0, 0]), box([3.2, 0, 0]), box([20, 0, 0])
        V = np.vstack([V1, V2, V3])
        F = np.vstack([F1, F2 + 8, F3 + 16])
        lab = np.array([gate.FRAME] * 12 + [gate.TEMPLE] * 12 + [gate.FRAME] * 12, np.int8)
        fl = gate.floating_parts(gate.Model("m", V, F, lab))
        self.assertEqual(fl["components"], 3)
        self.assertEqual(len(fl["floating"]), 1)                       # the far box; the two touching boxes are fine
        self.assertGreater(fl["floating"][0]["centroid_mm"][0], 15)
        lab[24:] = gate.LENS                                            # a lens is never flagged
        self.assertEqual(gate.floating_parts(gate.Model("m", V, F, lab))["floating"], [])

    def test_criteria_c3_uses_previous_plus_tolerance(self):
        mk = lambda a: {"kind": "bsa", "views": {"angled": {"front_piece": {"pct_w": a}}}, "seam": {"has_lens": True, "gap_pixels": 0},
                        "lens": {"gt": {"frame_symmetric_mean_mm": 0.2, "criterion_photo": "front"}}, "phantom": {"pct": 0.0}}
        prev = mk(0.30)
        self.assertTrue(gate.criteria(mk(0.39), prev)["c3_heldout_angled_front_piece"]["pass"])
        self.assertFalse(gate.criteria(mk(0.41), prev)["c3_heldout_angled_front_piece"]["pass"])
        c = gate.criteria(mk(0.30), prev)
        self.assertTrue(c["c2_lens_edge_vs_gt"]["pass"] and c["c4_zero_seam_gaps"]["pass"])
        self.assertIsNone(c["c1_contract_ar_lenses"]["pass"])


# ------------------------------------------------------------------------------------------ real data
def _have(p: str, *stages) -> bool:
    return all((run_dir("m1", p) / s / "result.json").exists() for s in stages)


@unittest.skipUnless(_have("oakley", "s0_intake", "s2_front", "s3_cameras", "s4_depth"), "m1 artifacts missing")
class TestViewConsistency(unittest.TestCase):
    """The geometric front/back check replaces S2's 2D 'front_back_disagree': on the real product it must see the
    oakley outline as consistent (the 2D distance was parallax), and a grossly wrong outline must be flagged."""

    def test_oakley_consistent_and_sensitive(self):
        r = gate.lens_view_consistency("oakley", "m1")
        self.assertTrue(r["available"])
        self.assertEqual((r["source_view"], r["other_view"]), ("front", "back"))
        self.assertFalse(r["flag"])
        self.assertLess(r["ratio"], gate.VIEW_CONSISTENCY_K)
        self.assertGreater(r["noise_mm"], 0)
        # a 3 mm outline error is flagged in at least one direction
        self.assertTrue(any((v or 0) > gate.VIEW_CONSISTENCY_K for k, v in r["sensitivity_ratio"].items() if k in ("-3mm", "+3mm")))
        self.assertIsNotNone(r["detects_outline_error_mm"])

    def test_grow_ring(self):
        P = np.stack([100 + 30 * np.cos(np.linspace(0, 2 * np.pi, 200, endpoint=False)),
                      100 + 20 * np.sin(np.linspace(0, 2 * np.pi, 200, endpoint=False))], 1)
        from bsa import front
        self.assertAlmostEqual(front.contour_distance(P, gate._grow_ring(P, 3.0))[0], 3.0, delta=0.15)
        self.assertAlmostEqual(front.contour_distance(P, gate._grow_ring(P, -3.0))[0], 3.0, delta=0.15)


@unittest.skipUnless(_have("vb", "s0_intake", "s1_generator", "s2_front", "s3_cameras"), "m1 artifacts missing")
class TestRealVB(unittest.TestCase):
    def test_previous_candidate_alignment(self):
        from bsa import generator
        gen = generator.load("vb")
        m = gate.load_glb_model("previous", PRODUCTS["vb"].candidate_glb, gen, "previous")
        pl = m.info["placement"]
        self.assertLess(pl["residual_candidate_to_generator_mm"]["median"], 0.05)
        self.assertLess(pl["rotation_deg"], 0.1)
        self.assertAlmostEqual(pl["scale"], 0.966, delta=0.003)

    def test_card_chain_and_held_out(self):
        """The card lens is S2 lifted and re-rendered: its edge must reproduce S2 (<0.15 mm); on the held-out
        view the card (no temples, flat) must score far worse than 1 %W."""
        from bsa import generator
        gen = generator.load("vb")
        _, cams, _ = C.load_cameras("vb")
        card = gate.tilted_card("vb", gen, cams)
        ev = gate.evaluate("vb", {"card": card}, views=("front", "angled"), log=lambda *a: None)
        r = ev["models"]["card"]
        self.assertLess(r["lens"]["s2"]["symmetric_mean_mm"], 0.15)
        self.assertLess(r["views"]["front"]["front_piece"]["pct_w"], 0.3)
        self.assertGreater(r["views"]["angled"]["front_piece"]["pct_w"], 1.0)
        self.assertEqual(r["seam"]["gap_pixels"], 0)


class TestLensColour(unittest.TestCase):
    def test_lens_colour_rule(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "s8_lens"
            d.mkdir()
            with mock.patch.object(gate, "run_dir", lambda run, product: Path(td)):
                self.assertEqual(gate.lens_colour("x", "r")["flag"], "lens_colour_unchecked")
                for val, flag in ((2.7, None), (gate.LENS_COLOUR_DE00_MAX + 0.1, "lens_colour_mismatch")):
                    (d / "result.json").write_text(json.dumps({"lens_colour_check": {"ok": True, "rendered_dE00": val}}))
                    lc = gate.lens_colour("x", "r")
                    self.assertEqual(lc["flag"], flag)
                    self.assertEqual(lc["rendered_dE00"], val)
                (d / "result.json").write_text(json.dumps({"lens_colour_check": {"ok": False, "reason": "harness"},
                                                            "flags": ["lens_colour_check_failed"]}))
                self.assertIsNone(gate.lens_colour("x", "r")["flag"])          # S8's own flag carries it
        ok = {"c1": {"pass": True}, "c2": {"pass": True}, "c3": {"pass": True}, "c4": {"pass": True}}
        self.assertEqual(gate.decide(ok, ["lens_colour_mismatch"], True)["decision"], "REVIEW")


class TestStageResults(unittest.TestCase):
    """The saved s10_gate results (when present): structure, the card control, every PRIMARY fault check, the
    same-mesh noise floor on the held-out view, and that re-deciding a saved result reproduces it."""

    def test_saved_results(self):
        found = 0
        for p in PRODUCTS:
            f = run_dir("m1", p) / "s10_gate" / "result.json"
            if not f.exists():
                continue
            r = json.loads(f.read_text())
            if "validation" not in r or not r["validation"].get("faults"):
                continue
            found += 1
            with self.subTest(product=p):
                self.assertIn(r["decision"]["decision"], ("READY", "RETRY", "REVIEW"))
                v = r["validation"]
                self.assertTrue(v["card_vs_previous_angled"]["card_fails_c3"])
                self.assertIn("angled_front_piece", v["card_plan_gates_failed"])
                for fault, rows in v["faults"].items():
                    for row in rows:
                        if row["primary"]:
                            self.assertTrue(row["pass"], f"{p} {fault} {row['metric']}")
                self.assertTrue(v["all_pass"])
                self.assertLess(abs(v["same_mesh_noise_pct_w"]["angled"]), gate.CRIT3_PCT_W / 2)
                for name in ("previous", "card", "generator"):
                    self.assertIn(name, r["models"])
                re = gate.finalize(r, "m1")
                self.assertEqual(re["decision"], r["decision"])
                self.assertEqual(re["validation"]["all_pass"], v["all_pass"])
        if not found:
            self.skipTest("no s10_gate results")


if __name__ == "__main__":
    unittest.main()
