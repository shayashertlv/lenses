"""M5-prep runway: real SDK and budget accounting over the offline scripted transport."""
import asyncio
import json

from agents.exceptions import ToolTimeoutError
from agents.tool import ToolOutputImage, ToolOutputText, default_tool_timeout_error_message
from agents.tool_context import ToolContext
from PIL import Image
import pytest

from glasses3d.agent import budget as B, loop as L, replay as R, tools as T
from glasses3d.agent.journal import WorkerFailed
from glasses3d.tests.agent.loop_support import FakeScene, ledger_rows, make_job, spec_for
from glasses3d.tests.agent.support import fake_counter, provider_body, request, response, usage
from glasses3d.tests.agent.test_job_tools import invoke, record, scene  # noqa: F401 - shared pytest fixture


RUN = "20261013T180000-runway"


def _settled_guard(tmp_path, *, stop="20", tokens=100_000):
    counter, _ = fake_counter(tokens)
    guard = B.BudgetGuard(counter, tmp_path / "runway-budget.json", "23", settled_stop_usd=stop)

    async def settle():
        await guard.before_request(request(max_output_tokens=100))
        await guard.after_response(response(body=provider_body(
            usage_block=usage(tokens=tokens, written=tokens, output=0))))

    asyncio.run(settle())
    return guard


def _footer(output):
    assert isinstance(output[-1], ToolOutputText)
    return output[-1].text


def test_real_sdk_sees_runway_notice_when_settled_spend_crosses_75_percent(tmp_path, scene, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a production client must never be constructed in a scripted runway test")

    monkeypatch.setattr(L, "ProductionClients", forbidden)
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=5, settled_stop_usd="20")
    tools = T.build_tools(scene, settings)
    job, approval_sha = make_job(tmp_path, tools=tools, run_id=RUN, cap="23", settings=settings)
    transport = R.ScriptedOpenAI([
        R.tool_call(name, counted=counted, usage=R.default_usage(counted, output_tokens=0, reasoning_tokens=0))
        for name, counted in (("scene_summary", 400_000), ("scene_summary", 200_000), ("budget_status", 200_000))
    ] + [R.message("Ready to finish the best checkpoint.", counted=1000,
                    usage=R.default_usage(1000, output_tokens=0, reasoning_tokens=0))])
    result = asyncio.run(L.run_astra(spec_for(job, RUN, approval_sha, tools, transport, settings=settings),
                                    scene=FakeScene()))
    assert result["status"] == "completed", result
    assert len(transport.counts) == len(transport.creates) == 4
    assert transport.violations == transport.unexpected == []
    assert ("This run's working limit: $20.000000 settled stop (reserve cap $23.000000)."
            in json.dumps(transport.creates[0]["input"]))
    rows = ledger_rows(job, RUN)
    assert [row["settled_micro_usd"] for row in rows[:3]] == [10_000_000, 2_500_000, 2_500_000]
    assert all(row["outcome"] == "settled" and row["settled_from"] == "transport" for row in rows)
    settled = 0
    for index, expected in enumerate(("10.00", "12.50", "15.00"), 1):
        settled += rows[index - 1]["settled_micro_usd"]
        outputs = [item for item in transport.creates[index]["input"]
                   if item.get("type") == "function_call_output" and item["call_id"] == f"call_{index:04d}"]
        assert len(outputs) == 1
        content = outputs[0]["output"]
        assert isinstance(content, list) and content[0]["type"] == content[-1]["type"] == "input_text"
        body = json.loads(content[0]["text"])  # first text stays independently parseable JSON
        footer = content[-1]["text"]
        assert f"settled ${expected} of $20.00 stop (reserve cap $23.00)" in footer
        assert ("finish your best checkpoint soon" in footer) is (index == 3)
        if index == 3:
            assert "\nAbout $5.00 left; export, AR-capture and finish your best checkpoint soon." in footer
            assert body["budget"]["settled_micro_usd"] == settled
            assert body["budget"]["settled_stop_usd"] == "20.000000"
            assert body["budget"]["remaining_to_stop_usd"] == "5.000000"
            assert body["budget"]["maximum_usd"] == "23.000000"
        else:
            assert body["result"]["objects"] == ["frame"]
    assert result["budget"]["remaining_to_stop_usd"] == B.usd_text(
        20_000_000 - sum(row["settled_micro_usd"] for row in rows))


@pytest.mark.parametrize("outcome", ["held", "unknown"])
@pytest.mark.parametrize("stop,remaining", [("20", "17.495000"), ("2", "0.000000")])
def test_runway_deducts_unsettled_holds_and_clamps_zero_without_releasing_money(
        tmp_path, scene, outcome, stop, remaining):
    guard = _settled_guard(tmp_path, stop=stop)

    async def hold():
        await guard.before_request(request(max_output_tokens=100))
        if outcome == "unknown":
            await guard.mark_unknown("scripted connection dropped after the request was sent")

    asyncio.run(hold())
    before = guard.ledger_path.read_bytes()
    summary = guard.summary()
    assert summary["settled_usd"] == "1.250000"
    assert summary[f"{outcome}_usd"] == "1.255000"
    assert summary["committed_usd"] == "2.505000"
    assert summary["settled_stop_usd"] == f"{int(stop)}.000000"
    assert summary["remaining_to_stop_usd"] == remaining
    output = invoke(scene, "budget_status", context=L.RunStatus(guard))
    assert record(output)["budget"] == summary
    # Warning starts from settled spend, not from an unbilled request's reservation.
    assert "finish your best checkpoint soon" not in _footer(output)
    assert guard.ledger_path.read_bytes() == before


