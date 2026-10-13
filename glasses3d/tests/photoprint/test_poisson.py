"""Independent small-surface controls for the production Poisson seam solve."""
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.photoprint import poisson as PO
from glasses3d.tests.photoprint import printproof as PP


def surface():
    return SimpleNamespace(index=np.array([[0, 1, 2, 3, -1, -1, 4, 5, 6, 7]]),
                           chart=np.repeat([0, 1], 4), part=np.zeros(8, int))


def test_removes_chart_exposure_step_with_observations_fixed():
    wanted = np.tile([120., 80., 40.], (8, 1))
    guide = wanted.copy()
    guide[4:] = [60., 40., 20.]
    protected = np.array([1, 1, 0, 0, 0, 0, 0, 0], bool)
    out, report = PO.stitch(surface(), guide, protected, [[3, 4]])
    np.testing.assert_array_equal(out, wanted)
    np.testing.assert_array_equal(guide[4:], np.tile([60., 40., 20.], (4, 1)))
    assert report['protected_changed'] == 0
    assert all(c['relative_residual'] < 1e-7 for c in report['channels'])


def test_unanchored_component_keeps_mean_log_radiance():
    guide = np.tile([120., 80., 40.], (8, 1))
    guide[4:] /= 2
    out, report = PO.stitch(surface(), guide, np.zeros(8, bool), [[3, 4]])
    expected = np.rint(T.linear_to_srgb(np.exp(np.log(T.srgb_to_linear(guide)).mean(axis=0))))
    np.testing.assert_array_equal(out, np.tile(expected, (8, 1)))
    assert report['unanchored_components'] == 1


def test_matches_independent_dense_least_squares_with_conflicting_anchors():
    tex = SimpleNamespace(index=np.array([[0, 1, -1, 2, 3]]),
                          chart=np.array([0, 0, 1, 1]), part=np.zeros(4, int))
    guide = np.array([[80., 100., 120.], [90., 110., 130.], [150., 110., 80.], [160., 120., 90.]])
    # Correction differences on edges 0-1, 1-2 (seam), 2-3.
    design = np.array([[1., 0.], [-1., 1.], [0., -1.]])
    log = np.log(T.srgb_to_linear(guide))
    target = np.vstack([np.zeros(3), log[1]-log[2], np.zeros(3)])
    delta = np.linalg.lstsq(design, target, rcond=None)[0]
    expected = np.rint(guide).astype(np.uint8)
    expected[1:3] = np.rint(T.linear_to_srgb(np.exp(log[1:3]+delta)))
    out, _ = PO.stitch(tex, guide, [True, False, False, True], [[1, 2]])
    np.testing.assert_array_equal(out, expected)
    np.testing.assert_array_equal(out[[0, 3]], guide[[0, 3]])


@pytest.mark.parametrize('all_protected', [True, False])
def test_constant_input_stays_constant(all_protected):
    guide = np.tile([90., 60., 30.], (8, 1))
    out, _ = PO.stitch(surface(), guide, np.full(8, all_protected), [[3, 4]])
    np.testing.assert_array_equal(out, guide)


def test_neighbouring_unrelated_charts_and_cross_part_pairs_do_not_bleed():
    tex = SimpleNamespace(index=np.arange(4).reshape(1, 4), chart=np.array([0, 0, 1, 1]),
                          part=np.array([0, 0, 1, 1]))
    guide = np.array([[80., 60., 40.]]*2+[[150., 120., 90.]]*2)
    out, report = PO.stitch(tex, guide, [True, False, False, False], [[1, 2]])
    np.testing.assert_array_equal(out, guide)
    assert report['components'] == 2 and report['unanchored_components'] == 1


def test_empty_and_isolated_texels_are_identity():
    tex = SimpleNamespace(index=np.full((1, 1), -1), chart=np.array([], int), part=np.array([], int))
    out, _ = PO.stitch(tex, np.empty((0, 3)), np.array([], bool), [])
    assert out.shape == (0, 3)
    tex = SimpleNamespace(index=np.array([[0]]), chart=np.array([0]), part=np.array([0]))
    out, _ = PO.stitch(tex, [[30., 50., 80.]], [False], [])
    np.testing.assert_array_equal(out, [[30, 50, 80]])


def test_solver_failure_is_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(PO, 'cg', lambda a,b,**kw: (np.zeros(len(b)), 1))
    with pytest.raises(RuntimeError, match='did not converge'):
        PO.stitch(surface(), np.full((8, 3), 100.), np.zeros(8, bool), [[3, 4]])


def test_a5_never_moves_inside_frozen_sampling_floor():
    # the bake's reflection-band report stub (always 0) was deleted on 2026-10-13; the frozen offsets stay 7/8
    got, diagnostic, actual = PP.a5_offsets()
    assert got == (7,8) and diagnostic == (1,2,4,6) and actual == 0.0
