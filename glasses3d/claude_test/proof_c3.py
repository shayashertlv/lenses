"""Offline C3 protocol/native-tool proof on a private accepted Oakley copy.

This is a procedure/tool test, never a product run or owner acceptance. The real
SDK, guarded accounting, Blender worker, tools and delivery helpers execute.
Only the provider wire is scripted. Run once after the C2 unit suite is green.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit
from uuid import uuid4
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from glasses3d.agent import jobs, procs
from glasses3d.agent.budget import write_json_atomic
from glasses3d.agent.delivery import load_delivery
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.review import _loopback
from glasses3d.g3d_host import compare
from .images import check_request, fits
from .scripted import ScriptedClaude, ScriptTurn

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path(__file__).resolve().parent
SOURCE_JOB = ROOT / "data/jobs/oakley-oo9208-v2-911a2b0e"
SOURCE_RUN = "20261019T172111-465930"
STATEMENT = "Scripted offline tool procedure proof only; no automated likeness judgment or owner acceptance."

# Every entry is resolved to passing JUnit cases, not inferred from a module's
# overall pass count. Native assertions below supply the actual worker evidence.
UNIT_ASSERTIONS = {
    "append_only_thinking_without_context_edits": (
        "test_prefix_survives_reopen_and_preserves_every_thinking_field",
        "test_requests_preserve_prefix_and_thinking_without_context_edits",
        "test_policy_is_immutable_within_epoch",
    ),
    "cache_cost_through_real_runner_and_renewal": (
        "test_long_runner_cache_cost_through_real_renewal",
    ),
    "factual_tool_use_notes_and_claude_only_instructions": (
        "test_notes_through_runner_wire_reset_and_preserve_results",
        "test_refused_comparison_and_invalid_calls_do_not_reset_or_get_notes",
        "test_frozen_effective_instructions_and_unmodified_astra_text",
        "test_renewal_keeps_scene_change_count",
        "test_paged_measurements_use_verified_full_report",
        "test_surface_measurements_count_hits_not_successful_calls",
        "test_stop_after_settlement_still_records_note_sent",
    ),
    "atomic_epoch_recovery_and_tool_id_ordering": (
        "test_atomic_renewal_archives_epochs_and_recovers_completed_ids",
        "test_failed_epoch_transaction_keeps_old_pointer_and_rows",
        "test_append_copies_and_refuses_result_reordering",
        "test_no_bound_content_crosses_new_epoch",
    ),
    "native_pixels_limits_and_seven_day_upload_once": (
        "test_tiled_native_pixels_and_output_order_with_upload_dedup",
        "test_opening_keeps_whole_reference_bytes_and_verbatim_inputs",
        "test_real_sdk_files_retains_bytes_and_uploads_cache_once",
    ),
    "scripted_file_deletion_identity_and_retained_evidence": (
        "test_real_sdk_files_delete_preserves_evidence_and_per_upload_identity",
        "test_scripted_failed_delete_preserves_file_and_does_not_retry",
        "test_scripted_deleted_file_is_inaccessible",
    ),
    "owned_upload_cleanup_all_endings_closed_admission_and_failed_deletes": (
        "test_teardown_deletes_uploads_after_every_ending",
        "test_failed_delete_is_reported_once_with_expiry_without_changing_status",
        "test_delete_requires_finished_closed_ledger_and_own_run_cache",
        "test_delete_failure_is_free_and_never_retried",
        "test_closed_run_still_refuses_list_and_download",
        "test_cache_records_provider_expiry_and_deletion_outcomes",
        "test_parent_teardown_includes_specialist_uploads",
        "test_cancellation_during_delete_waits_before_releasing_locks",
    ),
    "truthful_stop_details_and_free_only_livecheck": (
        "test_non_refusal_stop_details_are_receipted_and_do_not_end_run",
        "test_refusal_is_priced_upper_bound_and_draft",
        "test_livecheck_counts_the_exact_opening_and_deletes_six_uploads",
        "test_livecheck_guard_refuses_messages_before_mock_transport",
        "test_livecheck_deletes_uploads_when_a_free_step_fails",
        "test_livecheck_failed_delete_is_recorded_once_with_expiry",
    ),
    "large_count_projection_is_not_inference_limit_and_forecast_has_uncertainty": (
        "test_large_base64_projection_does_not_block_small_files_inference",
        "test_local_measurement_counts_utf8_images_and_each_occurrence",
        "test_exact_image_and_context_soft_boundaries",
        "test_serialized_byte_soft_boundary_is_inclusive",
    ),
    "original_schema_domain_timeout_fatal_and_partial_tool_safety": (
        "test_registry_keeps_original_descriptors_without_strict",
        "test_schema_and_nonfinite_rejected_before_executor",
        "test_valid_local_schema_keeps_original_domain_validation_and_order",
        "test_sdk_timeout_is_preserved", "test_fatal_error_callback_escapes",
        "test_truncated_output_never_invokes_tool",
    ),
    "two_call_finish_bad_id_missing_photo_unseen_changed_checkpoint": (
        "test_original_two_call_finish_review_protocol",
        "test_finish_requires_delivered_result_and_unchanged_head",
        "test_accepted_finish_needs_no_extra_request",
    ),
    "settled_cutoff_crossing_parent_child_and_valid_finish": (
        "test_crossing_response_finish_can_complete",
        "test_crossing_response_executes_tool_but_blocks_next_request",
        "test_shared_parent_and_specialist_client_cutoff",
        "test_temple_cutoff_or_unknown_never_starts_child",
        "test_child_submicro_cutoff_allows_crossing_tool_then_blocks_parent",
    ),
    "unknown_usage_missing_model_error_truncation_no_retry": (
        "test_unknown_outcomes_persist_and_cannot_retry",
        "test_unknown_usage_never_invokes_proposed_tool",
        "test_other_http_errors_are_not_retried",
        "test_child_unknown_usage_stops_shared_parent_before_mutation",
    ),
    "retry_after_identical_body_wait_cap_missing_header_refusal_upper_bound": (
        "test_retry_after_replays_identical_body_and_logs_assumption",
        "test_wait_cap", "test_retry_without_valid_bounded_header_stops",
        "test_refusal_settles_upper_bound_and_stops",
    ),
    "exact_text_notes_honest_refusal_draft_and_endpoint_denial": (
        "test_text_only_notes_are_exact_and_consecutive",
        "test_refusal_is_priced_upper_bound_and_draft",
        "test_endpoint_allowlist_refuses_before_send",
        "test_wire_rejects_unknown_endpoint_or_query",
    ),
    "multi_tool_serial_results_and_post_finish_mutation": (
        "test_multi_tool_reply_is_serial_one_result_message_and_later_mutation_invalidates_finish",
    ),
    "mutation_result_crash_barrier_and_lost_finish_images": (
        "test_lost_result_keeps_original_rollback_barrier",
        "test_lost_images_invalidate_pending_and_accepted_finish",
    ),
    "stop_wait_and_child_teardown_outcomes": (
        "test_stop_preserves_unknown_charge_during_stream",
        "test_closing_unconsumed_stream_records_unknown",
        "test_private_temple_cancellation_waits_for_child_unwind",
        "test_specialist_close_cancels_and_joins_actual_child_unwind",
        "test_blocked_sse_stop_drains_thread_and_records_unknown",
        "test_stop_joins_shared_sync_tool_before_releasing_scene",
    ),
    "exact_submicro_specialist_money_callback_and_fatal_errors": (
        "test_temple_exact_submicrodollar_and_bridge_callback",
        "test_three_money_facts_preserve_submicro_precision",
        "test_private_temple_fatal_callback_survives_redecoration",
        "test_renewal_expires_only_dedup_and_namespaces_only_log",
        "test_specialist_real_sdk_shares_model_money_and_counter_but_not_history",
        "test_child_worker_failure_retains_fatal_cause_and_receipt",
        "test_changed_specialist_source_contract_refuses_before_network",
    ),
    "owner_inputs_approval_and_private_review_safety": (
        "test_copy_job_only_copies_verified_owner_inputs",
        "test_prepare_approval_seal_and_single_use",
        "test_any_changed_seal_file_refuses",
        "test_cross_provider_lock_excludes_worker_start",
        "test_review_is_provider_labelled_read_only_and_stops_only_owned",
        "test_offline_fixture_still_blocks_external_sockets_and_production_http",
    ),
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _results(body):
    return {block["tool_use_id"]: block for message in body["messages"]
            if message["role"] == "user" for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"}


def _report(result):
    for item in result["content"]:
        if item.get("type") == "text":
            try:
                value, _ = json.JSONDecoder().raw_decode(item["text"].lstrip())
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    raise AssertionError("tool result contains no JSON report")


def _images(value):
    return [item for item in _walk(value) if item.get("type") == "image"]


def _call(name, arguments, cid):
    return ScriptTurn([
        {"type": "thinking", "thinking": "Synthetic procedure summary for " + name,
         "signature": "SYNTHETIC-C3-SIGNATURE-" + cid},
        {"type": "tool_use", "id": cid, "name": name, "input": arguments},
    ])


class SeedCheckedScene(JobScene):
    """Independent start assertion; the ordinary JobScene skips this for fresh jobs."""
    def start(self):
        first = not hasattr(self, "proof_started_fingerprint")
        result = super().start()
        if first:
            assert self.s.head["fingerprint"] == self.captured_fingerprint, self.s.head
            self.proof_started_fingerprint = self.s.head["fingerprint"]
        return result


class NativeScript:
    def __init__(self, scene, job, workers):
        self.scene, self.job, self.workers = scene, job, workers
        self.saved = None
        self.basis = None
        self.retained_history_seen = False
        self.forced = False
        self.renewal_finish_reset = False
        self.steps = []
        self._make_steps()

    def add(self, name, args, cid):
        def reply(body):
            arguments = args(body) if callable(args) else args
            return _call(name, arguments, cid)
        self.steps.append(reply)

    def _make_steps(self):
        self.add("scene_summary", {"objects": None}, "c3_summary")
        for i, row in enumerate(self.job["images"], 1):
            self.add("read_image", {"path": row["file"], "region": None}, f"c3_photo_{i}")
        self.add("budget_status", {}, "c3_budget")
        self.add("save_checkpoint", {"label": "C3 source checkpoint; tool test only", "best": True}, "c3_source")

        def fit(body):
            report = _report(_results(body)["c3_source"])
            assert report["status"] == "ok", report
            self.saved = report["checkpoint"]
            return self.fit_arguments()
        self.add("fit_cameras", fit, "c3_fit")

        def render(body):
            report = _report(_results(body)["c3_fit"])
            assert report["status"] == "ok", report
            basis = compare.load_basis(self.scene.root)
            assert len(basis.usable_refs()) == 6, basis.summary()
            self.basis = basis.id
            return {"views": ["reference"], "passes": ["native", "clay"], "size_px": 1024,
                    "background": "white", "basis": self.basis}
        self.add("render_views", render, "c3_render")
        self.add("compare_to_photos", lambda _: {"refs": None, "parts": "all", "size_px": 1024,
                                                "basis": self.basis}, "c3_compare")
        self.add("temple_stage", {}, "c3_temple")

        # Select through actual Blender role/material metadata, with no product
        # names or geometry constants. Restore the captured source after proof.
        code = """import bpy
