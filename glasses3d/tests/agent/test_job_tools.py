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

    def call_mutating(self, tool, method, args, **kw):
        self.calls.append((tool, method, args, kw))
        self.head = {"id": "c0002", "fingerprint": "a" * 64}
        return {"status": "ok", "checkpoint": "c0002", "result": {"status": "partial", "warnings": ["Still exposed"]}}

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
              "validate_spec": {"ok": True}, "seat_check": {"ok": True},
              "print_check": {"ok": True, "status": "not_required", "glb_sha256": AR.sha256(model)}}
    obj = SimpleNamespace(root=tmp_path, job={}, s=session, lock=threading.RLock(), finish=None, last_ar=None,
                          calls=[], images=[], unseen=[], export=export)
    obj.record_call = lambda *args: obj.calls.append(args)
    obj.record_images = lambda cid, result: obj.images.extend(result["images"])
    obj.mark_unseen = lambda cid: obj.unseen.append(cid)
    obj.seeing = SimpleNamespace(export_head=lambda **kw: copy.deepcopy(obj.export))
    return obj


@pytest.fixture
def finish_scene(scene, monkeypatch):
    """Finish-only fakes keep real AR wrapper tests independent of finalization setup."""
    scene.finish_steps = []
    scene.ar_result = None
    sheet = scene.root / "work/finish-ar.png"
    fallback = scene.root / "work/finish-diagnostic.png"
    Image.new("RGB", (32, 24), "blue").save(sheet)
    Image.new("RGB", (24, 24), "orange").save(fallback)

    def export(**kw):
        scene.finish_steps.append(("export", kw))
        return copy.deepcopy(scene.export)

    def capture(root, exp, cid, **kw):
        scene.finish_steps.append(("ar", copy.deepcopy(exp), cid, kw))
        if scene.ar_result is not None:
            return copy.deepcopy(scene.ar_result)
        return {"status": "ok", "exit_code": 0, "export_sha256": exp["sha256"],
                "checkpoint": exp["checkpoint"], "fixed_scale": True,
                "images": [{"path": str(sheet), "view": "AR default poses", "kind": "arview_capture",
                            "checkpoint": exp["checkpoint"]}]}

    def render(**kw):
        scene.finish_steps.append(("diagnostic", kw))
        return {"status": "ok", "sheets": [str(fallback)], "views": {"front": {"kind": "canonical"}}}

    scene.seeing.export_head = export
    scene.seeing.render_views = render
    monkeypatch.setattr(AR, "capture", capture)
    return scene


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
        if tool.name in {"temple_stage", "photo_print"}:
            assert tool.timeout_seconds is None  # long stages retain individual worker/request deadlines
        else:
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


def test_load_font_uses_journaled_allowlisted_worker_method(scene):
    result = record(invoke(scene, "load_font", {"family": "arial"}))
    assert result["status"] == "ok" and result["checkpoint"] == "c0002"
    tool, method, params, kwargs = scene.s.calls[0]
    assert (tool, method, params) == ("load_font", "load_font", {"family": "arial"})
    assert kwargs == {"deadline_s": 60, "label": "load font arial", "call_id": "call_test"}
    assert scene.calls[0][:4] == ("call_test", "load_font", "c0001", "c0002")
    registered = {tool.name: tool for tool in T.build_tools(scene)}
    assert registered["load_font"].params_json_schema["properties"]["family"]["enum"] == [
        "arial", "arialbd", "times", "timesbd", "calibri", "segoeui"]


def test_camera_overlay_is_readable_at_native_crop_coordinates(scene):
    path = scene.root / "cameras/basis-001/overlay_front.png"
    path.parent.mkdir(parents=True)
    image = Image.new("RGB", (1400, 1100), "red")
    image.paste("blue", (1200, 900, 1300, 1000))
    image.save(path)
    output = invoke(scene, "read_image", {"path": "cameras/basis-001/overlay_front.png",
                                         "region": [1200, 900, 1300, 1000]})
    body = record(output)
    assert body["status"] == "ok"
    meta = body["images"][0]
    assert meta["source_path"] == "cameras/basis-001/overlay_front.png"
    assert meta["size_px"] == [100, 100]
    assert Image.open(scene.root / meta["path"]).getpixel((0, 0)) == (0, 0, 255)


