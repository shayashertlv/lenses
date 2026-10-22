"""Exact delivered bytes, current-conversation membership and explicit rereads."""
import copy
import asyncio
import json
from types import SimpleNamespace

from agents.tool import ToolOutputImage
from agents import function_tool
from agents.tool_context import ToolContext
from PIL import Image

from glasses3d.agent import renewal as N, tools as T
from glasses3d.agent.pricing import Tariff
from glasses3d.agent.instruction_profiles import DEFAULT_PROFILE, LEGACY_PROFILE
from glasses3d.tests.agent.test_job_tools import scene, invoke, record


def admitted(scene, items, profile=DEFAULT_PROFILE):
    scene.instruction_profile = profile
    renewal = N.ConversationRenewal(
        spec=SimpleNamespace(settings=SimpleNamespace(instruction_profile=profile),
                             tools=[], owner_notes=()),
        scene=scene, session=SimpleNamespace(session_id="segment"), log=None,
        guard=SimpleNamespace(tariff=Tariff.frozen()), client=None, directory=scene.root)
    scene.renewal_state = lambda: {}
    renewal.check({"input": items}, 1000, 2, 1000)


def wire(output, cid="earlier"):
    return [{"type": "function_call_output", "call_id": cid, "output": [
        {"type": "input_image", "image_url": part.image_url, "detail": part.detail}
        if isinstance(part, ToolOutputImage) else {"type": "input_text", "text": part.text}
        for part in output]}]


def make_image(scene, name, colour):
    path = scene.root / "work" / name
    Image.new("RGB", (160, 96), colour).save(path)
    return str(path)


def emitted(scene, cid, paths, name="render_views"):
    return T._output(scene, cid, name, {"status": "ok", "image_delivery": "separate",
        "images": [{"path": path, "view": f"panel {index}"} for index, path in enumerate(paths)]})


def test_renewed_context_duplicates_are_references_and_new_bytes_stay_exact(scene):
    old = make_image(scene, "old.png", "navy")
    new = make_image(scene, "new.png", "orange")
    first = emitted(scene, "original", [old])
    context = wire(first, "original")
    unchanged = copy.deepcopy(context)
    scene._emitted_images = {}  # renewal clears this historical live-scene cache
    admitted(scene, context)
    result = emitted(scene, "next", [old, new])
    images = [part for part in result if isinstance(part, ToolOutputImage)]
    assert len(images) == 1
    rows = record(result)["images"]
    assert rows[0]["reused_from_call"] == "original"
    assert "still above" in result[1].text
    assert rows[1]["output_index"] is not None
    assert images[0].image_url == T._image(scene.root, new, max_side=T.LOOK_IMAGE_SIDE)[1]
    assert context == unchanged


def test_dropped_at_renewal_is_sent_again_even_with_stale_scene_cache(scene):
    path = make_image(scene, "old.png", "navy")
    first = emitted(scene, "original", [path])
    admitted(scene, wire(first, "original"))
    admitted(scene, [{"role": "user", "content": "Fresh segment without old evidence"}])
    again = emitted(scene, "again", [path])
    assert sum(isinstance(part, ToolOutputImage) for part in again) == 1


def test_explicit_read_image_always_redelivers_in_decision_profile(scene):
    scene.instruction_profile = DEFAULT_PROFILE
    make_image(scene, "reread.png", "green")
    first = invoke(scene, "read_image", {"path": "work/reread.png"}, cid="first")
    second = invoke(scene, "read_image", {"path": "work/reread.png"}, cid="second")
    assert [part.image_url for part in first if isinstance(part, ToolOutputImage)] == [
        part.image_url for part in second if isinstance(part, ToolOutputImage)]
    assert sum(isinstance(part, ToolOutputImage) for part in second) == 1


def test_one_byte_change_is_never_a_duplicate(scene):
    path = make_image(scene, "change.png", "navy")
    first = emitted(scene, "original", [path])
    admitted(scene, wire(first, "original"))
    with Image.open(path) as opened:
        changed = opened.copy()
    changed.putpixel((2, 3), (1, 2, 3))
    changed.save(path)
    again = emitted(scene, "changed", [path])
    assert sum(isinstance(part, ToolOutputImage) for part in again) == 1
    assert record(first)["images"][0]["sha256"] != record(again)["images"][0]["sha256"]


