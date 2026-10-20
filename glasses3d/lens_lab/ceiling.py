"""Predeclared material search through the real Blender exporter and AR renderer.

All candidates are diagnostics on private copies. This is a bounded empirical
search, never a mathematical upper bound, product recipe, or acceptance gate.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import io
import itertools
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import traceback

import numpy as np
from PIL import Image

from . import score


def now():
    return datetime.now(timezone.utc).isoformat()


def worker_capacity(free_commit_bytes, cpu_count=None):
    return max(0, min(14, cpu_count or os.cpu_count() or 1,
                      math.floor((free_commit_bytes / 2**30 - 2) / 1.3)))


def design(*, sobol_power=7, include_texture=True):
    """No random seed selection: one unscrambled base-2 Sobol net plus fixed grid."""
    from scipy.stats import qmc
    if not 1 <= sobol_power <= 12:
        raise ValueError("sobol_power must be 1..12")
    neutral = {"base_rgb": [1., 1., 1.], "metallic": 0., "roughness": 0., "transmission": 1.,
               "ior": 1.5, "specular_factor": 1., "film_factor": 0., "film_ior": 1.3,
               "thickness_min_nm": 100., "thickness_max_nm": 400., "texture_profile": None}
    cases = [{**neutral, "family": "neutral_control"}]
    # Complete fixed factorial for the principal physical angle mechanism.
    for base, metallic, bulk_ior, film_ior, thickness in itertools.product(
            (1., .5), (0., .5), (1.5, 3., 6.), (1.3, 1.7, 2.1), (150., 300., 450., 600., 750., 900.)):
        cases.append({**neutral, "family": "uniform_film_grid", "base_rgb": [base] * 3,
                      "metallic": metallic, "roughness": .12, "film_factor": 1., "ior": bulk_ior,
                      "film_ior": film_ior, "thickness_max_nm": thickness,
                      "thickness_min_nm": min(100., thickness)})
    for row in qmc.Sobol(d=12, scramble=False).random_base2(sobol_power):
        minimum = 50. + 300. * row[10]
        cases.append({**neutral, "family": "uniform_sobol", "base_rgb": (.05 + .95 * row[:3]).tolist(),
                      "metallic": float(row[3]), "roughness": float(.02 + .48 * row[4]),
                      "transmission": float(row[5]), "ior": float(1. + 5. * row[6]),
                      "specular_factor": float(row[7]), "film_factor": float(row[8]),
                      "film_ior": float(1.1 + 1.4 * row[9]), "thickness_min_nm": float(minimum),
                      "thickness_max_nm": float(minimum + 100. + 650. * row[11])})
    if include_texture:
        # A fixed graph/UV-domain experiment; no photo-derived or hand-painted texture.
        for metallic, bulk_ior, film_ior, thickness_range, profile in itertools.product(
                (0., .25, .5), (1.5, 3., 6.), (1.3, 1.7),
                ((50., 350.), (100., 550.), (250., 750.), (450., 950.)),
                ("centre_peak", "edges_peak")):
            cases.append({**neutral, "family": "thickness_texture_grid", "base_rgb": [.65] * 3,
                          "metallic": metallic, "roughness": .12, "film_factor": 1., "ior": bulk_ior,
                          "film_ior": film_ior, "thickness_min_nm": thickness_range[0],
                          "thickness_max_nm": thickness_range[1], "texture_profile": profile})
    for index, case in enumerate(cases):
        case["id"] = f"candidate-{index:04d}"
    return {"schema": "g3d.lens_lab.ceiling_design/1", "label": score.DIAGNOSTIC,
            "created": now(), "sobol_power": sobol_power, "include_texture": include_texture,
            "selection": "All declared candidates run; no dropped rows, shuffled seeds or success-based stop.",
            "uniform_minimum_note": "Minimum is serialized but inactive without a thickness texture.",
            "ranges": {"base_rgb_linear": [.05, 1.], "metallic": [0., 1.], "roughness": [0., .5],
                       "transmission": [0., 1.], "bulk_ior": [1., 6.], "specular_factor": [0., 1.],
                       "film_factor": [0., 1.], "film_ior": [1.1, 2.5],
                       "thickness_nm": [50., 1100.],
                       "texture": "fixed symmetric linear U gradients on existing UV",
                       "texture_ranges_nm": [[50., 350.], [100., 550.], [250., 750.], [450., 950.]]},
            "refinement_policy": "No adaptive refinement in this design. Report best measured candidate and "
                                 "every candidate's status; finite search cannot prove impossibility.",
            "objective": "Equal mean regional CIE76 over front and angled view, 6 fixed photo regions.",
            "candidates": cases}


def material_code(candidate, lens_materials):
    """Complete per-candidate lens material state, including supported texture graph."""
    return """candidate = %r
