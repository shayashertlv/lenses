"""WP6 measured relief cue for the joint fit (fit-4): what the photos show about the facets, beyond silhouettes.

The silhouettes do not see Prada's relief (WP6_DESIGN §0.4: relief 0 -> 5.3 mm changes 25 px in 06); the WP6
diagnostic fit therefore returned a near-slab (r_C 2.22, A-B crease 1.4-4.1 deg) that R2 graded SHALLOW / FLAG. (The
hand-traced tool test's r_C 3.40 is NOT a target: it is the front bound's cap, chosen because a withdrawn 5.3 mm C - B
estimate lay above it; its R2 pass rests on A-B pieces only and holds only for r_C >= ~2.65 mm; no photo measures
the depth, WP6 fix round 3.) Two measured cues carry what the photos do show into the fit:

    photo_steps(job, key, lines)          the photo's L* step across each snapped crease line (relief_check's step:
                                          median L* 3-7 px either side, blurred like the Canny input; decals, the
                                          matte's outside and highlights masked) and its standard error; once per job
    model_steps(job, model, key, cam, near, lines, theta)
                                          the step the MODEL makes across the same photo lines at the view's camera and
                                          theta (0 in a side view), under the matched light (CUE_LIGHT: the one camera-
                                          fixed key light that reproduces the real Prada side views' crease pattern,
                                          relief_fit.SYNTH_LIGHT), from the near temple's face normals (front occlusion
                                          respected)
    step_misfit(photo, model)             per view the gain G >= 0 that best scales the model steps onto the photo
                                          steps (closed form: exposure and albedo are unknown) and the misfit in units
                                          of STEP_MIN_L (relief_rules: the smallest step that is a crease). The gain
                                          makes the cue blind to the relief's SCALE (Lambert steps are G x relief) and
                                          sensitive to what the photos fix: which creases exist, their signs and
                                          ratios, and where they lie (a photo line that misses the model crease reads
                                          a step of 0 on the model)
    triangulate(job, l05, l02, f05, f02, nodes, hinge_z, endpoint_px)
                                          the B-C crease as a 3D line: every 05 ray through the 05 B-C line cut by the
                                          plane of the 02 B-C line, both carried into the temple's own frame (opening
                                          0: each view's theta undone about the hinge axis), so the points are in the
                                          temple's chart (d, y, X)); X at B and
                                          at C (the line extended to C's traced (d, y)); X_C - X_B is the relief's
                                          SCALE in absolute mm, the cue the steps cannot give. Only B-C: A-B runs
                                          nearly along the epipolar direction in both 05 and 02, so its cut is
                                          ill-conditioned (WP6 2026-10-07: 0.5 deg of 02 roll moved X_B by 2.3 mm
                                          through A-B, 0.26 mm through B-C; the WP6 measurement had flagged A as 1.43
                                          mm of X per px). sigma: every line endpoint perturbed along its normal by
                                          its own uncertainty (endpoint_px: at least TRI_ENDPOINT_PX; the proof passes
                                          the tracer's own move from the guide click to the snapped line, so a line
                                          the snapper and the click disagree on carries that disagreement)
    topology_evidence(job, lines, fits)   the evidence behind Astra's F1 reading: the step from the band above A-B to
                                          F2 (the A-B crease) and the steps inside the band (across a probe line up
                                          from B; across A-B extended behind B); "F1 = base" when no band step reaches
                                          STEP_MIN_L in either side view (relief_rules: below it a step is no crease)

The objective adds NOISE_LOSS x ((misfit / STEP_MIN_L)^2 summed) and NOISE_LOSS x ((X_C - X_B)_model - triangulated)^2
/ sigma^2: in the objective's own noise unit, so one sigma of either measurement costs what one noise unit of the
silhouettes costs (relief_fit.NOISE_LOSS), and the sigma report reads the measurement's own sigma back when it
dominates. The front bound stays the hard cap (§2: no view is dropped or weighted down; a disagreement is STOP-C).
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import raster
from . import relief_rules as RR

CUE_LIGHT = (0.0, math.sin(math.radians(40.0)), math.cos(math.radians(40.0)))   # = relief_fit.SYNTH_LIGHT (tested)
STEP_TRIM = 0.15             # the outer 15 % of a crease line is skipped (other creases meet there; relief_trace)
STEP_MIN_SIDE = 5            # samples a side needs for a step
TRI_ENDPOINT_PX = 1.0        # triangulation sigma: each line endpoint perturbed by this along its normal
CREASE_CORNERS = {"B": ("BC",), "C": ("BC",)}
TRI_CREASES = ("BC",)


# --------------------------------------------------------------------------------------------- sampling
def line_samples(p0, p1, offsets=RR.STEP_OFFSETS_PX, trim: float = STEP_TRIM):
    """Positions along p0 -> p1 (every px, the outer `trim` skipped), its unit normal (-t_y, t_x) (relief_check's +
    side) and the sample points (side, offset, position, 2) at +-offsets[0..1] px."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ln = float(np.linalg.norm(p1 - p0))
    t = (p1 - p0) / max(ln, 1e-9)
    nv = np.array([-t[1], t[0]])
    s = np.linspace(trim, 1.0 - trim, max(8, int(ln * (1.0 - 2 * trim))))
    P = p0[None] + s[:, None] * (p1 - p0)[None]
    offs = np.arange(offsets[0], offsets[1] + 1, dtype=float)
    S = np.stack([P[None] + sg * offs[:, None, None] * nv[None, None] for sg in (+1.0, -1.0)])
    return P, nv, S