def test_original_owner_photo_bytes_are_already_in_context(scene):
    path = make_image(scene, "owner.png", "navy")
    meta, url = T._image(scene.root, path, max_side=T.LOOK_IMAGE_SIDE)
    context = [{"role": "user", "content": [
        {"type": "input_text", "text": "Owner reference: front"},
        {"type": "input_image", "image_url": url, "detail": "high"}]}]
    admitted(scene, context)
    output = emitted(scene, "repeated", [path])
    assert not any(isinstance(part, ToolOutputImage) for part in output)
    assert meta["sha256"] in scene._segment_images


def test_legacy_does_not_seed_or_change_previous_transmitted_output(scene):
    scene.instruction_profile = LEGACY_PROFILE
    path = make_image(scene, "legacy.png", "navy")
    first = emitted(scene, "first", [path], name="read_image")
    second = emitted(scene, "second", [path], name="read_image")
    assert not any(isinstance(part, ToolOutputImage) for part in second)
    previous = copy.deepcopy(second)
    scene._emitted_images = {record(first)["images"][0]["sha256"]: "first"}
    admitted(scene, [], LEGACY_PROFILE)
    assert emitted(scene, "second", [path], name="read_image") == previous
    scene._emitted_images = {}
    admitted(scene, wire(first, "first"), LEGACY_PROFILE)
    assert sum(isinstance(part, ToolOutputImage) for part in emitted(scene, "next", [path])) == 1


def test_scripted_sdk_renewal_reattaches_once_then_references_carried_bytes(tmp_path, monkeypatch):
    from glasses3d.agent import loop as L, replay as R
    from glasses3d.tests.agent import loop_support as S

    scene = S.FakeScene()
    scene.root = tmp_path
    (tmp_path / "work").mkdir()
    scene.s = SimpleNamespace(head={"id": "c0001", "fingerprint": "f" * 64})
    scene.instruction_profile = DEFAULT_PROFILE
    scene.images = []
    scene.record_images = lambda cid, result: scene.images.extend(copy.deepcopy(result["images"]))
    scene.renewal_evidence = lambda: []
    def renewed():
        scene._emitted_images = {}
        scene._segment_images = {}
        return {"finish_review_restarted": False}
    scene.renewal_started = renewed
    old = make_image(scene, "retained.png", "navy")
    new = make_image(scene, "added.png", "orange")
    results = []

    @function_tool
    async def emit(ctx: ToolContext, fresh: bool):
        """Return native images in this scripted offline proof."""
        result = emitted(scene, ctx.tool_call_id, [old, new] if fresh else [old])
        results.append(copy.deepcopy(result))
        return result

    @function_tool
    async def record_difference():
        """Decision-profile marker; never called in this proof."""
        return "unused"

    @function_tool
    async def finish():
        """Finish the offline transport proof."""
        return json.dumps({"status": "accepted"})

    tools = [emit, record_difference, finish]
    settings = L.RunSettings(effort="max", max_turns=5, max_output_tokens=100,
                            finish_tool="finish", settled_stop_usd="20", instruction_profile=DEFAULT_PROFILE)
    with monkeypatch.context() as patch:
        patch.setattr(S, "tool_registry_sha256", lambda tools: L.tool_registry_sha256(tools, settings))
        job, sha = S.make_job(tmp_path, tools=tools, settings=settings, cap="20")

    def count(body):
        # Trigger exactly one renewal after the first result, as a large saved look would.
        return 220000 if any(item.get("type") == "function_call_output" and
                            item.get("call_id") == "first" for item in body.get("input", [])) else 1000

    transport = R.ScriptedOpenAI([
        R.tool_call("emit", {"fresh": False}, call_id="first"), R.message("Carry current native evidence."),
        R.tool_call("emit", {"fresh": True}, call_id="mixed"), R.tool_call("finish")], count_tokens=count)
    run = asyncio.run(L.run_astra(S.spec_for(job, "20261007T120000-run001", sha, tools, transport,
                    settings=settings, requested_cap_usd="20"), scene=scene))
    assert run["status"] == "completed", run
    assert len(run["renewals"]) == 1 and not transport.violations
    assert [sum(isinstance(part, ToolOutputImage) for part in output) for output in results] == [1, 1]
    assert record(results[1])["images"][0]["reused_from_call"] == "first"
    first_request, summary, fresh_request, last_request = transport.creates
    assert summary["input"][:len(first_request["input"])] == first_request["input"]
    # Two owner photos plus the same exact carried image; then one genuinely new image.
    assert len(R.input_images(fresh_request)) == 3
    assert len(R.input_images(last_request)) == 4
    assert last_request["input"][:len(fresh_request["input"])] == fresh_request["input"]
