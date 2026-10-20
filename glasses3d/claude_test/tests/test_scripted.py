"""Real SDK, guarded accounting and finite scripted Anthropic wire."""
from __future__ import annotations

from decimal import Decimal
import io
import json

import anthropic
import httpx2
from PIL import Image
import pytest

from glasses3d.claude_test.images import FileCache, image_block, tile
from glasses3d.claude_test.money import Ledger, consume_approval, write_approval
from glasses3d.claude_test.scripted import DEFAULT_USAGE, MODEL, ScriptedClaude, ScriptTurn
from glasses3d.claude_test.transport import Refused, files_upload_params, guarded_client


def ledger_at(root, cutoff="2"):
    approval = root / "approval.json"
    write_approval(approval, words="Offline scripted proof only; no paid authorization",
                   amount_usd=Decimal(cutoff), model=MODEL, effort="max", run_id="scripted-test")
    return Ledger(root / "ledger.jsonl", cutoff_usd=Decimal(cutoff),
                  approval=consume_approval(approval, "scripted-test"))


def sdk(root, wire, *, cutoff="2", sleeps=None):
    ledger = ledger_at(root, cutoff)
    http = guarded_client(ledger, root / "receipts", inner=wire.transport(),
                          sleep=(sleeps if sleeps is not None else []).append)
    return anthropic.Anthropic(api_key="sk-offline-scripted", http_client=http, max_retries=0), ledger


def stream(client, messages=None):
    with client.beta.messages.stream(model=MODEL, max_tokens=128000,
                                     output_config={"effort": "max"}, service_tier="standard_only",
                                     betas=["context-management-2025-06-27"],
                                     messages=messages or [{"role": "user", "content": "test"}]) as response:
        return response.get_final_message()


def test_real_sdk_thinking_tools_redacted_text_and_callback(tmp_path):
    content = [
        {"type": "thinking", "thinking": "Synthetic readable summary — שלום", "signature": "opaque-FAKE-signature-012345"},
        {"type": "redacted_thinking", "data": "synthetic_opaque_redacted"},
        {"type": "text", "text": "Inspect the scene."},
        {"type": "tool_use", "id": "toolu_real_id", "name": "scene_summary", "input": {"objects": None}},
    ]
    def followup(body):
        assert body["messages"][1]["content"] == content
        assert body["messages"][2]["content"][0]["tool_use_id"] == "toolu_real_id"
        return ScriptTurn([{"type": "text", "text": "Finished scripted protocol."}], "end_turn")
    wire = ScriptedClaude([ScriptTurn(content, chunk_size=1), followup])
    client, ledger = sdk(tmp_path, wire)
    with client:
        first = stream(client)
        actual = first.model_dump(mode="json", exclude_none=True)["content"]
        assert actual == content
        messages = wire.requests[0]["messages"] + [
            {"role": "assistant", "content": actual},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_real_id",
                                         "content": [{"type": "text", "text": '{"status":"ok"}'}]}]}]
        stream(client, messages)
    assert ledger.settled_usd() == Decimal(".0012")
    assert wire.assert_complete()["scripted_messages"] == 2


def test_real_sdk_files_retains_bytes_and_uploads_cache_once(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (53, 37), (3, 19, 111)).save(buffer, "PNG")
    raw = buffer.getvalue()
    wire = ScriptedClaude([ScriptTurn([{"type": "text", "text": "image received"}], "end_turn")])
    client, ledger = sdk(tmp_path, wire)
    with client:
        def upload(data, filename):
            return client.files.upload(file=(filename, data, "image/png"), **files_upload_params()).id
        cache = FileCache(tmp_path / "cache.json", upload)
        value = tile(raw, "owner photo")[0]
        file_id = cache.file_id_for(value)
        assert cache.file_id_for(value) == file_id
        assert client.files.retrieve_metadata(file_id).size_bytes == len(raw)
        stream(client, [{"role": "user", "content": [image_block(file_id)]}])
    assert wire.files[file_id]["bytes"] == raw
    assert wire.uploads == [{"file_id": file_id, "sha256": value.sha256, "expires_in_seconds": 604800}]
    assert ledger.settled_usd() == Decimal(".0006")
    wire.assert_complete()