def _pix(S, shape):
    q = np.round(S).astype(int)
    q[..., 0] = np.clip(q[..., 0], 0, shape[1] - 1)
    q[..., 1] = np.clip(q[..., 1], 0, shape[0] - 1)
    return q


# --------------------------------------------------------------------------------------------- photo
def blurred_lightness(job, key) -> np.ndarray:
    from .relief_check import lightness
    cache = job.__dict__.setdefault("_cue_L", {})
    if key not in cache:
        cache[key] = cv2.GaussianBlur(lightness(job.photo(key)), (0, 0), RR.CANNY_SIGMA_PX)
    return cache[key]


def photo_steps(job, key: str, lines: dict, decal_mask: np.ndarray | None = None) -> dict:
    """{crease: {"step_L", "sigma_L", "n", "p0", "p1"}} on the photo (None for a line with too few valid samples)."""
    from scipy import ndimage
    L = blurred_lightness(job, key)
    ok = job.matte(key) & (L <= RR.MASK_HIGHLIGHT_L)
    if decal_mask is not None and decal_mask.any():
        ok &= ~ndimage.binary_dilation(decal_mask, iterations=RR.MASK_DECAL_PX)
    out = {}
    for cn, (p0, p1) in lines.items():
        P, nv, S = line_samples(p0, p1)
        q = _pix(S, L.shape)
        v = L[q[..., 1], q[..., 0]]
        valid = ok[q[..., 1], q[..., 0]]
        a, b = v[0][valid[0]], v[1][valid[1]]
        if len(a) < STEP_MIN_SIDE or len(b) < STEP_MIN_SIDE:
            out[cn] = None
            continue
        step = float(np.median(a) - np.median(b))
        # per position: the side medians over the offsets, then their difference; its robust spread / sqrt(n)
        per = []
        for i in range(P.shape[0]):
            aa, bb = v[0, :, i][valid[0, :, i]], v[1, :, i][valid[1, :, i]]
            if len(aa) and len(bb):
                per.append(float(np.median(aa) - np.median(bb)))
        per = np.asarray(per)
        sig = float(1.4826 * np.median(np.abs(per - np.median(per))) / math.sqrt(len(per))) if len(per) > 2 else None
        out[cn] = {"step_L": step, "sigma_L": sig, "n": int(len(per)), "p0": list(map(float, p0)),
                   "p1": list(map(float, p1))}
    return out


# --------------------------------------------------------------------------------------------- model
def _near_scene(job, model, near: int, theta: float = 0.0):
    V, F = model.temple_mesh(near, theta, job.hinge["axis_xz_mm"])
    return raster.get_scene(V, F, job.frame), V, F


def model_steps(job, model, key: str, cam, near: int, lines: dict, light=CUE_LIGHT, theta: float = 0.0) -> dict:
    """{crease: step} in Lambert shade units (n . l, clipped at 0) of the model's near temple (opened by `theta`)
    through `cam` across each photo line; 0.0 where a side has fewer than STEP_MIN_SIDE samples on the near temple
    (the model shows no surface there to crease). Hits hidden behind the front are dropped."""
    sc, V, F = _near_scene(job, model, near, theta)
    fr = job.front_scene()
    Rr, Ru, Rt = raster.camera_basis(cam)
    lw = np.asarray(light, float)
    lw = lw / np.linalg.norm(lw)
    lw = lw[0] * Rr + lw[1] * Ru + lw[2] * Rt
    out = {}
    for cn, (p0, p1) in lines.items():
        P, nv, S = line_samples(p0, p1)
        u, v = S[..., 0].ravel(), S[..., 1].ravel()
        h = sc.cast(cam, u, v, want_normal=True)
        hf = fr.cast(cam, u, v)
        vis = h["hit"] & ~(hf["hit"] & (hf["depth"] < h["depth"]))
        n = h["normal"].astype(np.float64)
        n[(n @ Rt) < 0] *= -1
        shade = np.clip(n @ lw, 0.0, 1.0).reshape(S.shape[:3])
        vis = vis.reshape(S.shape[:3])
        a, b = shade[0][vis[0]], shade[1][vis[1]]
        out[cn] = float(np.median(a) - np.median(b)) if len(a) >= STEP_MIN_SIDE and len(b) >= STEP_MIN_SIDE else 0.0
    return out


