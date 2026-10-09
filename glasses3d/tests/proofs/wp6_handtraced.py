"""WP6 HAND-TRACED TOOL TEST (2026-10-06, fix round 3 2026-10-07; $0, NOT a proof result, NOT a delivered model): the
re-diagnosis question after the Prada proof's STOP-I: can g3d.faceted_temple reproduce the photographed Prada PRS 17WS
temple when it is given GOOD parameters? Claude hand-traces the parameters from the six real photos (PLAN §0.2: a $0
test may call the TOOL with hand-measured parameters), the tool builds them on a COPY of the Prada scene, and the frozen
gates grade the result against the FITTED SLAB (WP6_DESIGN §0.5 / §7 / §8: the same fit with every r = 0; here the
diagnostic_resume fitted slab, the fit whose candidate gave the hand trace its outline and arm) and the delivered GLB.

Hand trace (what each value rests on; nothing optimised):
    outline         the diagnostic fit's re-traced side outline (G3: 0.08 / 0.16-0.24 mm in 05 / 01)
    corners A B C   the 05 and 01 crease lines (wp6_prada_guides.json, snapped to the L* ridge) lifted onto THIS
                    model's surface through THIS model's own refit cameras (re-lifted until stable)
    topology        F1 band above A-B = the base (vertical, like the arm): the photos show F2 brighter than the band by
                    5.1-6.7 L* in 05 / 01 / 02 while the band, F3 and F4 are alike, and no step across A-B extended
                    behind B (-0.4 / -0.8 L* in 05, -1.6 / -1.2 in 01), so the band does not tilt up past F2 and meets
                    the arm without a crease; F2 = A-B-C, F3 = A-H-C (front-lower), F4 = B-C-E (rear-lower, E on the
                    bottom edge at d 55); A, B, E on the base (r 0), C proud
    inner face      x_in = 65.6 at d 0 (the hinge: leaf at |x| 64.85-65.45, frame knuckles to 65.66) falling linearly to
                    the fitted spec's own value at the zone end (d 60), so the fitted arm beyond it is kept exactly
    thickness       T = 2.2 at d 0 rising linearly to the fitted spec's value at d 60. NOT a photo measurement: its
                    source was not recorded when it was chosen (the "03 top face strip 2.3-2.7 mm" once quoted here has
                    no measurement on record). Fix round 3 measures the 03 strip (stage T0_EVIDENCE: pixels per d bin,
                    px/mm) and the front bound's room for relief at the hand trace's own 04 camera as a function of T0
                    (T0 and r_C trade directly)
    relief r_C      the FRONT BOUND'S CAP, not a depth measurement: the largest r_C whose hinge zone stays clear at the
                    model's own 04 camera (searched up to the family's r_C bound). No valid photo measurement of the
                    depth exists: the 5.3 +- 0.6 mm C - B triangulation is WITHDRAWN (WP6_DESIGN §0.6 / §2: the outer-
                    face-plane lift bias; the A-B intersection is ill-conditioned, 0.5 deg of 02 roll moves X_B 2.3 mm);
                    the B-C-only triangulation gives -0.05 +- 3.5 mm. R2 on this temple holds only while A-B's dihedral
                    stays above R2's median (stage R2_CONDITIONAL: every confirmed segment lies on A-B)
    (a first run with the zone end at 80, re-knotting the arm over d 60-80, is kept in data/wp6/handtraced_run1_zone80;
    the 2026-10-06 run graded R3 / R4 against an UNFITTED slab control under the "fitted slab" label and is superseded)

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_handtraced [--workers N] [--out-dir DIR]

Owner machine rule: a process pool of os.cpu_count() - 2 workers, each opted out of EcoQoS (relief_fit.FitPool);
progress at least every 60 s.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import pickle
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d.temple import build_temple, report  # noqa: E402
from glasses3d.g3d_host import raster  # noqa: E402
from glasses3d.g3d_host import relief_check as RC  # noqa: E402
from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.g3d_host import relief_trace as RT  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.g3d_host.spec import validate_spec  # noqa: E402
from glasses3d.tests.proofs import wp6_prada as P  # noqa: E402
from glasses3d.tests.proofs import wp6_prada_stages as PS  # noqa: E402
from glasses3d.tests.proofs import wp6_view_sheets as VS  # noqa: E402

LABEL = ("HAND-TRACED TOOL TEST (WP6 re-diagnosis 2026-10-06): Claude's hand-traced parameters built by "
         "g3d.faceted_temple on a Prada scene COPY, graded by the frozen gates; NOT a proof result, not a delivered model")
OUT = G3D / "data" / "wp6" / "handtraced"
DIAG = G3D / "data" / "wp6" / "diagnostic_resume"
H_LAB, S_LAB, D_LAB = "candidate (TOOL TEST)", "fitted slab", "delivered"     # the labels stage_gates grades
NAMES = {H_LAB: "hand-traced", S_LAB: "fitted slab (diagnostic_resume)", D_LAB: "delivered GLB"}
SLAB_FIT = DIAG / "fit_fitted_slab.pkl"         # the R3 / R4 comparator (WP6_DESIGN §0.5 / §7 / §8)
NEAR = {"05": 1, "01": -1, "02": 1}
CREASE_NODES = {"AB": ("A", "B"), "AC": ("A", "C"), "BC": ("B", "C")}

# the hand trace (module docstring); x_in_z / T_z: the fitted spec's own values at zone_end
HAND = {"x_in0": 65.6, "x_in_z": None, "T0": 2.2, "T_z": None, "zone_end": 60.0, "r_H": 0.0, "d_E": 55.0}
HAND_NOTES = {
    "T0": "2.2 mm at d 0: source not recorded when chosen; not a photo measurement (stage T0_EVIDENCE records the 03 "
          "strip and the front bound's room for relief versus T0)",
    "r_C": "front-bound cap; no valid depth measurement (the largest r_C whose hinge zone stays clear at the model's own "
           "04 camera)",
    "withdrawn": {"C_minus_B_abs_mm": 5.3, "C_minus_B_sigma_mm": 0.6,
                  "status": "WITHDRAWN: WP6_DESIGN §0.6 / §2 (outer-face-plane lift bias); the A-B intersection it "
                            "used is ill-conditioned (0.5 deg of 02 roll moves X_B by 2.3 mm); the B-C-only "
                            "triangulation gives -0.05 +- 3.5 mm (4.7 mm with the click). No longer used: the "
                            "2026-10-06 run took it as the upper end of the front-bound search and lifted the corners "
                            "through a surface at the r_C it implied"}}
LIFT_STABLE_MM = 0.25
R_C_STEP = 0.05
R_C_SEARCH_MAX = dict(RF.PYRAMID_FREE)["r_C"][1]          # the family's r_C bound: the front-bound search's upper end
T0_PROBE = (2.2, 2.3, 2.4, 2.5, 2.6, 3.0, 3.5, 4.0, 5.0)


# --------------------------------------------------------------------------------------------- the spec
def _yt(spec, d):
    t = np.asarray(spec["outline"]["top"], float)
    return float(np.interp(d, t[:, 0], t[:, 1]))


def _yb(spec, d):
    t = np.asarray(spec["outline"]["bottom"], float)
    return float(np.interp(d, t[:, 0], t[:, 1]))


def hand_spec(base: dict, *, A=None, B, C, r_C: float, x_in0: float, x_in_z: float, T0: float, T_z: float,
              zone_end: float, r_H: float, d_E: float, **_):
    """The hand-traced Prada temple as a g3d.temple/1 spec on `base` (a fitted spec: its outline and its arm beyond
    zone_end). A defaults to the hinge-end top corner; A and C may lie outside the outline (the tool clips)."""
    s = copy.deepcopy(base)
    d0 = s["outline"]["top"][0][0]
    s["inner"] = [[d0, x_in0], [zone_end, x_in_z]] + [list(k) for k in base["inner"] if k[0] > zone_end]
    s["thickness"] = [[d0, T0], [zone_end, T_z]] + [list(k) for k in base["thickness"] if k[0] > zone_end]
    s["zone_end_d"] = float(zone_end)
    A = (d0, _yt(s, d0)) if A is None else tuple(A)
    H = (d0 - 1.0, _yb(s, d0))
    E = (float(d_E), _yb(s, d_E))
    lo = -60.0
    dl = min(d0, A[0]) - 1.0
    s["nodes"] = {"A": [A[0], A[1], 0.0], "B": [B[0], B[1], 0.0], "C": [C[0], C[1], float(r_C)],
                  "H": [H[0], H[1], float(r_H)], "E": [E[0], E[1], 0.0], "A_l": [dl, A[1], None],
                  "H_lo": [dl, lo, None], "C_lo": [C[0], lo, None], "E_lo": [E[0], lo, None]}
    s["nodes"]["H"][0] = dl
    s["facets"] = {"F2": {"poly": ["A", "C", "B"], "plane": "nodes"},
                   "F3": {"poly": ["A", "A_l", "H", "H_lo", "C_lo", "C"], "plane": "nodes"},
                   "F4": {"poly": ["B", "C", "C_lo", "E_lo", "E"], "plane": "nodes"}}
    s["chamfers"] = []
    return s


def abs_c_minus_b(spec: dict) -> float:
    """C - B in absolute X (what the 05 x 02 triangulation measured): r_C plus the base's own fall between them."""
    tb = build_temple(spec)
    ps = tb.spec
    c, b = ps.nodes["C"], ps.nodes["B"]
    return float(ps.x_base(c[0]) + c[2] - ps.x_base(b[0]) - (b[2] or 0.0))


