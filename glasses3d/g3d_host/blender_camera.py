"""WP3b photo camera <-> Blender camera (WP2_DESIGN §6), numpy only (host side; g3d/render.py applies the spec).

A WP3b ``Camera{yaw, pitch, roll, perspective k, scale s, center_x, center_y}`` maps NORMALIZED model points
(``NormFrame{C, E}``: (x_mm - C) / E, GLB millimetres) to native pixels (``camera.project``; integer = pixel centre).
The Blender scene is the GLB frame shifted by the bridge-underside origin ``o`` (``mdl_bridge_underside``, mm): the
exporter writes (blender_mm - o) / 1000 with ``export_yup=False`` (g3d/export_glb.py), so blender_mm = glb_mm + o.

Basis (camera.py): right = (cos y, 0, -sin y), up = (-sin p sin y, cos p, -sin p cos y), toward = (cos p sin y,
sin p, cos p cos y); roll: e_u = cos r right - sin r up, e_v = sin r right + cos r up. The Blender camera's local axes
(X right, Y up, looking down -Z) are the columns (e_u, e_v, toward).

- Perspective (k > 0): location = C + o + toward E / k, ``lens = (s / k) 36 / big``, sensor 36 mm.
  Proof: camera coords (X, Y, Z) = (P.e_u, P.e_v, P.toward) E - (0, 0, E/k), so -z_cam = (E/k)(1 - k Z) = (E/k) depth
  and the view-plane coordinate x_cam / -z_cam = k X / depth; Blender maps it to big * lens / 36 pixels.
- Orthographic (k = 0): location = C + o + toward 4E, ``ortho_scale = big E / s``.
- Both: ``sensor_fit = 'AUTO'`` (the sensor and the shift span big = max(W, H)), native W x H, pixel aspect 1,
  ``shift_x = -(cx + 0.5 - W/2) / big``, ``shift_y = (cy + 0.5 - H/2) / big`` (a WP3b pixel centre is Blender's
  edge coordinate minus 0.5).

``project(spec, points)`` is an independent numpy model of Blender's view plane (BKE_camera_params_compute_viewplane +
world_to_camera_view, sensor fit AUTO); the parity tests hold it, and Blender's own ``world_to_camera_view`` and
renders, against ``camera.project_mm`` (WP2_DESIGN §13: analytic <= 0.01 px, rendered <= 0.1 px). ``rays`` inverts it
(pixel centre -> scene-mm ray) for ``pick_surface_point``. ``from_blender`` is the inverse conversion: any sensor-fit
AUTO Blender camera -> a WP3b camera (and NormFrame) that projects exactly as Blender does.

Views (``CANONICAL_VIEWS``, ``framed_view``, ``custom_view``): canonical ORTHOGRAPHIC views framed to the
render-enabled bounding box + 5 % (front, rear, left, right, top, bottom, angled_left, angled_right), and custom
``{yaw, pitch, roll, perspective, target_mm, width_mm}`` views, all expressed as WP3b cameras in a NormFrame, so the
Blender render, the host raster (parts pass, compare) and picking share one camera.
"""
from __future__ import annotations

import math

import numpy as np

from .camera import Camera, NormFrame

SCHEMA = "g3d.blender_camera/1"
SENSOR_MM = 36.0
PERSPECTIVE_EPS = 1e-9
ORTHO_DISTANCE_EXTENTS = 4.0          # orthographic cameras sit 4 E in front of the frame centre (WP2_DESIGN §6)
SHIFT_LIMIT = 10.0                    # Blender's hard range for Camera.shift_x / shift_y
LENS_MIN_MM = 1.0                     # Blender's hard minimum for Camera.lens
FRAME_MARGIN = 0.05                   # canonical views: the projected box + 5 % on every side
MIN_SIDE_PX = 16
# yaw, pitch (WP3b convention: yaw 0 = from +Z (front), +90 = from +X (the glasses' own LEFT side), pitch +90 = above)
CANONICAL_VIEWS: dict[str, tuple[float, float]] = {
    "front": (0.0, 0.0), "rear": (180.0, 0.0), "left": (90.0, 0.0), "right": (-90.0, 0.0),
    "top": (0.0, 90.0), "bottom": (0.0, -90.0), "angled_left": (35.0, 15.0), "angled_right": (-35.0, 15.0)}


def as_camera(cam) -> Camera:
    return cam if isinstance(cam, Camera) else Camera.from_dict(cam)


def as_frame(frame) -> NormFrame:
    return frame if isinstance(frame, NormFrame) else NormFrame.from_dict(frame)