def step_misfit(photo: dict, model: dict) -> dict:
    """The per-view gain and misfit: photo = {view: photo_steps(...)}, model = {view: model_steps(...)}. Per view, over
    the creases both measured: G = max(0, sum(s_p s_m) / sum(s_m^2)) (0 when the model has no step at all), misfit =
    sum((s_p - G s_m)^2) / STEP_MIN_L^2 (dimensionless; a slab scores sum((s_p / STEP_MIN_L)^2))."""
    out = {"views": {}, "chi2": 0.0}
    for k, ph in photo.items():
        md = model.get(k) or {}
        cs = [c for c, p in ph.items() if p is not None and c in md]
        if not cs:
            continue
        sp = np.array([ph[c]["step_L"] for c in cs])
        sm = np.array([md[c] for c in cs])
        den = float(sm @ sm)
        g = max(0.0, float(sp @ sm) / den) if den > 0 else 0.0
        res = sp - g * sm
        chi = float(res @ res) / RR.STEP_MIN_L ** 2
        out["views"][k] = {"gain": g, "creases": cs, "photo_L": sp.tolist(), "model_shade": sm.tolist(),
                           "residual_L": res.tolist(), "chi2": chi}
        out["chi2"] += chi
    return out


# --------------------------------------------------------------------------------------------- triangulation
def _ray_mm(job, cam, u, v):
    """(origins, unit directions) in model mm of the camera rays through pixels (u, v)."""
    z0 = max(1.0, 1.05 * float(job.front_scene().max_radius) + 0.01)
    o, d = raster.camera_rays(cam, np.atleast_1d(np.asarray(u, float)), np.atleast_1d(np.asarray(v, float)), z0)
    O = job.frame.to_mm(o)
    D = job.frame.to_mm(o + d) - O
    D /= np.linalg.norm(D, axis=1, keepdims=True)
    return O, D


def _unopen(job, P, theta: float):
    """World points of the +X temple opened by theta -> the same points at opening 0 (the temple's own frame)."""
    from .relief_views import rot_about_hinge
    return rot_about_hinge(np.asarray(P, float), 1, -float(theta), job.hinge["axis_xz_mm"]) if theta else P


def _line_plane(job, cam, p0, p1, theta: float = 0.0):
    """(point, unit normal) of the plane of the camera rays through the image line p0 -> p1 (model mm), carried into
    the +X temple's opening-0 frame when that temple was photographed opened by theta."""
    O, D = _ray_mm(job, cam, [p0[0], p1[0]], [p0[1], p1[1]])
    pts = _unopen(job, np.vstack([O, O + 100.0 * D]), theta)
    c = pts.mean(0)
    n = np.linalg.svd(pts - c)[2][-1]
    return c, n


def _cut(job, cam05, l05, plane, n_pts: int = 25, theta05: float = 0.0):
    """Points of the 05 rays through the 05 line (t 0.15 .. 0.85) where they cross `plane` (model mm, the temple's
    opening-0 frame; the rays carried there when 05 has a theta)."""
    p0, p1 = np.asarray(l05[0], float), np.asarray(l05[1], float)
    t = np.linspace(STEP_TRIM, 1.0 - STEP_TRIM, n_pts)[:, None]
    uv = p0[None] * (1 - t) + p1[None] * t
    O, D = _ray_mm(job, cam05, uv[:, 0], uv[:, 1])
    if theta05:
        O2 = _unopen(job, O + D, theta05)
        O = _unopen(job, O, theta05)
        D = O2 - O
        D /= np.linalg.norm(D, axis=1, keepdims=True)
    c, n = plane
    den = D @ n
    s = ((c - O) @ n) / np.where(np.abs(den) < 1e-12, np.nan, den)
    return O + s[:, None] * D


def _chart(P_mm, hinge_z):
    """+X temple at opening 0: world mm -> chart (d, y, X)."""
    return np.stack([hinge_z - P_mm[:, 2], P_mm[:, 1], P_mm[:, 0]], axis=1)


