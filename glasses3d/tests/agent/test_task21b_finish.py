"""Inline difference reconciliation through real SDK tools and the offline loop."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from glasses3d.agent import differences as D, loop as L, renewal as N, replay as R, tools as T
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.jsonl import read_jsonl
from glasses3d.tests.agent import loop_support
from glasses3d.tests.agent.loop_support import make_job, spec_for
from glasses3d.tests.agent.test_finish_renewal import FinishRunnerScene, REPLAY_TOKENS, RUN
from glasses3d.tests.agent.test_finish_review import review_scene, first
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_task21_differences import decision_scene, entry, finish_args
from glasses3d.tests.agent.test_task21_renewal import MemorySession


def journal_path(scene):
    return scene.root / "runs" / scene.active_run_id / "differences.jsonl"


def difference_rows(scene):
    return [row for row in read_jsonl(journal_path(scene)).rows if row["kind"] == "difference"]


def seed_items(scene, *, stale=False):
    """An earlier comparison's host list, without any scripted bookkeeping requests."""
    assert first(scene)["status"] == "review_required"
    for item in [entry(), entry(id="tip-length", what_differs="Temple tips extend too far.",
                               refs=["front", "top"], status="fixed" if stale else "open",
                               last_correction="Compared a shorter temple-tip candidate.",
                               outcome="The comparison showed improved tip length.")]:
        assert D.record(scene, **item, call_id="earlier-update")["status"] == "ok"
    if stale:
        scene.s.head["fingerprint"] = scene.export["fingerprint"] = "current-head-fingerprint"
    return D.snapshot(scene)


def updates(items, *, evidence_ref=None):
    result = []
    for index, item in enumerate(items):
        resolved = {key: value for key, value in item.items() if key != "evidence_ref"}
        resolved.update(status="fixed" if index == 0 else "limitation",
                        last_correction="Compared a corrected candidate and kept this current state.",
                        outcome="Current review supports the retained correction and its stated tradeoff.",
                        next_action="No further correction justified by the current views.",
                        reason=None if index == 0 else "Shortening the tips loses the compared worn-side fit.")
        if evidence_ref is not None:
            resolved["evidence_ref"] = evidence_ref
        result.append(resolved)
    return result


def test_inline_confirm_reconciles_all_open_and_stale_items_with_current_review(decision_scene):
    s = decision_scene
    old_items = seed_items(s, stale=True)
    before = journal_path(s).read_bytes()
    assert record(invoke(s, "finish", {"summary": "Review current head"}, cid="current"))["status"] == "review_required"
    out = record(invoke(s, "finish", finish_args(review_call_id="current",
                                                difference_updates=updates(old_items)), cid="confirmed"))
    assert out["status"] == "accepted" and s.finish == out
    assert [(item["id"], item["status"], item["evidence_ref"]) for item in D.snapshot(s)] == [
        ("lens-hue", "fixed", "current"), ("tip-length", "limitation", "current")]
    assert out["differences"] == D.snapshot(s)
    assert journal_path(s).read_bytes().startswith(before)
    assert [row["call_id"] for row in difference_rows(s)[-2:]] == ["confirmed", "confirmed"]


def test_scripted_finish_is_two_requests_with_inline_updates_and_no_bookkeeping_request(
        tmp_path, monkeypatch, decision_scene):
    s = decision_scene
    old_items = seed_items(s, stale=True)
    s.calls.clear()
    runner_scene = FinishRunnerScene(s)
    tools = [tool for tool in T.build_tools(s) if tool.name == "finish"]
    settings = L.RunSettings(effort="max", max_turns=4, max_output_tokens=100,
                             finish_tool="finish", settled_stop_usd="20")
    with monkeypatch.context() as patch:
        patch.setattr(loop_support, "tool_registry_sha256", lambda tools: L.tool_registry_sha256(tools, settings))
        job, sha = make_job(tmp_path / "loop", tools=tools, settings=settings, cap="20")
    replies = [R.tool_call("finish", {"summary": "Inspect current photos"}, call_id="current"),
               R.tool_call("finish", finish_args(review_call_id="current",
                                                 difference_updates=updates(old_items)), call_id="confirmed")]
    transport = R.ScriptedOpenAI(replies, count_tokens=lambda body:
        REPLAY_TOKENS if any(item.get("type") == "function_call_output" and item.get("call_id") == "current"
                            for item in body.get("input", [])) else 100)
    result = asyncio.run(L.run_astra(
        spec_for(job, RUN, sha, tools, transport, settings=settings, requested_cap_usd="20"),
        scene=runner_scene))
    assert result["status"] == "completed", result
    assert result["requests"] == len(transport.creates) == 2
    assert result["renewals"] == [] and transport.violations == []
    assert [call[1] for call in s.calls] == ["finish", "finish"]
    assert s.finish["status"] == "accepted"
    assert {item["status"] for item in D.snapshot(s)} == {"fixed", "limitation"}
    assert all(item["evidence_ref"] == "current" for item in D.snapshot(s))


