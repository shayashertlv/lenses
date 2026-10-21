"""$0 proofs that a pending photo review gets one request before soft renewal."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from glasses3d.agent import budget as B, loop as L, replay as R, renewal as N, tools as T
from glasses3d.agent.job_scene import JobScene
from glasses3d.tests.agent import loop_support
from glasses3d.tests.agent.loop_support import FakeScene, make_job, spec_for, ledger_rows
from glasses3d.tests.agent.test_finish_review import review_scene, final_args
from glasses3d.tests.agent.test_job_tools import scene, finish_scene

RUN = "20261007T120000-run001"
REPLAY_TOKENS = 270_844


def candidate(tmp_path, *, pending=True):
    state = {"finish_review": {"status": "pending" if pending else "not_pending",
                               **({"call_id": "first"} if pending else {})}}
    events = []
    guard = SimpleNamespace(tariff=B.Tariff.frozen(), summary=lambda: {"requests": 51})
    obj = N.ConversationRenewal(
        spec=SimpleNamespace(owner_notes=[]),
        scene=SimpleNamespace(renewal_state=lambda: state),
        session=SimpleNamespace(session_id="astra"),
        log=SimpleNamespace(run_id="test-run", event=lambda name, **row: events.append((name, row))),
        guard=guard, client=None, directory=tmp_path)
    return obj, state, events


def test_pending_review_defers_soft_pressure_once_and_records_measured_receipt(tmp_path):
    obj, _, events = candidate(tmp_path)
    obj.check({"input": []}, 42_000_000, 181, REPLAY_TOKENS)
    receipt = json.loads((tmp_path / "renewals.json").read_text(encoding="utf-8"))
    assert receipt["renewals"] == []
    rows = receipt["finish_review_deferrals"]
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "g3d.conversation_renewal_deferral/1"
    assert row["conversation_id"] == "astra" and row["review_call_id"] == "first"
    assert row["request_sequence"] == 52
    assert row["measurements"] == {"payload_bytes": 42_000_000, "images": 181,
                                   "input_tokens": REPLAY_TOKENS}
    assert [reason["reason"] for reason in row["reasons"]] == ["input_tokens"]
    assert [name for name, _ in events] == ["conversation_renewal_deferred"]
    with pytest.raises(N.RenewalNeeded):
        obj.check({"input": []}, 42_000_000, 181, REPLAY_TOKENS)
    assert len(events) == len(obj.finish_review_deferrals) == 1


@pytest.mark.parametrize("kind", ["price", "context", "images", "payload_bytes"])
@pytest.mark.parametrize("over", [False, True], ids=["at-limit", "over-limit"])
def test_pending_review_respects_existing_hard_limit_boundaries(tmp_path, monkeypatch, kind, over):
    obj, _, _ = candidate(tmp_path)
    body_bytes, images, tokens = 1_000, 2, REPLAY_TOKENS
    if kind == "price":
        tokens = obj.guard.tariff.long_context_input_tokens + int(over)
    elif kind == "context":
        # The price step is normally reached first. Lower only the existing input
        # ceiling in this test so its independent protection is observable.
        monkeypatch.setattr(B, "MODEL_MAX_INPUT_TOKENS", 250_000)
        tokens = B.MODEL_MAX_INPUT_TOKENS + int(over)
    elif kind == "images":
        images = B.PAYLOAD_LIMIT_IMAGES + int(over)
    else:
        body_bytes = B.PAYLOAD_LIMIT_BYTES + int(over)
    if over:
        with pytest.raises(N.RenewalNeeded):
            obj.check({"input": []}, body_bytes, images, tokens)
        assert obj.finish_review_deferrals == []
    else:
        obj.check({"input": []}, body_bytes, images, tokens)
        assert len(obj.finish_review_deferrals) == 1


def test_unknown_token_count_does_not_waive_soft_renewal(tmp_path):
    obj, _, _ = candidate(tmp_path)
    with pytest.raises(N.RenewalNeeded):
        obj.check({"input": []}, B.PAYLOAD_LIMIT_BYTES * 4 // 5 + 1, 2, None)
    assert obj.finish_review_deferrals == []


def test_no_pending_finish_preserves_soft_threshold_and_renewal(tmp_path):
    obj, _, events = candidate(tmp_path, pending=False)
    threshold = obj.guard.tariff.long_context_input_tokens * 4 // 5
    obj.check({"input": []}, 1_000, 2, threshold)
    with pytest.raises(N.RenewalNeeded):
        obj.check({"input": []}, 1_000, 2, threshold + 1)
    assert obj.finish_review_deferrals == events == []


def test_review_opportunity_is_used_even_before_soft_pressure(tmp_path):
    obj, _, _ = candidate(tmp_path)
    obj.check({"input": []}, 1_000, 2, 100)
    with pytest.raises(N.RenewalNeeded):
        obj.check({"input": []}, 1_000, 2, REPLAY_TOKENS)
    assert obj.finish_review_deferrals == []


def test_new_review_identity_gets_grace_after_an_intervening_request(tmp_path):
    obj, state, _ = candidate(tmp_path)
    obj.check({"input": []}, 1_000, 2, REPLAY_TOKENS)
    obj.check({"input": []}, 1_000, 2, 100)
    state["finish_review"]["call_id"] = "new-first"
    obj.check({"input": []}, 1_000, 2, REPLAY_TOKENS)
    with pytest.raises(N.RenewalNeeded):
        obj.check({"input": []}, 1_000, 2, REPLAY_TOKENS)
    assert [row["review_call_id"] for row in obj.finish_review_deferrals] == ["first", "new-first"]


class FinishRunnerScene(FakeScene):
    """Use the production finish tools with only renderer/session work replaced."""

    def __init__(self, tool_scene):
        super().__init__()
        self.tool_scene = tool_scene
        self.tool_scene._state_lock = threading.RLock()
        self.tool_scene.save = lambda: None

    def renewal_state(self):
        review = getattr(self.tool_scene, "finish_review", None)
        return {"head_checkpoint": self.tool_scene.s.head["id"], "best_checkpoint": None,
                "finish_review": {"status": "pending" if review else "not_pending", **(review or {})}}

    def renewal_started(self):
        return JobScene.renewal_started(self.tool_scene)


def scripted_finish(tmp_path, monkeypatch, review_scene, *, next_action="finish", hard=False):
    # Scheduling is exercised on the new production profile. This proof supplies
    # no actionable appearance faults; the differences suite covers those.
    review_scene.instruction_profile = "decision-20261021"
    def reviewed(call="first"):
        args = final_args(call)
        args["per_photo"] = [{"ref": row["ref"], "differences": "none"} for row in args["per_photo"]]
        return args
    runner_scene = FinishRunnerScene(review_scene)
    tools = [tool for tool in T.build_tools(review_scene) if tool.name in {"finish", "scene_summary"}]
    settings = L.RunSettings(effort="max", max_turns=10, max_output_tokens=100,
                             finish_tool="finish", settled_stop_usd="20")
    with monkeypatch.context() as patch:
        patch.setattr(loop_support, "tool_registry_sha256", lambda tools: L.tool_registry_sha256(tools, settings))
        job, sha = make_job(tmp_path, tools=tools, settings=settings, cap="20")
    replies = [R.tool_call("finish", {"summary": "Inspect every photo"}, call_id="first")]
    if not hard:
        if next_action == "refused":
            replies.append(R.tool_call("finish", {**reviewed(), "per_photo": []}, call_id="second"))
        elif next_action == "unrelated":
            replies.append(R.tool_call("scene_summary", call_id="unrelated"))
        elif next_action == "retry_first":
            replies.append(R.tool_call("finish", {"summary": "Get review again"}, call_id="second"))
        else:
            replies.append(R.tool_call("finish", reviewed(), call_id="second"))
    if hard or next_action != "finish":
        replies.extend([R.message("Current head checked; resume the pending photo review."),
                        R.tool_call("finish", {"summary": "Inspect fresh evidence"}, call_id="fresh"),
                        R.tool_call("finish", reviewed("fresh"), call_id="complete")])
    def count(body):
        old_evidence = any(item.get("type") == "function_call_output" and item.get("call_id") == "first"
                           for item in body.get("input", []))
        return (B.Tariff.frozen().long_context_input_tokens + 1 if hard else REPLAY_TOKENS) if old_evidence else 100
    transport = R.ScriptedOpenAI(replies, count_tokens=count)
    result = asyncio.run(L.run_astra(
        spec_for(job, RUN, sha, tools, transport, settings=settings, requested_cap_usd="20"),
        scene=runner_scene))
    assert result["status"] == "completed", result
    assert transport.violations == []
    assert review_scene.finish["status"] == "accepted"
    return job, transport, result


def test_real_finish_review_is_submitted_in_same_conversation_under_soft_pressure(
        tmp_path, monkeypatch, review_scene):
    job, transport, result = scripted_finish(tmp_path, monkeypatch, review_scene)
    assert result["requests"] == len(transport.creates) == 2
    assert result["renewals"] == []
    assert review_scene.finish["review"]["review_call_id"] == "first"
    assert any(item.get("call_id") == "first" and item.get("type") == "function_call_output"
               for item in transport.creates[1]["input"])
    assert [row["input_tokens"] for row in ledger_rows(job, RUN)] == [100, REPLAY_TOKENS]
    receipts = json.loads((job / "runs" / RUN / "renewals.json").read_text(encoding="utf-8"))
    assert len(receipts["finish_review_deferrals"]) == 1
    assert receipts["finish_review_deferrals"][0]["review_call_id"] == "first"


@pytest.mark.parametrize("next_action", ["refused", "unrelated", "retry_first"])
def test_real_refused_or_missing_second_finish_renews_on_the_following_request(
        tmp_path, monkeypatch, review_scene, next_action):
    job, transport, result = scripted_finish(tmp_path, monkeypatch, review_scene, next_action=next_action)
    assert result["requests"] == len(transport.creates) == 5
    assert len(result["renewals"]) == 1
    assert result["renewals"][0]["summary_request_sequence"] == 3
    assert result["renewals"][0]["host_state"]["finish_review_renewal"] == {
        "finish_review_restarted": True,
        "previous_review_call_id": "second" if next_action == "retry_first" else "first"}
    assert transport.creates[2]["input"][-1]["content"] == N.summary_request("legacy-20261021")
    assert review_scene.finish["review"]["review_call_id"] == "fresh"
    receipts = json.loads((job / "runs" / RUN / "renewals.json").read_text(encoding="utf-8"))
    assert len(receipts["finish_review_deferrals"]) == 1
    if next_action == "refused":
        assert review_scene.calls[1][-1]["status"] == "refused"
        assert review_scene.calls[1][-1]["code"] == "E_FINISH_REVIEW"
    elif next_action == "retry_first":
        assert review_scene.calls[1][-1]["status"] == "review_required"


def test_real_pending_finish_still_renews_above_hard_price_step(tmp_path, monkeypatch, review_scene):
    _, transport, result = scripted_finish(tmp_path, monkeypatch, review_scene, hard=True)
    assert result["requests"] == len(transport.creates) == 4
    assert len(result["renewals"]) == 1
    assert result["renewals"][0]["summary_request_sequence"] == 2
    assert result["renewals"][0]["host_state"]["finish_review_renewal"]["previous_review_call_id"] == "first"
    assert transport.creates[1]["input"][-1]["content"] == N.summary_request("legacy-20261021")
    assert review_scene.finish["review"]["review_call_id"] == "fresh"