def test_legacy_guard_without_stop_keeps_cap_footer_and_null_runway(tmp_path, scene):
    guard = _settled_guard(tmp_path, stop=None)
    before = guard.ledger_path.read_bytes()
    summary = guard.summary()
    assert summary["settled_stop_usd"] is None and summary["remaining_to_stop_usd"] is None
    output = invoke(scene, "budget_status", context=L.RunStatus(guard))
    assert record(output)["budget"] == summary
    assert "settled $1.25 / cap $23.00" in _footer(output)
    assert " stop " not in _footer(output) and "finish your best checkpoint soon" not in _footer(output)
    assert guard.ledger_path.read_bytes() == before


@pytest.mark.parametrize("tokens,notice", [(5999, False), (6000, True), (6001, True)])
def test_notice_includes_exact_75_percent_for_decimal_dollar_limits(tmp_path, tokens, notice):
    guard = _settled_guard(tmp_path, stop="0.10", tokens=tokens)
    summary = guard.summary()
    if tokens == 6000:
        assert summary["settled_micro_usd"] == 75_000
        assert summary["remaining_to_stop_usd"] == "0.025000"
    assert ("finish your best checkpoint soon" in L.RunStatus(guard).footer()) is notice


def test_footer_is_separate_from_json_and_preserves_image_positions(tmp_path, scene):
    guard = _settled_guard(tmp_path, tokens=600_000)  # exact $15 settled at the frozen long-context tariff
    path = scene.root / "work/runway-sheet.png"
    Image.new("RGB", (8, 8), (40, 60, 80)).save(path)
    scene.seeing.render_views = lambda **kwargs: {"status": "ok", "sheets": [str(path)],
                                                "views": {"front": {"kind": "canonical"}}}
    output = invoke(scene, "render_views", context=L.RunStatus(guard))
    data = record(output)
    assert len(output) == 4 and data["images"][0]["output_index"] == 2
    assert isinstance(output[1], ToolOutputText) and "view canonical:front" in output[1].text
    assert isinstance(output[2], ToolOutputImage) and output[2].image_url.startswith("data:image/png;base64,")
    assert _footer(output) == L.RunStatus(guard).footer()
    assert "About $5.00 left" in _footer(output)


@pytest.mark.parametrize("kind", ["busy", "refused"])
def test_early_tool_results_also_carry_the_runway_footer(tmp_path, scene, kind):
    guard = _settled_guard(tmp_path, tokens=600_000)
    if kind == "busy":
        class BusyLock:
            def acquire(self, **kwargs):
                return False
        scene.lock = BusyLock()
        output = invoke(scene, "scene_summary", context=L.RunStatus(guard))
        assert record(output)["code"] == "E_BUSY"
    else:
        output = invoke(scene, "read_image", {"path": "work/missing.png"}, context=L.RunStatus(guard))
        assert record(output)["status"] == "refused"
    assert len(output) == 2 and _footer(output) == L.RunStatus(guard).footer()
    assert "About $5.00 left" in _footer(output)


def test_sdk_timeout_keeps_original_message_and_adds_current_runway(tmp_path, scene):
    runtime = L.RunStatus(_settled_guard(tmp_path, tokens=600_000))
    tool = next(tool for tool in T.build_tools(scene) if tool.name == "scene_summary")
    context = ToolContext(context=runtime, tool_name=tool.name, tool_call_id="timeout", tool_arguments="{}")
    error = ToolTimeoutError(tool.name, 1.25)
    original = default_tool_timeout_error_message(tool_name=tool.name, timeout_seconds=1.25)
    assert original == "Tool 'scene_summary' timed out after 1.25 seconds."
    assert tool.timeout_error_function(context, error) == original + "\n" + runtime.footer()
    assert "About $5.00 left" in runtime.footer()
    assert scene.s.calls == []


def test_sdk_schema_failure_keeps_original_error_and_runway_but_fatal_errors_escape(tmp_path, scene):
    runtime = L.RunStatus(_settled_guard(tmp_path, tokens=600_000))
    invalid = {"code": None}
    original = invoke(scene, "execute_blender_code", invalid)
    assert isinstance(original, str) and "Invalid JSON input for tool execute_blender_code" in original
    assert invoke(scene, "execute_blender_code", invalid, context=runtime) == original + "\n" + runtime.footer()
    assert scene.s.calls == []
    error = WorkerFailed("the worker is gone", scene.s.head)

    def fail(*args, **kwargs):
        raise error

    scene.s.execute = fail
    with pytest.raises(WorkerFailed) as caught:
        invoke(scene, "execute_blender_code", {"code": "result=1"}, context=runtime)
    assert caught.value is error