@pytest.mark.parametrize("path", ["cameras/basis-001/basis.json", "cameras/basis-001/photos/front.png",
                                  "cameras/basis-001/mattes/front.png", "cameras/basis-001/plain.png"])
def test_read_image_only_opens_camera_diagnostic_images(scene, path):
    assert record(invoke(scene, "read_image", {"path": path}))["status"] == "refused"


def test_tool_docs_explain_export_roles_and_hinge_recognition():
    docs = {t.name: t.description for t in T.build_tools(SimpleNamespace())}
    assert "only partRole values lens, frame and temple" in docs["execute_blender_code"]
    assert "strictly below 3.2 mm" in docs["temple_stage"] and "within" in docs["temple_stage"]
    assert "4 mm" in docs["temple_stage"] and "do not shrink real hardware" in docs["temple_stage"]


def test_scene_summary_is_read_only_worker_call(scene):
    assert record(invoke(scene, "scene_summary"))["result"]["objects"] == ["frame"]
    assert scene.s.calls == [("scene_summary", "scene_summary", {"objects": "all"},
                              {"deadline_s": 30, "call_id": "call_test"})]


def test_temple_stage_requires_the_active_shared_budget_context(scene):
    result = record(invoke(scene, "temple_stage", {"budget_usd": 2}))
    assert result["status"] == "refused" and result["code"] == "E_STAGE_CONTEXT"
    assert scene.s.calls == []


def test_best_checkpoint_is_explicit_and_saved_with_scene_journal(scene):
    from glasses3d.agent.job_scene import JobScene
    durable = JobScene(scene.root, job={})
    scene.s.index = durable.s.index
    durable.s = scene.s
    assert record(invoke(durable, "save_checkpoint", {"label": "named best"}))["status"] == "ok"
    assert durable.best_checkpoint is None  # a label does not secretly select a best checkpoint
    output = record(invoke(durable, "save_checkpoint", {"label": "preferred", "best": True}, cid="best"))
    assert output["best_checkpoint"] == durable.best_checkpoint == "c0001"
    assert JobScene(scene.root, job={}).best_checkpoint == "c0001"
    scene.s.head = {"id": "c0002", "fingerprint": "b" * 64}
    scene.s.save_checkpoint = lambda *a, **kw: {"status": "error"}
    assert record(invoke(durable, "save_checkpoint", {"label": "bad", "best": True}, cid="bad"))["status"] == "error"
    assert JobScene(scene.root, job={}).best_checkpoint == "c0001"


def test_every_sheet_has_an_adjacent_identity_label_and_real_view_names(scene):
    paths = []
    for n in range(2):
        path = scene.root / f"work/sheet{n}.png"
        Image.new("RGB", (8, 8), (n, 0, 0)).save(path)
        paths.append(str(path))
    views = {f"alias-{n}": {"kind": "reference", "ref": f"photo {n}"} for n in range(T.VIEWS_PER_SHEET + 1)}
    scene.seeing.render_views = lambda **kw: {"status": "ok", "sheets": paths, "views": views}
    output = invoke(scene, "render_views")
    rows = record(output)["images"]
    assert len(output) == 4 and [row["output_index"] for row in rows] == [2]
    assert isinstance(output[-1], ToolOutputText) and output[-1].text == loop.footer_text()
    assert rows[0]["panels"][0]["views"] == [f"reference:photo {n}" for n in range(T.VIEWS_PER_SHEET)]
    assert rows[0]["panels"][1]["views"] == [f"reference:photo {T.VIEWS_PER_SHEET}"]
    assert max(rows[0]["size_px"]) <= 1024
    for row in rows:
        i = row["output_index"]
        assert isinstance(output[i], ToolOutputImage) and isinstance(output[i - 1], ToolOutputText)
        assert f"call call_test; checkpoint c0001; revision c0001; view {row['view']}" in output[i - 1].text


