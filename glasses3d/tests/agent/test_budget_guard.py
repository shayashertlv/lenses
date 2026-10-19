"""BudgetGuard: the Studio's 22 budget tests (source-before/tests/test_blender_agent_budget.py) ported to the glasses3d
guard, then tests for each of the seven WP2 edits (glasses3d/plan/archive/20261019/WP2_DESIGN.md section 8). Offline: counting is a mock or an
httpx2.MockTransport; no socket is opened (conftest)."""
import asyncio
from datetime import datetime
import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from glasses3d.agent import budget as B
from glasses3d.agent.budget import (LEDGER_SCHEMA, ROW_FIELDS, BudgetExceeded, BudgetGuard, BudgetGuardError,
                                    BudgetViolation, LedgerPersistError, ReconcileError, RunLedger)
from glasses3d.agent.pricing import Tariff
from glasses3d.tests.agent.support import (INFERENCE_URL, OFFLINE_KEY, fake_counter, ledger, provider_body, request,
                                           response, usage)

T = Tariff.frozen()
run = asyncio.run
DELAYS = [.01, .025, .05, .1, .2, .4, .8, 1.6]


@pytest.fixture
def env(tmp_path):
    client, count = fake_counter()
    return SimpleNamespace(path=tmp_path / "budget.json", client=client, count=count, tmp=tmp_path)


def body_with_usage(block, **changes):
    body = provider_body(**changes)
    body["usage"] = block
    return body


# =========================================================================== the 22 Studio tests, ported

@pytest.mark.parametrize("tokens,cap", [(1000, "0.020"), (300000, "7.503")])
def test_full_output_and_exact_wire_bytes_are_preserved_below_cutoff(env, tokens, cap):
    client, _ = fake_counter(tokens)
    guard = BudgetGuard(client, env.path, cap)
    req = request(max_output_tokens=4096, instructions="unaltered", tools=[])
    original = json.loads(req.content)
    run(guard.before_request(req))
    assert json.loads(req.content) == original
    assert b"".join(req.stream) == req.content
    assert int(req.headers["content-length"]) == len(req.content)
    assert req.headers["authorization"] == "Bearer private-key"
    row = ledger(env.path)["rows"][0]
    assert row["max_output_tokens"] == 4096
    assert row["reserved_micro_usd"] > guard.ledger.maximum_micro


def test_output_fitting_is_refused_before_ledger_creation(env):
    for name, cap in (("input", "0.010"), ("default", "0.020")):
        path = env.tmp / (name + ".json")
        with pytest.raises(BudgetGuardError, match="no output fitting"):
            BudgetGuard(env.client, path, cap, fit_output_to_budget=True)
        assert not path.exists() and env.count.call_count == 0


def test_requests_use_settled_balance_and_never_shrink_output(env):
    guard = BudgetGuard(env.client, env.path, "0.025")
    async def flow():
        first = request(max_output_tokens=100)
        before = first.content
        await guard.before_request(first)
        assert first.content == before
        await guard.after_response(response(body=provider_body(usage_block=usage(output=100))))
        # $0.015 settled, $0.010 left; the next input alone costs at most $0.0125.
        second = request(max_output_tokens=4096)
        before = second.content
        await guard.before_request(second)
        assert second.content == before
        await guard.after_response(response(body=provider_body(response_id="resp_second", usage_block=usage(output=100))))
        with pytest.raises(BudgetExceeded, match="Settled spend reached"):
            await guard.before_request(request())
        assert len(guard.ledger.rows) == env.count.call_count == 2
    run(flow())


def test_counts_serialized_images_tools_and_schemas_before_reserving(env):
    guard = BudgetGuard(env.client, env.path, "1")
    input_items = [{"role": "user", "content": [
        {"type": "input_image", "detail": "high", "image_url": "data:image/png;base64,SECRET"},
        {"type": "input_text", "text": "private product information"}]}]
    tools = [{"type": "function", "name": "view", "parameters": {"type": "object"}}]
    text = {"format": {"type": "json_schema", "name": "check", "schema": {"type": "object"}}}
    run(guard.before_request(request(input=input_items, tools=tools, text=text, reasoning={"effort": "max"},
                                     instructions="private instructions")))
    sent = env.count.call_args.kwargs
    assert sent["input"] == input_items and sent["tools"] == tools and sent["text"] == text
    assert sent["reasoning"] == {"effort": "max"} and sent["instructions"] == "private instructions"
    assert "max_output_tokens" not in sent and "service_tier" not in sent and "stream" not in sent
    env.client.with_options.assert_called_once_with(max_retries=0)
    assert ledger(env.path)["rows"][0]["reserved_micro_usd"] == T.reserve_micro(1000, 100)
    for secret in ("SECRET", "private", "Bearer", "data:image", "input_items"):
        assert secret not in env.path.read_text(encoding="utf-8")


@pytest.mark.timing
def test_planning_reports_numbers_before_the_cliff_without_changing_cap_or_holds(env):
    guard = BudgetGuard(env.client, env.path, "0.022")
    assert guard.planning() == {"last_counted_input_tokens": None, "max_output_tokens": None,
                                "next_hold_micro_usd": None, "remaining_micro_usd": 22_000,
                                "last_settled_micro_usd": None, "recent_high_settled_micro_usd": None, "requests": 0,
                                "last_request_bytes": None, "last_request_images": None, "last_count_ms": None}

    async def flow():
        sent = request()
        await guard.before_request(sent)
        await guard.after_response(response(body=provider_body(usage_block=usage(cached=1000, output=40))))  # $0.003
        before = env.path.read_bytes()
        planning = guard.summary()["planning"]
        count_ms = planning.pop("last_count_ms")
        assert type(count_ms) is int and 0 <= count_ms < 5_000
        assert planning == {"last_counted_input_tokens": 1000, "max_output_tokens": 100, "next_hold_micro_usd": 17_500,
                            "remaining_micro_usd": 19_000, "last_settled_micro_usd": 3_000,
                            "recent_high_settled_micro_usd": 3_000, "requests": 1,
                            "last_request_bytes": len(sent.content), "last_request_images": 0}
        assert env.path.read_bytes() == before          # planning never writes, holds or refunds
        await guard.before_request(request())           # numbers are advisory: affordable work goes on
        await guard.after_response(response(body=provider_body(response_id="resp_test_2",
                                                               usage_block=usage(cached=1000, output=40))))
        planning = guard.planning()
        assert planning["remaining_micro_usd"] == 16_000 < planning["next_hold_micro_usd"] == 17_500
        await guard.before_request(request())  # estimated liability no longer gates new requests
        assert len(guard.ledger.rows) == 3
    run(flow())


