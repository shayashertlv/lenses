"""Lossless bounded diagnostics through real main/facet tool registries; no provider or worker."""
import asyncio
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import threading

from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
import pytest

from glasses3d.agent import tool_reports as R
from glasses3d.agent import tools as T
from glasses3d.agent.temple_lab import tools as F
from glasses3d.tests.agent.test_temple_lab_v3 import FakeLab


def long_result():
    stations = [{"verdict": "PASS", "required": True, "value": .4, "node": i,
                 "detail": "finite geometry measurement" * 8} for i in range(848)]
    stations[700].update(verdict="FAIL", value=-.9)
    stations[501].update(verdict="FAIL", value=-.5)
    return {"status": "ok", "result": {"ok": False, "lenses": [{"pass": False,
            "reasons": ["2 required stations FAIL"], "worst_deficit": {"mm": 1.2},
            "stations": stations}]}, "huge_string": "a\n\"雪\\" * 10000}


def text_count(output):
    return sum(len(part.text) for part in output if part.type == "text")


def emitted(root, result, *, facet=False):
    first = R.dumps(result) + ("\nsettled $0 / cap $23" if facet else "")
    image = ToolOutputImage(image_url="data:image/png;base64,eA==", detail="high")
    original = [ToolOutputText(text=first), ToolOutputText(text="Image label " * 3000), image,
                ToolOutputText(text="spent $0 (settled); held $0")]
    return original, R.limit_output(root, "call-a", "fixture", original)


