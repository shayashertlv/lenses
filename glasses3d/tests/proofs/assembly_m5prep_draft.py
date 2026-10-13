"""$0 M5-prep draft proof: real Blender/export/AR, detached review, and bound resume notes.

Run as a module with the repository Python. This script constructs a fresh synthetic job; it never opens an
original product scene, invokes a model, grants an approval, or opens a webcam. All artifacts stay in the new
ignored data/assembly directory. The real AR server is stopped using only its own launch state and port.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

from PIL import Image

from glasses3d.agent import approvals as A, cli, delivery as D, job_run, jobs, procs, review as R, verdicts
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.loop import BlobSession, SESSION_ID
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.receipts import BlobStore
from glasses3d.agent.session_inputs import plan_session_input
from glasses3d.g3d_host import fitmesh, raster
from glasses3d.tests.proofs.assembly_m1 import build_code
from glasses3d.tests.test_fit_views_synthetic import PPM, SHAPE, TRUTH, mesh_glb, photo, synthetic_parts

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = "Scripted fixture summary: geometry exists; lens seating remains unfinished. No model was called."
NOTES = "Synthetic proof changes, not a product-owner verdict.\nKeep the best draft colour and finish seating."


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _files(root, directories):
    """Exact retained source bytes, including the checkpoint journal and the actual SQLite history."""
    paths = {root / "job.json"}
    for directory in directories:
        path = root / directory
        if path.is_dir():
            paths.update(p for p in path.rglob("*") if p.is_file())
    return {p.relative_to(root).as_posix(): A.file_sha256(p) for p in sorted(paths)}


SOURCE_DIRS = ("inputs", "images", "reference_images", "checkpoints", "session", "blobs",
               "work/job_scene", "work/session_calls")


def _job(work):
    source = work / "source"
    images = source / "images"
    images.mkdir(parents=True)
    glb = source / "synthetic.glb"
    glb.write_bytes(mesh_glb(synthetic_parts()))
    mesh = fitmesh.load_fit_mesh(glb)
    scene = mesh.scene()
    views = {}
    for name, (kind, yaw, pitch, roll, perspective) in TRUTH.items():
        camera = raster.view_camera(mesh.frame, yaw, pitch, PPM, SHAPE, roll=roll, perspective=perspective)
        Image.fromarray(photo(scene.render(camera, SHAPE)["mask"])).save(images / name)
        views[name] = {"kind": kind, "side": "left" if kind == "side" else None}
    procs.write_json_atomic(source / "views.json", views)
    root = jobs.create_job(images=images, views=source / "views.json", name="Draft",
                           description="Synthetic fixture for host delivery verification; no product acceptance.",
                           jobs_root=work / "jobs")
    return root, {p.relative_to(source).as_posix(): A.file_sha256(p)
                  for p in sorted(source.rglob("*")) if p.is_file()}


def _history(root, *, create=False):
    path = root / "session/session.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    session = BlobSession(SESSION_ID, path, blobs=BlobStore(root / "blobs"))

    async def operation():
        if create:
            await session.add_items([{"role": "user", "content": "Scripted offline draft fixture."},
                                     {"role": "assistant", "content": SUMMARY}])
        return await session.get_items()

    try:
        return asyncio.run(operation())
    finally:
        session.close()


def _stop_review(record):
    state = Path(record["server"]["state_path"])
    stopped = subprocess.run([shutil.which("node"), str(R.ARVIEW / "bin/arview.mjs"), "serve", "--stop",
                              "--port", str(record["server"]["port"]), "--state-dir", str(state.parent)],
                             capture_output=True, text=True, cwd=R.ARVIEW,
                             **({"creationflags": procs.CREATE_NO_WINDOW} if os.name == "nt" else {}))
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert not state.exists(), "the proof's detached review server did not stop"


def run(out=None, *, failed=False):
    started = time.monotonic()
    # Keep the proof's nested fixture below Windows MAX_PATH after the immutable SHA directory is added.
    work = Path(out).resolve() if out else ROOT / "data/assembly" / ("m5d-" + uuid4().hex[:8])
    work.mkdir(parents=True, exist_ok=False)
    disable_sdk_tracing()
    root, source = _job(work)
    print(json.dumps({"stage": "created", "work": str(work), "job": str(root)}), flush=True)
    scene = JobScene(root)
    try:
        scene.start()
        made = scene.s.execute(build_code(), label="Synthetic best, before seating", call_id="proof-build")
        assert made["status"] == "ok", made
        best = copy.deepcopy(scene.s.head)
        scene.best_checkpoint = best["id"]
        scene.save()
        best_export = scene.seeing.export_head(call_id="proof-best-export")
        assert best_export["status"] == "exported", best_export
        assert best_export["validate_spec"]["ok"], best_export["validate_spec"]
        # The ordinary M1 fixture is deliberately not seated here. Its real failure must remain visible in a draft.
        assert best_export["seat_check"]["ok"] is False, best_export["seat_check"]
        altered = scene.s.execute("""import bpy
