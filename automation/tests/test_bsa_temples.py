"""bsa.temples (S5): mesh surgery, endpiece rule, UV transfer, rigid refinement, real-run smoke."""
from __future__ import annotations

import json
import unittest

import numpy as np
import open3d as o3d

from bsa import raster
from bsa import temples as T
from bsa.contract import topology
from bsa.core import NormFrame, project_mm, run_dir, stage_dir
from reconstruction.camera import Camera
from _meshes import box


_box = box


def _open_front(V, F, z_front):
    """Drop the faces of the +z cap: an open tube whose boundary loop lies on z = z_front."""
    keep = ~(np.abs(V[F][:, :, 2] - z_front) < 1e-9).all(axis=1)
    return F[keep]


def _merge(parts):
    V, F, off = [], [], 0
    for v, f in parts:
        V.append(v)
        F.append(f + off)
        off += len(v)
    return np.vstack(V), np.vstack(F)


class MeshSurgery(unittest.TestCase):
    def test_weld_merges_seam_duplicates_and_components(self):
        V, F = _box(0, 1, 0, 1, 0, 1)
        # split every face onto its own vertices (like UV seams): 36 vertices, all faces disconnected
        Vs = V[F].reshape(-1, 3)
        Fs = np.arange(len(Vs)).reshape(-1, 3)
        self.assertEqual(T.face_components(Fs, len(Vs)).max() + 1, len(Fs))
        W, inv = T.weld(Vs)
        self.assertEqual(len(W), 8)
        self.assertEqual(T.face_components(inv[Fs], len(W)).max() + 1, 1)
        V2, F2 = _merge([_box(0, 1, 0, 1, 0, 1), _box(5, 6, 0, 1, 0, 1)])
        lab = T.face_components(F2, len(V2))
        self.assertEqual(lab.max() + 1, 2)
        self.assertEqual(lab[0], 0)                      # labels by first occurrence

    def test_orientation_repair_and_outward(self):
        V, F = _box(0, 2, 0, 3, 0, 4, subdivide=1)
        bad = F.copy()
        bad[::3] = bad[::3, ::-1]
        self.assertGreater(T.edge_topology(bad)["misoriented"], 0)
        G, flipped = T.orient_consistently(bad)
        self.assertEqual(T.edge_topology(G)["misoriented"], 0)
        inward = G if T.signed_volume(V, G) < 0 else G[:, ::-1]
        G2, _, n = T.orient_outward(V, inward)
        self.assertAlmostEqual(T.signed_volume(V, G2), 24.0, places=6)
        self.assertEqual(n, 1)
        self.assertEqual(T.orient_outward(V, G2)[2], 0)

    def test_boundary_loops_split_a_pinched_boundary(self):
        # two triangles touching at vertex 0 (a bowtie): two simple loops, one crossing
        F = np.array([[0, 1, 2], [0, 3, 4]])
        loops = T.boundary_loops(F)
        self.assertEqual(sorted(len(lp) for lp in loops), [3, 3])
        self.assertEqual(T.count_crossings(loops), 1)
        V, F2 = _box(0, 5, 0, 8, -60, 0, subdivide=2)
        loops = T.boundary_loops(_open_front(V, F2, 0.0))
        self.assertEqual(len(loops), 1)
        self.assertTrue(np.allclose(V[loops[0], 2], 0.0))
        self.assertEqual(T.count_crossings(loops), 1)

    def test_close_temple_lofts_to_the_join_plane_and_is_watertight(self):
        V, F = _box(62, 67, 0, 8, -60, 50, subdivide=2)
        Fo = _open_front(V, F, 50.0)
        Fo, _ = T.orient_consistently(Fo)
        CUV = np.random.default_rng(0).random((len(Fo), 3, 2))
        lc, lh = T.classify_loops(V, Fo, 50.0, 0.5)
        self.assertEqual((len(lc), len(lh)), (1, 0))
        Vc, Fc, CUVc, kinds, info = T.close_temple(V, Fo, lc, lh, 51.2, CUV)
        # wall corners reuse the UVs of the donor face that owns their boundary edge: wall face (b, a, a')
        edge_uv = {}
        for k in range(3):
            for f in range(len(Fo)):
                edge_uv[(int(Fo[f, k]), int(Fo[f, (k + 1) % 3]))] = (CUV[f, k], CUV[f, (k + 1) % 3])
        walls = np.nonzero(kinds == 1)[0]
        for f in walls[:len(walls) // 2]:                                  # first block: (b, a, a') per edge
            b, a, ap = (int(v) for v in Fc[f])
            ua, ub = edge_uv[(a, b)]
            self.assertTrue(np.allclose(CUVc[f], [ub, ua, ua]))
            self.assertTrue(np.allclose(Vc[ap, :2], Vc[a, :2]) and abs(Vc[ap, 2] - 51.2) < 1e-9)
        self.assertEqual(len(np.unique(CUVc[kinds == 2].reshape(-1, 2), axis=0)), 1)     # the cap is one texel
        Fc, CUVc, _ = T.orient_outward(Vc, Fc, CUVc)
        topo = topology(Vc / 1000.0, Fc)
        self.assertTrue(topo["watertight"], topo)
        self.assertAlmostEqual(T.signed_volume(Vc, Fc), 5 * 8 * 111.2, places=4)
        self.assertAlmostEqual(Vc[:, 2].max(), 51.2)
        self.assertAlmostEqual(info[0]["loft_mm_max"], 1.2, places=6)
        self.assertEqual(CUVc.shape, (len(Fc), 3, 2))
        # split into glTF per-vertex UVs keeps the topology closed after welding
        Vo, Fsplit, UVo = T.split_by_uv(Vc, Fc, CUVc)
        self.assertEqual(len(UVo), len(Vo))
        self.assertTrue(topology(Vo / 1000.0, Fsplit)["watertight"])

    def test_min_loft_never_welds_a_wall_shut(self):
        V, F = _box(62, 67, 0, 8, -60, 50, subdivide=1)
        Fo, _ = T.orient_consistently(_open_front(V, F, 50.0))
        lc, lh = T.classify_loops(V, Fo, 50.0, 0.5)
        Vc, Fc, _, _, _ = T.close_temple(V, Fo, lc, lh, 49.0)       # join plane BEHIND the cut end
        Fc, _, _ = T.orient_outward(Vc, Fc)
        self.assertTrue(topology(Vc / 1000.0, Fc)["watertight"])

    def test_decimate_keeps_a_closed_mesh_closed_and_is_deterministic(self):
        m = o3d.geometry.TriangleMesh.create_sphere(radius=10.0, resolution=40)
        V, F = np.asarray(m.vertices), np.asarray(m.triangles)
        a = T.decimate(V, F, 600)
        b = T.decimate(V, F, 600)
        self.assertTrue(np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]))
        self.assertLessEqual(len(a[1]), 700)
        self.assertEqual((a[2]["boundary"], a[2]["nonmanifold"], a[2]["misoriented"]), (0, 0, 0))

    def test_split_by_uv(self):
        V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], float)
        F = np.array([[0, 1, 2], [2, 1, 3]])
        uv = V[:, :2] / 2
        CUV = uv[F]
        Vo, Fo, UVo = T.split_by_uv(V, F, CUV)
        self.assertEqual(len(Vo), 4)
        CUV2 = CUV.copy()
        CUV2[1, 0] += 0.25                                    # vertex 2 disagrees across the shared edge
        Vo, Fo, UVo = T.split_by_uv(V, F, CUV2)
        self.assertEqual(len(Vo), 5)
        self.assertTrue(np.allclose(UVo[Fo], CUV2, atol=1e-6))


