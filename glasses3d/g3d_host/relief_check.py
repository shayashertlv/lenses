"""WP6 check_relief on the host (plan/WP6_DESIGN.md §5): the GLB's temples through the shared measure core
(g3d/temple/measure.py) plus the photo verdicts.

    glb_temples(glb, hinge_z=None)            per side: the temple body (role temple, else position) in its chart
    check_relief(glb, job=None, ...)          geometry numbers and flags per side (+ the photo verdicts when the
                                              caller passes a relief_views Job, a Model and its fitted views)
    photo_crease_segments(...)                Canny on L* + probabilistic Hough on the near temple's outer face,
                                              masked (decals + 6 px, hinge, L* > 97, 5 px silhouette band); per
                                              segment the step, the model's normal change dn across it, the distance
                                              to the nearest model crease, the verdict FLAG / SHALLOW / OK, its
                                              lift onto the model surface ((d, y) in the +X chart)
    confirm_creases(seg05, seg01)             05 (+X) and 01 (-X, mirrored) lifted within 0.5 mm median
    crease_transfer(...)                      a 05 crease lifted onto the model surface, projected into 02, distance
                                              to the 02 photo crease (mm): works for any model, a slab included
    invented_creases(...)                     advisory: model creases >= 5 deg with no photo edge within 2 px
    shading_residual(...)                     report only: L* vs an order-2 SH fit of the model normal on the front
    r2_verdict(...)                           R2 from the segments and the confirmed creases

Rules: g3d_host/relief_rules.py (frozen). Sides follow the x sign (+X = the wearer's left), never part names.
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage

from ..g3d.temple import measure as MS
from . import cameras as C
from . import glb as glbmod
from . import raster
from . import relief_rules as RR


# --------------------------------------------------------------------------------------------- geometry
def _node_role(g, ni) -> str | None:
    node = g.nodes[ni] if 0 <= ni < len(g.nodes) else {}
    r = (node.get("extras") or {}).get("partRole")
    if r is None and "mesh" in node:
        r = (g.meshes[node["mesh"]].get("extras") or {}).get("partRole")
    return r if isinstance(r, str) else None


def glb_temples(glb_path, hinge_z: float | None = None) -> dict:
    """{side: {"V", "F" (chart), "node", "chart", "W", "T" (world mm)}}: per side the temple-role body reaching furthest
    back (>= 60 mm); without roles, the part behind the lenses at |x| > 45 mm reaching furthest back."""
    g = glbmod.load(glb_path)
    by_node: dict = {}
    for p in g.primitives:
        if p.mode != 4 or len(p.triangles) == 0:
            continue
        W = p.positions * 1000.0
        rec = by_node.setdefault(p.node_index, {"name": p.node_name, "W": [], "T": [], "n": 0,
                                                "role": _node_role(g, p.node_index)})
        rec["T"].append(p.triangles + rec["n"])
        rec["W"].append(W)
        rec["n"] += len(W)
    roles = any(r["role"] for r in by_node.values())
    best = {1: None, -1: None}
    for ni, r in by_node.items():
        W = np.vstack(r["W"])
        if roles and r["role"] != "temple":
            continue
        reach = float(W[:, 2].max() - W[:, 2].min())
        cx = float((W[:, 0].max() + W[:, 0].min()) / 2)
        if reach < 60.0 or abs(cx) < (20.0 if roles else 45.0):
            continue
        s = 1 if cx > 0 else -1
        if best[s] is None or reach > best[s][0]:
            best[s] = (reach, ni, W, np.vstack(r["T"]))
    out = {}
    for s in (1, -1):
        if best[s] is None:
            continue
        _, ni, W, T = best[s]
        hz = float(W[:, 2].max()) if hinge_z is None else float(hinge_z)
        V, F = MS.chart_mesh(W, T, s, hz)
        out[s] = {"V": V, "F": F, "W": W, "T": T, "node": by_node[ni]["name"],
                  "chart": f"world (hinge z {hz:.2f}{' from the temple front' if hinge_z is None else ''})"}
    return out


def check_relief(glb, job=None, *, hinge_z: float | None = None, photo_creases: int | None = None,
                 zone_end: float = 50.0, photo: dict | None = None) -> dict:
    """Geometry numbers and flags of both temples of a GLB (§5 "Geometry"); `photo` = the photo verdicts computed by
    the caller (relief_views + photo_crease_segments) are attached as they are."""
    if hinge_z is None and job is not None:
        hinge_z = job.hinge["z_mm"]
    temples = glb_temples(glb, hinge_z)
    rep = {"glb": str(glb), "temples": {}, "rules": RR.RULES_VERSION}
    for s, t in temples.items():
        m = MS.measure_temple(t["V"], t["F"], zone_end=zone_end, photo_creases=photo_creases)
        m["node"], m["chart"] = t["node"], t["chart"]
        rep["temples"]["+x" if s > 0 else "-x"] = m
    missing = [k for k in ("+x", "-x") if k not in rep["temples"]]
    if missing:
        rep["missing"] = missing
    if photo is not None:
        rep["photo"] = photo
    return rep


# --------------------------------------------------------------------------------------------- photo creases
def lightness(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2LAB)[..., 0].astype(np.float64) * 100.0 / 255.0


def chart_of(points: np.ndarray, side: int, hinge_z: float, theta: float, axis_xz) -> np.ndarray:
    """World hit points of a temple opened by theta -> (d, y, X) in that temple's chart (X = |x|)."""
    from .relief_views import rot_about_hinge
    Q = rot_about_hinge(points, side, -theta, axis_xz)
    return np.stack([hinge_z - Q[:, 2], Q[:, 1], side * Q[:, 0]], axis=1)


