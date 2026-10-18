"""Photo-camera AR appearance evidence plus a same-export worn portrait. No appearance gates."""
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy.ndimage import binary_erosion

from ...g3d_host import compare as C
from ...g3d_host.ar_camera import explicit_camera
from .. import procs
from . import arview, look_panels, look_composition

SCHEMA = "g3d.look_comparison/1"


def border_colour(photo):
    """Each outermost border pixel once; uniformity is reported, never assumed or graded."""
    edge = np.zeros(photo.shape[:2], bool)
    edge[[0, -1], :] = True
    edge[:, [0, -1]] = True
    pixels = photo[edge].astype(float)
    median = np.median(pixels, axis=0)
    delta = np.max(np.abs(pixels - median), axis=1)
    rgb = np.rint(median).astype(int).tolist()
    return {"rgb_8bit": rgb, "hex": "#" + "".join(f"{v:02x}" for v in rgb), "pixels": len(pixels),
            "method": "median of all outermost border pixels, corners counted once",
            "uniformity": {"p95_max_channel_distance_8bit": float(np.percentile(delta, 95)),
                           "fraction_within_5_levels": float(np.mean(delta <= 5))}}


def part_masks(ids, palette):
    masks = {name: np.zeros(ids.shape[:2], bool) for name in ("lens", "frame")}
    for entry in palette:
        role = entry.get("class")
        if role in masks:
            masks[role] |= np.max(np.abs(ids.astype(float) - entry["rgb"]), axis=2) <= entry["decode_tolerance"]
    return masks


def colour_stats(image, mask):
    pixels = np.asarray(image)[mask]
    if not len(pixels):
        return {"status": "unavailable", "reason": "no interior pixels after three-pixel erosion", "pixels": 0}
    srgb = pixels.astype(float) / 255
    linear = np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)
    luminance = linear @ np.array([.2126, .7152, .0722])
    lightness = np.where(luminance > (6 / 29) ** 3, 116 * np.cbrt(luminance) - 16, (29 / 3) ** 3 * luminance)
    return {"status": "measured", "pixels": len(pixels), "median_srgb_8bit": np.median(pixels, axis=0).tolist(),
            "median_lightness_Lstar": float(np.median(lightness))}


def box_of(mask, margin=8):
    y, x = np.nonzero(mask)
    if not len(x):
        return [0, 0, mask.shape[1], mask.shape[0]]
    return [max(0, int(x.min()) - margin), max(0, int(y.min()) - margin),
            min(mask.shape[1], int(x.max()) + margin + 1), min(mask.shape[0], int(y.max()) + margin + 1)]


