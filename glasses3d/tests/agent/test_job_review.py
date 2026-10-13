"""Immutable owner-review binding and the real detached loopback AR server; no paid/network client."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from glasses3d.agent import review as R
from glasses3d.tests import glbfab


@pytest.fixture
def delivered(tmp_path, monkeypatch):
    root = tmp_path / "job"
    folder = root / "deliveries" / "delivery-001"
    folder.mkdir(parents=True)
    model = folder / "model.glb"
    model.write_bytes(glbfab.build())
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    record = {"id": "delivery-001", "delivery_id": "delivery-001", "status": "delivered",
              "model": str(model), "glb_sha256": digest, "manifest_sha256": "b" * 64}

    def load(where, delivery_id=None):
        assert Path(where) == root
        assert delivery_id in (None, "delivery-001")
        if hashlib.sha256(model.read_bytes()).hexdigest() != digest:
            raise ValueError("delivery bytes changed")
        return dict(record)

    monkeypatch.setattr(R.delivery, "load_delivery", load)
    return root, record


@pytest.fixture
def server(delivered, monkeypatch):
    root, item = delivered
    port, pid = 18251, 7890
    model_url = f"http://127.0.0.1:{port}/__arview/models/{item['glb_sha256']}/model.glb"
    report_url = f"http://127.0.0.1:{port}/__arview/report.json"
    url = f"http://127.0.0.1:{port}/?" + urlencode({"model": model_url, "sha256": item["glb_sha256"],
                                                   "report": report_url, "lensenv": "1"})
    state = {"schema": "arview.serve-state/1", "ready": True, "pid": pid, "instance": "unique-server",
             "launch_id": "unique-launch", "port": port, "sha256": item["glb_sha256"], "model": item["model"],
             "url": url, "report_url": report_url, "token": "private-stop-token", "log": "own.log"}
    report = {"schema": "arview.serve/1", "server": {"pid": pid, "instance": "unique-server", "detached": True},
              "served_sha256": item["glb_sha256"], "model": {"sha256": item["glb_sha256"], "path": item["model"],
                                                               "url": model_url},
              "handover": {"lensenv": 1}, "page_url": url, "verdict": {"spec_ok": True}, "errors": [],
              "status": "rendered", "warnings": []}
    calls = []

    def serve(node, model, requested_port, launch):
        calls.append((node, model, requested_port, launch))
        state_dir = launch / "state"
        state_dir.mkdir()
        path = state_dir / f"port-{port}.json"
        path.write_text(json.dumps(state))
        return copy.deepcopy(state), path, "wmi", launch / "startup.log"

    def request(request_port, route, **kwargs):
        if request_port != port:
            raise R.ReviewError("nothing serving on this port")
        if kwargs.get("method") == "POST":
            calls.append(("stop", request_port, route, kwargs["headers"]))
            return 202, {}, b"stopping"
        if route == "/__arview/report.json":
            return 200, {}, json.dumps(report).encode()
        assert route == f"/__arview/models/{item['glb_sha256']}/model.glb"
        return 200, {}, Path(item["model"]).read_bytes()

    monkeypatch.setattr(R.shutil, "which", lambda name: "node")
    monkeypatch.setattr(R, "_serve", serve)
    monkeypatch.setattr(R, "_loopback", request)
    return state, report, calls


def test_review_binds_exact_delivery_only_after_live_hash_check(delivered, server, monkeypatch):
    root, item = delivered
    state, report, calls = server
    got = R.review_job(root, port=18251)
    assert got["status"] == "ready" and got["schema"] == R.REVIEW_SCHEMA
    assert got["delivery_id"] == item["delivery_id"] and got["glb_sha256"] == item["glb_sha256"]
    assert got["manifest_sha256"] == item["manifest_sha256"] and got["url"] == state["url"]
    assert got["appearance_policy"] == "g3d.ar_appearance/1" and got["lens_env_intensity"] == 1
    assert got["server"]["pid"] == state["pid"] and got["server"]["instance"] == state["instance"]
    assert "private-stop-token" not in json.dumps(got)
    assert calls[0][1] == Path(item["model"]) and len(calls) == 1
    assert json.loads((root / "review/current.json").read_text()) == got
    assert len(list((root / "review/servers").glob("*/review.json"))) == 1
    monkeypatch.setattr(R, "_report", lambda _: pytest.fail("a verdict binding must survive server shutdown"))
    assert R.reviewed_delivery(root) == got


@pytest.mark.parametrize("defect", ["pending", "state_sha", "state_model", "state_port", "report_sha", "other_server",
                                     "lens", "page", "model_url", "spec", "report_error"])
def test_no_binding_on_stale_failed_or_mismatched_readiness(delivered, server, defect):
    root, _item = delivered
    state, report, calls = server
    if defect == "pending": state["ready"] = False
    elif defect == "state_sha": state["sha256"] = "f" * 64
    elif defect == "state_model": state["model"] = str(root / "deliverable/model.glb")
    elif defect == "state_port": state["port"] = 18252
    elif defect == "report_sha": report["served_sha256"] = "f" * 64
    elif defect == "other_server": report["server"]["instance"] = "unrelated"
    elif defect == "lens": report["handover"]["lensenv"] = 1.5
    elif defect == "page": report["page_url"] = "http://127.0.0.1:18251/"
    elif defect == "model_url": report["model"]["url"] = "https://example.invalid/model.glb"
    elif defect == "spec": report["verdict"]["spec_ok"] = None
    else: report["errors"] = ["model changed"]
    with pytest.raises(R.ReviewError):
        R.review_job(root, port=18251)
    assert not (root / "review/current.json").exists()
    if defect == "other_server":
        assert not any(call[0] == "stop" for call in calls)


def test_corrupt_served_model_never_gets_an_owner_review_binding(delivered, server, monkeypatch):
    root, _item = delivered
    original = R._loopback

    def wrong_bytes(port, route, **kwargs):
        if route.startswith("/__arview/models/"):
            return 200, {}, b"different model"
        return original(port, route, **kwargs)

    monkeypatch.setattr(R, "_loopback", wrong_bytes)
    with pytest.raises(R.ReviewError, match="served bytes"):
        R.review_job(root, port=18251)
    assert not (root / "review/current.json").exists()


def test_busy_port_leaves_previous_binding_and_unrelated_server_alone(delivered, server, monkeypatch):
    root, _item = delivered
    prior = R.review_job(root, port=18251)
    stopped = []
    monkeypatch.setattr(R, "_stop_owned", lambda state: stopped.append(state))
    monkeypatch.setattr(R, "_serve", lambda *args: (_ for _ in ()).throw(R.ReviewError("port already in use")))
    with pytest.raises(R.ReviewError, match="port already"):
        R.review_job(root, port=18251)
    assert R.reviewed_delivery(root) == prior and not stopped


def test_saved_review_rejects_changed_delivery_or_manifest(delivered, server):
    root, item = delivered
    R.review_job(root, port=18251)
    path = root / "review/current.json"
    saved = json.loads(path.read_text())
    saved["manifest_sha256"] = "c" * 64
    path.write_text(json.dumps(saved))
    with pytest.raises(R.ReviewError, match="changed delivery"):
        R.reviewed_delivery(root)
    Path(item["model"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="delivery bytes changed"):
        R.reviewed_delivery(root)


@pytest.mark.parametrize("port", [-1, 1023, 65536, True, 8251.0])
def test_invalid_port_cannot_start_a_server(delivered, monkeypatch, port):
    root, _item = delivered
    monkeypatch.setattr(R, "_serve", lambda *args: pytest.fail("invalid port started a server"))
    with pytest.raises(R.ReviewError, match="port"):
        R.review_job(root, port=port)


def test_launcher_uses_existing_detach_once_with_file_logs_and_no_timeout_wrapper(tmp_path, monkeypatch):
    launch = tmp_path / "launch"
    launch.mkdir()
    model = tmp_path / "model.glb"
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")

    def run(argv, **kwargs):
        assert argv.count("--detach") == 1 and "timeout" not in kwargs
        assert argv[argv.index("--lensenv") + 1] == "1"
        assert argv[argv.index("--state-dir") + 1] == str(launch / "state")
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["stdout"].name == str(launch / "startup.log")
        assert kwargs["stderr"] == subprocess.STDOUT
        if R.os.name == "nt":
            assert kwargs["creationflags"] == R.procs.CREATE_NO_WINDOW
        kwargs["stdout"].write(b"started the detached server (pid 123, wmi); log own.log\n")
        (launch / "state/port-18251.json").write_text('{"pid":123}')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(R.subprocess, "run", run)
    state, path, method, log = R._serve("node", model, 18251, launch)
    assert state["pid"] == 123 and method == "wmi"
    assert path == launch / "state/port-18251.json" and log == launch / "startup.log"


def test_real_detached_serve_hash_readiness_and_owned_cleanup(tmp_path):
    """Use a copied, synthetic spec-passing GLB. No camera is opened and no GPU capture is started."""
    from glasses3d.tests.agent.test_delivery import make_finished
    root = tmp_path / "job"
    scene, result = make_finished(root)
    R.delivery.deliver_finished(root, scene, result)
    item = R.delivery.load_delivery(root)
    assert shutil.which("node"), "the full glasses3d suite requires Node.js"
    got = None
    try:
        got = R.review_job(root, port=0)
        assert got["server"]["detached"] and got["glb_sha256"] == item["glb_sha256"]
        assert R._report(got["server"]["port"])["served_sha256"] == item["glb_sha256"]
        assert R.reviewed_delivery(root) == got
        scene2, result2 = make_finished(root, run_id="newer", variation=1)
        R.delivery.deliver_finished(root, scene2, result2)
        assert R.delivery.load_delivery(root)["delivery_id"] != item["delivery_id"]
        assert R.reviewed_delivery(root) == got  # a newer delivery cannot retarget an already viewed model
    finally:
        if got is not None:
            state_path = Path(got["server"]["state_path"])
            # The existing CLI uses the state instance/token, never a broad Node kill or an unrelated port.
            stopped = subprocess.run([shutil.which("node"), str(R.ARVIEW / "bin/arview.mjs"), "serve", "--stop",
                                      "--port", str(got["server"]["port"]), "--state-dir", str(state_path.parent)],
                                     capture_output=True, text=True, cwd=R.ARVIEW,
                                     **({"creationflags": R.procs.CREATE_NO_WINDOW} if R.os.name == "nt" else {}))
            assert stopped.returncode == 0, stopped.stdout + stopped.stderr
            assert not state_path.exists()