class UVTransfer(unittest.TestCase):
    def _source(self, n=41):
        """Square [0,10]^2 at z = 0 as two UV islands (x < 5 and x > 5) with a UV jump at the seam."""
        parts, uvs = [], []
        for x0, x1, off in ((0.0, 5.0, 0.0), (5.0, 10.0, 0.3)):
            xs, ys = np.linspace(x0, x1, n // 2 + 1), np.linspace(0, 10, n)
            X, Y = np.meshgrid(xs, ys)
            V = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])
            nx = len(xs)
            F = []
            for j in range(len(ys) - 1):
                for i in range(nx - 1):
                    a = j * nx + i
                    F += [[a, a + 1, a + nx], [a + 1, a + nx + 1, a + nx]]
            parts.append((V, np.array(F)))
            uvs.append(np.column_stack([V[:, 0] / 20 + off, V[:, 1] / 10]))
        V, F = _merge(parts)
        return V, F, np.vstack(uvs)

    def test_interior_exact_and_seam_faces_sample_one_island(self):
        sV, sF, sUV = self._source()
        xs = np.linspace(0, 10, 4)                              # coarse target: faces straddle x = 5
        X, Y = np.meshgrid(xs, xs)
        V = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])
        F = []
        for j in range(3):
            for i in range(3):
                a = j * 4 + i
                F += [[a, a + 1, a + 4], [a + 1, a + 5, a + 4]]
        F = np.array(F)
        CUV, info = T.transfer_uv(sV, sF, sUV, V, F)
        self.assertGreater(info["seam_faces"], 0)
        left = (V[F][:, :, 0] <= 5.0 - 1e-9).all(axis=1)
        exp = np.column_stack([V[:, 0] / 20, V[:, 1] / 10])[F]
        self.assertTrue(np.allclose(CUV[left], exp[left], atol=1e-5))
        u = CUV[..., 0]
        one_island = (u <= 0.25 + 1e-6).all(axis=1) | (u >= 0.55 - 1e-6).all(axis=1)
        self.assertTrue(one_island.all(), "a seam face mixes two UV islands")


