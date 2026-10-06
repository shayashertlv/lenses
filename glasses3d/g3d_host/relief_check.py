"""WP6 check_relief on the host (plan/WP6_DESIGN.md §5): the GLB's temples through the shared measure core
(g3d/temple/measure.py) plus the photo verdicts.

    glb_temples(glb, hinge_z=None)            per side: the temple body (role temple, else position) in its chart
                                              (from its g3d_temple extras when present, else the world chart)
    check_relief(glb, job=None, ...)          geometry numbers and flags per side (+ the photo verdicts when the
                                              caller passes a relief_views Job, a Model and its fitted views)
    temple_registration_mm(...)               a side view's registration at the near temple (outline offset, mm)
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

import json
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


def _node_temple_extras(g, ni) -> dict | None:
    """The g3d_temple record faceted_temple writes on the object (a JSON string or an object in node or mesh extras),
    or None."""
    node = g.nodes[ni] if 0 <= ni < len(g.nodes) else {}
    for ex in ((node.get("extras") or {}),
               ((g.meshes[node["mesh"]].get("extras") or {}) if "mesh" in node else {})):
        v = ex.get("g3d_temple")
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = None
        if isinstance(v, dict) and isinstance(v.get("canonical_spec"), dict):
            return v
    return None


def glb_temples(glb_path, hinge_z: float | None = None) -> dict:
    """{side: {"V", "F" (chart), "node", "chart", "W", "T" (world mm), "zone_end", "facet_planes"}}: per side the
    temple-role body reaching furthest back (>= 60 mm); without roles, the part behind the lenses at |x| > 45 mm
    reaching furthest back. The chart comes from the temple's g3d_temple extras when the GLB carries them (the
    spec's hinge and opening, its zone end and facet planes: the same chart as the tool's receipt and the Blender
    check), else the world chart at opening 0 (d = hinge_z - z, X = |x|; hinge_z defaults to the temple's front)."""
    g = glbmod.load(glb_path)
    by_node: dict = {}
    for p in g.primitives:
        if p.mode != 4 or len(p.triangles) == 0:
            continue
        W = p.positions * 1000.0
        rec = by_node.setdefault(p.node_index, {"name": p.node_name, "W": [], "T": [], "n": 0,
                                                "role": _node_role(g, p.node_index),
                                                "temple": _node_temple_extras(g, p.node_index)})
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
        ext = by_node[ni]["temple"]
        if ext is not None:
            from ..g3d.temple import build as TB
            from ..g3d.temple import report as RP
            spec = ext["canonical_spec"]
            h = spec["hinge"]
            V = TB.to_local(W, h["z_mm"], h["axis_xz_mm"], h["opening_deg"], side=s)
            F = MS.chart_faces(T, s)
            out[s] = {"V": V, "F": F, "W": W, "T": T, "node": by_node[ni]["name"], "chart": "g3d_temple extras",
                      "zone_end": float(spec["zone_end_d"]),
                      "facet_planes": RP.facet_planes_chart(TB.build_temple(spec))}
            continue
        hz = float(W[:, 2].max()) if hinge_z is None else float(hinge_z)
        V, F = MS.chart_mesh(W, T, s, hz)
        out[s] = {"V": V, "F": F, "W": W, "T": T, "node": by_node[ni]["name"],
                  "chart": f"world (hinge z {hz:.2f}{' from the temple front' if hinge_z is None else ''})",
                  "zone_end": None, "facet_planes": None}
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
        m = MS.measure_temple(t["V"], t["F"], zone_end=t["zone_end"] if t["zone_end"] is not None else zone_end,
                              facet_planes=t["facet_planes"], photo_creases=photo_creases)
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


def temple_registration_mm(job, key: str, r: dict, cam, near: int, zone_end: float = RR.HINGE_ZONE_D[1]) -> dict:
    """How well the model sits on the photo at the near temple of a side view (§5 "registered <= 1 mm at the
    temple"): the mean |photo - model| silhouette offset (relief_views.side_outline) of the top and of the bottom
    edge over the facet zone's columns (station d < zone_end), where the crease lifts and the model normals are read;
    `registration_mm` = the worse of the two edges (both must sit within the limit)."""
    from .relief_views import side_outline
    so = side_outline(job, key, r, cam, near)
    out, n = {}, 0
    for e in ("top", "bottom"):
        A = np.asarray(so[e]["samples"], float).reshape(-1, 2)
        A = A[np.isfinite(A[:, 0]) & (A[:, 0] < zone_end)]
        out[f"{e}_mm"] = float(np.mean(np.abs(A[:, 1]))) if len(A) else None
        n += len(A)
    edges = [v for v in (out["top_mm"], out["bottom_mm"]) if v is not None]
    out["n"] = int(n)
    out["registration_mm"] = float(max(edges)) if edges else float("inf")
    return out


def photo_crease_segments(job, key: str, r: dict, cam, theta: dict, near: int, decal_mask: np.ndarray | None = None,
                          hinge_mask: np.ndarray | None = None, registration_mm: float | None = None) -> dict:
    """Crease segments on the near temple's outer face of one view, with the model's verdict on each. The view is
    `usable` for grading (R2) when the outer face is seen at >= VIEW_MIN_PX_PER_MM and a viewing cosine >= VIEW_MIN_COS
    and, when the caller gives the model's `registration_mm` at that temple (temple_registration_mm), it is <=
    VIEW_MAX_REG_MM; the segments are listed either way."""
    img = job.photo(key)
    fg = job.matte(key)
    L = lightness(img)
    region = outer_face_region(r, cam, near)
    pts_ok = np.isfinite(r["points"][..., 0])
    if not region.any():
        return {"usable": False, "reason": "no outer-face pixels", "segments": [], "registration_mm": registration_mm}
    med = np.nanmedian(r["points"][region], axis=0)
    ppm = float(C.px_per_mm_at(cam, job.frame, med))
    toward = raster.camera_basis(cam)[2]
    cosv = float(np.median(np.abs(r["normal"][region] @ toward)))
    why = []
    if ppm < RR.VIEW_MIN_PX_PER_MM:
        why.append(f"outer face at {ppm:.2f} px/mm < {RR.VIEW_MIN_PX_PER_MM}")
    if cosv < RR.VIEW_MIN_COS:
        why.append(f"viewing cosine {cosv:.3f} < {RR.VIEW_MIN_COS}")
    if registration_mm is not None and not registration_mm <= RR.VIEW_MAX_REG_MM:
        why.append(f"registered {registration_mm:.3f} mm > {RR.VIEW_MAX_REG_MM} at the temple")
    usable = not why
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
    return {"usable": bool(usable), "ppm": ppm, "view_cos": cosv, "registration_mm": registration_mm,
            "unusable_because": why, "segments": out, "near_side": near}


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


R2_VIEWS = ("05", "01")          # §5: the crease views (05 sees the +X temple, 01 the -X temple)


def r2_verdict(view_segments: dict, confirmed: list, seg05: list, required=R2_VIEWS) -> dict:
    """R2 per side view: no FLAG >= R2_FLAG_MIN_MM, length-weighted median dn >= R2_MEDIAN_DN_DEG; every confirmed
    crease has a model crease within R2_CONFIRMED_NEAR_MM with dn >= R2_CONFIRMED_DN_DEG. R2 is judged only when EVERY
    `required` crease view (§5: 05 and 01) is usable: confirmation needs both, and a view a model is misregistered in
    must not shrink what it is graded on. With a required view missing or unusable R2 does not pass and records
    `judged` False with the reason (the usable views are still graded, for the record)."""
    out = {"views": {}, "pass": True, "judged": True}
    usable = 0
    for key, vs in view_segments.items():
        segs = vs.get("segments", [])
        if not vs.get("usable", False):
            out["views"][key] = {"usable": False, "because": vs.get("unusable_because") or vs.get("reason")}
            continue
        usable += 1
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
    out["usable_views"] = usable
    missing = [k for k in (required or ()) if not (view_segments.get(k) or {}).get("usable", False)]
    if missing:
        def because(k):
            vs = view_segments.get(k) or {}
            return "; ".join(vs.get("unusable_because") or [vs.get("reason") or "missing"])
        out["pass"] = False
        out["judged"] = False
        out["why"] = ("not judged: crease view(s) " + ", ".join(f"{k} ({because(k)})" for k in missing)
                      + " unusable; R2 needs both " + " and ".join(required) + " (§5)")
    elif not usable:
        out["pass"] = False
        out["judged"] = False
        out["why"] = "no usable crease view: R2 cannot be judged"
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


def decal_parts(glb_path, hinge: dict | None = None) -> dict:
    """The delivered GLB's surface decals on its temples, found by GEOMETRY (WP6_DESIGN §3 "Decals", the same rule
    g3d.faceted_temple uses in Blender: measure.is_surface_decal), never by part names: every non-temple part on a
    temple's side (sign of x) whose vertices all lie within DECAL_NEAR_MM of that temple's surface and that is open or
    thin (bbox, or its signed distances' spread), EXCEPT the hinge parts (§3 "Hinge parts are never moved":
    measure.hinge_part_kind by partRole and bbox, the set g3d.faceted_temple's hinge report grades), which are never
    decals however close and thin. The temples are the parts reaching >= 60 mm back, side by x sign
    (relief_views.temple_parts). `hinge` = {"z_mm", "axis_xz_mm"} (a job's hinge); without it each side's hinge plane
    is its temple's front and the axis the bbox centre of its temple-role knuckles (measure.knuckle_axis). Returns
    {"V", "F" (the decal faces), "parts": [{node, side, ...}], "hinge_parts": [{node, side, kind}]}."""
    from . import fitmesh
    from .relief_views import temple_parts
    full = fitmesh.load_fit_mesh(glb_path)
    tp = temple_parts(full)
    g = glbmod.load(glb_path)
    role_of = {}
    for p in g.primitives:
        role_of.setdefault(p.node_name, _node_role(g, p.node_index))
    boxes = {}
    for pid, nm in enumerate(full.node_names):
        F = full.F[full.part == pid]
        if len(F):
            Vp = full.V[np.unique(F)]
            boxes[pid] = (Vp.min(axis=0), Vp.max(axis=0), role_of.get(nm), len(Vp))
    hinge_of = {}
    for s, pid in tp.items():
        if hinge is not None:
            hinge_of[s] = (float(hinge["z_mm"]), (hinge.get("axis_xz_mm") or [None])[0])
        else:
            hz = float(boxes[pid][1][2])
            hinge_of[s] = (hz, (MS.knuckle_axis([(lo, hi, r) for lo, hi, r, _ in boxes.values()], s, hz) or [None])[0])
    hinge_ids, hinge_rec = set(), []
    for pid, (lo, hi, role, nv) in boxes.items():
        if pid in tp.values():
            continue
        for s in (1, -1):
            kind = MS.hinge_part_kind(lo, hi, role, s, hinge_of[s][1], hinge_of[s][0], nv)
            if kind is not None:
                hinge_ids.add(pid)
                hinge_rec.append({"node": full.node_names[pid], "side": s, "kind": kind})
                break
    o3d = raster.o3d
    scenes = {}
    for s, pid in tp.items():
        sc = o3d.t.geometry.RaycastingScene()
        sc.add_triangles(o3d.core.Tensor(full.V.astype(np.float32)),
                         o3d.core.Tensor(full.F[full.part == pid].astype(np.uint32)))
        scenes[s] = sc
    keep, parts = [], []
    for pid, nm in enumerate(full.node_names):
        F = full.F[full.part == pid]
        if len(F) == 0 or pid in tp.values() or pid in hinge_ids:
            continue
        V = full.V[np.unique(F)]
        lo, hi = V.min(axis=0), V.max(axis=0)
        s = 1 if lo[0] + hi[0] > 0 else -1
        sd = scenes[s].compute_signed_distance(o3d.core.Tensor(V.astype(np.float32))).numpy().astype(np.float64)
        if float(np.abs(sd).max()) > MS.DECAL_NEAR_MM:
            continue
        Fw = MS.weld(full.V, F)                       # GLB seams duplicate positions: open = a welded edge used once
        e = np.sort(np.concatenate([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]]), axis=1)
        _, cnt = np.unique(e, axis=0, return_counts=True)
        is_open = bool(np.any(cnt == 1))
        if MS.is_surface_decal(sd, float(np.min(hi - lo)), is_open):
            keep.append(pid)
            parts.append({"node": nm, "side": s, "open": is_open, "bbox_min_mm": float(np.min(hi - lo)),
                          "signed_mm": [float(sd.min()), float(sd.max())]})
    F = full.F[np.isin(full.part, keep)] if keep else np.zeros((0, 3), np.int64)
    return {"V": full.V, "F": F, "parts": parts, "hinge_parts": hinge_rec}


