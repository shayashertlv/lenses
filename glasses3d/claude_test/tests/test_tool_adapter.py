from __future__ import annotations

import asyncio
import base64
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from PIL import Image
import pytest

from glasses3d.agent.loop import RunSettings, StageRunFailed, fatal_tool_errors
from glasses3d.agent.tools import build_tools
from glasses3d.claude_test.images import FileCache, check_request
from glasses3d.claude_test.tool_adapter import (
    INSTRUCTION_REPLACEMENTS, SPECIALIST_REPLACEMENTS, ToolAdapter,
    claude_instructions, effective_registry_sha256, prepare_images, tool_definitions,
)


class Scene:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.s = SimpleNamespace(head={"id": "c1", "fingerprint": "fp1"})
        self._unseen = set()
        self._emitted_images = {}
        self.finish = self.finish_review = None
        self.rows = []
        self.images = []
        self.owner_edit = None

    def record_call(self, cid, name, before, after, result):
        self.rows.append({"call_id": cid, "name": name, "before": before, "after": after,
                          "result": result})

    def record_images(self, cid, result):
        self.images.extend(result.get("images", []))

    def journal_entries(self):
        return self.rows

    def mark_unseen(self, cid):
        self._unseen.add(cid)

    def save(self):
        pass


class Log:
    def __init__(self):
        self.calls = []

    async def on_tool_start(self, ctx, agent, tool):
        self.calls.append(("start", ctx.tool_call_id, tool.name))

    async def on_tool_end(self, ctx, agent, tool, result):
        self.calls.append(("end", ctx.tool_call_id, tool.name))

    def record_journal(self, cid, row):
        self.calls.append(("journal", cid, row))

    def event(self, kind, **values):
        self.calls.append((kind, values))


def make_adapter(tmp_path, tools, *, status=None, scene=None, uploader=None, specialist=False):
    scene = scene or Scene(tmp_path)
    status = status or SimpleNamespace(footer=lambda: "Settled $0 · cutoff $1 · left $1")
    uploaded = {}

    def upload(data, name):
        uploaded[name] = data
        return "file-" + hashlib.sha256(data).hexdigest()

    files = FileCache(tmp_path / "files.json", uploader or upload)
    adapter = ToolAdapter(tools, scene=scene, status=status, log=Log(), files=files,
                          image_manifest=tmp_path / "images.jsonl", specialist=specialist,
                          parent_settings=RunSettings())
    return adapter, uploaded


def block(name, inputs=None, cid="toolu_1"):
    return {"type": "tool_use", "name": name, "id": cid, "input": inputs or {}}