@pytest.mark.parametrize("defect", ["unknown_evidence", "stale_evidence", "missing_reason", "lost_refs"])
def test_invalid_inline_item_commits_nothing_and_same_review_can_be_retried(decision_scene, defect):
    s = decision_scene
    original = seed_items(s, stale=True)
    assert record(invoke(s, "finish", {"summary": "Current comparison"}, cid="current"))["status"] == "review_required"
    receipt = copy.deepcopy(s.finish_review)
    before = difference_rows(s)
    proposed = updates(original)
    if defect == "unknown_evidence":
        proposed[1]["evidence_ref"] = "never-delivered"
    elif defect == "stale_evidence":
        proposed[1]["evidence_ref"] = "first"
    elif defect == "missing_reason":
        proposed[1].pop("reason")
    else:
        proposed[1]["refs"] = ["front"]
    refused = record(invoke(s, "finish", finish_args(review_call_id="current",
                                                    difference_updates=proposed), cid="bad-confirm"))
    assert refused["status"] == "refused" and refused["code"] == "E_DIFFERENCES"
    assert "tip-length" in refused["message"]
    assert D.snapshot(s) == original and difference_rows(s) == before
    assert s.finish is None and s.finish_review == receipt
    accepted = record(invoke(s, "finish", finish_args(review_call_id="current",
                                                     difference_updates=updates(original)), cid="retry"))
    assert accepted["status"] == "accepted"
    assert all(row["call_id"] == "retry" for row in difference_rows(s)[len(before):])


@pytest.mark.parametrize("failure", ["output", "journal", "unseen"])
def test_undelivered_inline_confirmation_commits_no_updates(decision_scene, monkeypatch, failure):
    s = decision_scene
    original = seed_items(s)
    before = difference_rows(s)
    original_limit = T.limit_output
    with monkeypatch.context() as patch:
        if failure == "journal":
            def fail_journal(*args, **kwargs):
                raise OSError("confirmation journal failed")
            patch.setattr(s, "record_call", fail_journal)
        else:
            def fail_output(root, cid, name, output):
                if failure == "output":
                    raise OSError("confirmation output failed")
                s.mark_unseen(cid)
                return original_limit(root, cid, name, output)
            patch.setattr(T, "limit_output", fail_output)
        failed = invoke(s, "finish", finish_args(difference_updates=updates(original)), cid="lost-confirm")
    if failure != "unseen":
        assert isinstance(failed, str) and f"confirmation {failure} failed" in failed
    assert s.finish is None
    assert D.snapshot(s) == original and difference_rows(s) == before
    assert "lost-confirm" in s.unseen
    assert record(invoke(s, "finish", {"summary": "Review again"}, cid="retry-review"))["status"] == "review_required"
    accepted = record(invoke(s, "finish", finish_args(review_call_id="retry-review",
        difference_updates=updates(original)), cid="retry-confirm"))
    assert accepted["status"] == "accepted"
    assert all(row["call_id"] == "retry-confirm" for row in difference_rows(s)[len(before):])


def test_unresolved_item_refuses_inline_subset_without_committing_it(decision_scene):
    s = decision_scene
    original = seed_items(s)
    before = difference_rows(s)
    refused = record(invoke(s, "finish", finish_args(difference_updates=updates(original)[:1]), cid="partial"))
    assert refused["status"] == "refused" and refused["open_difference_ids"] == ["tip-length"]
    assert D.snapshot(s) == original and difference_rows(s) == before
    accepted = record(invoke(s, "finish", finish_args(difference_updates=updates(original)), cid="complete"))
    assert accepted["status"] == "accepted"


@pytest.mark.parametrize("condition", ["before_first_review", "stale_review", "wrong_review_id"])
def test_inline_confirmation_requires_current_delivered_review(decision_scene, condition):
    s = decision_scene
    if condition == "before_first_review":
        original, proposed, before = [], updates([entry()]), []
    else:
        original = seed_items(s, stale=condition == "stale_review")
        proposed, before = updates(original), difference_rows(s)
    args = finish_args(review_call_id="invented" if condition == "wrong_review_id" else "first",
                       difference_updates=proposed)
    refused = record(invoke(s, "finish", args, cid="premature-confirm"))
    assert refused["status"] == "refused"
    assert refused["code"] == ("E_FINISH_REVIEW" if condition == "wrong_review_id" else "E_DIFFERENCES")
    assert D.snapshot(s) == original and difference_rows(s) == before
    assert s.finish is None
    assert record(invoke(s, "finish", {"summary": "Inspect current evidence"}, cid="fresh"))["status"] == "review_required"
    accepted = record(invoke(s, "finish", finish_args(review_call_id="fresh",
                                                     difference_updates=proposed), cid="retry"))
    assert accepted["status"] == "accepted"
    assert all(item["evidence_ref"] == "fresh" for item in D.snapshot(s))


