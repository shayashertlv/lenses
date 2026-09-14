import asyncio
import base64
import hashlib
import json
import struct
from pathlib import Path

import httpx
import pytest
from PIL import Image

import app.providers as providers
from app.prompts import API_GUIDE, image_labels, stage_context, stage_instructions
from app.providers import AstraClient, MeshyClient, ProviderError, _download_url, _extract_script, meshy_settings


@pytest.fixture
def refs(tmp_path):
    result = {}
    for index, label in enumerate(("front", "back", "left", "right", "angled")):
        path = tmp_path / (label + ".jpg")
        Image.new("RGB", (16, 16), (index * 30, 25, 140)).save(path)
        result[label] = path
    return result


def glb():
    document = json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0}).encode()
    document += b" " * (-len(document) % 4)
    return b"glTF" + struct.pack("<IIII", 2, 20 + len(document), len(document), 0x4E4F534A) + document


def astra_response(script="import math\nx = math.sqrt(2)", **changes):
    result = {"id": "resp_fixture", "status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "custom_tool_call", "name": "run_blender_python", "call_id": "call_fixture", "status": "completed", "input": script},
    ], "usage": {"input_tokens": 12, "output_tokens": 18}}
    result.update(changes)
    return result


class Pieces(httpx.AsyncByteStream):
    def __init__(self, payload, size=7, pause=False):
        self.payload, self.size, self.pause = payload, size, pause
        self.closed = False
        self.first_chunk_consumed = asyncio.Event()

    async def __aiter__(self):
        for offset in range(0, len(self.payload), self.size):
            if self.pause and offset:
                await asyncio.sleep(0.01)
            yield self.payload[offset:offset + self.size]
            self.first_chunk_consumed.set()

    async def aclose(self):
        self.closed = True


def sse(response, newline=b"\n"):
    return newline.join([
        b"event: response.output_item.added",
        b'data: {"type":"response.output_item.added","output_index":1,"item":{"type":"custom_tool_call","name":"run_blender_python","input":""}}',
        b"", b"event: response.custom_tool_call_input.delta",
        b'data: {"type":"response.custom_tool_call_input.delta","delta":"import math"}', b"",
        b"event: response.completed", b"data: " + json.dumps({"type": "response.completed", "response": response}).encode(), b"", b"",
    ])


def test_quality_settings_are_exact_supported_parameters():
    assert meshy_settings("generate") == {"ai_model": "meshy-7", "ultra_mode": True, "should_texture": False,
                                           "should_remesh": False, "target_formats": ["glb"]}
    assert meshy_settings("texture") == {"ai_model": "meshy-7", "enable_pbr": True,
                                          "texture_resolution": "8k", "enable_original_uv": False, "target_formats": ["glb"]}
    assert not {"remove_lighting", "hd_texture", "ultra_mode", "should_remesh", "target_polycount"} & meshy_settings("texture").keys()


@pytest.mark.parametrize("stage,count", [("lenses", 5), ("connections", 6), ("finish", 5)])
def test_standalone_stage_instructions(stage, count):
    prompt = stage_instructions(stage)
    assert "exactly one" in prompt and "executed once" in prompt
    assert len(image_labels(stage)) == count
    assert "mathutils.bvhtree" in prompt and "mathutils.kdtree" in prompt
    assert "previous_response_id" not in prompt
    context = stage_context(stage, {"name": "Fixture", "inspection": {"objects": []}, "history": "secret history", "api_key": "secret"})
    assert "secret" not in context and "history" not in context
    assert "Meshy" in prompt


@pytest.mark.parametrize("stage", ["lenses", "finish"])
def test_original_images_for_reference_stages(stage):
    assert all(label.startswith("original reference") for label in image_labels(stage))
    assert "five original" in stage_instructions(stage) or "five attached" in stage_instructions(stage)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["generate", "texture"])
