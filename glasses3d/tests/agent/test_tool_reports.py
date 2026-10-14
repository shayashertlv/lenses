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
