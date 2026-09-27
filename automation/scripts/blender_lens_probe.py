"""Run inside Blender to test real shader output against shared canonical cases.

Example:
  blender --background --factory-startup --disable-autoexec --python scripts/blender_lens_probe.py -- \
    --cases data/lens-conformance/cases.json --output data/lens-conformance/blender

The saved EXRs and numeric comparisons use scene-linear radiance. PNGs are
display encodings only. This small controlled probe does not validate general
lighting, finite roughness, curved closed meshes, or production AR optics.
"""

from __future__ import annotations

import argparse
from collections import Counter
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

EXPECTED_CASE_IDS = (
    "clear", "neutral_tint", "saturated_tint", "vertical_gradient", "multi_stop_gradient",
    "strong_neutral_mirror", "total_mirror", "strong_colored_mirror", "angular_coating", "mirrored_gradient",
)
EXPECTED_ENVIRONMENT_IDS = (
    "neutral_gray", "dark_mirror", "transmission_only", "black", "white", "colored_studio",
)
PROBED_ENVIRONMENT_IDS = ("transmission_only", "dark_mirror", "colored_studio")
EXPECTED_UV_VALUES = tuple(index / 8 for index in range(9))
EXPECTED_ANGLES = (0, 15, 30, 45, 60, 75, 85)
EXPECTED_SAMPLE_GRID = {(v, angle) for v in EXPECTED_UV_VALUES for angle in EXPECTED_ANGLES}
EXPECTED_INTERIOR_POSES = [(v, angle) for v in EXPECTED_UV_VALUES if 0 < v < 1 for angle in EXPECTED_ANGLES]