def test_audit_keeps_latest_exact_count_and_long_context_tariff_above_cutoff(env):
    guard = BudgetGuard(env.client, env.path, "1")
    env.count.return_value.input_tokens = 272_001
    run(guard.before_request(request(max_output_tokens=25_000)))
    planning = guard.planning()
    assert planning["last_counted_input_tokens"] == 272_001 and planning["max_output_tokens"] == 25_000
    assert planning["next_hold_micro_usd"] == 8_675_025
    assert guard.summary()["remaining_micro_usd"] == 1_000_000
    assert guard.summary()["committed_usd"] == "8.675025" and guard.summary()["requests"] == 1


def test_transport_sees_durable_reservation_and_never_sees_blocked_request(env):
    guard = BudgetGuard(env.client, env.path, "0.001050")
    seen = []

    async def transport(sent):
        row = ledger(env.path)["rows"][-1]
        seen.append((row["reserved_micro_usd"], row["outcome"]))
        return httpx2.Response(200, json=provider_body(usage_block=usage(cached=1000)))

    async def flow():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport), event_hooks={
                "request": [guard.before_request], "response": [guard.after_response]}) as client:
            await client.send(request())
            with pytest.raises(BudgetExceeded):
                await client.send(request())
    run(flow())
    assert seen == [(17_500, "held")]
    assert guard.summary()["remaining_usd"] == "0.000000"
    assert env.count.call_count == 1
    assert guard.summary()["blocked_kind"] == "BudgetExceeded" and guard.status_hint() == "budget_limit"


def test_payload_over_the_providers_limit_is_refused_before_counting(env, monkeypatch):
    """A body over the provider's documented per-request limit (PAYLOAD_LIMIT_BYTES 512 MB or PAYLOAD_LIMIT_IMAGES
    1,500, OpenAI's vision guide, v3p3-verify-15) is neither counted nor reserved: PayloadTooLarge, status hint
    payload_limit, $0 (review 2026-10-12, P2: request bytes and images were neither modelled nor guarded). The limits
    are the module's; the test lowers them to this request's own size."""
    assert (B.PAYLOAD_LIMIT_BYTES, B.PAYLOAD_LIMIT_IMAGES) == (512_000_000, 1_500)
    image = {"type": "input_image", "detail": "high", "image_url": "data:image/png;base64," + "A" * 4000}
    items = [{"role": "user", "content": [image, image]},
             {"type": "function_call_output", "call_id": "c1", "output": [image, {"type": "input_text", "text": "t"}]}]
    sent = request(input=items)
    assert B.payload_images(json.loads(sent.content)["input"]) == 3
    for limits, ok in (((len(sent.content), 3), True), ((len(sent.content) - 1, 3), False),
                       ((len(sent.content), 2), False)):
        monkeypatch.setattr(B, "PAYLOAD_LIMIT_BYTES", limits[0])
        monkeypatch.setattr(B, "PAYLOAD_LIMIT_IMAGES", limits[1])
        path = env.tmp / f"limit-{limits[0]}-{limits[1]}.json"
        guard = BudgetGuard(env.client, path, "1")
        calls = env.count.call_count
        if ok:                                       # at the limit: counted and reserved
            run(guard.before_request(request(input=items)))
            assert env.count.call_count == calls + 1 and len(ledger(path)["rows"]) == 1
            assert guard.planning()["last_request_images"] == 3
            continue
        with pytest.raises(B.PayloadTooLarge, match="documented limit .* inference was not sent"):
            run(guard.before_request(request(input=items)))
        assert env.count.call_count == calls and ledger(path)["rows"] == []
        assert guard.blocked_kind == "PayloadTooLarge" and guard.status_hint() == "payload_limit"


def test_count_timeout_scales_with_the_body(env):
    """The count's timeout is COUNT_TIMEOUT_S + COUNT_TIMEOUT_PER_MB_S per MB of the body (httpcore2 writes a one-chunk
    body inside one write timeout, so a fixed 60 s bounded the whole upload: the paid runs' counts took 1.27 s + 0.316
    s per MB, 60 s near 186 MB, and the v3 projections reach 108-161 MB; v3p3-verify-15). The count is sent with the
    timeout of this body, about 3 x the measured rate at every size."""
    assert (B.COUNT_TIMEOUT_S, B.COUNT_TIMEOUT_PER_MB_S) == (60.0, 1.0)
    assert B.count_timeout_s(0) == 60.0 and B.count_timeout_s(30_780_000) == pytest.approx(90.78)
    for mb in (30.8, 100, 161, 512):
        assert B.count_timeout_s(mb * 1e6) >= 3 * (1.27 + 0.316 * mb)
    guard = BudgetGuard(env.client, env.path, "1")
    sent = request(input="x" * 2_000_000)
    run(guard.before_request(sent))
    assert env.count.call_args.kwargs["timeout"] == pytest.approx(B.count_timeout_s(len(sent.content)))
    assert guard.planning()["last_request_bytes"] == len(sent.content) > 2_000_000


def test_concurrent_requests_share_one_ceiling_and_one_open_request(env):
    for cap, path in (("0.017500", env.path), ("100", env.tmp / "roomy.json")):
        guard = BudgetGuard(env.client, path, cap)

        async def flow():
            return await asyncio.gather(*(guard.before_request(request()) for _ in range(3)), return_exceptions=True)
        results = run(flow())
        assert sum(result is None for result in results) == 1
        refused = [result for result in results if result is not None]
        assert len(refused) == 2
        assert all(type(error) is BudgetGuardError and "one request at a time" in str(error) for error in refused)
        assert guard.summary()["requests"] == 1


def test_long_context_cache_write_reservation_and_cap_round_down(env):
    env.count.return_value.input_tokens = 272_001
    amount = T.reserve_micro(272_001, 100)
    guard = BudgetGuard(env.client, env.path, str(amount / 1_000_000))
    run(guard.before_request(request()))
    assert ledger(env.path)["rows"][0]["reserved_micro_usd"] == amount == 272_001 * 25 + 100 * 75
    with pytest.raises(BudgetGuardError, match="positive settled stop"):
        BudgetGuard(env.client, env.tmp / "fractional.json", "0.0000009")
    assert not (env.tmp / "fractional.json").exists()


