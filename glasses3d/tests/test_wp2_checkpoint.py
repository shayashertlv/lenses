"""WP2 stage A: checkpoints and the per-call protocol (g3d/checkpoint.py + agent/journal.py), WP2_DESIGN §3.2, §5.

* restore stability on three scenes (synthetic export fixture, Prada, q001): c0000 reopened in a NEW worker and
  restored in place both give the recorded fingerprint (stage A stop rule: any mismatch);
* the prototype defects are handled: (1) the VIEWER Render Result and the active file path are outside the
  fingerprint, (2) pixel edits on an unpacked generated image survive a checkpoint and a restart, (3) unassigned
  datablocks survive;
* every non-ok call (error, timeout, refused_runtime) leaves the head fingerprint and adds 0 checkpoints; a refused
  snippet never reaches the worker; drift is checkpointed and logged; a restore is verified and recorded;
* kill and restart: a numpy C call past the hard timeout returns worker_restarted on a new pid at the verified head;
  an external kill between calls is recovered the same way; 3 restarts in 10 minutes end the run (WorkerFailed).
Needs Blender 5.2; the Prada and q001 scenes are data (skipped, or failed under G3D_REQUIRE_DATA=1)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from glasses3d.agent import journal as J
from glasses3d.agent.journal import BlenderSession, WorkerFailed
from glasses3d.g3d_host import blender

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")

G3D = Path(__file__).resolve().parents[1]
JOBS = G3D.parent / "automation" / "data" / "blender_agent" / "studio" / "jobs"
STUDIO = {"prada": JOBS / "8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend",
          "q001": JOBS / "3b8a1c7ae8114868b49b298b681f1b29/original/scene.blend"}
FIXTURE = G3D / "tests" / "blender" / "build_export_fixture.py"
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("wp2fixture")
    blender.run(None, FIXTURE, [d, "ok"], timeout_s=300)
    out = d / "fixture-ok.blend"
    assert out.is_file()
    return out


def scene_copy(name: str, synthetic: Path, tmp: Path) -> Path:
    src = synthetic if name == "synthetic" else STUDIO[name]
    need(src.is_file(), f"{name} scene missing: {src}")
    dst = tmp / f"seed-{name}.blend"
    shutil.copy2(src, dst)
    os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
    return dst


def index_rows(root: Path) -> list[dict]:
    return [json.loads(x) for x in (root / "checkpoints" / "index.jsonl").read_text().splitlines() if x.strip()]


# ------------------------------------------------------------------------------------------------ three scenes
@pytest.mark.parametrize("name", ["synthetic", "prada", "q001"])
def test_checkpoint_restore_is_fingerprint_stable(name, synthetic, tmp_path):
    seed = scene_copy(name, synthetic, tmp_path)
    src_sha = J.procs and __import__("hashlib").sha256(seed.read_bytes()).hexdigest()
    s = BlenderSession(tmp_path / "job", seed_blend=seed)
    try:
        info = s.start()
        c0 = info["head"]
        assert c0["id"] == "c0000" and c0["kind"] == "start"
        live_state = s.worker.call("fingerprint", {"state": True})["state"]
        # restored in place in the same worker
        r = s.worker.call("checkpoint_restore", {"path": str(s.ck_dir / c0["file"]),
                                                 "expect_fingerprint": c0["fingerprint"],
                                                 "expect_sha256": c0["sha256"]}, deadline_s=120)
        assert r["matches"] is True, name
        # reopened by a NEW worker (what a restart does)
        s.close()
        s2 = BlenderSession(tmp_path / "job", seed_blend=seed)
        s2.start()
        try:
            assert s2.worker.hello["fingerprint"] == c0["fingerprint"], name
            reopened = s2.worker.call("fingerprint", {"state": True})["state"]
            differing = [k for k in live_state if k != "summary" and live_state[k] != reopened[k]]
            assert differing == [], differing
            assert s2.defects == []
        finally:
            s2.close()
    finally:
        s.close()
    ck = tmp_path / "job" / "checkpoints" / "c0000.blend"
    assert not os.access(ck, os.W_OK)                                 # read-only
    assert __import__("hashlib").sha256(ck.read_bytes()).hexdigest() == c0["sha256"]
    assert __import__("hashlib").sha256(seed.read_bytes()).hexdigest() == src_sha     # the seed is never written


# ------------------------------------------------------------------------------------------------ one session
@pytest.fixture
def session(synthetic, tmp_path_factory):
    root = tmp_path_factory.mktemp("wp2session")
    seed = root / "seed.blend"
    shutil.copy2(synthetic, seed)
    s = BlenderSession(root / "job", seed_blend=seed, selftest=True, calls_dir=root / "job" / "tools")
    s.start()
    yield s
    s.close()


def n_checkpoints(s: BlenderSession) -> int:
    return len(s.index.checkpoints())


def test_a_changing_call_checkpoints_and_a_read_only_one_does_not(session):
    before = n_checkpoints(session)
    r = session.execute("import bmesh\nbm = bmesh.new()\nbmesh.ops.create_cube(bm, size=3)\n"
                        "me = bpy.data.meshes.new('Box')\nbm.to_mesh(me)\nbm.free()\n"
                        "ob = bpy.data.objects.new('Box', me)\nbpy.context.scene.collection.objects.link(ob)\n"
                        "ob['partRole'] = 'frame'\nresult = len(me.vertices)", label="box")
    assert r["status"] == "ok" and r["result"] == 8 and r["restored_to"] is None
    assert r["checkpoint"] == session.head["id"] and n_checkpoints(session) == before + 1
    assert r["scene_delta"]["added"] == ["Box"]
    r2 = session.execute("result = sorted(o.name for o in bpy.data.objects)")
    assert r2["status"] == "ok" and r2["checkpoint"] == r["checkpoint"] and n_checkpoints(session) == before + 1
    assert session.fingerprint() == session.head["fingerprint"]


@pytest.mark.parametrize("code,status,unvalidated", [
    ("ob = bpy.data.objects.new('Tmp', None)\nbpy.context.scene.collection.objects.link(ob)\nraise ValueError('x')",
     "error", False),
    ("ob = bpy.data.objects.new('Tmp', None)\nbpy.context.scene.collection.objects.link(ob)\nwhile True:\n    pass",
     "timeout", False),
    ("ob = bpy.data.objects.new('Tmp', None)\nbpy.context.scene.collection.objects.link(ob)\nimport os", "refused_runtime",
     True),
])
def test_a_failed_call_restores_checkpoint_before(session, code, status, unvalidated):
    head = session.head
    before = n_checkpoints(session)
    r = session.execute(code, soft_timeout_s=2, unvalidated=unvalidated)
    assert r["status"] == status, r
    assert r["restored_to"] == head["id"] and r["checkpoint"] == head["id"]
    assert n_checkpoints(session) == before and session.fingerprint() == head["fingerprint"]


def test_a_refused_snippet_never_reaches_the_worker(session):
    head, before = session.head, n_checkpoints(session)
    r = session.execute("np.save('x', np.zeros(3))\nfrom numpy import load")
    assert r["status"] == "refused" and len(r["violations"]) == 2 and r["restored_to"] is None
    assert n_checkpoints(session) == before and session.fingerprint() == head["fingerprint"]


def test_c_level_blend_io_is_refused_restored_and_cleaned(session, tmp_path):
    head = session.head
    stray = session.root / "stray.blend"
    r = session.execute(f"ob = bpy.data.objects.new('Tmp', None)\nbpy.context.scene.collection.objects.link(ob)\n"
                        f"bpy.ops.wm.save_as_mainfile(filepath={str(stray)!r})", unvalidated=True)
    assert r["status"] == "refused_runtime" and r["restored_to"] == head["id"]
    assert not stray.exists() and r["stray_files"][0]["deleted"]
    assert session.fingerprint() == head["fingerprint"]
    assert session.worker.call("ping")["file"].endswith(head["file"])        # the active file is the checkpoint again


def test_a_restore_clears_handlers_a_call_registered(session):
    probe = "result = [repr(h) for h in bpy.app.handlers.load_post]"
    baseline = session.worker.call("exec_unvalidated", {"code": probe})["result"]
    r = session.execute("bpy.app.handlers.load_post.append(print)", unvalidated=True)
    assert r["status"] == "refused_runtime" and r["restored_to"] == session.head["id"]
    after = session.worker.call("exec_unvalidated", {"code": probe})["result"]
    assert after == baseline and not [h for h in after if "print" in h]


def test_defect_2_and_3_pixels_and_unassigned_datablocks_survive_a_restart(session):
    code = ("img = bpy.data.images.new('Grain', 16, 8)\npx = np.zeros((8, 16, 4), np.float32)\n"
            "px[..., 0] = np.linspace(0, 1, 16)[None, :]\npx[..., 3] = 1\nimg.pixels.foreach_set(px.ravel())\n"
            "m = bpy.data.materials.new('Loose material')\nm.diffuse_color = (0.2, 0.4, 0.6, 1)\n"
            "c = bpy.data.curves.new('Loose curve', 'CURVE')\nresult = img.is_dirty")
    r = session.execute(code)
    assert r["status"] == "ok" and r["checkpoint"] == session.head["id"]
    row = session.head
    assert "Grain" in row["packed_images"]
    assert {"materials:Loose material", "curves:Loose curve"} <= set(row["fake_user_added"])
    old_pid = session.worker.pid
    session.worker.kill("test")                          # the next call finds the worker dead and restarts it
    r = session.execute("img = bpy.data.images['Grain']\npx = np.zeros(16 * 8 * 4, np.float32)\n"
                        "img.pixels.foreach_get(px)\npx = px.reshape(8, 16, 4)\n"
                        "result = [round(float(px[0, 15, 0]), 3), 'Loose material' in bpy.data.materials, "
                        "'Loose curve' in bpy.data.curves]")
    assert r["status"] == "worker_restarted" and r["worker_pid"] != old_pid and r["restored_to"] == row["id"]
    r = session.execute("img = bpy.data.images['Grain']\npx = np.zeros(16 * 8 * 4, np.float32)\n"
                        "img.pixels.foreach_get(px)\npx = px.reshape(8, 16, 4)\n"
                        "result = [round(float(px[0, 15, 0]), 3), 'Loose material' in bpy.data.materials, "
                        "'Loose curve' in bpy.data.curves]")
    assert r["status"] == "ok" and r["result"] == [1.0, True, True]
    assert session.fingerprint() == row["fingerprint"]


def test_defect_1_viewer_images_and_the_file_path_are_outside_the_fingerprint(session, tmp_path):
    fp = session.fingerprint()
    r = session.worker.call("exec_unvalidated", {"code": (
        "sc = bpy.data.scenes.new('g3d_render_t')\ncd = bpy.data.cameras.new('c')\nco = bpy.data.objects.new('c', cd)\n"
        "sc.collection.objects.link(co)\nsc.camera = co\nsc.render.resolution_x = sc.render.resolution_y = 16\n"
        "bpy.ops.render.render(scene=sc.name)\nviewers = [i.name for i in bpy.data.images if i.source == 'VIEWER']\n"
        "bpy.data.objects.remove(co)\nbpy.data.cameras.remove(cd)\nbpy.data.scenes.remove(sc)\nresult = viewers")},
        deadline_s=120)
    assert r["status"] == "ok" and r["result"], r       # a Render Result exists now
    assert session.fingerprint() == fp
    assert session.worker.call("ping")["file"] != ""    # the active file differs from c0000's seed path, same fp


def test_drift_is_checkpointed_and_logged(session):
    result = session.execute("ob = bpy.data.objects.new('Box', None)\nbpy.context.scene.collection.objects.link(ob)")
    assert result["status"] == "ok", result
    head, before = session.head, n_checkpoints(session)
    session.worker.call("exec", {"code": "bpy.data.objects['Box'].location.z = 7.5"}, deadline_s=60)   # behind the journal
    r = session.execute("result = bpy.data.objects['Box'].location.z")
    assert r["status"] == "ok" and r["result"] == 7.5
    drift = session.index.checkpoints()[-1]
    assert drift["kind"] == "drift" and drift["parent"] == head["id"] and n_checkpoints(session) == before + 1
    assert session.defects and session.defects[-1]["kind"] == "drift"


def test_save_label_restore_and_list(session):
    result = session.execute("ob = bpy.data.objects.new('Box', None)\nbpy.context.scene.collection.objects.link(ob)")
    assert result["status"] == "ok", result
    head = session.head
    r = session.save_checkpoint("candidate A")
    assert r["status"] == "ok" and r["checkpoint"] == head["id"] and r["saved"] is False
    assert r["labels"][-1] == "candidate A"
    session.execute("bpy.data.objects['Box'].location.x = 3.0")
    moved = session.head
    assert moved["id"] != head["id"]
    r = session.restore_checkpoint(head["id"])
    assert r["status"] == "ok" and r["changed"] and r["restored_from"] == head["id"]
    row = session.head
    assert row["kind"] == "restore" and row["file"] == head["file"] and row["fingerprint"] == head["fingerprint"]
    assert session.fingerprint() == head["fingerprint"]
    assert session.restore_checkpoint(head["id"])["changed"] is False       # already there: nothing recorded
    listed = session.list_checkpoints()
    assert listed[-1]["id"] == row["id"] and "candidate A" in [x for c in listed for x in c["labels"]]


def test_index_and_files_are_consistent(session):
    result = session.execute("ob = bpy.data.objects.new('Box', None)\nbpy.context.scene.collection.objects.link(ob)")
    assert result["status"] == "ok", result
    original = session.head
    result = session.execute("bpy.data.objects['Box'].location.x = 3.0")
    assert result["status"] == "ok", result
    result = session.restore_checkpoint(original["id"])
    assert result["status"] == "ok" and result["changed"], result
    rows = index_rows(session.root)
    cps = [r for r in rows if r["kind"] in J.HEAD_KINDS]
    assert len(cps) == 4 and cps[-1]["kind"] == "restore"
    assert [r["id"] for r in cps] == [f"c{i:04d}" for i in range(len(cps))]
    for r in cps:
        f = session.ck_dir / r["file"]
        assert f.is_file() and not os.access(f, os.W_OK)
        assert __import__("hashlib").sha256(f.read_bytes()).hexdigest() == r["sha256"]
    assert not list(session.ck_dir.glob("*.part.blend"))
    tools = sorted((session.root / "tools").iterdir())
    assert tools and all((d / "journal.json").is_file() and (d / "result.json").is_file() for d in tools)


# ------------------------------------------------------------------------------------------------ kill / restart
@pytest.mark.timing
def test_a_numpy_c_call_past_the_hard_timeout_restarts_at_the_verified_head(session):
    head, old_pid, before = session.head, session.worker.pid, n_checkpoints(session)
    t0 = time.perf_counter()
    r = session.execute("a = np.random.default_rng(0).random((2600, 2600))\nnp.linalg.svd(a)", soft_timeout_s=2,
                        hard_timeout_s=10)
    dt = time.perf_counter() - t0
    assert r["status"] == "worker_restarted" and r["reason"] == "hard_timeout", r
    assert r["worker_pid"] != old_pid and r["restored_to"] == head["id"]
    assert r["warm_spare"] is True                       # started at soft + 1 s, verified on the head
    assert session.fingerprint() == head["fingerprint"] and n_checkpoints(session) == before
    assert dt <= 15.0, f"worker_restarted after {dt:.1f} s (criterion 8: <= 15 s)"
    assert session.execute("result = 1 + 1")["result"] == 2


def test_three_restarts_within_ten_minutes_end_the_run(synthetic, tmp_path, monkeypatch):
    seed = scene_copy("synthetic", synthetic, tmp_path)
    s = BlenderSession(tmp_path / "job", seed_blend=seed)
    s.start()
    try:
        for i in range(2):
            s.worker.kill("test")
            assert s.execute("result = 1")["status"] == "worker_restarted"
        s.worker.kill("test")
        with pytest.raises(WorkerFailed) as info:
            s.execute("result = 1")
        assert info.value.head["id"] == "c0000" and "failed_worker" in str(info.value)
    finally:
        s.close()


def test_a_session_resumes_on_its_head(session, tmp_path):
    head = session.head
    s2 = BlenderSession(session.root)
    s2.start()
    try:
        assert s2.head["id"] == head["id"] and s2.worker.hello["fingerprint"] == head["fingerprint"]
        assert s2.defects == []
    finally:
        s2.close()


@pytest.mark.parametrize("has_parent", [False, True])
def test_reopen_rejects_changed_bytes_even_when_the_scene_fingerprint_matches(tmp_path, has_parent):
    from types import SimpleNamespace
    import hashlib

    s = BlenderSession(tmp_path / "job")
    s.ck_dir.mkdir(parents=True)
    good = b"checkpoint bytes"
    digest = hashlib.sha256(good).hexdigest()
    parent = {"id": "c0000", "kind": "start", "parent": None, "file": "c0000.blend",
              "sha256": digest, "fingerprint": "parent-fingerprint"}
    if has_parent:
        s.index.append(parent)
        (s.ck_dir / parent["file"]).write_bytes(good)
    head = {"id": "c0001" if has_parent else "c0000", "kind": "save", "parent": "c0000" if has_parent else None,
            "file": "head.blend", "sha256": digest, "fingerprint": "same-scene"}
    s.index.append(head)
    (s.ck_dir / head["file"]).write_bytes(good + b"changed bytes")
    restored = []

    def restore(method, params, **kwargs):
        restored.append((method, params))
        return {"matches": True}

    s.worker = SimpleNamespace(hello={"fingerprint": "same-scene"}, call=restore)
    if has_parent:
        s._verify_open(head)
        assert s.head["kind"] == "fallback" and s.head["restored_from"] == "c0000"
        assert restored[0][0] == "checkpoint_restore"
        assert restored[0][1]["expect_sha256"] == digest
        assert restored[0][1]["expect_fingerprint"] == "parent-fingerprint"
    else:
        with pytest.raises(J.CheckpointMismatch, match="recorded bytes/fingerprint"):
            s._verify_open(head)
        assert restored == []
    assert s.defects[0]["kind"] == "head_mismatch"
