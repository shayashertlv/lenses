"""Read-only GLB part probes with exact triangle multiset correspondence.

No welding, nearest-surface projection, remeshing, or semantic assignment.
Hashes use decoded float64 world positions, canonical corner order, and SHA256.
Duplicate geometric triangles are counted but never given a fabricated unique map.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

from reconstruction.partition_glb import inspect_partition_source, _view, _DTYPES, _WIDTHS
from qa.provider_comparison import font, put_image

ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ("positions", "winding", "uv", "attributes")
CHUNK = 16384


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def accessor(doc, binary, index):
    acc = doc["accessors"][index]
    count, width = acc["count"], _WIDTHS[acc["type"]]
    dtype = np.dtype(_DTYPES[acc["componentType"]])
    values = (_view(doc, binary, acc["bufferView"], acc.get("byteOffset", 0), count, dtype, width).copy()
              if "bufferView" in acc else np.zeros((count, width), dtype=dtype))
    if "sparse" in acc:
        sparse = acc["sparse"]
        idx, val = sparse["indices"], sparse["values"]
        ids = _view(doc, binary, idx["bufferView"], idx.get("byteOffset", 0), sparse["count"],
                    np.dtype(_DTYPES[idx["componentType"]]), 1, sparse=True).ravel()
        values[ids] = _view(doc, binary, val["bufferView"], val.get("byteOffset", 0), sparse["count"], dtype, width, sparse=True)
    values = values.astype("<f8")
    if acc.get("normalized"):
        bound = np.iinfo(dtype).max
        values = np.maximum(values / bound, -1) if dtype.kind == "i" else values / bound
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite attribute cannot be certified")
    values[values == 0] = 0  # canonicalize signed zero only; no rounding
    return values


def row_hashes(values, prefix=b""):
    packed = np.ascontiguousarray(values, dtype="<f8").reshape((len(values), -1))
    raw = packed.view(np.uint8).reshape((len(values), -1))
    return np.frombuffer(b"".join(hashlib.sha256(prefix + row.tobytes()).digest() for row in raw), dtype="V32")


def inventory(path: Path, cache: Path):
    raw = path.read_bytes()
    digest = sha(raw)
    cache.mkdir(parents=True, exist_ok=True)
    meta_path = cache / f"{digest}.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("schema_version") == 1 and all((cache / f"{digest}.{key}.npy").exists() for key in CHANNELS):
            return meta, {key: np.load(cache / f"{digest}.{key}.npy", mmap_mode="r") for key in CHANNELS}
    source = inspect_partition_source(raw)
    doc, binary, instances = source["document"], source["binary"], source["instances"]
    total = sum(len(part["indices"]) for part in instances)
    arrays = {key: np.lib.format.open_memmap(cache / f"{digest}.{key}.npy", mode="w+", dtype="V32", shape=(total,)) for key in CHANNELS}
    meta = {"schema_version": 1, "path": str(path.resolve()), "sha256": digest, "file_bytes": len(raw),
            "face_count": total, "selected_scene": source["selected_scene"], "parts": [],
            "materials": doc.get("materials", []), "nodes": doc.get("nodes", []),
            "mesh_count": len(doc.get("meshes", [])), "images": len(doc.get("images", [])),
            "extensions_used": doc.get("extensionsUsed", []),
            "attribute_comparison": "Decoded numeric local vertex attributes; POSITION compared in world space. No rounding; signed zero canonicalized. Normals remain local values.",
            "hash_method": "SHA256 per triangle over little-endian float64 numeric values. Unordered world-position corners except winding channel; multiplicities retained."}
    offset = 0
    bounds_min, bounds_max = np.full(3, np.inf), np.full(3, -np.inf)
    for part_id, part in enumerate(instances):
        binding, primitive = part["source_binding"], part["primitive"]
        attrs = {name: accessor(doc, binary, index) for name, index in primitive["attributes"].items() if name != "POSITION"}
        names, uv_names = sorted(attrs), sorted(name for name in attrs if name.startswith("TEXCOORD_"))
        prefix = json.dumps([(name, attrs[name].shape[1]) for name in names], separators=(",", ":")).encode()
        uv_prefix = json.dumps([(name, attrs[name].shape[1]) for name in uv_names], separators=(",", ":")).encode()
        matrix, indices = part["world_matrix"], part["indices"]
        vertices = part["positions"] @ matrix[:3, :3].T + matrix[:3, 3]
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        area, zero_area = 0., 0
        for begin in range(0, len(indices), CHUNK):
            ids = indices[begin:begin + CHUNK]
            triangles = np.asarray(vertices[ids], dtype="<f8")
            triangles[triangles == 0] = 0
            order = np.lexsort((triangles[:, :, 2], triangles[:, :, 1], triangles[:, :, 0]), axis=1)
            canonical = np.take_along_axis(triangles, order[:, :, None], axis=1)
            sl = slice(offset + begin, offset + begin + len(ids))
            arrays["positions"][sl] = row_hashes(canonical)
            cyclic = (order[:, :1] + np.arange(3)[None, :]) % 3
            arrays["winding"][sl] = row_hashes(np.take_along_axis(triangles, cyclic[:, :, None], axis=1))
            corners = {name: np.take_along_axis(value[ids], order[:, :, None], axis=1) for name, value in attrs.items()}
            arrays["uv"][sl] = row_hashes(np.concatenate([canonical] + [corners[name] for name in uv_names], axis=2), uv_prefix)
            arrays["attributes"][sl] = row_hashes(np.concatenate([canonical] + [corners[name] for name in names], axis=2), prefix)
            lo = np.minimum(lo, triangles.min(axis=(0, 1)))
            hi = np.maximum(hi, triangles.max(axis=(0, 1)))
            cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
            areas = np.linalg.norm(cross, axis=1) * .5
            area += float(areas.sum())
            zero_area += int(np.count_nonzero(areas == 0))
        node, mesh = doc["nodes"][binding["node_index"]], doc["meshes"][binding["mesh_index"]]
        meta["parts"].append({"id": f"part-{part_id:03d}", "index": part_id, "binding": binding,
                              "node_name": node.get("name"), "mesh_name": mesh.get("name"),
                              "node_extras": node.get("extras"), "primitive_extras": primitive.get("extras"),
                              "material_index": primitive.get("material"), "face_start": offset,
                              "face_count": len(indices), "vertex_count": len(vertices), "world_bounds": {"min": lo.tolist(), "max": hi.tolist()},
                              "world_area": area, "zero_area_faces": zero_area,
                              "attributes": {name: {k: doc["accessors"][ai].get(k) for k in ("type", "componentType", "normalized", "count")} for name, ai in primitive["attributes"].items()}})
        bounds_min, bounds_max = np.minimum(bounds_min, lo), np.maximum(bounds_max, hi)
        offset += len(indices)
    meta["world_bounds"] = {"min": bounds_min.tolist(), "max": bounds_max.tolist()}
    for values in arrays.values():
        values.flush()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta, arrays


def compare_hashes(original, candidate, output, key):
    original_order = np.argsort(original, kind="stable")
    sorted_original = original[original_order]
    left = np.searchsorted(sorted_original, candidate, side="left")
    right = np.searchsorted(sorted_original, candidate, side="right")
    found = right > left
    unique = right - left == 1
    mapping = np.full(len(candidate), -1, dtype=np.int64)
    mapping[found & ~unique] = -2
    mapping[unique] = original_order[left[unique]]
    orig_keys, orig_counts = np.unique(original, return_counts=True)
    cand_keys, cand_counts = np.unique(candidate, return_counts=True)
    _, oi, ci = np.intersect1d(orig_keys, cand_keys, assume_unique=True, return_indices=True)
    matched_occurrences = int(np.minimum(orig_counts[oi], cand_counts[ci]).sum())
    result = {"matched_occurrences": matched_occurrences, "original_faces": len(original), "candidate_faces": len(candidate),
              "original_missing_occurrences": len(original) - matched_occurrences,
              "candidate_extra_occurrences": len(candidate) - matched_occurrences,
              "exact_multiset_equal": len(original) == len(candidate) == matched_occurrences,
              "candidate_faces_with_unique_original_key": int(unique.sum()),
              "candidate_faces_with_ambiguous_original_key": int((found & ~unique).sum()),
              "original_duplicate_key_occurrences": int(orig_counts[orig_counts > 1].sum()),
              "candidate_duplicate_key_occurrences": int(cand_counts[cand_counts > 1].sum()),
              "same_face_order": bool(len(original) == len(candidate) and np.array_equal(original, candidate))}
    if key == "positions":
        filename = "candidate-to-original-face.npy"
        np.save(output / filename, mapping)
        result["mapping_file"] = filename
        result["mapping_semantics"] = "Candidate global face ordinal -> unique original global face ordinal; -1 absent, -2 ambiguous duplicate geometry. Repeated candidate occurrences can refer to one original: multiset audit must also pass."
    return result, mapping


def recentered_correspondence(original_path, candidate_path, output):
    """Test a specified float32 recentering transform, never nearest geometry.

    A match means the original corners, transformed into the candidate node's
    local coordinates then rounded to float32, exactly equal its local corners.
    It does NOT mean unchanged world coordinates. Require complete bijection and
    inspect the separately measured corner error before transferring labels.
    """
    original_raw, candidate_raw = original_path.read_bytes(), candidate_path.read_bytes()
    original, candidate = inspect_partition_source(original_raw), inspect_partition_source(candidate_raw)
    total = sum(len(part["indices"]) for part in original["instances"])
    triangles = np.empty((total, 3, 3), dtype="<f8")
    start = 0
    for part in original["instances"]:
        matrix = part["world_matrix"]
        vertices = part["positions"] @ matrix[:3, :3].T + matrix[:3, 3]
        triangles[start:start + len(part["indices"])] = vertices[part["indices"]]
        start += len(part["indices"])
    centers = triangles.mean(axis=1)
    width = float(np.ptp(triangles.reshape((-1, 3)), axis=0).max())
    bound_tolerance = width * 1e-6
    candidate_count = sum(len(part["indices"]) for part in candidate["instances"])
    mapping = np.full(candidate_count, -1, dtype=np.int64)
    residuals = np.full(candidate_count, np.nan, dtype=np.float64)
    labels = np.full(total, -1, dtype=np.int32)
    winding_matches = 0
    rows, offset = [], 0
    for part_id, part in enumerate(candidate["instances"]):
        matrix, ids = part["world_matrix"], part["indices"]
        actual_local = np.asarray(part["positions"][ids], dtype="<f8")
        actual_world = actual_local @ matrix[:3, :3].T + matrix[:3, 3]
        lo, hi = actual_world.min(axis=(0, 1)), actual_world.max(axis=(0, 1))
        eligible = np.flatnonzero(np.all((centers >= lo - bound_tolerance) & (centers <= hi + bound_tolerance), axis=1))
        inverse_linear = np.linalg.inv(matrix[:3, :3])
        predicted = ((triangles[eligible] - matrix[:3, 3]) @ inverse_linear.T).astype("<f4").astype("<f8")
        predicted[predicted == 0] = 0
        actual_local[actual_local == 0] = 0
        def canonical(values):
            order = np.lexsort((values[:, :, 2], values[:, :, 1], values[:, :, 0]), axis=1)
            return np.take_along_axis(values, order[:, :, None], axis=1), order
        expected_canonical, expected_order = canonical(predicted)
        actual_canonical, actual_order = canonical(actual_local)
        expected_hash = row_hashes(expected_canonical)
        actual_hash = row_hashes(actual_canonical)
        sorted_indices = np.argsort(expected_hash, kind="stable")
        sorted_hash = expected_hash[sorted_indices]
        left, right = np.searchsorted(sorted_hash, actual_hash, "left"), np.searchsorted(sorted_hash, actual_hash, "right")
        unique, found = right - left == 1, right > left
        local_map = np.full(len(ids), -1, dtype=np.int64)
        local_map[found & ~unique] = -2
        local_map[unique] = eligible[sorted_indices[left[unique]]]
        mapping[offset:offset + len(ids)] = local_map
        matched_target = np.flatnonzero(unique)
        matched_expected = sorted_indices[left[unique]]
        original_corners = np.take_along_axis(triangles[local_map[unique]], expected_order[matched_expected, :, None], axis=1)
        candidate_corners = np.take_along_axis(actual_world[unique], actual_order[unique, :, None], axis=1)
        error = np.linalg.norm(original_corners - candidate_corners, axis=2).max(axis=1)
        residuals[offset + matched_target] = error
        expected_cycle = (expected_order[matched_expected, :1] + np.arange(3)[None, :]) % 3
        actual_cycle = (actual_order[unique, :1] + np.arange(3)[None, :]) % 3
        winding_matches += int(np.all(np.take_along_axis(predicted[matched_expected], expected_cycle[:, :, None], axis=1) ==
                                      np.take_along_axis(actual_local[unique], actual_cycle[:, :, None], axis=1), axis=(1, 2)).sum())
        labels[local_map[unique]] = part_id
        row = {"part_index": part_id, "binding": part["source_binding"], "face_start": offset, "face_count": len(ids),
               "unique_predicted_local_matches": int(unique.sum()), "ambiguous_predicted_local_matches": int((found & ~unique).sum()),
               "unmatched_faces": int((~found).sum()), "original_bbox_candidates": len(eligible),
               "maximum_corner_error_world": float(error.max()) if len(error) else None,
               "p95_corner_error_world": float(np.percentile(error, 95)) if len(error) else None}
        rows.append(row)
        print(f"Recenter {part_id}: {int(unique.sum())}/{len(ids)} uniquely matched", flush=True)
        offset += len(ids)
    accepted = mapping >= 0
    mapped_ids, uses = np.unique(mapping[accepted], return_counts=True)
    repeated = mapped_ids[uses > 1]
    labels[repeated] = -2
    error = residuals[np.isfinite(residuals)]
    result = {"schema_version": 1, "original_sha256": sha(original_raw), "candidate_sha256": sha(candidate_raw),
              "method": "Exact hashes of original world triangles transformed to each candidate node local frame, then rounded once to float32. Bounding boxes only prune candidates; no nearest points/projection/rounding grid is used.",
              "world_coordinates_exactly_preserved": False,
              "original_faces": total, "candidate_faces": candidate_count, "uniquely_matched_candidate_faces": int(accepted.sum()),
              "mapped_original_faces": len(mapped_ids), "repeated_original_faces": len(repeated),
              "complete_bijection": bool(accepted.all() and len(mapped_ids) == total == candidate_count and not len(repeated)),
              "winding_matches": winding_matches,
              "maximum_corner_error_world": float(error.max()) if len(error) else None,
              "p95_corner_error_world": float(np.percentile(error, 95)) if len(error) else None,
              "maximum_corner_error_relative_to_extent": float(error.max() / width) if len(error) and width else None,
              "bbox_candidate_tolerance": bound_tolerance,
              "candidate_mapping_file": "candidate-to-original-recentered-face.npy", "original_part_labels_file": "original-face-part-labels.npy",
              "mapping_semantics": "Mapping -1=unmatched,-2=ambiguous. Original part labels -1=unassigned,-2=multiply-used original face. Incomplete maps are proposals only.",
              "parts": rows}
    np.save(output / result["candidate_mapping_file"], mapping)
    np.save(output / result["original_part_labels_file"], labels)
    (output / "recenter-correspondence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def bounded_correspondence(original_path, candidate_path, output, relative_tolerance=1e-6):
    """Approximate correspondence with explicit corner bounds and bijection audit.

    The nearest centroid only proposes a triangle. Its THREE corresponding
    corners must fit, no second centroid may lie inside tolerance, and a global
    bijection is independently required. No mesh is moved or projected.
    """
    from scipy.spatial import cKDTree
    original_raw, candidate_raw = original_path.read_bytes(), candidate_path.read_bytes()
    original, candidate = inspect_partition_source(original_raw), inspect_partition_source(candidate_raw)
    total = sum(len(part["indices"]) for part in original["instances"])
    triangles = np.empty((total, 3, 3), dtype="<f8")
    start = 0
    for part in original["instances"]:
        matrix = part["world_matrix"]
        points = part["positions"] @ matrix[:3, :3].T + matrix[:3, 3]
        triangles[start:start + len(part["indices"])], start = points[part["indices"]], start + len(part["indices"])
    width = float(np.ptp(triangles.reshape((-1, 3)), axis=0).max())
    tolerance = width * relative_tolerance
    tree = cKDTree(triangles.mean(axis=1))
    candidate_count = sum(len(part["indices"]) for part in candidate["instances"])
    mapping = np.full(candidate_count, -1, dtype=np.int64)
    residuals = np.full(candidate_count, np.nan, dtype=np.float64)
    corner_permutations = np.full(candidate_count, -1, dtype=np.int8)
    labels = np.full(total, -1, dtype=np.int32)
    rows, offset = [], 0
    permutations = np.array([[0, 1, 2], [1, 2, 0], [2, 0, 1], [0, 2, 1], [2, 1, 0], [1, 0, 2]])
    for part_id, part in enumerate(candidate["instances"]):
        matrix, ids = part["world_matrix"], part["indices"]
        points = part["positions"] @ matrix[:3, :3].T + matrix[:3, 3]
        ambiguous = 0
        for begin in range(0, len(ids), CHUNK):
            actual = points[ids[begin:begin + CHUNK]]
            distances, neighbors = tree.query(actual.mean(axis=1), k=2, workers=1)
            expected = triangles[neighbors[:, 0]]
            errors = np.stack([np.linalg.norm(expected[:, order] - actual, axis=2).max(axis=1) for order in permutations], axis=1)
            best = errors.argmin(axis=1)
            corner_error = errors[np.arange(len(actual)), best]
            close_competitor = distances[:, 1] <= tolerance
            accepted = (distances[:, 0] <= tolerance) & (corner_error <= tolerance) & ~close_competitor
            sl = slice(offset + begin, offset + begin + len(actual))
            row_map = np.where(accepted, neighbors[:, 0], -1)
            row_map[close_competitor] = -2
            mapping[sl] = row_map
            residuals[sl] = np.where(accepted, corner_error, np.nan)
            corner_permutations[sl] = np.where(accepted, best, -1)
            labels[row_map[accepted]] = part_id
            ambiguous += int(close_competitor.sum())
        section = mapping[offset:offset + len(ids)]
        part_errors = residuals[offset:offset + len(ids)]
        part_errors = part_errors[np.isfinite(part_errors)]
        rows.append({"part_index": part_id, "binding": part["source_binding"], "face_start": offset, "face_count": len(ids),
                     "bounded_unique_matches": int((section >= 0).sum()), "close_centroid_ambiguities": ambiguous,
                     "maximum_corner_error_world": float(part_errors.max()) if len(part_errors) else None,
                     "p95_corner_error_world": float(np.percentile(part_errors, 95)) if len(part_errors) else None})
        print(f"Bounded {part_id}: {int((section >= 0).sum())}/{len(ids)} matched", flush=True)
        offset += len(ids)
    accepted = mapping >= 0
    mapped_ids, uses = np.unique(mapping[accepted], return_counts=True)
    repeated = mapped_ids[uses > 1]
    labels[repeated] = -2
    errors = residuals[np.isfinite(residuals)]
    result = {"schema_version": 1, "original_sha256": sha(original_raw), "candidate_sha256": sha(candidate_raw),
              "method": "Nearest triangle centroid proposes correspondence; all three corners checked under all six permutations. A second centroid within tolerance rejects the proposal. Global source occurrence counts independently test bijection. No projection or geometry edits.",
              "exact_world_geometry_preservation": False, "approximate_correspondence": True,
              "relative_tolerance": relative_tolerance, "world_tolerance": tolerance,
              "original_faces": total, "candidate_faces": candidate_count, "bounded_matched_candidate_faces": int(accepted.sum()),
              "mapped_original_faces": len(mapped_ids), "repeated_original_faces": len(repeated),
              "unmatched_candidate_faces": int((mapping == -1).sum()), "ambiguous_candidate_faces": int((mapping == -2).sum()),
              "complete_bijection": bool(accepted.all() and len(mapped_ids) == total == candidate_count and not len(repeated)),
              "reversed_winding_faces": int((corner_permutations >= 3).sum()),
              "maximum_corner_error_world": float(errors.max()) if len(errors) else None,
              "p95_corner_error_world": float(np.percentile(errors, 95)) if len(errors) else None,
              "maximum_corner_error_relative_to_extent": float(errors.max() / width) if len(errors) and width else None,
              "candidate_mapping_file": "candidate-to-original-bounded-face.npy",
              "original_part_labels_file": "original-face-part-labels-bounded.npy",
              "corner_permutations_file": "candidate-to-original-corner-permutations.npy",
              "corner_permutations": permutations.tolist(),
              "mapping_semantics": "Mapping -1=unmatched,-2=centroid ambiguity. Original labels -1=unassigned,-2=multiply-used source face. Incomplete maps cannot certify whole-model label transfer.",
              "parts": rows}
    np.save(output / result["candidate_mapping_file"], mapping)
    np.save(output / result["original_part_labels_file"], labels)
    np.save(output / result["corner_permutations_file"], corner_permutations)
    (output / "bounded-correspondence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def render_manifest(candidate, args, output):
    products = {}
    inputs = ROOT / "data/provider-comparison-v1/inputs.json"
    if inputs.exists():
        source = json.loads(inputs.read_text(encoding="utf-8"))
        products[args.product] = {"photos": source.get("products", {}).get(args.product, {}).get("photos", {})}
    base = {"product": args.product, "path": str(args.candidate.resolve()), "model_sha256": candidate["sha256"],
            "rotation_degrees": args.rotation, "part_bindings": [part["binding"] for part in candidate["parts"]]}
    cases = [{**base, "id": args.id, "provider": f"{args.id} / all parts"}]
    for part in candidate["parts"][:args.max_parts]:
        cases.append({**base, "id": f"{args.id}-{part['id']}", "provider": f"{part['id']} / {part['face_count']:,} faces",
                      "visible_bindings": [part["binding"]]})
    manifest = {"schema_version": 1, "cases": cases, "products": products,
                "modes": ["primitives"], "backgrounds": ["light"], "views": ["front", "back", "left", "right", "angled"],
                "width": 640, "height": 480}
    path = output / "render-manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def part_overviews(output):
    """Small pages keep individual isolated parts readable in image inspection."""
    directory = output / "renders"
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    files = []
    for start in range(0, len(report["cases"]), 8):
        cases = report["cases"][start:start + 8]
        sheet = Image.new("RGB", (1280, 70 + ((len(cases) + 1) // 2) * 300), "#eef1f5")
        draw = ImageDraw.Draw(sheet)
        draw.text((18, 14), f"{output.name} | parts {start}–{start + len(cases) - 1}", font=font(26), fill="#202d3d")
        for index, case in enumerate(cases):
            x, y = index % 2 * 640, 70 + index // 2 * 300
            title = f"{case['id']} | {case.get('visible_triangles', case.get('triangles', 0)):,} faces"
            draw.text((x + 12, y + 3), title, font=font(17), fill="#202d3d")
            for column, view in enumerate(("front", "angled")):
                render = next((r for r in case["renders"] if r["view"] == view), None)
                if render:
                    put_image(sheet, directory / render["filename"], (x + column * 320, y + 34, x + (column + 1) * 320, y + 278))
        filename = f"part-overview-{start // 8 + 1:02d}.png"
        sheet.save(directory / filename)
        files.append(str(directory / filename))
    (output / "part-overviews.json").write_text(json.dumps({"files": files}, indent=2) + "\n", encoding="utf-8")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / "data/part-probes-v1/audit-cache")
    parser.add_argument("--id", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--rotation", type=float, nargs=3, default=[0, -90, 0])
    parser.add_argument("--max-parts", type=int, default=32)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--recenter-only", action="store_true", help="Test explicitly rounded recentering correspondence, separate from unchanged geometry")
    parser.add_argument("--bounded-only", action="store_true", help="Measure approximate correspondence under explicit corner tolerance, never exact identity")
    parser.add_argument("--sheets-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.sheets_only:
        print(json.dumps({"files": part_overviews(output)}, indent=2))
        return
    if args.recenter_only or args.bounded_only:
        method = bounded_correspondence if args.bounded_only else recentered_correspondence
        report = method(args.original.resolve(), args.candidate.resolve(), output)
        print(json.dumps({key: value for key, value in report.items() if key != "parts"}, indent=2))
        return
    original, original_hashes = inventory(args.original.resolve(), args.cache.resolve())
    candidate, candidate_hashes = inventory(args.candidate.resolve(), args.cache.resolve())
    report = {"schema_version": 1, "id": args.id, "product": args.product, "status": "audited", "semantic_verdict": "unmeasured",
              "original": original, "candidate": candidate, "comparisons": {},
              "scope": "No geometry or attribute mutation. Exact means decoded numeric identity after scene transforms, ignoring triangle/corner ordering as stated. This is not a nearest-surface approximation or semantic correctness test."}
    for key in CHANNELS:
        print(f"Comparing {args.id}: {key}", flush=True)
        comparison, mapping = compare_hashes(original_hashes[key], candidate_hashes[key], output, key)
        report["comparisons"][key] = comparison
        if key == "positions":
            for part in candidate["parts"]:
                values = mapping[part["face_start"]:part["face_start"] + part["face_count"]]
                part["exact_original_matches"] = int((values >= 0).sum())
                part["unmatched_faces"] = int((values == -1).sum())
                part["ambiguous_duplicate_faces"] = int((values == -2).sum())
    report["preserved_geometry_and_local_attributes"] = all(report["comparisons"][key]["exact_multiset_equal"] for key in CHANNELS)
    manifest = render_manifest(candidate, args, output)
    report["render_manifest"] = str(manifest)
    (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"audit": str(output / "audit.json"), "parts": len(candidate["parts"]), "triangles": candidate["face_count"],
                      "comparison": report["comparisons"], "render_manifest": str(manifest)}, indent=2), flush=True)
    if args.render:
        subprocess.run([sys.executable, "-m", "qa.provider_comparison", "--manifest", str(manifest), "--output", str(output / "renders")], cwd=ROOT, check=True)
        part_overviews(output)


if __name__ == "__main__":
    main()
