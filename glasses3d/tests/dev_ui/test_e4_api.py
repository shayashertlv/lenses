"""E4 API orchestration: role edits, immutable versions and native Astra authority."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from glasses3d.dev_ui.contracts import Config
from glasses3d.dev_ui.server import create_app
from glasses3d.dev_ui.tasks import execute_task
from glasses3d.tests.dev_ui.test_api_edits import EditCore, EditLauncher, _ok, _add, _capture, _apply_body
from glasses3d.tests.dev_ui.test_lens_api import VALUES

BASE = "http://127.0.0.1:8274"


class AppearanceCore(EditCore):
    def __init__(self, root):
        super().__init__(root)
        self.looks, self.look_saves, self.mappings = {}, [], []
        self.skip_mapping = False

    def read_look_materials(self, model):
        return {"materials": [{"material_index": 0, "name": "Clear frame", "role": "frame",
            "values": copy.deepcopy(VALUES), "gradient": None, "has_colour_texture": False,
            "can_transmission": True, "can_gradient": False},
            {"material_index": 2, "name": "Lens", "role": "lens", "values": copy.deepcopy(VALUES),
             "gradient": None, "has_colour_texture": False, "can_transmission": True, "can_gradient": True}],
            "refusals": [{"name": "Shared", "reason": "This material is shared by Frame and Temples."}]}

    def patch_look_glb(self, model, values):
        if not values or set(values) - {0, 2}:
            raise ValueError("Only model-owned, unshared materials may be changed.")
        if any(set(row) != set(VALUES) | {"gradient"} for row in values.values()):
            raise ValueError("Send every material control value.")
        return b"glTF-exact-owner-preview", {"source": str(model)}

    def build_look(self, model, values):
        return {"schema": "fixture.look/1", "values": copy.deepcopy(values)}

    def map_look(self, look, model, *, mode, same_job):
        self.mappings.append((copy.deepcopy(look), model, mode, same_job))
        if not same_job or self.skip_mapping:
            return {"values": {}, "applied": [], "skipped": [{"name": "Clear frame", "reason": "No matching material."}]}
        return {"values": copy.deepcopy(look["values"]), "applied": [{"name": "Lens"}], "skipped": []}

    def save_look_edit(self, root, identifier, values, destination=None, *, look=None, mapping=None):
        self.look_saves.append((str(root), identifier, copy.deepcopy(values), copy.deepcopy(mapping)))
        destination = Path(destination)
        if destination.exists():
            return destination
        self.make_delivery(destination, identifier="owner-1", payload=b"glTF-owner-appearance")
        self.deliveries[(str(destination), "owner-1")]["report_data"].update(origin="owner_look_edit", accepted_finish=False)
        binding = self.looks.get((str(root), identifier))
        self.looks[(str(destination), "owner-1")] = {
            "look": look or self.build_look(None, values), "native_source": binding["native_source"] if binding else
            {"root": str(root), "delivery_id": identifier}, "skipped": (mapping or {}).get("skipped", [])}
        return destination

    def create_look_preview(self, root, identifier, values, destination=None):
        return {"url": "http://127.0.0.1:8299/?model=temporary"}

    def read_saved_look(self, root, identifier):
        return copy.deepcopy(self.looks.get((str(root), identifier)))

    def resolve_native_source(self, root, identifier):
        return self.read_saved_look(root, identifier)["native_source"]

    def photo_cameras(self, root, identifier):
        return {}


@pytest.fixture
def appearance(tmp_path):
    config = Config(port=8274, state_root=tmp_path / "state", jobs_root=tmp_path / "jobs")
    core, launcher = AppearanceCore(tmp_path / "core"), EditLauncher()
    source = core.make_delivery(config.jobs_root / "source")
    (source / "job.json").write_text(json.dumps({"id": "fixture-job", "name": "Fixture", "images": []}))
    app = create_app(config, launcher=launcher, core_adapter=core, task_launcher=execute_task)
    service = app.state.service
    key = service.register_delivery(source, "delivery-1", job_id="fixture-job")
    body = {"source_sha256": service.delivery(key)["glb_sha256"], "materials": [
        {"material_index": 2, "values": {**copy.deepcopy(VALUES), "gradient": None}}], "idempotency_key": "save"}
    with TestClient(app, base_url=BASE, headers={"Origin": BASE}, raise_server_exceptions=False) as client:
        yield SimpleNamespace(core=core, launcher=launcher, app=app, service=service, client=client,
                              source=source, config=config, delivery_key=key, key=key, body=body)


def _save(fixture):
    task = _ok(fixture.client.post(f"/api/deliveries/{fixture.key}/appearance-save", json=fixture.body))
    result = _ok(fixture.client.get("/api/tasks/" + task["task_key"]))
    assert result["phase"] == "done", result
    return result["result"]["delivery_key"]


def test_role_metadata_and_immutable_versions(appearance):
    saved = _save(appearance)
    other = appearance.core.make_delivery(appearance.config.jobs_root / "other", identifier="other-1")
    (other / "job.json").write_text(json.dumps({"id": "other-job", "name": "Other model", "images": []}))
    appearance.service.register_delivery(other, "other-1", job_id="other-job")
    dto = _ok(appearance.client.get(f"/api/deliveries/{appearance.key}/appearance"))
    assert dto["roles"] == [{"role": "lens", "label": "Lenses"}, {"role": "frame", "label": "Frame"}]
    assert dto["materials"][0]["can_transmission"] is True
    assert dto["refusals"][0]["reason"] == "This material is shared by Frame and Temples."
    assert {row["label"] for row in dto["versions"]} == {"your edit", "Astra delivery-1"}
    assert {row["delivery_key"] for row in dto["versions"]} == {saved, appearance.key}
    assert {row["label"] for row in dto["copy_sources"]} == {
        "Fixture — your edit", "Fixture — Astra delivery-1", "Other model — Astra other-1"}
    assert not appearance.launcher.approvals


def test_live_model_uses_exact_backend_patch_without_publication(appearance):
    body = {k: value for k, value in appearance.body.items() if k != "idempotency_key"}
    response = appearance.client.post(f"/api/deliveries/{appearance.key}/appearance-model", json=body)
    assert response.status_code == 200 and response.content == b"glTF-exact-owner-preview"
    assert response.headers["content-type"] == "model/gltf-binary"
    assert not appearance.core.look_saves and not appearance.service.registry.list("tasks")
    assert not appearance.launcher.approvals


@pytest.mark.parametrize("mutation", ["stale", "duplicate", "bool_index", "extra", "partial", "shared"])
def test_invalid_appearance_never_queues(appearance, mutation):
    body = copy.deepcopy(appearance.body)
    if mutation == "stale": body["source_sha256"] = "0" * 64
    if mutation == "duplicate": body["materials"] *= 2
    if mutation == "bool_index": body["materials"][0]["material_index"] = True
    if mutation == "extra": body["destination"] = "anywhere"
    if mutation == "partial": del body["materials"][0]["values"]["ior"]
    if mutation == "shared": body["materials"][0]["material_index"] = 9
    result = appearance.client.post(f"/api/deliveries/{appearance.key}/appearance-save", json=body)
    assert result.status_code in (400, 409)
    assert not appearance.core.look_saves and not appearance.service.registry.list("tasks")


def test_copy_is_role_scoped_and_cross_job_mapping_is_explicit(appearance):
    other = appearance.core.make_delivery(appearance.config.jobs_root / "other", identifier="other-1")
    other_key = appearance.service.register_delivery(other, "other-1", job_id="other-job")
    body = {"from_delivery_key": other_key, "role": "frame", "source_sha256": appearance.body["source_sha256"]}
    copied = _ok(appearance.client.post(f"/api/deliveries/{appearance.key}/appearance-copy", json=body))
    assert copied["materials"] == [] and copied["skipped"][0]["reason"] == "No matching material."
    look, _, mode, same_job = appearance.core.mappings[-1]
    assert mode == "copy" and same_job is False and set(look["values"]) == {0}
    assert not appearance.core.look_saves and not appearance.launcher.approvals


def test_shape_draft_and_capture_use_native_authority_and_keep_look_binding(appearance):
    saved = _save(appearance)
    draft = _ok(appearance.client.post("/api/edit-drafts", json={"delivery_key": saved}))
    assert draft["source"]["delivery_key"] == appearance.key
    assert draft["source"]["glb_sha256"] == appearance.body["source_sha256"]
    assert draft["appearance_source"]["delivery_key"] == saved
    assert "reapplied" in draft["look_notice"]
    _ok(_add(appearance, draft))
    assert appearance.core.validated[0]["source"] == draft["source"]
    sent = _ok(appearance.client.post(f"/api/edit-drafts/{draft['draft_id']}/send",
                                    json={"cutoff_usd": "27.50", "idempotency_key": "send"}))
    assert appearance.core.created[0][:2] == (appearance.source, "delivery-1")
    assert appearance.service.get("edits", sent["edit_key"])["appearance_source"] == draft["appearance_source"]
    assert appearance.launcher.approvals[0][2:4] == ("27.50", "Send to Astra (paid)")


def test_changed_saved_look_refuses_before_paid_send(appearance):
    saved = _save(appearance)
    draft = _ok(appearance.client.post("/api/edit-drafts", json={"delivery_key": saved}))
    _ok(_add(appearance, draft))
    stored = appearance.service.get("drafts", draft["draft_id"])
    stored["appearance_source"]["glb_sha256"] = "0" * 64
    appearance.service.registry.put("drafts", stored, draft["draft_id"])
    response = appearance.client.post(f"/api/edit-drafts/{draft['draft_id']}/send",
                                     json={"cutoff_usd": "10", "idempotency_key": "stale"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "stale_source"
    assert not appearance.launcher.prepares and not appearance.launcher.approvals
    assert not appearance.core.created


@pytest.mark.parametrize("skip", [False, True])
def test_apply_picks_reapplies_look_and_preserves_native_delivery(appearance, skip):
    saved = _save(appearance)
    appearance.core.skip_mapping = skip
    draft = _ok(appearance.client.post("/api/edit-drafts", json={"delivery_key": saved}))
    _ok(_add(appearance, draft))
    sent = _ok(appearance.client.post(f"/api/edit-drafts/{draft['draft_id']}/send",
                                    json={"cutoff_usd": "10", "idempotency_key": "understand"}))
    body = _apply_body(); body["skipped"] = []
    requested = _ok(appearance.client.post(f"/api/edits/{sent['edit_key']}/apply", json=body))
    task = _ok(appearance.client.get("/api/tasks/" + requested["task_key"]))
    assert task["phase"] == "done", task
    final = appearance.service.delivery(task["result"]["delivery_key"])
    native = appearance.service.delivery(task["result"]["native_delivery_key"])
    assert final["origin"] == "owner_look_edit" and native["origin"] == "applied_picks"
    assert native["version_label"] == "your edit"
    assert bool(task["result"]["skipped"]) == skip
    assert len(appearance.core.look_saves) == 2
    assert appearance.core.look_saves[-1][3]["skipped"] == task["result"]["skipped"]
    reopened = _ok(appearance.client.post("/api/edit-drafts", json={"delivery_key": final["delivery_key"]}))
    assert reopened["source"]["delivery_key"] == native["delivery_key"]
    assert reopened["appearance_source"]["delivery_key"] == final["delivery_key"]
    assert len(appearance.launcher.approvals) == 1  # Reapplication never spends.


@pytest.mark.parametrize("failure_stage", ["mapping", "save"])
def test_apply_keeps_native_result_and_visible_warning_if_look_fails(appearance, monkeypatch, failure_stage):
    saved = _save(appearance)
    draft = _ok(appearance.client.post("/api/edit-drafts", json={"delivery_key": saved}))
    _ok(_add(appearance, draft))
    sent = _ok(appearance.client.post(f"/api/edit-drafts/{draft['draft_id']}/send",
                                    json={"cutoff_usd": "10", "idempotency_key": "understand"}))
    def fail(*args, **kwargs):
        raise ValueError("The existing appearance check refused this copy.")
    monkeypatch.setattr(appearance.core, "map_look" if failure_stage == "mapping" else "save_look_edit", fail)
    before = {str(path): path.read_bytes() for path in appearance.source.rglob("*") if path.is_file()}
    body = _apply_body(); body["skipped"] = []
    requested = _ok(appearance.client.post(f"/api/edits/{sent['edit_key']}/apply", json=body))
    task = _ok(appearance.client.get("/api/tasks/" + requested["task_key"]))
    assert task["phase"] == "done", task
    assert task["progress"] == "Picks applied; saved look could not be reapplied"
    assert task["result"]["delivery_key"] == task["result"]["native_delivery_key"]
    assert task["result"]["look_delivery_key"] == saved
    assert "appearance check refused" in task["result"]["appearance_error"]
    native_key = task["result"]["delivery_key"]
    native = _ok(appearance.client.get("/api/deliveries/" + native_key))
    assert native["origin"] == "applied_picks" and native["version_label"] == "your edit"
    assert native["appearance_error"] == task["result"]["appearance_error"]
    assert appearance.client.get(native["model_url"]).content == b"glTFsynthetic-applied"
    general = _ok(appearance.client.get(f"/api/deliveries/{native_key}/appearance"))
    assert general["appearance_error"] == native["appearance_error"]
    assert {"name": "Saved look", "reason": native["appearance_error"]} in general["refusals"]
    reopened = _ok(appearance.client.post("/api/edit-drafts", json={"delivery_key": native_key}))
    assert reopened["source"]["delivery_key"] == native_key
    assert len(appearance.launcher.approvals) == 1 and len(appearance.core.applied) == 1
    assert before == {str(path): path.read_bytes() for path in appearance.source.rglob("*") if path.is_file()}


def test_full_edit_result_reapplies_once_without_paid_request(appearance, monkeypatch):
    saved = _save(appearance)
    native_root = appearance.core.make_delivery(appearance.config.jobs_root / "full-result", identifier="full-1")
    edit_key = appearance.service.registry.put("edits", {"appearance_source": {
        "delivery_key": saved, "glb_sha256": appearance.service.delivery(saved)["glb_sha256"]}})
    run_key = appearance.service.registry.put("runs", {"root": str(native_root), "run_id": "run-1",
        "kind": "full_edit_item", "job_id": "fixture-job", "edit_key": edit_key})
    monkeypatch.setattr("glasses3d.dev_ui.api.read_run_status", lambda record: {"phase": "ended"})
    monkeypatch.setattr("glasses3d.dev_ui.api.find_run_delivery", lambda record: {"root": native_root, "delivery_id": "full-1"})
    first = appearance.service.run(run_key)
    second = appearance.service.run(run_key)
    assert first["delivery_key"] == second["delivery_key"] != first["native_delivery_key"]
    assert appearance.service.delivery(first["delivery_key"])["origin"] == "owner_look_edit"
    assert len(appearance.core.look_saves) == 2
    assert not appearance.launcher.approvals


def test_photo_angles_use_hash_bound_basis_and_never_guess(appearance, monkeypatch):
    from glasses3d.dev_ui.appearance_flow import photo_cameras
    from glasses3d.g3d_host.camera import Camera, NormFrame
    from PIL import Image
    photo = appearance.source / "photo.png"
    Image.new("RGB", (200, 100), "white").save(photo)
    digest = hashlib.sha256(photo.read_bytes()).hexdigest()
    basis = SimpleNamespace(
        refs={"good": {"photo_sha256": digest}, "failed": {"photo_sha256": "a" * 64}},
        is_usable=lambda rid: rid == "good", frame=NormFrame([0, 0, 0], 100),
        camera=lambda rid: Camera(yaw=0, pitch=0, roll=0, scale=100, center_x=99.5, center_y=49.5, perspective=.2),
        shape=lambda rid: (100, 200), pose=lambda rid: None, photo_path=lambda rid: photo)
    monkeypatch.setattr("glasses3d.agent.edits._source_basis", lambda root, report: basis)
    result = photo_cameras(appearance.core, appearance.source, "delivery-1")
    assert list(result) == [digest]
    camera = result[digest]["camera"]
    assert (camera["width"], camera["height"]) == (200, 100)
    assert camera["meta"]["source_camera"]["yaw"] == 0
    assert len(camera["view"]) == 16 and camera["fx"] > 0
    monkeypatch.setattr("glasses3d.agent.edits._source_basis", lambda root, report: (_ for _ in ()).throw(ValueError("basis changed")))
    assert photo_cameras(appearance.core, appearance.source, "delivery-1") == {}
