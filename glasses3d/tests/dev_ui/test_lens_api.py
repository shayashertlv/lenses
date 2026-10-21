"""Lens-colour HTTP binding, local tasks and publication without model calls."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
from starlette.testclient import TestClient

from glasses3d.dev_ui.contracts import Config
from glasses3d.dev_ui.server import create_app
from glasses3d.dev_ui.tasks import execute_task


BASE = "http://127.0.0.1:8278"
VALUES = {"tint": [.2, .4, .6], "transmission": .7, "metallic": .2, "roughness": .1,
          "ior": 1.5, "iridescence": .6, "iridescence_ior": 1.3,
          "iridescence_thickness_min": 100., "iridescence_thickness_max": 400.}


class NoPaidCalls:
    offline_test_mode = True

    def __getattr__(self, name):
        raise AssertionError("Lens edits must not use the paid launcher: " + name)


class LensCore:
    def __init__(self):
        self.data, self.saves, self.previews = {}, [], []

    def make(self, root, origin="run"):
        folder = root / "deliveries/version-1"
        folder.mkdir(parents=True)
        model = folder / "model.glb"
        model.write_bytes(b"glTF-test-" + origin.encode())
        (folder / "manifest.json").write_text('{"id":"version-1"}')
        (root / "deliveries/current.json").write_text('{"id":"version-1"}')
        self.data[str(root)] = {"model": str(model), "glb_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                               "report_data": {"finished": True, "origin": origin, "accepted_finish": origin == "run",
                                               "created_utc": "2026-10-21T00:00:00Z"}}
        return root

    def load_delivery(self, root, identifier):
        assert identifier == "version-1"
        return copy.deepcopy(self.data[str(root)])

    def read_lens_materials(self, path):
        return {"materials": [{"material_index": 2, "name": "Shield", "values": copy.deepcopy(VALUES)}]}

    def patch_lens_glb(self, path, values):
        if set(values) != {2} or set(values[2]) != set(VALUES):
            raise ValueError("Choose a lens and supply every colour value")
        return b"glTFpatched", {}

    def save_lens_edit(self, root, identifier, values, destination=None):
        self.saves.append((root, identifier, values))
        return self.make(destination, "owner_lens_edit")

    def create_lens_preview(self, root, identifier, values, destination=None):
        self.previews.append((root, identifier, values))
        return {"url": "http://127.0.0.1:8299/?model=temporary"}


@pytest.fixture
def lens(tmp_path):
    config = Config(port=8278, state_root=tmp_path / "state", jobs_root=tmp_path / "jobs")
    core = LensCore()
    source = core.make(config.jobs_root / "source")
    photo = source / "photo.png"
    Image.new("RGB", (5, 5), "red").save(photo)
    (source / "job.json").write_text(json.dumps({"id": "source", "name": "Product", "images": [
        {"file": "photo.png", "sha256": hashlib.sha256(photo.read_bytes()).hexdigest(),
         "view_kind": "front", "side": "both", "original_name": "Owner photo"}]}))
    app = create_app(config, launcher=NoPaidCalls(), core_adapter=core, task_launcher=execute_task)
    service = app.state.service
    key = service.register_delivery(source, "version-1", job_id="source")
    body = {"source_sha256": core.data[str(source)]["glb_sha256"],
            "materials": [{"material_index": 2, "values": copy.deepcopy(VALUES)}], "idempotency_key": "one"}
    with TestClient(app, base_url=BASE, headers={"Origin": BASE}, raise_server_exceptions=False) as client:
        yield SimpleNamespace(core=core, client=client, service=service, key=key, body=body, source=source)


def test_panel_has_only_verified_photos_and_source(lens):
    response = lens.client.get(f"/api/deliveries/{lens.key}/lens-colour")
    assert response.status_code == 200
    data = response.json()
    assert data["source"]["glb_sha256"] == lens.body["source_sha256"]
    assert data["materials"][0]["values"] == VALUES
    assert data["photos"][0]["kind"] == "front"
    assert lens.client.get(data["photos"][0]["url"]).headers["content-type"] == "image/png"


def test_save_is_idempotent_new_version_and_visible_in_jobs(lens):
    before = {p.relative_to(lens.source): p.read_bytes() for p in lens.source.rglob("*") if p.is_file()}
    endpoint = f"/api/deliveries/{lens.key}/lens-colour/save"
    first = lens.client.post(endpoint, json=lens.body)
    assert first.status_code == 200, first.text
    second = lens.client.post(endpoint, json=lens.body)
    assert second.json() == first.json()
    task = lens.client.get("/api/tasks/" + first.json()["task_key"]).json()
    assert task["phase"] == "done", task
    result = lens.client.get("/api/deliveries/" + task["result"]["delivery_key"]).json()
    assert result["origin"] == "owner_lens_edit"
    assert result["label"] == "edited by you"
    assert not result["accepted_finish"]
    jobs = lens.client.get("/api/jobs").json()["jobs"]
    assert any(d["origin_label"] == "your edit" for j in jobs for d in j["deliveries"])
    assert len(lens.core.saves) == 1
    assert before == {p.relative_to(lens.source): p.read_bytes() for p in lens.source.rglob("*") if p.is_file()}
    # No native checkpoint is claimed, so later paid edits cannot drop the colours.
    refused = lens.client.post("/api/edit-drafts", json={"delivery_key": result["delivery_key"]})
    assert refused.status_code == 409


def test_preview_is_temporary_and_not_a_delivery(lens):
    initial = lens.service.registry.list("deliveries")
    response = lens.client.post(f"/api/deliveries/{lens.key}/lens-colour/preview", json=lens.body)
    assert response.status_code == 200
    task = lens.client.get("/api/tasks/" + response.json()["task_key"]).json()
    assert task["phase"] == "done"
    assert task["result"]["url"].startswith("http://127.0.0.1:")
    assert len(lens.service.registry.list("deliveries")) == len(initial)
    assert len(lens.core.previews) == 1 and not lens.core.saves


@pytest.mark.parametrize("mutation", ["stale", "missing", "duplicate", "not_lens", "unknown", "bool_index"])
def test_invalid_edits_never_launch(lens, mutation):
    body = copy.deepcopy(lens.body)
    if mutation == "stale": body["source_sha256"] = "0" * 64
    if mutation == "missing": body["materials"][0]["values"].pop("transmission")
    if mutation == "duplicate": body["materials"] *= 2
    if mutation == "not_lens": body["materials"][0]["material_index"] = 0
    if mutation == "bool_index": body["materials"][0]["material_index"] = True
    if mutation == "unknown": body["path"] = "outside"
    response = lens.client.post(f"/api/deliveries/{lens.key}/lens-colour/save", json=body)
    assert response.status_code in (400, 409), response.text
    assert not lens.core.saves and not lens.service.registry.list("tasks")


def test_reusing_button_with_other_values_refuses(lens):
    endpoint = f"/api/deliveries/{lens.key}/lens-colour/save"
    assert lens.client.post(endpoint, json=lens.body).status_code == 200
    lens.body["materials"][0]["values"]["transmission"] = .5
    response = lens.client.post(endpoint, json=lens.body)
    assert response.status_code == 409
    assert len(lens.core.saves) == 1


def test_mutated_photo_refuses(lens):
    (lens.source / "photo.png").write_bytes(b"changed")
    response = lens.client.get(f"/api/deliveries/{lens.key}/lens-colour")
    assert response.status_code == 400


def test_legacy_delivery_without_seating_can_open_panel(lens):
    lens.core.data[str(lens.source)]["report_data"]["seating"] = None
    response = lens.client.get(f"/api/deliveries/{lens.key}/lens-colour")
    assert response.status_code == 200, response.text


def test_verified_lens_provenance_overrides_discovered_run_label(lens):
    loaded = lens.core.data[str(lens.source)]
    folder = Path(loaded["model"]).parent
    application = {"schema": "g3d.owner_applied_preview/1", "origin": "owner_lens_edit",
                   "owner_lens_edit": {"schema": "g3d.owner_lens_edit/1", "native_scene_available": False}}
    payload = json.dumps(application).encode()
    path = folder / "owner-applied/report.json"
    path.parent.mkdir()
    path.write_bytes(payload)
    loaded["report"] = str(folder / "report.json")
    loaded["report_data"].pop("origin")
    loaded["report_data"]["owner_applied"] = {"path": "owner-applied/report.json", "sha256": hashlib.sha256(payload).hexdigest()}
    result = lens.client.get("/api/deliveries/" + lens.key).json()
    assert result["origin"] == "owner_lens_edit"
    assert result["label"] == "edited by you"
    assert lens.client.post("/api/edit-drafts", json={"delivery_key": lens.key}).status_code == 409


def test_saved_draft_cannot_launch_native_edit_from_lens_version(lens):
    lens.core.data[str(lens.source)]["report_data"]["origin"] = "owner_lens_edit"
    key = lens.service.registry.put("drafts", {"source": {"delivery_key": lens.key}, "items": [{}], "frozen": False})
    response = lens.client.post(f"/api/edit-drafts/{key}/send", json={"cutoff_usd": "10", "idempotency_key": "guard"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "native_edit_unavailable"