# --------------------------------------------------------------------------------------------- trace steps
def lift_corners(job, spec, fits, guides) -> dict:
    """RT.trace_creases on this spec's own surface and cameras: corners (d, y) per view and their mean."""
    model = RV.Model.from_spec(job, spec, "hand-traced")
    cr = RT.trace_creases(job, {k: guides[k] for k in ("05", "01")}, model, fits)
    return {c: {"d": v["d"], "y": v["y"], "by_view": v["by_view"], "spread_mm": v.get("spread_mm")}
            for c, v in cr["corners"].items()}


def front_bound_r_c(job, params, base, cam04, theta04, r_hi) -> dict:
    """The largest r_C (R_C_STEP grid, <= r_hi) whose hinge zone stays clear of the 04 silhouette at this camera."""
    def fb(rc):
        sp = hand_spec(base, **{**params, "r_C": rc})
        m = RV.Model.from_spec(job, sp, "probe")
        r = RV.render_model(job, m, "04", cam04, theta04, points=True)
        return RV.front_bound(job, r, cam04)
    grid = [round(i * R_C_STEP, 6) for i in range(int(math.floor(r_hi / R_C_STEP + 1e-9)) + 1)]
    tried = {}

    def clear(i):
        if i not in tried:
            f = fb(grid[i])
            tried[i] = {"r_C": grid[i], "clear": f["clear"], "hinge_zone_max_mm": f["hinge_zone_max_mm"]}
        return tried[i]["clear"]
    if clear(len(grid) - 1):
        return {"r_C": grid[-1], "capped": False, "tried": list(tried.values())}
    lo, hi = 0, len(grid) - 1                       # bisection on the grid: largest clear index
    if not clear(lo):
        return {"r_C": 0.0, "capped": True, "tried": list(tried.values())}
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if clear(mid):
            lo = mid
        else:
            hi = mid
    return {"r_C": grid[lo], "capped": True, "tried": sorted(tried.values(), key=lambda t: t["r_C"])}


