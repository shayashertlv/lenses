"""Detached-task crash recovery with synthetic files, fake core, and no Blender."""
from types import SimpleNamespace

import pytest

from glasses3d.agent import procs
from glasses3d.dev_ui import tasks
from glasses3d.dev_ui.contracts import Config, now_iso
from glasses3d.dev_ui.registry import Registry

OLD = "2000-01-01T00:00:00+00:00"
CLAIM = {"pid": 1234567, "create_time": 1.0, "exe": "test-only"}


class FakeService:
    def __init__(self, config):
        self.config = config
        self.registry = Registry(config.state_root)
        self.calls = []
        self.error = None
        self.delivery_directory = False
        self.core = SimpleNamespace(apply_choices=self.apply_choices)

    def get(self, kind, key):
        return self.registry.get(kind, key)

    def apply_choices(self, root, sha, choices, skipped, confirmation, *, destination):
        self.calls.append((root, sha, choices, skipped, confirmation, destination))
        if self.error:
            raise self.error
        procs.write_json_atomic(destination / "deliveries/current.json", {"id": "delivery-1"})
        return destination / "deliveries/delivery-1" if self.delivery_directory else destination

    def understanding(self, key):
        return {"items": [{"item_id": "I1", "options": [{"option_id": "I1-B", "amount_text": "2 mm"}]}]}

    def register_delivery(self, root, delivery_id, **kwargs):
        found = self.registry.find("deliveries", root=str(root))
        if found:
            return found["key"]
        return self.registry.put("deliveries", {"root": str(root), "delivery_id": delivery_id,
                                                 **kwargs, "glb_sha256": "a" * 64})

    def delivery(self, key):
        return self.get("deliveries", key)


@pytest.fixture
def service(tmp_path, monkeypatch):
    config = Config(port=8278, state_root=tmp_path / "ui", jobs_root=tmp_path / "jobs")
    value = FakeService(config)
    monkeypatch.setattr(tasks, "_worker_identity", lambda: dict(CLAIM))
    monkeypatch.setattr(tasks, "_identity_alive", lambda identity: identity == CLAIM)
    return value


def new_task(service, **fields):
    row = {"task_key": "task_test_1", "kind": "apply_picks", "phase": "queued", "started_at": None,
           "created_utc": OLD, "progress": "Queued", "result": None, "error": None,
           "edit_key": "edit_test_1", "round_root": str(service.config.state_root / "round"), "job_id": "job_test_1",
           "confirmation_text": 'Dev UI: owner pressed "Apply my picks": I1=I1-B',
           "body": {"understanding_sha256": "b" * 64, "choices": [{"item_id": "I1", "option_id": "I1-B"}],
                    "skipped": []}, **fields}
    service.registry.put("tasks", row, row["task_key"])
    return row["task_key"]


@pytest.mark.parametrize("directory", [False, True])
def test_apply_success_once_supports_both_core_return_shapes(service, directory):
    service.delivery_directory = directory
    key = new_task(service)
    result = tasks.execute_task(service, key)
    assert result["phase"] == "done"
    assert result["recoverable"] is False
    assert result["worker_identity"] == CLAIM
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call[1:5] == ("b" * 64, [{"item_id": "I1", "option_id": "I1-B"}], [],
                          'Dev UI: owner pressed "Apply my picks": I1=I1-B')
    assert call[5] == service.config.state_root / "applied" / key
    delivery = service.delivery(result["result"]["delivery_key"])
    assert delivery["applied"] == [{"item_id": "I1", "option_id": "I1-B", "amount_text": "2 mm"}]
    assert delivery["delivery_id"] == ("delivery-1" if directory else None)
    assert tasks.execute_task(service, key)["phase"] == "done"
    assert len(service.calls) == len(service.registry.list("drafts")) == 1


def test_actual_core_failure_stays_terminal(service):
    service.error = ValueError("sealed choice failed its hard check")
    key = new_task(service)
    result = tasks.execute_task(service, key)
    assert result["phase"] == "failed" and result["recoverable"] is False
    assert result["error"] == "sealed choice failed its hard check"
    assert tasks.reconcile_task(service, key)["recoverable"] is False
    tasks.execute_task(service, key)
    assert len(service.calls) == 1


def test_live_worker_is_not_repeated_even_with_stale_exit_status(service):
    key = new_task(service, phase="running", worker_identity=CLAIM, started_at=OLD)
    procs.write_json_atomic(tasks._folder(service.config, key) / "runner-status.json", {"exit_code": 1})
    assert tasks.reconcile_task(service, key)["phase"] == "running"
    assert tasks.execute_task(service, key)["phase"] == "running"
    assert not service.calls


def test_interrupted_running_task_recovers_existing_delivery_without_reapply(service):
    key = new_task(service, phase="running", worker_identity={"pid": 9999, "create_time": 1}, started_at=OLD)
    procs.write_json_atomic(service.config.state_root / "applied" / key / "deliveries/current.json", {"id": "delivery-1"})
    reconciled = tasks.reconcile_task(service, key)
    assert reconciled["phase"] == "failed" and reconciled["recoverable"] is True
    assert "interrupted" in reconciled["error"]
    assert tasks.execute_task(service, key)["phase"] == "done"
    assert not service.calls


