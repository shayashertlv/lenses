"""M1 generic tools: real SDK schemas over local fakes, plus an actual child-tree timeout; zero network."""
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

from agents.tool_context import ToolContext
from agents.tool import ToolOutputImage, ToolOutputText
from PIL import Image
import psutil
import pytest

from glasses3d.agent import loop, tools as T
from glasses3d.agent.tools import arview as AR
from glasses3d.agent.journal import WorkerFailed
from glasses3d.agent.offline import disable_sdk_tracing

disable_sdk_tracing()


class FakeSession:
    def __init__(self):
        self.head = {"id": "c0001", "fingerprint": "f" * 64}
        self.calls = []

    def execute(self, code, **kw):
        self.calls.append(("execute", code, kw))
        self.head = {"id": "c0002", "fingerprint": "a" * 64}
        return {"status": "ok", "checkpoint": "c0002"}

    def call_read(self, tool, method, args, **kw):
        self.calls.append((tool, method, args, kw))
        return {"status": "ok", "result": {"objects": ["frame"]}}

    def save_checkpoint(self, label, **kw):
        return {"status": "ok", "checkpoint": self.head["id"], "label": label}

    def list_checkpoints(self):
        return [self.head]

    def restore_checkpoint(self, checkpoint, **kw):
        self.head = {"id": "c0003", "fingerprint": "b" * 64}
        return {"status": "ok", "checkpoint": self.head["id"], "restored_from": checkpoint}


@pytest.fixture
def scene(tmp_path):
    session = FakeSession()
    model = tmp_path / "work/exports/model.glb"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"fixture exported bytes")
    export = {"status": "exported", "checkpoint": session.head["id"], "fingerprint": session.head["fingerprint"],
              "path": str(model), "sha256": AR.sha256(model), "validation_sha256": AR.sha256(model),
              "validate_spec": {"ok": True}, "seat_check": None}
    obj = SimpleNamespace(root=tmp_path, job={}, s=session, lock=threading.RLock(), finish=None, last_ar=None,
                          calls=[], images=[], unseen=[], export=export)
    obj.record_call = lambda *args: obj.calls.append(args)
    obj.record_images = lambda cid, result: obj.images.extend(result["images"])
    obj.mark_unseen = lambda cid: obj.unseen.append(cid)
    obj.seeing = SimpleNamespace(export_head=lambda **kw: copy.deepcopy(obj.export))
    return obj


def invoke(scene, name, args=None, context=None, cid="call_test"):
    registry = {t.name: t for t in T.build_tools(scene)}
    raw = json.dumps(args or {})
    ctx = ToolContext(context=context, tool_name=name, tool_call_id=cid, tool_arguments=raw)
    return asyncio.run(registry[name].on_invoke_tool(ctx, raw))


def record(output):
    assert isinstance(output[0], ToolOutputText)
    return json.loads(output[0].text)


def test_registry_builds_without_a_started_scene_and_has_strict_timed_schemas():
    tools = T.build_tools(SimpleNamespace())
    assert tuple(t.name for t in tools) == T.TOOL_NAMES
    for tool in tools:
        assert tool.timeout_seconds == loop.sdk_timeout_s(tool.name)
        assert tool.params_json_schema["additionalProperties"] is False
        assert tool.strict_json_schema


def test_mutating_call_ids_and_checkpoint_receipts(scene):
    result = record(invoke(scene, "execute_blender_code", {"code": "result=1", "label": "first"}))
    assert result["status"] == "ok" and result["checkpoint"] == "c0002"
    assert scene.s.calls[0] == ("execute", "result=1", {"soft_timeout_s": 120, "label": "first", "call_id": "call_test"})
    assert scene.calls[0][:4] == ("call_test", "execute_blender_code", "c0001", "c0002")
    assert record(invoke(scene, "save_checkpoint", {"label": "best"}))["label"] == "best"
    assert record(invoke(scene, "list_checkpoints"))["checkpoints"] == [scene.s.head]
    assert record(invoke(scene, "restore_checkpoint", {"checkpoint": "c0001"}))["restored_from"] == "c0001"


def test_scene_summary_is_read_only_worker_call(scene):
    assert record(invoke(scene, "scene_summary"))["result"]["objects"] == ["frame"]
    assert scene.s.calls == [("scene_summary", "scene_summary", {"objects": "all"},
                              {"deadline_s": 30, "call_id": "call_test"})]


def test_worker_failure_propagates_out_of_sdk_tool(scene):
    def fail(*a, **kw):
        raise WorkerFailed("worker lost", scene.s.head)
    scene.s.execute = fail
    with pytest.raises(WorkerFailed, match="worker lost"):
        invoke(scene, "execute_blender_code", {"code": "result=1"})


def test_busy_tool_does_not_touch_the_scene(scene):
    entered, release = threading.Event(), threading.Event()
    def hold():
        with scene.lock:
            entered.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert entered.wait(2)
        assert record(invoke(scene, "scene_summary"))["code"] == "E_BUSY"
        assert scene.s.calls == []
    finally:
        release.set()
        thread.join()