async def test_meshy_submission_contract_and_no_duplicate(stage, refs, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"result": "task_fixture"})
    path = tmp_path / "edited.glb"
    path.write_bytes(glb())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient("private-meshy-key", http)
        receipt = tmp_path / "receipt"
        assert await client.submit(stage, refs, path if stage == "texture" else None, receipt, asyncio.Event()) == "task_fixture"
        with pytest.raises(ProviderError, match="resubmission"):
            await client.submit(stage, refs, path if stage == "texture" else None, receipt, asyncio.Event())
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    field = "image_urls" if stage == "generate" else "multiview_image_urls"
    assert len(payload[field]) == 4
    assert payload[field][0].startswith("data:image/jpeg;base64,")
    assert payload["ai_model"] == "meshy-7"
    if stage == "texture":
        assert payload["model_url"].startswith("data:application/octet-stream;base64,")
        assert not {"input_task_id", "text_style_prompt", "image_style_url", "remove_lighting"} & payload.keys()
    saved = (receipt / "submit_request.json").read_text()
    assert "private-meshy-key" not in saved and "base64" not in saved
    assert json.loads(saved)["references"][0]["sha256"] == hashlib.sha256(refs["front"].read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_meshy_poll_tracks_matching_task_and_no_post(tmp_path):
    requests = []
    statuses = iter(["PENDING", "IN_PROGRESS", "SUCCEEDED"])
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "task_fixture", "status": next(statuses)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient("key", http)
        client.poll_interval = 0.001
        result = await client.poll("texture", "task_fixture", tmp_path, asyncio.Event())
    assert result["status"] == "SUCCEEDED"
    assert all(request.method == "GET" for request in requests)
    assert len(list(tmp_path.glob("poll_*.json"))) == 3


@pytest.mark.asyncio
async def test_meshy_poll_keeps_first_latest_pending_and_terminal_receipts(tmp_path):
    statuses = iter(["PENDING", "PENDING", "IN_PROGRESS", "IN_PROGRESS", "IN_PROGRESS", "SUCCEEDED"])
    replies = []
    def handler(request):
        reply = {"id": "task_fixture", "status": next(statuses), "progress": len(replies)}
        replies.append(reply)
        return httpx.Response(200, json=reply)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient("key", http)
        client.poll_interval = 0.001
        result = await client.poll("generate", "task_fixture", tmp_path, asyncio.Event())
    assert result["status"] == "SUCCEEDED" and len(replies) == 6
    kept = sorted(json.loads(path.read_text())["progress"] for path in tmp_path.glob("poll_*.json"))
    assert kept == [0, 4, 5]
    assert json.loads((tmp_path / "task.json").read_text())["status"] == "SUCCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [{"id": "wrong", "status": "SUCCEEDED"}, {"id": "task", "status": "FAILED"}, {"id": "task", "status": "WHATEVER"}])
async def test_meshy_poll_failures_are_terminal(reply, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=reply)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError):
            await MeshyClient("key", http).poll("generate", "task", tmp_path, asyncio.Event())
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_meshy_cancel_poll_keeps_task(tmp_path, monkeypatch):
    cancel = asyncio.Event()
    saved = asyncio.Event()
    requests = []
    original_save = providers._json_file
    def save_and_signal(path, data, **kwargs):
        original_save(path, data, **kwargs)
        if path == tmp_path / 'task.json':
            saved.set()
    monkeypatch.setattr(providers, '_json_file', save_and_signal)
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "task", "status": "IN_PROGRESS"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient("key", http)
        operation = asyncio.create_task(client.poll("generate", "task", tmp_path, cancel))
        # Exercise cancellation after the durable task exists. A fixed sleep
        # races disk writes under load and can cancel an earlier request phase.
        await asyncio.wait_for(saved.wait(), 5)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert len(requests) == 1
    assert json.loads((tmp_path / "task.json").read_text())["id"] == "task"