def test_counter_failure_and_invalid_count_stop_request_without_logging_error_body(env, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(B.asyncio, "sleep", sleep)
    guard = BudgetGuard(env.client, env.path, "1")
    env.count.side_effect = RuntimeError("private-key and private prompt")
    with pytest.raises(BudgetGuardError, match="Token counting failed") as error:
        run(guard.before_request(request()))
    assert "private" not in str(error.value)
    assert env.count.await_count == 3
    assert [item.args[0] for item in sleep.await_args_list] == [0.5, 1.0]
    env.count.side_effect = None
    for invalid in (-1, True, 1.5, "1000", 922_001, None):
        env.count.return_value = SimpleNamespace(input_tokens=invalid)
        with pytest.raises(BudgetGuardError):
            run(guard.before_request(request()))
    assert guard.summary()["requests"] == 0 and not ledger(env.path)["rows"]


def test_free_count_retries_identical_payload_before_one_reservation(env, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(B.asyncio, "sleep", sleep)
    env.count.side_effect = [RuntimeError("private prompt"), TimeoutError("private key"),
                             SimpleNamespace(input_tokens=1000)]
    guard = BudgetGuard(env.client, env.path, "1")
    run(guard.before_request(request()))
    assert env.count.await_count == 3
    assert env.count.await_args_list[0] == env.count.await_args_list[1] == env.count.await_args_list[2]
    assert [item.args[0] for item in sleep.await_args_list] == [0.5, 1.0]
    assert len(guard.ledger.rows) == 1 and guard.ledger.rows[0]["outcome"] == "held"


def test_cancelled_count_is_not_retried_or_reserved(env, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(B.asyncio, "sleep", sleep)
    env.count.side_effect = asyncio.CancelledError()
    guard = BudgetGuard(env.client, env.path, "1")
    with pytest.raises(asyncio.CancelledError):
        run(guard.before_request(request()))
    assert env.count.await_count == 1 and sleep.await_count == 0 and guard.ledger.rows == []


def test_unpriced_settings_refused_before_counting(env):
    guard = BudgetGuard(env.client, env.path, "1")
    cases = [dict(model="different"), dict(model="gpt-6-luna"), dict(service_tier="auto"), dict(service_tier=None),
             dict(service_tier="flex"), dict(stream=True), dict(background=True), dict(max_output_tokens=None),
             dict(max_output_tokens=True), dict(max_output_tokens=0), dict(max_output_tokens=128_001),
             dict(max_output_tokens=1.5), dict(tools=[{"type": "web_search"}]), dict(tools="function"),
             dict(tools=[{"type": "function"}, {"type": "mcp"}]), dict(unreviewed_feature=True)]
    for changes in cases:
        with pytest.raises(BudgetGuardError):
            run(guard.before_request(request(**changes)))
    no_cap = request()
    payload = json.loads(no_cap.content)
    del payload["max_output_tokens"]
    with pytest.raises(BudgetGuardError, match="max_output_tokens"):
        run(guard.before_request(httpx2.Request("POST", INFERENCE_URL, json=payload)))
    for body in (b"[1, 2]", b"null", b"not json"):
        with pytest.raises(BudgetGuardError, match="JSON object"):
            run(guard.before_request(httpx2.Request("POST", INFERENCE_URL, content=body)))
    # the refusal names every unreviewed field (sorted), so an SDK upgrade says what to review
    with pytest.raises(BudgetGuardError, match=r"unreviewed fields for token counting: 'context_management', "
                                               r"'prompt_cache_options', 'unreviewed_feature'$"):
        run(guard.before_request(request(unreviewed_feature=True, context_management=[], prompt_cache_options={})))
    env.count.assert_not_called()


def test_unknown_endpoints_and_retry_are_refused(env):
    guard = BudgetGuard(env.client, env.path, "1")
    for url in ("https://eu.api.openai.com/v1/responses", "https://api.openai.com/v1/chat/completions",
                "https://example.com/v1/responses", "https://api.openai.com/v1/responses?override=1",
                "http://api.openai.com/v1/responses", "https://api.openai.com/v1/responses/input_tokens"):
        with pytest.raises(BudgetGuardError):
            run(guard.before_request(httpx2.Request("POST", url, json={})))
    with pytest.raises(BudgetGuardError):
        run(guard.before_request(httpx2.Request("GET", INFERENCE_URL)))
    retried = request()
    retried.headers["x-stainless-retry-count"] = "1"
    with pytest.raises(BudgetGuardError, match="retries"):
        run(guard.before_request(retried))
    env.count.assert_not_called()


def test_failure_after_reservation_never_refunds(env):
    guard = BudgetGuard(env.client, env.path, "1")
    calls = []

    async def transport(sent):
        calls.append(1)
        raise httpx2.ReadTimeout("uncertain outcome")

    async def flow():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport), event_hooks={
                "request": [guard.before_request], "response": [guard.after_response]}) as client:
            with pytest.raises(httpx2.ReadTimeout) as raised:
                await client.send(request())
            # no response hook ran: the request stays open and nothing may be sent beside it
            with pytest.raises(BudgetGuardError, match="one request at a time"):
                await client.send(request())
            row = await guard.transport_failed(raised.value)
            assert row["outcome"] == "unknown" and row["reason"] == "no provider response (ReadTimeout)"
            with pytest.raises(BudgetGuardError, match="unknown outcome"):
                await client.send(request())
    run(flow())
    assert calls == [1]
    summary = guard.summary()
    assert (summary["unknown_usd"], summary["committed_usd"], summary["settled_usd"]) == (
        "0.017500", "0.017500", "0.000000")
    assert guard.status_hint() == "provider_unknown"


def test_valid_cache_usage_releases_hold_but_keeps_reservation_history(env):
    guard = BudgetGuard(env.client, env.path, "0.02")

    async def flow():
        await guard.before_request(request())
        row = await guard.after_response(response(body=provider_body(usage_block=usage(cached=1000))))
        assert row["settled_micro_usd"] == T.settle_micro(usage(cached=1000)) == 1050
        summary = guard.summary()
        assert summary["settled_usd"] == "0.001050" and summary["committed_usd"] == "0.001050"
        await guard.before_request(request())
        summary = guard.summary()
        assert summary["committed_usd"] == "0.018550" and summary["held_usd"] == "0.017500"
    run(flow())
    rows = ledger(env.path)["rows"]
    assert (rows[0]["outcome"], rows[0]["settled_micro_usd"], rows[0]["reserved_micro_usd"]) == ("settled", 1050, 17_500)
    assert rows[1]["outcome"] == "held" and rows[1]["settled_micro_usd"] is None


def test_unknown_hold_remains_and_a_settled_request_cannot_settle_twice(env):
    guard = BudgetGuard(env.client, env.path, "0.04")

    async def flow():
        await guard.before_request(request())
        await guard.after_response(response(body=provider_body(usage_block=usage(cached=1000))))
        rows = ledger(env.path)["rows"]
        with pytest.raises(BudgetGuardError, match="without an open reservation"):
            await guard.after_response(response(body=provider_body(usage_block=usage(cached=1000))))
        assert ledger(env.path)["rows"] == rows
        with pytest.raises(BudgetGuardError, match="further inference is blocked"):
            await guard.before_request(request())
    run(flow())
    other = BudgetGuard(env.client, env.tmp / "unknown.json", "0.04")

    async def uncertain():
        await other.before_request(request())
        await other.mark_unknown("connection dropped after send")
    run(uncertain())
    summary = other.summary()
    assert (summary["unknown_usd"], summary["requests_unknown"], summary["committed_usd"]) == ("0.017500", 1, "0.017500")


@pytest.mark.parametrize("block", [None, {}, {"input_tokens": 0, "output_tokens": 0}, usage(cached=1001),
                                   usage(output=-1), dict(usage(), total_tokens=123), "usage", [usage()]])
def test_missing_and_invalid_usage_cannot_release_reservation(env, block):
    guard = BudgetGuard(env.client, env.path, "0.02")

    async def flow():
        await guard.before_request(request())
        with pytest.raises(BudgetGuardError, match="unknown|usage"):
            await guard.after_response(response(body=body_with_usage(block)))
    run(flow())
    row = ledger(env.path)["rows"][0]
    assert row["outcome"] == "unknown" and row["settled_micro_usd"] is None and row["usage"] is None
    assert guard.summary()["committed_usd"] == "0.017500" and guard.summary()["settled_usd"] == "0.000000"


def test_a_response_without_any_reservation_settles_nothing_and_blocks(env):
    guard = BudgetGuard(env.client, env.path, "1")
    with pytest.raises(BudgetGuardError, match="without an open reservation"):
        run(guard.after_response(response()))
    assert ledger(env.path)["rows"] == []
    with pytest.raises(BudgetGuardError):
        run(guard.before_request(request()))
    env.count.assert_not_called()


def test_settled_cost_above_reservation_is_recorded_and_blocks_next_request(env):
    guard = BudgetGuard(env.client, env.path, "10")

    async def flow():
        await guard.before_request(request())
        with pytest.raises(BudgetViolation, match="exceeded its reservation"):
            await guard.after_response(response(body=provider_body(usage_block=usage(output=1000))))
        data = ledger(env.path)
        assert data["violation"]["settled_micro_usd"] == T.settle_micro(usage(output=1000)) == 60_000
        assert data["rows"][0]["settled_micro_usd"] == 60_000
        calls = env.count.call_count
        with pytest.raises(BudgetGuardError, match="further inference is blocked"):
            await guard.before_request(request())
        assert env.count.call_count == calls
    run(flow())
    assert guard.summary()["settled_usd"] == "0.060000"


def test_settlement_persistence_failure_cannot_release_hold(env):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        original = env.path.read_bytes()
        with patch("glasses3d.agent.budget.os.replace", side_effect=OSError("disk unavailable")):
            with pytest.raises(BudgetGuardError, match="persistence failed"):
                await guard.after_response(response(body=provider_body(usage_block=usage(cached=1000))))
        assert env.path.read_bytes() == original
        summary = guard.summary()
        assert summary["settled_usd"] == "0.000000" and summary["committed_usd"] == "0.017500"
        with pytest.raises(BudgetGuardError):
            await guard.before_request(request())
    run(flow())


def test_transient_windows_replace_retries_one_durable_reservation(env):
    original_replace = os.replace
    for code in (5, 32, 33):
        path = env.tmp / f"sharing-{code}.json"
        guard = BudgetGuard(env.client, path, "1")
        calls, count_before = [], env.count.call_count
        error = PermissionError(errno.EACCES, "private OS path must not be logged")
        error.winerror = code

        def replace(source, target):
            calls.append((Path(source), Path(source).read_bytes()))
            if len(calls) < 3:
                assert ledger(path)["rows"] == []
                raise error
            return original_replace(source, target)

        with patch("glasses3d.agent.budget.WINDOWS", True), \
                patch("glasses3d.agent.budget.os.replace", side_effect=replace), \
                patch("glasses3d.agent.budget.time.sleep") as sleep:
            run(guard.before_request(request()))
        assert env.count.call_count == count_before + 1
        assert calls == [calls[0]] * 3, "retry the same fsynced payload"
        assert [call.args[0] for call in sleep.call_args_list] == DELAYS[:2]
        assert guard.summary()["requests"] == 1 and guard.summary()["committed_usd"] == "0.017500"
        assert ledger(path)["rows"][0]["reserved_micro_usd"] == 17_500


def test_exhausted_windows_retries_keep_hold_and_report_only_error_codes(env):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        original = env.path.read_bytes()
        error = PermissionError(errno.EACCES, "private path and private information")
        error.winerror = 32
        with patch("glasses3d.agent.budget.WINDOWS", True), \
                patch("glasses3d.agent.budget.os.replace", side_effect=error) as replace, \
                patch("glasses3d.agent.budget.time.sleep") as sleep:
            with pytest.raises(BudgetGuardError, match="errno=13, winerror=32") as raised:
                await guard.after_response(response(body=provider_body(usage_block=usage(cached=1000))))
        assert "private" not in str(raised.value)
        assert replace.call_count == 9                                      # edit (7): 8 delays, 9 attempts
        assert [call.args[0] for call in sleep.call_args_list] == DELAYS
        assert env.path.read_bytes() == original
        summary = guard.summary()
        assert summary["settled_usd"] == "0.000000" and summary["held_usd"] == "0.017500"
        assert list(env.path.parent.glob(".budget-*.json")) == []
        count_before = env.count.call_count
        with pytest.raises(BudgetGuardError):
            await guard.before_request(request())
        assert env.count.call_count == count_before
    run(flow())


def test_nonsharing_or_nonwindows_failure_is_not_retried(env):
    for windows, code in ((True, 112), (False, 32)):
        guard = BudgetGuard(env.client, env.tmp / f"other-{windows}.json", "1")
        error = OSError(errno.ENOSPC, "unavailable")
        error.winerror = code
        with patch("glasses3d.agent.budget.WINDOWS", windows), \
                patch("glasses3d.agent.budget.os.replace", side_effect=error) as replace, \
                patch("glasses3d.agent.budget.time.sleep") as sleep:
            with pytest.raises(BudgetGuardError):
                run(guard.before_request(request()))
        assert replace.call_count == 1
        sleep.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows readers can deny atomic replacement")
def test_actual_open_windows_reader_can_close_then_settlement_commits(env):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        original = env.path.read_bytes()
        reader = env.path.open("rb")            # the same normal read handle a status command uses
        original_replace, errors = os.replace, []

        def replace(source, target):
            try:
                return original_replace(source, target)
            except PermissionError as error:
                errors.append(error.winerror)
                assert reader.read() == original, "the old ledger remains intact"
                reader.close()
                raise

        try:
            with patch("glasses3d.agent.budget.os.replace", side_effect=replace):
                await guard.after_response(response(body=provider_body(usage_block=usage(cached=1000))))
        finally:
            reader.close()
        assert len(errors) == 1 and errors[0] in (5, 32, 33)
    run(flow())
    summary = guard.summary()
    assert summary["settled_usd"] == "0.001050" and summary["unknown_usd"] == "0.000000"
    assert ledger(env.path)["rows"][0]["settled_micro_usd"] == 1050


def test_ledger_write_failure_blocks_transport(env):
    guard = BudgetGuard(env.client, env.path, "1")
    transport = AsyncMock(return_value=httpx2.Response(200))

    async def flow():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport),
                                      event_hooks={"request": [guard.before_request]}) as client:
            with patch("glasses3d.agent.budget.os.replace", side_effect=OSError("disk unavailable")):
                with pytest.raises(BudgetGuardError, match="persistence failed"):
                    await client.send(request())
            # the unpersisted reservation stays in memory: it never becomes an allowance for another request
            with pytest.raises(BudgetGuardError, match="one request at a time"):
                await client.send(request())
    run(flow())
    transport.assert_not_called()
    assert ledger(env.path)["rows"] == []


