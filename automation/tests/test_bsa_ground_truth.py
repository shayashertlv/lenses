"""M0 ground truth: synthetic tests for the mask / distance maths plus real-data checks of the traced
outlines (skipped when data/bsa/ground_truth is missing)."""
from __future__ import annotations

import unittest

import numpy as np

from bsa import ground_truth as gt
from bsa.core import PRODUCTS, sha256_file


def _square(x0, y0, x1, y1):
    # clockwise on screen (v down): TL, TR, BR, BL
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)


def _lens(P, types=None, occluded=None, W=200, H=200, side="L", photo="front"):
    K = len(P)
    return {"side": side, "photo": photo, "image_size": [W, H], "points_px": np.asarray(P, float).tolist(),
            "segment_types": list(types or ["frame"] * K), "occluded": list(occluded or [False] * K)}


class MaskTests(unittest.TestCase):
    def test_square_mask_uses_pixel_centres(self):
        m = gt.polygon_mask(_square(0.5, 0.5, 4.5, 4.5), (8, 8))
        self.assertEqual(int(m.sum()), 16)
        self.assertTrue(m[1:5, 1:5].all())
        self.assertFalse(m[0].any() or m[:, 0].any() or m[5:].any() or m[:, 5:].any())

    def test_circle_mask_matches_area(self):
        t = np.linspace(0, 2 * np.pi, 400, endpoint=False)
        P = np.column_stack([100 + 60 * np.cos(t), 90 + 40 * np.sin(t)])
        m = gt.polygon_mask(P, (200, 200))
        self.assertAlmostEqual(m.sum() / gt.polygon_area(P), 1.0, delta=0.01)

    def test_mask_clipped_to_image(self):
        m = gt.polygon_mask(_square(-10.5, -10.5, 3.5, 3.5), (6, 6))
        self.assertEqual(int(m.sum()), 16)

    def test_iou_identity_and_shift(self):
        L = _lens(_square(20.3, 30.7, 120.3, 90.7))
        self.assertEqual(gt.mask_iou(gt.points(L), L), 1.0)
        shifted = gt.points(L) + [10, 0]
        self.assertAlmostEqual(gt.mask_iou(shifted, L), 90 * 60 / (110 * 60), delta=0.01)


class DistanceTests(unittest.TestCase):
    def setUp(self):
        # top edge + first half of the right/left edges are 'frame', the rest 'free'
        self.L = _lens(_square(10, 10, 110, 110), types=["frame", "frame", "free", "free"])

    def test_grown_candidate_is_two_px_outside(self):
        e = gt.edge_distance(_square(8, 8, 112, 112), self.L, "frame")
        self.assertAlmostEqual(e["mean"], 2.0, places=6)
        self.assertAlmostEqual(e["p95"], 2.0, places=6)
        self.assertAlmostEqual(e["signed_mean"], 2.0, places=6)
        s = gt.edge_distance(_square(12, 12, 108, 108), self.L, "frame")
        # GT samples next to the corners are slightly farther than 2 px from the shrunk square
        self.assertAlmostEqual(s["signed_mean"], -2.0, delta=0.05)
        self.assertTrue(np.all(np.isfinite([s["mean"], s["p95"], s["max"]])))

    def test_restriction_to_segment_type(self):
        cand = _square(10, 10, 110, 115)          # only the bottom edge moved (free)
        f = gt.edge_distance(cand, self.L, "frame")
        r = gt.edge_distance(cand, self.L, "free")
        self.assertLess(f["mean"], 0.3)           # the frame half of the sides sits 0 px off
        self.assertAlmostEqual(f["median"], 0.0, places=6)
        self.assertGreater(r["mean"], 1.0)
        self.assertAlmostEqual(r["max"], 5.0, places=6)
        both = gt.edge_distance(cand, self.L, ("frame", "free"))
        self.assertEqual(both["n"], f["n"] + r["n"])
        self.assertEqual(gt.edge_distance(cand, self.L, None)["n"], both["n"])

    def test_sample_counts_follow_arc_length(self):
        e = gt.edge_distance(_square(10, 10, 110, 110), self.L, None, step=0.5)
        self.assertEqual(e["n"], 800)             # 400 px perimeter / 0.5
        f = gt.edge_distance(_square(10, 10, 110, 110), self.L, "frame", step=0.5)
        self.assertEqual(f["n"], 400)             # top edge + half of each side

    def test_occluded_samples_excluded_by_default(self):
        L = _lens(_square(10, 10, 110, 110), types=["frame"] * 4, occluded=[True, True, False, False])
        a = gt.edge_distance(_square(10, 10, 110, 110), L, "frame")
        b = gt.edge_distance(_square(10, 10, 110, 110), L, "frame", include_occluded=True)
        self.assertEqual(a["n"], 400)
        self.assertEqual(b["n"], 800)

    def test_empty_selection(self):
        e = gt.edge_distance(_square(8, 8, 112, 112), self.L, "rimless")
        self.assertEqual(e["n"], 0)
        self.assertIsNone(e["mean"])

    def test_candidate_direction(self):
        c = gt.candidate_edge_distance(_square(8, 8, 112, 112), self.L, "frame")
        self.assertAlmostEqual(c["mean"], 2.0, delta=0.05)
        self.assertGreater(c["signed_mean"], 1.9)
        c2 = gt.candidate_edge_distance(_square(12, 12, 108, 108), self.L, None)
        self.assertLess(c2["signed_mean"], -1.9)

    def test_boundary_sample_labels_split_at_midpoint(self):
        S, T, O = gt.boundary_samples(_square(0, 0, 10, 10), ["frame", "frame", "free", "free"], None, 1.0)
        self.assertEqual(len(S), 40)
        self.assertEqual(int(np.sum(T == "frame")), 10 + 5 + 5)   # top edge, half right, half left


