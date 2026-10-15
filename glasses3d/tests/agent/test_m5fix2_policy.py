"""Owner-authorized seating policy and separate money presentation; no model or network calls."""
from copy import deepcopy
import json
import pytest

from glasses3d.agent import delivery as D
from glasses3d.agent.money import owner_money, money_text, reservation_refusal
from glasses3d.tests.agent.test_delivery import make_finished
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record  # noqa: F401


def test_failed_seating_report_does_not_block_finish_and_is_not_rewritten(finish_scene):
    expected = {"ok": False, "lenses": [{"pass": False, "frame_type_source": "inferred",
                "reasons": ["visible edge unsupported"], "stations": [{"capture_mm": -.7}]}]}
    finish_scene.export["seat_check"] = deepcopy(expected)
    result = record(invoke(finish_scene, "finish", {"summary": "Edge matches the photos; seating report has a gap."}))
    assert result["status"] == "review_required" and result["failures"] == []
    result = record(invoke(finish_scene, "finish", {"summary": "Seating gap reported; no usable photo cameras.",
                    "review_call_id": "call_test", "per_photo": []}, cid="second"))
    assert result["status"] == "accepted" and result["failures"] == []
    assert result["export"]["seat_check"] == expected == finish_scene.finish["export"]["seat_check"]
    assert [step[0] for step in finish_scene.finish_steps] == ["export", "ar", "export", "ar"]


def test_failed_seating_survives_finished_delivery_and_reload(tmp_path):
    scene, result = make_finished(tmp_path)
    seating = scene.finish["export"]["seat_check"]
    seating.update(ok=False, reasons=["Photo-supported visible edge; diagnostic failure retained."])
    original = deepcopy(seating)
    D.deliver_finished(tmp_path, scene, result)
    loaded = D.load_delivery(tmp_path)
    saved = loaded["report_data"]["seating"]
    assert saved["ok"] is False and saved["reasons"] == original["reasons"]
    assert saved["lenses"] == original["lenses"]
    assert loaded["report_data"]["finished"] is True


def test_money_presentation_keeps_every_separate_amount_without_changing_ledger():
    totals = {"settled_micro_usd": 14_462_361, "held_micro_usd": 8_346_900, "unknown_micro_usd": 0,
              "committed_micro_usd": 22_809_261, "committed_usd": "22.809261"}
    before = deepcopy(totals)
    shown = owner_money({"cost": totals})["cost"]
    assert totals == before
    assert "spent $14.462361 (settled)" in shown["money_report"]
    assert "held for the request in flight $8.346900 (released after it settles)" in shown["money_report"]
    assert "committed" not in json.dumps(shown) and "22.809261" not in json.dumps(shown)
    settled = {**totals, "settled_micro_usd": 15_468_835, "held_micro_usd": 0}
    assert "spent $15.468835 (settled)" in money_text(settled)
    assert "in flight $0.000000" in money_text(settled)
    assert "unresolved request hold $8.346900 (kept until reconciled)" in money_text(
        {**totals, "held_micro_usd": 0, "unknown_micro_usd": 8_346_900})
    refusal = reservation_refusal(8_389_200, 23_000_000, totals)
    assert "committed" not in refusal and "hold of $8.389200" in refusal
    assert "maximum reserve $23.000000" in refusal


@pytest.mark.parametrize("amount", ["unavailable", {"property": 1}, False, "NaN", "Infinity", 4])
def test_unrelated_tool_properties_are_not_money_reports(scene, amount):
    arbitrary = {"settled_usd": amount, "committed_usd": "model property", "money_report": "user content"}
    before = deepcopy(arbitrary)
    scene.s.execute = lambda *args, **kwargs: {"status": "ok", "result": arbitrary}
    output = record(invoke(scene, "execute_blender_code", {"code": "return properties"}))
    assert output["status"] == "ok" and output["result"] == before
    assert scene.calls[-1][-1]["result"] == before and arbitrary == before


def test_malformed_budget_like_content_is_preserved_without_decimal_error():
    value = {"settled_usd": "1", "held_usd": "2", "unknown_usd": "3", "unknown_micro_usd": {},
             "committed_usd": "unrelated data", "money_report": "original"}
    assert owner_money(value) == value