def test_existing_ledger_never_resets_and_bad_configuration_fails(env):
    BudgetGuard(env.client, env.path, "1")
    original = env.path.read_bytes()
    with pytest.raises(FileExistsError):
        BudgetGuard(env.client, env.path, "10")
    assert env.path.read_bytes() == original
    for invalid in (True, -1, "NaN", "Infinity", "not money", None, float("nan")):
        with pytest.raises(BudgetGuardError):
            BudgetGuard(env.client, env.path, invalid)
    env.client.base_url = "https://eu.api.openai.com/v1/"
    with pytest.raises(BudgetGuardError):
        BudgetGuard(env.client, env.tmp / "eu.json", "1")
    assert not (env.tmp / "eu.json").exists()


def test_actual_async_openai_transport_preserves_hook_and_refusal(env):
    # The installed OpenAI SDK uses httpx2; this establishes that the SDK itself invokes both hooks.
    from openai import APIConnectionError, AsyncOpenAI, DefaultAsyncHttpxClient

    calls = []

    async def count_transport(sent):
        assert str(sent.url) == "https://api.openai.com/v1/responses/input_tokens"
        payload = json.loads(sent.content)
        assert payload["input"] == "private product information" and payload["model"] == "gpt-6-astra"
        assert "max_output_tokens" not in payload and "service_tier" not in payload
        calls.append("count")
        return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 1000})

    async def inference_transport(sent):
        row = ledger(env.path)["rows"][-1]
        assert (row["reserved_micro_usd"], row["outcome"]) == (17_500, "held")
        assert sent.headers["x-stainless-retry-count"] == "0"
        calls.append("inference")
        return httpx2.Response(200, json=provider_body(response_id="resp_mock", usage_block=usage(cached=1000)),
                               headers={"x-request-id": "req_mock"})

    async def flow():
        async with AsyncOpenAI(api_key=OFFLINE_KEY, base_url="https://api.openai.com/v1", max_retries=0,
                               http_client=DefaultAsyncHttpxClient(
                                   transport=httpx2.MockTransport(count_transport))) as counter:
            guard = BudgetGuard(counter, env.path, "0.001050")
            async with AsyncOpenAI(api_key=OFFLINE_KEY, base_url="https://api.openai.com/v1", max_retries=0,
                                   http_client=DefaultAsyncHttpxClient(
                                       transport=httpx2.MockTransport(inference_transport),
                                       event_hooks={"request": [guard.before_request],
                                                    "response": [guard.after_response]})) as client:
                arguments = dict(model="gpt-6-astra", input="private product information", max_output_tokens=100,
                                 service_tier="default", stream=False)
                created = await client.responses.create(**arguments)
                assert created.id == "resp_mock"
                with pytest.raises((APIConnectionError, BudgetExceeded)) as error:
                    await client.responses.create(**arguments)
                if isinstance(error.value, APIConnectionError):
                    assert isinstance(error.value.__cause__, BudgetExceeded)
        return guard
    guard = run(flow())
    assert calls == ["count", "inference"]
    assert guard.blocked_kind == "BudgetExceeded" and "Settled spend reached" in guard.blocked_reason
    assert "$0.001050" in guard.blocked_reason and "no inference sent" in guard.blocked_reason
    row = ledger(env.path)["rows"][0]
    assert (row["response_id"], row["request_id"], row["settled_micro_usd"]) == ("resp_mock", "req_mock", 1050)


