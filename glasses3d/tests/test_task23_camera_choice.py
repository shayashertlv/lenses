"""Frozen-candidate choices retain exact known poses and the existing camera/occlusion checks."""
from copy import deepcopy
from dataclasses import replace
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.seeing import Seeing
from glasses3d.agent.tools.camera_choice import choose, tool_for
from glasses3d.g3d_host import camera_choice, cameras, compare as C, fit_views, fitmesh, pose, raster
from glasses3d.tests import glbfab


def prepared(tmp_path, *, view="right", yaw=-72., pitch=11., opening=False, extra=False):
    root = tmp_path / "job"
    root.mkdir()
    path = root / "model.glb"
    path.write_bytes(glbfab.build(role_where="node"))
    mesh = fitmesh.load_fit_mesh(path)
    shape = (180, 260)
    true = raster.view_camera(mesh.frame, yaw, pitch, 1., shape, perspective=0.)
    old = replace(true, yaw=-108. if yaw < 0 else 108., pitch=-pitch)
    axes = {"+1": [68., -2.], "-1": [-68., -2.]} if opening else {}
    theta = {k: 0. for k in axes}
    posed = pose.posed(mesh, {k: {"theta_deg": 0., "axis_xz_mm": v} for k, v in axes.items()}, pose.part_roles(path))
    fg = posed.scene().render(true, shape)["mask"]
    photo = root / "side.png"
    Image.fromarray(np.where(fg[..., None], 30, np.full((*shape, 3), 240)).astype("uint8")).save(photo)
    bdir = C.basis_root(root) / "basis-01"
    bdir.mkdir(parents=True)
    Image.fromarray(fg.astype("uint8") * 255).save(bdir / "provided.png")
    model = fit_views._model_record(path, mesh, mesh.front_pivot(), (), None)
    candidate = {"id": "known", "view": view, "camera": true.to_dict(), "theta_deg": theta,
                 "loss_common_level": 0., "label_refusal": None, "overlay": None}
    row = {"image": str(photo), "image_sha256": C.sha256_file(photo), "view_kind": view, "view": view,
           "shape": list(shape), "roi": list(cameras.matte_roi(fg)), "camera": old.to_dict(),
           "status": "ok", "refusal": None, "iou": 1., "candidates": [view],
           "matte_mode": "supplied", "matte_file": "provided.png",
           "matte_file_sha256": C.sha256_file(bdir / "provided.png"),
           "camera_ambiguity": {"schema": "g3d.camera_orientation/1", "status": "unresolved",
                                "selected": "old", "candidates": [candidate]},
           "opening_fit": {"status": "unavailable", "unavailable": {"hinges": "not recognized"}},
           "flags": ["camera_orientation_unresolved"]}
    if opening:
        row.update(theta_deg=theta, theta_observed={k: True for k in axes}, hinge_axis_xz_mm=axes)
        row["pose_provenance"] = fit_views.articulated_fit_marker(model["sha256"], row["image_sha256"],
            row["camera"], theta, axes, row["theta_observed"], "synthetic", theta)
    rows, photos = {"side.png": row}, {"side.png": photo}
    if extra:
        second = root / "other.png"
        second.write_bytes(photo.read_bytes())
        rows["other.png"] = {**deepcopy(row), "image": str(second)}
        photos["other.png"] = second
    C.write_json(bdir / C.CAMERAS_JSON, {"model": model, "images": rows})
    basis = C.freeze_basis(root, bdir, photos, reason="known pose", call_id="before", source="test")
    export = {"status": "exported", "path": str(path), "sha256": model["sha256"],
              "checkpoint": "c0001", "fingerprint": "synthetic"}
    calls = []
    scene = SimpleNamespace(root=root, s=SimpleNamespace(record_call=lambda *args: calls.append(args)),
                            seeing=SimpleNamespace(export_head=lambda _: export, _fit_ref_summary=Seeing._fit_ref_summary))
    return scene, basis, mesh, row, true, fg, calls


