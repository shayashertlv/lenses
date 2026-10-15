"""Native photo-first AR room-light comparisons of real temple-builder exports."""
from pathlib import Path
import json
import shutil
import time

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.agent.procs import write_json_atomic
from glasses3d.agent.tools import arview, look, look_panels
from glasses3d.g3d.temple import build as B
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.ar_camera import explicit_camera

TILE_SIDE = 500


def photo_rows(lab, photos, directory):
    bs = lab.basis
    rows = []
    for index, pid in enumerate(photos):
        rid = lab.rid(pid)
        photo = bs.photo(rid)
        camera = explicit_camera(bs.camera(rid), bs.frame, bs.shape(rid), name=f"photo-{index:03d}",
            background=look.border_colour(photo)["hex"], size_px=min(4096, max(photo.shape[:2])),
            temple_pose=bs.pose(rid))
        scale = camera["meta"]["pixel_scale"]
        pixels = look.C.resample_box(photo, [0, 0, photo.shape[1], photo.shape[0]], scale,
                                    (camera["height"], camera["width"]))
        path = directory / f"{camera['id']}-photo.png"
        Image.fromarray(pixels).save(path)
        rows.append({"photo": pid, "reference": rid, "camera": camera, "photo_path": str(path),
                     "photo_sha256": arview.sha256(path), "native_pixel_scale": scale})
    return rows


def capture(lab, export, rows, directory, deadline):
    """Same explicit-camera renderer/export checks as the parent look tool, without a new worn-view policy."""
    directory.mkdir(parents=True, exist_ok=False)
    camera_file = directory / "cameras.json"
    write_json_atomic(camera_file, {"schema": "arview.cameras/1", "cameras": [r["camera"] for r in rows]})
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js is unavailable")
    model = arview.confined(lab.root, export["path"])
    if arview.sha256(model) != export["sha256"]:
        raise ValueError("candidate GLB bytes changed")
    out = directory / "capture"
    code, timeout = arview.run_capture([node, str(arview.ARVIEW / "bin/arview.mjs"), "capture", str(model),
        "--cameras", str(camera_file), "--no-face", "--fixed-scale", "--lensenv", str(arview.LENS_ENV_INTENSITY),
        "--out", str(out)], directory, timeout_s=max(1, deadline-time.monotonic()))
    if timeout or code not in (0, 3):
        raise ValueError(f"AR capture failed: exit {code}, timeout {timeout}")
    report_path = out / "report.json"
    _, report = look.verified_report(lab.root, {"report": str(report_path),
        "report_sha256": arview.sha256(report_path), "export_sha256": export["sha256"]}, export["sha256"])
    return {"report": str(report_path), "report_sha256": arview.sha256(report_path),
            "spec": report.get("spec"), "exit_code": code, "gpu": report.get("gpu"),
            "room_light": {"environment": "RoomEnvironment", "intensity": .8, "lensenv": arview.LENS_ENV_INTENSITY},
            "images": [{"photo": row["photo"], "path": str(out / f"{row['camera']['id']}__beauty.png"),
                        "sha256": arview.sha256(out / f"{row['camera']['id']}__beauty.png")}
                       for row in rows]}


def visible_target(model, camera, tb, moving_facet, box, side, *, _geometry=None):
    """Native first-hit visibility of the named outer face, including front/marking occlusion."""
    geometry = _geometry or look_panels.posed_geometry(look_panels.load_geometry(model), camera.get("temple_pose"))
    x0, y0, x1, y1 = box
    # Keep the ray arrays bounded even if the projected face spans the entire 4096px capture.
    if (x1-x0)*(y1-y0) > 250000:
        return sum(visible_target(model, camera, tb, moving_facet, tile, side, _geometry=geometry)
                   for tile in look_panels._tiles(box, TILE_SIDE))
    yy, xx = np.mgrid[y0:y1, x0:x1]
    x, y = xx.ravel(), yy.ravel()
    V = np.asarray(camera["view"]).reshape(4, 4)
    R, t = V[:3, :3], V[:3, 3]
    eye = -R.T @ t
    directions = np.column_stack(((x-camera["cx"])/camera["fx"],
                                  (camera["cy"]-y)/camera["fy"], -np.ones(len(x)))) @ R
    hit = geometry.scene.cast_rays(look_panels._tensor(np.column_stack((np.broadcast_to(eye, directions.shape), directions))), nthreads=1)
    distance, ids = hit["t_hit"].numpy(), hit["primitive_ids"].numpy()
    good = np.isfinite(distance) & (ids < len(geometry.faces))
    mask = np.zeros(len(x), bool)
    if good.any():
        point = (eye + directions[good] * distance[good, None])*1000
        parsed = PO.parse_pose(camera.get("temple_pose"))
        if side in parsed:
            theta, axis = parsed[side]
            point = PO.rot_about_hinge(point, side, -theta, axis)
        h = tb.spec.hinge
        local = B.to_local(point, h["z_mm"], h["axis_xz_mm"], h["opening_deg"], side)
        labels = np.array([p["role"] == "temple" for p in geometry.parts])
        owner = geometry.face_parts[ids[good].astype(int)]
        # Clipped builder polygons define the face directly, independent of product labels/side conventions.
        inside = np.zeros(len(local), bool)
        for index, poly in enumerate(tb.polys):
            if tb.poly_region[index] != moving_facet or tb.poly_kind[index] != "outer":
                continue
            Q = tb.V[poly]
            normal = tb.normals[index]
            plane = np.abs((local-Q[0]) @ normal) <= .025
            inside |= plane & B.points_in_poly(local[:, :2], Q[:, :2])
        mask[np.flatnonzero(good)] = labels[owner] & inside
    return int(mask.sum())


