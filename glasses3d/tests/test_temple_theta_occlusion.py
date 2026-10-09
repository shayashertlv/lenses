"""An angle hidden across its entire allowed grid has no silhouette evidence."""
import itertools

import numpy as np
import pytest

from glasses3d.g3d_host import relief_rules as RR, relief_views as RV
from glasses3d.tests.fixtures.occlusion_evidence import make_job


@pytest.mark.parametrize('side,yaw,visible', list(itertools.product((1, -1), (60., 78.), (False, True))))
def test_hinge_observability_agrees_with_complete_scene(side, yaw, visible):
    job, model, cam = make_job(side, yaw, visible)
    photo = job.matte('02')
    assert not any(edge.any() for edge in (photo[0], photo[-1], photo[:, 0], photo[:, -1]))
    changed, visible_pixels = [], []
    for angle in np.arange(RR.THETA_RANGE_DEG[0], RR.THETA_RANGE_DEG[1]+1e-9, RR.THETA_GRID_DEG):
        render = RV.render_model(job, model, '02', cam, {side: 0., -side: float(angle)}, points=False)
        changed.append(np.count_nonzero(render['mask'] != photo))
        visible_pixels.append(np.count_nonzero(render['mask'] & (render['tag'] == -side)))
    result = RV.fit_theta(job, model, '02', cam, {1: 0., -1: 0.}, sides=(-side,))[-side]
    if visible:
        assert max(changed) > 0 and max(visible_pixels) > 0
        assert result['observed']
        fitted = RV.render_model(job, model, '02', cam, {side: 0., -side: result['theta']}, points=False)
        assert np.array_equal(fitted['mask'], photo)
    else:
        assert max(changed) == max(visible_pixels) == 0
        assert not result['observed'], 'An arm with no image effect cannot supply an observed hinge angle'
        assert result['theta'] == 0.


@pytest.mark.parametrize('side,yaw,visible', list(itertools.product((1, -1), (66., 82.), (False, True))))
def test_occlusion_with_nonzero_fixed_pose_and_start(side, yaw, visible):
    """Changing one arm must retain the other arm's actual input pose."""
    job, model, cam = make_job(side, yaw, visible)
    truth = {side: 3.4, -side: 2.7}
    job.truth_theta['02'] = truth
    photo = job.matte('02')
    initial = {side: 3.4, -side: 6.0}
    changed = []
    for angle in np.arange(RR.THETA_RANGE_DEG[0], RR.THETA_RANGE_DEG[1]+1e-9, RR.THETA_GRID_DEG):
        pose = {side: truth[side], -side: float(angle)}
        mask = RV.render_model(job, model, '02', cam, pose, points=False)['mask']
        changed.append(np.count_nonzero(mask != photo))
    result = RV.fit_theta(job, model, '02', cam, initial, sides=(-side,))[-side]
    assert initial == {side: 3.4, -side: 6.0}
    if visible:
        assert max(changed) > 0 and result['observed']
        assert result['theta'] == pytest.approx(truth[-side], abs=0.3)
    else:
        assert max(changed) == 0
        assert not result['observed']
        assert result['grid_range'] == 0.
        assert result['theta'] == initial[-side]