class EndpieceRule(unittest.TestCase):
    def test_back_face_of_a_synthetic_front(self):
        front_z = 70.0
        X, Y, Z = np.meshgrid(np.linspace(-70, 70, 141), np.linspace(-20, 20, 41), [60.0, 65.0, 70.0])
        plate = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
        pts = [plate]
        for sx in (-1, 1):
            X, Y, Z = np.meshgrid(sx * np.array([62.0, 65.0, 68.0]), np.linspace(0, 8, 9), np.arange(-70, 60, 0.2))
            pts.append(np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]))
        V = np.vstack(pts)
        for sign in (1.0, -1.0):
            r = T.endpiece_back_face(V, front_z, sign)
            self.assertIsNotNone(r["z"])
            self.assertLessEqual(abs(r["z"] - 60.0), SLAB_TOL, r)
            self.assertAlmostEqual(r["h_ref_mm"], 8.0, places=6)
            self.assertAlmostEqual(r["w_ref_mm"], 6.0, places=6)

    def test_no_front_piece_reports_failure(self):
        Z = np.arange(-70, 70, 0.2)
        V = np.column_stack([np.full_like(Z, 65.0), np.zeros_like(Z), Z])
        V = np.vstack([V, V + [0, 5, 0], V * [-1, 1, 1]])
        self.assertIsNone(T.endpiece_back_face(V, 70.0, 1.0)["z"])


SLAB_TOL = T.SLAB_HALF_MM + T.SLAB_STEP_MM


class SplitPlane(unittest.TestCase):
    def test_split_avoids_a_relief_feature(self):
        """A temple (box) with a relief knob just behind the endpiece: the split skips the knob's planes."""
        arm_R = _box(60, 64, -3, 3, -60, 40, 4)
        arm_L = _box(-64, -60, -3, 3, -60, 40, 4)
        knob = _box(64, 66, -2, 2, 34.5, 37.5, 2)                    # 'logo' relief between z 34.5 and 37.5
        V, F = _merge([arm_R, arm_L, knob])
        z_front = 38.0
        per_knob = T.section_perimeter(V, F, 36.0, 40.0)
        per_plain = T.section_perimeter(V, F, 30.0, 40.0)
        self.assertAlmostEqual(per_plain, 2 * (2 * 4 + 2 * 6), delta=0.1)   # two 4 x 6 rectangles
        self.assertGreater(per_knob, per_plain + 3.0)
        z, info = T.choose_split(V, F, z_front, 40.0)
        self.assertTrue(z > 37.5 or z < 34.5, z)                      # never through the knob
        self.assertAlmostEqual(z, 38.0, delta=1e-9)                   # front-most plain plane (38 > 37.5)
        z2, _ = T.choose_split(V, F, 37.0, 40.0)                      # starting inside the knob: skip behind it
        self.assertLessEqual(z2, 34.5)
        self.assertGreater(z2, 37.0 - T.SPLIT_SEARCH_MM - 1e-9)


