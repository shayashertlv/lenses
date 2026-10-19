"""Isolated $0 real-browser dev-UI proof. Scripted Astra, real SDK and Blender.

Run from the repository root with the glasses3d venv, -B -m, and --out under
glasses3d/data/m12/proofs. No output belongs to an owner job or existing delivery.
The retained C2 plan supplies declared operations, not product deliverables.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "glasses3d/data/m12/proofs"
DEFAULT_PLAN = REPO / "glasses3d/data/m12/codex/c2_proof_plan.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    from glasses3d.agent.procs import write_json_atomic
    write_json_atomic(path, value)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_files(folder):
    for path in Path(folder).rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"Linked source is not a proof input: {path}")
        if path.is_file() and path.name.lower().startswith(".env"):
            raise ValueError("Environment files are never proof inputs")


def source_hashes(source):
    """Hash only explicit non-secret immutable input/delivery/checkpoint folders."""
    source = Path(source)
    paths = [source / "job.json"]
    for name in ("images", "reference_images", "inputs", "deliverable", "deliveries", "checkpoints", "cameras", "work/print"):
        folder = source / name
        if folder.exists():
            safe_files(folder)
            paths.extend(p for p in folder.rglob("*") if p.is_file())
    return {p.relative_to(source).as_posix(): sha(p) for p in sorted(paths)}


def copy_source(source, destination):
    before = source_hashes(source)
    destination.mkdir(parents=True, exist_ok=False)
    for relative, digest in before.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
        assert sha(target) == digest
    assert source_hashes(destination) == before
    return before


def prepare_source(out, plan_path):
    plan = read(plan_path)
    source = Path(plan["source"]).resolve(strict=True)
    original = Path(plan.get("original_source", REPO / "glasses3d/data/jobs/prada-beige-86248315")).resolve(strict=True)
    destination = out / "jobs" / source.name
    before = {"adapted_source": {"root": str(source), "files": source_hashes(source)},
              "original_source": {"root": str(original), "files": source_hashes(original)}}
    write(out / "source-before.json", before)
    copy_source(source, destination)
    plan["source"] = str(destination)
    plan["original_source"] = str(original)
    write(out / "source-plan.json", plan)
    from glasses3d.agent.delivery import load_delivery
    loaded = load_delivery(destination, plan["delivery_id"])
    assert loaded["glb_sha256"] == plan["source_glb_sha256"]
    write(out / "copy-before.json", {"root": str(destination), "files": source_hashes(destination)})
    return {"source_root": str(destination), "delivery_id": plan["delivery_id"],
            "source_glb_sha256": plan["source_glb_sha256"], "source_plan": str(out / "source-plan.json"),
            "plan_path": str(out / "source-plan.json"),
            "jobs_root": str(out / "jobs"), "state_root": str(out / "ui"),
            "control_path": str(out / "control.json")}


def synthetic_photos(out):
    import numpy as np
    from PIL import Image
    from glasses3d.agent.job_scene import JobScene
    from glasses3d.tests.proofs.assembly_m1 import build_code, TRUTH, photo
    truth = JobScene(out / "truth", job={})
    photos = out / "photos"
    photos.mkdir(parents=True)
    try:
        truth.start()
        built = truth.s.execute(build_code(), label="M12 synthetic truth")
        assert built["status"] == "ok", built
        export = truth.seeing.export_head()
        assert export["validate_spec"]["ok"]
        names = {name: f"truth-{i:02d}" for i, name in enumerate(TRUTH, 1)}
        views = [{"name": names[name], "yaw": yaw, "pitch": pitch, "roll": roll,
                  "perspective": perspective, "width_mm": 640 / 3}
                 for name, (_, yaw, pitch, roll, perspective) in TRUTH.items()]
        rendered = truth.seeing.render_views(views, passes=["silhouette"], size_px=640, background="white")
        assert rendered["status"] == "ok", rendered
        for name in TRUTH:
            with Image.open(rendered["views"][names[name]]["files"]["silhouette"]) as im:
                Image.fromarray(photo(np.asarray(im) > 127)).save(photos / name)
    finally:
        truth.close()
    return [{"path": str(photos / name), "kind": values[0],
             "side": "left" if name == "side.png" else "both" if name == "front.png" else "none"}
            for name, values in TRUTH.items()]


def prepare(out, plan):
    config_path = out / "proof-config.json"
    if config_path.exists():
        config = read(config_path)
    else:
        config = prepare_source(out, plan)
        write(config_path, config)
    if not config.get("photos"):
        config["photos"] = synthetic_photos(out)
    write(config_path, config)
    write(out / "control.json", {"next_job_scenario": "success"})
    return config


def get_json(port, route):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", route)
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200, (route, response.status, body[:500])
        return json.loads(body)
    finally:
        connection.close()


def browser(out, port, scenarios=None, resume=False):
    from glasses3d.agent import procs, review
    config = read(out / "proof-config.json")
    if not 8290 <= port <= 8299:
        raise ValueError("D1 UI ports are 8290 through 8299")
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))  # refuse an existing UI; never replace one
    (out / "server-stop.requested").unlink(missing_ok=True)
    command = [sys.executable, "-B", "-m", "glasses3d.tests.proofs.m12_offline_ui",
               "serve", "--proof-root", str(out), "--port", str(port)]
    server = procs.spawn_detached(command, cwd=REPO, log_path=out / "server.log",
        status_path=out / "server-status.json", env={"G3D_OFFLINE": "1"})
    write(out / "server-launch.json", server.record())
    started = time.monotonic()
    try:
        while True:
            try:
                assert get_json(port, "/api/config")["offline_test_mode"] is True
                break
            except (OSError, AssertionError):
                status = server.status()
                if (status and status.get("exit_code") is not None) or time.monotonic() - started > 60:
                    raise RuntimeError("Offline UI failed to start; see server.log")
                time.sleep(.25)
        jobs = get_json(port, "/api/jobs")["jobs"]
        source = next(job for job in jobs if job["job_id"] == Path(config["source_root"]).name)
        candidates = [row["delivery_key"] for row in source["deliveries"]]
        key = next(k for k in candidates if get_json(port, f"/api/deliveries/{k}")["delivery_id"] == config["delivery_id"])
        driver_config = {"base_url": f"http://127.0.0.1:{port}", "proof_root": str(out),
                         "output_root": str(out / "browser"), "photos": config["photos"],
                         "prada_delivery_key": key, "control_path": config["control_path"], "resume": resume}
        if scenarios:
            driver_config["scenarios"] = scenarios
        write(out / "browser-config.json", driver_config)
        argv = [shutil.which("node"), str(REPO / "glasses3d/dev_ui/e2e/run.mjs"), str(out / "browser-config.json")]
        receipt = {"argv": argv, "started_utc": procs.utcnow(), "actual_usd": 0}
        with (out / "browser.log").open("ab") as log:
            child = subprocess.Popen(argv, cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
            receipt["pid"] = child.pid
            write(out / "browser-process.json", receipt)
            receipt["exit_code"] = child.wait()
        receipt.update(finished_utc=procs.utcnow(), seconds=time.monotonic() - started)
        write(out / "browser-process.json", receipt)
        assert receipt["exit_code"] == 0, "Browser proof failed; see browser.log"
    finally:
        # Signal only this Python-injected server. It drains its own active tasks
        # before quitting; browser closure never controls the run lifetime.
        (out / "server-stop.requested").write_text("D1 proof complete", encoding="utf-8")
        server.wait()
        for path in out.rglob("port-*.json"):
            if path.is_file():
                state = read(path)
                if state.get("schema") == "arview.serve-state/1":
                    review._stop_owned(state)


def audit_applies(out, report, receipts):
    """Authenticate current outcomes while retaining superseded native failures."""
    from glasses3d.agent import delivery, edits
    from glasses3d.tests.proofs.m12_offline_ui import SAME_PART_REFUSAL, confined
    state = report["state"].get("edit", {})
    refusal_state, single = state.get("same_part_refusal"), state.get("single")
    if not refusal_state or not single:
        return [], receipts, {"complete": False, "reason": "Required refusal or single apply is missing"}
    by_key = {row["task_key"]: row for row in receipts}
    refusal_key, single_key = refusal_state["task"]["task_key"], single["task"]["task_key"]
    assert refusal_key != single_key
    refusal, applied = by_key[refusal_key], by_key[single_key]
    task_root = out / "ui/registry/tasks"
    refused_task, applied_task = read(task_root / (refusal_key + ".json")), read(task_root / (single_key + ".json"))
    assert refusal["status"] == refused_task["phase"] == refusal_state["task"]["phase"] == "failed"
    assert refusal["error"] == refused_task["error"] == refusal_state["task"]["error"] == SAME_PART_REFUSAL
    assert refusal_state["expected_error"] == SAME_PART_REFUSAL and refusal["result"] is None
    expected = refusal["expected_refusal"]
    assert expected["status"] == "expected_same_part_shading_refusal" and expected["message"] == SAME_PART_REFUSAL
    evidence_path = confined(out, expected["evidence_path"])
    assert evidence_path == out / "ui/tasks" / refusal_key / "same-part-refusal.json"
    assert sha(evidence_path) == expected["evidence_sha256"]
    observation = read(evidence_path)
    assert observation["message"] == SAME_PART_REFUSAL
    assert observation["choices"] == refused_task["body"]["choices"] == [
        {"item_id": "I1", "option_id": "B"}, {"item_id": "I2", "option_id": "B"}]
    assert observation["skipped"] == refused_task["body"]["skipped"]
    assert all(observation[name] == 0 for name in
               ("directory_creations", "private_copies", "native_worker_starts", "publications",
                "actual_provider_requests", "actual_spend_usd"))
    assert observation["destination_absent"] and observation["source_round_unchanged"]
    destination = confined(out, observation["destination"])
    assert destination == out / "ui/applied" / refusal_key and not destination.exists()
    root = confined(out, observation["source_root"])
    assert root == Path(refused_task["round_root"]) == Path(applied_task["round_root"])
    assert edits.tree_hashes(root) == observation["source_round_before"] == observation["source_round_after"]
    understanding_path = root / "work/owner_edit/understanding.json"
    assert sha(understanding_path) == observation["understanding_sha256"] == state["understanding"]["understanding_sha256"]
    assert all(row["body"]["understanding_sha256"] == sha(understanding_path)
               for row in (refused_task, applied_task))

    assert applied["status"] == applied_task["phase"] == single["task"]["phase"] == "done"
    assert applied["result"] == applied_task["result"] == single["task"]["result"]
    delivery_key = applied["result"]["delivery_key"]
    assert single["delivery"]["delivery_key"] == delivery_key
    registered = read(out / "ui/registry/deliveries" / (delivery_key + ".json"))
    applied_root = confined(out, registered["root"])
    assert applied_root == out / "ui/applied" / single_key and not (applied_root / "runs").exists()
    loaded = delivery.load_delivery(applied_root, registered["delivery_id"])
    application_path = Path(loaded["report"]).parent / loaded["report_data"]["owner_applied"]["path"]
    assert application_path == confined(out, applied["application_path"])
    assert sha(application_path) == applied["application_sha256"]
    application = read(application_path)
    assert application["api_requests"] == 0 and application["settled_usd"] == "0.000000"
    assert application["origin"] == single["delivery"]["origin"] == "applied_picks"
    assert application["notes"] == application["skipped"] == applied_task["body"]["skipped"] == single["delivery"]["notes"]
    assert {row["item_id"] for row in application["skipped"]} == {"I2", "I3"}
    assert any(row["item_id"] == "I2" and row["note"].strip() for row in application["skipped"])
    assert [{k: row[k] for k in ("item_id", "option_id")} for row in application["applied"]] == [
        {"item_id": "I1", "option_id": "B"}] == applied_task["body"]["choices"]
    exactness = application["exactness"]
    assert exactness == applied["exactness"] == single["delivery"]["exactness"]
    assert exactness["ok"] and exactness["mode"] == "single_glb_bytes" and exactness["single_pick_glb_equal"]
    assert exactness["overlaps"] == [] and application["warnings"] == []
    assert len(exactness["items"]) == 1 and exactness["items"][0]["exact_preview_geometry"]
    understanding = read(understanding_path)
    item = next(row for row in understanding["items"] if row["item_id"] == "I1")
    preview_path = edits.confined(root, item["amount_preview"]["preview_receipt"])
    assert sha(preview_path) == item["amount_preview"]["preview_sha256"]
    option = next(row for row in read(preview_path)["options"] if row["id"] == "B")
    preview_glb = confined(root, option["export"]["path"])
    assert sha(preview_glb) == option["export"]["sha256"] == loaded["glb_sha256"]
    assert loaded["glb_sha256"] == applied["glb_sha256"] == single["delivery"]["glb_sha256"]
    historical = [row for row in receipts if row["task_key"] not in {refusal_key, single_key}]
    # The earlier native failure remains a failure, never a successful apply.
    old_task = report["state"].get("apply_tasks", {}).get("two-picks")
    assert {row["task_key"] for row in historical} == ({old_task["task_key"]} if old_task else set())
    assert all(row["status"] == "failed" and not row.get("expected_refusal")
               and row["error"] == "normal intent or encoded normals differ from the confirmed plan"
               for row in historical)
    return [refusal, applied], historical, {"complete": True,
        "refusal_task_key": refusal_key, "single_apply_task_key": single_key,
        "single_delivery_reauthenticated": True, "single_glb_sha256": loaded["glb_sha256"],
        "source_round_unchanged": True, "no_native_work_or_publication_for_refusal": True}


def audit(out):
    before = read(out / "source-before.json")
    after = {}
    for role, record in before.items():
        files = source_hashes(Path(record["root"]))
        after[role] = {"root": record["root"], "files": files, "unchanged": files == record["files"]}
        assert after[role]["unchanged"], f"Protected source changed: {role}"
    copied = read(out / "copy-before.json")
    assert source_hashes(Path(copied["root"])) == copied["files"], "Private source immutable files changed"
    write(out / "source-after.json", after)
    report = read(out / "browser/browser-report.json")
    required = {"1-new-run-and-double-click", "2-cutoff-draft", "3-browser-survival-stop-and-concurrency",
                "4-captures-understand-refusal-and-single-exact-apply", "5-fallback-and-fresh-understanding"}
    passed = {row["id"] for row in report["scenarios"] if row["status"] == "passed"}
    assert not report["page_errors"] and not report["network_violations"]
    assert report["state"]["stale_simulation"]["status"] == 409
    assert report["state"]["stale_simulation"]["body"]["error"]["code"] == "stale_understanding"
    survival = next(row for row in report["scenarios"] if row["id"] == "3-browser-survival-stop-and-concurrency")
    assert survival["result"]["concurrent_refusal"]["status"] == 409
    assert survival["result"]["concurrent_refusal"]["body"]["error"]["code"] == "paid_run_busy"
    new_run = next(row for row in report["scenarios"] if row["id"] == "1-new-run-and-double-click")
    starts = report["requests"][new_run["request_start"]:new_run["request_end"]]
    assert sum(row["method"] == "POST" and row["path"] == "/api/jobs/start" for row in starts) == 1
    run_receipts = []
    for path in out.rglob("proof-script.json"):
        script = read(path)
        folder = path.parent
        receipt = read(folder / "proof-receipt.json")
        approval, request = read(folder / "approval.json"), read(folder / "approval-request.json")
        assert receipt["status"] == "passed" and receipt["actual_provider_requests"] == 0
        assert receipt["no_request_at_or_above_cutoff"]
        assert approval["authorized_by"] == "offline_fixture" and request["offline_only"]
        assert request["effort"] == "high"
        assert approval["approval_request_sha256"] == sha(folder / "approval-request.json")
        run_receipts.append({"path": str(folder), **receipt})
    assert {row["scenario"] for row in run_receipts} == {"success", "cutoff", "slow", "understand", "fallback", "followup"}
    assert len(run_receipts) == 6, "Each scripted paid-button scenario creates exactly one run"
    previous_audit = out / "followup1/previous/run-audit.json"
    if previous_audit.is_file():
        prior_runs = read(previous_audit)["runs"]
        assert {row["path"]: row for row in prior_runs} == {row["path"]: row for row in run_receipts}, \
            "Follow-up 1 reuses all six completed scripted runs without changing their receipts"
    assert len(list(out.rglob("approval-request.json"))) == len(run_receipts), "No extra approval was prepared"
    for scenario, count in {"success": 13, "cutoff": 2, "understand": 6, "fallback": 4, "followup": 2}.items():
        assert any(row["scenario"] == scenario and row["requests"] == count for row in run_receipts)
    native = next(row for row in run_receipts if row["scenario"] == "understand")["captures"]
    assert native["captures"] == 3 and native["native_pixels_equal"] and native["actual_first_request"]
    fresh = next(row for row in run_receipts if row["scenario"] == "followup")["captures"]
    assert fresh["captures"] == 1 and fresh["native_pixels_equal"] and fresh["actual_first_request"]
    task_folders = [path for path in (out / "ui/tasks").iterdir() if path.is_dir()]
    task_receipts = [{**read(folder / "proof-receipt.json"), "task_key": folder.name,
                      "path": str(folder / "proof-receipt.json")} for folder in task_folders]
    assert all(row["understand_requests_unchanged"] and row["actual_provider_requests"] == row["actual_spend_usd"] == 0
               for row in task_receipts)
    isolation_paths = [out / "server/offline-isolation.json"]
    isolation_paths.extend(Path(row["path"]) / "offline-isolation.json" for row in run_receipts)
    isolation_paths.extend(path.parent / "offline-isolation.json"
                           for path in (out / "ui/tasks").glob("*/proof-receipt.json"))
    for path in isolation_paths:
        isolation = read(path)
        assert isolation["actual_provider_requests"] == isolation["actual_spend_usd"] == 0
        assert not isolation.get("non_loopback_attempts") and not isolation.get("production_transport_constructions")
    fixtures, inputs = report["fixtures"], []
    all_jobs = list((out / "jobs").glob("*/job.json"))
    assert len(all_jobs) == 4, "Only the copied source and three submitted new jobs exist"
    for name in fixtures["names"].values():
        roots = [path.parent for path in (out / "jobs").glob("*/job.json") if read(path)["name"] == name]
        assert len(roots) == 1, (name, roots)
        job = read(roots[0] / "job.json")
        assert len(list((roots[0] / "runs").glob("*/proof-script.json"))) == 1
        assert job["description"] == fixtures["description"] and job["specs_json"] == fixtures["specs_text"]
        expected = {Path(row["path"]).name: row for row in read(out / "proof-config.json")["photos"]}
        assert sorted(image["original_name"] for image in job["images"]) == sorted(expected)
        for image in job["images"]:
            supplied = expected[image["original_name"]]
            assert image["view_kind"] == supplied["kind"]
            assert image["side"] == (None if supplied["side"] == "none" else supplied["side"])
            assert not {"matte_mode", "reflective_floor", "material", "hint"} & image.keys()
            assert sha(roots[0] / image["file"]) == sha(supplied["path"])
        inputs.append({"job": str(roots[0]), "description_verbatim": True, "specs_verbatim": True,
                       "photos_and_view_side_labels_only": True})
    for path in (out / "ui/uploads").glob("*/views.json"):
        assert all(set(view) == {"kind", "side"} for view in read(path).values())
    accepted_tasks, historical_tasks, apply_audit = audit_applies(out, report, task_receipts)
    write(out / "run-audit.json", {"runs": run_receipts, "tasks": task_receipts, "inputs": inputs,
          "accepted_apply_outcomes": accepted_tasks, "historical_task_receipts": historical_tasks,
          "apply_audit": apply_audit})
    # Preserve independently checked evidence even when a required native task
    # refused. This never turns a partial run into a passed proof.
    complete = report["status"] == "passed" and required <= passed and apply_audit["complete"]
    write(out / "audit-evidence.json", {"status": "complete" if complete else "incomplete",
          "missing_required_scenarios": sorted(required - passed),
          "failed_tasks": [row for row in task_receipts if row["status"] != "done"],
          "accepted_apply_outcomes": accepted_tasks, "historical_task_receipts": historical_tasks,
          "apply_audit": apply_audit,
          "sources_unchanged": True, "private_source_unchanged": True,
          "isolation_receipts_checked": [str(path) for path in isolation_paths],
          "actual_spend_usd": 0, "runs": run_receipts, "tasks": task_receipts, "inputs": inputs})
    assert complete, "D1 remains incomplete; see audit-evidence.json and browser attempt archives"
    write(out / "proof.json", {"status": "passed", "scope": "D1 browser scenarios 1-6; M10 regression reported separately",
          "actual_spend_usd": 0,
          "browser": report, "sources_unchanged": True,
          "scripted_requests": sum(row["requests"] for row in run_receipts),
          "run_receipts": run_receipts, "apply_receipts": accepted_tasks,
          "historical_task_receipts": historical_tasks, "apply_audit": apply_audit,
          "source_file_counts": {key: len(value["files"]) for key, value in before.items()}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--port", type=int, default=8290)
    parser.add_argument("--phase", choices=("prepare", "browser", "audit", "all"), default="all")
    parser.add_argument("--scenarios", nargs="+")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(BASE.resolve()) or out == BASE.resolve():
        raise ValueError("Proof output must be a private subdirectory of data/m12/proofs")
    out.mkdir(parents=True, exist_ok=True)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    os.environ["G3D_OFFLINE"] = "1"
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
    from glasses3d.agent import procs, budget
    from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
    disable_sdk_tracing()
    budget.PAID_RUN_LOCK_ROOT = out / "locks"
    started = time.monotonic()
    try:
        with LoopbackOnly() as network, ProductionTransportTripwire() as clients, procs.DescendantsOptOut():
            if args.phase in ("prepare", "all"):
                prepare(out, args.plan)
            if args.phase in ("browser", "all"):
                browser(out, args.port, args.scenarios, args.resume)
            if args.phase in ("audit", "all"):
                audit(out)
            assert not network.attempts and not clients.constructions
        write(out / f"{args.phase}-completed.json", {"seconds": time.monotonic() - started,
              "actual_spend_usd": 0, "network_attempts": [], "production_transports": [], "exit_code": 0})
    except BaseException:
        write(out / f"{args.phase}-failure.json", {"seconds": time.monotonic() - started,
              "actual_spend_usd": 0, "error": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
