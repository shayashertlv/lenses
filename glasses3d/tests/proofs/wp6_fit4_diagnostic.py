"""WP6 fit-4 DIAGNOSTIC (2026-10-07; TOOL TEST, $0, NOT a proof result): what the proof (tests/proofs/wp6_prada.py,
fit-4: the measured relief cue, no hinge angle in the side views, the hinge constraint) would run after a stop that only the owner can
lift (STOP-C, the R1 / R3 rule-set decisions): the P3 build of the automatic candidate on a Prada scene COPY and the
gates (against the delivered GLB and the fitted slab), plus a comparison sheet photo | automatic fit-4 candidate |
hand-traced tool test | delivered. It reads the proof's own records (P2, the two fits it pickled, the delivered
cameras) and refits nothing that is on record; every output is labelled DIAGNOSTIC TOOL TEST and lands in
data/wp6/diagnostic_fit4/.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_fit4_diagnostic [--workers N]

Owner machine rule: a process pool of os.cpu_count() - 2 workers, each opted out of EcoQoS; progress at least every
60 s.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d.temple import build_temple, report  # noqa: E402
from glasses3d.g3d_host import relief_cue as CU  # noqa: E402
from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.tests.proofs import wp6_prada as P  # noqa: E402
from glasses3d.tests.proofs import wp6_prada_stages as PS  # noqa: E402
from glasses3d.tests.proofs import wp6_view_sheets as VS  # noqa: E402

LABEL = ("DIAGNOSTIC TOOL TEST (WP6 fit-4 2026-10-07): the proof's automatic fit (measured relief cue, no hinge angle "
         "in the side views, hinge constraint) built and graded past the proof's STOP-C on a Prada scene COPY; NOT a "
         "proof result, not a delivered model")
PROOF = G3D / "data" / "wp6" / "proof"
OUT = G3D / "data" / "wp6" / "diagnostic_fit4"
HAND = G3D / "data" / "wp6" / "handtraced"


class Diag(P.Proof):
    def __init__(self, out: Path, args):
        self.out = out
        self.args = args
        out.mkdir(parents=True, exist_ok=True)
        self.hb = P.Heartbeat()
        self.pool = None
        self.rec = {"label": LABEL, "started_utc": P.utc(),
                    "rules": {"path": str(P.RULES), "sha256": P.sha(P.RULES), "version": RR.RULES_VERSION},
                    "fit_version": RF.FIT_VERSION,
                    "source_scene": {"path": str(P.PRADA_SCENE), "sha256_before": P.sha(P.PRADA_SCENE)},
                    "ecoqos_opt_out": P.opt_out_ecoqos(), "stages": {}, "stop": None}
        self.save()

    def save(self):
        (self.out / "diagnostic.json").write_text(json.dumps(self.rec, indent=1, default=P._json), encoding="utf-8")


def crease_table(job, models: dict, fits: dict, glines: dict) -> dict:
    """Per crease view and model: the photo step on each snapped line, the model's step there under the matched light
    (relief_cue, at the model's own refit camera and theta) and the per-view gain / misfit; its crease dihedrals."""
    from glasses3d.g3d_host.relief_check import decal_mask
    photo = {k: CU.photo_steps(job, k, glines[k], decal_mask(job, k, job.base[k])) for k, _ in RV.CREASE_VIEWS
             if k in glines}
    out = {"photo": {k: {c: (None if v is None else v["step_L"]) for c, v in p.items()} for k, p in photo.items()},
           "models": {}}
    for lab, m in models.items():
        f = fits[lab]
        ms = {k: CU.model_steps(job, m, k, f[k]["camera"], RF.NEAR[k],
                                {c: (p["p0"], p["p1"]) for c, p in photo[k].items() if p is not None},
                                theta=f[k]["theta"].get(RF.NEAR[k], 0.0)) for k in photo}
        mis = CU.step_misfit(photo, ms)
        rec = {"misfit_chi2": mis["chi2"], "views": mis["views"]}
        if m.build is not None and m.build.spec.facets:
            rec["dihedrals"] = {"/".join(c["regions"]): c["dihedral_deg"][0] for c in m.build.tables["creases"]}
            rec["offset_CB_mm"] = CU.model_offset_cb(m.build)
        out["models"][lab] = rec
    return out


def _put(img, text, org, scale=0.55, color=(0, 0, 0), thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def comparison_sheet(job, models: dict, fits: dict, glines: dict, steps: dict, path: Path, width: int = 560) -> Path:
    """photo | each model (shaded, its own refit camera; brown = its creases >= 6 deg; green = the photo's snapped
    crease lines) for 05 (+X temple), 01 (-X) and 02 (+X), with the photo steps and each model's steps under the
    matched light and its dihedrals under the row."""
    labs = list(models)
    blocks = []
    for key in ("05", "01", "02"):
        near = RF.NEAR[key]
        rend = {lab: VS.shaded(job, models[lab], key, fits[lab][key]["camera"], fits[lab][key]["theta"])
                for lab in labs}
        dmask = np.zeros(job.shape(key), bool)
        for lab in labs:
            f = fits[lab][key]
            r = RV.render_model(job, models[lab], key, f["camera"], f["theta"], points=True)
            dmask |= (r["tag"] == near) & (r["d"] < 90)
        ys, xs = np.nonzero(dmask)
        x0, x1 = max(0, xs.min() - 30), min(dmask.shape[1], xs.max() + 30)
        y0, y1 = max(0, ys.min() - 30), min(dmask.shape[0], ys.max() + 30)
        sc = width / (x1 - x0)
        h = int(round(sc * (y1 - y0)))

        def tile(img):
            im = img.copy()
            for cn, (p0, p1) in glines.get(key, {}).items():
                cv2.line(im, tuple(int(round(v)) for v in p0), tuple(int(round(v)) for v in p1), (0, 170, 0), 1,
                         cv2.LINE_AA)
            return cv2.resize(np.ascontiguousarray(im[y0:y1, x0:x1]), (width, h),
                              interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
        tiles = [tile(job.photo(key)[..., :3].astype(np.uint8))] + [tile(rend[lab][0]) for lab in labs]
        _put(tiles[0], f"photo {key} (real) - {'+' if near > 0 else '-'}x temple", (8, 22), 0.6)
        for i, lab in enumerate(labs):
            _put(tiles[i + 1], f"{lab} - shaded, {key}", (8, 22), 0.6)
        foot = [np.full((54, width, 3), 255, np.uint8) for _ in range(len(labs) + 1)]
        ph = steps["photo"].get(key, {})
        _put(foot[0], "photo L* step: " + "  ".join(f"{c} {v:+.1f}" for c, v in ph.items() if v is not None), (8, 20))
        _put(foot[0], "green = photo creases (snapped)", (8, 44), 0.5, (0, 140, 0))
        for i, lab in enumerate(labs):
            mv = (steps["models"][lab]["views"].get(key) or {})
            g = mv.get("gain", 0.0)
            pred = "  ".join(f"{c} {g * s:+.1f}" for c, s in zip(mv.get("creases", []), mv.get("model_shade", [])))
            _put(foot[i + 1], f"model step x gain {g:.0f}: {pred}", (8, 20), 0.48)
            dh = steps["models"][lab].get("dihedrals") or {}
            _put(foot[i + 1], "dihedrals " + " ".join(f"{k} {v:.1f}" for k, v in dh.items())[:70], (8, 44), 0.42)
        sep = np.full((h, 4, 3), 255, np.uint8)
        row = np.hstack([t for tl in tiles for t in (tl, sep)][:-1])
        fsep = np.full((54, 4, 3), 255, np.uint8)
        frow = np.hstack([t for tl in foot for t in (tl, fsep)][:-1])
        blocks.append(np.vstack([row, frow, np.full((10, row.shape[1], 3), 255, np.uint8)]))
    W = max(b.shape[1] for b in blocks)
    body = np.vstack([np.pad(b, ((0, 0), (0, W - b.shape[1]), (0, 0)), constant_values=255) for b in blocks])
    head = np.full((36, W, 3), 255, np.uint8)
    for i, lab in enumerate(["photo (real)"] + labs):
        _put(head, lab, (10 + i * (width + 4), 26), 0.65)
    banner = np.zeros((40, W, 3), np.uint8)
    banner[:] = (180, 0, 0)
    _put(banner, "WP6 Prada fit-4 automatic fit vs hand-traced vs delivered, scene copy - tool test (not a delivered "
         "model)", (10, 27), 0.65, (255, 255, 255), 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack([banner, head, body])).save(path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=RF.pool_workers())
    ap.add_argument("--proof-dir", default=str(PROOF))
    ap.add_argument("--out-dir", default=str(OUT))
    a = ap.parse_args(argv)
    if a.workers < 1:
        ap.error("--workers must be positive")
    proof = Path(a.proof_dir)
    pf = Diag(Path(a.out_dir), a)
    pf.hb.log(LABEL)
    rec = json.loads((proof / "proof.json").read_text(encoding="utf-8"))
    if rec["rules"]["sha256"] != pf.rec["rules"]["sha256"]:
        raise SystemExit("relief_rules.py differs from the proof's record")
    pf.rec["proof_record"] = {"path": str(proof / "proof.json"), "stop": rec.get("stop"), "result": rec.get("result"),
                              "s1_failures": rec.get("s1_failures"), "s1_stop_i": rec.get("s1_stop_i"),
                              "s1_case_b_mm": PS._s1_case_b(rec)}
    pf.rec["stages"]["S1"] = rec["stages"].get("S1")
    job = RV.Job("prada")
    fits_d = pickle.loads((proof / "fits_delivered.pkl").read_bytes())
    gp = json.loads(P.GUIDES.read_text(encoding="utf-8"))
    start = P.start_params(job, gp)
    p2 = rec["stages"].get("P2")
    if not p2 or p2.get("error"):
        raise SystemExit("the proof has no P2 on record")
    topology = gp.get("start", {}).get("topology", "f1")
    try:
        if a.workers > 1:
            pf.pool = RF.FitPool(a.workers, log=pf.hb.log)
            up = pf.pool.warm(job)
            pf.rec["pool"] = {"workers": a.workers, "started": len(up),
                              "ecoqos_opt_out": sum(bool(u["ecoqos_opt_out"]) for u in up)}
        fo = None
        fit_rec = rec["stages"].get("FIT") or {}
        pk = {lab: proof / f"fit_{lab.replace(' ', '_')}.pkl" for lab in ("candidate", "fitted slab")}
        if fit_rec and not fit_rec.get("error") and all(p.exists() for p in pk.values()):
            res = {lab: pickle.loads(p.read_bytes()) for lab, p in pk.items()}
            if all(r.get("fit_version") == RF.FIT_VERSION for r in res.values()):
                fo = dict(fit_rec)
                fo["_results"] = res
                pf.rec["fit_source"] = "the proof's own FIT stage (pickles in the proof folder)"
        if fo is None:
            fo = pf.stage("FIT", lambda: PS.stage_fit(pf, job, start, p2, False, topology, gp["guides"]))
            if fo.get("error"):
                return finish(pf, "FIT crashed")
            pf.rec["fit_source"] = "run here (the proof stopped before its FIT)"
        res = fo["_results"]
        pf.rec["fits"] = {lab: {k: r.get(k) for k in ("x", "loss", "sigma", "sigma_status", "termination",
                                                      "rounds_run", "fit_version")}
                          | {"cue_at_fit": (r.get("details") or {}).get("cue"),
                             "hinge_violation_mm": (r.get("details") or {}).get("hinge_violation_mm"),
                             "front_bound": (r.get("details") or {}).get("front_bound")}
                          for lab, r in res.items()}
        p3 = pf.stage("P3", lambda: PS.stage_p3(pf, res["candidate"]["spec"]))
        gt = pf.stage("GATES", lambda: PS.stage_gates(pf, job, fits_d, fo, p2, gp["guides"], p3))
        PS.label_sheet(pf.out / "views" / "six_view_sheet.png", "DIAGNOSTIC TOOL TEST - WP6 fit-4 2026-10-07, past the "
                       "proof's owner stop; Prada scene copy; tool test (not a delivered model)")

        def sheets():
            glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in p2["guide_lines"].items()}
            models = {"automatic fit-4": RV.Model.from_spec(job, res["candidate"]["spec"], "fit-4"),
                      "delivered": RV.Model.delivered(job)}
            fits = {"automatic fit-4": PS._compare_cache(res["candidate"], "candidate"),
                    "delivered": {k: (dict(f) if k != "06" else {kk: vv for kk, vv in f.items() if kk != "family"})
                                  for k, f in fits_d.items()}}
            hs = HAND / "spec_handtraced.json"
            if hs.exists():
                models = {"automatic fit-4": models["automatic fit-4"],
                          "hand-traced": RV.Model.from_spec(job, json.loads(hs.read_text(encoding="utf-8")), "hand"),
                          "delivered": models["delivered"]}
                fh = pickle.loads((HAND / "fits_handtraced.pkl").read_bytes())["H"]
                fits = {"automatic fit-4": fits["automatic fit-4"], "hand-traced": {k: dict(f) for k, f in fh.items()},
                        "delivered": fits["delivered"]}
            steps = crease_table(job, models, fits, glines)
            path = comparison_sheet(job, models, fits, glines, steps, pf.out / "sheet_fit4_TOOL_TEST.png")
            out = {"path": str(path), "steps": steps}
            for title, keys in VS.SHEETS.items():
                pth = pf.out / "sheets" / f"sheet_{title}_TOOL_TEST.png"
                VS.view_sheet(job, models, fits, keys, pth, f"WP6 Prada fit-4 DIAGNOSTIC, scene copy: {title}")
                out[f"view_sheet_{title}"] = str(pth)
            return out
        pf.stage("SHEETS", sheets)
        tb = build_temple(res["candidate"]["spec"])
        pf.rec["geometry"] = {"creases": [{k: c[k] for k in ("regions", "length_mm", "dihedral_deg", "shape")}
                                          for c in tb.tables["creases"]],
                              "numbers": report.geometry_numbers(tb, photo_creases=4),
                              "offset_CB_mm": CU.model_offset_cb(tb)}
        if gt.get("error"):
            return finish(pf, "GATES crashed")
        pf.rec["gates_failed"] = gt.get("failed")
        return finish(pf, f"fit-4 diagnostic done: gates failed {gt.get('failed')}")
    finally:
        if pf.pool is not None:
            pf.pool.close()


def finish(pf, msg) -> int:
    pf.rec["open_owner_decisions"] = PS.open_owner_decisions(pf.rec)
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
