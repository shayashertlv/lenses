"""Independent hinge/camera truth: explicit Rodrigues matrices, no pose helper.

The occlusion controls reproduce dc4cdec's analytic opaque boxes, including its
held-out yaws and nonzero fixed-other pose. They contain no product evidence.
"""
from __future__ import annotations

from dataclasses import replace
import math

import numpy as np

from glasses3d.g3d_host.camera import Camera, NormFrame
from glasses3d.g3d_host.fitmesh import FitMesh
from glasses3d.g3d_host import raster

FACES = np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],
                  [2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]], dtype=np.int64)
RECOVERY_CASES = (
    {"name": "positive_angled", "view": "angled", "yaw":34., "pitch":22., "roll":1., "perspective":.12,
     "theta": {"+1":7.2, "-1":-3.4}},
    {"name": "negative_angled", "view": "angled", "yaw":-38., "pitch":28., "roll":-1., "perspective":.08,
     "theta": {"+1":-2.4, "-1":9.1}},
    {"name": "top", "view": "top", "yaw":8., "pitch":68., "roll":2., "perspective":.10,
     "theta": {"+1":5.6, "-1":10.3}},
)


def box(xs, ys, zs):
    return np.array([[x,y,z] for x in xs for y in ys for z in zs], dtype=float), FACES.copy()


def combine(parts):
    v, f, offset = [], [], 0
    for vertices, faces in parts:
        v.append(vertices)
        f.append(faces + offset)
        offset += len(vertices)
    return np.vstack(v), np.vstack(f)


def pack(front, plus, minus, frame=None):
    vertices, faces, part_ids, offset = [], [], [], 0
    for i, (v,f) in enumerate((front, plus, minus)):
        vertices.append(v)
        faces.append(f+offset)
        part_ids.extend([i]*len(f))
        offset += len(v)
    v = np.vstack(vertices)
    names = ["Asymmetric front", "Unequal plus arm", "Unequal minus arm"]
    mesh = FitMesh(v, np.vstack(faces), np.array(part_ids), names, names, [None]*3,
                   frame or NormFrame.from_bbox(v))
    return mesh, {names[0]:"frame", names[1]:"temple", names[2]:"temple"}


def truth_pose(mesh, axes, angles):
    """Rotate each arm with an explicit Rodrigues matrix about its signed axis.

    +theta opens the tip outwards on both sides. The front's vertices are copied
    unchanged; the two arms deliberately do not share a geometric template.
    """
    vertices = mesh.V.copy()
    unit_y = np.array([0.,1.,0.])
    cross = np.array([[0.,0.,1.],[0.,0.,0.],[-1.,0.,0.]])
    for side, part in ((1,1),(-1,2)):
        key = "+1" if side == 1 else "-1"
        angle = math.radians(-side*float(angles.get(key, angles.get(side, 0.))))
        matrix = math.cos(angle)*np.eye(3) + (1-math.cos(angle))*np.outer(unit_y,unit_y) + math.sin(angle)*cross
        axis = axes[key]
        axis = np.array([axis[0],0.,axis[1]])
        selected = np.unique(mesh.F[mesh.part == part])
        vertices[selected] = (vertices[selected]-axis) @ matrix.T + axis
    return replace(mesh, V=vertices)


def recovery_case(case):
    front = combine([box((-64,66),(-12,15),(-5,3)), box((-60,-22),(15,22),(-7,2)),
                     box((14,45),(15,19),(-5,6)), box((-9,3),(-24,-12),(-2,5))])
    # Different widths/heights, plan bends and ear hooks identify the two arms.
    plus = combine([box((63,68),(3,13),(-80,-8)), box((65,72),(1,11),(-119,-78)),
                    box((62,69),(-17,5),(-144,-116))])
    minus = combine([box((-68,-63),(1,14),(-68,-8)), box((-74,-65),(-1,10),(-109,-66)),
                     box((-70,-64),(-25,2),(-137,-107))])
    mesh, roles = pack(front, plus, minus)
    axes = {"+1":[65.,-8.], "-1":[-65.,-8.]}
    shape = (600,840)
    camera = raster.view_camera(mesh.frame, case["yaw"], case["pitch"], 2.5, shape,
                                roll=case["roll"], perspective=case["perspective"])
    truth = truth_pose(mesh, axes, case["theta"])
    fg = truth.scene().render(camera, shape)["mask"]
    initial = replace(camera, yaw=camera.yaw+1.2, pitch=camera.pitch-1., scale=camera.scale*1.01,
                      center_x=camera.center_x+2., center_y=camera.center_y-2.)
    return {"mesh":mesh, "roles":roles, "axes":axes, "fg":fg, "camera":camera, "initial_camera":initial,
            "theta":dict(case["theta"]), "view":case["view"], "shape":shape,
            "pivot":mesh.front_pivot((mesh.names[0],))}


def occlusion_case(side, yaw, visible, nonzero=False):
    front = box((-64,64),(-18,14),(-9,4))
    near = box((63,74),(150,160) if visible else (-60,60),(-250,80))
    far = box((-67,-64),(-4,4),(-145,-25))
    near[0][:,0] *= side
    far[0][:,0] *= side
    if side < 0:
        near = (near[0], near[1][:,::-1])
        far = (far[0], far[1][:,::-1])
    plus, minus = (near, far) if side > 0 else (far, near)
    bounds = box((-74,74),(-60,160),(-250,80))[0]
    frame = NormFrame.from_bbox(np.vstack((front[0], near[0], far[0], bounds)))
    mesh, roles = pack(front,plus,minus,frame)
    axes = {"+1":[64.6,-10.1],"-1":[-64.6,-10.1]}
    shape = (1000,1400)
    camera = raster.view_camera(frame,side*yaw,12.,2.5,shape,perspective=0.,center_mm=np.array([0.,40.,-90.]))
    near_key, far_key = ("+1","-1") if side > 0 else ("-1","+1")
    theta = {near_key:3.4,far_key:2.7} if nonzero else {near_key:0.,far_key:0.}
    initial = {near_key:3.4,far_key:6.0} if nonzero else dict(theta)
    fg = truth_pose(mesh,axes,theta).scene().render(camera,shape)["mask"]
    return {"mesh":mesh,"roles":roles,"axes":axes,"camera":camera,"fg":fg,"shape":shape,
            "theta":theta,"initial_theta":initial,"far_key":far_key,"near_key":near_key,
            "far_side":-side,"view":"angled","pivot":mesh.front_pivot((mesh.names[0],))}
