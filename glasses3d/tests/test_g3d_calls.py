"""Trusted seating JSON boundary and mutation/read envelopes, without Blender or product data."""
from __future__ import annotations

import ast
import copy
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from glasses3d.g3d import calls as C


@pytest.mark.parametrize("op,kwargs", [
    ("seat_lens", {"lens": "Lens.001", "frame_parts": ["Frame", "Frame.001"], "frame_type": "acetate",
                   "capture_mm": 0.8, "lower_edge": None, "translucent": True, "uv": "keep", "dry_run": False}),
    ("hide_eyewire", {"eyewire": "Wire", "under": "Brow", "hidden_x_range_mm": [[-12.0, -3], [3, 12]],
                      "lens": None, "margin_mm": 0.1, "ramp_mm": 3.0, "lift_max_mm": 2.5, "dry_run": True}),
    ("check_lens_seating", {}),
    ("check_lens_seating", {"lenses": "Lens", "frame_parts": None, "frame_type": "semi_rimless",
                            "lower_edge": "nylor", "hidden_x_range_mm": None, "stations_table": True}),
    ("unseat_lens", {"lens": "Lens"}),
    ("unhide_eyewire", {"eyewire": "Wire", "lens": "Lens"}),
    ("print_uv", {"parts": ["Frame", "Frame.001"], "bake": "job/work/bake", "replace": True}),
    ("print_uv", {"parts": "Frame", "density": 12, "layer": "g3d_print", "dry_run": True}),
    ("attach_print", {"report": "job/work/bake", "parts": ["Frame.001"], "gain": [1, 1, 1]}),
    ("detach_print", {"parts": ["Frame.001"]}),
])
def test_valid_json_arguments_retain_exact_object_names_and_values(op, kwargs):
    params = {"op": op, "kwargs": kwargs, "expect_fingerprint": "a" * 64}
    before = copy.deepcopy(params)
    assert C.validate(params) == (op, kwargs)
    assert params == before


@pytest.mark.parametrize("params", [
    None, [], "seat_lens", {1: "seat_lens"}, {}, {"op": "seat_lens"}, {"op": [], "kwargs": {}},
    {"op": "export_glb", "kwargs": {}}, {"op": "__getattribute__", "kwargs": {}},
    {"op": "check_lens_seating", "kwargs": {}, "code": "print('no')"},
    {"op": "check_lens_seating", "kwargs": {"scene": "Scene"}},
    {"op": "check_lens_seating", "kwargs": [], "expect_fingerprint": "a"},
    {"op": "check_lens_seating", "kwargs": {}, "expect_fingerprint": False},
    {"op": "check_lens_seating", "kwargs": {1: "bad"}},
    {"op": "seat_lens", "kwargs": {}},
    {"op": "seat_lens", "kwargs": {"lens": ""}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens\0"}},
    {"op": "seat_lens", "kwargs": {"lens": {"name": "Lens"}}},
    {"op": "seat_lens", "kwargs": {"lens": SimpleNamespace(name="Lens")}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "frame_parts": ["Frame", 1]}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "frame_type": ["acetate"]}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "lower_edge": "invalid"}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "capture_mm": True}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "capture_mm": float("nan")}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "capture_mm": 10 ** 1000}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "translucent": 1}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "dry_run": "false"}},
    {"op": "seat_lens", "kwargs": {"lens": "Lens", "uv": "extrapolate"}},
    {"op": "hide_eyewire", "kwargs": {"eyewire": "Wire", "under": "Brow", "hidden_x_range_mm": [[1, 2, 3]]}},
    {"op": "hide_eyewire", "kwargs": {"eyewire": "Wire", "under": "Brow", "hidden_x_range_mm": [[1, float("inf")]]}},
    {"op": "hide_eyewire", "kwargs": {"eyewire": "Wire", "under": "Brow", "hidden_x_range_mm": [[True, 2]]}},
    {"op": "unseat_lens", "kwargs": {"lens": "Lens", "dry_run": True}},
    {"op": "unhide_eyewire", "kwargs": {"eyewire": "Wire", "lens": ["Lens"]}},
    {"op": "print_uv", "kwargs": {"parts": "Frame", "density": True}},
    {"op": "print_uv", "kwargs": {"parts": "Frame", "density": -1}},
    {"op": "print_uv", "kwargs": {"parts": "Frame", "density": float("inf")}},
    {"op": "print_uv", "kwargs": {"parts": "Frame", "layer": "UVMap"}},
    {"op": "attach_print", "kwargs": {"report": {"print_png": "private"}}},
    {"op": "attach_print", "kwargs": {"report": "bake", "gain": [1, 1, False]}},
    {"op": "attach_print", "kwargs": {"report": "bake", "transmission_map": True}},
    {"op": "detach_print", "kwargs": {}},
    {"op": "detach_print", "kwargs": {"parts": None}},
])
def test_invalid_arguments_are_refused(params):
    with pytest.raises(ValueError):
        C.validate(params)


