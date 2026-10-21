"""Decision-profile differences through real SDK tools; local images, no paid calls."""
from types import SimpleNamespace

import pytest
from pathlib import Path

from glasses3d.agent import differences as D
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_finish_review import review_scene, first


@pytest.fixture
def decision_scene(review_scene):
    review_scene.instruction_profile = "decision-20261021"
    review_scene.active_run_id = "decision-test"
    return review_scene


def entry(**changes):
    return {"id": "lens-hue", "what_differs": "Lens hue remains too green at the outer edge.",
            "evidence_ref": "first", "status": "open", "last_correction": "No correction tried yet.",
            "outcome": "The worn comparison shows the green edge.", "next_action": "Revise the coating and compare.",
            "refs": ["front"], **changes}


def finish_args(**changes):
    return {"summary": "Photo review complete", "review_call_id": "first",
            "per_photo": [{"ref": "front", "differences": "none"}, {"ref": "top", "differences": "none"}], **changes}


def test_sdk_list_is_persistent_append_only_and_run_scoped(decision_scene):
    s = decision_scene
    first(s)
    out = record(invoke(s, "record_difference", entry(), cid="record-1"))
    assert out["status"] == "ok" and D.snapshot(s)[0]["status"] == "open"
    path = s.root / "runs/decision-test/differences.jsonl"
    before = path.read_bytes()
    fixed = entry(status="fixed", last_correction="Changed the outer coating distribution.",
                  outcome="Worn comparison now matches the edge hue.", next_action="No further correction for this item.")
    assert record(invoke(s, "record_difference", fixed, cid="record-2"))["status"] == "ok"
    assert path.read_bytes().startswith(before)
    fresh = SimpleNamespace(root=s.root, active_run_id=s.active_run_id)
    assert D.snapshot(fresh)[0]["status"] == "fixed"
    s.active_run_id = "other-run"
    assert D.snapshot(s) == []
    refused = record(invoke(s, "record_difference", entry(), cid="other-run-record"))
    assert refused["status"] == "refused" and "unknown evidence_ref" in refused["message"]


def test_sdk_look_call_can_support_a_difference_after_transport_rewrites_image_paths(decision_scene):
    s = decision_scene
    look = record(invoke(s, "compare_look_to_photos", {}, cid="look-first"))
    assert look["status"] == "ok"
    out = record(invoke(s, "record_difference", entry(evidence_ref="look-first"), cid="look-item"))
    assert out["status"] == "ok" and D.snapshot(s)[0]["evidence_ref"] == "look-first"


@pytest.mark.parametrize("closed", ["fixed", "limitation"])
def test_sdk_finish_refuses_open_then_accepts_current_supported_resolution(decision_scene, closed):
    s = decision_scene
    first(s)
    assert record(invoke(s, "record_difference", entry(), cid="record-open"))["status"] == "ok"
    out = record(invoke(s, "finish", finish_args(), cid="refused-final"))
    assert out["status"] == "refused" and out["code"] == "E_DIFFERENCES"
    assert out["open_difference_ids"] == ["lens-hue"] and s.finish is None
    resolved = entry(status=closed, last_correction="Revised coating distribution and compared.",
                     outcome="The worn edge is closer; the current photo-camera panel remains greener.",
                     next_action="No further action for this item.",
                     reason="Remaining difference is the fixed room's reflection; changing it loses the worn hue."
                            if closed == "limitation" else None)
    assert record(invoke(s, "record_difference", resolved, cid="resolve"))["status"] == "ok"
    final = record(invoke(s, "finish", finish_args(), cid="accepted-final"))
    assert final["status"] == "accepted" and s.finish == final


