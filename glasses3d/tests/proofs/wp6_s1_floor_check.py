"""WP6 S1 follow-up (fix round 3; TOOL TEST, $0, writes only data/wp6/s1_floor_check.json): is the S1 instrument
floor's STOP-I (the TRUE temple's R3 transfer > R3_TRANSFER_MAX_MM on lines that carry the real tracer's endpoint
moves) a property of the real lines' error budget, or of the one sign pattern the proof's S1 copied?

The S1 truth is rebuilt from the proof record (wp6_s1_theta_check.rebuild_s1: sha-checked truth, the recorded true
cameras and theta). S1's exact lines are recovered from its measured lines (the cue's photo-step lines) by taking the
recorded endpoint moves back off along each line's own normal; then the TRUE temple's R3 transfer is computed at the
S1-fitted 05 / 02 cameras and theta for every sign pattern of the real move SIZES on the four 02 endpoints (A-B and
B-C, 16 patterns; 05's own small moves kept as recorded), and for the exact lines. The proof's measured pattern is one
of the 16. Evidence for the owner's STOP-I decision; it never decides it.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_s1_floor_check
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.tests.proofs.wp6_prada import _unit_normal  # noqa: E402
from glasses3d.tests.proofs.wp6_s1_theta_check import rebuild_s1  # noqa: E402

PROOF = G3D / "data" / "wp6" / "proof"
OUT = G3D / "data" / "wp6" / "s1_floor_check.json"


def exact_from_measured(meas, moves, iters: int = 4):
    """The line whose endpoints moved by `moves` along its OWN normal gives `meas`: solve back (fixed point)."""
    m0, m1 = (np.asarray(v, float) for v in meas)
    e0, e1 = m0.copy(), m1.copy()
    for _ in range(iters):
        nv = _unit_normal(e0, e1)
        e0, e1 = m0 - moves[0] * nv, m1 - moves[1] * nv
    return e0, e1


def main() -> int:
    rec = json.loads((PROOF / "proof.json").read_text(encoding="utf-8"))
    job, sj, truth, s1 = rebuild_s1(rec)
    if not s1.get("line_error_added"):
        raise SystemExit("the recorded S1 added no endpoint error to its lines (s1-4 with line_error_added only)")
    fit = pickle.loads((PROOF / "s1_fit.pkl").read_bytes())["fits"]
    fits = {k: {"camera": fit[k]["camera"], "theta": fit[k]["theta"]} for k in ("05", "02")}
    steps = s1["cue"]["photo_steps"]
    meas = {k: {cn: (v["p0"], v["p1"]) for cn, v in steps[k].items() if v is not None} for k in ("05", "02")}
    moves = {k: {cn: s1["lines"][f"{k} {cn}"]["error_added_px"] for cn in meas[k]} for k in meas}
    exact = {k: {cn: exact_from_measured(meas[k][cn], moves[k][cn]) for cn in meas[k]} for k in meas}

    def transfer(lines):
        tr = RF.transfer_all(RF.Context(sj, fits, {}, (), lines), truth)
        return tr["mean_mm"], {c: (None if v is None else v["mean_abs_mm"]) for c, v in tr["per_crease"].items()}
    as_list = lambda d: {k: {cn: (np.asarray(a).tolist(), np.asarray(b).tolist()) for cn, (a, b) in v.items()}
                         for k, v in d.items()}
    t_meas, pc_meas = transfer(as_list({k: {cn: tuple(np.asarray(p, float) for p in v) for cn, v in d.items()}
                                        for k, d in meas.items()}))
    t_exact, pc_exact = transfer(as_list(exact))
    ends = [(cn, e) for cn in ("AB", "BC") if cn in exact["02"] for e in (0, 1)]
    pats = []
    for bits in range(2 ** len(ends)):
        sg = [1.0 if bits >> i & 1 else -1.0 for i in range(len(ends))]
        l02 = {}
        for cn in exact["02"]:
            e0, e1 = exact["02"][cn]
            nv = _unit_normal(e0, e1)
            q = [e0.copy(), e1.copy()]
            for (c2, e), s_ in zip(ends, sg):
                if c2 == cn:
                    q[e] = q[e] + s_ * abs(moves["02"][cn][e]) * nv
            l02[cn] = (q[0], q[1])
        lines = as_list({"05": {cn: tuple(np.asarray(p, float) for p in v) for cn, v in meas["05"].items()},
                         "02": l02})
        t, pc = transfer(lines)
        pats.append({"signs": dict(zip([f"02 {c} end {e}" for c, e in ends], sg)), "transfer_mm": t,
                     "per_crease": pc})
    tv = np.array([p["transfer_mm"] for p in pats], float)
    out = {"label": "TOOL TEST (S1 follow-up on synthetic photos of the S1 truth; no scene touched)",
           "s1_procedure": s1.get("s1_procedure"), "s1_truth_spec_sha256": s1["truth_spec_sha256"],
           "limit_mm": RR.R3_TRANSFER_MAX_MM,
           "true_temple_transfer_mm": {"measured_lines": t_meas, "exact_lines": t_exact},
           "per_crease": {"measured_lines": pc_meas, "exact_lines": pc_exact},
           "matches_s1_record_floor": abs(t_meas - s1["instrument_floor"]["models"]["truth"]["R3_transfer_mm"]) < 1e-6,
           "move_sizes_px_02": {cn: [abs(v) for v in moves["02"][cn]] for cn in exact["02"]},
           "sign_patterns": pats,
           "sign_patterns_summary": {"min_mm": float(tv.min()), "median_mm": float(np.median(tv)),
                                     "max_mm": float(tv.max()),
                                     "over_limit": int(np.sum(tv > RR.R3_TRANSFER_MAX_MM)), "of": int(len(tv))}}
    print(f"TRUE temple R3 transfer: measured lines {t_meas:.3f} mm, exact lines {t_exact:.3f} mm; 02 sign patterns "
          f"min {tv.min():.3f} / median {np.median(tv):.3f} / max {tv.max():.3f} mm, "
          f"{out['sign_patterns_summary']['over_limit']} of {len(tv)} over {RR.R3_TRANSFER_MAX_MM} mm", flush=True)
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
