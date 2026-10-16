"""The $0 declared mapping refuses before estimates, approvals, clients or counts."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from glasses3d.agent import approvals as A, cli, edit_capabilities as C, edits, job_run, loop
from .test_owner_edit_lifecycle_review import selected_source


def mapping(words, kinds=("translate_region",), *, items=None):
    return {"schema": C.MAPPING_SCHEMA, "request_text": words, "items": items if items is not None else [{
        "id": "I1", "label": "Move the labelled region", "start": 0, "end": len(words), "text": words,
        "operation_kinds": list(kinds)}]}


def round_with_coverage(fixture, words="Move the shown face."):
    root = edits.create_understand(fixture.source, words, destination=fixture.destination)
    report = C.check(words, mapping(words))
    C.install(root, report)
    return root, report


def fail_if_called(*args, **kwargs):
    pytest.fail("dry-run refusal reached estimation, a worker, a client, or a token count")


def test_no_keyword_classifier_can_certify_an_unknown_or_compound_request():
    words = "Translate this face and invent an entirely new self-folding hinge mechanism."
    result = C.check(words)
    assert result["status"] == "needs_mapping"
    assert result["diagnostics"][0]["code"] == "mapping_required"
    assert result["api_calls"] == 0 and result["cost_usd"] == "0"
    assert "does not understand natural language" in result["notice"]


def test_record_keeps_verbatim_text_and_real_implementation_version():
    words = "  Move the shown face.\r\nKeep its neighbour fixed.  "
    report = C.check(words, mapping(words))
    assert report["status"] == "ready" and report["request_text"] == words
    assert report["request_sha256"] == C.text_digest(words)
    assert report["catalog"]["implementation_sha256"]["g3d/owner_mesh.py"]
    assert report["catalog"]["version_sha256"] == C.catalog()["version_sha256"]
    assert C.require_ready(report, words) == report


@pytest.mark.parametrize("change,code", [
    (lambda m: m["items"][0].update(operation_kinds=["teleport_topology"]), "unsupported_operation"),
    (lambda m: m["items"][0].update(operation_kinds=[]), "unmapped_item"),
    (lambda m: m["items"][0].update(text="Unmatched text"), "ambiguous_span"),
    (lambda m: m["items"].append(copy.deepcopy(m["items"][0])), "invalid_label"),
    (lambda m: m["items"].append({**m["items"][0], "id": "I2"}), "overlapping_items"),
    (lambda m: m.update(request_text="Changed owner text."), "request_changed"),
])
def test_bad_mapping_is_diagnostic_never_ready(change, code):
    words = "Move the shown face."
    declaration = mapping(words)
    change(declaration)
    result = C.check(words, declaration)
    assert result["status"] == "needs_mapping"
    assert code in {row["code"] for row in result["diagnostics"]}
    with pytest.raises(ValueError, match="dry run"):
        C.require_ready(result, words)


def test_dropped_second_request_is_reported_at_exact_offsets():
    first, second = "Move the face.", " Add an unavailable mechanism."
    words = first + second
    declaration = mapping(words)
    declaration["items"][0].update(end=len(first), text=first)
    result = C.check(words, declaration)
    missing = next(row for row in result["diagnostics"] if row["code"] == "unmapped_text")
    assert missing["character_offsets"] == [i for i in range(len(first), len(words)) if not words[i].isspace()]


@pytest.mark.parametrize("field", ["status", "request_sha256", "record_sha256", "catalog"])
def test_tampering_cannot_turn_a_record_into_an_authority(field):
    words = "Move the shown face."
    report = C.check(words, mapping(words))
    report[field] = "forged"
    with pytest.raises(ValueError, match="stale or changed"):
        C.require_ready(report, words)


def test_changed_executor_bytes_invalidate_an_otherwise_identical_mapping(monkeypatch):
    words = "Move the shown face."
    report = C.check(words, mapping(words))
    newer = C.catalog()
    newer["implementation_sha256"]["g3d/owner_mesh.py"] = "0" * 64
    newer["version_sha256"] = "1" * 64
    monkeypatch.setattr(C, "catalog", lambda: newer)
    with pytest.raises(ValueError, match="stale or changed"):
        C.require_ready(report, words)


def test_prepare_missing_coverage_refuses_before_scene_tools_or_estimate(selected_source, monkeypatch):
    root = edits.create_understand(selected_source.source, "Move the shown face.", destination=selected_source.destination)
    monkeypatch.setattr(job_run, "JobScene", fail_if_called)
    monkeypatch.setattr(job_run, "build_tools", fail_if_called)
    monkeypatch.setattr(job_run.E, "estimate_g3d", fail_if_called)
    with pytest.raises(ValueError, match="dry run is missing"):
        job_run.prepare_run(root, write_request=True)
    assert not (root / "runs").exists()


def test_direct_estimate_writer_cannot_bypass_coverage(selected_source):
    root = edits.create_understand(selected_source.source, "Move the shown face.", destination=selected_source.destination)
    with pytest.raises(A.ApprovalRefused, match="dry run is missing"):
        A.write_approval_request(root / "runs/bypass", job_id=root.name, request={}, estimate={}, suggested_cap_usd="1")
    assert not (root / "runs/bypass").exists()


def test_coverage_input_is_bound_to_approval_and_rechecked_before_approve(selected_source, monkeypatch):
    root, report = round_with_coverage(selected_source)
    spec, _ = job_run.prepare_run(root, "local-test-only", write_request=True)
    request = edits.read(root / "runs/local-test-only/approval-request.json")["request"]
    assert any(row["path"] == C.RELATIVE_PATH for row in request["inputs"])
    changed = C.catalog()
    changed["version_sha256"] = "changed"
    monkeypatch.setattr(C, "catalog", lambda: changed)
    with pytest.raises(A.ApprovalRefused, match="stale or changed"):
        A.write_approval(root / "runs/local-test-only", max_usd="1", owner_words="Offline fixture only.")
    assert not (root / "runs/local-test-only/approval.json").exists()


def test_after_approval_catalog_is_rechecked_before_any_model_clients(selected_source, monkeypatch):
    import asyncio
    root, report = round_with_coverage(selected_source)
    spec, _ = job_run.prepare_run(root, "bound-again", cap="1", write_request=True)
    A.write_approval(root / "runs/bound-again", max_usd="1", owner_words="Offline fixture only.")
    changed = C.catalog()
    changed["version_sha256"] = "changed after approval"
    monkeypatch.setattr(C, "catalog", lambda: changed)
    monkeypatch.setattr(loop, "model_clients", fail_if_called)
    with pytest.raises(A.ApprovalRefused, match="stale or changed"):
        asyncio.run(loop.run_astra(spec))
    assert not (root / "runs/bound-again/budget.json").exists()


def edit_doc(words, items, operations):
    return {"phase": "edit", "request_text": words,
            "understanding": {"items": items, "operations": operations}}


def test_two_items_cannot_be_covered_by_one_confirmed_operation(selected_source):
    words = "Move A. Move B."
    root = edits.create_understand(selected_source.source, words, destination=selected_source.destination)
    items = [{"id": "I1", "label": "Move A", "start": 0, "end": 7, "text": "Move A.",
              "operation_kinds": ["translate_region"]},
             {"id": "I2", "label": "Move B", "start": 8, "end": 15, "text": "Move B.",
              "operation_kinds": ["translate_region"]}]
    C.install(root, C.check(words, mapping(words, items=items)))
    op = {"id": "only-one", "kind": "translate_region"}
    understanding_items = [{"number": i, "operation_json": json.dumps(op)} for i in (1, 2)]
    with pytest.raises(ValueError, match="own distinct confirmed operations"):
        C.require_round(root, edit_doc(words, understanding_items, [op]))
    second = {"id": "second", "kind": "translate_region"}
    understanding_items[1]["operation_json"] = json.dumps(second)
    assert C.require_round(root, edit_doc(words, understanding_items, [op, second]))["status"] == "ready"


def test_confirmed_item_must_use_the_declared_available_operation(selected_source):
    root, report = round_with_coverage(selected_source)
    op = {"id": "wrong-kind", "kind": "rotate_region"}
    doc = edit_doc(report["request_text"], [{"number": 1, "operation_json": json.dumps(op)}], [op])
    with pytest.raises(ValueError, match="differ from its mapping"):
        C.require_round(root, doc)
    doc["understanding"]["items"][0]["disposition"] = "unsupported"
    with pytest.raises(ValueError, match="blocked items"):
        C.require_round(root, doc)


def test_edit_check_cli_is_local_and_writes_no_round_or_estimate(selected_source, tmp_path, monkeypatch):
    words = "  Move the shown face.\r\n"
    request_file, mapping_file, report_file = [tmp_path / name for name in ("request.txt", "mapping.json", "report.json")]
    request_file.write_bytes(words.encode())
    mapping_file.write_text(json.dumps(mapping(words)), encoding="utf-8")
    monkeypatch.setattr(job_run, "prepare_run", fail_if_called)
    monkeypatch.setattr(edits, "create_understand", fail_if_called)
    before = edits.tree_hashes(selected_source.source)
    result = cli.dispatch(cli.parser().parse_args(["job", "edit-check", str(selected_source.source),
        "--request-file", str(request_file), "--mapping", str(mapping_file), "--output", str(report_file)]))
    assert result["status"] == "ready" and result["request_text"] == words
    assert edits.read(report_file) == result
    assert edits.tree_hashes(selected_source.source) == before


def test_cli_nonready_report_has_nonzero_exit_without_an_estimate(selected_source, capsys):
    result = cli.main(["job", "edit-check", str(selected_source.source), "--request", "Invent a new mechanism."])
    assert result == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "needs_mapping" and report["api_calls"] == 0


def test_cli_new_edit_requires_a_ready_report_before_copying(selected_source, monkeypatch):
    monkeypatch.setattr(edits, "create_understand", fail_if_called)
    with pytest.raises(ValueError, match="edit-check"):
        cli.dispatch(cli.parser().parse_args(["job", "edit", str(selected_source.source),
            "--request", "Move the shown face.", "--cap", "1", "--settled-stop", "1"]))