class MirrorAndMatchTests(unittest.TestCase):
    def test_mirror_maps_pixel_centres_and_round_trips(self):
        L = _lens([[0, 5], [10, 5], [10, 20], [0, 20]], types=["frame", "free", "free", "free"], W=50)
        M = gt.mirror_lens(L)
        self.assertEqual(M["side"], "R")
        self.assertEqual(M["photo"], "front_mirrored")
        np.testing.assert_allclose(sorted(gt.points(M)[:, 0]), sorted([49, 39, 39, 49]))
        # orientation preserved (same sign of the shoelace sum)
        def signed(P):
            x, y = P[:, 0], P[:, 1]
            return np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)
        self.assertEqual(np.sign(signed(gt.points(M))), np.sign(signed(gt.points(L))))
        back = gt.mirror_lens(M)
        np.testing.assert_allclose(gt.points(back), gt.points(L))
        self.assertEqual(back["segment_types"], L["segment_types"])
        # the label stays attached to the same physical vertex
        i = int(np.argmin(np.linalg.norm(gt.points(M) - [49, 5], axis=1)))
        self.assertEqual(M["segment_types"][i], "frame")

    def test_match_lenses_by_centroid(self):
        g = [_lens(_square(0, 0, 10, 10)), _lens(_square(100, 0, 110, 10))]
        pairs = gt.match_lenses(g, [_square(98, 1, 111, 9), _square(1, 1, 9, 9)])
        self.assertEqual(pairs, [(0, 1), (1, 0)])


