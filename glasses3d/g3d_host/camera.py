"""Photo camera model and the model-to-normalized frame (ported from automation/reconstruction/camera.py and
automation/bsa/core.py, WP3b; see glasses3d/plan/archive/20261019/PORT_REVIEW.md section 9).

A ``Camera`` maps NORMALIZED model points (``NormFrame.to_norm`` of millimetres) to NATIVE photo pixels:

- basis (normalized frame): right = (cy, 0, -sy), up = (-sp sy, cp, -sp cy), toward = (cp sy, sp, cp cy);
- yaw 0 looks from +Z (the front photo), +90 from +X (the glasses' own LEFT side), 180 from -Z (rear);
  positive pitch = the camera is above the glasses looking down (+90 = straight down, a top view);
- for camera coordinates (X, Y, Z) = (P.right, P.up, P.toward): depth = 1 - perspective Z,
  (x, y) = (X - eye_x, Y - eye_y) / depth. Optional eye_x/eye_y are lateral camera translations in normalized
  extents before roll; they default to zero and are omitted from historical camera dictionaries.
  u = cx + s (cr x - sr y), v = cy - s (sr x + cr y). ``perspective`` = 1 / axial camera distance in NormFrame extents;
  0 is orthographic. Integer (u, v) is a pixel CENTRE.

The field order and the arithmetic of ``project`` are exactly those of ``reconstruction.camera``, so cameras fitted by
the old code (pickles, JSON) load and project identically (``load_camera_pickle``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import pickle
from pathlib import Path

import numpy as np

CAMERA_FIELDS = ("yaw", "pitch", "roll", "perspective", "scale", "center_x", "center_y")
EYE_FIELDS = ("eye_x", "eye_y")


@dataclass(frozen=True)
class Camera:
    yaw: float
    pitch: float
    roll: float
    perspective: float
    scale: float
    center_x: float
    center_y: float
    # Lateral eye coordinates along the pre-roll right/up axes, in NormFrame extents.
    # Zero is the historical camera whose optical axis passes through frame.center.
    eye_x: float = 0.0
    eye_y: float = 0.0

    def to_dict(self) -> dict:
        result = asdict(self)
        if self.eye_x == 0.0 and self.eye_y == 0.0:
            for key in EYE_FIELDS:
                del result[key]
        return result

    @staticmethod
    def from_dict(d: dict) -> "Camera":
        if any(isinstance(d.get(k), bool) or not math.isfinite(float(d.get(k, 0.0))) for k in EYE_FIELDS):
            raise ValueError("lateral camera offsets must be finite numbers")
        return Camera(**{k: float(d[k]) for k in CAMERA_FIELDS},
                      **{k: float(d.get(k, 0.0)) for k in EYE_FIELDS})


def camera_from_dict(d: dict) -> Camera:
    return Camera.from_dict(d)


def project(vertices: np.ndarray, camera: Camera) -> np.ndarray:
    """Normalized model points (N, 3) -> native pixel coordinates (N, 2). Raises when anything reaches the near plane
    (depth <= 0.05)."""
    yaw, pitch, roll = np.radians([camera.yaw, camera.pitch, camera.roll])
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right, up, toward = np.array([cy, 0, -sy]), np.array([-sp*sy, cp, -sp*cy]), np.array([cp*sy, sp, cp*cy])
    depth = 1 - camera.perspective * (vertices @ toward)
    if np.any(depth <= 0.05):
        raise ValueError("Geometry crosses the camera near plane")
    x, y = vertices @ right, vertices @ up
    if camera.eye_x or camera.eye_y:
        x, y = x - camera.eye_x, y - camera.eye_y
    x, y = x / depth, y / depth
    cr, sr = math.cos(roll), math.sin(roll)
    return np.column_stack((camera.center_x + camera.scale*(cr*x - sr*y),
                            camera.center_y - camera.scale*(sr*x + cr*y)))


@dataclass(frozen=True)
class NormFrame:
    """Maps MODEL millimetres to the normalized frame the cameras live in."""
    center: tuple[float, float, float]
    extent: float

    def to_norm(self, v_mm: np.ndarray) -> np.ndarray:
        return (np.asarray(v_mm, float) - np.asarray(self.center)) / self.extent

    def to_mm(self, v_norm: np.ndarray) -> np.ndarray:
        return np.asarray(v_norm, float) * self.extent + np.asarray(self.center)

    def to_dict(self) -> dict:
        return {"center": list(self.center), "extent": self.extent}

    @staticmethod
    def from_dict(d: dict) -> "NormFrame":
        return NormFrame(tuple(float(x) for x in d["center"]), float(d["extent"]))

    @staticmethod
    def from_bbox(V_mm: np.ndarray) -> "NormFrame":
        """Centre = bounding-box centre, extent = the largest bounding-box side (the bsa/modeler convention)."""
        V_mm = np.asarray(V_mm, float)
        lo, hi = V_mm.min(0), V_mm.max(0)
        return NormFrame(tuple(float(x) for x in (lo + hi) / 2), float((hi - lo).max()))


def px_per_mm(camera: Camera, frame: NormFrame) -> float:
    """Image pixels per model millimetre at the NormFrame centre depth (see ``cameras.px_per_mm_at`` elsewhere)."""
    return camera.scale / frame.extent


def project_mm(v_mm: np.ndarray, camera: Camera, frame: NormFrame) -> np.ndarray:
    return project(frame.to_norm(v_mm), camera)


# ----------------------------------------------------------------------------- legacy pickles
_LEGACY_CLASSES = {("reconstruction.camera", "Camera"): Camera, ("bsa.core", "NormFrame"): NormFrame}


class _LegacyUnpickler(pickle.Unpickler):
    """Reads pickles written by automation/ code without importing it: only the two plain value classes are mapped,
    and every other global is refused (a pickle can otherwise execute arbitrary code)."""

    def find_class(self, module: str, name: str):
        cls = _LEGACY_CLASSES.get((module, name))
        if cls is None:
            raise pickle.UnpicklingError(f"refusing to load global {module}.{name}")
        return cls


def load_camera_pickle(path: str | Path) -> Camera:
    """A ``reconstruction.camera.Camera`` pickle (e.g. the prototype's cam_*.pkl) as a glasses3d ``Camera``."""
    with open(path, "rb") as f:
        obj = _LegacyUnpickler(f).load()
    if not isinstance(obj, Camera):
        raise TypeError(f"{path} does not hold a Camera (got {type(obj).__name__})")
    missing = [k for k in CAMERA_FIELDS if k not in obj.__dict__]
    if missing:
        raise ValueError(f"{path}: camera pickle lacks fields {missing}")
    return Camera.from_dict(obj.__dict__)