@pytest.mark.parametrize("view,yaw,pitch", [("right", -72., 11.), ("left", 72., 11.), ("top", 0., 65.)])
def test_exact_known_candidate_freezes_new_basis_without_optimizer(tmp_path, monkeypatch, view, yaw, pitch):
    scene, old, mesh, row, true, fg, calls = prepared(tmp_path, view=view, yaw=yaw, pitch=pitch)
    snapshot = {p.relative_to(old.dir).as_posix(): p.read_bytes() for p in old.dir.rglob("*") if p.is_file()}
    monkeypatch.setattr(cameras, "fit_best", lambda *a, **kw: pytest.fail("orientation choice must not refit"))
    result = choose(scene, "The visible front face supports this saved family", old.id,
                    [{"photo": "side", "candidate": "known"}], "choice")
    assert result["status"] == "ok", result
    assert result["basis"] == "basis-02" and result["refit"] is False
    new = C.load_basis(scene.root)
    assert new.camera("side").to_dict() == true.to_dict()
    np.testing.assert_array_equal(new.matte("side"), fg)
    assert new.refs["side"]["fit"]["iou"] == 1.
    assert result["refs"]["side"]["camera_ambiguity"]["status"] == "chosen"
    assert result["refs"]["side"]["photographic_arm_opening"]["status"] == "unavailable"
    assert snapshot == {p.relative_to(old.dir).as_posix(): p.read_bytes() for p in old.dir.rglob("*") if p.is_file()}
    assert len(calls) == 1


@pytest.mark.parametrize("change,message", [
    ({"yaw": 72.}, "view-label"), ({"center_x": 10000.}, "camera gate"),
    ({"scale": -1.}, "positive scale"), ({"perspective": -1.}, "nonnegative"),
    ({"perspective": 100.}, "near plane"), ({"yaw": float("nan")}, "finite"),
])
def test_bad_known_candidates_refuse_without_relaxing_checks(tmp_path, change, message):
    scene, basis, mesh, row, true, fg, calls = prepared(tmp_path)
    row["camera_ambiguity"]["candidates"][0]["camera"].update(change)
    with pytest.raises(ValueError, match=message):
        camera_choice.select(row, "known", mesh, pose.part_roles(scene.root / "model.glb"), fg,
                             mesh.front_pivot(), "model", "photo")


def test_top_negative_pitch_and_off_axis_wrong_eye_refuse(tmp_path):
    scene, basis, mesh, row, true, fg, calls = prepared(tmp_path, view="top", yaw=0., pitch=65.)
    candidate = row["camera_ambiguity"]["candidates"][0]
    candidate["camera"]["pitch"] = -65.
    with pytest.raises(ValueError, match="view-label"):
        camera_choice.select(row, "known", mesh, {}, fg, mesh.front_pivot(), "model", "photo")
    candidate["camera"] = replace(true, pitch=10., perspective=.1, eye_y=-10.).to_dict()
    with pytest.raises(ValueError, match="view-label"):
        camera_choice.select(row, "known", mesh, {}, fg, mesh.front_pivot(), "model", "photo")


@pytest.mark.parametrize("problem", ["unknown", "repeat", "stale_model", "changed_photo", "changed_basis",
                                     "unknown_basis", "owner_edit"])
def test_refusal_leaves_current_basis_intact(tmp_path, problem):
    scene, old, mesh, row, true, fg, calls = prepared(tmp_path)
    choices = [{"photo": "side", "candidate": "known"}]
    if problem == "unknown":
        choices[0]["candidate"] = "absent"
    elif problem == "repeat":
        choices *= 2
    elif problem == "stale_model":
        scene.seeing.export_head("t")["sha256"] = "another model"
    elif problem == "changed_photo":
        (scene.root / "side.png").write_bytes(b"different photo")
    elif problem == "changed_basis":
        (old.dir / C.CAMERAS_JSON).write_text("{}", encoding="utf-8")
    elif problem == "owner_edit":
        scene.owner_edit = {"phase": "understand"}
    result = choose(scene, "Inspect visible occlusions", "basis-99" if problem == "unknown_basis" else old.id,
                    choices, "choice")
    assert result["status"] == "refused" and result["code"] == "E_CAMERA_CHOICE"
    assert C.list_bases(scene.root) == [old.id]
    assert len(calls) == 1


