"""WP6 RESUME DIAGNOSTIC (2026-10-06; TOOL TEST, $0, NOT a proof result). The proof (tests/proofs/wp6_prada.py
--resume) stops at STOP-I: the S1 on record cannot recover theta (2.86 deg vs 0.3) and R2 / R3 read theta; only the
owner may waive that. This diagnostic runs what the proof would run next, PAST that stop, so the owner sees every gate
and the comparison sheets while STOP-I and the R1 / R3 rule-set decisions are open: P2 traced from the clean start
(the guides' start parameters), the joint fit of the candidate and of the fitted zero-relief slab control (pooled,
FIT_VERSION as recorded), the P3 build on a Prada scene COPY, the gates (graded against the delivered GLB AND the fitted
slab) and the six-view sheet. Nothing here feeds the proof record; every output is labelled DIAGNOSTIC TOOL TEST.

Inputs (read only): data/wp6/proof/proof.json (its stop, its S1 case (b); its rules sha256 must match),
data/wp6/proof/fits_delivered.pkl (the delivered model's section-6 cameras). Outputs: data/wp6/diagnostic_resume/.
The comparison sheets are drawn afterwards from those outputs by tests/proofs/wp6_view_sheets.py.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_resume_diagnostic [--workers N] [--quick]

Owner machine rule: a process pool of os.cpu_count() - 2 workers, each opted out of EcoQoS (relief_fit.FitPool);
progress at least every 60 s (heartbeat) and every 30 s inside pooled batches.
"""
from __future__ import annotations

import argparse
import copy
import json
import pickle
import sys
from pathlib import Path

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.tests.proofs import wp6_prada as P  # noqa: E402
from glasses3d.tests.proofs import wp6_prada_stages as PS  # noqa: E402

LABEL = ("DIAGNOSTIC TOOL TEST (WP6 resume 2026-10-06): P2 from the clean start + fit + P3 + gates run past the "
         "proof's STOP-I on a Prada scene COPY; NOT a proof result, not a delivered model")
PROOF = G3D / "data" / "wp6" / "proof"
OUT = G3D / "data" / "wp6" / "diagnostic_resume"
PREV_DIAG = G3D / "data" / "wp6" / "diagnostic_fix2" / "diagnostic.json"     # fix round 2 diagnostic (same clean start)