for material_name in %r:
    material = bpy.data.materials[material_name]
    tree = material.node_tree
    shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    for input_name in ('Base Color', 'Metallic', 'Roughness', 'Transmission Weight', 'IOR',
                       'Specular IOR Level', 'Alpha', 'Thin Film Thickness', 'Thin Film IOR'):
        for link in list(shader.inputs[input_name].links):
            tree.links.remove(link)
    for node in tree.nodes:
        if node.type == 'GROUP' and node.node_tree and node.node_tree.name.lower().startswith(('gltf material output', 'gltf settings')):
            for input_name in ('Iridescence Factor', 'Iridescence Thickness Minimum'):
                if input_name in node.inputs:
                    for link in list(node.inputs[input_name].links):
                        tree.links.remove(link)
    shader.inputs['Base Color'].default_value = (*candidate['base_rgb'], 1.)
    shader.inputs['Alpha'].default_value = 1.
    shader.inputs['Metallic'].default_value = candidate['metallic']
    shader.inputs['Roughness'].default_value = candidate['roughness']
    shader.inputs['Transmission Weight'].default_value = candidate['transmission']
    shader.inputs['IOR'].default_value = candidate['ior']
    shader.inputs['Specular IOR Level'].default_value = candidate['specular_factor'] * .5
    g3d.lens_coating(material, factor=candidate['film_factor'], ior=candidate['film_ior'],
                     thickness_min_nm=candidate['thickness_min_nm'],
                     thickness_max_nm=candidate['thickness_max_nm'], tint=candidate['base_rgb'])
    profile = candidate['texture_profile']
    if profile:
        def node(name, kind):
            result = tree.nodes.get(name)
            if result is None:
                result = tree.nodes.new(kind)
                result.name = name
            return result
        image_node = node('LAB thickness image', 'ShaderNodeTexImage')
        image = image_node.image
        if image is None:
            image = bpy.data.images.new('LAB thickness diagnostic ' + material_name, width=128, height=8, alpha=True)
            image_node.image = image
        image.colorspace_settings.name = 'Non-Color'
        pixels = []
        for y in range(8):
            for x in range(128):
                edge = abs(2. * (x + .5) / 128. - 1.)
                value = 1. - edge if profile == 'centre_peak' else edge
                pixels.extend((value, value, value, 1.))
        image.pixels.foreach_set(pixels)
        image.update()
        image_node.interpolation = 'Linear'
        separate = node('LAB thickness green', 'ShaderNodeSeparateColor')
        mix = node('LAB thickness range', 'ShaderNodeMix')
        mix.data_type = 'FLOAT'
        minimum = node('LAB thickness minimum', 'ShaderNodeValue')
        maximum = node('LAB thickness maximum', 'ShaderNodeValue')
        minimum.outputs[0].default_value = candidate['thickness_min_nm']
        maximum.outputs[0].default_value = candidate['thickness_max_nm']
        control = next(n for n in tree.nodes if n.type == 'GROUP' and n.node_tree and
                       n.node_tree.name.lower().startswith(('gltf material output', 'gltf settings')))
        # Exporter recognizes precisely this FLOAT Mix + shared minimum Value graph.
        for socket in (separate.inputs[0], mix.inputs[0], mix.inputs[2], mix.inputs[3],
                       control.inputs['Iridescence Thickness Minimum'], shader.inputs['Thin Film Thickness']):
            for link in list(socket.links):
                tree.links.remove(link)
        tree.links.new(image_node.outputs['Color'], separate.inputs[0])
        tree.links.new(separate.outputs[1], mix.inputs[0])
        tree.links.new(minimum.outputs[0], mix.inputs[2])
        tree.links.new(minimum.outputs[0], control.inputs['Iridescence Thickness Minimum'])
        tree.links.new(maximum.outputs[0], mix.inputs[3])
        tree.links.new(mix.outputs[0], shader.inputs['Thin Film Thickness'])
