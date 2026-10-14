"""$0 nested temple-stage proof on a COPY of a second product (FT1119).

All edits and judgements are arbitrary SCRIPTED CONTROLS, never product output or an owner verdict. The ordinary
parent run invokes temple_stage; its real facet tools compare four distinct heads and three variants, finish, apply
to the captured parent, and return to the parent SDK session. No provider transport can leave loopback.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from glasses3d.agent import approvals as A, job_run, jobs, replay as R
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.loop import run_astra
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.procs import write_json_atomic
from glasses3d.agent.temple_lab import state as S
from glasses3d.agent.temple_lab.lab import Lab
from glasses3d.g3d_host import compare as C, glb
from glasses3d.g3d_host.fit_views import rigid_fit_marker

REPO = Path(__file__).resolve().parents[3]
G3D = REPO / "glasses3d"
SOURCE = REPO / "automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29/original/scene.blend"
REFERENCE = G3D / "data/reference/ft1119"
CAMERAS = G3D / "data/wp3b/ft1119/cameras.json"
MODEL_SHA = "af6e9d100fcf7a30ee3862884bff6418f7166a7395c34834559c2da6fcad6cd7"
CAMERAS_SHA = "a82232f90bdc5b798139435b414c4dea523521a99a2b124f0ad145d11d9de29d"
LABEL = "SCRIPTED OFFLINE STAGE CONTROL; NOT PRODUCT OUTPUT"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def unchanged_parts(path):
    asset = glb.load(path)
    rows = {}
    for part in asset.primitives:
        name = part.node_name
        if "temple" in name.lower() or any(tag in name for tag in ("inner_brand_print", "medallion", "TOM_FORD")):
            continue  # temple geometry and attached temple markings may move together
        material = asset.materials[part.material_index] if part.material_index is not None else None
        raw = asset.doc["meshes"][part.mesh_index]["primitives"][part.primitive_index]
        digest = hashlib.sha256(part.positions.tobytes() + part.triangles.tobytes() + part.world.tobytes())
        for attribute, accessor in sorted(raw["attributes"].items()):
            digest.update(attribute.encode())
            digest.update(asset.accessor(accessor).tobytes())
        digest.update(json.dumps({"material": material, "extras": asset.nodes[part.node_index].get("extras")},
                                 sort_keys=True).encode())
        rows[name, part.primitive_index] = digest.hexdigest()
    assert rows
    return rows


def run():
    disable_sdk_tracing()
    started = time.monotonic()
    work = G3D / "data/assembly" / ("m4b-" + uuid4().hex[:8])
    work.mkdir(parents=True)
    sources = {str(path): A.file_sha256(path) for path in (SOURCE, CAMERAS, REFERENCE / "model.glb")}
    assert sources[str(REFERENCE / "model.glb")] == MODEL_SHA == read(CAMERAS)["model"]["sha256"]
    assert sources[str(CAMERAS)] == CAMERAS_SHA
    cameras = read(CAMERAS)
    for name, record in cameras["images"].items():
        path = REFERENCE / name
        sources[str(path)] = A.file_sha256(path)
        assert sources[str(path)] == record["image_sha256"]
    views = {name: {"kind": rec["view_kind"], "side": rec.get("side")} for name, rec in cameras["images"].items()}
    write_json_atomic(work / "views.json", views)
    root = jobs.create_job(images=REFERENCE, views=work / "views.json", name="Offline FT1119 stage copy",
                           description=LABEL, jobs_root=work / "jobs")
    scene = JobScene(root, job=jobs.load_job(root))
    scene.seed_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, scene.seed_path)
    before = None
    try:
        scene.start()
        # A non-temple edit AFTER the original was loaded proves package/apply use this current parent head.
        witness = scene.s.execute("import bpy\nfor ob in bpy.data.objects:\n"
            "    if ob.type == 'MESH' and ob.name == 'Saddle_bridge':\n"
            "        ob.location.y += 0.125\n", label="parent-head preservation witness")
        assert witness["status"] == "ok" and witness.get("scene_delta"), witness
        exp = scene.seeing.export_head()
        assert exp["validate_spec"]["ok"], exp
        before = unchanged_parts(exp["path"])
        captured = dict(scene.s.head)
        # Test-data migration of this EXACT historical rigid WP3 fit. That fitter rendered the authored GLB with
        # no per-photo temple rotation. The marker records its known zero DELTA; it does not measure an opening.
        # Runtime never recognizes this product/hash or fills arbitrary missing pose records.
        for rec in cameras["images"].values():
            rec["rigid_fit"] = rigid_fit_marker(MODEL_SHA, rec["image_sha256"], rec["camera"])
        write_json_atomic(work / "rigid-cameras.json", cameras)
        C.import_cameras_json(root, work / "rigid-cameras.json", jobs.image_paths(root),
                              reason="SHA-pinned historical rigid WP3 fit; authored pose, no measured opening")
        settings = replace(job_run.settings_default(), max_turns=9)
        spec, _ = job_run.prepare_run(root, "offline-nested", settings=settings, write_request=True, scene=scene)
        A.write_approval(root / "runs" / spec.run_id, max_usd="23",
                         owner_words="Synthetic offline proof only; no paid call authorized.", recorded_by="offline proof")
        transport = R.ScriptedOpenAI([])
        stage_dir = root / "work/temple_stage" / hashlib.sha256(b"parent-stage").hexdigest()[:16]

        def lab_now():
            return Lab(stage_dir)  # read persisted state; never starts another worker

        def judgement(lab):
            pending = S.pending_compare(lab.state)
            if pending is None:
                return None
            return {"compare_id": pending["compare_id"], "versus_compare_id": None, "overall": "mixed",
                    "photos": [{"photo": p, "verdict": "mixed", "differences": []} for p in pending["photos"]],
                    "best_checkpoint": lab.s.head["id"]}

        def offsets(lab, amount):
            base = lab.design(0)["base"]
            return [{"op": "offset_chain", "chain": "base." + name,
                     "d_from_mm": base[name][0][0], "d_to_mm": base[name][-1][0],
                     "delta_mm": amount, "taper_mm": 0.0} for name in ("top", "bottom")]

        def reply(index):
            if index == 0:
                return R.tool_call("temple_stage", {}, call_id="parent-stage")
            if index == 8:
                return R.tool_call("scene_summary", {"objects": None}, call_id="parent-after-stage")
            lab = lab_now()
            if index == 1:
                return R.tool_call("facet_inspect", {}, call_id="child-inspect")
            if index == 2:
                return R.tool_call("photo_compare", {"photos": lab.compare_ids(), "crop": "full", "zoom": None,
                                                     "points_px": None}, call_id="child-compare")
            if index in (3, 4, 5):
                return R.tool_call("facet_edit", {"parent_revision": 0, "reset_layout": False,
                    "edits": offsets(lab, .4 * (index - 2)), "judgement": judgement(lab), "note": LABEL,
                    "object": "Scripted control: plain temple outer surface, uniformly offset; no new facet layout.",
                    "apply": True, "photos": lab.compare_ids()}, call_id=f"child-edit-{index - 2}")
            if index == 6:
                side = next(r["id"] for r in lab.manifest["photos"] if r["view"] == "side")
                return R.tool_call("facet_variants", {"parent_revision": 0, "variants": [
                    # Three alternatives distinct from one another AND the current +1.2 mm head.
                    {"label": f"control-{k}", "edits": offsets(lab, .4 * k)} for k in (1, 2, 4)],
                    "photos": [side], "judgement": judgement(lab)}, call_id="child-variants")
            assert index == 7
            return R.tool_call("facet_finish", {"status": "matched", "best_checkpoint": lab.s.head["id"],
                    "summary": LABEL, "judgement": judgement(lab)}, call_id="child-finish")

        def handler(request):
            if request.url.path == R.COUNT_PATH and len(transport.replies) == len(transport.counts):
                assert len(transport.replies) < 9, "unexpected extra inference"
                transport.replies.append(reply(len(transport.replies)))
            return transport.handle(request)

        spec.offline_handler, spec.offline_api_key = handler, "sk-offline-stage-proof"
        with LoopbackOnly() as network, ProductionTransportTripwire() as tripwire:
            result = asyncio.run(run_astra(spec, scene=scene))
        assert result["status"] == "turn_limit", result  # the controlled parent stops after its post-stage inspection
        assert result["requests"] == result["turns_used"] == 9
        assert not transport.violations and not transport.unexpected
        assert result["budget"]["unknown_micro_usd"] == result["budget"]["held_micro_usd"] == 0
        application = read(stage_dir / "application.json")
        assert application["status"] == "ok" and application["source"]["checkpoint"] == captured["id"]
        assert application["source"]["fingerprint"] == captured["fingerprint"]
        parent_after = next(row for row in scene.journal_entries() if row.get("call_id") == "parent-after-stage")
        assert parent_after["status"] == "ok" and not parent_after["mutating"], parent_after
        finished_lab = lab_now()
        heads = S.distinct_compared_heads(finished_lab.state, finished_lab.distinct)
        variants = next(row for row in S.seen_variants(finished_lab.state) if row.get("spread"))
        assert len(heads) >= 4 and len(variants["spread"]) == 3
        assert result["exit_checkpoint"]["verified"] and result["exit_checkpoint"]["id"] != captured["id"]
        scene.start()
        after = scene.seeing.export_head()
        assert after["validate_spec"]["ok"] and unchanged_parts(after["path"]) == before
        assert {path: A.file_sha256(path) for path in sources} == sources
        assert len(list(root.glob("runs/*/budget.json"))) == 1
        assert not list(stage_dir.glob("runs/*/budget.json"))
        report = {"status": "passed", "label": LABEL, "job": str(root), "application": str(stage_dir / "application.json"),
                  "requests": 9, "child_requests": 7, "single_approval_and_ledger": True,
                  "distinct_compared_heads": heads, "variant_spread": variants["spread"],
                  "parent_resumed_and_inspected": True,
                  "current_parent_preserved": True, "source_files_unchanged": True, "actual_spend_usd": 0,
                  "seconds": time.monotonic() - started}
        write_json_atomic(work / "proof.json", report)
        print(json.dumps(report, indent=2))
        return report
    finally:
        scene.close()


if __name__ == "__main__":
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    run()
