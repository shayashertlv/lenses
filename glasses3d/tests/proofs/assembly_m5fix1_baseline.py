"""$0 accepted-asset instrument baseline; immutable GLB bytes, no Blender import/re-export.

Prepare before M5-fix-1 item 4; execute only after earlier items are complete. The adapter
supplies an existing GLB to the real Seeing fit/compare methods. Its asset SHA is not a
Blender scene fingerprint or a verified checkpoint. Comparison uses the supported empty
render-pass list: real contour metrics and photo/silhouette sheets; fixed-scale AR supplies
the material renders. Seating declarations and delivery gates are never changed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time


REPO = Path(__file__).resolve().parents[3]
ACCEPTED = REPO / "automation/data/blender_agent/studio/jobs/3dff867466d449c9a6369273f0a14631/agent/ar/h_detailed_closed_shield-6c7d3bd5/model.glb"
ACCEPTED_SHA256 = "2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688"
JOB = REPO / "glasses3d/data/jobs/oakley-oo9208-v2-3e057bab"
VENT_EVIDENCE = REPO / "glasses3d/data/assembly/m5oakley/thorough-review/visual/visual-evidence.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")


def load_vent_sources(sources):
    """Bind prior review measurements and original pixels before the baseline starts."""
    sources[str(VENT_EVIDENCE)] = sha(VENT_EVIDENCE)
    evidence = json.loads(VENT_EVIDENCE.read_text(encoding="utf8"))["vent_analysis"]
    for checkpoint in ("c0008", "c0021"):
        record = evidence[checkpoint]
        path = Path(record["path"]).resolve()
        if not path.is_relative_to(JOB / "work/compare") or sha(path) != record["sha256"]:
            raise AssertionError("vent source image path/hash differs from reviewed evidence")
        sources[str(path)] = record["sha256"]
    return evidence


def vent_comparison(out, evidence):
    """Compose labelled existing pixels only; area means rendered aperture, never physical area."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from scipy.ndimage import find_objects, label

    normal_width = 4000
    common_crop = [.40, .34, .60, .43]
    panels, records = [], {}
    for checkpoint in ("c0008", "c0021"):
        original = evidence[checkpoint]
        path = Path(original["path"])
        if sha(path) != original["sha256"]:
            raise AssertionError("vent source changed during baseline")
        with Image.open(path) as opened:
            rgba = opened.convert("RGBA")
        if list(rgba.size) != original["size_px"]:
            raise AssertionError("vent source dimensions differ from reviewed evidence")
        width, height = rgba.size
        labelled, _ = label(np.asarray(rgba)[:, :, 3] < original["alpha_threshold"])
        measured = []
        for index, bounds in enumerate(find_objects(labelled), 1):
            if bounds is None:
                continue
            ys, xs = bounds
            if .4 * width < xs.start < .6 * width and .3 * height < ys.start < .4 * height and ys.stop - ys.start > 1:
                measured.append({"bbox_xyxy": [xs.start, ys.start, xs.stop, ys.stop],
                                 "transparent_pixels": int(np.count_nonzero(labelled[bounds] == index))})
        if measured != original["components"] or len(measured) != 2:
            raise AssertionError("central vent pixel measurements no longer reproduce reviewed evidence")
        area = sum(item["transparent_pixels"] for item in measured)
        if area != original["combined_area_px"]:
            raise AssertionError("central vent area differs from reviewed evidence")
        white = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
        normalized = white.resize((normal_width, round(height * normal_width / width)), Image.Resampling.LANCZOS)
        crop_box = [round(common_crop[0] * normalized.width), round(common_crop[1] * normalized.height),
                    round(common_crop[2] * normalized.width), round(common_crop[3] * normalized.height)]
        panels.append(normalized.crop(crop_box))
        records[checkpoint] = {**deepcopy(original), "checkpoint": checkpoint,
                               "normalized_image_size_px": list(normalized.size), "normalized_crop_xyxy": crop_box,
                               "width_normalized_area_rechecked": area / width ** 2}
    reduction = 1 - records["c0021"]["width_normalized_area_rechecked"] / records["c0008"]["width_normalized_area_rechecked"]
    if abs(reduction - evidence["apparent_area_reduction_fraction"]) > 1e-12:
        raise AssertionError("width-normalized vent reduction differs from reviewed evidence")

    def font(size):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
        except OSError:
            return ImageFont.load_default(size=size)

    margin, gap, panel_width = 30, 30, panels[0].width
    panel_top = 152
    panel_height = max(panel.height for panel in panels)
    figure = Image.new("RGB", (2 * panel_width + 2 * margin + gap, panel_top + panel_height + 174), "white")
    draw = ImageDraw.Draw(figure)
    draw.text((margin, 18), "Run 1 central vents: earlier and final checkpoints", font=font(32), fill="black")
    draw.text((margin, 64), "Same image-width normalization and common crop; original rendered pixels on white.",
              font=font(23), fill="#333333")
    for index, (checkpoint, panel) in enumerate(zip(("c0008", "c0021"), panels)):
        x = margin + index * (panel_width + gap)
        rec = records[checkpoint]
        draw.text((x, 103), f"{'Before' if index == 0 else 'After'}: run 1 {checkpoint}", font=font(26), fill="black")
        draw.text((x, 134), f"Source {rec['size_px'][0]} x {rec['size_px'][1]} px", font=font(17), fill="#333333")
        figure.paste(panel, (x, panel_top))
        draw.text((x, panel_top + panel_height + 12),
                  f"A / image width² = {rec['width_normalized_area_rechecked']:.8f}", font=font(23), fill="black")
    y = panel_top + panel_height + 54
    draw.text((margin, y), f"{reduction * 100:.1f}% reduction in width-normalized apparent opening area.", font=font(27), fill="black")
    draw.text((margin, y + 40), "Different render resolutions and antialiasing; same frozen camera basis.", font=font(23), fill="#333333")
    draw.text((margin, y + 74), "Rendered apparent aperture only: not physical area, and not a claim that the vents sealed.",
              font=font(23), fill="#333333")
    target = out / "vent-before-after.png"
    figure.save(target)
    report = {"figure": str(target), "figure_sha256": sha(target), "source_evidence": str(VENT_EVIDENCE),
              "source_evidence_sha256": sha(VENT_EVIDENCE), "sources": records,
              "normalization": {"full_image_width_px": normal_width, "common_fractional_crop_xyxy": common_crop,
                                "background": "white alpha composite", "resampling": "PIL Lanczos"},
              "method": evidence["method"], "apparent_area_reduction_fraction": reduction,
              "limitation": evidence["limitation"], "product_geometry_or_material_edited": False,
              "original_source_files_modified": False,
              "operations": ["white alpha composite", "uniform resize by source width", "common crop", "external labels"]}
    write(out / "vent-before-after.json", report)
    return report


