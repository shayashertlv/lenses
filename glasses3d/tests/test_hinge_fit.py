"""Independent known-opening and whole-scene occlusion tests, declared before fitting."""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from glasses3d.tests.fixtures.hinge_fit_known import RECOVERY_CASES, occlusion_case, recovery_case, truth_pose


def iou(a,b):
    return float(np.count_nonzero(a&b)/max(1,np.count_nonzero(a|b)))


@pytest.mark.parametrize("case", RECOVERY_CASES, ids=[c["name"] for c in RECOVERY_CASES])
def test_joint_camera_and_openings_against_independent_truth(case):
    from glasses3d.g3d_host import hinge_fit
    fixture = recovery_case(case)
    before = fixture["mesh"].V.copy()
    result = hinge_fit.refine(fixture["mesh"],fixture["roles"],fixture["axes"],fixture["fg"],
                              fixture["initial_camera"],fixture["view"],fixture["pivot"],
                              initial_theta={"+1":0.,"-1":0.})
    assert np.array_equal(fixture["mesh"].V,before)
    rendered = result["mesh"].scene().render(result["camera"],fixture["shape"])["mask"]
    assert iou(rendered,fixture["fg"]) >= .98
    for key, expected in fixture["theta"].items():
        assert result["theta_observed"][key], (key,result)
        assert result["theta_deg"][key] == pytest.approx(expected,abs=.8)
    assert abs(result["camera"].yaw-fixture["camera"].yaw) <= 1.
    assert abs(result["camera"].pitch-fixture["camera"].pitch) <= 1.


@pytest.mark.parametrize("side,yaw,visible", list(itertools.product((1,-1),(60.,78.),(False,True))))
def test_hidden_arm_has_no_whole_scene_evidence(side,yaw,visible):
    _occlusion_check(side,yaw,visible,False)


@pytest.mark.parametrize("side,yaw,visible", list(itertools.product((1,-1),(66.,82.),(False,True))))
def test_hidden_arm_keeps_nonzero_start_and_other_arm_pose(side,yaw,visible):
    _occlusion_check(side,yaw,visible,True)


def _occlusion_check(side,yaw,visible,nonzero):
    from glasses3d.g3d_host import hinge_fit
    fixture = occlusion_case(side,yaw,visible,nonzero)
    initial = dict(fixture["initial_theta"])
    fg = fixture["fg"]
    assert not any(edge.any() for edge in (fg[0],fg[-1],fg[:,0],fg[:,-1]))
    # The original regression's complete [-4,12] degree, 1-degree grid; every
    # trial retains the other arm's actual nonzero pose, never a reset to zero.
    changed,visible_pixels = [],[]
    for angle in np.arange(-4.,12.00001,1.):
        theta = dict(fixture["theta"])
        theta[fixture["far_key"]] = float(angle)
        posed = truth_pose(fixture["mesh"],fixture["axes"],theta)
        drawing = posed.scene().render(fixture["camera"],fixture["shape"])
        changed.append(np.count_nonzero(drawing["mask"] != fg))
        faces = drawing["face_id"][drawing["mask"]]
        far_part = 1 if fixture["far_side"] == 1 else 2
        visible_pixels.append(np.count_nonzero(posed.part[faces] == far_part))
    result = hinge_fit.refine(fixture["mesh"],fixture["roles"],fixture["axes"],fg,fixture["camera"],
                              fixture["view"],fixture["pivot"],initial_theta=initial,
                              fit_camera=False,sides=(fixture["far_side"],),
                              angle_bounds={fixture["far_key"]:(-4.,12.)})
    assert initial == fixture["initial_theta"]
    assert result["theta_deg"][fixture["near_key"]] == fixture["theta"][fixture["near_key"]]
    if visible:
        assert max(changed) > 0 and max(visible_pixels) > 0
        assert result["theta_observed"][fixture["far_key"]]
        assert result["theta_deg"][fixture["far_key"]] == pytest.approx(fixture["theta"][fixture["far_key"]],abs=.3)
    else:
        assert max(changed) == max(visible_pixels) == 0
        assert not result["theta_observed"][fixture["far_key"]]
        assert result["theta_deg"][fixture["far_key"]] == initial[fixture["far_key"]]


def test_invalid_near_plane_trial_does_not_discard_valid_input():
    """The permitted full opening domain includes a non-renderable trial.

    The actual photo is the exact authored pose; a bad search trial must not
    turn that valid input into a failed photo fit.
    """
    from glasses3d.g3d_host import hinge_fit
    from glasses3d.g3d_host.camera import Camera
    from glasses3d.tests.fixtures.hinge_fit_known import box, pack
    mesh, roles = pack(box((-75,75),(-10,10),(-1,0)),
                       box((73,75),(-2,2),(-150,0)),
                       box((-75,-73),(-2,2),(-150,0)))
    axes = {"+1":[74.,0.],"-1":[-74.,0.]}
    camera, shape = Camera(45,0,0,.6,250,240,160), (320,480)
    fg = mesh.scene().render(camera,shape)["mask"]
    before = mesh.V.copy()
    crossing = truth_pose(mesh,axes,{"+1":135.,"-1":0.})
    with pytest.raises(ValueError,match="near plane"):
        crossing.scene().render(camera,shape)
    result = hinge_fit.refine(mesh,roles,axes,fg,camera,"angled",mesh.front_pivot(),
                              fit_camera=False,sides=(1,))
    assert result["theta_deg"] == {"+1":0.,"-1":0.}
    assert np.array_equal(mesh.V,before)
    assert np.array_equal(result["mesh"].scene().render(result["camera"],shape)["mask"],fg)
