"""Decision-profile companion for choosing validated, already fitted camera alternatives."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid
from typing import Annotated

from agents.tool_context import ToolContext
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...g3d_host import camera_choice, compare as C, fitmesh, fit_views, pose


class OrientationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    photo: str = Field(min_length=1, description="Photo id or original filename in the named frozen basis.")
    candidate: str = Field(min_length=1, description="Exact id from that photo's camera_ambiguity.candidates.")


def tool_for(scene, tool, call):
    @tool("choose_camera_orientation")
    async def choose_camera_orientation(ctx: ToolContext, reason: str, basis: str,
                                        choices: Annotated[list[OrientationChoice], Field(min_length=1)]):
        """Choose reviewed orientation alternatives from fit_cameras without a full refit.

        Inspect the candidate overlays and visible front/temple occlusions first. Supply the exact frozen basis
        and candidate ids; reason records the photo evidence behind your choice. Every choice is checked against
        the unchanged fitted model, photos, matte, view labels/eye position, physical arm opening, near plane and
        camera-overlap gate using full-scene occlusion. All choices succeed together in a new immutable basis;
        stale evidence or any failed check refuses. Unselected photos retain their exact frozen cameras/mattes.
        The candidate's fitted arm pose is retained. A camera choice does not measure photographic arm opening;
        opening observations conditional on another camera become unobserved. See photographic_arm_opening.
        Returns native overlay paths readable with read_image, full diagnostics and new basis id. This is an
        explicit choice, not proof of independent calibration. Run a new comparison on the chosen basis.
        """
        return await call(ctx, "choose_camera_orientation", lambda cid: choose(
            scene, reason, basis, [item.model_dump() for item in choices], cid))
    return choose_camera_orientation


def choose(scene, reason: str, basis: str, choices: list[dict], call_id: str):
    """Install one new immutable basis, with every requested choice checked before publication."""
    started = time.perf_counter()
    args = {"reason": reason, "basis": basis, "choices": choices}
    try:
        if getattr(scene, "owner_edit", None):
            raise ValueError("Keep the frozen edit cameras; orientation choices are unavailable in owner edit rounds")
        if not isinstance(reason, str) or not reason.strip() or not basis or not choices:
            raise ValueError("reason, an explicit basis id and at least one choice are required")
        previous = C.load_basis(scene.root, basis)
        selected = {}
        for item in choices:
            rid = C.ref_id(item["photo"])
            if rid not in previous.refs or rid in selected:
                raise ValueError(f"unknown or repeated photo {item['photo']!r}")
            selected[rid] = item["candidate"]
        export = scene.seeing.export_head(call_id)
        if export.get("status") != "exported" or export.get("drift"):
            raise ValueError("current scene does not have a verified export")
        if export["sha256"] != previous.meta["model"]["sha256"] or C.sha256_file(export["path"]) != export["sha256"]:
            raise ValueError("orientation evidence is stale: current export differs from the fitted model; fit cameras again")
        mesh = fitmesh.load_fit_mesh(export["path"], skip=previous.skip, frame=previous.frame)
        pivot = previous.frame.to_norm(previous.pivot_mm[None])[0]
        roles = pose.part_roles(export["path"])
        doc = json.loads((previous.dir / C.CAMERAS_JSON).read_text(encoding="utf-8"))
        if doc["model"]["sha256"] != export["sha256"]:
            raise ValueError("orientation evidence is stale: fitted source model differs from current export")
        replacements = {}
        for rid, candidate in selected.items():
            rec = previous.refs[rid]
            replacements[rid] = camera_choice.select(doc["images"][rec["image"]], candidate, mesh, roles,
                previous.matte(rid), pivot, export["sha256"], rec["photo_sha256"])
        # Freeze out of the published basis catalogue; any refusal leaves its newest basis unchanged.
        new_id = C.next_basis_id(scene.root)
        staging = Path(scene.root) / "work" / "camera_choices" / uuid.uuid4().hex / new_id
        staging.mkdir(parents=True)
        for source in previous.dir.rglob("*"):
            if source.is_file() and source.name not in ("basis.json", C.CAMERAS_JSON):
                if source.is_symlink() or not source.resolve().is_relative_to(previous.dir.resolve()):
                    raise ValueError("camera evidence escapes its frozen basis")
                target = staging / source.relative_to(previous.dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        photos = {}
        for rid, rec in previous.refs.items():
            name = rec["image"]
            photos[name] = previous.photo_path(rid)
            if rid in replacements:
                row, mask = replacements[rid]
                overlay = staging / f"overlay_{rid}.png"
                Image.fromarray(fit_views.overlay_image(previous.photo(rid), previous.matte(rid), mask)).save(overlay)
                row["overlay"] = overlay.name
                row["camera_ambiguity"]["choice"].update(source_basis=basis, reason=reason)
                doc["images"][name] = row
            if rec.get("matte"):
                doc["images"][name].update(matte_mode="supplied", matte_file=rec["matte"],
                                           matte_file_sha256=rec["matte_sha256"])
        C.write_json(staging / C.CAMERAS_JSON, doc)
        C.freeze_basis(scene.root, staging, photos, reason=reason, call_id=call_id,
                       source="choose_camera_orientation", model={"glb_path": export["path"],
                       "glb_sha256": export["sha256"], "fingerprint": export["fingerprint"],
                       "checkpoint": export["checkpoint"]})
        if C.sha256_file(export["path"]) != export["sha256"]:
            raise ValueError("export changed during orientation validation")
        previous._verify_evidence()
        destination = C.basis_root(scene.root) / new_id
        staging.rename(destination)
        installed = C.load_basis(scene.root, new_id)
        out = {"status": "ok", "call_id": call_id, "basis": new_id, "source_basis": basis,
               "checkpoint": export["checkpoint"], "glb_sha256": export["sha256"],
               "refit": False, "changed_refs": list(selected),
               "refs": {rid: scene.seeing._fit_ref_summary(rec, usable=installed.is_usable(rid),
                        basis_path=destination.relative_to(scene.root).as_posix())
                        for rid, rec in installed.refs.items()},
               "seconds": round(time.perf_counter() - started, 3)}
    except (ValueError, LookupError, TypeError, OSError) as exc:
        out = {"status": "refused", "code": "E_CAMERA_CHOICE", "call_id": call_id,
               "message": f"{type(exc).__name__}: {exc}"}
    scene.s.record_call("choose_camera_orientation", call_id, args, out)
    return out
