"""Long append-only requests through the real runner, SDK and transport guard."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import json

from agents import function_tool
from agents.tool_context import ToolContext

from glasses3d.agent.renewal import SUMMARY_REQUEST
from glasses3d.claude_test import conversation as C, runner as R
from glasses3d.claude_test.cache_sim import (
    CacheBlock, CacheSimulator, MINIMUM_PREFIX_TOKENS, request_namespace, request_values,
)
from glasses3d.claude_test.scripted import ScriptedClaude, ScriptTurn
from glasses3d.claude_test.tests.test_conversation import png, reference
from glasses3d.claude_test.tests.test_runner import Harness, call, root


def test_long_runner_cache_cost_through_real_renewal(root):
    """Cross 350K, reach unchanged 800K renewal, pay one fresh opening write."""
    simulator = CacheSimulator()
    wire = None

    def measured_blocks(body):
        return [CacheBlock(value, C.estimate_content(value["value"], wire.dims)["total_tokens"])
                for value in request_values(body)]

    def summary(body):
        assert body["messages"][-1]["content"] == [{"type": "text", "text": SUMMARY_REQUEST}]
        return ScriptTurn([{"type": "text", "text": "Synthetic current state; keep owner inputs."}],
                          "end_turn", cache_output_tokens=20)

    wire = ScriptedClaude([
        *[ScriptTurn([
            {"type": "thinking", "thinking": f"Synthetic step {number}", "signature": f"opaque-{number}"},
            call("grow", f"growth_{number}")], cache_output_tokens=100)
          for number in range(8)],
        summary,
        ScriptTurn([call("finish", "final_finish")], cache_output_tokens=100),
    ], cache_sim=simulator, cache_blocks=measured_blocks)
    h = Harness(root, [], wire=wire)

    @function_tool
    async def grow(ctx: ToolContext):
        """Return large synthetic evidence without a modeling worker."""
        return json.dumps({"status": "ok", "call": ctx.tool_call_id, "evidence": "x" * 300_000})

    h.spec = replace(h.spec, tools=(grow, h.spec.tools[-1]),
                     instructions="Synthetic fixed instruction. " * 100,
                     invocation_text="Continue the synthetic offline fixture.",
                     photos=(reference(png(56, 56)),))
    h.runtime = R._Runtime(
        spec=h.spec, scene=h.scene, conversation=h.conversation, ledger=h.ledger,
        client=h.client, files=h.files, log=h.log, directory=root, shared=h.shared)

    async def scenario():
        opening, _ = C.opening_content(h.spec, files=h.files)
        await h.runtime.append({"role": "user", "content": opening})
        assert await h.runtime.drive() == "completed"

    try:
        asyncio.run(scenario())
        h.spec.settings.validate()
        assert (C.SOFT_IMAGES, C.SOFT_BYTES, C.SOFT_TOKENS) == (480, 25_600_000, 800_000)
        assert len(h.runtime.renewals) == 1
        renewal = h.runtime.renewals[0]
        assert renewal["reasons"] == [{
            "reason": "input_tokens", "measured": renewal["measurements"]["forecast_tokens"],
            "threshold": 800_000, "limit": 1_000_000,
        }]
        assert renewal["measurements"]["forecast_tokens"] > 800_000
        assert any(350_000 < row.total_input_tokens < 800_000 for row in wire.cache_results)
        assert len(wire.requests) == 10
        assert all("context_management" not in body for body in wire.requests)
        assert all(body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
                   for body in wire.requests)
        assert all(not call_["query"] for call_ in wire.calls if call_["path"] == "/v1/messages")
        assert all(len(body["messages"]) > 1 for body in wire.requests[1:-1])
        assert len(wire.requests[-1]["messages"]) == 1
        old_photo = [b for b in wire.requests[0]["messages"][0]["content"] if b["type"] == "image"]
        new_photo = [b for b in wire.requests[-1]["messages"][0]["content"] if b["type"] == "image"]
        assert old_photo == new_photo and len(old_photo) == 1
        assert all(b["type"] not in {"thinking", "tool_use", "tool_result"}
                   for b in wire.requests[-1]["messages"][0]["content"])
        for index in range(1, len(wire.requests) - 1):
            previous = measured_blocks(wire.requests[index - 1])
            current = measured_blocks(wire.requests[index])
            assert current[:len(previous)] == previous
            added = sum(block.tokens for block in current[len(previous):])
            result = wire.cache_results[index]
            assert result.read_tokens == sum(block.tokens for block in previous)
            assert result.write_tokens == added
            assert result.write_tokens <= added + MINIMUM_PREFIX_TOKENS
        fresh = wire.cache_results[-1]
        assert fresh.read_tokens == 0 and fresh.write_tokens == fresh.total_input_tokens
        # Independent replay from captured wire bytes predicts each settlement.
        prediction = CacheSimulator()
        expected = [prediction.request(measured_blocks(body), output_tokens=reply.cache_output_tokens,
                                       now=index * 60, namespace=request_namespace(body))
                    for index, (body, reply) in enumerate(zip(wire.requests, wire.replies))]
        assert [row.usage for row in expected] == [reply.usage for reply in wire.replies]
        total = sum((row.cost_usd for row in expected), Decimal(0))
        assert h.ledger.settled_usd() == total
        rows = [json.loads(line) for line in h.ledger.path.read_text().splitlines()]
        assert sum(Decimal(row["cost_usd"]) for row in rows if row["event"] == "settled") == total
        assert not any(row["event"] == "unknown" for row in rows)
        wire.assert_complete()
    finally:
        h.close()