@pytest.mark.parametrize("new_value", [[], [entry(status="fixed")]], ids=["empty", "nonempty"])
def test_inline_parameter_is_available_only_in_decision_profile(review_scene, new_value):
    s = review_scene
    s.instruction_profile = "decision-20261021"
    decision_tool = next(tool for tool in T.build_tools(s) if tool.name == "finish")
    assert "difference_updates" in decision_tool.params_json_schema["properties"]
    s.instruction_profile = "legacy-20261021"
    legacy_tool = next(tool for tool in T.build_tools(s) if tool.name == "finish")
    assert "difference_updates" not in legacy_tool.params_json_schema["properties"]
    assert first(s)["status"] == "review_required"
    # The installed SDK ignores unknown top-level arguments for legacy tools.
    # Preserve that behavior: this field neither enters the schema nor reconciles.
    ignored = record(invoke(s, "finish", finish_args(difference_updates=new_value), cid="legacy-inline"))
    assert ignored["status"] == "accepted" and s.finish == ignored
    assert not {"differences", "difference_updates", "difference_reconciliation"} & ignored.keys()
    assert D.snapshot(s) == []
    accepted = record(invoke(s, "finish", finish_args(), cid="legacy-confirm"))
    assert accepted["status"] == "accepted"


@pytest.mark.parametrize("mode", ["batch", "inline"])
def test_renewal_carries_batch_or_inline_updated_host_list(decision_scene, mode):
    s = decision_scene
    original = seed_items(s)
    proposed = updates(original, evidence_ref="first")
    if mode == "batch":
        assert record(invoke(s, "record_difference", {"items": proposed}, cid="batch"))["status"] == "ok"
    else:
        assert record(invoke(s, "finish", finish_args(difference_updates=proposed), cid="inline"))["status"] == "accepted"
    expected = D.snapshot(s)
    before = journal_path(s).read_bytes()
    s._state_lock = threading.RLock()
    s.save = lambda: None
    s.renewal_state = lambda: {"head_checkpoint": s.s.head["id"], "best_checkpoint": None}
    s.renewal_evidence = lambda: []
    s.renewal_started = lambda: JobScene.renewal_started(s)
    session = MemorySession()
    original_conversation = copy.deepcopy(session.items)

    async def response(**request):
        return SimpleNamespace(id="summary", usage=None, status="completed", output=[],
                               output_text="There are no recorded differences.")

    async def llm_hook(*args):
        pass

    guard = SimpleNamespace(request_pressure=None, renewal_summary=False,
        summary=lambda: {"requests": 2, "settled_usd": "1.25", "settled_stop_usd": "20"})
    log = SimpleNamespace(run_id=s.active_run_id, interactions=0, tool_calls=[],
        event=lambda *args, **kwargs: None, note=lambda *args, **kwargs: None)
    spec = SimpleNamespace(owner_notes=[], brief="Owner's unchanged brief.", photos=[],
        tools=[SimpleNamespace(name="record_difference")], settings=L.RunSettings())
    renewal = N.ConversationRenewal(spec=spec, scene=s, session=session, log=log, guard=guard,
        client=SimpleNamespace(responses=SimpleNamespace(create=response)), directory=journal_path(s).parent)
    trigger = N.RenewalNeeded({"input": original_conversation}, {"input_tokens": 250000},
                             [{"reason": "input_tokens"}])
    asyncio.run(renewal.renew(trigger, agent=SimpleNamespace(instructions="same instructions"), context=None,
                             hooks=SimpleNamespace(on_llm_start=llm_hook, on_llm_end=llm_hook)))
    assert renewal.records[0]["host_state"]["differences"] == expected
    assert {item["status"] for item in expected} == {"fixed", "limitation"}
    texts = [part["text"] for part in session.items[0]["content"] if part.get("type") == "input_text"]
    assert any(json.dumps(expected, ensure_ascii=False) in text for text in texts)
    assert journal_path(s).read_bytes() == before
    assert session.archived[:len(original_conversation)] == original_conversation
