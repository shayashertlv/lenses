"""Offline witnesses that the request hook stops inference before overspending."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from blender_agent.budget import BudgetExceeded, BudgetGuard, BudgetGuardError
from modeler.agentic.pricing import Tariff


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "budget.json"
        self.counter = AsyncMock(return_value=SimpleNamespace(input_tokens=1000))
        self.client = SimpleNamespace(
            base_url="https://api.openai.com/v1/",
            responses=SimpleNamespace(input_tokens=SimpleNamespace(count=self.counter)),
        )
        self.client.with_options = Mock(return_value=self.client)

    def request(self, **changes):
        payload = {"model": "gpt-6-astra", "service_tier": "default", "stream": False,
                   "max_output_tokens": 100, "input": "private product information"}
        payload.update(changes)
        return httpx.Request("POST", "https://api.openai.com/v1/responses", json=payload,
                             headers={"authorization": "Bearer private-key"})

    async def test_counts_serialized_images_tools_and_schemas_before_reserving(self):
        guard = BudgetGuard(self.client, self.path, "1")
        input_items = [{"role": "user", "content": [
            {"type": "input_image", "detail": "high", "image_url": "data:image/png;base64,SECRET"},
            {"type": "input_text", "text": "private product information"},
        ]}]
        tools = [{"type": "function", "name": "view", "parameters": {"type": "object"}}]
        text = {"format": {"type": "json_schema", "name": "check", "schema": {"type": "object"}}}
        await guard.before_request(self.request(input=input_items, tools=tools, text=text,
                                               reasoning={"effort": "max"}, instructions="private instructions"))
        sent = self.counter.call_args.kwargs
        self.assertEqual(sent["input"], input_items)
        self.assertEqual(sent["tools"], tools)
        self.assertEqual(sent["text"], text)
        self.assertEqual(sent["reasoning"], {"effort": "max"})
        self.assertEqual(sent["instructions"], "private instructions")
        self.assertNotIn("max_output_tokens", sent)
        self.client.with_options.assert_called_once_with(max_retries=0)
        ledger = json.loads(self.path.read_text())
        self.assertEqual(ledger["reserved_micro_usd"], Tariff.frozen().reserve_micro(1000, 100))
        for secret in ("SECRET", "private", "Bearer", "data:image", "input_items"):
            self.assertNotIn(secret, self.path.read_text())

    async def test_planning_warns_before_reservation_cliff_without_changing_cap_or_holds(self):
        guard = BudgetGuard(self.client, self.path, "0.025")
        self.assertEqual(guard.planning()["status"], "awaiting_first_count")
        await guard.before_request(self.request())
        await guard.settle_latest(self.usage(cached=1000, output=40))  # $0.003
        before = self.path.read_bytes()
        planning = guard.summary()["planning"]
        self.assertEqual(planning["next_reservation_at_last_size_usd"], "0.017500")
        self.assertEqual(planning["suggested_finish_balance_usd"], "0.026500")
        self.assertEqual(planning["status"], "finalize_now")
        self.assertTrue(planning["next_request_fits_at_last_size"])
        self.assertEqual(self.path.read_bytes(), before)
        # Advisory does not stop affordable work or spend any allowance.
        await guard.before_request(self.request())
        self.assertEqual(guard.summary()["requests_reserved"], 2)
        self.assertFalse(guard.planning()["next_request_fits_at_last_size"])
        with self.assertRaises(BudgetExceeded):
            await guard.before_request(self.request())

    async def test_planning_uses_latest_exact_count_after_refusal_and_long_context_tariff(self):
        guard = BudgetGuard(self.client, self.path, "1")
        self.counter.return_value.input_tokens = 272001
        with self.assertRaises(BudgetExceeded):
            await guard.before_request(self.request(max_output_tokens=25000))
        planning = guard.planning()
        self.assertEqual(planning["last_counted_input_tokens"], 272001)
        self.assertEqual(planning["configured_max_output_tokens"], 25000)
        self.assertEqual(planning["next_reservation_at_last_size_usd"], "8.675025")
        self.assertFalse(planning["next_request_fits_at_last_size"])
        self.assertEqual(guard.summary()["committed_usd"], "0.000000")

    async def test_transport_sees_durable_reservation_and_never_sees_blocked_request(self):
        guard = BudgetGuard(self.client, self.path, "0.017500")
        seen = []

        async def transport(request):
            seen.append(json.loads(self.path.read_text())["reserved_micro_usd"])
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport),
                                     event_hooks={"request": [guard.before_request]}) as client:
            await client.send(self.request())
            with self.assertRaises(BudgetExceeded):
                await client.send(self.request())
        self.assertEqual(seen, [17500])
        self.assertEqual(guard.summary()["remaining_usd"], "0.000000")
        self.assertEqual(guard.summary()["blocked_kind"], "BudgetExceeded")

    async def test_concurrent_requests_share_one_ceiling(self):
        guard = BudgetGuard(self.client, self.path, "0.017500")
        results = await asyncio.gather(*(guard.before_request(self.request()) for _ in range(3)),
                                       return_exceptions=True)
        self.assertEqual(sum(result is None for result in results), 1)
        self.assertEqual(sum(isinstance(result, BudgetExceeded) for result in results), 2)
        self.assertEqual(guard.summary()["requests_reserved"], 1)

    async def test_long_context_cache_write_reservation_and_cap_round_down(self):
        self.counter.return_value.input_tokens = 272001
        amount = Tariff.frozen().reserve_micro(272001, 100)
        guard = BudgetGuard(self.client, self.path, str(amount / 1_000_000))
        await guard.before_request(self.request())
        self.assertEqual(json.loads(self.path.read_text())["reserved_micro_usd"], amount)
        another = BudgetGuard(self.client, self.path.with_name("fractional.json"), "0.0000009")
        self.assertEqual(another.summary()["maximum_usd"], "0.000000")

    async def test_counter_failure_and_invalid_count_stop_request_without_logging_error_body(self):
        guard = BudgetGuard(self.client, self.path, "1")
        self.counter.side_effect = RuntimeError("private-key and private prompt")
        with self.assertRaisesRegex(BudgetGuardError, "Token counting failed") as error:
            await guard.before_request(self.request())
        self.assertNotIn("private", str(error.exception))
        self.counter.side_effect = None
        for invalid in (-1, True, 1.5, "1000", 922001):
            self.counter.return_value = SimpleNamespace(input_tokens=invalid)
            with self.subTest(invalid=invalid), self.assertRaises(BudgetGuardError):
                await guard.before_request(self.request())
        self.assertEqual(guard.summary()["requests_reserved"], 0)

    async def test_unpriced_settings_refused_before_counting(self):
        guard = BudgetGuard(self.client, self.path, "1")
        cases = [dict(model="different"), dict(service_tier="auto"), dict(service_tier=None),
                 dict(stream=True), dict(background=True), dict(max_output_tokens=None),
                 dict(max_output_tokens=True), dict(max_output_tokens=0), dict(max_output_tokens=128001),
                 dict(tools=[{"type": "web_search"}]), dict(unreviewed_feature=True)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(BudgetGuardError):
                await guard.before_request(self.request(**changes))
        self.counter.assert_not_called()

    async def test_unknown_endpoints_and_retry_are_refused(self):
        guard = BudgetGuard(self.client, self.path, "1")
        for url in ("https://eu.api.openai.com/v1/responses", "https://api.openai.com/v1/chat/completions",
                    "https://example.com/v1/responses", "https://api.openai.com/v1/responses?override=1"):
            with self.subTest(url=url), self.assertRaises(BudgetGuardError):
                await guard.before_request(httpx.Request("POST", url, json={}))
        request = self.request()
        request.headers["x-stainless-retry-count"] = "1"
        with self.assertRaises(BudgetGuardError):
            await guard.before_request(request)
        self.counter.assert_not_called()

    async def test_failure_after_reservation_never_refunds(self):
        guard = BudgetGuard(self.client, self.path, "0.017500")

        async def transport(request):
            raise httpx.ReadTimeout("uncertain outcome")

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport),
                                     event_hooks={"request": [guard.before_request]}) as client:
            with self.assertRaises(httpx.ReadTimeout):
                await client.send(self.request())
            with self.assertRaises(BudgetExceeded):
                await client.send(self.request())
        self.assertEqual(guard.summary()["reserved_usd"], "0.017500")

    def usage(self, *, cached=0, output=1, written=0):
        return {"input_tokens": 1000, "output_tokens": output, "total_tokens": 1000 + output,
                "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
                "output_tokens_details": {"reasoning_tokens": 0}}

    async def test_valid_cache_usage_releases_hold_but_keeps_reservation_history(self):
        guard = BudgetGuard(self.client, self.path, "0.02")
        await guard.before_request(self.request())
        usage = self.usage(cached=1000)
        result = await guard.settle_latest(usage)
        actual = Tariff.frozen().settle_micro(usage)
        self.assertEqual(actual, 1050)
        self.assertEqual(result["settled_usd"], "0.001050")
        self.assertEqual(result["committed_usd"], "0.001050")
        self.assertEqual(result["reserved_usd"], "0.017500")
        await guard.before_request(self.request())
        self.assertEqual(guard.summary()["committed_usd"], "0.018550")
        self.assertEqual(guard.summary()["reserved_usd"], "0.035000")
        ledger = json.loads(self.path.read_text())
        self.assertEqual(ledger["reservations"][0]["settled_micro_usd"], actual)
        self.assertNotIn("settled_micro_usd", ledger["reservations"][1])

    async def test_unknown_older_response_remains_held_and_latest_cannot_settle_twice(self):
        guard = BudgetGuard(self.client, self.path, "0.04")
        await guard.before_request(self.request())  # Outcome unknown; no settlement.
        await guard.before_request(self.request())
        await guard.settle_latest(self.usage(cached=1000))
        original = self.path.read_bytes()
        with self.assertRaisesRegex(BudgetGuardError, "already settled"):
            await guard.settle_latest(self.usage(cached=1000))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(guard.summary()["unknown_reserved_usd"], "0.017500")
        self.assertEqual(guard.summary()["requests_unsettled"], 1)
        self.assertEqual(guard.summary()["committed_usd"], "0.018550")

    async def test_missing_and_invalid_usage_cannot_release_reservation(self):
        guard = BudgetGuard(self.client, self.path, "0.02")
        with self.assertRaises(BudgetGuardError):
            await guard.settle_latest(self.usage())
        await guard.before_request(self.request())
        original = self.path.read_bytes()
        for usage in (None, {}, {"input_tokens": 0, "output_tokens": 0},
                      self.usage(cached=1001), self.usage(output=-1),
                      dict(self.usage(), total_tokens=123)):
            with self.subTest(usage=usage), self.assertRaises(BudgetGuardError):
                await guard.settle_latest(usage)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(guard.summary()["committed_usd"], "0.017500")

    async def test_settled_cost_above_reservation_is_recorded_and_blocks_next_request(self):
        guard = BudgetGuard(self.client, self.path, "10")
        await guard.before_request(self.request())
        usage = self.usage(output=1000)
        actual = Tariff.frozen().settle_micro(usage)
        with self.assertRaisesRegex(BudgetGuardError, "exceeded its reservation"):
            await guard.settle_latest(usage)
        ledger = json.loads(self.path.read_text())
        self.assertEqual(ledger["violation"]["settled_micro_usd"], actual)
        self.assertEqual(ledger["reservations"][0]["settled_micro_usd"], actual)
        count_calls = self.counter.call_count
        with self.assertRaisesRegex(BudgetGuardError, "further inference is blocked"):
            await guard.before_request(self.request())
        self.assertEqual(self.counter.call_count, count_calls)
        self.assertEqual(guard.summary()["settled_usd"], "0.060000")

    async def test_settlement_persistence_failure_cannot_release_hold(self):
        guard = BudgetGuard(self.client, self.path, "1")
        await guard.before_request(self.request())
        original = self.path.read_bytes()
        with patch("blender_agent.budget.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(BudgetGuardError, "persistence failed"):
                await guard.settle_latest(self.usage(cached=1000))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(guard.summary()["settled_usd"], "0.000000")
        self.assertEqual(guard.summary()["committed_usd"], "0.017500")
        with self.assertRaises(BudgetGuardError):
            await guard.before_request(self.request())

    async def test_ledger_write_failure_blocks_transport(self):
        guard = BudgetGuard(self.client, self.path, "1")
        transport = AsyncMock(return_value=httpx.Response(200))
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport),
                                     event_hooks={"request": [guard.before_request]}) as client:
            with patch("blender_agent.budget.os.replace", side_effect=OSError("disk unavailable")):
                with self.assertRaisesRegex(BudgetGuardError, "persistence failed"):
                    await client.send(self.request())
        transport.assert_not_called()

    def test_existing_ledger_never_resets_and_bad_configuration_fails(self):
        BudgetGuard(self.client, self.path, "1")
        original = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            BudgetGuard(self.client, self.path, "10")
        self.assertEqual(self.path.read_bytes(), original)
        for invalid in (True, -1, "NaN", "Infinity", "not money"):
            with self.subTest(invalid=invalid), self.assertRaises(BudgetGuardError):
                BudgetGuard(self.client, self.path, invalid)
        self.client.base_url = "https://eu.api.openai.com/v1/"
        with self.assertRaises(BudgetGuardError):
            BudgetGuard(self.client, self.path, "1")

    async def test_actual_async_openai_transport_preserves_hook_and_refusal(self):
        # The installed OpenAI SDK uses httpx2; generic httpx hook tests above
        # cannot by themselves establish that the actual SDK invokes this hook.
        import httpx2
        from openai import APIConnectionError, AsyncOpenAI, DefaultAsyncHttpxClient

        calls = []

        async def count_transport(request):
            self.assertEqual(str(request.url), "https://api.openai.com/v1/responses/input_tokens")
            payload = json.loads(request.content)
            self.assertEqual(payload["input"], "private product information")
            self.assertEqual(payload["model"], "gpt-6-astra")
            self.assertNotIn("max_output_tokens", payload)
            calls.append("count")
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 1000})

        async def inference_transport(request):
            self.assertEqual(json.loads(self.path.read_text())["reserved_micro_usd"], 17500)
            calls.append("inference")
            return httpx2.Response(200, json={"id": "resp_mock", "object": "response", "output": []})

        async with AsyncOpenAI(
            api_key="unit-test-not-a-real-key", base_url="https://api.openai.com/v1", max_retries=0,
            http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(count_transport)),
        ) as counter:
            guard = BudgetGuard(counter, self.path, "0.017500")
            async with AsyncOpenAI(
                api_key="unit-test-not-a-real-key", base_url="https://api.openai.com/v1", max_retries=0,
                http_client=DefaultAsyncHttpxClient(
                    transport=httpx2.MockTransport(inference_transport),
                    event_hooks={"request": [guard.before_request]},
                ),
            ) as client:
                arguments = dict(model="gpt-6-astra", input="private product information",
                                 max_output_tokens=100, service_tier="default", stream=False)
                await client.responses.create(**arguments)
                with self.assertRaises((APIConnectionError, BudgetExceeded)) as error:
                    await client.responses.create(**arguments)
                if isinstance(error.exception, APIConnectionError):
                    self.assertIsInstance(error.exception.__cause__, BudgetExceeded)
        self.assertEqual(calls, ["count", "inference", "count"])
        self.assertEqual(guard.blocked_kind, "BudgetExceeded")
        self.assertIn("cap", guard.blocked_reason)


if __name__ == "__main__":
    unittest.main()
