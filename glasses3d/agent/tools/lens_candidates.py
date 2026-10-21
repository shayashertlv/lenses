"""Read-only, native-pixel lens experiments on exact copies of the current GLB."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field
from scipy.ndimage import binary_erosion

from ...g3d_host import compare as C
from ...g3d_host.ar_camera import explicit_camera
from .. import owner_lens_edit, procs
from ..look_cost import documented_image
from ..tool_timeouts import LOOK_CAPTURE_DEADLINE_S
from . import arview, look, native_panels


class LensCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    label: str = Field(min_length=1, max_length=48)
    tint: list[float] = Field(min_length=3, max_length=3)
    transmission: float
    metallic: float
    roughness: float
    ior: float
    iridescence: float
    iridescence_ior: float
    iridescence_thickness_min: float
    iridescence_thickness_max: float


def validate_candidates(candidates):
    if not isinstance(candidates, list) or not 2 <= len(candidates) <= 6:
        raise ValueError("Provide 2–6 complete candidate material sets.")
    rows, labels = [], set()
    for candidate in candidates:
        value = candidate.model_dump() if isinstance(candidate, LensCandidate) else dict(candidate)
        label = value.pop("label", None)
        if (not isinstance(label, str) or not label.strip() or len(label) > 48
                or any(ord(c) < 32 for c in label) or label in labels):
            raise ValueError("Candidate labels must be distinct, short, printable text (1–48 characters).")
        labels.add(label)
        rows.append({"label": label, "values": owner_lens_edit._values(value)})
    return rows


def _selected_materials(source, names):
    doc, _ = owner_lens_edit._parse(Path(source).read_bytes())
    rows = owner_lens_edit.read_lens_materials(source)["materials"]
    if (not isinstance(names, list) or not names or len(set(names)) != len(names)
            or any(not isinstance(name, str) or not name for name in names)):
        raise ValueError("Name the distinct lens materials to compare; keep separate lens marks unselected.")
    chosen = [row for row in rows if row["name"] in names]
    if sorted(row["name"] for row in chosen) != sorted(names):
        raise ValueError("Each selected name must identify exactly one material used exclusively by labelled lenses.")
    return [{**row, "source_double_sided": doc["materials"][row["material_index"]].get("doubleSided", False)}
            for row in chosen]


def _check_sidedness(root, export, selected, candidates):
    """An exact JSON patch cannot silently reproduce a separate automatic exporter correction."""
    if not any(row["values"]["transmission"] > 0 for row in candidates):
        return
    double = [row for row in selected if row["source_double_sided"]]
    if not double:
        return
    receipt = json.loads(arview.confined(root, export["receipt"]).read_text(encoding="utf-8")) if export.get("receipt") else {}
    audit = receipt.get("material_sidedness", [])
    for row in double:
        match = [item for item in audit if item.get("material") == row["name"] and item.get("partRole") == "lens"]
        if len(match) != 1 or match[0].get("closed_outward") is not False:
            raise ValueError(f"Lens {row['name']!r} is double-sided; a transmitting candidate would change, or cannot "
                "verify, exporter sidedness. Set its intended backface culling in Blender, export, and compare again. "
                "The existing closed-outward exporter rule is unchanged.")


def _worn(root, export, call_id):
    captured = arview.capture(root, export, call_id, timeout_s=LOOK_CAPTURE_DEADLINE_S)
    path, report = look.verified_report(root, captured, export["sha256"])
    if captured.get("status") not in {"ok", "spec_failed"} or report.get("errors"):
        raise ValueError("Worn candidate capture failed: " + str(captured.get("message", captured.get("status"))))
    records = report["images"]
    front = next(row for row in records if row.get("kind") == "portrait"
                 and row.get("variant") == "condition-a" and row.get("region") == "eyes")
    images = {"Worn front": str(path.parent / front["file"])}
    for yaw in (-50, 50):
        poses = [pose["id"] for pose in report["poses"] if pose.get("worn")
                 and pose.get("yaw") == yaw and pose.get("pitch") == 0 and pose.get("roll") == 0]
        rec = next(row for row in records if row.get("kind") == "worn_crop" and row.get("pose") in poses)
        images[f"Worn head yaw {yaw:+d} degrees"] = str(path.parent / rec["file"])
    return images, captured


def _front(root, export, directory, camera, photo, ref):
    node = shutil.which("node")
    if node is None:
        raise OSError("Node.js is unavailable")
    cameras = directory / "camera.json"
    procs.write_json_atomic(cameras, {"schema": "arview.cameras/1", "cameras": [camera]})
    out = directory / "front"
    code, timed_out = arview.run_capture([node, str(arview.ARVIEW / "bin/arview.mjs"), "capture",
        str(export["path"]), "--cameras", str(cameras), "--no-face", "--fixed-scale",
        "--lensenv", str(arview.LENS_ENV_INTENSITY), "--out", str(out)], directory,
        timeout_s=LOOK_CAPTURE_DEADLINE_S)
    if timed_out or code not in (0, 3):
        raise ValueError(f"Front candidate capture failed (exit {code}, timeout {timed_out}).")
    report_path = out / "report.json"
    _, report = look.verified_report(root, {"report": str(report_path), "report_sha256": arview.sha256(report_path),
        "export_sha256": export["sha256"]}, export["sha256"])
    if report.get("errors"):
        raise ValueError("Front candidate capture reported errors.")
    drawing_path = out / (camera["id"] + "__beauty.png")
    with Image.open(drawing_path) as opened:
        drawing = np.asarray(opened.convert("RGB"))
    with Image.open(out / (camera["id"] + "__part-id.png")) as opened:
        ids = np.asarray(opened.convert("RGB"))
    mask = binary_erosion(look.part_masks(ids, report["id_palette"])["lens"], structure=np.ones((7, 7), bool))
    row = {"ref": ref, "colours": {"lens": {"photo": look.colour_stats(photo, mask),
        "drawing": look.colour_stats(drawing, mask)}}}
    return str(drawing_path), look.lens_colour_lines([row]), str(report_path)


def capture(root, export, call_id, *, candidates, materials, ref=None, basis=None):
    base = {"schema": "g3d.lens_candidates/1", "informational_only": True,
            "checkpoint": export.get("checkpoint"), "export_sha256": export.get("sha256"),
            "images": [], "image_delivery": "separate"}
    try:
        rows = validate_candidates(candidates)
        root = Path(root).resolve()
        source = arview.confined(root, export["path"])
        if export.get("status") != "exported" or arview.sha256(source) != export["sha256"]:
            raise ValueError("Current export is unavailable or its bytes changed.")
        selected = _selected_materials(source, materials)
        _check_sidedness(root, export, selected, rows)
        bs = C.load_basis(root, basis)
        fronts = [rid for rid in bs.usable_refs() if bs.refs[rid].get("view_kind") == "front"]
        ref = C.ref_id(ref) if ref is not None else next(iter(fronts), None)
        if ref not in fronts:
            raise ValueError("Choose a usable frozen front photo camera; this tool never fits cameras.")
        photo = bs.photo(ref)
        if max(photo.shape[:2]) > 4096:
            raise ValueError("Front photo exceeds the production camera's 4096-pixel limit; refusing to shrink it.")
        camera = explicit_camera(bs.camera(ref), bs.frame, bs.shape(ref), name="candidate-front",
            background=look.border_colour(photo)["hex"], size_px=max(photo.shape[:2]), temple_pose=bs.pose(ref))
        directory = arview.output_dir(root, call_id + "-lens-candidates")
        records = []
        for number, candidate in enumerate(rows):
            target = directory / f"candidate-{number + 1}"
            target.mkdir()
            payload, patch = owner_lens_edit.patch_lens_glb(source,
                {row["material_index"]: candidate["values"] for row in selected})
            glb = target / "model.glb"
            glb.write_bytes(payload)
            copied = {**export, "path": str(glb), "sha256": patch["glb_sha256"]}
            worn, worn_report = _worn(root, copied, call_id + f"-lens-{number + 1}-worn")
            front, gaps, front_report = _front(root, copied, target, camera, photo, ref)
            records.append({**candidate, "export_path": str(glb), "export_sha256": patch["glb_sha256"],
                "apply_code": "values = " + repr(candidate["values"]) + "\n" + "\n".join(
                    f"g3d.apply_lens_candidate({row['name']!r}, source_double_sided={row['source_double_sided']!r}, **values)"
                    for row in selected),
                "patch": patch, "lens_colour_gap": gaps, "worn": worn, "front": front,
                "capture_reports": [worn_report["report"], front_report]})
        images = []
        for number, view in enumerate(records[0]["worn"]):
            paths = [row["worn"][view] for row in records]
            images.extend(_panels(paths, directory / f"worn-{number}.png", view, [row["label"] for row in records]))
        photo_path = directory / "photo.png"
        Image.fromarray(photo).save(photo_path)
        images.extend(_panels([str(photo_path), *[row["front"] for row in records]], directory / "front-pair.png",
            f"{ref}: frozen front photo-background comparison", ["Owner photo", *[row["label"] for row in records]]))
        if arview.sha256(source) != export["sha256"]:
            raise ValueError("Current export changed during the candidate comparison.")
        result = {**base, "status": "ok", "basis": bs.id, "ref": ref, "camera": camera,
            "basis_sha256": arview.sha256(bs.dir / "basis.json"), "cameras_sha256": bs.meta["cameras_json_sha256"],
            "photo_sha256": bs.refs[ref]["photo_sha256"], "materials": materials, "candidates": records,
            "apply_materials": [{"material": row["name"], "source_double_sided": row["source_double_sided"]}
                                for row in selected],
            "lens_colour_gap": [f"{row['label']}: {line}" for row in records for line in row["lens_colour_gap"]],
            "images": images, "source_unchanged": True,
            "lens_colour_judgement": look.LENS_COLOUR_JUDGEMENT,
            "apply": "Use execute_blender_code with the selected candidate's apply_code: it preserves exact values "
                     "and source exported sidedness. Then export and compare.",
            "image_cost": {"images": len(images), "bytes": sum(Path(row["path"]).stat().st_size for row in images),
                "estimated_image_tokens": sum(documented_image(*row["size_px"])["tokens"] for row in images),
                "method": "Existing offline high-detail 32px formula; excludes text/history/output and is not provider usage."}}
        report_path = directory / "report.json"
        procs.write_json_atomic(report_path, result)
        return {**result, "report": str(report_path), "report_sha256": arview.sha256(report_path)}
    except (C.NoBasis, C.UnusableReference, OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        return {**base, "status": "unavailable", "code": "E_LENS_CANDIDATES", "message": str(error)}


def _panels(paths, path, title, labels):
    images = []
    for source in paths:
        with Image.open(source) as opened:
            images.append(opened.convert("RGB"))
    _, tiles = native_panels.comparison(images, [0, 0, *images[0].size], path, title, labels)
    for tile in tiles:
        tile.update(kind="lens_candidates", view=title + ": " + " | ".join(labels))
    return tiles


def tool_for(scene, tool, call):
    @tool("compare_lens_candidates")
    async def compare_lens_candidates(ctx: ToolContext, candidates: list[LensCandidate], materials: list[str],
                                      ref: str | None = None, basis: str | None = None):
        """Compare 2–6 labelled lens material sets side by side, without changing the scene or formal best.
        Uses exact GLB copies, production AR native pixels: worn front and both ±50° sides, plus a frozen front
        photo-background pair and lens_colour_gap lines. Overflow is tiled, never shrunk. Measurements are
        information, not gates. All values are glTF: linear RGB tint; transmission (see-through), metallic
        (mirror), roughness and iridescence (coating factor) in 0..1; lens ior is 0 or >=1; film iridescence_ior
        >=1; iridescence_thickness_min/max in nonnegative nm. Texture bindings are preserved; linked controls
        cannot be applied with the scalar helper. Without a thickness texture only maximum is active.
        Apply the selected result's apply_code with execute_blender_code: g3d.apply_lens_candidate(material_name,
        source_double_sided=the_source_flag, **values), using every candidate field except label. The helper
        preserves exact exported scalars and source sidedness. Transitions that require the exporter's separate
        closed-outward sidedness correction refuse before capture; correct native culling and compare again.

        Args:
            candidates: Two to six complete sets with distinct short labels; no automatic choice or defaults.
            materials: Exact lens material names from scene_summary; select the lens body, not separate marks.
            ref: Usable frozen front photo id; null selects the first front. Never fits a camera.
            basis: Frozen camera basis id; null uses the latest basis.
        """
        return await call(ctx, "compare_lens_candidates", lambda cid: capture(scene.root,
            scene.seeing.export_head(call_id=cid), cid, candidates=candidates, materials=materials, ref=ref, basis=basis))
    return compare_lens_candidates
