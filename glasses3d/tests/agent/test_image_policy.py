"""Assembly M2 image policy on the real SDK and scripted transport; no Blender or network."""
import asyncio
import copy
from dataclasses import replace
import hashlib
import json

from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
import pytest

from glasses3d.agent import approvals as A, loop as L, replay as R
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.receipts import BlobStore
from glasses3d.tests.agent.loop_support import BRIEF, FakeScene, data_url, photos, png_bytes, tool_instructions
from glasses3d.tests.agent.support import OFFLINE_KEY
from glasses3d.tests.agent.image_history import tool_output_image_occurrences


SETTINGS = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=12, finish_tool="finish",
                         context_guard_tokens=150_000, context_guard_every_requests=3)


class PolicyScene(FakeScene):
    def __init__(self, root):
        super().__init__()
        self.policy = JobScene(root, job={})
        self.asked = []

    def context_image_stubs(self, images):
        self.asked.append(copy.deepcopy(images))
        raise AssertionError("retired history rewriting must never be called")


def record(cid, view, checkpoint, seed, kind="compare_to_photos", **extra):
    return {"call_id": cid, "view": view, "checkpoint": checkpoint, "kind": kind,
            "sha256": hashlib.sha256(png_bytes(seed=seed)).hexdigest(), "output_index": 2, **extra}



def make_tools(scene):
    @function_tool
    async def look(ctx: ToolContext, view: str, checkpoint: str, seed: int) -> list:
        """Return a comparison image of this checkpoint at the named photo view."""
        scene.policy.images.append(record(ctx.tool_call_id, view, checkpoint, seed))
        return [ToolOutputText(text='{"status":"ok"}'),
                ToolOutputText(text=f"Image: call {ctx.tool_call_id}; checkpoint {checkpoint}; view {view}."),
                ToolOutputImage(image_url=data_url(png_bytes(seed=seed)), detail="high")]

    @function_tool
    async def finish() -> list:
        """End this offline image-policy proof."""
        return [ToolOutputText(text='{"status":"accepted"}')]

    return [look, finish]


def run(root, replies, scene, *, run_id="images-1", resume=False, id_start=1, settings=SETTINGS):
    cutoff = settings.settled_stop_usd or "20"
    settings = replace(settings, settled_stop_usd=cutoff)
    job = root / "job"
    (job / "inputs/images").mkdir(parents=True, exist_ok=True)
    (job / "job.json").write_text('{"schema":"g3d.job/1","name":"Image policy proof"}')
    for i, _photo in enumerate(photos(2), 1):
        (job / f"inputs/images/reference-0{i}.png").write_bytes(png_bytes(seed=i))
    tools = make_tools(scene)
    instructions = tool_instructions(tools)
    request = A.build_request(job, instructions_sha256=L.instructions_sha256(instructions),
                              tool_registry_sha256=L.tool_registry_sha256(tools, settings),
                              settings=settings.approval_settings(), run_inputs=A.run_input_binding(job, BRIEF, photos(2)))
    A.write_approval_request(job / "runs" / run_id, job_id=job.name, request=request,
                             estimate={"expected_usd": "1"}, suggested_cap_usd=cutoff)
    A.write_approval(job / "runs" / run_id, max_usd=cutoff, owner_words="Offline fixture only; no paid call.")
    transport = R.ScriptedOpenAI(replies, id_start=id_start)
    spec = L.RunSpec(job_dir=job, run_id=run_id, instructions=instructions, tools=tools, brief=BRIEF,
                     photos=photos(2), settings=settings, resume=resume, requested_cap_usd=cutoff,
                     current_request_sha256=A.request_sha256(request),
                     offline_handler=transport.handle, offline_api_key=OFFLINE_KEY, heartbeat_interval_s=.05,
                     contract=L.ToolContract(frozenset(t.name for t in tools)))
    result = asyncio.run(L.run_astra(spec, scene=scene))
    assert result["status"] == "completed", result
    assert not transport.violations and not transport.unexpected
    return job, transport, result


def history(job):
    session = L.BlobSession(L.SESSION_ID, job / "session/session.sqlite", blobs=BlobStore(job / "blobs"))
    try:
        return asyncio.run(session.get_items())
    finally:
        session.close()


def look(view, checkpoint, seed):
    return R.tool_call("look", {"view": view, "checkpoint": checkpoint, "seed": seed})


def image_calls(body):
    return {image["call_id"] for image in tool_output_image_occurrences(body["input"])}


def test_job_policy_keeps_every_previous_item_at_all_request_boundaries(tmp_path):
    from glasses3d.agent.job_run import settings_default
    scene = PolicyScene(tmp_path / "policy")
    replies = [look("front", f"c{i}", i) for i in range(1, 8)] + [R.tool_call("finish")]
    settings = replace(settings_default(), max_turns=12, max_output_tokens=4096, settled_stop_usd="19")
    _, transport, result = run(tmp_path, replies, scene, settings=settings)
    assert not scene.asked and not result.get("context_guard")
    for previous, current in zip(transport.creates, transport.creates[1:]):
        assert current["input"][:len(previous["input"])] == previous["input"]
    for counted, sent in zip(transport.counts, transport.creates):
        assert counted["input"] == sent["input"]
        assert [part["image_url"] for item in sent["input"] if item.get("role") == "user"
                and isinstance(item.get("content"), list) for part in item["content"]
                if part["type"] == "input_image"] == [photo.image_url for photo in photos(2)]