# =========================================================================== edit (1): the g3d.run_budget/1 ledger


def test_edit1_ledger_schema_and_exact_row_fields(env):
    guard = BudgetGuard(env.client, env.path, "1", run_id="20261007T090000-abc123")
    data = ledger(env.path)
    assert data["schema"] == LEDGER_SCHEMA == "g3d.run_budget/1"
    assert (data["run_id"], data["maximum_micro_usd"], data["rows"], data["violation"], data["blocked"]) == (
        "20261007T090000-abc123", 1_000_000, [], None, None)
    assert data["tariff"] == T.to_dict()

    async def flow():
        await guard.before_request(request())
        held = ledger(env.path)["rows"][0]
        assert tuple(held) == ROW_FIELDS
        assert held["outcome"] == "held" and all(held[key] is None for key in (
            "request_id", "response_id", "http_status", "settled_micro_usd", "usage", "settled_from", "reason"))
        await guard.after_response(response(body=provider_body(response_id="resp_e1", usage_block=usage(cached=1000)),
                                            request_id="req_e1"))
    run(flow())
    row = ledger(env.path)["rows"][0]
    assert tuple(row) == ROW_FIELDS
    datetime.fromisoformat(row["time"])
    assert {key: row[key] for key in ROW_FIELDS if key != "time"} == {
        "sequence": 1, "input_tokens": 1000, "max_output_tokens": 100, "reserved_micro_usd": 17_500,
        "outcome": "settled", "request_id": "req_e1", "response_id": "resp_e1", "http_status": 200,
        "settled_micro_usd": 1050, "usage": {"input_tokens": 1000, "cached_tokens": 1000, "cache_write_tokens": 0,
                                             "output_tokens": 1, "reasoning_tokens": 0},
        "settled_from": "transport", "reason": None}
    assert RunLedger.load(env.path).totals()["settled_micro_usd"] == 1050


