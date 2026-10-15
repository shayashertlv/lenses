"""Immutable M3 delivery, source receipt checks and stable identities; no renderer, SDK or network calls."""
from __future__ import annotations

import hashlib
import json
import os
import copy
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from PIL import Image
import pytest

from glasses3d.agent import delivery as D
from glasses3d.agent.money import owner_money
from glasses3d.agent.tools import arview
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.g3d_host.seat_check import check_glb
from glasses3d.tests import glbfab
from glasses3d.tests.fixtures import seat_fixtures as SF


_FINISHED_MODELS = {}


def _seated_model(path: Path, variation: int):
    """A real, seated pair. Cache immutable bytes/reports because many receipt tests use the same geometry."""
    if variation not in _FINISHED_MODELS:
        nodes = []
        for side, lens in (("L", SF.base_lens()), ("R", SF.mirrored_lens())):
            nodes += SF.scene_nodes({"lens": lens, "parts": {f"Rim_{side}": SF.ring_frame(lens, 0.8)}},
                                    lens_name=f"Lens_{side}", seat_extras={"frame_type": "acetate"})
        for name in ("Bridge", "Temple_L", "Temple_R"):
            box, _, role = glbfab.default_parts()[name]
            if name == "Temple_L":
                box = (*box[:4], box[4] - variation, box[5])
            nodes.append({"name": name, "mesh": SF.Mesh(*glbfab.box(*box)), "role": role,
                          "material": {"name": "Frame"}})
        SF.write_glb(path, nodes)
        seating = check_glb(path)
        assert seating["ok"], seating
        _FINISHED_MODELS[variation] = (path.read_bytes(), seating)
    payload, seating = _FINISHED_MODELS[variation]
    path.write_bytes(payload)
    seating = copy.deepcopy(seating)
    seating["glb"] = str(path)
    return seating


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_finished(root: Path, *, run_id="offline", variation=0):
    """Reusable $0 fixture: real valid GLB plus small local AR report/image receipts with actual hashes."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    model = root / "finished.glb"
    seating = _seated_model(model, variation)
    sha = digest(model)
    validation = validate_spec(model)
    assert validation["ok"], validation
    capture_dir = root / "work/arview" / run_id
    capture_dir.mkdir(parents=True, exist_ok=True)
    raw = capture_dir / "front.png"
    Image.new("RGB", (24, 18), (48 + variation, 80, 110)).save(raw)
    records = [{"file": raw.name, "sha256": digest(raw), "pose": "front", "variant": "beauty"}]
    capture = {"schema": "arview.capture/1", "status": "captured", "exit_code": 0,
               "input": {"sha256": sha}, "request": {"fixed_scale": True}, "images": records,
               "warnings": [], "errors": []}
    report = capture_dir / "report.json"
    report.write_text(json.dumps(capture), encoding="utf-8")
    sheet = arview.contact_sheet(capture_dir, records)
    image = root / "work/tool_images" / f"{run_id}.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(sheet) as opened:
        opened.convert("RGB").save(image)
    fp = "f" * 64
    export = {"status": "exported", "path": str(model), "sha256": sha, "checkpoint": "c0001",
              "fingerprint": fp, "validation_sha256": sha, "validate_spec": validation, "seat_check": seating,
              "print_check": {"ok": True, "status": "not_required", "glb_sha256": sha}}
    scene = SimpleNamespace(finish={"status": "accepted", "checkpoint": "c0001", "fingerprint": fp,
                                    "summary": "Offline fixture.", "export": export},
                            last_ar={"status": "ok", "exit_code": 0, "checkpoint": "c0001",
                                     "export_sha256": sha, "glb_sha256": sha, "report": str(report),
                                     "report_sha256": digest(report), "sheets": [str(sheet)], "fixed_scale": True,
                                     "appearance_policy": "g3d.ar_appearance/1", "lens_env_intensity": 1,
                                     "images": [{"path": image.relative_to(root).as_posix(), "sha256": digest(image),
                                                 "source_path": sheet.relative_to(root).as_posix(),
                                                 "source_sha256": digest(sheet), "checkpoint": "c0001",
                                                 "view": "AR default poses", "kind": "arview_capture"}]})
    result = {"status": "completed", "run_id": run_id, "exit_checkpoint": {"verified": True, "id": "c0001",
                                                                                  "sha256": "c" * 64},
              "budget": {"settled_usd": "0.000000", "requests": 8}}
    return scene, result


def test_snapshot_and_compatibility_report_contain_all_required_evidence(tmp_path):
    scene, result = make_finished(tmp_path)
    out = D.deliver_finished(tmp_path, scene, result)
    loaded = D.load_delivery(tmp_path)
    assert out["status"] == loaded["status"] == "delivered"
    assert out["model"] == str(tmp_path / "deliverable/model.glb")
    assert out["snapshot_model"] == loaded["model"]
    assert loaded["id"] == out["delivery_id"]
    assert digest(loaded["model"]) == loaded["glb_sha256"] == scene.finish["export"]["sha256"]
    report = json.loads(Path(out["report"]).read_text())
    assert report["validate_spec"]["ok"] and report["seating"]["ok"]
    assert report["print_check"]["ok"] and report["print_check"]["glb_sha256"] == loaded["glb_sha256"]
    assert report["seating"]["glb"] == loaded["model"]
    assert report["checkpoint"] == "c0001" and report["cost"] == owner_money(result["budget"])
    assert report["arview"]["lens_env_intensity"] == 1
    assert report["arview"]["appearance_policy"] == "g3d.ar_appearance/1"
    assert Path(report["arview"]["report"]).is_file()
    for name in report["arview"]["sheets"]:
        assert Path(name).is_file() and str(loaded["id"]) in name
    cap = json.loads(Path(report["arview"]["report"]).read_text())
    for rec in cap["images"]:
        assert digest(Path(report["arview"]["report"]).parent / rec["file"]) == rec["sha256"]
    manifest = json.loads(Path(loaded["manifest"]).read_text())
    assert set(manifest["files"]) == {p.relative_to(Path(loaded["manifest"]).parent).as_posix()
                                      for p in Path(loaded["manifest"]).parent.rglob("*")
                                      if p.is_file() and p.name != "manifest.json"}


def test_next_delivery_and_source_cleanup_cannot_change_previous_identity(tmp_path):
    first_scene, first_result = make_finished(tmp_path, run_id="first")
    first = D.deliver_finished(tmp_path, first_scene, first_result)
    first_bytes = Path(first["snapshot_model"]).read_bytes()
    second_scene, second_result = make_finished(tmp_path, run_id="second", variation=1)
    second = D.deliver_finished(tmp_path, second_scene, second_result)
    assert first["id"] != second["id"] and first["glb_sha256"] != second["glb_sha256"]
    assert D.load_delivery(tmp_path)["id"] == second["id"]
    shutil.rmtree(tmp_path / "work")
    (tmp_path / "finished.glb").unlink()
    assert Path(D.load_delivery(tmp_path, first["id"])["model"]).read_bytes() == first_bytes
    assert D.load_delivery(tmp_path, second["id"])["glb_sha256"] == second["glb_sha256"]


def test_lens_evidence_native_images_and_zero_response_survive_finished_delivery(tmp_path):
    scene, result = make_finished(tmp_path)
    ar = scene.last_ar
    source = Path(ar["report"])
    capture = json.loads(source.read_text())
    for name, kind in [("portrait__a__eyes.png", "portrait"), ("front__checker__beauty.png", "pose"),
                       ("front__dark__beauty.png", "pose")]:
        path = source.parent / name
        Image.new("RGB", (40, 30), "blue").save(path)
        capture["images"].append({"file": name, "kind": kind, "sha256": digest(path)})
    source.write_text(json.dumps(capture))
    diagnostic = {"informational_only": True, "views": [{"view": "front", "status": "measured",
                  "mean_channel_background_response_gain": 0.0}]}
    ar.update(report_sha256=digest(source), lens_background_response=diagnostic)
    published = D.deliver_finished(tmp_path, scene, result)
    loaded = D.load_delivery(tmp_path)
    assert published["status"] == "delivered"
    assert loaded["report_data"]["arview"]["lens_background_response"] == diagnostic
    report = Path(loaded["report_data"]["arview"]["report"])
    for rec in capture["images"]:
        assert digest(report.parent / rec["file"]) == rec["sha256"]


def test_same_finished_run_is_idempotent_without_rewriting_snapshot(tmp_path):
    scene, result = make_finished(tmp_path)
    first = D.deliver_finished(tmp_path, scene, result)
    stamp = Path(first["manifest"]).stat().st_mtime_ns
    again = D.deliver_finished(tmp_path, scene, result)
    assert again["id"] == first["id"] and Path(first["manifest"]).stat().st_mtime_ns == stamp
    assert len([p for p in (tmp_path / "deliveries").iterdir() if p.is_dir()]) == 1


@pytest.mark.parametrize("corrupt", [False, True])
def test_worn_view_links_are_bound_to_immutable_capture_images(tmp_path, corrupt):
    scene, result = make_finished(tmp_path)
    ar = scene.last_ar
    source = Path(ar["report"])
    capture = json.loads(source.read_text())
    crop = source.parent / "yaw_p50__worn.png"
    Image.new("RGB", (20, 12), "tan").save(crop)
    view = {"file": crop.name, "kind": "worn_crop", "sha256": digest(crop), "pose": "yaw_p50",
            "variant": "beauty", "source": "front.png", "head": "same occlusion meshes", "hair": False}
    capture["images"].append(view)
    source.write_text(json.dumps(capture))
    ar.update(report_sha256=digest(source), worn_views=[{**view, "path": str(crop)}])
    if corrupt:
        ar["worn_views"][0]["sha256"] = "0" * 64
        with pytest.raises(D.DeliveryError, match="worn view identity"):
            D.deliver_finished(tmp_path, scene, result)
        return
    published = D.deliver_finished(tmp_path, scene, result)
    directory = Path(published["manifest"]).parent
    stored = json.loads((directory / "report.json").read_text())["arview"]["worn_views"][0]
    assert stored["path"] == "arview/" + crop.name and stored["source_path"] == "arview/front.png"
    work = (tmp_path / "work").resolve()
    assert work.is_relative_to(tmp_path.resolve())
    shutil.rmtree(work)
    loaded = D.load_delivery(tmp_path)["report_data"]["arview"]["worn_views"][0]
    assert Path(loaded["path"]).is_relative_to(directory) and digest(loaded["path"]) == view["sha256"]
    assert Path(loaded["source_path"]).is_relative_to(directory)
    assert digest(loaded["source_path"]) == loaded["source_sha256"]


def test_pre_m4_immutable_delivery_without_seating_remains_readable_and_unchanged(tmp_path):
    scene, result = make_finished(tmp_path)
    manifest, files = D._prepare(tmp_path, scene, result)
    report = json.loads(files["report.json"])
    report["seating"] = None  # a historical M3 report, before all-frame seating became a publication requirement
    report.pop("print_check")
    files["report.json"] = D._json_bytes(report)
    manifest["files"]["report.json"] = {"sha256": hashlib.sha256(files["report.json"]).hexdigest(),
                                         "bytes": len(files["report.json"])}
    manifest["id"] = D._snapshot_id(manifest)
    directory = tmp_path / "deliveries" / manifest["id"]
    for name, data in {**files, "manifest.json": D._json_bytes(manifest)}.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    before = {p.relative_to(directory).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in directory.rglob("*") if p.is_file()}
    loaded = D.load_delivery(tmp_path, manifest["id"])
    assert loaded["report_data"]["seating"] is None
    assert {p.relative_to(directory).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in directory.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("failure", ["not_accepted", "unverified", "wrong_checkpoint", "export_checkpoint",
                                     "fingerprint", "invalid_spec", "validation_sha", "invalid_seating", "no_seating", "model_bytes",
                                     "stale_print", "no_print_report", "wrong_print_sha",
                                     "ar_status", "ar_sha", "ar_report_bytes", "capture_bytes", "sheet_bytes",
                                     "tool_image_bytes", "missing_sheet_receipt", "empty_capture", "report_glb",
                                     "report_scale", "report_exit"])
def test_delivery_refuses_incomplete_or_corrupt_source_evidence(tmp_path, failure):
    scene, result = make_finished(tmp_path)
    exp, ar = scene.finish["export"], scene.last_ar
    if failure == "not_accepted":
        scene.finish["status"] = "refused"
    elif failure == "unverified":
        result["exit_checkpoint"]["verified"] = False
    elif failure == "wrong_checkpoint":
        result["exit_checkpoint"]["id"] = "c0002"
    elif failure == "export_checkpoint":
        exp["checkpoint"] = "c0002"
    elif failure == "fingerprint":
        exp["fingerprint"] = "0" * 64
    elif failure == "invalid_spec":
        exp["validate_spec"]["ok"] = False
    elif failure == "validation_sha":
        exp["validation_sha256"] = "0" * 64
    elif failure == "invalid_seating":
        exp["seat_check"] = {"ok": "false"}
    elif failure == "no_seating":
        exp["seat_check"] = None
    elif failure == "stale_print":
        exp["print_check"]["ok"] = False
    elif failure == "no_print_report":
        exp.pop("print_check")
    elif failure == "wrong_print_sha":
        exp["print_check"]["glb_sha256"] = "0" * 64
    elif failure == "model_bytes":
        Path(exp["path"]).write_bytes(b"changed")
    elif failure == "ar_status":
        ar["status"] = "corrupt"
    elif failure == "ar_sha":
        ar["export_sha256"] = "0" * 64
    elif failure == "ar_report_bytes":
        Path(ar["report"]).write_bytes(b"changed")
    elif failure == "capture_bytes":
        (Path(ar["report"]).parent / "front.png").write_bytes(b"changed")
    elif failure == "sheet_bytes":
        Path(ar["sheets"][0]).write_bytes(b"changed")
    elif failure == "tool_image_bytes":
        (tmp_path / ar["images"][0]["path"]).write_bytes(b"changed")
    elif failure == "missing_sheet_receipt":
        ar["images"] = []
    else:
        path = Path(ar["report"])
        doc = json.loads(path.read_text())
        if failure == "empty_capture":
            doc["images"] = []
        elif failure == "report_glb":
            doc["input"]["sha256"] = "0" * 64
        elif failure == "report_scale":
            doc["request"]["fixed_scale"] = False
        elif failure == "report_exit":
            doc["exit_code"] = 3
        path.write_text(json.dumps(doc))
        ar["report_sha256"] = digest(path)
    with pytest.raises(D.DeliveryError):
        D.deliver_finished(tmp_path, scene, result)
    assert not (tmp_path / "deliveries").exists()
    assert not (tmp_path / "deliverable").exists()


@pytest.mark.parametrize("target", ["model.glb", "report.json", "manifest.json", "arview/report.json",
                                    "arview/front.png", "contact-sheets/01.jpg", "tool-images/01.png"])
def test_load_and_redelivery_refuse_tampered_snapshot(tmp_path, target):
    scene, result = make_finished(tmp_path)
    out = D.deliver_finished(tmp_path, scene, result)
    snapshot = Path(out["manifest"]).parent
    (snapshot / target).write_bytes(b"changed")
    for delivery_id in (None, out["id"]):
        with pytest.raises(D.DeliveryError):
            D.load_delivery(tmp_path, delivery_id)
    with pytest.raises(D.DeliveryError):
        D.deliver_finished(tmp_path, scene, result)
    assert (snapshot / target).read_bytes() == b"changed"  # no overwrite that hides a tampered past delivery


@pytest.mark.parametrize("defect", ["missing", "extra", "rewritten_manifest", "reformatted_manifest",
                                    "pointer_hash", "pointer_sha"])
def test_manifest_and_pointer_integrity_are_checked(tmp_path, defect):
    scene, result = make_finished(tmp_path)
    out = D.deliver_finished(tmp_path, scene, result)
    snapshot = Path(out["manifest"]).parent
    if defect == "missing":
        (snapshot / "arview/front.png").unlink()
    elif defect == "extra":
        (snapshot / "unlisted.txt").write_text("unexpected")
    elif defect == "rewritten_manifest":
        # Rehashing a changed file cannot hide tampering: the directory id binds the entire manifest payload.
        (snapshot / "model.glb").write_bytes(b"other model")
        path = snapshot / "manifest.json"
        data = json.loads(path.read_text())
        data["files"]["model.glb"] = {"sha256": digest(snapshot / "model.glb"), "bytes": 11}
        path.write_text(json.dumps(data))
    elif defect == "reformatted_manifest":
        path = snapshot / "manifest.json"
        path.write_text(json.dumps(json.loads(path.read_text())))
    else:
        path = tmp_path / "deliveries/current.json"
        data = json.loads(path.read_text())
        data["manifest_sha256" if defect == "pointer_hash" else "glb_sha256"] = "0" * 64
        path.write_text(json.dumps(data))
    with pytest.raises(D.DeliveryError):
        D.load_delivery(tmp_path)
    if defect in ("missing", "extra", "rewritten_manifest", "reformatted_manifest"):
        with pytest.raises(D.DeliveryError):
            D.load_delivery(tmp_path, out["id"])


def test_current_pointer_is_unchanged_after_failure_to_publish_compatibility(tmp_path, monkeypatch):
    first_scene, first_result = make_finished(tmp_path, run_id="first")
    first = D.deliver_finished(tmp_path, first_scene, first_result)
    scene, result = make_finished(tmp_path, run_id="second", variation=1)
    def fail(*_):
        raise OSError("simulated compatibility write failure")
    monkeypatch.setattr(D, "_compatibility", fail)
    with pytest.raises(OSError):
        D.deliver_finished(tmp_path, scene, result)
    assert D.load_delivery(tmp_path)["id"] == first["id"]


@pytest.mark.parametrize("delivery_id", ["../outside", "C:\\elsewhere", "missing", "", None])
def test_missing_or_invalid_delivery_refused(tmp_path, delivery_id):
    with pytest.raises(D.DeliveryError):
        D.load_delivery(tmp_path, delivery_id)


def test_capture_path_traversal_cannot_enter_snapshot(tmp_path):
    scene, result = make_finished(tmp_path)
    ar = scene.last_ar
    path = Path(ar["report"])
    doc = json.loads(path.read_text())
    doc["images"][0]["file"] = "../front.png"
    path.write_text(json.dumps(doc))
    ar["report_sha256"] = digest(path)
    with pytest.raises(D.DeliveryError, match="confined relative"):
        D.deliver_finished(tmp_path, scene, result)


def test_unfinished_run_has_no_delivery_side_effects(tmp_path):
    scene, result = make_finished(tmp_path)
    result["status"] = "failed"
    assert D.deliver_finished(tmp_path, scene, result)["status"] == "not_finished"
    assert not (tmp_path / "deliveries").exists()


def test_mutable_copy_cannot_follow_a_link_into_an_immutable_snapshot(tmp_path):
    scene, result = make_finished(tmp_path)
    first = D.deliver_finished(tmp_path, scene, result)
    saved = Path(first["snapshot_model"]).read_bytes()
    alias = tmp_path / "deliverable/model.glb"
    alias.unlink()
    try:
        alias.symlink_to(first["snapshot_model"])
    except OSError as exc:
        if os.name != "nt":
            raise
        # Windows junctions need no developer-mode symlink privilege. Redirect the whole compatibility folder
        # instead; every move target is a literal child of this test's temporary root.
        compatibility = (tmp_path / "deliverable").resolve()
        backup = (tmp_path / "compatibility-backup").resolve()
        assert compatibility.parent == backup.parent == tmp_path.resolve()
        compatibility.rename(backup)
        made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(compatibility),
                               str(Path(first["snapshot_model"]).parent)], capture_output=True)
        assert made.returncode == 0, (exc, made.stdout, made.stderr)
    scene, result = make_finished(tmp_path, run_id="second", variation=1)
    with pytest.raises(D.DeliveryError, match="filesystem link"):
        D.deliver_finished(tmp_path, scene, result)
    assert Path(first["snapshot_model"]).read_bytes() == saved
    assert D.load_delivery(tmp_path)["id"] == first["id"]
