"""$0 all-six camera proof on the exact owner-accepted GLB; no Blender import or model editing.

Compare newly fitted cameras and silhouettes with the immutable M5-fix-1 item-4 baseline. Every original photo
remains a row. The top-photo usability result is a camera diagnostic, never a new model-likeness finish gate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time

from .assembly_m5fix1_baseline import (
    ACCEPTED, ACCEPTED_SHA256, JOB, REPO, StaticAssetSession, sha, static_seeing, write,
)


BASELINE = REPO / "glasses3d/data/assembly/m5fix1-baseline-a/cameras/basis-01/cameras.json"


def run(out, requested_workers=None):
    from glasses3d.agent.tools import fit_workers, free_commit_bytes, single_threaded_blas
    from glasses3d.g3d_host import compare as C, fit_views as FV

    out = Path(out).resolve()
    if out.exists() or out.is_relative_to(JOB) or out.is_relative_to(ACCEPTED.parent):
        raise ValueError("choose a new evidence folder outside the original job and accepted asset")
    job_path = JOB / "job.json"
    job = json.loads(job_path.read_text(encoding="utf8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf8"))
    if sha(ACCEPTED) != ACCEPTED_SHA256 or baseline["model"]["sha256"] != ACCEPTED_SHA256:
        raise AssertionError("accepted GLB and original baseline must match the owner's bound asset hash")
    if len(job["images"]) != 6 or len(baseline["images"]) != 6:
        raise AssertionError("the exact six original photos are required")
    sources = {str(ACCEPTED): ACCEPTED_SHA256, str(job_path): sha(job_path), str(BASELINE): sha(BASELINE)}
    for row in job["images"]:
        source = (JOB / row["file"]).resolve()
        if not source.is_relative_to(JOB / "images") or sha(source) != row["sha256"]:
            raise AssertionError("a source photo differs from its job receipt")
        before = baseline["images"][row["original_name"]]
        if before["image_sha256"] != row["sha256"] or before["matte_mode"] != row["matte_mode"]:
            raise AssertionError("photo bytes or matte policy differ from the original baseline")
        sources[str(source)] = row["sha256"]
        if before.get("overlay"):
            sources[str(Path(before["overlay"]).resolve())] = sha(before["overlay"])
    out.mkdir(parents=True)
    (out / "work/exports").mkdir(parents=True)
    (out / "images").mkdir()
    asset = out / "work/exports/accepted.glb"
    shutil.copyfile(ACCEPTED, asset)
    images, views, candidates, mattes = {}, {}, {}, {}
    for row in job["images"]:
        photo = out / row["file"]
        shutil.copyfile(JOB / row["file"], photo)
        if sha(photo) != row["sha256"]:
            raise AssertionError("copied photo bytes changed")
        name = row["original_name"]
        images[name], views[name] = photo, row["fit_kinds"][0]
        candidates[name], mattes[name] = tuple(row["fit_kinds"]), row["matte_mode"]
    write(out / "input-receipt.json", {"asset_sha256": ACCEPTED_SHA256, "source_hashes": sources,
                                      "photos": job["images"], "identity_kind": "immutable_existing_glb"})
    report = {"schema": "g3d.accepted_asset_camera_roll/1", "status": "running",
              "source_asset": str(ACCEPTED), "asset_sha256": ACCEPTED_SHA256,
              "identity_kind": "immutable_existing_glb", "imported_or_reexported": False,
              "blender_checkpoint_verified": False, "model_requests": 0, "actual_spend_usd": 0,
              "baseline": str(BASELINE), "source_hashes": sources, "stages": {}}
    seeing = static_seeing(StaticAssetSession(out, asset))
    started = time.monotonic()

    def stage(name, action):
        t0 = time.monotonic()
        print(f"START {name} {datetime.now(timezone.utc).isoformat()}", flush=True)
        result = action()
        report["stages"][name] = {"seconds": time.monotonic() - t0, "result": result}
        write(out / f"{name}.json", result)
        write(out / "proof.json", report)
        print(f"END {name} {report['stages'][name]['seconds']:.2f}s status={result.get('status')}", flush=True)
        return result

    try:
        report["free_commit_bytes_before_fit"] = free_commit_bytes()
        report["fit_workers"] = fit_workers(requested_workers)
        with single_threaded_blas():
            fit = stage("fit", lambda: seeing.fit_cameras(
                "Verify complete-circle top/bottom camera search on the unchanged accepted asset and all six photos.",
                images, views, refs="all", workers=report["fit_workers"], candidates=candidates,
                matte_modes=mattes, call_id="roll-fit", log=lambda *args: print(*args, flush=True)))
        if fit.get("status") != "ok":
            raise AssertionError(f"camera fitting did not complete: {fit}")
        basis = C.load_basis(out, fit["basis"])
        after = json.loads((basis.dir / FV.CAMERAS_JSON).read_text(encoding="utf8"))
        if list(after["images"]) != list(baseline["images"]):
            raise AssertionError("camera proof omitted or reordered reference photos")
        compared = stage("compare", lambda: seeing.compare_to_photos(
            refs="all", parts="all", passes=[], basis=basis.id, call_id="roll-compare"))
        if compared.get("status") != "ok":
            raise AssertionError(f"contour comparison did not complete: {compared}")
        rows = []
        for source in job["images"]:
            name = source["original_name"]
            before, current = baseline["images"][name], after["images"][name]
            keys = ("status", "view", "camera", "iou", "iou_lens_optional", "contour_mean_mm",
                    "contour_mean_px_lens_optional", "loss", "loss_common_level", "refusal", "overlay")
            row = {"image": name, "photo_sha256": source["sha256"], "matte_mode": source["matte_mode"],
                   "view_kind": source["view_kind"], "fit_kinds": source["fit_kinds"],
                   "before": {k: deepcopy(before.get(k)) for k in keys},
                   "after": {k: deepcopy(current.get(k)) for k in keys},
                   "before_usability_under_new_policy": FV.camera_usability(before),
                   "after_usability": current["usability"],
                   "raw_iou_delta": current["iou"] - before["iou"],
                   "contour_mm_delta": current["contour_mean_mm"] - before["contour_mean_mm"],
                   "comparison": compared.get("refs", {}).get(C.ref_id(name)),
                   "comparison_refusal": compared.get("unusable_refs", {}).get(C.ref_id(name))}
            rows.append(row)
        report["per_photo"] = rows
        write(out / "per-photo.json", rows)
        top = [r for r in rows if r["view_kind"] == "top"]
        report["top_registered"] = bool(top) and all(
            r["after"]["status"] == "ok" and r["after_usability"]["usable"]
            and r["after"]["camera"]["pitch"] > 0 for r in top)
        report["other_cameras_unchanged"] = all(
            r["before"]["camera"] == r["after"]["camera"] for r in rows if r not in top)
        report["all_photos_retained"] = len(rows) == len(images) == 6
        report["notes"] = [
            "Historical baseline files are unchanged; its status is shown as originally recorded.",
            "The new-policy baseline classification is separate evidence, not a rewrite of the saved basis.",
            "All six unchanged photos were fitted. Native overlays and contour sheets are linked per row.",
            "The 0.5 usability floor labels a registration; it is not an appearance or finish requirement."]
        report["status"] = "passed" if report["top_registered"] and report["other_cameras_unchanged"] else "failed"
        return report
    except Exception as exc:
        report.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        report["seconds"] = time.monotonic() - started
        report["sources_unchanged"] = all(sha(path) == value for path, value in sources.items())
        report["copied_glb_unchanged"] = sha(asset) == ACCEPTED_SHA256
        if not report["sources_unchanged"] or not report["copied_glb_unchanged"]:
            report["status"] = "failed"
        write(out / "proof.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args()
    result = run(args.out, args.workers)
    print(json.dumps({k: result[k] for k in ("status", "seconds", "top_registered", "other_cameras_unchanged",
                                            "sources_unchanged", "actual_spend_usd")}), flush=True)
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
