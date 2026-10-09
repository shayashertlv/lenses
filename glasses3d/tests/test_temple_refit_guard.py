"""An unmeasured front contour cannot authorize a camera refit."""
from dataclasses import replace
import math

import pytest

from glasses3d.g3d_host import cameras as C, relief_views as RV
from glasses3d.tests.fixtures.camera_evidence import make_job


@pytest.mark.parametrize('front_start,front_end,keep', [
    (0.2, 0.25, True), (0.2, 0.4, False),
    (float('inf'), float('inf'), False), (float('inf'), 0.1, False),
    (0.2, float('inf'), False), (float('nan'), 0.1, False),
    (0.2, float('nan'), False),
])
def test_refit_requires_measured_front_guard(monkeypatch, front_start, front_end, keep):
    job, model = make_job(35., 1.)
    base = job.base['02']
    proposed = replace(base, yaw=base.yaw + .1)
    monkeypatch.setattr(C, 'powell', lambda *args, **kwargs: (proposed, .01, 1))
    readings = iter((front_start, front_end))
    monkeypatch.setattr(RV, 'front_contour_mm', lambda *args, **kwargs: next(readings))
    camera, info = RV.refit_camera(job, model, '02', base, {1: 0., -1: 0.})
    assert info['kept'] == keep
    assert info['front_guard_measured'] == (math.isfinite(front_start) and math.isfinite(front_end))
    if keep:
        assert camera == proposed
    else:
        # The bounded seed round-trips through pack/unpack, within float precision.
        for key, value in base.to_dict().items():
            assert camera.to_dict()[key] == pytest.approx(value, abs=1e-10)
