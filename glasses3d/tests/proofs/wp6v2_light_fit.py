"""The photo-estimated environment (rig photo_env, WP6_FACETS §3.2 amended 2026-10-10): fit, then held-out checks.

For the ORIGINAL Prada scene (sha 56274deb...) and the PREFERRED v1 build (data/wp6/preferred_goal_20261010/
photo_proof_coverage_checked/preferred/p3/prada_tool_test_faceted.blend, sha 45964428...), each a byte copy in its
own worker job with the WP6 v2 pose basis (data/wp6v2/basis):

1. fit: ``Seeing.photo_env()`` fits the camera-relative lobe environment on the front acetate of the calibration
   photos (g3d_host/lighting.py; the temples are never used) and freezes it in the job (weights, CV table, residuals,
   photo shas);
2. held-out renders at every photo (01-06, black-side; long side <= 1024 px; native_linear with the per-channel
   white-balanced gain of 04's front, gloss_black with the black photo's luma gain), never fed back into the fit:
   - whole-image residuals per photo and region (front acetate, temple bodies, all acetate): median signed and
     median absolute L8 (render - photo; no stretch);
   - temple facet brightness order and the upper-crease step (F2 - F1, 3-20 px bands either side of their shared
     edge) at 05 / 01 (native, beige) and black-side (gloss_black), on the facet polygons of the preferred spec
     cfc11127 built by the v1 builder (data/wp6v2/t2/base/build.json, T2), projected through each photo's pose and
     restricted to the visible temple body (eroded 10 px). For the original model the same polygons are image
     regions;
   - a diagnosis per facet: its posed normal, the mirror direction at the camera (elevation, camera-relative
     azimuth) and the fitted environment's radiance there, and the cosine-weighted irradiance of its normal — so a
     disagreement can be traced to the light (what the environment holds in those directions) or the geometry
     (where the facet points);
3. diagnosis of a disagreement (a counterfactual, never frozen or used by any compare): the conditioning of the
   fitted (linearised) calibration system, and the least calibration-residual environment that ALSO orders the
   facets' mirror radiances as the black photo orders the facets (SLSQP on the same linearised system, the same
   smoothness and lambda); its calibration cost, and its renders of the preferred geometry at 05 / 01 / black-side.
   If that environment fits the front acetate about as well and reproduces the photos' facet order on the preferred
   geometry, the disagreement is the light (directions the front acetate does not constrain), not the geometry.
Nothing in steps 1-2 is tuned on the temples.

Writes data/wp6v2/light_fit/{report.json, <model>_<photo>.png (photo | render | dL), env_<model>.png}.
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_light_fit
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import optimize, stats

from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import lighting as L
from glasses3d.g3d_host import pose as PO
from glasses3d.tests.proofs.wp6v2_rig_check import bands, facet_labels

G3D = Path(__file__).resolve().parents[2]
REPO = G3D.parent
DATA = G3D / "data"
OUT = DATA / "wp6v2" / "light_fit"
BASIS_JSON = DATA / "wp6v2" / "basis" / "cameras.json"
MODELS = {
    "original": (REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend",
                 "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"),
    "preferred": (DATA / "wp6/preferred_goal_20261010/photo_proof_coverage_checked/preferred/p3/"
                         "prada_tool_test_faceted.blend",
                  "45964428a9d38910048477cd037a04f6cec0b17531136ab227bed4a354826e90"),
}
SPEC = DATA / "wp6/pose_arm_resume_20261010/distal_all_views/before/spec.json"
SPEC_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
BUILD = DATA / "wp6v2" / "t2" / "base" / "build.json"
PHOTOS = ("reference-01", "reference-02", "reference-03", "reference-04", "reference-05", "reference-06", "black-side")
FACET_PHOTOS = {"reference-05": "native_linear", "reference-01": "native_linear", "black-side": "gloss_black"}
VIEW_MAX_PX = 1024
ERODE_PX = 10
MIN_FACET_PX = 200
ORDER_MARGIN_L8 = 3.0          # counterfactual: facet pairs the black photo orders by more than this


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def open_job(name: str, blend: Path, photos: dict):
    root = OUT / f"job_{name}"
    if root.exists():
        shutil.rmtree(root, onerror=_rw)
    root.mkdir(parents=True)
    seed = root / "seed.blend"
    shutil.copy2(blend, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    C.import_cameras_json(root, BASIS_JSON, photos, reason="photo_env fit + held-out checks")
    s = BlenderSession(root, seed_blend=seed, calls_dir=root / "tools")
    s.start()
    return s, Seeing(s)


def views_for(bs: C.Basis, origin) -> dict:
    out = {}
    for rid in PHOTOS:
        cam0, (H, W) = bs.camera(rid), bs.shape(rid)
        k = min(1.0, VIEW_MAX_PX / max(H, W))
        cam = BC.crop_camera(cam0, 0, 0, k) if k < 1.0 else cam0
        shape = (max(1, round(H * k)), max(1, round(W * k)))
        out[rid] = {"k": k, "cam": cam, "shape": shape, "pose": bs.pose(rid),
                    "view": {"name": rid, "camera": BC.to_blender(cam, bs.frame, origin, shape),
                             "passes": ["native_linear", "gloss_black"], "pose": bs.pose(rid)}}
    return out


def cam_dirs(cam, frame, P_mm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(unit directions from the points to the camera, the camera's horizontal azimuth direction), asset axes."""
    _r, _u, toward, _eu, e_v = BC.axes(cam)
    p = float(cam.perspective)
    Pn = frame.to_norm(P_mm)
    v = (toward / p - Pn) if p > 0 else np.broadcast_to(toward, Pn.shape).copy()
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    h = toward - e_v
    h[1] = 0.0
    return v, h / np.linalg.norm(h)