def t0_probe(job, params, base, cam04, theta04) -> list:
    """The front bound's cap on r_C at the hand trace's own 04 camera for each T0 in T0_PROBE (everything else as
    traced): T0 and r_C trade against each other through the 04 hinge zone."""
    out = []
    for t0 in T0_PROBE:
        fb = front_bound_r_c(job, {**params, "T0": t0}, base, cam04, theta04, R_C_SEARCH_MAX)
        out.append({"T0_mm": t0, "r_C_cap_mm": fb["r_C"], "capped": fb["capped"]})
    return out


STRIP_03_METHOD = ("the 03 top-face strip: the bright L* plateau between the dark inner face and the next facet (its "
                   "outer edge a 4-6 L* drop), per d bin and temple, in the real photo at the DELIVERED model's 03 "
                   "refit camera. Rows map to d through the delivered model's top face (|n_y| > 0.8) in that row, which "
                   "also bounds the search window (-10 / +12 px); per row the inner edge is the strongest rise of L* "
                   "(blurred 0.7 px) in the window's inner 60 % and the outer edge the first pixel 2.5 L* below the "
                   "plateau (median of the 7 px after inner edge + 3). Widths in px and in mm at the hinge's px/mm "
                   "(image x ~ X for a near-horizontal face; +-5 % perspective). Whether the strip is the top face "
                   "alone (= the thickness at the top) or also takes in F2's up-tilted top is an interpretation the "
                   "photo does not settle")


def strip_03(job, fits_d) -> dict:
    """STRIP_03_METHOD."""
    import cv2
    f = fits_d["03"]
    r = RV.render_model(job, RV.Model.delivered(job), "03", f["camera"], f["theta"], points=True, normals=True)
    L = cv2.GaussianBlur(RC.lightness(job.photo("03").astype(np.uint8)), (0, 0), 0.7)
    out = {"method": STRIP_03_METHOD, "sides": {}}
    for side in (1, -1):
        ppm = job.hinge_ppm(f["camera"], side)
        top = (r["tag"] == side) & (np.abs(r["normal"][..., 1]) > 0.8)
        rows = []
        for y in np.unique(np.nonzero(top)[0]):
            xs = np.nonzero(top[y])[0]
            a, b = xs.min() - 10, xs.max() + 12
            prof = L[y, a:b].astype(float)
            if side < 0:
                prof = prof[::-1]
            g = np.gradient(prof)
            n = len(prof)
            xr = int(np.argmax(g[: int(0.6 * n)]))
            if g[xr] < 0.8:
                continue
            p_ = float(np.median(prof[xr + 3: xr + 10]))
            xf = next((x for x in range(xr + 5, n) if prof[x] < p_ - 2.5), None)
            if xf is None:
                continue
            rows.append((float(np.median(r["d"][y][top[y]])), float(xf - xr), p_,
                         float(p_ - np.median(prof[xf + 1: xf + 5]))))
        bins = []
        for lo in range(0, 33, 3):
            sel = [q for q in rows if lo <= q[0] < lo + 3]
            if len(sel) < 3:
                continue
            w = np.array([q[1] for q in sel])
            bins.append({"d_range_mm": [lo, lo + 3], "rows": len(sel), "width_px_median": float(np.median(w)),
                         "width_px_p25_p75": [float(np.percentile(w, 25)), float(np.percentile(w, 75))],
                         "width_mm": float(np.median(w) / ppm),
                         "plateau_L": float(np.median([q[2] for q in sel])),
                         "outer_edge_drop_L": float(np.median([q[3] for q in sel]))})
        out["sides"]["+x" if side > 0 else "-x"] = {"px_per_mm_at_hinge": float(ppm), "bins": bins}
    return out


def r2_conditional(base, params) -> dict:
    """R2 on the hand trace rests on A-B only (stage GATES' R2 by_crease): the A-B (F2 / base), A-C and B-C dihedrals
    of this spec as r_C sweeps (everything else as traced), and the r_C below which A-B falls under R2's length-weighted
    median (R2_MEDIAN_DN_DEG) and under the confirmed-crease floor (R2_CONFIRMED_DN_DEG). Geometric dihedrals: the side
    views' segments read the A-B dihedral itself (dn 7.745 deg = F2 / base at r_C 3.40 in the 2026-10-06 run)."""
    rows = []
    for rc in np.round(np.arange(0.2, R_C_SEARCH_MAX + 1e-9, 0.1), 3):
        try:
            tb = build_temple(hand_spec(base, **{**params, "r_C": float(rc)}))
        except Exception as e:                                    # noqa: BLE001 - a refused r_C is recorded
            rows.append({"r_C": float(rc), "refused": str(e)[:120]})
            continue
        crs = {tuple(c["regions"]): c["dihedral_deg"][0] for c in tb.tables["creases"]}
        row = {"r_C": float(rc)}
        for cn, (n1, n2) in CREASE_NODES.items():
            key = RF.crease_regions(tb, n1, n2)
            row[cn] = None if key is None else crs.get(tuple(key))
        rows.append(row)
    ok = [r for r in rows if r.get("AB") is not None]

    def crossing(level):
        for a, b in zip(ok, ok[1:]):
            if (a["AB"] - level) * (b["AB"] - level) <= 0 and a["AB"] != b["AB"]:
                return float(a["r_C"] + (level - a["AB"]) * (b["r_C"] - a["r_C"]) / (b["AB"] - a["AB"]))
        return None
    return {"sweep": rows, "r_C_traced": params["r_C"],
            "r_C_min_for_R2_median_mm": crossing(RR.R2_MEDIAN_DN_DEG),
            "r_C_min_for_confirmed_floor_mm": crossing(RR.R2_CONFIRMED_DN_DEG),
            "levels_deg": {"R2_MEDIAN_DN_DEG": RR.R2_MEDIAN_DN_DEG, "R2_CONFIRMED_DN_DEG": RR.R2_CONFIRMED_DN_DEG}}