@pytest.mark.parametrize("op,status", [(op, status) for op, statuses in sorted(C.SUCCESS.items())
                                      for status in sorted(statuses)])
def test_success_keeps_complete_domain_report_including_partial(op, status):
    result = {"status": status, "warnings": ["W_CHECK_FAILS"], "check": {"pass": False},
              "geometry": {"changes": [1, 2]}, "detail": "retained"}
    out = C.envelope(op, result)
    assert out == {"status": "ok", "op": op, "result": result}
    assert out["result"] is result


@pytest.mark.parametrize("status", ["refused", "error", "unknown", None])
def test_refused_or_unknown_domain_status_never_claims_applied(status):
    result = {"status": status, "code": "E_POSTCHECK", "message": "failed", "after": {"captured": False}}
    out = C.envelope("seat_lens", result)
    assert out["status"] == ("refused" if status == "refused" else "error")
    assert out["result"] == result and out["code"] == "E_POSTCHECK"


def _worker_methods(checkpoint):
    """Execute the production protocol methods against a scene-state double, with no Blender import."""
    path = Path(__file__).resolve().parents[1] / "g3d" / "worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_mutating", "m_g3d_call")]
    namespace = {"checkpoint": checkpoint, "time": time}
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace["m_g3d_call"]


class Scene:
    def __init__(self):
        self.value = "before"
        self.protected = False

    def scene_state(self):
        return self.value

    def fingerprint(self, state=None):
        return self.value if state is None else state

    def protect(self):
        self.protected = True
        return {"protected": True}

    def scene_delta(self, before, after):
        return {"before": before, "after": after}


@pytest.mark.parametrize("domain_status,outer_status", [("partial", "ok"), ("refused", "refused")])
def test_worker_mutation_protocol_retains_partial_and_marks_refusal_for_host_restore(monkeypatch,
                                                                                   domain_status, outer_status):
    scene = Scene()
    domain = {"status": domain_status, "code": "E_POSTCHECK", "check": {"pass": False}}

    def invoke(op, kwargs):
        scene.value = "after"
        return domain

    monkeypatch.setattr(C, "invoke", invoke)
    out = _worker_methods(scene)({"op": "seat_lens", "kwargs": {"lens": "Lens"}, "expect_fingerprint": "before"})
    assert out["status"] == outer_status and out["result"] == domain and out["fingerprint_before"] == "before"
    assert scene.protected is (outer_status == "ok")
    if outer_status == "ok":
        assert out["changed"] and out["fingerprint_after"] == "after" and out["scene_delta"]
    else:
        assert "fingerprint_after" not in out  # host receives non-ok + before and restores its checkpoint


@pytest.mark.parametrize("change", [False, True])
def test_read_check_exposes_fingerprint_integrity_and_keeps_false_quality_verdict(monkeypatch, change):
    scene = Scene()
    domain = {"ok": False, "reasons": ["capture below threshold"], "lenses": [{"pass": False}]}

    def invoke(op, kwargs):
        if change:
            scene.value = "after"
        return domain

    monkeypatch.setattr(C, "invoke", invoke)
    out = _worker_methods(scene)({"op": "check_lens_seating", "kwargs": {}})
    assert out["status"] == "ok" and out["result"] == domain
    assert out["unchanged"] is (not change) and not scene.protected


def test_drift_and_bad_arguments_never_invoke_operation(monkeypatch):
    def invoke(*args):
        pytest.fail("operation must not run")

    monkeypatch.setattr(C, "invoke", invoke)
    call = _worker_methods(Scene())
    assert call({"op": "seat_lens", "kwargs": {"lens": "Lens"}, "expect_fingerprint": "stale"})["status"] == "drift"
    assert call({"op": "unseat_lens", "kwargs": {"lens": 1}})["code"] == "E_ARGS"