def axes(cam) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(right, up, toward, e_u, e_v) of a WP3b camera, float64 unit vectors in the GLB / scene axes."""
    cam = as_camera(cam)
    yaw, pitch, roll = (math.radians(v) for v in (cam.yaw, cam.pitch, cam.roll))
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right = np.array([cy, 0.0, -sy])
    up = np.array([-sp * sy, cp, -sp * cy])
    toward = np.array([cp * sy, sp, cp * cy])
    cr, sr = math.cos(roll), math.sin(roll)
    return right, up, toward, cr * right - sr * up, sr * right + cr * up


def to_blender(cam, frame, origin_mm=(0.0, 0.0, 0.0), shape_hw=(0, 0)) -> dict:
    """The Blender camera that renders exactly what ``camera.project_mm(points, cam, frame)`` predicts, for a scene
    whose GLB origin sits at ``origin_mm``, at native size ``shape_hw = (H, W)``. Raises ValueError when Blender cannot
    hold it (shift beyond +-10, lens below 1 mm)."""
    cam, frame = as_camera(cam), as_frame(frame)
    H, W = int(shape_hw[0]), int(shape_hw[1])
    if H < 1 or W < 1:
        raise ValueError(f"image shape must be positive, got {shape_hw}")
    _right, _up, toward, e_u, e_v = axes(cam)
    center = np.asarray(frame.center, float) + np.asarray(origin_mm, float)
    E, k, s = float(frame.extent), float(cam.perspective), float(cam.scale)
    if not (E > 0 and s > 0 and k >= 0):
        raise ValueError(f"camera scale {s} and frame extent {E} must be positive, perspective {k} >= 0")
    big = max(W, H)
    shift_x = -(cam.center_x + 0.5 - W / 2.0) / big
    shift_y = (cam.center_y + 0.5 - H / 2.0) / big
    if abs(shift_x) > SHIFT_LIMIT or abs(shift_y) > SHIFT_LIMIT:
        raise ValueError(f"the principal point lies {max(abs(shift_x), abs(shift_y)):.1f} image widths off centre "
                         f"(Blender allows {SHIFT_LIMIT:g}); use a crop nearer the photo's centre")
    rot = np.column_stack([e_u, e_v, toward])
    spec = {"schema": SCHEMA, "sensor_fit": "AUTO", "sensor_mm": SENSOR_MM, "shift_x": float(shift_x),
            "shift_y": float(shift_y), "resolution": [W, H]}
    if k > PERSPECTIVE_EPS:
        dist = E / k
        loc = center + toward * dist
        lens = (s / k) * SENSOR_MM / big
        if lens < LENS_MIN_MM:
            raise ValueError(f"focal length {lens:.3f} mm is below Blender's {LENS_MIN_MM:g} mm minimum")
        spec.update(type="PERSP", lens_mm=float(lens), ortho_scale=None,
                    clip_start=float(max(dist * 0.01, 1e-3)), clip_end=float(dist + 10.0 * E))
    else:
        loc = center + toward * (ORTHO_DISTANCE_EXTENTS * E)
        spec.update(type="ORTHO", lens_mm=None, ortho_scale=float(big * E / s),
                    clip_start=float(E * 0.01), clip_end=float(E * (ORTHO_DISTANCE_EXTENTS + 10.0)))
    m = np.eye(4)
    m[:3, :3] = rot
    m[:3, 3] = loc
    spec["matrix_world"] = m.tolist()
    return spec


def from_blender(spec: dict, origin_mm=(0.0, 0.0, 0.0), frame=None, *, extent: float | None = None
                 ) -> tuple[Camera, NormFrame, tuple[int, int]]:
    """The inverse of ``to_blender``: a Blender camera spec (sensor fit AUTO, pixel aspect 1, no lens-unit or
    sensor surprises) -> (WP3b camera, NormFrame, (H, W)) projecting exactly as Blender does.

    An orthographic camera, or a perspective one whose eye lies on the axis through ``frame``'s centre, keeps
    ``frame``. Otherwise (a perspective eye off that axis: a lateral eye shift is parallax, not a principal-point
    shift) the frame is re-centred on the camera axis with the same extent. Without a frame, the centre is the point
    on the axis nearest the origin marker and the extent ``extent`` (default 100 mm)."""
    if spec.get("sensor_fit", "AUTO") != "AUTO":
        raise ValueError("only sensor_fit AUTO specs can be converted")
    m = matrix_world(spec)
    R, loc = m[:3, :3], m[:3, 3]
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-9) or np.linalg.det(R) < 0:
        raise ValueError("the camera matrix must be a pure rotation (no scale, no mirror)")
    e_u, e_v, toward = R[:, 0], R[:, 1], R[:, 2]
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(toward[1])))))
    yaw = math.degrees(math.atan2(float(toward[0]), float(toward[2])))
    base = Camera(yaw, pitch, 0.0, 0.0, 1.0, 0.0, 0.0)
    right, up = axes(base)[:2]
    roll = math.degrees(math.atan2(-float(e_u @ up), float(e_u @ right)))
    W, H = (int(v) for v in spec["resolution"])
    big = max(W, H)
    origin = np.asarray(origin_mm, float)
    if frame is None:
        E = float(extent or 100.0)
        c = loc - origin
        c = c - toward * float(c @ toward)                       # the axis point nearest the origin marker (GLB mm)
        frame = NormFrame(tuple(float(x) for x in c), E)
    frame = as_frame(frame)
    E = float(frame.extent)
    rel = loc - origin - np.asarray(frame.center, float)
    cx = W / 2.0 - float(spec["shift_x"]) * big - 0.5
    cy = H / 2.0 + float(spec["shift_y"]) * big - 0.5
    if spec["type"] == "PERSP":
        dist = float(rel @ toward)
        if dist <= 0:
            raise ValueError("the frame centre is behind the camera")
        lateral = rel - toward * dist
        if np.linalg.norm(lateral) > 1e-9 * max(E, 1.0):
            frame = NormFrame(tuple(float(x) for x in np.asarray(frame.center, float) + lateral), E)
        k = E / dist
        s = float(spec["lens_mm"]) * big / float(spec["sensor_mm"]) * k
    else:
        k, s = 0.0, big * E / float(spec["ortho_scale"])
        # an orthographic eye off the axis is a principal-point shift: fold it into (cx, cy)
        lateral = rel - toward * float(rel @ toward)
        cx -= s * float(lateral @ e_u) / E
        cy += s * float(lateral @ e_v) / E
    return Camera(yaw, pitch, roll, k, s, cx, cy), frame, (H, W)


def matrix_world(spec: dict) -> np.ndarray:
    return np.asarray(spec["matrix_world"], float).reshape(4, 4)


def _viewplane(spec: dict) -> tuple[float, float, float]:
    """(big = max(W, H), W, H) of the spec's resolution, as floats."""
    W, H = (int(v) for v in spec["resolution"])
    return float(max(W, H)), float(W), float(H)


