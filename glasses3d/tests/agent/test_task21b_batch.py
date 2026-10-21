"""Atomic difference updates through the real SDK wrapper, with no paid calls."""
import hashlib
import json

import pytest

from glasses3d.agent import differences as D
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_finish_review import review_scene, first
from glasses3d.tests.agent.test_task21_differences import decision_scene, entry, finish_args


def journal(scene):
    return scene.root / "runs" / scene.active_run_id / "differences.jsonl"


def test_sdk_batch_appends_every_valid_item_and_keeps_one_item_calls(decision_scene):
    s = decision_scene
    first(s)
    original = journal(s).read_bytes()
    items = [entry(), entry(id="temple-tip", refs=["top"], what_differs="Temple tip is long.")]
    output = record(invoke(s, "record_difference", {"items": items}, cid="batch-open"))
    assert output["status"] == "ok"
    assert [item["id"] for item in output["updated_differences"]] == ["lens-hue", "temple-tip"]
    assert D.snapshot(s) == output["differences"] == output["updated_differences"]
    assert journal(s).read_bytes().startswith(original)
    rows = D._records(s)
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert [row["call_id"] for row in rows if row["kind"] == "difference"] == ["batch-open"] * 2
    output = record(invoke(s, "record_difference", entry(status="fixed"), cid="single-fixed"))
    assert output["status"] == "ok" and output["difference"]["status"] == "fixed"
    assert len(D.snapshot(s)) == 2


def test_batch_reports_all_failing_ids_and_appends_nothing(decision_scene):
    s = decision_scene
    first(s)
    original = journal(s).read_bytes()
    items = [entry(id="valid"), entry(id="unknown", evidence_ref="never-delivered"),
             entry(id="missing-reason", status="limitation", reason=" ")]
    output = record(invoke(s, "record_difference", {"items": items}, cid="invalid-batch"))
    assert output["status"] == "refused"
    assert output["invalid_difference_ids"] == ["unknown", "missing-reason"]
    assert "unknown" in output["message"] and "missing-reason" in output["message"]
    assert journal(s).read_bytes() == original and D.snapshot(s) == []


def test_large_batch_refusal_retains_every_failing_id_in_bounded_output(decision_scene):
    s = decision_scene
    first(s)
    original = journal(s).read_bytes()
    ids = [f"item-{index:02d}-" + "x" * 88 for index in range(20)]
    items = [entry(id=key, evidence_ref="never-delivered") for key in ids]
    output = record(invoke(s, "record_difference", {"items": items}, cid="many-invalid"))
    assert output["status"] == "refused"
    if "full_report" in output:
        report = output["full_report"]
        payload = (s.root / report["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == report["sha256"]
        output = json.loads(payload)["result"]
    assert output["invalid_difference_ids"] == ids
    assert len(output["errors"]) == len(ids)
    assert journal(s).read_bytes() == original and D.snapshot(s) == []


@pytest.mark.parametrize("malformed", ["status", "missing-field"])
@pytest.mark.parametrize("tool_name", ["record_difference", "finish"])
def test_sdk_schema_refusal_names_invalid_batch_item(decision_scene, malformed, tool_name):
    s = decision_scene
    first(s)
    original = journal(s).read_bytes()
    item = entry(id="schema-invalid")
    if malformed == "status":
        item["status"] = "invented"
    else:
        del item["outcome"]
    items = [entry(id="valid"), item]
    arguments = {"items": items} if tool_name == "record_difference" else finish_args(difference_updates=items)
    output = invoke(s, tool_name, arguments, cid="invalid-schema")
    assert isinstance(output, str) and "schema-invalid" in output
    assert journal(s).read_bytes() == original and D.snapshot(s) == []


@pytest.mark.parametrize("change", ["refs", "unknown", "unseen", "head", "photo", "reason"])
def test_invalid_later_batch_item_preserves_journal_bytes(decision_scene, change):
    s = decision_scene
    first(s)
    assert record(invoke(s, "record_difference", entry(refs=["front", "top"]), cid="seed"))["status"] == "ok"
    original = journal(s).read_bytes()
    invalid = entry(status="fixed", refs=["front", "top"])
    if change == "refs":
        invalid["refs"] = ["front"]
    elif change == "unknown":
        invalid["evidence_ref"] = "unknown-evidence"
    elif change == "unseen":
        s.mark_unseen("first")
    elif change == "head":
        s.s.head["fingerprint"] = "changed"
    elif change == "photo":
        s.binding["photos"][0]["sha256"] = "changed"
    else:
        invalid.update(status="limitation", reason="")
    output = record(invoke(s, "record_difference", {"items": [entry(id="valid"), invalid]}, cid="invalid-batch"))
    assert output["status"] == "refused" and "lens-hue" in output["message"]
    assert journal(s).read_bytes() == original
    assert D.snapshot(s)[0]["status"] == "open"


def test_batch_cannot_resolve_then_drop_refs_through_a_duplicate_id(decision_scene):
    s = decision_scene
    first(s)
    original = journal(s).read_bytes()
    output = record(invoke(s, "record_difference", {"items": [entry(refs=["front", "top"]),
                             entry(status="fixed", refs=["front"])]}, cid="duplicate"))
    assert output["status"] == "refused" and "lens-hue" in output["message"]
    assert journal(s).read_bytes() == original


def test_prepared_batch_does_not_append_and_uses_current_review_default(decision_scene):
    s = decision_scene
    first(s)
    original = journal(s).read_bytes()
    item = entry(status="fixed")
    item.pop("evidence_ref")
    prepared = D.prepare_updates(s, [item], default_evidence_ref="first", current_export=s.export)
    assert prepared[0]["evidence_ref"] == "first" and prepared[0]["status"] == "fixed"
    assert journal(s).read_bytes() == original and D.snapshot(s) == []


def test_staged_batch_revalidates_all_items_before_any_append(decision_scene):
    s = decision_scene
    first(s)
    original = journal(s).read_bytes()
    result = {"call_id": "confirm", "status": "accepted", "export": s.export,
              "difference_updates": [entry(id="valid", status="fixed"),
                                     entry(id="invalid", evidence_ref="unknown", status="fixed")]}
    with pytest.raises(ValueError, match="invalid"):
        D.commit_finish(s, result)
    assert journal(s).read_bytes() == original