def sheet(rows, portrait, path, *, compact):
    # Each row uses the SAME crop on photo and drawing. No colour adjustment, normalization or contrast stretch.
    rh = max(1, min(220, 620 // max(1, len(rows)))) if compact else 250
    result = Image.new("RGB", (1024, 42 + rh * len(rows) + 330), "#ededed")
    draw = ImageDraw.Draw(result)
    draw.text((8, 5), "Photo | AR at frozen camera | Photo lens detail | AR lens detail", fill="black", font_size=16)
    for index, row in enumerate(rows):
        top = 42 + index * rh
        label_height = 22 if rh >= 60 else 0  # large photo sets retain native per-photo sheets for inspection
        if label_height:
            draw.text((8, top), f"{row['ref']} ({row['view_kind']})", fill="black", font_size=13)
        for col, (key, box) in enumerate((("photo_path", "crop_box"), ("drawing_path", "crop_box"),
                                         ("photo_path", "lens_box"), ("drawing_path", "lens_box"))):
            with Image.open(row[key]) as im:
                cropped = im.convert("RGB").crop(row[box])
                tile = ImageOps.contain(cropped, (252, max(1, rh - label_height - 2)))
            result.paste(tile, (col * 256 + (256 - tile.width) // 2,
                                top + label_height + (rh - label_height - tile.height) // 2))
    top = 42 + rh * len(rows)
    draw.text((8, top), "Same export worn on the test face: judge lens colour and visible eyes", fill="black", font_size=16)
    with Image.open(portrait) as im:
        tile = ImageOps.contain(im.convert("RGB"), (1008, 300))
    result.paste(tile, ((1024 - tile.width) // 2, top + 26))
    result.save(path)


def verified_report(root, record, digest):
    path = arview.confined(root, record["report"])
    if arview.sha256(path) != record["report_sha256"]:
        raise ValueError("capture report bytes changed")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["input"]["sha256"] != digest or record.get("export_sha256") != digest:
        raise ValueError("capture is not of this export")
    for image in report["images"]:
        target = (path.parent / image["file"]).resolve()
        if not target.is_relative_to(path.parent) or arview.sha256(target) != image["sha256"]:
            raise ValueError("capture image bytes changed or escaped its directory")
    return path, report


def capture(root, export, call_id, *, refs=None, basis=None, front_only=False, worn=None, timeout_s=280,
            combine=False):
    """Return an informational refusal on missing evidence; never alter the frozen basis or the export."""
    root = Path(root).resolve()
    base = {"schema": SCHEMA, "informational_only": True, "export_sha256": export.get("sha256"),
            "checkpoint": export.get("checkpoint"), "images": []}
    try:
        bs = C.load_basis(root, basis)
        wanted = bs.usable_refs() if refs is None else [C.ref_id(name) for name in refs]
        if front_only:
            wanted = [rid for rid in wanted if bs.refs[rid].get("view_kind") == "front"][:1]
        if not wanted:
            return {**base, "status": "unavailable", "code": "E_NO_PHOTO_CAMERA", "message": "No usable front photo camera."}
        wanted = list(dict.fromkeys(wanted))
        if any(rid not in bs.refs for rid in wanted):
            raise ValueError("unknown photo reference")
        wanted.sort(key=lambda rid: bs.refs[rid].get("view_kind") != "front")
        model = arview.confined(root, export["path"])
        if export.get("status") != "exported" or arview.sha256(model) != export["sha256"]:
            raise ValueError("current export bytes changed or are unavailable")
        directory = arview.output_dir(root, call_id + "-look")
        rows, cameras = [], []
        geometry = look_panels.load_geometry(model)
        for index, rid in enumerate(wanted):
            photo = bs.photo(rid)
            background = border_colour(photo)
            cam = explicit_camera(bs.camera(rid), bs.frame, bs.shape(rid), name=f"photo-{index:03d}",
                                  background=background["hex"], size_px=min(4096, max(photo.shape[:2])),
                                  temple_pose=bs.pose(rid))
            cameras.append(cam)
            resized = C.resample_box(photo, [0, 0, photo.shape[1], photo.shape[0]], cam["meta"]["pixel_scale"],
                                     (cam["height"], cam["width"]))
            path = directory / f"{cam['id']}-photo.png"
            Image.fromarray(resized).save(path)
            rows.append({"ref": rid, "view_kind": bs.refs[rid].get("view_kind"), "camera": cam,
                         "photo_sha256": bs.refs[rid]["photo_sha256"], "photo_path": str(path),
                         "background": background, "frozen_temple_pose": bs.pose(rid)})
        node = shutil.which("node")
        if not node:
            raise OSError("Node.js is unavailable")
        captures = []
        for start in range(0, len(cameras), 64):
            batch = cameras[start:start + 64]
            camera_path = directory / f"cameras-{start:03d}.json"
            procs.write_json_atomic(camera_path, {"schema": "arview.cameras/1", "cameras": batch})
            out = directory / f"capture-{start:03d}"
            code, timed_out = arview.run_capture([node, str(arview.ARVIEW / "bin/arview.mjs"), "capture", str(model),
                "--cameras", str(camera_path), "--no-face", "--fixed-scale", "--lensenv", str(arview.LENS_ENV_INTENSITY),
                "--out", str(out)], directory, timeout_s=timeout_s)
            if timed_out or code not in (0, 3):
                raise ValueError(f"photo-camera capture failed (exit {code}, timeout {timed_out})")
            path = out / "report.json"
            _, report = verified_report(root, {"report": str(path), "report_sha256": arview.sha256(path),
                                               "export_sha256": export["sha256"]}, export["sha256"])
            captures.append(str(path))
            for row in rows[start:start + 64]:
                name = row["camera"]["id"]
                row.update(drawing_path=str(out / f"{name}__beauty.png"), part_id_path=str(out / f"{name}__part-id.png"))
                photo = np.asarray(Image.open(row["photo_path"]).convert("RGB"))
                drawing = np.asarray(Image.open(row["drawing_path"]).convert("RGB"))
                ids = np.asarray(Image.open(row["part_id_path"]).convert("RGB"))
                masks = part_masks(ids, report["id_palette"])
                silhouette = np.max(ids, axis=2) > 24
                # Union of frozen photo ROI and current model bounds retains obvious shape disagreements.
                roi = np.rint(np.asarray(bs.roi(row["ref"])) * row["camera"]["meta"]["pixel_scale"]).astype(int)
                mask = silhouette.copy()
                mask[max(0, roi[1]):min(mask.shape[0], roi[3]), max(0, roi[0]):min(mask.shape[1], roi[2])] = True
                row.update(crop_box=box_of(mask), lens_box=box_of(masks["lens"]), colours={})
                for role, mask in masks.items():
                    interior = binary_erosion(mask, structure=np.ones((7, 7), bool))
                    row["colours"][role] = {"photo": colour_stats(photo, interior), "drawing": colour_stats(drawing, interior)}
                panels = look_panels.build_panels(row, report["id_palette"], geometry, directory / name)
                row.update(panels)
        # Same immutable model, cameras and materials; only this diagnostic light differs.
        # Missing diagnostics are reported as information and do not invent a quality gate.
        from ...g3d_host import surface_quality
        try:
            surface = surface_quality.report(model)
        except (OSError, ValueError, KeyError, TypeError, np.linalg.LinAlgError) as error:
            surface = {"schema": "g3d.surface_quality/1", "informational_only": True,
                       "measurement_status": "unavailable", "measurement_error": str(error)}
        native_receipt = export.get("receipt")
        if native_receipt:
            try:
                surface["native_facets"] = json.loads(arview.confined(root, native_receipt).read_text(encoding="utf-8")).get("surface_quality", {})
            except (OSError, ValueError, TypeError):
                surface["native_facets"] = {"status": "unavailable"}
        surface_images = []
        surface["views"] = []
        try:
            for start in range(0, len(cameras), 64):
                batch = [{**cam, "lighting": {"preset": "side_studio", "intensity": .8}} for cam in cameras[start:start+64]]
                camera_path = directory / f"surface-cameras-{start:03d}.json"
                procs.write_json_atomic(camera_path, {"schema": "arview.cameras/1", "cameras": batch})
                out = directory / f"surface-{start:03d}"
                code, timed_out = arview.run_capture([node, str(arview.ARVIEW / "bin/arview.mjs"), "capture", str(model),
                    "--cameras", str(camera_path), "--no-face", "--fixed-scale", "--lensenv", str(arview.LENS_ENV_INTENSITY),
                    "--out", str(out)], directory, timeout_s=timeout_s)
                if timed_out or code not in (0, 3):
                    raise ValueError(f"side-light capture failed (exit {code}, timeout {timed_out})")
                report_path = out / "report.json"
                _, diagnostic = verified_report(root, {"report": str(report_path), "report_sha256": arview.sha256(report_path),
                    "export_sha256": export["sha256"]}, export["sha256"])
                captures.append(str(report_path))
                for row in rows[start:start+64]:
                    name = row["camera"]["id"]
                    side = {**row, "drawing_path": str(out / f"{name}__beauty.png"),
                            "part_id_path": str(out / f"{name}__part-id.png")}
                    panels = look_panels.build_panels(side, diagnostic["id_palette"], geometry,
                        directory / ("surface-"+name), surface_only=True)
                    surface_images.extend(panels["images"])
                    surface["views"].append({"ref": row["ref"], "light": "side_studio", "details": panels["details"],
                                              "drawing_path": side["drawing_path"]})
            surface["status"] = "measured"
        except (OSError, ValueError, KeyError, TypeError) as error:
            surface.update(status="unavailable", message=str(error))
        if worn is None:
            worn = arview.capture(root, export, call_id + "-worn", timeout_s=timeout_s)
        path, report = verified_report(root, worn, export["sha256"])
        rec = next(rec for rec in report["images"] if rec.get("kind") == "portrait" and
                   rec.get("variant") == "condition-a" and rec.get("region") == "eyes")
        portrait = directory / "worn-front.png"
        shutil.copyfile(path.parent / rec["file"], portrait)
        worn_directory = directory / "worn"
        worn_directory.mkdir()
        shutil.copyfile(path, worn_directory / "report.json")
        for image in report["images"]:
            target = worn_directory / image["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path.parent / image["file"], target)
        overview = directory / "comparison.png"
        sheet(rows, portrait, overview, compact=True)
        # Pack only this call's evidence, per photo. Keep the labelled source panels
        # and detail reports intact; neither historical outputs nor close-up selection change.
        images = [image for row in rows for image in row["images"]] + surface_images
        if combine:
            images = []
            for row in rows:
                sources = row["images"] + [image for image in surface_images if image["ref"] == row["ref"]]
                row["composed_images"] = look_composition.compose(
                    sources, directory / row["camera"]["id"] / "combined", row["ref"])
                images.extend(row["composed_images"])
        delivery_note = ("Each photo's full pair and native details are combined without changing pixels; "
                         "overflow uses additional images." if combine else
                         "Full pairs and native details are separate images.")
        result = {**base, "status": "ok", "basis": bs.id, "basis_sha256": arview.sha256(bs.dir / "basis.json"),
                  "cameras_sha256": bs.meta["cameras_json_sha256"], "views": rows,
                  "unusable_refs": bs.unusable_refs(), "worn_front_path": str(portrait), "capture_reports": captures,
                  "worn_capture": {"report": str(worn_directory / "report.json"), "report_sha256": arview.sha256(path),
                                   "export_sha256": export["sha256"], "portrait_sha256": rec["sha256"]},
                  "fixed_scale": True, "lens_env_intensity": arview.LENS_ENV_INTENSITY,
                  "summary": "Photo-camera AR colour check plus same-export worn front. Colours are information, not a gate. "
                             "Fixed-room highlights are not lens colour. " + delivery_note,
                  "method": "Median sRGB and CIE L* in AR lens/frame part-id masks eroded 3 pixels, applied identically "
                            "to photo and drawing. Registration can mix photo regions; no tone/colour normalization. "
                            "Frozen per-photo temple deltas are applied to tagged temple parts and their markings. "
                            "The worn view resets to the authored model before the normal face fit.",
                  "overview_path": str(overview), "image_delivery": "separate", "surface_quality": surface,
                  "images": images + [
                      {"path": str(portrait), "sha256": arview.sha256(portrait),
                       "view": "Same export worn front on the test face"}]}
        result["assets"] = [{"path": str(p), "sha256": arview.sha256(p)} for p in sorted(directory.rglob("*"))
                            if p.is_file() and p.suffix in (".png", ".json")]
        report_path = directory / "comparison-report.json"
        procs.write_json_atomic(report_path, result)
        return {**result, "report": str(report_path), "report_sha256": arview.sha256(report_path)}
    except (C.NoBasis, C.UnusableReference, OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        return {**base, "status": "unavailable", "code": "E_LOOK", "message": str(error)}


def tool_for(scene, tool, call):
    from agents.tool_context import ToolContext

    @tool("compare_look_to_photos")
    async def compare_look_to_photos(ctx: ToolContext, refs: list[str] | None = None, basis: str | None = None):
        """Like-for-like shape and appearance check: the owner's AR renderer at each frozen photo camera.
        Judge shape details, lens hue, lightness and see-through and frame colour against the photos. Fixed-room reflection
        highlights are expected and are not lens colour. Every successful call also shows the worn front on the
        test face, eyes legible, from the SAME export. After shape edits and colour/material edits, inspect the
        full-size photo pairs and native close-ups, and judge the worn view too.
        Returns each photo's full-size photo/drawing pair combined with its automatic native details of corners,
        inner rims, lens marks, arm roots and tips. Every panel keeps its pixels, including labels; overflow uses
        another image within high-detail limits, never shrinking any panel. Also returns the worn front.
        Front-facing surfaces, depth walls and inner-rim walls have
        separate colour reports, including their rendered/photo shading; surface selection is geometric.
        Also returns side-light native close-ups of facet flatness, front shading seams and lens ripple,
        with measured planarity, degenerates, tessellation and normal discontinuities as information only.
        Intentional creases also make normal jumps; use the photos to decide what belongs. A missing side-light
        diagnostic is reported and is not a new finish gate. Review lighting and material settings stay unchanged.
        No colour gate. Unusable cameras and missing evidence are reported. Frozen temple openings are applied
        per photo; the worn view uses the authored model and face fit. Perspective-0 cameras use a stated
        distant-pinhole approximation.

        Args:
            refs: Photo ids or original filenames; null uses every usable frozen camera, front photo first.
            basis: Frozen basis id; null uses the latest usable basis. Does not fit or modify cameras.
        """
        return await call(ctx, "compare_look_to_photos", lambda cid: capture(
            scene.root, scene.seeing.export_head(call_id=cid), cid, refs=refs, basis=basis, combine=True))
    return compare_look_to_photos