def project(spec: dict, points_mm, *, return_depth: bool = False):
    """Scene-mm points -> pixel-centre coordinates (N, 2) through the Blender camera ``spec`` (Blender's own view-plane
    arithmetic for sensor fit AUTO, pixel aspect 1). With ``return_depth`` also the distance in front of the camera
    along its axis (mm; <= 0 = behind it)."""
    P = np.asarray(points_mm, float).reshape(-1, 3)
    m = matrix_world(spec)
    R, t = m[:3, :3], m[:3, 3]
    pc = (P - t) @ R                                     # camera-local coordinates (R orthonormal)
    big, W, H = _viewplane(spec)
    depth = -pc[:, 2]
    if spec["type"] == "PERSP":
        f = float(spec["lens_mm"]) / float(spec["sensor_mm"])
        with np.errstate(divide="ignore", invalid="ignore"):
            xv, yv = pc[:, 0] / depth * f, pc[:, 1] / depth * f
    else:
        xv, yv = pc[:, 0] / float(spec["ortho_scale"]), pc[:, 1] / float(spec["ortho_scale"])
    u = W / 2.0 + big * (xv - float(spec["shift_x"])) - 0.5
    v = H / 2.0 - big * (yv - float(spec["shift_y"])) - 0.5
    uv = np.column_stack([u, v])
    return (uv, depth) if return_depth else uv


def rays(spec: dict, uv) -> tuple[np.ndarray, np.ndarray]:
    """Rays through pixel-centre coordinates (N, 2): (origins, unit directions), scene mm. Perspective rays start at
    the camera; orthographic rays on the camera plane."""
    uv = np.asarray(uv, float).reshape(-1, 2)
    big, W, H = _viewplane(spec)
    xv = (uv[:, 0] + 0.5 - W / 2.0) / big + float(spec["shift_x"])
    yv = (H / 2.0 - (uv[:, 1] + 0.5)) / big + float(spec["shift_y"])
    m = matrix_world(spec)
    R, t = m[:3, :3], m[:3, 3]
    if spec["type"] == "PERSP":
        f = float(spec["lens_mm"]) / float(spec["sensor_mm"])
        d = np.column_stack([xv / f, yv / f, -np.ones(len(uv))]) @ R.T
        o = np.repeat(t[None], len(uv), axis=0)
    else:
        sc = float(spec["ortho_scale"])
        o = np.column_stack([xv * sc, yv * sc, np.zeros(len(uv))]) @ R.T + t
        d = np.repeat((-R[:, 2])[None], len(uv), axis=0)
    return o, d / np.linalg.norm(d, axis=1, keepdims=True)


