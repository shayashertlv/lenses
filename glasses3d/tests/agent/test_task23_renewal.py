"""Summary-only image omission preserves history and intact latest evidence, at $0."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText

from glasses3d.agent import loop as L, renewal as N, replay as R
from glasses3d.tests.agent import loop_support as S
from glasses3d.tests.agent.test_task21_renewal import MemorySession


def inputs():
    old = [{"role": "user", "content": [
        {"type": "input_text", "text": "Owner photo"},
        {"type": "input_image", "image_url": S.data_url(S.png_bytes(seed=1)), "detail": "high"}]}]
    latest = {"type": "function_call_output", "call_id": "new-look", "output": [
        {"type": "input_text", "text": "Exact current result text: lens_colour_gap and remaining differences."},
        {"type": "input_text", "text": "Image 1: worn front; checkpoint c0018"},
        {"type": "input_image", "image_url": S.data_url(S.png_bytes(seed=2)), "detail": "high"},
        {"type": "input_text", "text": "Image 2: photo pair; checkpoint c0018"},
        {"type": "input_image", "image_url": S.data_url(S.png_bytes(seed=3)), "detail": "high"}]}
    return old, latest


def test_only_new_images_are_replaced_and_original_text_and_prefix_stay_exact():
    old, latest = inputs()
    original = copy.deepcopy([*old, latest])
    transformed, carry, omitted = N.summary_input(original, (len(old), N.input_digest(old)))
    assert original == [*old, latest]
    assert transformed[:len(old)] == old
    assert carry == [latest]
    assert len(R.input_images({"input": transformed})) == 1
    assert [p for p in transformed[-1]["output"] if p["type"] == "input_text"][:-1] == [
        p for p in latest["output"] if p["type"] == "input_text"]
    assert [row["label"] for row in omitted] == [latest["output"][1]["text"], latest["output"][3]["text"]]
    assert "do not claim to have inspected" in transformed[-1]["output"][-1]["text"]


@pytest.mark.parametrize("prefix", [None, (1, "changed"), (100, "absent")])
def test_unknown_or_changed_prefix_never_drops_images(prefix):
    old, latest = inputs()
    assert N.summary_input([*old, latest], prefix) == ([*old, latest], [], [])


def test_already_sent_latest_images_stay_in_the_cached_prefix():
    old, latest = inputs()
    original = [*old, latest]
    assert N.summary_input(original, (len(original), N.input_digest(original))) == (original, [], [])


@pytest.mark.parametrize("profile", ["decision-20261021", "legacy-20261021"])
def test_scripted_sdk_summary_and_fresh_conversation_keep_exact_outputs(tmp_path, monkeypatch, profile):
    photo = S.data_url(S.png_bytes(seed=2))
    scene = S.FakeScene()
    scene.renewal_evidence = lambda: []

    @function_tool
    async def grow():
        """Return the current comparison."""
        return [ToolOutputText(text="NEW_LARGE_LOOK exact text"),
                ToolOutputText(text="Image 1: native worn front"),
                ToolOutputImage(image_url=photo, detail="high")]

    @function_tool
    async def record_difference():
        """Decision-profile capability marker for the isolated loop proof."""
        return "unused"

    @function_tool
    async def finish():
        """Finish the scripted test."""
        return json.dumps({"status": "accepted"})

    tools = [grow, record_difference, finish]
    settings = L.RunSettings(effort="max", max_turns=5, max_output_tokens=100,
                             finish_tool="finish", settled_stop_usd="20", instruction_profile=profile)
    with monkeypatch.context() as patch:
        patch.setattr(S, "tool_registry_sha256", lambda tools: L.tool_registry_sha256(tools, settings))
        job, sha = S.make_job(tmp_path, tools=tools, settings=settings, cap="20")
    transport = R.ScriptedOpenAI([R.tool_call("grow", call_id="large-look"),
                                 R.message("Summarized text; latest images await inspection."),
                                 R.tool_call("finish")],
        count_tokens=lambda body: 220000 if "NEW_LARGE_LOOK" in json.dumps(body) else 100)
    result = asyncio.run(L.run_astra(S.spec_for(job, "20261007T120000-run001", sha, tools, transport,
                                             settings=settings, requested_cap_usd="20"), scene=scene))
    assert result["status"] == "completed", result
    assert transport.violations == []
    previous, summary, fresh = transport.creates
    assert summary["input"][:len(previous["input"])] == previous["input"]
    receipt = result["renewals"][0]
    if profile == "decision-20261021":
        assert len(R.input_images(summary)) == 2  # both owner photos, newest look absent
        assert len(R.input_images(fresh)) == 3
        exact = next(item["output"] for item in transport.counts[1]["input"]
                     if item.get("type") == "function_call_output")
        content = fresh["input"][0]["content"]
        assert content[-len(exact):] == exact
        assert receipt["latest_result_call_ids"] == ["large-look"]
        assert len(receipt["summary_omitted_images"]) == 1
    else:
        assert len(R.input_images(summary)) == 3
        assert "NEW_LARGE_LOOK" not in json.dumps(fresh)
        assert receipt.get("summary_omitted_images", []) == []


def test_saved_request_73_replay_preserves_prefix_text_and_complete_new_result(tmp_path):
    from glasses3d.tests.proofs.task23_renewal import replay
    result = replay(tmp_path)
    if result is None:
        pytest.skip("saved paid run unavailable")
    assert result["summary_images_before"] == 98
    assert result["summary_images_after"] == 62
    assert result["omitted_images"] == 36
    assert result["removed_image_tokens_estimate"] == 83381
    assert result["saving_micro_usd_estimate"] > 2_400_000
    assert result["exact_prefix"] and result["exact_result_text"] and result["exact_latest_carry"]


def test_latest_result_with_reused_images_carries_referenced_pixels_too(tmp_path):
    old, latest = inputs()
    reused = latest["output"].pop()
    latest["output"].append({"type": "input_text", "text": "Same image already delivered in prior-look"})
    old.append({"role": "user", "content": [reused]})
    records = []
    for seed in (2, 3):
        raw = S.png_bytes(seed=seed)
        path = tmp_path / f"panel-{seed}.png"
        path.write_bytes(raw)
        records.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                        "call_id": latest["call_id"], "checkpoint": "c0018", "kind": "look_composed", "view": str(seed)})
    session = MemorySession()
    session.items = copy.deepcopy([*old, latest])
    scene = SimpleNamespace(renewal_evidence=lambda: records, renewal_state=lambda: {},
                            renewal_started=lambda: {"finish_review_restarted": False})
    async def response(**request):
        return SimpleNamespace(id="summary", usage=None, status="completed", output=[], output_text="Summary")
    async def hook(*args): pass
    guard = SimpleNamespace(request_pressure=None, renewal_summary=False,
                            summary=lambda: {"requests": 2, "settled_usd": "1", "settled_stop_usd": "20"})
    log = SimpleNamespace(run_id="test", interactions=0, tool_calls=[],
                          event=lambda *a, **kw: None, note=lambda *a, **kw: None)
    spec = SimpleNamespace(owner_notes=[], brief="Brief", photos=[], tools=[SimpleNamespace(name="record_difference")],
                           settings=L.RunSettings())
    renewal = N.ConversationRenewal(spec=spec, scene=scene, session=session, log=log, guard=guard,
        client=SimpleNamespace(responses=SimpleNamespace(create=response)), directory=tmp_path / "renewal")
    renewal._previous_input = (len(old), N.input_digest(old))
    asyncio.run(renewal.renew(N.RenewalNeeded({"input": session.items}, {}, []),
        agent=SimpleNamespace(instructions="same"), context=None,
        hooks=SimpleNamespace(on_llm_start=hook, on_llm_end=hook)))
    fresh = {"input": session.items}
    assert sorted(R.input_images(fresh)) == sorted([S.data_url(S.png_bytes(seed=2)), reused["image_url"]])
    assert session.items[0]["content"][-len(latest["output"]):] == latest["output"]
    assert renewal.records[0]["evidence"] == [{**record, "detail": "high"} for record in records]