@pytest.mark.parametrize("change", [
    {"validate_spec": {"ok": False}}, {"seat_check": {"ok": False}}, {"validation_sha256": "wrong"},
    {"checkpoint": "c0000"}, {"fingerprint": "stale"}, {"drift": "worker changed"},
])
def test_finish_refuses_invalid_or_stale_export(scene, change):
    scene.export.update(change)
    assert record(invoke(scene, "finish", {"summary": "done"}))["code"] == "E_EXPORT"
    assert scene.finish is None


def test_finish_checks_export_bytes_and_ar_identity(scene):
    model = Path(scene.export["path"])
    original = model.read_bytes()
    model.write_bytes(b"changed")
    assert record(invoke(scene, "finish", {"summary": "done"}))["code"] == "E_EXPORT"
    model.write_bytes(original)
    scene.last_ar = {"export_sha256": "stale", "status": "ok", "exit_code": 0}
    assert record(invoke(scene, "finish", {"summary": "done"}))["code"] == "E_AR_STALE"
    scene.last_ar = {"export_sha256": scene.export["sha256"], "status": "ok", "exit_code": 0}
    out = record(invoke(scene, "finish", {"summary": "done"}))
    assert out["status"] == "accepted" and scene.finish == out
    assert out["call_id"] == "call_test" and out["export"]["validate_spec"]["ok"]


@pytest.mark.parametrize("ar", [None, {"status": "infrastructure_failed", "exit_code": 1},
                               {"status": "ok", "exit_code": 3}, {"status": "timeout", "exit_code": None}])
def test_finish_requires_successful_current_ar_for_deliverability(scene, ar):
    scene.last_ar = None if ar is None else {**ar, "export_sha256": scene.export["sha256"]}
    assert record(invoke(scene, "finish", {"summary": "done"}))["code"] == "E_AR"
    assert scene.finish is None


def test_budget_status_reads_guard_without_money_side_effects(scene):
    ctx = SimpleNamespace(guard=SimpleNamespace(summary=lambda: {"settled_usd": "1.000000", "maximum_usd": "23.000000"}))
    assert record(invoke(scene, "budget_status", context=ctx))["budget"]["settled_usd"] == "1.000000"