def test_failed_before_worker_claim_is_recoverable(service):
    key = new_task(service, launched=True, launch_requested_at=OLD)
    procs.write_json_atomic(tasks._folder(service.config, key) / "runner-status.json",
                            {"exit_code": 2, "finished_utc": now_iso()})
    result = tasks.reconcile_task(service, key)
    assert result["phase"] == "failed" and result["recoverable"] is True
    assert "exit 2" in result["error"]
    assert not result["launched"]


def test_fresh_unpublished_startup_has_grace_but_does_not_wait_forever(service):
    key = new_task(service, launch_requested_at=now_iso())
    assert tasks.reconcile_task(service, key)["phase"] == "queued"
    row = service.get("tasks", key)
    row["launch_requested_at"] = OLD
    service.registry.put("tasks", row, key)
    assert tasks.reconcile_task(service, key)["recoverable"] is True


def test_new_unlaunched_queue_is_not_marked_interrupted(service):
    key = new_task(service)
    assert tasks.reconcile_task(service, key)["phase"] == "queued"


def test_launch_persists_receipt_and_uses_detached_keeper_default(service, monkeypatch):
    key = new_task(service)
    calls = []
    receipt = {"pid": 7654321, "create_time": 11.0, "method": "fake", "status_file": "fake.json"}
    def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(record=lambda: receipt)
    monkeypatch.setattr(procs, "spawn_detached", spawn)
    tasks.launch_task(service.config, key)
    saved = service.get("tasks", key)
    assert saved["launched"] and saved["launch"] == receipt
    assert saved["launch_requested_at"] and saved["launched_at"]
    assert procs.read_json(tasks._folder(service.config, key) / "launch.json") == receipt
    assert calls[0][0][0][:5] == [str(service.config.python), "-B", "-m", "glasses3d.dev_ui", "task"]
    assert "python" not in calls[0][1]  # procs uses its correctly identified base-interpreter keeper
    with pytest.raises(ValueError, match="already launched"):
        tasks.launch_task(service.config, key)
    assert len(calls) == 1


@pytest.mark.parametrize("phase", ["running", "done", "failed"])
def test_launch_does_not_repeat_active_or_terminal_task(service, monkeypatch, phase):
    key = new_task(service, phase=phase)
    monkeypatch.setattr(procs, "spawn_detached", lambda *args, **kwargs: pytest.fail("duplicate task launch"))
    with pytest.raises(ValueError, match="already launched or terminal"):
        tasks.launch_task(service.config, key)


def test_launch_receipt_and_status_keep_live_detached_task_queued(service, monkeypatch):
    key = new_task(service, launched=True, launch={"pid": 7654321, "create_time": 11.0})
    monkeypatch.setattr(tasks, "_identity_alive", lambda identity: isinstance(identity, dict) and identity.get("pid") == 7654321)
    assert tasks.reconcile_task(service, key)["phase"] == "queued"
    # If the HTTP caller crashed before retaining the receipt, recover the keeper from its own status.
    task = service.get("tasks", key); task.pop("launch"); service.registry.put("tasks", task, key)
    folder = tasks._folder(service.config, key)
    procs.write_json_atomic(folder / "runner-status.json", {"keeper_pid": 7654321, "keeper_create_time": 11.0,
        "argv": [str(service.config.python), "-B", "-m", "glasses3d.dev_ui", "task", str(folder / "task.json")]})
    assert tasks.reconcile_task(service, key)["phase"] == "queued"


def test_launch_failure_preserves_recovery_marker(service, monkeypatch):
    key = new_task(service)
    def fail(*args, **kwargs):
        raise procs.DetachError("synthetic detach failure")
    monkeypatch.setattr(procs, "spawn_detached", fail)
    with pytest.raises(procs.DetachError):
        tasks.launch_task(service.config, key)
    assert service.get("tasks", key)["launch_requested_at"]
    assert tasks.reconcile_task(service, key)["phase"] == "queued"


def test_crash_after_draft_write_reuses_same_delivery_and_draft(service, monkeypatch):
    key = new_task(service)
    original_put = service.registry.put
    def crash_on_done(kind, row, key=None):
        if kind == "tasks" and row.get("phase") == "done":
            raise SystemExit("simulated process death before terminal task publication")
        return original_put(kind, row, key)
    monkeypatch.setattr(service.registry, "put", crash_on_done)
    with pytest.raises(SystemExit):
        tasks.execute_task(service, key)
    assert len(service.calls) == len(service.registry.list("drafts")) == 1
    first_draft = service.registry.list("drafts")[0]["key"]
    monkeypatch.setattr(service.registry, "put", original_put)
    monkeypatch.setattr(tasks, "_identity_alive", lambda identity: False)
    result = tasks.execute_task(service, key)
    assert result["phase"] == "done" and result["draft_id"] == first_draft
    assert len(service.calls) == len(service.registry.list("drafts")) == 1


def test_production_worker_refuses_offline_task(service):
    key = new_task(service, offline_test_mode=True)
    file = tasks._folder(service.config, key) / "task.json"
    procs.write_json_atomic(file, {"config": {"state_root": str(service.config.state_root)}, "task_key": key})
    with pytest.raises(ValueError, match="Offline tasks require"):
        tasks.run_task_file(file)
