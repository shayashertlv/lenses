"""The real worker/journal boundary preserves an intervening UV revision on a failed stage write.

The fault is injected into the host's received response after a real successful Blender write. This exercises
the existing non-ok checkpoint restore without adding a production injection endpoint or bypassing safe mode.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from glasses3d.agent.journal import BlenderSession
from glasses3d.g3d.temple import fixtures as F
from glasses3d.g3d.temple.build import build_temple
from glasses3d.g3d_host import blender


pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")


def _seed_code():
    built = build_temple(F.straight())
    code = ["for ob in list(bpy.data.objects):\n    bpy.data.objects.remove(ob, do_unlink=True)"]
    for side in (1, -1):
        vertices, polygons, _ = built.world(side)
        name = f"Temple {side:+d}"
        code.extend([
            f"mesh = bpy.data.meshes.new({name!r})",
            f"mesh.from_pydata({vertices.tolist()!r}, [], {polygons!r})",
            f"ob = bpy.data.objects.new({name!r}, mesh)",
            "bpy.context.scene.collection.objects.link(ob)",
            "ob['partRole'] = 'temple'",
            "uv = mesh.uv_layers.new(name='authored')",
            "for loop in mesh.loops:\n"
            "    co = mesh.vertices[loop.vertex_index].co\n"
            "    uv.data[loop.index].uv = [-co.z / 150., .2 + co.y / 40.]",
        ])
    return "\n".join(code)


def _read(session, code):
    result = session.worker.call("exec", {"code": code}, deadline_s=30)
    assert result["status"] == "ok", result
    return result["result"]


def _uv_bytes(session):
    """Read exact float32 bytes in bounded chunks below the worker's result-size limit."""
    catalog = _read(session,
                    "result = [[ob.name, ob.data.uv_layers.active_index, "
                    "[[uv.name, len(uv.data), uv.active_render, uv.active_clone] "
                    "for uv in ob.data.uv_layers]] for ob in sorted(bpy.data.objects, key=lambda o: o.name) "
                    "if ob.type == 'MESH']")
    values = {}
    for name, _, layers in catalog:
        for layer, count, _, _ in layers:
            chunks = []
            for start in range(0, count, 400):
                code = (f"uv = bpy.data.objects[{name!r}].data.uv_layers[{layer!r}]\n"
                        f"values = np.asarray([list(uv.data[i].uv) for i in range({start}, "
                        f"{min(start + 400, count)})], dtype=np.float32)\n"
                        "result = values.tobytes().hex()")
                chunks.append(bytes.fromhex(_read(session, code)))
            values[(name, layer)] = b"".join(chunks)
    return catalog, values


def _apply(session, spec, call_id):
    return session.call_mutating("temple_stage", "temple_stage_apply",
                                 {"spec": spec, "source_fingerprint": session.head["fingerprint"]},
                                 deadline_s=120, call_id=call_id)


def test_failed_worker_uv_write_restores_exact_intervening_revision(tmp_path, monkeypatch):
    root = tmp_path / "job"
    session = BlenderSession(root, calls_dir=root / "tools")
    session.start()
    try:
        seeded = session.execute(_seed_code(), label="authored UV seed")
        assert seeded["status"] == "ok", seeded
        spec = F.straight(nodes={"A": [20., 10., 4.], "B": [100., 10., 4.], "C": [50., -10., 6.]},
                          facets={"F": ["A", "B", "C"]})
        first = _apply(session, spec, "first-facet")
        assert first["status"] == "ok", first
        old_uv = _uv_bytes(session)

        edited = session.execute(
            "for ob in bpy.data.objects:\n"
            "    if ob.type == 'MESH':\n"
            "        uv = ob.data.uv_layers['authored']\n"
            "        for value in uv.data:\n"
            "            value.uv = [value.uv.x * .875 + .037, value.uv.y * .75 + .123]\n"
            "        ob['owner_uv_revision'] = 'preserve this later edit'",
            label="intervening authored UV edit")
        assert edited["status"] == "ok", edited
        before = deepcopy(session.head)
        uv_before = _uv_bytes(session)
        assert uv_before != old_uv
        checkpoint_path = session.ck_dir / before["file"]
        checkpoint_bytes = checkpoint_path.read_bytes()
        index_bytes = session.index.path.read_bytes()
        count = len(session.index.checkpoints())
        live_before = session.fingerprint()
        assert live_before == before["fingerprint"]

        actual_call = session.worker.call
        observed = []

        def fail_after_write(method, params=None, **kwargs):
            result = actual_call(method, params, **kwargs)
            if method == "temple_stage_apply":
                # The fault must follow an actual completed UV/geometry write, not an early refusal.
                assert result["status"] == "ok", result
                assert result["changed"] is True
                assert result["fingerprint_after"] != live_before
                observed.append(deepcopy(result))
                return {**result, "status": "failed", "message": "injected failure after the UV write"}
            return result

        monkeypatch.setattr(session.worker, "call", fail_after_write)
        revised = deepcopy(spec)
        revised["nodes"]["C"][2] = 5.0
        failed = _apply(session, revised, "failed-after-uv-write")

        assert len(observed) == 1
        assert failed["status"] == "failed"
        assert failed["checkpoint"] == failed["restored_to"] == before["id"]
        assert session.head == before
        assert session.fingerprint() == live_before
        assert _uv_bytes(session) == uv_before
        assert len(session.index.checkpoints()) == count
        assert session.index.path.read_bytes() == index_bytes
        assert checkpoint_path.read_bytes() == checkpoint_bytes
        assert hashlib.sha256(checkpoint_bytes).hexdigest() == before["sha256"]
        assert session.defects == []
        journals = [json.loads(path.read_text()) for path in (root / "tools").glob("*/journal.json")]
        journal = next(row for row in journals if row["call_id"] == "failed-after-uv-write")
        assert journal["status"] == "failed"
        assert journal["checkpoint_before"] == journal["checkpoint_after"] == journal["restored_to"] == before["id"]
        assert journal["fingerprint_before"] == live_before
        assert journal["fingerprint_after"] == observed[0]["fingerprint_after"]
    finally:
        session.close()