print('LAB diagnostic material configured: ' + candidate['id'])
""" % (candidate, list(lens_materials))


def _glb(path):
    data = Path(path).read_bytes()
    length, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:
        raise ValueError("GLB JSON chunk missing")
    doc = json.loads(data[20:20 + length])
    offset = 20 + length
    binary = b""
    if offset + 8 <= len(data):
        binary_length, binary_type = struct.unpack_from("<II", data, offset)
        if binary_type == 0x004E4942:
            binary = data[offset + 8:offset + 8 + binary_length]
    return doc, binary


def glb_materials(path, names):
    doc, _ = _glb(path)
    return [material for material in doc["materials"] if material.get("name") in names]


def texture_receipts(path, names, profile):
    """Verify actual embedded green data and the UV attribute used by the shader."""
    if profile is None:
        return []
    doc, binary = _glb(path)
    receipts = []
    for material_index, material in enumerate(doc["materials"]):
        if material.get("name") not in names:
            continue
        texture_info = material["extensions"]["KHR_materials_iridescence"]["iridescenceThicknessTexture"]
        texcoord = texture_info.get("texCoord", 0)
        texture = doc["textures"][texture_info["index"]]
        image = doc["images"][texture["source"]]
        if "bufferView" not in image:
            raise ValueError("expected a self-contained embedded thickness texture")
        view = doc["bufferViews"][image["bufferView"]]
        offset = view.get("byteOffset", 0)
        blob = binary[offset:offset + view["byteLength"]]
        with Image.open(io.BytesIO(blob)) as decoded:
            pixels = np.asarray(decoded.convert("RGB"))
        green = pixels[..., 1]
        if int(green.max()) - int(green.min()) < 128:
            raise ValueError("exported thickness texture lacks the declared green-channel variation")
        mid = green[:, green.shape[1] // 2].mean()
        edges = green[:, [0, -1]].mean()
        if (profile == "centre_peak") != (mid > edges):
            raise ValueError("exported green-channel profile does not match declared gradient")
        primitives = [p for mesh in doc["meshes"] for p in mesh["primitives"]
                      if p.get("material") == material_index]
        if not primitives or any(f"TEXCOORD_{texcoord}" not in p["attributes"] for p in primitives):
            raise ValueError("thickness texture is missing the actual mesh UV attribute")
        receipts.append({"material": material["name"], "image_sha256": hashlib.sha256(blob).hexdigest(),
                         "size_px": [green.shape[1], green.shape[0]], "green_min": int(green.min()),
                         "green_max": int(green.max()), "green_centre_mean": float(mid),
                         "green_edges_mean": float(edges), "uv_attribute": f"TEXCOORD_{texcoord}",
                         "primitives_with_uv": len(primitives), "profile": profile})
    if not receipts:
        raise ValueError("no exported lens thickness texture checked")
    return receipts


def verify_export(candidate, materials):
    """Prevent a dropped authoring graph from masquerading as a renderer result."""
    if not materials:
        raise ValueError("no lens material in exported GLB")
    for material in materials:
        extensions = material.get("extensions", {})
        pbr = material.get("pbrMetallicRoughness", {})
        checks = [(pbr.get("baseColorFactor", [1., 1., 1., 1.])[:3], candidate["base_rgb"], "base"),
                  (pbr.get("metallicFactor", 1.), candidate["metallic"], "metallic"),
                  (pbr.get("roughnessFactor", 1.), candidate["roughness"], "roughness"),
                  (extensions.get("KHR_materials_transmission", {}).get("transmissionFactor", 0.),
                   candidate["transmission"], "transmission"),
                  (extensions.get("KHR_materials_ior", {}).get("ior", 1.5), candidate["ior"], "ior"),
                  (extensions.get("KHR_materials_specular", {}).get("specularFactor", 1.),
                   candidate["specular_factor"], "specular")]
        film = extensions.get("KHR_materials_iridescence", {})
        checks.append((film.get("iridescenceFactor", 0.), candidate["film_factor"], "film factor"))
        if candidate["film_factor"] > 0:
            checks += [(film.get("iridescenceIor", 1.3), candidate["film_ior"], "film ior"),
                       (film.get("iridescenceThicknessMinimum", 100.), candidate["thickness_min_nm"], "film min"),
                       (film.get("iridescenceThicknessMaximum", 400.), candidate["thickness_max_nm"], "film max")]
        for actual, expected, name in checks:
            aa = actual if isinstance(actual, list) else [actual]
            ee = expected if isinstance(expected, list) else [expected]
            if len(aa) != len(ee) or any(not math.isclose(a, e, abs_tol=1e-4, rel_tol=1e-5)
                                       for a, e in zip(aa, ee)):
                raise ValueError(f"export changed {name}: {actual!r} != {expected!r}")
        if bool(film.get("iridescenceThicknessTexture")) != bool(candidate["texture_profile"]):
            raise ValueError("thickness texture did not survive real export")
    return True


def _worker(args):
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    from glasses3d.agent import procs
    from . import harness
    procs.opt_out_self()
    output, basis_dir, index, cases = args
    output = Path(output)
    trial_dir = output / "workers" / f"worker-{index:02d}"
    manifest = harness.init_trial(trial_dir)
    rows = []
    with harness.LabSession(trial_dir) as session:
        for candidate in cases:
            case_dir = output / "candidates" / candidate["id"]
            case_dir.mkdir(parents=True, exist_ok=False)
            record = {"candidate": candidate, "started": now(), "worker": index, "worker_pid": os.getpid(),
                      "label": score.DIAGNOSTIC}
            started = time.monotonic()
            score.write_json(case_dir / "candidate.json", candidate)
            score.write_json(case_dir / "result.json", {**record, "status": "running"})
            try:
                result = session.call("execute_blender_code", {
                    "code": material_code(candidate, manifest["lens_materials"]),
                    "label": "LAB diagnostic " + candidate["id"], "soft_timeout_s": 60})
                if result["result"].get("status") != "ok":
                    raise RuntimeError(f"candidate material refused: {result['result']}")
                exported = session.call("export_glb", {})["result"]
                if exported.get("status") != "exported":
                    raise RuntimeError(f"candidate export failed: {exported}")
                model = Path(manifest["job_dir"]) / exported["path"]
                materials = glb_materials(model, manifest["lens_materials"])
                verify_export(candidate, materials)
                score.write_json(case_dir / "export-materials.json", materials)
                textures = texture_receipts(model, manifest["lens_materials"], candidate["texture_profile"])
                score.write_json(case_dir / "export-textures.json", textures)
                measured = score.score_glb(model, case_dir / "score", basis_dir=basis_dir,
                                          trial_label=score.DIAGNOSTIC + " | " + candidate["id"])
                record.update(status="measured", score=measured["mean_regional_delta_E76"],
                              front_pattern=measured["front_pattern"], export=exported,
                              score_file=str(case_dir / "score/score.json"),
                              glb=str(case_dir / "score/render/lab-model.glb"),
                              material_verified=True, texture_receipts=textures)
            except Exception as error:
                record.update(status="failed", error=str(error), traceback=traceback.format_exc())
            record.update(finished=now(), seconds=time.monotonic() - started)
            score.write_json(case_dir / "result.json", record)
            rows.append(record)
            print(f"{candidate['id']} {record['status']} score={record.get('score')} "
                  f"seconds={record['seconds']:.1f}", flush=True)
    return rows


def write_summary(output, result):
    output = Path(output)
    rows = result["candidates"]
    best = result["best"]
    lines = [f"**{score.DIAGNOSTIC}**", "",
             f"Measured {result['measured']} of {result['all_candidates']} predeclared candidates; "
             f"{result['failed']} failed and remain in results.json. Actual API spend: $0.", ""]
    if best:
        baseline = score.read_json(best["score_file"])["run4_mean_regional_delta_E76"]
        lines += [f"Best measured regional error: **{best['score']:.4f} ΔE76**, compared with "
                  f"accepted run 4's **{baseline:.4f}** on the same six fixed photo regions.",
                  f"Best candidate: `{best['candidate']['id']}` ({best['candidate']['family']}).", "",
                  f"![{score.DIAGNOSTIC}](candidates/{best['candidate']['id']}/score/comparison.png)", ""]
    lines += [f"{result['front_sign_pattern_count']} candidates have front median a* < 0 in the centre, "
              "a* > 0 and b* < 0 on both sides. This describes green/purple signs, not a visual match "
              "or a production quality gate.", "",
              "| Design family | Declared | Measured | Best six-region ΔE76 |",
              "|---|---:|---:|---:|"]
    for family in sorted({row["candidate"]["family"] for row in rows}):
        subset = [row for row in rows if row["candidate"]["family"] == family]
        measured = [row for row in subset if row["status"] == "measured"]
        value = f"{min(row['score'] for row in measured):.4f}" if measured else "unavailable"
        lines.append(f"| {family} | {len(subset)} | {len(measured)} | {value} |")
    lines += ["", result["limitation"], "",
              "Each candidate uses the real guarded Blender lens-material tool, real exporter and pinned AR renderer. "
              "The photo lens masks and horizontal thirds were frozen before the search; candidate images never "
              "select scoring pixels. Both views have equal weight. The source run and camera basis are unchanged.",
              "Thickness textures are analytic U gradients on the existing mesh UV, not photo-derived paint. "
              "Each textured export is checked for the actual embedded green-channel gradient and mesh UV attribute. "
              "All parameter states, scalar/texture export checks, raw captures, diagnostic labelled comparisons, "
              "process receipts and scores are retained under candidates/ and workers/.", ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def run(output_dir, *, basis_dir=None, sobol_power=7, include_texture=True, workers=None):
    output = score.output_path(output_dir)
    from . import harness
    from glasses3d.g3d_host.resources import free_commit_bytes
    from glasses3d.agent import procs
    harness.ensure_heavy_allowed()
    memory = free_commit_bytes()
    capacity = worker_capacity(memory)
    count = capacity if workers is None else workers
    if count < 1 or count > capacity:
        raise ValueError(f"workers must be 1..{capacity} under current free commit")
    basis = score.load_basis(basis_dir)
    plan = design(sobol_power=sobol_power, include_texture=include_texture)
    count = min(len(plan["candidates"]), count)
    output.mkdir(parents=True, exist_ok=False)
    score.write_json(output / "design.json", plan)  # committed to local evidence before first render
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    start = time.monotonic()
    process = {"started": now(), "pid": os.getpid(), "argv": sys.argv, "free_commit_bytes": memory, "workers": count,
               "blas_threads": 1, "ecoqos_opt_out": procs.opt_out_self(), "design_sha256": score.sha256(output / "design.json"),
               "basis_sha256": basis["basis_sha256"], "all_candidates": len(plan["candidates"]),
               "actual_api_spend_usd": 0}
    score.write_json(output / "process.json", process)
    args = [(str(output), basis["directory"], index, plan["candidates"][index::count]) for index in range(count)]
    rows = []
    with ProcessPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(_worker, arg) for arg in args]
        for future in as_completed(futures):
            rows.extend(future.result())
    rows.sort(key=lambda row: row["candidate"]["id"])
    measured = sorted((row for row in rows if row["status"] == "measured"), key=lambda row: row["score"])
    best = measured[0] if measured else None
    patterns = [row for row in measured if row["front_pattern"]["purple_sides_green_centre_signs"]]
    # Every declared candidate gets one status; failures remain failures in the report.
    if len(rows) != len(plan["candidates"]):
        raise RuntimeError("search omitted a declared candidate")
    for row in measured[:8]:
        folder = Path(row["score_file"]).parent
        score.score_capture(folder / "render/capture", folder, basis_dir=basis["directory"],
                            trial_label=score.DIAGNOSTIC + " | " + row["candidate"]["id"])
    if patterns and patterns[0] not in measured[:8]:
        row = patterns[0]
        folder = Path(row["score_file"]).parent
        score.score_capture(folder / "render/capture", folder, basis_dir=basis["directory"],
                            trial_label=score.DIAGNOSTIC + " | " + row["candidate"]["id"])
    result = {"schema": "g3d.lens_lab.ceiling_result/1", "label": score.DIAGNOSTIC,
              "design_sha256": process["design_sha256"], "basis_sha256": basis["basis_sha256"],
              "all_candidates": len(rows), "measured": len(measured), "failed": len(rows) - len(measured),
              "best": best, "best_front_sign_pattern": patterns[0] if patterns else None,
              "front_sign_pattern_count": len(patterns), "candidates": rows,
              "limitation": "Best observed only within this declared finite design and fixed room/cameras. "
                            "No mathematical maximum, product recipe, visual acceptance or impossibility proof.",
              "seconds": time.monotonic() - start, "actual_api_spend_usd": 0}
    score.write_json(output / "results.json", result)
    write_summary(output, result)
    process.update(finished=now(), seconds=result["seconds"], exit=0 if not result["failed"] else 1)
    score.write_json(output / "process.json", process)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--basis-dir", type=Path)
    parser.add_argument("--sobol-power", type=int, default=7)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--no-texture", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir, basis_dir=args.basis_dir, sobol_power=args.sobol_power,
                 include_texture=not args.no_texture, workers=args.workers)
    print(json.dumps({key: result[key] for key in ("all_candidates", "measured", "failed",
                                                  "front_sign_pattern_count", "seconds")}, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
