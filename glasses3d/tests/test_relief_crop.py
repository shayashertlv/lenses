"""Independent cropped-box controls; no Prada photos, geometry or gate changes."""
from dataclasses import replace

import numpy as np
import pytest

from glasses3d.g3d_host import camera as CM, cameras as C, raster
from glasses3d.g3d_host.relief_crop import CroppedPhotoLevel
from glasses3d.tests.fixtures.camera_evidence import FACES


def scene_and_camera(shift):
    vertices=np.array([[x,y,z] for x in (-32.,32.) for y in (-13.,13.) for z in (-8.,8.)])
    frame=CM.NormFrame((0.,0.,0.),80.)
    scene=raster.get_scene(vertices,FACES,frame)
    cam=CM.Camera(23.,17.,-4.,.2,280.,100.+shift[0],80.+shift[1])
    return scene,cam,(160,200)


@pytest.mark.parametrize('shift',[(-55,0),(55,0),(0,-65),(0,65),(-45,-45),(45,45)])
def test_true_cropped_geometry_has_no_outside_penalty(shift):
    scene,cam,shape=scene_and_camera(shift)
    mask=scene.render(cam,shape,1)['mask']
    ordinary=C.Level(mask,1000)
    cropped=CroppedPhotoLevel(mask,1000)
    assert ordinary.outside_share(scene,cam) > .05
    assert ordinary.loss(scene,cam) > .001
    assert cropped.loss(scene,cam) == 0.
    assert cropped.roi == (0,0,shape[1],shape[0])
    assert not cropped.edge[0].any() and not cropped.edge[-1].any()
    assert not cropped.edge[:,0].any() and not cropped.edge[:,-1].any()
    # Offsets make real visible boundaries disagree; cropping must not forgive that.
    assert cropped.loss(scene,replace(cam,center_y=cam.center_y+4)) > 0


def test_uncropped_unit_stride_scores_are_identical():
    scene,cam,shape=scene_and_camera((0,0))
    cam=replace(cam,scale=160.)
    mask=scene.render(cam,shape,1)['mask']
    ordinary=C.Level(mask,1000,include_xyxy=(0,0,shape[1],shape[0]))
    cropped=CroppedPhotoLevel(mask,1000)
    shifted=scene.render(replace(cam,center_x=cam.center_x+3),shape,1)['mask']
    assert cropped.score(shifted) == ordinary.score(shifted)


def test_viewport_cut_is_not_an_object_contour():
    photo=np.zeros((80,100),bool);photo[20:60,:45]=True
    level=CroppedPhotoLevel(photo,1000)
    assert not level.edge[:,0].any()
    assert level.edge[20,20] and level.edge[40,44]
    # Removing visible foreground still has a cost, including at the cut.
    wrong=photo.copy();wrong[:,0:8]=False
    assert level.score(wrong)[0] > 0


def test_fully_covered_viewport_does_not_claim_a_zero_contour_score():
    photo=np.ones((80,100),bool)
    level=CroppedPhotoLevel(photo,1000)
    assert not level.edge.any()
    assert level.score(photo)[2] > 0
