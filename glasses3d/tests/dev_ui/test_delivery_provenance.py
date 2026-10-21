"""Verified delivery provenance survives UI discovery and registry recovery."""
import json
import pytest

from glasses3d.tests.dev_ui.test_api_runs import setup, client, start, publish, write_json


@pytest.mark.parametrize("discovered", [False, True])
def test_verified_applied_provenance_wins_over_registry_hints(setup, discovered):
    _, _, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        root = core.created[0]["root"]
        delivery_id, _ = publish(core, root, started["run_id"])
        path = root / "deliveries" / delivery_id / "report.json"
        report = json.loads(path.read_bytes())
        expected = {"origin": "applied_picks",
                    "applied": [{"item_id": "I1", "option_id": "B", "amount_text": "60% repair"},
                                {"item_id": "I2", "option_id": "A", "amount_text": "25% repair"}],
                    "notes": [{"item_id": "I3", "note": "Keep this item"}],
                    "warnings": [], "exactness": {"ok": True, "mode": "disjoint_items", "overlaps": []}}
        report.update(expected)
        write_json(path, report)
        if discovered:
            key = browser.get("/api/jobs?show_test_jobs=1").json()["jobs"][0]["deliveries"][0]["delivery_key"]
        else:
            key = app.state.service.register_delivery(root, delivery_id, origin="run",
                applied=[{"item_id": "I2", "option_id": "wrong"}], notes=[{"item_id": "I1", "note": "stale"}])
        actual = browser.get(f"/api/deliveries/{key}").json()
        assert {field: actual.get(field) for field in expected} == expected


def test_fallback_side_effects_reach_delivery_page_data(setup):
    _, _, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        root = core.created[0]["root"]
        delivery_id, _ = publish(core, root, started["run_id"])
        path = root / "deliveries" / delivery_id / "report.json"
        report = json.loads(path.read_bytes())
        effects = {"schema": "g3d.full_edit_side_effects/1", "item_id": "I3", "status": "partially_resolved",
                   "informational_only": True, "summary": {"changed_objects": ["Front"],
                       "geometry_outside_item_region": True, "outside_region_objects": ["Front"],
                       "unresolved_objects": ["Temple"]}, "objects": []}
        report.update(origin="full_edit_item", item_id="I3", side_effects=effects)
        write_json(path, report)
        key = app.state.service.register_delivery(root, delivery_id)
        actual = browser.get(f"/api/deliveries/{key}").json()
        assert actual["origin"] == "full_edit_item"
        assert actual["side_effects"] == effects


def test_old_cached_dto_is_rebuilt_from_verified_delivery(setup):
    _, _, core, app = setup
    with client(app) as browser:
        started = start(browser).json()
        root = core.created[0]["root"]
        delivery_id, _ = publish(core, root, started["run_id"])
        key = app.state.service.register_delivery(root, delivery_id)
        browser.get(f"/api/deliveries/{key}")
        row = app.state.service.get("deliveries", key)
        row.pop("dto_revision", None)
        row["dto"]["origin"] = "stale-cache"
        app.state.service.registry.put("deliveries", row, key)
        assert browser.get(f"/api/deliveries/{key}").json()["origin"] == "run"
        assert len(core.loads) == 2
