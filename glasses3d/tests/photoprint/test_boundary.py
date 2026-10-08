"""Independent controls for the owner-selected observed-slope quilt join."""
import numpy as np
import pytest

from glasses3d.g3d_host.photoprint import boundary as B, fill as F


def affine():
    y, x = np.mgrid[:60, :70]
    img = np.stack([35 + 2*x + y, 30 + x + y, 25 + x], axis=-1).astype(np.uint8)
    hole = np.zeros(img.shape[:2], bool)
    hole[18:43, 21:50] = True
    return img, hole


def test_correct_affine_image_is_fixed_point():
    img, hole = affine()
    np.testing.assert_array_equal(B.match(img, img, hole, ~hole, 10), img)


def test_hidden_truth_and_non_donors_cannot_affect_fill():
    img, hole = affine()
    donors = ~hole
    donors[:, :15] = False
    filled = img.copy()
    filled[hole] = 120
    poisoned = img.copy()
    poisoned[~donors] = 255 - poisoned[~donors]
    actual = B.match(filled, img, hole, donors, 10)
    np.testing.assert_array_equal(actual, B.match(filled, poisoned, hole, donors | hole, 10))
    np.testing.assert_array_equal(actual[~hole], filled[~hole])
    assert np.any(actual[hole] != filled[hole])
    assert np.all(filled[hole] == 120)


@pytest.mark.parametrize('kind', ['empty', 'whole', 'disconnected'])
def test_no_donors_preserve_fill_even_for_unanchored_components(kind):
    img, hole = affine()
    if kind == 'empty':
        hole[:] = False
    elif kind == 'whole':
        hole[:] = True
    else:
        hole[1:3, 1:4] = True
    out = B.match(img, img, hole, np.zeros_like(hole), 5)
    np.testing.assert_array_equal(out, img)
    assert out is not img


def test_one_pixel_matches_closed_form_and_single_donor_fallback():
    img = np.full((3, 3, 3), 100, np.uint8)
    filled = img.copy()
    filled[1, 1] = 20
    hole = np.zeros((3, 3), bool)
    hole[1, 1] = True
    # Four boundary values of 100, no second donor in any direction.
    # delta = 4 * (100 - 20) / (4 + 1/radius**2).
    out = B.match(filled, img, hole, ~hole, 2)
    np.testing.assert_array_equal(out[1, 1], np.full(3, round(20 + 320/4.25)))


@pytest.mark.parametrize('status,value', [(1, 0.), (0, np.nan)])
def test_solver_failure_is_reported(monkeypatch, status, value):
    img, hole = affine()
    monkeypatch.setattr(B, 'cg', lambda matrix, rhs, **kw: (np.full(len(rhs), value), status))
    with pytest.raises(RuntimeError, match='boundary solve failed'):
        B.match(img, img, hole, ~hole, 10)


def test_default_dispatch_joins_quilt_once_and_preserves_patchmatch(monkeypatch):
    img, hole = affine()
    raw = img.copy()
    raw[hole] = 120
    monkeypatch.setattr(F, 'quilt_call', lambda *a, **kw: {'out': raw.copy()})
    monkeypatch.setattr(F, 'patchmatch_call', lambda *a, **kw: {'out': raw.copy()})
    actual = F.fill_call('quilt', img, hole, ~hole, 10, 0)
    np.testing.assert_array_equal(actual['out'], B.match(raw, img, hole, ~hole, 10))
    assert actual['boundary_join'] == 'slope'
    pm = F.fill_call('patchmatch', img, hole, ~hole, 10, 0)
    np.testing.assert_array_equal(pm['out'], raw)
    assert 'boundary_join' not in pm
