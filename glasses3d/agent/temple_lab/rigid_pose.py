"""Explicit identity temple deltas for cameras fitted against a fixed authored mesh.

This never estimates a real opening angle or treats missing pose as evidence of zero. Only a hash-bound rigid
fitter marker permits materialisation; arbitrary imported cameras still need an explicit pose. Hinge axes are
geometric anchors from the captured current GLB, recorded separately from the camera's original model lineage.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from glasses3d.g3d_host import compare as C, pose as PO, hinge_axes
from glasses3d.g3d_host.fit_views import rigid_fit_marker


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def derive_axes(captured_glb, captured_sha256) -> dict:
    """Use the existing temple-body and knuckle rules, with no guessed or photo-measured hinge anchors."""
    if not _digest(captured_sha256):
        raise ValueError("captured temple source GLB differs from its SHA-256")
    derived = hinge_axes.derive_axes(captured_glb, captured_sha256, require_both=True)
    return {k:derived[k] for k in ("hinge_axis_xz_mm", "axis_source")}


def materialize(source_basis, captured_glb, captured_sha256) -> dict:
    """Patches for missing-pose refs only; declared pose survives verbatim, unknown rigid lineage refuses."""
    missing = []
    for rid, old in source_basis.refs.items():
        theta, axes = old.get("theta_deg"), old.get("hinge_axis_xz_mm")
        if theta is None and axes is None:
            missing.append(rid)
            continue
        if not isinstance(theta, dict) or not isinstance(axes, dict) or not theta or set(theta) != set(axes):
            raise ValueError(f"{rid}: incomplete explicit temple pose")
        PO.parse_pose({k: {"theta_deg": v, "axis_xz_mm": axes[k]} for k, v in theta.items()})
    if not missing:
        return {}
    raw_path = source_basis.dir / C.CAMERAS_JSON
    raw_bytes = raw_path.read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != source_basis.meta.get("cameras_json_sha256"):
        raise ValueError("rigid source cameras.json differs from its frozen SHA-256")
    doc = json.loads(raw_bytes)
    model = doc.get("model") or {}
    model_sha = model.get("sha256")
    if not _digest(model_sha) or model_sha != (source_basis.meta.get("model") or {}).get("sha256"):
        raise ValueError("rigid camera source model SHA-256 is missing or differs from the frozen basis")
    if (model.get("frame") != source_basis.meta.get("frame")
            or model.get("front_pivot_mm") != source_basis.meta.get("front_pivot_mm")):
        raise ValueError("rigid camera frame or pivot differs from the frozen basis")
    markers = {}
    for rid in missing:
        old = source_basis.refs[rid]
        raw = (doc.get("images") or {}).get(old["image"]) or {}
        if raw.get("status") != "ok" or raw.get("refusal"):
            raise ValueError(f"{rid}: a successful rigid camera fit is required")
        if (raw.get("camera") != old.get("camera") or raw.get("image_sha256") != old.get("photo_sha256")
                or not _digest(old.get("photo_sha256"))):
            raise ValueError(f"{rid}: rigid camera or photo differs from the frozen input")
        if raw.get("theta_deg") is not None or raw.get("hinge_axis_xz_mm") is not None:
            raise ValueError(f"{rid}: source pose differs from the frozen basis")
        expected = rigid_fit_marker(model_sha, old["photo_sha256"], old["camera"])
        marker = raw.get("rigid_fit")
        if marker != expected or any(type(value) not in (int, float) for value in
                                    (marker or {}).get("temple_delta_deg", {}).values()):
            raise ValueError(f"{rid}: no verified identity delta from the rigid camera fitter")
        source_basis.camera(rid)
        markers[rid] = marker
    derived = derive_axes(captured_glb, captured_sha256)
    axes = derived["hinge_axis_xz_mm"]
    if set(axes) != {"+1", "-1"}:
        raise ValueError("derived rigid-pose axes must describe both temple sides")
    PO.parse_pose({k: {"theta_deg": 0.0, "axis_xz_mm": v} for k, v in axes.items()})
    return {rid: {"theta_deg": {"+1": 0.0, "-1": 0.0}, "hinge_axis_xz_mm": deepcopy(axes),
                  "theta_observed": {"+1": False, "-1": False},
                  "pose_provenance": {"mode": "as_authored", "opening_calibrated": False,
                                      "rigid_fit": deepcopy(markers[rid]),
                                      "axis_source": deepcopy(derived["axis_source"]),
                                      "meaning": "identity delta from the rigid fit's authored model pose; "
                                                 "real hinge opening was not measured"}}
            for rid in missing}