def outer_face_region(r: dict, cam, near: int) -> np.ndarray:
    """Pixels of the near temple's outer face (world normal x pointing outward, facing the camera)."""
    toward = raster.camera_basis(cam)[2]
    n = r["normal"]
    facing = (n @ toward) > 0
    n = np.where(facing[..., None], n, -n)
    return (r["tag"] == near) & (n[..., 0] * near > RR.VIEW_MIN_COS)


def model_crease_mask(r: dict, region: np.ndarray, deg: float = RR.MODEL_CREASE_DEG) -> np.ndarray:
    """Model crease pixels: normal change >= deg between pixels 2 px apart, both inside the region."""
    n = r["normal"]
    out = np.zeros(region.shape, bool)
    for dy, dx in ((0, 2), (2, 0), (2, 2), (2, -2)):
        b = np.roll(np.roll(n, -dy, 0), -dx, 1)
        same = region & np.roll(np.roll(region, -dy, 0), -dx, 1)
        cosang = np.clip((n * b).sum(-1), -1, 1)
        out |= same & (np.degrees(np.arccos(cosang)) >= deg)
    return out


def _samp(img, P, nn, off):
    W, H = img.shape[1], img.shape[0]
    Q = P[None] + np.arange(off[0], off[1] + 1)[:, None, None] * nn[None, None]
    qi = np.clip(np.round(Q).astype(int), [0, 0], [W - 1, H - 1])
    return img[qi[..., 1], qi[..., 0]]


