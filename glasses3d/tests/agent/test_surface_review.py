"""Independent informational-diagnostic failure isolation; no renderer or API."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.tools import look
from glasses3d.g3d_host import surface_quality
from glasses3d.g3d_host.camera import Camera, NormFrame


@pytest.mark.parametrize("failure", ["measurement", "side_light"])
@pytest.mark.parametrize("combine", [False, True])
def test_surface_diagnostic_failure_retains_ordinary_photo_and_worn_evidence(tmp_path, monkeypatch, failure, combine):
    root = tmp_path
    model = root / "work/model.glb"
    model.parent.mkdir()
    model.write_bytes(b"synthetic immutable export")
    export = {"status": "exported", "path": str(model), "sha256": look.arview.sha256(model), "checkpoint": "c0001"}
    basis_dir = root / "cameras/basis-01"
    basis_dir.mkdir(parents=True)
    (basis_dir / "basis.json").write_text("{}")
    photo = np.full((64, 64, 3), 235, dtype=np.uint8)
    bs = SimpleNamespace(id="basis-01", dir=basis_dir, refs={"front": {"view_kind": "front", "photo_sha256": "photo"}},
        frame=NormFrame((0, 0, 0), 140), meta={"cameras_json_sha256": "camera"}, usable_refs=lambda: ["front"],
        unusable_refs=lambda: [], photo=lambda _: photo, pose=lambda _: {}, shape=lambda _: (64, 64),
        roi=lambda _: [0, 0, 64, 64], camera=lambda _: Camera(0, 0, 0, .2, 60, 31.5, 31.5))
    monkeypatch.setattr(look.C, "load_basis", lambda *a, **k: bs)
    monkeypatch.setattr(look.shutil, "which", lambda _: "offline-node-stub")
    monkeypatch.setattr(look.look_panels, "load_geometry", lambda _: object())

    def panels(row, palette, geometry, output, **kwargs):
        return {"images": [{"path": row["drawing_path"], "sha256": look.arview.sha256(Path(row["drawing_path"])),
                            "view": "ordinary evidence", "ref": row["ref"],
                            "kind": "surface_detail" if kwargs.get("surface_only") else "look_full_pair"}], "details": []}
    monkeypatch.setattr(look.look_panels, "build_panels", panels)

    def write_capture(folder, cameras, portrait=False):
        folder.mkdir(parents=True, exist_ok=True)
        images = []
        for camera in cameras:
            for variant in ("beauty", "part-id"):
                name = camera["id"] + "__" + variant + ".png"
                Image.new("RGB", (camera["width"], camera["height"]), "navy").save(folder / name)
                images.append({"file": name, "sha256": look.arview.sha256(folder / name)})
        if portrait:
            Image.new("RGB", (64, 64), "pink").save(folder / "portrait.png")
            images.append({"file": "portrait.png", "sha256": look.arview.sha256(folder / "portrait.png"),
                           "kind": "portrait", "variant": "condition-a", "region": "eyes"})
        report = folder / "report.json"
        report.write_text(json.dumps({"input": {"sha256": export["sha256"]}, "images": images,
            "id_palette": [{"class": "frame", "rgb": [0, 0, 128], "decode_tolerance": 1}]}))
        return {"report": str(report), "report_sha256": look.arview.sha256(report), "export_sha256": export["sha256"]}

    def capture(argv, directory, **kwargs):
        cameras = json.loads(Path(argv[argv.index("--cameras") + 1]).read_text())["cameras"]
        if failure == "side_light" and "lighting" in cameras[0]:
            return 1, False
        write_capture(Path(argv[argv.index("--out") + 1]), cameras)
        return 0, False
    monkeypatch.setattr(look.arview, "run_capture", capture)

    def measured(_):
        if failure == "measurement":
            raise ValueError("independent injected unavailable surface analysis")
        return {"schema": "g3d.surface_quality/1", "informational_only": True, "objects": []}
    monkeypatch.setattr(surface_quality, "report", measured)
    worn = write_capture(root / "work/worn-source", [], portrait=True)
    result = look.capture(root, export, "independent-review", worn=worn, combine=combine)
    assert result["status"] == "ok", result.get("message", result)
    assert result["views"][0]["images"] and result["worn_front_path"]
    assert len(result["images"]) >= 2
    assert any(image.get("kind") == "look_composed" for image in result["images"]) == combine
    diagnostic = result["surface_quality"]
    if failure == "measurement":
        assert diagnostic["measurement_status"] == "unavailable"
        assert diagnostic["views"]
    else:
        assert diagnostic["status"] == "unavailable"
    assert model.read_bytes() == b"synthetic immutable export"