candidates = sorted((obj for obj in bpy.data.objects if obj.type == 'MESH'
    and obj.get('partRole', obj.data.get('partRole')) == 'frame'), key=lambda obj: obj.name)
selected = next((obj for obj in candidates if any(mat and mat.use_nodes
    and mat.node_tree.nodes.get('Principled BSDF') for mat in obj.data.materials)), None)
assert selected is not None, 'no frame material available'
material = next(mat for mat in selected.data.materials if mat and mat.use_nodes
    and mat.node_tree.nodes.get('Principled BSDF'))
node = material.node_tree.nodes.get('Principled BSDF')
before = float(node.inputs['Roughness'].default_value)
after = before + 0.02 if before <= 0.97 else before - 0.02
node.inputs['Roughness'].default_value = after
print({'tool_test': True, 'object': selected.name, 'material': material.name,
       'roughness_before': before, 'roughness_after': after})
"""
        def edit(body):
            stage = _report(_results(body)["c3_temple"])
            # Oakley's multiple physical components are outside the specialist's
            # one-temple scope. A refusal is expected, never a gate relaxation.
            assert stage["status"] == "refused", stage
            return {"code": code, "soft_timeout_s": 120, "label": "C3 reversible generic roughness test",
                    "seating_repair": False}
        self.add("execute_blender_code", edit, "c3_edit")
        self.add("save_checkpoint", {"label": "C3 temporary material test", "best": False}, "c3_edited")
        self.add("execute_blender_code", {
            "code": "import bpy\nprint({'tool_test': True, 'objects': len(bpy.data.objects)})\n",
            "soft_timeout_s": 120, "label": "C3 read-only scene inspection", "seating_repair": False,
        }, "c3_read_only")
        self.add("execute_blender_code", edit, "c3_edit_second")
        self.add("compare_look_to_photos", lambda _: {"refs": None, "basis": self.basis}, "c3_look")
        self.add("execute_blender_code", edit, "c3_edit_after_compare")
        self.add("scene_summary", {"objects": None}, "c3_retained_history")

        def repeat(body):
            assert _images(_results(body)["c3_look"]), "first comparison must deliver real images"
            assert "context_management" not in body
            self.retained_history_seen = True
            return {"refs": None, "basis": self.basis}
        self.add("compare_look_to_photos", repeat, "c3_repeated_look")

        def summary(body):
            assert self.forced, "summary request must follow deterministic soft pressure"
            # Earlier evidence remains available. A repeated real comparison may
            # refer to already delivered pixels; no server event expires dedup.
            assert _images(_results(body)["c3_look"])
            assert _report(_results(body)["c3_repeated_look"])["status"] == "ok"
            return ScriptTurn([{"type": "thinking", "thinking": "Synthetic renewal summary",
                                "signature": "SYNTHETIC-C3-OLD-EPOCH"},
                               {"type": "text", "text": STATEMENT + " Temporary roughness change was checked. "
                                "Restore the saved source checkpoint, then export, capture and complete review."}],
                              "end_turn")
        self.steps.append(summary)
        self.add("restore_checkpoint", lambda _: {"checkpoint": self.saved}, "c3_restore")

        def basis_or_refit(body):
            restored = _report(_results(body)["c3_restore"])
            assert restored["status"] == "ok", restored
            assert self.scene.s.head["fingerprint"] == self.scene.captured_fingerprint
            try:
                basis = compare.load_basis(self.scene.root, self.basis)
                assert set(basis.usable_refs()) == {Path(row["file"]).stem for row in self.job["images"]}
                assert basis.meta["model"]["fingerprint"] == self.scene.captured_fingerprint
            except (compare.NoBasis, ValueError, KeyError, AssertionError):
                return _call("fit_cameras", self.fit_arguments(), "c3_refit")
            return _call("scene_summary", {"objects": None}, "c3_basis_valid")
        self.steps.append(basis_or_refit)
        self.add("export_glb", {}, "c3_export")
        self.add("arview_capture", {}, "c3_ar")
        self.add("compare_look_to_photos", lambda _: {"refs": None,
                                                    "basis": compare.load_basis(self.scene.root).id}, "c3_final_look")
        self.add("finish", {"summary": STATEMENT, "review_call_id": None, "per_photo": None}, "c3_finish_review")

        def second_finish(body):
            report = _report(_results(body)["c3_finish_review"])
            assert report["status"] == "review_required", report
            review = report["review"]
            refs = review["required_refs"]
            # The six-photo contract retains this small required-ref list in
            # bounded output. Never fabricate IDs if that contract changes.
            assert isinstance(refs, list) and len(refs) == 6, report
            assert review["review_call_id"] == "c3_finish_review"
            # Shared finish may refer to byte-identical images delivered by the
            # immediately preceding look/AR calls. Verify those actual emission
            # identities rather than requiring a redundant image upload.
            evidence = [row for row in self.scene.images if row["call_id"] == "c3_finish_review"]
            assert evidence and self.scene.finish_review["call_id"] == "c3_finish_review"
            for row in evidence:
                emitted_by = self.scene._emitted_images.get(row["sha256"])
                assert emitted_by and emitted_by not in self.scene._unseen
            return {"summary": STATEMENT, "review_call_id": review["review_call_id"],
                    "per_photo": [{"ref": ref, "differences": STATEMENT} for ref in refs]}
        self.add("finish", second_finish, "c3_finish_accept")

    def fit_arguments(self):
        return {"reason": "Offline tool proof: fit copied source against its unchanged owner photos",
                "refs": None, "workers": self.workers, "off_axis": False, "mattes": None}

    def pressure(self, runtime, body, measurement):
        if not self.forced and "c3_repeated_look" in _results(body):
            assert self.retained_history_seen
            self.forced = True
            return [{"kind": "tokens", "value": 800001, "limit": 800000,
                     "test_only": True, "method": "deterministic fake forecast pressure"}]
        if self.forced and runtime.renewals and not self.renewal_finish_reset:
            assert self.scene.finish_review is None and self.scene.finish is None
            self.renewal_finish_reset = True
        return []


def _source_snapshot(source):
    job = jobs.load_job(source)
    result = read(source / "runs" / SOURCE_RUN / "result.json")
    selected = result["exit_checkpoint"]
    assert selected["verified"] is True
    rows = [json.loads(line) for line in (source / "checkpoints/index.jsonl").read_text(encoding="utf-8").splitlines()]
    checkpoint = next(row for row in rows if row.get("id") == selected["id"] and row.get("kind") == "save")
    seed = source / "checkpoints" / checkpoint["file"]
    assert digest(seed) == selected["sha256"] == checkpoint["sha256"]
    protected = {source / "job.json", seed, source / "checkpoints/index.jsonl"}
    for row in job["images"]:
        protected.update((source / row["file"], source / row["sent_file"]))
    for directory in ("runs", "reviews", "review", "deliveries", "deliverable"):
        protected.update(path for path in (source / directory).rglob("*.json") if path.is_file())
        protected.update(path for path in (source / directory).rglob("*.jsonl") if path.is_file())
        protected.update(path for path in (source / directory).rglob("*.glb") if path.is_file())
    # Approval and verdict metadata are part of the unchanged-source audit.
    return job, seed, checkpoint, {str(path): digest(path) for path in sorted(protected)}


def audit_wire(wire, spec, script):
    epochs, previous = [], None
    expected_tools = [{"name": tool.name, "description": tool.description,
                       "input_schema": tool.params_json_schema} for tool in spec.tools]
    for number, body in enumerate(wire.requests):
        assert "context_management" not in body
        assert body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert not any("cache_control" in item for item in _walk(body["messages"]))
        assert body["tools"] == expected_tools
        assert not check_request(body, wire.dims)
        messages = body["messages"]
        if previous is None or messages[:len(previous["messages"])] != previous["messages"]:
            epochs.append(number)
            assert len(epochs) <= 2, "unexpected history rewrite or extra renewal"
            assert not any(item.get("type") in ("thinking", "redacted_thinking")
                           for item in _walk(messages[0]))
        else:
            assert {k: v for k, v in body.items() if k != "messages"} == {
                k: v for k, v in previous.items() if k != "messages"}
        calls = {}
        for message in messages:
            for block in message["content"]:
                if block.get("type") == "tool_use":
                    assert block["id"] not in calls
                    calls[block["id"]] = False
                if block.get("type") == "tool_result":
                    assert block["tool_use_id"] in calls and not calls[block["tool_use_id"]]
                    calls[block["tool_use_id"]] = True
                if block.get("type") == "thinking":
                    source = [item for turn in wire.replies for item in turn.content
                              if item.get("type") == "thinking" and item.get("signature") == block["signature"]]
                    assert source and block == source[0]
        assert all(calls.values()), "a request contains unmatched tool calls"
        previous = body
    assert len(epochs) == 2 and script.forced and script.renewal_finish_reset
    assert not wire.counts, "runtime must never call count_tokens"
    assert len(wire.uploads) == len(wire.files) == len({row["sha256"] for row in wire.uploads}), (
        "an image SHA was uploaded more than once")
    assert all(row["expires_in_seconds"] == 604800 for row in wire.uploads)
    for call in wire.calls:
        if call["path"] == "/v1/messages":
            assert call["query"] == "", "native proof must use standard Messages"
        allowed = ((call["method"], call["path"]) in {
            ("POST", "/v1/messages"), ("POST", "/v1/files")}
            or call["method"] in {"GET", "DELETE"}
            and re.fullmatch(r"/v1/files/[A-Za-z0-9_-]+", call["path"]))
        assert allowed, f"unexpected native proof endpoint: {call}"
        if call["method"] in {"GET", "DELETE"}:
            assert call["path"].rsplit("/", 1)[1] in wire.files
    for index in epochs:
        opening_ids = [item["source"]["file_id"] for item in _images(wire.requests[index]["messages"][0])]
        for photo in spec.photos:
            # File blocks carry the actual original photo bytes for this job.
            expected = Image.open(io.BytesIO((spec.job_dir / photo.path).read_bytes())).convert("RGBA")
            assert any(Image.open(io.BytesIO(wire.files[file_id]["bytes"])).convert("RGBA").size == expected.size
                       and np.array_equal(np.asarray(Image.open(io.BytesIO(wire.files[file_id]["bytes"])).convert("RGBA")),
                                          np.asarray(expected)) for file_id in opening_ids)
    return {"epochs": len(epochs), "requests": len(wire.requests),
            "max_request_bytes": max(wire.request_sizes), "request_sha256": wire.request_sha256,
            "max_images": max(len(_images(body)) for body in wire.requests),
            "thinking_roundtrip": True, "all_six_photos_in_each_epoch": True,
            "descriptors_unchanged": True, "upload_once": True,
            "no_context_edits": True, "automatic_one_hour_cache": True}


def audit_upload_cleanup(run_dir, outcome, wire):
    """Verify actual post-run wire deletes against durable cache/accounting."""
    result = outcome["result"]
    cleanup = result["upload_cleanup"]
    assert cleanup == read(run_dir / "upload-cleanup.json")
    assert cleanup == read(run_dir / "summary.json")["upload_cleanup"]
    cache = read(run_dir / "file-cache.json")
    uploaded_ids = {row["file_id"] for row in wire.uploads}
    assert uploaded_ids and len(uploaded_ids) == len(wire.uploads)
    assert len(cache) == len(uploaded_ids)
    assert {row["file_id"] for row in cache.values()} == uploaded_ids
    assert cleanup["uploads"] == cleanup["deleted"] == len(uploaded_ids)
    assert cleanup["failed"] == [] and not cleanup.get("admission_error")
    assert len(cleanup["results"]) == len(uploaded_ids)
    assert {row["file_id"] for row in cleanup["results"]} == uploaded_ids
    expected_deletes = Counter({file_id: 1 for file_id in uploaded_ids})
    assert Counter(row["file_id"] for row in wire.deletions) == expected_deletes
    assert all(row["deleted"] and row["http_status"] == 200 for row in wire.deletions)
    assert all(wire.files[file_id]["deleted"] and wire.files[file_id]["deleted_at"]
               for file_id in uploaded_ids)
    ledger = [json.loads(line) for line in (run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    closed = [index for index, row in enumerate(ledger) if row["event"] == "conversation_finished"]
    assert len(closed) == 1
    closed_index = closed[0]
    terminal = {row["request_no"] for row in ledger[:closed_index]
                if row["event"] in {"settled", "unknown", "rejected", "not_sent"}}
    started = {row["request_no"] for row in ledger if row["event"] == "started"}
    assert started == terminal, "conversation closed with an unresolved request"
    assert not any(row["event"] == "started" for row in ledger[closed_index + 1:])
    closed_at = datetime.fromisoformat(ledger[closed_index]["time"].replace("Z", "+00:00"))
    receipts = [read(path) for path in (run_dir / "transport").glob("*.json")]
    deletes = [row for row in receipts if row.get("method") == "DELETE"]
    assert len(deletes) == len(uploaded_ids)
    assert Counter(urlsplit(row["url"]).path.rsplit("/", 1)[1] for row in deletes) == expected_deletes
    for entry in cleanup["results"]:
        file_id, digest = entry["file_id"], entry["sha256"]
        record = cache[digest]
        assert record["file_id"] == file_id and record["run_id"] == result["run_id"]
        assert record["deleted"] is True and record["deleted_at"] == entry["deleted_at"]
        assert record["expires_in_seconds"] == 604800 and record["expires_at"] == entry["expires_at"]
        assert wire.files[file_id]["sha256"] == digest
        authority = next(row for row in deletes if urlsplit(row["url"]).path.rsplit("/", 1)[1] == file_id)
        assert authority["request_no"] is None and authority["cost_usd"] == "0"
        assert authority["outcome"] == "free" and authority["http_status"] == 200
        assert authority["request_id"] == entry["request_id"]
        assert authority["response"] == {"id": file_id, "type": "file_deleted"}
        assert datetime.fromisoformat(authority["started"].replace("Z", "+00:00")) >= closed_at
        assert datetime.fromisoformat(record["deleted_at"].replace("Z", "+00:00")) >= closed_at
    last_message = max(index for index, row in enumerate(wire.calls)
                       if (row["method"], row["path"]) == ("POST", "/v1/messages"))
    first_delete = next(index for index, row in enumerate(wire.calls) if row["method"] == "DELETE")
    assert first_delete > last_message
    assert all(row["method"] == "DELETE" for row in wire.calls[first_delete:])
    markdown = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert f"Uploads: {len(uploaded_ids)} made; {len(uploaded_ids)} deleted at teardown." in markdown
    return {"status": "passed", "uploads": len(uploaded_ids), "deleted": len(deletes),
            "failed": [], "conversation_closed_before_deletion": True,
            "durable_cache_and_receipts_verified": True}


def audit_pixels(run_dir, wire):
    rows = [json.loads(line) for line in (run_dir / "image-manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    groups = defaultdict(list)
    for row in rows:
        assert fits(row["w"], row["h"], many=True)
        assert row["tile_sha256"] == wire.files[row["file_id"]]["sha256"]
        if "call_id" in row:
            groups[(row["call_id"], row["source_sha256"])].append(row)
    split = 0
    for (call_id, source_sha), tiles in groups.items():
        tiles = list({(tile["x0"], tile["y0"], tile["tile_sha256"]): tile for tile in tiles}.values())
        if len(tiles) <= 1:
            continue
        source = Path(tiles[0]["source_path"])
        assert digest(source) == source_sha
        with Image.open(source) as image:
            expected = np.array(image.convert("RGBA"))
        reconstructed = np.zeros_like(expected)
        coverage = np.zeros(expected.shape[:2], dtype=bool)
        for tile in tiles:
            with Image.open(io.BytesIO(wire.files[tile["file_id"]]["bytes"])) as image:
                actual = np.array(image.convert("RGBA"))
            x0, y0, x1, y1 = (tile[key] for key in ("x0", "y0", "x1", "y1"))
            assert np.array_equal(actual, expected[y0:y1, x0:x1])
            reconstructed[y0:y1, x0:x1] = actual
            coverage[y0:y1, x0:x1] = True
        assert coverage.all() and np.array_equal(reconstructed, expected)
        if call_id in {"c3_look", "c3_repeated_look", "c3_final_look", "c3_finish_review"}:
            split += 1
    assert split > 0, "no actual native comparison panel exercised splitting"
    return {"manifest_rows": len(rows), "native_comparison_sources_split_and_reconstructed": split}


async def audit_epochs(private, renewal, wire):
    from glasses3d.agent.loop import BlobSession
    from glasses3d.agent.receipts import BlobStore
    from glasses3d.agent.renewal import durable_receipts
    from .conversation import canonical_bytes

    db = private / "session/session.sqlite"
    receipts = durable_receipts(db)
    assert receipts == [renewal], "SQLite renewal receipt differs from the published result"
    session = BlobSession("claude", db, blobs=BlobStore(private / "blobs"))
    try:
        assert session.session_id == renewal["new_conversation_id"]
        current = await session.get_items()
        session.session_id = renewal["old_conversation_id"]
        old = await session.get_items()
    finally:
        session.close()
    assert len(old) == renewal["old_epoch_items"]
    assert hashlib.sha256(canonical_bytes(old)).hexdigest() == renewal["old_epoch_items_sha256"]
    assert hashlib.sha256(canonical_bytes(old[:renewal["old_items"]])).hexdigest() == renewal["old_items_sha256"]
    assert not any(block.get("type") in {"thinking", "redacted_thinking"} for block in _walk(current[0]))
    assert current[0] == next(body["messages"][0] for body in wire.requests
                             if body["messages"][0] != wire.requests[0]["messages"][0])
    assert _report(_results({"messages": current})["c3_finish_accept"])["status"] == "accepted"
    old_signatures = {block["signature"] for block in _walk(old) if block.get("type") == "thinking"}
    new_signatures = {block["signature"] for block in _walk(current) if block.get("type") == "thinking"}
    assert old_signatures and new_signatures and not old_signatures.intersection(new_signatures)
    assert renewal["host_state"]["finish_review"]["status"] == "not_pending"
    return {"status": "passed", "old_epoch": renewal["old_conversation_id"],
            "new_epoch": renewal["new_conversation_id"], "old_messages": len(old),
            "new_messages": len(current), "old_history_hash_verified": True,
            "atomic_pointer_receipt_verified": True, "final_accepted_result_persisted": True}


def audit_settlement_and_forecasts(run_dir, wire):
    from .pricing import price_usage

    ledger = [json.loads(line) for line in (run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    settled = [row for row in ledger if row["event"] == "settled"]
    assert len(settled) == len(wire.requests)
    assert not [row for row in ledger if row["event"] in {"unknown", "rejected"}]
    receipts = [read(path) for path in (run_dir / "transport").glob("*.json")]
    paid = [row for row in receipts if row.get("schema") == "g3d.claude_transport/1"
            and row.get("request_no") is not None]
    assert len(paid) == len(settled)
    for number, row in enumerate(settled, 1):
        assert row["request_no"] == number
        assert Decimal(row["cost_usd"]) == price_usage(row["model"], row["usage"])
        authority = next(receipt for receipt in paid if receipt["request_no"] == number)
        assert authority["outcome"] == "settled" and authority["http_status"] == 200
        assert authority["usage"] == row["usage"] and authority["raw_sse"]
        assert authority["request_id"] == row["request_id"]
        meter = read(run_dir / "requests" / f"{number:04d}-meter.json")
        assert meter["forecast"]["uncertainty"] and meter["forecast"]["count_tokens_called"] is False
        assert meter["actual_next_usage"] == row["usage"]
        assert meter["prefix"]
    return {"status": "passed", "settled_replies": len(settled),
            "forecasts_with_actual_next_usage": len(settled),
            "simulated_settled_usd": str(sum((Decimal(row["cost_usd"]) for row in settled), Decimal(0))),
            "actual_provider_requests": 0, "actual_spend_usd": "0"}


def audit_native_tools(scene, wire):
    expected = {}
    for turn in wire.replies:
        for block in turn.content:
            if block.get("type") == "tool_use":
                expected[block["id"]] = block["name"]
    rows = {row["call_id"]: row for row in scene.events if row.get("kind", "call") == "call"}
    statuses = {}
    for cid, name in expected.items():
        assert cid in rows and rows[cid]["tool"] == name
        wanted = ("refused" if name == "temple_stage" else "review_required" if cid == "c3_finish_review"
                  else "accepted" if cid == "c3_finish_accept" else "exported" if name == "export_glb"
                  else "ok")
        assert rows[cid]["status"] == wanted, (cid, rows[cid])
        statuses[cid] = rows[cid]["status"]
    assert rows["c3_edit"]["mutating"] is True
    assert rows["c3_edit"]["fingerprint_before"] == scene.captured_fingerprint
    assert rows["c3_edit"]["fingerprint_after"] != scene.captured_fingerprint
    assert rows["c3_restore"]["fingerprint_after"] == scene.captured_fingerprint
    return {"status": "passed", "journaled_tools": statuses, "reversible_material_mutation": True}


def audit_tool_use(run_dir, scene, wire):
    """Check host notes against native fingerprints, original outputs and wire."""
    path = run_dir / "tool-use.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    calls = [block for turn in wire.replies for block in turn.content
             if block.get("type") == "tool_use"]
    assert [row["call_id"] for row in rows] == [call["id"] for call in calls]
    assert [row["name"] for row in rows] == [call["name"] for call in calls]
    assert all(row["stage"] is None for row in rows)
    native = {row["call_id"]: row for row in scene.events if row.get("kind", "call") == "call"}
    original = {}
    for result_path in (run_dir / "tools").glob("*/result.json"):
        result = read(result_path)
        original[result["call_id"]] = result
    seen, first_request = {}, {}
    for number, body in enumerate(wire.requests, 1):
        for cid, result in _results(body).items():
            if cid in seen:
                assert result == seen[cid], "host note changed in append-only wire history"
            seen[cid] = result
            first_request.setdefault(cid, number)

    expected_notes = {
        "c3_edit": (1, "compare_to_photos"),
        "c3_edit_second": (2, "compare_to_photos"),
        "c3_edit_after_compare": (1, "compare_look_to_photos"),
    }
    sent_path = path.with_name("tool-use-sent.jsonl")
    sent = [json.loads(line) for line in sent_path.read_text(encoding="utf-8").splitlines()]
    expected_paged = {}
    for row in rows:
        for item in row["result"]["content"]:
            if item.get("type") != "text":
                continue
            try:
                value, _ = json.JSONDecoder().raw_decode(item["text"].lstrip())
                break
            except ValueError:
                value = None
        else:
            value = None
        if (row["name"] != "read_report" and isinstance(value, dict)
                and value.get("paged") is True):
            digest = value["full_report"]["id"]
            assert re.fullmatch(r"[0-9a-f]{64}", digest)
            expected_paged[row["call_id"]] = digest
    expected_sent = (set(expected_notes) | set(expected_paged)) & seen.keys()
    assert Counter(row["call_id"] for row in sent) == Counter({cid: 1 for cid in expected_sent})
    for row in sent:
        assert row["request_no"] == first_request[row["call_id"]]
    comparisons = {"compare_to_photos", "compare_look_to_photos"}
    streak = longest = 0
    for row in rows:
        cid, name = row["call_id"], row["name"]
        paged_note = (f"Host: this result is paged; the full report is available with read_report "
                      f"(id {expected_paged[cid]}).") if cid in expected_paged else None
        assert row.get("paged_note") == paged_note
        assert row.get("paged_report") == expected_paged.get(cid)
        appended = ([row["host_note"]] if row["host_note"] else []) + (
            [paged_note] if paged_note else [])
        assert row["result"]["tool_use_id"] == cid
        if cid in seen:
            assert row["result"] == seen[cid], "durable tool-use receipt differs from the wire"
        else:
            # Accepted finish ends immediately; audit_epochs checks that final
            # result in the durable conversation instead of inventing a request.
            assert cid == "c3_finish_accept"
        reset = name in comparisons and row["successful"]
        assert row["comparison_reset"] is reset
        if reset:
            streak = 0
        if name == "execute_blender_code":
            journal = native[cid]
            change = row["scene_change"]
            assert change["before"] == journal["fingerprint_before"]
            assert change["after"] == journal["fingerprint_after"]
            assert change["changed"] is (change["before"] != change["after"])
            assert change["changed"] is (cid in expected_notes)
            if change["changed"]:
                streak += 1
                longest = max(longest, streak)
            # Scripts emit only original text plus the separate host lines:
            # preserve their status, stdout, footer and all other content.
            actual = row["result"]["content"]
            assert (actual[:-len(appended)] if appended else actual) == original[cid]["result"]
            assert not row["result"].get("is_error")
        assert row["changes_since_comparison"] == streak
        notes = [item["text"] for item in row["result"]["content"]
                 if item.get("type") == "text" and item["text"].startswith("Host: ")]
        assert notes == appended
        if appended:
            assert row["result"]["content"][-len(appended):] == [
                {"type": "text", "text": note} for note in appended]
        if cid in expected_notes:
            number, last_comparison = expected_notes[cid]
            note = row["host_note"]
            assert isinstance(note, str) and "\n" not in note
            assert note.startswith(f"Host: {number} model change")
            assert f"since your last {last_comparison}." in note
            for tool in ("compare_to_photos", "compare_look_to_photos", "photo_read", "pick_surface_point"):
                assert tool in note
        else:
            assert row["host_note"] is None
    assert native["c3_read_only"]["fingerprint_before"] == native["c3_read_only"]["fingerprint_after"]
    assert longest == 2
    counts = Counter(call["name"] for call in calls)
    metrics = read(run_dir / "summary.json")["tool_use"]
    assert metrics["per_tool_counts"] == dict(counts)
    assert metrics["total_calls"] == len(calls)
    assert metrics["dedicated_tool_calls"] == len(calls) - counts["execute_blender_code"]
    assert metrics["dedicated_tool_share"] == (len(calls) - counts["execute_blender_code"]) / len(calls)
    assert metrics["longest_no_comparison_streak"] == longest
    assert metrics["photo_measurements"] == 0
    assert metrics["photo_measurement_calls"] == metrics["unresolved_measurement_calls"] == 0
    assert metrics["host_notes_sent"] == len(expected_notes) + len(set(expected_paged) & seen.keys())
    assert metrics["host_notes_prepared"] == len(expected_notes) + len(expected_paged)
    assert metrics["paged_results"] == len(expected_paged)
    assert metrics["paged_notes_sent"] == len(set(expected_paged) & seen.keys())
    assert metrics["paged_results_later_read"] == 0  # The native script never calls read_report.
    return {"status": "passed", "receipt": str(path), "sent_receipt": str(sent_path), "metrics": metrics,
            "scene_changing_scripts": list(expected_notes), "read_only_script_note_absent": True,
            "comparison_reset_verified": True, "original_script_outputs_unchanged": True,
            "wire_and_journal_consistent": True}


def audit_summary(run_dir, outcome, wire, delivered):
    summary = read(run_dir / "summary.json")
    result = outcome["result"]
    assert summary["provider"] == "anthropic" and summary["model"] == "claude-opus-5-5"
    assert summary["tool_test"] is True and summary["status"] == "completed"
    assert summary["requests"] == result["requests"] == len(wire.requests)
    assert summary["request_counts"] == {
        "model_requests": len(wire.requests), "message_http_attempts": len(wire.requests),
        "retry_resends": 0, "retry_waits": 0, "count_tokens": 0, "uploads": len(wire.uploads)}
    assert summary["budget"] == result["budget"]
    assert Decimal(summary["budget"]["settled_usd"]) > 0
    assert summary["delivery"] == outcome["delivery"]
    lens = summary["lens_material_choices"]
    assert lens["availability"] == "verified" and lens["materials"]
    assert lens["glb_sha256"] == delivered["glb_sha256"]
    assert all(row["material"] for row in lens["materials"])
    method = summary["image_reference_method"]
    assert "public text" in method and "implicit references are not counted" in method
    assert "receipts prove internal attention" in method
    markdown = (run_dir / "summary.md").read_text(encoding="utf-8")
    assert method in markdown and delivered["glb_sha256"] in markdown
    return {"status": "passed", "request_counts": summary["request_counts"],
            "settled_usd": summary["budget"]["settled_usd"], "lens_materials": len(lens["materials"]),
            "verified_glb_sha256": lens["glb_sha256"], "public_reference_uncertainty": method}


def audit_units(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    assert cases and not list(root.iter("failure")) and not list(root.iter("error")) and not list(root.iter("skipped"))
    names = [case.attrib.get("classname", "") + "::" + case.attrib["name"] for case in cases]
    required_modules = ("test_transport", "test_money", "test_images", "test_scripted",
                        "test_tool_adapter", "test_conversation", "test_runner", "test_cli")
    assert all(any(module + "." in name or module + "::" in name for name in names) for module in required_modules)
    assertions = {}
    for label, required in UNIT_ASSERTIONS.items():
        evidence = []
        for function in required:
            matched = [name for name in names if name.split("::")[-1].split("[")[0] == function]
            assert matched, f"no passing evidence for {label}: {function}"
            evidence.extend(matched)
        assertions[label] = {"status": "passed", "test_cases": evidence}
    return {"status": "passed", "passed": len(cases), "junit_path": str(Path(path).resolve()),
            "junit_sha256": digest(path), "assertions": assertions, "tests": names}


async def native(work, workers, assertions):
    from . import cli, runner

    job, source_seed, checkpoint, before = _source_snapshot(SOURCE_JOB)
    assertions["original_inputs_and_source"] = {"status": "pending", "before": before}
    private = work / "jobs" / job["id"]
    cli.copy_job(SOURCE_JOB, private)
    seed = private / "work/c3-source.blend"
    seed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_seed, seed)
    assert digest(seed) == checkpoint["sha256"]
    scene = SeedCheckedScene(private, job=jobs.load_job(private), captured_seed=seed,
                             captured_fingerprint=checkpoint["fingerprint"])
    script = NativeScript(scene, job, workers)
    wire = ScriptedClaude(script.steps)
    settings = runner.ClaudeSettings(model="claude-opus-5-5", cutoff_usd=Decimal("2"),
                                     max_turns=len(script.steps) + 2)
    spec, _ = runner.prepare_run(private, settings=settings, run_id="c3-native", offline=True,
                                 write_request=True, scene=scene)
    runner.approve_run(spec, words="Offline C3 fixture only; synthetic approval, no paid authorization")
    try:
        outcome = await runner.execute_run(spec, scene=scene, inner=wire.transport(),
                                           isolated_lock_root=work / "locks", pressure_hook=script.pressure)
        write_json_atomic(work / "outcome.json", outcome)
        assert outcome["result"]["status"] == "completed", outcome
        assert outcome["delivery"]["status"] == "delivered", outcome
        assert outcome["result"]["exit_checkpoint"]["verified"]
        assert not outcome["result"].get("teardown_errors")
        assert scene.proof_started_fingerprint == checkpoint["fingerprint"]
        run_dir = private / "runs" / spec.run_id
        assert len(outcome["result"]["renewals"]) == 1
        renewal = outcome["result"]["renewals"][0]
        assert renewal, "durable renewal receipt missing"
        assertions["native_tools_finish_delivery"] = {
            "status": "passed", "result": str(run_dir / "result.json"), "renewal": renewal,
            "source_fingerprint": checkpoint["fingerprint"], "native_temple_scope_refused": True,
            "simulated_settled_usd": outcome["result"]["budget"]["settled_usd"],
            "script": wire.assert_complete()}
        assertions["wire_history_images"] = {"status": "passed", **audit_wire(wire, spec, script)}
        assertions["native_tool_journals"] = audit_native_tools(scene, wire)
        assertions["native_tool_use_notes"] = audit_tool_use(run_dir, scene, wire)
        assertions["native_tile_pixels"] = {"status": "passed", **audit_pixels(run_dir, wire)}
        assertions["durable_renewal_epochs"] = await audit_epochs(private, renewal, wire)
        assertions["transport_settlement_and_forecasts"] = audit_settlement_and_forecasts(run_dir, wire)
        assertions["uploads_deleted_at_teardown"] = audit_upload_cleanup(run_dir, outcome, wire)
        delivered = load_delivery(private)
        assert digest(delivered["model"]) == delivered["glb_sha256"]
        assertions["native_run_summary"] = audit_summary(run_dir, outcome, wire, delivered)
        report = delivered["report_data"]
        assert report["validate_spec"]["ok"] and report["arview"]["exit_code"] == 0
        review = cli.review_run(private, spec.run_id, port=0)
        try:
            url = urlsplit(review["page_url"])
            status, _, page = _loopback(url.port, url.path or "/")
            assert status == 200 and b"anthropic" in page.lower() and b"claude-opus-5-5" in page
            native_review = review.get("native_review", review.get("native", review))
            server = native_review["server"]
            status, _, body = _loopback(server["port"], "/__arview/report.json")
            live = json.loads(body)
            assert status == 200 and live["served_sha256"] == delivered["glb_sha256"]
            model_url = urlsplit(live["model"]["url"])
            status, _, model_bytes = _loopback(model_url.port, model_url.path,
                                              limit=Path(delivered["model"]).stat().st_size)
            assert status == 200 and hashlib.sha256(model_bytes).hexdigest() == delivered["glb_sha256"]
            assertions["review_and_native_manifest"] = {"status": "passed",
                "delivery_id": delivered["delivery_id"], "glb_sha256": delivered["glb_sha256"],
                "manifest_sha256": delivered["manifest_sha256"], "provider_page": url.geturl(),
                "owned_server_pid": server["pid"], "owner_acceptance_written": False}
        finally:
            cli.stop_review(review)
    finally:
        scene.close()
        after = _source_snapshot(SOURCE_JOB)[3]
        assert before == after, "original input/checkpoint/approval/verdict files changed"
        assertions["original_inputs_and_source"] = {"status": "passed", "before": before, "after": after}
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof-id", default="proof-" + uuid4().hex[:8])
    parser.add_argument("--unit-results", type=Path)
    parser.add_argument("--workers", type=int, default=14)
    options = parser.parse_args(argv)
    assert re_safe_id(options.proof_id)
    assert 1 <= options.workers <= 16
    work = PACKAGE / "work/c3" / options.proof_id
    work.mkdir(parents=True, exist_ok=False)
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "OPENAI_BASE_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    started = time.monotonic()
    receipt = {"schema": "g3d.claude_c3_proof/1", "status": "running", "tool_test": True,
               "pid": os.getpid(), "started": procs.utcnow(), "argv": sys.argv,
               "actual_provider_requests": 0, "actual_spend_usd": "0", "assertions": {}}
    write_json_atomic(work / "proof.json", receipt)
    try:
        from glasses3d.agent.tools import free_commit_bytes
        free_commit_gib = free_commit_bytes() / 2**30
        ceiling = min(os.cpu_count() or 1, 16, max(1, int((free_commit_gib - 2) / 1.3)))
        options.workers = min(options.workers, ceiling)
        receipt["resources"] = {"free_commit_gib": free_commit_gib, "workers": options.workers,
                                "ecoqos_opt_out": procs.opt_out_self(), "blas_threads": 1}
        junit = options.unit_results
        if junit is None:
            junit = work / "unit-results.xml"
            command = [sys.executable, "-m", "pytest", "-c", str(PACKAGE / "pytest.ini"),
                       str(PACKAGE / "tests"), "-q", "-n", str(options.workers), "--junitxml", str(junit)]
            with (work / "unit-tests.log").open("wb") as log:
                completed = subprocess.run(command, cwd=ROOT.parent, stdout=log, stderr=subprocess.STDOUT)
            assert completed.returncode == 0, "claude_test unit proof failed"
        receipt["assertions"]["unit_negative_scenarios"] = audit_units(junit)
        disable_sdk_tracing()
        with procs.DescendantsOptOut(), LoopbackOnly() as network, ProductionTransportTripwire() as transports:
            asyncio.run(native(work, options.workers, receipt["assertions"]))
        assert not network.attempts and not transports.constructions
        receipt["assertions"]["network_isolation"] = {"status": "passed",
            "external_socket_dns_attempts": network.attempts, "production_transports": transports.constructions}
        receipt["status"] = "passed"
    except BaseException as error:
        receipt["status"] = "failed"
        receipt["error"] = {"type": type(error).__name__, "message": str(error),
                            "traceback": traceback.format_exc()}
        raise
    finally:
        receipt["seconds"] = round(time.monotonic() - started, 3)
        receipt["finished"] = procs.utcnow()
        write_json_atomic(work / "proof.json", receipt)
        print(json.dumps({"proof": str(work / "proof.json"), "status": receipt["status"],
                          "seconds": receipt["seconds"]}), flush=True)
    return 0


def re_safe_id(value):
    import re
    return re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None


if __name__ == "__main__":
    raise SystemExit(main())
