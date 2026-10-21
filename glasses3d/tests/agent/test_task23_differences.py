"""Explicit compared-best selection and partial observations, through real SDK tools."""
import copy
import json
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace

import pytest

from glasses3d.agent import differences as D
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.tools import look, finish_review as F
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_finish_review import review_scene, first
from glasses3d.tests.agent.test_task21_differences import decision_scene, entry, finish_args


def partial_comparison(s, refs, cid="partial"):
    result = look.capture(s.root, s.export, cid)
    result["views"] = [row for row in result["views"] if row["ref"] in refs]
    result["call_id"] = cid
    D.register_evidence(s, "compare_look_to_photos", result)


@pytest.mark.parametrize("supplied", [True, False])
def test_open_observation_retains_affected_refs_and_records_coverage(decision_scene, supplied):
    s = decision_scene
    first(s)
    D.record(s, **entry(refs=["front", "top"]))
    journal = s.root / "runs" / s.active_run_id / "differences.jsonl"
    partial_comparison(s, ["front"])
    before = journal.read_bytes()
    args = entry(evidence_ref="partial", refs=["front"] if supplied else None)
    result = record(invoke(s, "record_difference", args, cid="partial-observation"))
    assert result["status"] == "ok"
    assert result["difference"]["refs"] == ["front", "top"]
    assert result["difference"]["observation_refs"] == ["front"]
    assert journal.read_bytes().startswith(before)
    reopened = SimpleNamespace(root=s.root, active_run_id=s.active_run_id)
    assert D.snapshot(reopened) == result["differences"]


@pytest.mark.parametrize("status", ["fixed", "limitation"])
@pytest.mark.parametrize("refs", [["front"], ["front", "top"], None])
def test_resolution_after_partial_progress_still_requires_all_current_refs(decision_scene, status, refs):
    s = decision_scene
    first(s)
    D.record(s, **entry(refs=["front", "top"]))
    partial_comparison(s, ["front"])
    assert record(invoke(s, "record_difference", entry(evidence_ref="partial")))["status"] == "ok"
    current = D.snapshot(s)
    result = record(invoke(s, "record_difference", entry(evidence_ref="partial", refs=refs, status=status,
                           reason="The supported limitation requires every affected view."), cid="resolve-partial"))
    assert result["status"] == "refused"
    assert D.snapshot(s) == current
    final = record(invoke(s, "record_difference", entry(refs=["front", "top"], status=status,
                          reason="The full current comparison supports this limitation."), cid="resolve-full"))
    assert final["status"] == "ok"
    assert final["difference"]["observation_refs"] == ["front", "top"]


def test_open_observation_can_expand_affected_scope_and_no_refs_cannot_invent_coverage(decision_scene):
    s = decision_scene
    first(s)
    D.record(s, **entry())
    partial_comparison(s, ["top"])
    refused = record(invoke(s, "record_difference", entry(evidence_ref="partial", refs=None)))
    assert refused["status"] == "refused"
    result = record(invoke(s, "record_difference", entry(evidence_ref="partial", refs=["top"])))
    assert result["difference"]["refs"] == ["front", "top"]
    assert result["difference"]["observation_refs"] == ["top"]


def test_explicit_best_at_decision_carries_other_remaining_items(decision_scene):
    s = decision_scene
    first(s)
    D.record(s, **entry(id="temple-tip", refs=["top"], status="limitation",
                         reason="The current views support the stated limitation."))
    result = record(invoke(s, "record_difference", {**entry(), "best": True}, cid="decision-and-best"))
    assert result["status"] == "ok"
    assert D.formal_best(s) == {"checkpoint": "c0001", "run_id": s.active_run_id}
    assert result["best_selection"] == {"checkpoint": "c0001", "evidence_refs": ["first"],
                                       "remaining_difference_ids": ["temple-tip", "lens-hue"]}
    assert [item["id"] for item in result["differences"]] == ["temple-tip", "lens-hue"]
    assert record(invoke(s, "finish", finish_args(), cid="finish-open"))["code"] == "E_DIFFERENCES"


def test_best_can_select_head_with_partial_open_observation_without_resolving_unseen_views(decision_scene):
    s = decision_scene
    first(s)
    D.record(s, **entry(refs=["front", "top"]))
    partial_comparison(s, ["front"])
    result = record(invoke(s, "record_difference", {**entry(evidence_ref="partial"), "best": True}))
    assert result["status"] == "ok" and result["formal_best_checkpoint"]["checkpoint"] == "c0001"
    assert result["difference"]["status"] == "open"
    assert result["difference"]["refs"] == ["front", "top"]
    assert result["difference"]["observation_refs"] == ["front"]


@pytest.mark.parametrize("defect", ["head", "fingerprint", "unseen", "photo", "camera", "unknown"])
def test_best_refuses_noncurrent_or_unseen_comparison_without_partial_updates(decision_scene, defect):
    s = decision_scene
    first(s)
    s.best_checkpoint, s.best_checkpoint_run_id = "c0000", s.active_run_id
    selected = record(invoke(s, "record_difference", {**entry(), "best": True}, cid="valid-best"))
    assert selected["formal_best_checkpoint"]["checkpoint"] == "c0001"
    original = D.snapshot(s)
    args = {"items": [entry(id="first-item"), entry(id="second-item")], "best": True}
    if defect == "head":
        s.s.head["id"] = "c0002"
    elif defect == "fingerprint":
        s.s.head["fingerprint"] = "changed"
    elif defect == "unseen":
        s.mark_unseen("first")
    elif defect == "photo":
        s.binding["photos"][0]["sha256"] = "changed"
    elif defect == "camera":
        s.binding["cameras_sha256"] = "changed"
    else:
        args["items"][1]["evidence_ref"] = "unknown"
    result = record(invoke(s, "record_difference", args, cid="invalid-best"))
    assert result["status"] == "refused"
    assert D.snapshot(s) == original and D.formal_best(s)["checkpoint"] == "c0001"


