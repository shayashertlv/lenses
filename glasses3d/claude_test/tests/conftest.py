"""Shared mock SSE fixtures; all tests are isolated from credentials and the network."""
from __future__ import annotations

import json
import socket

import httpx
import httpx2
import pytest


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("M13 C1 tests must never use a real network transport")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def sse_builder():
    """Build SDK-shaped events, or serialize an explicitly supplied event sequence."""
    def build(*, model="claude-opus-5-5", usage=None, final_usage=None,
              events=None, stop=True, text="Hello", stop_reason="end_turn", stop_details=None):
        if events is None:
            events = [
                {"type": "message_start", "message": {
                    "id": "msg_test", "type": "message", "role": "assistant",
                    "model": model, "content": [], "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 100, "output_tokens": 1}
                    if usage is None else usage,
                }},
                {"type": "content_block_start", "index": 0,
                 "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "text_delta", "text": text}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta",
                 "delta": {"stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details},
                 "usage": {"output_tokens": 10} if final_usage is None else final_usage},
            ]
            if stop:
                events.append({"type": "message_stop"})
        return "".join(
            f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            for event in events
        ).encode("utf-8")
    return build


@pytest.fixture
def fake_sse(sse_builder):
    """Alias for callers wanting a named fake stream builder."""
    return sse_builder