def test_compact_image_repeat_references_prior_call_and_preserves_native_panel(scene):
    path = scene.root / "work/large.png"
    Image.new("RGB", (3000, 2000), "navy").save(path)
    original = path.read_bytes()
    scene.seeing.render_views = lambda **kw: {"status": "ok", "sheets": [str(path)]}
    first = invoke(scene, "render_views", cid="first")
    before = copy.deepcopy(first)
    second = invoke(scene, "render_views", cid="second")
    assert sum(isinstance(x, ToolOutputImage) for x in first) == 1
    assert not any(isinstance(x, ToolOutputImage) for x in second)
    assert record(second)["images"][0]["reused_from_call"] == "first"
    assert max(record(first)["images"][0]["size_px"]) == 1024
    assert record(first)["images"][0]["source_size_px"] == [3000, 2000]
    assert first == before and path.read_bytes() == original
    crop = invoke(scene, "read_image", {"path": "work/large.png", "region": [0, 0, 150, 100]}, cid="crop")
    assert sum(isinstance(x, ToolOutputImage) for x in crop) == 1
    assert record(crop)["images"][0]["size_px"] == [150, 100]


def test_unseen_compact_image_is_delivered_again(scene):
    path = scene.root / "work/image.png"
    Image.new("RGB", (10, 10), "red").save(path)
    first = invoke(scene, "read_image", {"path": "work/image.png"}, cid="unseen")
    scene.mark_unseen("unseen")
    again = invoke(scene, "read_image", {"path": "work/image.png"}, cid="retry")
    assert sum(isinstance(x, ToolOutputImage) for x in first) == 1
    assert sum(isinstance(x, ToolOutputImage) for x in again) == 1


def test_crop_of_composite_native_panel_retains_checkpoint_and_view(scene):
    paths = []
    for n in range(2):
        path = scene.root / f"work/panel{n}.png"
        Image.new("RGB", (100, 80), (n * 100, 0, 0)).save(path)
        paths.append(str(path))
    scene.seeing.compare_to_photos = lambda **kw: {"status": "ok", "refs": {
        "front": {"sheet": paths[0]}, "top": {"sheet": paths[1]}}}
    invoke(scene, "compare_to_photos", cid="composite")
    scene.s.head = {"id": "c0002", "fingerprint": "x" * 64}
    crop = record(invoke(scene, "read_image", {"path": "work/panel1.png", "region": [5, 5, 50, 50]}))["images"][0]
    assert crop["checkpoint"] == "c0001" and crop["view"] == "top"


def test_generated_image_reread_keeps_source_checkpoint_and_separate_detail_group(scene):
    path = scene.root / "work/compare.png"
    Image.new("RGB", (8, 8)).save(path)
    scene.images = [{"path": "work/compare.png", "source_path": "work/compare.png", "view": "front",
                     "checkpoint": "c0000", "kind": "compare_to_photos"}]
    output = invoke(scene, "read_image", {"path": "work/compare.png", "region": [0, 0, 4, 4]})
    row = record(output)["images"][0]
    assert row["checkpoint"] == "c0000" and row["context_role"] == "detail" and row["views"] == ["front"]
    assert "checkpoint c0000" in output[1].text


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
    {"validate_spec": {"ok": False}}, {"seat_check": {"ok": "false"}}, {"seat_check": None},
    {"seat_check": {}}, {"validation_sha256": "wrong"},
    {"print_check": None}, {"print_check": {"ok": False}}, {"print_check": {"ok": True, "glb_sha256": "wrong"}},
    {"checkpoint": "c0000"}, {"fingerprint": "stale"}, {"drift": "worker changed"},
])
def test_finish_refuses_invalid_or_stale_export(finish_scene, change):
    scene = finish_scene
    scene.export.update(change)
    output = invoke(scene, "finish", {"summary": "done"})
    result = record(output)
    assert result["status"] == "refused" and result["code"] == "E_EXPORT"
    assert result["export"] == scene.export and result["failures"]
    assert any(isinstance(part, ToolOutputImage) for part in output)
    assert scene.finish is None


