"""Read-only, frozen-front evidence around seating repairs; never a likeness gate."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from PIL import Image

from ...g3d_host import compare as C
from ..seeing import RENDER_DEADLINE_S
from ..procs import write_json_atomic

PRESERVE_FEATURES = (
    "A seating repair must preserve every opening and feature visible in the photos, including vents. "
    "Inspect the front before and after at this frozen camera; repair lost features before continuing. "
    "This is a visual report, not a new gate. The geometric seating check remains required for finish."
)


def repair_report(scene, cid, action, fatal):
    """Bracket a repair with the same camera, image size and light frame, even if bounds change.

    Rendering failures are reported without replacing the mutation result. Integrity/worker failures retain
    their normal fatal behavior. The action runs only once; its normal journal and rollback semantics apply.
    """
    report = {"status": "unavailable", "guidance": PRESERVE_FEATURES, "gate": False, "warnings": []}
    images = []
    params = None
    output_dir = scene.root / "work" / "seating_reports" / hashlib.sha256(cid.encode()).hexdigest()[:24]

    def capture(phase):
        folder = output_dir / phase
        folder.mkdir(parents=True, exist_ok=True)
        request = {**deepcopy(params), "out_dir": str(folder)}
        out = scene.s.call_read("seating_front_report", "render", request,
                                deadline_s=RENDER_DEADLINE_S, call_id=f"{cid}-front-{phase}")
        if out.get("status") != "ok" or out.get("restored_to") is not None:
            raise ValueError(f"{phase} render: {out.get('message') or out.get('code') or out.get('status')}")
        source = Path(out["result"]["views"][0]["files"]["native"]["path"])
        # Composite alpha onto white so openings stay visible in the compact tool preview.
        with Image.open(source) as opened:
            rgba = opened.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            white.alpha_composite(rgba)
            path = folder / "front-white.png"
            white.convert("RGB").save(path)
        record = {"path": str(path), "view": f"front {phase} seating repair (same frozen camera)",
                  "checkpoint": scene.s.head["id"], "fingerprint": scene.s.head["fingerprint"],
                  "source_render": str(source), "source_render_sha256": C.sha256_file(source)}
        report[phase] = record
        images.append(record)

    try:
        try:
            basis = C.load_basis(scene.root)
            fronts = [Path(row["original_name"]).stem for row in scene.job.get("images", [])
                      if row.get("view_kind") == "front"]
            ref = next((rid for rid in fronts if rid in basis.refs and basis.is_usable(rid)), None)
            if ref is None:
                raise ValueError("no usable declared front photo in the current basis")
            view = scene.seeing.resolve_views(["reference:" + ref], 1536, None, basis.id,
                                              call_id=f"{cid}-front-camera")[0]
            report.update(camera_source="frozen_photo", basis=basis.id, ref=ref)
        except (C.NoBasis, ValueError) as error:
            report["warnings"].append(f"Photo front unavailable: {error}; using a front camera frozen before repair.")
            view = scene.seeing.resolve_views(["front"], 1536, None, None, call_id=f"{cid}-front-camera")[0]
            report.update(camera_source="frozen_canonical", basis=None, ref=None)
        params = {"engine": "auto", "samples": 16, "light_frame": scene.seeing._light_frame(cid),
                  "views": [{"name": "front", "camera": deepcopy(view["blender"]), "passes": ["native"]}]}
        report["render_parameters"] = deepcopy(params)
        report["camera_sha256"] = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        capture("before")
    except fatal:
        raise
    except Exception as error:
        report["warnings"].append(f"Before report unavailable: {type(error).__name__}: {error}")
    result = action()
    try:
        if params is not None:
            capture("after")
        report["status"] = "reported" if "before" in report and "after" in report else "incomplete"
    except fatal:
        raise
    except Exception as error:
        report.update(status="incomplete")
        report["warnings"].append(f"After report unavailable: {type(error).__name__}: {error}")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(output_dir / "report.json", report)
    except (OSError, ValueError, TypeError) as error:
        report["warnings"].append(f"Report file unavailable: {type(error).__name__}: {error}")
    return {**result, "seating_repair_report": report, "images": [*result.get("images", []), *images]}