@pytest.mark.parametrize("when", ["first", "second"])
def test_finish_faults_automatically_become_open_items(decision_scene, when):
    s = decision_scene
    rows = [{"ref": "front", "differences": "none"},
            {"ref": "top", "differences": "Temple tips remain too long."}]
    if when == "first":
        initial = first(s, per_photo=rows)
        assert initial["status"] == "review_required"
    else:
        first(s)
    final = record(invoke(s, "finish", finish_args(per_photo=rows), cid="second"))
    assert final["status"] == "refused" and final["code"] == "E_DIFFERENCES"
    items = D.snapshot(s)
    assert len(items) == 1 and items[0]["what_differs"] == rows[1]["differences"]
    assert items[0]["refs"] == ["top"] and items[0]["status"] == "open"
    args = entry(id=items[0]["id"], what_differs=items[0]["what_differs"], refs=["top"],
                 status="limitation", reason="Shorter tips intrude into the accepted worn-side fit.",
                 last_correction="Compared a shorter tip and restored this state.",
                 outcome="The current top view still has extra tip length; the worn-side fit remains preserved.",
                 next_action="No further tip change supported by these views.")
    assert record(invoke(s, "record_difference", args, cid="resolve-tip"))["status"] == "ok"
    rows[1] = {"ref": "top", "differences": "Residual tip length is the documented fit limitation.",
               "difference_ids": [items[0]["id"]]}
    assert record(invoke(s, "finish", finish_args(per_photo=rows), cid="third"))["status"] == "accepted"
    assert len(D.snapshot(s)) == 1


@pytest.mark.parametrize("change", ["unknown", "unseen", "head", "fingerprint", "basis", "cameras", "photo", "other-run"])
def test_record_refuses_unknown_unseen_or_stale_evidence(decision_scene, change):
    s = decision_scene
    first(s)
    args = entry()
    if change == "unknown":
        args["evidence_ref"] = "invented"
    elif change == "unseen":
        s.mark_unseen("first")
    elif change in {"head", "fingerprint"}:
        s.s.head["id" if change == "head" else "fingerprint"] = "changed"
    elif change == "photo":
        s.binding["photos"][0]["sha256"] = "changed"
    elif change == "other-run":
        s.active_run_id = "different-run"
    else:
        s.binding["basis_sha256" if change == "basis" else "cameras_sha256"] = "changed"
    out = record(invoke(s, "record_difference", args, cid="refuse"))
    assert out["status"] == "refused" and D.snapshot(s) == []


@pytest.mark.parametrize("closed", ["fixed", "limitation"])
def test_later_mutation_does_not_leave_a_stale_resolution_acceptable(decision_scene, closed):
    s = decision_scene
    first(s)
    args = entry(status=closed, reason="This fixed reflection cannot move without losing the worn colour.")
    assert record(invoke(s, "record_difference", args, cid="resolve-old"))["status"] == "ok"
    s.s.head["fingerprint"] = s.export["fingerprint"] = "new-fingerprint"
    initial = record(invoke(s, "finish", {"summary": "new state"}, cid="new-review"))
    assert initial["status"] == "review_required"
    final = record(invoke(s, "finish", finish_args(review_call_id="new-review"), cid="new-final"))
    assert final["status"] == "refused" and final["stale_difference_ids"] == ["lens-hue"]
    assert record(invoke(s, "record_difference", {**args, "evidence_ref": "new-review"}, cid="resolve-new"))["status"] == "ok"
    assert record(invoke(s, "finish", finish_args(review_call_id="new-review"), cid="new-final-2"))["status"] == "accepted"


def test_failed_delivery_cannot_register_evidence_or_first_finish_faults(decision_scene, monkeypatch):
    from glasses3d.agent import tools as T
    s = decision_scene
    monkeypatch.setattr(T, "limit_output", lambda *a, **k: (_ for _ in ()).throw(OSError("delivery failed")))
    out = invoke(s, "finish", {"summary": "inspect", "per_photo": [{"ref": "top", "differences": "Tip too long."}]}, cid="first")
    assert isinstance(out, str) and "delivery failed" in out
    assert D.snapshot(s) == []
    with pytest.raises(ValueError, match="delivered"):
        D.validate_evidence(s, "first")


@pytest.mark.parametrize("change", [{"reason": ""}, {"outcome": ""}, {"last_correction": ""}, {"next_action": ""},
                                   {"refs": ["missing"]}])
def test_limitation_requires_specific_reason_outcome_and_current_photo_refs(decision_scene, change):
    first(decision_scene)
    args = {**entry(status="limitation", reason="The reflection mismatch persists in this fixed lighting."), **change}
    assert record(invoke(decision_scene, "record_difference", args, cid="invalid"))["status"] == "refused"
    assert D.snapshot(decision_scene) == []


def test_formal_best_is_host_recorded_for_current_run(decision_scene):
    s = decision_scene
    s.best_checkpoint = "c0001"
    s.best_checkpoint_run_id = "earlier-run"
    assert D.formal_best(s)["checkpoint"] is None
    s.best_checkpoint_run_id = s.active_run_id
    assert D.formal_best(s) == {"checkpoint": "c0001", "run_id": "decision-test"}


