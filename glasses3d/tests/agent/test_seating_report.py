"""Reports expose lost features without changing the seating/mutation verdict."""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from agents.tool import ToolOutputImage

from glasses3d.agent.journal import WorkerFailed
from glasses3d.agent.tools import seating_report as R
from glasses3d.tests.agent.test_job_tools import scene, invoke, record


@pytest.fixture
def reporting_scene(scene, monkeypatch):
    calls = []
    scene.report_calls = calls
    scene.seeing.resolve_views = lambda *args, **kwargs: [{"blender": {"position": [0, 0, 200],
        "target": [0, 0, 0], "shape": [80, 120], "scale": 160}}]
    scene.seeing._light_frame = lambda cid: {"center": [0, 0, 0], "radius": 80}
    monkeypatch.setattr(R.C, "load_basis", lambda root: (_ for _ in ()).throw(R.C.NoBasis("no basis")))

    def render(tool, method, args, **kwargs):
        calls.append(copy.deepcopy(args))
        path = Path(args["out_dir"]) / "front_native.png"
        img = Image.new("RGBA", (120, 80), "black")
        # A scripted support edit removes a visible opening. This must be reported, not gated.
        if scene.s.head["id"] == "c0001":
            ImageDraw.Draw(img).rectangle((40, 25, 80, 35), fill=(0, 0, 0, 0))
        img.save(path)
        return {"status": "ok", "result": {"views": [{"files": {"native": {"path": str(path)}}}]}}
    scene.s.call_read = render
    return scene


@pytest.mark.parametrize("tool,args", [
    ("seat_lens", {"lens": "Lens", "frame_type": "semi_rimless"}),
    ("hide_eyewire", {"eyewire": "Wire", "under": ["Brow"], "hidden_x_range_mm": [[-10, 10]]}),
    ("unseat_lens", {"lens": "Lens"}), ("unhide_eyewire", {"eyewire": "Wire"}),
    ("execute_blender_code", {"code": "support edit", "seating_repair": True}),
])
def test_repairs_report_lost_opening_at_identical_camera_without_a_gate(reporting_scene, tool, args):
    scene = reporting_scene
    output = invoke(scene, tool, args)
    result = record(output)
    assert result["status"] == "ok"
    report = result["seating_repair_report"]
    assert report["status"] == "reported" and report["gate"] is False
    assert "every opening and feature" in report["guidance"]
    assert report["before"]["checkpoint"] == "c0001" and report["after"]["checkpoint"] == "c0002"
    a, b = scene.report_calls
    assert {k: v for k, v in a.items() if k != "out_dir"} == {k: v for k, v in b.items() if k != "out_dir"}
    assert report["before"]["source_render_sha256"] != report["after"]["source_render_sha256"]
    assert len(result["images"][0]["panels"]) == 2
    assert sum(isinstance(x, ToolOutputImage) for x in output) == 1
    with Image.open(report["before"]["path"]) as before, Image.open(report["after"]["path"]) as after:
        assert before.getpixel((60, 30)) == (255, 255, 255) and after.getpixel((60, 30)) == (0, 0, 0)


def test_photo_basis_preferred_and_resolved_once(reporting_scene, monkeypatch):
    scene = reporting_scene
    scene.job = {"images": [{"original_name": "01.png", "view_kind": "front"}]}
    from types import SimpleNamespace
    monkeypatch.setattr(R.C, "load_basis", lambda root: SimpleNamespace(id="basis-01", refs={"01": {}},
                                                                     is_usable=lambda rid: True))
    resolved = []
    old = scene.seeing.resolve_views
    scene.seeing.resolve_views = lambda *a, **kw: (resolved.append((a, kw)) or old(*a, **kw))
    result = record(invoke(scene, "seat_lens", {"lens": "Lens", "frame_type": "acetate"}))
    assert result["seating_repair_report"]["camera_source"] == "frozen_photo"
    assert len(resolved) == 1 and resolved[0][0][0] == ["reference:01"]


def test_report_failure_retains_successful_mutation_and_required_seating_failure(reporting_scene):
    scene = reporting_scene
    scene.s.call_read = lambda *a, **kw: {"status": "error", "message": "render unavailable"}
    result = record(invoke(scene, "seat_lens", {"lens": "Lens", "frame_type": "acetate"}))
    assert result["status"] == "ok" and result["checkpoint"] == "c0002"
    assert result["result"]["status"] == "partial"
    assert result["seating_repair_report"]["status"] == "incomplete"
    assert len(result["seating_repair_report"]["warnings"]) == 3


def test_report_integrity_failures_still_abort(reporting_scene):
    def broken(*a, **kw):
        raise WorkerFailed("worker failed", reporting_scene.s.head)
    reporting_scene.s.call_read = broken
    with pytest.raises(WorkerFailed):
        invoke(reporting_scene, "seat_lens", {"lens": "Lens", "frame_type": "acetate"})
    assert reporting_scene.s.head["id"] == "c0001"


def test_dry_run_does_not_render(reporting_scene):
    result = record(invoke(reporting_scene, "seat_lens", {"lens": "Lens", "frame_type": "acetate", "dry_run": True}))
    assert not reporting_scene.report_calls and "every opening and feature" in result["seating_guidance"]


def test_image_output_failure_preserves_repair(reporting_scene, monkeypatch):
    from glasses3d.agent import tools
    def broken(*a, **kw):
        raise OSError("cannot compose diagnostic image")
    monkeypatch.setattr(tools, "_image", broken)
    result = record(invoke(reporting_scene, "seat_lens", {"lens": "Lens", "frame_type": "acetate"}))
    assert result["status"] == "ok" and result["checkpoint"] == "c0002"
    assert result["result"]["status"] == "partial"
    assert result["images"] == []
    assert result["seating_repair_report"]["status"] == "incomplete"
    assert "cannot compose" in result["seating_repair_report"]["image_output_error"]


def test_after_only_failure_still_saves_diagnostic(reporting_scene):
    scene = reporting_scene
    render = scene.s.call_read
    def after_fails(*args, **kwargs):
        if scene.s.head["id"] == "c0002":
            raise OSError("after render unavailable")
        return render(*args, **kwargs)
    scene.s.call_read = after_fails
    result = record(invoke(scene, "seat_lens", {"lens": "Lens", "frame_type": "acetate"}))
    assert result["status"] == "ok" and result["checkpoint"] == "c0002"
    saved = json.loads(next((scene.root / "work/seating_reports").glob("*/report.json")).read_text())
    assert saved["status"] == "incomplete" and "before" in saved and "after" not in saved
    assert "after render unavailable" in saved["warnings"][-1]
