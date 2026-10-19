"""Astra sees settled facts only, including throughout the two-call finish review."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from glasses3d.agent import instructions, loop, money, tools
from glasses3d.agent.temple_lab import stage
from glasses3d.tests.agent.test_finish_review import review_scene, final_args
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record


FORBIDDEN = ("finish_runway", "finish-runway", "finish runway", "Finish-review", "finish call(s)",
             "finish your best checkpoint soon", "Start the finish review now", "reserve cap",
             "held for the request", "allowance", "estimated cost", "cost projection", "projected cost")


def no_advice(text):
    for phrase in FORBIDDEN:
        assert phrase.lower() not in text.lower()


def runtime(settled="19.000000"):
    values = {"settled_usd": settled, "settled_stop_usd": "20.000000", "held_usd": "8.000000",
              "unknown_usd": "0.000000", "maximum_usd": "20.000000",
              "remaining_to_stop_usd": "0.000000", "remaining_usd": "0.000000",
              "planning": {"last_counted_input_tokens": 246969, "next_request_hold_usd": "50.000000"}}
    return loop.RunStatus(SimpleNamespace(summary=lambda: copy.deepcopy(values))), values


def start_review(scene, context):
    return invoke(scene, "finish", {"summary": "Inspect"}, cid="first", context=context)


@pytest.mark.parametrize("settled", ["0.000000", "15.000000", "19.999999", "20.100000"])
def test_money_facts_and_footer_never_estimate_or_urge_early_finish(settled):
    context, values = runtime(settled)
    before = copy.deepcopy(values)
    facts = money.money_facts(values)
    assert set(facts) == {"settled_usd", "settled_stop_usd", "remaining_to_stop_usd"}
    assert facts["remaining_to_stop_usd"] == {
        "0.000000": "20.000000", "15.000000": "5.000000", "19.999999": "0.000001",
        "20.100000": "0.000000"}[settled]
    assert f"spent ${settled} (settled)" in context.footer()
    assert "owner cutoff $20.000000" in context.footer()
    assert "context 246969 tokens" in context.footer()
    no_advice(context.footer())
    assert values == before


def test_first_finish_inspection_budget_and_second_finish_have_only_facts(review_scene):
    context, values = runtime("19.999999")
    before = copy.deepcopy(values)
    first = start_review(review_scene, context)
    assert record(first)["status"] == "review_required"
    from glasses3d.agent.tool_reports import save_report
    receipt = save_report(review_scene.root, {"result": {"status": "ok", "detail": "saved diagnostic"}})
    outputs = [first,
        invoke(review_scene, "read_image", {"path": "work/full-front.png"}, cid="inspect", context=context),
        invoke(review_scene, "read_report", {"report": receipt["id"]}, cid="report", context=context),
        invoke(review_scene, "budget_status", context=context),
        invoke(review_scene, "finish", final_args(), cid="second", context=context)]
    assert record(outputs[3])["budget"] == money.money_facts(values)
    assert record(outputs[-1])["status"] == "accepted" and review_scene.finish is not None
    for output in outputs:
        for item in output:
            if hasattr(item, "text"):
                no_advice(item.text)
        assert "left before cutoff $0.000001" in output[-1].text
    assert values == before


@pytest.mark.parametrize("failure", ["bounded_output", "journal"])
def test_terminal_delivery_failure_keeps_run_unfinished(review_scene, monkeypatch, failure):
    context, _ = runtime()
    start_review(review_scene, context)
    def fail(*args, **kwargs):
        raise OSError("terminal delivery failed")
    if failure == "bounded_output":
        monkeypatch.setattr(tools, "limit_output", fail)
    else:
        monkeypatch.setattr(review_scene, "record_call", fail)
    output = invoke(review_scene, "finish", final_args(), cid="failed-second", context=context)
    assert isinstance(output, str) and "terminal delivery failed" in output
    assert review_scene.finish is None and review_scene.finish_review is None
    no_advice(context.footer())


@pytest.mark.parametrize("owner_edit", [None, {"phase": "understand"}, {"phase": "edit"}])
def test_main_and_edit_instructions_and_tool_descriptions_have_one_cutoff_sentence(owner_edit):
    registry = tools.build_tools(SimpleNamespace(root=None, owner_edit=owner_edit))
    text = " ".join(instructions.build_instructions(registry, owner_edit=owner_edit).split())
    assert text.count(money.MONEY_INSTRUCTION) == 1
    no_advice(text)
    for tool in registry:
        no_advice(tool.description)


def test_specialist_and_opening_money_text_are_facts_only():
    context, values = runtime()
    text = " ".join((Path(stage.__file__).with_name("instructions.txt")).read_text(encoding="utf-8").split())
    assert text.count(money.MONEY_INSTRUCTION) == 1
    note = stage.money_note(context, loop.RunSettings(), "20")
    assert note == money.factual_money_text(values) + "."
    opening = loop.invocation_text(run_id="r", scene_note="Current scene.", cap_micro=20_000_000,
                                   settled_stop_micro=20_000_000, rollback=None)
    assert "This run's owner cutoff: $20.000000 settled spend." in opening
    no_advice(text + note + opening)


def test_nested_tool_budget_summary_is_sanitized_without_changing_owner_record():
    _, values = runtime()
    report = {"status": "ok", "child": {"budget": values}, "material": {"settled_usd": "blue"}}
    before = copy.deepcopy(report)
    shown = money.astra_money(report)
    assert shown["child"]["budget"] == money.money_facts(values)
    assert shown["material"] == report["material"]
    assert report == before
