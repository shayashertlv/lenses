"""WP6 fix round 1 DIAGNOSTIC (TOOL TEST, $0, NOT a proof result): the joint fit (candidate and fitted slab, FIT_VERSION
fit-2), the P3 build on a Prada scene COPY and the gates, run PAST the proof's open stops so the owner sees what every
gate reads while the STOP-I (S1 theta / outline) and R1 decisions are open. The proof itself (tests/proofs/wp6_prada.py)
stops at its first stop; nothing here feeds it, and every output is labelled DIAGNOSTIC TOOL TEST.

Inputs (read only): data/wp6/proof/proof.json (start parameters, the recorded P2 trace and crease lines),
data/wp6/proof/fits_delivered.pkl (the delivered model's §6 cameras). Outputs: data/wp6/diagnostic_fix1/.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_fix1_diagnostic [--workers N] [--quick]

Owner machine rule: a process pool of os.cpu_count() - 2 workers, each opted out of EcoQoS (relief_fit.FitPool);
progress at least every 60 s (heartbeat) and every 30 s inside pooled batches.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.tests.proofs import wp6_prada as P  # noqa: E402
from glasses3d.tests.proofs import wp6_prada_stages as PS  # noqa: E402

LABEL = ("DIAGNOSTIC TOOL TEST (WP6 fix round 1): fit + P3 + gates run past the proof's open stops on a Prada scene "
         "COPY; NOT a proof result, not a delivered model")
PROOF = G3D / "data" / "wp6" / "proof"
OUT = G3D / "data" / "wp6" / "diagnostic_fix1"


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
    pf.rec["proof_record"] = {"path": str(PROOF / "proof.json"), "stop": rec.get("stop"),
                              "s1_failures": rec.get("s1_failures"), "rules_sha256": rec["rules"]["sha256"]}
    if rec["rules"]["sha256"] != pf.rec["rules"]["sha256"]:
        raise SystemExit("relief_rules.py differs from the proof's record")
    job = RV.Job("prada")
    fits_d = pickle.loads((PROOF / "fits_delivered.pkl").read_bytes())
    gp = json.loads(P.GUIDES.read_text(encoding="utf-8"))
    start = P.start_params(job, gp)
    p2 = rec["stages"]["P2"]
    try:
        if a.workers > 1:
            pf.pool = RF.FitPool(a.workers, log=pf.hb.log)
            up = pf.pool.warm(job)
            pf.rec["pool"] = {"workers": a.workers, "started": len(up),
                              "ecoqos_opt_out": sum(bool(u["ecoqos_opt_out"]) for u in up)}
            pf.hb.log(f"process pool: {len(up)} of {a.workers} workers up, {pf.rec['pool']['ecoqos_opt_out']} opted "
                      "out of EcoQoS")
        fo = pf.stage("FIT", lambda: PS.stage_fit(pf, job, start, p2, a.quick))
        if fo.get("error"):
            return finish(pf, "FIT crashed")
        res = fo["_results"]
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
        PS.label_sheet(pf.out / "views" / "six_view_sheet.png", "DIAGNOSTIC TOOL TEST - WP6 fix round 1, past the "
                       "proof's open stops; Prada scene copy; not a delivered model")
        if gt.get("error"):
            return finish(pf, "GATES crashed")
        pf.rec["gates_failed"] = gt.get("failed")
        return finish(pf, f"diagnostic done: gates failed {gt.get('failed')}")
    finally:
        if pf.pool is not None:
            pf.pool.close()


def finish(pf, msg) -> int:
    pf.rec["result"] = msg
    pf.rec["finished_utc"] = P.utc()
    pf.rec["source_scene"]["sha256_after"] = P.sha(P.PRADA_SCENE)
    pf.save()
    pf.hb.log(f"result: {msg}")
    pf.hb.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
