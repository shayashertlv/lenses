"""Exercise evidence on the installed SDK HTTP transport, without network/inference."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("agents")
import httpx2
from openai import DefaultAsyncHttpxClient
from blender_agent.agent import ReviewLog


def test_transport_preserves_response_and_archives_exact_image_bytes(tmp_path):
    image = b"offline image bytes"
    secret = "offline-test-secret"
    payload = {"model": "gpt-6-astra", "input": [{"type": "input_image",
               "image_url": "data:image/png;base64," + base64.b64encode(image).decode()}]}
    provider = {"id": "resp_offline", "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 123, "output_tokens": 456}}
    calls = []
    log = ReviewLog(tmp_path, secret)
    log.interactions = 7

    class Budget:
        async def before_request(self, request):
            calls.append("reserved")
            await request.aread()

    log.budget = Budget()

    async def handle(request):
        assert calls == ["reserved"]
        calls.append("dispatched")
        assert json.loads(request.content) == payload
        assert request.headers["Authorization"] == "Bearer " + secret
        return httpx2.Response(200, json=provider, headers={"x-request-id": "req_offline"})

    async def run():
        async with DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handle),
                event_hooks={"request": [log.before_request], "response": [log.after_response]}) as client:
            response = await client.post("https://api.openai.com/v1/responses", json=payload,
                                         headers={"Authorization": "Bearer " + secret})
            assert response.json() == provider
            assert await response.aread() == response.content
    asyncio.run(run())
    assert calls == ["reserved", "dispatched"]
    saved = json.loads((tmp_path / "transport/0007-request.json").read_text())
    receipt = saved["input"][0]["image_url"]
    assert Path(receipt["saved_image"]).read_bytes() == image
    assert receipt["sha256"] == hashlib.sha256(image).hexdigest()
    assert json.loads((tmp_path / "transport/0007-response.json").read_text()) == provider
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [item["sequence"] for item in events] == [1, 2]
    assert events[-1]["request_id"] == "req_offline"
    assert events[-1]["status"] == "incomplete"
    assert events[-1]["usage"] == provider["usage"]
    assert all(secret not in path.read_text() for path in tmp_path.rglob("*.json*"))


@pytest.mark.parametrize("body", [b"[1,2]", b"null", b"gateway unavailable"])
def test_error_payload_does_not_mask_sdk_response(tmp_path, body):
    log = ReviewLog(tmp_path)
    response = httpx2.Response(502, content=body)
    asyncio.run(log.after_response(response))
    assert response.content == body
    event = json.loads((tmp_path / "events.jsonl").read_text())
    assert event["http_status"] == 502
    assert event["status"] is None