# =========================================================================== edit (2): settle from the transport


def terminal_usage():
    return {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
            "input_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 100},
            "output_tokens_details": {"reasoning_tokens": 20}}


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_edit2_every_settleable_200_settles_from_its_own_usage(env, status):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        return await guard.after_response(response(body=provider_body(response_id="resp_terminal", status=status,
                                                                      usage_block=terminal_usage())))
    row = run(flow())
    assert (row["outcome"], row["settled_from"], row["settled_micro_usd"], row["response_id"]) == (
        "settled", "transport", 7150, "resp_terminal")


def sdk_response(response_id, block):
    return SimpleNamespace(response_id=response_id, usage=block, output=[])


def test_edit2_on_llm_end_cross_checks_and_never_settles(env):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        await guard.after_response(response(body=provider_body(response_id="resp_x", usage_block=terminal_usage())))
        saved = env.path.read_bytes()
        await guard.on_llm_end(sdk_response("resp_x", terminal_usage()))      # the SDK agrees: nothing changes
        await guard.on_llm_end(sdk_response("resp_x", terminal_usage()))      # and never settles twice
        assert env.path.read_bytes() == saved
        assert guard.summary()["settled_micro_usd"] == 7150
        with pytest.raises(BudgetGuardError, match="response id differs"):
            await guard.on_llm_end(sdk_response("resp_different", terminal_usage()))
        assert ledger(env.path)["rows"] == json.loads(saved)["rows"]
        assert ledger(env.path)["blocked"]["kind"] == "BudgetGuardError"
        calls = env.count.call_count
        with pytest.raises(BudgetGuardError, match="cross-check failed"):
            await guard.before_request(request())
        assert env.count.call_count == calls
    run(flow())


@pytest.mark.parametrize("sdk", [
    lambda: sdk_response("resp_x", dict(terminal_usage(), output_tokens=101, total_tokens=1101)),
    lambda: sdk_response("resp_x", dict(terminal_usage(), input_tokens_details={"cached_tokens": 1000})),
    lambda: sdk_response("resp_x", None),
    lambda: sdk_response(None, terminal_usage())])
def test_edit2_on_llm_end_blocks_on_a_usage_or_id_mismatch(env, sdk):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        await guard.after_response(response(body=provider_body(response_id="resp_x", usage_block=terminal_usage())))
        with pytest.raises(BudgetGuardError, match="cross-check failed"):
            await guard.on_llm_end(sdk())
        with pytest.raises(BudgetGuardError):
            await guard.before_request(request())
    run(flow())
    assert guard.summary()["settled_micro_usd"] == 7150      # the transport's settlement stands


def test_edit2_on_llm_end_for_an_unsettled_response_blocks(env):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        with pytest.raises(BudgetGuardError):
            await guard.after_response(response(body=body_with_usage(None, response_id="resp_bad")))
        with pytest.raises(BudgetGuardError, match="did not settle"):
            await guard.on_llm_end(sdk_response("resp_bad", terminal_usage()))
    run(flow())
    assert guard.summary()["unknown_usd"] == "0.017500"


# =========================================================================== edit (3): 4xx, 5xx, unusable, no response


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 409, 413, 422, 429])
def test_edit3_a_4xx_settles_at_zero_and_records_only_identifiers(env, status):
    guard = BudgetGuard(env.client, env.path, "1")
    body = {"error": {"type": "insufficient_quota", "code": "insufficient_quota",
                      "message": "private prompt quoted by the provider"}}

    async def flow():
        await guard.before_request(request())
        row = await guard.after_response(response(status, body, request_id="req_4xx"))
        assert (row["outcome"], row["settled_micro_usd"], row["http_status"], row["request_id"], row["settled_from"],
                row["usage"]) == ("rejected_4xx", 0, status, "req_4xx", "transport", None)
        assert row["reason"] == f"http {status}: insufficient_quota insufficient_quota"
        assert guard.status_hint() == "provider_rejected"
        await guard.before_request(request())          # $0 and a recorded outcome: the loop decides what follows
    run(flow())
    assert "private" not in env.path.read_text(encoding="utf-8")
    summary = guard.summary()
    assert (summary["settled_usd"], summary["requests_rejected_4xx"], summary["held_usd"]) == ("0.000000", 1, "0.017500")


def test_edit3_a_4xx_with_a_non_json_body_still_settles_at_zero(env):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        return await guard.after_response(response(429, content=b"Too Many Requests"))
    row = run(flow())
    assert (row["outcome"], row["settled_micro_usd"], row["reason"]) == ("rejected_4xx", 0, "http 429")


@pytest.mark.parametrize("status,content", [(500, None), (502, b"gateway unavailable"), (503, b""), (520, None),
                                            (504, b"null"), (302, b""), (201, None), (204, b"")])
def test_edit3_a_non_200_non_4xx_keeps_the_full_hold_as_unknown_and_ends_spending(env, status, content):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        row = await guard.after_response(response(status, {"error": {"type": "server_error"}}, content=content))
        assert (row["outcome"], row["settled_micro_usd"], row["http_status"], row["reason"]) == (
            "unknown", None, status, f"http {status}")
        with pytest.raises(BudgetGuardError, match="unknown outcome"):
            await guard.before_request(request())
    run(flow())
    assert env.count.call_count == 1
    summary = guard.summary()
    assert (summary["unknown_usd"], summary["committed_usd"]) == ("0.017500", "0.017500")
    assert guard.status_hint() == "provider_unknown"


