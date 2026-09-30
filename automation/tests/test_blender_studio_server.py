import json
from pathlib import Path
import struct
import threading

import httpx
import pytest

from blender_agent.studio_jobs import JobStore
from blender_agent.studio_server import Studio, create_server


@pytest.fixture
def service(tmp_path):
    studio = Studio(JobStore(tmp_path), port=0)
    server = create_server(studio, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{studio.port}"
    yield studio, url
    server.shutdown(); server.server_close(); thread.join()


def test_config_has_no_secrets_and_requires_host_origin_csrf(service, monkeypatch):
    studio, url = service
    monkeypatch.setenv("OPENAI_API_KEY", "never-send-this")
    with httpx.Client() as client:
        response = client.get(url + "/api/config")
        assert response.status_code == 200
        assert "never-send-this" not in response.text
        assert response.json()["capabilities"]["openai_key"] is True
        token = response.json()["csrf_token"]
        assert client.get(url + "/api/config", headers={"Host": "attacker.example"}).status_code == 403
        assert client.get(url + "/api/config", headers={"Origin": "https://attacker.example"}).status_code == 403
        assert client.post(url + "/api/jobs", json={}).status_code == 403
        headers = {"Origin": url, "X-CSRF-Token": token}
        created = client.post(url + "/api/jobs", headers=headers, json={"name": "Local draft"})
        assert created.status_code == 201
        job = created.json()
        assert job["status"] == "draft"
        assert client.get(url + "/api/jobs").json()["jobs"][0]["id"] == job["id"]
        patched = client.patch(url + "/api/jobs/" + job["id"], headers=headers, json={"budget_usd": 17})
        assert patched.json()["budget_usd"] == 17


def test_ar_get_is_artifact_only_not_cross_origin_api(service):
    studio, url = service
    with httpx.Client() as client:
        assert client.get(url + "/api/config", headers={"Origin": studio.store.ar_origin}).status_code == 403
        assert client.get(url + "/api/jobs/" + "a"*32 + "/model.glb", headers={"Origin": studio.store.ar_origin}).status_code == 404
        assert client.get(url + "/api/jobs/../../.env").status_code == 404
        assert client.get(url + "/.env").status_code == 404
        assert client.get(url + "/vendor/three/build/../../../../.env").status_code == 404


def glb(path):
    doc = {"asset": {"version": "2.0"}, "materials": [{"name": "frame", "pbrMetallicRoughness": {
        "baseColorFactor": [.1, .1, .1, 1], "metallicFactor": 0, "roughnessFactor": .3}}]}
    payload = json.dumps(doc).encode(); payload += b" " * (-len(payload) % 4)
    path.write_bytes(struct.pack("<IIIII", 0x46546C67, 2, 20+len(payload), len(payload), 0x4E4F534A) + payload)


def test_material_revision_optimistic_lock_and_sticky_blend_mismatch(service):
    studio, url = service
    store = studio.store; job = store.create({}); job = store.metadata(job["id"])
    directory = store.directory(job["id"]); model = directory / "model.glb"; glb(model)
    from blender_agent.studio_jobs import sha
    job.update(status="imported", read_only=True, artifacts={"model": "model.glb", "original_model": "model.glb"})
    job["result"] = store.result_urls(job["id"], sha(model), False); store.save(job)
    initial = model.read_bytes()
    first = studio.change_materials(job["id"], {"base_revision": "original", "viewer": {"lens_reflection": 1.5}, "edits": {}})
    assert first["job"]["result"]["scene_matches_revision"] is None
    assert "/studio-viewer.html?" in first["job"]["result"]["viewer_url"]
    assert first["materials"]["source_blend_matches_revision"] is None
    assert first["job"]["result"]["glb_materials_edited"] is False
    second = studio.change_materials(job["id"], {"base_revision": first["materials"]["revision"], "edits": {"0": {"roughness": .6}}})
    assert second["job"]["result"]["scene_matches_revision"] is False
    assert second["job"]["result"]["glb_materials_edited"] is True
    third = studio.change_materials(job["id"], {"base_revision": second["materials"]["revision"], "edits": {}})
    assert third["materials"]["source_blend_matches_revision"] is False
    assert third["job"]["result"]["glb_materials_edited"] is True
    from blender_agent.studio_jobs import JobError
    with pytest.raises(JobError, match="revision changed"):
        studio.change_materials(job["id"], {"base_revision": "original", "edits": {}})
    assert model.read_bytes() == initial
    with httpx.Client() as client:
        base = f"{url}/api/jobs/{job['id']}/model.glb"
        assert client.get(base + "?revision=original").content == initial
        assert client.get(base + "?revision=" + "f" * 16).status_code == 404


def test_describe_double_click_does_not_issue_second_request_and_preserves_user_specs(service, monkeypatch):
    studio, _ = service
    import blender_agent.studio_description as description
    job = studio.store.create({"specs": {"frame_width_mm": "145", "lens_type": "unknown"}})
    monkeypatch.setattr(description, "credentials", lambda _: {"GEMINI_API_KEY": "secret"})
    calls = []
    def fake(*args, **kwargs):
        from blender_agent.studio_jobs import JobError
        calls.append(1)
        with pytest.raises(JobError, match="before the first start"):
            studio.describe(job["id"])
        return {"description": "Inferred", "specs": {"frame_width_mm": "160", "lens_type": "mirror"}, "uncertainties": [], "model": "fake"}
    monkeypatch.setattr(description, "describe", fake)
    result = studio.describe(job["id"])
    assert calls == [1] and result["specs"]["frame_width_mm"] == "145"
    assert result["specs"]["lens_type"] == "mirror"
    assert studio.store.get(job["id"])["status"] == "draft"


def test_description_crash_recovery_does_not_reset_a_live_request(tmp_path, monkeypatch):
    import blender_agent.studio_server as server
    store = JobStore(tmp_path)
    public = store.create({}); job = store.metadata(public["id"])
    job.update(status="describing", description_request={"pid": 123, "process_created": 10})
    store.save(job)
    monkeypatch.setattr(server, "process_identity", lambda _: 10)
    Studio(store)
    assert store.get(job["id"])["status"] == "describing"
    monkeypatch.setattr(server, "process_identity", lambda _: False)
    Studio(store)
    recovered = store.get(job["id"])
    assert recovered["status"] == "draft"
    assert "unresolved" in recovered["error"] and "no automatic retry" in recovered["error"]