def photo_crease_segments(job, key: str, r: dict, cam, theta: dict, near: int, decal_mask: np.ndarray | None = None,
                          hinge_mask: np.ndarray | None = None) -> dict:
    """Crease segments on the near temple's outer face of one view, with the model's verdict on each."""
    img = job.photo(key)
    fg = job.matte(key)
    L = lightness(img)
    region = outer_face_region(r, cam, near)
    pts_ok = np.isfinite(r["points"][..., 0])
    if not region.any():
        return {"usable": False, "reason": "no outer-face pixels", "segments": []}
    med = np.nanmedian(r["points"][region], axis=0)
    ppm = float(C.px_per_mm_at(cam, job.frame, med))
    toward = raster.camera_basis(cam)[2]
    cosv = float(np.median(np.abs(r["normal"][region] @ toward)))
    usable = ppm >= RR.VIEW_MIN_PX_PER_MM and cosv >= RR.VIEW_MIN_COS
    roi = ndimage.binary_erosion(region, iterations=RR.MASK_SILHOUETTE_PX) & fg & pts_ok
    if decal_mask is not None:
        roi &= ~ndimage.binary_dilation(decal_mask, iterations=RR.MASK_DECAL_PX)
    if hinge_mask is not None:
        roi &= ~hinge_mask
    roi &= L <= RR.MASK_HIGHLIGHT_L
    Ls = cv2.GaussianBlur(L, (0, 0), RR.CANNY_SIGMA_PX)
    edges = cv2.Canny(np.clip(Ls * 2.55, 0, 255).astype(np.uint8), RR.CANNY_LOW, RR.CANNY_HIGH) > 0
    edges &= roi
    segs = cv2.HoughLinesP(edges.astype(np.uint8) * 255, 1, np.pi / 360, threshold=RR.HOUGH_THRESHOLD,
                           minLineLength=max(2, int(RR.HOUGH_MIN_MM * ppm)), maxLineGap=RR.HOUGH_MAX_GAP_PX)
    segs = [] if segs is None else [tuple(int(v) for v in s) for s in np.asarray(segs).reshape(-1, 4)]
    tregion = (r["tag"] == near)
    crease = model_crease_mask(r, tregion)
    cd = ndimage.distance_transform_edt(~crease) / ppm
    nrm = r["normal"].copy()
    flip = (nrm @ toward) < 0
    nrm[flip] *= -1
    out = []
    for x0, y0, x1, y1 in segs:
        p0, p1 = np.array([x0, y0], float), np.array([x1, y1], float)
        ln = float(np.linalg.norm(p1 - p0))
        t = (p1 - p0) / ln
        nn = np.array([-t[1], t[0]])
        ss = np.linspace(0, 1, max(8, int(ln)))
        P = p0[None] + ss[:, None] * (p1 - p0)[None]
        okA = _samp(roi.astype(float), P, nn, RR.STEP_OFFSETS_PX) > 0
        okB = _samp(roi.astype(float), P, nn, (-RR.STEP_OFFSETS_PX[1], -RR.STEP_OFFSETS_PX[0])) > 0
        if okA.mean() < 0.6 or okB.mean() < 0.6:
            continue
        la = _samp(Ls, P, nn, RR.STEP_OFFSETS_PX)
        lb = _samp(Ls, P, nn, (-RR.STEP_OFFSETS_PX[1], -RR.STEP_OFFSETS_PX[0]))
        step = float(np.median(la[okA]) - np.median(lb[okB]))
        if abs(step) < RR.STEP_MIN_L:
            continue
        na = np.stack([_samp(nrm[..., k], P, nn, RR.DN_SAMPLE_PX) for k in range(3)], -1)[okA]
        nb = np.stack([_samp(nrm[..., k], P, nn, (-RR.DN_SAMPLE_PX[1], -RR.DN_SAMPLE_PX[0])) for k in range(3)],
                      -1)[okB]
        ma, mb = na.mean(0), nb.mean(0)
        dn = float(np.degrees(np.arccos(np.clip(ma @ mb / (np.linalg.norm(ma) * np.linalg.norm(mb)), -1, 1))))
        qi = np.clip(np.round(P).astype(int), [0, 0], [fg.shape[1] - 1, fg.shape[0] - 1])
        dist = cd[qi[:, 1], qi[:, 0]]
        near_mm = float(np.median(dist))
        verdict = "OK" if dn >= RR.DN_OK_DEG else ("SHALLOW" if near_mm <= RR.CREASE_NEAR_MM else "FLAG")
        hp = r["points"][qi[:, 1], qi[:, 0]]
        good = np.isfinite(hp[:, 0]) & (r["tag"][qi[:, 1], qi[:, 0]] == near)
        lift = None
        if good.sum() >= 3:
            ch = chart_of(hp[good], near, job.hinge["z_mm"], theta.get(near, 0.0), job.hinge["axis_xz_mm"])
            lift = ch[:, :2].tolist()
        out.append({"px": [x0, y0, x1, y1], "len_mm": ln / ppm, "step_L": step, "dn_deg": dn,
                    "model_crease_mm": near_mm, "verdict": verdict, "lift_dy": lift})
    return {"usable": bool(usable), "ppm": ppm, "view_cos": cosv, "segments": out, "near_side": near}


def _line_fit(P: np.ndarray):
    c = P.mean(0)
    _, _, vt = np.linalg.svd(P - c)
    return c, vt[0]


