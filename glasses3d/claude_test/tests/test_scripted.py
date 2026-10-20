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


def test_real_sdk_files_delete_preserves_evidence_and_per_upload_identity(tmp_path):
    """A shared fake service must distinguish identical photos from two runs."""
    buffer = io.BytesIO()
    Image.new("RGB", (53, 37), (3, 19, 111)).save(buffer, "PNG")
    raw = buffer.getvalue()
    wire = ScriptedClaude([])
    with anthropic.Anthropic(api_key="sk-offline-scripted", max_retries=0,
                              http_client=httpx2.Client(transport=wire.transport(), trust_env=False)) as client:
        def upload(data, filename):
            return client.files.upload(file=(filename, data, "image/png"), **files_upload_params()).id
        photo = tile(raw, "owner photo")[0]
        first = FileCache(tmp_path / "first-run-cache.json", upload).file_id_for(photo)
        second = FileCache(tmp_path / "second-run-cache.json", upload).file_id_for(photo)
        assert first != second
        deletion = client.files.delete(first)
        assert deletion.id == first and deletion.type == "file_deleted"
        assert deletion._request_id == wire.deletions[0]["request_id"]
        assert client.files.retrieve_metadata(second).size_bytes == len(raw)
    assert len(wire.uploads) == 2
    assert wire.files[first]["bytes"] == wire.files[second]["bytes"] == raw
    assert wire.files[first]["metadata"]["expires_at"] == "2026-10-27T00:00:00Z"
    assert wire.files[first]["deleted"] is True and wire.files[first]["deleted_at"]
    assert wire.files[second]["deleted"] is False and wire.files[second]["deleted_at"] is None
    assert wire.assert_complete()["deleted_files"] == 1


def test_scripted_failed_delete_preserves_file_and_does_not_retry():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24)).save(buffer, "PNG")
    wire = ScriptedClaude([], delete_statuses=[503])
    with anthropic.Anthropic(api_key="sk-offline-scripted", max_retries=0,
                              http_client=httpx2.Client(transport=wire.transport(), trust_env=False)) as client:
        uploaded = client.files.upload(file=("fixture.png", buffer.getvalue(), "image/png"),
                                       **files_upload_params())
        with pytest.raises(anthropic.APIStatusError) as failure:
            client.files.delete(uploaded.id)
        assert failure.value.status_code == 503
        assert failure.value.request_id == wire.deletions[0]["request_id"]
        assert client.files.retrieve_metadata(uploaded.id).id == uploaded.id
    assert len(wire.deletions) == 1
    assert wire.deletions[0]["deleted"] is False
    assert wire.files[uploaded.id]["deleted"] is False
    assert wire.assert_complete()["deleted_files"] == 0


def test_cleanup_proof_audits_real_guarded_teardown(tmp_path):
    """Check proof receipt/cache assumptions cheaply before its native run."""
    from glasses3d.agent.budget import write_json_atomic
    from glasses3d.claude_test.proof_c3 import audit_upload_cleanup
    from glasses3d.claude_test.runner import delete_uploads

    buffer = io.BytesIO()
    Image.new("RGB", (32, 24)).save(buffer, "PNG")
    wire = ScriptedClaude([ScriptTurn([{"type": "text", "text": "ending"}], "end_turn")])
    ledger = ledger_at(tmp_path)
    def upload(data, filename):
        return client.files.upload(file=(filename, data, "image/png"), **files_upload_params()).id
    files = FileCache(tmp_path / "file-cache.json", upload, run_id=ledger.run_id)
    with anthropic.Anthropic(api_key="sk-offline-scripted", max_retries=0,
                              http_client=guarded_client(ledger, tmp_path / "transport",
                                                         file_cache=files, inner=wire.transport())) as client:
        file_id = files.file_id_for(tile(buffer.getvalue(), "owner photo")[0])
        stream(client, [{"role": "user", "content": [image_block(file_id)]}])
        cleanup = delete_uploads(client, files, ledger, tmp_path)
    result = {"run_id": ledger.run_id, "upload_cleanup": cleanup}
    write_json_atomic(tmp_path / "summary.json", {"upload_cleanup": cleanup})
    (tmp_path / "summary.md").write_text("Uploads: 1 made; 1 deleted at teardown.", encoding="utf-8")
    assert audit_upload_cleanup(tmp_path, {"result": result}, wire)["deleted"] == 1
    wire.assert_complete()


