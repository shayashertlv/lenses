"""S6 assembly: synthetic fixtures (vectorising, triangulation, solids, lens growth, a full synthetic front with
seam and containment checks) plus a real-data smoke test on the saved m1 artifacts (skips when missing)."""
import json
import unittest

import cv2
import numpy as np
import shapely
from shapely.geometry import Polygon

from bsa import assemble, core, depth, raster
from bsa.core import NormFrame
from _meshes import box

FRAME = NormFrame((0.0, 0.0, 60.0), 140.0)


def _ellipse(cx, cy, a, b, n=400):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([cx + a * np.cos(t), cy + b * np.sin(t)], 1)


def _raster(poly, shape):
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.round(poly * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    return m.astype(bool)


class TwoD(unittest.TestCase):
    def test_mask_to_geometry_keeps_holes_and_area(self):
        yy, xx = np.mgrid[0:200, 0:300]
        m = ((xx - 150) ** 2 / 120 ** 2 + (yy - 100) ** 2 / 80 ** 2 <= 1) & \
            ~((xx - 150) ** 2 / 60 ** 2 + (yy - 100) ** 2 / 40 ** 2 <= 1)
        g = assemble.mask_to_geometry(m)
        self.assertIsInstance(g, Polygon)
        self.assertEqual(len(g.interiors), 1)
        self.assertAlmostEqual(g.area / m.sum(), 1.0, delta=0.01)

    def test_cdt_refine_and_orientation(self):
        outer = Polygon(_ellipse(0, 0, 100, 60, 120), [_ellipse(0, 0, 50, 30, 200)[::-1]])
        P, T = assemble.triangulate_cdt(outer)
        T = assemble.orient_triangles(P, T, -1.0)
        area = lambda P, T: 0.5 * ((P[T[:, 1], 0] - P[T[:, 0], 0]) * (P[T[:, 2], 1] - P[T[:, 0], 1])
                                  - (P[T[:, 2], 0] - P[T[:, 0], 0]) * (P[T[:, 1], 1] - P[T[:, 0], 1]))
        self.assertAlmostEqual(-area(P, T).sum(), outer.area, delta=1e-6 * outer.area)
        P2, T2 = assemble.refine_long_edges(P, T, 8.0)
        self.assertTrue(np.all(area(P2, T2) < 0))
        self.assertAlmostEqual(-area(P2, T2).sum(), outer.area, delta=1e-6 * outer.area)
        E = np.concatenate([T2[:, [0, 1]], T2[:, [1, 2]], T2[:, [2, 0]]])
        L = np.linalg.norm(P2[E[:, 0]] - P2[E[:, 1]], axis=1)
        self.assertLessEqual(L.max(), 8.0 + 1e-9)

    def test_interior_triangulation_keeps_boundary(self):
        poly = Polygon(_ellipse(0, 0, 40, 25, 300))
        P, T = assemble.triangulate_with_interior(poly, 0.8, 3.0)
        B = assemble.boundary_edges(T)
        self.assertEqual(len(B), 300)
        self.assertGreater(len(P), 300 + 50)          # interior points exist
        c = P[T].mean(axis=1)
        self.assertTrue(shapely.contains_xy(poly, c[:, 0], c[:, 1]).all())

    def test_solid_is_watertight_with_right_volume(self):
        poly = Polygon(_ellipse(0, 0, 30, 20, 200))
        P, T = assemble.triangulate_with_interior(poly, 1.0, 3.0)
        T = assemble.orient_triangles(P, T, -1.0)
        # pixel (u, v) -> model (x = u, y = -v): negative (u, v) area is counter-clockwise in (x, y)
        front = np.c_[P[:, 0], -P[:, 1], np.full(len(P), 5.0)]
        back = front - [0.0, 0.0, 3.0]
        V, F, region = assemble.solid_from_cap(front, back, T)
        topo = assemble.mesh_topology(F, len(V))
        self.assertTrue(topo["watertight"], topo)
        self.assertAlmostEqual(assemble.signed_volume(V, F), 3.0 * poly.area, delta=0.01 * poly.area)
        self.assertEqual(set(np.unique(region)), {0, 1, 2})
        broken = assemble.mesh_topology(F[1:])
        self.assertFalse(broken["watertight"])

    def test_grow_lens_frame_bounded_only(self):
        ring = _ellipse(0, 0, 100, 60, 400)
        types = np.zeros(400, np.int8)
        types[(ring[:, 1] > 20)] = assemble.TYPE_FREE           # the bottom (v down) is a free edge
        rimw = np.full(400, 30.0)
        clip = Polygon(_ellipse(0, 0, 140, 100, 400))
        grown, g = assemble.grow_lens(ring, types, rimw, 0.1, 1.5, clip)
        d = shapely.distance(shapely.points(ring), grown.exterior)
        fb_far = (types == 0) & (np.abs(ring[:, 1]) < 5) & (ring[:, 0] > 0)
        self.assertTrue(np.all(d[fb_far] >= 0.6 / 0.1 - 0.5))   # >= 0.6 mm (= 6 px here) on the rim
        self.assertAlmostEqual(float(np.median(g[fb_far])), max(0.6, 0.35 * 3.0), delta=0.05)
        free_mid = (types == assemble.TYPE_FREE) & (np.abs(ring[:, 0]) < 40)
        self.assertLess(float(d[free_mid].max()), 1e-6)            # exact on free edges
        self.assertTrue(clip.buffer(1e-6).contains(grown))

    def test_grow_lens_tucks_free_edges_under_material(self):
        ring = _ellipse(0, 0, 100, 60, 400)
        types = np.full(400, assemble.TYPE_FREE, np.int8)
        rimw = np.zeros(400)
        material = np.zeros((300, 400), bool)
        material[:, 250:] = True                                   # photo material beyond the right end only
        ring_img = ring + [200, 150]
        clip = Polygon(_ellipse(200, 150, 140, 100, 400))
        grown, g = assemble.grow_lens(ring_img, types, rimw, 0.1, 1.5, clip, material)
        d = shapely.distance(shapely.points(ring_img), grown.exterior)
        right = ring_img[:, 0] > 290                                  # edge meeting material: tucked 0.3 mm (3 px)
        left = ring_img[:, 0] < 150                                   # edge over backdrop: exact
        self.assertTrue(np.all(np.abs(g[right] - assemble.TUCK_FREE_MM) < 1e-9))
        self.assertGreater(float(np.median(d[right])), 0.8 * assemble.TUCK_FREE_MM / 0.1)
        self.assertLess(float(d[left].max()), 1e-6)
        self.assertTrue(np.all(g[left] == 0))

    def test_grow_lens_stops_at_backdrop(self):
        """The tuck march is blocked by the first backdrop pixel of the matte: material seen beyond a measured gap
        (a shield's brow vent) is not reached, while material the edge touches through matte (its own halo) is."""
        ring = _ellipse(0, 0, 100, 60, 400) + [200, 150]
        types = np.full(400, assemble.TYPE_FREE, np.int8)
        rimw = np.zeros(400)
        clip = Polygon(_ellipse(200, 150, 140, 100, 400))
        material = np.zeros((300, 400), bool)
        material[:, 312:] = True                       # material 12 px (1.2 mm) beyond the right end
        matte = np.ones((300, 400), bool)
        matte[:, 303:312] = False                      # ... behind a 9 px backdrop gap
        _, g_gap = assemble.grow_lens(ring, types, rimw, 0.1, 1.5, clip, material, matte)
        right = ring[:, 0] > 299
        self.assertTrue(np.all(g_gap[right] == 0))
        _, g_touch = assemble.grow_lens(ring, types, rimw, 0.1, 1.5, clip, material, np.ones((300, 400), bool))
        self.assertTrue(np.all(g_touch[right] > assemble.TUCK_FREE_MM - 1e-9))

    def test_graph_smooth_keeps_an_isolated_peak_with_dilation(self):
        poly = Polygon(_ellipse(0, 0, 30, 20, 120))
        P, T = assemble.triangulate_with_interior(poly, 1.5, 3.0)
        v = np.zeros(len(P))
        k = int(np.argmin(np.hypot(P[:, 0], P[:, 1])))
        v[k] = 1.0
        plain = assemble.graph_smooth(v, T, 3)
        dil = assemble.graph_smooth(v, T, 3, 1)
        self.assertLess(plain[k], 0.5)                           # averaging alone dilutes an isolated value
        self.assertGreater(dil[k], 0.5)                          # one max round first keeps it
        self.assertLessEqual(float(dil.max()), 1.0 + 1e-12)
        np.testing.assert_allclose(assemble.graph_smooth(np.full(len(P), 2.0), T, 5, 2), 2.0)

    def test_metric_smooth_is_density_independent(self):
        """The carve / silhouette smoothing acts in millimetres: a field smoothed on a dense and on a sparse
        triangulation of the same plate agrees (the old graph average smoothed 1.5 mm at a 0.5 mm lens ring and 9 mm
        in the middle of a rim), a constant stays constant and the dilation keeps an isolated value's amplitude."""
        poly = Polygon(_ellipse(0, 0, 30, 20, 160))
        fine = assemble.triangulate_with_interior(poly, 0.5, 0.7)
        coarse = assemble.triangulate_with_interior(poly, 1.5, 3.0)
        sig, lam = 1.5, 6.0
        expect = np.exp(-0.5 * (sig / lam) ** 2)          # a Gaussian's gain at wavelength 2 pi lam
        for P, T in (fine, coarse):
            P3 = np.c_[P, np.zeros(len(P))]
            sm = assemble.metric_smooth(np.sin(P[:, 0] / lam), P3, T, sig)
            inner = shapely.distance(shapely.points(P), poly.exterior) > 3.5 * sig      # away from the boundary
            np.testing.assert_allclose(sm[inner], expect * np.sin(P[inner, 0] / lam), atol=0.03)
            np.testing.assert_allclose(assemble.metric_smooth(np.full(len(P), 2.0), P3, T, sig), 2.0)
        P, T = fine                                        # an isolated value on a 0.5-0.7 mm mesh
        P3 = np.c_[P, np.zeros(len(P))]
        v = np.zeros(len(P))
        k = int(np.argmin(np.hypot(P[:, 0], P[:, 1])))
        v[k] = 1.0
        plain = assemble.metric_smooth(v, P3, T, 1.5)[k]
        dil = assemble.metric_smooth(v, P3, T, 1.5, 1.5)[k]        # the carve's max over sigma, then the Gaussian
        self.assertLess(plain, 0.1)                                  # averaging alone dilutes an isolated carve
        self.assertGreater(dil, 0.4)                                 # the dilation keeps ~1 - exp(-1/2) of it
        self.assertGreater(dil, 5 * plain)
        self.assertAlmostEqual(assemble.CARVE_SIGMA_MM * 2 * np.pi / np.sqrt(2 * np.log(2)), depth.KNOT_MM, places=9)

    def test_harmonic_fill_max_principle(self):
        poly = Polygon(_ellipse(0, 0, 30, 20, 120))
        P, T = assemble.triangulate_with_interior(poly, 1.5, 3.0)
        B = np.unique(assemble.boundary_edges(T))
        vals = np.sin(np.arctan2(P[B, 1], P[B, 0]))
        h = assemble.harmonic_fill(T, len(P), B, vals)
        np.testing.assert_allclose(h[B], vals)
        self.assertLessEqual(h.max(), vals.max() + 1e-9)
        self.assertGreaterEqual(h.min(), vals.min() - 1e-9)


def _synthetic_scene(mirrored=False):
    """A curved front (z = 65 - 0.3 (x/30)^2) seen by a pitched perspective camera; a rounded frame with two
    elliptical lens holes drawn in pixels; flat lens plates 1.5 mm behind the front; thickness 4 mm."""
    shape = (420, 1000)
    cam = raster.view_camera(FRAME, 0.0, 8.0, 6.5, shape, perspective=0.15)
    base = depth.Base()
    front = depth.SmoothField(depth.Poly2D(((0, 0), (2, 0)), 0.0, 0.0, 30.0), np.array([65.0, -0.3]))
    s_axis = np.arange(-100, 100.01, 0.5)
    y_axis = np.arange(60, -60.01, -0.5)
    thick = np.full((len(y_axis), len(s_axis)), 4.0)
    lens_poly = depth.Poly2D(((0, 0), (2, 0)), 0.0, 0.0, 30.0)
    lenses = [depth.LensModel(1, "R", lens_poly, np.array([63.5, -0.3]), "quartic"),
              depth.LensModel(2, "L", lens_poly, np.array([63.5, -0.3]), "quartic")]
    box = ((-150.0, 150.0), (-100.0, 100.0), (0.0, 100.0))
    df = depth.DepthField(base, front, s_axis, y_axis, thick, lenses, box)
    src = depth.SourceView("front", cam, shape, False)
    outer = np.zeros(shape, np.uint8)
    cv2.rectangle(outer, (70, 70), (930, 350), 1, -1)
    polys = [_ellipse(705, 205, 190, 105), _ellipse(295, 205, 190, 105)]
    holes = _raster(polys[0], shape) | _raster(polys[1], shape)
    frame_mask = outer.astype(bool) & ~holes
    types = [np.zeros(400, np.int8), np.zeros(400, np.int8)]
    rimw = [np.full(400, 25.0), np.full(400, 25.0)]
    return df, src, frame_mask, polys, types, rimw


class SyntheticFront(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df, cls.src, cls.frame_mask, cls.polys, types, rimw = _synthetic_scene()
        cls.ppm = 6.5
        cls.b = assemble.build(cls.frame_mask, cls.polys, types, rimw, ["R", "L"], [[1, 0, 0], [0, 1, 0]],
                               cls.df, cls.src, FRAME, cls.ppm)

    def test_parts_are_watertight_and_outward(self):
        b = self.b
        topo = assemble.mesh_topology(b["frame_F"], len(b["frame_V"]))
        self.assertTrue(topo["watertight"], topo)
        self.assertGreater(assemble.signed_volume(b["frame_V"], b["frame_F"]), 0)
        for lo in b["lens_out"]:
            t = assemble.mesh_topology(lo["F"], len(lo["V"]))
            self.assertTrue(t["watertight"], t)
            self.assertGreater(assemble.signed_volume(lo["V"], lo["F"]), 0)

    def test_front_cap_projection_and_holes_exact(self):
        b = self.b
        n = len(b["fP2"])
        uv = core.project_mm(b["frame_V"][:n], self.src.camera, FRAME)
        np.testing.assert_allclose(uv, b["fQ2"], atol=1e-4)            # where each front-cap vertex really projects
        np.testing.assert_allclose(b["uv_front"][:n], b["fQ2"])
        self.assertTrue(np.all(b["uv_front"][n:] == -1))
        # front cap on the front surface
        z = self.df.front(b["frame_V"][:n, 0], b["frame_V"][:n, 1])
        np.testing.assert_allclose(b["frame_V"][:n, 2], z, atol=1e-4)
        # the hole is the unbuffered S2 polygon: every hole-ring vertex is a front-cap vertex, exact in the photo
        lens_adj = b["silhouette"]["lens_adj"]
        np.testing.assert_allclose(b["fQ2"][lens_adj], b["fP2"][lens_adj], atol=1e-9)
        for lo in b["lens_out"]:
            dist = shapely.distance(shapely.points(lo["ring"]), shapely.MultiPoint(b["fP2"][lens_adj]))
            self.assertLess(float(np.max(dist)), 1e-9)

    def test_outer_silhouette_is_the_outline(self):
        """Camera 8 deg above: the top outline is the plate's BACK edge, the bottom its FRONT edge; the solid's
        outline (front or back edge, whichever is further out) lies on the design outline everywhere."""
        b = self.b
        n = len(b["fP2"])
        verts, m = assemble.boundary_frame(b["fP2"], b["fT"])
        outer = b["silhouette"]["outer"][verts]
        v, m = verts[outer], m[outer]
        pf = core.project_mm(b["frame_V"][v], self.src.camera, FRAME)
        pb = core.project_mm(b["frame_V"][v + n], self.src.camera, FRAME)
        p0 = b["fP2"][v]
        reach = np.maximum(np.sum(m * (pf - p0), axis=1), np.sum(m * (pb - p0), axis=1))
        self.assertLess(float(np.median(np.abs(reach))), 0.02)
        # the shift is smoothed along the outline (no step where a front-edge silhouette turns into a back-edge one):
        # within a pixel at those corners
        self.assertLess(float(np.percentile(np.abs(reach), 95)), 0.5)
        self.assertLess(float(np.max(np.abs(reach))), 1.5)
        top = m[:, 1] < -0.9                                           # outward normal pointing up (v down)
        bottom = m[:, 1] > 0.9
        self.assertTrue(top.any() and bottom.any())
        shift = b["silhouette"]["shift_px"][v]
        self.assertGreater(float(np.median(shift[top])), 0.2)          # 4 mm x tan 8 deg = 0.56 mm = 3.6 px, less persp.
        self.assertLess(float(np.max(shift[bottom])), 1e-6)
        # the plate is a prism along the depth axis (-Z here) away from the lens holes
        d = b["frame_V"][v] - b["frame_V"][v + n]
        np.testing.assert_allclose(d[:, :2], 0.0, atol=1e-6)
        np.testing.assert_allclose(d[:, 2], 4.0, atol=1e-6)

    def test_zero_seam_gaps_and_containment(self):
        b = self.b
        parts = [(b["frame_V"], b["frame_F"])] + [(lo["V"], lo["F"]) for lo in b["lens_out"]]
        roi = (40, 40, 960, 380)
        for ss in (1, 3):
            seam = assemble.seam_check(parts, b["lens_out"], b["frame_geom"], self.src, FRAME, roi, ss)
            self.assertGreater(seam["band_pixels"], 1000)
            self.assertEqual(seam["gap_pixels"], 0)
            self.assertEqual(seam["uncovered_design"], 0)
        cont = assemble.containment_check(b["lens_out"], b["frame_geom"], self.src, FRAME, self.df.front_surface(),
                                          self.df, b["rim_t"])
        for c in cont:
            self.assertGreater(c["vertices"], 100)
            self.assertEqual(c["lens_front_ahead_of_frame_front"], 0, c)
            self.assertEqual(c["lens_back_behind_frame_back"], 0, c)

    def test_lens_uv_and_thickness(self):
        for lo in self.b["lens_out"]:
            self.assertGreaterEqual(lo["t"], assemble.LENS_T_RANGE_MM[0])
            self.assertLessEqual(lo["t"], assemble.LENS_T_RANGE_MM[1])
            self.assertAlmostEqual(lo["t"], 2.0, places=6)                 # half of the 4 mm rim, capped
            # lens mid-surface at mid-rim: the front at 65 - 2 + 1 on the rim, lens front 1 mm behind the frame front
            self.assertAlmostEqual(lo["info"]["placement"]["offset_mm"], 65 - 2 - 63.5, delta=0.05)
        arr = self.b["arrays"]
        for i in (1, 2):
            uv = arr[f"lens{i}_uv"]
            self.assertGreaterEqual(float(uv.min()), -1e-6)
            self.assertLessEqual(float(uv.max()), 1 + 1e-6)

    def test_back_points_axis_and_ray(self):
        P = np.array([[10.0, 5.0, 64.0], [-30.0, -10.0, 63.0]])
        px = core.project_mm(P, self.src.camera, FRAME)
        base = self.df.base
        B0 = assemble.back_points(P, px, self.src, FRAME, base, 4.0, 0.0)
        np.testing.assert_allclose(B0, P - [0.0, 0.0, 4.0], atol=1e-9)          # prism along the depth axis
        B1 = assemble.back_points(P, px, self.src, FRAME, base, 4.0, 1.0)
        np.testing.assert_allclose(core.project_mm(B1, self.src.camera, FRAME), px, atol=1e-6)   # on the ray
        np.testing.assert_allclose(P[:, 2] - B1[:, 2], 4.0, atol=1e-9)          # same depth drop

    def test_back_points_exact_ray_at_steep_incidence(self):
        """A lens-hole wall (w = 1) stays on the source ray however steep (a wrapped shield's lateral end is seen 40-70
        deg off its radial axis): beyond EXTRUDE_MAX_DEG the wall is SHORTER, never bent into the hole (the old
        direction clamp moved oakley's hole back edge up to 4.8 mm into the lens in the source view)."""
        P = np.array([[10.0, 5.0, 64.0], [-20.0, -10.0, 63.0]])
        for yaw in (60.0, 80.0):                                                        # below / beyond the 70 deg cap
            cam = raster.view_camera(FRAME, yaw, 0.0, 6.5, (420, 1000))                # orthographic
            src = depth.SourceView("front", cam, (420, 1000), False)
            px = core.project_mm(P, cam, FRAME)
            B = assemble.back_points(P, px, src, FRAME, self.df.base, 4.0, 1.0)
            np.testing.assert_allclose(core.project_mm(B, cam, FRAME), px, atol=1e-6)  # on the ray: hole exact
            L = np.linalg.norm(B - P, axis=1)
            if yaw < assemble.EXTRUDE_MAX_DEG:
                np.testing.assert_allclose(P[:, 2] - B[:, 2], 4.0, atol=1e-6)          # full depth drop
            else:
                np.testing.assert_allclose(L, 4.0 / np.cos(np.radians(assemble.EXTRUDE_MAX_DEG)), atol=1e-6)
                self.assertTrue(np.all(P[:, 2] - B[:, 2] < 4.0))                        # thinner along the axis
        B0 = assemble.back_points(P, px, src, FRAME, self.df.base, 4.0, 0.0)             # the prism is unchanged
        np.testing.assert_allclose(B0, P - [0.0, 0.0, 4.0], atol=1e-9)

    def test_carve_hold_floor(self):
        """In the band that holds a lens the carve never thins the rim below t_hold (lens + margins): the lens is
        placed in the rim as carved, and a thinner rim exposed the lens edge as notches along the vb lower rims."""
        shape = (300, 600)
        cam = raster.view_camera(FRAME, 90.0, 0.0, 4.0, shape)
        F3 = np.array([[40.0, 0.0, 65.0], [40.0, 5.0, 65.0]])
        B3 = F3 - [0.0, 0.0, 6.0]
        uv = raster._project_norm(FRAME.to_norm(np.vstack([F3, F3 - [0.0, 0.0, 1.0]])), cam)
        matte = np.zeros(shape, np.uint8)
        x0, y0 = np.floor(uv.min(0)).astype(int)
        x1, y1 = np.ceil(uv.max(0)).astype(int)
        cv2.rectangle(matte, (x0 - 20, y0 - 20), (x1, y1 + 20), 1, -1)                # only 1 mm deep in this view
        hv = assemble.HullView("left", cam, matte.astype(bool), FRAME, 0.0, 4.0)
        px = core.project_mm(F3, self.src.camera, FRAME)
        t_min = np.array([1.0, 1.0])
        _, B2, info = assemble.carve_plate(F3, B3, px, t_min, [hv], self.src, FRAME, hold=np.array([True, False]),
                                           t_hold=2.5)
        t = np.linalg.norm(B2 - F3, axis=1)
        self.assertAlmostEqual(float(t[0]), 2.5, delta=1e-6)                           # held: t_hold
        self.assertLess(float(t[1]), 2.0)                                               # free: carved toward 1 mm
        self.assertEqual(info["lens_hold_vertices"], 1)

    def test_rim_depth_as_built(self):
        b = self.b
        n = len(b["fP2"])
        rf, rb, ok = b["rim_depth"](b["fQ2"][:50])
        self.assertTrue(ok.all())
        d = self.df.base.to_param(b["frame_V"][:50])[2]
        np.testing.assert_allclose(rf, d, atol=0.2)
        self.assertTrue(np.all(rf - rb > 1.0))
        _, _, far = b["rim_depth"](np.array([[-500.0, -500.0]]))
        self.assertFalse(far[0])

    def test_carving_by_a_side_view(self):
        """A side view whose matte ends 2 mm behind the front: back points come forward to it, never below t_min;
        front points already inside stay."""
        shape = (300, 600)
        cam = raster.view_camera(FRAME, 90.0, 0.0, 4.0, shape)                  # orthographic, from +X
        F3 = np.array([[40.0, 0.0, 65.0], [40.0, 5.0, 65.0], [40.0, 10.0, 65.0]])
        B3 = F3 - [0.0, 0.0, 4.0]
        Pm = np.vstack([F3, F3 - [0.0, 0.0, 2.0]])
        uv = raster._project_norm(FRAME.to_norm(Pm), cam)
        matte = np.zeros(shape, np.uint8)
        x0, y0 = np.floor(uv.min(0)).astype(int)
        x1, y1 = np.ceil(uv.max(0)).astype(int)
        cv2.rectangle(matte, (x0 - 20, y0 - 20), (x1, y1 + 20), 1, -1)        # extends 2 mm behind the front only
        hv = assemble.HullView("left", cam, matte.astype(bool), FRAME, 0.0, 4.0)
        px = core.project_mm(F3, self.src.camera, FRAME)
        t_min = np.array([1.0, 1.0, 3.0])
        F2, B2, info = assemble.carve_plate(F3, B3, px, t_min, [hv], self.src, FRAME)
        np.testing.assert_allclose(F2, F3)
        t = np.linalg.norm(B2 - F2, axis=1)
        self.assertAlmostEqual(float(t[0]), 2.0, delta=0.3)                     # carved to the silhouette
        self.assertAlmostEqual(float(t[2]), 3.0, delta=1e-6)                    # never below t_min
        self.assertEqual(info["back_carved_vertices"], 3)

    def test_bridge_line(self):
        v = assemble._axis_bottom_v(self.b["frame_geom"], [lo["grown"] for lo in self.b["lens_out"]], 500.0)
        self.assertAlmostEqual(v, 350.0, delta=1.5)


class DonorContact(unittest.TestCase):
    """S6's final donor anchoring (the S10 integrity rule on the delivered geometry) and the plate back meeting the
    donors directly behind it. Synthetic boxes only: no saved run is read, so no skip guard."""

    _box = staticmethod(box)

    def test_donor_anchoring_snaps_or_drops(self):
        from bsa import donor, export
        fV, fF = self._box(0, 20, 0, 10, 0, 5)                                  # the plate
        arm = self._box(40, 80, 0, 5, -40, -35)                                 # the arm, far away
        near = self._box(5, 10, 2, 8, -3.7, -0.7)                               # 0.7 mm behind the plate back (z = 0)
        far = self._box(5, 10, 2, 8, -12.0, -6.0)                               # 6 mm behind it (2.3 from the near one)
        touching = self._box(12, 16, 2, 8, -4.0, 0.2)                           # into the plate
        parts = [arm, near, far, touching]
        V = np.vstack([p[0] for p in parts])
        offs = np.cumsum([0] + [len(p[0]) for p in parts[:-1]])
        F = np.vstack([p[1] + o for p, o in zip(parts, offs)])
        d0 = len(arm[1])
        a5 = {"temple_R_V": V.astype(np.float32), "temple_R_F": F.astype(np.int32),
              "donor_R_faces": np.array([d0, len(F)], np.int64)}
        arrays, info = assemble.donor_anchoring(fV, fF, [], a5)
        self.assertEqual(len(info["snapped"]), 1)
        self.assertEqual(len(info["dropped"]), 1)
        self.assertAlmostEqual(info["snapped"][0]["gap_mm"], 0.7, places=3)
        self.assertLessEqual(info["snapped"][0]["gap_after_mm"], donor.ANCHOR_GAP_MM)
        keep = arrays["donor_keep_R"]
        nn, nf = len(near[1]), len(far[1])
        self.assertTrue(keep[:d0 + nn].all() and keep[d0 + nn + nf:].all())
        self.assertFalse(keep[d0 + nn:d0 + nn + nf].any(), "the far part is dropped")
        off = arrays["donor_offset_R"]
        self.assertGreater(float(off[offs[1]:offs[2], 2].min()), 0.9, "moved 0.7 + 0.3 mm toward the plate")
        self.assertEqual(float(np.abs(off[:offs[1]]).max()), 0.0)
        TV, TF, _, how = export.temple_arrays(a5, {"R": {"accepted": True}}, "R", arrays)
        self.assertEqual(len(TF), len(F) - nf)
        self.assertEqual(how, "full")

    def test_meet_donors(self):
        F3 = np.array([[0.0, 0.0, 5.0], [10.0, 0.0, 5.0], [30.0, 0.0, 5.0]])
        B3 = F3 - np.array([0.0, 0.0, 5.0])                                     # plate back at z = 0
        pad = self._box(-2, 12, -2, 2, -6.0, -1.0)                              # donor 1 mm behind the first two points
        deep = self._box(28, 32, -2, 2, -9.0, -3.0)                             # 3 mm behind the third
        B2, info = assemble.meet_donors(F3, B3, [pad, deep])
        self.assertEqual(info["moved"], 2)
        np.testing.assert_allclose(B2[:2, 2], -1.3, atol=1e-4)
        self.assertEqual(float(B2[2, 2]), 0.0)


class RealData(unittest.TestCase):
    def test_saved_assemblies(self):
        seen = 0
        for p in core.PRODUCTS:
            sd = core.stage_dir("m1", p, assemble.STAGE)
            if not sd.done():
                continue
            seen += 1
            res, a = sd.load()
            self.assertTrue(all(res["watertight"].values()), (p, res["watertight"]))
            self.assertEqual(res["seam"]["gap_pixels_native"], 0, p)
            self.assertEqual(res["seam"]["gap_pixels_supersampled"], 0, p)
            self.assertEqual(len(res["bridge_underside_mm"]), 3)
            self.assertLessEqual(res["triangles"]["total_front"], 100_000)
            F, V = a["frame_F"], a["frame_V"]
            self.assertEqual(len(a["frame_region"]), len(F))
            self.assertEqual(a["frame_uv_px"].shape, (len(V), 2))
            n = int((a["frame_uv_src_px"][:, 0] >= 0).sum())
            s1 = json.loads(core.stage_dir("m1", p, "s1_generator").result_path.read_text())
            frame = NormFrame.from_dict(s1["frame"])
            src = depth.source_view(p)
            np.testing.assert_allclose(src.project(V[:n], frame), a["frame_uv_src_px"][:n], atol=5e-3)
            i = 1
            while f"lens{i}_V" in a:
                uv = a[f"lens{i}_uv"]
                self.assertEqual(len(uv), len(a[f"lens{i}_V"]))
                self.assertTrue(np.all((uv >= -1e-6) & (uv <= 1 + 1e-6)))
                i += 1
            self.assertEqual(i - 1, len(res["lenses"]))
        if not seen:
            self.skipTest("no s6 artifacts")


if __name__ == "__main__":
    unittest.main()
