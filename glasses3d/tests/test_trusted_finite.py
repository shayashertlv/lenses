"""M11 task 1: invalid geometry/arguments are ordinary refusals, never fatal RPC frames."""
import ast
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.agent.seeing import Seeing, SeeingError
from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.worker_client import WorkerClient
from glasses3d.g3d import calls, rpc
from glasses3d.g3d.seat import deform, geom, plan
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.g3d_host import blender
from glasses3d.tests.fixtures import seat_fixtures as SF


def test_bounds_refusal_reaches_seeing_without_caching_bad_measurements(tmp_path):
    refusal = {"status": "refused", "code": "E_NONFINITE", "message": "Broken lens: non-finite vertex"}
    session = SimpleNamespace(root=tmp_path, head={"fingerprint": "head"},
                              call_read=lambda *a, **kw: {"status": "ok", "result": refusal})
    seeing = Seeing(session)
    with pytest.raises(SeeingError, match="Broken lens: non-finite vertex") as error:
        seeing.bounds("bad-bounds")
    assert error.value.code == "E_NONFINITE" and seeing._bounds is None


def test_nonfinite_read_preserves_scene_integrity_and_journal_restores():
    result = calls.trusted_result({"measurement_mm": float("nan"), "unchanged": False,
                                   "fingerprint_before": "before", "fingerprint_after": "after"}, "render")
    result = rpc.decode(rpc.encode(result)[4:])
    restored, defects = [], []
    head = {"id": "c0001", "fingerprint": "before"}
    session = SimpleNamespace(worker=SimpleNamespace(call=lambda *a, **kw: result), head=head,
                              _restore=restored.append, _defect=lambda *args: defects.append(args))
    out = BlenderSession.call_read(session, "render", "render", {}, deadline_s=30., call_id="bad-read")
    assert out["result"]["code"] == "E_NONFINITE" and out["restored_to"] == "c0001"
    assert restored == [head] and defects[0][0] == "read_only_changed"
    assert "before -> after" in defects[0][1]


@pytest.mark.parametrize("ramp", [0., -1., float("nan"), float("inf")])
def test_lift_field_refuses_invalid_ramp_even_when_no_lift_is_needed(ramp):
    with pytest.raises(ValueError, match="ramp_mm"):
        plan.lift_field(np.array([0.]), np.array([0.]), np.array([0.]), 10., ramp)


def test_saved_ramp_zero_reproducer_is_a_named_finite_refusal():
    sc = SF.browline_scene()
    lens = sc["lens"]
    wire, brow = sc["parts"]["Eyewire"], sc["parts"]["Brow"]
    result = deform.lift_arrays(wire.V, wire.T, geom.edge_table(wire.T).edges,
                               [("Brow", brow.V, brow.T)], [], lens.V, lens.T, NumpyBackend,
                               [sc["range"]], lens_poly_edges_raw=lens.poly_edges,
                               lens_name="Lens", eyewire_name="Eyewire", ramp=0.).as_dict()
    result = rpc.decode(rpc.encode(result)[4:])
    assert result["status"] == "refused" and result["code"] == "E_ARGS"
    assert "Eyewire" in result["message"] and "ramp_mm" in result["message"]


@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")
def test_real_worker_bounds_nan_vertex_refuses_and_worker_remains_usable(tmp_path):
    """SS-6: old bounds reply raised WorkerError(FrameError); the valid repair remains possible now."""
    with WorkerClient(tmp_path / "worker") as worker:
        pid = worker.pid
        result = worker.call("exec", {"code": 'bpy.data.objects["Cube"].data.vertices[0].co.x = float("nan")'})
        assert result["status"] == "ok", result
        result = rpc.decode(rpc.encode(worker.call("bounds"))[4:])
        assert result["status"] == "refused" and result["code"] == "E_NONFINITE"
        assert result["object"] == "Cube" and "vertex coordinates" in result["message"]
        repair = worker.call("exec", {"code": 'bpy.data.objects["Cube"].data.vertices[0].co.x = -1.0'})
        assert repair["status"] == "ok" and worker.pid == pid
        bounds = worker.call("bounds")
        assert bounds["objects"]["Cube"]["lo"] and "status" not in bounds


@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")
def test_real_worker_hide_zero_ramp_refuses_before_any_scene_write(tmp_path):
    with WorkerClient(tmp_path / "worker") as worker:
        before = worker.call("fingerprint")["fingerprint"]
        result = worker.call("g3d_call", {"op": "hide_eyewire", "kwargs": {
            "eyewire": "Cube", "under": "Cube", "hidden_x_range_mm": [[0., 1.]], "ramp_mm": 0.}})
        result = rpc.decode(rpc.encode(result)[4:])
        assert result["status"] == "refused" and result["code"] == "E_ARGS"
        assert "ramp_mm" in result["message"]
        assert worker.call("fingerprint")["fingerprint"] == before


def test_direct_blender_adapter_validates_before_loading_geometry():
    path = Path(__file__).resolve().parents[1] / "g3d" / "seat_bpy.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "hide_eyewire"]

    def refuse(report, code, message):
        return {**report, "code": code, "message": message}

    namespace = {"__package__": "glasses3d.g3d", "time": time, "_refuse": refuse,
                 "_obj": lambda *_: pytest.fail("invalid ramp must be refused before touching Blender geometry")}
    exec(compile(tree, str(path), "exec"), namespace)
    result = namespace["hide_eyewire"]("Wire", "Brow", [[0., 1.]], ramp_mm=0.)
    assert rpc.decode(rpc.encode(result)[4:])["code"] == "E_ARGS"
    assert "Wire" in result["message"] and "ramp_mm" in result["message"]
