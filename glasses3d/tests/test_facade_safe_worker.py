"""All new facade helpers execute through the real worker's armed safety layers.

Disposable meshes only; no model inputs, external providers or selftest bypass.
These tests complement native helper tests by exercising the public exec path.
"""
from __future__ import annotations

import json
import pytest

from glasses3d.agent.worker_client import WorkerClient
from glasses3d.g3d_host import blender
from glasses3d.tests.test_owner_health import box


pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender is unavailable")


@pytest.fixture(scope="module")
def worker(tmp_path_factory):
    client = WorkerClient(tmp_path_factory.mktemp("facade-safe-worker")).start()
    yield client
    client.close()


def execute(worker, code):
    reply = worker.call("exec", {"code": code, "soft_timeout_s": 30.}, deadline_s=45)
    assert reply["status"] == "ok", json.dumps(reply, indent=2)
    assert reply["runtime_violations"] == [], reply
    return reply["result"]


def test_quadratic_lens_runs_through_guarded_from_import_and_keeps_spec_refusal(worker):
    result = execute(worker, """
from g3d.lib import quadratic_lens
result = quadratic_lens([[-3,-2],[3,-2],[3,2],[-3,2]], [1,0,0,.002,0,.003],
                        1.2, max_edge_mm=2., max_chord_mm=.003, name='Safe lens')
""")
    assert result["status"] == "ok" and result["object"] == "Safe lens"
    assert result["max_optical_edge_mm"] <= 2.
    assert result["max_quadratic_edge_chord_mm"] <= .003
    assert result["axial_thickness_mm"] == 1.2
    # The positive example really made a closed lens, not merely an accepted name.
    physical = execute(worker, """
obj = bpy.data.objects['Safe lens']
edges = {}
for polygon in obj.data.polygons:
    ids = list(polygon.vertices)
    for a,b in zip(ids,ids[1:]+ids[:1]):
        key = tuple(sorted((a,b)))
        edges[key] = edges.get(key,0)+1
result = {'closed': all(count==2 for count in edges.values()), 'role': obj['partRole']}
""")
    assert physical == {"closed": True, "role": "lens"}
    before = worker.call("fingerprint")["fingerprint"]
    invalid = execute(worker, """
result = g3d.quadratic_lens([[-3,-2],[3,-2],[3,2],[-3,2]], [1,0,0,.002,0,.003],
                           -1., name='Invalid lens')
""")
    assert invalid["status"] == "refused" and invalid["code"] == "E_SPEC"
    assert worker.call("fingerprint")["fingerprint"] == before


def fixture_code(kind):
    source, _ = box()
    vertices = source["vertices_local"]
    if kind == "mesh_cleanup":
        vertices = vertices + [vertices[0].copy()]
    if kind == "symmetrize_mesh":
        vertices[0][0] = -2.1
    code = f"""
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj,do_unlink=True)
mesh = bpy.data.meshes.new('Safe health fixture')
mesh.from_pydata({vertices!r}, [], {source['polygons']!r})
mesh.update()
obj = bpy.data.objects.new('Safe health fixture',mesh)
bpy.context.scene.collection.objects.link(obj)
obj['partRole'] = 'frame'
"""
    if kind == "consistent_normals":
        normals = [[-v for v in row] for row in source["corner_normals"]]
        code += f"mesh.normals_split_custom_set({normals!r})\n"
    return code


@pytest.mark.parametrize("kind", ["mesh_cleanup", "consistent_normals", "symmetrize_mesh", "controlled_decimate"])
def test_each_mesh_health_helper_dry_run_and_apply_under_armed_worker(worker, kind):
    execute(worker, fixture_code(kind))
    before = worker.call("fingerprint")["fingerprint"]
    extra = ", [[1,0],[2,3],[5,4],[6,7]]" if kind == "symmetrize_mesh" else ""
    # Both documented from-import spellings and the prebound facade are covered.
    module = "g3d.lib" if kind in {"mesh_cleanup", "controlled_decimate"} else "g3d"
    dry = execute(worker, f"from {module} import {kind} as helper\n"
                  f"result = helper('Safe health fixture'{extra}, dry_run=True)")
    assert dry["status"] == "ok" and dry["dry_run"] is True, dry
    assert worker.call("fingerprint")["fingerprint"] == before
    applied = execute(worker, f"result = g3d.{kind}('Safe health fixture'{extra})")
    assert applied["status"] == "ok" and applied["checks"]["ok"], applied
    assert applied["dry_run"] is False
    assert applied["operations"][0]["physical"]["closed"]
    current = execute(worker, """
obj = bpy.data.objects['Safe health fixture']
result = {'vertices':len(obj.data.vertices),'x0':obj.data.vertices[0].co.x}
""")
    if kind == "mesh_cleanup":
        assert current["vertices"] == 9
    elif kind == "controlled_decimate":
        assert current["vertices"] == 8
    elif kind == "symmetrize_mesh":
        assert current["x0"] == -2.
    else:
        assert applied["operations"][0]["valid_authored_smooth_and_split_normals_preserved"]
    before = worker.call("fingerprint")["fingerprint"]
    refused = execute(worker, f"result = g3d.{kind}('Safe health fixture'{extra}, vertex_ids=[99999])")
    assert refused["status"] == "refused"
    assert worker.call("fingerprint")["fingerprint"] == before


def test_helper_registration_does_not_open_private_module_or_file_access(worker):
    before = worker.call("fingerprint")["fingerprint"]
    for code in ("result = g3d.mesh_cleanup.__globals__",
                 "from g3d.mesh_health_bpy import mesh_cleanup",
                 "g3d.export_glb('C:/unsafe.glb')"):
        reply = worker.call("exec", {"code": code}, deadline_s=30)
        assert reply["status"] == "refused", reply
    assert worker.call("fingerprint")["fingerprint"] == before
