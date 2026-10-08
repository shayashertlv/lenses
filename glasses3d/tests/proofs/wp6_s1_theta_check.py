"""WP6 S1 follow-up (TOOL TEST, $0, writes only data/wp6/s1_theta_check.json): does R3 (05 -> 02 crease transfer) or the
R2 crease lift depend on the hinge angle theta that S1 did not recover?

The S1 truth temple (proof.json S1 `truth_params`, checked against the recorded `truth_spec_sha256`; never rebuilt
from the record's start parameters, which an S1 before fix round 2 had overwritten with its own outline trace) is
graded on its own synthetic photos with the TRUE crease lines of 05 and 02 as the "photo" lines, at
  (a) the true cameras and true theta                 -> the instrument's floor (should be ~0)
  (b) the S1-fitted cameras and fitted theta          -> what the proof would measure on a perfect model
  (c) the S1-fitted cameras with the true theta       -> camera error alone
  (d) the true cameras with the fitted theta          -> theta error alone
R3's gate is transfer <= 0.7 mm and <= delivered - 0.5 / slab - 0.5. These cases are EVIDENCE for the owner's STOP-I
decision (tests/proofs/wp6_prada_s1_dependencies.json: R2 and R3 both read theta); they never decide it.
tests/proofs/wp6_prada.py's S1 stage records the same cases (`transfer_cases`) with every S1 run.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_s1_theta_check
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d.temple import fixtures as FX  # noqa: E402
from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.g3d_host.camera import Camera  # noqa: E402

PROOF = G3D / "data" / "wp6" / "proof"
OUT = G3D / "data" / "wp6" / "s1_theta_check.json"


def transfer_cases(sj, truth, fitted: dict, theta_true: dict) -> dict:
    """Cases (a)-(d) above on a synthetic job `sj` whose photos are renders of `truth`; `fitted` = {view: {"camera",
    "theta"}} of the S1 fit for 05 and 02; `theta_true` = {view: {side: deg}}."""
    true_fit = {k: {"camera": sj.true_cams[k], "theta": theta_true[k]} for k in ("05", "02")}
    lines = {}
    for key, side in (("05", 1), ("02", 1)):
        lines[key] = {}
        for cname, nodes in (("AB", ("A", "B")), ("AC", ("A", "C")), ("BC", ("B", "C"))):
            q = RF.model_crease_px(sj, truth.build, true_fit[key], side, *nodes)
            if q is None:
                continue
            c = q.mean(0)
            d = np.linalg.svd(q - c)[2][0]
            t = (q - c) @ d
            lines[key][cname] = ((c + d * t.min()).tolist(), (c + d * t.max()).tolist())
    th_fit = {k: {int(s): float(v) for s, v in fitted[k]["theta"].items()} for k in ("05", "02")}
    cases = {"a true cameras, true theta": ("true", "true"), "b fitted cameras, fitted theta": ("fit", "fit"),
             "c fitted cameras, true theta": ("fit", "true"), "d true cameras, fitted theta": ("true", "fit")}
    out = {"theta": {k: {"true": theta_true[k], "fitted": th_fit[k]} for k in ("05", "02")}, "cases": {}}
    for name, (cm, th) in cases.items():
        fits = {k: {"camera": (sj.true_cams[k] if cm == "true" else fitted[k]["camera"]),
                    "theta": (theta_true[k] if th == "true" else th_fit[k])} for k in ("05", "02")}
        tr = RF.transfer_all(RF.Context(sj, fits, {}, (), lines), truth)
        out["cases"][name] = {"mean_mm": tr["mean_mm"],
                              "per_crease": {c: (None if v is None else round(v["mean_abs_mm"], 4))
                                             for c, v in tr["per_crease"].items()}}
    return out


def rebuild_s1(rec: dict, *, cameras_path: str | Path | None = None):
    """(job, synthetic job, truth model, S1 record) rebuilt from a proof record: the truth from `truth_params` with the
    record's topology (checked against `truth_spec_sha256`), the true cameras and theta from the S1 record
    (`truth_cameras` / `truth_theta`, s1-4 on: the side views carry a hinge angle) or, for an older record, the
    delivered fits; the perturbation from each view's contour error, as stage_s1 builds it."""
    from glasses3d.tests.proofs.wp6_prada_stages import _spec_sha
    s1 = rec["stages"]["S1"]
    if "truth_params" not in s1:
        raise SystemExit("the recorded S1 predates fix round 2 (no truth_params): re-run S1 first")
    truth_p = dict(s1["truth_params"])
    for k in ("A", "B", "C"):
        if k in truth_p and truth_p[k] is not None:
            truth_p[k] = tuple(truth_p[k])
    builder = FX.prada_pyramid_spec if s1.get("topology") == "pyramid" else FX.prada_spec
    truth_spec = builder(**truth_p)
    if _spec_sha(truth_spec) != s1["truth_spec_sha256"]:
        raise SystemExit("the rebuilt S1 truth differs from the recorded one (truth_spec_sha256)")
    job = RV.Job("prada", cameras_path=cameras_path)
    truth = RV.Model.from_spec(job, truth_spec, "S1 truth")
    if s1.get("truth_cameras") and s1.get("truth_theta"):
        cams = {k: Camera.from_dict(c) for k, c in s1["truth_cameras"].items()}
        theta = {k: {int(sd): float(v) for sd, v in t.items()} for k, t in s1["truth_theta"].items()}
    else:
        fd = rec["stages"]["delivered_fits"]
        cams = {k: Camera.from_dict(fd[k]["camera"]) for k in RV.VIEW_ORDER}
        theta = {k: {int(sd): float(v) for sd, v in fd[k]["theta"].items()} for k in RV.VIEW_ORDER}
    cams_json = json.loads(job.cameras_path.read_text(encoding="utf-8"))["images"]
    contour_px = {k: cams_json[job.views[k][0]]["contour_mean_px"] for k in RV.VIEW_ORDER}
    sj = RF.SyntheticJob.from_job(job, truth, theta, cams, contour_px)
    return job, sj, truth, s1


def main() -> int:
    rec = json.loads((PROOF / "proof.json").read_text(encoding="utf-8"))
    job, sj, truth, s1 = rebuild_s1(rec)
    theta = sj.truth_theta
    fit = pickle.loads((PROOF / "s1_fit.pkl").read_bytes())["fits"]
    res = transfer_cases(sj, truth, {k: fit[k] for k in ("05", "02")}, theta)
    out = {"label": "TOOL TEST (S1 follow-up on synthetic photos of the S1 truth; no scene touched)",
           "s1_synthetic_render": s1.get("synthetic_render"), "s1_fit_version": s1.get("fit_version"),
           "s1_procedure": s1.get("s1_procedure"), "s1_truth_spec_sha256": s1["truth_spec_sha256"],
           "s1_theta_deg_error": s1["recovery"]["theta_deg"], "s1_theta_graded_on": s1.get("theta_graded_on"),
           "matches_s1_record": {n: abs(c["mean_mm"] - s1["theta_cases"]["cases"][n]["mean_mm"]) < 1e-9
                                 for n, c in res["cases"].items()}, **res}
    for name, c in res["cases"].items():
        print(f"{name}: R3 transfer of the TRUE temple {c['mean_mm']:.3f} mm {c['per_crease']}", flush=True)
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