class StaticAssetSession:
    """Only identities and a local instrument-call journal; never an invented Blender session."""

    def __init__(self, root, asset):
        self.root, self.asset, self.calls = root, asset, []
        self.head = {"id": "immutable-glb-" + ACCEPTED_SHA256[:16],
                     "fingerprint": "asset-sha256:" + ACCEPTED_SHA256}

    def new_call_id(self, name, call_id=None):
        return call_id or f"baseline-{len(self.calls) + 1}-{name}"

    def record_call(self, tool, call_id, arguments, result):
        self.calls.append({"tool": tool, "call_id": call_id, "arguments": arguments, "result": result})
        write(self.root / "instrument-calls.json", self.calls)

    def call_read(self, *args, **kwargs):
        raise AssertionError("immutable-asset baseline cannot call a Blender worker")


def static_seeing(session):
    from glasses3d.agent.seeing import Seeing

    class ExistingAssetSeeing(Seeing):
        def export_head(self, call_id=None):
            if sha(session.asset) != ACCEPTED_SHA256:
                raise AssertionError("the copied accepted GLB changed")
            return {"status": "exported", "path": str(session.asset), "sha256": ACCEPTED_SHA256,
                    "bytes": session.asset.stat().st_size, **self.head,
                    "checkpoint": self.head["id"], "identity_kind": "immutable_existing_glb",
                    "verification": "SHA-256 byte identity only; no Blender checkpoint or re-export",
                    "source_asset": str(ACCEPTED)}

        def bounds(self, call_id):
            mesh = self.fit_mesh(str(session.asset), ACCEPTED_SHA256)
            return {"origin_mm": [0, 0, 0], "lo": mesh.V.min(axis=0).tolist(),
                    "hi": mesh.V.max(axis=0).tolist(), "identity_kind": "immutable_glb_coordinates"}

    return ExistingAssetSeeing(session)


