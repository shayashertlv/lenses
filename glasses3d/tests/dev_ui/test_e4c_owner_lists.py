"""E4c owner lists use provenance, SHA-bound verdicts and role-derived defaults."""
import copy
import hashlib
import json

import pytest

from glasses3d.dev_ui.owner_lists import job_is_test, marked_test, lens_swatch
from glasses3d.tests.dev_ui.test_e4_api import appearance, _save
from glasses3d.tests.dev_ui.test_api_runs import write_json


def add_version(fixture, name, *, date="2026-10-21T01:00:00Z", origin="run", finished=True):
    root = fixture.core.make_delivery(fixture.config.jobs_root / name, identifier="version-1",
                                     payload=b"glTF-" + name.encode())
    write_json(root / "job.json", {"id": name, "name": "Owner product", "images": []})
    loaded = fixture.core.deliveries[(str(root), "version-1")]
    loaded["report_data"].update(created_utc=date, origin=origin, finished=finished, checkpoint="c0042")
    write_json(root / "deliveries/version-1/report.json", loaded["report_data"])
    key = fixture.service.register_delivery(root, "version-1", job_id=name)
    return root, key


@pytest.mark.parametrize("marker", [
    {"offline_test_mode": True}, {"tool_test": True}, {"imported_test_fixture": True},
    {"test_fixture": True}, {"proof_root": "some-proof"}, {"client": {"kind": "offline"}},
    {"client": {"kind": "scripted"}}, {"scope": "tool_test"}, {"origin": "imported_test_fixture"},
    {"source": {"checkpoint": {"kind": "imported_fixture_glb"}}},
])
def test_structured_test_markers(marker):
    assert marked_test(marker)


def test_names_words_and_false_flags_are_not_markers():
    assert not marked_test({"name": "Synthetic assembly proof", "description": "offline tool_test",
                            "tool_test": False, "offline_test_mode": False, "client": {"kind": "live"}})


@pytest.mark.parametrize("where,marker", [
    ("job", {"imported_test_fixture": True}), ("job", {"tool_test": True}),
    ("run", {"client": {"kind": "offline"}}), ("run", {"client": {"kind": "scripted"}}),
    ("registry", {"offline_test_mode": True}),
])
def test_api_lists_filter_recorded_tests_and_toggle_restores_them(appearance, where, marker):
    root, key = add_version(appearance, "test-copy")
    if where == "job":
        write_json(root / "job.json", {"id": "test-copy", "name": "Owner product", "images": [], **marker})
    elif where == "run":
        write_json(root / "runs/run-1/run-config.json", marker)
    else:
        appearance.service.registry.put("runs", {"kind": "job", "root": str(root), "job_id": "test-copy",
            "run_id": "run-1", "run_key": "fixture-run", "cutoff_usd": "1",
            "created_utc": "2026-10-21T00:00:00Z", **marker}, "fixture-run")
    jobs = appearance.client.get("/api/jobs").json()["jobs"]
    assert "test-copy" not in {row["job_id"] for row in jobs}
    url = f"/api/deliveries/{appearance.key}/appearance"
    assert key not in {row["delivery_key"] for row in appearance.client.get(url).json()["copy_sources"]}
    for query in ("1", "true"):
        assert "test-copy" in {row["job_id"] for row in appearance.client.get(
            "/api/jobs?show_test_jobs=" + query).json()["jobs"]}
        assert key in {row["delivery_key"] for row in appearance.client.get(
            url + "?show_test_jobs=" + query).json()["copy_sources"]}
    assert not appearance.launcher.approvals


def test_filtering_is_independent_of_product_name(appearance):
    write_json(appearance.source / "job.json", {"id": "fixture-job", "images": [],
                                                "name": "Synthetic assembly proof"})
    assert appearance.client.get("/api/jobs").json()["jobs"][0]["name"] == "Synthetic assembly proof"


def test_paid_job_survives_offline_runs_and_tool_test_deliveries(appearance):
    write_json(appearance.source / "runs/paid/run-config.json", {"client": {"kind": "openai"}})
    write_json(appearance.source / "runs/offline/run-config.json", {"client": {"kind": "offline"}})
    saved = _save(appearance)
    owner_record = appearance.service.get("deliveries", saved)
    from pathlib import Path
    write_json(Path(owner_record["root"]) / "deliveries/owner-1/report.json", {"tool_test": True})
    jobs = appearance.client.get("/api/jobs").json()["jobs"]
    assert [row["job_id"] for row in jobs] == ["fixture-job"]
    assert [row["delivery_key"] for row in jobs[0]["deliveries"]] == [appearance.key]
    shown = appearance.client.get(f"/api/deliveries/{appearance.key}/appearance?show_test_jobs=1").json()
    assert {row["delivery_key"] for row in shown["versions"]} == {appearance.key, saved}