@pytest.mark.parametrize("status", [429, 529])
def test_retry_after_replays_identical_body_and_logs_assumption(tmp_path, status):
    wire = ScriptedClaude([ScriptTurn([], http_status=status, headers={"retry-after": "2.5"}),
                           ScriptTurn([{"type": "text", "text": "ok"}], "end_turn")])
    sleeps = []
    client, ledger = sdk(tmp_path, wire, sleeps=sleeps)
    with client:
        stream(client)
    assert sleeps == [2.5]
    assert wire.requests[0] == wire.requests[1]
    waits = [json.loads(row) for row in ledger.path.read_text().splitlines() if '"event":"wait"' in row]
    assert len(waits) == 1 and waits[0]["assumed_unbilled_reconcile_with_console"] is True
    wire.assert_complete()


@pytest.mark.parametrize("status", [429, 529])
@pytest.mark.parametrize("headers", [None, {"retry-after": "invalid"}, {"retry-after": "1201"}])
def test_retry_without_valid_bounded_header_stops(tmp_path, status, headers):
    wire = ScriptedClaude([ScriptTurn([], http_status=status, headers=headers)])
    sleeps = []
    client, ledger = sdk(tmp_path, wire, sleeps=sleeps)
    with client, pytest.raises(Exception):
        stream(client)
    assert sleeps == ([1200.0] if headers == {"retry-after": "1201"} else [])
    assert len(wire.requests) == 1
    assert ledger.may_start_request()[0] is False
    wire.assert_complete()


@pytest.mark.parametrize("turn", [
    ScriptTurn([], usage={**DEFAULT_USAGE, "new_billable_field": 1}),
    ScriptTurn([], missing_usage=True),
    ScriptTurn([], served_model="unapproved-model"),
    ScriptTurn([], error={"type": "overloaded_error", "message": "synthetic error"}),
    ScriptTurn([], truncate=True),
])
def test_unknown_outcomes_persist_and_cannot_retry(tmp_path, turn):
    wire = ScriptedClaude([turn])
    client, ledger = sdk(tmp_path, wire)
    with client:
        with pytest.raises(Exception):
            stream(client)
        assert "unknown" in ledger.may_start_request()[1]
        with pytest.raises(Exception):
            stream(client)
    assert len(wire.requests) == 1
    assert '"event":"unknown"' in ledger.path.read_text()
    wire.assert_complete()


def test_cutoff_crossing_settles_and_stops_next_request(tmp_path):
    wire = ScriptedClaude([ScriptTurn([{"type": "text", "text": "crosses cutoff"}], "end_turn")])
    client, ledger = sdk(tmp_path, wire, cutoff=".0005")
    with client:
        assert ledger.may_start_request()[0] is True
        stream(client)
        assert ledger.settled_usd() == Decimal(".0006")
        assert ledger.may_start_request()[0] is False
        with pytest.raises(Exception):
            stream(client)
    assert len(wire.requests) == 1
    wire.assert_complete()


def test_refusal_settles_upper_bound_and_stops(tmp_path):
    wire = ScriptedClaude([ScriptTurn([{"type": "text", "text": "Synthetic refusal"}], "refusal",
                                      stop_details={"category": "other"})])
    client, ledger = sdk(tmp_path, wire)
    with client:
        assert stream(client).stop_reason == "refusal"
    assert ledger.may_start_request() == (False, "refusal")
    settled = json.loads(ledger.path.read_text().splitlines()[-1])
    assert settled["upper_bound"] is True and settled["refusal"] is True
    wire.assert_complete()


def test_context_edit_event_is_reported_by_real_sdk(tmp_path):
    editing = {"applied_edits": [{"type": "clear_tool_uses_20250919", "cleared_tool_uses": 3,
                                 "cleared_input_tokens": 150000}]}
    wire = ScriptedClaude([ScriptTurn([], "end_turn", context_management=editing)])
    client, _ = sdk(tmp_path, wire)
    with client:
        response = stream(client).model_dump(mode="json", exclude_none=True)
    assert response["context_management"] == editing
    wire.assert_complete()