# --------------------------------------------------------------------------------------------- Blender (P3)
def p3_build(pf, name: str, spec: dict) -> dict:
    out = pf.out / "p3" / name
    out.mkdir(parents=True, exist_ok=True)
    blend = P.scene_copy(out, f"prada_copy_{name}.blend")
    glb = out / f"prada_{name}_TOOL_TEST.glb"
    st = P.blender_steps(out, blend, [{"op": "faceted_temple", "spec": spec}, {"op": "check_relief", "photo_creases": 4},
                                      {"op": "export", "path": str(glb)},
                                      {"op": "save_copy", "path": str(out / f"prada_{name}_TOOL_TEST.blend")}], name)
    ft = st[0]
    res = {"tool": {k: ft.get(k) for k in ("status", "code", "message", "warnings", "budget", "geometry", "facets",
                                           "creases", "thickness")},
           "sides": ft.get("sides"), "check_relief": st[1], "export": {k: st[2].get(k) for k in ("status", "error")}}
    if st[2].get("status") == "exported":
        res["validate_spec"] = {"ok": validate_spec(glb)["ok"]}
        from glasses3d.tests.proofs.wp6_p0_instrument import grade_glb
        g = grade_glb(glb, hinge_z=spec["hinge"]["z_mm"], zone_end=spec["zone_end_d"], outline=spec["outline"],
                      facet_polys={l: [spec["nodes"][n][:2] for n in (f["poly"] if isinstance(f, dict) else f)]
                                   for l, f in spec["facets"].items()})
        res["instrument"] = {"pass": g["pass"], "failed": {s: {k: v for k, v in t["checks"].items() if not v["pass"]}
                                                           for s, t in g["temples"].items()}}
    res["glb"] = str(glb)
    res["spec_sha256"] = PS._spec_sha(spec)
    res["source_sha256_unchanged"] = P.sha(P.PRADA_SCENE) == pf.rec["source_scene"]["sha256_before"]
    warn = [w.get("code") for w in (ft.get("warnings") or [])]
    res["pass"] = (ft.get("status") == "ok" and res.get("validate_spec", {}).get("ok", False)
                   and res["source_sha256_unchanged"])
    res["hinge_warnings"] = [w for w in warn if w in ("W_HINGE_LEAF", "W_KNUCKLE_PENETRATION")]
    res["hinge_detail"] = {s: (v or {}).get("hinge") for s, v in (ft.get("sides") or {}).items()}
    res["decals"] = {s: (v or {}).get("decals") for s, v in (ft.get("sides") or {}).items()}
    return res


def hinge_probe(pf, spec) -> dict:
    """faceted_temple on a scene copy, dry run: the hinge report (leaf gap, knuckle contact) before any camera work."""
    out = pf.out / "p3" / "hinge_probe"
    out.mkdir(parents=True, exist_ok=True)
    blend = P.scene_copy(out, "prada_copy_probe.blend")
    st = P.blender_steps(out, blend, [{"op": "faceted_temple", "spec": spec, "dry_run": True}], "probe")
    ft = st[0]
    return {"status": ft.get("status"), "code": ft.get("code"), "message": ft.get("message"),
            "warnings": [w for w in (ft.get("warnings") or []) if w.get("code") in ("W_HINGE_LEAF",
                                                                                  "W_KNUCKLE_PENETRATION")],
            "hinge": {s: (v or {}).get("hinge") for s, v in (ft.get("sides") or {}).items()}}


# --------------------------------------------------------------------------------------------- shading and creases
def _sh9(n):
    x, y, z = n[:, 0], n[:, 1], n[:, 2]
    return np.stack([np.ones_like(x), x, y, z, x * y, y * z, x * z, x * x - y * y, 3 * z * z - 1], 1)


def _robust(A, y):
    w = np.ones(len(y))
    for _ in range(4):
        coef, *_ = np.linalg.lstsq(A * w[:, None], y * w, rcond=None)
        res = y - A @ coef
        s = 1.4826 * np.median(np.abs(res)) + 1e-6
        w = np.where(np.abs(res) <= 1.5 * s, 1.0, 1.5 * s / np.abs(res))
    return coef, res


def predicted_lightness(job, key, r, cam) -> dict:
    """Two predictions of the photo's L* from a model's normals: 'photo_lit' = an order-2 SH lighting fitted (robust)
    on the FRONT part of the photo only (the same delivered front for every model, so it is independent of the
    temple), and 'key_fill' = the comparison sheets' fixed overhead key + fill Lambert (wp6_view_sheets), as L*."""
    L = RC.lightness(job.photo(key))
    fg = job.matte(key)
    Rr, Ru, Rt = raster.camera_basis(cam)
    n = r["normal"].copy()
    n[(n @ Rt) < 0] *= -1
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    hit = r["tag"] != -2
    front = ndimage.binary_erosion(r["tag"] == 0, iterations=4) & fg & (L < RR.MASK_HIGHLIGHT_L) & (L > 40)
    cf, res = _robust(_sh9(n[front]), L[front])
    pl = np.full(L.shape, np.nan)
    pl[hit] = _sh9(n[hit]) @ cf
    k = VS.KEY / np.linalg.norm(VS.KEY)
    f = VS.FILL / np.linalg.norm(VS.FILL)
    lk = k[0] * Rr + k[1] * Ru + k[2] * Rt
    lf = f[0] * Rr + f[1] * Ru + f[2] * Rt
    s = np.clip(0.42 + 0.62 * np.clip(n @ lk, 0, 1) + 0.10 * np.clip(n @ lf, 0, 1), 0, 1.05)
    kf = np.where(hit, 100.0 * s / 1.05, np.nan)
    return {"L": L, "photo_lit": pl, "photo_lit_front_rms_L": float(np.sqrt(np.mean(res ** 2))), "key_fill": kf,
            "n": n}


