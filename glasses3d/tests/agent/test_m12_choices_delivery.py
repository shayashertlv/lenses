"""Applied picks retain immutable publication checks and truthful per-item provenance."""
import copy
from pathlib import Path
import shutil

import pytest

from glasses3d.agent import delivery as D, edits, review
from .test_delivery import make_finished
from .test_m12_choices_apply import delivery_record


FIELDS = ("origin", "applied", "notes", "warnings", "exactness")


def application(root, scene, *, several=False, overlap=False):
    app, _ = delivery_record(several=several, overlap=overlap)
    app.update(export=copy.deepcopy(scene.finish["export"]), tool_test=True)
    image = Path(scene.last_ar["report"]).parent / "front.png"
    app["images"] = [{"path": str(image), "sha256": edits.sha(image)}]
    return app


def publish(root, *, several=False, overlap=False):
    scene, result = make_finished(root)
    app = application(root, scene, several=several, overlap=overlap)
    saved = D.deliver_owner_applied(root, scene.finish["export"], result["exit_checkpoint"], scene.last_ar, app)
    return saved, app, scene, result


def changed_snapshot(root, saved, change):
    """Rehash an independently changed package to reach semantic checks after byte verification."""
    directory = Path(saved["manifest"]).parent
    manifest = edits.read(saved["manifest"])
    files = {name: (directory / name).read_bytes() for name in manifest["files"]}
    report = edits.read(directory / "report.json")
    change(report, files)
    files["report.json"] = D._json_bytes(report)
    manifest["files"] = {name: {"sha256": D._sha(data), "bytes": len(data)} for name, data in files.items()}
    manifest["id"] = D._snapshot_id(manifest)
    target = root / "deliveries" / manifest["id"]
    assert target != directory and not target.exists()
    for name, data in {**files, "manifest.json": D._json_bytes(manifest)}.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return manifest["id"]


@pytest.mark.parametrize("several,overlap", [(False, False), (True, False), (True, True)])
def test_applied_picks_publish_load_and_reopen_with_exact_provenance(tmp_path, several, overlap):
    scene, result = make_finished(tmp_path)
    app = application(tmp_path, scene, several=several, overlap=overlap)
    before = copy.deepcopy(app)
    saved = D.deliver_owner_applied(tmp_path, scene.finish["export"], result["exit_checkpoint"], scene.last_ar, app)
    loaded = D.load_delivery(tmp_path)
    assert app == before
    assert loaded["delivery_id"] == saved["delivery_id"]
    report = loaded["report_data"]
    for key in FIELDS:
        assert loaded[key] == saved[key] == report[key] == app[key]
    assert report["delivery_kind"] == report["run_status"] == "owner_applied"
    assert report["finished"] is True and report["accepted_finish"] is False
    assert report["tool_test"] is True
    assert report["summary"] == ("Owner-selected items composed in item order at $0; interactions are recorded. "
                                 "Visual acceptance remains the owner's decision.")
    assert report["cost"] == {"settled_micro_usd": 0, "requests": 0}
    assert report["validate_spec"]["ok"] and report["print_check"]["ok"]
    assert report["arview"]["status"] == "ok"
    assert report["notes"] == [{"item_id": "I3", "note": "  Leave this one.\r\n"}]
    assert bool(report["warnings"]) is overlap
    assert report["exactness"]["single_pick_glb_equal"] is (None if several else True)
    assert review._delivery_context(loaded)["tool_test"] is True
    packaged = Path(loaded["manifest"]).parent / report["owner_applied"]["path"]
    receipt = edits.read(packaged)
    assert receipt["schema"] == "g3d.owner_applied_choices/1"
    assert "preview_export_sha256" not in receipt and "option" not in receipt
    for image in receipt["images"]:
        assert image["path"].startswith("owner-applied/")
        assert edits.sha(packaged.parent.parent / image["path"]) == image["sha256"]
    # Loading depends on sealed delivery bytes, not mutable source renders or the export file.
    work = (tmp_path / "work").resolve()
    assert work.is_relative_to(tmp_path.resolve())
    shutil.rmtree(work)
    (tmp_path / "finished.glb").unlink()
    reopened = D.load_delivery(tmp_path, saved["delivery_id"])
    assert {key: reopened[key] for key in FIELDS} == {key: app[key] for key in FIELDS}


