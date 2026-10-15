"""Final visibility describes the returned pose after any hidden/unobserved-angle reset.

The optimizer's search decisions are controlled to reach the reset branch; the
geometry, projection, loss comparison and native first-hit visibility are real.
No fit thresholds or camera/geometry values are searched to make these pass.
"""
import numpy as np
import pytest

from glasses3d.g3d_host import cameras, hinge_fit
from glasses3d.g3d_host.camera import Camera, NormFrame
from glasses3d.tests.fixtures.hinge_fit_known import box, pack, truth_pose


def _counts(mesh, camera, shape):
    """Independent ownership by fixture part IDs, not the fitter's visibility helper."""
    drawing = mesh.scene().render(camera, shape)
    hit = drawing['face_id']
    parts = mesh.part[hit[hit >= 0]]
    return {"+1": int(np.count_nonzero(parts == 1)), "-1": int(np.count_nonzero(parts == 2))}


@pytest.mark.parametrize('reset_reveals', [True, False], ids=['reset-reveals-other', 'reset-occludes-other'])
@pytest.mark.parametrize('mirror', [False, True], ids=['plus-occluder', 'minus-occluder'])
def test_final_native_counts_and_hidden_input_invariant_after_reset(monkeypatch, reset_reveals, mirror):
    # Looking down +Y, the broad upper arm at 0 degrees covers the narrow lower
    # arm. At +90 it swings clear. Its bounds centre remains on its assigned side.
    front = box((-70, 100), (-3, 3), (0, 10))
    upper = box((-65, 95), (5, 10), (-100, -10))
    lower = box((-25, -20), (-5, 0), (-90, -20))
    if mirror:
        for vertices, faces in (front, upper, lower):
            vertices[:, 0] *= -1
            faces[:] = faces[:, ::-1]
    plus, minus = (lower, upper) if mirror else (upper, lower)
    mesh, roles = pack(front, plus, minus, frame=NormFrame((0., 0., -50.), 400.))
    camera, shape = Camera(0., 90., 0., 0., 400., 220., 180.), (360, 440)
    moving, other = ('-1', '+1') if mirror else ('+1', '-1')
    side = int(moving)
    axes = {moving: [side * 70., 0.], other: [-side * 25., -10.]}
    initial = {moving: 90. if reset_reveals else 0., other: 0.}
    proposed = {moving: 0. if reset_reveals else 90., other: 10.}
    original = mesh.V.copy()
    authored = _counts(truth_pose(mesh, axes, initial), camera, shape)
    candidate = _counts(truth_pose(mesh, axes, proposed), camera, shape)
    assert authored[moving] > 1000 and candidate[moving] > 1000
    assert (authored[other] > 100) == reset_reveals
    assert (candidate[other] == 0) == reset_reveals
    foreground = truth_pose(mesh, axes, proposed).scene().render(camera, shape)['mask']
    calls = {moving: 0, other: 0}
    search_starts = []

    def search(actual_mesh, _roles, _axes, theta, cam, level, searched_side, _labels, bounds):
        key = f'{searched_side:+d}'
        calls[key] += 1
        native = _counts(truth_pose(actual_mesh, axes, theta), cam, level.shape)[key]
        # The second pass withdraws the moving arm's observability, requiring an
        # input-angle reset. The other arm follows genuine native visibility.
        observed = native > 0 and not (key == moving and calls[key] == 2)
        angle = proposed[key] if observed else theta[key]
        search_starts.append((key, calls[key], dict(theta), native))
        return angle, {"input_deg": theta[key], "theta_deg": angle, "observed": observed,
                       "visible_pixels_initial": native, "visibility_pixel_stride": 1,
                       "visibility_domain": "full_photo", "pixel_stride": level.stride,
                       "fixed_other_theta_deg": {k: v for k, v in theta.items() if k != key},
                       "bounds_deg": list(bounds), "grid": [], "grid_range": 1. if observed else 0.,
                       "evaluations": 1,
                       "status": 'visible_angle_evidence' if observed else 'flat_whole_scene_objective'}

    monkeypatch.setattr(hinge_fit, '_angle_search', search)
    monkeypatch.setattr(cameras, 'powell', lambda scene, level, cam, *_args, **_kwargs:
                        (cam, level.loss(scene, cam), 0))
    monkeypatch.setattr(hinge_fit, '_matches', lambda *_args, **_kwargs: None)
    # Process the occluder first on both mirrored controls. This ensures the same
    # explicit order while testing both signed hinge conventions.
    result = hinge_fit.refine(mesh, roles, axes, foreground, camera, 'top',
                              mesh.front_pivot((mesh.names[0],)), initial_theta=initial,
                              sides=(int(moving), int(other)), max_rounds=0)
    assert calls == {moving: 2, other: 2}
    assert next(t for key, n, t, _ in search_starts if key == moving and n == 2)[moving] == proposed[moving]
    assert result['theta_deg'][moving] == initial[moving]
    expected = truth_pose(mesh, axes, result['theta_deg'])
    np.testing.assert_allclose(result['mesh'].V, expected.V, atol=1e-12, rtol=0)
    np.testing.assert_array_equal(mesh.V, original)
    assert initial == {moving: 90. if reset_reveals else 0., other: 0.}
    final = _counts(result['mesh'], result['camera'], shape)
    assert (final[other] > 100) == reset_reveals
    for key, count in final.items():
        row = result['opening_fit']['per_side'][key]
        assert row['visible_pixels_final'] == count
        assert row['theta_deg'] == result['theta_deg'][key]
        if not count:
            assert result['theta_deg'][key] == initial[key]
            assert not row['observed'] and not result['theta_observed'][key]
            assert row['status'] == 'no_visible_pixels'
