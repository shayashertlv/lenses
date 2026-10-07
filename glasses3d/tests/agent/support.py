"""Shared helpers for the stage-C money tests (no network, no Blender)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
OFFLINE_KEY = "sk-offline-stage-c-test"
INFERENCE_URL = "https://api.openai.com/v1/responses"


def require_data(path: Path | None, what: str) -> Path:
    """Data-dependent tests skip when the data is absent, and FAIL under G3D_REQUIRE_DATA=1."""
    if path is not None and path.exists():
        return path
    message = f"{what} not found"
    if os.environ.get("G3D_REQUIRE_DATA") == "1":
        pytest.fail(message + " (G3D_REQUIRE_DATA=1)")
    pytest.skip(message)


def fake_counter(tokens: int = 1000):
    """The Studio tests' counting client: a mock with the global base URL and an async count()."""
    count = AsyncMock(return_value=SimpleNamespace(input_tokens=tokens))
    client = SimpleNamespace(base_url="https://api.openai.com/v1/",
                             responses=SimpleNamespace(input_tokens=SimpleNamespace(count=count)))
    client.with_options = Mock(return_value=client)
    return client, count


def request(**changes) -> httpx2.Request:
    payload = {"model": "gpt-6-astra", "service_tier": "default", "stream": False,
               "max_output_tokens": 100, "input": "private product information"}
    payload.update(changes)
    return httpx2.Request("POST", INFERENCE_URL, json=payload, headers={"authorization": "Bearer private-key"})


def usage(*, tokens: int = 1000, cached: int = 0, output: int = 1, written: int = 0, reasoning: int = 0) -> dict:
    return {"input_tokens": tokens, "output_tokens": output, "total_tokens": tokens + output,
            "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
            "output_tokens_details": {"reasoning_tokens": reasoning}}


def provider_body(*, response_id: str = "resp_test_1", status: str = "completed", model: str = "gpt-6-astra",
                  usage_block=None, **extra) -> dict:
    body = {"id": response_id, "object": "response", "created_at": 1790000000, "model": model, "status": status,
            "output": [{"type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "ok", "annotations": []}]}],
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [], "service_tier": "default",
            "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
            "usage": usage() if usage_block is None else usage_block}
    body.update(extra)
    return body


def response(status: int = 200, body=None, *, request_id: str | None = "req_test_1", content: bytes | None = None):
    headers = {"x-request-id": request_id} if request_id else {}
    if content is not None:
        return httpx2.Response(status, content=content, headers=headers)
    return httpx2.Response(status, json=provider_body() if body is None else body, headers=headers)


def ledger(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