def _x_at(Q, dy):
    """X of the 3D line through chart points Q (least squares) at the point of the line whose (d, y) is nearest dy."""
    Q = Q[np.all(np.isfinite(Q), axis=1)]
    if len(Q) < 3:
        return None
    c = Q.mean(0)
    u = np.linalg.svd(Q - c)[2][0]
    uv = u[:2]
    nn = float(uv @ uv)
    if nn < 1e-12:
        return None
    s = float((np.asarray(dy, float) - c[:2]) @ uv / nn)
    return float(c[2] + s * u[2])


def _tri_once(job, l05, l02, cam05, cam02, nodes, hinge_z, th05: float = 0.0, th02: float = 0.0):
    xs = {}
    lines3 = {}
    for cn in TRI_CREASES:
        if cn not in l05 or cn not in l02:
            continue
        Q = _cut(job, cam05, l05[cn], _line_plane(job, cam02, *l02[cn], theta=th02), theta05=th05)
        lines3[cn] = _chart(Q, hinge_z)
    for corner, cns in CREASE_CORNERS.items():
        vals = [_x_at(lines3[cn], nodes[corner][:2]) for cn in cns if cn in lines3]
        vals = [v for v in vals if v is not None]
        if vals:
            xs[corner] = float(np.mean(vals))
    return xs, lines3


def triangulate(job, l05: dict, l02: dict, fit05: dict, fit02: dict, nodes: dict, hinge_z: float,
                endpoint_px: dict | None = None) -> dict | None:
    """{"X": {"B", "C"}, "offset_CB_mm", "sigma_mm", "lines"} from the photo crease lines of 05 and 02 at the given
    fits ({"camera", "theta"}: the +X temple's theta in each view is undone, so the points are in its opening-0
    frame); `nodes` = the spec's (d, y) node positions (traced); `endpoint_px` = {(view, crease, end): px} endpoint
    uncertainties (default TRI_ENDPOINT_PX, never below it). None when B or C cannot be triangulated."""
    th05 = float((fit05.get("theta") or {}).get(1, 0.0))
    th02 = float((fit02.get("theta") or {}).get(1, 0.0))
    xs, lines3 = _tri_once(job, l05, l02, fit05["camera"], fit02["camera"], nodes, hinge_z, th05, th02)
    if "B" not in xs or "C" not in xs:
        return None
    off = xs["C"] - xs["B"]
    var = 0.0
    used = {}
    for which, lines in (("05", l05), ("02", l02)):
        for cn in TRI_CREASES:
            if cn not in lines or cn not in l05 or cn not in l02:
                continue
            p0, p1 = np.asarray(lines[cn][0], float), np.asarray(lines[cn][1], float)
            t = (p1 - p0) / np.linalg.norm(p1 - p0)
            for end in (0, 1):
                px = max(TRI_ENDPOINT_PX, float((endpoint_px or {}).get((which, cn, end), 0.0)))
                used[f"{which} {cn} end {end}"] = px
                nv = np.array([-t[1], t[0]]) * px
                q = [p0.copy(), p1.copy()]
                q[end] = q[end] + nv
                pl = dict(l05) if which == "05" else dict(l02)
                pl[cn] = (q[0].tolist(), q[1].tolist())
                xs2, _ = _tri_once(job, pl if which == "05" else l05, pl if which == "02" else l02,
                                   fit05["camera"], fit02["camera"], nodes, hinge_z, th05, th02)
                if "B" in xs2 and "C" in xs2:
                    var += ((xs2["C"] - xs2["B"]) - off) ** 2
    return {"X": xs, "offset_CB_mm": float(off), "sigma_mm": float(math.sqrt(var)) if var > 0 else None,
            "lines": {cn: Q[np.all(np.isfinite(Q), axis=1)].tolist() for cn, Q in lines3.items()},
            "endpoint_px": used}


