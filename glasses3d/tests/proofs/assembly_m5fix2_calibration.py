"""$0 fresh calibration on immutable accepted photos/model, then untouched held-out photo_read picks.

The fitter receives silhouettes only. Accuracy evaluation happens after every camera has been frozen.
Exit 2 preserves a completed but failed accuracy proof; it is never converted to a synthetic pass.
"""
from __future__ import annotations

import os
for _variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_variable] = "1"

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time

from glasses3d.agent.offline import disable_sdk_tracing, LoopbackOnly, ProductionTransportTripwire
from glasses3d.agent.procs import DescendantsOptOut, opt_out_self, write_json_atomic
from glasses3d.agent.tools import fit_workers, free_commit_bytes
from glasses3d.g3d_host import compare as C, fit_views as FV


def run(source, out, requested_workers=None):
    source, out = Path(source).resolve(), Path(out).resolve()
    if out.exists() or out.is_relative_to(source):
        raise ValueError("choose a fresh output directory outside the immutable source job")
    original = C.load_basis(source, "basis-01")
    previous = json.loads((original.dir/FV.CAMERAS_JSON).read_text(encoding="utf8"))
    asset_source = Path(original.meta["model"]["fit_glb_path"])
    if C.sha256_file(asset_source) != original.meta["model"]["sha256"]:
        raise AssertionError("accepted model differs from frozen source")
    if len(original.refs) != 6:
        raise AssertionError("this proof requires all six original photographs")
    out.mkdir(parents=True)
    asset = out / "work/exports/accepted.glb"
    asset.parent.mkdir(parents=True)
    shutil.copyfile(asset_source, asset)
    sources = {str(original.dir/"basis.json"): C.sha256_file(original.dir/"basis.json"),
               str(original.dir/FV.CAMERAS_JSON): C.sha256_file(original.dir/FV.CAMERAS_JSON),
               str(asset_source): C.sha256_file(asset_source)}
    images, views, candidates, n_refine, mattes = {}, {}, {}, {}, {}
    for rid, row in original.refs.items():
        source_photo = original.photo_path(rid)
        relative = Path(row["photo"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("source photos must have confined relative paths")
        photo = out / relative
        photo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_photo, photo)
        sources[str(source_photo)] = C.sha256_file(source_photo)
        if C.sha256_file(photo) != sources[str(source_photo)]:
            raise AssertionError("photo copy differs")
        name = row["image"]
        old = previous["images"][name]
        images[name], views[name] = photo, old["view_kind"]
        candidates[name], n_refine[name] = tuple(old["candidates"]), old["n_refine"]
        mattes[name] = old["matte_mode"]
    code_root = Path(__file__).resolve().parents[2]
    code_names = ["g3d_host/"+name+".py" for name in
                  ("camera", "cameras", "raster", "fit_views", "fitmesh", "matte", "compare", "pose")]
    report = {"schema": "g3d.m5fix2_calibration/1", "status": "running", "cost_usd": 0,
              "source": str(source), "output": str(out), "source_hashes": sources,
              "source_code_sha256": {name: C.sha256_file(code_root/name) for name in code_names},
              "asset_sha256": C.sha256_file(asset), "created_utc": datetime.now(timezone.utc).isoformat(),
              "free_commit_bytes": free_commit_bytes(), "workers": fit_workers(requested_workers),
              "fit_inputs": "All six unchanged photos and accepted model; original candidates, n_refine, "
                            "matte policy and bounds. No held-out feature coordinates enter fitting."}
    write_json_atomic(out/"input-receipt.json", report)
    started = time.monotonic()
    try:
        print(f"START fresh six-photo fit: {report['workers']} workers", flush=True)
        fitted = FV.fit_views(asset, images, views, out/"cameras/basis-01",
                             skip=original.meta["skip"], front_parts=original.meta["front_parts"],
                             candidates=candidates, n_refine=n_refine, matte_modes=mattes,
                             workers=report["workers"], off_axis=True, log=lambda *a: print(*a, flush=True))
        model = {**original.meta["model"], "fit_glb_path": str(asset), "glb_path": str(asset)}
        basis = C.freeze_basis(out, out/"cameras/basis-01", images,
                               reason="Fresh general off-axis camera fit; all accuracy picks held out.",
                               call_id="off-axis-proof-fit", source="fit_cameras", model=model)
        report["basis_sha256"] = C.sha256_file(basis.dir/"basis.json")
        report["photos"] = {name: {key: row.get(key) for key in
                                  ("status", "refusal", "camera", "iou", "iou_lens_optional", "flags", "off_axis", "sign")}
                            for name, row in fitted["images"].items()}
        print("END fit; evaluate the unchanged four held-out features", flush=True)
        # Import observations only after fitting and freezing. The original picks/errors remain unchanged.
        from .assembly_m5fix2_photo_read import run as evaluate
        accuracy = evaluate(out, out/"held-out")
        report["accuracy"] = accuracy
        report["status"] = "passed" if accuracy["accuracy_proof_passed"] else "accuracy_failed"
        return report
    except Exception as exc:
        report.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        report["seconds"] = time.monotonic()-started
        report["sources_unchanged"] = all(C.sha256_file(path) == sha for path, sha in sources.items())
        report["copied_model_unchanged"] = C.sha256_file(asset) == report["asset_sha256"]
        write_json_atomic(out/"proof.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args()
    disable_sdk_tracing()
    opt_out_self()
    with DescendantsOptOut(), LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        report = run(args.source, args.out, args.workers)
        report["network_attempts"] = network.attempts
        report["production_client_attempts"] = clients.constructions
        assert not network.attempts and not clients.constructions
        assert report["sources_unchanged"] and report["copied_model_unchanged"]
        write_json_atomic(args.out/"proof.json", report)
    print(json.dumps({key: report[key] for key in
                      ("status", "seconds", "cost_usd", "sources_unchanged", "copied_model_unchanged")}), flush=True)
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
