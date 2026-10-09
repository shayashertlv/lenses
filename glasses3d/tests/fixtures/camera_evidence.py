"""Analytic box/wedge camera controls: no Prada geometry, photos, guides or fits."""
import numpy as np

from glasses3d.g3d_host import camera as CM, raster, relief_fit as RF, relief_views as RV


FACES = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])


def make_job(yaw, sign):
    front = np.array([[x, y, z] for x in (-64., 64.) for y in (-18., 14.) for z in (-9., 4.)])
    # A tapered wedge arm: straight inner wall, varying height and thickness.
    plus = np.array([[64.+outer*(3.+.012*(-z)), low if upper == 0 else high, z]
                     for outer in (0, 1) for upper in (0, 1)
                     for z, low, high in ((-135., -3., 4.), (-11., -8., 14.))])
    minus = plus.copy()
    minus[:, 0] *= -1
    model = RV.Model('analytic wedge arms', {1: (plus, FACES), -1: (minus, FACES[:, ::-1])})
    frame = CM.NormFrame.from_bbox(np.vstack([front, plus, minus]))
    shape = (420, 700)
    side = abs(yaw) == 90.
    view = 'side' if side else 'angled'
    name = ('left' if yaw > 0 else 'right') if side else 'angled'
    true = raster.view_camera(frame, yaw, 16., 3.5, shape, roll=1.1, perspective=.18,
                              center_mm=np.array([0., -2., -60.]))
    theta = {1: 0., -1: 0.} if side else {1: 2.4, -1: 7.1}
    job = RF.SyntheticJob(frame=frame, V0=front, F0=FACES,
                         hinge={'z_mm': -10.6, 'axis_xz_mm': [64.6, -10.1]},
                         cams={'02': true}, shapes={'02': shape}, views={'02': ('analytic', view)},
                         view_names={'02': name}, truth=model, theta={'02': theta})
    params = true.to_dict()
    params.update(yaw=true.yaw+sign*.6, pitch=true.pitch+sign*.3, roll=true.roll+sign*.2,
                  scale=true.scale*(1+sign*.006), center_x=true.center_x+sign*3., center_y=true.center_y-sign*2.)
    job.base['02'] = CM.Camera.from_dict(params)
    return job, model


def projection_errors(job, model, fit):
    groups = {'front': (job.V0, 0), '+arm': (model.temples[1][0], 1), '-arm': (model.temples[-1][0], -1)}
    output = {}
    for label, (points, side) in groups.items():
        def projected(cam, theta):
            world = RV.rot_about_hinge(points, side, theta.get(side, 0.), job.hinge['axis_xz_mm']) if side else points
            return raster._project_norm(job.frame.to_norm(world), cam)
        exact = projected(job.true_cams['02'], job.truth_theta['02'])
        actual = projected(fit['camera'], fit['theta'])
        errors = np.linalg.norm(actual-exact, axis=1)
        output[label] = {'rms_px': float(np.sqrt(np.mean(errors**2))), 'max_px': float(errors.max())}
    return output
