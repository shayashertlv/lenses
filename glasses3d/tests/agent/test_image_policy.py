"""Assembly M2 image policy on the real SDK and scripted transport; no Blender or network."""
import asyncio
import copy
from dataclasses import replace
import hashlib
import json

from agents import function_tool
from agents.run_config import CallModelData, ModelInputData
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
import pytest

from glasses3d.agent import approvals as A, loop as L, replay as R
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.receipts import BlobStore
from glasses3d.tests.agent.loop_support import BRIEF, FakeScene, data_url, photos, png_bytes, tool_instructions
from glasses3d.tests.agent.support import OFFLINE_KEY


SETTINGS = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=12, finish_tool="finish",
                         context_guard_tokens=150_000, context_guard_every_requests=3)


class PolicyScene(FakeScene):
    def __init__(self, root):
        super().__init__()
        self.policy = JobScene(root, job={})
        self.asked = []

    def context_image_stubs(self, images):
        self.asked.append(copy.deepcopy(images))
        return self.policy.context_image_stubs(images)


def record(cid, view, checkpoint, seed, kind="compare_to_photos", **extra):
    return {"call_id": cid, "view": view, "checkpoint": checkpoint, "kind": kind,
            "sha256": hashlib.sha256(png_bytes(seed=seed)).hexdigest(), "output_index": 2, **extra}