@pytest.mark.parametrize("damage", ["spec", "print", "checkpoint", "ar", "requests", "exactness",
                                    "visual_acceptance", "export_fingerprint", "unknown_schema"])
def test_applied_picks_keep_existing_publication_and_new_provenance_refusals(tmp_path, damage):
    scene, result = make_finished(tmp_path)
    app = application(tmp_path, scene)
    if damage == "spec":
        scene.finish["export"]["validate_spec"]["ok"] = False
    elif damage == "print":
        scene.finish["export"]["print_check"]["ok"] = False
    elif damage == "checkpoint":
        result["exit_checkpoint"]["verified"] = False
    elif damage == "ar":
        scene.last_ar["export_sha256"] = "0" * 64
    elif damage == "requests":
        app["api_requests"] = 1
    elif damage == "exactness":
        app["exactness"]["ok"] = False
    elif damage == "visual_acceptance":
        app["visual_acceptance"] = True
    elif damage == "export_fingerprint":
        app["export"]["fingerprint"] = "0" * 64
    else:
        app["schema"] = "g3d.owner_applied_choices/2"
    with pytest.raises(D.DeliveryError):
        D.deliver_owner_applied(tmp_path, scene.finish["export"], result["exit_checkpoint"], scene.last_ar, app)
    assert not (tmp_path / "deliveries/current.json").exists()


@pytest.mark.parametrize("field", FIELDS)
def test_applied_picks_rehashed_report_cannot_disagree_with_packaged_application(tmp_path, field):
    saved, _, _, _ = publish(tmp_path)
    delivery_id = changed_snapshot(tmp_path, saved, lambda report, files: report.update({field: "changed"}))
    with pytest.raises(D.DeliveryError, match="provenance changed"):
        D.load_delivery(tmp_path, delivery_id)
    assert D.load_delivery(tmp_path)["id"] == saved["id"]


@pytest.mark.parametrize("damage,match", [
    ("draft", "applied picks require a validated finished export"),
    ("accepted_finish", "validated finished model"),
    ("run_status", "validated finished model"),
    ("missing_finished", "Applied picks do not bind"),
])
def test_applied_picks_cannot_be_draft_or_astra_finish(tmp_path, damage, match):
    saved, _, _, _ = publish(tmp_path)

    def change(report, files):
        if damage == "draft":
            report["finished"] = False
        elif damage == "accepted_finish":
            report["accepted_finish"] = True
        elif damage == "run_status":
            report["run_status"] = "completed"
        else:
            report.pop("finished")

    delivery_id = changed_snapshot(tmp_path, saved, change)
    with pytest.raises(D.DeliveryError, match=match):
        D.load_delivery(tmp_path, delivery_id)


@pytest.mark.parametrize("field", ["seating", "print_check"])
@pytest.mark.parametrize("missing", [True, False])
def test_applied_picks_cannot_use_historical_missing_check_exemption(tmp_path, field, missing):
    saved, _, _, _ = publish(tmp_path)

    def change(report, files):
        if missing:
            report.pop(field)
        else:
            report[field] = None

    delivery_id = changed_snapshot(tmp_path, saved, change)
    with pytest.raises(D.DeliveryError, match="seating and retained-print reports"):
        D.load_delivery(tmp_path, delivery_id)


@pytest.mark.parametrize("name", ["owner-applied/report.json", "owner-applied/0000.png", "report.json", "model.glb"])
def test_applied_picks_packaged_bytes_remain_hash_bound(tmp_path, name):
    saved, _, _, _ = publish(tmp_path)
    path = Path(saved["manifest"]).parent / name
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(D.DeliveryError):
        D.load_delivery(tmp_path)
