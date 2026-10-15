"""Native first-hit visibility must survive a search grid that misses a thin arm."""
import numpy as np
import pytest

from glasses3d.g3d_host import cameras, hinge_fit, raster
from glasses3d.g3d_host.camera import Camera, NormFrame
from glasses3d.tests.fixtures.hinge_fit_known import box, pack


@pytest.mark.parametrize('side', [1, -1])
def test_native_visible_arm_is_not_declared_hidden_by_coarse_search_grid(side):
    # At exactly 8 px/mm each 0.05 mm arm spans 0.4 native pixels and its centre
    # lands on an integer column. Stride-two rays instead lie on half-integers.
    mesh, roles = pack(box((-64, 64), (-2, 2), (-1, 1)),
                       box((62.975, 63.025), (-1, 1), (-120, -2)),
                       box((-63.025, -62.975), (-1, 1), (-120, -2)),
                       frame=NormFrame((0., 0., -60.), 128.))
    camera = Camera(0., 90., 0., 0., 1024., 700., 640.)
    shape = (1280, 1400)
    scene = mesh.scene()
    foreground = scene.render(camera, shape)['mask']
    level = cameras.Level(foreground, cameras.FIT_W, 'native_visibility_control',
                          margin=cameras.FINAL_ROI_MARGIN)
    assert level.stride == 2
    native = scene.render(camera, shape, 1, level.roi)
    coarse = scene.render(camera, shape, level.stride, level.roi)
    selected_part = 1 if side > 0 else 2

    def count_arm(rendered):
        faces = rendered['face_id']
        hits = faces[faces >= 0]
        return int(np.count_nonzero(mesh.part[hits] == selected_part))

    native_count = count_arm(native)
    assert native_count > 900
    assert count_arm(coarse) == 0
    coarse_columns, _ = raster.pixel_grid(shape, level.stride, level.roi)
    assert np.all(np.mod(coarse_columns, 1) == .5)
    face_sides = np.where(mesh.part == 1, 1, np.where(mesh.part == 2, -1, 0))
    axes = {'+1': [63., -2.], '-1': [-63., -2.]}
    theta = {'+1': 0., '-1': 0.}
    before = mesh.V.copy()
    # These deliberately tiny diagnostic bounds keep every coarse trial blind.
    # A flat search objective is not evidence that the arm has no native pixels.
    angle, result = hinge_fit._angle_search(mesh, roles, axes, theta, camera, level,
                                            side, face_sides, (-.0001, .0001))
    assert result['visible_pixels_initial'] == native_count
    assert result['visibility_pixel_stride'] == 1 and result['pixel_stride'] == 2
    assert result['status'] == 'flat_whole_scene_objective'
    assert not result['observed'] and angle == 0.
    assert result['fixed_other_theta_deg'] == {f'{-side:+d}': 0.}
    np.testing.assert_array_equal(mesh.V, before)
    assert theta == {'+1': 0., '-1': 0.}


@pytest.mark.parametrize('side', [1, -1])
def test_full_photo_visible_arm_is_not_hidden_by_foreground_fitting_roi(side):
    # The photo foreground describes a short front, while current model arms are
    # farther back. Their lack of overlap is a fit discrepancy, not occlusion.
    mesh, roles = pack(box((-64, 64), (-2, 2), (-1, 1)),
                       box((62.975, 63.025), (-1, 1), (-120, -50)),
                       box((-63.025, -62.975), (-1, 1), (-120, -50)),
                       frame=NormFrame((0., 0., -60.), 128.))
    camera = Camera(0., 90., 0., 0., 1024., 700., 640.)
    shape = (1280, 1400)
    scene = mesh.scene()
    full = scene.render(camera, shape)
    full_parts = np.full(shape, -1, int)
    hit = full['face_id'] >= 0
    full_parts[hit] = mesh.part[full['face_id'][hit]]
    foreground = full_parts == 0
    level = cameras.Level(foreground, cameras.FIT_W, 'cropped_visibility_control',
                          margin=cameras.FINAL_ROI_MARGIN)
    selected_part = 1 if side > 0 else 2
    native_count = int(np.count_nonzero(full_parts == selected_part))
    assert native_count > 500
    cropped = scene.render(camera, shape, 1, level.roi)['face_id']
    assert not np.any(mesh.part[cropped[cropped >= 0]] == selected_part)
    arm_y, _ = np.nonzero(full_parts == selected_part)
    assert arm_y.max() < level.roi[1]
    face_sides = np.where(mesh.part == 1, 1, np.where(mesh.part == 2, -1, 0))
    axes = {'+1': [63., -2.], '-1': [-63., -2.]}
    theta = {'+1': 0., '-1': 0.}
    before = mesh.V.copy()
    angle, result = hinge_fit._angle_search(mesh, roles, axes, theta, camera, level,
                                            side, face_sides, (-.0001, .0001))
    assert result['visible_pixels_initial'] == native_count
    assert result['status'] == 'flat_whole_scene_objective'
    assert not result['observed'] and angle == 0.
    np.testing.assert_array_equal(mesh.V, before)
    assert theta == {'+1': 0., '-1': 0.}
