"""$0 full photo_print tool proof on a copied FT1119 scene and immutable source photos.

This is an assembly tool test, never a product delivery or appearance verdict. The separately pinned historical
recipe proves accepted texture compatibility; this run uses current scene geometry with explicit frozen cameras.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from agents.tool_context import ToolContext

from glasses3d.agent import jobs, tools as T
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.procs import write_json_atomic
from glasses3d.g3d_host import compare as C
from glasses3d.tests.instruments import print_metrics as PM
from glasses3d.tests.proofs.assembly_m4b import SOURCE, REFERENCE, CAMERAS, MODEL_SHA, CAMERAS_SHA

G3D = Path(__file__).resolve().parents[2]
CALIBRATION = G3D / "data/wp5/ar_calibration/calibration.json"
PARTS = ["Brow_Left", "Brow_Right", "Temple_Left", "Temple_Right"]
LABEL = "OFFLINE PHOTO PRINT TOOL TEST; NOT PRODUCT OUTPUT"


def run():
    disable_sdk_tracing()
    started = time.monotonic()
    work = G3D / "data/assembly" / ("m4c-" + uuid4().hex[:8])
    work.mkdir(parents=True)
    cameras = json.loads(CAMERAS.read_bytes())
    assert C.sha256_file(CAMERAS) == CAMERAS_SHA
    assert C.sha256_file(REFERENCE / "model.glb") == MODEL_SHA
    sources = {str(p): C.sha256_file(p) for p in (SOURCE, CAMERAS, CALIBRATION, REFERENCE / "model.glb")}
    views = {}
    for name, record in cameras["images"].items():
        p = REFERENCE / name
        sources[str(p)] = C.sha256_file(p)
        assert sources[str(p)] == record["image_sha256"]
        views[name] = {"kind": record["view_kind"], "side": record.get("side")}
    write_json_atomic(work / "views.json", views)
    root = jobs.create_job(images=REFERENCE, views=work / "views.json", name="Offline FT1119 print copy",
                           description=LABEL, jobs_root=work / "jobs")
    scene = JobScene(root, job=jobs.load_job(root))
    scene.seed_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, scene.seed_path)
    (root / "inputs").mkdir(exist_ok=True)
    shutil.copyfile(CALIBRATION, root / "inputs/calibration.json")
    try:
        scene.start()
        before = dict(scene.s.head)
        original = scene.seeing.export_head()
        assert original["validate_spec"]["ok"]
        basis = C.import_cameras_json(root, CAMERAS, jobs.image_paths(root), reason="offline print tool control")
        tool = next(t for t in T.build_tools(scene) if t.name == "photo_print")
        args = json.dumps({"parts": PARTS, "basis": basis.id,
                           "calibration": "inputs/calibration.json", "registration_report": None})
        ctx = ToolContext(context=None, tool_name="photo_print", tool_call_id="full-print-control",
                          tool_arguments=args)
        with LoopbackOnly(), ProductionTransportTripwire():
            outputs = asyncio.run(tool.on_invoke_tool(ctx, args))
        result = json.loads(outputs[0].text)
        write_json_atomic(work / "tool-result.json", result)
        assert result["status"] == "ok", result
        assert result["print_check"]["ok"] and result["export"]["validate_spec"]["ok"]
        assert result["export"]["print_check"]["ok"] and result["export"]["print_check"]["status"] == "verified"
        assert result["export"]["print_check"]["glb_sha256"] == result["export"]["sha256"]
        assert result["production"]["quality_proof"]["status"] == "not_run"
        assert scene.s.head["id"] != before["id"]
        invariant = PM.a6_round_trip(Path(original["path"]), Path(result["export"]["path"]), result["texture_parts"],
                                    print_png=Path(result["bake"]) / "print.png", allow_rename=True)
        assert invariant["pass"], invariant
        entry = next(r for r in scene.journal_entries() if r.get("call_id") == "full-print-control")
        assert entry["status"] == "ok" and entry["mutating"]
        detached = scene.s.call_mutating("detach_print", "g3d_call",
            {"op": "detach_print", "kwargs": {"parts": PARTS}}, deadline_s=600, call_id="proof-detach")
        assert detached["status"] == "ok", detached
        uv_export = scene.seeing.export_head()
        assert uv_export["print_check"]["ok"] and uv_export["print_check"]["status"] == "not_required"
        uv_invariance = PM.print_uv_invariance(Path(original["path"]), Path(uv_export["path"]), PARTS)
        assert uv_invariance["pass"], uv_invariance
        material_control = PM.b5_material_control(Path(uv_export["path"]), Path(result["export"]["path"]),
                                                  result["texture_parts"], allow_rename=True)
        assert material_control["pass"], material_control
        restored = scene.s.restore_checkpoint(before["id"], call_id="proof-restore")
        assert restored["status"] == "ok" and scene.s.fingerprint() == before["fingerprint"]
        assert {p: C.sha256_file(p) for p in sources} == sources
        report = {"status": "passed", "label": LABEL, "job": str(root), "tool": str(work / "tool-result.json"),
                  "a6": invariant, "uv_invariance": uv_invariance, "b5": material_control,
                  "source_files_unchanged": True, "pre_print_checkpoint_restored": True,
                  "actual_spend_usd": 0, "seconds": time.monotonic() - started}
        write_json_atomic(work / "proof.json", report)
        print(json.dumps({k: v for k, v in report.items() if k not in {"a6", "b5", "uv_invariance"}}, indent=2))
        return report
    finally:
        scene.close()


if __name__ == "__main__":
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    run()