def run(out, requested_workers=None):
    from glasses3d.agent.tools import fit_workers, free_commit_bytes, single_threaded_blas
    from glasses3d.agent.tools import arview
    from glasses3d.g3d_host import compare as comparison, seat_check

    out = Path(out).resolve()
    if out.exists() or out.is_relative_to(JOB) or out.is_relative_to(ACCEPTED.parent):
        raise ValueError("choose a new evidence directory outside the original job and accepted asset")
    job_path = JOB / "job.json"
    job = json.loads(job_path.read_text(encoding="utf8"))
    if len(job["images"]) != 6 or any(row["matte_mode"] != "open_frame" for row in job["images"]):
        raise AssertionError("expected all six authoritative photos with the recorded open_frame matte")
    sources = {str(ACCEPTED): sha(ACCEPTED), str(job_path): sha(job_path)}
    if sources[str(ACCEPTED)] != ACCEPTED_SHA256:
        raise AssertionError("accepted asset differs from the owner's bound SHA-256")
    for row in job["images"]:
        photo = (JOB / row["file"]).resolve()
        if not photo.is_relative_to(JOB / "images") or sha(photo) != row["sha256"]:
            raise AssertionError("source reference path/hash differs from job.json")
        sources[str(photo)] = row["sha256"]
    vent_evidence = load_vent_sources(sources)
    # The run's last all-six comparison was c0008; later comparison rows cover a subset.
    # Preserve checkpoint labels rather than presenting stale rows as final-model metrics.
    run1_reports = []
    for path in sorted((JOB / "work/compare").glob("*/report.json")):
        report = json.loads(path.read_text(encoding="utf8"))
        if report.get("schema") == "g3d.compare/1":
            sources[str(path)] = sha(path)
            run1_reports.append((report, path))
    run1_by_ref = {}
    for report, path in sorted(run1_reports, key=lambda pair: pair[0].get("created", "")):
        for rid, row in report["refs"].items():
            run1_by_ref[rid] = {"checkpoint": report["checkpoint"], "call_id": report["call_id"],
                               "created": report["created"], "glb_sha256": report["glb"]["sha256"],
                               "report": str(path), "metrics_all": deepcopy(row["all"])}
    out.mkdir(parents=True)
    (out / "work/exports").mkdir(parents=True)
    (out / "images").mkdir()
    asset = out / "work/exports/accepted.glb"
    shutil.copyfile(ACCEPTED, asset)
    images, views, candidates, mattes = {}, {}, {}, {}
    for row in job["images"]:
        target = out / row["file"]
        shutil.copyfile(JOB / row["file"], target)
        if sha(target) != row["sha256"]:
            raise AssertionError("reference copy differs from original")
        name = row["original_name"]
        images[name] = target
        views[name] = row["fit_kinds"][0]
        candidates[name] = tuple(row["fit_kinds"])
        mattes[name] = row["matte_mode"]
    write(out / "input-receipt.json", {"asset_sha256": ACCEPTED_SHA256, "source_hashes": sources,
                                      "photos": job["images"], "identity_kind": "immutable_existing_glb"})
    session = StaticAssetSession(out, asset)
    seeing = static_seeing(session)
    report = {"schema": "g3d.accepted_asset_baseline/1", "source_asset": str(ACCEPTED),
              "asset_sha256": ACCEPTED_SHA256, "identity_kind": "immutable_existing_glb",
              "blender_checkpoint_verified": False, "imported_or_reexported": False,
              "model_requests": 0, "actual_spend_usd": 0, "stages": {},
              "notes": ["Real Seeing camera fit and contour comparison on exact copied bytes.",
                        "Empty native/clay passes avoid a Blender scene; AR supplies material renders.",
                        "Run-1 rows use their latest available comparison, with per-row checkpoints.",
                        "Explicit semi_rimless seating is a diagnostic override, not the default gate verdict."]}

    def stage(name, action):
        started = time.monotonic()
        print(f"START {name} {datetime.now(timezone.utc).isoformat()}", flush=True)
        result = action()
        report["stages"][name] = {"seconds": time.monotonic() - started, "result": result}
        write(out / f"{name}.json", result)
        write(out / "proof.json", report)
        print(f"END {name} {report['stages'][name]['seconds']:.2f}s status={result.get('status', result.get('ok'))}", flush=True)
        return result

    started = time.monotonic()
    try:
        report["free_commit_bytes_before_fit"] = free_commit_bytes()
        report["fit_workers"] = fit_workers(requested_workers)
        with single_threaded_blas():
            fit = stage("fit", lambda: seeing.fit_cameras(
                "Baseline current instruments on the owner's accepted immutable Oakley GLB; no geometry edits.",
                images, views, refs="all", workers=report["fit_workers"], candidates=candidates,
                matte_modes=mattes, call_id="baseline-fit", log=lambda *args: print(*args, flush=True)))
        # Normal contour-only comparison measures every usable target camera and retains its unusable_refs.
        # The six-row table below also retains every refused camera rather than silently omitting that photo.
        compared = stage("compare", lambda: seeing.compare_to_photos(refs="all", parts="all", passes=[],
            basis=fit.get("basis"), call_id="baseline-compare"))
        ar = stage("arview", lambda: arview.capture(out, seeing.export_head(), "baseline-arview"))
        # The independently identified construction diagnostic is fixed before the default gate is read;
        # it is not a parameter search or a replacement verdict. The default check is the last instrument call.
        explicit = stage("seat-explicit-semi-rimless", lambda: seat_check.check_glb(
            asset, frame_type="semi_rimless", stations_table=True))
        default = stage("seat-default", lambda: seat_check.check_glb(asset, stations_table=True))
        table = []
        for row in job["images"]:
            rid = comparison.ref_id(row["original_name"])
            table.append({"ref": rid, "view_kind": row["view_kind"], "side": row["side"],
                          "fit_kinds": row["fit_kinds"], "matte_mode": row["matte_mode"],
                          "accepted_fit": fit.get("refs", {}).get(rid),
                          "accepted_compare": compared.get("refs", {}).get(rid),
                          "accepted_compare_refusal": (compared.get("unusable_refs", {}).get(rid)
                                                       or fit.get("unusable_refs", {}).get(rid)),
                          "run1_latest_available": run1_by_ref.get(rid)})
        report.update(per_photo=table, default_seating_passes=default.get("ok") is True,
                      explicit_semi_rimless_diagnostic_passes=explicit.get("ok") is True,
                      owner_gate_decision_required=default.get("ok") is not True,
                      status="owner_gate_decision_required" if default.get("ok") is not True else "completed")
        report["instrument_errors"] = [{"stage": name, "status": result.get("status"),
                                        "code": result.get("code"), "message": result.get("message")}
                                       for name, result in (("fit", fit), ("compare", compared), ("arview", ar))
                                       if result.get("status") != "ok"]
        if report["instrument_errors"] and report["status"] == "completed":
            report["status"] = "instrument_errors"
        write(out / "per-photo.json", table)
        report["vent_comparison"] = vent_comparison(out, vent_evidence)
    finally:
        report["seconds"] = time.monotonic() - started
        report["source_hashes"] = sources
        report["sources_unchanged"] = all(sha(path) == digest for path, digest in sources.items())
        report["copied_asset_unchanged"] = sha(asset) == ACCEPTED_SHA256
        report["copied_photos_unchanged"] = all(sha(out / row["file"]) == row["sha256"] for row in job["images"])
        write(out / "proof.json", report)
        if not (report["sources_unchanged"] and report["copied_asset_unchanged"] and report["copied_photos_unchanged"]):
            raise AssertionError("an immutable baseline input changed")
    print(json.dumps({key: report[key] for key in ("status", "default_seating_passes",
        "explicit_semi_rimless_diagnostic_passes", "owner_gate_decision_required", "sources_unchanged", "seconds")}, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int)
    args = parser.parse_args()
    os.environ["G3D_OFFLINE"] = "1"
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        run(args.out, args.workers)
    assert not network.attempts and not clients.constructions


if __name__ == "__main__":
    main()