def test_proof_roots_do_not_need_a_name_prefix(appearance):
    root = appearance.config.repo_root / "glasses3d/data/m12/proofs/arbitrary-name/job"
    assert job_is_test({"root": str(root)}, appearance.config)


def test_owner_version_inherits_only_its_exact_offline_source(appearance):
    from pathlib import Path
    from glasses3d.dev_ui.owner_lists import delivery_is_test
    root, source_key = add_version(appearance, "mixed-job")
    write_json(root / "runs/real/run-config.json", {"client": {"kind": "production"}})
    write_json(root / "runs/offline/run-config.json", {"client": {"kind": "offline"}})
    source = appearance.service.delivery(source_key)
    report_path = root / "deliveries/version-1/report.json"
    report = json.loads(report_path.read_bytes())
    report["run_id"] = "offline"
    report_sha = write_json(report_path, report)
    manifest_sha = write_json(root / "deliveries/version-1/manifest.json", {
        "id": "version-1", "glb_sha256": source["glb_sha256"], "files": {"report.json": {"sha256": report_sha}}})
    saved = _save(appearance)
    owner = appearance.service.get("deliveries", saved)
    owner_root = Path(owner["root"])
    application = {"owner_look_edit": {"native_source": {"root": str(root), "delivery_id": "version-1",
        "glb_sha256": source["glb_sha256"], "manifest_sha256": manifest_sha}}}
    digest = write_json(owner_root / "deliveries/owner-1/owner-applied/report.json", application)
    owner_report = {"tool_test": False, "owner_applied": {"path": "owner-applied/report.json", "sha256": digest}}
    assert not job_is_test({"root": str(root)}, appearance.config)
    assert delivery_is_test(owner, owner_report, appearance.config)
    # A mismatched source binding cannot hide an unrelated product version.
    application["owner_look_edit"]["native_source"]["glb_sha256"] = "0" * 64
    owner_report["owner_applied"]["sha256"] = write_json(
        owner_root / "deliveries/owner-1/owner-applied/report.json", application)
    assert not delivery_is_test(owner, owner_report, appearance.config)


def test_imported_checkpoint_filters_delivery_outside_proof_roots(appearance):
    root, key = add_version(appearance, "imported-elsewhere")
    path = root / "deliveries/version-1/report.json"
    report = json.loads(path.read_bytes())
    report["source_checkpoint"] = {"kind": "imported_fixture_glb", "id": "fixture-glb"}
    write_json(path, report)
    assert "imported-elsewhere" not in {job["job_id"] for job in appearance.client.get("/api/jobs").json()["jobs"]}


def test_reject_verdict_reads_rejected_and_changes_reads_changes_requested(appearance, tmp_path):
    appearance.config.repo_root = tmp_path
    _, asked = add_version(appearance, "asked", date="2026-10-19T02:00:00Z")
    _, refused = add_version(appearance, "refused", date="2026-10-18T02:00:00Z")
    rows = [{"glb_sha256": appearance.service.delivery(asked)["glb_sha256"], "verdict": "accept", "date": "2026-10-19"},
            {"glb_sha256": appearance.service.delivery(asked)["glb_sha256"], "verdict": "changes", "date": "2026-10-20"},
            {"glb_sha256": appearance.service.delivery(refused)["glb_sha256"], "verdict": "reject", "date": "2026-10-18"}]
    ledger = tmp_path / "glasses3d/data/ledger/owner_verdicts.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    dto = appearance.client.get(f"/api/deliveries/{appearance.key}/appearance").json()
    labels = {row["job_id"]: row["label"] for row in dto["copy_sources"] if row["job_id"] in {"asked", "refused"}}
    assert labels["asked"].endswith("changes requested by you")
    assert labels["refused"].endswith("rejected by you")


