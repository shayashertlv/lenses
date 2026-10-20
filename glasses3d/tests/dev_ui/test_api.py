"""HTTP trust boundary, static confinement, and construction-only offline mode."""
import importlib
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from glasses3d.dev_ui.contracts import Config
from glasses3d.dev_ui.core_adapter import CoreAdapter
from glasses3d.dev_ui.server import create_app


class NoLaunch:
    offline_test_mode = True

    def __getattr__(self, name):
        raise AssertionError(f"unexpected model action: {name}")


@pytest.fixture
def boundary(tmp_path):
    config = Config(port=8270, state_root=tmp_path / "state", jobs_root=tmp_path / "jobs",
                    static_root=tmp_path / "static", vendor_root=tmp_path / "vendor")
    config.static_root.mkdir()
    for name in ("index", "new", "run", "results", "edit", "understand", "task"):
        (config.static_root / (name + ".html")).write_text(name, encoding="utf-8")
    (config.static_root / "app.js").write_text("export const okay=true", encoding="utf-8")
    (config.static_root / ".secret").write_text("unavailable", encoding="utf-8")
    for name in ("build/three.module.js", "examples/jsm/controls/OrbitControls.js", "package.json", "src/private.js"):
        path = config.vendor_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    app = create_app(config, launcher=NoLaunch())
    with TestClient(app, base_url="http://127.0.0.1:8270", headers={"Origin": "http://127.0.0.1:8270"}) as client:
        yield config, app, client


@pytest.mark.parametrize("path,content", [("/", "index"), ("/new", "new"), ("/run/foobar", "run"),
    ("/results/foobar", "results"), ("/edit/foobar", "edit"), ("/understand/foobar", "understand"),
    ("/task/foobar", "task"), ("/static/app.js", "export const okay=true"),
    ("/vendor/three/build/three.module.js", "build/three.module.js"),
    ("/vendor/three/examples/jsm/controls/OrbitControls.js", "examples/jsm/controls/OrbitControls.js")])
def test_pages_and_allowed_assets(boundary, path, content):
    response = boundary[2].get(path)
    assert response.status_code == 200
    assert response.text == content
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/vendor/three/package.json", "/vendor/three/src/private.js",
    "/vendor/three/build/%2e%2e/package.json", "/vendor/three/build/%2e%2e/src/private.js",
    "/static/.secret", "/static/%2e%2e/.secret", "/static/C%3A/test.html", "/static/sub%5capp.js"])
def test_static_and_vendor_confinement(boundary, path):
    response = boundary[2].get(path)
    assert response.status_code == 404
    assert "error" in response.json()


@pytest.mark.parametrize("host", ["example.test:8270", "127.0.0.1", "localhost", "127.0.0.1:8260",
                                 "user@127.0.0.1:8270", "127.0.0.1:8270.example.test"])
def test_rejects_other_hosts(boundary, host):
    response = boundary[2].get("/api/config", headers={"Host": host})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_host"


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
@pytest.mark.parametrize("origin", ["null", "https://127.0.0.1:8270", "http://evil.test", "http://localhost:8270",
                                   "http://127.0.0.1:8270/", "http://127.0.0.1:8270?x=1"])