@pytest.mark.parametrize("make,expect", [
    (lambda: response(content=b"<html>not json</html>"), "not a JSON object"),
    (lambda: response(content=b"[1, 2]"), "not a JSON object"),
    (lambda: response(body=provider_body(model="gpt-6-luna")), "does not name gpt-6-astra"),
    (lambda: response(body={k: v for k, v in provider_body().items() if k != "id"}), "no valid response id"),
    (lambda: response(body=provider_body(response_id="resp with spaces")), "no valid response id"),
    (lambda: response(body=provider_body(status="failed")), "status failed"),
    (lambda: response(body=provider_body(status="in_progress")), "status in_progress"),
    (lambda: response(body=provider_body(status="cancelled")), "status cancelled"),
    (lambda: response(body=provider_body(status="queued")), "status queued")])
def test_edit3_an_unusable_200_keeps_the_full_hold_and_raises(env, make, expect):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        with pytest.raises(BudgetGuardError, match=expect):
            await guard.after_response(make())
        with pytest.raises(BudgetGuardError, match="unknown outcome"):
            await guard.before_request(request())
    run(flow())
    row = ledger(env.path)["rows"][0]
    assert (row["outcome"], row["http_status"], row["settled_micro_usd"]) == ("unknown", 200, None)
    assert guard.summary()["unknown_micro_usd"] == 17_500


@pytest.mark.parametrize("failure", [httpx2.ConnectError("dropped"), httpx2.ReadTimeout("timeout"),
                                     httpx2.RemoteProtocolError("server disconnected"), httpx2.WriteError("reset")])
def test_edit3_a_connection_error_or_timeout_is_unknown_with_exactly_one_request(env, failure):
    guard = BudgetGuard(env.client, env.path, "1")
    calls = []

    async def transport(sent):
        calls.append(1)
        raise failure

    async def flow():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport), event_hooks={
                "request": [guard.before_request], "response": [guard.after_response]}) as client:
            with pytest.raises(type(failure)):
                await client.send(request())
            await guard.transport_failed(failure)
            with pytest.raises(BudgetGuardError):
                await client.send(request())
            assert await guard.transport_failed(failure) is None        # nothing open any more
    run(flow())
    assert calls == [1]
    row = ledger(env.path)["rows"][0]
    assert (row["outcome"], row["reason"], row["http_status"]) == (
        "unknown", f"no provider response ({type(failure).__name__})", None)
    assert guard.summary()["unknown_usd"] == "0.017500"


def test_edit3_a_response_for_another_request_is_refused(env):
    guard = BudgetGuard(env.client, env.path, "1")
    reserved = request()

    async def flow():
        await guard.before_request(reserved)
        stray = httpx2.Response(200, json=provider_body(), request=request())
        with pytest.raises(BudgetGuardError, match="does not belong"):
            await guard.after_response(stray)
        assert guard.ledger.rows[0]["outcome"] == "held"
        await guard.mark_unknown("the run ended")
    run(flow())
    assert guard.summary()["unknown_usd"] == "0.017500"


# =========================================================================== edit (4): one open request


def test_edit4_one_open_request_at_a_time(env):
    guard = BudgetGuard(env.client, env.path, "100")

    async def flow():
        assert guard.open_sequence is None
        await guard.before_request(request())
        assert guard.open_sequence == 1
        with pytest.raises(BudgetGuardError, match="Request 1 has no recorded outcome yet; one request at a time"):
            await guard.before_request(request())
        assert env.count.call_count == 1                 # refused before counting
        await guard.after_response(response())
        assert guard.open_sequence is None
        await guard.before_request(request())             # an outcome is recorded: the next one may go
        assert guard.open_sequence == 2
    run(flow())
    assert [row["outcome"] for row in ledger(env.path)["rows"]] == ["settled", "held"]


# =========================================================================== edit (5): planning numbers only


def test_edit5_planning_returns_numbers_only(env):
    guard = BudgetGuard(env.client, env.path, "1")

    def numbers_only(planning):
        return all(value is None or type(value) is int for value in planning.values())

    assert numbers_only(guard.planning())

    async def flow():
        await guard.before_request(request())
        await guard.after_response(response())
    run(flow())
    planning = guard.planning()
    assert numbers_only(planning) and planning["next_hold_micro_usd"] == 17_500
    assert not {"status", "action", "basis", "advisory_only"} & set(planning)
    assert "finalize" not in json.dumps(guard.summary())


# =========================================================================== edit (6): owner reconcile


def test_edit6_reconcile_unknown_with_dashboard_usage_or_no_charge(env):
    guard = BudgetGuard(env.client, env.path, "1")
    words = "Owner: the dashboard shows that request cost $0.00105, reconcile it  (exact words)"

    async def flow():
        await guard.before_request(request())
        await guard.mark_unknown("connection dropped after send")
        for bad in (dict(owner_words="", usage=usage(cached=1000)), dict(owner_words="   ", no_charge=True),
                    dict(owner_words=words), dict(owner_words=words, usage=usage(), no_charge=True),
                    dict(owner_words=words, usage={"input_tokens": -1, "output_tokens": 0})):
            with pytest.raises(ReconcileError):
                await guard.reconcile_unknown(1, **bad)
        assert await guard.reconcile_unknown(1, words, usage(cached=1000)) == 1050
        with pytest.raises(ReconcileError, match="only an unknown hold"):
            await guard.reconcile_unknown(1, words, no_charge=True)
        with pytest.raises(ReconcileError, match="no request"):
            await guard.reconcile_unknown(2, words, no_charge=True)
    run(flow())
    data = ledger(env.path)
    row = data["rows"][0]
    assert (row["outcome"], row["settled_from"], row["reason"], row["settled_micro_usd"]) == (
        "reconciled", "owner", words, 1050)
    assert [entry["sequence"] for entry in data["reconciliations"]] == [1]
    assert guard.summary()["committed_micro_usd"] == 1050 and guard.summary()["unknown_micro_usd"] == 0
    with pytest.raises(BudgetGuardError, match="unknown outcome"):   # reconciled money never restarts this run
        run(guard.before_request(request()))
    nothing = BudgetGuard(env.client, env.tmp / "no-charge.json", "1")

    async def no_charge():
        await nothing.before_request(request())
        await nothing.after_response(response(520))
        return await nothing.reconcile_unknown(1, "Owner: nothing was charged for that one", no_charge=True)
    assert run(no_charge()) == 0
    row = ledger(env.tmp / "no-charge.json")["rows"][0]
    assert (row["outcome"], row["settled_micro_usd"], row["usage"], row["http_status"]) == ("reconciled", 0, None, 520)