def test_finish_checks_export_bytes_before_ar_and_returns_diagnostic(finish_scene):
    scene = finish_scene
    model = Path(scene.export["path"])
    model.write_bytes(b"changed")
    output = invoke(scene, "finish", {"summary": "done"})
    out = record(output)
    assert out["code"] == "E_EXPORT" and scene.finish is None
    assert {failure["check"] for failure in out["failures"]} == {"export", "arview"}
    assert [step[0] for step in scene.finish_steps] == ["export", "diagnostic"]
    assert out["image_source"] == "scene_render_fallback"
    assert sum(isinstance(part, ToolOutputImage) for part in output) == 1


@pytest.mark.parametrize("prior", [None, "stale", "current"])
def test_finish_exports_and_captures_fresh_without_requiring_prior_ar(finish_scene, prior):
    scene = finish_scene
    scene.last_ar = None if prior is None else {
        "export_sha256": scene.export["sha256"] if prior == "current" else "stale", "status": "ok", "exit_code": 0}
    output = invoke(scene, "finish", {"summary": "done"})
    out = record(output)
    assert out["status"] == "review_required" and scene.finish is None
    assert out["call_id"] == "call_test" and out["export"]["validate_spec"]["ok"]
    assert out["failures"] == [] and out["image_source"] == "arview"
    assert [step[0] for step in scene.finish_steps] == ["export", "ar"]
    assert scene.finish_steps[0][1] == {"call_id": "call_test"}
    assert scene.finish_steps[1][1] == out["export"]
    assert scene.finish_steps[1][2:] == ("call_test", {"timeout_s": loop.hard_timeout_s("arview_capture") - 10})
    assert scene.last_ar == out["arview"]
    assert out["arview"]["images"] == out["images"]
    assert sum(isinstance(part, ToolOutputImage) for part in output) == 1
    final = record(invoke(scene, "finish", {"summary": "done; photo evidence unavailable",
                   "review_call_id": "call_test", "per_photo": []}, cid="call_second"))
    assert final["status"] == "accepted" and scene.finish == final
    assert [step[0] for step in scene.finish_steps] == ["export", "ar", "export", "ar"]


@pytest.mark.parametrize("ar", [{}, {"status": "infrastructure_failed", "exit_code": 1},
                               {"status": "ok", "exit_code": 3}, {"status": "timeout", "exit_code": None},
                               {"status": "ok", "exit_code": 0}])
def test_finish_requires_successful_new_ar_with_images_and_retains_fallback(finish_scene, ar):
    scene = finish_scene
    scene.last_ar = {"export_sha256": scene.export["sha256"], "status": "ok", "exit_code": 0, "images": ["old"]}
    scene.ar_result = {**ar, "export_sha256": scene.export["sha256"]}
    output = invoke(scene, "finish", {"summary": "done"})
    out = record(output)
    assert out["code"] == "E_AR" and out["status"] == "refused"
    assert out["arview"] == scene.ar_result and out["failures"] == [
        {"check": "arview", "code": "E_AR", "report": scene.ar_result}]
    assert [step[0] for step in scene.finish_steps] == ["export", "ar", "diagnostic"]
    assert scene.finish_steps[-1][1] == {"views": ["front"], "passes": ["native", "clay"],
                                           "size_px": 768, "call_id": "call_test-diagnostic"}
    assert out["diagnostic_render"]["status"] == "ok" and out["image_source"] == "scene_render_fallback"
    assert sum(isinstance(part, ToolOutputImage) for part in output) == 1
    assert scene.finish is None


