"""Tests for bsa.texture (S7): synthetic fixtures for the maths, plus a smoke test of the real m1 artifacts
(skipped when data/ or the S7 run is missing)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bsa import texture as T  # noqa: E402
from bsa import core, raster  # noqa: E402


def annulus_frame(thickness: float = 3.0):
    """A square frame (outer 0..40, hole 10..30) in the S6 layout: n front-cap vertices (P2 order), then the
    same n back-cap vertices, caps + walls; returns V, F, region, P2, T2."""
    outer = [(0, 0), (40, 0), (40, 40), (0, 40)]
    inner = [(10, 10), (30, 10), (30, 30), (10, 30)]
    P2 = np.array(outer + inner, float)
    # 8 triangles between the rings, counter-clockwise in (x, y)
    T2 = np.array([[0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    n = len(P2)
    Vf = np.column_stack([P2, np.zeros(n)])
    Vb = Vf + np.array([0.0, 0.0, -thickness])
    V = np.vstack([Vf, Vb])
    F = [t for t in T2] + [t[::-1] + n for t in T2]
    region = [0] * len(T2) + [1] * len(T2)
    for loop in T.boundary_loops(T2):
        for a, b in loop:
            F += [np.array([b, a, a + n]), np.array([b, a + n, b + n])]
            region += [2, 2]
    return V, np.array(F), np.array(region, np.int8), P2, T2


class TestLowResolutionFront(unittest.TestCase):
    def test_detail_transfer_keeps_the_base_colour_and_adds_the_detail(self):
        H, W = 40, 60
        cov = np.ones((H, W), bool)
        base = np.tile(np.array([40.0, 60.0, 90.0]), (H * W, 1))              # the low-res front: flat navy
        yy, xx = np.mgrid[0:H, 0:W]
        stripes = (np.sin(xx / 1.5) > 0).ravel()
        detail = np.where(stripes[:, None], 120.0, 80.0) * np.ones((1, 3))      # the back photo: fine stripes
        out = T.detail_transfer(base, np.ones(H * W, bool), detail, np.ones(H * W, bool), cov, 4.0)
        lo = T._nconv_texels(T.srgb_to_linear(out), np.ones(H * W, bool), cov, 4.0)
        np.testing.assert_allclose(lo.mean(0), T.srgb_to_linear(base[:1])[0], rtol=0.05)
        self.assertGreater(float(out[stripes, 0].mean() - out[~stripes, 0].mean()), 5.0, "the detail survives")

    def test_supersampled_base_integrates_pixels(self):
        V = np.array([[-5.2, -5.2, 0], [5.2, -5.2, 0], [5.2, 5.2, 0], [-5.2, 5.2, 0]])
        F = np.array([[0, 1, 2], [0, 2, 3]])
        frame = core.NormFrame((0.0, 0.0, 0.0), 20.0)
        cam = raster.view_camera(frame, 0.0, 0.0, 1.0, (21, 21))
        parts = [{"V": V, "F": F, "UVc": None, "tex": None, "tex_id": None, "rgb": (0, 0, 0)}]
        b1 = T.render_parts(parts, cam, frame, (21, 21), (0, 0, 21, 21))["base"]
        b4 = T.supersampled_base(parts, cam, frame, (21, 21), (0, 0, 21, 21), 4)
        self.assertEqual(b4.shape, b1.shape)
        vals = b4[..., 0][(b4[..., 0] > 1) & (b4[..., 0] < 254)]
        self.assertGreater(len(vals), 0, "edge pixels are blends of the part and the white backdrop")
        self.assertAlmostEqual(float(b4[10, 10, 0]), 0.0)


class TestColour(unittest.TestCase):
    def test_srgb_round_trip(self):
        x = np.linspace(0, 255, 256)
        np.testing.assert_allclose(T.linear_to_srgb(T.srgb_to_linear(x)), x, atol=1e-9)

    def test_identity_gain(self):
        x = np.random.default_rng(0).uniform(0, 255, (100, 3))
        np.testing.assert_allclose(T.apply_gain(x, np.ones(3), np.zeros(3)), x, atol=1e-9)

    def test_de00_zero_on_equal_colours(self):
        c = np.array([[10, 50, 200], [240, 240, 240]], float)
        np.testing.assert_allclose(T.de00(c, c), 0, atol=1e-9)
        self.assertAlmostEqual(T.mean_colour_de00(c, c), 0.0, places=9)


class TestQuantileGain(unittest.TestCase):
    def setUp(self):
        self.gen = np.random.default_rng(1).uniform(20, 200, (5000, 3))

    def _photo(self, g, o):
        return T.linear_to_srgb(T.srgb_to_linear(self.gen) * np.asarray(g) + np.asarray(o))

    def test_recovers_pure_gain(self):
        g = np.array([1.3, 0.9, 0.6])
        gain, off, info = T.quantile_gain(self.gen, self._photo(g, 0.0))
        np.testing.assert_allclose(gain, g, rtol=0.02)
        np.testing.assert_allclose(off, 0.0, atol=2e-3)

    def test_recovers_lifted_black_level(self):
        gain, off, _ = T.quantile_gain(self.gen, self._photo([0.7, 0.7, 0.7], [0.03, 0.02, 0.01]))
        np.testing.assert_allclose(gain, 0.7, rtol=0.03)
        np.testing.assert_allclose(off, [0.03, 0.02, 0.01], atol=3e-3)

    def test_wider_photo_spread_stays_gain_only_and_matches_median(self):
        # photo = 2 gen - 0.02 (sheen widens the spread): the negative offset is not allowed
        photo = self._photo([2.0] * 3, [-0.02] * 3)
        gain, off, _ = T.quantile_gain(self.gen, photo)
        np.testing.assert_array_equal(off, 0.0)
        gl, pl = T.srgb_to_linear(self.gen), T.srgb_to_linear(photo)
        np.testing.assert_allclose(gain * np.median(gl, 0), np.median(pl, 0), rtol=1e-9)
        self.assertTrue(np.all(T.apply_gain(np.zeros((1, 3)), gain, off) >= 0))

    def test_gain_bounds(self):
        gain, off, _ = T.quantile_gain(self.gen, self._photo([9.0] * 3, 0.0))
        self.assertTrue(np.all(gain <= T.GAIN_BOUNDS[1] + 1e-12))
        gain, off, _ = T.quantile_gain(self.gen, self._photo([0.05] * 3, 0.0))
        self.assertTrue(np.all(gain >= T.GAIN_BOUNDS[0] - 1e-12))

    def test_too_few_pairs(self):
        gain, off, info = T.quantile_gain(self.gen[:10], self.gen[:10])
        np.testing.assert_array_equal(gain, 1.0)
        self.assertEqual(info.get("skipped"), "too_few_pairs")


class TestRasterFill(unittest.TestCase):
    def test_rasterize_barycentric_reconstructs_texel_centres(self):
        tri = np.array([[[0, 0], [10, 0], [10, 10]], [[0, 0], [10, 10], [0, 10]]], float)
        fid, bary = T.rasterize_triangles(tri, (12, 12))
        self.assertTrue((fid[:10, :10] >= 0).all())
        self.assertTrue((fid[10:, :] < 0).all() and (fid[:, 10:] < 0).all())
        ii, jj = np.nonzero(fid >= 0)
        b = bary[ii, jj].astype(float)
        np.testing.assert_allclose(b.sum(1), 1.0, atol=1e-6)
        rec = np.einsum("kc,kcd->kd", b, tri[fid[ii, jj]])
        np.testing.assert_allclose(rec, np.column_stack([jj + 0.5, ii + 0.5]), atol=1e-5)

    def test_nearest_fill(self):
        img = np.zeros((5, 5, 3), np.uint8)
        img[2, 2] = (10, 20, 30)
        valid = np.zeros((5, 5), bool)
        valid[2, 2] = True
        out, dist = T.nearest_fill(img, valid)
        self.assertTrue((out == (10, 20, 30)).all())
        self.assertAlmostEqual(float(dist[0, 0]), np.hypot(2, 2))

    def test_erode_by_component_keeps_thin_parts(self):
        m = np.zeros((40, 40), bool)
        m[5:25, 5:25] = True          # blob: eroded normally
        m[30, 5:35] = True            # 1 px wire: would vanish, keeps its r = 0 erosion
        e = T.erode_by_component(m, 2)
        self.assertEqual(int(e[5:25, 5:25].sum()), 16 * 16)
        self.assertTrue(e[30, 5:35].all())

    def test_sample_bilinear_pixel_centres(self):
        img = np.arange(12, dtype=np.float32).reshape(3, 4, 1)
        v = T.sample_bilinear(img, np.array([1.0, 1.5]), np.array([1.0, 1.0]))
        np.testing.assert_allclose(v.ravel(), [5.0, 5.5])


class TestLayouts(unittest.TestCase):
    def test_boundary_loops(self):
        V, F, region, P2, T2 = annulus_frame()
        loops = T.boundary_loops(T2)
        self.assertEqual(sorted(len(l) for l in loops), [4, 4])
        for loop in loops:                      # each loop closes
            self.assertEqual(loop[0][0], loop[-1][1])

    def test_wall_layout_every_wall_face_mapped_and_isometric(self):
        V, F, region, P2, T2 = annulus_frame(thickness=3.0)
        n = len(P2)
        wl = T.wall_layout(V, T2, n, px_per_mm=10.0)
        self.assertEqual(wl["loops"], 2)
        self.assertEqual(sorted(wl["loop_lengths_mm"]), [80.0, 160.0])
        wall = np.nonzero(region == 2)[0]
        tri, bad = T.wall_corner_texels(F, wall, n, wl)
        self.assertEqual(bad, 0)
        H, W = wl["shape"]
        self.assertTrue(np.isfinite(tri).all())
        self.assertTrue((tri[..., 0] >= 0).all() and (tri[..., 0] <= W).all())
        self.assertTrue((tri[..., 1] >= 0).all() and (tri[..., 1] <= H).all())
        # texel distances equal model distances x density (strips are unrolled isometrically)
        d = wl["px_per_mm"]
        for k, f in enumerate(wall):
            for i, j in ((0, 1), (1, 2), (2, 0)):
                dm = np.linalg.norm(V[F[f, i]] - V[F[f, j]])
                dt = np.linalg.norm(tri[k, i] - tri[k, j])
                self.assertAlmostEqual(dt, dm * d, places=6)
        # texel -> 3D points lie on the walls (inside the side planes of the annulus, between the caps)
        fid, bary = T.rasterize_triangles(tri, (H, W))
        cov = fid >= 0
        P = np.einsum("kc,kcd->kd", bary[cov].astype(float), V[F[wall[fid[cov]]]])
        on_side = np.min(np.abs(np.stack([P[:, 0], P[:, 0] - 40, P[:, 0] - 10, P[:, 0] - 30,
                                          P[:, 1], P[:, 1] - 40, P[:, 1] - 10, P[:, 1] - 30], 1)), 1)
        self.assertLess(float(on_side.max()), 1e-4)      # float32 barycentrics
        self.assertTrue(((P[:, 2] <= 1e-9) & (P[:, 2] >= -3 - 1e-9)).all())

    def test_wall_layout_shrinks_density_to_fit(self):
        V, F, region, P2, T2 = annulus_frame(thickness=3.0)
        wl = T.wall_layout(V, T2, len(P2), max_px=256, px_per_mm=50.0)
        self.assertLessEqual(wl["shape"][0], 256)
        self.assertLess(wl["px_per_mm"], 50.0)

    def test_cap_layout_maps_pixel_centres_to_texel_centres(self):
        P2 = np.array([[100.0, 50.0], [300.0, 50.0], [300.0, 150.0]])
        lay = T.cap_layout(P2, margin=4)
        self.assertEqual(lay["k"], 1.0)
        t = T.cap_texel(np.array([lay["origin"]]), lay)
        np.testing.assert_allclose(t, [[0.5, 0.5]])
        big = T.cap_layout(np.array([[0.0, 0.0], [5000.0, 100.0]]), margin=0, max_px=1000)
        self.assertLessEqual(max(big["shape"]), 1000)


class TestMasks(unittest.TestCase):
    def _scene(self):
        img = np.zeros((60, 60, 3), np.uint8)
        img[:] = (200, 60, 40)                         # frame colour
        lens = np.zeros((60, 60), bool)
        lens[:, :20] = True
        img[lens] = (60, 60, 200)                       # lens colour
        img[10:14, 20:23] = (60, 60, 200)               # lens colour leaking in at the lens edge
        img[40:43, 45:48] = (60, 60, 200)               # same colour, isolated inside the frame
        cap = ~lens
        return img, cap, lens

    def test_lens_like_connected_to_the_lens_only(self):
        img, cap, lens = self._scene()
        ref_frame = cap.copy()
        ref_frame[:, :30] = False
        m_all, _ = T.lens_like_mask(img, cap, lens, ref_frame)
        self.assertTrue(m_all[41, 46] and m_all[12, 21])
        m, info = T.lens_like_mask(img, cap, lens, ref_frame, lens_region=lens, band_px=10.0)
        self.assertTrue(m[12, 21])
        self.assertFalse(m[41, 46])
        self.assertEqual(info["lens_like_px"], 12)

    def test_backdrop_like(self):
        img = np.full((20, 20, 3), 40, np.uint8)
        img[:2] = 250
        region = np.ones((20, 20), bool)
        m, info = T.backdrop_like(img, region, [255, 255, 255])
        self.assertEqual(int(m.sum()), 40)
        img[:] = 252                                     # a frame in the backdrop colour: nothing removed
        m, info = T.backdrop_like(img, region, [255, 255, 255])
        self.assertFalse(m.any())
        self.assertTrue(info["skipped_frame_is_backdrop_coloured"])

    def test_inpaint_suspects_uses_only_good_neighbours(self):
        tex = np.zeros((20, 20, 3), np.float32)
        covered = np.zeros((20, 20), bool)
        covered[5:15, 5:15] = True
        tex[covered] = (40, 50, 60)
        tex[9:11, 9:11] = (250, 250, 250)                # backdrop-coloured texels inside a chart
        out, info = T._inpaint_suspects(tex, covered, tex, None, T.lab_hist(np.array([[40, 50, 60]] * 60)),
                                        [255, 255, 255], np.array([40, 50, 60]), covered)
        self.assertEqual(info["backdrop_like_texels"], 4)
        np.testing.assert_allclose(out[9:11, 9:11].reshape(-1, 3), [[40, 50, 60]] * 4, atol=1.5)
        np.testing.assert_array_equal(out[~covered], 0)  # uncovered texels untouched (padded later)


class TestBaker(unittest.TestCase):
    def test_closest_point_colour_and_exclusion(self):
        # a 10 x 10 mm quad at z = 0 with uv = (x/10, 1 - y/10); a second quad at z = 1 is excluded (lens)
        V = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
                      [0, 0, 1], [10, 0, 1], [10, 10, 1], [0, 10, 1]], np.float32)
        F = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]], np.int32)
        UV = np.column_stack([V[:, 0] / 10, 1 - V[:, 1] / 10]).astype(np.float32)
        tex = np.zeros((64, 64, 3), np.uint8)
        tex[..., 0] = np.linspace(0, 255, 64)[None, :].astype(np.uint8)       # red grows with u
        gen = SimpleNamespace(V=V, F=F, UV=UV)
        bk = T.Baker(gen, np.array([False, False, True, True]), tex)
        P = np.array([[2.0, 5.0, 0.8], [7.5, 3.0, 0.9]])       # nearer to the excluded quad
        c = bk.closest(P)
        self.assertTrue(np.all(c["face"] < 2))
        np.testing.assert_allclose(c["dist"], [0.8, 0.9], atol=1e-5)
        col, dist = bk.colour(P)
        u = P[:, 0] / 10
        expect = np.interp(u * 64 - 0.5, np.arange(64), np.linspace(0, 255, 64).astype(np.uint8).astype(float))
        np.testing.assert_allclose(col[:, 0], expect, atol=1.0)


class TestRender(unittest.TestCase):
    def test_render_parts_samples_per_corner_uv(self):
        V, F, region, P2, T2 = annulus_frame()
        frame = core.NormFrame((20.0, 20.0, -1.5), 40.0)
        cam = raster.view_camera(frame, 0.0, 0.0, 4.0, (200, 200))
        texs = [np.full((4, 4, 3), c, np.uint8) for c in ((200, 0, 0), (0, 200, 0), (0, 0, 200))]
        UVc = np.full((len(F), 3, 2), 0.5)
        r = T.render_parts([{"V": V, "F": F, "UVc": UVc, "tex": texs, "tex_id": region.astype(int)}], cam, frame, (200, 200))
        hit = r["label"] == 0
        self.assertGreater(int(hit.sum()), 1000)
        # from the front only the front cap (red) is visible; the hole is empty
        np.testing.assert_allclose(r["base"][hit].mean(0), [200, 0, 0], atol=1e-3)
        self.assertEqual(int(r["label"][100, 100]), -1)

    def test_colour_metrics(self):
        a = np.full((10, 10, 3), 100, np.uint8)
        m = np.ones((10, 10), bool)
        self.assertIsNone(T.colour_metrics(a, a, np.zeros_like(m)))
        res = T.colour_metrics(a, a.astype(np.float32), m)
        self.assertEqual(res["de00_mean_colour"], 0.0)


class TestArCalibrationMaths(unittest.TestCase):
    """The AR material fit's building blocks (the harness itself is exercised by the real S7 run)."""

    def test_aces_inverse_round_trip(self):
        x = np.random.default_rng(0).uniform(0, 2.5, (2000, 3))
        y = T.aces_filmic(x)
        ok = ((y < 0.999) & (y > 1e-4)).all(1)             # below the clip at both ends
        np.testing.assert_allclose(T.aces_filmic_inverse(y[ok]), x[ok], atol=1e-9)
        self.assertTrue(((y >= 0) & (y <= 1)).all())

    def test_gain_synthesis_is_exact_for_an_affine_scene(self):
        # the render for gain g is L0 + g (L1 - L0) before tone mapping; decode the 8-bit renders, synthesise g
        rng = np.random.default_rng(1)
        spec, diff = rng.uniform(0.02, 0.2, (500, 3)), rng.uniform(0, 0.6, (500, 3))   # dark but unclipped
        enc = lambda L: np.round(T.linear_to_srgb(T.aces_filmic(L)))           # noqa: E731
        L0, L1 = (T.aces_filmic_inverse(T.srgb_to_linear(enc(L))) for L in (spec, spec + diff))
        g = 0.4
        synth = T.linear_to_srgb(T.aces_filmic(L0 + g * (L1 - L0)))
        self.assertLess(np.abs(synth - enc(spec + g * diff)).max(), 1.6)       # 8-bit quantisation only

    def test_slab_distance(self):
        rng = np.random.default_rng(2)
        lab = np.column_stack([rng.uniform(10, 60, 3000), rng.normal(0, 2, 3000), rng.normal(-8, 2, 3000)])
        self.assertAlmostEqual(T.slab_distance(lab, lab), 0.0, places=9)
        self.assertAlmostEqual(T.slab_distance(lab, lab[::-1].copy()), 0.0, places=9)   # order-free
        brighter = lab + np.array([10.0, 0, 0])
        self.assertGreater(T.slab_distance(lab, brighter), 5.0)
        self.assertAlmostEqual(T.slab_distance(lab, brighter), T.slab_distance(brighter, lab), places=9)

    def test_glb_patch_changes_factors_only(self):
        from bsa import export
        import tempfile
        parts, mats = export.synthetic_parts()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.glb"
            export.write_glb(parts, mats, p)
            data = p.read_bytes()
        patched = T.glb_patch_materials(data, {"frame": {"pbr": {"roughnessFactor": 0.9, "baseColorFactor": [0.5, 0.5, 0.5, 1.0]}},
                                               "lens": {"top": {"emissiveFactor": [0.3, 0.3, 0.3]}}})
        d0, b0 = T.glb_split(data)
        d1, b1 = T.glb_split(patched)
        self.assertEqual(b0, b1)                                    # geometry and textures untouched
        m = {x["name"]: x for x in d1["materials"]}
        self.assertEqual(m["frame"]["pbrMetallicRoughness"]["roughnessFactor"], 0.9)
        self.assertEqual(m["frame"]["pbrMetallicRoughness"]["baseColorFactor"], [0.5, 0.5, 0.5, 1.0])
        self.assertIn("baseColorTexture", m["frame"]["pbrMetallicRoughness"])
        self.assertEqual(m["lens"]["emissiveFactor"], [0.3, 0.3, 0.3])
        self.assertEqual(m["temple"], {x["name"]: x for x in d0["materials"]}["temple"])
        prims = T.glb_primitives(patched)
        self.assertEqual([p["node"] for p in prims], ["frame", "temple_R", "temple_L", "lens_R", "lens_L"])
        self.assertLess(np.abs(prims[0]["V"]).max(), 0.2)            # metres

    def test_ar_label_and_projection(self):
        # a camera at z = +0.5 m looking down -Z (three.js conventions: identity view, perspective projection)
        W, H, f = 200, 100, 1.0 / np.tan(np.radians(20))
        n_, fa = 0.01, 10.0
        P = np.array([[f * H / W, 0, 0, 0], [0, f, 0, 0], [0, 0, -(fa + n_) / (fa - n_), -2 * fa * n_ / (fa - n_)], [0, 0, -1, 0]])
        A2W = np.eye(4)
        A2W[2, 3] = -0.5
        meta = {"P": P, "V": np.eye(4), "A2W": A2W, "W": W, "H": H}
        tri_near = {"node": "frame", "V": np.array([[-0.05, -0.05, 0.0], [0.05, -0.05, 0.0], [0.0, 0.05, 0.0]]), "F": np.array([[0, 1, 2]])}
        tri_far = {"node": "lens", "V": tri_near["V"] * 2 + [0, 0, -0.1], "F": np.array([[0, 1, 2]])}
        lab = T.ar_label(meta, [tri_near, tri_far])
        c = T.ar_project(meta, np.array([[0.0, -0.01, 0.0]]))[0]
        self.assertAlmostEqual(c[0], W / 2, places=6)
        self.assertEqual(lab["first"][int(c[1]), int(c[0])], 0)
        self.assertEqual(lab["second"][int(c[1]), int(c[0])], 1)
        np.testing.assert_allclose(lab["points"][int(c[1]), int(c[0])][2], 0.0, atol=1e-5)
        self.assertEqual(lab["first"][0, 0], -1)
        # px/mm at the origin: f * (H/2) / 0.5 m per metre
        self.assertAlmostEqual(T.ar_px_per_mm(meta), f * H / 2 / 0.5 / 1000, places=3)

    def test_backdrop_mix_mask_only_at_the_matte_edge(self):
        img = np.full((40, 60, 3), 255, np.uint8)
        img[10:30, 10:50] = (30, 40, 70)                 # navy frame
        img[10, 10:50] = (150, 155, 170)                 # a half-backdrop anti-aliased top edge
        img[20, 25] = (200, 200, 205)                    # an interior highlight (not at the edge)
        region = np.zeros((40, 60), bool)
        region[10:30, 10:50] = True
        outside = ~region
        mix, info = T.backdrop_mix_mask(img, region, outside, (255, 255, 255), (30, 40, 70))
        self.assertTrue(mix[10, 12:48].all())
        self.assertFalse(mix[20, 25])
        self.assertFalse(mix[15:25, 15:45].any())
        _, info2 = T.backdrop_mix_mask(img, region, outside, (255, 255, 255), (250, 250, 250))
        self.assertTrue(info2.get("skipped_frame_near_backdrop"))