@pytest.mark.asyncio
async def test_meshy_download_stream_and_local_integrity_reuse(tmp_path):
    requests = []
    content = glb()
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Pieces(content, 3))
    task = {"id": "task", "status": "SUCCEEDED", "model_urls": {"glb": "https://cdn.meshy.ai/fixture.glb"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient("secret-key", http)
        path = await client.download(task, tmp_path, asyncio.Event())
        assert path.read_bytes() == content
        assert await client.download(task, tmp_path, asyncio.Event()) == path
        path.write_bytes(b"changed")
        with pytest.raises(ProviderError, match="integrity"):
            await client.download(task, tmp_path, asyncio.Event())
    assert len(requests) == 1
    assert "secret-key" not in str(requests[0].headers)


@pytest.mark.parametrize("url", ["http://cdn.meshy.ai/a", "https://127.0.0.1/a", "https://localhost/a", "https://host.local/a", "file:///model.glb", "https://user:password@cdn.meshy.ai/a", "https://cdn.meshy.ai:8000/a"])
def test_download_rejects_nonpublic_urls(url):
    with pytest.raises(ProviderError):
        _download_url(url)


@pytest.mark.asyncio
async def test_redirect_revalidation(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match="public HTTPS"):
            await MeshyClient("key", http).download({"id": "task", "status": "SUCCEEDED", "model_urls": {"glb": "https://cdn.meshy.ai/a"}}, tmp_path, asyncio.Event())
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"not glb", b"glTF" + b"\0" * 20])
async def test_invalid_download_retained_without_adoption(payload, tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload))) as http:
        with pytest.raises(ProviderError):
            await MeshyClient("key", http).download({"id": "task", "status": "SUCCEEDED", "model_urls": {"glb": "https://cdn.meshy.ai/a"}}, tmp_path, asyncio.Event())
    assert not (tmp_path / "source.glb").exists()
    assert list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,newline,size", [("lenses", b"\n", 1), ("connections", b"\r\n", 7), ("finish", b"\n", 100000)])
async def test_astra_stream_fragmentation_and_request_contract(stage, newline, size, refs, tmp_path):
    requests = []
    result = astra_response()
    stream = Pieces(sse(result, newline), size)
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    images = list(refs.values()) + ([refs["front"]] if stage == "connections" else [])
    receipt = tmp_path / "astra"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AstraClient("private-astra-key", http)
        script = await client.edit(stage, images, {"inspection": {"objects": []}, "history": "must not appear"}, receipt, asyncio.Event())
        with pytest.raises(ProviderError, match="resubmission"):
            await client.edit(stage, images, {}, receipt, asyncio.Event())
    assert len(requests) == 1 and stream.closed
    assert script == result["output"][1]["input"]
    assert (receipt / "astra_script.py").read_text() == script
    assert json.loads((receipt / "astra_response.json").read_text())["usage"]["output_tokens"] == 18
    payload = json.loads(requests[0].content)
    assert payload["model"] == "gpt-6-astra" and payload["reasoning"] == {"effort": "high"}
    assert payload["tool_choice"] == {"type": "custom", "name": "run_blender_python"}
    assert payload["parallel_tool_calls"] is False and payload["store"] is False
    assert len(payload["tools"]) == 1
    assert not {"temperature", "top_p", "previous_response_id", "conversation"} & payload.keys()
    content = payload["input"][0]["content"]
    assert len([item for item in content if item["type"] == "input_image"]) == len(images)
    assert "must not appear" not in json.dumps(payload)
    saved = (receipt / "astra_request.json").read_text()
    assert "private-astra-key" not in saved and "base64" not in saved


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,count", [("lenses", 4), ("lenses", 6), ("connections", 5), ("connections", 7), ("finish", 6)])
async def test_wrong_astra_images_fail_before_request(stage, count, refs, tmp_path):
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as http:
        with pytest.raises(ProviderError, match="exactly"):
            await AstraClient("key", http).edit(stage, [refs["front"]] * count, {}, tmp_path, asyncio.Event())
    assert not requests and not (tmp_path / "astra_request.json").exists()