def test_finish_retains_all_quality_failures_and_ar_evidence_in_one_result(finish_scene):
    scene = finish_scene
    for key in ("validate_spec", "seat_check", "print_check"):
        scene.export[key] = {"ok": False, "reason": key + " failed", "details": {"preserve": [1, 2, 3]}}
    output = invoke(scene, "finish", {"summary": "known differences"})
    out = record(output)
    assert out["status"] == "refused" and out["code"] == "E_EXPORT"
    assert out["export"] == scene.export and out["summary"] == "known differences"
    assert out["failures"] == [
        {"check": "specification", "code": "E_SPEC", "report": scene.export["validate_spec"]},
        {"check": "print", "code": "E_PRINT", "report": scene.export["print_check"]}]
    assert [step[0] for step in scene.finish_steps] == ["export", "ar"]
    assert out["arview"]["export_sha256"] == scene.export["sha256"]
    assert out["image_source"] == "arview" and sum(isinstance(part, ToolOutputImage) for part in output) == 1
    assert scene.finish is None


def test_finish_rejects_fresh_ar_for_different_export_bytes_even_with_images(finish_scene):
    scene = finish_scene
    scene.ar_result = {"status": "ok", "exit_code": 0, "export_sha256": "different",
                       "images": [{"path": str(scene.root / "work/finish-ar.png"), "view": "AR default poses"}]}
    out = record(invoke(scene, "finish", {"summary": "done"}))
    assert out["status"] == "refused" and out["code"] == "E_AR"
    assert [failure["check"] for failure in out["failures"]] == ["arview"]
    assert scene.finish is None


@pytest.mark.parametrize("quality_fails", [False, True])
def test_finish_image_processing_error_cannot_persist_an_accepted_finish(finish_scene, monkeypatch, quality_fails):
    scene = finish_scene
    scene.finish = {"status": "accepted", "call_id": "prior"}
    if quality_fails:
        for key in ("validate_spec", "seat_check", "print_check"):
            scene.export[key] = {"ok": False, "reason": key + " failed"}
    def corrupt(*args, **kwargs):
        raise ValueError("cannot process capture image")
    monkeypatch.setattr(T, "_image", corrupt)
    out = record(invoke(scene, "finish", {"summary": "done"}))
    assert out["status"] == "refused" and out["code"] == "E_IMAGE"
    assert "cannot process capture image" in out["message"]
    assert out["export"] == scene.export and out["arview"]["export_sha256"] == scene.export["sha256"]
    assert out["images"] == [] and out["failures"][-1]["check"] == "image_output"
    assert out["failures"][-1]["code"] == "E_IMAGE"
    assert [item["check"] for item in out["failures"][:-1]] == (["specification", "print"] if quality_fails else [])
    if quality_fails:
        for failure, key in zip(out["failures"], ("validate_spec", "print_check")):
            assert failure["report"] == scene.export[key]
    assert scene.calls[-1][-1] == out
    assert scene.finish is None


@pytest.mark.parametrize("diagnostic_fails", [False, True])
def test_finish_capture_exception_preserves_all_quality_reports_and_fallback_result(
        finish_scene, monkeypatch, diagnostic_fails):
    scene = finish_scene
    for key in ("validate_spec", "seat_check", "print_check"):
        scene.export[key] = {"ok": False, "reason": key + " failed", "evidence": [1, 2, 3]}

    def capture_error(*args, **kwargs):
        raise OSError("capture process unavailable")

    def diagnostic_error(**kwargs):
        raise ValueError("diagnostic render unavailable")

    monkeypatch.setattr(AR, "capture", capture_error)
    if diagnostic_fails:
        scene.seeing.render_views = diagnostic_error
    output = invoke(scene, "finish", {"summary": "partial diagnostics"})
    out = record(output)
    assert out["status"] == "refused" and out["code"] == "E_EXPORT"
    assert out["export"] == scene.export and out["summary"] == "partial diagnostics"
    assert [failure["check"] for failure in out["failures"]] == ["specification", "print", "arview"]
    for failure, key in zip(out["failures"], ("validate_spec", "print_check")):
        assert failure["report"] == scene.export[key]
    assert out["arview"] == {"status": "infrastructure_failed", "code": "E_AR", "message": "capture process unavailable"}
    assert out["failures"][-1]["report"] == out["arview"]
    assert out["image_source"] == "scene_render_fallback"
    if diagnostic_fails:
        assert out["diagnostic_render"] == {"status": "error", "code": "E_DIAGNOSTIC", "message": "diagnostic render unavailable"}
        assert out["images"] == [] and not any(isinstance(part, ToolOutputImage) for part in output)
    else:
        assert out["diagnostic_render"]["status"] == "ok"
        assert sum(isinstance(part, ToolOutputImage) for part in output) == 1
    assert scene.calls[-1][-1] == out and scene.finish is None


