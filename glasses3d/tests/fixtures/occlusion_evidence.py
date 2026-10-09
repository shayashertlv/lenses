"""Analytic opaque boxes: a far arm either hidden for the whole angle grid or visible."""
import numpy as np

from glasses3d.g3d_host import camera as CM, raster, relief_fit as RF, relief_views as RV
from glasses3d.tests.fixtures.camera_evidence import FACES


def _box(xs, ys, zs):
    return np.array([[x, y, z] for x in xs for y in ys for z in zs], float)


def make_job(side, yaw, visible):
    front = _box((-64, 64), (-18, 14), (-9, 4))
    near = _box((63, 74), (150, 160) if visible else (-60, 60), (-250, 80))
    far = _box((-67, -64), (-4, 4), (-145, -25))
    near[:, 0] *= side
    far[:, 0] *= side
    model = RV.Model('analytic occlusion boxes', {side: (near, FACES), -side: (far, FACES)})
    frame = CM.NormFrame.from_bbox(np.vstack([front, near, far,
                                            _box((-74, 74), (-60, 160), (-250, 80))]))
    shape = (1000, 1400)
    cam = raster.view_camera(frame, side*yaw, 12., 2.5, shape, roll=0., perspective=0.,
                             center_mm=np.array([0., 40., -90.]))
    job = RF.SyntheticJob(frame=frame, V0=front, F0=FACES,
                         hinge={'z_mm': -10.6, 'axis_xz_mm': [64.6, -10.1]},
                         cams={'02': cam}, shapes={'02': shape},
                         views={'02': ('occlusion control', 'angled')}, view_names={'02': 'angled'},
                         truth=model, theta={'02': {1: 0., -1: 0.}})
    return job, model, cam
