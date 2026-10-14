"""$0 M5-fix-2 worn-view proof on the owner-accepted Oakley; no modelling or provider client."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from glasses3d.agent.tools import arview, _image
from PIL import Image, ImageChops

REPO = Path(__file__).resolve().parents[3]
ACCEPTED = REPO / "automation/data/blender_agent/studio/jobs/3dff867466d449c9a6369273f0a14631/agent/ar/h_detailed_closed_shield-6c7d3bd5/model.glb"
SHA = "2c46877ad2d90d5e51a0c79b2b9dbfa40f8cd1daa99fbaf98e96b4cd3cc59688"
STANDARD = ["front", "yaw_p30", "yaw_m30", "yaw_p45", "yaw_m45", "pitch_p20", "pitch_m20"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    out = parser.parse_args().out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    assert arview.sha256(ACCEPTED) == SHA, "accepted asset identity changed"
    model = out / "work/accepted.glb"
    model.parent.mkdir(parents=True)
    shutil.copyfile(ACCEPTED, model)
    result = arview.capture(out, {"status": "exported", "path": str(model), "sha256": SHA,
                                "checkpoint": "owner-accepted-oakley"}, "worn-proof")
    assert result["status"] == "ok", result
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert report["request"]["fixed_scale"] is True
    assert report["request"]["plan"]["poses"] == STANDARD + ["yaw_p50", "yaw_m50"]
    assert len(report["images"]) == report["planned_images"] == 20
    for pose in report["poses"]:
        assert pose["fitting"]["mode"] == "fixed" and pose["fitting"]["scale"] == 1
    crops = result["worn_views"]
    assert len(crops) == 2
    for crop in crops:
        assert crop["timing"]["visible_head"] is True and crop["timing"]["hair_applied"] is False
        assert crop["head"] == "same occlusion meshes" and crop["hair"] is False
        assert arview.sha256(Path(crop["path"])) == crop["sha256"]
    emitted, _ = _image(out, result["images"][0]["path"], trusted_output=True)
    assert emitted["size_px"] == emitted["source_size_px"] == [1024, 1000]
    with Image.open(out / emitted["path"]) as shown, Image.open(result["images"][0]["path"]) as source:
        assert ImageChops.difference(shown.convert("RGB"), source.convert("RGB")).getbbox() is None
    # Independently capture the unchanged seven-pose path and compare every native standard panel byte-for-byte.
    plain = out / "plain"
    process = out / "plain-process"
    process.mkdir()
    code, timed_out = arview.run_capture([shutil.which("node"), str(arview.ARVIEW / "bin/arview.mjs"), "capture", str(model),
                                        "--out", str(plain), "--poses", "default", "--backgrounds", "skin",
                                        "--fixed-scale", "--no-portrait", "--lensenv", "1"], process, timeout_s=300)
    assert code == 0 and not timed_out
    plain_report = json.loads((plain / "report.json").read_text(encoding="utf-8"))
    original = {image["file"]: image["sha256"] for image in plain_report["images"]}
    matches = [{"file": image["file"], "same": image["sha256"] == original[image["file"]]}
               for image in report["images"] if image["pose"] in STANDARD]
    assert len(matches) == 14 and all(row["same"] for row in matches)
    proof = {"status": "passed", "cost_usd": 0, "accepted_asset": str(ACCEPTED), "accepted_sha256": SHA,
             "capture": result, "emitted_image": emitted, "standard_panels_unchanged": matches,
             "temple_reach": [{"pose": image["pose"], **image["temple_reach"]} for image in report["images"]
                              if image.get("pose") in {"yaw_p50", "yaw_m50"} and image.get("temple_reach")],
             "note": "Visible head uses the exact existing depth meshes. No anatomical ears were invented; hidden portions remain governed by the original relief/return/clip."}
    (out / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    print(json.dumps({"status": proof["status"], "proof": str(out / "proof.json"), "cost_usd": 0,
                      "worn_views": [crop["path"] for crop in crops], "unchanged_standard_panels": len(matches)}))


if __name__ == "__main__":
    main()