@pytest.mark.parametrize("method,url", [
    ("POST", "https://other.example/v1/messages"),
    ("GET", "https://api.anthropic.com/v1/files"),
    ("DELETE", "https://api.anthropic.com/v1/files/file_a"),
    ("GET", "https://api.anthropic.com/v1/files/file_a/content"),
    ("POST", "https://api.anthropic.com/v1/messages?unknown=true"),
    ("POST", "https://api.anthropic.com/v1/messages?beta=true&beta=true"),
    ("POST", "http://api.anthropic.com/v1/messages"),
])
def test_wire_rejects_unknown_endpoint_or_query(method, url):
    wire = ScriptedClaude([])
    with pytest.raises(AssertionError):
        wire(httpx2.Request(method, url, json={}))
    assert len(wire.violations) == 1


def test_script_exhaustion_has_no_fallback(tmp_path):
    wire = ScriptedClaude([])
    client, ledger = sdk(tmp_path, wire)
    with client, pytest.raises(Exception):
        stream(client)
    assert "script exhausted" in wire.violations[0]
    assert "unknown" in ledger.may_start_request()[1]


def test_count_fake_is_explicit_and_never_spends(tmp_path):
    wire = ScriptedClaude([], token_counts=[{"input_tokens": 1234}])
    client, ledger = sdk(tmp_path, wire)
    with client:
        result = client.messages.count_tokens(model=MODEL, messages=[{"role": "user", "content": "count me"}])
    assert result.input_tokens == 1234 and ledger.settled_usd() == 0
    wire.assert_complete()


def test_native_proof_script_arguments_validate_against_original_registry(tmp_path, monkeypatch):
    """Check the full planned native script before paying its Blender runtime."""
    from types import SimpleNamespace
    from jsonschema import Draft202012Validator
    from glasses3d.agent.loop import RunSettings
    from glasses3d.agent.tools import build_tools
    from glasses3d.claude_test.proof_c3 import NativeScript
    from glasses3d.g3d_host import compare

    scene = SimpleNamespace(root=tmp_path, owner_edit=None, captured_fingerprint="source-fingerprint",
                            s=SimpleNamespace(head={"fingerprint": "source-fingerprint"}),
                            images=[{"call_id": "c3_finish_review", "sha256": "seen-image"}],
                            finish_review={"call_id": "c3_finish_review"}, _unseen=set(),
                            _emitted_images={"seen-image": "c3_final_look"})
    refs = [f"photo-{index}" for index in range(6)]
    job = {"images": [{"file": f"images/{ref}.png"} for ref in refs]}
    basis = SimpleNamespace(id="basis-01", usable_refs=lambda: refs, summary=lambda: {},
                            meta={"model": {"fingerprint": "source-fingerprint"}})
    monkeypatch.setattr(compare, "load_basis", lambda *args: basis)
    source_results = {
        "c3_source": {"status": "ok", "checkpoint": "actual-checkpoint"},
        "c3_fit": {"status": "ok"}, "c3_temple": {"status": "refused"},
        "c3_restore": {"status": "ok"},
        "c3_look": {"status": "ok"}, "c3_repeated_look": {"status": "ok"},
        "c3_finish_review": {"status": "review_required",
                             "review": {"review_call_id": "c3_finish_review", "required_refs": refs}},
    }
    body = {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": cid, "content": [
            {"type": "text", "text": json.dumps(report)}, image_block("fake_image")]}
        for cid, report in source_results.items()]}]}
    script = NativeScript(scene, job, 14)
    script.forced = True
    schemas = {tool.name: tool.params_json_schema for tool in build_tools(scene, RunSettings())}
    count = 0
    for callback in script.steps:
        turn = callback(body)
        for block in turn.content:
            if block["type"] == "tool_use":
                Draft202012Validator(schemas[block["name"]]).validate(block["input"])
                count += 1
    assert count == len(script.steps) - 1  # the one renewal summary is text-only