def elev_cos(d: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    d = d / np.linalg.norm(d, axis=-1, keepdims=True)
    e = np.degrees(np.arcsin(np.clip(d[..., 1], -1, 1)))
    hz = d * np.array([1.0, 0.0, 1.0])
    n = np.linalg.norm(hz, axis=-1)
    c = np.where(n > 1e-9, (hz @ h) / np.maximum(n, 1e-9), 0.0)
    return e, c


def irradiance(weights, normal: np.ndarray, h: np.ndarray, n_grid: int = 64) -> float:
    """Cosine-weighted world radiance over the hemisphere of ``normal`` (/ pi: 1 for a uniform unit world)."""
    th = (np.arange(n_grid) + 0.5) / n_grid * np.pi
    ph = (np.arange(2 * n_grid) + 0.5) / (2 * n_grid) * 2 * np.pi
    T, P = np.meshgrid(th, ph, indexing="ij")
    d = np.stack([np.sin(T) * np.cos(P), np.cos(T), np.sin(T) * np.sin(P)], -1)
    dw = np.sin(T) * (np.pi / n_grid) * (2 * np.pi / (2 * n_grid))
    cos = np.maximum(d @ normal, 0.0)
    e, c = elev_cos(d, h)
    return float((L.radiance(weights, e, c) * cos * dw).sum() / np.pi)


def facet_geometry(build_side: dict, pose: dict, side: int) -> dict:
    """{facet: (centroid_mm, unit outward normal)} of the build's outer polygons, posed (area-weighted)."""
    V = np.asarray(build_side["V"], float)
    p = PO.parse_pose(pose)
    th, axis = p.get(side, (0.0, (0.0, 0.0)))
    W = PO.rot_about_hinge(V, side, th, axis)
    acc = {}
    for poly, kind, region in zip(build_side["polys"], build_side["kind"], build_side["region"]):
        if kind != "outer":
            continue
        P = W[poly]
        n = np.zeros(3)
        for i in range(1, len(P) - 1):
            n += np.cross(P[i] - P[0], P[i + 1] - P[0])
        a = float(np.linalg.norm(n)) / 2
        if a <= 0:
            continue
        lab = region.split("/")[0]
        c, nn, aa = acc.get(lab, (np.zeros(3), np.zeros(3), 0.0))
        acc[lab] = (c + P.mean(0) * a, nn + n, aa + a)
    out = {}
    for lab, (c, n, a) in acc.items():
        n = n / np.linalg.norm(n)
        if n[0] * side < 0:                       # outward = away from the head on that side
            n = -n
        out[lab] = (c / a, n)
    return out


def diff_image(photo_L, render_L, mask) -> Image.Image:
    d = np.where(mask, render_L - photo_L, 0.0)
    t = np.clip(d / 40.0, -1, 1)
    rgb = np.full(d.shape + (3,), 255.0)
    rgb[..., 0] -= np.where(t < 0, -t * 255, 0)
    rgb[..., 1] -= np.abs(t) * 255
    rgb[..., 2] -= np.where(t > 0, t * 255, 0)
    rgb[~mask] = 235
    return Image.fromarray(rgb.astype(np.uint8))


def analyse(name: str, s: BlenderSession, see: Seeing, build: dict, rec: dict | None = None,
            tag: str = "fitted") -> dict:
    """Held-out renders and checks of one job under its fitted environment (or ``rec``, labelled ``tag``)."""
    t0 = time.perf_counter()
    bs = C.load_basis(see.root)
    if rec is None:
        env = see.photo_env()
        assert env["status"] == "ok", env
        rec = json.loads(Path(env["path"]).read_text(encoding="utf-8"))
    exp = see.export_head()
    mesh = see.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
    roles = PO.part_roles(exp["path"])
    origin = np.asarray(see.bounds("lf-bounds")["origin_mm"] or (0.0, 0.0, 0.0), float)
    out_dir = see._prepare_work_dir(see.work / "light_fit")
    gains = see._matched_gains(bs, mesh, roles, "photo_env", origin, "lf-gains", out_dir, C.geometry_key(mesh), rec)
    vs = views_for(bs, origin)
    r = s.call_read("light_fit", "render", {"out_dir": str(out_dir / f"views-{tag}"),
                                            "views": [v["view"] for v in vs.values()], "rig": "photo_env",
                                            "env": L.env_param(rec), "linear_format": "float", "engine": "auto"},
                    deadline_s=900, call_id=f"lf-views-{tag}")
    assert r["status"] == "ok" and r["result"]["unchanged"], r
    files = {v["name"]: v["files"] for v in r["result"]["views"]}
    fid, acetate = Seeing._acetate_ids(mesh, roles)
    g_nat = np.asarray(gains["colourways"]["target"]["native_linear"]["gain"])
    g_glo = float(gains["gloss_black"]["gain"])
    res = {"env": {k: rec.get(k) for k in ("weights", "weights_sha", "lambda", "chosen_iteration", "level", "chroma",
                                           "system")},
           "env_cv": rec.get("cv"), "env_iterations": [{k: it[k] for k in ("iteration", "direct_mean_rel_rms",
                                                                            "direct_residuals", "model_residuals")}
                                                       for it in rec.get("iterations", [])],
           "calibration": rec.get("calibration"), "fit_seconds": rec.get("seconds"), "env_label": tag,
           "gains": {"native_rgb": g_nat.tolist(), "native_region": gains["colourways"]["target"]["region"],
                     "gloss_luma": g_glo, "gloss_from": gains["gloss_black"]["from"]},
           "photos": {}}
    for rid, v in vs.items():
        cw = bs.colourway(rid)
        mp = PO.posed(mesh, v["pose"], roles) if v["pose"] else mesh
        ras = mp.scene().render(v["cam"], v["shape"], 1)
        part = np.full(v["shape"], -1, np.int64)
        part[ras["mask"]] = mp.part[ras["face_id"][ras["mask"]]]
        pc, mc = Seeing.photo_at(bs, rid, (0, 0) + tuple(bs.shape(rid)[::-1]), v["k"], v["shape"])
        bg = C.photo_background(bs.photo(rid), bs.matte(rid))
        nat = C.read_linear(files[rid]["native_linear"]["path"])
        glo = C.read_linear(files[rid]["gloss_black"]["path"])
        ps = "native_linear" if cw == "target" else "gloss_black"
        rgb, a = (nat if ps == "native_linear" else glo)
        render_f = C.composite_render(rgb, a, g_nat if ps == "native_linear" else g_glo, bg, as_float=True)
        Lp, Lr = C.luma8(pc.astype(float)), C.luma8(render_f)
        ok = mc & (a > 0.999)
        bodies = [PO.temple_body(mp, sd, roles) for sd in (1, -1)]
        regions = {"front": C.erode_px((part == fid) & ok, 3), "temples": C.erode_px(np.isin(part, bodies) & ok, 3),
                   "acetate": C.erode_px(np.isin(part, acetate) & ok, 3)}
        prec = {"pass": ps, "k": v["k"], "regions": {}}
        for reg, m in regions.items():
            if m.sum() < 100:
                continue
            d = Lr[m] - Lp[m]
            prec["regions"][reg] = {"px": int(m.sum()), "median_signed_L8": round(float(np.median(d)), 2),
                                    "median_abs_L8": round(float(np.median(np.abs(d))), 2),
                                    "photo_median_L8": round(float(np.median(Lp[m])), 1),
                                    "render_median_L8": round(float(np.median(Lr[m])), 1)}
        img = C.grid([C.labelled(Image.fromarray(pc[..., :3]), f"{rid} photo"),
                      C.labelled(Image.fromarray(np.round(render_f).astype(np.uint8)),
                                 f"{name} photo_env ({tag}) {ps}"),
                      C.labelled(diff_image(Lp, Lr, regions["acetate"]), "render - photo L8 (red +, blue -, +-40)")],
                     3, max_px=3000)
        p = OUT / f"{name}_{tag}_{rid}.png"
        img.convert("RGB").save(p)
        prec["image"] = str(p)
        if rid in FACET_PHOTOS:
            side = 1 if bs.camera(rid).yaw > 0 else -1
            body = PO.temple_body(mp, side, roles)
            labels = facet_labels(build["sides"][str(side)], v["pose"], side, v["cam"], bs.frame, v["shape"],
                                  (part == body) & ok)
            er = {lab: C.erode_px(m, ERODE_PX) for lab, m in labels.items()}
            regs = [lab for lab in sorted(er) if er[lab].sum() >= MIN_FACET_PX]
            ph = [float(np.median(Lp[er[lab]])) for lab in regs]
            rn = [float(np.median(Lr[er[lab]])) for lab in regs]
            rho = stats.spearmanr(ph, rn).statistic if len(regs) >= 3 else None
            fo = {"pass": ps, "regions": regs, "photo_L8": [round(x, 1) for x in ph],
                  "render_L8": [round(x, 1) for x in rn], "facet_px": [int(er[lab].sum()) for lab in regs],
                  "spearman": None if rho is None else round(float(rho), 3)}
            if "F1" in labels and "F2" in labels:
                b1, b2 = bands(C.erode_px(labels["F1"], 1), C.erode_px(labels["F2"], 1))
                if b1.sum() >= 50 and b2.sum() >= 50:
                    fo["upper_crease_step_F2_minus_F1"] = {
                        "photo": round(float(np.median(Lp[b2]) - np.median(Lp[b1])), 2),
                        "render": round(float(np.median(Lr[b2]) - np.median(Lr[b1])), 2),
                        "band_px": [int(b1.sum()), int(b2.sum())]}
            geo = facet_geometry(build["sides"][str(side)], v["pose"], side)
            vdir, h = cam_dirs(v["cam"], bs.frame, np.array([c for c, _n in geo.values()]))
            diag = {}
            for (lab, (c, n)), vv in zip(geo.items(), vdir):
                refl = 2 * (n @ vv) * n - vv
                e_r, c_r = elev_cos(refl[None], h)
                e_n, c_n = elev_cos(n[None], h)
                diag[lab] = {"normal_elev_deg": round(float(e_n[0]), 1), "normal_cos_az": round(float(c_n[0]), 3),
                             "mirror_elev_deg": round(float(e_r[0]), 1), "mirror_cos_az": round(float(c_r[0]), 3),
                             "env_at_mirror": round(float(L.radiance(rec["weights"], e_r, c_r)[0]), 3),
                             "irradiance": round(irradiance(rec["weights"], n, h), 3)}
            fo["diagnosis"] = diag
            prec["facets"] = fo
        res["photos"][rid] = prec
    envimg = L.env_image(rec["weights"], 720, 360)
    im = Image.fromarray(C.linear_to_srgb8(envimg / max(1e-6, float(envimg.max())))).convert("RGB")
    dr = ImageDraw.Draw(im)
    for frac, lab in ((0.5, "toward camera"), (0.25, "side"), (0.75, "side"), (0.0, "back")):
        dr.line([(frac * 720, 0), (frac * 720, 359)], fill=(255, 0, 0))
        dr.text((frac * 720 + 3, 3), lab, fill=(255, 0, 0))
    dr.line([(0, 180), (719, 180)], fill=(0, 160, 255))
    dr.text((3, 183), "horizon", fill=(0, 160, 255))
    for rid in FACET_PHOTOS:
        for lab, dg in (res["photos"][rid].get("facets", {}).get("diagnosis") or {}).items():
            x = 360 + np.degrees(np.arccos(np.clip(dg["mirror_cos_az"], -1, 1))) * 2
            y = (90 - dg["mirror_elev_deg"]) * 2
            dr.ellipse([x - 3, y - 3, x + 3, y + 3], outline=(0, 200, 0))
            dr.text((x + 4, y - 6), f"{rid[-2:]}:{lab}", fill=(0, 140, 0))
    pe = OUT / f"env_{name}_{tag}.png"
    im.save(pe)
    res["env_image"] = str(pe)
    res["seconds"] = round(time.perf_counter() - t0, 1)
    return res


def counterfactual(rec: dict, facets: dict) -> dict:
    """The least calibration-residual environment whose radiances at the facets' mirror directions follow the black
    photo's facet order (pairs apart by > ORDER_MARGIN_L8), on the fitted linearised system (diagnosis only)."""
    z = np.load(rec["system"])
    B, rhs, groups, lam = z["B"], z["rhs"], z["groups"], float(z["lam"])
    Aw, bw = L._weighted(B, rhs, groups)
    D = L.smoothness()
    sc = float((Aw ** 2).sum() / Aw.shape[1])
    sv = np.linalg.svd(Aw, compute_uv=False)

    def cost(w):
        return float(((Aw @ w - bw) ** 2).sum() + lam * sc * ((D @ w) ** 2).sum())

    def grad(w):
        return 2 * Aw.T @ (Aw @ w - bw) + 2 * lam * sc * D.T @ (D @ w)
    w_u = L.nnls_smooth(Aw, bw, lam, D)
    regs, ph, diag = facets["regions"], dict(zip(facets["regions"], facets["photo_L8"])), facets["diagnosis"]
    row = {lab: L.lobe_matrix(np.array([diag[lab]["mirror_elev_deg"]]), np.array([diag[lab]["mirror_cos_az"]]))[0]
           for lab in regs if lab in diag}
    pairs = [(a, b) for a in row for b in row if ph[a] - ph[b] > ORDER_MARGIN_L8]
    G = np.array([row[a] - row[b] for a, b in pairs])
    sol = optimize.minimize(cost, w_u, jac=grad, method="SLSQP", bounds=[(0.0, None)] * L.N_LOBES,
                            constraints=[{"type": "ineq", "fun": lambda w: G @ w, "jac": lambda w: G}],
                            options={"maxiter": 500, "ftol": 1e-12})
    w_c = np.maximum(sol.x, 0.0)
    return {"note": "diagnosis only: never frozen, never used by a compare",
            "singular_values": [float(v) for v in sv], "condition": float(sv[0] / sv[-1]),
            "effective_rank_1e-2": int((sv > 1e-2 * sv[0]).sum()), "pairs": pairs,
            "solver": {"success": bool(sol.success), "message": str(sol.message)},
            "cost_unconstrained": cost(w_u), "cost_constrained": cost(w_c),
            "cost_ratio": cost(w_c) / max(cost(w_u), 1e-30),
            "residuals_unconstrained": L.group_residuals(B, rhs, w_u, groups),
            "residuals_constrained": L.group_residuals(B, rhs, w_c, groups),
            "mirror_radiance_unconstrained": {lab: float(r @ w_u) for lab, r in row.items()},
            "mirror_radiance_constrained": {lab: float(r @ w_c) for lab, r in row.items()},
            "weights": [float(x) for x in w_c], "weights_sha": L.weights_sha(w_c)}


def main() -> dict:
    t0 = time.perf_counter()
    for name, (path, want) in MODELS.items():
        assert sha(path) == want, f"{name}: {path} changed"
    assert sha(SPEC) == SPEC_SHA and json.loads((BUILD.parent / "spec.json").read_text(encoding="utf-8")) == \
        json.loads(SPEC.read_text(encoding="utf-8")), "the T2 build is not of spec cfc11127"
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    assert build["status"] == "ok"
    OUT.mkdir(parents=True, exist_ok=True)
    doc = json.loads(BASIS_JSON.read_text(encoding="utf-8"))
    photos = {n: Path(r["image"]) for n, r in doc["images"].items()}
    report = {"inputs": {n: {"path": str(p), "sha256": w} for n, (p, w) in MODELS.items()},
              "basis": {"path": str(BASIS_JSON), "sha256": sha(BASIS_JSON)},
              "facets_from": {"build": str(BUILD), "build_sha256": sha(BUILD), "spec_sha256": SPEC_SHA},
              "models": {}}
    for name, (path, _w) in MODELS.items():
        s, see = open_job(name, path, photos)
        try:
            report["models"][name] = analyse(name, s, see, build)
            if name == "preferred":
                rec = report["models"][name]
                cf = counterfactual({"system": rec["env"]["system"]}, rec["photos"]["black-side"]["facets"])
                report["counterfactual"] = cf
                report["models"]["preferred_counterfactual"] = analyse(name, s, see, build, cf, "counterfactual")
            assert s.fingerprint() == s.head["fingerprint"]
        finally:
            s.close()
        C.write_json(OUT / "report.json", report)
    w = [np.asarray(report["models"][n]["env"]["weights"]) for n in MODELS]
    rec = json.loads((OUT / "job_original" / "work" / "compare" / "env-basis-01.json").read_text(encoding="utf-8"))
    report["fit_stability"] = {"same_front_two_jobs": "original vs preferred (identical front)",
                               "direct_mean_rel_rms": {n: min(i["direct_mean_rel_rms"] for i in
                                                              report["models"][n]["env_iterations"]) for n in MODELS},
                               "iterates_of_original": [L.weights_sha(i["weights"]) for i in rec["iterations"]]}
    report["env_weight_difference"] = {"max_abs": float(np.abs(w[0] - w[1]).max()),
                                       "relative_l2": float(np.linalg.norm(w[0] - w[1]) / np.linalg.norm(w[0]))}
    report["seconds"] = round(time.perf_counter() - t0, 1)
    C.write_json(OUT / "report.json", report)
    return report


if __name__ == "__main__":
    rep = main()
    for name, m in rep["models"].items():
        print(name, "lambda", m["env"]["lambda"], "iter", m["env"]["chosen_iteration"],
              {it["iteration"]: round(it["direct_mean_rel_rms"], 4) for it in m["env_iterations"]})
        for rid, p in m["photos"].items():
            print(" ", rid, {k: (v["median_signed_L8"], v["median_abs_L8"]) for k, v in p["regions"].items()})
            if "facets" in p:
                f = p["facets"]
                print("    facets", f["regions"], "photo", f["photo_L8"], "render", f["render_L8"], "rho",
                      f["spearman"], "step", f.get("upper_crease_step_F2_minus_F1"))
    cf = rep.get("counterfactual") or {}
    print("counterfactual cost ratio", cf.get("cost_ratio"), "condition", cf.get("condition"), "rank",
          cf.get("effective_rank_1e-2"), {k: (round(v["rel_rms"], 4)) for k, v in
                                          (cf.get("residuals_constrained") or {}).items()})
    print("env weight difference", rep["env_weight_difference"], "seconds", rep["seconds"])