@pytest.mark.parametrize("stage", ["capture", "diagnostic"])
def test_finish_worker_failure_still_propagates_from_finalization_stages(finish_scene, monkeypatch, stage):
    scene = finish_scene
    scene.finish = {"status": "accepted", "call_id": "prior"}

    def fatal(*args, **kwargs):
        raise WorkerFailed("finalization worker lost", scene.s.head)

    if stage == "capture":
        monkeypatch.setattr(AR, "capture", fatal)
    else:
        scene.ar_result = {"status": "infrastructure_failed", "message": "capture unavailable"}
        scene.seeing.render_views = fatal
    with pytest.raises(WorkerFailed, match="finalization worker lost"):
        invoke(scene, "finish", {"summary": "done"})
    assert scene.finish is None


def test_budget_status_reads_guard_without_money_side_effects(scene):
    ctx = SimpleNamespace(guard=SimpleNamespace(summary=lambda: {"settled_usd": "1.000000", "maximum_usd": "23.000000"}))
    assert record(invoke(scene, "budget_status", context=ctx))["budget"]["settled_usd"] == "1.000000"


@pytest.mark.parametrize("name,args", [
    ("seat_lens", {"lens": "Lens left", "frame_type": "acetate"}),
    ("hide_eyewire", {"eyewire": "Wire", "under": ["Brow"], "hidden_x_range_mm": [[-55, -12]]}),
    ("unseat_lens", {"lens": "Lens left"}),
    ("unhide_eyewire", {"eyewire": "Wire", "lens": "Lens left"}),
])
def test_seating_mutations_use_trusted_journaled_path_and_keep_partial_reports(scene, name, args):
    out = record(invoke(scene, name, args, cid="seat-call"))
    assert out["status"] == "ok" and out["result"]["status"] == "partial"
    assert out["result"]["warnings"] == ["Still exposed"]
    tool, method, params, options = scene.s.calls[0]
    assert tool == name and method == "g3d_call" and params["op"] == name
    assert all(params["kwargs"][key] == value for key, value in args.items())
    assert options == {"deadline_s": 150, "label": name, "call_id": "seat-call"}
    assert scene.calls[0][:4] == ("seat-call", name, "c0001", "c0002")
    if name == "seat_lens":
        assert "translucent" not in params["kwargs"]  # crystal recognition follows actual supporting materials


def test_seating_check_is_read_only_and_preserves_a_failed_quality_report(scene):
    def check(tool, method, args, **kwargs):
        assert tool == "check_lens_seating" and method == "g3d_call"
        assert args["op"] == tool and args["kwargs"]["frame_type"] is None
        assert kwargs == {"deadline_s": 120, "call_id": "call_test"}
        return {"status": "ok", "restored_to": None, "result": {
            "status": "ok", "unchanged": True,
            "result": {"ok": False, "reasons": ["undeclared construction"]}}}
    scene.s.call_read = check
    out = record(invoke(scene, "check_lens_seating"))
    assert out["status"] == "ok" and out["result"]["ok"] is False
    assert scene.calls[0][2:4] == ("c0001", "c0001")


@pytest.mark.parametrize("worker,restored,expected", [
    ({"status": "refused", "code": "E_ARGS", "message": "bad construction"}, None, "refused"),
    ({"status": "ok", "result": {"ok": True}, "unchanged": False}, "c0001", "error"),
])
def test_seating_check_preserves_worker_refusal_and_read_rollback(scene, worker, restored, expected):
    scene.s.call_read = lambda *args, **kwargs: {"status": "ok", "result": worker, "restored_to": restored}
    out = record(invoke(scene, "check_lens_seating"))
    assert out["status"] == expected
    assert out["restored_to"] == restored
    assert out["code"] == ("E_READ_CHANGED" if restored else "E_ARGS")


