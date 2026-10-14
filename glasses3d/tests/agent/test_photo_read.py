"""General main-session photo measurement: arithmetic, error honesty, provenance and real registry. $0."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from agents.tool_context import ToolContext

from glasses3d.agent import photo_read as P
from glasses3d.agent import tools as T
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.g3d_host import compare as C


class Basis:
    def __init__(self, root):
        self.id, self.dir = "basis-01", root / "cameras/basis-01"
        self.dir.mkdir(parents=True)
        for name in ("basis.json", "cameras.json"):
            (self.dir / name).write_text("{}")
        self.frame = NormFrame((0, 0, -70), 200)
        self.pivot_mm = np.zeros(3)
        self.meta = {"model": {"sha256": "f" * 64}}
        self.cameras = {"reference-a": Camera(-50, 7, -2, .12, 430, 400, 300),
                        "reference-b": Camera(45, 4, 3, .10, 450, 400, 300),
                        "reference-c": Camera(175, 65, 78, .05, 400, 400, 300)}
        self.refs, self.poses = {}, {}
        self.path = root / "photo.png"
        Image.new("RGB", (800, 600), "white").save(self.path)
        for rid in self.cameras:
            self.refs[rid] = {"photo_sha256": C.sha256_file(self.path)}

    def camera(self, rid):
        return self.cameras[rid]

    def shape(self, rid):
        return 600, 800

    def pose(self, rid):
        return self.poses.get(rid)

    def photo(self, rid):
        if C.sha256_file(self.path) != self.refs[rid]["photo_sha256"]:
            raise ValueError("photo differs from frozen basis")
        return np.asarray(Image.open(self.path).convert("RGB"))


@pytest.fixture
def basis(tmp_path):
    return Basis(tmp_path)


def picks(basis, xyz, *, photos=("reference-a", "reference-b", "reference-c"), side=None, crossing=False):
    result = []
    for rid in photos:
        uv = project_mm(P.posed_points([xyz], basis, rid, side), basis.camera(rid), basis.frame)[0]
        result.append({"photo": rid, "uv": None if crossing else uv.tolist(),
                       "edges": [[(uv + delta).tolist() for delta in ((-18, 0), (18, 0))],
                                 [(uv + delta).tolist() for delta in ((0, -18), (0, 18))]] if crossing else None,
                       "sd_px": 1.0})
    return result


@pytest.mark.parametrize("xyz", [[-70, 8, -30], [70, 8, -30], [0, 0, 0], [0, 15, -120]])
def test_world_projection_and_reading_do_not_mirror_or_depend_on_model(basis, xyz):
    for rid in basis.refs:
        assert np.allclose(P.R._project(P.world_matrix(basis, rid), xyz)[0],
                           project_mm([xyz], basis.camera(rid), basis.frame)[0], atol=1e-9)
    rec = P.read_point(basis, "corner", picks(basis, xyz))
    assert np.allclose(rec["xyz_mm"], xyz, atol=1e-8)
    assert rec["consistent_with_stated_error"] and not rec["warnings"]
    assert len(rec["pairs"]) == 3 and max(rec["residual_px"]) < 1e-9
    assert np.all(np.asarray(rec["sd_xyz_mm"]) > 0)


@pytest.mark.parametrize("side", [-1, 1])
def test_explicit_temple_pose_returns_neutral_world_point(basis, side):
    for rid, angle in zip(basis.refs, (7, 20, -5)):
        basis.poses[rid] = {f"{side:+d}": {"theta_deg": angle, "axis_xz_mm": [side * 65, -18]}}
    xyz = [side * 52, -4, -145]
    rec = P.read_point(basis, "tip", picks(basis, xyz, side=side), temple_side=side)
    assert np.allclose(rec["xyz_mm"], xyz, atol=1e-8)
    assert max(rec["residual_px"]) < 1e-8
    with pytest.raises(ValueError, match="no temple side"):
        P.read_point(basis, "tip", picks(basis, xyz, side=side), temple_side=-side)


def test_crossings_share_facet_anisotropic_error_and_shallow_warning(basis):
    xyz = [55, 5, -70]
    pk = picks(basis, xyz, crossing=True)
    rec = P.read_point(basis, "cross", pk, kind="crossing")
    assert np.allclose(rec["xyz_mm"], xyz, atol=1e-8)
    assert np.allclose(rec["crossing_deg"], 90)
    for row in pk:
        uv = np.mean(row["edges"][0], axis=0)
        row["edges"][1] = [(uv + delta).tolist() for delta in ((-20, -2), (20, 2))]
    shallow = P.read_point(basis, "shallow", pk, kind="crossing")
    assert "W_SHALLOW_CROSSING" in {w["code"] for w in shallow["warnings"]}
    assert max(np.diag(shallow["uv_cov"][0])) > max(np.diag(rec["uv_cov"][0]))


def test_condition_refuses_one_direction_not_just_low_residual(basis):
    basis.cameras["reference-b"] = basis.cameras["reference-a"]
    with pytest.raises(ValueError, match="nearly one line"):
        P.read_point(basis, "same", picks(basis, [50, 3, -100], photos=("reference-a", "reference-b")))


def test_shared_camera_error_is_correlated_not_independent(basis):
    basis.refs["reference-b"]["transport"] = {"source": "reference-a"}
    basis.cameras["reference-b"] = replace(basis.cameras["reference-a"], center_x=410)
    pk = picks(basis, [40, 3, -80])
    parsed = P.R._parse_picks(basis, pk, "corner", None)
    matrices = [P.world_matrix(basis, row["rid"]) for row in parsed]
    cov, sources = P._pixel_covariance(basis, parsed, matrices, [40, 3, -80], None)
    assert sources == ["reference-a", "reference-a", "reference-c"]
    assert np.allclose(cov[:2, 2:4], np.eye(2), atol=1e-10)
    assert np.allclose(cov[:2, 4:6], 0)
    assert P.read_point(basis, "shared", pk)["consistent_with_stated_error"]


def test_mismatch_reports_joint_and_cross_pair_warnings_without_error_inflation(basis):
    pk = picks(basis, [50, 4, -90])
    pk[2]["uv"][0] += 35
    rec = P.read_point(basis, "conflict", pk)
    assert not rec["consistent_with_stated_error"]
    assert {"W_RESIDUAL", "W_PAIR_DISAGREEMENT"} <= {w["code"] for w in rec["warnings"]}
    assert max(rec["sd_xyz_mm"]) < 2
    assert "conditional" in rec["uncertainty_note"]


def test_bad_picks_changed_pixels_and_duplicate_aliases_refuse(basis):
    pk = picks(basis, [40, 4, -80])
    pk[0]["uv"] = [-3, 4]
    with pytest.raises(ValueError, match="inside the native"):
        P.read_point(basis, "outside", pk)
    pk = picks(basis, [40, 4, -80])
    pk[1]["photo"] = "a"
    with pytest.raises(ValueError, match="aliases"):
        P.read_point(basis, "duplicate", pk)
    basis.path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="differs"):
        P.read_point(basis, "changed", picks(basis, [40, 4, -80]))


def test_real_registry_journals_measurement_images_and_full_hash_bound_report(basis, tmp_path, monkeypatch):
    monkeypatch.setattr(P.C, "load_basis", lambda root, bid: basis)
    scene = SimpleNamespace(root=tmp_path, s=SimpleNamespace(head={"id": "c0001"}),
                            lock=threading.RLock(), calls=[], images=[], unseen=[])
    scene.record_call = lambda *args: scene.calls.append(args)
    scene.record_images = lambda cid, result: scene.images.extend(result["images"])
    scene.mark_unseen = lambda cid: scene.unseen.append(cid)
    tool = next(t for t in T.build_tools(scene) if t.name == "photo_read")
    args = {"points": [{"name": "hinge", "kind": "corner", "temple_side": None,
                         "picks": picks(basis, [-60, 6, -20], photos=("reference-a", "reference-b"))}], "basis": "basis-01"}
    raw = json.dumps(args)
    ctx = ToolContext(context=None, tool_name="photo_read", tool_call_id="measurement-call", tool_arguments=raw)
    output = asyncio.run(tool.on_invoke_tool(ctx, raw))
    result = json.loads(output[0].text)
    assert result["status"] == "ok" and result["checkpoint"] == "c0001"
    assert np.allclose(result["readings"][0]["xyz_mm"], [-60, 6, -20])
    report = tmp_path / result["report_path"]
    assert C.sha256_file(report) == result["report_sha256"]
    assert result["basis_sha256"] == C.sha256_file(basis.dir / "basis.json")
    assert scene.calls[0][:4] == ("measurement-call", "photo_read", "c0001", "c0001")
    assert scene.images and (tmp_path / scene.images[0]["path"]).is_file()


def test_missing_basis_is_not_replaced_by_model_or_camera_guess(tmp_path):
    with pytest.raises(C.NoBasis):
        P.measure(tmp_path, "no-basis", [{"name": "point", "picks": []}])


def test_output_path_cannot_follow_link_out_of_job(basis, tmp_path, monkeypatch):
    monkeypatch.setattr(P.C, "load_basis", lambda root, bid: basis)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "work").mkdir()
    try:
        (tmp_path / "work/photo_read").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not allow directory symlink creation")
    with pytest.raises(ValueError, match="inside the job"):
        P.measure(tmp_path, "escape", [{"name": "point", "picks": picks(basis, [40, 4, -80])}])
    assert not list(outside.iterdir())
