"""Synthetic status files: no model client, Blender, ledger, or process launch."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from glasses3d.dev_ui.status import find_run_delivery, read_run_status


START = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
NOW = START + timedelta(seconds=120)
RUN = "20261019T100000-a1b2c3"
SHA = "a" * 64


def write(root, relative, payload):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def record(tmp_path):
    root = tmp_path / "synthetic-job"
    root.mkdir()
    return {"root": str(root), "run_key": "run_abcd", "kind": "job", "job_id": root.name,
            "run_id": RUN, "cutoff_usd": "27.50", "edit_key": None, "offline_test_mode": False}


def root_of(record):
    from pathlib import Path
    return Path(record["root"])


def runner(record, **kwargs):
    write(root_of(record), f"runs/{RUN}/runner-status.json", {
        "started_utc": START.isoformat(), "exit_code": None, "finished_utc": None, **kwargs})


def heartbeat(record, **kwargs):
    write(root_of(record), "heartbeat.json", {"schema": "g3d.heartbeat/1", "run_id": RUN,
        "state": "model_wait", "since": (NOW - timedelta(seconds=2)).isoformat(),
        "updated": NOW.isoformat(), "settled_usd": "3.400001", "held_usd": "2.000002",
        "unknown_usd": "0.100003", **kwargs})


def result(record, **kwargs):
    write(root_of(record), f"runs/{RUN}/result.json", {"schema": "g3d.run_result/1", "run_id": RUN,
        "job_id": record["job_id"], "status": "completed", "started": (START + timedelta(seconds=3)).isoformat(),
        "finished": (START + timedelta(seconds=90)).isoformat(), "error": None,
        "budget": {"settled_micro_usd": 5_000_007, "held_micro_usd": 0, "unknown_micro_usd": 0}, **kwargs})


def publish(record, *, run_id=RUN, finished=True, current=True, attempt=True):
    root = root_of(record)
    delivery_id = run_id + "-" + SHA
    manifest = {"schema": "g3d.delivery/1", "job_id": root.name, "run_id": run_id,
                "id": delivery_id, "glb_sha256": SHA, "model": "model.glb", "report": "report.json"}
    digest = write(root, f"deliveries/{delivery_id}/manifest.json", manifest)
    if current:
        write(root, "deliveries/current.json", {"schema": "g3d.delivery_current/1", "id": delivery_id,
              "glb_sha256": SHA, "manifest_sha256": digest})
    if attempt:
        write(root, f"runs/{RUN}/delivery.json", {"status": "delivered", "id": delivery_id,
              "manifest_sha256": digest, "finished": finished, "run_status": "completed" if finished else "budget_limit"})
    return delivery_id


def understanding(record, *, usage_run=RUN, delivered=True, tamper=False):
    root = root_of(record)
    digest = write(root, "work/owner_edit/understanding.json", {
        "schema": "g3d.owner_understanding/1", "call_id": "call_1", "status": "awaiting_owner_confirmation"})
    write(root, "work/owner_edit/understanding-delivery.json", {
        "schema": "g3d.owner_understanding_delivery/1", "delivered": delivered,
        "call_id": "call_1", "sha256": "b" * 64 if tamper else digest})
    write(root, "work/owner_edit/understanding-usage.json", {
        "schema": "g3d.owner_understanding_usage/1", "run_id": usage_run,
        "settled_usd": "2.500005", "requests": 7})


def test_starting_is_exact_dto_and_unknown_money_is_not_fabricated(record):
    status = read_run_status(record, now=NOW)
    assert set(status) == {"schema", "run_key", "kind", "job_id", "run_id", "phase", "loop_state",
        "started_at", "updated_at", "elapsed_seconds", "settled_usd", "held_usd", "unknown_usd",
        "cutoff_usd", "estimate_usd", "terminal_status", "stop_requested", "delivery_key", "edit_key",
        "error", "offline_test_mode"}
    assert status["schema"] == "g3d.dev_run/1"
    assert status["phase"] == "starting"
    assert status["started_at"] is None and status["elapsed_seconds"] == 0
    assert status["settled_usd"] is None
    assert status["cutoff_usd"] == "27.50"
    assert status["offline_test_mode"] is False


def test_running_uses_runner_start_not_heartbeat_state_start(record):
    runner(record)
    heartbeat(record, state="tool:temple_stage")
    status = read_run_status(record, now=NOW)
    assert status["phase"] == "running" and status["loop_state"] == "tool:temple_stage"
    assert status["elapsed_seconds"] == 120
    assert [status[k] for k in ("settled_usd", "held_usd", "unknown_usd")] == ["3.400001", "2.000002", "0.100003"]


def test_old_heartbeat_and_old_current_delivery_are_never_this_run(record):
    heartbeat(record, run_id="previous-run")
    publish(record, run_id="previous-run", attempt=False)
    record["delivery_key"] = "old_key"
    status = read_run_status(record, now=NOW)
    assert status["phase"] == "starting" and status["settled_usd"] is None
    assert status["delivery_key"] is None and find_run_delivery(record) is None


def test_result_remains_finalizing_with_old_delivery_and_uses_shared_guard_only(record):
    runner(record)
    heartbeat(record)
    result(record, status="budget_limit", stages=[{"budget": {"settled_micro_usd": 4_000_000}}])
    publish(record, run_id="previous-run", attempt=False)
    status = read_run_status(record, now=NOW)
    assert status["phase"] == "finalizing" and status["terminal_status"] == "budget_limit"
    assert status["elapsed_seconds"] == 120
    assert status["settled_usd"] == "5.000007" and status["held_usd"] == "0"
    assert status["delivery_key"] is None


@pytest.mark.parametrize("terminal", ["completed", "budget_limit", "provider_unknown", "failed_worker", "stopped"])
def test_terminal_heartbeat_precedes_result_and_remains_finalizing(record, terminal):
    runner(record)
    heartbeat(record, state=terminal, final_status=terminal)
    assert read_run_status(record, now=NOW)["phase"] == "finalizing"


@pytest.mark.parametrize("finished,status", [(True, "completed"), (False, "budget_limit"), (False, "failed_worker")])
def test_finished_or_unfinished_publication_ends_with_actual_loop_status(record, finished, status):
    runner(record, exit_code=0, finished_utc=(START + timedelta(seconds=110)).isoformat())
    result(record, status=status)
    delivery_id = publish(record, finished=finished)
    record["delivery_key"] = "delivery_123"
    dto = read_run_status(record, now=NOW)
    assert dto["phase"] == "ended" and dto["terminal_status"] == status
    assert dto["delivery_key"] == "delivery_123" and dto["elapsed_seconds"] == 110
    assert find_run_delivery(record)["delivery_id"] == delivery_id


def test_current_publication_can_precede_delivery_attempt(record):
    result(record)
    delivery_id = publish(record, attempt=False)
    assert read_run_status(record, now=NOW)["phase"] == "ended"
    assert find_run_delivery(record)["delivery_id"] == delivery_id


def test_run_specific_attempt_survives_current_moving_to_later_run(record):
    delivery_id = publish(record, current=False)
    publish(record, run_id="later-run", attempt=False)
    assert find_run_delivery(record)["delivery_id"] == delivery_id


@pytest.mark.parametrize("status", ["no_usable_checkpoint", "draft_failed"])
def test_explicit_delivery_refusal_is_failed_not_old_delivery(record, status):
    result(record, status="budget_limit")
    publish(record, run_id="previous-run", attempt=False)
    write(root_of(record), f"runs/{RUN}/delivery.json", {"run_id": RUN, "status": status, "reason": "No checkpoint."})
    dto = read_run_status(record, now=NOW)
    assert dto["phase"] == "failed" and dto["error"] == "No checkpoint."
    assert dto["delivery_key"] is None


@pytest.mark.parametrize("code", [0, 1, 70])
def test_exited_runner_without_publication_reports_failure(record, code):
    runner(record, exit_code=code, finished_utc=NOW.isoformat())
    result(record)
    dto = read_run_status(record, now=NOW)
    assert dto["phase"] == "failed"
    assert "published" in dto["error"]


def test_runner_failure_before_loop_has_clear_error(record):
    runner(record, exit_code=70, error="Could not start Python.")
    assert read_run_status(record, now=NOW)["error"] == "Could not start Python."


def test_understanding_requires_own_usage_and_delivered_matching_bytes(record):
    record.update(kind="understand", edit_key="edit_123")
    runner(record)
    result(record)
    understanding(record)
    dto = read_run_status(record, now=NOW)
    assert dto["phase"] == "ended" and dto["edit_key"] == "edit_123"
    assert dto["delivery_key"] is None


@pytest.mark.parametrize("kwargs", [{"usage_run": "previous-run"}, {"delivered": False}, {"tamper": True}])
def test_understanding_stale_unshown_or_changed_record_stays_finalizing(record, kwargs):
    record.update(kind="understand", edit_key="edit_123")
    runner(record)
    result(record)
    understanding(record, **kwargs)
    assert read_run_status(record, now=NOW)["phase"] == "finalizing"


def test_understanding_usage_can_supply_settled_when_result_budget_is_unavailable(record):
    record.update(kind="understand")
    result(record, budget=None)
    understanding(record)
    dto = read_run_status(record, now=NOW)
    assert dto["settled_usd"] == "2.500005"
    assert dto["unknown_usd"] is None


def test_estimate_is_decimal_from_prepared_request_and_stop_is_persistent(record):
    write(root_of(record), f"runs/{RUN}/approval-request.json", {
        "estimate": {"expected_micro_usd": 19_876_543}})
    (root_of(record) / "stop.requested").write_text("Stop", encoding="utf-8")
    dto = read_run_status(record, now=NOW)
    assert dto["estimate_usd"] == "19.876543" and dto["stop_requested"] is True


def test_observed_stop_survives_core_removing_stop_file(record):
    result(record, status="stopped", stop_observed=True)
    assert read_run_status(record, now=NOW)["stop_requested"] is True


def test_later_stop_file_does_not_relabel_finished_run_as_stopped(record):
    result(record)
    publish(record)
    (root_of(record) / "stop.requested").write_text("Later run", encoding="utf-8")
    assert read_run_status(record, now=NOW)["stop_requested"] is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "not money"])
def test_invalid_money_never_reaches_json_as_nonfinite_or_false_zero(record, value):
    heartbeat(record, settled_usd=value)
    assert read_run_status(record, now=NOW)["settled_usd"] is None


def test_offline_mode_only_comes_from_injected_registry_record(record):
    heartbeat(record, offline_test_mode=True)
    result(record, offline_test_mode=True)
    assert read_run_status(record, now=NOW)["offline_test_mode"] is False
    record["offline_test_mode"] = True
    assert read_run_status(record, now=NOW)["offline_test_mode"] is True


def test_corrupt_or_nonobject_files_are_unavailable_without_crashing(record):
    root = root_of(record)
    (root / "heartbeat.json").write_text("{broken", encoding="utf-8")
    write(root, f"runs/{RUN}/runner-status.json", [1, 2])
    assert read_run_status(record, now=NOW)["phase"] == "starting"


def test_delivery_pointer_hash_mismatch_is_not_reported_as_ready(record):
    result(record)
    delivery_id = publish(record)
    write(root_of(record), "deliveries/current.json", {"id": delivery_id, "glb_sha256": SHA,
          "manifest_sha256": "b" * 64})
    assert find_run_delivery(record) is None
    assert read_run_status(record, now=NOW)["phase"] == "finalizing"


def test_poll_never_opens_model_report_ledger_or_walks_directories(record, monkeypatch):
    from pathlib import Path
    publish(record)
    reads = []
    original = Path.read_bytes

    def read_bytes(path):
        reads.append(path.name)
        assert path.name.endswith(".json") and path.name != "report.json"
        assert "budget" not in path.parts and "session" not in path.parts
        return original(path)

    def no_walk(*args, **kwargs):
        raise AssertionError("status must not scan directories")

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "glob", no_walk)
    monkeypatch.setattr(Path, "rglob", no_walk)
    assert read_run_status(record, now=NOW)["phase"] == "ended"
    assert "manifest.json" in reads


@pytest.mark.parametrize("run_id", ["../outside", "..", "C:\\outside", "a/b"])
def test_registered_run_id_must_be_one_safe_path_segment(record, run_id):
    record["run_id"] = run_id
    with pytest.raises(ValueError, match="run id"):
        read_run_status(record, now=NOW)