def test_image_output_has_oriented_crop_and_delivered_digest(scene):
    src = scene.root / "images/photo.jpg"
    src.parent.mkdir()
    image = Image.new("RGB", (20, 10), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(src, exif=exif)
    output = invoke(scene, "read_image", {"path": "images/photo.jpg", "region": [0, 0, 10, 15]})
    rec = record(output)["images"][0]
    assert len(output) == 4 and isinstance(output[1], ToolOutputText) and isinstance(output[2], ToolOutputImage)
    assert isinstance(output[-1], ToolOutputText) and output[-1].text == loop.footer_text()
    assert "call call_test; checkpoint source; revision source:" in output[1].text
    assert "view images/photo.jpg" in output[1].text and rec["output_index"] == 2
    assert rec["context_role"] == "reference"
    encoded = output[2].image_url.split(",", 1)[1]
    import base64
    payload = base64.b64decode(encoded)
    assert Image.open(io.BytesIO(payload)).size == (10, 15)
    assert rec["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (scene.root / rec["path"]).read_bytes() == payload
    assert rec["source_size_px"] == [10, 20]
    assert scene.images[0]["sha256"] == rec["sha256"]
    again = record(invoke(scene, "read_image", {"path": rec["path"]}, cid="read_crop_again"))["images"][0]
    assert again["context_role"] == "reference" and again["revision"] == rec["revision"]
    assert again["checkpoint"] == "source"


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
        {"original_name": "side.jpg", "file": "images/side.jpg", "view_kind": "side", "fit_kinds": ["left"],
         "matte_mode": "glossy_floor"}]})
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
    assert received[0][1]["matte_modes"] == {"front.jpg": None, "side.jpg": "glossy_floor"}
    assert received[0][1]["off_axis"] is False
    assert len(output) == 4 and record(output)["images"][0]["source_path"].startswith("cameras/")
    assert isinstance(output[-1], ToolOutputText) and output[-1].text == loop.footer_text()
    invoke(scene, "fit_cameras", {"reason": "one photo", "refs": ["side"], "workers": 1, "off_axis": True},
           cid="call_subset")
    assert received[1][1]["candidates"] == {"side.jpg": ("left",)}
    assert received[1][1]["off_axis"] is True


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


def test_extended_camera_diagnostics_remain_available_through_report_pages(scene, monkeypatch):
    from glasses3d.agent import jobs, tool_reports
    from glasses3d.agent.seeing import Seeing
    from glasses3d.g3d_host.camera import Camera
    camera = Camera(75, 12, 3, .3, 580, 400, 300, .22, -.12).to_dict()
    evidence = {"mode": "legacy_search_then_off_axis_continuation", "selected": "off_axis",
                "candidate_camera": camera, "legacy_camera": {**camera, "eye_x": 0, "eye_y": 0},
                "common_losses": [.02, .01], "eye_limit": .6, "candidate_refusal": None}
    sign = {"own_camera_full": camera, "twin_camera_full": {**camera, "pitch": -12},
            "own_eye_relative_pivot_norm": [3.1, .5, 1.2], "flag": "pitch_sign_ambiguous"}
    ref = {"camera": camera, "view": "left", "view_kind": "side",
           "fit": {"status": "ok", "iou": .98, "off_axis": evidence, "sign": sign}}
    monkeypatch.setattr(jobs, "load_job", lambda root: {"images": [
        {"original_name": "side.jpg", "file": "images/side.jpg", "view_kind": "side", "fit_kinds": ["left"]}]})
    scene.seeing.fit_cameras = lambda *args, **kwargs: {
        "status": "ok", "off_axis": True,
        "refs": {f"photo-{i}": Seeing._fit_ref_summary(ref, usable=True) for i in range(40)}}
    output = invoke(scene, "fit_cameras", {"reason": "inspect extended calibration", "off_axis": True, "workers": 1})
    assert sum(len(part.text) for part in output if part.type == "text") <= tool_reports.TEXT_LIMIT
    report = record(output)["full_report"]["id"]
    for field, expected in (("camera", camera), ("off_axis", evidence), ("sign", sign)):
        page = record(invoke(scene, "read_report", {"report": report,
                             "pointer": f"/result/refs/photo-0/{field}", "limit": 100}, cid=f"read-{field}"))
        assert {entry["key"]: entry["value"] for entry in page["entries"]} == expected


