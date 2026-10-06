"""A1 checks of a print UV layout (WP5_DESIGN §6.1, §8 A1). numpy only.

- **overlaps**: atlas texel centres covered by more than one triangle (strict interiors, so shared edges never count
  twice), counted on the final atlas at its density;
- **flips**: triangles whose signed UV area (u right, v down) is >= 0 (every chart is laid out seen from outside, so an
  outward counter-clockwise face has a NEGATIVE area); zero-area triangles are counted separately;
- **anisotropy** p95 (area-weighted) of sigma_max / sigma_min of the 3D -> UV map;
- **density** p1 (area-weighted) of sigma_min in texels per mm, as a share of the target density;
- **mirror partners**: index identity (B's chart-local uv = A's with u negated, at every corresponding corner).

Criteria (A1): 0 overlaps, 0 flips, anisotropy p95 <= 2.0, density p1 >= 0.6 x target, partners index-identical.
"""
from __future__ import annotations

import numpy as np

from . import strip as SU
from .charts import signed_area, texel_pairs

ANISO_P95_MAX = 2.0
DENSITY_P1_MIN = 0.6


def overlap_texels(tri_tx: np.ndarray) -> tuple[int, int]:
    """(covered texels, texels covered more than once) of triangles in atlas texel coordinates."""
    r, c, _k = texel_pairs(tri_tx)
    if not len(r):
        return 0, 0
    key = r * (np.int64(1) << 31) + c
    _u, cnt = np.unique(key, return_counts=True)
    return int(len(cnt)), int((cnt > 1).sum())


def a1_report(V: np.ndarray, F: np.ndarray, faces: np.ndarray, uv_tx: np.ndarray, tx_target: float, *,
              partners: dict | None = None) -> dict:
    """The A1 report of a layout: ``faces`` global ids, ``uv_tx`` (K, 3, 2) atlas texel coordinates, ``tx_target`` the
    target density (texels / mm); ``partners`` = {name: {'pass': bool, 'max_abs_texel': float, ...}} from the
    layout's own mirror check."""
    auv = signed_area(uv_tx)
    sig, a3, _ = SU.distortion(V, F, faces, uv_tx[..., 0], uv_tx[..., 1])
    an = sig[:, 0] / np.maximum(sig[:, 1], 1e-12)
    covered, overl = overlap_texels(uv_tx)
    zero = np.abs(auv) < 1e-9
    flips = (auv >= 0) & ~zero
    an_p = SU.wquant(an, a3, [0.5, 0.95, 0.99])
    dens_p1 = SU.wquant(sig[:, 1], a3, [0.01])[0]
    rep = {"faces": int(len(faces)), "texels_covered": covered, "overlap_texels": overl,
           "flipped_faces": int(flips.sum()), "flipped_area_mm2": float(a3[flips].sum()),
           "zero_area_faces": int(zero.sum()),
           "anisotropy_p50_p95_p99": [float(x) for x in an_p],
           "density_p1_tx_per_mm": float(dens_p1), "density_target_tx_per_mm": float(tx_target),
           "density_p1_share": float(dens_p1 / tx_target) if tx_target > 0 else None,
           "area_scale_p1_p50_p99": [float(x) for x in SU.wquant(sig[:, 0] * sig[:, 1] / tx_target ** 2, a3,
                                                                  [0.01, 0.5, 0.99])],
           "partners": partners or {}}
    crit = {"overlaps": overl == 0, "flips": int(flips.sum()) == 0 and int(zero.sum()) == 0,
            "anisotropy_p95": bool(an_p[1] <= ANISO_P95_MAX),
            "density_p1": bool(dens_p1 >= DENSITY_P1_MIN * tx_target),
            "mirror_index_identical": all(p.get("pass") for p in (partners or {}).values())}
    rep["criteria"] = {k: bool(v) for k, v in crit.items()}
    rep["pass"] = bool(all(crit.values()))
    return rep
