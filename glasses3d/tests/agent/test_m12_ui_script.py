"""M12 scripted confirmations exercise the real decision-profile finish tool."""
import copy
import json

import httpx
import pytest

from glasses3d.agent import differences, replay
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_finish_review import review_scene
from glasses3d.tests.agent.test_task21_differences import decision_scene
from glasses3d.tests.proofs import m12_ui_script as proof


@pytest.mark.parametrize("scenario,index,review_id", [
    ("success", 12, "call_0012"), ("fallback", 3, "fallback-review")])
def test_confirming_script_reconciles_real_finish_with_honest_limitations(
        decision_scene, monkeypatch, tmp_path, scenario, index, review_id):
    s = decision_scene
    review = record(invoke(s, "finish", {"summary": "Inspect the procedure test"}, cid=review_id))
    assert review["status"] == "review_required"
    monkeypatch.setattr(proof, "tool_result", lambda root, cid: review if cid == review_id else None)
    script = proof.make_script(s.root, {"scenario": scenario}, tmp_path / "script", None)
    name, args, cid = script.reply(index)
    assert name == "finish" and args["review_call_id"] == review_id
    assert {row["ref"] for row in args["per_photo"]} == set(review["review"]["required_refs"])
    assert all(proof.LABEL in row["differences"] for row in args["per_photo"])
    assert all(item["status"] == "limitation" and item["evidence_ref"] == review_id
               for item in args["difference_updates"])

    # Removing inline reconciliation must still refuse; no runtime gate is relaxed.
    stale = copy.deepcopy(args)
    stale.pop("difference_updates")
    refused = record(invoke(s, name, stale, cid="unreconciled"))
    assert refused["status"] == "refused" and refused["code"] == "E_DIFFERENCES"
    assert differences.snapshot(s) == [] and s.finish is None

    accepted = record(invoke(s, name, args, cid=cid))
    assert accepted["status"] == "accepted"
    assert accepted["difference_reconciliation"] is True
    items = differences.snapshot(s)
    assert len(items) == len(args["difference_updates"])
    assert all(item["status"] == "limitation" and item["evidence_ref"] == review_id for item in items)
    assert accepted["review"]["per_photo"] == args["per_photo"]
    assert {key for row in args["per_photo"] for key in row["difference_ids"]} == {
        item["id"] for item in items}
    if scenario == "fallback":
        assert all("0.2 mm" in row["differences"] and "outside marked polygons" in row["differences"]
                   for row in args["per_photo"])
    else:
        assert all("procedure test, not product acceptance" in row["differences"] for row in args["per_photo"])


@pytest.mark.parametrize("scenario,index,expected", [
    ("success", 13, "success script exhausted after 13 requests"),
    ("fallback", 4, "fallback script exhausted after four requests")])
def test_exhausted_script_records_actionable_error(tmp_path, scenario, index, expected):
    script = proof.make_script(tmp_path, {"scenario": scenario}, tmp_path, None)
    script.transport.creates.extend([{}] * index)
    request = httpx.Request("POST", "https://api.openai.com" + replay.COUNT_PATH, json={})
    with pytest.raises(AssertionError, match=expected):
        script.handle(request)
    error = json.loads((tmp_path / "replay-handler-error.json").read_text(encoding="utf-8"))
    assert error["index"] == index and error["type"] == "AssertionError"
    assert expected in error["message"] and "confirming finish result" in error["message"]