class TestWallsAndClasses(unittest.TestCase):
    """Photo-visible walls, cap extrusion (frame evidence for walls), lens-edge blends, material classes."""

    def test_cap_extrusion_blends_front_and_back_caps_along_the_depth(self):
        V, F, region, P2, T2 = annulus_frame()
        n = len(P2)
        front = np.zeros((8, 8, 3), np.float32)
        front[:] = (200, 0, 0)
        back = np.zeros((8, 8, 3), np.float32)
        back[:] = (0, 0, 200)
        capuv = np.full((n, 2), 0.5)
        wall = np.nonzero(region == 2)[0][:1]
        Fw = F[wall]
        # a texel on the front edge, one on the back edge, one half way
        front_v = (Fw[0] < n).astype(float)
        b_front = front_v / front_v.sum()
        b_back = (1 - front_v) / (1 - front_v).sum()
        B = np.stack([b_front, b_back, 0.5 * (b_front + b_back)])
        col = T.cap_extrusion(np.repeat(Fw, 3, axis=0), B, n, capuv, front, back)
        np.testing.assert_allclose(col[0], (200, 0, 0), atol=1e-4)
        np.testing.assert_allclose(col[1], (0, 0, 200), atol=1e-4)
        np.testing.assert_allclose(col[2], (100, 0, 100), atol=1e-4)

    def test_inpaint_suspects_takes_frame_evidence_for_lens_coloured_texels(self):
        tex = np.zeros((10, 10, 3), np.float32)
        tex[:] = (30, 40, 70)
        raw = tex.copy()
        raw[4:6, 4:6] = (150, 210, 235)                       # the bake is lens-coloured here
        h_lens = T.lab_hist(np.tile([[150, 210, 235]], (100, 1)))
        h_frame = T.lab_hist(np.tile([[30, 40, 70]], (100, 1)))
        ev = np.zeros_like(tex)
        ev[:] = (11, 22, 33)
        cand = np.ones((10, 10), bool)
        out, info = T._inpaint_suspects(tex, cand, raw, h_lens, h_frame, (255, 255, 255), (30, 40, 70), cand, evidence=ev)
        self.assertEqual(info["frame_evidence_texels"], 4)
        np.testing.assert_allclose(out[4:6, 4:6].reshape(-1, 3), [[11, 22, 33]] * 4)
        np.testing.assert_allclose(out[0, 0], (30, 40, 70))

    def test_visible_points_facing_and_first_hit(self):
        frame = core.NormFrame((0.0, 0.0, 0.0), 100.0)
        cam = raster.view_camera(frame, 0.0, 0.0, 4.0, (200, 300))
        # a plate at z = 0 (facing +Z) and a blocker 5 mm in front of its left half
        Vp = np.array([[-20, -10, 0], [20, -10, 0], [20, 10, 0], [-20, 10, 0]], float)
        Vb = np.array([[-20, -10, 5], [0, -10, 5], [0, 10, 5], [-20, 10, 5]], float)
        V = np.vstack([Vp, Vb])
        F = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
        scene = raster.get_scene(V, F, frame)
        P = np.array([[10.0, 0, 0], [-10.0, 0, 0], [10.0, 0, 0]])
        N = np.array([[0, 0, 1.0], [0, 0, 1.0], [0, 0, -1.0]])
        vis, uv = T.visible_points(P, N, cam, frame, scene, (200, 300))
        self.assertEqual(vis.tolist(), [True, False, False])       # seen; behind the blocker; facing away

    def test_material_classes_cleans_streaks_by_colour(self):
        H, W = 40, 80
        tex = np.zeros((H, W, 3), np.uint8)
        tex[:, :40] = (225, 190, 120)                         # gold
        tex[:, 40:] = (110, 50, 25)                           # tortoise brown
        orm = np.zeros((H, W, 3), np.uint8)
        orm[:, :40, 2] = 255
        orm[10:14, 5:35, 2] = 0                               # a dielectric streak along the gold arm (map noise)
        used = np.ones((H, W), bool)
        cls, info = T.material_classes(orm, tex, used)
        self.assertTrue((cls[:, :38] == 1).all())
        self.assertTrue((cls[:, 42:] == 0).all())
        self.assertGreater(info["flipped_by_colour"], 0)

    def test_mr_texture_and_class_gain(self):
        cls = np.array([[0, 1]], np.uint8)
        mr = T.mr_texture(cls, (0.0, 1.0), (0.8, 0.4))
        self.assertEqual(mr[0, 0].tolist(), [255, 204, 0])
        self.assertEqual(mr[0, 1].tolist(), [255, 102, 255])
        tex = np.full((1, 2, 3), 200.0)
        out = T.bake_class_gain(tex, cls, {0: [0.5, 0.5, 0.5], 1: [1.0, 1.0, 1.0]})
        np.testing.assert_allclose(out[0, 1], 200.0, atol=1e-3)
        np.testing.assert_allclose(T.srgb_to_linear(out[0, 0]), 0.5 * T.srgb_to_linear(200.0), atol=1e-6)

    def test_hit_uv_barycentric(self):
        prim = {"V": np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float), "F": np.array([[0, 1, 2]]),
                "UV": np.array([[0, 0], [1, 0], [0, 1]], float)}
        uv = T.hit_uv(prim, np.array([0, 0]), np.array([[0.25, 0.5, 0.0], [0.0, 0.0, 0.0]]))
        np.testing.assert_allclose(uv, [[0.25, 0.5], [0.0, 0.0]], atol=1e-12)

    def test_pixel_classes_uses_the_class_map_on_textured_parts(self):
        prims = [{"material": "frame_front", "V": np.zeros((3, 3)), "F": np.array([[0, 1, 2]])},
                 {"material": "temple_R", "V": np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float),
                  "F": np.array([[0, 1, 2]]), "UV": np.array([[0.1, 0.5], [0.9, 0.5], [0.1, 0.5]])}]
        lab = {"first": np.array([[0, 1, 1]]), "face": np.array([[0, 0, 0]]),
               "points": np.array([[[0, 0, 0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], float)}
        cm = np.zeros((4, 4), np.uint8)
        cm[:, 2:] = 1
        out = T.pixel_classes(lab, np.ones((1, 3), bool), prims,
                              {"texture_class": cm, "textured": ["temple_R"], "frame_class": 1})
        self.assertEqual(out.tolist(), [1, 0, 1])              # frame -> its class; temple uv 0.1 -> 0, 0.9 -> 1


class TestRealArtifacts(unittest.TestCase):
    """Smoke test of the m1 run artifacts (does not recompute the stage)."""

    def test_m1_material_fit(self):
        from bsa.core import stage_dir, run_dir
        done = [p for p in core.PRODUCTS if (run_dir("m1", p) / T.STAGE / "result.json").exists()]
        fitted = [p for p in done if (stage_dir("m1", p, T.STAGE).load()[0].get("material_fit") or {}).get("ok")]
        if not fitted:
            self.skipTest("no fitted S7 m1 artifacts")
        # every S7 run fitted its material: a failed fit silently falls back to the old ORM rule (Miu, 2026-09-24:
        # "no usable front view" after the S5/S6 donor rework, unnoticed because only fitted products were checked)
        self.assertEqual(sorted(fitted), sorted(done))
        for p in fitted:
            with self.subTest(product=p):
                r = stage_dir("m1", p, T.STAGE).load()[0]
                f = r["material_fit"]
                c = f["chosen"]
                self.assertIn(c["roughness"], T.AR_FIT_ROUGHNESS)
                allowed_m = T.AR_FIT_METALLIC["metal"] if f.get("split") else T.AR_FIT_METALLIC[r["material_class"]["class"]]
                self.assertIn(c["metallic"], allowed_m)
                gain = np.broadcast_to(np.asarray(c["gain"], float), (3,))       # per channel since 2026-09-24
                self.assertTrue(np.all((T.AR_FIT_GAIN[0] - 1e-9 <= gain) & (gain <= T.AR_FIT_GAIN[1] + 1e-9)))
                self.assertLessEqual(c["objective"], f["before_old_rule"]["objective"])
                pc = f["per_channel_gain"]
                self.assertLessEqual(pc["objective"], pc["objective_scalar_gain"] + 1e-9)
                self.assertNotIn("angled", f["views"])
                self.assertTrue(f["views"])
                self.assertEqual("front" in f.get("views_unusable", []), "ar_fit_front_view_unusable" in r["flags"])
                fac = r["materials"]["frame_front"]["factors"]
                self.assertEqual(fac["roughness"], c["roughness"])
                self.assertEqual(fac["base_color"][:3], [round(float(x), 4) for x in gain])
                frame_mats = [m for m in r["materials"].values() if m["part"] == "frame"]
                self.assertTrue(all(m["factors"] == fac for m in frame_mats))
                temple_mats = [m for m in r["materials"].values() if m["part"] != "frame"]
                self.assertEqual(bool(f.get("split")), bool((r.get("material_classes") or {}).get("split")))
                if f.get("split"):
                    # two materials on the temples: metallic/roughness per texel (the texture), each class's gain
                    # baked into the base colour; a dielectric class is never metallic
                    self.assertEqual(f["class_factors"]["dielectric"]["metallic"], 0.0)
                    for m in temple_mats:
                        self.assertEqual(m["metallic_roughness_texture"], "temple_mr.png")
                        self.assertEqual(m["factors"], {"metallic": 1.0, "roughness": 1.0, "base_color": [1.0, 1.0, 1.0, 1.0]})
                    self.assertTrue((stage_dir("m1", p, T.STAGE).root / "temple_mr.png").exists())
                else:
                    self.assertTrue(all(m["factors"] == fac and "metallic_roughness_texture" not in m for m in temple_mats))

    def test_m1_artifacts(self):
        from bsa.core import stage_dir, run_dir
        done = [p for p in core.PRODUCTS if (run_dir("m1", p) / T.STAGE / "result.json").exists()]
        if not done:
            self.skipTest("no S7 m1 artifacts")
        from bsa import export
        for p in done:
            with self.subTest(product=p):
                sd = stage_dir("m1", p, T.STAGE)
                r, a = sd.load()
                r6, a6 = stage_dir("m1", p, "s6_assembly").load()
                self.assertEqual(a["frame_UV"].shape, (len(a6["frame_F"]), 3, 2))
                self.assertTrue(np.isfinite(a["frame_UV"]).all())
                self.assertTrue((a["frame_UV"] >= 0).all() and (a["frame_UV"] <= 1).all())
                for name in ("frame_front", "frame_back", "frame_wall"):
                    m = r["materials"][name]
                    self.assertEqual(m["part"], "frame")
                    self.assertTrue((sd.root / m["texture"]).exists())
                    self.assertEqual(m["uv"], "frame_UV")
                for s in ("R", "L"):
                    self.assertIn(f"temple_{s}_UV", a)
                self.assertEqual(r["walls"]["faces_unmatched"], 0)
                self.assertEqual(r["back"]["back_faces_matched"], r["back"]["front_triangles"])
                # fit views only: S7 never renders or measures the held-out view (it used to show it on its sheet)
                self.assertEqual(set(r["colour_difference"]), set(core.FIT_VIEWS))
                parts, mats, origin, notes = export.gather_inputs(p, "m1")
                self.assertEqual(notes, [])
                for name, m in r["materials"].items():            # S9 carries S7's metallicRoughness texture
                    self.assertEqual("metallic_roughness_texture" in mats[name], "metallic_roughness_texture" in m)
                # a wall texel the front photo sees takes the front cap's colour (1), any other is baked (3); none on a
                # front photo too coarse to resolve a wall
                ws = a["wall_source"]
                self.assertTrue(set(np.unique(ws).tolist()) <= {0, 1, 3})
                if "front_low_resolution" in r["flags"]:
                    self.assertFalse((ws == 1).any())
                self.assertEqual(parts["frame"]["material"], ["frame_front", "frame_back", "frame_wall"])
                self.assertTrue((np.asarray(parts["frame"]["face_material"]) >= 0).all())
                self.assertEqual(np.asarray(parts["frame"]["UV"]).shape, (len(a6["frame_F"]), 3, 2))
            # the front cap IS the front photo: its colour must reproduce there. Its own subTest, after the structural
            # checks, so a known colour failure never hides a structural regression (review finding: invu's 1.84
            # stopped the rest of its checks). The 1.5 limit is the owner's and is kept as is.
            with self.subTest(product=p, check="front_cap_dE00"):
                self.assertLess(r["colour_difference"]["front"]["s7"]["frame"]["de00_mean_colour"], 1.5)


if __name__ == "__main__":
    unittest.main()
