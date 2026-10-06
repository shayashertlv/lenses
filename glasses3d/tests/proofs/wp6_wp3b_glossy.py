"""WP6 §6: re-record the WP3b Prada camera table with the glossy_floor matte, beside the old one.

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.proofs.wp6_wp3b_glossy [--out-dir DIR]

Same code, seeds and candidates as WP3b (fit_views.fit_views on the sha256-checked reference data), only the matte
differs: fit_views' matte call is pointed at matte(mode="glossy_floor") for this run (sequential, one process, so the
switch reaches every fit). Writes DIR/cameras.json + overlays and DIR/table.json (old vs new, per photo).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from glasses3d.g3d_host import fit_views as FV
from glasses3d.g3d_host import matte as M

G3D = Path(__file__).resolve().parents[2]
OUT = G3D / "data" / "wp6" / "wp3b_glossy"
OLD = G3D / "data" / "wp3b" / "prada" / "cameras.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(OUT))
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prod = FV.PRODUCTS["prada"]
    files = FV.reference_files("prada")
    real = M.matte
    FV.M.matte = lambda img, **kw: real(img, mode="glossy_floor")
    t0 = time.time()
    try:
        rec = FV.fit_views(files[FV.MODEL], {n: files[n] for n in prod.views}, dict(prod.views), out,
                           skip=prod.skip, front_parts=prod.front_parts, workers=1,
                           log=lambda m: print(f"[{time.time() - t0:6.0f} s] {m}", flush=True))
    finally:
        FV.M.matte = real
    old = json.loads(OLD.read_text(encoding="utf-8")) if OLD.exists() else {"images": {}}
    table = {}
    for name, r in rec["images"].items():
        o = old["images"].get(name, {})
        table[name] = {"old": {k: o.get(k) for k in ("iou", "contour_mean_mm", "contour_p90_mm")},
                       "glossy_floor": {k: r.get(k) for k in ("iou", "contour_mean_mm", "contour_p90_mm")},
                       "camera_old": o.get("camera"), "camera_glossy_floor": r.get("camera")}
        print(f"{name}: IoU {o.get('iou', float('nan')):.3f} -> {r['iou']:.3f}, contour {o.get('contour_mean_mm', float('nan')):.2f}"
              f" -> {r['contour_mean_mm']:.2f} mm", flush=True)
    (out / "table.json").write_text(json.dumps({"matte": "glossy_floor", "table": table,
                                               "seconds": time.time() - t0}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
