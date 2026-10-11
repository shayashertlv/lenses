"""g3d_host/camera_similarity.py: image similarity registration and camera transport (restored from wp6-v1-final).

* similarity_matrix / transport_camera / mask_contour / register_masks are the tag's functions verbatim (source compared
  with the tag when it exists), and the tag's own tests hold: transport is exact on non-planar points for any
  similarity, refuses transforms a camera cannot carry, registers a cropped asymmetric polygon, reports a wrong shape
  as a residual (never a pass) and refuses a start that leaves no contour in view;
* new: bbox_seed starts close enough to converge on a scaled, shifted, slightly rolled copy; warped_iou is 1 for the
  true similarity and drops off it; transport() returns the transported camera (equal to transport_camera of the fitted
  matrix), its residuals and the mask IoU, and a projected mask of a 3D point set registers onto the same set seen
  through the moved camera.
* the WP6 v2 basis's matte choice (tests/proofs/wp6v2_basis.py; review 2026-10-11 P1, B3's dropped temple tops), on
  synthetic images: the white-background matte keeps a lit light-grey part the dark-studio matte drops, drops specks
  and fills small holes, but takes in a floor reflection (so black-side keeps the dark matte); the registration
  picks the candidate matte that segments the object as the source matte does: a matte without a lit band biases
  the transported camera's scale by ~2 %, and when the source matte lacks the same band that matte is the right one;
  ties keep the supplied (first) candidate.
"""
import inspect
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from glasses3d.g3d_host import camera_similarity as CS
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.g3d_host.camera_similarity import register_masks, similarity_matrix, transport_camera

REPO = Path(__file__).resolve().parents[2]
POLYGON = np.array([[15, 35], [130, 20], [140, 65], [250, 40], [280, 85],
                    [225, 145], [130, 110], [70, 170], [25, 125]], float)


def test_restored_functions_are_the_tags_verbatim():
    try:
        old = subprocess.run(["git", "-C", str(REPO), "show", "wp6-v1-final:glasses3d/g3d_host/camera_similarity.py"],
                             capture_output=True, text=True, check=True, encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("the wp6-v1-final tag is not available")
    for fn in (CS.similarity_matrix, CS.transport_camera, CS.mask_contour, CS.register_masks):
        assert inspect.getsource(fn) in old, fn.__name__


# ------------------------------------------------------------------------------------------------ the tag's tests
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
    source = np.zeros((210, 320), np.uint8)
    cv2.fillPoly(source, [np.rint(POLYGON).astype(np.int32)], 1)
    a, b = scale*np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))])
    truth = np.array([a, b, -75., 15.])
    matrix = similarity_matrix(truth)
    # Independently rasterize the transformed polygon, not a warp of source pixels.
    target = np.zeros((210, 220), np.uint8)
    cv2.fillPoly(target, [np.rint(POLYGON @ matrix[:, :2].T+matrix[:, 2]).astype(np.int32)], 1)
    fit = register_masks(source, target, truth+[.02, -.015, 4, -3])
    estimated = np.array(fit['matrix'])
    error = POLYGON @ (estimated[:, :2]-matrix[:, :2]).T + estimated[:, 2]-matrix[:, 2]
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


# ------------------------------------------------------------------------------------------------ new helpers
def _poly_masks(scale, angle, shift, shape_t=(320, 420)):
    source = np.zeros((210, 320), np.uint8)
    cv2.fillPoly(source, [np.rint(POLYGON).astype(np.int32)], 1)
    a, b = scale * np.array([np.cos(np.deg2rad(angle)), np.sin(np.deg2rad(angle))])
    matrix = similarity_matrix([a, b, *shift])
    target = np.zeros(shape_t, np.uint8)
    cv2.fillPoly(target, [np.rint(POLYGON @ matrix[:, :2].T + matrix[:, 2]).astype(np.int32)], 1)
    return source, target, matrix


@pytest.mark.parametrize('scale, angle, shift', [(1.054, 0.4, (40., 60.)), (1.22, -0.6, (-5., 90.)),
                                                 (0.85, 2.0, (70., 30.))])
def test_bbox_seed_converges_on_a_scaled_shifted_copy(scale, angle, shift):
    source, target, matrix = _poly_masks(scale, angle, shift)
    seed = CS.bbox_seed(source, target)
    assert seed[1] == 0.0 and abs(seed[0] - scale) < 0.05 * scale              # no rotation; the width ratio
    fit = register_masks(source, target, seed)
    estimated = np.array(fit['matrix'])
    error = POLYGON @ (estimated[:, :2] - matrix[:, :2]).T + estimated[:, 2] - matrix[:, 2]
    assert np.max(np.linalg.norm(error, axis=1)) < 1.5 and fit['termination'] == 'parameter_step_converged'


def test_bbox_seed_refuses_an_empty_mask():
    with pytest.raises(ValueError, match='foreground'):
        CS.bbox_seed(np.zeros((10, 10), np.uint8), np.ones((10, 10), np.uint8))


def test_warped_iou_is_one_at_the_truth_and_drops_off_it():
    source, target, matrix = _poly_masks(1.1, 3.0, (30., 40.))
    assert CS.warped_iou(source, target, matrix) > 0.97                       # rasterisation of the edges only
    off = matrix.copy()
    off[0, 2] += 12.0
    assert CS.warped_iou(source, target, off) < CS.warped_iou(source, target, matrix) - 0.05
    assert CS.warped_iou(np.zeros((5, 5)), np.zeros((5, 5)), np.eye(2, 3)) == 0.0