def test_no_implicit_best_and_explicit_batch_best_keeps_every_difference(decision_scene):
    s = decision_scene
    first(s)
    s.best_checkpoint, s.best_checkpoint_run_id = "c0000", s.active_run_id
    items = [entry(), entry(id="temple-tip", refs=["top"])]
    result = record(invoke(s, "record_difference", {"items": items}, cid="ordinary-decision"))
    assert result["formal_best_checkpoint"]["checkpoint"] == "c0000"
    assert "best_selection" not in result
    result = record(invoke(s, "record_difference", {"items": items, "best": True}, cid="explicit-decision"))
    assert result["best_selection"]["checkpoint"] == "c0001"
    assert len(result["differences"]) == 2


def test_staged_open_observation_revalidates_its_coverage_without_dropping_scope(decision_scene):
    s = decision_scene
    first(s)
    D.record(s, **entry(refs=["front", "top"]))
    partial_comparison(s, ["front"])
    staged = D.prepare_updates(s, [entry(evidence_ref="partial")])
    assert staged[0]["observation_refs"] == ["front"]
    D.commit_finish(s, {"call_id": "stage", "difference_updates": staged, "export": s.export})
    assert D.snapshot(s)[0]["refs"] == ["front", "top"]
    assert D.snapshot(s)[0]["observation_refs"] == ["front"]


def test_best_and_remaining_differences_survive_scene_reopen(decision_scene):
    s = decision_scene
    first(s)
    s._state_lock, s._unseen, s.events = threading.RLock(), set(), []
    s.best_checkpoint = s.best_checkpoint_run_id = None
    s.state_path = s.root / "work/job_scene/state.json"
    previous_record_call = s.record_call
    def persist_receipt(*args):
        previous_record_call(*args)
        JobScene.save(s)
    s.record_call = persist_receipt
    result = record(invoke(s, "record_difference", {**entry(), "best": True}, cid="durable-best"))
    assert result["formal_best_checkpoint"]["checkpoint"] == "c0001"
    reopened = JobScene(s.root, job={})
    assert D.formal_best(reopened) == D.formal_best(s)
    assert D.snapshot(reopened) == result["differences"]


def test_saved_tool0047_exact_arguments_replay_on_copies(tmp_path):
    """Replay the saved refusal with actual photo/camera/export identity checks."""
    source = Path(__file__).resolve().parents[2] / "data/jobs/oakley-oo9208-v2-7035e95c"
    run_id = "20261021T123308-aa22d4"
    run = source / "runs" / run_id
    if not run.is_dir():
        pytest.skip("Local paid-run evidence is intentionally not committed")
    arguments_path = run / "tools/0047-record_difference/arguments.json"
    arguments_bytes = arguments_path.read_bytes()
    saved = json.loads(arguments_bytes)
    source_journal = run / "differences.jsonl"
    source_bytes = source_journal.read_bytes()
    rows = [json.loads(line) for line in source_bytes.splitlines()]
    at = next(index for index, row in enumerate(rows)
              if row.get("evidence_ref") == saved["arguments"]["evidence_ref"])
    prefix = copy.deepcopy(rows[:at + 1])
    binding = prefix[-1]["binding"]
    root = tmp_path / "replay"
    shutil.copytree(source / "cameras" / binding["basis"], root / "cameras" / binding["basis"])
    shutil.copytree(source / "inputs", root / "inputs")
    shutil.copytree(source / "images", root / "images")
    shutil.copy2(source / "job.json", root / "job.json")
    export = Path(prefix[-1]["export_path"])
    copied_export = root / "work/exports" / export.name
    copied_export.parent.mkdir(parents=True)
    shutil.copy2(export, copied_export)
    prefix[-1]["export_path"] = str(copied_export)
    journal = root / "runs" / run_id / "differences.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("".join(json.dumps(row) + "\n" for row in prefix), encoding="utf-8")
    s = SimpleNamespace(root=root, active_run_id=run_id, instruction_profile="decision-20261021",
                        s=SimpleNamespace(head={"id": binding["checkpoint"], "fingerprint": binding["fingerprint"]}))
    # No patched evidence function: F.evidence hashes copied cameras/photos and D checks export bytes.
    assert F.evidence(root, s.s.head, {"sha256": binding["export_sha256"]})["basis"] == binding["basis"]
    before = journal.read_bytes()
    result = D.record(s, **saved["arguments"], call_id=saved["call_id"])
    assert result["status"] == "ok"
    item = result["difference"]
    assert item["status"] == "open" and item["observation_refs"] == saved["arguments"]["refs"]
    assert item["refs"] == [f"reference-{number:02d}" for number in range(1, 6)]
    for field in ("what_differs", "evidence_ref", "last_correction", "outcome", "next_action", "reason"):
        assert item[field] == saved["arguments"][field]
    assert journal.read_bytes().startswith(before)
    assert source_journal.read_bytes() == source_bytes and arguments_path.read_bytes() == arguments_bytes
