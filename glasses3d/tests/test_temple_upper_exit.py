"""Independent geometry controls for a rearward, downward-sloping upper exit."""
import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX


def test_rearward_exit_is_selected_explicitly():
    # Zero line y = 8 - d/10 meets the descending top at d = 24.5/.275.
    chain = [[0., 10.], [60., 10.], [100., -5.], [120., -10.]]
    hit = FX._zero_line_from([-1.6, .02, .2], chain, np.array([40., 4.]), direction="rearward")
    d = 24.5 / .275
    np.testing.assert_allclose(hit, [d, 8-d/10], atol=1e-12)
    assert FX._zero_line_from([-1.6, .02, .2], chain, np.array([40., 4.])) is None


@pytest.mark.parametrize('relief_scale', [.25, 1., 2.])
def test_rearward_face_and_virtual_front_node_form_a_solid(relief_scale):
    A, B = np.array([-2., 6.]), np.array([40., 4.])
    u = (B-A)/np.linalg.norm(B-A)
    rise = np.array([.02, .2]) @ np.array([-u[1], u[0]])
    spec = FX.prada_spec(top=[[0., 10.], [60., 10.], [100., -5.], [120., -10.]],
        bottom=[[0., -20.], [100., -20.], [120., -30.]], zone_end=100.,
        x_in0=60., x_in_ze=60., t_zone=5., arm=[[120., 58., 4.]],
        A=A, B=B, C=[20., -22.], d_E=85., r_A=-.44*relief_scale, r_B=0., r_C=3.,
        r_H=0., rise=rise*relief_scale, top_zero_direction="rearward")
    built = build_temple(spec)
    assert built.checks["closed"]
    np.testing.assert_allclose(spec["nodes"]["K"][:2], [24.5/.275, 8-(24.5/.275)/10], atol=1e-10)
    assert spec["nodes"]["A_l"][0] < A[0]


def test_invalid_exit_direction_is_refused():
    with pytest.raises(ValueError, match="direction"):
        FX._zero_line_from([1., 2., 3.], [[0., 0.], [1., 1.]], np.array([0., 0.]), direction="guess")


def test_outline_change_cannot_extend_a_facet_past_its_zone():
    # The same zero line as above meets this raised outline at d=16/.15,
    # beyond the unchanged 100 mm planar region. Refusing it is required.
    A, B = np.array([-2., 6.]), np.array([40., 4.])
    u = (B-A)/np.linalg.norm(B-A)
    rise = np.array([.02, .2]) @ np.array([-u[1], u[0]])
    with pytest.raises(ValueError, match="beyond zone_end"):
        FX.prada_spec(top=[[0., 14.], [60., 14.], [100., -1.], [120., -6.]],
            bottom=[[0., -20.], [100., -20.], [120., -30.]], zone_end=100.,
            x_in0=60., x_in_ze=60., t_zone=5., arm=[[120., 58., 4.]],
            A=A, B=B, C=[20., -22.], d_E=85., r_A=-.44, r_B=0., r_C=3.,
            r_H=0., rise=rise, top_zero_direction="rearward")
