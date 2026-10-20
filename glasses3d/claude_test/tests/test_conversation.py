"""Offline checks of durable Messages epochs, forecast arithmetic and renewal."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from glasses3d.agent.receipts import BlobStore
from glasses3d.agent.renewal import SUMMARY_REQUEST, durable_receipts
from glasses3d.agent.session_inputs import ReferenceInput
from glasses3d.claude_test.conversation import (
    ClaudeReviewLog, Conversation, canonical_bytes, context_measurement, estimate_content,
    opening_content, pending_tool_ids, pressure, renew_conversation, request_body,
)
from glasses3d.claude_test.images import FileCache, check_request, image_block


def run(coroutine):
    return asyncio.run(coroutine)


def png(w=13, h=11, color=(12, 56, 99)):
    output = io.BytesIO()
    Image.new("RGB", (w, h), color).save(output, format="PNG")
    return output.getvalue()


def reference(data, index=1):
    return ReferenceInput(f"Reference {index}", f"images/ref-{index}.png",
                          "data:image/png;base64," + base64.b64encode(data).decode(),
                          view="front", size_px=Image.open(io.BytesIO(data)).size)


def spec(*photos):
    return SimpleNamespace(
        settings=SimpleNamespace(model="claude-opus-5-5", effort="max", max_tokens=128_000),
        instructions="Effective Claude instructions", brief="Owner words unchanged.",
        photos=photos, owner_notes=("Keep these exact words.",), tools=(),
    )


def cache(tmp_path):
    uploaded = []

    def upload(data, filename):
        uploaded.append((data, filename))
        return f"file_{len(uploaded)}"

    return FileCache(tmp_path / "file-cache.json", upload), uploaded


def conversation(tmp_path):
    return Conversation(tmp_path / "session.sqlite", blobs=BlobStore(tmp_path / "blobs"))


def user(text="opening"):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant(*blocks):
    return {"role": "assistant", "content": list(blocks)}


def call(call_id="call_1"):
    return {"type": "tool_use", "id": call_id, "name": "scene_summary", "input": {}}


def result(call_id="call_1"):
    return {"type": "tool_result", "tool_use_id": call_id, "content": "done"}


def test_prefix_survives_reopen_and_preserves_every_thinking_field(tmp_path):
    tmp_path = tmp_path / "new" / "session"

    async def scenario():
        c = conversation(tmp_path)
        await c.append(user())
        first = request_body(spec(), await c.messages(), tools=[])
        checked = await c.assert_request_prefix(first)
        assert checked["previous_messages"] == 0
        thinking = {"type": "thinking", "thinking": "", "signature": "opaque==",
                    "future_binding": {"data": [None, 17, "טקסט"]}}
        redacted = {"type": "redacted_thinking", "data": "opaque-redacted"}
        await c.append(assistant(thinking, redacted, call()))
        await c.append({"role": "user", "content": [result()]})
        c.close()
        c = conversation(tmp_path)
        second = request_body(spec(), await c.messages(), tools=[])
        assert second["messages"][:len(first["messages"])] == first["messages"]
        assert second["messages"][1]["content"][:2] == [thinking, redacted]
        checked = await c.assert_request_prefix(second)
        assert checked["previous_messages"] == 1
        second["messages"][1]["content"][0]["signature"] = "mutated"
        with pytest.raises(ValueError, match="durable"):
            await c.assert_request_prefix(second)
        assert (await c.messages())[1]["content"][0]["signature"] == "opaque=="
        c.close()
    run(scenario())


@pytest.mark.parametrize("field,value", [
    ("model", "another"), ("system", "changed"), ("tools", [{"name": "new"}]),
    ("cache_control", {"type": "ephemeral", "ttl": "5m"}),
    ("tool_choice", {"type": "none"}), ("output_config", {"effort": "high"}),
])
def test_policy_is_immutable_within_epoch(tmp_path, field, value):
    async def scenario():
        c = conversation(tmp_path)
        try:
            await c.append(user())
            body = request_body(spec(), await c.messages(), tools=[])
            await c.assert_request_prefix(body)
            body[field] = value
            with pytest.raises(ValueError, match="policy"):
                await c.assert_request_prefix(body)
        finally:
            c.close()
    run(scenario())


def test_append_copies_and_refuses_result_reordering(tmp_path):
    async def scenario():
        c = conversation(tmp_path)
        try:
            source = user()
            await c.append(source)
            source["content"][0]["text"] = "mutated"
            assert (await c.messages())[0]["content"][0]["text"] == "opening"
            await c.append(assistant(call("a"), call("b")))
            for content in ([result("b"), result("a")], [result("a")],
                            [{"type": "text", "text": "late results"}, result("a"), result("b")]):
                with pytest.raises(ValueError, match="immediately"):
                    await c.append({"role": "user", "content": content})
            with pytest.raises(ValueError, match="pending"):
                await c.assert_request_prefix(request_body(spec(), await c.messages(), tools=[]))
            await c.append({"role": "user", "content": [result("a"), result("b")]})
            assert pending_tool_ids(await c.messages()) == []
        finally:
            c.close()
    run(scenario())


def test_atomic_renewal_archives_epochs_and_recovers_completed_ids(tmp_path):
    async def scenario():
        c = conversation(tmp_path)
        await c.append(user())
        await c.assert_request_prefix(request_body(spec(), await c.messages(), tools=[]))
        await c.append(assistant(call()))
        with pytest.raises(ValueError, match="pending"):
            await c.renew([user("fresh")], receipt={})
        assert await c.repair_dangling_calls() == ["call_1"]
        receipt = {"schema": "g3d.conversation_renewal/1", "old_conversation_id": c.session_id}
        new_id = await c.renew([user("fresh")], receipt=receipt)
        assert receipt["new_conversation_id"] == new_id
        c.close()
        c = conversation(tmp_path)
        try:
            assert c.session_id == new_id
            assert await c.messages() == [user("fresh")]
            assert await c.recovery_results() == [{"type": "function_call_output", "call_id": "call_1"}]
            assert durable_receipts(c.db_path) == [receipt]
            body = request_body(spec(), await c.messages(), tools=[])
            body["system"] = "new epoch policy"
            assert (await c.assert_request_prefix(body))["previous_messages"] == 0
        finally:
            c.close()
    run(scenario())


def test_failed_epoch_transaction_keeps_old_pointer_and_rows(tmp_path, monkeypatch):
    async def scenario():
        c = conversation(tmp_path)
        try:
            await c.append(user())
            original = c._session._insert_items

            def fail_after_insert(conn, items):
                original(conn, items)
                raise OSError("disk failure")

            monkeypatch.setattr(c._session, "_insert_items", fail_after_insert)
            with pytest.raises(OSError):
                await c.renew([user("fresh")], receipt={})
            assert c.session_id == "claude"
            assert await c.messages() == [user()]
            assert durable_receipts(c.db_path) == []
        finally:
            c.close()
    run(scenario())


def test_corrupt_session_row_is_not_silently_dropped(tmp_path):
    async def scenario():
        c = conversation(tmp_path)
        try:
            await c.append(user())
            with c._session._write_connection() as conn:
                conn.execute("UPDATE agent_messages SET message_data=?", ("{broken-json",))
                conn.commit()
            with pytest.raises(ValueError):
                await c.messages()
        finally:
            c.close()
    run(scenario())


@pytest.mark.parametrize("bad", [
    {"type": "thinking", "thinking": "", "signature": "signed"},
    {"type": "redacted_thinking", "data": "signed"},
    call(), result(),
])
def test_no_bound_content_crosses_new_epoch(tmp_path, bad):
    async def scenario():
        c = conversation(tmp_path)
        try:
            await c.append(user())
            with pytest.raises(ValueError, match="cannot cross"):
                await c.renew([{"role": "user", "content": [bad]}], receipt={})
        finally:
            c.close()
    run(scenario())


def test_opening_keeps_whole_reference_bytes_and_verbatim_inputs(tmp_path):
    files, uploaded = cache(tmp_path)
    data = png(1920, 1280)
    content, receipt = opening_content(spec(reference(data)), files=files)
    assert uploaded[0][0] == data
    assert len(receipt["images"]) == 1
    assert receipt["images"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert receipt["images"][0]["size_px"] == [1920, 1280]
    assert receipt["images"][0]["x0"] == receipt["images"][0]["y0"] == 0
    assert content[0]["text"] == "Blender product brief (verbatim):\nOwner words unchanged."
    assert content[-1]["text"] == "Owner note (verbatim):\nKeep these exact words."
    image_index = next(i for i, b in enumerate(content) if b["type"] == "image")
    assert content[image_index - 1]["type"] == "text"
    assert content[image_index] == image_block("file_1")
    assert check_request({"messages": [{"role": "user", "content": content}]},
                         {"file_1": (1920, 1280)}) == []
    opening_content(spec(reference(data)), files=files)
    assert len(uploaded) == 1


def test_oversize_owner_photo_refuses_without_upload_or_tiling(tmp_path):
    files, uploaded = cache(tmp_path)
    with pytest.raises(ValueError, match="owner reference must fit whole"):
        opening_content(spec(reference(png(2100, 20))), files=files)
    assert uploaded == []


def test_request_policy_matches_binding_amendments():
    body = request_body(spec(), [user()], tools=[{"name": "x", "input_schema": {}}])
    assert body["max_tokens"] == 128_000
    assert body["output_config"] == {"effort": "max"}
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["service_tier"] == "standard_only"
    assert body["inference_geo"] == "global"
    assert body["speed"] == "standard"
    assert "context_management" not in body
    assert "betas" not in body
    assert "fallbacks" not in body
    assert "strict" not in body["tools"][0]


def test_local_measurement_counts_utf8_images_and_each_occurrence():
    content = [{"type": "text", "text": "שלום"}, image_block("f"), image_block("f"),
               {"type": "tool_result", "tool_use_id": "a",
                "content": [{"type": "text", "text": "four"}]}]
    estimate = estimate_content(content, {"f": (1920, 1280)})
    assert estimate == {"text_bytes": 12, "text_tokens": 5, "images": 2,
                        "image_tokens": 6348, "total_tokens": 6353}
    measurement = context_measurement(
        last_usage={"input_tokens": 70, "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 100, "output_tokens": 40},
        appended_estimate=estimate,
    )
    assert measurement["forecast_tokens"] == 220 + 40 + 6353
    assert measurement["count_tokens_called"] is False
    assert context_measurement(last_usage=None, appended_estimate=estimate)["forecast_tokens"] == 6353
    with pytest.raises(ValueError, match="known"):
        estimate_content(image_block("unknown"), {})


@pytest.mark.parametrize("images,tokens,expected", [
    (480, 800_000, []), (481, 800_000, ["images"]),
    (480, 800_001, ["input_tokens"]), (481, 800_001, ["images", "input_tokens"]),
])
def test_exact_image_and_context_soft_boundaries(images, tokens, expected):
    body = {"messages": [{"role": "user", "content": [image_block("f")] * images}]}
    assert [r["reason"] for r in pressure(body, dims={"f": (1, 1)},
                                        measurement={"forecast_tokens": tokens})] == expected


def test_serialized_byte_soft_boundary_is_inclusive():
    body = {"messages": [{"role": "user", "content": ""}]}
    overhead = len(json.dumps(body, separators=(",", ":")).encode())
    body["messages"][0]["content"] = "x" * (25_600_000 - overhead - 1)
    assert pressure(body, dims={}, measurement={"forecast_tokens": 1}) == []
    body["messages"][0]["content"] += "x"
    assert pressure(body, dims={}, measurement={"forecast_tokens": 1}) == [{
        "reason": "payload_bytes", "measured": 25_600_000, "threshold": 25_600_000, "limit": 32_000_000,
    }]


def test_review_log_keeps_raw_signatures_and_only_public_summaries(tmp_path):
    heartbeat_updates = []
    log = ClaudeReviewLog(tmp_path / "run", blobs_dir=tmp_path / "blobs",
                          heartbeat=SimpleNamespace(update=lambda **kw: heartbeat_updates.append(kw)))
    raw = {
        "id": "msg_1", "model": "claude-opus-5-5", "stop_reason": "tool_use",
        "content": [
            {"type": "thinking", "thinking": "Public summary", "signature": "must-remain-exact"},
            {"type": "redacted_thinking", "data": "opaque-hidden"},
            {"type": "text", "text": "Hello"}, call(),
        ], "usage": {"input_tokens": 1, "output_tokens": 2},
        "future_field": {"binding": "unchanged"},
    }
    original = copy.deepcopy(raw)
    log.record_message(request_no=1, request_id="req_1", raw_response=raw,
                       transport_receipt=tmp_path / "run" / "transport" / "wire.json")
    assert json.loads((tmp_path / "run" / "transport" / "0001-claude-response.json").read_text()) == original
    assert raw == original
    public = log.transcript_path.read_text(encoding="utf-8")
    assert "Claude run" in public and "Provider: anthropic" in public and "claude-opus-5-5" in public
    assert "Public summary" in public
    assert "must-remain-exact" not in public and "opaque-hidden" not in public and "Astra" not in public
    summaries = json.loads(log.summaries_path.read_text(encoding="utf-8"))
    assert summaries["summaries"] == ["Public summary"]
    facts = {"settled_usd": "0.0000001", "cutoff_usd": "1", "left_usd": "0.9999999"}
    log.publish_money(facts)
    assert heartbeat_updates == [facts]
    with pytest.raises(ValueError, match="exactly"):
        log.publish_money({**facts, "reserved_usd": "0"})
    with pytest.raises(ValueError, match="overwrite"):
        log.record_message(request_no=1, request_id="req_1", raw_response=raw,
                           transport_receipt=tmp_path / "wire.json")


def runtime_fixture(tmp_path, *, summary_blocks=None, allowed=True, reason="below cutoff"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    files, uploaded = cache(tmp_path)
    scene_events = []
    evidence_path = tmp_path / "wide.png"
    evidence_path.write_bytes(png(2924, 864))
    evidence_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    scene = SimpleNamespace(
        renewal_evidence=lambda: scene_events.append("evidence") or [{
            "path": str(evidence_path), "sha256": evidence_sha, "call_id": "earlier",
            "checkpoint": "c1", "view": "front", "kind": "look_full_pair",
        }],
        renewal_started=lambda: scene_events.append("restart") or {
            "finish_review_restarted": True, "previous_review_call_id": "review1"},
        renewal_state=lambda: scene_events.append("state") or {"head_checkpoint": "c1"},
    )
    c = conversation(tmp_path)
    model_spec = spec(reference(png()))
    log = ClaudeReviewLog(tmp_path / "run", blobs_dir=tmp_path / "blobs")
    rt = SimpleNamespace(
        conversation=c, spec=model_spec, files=files, scene=scene, log=log,
        ledger=SimpleNamespace(may_start_request=lambda: (allowed, reason),
                               money_status=lambda: {"settled_usd": "1", "cutoff_usd": "2", "left_usd": "1"}),
        directory=tmp_path / "run", adapter=SimpleNamespace(image_manifest=tmp_path / "image-manifest.jsonl"),
        renewals=[], definitions=[], request_calls=[], renewed=False,
    )

    def dimensions():
        return {value["file_id"]: (value["w"], value["h"]) for value in files._read().values()}

    async def request(*, summary=False):
        assert summary is True
        rt.request_calls.append(summary)
        messages = await c.messages()
        assert messages[-1]["content"] == [{"type": "text", "text": SUMMARY_REQUEST}]
        await c.assert_request_prefix(request_body(model_spec, messages, tools=[]))
        content = summary_blocks if summary_blocks is not None else [
            {"type": "thinking", "thinking": "summary thought", "signature": "old-bound"},
            {"type": "text", "text": "Current state, outstanding differences and next action."},
        ]
        await c.append(assistant(*content))
        return {"id": "summary", "content": content, "stop_reason": "tool_use" if any(
            block["type"] == "tool_use" for block in content) else "end_turn"}

    def mark_renewed():
        rt.renewed = True

    rt.dimensions, rt.request, rt.mark_renewed = dimensions, request, mark_renewed
    return rt, scene_events, uploaded


@pytest.mark.parametrize("summary_calls", [False, True])
def test_renewal_carries_photos_tiles_state_and_no_bound_thinking(tmp_path, summary_calls):
    async def scenario():
        blocks = [call("summary-unexecuted")] if summary_calls else None
        rt, scene_events, uploaded = runtime_fixture(tmp_path, summary_blocks=blocks)
        c = rt.conversation
        try:
            content, receipt = opening_content(rt.spec, files=rt.files)
            await c.append({"role": "user", "content": content})
            first = request_body(rt.spec, await c.messages(), tools=[])
            await c.assert_request_prefix(first)
            await c.append(assistant({"type": "thinking", "thinking": "", "signature": "prior-bound"},
                                     {"type": "text", "text": "working"}))
            row = await renew_conversation(rt, {"reasons": [{"reason": "images"}],
                                                 "measurements": {"images": 481}})
            fresh = await c.messages()
            encoded = canonical_bytes(fresh)
            assert b"prior-bound" not in encoded and b"old-bound" not in encoded
            assert fresh[0]["content"][0] == content[0]
            owner_images = [b for b in fresh[0]["content"] if b.get("type") == "image"]
            assert owner_images[0] == image_block("file_1")
            assert len(owner_images) == 3  # whole owner photo + two native comparison tiles
            assert len(row["evidence"]) == 2
            assert check_request(request_body(rt.spec, fresh, tools=[]), rt.dimensions()) == []
            assert scene_events == ["evidence", "restart", "state"]
            assert rt.renewed is True and rt.request_calls == [True]
            assert row == durable_receipts(c.db_path)[0]
            assert row["host_state"]["finish_review_renewal"]["finish_review_restarted"] is True
            if summary_calls:
                assert row["summary_skipped"] == "summary response contained no written state"
                assert await c.recovery_results() == [
                    {"type": "function_call_output", "call_id": "summary-unexecuted"}]
            else:
                assert row["summary_text"].startswith("Current state")
            # Reconstruct both emitted comparison tiles against their source pixels.
            with Image.open(tmp_path / "wide.png") as original:
                for emitted in row["evidence"]:
                    with Image.open(emitted["tile_path"]) as tile:
                        box = tuple(emitted[key] for key in ("x0", "y0", "x1", "y1"))
                        assert tile.tobytes() == original.crop(box).tobytes()
            # Uniform near-equal tiles have identical bytes: both occurrences
            # remain in history, but the cache uploads their shared SHA once.
            assert row["evidence"][0]["tile_sha256"] == row["evidence"][1]["tile_sha256"]
            assert len(uploaded) == 2
        finally:
            c.close()
    run(scenario())


def test_renewal_skips_cutoff_summary_and_unknown_money_aborts(tmp_path):
    async def scenario():
        rt, _, _ = runtime_fixture(tmp_path, allowed=False, reason="settled spend reached the owner's cutoff")
        try:
            await rt.conversation.append(user())
            row = await renew_conversation(rt, {})
            assert row["summary_skipped"].startswith("settled cutoff")
            assert rt.request_calls == []
        finally:
            rt.conversation.close()
        rt, scene_events, _ = runtime_fixture(tmp_path / "unknown", allowed=False, reason="unknown charge")
        try:
            await rt.conversation.append(user())
            with pytest.raises(RuntimeError, match="unknown charge"):
                await renew_conversation(rt, {})
            assert rt.request_calls == [] and scene_events == [] and rt.renewals == []
        finally:
            rt.conversation.close()
    run(scenario())


@pytest.mark.parametrize("limit", ["images", "tokens"])
def test_renewal_skips_summary_that_cannot_pass_hard_admission(tmp_path, limit):
    async def scenario():
        rt, _, _ = runtime_fixture(tmp_path)
        try:
            content, _ = opening_content(rt.spec, files=rt.files)
            if limit == "images":
                content.extend([image_block("file_1")] * 600)
            else:
                rt.last_usage = {"input_tokens": 1_000_000, "output_tokens": 1}
                rt.appended_estimate = {"total_tokens": 0}
            await rt.conversation.append({"role": "user", "content": content})
            row = await renew_conversation(rt, {})
            assert rt.request_calls == []
            assert row["summary_skipped"].startswith("summary exceeds hard request limits")
            assert rt.renewed
        finally:
            rt.conversation.close()
    run(scenario())


def test_failed_summary_never_starts_new_epoch_or_changes_scene(tmp_path):
    async def scenario():
        rt, scene_events, _ = runtime_fixture(tmp_path)

        async def failed_request(*, summary):
            raise RuntimeError("unknown charge: interrupted stream")

        rt.request = failed_request
        try:
            await rt.conversation.append(user())
            with pytest.raises(RuntimeError, match="unknown charge"):
                await renew_conversation(rt, {})
            assert rt.conversation.session_id == "claude"
            assert scene_events == [] and rt.renewals == [] and not rt.renewed
            assert (await rt.conversation.messages())[-1]["content"][0]["text"] == SUMMARY_REQUEST
        finally:
            rt.conversation.close()
    run(scenario())