def test_image_output_has_oriented_crop_and_delivered_digest(scene):
    src = scene.root / "images/photo.jpg"
    src.parent.mkdir()
    image = Image.new("RGB", (20, 10), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(src, exif=exif)
    output = invoke(scene, "read_image", {"path": "images/photo.jpg", "region": [0, 0, 10, 15]})
    rec = record(output)["images"][0]
    assert len(output) == 2 and isinstance(output[1], ToolOutputImage)
    encoded = output[1].image_url.split(",", 1)[1]
    import base64
    payload = base64.b64decode(encoded)
    assert Image.open(io.BytesIO(payload)).size == (10, 15)
    assert rec["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (scene.root / rec["path"]).read_bytes() == payload
    assert rec["source_size_px"] == [10, 20]
    assert scene.images[0]["sha256"] == rec["sha256"]


@pytest.mark.parametrize("path", ["../outside.png", "budget/secret.png", "runs/key.png", "cameras/hidden.png"])
def test_read_image_cannot_read_outside_image_areas(scene, path):
    assert record(invoke(scene, "read_image", {"path": path}))["code"] == "E_ARGS"


def test_fit_camera_output_can_return_its_internal_camera_sheet(scene, monkeypatch):
    from glasses3d.agent import jobs
    sheet = scene.root / "cameras/basis-001/sheet.png"
    sheet.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(sheet)
    monkeypatch.setattr(jobs, "load_job", lambda root: {"images": [
        {"original_name": "front.jpg", "file": "images/front.jpg", "view_kind": "front", "fit_kinds": ["front"]},
        {"original_name": "side.jpg", "file": "images/side.jpg", "view_kind": "side", "fit_kinds": ["left"]}]})
    monkeypatch.setattr(T, "free_commit_bytes", lambda: 10 * 2**30)
    received = []
    def fit(*a, **kw):
        assert T.os.environ["OPENBLAS_NUM_THREADS"] == "1"
        received.append((a, kw))
        return {"status": "ok", "sheet": str(sheet)}
    scene.seeing.fit_cameras = fit
    output = invoke(scene, "fit_cameras", {"reason": "front shape ready", "workers": 2})
    assert received[0][0][0] == "front shape ready" and received[0][1]["workers"] == 2
    assert received[0][0][2] == {"front.jpg": "front", "side.jpg": "side"}
    assert received[0][1]["candidates"] == {"front.jpg": ("front",), "side.jpg": ("left",)}
    assert len(output) == 2 and record(output)["images"][0]["source_path"].startswith("cameras/")
    invoke(scene, "fit_cameras", {"reason": "one photo", "refs": ["side"], "workers": 1}, cid="call_subset")
    assert received[1][1]["candidates"] == {"side.jpg": ("left",)}


def test_render_compare_pick_and_ar_wrappers_forward_checked_arguments(scene, monkeypatch):
    received = []
    scene.seeing.render_views = lambda **kw: received.append(("render", kw)) or {"status": "ok"}
    scene.seeing.compare_to_photos = lambda **kw: received.append(("compare", kw)) or {"status": "ok"}
    scene.seeing.pick_surface_point = lambda *a, **kw: received.append(("pick", a, kw)) or {"status": "ok"}
    invoke(scene, "render_views", {"views": [{"name": "oblique", "yaw": 30, "pitch": 20}], "size_px": 512})
    assert received[0][1]["views"][0]["yaw"] == 30 and received[0][1]["size_px"] == 512
    invoke(scene, "compare_to_photos", {"refs": ["front"], "parts": "front"})
    assert received[1][1]["refs"] == ["front"] and received[1][1]["parts"] == "front"
    invoke(scene, "pick_surface_point", {"view": "front", "points": [[20, 30]], "manifest": "work/renders/view.json"})
    assert received[2][1] == ("front", [[20.0, 30.0]])
    assert Path(received[2][2]["manifest"]).is_relative_to(scene.root)
    monkeypatch.setattr(AR, "capture", lambda root, exp, cid, **kw: {
        "status": "ok", "export_sha256": exp["sha256"], "report": "work/arview/report.json", "sheets": []})
    result = record(invoke(scene, "arview_capture"))
    assert scene.last_ar == result and result["export_sha256"] == scene.export["sha256"]


def test_worker_capacity_is_bounded_by_free_commit_and_cpu(monkeypatch):
    monkeypatch.setattr(T.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(T, "free_commit_bytes", lambda: 40 * 2**30)
    assert T.fit_workers() == 16 and T.fit_workers(3) == 3
    monkeypatch.setattr(T, "free_commit_bytes", lambda: 6 * 2**30)
    assert T.fit_workers(16) == 3
    monkeypatch.setattr(T, "free_commit_bytes", lambda: 1 * 2**30)
    assert T.fit_workers() == 1


@pytest.mark.parametrize("code,status", list(AR.EXIT_STATUS.items()))
def test_arview_fixed_scale_exit_mapping_and_image_integrity(scene, monkeypatch, code, status):
    monkeypatch.setattr(AR.shutil, "which", lambda name: "node.exe")
    def run_capture(argv, log_dir, **kw):
        assert "--fixed-scale" in argv and "--no-portrait" in argv
        assert argv[argv.index("--poses") + 1] == "default"
        out = Path(argv[argv.index("--out") + 1])
        assert not list(out.iterdir())
        image = out / "front__skin__beauty.png"
        Image.new("RGB", (20, 10), "blue").save(image)
        report = {"schema": "arview.capture/1", "input": {"sha256": scene.export["sha256"]}, "status": status,
                  "images": [{"file": image.name, "sha256": AR.sha256(image), "pose": "front", "variant": "beauty"}]}
        (out / "report.json").write_text(json.dumps(report))
        return code, False
    monkeypatch.setattr(AR, "run_capture", run_capture)
    result = AR.capture(scene.root, scene.export, "capture")
    assert result["status"] == status and result["exit_code"] == code
    assert result["export_sha256"] == scene.export["sha256"]
    assert Path(result["sheets"][0]).is_file() and result["report_sha256"]


@pytest.mark.parametrize("defect", ["sha", "path", "model"])
def test_arview_refuses_corrupt_image_or_report(scene, monkeypatch, defect):
    monkeypatch.setattr(AR.shutil, "which", lambda name: "node.exe")
    def runner(argv, *a, **kw):
        out = Path(argv[argv.index("--out") + 1])
        report = {"schema": "arview.capture/1", "input": {"sha256": "wrong" if defect == "model" else scene.export["sha256"]},
                  "images": [{"file": "../outside.png" if defect == "path" else "missing.png", "sha256": "wrong"}]}
        (out / "report.json").write_text(json.dumps(report))
        return 0, False
    monkeypatch.setattr(AR, "run_capture", runner)
    result = AR.capture(scene.root, scene.export, "capture")
    assert result["status"] == "infrastructure_failed" and result["code"] == "E_INTEGRITY"
    assert result["images"] == []


def test_capture_timeout_kills_the_real_process_and_its_child(tmp_path):
    pid_file = tmp_path / "pids.json"
    script = tmp_path / "tree.py"
    script.write_text("import json,os,subprocess,sys,time\n"
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\n"
                      "open(sys.argv[1],'w').write(json.dumps([os.getpid(),p.pid]))\n"
                      "time.sleep(120)\n")
    code, timed_out = AR.run_capture([sys.executable, str(script), str(pid_file)], tmp_path, timeout_s=2)
    assert timed_out and code is None
    pids = json.loads(pid_file.read_text())
    deadline = time.monotonic() + 3
    while any(psutil.pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(.02)
    assert not any(psutil.pid_exists(pid) for pid in pids)