def confirm_creases(seg05: list, seg01: list) -> list:
    """Pairs of 05 / 01 segments whose (d, y) lifts lie within CONFIRM_MEDIAN_MM median of each other's line."""
    pairs = []
    for i, a in enumerate(seg05):
        if not a.get("lift_dy"):
            continue
        A = np.array(a["lift_dy"])
        ca, ua = _line_fit(A)
        best = None
        for j, b in enumerate(seg01):
            if not b.get("lift_dy"):
                continue
            B = np.array(b["lift_dy"])
            nrm = np.array([-ua[1], ua[0]])
            dist = float(np.median(np.abs((B - ca) @ nrm)))
            ta, tb = (A - ca) @ ua, (B - ca) @ ua
            overlap = min(ta.max(), tb.max()) - max(ta.min(), tb.min())
            if overlap <= 0:
                continue
            if dist <= RR.CONFIRM_MEDIAN_MM and (best is None or dist < best[1]):
                best = (j, dist)
        if best is not None:
            pairs.append({"seg05": i, "seg01": best[0], "median_mm": best[1]})
    return pairs


def r2_verdict(view_segments: dict, confirmed: list, seg05: list) -> dict:
    """R2 per side view: no FLAG >= R2_FLAG_MIN_MM, length-weighted median dn >= R2_MEDIAN_DN_DEG; every confirmed
    crease has a model crease within R2_CONFIRMED_NEAR_MM with dn >= R2_CONFIRMED_DN_DEG."""
    out = {"views": {}, "pass": True}
    for key, vs in view_segments.items():
        segs = vs.get("segments", [])
        if not vs.get("usable", False):
            out["views"][key] = {"usable": False}
            continue
        flags = [s for s in segs if s["verdict"] == "FLAG" and s["len_mm"] >= RR.R2_FLAG_MIN_MM]
        if segs:
            dn = np.array([s["dn_deg"] for s in segs])
            w = np.array([s["len_mm"] for s in segs])
            o = np.argsort(dn)
            cw = np.cumsum(w[o]) / w.sum()
            wmed = float(dn[o][np.searchsorted(cw, 0.5)])
        else:
            wmed = float("nan")
        ok = not flags and np.isfinite(wmed) and wmed >= RR.R2_MEDIAN_DN_DEG
        out["views"][key] = {"segments": len(segs), "flags_ge_4mm": len(flags), "weighted_median_dn_deg": wmed,
                             "length_mm": float(sum(s["len_mm"] for s in segs)),
                             "verdicts": {v: int(sum(s["verdict"] == v for s in segs)) for v in ("OK", "SHALLOW",
                                                                                                  "FLAG")},
                             "pass": bool(ok)}
        out["pass"] &= bool(ok)
    conf = []
    for c in confirmed:
        s = seg05[c["seg05"]]
        good = s["model_crease_mm"] <= RR.R2_CONFIRMED_NEAR_MM and s["dn_deg"] >= RR.R2_CONFIRMED_DN_DEG
        conf.append({**c, "model_crease_mm": s["model_crease_mm"], "dn_deg": s["dn_deg"], "pass": bool(good)})
        out["pass"] &= bool(good)
    out["confirmed"] = conf
    return out


def crease_transfer(job, model, theta05: dict, cam05, cam02, theta02: dict, line05_px, line02_px, near: int = 1,
                    n: int = 25) -> dict | None:
    """Lift points of a 05 crease line (px endpoints) onto the model surface, project into 02, and measure the
    perpendicular distance to the 02 crease line (mm at each point's local px/mm). None when < 5 points land on the
    near temple."""
    from .relief_views import rot_about_hinge
    V, F, tag = model.composite(job, theta05)
    sc = raster.get_scene(V, F, job.frame)
    p0, p1 = np.asarray(line05_px[0], float), np.asarray(line05_px[1], float)
    t = np.linspace(0.1, 0.9, n)[:, None]
    uv = p0[None] * (1 - t) + p1[None] * t
    hit = sc.cast(cam05, uv[:, 0], uv[:, 1], want_points=True)
    ok = hit["hit"] & (tag[np.maximum(hit["face_id"], 0)] == near)
    if ok.sum() < 5:
        return None
    P = hit["points"][ok]
    th05, th02 = theta05.get(near, 0.0), theta02.get(near, 0.0)
    if th05 != th02:                                   # carry the points with the temple to the 02 opening
        P = rot_about_hinge(rot_about_hinge(P, near, -th05, job.hinge["axis_xz_mm"]), near, th02,
                            job.hinge["axis_xz_mm"])
    q = C.project(job.frame.to_norm(P), cam02)
    a, b = np.asarray(line02_px[0], float), np.asarray(line02_px[1], float)
    dv = (b - a) / np.linalg.norm(b - a)
    nv = np.array([-dv[1], dv[0]])
    dist = (q - a) @ nv
    ppm = np.array([C.px_per_mm_at(cam02, job.frame, p) for p in P])
    return {"mean_abs_mm": float(np.mean(np.abs(dist / ppm))), "signed_mm": float(np.mean(dist / ppm)),
            "n": int(ok.sum())}


