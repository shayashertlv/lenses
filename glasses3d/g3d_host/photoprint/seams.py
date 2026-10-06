"""WP5 step 4b / 7a: seams (WP5_DESIGN §6.4, §6.5, §6.7, A5).

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
  point. ``harmonise`` (fix round 1: never mixes two photos, deterministic): every texel has a RANK (non-photo 0;
  photo 1, + 1 when painted by its region's anchor, + its donor weight / the largest weight, < 1). In a pair the
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
- **Gutters**: every atlas pixel within 8 texels of a texel takes its nearest texel's colour (bilinear and mipmapped
  lookups never read the background), after composition.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from .. import texture_utils as T
from .labels import weld_ids

STEP_MAX = 3.0
BAND_ENERGY_MIN = 0.85
SEAM_SHARE_MAX = 0.15
PAIR_STEP = 0.5
HARMONISE_DE = 3.0
FEATHER = 3
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


def chart_seam_pairs(job, layout, tex, step: float = PAIR_STEP) -> np.ndarray:
    """(K, 2) texel rows of the same 3D point on the two sides of every uv seam (mesh edges whose two faces have
    different uv at the edge's ends, on welded positions)."""
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
    H, W = tex.shape
    for k in np.nonzero(seam)[0]:
        la = np.linalg.norm(ua1[k] - ua0[k])
        lb = np.linalg.norm(ub1[k] - ub0[k])
        n = int(max(2, np.ceil(max(la, lb) / step)))
        t = (np.arange(n) + 0.5) / n
        pa = ua0[k][None] * (1 - t[:, None]) + ua1[k][None] * t[:, None]
        pb = ub0[k][None] * (1 - t[:, None]) + ub1[k][None] * t[:, None]
        ia = tex.index[np.clip(np.floor(pa[:, 1]).astype(int), 0, H - 1), np.clip(np.floor(pa[:, 0]).astype(int), 0, W - 1)]
        ib = tex.index[np.clip(np.floor(pb[:, 1]).astype(int), 0, H - 1), np.clip(np.floor(pb[:, 0]).astype(int), 0, W - 1)]
        ok = (ia >= 0) & (ib >= 0) & (ia != ib)
        if ok.any():
            rows.append(np.stack([ia[ok], ib[ok]], 1))
    if not rows:
        return np.zeros((0, 2), np.int64)
    return np.unique(np.concatenate(rows), axis=0)


def harmonise(tex, col: np.ndarray, photo: np.ndarray, pairs: np.ndarray, rank: np.ndarray | None = None,
              source: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Make the two texels of each chart-seam pair agree (see the module docstring). ``rank`` (n,) float, higher
    wins (default: photo 1, else 0); ``source`` (n,) int painting view of photo texels (-1 otherwise): copies from two
    views are never averaged. Returns (colours, texels that stop being protected, counts)."""
    col = np.asarray(col, float).copy()
    n_tex = len(col)
    unprot = np.zeros(n_tex, bool)
    if not len(pairs):
        return col, unprot, {"pairs": 0}
    photo = np.asarray(photo, bool)
    rank = photo.astype(float) if rank is None else np.asarray(rank, float)
    a, b = pairs[:, 0], pairs[:, 1]
    de = T.de00(col[a], col[b])
    pa, pb = photo[a], photo[b]
    n = {"pairs": int(len(pairs)), "agree": int((de <= HARMONISE_DE).sum()),
         "agree_share": float((de <= HARMONISE_DE).mean()), "de00_p50": float(np.median(de)),
         "de00_p95": float(np.percentile(de, 95))}
    a_wins = (rank[a] > rank[b]) | ((rank[a] == rank[b]) & (a < b))
    win = np.where(a_wins, a, b)
    lose = np.where(a_wins, b, a)
    both_edit = ~pa & ~pb
    lose_photo = photo[lose]
    copy = ~both_edit & (~lose_photo | (de > HARMONISE_DE))     # a photo loser moves only on a real disagreement
    # proposals: (texel, colour, priority); priority 1 = copy from a winner, 0 = mean of two edited texels
    mean0 = (col[a[both_edit]] + col[b[both_edit]]) / 2
    tx_idx = np.concatenate([lose[copy], a[both_edit], b[both_edit]])
    tx_col = np.concatenate([col[win[copy]], mean0, mean0])
    tx_pri = np.concatenate([np.ones(int(copy.sum()), np.int64), np.zeros(2 * int(both_edit.sum()), np.int64)])
    src = np.full(n_tex, -1, np.int64) if source is None else np.asarray(source, np.int64)
    tx_src = np.concatenate([src[win[copy]], np.full(2 * int(both_edit.sum()), -1, np.int64)])
    tx_wrank = np.concatenate([rank[win[copy]], np.full(2 * int(both_edit.sum()), -np.inf)])
    best = np.full(n_tex, -1, np.int64)
    np.maximum.at(best, tx_idx, tx_pri)
    keep = tx_pri == best[tx_idx]
    # fix round 2: copies from winners of DIFFERENT views are never averaged: keep the highest-ranked winner's view
    resolved = 0
    if source is not None and keep.any():
        kc = keep & (tx_pri == 1)
        if kc.any():
            idx_k = tx_idx[kc]
            nsrc = np.zeros(n_tex, np.int64)
            # distinct sources per texel among the kept copies
            order = np.lexsort((tx_src[kc], idx_k))
            ii_, ss_ = idx_k[order], tx_src[kc][order]
            newgrp = np.r_[True, (ii_[1:] != ii_[:-1]) | (ss_[1:] != ss_[:-1])]
            np.add.at(nsrc, ii_[newgrp], 1)
            multi = nsrc > 1
            if multi.any():
                cand_ = kc & multi[tx_idx]
                bestr = np.full(n_tex, -np.inf)
                np.maximum.at(bestr, tx_idx[cand_], tx_wrank[cand_])
                top = cand_ & (tx_wrank == bestr[tx_idx])
                minsrc = np.full(n_tex, np.iinfo(np.int64).max)
                np.minimum.at(minsrc, tx_idx[top], tx_src[top])
                drop = cand_ & ~(top & (tx_src == minsrc[tx_idx]))
                keep &= ~drop
                resolved = int(multi.sum())
    acc = np.zeros((n_tex, 3))
    cnt = np.zeros(n_tex)
    np.add.at(acc, tx_idx[keep], tx_col[keep])
    np.add.at(cnt, tx_idx[keep], 1.0)
    changed = cnt > 0
    target = np.full(col.shape, np.nan)
    target[changed] = acc[changed] / cnt[changed, None]
    pp_moved = copy & lose_photo
    unprot[lose[pp_moved]] = True
    multi = np.bincount(tx_idx[keep], minlength=n_tex)
    kk = keep & (tx_pri == 1) & (tx_src >= 0)
    mv = 0
    if kk.any():
        o_ = np.lexsort((tx_src[kk], tx_idx[kk]))
        i2, s2 = tx_idx[kk][o_], tx_src[kk][o_]
        ng = np.r_[True, (i2[1:] != i2[:-1]) | (s2[1:] != s2[:-1])]
        cnt_src = np.bincount(i2[ng], minlength=n_tex)
        mv = int((cnt_src > 1).sum())
    n.update({"multi_view_proposals_resolved": resolved, "multi_view_means": mv})
    n.update({"photo_to_edited": int((copy & ~lose_photo).sum()), "edited_mean": int(both_edit.sum()),
              "photo_photo_copied": int(pp_moved.sum()), "photo_photo_kept_within_3": int((~both_edit & lose_photo
                                                                                          & ~copy).sum()),
              "texels_set": int(changed.sum()), "texels_with_several_proposals": int((multi > 1).sum()),
              "unprotected_texels": int(unprot.sum())})
    # feather edited (non-photo) texels near changed seam texels toward the nearest one's target
    img_changed = tex.image(changed, False, bool)
    if img_changed.any():
        d, (iy, ix) = ndimage.distance_transform_edt(~img_changed, return_indices=True)
        dd = d[tex.ii, tex.jj]
        src = tex.index[iy[tex.ii, tex.jj], ix[tex.ii, tex.jj]]
        same_chart = tex.chart[np.maximum(src, 0)] == tex.chart
        f = (~photo | unprot) & ~changed & (dd <= FEATHER) & (src >= 0) & same_chart
        alpha = (1.0 - dd[f] / (FEATHER + 1))[:, None]
        col[f] = (1 - alpha) * col[f] + alpha * target[src[f]]
        n["feathered"] = int(f.sum())
    col[changed] = target[changed]
    return col, unprot, n


def pad_gutters(img: np.ndarray, used: np.ndarray, width: int = GUTTER) -> np.ndarray:
    """Every pixel within ``width`` of a used texel takes the nearest used texel's colour."""
    if not used.any():
        return img
    d, (iy, ix) = ndimage.distance_transform_edt(~used, return_indices=True)
    out = img.copy()
    m = (~used) & (d <= width)
    out[m] = img[iy[m], ix[m]]
    return out
