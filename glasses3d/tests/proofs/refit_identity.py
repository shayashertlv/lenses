"""Re-fit a saved camera basis from its recorded inputs and compare it with the saved result ($0, host only).

    glasses3d/.venv/Scripts/python.exe -B -m glasses3d.tests.proofs.refit_identity --job JOB_ROOT --basis basis-06
        --out OUT_DIR

Reads the job's photos, the basis's exported GLB and its cameras.json; writes only OUT_DIR. Arguments come from the
saved records: view kinds, candidate views, explicit or automatic matte per photo, off-axis, initial openings.
Every image record must be equal except timings and output paths, the cross winners equal, and every overlay PNG
byte-identical. Prints the measured fit seconds beside the saved fit's. Used for the 2026-10-23 fit-speed changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from glasses3d.agent import procs
from glasses3d.agent.tools import fit_workers, single_threaded_blas
from glasses3d.g3d_host import fit_views

IGNORED = {"seconds", "created_utc"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _differences(old, new, where="", out=None):
    """Paths where two JSON values differ; timings are ignored and output paths compared by file name."""
    out = [] if out is None else out
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            if key in IGNORED:
                continue
            if key not in old or key not in new:
                out.append(f"{where}{key}: present on one side only")
            else:
                _differences(old[key], new[key], f"{where}{key}.", out)
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            out.append(f"{where}: {len(old)} vs {len(new)} items")
        for i, (a, b) in enumerate(zip(old, new)):
            _differences(a, b, f"{where}[{i}].", out)
    elif old != new and not (isinstance(old, str) and isinstance(new, str) and old.endswith(".png")
                             and Path(old).name == Path(new).name):
        out.append(f"{where}: {str(old)[:80]} != {str(new)[:80]}")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--basis", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=None)
    a = parser.parse_args(argv)
    saved_dir = a.job / "cameras" / a.basis
    saved = json.loads((saved_dir / "cameras.json").read_text(encoding="utf-8"))
    job = json.loads((a.job / "job.json").read_text(encoding="utf-8"))
    rows = {row["original_name"]: row for row in job["images"]}
    glb = Path(saved["model"]["path"])
    if _sha(glb) != saved["model"]["sha256"]:
        raise SystemExit(f"{glb} no longer has the saved model's bytes")
    images = {name: a.job / rows[name]["file"] for name in saved["images"]}
    views = {name: rec["view_kind"] for name, rec in saved["images"].items()}
    candidates = {name: tuple(rec["candidates"]) for name, rec in saved["images"].items()}
    mattes = {name: (rec["matte_selection"]["chosen"] if rec["matte_selection"]["mode"] == "explicit" else None)
              for name, rec in saved["images"].items()}
    openings = {}
    for name, rec in saved["images"].items():
        start = (rec.get("opening_fit") or {}).get("initial_theta_deg") or {}
        if any(float(v) for v in start.values()):
            openings[name] = start
    off_axis = "off_axis" in saved["policy"]
    workers = fit_workers(a.workers)
    a.out.mkdir(parents=True, exist_ok=False)
    procs.opt_out_self()
    started = time.perf_counter()
    with single_threaded_blas(), procs.DescendantsOptOut():
        result = fit_views.fit_views(glb, images, views, a.out, candidates=candidates, workers=workers,
                                     matte_modes=mattes, off_axis=off_axis, initial_openings=openings or None,
                                     log=lambda _: None)
    wall = time.perf_counter() - started
    fresh = json.loads((a.out / fit_views.CAMERAS_JSON).read_text(encoding="utf-8"))
    differences = _differences(saved["images"], fresh["images"])
    differences += _differences(saved["cross_winners"], fresh["cross_winners"], "cross_winners.")
    overlays = sorted(p.name for p in saved_dir.glob("overlay_*.png"))
    fresh_overlays = sorted(p.name for p in a.out.glob("overlay_*.png"))
    if overlays != fresh_overlays:
        differences.append(f"overlay files differ: {sorted(set(overlays) ^ set(fresh_overlays))}")
    differences += [f"overlay {n} bytes differ" for n in set(overlays) & set(fresh_overlays)
                    if _sha(saved_dir / n) != _sha(a.out / n)]
    report = {"job": str(a.job), "basis": a.basis, "workers": workers, "off_axis": off_axis,
              "matte_modes": mattes, "photos": len(images), "overlays_compared": len(overlays),
              "saved_fit_seconds": round(saved["seconds"], 1), "fit_seconds": round(result["seconds"], 1),
              "wall_seconds": round(wall, 1), "identical": not differences, "differences": differences[:50]}
    (a.out / "refit_identity.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0 if not differences else 1


if __name__ == "__main__":
    sys.exit(main())
