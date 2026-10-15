"""Current-head stage capture, exact photo IDs, calibration reuse and refusal regressions; no Blender."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from glasses3d.agent.temple_lab import cli, package, pose_basis
from glasses3d.agent.temple_lab.lab import Lab, read, sha, write_json_atomic
from glasses3d.g3d_host import compare as C
from glasses3d.tests.agent.test_temple_lab_cli import PrepLab


def source_job(tmp_path, *, posed=True, rigid=False):
    root = tmp_path / "parent"
    (root / "images").mkdir(parents=True)
    (root / "checkpoints").mkdir()
    cp = root / "checkpoints" / "c0007.blend"
    cp.write_bytes(b"CURRENT EDITED SCENE")
    export = root / "current.glb"
    from glasses3d.tests import glbfab
    export.write_bytes(glbfab.build(glbfab.default_parts(),role_where="node"))
    rows, images, paths = [], {}, {}
    for n, name in enumerate(("left view.png", "custom.front.png")):
        p = root / "images" / name
        Image.new("RGB", (40, 32), (30, 80 + n, 100)).save(p)
        matte = root / f"mask{n}.png"
        Image.new("L", (40, 32), 255).save(matte)
        rows.append({"original_name": name, "file": f"images/{name}", "sha256": sha(p),
                     "view_kind": "side" if n == 0 else "front", "side": "left" if n == 0 else None})
        images[name] = {"image": str(p), "image_sha256": sha(p), "shape": [32, 40], "status": "ok",
                        "view_kind": rows[-1]["view_kind"], "colourway": "target",
                        "camera": dict(yaw=90 if n == 0 else 0, pitch=8, roll=0, perspective=.2,
                                       scale=38, center_x=20, center_y=16),
                        "matte_mode": "supplied", "matte_file": matte.name, "matte_file_sha256": sha(matte),
                        "transport": {"source": "explicit provenance retained"}}
        if posed:
            images[name].update(theta_deg={"+1": 3, "-1": -2}, theta_observed={"+1": True, "-1": False},
                                hinge_axis_xz_mm={"+1": [62, -10], "-1": [-62, -10]})
        elif rigid:
            from glasses3d.g3d_host.fit_views import rigid_fit_marker
            images[name]["rigid_fit"] = rigid_fit_marker("a" * 64, sha(p), images[name]["camera"])
            images[name].pop("transport")
        paths[name] = p
    write_json_atomic(root / "cameras.json", {"model": {"path": "older-fit.glb", "sha256": "a" * 64,
        "frame": {"center": [0, -4, -55], "extent": 140}, "front_pivot_mm": [0, -4, 0],
        "front_parts": ["exact front.001"], "skip": ["logo.001"]}, "images": images})
    basis = C.import_cameras_json(root, root / "cameras.json", paths, reason="source calibration")
    head = {"id": "c0007", "file": cp.name, "sha256": sha(cp), "fingerprint": "current-fingerprint"}
    session = SimpleNamespace(head=head, fingerprint=lambda: "current-fingerprint")
    exp = {"status": "exported", "path": str(export), "sha256": sha(export), "fingerprint": head["fingerprint"],
           "checkpoint": head["id"]}
    scene = SimpleNamespace(root=root, job={"name": "Different Product", "images": rows,
                                           "description": "Exact owner description", "specs": {}, "specs_json": "{}"},
                            s=session, seeing=SimpleNamespace(export_head=lambda _call_id: deepcopy(exp)))
    return scene, basis, exp


def test_catalog_has_no_photo_number_convention_and_preserves_explicit_ids():
    rows = [{"original_name": "reference-07.webp", "view_kind": "front"},
            {"original_name": "blue side.jpg", "view_kind": "side", "id": "BlueSide", "basis_ref": "blue side",
             "colourway": "variant", "colour": "blue", "finish": "glossy"}]
    catalog = package.photo_catalog({"images": rows})
    assert [(r["id"], r["basis_ref"], r["role"]) for r in catalog] == [
        ("reference-07", "reference-07", "target"), ("BlueSide", "blue side", "colourway")]
    assert catalog[1]["colour"] == "blue"
    with pytest.raises(ValueError, match="distinct"):
        package.photo_catalog({"images": rows + [{**rows[1], "id": "reference-07"}]})


@pytest.mark.parametrize("posed", [False, True])
def test_prepare_captures_current_head_complete_basis_and_closes_worker(tmp_path, monkeypatch, posed):
    from glasses3d.agent.temple_lab import rigid_pose
    scene, source, export = source_job(tmp_path, posed=posed, rigid=not posed)
    def axes(path, expected):
        assert sha(path) == expected == export["sha256"]
        return {"hinge_axis_xz_mm": {"+1": [62, -10], "-1": [-62, -10]},
                "axis_source": {"glb_sha256": expected, "method": "test-axis-fixture"}}
    monkeypatch.setattr(rigid_pose, "derive_axes", axes)
    files = {p: sha(p) for p in scene.root.rglob("*") if p.is_file()}
    head = deepcopy(scene.s.head)
    root = tmp_path / "stage"
    out = package.prepare(scene, root, "temple-call", lab_class=PrepLab)
    assert PrepLab.made[-1].closed and out["status"] == "prepared" and out["paid_calls"] == 0
    assert out["source"]["checkpoint"] == "c0007" and out["source"]["export_sha256"] == export["sha256"]
    assert (root / "inputs" / "scene.blend").read_bytes() == b"CURRENT EDITED SCENE"
    assert sha(root / "inputs" / "parent.glb") == export["sha256"]
    assert out["photo_ids"] == out["compare_ids"] == out["image_ids"] == ["left view", "custom.front"]
    copied = C.load_basis(root, out["basis"]["id"])
    assert copied.meta["frame"] == source.meta["frame"]
    assert copied.meta["front_parts"] == ["exact front.001"] and copied.meta["skip"] == ["logo.001"]
    assert copied.meta["model"] == source.meta["model"]  # Calibration model need not equal current geometry.
    for rid, rec in source.refs.items():
        for key in ("camera", "roi", "shape", "fit", "matte_sha256", "photo_sha256"):
            assert copied.refs[rid][key] == rec[key]
        assert copied.matte(rid).tolist() == source.matte(rid).tolist()
        if posed:
            assert copied.pose(rid) == source.pose(rid)
        else:
            assert source.pose(rid) is None
            assert copied.pose(rid) == {side: {"theta_deg": 0., "axis_xz_mm": [sign * 62, -10]}
                                        for side, sign in (("+1", 1), ("-1", -1))}
        assert copied.refs[rid]["pose_provenance"]["opening_calibrated"] is posed
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    assert "Exact owner description" in brief and "Prada" not in brief
    assert ("As-authored temple pose" in brief) is not posed
    assert scene.s.head == head and {p: sha(p) for p in files} == files
    assert any("/mattes/" in p for p in out["basis"]["files"])
    # A compare history is mutable evidence, not a frozen calibration input.
    C.append_history(copied, {"compare": "new"})
    assert cli.basis_files(root, copied.id) == out["basis"]["files"]
    lab = object.__new__(Lab)
    lab.manifest = read(root / "inputs" / "lab.json")
    lab._basis = copied
    assert lab.photo_ids() == out["photo_ids"] and lab.pid("left view") == "left view"
    assert lab.rid("custom.front") == "custom.front"


@pytest.mark.parametrize("damage", ["checkpoint", "live", "export", "photo", "matte", "missing_camera", "pose"])
def test_prepare_refuses_changed_or_incomplete_sources(tmp_path, damage):
    scene, basis, export = source_job(tmp_path)
    if damage == "checkpoint":
        (scene.root / "checkpoints" / scene.s.head["file"]).write_bytes(b"changed")
    elif damage == "live":
        scene.s.fingerprint = lambda: "different"
    elif damage == "export":
        export["checkpoint"] = "c0000"
    elif damage == "photo":
        (scene.root / scene.job["images"][0]["file"]).write_bytes(b"changed")
    elif damage == "matte":
        (basis.dir / basis.refs["left view"]["matte"]).write_bytes(b"changed")
    elif damage == "missing_camera":
        basis.meta["refs"].pop("left view")
        write_json_atomic(basis.dir / "basis.json", basis.meta)
    else:
        basis.meta["refs"]["left view"]["theta_deg"] = {"+1": 2}
        write_json_atomic(basis.dir / "basis.json", basis.meta)
    with pytest.raises(ValueError):
        package.prepare(scene, tmp_path / "stage", "bad-call", lab_class=PrepLab)
    assert not (tmp_path / "stage" / "prepared.json").exists()


def test_pose_never_invents_opening_and_rejects_unpaired_or_wrong_sided_axes():
    for rec in ({}, {"theta_deg": {"+1": 0}},
                {"theta_deg": {"+1": 0}, "hinge_axis_xz_mm": {"-1": [-60, 0]}},
                {"theta_deg": {"+1": 0}, "hinge_axis_xz_mm": {"+1": [-60, 0]}}):
        with pytest.raises(ValueError):
            pose_basis.pose_metadata(rec)


def test_prepare_refuses_unknown_missing_pose_before_opening_lab(tmp_path):
    scene, _, _ = source_job(tmp_path, posed=False)
    made = len(PrepLab.made)
    with pytest.raises(ValueError, match="rigid|pose|provenance"):
        package.prepare(scene, tmp_path / "stage", "unknown-pose", lab_class=PrepLab)
    assert len(PrepLab.made) == made and not (tmp_path / "stage" / "prepared.json").exists()


def test_export_refusal_preserves_real_cause_before_creating_lab(tmp_path):
    scene, _, export = source_job(tmp_path)
    head = deepcopy(scene.s.head)
    export.clear()
    export.update(status="refused", code="E_EXPORT_ROLE", message="Hinge: invalid partRole 'hinge'")
    made = len(PrepLab.made)
    with pytest.raises(ValueError, match="parent export refused") as error:
        package.prepare(scene, tmp_path / "stage", "bad-export", lab_class=PrepLab)
    assert "E_EXPORT_ROLE" in str(error.value) and export["message"] in str(error.value)
    assert "checkpoint" not in str(error.value)
    assert len(PrepLab.made) == made and scene.s.head == head
    assert not (tmp_path / "stage").exists()


def test_owner_words_are_bound_but_separate_from_host_instruction_contract(tmp_path):
    scene, _, _ = source_job(tmp_path)
    scene.job.update(name="fade", description="Keep the fade visible; facet_restore is printed on the inside.",
                     specs={"caption": "lean_deg"}, specs_json='{"caption": "lean_deg"}')
    root = tmp_path / "stage"
    package.prepare(scene, root, "owner-words", lab_class=PrepLab)
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    contract = (root / "inputs" / "contract-brief.txt").read_text(encoding="utf-8")
    assert scene.job["description"] in brief and scene.job["specs_json"] in brief
    assert all(word not in contract for word in ("fade", "facet_restore", "lean_deg"))
    assets = {r["path"]: r["sha256"] for r in read(root / "inputs" / "lab.json")["assets"]}
    assert assets["contract-brief.txt"] == sha(root / "inputs" / "contract-brief.txt")
    assert assets["brief.txt"] == sha(root / "inputs" / "brief.txt")


def test_standalone_generic_sources_copy_supplied_target_mattes(tmp_path):
    scene, basis, _ = source_job(tmp_path)
    write_json_atomic(scene.root / "images" / "manifest.json", {"name": scene.job["name"],
        "files": {r["original_name"]: {"sha256": r["sha256"]} for r in scene.job["images"]}})
    root = tmp_path / "standalone"
    cli.prepare(root, source_scene=scene.root / "checkpoints" / scene.s.head["file"],
                scene_sha256=scene.s.head["sha256"], reference=scene.root / "images",
                basis_json=scene.root / "cameras.json", lab_class=PrepLab)
    copied = C.load_basis(root)
    assert set(copied.refs) == {"custom.front", "left view"}
    for rid in copied.refs:
        assert copied.matte(rid).tolist() == basis.matte(rid).tolist()
    assert [p["id"] for p in read(root / "inputs" / "lab.json")["photos"]] == ["custom.front", "left view"]


def test_explicit_photo_rename_keeps_camera_in_brief_and_original_basis_identity(tmp_path):
    scene, original, _ = source_job(tmp_path, posed=True)
    scene.job["images"][0].update(original_name="renamed view.png", id="OwnerProfile", basis_ref="left view")
    root = tmp_path / "stage"
    out = package.prepare(scene, root, "rename", lab_class=PrepLab)
    copied = C.load_basis(root)
    assert out["photo_ids"][0] == "OwnerProfile"
    assert copied.refs["left view"]["image"] == "renamed view.png"
    assert copied.refs["left view"]["source_image"] == "left view.png"
    cameras = read(copied.dir / "cameras.json")["images"]
    assert cameras["renamed view.png"]["camera"] == original.refs["left view"]["camera"]
    assert cameras["renamed view.png"]["source_image"] == "left view.png"
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    assert "- OwnerProfile: side; camera yaw +90.0" in brief
    assert "Calibrated hinge-opening metadata: OwnerProfile, custom.front." in brief
    cameras["custom.front.png"]["transport"] = {"source": "left view.png"}
    photos = read(root / "inputs" / "lab.json")["photos"]
    with_lineage = cli.brief_text("Renamed photos", photos, cameras)
    assert "camera moved from OwnerProfile's by matching" in with_lineage
