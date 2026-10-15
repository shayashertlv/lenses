"""Frozen WP3b cameras to arview.cameras/1. No fit, material or model changes."""
import numpy as np

from .blender_camera import as_camera, as_frame, axes


def explicit_camera(cam, frame, shape_hw, *, name, background, size_px=1280):
    """Asset-local mm -> camera metres, preserving roll, off-axis eye and pixel centres.

    Orthographic views use a distant pinhole (1e4 frame extents). This is an explicit approximation,
    not a camera refit; the maximum perspective error is reported over the normalized unit box.
    """
    cam, frame = as_camera(cam), as_frame(frame)
    h, w = shape_hw
    if not (64 <= size_px <= 1280 and h > 0 and w > 0 and frame.extent > 0 and cam.scale > 0
            and cam.perspective >= 0):
        raise ValueError("invalid camera size, scale, frame or perspective")
    scale = min(1., size_px / max(h, w))
    width, height = round(w * scale), round(h * scale)
    if min(width, height) < 64:
        raise ValueError("photo aspect ratio produces a render side below the AR minimum of 64 pixels")
    right, up, toward, eu, ev = axes(cam)
    k = cam.perspective if cam.perspective > 1e-9 else 1e-4
    eye = (np.asarray(frame.center) + frame.extent *
           (toward / k + cam.eye_x * right + cam.eye_y * up)) / 1000
    rotation = np.asarray([eu, ev, toward])
    view = np.eye(4)
    view[:3, :3], view[:3, 3] = rotation, -rotation @ eye
    distance, extent = frame.extent / (1000 * k), frame.extent / 1000
    spec = {"id": name, "width": width, "height": height, "background": background,
            "view": view.ravel().tolist(), "fx": cam.scale * scale / k, "fy": cam.scale * scale / k,
            "cx": (cam.center_x + .5) * scale - .5, "cy": (cam.center_y + .5) * scale - .5,
            "near": max(distance * .001, distance - 10 * extent, 1e-6), "far": distance + 10 * extent,
            "meta": {"source_camera": cam.to_dict(), "frame": frame.to_dict(), "native_shape": [h, w],
                     "pixel_scale": scale, "orthographic_approximation": cam.perspective <= 1e-9}}
    if cam.perspective <= 1e-9:
        radius = np.sqrt(3) / 2
        bound = cam.scale * scale * (radius + np.hypot(cam.eye_x, cam.eye_y)) * k * radius / (1 - k * radius)
        spec["meta"].update(note="Perspective at or below 1e-9 approximated by a distant pinhole, 10000 frame extents away.",
                            effective_perspective=k, unit_frame_box_error_bound_px=float(bound))
    return spec
