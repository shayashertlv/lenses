"""WP2 stage A: the headless Blender worker (g3d/worker.py + agent/worker_client.py), WP2_DESIGN §3/§4.

Lifecycle (receipt, identity, token, single client, exit on disconnect, secret-free environment, Job Object, EcoQoS),
exec (ok / error with snippet lines / static refusal / soft timeout on the same pid), the runtime layers through the
--selftest channel (refused in run mode), and the hard path (a numpy C call ignores the soft timeout; the deadline
kills the worker through its Job Object). Needs Blender 5.2."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import struct
import time
from pathlib import Path

import psutil
import pytest

from glasses3d.agent import procs
from glasses3d.agent.worker_client import WORKER_PY, WorkerClient, WorkerDied, WorkerError
from glasses3d.g3d import rpc
from glasses3d.g3d_host import blender

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return tmp_path_factory.mktemp("wp2worker")


@pytest.fixture(scope="module")
def w(root):
    """A --selftest worker with the EEVEE probe, started with a fake secret in the parent environment."""
    keep = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "GITHUB_TOKEN")}
    os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"
    os.environ["GITHUB_TOKEN"] = "ghp_test"
    try:
        worker = WorkerClient(root, selftest=True, probe=True).start()
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    yield worker
    worker.close()


@pytest.fixture(scope="module")
def plain(root):
    """A run-mode worker (no --selftest)."""
    worker = WorkerClient(root / "plain").start()
    yield worker
    worker.close()


# ------------------------------------------------------------------------------------------------ lifecycle
def test_receipt_identity_and_versions(w):
    r = w.receipt
    assert r["schema"] == "g3d.worker/1" and r["pid"] == w.proc.pid
    assert abs(r["create_time"] - psutil.Process(w.proc.pid).create_time()) < 1.0
    assert len(r["token_sha256"]) == 64 and isinstance(r["port"], int) and r["port"] > 0
    assert r["blender"].startswith("5.2") and r["python"].startswith("3.13")
    assert r["safe_mode_selfcheck"] == {"unlisted_edges": []}
    assert r["g3d_facade"]["errors"] == {} and len(r["g3d_facade"]["names"]) == 8
    assert w.hello["pid"] == w.proc.pid and len(w.hello["fingerprint"]) == 64
    assert w.ready_s < 60


def test_eevee_probe_and_gpu_in_the_receipt(w):
    probe = w.receipt["probe"]
    assert probe.get("error") is None, probe
    assert probe["nonempty_px"] > 0 and probe["seconds"] > 0
    assert probe["gpu"].get("renderer"), probe["gpu"]


def test_worker_is_in_its_job_object_and_opted_out_of_ecoqos(w):
    if os.name != "nt":
        pytest.skip("Windows")
    assert w.proc.pid in w.job.pids()
    assert w.qos == (1, 0)


def test_worker_environment_is_secret_free(w):
    env = psutil.Process(w.proc.pid).environ()
    upper = {k.upper() for k in env}
    assert "OPENAI_API_KEY" not in upper and "GITHUB_TOKEN" not in upper and "PYTHONPATH" not in upper
    assert "G3D_WORKER_TOKEN" not in upper                 # popped by the worker; only its hash is kept
    assert not [k for k in upper if any(s in k for s in ("API_KEY", "SECRET", "PASSWORD"))]
    assert env.get("TEMP", env.get("Temp", "")).startswith(str(w.dir))


def _manual_worker(tmp: Path):
    """Start a worker by hand (the test needs the token)."""
    token = secrets.token_bytes(32).hex()
    receipt = tmp / "receipt.json"
    (tmp / "cwd").mkdir(parents=True, exist_ok=True)
    argv = [str(blender.blender_exe()), "-b", "--factory-startup", "--disable-autoexec", "--python", str(WORKER_PY),
            "--", "--receipt", str(receipt), "--root", str(tmp)]
    log = open(tmp / "worker.log", "ab")
    proc, job = procs.spawn_in_job(argv, cwd=tmp / "cwd", stdout=log, env=procs.worker_env(token, tmp / "t"))
    t0 = time.time()
    while not receipt.exists():
        assert proc.poll() is None and time.time() - t0 < 60
        time.sleep(0.05)
    time.sleep(0.1)
    info = json.loads(receipt.read_text())
    return token, info, proc, job, log


def _hello(port, token, rid=1):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    rpc.send_frame(s, {"id": rid, "method": "hello", "token": token})
    return s, rpc.recv_frame(s)


def test_token_single_client_ids_and_exit_on_disconnect(tmp_path):
    token, info, proc, job, log = _manual_worker(tmp_path)
    try:
        assert info["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
        bad, reply = _hello(info["port"], "f" * 64)
        assert reply["ok"] is False and reply["error"]["type"] == "Auth"
        with pytest.raises((rpc.PeerClosed, OSError)):
            rpc.recv_frame(bad)                              # only that connection is closed
        good, reply = _hello(info["port"], token)
        assert reply["ok"] is True and reply["result"]["pid"] == proc.pid
        with pytest.raises(OSError):                          # the listener closed after the first client
            socket.create_connection(("127.0.0.1", info["port"]), timeout=3)
        rpc.send_frame(good, rpc.request(2, "ping"))
        assert rpc.recv_frame(good)["result"]["pong"] is True
        rpc.send_frame(good, rpc.request(2, "ping"))          # ids must strictly increase
        reply = rpc.recv_frame(good)
        assert reply["ok"] is False and reply["error"]["type"] == "BadRequest"
        proc.wait(20)                                         # a protocol error ends the worker
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            job.terminate()
        job.close()
        log.close()


def test_client_disconnect_ends_the_worker(tmp_path):
    token, info, proc, job, log = _manual_worker(tmp_path)
    try:
        s, reply = _hello(info["port"], token)
        assert reply["ok"]
        s.close()
        proc.wait(20)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            job.terminate()
        job.close()
        log.close()


def test_a_garbage_frame_ends_the_worker(tmp_path):
    token, info, proc, job, log = _manual_worker(tmp_path)
    try:
        s, reply = _hello(info["port"], token)
        s.sendall(struct.pack(">I", 12) + b"not json!!!!")
        reply = rpc.recv_frame(s)
        assert reply["ok"] is False and reply["error"]["type"] == "FrameError"
        proc.wait(20)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            job.terminate()
        job.close()
        log.close()


# ------------------------------------------------------------------------------------------------ exec
def test_exec_ok_returns_result_stdout_and_delta(w):
    r = w.call("exec", {"code": "import bmesh\nbm = bmesh.new()\nbmesh.ops.create_cube(bm, size=4)\n"
                                "me = bpy.data.meshes.new('T1')\nbm.to_mesh(me)\nbm.free()\n"
                                "ob = bpy.data.objects.new('T1', me)\nbpy.context.scene.collection.objects.link(ob)\n"
                                "print('made', ob.name)\nresult = {'v': len(me.vertices), 'co': ob.location}"},
               deadline_s=60)
    assert r["status"] == "ok" and r["result"] == {"v": 8, "co": [0.0, 0.0, 0.0]}
    assert r["stdout"] == "made T1\n" and r["changed"] is True
    assert r["scene_delta"]["added"] == ["T1"]
    assert r["fingerprint_before"] != r["fingerprint_after"]


def test_exec_error_reports_snippet_lines(w):
    r = w.call("exec", {"code": "x = 1\n\ndef f():\n    return {}['missing']\nf()\n"}, deadline_s=60)
    assert r["status"] == "error"
    assert "snippet line 5" in r["traceback"] and "snippet line 4" in r["traceback"]
    assert "KeyError" in r["traceback"] and "worker.py" not in r["traceback"]


def test_exec_static_refusal_never_runs(w):
    r = w.call("exec", {"code": "import bpy\nbpy.ops.wm.save_mainfile()\nimport os"}, deadline_s=60)
    assert r["status"] == "refused" and len(r["violations"]) == 2
    assert "fingerprint_before" not in r
    assert any("save_checkpoint" in (v["use_instead"] or "") for v in r["violations"])


def test_soft_timeout_interrupts_a_python_loop_on_the_same_pid(w):
    t0 = time.perf_counter()
    r = w.call("exec", {"code": "x = 0\nwhile True:\n    x += 1", "soft_timeout_s": 2}, deadline_s=60)
    dt = time.perf_counter() - t0
    assert r["status"] == "timeout" and dt <= 2.0 + 0.5 + 0.5, dt   # deadline + 0.5 s (+ fingerprints)
    assert r["exec_seconds"] <= 2.5
    assert w.call("ping")["pid"] == w.proc.pid


def test_a_snippet_that_swallows_the_interrupt_is_interrupted_again_and_still_times_out(w):
    code = "n = 0\nwhile n < 2:\n    try:\n        while True:\n            pass\n    except:\n        n += 1\nresult = n"
    t0 = time.perf_counter()
    r = w.call("exec", {"code": code, "soft_timeout_s": 1}, deadline_s=60)
    assert r["status"] == "timeout" and "caught the interrupt" in r["traceback"]
    assert 1.5 < time.perf_counter() - t0 < 4.5          # re-interrupted every second after the deadline


def test_except_exception_does_not_catch_the_soft_timeout(w):
    r = w.call("exec", {"code": "try:\n    while True:\n        pass\nexcept Exception:\n    result = 'caught'",
                        "soft_timeout_s": 1}, deadline_s=60)
    assert r["status"] == "timeout" and r["result"] is None   # SoftTimeout is a BaseException


def test_exec_unvalidated_is_refused_in_run_mode(plain):
    with pytest.raises(WorkerError, match="selftest"):
        plain.call("exec_unvalidated", {"code": "result = 1"})
    assert plain.call("exec", {"code": "result = 2"})["result"] == 2


# ------------------------------------------------------------------------------------------------ runtime layers
@pytest.mark.parametrize("code,layer", [
    ("import os", "builtins"),
    ("n = 'te' + 'xts'\nt = getattr(bpy.data, n)", "builtins"),
    ("import numpy as np\nnp.save(r'{root}/leak.npy', np.zeros(3))", "audit"),
    ("import json\njson.codecs.open(r'{root}/leak.txt', 'w')", "audit"),
    ("try:\n    np.save(r'{root}/leak2.npy', np.zeros(3))\nexcept Exception:\n    pass", "audit"),
])
def test_runtime_layers_refuse_unvalidated_code(w, root, code, layer):
    r = w.call("exec_unvalidated", {"code": code.format(root=str(root).replace("\\", "/"))}, deadline_s=60)
    assert r["status"] == "refused_runtime", r
    assert any(v["layer"] == layer for v in r["runtime_violations"]), r["runtime_violations"]
    assert not list(root.glob("leak*"))


def test_c_level_blend_save_is_caught_and_the_stray_file_deleted(w, root):
    target = root / "stray.blend"
    r = w.call("exec_unvalidated", {"code": f"bpy.ops.wm.save_as_mainfile(filepath={str(target)!r}, copy=True)"},
               deadline_s=60)
    assert r["status"] == "refused_runtime"
    assert any(v["layer"] == "blend_io" for v in r["runtime_violations"])
    assert r["stray_files"] and r["stray_files"][0]["deleted"] and not target.exists()


def test_persistence_surfaces_are_checked_after_the_call(w):
    r = w.call("exec_unvalidated", {"code": "bpy.app.handlers.depsgraph_update_post.append(print)"}, deadline_s=60)
    assert r["status"] == "refused_runtime"
    assert any(v["layer"] == "persistence" for v in r["runtime_violations"])
    w.call("exec_unvalidated", {"code": "bpy.app.handlers.depsgraph_update_post.clear()"}, deadline_s=60)


def test_trusted_paths_are_confined_to_the_job_root(w, tmp_path):
    with pytest.raises(WorkerError, match="outside the job root"):
        w.call("checkpoint_save", {"path": str(tmp_path / "x.blend")}, deadline_s=60)


# ------------------------------------------------------------------------------------------------ hard path
def test_a_numpy_c_call_takes_the_hard_path(root):
    worker = WorkerClient(root / "hard").start()
    pid = worker.pid
    members = worker.job.pids() if os.name == "nt" else [pid]     # Blender and the helper process it starts
    assert pid in members
    t0 = time.perf_counter()
    with pytest.raises(WorkerDied) as info:
        worker.call("exec", {"code": "a = np.random.default_rng(0).random((2600, 2600))\nnp.linalg.svd(a)",
                             "soft_timeout_s": 2}, deadline_s=6)
    dt = time.perf_counter() - t0
    assert info.value.reason == "hard_timeout"
    assert dt < 6 + 15, dt
    assert worker.proc.poll() is not None and not worker.alive
    assert not [m for m in members if procs.pid_alive(m)]     # the Job Object reaped every member


# ------------------------------------------------------------------------------------------------ read-only + fonts
def test_scene_summary_reports_parts_and_warnings(w):
    made = w.call("exec", {"code": "import bmesh\nfor name, role in (('S_open', None), ('S_lens', 'lens')):\n"
                                   "    bm = bmesh.new()\n    bmesh.ops.create_cube(bm, size=2)\n"
                                   "    if role is None:\n"
                                   "        bmesh.ops.delete(bm, geom=list(bm.faces)[:1], context='FACES_ONLY')\n"
                                   "    me = bpy.data.meshes.new(name)\n    bm.to_mesh(me)\n    bm.free()\n"
                                   "    ob = bpy.data.objects.new(name, me)\n"
                                   "    bpy.context.scene.collection.objects.link(ob)\n"
                                   "    if role:\n        ob['partRole'] = role\n"}, deadline_s=60)
    assert made["status"] == "ok", made
    s = w.call("scene_summary", {"objects": ["S_open", "S_lens"]})
    lens, opened = s["objects"]["S_lens"], s["objects"]["S_open"]
    assert lens["partRole"] == "lens" and lens["closed"] is True and lens["loose_parts"] == 1
    assert lens["vertices"] == 8 and lens["triangles"] == 12 and len(lens["bbox_mm"]) == 2
    assert opened["closed"] is False and opened["partRole"] is None
    assert any("S_open" in x and "partRole" in x for x in s["warnings"])
    assert len(s["fingerprint"]) == 64


def test_load_font_is_allowlisted_and_packed(w):
    r = w.call("load_font", {"family": "arial"}, deadline_s=60)
    assert r["status"] == "ok" and r["result"]["family"] == "arial" and r["changed"] is True
    packed = w.call("exec_unvalidated", {"code": f"result = bpy.data.fonts[{r['result']['name']!r}].packed_file "
                                                 "is not None"})["result"]
    assert packed is True
    again = w.call("load_font", {"family": "arial"}, deadline_s=60)
    assert again["status"] == "ok" and again["changed"] is False          # check_existing: nothing new
    bad = w.call("load_font", {"family": "../../evil"}, deadline_s=60)
    assert bad["status"] == "refused" and bad["code"] == "E_FONT"
