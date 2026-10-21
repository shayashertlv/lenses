"""The saved offline conversations reconcile real decision-profile finish receipts."""
import copy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from glasses3d.agent import differences as D, replay
from glasses3d.agent.jsonl import read_jsonl
from glasses3d.tests.agent.loop_support import png_bytes
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_finish_review import review_scene
from glasses3d.tests.agent.test_task21_differences import decision_scene
from glasses3d.tests.proofs import assembly_m12_fallback as fallback
from glasses3d.tests.proofs import assembly_m3 as m3
from glasses3d.tests.proofs import assembly_m6_owner_edit as m6
from glasses3d.tests.proofs import task11_oakley_money as money


CASES = ["m3-first", "m3-second", "m6-child", "m6-parent", "fallback", "money"]


def reply_arguments(reply):
    calls = [item for item in reply.output if item["type"] == "function_call"]
    assert len(calls) == 1 and calls[0]["name"] == "finish"
    return json.loads(calls[0]["arguments"])


def confirming_arguments(case, root, monkeypatch):
    refs = ["front", "top"]
    if case.startswith("m3"):
        monkeypatch.setattr(m3, "TRUTH", {ref + ".png": () for ref in refs})
        replies = m3.resume_replies(1 if case == "m3-first" else 2)
        assert len(replies) == 3
        return reply_arguments(replies[-1])
    if case.startswith("m6"):
        monkeypatch.setattr(m6.edits.Authority, "resolve", lambda *a, **k: {"operations": [{"id": "one"}]})
        monkeypatch.setattr(m6, "result", lambda *a: {"review": {"required_refs": refs}})
        script = m6.Script(root, {}, "edit", root)
        name, args, cid, where, expected = script.edit(4 if case == "m6-child" else 7)
        assert name == "finish" and expected == {"accepted"}
        assert cid == args["review_call_id"].replace("-1", "-2")
        assert (where != root) == (case == "m6-child")
        return args
    if case == "fallback":
        script = fallback.FinishScript(root)
        script.transport.creates[:] = [{}] * 3
        script.transport.replies[:] = [None] * 3
        monkeypatch.setattr(script.transport, "handle", lambda request: None)
        monkeypatch.setattr(fallback, "tool_result", lambda *a: {
            "status": "review_required", "review": {"required_refs": refs}})
        script.handle(SimpleNamespace(url=SimpleNamespace(path=replay.COUNT_PATH)))
        return reply_arguments(script.transport.replies[-1])
    replies = money.finish_pair(refs, replay.default_usage(1), replay.default_usage(2), 1, 2)
    assert len(replies) == 2
    return reply_arguments(replies[-1])


@pytest.mark.parametrize("case", CASES)
def test_proof_confirmations_use_supported_inline_limitations(case, decision_scene, monkeypatch):
    s = decision_scene
    args = confirming_arguments(case, s.root, monkeypatch)
    review_id = args["review_call_id"]
    shown = record(invoke(s, "finish", {"summary": "Deliver this proof's current review"}, cid=review_id))
    assert shown["status"] == "review_required"
    assert {row["ref"] for row in args["per_photo"]} == set(shown["review"]["required_refs"])
    updates = args["difference_updates"]
    assert updates and all(item["status"] == "limitation" for item in updates)
    assert all(item["evidence_ref"] == review_id and item["reason"] for item in updates)
    assert all(row["differences"].casefold() != "none" and row["difference_ids"] for row in args["per_photo"])
    if case == "fallback":
        assert "0.2 mm" in updates[0]["what_differs"]
        assert "outside point A's footprint" in updates[0]["last_correction"]
    elif case.startswith("m6"):
        assert "projected silhouettes can shift" in updates[0]["what_differs"]
        assert "owner visual approval" in updates[0]["next_action"]
    confirmed = record(invoke(s, "finish", args, cid="proof-confirmed"))
    assert confirmed["status"] == "accepted", confirmed
    assert D.snapshot(s) == [{**item, "observation_refs": item["refs"]} for item in updates]
    assert confirmed["review"]["difference_mappings"] == [
        {"ref": row["ref"], "difference_ids": row["difference_ids"]} for row in args["per_photo"]]