def occurrence(rec):
    return {"call_id": rec["call_id"], "part_index": rec["output_index"], "sha256": rec["sha256"]}


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
                             estimate={"expected_usd": "1"}, suggested_cap_usd="20")
    A.write_approval(job / "runs" / run_id, max_usd="20", owner_words="Offline fixture only; no paid call.")
    transport = R.ScriptedOpenAI(replies, id_start=id_start)
    spec = L.RunSpec(job_dir=job, run_id=run_id, instructions=instructions, tools=tools, brief=BRIEF,
                     photos=photos(2), settings=settings, resume=resume, current_request_sha256=A.request_sha256(request),
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
    return {image["call_id"] for image in L.tool_output_image_occurrences(body["input"])}


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


def test_repeated_epochs_before_count_keep_originals_and_new_cached_occurrences(tmp_path):
    scene = PolicyScene(tmp_path / "policy")
    # call4 repeats call1's exact bytes after call1 was stubbed at request4.
    replies = [look("front", "c1", 1), look("side", "c2", 2), look("front", "c3", 3),
               look("front", "c4", 1), look("side", "c5", 5), look("front", "c6", 6),
               look("side", "c7", 7), look("front", "c8", 8), look("side", "c9", 9), R.tool_call("finish")]
    job, transport, result = run(tmp_path, replies, scene)
    assert [epoch["at_request"] for epoch in result["context_guard"]["epochs"]] == [1, 4, 7, 10]
    assert all(epoch["trigger"] == "request_cadence" for epoch in result["context_guard"]["epochs"])
    assert [len(R.input_images(body)) for body in transport.creates] == [2, 3, 4, 4, 5, 6, 4, 5, 6, 4]
    assert "call_0001" not in image_calls(transport.creates[4]) and "call_0004" in image_calls(transport.creates[4])
    assert image_calls(transport.creates[-1]) == {"call_0008", "call_0009"}
    for counted, sent in zip(transport.counts, transport.creates):
        assert counted["input"] == sent["input"]  # policy acts before the free count, which prices the sent input
        user_images = [part["image_url"] for item in sent["input"] if item.get("role") == "user"
                       and isinstance(item.get("content"), list) for part in item["content"] if part["type"] == "input_image"]
        assert user_images == [photo.image_url for photo in photos(2)]
    persisted = history(job)
    assert len(L.tool_output_image_occurrences(persisted)) == 9
    assert "superseded" not in json.dumps(persisted)  # only outgoing payloads are pruned, never durable history


def test_resume_prunes_immediately_and_restores_newly_selected_best_images(tmp_path):
    scene = PolicyScene(tmp_path / "policy")
    job, first, _ = run(tmp_path, [look("front", "c1", 1), look("front", "c2", 2),
                                  look("front", "c3", 3), R.tool_call("finish")], scene)
    assert image_calls(first.creates[-1]) == {"call_0003"}
    before = history(job)
    scene.policy.best_checkpoint = "c1"
    scene.policy.save()
    resumed = PolicyScene(tmp_path / "policy")
    assert resumed.policy.best_checkpoint == "c1"
    _job, second, result = run(tmp_path, [R.tool_call("finish")], resumed, run_id="images-2", resume=True, id_start=101)
    assert result["context_guard"]["epochs"][0]["at_request"] == 1
    assert image_calls(second.creates[0]) == {"call_0001", "call_0003"}
    assert len(R.input_images(second.creates[0])) == 4
    assert second.counts[0]["input"] == second.creates[0]["input"]
    after = history(job)
    assert after[:len(before)] == before


def test_retention_semantics_and_unseen_output_cannot_supersede_a_seen_image(tmp_path):
    scene = JobScene(tmp_path, job={})
    rows = [record("photo", "front", "source", 0, "read_image", context_role="reference"),
            record("old-front", "front", "c1", 1, path="work/tool_images/old-front.png"), record("side", "side", "c1", 2),
            record("new-front", "front", "c2", 3),
            record("old-render", "front", "c1", 4, "render_views"), record("new-render", "front", "c2", 5, "render_views"),
            record("old-pick", "front", "c1", 6, "photo_read"), record("current-pick", "front", "c2", 7, "photo_read"),
            record("old-detail", "front", "c1", 8, "read_image", context_role="detail"),
            record("new-detail", "front", "c2", 9, "read_image", context_role="detail"),
            record("unknown", "front", "c0", 10, "unknown")]
    scene.images = rows + [record("not-delivered", "side", "c3", 20)]
    present = [occurrence(row) for row in rows]
    stubs = scene.context_image_stubs(present)
    assert {key[0] for key in stubs} == {"old-front", "old-render", "old-pick", "old-detail"}
    assert "checkpoint c1, view front" in stubs[L.image_key(present[1])]
    assert "; path work/tool_images/old-front.png]" in stubs[L.image_key(present[1])]
    scene.best_checkpoint = "c1"
    assert scene.context_image_stubs(present) == {}
    scene.best_checkpoint = "c2"
    assert {key[0] for key in scene.context_image_stubs(present)} == {"old-front", "old-render", "old-pick", "old-detail"}


def test_same_bytes_in_one_output_have_independent_view_occurrences(tmp_path):
    scene = JobScene(tmp_path, job={})
    scene.images = [record("same-call", "front", "c1", 1),
                    record("same-call", "side", "c1", 1, output_index=4), record("new", "front", "c2", 2)]
    stubs = scene.context_image_stubs([occurrence(row) for row in scene.images])
    assert set(stubs) == {("same-call", 2, scene.images[0]["sha256"])}


def test_guard_does_not_mutate_inputs_and_can_restore_selected_images_at_later_epoch():
    url = data_url(png_bytes(seed=1))
    items = [{"role": "user", "content": [{"type": "input_image", "image_url": url}]},
             {"type": "function_call_output", "call_id": "old", "output": [{"type": "input_image", "image_url": url}]},
             {"type": "function_call_output", "call_id": "new", "output": [{"type": "input_image", "image_url": url}]}]
    original = copy.deepcopy(items)
    old_key = L.image_key(L.tool_output_image_occurrences(items)[0])
    choices = {old_key: "superseded"}
    guard = L.ContextGuard(150_000, counted=lambda: None, choose=lambda _: {}, every_requests=3,
                           choose_images=lambda _: choices)
    data = CallModelData(model_data=ModelInputData(input=items, instructions="i"), agent=None, context=None)
    for _ in range(3):
        got = guard(data)
        assert got.input[1]["output"] == [{"type": "input_text", "text": "superseded"}]
        assert got.input[2] == items[2]
    choices.clear()
    assert guard(data).input == original and guard.replaced == 0
    assert items == original


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