def test_rejects_cross_origin_mutations(boundary, method, origin):
    response = getattr(boundary[2], method)("/api/edit-drafts/notfound/items/I1", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


def test_missing_origin_refuses_but_same_origin_referer_is_allowed(boundary):
    config, app, _ = boundary
    with TestClient(app, base_url="http://127.0.0.1:8270") as client:
        assert client.post("/api/edit-drafts", json={}).status_code == 403
        response = client.post("/api/edit-drafts", json={}, headers={"Referer": "http://127.0.0.1:8270/edit/foobar"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_localhost_same_origin_works(boundary):
    response = boundary[2].post("/api/edit-drafts", json={},
        headers={"Host": "localhost:8270", "Origin": "http://localhost:8270"})
    assert response.status_code == 404


def test_config_is_code_derived_and_offline_is_construction_only(boundary, monkeypatch):
    config, app, client = boundary
    from glasses3d.g3d_host.fit_views import VIEW_KINDS
    dto = client.get("/api/config").json()
    assert dto == {"schema": "g3d.dev_config/1", "offline_test_mode": True, "view_kinds": list(VIEW_KINDS),
                   "sides": ["both", "left", "right", "none"], "effort": "max", "edit_effort": "high"}
    monkeypatch.setenv("G3D_OFFLINE", "1")
    monkeypatch.setenv("OFFLINE_TEST_MODE", "1")
    with TestClient(create_app(config), base_url="http://127.0.0.1:8270") as production:
        assert production.get("/api/config?offline_test_mode=true").json()["offline_test_mode"] is False
        assert production.post("/api/config", json={"offline_test_mode": True},
                               headers={"Origin": "http://127.0.0.1:8270"}).status_code == 405
        assert production.get("/api/config").json()["offline_test_mode"] is False


@pytest.mark.parametrize("path", ["/api/runs/unknown", "/api/deliveries/unknown", "/api/edit-drafts/unknown",
    "/api/edits/unknown/understanding", "/api/tasks/unknown", "/api/artifacts/" + "0" * 64,
    "/api/deliveries/unknown/model.glb", "/api/artifacts/bad", "/not-a-page"])
def test_unknown_handles_are_json_404(boundary, path):
    response = boundary[2].get(path)
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message"}


@pytest.mark.parametrize("body", ["[]", "null", '{"delivery_key":"a","delivery_key":"b"}', '{"x":NaN}', "broken"])
def test_malformed_json_is_plain_error(boundary, body):
    response = boundary[2].post("/api/edit-drafts", content=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_json"


def test_request_limit_before_body_or_model_action(boundary):
    config, _, client = boundary
    config.max_request_bytes = 50
    response = client.post("/api/jobs/start", content=b"x" * 51)
    assert response.status_code == 413
    response = client.post("/api/jobs/start", content=iter([b"x" * 25, b"x" * 30]))
    assert response.status_code in (400, 413)  # missing multipart refuses before consuming an unbounded body
    response = client.post("/api/edit-drafts", content=iter([b"x" * 25, b"x" * 30]))
    assert response.status_code == 413


def test_missing_multipart_and_duplicate_parts_refuse(boundary):
    client = boundary[2]
    response = client.post("/api/jobs/start", json={})
    assert response.status_code == 400
    response = client.post("/api/jobs/start", files=[("manifest", (None, "{}")), ("manifest", (None, "{}"))])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_form"


def test_missing_facades_raise_clear_not_built_error(monkeypatch):
    adapter = importlib.import_module("glasses3d.dev_ui.core_adapter")
    def missing(name):
        raise ModuleNotFoundError(name)
    monkeypatch.setattr(adapter, "import_module", missing)
    for name, args in [("read_understanding", [Path("unused")]),
                       ("apply_choices", [Path("unused"), "a" * 64, [], [], "words"]),
                       ("create_full_edit_item", [Path("unused"), "I1"])]:
        with pytest.raises(ValueError, match="not built yet") as error:
            getattr(CoreAdapter(), name)(*args)
        assert error.value.code == "not_built_yet"


def test_real_intake_preserves_verbatim_inputs_with_fake_launcher(boundary):
    import io
    from PIL import Image
    class FakePaid:
        offline_test_mode = True
        def check_available(self):
            pass
        def prepare(self, root, amount, *, run_id, kind):
            return {"run_id": run_id, "cutoff_usd": amount, "estimate_usd": "1.00"}
        def approve(self, *args):
            pass
        def launch(self, *args):
            pass
    config = boundary[0]
    app = create_app(config, launcher=FakePaid(), core_adapter=CoreAdapter())
    image = io.BytesIO(); Image.new("RGB", (15, 12), "red").save(image, format="PNG")
    description = "  Verbatim owner description\nDo not rewrite.  "
    specs_text = '{\n "owner_spec" : "unchanged"\n}\n'
    manifest = {"name": "", "description": description, "specs_text": specs_text,
        "cutoff_usd": "37.50", "idempotency_key": "real-intake-fake-paid",
        "photos": [{"field": "photo", "filename": "Original Name.png", "kind": "front", "side": "none"}]}
    with TestClient(app, base_url="http://127.0.0.1:8270", headers={"Origin": "http://127.0.0.1:8270"}) as browser:
        response = browser.post("/api/jobs/start", data={"manifest": json.dumps(manifest)},
            files={"photo": ("Original Name.png", image.getvalue(), "image/png")})
        assert response.status_code == 200, response.text
    root = config.jobs_root / response.json()["job_id"]
    job = json.loads((root / "job.json").read_bytes())
    assert job["description"] == description
    assert job["specs_json"] == specs_text
    assert job["name"] == "Product"
    assert job["images"][0]["side"] is None
    assert job["images"][0]["view_kind"] == "front"
    assert (root / "images/Original Name.png").read_bytes() == image.getvalue()
    assert not list(root.rglob("approval.json"))