@pytest.mark.parametrize("case", CASES)
def test_proof_confirmations_still_refuse_without_reconciliation(case, decision_scene, monkeypatch):
    s = decision_scene
    args = copy.deepcopy(confirming_arguments(case, s.root, monkeypatch))
    assert record(invoke(s, "finish", {"summary": "Deliver review"}, cid=args["review_call_id"]))["status"] == "review_required"
    args.pop("difference_updates")
    for row in args["per_photo"]:
        row.pop("difference_ids")
    refused = record(invoke(s, "finish", args, cid="unreconciled"))
    assert refused["status"] == "refused" and refused["code"] == "E_DIFFERENCES"
    assert s.finish is None and refused["open_difference_ids"]


def test_dynamic_proof_exhaustion_names_the_expected_finish(tmp_path, monkeypatch):
    script = fallback.FinishScript(tmp_path)
    script.transport.creates[:] = [{}] * 4
    script.transport.replies[:] = [None] * 4
    with pytest.raises(AssertionError, match="Fallback must finish after four scripted requests"):
        script.handle(SimpleNamespace(url=SimpleNamespace(path=replay.COUNT_PATH)))
    monkeypatch.setattr(m6.edits.Authority, "resolve", lambda *a, **k: {"operations": [{"id": "one"}]})
    with pytest.raises(AssertionError, match="Edit must stop after accepted finish"):
        m6.Script(tmp_path, {}, "edit", tmp_path).edit(8)


def test_saved_money_scene_registers_current_review_in_its_actual_run(decision_scene):
    """Exercise the proof's real adapter and loop, not just its reply dictionaries."""
    s = decision_scene
    inputs = s.root / "test-inputs"
    inputs.mkdir()
    for index, ref in enumerate(("front", "top"), 1):
        (inputs / (ref + ".png")).write_bytes(png_bytes(seed=index))
    views = s.root / "test-views.json"
    views.write_text(json.dumps({ref + ".png": {"kind": ref, "side": None}
                                 for ref in ("front", "top")}), encoding="utf-8")
    created = money.jobs.create_job(images=inputs, views=views, name="Saved evidence test",
        description="Synthetic source for the offline money-proof adapter.", jobs_root=s.root / "intake")
    shutil.copy2(created / "job.json", s.root / "job.json")
    shutil.copytree(created / "images", s.root / "images")
    s.binding["photos"] = [{"ref": ref, "sha256": money.sha(s.root / "images" / (ref + ".png"))}
                           for ref in ("front", "top")]
    saved = record(invoke(s, "finish", {"summary": "Synthetic saved review evidence"}, cid="saved-review"))
    assert saved["status"] == "review_required"
    saved["look_comparison"]["checkpoint"] = saved["checkpoint"]
    destination = s.root / "money-replay"
    hashes = {}
    outcome = money.scenario(s.root, destination, saved,
        money.finish_pair(["front", "top"], replay.default_usage(1), replay.default_usage(2), 1, 2), hashes)
    assert outcome["status"] == "completed" and outcome["requests"] == 2
    assert [row["status"] for row in outcome["calls"]] == ["review_required", "accepted"]
    journal = read_jsonl(destination / "runs/offline-policy/differences.jsonl").rows
    assert journal and {row["run_id"] for row in journal} == {"offline-policy"}
    evidence = next(row for row in journal if row["kind"] == "evidence" and row["evidence_ref"] == "review")
    assert evidence["binding"] == saved["review"]["binding"]
    assert Path(evidence["export_path"]).is_relative_to(destination)
    assert money.sha(evidence["export_path"]) == evidence["binding"]["export_sha256"]
    assert [row["item"]["status"] for row in journal if row["kind"] == "difference"] == ["limitation"]
    assert all(money.sha(path) == digest for path, digest in hashes.items())


def test_saved_money_scene_uses_bound_review_log_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(money.jobs, "load_job", lambda root: {})
    saved = {"checkpoint": "c0001", "fingerprint": "f" * 64, "export": {}}
    scene = money.SavedScene(tmp_path, saved)
    log = SimpleNamespace(run_id="separate-log", run_dir=tmp_path / "host-review-log")
    scene.bind_review_log(log)
    assert scene.active_run_id == log.run_id and scene._review_log is log
    assert D._path(scene) == log.run_dir / "differences.jsonl"