def crop_camera(cam, x0: float = 0.0, y0: float = 0.0, k: float = 1.0) -> Camera:
    """The camera of the crop [x0:, y0:] of a native image resized by ``k`` (= cameras.resized_camera)."""
    cam = as_camera(cam)
    return Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale * k,
                  (cam.center_x - x0 + 0.5) * k - 0.5, (cam.center_y - y0 + 0.5) * k - 0.5)


def _project_norm(cam: Camera, pn: np.ndarray) -> np.ndarray:
    """camera.project without its near-plane refusal (framing decides that itself)."""
    _r, _u, toward, e_u, e_v = axes(cam)
    depth = 1.0 - cam.perspective * (pn @ toward)
    if np.any(depth <= 0.05):
        raise ValueError("the framed geometry reaches the camera's near plane; lower the perspective")
    return np.column_stack([cam.center_x + cam.scale * (pn @ e_u) / depth,
                            cam.center_y - cam.scale * (pn @ e_v) / depth])


def framed_view(yaw: float, pitch: float, points_glb_mm, frame, size_px: int, *, roll: float = 0.0,
                perspective: float = 0.0, margin: float = FRAME_MARGIN, square: bool = False
                ) -> tuple[Camera, tuple[int, int]]:
    """A camera at (yaw, pitch, roll, perspective) whose image holds ``points_glb_mm`` (GLB mm, e.g. the corners of the
    render-enabled boxes) with ``margin`` on every side; the longer image side is ``size_px`` (both when ``square``).
    Returns (camera, (H, W))."""
    frame = as_frame(frame)
    pn = frame.to_norm(np.asarray(points_glb_mm, float).reshape(-1, 3))
    unit = Camera(float(yaw), float(pitch), float(roll), float(perspective), 1.0, 0.0, 0.0)
    uv = _project_norm(unit, pn)
    lo, hi = uv.min(0), uv.max(0)
    w0, h0 = max(float(hi[0] - lo[0]), 1e-9), max(float(hi[1] - lo[1]), 1e-9)
    size_px = int(size_px)
    s = size_px / ((1.0 + 2.0 * margin) * max(w0, h0))
    if square:
        W = H = size_px
    elif w0 >= h0:
        W, H = size_px, max(MIN_SIDE_PX, int(math.ceil(h0 * s * (1.0 + 2.0 * margin))))
    else:
        W, H = max(MIN_SIDE_PX, int(math.ceil(w0 * s * (1.0 + 2.0 * margin)))), size_px
    cx = (W - 1) / 2.0 - s * (lo[0] + hi[0]) / 2.0
    cy = (H - 1) / 2.0 - s * (lo[1] + hi[1]) / 2.0
    return Camera(float(yaw), float(pitch), float(roll), float(perspective), float(s), float(cx), float(cy)), (H, W)


def custom_view(yaw: float, pitch: float, target_glb_mm, width_mm: float, frame, size_px: int, *, roll: float = 0.0,
                perspective: float = 0.0) -> tuple[Camera, tuple[int, int]]:
    """A square ``size_px`` view looking at ``target_glb_mm`` from (yaw, pitch) with ``width_mm`` of model across the
    image at the target's depth. ``perspective`` = frame extent / camera distance (0 = orthographic)."""
    frame = as_frame(frame)
    if not width_mm > 0:
        raise ValueError(f"width_mm must be positive, got {width_mm}")
    tn = frame.to_norm(np.asarray(target_glb_mm, float).reshape(1, 3))
    unit = Camera(float(yaw), float(pitch), float(roll), float(perspective), 1.0, 0.0, 0.0)
    toward = axes(unit)[2]
    depth = 1.0 - float(perspective) * float(tn[0] @ toward)
    if depth <= 0.05:
        raise ValueError("the target is behind or at the camera; lower the perspective")
    size_px = int(size_px)
    s = size_px / float(width_mm) * frame.extent * depth
    cam = Camera(unit.yaw, unit.pitch, unit.roll, unit.perspective, float(s), 0.0, 0.0)
    uv = _project_norm(cam, tn)[0]
    W = H = size_px
    return Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale, float((W - 1) / 2.0 - uv[0]),
                  float((H - 1) / 2.0 - uv[1])), (H, W)


def px_per_mm_at(cam, frame, point_glb_mm) -> float:
    """Image px per model mm at a point (perspective shrinks with depth); = cameras.px_per_mm_at."""
    cam, frame = as_camera(cam), as_frame(frame)
    toward = axes(cam)[2]
    zn = float(frame.to_norm(np.asarray(point_glb_mm, float).reshape(1, 3))[0] @ toward)
    return cam.scale / frame.extent / (1.0 - cam.perspective * zn)


def box_corners(lo, hi) -> np.ndarray:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
