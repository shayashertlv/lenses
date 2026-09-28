"""bsa.donor: exact mesh clipping on scalar fields, the rim/hardware rule, hull views, a synthetic extraction
(a plate + an endpiece block + a temple behind the split), and a real-run smoke test of the delivered donors."""
from __future__ import annotations

import unittest

import cv2
import numpy as np
import open3d as o3d

from bsa import depth, donor, raster
from bsa import temples as T
from bsa.contract import topology
from bsa.core import NormFrame, run_dir, stage_dir
from _meshes import box

FRAME = NormFrame((0.0, 0.0, 60.0), 140.0)


_box = box


def _signed_volume(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


class Clip(unittest.TestCase):
    def test_plane_clip_of_a_cube_is_exact_and_manifold(self):
        V, F = _box(-0.5, 0.5, -0.5, 0.5, -0.5, 0.5, subdivide=2)
        V2, F2, parent = donor.clip_mesh(V, F, V[:, 2] - 0.3)
        V2, F2, _ = donor.compact(V2, F2)
        self.assertLessEqual(float(V2[:, 2].max()), 0.3 + 1e-9)
        self.assertAlmostEqual(float(donor.face_areas(V2, F2).sum()), 1.0 + 4 * 0.8, places=9)
        topo = T.edge_topology(F2)
        self.assertEqual(topo["nonmanifold"], 0)
        self.assertEqual(topo["misoriented"], 0)
        loops = T.boundary_loops(F2)
        self.assertEqual(len(loops), 1)                                   # one cut loop, on the plane
        np.testing.assert_allclose(V2[loops[0], 2], 0.3, atol=1e-9)
        # capping the cut loop closes it with the input's orientation (positive volume = 0.8)
        Vc, Fc, _, _, _ = T.close_temple(V2, F2, [], loops, 0.0, None)
        self.assertTrue(topology(Vc, Fc, weld=1e-9)["watertight"])
        self.assertAlmostEqual(_signed_volume(Vc, Fc), 0.8, places=6)
        self.assertEqual(len(parent), len(F2))

    def test_curved_field_and_orientation(self):
        m = o3d.geometry.TriangleMesh.create_sphere(1.0, 40)
        V, F = np.asarray(m.vertices, float), np.asarray(m.triangles, np.int64)
        phi = V[:, 0] ** 2 + V[:, 1] ** 2 - 0.5 ** 2                  # keep inside a cylinder of radius 0.5
        V2, F2, par = donor.clip_mesh(V, F, phi)
        V2, F2, _ = donor.compact(V2, F2)
        r = np.hypot(V2[:, 0], V2[:, 1])
        self.assertLessEqual(float(r.max()), 0.5 + 0.02)                   # linear interpolation of a quadric
        self.assertEqual(len(par), len(F2))
        n2 = np.cross(V2[F2[:, 1]] - V2[F2[:, 0]], V2[F2[:, 2]] - V2[F2[:, 0]])
        ok = np.linalg.norm(n2, axis=1) > 1e-12
        # every output face keeps the outward orientation of the sphere
        C = V2[F2].mean(axis=1)
        self.assertTrue(np.all(np.sum(n2[ok] * C[ok], axis=1) > 0))

    def test_empty_and_full(self):
        V, F = _box(0, 1, 0, 1, 0, 1)
        V2, F2, _ = donor.clip_mesh(V, F, np.full(len(V), 1.0))
        self.assertEqual(len(F2), 0)
        V2, F2, _ = donor.clip_mesh(V, F, np.full(len(V), -1.0))
        np.testing.assert_array_equal(F2, F)


class Hardware(unittest.TestCase):
    def test_rim_band_vs_hardware(self):
        shape = (400, 600)
        lens = np.stack([300 + 100 * np.cos(np.linspace(0, 2 * np.pi, 400, endpoint=False)),
                         200 + 80 * np.sin(np.linspace(0, 2 * np.pi, 400, endpoint=False))], 1)
        lens_m = np.zeros(shape, np.uint8)
        cv2.fillPoly(lens_m, [np.round(lens * 16).astype(np.int32)], 1, shift=4)
        rim = cv2.dilate(lens_m, np.ones((21, 21), np.uint8)).astype(bool) & ~lens_m.astype(bool)
        # a rim band on the whole upper half only (a half-rim) and a blob touching the lens at the right end
        upper = rim.copy()
        upper[200:, :] = False
        blob = np.zeros(shape, bool)
        blob[205:230, 396:430] = True
        blob &= ~lens_m.astype(bool)
        far = np.zeros(shape, bool)
        far[20:40, 20:60] = True
        frame = upper | blob | far
        types = np.zeros(len(lens), np.int8)                     # every edge frame-bounded where frame touches
        hw, info = donor.hardware_mask(frame, [lens], [types])
        self.assertFalse(hw[upper & ~blob].any())                # half rim: ~50 % of the outline -> rim band
        self.assertTrue(hw[blob & ~upper].all())                 # short contact -> hardware
        self.assertTrue(hw[far].all())                           # touching nothing -> hardware
        self.assertEqual(info["hardware_components"], 2)
        # a rimless outline (type 2 everywhere) makes even the half rim hardware
        hw2, _ = donor.hardware_mask(frame, [lens], [np.full(len(lens), 2, np.int8)])
        self.assertTrue(hw2[upper].all())

    def test_specks_are_not_hardware(self):
        # 8-connected components (a pixel touching the rim diagonally is rim), hardware >= HW_MIN_AREA_MM2
        shape = (200, 300)
        lens = np.stack([150 + 60 * np.cos(np.linspace(0, 2 * np.pi, 300, endpoint=False)),
                         100 + 40 * np.sin(np.linspace(0, 2 * np.pi, 300, endpoint=False))], 1)
        lens_m = np.zeros(shape, np.uint8)
        cv2.fillPoly(lens_m, [np.round(lens * 16).astype(np.int32)], 1, shift=4)
        rim = cv2.dilate(lens_m, np.ones((15, 15), np.uint8)).astype(bool) & ~lens_m.astype(bool)
        ys, xs = np.nonzero(rim)
        k = int(np.argmin(ys))                                   # the rim's top pixel
        frame = rim.copy()
        frame[ys[k] - 1, xs[k] + 1] = True                       # a diagonal neighbour of the rim
        frame[20, 20] = True                                     # an isolated 1 px speck
        frame[20:40, 250:280] = True                             # a real part (600 px)
        types = [np.zeros(len(lens), np.int8)]
        hw, info = donor.hardware_mask(frame, [lens], types, ppm=10.0)   # 1 mm2 = 100 px
        self.assertFalse(hw[ys[k] - 1, xs[k] + 1], "diagonal to the rim: part of the rim band")
        self.assertFalse(hw[20, 20], "a speck below 1 mm2 is not hardware")
        self.assertTrue(hw[20:40, 250:280].all())
        self.assertEqual(info["hardware_components"], 1)
        self.assertEqual(info["specks_dropped"], 1)
        hw_nopm, _ = donor.hardware_mask(frame, [lens], types)            # no scale: no area floor
        self.assertTrue(hw_nopm[20, 20])

    def test_projection_failure_is_recorded(self):
        class NearPlane:
            def project(self, P, frame):
                raise ValueError("Geometry crosses the camera near plane")
        failures = []
        cosv = donor.facing_cos(np.zeros((3, 3)), None, NearPlane(), FRAME, failures)
        self.assertTrue(np.all(cosv == 1.0))
        self.assertEqual(failures, ["facing"])

    def test_signed_distance_and_grid_sampling(self):
        m = np.zeros((50, 60), bool)
        m[10:20, 10:30] = True
        sd = donor.signed_distance(m)
        self.assertLess(sd[15, 20], 0)
        self.assertGreater(sd[40, 50], 0)
        s_axis = np.arange(60) * 0.5
        y_axis = np.arange(50)[::-1] * 0.5                       # descending y (as the S4 grid)
        v = donor.sample_grid(sd, s_axis, y_axis, np.array([10.0]), np.array([y_axis[15]]), 99.0)
        self.assertAlmostEqual(float(v[0]), float(sd[15, 20]), places=6)
        self.assertEqual(float(donor.sample_grid(sd, s_axis, y_axis, np.array([-50.0]), np.array([0.0]), 99.0)[0]), 99.0)


class Hull(unittest.TestCase):
    def test_hull_view_inside_outside(self):
        shape = (300, 400)
        cam = raster.view_camera(FRAME, 90.0, 0.0, 4.0, shape)
        P = np.array([[60.0, 0.0, 60.0], [60.0, 30.0, 60.0]])
        uv = raster._project_norm(FRAME.to_norm(P), cam)
        matte = np.zeros(shape, bool)
        u, v = np.round(uv[0]).astype(int)
        matte[v - 10:v + 10, u - 10:u + 10] = True
        h = donor.HullView("left", cam, matte, FRAME, 2.0, 4.0)
        out = h.outside(P)
        self.assertLess(out[0], 0)
        self.assertGreater(out[1], 50)
        np.testing.assert_allclose(donor.hull_outside_mm([h], P), out / 4.0)


class TrustedHulls(unittest.TestCase):
    def test_low_weight_views_do_not_carve(self):
        from types import SimpleNamespace
        views = [SimpleNamespace(view="back", weight=1.0), SimpleNamespace(view="left", weight=0.25),
                 SimpleNamespace(view="right")]                              # no weight recorded: trusted
        kept, skipped = donor.trusted_hulls(views)
        self.assertEqual([h.view for h in kept], ["back", "right"])
        self.assertEqual(skipped, ["left"])
        self.assertEqual(donor.trusted_hulls(None), ([], []))


class HardwareOverLens(unittest.TestCase):
    """A fused mesh: a lens plate (white) with a drill-mount block (gold) passing THROUGH it, the block joined by a
    bar to S2 hardware off the lens; a gold blob elsewhere on the lens touches no hardware."""

    @staticmethod
    def _mesh():
        parts = [(_box(-10, 10, -10, 10, -1, 1, 4), (245, 245, 243), "lens"),       # lens plate, 2 mm thick
                 (_box(6, 9, -2, 2, -3, 3, 2), (230, 200, 140), "mount"),         # drill mount through the lens
                 (_box(9, 17, -1, 1, 1, 3, 4), (230, 200, 140), "bar"),           # bar to the hinge (off the lens)
                 (_box(-6, -4, -1, 1, 1, 2, 2), (230, 200, 140), "blob")]         # gold blob on the lens, isolated
        V, F, col, tag = [], [], [], []
        off = 0
        for (v, f), c, t in parts:
            V.append(v); F.append(f + off); col.append(np.tile(c, (len(f), 1))); tag += [t] * len(f)
            off += len(v)
        V, F = np.vstack(V), np.vstack(F)
        # fuse: weld the mount and the bar where they touch (the generator is one mesh)
        Wpos, winv = T.weld(V)
        return Wpos, winv[F], np.vstack(col).astype(float), np.array(tag)

    def test_block_through_the_lens_is_hardware_whole(self):
        Wpos, FW, rgb, tag = self._mesh()
        C = Wpos[FW].mean(axis=1)
        in_lens = (np.abs(C[:, 0]) <= 10.5) & (np.abs(C[:, 1]) <= 10.5)
        lens_ref = (tag == "lens") & (np.abs(C[:, 0]) < 4)
        hw_ref = (tag == "bar") & (C[:, 0] > 11)
        seed = (tag == "bar")
        hw, lens_col, info = donor.hardware_over_lens(FW, len(Wpos), rgb, in_lens, lens_ref, hw_ref, seed)
        self.assertTrue(info["active"])
        mount_in_lens = (tag == "mount") & in_lens
        self.assertTrue(hw[mount_in_lens].all())            # its walls inside the lens slab included
        self.assertFalse(hw[tag == "blob"].any())            # not connected to hardware
        self.assertFalse(hw[tag == "lens"].any())
        self.assertTrue(lens_col[(tag == "lens") & in_lens].all())

    def test_no_colour_rule_when_colours_do_not_separate(self):
        Wpos, FW, rgb, tag = self._mesh()
        rgb[:] = (120, 120, 120)                              # lens and hardware the same colour
        C = Wpos[FW].mean(axis=1)
        in_lens = (np.abs(C[:, 0]) <= 10.5)
        hw, lens_col, info = donor.hardware_over_lens(FW, len(Wpos), rgb, in_lens, tag == "lens", tag == "bar", tag == "bar")
        self.assertFalse(info["active"])
        self.assertFalse(hw.any() or lens_col.any())


class HardwareFragments(unittest.TestCase):
    def test_a_floating_hardware_fragment_is_dropped_a_part_is_kept(self):
        """Hardware is anchored by itself (the lens holds rimless hardware), except a fragment smaller than
        HW_FRAGMENT_MM2 (a clipped-off corner): it must touch something like any other donor part."""
        small = _box(0, 2, 0, 2, 0, 2, 1)                     # 24 mm2: a fragment
        big = _box(10, 14, 0, 4, 0, 4, 1)                     # 96 mm2: a part
        V = np.vstack([small[0], big[0]])
        F = np.vstack([small[1], big[1] + len(small[0])])
        n = len(V)
        info = {}
        res = donor._finish(V, F, np.zeros(n), np.ones(n, bool), V, F, None, np.arange(len(F)), 4000, T, "R", info,
                            np.zeros(n, bool), np.zeros(n, bool), None)
        self.assertEqual(info["R"]["floating_dropped"], 1)
        self.assertEqual(info["R"]["components_kept"], 1)
        self.assertGreater(float(res["Vw"][:, 0].min()), 9.9)
        # touching an anchored part within ANCHOR_GAP_MM, the same fragment stays
        V2 = np.vstack([small[0] + [8.2, 0, 0], big[0]])
        info2 = {}
        donor._finish(V2, F, np.zeros(n), np.ones(n, bool), V2, F, None, np.arange(len(F)), 4000, T, "R", info2,
                      np.zeros(n, bool), np.zeros(n, bool), None)
        self.assertEqual(info2["R"]["components_kept"], 2)


class SyntheticExtraction(unittest.TestCase):
    """A planar plate (front z = 70, 5 mm thick) seen by an orthographic front camera; the 'generator' is the plate
    slab, an endpiece block behind its right end (z 58..65) and a temple arm behind the split (z < 58)."""

    @classmethod
    def setUpClass(cls):
        plate = _box(-60, 60, -20, 20, 65, 70, 3)
        block = _box(50, 60, -5, 5, 57, 65.05, 3)                  # touches the plate back
        arm = _box(55, 59, -3, 3, 20, 58.5, 3)
        V, F, off = [], [], 0
        for v, f in (plate, block, arm):
            V.append(v); F.append(f + off); off += len(v)
        cls.V, cls.F = np.vstack(V), np.vstack(F)
        cls.UV = np.stack([(cls.V[:, 0] + 70) / 140, (cls.V[:, 1] + 30) / 60], 1)
        base = depth.Base("planar")
        front = depth.SmoothField(depth.Poly2D(((0, 0),), 0.0, 0.0, 30.0), np.array([70.0]))
        s_axis = np.arange(-80, 80.01, 0.5)
        y_axis = np.arange(40, -40.01, -0.5)
        thick = np.full((len(y_axis), len(s_axis)), 5.0)
        cls.df = depth.DepthField(base, front, s_axis, y_axis, thick, [], ((-150, 150), (-100, 100), (0, 100)))
        shape = (200, 700)
        cam = raster.view_camera(FRAME, 0.0, 0.0, 4.0, shape)
        cls.src = depth.SourceView("front", cam, shape, False)
        uv = raster._project_norm(FRAME.to_norm(cls.V), cam)
        fg = np.zeros(shape, np.uint8)
        x0, y0 = uv.min(0)
        x1, y1 = uv.max(0)
        cv2.rectangle(fg, (int(x0) - 3, int(y0) - 3), (int(x1) + 3, int(y1) + 3), 1, -1)
        S, Y = np.meshgrid(s_axis, y_axis)
        cls_grid = np.where((np.abs(S) <= 60) & (np.abs(Y) <= 20), 1, 0).astype(np.int8)
        t_raw = np.where(cls_grid == 1, 5.0, -1.0)
        t_raw[(S >= 50) & (S <= 60) & (np.abs(Y) <= 5)] = 13.0     # the block makes the generator thicker there
        cls.s4 = {"param_s": s_axis, "param_y": y_axis, "param_class": cls_grid, "param_t_raw": t_raw,
                  "param_thickness": thick}
        cls.Wpos, cls.winv = T.weld(cls.V)
        cls.res = donor.extract(cls.V, cls.F, cls.UV, cls.Wpos, cls.winv, cls.df, cls.src, FRAME, fg.astype(bool), [],
                                cls.s4, 58.0, 4.0, target_faces=4000)

    def test_right_block_only(self):
        r = self.res["R"]
        self.assertIsNotNone(r)
        self.assertIsNone(self.res["L"])                           # nothing thick behind the left end
        self.assertTrue(r["closed"])
        V = r["Vw"]
        # the block between the plate back (65, minus the overlap) and the split (58); no plate, no arm
        self.assertGreaterEqual(float(V[:, 2].min()), 58.0 - 1e-6)
        self.assertLessEqual(float(V[:, 2].max()), 65.0 + donor.OVERLAP_MM + 1e-6)
        self.assertGreater(float(V[:, 2].max()), 65.0 - 1e-6)     # reaches INTO the plate: no void
        self.assertGreater(float(V[:, 0].min()), 49.0)
        self.assertEqual(r["UV"].shape, (len(r["V"]), 2))
        self.assertTrue(topology(r["V"] / 1000.0, r["F"])["watertight"])
        self.assertGreater(r["volume_mm3"], 0.8 * 10 * 10 * (65 - 58))

    def test_floating_part_is_dropped(self):
        """A generator part behind the plate that touches neither the plate nor the arm (a sliver behind a temple
        root) would float in the delivered model: dropped. The block that touches the plate back stays."""
        floater = _box(-58, -50, -4, 4, 59.0, 62.0, 3)                # 3 mm behind the plate back, clear of it
        V = np.vstack([self.V, floater[0]])
        F = np.vstack([self.F, floater[1] + len(self.V)])
        UV = np.stack([(V[:, 0] + 70) / 140, (V[:, 1] + 30) / 60], 1)
        s4 = dict(self.s4)
        S, Y = np.meshgrid(s4["param_s"], s4["param_y"])
        t_raw = s4["param_t_raw"].copy()
        t_raw[(S >= -60) & (S <= -48) & (np.abs(Y) <= 6)] = 13.0   # generator thicker there: donor zone
        s4["param_t_raw"] = t_raw
        Wpos, winv = T.weld(V)
        res = donor.extract(V, F, UV, Wpos, winv, self.df, self.src, FRAME, np.ones(self.src.shape, bool), [], s4,
                            58.0, 4.0, target_faces=4000, anchors={"R": _box(55, 59, -3, 3, 20, 58.5, 3)[0]})
        self.assertIsNone(res["L"])
        self.assertEqual(res["info"]["L"]["floating_dropped"], 1)
        self.assertIsNotNone(res["R"])
        self.assertEqual(res["info"]["R"]["floating_dropped"], 0)

    def test_deterministic(self):
        again = donor.extract(self.V, self.F, self.UV, self.Wpos, self.winv, self.df, self.src, FRAME,
                              np.ones(self.src.shape, bool), [], self.s4, 58.0, 4.0, target_faces=4000)
        np.testing.assert_array_equal(again["R"]["F"], self.res["R"]["F"])
        np.testing.assert_allclose(again["R"]["V"], self.res["R"]["V"])


PRODUCTS = ("miu", "oakley", "rayban", "vb", "invu")


# an existence test that builds no StageDir: StageDir() creates its folder, so collection would write under data/
@unittest.skipUnless(all((run_dir("m1", p) / T.STAGE / "result.json").is_file() for p in PRODUCTS), "S5 m1 artifacts missing")
class RealRunDonors(unittest.TestCase):
    def test_saved_donors(self):
        for p in PRODUCTS:
            res, arr = stage_dir("m1", p, T.STAGE).load()
            with self.subTest(product=p):
                self.assertIn("donor", res)
                self.assertIn("split", res)
                # the hull carves only through cameras S3 trusts
                s3 = stage_dir("m1", p, "s3_cameras").load()[0]
                for v in res["donor"].get("hull_views", []):
                    self.assertGreaterEqual(float(s3["cameras"][v].get("weight", 1.0)), donor.HULL_MIN_WEIGHT)
                # the hardware-over-the-lens colour rule runs only on a rimless front
                s2 = stage_dir("m1", p, "s2_front").load()[0]
                if s2.get("rim_class") != "rimless":
                    self.assertNotIn("hardware_over_lens", res["donor"])
                for s in T.SIDES:
                    if f"donor_{s}_faces" not in arr:
                        continue
                    a, b = arr[f"donor_{s}_faces"]
                    F = arr[f"temple_{s}_F"][a:b]
                    V = arr[f"temple_{s}_V"].astype(np.float64)
                    used = np.unique(F)
                    self.assertTrue(topology(V[used] / 1000.0, np.searchsorted(used, F))["watertight"])
                    # in front of the split, on its own side
                    self.assertGreaterEqual(float(V[used, 2].min()), res["cut_z_mm"] - 0.05)
                    self.assertGreater(T.SIGN[s] * float(V[used, 0].min()), -0.01)
                self.assertLessEqual(res["cut_depth_behind_front_mm"] - (res["front_z_mm"] - min(res["endpiece_back_face_z_mm"].values())),
                                     T.CUT_BEHIND_MM + T.SPLIT_SEARCH_MM + 1e-6)


if __name__ == "__main__":
    unittest.main()
