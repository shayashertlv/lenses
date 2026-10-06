"""S3 (WP6_DESIGN §9 STOP-C): relief_fit.own_optimum on the profile shapes the Prada resume run recorded."""
import numpy as np

from glasses3d.g3d_host import relief_fit as RF

DELTAS = np.linspace(-2.5, 2.5, 9)


def test_constant_profile_has_no_optimum():
    # the 06 hinge-zone envelope over the relief at C (WP6 resume diagnostic 2026-10-06): relief is invisible in that
    # silhouette, so all nine values were bit-identical; the argmin of a constant read as an optimum at -2.5 (outside)
    flat = [0.23141820806615368] * 9
    assert RF.own_optimum(DELTAS, flat, signed=True) is None
    assert RF.own_optimum(DELTAS, flat, signed=False) is None


def test_signed_zero_crossing_and_unsigned_minimum_unchanged():
    lvl = np.linspace(-2.0, 2.0, 9)
    vals = [3.1506, 2.7563, 2.2480, 1.7419, 1.2299, 0.7819, 0.2632, -0.2654, -0.7866]   # 03 offset over the level
    o = RF.own_optimum(lvl, vals, signed=True)
    assert 1.2 < o < 1.3
    tr = [1.81, 1.61, 1.39, 1.17, 0.95, 0.73, 0.52, 0.30, 0.10]                       # monotone transfer
    assert RF.own_optimum(lvl, tr, signed=False) == 2.0                                  # beyond the sweep: its edge
    assert RF.own_optimum(lvl, [np.nan] * 7 + [1.0, 2.0], signed=False) is None


def test_near_constant_profile_still_has_an_optimum():
    # a profile that changes (even a little) keeps its optimum: only an exactly constant one is "not seen"
    vals = [1.2066, 1.2065, 1.2298, 1.2068, 1.2299, 1.2066, 1.2068, 1.2068, 1.2069]
    assert RF.own_optimum(DELTAS, vals, signed=False) == DELTAS[1]
