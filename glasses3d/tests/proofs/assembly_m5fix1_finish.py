"""Real $0 finalization, including seating reports, two-call visual review and immutable delivery."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import time

from agents.tool import ToolOutputImage
from agents.tool_context import ToolContext
from PIL import Image

from glasses3d.agent import delivery, jobs, procs, tool_reports
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.tools import build_tools
from glasses3d.tests.proofs.assembly_m1 import build_code


def run(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    photos = out / "photos"
    photos.mkdir()
    Image.new("RGB", (16, 16), "white").save(photos / "front.png")
    views = out / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": "both"}}))
    root = jobs.create_job(images=photos, views=views, name="Finish procedure proof",
                          description="Synthetic fixture; no product or model inference.", jobs_root=out / "jobs")
    scene = JobScene(root)
    tools = {tool.name: tool for tool in build_tools(scene)}
    calls = []

    def invoke(name, args):
        cid = f"proof-{len(calls) + 1}-{name}"
        raw = json.dumps(args)
        context = ToolContext(context=None, tool_name=name, tool_call_id=cid, tool_arguments=raw)
        output = asyncio.run(tools[name].on_invoke_tool(context, raw))
        result = json.loads(output[0].text)
        summary, pages = result, 0
        if result.get("paged"):
            # Assert the lossless diagnostics, not a sampled table in the bounded model-facing summary.
            chunks, offset = [], 0
            while True:
                page = tool_reports.read_report(root, summary["full_report"]["id"], mode="text",
                                                offset=offset, limit=8000)
                assert page["status"] == "ok"
                chunks.append(page["text"])
                pages += 1
                if page["next_offset"] is None:
                    break
                assert page["next_offset"] > offset
                offset = page["next_offset"]
            result = json.loads("".join(chunks))
        image_count = sum(isinstance(item, ToolOutputImage) for item in output)
        calls.append({"tool": name, "result": result, "images": image_count,
                      "bounded_summary": summary, "lossless_pages": pages})
        procs.write_json_atomic(out / "calls.json", calls)
        return result, image_count

    started = time.monotonic()
    try:
        scene.start()
        built = scene.s.execute(build_code(), label="synthetic fixture")
        assert built["status"] == "ok", built
        declared, _ = invoke("execute_blender_code", {"code": "import bpy\nfor side in (-1, 1):\n"
            "    lens = bpy.data.objects[f'Lens_{side}']\n"
            "    lens['g3d_seat'] = '{\"frame_type\":\"acetate\"}'\n"
            "    for v in lens.data.vertices:\n"
            "        v.co.x = side * 32 + (v.co.x - side * 32) * 0.95\n"
            "        v.co.y = -7 + (v.co.y + 7) * 0.95\n"
            "    lens.data.update()\n",
            "label": "declared fixture with lens radius below the rim aperture"})
        assert declared["status"] == "ok", declared
        unseated, images = invoke("finish", {"summary": "Unseated fixture must retain the failed seating report."})
        assert unseated["status"] == "review_required" and scene.finish is None, unseated
        assert not unseated["export"]["seat_check"]["ok"] and not unseated["failures"], unseated
        assert all(lens["frame_type_source"] == "g3d_seat"
                   for lens in unseated["export"]["seat_check"]["lenses"])
        assert images == 1 and unseated["image_source"] == "arview", unseated
        for side in (-1, 1):
            seated, _ = invoke("seat_lens", {"lens": f"Lens_{side}", "frame_type": "acetate"})
            assert seated["status"] == "ok", seated
            report = seated["seating_repair_report"]
            assert report["status"] == "reported" and report["gate"] is False, report
            assert report["before"]["checkpoint"] != report["after"]["checkpoint"]
            assert len(seated["images"][0]["panels"]) == 2
        # A manual support rebuild was the Oakley failure path. Exercise that path on synthetic geometry only.
        for delta in (-10, 10):
            manual, shown = invoke("execute_blender_code", {"code":
                f"import bpy\nbpy.data.objects['Front_2'].location.y += {delta}\n",
                "label": "synthetic support displacement and restoration", "seating_repair": True})
            assert manual["status"] == "ok", manual
            report = manual["seating_repair_report"]
            assert report["status"] == "reported" and report["gate"] is False, report
            assert report["before"]["source_render_sha256"] != report["after"]["source_render_sha256"]
            assert shown == 1
        scene.last_ar = {"status": "ok", "exit_code": 0, "export_sha256": "stale"}
        evidence, _ = invoke("finish", {"summary": "Synthetic frame and lenses ready for visual review."})
        assert evidence["status"] == "review_required" and scene.finish is None, evidence
        accepted, images = invoke("finish", {"summary": "Synthetic AR reviewed; no usable photo camera in this proof.",
            "review_call_id": evidence["call_id"], "per_photo": []})
        assert accepted["status"] == "accepted" and not accepted["failures"], accepted
        assert accepted["arview"]["fixed_scale"] is True
        assert images == 1 or (images == 0 and all(rec.get("reused_from_call") in {unseated["call_id"], evidence["call_id"]}
                                                  for rec in accepted["images"]))
        assert accepted["arview"]["images"] == accepted["images"]
        assert scene.finish is accepted or scene.finish == accepted
        result = {"status": "completed", "run_id": "offline-finalize", "exit_checkpoint": scene.exit_checkpoint(),
                  "budget": {"settled_usd": "0.000000", "requests": 0}}
    finally:
        scene.close()
    delivered = delivery.deliver_finished(root, scene, result)
    assert delivered["status"] == "delivered", delivered
    saved = delivery.load_delivery(root)
    assert saved["report_data"]["accepted_finish"] is True
    proof = {"status": "passed", "job": str(root), "delivery": delivered, "calls": [x["tool"] for x in calls],
             "no_separate_export_or_ar_calls": True, "failed_seating_has_images": True,
             "both_seating_repairs_have_frozen_front_reports": True,
             "manual_support_repair_and_restoration_reported": True,
             "declaration_alone_does_not_pass_unseated_geometry": True,
             "model_requests": 0, "actual_spend_usd": 0, "seconds": time.monotonic() - started}
    procs.write_json_atomic(out / "proof.json", proof)
    print(json.dumps(proof, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    os.environ["G3D_OFFLINE"] = "1"
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        run(args.out)
    assert not network.attempts and not clients.constructions