def invented_creases(job, key: str, r: dict, near: int, cam) -> dict:
    """Advisory: model crease pixels (>= INVENTED_CREASE_DEG) on the near temple with no photo Canny edge within
    INVENTED_CREASE_PX; reported as the share of model crease length unsupported."""
    region = outer_face_region(r, cam, near)
    crease = model_crease_mask(r, ndimage.binary_erosion(region, iterations=2), RR.INVENTED_CREASE_DEG)
    if not crease.any():
        return {"model_crease_px": 0, "unsupported_share": 0.0, "invented": False}
    L = lightness(job.photo(key))
    Ls = cv2.GaussianBlur(L, (0, 0), RR.CANNY_SIGMA_PX)
    edges = cv2.Canny(np.clip(Ls * 2.55, 0, 255).astype(np.uint8), RR.CANNY_LOW, RR.CANNY_HIGH) > 0
    near_edge = ndimage.binary_dilation(edges, iterations=RR.INVENTED_CREASE_PX)
    lab, nlab = ndimage.label(crease, structure=np.ones((3, 3)))
    unsupported = []
    for i in range(1, nlab + 1):
        comp = lab == i
        if comp.sum() < 20:
            continue
        share = float((comp & near_edge).sum() / comp.sum())
        if share < 0.3:
            ys, xs = np.nonzero(comp)
            unsupported.append({"px": int(comp.sum()), "supported_share": share,
                                "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]})
    tot = int(crease.sum())
    un = int(sum(u["px"] for u in unsupported))
    return {"model_crease_px": tot, "unsupported_share": un / max(tot, 1), "invented": bool(unsupported),
            "components": unsupported}


def _sh9(n):
    x, y, z = n[:, 0], n[:, 1], n[:, 2]
    return np.stack([np.ones_like(x), x, y, z, x * y, y * z, x * z, x * x - y * y, 3 * z * z - 1], 1)


def shading_residual(job, key: str, r: dict, near: int, cam) -> dict:
    """Report only (§5: two measurements say a lighting model cannot grade facets): an order-2 SH fit of L* vs the
    model normal on the front part (robust), its RMS, and the residual on the near temple's outer face."""
    L = lightness(job.photo(key))
    fg = job.matte(key)
    toward = raster.camera_basis(cam)[2]
    n = r["normal"].copy()
    n[(n @ toward) < 0] *= -1
    front = ndimage.binary_erosion(r["tag"] == 0, iterations=4) & fg & (L < RR.MASK_HIGHLIGHT_L) & (L > 40)
    temple = ndimage.binary_erosion(outer_face_region(r, cam, near), iterations=4) & fg & (L < RR.MASK_HIGHLIGHT_L)
    if front.sum() < 200 or temple.sum() < 50:
        return {"available": False}
    A = _sh9(n[front])
    y = L[front]
    w = np.ones(len(y))
    for _ in range(3):
        coef, *_ = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)
        res = y - A @ coef
        s = 1.4826 * np.median(np.abs(res)) + 1e-6
        w = np.where(np.abs(res) <= 1.5 * s, 1.0, 1.5 * s / np.abs(res))
    rt = L[temple] - _sh9(n[temple]) @ coef
    return {"available": True, "front_rms_L": float(np.sqrt(np.mean((y - A @ coef) ** 2))),
            "temple_rms_L": float(np.sqrt(np.mean(rt ** 2))), "temple_median_abs_L": float(np.median(np.abs(rt))),
            "note": "report only, never gated"}


def decal_mask(job, key: str, cam, theta: dict | None = None) -> np.ndarray:
    """The delivered GLB's thin decal parts (the WP3b skip list: hallmarks, ribbon) rendered through the camera."""
    from . import fitmesh
    full = fitmesh.load_fit_mesh(job.glb, frame=job.frame)
    skip = job.cfg["skip"]
    ids = [i for i, nm in enumerate(full.node_names) if any(s in nm for s in skip)]
    if not ids:
        return np.zeros(job.shape(key), bool)
    sel = np.isin(full.part, ids)
    V, F = full.V, full.F[sel]
    return raster.get_scene(V, F, job.frame).render(cam, job.shape(key), 1)["mask"]
