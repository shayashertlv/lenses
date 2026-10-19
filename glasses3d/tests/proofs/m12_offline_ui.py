"""Private, injected offline launcher and detached workers for the M12 UI proof.

Run ``python -m glasses3d.tests.proofs.m12_offline_ui serve --proof-root ROOT
--port 8290``. ROOT must be below data/m12/proofs and contain proof-config.json
with ``source_plan`` (a private copy of C2's declared instrument plan). The next
new-job scenario is selected by ROOT/control.json, never by an HTTP parameter.
Write ROOT/server-stop.requested for graceful server shutdown and guard receipts.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import threading
import tempfile
import time
import traceback
from unittest.mock import patch
from uuid import uuid4

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

from glasses3d.agent import budget, delivery, edits, job_run, owner_captures, procs
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.dev_ui.contracts import Config, now_iso, opaque
from glasses3d.dev_ui.core_adapter import CoreAdapter
from glasses3d.dev_ui.launcher import Launcher, cutoff_text
from glasses3d.tests.proofs.offline_job_approval import prepare_offline_request

REPO = Path(__file__).resolve().parents[3]
PROOFS = (REPO / "glasses3d/data/m12/proofs").resolve()
MODULE = "glasses3d.tests.proofs.m12_offline_ui"
LABEL = "scripted by Codex, not Astra"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    procs.write_json_atomic(path, value)


def private_root(path):
    root = Path(path).resolve(strict=True)
    if root == PROOFS or not root.is_relative_to(PROOFS):
        raise ValueError("The UI proof requires a private directory below data/m12/proofs")
    return root


def confined(proof, path):
    result = Path(path).resolve()
    if not result.is_relative_to(proof) or result == proof:
        raise ValueError("The offline proof cannot write outside its private root")
    return result


def settings(proof, port=None):
    doc = read(proof / "proof-config.json")
    chosen = port if port is not None else doc.get("port", 8290)
    if not 8290 <= chosen <= 8299:
        raise ValueError("The D1 UI port must be in 8290..8299")
    config = Config(port=chosen, jobs_root=proof / "jobs", state_root=proof / "ui")
    plan = confined(proof, doc.get("source_plan", doc.get("plan_path", proof / "source-plan.json")))
    return config, plan, doc


def offline_environment():
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    disable_sdk_tracing()


@contextmanager
def isolation(proof, receipt):
    offline_environment()
    temporary = proof / "t"
    temporary.mkdir(exist_ok=True)
    with (patch.object(budget, "PAID_RUN_LOCK_ROOT", proof / "locks"),
          patch.object(tempfile, "tempdir", str(temporary)),
          patch.dict(os.environ, {"TEMP": str(temporary), "TMP": str(temporary)}),
          procs.DescendantsOptOut(), LoopbackOnly() as network,
          ProductionTransportTripwire() as clients):
        try:
            yield
        finally:
            write(receipt, {"label": LABEL, "actual_provider_requests": 0, "actual_spend_usd": 0,
                "non_loopback_attempts": network.attempts,
                "production_transport_constructions": clients.constructions,
                "paid_lock_root": str(budget.PAID_RUN_LOCK_ROOT), "temporary_root": str(temporary)})
            assert not network.attempts and not clients.constructions


def _prepare_only_handler(request):
    raise AssertionError("Preparation must not send a model request")


class OfflineLauncher(Launcher):
    offline_test_mode = True

    def __init__(self, config, proof):
        super().__init__(config)
        self.proof = private_root(proof)

    def prepare(self, root, cutoff_usd, *, run_id=None):
        root = confined(self.proof, root)
        amount = cutoff_text(cutoff_usd)
        if not run_id:
            raise ValueError("The UI must durably assign its run ID before offline preparation")
        folder = confined(self.proof, root / "runs" / run_id)
        request_path = folder / "approval-request.json"
        if request_path.is_file():
            request = read(request_path)
            assert request["offline_only"] and budget.cap_to_micro(request["requested_cap_usd"]) == budget.cap_to_micro(amount)
            return {"run_id": run_id, "cutoff_usd": amount, "estimate_usd": "0"}
        self.check_available()
        if (root / "inputs/full-edit-item.json").is_file():
            scenario = "fallback"
        elif (root / "inputs/owner-edit.json").is_file():
            captures = owner_captures.load_round_captures(root)
            scenario = "understand" if len(captures) == 3 else "followup"
            assert len(captures) in {1, 3}
        else:
            scenario = read(self.proof / "control.json")["next_job_scenario"]
            assert scenario in {"success", "cutoff", "slow"}
        run_settings = replace(job_run.settings_default(), effort="high", settled_only=True,
            settled_stop_usd=amount, fit_output_to_budget=False, max_turns=1000 if scenario == "slow" else 20)
        prepare_offline_request(root, run_id, _prepare_only_handler, settings=run_settings, cap=amount)
        write(folder / "proof-script.json", {"scenario": scenario, "cutoff_usd": amount,
            "root": str(root), "run_id": run_id, "proof_root": str(self.proof), "label": LABEL})
        return {"run_id": run_id, "cutoff_usd": amount, "estimate_usd": "0"}

    def approve(self, root, run_id, cutoff_usd, button_label, subject):
        self.check_available()
        folder = confined(self.proof, Path(root) / "runs" / run_id)
        request, approval = read(folder / "approval-request.json"), read(folder / "approval.json")
        assert request["offline_only"] and approval["authorized_by"] == "offline_fixture"
        assert approval["approval_request_sha256"] == edits.sha(folder / "approval-request.json")
        # Never turn a browser click in a build task into owner spending authority.
        write(folder / "proof-button.json", {"button_label": button_label, "cutoff_usd": cutoff_text(cutoff_usd),
            "subject": subject, "synthetic_approval": True, "actual_spend_usd": 0})
        return approval

    def launch(self, root, run_id):
        folder = confined(self.proof, Path(root) / "runs" / run_id)
        receipt_path = folder / "ui-launch.json"
        if receipt_path.is_file():
            return read(receipt_path)
        self.check_available()
        child = procs.spawn_detached([str(self.config.python), "-B", "-m", MODULE, "run",
            "--proof-root", str(self.proof), "--root", str(root), "--run-id", run_id],
            cwd=self.config.repo_root, log_path=folder / "runner.log", status_path=folder / "runner-status.json",
            env={"G3D_OFFLINE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
        receipt = {**child.record(), "status": "started", "run_id": run_id, "offline_test_mode": True}
        write(receipt_path, receipt)
        return receipt

    def stop(self, root, run_id=None):
        from glasses3d.agent import cli
        root = confined(self.proof, root)
        return cli.dispatch(cli.parser().parse_args(["job", "stop", str(root)]))


class PrivateCore(CoreAdapter):
    def __init__(self, proof):
        self.proof = private_root(proof)

    def create_capture_understand(self, source_root, delivery_id, items, destination=None):
        confined(self.proof, source_root)
        for item in items:
            confined(self.proof, item["capture_dir"])
        destination = confined(self.proof, destination or self.proof / "rounds" / ("u" + uuid4().hex[:8]))
        return super().create_capture_understand(source_root, delivery_id, items, destination)

    def create_full_edit_item(self, round_root, item_id, destination=None):
        confined(self.proof, round_root)
        destination = confined(self.proof, destination or self.proof / "rounds" / ("f" + uuid4().hex[:8]))
        return super().create_full_edit_item(round_root, item_id, destination)

    def apply_choices(self, round_root, understanding_sha256, choices, skipped, confirmation_text, destination=None):
        confined(self.proof, round_root)
        confined(self.proof, destination)
        return super().apply_choices(round_root, understanding_sha256, choices, skipped, confirmation_text, destination)

    def review(self, root, delivery_id):
        confined(self.proof, root)
        result = super().review(root, delivery_id)
        write(self.proof / "review-launches" / (uuid4().hex + ".json"), result)
        return result


def launch_task(service, key):
    proof = service.launcher.proof
    key = opaque(key)
    folder = confined(proof, service.config.state_root / "tasks" / key)
    folder.mkdir(parents=True, exist_ok=True)
    task = service.get("tasks", key)
    assert task["offline_test_mode"]
    task.update(launch_requested_at=now_iso(), recoverable=False)
    service.registry.put("tasks", task, key)
    child = procs.spawn_detached([str(service.config.python), "-B", "-m", MODULE, "task",
        "--proof-root", str(proof), "--task-key", key], cwd=service.config.repo_root,
        log_path=folder / "task.log", status_path=folder / "runner-status.json",
        env={"G3D_OFFLINE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
    receipt = child.record()
    write(folder / "launch.json", receipt)
    task.update(launch=receipt, launched=True, launched_at=now_iso())
    service.registry.put("tasks", task, key)
    return child


def build_app(proof_root, port=8290):
    from glasses3d.dev_ui.server import create_app
    proof = private_root(proof_root)
    config, _, _ = settings(proof, port)
    return create_app(config, launcher=OfflineLauncher(config, proof),
                      core_adapter=PrivateCore(proof), task_launcher=launch_task)


def native_capture_audit(root, transport):
    """Compare the bytes in the first actual SDK request with every native tile."""
    from PIL import Image
    import numpy as np
    from glasses3d.agent.session_inputs import owner_capture_references
    refs = owner_capture_references(root)
    if not refs:
        return {"captures": 0, "tiles": 0}
    assert transport.creates, "Captures must reach an actual scripted request"
    request = transport.creates[0]
    urls, texts = [], []

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "input_image":
                urls.append(value["image_url"])
            if value.get("type") == "input_text":
                texts.append(value["text"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(request["input"])
    joined = "\n".join(texts)
    records = owner_captures.load_round_captures(root)
    by_role = {}
    for ref in refs:
        assert ref.image_url in urls, ref.label
        assert ref.label in joined and "not a product photo" in ref.label
        meta = ref.evidence_metadata
        assert meta["native_pixels"] and ref.evidence_text in joined
        key = (meta["capture_id"], meta["image_role"])
        width, height = meta["source_size_px"]
        if key not in by_role:
            source = next(row for row in records if row["capture_id"] == key[0])
            original = np.asarray(Image.open(source[key[1] + "_path"]))
            by_role[key] = (original, np.zeros_like(original), np.zeros((height, width), dtype=np.uint8))
        original, rebuilt, covered = by_role[key]
        x0, y0, x1, y1 = meta["crop_box"]
        tile = np.asarray(Image.open(io.BytesIO(base64.b64decode(ref.image_url.split(",", 1)[1]))))
        rebuilt[y0:y1, x0:x1] = tile
        covered[y0:y1, x0:x1] += 1
    for original, rebuilt, covered in by_role.values():
        assert np.array_equal(original, rebuilt) and np.all(covered == 1)
    return {"captures": len(records), "tiles": len(refs), "native_pixels_equal": True,
        "actual_first_request": True, "owner_mark_labels_present": True,
        "roles": [{"capture_id": key[0], "role": key[1], "pixels": int(value[2].size)}
                  for key, value in by_role.items()]}


def audit_run(root, run_id, script, record, seconds):
    folder = root / "runs" / run_id
    transport = script.transport
    result = read(folder / "result.json")
    assert not result.get("teardown_errors") and not result.get("transcript_errors")
    ledger = budget.RunLedger.load(folder / "budget.json")
    assert not transport.violations and not transport.unexpected
    assert ledger.violation is None and ledger.blocked is None
    assert len(transport.creates) == len(ledger.rows) == result["requests"]
    settled = 0
    for row in ledger.rows:
        assert settled < ledger.maximum_micro, "Request began at or above the typed cutoff"
        assert row["outcome"] == "settled" and row["settled_from"] == "transport"
        settled += row["settled_micro_usd"]
    assert result["budget"]["settled_micro_usd"] == settled
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    requests = sorted((folder / "transport").glob("*-request.json"))
    responses = sorted((folder / "transport").glob("*-response.json"))
    assert len(requests) == len(responses) == len(transport.creates)
    expected_status = {"cutoff": "budget_limit", "slow": "stopped"}.get(record["scenario"], "completed")
    assert result["status"] == expected_status, result.get("error")
    if record["scenario"] == "cutoff":
        assert settled > ledger.maximum_micro and len(transport.creates) == 2
    receipt = {"status": "passed", "label": LABEL, "scenario": record["scenario"], "seconds": seconds,
        "terminal_status": result["status"], "requests": len(transport.creates), "counts": len(transport.counts),
        "transport_receipts": len(requests)+len(responses), "simulated_settled_micro_usd": settled,
        "cutoff_micro_usd": ledger.maximum_micro, "no_request_at_or_above_cutoff": True,
        "in_flight_crossed_cutoff": settled > ledger.maximum_micro, "actual_provider_requests": 0,
        "actual_spend_usd": 0, "captures": native_capture_audit(root, transport)}
    if record["scenario"] == "success":
        from glasses3d.tests.proofs.assembly_m1 import check_receipts
        receipt["m1_receipt_checks"] = check_receipts(root, run_id, [
            "execute_blender_code", "scene_summary", "fit_cameras", "render_views", "seat_lens", "seat_lens",
            "check_lens_seating", "compare_to_photos", "export_glb", "arview_capture", "compare_look_to_photos",
            "finish", "finish"])
    write(folder / "proof-receipt.json", receipt)
    return receipt


async def run_worker(proof, root, run_id):
    from glasses3d.agent.tools import free_commit_bytes, owner_preview
    from glasses3d.tests.proofs.m12_ui_script import make_script
    root = confined(proof, root)
    config, plan, options = settings(proof)
    folder = root / "runs" / run_id
    record = read(folder / "proof-script.json")
    assert record["proof_root"] == str(proof) and record["run_id"] == run_id
    request = read(folder / "approval-request.json")
    assert request["offline_only"]
    script = make_script(root, record, folder, plan)
    free = free_commit_bytes()
    memory_workers = max(1, min(14, int(free / (1.3 * 1024**3))-2))
    workers = max(1, min(memory_workers, int(options.get("workers", memory_workers))))
    write(folder / "proof-resources.json", {"free_commit_bytes": free, "preview_workers": workers,
        "pool_ceiling": memory_workers, "blas_threads": 1, "label": LABEL})
    started = time.monotonic()
    with patch.object(owner_preview, "preview_workers", lambda _: workers):
        outcome = await job_run.execute_run(root, run_id, offline_handler=script.handle)
    write(folder / "proof-outcome.json", outcome)
    audit_run(root, run_id, script, record, time.monotonic()-started)
    return 0


def task_worker(proof, key):
    from glasses3d.dev_ui.api import Service
    from glasses3d.dev_ui.tasks import execute_task
    config, _, _ = settings(proof)
    service = Service(config, OfflineLauncher(config, proof), PrivateCore(proof), launch_task)
    task = service.get("tasks", opaque(key))
    assert task["offline_test_mode"]
    root = confined(proof, task["round_root"])
    transport_before = {str(p): edits.sha(p) for p in root.glob("runs/*/transport/*.json")}
    start = time.monotonic()
    result = execute_task(service, key)
    transport_after = {str(p): edits.sha(p) for p in root.glob("runs/*/transport/*.json")}
    assert transport_before == transport_after
    receipt = {"status": result["phase"], "seconds": time.monotonic()-start,
        "actual_provider_requests": 0, "actual_spend_usd": 0, "understand_requests_unchanged": True,
        "result": result.get("result"), "error": result.get("error")}
    if result["phase"] == "done":
        registered = service.get("deliveries", result["result"]["delivery_key"])
        saved = delivery.load_delivery(Path(registered["root"]), registered["delivery_id"])
        application_path = Path(saved["report"]).parent / saved["report_data"]["owner_applied"]["path"]
        application = read(application_path)
        assert application["api_requests"] == 0 and application["settled_usd"] == "0.000000"
        exactness = application["exactness"]
        assert exactness["ok"] and exactness["overlaps"] == [] and application["warnings"] == []
        assert all(row["exact_preview_geometry"] for row in exactness["items"])
        assert application["notes"] == application["skipped"] == task["body"]["skipped"]
        assert [{k: row[k] for k in ("item_id", "option_id")} for row in application["applied"]] == task["body"]["choices"]
        if len(task["body"]["choices"]) == 1:
            from glasses3d.tests.proofs.assembly_m12_fallback import tool_result
            pick = task["body"]["choices"][0]
            preview = tool_result(root, "c2-preview-" + pick["item_id"])
            option = next(row for row in preview["options"] if row["id"] == pick["option_id"])
            assert exactness["mode"] == "single_glb_bytes" and exactness["single_pick_glb_equal"]
            assert saved["glb_sha256"] == option["export"]["sha256"]
        else:
            assert exactness["mode"] == "per_item_geometry" and len(application["applied"]) == 2
        assert not (Path(registered["root"]) / "runs").exists()
        receipt.update(application_path=str(application_path), application_sha256=edits.sha(application_path),
            exactness=application["exactness"], glb_sha256=saved["glb_sha256"], delivery_report=str(saved["report"]))
    write(config.state_root / "tasks" / key / "proof-receipt.json", receipt)
    return 0 if result["phase"] == "done" else 1


def serve(proof, port):
    import uvicorn
    from glasses3d.dev_ui.status import read_run_status
    app = build_app(proof, port)
    # Only after the watcher drains native workers does should_exit become
    # true. A browser's reset Windows socket can strand asyncio wait_closed;
    # bound this HTTP-only shutdown without interrupting any Blender task.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info",
                                         timeout_graceful_shutdown=5))
    shutdown = threading.Event()

    def watch():
        stopped = set()
        while not shutdown.wait(0.5):
            if (proof / "server-stop.requested").is_file():
                service = app.state.service
                active_runs, active_tasks = [], []
                for row in service.registry.list("runs"):
                    root = confined(proof, row["root"])
                    phase = read_run_status(row)["phase"]
                    runner = procs.read_json(root / "runs" / row["run_id"] / "runner-status.json") or {}
                    # The UI sees publication before the proof audit/guard's
                    # final receipts. Drain the detached worker, too.
                    pending_worker = bool(row.get("launch_requested")) and type(runner.get("exit_code")) is not int
                    if phase in {"starting", "running", "finalizing"}:
                        if row["run_key"] not in stopped:
                            service.launcher.stop(root, row["run_id"])
                            stopped.add(row["run_key"])
                    if phase in {"starting", "running", "finalizing"} or pending_worker:
                        active_runs.append(row["run_key"])
                for row in service.registry.list("tasks"):
                    # Production reconciliation detects an exited/interrupted
                    # worker; successful native applications are allowed to end.
                    runner = procs.read_json(service.config.state_root / "tasks" / row["task_key"] / "runner-status.json") or {}
                    pending_worker = bool(row.get("launched")) and type(runner.get("exit_code")) is not int
                    if service.task(row["task_key"])["phase"] not in {"done", "failed"} or pending_worker:
                        active_tasks.append(row["task_key"])
                write(proof / "server/shutdown.json", {"updated_at": now_iso(),
                    "cooperative_stop_requested": sorted(stopped), "active_runs": active_runs,
                    "active_tasks": active_tasks, "all_private_work_drained": not (active_runs or active_tasks)})
                if not active_runs and not active_tasks:
                    server.should_exit = True
                    return
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        shutdown.set()
        watcher.join(2)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "run", "task"))
    parser.add_argument("--proof-root", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8290)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--task-key")
    args = parser.parse_args()
    proof = private_root(args.proof_root)
    if args.command == "run":
        target = confined(proof, args.root / "runs" / args.run_id)
    elif args.command == "task":
        target = confined(proof, proof / "ui/tasks" / opaque(args.task_key))
    else:
        target = proof / "server"
    target.mkdir(parents=True, exist_ok=True)
    with isolation(proof, target / "offline-isolation.json"):
        try:
            if args.command == "serve":
                return serve(proof, args.port)
            if args.command == "run":
                return asyncio.run(run_worker(proof, args.root, args.run_id))
            return task_worker(proof, args.task_key)
        except BaseException as error:
            write(target / "proof-error.json", {"type": type(error).__name__, "message": str(error),
                "traceback": traceback.format_exc(), "actual_spend_usd": 0})
            raise


if __name__ == "__main__":
    raise SystemExit(main())