@pytest.mark.parametrize("fault", ["wrong_review_id", "missing_photo", "unseen", "changed_checkpoint", "none"])
def test_original_two_call_finish_review_protocol(tmp_path, monkeypatch, fault):
    """Isolate the original review procedure with complete controlled evidence."""
    from copy import deepcopy
    from types import SimpleNamespace
    from glasses3d.agent.tools import finish_review as F
    from glasses3d.agent.tools.look_panels import DETAIL_KINDS

    photos = [{"ref": "photo-one", "sha256": "a" * 64}, {"ref": "photo-two", "sha256": "b" * 64}]
    binding = {"checkpoint": "source", "fingerprint": "source-fingerprint", "export_sha256": "c" * 64,
               "basis": "basis-one", "basis_sha256": "d" * 64, "cameras_sha256": "e" * 64,
               "photos": photos}
    # Only evidence acquisition is a fixture. The complete-image gate and actual
    # finish_review.apply/delivered logic remain unchanged.
    monkeypatch.setattr(F, "evidence", lambda *args: deepcopy(binding))
    look = {key: binding[key] for key in ("basis", "basis_sha256", "cameras_sha256", "export_sha256")}
    look.update(status="ok", worn_front_path="work/worn.png", views=[], images=[{"path": "work/worn.png"}])
    for photo in photos:
        pair = {"kind": "look_full_pair", "path": "work/" + photo["ref"] + ".png"}
        details = [{"kind": kind, "status": "unavailable", "reason": "Controlled no-visible-region fixture"}
                   for kind in DETAIL_KINDS]
        look["views"].append({"ref": photo["ref"], "photo_sha256": photo["sha256"],
                              "images": [pair], "details": details})
        look["images"].append(pair)
    scene = SimpleNamespace(root=tmp_path, s=SimpleNamespace(head={"id": "source"}),
                            finish_review=None, _unseen=set(), save=lambda: None)
    first = {"status": "accepted", "call_id": "actual-first-id", "failures": [], "export": {},
             "look_comparison": deepcopy(look)}
    F.apply(scene, first, None, None)
    assert first["status"] == "review_required"
    F.delivered(scene, first)
    assert scene.finish_review["call_id"] == "actual-first-id"
    review_id = "invented-id" if fault == "wrong_review_id" else "actual-first-id"
    per_photo = [{"ref": row["ref"], "differences": "Scripted procedure, no likeness claim"} for row in photos]
    if fault == "missing_photo":
        per_photo.pop()
    elif fault == "unseen":
        scene._unseen.add("actual-first-id")
    elif fault == "changed_checkpoint":
        binding["checkpoint"] = "changed"
    second = {"status": "accepted", "call_id": "actual-second-id", "failures": [], "export": {},
              "look_comparison": deepcopy(look)}
    F.apply(scene, second, review_id, per_photo)
    if fault == "none":
        assert second["status"] == "accepted"
        assert second["review"]["per_photo"] == per_photo
    else:
        assert second["status"] in {"refused", "review_required"}
        assert second["status"] != "accepted"


def test_large_base64_projection_does_not_block_small_files_inference(tmp_path):
    import base64
    import numpy as np
    from glasses3d.claude_test.conversation import context_measurement, estimate_content

    pixels = np.random.default_rng(413).integers(0, 256, (400, 400, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, "PNG")
    raw = buffer.getvalue()
    wire = ScriptedClaude([ScriptTurn([{"type": "text", "text": "valid Files inference"}], "end_turn")])
    client, ledger = sdk(tmp_path, wire)
    with client:
        uploaded = client.files.upload(file=("fixture.png", raw, "image/png"), **files_upload_params())
        blocks = [image_block(uploaded.id) for _ in range(60)]
        projection = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(raw).decode()},
                       "transformations": {"oversized_image": "error"}} for _ in range(60)]
        assert len(json.dumps(projection).encode()) > 32_000_000
        forecast = context_measurement(last_usage={"input_tokens": 100, "output_tokens": 10},
                                       appended_estimate=estimate_content(blocks, wire.dims))
        assert forecast["forecast_tokens"] >= 60 * 225
        assert forecast["uncertainty"]
        stream(client, [{"role": "user", "content": blocks}])
    assert ledger.settled_usd() > 0 and wire.counts == []
    assert len(json.dumps(wire.requests[0]).encode()) < 32_000_000
    wire.assert_complete()
