"""$0 delivery/review/resume proof using the actual M1 model, immutable files and a detached AR server.

Every verdict and approval below belongs to a synthetic tool test; no product acceptance or paid call is implied.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import time

from glasses3d.agent import approvals as A, cli, delivery, job_run, procs, replay, review
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire
from glasses3d.agent.session_inputs import NOTE_PREFIX
from glasses3d.tests.proofs.assembly_m1 import run as assembly, TRUTH
from glasses3d.tests.proofs.offline_job_approval import approve_offline_request


def resume_replies(index):
    review_id = f"call_{100 * index + 2:04d}"
    difference = {"id": "resume-procedure", "refs": [Path(name).stem for name in TRUTH],
        "what_differs": "Scripted procedure proof; no automated likeness judgment.",
        "evidence_ref": review_id, "status": "limitation",
        "last_correction": "No geometry correction attempted; the resume note asks to retain the synthetic geometry.",
        "outcome": "The delivered review compares the retained synthetic model with its procedural reference images.",
        "next_action": "No further correction in this resume procedure test; product appearance remains untested.",
        "reason": "These synthetic references and scripted replies prove review-note handling, not Astra or owner product acceptance."}
    return [
        replay.tool_call("scene_summary", {"objects": None}),
        replay.tool_call("finish", {"summary": "Offline resume proof: review note received; geometry retained."}),
        replay.tool_call("finish", {"summary": "Offline resume proof: reviewed all comparison evidence.",
            "review_call_id": review_id, "per_photo": [{"ref": ref,
                "differences": difference["what_differs"], "difference_ids": [difference["id"]]}
                for ref in difference["refs"]], "difference_updates": [difference]})]


def run():
    started = time.monotonic()
    built = assembly()
    root = Path(built["job"])
    first = delivery.load_delivery(root)
    before = {name: A.file_sha256(first[name]) for name in ("model", "report", "manifest", "contact_sheet")}
    live = None
    try:
        live = review.review_job(root, port=0)
        assert live["server"]["detached"] and live["delivery_id"] == first["id"]
        notes = "  OFFLINE PROOF ONLY: keep the geometry unchanged and acknowledge this review note.\nKeep exact spacing.  "
        verdict = cli.dispatch(cli.parser().parse_args(["job", "verdict", str(root), "changes", "--notes", notes]))
        assert verdict["glb_sha256"] == first["glb_sha256"] and verdict["delivery_id"] == first["id"]
        assert verdict["notes"] == notes
        resumed = []
        for index in (1, 2):
            transport = replay.ScriptedOpenAI(resume_replies(index), id_start=100 * index + 1)
            settings = replace(job_run.settings_default(), max_turns=4)
            spec, _ = job_run.prepare_run(root, f"offline-review-resume-{index}", settings=settings,
                                           write_request=True, resume=True)
            assert len(spec.owner_notes) == 1 and spec.owner_notes[0].endswith(notes)
            approve_offline_request(root / "runs" / spec.run_id, transport.handle, cap="20")
            outcome = asyncio.run(job_run.execute_run(root, spec.run_id, resume=True, offline_handler=transport.handle))
            assert outcome["result"]["status"] == "completed" and outcome["delivery"]["status"] == "delivered", outcome
            assert len(transport.creates) == len(transport.counts) == 3
            assert not transport.violations and not transport.unexpected
            receipt = json.loads((root / "runs" / spec.run_id / "input-receipt.json").read_text())
            assert receipt["notes_sent"] == (1 if index == 1 else 0)
            for body in transport.creates:
                texts = [part["text"] for item in body["input"] if item.get("role") == "user"
                         for part in item.get("content", []) if part.get("type") == "input_text"]
                assert texts.count(NOTE_PREFIX + spec.owner_notes[0]) == 1
            current = delivery.load_delivery(root)
            assert current["id"] != first["id"] and current["glb_sha256"] == first["glb_sha256"]
            assert review.reviewed_delivery(root)["delivery_id"] == first["id"]
            resumed.append({"run_id": spec.run_id, "delivery_id": current["id"], "notes_sent": receipt["notes_sent"]})
        assert {name: A.file_sha256(first[name]) for name in before} == before
        assert review._report(live["server"]["port"])["served_sha256"] == first["glb_sha256"]
        result = {"status": "passed", "job": str(root), "first_delivery_id": first["id"],
                  "review_detach": live["server"]["method"], "verdict_glb_sha256": verdict["glb_sha256"],
                  "immutable_files_unchanged": True, "resumes": resumed,
                  "actual_spend_usd": 0, "seconds": time.monotonic() - started}
    finally:
        if live is not None:
            state = json.loads(Path(live["server"]["state_path"]).read_text())
            review._stop_owned(state)
    procs.write_json_atomic(root / "m3-proof.json", result)
    print(json.dumps(result, indent=2), flush=True)
    return result


if __name__ == "__main__":
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        run()
    assert not network.attempts and not clients.constructions
