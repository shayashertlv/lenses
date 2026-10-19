"""Scene fingerprint, protection and .blend checkpoints, inside Blender 5.2 (glasses3d/plan/archive/20261019/WP2_DESIGN.md §5).

`scene_state()` is the component-digest form of `export_glb.snapshot` (which stays untouched for the exporter's own
scene-unchanged check): scene props and units, view layers, collections, every object (matrix, visibility, parenting,
modifiers, constraints, material slots, custom props), every mesh (`export_glb._mesh_digest`: positions, topology,
every attribute), curves and text, materials and node groups (node settings, socket values, links, and nonempty
group interfaces including socket names/types/defaults), images (packed
bytes), and the other ID types. `fingerprint(state)` is the sha256 of its canonical JSON. Excluded because they are
not scene content and change across a save and reopen (prototype defect 1): `bpy.data.filepath`, images with source
VIEWER (Render Result / Viewer Node), `use_fake_user` flags and user counts. Automatic texture-space bounds are
evaluation caches, so only manually authored texture-space bounds are hashed. The per-object summaries (vertex and
triangle counts, bbox in mm) ride along for `scene_delta` and are not hashed.

`protect()` runs before every checkpoint: it packs every generated, dirty (pixel-edited) or external image and every
external font (a pixel edit on an unpacked generated image is lost on save: defect 2) and gives every zero-user
datablock a fake user (every save drops them: defect 3, 9/51 Studio replays failed on unassigned materials).

`save(path)` writes `path` through `<stem>.part.blend` with `save_as_mainfile(copy=True, compress=True)` (the active
file never changes), moves it into place with the Windows sharing-violation retry, makes it read-only and hashes it.
`restore(path)` opens the read-only checkpoint itself (`open_mainfile(load_ui=False, use_scripts=False)`); later
`copy=True` saves leave it untouched. No code path here calls `save_mainfile`.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

from .export_glb import _custom_props, _mesh_digest, _plain, _tree_state as _export_tree_state

SCHEMA = "g3d.scene_state/1"
RENDER_SCENE = "g3d_render"            # the temporary scene of the render method (never part of the state)
#: exactly the trusted temporary scenes (a prefix would let a snippet hide a scene of its own from the fingerprint)
TRUSTED_TEMP_SCENES = frozenset({RENDER_SCENE, RENDER_SCENE + "_probe"})
REPLACE_DELAYS_S = (0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6)
EXCLUDED_PROPS = frozenset({
    "rna_type", "execution_time", "is_active", "show_expanded", "is_override_data", "users", "tag", "is_evaluated",
    "original", "session_uid", "preview", "is_editable", "is_runtime_data", "dimensions", "select",
    "is_override_data_collection", "use_fake_user", "use_extra_user", "is_missing", "is_library_indirect",
    "library_weak_reference", "is_embedded_data", "name_full", "id_type", "is_dirty", "bindcode", "is_float",
    "has_data", "is_editmode", "mode"})
OTHER_IDS = ("lights", "cameras", "worlds", "textures", "fonts", "actions", "armatures", "lattices", "metaballs",
             "grease_pencils", "particles", "volumes", "pointclouds", "hair_curves", "speakers", "lightprobes")
#: Every ID collection in scene_state must survive saving even when it has no object/material users. Do not
#: retain just the common geometry types: Blender drops orphan armatures, lattices, etc. by the same rule.
KEEP = tuple(dict.fromkeys(("materials", "meshes", "node_groups", "images", "curves", "objects", "collections")
                          + OTHER_IDS))


def _h(value) -> str:
    return hashlib.sha256(repr(value).encode("utf-8", "replace")).hexdigest()


def _tree_state(tree, seen: set) -> tuple:
    state = _export_tree_state(tree, seen)
    if not state:
        return state
    # Value/RGB nodes author their value on an output socket, rather than an input socket. These persisted
    # values are scene content too, including in an unassigned (fake-user) node group.
    outputs = tuple((n.name, tuple(_plain(getattr(socket, "default_value", None)) for socket in n.outputs))
                    for n in tree.nodes)
    return state, outputs, _custom_props(tree)


def _rna(struct) -> tuple:
    """Every simple RNA property of `struct` (pointers by name), minus the excluded ones."""
    out = []
    for prop in struct.bl_rna.properties:
        ident = prop.identifier
        if ident in EXCLUDED_PROPS or prop.type == "COLLECTION":
            continue
        # Blender recalculates these bounds on evaluation and file load, including for orphan TextCurves left
        # by conversion to mesh. They are not authored content while auto mode is enabled. Keep the mode flag
        # and manual bounds: changing either changes the rendered material mapping and must change the hash.
        if ident in {"texspace_location", "texspace_size"} and getattr(struct, "use_auto_texspace", False):
            continue
        try:
            value = getattr(struct, ident)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if struct.bl_rna.identifier == "ParticleSettings" and ident == "trail_count":
            # New particle settings expose an invalid factory zero, but Blender's file loader clamps it to the
            # RNA minimum of one. Hash that persisted value; authored valid trail counts remain distinguishable.
            value = max(prop.hard_min, value)
        if struct.bl_rna.identifier == "Volume" and ident == "filepath":
            # Save-as remaps volume paths relative to the checkpoint directory. Hash the target, not its
            # spelling. Blender also serializes the invalid empty factory path as a path to the drive root.
            # Preserve every nonempty file target; do not omit filepath just because no grids are loaded.
            value = os.path.normcase(os.path.normpath(bpy.path.abspath(value) if value else os.path.abspath(os.sep)))
        if prop.type == "POINTER":
            value = getattr(value, "name", None) if value is not None else None
            if not isinstance(value, (str, type(None))):
                continue
        out.append((ident, _plain(value)))
    return tuple(out)


def _bbox_mm(obj) -> list | None:
    try:
        corners = np.array([tuple(obj.matrix_world @ Vector(c)) for c in obj.bound_box], float)
    except (TypeError, ValueError, AttributeError):
        return None
    if not corners.size or not np.isfinite(corners).all():
        return None
    return [np.round(corners.min(0), 4).tolist(), np.round(corners.max(0), 4).tolist()]


def _mesh_counts(me) -> tuple[int, int]:
    n = len(me.polygons)
    if not n:
        return len(me.vertices), 0
    tot = np.zeros(n, np.int32)
    me.polygons.foreach_get("loop_total", tot)
    return len(me.vertices), int((tot - 2).clip(min=0).sum())


def _curve_digest(cu) -> str:
    splines = []
    for sp in cu.splines:
        pts = tuple((_plain(p.co), p.radius, p.tilt, p.weight_softbody) for p in sp.points)
        bez = tuple((_plain(p.co), _plain(p.handle_left), _plain(p.handle_right), p.handle_left_type,
                     p.handle_right_type, p.radius, p.tilt) for p in sp.bezier_points)
        splines.append((_rna(sp), pts, bez))
    extra = ()
    if cu.bl_rna.identifier == "TextCurve":
        extra = (tuple(_rna(b) for b in cu.body_format), tuple(_rna(tb) for tb in cu.text_boxes))
    return _h((_rna(cu), _custom_props(cu), tuple(m.name if m else None for m in cu.materials), tuple(splines), extra))


def _image_digest(im) -> str:
    packed = None
    if im.packed_file is not None:
        packed = hashlib.sha256(bytes(im.packed_file.data)).hexdigest()
    gen = (im.generated_type, tuple(im.generated_color), im.generated_width, im.generated_height,
           im.use_generated_float) if im.source == "GENERATED" else None
    return _h((im.source, im.filepath if packed is None else None, packed, gen, tuple(im.size) if packed is None else None,
               im.colorspace_settings.name, im.alpha_mode, im.file_format if packed else None, _custom_props(im)))


def _font_digest(f) -> str:
    packed = hashlib.sha256(bytes(f.packed_file.data)).hexdigest() if f.packed_file is not None else None
    return _h((f.filepath if packed is None else None, packed, _custom_props(f)))


def _object_digest(obj) -> str:
    return _h((obj.type, obj.data.name if obj.data else None, tuple(tuple(r) for r in obj.matrix_world),
               tuple(tuple(r) for r in obj.matrix_parent_inverse), obj.parent.name if obj.parent else None,
               obj.parent_type, obj.parent_bone, obj.hide_render, obj.hide_viewport, obj.hide_select,
               obj.display_type, obj.instance_type,
               obj.instance_collection.name if obj.instance_collection else None, obj.visible_camera,
               obj.visible_shadow, obj.visible_diffuse, obj.visible_glossy, obj.visible_transmission, obj.is_holdout,
               obj.pass_index, _custom_props(obj),
               tuple((m.name, m.type, _rna(m)) for m in obj.modifiers),
               tuple((c.name, c.type, _rna(c)) for c in obj.constraints),
               tuple((s.link, s.material.name if s.material else None) for s in obj.material_slots),
               tuple(sorted(c.name for c in obj.users_collection if c.name not in TRUSTED_TEMP_SCENES))))


def _layer_collections(lc, acc) -> None:
    acc.append((lc.name, lc.exclude, lc.hide_viewport, lc.holdout, lc.indirect_only))
    for ch in lc.children:
        _layer_collections(ch, acc)


def _scene_digest(scene) -> str:
    us, r, vs = scene.unit_settings, scene.render, scene.view_settings
    return _h((scene.name, _custom_props(scene), (us.system, us.scale_length, us.length_unit, us.system_rotation),
               scene.camera.name if scene.camera else None, scene.world.name if scene.world else None,
               (scene.frame_start, scene.frame_end, scene.frame_current),
               (r.engine, r.resolution_x, r.resolution_y, r.resolution_percentage, r.film_transparent),
               (vs.view_transform, vs.look, vs.exposure, vs.gamma), scene.display_settings.display_device,
               tuple(sorted(o.name for o in scene.collection.objects)),
               tuple(sorted(c.name for c in scene.collection.children))))


def _view_layers_digest(scene) -> str:
    layers = []
    for vl in scene.view_layers:
        acc: list = []
        _layer_collections(vl.layer_collection, acc)
        layers.append((vl.name, vl.use, tuple(acc), vl.material_override.name if vl.material_override else None))
    return _h(tuple(layers))


def user_scenes() -> list:
    return [s for s in bpy.data.scenes if s.name not in TRUSTED_TEMP_SCENES]


def scene_state() -> dict:
    """Component digests of the whole file's content (see module doc) plus per-object summaries."""
    try:
        bpy.context.view_layer.update()        # matrix_world of objects created this call is stale until evaluated
    except (AttributeError, RuntimeError):
        pass
    objects, summary = {}, {}
    for obj in bpy.data.objects:
        objects[obj.name] = _object_digest(obj)
        info = {"type": obj.type, "data": obj.data.name if obj.data else None, "bbox_mm": _bbox_mm(obj),
                "partRole": obj.get("partRole") if isinstance(obj.get("partRole"), str) else None,
                "in_scene": bool(obj.users_scene),
                "materials": [s.material.name if s.material else None for s in obj.material_slots]}
        if obj.type == "MESH" and obj.data is not None:
            info["vertices"], info["triangles"] = _mesh_counts(obj.data)
        summary[obj.name] = info
    seen: set = set()
    materials = {m.name: _h((_custom_props(m), _rna(m), _tree_state(m.node_tree, seen))) for m in bpy.data.materials}
    groups = {g.name: _h(_tree_state(g, seen)) for g in bpy.data.node_groups}
    other = {}
    for coll in OTHER_IDS:
        ids = getattr(bpy.data, coll, None)
        if ids is None:
            continue
        if coll == "fonts":
            other[coll] = {f.name: _font_digest(f) for f in ids}
        elif coll == "worlds":
            other[coll] = {w.name: _h((_rna(w), _custom_props(w), _tree_state(w.node_tree, seen))) for w in ids}
        elif coll == "lights":
            other[coll] = {lt.name: _h((_rna(lt), _custom_props(lt), _tree_state(lt.node_tree, seen))) for lt in ids}
        elif coll == "actions":
            other[coll] = {
                a.name: _h((_rna(a), _custom_props(a),
                            tuple((fc.data_path, fc.array_index, tuple(tuple(k.co) for k in fc.keyframe_points))
                                  for fc in getattr(a, "fcurves", ()))))
                for a in ids}
        else:
            other[coll] = {i.name: _h((_rna(i), _custom_props(i))) for i in ids}
    counts = {name: len(getattr(bpy.data, name)) for name in sorted(set(KEEP) | set(OTHER_IDS) | {"scenes"})
              if hasattr(bpy.data, name)}
    counts["scenes"] = len(user_scenes())
    counts["images"] = sum(1 for im in bpy.data.images if im.source != "VIEWER")
    state = {
        "schema": SCHEMA,
        "scenes": {s.name: _scene_digest(s) for s in user_scenes()},
        "view_layers": {s.name: _view_layers_digest(s) for s in user_scenes()},
        "collections": {c.name: _h((c.hide_render, c.hide_viewport, c.hide_select, _custom_props(c),
                                    tuple(sorted(o.name for o in c.objects)),
                                    tuple(sorted(ch.name for ch in c.children)))) for c in bpy.data.collections},
        "objects": objects,
        "meshes": {me.name: _mesh_digest(me) for me in bpy.data.meshes},
        "curves": {cu.name: _curve_digest(cu) for cu in bpy.data.curves},
        "materials": materials,
        "node_groups": groups,
        "images": {im.name: _image_digest(im) for im in bpy.data.images if im.source != "VIEWER"},
        "other": other,
        "counts": counts,
    }
    state["summary"] = summary
    return state