def make_sheets(lab, comparison_id, moving_facet, rows, baseline, candidates, builds, directory, *, facets=None):
    """Tile large areas at native scale; every image starts with the original photo pixels."""
    baseline_tb = builds["baseline"]
    context_facets = set(facets or [moving_facet])
    points = []
    for tb in builds.values():
        for i, poly in enumerate(tb.polys):
            if tb.poly_region[i] in context_facets and tb.poly_kind[i] == "outer":
                points.extend(tb.V[poly].tolist())
    points = np.asarray(points, float)
    output, photo_records = [], []
    for row in rows:
        side, native_box = lab._variant_box(row["reference"], points, baseline_tb.spec.hinge)
        if native_box is None:
            photo_records.append({**row, "visible_pixels": 0, "reason": "named face projects outside the photo"})
            continue
        scale = row["camera"]["meta"]["pixel_scale"]
        box = np.rint(np.asarray(native_box)*scale).astype(int).tolist()
        box[0], box[2] = np.clip([box[0], box[2]], 0, row["camera"]["width"]).tolist()
        box[1], box[3] = np.clip([box[1], box[3]], 0, row["camera"]["height"]).tolist()
        photo = Image.open(row["photo_path"]).convert("RGB")
        items = [baseline] + [c for c in candidates if c.get("capture") and c.get("export")]
        visible = {item["label"]: visible_target(item["export"]["path"], row["camera"], builds[item["label"]],
                   moving_facet, box, side) for item in items}
        panels = [("Reference photo", photo)]
        for item in items:
            image = next(i for i in item["capture"]["images"] if i["photo"] == row["photo"])
            label = f"Baseline {item['built_degrees']:.5g} deg" if item is baseline else f"Choice {item['built_degrees']:.5g} deg"
            if not visible[item["label"]]:
                label += " (occluded)"
            if item.get("status") != "eligible" and item is not baseline:
                label += " — hard checks failed"
            panels.append((label, Image.open(image["path"]).convert("RGB")))
        paths = []
        for index, tile in enumerate(look_panels._tiles(box, TILE_SIDE)):
            w, h = tile[2]-tile[0], tile[3]-tile[1]
            sheet = Image.new("RGB", (len(panels)*w, h+52), "#eeeeee")
            draw = ImageDraw.Draw(sheet)
            scale_label = "native pixels" if scale == 1 else f"photo scale {scale:.5g} (4096px capture bound)"
            draw.text((5, 2), f"{row['photo']} | {moving_facet} | AR room light | {scale_label}; comparison {comparison_id}", fill="black")
            for col, (label, pixels) in enumerate(panels):
                draw.text((col*w+5, 26), label, fill="black")
                sheet.paste(pixels.crop(tile), (col*w, 52))
            path = directory / f"{row['camera']['id']}-tile-{index:02d}.png"
            sheet.save(path)
            paths.append({"path": str(path), "sha256": arview.sha256(path), "photo_first": True,
                          "native_box": tile, "scale": scale, "labels": [p[0] for p in panels]})
            output.append(lab._image(path, "crease_angles", ref=comparison_id, photo=row["photo"]))
        photo_records.append({**row, "context_facets": sorted(context_facets), "visible_pixels": max(visible.values(), default=0),
            "visible_pixels_by_variant": visible, "side": side, "box": box, "sheets": paths,
            **({"reason": "named outer face has no first-hit pixels in these variants"} if not any(visible.values()) else {})})
    return output, photo_records
