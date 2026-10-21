"""Run/delivery API contracts with isolated files and a non-spending launcher."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from starlette.testclient import TestClient

from glasses3d.dev_ui.contracts import APIError, Config
from glasses3d.dev_ui.server import create_app


ORIGIN = "http://127.0.0.1:8271"


def test_stop_an_old_finished_run_never_stops_later_run(setup):
    _, launcher, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        run = app.state.service.get("runs", started["run_key"])
        root = Path(run["root"])
        write_json(root / "runs" / run["run_id"] / "runner-status.json", {"exit_code": 1})
        write_json(root / "heartbeat.json", {"run_id": "later-run", "state": "model_request"})
        result = browser.post(f'/api/runs/{started["run_key"]}/stop', json={})
        assert result.status_code == 200
        assert result.json()["phase"] == "failed"
        assert launcher.stops == []


def test_stop_refuses_different_active_run_identity(setup):
    _, launcher, _, app = setup
    with client(app) as browser:
        started = start(browser).json()
        run = app.state.service.get("runs", started["run_key"])
        write_json(Path(run["root"]) / "heartbeat.json", {"run_id": "later-run", "state": "model_request"})
        result = browser.post(f'/api/runs/{started["run_key"]}/stop', json={})
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "stale_run"
        assert launcher.stops == []


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class FakeLauncher:
    """Records calls only. There is no approval writer, transport, or subprocess."""

    def __init__(self, *, offline=True):
        self.offline_test_mode = offline
        self.busy = False
        self.fail_next_launch = False
        self.prepares, self.approvals, self.launches, self.stops = [], [], [], []

    def check_available(self):
        if self.busy:
            raise APIError("paid_run_busy", "Another paid run is in progress", 409)

    def prepare(self, root, amount, *, run_id, kind):
        assert kind == "job"
        self.check_available()
        preparation = (Path(root), amount, run_id)
        if preparation not in self.prepares:
            self.prepares.append(preparation)
        return {"run_id": run_id, "cutoff_usd": amount, "estimate_usd": "8.123456"}

    def approve(self, root, run_id, amount, button, subject):
        self.check_available()
        self.approvals.append((Path(root), run_id, amount, button, subject))
        return {"fake_only": True}

    def launch(self, root, run_id):
        if self.fail_next_launch:
            self.fail_next_launch = False
            raise APIError("launch_failed", "Synthetic launch failed before detaching.", 409)
        self.launches.append((Path(root), run_id))
        return {"status": "started", "run_id": run_id, "fake_only": True}

    def stop(self, root, run_id):
        self.stops.append((Path(root), run_id))
        (Path(root) / "stop.requested").write_text("stop", encoding="utf-8")
        return {"status": "stop_requested"}


class FakeCore:
    def __init__(self):
        self.created = []
        self.loads = []
        self.reviews = []

    def create_job(self, *, images, views, name, description, specs, jobs_root):
        number = len(self.created) + 1
        root = jobs_root / f"synthetic-job-{number}"
        root.mkdir(parents=True)
        shutil.copytree(images, root / "images")
        view_bytes = views.read_bytes()
        specs_bytes = specs.read_bytes() if specs else b"{}"
        self.created.append({"root": root, "name": name, "description": description,
                             "views_bytes": view_bytes, "specs_bytes": specs_bytes})
        write_json(root / "job.json", {"id": root.name, "name": name, "description": description,
            "created": f"2026-10-19T10:00:{number:02d}+00:00", "specs_json": specs_bytes.decode("utf-8")})
        return root

    def load_delivery(self, root, delivery_id):
        self.loads.append((Path(root), delivery_id))
        directory = Path(root) / "deliveries" / delivery_id
        manifest = json.loads((directory / "manifest.json").read_bytes())
        report = json.loads((directory / "report.json").read_bytes())
        model = directory / "model.glb"
        if hashlib.sha256(model.read_bytes()).hexdigest() != manifest["glb_sha256"]:
            raise ValueError("Synthetic model hash changed")
        return {"id": delivery_id, "glb_sha256": manifest["glb_sha256"], "model": str(model),
                "manifest": str(directory / "manifest.json"), "report_data": report,
                "finished": report["finished"], "run_status": report["run_status"]}

    def review(self, root, delivery_id):
        self.reviews.append((Path(root), delivery_id))
        return {"url": "http://127.0.0.1:43210/?model=%2Fmodel.glb&name=Owner%20model&sha256=exact"}


@pytest.fixture
def setup(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("synthetic UI", encoding="utf-8")
    config = Config(port=8271, state_root=tmp_path / "state", jobs_root=tmp_path / "jobs", static_root=static)
    launcher, core = FakeLauncher(), FakeCore()
    app = create_app(config, launcher=launcher, core_adapter=core)
    return config, launcher, core, app


def client(app):
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def manifest(**changes):
    return {"name": "", "description": "  Owner's exact words.\nSecond line.  ",
            "specs_text": ' {\n "width_mm" : 145, "note": "owner text"\n}\n',
            "cutoff_usd": "27.50", "idempotency_key": "button-1",
            "photos": [{"field": "photo1", "filename": "Front Exact.png", "kind": "front", "side": "both"},
                       {"field": "photo2", "filename": "Side Exact.png", "kind": "side", "side": "none"}],
            **changes}


def start(browser, body=None, *, bytes_suffix=b""):
    body = manifest() if body is None else body
    files = {row["field"]: (row["filename"], b"synthetic-photo-" + row["field"].encode() + bytes_suffix, "image/png")
             for row in body["photos"]}
    return browser.post("/api/jobs/start", data={"manifest": json.dumps(body)}, files=files)


def publish(core, root, run_id, *, finished=True, status="completed", cost=None, created=False):
    data = b"glTF" + b"synthetic-dev-ui-test-only"
    sha = hashlib.sha256(data).hexdigest()
    delivery_id = run_id + "-" + sha
    directory = root / "deliveries" / delivery_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "model.glb").write_bytes(data)
    report = {"run_id": run_id, "job_id": root.name, "finished": finished, "accepted_finish": finished,
        "run_status": status, "glb_sha256": sha,
        "cost": {"settled_usd": "3.123456"} if cost is None else cost,
        "validate_spec": {"ok": True}, "seating": {"ok": True}, "print_check": {"ok": True},
        "arview": {"status": "ok", "exit_code": 0}}
    if created:
        report["created_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(directory / "report.json", report)
    digest = write_json(directory / "manifest.json", {"schema": "g3d.delivery/1", "id": delivery_id,
        "run_id": run_id, "job_id": root.name, "glb_sha256": sha, "model": "model.glb", "report": "report.json"})
    write_json(root / "deliveries/current.json", {"schema": "g3d.delivery_current/1", "id": delivery_id,
        "glb_sha256": sha, "manifest_sha256": digest})
    write_json(root / "runs" / run_id / "delivery.json", {
        "status": "delivered", "id": delivery_id, "manifest_sha256": digest})
    return delivery_id, data


def finish_result(root, run_id, *, status="completed"):
    write_json(root / "runs" / run_id / "result.json", {"run_id": run_id, "status": status,
        "started": "2026-10-19T10:00:00+00:00", "finished": "2026-10-19T10:02:00+00:00",
        "budget": {"settled_micro_usd": 3_123_456, "held_micro_usd": 0, "unknown_micro_usd": 1_000_001}})


def test_start_keeps_exact_inputs_and_prepares_one_typed_budget(setup):
    _, launcher, core, app = setup
    body = manifest()
    with client(app) as browser:
        response = start(browser, body)
    assert response.status_code == 200, response.text
    dto = response.json()
    assert set(dto) == {"run_key", "job_id", "run_id", "cutoff_usd"}
    assert dto["cutoff_usd"] == "27.50"
    created = core.created[0]
    assert created["description"] == body["description"]
    assert created["specs_bytes"] == body["specs_text"].encode("utf-8")
    assert created["name"] == "Product"
    assert sorted(path.name for path in (created["root"] / "images").iterdir()) == ["Front Exact.png", "Side Exact.png"]
    assert json.loads(created["views_bytes"]) == {
        "Front Exact.png": {"kind": "front", "side": "both"}, "Side Exact.png": {"kind": "side", "side": None}}
    assert len(launcher.prepares) == len(launcher.approvals) == len(launcher.launches) == 1
    assert launcher.approvals[0][2:] == ("27.50", "Start a paid run", dto["job_id"])


def test_identical_start_is_idempotent_across_server_restart(setup):
    config, launcher, core, app = setup
    with client(app) as browser:
        first = start(browser)
        repeated = start(browser)
    assert first.status_code == repeated.status_code == 200
    rebuilt = create_app(config, launcher=launcher, core_adapter=core)
    with client(rebuilt) as browser:
        restarted = start(browser)
    assert first.json() == repeated.json() == restarted.json()
    assert len(core.created) == len(launcher.prepares) == len(launcher.approvals) == len(launcher.launches) == 1


@pytest.mark.parametrize("changed", [{"description": "Changed words"}, {"cutoff_usd": "28"}])
def test_same_idempotency_key_cannot_change_approved_inputs(setup, changed):
    _, launcher, core, app = setup
    with client(app) as browser:
        assert start(browser).status_code == 200
        response = start(browser, manifest(**changed))
    assert response.status_code == 409 and response.json()["error"]["code"] == "idempotency_conflict"
    assert len(core.created) == len(launcher.approvals) == 1


def test_same_idempotency_key_binds_photo_bytes(setup):
    _, launcher, _, app = setup
    with client(app) as browser:
        assert start(browser).status_code == 200
        response = start(browser, bytes_suffix=b"changed")
    assert response.status_code == 409
    assert len(launcher.approvals) == 1


def test_busy_lock_refuses_before_any_job_or_approval(setup):
    _, launcher, core, app = setup
    launcher.busy = True
    with client(app) as browser:
        response = start(browser)
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Another paid run is in progress"
    assert core.created == launcher.prepares == launcher.approvals == launcher.launches == []


def test_startup_gap_refuses_second_paid_send_before_shared_lock_is_taken(setup):
    _, launcher, core, app = setup
    with client(app) as browser:
        assert start(browser).status_code == 200
        response = start(browser, manifest(idempotency_key="button-2"))
    assert response.status_code == 409
    assert len(core.created) == len(launcher.approvals) == 1


def test_failed_detach_can_retry_same_approval_without_creating_a_second_run(setup):
    _, launcher, core, app = setup
    launcher.fail_next_launch = True
    with client(app) as browser:
        failed = start(browser)
        retried = start(browser)
    assert failed.status_code == 409 and retried.status_code == 200
    assert len(core.created) == len(launcher.prepares) == len(launcher.approvals) == len(launcher.launches) == 1


def test_failed_detach_does_not_permanently_block_distinct_new_run(setup):
    _, launcher, core, app = setup
    launcher.fail_next_launch = True
    with client(app) as browser:
        assert start(browser).status_code == 409
        response = start(browser, manifest(idempotency_key="new-button"))
    assert response.status_code == 200, response.text
    assert len(core.created) == 2 and len(launcher.launches) == 1


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "not money", None, 20])
def test_invalid_cutoff_is_refused_before_approval(setup, value):
    _, launcher, core, app = setup
    with client(app) as browser:
        response = start(browser, manifest(cutoff_usd=value))
    assert response.status_code == 400
    assert core.created == launcher.approvals == []


@pytest.mark.parametrize("filename", ["../front.png", "C:\\front.png", "nested/front.png", "front.png:alternate", "CON.png"])
def test_filename_path_parts_are_refused(setup, filename):
    _, launcher, core, app = setup
    body = manifest()
    body["photos"][0]["filename"] = filename
    with client(app) as browser:
        response = start(browser, body)
    assert response.status_code == 400
    assert core.created == launcher.approvals == []


@pytest.mark.parametrize("specs", ["[]", '{"a": 1, "a": 2}', '{"a": NaN}'])
def test_invalid_specs_never_reaches_core_or_approval(setup, specs):
    _, launcher, core, app = setup
    with client(app) as browser:
        response = start(browser, manifest(specs_text=specs))
    assert response.status_code == 400
    assert core.created == launcher.approvals == []


def test_run_poll_is_cheap_owns_delivery_and_exposes_decimal_money(setup):
    _, launcher, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        root, run_id = core.created[0]["root"], started["run_id"]
        publish(core, root, "previous-run")
        finish_result(root, run_id, status="budget_limit")
        pending = browser.get(f'/api/runs/{started["run_key"]}').json()
        assert pending["phase"] == "finalizing" and pending["delivery_key"] is None
        publish(core, root, run_id, finished=False, status="budget_limit")
        ready = browser.get(f'/api/runs/{started["run_key"]}').json()
        assert ready["phase"] == "ended" and ready["delivery_key"]
        assert ready["settled_usd"] == "3.123456" and ready["held_usd"] == "0" and ready["unknown_usd"] == "1.000001"
        assert ready["estimate_usd"] == "8.123456" and ready["cutoff_usd"] == "27.50"
        assert ready["offline_test_mode"] is True
        assert core.loads == []  # Polling registers the immutable identity, not the heavy model/report.
        fetched = browser.get(f'/api/deliveries/{ready["delivery_key"]}').json()
        assert fetched["label"] == "not finished" and fetched["finished"] is False
        assert fetched["run_settled_usd"] == "3.123456"
        assert len(core.loads) == 1 and len(launcher.approvals) == 1


def test_delivery_model_and_ar_use_immutable_registered_bytes_and_cached_url(setup):
    _, _, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        root, run_id = core.created[0]["root"], started["run_id"]
        finish_result(root, run_id)
        delivery_id, expected = publish(core, root, run_id)
        key = browser.get(f'/api/runs/{started["run_key"]}').json()["delivery_key"]
        assert browser.get(f"/api/deliveries/{key}").status_code == 200
        model = browser.get(f"/api/deliveries/{key}/model.glb")
        assert model.content == expected and model.headers["content-type"] == "model/gltf-binary"
        first = browser.post(f"/api/deliveries/{key}/ar", json={})
        second = browser.post(f"/api/deliveries/{key}/ar", json={})
        assert first.json() == second.json() == {"url": "http://127.0.0.1:43210/?model=%2Fmodel.glb&name=Owner%20model&sha256=exact"}
        assert core.reviews == [(root, delivery_id)] and len(core.loads) == 1
        (root / "deliveries" / delivery_id / "model.glb").write_bytes(expected + b"changed")
        assert browser.get(f"/api/deliveries/{key}/model.glb").status_code == 400


def test_delivery_money_uses_micro_when_decimal_string_is_absent(setup):
    _, _, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        root, run_id = core.created[0]["root"], started["run_id"]
        publish(core, root, run_id, cost={"settled_micro_usd": 7_654_321})
        key = browser.get(f'/api/runs/{started["run_key"]}').json()["delivery_key"]
        response = browser.get(f"/api/deliveries/{key}")
    assert response.status_code == 200 and response.json()["run_settled_usd"] == "7.654321"


def test_stop_is_the_actual_registered_root_and_state_survives_restart(setup):
    config, launcher, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        response = browser.post(f'/api/runs/{started["run_key"]}/stop', json={})
    assert response.status_code == 200 and response.json()["stop_requested"] is True
    assert launcher.stops == [(core.created[0]["root"], started["run_id"])]
    with client(create_app(config, launcher=launcher, core_adapter=core)) as browser:
        assert browser.get(f'/api/runs/{started["run_key"]}').json()["stop_requested"] is True


def test_job_list_imports_existing_jobs_and_real_delivery_dates(setup):
    config, _, core, app = setup
    older = config.jobs_root / "existing-job"
    write_json(older / "job.json", {"id": older.name, "name": "Earlier owner job", "created": "2026-10-18T10:00:00+00:00"})
    publish(core, older, "older-run")
    with client(app) as browser:
        started = start(browser).json()
        root, run_id = core.created[0]["root"], started["run_id"]
        publish(core, root, run_id)
        assert [job["job_id"] for job in browser.get("/api/jobs").json()["jobs"]] == ["existing-job"]
        listed = browser.get("/api/jobs?show_test_jobs=1")
    assert listed.status_code == 200
    jobs = listed.json()["jobs"]
    assert [job["job_id"] for job in jobs] == [started["job_id"], "existing-job"]
    assert jobs[0]["runs"][0]["run_key"] == started["run_key"]
    assert jobs[1]["deliveries"][0]["verdict_label"] == "finished"
    for job in jobs:
        assert datetime.fromisoformat(job["deliveries"][0]["created_utc"]).tzinfo is not None


def test_offline_mode_cannot_be_enabled_by_http_payload_or_query(setup):
    config, launcher, core, _ = setup
    launcher.offline_test_mode = False
    app = create_app(config, launcher=launcher, core_adapter=core)
    with client(app) as browser:
        assert browser.get("/api/config?offline_test_mode=true").json()["offline_test_mode"] is False
        started = start(browser, manifest(offline_test_mode=True)).json()
        assert browser.get(f'/api/runs/{started["run_key"]}?offline_test_mode=true').json()["offline_test_mode"] is False
    assert len(launcher.approvals) == 1  # This is still our non-spending fake.