def endpoint_sigmas(guides: dict, snapped: dict, views=("05", "02"), signed: bool = False) -> dict:
    """{(view, crease, end): px}: how far the tracer moved each line endpoint from the guide click to the snapped line
    (measured along the snapped line's normal) - the uncertainty a crease line carries when the photo's gradient and the
    click disagree (Prada 02 B-C: 7 px, the edge fading toward C). `signed`: the move itself (snapped minus click, along
    the snapped line's normal (-t_y, t_x), t = its p0 -> p1 direction), which the proof's S1 copies onto its synthetic
    lines (tests/proofs/wp6_prada.s1_guide_plan)."""
    out = {}
    for k in views:
        for cn, sn in (snapped.get(k) or {}).items():
            gd = (guides.get(k) or {}).get(cn)
            if gd is None or sn is None:
                continue
            a, b = np.asarray(sn[0], float), np.asarray(sn[1], float)
            t = (b - a) / np.linalg.norm(b - a)
            nv = np.array([-t[1], t[0]])
            g0, g1 = np.asarray(gd[0], float), np.asarray(gd[1], float)
            for end, p in ((0, a), (1, b)):
                # the guide line's point nearest to this snapped endpoint, then the offset along the normal
                u = (g1 - g0) / np.linalg.norm(g1 - g0)
                q = g0 + float((p - g0) @ u) * u
                m = float((p - q) @ nv)
                out[(k, cn, end)] = m if signed else abs(m)
    return out


def model_offset_cb(tb) -> float:
    """X_C - X_B of a built spec (base level plus the nodes' relief; C may lie outside the outline)."""
    ps = tb.spec
    c, b = ps.nodes["C"], ps.nodes["B"]
    rc = c[2] if c[2] is not None else tb.sol.node_r.get("C", 0.0)
    rb = b[2] if b[2] is not None else tb.sol.node_r.get("B", 0.0)
    return float(ps.x_base(c[0]) + rc - ps.x_base(b[0]) - rb)


# --------------------------------------------------------------------------------------------- topology
def topology_evidence(job, lines: dict, corners_px: dict, decal_masks: dict | None = None) -> dict:
    """Astra's F1 reading measured on the side views: per view (05, 01), with that view's snapped A-B line and corner B
    (px): "AB_step" (band vs F2: the A-B crease), "band_behind_B" (across a probe line from B up to the top edge,
    perpendicular to A-B: the band ahead of B vs behind it) and "AB_extended" (across A-B extended behind B by half its
    length). "F1 = base" when both band steps stay below STEP_MIN_L in every side view measured."""
    out = {"views": {}, "rule": f"F1 = base when no band step reaches STEP_MIN_L ({RR.STEP_MIN_L} L*) in a side view"}
    verdict = []
    for key in ("05", "01"):
        lk = lines.get(key) or {}
        B = (corners_px.get(key) or {}).get("B")
        if "AB" not in lk or B is None:
            continue
        p0, p1 = np.asarray(lk["AB"][0], float), np.asarray(lk["AB"][1], float)
        B = np.asarray(B, float)
        t = (p1 - p0) / np.linalg.norm(p1 - p0)
        A_side = p0 if np.linalg.norm(p0 - B) > np.linalg.norm(p1 - B) else p1
        back = -(A_side - B) / np.linalg.norm(A_side - B)             # unit vector from B away from A
        up = np.array([-t[1], t[0]])
        fg = job.matte(key)
        # the photo's top edge above B along `up` (the matte's last foreground pixel), minus the silhouette band
        sgn = 1.0 if (B + 20 * up)[1] < B[1] else -1.0                 # image y grows downward: "up" = smaller y
        up = up * sgn
        h = 0
        for s in range(1, 400):
            q = np.round(B + s * up).astype(int)
            if not (0 <= q[1] < fg.shape[0] and 0 <= q[0] < fg.shape[1]) or not fg[q[1], q[0]]:
                break
            h = s
        h = max(0, h - RR.MASK_SILHOUETTE_PX)
        dm = (decal_masks or {}).get(key)
        ab = photo_steps(job, key, {"AB": (p0.tolist(), p1.tolist())}, dm)["AB"]
        probe = {"band_behind_B": (B + 2.0 * up, B + h * up) if h > 12 else None,
                 "AB_extended": (B + 3.0 * back, B + (3.0 + 0.5 * float(np.linalg.norm(A_side - B))) * back)}
        rec = {"AB_step": None if ab is None else ab["step_L"]}
        for nm, ln in probe.items():
            if ln is None:
                rec[nm] = None
                continue
            r = photo_steps(job, key, {nm: (ln[0].tolist(), ln[1].tolist())}, dm)[nm]
            rec[nm] = None if r is None else r["step_L"]
        band = [abs(v) for v in (rec["band_behind_B"], rec["AB_extended"]) if v is not None]
        rec["f1_is_base"] = bool(band) and max(band) < RR.STEP_MIN_L
        out["views"][key] = rec
        verdict.append(rec["f1_is_base"])
    out["f1_is_base"] = bool(verdict) and all(verdict)
    out["topology"] = "pyramid (F1 = base)" if out["f1_is_base"] else "F1 crease + rise"
    return out
