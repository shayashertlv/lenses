"""$0 helper -> real GLB extension -> fixed-scale AR at seven poses, on synthetic glasses only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import numpy as np
from PIL import Image

from glasses3d.agent import jobs, procs
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.tools import arview
from glasses3d.g3d_host import glb
from glasses3d.tests.proofs.assembly_m1 import build_code


def run(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    photos = out / "photos"
    photos.mkdir()
    Image.new("RGB", (16, 16), "white").save(photos / "front.png")
    views = out / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": "both"}}))
    root = jobs.create_job(images=photos, views=views, name="Coating proof",
                          description="Synthetic optical test only; no product inference.", jobs_root=out / "jobs")
    scene = JobScene(root)
    cases = {}
    started = time.monotonic()
    try:
        scene.start()
        assert scene.s.execute(build_code(), label="synthetic optical fixture")["status"] == "ok"
        for factor in (0.0, 1.0):
            name = "uncoated" if not factor else "coated"
            code = f"""import bpy
import g3d
mat = bpy.data.materials['lens']
node = mat.node_tree.nodes.get('Principled BSDF')
node.inputs['Transmission Weight'].default_value = 0.65
node.inputs['Roughness'].default_value = 0.12
result = g3d.lens_coating(mat, factor={factor}, ior=1.38,
    thickness_min_nm=140.0, thickness_max_nm=460.0, tint=[0.18, 0.45, 0.28])
"""
            changed = scene.s.execute(code, label=name)
            assert changed["status"] == "ok", changed
            exp = scene.seeing.export_head(call_id=name)
            assert exp["status"] == "exported" and exp["validate_spec"]["ok"], exp
            materials = glb.load(exp["path"]).materials
            lens = next(m for m in materials if (m.get("extras") or {}).get("partRole") == "lens")
            coating = (lens.get("extensions") or {}).get("KHR_materials_iridescence")
            if factor:
                assert coating and coating.get("iridescenceFactor") == 1.0, lens
                assert abs(coating.get("iridescenceIor", 1.3) - 1.38) < 1e-5
                assert coating.get("iridescenceThicknessMinimum", 100) == 140.0
                assert coating.get("iridescenceThicknessMaximum", 400) == 460.0
            else:
                assert not coating or coating.get("iridescenceFactor", 0) == 0, lens
            capture = arview.capture(root, exp, name)
            assert capture["status"] == "ok" and capture["fixed_scale"] is True, capture
            cases[name] = {"helper": changed, "export": exp, "lens_material": lens, "arview": capture}
        exit_checkpoint = scene.exit_checkpoint()
        assert exit_checkpoint["verified"]
    finally:
        scene.close()
    def captures(case):
        path = Path(case["arview"]["report"])
        doc = json.loads(path.read_text())
        return {(record["pose"], record["variant"]): path.parent / record["file"] for record in doc["images"]}
    before, after = captures(cases["uncoated"]), captures(cases["coated"])
    differences = []
    for key in before:
        if key[1] != "beauty":
            continue
        a = np.asarray(Image.open(before[key]).convert("RGB"), dtype=float)
        b = np.asarray(Image.open(after[key]).convert("RGB"), dtype=float)
        diff = np.abs(a - b)
        differences.append({"pose": key[0], "changed_pixels": int(np.any(diff > 0, axis=2).sum()),
                            "mean_abs_rgb_255": float(diff.mean()), "before": str(before[key]), "after": str(after[key])})
    assert len(differences) >= 3 and sum(row["changed_pixels"] > 0 for row in differences) >= 3, differences
    proof = {"status": "passed", "cases": cases, "poses": differences, "exit_checkpoint": exit_checkpoint,
             "meaning": "Exported coating changes the fixed AR rendering; this does not certify photographic likeness.",
             "model_requests": 0, "actual_spend_usd": 0, "seconds": time.monotonic() - started}
    procs.write_json_atomic(out / "proof.json", proof)
    print(json.dumps({key: proof[key] for key in ("status", "poses", "actual_spend_usd", "seconds")}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    os.environ["G3D_OFFLINE"] = "1"
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        run(args.out)
    assert not network.attempts and not clients.constructions