def test_full_labels_exact_sha_verdicts_accepted_first_then_newest_and_no_duplicates(appearance, tmp_path):
    appearance.config.repo_root = tmp_path
    _, accepted = add_version(appearance, "accepted", date="2026-10-15T01:30:00Z")
    _, recent = add_version(appearance, "recent", date="2026-10-21T02:00:00Z", finished=False)
    _, rejected = add_version(appearance, "rejected", date="2026-10-20T02:00:00Z")
    rows = [{"glb_sha256": appearance.service.delivery(accepted)["glb_sha256"], "verdict": "accept", "date": "2026-10-16"},
            {"glb_sha256": appearance.service.delivery(rejected)["glb_sha256"], "verdict": "changes", "date": "2026-10-20"},
            {"job_id": "recent", "glb_sha256": "0" * 64, "verdict": "accept", "date": "2026-10-21"}]
    ledger = tmp_path / "glasses3d/data/ledger/owner_verdicts.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    duplicate = appearance.service.get("deliveries", accepted)
    duplicate.pop("key")
    appearance.service.registry.put("deliveries", duplicate)
    dto = appearance.client.get(f"/api/deliveries/{appearance.key}/appearance").json()
    selected = [row for row in dto["copy_sources"] if row["job_id"] in {"accepted", "recent", "rejected"}]
    assert [row["job_id"] for row in selected] == ["accepted", "recent", "rejected"]
    assert selected[0]["label"] == "Owner product — 2026-10-15 01:30 UTC — Astra c0042 — accepted by you"
    assert selected[1]["label"].endswith("not finished")
    assert selected[2]["label"].endswith("changes requested by you")
    assert selected[0]["lens_swatch"]["css"].startswith("#")
    assert len({row["delivery_key"] for row in dto["copy_sources"]}) == len(dto["copy_sources"])


def test_chain_verdict_binds_exact_manifest_and_never_promotes_tool_test(appearance):
    from glasses3d.agent.verdicts import _append
    root = appearance.source
    dto = appearance.service.delivery(appearance.key)
    manifest = root / "deliveries/delivery-1/manifest.json"
    base = {"schema": "g3d.verdict/1", "id": "verdict-" + "1" * 32, "scope": "product", "origin": "live",
        "recorded_at": "2026-10-21T00:00:00Z", "job_id": root.name, "delivery_id": "delivery-1",
        "glb_sha256": dto["glb_sha256"], "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "verdict": "accept", "notes": "owner accepted"}
    ledger = root / "reviews/verdicts.jsonl"
    ledger.parent.mkdir()
    _append(ledger, [], [base])
    url = f"/api/deliveries/{appearance.key}/appearance"
    assert appearance.client.get(url).json()["versions"][0]["owner_verdict"] == "accepted"
    # SHA-equal model with a changed immutable manifest cannot inherit acceptance.
    manifest.write_text('{"different":true}')
    assert appearance.client.get(url).json()["versions"][0]["owner_verdict"] is None
    # Corrupt chain is also never used for acceptance.
    ledger.write_text(ledger.read_text().replace('"product"', '"tool_test"'))
    assert appearance.client.get(url).json()["versions"][0]["owner_verdict"] is None


def test_material_default_dedup_preserves_distinct_same_name_roles(appearance):
    original = appearance.core.read_look_materials
    def rows(model):
        value = original(model)
        value["materials"][0]["name"] = "Shared label"
        value["materials"].append(copy.deepcopy(value["materials"][0]))
        distinct = copy.deepcopy(value["materials"][0])
        distinct.update(material_index=8, role="temple")
        value["materials"].append(distinct)
        return value
    appearance.core.read_look_materials = rows
    dto = appearance.client.get(f"/api/deliveries/{appearance.key}/appearance").json()
    assert dto["default_material_index"] == 2
    assert [(row["material_index"], row["role"]) for row in dto["materials"]] == [(2, "lens"), (0, "frame"), (8, "temple")]


def test_swatch_matches_linear_colour_and_gradient_direction():
    values = {"tint": [0, .5, 1]}
    row = {"material_index": 1, "role": "lens", "values": values}
    assert lens_swatch([row])["css"] == "#00bcff"
    row["gradient"] = {"direction": "centre_to_sides", "blend": .5,
        "colours": [{"colour": [0, 0, 0], "share": 50}, {"colour": [1, 1, 1], "share": 50}]}
    swatch = lens_swatch([row])
    assert swatch["colors"][0] == swatch["colors"][-1] == "#ffffff"
    assert swatch["colors"][10] == "#000000"
