"""Exact triangle ray inspection at pixels from the provider QA front camera."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from reconstruction.partition_glb import inspect_partition_source


def inspect(glb, render_report, case_id, pixels):
    report = json.loads(render_report.read_text(encoding="utf-8"))
    case = next(row for row in report["cases"] if row["id"] == case_id)
    transform = case["normalization"]
    # Three Euler XYZ: Rx @ Ry @ Rz. The camera looks from +Z toward zero.
    x, y, z = np.deg2rad(transform["rotation_degrees"])
    rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    rotation = rx @ ry @ rz
    width, height = report["renderer"]["width"], report["renderer"]["height"]
    pixel_span = case["orthographic_vertical_span"] / height
    rays = [{"pixel": pixel, "normalized_xy": [(pixel[0] + .5 - width / 2) * pixel_span,
                                                (height / 2 - pixel[1] - .5) * pixel_span], "hits": []} for pixel in pixels]
    raw = glb.read_bytes()
    source = inspect_partition_source(raw)
    for part_id, part in enumerate(source["instances"]):
        matrix = part["world_matrix"]
        points = part["positions"] @ matrix[:3, :3].T + matrix[:3, 3]
        original_triangles = points[part["indices"]]
        triangles = ((original_triangles @ rotation.T) + transform["translation"]) * transform["uniform_scale"]
        a, u, v = triangles[:, 0, :2], triangles[:, 1, :2] - triangles[:, 0, :2], triangles[:, 2, :2] - triangles[:, 0, :2]
        det = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
        for ray in rays:
            relative = np.array(ray["normalized_xy"]) - a
            with np.errstate(divide="ignore", invalid="ignore"):
                b1 = (relative[:, 0] * v[:, 1] - relative[:, 1] * v[:, 0]) / det
                b2 = (u[:, 0] * relative[:, 1] - u[:, 1] * relative[:, 0]) / det
            keep = (np.abs(det) > 1e-20) & (b1 >= -1e-10) & (b2 >= -1e-10) & (b1 + b2 <= 1 + 1e-10)
            for face in np.flatnonzero(keep):
                bary = np.array([1 - b1[face] - b2[face], b1[face], b2[face]])
                point = bary @ triangles[face]
                normal = np.cross(triangles[face, 1] - triangles[face, 0], triangles[face, 2] - triangles[face, 0])
                normal /= max(np.linalg.norm(normal), 1e-30)
                ray["hits"].append({"part_index": part_id, "binding": part["source_binding"], "part_face_index": int(face),
                                    "normalized_depth_z": float(point[2]), "world_xyz": (bary @ original_triangles[face]).tolist(),
                                    "front_facing_normal_z": float(normal[2]), "barycentric": bary.tolist()})
    for ray in rays:
        ray["hits"].sort(key=lambda hit: -hit["normalized_depth_z"])
    return {"scope": "All triangle intersections, including front/back faces and hidden layers; no semantic assignments. +normalized Z is nearer the front camera.",
            "glb": str(glb.resolve()), "glb_sha256": hashlib.sha256(raw).hexdigest(),
            "render_report_sha256": hashlib.sha256(render_report.read_bytes()).hexdigest(),
            "case_id": case_id, "normalization": transform, "rays": rays}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--render-report", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--pixels", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.glb, args.render_report, args.case_id, json.loads(args.pixels))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for ray in result["rays"]:
        print(ray["pixel"], [(hit["part_index"], round(hit["world_xyz"][0], 6), round(hit["front_facing_normal_z"], 3)) for hit in ray["hits"]])
