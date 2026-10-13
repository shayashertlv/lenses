"""Build a private pose basis from declared, frozen per-photo calibration.

This copies measured cameras and masks without refitting them or guessing temple openings.
An explicit rigid-fit marker can prove an authored-pose identity transform; unknown missing poses refuse.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import math
import shutil

from PIL import Image

from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host.pose import parse_pose
from .lab import read, sha, write_json_atomic


def pose_metadata(rec: dict) -> dict:
    """Validate explicit opening data; absence is not a fitted zero-degree opening."""
    theta, axes = rec.get("theta_deg"), rec.get("hinge_axis_xz_mm")
    if theta is None and axes is None:
        raise ValueError("temple pose is missing; an explicit pose or verified rigid-fit provenance is required")
    if (not isinstance(theta, dict) or not isinstance(axes, dict) or not theta or set(theta) != set(axes)
            or set(theta) - {"+1", "-1"}):
        raise ValueError("opening angles and hinge axes must name the same explicit sides")
    parse_pose({k: {"theta_deg": v, "axis_xz_mm": axes[k]} for k, v in theta.items()})
    observed = rec.get("theta_observed") or {}
    if not isinstance(observed, dict) or set(observed) - set(theta) or any(type(v) is not bool for v in observed.values()):
        raise ValueError("theta_observed must contain boolean values for declared sides")
    provenance = rec.get("pose_provenance")
    if isinstance(provenance, dict) and provenance.get("mode") == "as_authored":
        if (provenance.get("opening_calibrated") is not False or set(theta) != {"+1", "-1"}
                or any(float(v) != 0. for v in theta.values()) or observed != {"+1": False, "-1": False}):
            raise ValueError("an authored rigid pose must be an uncalibrated, unobserved two-sided identity")
        return deepcopy(provenance)
    return {"mode": "declared", "opening_calibrated": True,
            "theta_observed": deepcopy(observed)}


def _inside(path: Path, root: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("frozen basis asset escapes its directory")
    return path


def build(source: C.Basis, root: Path, photos: list[dict], *, call_id: str,
          captured_glb: Path, captured_sha256: str) -> C.Basis:
    """Copy a complete frozen basis to a new package, retaining original calibration lineage.

    ``photos`` contain explicit basis_ref and package-relative ``path`` below inputs/.
    Camera/frame/ROI/pose values and matte bytes are retained exactly. Only file locations change.
    """
    root = Path(root).resolve()
    refs = [p["basis_ref"] for p in photos if p["role"] != "reference"]
    if not refs or len(set(refs)) != len(refs) or set(refs) != set(source.refs):
        raise ValueError("the stage needs a complete frozen basis for exactly its calibrated photo IDs")
    if source.unusable_refs():
        raise ValueError(f"the temple stage needs successful cameras for every calibrated photo: {source.unusable_refs()}")
    source_json = source.dir / C.CAMERAS_JSON
    if sha(source_json) != source.meta.get("cameras_json_sha256"):
        raise ValueError("the source cameras.json changed after the basis was frozen")
    doc = read(source_json)
    from . import rigid_pose
    pose_updates = rigid_pose.materialize(source, captured_glb, captured_sha256)
    meta = deepcopy(source.meta)
    bid = C.next_basis_id(root)
    d = C.basis_root(root) / bid
    d.mkdir(parents=True)
    bdir = root / "basis"
    bdir.mkdir()
    images, frozen, modes = {}, {}, {}
    for index, row in enumerate(photos):
        if row["role"] == "reference":
            continue
        rid = row["basis_ref"]
        old = source.refs[rid]
        rec = deepcopy(doc["images"].get(old["image"]))
        if not rec or rec.get("camera") != old.get("camera"):
            raise ValueError(f"{rid}: camera disagrees with the frozen source")
        if rec.get("status") not in (None, "ok") or rec.get("refusal"):
            raise ValueError(f"{rid}: the source camera fit did not succeed")
        if not rec.get("camera") or not all(isinstance(v, (int, float)) and math.isfinite(v)
                                            for v in rec["camera"].values()):
            raise ValueError(f"{rid}: a complete finite fitted camera is required")
        source.camera(rid)  # Validate the actual camera field schema too.
        if row["role"] == "target" and source.colourway(rid) != "target":
            raise ValueError(f"{rid}: target/colourway role differs from the frozen basis")
        if row["role"] == "colourway" and source.colourway(rid) == "target":
            raise ValueError(f"{rid}: colourway role differs from the frozen basis")
        local = _inside(root / "inputs" / row["path"], root / "inputs")
        if sha(local) != old["photo_sha256"] or sha(source.photo_path(rid)) != old["photo_sha256"]:
            raise ValueError(f"{rid}: photo differs from the frozen camera's input")
        matte = _inside(source.dir / old["matte"], source.dir)
        if sha(matte) != old["matte_sha256"]:
            raise ValueError(f"{rid}: frozen matte changed")
        with Image.open(matte) as im:
            if list(reversed(im.size)) != old["shape"]:
                raise ValueError(f"{rid}: frozen matte dimensions disagree")
        for key in ("theta_deg", "hinge_axis_xz_mm", "theta_observed"):
            if old.get(key) != rec.get(key):
                raise ValueError(f"{rid}: opening metadata differs from the frozen source")
        patch = pose_updates.get(rid, {})
        mode = pose_metadata({**old, **patch})
        modes[rid] = mode
        mrel = f"mattes/photo-{index:03d}.png"
        for folder in (d, bdir):
            (folder / mrel).parent.mkdir(exist_ok=True)
            shutil.copyfile(matte, folder / mrel)
        updated = deepcopy(old)
        updated.update(deepcopy(patch))
        local_name = Path(row["path"]).name
        updated.update(image=local_name, source_image=old["image"],
                       photo=local.relative_to(root).as_posix(), matte=mrel, overlay=None,
                       pose_provenance=mode)
        frozen[rid] = updated
        rec.update(deepcopy(patch))
        rec.update(image=str(local), source_image=old["image"], matte_mode="supplied", matte_file=mrel,
                   matte_file_sha256=old["matte_sha256"], pose_provenance=mode)
        images[local_name] = rec
    copied_doc = {**doc, "images": images}
    write_json_atomic(d / C.CAMERAS_JSON, copied_doc)
    write_json_atomic(bdir / C.CAMERAS_JSON, copied_doc)
    meta.update(id=bid, k=int(bid.split("-")[1]), refs=frozen, call_id=call_id,
                reason="private temple-stage copy of the active frozen camera basis",
                cameras_json_sha256=sha(d / C.CAMERAS_JSON),
                parent_basis={"id": source.id, "sha256": sha(source.dir / "basis.json"),
                              "cameras_json_sha256": sha(source_json)}, pose_provenance=modes)
    write_json_atomic(d / "basis.json", meta)
    return C.Basis(d, root)