mat = bpy.data.materials['frame']
mat.diffuse_color = (0.15, 0.35, 0.65, 1.0)
mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = mat.diffuse_color
""", label="Different later head colour", call_id="proof-later-head")
        assert altered["status"] == "ok", altered
        head = copy.deepcopy(scene.s.head)
        later_export = scene.seeing.export_head(call_id="proof-later-export")
        assert later_export["status"] == "exported", later_export
        assert head["id"] != best["id"] and head["fingerprint"] != best["fingerprint"]
        assert later_export["sha256"] != best_export["sha256"]
        exit_checkpoint = scene.exit_checkpoint()
        assert exit_checkpoint["verified"] is True and exit_checkpoint["id"] == head["id"]
    finally:
        scene.close()
    history = _history(root, create=True)
    before = _files(root, SOURCE_DIRS)
    status = "failed" if failed else "budget_limit"
    selected, selected_export = (head, later_export) if failed else (best, best_export)
    other_export = best_export if failed else later_export
    # A failed run need not have verified its live scene at shutdown; salvage independently verifies the saved
    # original bytes and reopened private copy. This flag stays false in the published report.
    if failed:
        exit_checkpoint = {**exit_checkpoint, "verified": False}
    result = {"status": status, "run_id": "offline-draft", "exit_checkpoint": exit_checkpoint,
              "final_output": SUMMARY, "budget": {"settled_usd": "0", "reserved_usd": "0"},
              "requests": 0, "tool_calls": 0}
    print(json.dumps({"stage": "closed", "best_checkpoint": best["id"], "head": head["id"]}), flush=True)
    delivered = D.deliver_draft(root, scene, result)
    procs.write_json_atomic(work / "delivery-result.json", delivered)
    assert delivered["status"] == "delivered" and delivered["finished"] is False, delivered
    saved = D.load_delivery(root, delivered["delivery_id"])
    report = saved["report_data"]
    assert report["finished"] is False and report["accepted_finish"] is False
    assert report["run_status"] == status and report["summary"] == SUMMARY
    assert report["checkpoint_selection"] == ("last_verified" if failed else "best")
    assert report["checkpoint"] == selected["id"]
    assert report["source_checkpoint"]["verified"] is True
    assert report["exit_checkpoint"]["verified"] is (not failed)
    if failed:
        assert report["source_checkpoint"]["verification_attempts"] == [{
            "checkpoint": head["id"], "status": "verified", "sha256": head["sha256"], "fingerprint": head["fingerprint"]}]
    for key in ("id", "sha256", "fingerprint"):
        assert report["source_checkpoint"][key] == selected[key]
    assert saved["glb_sha256"] == selected_export["sha256"] != other_export["sha256"]
    assert report["validate_spec"]["ok"] is True and report["seating"]["ok"] is False
    assert "seating" in report["failed_checks"] and report["validation_sha256"] == saved["glb_sha256"]
    ar = report["arview"]
    assert ar["status"] == "ok" and ar["exit_code"] == 0, ar
    assert ar["fixed_scale"] is True and ar["lens_env_intensity"] == 1
    assert ar["appearance_policy"] == R.AR_APPEARANCE_POLICY and ar["glb_sha256"] == saved["glb_sha256"]
    assert saved["contact_sheets"] and all(Path(p).is_file() for p in saved["contact_sheets"])
    assert scene.s.worker is None and scene.s.head == head and scene.finish is None
    assert _files(root, SOURCE_DIRS) == before, "draft export changed source checkpoint/history/input bytes"
    shown = cli.dispatch(cli.parser().parse_args(["job", "show", str(root)]))["delivery"]
    assert shown["status"] == "draft" and shown["finished"] is False and shown["accepted_finish"] is False
    assert shown["run_status"] == status and shown["checks"]["seating"]["ok"] is False
    immutable_root = Path(saved["manifest"]).parent
    immutable_before = {p.relative_to(immutable_root).as_posix(): A.file_sha256(p)
                        for p in sorted(immutable_root.rglob("*")) if p.is_file()}
    base_spec, base_scene = job_run.prepare_run(root, "before-notes", resume=True, write_request=True)
    assert base_scene.s.worker is None and base_spec.owner_notes == []
    base_request = _read(root / "runs/before-notes/approval-request.json")
    reviewed = None
    compatibility = Path(delivered["model"])
    compatibility_bytes = compatibility.read_bytes()
    try:
        # Replace only the convenience copy with a distinct, already verified export. Review must serve the snapshot.
        compatibility.write_bytes(Path(other_export["path"]).read_bytes())
        reviewed = cli.dispatch(cli.parser().parse_args(["job", "review", str(root), "--port", "0"]))
        assert reviewed["finished"] is False and reviewed["accepted_finish"] is False
        assert reviewed["delivery_checks"]["seating"]["ok"] is False
        assert reviewed["delivery_id"] == saved["delivery_id"] and reviewed["glb_sha256"] == saved["glb_sha256"]
        assert reviewed["manifest_sha256"] == saved["manifest_sha256"]
        assert Path(reviewed["model"]) == Path(saved["model"]) and reviewed["server"]["detached"] is True
        assert R._report(reviewed["server"]["port"])["served_sha256"] == saved["glb_sha256"]
        assert R.reviewed_delivery(root) == reviewed
        verdict = cli.dispatch(cli.parser().parse_args(["job", "verdict", str(root), "changes", "--notes", NOTES]))
        assert verdict["notes"] == NOTES and verdict["verdict"] == "changes"
        assert verdict["delivery_id"] == saved["delivery_id"] and verdict["glb_sha256"] == saved["glb_sha256"]
        assert verdict["manifest_sha256"] == saved["manifest_sha256"]
    finally:
        compatibility.write_bytes(compatibility_bytes)
        if reviewed is not None:
            _stop_review(reviewed)
    assert R.reviewed_delivery(root) == reviewed, "closed review must retain the viewed asset identity"
    notes = verdicts.pending_notes(root)
    assert len(notes) == 1 and notes[0].endswith("\n" + NOTES)
    assert saved["delivery_id"] in notes[0] and saved["glb_sha256"] in notes[0]
    resumed, resumed_scene = job_run.prepare_run(root, "after-notes", resume=True, write_request=True)
    assert resumed.resume and resumed.owner_notes == notes and resumed_scene.s.worker is None
    assert resumed_scene.s.head == head and resumed_scene.best_checkpoint == best["id"]
    resume_request = _read(root / "runs/after-notes/approval-request.json")
    assert resumed.current_request_sha256 != base_spec.current_request_sha256
    expected_request = copy.deepcopy(base_request["request"])
    expected_request["unsent_owner_notes"] = [hashlib.sha256(A.canonical_json(note)).hexdigest() for note in notes]
    assert resume_request["request"] == expected_request, "resume binding changed beyond the exact new verdict notes"
    plan = plan_session_input(history, prompt=resumed.brief, photos=resumed.photos, owner_notes=resumed.owner_notes,
                              invocation_text="Offline proof resumes the saved head; no request is sent.")
    text = "\n".join(p.get("text", "") for p in plan.content)
    assert NOTES in text and saved["glb_sha256"] in text and saved["delivery_id"] in text
    replayed = history + [{"role": "user", "content": plan.content}]
    repeated = plan_session_input(replayed, prompt=resumed.brief, photos=resumed.photos, owner_notes=notes,
                                  invocation_text="A second offline input plan.")
    assert NOTES not in "\n".join(p.get("text", "") for p in repeated.content), "a persisted note was duplicated"
    assert _history(root) == history
    assert _files(root, SOURCE_DIRS) == before
    assert {p.relative_to(immutable_root).as_posix(): A.file_sha256(p)
            for p in sorted(immutable_root.rglob("*")) if p.is_file()} == immutable_before
    assert {p.relative_to(work / "source").as_posix(): A.file_sha256(p)
            for p in sorted((work / "source").rglob("*")) if p.is_file()} == source
    assert not list((root / "runs").glob("*/approval.json"))
    assert not list((root / "runs").glob("*/transport/*"))
    checks = {name: True for name in ("real_blender", "distinct_best_and_head",
              "last_verified_selected" if failed else "best_selected", "source_preserved",
              "failed_seating_reported", "draft_show_label", "fixed_scale_ar", "immutable_snapshot",
              "detached_review_ready", "owned_server_stopped", "verdict_identity", "resume_note_binding",
              "resume_note_sent_once", "no_approval", "no_model_requests")}
    proof = {"status": "passed", "run_status": status, "job": str(root), "delivery_id": saved["delivery_id"],
             "glb_sha256": saved["glb_sha256"], "best_checkpoint": best["id"], "head_checkpoint": head["id"],
             "failed_checks": report["failed_checks"], "checks": checks, "source_files": before,
             "immutable_files": immutable_before, "resume_request_sha256": resumed.current_request_sha256,
             "actual_spend_usd": 0, "model_requests": 0, "seconds": time.monotonic() - started}
    return work, proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="new output directory; existing directories are refused")
    parser.add_argument("--failed", action="store_true", help="verify failed-run latest-checkpoint delivery instead of best")
    args = parser.parse_args()
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    with LoopbackOnly() as network, ProductionTransportTripwire() as transport:
        work, proof = run(args.out, failed=args.failed)
    assert not network.attempts and not transport.constructions
    proof["network_attempts"] = network.attempts
    proof["production_transports"] = transport.constructions
    procs.write_json_atomic(work / "proof.json", proof)
    print(json.dumps({k: v for k, v in proof.items() if k not in ("source_files", "immutable_files")}, indent=2))


if __name__ == "__main__":
    main()