class Diag(P.Proof):
    """Proof's stage / save / stop bookkeeping, written to diagnostic.json in OUT (never the proof's record)."""

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
                    "ecoqos_opt_out": P.opt_out_ecoqos(), "stages": {}, "would_stop": [], "stop": None}
        self.save()

    def save(self):
        (self.out / "diagnostic.json").write_text(json.dumps(self.rec, indent=1, default=P._json), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=RF.pool_workers())
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)
    pf = Diag(OUT, a)
    pf.hb.log(LABEL)
    rec = json.loads((PROOF / "proof.json").read_text(encoding="utf-8"))
    s1 = rec["stages"].get("S1") or {}
    pf.rec["proof_record"] = {"path": str(PROOF / "proof.json"), "stop": rec.get("stop"),
                              "s1_failures": rec.get("s1_failures"), "s1_not_fitted": rec.get("s1_not_fitted"),
                              "rules_sha256": rec["rules"]["sha256"],
                              "s1_procedure": s1.get("s1_procedure"), "s1_fit_version": s1.get("fit_version"),
                              "s1_case_b_mm": PS._s1_case_b(rec)}
    if rec["rules"]["sha256"] != pf.rec["rules"]["sha256"]:
        raise SystemExit("relief_rules.py differs from the proof's record")
    job = RV.Job("prada")
    fits_d = pickle.loads((PROOF / "fits_delivered.pkl").read_bytes())
    gp = json.loads(P.GUIDES.read_text(encoding="utf-8"))
    start = P.start_params(job, gp)
    start_json = json.dumps(start, sort_keys=True, default=P._json)
    pf.rec["start_params"] = copy.deepcopy(start)
    try:
        if a.workers > 1:
            pf.pool = RF.FitPool(a.workers, log=pf.hb.log)
            up = pf.pool.warm(job)
            pf.rec["pool"] = {"workers": a.workers, "started": len(up),
                              "ecoqos_opt_out": sum(bool(u["ecoqos_opt_out"]) for u in up)}
            pf.hb.log(f"process pool: {len(up)} of {a.workers} workers up, {pf.rec['pool']['ecoqos_opt_out']} opted "
                      "out of EcoQoS")
        p2 = pf.stage("P2", lambda: PS.stage_p2(pf, job, fits_d, start, gp["guides"]))
        if p2.get("error"):
            return finish(pf, "P2 crashed")
        prev = json.loads(PREV_DIAG.read_text(encoding="utf-8")) if PREV_DIAG.exists() else {}
        old_p2 = (prev.get("stages") or {}).get("P2") or {}
        if old_p2.get("traced"):                 # same clean start, same code path: a reproducibility check
            import numpy as np
            d = {e: np.abs(np.array(p2["traced"][e])[:, 1] - np.array(old_p2["traced"][e])[:, 1])
                 for e in ("top", "bottom")}
            pf.rec["p2_vs_fix2_diagnostic"] = {e: {"mean_mm": float(v.mean()), "max_mm": float(v.max())}
                                              for e, v in d.items()}
            pf.hb.log(f"P2 traced outline vs the fix-2 diagnostic: {pf.rec['p2_vs_fix2_diagnostic']}")
        fo = pf.stage("FIT", lambda: PS.stage_fit(pf, job, start, p2, a.quick))
        if fo.get("error"):
            return finish(pf, "FIT crashed")
        res = fo["_results"]
        pfits = (prev.get("stages") or {}).get("FIT", {}).get("fits") or {}
        pf.rec["fit_vs_fix2_diagnostic"] = {lab: {"x_now": r["x"], "x_fix2": (pfits.get(lab) or {}).get("x"),
                                                  "loss_now": r["loss"], "loss_fix2": (pfits.get(lab) or {}).get("loss"),
                                                  "identical": (pfits.get(lab) or {}).get("x") == r["x"]}
                                            for lab, r in res.items()}
        for lab, r in res.items():
            fam = (r.get("fits", {}).get("06") or {}).get("family")
            if fam and fam.get("stop_r"):
                pf.rec["would_stop"].append({"code": "STOP-R", "model": lab})
        if fo["stop_s"]:
            pf.rec["would_stop"].append({"code": "STOP-S", "views": fo["stop_s"]})
        if fo["s3"]["stop_c"] or fo["s3"]["front_bound_stop"]:
            pf.rec["would_stop"].append({"code": "STOP-C", "stop_c": fo["s3"]["stop_c"],
                                         "front_bound_stop": fo["s3"]["front_bound_stop"]})
        pf.save()
        p3 = pf.stage("P3", lambda: PS.stage_p3(pf, res["candidate"]["spec"]))
        gt = pf.stage("GATES", lambda: PS.stage_gates(pf, job, fits_d, fo, p2, gp["guides"], p3))
        PS.label_sheet(pf.out / "views" / "six_view_sheet.png", "DIAGNOSTIC TOOL TEST - WP6 resume 2026-10-06, past the "
                       "proof's STOP-I; Prada scene copy; tool test (not a delivered model)")
        if json.dumps(start, sort_keys=True, default=P._json) != start_json:
            pf.rec["start_mutated"] = True
        if gt.get("error"):
            return finish(pf, "GATES crashed")
        pf.rec["gates_failed"] = gt.get("failed")
        return finish(pf, f"diagnostic done: gates failed {gt.get('failed')}")
    finally:
        if pf.pool is not None:
            pf.pool.close()


def finish(pf, msg) -> int:
    PS.record_gate_decisions(pf.rec)
    pf.rec["result"] = msg
    pf.rec["finished_utc"] = P.utc()
    pf.rec["source_scene"]["sha256_after"] = P.sha(P.PRADA_SCENE)
    pf.save()
    pf.hb.log(f"result: {msg}")
    pf.hb.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