@unittest.skipUnless(all(gt.json_path(p).exists() for p in PRODUCTS), "ground truth not generated")
class RealGroundTruthTests(unittest.TestCase):
    def test_every_product_has_the_expected_entries(self):
        expected = {"miu": [("L", "front"), ("R", "front")], "rayban": [("L", "front"), ("R", "front")],
                    "vb": [("L", "front"), ("R", "front")], "oakley": [("C", "front")],
                    "invu": [("C", "back"), ("C", "front")]}
        total = 0
        for p, want in expected.items():
            d = gt.load(p)
            got = sorted((lens["side"], lens["photo"]) for lens in d["lenses"])
            self.assertEqual(got, sorted(want), p)
            self.assertEqual(d["photo"], "front")
            total += len(got)
            self.assertTrue(gt.overlay_path(p).exists(), p)
        self.assertEqual(total, 9)

    def test_photo_hashes_and_sizes_match(self):
        from PIL import Image
        for p, prod in PRODUCTS.items():
            d = gt.load(p)
            self.assertEqual(d["photo_sha256"], sha256_file(prod.photo_path("front")), p)
            for lens in d["lenses"]:
                path = prod.photo_path(lens["photo"])
                self.assertEqual(lens["photo_sha256"], sha256_file(path), (p, lens["photo"]))
                with Image.open(path) as im:
                    self.assertEqual(list(im.size), lens["image_size"], (p, lens["photo"]))

    def test_outlines_are_valid_closed_polygons(self):
        import shapely
        for p in PRODUCTS:
            for lens in gt.load(p)["lenses"]:
                P = gt.points(lens)
                W, H = lens["image_size"]
                K = len(P)
                self.assertEqual(lens["method"], "ai_visual_trace")
                self.assertIn(lens["confidence"], ("high", "medium-high", "medium", "low"))
                self.assertTrue(lens["notes"])
                self.assertEqual(len(lens["segment_types"]), K)
                self.assertEqual(len(lens["occluded"]), K)
                self.assertTrue(set(lens["segment_types"]) <= set(gt.SEGMENT_TYPES))
                self.assertGreaterEqual(K, 60, (p, lens["side"]))
                self.assertLessEqual(K, 250, (p, lens["side"]))
                self.assertFalse(np.allclose(P[0], P[-1]), "no repeated end point")
                self.assertTrue((P >= 0).all() and (P[:, 0] <= W - 1).all() and (P[:, 1] <= H - 1).all())
                self.assertTrue(shapely.Polygon(P).is_valid, (p, lens["side"], lens["photo"]))
                x, y = P[:, 0], P[:, 1]
                self.assertGreater(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y), 0, "clockwise on screen")
                # occluded vertices are always frame-bounded
                occ = np.array(lens["occluded"])
                self.assertTrue(all(t == "frame" for t in np.array(lens["segment_types"])[occ]))
                self.assertLess(np.linalg.norm(np.diff(np.vstack([P, P[:1]]), axis=0), axis=1).max(), 40)

    def test_pairs_are_left_right_and_mirror_consistent(self):
        """Independent traces of the two lenses of a pair agree after mirroring (tracing consistency;
        the bound also absorbs the photos' own small asymmetry)."""
        for p in ("vb", "rayban", "miu"):
            ls = {lens["side"]: lens for lens in gt.lenses(p)}
            L, R = gt.points(ls["L"]), gt.points(ls["R"])
            self.assertLess(L[:, 0].mean(), R[:, 0].mean())
            axis = (L[:, 0].mean() + R[:, 0].mean()) / 2
            M = dict(ls["L"])
            M["points_px"] = np.column_stack([2 * axis - L[:, 0], L[:, 1]])[::-1].tolist()
            M["segment_types"] = ls["L"]["segment_types"][::-1]
            M["occluded"] = ls["L"]["occluded"][::-1]
            self.assertGreater(gt.mask_iou(R, M), 0.97, p)
            self.assertLess(gt.edge_distance(R, M, None)["mean"], 3.0, p)
            self.assertAlmostEqual(gt.polygon_area(L) / gt.polygon_area(R), 1.0, delta=0.02)

    def test_type_mix_per_product(self):
        def frac(p, photo="front"):
            return [gt.summary(lens)["type_fractions"] for lens in gt.lenses(p, photo)]
        for p in ("vb", "rayban"):
            self.assertTrue(all(f["frame"] == 1.0 for f in frac(p)))
        for f in frac("miu"):
            self.assertGreater(f["rimless"], 0.7)
        for f in frac("oakley") + frac("invu") + frac("invu", "back"):
            self.assertGreater(f["frame"], 0.3)
            self.assertGreater(f["free"], 0.2)

    def test_back_entry_mirrors_into_the_back_mirrored_frame(self):
        (back,) = gt.lenses("invu", "back")
        M = gt.mirror_lens(back)
        W = back["image_size"][0]
        np.testing.assert_allclose(gt.points(M)[::-1][:, 0], W - 1 - gt.points(back)[:, 0])
        self.assertAlmostEqual(gt.polygon_area(gt.points(M)), gt.polygon_area(gt.points(back)), places=6)


if __name__ == "__main__":
    unittest.main()