def assess_bundle(document):
    """Assess full fixture coverage independently from the requested render subset."""
    errors = []
    case_ids = [case.get("id") for case in document.get("cases", [])]
    environment_ids = [environment.get("id") for environment in document.get("environments", [])]
    if document.get("schema_version") != 1 or document.get("color_space") != "scene_linear_srgb_D65":
        errors.append("Expected fixture schema 1 in scene_linear_srgb_D65")
    if Counter(case_ids) != Counter(EXPECTED_CASE_IDS):
        errors.append("Fixture must contain exactly the ten expected unique case IDs")
    if Counter(environment_ids) != Counter(EXPECTED_ENVIRONMENT_IDS):
        errors.append("Fixture must contain exactly the six expected unique environment IDs")
    sample_coverage = []
    for case in document.get("cases", []):
        samples = case.get("samples", [])
        coordinates = [(sample.get("v"), sample.get("angle_degrees")) for sample in samples]
        complete = len(coordinates) == 63 and set(coordinates) == EXPECTED_SAMPLE_GRID
        compositions_complete = all(set(sample.get("compositions", {})) == set(EXPECTED_ENVIRONMENT_IDS)
                                    for sample in samples)
        if not complete:
            errors.append(f"{case.get('id')}: expected exactly 63 unique samples on the declared UV/angle grid")
        if not compositions_complete:
            errors.append(f"{case.get('id')}: every sample must declare all six environment compositions")
        sample_coverage.append({"case": case.get("id"), "sample_count": len(coordinates),
                                "unique_sample_count": len(set(coordinates)), "complete_grid": complete,
                                "complete_environment_compositions": compositions_complete})
    return {"complete": not errors, "errors": errors, "case_ids": case_ids,
            "environment_ids": environment_ids, "expected_case_ids": list(EXPECTED_CASE_IDS),
            "expected_environment_ids": list(EXPECTED_ENVIRONMENT_IDS), "sample_coverage": sample_coverage}


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "data/lens-conformance/cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/lens-conformance/blender")
    parser.add_argument("--resolution", type=int, default=32)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--tolerance", type=float, default=0.012)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--quick", action="store_true", help="Only the three contact-sheet poses")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def setup(resolution, samples):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 4
    scene.cycles.transparent_max_bounces = 4
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    world = bpy.data.worlds.new("Independent uniform reflected environment")
    world.use_nodes = True
    scene.world = world
    world_background = world.node_tree.nodes.get("Background")
    world_background.inputs["Strength"].default_value = 1

    # A front-facing single surface is deliberate: the canonical effective
    # slab already accounts for transmission and coating once.
    mesh = bpy.data.meshes.new("Single effective slab")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.update()
    uv = mesh.uv_layers.new(name="LensUV")
    for loop, xy in zip(uv.data, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop.uv = xy
    slab = bpy.data.objects.new("Canonical lens sample", mesh)
    scene.collection.objects.link(slab)

    bpy.ops.mesh.primitive_plane_add(size=200, location=(0, 0, -3))
    rear = bpy.context.object
    rear.name = "Independent transmitted background"
    material = bpy.data.materials.new("Rear radiance")
    material.use_nodes = True
    material.node_tree.nodes.clear()
    emission = material.node_tree.nodes.new("ShaderNodeEmission")
    output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
    rear.data.materials.append(material)
    camera = bpy.data.objects.new("Probe camera", bpy.data.cameras.new("Orthographic probe"))
    scene.collection.objects.link(camera)
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 0.02
    scene.camera = camera
    return scene, slab, camera, world_background, emission


def render_linear(scene, target):
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "32"
    scene.render.filepath = str(target)
    bpy.ops.render.render(write_still=True)
    loaded = bpy.data.images.load(str(target), check_existing=False)
    values = np.empty(len(loaded.pixels), np.float32)
    loaded.pixels.foreach_get(values)
    image = values.reshape(loaded.size[1], loaded.size[0], 4)
    bpy.data.images.remove(loaded)
    return image


def save_png(scene, pixels, path, name):
    image = bpy.data.images.new(name, width=pixels.shape[1], height=pixels.shape[0], alpha=True, float_buffer=True)
    image.colorspace_settings.name = "Linear Rec.709"
    image.pixels.foreach_set(pixels.astype(np.float32).ravel())
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    image.save_render(str(path), scene=scene)
    bpy.data.images.remove(image)


def main():
    args = arguments()
    if args.resolution < 8 or args.samples < 1 or not 0 < args.tolerance < 1:
        raise ValueError("Probe needs resolution>=8, samples>=1 and tolerance in (0,1)")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    cases_document = json.loads(args.cases.read_text(encoding="utf-8"))
    bundle_completeness = assess_bundle(cases_document)
    # Invalid coverage can still yield useful diagnostics. Render each known
    # case/environment only once; unknown IDs never become filesystem paths.
    known_cases = {}
    for case in cases_document["cases"]:
        if case["id"] in EXPECTED_CASE_IDS:
            known_cases.setdefault(case["id"], case)
    cases = list(known_cases.values())
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        raise ValueError("Conformance bundle contains no cases")
    environments = {}
    for item in cases_document["environments"]:
        environments.setdefault(item["id"], item)
    selected_environments = [environments[key] for key in PROBED_ENVIRONMENT_IDS if key in environments]
    contact_poses = [(0.125, 0), (0.5, 45), (0.875, 75)]
    # Interior fixture UV samples bracket density knots without rendering a
    # plane's literal boundary, where half a pixel would see no lens at all.
    poses = contact_poses if args.quick else EXPECTED_INTERIOR_POSES
    scene, slab, camera, world, rear = setup(args.resolution, args.samples)
    results, tiles, contracts = [], {}, []
    started = time.perf_counter()
    for case in cases:
        appearance = LensAppearance.from_dict(case["appearance"])
        if appearance.roughness != 0:
            raise ValueError("This controlled conformance probe requires roughness=0")
        material = compile_lens_material(appearance, case["id"])
        graph = material.node_tree
        # Actual native graph contract: arbitrary CPU-colored emission or a
        # baked image cannot accidentally satisfy this proof compiler's test.
        assert json.loads(material["lens_appearance_v1"]) == appearance.to_dict()
        assert not any(node.bl_idname in {"ShaderNodeEmission", "ShaderNodeTexImage"} for node in graph.nodes)
        assert graph.nodes["Lens-local coordinates"].uv_map == "LensUV"
        assert graph.nodes["Canonical reflection R"].inputs["Roughness"].default_value == appearance.roughness
        assert graph.nodes["Canonical reflection R"].inputs["Color"].is_linked
        assert graph.nodes["Canonical transmission T"].inputs["Color"].is_linked
        assert graph.nodes["Effective slab output"].inputs["Surface"].links[0].from_node.bl_idname == "ShaderNodeAddShader"
        contracts.append({"case": case["id"], "passed": True, "descriptor_preserved": True, "dynamic_uv_and_angle_math": True,
                          "no_emission_or_image_lookup": True, "additive_transmission_reflection": True,
                          "nodes": len(graph.nodes)})
        slab.data.materials.clear()
        slab.data.materials.append(material)
        available_samples = {}
        for sample in case["samples"]:
            available_samples.setdefault((sample["v"], sample["angle_degrees"]), sample)
        for v, angle in poses:
            if (v, angle) not in available_samples:
                continue
            radians = math.radians(angle)
            target = Vector((0, 2 * v - 1, 0))
            camera.location = target + Vector((3 * math.sin(radians), 0, 3 * math.cos(radians)))
            camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
            # Shared fixture samples are independently generated by the CPU
            # reference. Require exact coordinates rather than nearest matching.
            saved_sample = available_samples[(v, angle)]
            for environment in selected_environments:
                if environment["id"] not in saved_sample.get("compositions", {}):
                    continue
                background = environment["background_linear_rgb"]
                reflected = environment["reflected_linear_rgb"]
                world.inputs["Color"].default_value = (*reflected, 1)
                rear.inputs["Color"].default_value = (*background, 1)
                name = f"{case['id']}_v{v:g}_a{angle:g}_{environment['id']}"
                pixels = render_linear(scene, args.output / f"{name}.exr")
                center = args.resolution // 2
                radius = max(1, args.resolution // 4)
                crop = pixels[center - radius:center + radius, center - radius:center + radius, :3]
                actual = np.mean(crop, axis=(0, 1)).astype(np.float64)
                expected = np.asarray(saved_sample["compositions"][environment["id"]], np.float64)
                error = np.abs(actual - expected)
                result = {"case": case["id"], "v": v, "angle_degrees": angle,
                          "environment": environment["id"], "expected_linear_rgb": expected.tolist(),
                          "actual_linear_rgb": actual.tolist(), "max_absolute_error": float(error.max()),
                          "passed": bool(error.max() <= args.tolerance), "exr": f"{name}.exr"}
                results.append(result)
                print("LENS_PROBE " + json.dumps(result), flush=True)
                # Expected / observed halves make the contact sheet reviewable.
                tile = np.ones((args.resolution, args.resolution * 2, 4), np.float32)
                tile[:, :args.resolution, :3] = expected
                tile[:, args.resolution:] = pixels
                if (v, angle) in contact_poses:
                    tiles[(case["id"], v, angle, environment["id"])] = tile
        slab.data.materials.clear()
        bpy.data.materials.remove(material)
    columns = 9
    missing_tile = np.full((args.resolution, args.resolution * 2, 4), 0.15, np.float32)
    missing_tile[:, :, 3] = 1
    ordered_tiles = [tiles.get((case["id"], v, angle, environment["id"]), missing_tile) for case in cases
                     for v, angle in contact_poses for environment in selected_environments]
    if not ordered_tiles:
        ordered_tiles = [missing_tile]
    rows = math.ceil(len(ordered_tiles) / columns)
    sheet = np.ones((rows * args.resolution, columns * args.resolution * 2, 4), np.float32)
    for index, tile in enumerate(ordered_tiles):
        y, x = (index // columns) * args.resolution, (index % columns) * args.resolution * 2
        sheet[y:y + args.resolution, x:x + args.resolution * 2] = tile
    save_png(scene, sheet, args.output / "contact-sheet.png", "Expected left, Blender right")
    render_targets_passed = bool(results) and all(row["passed"] for row in results)
    expected_probe_keys = {(case_id, v, angle, environment_id) for case_id in EXPECTED_CASE_IDS
                           for v, angle in EXPECTED_INTERIOR_POSES for environment_id in PROBED_ENVIRONMENT_IDS}
    actual_probe_keys = [(row["case"], row["v"], row["angle_degrees"], row["environment"]) for row in results]
    complete_probe_coverage = len(actual_probe_keys) == 1470 and set(actual_probe_keys) == expected_probe_keys
    complete_native_contracts = (Counter(row["case"] for row in contracts) == Counter(EXPECTED_CASE_IDS)
                                 and all(row["passed"] for row in contracts))
    complete_conformance = (bundle_completeness["complete"] and complete_probe_coverage
                            and len(poses) == 49 and complete_native_contracts and render_targets_passed)
    report = {"schema_version": 1, "compiler": "effective-slab-blender-v1", "blender": bpy.app.version_string,
              "engine": "CYCLES CPU", "color_space": "scene_linear_srgb_D65", "samples": args.samples,
              "resolution": args.resolution, "absolute_tolerance": args.tolerance,
              "passed": complete_conformance, "complete_conformance": complete_conformance,
              "render_targets_passed": render_targets_passed,
              "summary_level": "complete_conformance" if complete_conformance else "partial_or_failed_diagnostic",
              "bundle_completeness": bundle_completeness, "rendered_case_ids": [case["id"] for case in cases],
              "probed_environment_ids": [environment["id"] for environment in selected_environments],
              "complete_probe_coverage": complete_probe_coverage, "complete_native_contracts": complete_native_contracts,
              "probe_count": len(results), "expected_probe_count": 1470,
              "pose_count": len(poses), "native_graph_contracts": contracts,
              "maximum_absolute_error": max((row["max_absolute_error"] for row in results), default=None),
              "seconds": time.perf_counter() - started, "cases_file": str(args.cases.resolve()),
              "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              "compiler_sha256": hashlib.sha256((ROOT / "reconstruction/blender_lens_material.py").read_bytes()).hexdigest(),
              "reference_sha256": hashlib.sha256((ROOT / "reconstruction/lens_appearance.py").read_bytes()).hexdigest(),
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "contact_sheet": "contact-sheet.png", "contact_sheet_layout":
              "Rows: cases in fixture order. Columns: v/angle pose order then transmission-only/reflection-only/colored environment. Each tile: expected left, actual right.",
              "limitations": ["effective single surface; no ray displacement or internal reflections",
                              "uniform reflected and background radiance only; roughness must be zero",
                              "central half-image average spans a small UV interval; gradient expected is exact center",
                              "not a production optical model or arbitrary Blender/AR material equivalence"],
              "results": results}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("LENS_PROBE_SUMMARY " + json.dumps({key: report[key] for key in
          ("passed", "complete_conformance", "render_targets_passed", "summary_level", "probe_count", "maximum_absolute_error", "seconds")}), flush=True)
    if not render_targets_passed:
        raise RuntimeError("Native lens conformance failed; inspect saved report without changing reference equations")


if __name__ == "__main__":
    main()
