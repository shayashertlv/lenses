"""The six texture helpers ported from automation/bsa/texture.py (WP3b), each on tiny arrays."""
from __future__ import annotations

import numpy as np
import pytest

from glasses3d.g3d_host import camera as C
from glasses3d.g3d_host import raster
from glasses3d.g3d_host import texture_utils as T
from glasses3d.tests import glbfab


def test_de00_identity_known_pair_and_shape():
    a = np.array([[10, 120, 200], [255, 255, 255]], float)
    np.testing.assert_allclose(T.de00(a, a), [0.0, 0.0], atol=1e-9)
    bw = T.de00(np.array([0.0, 0.0, 0.0]), np.array([255.0, 255.0, 255.0]))
    assert float(bw) == pytest.approx(100.0, abs=0.01)                 # L* 0 -> 100, no chroma
    assert T.de00(np.zeros((2, 3, 3)), np.full((2, 3, 3), 128.0)).shape == (2, 3)
    lab = T.lab_of(np.array([255.0, 255.0, 255.0]))
    np.testing.assert_allclose(lab, [100.0, 0.0, 0.0], atol=0.01)


def test_srgb_linear_round_trip():
    v = np.arange(256, dtype=float)
    np.testing.assert_allclose(T.linear_to_srgb(T.srgb_to_linear(v)), v, atol=1e-9)


def test_quantile_gain_recovers_a_linear_light_gain():
    rng = np.random.default_rng(1)
    gen = rng.uniform(20, 160, (500, 3))
    true_gain = np.array([1.6, 0.8, 1.2])
    photo = T.linear_to_srgb(T.srgb_to_linear(gen) * true_gain)
    gain, off, info = T.quantile_gain(gen, photo)
    np.testing.assert_allclose(gain, true_gain, rtol=1e-6)
    np.testing.assert_allclose(off, 0.0, atol=1e-9)
    np.testing.assert_allclose(T.apply_gain(gen, gain, off), photo, atol=1e-6)
    assert info["pairs"] == 500 and len(info["channels"]) == 3


def test_quantile_gain_offset_is_non_negative_bounded_and_needs_pairs():
    rng = np.random.default_rng(2)
    gen = rng.uniform(20, 160, (400, 3))
    lifted = T.linear_to_srgb(T.srgb_to_linear(gen) * 0.9 + 0.05)       # a lifted black level -> positive offset
    gain, off, _ = T.quantile_gain(gen, lifted)
    assert (off >= 0).all() and off.max() > 0.01
    dark = T.linear_to_srgb(T.srgb_to_linear(gen) * 0.01)               # beyond the lower gain bound
    g2, o2, _ = T.quantile_gain(gen, dark)
    assert (g2 >= T.GAIN_BOUNDS[0] - 1e-12).all() and (o2 >= 0).all()
    g3, o3, info = T.quantile_gain(gen[:10], gen[:10])
    assert info["skipped"] == "too_few_pairs" and (g3 == 1).all() and (o3 == 0).all()


def test_rasterize_triangles_covers_a_square_with_valid_barycentrics():
    tri = np.array([[[0, 0], [4, 0], [4, 3]], [[0, 0], [4, 3], [0, 3]]], float)     # a 4 x 3 texel square
    fid, bary = T.rasterize_triangles(tri, (5, 6))
    assert (fid[:3, :4] >= 0).all() and (fid[3:, :] == -1).all() and (fid[:, 4:] == -1).all()
    np.testing.assert_allclose(bary[:3, :4].sum(-1), 1.0, atol=1e-6)
    i, j = 0, 3                                          # texel centre (3.5, 0.5) lies in triangle 0
    assert fid[i, j] == 0
    P = (bary[i, j, :, None] * tri[0]).sum(0)
    np.testing.assert_allclose(P, [j + 0.5, i + 0.5], atol=1e-6)
    later, _ = T.rasterize_triangles(np.concatenate([tri, tri[:1]]), (5, 6))
    assert later[0, 3] == 2                              # later triangles win
    empty, _ = T.rasterize_triangles(np.array([[[0, 0], [1, 1], [2, 2]]], float), (3, 3))   # degenerate
    assert (empty == -1).all()


def test_nearest_fill():
    img = np.zeros((3, 4, 3), np.uint8)
    valid = np.zeros((3, 4), bool)
    img[0, 0] = (255, 0, 0); valid[0, 0] = True
    img[2, 3] = (0, 0, 255); valid[2, 3] = True
    out, dist = T.nearest_fill(img, valid)
    assert tuple(out[0, 1]) == (255, 0, 0) and tuple(out[2, 2]) == (0, 0, 255)
    assert dist[0, 0] == 0 and dist[0, 1] == 1 and dist[1, 1] == pytest.approx(np.sqrt(2))
    same, inf = T.nearest_fill(img, np.zeros((3, 4), bool))
    assert np.array_equal(same, img) and np.isinf(inf).all()


def test_sample_bilinear_pixel_centres_interpolation_and_clamp():
    img = np.arange(12, dtype=float).reshape(3, 4)              # value = 4 v + u
    s = T.sample_bilinear(img, np.array([0.0, 1.5, 3.0, -2.0, 9.0]), np.array([0.0, 0.5, 2.0, 1.0, 9.0]))
    np.testing.assert_allclose(s[:, 0], [0.0, 3.5, 11.0, 4.0, 11.0], atol=1e-6)
    rgb = np.stack([img, 2 * img, 3 * img], -1)
    np.testing.assert_allclose(T.sample_bilinear(rgb, np.array([1.5]), np.array([0.5])), [[3.5, 7.0, 10.5]], atol=1e-5)


def test_visible_points_facing_occlusion_and_image_bounds():
    v1, f1 = glbfab.box(-10, 10, -10, 10, -1, 1)        # front plate
    v2, f2 = glbfab.box(-10, 10, -10, 10, -21, -19)     # plate behind it
    V = np.vstack([v1, v2]); F = np.vstack([f1, f2 + 8]).astype(np.int64)
    frame = C.NormFrame.from_bbox(V)
    scene = raster.get_scene(V, F, frame)
    cam = raster.view_camera(frame, 0.0, 0.0, 5.0, (200, 200))          # front camera
    n = np.array([0.0, 0.0, 1.0])
    P = np.array([[0.0, 0.0, 1.0],         # front face of the front plate: visible
                  [0.0, 0.0, -1.0],        # its back face: facing away
                  [0.0, 0.0, -19.0],       # front face of the hidden plate: occluded
                  [0.0, 0.0, 1.0]])        # same point, normal sideways: grazing
    N = np.array([n, -n, n, [1.0, 0.0, 0.0]])
    vis, uv = T.visible_points(P, N, cam, frame, scene, (200, 200))
    assert vis.tolist() == [True, False, False, False]
    np.testing.assert_allclose(uv[0], [99.5, 99.5], atol=1e-9)
    far = raster.view_camera(frame, 0.0, 0.0, 5.0, (200, 200), center_mm=np.array([500.0, 0.0, 0.0]))
    assert not T.visible_points(P[:1], N[:1], far, frame, scene, (200, 200))[0][0]   # outside the image
    v_open = T.visible_points(P[2:3], N[2:3], cam, frame, scene, (200, 200), tol_mm=25.0)[0]
    assert v_open[0]                                                     # a generous tolerance lets it through
    e, euv = T.visible_points(np.zeros((0, 3)), np.zeros((0, 3)), cam, frame, scene, (200, 200))
    assert e.shape == (0,) and euv.shape == (0, 2)