def decal_mask(job, key: str, cam, theta: dict | None = None) -> np.ndarray:
    """The delivered GLB's temple surface decals (decal_parts: by geometry) rendered through the camera; empty for a
    job without a GLB (synthetic photos)."""
    if getattr(job, "glb", None) is None:
        return np.zeros(job.shape(key), bool)
    dec = job.__dict__.get("_decals")
    if dec is None or dec[0] != str(job.glb):
        dec = (str(job.glb), decal_parts(job.glb, hinge=getattr(job, "hinge", None)))
        job.__dict__["_decals"] = dec
    d = dec[1]
    if not len(d["F"]):
        return np.zeros(job.shape(key), bool)
    return raster.get_scene(d["V"], d["F"], job.frame).render(cam, job.shape(key), 1)["mask"]


# --------------------------------------------------------------------------------------------- what Astra gets (§5)
VERDICT_RGB = {"OK": (0, 180, 0), "SHALLOW": (255, 140, 0), "FLAG": (230, 0, 0)}
MODEL_CREASE_RGB = (0, 200, 255)


def _header(width: int, lines: list[str], h: int = 22) -> np.ndarray:
    out = np.full((h * len(lines) + 6, width, 3), 255, np.uint8)
    for i, t in enumerate(lines):
        cv2.putText(out, t[: max(10, width // 8)], (6, h * (i + 1) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1,
                    cv2.LINE_AA)
    return out


def crease_crop(job, key: str, r: dict, seg: dict, near: int, path=None, *, pad: int = 30, scale: int = 3,
                label: str = "") -> np.ndarray | None:
    """One view's near-temple crop for Astra (§5): the photo, the model's creases (normal change >= MODEL_CREASE_DEG,
    cyan) and every photo crease segment coloured by its verdict (OK green, SHALLOW orange, FLAG red), with the
    view's usability. `r` = render_model(..., points=True, normals=True) of the model the segments were graded on."""
    region = r["tag"] == near
    ys, xs = np.nonzero(region)
    if not len(xs):
        return None
    H, W = region.shape
    x0, y0 = max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad)
    x1, y1 = min(W, int(xs.max()) + pad + 1), min(H, int(ys.max()) + pad + 1)
    img = np.ascontiguousarray(job.photo(key)[..., :3]).copy()
    img[model_crease_mask(r, region)] = MODEL_CREASE_RGB
    crop = cv2.resize(img[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    to = lambda u, v: (int(round((u - x0 + 0.5) * scale)), int(round((v - y0 + 0.5) * scale)))
    counts = {v: 0 for v in VERDICT_RGB}
    for s in seg.get("segments", []):
        u0, v0, u1, v1 = s["px"]
        cv2.line(crop, to(u0, v0), to(u1, v1), VERDICT_RGB[s["verdict"]], 2, cv2.LINE_AA)
        counts[s["verdict"]] += 1
    lines = [f"{label} view {key} ({'+X' if near > 0 else '-X'} temple)  usable: {seg.get('usable')}"
             + ("" if seg.get("usable") else f" ({'; '.join(seg.get('unusable_because') or [seg.get('reason', '')])})"),
             "segments: " + ", ".join(f"{k} {n}" for k, n in counts.items())
             + "  (green OK, orange SHALLOW, red FLAG; cyan = model creases)"]
    out = np.vstack([_header(crop.shape[1], lines), crop])
    if path is not None:
        from PIL import Image
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out).save(path)
    return out


def relief_sheet(spec: dict, path=None, *, x: dict | None = None, sigma: dict | None = None,
                 front_bound: dict | None = None, unobserved: list | None = None, nodes=("A", "B", "C", "K", "E"),
                 label: str = "", px_per_mm: float = 14.0, sigma_status: dict | None = None) -> dict:
    """The relief sheet for Astra (§5): sections of the spec's +X temple at the photo nodes' stations (outer profile
    black, inner face grey, the base outer surface x_in + T dashed blue, each node's own relief marked red), with the
    fitted values, their sigma, the front bound and the UNOBSERVED list as text. With `sigma_status` (relief_fit's
    sigma_report statuses) a sigma is printed only for status "ok": any other status (a penalty acting along the
    parameter, a band bound, not at a minimum, ...) prints the status instead, so a constraint's wall is never shown
    to Astra as a photo uncertainty. Returns {"stations": [...], "image", "sigma_shown": {param: bool}}."""
    from ..g3d.temple import build as TB
    tb = TB.build_temple(spec)
    ps = tb.spec
    rows = []
    for n in nodes:
        if n not in ps.nodes:
            continue
        d = float(np.clip(ps.nodes[n][0], ps.d0 + 0.25, ps.d1 - 0.25))
        seg = MS.slice_d(tb.V, tb.tris, d)
        if not len(seg):
            continue
        ylo, yhi = float(ps.y_bot(d)), float(ps.y_top(d))
        ys = np.arange(ylo, yhi + 1e-9, 0.05)
        xo, xi = MS.section_profile(seg, ys)
        rn = ps.nodes[n][2] if ps.nodes[n][2] is not None else tb.sol.node_r.get(n)
        rows.append({"node": n, "d": d, "y": float(ps.nodes[n][1]), "r": None if rn is None else float(rn),
                     "y_range": [ylo, yhi], "x_base": float(ps.x_base(d)), "x_in": float(ps.inner(d)),
                     "x_out_max": float(np.nanmax(xo)), "ys": ys, "xo": xo, "xi": xi})
    panels = []
    for row in rows:
        ylo, yhi = row["y_range"]
        X0 = row["x_in"] - 1.0
        X1 = max(row["x_out_max"], row["x_base"]) + 1.5
        w, h = int((X1 - X0) * px_per_mm) + 20, int((yhi - ylo + 2.0) * px_per_mm) + 20
        P = np.full((h, max(w, 150), 3), 255, np.uint8)
        to = lambda X, Y: (int(round(10 + (X - X0) * px_per_mm)), int(round(10 + (yhi + 1.0 - Y) * px_per_mm)))
        ok = np.isfinite(row["xo"]) & np.isfinite(row["xi"])
        pts_o = np.array([to(X, Y) for X, Y in zip(row["xo"][ok], row["ys"][ok])], np.int32)
        pts_i = np.array([to(X, Y) for X, Y in zip(row["xi"][ok], row["ys"][ok])], np.int32)
        if len(pts_o) > 1:
            cv2.polylines(P, [pts_i], False, (150, 150, 150), 1, cv2.LINE_AA)
            cv2.polylines(P, [pts_o], False, (0, 0, 0), 2, cv2.LINE_AA)
        for Y in np.arange(ylo, yhi, 1.0):                                 # the base outer surface, dashed
            cv2.line(P, to(row["x_base"], Y), to(row["x_base"], min(Y + 0.5, yhi)), (40, 90, 230), 1)
        if row["r"] is not None and ylo <= row["y"] <= yhi:
            cv2.circle(P, to(row["x_base"] + row["r"], row["y"]), 4, (220, 0, 0), -1, cv2.LINE_AA)
        cap = f"{row['node']}: d {row['d']:.1f}" + ("" if row["r"] is None else f" r {row['r']:.2f}")
        panels.append(np.vstack([_header(P.shape[1], [cap]), P]))
    text = [f"{label} relief sheet (+X temple; -X is its mirror). sections at the photo nodes, mm"]
    shown = {}

    def _sig(k):
        if not sigma or k not in sigma:
            shown[k] = False
            return ""
        st = (sigma_status or {}).get(k)
        if sigma_status is not None and st != "ok":
            shown[k] = False
            return f" (sigma n/a: {st})"
        shown[k] = bool(np.isfinite(sigma[k]))
        return f" +- {sigma[k]:.3f}" if np.isfinite(sigma[k]) else " (sigma inf)"
    if x:                         # packed so no line outgrows the header's 900 // 8 characters
        line = "fitted:"
        for k, v in x.items():
            item = f" {k} {v:.3f}{_sig(k)},"
            if len(line) + len(item) > 110 and line != "fitted:":
                text.append(line.rstrip(","))
                line = "fitted:"
            line += item
        text.append(line.rstrip(","))
    if front_bound is not None:
        text += [f"front bound (04): clear {front_bound.get('clear')}, hinge-zone overshoot "
                 f"{front_bound.get('hinge_zone_max_mm', 0.0):.2f} mm / {front_bound.get('hinge_zone_mm2', 0.0):.1f} mm2,"
                 f" arm tops {front_bound.get('arm_tops_mm2', 0.0):.1f} mm2"]
    if unobserved:
        text += ["UNOBSERVED: " + "; ".join(str(u.get("parameter")) for u in unobserved)]
    if panels:
        Hm = max(p.shape[0] for p in panels)
        body = np.hstack([np.pad(p, ((0, Hm - p.shape[0]), (0, 8), (0, 0)), constant_values=255) for p in panels])
    else:
        body = np.full((40, 600, 3), 255, np.uint8)
    W = max(body.shape[1], 900)
    body = np.pad(body, ((0, 0), (0, W - body.shape[1]), (0, 0)), constant_values=255)
    img = np.vstack([_header(W, text), body])
    if path is not None:
        from PIL import Image
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img).save(path)
    stations = [{k: v for k, v in row.items() if k not in ("ys", "xo", "xi")} for row in rows]
    return {"stations": stations, "image": img, "sigma_shown": shown}
