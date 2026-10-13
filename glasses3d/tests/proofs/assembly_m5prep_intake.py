"""$0 intake proof on an exact copy of an existing job, using real Blender and scripted SDK transport.

Run with --job ABSOLUTE_EXISTING_JOB --out FRESH_DIRECTORY. The source job is read only. The copied job gets a
clearly labelled offline approval, normal instructions/tools, and one read_image per photo followed by
scene_summary and budget_status. The turn limit stops it before another model request. No product geometry is made.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from glasses3d.agent import approvals as A, brief, job_run, jobs, loop as L, procs, replay
from glasses3d.agent.budget import RunLedger
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.receipts import BlobStore


RUN_ID = "offline-intake"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def files(root):
    """Refuse links and credential files before hashing the complete source tree."""
    paths = sorted(root.rglob("*"))
    for path in paths:
        assert not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()), path
        assert not path.name.startswith(".env"), "credential files are not intake proof inputs"
    return {p.relative_to(root).as_posix(): A.file_sha256(p) for p in paths if p.is_file()}


def check_receipts(root, expected, photos, result):
    run_dir = root / "runs" / RUN_ID
    n = len(expected)
    assert result["status"] == "turn_limit" and result["requests"] == result["turns_used"] == result["tool_calls"] == n
    assert result["exit_checkpoint"]["verified"] and not result["teardown_errors"] and not result["transcript_errors"]
    assert result["continuations"] == 0 and result["finish_notes"] == 0
    assert sorted(p.name for p in (run_dir / "transport").iterdir()) == [
        f"{i:04d}-{kind}.json" for i in range(1, n + 1) for kind in ("request", "response")]
    ledger = RunLedger.load(run_dir / "budget.json")
    assert len(ledger.rows) == n and ledger.violation is None and ledger.blocked is None
    settled = 0
    for row in ledger.rows:
        assert row["outcome"] == "settled" and row["settled_from"] == "transport" and row["http_status"] == 200
        assert settled + row["reserved_micro_usd"] <= ledger.maximum_micro
        response = read(run_dir / "transport" / f"{row['sequence']:04d}-response.json")
        assert response["id"] == row["response_id"] and response["usage"]["input_tokens"] == row["input_tokens"]
        settled += row["settled_micro_usd"]
    assert result["budget"]["settled_micro_usd"] == settled
    assert result["budget"]["maximum_usd"] == "23.000000" and result["budget"]["settled_stop_usd"] == "20.000000"
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    job_ledger = read(root / "budget/job-ledger.json")
    assert not job_ledger["blocking"] and job_ledger["totals"]["requests"] == n
    assert job_ledger["totals"]["settled_micro_usd"] == settled
    state = read(root / "work/job_scene/state.json")
    assert state["finish"] is None
    journal = {r["call_id"]: r for r in state["events"] if r.get("kind", "call") == "call"}
    folders = sorted((run_dir / "tools").iterdir())
    assert [p.name for p in folders] == [f"{i:04d}-{name}" for i, name in enumerate(expected, 1)]
    blobs = BlobStore(root / "blobs")
    verified_blobs = set()

    def verify_blobs(value):
        if isinstance(value, dict):
            if all(key in value for key in ("sha256", "bytes", "mime")) and str(value["mime"]).startswith("image/"):
                payload = blobs.get(value["sha256"], value["mime"])
                assert len(payload) == value["bytes"]
                verified_blobs.add(value["sha256"])
            for child in value.values():
                verify_blobs(child)
        elif isinstance(value, list):
            for child in value:
                verify_blobs(child)

    images = []
    for i, (folder, name) in enumerate(zip(folders, expected), 1):
        args, record, event = [read(folder / filename) for filename in ("arguments.json", "result.json", "journal.json")]
        cid = f"call_{i:04d}"
        assert args["call_id"] == record["call_id"] == event["call_id"] == cid
        assert args["tool"] == record["tool"] == event["tool"] == journal[cid]["tool"] == name
        assert record["result"][0]["type"] == "text"
        output = json.loads(record["result"][0]["text"])
        assert output["status"] == event["status"] == journal[cid]["status"] == "ok", output
        assert event["mutating"] is False and event["checkpoint_before"] == event["checkpoint_after"]
        assert event["checkpoint_after"] == result["exit_checkpoint"]["id"]
        response = read(run_dir / "transport" / f"{i:04d}-response.json")
        calls = [r for r in response["output"] if r.get("type") == "function_call"]
        assert [(r["call_id"], r["name"]) for r in calls] == [(cid, name)]
        verify_blobs(record)
        if name == "read_image":
            photo = photos[i - 1]
            assert args["arguments"] == {"path": photo.path, "region": None}
            assert len(output["images"]) == 1
            image = output["images"][0]
            assert image["source_path"] == photo.path
            assert A.file_sha256(root / image["source_path"]) == image["source_sha256"]
            assert A.file_sha256(root / image["path"]) == image["sha256"]
            assert image["sha256"] in verified_blobs and image["context_role"] == "reference"
            images.append({"path": photo.path, "source_sha256": image["source_sha256"],
                           "delivered_sha256": image["sha256"], "view": photo.view})
    for path in (run_dir / "transport").iterdir():
        verify_blobs(read(path))
    assert len(images) == len(photos) == len(state["images"])
    assert all(image["sha256"] in verified_blobs for image in state["images"])
    intake = read(run_dir / "input-receipt.json")
    assert intake["images_sent"] == len(photos) and intake["images_reused_from_history"] == 0
    assert [row["path"] for row in intake["references"]] == [photo.path for photo in photos]
    for photo, row in zip(photos, intake["references"]):
        assert row["sha256"] == A.file_sha256(root / photo.path) and row["sha256"] in verified_blobs
    return {"successful_tools": n, "settled_requests": n, "simulated_settled_micro_usd": settled,
            "verified_image_blobs": len(verified_blobs), "images": images}


def run(source, out):
    started = time.monotonic()
    source, out = Path(source), Path(out)
    assert source.is_absolute() and source.is_dir(), "--job must name an absolute existing job directory"
    source, out = source.resolve(), out.resolve()
    assert not out.is_relative_to(source) and not source.is_relative_to(out), "proof output must be separate from source"
    before = files(source)
    assert all(not (source / p).exists() for p in ("budget", "session", "checkpoints", "work")), "source must be pristine intake"
    assert not list((source / "runs").glob("*/approval.json")), "source must have no consumed/paid approval"
    source_job = jobs.load_job(source)
    source_brief = brief.build_brief(source_job)
    source_refs = jobs.references(source)
    assert source_refs, "intake has no photos"
    source_digests = A.input_digests(source)
    source_binding = A.run_input_binding(source, source_brief, source_refs)
    out.mkdir(parents=True, exist_ok=False)
    root = out / "jobs" / source.name
    root.mkdir(parents=True)
    procs.write_json_atomic(out / "source-before.json", before)
    for name in ["job.json", *(row["path"] for row in source_digests["inputs"])]:
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, dest)
        assert (source / name).read_bytes() == dest.read_bytes(), name
    assert A.input_digests(root) == source_digests
    copy_job = jobs.load_job(root)
    copy_brief, references = brief.build_brief(copy_job), jobs.references(root)
    copy_binding = A.run_input_binding(root, copy_brief, references)
    assert copy_brief == source_brief and A.canonical_json(copy_binding) == A.canonical_json(source_binding)
    procs.write_json_atomic(out / "intake-binding.json", {"input_digests": source_digests, "run_inputs": source_binding})
    print(json.dumps({"stage": "copied", "source": str(source), "copy": str(root), "photos": len(references)}), flush=True)
    replies = [replay.tool_call("read_image", {"path": photo.path, "region": None}) for photo in references]
    replies += [replay.tool_call("scene_summary", {"objects": None}), replay.tool_call("budget_status")]
    expected = ["read_image"] * len(references) + ["scene_summary", "budget_status"]
    transport = replay.ScriptedOpenAI(replies)
    settings = replace(job_run.settings_default(), max_turns=len(replies))
    scene = JobScene(root)
    try:
        spec, same_scene = job_run.prepare_run(root, RUN_ID, settings=settings, cap="23", write_request=True,
                                               scene=scene, offline_handler=transport.handle)
        assert same_scene is scene and spec.settings.effort == "max" and spec.settings.smoke is False
        assert spec.brief == source_brief and spec.photos == source_refs
        request = read(root / "runs" / RUN_ID / "approval-request.json")
        assert A.canonical_json(request["request"]["run_inputs"]) == A.canonical_json(source_binding)
        A.write_approval(root / "runs" / RUN_ID, max_usd="23",
                         owner_words="Scripted offline intake proof on an exact copy only; no paid call authorized.",
                         recorded_by="offline proof")
        result = asyncio.run(L.run_astra(spec, scene=scene))
        procs.write_json_atomic(out / "outcome.json", result)
        assert not transport.violations and not transport.unexpected
        assert len(transport.creates) == len(transport.counts) == len(replies)
        payload = transport.creates[0]
        assert payload["instructions"] == spec.instructions
        assert [tool["name"] for tool in payload["tools"]] == [tool.name for tool in spec.tools]
        assert payload["tool_choice"] == "required" and payload["parallel_tool_calls"] is False
        receipt = check_receipts(root, expected, references, result)
        assert scene.s.worker is None and len(scene.s.index.checkpoints()) == 1
        assert scene.finish is None and scene.best_checkpoint is None
        assert A.run_input_binding(root, copy_brief, jobs.references(root)) == source_binding
        for row in source_digests["inputs"]:
            assert A.file_sha256(root / row["path"]) == row["sha256"]
        assert A.file_sha256(root / "job.json") == source_digests["job_json_sha256"]
        for path in (root / "runs" / RUN_ID).rglob("*"):
            if path.is_file():
                assert b"data:image" not in path.read_bytes(), path
        checks = {key: True for key in ("byte_exact_intake_copy", "full_brief_and_ordered_photo_binding",
                  "real_empty_blender_scene", "full_normal_registry", "every_photo_read", "hashed_image_blobs",
                  "readonly_tool_journal", "one_request_per_tool", "turn_limit_before_next_request",
                  "settled_simulated_ledger", "worker_closed", "source_unchanged")}
        return out, {"status": "passed", "source_job": str(source), "copy_job": str(root),
                     "run_id": RUN_ID, "run_status": result["status"], "requests": len(replies),
                     "photo_count": len(references), "checks": checks, "receipt_checks": receipt,
                     "source_files": before, "input_digests": source_digests,
                     "run_input_binding_sha256": hashlib.sha256(A.canonical_json(source_binding)).hexdigest(),
                     "approval_request_sha256": spec.current_request_sha256,
                     "actual_spend_usd": 0, "paid_calls": 0, "seconds": time.monotonic() - started}
    finally:
        if scene.s.worker is not None:
            scene.close()
        after = files(source)
        procs.write_json_atomic(out / "source-after.json", after)
        assert after == before, "the intake proof changed the original job"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True, help="absolute path of an existing pristine intake job")
    parser.add_argument("--out", type=Path, required=True, help="fresh output directory; keep its path short on Windows")
    args = parser.parse_args()
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        work, proof = run(args.job, args.out)
    assert not network.attempts and not clients.constructions
    proof.update(network_attempts=network.attempts, production_transports=clients.constructions)
    procs.write_json_atomic(work / "proof.json", proof)
    print(json.dumps({key: value for key, value in proof.items() if key not in ("source_files", "input_digests")}, indent=2))


if __name__ == "__main__":
    main()
