"""A rigid fit's zero delta is an exact identity, with explicit lineage and no measured-opening claim."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from glasses3d.agent.temple_lab import rigid_pose as RP
from glasses3d.g3d_host import cameras, fit_views as FV, fitmesh, pose as PO, raster
from glasses3d.g3d_host.camera import Camera, project_mm
from glasses3d.tests import glbfab


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def captured(tmp_path, *, knuckles=True):
    parts = glbfab.default_parts()
    if knuckles:
        parts.update({"right hinge": ((66, 68, 2, 5, -3, -1), "frame", "temple"),
                      "left hinge": ((-68, -66, 2, 5, -3, -1), "frame", "temple")})
    path = tmp_path / "current.glb"
    path.write_bytes(glbfab.build(parts, role_where="node"))
    return path, digest(path)


def source(tmp_path, *, marker=True):
    camera = Camera(31., 12., 1., .1, 105., 80., 60.).to_dict()
    model = {"sha256": "a" * 64, "frame": {"center": [0., 0., -70.], "extent": 140.},
             "front_pivot_mm": [0., 0., 0.]}
    raw = {"status": "ok", "refusal": None, "camera": camera, "image_sha256": "b" * 64}
    if marker:
        raw["rigid_fit"] = FV.rigid_fit_marker(model["sha256"], raw["image_sha256"], camera)
    doc = {"model": model, "images": {"angle photo.png": raw}}
    ref = {"image": "angle photo.png", "photo_sha256": raw["image_sha256"], "camera": deepcopy(camera)}
    basis = SimpleNamespace(dir=tmp_path, refs={"angle photo": ref}, meta={"model": deepcopy(model),
        "frame": deepcopy(model["frame"]), "front_pivot_mm": model["front_pivot_mm"][:]})
    basis.camera = lambda rid: Camera.from_dict(basis.refs[rid]["camera"])
    save_source(basis, doc)
    return basis, doc


def save_source(basis, doc):
    path = basis.dir / "cameras.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    basis.meta["cameras_json_sha256"] = digest(path)


def test_identity_materialization_keeps_real_opening_unknown_and_binds_two_models(tmp_path):
    basis, doc = source(tmp_path)
    glb, sha = captured(tmp_path)
    before = deepcopy(basis.refs), deepcopy(basis.meta), (tmp_path / "cameras.json").read_bytes()
    patch = RP.materialize(basis, glb, sha)["angle photo"]
    assert patch["theta_deg"] == {"+1": 0., "-1": 0.}
    assert patch["theta_observed"] == {"+1": False, "-1": False}
    for side in (1, -1):
        np.testing.assert_allclose(patch["hinge_axis_xz_mm"][f"{side:+d}"], [side * 67., -2.], atol=1e-5)
    lineage = patch["pose_provenance"]
    assert lineage["mode"] == "as_authored" and lineage["opening_calibrated"] is False
    assert lineage["rigid_fit"] == doc["images"]["angle photo.png"]["rigid_fit"]
    assert lineage["rigid_fit"]["source_model_sha256"] == "a" * 64 != sha
    assert lineage["axis_source"]["glb_sha256"] == sha
    assert (basis.refs, basis.meta, (tmp_path / "cameras.json").read_bytes()) == before


@pytest.mark.parametrize("yaw,pitch,perspective", [(0., 0., 0.), (33., 15., .15), (90., 5., .1), (-72., 18., .2)])
def test_generated_zero_delta_is_exact_geometry_raster_and_projection_identity(tmp_path, yaw, pitch, perspective):
    basis, _ = source(tmp_path)
    glb, sha = captured(tmp_path)
    patch = RP.materialize(basis, glb, sha)["angle photo"]
    pose = {k: {"theta_deg": v, "axis_xz_mm": patch["hinge_axis_xz_mm"][k]}
            for k, v in patch["theta_deg"].items()}
    mesh = fitmesh.load_fit_mesh(glb)
    posed = PO.posed(mesh, pose, PO.part_roles(glb))
    assert posed.V.tobytes() == mesh.V.tobytes() and posed.F is mesh.F and posed.frame == mesh.frame
    cam = raster.view_camera(mesh.frame, yaw, pitch, .7, (120, 160), perspective=perspective)
    assert project_mm(mesh.V, cam, mesh.frame).tobytes() == project_mm(posed.V, cam, posed.frame).tobytes()
    original = raster.RasterScene(mesh.V, mesh.F, mesh.frame).render(
        cam, (120, 160), 1, want_bary=True, want_normal=True, want_points=True)
    identity = raster.RasterScene(posed.V, posed.F, posed.frame).render(
        cam, (120, 160), 1, want_bary=True, want_normal=True, want_points=True)
    assert set(original) == set(identity)
    for key in original:
        if key == "grid":
            for actual, expected in zip(original[key], identity[key]):
                np.testing.assert_array_equal(actual, expected)
        else:
            np.testing.assert_array_equal(original[key], identity[key])


@pytest.mark.parametrize("damage", ["missing", "schema", "model", "photo", "camera", "delta", "boolean",
                                    "raw_camera", "raw_photo", "frame", "pivot", "refused", "pose", "sha"])
def test_unknown_or_tampered_rigid_provenance_cannot_silently_become_zero(tmp_path, monkeypatch, damage):
    basis, doc = source(tmp_path)
    raw = doc["images"]["angle photo.png"]
    if damage == "missing":
        raw.pop("rigid_fit")
    elif damage in ("schema", "model", "photo", "camera"):
        key = {"schema": "schema", "model": "source_model_sha256", "photo": "photo_sha256",
               "camera": "camera_sha256"}[damage]
        raw["rigid_fit"][key] = "bad"
    elif damage in ("delta", "boolean"):
        raw["rigid_fit"]["temple_delta_deg"]["+1"] = 1. if damage == "delta" else False
    elif damage == "raw_camera":
        raw["camera"]["yaw"] += 1.
    elif damage == "raw_photo":
        raw["image_sha256"] = "c" * 64
    elif damage == "frame":
        doc["model"]["frame"]["extent"] += 1.
    elif damage == "pivot":
        doc["model"]["front_pivot_mm"][2] += 1.
    elif damage == "refused":
        raw["status"] = "refused"
    elif damage == "pose":
        raw["theta_deg"] = {"+1": 3.}
    save_source(basis, doc)
    if damage == "sha":
        (tmp_path / "cameras.json").write_text("{}")
    monkeypatch.setattr(RP, "derive_axes", lambda *_: pytest.fail("unverified pose reached geometry"))
    with pytest.raises(ValueError):
        RP.materialize(basis, tmp_path / "absent.glb", "d" * 64)


def test_existing_explicit_pose_is_not_reestimated_or_materialized(tmp_path):
    basis, _ = source(tmp_path, marker=False)
    basis.refs["angle photo"].update(theta_deg={"+1": 3., "-1": -2.},
        hinge_axis_xz_mm={"+1": [67., -2.], "-1": [-67., -2.]})
    before = deepcopy(basis.refs)
    (tmp_path / "cameras.json").unlink()
    assert RP.materialize(basis, tmp_path / "absent.glb", "bad") == {}
    assert basis.refs == before
    basis.refs["angle photo"]["hinge_axis_xz_mm"].pop("-1")
    with pytest.raises(ValueError, match="incomplete"):
        RP.materialize(basis, None, None)


def test_geometry_requires_hash_and_real_knuckles(tmp_path):
    glb, sha = captured(tmp_path, knuckles=False)
    with pytest.raises(ValueError, match="knuckle") as error:
        RP.derive_axes(glb, sha)
    message = str(error.value)
    assert "temple-role" in message and "X and Z" in message and "strictly below 3.2 mm" in message
    assert "within 4 mm" in message and "tool recognition heuristic" in message
    assert "do not shrink real hardware" in message
    with pytest.raises(ValueError, match="SHA-256"):
        RP.derive_axes(glb, "d" * 64)


def test_actual_rigid_fitter_emits_marker_only_for_successful_fit(tmp_path, monkeypatch):
    glb, sha = captured(tmp_path)
    mesh = fitmesh.load_fit_mesh(glb)
    cam = raster.view_camera(mesh.frame, 0., 8., .7, (120, 160))
    foreground = mesh.scene().render(cam, (120, 160), 1)["mask"]
    photo = tmp_path / "photo.png"
    Image.fromarray(np.where(foreground[..., None], [40, 50, 60], [240, 240, 240]).astype(np.uint8)).save(photo)
    monkeypatch.setattr(FV.M, "matte", lambda _, **kwargs: foreground)
    metrics = cameras.native_metrics(mesh.scene(), cam, foreground, cameras.matte_roi(foreground))
    best = {"camera": cam, "view": "front", "metrics": metrics, "sign": {}, "loss": 0., "common_loss": 0.,
            "refine": {"origin": "test"}, "fit": SimpleNamespace(evals=1)}
    monkeypatch.setattr(FV.cameras, "fit_best", lambda *_a, **_kw: (best, [best]))
    result = FV.fit_image(glb, photo, "front")
    assert result["rigid_fit"] == FV.rigid_fit_marker(sha, digest(photo), result["camera"])
    monkeypatch.setattr(FV, "sign_check", lambda *_: "rejected label")
    refused = FV.fit_image(glb, photo, "front")
    assert refused["status"] == "refused" and "rigid_fit" not in refused