class RigidPose(unittest.TestCase):
    def test_open_and_lift_signs_both_sides(self):
        for sign in (1.0, -1.0):
            pivot = np.array([65.0 * sign, 5.0, 50.0])
            tip = np.array([[65.0 * sign, 5.0, -100.0]])
            op = T.apply_rigid(tip, np.array([5.0, 0, 0, 0, 0]), sign, pivot)[0]
            self.assertGreater(abs(op[0]), 65.0 + 10.0)             # opens outward
            li = T.apply_rigid(tip, np.array([0, 3.0, 0, 0, 0]), sign, pivot)[0]
            self.assertGreater(li[1], 5.0 + 5.0)                     # tip rises
            self.assertTrue(np.allclose(T.apply_rigid(pivot[None], np.array([4.0, -2.0, 0, 0, 0]), sign, pivot)[0], pivot))
            R, _ = T.rigid(np.array([4.0, -2.0, 0.3, 0.1, 0.2]), sign, pivot)
            self.assertTrue(np.allclose(R @ R.T, np.eye(3)) and abs(np.linalg.det(R) - 1) < 1e-12)


class PhotoReading(unittest.TestCase):
    def test_backproject_round_trip_and_side_direction(self):
        frame = NormFrame((0.0, 0.0, 0.0), 150.0)
        cam = Camera(90.0, 10.0, 2.0, 0.2, 500.0, 400.0, 300.0)
        P = np.array([55.0, -12.0, -80.0])
        uv = project_mm(P[None], cam, frame)[0]
        Q = T.backproject_to_x(cam, frame, (float(uv[0]), float(uv[1])), P[0])
        self.assertTrue(np.allclose(Q, P, atol=1e-6))
        d = T.image_direction(Camera(90.0, 0.0, 0.0, 0.0, 500.0, 400.0, 300.0), frame, P)
        self.assertGreater(d[0], 0.99)                               # from +X, -z runs to image right
        m = np.zeros((10, 10), bool)
        m[4, 2:8] = True
        self.assertEqual(T.extreme_pixel(m, np.array([1.0, 0.0]), 100, 50), (107.0, 54.0))


class SyntheticRefinement(unittest.TestCase):
    """A box front piece and two box temples; the 'photos' are renders with temple_R opened 3 deg and lifted
    2 deg. Refining on left + back must recover the pose (the back view carries the opening)."""

    def test_recovers_open_and_lift(self):
        frame = NormFrame((0.0, 0.0, 0.0), 150.0)
        Vf, Ff = _box(-70, 70, -20, 20, 60, 70)
        VR, FR = _box(62, 68, 0, 8, -70, 59, subdivide=1)
        VL = VR * [-1, 1, 1]
        FL = FR[:, ::-1]
        pivot = np.array([65.0, 4.0, 59.0])
        truth = np.array([3.0, 2.0, 0.0, 0.0, 0.0])
        VRt = T.apply_rigid(VR, truth, 1.0, pivot)
        static = raster.RasterScene(Vf, Ff, frame)
        shape = (360, 560)
        cams = {"left": Camera(90.0, 10.0, 0.0, 0.15, 3.2 * 150, 280.0, 180.0),
                "back": Camera(180.0, 10.0, 0.0, 0.15, 3.2 * 150, 280.0, 180.0)}
        terms = []
        for v, cam in cams.items():
            fg = np.zeros(shape, bool)
            for V, F in ((Vf, Ff), (VRt, FR), (VL, FL)):
                fg |= raster.RasterScene(V, F, frame).render(cam, shape)["mask"]
            vd = T.ViewData(v, cam, fg, frame, static, [VR, VL])
            terms.append((vd, vd.render(VL, FL)))
        rf = T.refine_temple(terms, VR, FR, 1.0, pivot)
        p = rf["params"]
        self.assertLess(rf["loss_refined"], rf["loss_donor"])
        self.assertAlmostEqual(p["lift_deg"], 2.0, delta=0.6)
        self.assertAlmostEqual(p["open_deg"], 3.0, delta=1.0)
        self.assertGreater(rf["fit_iou_refined"]["back"], rf["fit_iou_donor"]["back"])