@pytest.mark.parametrize("output", [[], [{"type": "message", "content": []}],
    [{"type": "custom_tool_call", "name": "wrong", "input": "pass"}],
    [{"type": "function_call", "name": "run_blender_python", "arguments": "pass"}],
    astra_response()["output"] + [{"type": "function_call", "name": "other", "arguments": "{}"}],
    astra_response()["output"] + [{"type": "web_search_call", "status": "completed"}],
    astra_response()["output"] + [astra_response()["output"][1]],
])
def test_exactly_one_custom_tool_and_no_other_tool(output):
    with pytest.raises(ProviderError):
        _extract_script(astra_response(output=output))


@pytest.mark.parametrize("position", ["before", "after"])
def test_narrative_message_beside_the_one_tool_call_keeps_the_paid_script(position):
    # A stray message item is unwanted, but the paid script is still the tool input.
    message = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Here is the script."}]}
    output = astra_response()["output"]
    output = [message] + output if position == "before" else output + [message]
    assert _extract_script(astra_response(output=output)) == astra_response()["output"][1]["input"]


@pytest.mark.asyncio
async def test_unsafe_script_saved_before_validation(refs, tmp_path):
    response = astra_response("import os\nos.remove('important')")
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match="local validation"):
            await AstraClient("key", http).edit("lenses", list(refs.values()), {}, tmp_path, asyncio.Event())
    assert len(requests) == 1
    assert (tmp_path / "astra_script.py").read_text() == response["output"][1]["input"]
    assert (tmp_path / "astra_response.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 402, 429, 500])
async def test_http_failures_no_paid_retry(status, refs, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "fixture failure"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match=str(status)):
            await AstraClient("key", http).edit("finish", list(refs.values()), {}, tmp_path / "astra", asyncio.Event())
        with pytest.raises(ProviderError, match=str(status)):
            await MeshyClient("key", http).submit("generate", refs, None, tmp_path / "meshy", asyncio.Event())
    assert len(requests) == 2
    assert (tmp_path / "astra" / "astra_response.stream").exists()
    assert (tmp_path / "meshy" / "submit_response.json").exists()


@pytest.mark.asyncio
async def test_auth_rejection_has_private_raw_bytes_and_safe_durable_metadata(refs, tmp_path):
    key = 'sk-private-fixture-key'
    raw = json.dumps({'error': {'code': 'invalid_api_key', 'message': 'Incorrect API key provided: ' + key}}).encode()
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, content=raw)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AstraClient(key, http)
        with pytest.raises(ProviderError, match='API setup') as failure:
            await client.edit('lenses', list(refs.values()), {}, tmp_path, asyncio.Event())
        assert key not in str(failure.value)
        with pytest.raises(ProviderError, match='reservation'):
            await client.edit('lenses', list(refs.values()), {}, tmp_path, asyncio.Event())
    assert len(requests) == 1
    assert (tmp_path / 'astra_response.stream').read_bytes() == raw
    receipt = (tmp_path / 'astra_http_error.json').read_text()
    assert key not in receipt
    assert json.loads(receipt) == {
        'status_code': 401, 'error_code': 'invalid_api_key',
        'response_sha256': hashlib.sha256(raw).hexdigest(),
        'key_sha256': hashlib.sha256(key.encode()).hexdigest(),
    }
    assert not (tmp_path / 'astra_response.json').exists()
    assert not (tmp_path / 'astra_script.py').exists()