def test_legacy_epoch_settings_preserve_every_original_and_repeated_image_occurrence(tmp_path):
    scene = PolicyScene(tmp_path / "policy")
    # call4 repeats call1's exact bytes; both occurrences remain in every later request.
    replies = [look("front", "c1", 1), look("side", "c2", 2), look("front", "c3", 3),
               look("front", "c4", 1), look("side", "c5", 5), look("front", "c6", 6),
               look("side", "c7", 7), look("front", "c8", 8), look("side", "c9", 9), R.tool_call("finish")]
    job, transport, result = run(tmp_path, replies, scene)
    assert not scene.asked and result["context_guard"]["epochs"] == []
    assert result["context_guard"]["epoch"] is None and result["renewals"] == []
    assert [len(R.input_images(body)) for body in transport.creates] == list(range(2, 12))
    repeated = {row["call_id"]: row for row in tool_output_image_occurrences(transport.creates[4]["input"])}
    assert repeated["call_0001"]["sha256"] == repeated["call_0004"]["sha256"]
    assert image_calls(transport.creates[-1]) == {f"call_{i:04d}" for i in range(1, 10)}
    for previous, current in zip(transport.creates, transport.creates[1:]):
        assert current["input"][:len(previous["input"])] == previous["input"]
    for counted, sent in zip(transport.counts, transport.creates):
        assert counted["input"] == sent["input"]
        user_images = [part["image_url"] for item in sent["input"] if item.get("role") == "user"
                       and isinstance(item.get("content"), list) for part in item["content"] if part["type"] == "input_image"]
        assert user_images == [photo.image_url for photo in photos(2)]
    persisted = history(job)
    assert len(tool_output_image_occurrences(persisted)) == 9
    assert "superseded" not in json.dumps(persisted)


def test_resume_keeps_exact_history_and_every_image_after_selecting_a_different_best_checkpoint(tmp_path):
    scene = PolicyScene(tmp_path / "policy")
    job, first, _ = run(tmp_path, [look("front", "c1", 1), look("front", "c2", 2),
                                  look("front", "c3", 3), R.tool_call("finish")], scene)
    assert not scene.asked
    assert image_calls(first.creates[-1]) == {"call_0001", "call_0002", "call_0003"}
    assert [len(R.input_images(body)) for body in first.creates] == [2, 3, 4, 5]
    for previous, current in zip(first.creates, first.creates[1:]):
        assert current["input"][:len(previous["input"])] == previous["input"]
    before = history(job)
    scene.policy.best_checkpoint = "c1"
    scene.policy.save()
    resumed = PolicyScene(tmp_path / "policy")
    assert resumed.policy.best_checkpoint == "c1"
    _job, second, result = run(tmp_path, [R.tool_call("finish")], resumed, run_id="images-2", resume=True, id_start=101)
    assert not resumed.asked and result["context_guard"]["epochs"] == []
    assert result["context_guard"]["epoch"] is None and result["renewals"] == []
    assert image_calls(second.creates[0]) == {"call_0001", "call_0002", "call_0003"}
    assert len(R.input_images(second.creates[0])) == 5
    assert second.creates[0]["input"][:len(before)] == before
    assert all(photo.image_url in R.input_images(second.creates[0]) for photo in photos(2))
    assert second.counts[0]["input"] == second.creates[0]["input"]
    after = history(job)
    assert after[:len(before)] == before



def test_periodic_setting_is_bound_into_approval_and_registry():
    tools = make_tools(PolicyScene.__new__(PolicyScene))
    binding = SETTINGS.approval_settings()["context_guard"]
    assert binding == {"input_tokens": 150_000, "filter": L.CONTEXT_GUARD_PERIODIC, "every_requests": 3}
    assert L.tool_registry_sha256(tools, SETTINGS) != L.tool_registry_sha256(tools, replace(SETTINGS, context_guard_every_requests=4))
    assert L.tool_registry_sha256(tools, SETTINGS) != L.tool_registry_sha256(tools, replace(SETTINGS, context_guard_every_requests=None))


@pytest.mark.parametrize("cadence,tokens", [(0, 150_000), (-1, 150_000), (1.5, 150_000), (True, 150_000), (3, None)])
def test_invalid_cadence_is_refused_before_run(tmp_path, cadence, tokens):
    settings = replace(SETTINGS, context_guard_every_requests=cadence, context_guard_tokens=tokens)
    tools = make_tools(PolicyScene(tmp_path / "policy"))
    spec = L.RunSpec(job_dir=tmp_path, run_id="invalid", instructions=tool_instructions(tools), tools=tools, brief="b",
                     current_request_sha256="0" * 64, settings=settings,
                     contract=L.ToolContract(frozenset(t.name for t in tools)))
    with pytest.raises(L.RunRefused, match="context_guard_every_requests"):
        L._validate_spec(spec)