def line_step(img, valid, p0, p1, off=RR.STEP_OFFSETS_PX, trim=0.15):
    """median(img) 3..7 px on the +normal side of p0->p1 minus the -normal side (relief_check's step convention)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ln = float(np.linalg.norm(p1 - p0))
    t = (p1 - p0) / ln
    nv = np.array([-t[1], t[0]])
    P_ = p0[None] + np.linspace(trim, 1 - trim, max(8, int(ln)))[:, None] * (p1 - p0)[None]

    def side(sg):
        vals = []
        for o in range(off[0], off[1] + 1):
            qi = np.round(P_ + sg * o * nv).astype(int)
            qi[:, 0] = np.clip(qi[:, 0], 0, img.shape[1] - 1)
            qi[:, 1] = np.clip(qi[:, 1], 0, img.shape[0] - 1)
            v = img[qi[:, 1], qi[:, 0]]
            ok = valid[qi[:, 1], qi[:, 0]] & np.isfinite(v)
            vals.append(v[ok])
        v = np.concatenate(vals)
        return float(np.median(v)) if len(v) > 5 else float("nan")
    return side(+1) - side(-1)


def line_dn(n, valid, p0, p1, off=RR.DN_SAMPLE_PX, trim=0.15):
    """Model normal change (deg) across the line p0->p1 (relief_check's dn: mean normals 3..7 px either side)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ln = float(np.linalg.norm(p1 - p0))
    t = (p1 - p0) / ln
    nv = np.array([-t[1], t[0]])
    P_ = p0[None] + np.linspace(trim, 1 - trim, max(8, int(ln)))[:, None] * (p1 - p0)[None]
    m = []
    for sg in (1, -1):
        acc = []
        for o in range(off[0], off[1] + 1):
            qi = np.round(P_ + sg * o * nv).astype(int)
            qi[:, 0] = np.clip(qi[:, 0], 0, n.shape[1] - 1)
            qi[:, 1] = np.clip(qi[:, 1], 0, n.shape[0] - 1)
            ok = valid[qi[:, 1], qi[:, 0]]
            acc.append(n[qi[ok, 1], qi[ok, 0]])
        a = np.concatenate(acc)
        if len(a) < 5:
            return float("nan")
        m.append(a.mean(0))
    return float(np.degrees(np.arccos(np.clip(m[0] @ m[1] / (np.linalg.norm(m[0]) * np.linalg.norm(m[1])), -1, 1))))


def shading_stage(pf, job, models, fits, glines) -> dict:
    """At every model's own refit camera, per photo crease line in 05 / 01 / 02: the photo's L* step; the model's
    normal change across that photo line; the distance of the model's own crease to it (mm); and the step the model
    produces across ITS OWN crease line under the photo-fitted lighting and under the sheets' fixed key + fill."""
    out = {}
    for key in ("05", "01", "02"):
        near = NEAR[key]
        out[key] = {}
        for lab, m in models.items():
            f = fits[lab][key]
            r = RV.render_model(job, m, key, f["camera"], f["theta"], points=True, normals=True)
            dec = RC.decal_mask(job, key, f["camera"], f["theta"])
            pl = predicted_lightness(job, key, r, f["camera"])
            okp = job.matte(key) & ~ndimage.binary_dilation(dec, iterations=RR.MASK_DECAL_PX)
            okm = (r["tag"] == near) & okp
            rows = {}
            for cn, (p0, p1) in glines.get(key, {}).items():
                row = {"photo_step_L": line_step(pl["L"], okp, p0, p1),
                       "model_dn_across_photo_line_deg": line_dn(pl["n"], okm, p0, p1),
                       "slab_like_step_photo_line": {nm: line_step(pl[nm], okm, p0, p1)
                                                     for nm in ("photo_lit", "key_fill")}}
                if m.build is not None and m.build.spec.facets:
                    q = RF.model_crease_px(job, m.build, f, near, *CREASE_NODES[cn])
                    if q is not None:
                        if np.dot(q[-1] - q[0], np.subtract(p1, p0)) < 0:
                            q = q[::-1]
                        row["model_crease_to_photo_line_mm"] = RF._line_dist_mm(job, f["camera"], q, (p0, p1))
                        row["model_step_own_crease"] = {nm: line_step(pl[nm], okm, q[0], q[-1])
                                                        for nm in ("photo_lit", "key_fill")}
                rows[cn] = row
            out[key][lab] = {"lines": rows, "photo_lit_front_rms_L": pl["photo_lit_front_rms_L"]}
            pf.hb.log(f"  shading {key} {NAMES[lab]}: " + ", ".join(
                f"{cn} photo {v['photo_step_L']:+.1f} / dn {v['model_dn_across_photo_line_deg']:.1f} deg / "
                f"key-fill {v.get('model_step_own_crease', v['slab_like_step_photo_line'])['key_fill']:+.1f} / "
                f"photo-lit {v.get('model_step_own_crease', v['slab_like_step_photo_line'])['photo_lit']:+.1f}"
                for cn, v in rows.items()))
    return out


# --------------------------------------------------------------------------------------------- the sheet
def _put(img, text, org, scale=0.55, color=(0, 0, 0), thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def comparison_sheet(pf, job, models, fits, glines, shading, path: Path, width: int = 600) -> Path:
    """photo | hand-traced | slab control | delivered: 05 zoomed on the +X temple, 01 on the -X temple, 02 angled
    on the +X temple. Each model at its own refit camera, Lambert-shaded with the sheets' fixed key + fill (temples
    tinted), brown = model normal change >= 6 deg (relief_rules), green = the photo's crease lines (05 / 01 / 02
    guides snapped to the L* ridge), drawn on every tile."""
    order = [H_LAB, S_LAB, D_LAB]
    blocks = []
    for key in ("05", "01", "02"):
        near = NEAR[key]
        rend = {lab: VS.shaded(job, models[lab], key, fits[lab][key]["camera"], fits[lab][key]["theta"])
                for lab in order}
        tm = np.zeros(job.shape(key), bool)
        for _, _, tag in rend.values():
            tm |= tag == near
        ys, xs = np.nonzero(tm)
        # the facet zone and the first part of the arm (the zoom the task asks for)
        dmask = np.zeros_like(tm)
        for lab in order:
            f = fits[lab][key]
            r = RV.render_model(job, models[lab], key, f["camera"], f["theta"], points=True)
            dmask |= (r["tag"] == near) & (r["d"] < 90)
        ys, xs = np.nonzero(dmask if dmask.any() else tm)
        x0, x1 = max(0, xs.min() - 30), min(tm.shape[1], xs.max() + 30)
        y0, y1 = max(0, ys.min() - 30), min(tm.shape[0], ys.max() + 30)
        sc = width / (x1 - x0)
        h = int(round(sc * (y1 - y0)))

        def tile(img):
            im = img.copy()
            for cn, (p0, p1) in glines.get(key, {}).items():
                cv2.line(im, tuple(int(round(v)) for v in p0), tuple(int(round(v)) for v in p1), (0, 170, 0), 1,
                         cv2.LINE_AA)
            return cv2.resize(np.ascontiguousarray(im[y0:y1, x0:x1]), (width, h),
                              interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
        tiles = [tile(job.photo(key).astype(np.uint8))] + [tile(rend[lab][0]) for lab in order]
        _put(tiles[0], f"photo {key} (real) - {'+' if near > 0 else '-'}x temple", (8, 22), 0.6)
        for i, lab in enumerate(order):
            th = fits[lab][key]["theta"]
            _put(tiles[i + 1], f"{NAMES[lab]} - shaded, {key}", (8, 22), 0.6)
            _put(tiles[i + 1], f"own refit camera, theta +x {th.get(1, 0.0):.2f} -x {th.get(-1, 0.0):.2f}",
                 (8, h - 10), 0.45, (90, 90, 90))
        # step table under the row: photo step vs model normal change and key-fill step, per crease
        foot = [np.full((54, width, 3), 255, np.uint8) for _ in range(4)]
        sh = shading[key]
        ln = "photo L* step: " + "  ".join(f"{cn} {v['photo_step_L']:+.1f}" for cn, v in sh[H_LAB]["lines"].items())
        _put(foot[0], ln, (8, 20), 0.5)
        _put(foot[0], "green = photo creases", (8, 44), 0.5, (0, 140, 0))
        for i, lab in enumerate(order):
            rows = sh[lab]["lines"]
            a = "dn " + "  ".join(f"{cn} {v['model_dn_across_photo_line_deg']:.1f}" for cn, v in rows.items())
            b = "key-fill step " + "  ".join(
                f"{cn} {v.get('model_step_own_crease', v['slab_like_step_photo_line'])['key_fill']:+.1f}"
                for cn, v in rows.items())
            _put(foot[i + 1], a + " deg", (8, 20), 0.5)
            _put(foot[i + 1], b, (8, 44), 0.5)
        sep = np.full((h, 4, 3), 255, np.uint8)
        row = np.hstack([t for tl in tiles for t in (tl, sep)][:-1])
        fsep = np.full((54, 4, 3), 255, np.uint8)
        frow = np.hstack([t for tl in foot for t in (tl, fsep)][:-1])
        blocks.append(np.vstack([row, frow, np.full((10, row.shape[1], 3), 255, np.uint8)]))
    W = max(b.shape[1] for b in blocks)
    body = np.vstack([np.pad(b, ((0, 0), (0, W - b.shape[1]), (0, 0)), constant_values=255) for b in blocks])
    head = np.full((36, W, 3), 255, np.uint8)
    for i, lab in enumerate(["photo (real)"] + [NAMES[l] for l in order]):
        _put(head, lab, (10 + i * (width + 4), 26), 0.65)
    banner = np.zeros((40, W, 3), np.uint8)
    banner[:] = (180, 0, 0)
    _put(banner, "WP6 Prada HAND-TRACED tool test, scene copy - tool test (not a delivered model)", (10, 27), 0.7,
         (255, 255, 255), 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack([banner, head, body])).save(path)
    return path


# --------------------------------------------------------------------------------------------- main
class HT(P.Proof):
    def __init__(self, out: Path, args):
        self.out = out
        self.args = args
        out.mkdir(parents=True, exist_ok=True)
        self.hb = P.Heartbeat()
        self.pool = None
        self.rec = {"label": LABEL, "started_utc": P.utc(),
                    "rules": {"path": str(P.RULES), "sha256": P.sha(P.RULES), "version": RR.RULES_VERSION},
                    "source_scene": {"path": str(P.PRADA_SCENE), "sha256_before": P.sha(P.PRADA_SCENE)},
                    "ecoqos_opt_out": P.opt_out_ecoqos(), "hand": dict(HAND), "hand_notes": HAND_NOTES,
                    "stages": {}}
        self.save()

    def save(self):
        (self.out / "handtraced.json").write_text(json.dumps(self.rec, indent=1, default=P._json), encoding="utf-8")


def camera_fits(pf, job, spec, label):
    t0 = time.time()
    m = RV.Model.from_spec(job, spec, label)
    fits = RF.camera_block(job, m, RV.VIEW_ORDER, True, log=pf.hb.log, pool=pf.pool)
    pf.hb.log(f"  camera block for {label}: {time.time() - t0:.0f} s, rear family {fits['06']['family']['family']}")
    return fits


def fit_record(spec, fits):
    """The structure stage_gates reads from a fit result (final camera block on this exact spec)."""
    return {"spec": spec, "fits": fits, "final_camera_block": True, "x": {}, "sigma": {}, "sigma_status": {},
            "unobserved": [], "fit_version": "hand-traced (no fit: every parameter traced by hand)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=RF.pool_workers())
    ap.add_argument("--out-dir", default=str(OUT))
    a = ap.parse_args(argv)
    if a.workers < 1:
        ap.error("--workers must be positive")
    pf = HT(Path(a.out_dir), a)
    pf.hb.log(LABEL)
    diag = json.loads((DIAG / "diagnostic.json").read_text(encoding="utf-8"))
    if diag["rules"]["sha256"] != pf.rec["rules"]["sha256"]:
        raise SystemExit("relief_rules.py differs from the diagnostic's record")
    pf.rec["proof_record"] = {"s1_case_b_mm": (diag.get("proof_record") or {}).get("s1_case_b_mm"),
                              "proof_stop": (diag.get("proof_record") or {}).get("stop", {}).get("code")}
    job = RV.Job("prada")
    cand = pickle.loads((DIAG / "fit_candidate.pkl").read_bytes())
    base = cand["spec"]                                         # outline + arm (the fit already gets those right)
    cc = PS._compare_cache(cand, "candidate")
    fits_d = pickle.loads((G3D / "data" / "wp6" / "proof" / "fits_delivered.pkl").read_bytes())
    p2 = diag["stages"]["P2"]
    guides = json.loads(P.GUIDES.read_text(encoding="utf-8"))["guides"]
    glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in p2["guide_lines"].items()}
    try:
        if a.workers > 1:
            pf.pool = RF.FitPool(a.workers, log=pf.hb.log)
            up = pf.pool.warm(job)
            pf.rec["pool"] = {"workers": a.workers, "started": len(up),
                              "ecoqos_opt_out": sum(bool(u["ecoqos_opt_out"]) for u in up)}
            pf.hb.log(f"process pool: {len(up)} of {a.workers} up, {pf.rec['pool']['ecoqos_opt_out']} opted out of "
                      "EcoQoS")
        state = {}

        def trace():
            params = {k: HAND[k] for k in ("x_in0", "x_in_z", "T0", "T_z", "zone_end", "r_H", "d_E")}
            ze = params["zone_end"]
            if params["x_in_z"] is None:
                params["x_in_z"] = float(np.interp(ze, *np.asarray(base["inner"], float).T))
            if params["T_z"] is None:
                params["T_z"] = float(np.interp(ze, *np.asarray(base["thickness"], float).T))
            pf.rec["hand_resolved"] = {"x_in_z": params["x_in_z"], "T_z": params["T_z"]}
            tr = p2["traced"]
            params.update(A=None, B=tuple(tr["B"]), C=tuple(tr["C"]), r_C=None)
            hist = []
            fits = {k: {"camera": cc[k]["camera"], "theta": cc[k]["theta"]} for k in RV.VIEW_ORDER}
            src = "the diagnostic candidate's refit cameras"
            d0 = base["outline"]["top"][0][0]
            prev = None                          # (params, spec) the last camera block was fitted to
            for it in range(4):
                if params["r_C"] is None:        # the corners are lifted through the spec itself: start at the cap
                    params["r_C"] = front_bound_r_c(job, params, base, fits["04"]["camera"], fits["04"]["theta"],
                                                    R_C_SEARCH_MAX)["r_C"]
                spec0 = hand_spec(base, **params)
                corners = lift_corners(job, spec0, fits, guides)
                newp = {**params, "A": (corners["A"]["d"], corners["A"]["y"]),
                        "B": (corners["B"]["d"], corners["B"]["y"]), "C": (corners["C"]["d"], corners["C"]["y"])}
                move = max(float(np.hypot(*np.subtract(newp[c], params[c] if params[c] is not None
                                                       else (d0, _yt(base, d0))))) for c in ("A", "B", "C"))
                fbc = front_bound_r_c(job, newp, base, fits["04"]["camera"], fits["04"]["theta"], R_C_SEARCH_MAX)
                newp["r_C"] = fbc["r_C"]
                spec = hand_spec(base, **newp)
                hist.append({"iteration": it, "cameras": src, "corners": corners, "corner_move_mm": move,
                             "r_C_lifted_through": params["r_C"], "r_C_front_bound_cap": fbc,
                             "abs_C_minus_B_mm": abs_c_minus_b(spec)})
                pf.hb.log(f"  trace {it} at {src}: A {newp['A']}, B {newp['B']}, C {newp['C']} (moved "
                          f"{move:.2f} mm); r_C = front-bound cap {fbc['r_C']:.2f} (search to {R_C_SEARCH_MAX:g}); "
                          f"abs C-B {hist[-1]['abs_C_minus_B_mm']:.2f}")
                if it == 0:
                    hp = hinge_probe(pf, spec)
                    hist[-1]["hinge_probe"] = hp
                    pf.hb.log(f"  hinge probe: {hp['status']} {[w['message'] for w in hp['warnings']]}")
                if prev is not None and move < LIFT_STABLE_MM and newp["r_C"] == prev[0]["r_C"]:
                    hist[-1]["stable"] = (f"corners moved {move:.2f} mm < {LIFT_STABLE_MM} and r_C unchanged at the "
                                          "own cameras: the spec the last camera block was fitted to is kept")
                    break
                params = newp
                fits = camera_fits(pf, job, spec, f"hand-traced trace {it}")
                src = f"the hand-traced spec's own refit cameras (trace {it})"
                prev = (dict(params), spec)
                state["fits_H"] = fits
            params, spec = prev
            state["spec_H"] = spec
            state["params"] = params
            (pf.out / "spec_handtraced.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
            stable = any("stable" in h for h in hist)
            if not stable:
                pf.hb.log("  trace NOT stable after 4 lifts: the last spec a camera block was fitted to is kept")
            return {"params": params, "history": hist, "spec_sha256": PS._spec_sha(spec), "stable": stable,
                    "r_C": {"value_mm": params["r_C"], "status": HAND_NOTES["r_C"]}}

        tr = pf.stage("TRACE", trace)
        if tr.get("error"):
            return finish(pf, "TRACE crashed")
        spec_H = state["spec_H"]
        (pf.out / "fits_handtraced.pkl").write_bytes(pickle.dumps({"H": state["fits_H"]}))
        # the R3 / R4 comparator: the FITTED slab (the diagnostic_resume slab fit with its final camera block)
        slab = pickle.loads(SLAB_FIT.read_bytes())
        pf.rec["fitted_slab"] = {"path": str(SLAB_FIT), "sha256": P.sha(SLAB_FIT), "fit_version": slab.get("fit_version"),
                                 "final_camera_block": slab.get("final_camera_block"), "x": slab.get("x"),
                                 "spec_sha256": PS._spec_sha(slab["spec"]),
                                 "why": "WP6_DESIGN §0.5 / §7 / §8: relief is graded against the FITTED slab (the same "
                                        "fit with every r = 0); the diagnostic_resume fit is the one whose candidate "
                                        "gave the hand trace its outline and arm. The 2026-10-06 run used slab_of(the "
                                        "hand spec), never fitted, under this label"}

        def t0_evidence():
            return {"strip_03": strip_03(job, fits_d),
                    "t0_probe_front_bound_cap": t0_probe(job, state["params"], base, state["fits_H"]["04"]["camera"],
                                                         state["fits_H"]["04"]["theta"]),
                    "T0_used_mm": state["params"]["T0"], "note": HAND_NOTES["T0"]}
        t0e = pf.stage("T0_EVIDENCE", t0_evidence)
        if not t0e.get("error"):
            pf.hb.log("  03 strip (+x): " + ", ".join(f"d {b['d_range_mm'][0]}-{b['d_range_mm'][1]} "
                                                       f"{b['width_px_median']:.0f} px = {b['width_mm']:.2f} mm"
                                                       for b in t0e["strip_03"]["sides"]["+x"]["bins"]))
            pf.hb.log("  front-bound cap on r_C vs T0: " + ", ".join(f"T0 {q['T0_mm']:g} -> {q['r_C_cap_mm']:.2f}"
                                                                    for q in t0e["t0_probe_front_bound_cap"]))
        r2c = pf.stage("R2_CONDITIONAL", lambda: r2_conditional(base, state["params"]))
        if not r2c.get("error"):
            pf.hb.log(f"  R2 conditional: A-B dihedral reaches R2's {RR.R2_MEDIAN_DN_DEG:g} deg median at r_C "
                      f"{r2c['r_C_min_for_R2_median_mm']} mm (traced {r2c['r_C_traced']:.2f})")

        p3H = pf.stage("P3_HAND", lambda: p3_build(pf, "handtraced", spec_H))
        fo = {"_results": {"candidate": fit_record(spec_H, state["fits_H"]), "fitted slab": slab}}
        gt = pf.stage("GATES", lambda: PS.stage_gates(pf, job, fits_d, fo, p2, guides, p3H))
        if gt.get("error"):
            return finish(pf, "GATES crashed")
        PS.label_sheet(pf.out / "views" / "six_view_sheet.png", "HAND-TRACED TOOL TEST - WP6 re-diagnosis "
                       "(fix round 3, graded against the fitted slab); Prada scene copy; tool test (not a delivered "
                       "model)")
        models = {H_LAB: RV.Model.from_spec(job, spec_H, H_LAB), S_LAB: RV.Model.from_spec(job, slab["spec"], S_LAB),
                  D_LAB: RV.Model.delivered(job)}
        fits = {H_LAB: {k: state["fits_H"][k] for k in RV.VIEW_ORDER},
                S_LAB: {k: slab["fits"][k] for k in RV.VIEW_ORDER},
                D_LAB: {k: fits_d[k] for k in RV.VIEW_ORDER}}
        shd = pf.stage("SHADING", lambda: {"views": shading_stage(pf, job, models, fits, glines)})
        if not shd.get("error"):
            pf.stage("SHEET", lambda: {"path": str(comparison_sheet(pf, job, models, fits, glines, shd["views"],
                                                                     pf.out / "sheet_handtraced_TOOL_TEST.png"))})
        tb = build_temple(spec_H)
        pf.rec["geometry"] = {"facets": [{k: f[k] for k in ("label", "area_mm2", "lean_deg", "normal_local")}
                                         for f in tb.tables["facets"]],
                              "creases": [{k: c[k] for k in ("regions", "length_mm", "dihedral_deg", "shape")}
                                          for c in tb.tables["creases"]],
                              "thickness": tb.tables["thickness"], "numbers": report.geometry_numbers(tb, photo_creases=4),
                              "abs_C_minus_B_mm": abs_c_minus_b(spec_H)}
        pf.rec["gates_failed"] = gt.get("failed")
        g = (gt.get("gates") or {})
        pf.rec["summary"] = {
            "r_C": {"value_mm": state["params"]["r_C"], "status": HAND_NOTES["r_C"],
                    "R2_holds_only_for_r_C_ge_mm": (r2c or {}).get("r_C_min_for_R2_median_mm")},
            "R2_confirmed_by_crease": ((g.get("R2") or {}).get("by_crease") or {}).get("confirmed_by_crease"),
            "R2_creases_seen": {k: v.get("creases_seen") for k, v in
                                (((g.get("R2") or {}).get("by_crease") or {}).get("views") or {}).items()},
            "R4_vs_fitted_slab": (g.get("R4") or {}).get("contours"),
            "G1": {"hinge_offset_mm": (g.get("G1") or {}).get("hinge_offset_mm"),
                   "reading": "the 03-vs-04 level and thickness conflict (STOP-C), not a frame defect alone: the "
                              "hinge offset moves with the zone thickness, and at this camera T0 and r_C trade "
                              "through the 04 front bound (T0_EVIDENCE)"}}
        return finish(pf, f"hand-traced tool test done: gates failed {gt.get('failed')}")
    finally:
        if pf.pool is not None:
            pf.pool.close()


def finish(pf, msg) -> int:
    PS.record_gate_decisions(pf.rec)
    pf.rec["result"] = msg
    pf.rec["finished_utc"] = P.utc()
    pf.rec["source_scene"]["sha256_after"] = P.sha(P.PRADA_SCENE)
    pf.rec["exit_code"] = int(any((s or {}).get("error") for s in pf.rec["stages"].values())
                              or pf.rec["source_scene"]["sha256_after"] != pf.rec["source_scene"]["sha256_before"])
    pf.save()
    pf.hb.log(f"result: {msg}")
    pf.hb.stop()
    return pf.rec["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
