"""Build step 3 proof helpers (WP5_DESIGN §8 A2, A3, A4, A5, B3): each case is made from photoprint's outputs and
graded by the independent instrument (tests/instruments/print_metrics.py), never by photoprint's own numbers.

- ``round_trip`` (A2): bake from view v alone (every texel v sees as a donor, bicubic), render at v with THIS
  module's renderer (``render_atlas``: first hit on the scene, uv from the layout's per-corner coordinates, bilinear
  lookup with glTF semantics written in the instrument, ``print_metrics.sample_gltf``; fix round 1: photoprint's
  ``render_bake`` shared the bake's uv convention, so an error common to both would have cancelled), grade on
  E = rendered pixels eroded 2 px whose four bilinear texels are all projected, on v's acetate (eroded 1 px); both
  filters read only projected acetate pixels (a nearest-filled pixel inside the 4 px DoG's reach cost 0.01-0.03 of bp
  at region edges: 0.972 -> 0.990 on 02 with E eroded 3 px more, while the same pixels coloured directly from the
  photo give 1.000, so the loss was the filler, not the bake).
- ``readback_a3`` (A3, fix round 1): print.png READ BACK from disk, at every texel classes.png calls photo, against
  the photo colour sampled here independently (this module's own rasteriser of the layout at texel centres, the
  camera model's ``project_mm``, scipy's cubic spline) times the gains the bake declares in report.json (per view,
  then per region x view group). The bake's own A3 (protected == P0) is true by construction and only reported.
- ``knockout`` (A4, fix round 2: the holes are drawn HERE, never by the tool's ``knockout_holes`` / ``single_label``,
  and spread over the part): ``A4_HOLES`` (8) seeded hole centres per part, drawn among every place where a hole of
  the cycled shape (2 x 2, 4 x 4, 8 x 5, 4 x 2 mm, atlas axes; a smaller one where it does not fit) fits inside ONE
  painting view's photo texels of the part (eroded 1 texel; seam bands excluded), at least both half-diagonals + 1 mm
  apart; each is refilled by the
  tool's ``fill.patchmatch_call`` (the system under test) at the bake's radius from that view's photo texels within
  12 mm, graded by the instrument per hole, with the donor ring's own baseline (dE00 between the mean colour of the
  truth in a 1-3 mm ring around the hole and inside it: how predictable the hole's colour is from its surroundings).
  The part's verdict uses medians over the holes with their spread (``print_metrics.a4_criteria``). Fix round 1 put
  all five holes on one point of one chart, so the median was one correlated sample (Temple_Left's 14.98 dE00 was one
  amber blotch no donor strip contained).
- ``seams`` (A5, fix round 1): the instrument's own uv-seam samples (from the geometry and the layout's uv, not the
  tool's seam pairs) on print.png read back, graded 1 and 2 texels inside each chart against the within-chart change
  at the same separation (``print_metrics.seam_steps``); the same-3D-point agreement BEFORE the tool's harmonisation is
  reported as a diagnostic (after it, it is set by construction). Fix round 2: the samples are broken down by what
  lies on the two sides (classes.png and source.png read back: photo of one view, photos of two views, photo and
  fill, fill on both), and the criterion's status is recorded (``criterion_decision``).
- ``audit_outputs`` (fix round 2): single source checked from the WRITTEN outputs: every photo texel's view
  (source.png) is its region's anchor or the anchor of a region adjacent to it (registration.json's anchors; adjacency
  computed here from the mesh), and no layout face carries photo texels of two views.
- ``heldout_b3`` (B3): the production bake from every view except h, rendered at h's WP3b camera on S, per region h
  sees, against Astra's texture on identical pixels; eligible when >= 50 % of E came from photo texels and E >= 1,500 px.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from glasses3d.g3d import printuv as PU
from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.camera import project_mm
from glasses3d.g3d_host.photoprint import fill as FL
from glasses3d.g3d_host.photoprint import project as PR
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint import texels as TX
from glasses3d.g3d_host.photoprint.compose import CLASS_CODES, render_bake
from glasses3d.tests.instruments import print_metrics as PM

A4_HOLES = 8
A4_SHAPES_MM = ((2.0, 2.0), (4.0, 4.0), (8.0, 5.0), (4.0, 2.0))     # (atlas x, atlas y)
A4_MARGIN_MM = 12.0
A4_RING_MM = (1.0, 3.0)
A3_LEVEL_TOL = 1            # read-back colour within 1 level per channel (uint8 rounding) ...
A3_SHARE_MIN = 0.995        # ... on at least 99.5 % of the protected photo texels
A3_DE00_P99_MAX = 1.0


def _region_E(mask: np.ndarray, acet: np.ndarray) -> np.ndarray:
    return ndimage.binary_erosion(mask, iterations=2) & ndimage.binary_erosion(acet, iterations=1)


def render_atlas(job, layout, atlas: np.ndarray, cam, shape, scene) -> dict:
    """{'ren', 'mask'}: the atlas on the layout's faces at ``cam`` (first hit on ``scene``), uv = the per-corner layout
    coordinates combined with the hit's barycentrics, colour = ``print_metrics.sample_gltf`` (glTF bilinear)."""
    H, W = shape
    r = scene.render(cam, (H, W), 1, want_bary=True)
    fid = r["face_id"]
    lrow = layout.face_index(len(job.mesh.F))
    hit = (fid >= 0) & (fid < len(lrow))
    row = np.full((H, W), -1, np.int64)
    row[hit] = lrow[fid[hit]]
    m = row >= 0
    ii, jj = np.nonzero(m)
    b = r["bary"][ii, jj].astype(np.float64)
    w = np.stack([1.0 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1)
    uv = (w[:, :, None] * np.asarray(layout.uv, np.float64)[row[ii, jj]]).sum(1)
    ren = np.zeros((H, W, 3))
    ren[ii, jj] = PM.sample_gltf(np.asarray(atlas, np.float64), uv)
    return {"ren": ren, "mask": m}


def round_trip(job, view: str, tx: float, parts, layout=None) -> dict:
    layout = layout or PU.layout(job.mesh.V, job.mesh.F, job.mesh.part, job.mesh.names, parts, tx)
    tex = TX.atlas_texels(job, layout)
    occ = TX.OcclusionScene(job, job.part_ids(parts), TX.decal_geometry(PR.job_glb(job), job.mesh.skipped))
    s = TX.view_samples(job, tex, view, occ)
    atlas = tex.image(np.where(s.ok[:, None], s.col, 0.0), 0.0, float)
    proj = tex.image(s.ok.astype(float), 0.0, float)
    v = job.views[view]
    sc = occ.scene(job.mesh.V)
    r = render_atlas(job, layout, atlas, v.camera, v.shape, sc)
    rp = render_atlas(job, layout, np.repeat(proj[..., None], 3, -1), v.camera, v.shape, sc)
    full = r["mask"] & (rp["ren"][..., 0] > 1 - 1e-6)
    photo = v.image.astype(float)
    ren = r["ren"].copy()
    if full.any():
        filled, _ = T.nearest_fill(ren, full)
        hole = r["mask"] & ~full
        ren[hole] = filled[hole]
    E = _region_E(r["mask"], v.acetate) & full
    sup = full & v.acetate                    # neither filter reads a pixel the bake did not project
    out = PM.round_trip(ren, photo, sup, E, job.ppm(view))
    out.update({"view": view, "tx_per_mm": float(layout.tx_per_mm), "px_per_mm": float(job.ppm(view)),
                "renderer": "printproof.render_atlas (glTF bilinear, instrument)"})
    return out


# ----------------------------------------------------------------------------- A3 read back (independent)
def texel_faces(layout, shape) -> tuple[np.ndarray, np.ndarray]:
    """(row (H, W) layout row covering each texel centre or -1, barycentrics (H, W, 3)): this module's own rasteriser
    (texel (i, j) centre at (j + 0.5, i + 0.5); later faces win, as any rasteriser's draw order would)."""
    H, W = shape
    uv = np.asarray(layout.uv, np.float64)
    row = np.full((H, W), -1, np.int64)
    bar = np.zeros((H, W, 3))
    for k in range(len(uv)):
        t = uv[k]
        x0, x1 = int(max(np.floor(t[:, 0].min() - 0.5), 0)), int(min(np.ceil(t[:, 0].max() - 0.5), W - 1))
        y0, y1 = int(max(np.floor(t[:, 1].min() - 0.5), 0)), int(min(np.ceil(t[:, 1].max() - 0.5), H - 1))
        if x1 < x0 or y1 < y0:
            continue
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        px, py = xs.ravel() + 0.5, ys.ravel() + 0.5
        (ax, ay), (bx, by), (cx, cy) = t
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den
        l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den
        l2 = 1.0 - l0 - l1
        ins = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
        if not ins.any():
            continue
        yy, xx = ys.ravel()[ins], xs.ravel()[ins]
        row[yy, xx] = k
        bar[yy, xx] = np.stack([l0[ins], l1[ins], l2[ins]], 1)
    return row, bar


def _gain(rgb: np.ndarray, g) -> np.ndarray:
    g = np.asarray(g, float)
    if np.allclose(g, 1.0):
        return np.asarray(rgb, float)
    return np.clip(T.linear_to_srgb(T.srgb_to_linear(rgb) * g[None]), 0, 255)


def readback_a3(job, res: dict, out_dir) -> dict:
    """A3 graded independently (see the module docstring)."""
    out_dir = Path(out_dir)
    img = np.asarray(Image.open(out_dir / "print.png").convert("RGB")).astype(np.float64)
    cls = np.asarray(Image.open(out_dir / "classes.png"))
    src = np.asarray(Image.open(out_dir / "source.png"))
    lay = res["layout"]
    rep = res["report"]
    views = rep["views"]
    row, bar = texel_faces(lay, (lay.height, lay.width))
    photo_cls = (cls == CLASS_CODES["photo_anchor"]) | (cls == CLASS_CODES["photo_tier1"])
    prot = res["protected"]
    sel = photo_cls & prot & (row >= 0) & (src > 0)
    out = {"protected_photo_texels": int((photo_cls & prot).sum()), "graded": int(sel.sum()),
           "uncovered_by_own_raster": int((photo_cls & prot & (row < 0)).sum())}
    # region of each texel (the tool's grouping key for its region x view gains)
    tex = res["tex"]
    reg_img = tex.image(tex.region, -1, np.int64)
    per_view = {v: rep["gains"]["per_view"].get(v, {"gain": [1.0, 1.0, 1.0]})["gain"] for v in views}
    grp = {(g["region"], g["view_index"]): g["gain"] for g in rep["gains"]["across_boundaries"]["groups"]}
    ii, jj = np.nonzero(sel)
    faces = lay.faces[row[ii, jj]]
    P = (bar[ii, jj][:, :, None] * job.mesh.V[job.mesh.F[faces]]).sum(1)
    expect = np.zeros((len(ii), 3))
    vidx = src[ii, jj].astype(np.int64) - 1
    for k, v in enumerate(views):
        m = vidx == k
        if not m.any():
            continue
        cam = job.views[v].camera
        uv = project_mm(P[m], cam, job.frame)
        ph = np.asarray(job.views[v].image, np.float64)
        raw = np.stack([ndimage.map_coordinates(ph[..., c], [uv[:, 1], uv[:, 0]], order=3, mode="nearest")
                        for c in range(3)], -1)
        raw = np.clip(raw, 0, 255)
        e = _gain(raw, per_view[v])
        regs = reg_img[ii[m], jj[m]]
        gg = np.ones((m.sum(), 3))
        for r_ in np.unique(regs):
            gg[regs == r_] = grp.get((int(r_), k), [1.0, 1.0, 1.0])
        e = np.clip(T.linear_to_srgb(T.srgb_to_linear(e) * gg), 0, 255)
        expect[m] = e
    got = img[ii, jj]
    diff = np.abs(np.rint(expect) - got).max(1)
    de = T.de00(expect, got) if len(got) else np.zeros(0)
    out.update({"within_1_level_share": float((diff <= A3_LEVEL_TOL).mean()) if len(diff) else None,
                "max_level_diff": float(diff.max()) if len(diff) else None,
                "de00_p50": float(np.median(de)) if len(de) else None,
                "de00_p99": float(np.percentile(de, 99)) if len(de) else None,
                "de00_max": float(de.max()) if len(de) else None})
    out["criteria"] = {"within_1_level": bool(out["within_1_level_share"] is not None
                                              and out["within_1_level_share"] >= A3_SHARE_MIN),
                       "de00_p99": bool(out["de00_p99"] is not None and out["de00_p99"] <= A3_DE00_P99_MAX),
                       "own_raster_covers": bool(out["uncovered_by_own_raster"] == 0)}
    out["pass"] = bool(all(out["criteria"].values()))
    return out


# ----------------------------------------------------------------------------- A4, A5
def _part_mask(res: dict, part: str) -> np.ndarray:
    lay, tex = res["layout"], res["tex"]
    charts = [c["chart"] for c in lay.charts if c["part"] == part]
    return np.isin(tex.chart, charts)


def knockout(res: dict, part: str, *, seed: int = 0, n_holes: int = A4_HOLES) -> dict:
    """A4 for one part (see the module docstring)."""
    lay, tex = res["layout"], res["tex"]
    tx = float(lay.tx_per_mm)
    H, W = tex.shape
    pm = _part_mask(res, part)
    photo = res["photo"] & ~res["band"] & pm
    lab_img = tex.image(np.where(photo, res["label"], -9), -9, np.int64)
    truth_img = np.asarray(res["P0"], np.uint8)
    labels = [int(x) for x in np.unique(lab_img) if x >= 0]
    rng = np.random.default_rng([int(seed), 404, sum(ord(c) for c in part)])
    rp = FL.radius_px(res["radii"][part], tx)
    m = int(round(A4_MARGIN_MM * tx))
    chosen, rows, skipped = [], [], []
    masks = {l: lab_img == l for l in labels}
    fits: dict = {}
    def fit_map(hw, hh):
        if (hw, hh) not in fits:                       # where the hole (+ 1 texel) fits inside one view's texels
            cands = []
            for l in labels:
                ok = ndimage.minimum_filter(masks[l].astype(np.uint8), size=(hh + 2, hw + 2), mode="constant",
                                            cval=0) > 0
                ys, xs = np.nonzero(ok)
                if len(ys):
                    cands.append(np.stack([ys, xs, np.full(len(ys), l)], 1))
            fits[(hw, hh)] = np.concatenate(cands) if cands else np.zeros((0, 3), np.int64)
        return fits[(hw, hh)]

    order = sorted(A4_SHAPES_MM, key=lambda sh: -sh[0] * sh[1])
    for k in range(n_holes):
        first = A4_SHAPES_MM[k % len(A4_SHAPES_MM)]
        tries = [first] + [sh for sh in order if sh[0] * sh[1] < first[0] * first[1]]   # smaller shapes if no room
        C, why = np.zeros((0, 3), np.int64), "no room in any single-view area"
        for wmm, hmm in tries:
            hw, hh = max(2, int(round(wmm * tx))), max(2, int(round(hmm * tx)))
            C = fit_map(hw, hh)
            for (cy, cx, _l, sw, sh) in chosen:        # at least (both half-diagonals + 1 mm) apart
                far = np.hypot(C[:, 0] - cy, C[:, 1] - cx) >= (np.hypot(hw, hh) + np.hypot(sw, sh)) / 2 + 1.0 * tx
                C = C[far]
            if len(C):
                break
            why = "no room away from the earlier holes"
        if not len(C):
            skipped.append({"hole": k, "shape_mm": list(first), "reason": why})
            continue
        cy, cx, l = (int(v) for v in C[rng.integers(len(C))])
        chosen.append((cy, cx, l, hw, hh))
        y0, x0 = cy - hh // 2, cx - hw // 2
        hole_full = np.zeros((H, W), bool)
        hole_full[y0:y0 + hh, x0:x0 + hw] = True
        hole_full &= masks[l]
        Y0, Y1 = max(y0 - m, 0), min(y0 + hh + m, H)
        X0, X1 = max(x0 - m, 0), min(x0 + hw + m, W)
        sub = truth_img[Y0:Y1, X0:X1]
        hs = hole_full[Y0:Y1, X0:X1]
        sup = masks[l][Y0:Y1, X0:X1]
        don = sup & ~hs
        r = FL.patchmatch_call(sub, hs, don, rp, int(seed) * 1000 + k)
        g = PM.knockout(r["out"], sub, hs, sup, tx)
        ring = (ndimage.binary_dilation(hs, iterations=int(round(A4_RING_MM[1] * tx)))
                & ~ndimage.binary_dilation(hs, iterations=int(round(A4_RING_MM[0] * tx))) & don)
        g["ring_baseline_de00"] = PM.ring_baseline(sub, hs, ring)
        g.update({"hole": f"{wmm:g}x{hmm:g}mm", "centre_atlas_px": [cx, cy], "view_index": l,
                  "outside_changed": r["outside_changed"], "hole_changed": r["hole_changed"]})
        rows.append(g)
    return {"part": part, "radius_mm": res["radii"][part], "holes": rows, "skipped": skipped,
            "a4": PM.a4_criteria(rows), "hole_source": "printproof.knockout (drawn in the proof, seeded)"}


def seams(job, res: dict, out_dir=None) -> dict:
    """A5 by the instrument (see the module docstring). ``out_dir``: read print.png back (else the in-memory atlas)."""
    lay, tex = res["layout"], res["tex"]
    if out_dir is not None and (Path(out_dir) / "print.png").is_file():
        img = np.asarray(Image.open(Path(out_dir) / "print.png").convert("RGB")).astype(np.float64)
        source = "print.png read back"
    else:
        img = res["atlas"].astype(np.float64)
        source = "in-memory atlas"
    smp = PM.uv_seam_samples(job.mesh.V, job.mesh.F, lay.faces, lay.uv)
    out = PM.seam_steps(img, smp)
    out["image"] = source
    out["criterion_decision"] = (
        "A5 as graded here is the instrument's off-seam reading (fix round 1): p95 across the seam 1 and 2 texels "
        "inside the charts <= max(3, the within-chart p95 at the same separation). The design's literal reading "
        "(p95 dE00 <= 3 between texels of the SAME 3D point) is offset 0, which the tool's harmonisation writes, so it "
        "passes by construction and is reported as diagnostic_tool_pairs_final_by_construction. This criterion change "
        "is an engineering decision that is NOT yet recorded in PROGRESS.md (fix round 2 states it here).")
    if out_dir is not None and (Path(out_dir) / "classes.png").is_file():
        out["breakdown"] = seam_breakdown(img, smp, Path(out_dir))
    pre = tex.image(np.clip(np.asarray(res["colours_pre_harmonise"], float), 0, 255), 0.0, float)
    pre = T.nearest_fill(pre, tex.index >= 0)[0]          # gutters as the final atlas has them (nearest texel)
    out["diagnostic_pre_harmonise"] = PM.seam_steps(pre, {0: smp[0]})["offsets"][0]
    pairs = res["pairs"]
    if len(pairs):
        c = np.asarray(res["colours_pre_harmonise"], float)
        out["diagnostic_tool_pairs_pre_harmonise"] = PM.seam_continuity(c[pairs[:, 0]], c[pairs[:, 1]])
        fin = res["atlas"].astype(float)
        out["diagnostic_tool_pairs_final_by_construction"] = PM.seam_continuity(
            fin[tex.ii[pairs[:, 0]], tex.jj[pairs[:, 0]]], fin[tex.ii[pairs[:, 1]], tex.jj[pairs[:, 1]]])
    return out


def seam_breakdown(img: np.ndarray, smp: dict, out_dir: Path) -> dict:
    """A5 samples by what lies on the two sides (classes.png and source.png read back): 'photo, one view', 'photo, two
    views', 'photo and fill', 'fill both' (fill = every non-photo class). Per offset and kind: samples, across p95, and
    the share of the offset's samples over its overall p95."""
    cls = np.asarray(Image.open(out_dir / "classes.png"))
    src = np.asarray(Image.open(out_dir / "source.png"))
    photo_cls = (cls == CLASS_CODES["photo_anchor"]) | (cls == CLASS_CODES["photo_tier1"])
    H, W = cls.shape
    out = {}
    for kk in (1, 2):
        s = smp.get(kk)
        if s is None or not len(s["a"]):
            continue
        d = T.de00(PM.sample_gltf(img, s["a"]), PM.sample_gltf(img, s["b"]))
        p95 = float(np.percentile(d, 95))
        ia = (np.clip(np.floor(s["a"][:, 1]).astype(int), 0, H - 1), np.clip(np.floor(s["a"][:, 0]).astype(int), 0, W - 1))
        ib = (np.clip(np.floor(s["b"][:, 1]).astype(int), 0, H - 1), np.clip(np.floor(s["b"][:, 0]).astype(int), 0, W - 1))
        pa, pb = photo_cls[ia], photo_cls[ib]
        sa, sb = src[ia], src[ib]
        kind = np.where(pa & pb, np.where(sa == sb, "photo, one view", "photo, two views"),
                        np.where(pa | pb, "photo and fill", "fill both"))
        rows = {}
        for k_ in ("photo, one view", "photo, two views", "photo and fill", "fill both"):
            m_ = kind == k_
            if m_.any():
                rows[k_] = {"samples": int(m_.sum()), "across_p95": float(np.percentile(d[m_], 95)),
                            "share_of_over_p95": float((m_ & (d > p95)).sum() / max(int((d > p95).sum()), 1))}
        out[int(kk)] = {"p95": p95, "kinds": rows}
    return out


def audit_outputs(job, res: dict, out_dir) -> dict:
    """Single source from the written outputs (see the module docstring)."""
    import json
    out_dir = Path(out_dir)
    src = np.asarray(Image.open(out_dir / "source.png"))
    cls = np.asarray(Image.open(out_dir / "classes.png"))
    reg = json.loads((out_dir / "registration.json").read_text(encoding="utf-8"))
    views = res["report"]["views"]
    tex, lay = res["tex"], res["layout"]
    photo_cls = (cls == CLASS_CODES["photo_anchor"]) | (cls == CLASS_CODES["photo_tier1"])
    sel = photo_cls[tex.ii, tex.jj]
    view_of = src[tex.ii, tex.jj].astype(np.int64) - 1
    regions = res["regions"]
    anchor = {r.rid: reg["anchors"][r.name]["anchor"] for r in regions}
    # region adjacency from the mesh (welded positions), computed here
    F = job.mesh.F
    lut = np.full(len(F), -1, np.int64)
    for r in regions:
        lut[r.faces] = r.rid
    faces = np.nonzero(lut >= 0)[0]
    key = np.round(job.mesh.V / 1e-4).astype(np.int64)
    _u, wid = np.unique(key, axis=0, return_inverse=True)
    wid = wid.reshape(-1)[F[faces]]
    e = np.concatenate([wid[:, [0, 1]], wid[:, [1, 2]], wid[:, [2, 0]]])
    e.sort(1)
    fr = np.tile(lut[faces], 3)
    ek = e[:, 0] * (np.int64(1) << 32) + e[:, 1]
    o = np.argsort(ek, kind="stable")
    ek, fr = ek[o], fr[o]
    same = np.nonzero(ek[1:] == ek[:-1])[0]
    adj = {r.rid: set() for r in regions}
    for x, y in set(zip(fr[same].tolist(), fr[same + 1].tolist())):
        if x != y:
            adj[x].add(y)
            adj[y].add(x)
    bad = 0
    per_region = {}
    for r in regions:
        m = sel & (tex.region == r.rid)
        allowed = {anchor[r.rid]} | {anchor[q] for q in adj[r.rid]}
        allowed = {views.index(v) for v in allowed if v is not None}
        vs = view_of[m]
        nb = int((~np.isin(vs, list(allowed))).sum()) if len(vs) else 0
        bad += nb
        per_region[r.name] = {"photo_texels": int(m.sum()), "views": sorted({views[int(v)] for v in np.unique(vs)}),
                              "outside_own_or_adjacent_anchor": nb}
    # one view per layout face
    lrow = lay.face_index(len(F))[tex.face]
    rows = lrow[sel]
    vv = view_of[sel]
    order = np.lexsort((vv, rows))
    rr, vv2 = rows[order], vv[order]
    newv = np.r_[True, (rr[1:] != rr[:-1]) | (vv2[1:] != vv2[:-1])]
    nviews = np.bincount(rr[newv], minlength=len(lay.faces))
    return {"photo_texels": int(sel.sum()), "outside_own_or_adjacent_anchor": int(bad),
            "faces_with_two_views": int((nviews > 1).sum()), "regions": per_region,
            "pass": bool(bad == 0 and (nviews > 1).sum() == 0)}


def heldout_b3(job, parts, h: str, *, radii_mm: dict, tx: float, seed: int = 0, layout=None,
               thresholds=(0.8, 1.0, 2.5), b2: bool = False, keep_render: bool = False) -> dict:
    """The production bake without view h, graded at h per region (see the module docstring).

    ``b2`` (build step 3 run, single-source mode): each row also carries the B2 DIAGNOSTIC (§8 C1-C4 by
    ``print_metrics.evaluate_case``, never a gate): the same held-out render and Astra's on the same pixels, graded on
    E restricted to pixels painted from a photo texel (fills are not graded). It is the strict leave-one-view-out form
    (§7's B0: h at its WP3b camera, no registration, no folds), because no region registers on FT1119.
    ``keep_render``: the result also carries the held-out render at h (uint8, every layout face) and its mask, for
    the comparison sheets."""
    others = [n for n in job.views if n != h]
    res = PR.project_photo_texture(job, parts, out_dir=None, print_reg_mm=thresholds[0], edge_mean_mm=thresholds[1],
                                   edge_p90_mm=thresholds[2], views=others, tx_per_mm=tx, layout=layout,
                                   radii_mm=radii_mm, seed=seed, workers=1, write=False, log=None)
    lay, tex = res["layout"], res["tex"]
    v = job.views[h]
    sc = res["occ"].scene(job.mesh.V)
    r = render_bake(job, lay, res["atlas"].astype(float), h, v.camera, scene=sc)
    photo_cls = (res["classes"] == CLASS_CODES["photo_anchor"]) | (res["classes"] == CLASS_CODES["photo_tier1"])
    pc = tex.image(photo_cls.astype(float), 0.0, float)
    rpc = render_bake(job, lay, np.repeat(pc[..., None], 3, -1), h, v.camera, scene=sc)
    groups = RG.rigid_groups(job)
    regions = RG.build_regions(job, parts, groups, n_samples=64)
    lut = np.full(len(job.mesh.F) + 1, -1, np.int64)
    for reg in regions:
        lut[reg.faces] = reg.rid
    rid_img = lut[np.where(r["face"] >= 0, r["face"], -1)]
    photo = v.image.astype(float)
    # Astra at the same camera on S
    astra = np.zeros_like(photo)
    m = r["mask"]
    rr = sc.render(v.camera, v.shape, 1, want_bary=True)
    ii, jj = np.nonzero(m)
    astra[ii, jj] = job.astra.colours(job.mesh.part, r["face"][ii, jj], rr["bary"][ii, jj])
    rows = []
    for reg in regions:
        sel = m & (rid_img == reg.rid)
        if sel.sum() < 200:
            continue
        E = _region_E(sel, v.acetate)
        share = float((rpc["ren"][..., 0][E] > 0.5).mean()) if E.any() else 0.0
        sup = sel & v.acetate
        g = PM.evaluate_b3(r["ren"], astra, photo, sup, E, job.ppm(h), share)
        g.update({"held_out": h, "region": reg.name, "status_in_bake": res["report"]["statuses"][reg.name]["status"],
                  "anchor_in_bake": res["report"]["statuses"][reg.name]["anchor"],
                  "part_textured": reg.part_name in res["texture_parts"]})
        if b2:
            E2 = E & (rpc["ren"][..., 0] > 0.5)
            c = PM.evaluate_case(r["ren"], astra, photo, sup, E2, job.ppm(h), seed=seed)
            g["b2"] = {"pixels": c["pixels"], "b2_eligible": c["b2_eligible"], "b2_pass": c["b2_pass"],
                       "criteria": c.get("criteria"), "paired": c.get("paired"),
                       "projection": {k: c["projection"].get(k) for k in B2_KEYS if k in c["projection"]},
                       "astra": {k: c["astra"].get(k) for k in B2_KEYS if k in c["astra"]},
                       "protocol": "B0 form: h left out of the production bake, h at its WP3b camera on S, E = region "
                                   "eroded 2 px & acetate eroded 1 px & painted from a photo texel"}
        rows.append(g)
    out = {"held_out": h, "rows": rows, "views": others}
    if keep_render:
        out["render"] = np.clip(np.rint(r["ren"]), 0, 255).astype(np.uint8)
        out["mask"] = r["mask"]
        out["photo_painted"] = rpc["ren"][..., 0] > 0.5
    return out


B2_KEYS = ("de00_raw_mean", "de00_gain_mean", "bp_1_4px_r", "null_bp_1_4px", "energy_0.4_1.6mm", "energy_1_4px",
           "contamination", "spectrum_error", "corr_len_ratio", "decile_de00_raw")
