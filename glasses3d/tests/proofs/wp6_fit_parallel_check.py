"""WP6: re-run the Prada CANDIDATE joint fit on the process pool and compare it with the recorded serial fit
(data/wp6/proof/fit_candidate.pkl): the same inputs as the proof's FIT stage (tests/proofs/wp6_prada_stages.stage_fit:
the start parameters, P2's traced outline, corners and guide lines from proof.json, the outline re-traced every round,
four rounds, the rear family in round 0). A TOOL TEST on the proof's recorded data; it writes only to --out-dir
(default data/wp6/fit_parallel_check), never to data/wp6/proof and never to a scene.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_fit_parallel_check [--workers N] [--out-dir DIR]

The result records the wall time against the recorded serial fit's, and whether x, the loss, the history, sigma and
every camera are exactly (==) the recorded ones.
"""
from __future__ import annotations

import argparse
import copy
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_trace as RT  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.tests.proofs.wp6_prada import GUIDES, Heartbeat, _json, sha, RULES, start_params  # noqa: E402

PROOF = G3D / "data" / "wp6" / "proof"
LABEL = "TOOL TEST (WP6 pooled candidate fit vs the recorded serial fit, Prada proof data; not a delivered model)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=RF.pool_workers())
    ap.add_argument("--out-dir", default=str(G3D / "data" / "wp6" / "fit_parallel_check"))
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    hb = Heartbeat()
    hb.stage = "pooled candidate fit"
    rec = json.loads((PROOF / "proof.json").read_text(encoding="utf-8"))
    if rec["rules"]["sha256"] != sha(RULES):
        raise SystemExit("relief_rules.py changed since the recorded proof: the comparison would be meaningless")
    old = pickle.loads((PROOF / "fit_candidate.pkl").read_bytes())
    job = RV.Job("prada")
    gp = json.loads(GUIDES.read_text(encoding="utf-8"))
    params = copy.deepcopy(start_params(job, gp))
    p2 = rec["stages"]["P2"]
    t = p2.get("traced") or {}
    if t.get("top"):
        params["top"], params["bottom"] = t["top"], t["bottom"]
    for k in ("B", "C"):
        if t.get(k):
            params[k] = tuple(t[k])
    fam = RF.prada_family(params)
    glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in p2.get("guide_lines", {}).items()}

    def retrace(family, x, fits):                      # stage_fit's retrace, verbatim
        kw = family.kwargs(x)
        builder = lambda tt, bb: (family.builder(**{**kw, "top": tt, "bottom": bb}) if not family.slab_mode
                                  else RF.slab_spec(**{**kw, "top": tt, "bottom": bb}))
        ol = RT.trace_outline(job, family.spec(x), builder,
                              {k: {"camera": fits[k]["camera"], "theta": fits[k]["theta"]} for k in ("05", "01")},
                              ("05", "01"), iters=2, log=hb.log)
        fam2 = copy.deepcopy(family)
        fam2.base["top"], fam2.base["bottom"] = ol["top"], ol["bottom"]
        return fam2

    hb.log(f"{LABEL}; workers {a.workers}")
    with RF.FitPool(a.workers, log=hb.log) as pool:
        up = pool.warm(job)
        hb.log(f"process pool: {len(up)} workers up, {sum(bool(u['ecoqos_opt_out']) for u in up)} opted out of EcoQoS")
        t0 = time.time()
        res = RF.fit_temple(job, fam, guides_lines=glines, rounds=4, rear=True, retrace=retrace, log=hb.log,
                            budget_s=90 * 60, pool=pool)
        wall = time.time() - t0
        stats = dict(pool.stats)
    keep = {k: v for k, v in res.items() if k not in ("context", "family")}
    (out / "fit_candidate_pooled.pkl").write_bytes(pickle.dumps(keep))
    same_cams = {k: (old["fits"][k]["camera"] == res["fits"][k]["camera"] and
                     old["fits"][k]["theta"] == res["fits"][k]["theta"]) for k in old["fits"]}
    rep = {"label": LABEL, "workers": a.workers, "pool_stats": stats,
           "recorded_serial_seconds": old["seconds"], "pooled_seconds": wall, "speedup": old["seconds"] / wall,
           "x_identical": old["x"] == res["x"], "loss_identical": old["loss"] == res["loss"],
           "history_identical": old["history"] == res["history"],
           "sigma_identical": repr(old["sigma"]) == repr(res["sigma"]),
           "spec_identical": json.dumps(old["spec"], sort_keys=True) == json.dumps(res["spec"], sort_keys=True),
           "cameras_identical": same_cams, "over_budget": res.get("over_budget"),
           "recorded": {"x": old["x"], "loss": old["loss"]}, "pooled": {"x": res["x"], "loss": res["loss"]},
           "history_evaluations": [h["evaluations"] for h in res["history"]]}
    rep["all_identical"] = bool(rep["x_identical"] and rep["loss_identical"] and rep["history_identical"]
                                and rep["sigma_identical"] and rep["spec_identical"] and all(same_cams.values()))
    (out / "check.json").write_text(json.dumps(rep, indent=1, default=_json), encoding="utf-8")
    hb.log(json.dumps({k: v for k, v in rep.items() if k not in ("recorded", "pooled")}, default=_json))
    hb.stop()
    return 0 if rep["all_identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