def test_corrupt_or_torn_journal_refuses_without_rewriting(decision_scene):
    s = decision_scene
    first(s)
    path = s.root / "runs/decision-test/differences.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"torn":')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="torn tail"):
        D.snapshot(s)
    with pytest.raises(ValueError, match="torn tail"):
        D.record(s, **entry())
    assert path.read_bytes() == before


def test_unknown_finish_mapping_does_not_accept(decision_scene):
    first(decision_scene)
    rows = finish_args()["per_photo"]
    rows[0].update(differences="Still green", difference_ids=["invented"])
    out = record(invoke(decision_scene, "finish", finish_args(per_photo=rows), cid="unknown-map"))
    assert out["status"] == "refused" and out["code"] == "E_DIFFERENCES"


def test_legacy_runtime_still_rejects_new_per_photo_mapping_field(review_scene):
    output = invoke(review_scene, "finish", {"summary": "review", "per_photo": [
        {"ref": "front", "differences": "none", "difference_ids": []}]}, cid="legacy-new-field")
    assert isinstance(output, str) and "Invalid JSON input" in output


@pytest.mark.parametrize("profile", ["decision-20261021", "legacy-20261021"])
def test_first_finish_message_preserves_profile_colour_priority(review_scene, profile):
    review_scene.instruction_profile = profile
    review_scene.active_run_id = "message-test"
    message = first(review_scene)["message"]
    old = ("Inspect every full photo/drawing pair and native detail, plus the worn front. Then call finish again "
           "with this call's review_call_id and one nonblank differences entry (or 'none') per required ref. "
           "If there are no usable photo cameras, return an empty per_photo list and acknowledge the unavailable evidence.")
    if profile == "legacy-20261021":
        assert message == old
    else:
        assert message.startswith("Judge lens colour on the worn, on-face AR images against the owner photos first.")
        assert "shape, see-through and measurements" in message
        assert "a better photo-background colour number does not overrule the worn result" in message
        assert "Report disagreements and explain the kept choice" in message
        assert "plus the worn front" not in message


def test_complete_corrupt_record_refuses_without_rewriting(decision_scene):
    first(decision_scene)
    path = decision_scene.root / "runs/decision-test/differences.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{}\n')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="invalid differences journal"):
        D.snapshot(decision_scene)
    assert path.read_bytes() == before


def test_same_head_changed_export_refuses_old_resolution(decision_scene):
    from glasses3d.agent.tools import arview
    s = decision_scene
    first(s)
    args = entry(status="fixed")
    assert record(invoke(s, "record_difference", args, cid="resolve"))["status"] == "ok"
    Path(s.export["path"]).write_bytes(b"new bytes at the same head")
    digest = arview.sha256(Path(s.export["path"]))
    s.export.update(sha256=digest, validation_sha256=digest)
    s.export["print_check"]["glb_sha256"] = digest
    out = record(invoke(s, "record_difference", args, cid="stale-record"))
    assert out["status"] == "refused" and "export bytes changed" in out["message"]
    assert record(invoke(s, "finish", {"summary": "new export"}, cid="new-review"))["status"] == "review_required"
    out = record(invoke(s, "finish", finish_args(review_call_id="new-review"), cid="new-finish"))
    assert out["status"] == "refused" and out["stale_difference_ids"] == ["lens-hue"]


def test_changed_export_file_path_also_invalidates_old_resolution(decision_scene):
    from glasses3d.agent.tools import arview
    s = decision_scene
    first(s)
    args = entry(status="fixed")
    assert record(invoke(s, "record_difference", args, cid="resolve"))["status"] == "ok"
    path = s.root / "work/exports/new.glb"
    path.write_bytes(b"new export path with distinct bytes")
    digest = arview.sha256(path)
    s.export.update(path=str(path), sha256=digest, validation_sha256=digest)
    s.export["print_check"]["glb_sha256"] = digest
    assert record(invoke(s, "finish", {"summary": "new export"}, cid="new-review"))["status"] == "review_required"
    out = record(invoke(s, "record_difference", args, cid="stale-record"))
    assert out["status"] == "refused" and "current export differs" in out["message"]
    out = record(invoke(s, "finish", finish_args(review_call_id="new-review"), cid="new-finish"))
    assert out["status"] == "refused" and out["stale_difference_ids"] == ["lens-hue"]