@pytest.mark.parametrize("facet", [False, True])
def test_aggregate_cap_retains_images_failures_worst_rows_and_exact_originals(tmp_path, facet):
    result = long_result()
    before = deepcopy(result)
    original, output = emitted(tmp_path, result, facet=facet)
    assert text_count(output) <= R.TEXT_LIMIT and output[2] is original[2] and result == before
    body, _ = R._body(output[0].text)
    assert body["status"] == "ok" and body["paged"]
    table = body["table_summaries"][0]
    assert table["count"] == 848 and table["status_counts"] == {"PASS": 846, "FAIL": 2}
    assert [row["index"] for row in table["sample"][:2]] == [700, 501]
    path = tmp_path / body["full_report"]["path"]
    saved = json.loads(path.read_text(encoding="utf8"))
    assert saved["result"] == result
    assert saved["text_parts"] == [{"output_index": i, "text": item.text} for i, item in enumerate(original) if item.type == "text"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == body["full_report"]["id"]


def test_structured_filter_pages_cover_every_station_and_text_pages_are_lossless(tmp_path):
    _, output = emitted(tmp_path, long_result())
    rid = R._body(output[0].text)[0]["full_report"]["id"]
    pointer = "/result/result/lenses/0/stations"
    rows, offset = [], 0
    while offset is not None:
        page = R.read_report(tmp_path, rid, pointer, offset, 100)
        assert len(R.dumps(page)) <= R.PAGE_CHARS
        rows.extend(entry["value"] for entry in page["entries"])
        assert page["next_offset"] is None or page["next_offset"] > offset
        offset = page["next_offset"]
    assert rows == long_result()["result"]["lenses"][0]["stations"]
    page = R.read_report(tmp_path, rid, pointer, filter_path="/verdict", filter_value="FAIL")
    assert page["matched"] == 2 and [entry["key"] for entry in page["entries"]] == [501, 700]
    pieces, offset = [], 0
    while offset is not None:
        page = R.read_report(tmp_path, rid, "/result/huge_string", offset, 8000, "text")
        assert len(R.dumps(page)) <= R.PAGE_CHARS
        pieces.append(page["text"])
        assert page["next_offset"] is None or page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(pieces) == long_result()["huge_string"]


def test_oversized_single_rows_remain_reachable_and_pointer_escaping(tmp_path):
    result = {"status": "ok", "a/~": [{"giant": "Z" * 20000}, {"small": 1}]}
    _, output = emitted(tmp_path, result)
    rid = R._body(output[0].text)[0]["full_report"]["id"]
    page = R.read_report(tmp_path, rid, "/result/a~1~0", limit=1)
    assert page["next_offset"] == 1 and page["entries"][0]["pointer"] == "/result/a~1~0/0"
    assert "summary" in page["entries"][0]
    page = R.read_report(tmp_path, rid, "/result/a~1~0/0/giant", mode="text", limit=8000)
    assert page["text"] == "Z" * page["next_offset"] and page["next_offset"] > 0


def test_giant_key_pointer_does_not_stall_text_paging(tmp_path):
    key = "large" * 4000
    record = {"schema": R.REPORT_SCHEMA, "result": {key: "abc"}}
    ref = R.save_report(tmp_path, record)
    page = R.read_report(tmp_path, ref["id"], "/result/" + key, limit=1, mode="text")
    assert page["text"] == "a" and page["next_offset"] == 1 and page["pointer_echo_shortened"]
    assert len(R.dumps(page)) <= R.PAGE_CHARS


@pytest.mark.parametrize("report", ["../.env", "x.spec.json", "/tmp/report", "a"*63, "g"*64])
def test_reader_confines_ids_before_reading(tmp_path, report):
    with pytest.raises(ValueError, match="full_report.id"):
        R.read_report(tmp_path, report)


def test_reader_refuses_tampering_and_bad_pointers(tmp_path):
    _, output = emitted(tmp_path, long_result())
    ref = R._body(output[0].text)[0]["full_report"]
    for pointer in ("result", "/result/~2", "/result/missing", "/result/result/lenses/01"):
        with pytest.raises(ValueError):
            R.read_report(tmp_path, ref["id"], pointer)
    (tmp_path / ref["path"]).write_text("{}")
    with pytest.raises(ValueError, match="hash"):
        R.read_report(tmp_path, ref["id"])


def test_root_links_are_refused_without_following_them(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "job").mkdir()
    try:
        (tmp_path / "job/work").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not allow creating a test symlink")
    with pytest.raises(ValueError, match="links"):
        emitted(tmp_path / "job", long_result())
    assert not list(outside.iterdir())


def invoke(tool, args, cid="call-a"):
    raw = json.dumps(args)
    ctx = ToolContext(context=None, tool_name=tool.name, tool_call_id=cid, tool_arguments=raw)
    return asyncio.run(tool.on_invoke_tool(ctx, raw))


def test_main_registry_caps_output_but_keeps_full_journal_and_reader(tmp_path):
    result = long_result()
    records = []
    session = SimpleNamespace(head={"id": "c0001"}, call_read=lambda *a, **kw: deepcopy(result))
    scene = SimpleNamespace(root=tmp_path, s=session, lock=threading.RLock(), record_call=lambda *args: records.append(args), images=[])
    tools = {item.name: item for item in T.build_tools(scene)}
    output = invoke(tools["scene_summary"], {})
    body = R._body(output[0].text)[0]
    assert text_count(output) <= R.TEXT_LIMIT and records[0][-1]["huge_string"] == result["huge_string"]
    page = invoke(tools["read_report"], {"report": body["full_report"]["id"], "pointer": "/result/huge_string", "mode": "text", "limit": 8000}, "call-b")
    page_data = R._body(page[0].text)[0]
    assert page_data["text"] == result["huge_string"][:page_data["next_offset"]]
    assert text_count(page) <= R.TEXT_LIMIT


def test_facet_registry_caps_all_outputs_and_exposes_same_reader(tmp_path):
    lab = FakeLab()
    lab.root = tmp_path
    lab.inspect = lambda: long_result()
    tools = {item.name: item for item in F.registry(lab)}
    output = invoke(tools["facet_inspect"], {})
    body, _ = R._body(output[0].text)
    assert text_count(output) <= R.TEXT_LIMIT
    page = invoke(tools["read_report"], {"report": body["full_report"]["id"], "pointer": "/result/result/lenses/0/stations", "filter_path": "/verdict", "filter_value": "FAIL"}, "call-b")
    assert R._body(page[0].text)[0]["matched"] == 2


def test_external_failure_and_timeout_paths_are_bounded(tmp_path):
    async def returns(ctx, arguments):
        return "SDK failure " * 20000
    tool = SimpleNamespace(name="external", on_invoke_tool=returns,
                           failure_error_function=lambda ctx, error: "failure" * 30000,
                           timeout_error_function=lambda ctx, error: "timeout" * 30000)
    R.bounded_tool(tool, tmp_path)
    ctx = SimpleNamespace(tool_call_id="error")
    assert len(asyncio.run(tool.on_invoke_tool(ctx, "{}"))) <= R.TEXT_LIMIT
    assert len(tool.failure_error_function(ctx, ValueError())) <= R.TEXT_LIMIT
    assert len(tool.timeout_error_function(ctx, TimeoutError())) <= R.TEXT_LIMIT


def panel_output(count, *, reused=False, footer_in_body=False):
    footer = ("spent $11.90 (settled); held $0.00; settled stop $20.00 (reserve cap $23.00) · "
              "context 127982 tokens\nAbout $8.10 left; finish your best checkpoint soon.")
    records, parts = [], [None]
    for index in range(count):
        digest = hashlib.sha256(f"panel-{index}".encode()).hexdigest()
        record = {"view": f"photo-{index:03d}: inner rim native detail 1.1", "sha256": digest,
                  "call_id": "current-call-with-provider-identity", "output_index": None if reused else len(parts) + 1}
        if reused:
            record["reused_from_call"] = "earlier-call-with-provider-identity"
        label = (f"Image: call {record['call_id']}; checkpoint c0010; revision c0010; view {record['view']}; "
                 f"sha256 {digest[:12]}.")
        if reused:
            label += f" Same image already delivered in call {record['reused_from_call']}."
        parts.append(ToolOutputText(text=label))
        if not reused:
            parts.append(ToolOutputImage(image_url="data:image/png;base64,eA==", detail="high"))
        records.append(record)
    parts[0] = ToolOutputText(text=R.dumps({"status": "ok", "images": records, "large": "x" * 20_000})
                             + ("\n" + footer if footer_in_body else ""))
    if not footer_in_body:
        parts.append(ToolOutputText(text=footer))
    return parts, records, footer


def test_crease_metrics_survive_oversized_dependencies_and_tables_with_three_images(tmp_path):
    original, records, footer = panel_output(3, footer_in_body=True)
    angles = [3.729716499, 6.729716499, 9.729716499, 11.729716499, 16.729716499]
    summary = {"baseline": "built 1.73\u00b0; measurement +1.73\u00b12.31\u00b0 unresolved; /baseline"}
    for index, degrees in enumerate(angles):
        summary[f"angle-{index:02d}"] = (
            f"built/requested {degrees:.3g}/{degrees:.3g}\u00b0 eligible; wall1mm; local\u0394\u03c7\u00b2+12.9; "
            f"max|r|3.59sd; moved D; surfaces10; /alternatives/{index}")
    for start in (0, 3):
        summary[f"exact_requested_degrees_{start // 3 + 1}"] = "; ".join(
            f"angle-{index:02d}={angles[index]!r}" for index in range(start, min(start + 3, len(angles))))
    # Product comparisons carry six object tables and resolved fit dependencies; these
    # can force the smallest report envelope even though the five-choice summary is short.
    tables = [[{"name": f"candidate-{candidate}-object-{row}", **{
        f"diagnostic_{column}": "finite geometry measurement " * 20 for column in range(12)}}
        for row in range(19)] for candidate in range(6)]
    payload = {"status": "ok", "schema": "g3d.crease_angle_comparison/1", "summary": summary,
               "baseline": {"objects": tables[0]},
               "alternatives": [{"degrees": degrees, "objects": tables[index + 1]}
                                for index, degrees in enumerate(angles)],
               "choice_dependencies": {"resolved_fits": [{"name": f"corner-{index}",
                    "residuals": list(range(100))} for index in range(100)]},
               "image_receipt": {"panels": records}}
    original[0] = ToolOutputText(text=R.dumps(payload) + "\n" + footer)
    before = [part.model_dump() for part in original]
    output = R.limit_output(tmp_path, "crease-compare", "facet_crease_compare", original)
    bounded, suffix = R._body(output[0].text)
    assert bounded.get("summary") == summary
    assert text_count(output) <= R.TEXT_LIMIT and suffix == "\n" + footer
    assert len(output) == len(original)
    for index, part in enumerate(original):
        if part.type == "image":
            assert output[index] is part
            assert records[index // 2 - 1]["sha256"][:12] in output[index - 1].text
    assert [part.model_dump() for part in original] == before
    saved = json.loads((tmp_path / bounded["full_report"]["path"]).read_text(encoding="utf8"))
    assert saved["result"] == payload
    assert saved["text_parts"] == [{"output_index": index, "text": part.text}
                                  for index, part in enumerate(original) if part.type == "text"]


@pytest.mark.parametrize("reused", [False, True])
@pytest.mark.parametrize("footer_in_body", [False, True])
def test_many_panels_keep_each_identity_origin_footer_and_original(tmp_path, reused, footer_in_body):
    original, records, footer = panel_output(56, reused=reused, footer_in_body=footer_in_body)
    before = [part.model_dump() for part in original]
    output = R.limit_output(tmp_path, "current", "compare_look_to_photos", original)
    assert text_count(output) <= R.TEXT_LIMIT and len(output) == len(original)
    labels = [part.text for part in output[1:] if part.type == "text" and part.text != footer]
    assert len(labels) == len(records)
    for label, rec in zip(labels, records):
        assert label and rec["sha256"][:12] in label
        assert rec.get("reused_from_call", rec["call_id"]) in label
        assert "photo-" in label
        assert ("reused from" in label) is reused
    if footer_in_body:
        assert R._body(output[0].text)[1] == "\n" + footer
    else:
        assert output[-1].text == footer
    for index, part in enumerate(original):
        if part.type == "image":
            assert output[index] is part
    assert [part.model_dump() for part in original] == before
    body, _ = R._body(output[0].text)
    saved = json.loads((tmp_path / body["full_report"]["path"]).read_text(encoding="utf8"))
    assert saved["text_parts"] == [{"output_index": index, "text": part.text}
                                    for index, part in enumerate(original) if part.type == "text"]


@pytest.mark.parametrize("reused", [False, True])
def test_extreme_panel_count_uses_unique_lossless_identity_keys(tmp_path, reused):
    original, records, footer = panel_output(1024, reused=reused)
    output = R.limit_output(tmp_path, "current", "compare_look_to_photos", original)
    assert text_count(output) <= R.TEXT_LIMIT and len(output) == len(original)
    labels = [part.text for part in output[1:-1] if part.type == "text"]
    assert labels == [f"I{index + 1}" for index in range(len(records))]
    assert len(set(labels)) == len(records) and output[-1].text == footer
    body = R._body(output[0].text)[0]
    assert "text_parts[n]" in body["image_label_note"]
    saved = json.loads((tmp_path / body["full_report"]["path"]).read_text(encoding="utf8"))
    for index, label in enumerate(labels):
        original_label = saved["text_parts"][int(label[1:])]["text"]
        assert records[index]["sha256"][:12] in original_label
        assert records[index].get("reused_from_call", records[index]["call_id"]) in original_label
    for index, part in enumerate(original):
        if part.type == "image":
            assert output[index] is part


def test_short_outputs_are_returned_unchanged(tmp_path):
    image = ToolOutputImage(image_url="data:image/png;base64,eA==", detail="high")
    output = [ToolOutputText(text='{"status":"ok"}'), ToolOutputText(text="Original label"), image,
              ToolOutputText(text="spent $0 (settled)")]
    assert R.limit_output(tmp_path, "short", "fixture", output) is output
    assert not (tmp_path / "work").exists()


@pytest.mark.parametrize("nested", [False, True])
def test_every_lens_gap_stays_on_first_page_and_matches_lossless_colour_fields(tmp_path, nested):
    from glasses3d.agent.tools import look

    # More than the pager's six-row sample, plus enough real image labels to force
    # compaction. Include an unmeasured view without inventing a colour value.
    rows, expected = [], []
    for i in range(8):
        row = {"ref": f"reference-{i + 1:02d}", "colours": {"lens": {
            "photo": {"status": "measured", "pixels": 100, "median_srgb_8bit": [152, 151.5, 163],
                      "median_lightness_Lstar": 63.44},
            "drawing": {"status": "measured", "pixels": 100, "median_srgb_8bit": [82 + i, 105, 100],
                        "median_lightness_Lstar": 42.86 + i}}}}
        rows.append(row)
        expected.append(f"reference-{i + 1:02d} lens: AR L* {42.86 + i:.1f} vs photo 63.4 "
                        f"({20.58 - i:.1f} darker); median RGB (sRGB 8-bit) AR {82 + i},105,100 "
                        "vs photo 152,151.5,163")
    empty = {"status": "unavailable", "pixels": 0, "reason": "no interior pixels after three-pixel erosion"}
    rows.append({"ref": "reference-09", "colours": {"lens": {"photo": empty, "drawing": empty}}})
    expected.append("reference-09 lens: unavailable — no interior pixels after three-pixel erosion")
    look_result = {"schema": look.SCHEMA, "status": "ok", "views": rows,
                   "lens_colour_gap": look.lens_colour_lines(rows),
                   "surface_quality": {"status": "measured", "detail": "native measurement " * 20000}}
    original, records, footer = panel_output(56)
    payload = {"status": "review_required", "look_comparison": look_result,
               "review": {"review_call_id": "first", "required_refs": [row["ref"] for row in rows]}} if nested else look_result
    payload["images"] = records
    original[0] = ToolOutputText(text=R.dumps(payload))
    before = deepcopy(payload)
    output = R.limit_output(tmp_path, "gap-check", "finish" if nested else "compare_look_to_photos", original)
    visible = R._body(output[0].text)[0]
    first = visible["look_comparison"] if nested else visible
    assert first["lens_colour_gap"] == expected
    assert first["surface_quality"]["status"] == "measured"
    assert text_count(output) <= R.TEXT_LIMIT and output[-1].text == footer
    if nested:
        assert visible["review"] == payload["review"]
    saved = json.loads((tmp_path / visible["full_report"]["path"]).read_text(encoding="utf-8"))
    assert saved["result"] == before == payload
    pointer = "/result/look_comparison" if nested else "/result"
    for i, line in enumerate(first["lens_colour_gap"]):
        page = R.read_report(tmp_path, visible["full_report"]["id"], f"{pointer}/views/{i}/colours/lens")
        stats = {entry["key"]: entry["value"] for entry in page["entries"]}
        assert stats == rows[i]["colours"]["lens"]
        if stats["photo"]["status"] == "measured":
            assert f"{stats['drawing']['median_lightness_Lstar']:.1f}" in line
            assert f"{stats['photo']['median_lightness_Lstar']:.1f}" in line
    assert [p for p in output if p.type == "image"] == [p for p in original if p.type == "image"]


def test_many_nonimage_tail_parts_still_respect_aggregate_limit(tmp_path):
    original = [ToolOutputText(text=R.dumps(long_result()))]
    original += [ToolOutputText(text="ordinary diagnostic " * 1000) for _ in range(100)]
    original.append(ToolOutputText(text="spent $0 (settled); held $0"))
    output = R.limit_output(tmp_path, "current", "fixture", original)
    assert text_count(output) <= R.TEXT_LIMIT and len(output) == len(original)
    assert output[-1] is not None and output[-1].text == original[-1].text


@pytest.mark.parametrize("nested", [False, True])
def test_required_review_and_surface_fields_survive_long_outputs(tmp_path, nested):
    refs = [f"view-{n:03d}" for n in range(40)]
    diagnostics = {"status": "measured", "planarity": {"worst_mm": .12},
                   "views": [{"ref": ref, "details": ["measurement" * 500] * 8} for ref in refs]}
    payload = {"status": "review_required", **{f"early_{n}": "x" * 8000 for n in range(20)},
               "review": {"required_refs": refs, "review_call_id": "call-first-review",
                          "binding": {"photos": [{"ref": ref, "sha256": "d" * 64} for ref in refs]}},
               "look_comparison" if nested else "surface_quality": (
                   {"surface_quality": diagnostics, "usable_refs": refs, "unusable_refs": {"bad": "fit failed"}}
                   if nested else diagnostics)}
    output = R.limit_output(tmp_path, "finish", "finish", [ToolOutputText(text=R.dumps(payload))])
    body, _ = R._body(output[0].text)
    assert text_count(output) <= R.TEXT_LIMIT
    assert body["review"]["required_refs"] == refs
    assert body["review"]["review_call_id"] == "call-first-review"
    look = body["look_comparison"] if nested else body
    assert look["surface_quality"]["status"] == "measured"
    if nested:
        assert look["usable_refs"] == refs and look["unusable_refs"] == {"bad": "fit failed"}
    rid = body["full_report"]["id"]
    page = R.read_report(tmp_path, rid, "/result/review/required_refs", limit=100)
    assert [entry["value"] for entry in page["entries"]] == refs
    saved = json.loads((tmp_path / body["full_report"]["path"]).read_text(encoding="utf-8"))
    assert saved["result"] == payload


def test_mandatory_echo_fields_survive_small_envelope_after_image_labels(tmp_path):
    output, _, _ = panel_output(1024)
    body, _ = R._body(output[0].text)
    body["review"] = {"required_refs": [f"reference-{n}" for n in range(60)], "review_call_id": "review-1"}
    body["surface_quality"] = {"status": "measured", "views": [{"detail": "d" * 10000}] * 20}
    output[0] = ToolOutputText(text=R.dumps(body))
    result = R.limit_output(tmp_path, "finish", "finish", output)
    visible, _ = R._body(result[0].text)
    assert text_count(result) <= R.TEXT_LIMIT
    assert visible["review"] == body["review"]
    assert visible["surface_quality"]["status"] == "measured"


def test_oversized_required_reference_list_has_explicit_lossless_paging_without_tool_failure(tmp_path):
    refs = [f"reference-{n:03d}-" + "x" * 120 for n in range(100)]
    payload = {"status": "review_required", "review": {"required_refs": refs, "review_call_id": "review-100"}}
    output = R.limit_output(tmp_path, "review-100", "finish", [ToolOutputText(text=R.dumps(payload))])
    body, _ = R._body(output[0].text)
    assert text_count(output) <= R.TEXT_LIMIT
    assert body["review"]["review_call_id"] == "review-100"
    field = body["review"]["required_refs"]
    assert field["count"] == 100 and field["read_pointer"] == "/result/review/required_refs"
    assert "Required values" in field["read_note"]
    result, offset = [], 0
    while offset is not None:
        page = R.read_report(tmp_path, body["full_report"]["id"], field["read_pointer"], offset, 20)
        result.extend(entry["value"] for entry in page["entries"])
        offset = page["next_offset"]
    assert result == refs