def test_worker_capacity_is_bounded_by_free_commit_and_cpu(monkeypatch):
    monkeypatch.setattr(T.os, "cpu_count", lambda: 32)
    monkeypatch.setattr(T, "free_commit_bytes", lambda: 40 * 2**30)
    assert T.fit_workers() == 14 and T.fit_workers(3) == 3
    monkeypatch.setattr(T, "free_commit_bytes", lambda: 6 * 2**30)
    assert T.fit_workers(16) == 3
    monkeypatch.setattr(T, "free_commit_bytes", lambda: 1 * 2**30)
    assert T.fit_workers() == 1


@pytest.mark.parametrize("code,status", list(AR.EXIT_STATUS.items()))
def test_arview_fixed_scale_exit_mapping_and_image_integrity(scene, monkeypatch, code, status):
    monkeypatch.setattr(AR.shutil, "which", lambda name: "node.exe")
    def run_capture(argv, log_dir, **kw):
        assert "--fixed-scale" in argv and "--no-portrait" not in argv and "--worn-sides" in argv
        assert "--lens-evidence" in argv
        assert argv[argv.index("--lensenv") + 1] == str(AR.LENS_ENV_INTENSITY) == "1"
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
    assert result["appearance_policy"] == "g3d.ar_appearance/1" and result["lens_env_intensity"] == 1
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


@pytest.mark.timing
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


@pytest.mark.parametrize("code,errors,spec,portrait,expected", [
    (0, ["page reloaded"], {"available": True, "ok": True}, False, "infrastructure_failed"),
    (3, [], {"available": False, "reason": "validator missing"}, False, "validator_unavailable"),
    (0, [], {"available": True, "ok": True}, True, "ok"),
])
def test_capture_reports_actual_errors_validator_and_portrait(scene, monkeypatch, code, errors, spec, portrait, expected):
    monkeypatch.setattr(AR.shutil, "which", lambda name: "node.exe")
    def run(argv, *_args, **_kwargs):
        out = Path(argv[argv.index("--out") + 1])
        image = out / "capture.png"
        Image.new("RGB", (20, 10), "blue").save(image)
        report = {"schema": "arview.capture/1", "input": {"sha256": scene.export["sha256"]},
                  "status": "captured_with_warnings", "errors": errors, "spec": spec,
                  "images": [{"file": image.name, "sha256": AR.sha256(image), "pose": "front",
                              "kind": "portrait" if portrait else "pose", "variant": "condition-a" if portrait else "beauty"}]}
        (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return code, False
    monkeypatch.setattr(AR, "run_capture", run)
    result = AR.capture(scene.root, scene.export, "actual")
    assert result["status"] == expected and result["errors"] == errors, result.get("message")
    assert result["portrait"] is portrait and result["portrait_requested"] is True


def test_capture_sets_private_temp_for_node_and_children(tmp_path):
    probe = tmp_path / "temp_probe.py"
    output = tmp_path / "environment.json"
    probe.write_text("import json,os,subprocess,sys\n"
                     "child=subprocess.check_output([sys.executable,'-c','import os; print(os.environ[\"TEMP\"])'],text=True).strip()\n"
                     "open(sys.argv[1],'w').write(json.dumps([os.environ['TEMP'],os.environ['TMP'],child]))\n")
    code, timed_out = AR.run_capture([sys.executable, str(probe), str(output)], tmp_path, timeout_s=10)
    assert code == 0 and not timed_out
    assert json.loads(output.read_text()) == [str((tmp_path / "temp").resolve())] * 3
