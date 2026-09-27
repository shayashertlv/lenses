"""Additional native calibration observations near lens-local gradient ends.

This is a separate experiment, not a modification of the canonical fixture or
its conformance grid. It acquires actual Cycles observations where the initial
interior sampling provides insufficient basis coverage for inverse fitting.

Run inside Blender:
  blender --background --factory-startup --disable-autoexec --python scripts/blender_lens_boundary_probe.py -- \
    --cases data/lens-conformance/cases.json --output data/lens-conformance/calibration-boundaries
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import bpy
from mathutils import Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconstruction.blender_lens_material import compile_lens_material
from reconstruction.lens_appearance import LensAppearance
from scripts.blender_lens_probe import (
    EXPECTED_ANGLES, EXPECTED_CASE_IDS, PROBED_ENVIRONMENT_IDS,
    assess_bundle, render_linear, setup,
)

BOUNDARY_V = (0.025, 0.975)
EXPECTED_CAPTURE_COUNT = 420


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "data/lens-conformance/cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/lens-conformance/calibration-boundaries")
    parser.add_argument("--resolution", type=int, default=32)
    parser.add_argument("--samples", type=int, default=64)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def main():
    args = arguments()
    if args.resolution < 8 or args.samples < 1:
        raise ValueError("Capture needs resolution>=8 and samples>=1")
    args.cases = args.cases.resolve()
    args.output = args.output.resolve()
    if args.output == args.cases.parent / "blender":
        raise ValueError("Boundary calibration must not overwrite the original conformance capture")
    document = json.loads(args.cases.read_text(encoding="utf-8"))
    fixture_hash = sha256(args.cases)
    coverage = assess_bundle(document)
    if not coverage["complete"]:
        raise ValueError("Boundary capture requires the complete original fixture: " + "; ".join(coverage["errors"]))
    cases = document["cases"]
    environments = {environment["id"]: environment for environment in document["environments"]}
    args.output.mkdir(parents=True, exist_ok=True)
    scene, slab, camera, world, rear = setup(args.resolution, args.samples)
    results = []
    started = time.perf_counter()
    for case in cases:
        appearance = LensAppearance.from_dict(case["appearance"])
        if appearance.roughness != 0:
            raise ValueError("This controlled capture requires roughness=0")
        material = compile_lens_material(appearance, case["id"])
        slab.data.materials.clear()
        slab.data.materials.append(material)
        for v in BOUNDARY_V:
            for angle in EXPECTED_ANGLES:
                radians = math.radians(angle)
                target = Vector((0, 2 * v - 1, 0))
                camera.location = target + Vector((3 * math.sin(radians), 0, 3 * math.cos(radians)))
                camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
                reference = appearance.evaluate(v, angle)
                for environment_id in PROBED_ENVIRONMENT_IDS:
                    environment = environments[environment_id]
                    background = environment["background_linear_rgb"]
                    reflected = environment["reflected_linear_rgb"]
                    world.inputs["Color"].default_value = (*reflected, 1)
                    rear.inputs["Color"].default_value = (*background, 1)
                    name = f"{case['id']}_v{v:g}_a{angle:g}_{environment_id}"
                    path = args.output / f"{name}.exr"
                    pixels = render_linear(scene, path)
                    center, radius = args.resolution // 2, max(1, args.resolution // 4)
                    crop = pixels[center - radius:center + radius, center - radius:center + radius, :3]
                    actual = np.mean(crop, axis=(0, 1)).astype(np.float64)
                    expected = reference.compose(background, reflected)
                    error = float(np.max(np.abs(actual - expected)))
                    results.append({"case": case["id"], "v": v, "angle_degrees": angle,
                                    "environment": environment_id, "actual_linear_rgb": actual.tolist(),
                                    "expected_linear_rgb": expected.tolist(), "max_absolute_error": error,
                                    "exr": path.name, "exr_sha256": sha256(path)})
        slab.data.materials.clear()
        bpy.data.materials.remove(material)
        print("LENS_BOUNDARY_CASE " + json.dumps({"case": case["id"], "captured": len(results)}), flush=True)
    expected_keys = {(case_id, v, angle, environment_id) for case_id in EXPECTED_CASE_IDS
                     for v in BOUNDARY_V for angle in EXPECTED_ANGLES for environment_id in PROBED_ENVIRONMENT_IDS}
    actual_keys = [(row["case"], row["v"], row["angle_degrees"], row["environment"]) for row in results]
    finite = all(np.isfinite(row["actual_linear_rgb"]).all() for row in results)
    unchanged_fixture = sha256(args.cases) == fixture_hash
    complete = (len(actual_keys) == EXPECTED_CAPTURE_COUNT and set(actual_keys) == expected_keys
                and finite and unchanged_fixture)
    report = {
        "schema_version": 1, "capture_kind": "additional_boundary_calibration", "complete_capture": complete,
        "is_conformance_grid": False, "fixture_completeness": coverage,
        "case_ids": [case["id"] for case in cases], "boundary_v": list(BOUNDARY_V),
        "angles_degrees": list(EXPECTED_ANGLES), "environment_ids": list(PROBED_ENVIRONMENT_IDS),
        "expected_probe_count": EXPECTED_CAPTURE_COUNT, "probe_count": len(results), "pose_count": 14,
        "color_space": "scene_linear_srgb_D65", "engine": "CYCLES CPU", "blender": bpy.app.version_string,
        "samples": args.samples, "resolution": args.resolution, "seconds": time.perf_counter() - started,
        "cases_file": str(args.cases), "cases_sha256": fixture_hash, "fixture_unchanged": unchanged_fixture,
        "source_sha256": sha256(__file__),
        "compiler_sha256": sha256(ROOT / "reconstruction/blender_lens_material.py"),
        "reference_sha256": sha256(ROOT / "reconstruction/lens_appearance.py"),
        "native_probe_sha256": sha256(ROOT / "scripts/blender_lens_probe.py"),
        "maximum_absolute_error": max(row["max_absolute_error"] for row in results),
        "limitations": ["Additional experimental calibration evidence; original conformance grid is unchanged",
                        "Effective single surface with zero roughness and uniform reflected/background radiance",
                        "No ray displacement or internal reflections; not a production optical model",
                        "Central half-image average spans a small UV interval; reference is evaluated at its exact center"],
        "results": results,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("LENS_BOUNDARY_SUMMARY " + json.dumps({key: report[key] for key in
          ("complete_capture", "probe_count", "maximum_absolute_error", "seconds", "cases_sha256")}), flush=True)
    if not complete:
        raise RuntimeError("Boundary calibration capture is incomplete; inspect report")


if __name__ == "__main__":
    main()
