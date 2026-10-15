"""$0 proof of frozen camera conversion and photo/worn colour evidence; immutable input GLBs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion, distance_transform_edt

from glasses3d.agent import procs
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.tools import look, arview, _image
from glasses3d.g3d_host import blender, blender_camera as BC, compare as C, glb
from glasses3d.g3d_host.camera import project_mm

REPO = Path(__file__).resolve().parents[3]
BASIS = REPO / "glasses3d/data/assembly/m5fix1-camera-roll-a"
ACCEPTED = BASIS / "work/exports/accepted.glb"
RUN3 = REPO / "glasses3d/data/assembly/m5fix3-copy-a/jobs/run-3-re-export-proof-copy-269cb90e/work/run3-reexport.glb"
# Integrate the same native pixel areas on a fixed 4x4 subpixel grid. Four-sample MSAA alone has
# a large exactly-half-covered plateau; thresholding that plateau is not a precise edge measurement.
SUBPIXELS = 4


def coverage_tiles(camera):
    """Tile a higher-resolution image of exactly the same camera, keeping each GPU viewport <=1280."""
    n = SUBPIXELS
    for y in range(n):
        for x in range(n):
            yield {**camera, "id": f"{camera['id']}-tile-{y}-{x}",
                   "fx": camera["fx"] * n, "fy": camera["fy"] * n,
                   "cx": (camera["cx"] + .5) * n - .5 - x * camera["width"],
                   "cy": (camera["cy"] + .5) * n - .5 - y * camera["height"]}


def integrated_coverage(pixels):
    h, w = pixels.shape
    return pixels.reshape(h // SUBPIXELS, SUBPIXELS, w // SUBPIXELS, SUBPIXELS).mean(axis=(1, 3))


def verify_coverage_integrity(out):
    """Independent quantization witness and checks on the saved tiled camera evidence; no renders."""
    # Two straight edges enclose .405 and .595 of a pixel, yet a four-sample pattern records
    # both as exactly half covered. This is a synthetic integration defect, unrelated to Oakley.
    samples = np.array([[.375, .125], [.875, .375], [.125, .625], [.625, .875]])
    coarse = [float(np.mean(samples.sum(axis=1) < edge)) for edge in (.9, 1.1)]
    dense = np.concatenate([(samples + [x, y]) / SUBPIXELS for y in range(SUBPIXELS) for x in range(SUBPIXELS)])
    fine = [float(np.mean(dense.sum(axis=1) < edge)) for edge in (.9, 1.1)]
    assert coarse == [.5, .5] and fine[0] < .5 < fine[1]
    projections = np.load(out / "blender/blender-projection.npz")
    camera_errors = {}
    for rid in [name for name in projections.files if not name.endswith("-hi")]:
        expected = (projections[rid] + .5) * SUBPIXELS - .5
        error = float(np.linalg.norm(projections[rid + "-hi"] - expected, axis=1).max())
        assert error < .01, (rid, error)
        camera_errors[rid] = error
    count = 0
    for path in out.glob("coverage-*/*__silhouette.png"):
        ids_path = path.with_name(path.name.replace("__silhouette", "__part-id"))
        ids = np.asarray(Image.open(ids_path).convert("RGB")).max(axis=2)
        scalar = np.asarray(Image.open(path).convert("RGB"))
        assert np.array_equal(scalar[:, :, 0], scalar[:, :, 1]) and np.array_equal(scalar[:, :, 0], scalar[:, :, 2])
        assert np.array_equal(ids > 0, scalar[:, :, 0] > 0)
        count += 1
    assert count == 6 * SUBPIXELS ** 2
    result = {"status": "passed", "synthetic_exact_pixel_areas": [.405, .595], "coarse_coverage": coarse,
              "integrated_coverage": fine, "high_resolution_projection_error_px": camera_errors,
              "identical_part_id_support_tiles": count}
    procs.write_json_atomic(out / "coverage-integrity.json", result)
    return result


def run(out):
    started = time.monotonic()
    out.mkdir(parents=True, exist_ok=False)
    inputs = {str(p): arview.sha256(p) for p in [ACCEPTED, RUN3, * (BASIS / "cameras/basis-01").rglob("*")]
              if p.is_file()}
    results = {}
    for label, source in (("accepted", ACCEPTED), ("run3-reexport", RUN3)):
        root = out / label
        root.mkdir()
        shutil.copytree(BASIS / "images", root / "images")
        shutil.copytree(BASIS / "cameras", root / "cameras")
        (root / "work").mkdir()
        model = root / "work/model.glb"
        shutil.copyfile(source, model)
        result = look.capture(root, {"status": "exported", "path": str(model), "sha256": arview.sha256(model),
                                     "checkpoint": "immutable-" + label}, "proof", basis="basis-01")
        procs.write_json_atomic(out / f"{label}.json", result)
        assert result["status"] == "ok", result
        assert len(result["views"]) == 6 and result["views"][0]["view_kind"] == "front"
        for row in result["views"]:
            photo = C.load_basis(root, "basis-01").photo(row["ref"])
            border = np.concatenate([photo[0], photo[-1], photo[1:-1, 0], photo[1:-1, -1]])
            assert row["background"]["rgb_8bit"] == np.rint(np.median(border, axis=0)).astype(int).tolist()
        meta, _ = _image(root, result["images"][0]["path"], trusted_output=True)
        assert max(meta["size_px"]) <= 1024
        results[label] = result
    root = out / "blender"
    (root / "work").mkdir(parents=True)
    mesh = glb.load(ACCEPTED)
    vertices, triangles, offset = [], [], 0
    for primitive in mesh.primitives:
        assert not primitive.error and not primitive.compressed
        vertices.append(primitive.positions * 1000)
        triangles.append(primitive.triangles + offset)
        offset += len(primitive.positions)
    points = np.concatenate(vertices)
    np.savez(root / "geometry.npz", vertices=points, triangles=np.concatenate(triangles))
    bs = C.load_basis(BASIS, "basis-01")
    views, analytic, projected = [], {}, {}
    for row in results["accepted"]["views"]:
        explicit, rid = row["camera"], row["ref"]
        k = explicit["meta"]["pixel_scale"]
        cam = BC.crop_camera(bs.camera(rid), k=k)
        shape = (explicit["height"], explicit["width"])
        views.append({"name": rid, "camera": BC.to_blender(cam, bs.frame, shape_hw=shape), "passes": ["clay"]})
        hi_cam = BC.crop_camera(bs.camera(rid), k=k * SUBPIXELS)
        views.append({"name": rid + "-hi", "camera": BC.to_blender(hi_cam, bs.frame,
                      shape_hw=tuple(v * SUBPIXELS for v in shape)), "passes": ["clay"]})
        v = np.asarray(explicit["view"]).reshape(4, 4)
        pc = points / 1000 @ v[:3, :3].T + v[:3, 3]
        uv = np.column_stack([explicit["cx"] + explicit["fx"] * pc[:, 0] / -pc[:, 2],
                               explicit["cy"] - explicit["fy"] * pc[:, 1] / -pc[:, 2]])
        analytic[rid] = float(np.linalg.norm(uv - project_mm(points, cam, bs.frame), axis=1).max())
        projected[rid] = uv
        assert analytic[rid] < 1e-8
    tiles = [tile for row in results["accepted"]["views"] for tile in coverage_tiles(row["camera"])]
    tile_paths = {}
    for start in range(0, len(tiles), 64):
        batch = tiles[start:start + 64]
        camera_file = out / f"coverage-cameras-{start}.json"
        procs.write_json_atomic(camera_file, {"schema": "arview.cameras/1", "cameras": batch})
        directory = out / f"coverage-{start}"
        code, timeout = arview.run_capture([shutil.which("node"), str(arview.ARVIEW / "bin/arview.mjs"),
            "capture", str(ACCEPTED), "--cameras", str(camera_file), "--no-face", "--fixed-scale", "--lensenv", "1",
            "--out", str(directory)], out, timeout_s=280)
        assert not timeout and code in (0, 3)
        for tile in batch:
            tile_paths[tile["id"]] = directory / (tile["id"] + "__silhouette.png")
    procs.write_json_atomic(root / "render-input.json", {"out_dir": str(root / "work/render"), "views": views,
        "engine": "eevee", "light_frame": {"center": list(bs.frame.center), "radius": bs.frame.extent}, "samples": 32})
    with (root / "blender.log").open("w") as log:
        completed = subprocess.run([str(blender.blender_exe()), "-b", "--factory-startup", "--disable-autoexec",
            "--python", str(REPO / "glasses3d/tests/blender/look_camera.py"), "--", str(root)], stdout=log, stderr=log,
            env={k: v for k, v in os.environ.items() if k.upper() in procs.WORKER_ENV_KEYS})
    assert completed.returncode == 0 and (root / "render-result.json").is_file(), (root / "blender.log").read_text()[-4000:]
    actual_blender = np.load(root / "blender-projection.npz")
    metrics = []
    for row in results["accepted"]["views"]:
        rid = row["ref"]
        bpy_error = float(np.linalg.norm(actual_blender[rid] - projected[rid], axis=1).max())
        assert bpy_error < .01, (rid, bpy_error)  # Blender camera matrices store single-precision floats.
        ids = np.asarray(Image.open(row["part_id_path"]).convert("RGB")).max(axis=2)
        coverage_path = row["part_id_path"].replace("__part-id.png", "__silhouette.png")
        coverage = np.asarray(Image.open(coverage_path).convert("RGB"))[:, :, 0]
        assert np.array_equal(ids > 0, coverage > 0), "diagnostic must cover exactly the part-id geometry"
        # Flat white RGB is scalar MSAA coverage, independent of each part's palette colour.
        # Compare the same 50% coverage boundary to Blender's alpha, not faint coloured fringes.
        native_ar = coverage > 127
        native_bl = np.asarray(Image.open(root / f"work/render/{rid}_clay.png").convert("RGBA"))[:, :, 3] > 127
        h, w = coverage.shape
        high = np.zeros((h * SUBPIXELS, w * SUBPIXELS), np.uint8)
        for y in range(SUBPIXELS):
            for x in range(SUBPIXELS):
                tile_id = f"{row['camera']['id']}-tile-{y}-{x}"
                high[y*h:(y+1)*h, x*w:(x+1)*w] = np.asarray(Image.open(tile_paths[tile_id]).convert("RGB"))[:, :, 0]
        ar = integrated_coverage(high) > 127.5
        high_bl = np.asarray(Image.open(root / f"work/render/{rid}-hi_clay.png").convert("RGBA"))[:, :, 3]
        bl = integrated_coverage(high_bl) > 127.5
        ae, be = ar ^ binary_erosion(ar), bl ^ binary_erosion(bl)
        edge = max(float(distance_transform_edt(~be)[ae].max()), float(distance_transform_edt(~ae)[be].max()))
        iou = float((ar & bl).sum() / (ar | bl).sum())
        overlay = np.zeros((*ar.shape, 3), np.uint8)
        overlay[ar & bl] = [160, 160, 160]
        overlay[ar & ~bl] = [255, 60, 60]
        overlay[bl & ~ar] = [60, 180, 255]
        Image.fromarray(overlay).save(out / f"camera-parity-{rid}.png")
        metrics.append({"ref": rid, "iou": iou, "max_edge_offset_px": edge, "analytic_max_error_px": analytic[rid],
                        "blender_projection_max_error_px": bpy_error,
                        "native_four_sample_iou": float((native_ar & native_bl).sum() / (native_ar | native_bl).sum()),
                        "coverage_integration_grid": SUBPIXELS})
    proof = {"status": "passed" if all(r["iou"] >= .995 and r["max_edge_offset_px"] <= 2 for r in metrics) else "failed",
             "actual_spend_usd": 0, "model_requests": 0, "camera_parity": metrics,
             "parity_allowance": "IoU >= .995 and maximum edge distance <= 2 native pixels; both renderers integrate "
                                 "the same native pixel areas on a fixed 4x4 grid before the 50% contour",
             "sheets": {label: result["images"][0]["path"] for label, result in results.items()},
             "front_sheets": {label: result["views"][0]["sheet_path"] for label, result in results.items()},
             "unchanged_inputs": inputs, "seconds": time.monotonic() - started}
    for path, digest in inputs.items():
        assert arview.sha256(Path(path)) == digest
    procs.write_json_atomic(out / "proof.json", proof)
    verify_coverage_integrity(out)
    print(json.dumps(proof, indent=1))
    assert proof["status"] == "passed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true", help="Check saved coverage evidence without rendering")
    args = parser.parse_args()
    os.environ["G3D_OFFLINE"] = "1"
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        if args.verify_only:
            print(json.dumps(verify_coverage_integrity(args.out.resolve()), indent=1))
        else:
            run(args.out.resolve())
    assert not network.attempts and not clients.constructions
