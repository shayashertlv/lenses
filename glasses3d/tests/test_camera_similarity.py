import cv2
import numpy as np
import pytest

from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.g3d_host.camera_similarity import transport_camera, similarity_matrix, register_masks


@pytest.mark.parametrize('perspective', [0., .3])
@pytest.mark.parametrize('angle', [-31., 0., 22.])
def test_camera_transport_exact_on_nonplanar_points(perspective, angle):
    camera = Camera(39., 17., -6., perspective, 900., 410., 330.)
    frame = NormFrame((3., -2., -48.), 151.)
    points = np.random.default_rng(931).uniform([-65, -20, -140], [65, 22, 4], (37, 3))
    a, b = 1.23*np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))])
    matrix = similarity_matrix([a, b, -321., 75.])
    expected = project_mm(points, camera, frame) @ matrix[:, :2].T + matrix[:, 2]
    actual = project_mm(points, transport_camera(camera, matrix), frame)
    np.testing.assert_allclose(actual, expected, atol=5e-12, rtol=0)


@pytest.mark.parametrize('matrix', [np.array([[1, .1, 0], [0, 1, 0]]),
    np.array([[1, 0, 0], [0, -1, 0]]), np.array([[1, 0, 0], [0, 2, 0]]),
    np.zeros((2, 3)), np.full((2, 3), np.nan)])
def test_refuse_camera_transforms_that_change_geometry(matrix):
    with pytest.raises(ValueError):
        transport_camera(Camera(0, 0, 0, 0, 1, 0, 0), matrix)


@pytest.mark.parametrize('angle', [-7., 7.])
@pytest.mark.parametrize('scale', [.8, 1.1])
def test_cropped_asymmetric_polygon_registration(angle, scale):
    polygon = np.array([[15, 35], [130, 20], [140, 65], [250, 40], [280, 85],
                        [225, 145], [130, 110], [70, 170], [25, 125]], float)
    source = np.zeros((210, 320), np.uint8)
    cv2.fillPoly(source, [np.rint(polygon).astype(np.int32)], 1)
    a, b = scale*np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))])
    truth = np.array([a, b, -75., 15.])
    matrix = similarity_matrix(truth)
    # Independently rasterize the transformed polygon, not a warp of source pixels.
    target = np.zeros((210, 220), np.uint8)
    cv2.fillPoly(target, [np.rint(polygon @ matrix[:, :2].T+matrix[:, 2]).astype(np.int32)], 1)
    fit = register_masks(source, target, truth+[.02, -.015, 4, -3])
    estimated = np.array(fit['matrix'])
    error = polygon @ (estimated[:, :2]-matrix[:, :2]).T + estimated[:, 2]-matrix[:, 2]
    assert np.max(np.linalg.norm(error, axis=1)) < 1.5
    assert fit['mean_bidirectional_px'] < .65


def test_wrong_shape_is_reported_as_residual_not_a_pass():
    source = np.zeros((150, 200), np.uint8)
    cv2.rectangle(source, (25, 25), (150, 110), 1, -1)
    target = source.copy()
    cv2.circle(target, (145, 40), 30, 1, -1)
    fit = register_masks(source, target, [1, 0, 0, 0])
    assert fit['reverse_percentiles_px'][-1] > 8
    assert 'pass' not in fit


def test_no_in_view_evidence_refused():
    mask = np.zeros((20, 20), np.uint8)
    mask[5:15, 5:15] = 1
    with pytest.raises(ValueError, match='viewport'):
        register_masks(mask, mask, [1, 0, 100, 100])