def fingerprint(state: dict | None = None) -> str:
    state = scene_state() if state is None else state
    hashed = {k: v for k, v in state.items() if k != "summary"}
    return hashlib.sha256(json.dumps(hashed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _data_digest(state: dict, info: dict) -> str | None:
    if info.get("data") is None:
        return None
    return state["meshes"].get(info["data"]) or state["curves"].get(info["data"]) or \
        state["other"].get("lights", {}).get(info["data"]) or state["other"].get("cameras", {}).get(info["data"])


def scene_delta(before: dict, after: dict) -> dict:
    """What a call changed: objects added/removed/changed (counts and bbox before/after), materials added/removed/
    changed, and which other components moved."""
    bo, ao = before["objects"], after["objects"]
    bs, as_ = before["summary"], after["summary"]
    changed = []
    for name in sorted(set(bo) & set(ao)):
        if bo[name] != ao[name] or _data_digest(before, bs[name]) != _data_digest(after, as_[name]):
            b, a = bs[name], as_[name]
            changed.append({"object": name, "vertices": [b.get("vertices"), a.get("vertices")],
                            "triangles": [b.get("triangles"), a.get("triangles")],
                            "bbox_mm": {"before": b.get("bbox_mm"), "after": a.get("bbox_mm")}})
    bm, am = before["materials"], after["materials"]
    other_changed = sorted(k for k in ("scenes", "view_layers", "collections", "node_groups", "images", "other")
                           if before.get(k) != after.get(k))
    return {"added": sorted(set(ao) - set(bo)), "removed": sorted(set(bo) - set(ao)), "changed": changed[:200],
            "changed_total": len(changed), "materials_added": sorted(set(am) - set(bm)),
            "materials_removed": sorted(set(bm) - set(am)),
            "materials_changed": sorted(k for k in set(am) & set(bm) if am[k] != bm[k]),
            "other_changed": other_changed}


# ------------------------------------------------------------------------------------------------ protection
def protect() -> dict:
    """Pack generated / pixel-edited / external images and external fonts; fake-user every zero-user datablock."""
    packed, packed_external, pack_failed, fake = [], [], [], []
    for im in bpy.data.images:
        if im.source in ("VIEWER", "MOVIE", "SEQUENCE", "TILED") or im.library is not None:
            continue
        try:
            if im.source == "GENERATED" or im.is_dirty:
                im.pack()
                packed.append(im.name)
            elif im.packed_file is None and im.source == "FILE" and im.filepath:
                path = bpy.path.abspath(im.filepath)
                if os.path.isfile(path):
                    im.pack()
                    packed_external.append(im.name)
                else:
                    pack_failed.append({"image": im.name, "reason": "file not found"})
        except RuntimeError as exc:
            pack_failed.append({"image": im.name, "reason": str(exc)[:200]})
    for f in bpy.data.fonts:
        if f.packed_file is None and f.library is None and f.filepath and f.filepath != "<builtin>":
            try:
                if os.path.isfile(bpy.path.abspath(f.filepath)):
                    f.pack()
                    packed_external.append(f"font:{f.name}")
            except RuntimeError as exc:
                pack_failed.append({"font": f.name, "reason": str(exc)[:200]})
    for coll in KEEP:
        for idb in getattr(bpy.data, coll, ()):
            if idb.users == 0 and not idb.use_fake_user and idb.library is None:
                idb.use_fake_user = True
                fake.append(f"{coll}:{idb.name}")
    out = {"packed_images": packed, "packed_external": packed_external, "fake_user_added": fake}
    if pack_failed:
        out["pack_failed"] = pack_failed
    return out


# ------------------------------------------------------------------------------------------------ files
def confined(path, root) -> Path:
    """`path` resolved (junctions and symlinks followed) and refused unless it stays under `root`."""
    real = Path(os.path.realpath(str(path)))
    base = Path(os.path.realpath(str(root)))
    try:
        real.relative_to(base)
    except ValueError:
        raise PermissionError(f"path outside the job root: {real}") from None
    return real


def replace_with_retry(src: Path, dst: Path) -> None:
    for i, delay in enumerate(REPLACE_DELAYS_S + (None,)):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save(path, root, *, compress: bool = True) -> dict:
    """Protect, fingerprint and write a read-only checkpoint at `path` (must not exist). Returns its receipt."""
    t0 = time.perf_counter()
    dst = confined(path, root)
    if dst.exists():
        raise FileExistsError(f"checkpoint exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    prot = protect()
    state = scene_state()
    fp = fingerprint(state)
    t1 = time.perf_counter()
    part = dst.with_name(dst.stem + ".part.blend")
    if part.exists():
        os.chmod(part, stat.S_IWRITE | stat.S_IREAD)
        part.unlink()
    result = bpy.ops.wm.save_as_mainfile(filepath=str(part), copy=True, compress=compress, check_existing=False)
    if "FINISHED" not in result or not part.is_file():
        raise RuntimeError(f"save_as_mainfile returned {sorted(result)}")
    t2 = time.perf_counter()
    replace_with_retry(part, dst)
    os.chmod(dst, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    digest = sha256_file(dst)
    return {"path": str(dst), "sha256": digest, "bytes": dst.stat().st_size, "fingerprint": fp,
            "packed_images": prot["packed_images"], "packed_external": prot["packed_external"],
            "fake_user_added": prot["fake_user_added"], "pack_failed": prot.get("pack_failed", []),
            "objects": len(state["objects"]), "active_file": bpy.data.filepath,
            "seconds": {"protect_fingerprint": round(t1 - t0, 4), "save": round(t2 - t1, 4),
                        "total": round(time.perf_counter() - t0, 4)}}


def restore(path, root, *, expect_fingerprint: str | None = None, expect_sha256: str | None = None) -> dict:
    """Open the checkpoint at `path` (read-only file) and fingerprint the result."""
    t0 = time.perf_counter()
    src = confined(path, root)
    if not src.is_file():
        raise FileNotFoundError(f"checkpoint missing: {src}")
    digest = sha256_file(src) if expect_sha256 else None
    if expect_sha256 and digest != expect_sha256:
        raise RuntimeError(f"checkpoint sha256 {digest} != recorded {expect_sha256}: {src}")
    result = bpy.ops.wm.open_mainfile(filepath=str(src), load_ui=False, use_scripts=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"open_mainfile returned {sorted(result)}")
    t1 = time.perf_counter()
    fp = fingerprint()
    return {"restored": str(src), "fingerprint": fp,
            "matches": (fp == expect_fingerprint) if expect_fingerprint else None, "sha256": digest,
            "active_file": bpy.data.filepath,
            "seconds": {"open": round(t1 - t0, 4), "fingerprint": round(time.perf_counter() - t1, 4)}}
