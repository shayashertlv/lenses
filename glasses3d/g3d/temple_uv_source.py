"""Frozen UV sources for temple revisions, independent of the original undo mesh.

Planning only reads. A successful write retains at most one additional source mesh for externally edited UVs
or geometry; later revisions sample that generation rather than successively resampling their own output.
Deleting every UV layer is a generation too. The original pre-temple mesh remains the authority for unfacet.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

import bpy
import numpy as np

from .temple.spec import TempleError


PROP_TEMPLE = "g3d_temple"
PROP_BACKUP = "g3d_temple_backup"
SOURCE_SUFFIX = " | g3d UV source"


def _array(collection, attribute, width, dtype):
    values = np.empty(len(collection) * width, dtype)
    collection.foreach_get(attribute, values)
    return values


def fingerprint(ob, origin) -> str:
    """Exact authored mesh/UV/frame identity; excludes normals and unrelated material/shading edits."""
    mesh = ob.data
    h = hashlib.sha256()

    def add(label, value):
        h.update(label.encode("ascii") + b"\0")
        if isinstance(value, np.ndarray):
            h.update(str(value.shape).encode("ascii") + b":" + value.dtype.str.encode("ascii") + b"\0")
            h.update(np.ascontiguousarray(value).tobytes())
        else:
            h.update(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        h.update(b"\0")

    add("vertices", _array(mesh.vertices, "co", 3, np.float32))
    add("edges", _array(mesh.edges, "vertices", 2, np.int32))
    add("loops", _array(mesh.loops, "vertex_index", 1, np.int32))
    add("polygon_start", _array(mesh.polygons, "loop_start", 1, np.int32))
    add("polygon_total", _array(mesh.polygons, "loop_total", 1, np.int32))
    add("uv_active", mesh.uv_layers.active_index)
    for index, layer in enumerate(mesh.uv_layers):
        add(f"uv_meta_{index}", [layer.name, layer.active_render, layer.active_clone])
        add(f"uv_values_{index}", _array(layer.data, "uv", 2, np.float32))
    add("matrix", np.asarray(ob.matrix_world, np.float64))
    add("origin", np.asarray(origin, np.float64))
    return h.hexdigest()


def _frame(ob, origin, default_hinge, written=None):
    hinge = deepcopy(dict(default_hinge))
    hinge["opening_deg"] = 0.0  # an untagged source is charted in its current world pose
    previous = ob.get(PROP_TEMPLE)
    if previous:
        previous = json.loads(previous) if isinstance(previous, str) else dict(previous)
        hinge = deepcopy(previous["canonical_spec"]["hinge"])
        hinge["axis_xz_mm"] = deepcopy(previous.get("hinge_axis_xz_mm", hinge.get("axis_xz_mm")))
    matrix = np.asarray(ob.matrix_world, float)
    origin = np.asarray(origin, float)
    if written is not None and hinge == written["hinge"] and len(ob.data.uv_layers):
        # Object edits do not update the temple's authored extras. Carry its chart's hinge with the object,
        # including asset-origin changes, rather than sampling current UVs against a stale hinge location.
        delta = matrix @ np.linalg.inv(np.asarray(written["matrix_world"], float))
        scale = float(np.trace(delta[:3, :3]) / 3)
        if scale <= 0 or not np.allclose(delta[:3, :3], np.eye(3) * scale, rtol=1e-6, atol=1e-6):
            raise TempleError("E_TARGET", "the temple UV source frame changed by a rotation, reflection or "
                              "nonuniform scale; only positive uniform scale and translation can rebase "
                              "the existing hinge chart")
        shift = delta[:3, :3] @ np.asarray(written["origin"], float) + delta[:3, 3] - origin
        hinge["z_mm"] = scale * hinge["z_mm"] + float(shift[2])
        axis = hinge.get("axis_xz_mm")
        if axis is not None:
            side = -1 if previous and previous.get("side") == "-x" else 1
            hinge["axis_xz_mm"] = [scale * axis[0] + side * float(shift[0]),
                                   scale * axis[1] + float(shift[2])]
    return {"hinge": hinge, "matrix_world": matrix.tolist(), "origin": origin.tolist()}


def select_source(ob, backup, origin, default_hinge):
    """Return (mesh, saved chart frame, capture_current) without creating or changing a datablock.

    The saved output fingerprint proves whether the current mesh is still a result of this source generation.
    Legacy backups have no such proof, so their current authored bytes become the next source on a write.
    """
    if not backup:
        return ob.data, _frame(ob, origin, default_hinge), False
    original = bpy.data.meshes.get(backup.get("mesh", ""))
    if original is None:
        raise TempleError("E_TARGET", "the original temple backup mesh is missing")
    current = fingerprint(ob, origin)
    if backup.get("uv_written_fingerprint") == current:
        name = backup.get("uv_mesh") or backup["mesh"]
        source = bpy.data.meshes.get(name)
        if source is None:
            raise TempleError("E_TARGET", "the frozen temple UV source mesh is missing")
        frame = backup.get("uv_source")
        if not isinstance(frame, dict):
            raise TempleError("E_TARGET", "the frozen temple UV source frame is missing")
        return source, deepcopy(frame), False
    return ob.data, _frame(ob, origin, default_hinge, backup.get("uv_written_frame")), True


@dataclass
class SourceToken:
    backup: dict
    before: dict
    created: object | None
    obsolete: str | None
    original: str | None
    completed: bool = False


def retain_source(ob, backup, old_mesh, frame, capture_current, first=False):
    """Commit a planned source selection before the old current mesh can be renamed or removed.

    ``first`` is called after the original undo mesh has acquired its retained name. A later generation gets a
    copy, so the caller can freely rename/delete old_mesh while this source name stays durable in backup JSON.
    Call finalize_source only after the whole scene write succeeds; rollback_source restores this dict on failure.
    """
    token = SourceToken(backup, deepcopy(backup), None, None, backup.get("mesh"))
    try:
        if first:
            if bpy.data.meshes.get(backup.get("mesh", "")) != old_mesh:
                raise TempleError("E_TARGET", "first UV source must be the retained original temple mesh")
            backup["uv_mesh"] = backup["mesh"]
            backup["uv_source"] = deepcopy(frame)
        elif capture_current:
            previous = backup.get("uv_mesh")
            frozen = old_mesh.copy()
            token.created = frozen
            frozen.name = old_mesh.name + SOURCE_SUFFIX
            frozen.use_fake_user = True
            backup["uv_mesh"] = frozen.name
            backup["uv_source"] = deepcopy(frame)
            if previous and previous != backup.get("mesh") and previous != frozen.name:
                token.obsolete = previous
        elif "uv_source" not in backup:
            raise TempleError("E_TARGET", "a stable UV generation must have its source frame")
        return token
    except BaseException:
        rollback_source(token)
        raise


def record_written(ob, backup, origin):
    """Record the exact result after geometry and UV writes, using the current asset origin."""
    backup["uv_written_fingerprint"] = fingerprint(ob, origin)
    backup["uv_written_frame"] = _frame(ob, origin, {})


def _discard(mesh):
    if mesh is None:
        return
    # A retained source is normally fake-user-only. Never unlink another object's live datablock.
    try:
        for ob in bpy.data.objects:
            raw = ob.get(PROP_BACKUP)
            if not raw:
                continue
            try:
                record = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict) and mesh.name in (record.get("mesh"), record.get("uv_mesh")):
                return  # JSON references do not contribute to Blender's mesh.users count.
        mesh.use_fake_user = False
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    except ReferenceError:
        pass  # the caller's unfacet cleanup may already have removed this uncommitted source


def finalize_source(token):
    """Drop an obsolete extra generation only after the caller has committed the replacement scene."""
    if token is None or token.completed:
        return
    if token.obsolete and token.obsolete != token.original:
        _discard(bpy.data.meshes.get(token.obsolete))
    token.completed = True


def rollback_source(token):
    """Undo the source-dictionary change and its uncommitted snapshot; original undo bytes are untouched."""
    if token is None or token.completed:
        return
    token.backup.clear()
    token.backup.update(deepcopy(token.before))
    if token.created is not None:
        _discard(token.created)
    token.completed = True


def cleanup_sources(backup):
    """Called by unfacet after restoring the original mesh; remove only the additional frozen UV generation."""
    name = backup.get("uv_mesh")
    if name and name != backup.get("mesh"):
        _discard(bpy.data.meshes.get(name))
