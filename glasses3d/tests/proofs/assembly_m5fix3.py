"""$0 copy-job proof: run-3 c0007 re-export, exact material-only diff, fixed AR before/after.

The original job, checkpoint and delivery are read only. No run request, approval or delivery is created.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import time

from PIL import Image, ImageDraw

from glasses3d.agent import jobs, procs
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.tools import arview, _image
from glasses3d.g3d import glb_patch
from glasses3d.g3d_host import blender

REPO = Path(__file__).resolve().parents[3]
JOB = REPO / "glasses3d/data/jobs/oakley-oo9208-v2-3e057bab"
DELIVERY = JOB / "deliveries/20261014T142845-c99f84-f4ee4a39a933a46eaf4c88c596439c353a047f67848ebe5767b531b9188071d1"
ORIGINAL_SHA = "1f5bad10f75c3a03410158a0ff11b03d7133591da1a4cd32895e5dafa9f72603"
CHECKPOINT_SHA = "b506e111b40640c3c68746dcfb798fe28a450d55e422d6f8e97c64146cbdd383"


def run(out):
    started = time.monotonic()
    out.mkdir(parents=True, exist_ok=False)
    checkpoint, original = JOB / "checkpoints/c0007.blend", DELIVERY / "model.glb"
    assert arview.sha256(checkpoint) == CHECKPOINT_SHA
    assert arview.sha256(original) == ORIGINAL_SHA
    source = jobs.load_job(JOB)
    inputs = {str(JOB / row["file"]): row["sha256"] for row in source["images"]}
    inputs.update({str(checkpoint): CHECKPOINT_SHA, str(original): ORIGINAL_SHA,
                   str(JOB / "job.json"): arview.sha256(JOB / "job.json")})
    views = {Path(row["file"]).name: {"kind": row["view_kind"], "side": row["side"],
                                    "matte_mode": row["matte_mode"]} for row in source["images"]}
    (out / "views.json").write_text(json.dumps(views))
    root = jobs.create_job(images=JOB / "images", views=out / "views.json", name="Run 3 re-export proof copy",
                          description=source["description"], jobs_root=out / "jobs")
    (root / "checkpoints").mkdir()
    copied = root / "checkpoints/c0007.blend"
    shutil.copyfile(checkpoint, copied)
    before = root / "work/run3-delivered.glb"
    before.parent.mkdir()
    shutil.copyfile(original, before)
    after = root / "work/run3-reexport.glb"
    checks = blender.export_and_validate(copied, after)
    procs.write_json_atomic(out / "export.json", checks)
    assert checks["export"]["status"] == "exported" and checks["export"]["scene_unchanged"], checks
    assert checks["spec"]["ok"], checks["spec"]
    old_doc, old_bin = glb_patch.read_chunks(before.read_bytes())
    new_doc, new_bin = glb_patch.read_chunks(after.read_bytes())
    assert old_bin == new_bin, "geometry or other binary content changed"
    expected = copy.deepcopy(old_doc)
    changes = []
    for index, (old, new) in enumerate(zip(old_doc["materials"], new_doc["materials"])):
        if old.get("doubleSided", False) != new.get("doubleSided", False):
            assert old["doubleSided"] is True and new["doubleSided"] is False
            expected["materials"][index]["doubleSided"] = False
            changes.append({"index": index, "name": old["name"], "before": True, "after": False})
    assert new_doc == expected, "a field other than doubleSided changed"
    assert {entry["material"] for entry in checks["export"]["single_sided_materials"]} == {row["name"] for row in changes}
    assert changes
    captures = {}
    for label, model in [("before", before), ("after", after)]:
        result = arview.capture(root, {"status": "exported", "path": str(model), "sha256": arview.sha256(model),
                                      "checkpoint": "run3-c0007"}, label)
        captures[label] = result
        procs.write_json_atomic(out / f"{label}.json", result)
        assert result["status"] == "ok", result
        report = json.loads(Path(result["report"]).read_text())
        assert len(report["images"]) == report["planned_images"] == 35
        assert len(result["worn_views"]) == 2
        for worn in result["worn_views"]:
            assert worn["hair"] is False and worn["timing"]["visible_head"] is True
        assert len([r for r in report["images"] if r.get("background") == "checker"]) == 1
        assert len(result["lens_background_response"]["views"]) == 9
        assert all(row["status"] == "measured" for row in result["lens_background_response"]["views"])
        image, _ = _image(root, result["images"][0]["path"], trusted_output=True)
        assert image["source_size_px"] == image["size_px"] == [1024, 1012]
    def records(label):
        path = Path(captures[label]["report"])
        return path.parent, {rec["file"]: rec for rec in json.loads(path.read_text())["images"]}
    before_dir, before_records = records("before")
    after_dir, after_records = records("after")
    for filename, rec in before_records.items():
        if rec["variant"] == "part-id":
            assert rec["sha256"] == after_records[filename]["sha256"], "geometry appearance changed"
    response = {label: next(row for row in capture["lens_background_response"]["views"] if row["view"] == "front")
                ["mean_channel_background_response_gain"] for label, capture in captures.items()}
    assert abs(response["after"] - .1201514668) < .005, response  # prior isolated diagnostic, not a runtime gate
    assert response["after"] > response["before"] * 7, response
    # Label native eye crops without any colour adjustment. They are the fixed test face, not the owner's webcam.
    faces = [Image.open(directory / "portrait__a__eyes.png").convert("RGB") for directory in [before_dir, after_dir]]
    assert faces[0].size == faces[1].size
    width, height = faces[0].size
    panel = Image.new("RGB", (width * 2, height + 48), "white")
    draw = ImageDraw.Draw(panel)
    for index, (face, label) in enumerate(zip(faces, ["Before: run 3 delivered", "After: run 3 re-exported with the lens fix, not a new delivery"])):
        panel.paste(face, (index * width, 48))
        draw.text((index * width + 10, 14), label, fill="black", font_size=19)
    panel.save(out / "before-after-face.png")
    for path, sha in inputs.items():
        assert arview.sha256(Path(path)) == sha, path
    assert arview.sha256(copied) == CHECKPOINT_SHA
    proof = {"status": "passed", "actual_spend_usd": 0, "model_requests": 0,
             "copy_job": str(root), "unchanged_sources": inputs, "checkpoint": "c0007", "export": checks,
             "only_glb_changes": changes, "binary_identical": True, "front_background_response": response,
             "captures": captures, "face_comparison": str(out / "before-after-face.png"),
             "seconds": time.monotonic() - started}
    procs.write_json_atomic(out / "proof.json", proof)
    print(json.dumps({key: proof[key] for key in ("status", "copy_job", "only_glb_changes", "front_background_response", "actual_spend_usd", "seconds")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.environ["G3D_OFFLINE"] = "1"
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        run(args.out.resolve())
    assert not network.attempts and not clients.constructions
