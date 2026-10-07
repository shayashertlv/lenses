"""WP6 STOP-I re-diagnosis (2026-10-07, fit-4): why the S1 round trip could not recover the per-photo hinge angle
theta, how fit-4 measures it, and the instrument check that goes with it. Used by tests/proofs/wp6_prada.py's S1
stage; every number goes into the proof record.

Diagnosis (theta_diagnosis): theta is not identifiable apart from the camera that is fitted with it.
  - Side views 05 / 01 (yaw ~+-90, pitch ~13.5): opening a temple by theta about its vertical hinge axis moves it along
    the line of sight; its only image is a tilt of sin(pitch) x theta (0.23 theta), which a camera roll reproduces. The
    fit-3 S1 traded them (05: theta -2.57 deg against roll -0.61 deg; 0.23 x 2.57 = 0.59). WP6_DESIGN §6 never asked
    for it there (theta is scored on the 02 / 03 / 06 / 04 arm rows): fit-4 keeps theta 0 in the side views and lets
    the camera alone register them (relief_views.SIDE_THETA_WHY), so R2 (05 / 01) reads no hinge angle.
  - Angled view 02 (yaw ~31): theta trades with the camera yaw (fit-3 S1: yaw +0.82 against theta -1.21) while the
    temple's placement, which R3 reads, stays right. The value of theta relative to a fitted camera is therefore no
    recovery measure; apparent_theta measures it in a COMMON frame: the hinge angle at which the TRUE camera
    reproduces the fitted camera + theta's placement of the true temple's facet zone (d < 50, where R3's creases lie),
    with the placement that no hinge angle explains reported beside it.
Instrument floor (instrument_floor): R2 and R3 of the TRUE temple and of a zero-relief slab at the S1-fitted cameras
on the synthetic photos; a perfect model failing R2 or R3's 0.7 mm, or a slab passing R2, means the instruments
cannot judge relief: STOP-I (frozen thresholds only).
"""
from __future__ import annotations

import numpy as np
from scipy import optimize

from glasses3d.g3d_host import cameras as C
from glasses3d.g3d_host import relief_check as RC
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_views as RV

ZONE_D = RR.HINGE_ZONE_D[1]          # the facet zone the creases live in (d < 50)


def _zone(sj, model, side: int) -> np.ndarray:
    W = model.temples[side][0]       # world mm at opening 0
    return W[(sj.hinge["z_mm"] - W[:, 2]) < ZONE_D]


def _proj(sj, cam, side, theta, W):
    return C.project(sj.frame.to_norm(RV.rot_about_hinge(W, side, theta, sj.hinge["axis_xz_mm"])), cam)


def apparent_theta(sj, truth, fit: dict, key: str, side: int) -> dict:
    """The hinge angle at which the TRUE camera of `key` best reproduces (least squares, image mm at the hinge zone's
    px/mm) the placement of the TRUE temple's facet zone under the fitted camera and theta; its error against the true
    theta, the placement no hinge angle explains, and the facet zone's image motion per degree of theta."""
    ct, cf = sj.true_cams[key], fit["camera"]
    tt, tf = float(sj.truth_theta[key].get(side, 0.0)), float(fit["theta"].get(side, 0.0))
    W = _zone(sj, truth, side)
    ppm = sj.hinge_ppm(ct, side)
    Uf = _proj(sj, cf, side, tf, W)

    def resid(th):
        return float(np.sqrt(np.mean(np.sum((_proj(sj, ct, side, th, W) - Uf) ** 2, axis=1)))) / ppm
    r = optimize.minimize_scalar(resid, bounds=(tt - 8.0, tt + 8.0), method="bounded", options={"xatol": 1e-4})
    per_deg = float(np.sqrt(np.mean(np.sum((_proj(sj, ct, side, tt + 1.0, W) - _proj(sj, ct, side, tt, W)) ** 2,
                                           axis=1)))) / ppm
    return {"theta_true": tt, "theta_fit": tf, "raw_error_deg": tf - tt, "apparent_deg": float(r.x),
            "error_deg": float(r.x) - tt, "placement_left_mm": float(r.fun), "placement_error_mm": resid(tt),
            "zone_mm_per_deg": per_deg}