def test_transport_moves_the_camera_through_the_fitted_similarity():
    """A 3D point cloud's projected hull seen by camera A, and by A moved by a known similarity S: transport() from A
    registers the two masks, returns transport_camera(A, fitted S) and residuals well under a pixel."""
    camera = Camera(28.2, 11.4, 0.6, 0.26, 300., 230., 120.)
    frame = NormFrame((0., -4., -72.), 151.)
    pts = np.random.default_rng(7).uniform([-70, -22, -140], [70, 20, 4], (400, 3))
    a, b = 1.054 * np.array([np.cos(np.deg2rad(0.5)), np.sin(np.deg2rad(0.5))])
    truth = similarity_matrix([a, b, 10., 20.])
    moved = transport_camera(camera, truth)

    def hull_mask(cam, shape):
        uv = project_mm(pts, cam, frame)
        m = np.zeros(shape, np.uint8)
        cv2.fillPoly(m, [np.rint(cv2.convexHull(uv.astype(np.float32))[:, 0]).astype(np.int32)], 1)
        return m
    src, dst = hull_mask(camera, (260, 440)), hull_mask(moved, (300, 480))      # wholly in view
    t = CS.transport(camera, src, dst)
    np.testing.assert_allclose(t['matrix'], similarity_matrix(t['parameters']))
    assert t['camera'] == transport_camera(camera, np.array(t['matrix']))
    assert t['mean_bidirectional_px'] < 0.6 and t['mask_iou_after_warp'] > 0.98
    assert abs(t['scale'] - 1.054) < 0.005 and abs(t['rotation_deg'] - 0.5) < 0.3
    uv_t, uv_m = project_mm(pts, t['camera'], frame), project_mm(pts, moved, frame)
    assert np.max(np.linalg.norm(uv_t - uv_m, axis=1)) < 1.5                  # the moved camera, within a pixel or so
    for k in ('yaw', 'pitch', 'perspective'):
        assert getattr(t['camera'], k) == getattr(camera, k)                  # a similarity never changes the pose
    assert t['start'] == CS.bbox_seed(src, dst) and t['iterations'] >= 1 and 'contour agreement' in t['note']


# ------------------------------------------------------------------------------------------------ the basis's mattes
def _studio_photo(floor_reflection=False):
    """A white-background photo: a black body with a lit light-grey top (L* ~81), a lilac lens, a small background-
    coloured hole, a dark 3x3 speck and, optionally, a light-grey floor reflection below the body (L* ~85)."""
    img = np.full((200, 300, 3), 255, np.uint8)
    img[80:150, 50:250] = 20                                          # body
    img[60:80, 80:140] = 200                                          # lit top
    img[95:135, 70:120] = (225, 215, 240)                             # lens
    img[100:104, 200:204] = 255                                       # hole (16 px)
    img[10:13, 10:13] = 60                                            # speck
    if floor_reflection:
        img[155:185, 60:240] = 215
    return img


def test_white_background_matte_keeps_the_lit_part_the_dark_matte_drops():
    from glasses3d.tests.proofs import wp6v2_basis as WB
    img = _studio_photo()
    white, dark = WB.white_background_matte(img), WB.dark_studio_matte(img)
    assert white[60:80, 80:140].all() and not dark[60:80, 80:140].any()       # the lit top: B3's temple tops
    for m in (white, dark):
        assert m[80:150, 50:250].all()                                        # body, lens and the filled hole
    assert dark[10:13, 10:13].all() and not white[10:13, 10:13].any()         # the speck is dropped
    from scipy import ndimage
    assert ndimage.label(white)[1] == 1 and int(white.sum()) == 70 * 200 + 20 * 60
    # a floor reflection is foreground to the white-background matte: black-side (reflective floor) keeps the dark one
    refl = _studio_photo(floor_reflection=True)
    assert WB.white_background_matte(refl)[155:185, 60:240].all() and not WB.dark_studio_matte(refl)[155:185].any()
    assert WB.SUPPLIED_MATTE == {"black-side": "dark_studio", "black-angled": "white_background",
                                 "black-front": "white_background"}
    assert WB.REGISTRATION_MATTES[0] == "white_background"                     # the twins' supplied matte first


def test_registration_uses_the_matte_that_segments_like_the_source():
    from glasses3d.tests.proofs import wp6v2_basis as WB
    camera = Camera(28.2, 11.4, 0.6, 0.26, 300., 230., 120.)
    source, target, matrix = _poly_masks(1.1, 0.0, (30., 40.))
    lit_dropped = target.copy()
    lit_dropped[:82] = 0                                     # the top band (source rows < 38 -> target rows < 82)

    def error(t):
        estimated = np.array(t['matrix'])
        return np.abs(POLYGON @ (estimated[:, :2] - matrix[:, :2]).T + estimated[:, 2] - matrix[:, 2]).max()

    best, t, rows = WB.registration(camera, source, {"keeps": target, "drops": lit_dropped})
    assert best == "keeps" and error(t) < 1.0 and t['camera'] == transport_camera(camera, np.array(t['matrix']))
    assert rows["drops"]["mean_bidirectional_px"] > rows["keeps"]["mean_bidirectional_px"] + 1.0
    assert rows["keeps"]["camera"]["scale"] == pytest.approx(330.0, rel=0.005)  # 300 x 1.1
    assert rows["drops"]["camera"]["scale"] < 330.0 * 0.99                      # the dropped band biases the scale
    source_cut = source.copy()
    source_cut[:38] = 0                                      # a source matte without the same band (02's lit strips)
    best, t, rows = WB.registration(camera, source_cut, {"keeps": target, "drops": lit_dropped})
    assert best == "drops" and error(t) < 1.0
    best, _t, _rows = WB.registration(camera, source, {"a": target, "b": target.copy()})
    assert best == "a"                                       # a tie keeps the first (the supplied) candidate
