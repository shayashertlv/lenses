"""Exact pre14 text control and isolated capture binding; no native processes."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import threading
import types
from types import SimpleNamespace

from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from PIL import Image
import pytest

from glasses3d.agent import tool_reports
from glasses3d.agent.tools import arview, look
from glasses3d.lens_lab import pre14, trial, variants


def _scene(root):
    session = SimpleNamespace(head={"id": "c0001", "fingerprint": "f" * 64},
                              call_read=lambda *a, **kw: {"status": "ok", "result": {"objects": []}})
    scene = SimpleNamespace(root=root, s=session, job={}, lock=threading.RLock(), calls=[], images=[], unseen=[])
    scene.seeing = SimpleNamespace(export_head=lambda **kw: {"status": "exported", "checkpoint": "c0001"})
    scene.record_call = lambda *args: scene.calls.append(args)
    scene.record_images = lambda cid, result: scene.images.extend(result["images"])
    scene.mark_unseen = scene.unseen.append
    return scene


def _invoke(registry, name="compare_look_to_photos", arguments=None):
    tool = next(t for t in registry if t.name == name)
    raw = json.dumps(arguments or {})
    context = ToolContext(context=None, tool_name=name, tool_call_id="same-call", tool_arguments=raw)
    return asyncio.run(tool.on_invoke_tool(context, raw))


def _wire(output):
    return json.dumps([p.model_dump() if hasattr(p, "model_dump") else p for p in output],
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _frozen_variants():
    source = subprocess.run(["git", "show", "HEAD:glasses3d/lens_lab/variants.py"], cwd=trial.REPO,
                            check=True, capture_output=True).stdout
    module = types.ModuleType("glasses3d.lens_lab._before_pre14_variants")
    module.__package__ = "glasses3d.lens_lab"
    module.__file__ = variants.__file__
    exec(compile(source, "HEAD:glasses3d/lens_lab/variants.py", "exec"), module.__dict__)
    return module


def test_historical_instructions_are_exactly_one_sentence_less_and_other_prompt_bytes_equal():
    job = {"name": "test", "description": "exact owner words", "specs_json": "{}", "images": []}
    baseline = trial.head_prompt_sources(trial.REPO, job)
    before = copy.deepcopy(baseline)
    control = pre14.prompt_sources(trial.REPO, job, baseline)
    assert baseline == before
    assert control["instructions"] == baseline["instructions"].replace(pre14.METALLIC_SENTENCE, "", 1)
    assert control["instructions_source_commit"].startswith("61d5726")
    assert control["main_tool_names"] == baseline["main_tool_names"]
    assert control["brief"] == baseline["brief"]
    paragraph = "Same lab mechanics."
    assert trial.build_prompt(control, paragraph) == trial.build_prompt(baseline, paragraph).replace(
        pre14.METALLIC_SENTENCE, "", 1)
    with pytest.raises(ValueError, match="only by task 14"):
        pre14.prompt_sources(trial.REPO, job, {**baseline, "instructions": baseline["instructions"] + "drift"})


@pytest.mark.parametrize("before,after", [
    ('{"lens_colour_gap":["line"],"a":1}', '{"a":1}'),
    ('{"a":1,"lens_colour_gap":["line"],"b":2}', '{"a":1,"b":2}'),
    ('{"a":1,"lens_colour_gap":["line"]}', '{"a":1}'),
    ('{ "lens_colour_gap" : ["line"] }', '{  }'),
    ('{\n  "a": 1,\n  "lens_colour_gap": ["line"],\n  "b": 2\n}', '{\n  "a": 1,\n  "b": 2\n}'),
    ('{"nested":{"lens_colour_gap":["line"],"n":2},"n":1}\nfooter',
     '{"nested":{"n":2},"n":1}\nfooter'),
    ('{"text":"ordinary lens_colour_gap words"}', '{"text":"ordinary lens_colour_gap words"}'),
    ('not json lens_colour_gap', 'not json lens_colour_gap'),
])
def test_gap_removal_preserves_every_unrelated_byte(before, after):
    assert pre14.strip_gap_text(before) == after
    assert pre14.strip_gap_text(after) == after


def test_nested_serialized_report_text_keeps_original_unicode_escape_spelling():
    inner = '{"a":"α😀","lens_colour_gap":["gap"],"b":"\\t"}\nfooter'
    before = '{"text":' + json.dumps(inner, ensure_ascii=True) + ', "other":1}'
    expected = '{"text":' + json.dumps(inner.replace(',"lens_colour_gap":["gap"]', ''), ensure_ascii=True) + ', "other":1}'
    assert pre14.strip_gap_text(before) == expected
    assert json.loads(before)["text"] == inner


@pytest.mark.parametrize("variant", ["baseline", "regional"])
def test_baseline_and_regional_bytes_match_frozen_pre14_implementation(tmp_path, monkeypatch, variant):
    frozen = _frozen_variants()
    result = {"schema": look.SCHEMA, "status": "ok", "lens_colour_gap": ["whole lens line"],
              "views": [{"ref": "front", "colours": {"lens": {"photo": 1, "drawing": 2}}}], "images": []}
    monkeypatch.setattr(look, "capture", lambda *a, **kw: copy.deepcopy(result))
    module = variants.regional_module()
    module.capture = lambda *a, **kw: {**copy.deepcopy(result), "lens_colour_gap": ["whole lens line\nregions line"]}
    monkeypatch.setattr(variants, "regional_module", lambda: module)
    monkeypatch.setattr(frozen, "regional_module", lambda: module)
    old = frozen.build_variant_tools(_scene(tmp_path), variant)
    current = variants.build_variant_tools(_scene(tmp_path), variant)
    assert _wire(_invoke(old)) == _wire(_invoke(current))
    assert _wire(_invoke(old, "scene_summary")) == _wire(_invoke(current, "scene_summary"))
    schema = lambda tools: [(t.name, t.description, t.params_json_schema, t.timeout_seconds) for t in tools]
    assert schema(old) == schema(current)


@pytest.mark.parametrize("paged", [False, True])
def test_pre14_native_tool_binding_preserves_every_non_gap_byte_images_and_pager(tmp_path, monkeypatch, paged):
    image = tmp_path / "work/fixture.png"
    image.parent.mkdir()
    Image.new("RGB", (18, 14), (40, 80, 120)).save(image)
    gap = ["front lens: AR L* 55.0 vs photo 65.0 (10.0 darker); median RGB (sRGB 8-bit) AR 1,2,3 vs photo 4,5,6"]
    result = {"schema": look.SCHEMA, "status": "ok", "lens_colour_gap": gap,
              "views": [{"ref": "front", "unrelated": ["x" * 1000] * (50 if paged else 1)}],
              "images": [{"path": str(image), "view": "fixture lens"}]}
    monkeypatch.setattr(look, "capture", lambda *a, **kw: copy.deepcopy(result))
    baseline = _invoke(variants.build_variant_tools(_scene(tmp_path)))
    control = _invoke(variants.build_variant_tools(_scene(tmp_path), "pre14"))
    assert any(isinstance(p, ToolOutputImage) for p in baseline)
    assert len(control) == len(baseline)
    expected_member = '"lens_colour_gap":' + tool_reports.dumps(gap) + ','
    assert expected_member in baseline[0].text
    assert control[0].text == baseline[0].text.replace(expected_member, '', 1)
    assert _wire(control[1:]) == _wire(baseline[1:])
    assert json.loads(baseline[0].text).get("paged", False) is paged
    if paged:
        assert json.loads(control[0].text)["full_report"] == json.loads(baseline[0].text)["full_report"]
    assert look.capture is not None  # The global function remains the installed baseline stub.


def _saved_report(root):
    result = {"a": "same", "lens_colour_gap": ["UNIQUE_GAP_ONLY"], "b": {"measurement": 7}}
    text = tool_reports.dumps(result)
    record = {"schema": tool_reports.REPORT_SCHEMA, "tool": "compare_look_to_photos", "call_id": "same-call",
              "result": result, "text_parts": [{"output_index": 0, "text": text}]}
    receipt = tool_reports.save_report(root, record)
    return receipt, record, text


def test_read_report_json_hides_gap_entries_without_changing_ids_counts_offsets_or_other_entries(tmp_path):
    receipt, record, _ = _saved_report(tmp_path)
    original_bytes = tool_reports._path(tmp_path, receipt["id"]).read_bytes()
    baseline = tool_reports.read_report(tmp_path, receipt["id"])
    control = pre14.read_report(tmp_path, receipt["id"])
    assert control == {**baseline, "entries": [baseline["entries"][0], baseline["entries"][2]]}
    assert "UNIQUE_GAP_ONLY" not in tool_reports.dumps(control)
    entire = pre14.read_report(tmp_path, receipt["id"], pointer="")
    assert "UNIQUE_GAP_ONLY" not in tool_reports.dumps(entire)
    assert tool_reports._path(tmp_path, receipt["id"]).read_bytes() == original_bytes
    for pointer in ("/result/lens_colour_gap", "/result/lens_colour_gap/0"):
        with pytest.raises(ValueError, match="JSON pointer is absent"):
            pre14.read_report(tmp_path, receipt["id"], pointer=pointer)


@pytest.mark.parametrize("pointer", ["/result", "/text_parts/0/text", ""])
def test_read_report_text_pages_remove_only_gap_byte_intersections(tmp_path, pointer):
    receipt, record, text = _saved_report(tmp_path)
    value = tool_reports.select(record, pointer)
    source = value if isinstance(value, str) else tool_reports.dumps(value)
    pieces = []
    for offset in range(0, len(source), 7):
        baseline = tool_reports.read_report(tmp_path, receipt["id"], pointer=pointer, offset=offset, limit=7, mode="text")
        control = pre14.read_report(tmp_path, receipt["id"], pointer=pointer, offset=offset, limit=7, mode="text")
        assert {k: v for k, v in control.items() if k != "text"} == {k: v for k, v in baseline.items() if k != "text"}
        pieces.append(control["text"])
    expected = source.replace(',"lens_colour_gap":["UNIQUE_GAP_ONLY"]', '')
    expected = expected.replace(',\\"lens_colour_gap\\":[\\"UNIQUE_GAP_ONLY\\"]', '')
    assert ''.join(pieces) == expected


def test_read_report_summary_of_original_text_cannot_leak_truncated_gap(tmp_path):
    result = {"lens_colour_gap": ["UNIQUE_GAP_ONLY" * 100], "other": "same" * 3000}
    record = {"schema": tool_reports.REPORT_SCHEMA, "tool": "compare_look_to_photos", "call_id": "same-call",
              "result": result, "text_parts": [{"output_index": 0, "text": tool_reports.dumps(result)}]}
    receipt = tool_reports.save_report(tmp_path, record)
    result = pre14.read_report(tmp_path, receipt["id"], pointer="/text_parts/0")
    assert "UNIQUE_GAP_ONLY" not in tool_reports.dumps(result)


def test_pre14_read_report_registry_binding_does_not_modify_baseline_reader(tmp_path):
    receipt, _, _ = _saved_report(tmp_path)
    original = tool_reports.read_report
    args = {"report": receipt["id"]}
    baseline = _invoke(variants.build_variant_tools(_scene(tmp_path)), "read_report", args)
    control = _invoke(variants.build_variant_tools(_scene(tmp_path), "pre14"), "read_report", args)
    assert "UNIQUE_GAP_ONLY" in baseline[0].text
    assert "UNIQUE_GAP_ONLY" not in control[0].text
    assert tool_reports.read_report is original
    assert _wire(baseline[1:]) == _wire(control[1:])


@pytest.mark.parametrize("variant", ["baseline", "regional", "pre14"])
def test_capture_runner_binding_is_private_and_reaches_photo_worn_and_standalone(tmp_path, monkeypatch, variant):
    calls = []
    def runner(*args, **kwargs):
        calls.append(args)
        return 0, False
    namespace = {}
    exec('def capture(*a, **kw):\n    run_capture("worn")\n    return {"status":"ok", "images":[]}\n', namespace)
    monkeypatch.setattr(arview, "capture", namespace["capture"])
    look_namespace = {}
    exec('def capture(*a, **kw):\n    arview.run_capture("photo")\n    arview.capture()\n'
         '    return {"status":"ok", "images":[], "lens_colour_gap":["gap"]}\n', look_namespace)
    monkeypatch.setattr(look, "capture", look_namespace["capture"])
    regional = variants.regional_module()
    regional.capture = look_namespace["capture"]
    monkeypatch.setattr(variants, "regional_module", lambda: regional)
    original_run, original_capture, original_look = arview.run_capture, arview.capture, look.capture
    registry = variants.build_variant_tools(_scene(tmp_path), variant, capture_runner=runner)
    result = _invoke(registry)
    assert json.loads(result[0].text)["status"] == "ok"
    result = _invoke(registry, "arview_capture")
    assert json.loads(result[0].text)["status"] == "ok"
    assert calls == [("photo",), ("worn",), ("worn",)]
    assert (arview.run_capture, arview.capture, look.capture) == (original_run, original_capture, original_look)