@pytest.mark.parametrize("operation", ["metadata", "delete", "message"])
def test_scripted_deleted_file_is_inaccessible(operation):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24)).save(buffer, "PNG")
    wire = ScriptedClaude([])
    with httpx2.Client(transport=wire.transport(), trust_env=False) as http:
        uploaded = http.post("https://api.anthropic.com/v1/files",
                             files={"file": ("fixture.png", buffer.getvalue(), "image/png")},
                             data={"expires_in_seconds": "604800"}).json()
        url = "https://api.anthropic.com/v1/files/" + uploaded["id"]
        assert http.delete(url).json() == {"id": uploaded["id"], "type": "file_deleted"}
        with pytest.raises(AssertionError, match="deleted"):
            if operation == "metadata":
                http.get(url)
            elif operation == "delete":
                http.delete(url)
            else:
                http.post("https://api.anthropic.com/v1/messages", json={
                    "model": MODEL, "max_tokens": 128000, "stream": True,
                    "output_config": {"effort": "max"}, "service_tier": "standard_only",
                    "messages": [{"role": "user", "content": [image_block(uploaded["id"])]}],
                })
    assert len(wire.violations) == 1


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


def test_count_fake_accepts_supported_opening_settings(tmp_path):
    wire = ScriptedClaude([], token_counts=[{"input_tokens": 1234}])
    client, ledger = sdk(tmp_path, wire)
    settings = {"output_config": {"effort": "max"}, "cache_control": {"type": "ephemeral", "ttl": "1h"},
                "speed": "standard",
                "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]}}
    with client:
        result = client.beta.messages.count_tokens(
            model=MODEL, messages=[{"role": "user", "content": "count me"}],
            betas=["context-management-2025-06-27"], **settings)
    assert result.input_tokens == 1234 and ledger.settled_usd() == 0
    assert {key: wire.counts[0][key] for key in settings} == settings
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


def test_scripted_cache_usage_is_derived_from_actual_request_and_settled(tmp_path):
    from glasses3d.claude_test.cache_sim import CacheBlock, CacheSimulator, request_values

    def blocks(body):
        return [CacheBlock(value, 600) for value in request_values(body)]

    wire = ScriptedClaude([
        ScriptTurn([{"type": "text", "text": "first"}], "end_turn", cache_output_tokens=25),
        ScriptTurn([{"type": "text", "text": "second"}], "end_turn", cache_output_tokens=50),
    ], cache_sim=CacheSimulator(), cache_blocks=blocks)
    client, ledger = sdk(tmp_path, wire)
    messages = [{"role": "user", "content": [{"type": "text", "text": "opening"}]}]
    with client:
        for index in range(2):
            with client.messages.stream(
                    model=MODEL, messages=messages, max_tokens=128000,
                    output_config={"effort": "max"}, service_tier="standard_only",
                    cache_control={"type": "ephemeral", "ttl": "1h"}) as response:
                final = response.get_final_message()
            if index == 0:
                messages += [
                    {"role": "assistant", "content": final.model_dump(mode="json")["content"]},
                    {"role": "user", "content": [{"type": "text", "text": "continue"}]},
                ]
    assert [(row.read_tokens, row.write_tokens) for row in wire.cache_results] == [(0, 600), (600, 1200)]
    assert ledger.settled_usd() == sum(row.cost_usd for row in wire.cache_results)
    assert wire.replies[0].usage["output_tokens"] == 25
    assert wire.replies[1].usage["output_tokens"] == 50
    assert all(not call["query"] for call in wire.calls)
    wire.assert_complete()


def test_scripted_cache_usage_refuses_ambiguous_supplied_usage(tmp_path):
    from glasses3d.claude_test.cache_sim import CacheBlock, CacheSimulator

    with pytest.raises(ValueError, match="together"):
        ScriptedClaude([], cache_sim=CacheSimulator())
    wire = ScriptedClaude([ScriptTurn([], usage=DEFAULT_USAGE, cache_output_tokens=1)],
                          cache_sim=CacheSimulator(),
                          cache_blocks=lambda body: [CacheBlock(body["messages"], 1000)])
    client, ledger = sdk(tmp_path, wire)
    with client, pytest.raises(Exception):
        with client.messages.stream(
                model=MODEL, messages=[{"role": "user", "content": "test"}], max_tokens=128000,
                output_config={"effort": "max"}, service_tier="standard_only",
                cache_control={"type": "ephemeral", "ttl": "1h"}) as response:
            response.get_final_message()
    assert "computed usage cannot also be scripted" in wire.violations[0]
    assert ledger.settled_usd() == 0
    assert not ledger.may_start_request()[0]