def test_batch_refusal_atomic_and_unselected_photo_retained(tmp_path):
    scene, old, mesh, row, true, fg, calls = prepared(tmp_path, extra=True)
    result = choose(scene, "batch with absent candidate", old.id,
                    [{"photo": "side", "candidate": "known"}, {"photo": "other", "candidate": "absent"}], "bad")
    assert result["status"] == "refused" and C.list_bases(scene.root) == [old.id]
    result = choose(scene, "select side only", old.id, [{"photo": "side", "candidate": "known"}], "good")
    assert result["status"] == "ok", result
    new = C.load_basis(scene.root)
    assert new.refs["other"]["camera"] == old.refs["other"]["camera"]
    assert new.refs["other"]["fit"] == old.refs["other"]["fit"]
    assert new.refs["other"]["matte_sha256"] == old.refs["other"]["matte_sha256"]
    assert set(new.refs) == set(old.refs)


def test_opening_not_relabelled_observed_after_new_camera_and_full_scene_occlusion(tmp_path):
    scene, old, mesh, row, true, fg, calls = prepared(tmp_path, opening=True)
    result = choose(scene, "choose with independent occlusion evidence", old.id,
                    [{"photo": "side", "candidate": "known"}], "choice")
    assert result["status"] == "ok", result
    new = C.load_basis(scene.root)
    assert new.refs["side"]["theta_deg"] == row["theta_deg"]
    assert new.refs["side"]["theta_observed"] == {"+1": False, "-1": False}
    diagnostic = new.refs["side"]["fit"]["camera_ambiguity"]["photographic_arm_opening"]
    assert diagnostic["status"] == "not_fully_observed"
    rendered = mesh.scene().render(true, fg.shape)
    tags = pose.side_of_faces(mesh, pose.part_roles(scene.root / "model.glb"))
    for side in (1, -1):
        ids = rendered["face_id"]
        visible = (ids >= 0) & (tags[np.maximum(ids, 0)] == side)
        assert diagnostic["visibility"][f"{side:+d}"]["visible_model_pixels"] == int(visible.sum())


def test_physical_arm_pose_cannot_be_lost_or_moved_through_front(tmp_path):
    scene, old, mesh, row, true, fg, calls = prepared(tmp_path, opening=True)
    candidate = row["camera_ambiguity"]["candidates"][0]
    candidate["theta_deg"] = {"+1": 180., "-1": 0.}
    with pytest.raises(ValueError, match="physical opening"):
        camera_choice.select(row, "known", mesh, pose.part_roles(scene.root / "model.glb"), fg,
                             mesh.front_pivot(), "model", "photo")
    candidate["theta_deg"] = {}
    with pytest.raises(ValueError, match="exact photographic arm pose"):
        camera_choice.select(row, "known", mesh, {}, fg, mesh.front_pivot(), "model", "photo")


def test_sdk_wrapper_exposes_and_executes_exact_candidate_choice(tmp_path):
    from agents import function_tool
    from agents.tool_context import ToolContext
    scene, old, mesh, row, true, fg, calls = prepared(tmp_path)
    async def call(ctx, name, action):
        assert name == "choose_camera_orientation"
        return action("scripted-sdk")
    tool = tool_for(scene, lambda name: function_tool(name_override=name), call)
    assert set(tool.params_json_schema["properties"]) == {"reason", "basis", "choices"}
    raw = json.dumps({"reason": "Visible front face in photo", "basis": old.id,
                      "choices": [{"photo": "side", "candidate": "known"}]})
    ctx = ToolContext(context=None, tool_name=tool.name, tool_call_id="scripted-sdk", tool_arguments=raw)
    result = asyncio.run(tool.on_invoke_tool(ctx, raw))
    assert result["status"] == "ok", result
    assert C.load_basis(scene.root).camera("side").to_dict() == true.to_dict()