def png(w=64, h=32):
    image = Image.new("RGB", (w, h))
    image.putdata([((x * 13) % 256, (x // w) % 256, x % 251) for x in range(w * h)])
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def image_output(data):
    return ToolOutputImage(image_url="data:image/png;base64," + base64.b64encode(data).decode())


def result_json(result):
    return json.loads(result["content"][0]["text"])


@pytest.mark.parametrize("specialist,pairs", [(False, INSTRUCTION_REPLACEMENTS),
                                            (True, SPECIALIST_REPLACEMENTS)])
def test_instructions_only_replace_approved_fragments(specialist, pairs):
    original = "prefix\n" + "\n".join(old for old, new in pairs) + "\nowner Astra remains\nsuffix"
    expected = "prefix\n" + "\n".join(new for old, new in pairs) + "\nowner Astra remains\nsuffix"
    assert claude_instructions(original, specialist=specialist) == expected
    with pytest.raises(ValueError, match="exactly one"):
        claude_instructions(original + pairs[0][0], specialist=specialist)
    with pytest.raises(ValueError, match="exactly one"):
        claude_instructions(original.replace(pairs[0][0], ""), specialist=specialist)


def test_real_instruction_fragments_match(tmp_path):
    from glasses3d.agent.instructions import build_instructions
    text = build_instructions(build_tools(Scene(tmp_path), RunSettings()))
    assert claude_instructions(text).startswith("You are Claude.")
    text = (Path(__file__).parents[2] / "agent/temple_lab/instructions.txt").read_text(encoding="utf-8")
    assert claude_instructions(text, specialist=True).startswith("You are Claude, the temple specialist.")


def test_registry_keeps_original_descriptors_without_strict(tmp_path):
    tools = build_tools(Scene(tmp_path), RunSettings())
    source = [(tool.name, tool.description, json.dumps(tool.params_json_schema, sort_keys=True),
               tool.on_invoke_tool, tool.timeout_seconds) for tool in tools]
    adapter, _ = make_adapter(tmp_path, tools)
    definitions = tool_definitions(tools)
    assert len(definitions) == 26
    assert all(set(row) == {"name", "description", "input_schema"} for row in definitions)
    assert definitions == adapter.definitions
    assert source == [(tool.name, tool.description, json.dumps(tool.params_json_schema, sort_keys=True),
                       tool.on_invoke_tool, tool.timeout_seconds) for tool in tools]
    for tool, row in zip(tools, definitions):
        assert row["name"] == tool.name
        assert row["description"] == tool.description
        assert row["input_schema"] == tool.params_json_schema
    assert effective_registry_sha256(definitions) == effective_registry_sha256(tool_definitions(tools))
    definitions[0]["input_schema"]["description"] = "different"
    assert definitions[0]["input_schema"] != tools[0].params_json_schema


@pytest.mark.parametrize("inputs", [{}, {"value": 1, "extra": True}, {"value": float("nan")},
                                    {"value": float("inf")}, ["not", "object"], '{"value":1}'])
def test_schema_and_nonfinite_rejected_before_executor(tmp_path, inputs):
    called = []

    @function_tool
    async def example(value: int = 1):
        called.append(value)
        return "ok"

    adapter, _ = make_adapter(tmp_path, [example])
    result = asyncio.run(adapter.invoke({"type": "tool_use", "id": "id", "name": "example",
                                         "input": inputs}))
    assert result["is_error"] is True
    assert result_json(result)["code"] == "E_ARGS"
    assert not called
    assert not adapter.log.calls


def test_valid_local_schema_keeps_original_domain_validation_and_order(tmp_path):
    calls = []

    @function_tool
    async def domain(ctx: ToolContext, value: int = 1):
        calls.append((ctx.tool_call_id, value))
        return [ToolOutputText(text='{"status":"refused","code":"E_PHYSICAL"}'),
                ToolOutputText(text="settled facts")]

    adapter, _ = make_adapter(tmp_path, [domain])
    result = asyncio.run(adapter.invoke(block("domain", {"value": 2})))
    assert calls == [("toolu_1", 2)]
    assert "is_error" not in result
    assert [row["text"] for row in result["content"]] == [
        '{"status":"refused","code":"E_PHYSICAL"}', "settled facts"]
    assert adapter.log.calls[:2] == [("start", "toolu_1", "domain"), ("end", "toolu_1", "domain")]


def test_duplicate_unknown_and_nonobject_inputs_never_mutate(tmp_path):
    calls = []

    @function_tool
    async def mutate():
        calls.append(1)
        return "ok"

    adapter, _ = make_adapter(tmp_path, [mutate])

    async def run():
        await adapter.invoke(block("mutate"))
        assert (await adapter.invoke(block("mutate")))["is_error"]
        assert (await adapter.invoke(block("missing", cid="next")))["is_error"]
    asyncio.run(run())
    assert calls == [1]


@pytest.mark.parametrize("call_id", ["", "../call", "spaces are invalid", "x" * 161, None])
def test_invalid_call_identifiers_never_execute(tmp_path, call_id):
    @function_tool
    async def mutate():
        pytest.fail("invalid call identifier must never execute")

    adapter, _ = make_adapter(tmp_path, [mutate])
    result = asyncio.run(adapter.invoke(block("mutate", cid=call_id)))
    assert result["is_error"] and result_json(result)["code"] == "E_ARGS"


def test_sdk_timeout_is_preserved(tmp_path):
    cancelled = []

    @function_tool(timeout=0.01)
    async def slow():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    adapter, _ = make_adapter(tmp_path, [slow])
    result = asyncio.run(adapter.invoke(block("slow")))
    assert result["is_error"]
    assert "timed out" in result["content"][0]["text"]
    assert cancelled == [True]


def test_fatal_error_callback_escapes(tmp_path):
    @function_tool(failure_error_function=fatal_tool_errors(StageRunFailed))
    async def fatal():
        raise StageRunFailed("child failed")

    adapter, _ = make_adapter(tmp_path, [fatal])
    with pytest.raises(StageRunFailed, match="child failed"):
        asyncio.run(adapter.invoke(block("fatal")))
    assert "toolu_1" in adapter.scene._unseen


def test_tiled_native_pixels_and_output_order_with_upload_dedup(tmp_path):
    data = png(2924, 864)
    adapter, uploaded = make_adapter(tmp_path, [])
    raw = [ToolOutputText(text='{"status":"ok"}'), ToolOutputText(text="photo/render comparison"),
           image_output(data), ToolOutputText(text="money")]
    content, records = prepare_images(raw, files=adapter.files, metadata={"call_id": "c1", "checkpoint": "head"})
    assert content[0]["text"] == '{"status":"ok"}'
    assert content[-1]["text"] == "money"
    assert len(records) == 2
    assert all(row["source_sha256"] == hashlib.sha256(data).hexdigest() for row in records)
    assert all(row["call_id"] == "c1" and row["checkpoint"] == "head" for row in records)
    assert all(content[index - 1]["type"] == "text" for index, item in enumerate(content)
               if item["type"] == "image")
    with Image.open(io.BytesIO(data)) as source:
        reconstructed = Image.new(source.mode, source.size)
        for record in records:
            delivered = Path(record["tile_path"]).read_bytes()
            with Image.open(io.BytesIO(delivered)) as part:
                assert part.tobytes() == source.crop((record["x0"], record["y0"],
                                                     record["x1"], record["y1"])).tobytes()
                reconstructed.paste(part, (record["x0"], record["y0"]))
        assert reconstructed.tobytes() == source.tobytes()
    dims = {row["file_id"]: (row["w"], row["h"]) for row in records}
    assert check_request({"messages": [{"role": "user", "content": content}]}, dims) == []
    prepare_images(raw, files=adapter.files, metadata={})
    assert len(uploaded) == 2


def test_legacy_input_blocks_preserve_fitting_bytes(tmp_path):
    data = png()
    adapter, uploaded = make_adapter(tmp_path, [])
    raw = [{"type": "input_text", "text": "Owner photo"},
           {"type": "input_image", "image_url": image_output(data).image_url}]
    content, records = prepare_images(raw, files=adapter.files, metadata={})
    assert list(uploaded.values()) == [data]
    assert records[0]["source_sha256"] == records[0]["tile_sha256"]
    assert content[-1]["source"]["type"] == "file"
    assert content[-1]["transformations"] == {"oversized_image": "error"}


@pytest.mark.parametrize("failure", ["upload", "manifest"])
def test_lost_images_invalidate_pending_and_accepted_finish(tmp_path, failure):
    scene = Scene(tmp_path)
    scene.finish_review = {"call_id": "prior"}

    @function_tool
    async def finish():
        scene.finish = {"status": "accepted"}
        return [ToolOutputText(text='{"status":"accepted"}'), image_output(png())]

    def broken_upload(data, name):
        raise OSError("upload failed")

    adapter, _ = make_adapter(tmp_path, [finish], scene=scene,
                              uploader=broken_upload if failure == "upload" else None)
    if failure == "manifest":
        adapter.image_manifest.mkdir()
    result = asyncio.run(adapter.invoke(block("finish")))
    assert result["is_error"]
    assert scene.finish is None and scene.finish_review is None
    assert "toolu_1" in scene._unseen
    assert not adapter.accepted_finish("finish", adapter.last_raw_output)


def test_finish_requires_delivered_result_and_unchanged_head(tmp_path):
    scene = Scene(tmp_path)

    @function_tool
    async def finish():
        scene.finish = {"status": "accepted"}
        return '{"status":"accepted"}'

    adapter, _ = make_adapter(tmp_path, [finish], scene=scene)
    assert not adapter.accepted_finish("finish", '{"status":"accepted"}')
    asyncio.run(adapter.invoke(block("finish")))
    assert adapter.accepted_finish("finish", adapter.last_raw_output)
    scene.s.head = {"id": "c2", "fingerprint": "fp2"}
    assert not adapter.accepted_finish("finish", adapter.last_raw_output)


def test_failed_specialist_output_does_not_replace_finish_operation(tmp_path):
    scene = Scene(tmp_path)
    scene.state = {"finish": {"call_id": "toolu_1"}}
    original = lambda *args: "real specialist operation"
    scene.finish = original
    adapter, _ = make_adapter(tmp_path, [], scene=scene, specialist=True)
    adapter.mark_unseen("toolu_1")
    assert scene.finish is original
    assert scene.state["finish"] is None


def test_clear_expires_only_dedup_and_namespaces_only_log(tmp_path):
    scene = Scene(tmp_path)
    scene._emitted_images = {"sha": "previous"}

    @function_tool
    async def inspect(ctx: ToolContext):
        scene.rows.append({"call_id": ctx.tool_call_id})
        return "ok"

    status = SimpleNamespace(stage_call_id="parent_toolu", footer=lambda: "money")
    adapter, _ = make_adapter(tmp_path, [inspect], scene=scene, status=status, specialist=True)
    adapter.expire_image_dedup()
    assert scene._emitted_images == {} and scene._unseen == set()
    asyncio.run(adapter.invoke(block("inspect")))
    assert scene.rows[0]["call_id"] == "toolu_1"
    assert adapter.log.calls[0][1] == "stage:parent_toolu:toolu_1"
    assert adapter.log.calls[-1][0] == "journal"


def temple_adapter(tmp_path, *, allowed=True, left="0.00000001"):
    scene = Scene(tmp_path)
    calls = []
    ledger = SimpleNamespace(
        may_start_request=lambda: (allowed, "" if allowed else "unknown"),
        money_status=lambda: {"settled_usd": "0", "cutoff_usd": left, "left_usd": left})
    runtime = SimpleNamespace(run_stage=lambda *args: None)
    status = SimpleNamespace(ledger=ledger, runtime=runtime, footer=lambda: "money")
    tools = build_tools(scene, RunSettings())
    adapter, _ = make_adapter(tmp_path, tools, scene=scene, status=status)
    return adapter, calls


def test_temple_exact_submicrodollar_and_bridge_callback(tmp_path, monkeypatch):
    from glasses3d.agent.temple_lab import stage
    adapter, calls = temple_adapter(tmp_path)

    async def child(spec, scene, cap, cid):
        calls.append((spec, cap, cid))
        await asyncio.sleep(0)
        return {"status": "refused", "code": "E_STAGE_INCOMPLETE"}

    adapter.status.run_stage = child

    def execute(scene, runtime, bridge, cid, left, settings):
        assert scene.lock.locked()
        assert isinstance(bridge, stage.StageBridge)
        assert left == Decimal("0.00000001")
        assert isinstance(left, Decimal)
        assert settings.finish_k == 4
        return bridge.run(runtime, "childspec", scene, left, cid)

    monkeypatch.setattr(stage, "run", execute)
    result = asyncio.run(adapter.invoke(block("temple_stage")))
    assert result_json(result)["code"] == "E_STAGE_INCOMPLETE"
    assert calls == [("childspec", Decimal("0.00000001"), "toolu_1")]
    assert not adapter.scene.lock.locked()
    assert adapter.scene.rows[-1]["call_id"] == "toolu_1"


@pytest.mark.parametrize("reason", ["cutoff", "unknown"])
def test_temple_cutoff_or_unknown_never_starts_child(tmp_path, monkeypatch, reason):
    from glasses3d.agent.temple_lab import stage
    adapter, _ = temple_adapter(tmp_path, allowed=False)
    adapter.status.ledger.may_start_request = lambda: (False, reason)
    monkeypatch.setattr(stage, "run", lambda *args: pytest.fail("child must not start"))
    result = asyncio.run(adapter.invoke(block("temple_stage")))
    assert result_json(result)["code"] == "E_STAGE_BUDGET"


def test_private_temple_fatal_callback_survives_redecoration(tmp_path, monkeypatch):
    from glasses3d.agent.temple_lab import stage
    adapter, _ = temple_adapter(tmp_path)

    def failure(*args):
        raise StageRunFailed("fatal bridge outcome")

    monkeypatch.setattr(stage, "run", failure)
    with pytest.raises(StageRunFailed, match="fatal bridge outcome"):
        asyncio.run(adapter.invoke(block("temple_stage")))
    assert not adapter.scene.lock.locked()


def test_private_temple_cancellation_waits_for_child_unwind(tmp_path, monkeypatch):
    from glasses3d.agent.temple_lab import stage
    adapter, _ = temple_adapter(tmp_path)
    started, unwound = asyncio.Event(), []

    async def child(spec, scene, cap, cid):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            await asyncio.sleep(0.01)
            unwound.append(True)

    adapter.status.run_stage = child
    monkeypatch.setattr(stage, "run", lambda scene, runtime, bridge, cid, left, settings:
                        bridge.run(runtime, "child", scene, left, cid))

    async def run():
        task = asyncio.create_task(adapter.invoke(block("temple_stage")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert unwound == [True]
        assert not adapter.scene.lock.locked()
    asyncio.run(run())

