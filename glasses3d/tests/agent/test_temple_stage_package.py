"""Current-head stage capture, exact photo IDs, calibration reuse and refusal regressions; no Blender."""
import base64
from copy import deepcopy
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageOps

from glasses3d.agent.temple_lab import cli, package, pose_basis
from glasses3d.agent.temple_lab.lab import Lab, read, sha, write_json_atomic
from glasses3d.g3d_host import compare as C
from glasses3d.tests.agent.test_temple_lab_cli import PrepLab


def source_job(tmp_path, *, posed=True, rigid=False, photo_case=None):
    root = tmp_path / "parent"
    (root / "images").mkdir(parents=True)
    (root / "checkpoints").mkdir()
    cp = root / "checkpoints" / "c0007.blend"
    cp.write_bytes(b"CURRENT EDITED SCENE")
    export = root / "current.glb"
    from glasses3d.tests import glbfab
    export.write_bytes(glbfab.build(glbfab.default_parts(),role_where="node"))
    rows, images, paths = [], {}, {}
    first = "left view" + {"rotated": ".jpg", "gif": ".gif"}.get(photo_case, ".png")
    for n, name in enumerate((first, "custom.front.png")):
        p = root / "images" / name
        pixels = Image.new("RGB", (40, 32), (30, 80 + n, 100))
        pixels.paste("red", (1, 2, 11, 7))
        if n == 0 and photo_case == "rotated":
            exif = Image.Exif()
            exif[274] = 6
            pixels.save(p, exif=exif)
        else:
            pixels.save(p)
        if n == 0 and photo_case == "oversize":
            # Valid PNG with trailing bytes: real 20 MiB boundary, cheap to decode.
            with p.open("ab") as stream:
                stream.write(b"\0" * (20 * 1024 * 1024))
        with Image.open(p) as im:
            w, h = ImageOps.exif_transpose(im).size
        matte = root / f"mask{n}.png"
        Image.new("L", (w, h), 255).save(matte)
        rows.append({"original_name": name, "file": f"images/{name}", "sha256": sha(p),
                     "view_kind": "side" if n == 0 else "front", "side": "left" if n == 0 else None})
        if photo_case is not None:
            from glasses3d.agent import jobs as J
            details, transport = J._image(p.read_bytes(), name)
            rows[-1].update(details)
            rows[-1]["sent_file"] = rows[-1]["file"]
            if transport is not None:
                (root / "reference_images").mkdir(exist_ok=True)
                sent = f"reference_images/reference-{n + 1:03d}{rows[-1].pop('transport_suffix')}"
                (root / sent).write_bytes(transport)
                rows[-1]["sent_file"] = sent
        images[name] = {"image": str(p), "image_sha256": sha(p), "shape": [h, w], "status": "ok",
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
    assert ("Hinge opening is unmeasured or lacks calibration provenance" in brief) is not posed
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


@pytest.mark.parametrize("description,specs_json", [
    ("Café beige \r\nKeep the owner spacing.\r\n", '{\r\n  "caption": "owner wording"\r\n}'),
    ("Café beige \r\nSecond\nThird\rFourth", '{\r\n  "caption": "owner wording",\n  "width": 4\r}'),
])
def test_stage_package_preserves_embedded_owner_newline_bytes(tmp_path, description, specs_json):
    import json
    from glasses3d.agent import approvals as A
    scene, _, _ = source_job(tmp_path)
    scene.job.update(description=description, specs=json.loads(specs_json), specs_json=specs_json)
    root = tmp_path / "stage"
    package.prepare(scene, root, "exact-owner-bytes", lab_class=PrepLab)
    brief = (root / "inputs/brief.txt").read_bytes()
    suffix = "\nOwner description:\n" + description + "\nOwner specifications:\n" + specs_json + "\n"
    assert brief.endswith(suffix.encode("utf-8"))
    manifest = read(root / "inputs/lab.json")
    basis = C.load_basis(root)
    cameras = read(basis.dir / C.CAMERAS_JSON)["images"]
    expected_contract = package.contract_brief(manifest["photos"], cameras, manifest["rig"]).encode("utf-8")
    assert (root / "inputs/contract-brief.txt").read_bytes() == expected_contract
    assets = {r["path"]: r["sha256"] for r in manifest["assets"]}
    assert all(assets[name] == sha(root / "inputs" / name) for name in ("brief.txt", "contract-brief.txt"))
    lab = object.__new__(Lab)
    lab.root, lab.manifest = root, manifest
    binding = A.run_input_binding(root, brief.decode("utf-8"), cli.references(lab))
    assert binding["brief_sha256"] == assets["brief.txt"]


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


@pytest.mark.parametrize("photo_case", ["rotated", "oversize", "gif"])
def test_stage_sends_parent_transport_bytes_keeps_original_basis_and_binds_both(tmp_path, photo_case):
    import numpy as np
    from glasses3d.agent import approvals as A
    from glasses3d.agent.session_inputs import image_identity
    from glasses3d.g3d_host import matte

    scene, original_basis, _ = source_job(tmp_path, photo_case=photo_case)
    source_files = {p: sha(p) for p in scene.root.rglob("*") if p.is_file()}
    root = tmp_path / "stage"
    package.prepare(scene, root, "photo-transport", lab_class=PrepLab)
    lab = object.__new__(Lab)
    lab.root, lab.manifest = root, read(root / "inputs/lab.json")
    lab.verify_inputs()
    references = cli.references(lab)
    photo_rows = lab.manifest["photos"]
    copied_basis = C.load_basis(root)
    for source_row, copied_row, reference in zip(scene.job["images"], photo_rows, references):
        sent = base64.b64decode(reference.image_url.split(",", 1)[1])
        assert sent == (scene.root / source_row["sent_file"]).read_bytes()
        assert image_identity(reference.image_url) == (source_row["sent_sha256"], source_row["sent_mime"], len(sent))
        assert reference.path == copied_row["sent_path"]
        assert sha(root / "inputs" / copied_row["path"]) == source_row["sha256"]
        assert copied_basis.refs[copied_row["basis_ref"]]["photo_sha256"] == source_row["sha256"]
        assert copied_basis.photo(copied_row["basis_ref"]).tolist() == original_basis.photo(copied_row["basis_ref"]).tolist()
        with Image.open(io.BytesIO(sent)) as raw:
            assert raw.getexif().get(274, 1) == 1
            assert raw.size == reference.size_px == tuple(copied_row["size_px"])
            assert np.array_equal(np.asarray(raw.convert("RGB")), matte.load_photo(root / "inputs" / copied_row["path"]))
    if photo_case == "oversize":
        assert (scene.root / scene.job["images"][0]["file"]).stat().st_size > 20 * 1024 * 1024
    elif photo_case == "gif":
        assert references[0].image_url.startswith("data:image/gif;base64,")
    brief = (root / "inputs/brief.txt").read_bytes().decode("utf-8")
    binding = A.run_input_binding(root, brief, references)
    assert len(binding["photos"]) == len(references)  # Includes every bound arm overlay too.
    digests = {r["path"]: r["sha256"] for r in A.input_digests(root)["inputs"]}
    for row in photo_rows:
        assert digests["inputs/" + row["path"]] == row["sha256"]
        assert digests["inputs/" + row["sent_path"]] == row["sent_sha256"]
    with pytest.raises(A.ApprovalRefused, match="every job reference"):
        A.run_input_binding(root, brief, references[1:])
    assert {p: sha(p) for p in source_files} == source_files


def test_legacy_stage_manifest_keeps_original_reference_bytes_and_binding(tmp_path):
    from glasses3d.agent import approvals as A
    scene, _, _ = source_job(tmp_path)
    root = tmp_path / "stage"
    package.prepare(scene, root, "legacy-transport", lab_class=PrepLab)
    manifest = read(root / "inputs/lab.json")
    for row in manifest["photos"]:
        for key in ("sent_path", "sent_mime", "sent_sha256"):
            row.pop(key, None)
    write_json_atomic(root / "inputs/lab.json", manifest)
    lab = object.__new__(Lab)
    lab.root, lab.manifest = root, manifest
    before = A.input_digests(root)
    references = cli.references(lab)
    for row, reference in zip(manifest["photos"], references):
        assert reference.path == row["path"]
        assert base64.b64decode(reference.image_url.split(",", 1)[1]) == (root / "inputs" / row["path"]).read_bytes()
    brief = (root / "inputs/brief.txt").read_bytes().decode("utf-8")
    assert len(A.run_input_binding(root, brief, references)["photos"]) == len(references)
    assert A.input_digests(root) == before


@pytest.mark.parametrize("which", ["path", "sent_path"])
def test_stage_original_and_transport_are_verified_before_approval(tmp_path, which):
    from glasses3d.agent import approvals as A
    scene, _, _ = source_job(tmp_path, photo_case="rotated")
    root = tmp_path / "stage"
    package.prepare(scene, root, "damaged-copy", lab_class=PrepLab)
    lab = object.__new__(Lab)
    lab.root, lab.manifest = root, read(root / "inputs/lab.json")
    references = cli.references(lab)
    row = lab.manifest["photos"][0]
    (root / "inputs" / row[which]).write_bytes(b"changed after preparation")
    with pytest.raises(ValueError, match="input bytes changed"):
        lab.verify_inputs()
    brief = (root / "inputs/brief.txt").read_bytes().decode("utf-8")
    with pytest.raises(A.ApprovalRefused, match="lab original or transport photo changed"):
        A.run_input_binding(root, brief, references)
