"""Geometric measurements from projected ridge lines and explicit reference planes.

These helpers make the reference-plane/camera assumptions explicit. They do not
calibrate cameras, choose edge identities, infer topology or change gate scores.
"""
from __future__ import annotations
import numpy as np
from . import raster


def lift_pixels_to_plane(pixels, camera, frame, normal, constant):
    """Intersect pixel rays with normal.dot(world_mm) == constant.

    Refuse parallel rays or intersections behind the ray origin. The plane can
    extend beyond a mesh: this also works for virtual crease intersections.
    """
    pixels=np.asarray(pixels,float).reshape(-1,2)
    normal=np.asarray(normal,float)
    if normal.shape!=(3,) or not np.isfinite(normal).all() or np.linalg.norm(normal)==0:
        raise ValueError('normal must be a finite nonzero 3-vector')
    if not np.isfinite(pixels).all() or not np.isfinite(constant):raise ValueError('nonfinite plane or pixels')
    origins,directions=raster.camera_rays(camera,pixels[:,0],pixels[:,1],z0=3.)
    origins=frame.to_mm(origins);directions=directions*frame.extent
    denominator=directions@normal
    if np.any(np.abs(denominator)<=1e-12*np.linalg.norm(normal)*np.linalg.norm(directions,axis=1)):
        raise ValueError('ray is parallel to reference plane')
    distance=(constant-origins@normal)/denominator
    if np.any(distance<0):raise ValueError('reference plane is behind ray origin')
    return origins+distance[:,None]*directions


def intersect_axis_with_image_line(point, axis, p0, p1, camera, frame):
    """Return point + t*axis where its projection meets the specified image line.

    t is in millimetres for a unit axis. Homogeneous projection keeps perspective
    exact. Degenerate or unobservable image intersections are refused.
    """
    point,axis=np.asarray(point,float),np.asarray(axis,float)
    p0,p1=np.asarray(p0,float),np.asarray(p1,float)
    if point.shape!=(3,) or axis.shape!=(3,) or p0.shape!=(2,) or p1.shape!=(2,):
        raise ValueError('expected world 3-vectors and pixel 2-vectors')
    if not all(np.isfinite(a).all() for a in (point,axis,p0,p1)) or np.linalg.norm(axis)==0:
        raise ValueError('finite inputs and a nonzero axis required')
    line=np.cross([*p0,1.],[*p1,1.]);length=float(np.linalg.norm(line[:2]))
    if length<1e-12:raise ValueError('image line has zero length')
    line/=length
    right,up,toward=raster.camera_basis(camera);cr,sr=np.cos(np.radians(camera.roll)),np.sin(np.radians(camera.roll))
    def homogeneous(p,is_direction):
        x,y,z=p@right,p@up,p@toward
        depth=(0. if is_direction else 1.)-camera.perspective*z
        return np.array([camera.center_x*depth+camera.scale*(cr*x-sr*y),
                         camera.center_y*depth-camera.scale*(sr*x+cr*y),depth])
    h=homogeneous(frame.to_norm(point),False);dh=homogeneous(axis/frame.extent,True)
    denominator=float(line@dh)
    if abs(denominator)<1e-10:raise ValueError('axis/line intersection is unobservable')
    t=-float(line@h)/denominator
    if h[2]+t*dh[2]<=.05:raise ValueError('intersection crosses camera near plane')
    return {'point_mm':point+t*axis,'axis_distance':t,
            'line_constraint_per_mm':abs(denominator)/(h[2]+t*dh[2])}