@pytest.mark.asyncio
async def test_cancel_stream_preserves_bytes_and_reservation(refs, tmp_path):
    cancel = asyncio.Event()
    stream = Pieces(sse(astra_response()), 10, pause=True)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        operation = asyncio.create_task(AstraClient("key", http).edit("finish", list(refs.values()), {}, tmp_path, cancel))
        await asyncio.wait_for(stream.first_chunk_consumed.wait(), 5)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert len(requests) == 1 and stream.closed
    assert (tmp_path / "astra_response.stream").stat().st_size > 0
    assert (tmp_path / "astra_request.json").exists()
    assert not (tmp_path / "astra_script.py").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"data: not-json\n\n", b'data: {"type":"response.incomplete"}\n\n', b'data: {"type":"response.custom_tool_call_input.delta","delta":"pass"}\n\n'])
async def test_partial_malformed_stream_never_executes(body, refs, tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body))) as http:
        with pytest.raises(ProviderError):
            await AstraClient("key", http).edit("finish", list(refs.values()), {}, tmp_path, asyncio.Event())
    assert (tmp_path / "astra_response.stream").read_bytes() == body
    assert not (tmp_path / "astra_script.py").exists()


@pytest.mark.asyncio
async def test_size_limits_before_submit_and_during_stream(refs, tmp_path, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"x" * 100)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(providers, "MAX_IMAGE_BYTES", 4)
        with pytest.raises(ProviderError, match="limit"):
            await AstraClient("key", http).edit("finish", list(refs.values()), {}, tmp_path / "small", asyncio.Event())
        assert not requests
        monkeypatch.setattr(providers, "MAX_IMAGE_BYTES", 20_000_000)
        monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 10)
        with pytest.raises(ProviderError, match="8 MB"):
            await AstraClient("key", http).edit("finish", list(refs.values()), {}, tmp_path / "response", asyncio.Event())
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_empty_keys_allow_idle_boot_but_block_dispatch(refs, tmp_path):
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r))) as http:
        astra, meshy = AstraClient("", http), MeshyClient("", http)
        with pytest.raises(ProviderError, match="API key"):
            await astra.edit("finish", list(refs.values()), {}, tmp_path / "astra", asyncio.Event())
        with pytest.raises(ProviderError, match="API key"):
            await meshy.submit("generate", refs, None, tmp_path / "meshy", asyncio.Event())
    assert requests == []
    assert not (tmp_path / "astra").exists() and not (tmp_path / "meshy").exists()


@pytest.mark.asyncio
async def test_astra_total_timeout_keeps_partial_stream(refs, tmp_path):
    stream = Pieces(sse(astra_response()), 10, pause=True)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AstraClient("key", http)
        client.response_timeout = 0.1
        with pytest.raises(ProviderError, match="timed out"):
            await client.edit("finish", list(refs.values()), {}, tmp_path, asyncio.Event())
    assert len(requests) == 1 and stream.closed
    assert (tmp_path / "astra_response.stream").stat().st_size > 0
    assert not (tmp_path / "astra_script.py").exists()


