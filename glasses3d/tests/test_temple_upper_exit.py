"""Independent geometry controls for a rearward, downward-sloping upper exit (the Prada-layout helper of
g3d.temple/2: F1 meets the base along the line through B where its plane equals the base)."""
import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX

TOP = [[0., 10.], [60., 10.], [100., -5.], [120., -10.]]
BOTTOM = [[0., -20.], [100., -20.], [120., -30.]]
INNER = [[0., 60.], [100., 60.], [120., 58.]]
BASE_T = [[0., 5.], [100., 5.], [120., 4.]]


def test_rearward_exit_is_selected_explicitly():
    # Zero line y = 8 - d/10 meets the descending top at d = 24.5/.275.
    hit = FX.zero_line_from([-1.6, .02, .2], TOP, np.array([40., 4.]), direction="rearward")
    d = 24.5 / .275
    np.testing.assert_allclose(hit, [d, 8 - d / 10], atol=1e-12)
    assert FX.zero_line_from([-1.6, .02, .2], TOP, np.array([40., 4.])) is None


def _layout(relief_scale, top=TOP):
    A, B = np.array([-2., 6.]), np.array([40., 4.])
    u = (B - A) / np.linalg.norm(B - A)
    rise = np.array([.02, .2]) @ np.array([-u[1], u[0]])
    return FX.prada_layout(top=top, bottom=BOTTOM, inner=INNER, base_t=BASE_T, A=A, B=B, C=[20., -22.], d_E=85.,
                           t_a=5. - .44 * relief_scale, t_c=8., t_h="base", rise=rise * relief_scale,
                           top_exit="rearward")


@pytest.mark.parametrize('relief_scale', [-2., -1., -.25, .25, 1., 2.])
def test_rearward_face_and_virtual_front_node_form_a_solid(relief_scale):
    spec = _layout(relief_scale)
    built = build_temple(spec)
    assert built.checks["closed"] and built.checks["euler"] == 2
    np.testing.assert_allclose(spec["nodes"]["K"][:2], [24.5 / .275, 8 - (24.5 / .275) / 10], atol=1e-10)
    assert spec["nodes"]["A_l"][0] < -2.
    # The added upper face must not move the independent lower/rear plane: X through B (40, 4, 65), C (20, -22, 68)
    # and E (85, -20, 65) on the base X = 60 + 5.
    np.testing.assert_allclose(built.sol.planes['F4'].coef, [65 + 114 / 55, -12 / 275, -9 / 110], atol=1e-12, rtol=0)
    ramps = {}
    for r in built.tables["border_ramps"]:                    # one row per border run, signed
        ramps[r["facet"]] = max(ramps.get(r["facet"], 0.0), abs(r["height_mm"]))
    assert ramps["F1"] < 1e-9 and ramps["F4"] < 1e-9          # K and E sit where the planes meet the base exactly


def test_invalid_exit_direction_is_refused():
    with pytest.raises(ValueError, match="direction"):
        FX.zero_line_from([1., 2., 3.], [[0., 0.], [1., 1.]], np.array([0., 0.]), direction="guess")


def test_the_helper_refuses_an_exit_beyond_the_planar_base():
    # The same line meets this raised outline at d = 16/.15, beyond d 100 where the base stops being one plane.
    with pytest.raises(ValueError, match="one plane"):
        _layout(1., top=[[0., 14.], [60., 14.], [100., -1.], [120., -6.]])
