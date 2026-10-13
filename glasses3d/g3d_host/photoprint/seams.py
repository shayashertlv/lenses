"""WP5 step 4b / 7a: seams (WP5_DESIGN §6.4, §6.5, §6.7, A5).

Production uses ``poisson.stitch`` for chart seams after the owner's 2026-10-08
selection. The historical chart-seam helpers (``harmonise``, ``reflect_seams``, ``seam_edges``, ``_inward``,
``_bilinear_atlas`` and their constants), which the production bake no longer called, were deleted on 2026-10-13
with their tests; see git tag review-20261013-pre-cleanup. The text below on ``harmonise`` and the chart-seam
reflection is history.

- **Label seams** (a texel next to a 4-neighbour of a different painting view, inside one chart): a hard seam, its
  band of 2 or 4 texels re-synthesised by PatchMatch (never blended: blending is for registered pairs only). The band
  is kept at 2 texels when, after repair, the seam step ratio (mean |dL| of texel pairs straddling the band / mean |dL|
  of same-label pairs at the same offset outside any band) is <= 3.0 and the band energy (std of the 0.25-1 mm
  band-pass inside the band / in a 4-texel ring around it) >= 0.85; else 4 texels when that passes; else the last
  width tried, reported (``bands``) and warned. **Fix round 2:** the band is 2w - 1 texels thick around the boundary
  (w on each side), so straddling pairs are k = 2w + 1 texels apart (5 at w = 2, 9 at w = 4); fix round 1 derived w
  from one part's band over EVERY part's boundary and used k = 3 for both widths, i.e. pairs inside the repaired band.
  Seams must stay <= 15 % of the projected texels (reported).
- **Chart seams** (A5): every mesh edge (welded positions) whose two faces have different uv at its ends is sampled
  every 0.5 texel; each sample's texel in face A's chart and in face B's chart are a pair of texels of the same 3D
  point (``chart_seam_pairs``; the pairs feed ``gains.level_field`` and ``poisson.stitch``). ``harmonise`` (fix
  round 1: never mixes two photos, deterministic): every texel has a RANK (non-photo 0; photo 1, + 1 when painted by
  its region's anchor, + its donor weight / the largest weight, < 1). In a pair the
  higher rank wins (equal ranks: the lower texel row). A non-photo loser takes the winner's colour; a photo loser
  takes it only when the two differ by dE00 > 3 (a label seam on a chart seam) and then stops being protected; two
  non-photo sides take their mean. A texel in several pairs takes the MEAN of its proposals of the highest priority
  (copy from a winner before a non-photo mean), never "last write wins". **Fix round 2:** copies from winners painted
  by DIFFERENT views are never averaged: with ``source`` (per-texel painting view, -1 for non-photo) given, only the
  proposals of the highest-ranked winner's view are kept (ties: the lowest view index); ``multi_view_means`` counts
  texels whose kept copies still came from two views (0 by construction, asserted in the tests). Edited texels within 3 texels of a changed
  seam texel are feathered toward it. Photo-photo disagreements are no longer averaged (that mixed two unregistered
  views or gain groups). This step sets exactly the seam texels, so A5 is graded off the seam
  (``print_metrics.seam_steps``), with the pre-harmonisation pair agreement reported as a diagnostic.
- **Chart-seam reflection** (step 4 fix 4, ``reflect_seams``, before the harmonisation): where a seam's two sides
  are filled independently, the losing side's fill texels within 6 texels take the winning side's texture reflected
  across the seam (A5 at 1 texel on FT1119: p95 12.65 -> 3.11 dE00 against a within-chart 5.74; A5 passes).
- **Gutters**: every atlas pixel within 8 texels of a texel takes its nearest texel's colour (bilinear and mipmapped
  lookups never read the background), after composition.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from .labels import weld_ids

STEP_MAX = 3.0
BAND_ENERGY_MIN = 0.85
SEAM_SHARE_MAX = 0.15
PAIR_STEP = 0.5
GUTTER = 8


def label_boundary(tex, label: np.ndarray) -> np.ndarray:
    """Texels with a 4-neighbour of a different valid label in the same chart (both labels >= 0)."""
    L = tex.image(label, -9, np.int64)
    C = tex.image(tex.chart, -1, np.int64)
    b = np.zeros(L.shape, bool)
    for dy, dx in ((0, 1), (1, 0)):
        a = L[: L.shape[0] - dy, : L.shape[1] - dx]
        c = L[dy:, dx:]
        ca = C[: C.shape[0] - dy, : C.shape[1] - dx]
        cc = C[dy:, dx:]
        m = (a >= 0) & (c >= 0) & (a != c) & (ca == cc)
        b[: L.shape[0] - dy, : L.shape[1] - dx] |= m
        b[dy:, dx:] |= m
    return b[tex.ii, tex.jj]


def label_band(tex, label: np.ndarray, width: int) -> np.ndarray:
    """The boundary texels dilated to ``width`` texels inside the labelled set."""
    b = label_boundary(tex, label)
    if width <= 1 or not b.any():
        return b
    img = ndimage.binary_dilation(tex.image(b, False, bool), iterations=int(width) - 1)
    return img[tex.ii, tex.jj] & (label >= 0)


def band_tests(tex, img: np.ndarray, label: np.ndarray, band: np.ndarray, tx: float, width: int | None = None,
               part_mask: np.ndarray | None = None) -> dict:
    """Seam step ratio and band energy of a repaired band (``img`` (n, 3) per texel). The step compares texel pairs
    k = 2 w + 1 texels apart along rows and columns (w = the band's ``width`` per side; fix round 2): pairs whose ends
    carry DIFFERENT labels (they straddle the whole repaired band) against pairs with the same label outside any band
    (the texture's own difference at that distance), so the ratio does not depend on the density (a 1-texel neighbour
    baseline shrank 2-3 x at 16 texels/mm, where the photos are upsampled, and the ratio grew with it). Without
    ``width`` it is estimated as band texels / boundary texels on the same part (``part_mask``)."""
    L = tex.image(np.asarray(img, float).mean(-1), np.nan, float)
    lab = tex.image(label, -9, np.int64)
    C = tex.image(tex.chart, -1, np.int64)
    B = tex.image(band, False, bool)
    P = tex.image(label >= 0, False, bool)
    if width is None:
        bnd = label_boundary(tex, label)
        if part_mask is not None:
            bnd = bnd & np.asarray(part_mask, bool)
        width = max(1, int(round(B.sum() / max(2 * int(bnd.sum()), 1)))) if B.any() else 1
    k = 2 * int(width) + 1
    across, inside = [], []
    for dy, dx in ((0, k), (k, 0)):
        sa = (slice(0, L.shape[0] - dy), slice(0, L.shape[1] - dx))
        sb = (slice(dy, None), slice(dx, None))
        a, c = L[sa], L[sb]
        la, lc = lab[sa], lab[sb]
        ok = np.isfinite(a) & np.isfinite(c) & (C[sa] == C[sb]) & (C[sa] >= 0)
        d = np.abs(a - c)
        across.append(d[ok & (la != lc) & (la >= 0) & (lc >= 0)])
        inside.append(d[ok & (la == lc) & (la >= 0) & ~B[sa] & ~B[sb]])
    acr = np.concatenate(across)
    ins = np.concatenate(inside)
    step = float(acr.mean() / max(ins.mean(), 1e-6)) if len(acr) and len(ins) else None
    sup = np.isfinite(L) & P
    Lz = np.where(sup, L, 0.0)
    s1, s2 = 0.25 * tx, 1.0 * tx
    mf = sup.astype(float)
    g1 = ndimage.gaussian_filter(Lz, s1) / np.maximum(ndimage.gaussian_filter(mf, s1), 1e-6)
    g2 = ndimage.gaussian_filter(Lz, s2) / np.maximum(ndimage.gaussian_filter(mf, s2), 1e-6)
    bp = g1 - g2
    ring = ndimage.binary_dilation(B, iterations=4) & ~B & sup
    energy = float(bp[B & sup].std() / max(bp[ring].std(), 1e-6)) if (B & sup).any() and ring.any() else None
    return {"step_ratio": step, "band_energy": energy, "pair_offset_texels": int(k), "width": int(width),
            "pass": bool(step is not None and energy is not None and step <= STEP_MAX and energy >= BAND_ENERGY_MIN)}


def _chart_edge_texels(tex, points: np.ndarray, chart: int) -> np.ndarray:
    """Nearest covered texel centres in the edge point's local 3 x 3 stencil.

    An edge may lie exactly on the empty gutter side of its last texel. Looking
    up only floor(uv) loses that edge, or can pick an unrelated nearby chart.
    Restrict the search to the declared chart and never bridge a wider gap.
    """
    H, W = tex.shape
    base = np.floor(points).astype(np.int64)
    best = np.full(len(points), -1, np.int64)
    distance = np.full(len(points), np.inf)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            x, y = base[:, 0] + dx, base[:, 1] + dy
            inside = (x >= 0) & (x < W) & (y >= 0) & (y < H)
            ids = tex.index[np.clip(y, 0, H - 1), np.clip(x, 0, W - 1)]
            valid = inside & (ids >= 0)
            valid[valid] &= tex.chart[ids[valid]] == chart
            d = (x + 0.5 - points[:, 0]) ** 2 + (y + 0.5 - points[:, 1]) ** 2
            take = valid & (d < distance)
            best[take], distance[take] = ids[take], d[take]
    return best


def chart_seam_pairs(job, layout, tex, step: float = PAIR_STEP) -> np.ndarray:
    """(K, 2) nearest covered texels on either chart to each sampled seam point.

    Seams are welded mesh edges whose faces have different UV endpoints. Texel
    centres lie on either side of the edge; its exact UV can lie in the gutter.
    """
    faces = layout.faces
    tri = job.mesh.V[job.mesh.F[faces]]
    w = weld_ids(tri.reshape(-1, 3)).reshape(-1, 3)
    uv = layout.uv
    rows = []
    corner = np.tile(np.arange(3), (len(faces), 1))
    e0 = np.concatenate([w[:, [0, 1]], w[:, [1, 2]], w[:, [2, 0]]])
    c0 = np.concatenate([corner[:, [0, 1]], corner[:, [1, 2]], corner[:, [2, 0]]])
    fidx = np.tile(np.arange(len(faces)), 3)
    sw = e0[:, 0] > e0[:, 1]
    e = np.where(sw[:, None], e0[:, ::-1], e0)
    c = np.where(sw[:, None], c0[:, ::-1], c0)
    key = e[:, 0] * (np.int64(1) << 32) + e[:, 1]
    o = np.argsort(key, kind="stable")
    key, fidx, c = key[o], fidx[o], c[o]
    same = np.nonzero(key[1:] == key[:-1])[0]
    fa, fb = fidx[same], fidx[same + 1]
    ca, cb = c[same], c[same + 1]
    ua0, ua1 = uv[fa, ca[:, 0]], uv[fa, ca[:, 1]]
    ub0, ub1 = uv[fb, cb[:, 0]], uv[fb, cb[:, 1]]
    seam = (np.abs(ua0 - ub0).max(1) > 1e-6) | (np.abs(ua1 - ub1).max(1) > 1e-6)
    for k in np.nonzero(seam)[0]:
        la = np.linalg.norm(ua1[k] - ua0[k])
        lb = np.linalg.norm(ub1[k] - ub0[k])
        n = int(max(2, np.ceil(max(la, lb) / step)))
        t = (np.arange(n) + 0.5) / n
        pa = ua0[k][None] * (1 - t[:, None]) + ua1[k][None] * t[:, None]
        pb = ub0[k][None] * (1 - t[:, None]) + ub1[k][None] * t[:, None]
        ia = _chart_edge_texels(tex, pa, int(layout.chart[fa[k]]))
        ib = _chart_edge_texels(tex, pb, int(layout.chart[fb[k]]))
        ok = (ia >= 0) & (ib >= 0) & (ia != ib)
        if ok.any():
            rows.append(np.stack([ia[ok], ib[ok]], 1))
    if not rows:
        return np.zeros((0, 2), np.int64)
    return np.unique(np.concatenate(rows), axis=0)


def pad_gutters(img: np.ndarray, used: np.ndarray, width: int = GUTTER) -> np.ndarray:
    """Every pixel within ``width`` of a used texel takes the nearest used texel's colour."""
    if not used.any():
        return img
    d, (iy, ix) = ndimage.distance_transform_edt(~used, return_indices=True)
    out = img.copy()
    m = (~used) & (d <= width)
    out[m] = img[iy[m], ix[m]]
    return out