def test_edit6_reconcile_from_transport_releases_the_oakley_run1_hold(env):
    # Oakley run 1 (job 3dff8674, request 7): the settlement write failed after a valid 200 and its $0.707375 hold
    # stayed forever. The archived usage (token counts from that run's transport/0007-response.json) settles
    # $0.036967, releasing $0.670408 (glasses3d/plan/archive/20261019/WP2_DESIGN.md section 8, edit 6).
    env.count.return_value = SimpleNamespace(input_tokens=23_822)
    guard = BudgetGuard(env.client, env.path, "18")
    archived = provider_body(response_id="resp_oakley_run1_7",
                             usage_block=usage(tokens=23_822, cached=22_974, written=845, output=68))
    words = "Owner: settle request 1 from its archived response"

    async def flow():
        await guard.before_request(request(max_output_tokens=8192))
        with patch("glasses3d.agent.budget.os.replace", side_effect=OSError("disk unavailable")):
            with pytest.raises(LedgerPersistError):
                await guard.after_response(response(body=archived))
        assert (guard.ledger.rows[0]["outcome"], guard.ledger.rows[0]["reserved_micro_usd"]) == ("held", 707_375)
        with pytest.raises(ReconcileError, match="only an unknown hold"):   # still in flight for a live guard
            await guard.reconcile_from_transport(1, words, archived, http_status=200)
        await guard.mark_unknown("the settlement write failed")
        refused = [(dict(archived), 500), (dict(archived, status="in_progress"), 200),
                   (dict(archived, model="gpt-6-luna"), 200), (dict(archived, id=None), 200),
                   (body_with_usage(usage(tokens=23_821, cached=22_974, written=845, output=68)), 200),
                   (body_with_usage({"input_tokens": 23_822}), 200), ([archived], 200)]
        for bad, status in refused:
            with pytest.raises(ReconcileError):
                await guard.reconcile_from_transport(1, words, bad, http_status=status)
        with pytest.raises(ReconcileError):
            await guard.reconcile_from_transport(1, "", archived, http_status=200)
        return await guard.reconcile_from_transport(1, words, archived, http_status=200, request_id="req_oakley")
    assert run(flow()) == 36_967
    row = ledger(env.path)["rows"][0]
    assert (row["outcome"], row["settled_from"], row["response_id"], row["request_id"], row["http_status"]) == (
        "reconciled", "transport", "resp_oakley_run1_7", "req_oakley", 200)
    assert row["reserved_micro_usd"] - row["settled_micro_usd"] == 670_408
    assert RunLedger.load(env.path).totals()["committed_micro_usd"] == 36_967


def test_edit6_reconcile_from_transport_requires_the_recorded_response_id(env):
    guard = BudgetGuard(env.client, env.path, "1")

    async def flow():
        await guard.before_request(request())
        with pytest.raises(BudgetGuardError):     # a valid id but unusable usage: unknown, the id recorded
            await guard.after_response(response(body=body_with_usage({"input_tokens": 1000}, response_id="resp_a")))
        assert guard.ledger.rows[0]["response_id"] == "resp_a"
        with pytest.raises(ReconcileError, match="id differs"):
            await guard.reconcile_from_transport(1, "Owner: settle it", provider_body(response_id="resp_b"),
                                                 http_status=200)
        return await guard.reconcile_from_transport(1, "Owner: settle it", provider_body(response_id="resp_a"),
                                                    http_status=200)
    assert run(flow()) == T.settle_micro(usage())


# =========================================================================== edit (7): the Windows retry schedule


def test_edit7_replace_retry_schedule_is_the_design_schedule(tmp_path):
    assert B._REPLACE_DELAYS == (.01, .025, .05, .1, .2, .4, .8, 1.6)
    assert round(sum(B._REPLACE_DELAYS), 3) == 3.185
    target, source = tmp_path / "t.json", tmp_path / "s.json"
    original_replace = os.replace
    for failures, succeeds in ((8, True), (9, False)):
        source.write_text("{}")
        attempts = []
        denied = PermissionError(errno.EACCES, "denied")
        denied.winerror = 5

        def replace(a, b):
            attempts.append(1)
            if len(attempts) <= failures:
                raise denied
            return original_replace(a, b)

        with patch("glasses3d.agent.budget.WINDOWS", True), \
                patch("glasses3d.agent.budget.os.replace", side_effect=replace), \
                patch("glasses3d.agent.budget.time.sleep") as sleep:
            if succeeds:
                B.replace_with_retry(source, target)
            else:
                with pytest.raises(PermissionError):
                    B.replace_with_retry(source, target)
        assert len(attempts) == min(failures + 1, 9)
        assert [call.args[0] for call in sleep.call_args_list] == DELAYS[:failures if succeeds else 8]
        assert target.exists() == succeeds
        target.unlink(missing_ok=True)


# =========================================================================== attaching to a JobLedger-created ledger


def test_attach_only_to_a_fresh_empty_ledger(env):
    fresh = RunLedger.create(env.path, "1", run_id="r1")
    guard = BudgetGuard.attach(env.client, fresh)
    assert guard.ledger is fresh and fresh.live
    with pytest.raises(BudgetGuardError, match="attaches only"):
        BudgetGuard.attach(env.client, fresh)                       # already attached
    with pytest.raises(BudgetGuardError, match="attaches only"):
        BudgetGuard.attach(env.client, RunLedger.load(env.path))    # loaded from disk: maybe a prior run's ledger
    other = RunLedger.create(env.tmp / "eu.json", "1")
    env.client.base_url = "https://eu.api.openai.com/v1/"
    with pytest.raises(BudgetGuardError, match="global"):
        BudgetGuard.attach(env.client, other)
    env.client.base_url = "https://api.openai.com/v1/"
    run(guard.before_request(request()))
    assert RunLedger.load(env.path).rows[0]["outcome"] == "held"
