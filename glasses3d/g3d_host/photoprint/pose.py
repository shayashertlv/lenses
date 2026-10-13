"""Body points on the model's surface (WP5_DESIGN §5.2): ``points`` (texels) and ``bary3`` (compose).

Ported from the scratch `geomlib.points`; PORT_REVIEW section 10. The registration state of step 0 and the surface S'
it implied (cameras, hinges and per-region parallax: ``Pose``, ``Deformer``, ``pack`` / ``unpack``, ``cam_delta``,
``hinge_matrix``) were deleted on 2026-10-13 with the step-0 experiment; see git tag review-20261013-pre-cleanup.
"""
from __future__ import annotations

import numpy as np

from .inputs import face_normals


def points(V: np.ndarray, F: np.ndarray, face: np.ndarray, bary: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Positions (mm) and outward unit face normals of body samples on the surface with vertices ``V``."""
    tri = V[F[face]]
    P = np.einsum("kj,kjd->kd", bary, tri)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return P, n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)


def bary3(b2: np.ndarray) -> np.ndarray:
    """open3d primitive uvs (b0, b1) -> full barycentrics (1 - b0 - b1, b0, b1)."""
    b = np.asarray(b2, np.float64)
    return np.stack([1 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1)


__all__ = ["points", "bary3", "face_normals"]
