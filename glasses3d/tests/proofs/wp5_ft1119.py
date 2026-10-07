"""WP5 build steps (1)-(2) $0 proof on FT1119 (WP5_DESIGN §8 A0, B1, B2, A7 inputs; §9 STOP A).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.proofs.wp5_ft1119 [--a0-only | --skip-a0 | --finalize-only]
        [--out DIR] [--name ft1119]

Writes DIR/a0/a0.json (synthetic A0) and DIR/<name>/{registration,check_print,report}.json + sheets/ (DIR default
data/wp5).
STOP A in report.json counts A0's verdict when a0.json exists and fires when it does not. ``--skip-a0`` and
``--a0-only`` can run in parallel; ``--finalize-only`` then folds a0.json into the written report (STOP A, B1, B2).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import procs
from glasses3d.g3d_host.photoprint import report as RP
from glasses3d.g3d_host.photoprint import step0

OUT = IN.REPO / "glasses3d" / "data" / "wp5"


def run_a0(out_root=OUT) -> dict:
    from glasses3d.tests.photoprint import a0
    out = a0.run_a0()
    RP.write_json(out_root / "a0" / "a0.json", out)
    print(out["verdict_line"], flush=True)
    print(json.dumps(out["criteria"]), flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a0-only", action="store_true")
    ap.add_argument("--skip-a0", action="store_true")
    ap.add_argument("--finalize-only", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", default=str(OUT), help="output root (default data/wp5)")
    ap.add_argument("--name", default="ft1119", help="run directory under the root (default ft1119)")
    a = ap.parse_args(argv)
    root = Path(a.out)
    procs.opt_out_ecoqos()
    a0_pass, a0_line = None, None
    if a.finalize_only:
        a0_pass, a0_line = step0.load_a0(root / "a0" / "a0.json")
        rep = RP.finalize(root / a.name, a0_pass, a0_line)
        print(json.dumps(rep["stop_a"], indent=1))
        return 0
    if not a.skip_a0:
        a0r = run_a0(root)
        a0_pass, a0_line = a0r["pass"], a0r["verdict_line"]
    elif (root / "a0" / "a0.json").is_file():
        a0r = json.loads((root / "a0" / "a0.json").read_text(encoding="utf-8"))
        a0_pass, a0_line = a0r.get("pass"), a0r.get("verdict_line")
    if a.a0_only:
        return 0
    job = IN.load_job(IN.FT1119["cameras"])
    r = step0.run_step0(job, IN.FT1119_PARTS, root / a.name, workers=a.workers, a0_passed=a0_pass,
                        a0_summary=a0_line)
    print(json.dumps(r["report"]["stop_a"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