@pytest.mark.asyncio
async def test_meshy_download_cancel_retains_partial_bytes(tmp_path):
    cancel = asyncio.Event()
    stream = Pieces(glb(), 10, pause=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as http:
        client = MeshyClient("key", http)
        task = {"id": "task", "status": "SUCCEEDED", "model_urls": {"glb": "https://cdn.meshy.ai/a"}}
        operation = asyncio.create_task(client.download(task, tmp_path, cancel))
        await asyncio.wait_for(stream.first_chunk_consumed.wait(), 5)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert stream.closed
    assert sum(path.stat().st_size for path in tmp_path.glob("*.part")) > 0
    assert not (tmp_path / "source.glb").exists()


@pytest.mark.asyncio
async def test_cancel_during_submission_preserves_response_prefix(refs, tmp_path):
    cancel = asyncio.Event()
    stream = Pieces(json.dumps({"result": "task_fixture"}).encode(), 2, pause=True)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient("key", http)
        operation = asyncio.create_task(client.submit("generate", refs, None, tmp_path, cancel))
        await asyncio.wait_for(stream.first_chunk_consumed.wait(), 5)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    assert len(requests) == 1 and stream.closed
    assert (tmp_path / "submit_response.json").stat().st_size > 0
    assert (tmp_path / "submit_request.json").exists()
    assert not (tmp_path / "task_receipt.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [0, 4, 8])
async def test_texture_model_upload_streams_exact_json_without_full_file_read(extra, refs, tmp_path, monkeypatch):
    model = tmp_path / 'model.glb'
    binary = bytes(range(256)) * 2304 + b'\0' * extra
    content = bytearray(glb())
    content.extend(struct.pack('<II', len(binary), 0x004E4942) + binary)
    struct.pack_into('<I', content, 8, len(content))
    model.write_bytes(content)
    read_bytes = Path.read_bytes
    def disallow_full_model_read(path):
        if path == model:
            raise AssertionError('Model must be streamed, never read into one base64 allocation')
        return read_bytes(path)
    monkeypatch.setattr(Path, 'read_bytes', disallow_full_model_read)
    seen = []
    def handler(request):
        seen.append(request)
        assert int(request.headers['content-length']) == len(request.content)
        assert request.headers['content-type'] == 'application/json'
        body = json.loads(request.content)
        assert base64.b64decode(body['model_url'].split(',', 1)[1]) == content
        assert len(body['multiview_image_urls']) == 4
        return httpx.Response(200, json={'result': 'streamed-model-task'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient('key', http)
        assert await client.submit('texture', refs, model, tmp_path / 'receipt', asyncio.Event()) == 'streamed-model-task'
    assert len(seen) == 1
    receipt = json.loads((tmp_path / 'receipt' / 'submit_request.json').read_text())
    assert receipt['model']['bytes'] == len(content)
    assert receipt['model']['sha256'] == hashlib.sha256(content).hexdigest()
    assert providers.MAX_UPLOAD_BYTES == providers.MAX_GLB_BYTES == 2_000_000_000


@pytest.mark.asyncio
async def test_texture_upload_cancellation_preserves_reservation_and_never_reposts(refs, tmp_path, monkeypatch):
    cancel = asyncio.Event()
    model = tmp_path / 'model.glb'
    model.write_bytes(glb())
    original_body = providers._model_json_body
    entered = asyncio.Event()
    async def stop_during_body(prefix, path, metadata, stop):
        async for chunk in original_body(prefix, path, metadata, stop):
            yield chunk
            entered.set()
            await stop.wait()
            raise asyncio.CancelledError()
    monkeypatch.setattr(providers, '_model_json_body', stop_during_body)
    responses = []
    def handler(request):
        responses.append(request)
        return httpx.Response(200, json={'result': 'should-not-complete'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeshyClient('key', http)
        receipt = tmp_path / 'receipt'
        operation = asyncio.create_task(client.submit('texture', refs, model, receipt, cancel))
        await asyncio.wait_for(entered.wait(), 5)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        with pytest.raises(ProviderError, match='resubmission'):
            await client.submit('texture', refs, model, receipt, asyncio.Event())
    assert (receipt / 'submit_request.json').exists()
    assert not (receipt / 'task_receipt.json').exists()
    assert responses == []


@pytest.mark.asyncio
async def test_completed_response_cancel_before_execution_remains_locally_recoverable(refs, tmp_path):
    cancel = asyncio.Event()
    payload = sse(astra_response())
    class CompleteThenCancel(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload
            cancel.set()
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=CompleteThenCancel())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AstraClient('key', http)
        with pytest.raises(asyncio.CancelledError):
            await client.edit('finish', list(refs.values()), {}, tmp_path, cancel)
        with pytest.raises(ProviderError, match='resubmission'):
            await client.edit('finish', list(refs.values()), {}, tmp_path, asyncio.Event())
    assert len(requests) == 1
    assert (tmp_path / 'astra_response.stream').read_bytes() == payload
    assert json.loads((tmp_path / 'astra_response.json').read_text())['status'] == 'completed'
    assert not (tmp_path / 'astra_script.py').exists()
