"""Registration signals (WP5_DESIGN §2): definitions, highlight invariance, masks, masked DoG."""
import numpy as np
from scipy import ndimage

from glasses3d.g3d_host.photoprint import signals as S


def _img(seed=0, h=40, w=50):
    return np.random.default_rng(seed).integers(0, 200, (h, w, 3)).astype(np.float64)


def test_definitions():
    img = np.array([[[200, 120, 50], [30, 30, 30]]], float)
    assert np.allclose(S.luminance(img), [[370 / 3, 30]])
    assert np.allclose(S.warmth(img), [[150, 0]])
    assert np.allclose(S.irange(img), [[150, 0]])
    assert S.SIGNALS == ("warmth", "lum", "irange") and S.OBJECTIVE_SIGNALS == ("warmth", "lum")


def test_white_highlight_cancels_in_warmth_and_irange_not_in_luminance():
    img = _img()
    lit = img + 37.0                     # a white highlight adds equally to every channel
    assert np.allclose(S.warmth(lit), S.warmth(img))
    assert np.allclose(S.irange(lit), S.irange(img))
    assert np.allclose(S.luminance(lit), S.luminance(img) + 37.0)


def test_acetate_mask_rule():
    px = np.array([[[205, 128, 52],      # amber: lum 128, sat 153 -> acetate
                    [58, 34, 22],        # dark brown: lum 38 -> acetate
                    [150, 150, 150],     # grey metal: not
                    [238, 238, 238],     # background: not
                    [200, 180, 150],     # bright, lum 177: not
                    [100, 95, 90]]], float)   # lum 95, sat 10: not
    assert S.acetate_mask(px).tolist() == [[True, True, False, False, False, False]]


def test_valid_mask_is_eroded_matte_and_acetate():
    img = np.full((30, 30, 3), 40.0)
    matte = np.zeros((30, 30), bool)
    matte[5:25, 5:25] = True
    v = S.valid_mask(img, matte)
    assert v.sum() == 18 * 18 and not v[5].any() and v[6, 6]


def test_masked_gauss_ignores_outside_values():
    x = np.zeros((40, 40))
    m = np.zeros((40, 40), bool)
    m[10:30, 10:30] = True
    x[m] = 5.0
    x[~m] = 1e6                          # garbage outside the mask must not leak in
    g = S.masked_gauss(x, m, 3.0)
    assert np.allclose(g[m], 5.0)


def test_bandpass_equals_the_probe_formula():
    l = _img(1)[..., 0]
    m = np.ones_like(l, bool)
    m[:5] = False
    ref = (ndimage.gaussian_filter(l * m, 1.5) / np.maximum(ndimage.gaussian_filter(m.astype(float), 1.5), 1e-6)
           - ndimage.gaussian_filter(l * m, 4.0) / np.maximum(ndimage.gaussian_filter(m.astype(float), 4.0), 1e-6))
    assert np.allclose(S.bandpass(l, m, 1.5, 4.0), ref)


def test_ncc_guards_small_and_flat_samples():
    a = np.arange(200.0)
    assert abs(S.ncc(a, 2 * a + 3) - 1) < 1e-12
    assert S.ncc(a[:50], a[:50]) == 0.0            # below 100 samples
    assert S.ncc(a, np.ones(200)) == 0.0          # zero variance


def test_fast_bilinear_matches_texture_utils():
    from glasses3d.g3d_host import texture_utils as T
    img = np.ascontiguousarray(_img(2), np.float32)
    rng = np.random.default_rng(3)
    u, v = rng.uniform(-3, 53, 500), rng.uniform(-3, 43, 500)
    assert np.array_equal(S.sample_bilinear(img, u, v), T.sample_bilinear(img, u, v))