def _have(product: str, stage: str = T.STAGE) -> bool:
    # an existence test that builds no StageDir: StageDir() creates its folder, so collection would write under data/
    return (run_dir("m1", product) / stage / "result.json").is_file()


PRODUCTS = ("miu", "oakley", "rayban", "vb", "invu")


@unittest.skipUnless(all(_have(p) for p in PRODUCTS), "S5 m1 artifacts missing")
class RealRunSmoke(unittest.TestCase):
    def test_contract_of_saved_temples(self):
        for p in PRODUCTS:
            res, arr = stage_dir("m1", p, T.STAGE).load()
            self.assertIn("cut_z_mm", res)
            for s in T.SIDES:
                with self.subTest(product=p, side=s):
                    r = res[s]
                    for k in ("faces", "length_mm", "side_photo_length_mm", "gap_mm", "accepted", "reason"):
                        self.assertIn(k, r)
                    V, F = arr[f"temple_{s}_V"], arr[f"temple_{s}_F"]
                    self.assertEqual(V.dtype, np.float32)
                    self.assertEqual(F.dtype, np.int32)
                    self.assertEqual(arr[f"temple_{s}_UV"].shape, (len(V), 2))
                    self.assertEqual(arr[f"hinge_{s}"].shape, (3,))
                    self.assertTrue(topology(V.astype(np.float64) / 1000.0, F)["watertight"])
                    # the ARM (faces before the donor components): behind the split, lofted 1 mm into the donor
                    na = int(arr[f"donor_{s}_faces"][0]) if f"donor_{s}_faces" in arr else len(F)
                    Va = V[np.unique(F[:na])]
                    self.assertGreater(T.SIGN[s] * float(Va[:, 0].mean()), 30.0)       # R at +x, L at -x
                    self.assertLessEqual(float(Va[:, 2].max()), res["cut_z_mm"] + T.LOFT_INTO_DONOR_MM + 0.05)
                    self.assertLess(float(Va[:, 2].min()), res["endpiece_back_face_z_mm"][s] - 80.0)
                    self.assertLessEqual(len(F), 32_000)
                    if r["accepted"]:
                        self.assertLessEqual(r["gap_mm"], T.MAX_GAP_MM)
                        self.assertLessEqual(r["length_mismatch"], T.MAX_LENGTH_MISMATCH)
                        self.assertLessEqual(r["crossings"], T.MAX_CROSSINGS)
                        self.assertFalse(r["folded"])

    def test_cut_is_behind_the_front_and_symmetric(self):
        for p in PRODUCTS:
            res = json.loads(stage_dir("m1", p, T.STAGE).result_path.read_text())
            with self.subTest(product=p):
                self.assertGreater(res["cut_depth_behind_front_mm"], 10.0)
                self.assertLess(res["cut_depth_behind_front_mm"], 45.0)
                zb = res["endpiece_back_face_z_mm"]
                self.assertLess(abs(zb["R"] - zb["L"]), 1.0)


@unittest.skipUnless(_have("vb", "s1_generator"), "S1 m1 artifacts missing")
class RealExtract(unittest.TestCase):
    def test_vb_donors(self):
        from bsa import generator
        gen = generator.load("vb", "m1")
        ex = T.extract_donors(gen)
        behind = min(ex["z_b"].values()) - ex["cut_z"]                         # VB endpiece back face ~18 mm
        self.assertGreaterEqual(behind, T.CUT_BEHIND_MM - 1e-9)
        self.assertLessEqual(behind, T.CUT_BEHIND_MM + T.SPLIT_SEARCH_MM + 1e-9)
        self.assertAlmostEqual(ex["front_z"] - min(ex["z_b"].values()), 18.0, delta=1.5)
        for s in T.SIDES:
            d = ex["donors"][s]
            self.assertIsNotNone(d)
            C = ex["V"][ex["F"][d["faces"]]].mean(axis=1)
            self.assertGreater(T.SIGN[s] * C[:, 0].min(), 40.0)
            self.assertGreater(len(d["faces"]), 100_000)


if __name__ == "__main__":
    unittest.main()