def theta_diagnosis(sj, truth, fits: dict, pairs) -> dict:
    """Per (view, side): apparent_theta plus the camera's yaw / roll errors (what theta trades with)."""
    out = {}
    for k, s in pairs:
        if k not in fits:
            continue
        a = apparent_theta(sj, truth, fits[k], k, s)
        a.update({"yaw_error_deg": fits[k]["camera"].yaw - sj.true_cams[k].yaw,
                  "roll_error_deg": fits[k]["camera"].roll - sj.true_cams[k].roll,
                  "theta_fitted_in_view": bool((fits[k].get("theta_fit") or {}).get(str(s), {}).get("observed",
                                                                                                    True))})
        out[f"{k}{'+' if s > 0 else '-'}x"] = a
    return out


def _r2(sj, model, fits: dict) -> dict:
    segs, reg = {}, {}
    for key, near in (("05", 1), ("01", -1)):
        f = RV.crease_fit(fits, key)
        r = RV.render_model(sj, model, key, f["camera"], f["theta"], points=True, normals=True)
        reg[key] = RC.temple_registration_mm(sj, key, r, f["camera"], near)
        segs[key] = RC.photo_crease_segments(sj, key, r, f["camera"], f["theta"], near,
                                             decal_mask=RC.decal_mask(sj, key, f["camera"]),
                                             registration_mm=reg[key]["registration_mm"])
    both = segs["05"]["usable"] and segs["01"]["usable"]
    conf = RC.confirm_creases(segs["05"]["segments"], segs["01"]["segments"]) if both else []
    v = RC.r2_verdict(segs, conf, segs["05"]["segments"], required=("05", "01"))
    return {"pass": bool(v["pass"]), "judged": v.get("judged"), "views": v["views"],
            "confirmed": [{k: c[k] for k in ("model_crease_mm", "dn_deg", "pass")} for c in v["confirmed"]],
            "registration_mm": {k: reg[k]["registration_mm"] for k in reg}}


def instrument_floor(sj, truth, slab, fits: dict, glines: dict) -> dict:
    """R2 and R3 of the TRUE temple and of a zero-relief slab at the S1-fitted cameras on the synthetic photos. The
    instruments can judge relief when the truth passes R2 and transfers <= R3_TRANSFER_MAX_MM while the slab fails R2;
    `stop_i` lists what does not hold (frozen thresholds only)."""
    out = {"frame": "the S1-fitted cameras and theta (theta 0 in the side views)", "models": {}}
    for lab, m in (("truth", truth), ("slab", slab)):
        ctx = RF.Context(sj, {k: fits[k] for k in ("05", "02") if k in fits}, {}, (), glines)
        tr = RF.transfer_all(ctx, m) if ("05" in glines and "02" in glines) else None
        out["models"][lab] = {"R2": _r2(sj, m, fits), "R3_transfer_mm": None if tr is None else tr["mean_mm"],
                              "R3_per_crease": None if tr is None else
                              {c: (None if v is None else v["mean_abs_mm"]) for c, v in tr["per_crease"].items()}}
    t, sl = out["models"]["truth"], out["models"]["slab"]
    stop = []
    if not t["R2"]["pass"]:
        stop.append("the TRUE temple fails R2 at the S1-fitted cameras")
    if t["R3_transfer_mm"] is None or not t["R3_transfer_mm"] <= RR.R3_TRANSFER_MAX_MM:
        stop.append(f"the TRUE temple transfers {t['R3_transfer_mm']} mm > R3_TRANSFER_MAX_MM")
    if sl["R2"]["pass"]:
        stop.append("a zero-relief slab passes R2 at the S1-fitted cameras")
    out["stop_i"] = stop
    out["pass"] = not stop
    return out
