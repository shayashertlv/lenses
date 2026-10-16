"""Export the live Blender scene to a GLB that meets the owner's spec (PLAN.md §2). Runs inside Blender 5.2.

Reference: the Studio's native_export.py (PORT_REVIEW §3, rewrite). Kept: Blender's own glTF exporter on temporary
evaluated copies in a temporary scene, transforms baked, unrealized instances refused, cleanup in `finally`, and a
check that the live scene is unchanged afterwards. Changed:
- the scene convention is checked, not assumed: numeric millimetres (METRIC, scale_length 0.001), authored +Y up /
  +Z front (`blender_agent_axes`), and the bridge-underside origin `mdl_bridge_underside` (an error if missing);
- what is exported is what RENDERS: the view layer used for rendering (ViewLayer.use; the active layer only when it
  is one of them, otherwise the first render layer, noted in the receipt; several render layers must agree on what
  renders), object/collection hide_render and that layer's excluded collections (LayerCollection.exclude) decide it,
  and each skipped object is listed in receipt['excluded'] with its reason. The geometry comes from that layer's
  viewport depsgraph, so every way the viewport could differ from the render is refused with a fixable message: a
  render-enabled source (or its collection) disabled in viewports, a modifier whose viewport and render switches
  differ, Subdivision/Multires viewport levels != render levels, Scene Simplify with different viewport and render
  limits (Max Subdivision, Child Particles), curve/metaball render resolution != viewport resolution, and Geometry
  Nodes that branch on 'Is Viewport';
- objects that render no surface (no triangles: an empty helper mesh, a guide curve without bevel) are skipped and
  listed as 'renders no surface', unless they carry `partRole`: a declared part of the product that renders nothing
  (an emptied mesh, a Mask modifier removing every face) is refused. Every EXPORTED object must carry `partRole`
  (lens, frame or temple) on the object or its data (the two must agree); the role is written into node, mesh AND
  material extras (material copies are made per (material, role), so a material shared by a lens and a frame object
  becomes two materials and is reported);
- unrealized instances are found in the evaluated geometry (an instances component, or a dupli of another object),
  so a curve whose own mesh comes through a Geometry Nodes modifier (e.g. Smooth by Angle) is not an instance;
- every image an exported material samples must have data (packed, generated, or its file found); a lost texture
  is an error, never a silent drop;
- authored normals survive: custom split normals, sharp edges and flat faces go through Blender's exporter; for an
  object whose matrix is mirrored or non-uniformly scaled the corner normals are captured first, mapped by the
  inverse-transpose, and set again after the bake (Mesh.transform does not carry them through such a matrix);
- loose edges and vertices are never exported (they would become non-triangle primitives);
- see-through materials (exported transmission > 0), on lenses or frame parts, are single-sided when every whole
  evaluated mesh using them is a closed outward 2-manifold. Open/inward uses keep their sidedness with a note;
  opaque materials are untouched. Only the exported JSON changes; the receipt names each changed material;
- the GLB is written to a temporary file next to the target, patched (authored node, mesh and material names are
  restored: Blender would add ".001" to the copies) and moved into place only after every check passed, with a retry
  on a transient Windows sharing violation;
- no LENSES_part_visibility gate;
- WP4 allowlist (WP4_DESIGN §1 finding 1): the object properties `g3d_seat` and `g3d_hidden_x_ranges_mm` (JSON
  strings written by the seating tools) are copied from each source object to its export node's extras, so the host
  seating check reads the declared frame type, the sharpening flag and the hidden ranges;
- WP5 allowlist (WP5_DESIGN §4): the object property `g3d_print_uv` (JSON written by g3d.print_uv) is copied to the
  node extras with its `texcoord` set to the index of its UV layer in the EXPORTED mesh (Blender's exporter writes
  every UV layer as TEXCOORD_<its index>), so the host reads which TEXCOORD carries the print; a property whose layer
  the exported mesh lacks is not copied and the receipt notes it. Nothing else of the source's custom properties
  reaches the GLB.

Volume lengths (KHR_materials_volume): Blender's exporter writes the shader's Thickness and 1/density exactly as
authored, NOT scaled with the geometry. The Studio convention is shader lengths in METRES (q001's 'Champagne lens'
Thickness 0.00135, Prada's 0.0016), so author them in metres even though the geometry is in millimetres; the
receipt notes a thicknessFactor above 0.01 (it looks like millimetres) and a volume the exporter dropped (a
hand-made 'glTF Material Output' group without an Occlusion socket loses its Thickness).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector

from . import glb_patch
from .coating import coating_range, optical_intent, optical_warnings

ROLES = ("lens", "frame", "temple")
AXES_KEYS = ("blender_agent_axes", "authoring_axes", "g3d_axes")
EXPECTED_AXES = "+Y up; +Z front"
ORIGIN_KEY = "mdl_bridge_underside"
SURFACE_TYPES = ("MESH", "CURVE", "SURFACE", "FONT", "META")
VOLUME_THICKNESS_SUSPECT_M = 0.01
REPLACE_TRIES = 25
SEAT_EXTRAS = ("g3d_seat", "g3d_hidden_x_ranges_mm")     # WP4 allowlist: object property -> node extras
PRINT_UV_KEY = "g3d_print_uv"                            # WP5 allowlist: object property -> node extras (+ TEXCOORD)
PRINT_UV_LAYER = "g3d_print"


class ExportError(ValueError):
    """The scene cannot be exported as it stands; the message says what to fix."""


def check_convention(scene) -> tuple[float, list[float], list[str]]:
    """Return (metres per Blender unit, origin in Blender units, notes) or raise ExportError."""
    us = scene.unit_settings
    notes = []
    if us.system != "METRIC" or abs(us.scale_length - 0.001) > 1e-9:
        raise ExportError(f"scene units must be numeric millimetres (METRIC, scale_length 0.001); found {us.system} "
                          f"scale_length {us.scale_length}")
    axes = next((str(scene[k]) for k in AXES_KEYS if k in scene), None)
    if axes is None:
        notes.append(f"scene declares no axes property ({'/'.join(AXES_KEYS)}); assuming {EXPECTED_AXES}")
    elif not axes.startswith(EXPECTED_AXES):
        raise ExportError(f"scene axes are {axes!r}; the exporter needs {EXPECTED_AXES!r} authored in Blender's axes")
    if ORIGIN_KEY not in scene:
        raise ExportError(f"scene property {ORIGIN_KEY} (the bridge underside, in mm) is missing; set it before export")
    origin = [float(v) for v in scene[ORIGIN_KEY]]
    if len(origin) != 3 or not all(math.isfinite(v) for v in origin):
        raise ExportError(f"{ORIGIN_KEY} must be three finite numbers, got {origin}")
    return float(us.scale_length), origin, notes


# ------------------------------------------------------------------------------------------------ snapshot
def _plain(value):
    """A comparable, printable form of an RNA / ID-property value."""
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    for attr in ("to_dict", "to_list"):
        if hasattr(value, attr):
            return getattr(value, attr)()
    if isinstance(value, bpy.types.ID):
        return ("ID", value.name)
    try:
        return tuple(_plain(v) for v in value)
    except TypeError:
        return repr(value)


def _custom_props(idblock) -> tuple:
    try:
        return tuple(sorted((k, repr(_plain(idblock[k]))) for k in idblock.keys()))
    except (TypeError, AttributeError):
        return ()


_VOLATILE_PROPS = frozenset({     # change with evaluation, selection or UI, never with the scene's content
    "rna_type", "execution_time", "is_active", "show_expanded", "is_override_data", "users", "tag", "is_evaluated",
    "original", "session_uid", "preview", "is_editable", "is_runtime_data", "dimensions", "select",
    "is_override_data_collection"})


def _rna_simple(struct) -> tuple:
    """Every simple RNA property (bool/int/float/enum/string, arrays included; pointers by name) of `struct`."""
    out = []
    for prop in struct.bl_rna.properties:
        ident = prop.identifier
        if ident in _VOLATILE_PROPS:
            continue
        if prop.type == "COLLECTION":
            continue
        try:
            value = getattr(struct, ident)
        except (AttributeError, RuntimeError, TypeError):
            continue
        if prop.type == "POINTER":
            value = getattr(value, "name", None) if value is not None else None
            if not isinstance(value, (str, type(None))):
                continue
        out.append((ident, _plain(value)))
    return tuple(out)


_ATTR_ACCESS = {"FLOAT": ("value", 1, np.float32), "INT": ("value", 1, np.int32), "FLOAT_VECTOR": ("vector", 3, np.float32),
                "FLOAT_COLOR": ("color", 4, np.float32), "BYTE_COLOR": ("color", 4, np.float32),
                "BOOLEAN": ("value", 1, np.bool_), "FLOAT2": ("vector", 2, np.float32), "INT8": ("value", 1, np.int32),
                "INT32_2D": ("value", 2, np.int32), "INT16_2D": ("value", 2, np.int32),
                "QUATERNION": ("value", 4, np.float32), "FLOAT4X4": ("value", 16, np.float32)}


def _digest_collection(h, coll, prop: str, ncomp: int, dtype) -> None:
    buf = np.zeros(len(coll) * ncomp, dtype=dtype)
    try:
        coll.foreach_get(prop, buf)
    except (TypeError, RuntimeError, AttributeError):
        h.update(repr([_plain(getattr(item, prop, None)) for item in coll]).encode())
        return
    h.update(buf.tobytes())


def _mesh_digest(me) -> str:
    """Positions, topology and every attribute (UVs, sharp edges/faces, custom normals...), hashed. NaN-safe."""
    h = hashlib.sha256()
    _digest_collection(h, me.vertices, "co", 3, np.float32)
    _digest_collection(h, me.edges, "vertices", 2, np.int32)
    _digest_collection(h, me.loops, "vertex_index", 1, np.int32)
    _digest_collection(h, me.polygons, "loop_start", 1, np.int32)
    _digest_collection(h, me.polygons, "material_index", 1, np.int32)
    for attr in sorted(me.attributes, key=lambda a: a.name):
        h.update(f"|{attr.name}|{attr.domain}|{attr.data_type}|".encode())
        access = _ATTR_ACCESS.get(attr.data_type)
        if access:
            _digest_collection(h, attr.data, *access)
    h.update(repr(_custom_props(me)).encode())
    h.update(repr(tuple(m.name if m else None for m in me.materials)).encode())
    return h.hexdigest()


def _tree_state(tree, seen: set) -> tuple:
    if tree is None or tree.as_pointer() in seen:
        return ()
    seen.add(tree.as_pointer())          # embedded material trees all share one name
    nodes = tuple((n.name, n.bl_idname, _rna_simple(n), tuple(_plain(getattr(s, "default_value", None))
                                                                  for s in n.inputs))
                  for n in tree.nodes)
    links = tuple(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                         for l in tree.links))
    state = (tree.name, nodes, links)
    # Group interface names are functional content: the glTF exporter reads named, unconnected control sockets.
    # A socket rename can therefore change the exported optics without changing any node value or link. Include
    # defaults, socket types, order and panel structure even for unused groups, while retaining the legacy tuple
    # for ordinary trees with no interface (and therefore their existing checkpoint fingerprints).
    interface = tuple(_rna_simple(item) for item in tree.interface.items_tree)
    if interface:
        state += (("interface", interface),)
    return state


def snapshot(scene) -> str:
    """Everything an export must not change in the live scene, as one string (repr is NaN-safe: nan == nan).

    Covers the exporter's own inputs (scene custom props, unit settings, object/data custom props), objects
    (matrices, visibility, modifier stacks with their settings, material slots), mesh data (positions, topology,
    every attribute), curves, materials and node groups (node settings, socket values, links), images (path,
    source, packing), collections and the view layer's collection flags."""
    us = scene.unit_settings
    sc = (scene.name, _custom_props(scene), (us.system, us.scale_length, us.length_unit), scene.camera and scene.camera.name)
    layers = []
    for vl in scene.view_layers:
        def walk(lc, acc):
            acc.append((lc.name, lc.exclude, lc.hide_viewport, lc.holdout, lc.indirect_only))
            for ch in lc.children:
                walk(ch, acc)
        acc = []
        walk(vl.layer_collection, acc)
        layers.append((vl.name, vl.use, tuple(acc)))
    objects = []
    for obj in scene.objects:
        objects.append((obj.name, obj.type, obj.data.name if obj.data else None, tuple(tuple(r) for r in obj.matrix_world),
                        obj.hide_render, obj.hide_viewport, obj.parent.name if obj.parent else None,
                        obj.instance_type, _custom_props(obj),
                        tuple((m.name, m.type, _rna_simple(m)) for m in obj.modifiers),
                        tuple((s.link, s.material.name if s.material else None) for s in obj.material_slots)))
    meshes = tuple((me.name, _mesh_digest(me)) for me in bpy.data.meshes)
    curves = []
    for cu in bpy.data.curves:
        pts = tuple(tuple(_plain(p.co) for p in sp.points) + tuple(_plain(p.co) for p in sp.bezier_points)
                    for sp in cu.splines)
        curves.append((cu.name, _rna_simple(cu), _custom_props(cu), pts))
    seen: set = set()
    materials = tuple((m.name, _custom_props(m), _rna_simple(m), _tree_state(m.node_tree, seen))
                      for m in bpy.data.materials)
    groups = tuple(_tree_state(g, seen) for g in bpy.data.node_groups)
    images = tuple((im.name, im.filepath, im.source, im.packed_file is not None,
                    hashlib.sha256(bytes(im.packed_file.data)).hexdigest() if im.packed_file else None,
                    im.is_dirty, im.colorspace_settings.name, im.alpha_mode) for im in bpy.data.images)
    collections = tuple((c.name, c.hide_render, c.hide_viewport, tuple(sorted(o.name for o in c.objects)),
                         tuple(sorted(ch.name for ch in c.children))) for c in bpy.data.collections)
    counts = (len(bpy.data.meshes), len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.scenes),
              len(bpy.data.images), len(bpy.data.node_groups), len(bpy.data.curves))
    return repr((bpy.data.filepath, sc, tuple(layers), tuple(objects), meshes, tuple(curves), materials, groups,
                 images, collections, counts))


# ------------------------------------------------------------------------------------------------ visibility
def render_status(view_layer) -> dict:
    """{object: None when it renders, else the reason it does not}. Reasons: 'view_layer.exclude',
    'collection.hide_render', 'hide_render'. An object reachable through several collections renders if any path
    lets it."""
    status: dict = {}

    def walk(lc, reason):
        coll = lc.collection
        if reason is None and lc.exclude:
            reason = "view_layer.exclude"
        if reason is None and coll.hide_render:
            reason = "collection.hide_render"
        for obj in coll.objects:
            r = reason or ("hide_render" if obj.hide_render else None)
            if r is None:
                status[obj] = None
            elif obj not in status:
                status[obj] = r
        for child in lc.children:
            walk(child, reason)

    walk(view_layer.layer_collection, None)
    return status


def render_view_layer(scene, context_layer=None) -> tuple:
    """(the view layer whose render is exported, notes). Only layers with use=True render: the context layer when it is
    one of them, else the first one (noted). Several render layers must agree on which surface objects render (the
    render composites them; one GLB cannot); raises ExportError otherwise or when no layer renders."""
    layers = [vl for vl in scene.view_layers if vl.use]
    if not layers:
        raise ExportError("no view layer is used for rendering ('Use for Rendering' is off on every view layer); "
                          "enable it on the layer that shows the product")
    names = [vl.name for vl in layers]
    ctx = context_layer.name if context_layer is not None else None
    chosen = layers[names.index(ctx)] if ctx in names else layers[0]
    notes = []
    if ctx is not None and ctx not in names:
        notes.append(f"the active view layer {ctx!r} is not used for rendering; exported what view layer "
                     f"{chosen.name!r} renders")
    if len(layers) > 1:
        surfaces = [o for o in scene.objects if o.type in SURFACE_TYPES]
        enabled = {vl.name: {o for o, r in render_status(vl).items() if r is None} for vl in layers}
        differ = sorted(o.name for o in surfaces if len({o in enabled[n] for n in names}) > 1)
        if differ:
            raise ExportError(f"the view layers used for rendering ({names}) disagree on what renders: "
                              + ", ".join(differ[:12]) + ("..." if len(differ) > 12 else "")
                              + "; turn 'Use for Rendering' off on all but the product's layer, or make them agree")
        notes.append(f"{len(layers)} view layers are used for rendering and agree on what renders; exported "
                     f"{chosen.name!r}")
    return chosen, notes


def render_enabled_objects(scene, view_layer=None) -> set:
    view_layer = view_layer or render_view_layer(scene, bpy.context.view_layer)[0]
    return {o for o, r in render_status(view_layer).items() if r is None and o.name in scene.objects}


def simplify_mismatches(scene) -> list[str]:
    """Scene Simplify limits that make the viewport evaluation differ from the render (each item says what to fix).
    Simplify's Normals switch only affects viewport drawing, not the evaluated mesh (checked: identical GLB normals)."""
    r = scene.render
    if not r.use_simplify:
        return []
    out = []
    if r.simplify_subdivision != r.simplify_subdivision_render:
        out.append(f"Scene Simplify is on with Max Subdivision {r.simplify_subdivision} in viewports but "
                   f"{r.simplify_subdivision_render} for render; turn Simplify off or set the two equal")
    if abs(r.simplify_child_particles - r.simplify_child_particles_render) > 1e-9:
        out.append(f"Scene Simplify is on with Child Particles {r.simplify_child_particles:g} in viewports but "
                   f"{r.simplify_child_particles_render:g} for render; turn Simplify off or set the two equal")
    return out


def _tree_has(tree, idname: str, seen=None) -> bool:
    seen = set() if seen is None else seen
    if tree is None or tree.as_pointer() in seen:
        return False
    seen.add(tree.as_pointer())          # embedded material trees all share one name
    for n in tree.nodes:
        if n.bl_idname == idname:
            return True
        if getattr(n, "node_tree", None) is not None and _tree_has(n.node_tree, idname, seen):
            return True
    return False


def viewport_render_mismatches(obj, depsgraph) -> list[str]:
    """Why the viewport-evaluated geometry of `obj` could differ from what renders (each item says what to fix)."""
    out = []
    if obj.hide_viewport:
        out.append(f"{obj.name}: disabled in viewports (monitor icon) but enabled for render; enable it in viewports")
    elif not obj.evaluated_get(depsgraph).is_evaluated:
        colls = [c.name for c in obj.users_collection if c.hide_viewport]
        out.append(f"{obj.name}: not evaluated in the viewport (collection {colls or '?'} disabled in viewports); "
                   f"enable it in viewports")
    for m in obj.modifiers:
        if m.show_viewport != m.show_render:
            out.append(f"{obj.name}: modifier {m.name!r} is {'on' if m.show_render else 'off'} for render but "
                       f"{'on' if m.show_viewport else 'off'} in viewports; make the two switches match")
        if m.type in ("SUBSURF", "MULTIRES") and m.levels != m.render_levels:
            out.append(f"{obj.name}: modifier {m.name!r} has viewport levels {m.levels} but render levels "
                       f"{m.render_levels}; set them equal")
        if m.type == "NODES" and m.show_render and _tree_has(m.node_group, "GeometryNodeIsViewport"):
            out.append(f"{obj.name}: Geometry Nodes modifier {m.name!r} branches on 'Is Viewport'; remove that branch")
    data = obj.data
    if obj.type in ("CURVE", "SURFACE", "FONT") and data is not None:
        if data.render_resolution_u not in (0, data.resolution_u):
            out.append(f"{obj.name}: curve render resolution {data.render_resolution_u} != viewport resolution "
                       f"{data.resolution_u}; set render resolution to 0 or equal")
        if obj.type == "SURFACE" and data.render_resolution_v not in (0, data.resolution_v):
            out.append(f"{obj.name}: surface render resolution V {data.render_resolution_v} != viewport "
                       f"{data.resolution_v}; set them equal")
    if obj.type == "META" and data is not None and abs(data.render_resolution - data.resolution) > 1e-9:
        out.append(f"{obj.name}: metaball render resolution {data.render_resolution} != viewport resolution "
                   f"{data.resolution}; set them equal")
    return out


def unrealized_instances(enabled: set, candidates, depsgraph) -> list[str]:
    """Objects whose rendered geometry includes instances that new_from_object would not carry."""
    found = set()
    for inst in depsgraph.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        parent = inst.parent.original
        if parent in enabled and inst.object.original != parent:      # a dupli of ANOTHER object or collection
            found.add(parent.name)
    for obj in candidates:
        ev = obj.evaluated_get(depsgraph)
        if not ev.is_evaluated:
            continue
        try:
            geometry = ev.evaluated_geometry()                        # keep it alive while its pointcloud is read
            pc = geometry.instances_pointcloud()
            count = len(pc.points) if pc is not None else 0
        except (AttributeError, RuntimeError, TypeError, ReferenceError):
            continue
        if count:                                                     # an instances component (Geometry Nodes)
            found.add(obj.name)
    found.update(o.name for o in enabled if o.instance_type == "COLLECTION" and o.instance_collection is not None)
    return sorted(found)


# ------------------------------------------------------------------------------------------------ materials
def _image_has_data(image) -> bool:
    if image.packed_file is not None or image.source in ("GENERATED", "VIEWER"):
        return True
    if image.source in ("FILE", "SEQUENCE", "MOVIE", "TILED"):
        path = bpy.path.abspath(image.filepath, library=image.library)
        if image.source == "TILED":
            path = path.replace("<UDIM>", "1001")
        return bool(image.filepath) and os.path.isfile(path)
    return True


def sampled_images(material) -> list:
    """Images the material samples (an Image Texture node whose output is linked), nested groups included."""
    out: list = []
    seen: set = set()

    def walk(tree):
        if tree is None or tree.as_pointer() in seen:
            return
        seen.add(tree.as_pointer())          # embedded material trees all share one name
        for n in tree.nodes:
            if n.bl_idname == "ShaderNodeTexImage" and n.image is not None and any(s.is_linked for s in n.outputs):
                out.append(n.image)
            elif getattr(n, "node_tree", None) is not None:
                walk(n.node_tree)

    walk(material.node_tree)                  # Blender 5.x: materials always render their node tree
    return out


def images_without_data(material) -> list[str]:
    """Names of images the material samples that have no data (not packed, not generated, file not found)."""
    return [f"{im.name} ({im.filepath or 'no file'})" for im in sampled_images(material) if not _image_has_data(im)]


def volume_authored(material) -> bool:
    """True when the material authors a glTF volume: a glTF Material Output/Settings group with Thickness, or a
    Volume Absorption node feeding the Material Output's Volume."""
    tree = material.node_tree
    if tree is None:
        return False
    for n in tree.nodes:
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None and "Thickness" in n.inputs \
                and n.node_tree.name.startswith(("glTF Material Output", "glTF Settings")):
            t = n.inputs["Thickness"]
            if t.is_linked or float(t.default_value) > 0.0:
                return True
        if n.bl_idname == "ShaderNodeOutputMaterial" and n.inputs["Volume"].is_linked:
            src = n.inputs["Volume"].links[0].from_node
            if src.bl_idname == "ShaderNodeVolumeAbsorption":
                return True
    return False


# ------------------------------------------------------------------------------------------------ normals
def _corner_keys(mesh) -> np.ndarray:
    """(polygon index, vertex index) of every corner, packed into one int64 (stable under flip_normals)."""
    nl, npoly = len(mesh.loops), len(mesh.polygons)
    verts = np.zeros(nl, np.int32)
    mesh.loops.foreach_get("vertex_index", verts)
    start = np.zeros(npoly, np.int32)
    total = np.zeros(npoly, np.int32)
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", total)
    poly = np.zeros(nl, np.int64)
    for p, (s, t) in enumerate(zip(start.tolist(), total.tolist())):
        poly[s:s + t] = p
    return poly * max(len(mesh.vertices), 1) + verts


def _is_similarity(m3) -> bool:
    """A rotation times a uniform scale (custom normals survive Mesh.transform unchanged in direction)."""
    a = np.array(m3, dtype=np.float64)
    g = a.T @ a
    s2 = np.trace(g) / 3.0
    return s2 > 0 and float(np.abs(g - s2 * np.eye(3)).max()) <= 1e-6 * s2


def bake_transform(mesh, to_asset: Matrix) -> bool:
    """Apply `to_asset` to the mesh. For a mirrored or non-uniform matrix the corner normals are captured first,
    mapped by the inverse-transpose and set back as custom normals after the bake (flip_normals reverses each face's
    corners, so they are matched by (polygon, vertex)). Returns True when normals were re-set."""
    m3 = to_asset.to_3x3()
    det = m3.determinant()
    keep = None
    if (det < 0 or not _is_similarity(m3)) and len(mesh.loops):
        n = np.zeros(len(mesh.loops) * 3, np.float32)
        mesh.corner_normals.foreach_get("vector", n)
        nmat = np.array(m3.inverted().transposed(), dtype=np.float64)
        n = n.reshape(-1, 3).astype(np.float64) @ nmat.T
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
        keys = _corner_keys(mesh)
        order = np.argsort(keys, kind="stable")
        keep = (keys[order], n[order])
    mesh.transform(to_asset)
    if det < 0:
        mesh.flip_normals()
    if keep is not None:
        keys = _corner_keys(mesh)
        pos = np.searchsorted(keep[0], keys)
        pos = np.clip(pos, 0, len(keep[0]) - 1)
        if not np.array_equal(keep[0][pos], keys):
            raise RuntimeError("corner normals could not be matched after the transform")
        mesh.normals_split_custom_set(keep[1][pos].tolist())
    mesh.update()
    return keep is not None


# ------------------------------------------------------------------------------------------------ export
def _replace_with_retry(src: Path, dst: Path) -> None:
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def _has_role(obj) -> bool:
    """partRole present (any value) on the object or its data."""
    return obj.get("partRole") is not None or (obj.data is not None and obj.data.get("partRole") is not None)


def _role(obj) -> tuple[str | None, str, str | None]:
    """(role, source, conflict message) from the object, falling back to its data."""
    r_obj = obj.get("partRole")
    r_dat = obj.data.get("partRole") if obj.data is not None else None
    r_obj = r_obj if isinstance(r_obj, str) else (None if r_obj is None else repr(r_obj))
    r_dat = r_dat if isinstance(r_dat, str) else (None if r_dat is None else repr(r_dat))
    if r_obj is not None and r_dat is not None and r_obj != r_dat:
        return None, "conflict", f"{obj.name}: object partRole {r_obj!r} disagrees with data partRole {r_dat!r}"
    if r_obj is not None:
        return r_obj, "object", None
    return r_dat, "data", None


def solid_orientation(mesh) -> dict:
    """Check the entire evaluated surface, before glTF splits it by material/UV/normal.

    Every connected shell must be closed, consistently wound and outward. A positive total volume alone can hide
    an inward small shell inside a larger outward one. Loose vertices/edges are ignored, just as in the export.
    This only inspects topology/winding: it never welds vertices or changes authored geometry or normals.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[edge for edge in bm.edges if not edge.link_faces], context="EDGES")
        edges = [edge for edge in bm.edges if edge.link_faces]
        verts = [vert for vert in bm.verts if vert.link_faces]
        if not edges or any(not edge.is_manifold for edge in edges) or any(not vert.is_manifold for vert in verts):
            return {"outward_solid": False, "reason": "mesh is open or not a closed 2-manifold"}
        if any(not edge.is_contiguous for edge in edges):
            return {"outward_solid": False, "reason": "mesh has inconsistent face winding"}
        mesh.calc_loop_triangles()
        coordinates = np.asarray([vert.co for vert in mesh.vertices], dtype=np.float64)
        triangles = np.asarray([tri.vertices for tri in mesh.loop_triangles], dtype=np.int64)
        polygons = np.asarray([tri.polygon_index for tri in mesh.loop_triangles], dtype=np.int64)
        remaining = set(bm.faces)
        volumes = []
        while remaining:
            seed = remaining.pop()
            faces, todo = [seed], [seed]
            while todo:
                for edge in todo.pop().edges:
                    for face in edge.link_faces:
                        if face in remaining:
                            remaining.remove(face)
                            faces.append(face)
                            todo.append(face)
            # Local origin and float64 arithmetic avoid cancellation for small, translated solids.
            points = coordinates[triangles[np.isin(polygons, [face.index for face in faces])]]
            points -= points[0, 0].copy()
            extent = float(np.abs(points).max())
            volume = float(np.einsum("ij,ij->", np.cross(points[:, 1], points[:, 2]), points[:, 0])) / 6
            volumes.append(volume)
            if volume <= np.finfo(float).eps * extent ** 3 * 64:
                return {"outward_solid": False, "reason": "mesh has an inward-facing or zero-volume shell",
                        "signed_volumes_m3": volumes}
        return {"outward_solid": True, "signed_volumes_m3": volumes}
    finally:
        bm.free()


def export_glb(path: str | Path, scene=None) -> dict:
    """Export `scene` (default: the active scene) to `path` (.glb). Returns the receipt; raises ExportError."""
    scene = scene or bpy.context.scene
    path = Path(path).resolve()
    if path.suffix.lower() != ".glb":
        raise ExportError("export path must end in .glb")
    if bpy.context.mode != "OBJECT":
        raise ExportError("leave Edit/Sculpt mode before export (no mode was changed)")
    metres_per_unit, origin, notes = check_convention(scene)
    view_layer, layer_notes = render_view_layer(scene, bpy.context.view_layer)
    notes += layer_notes
    transform = Matrix.Scale(metres_per_unit, 4) @ Matrix.Translation(-Vector(origin))

    bpy.context.view_layer.update()
    before = snapshot(scene)
    receipt = {"path": str(path), "metres_per_unit": metres_per_unit, "origin_mm": origin, "notes": notes,
               "view_layer": view_layer.name, "objects": [], "excluded": [], "split_materials": [],
               "status": "failed", "scene_unchanged": False}
    tmp_scene, meshes, duplicates, copies = None, [], [], {}
    tmp_glb = path.with_name(f".{path.stem}.g3d-tmp-{os.getpid()}.glb")
    ready = False
    try:
        with bpy.context.temp_override(scene=scene, view_layer=view_layer):    # evaluate the layer that renders
            depsgraph = bpy.context.evaluated_depsgraph_get()
        status = render_status(view_layer)
        enabled = {o for o, r in status.items() if r is None}
        surfaces = [o for o in scene.objects if o.type in SURFACE_TYPES]
        candidates = [o for o in surfaces if o in enabled]
        receipt["excluded"] = [{"source": o.name, "reason": status.get(o) or "not in the view layer"}
                               for o in surfaces if o not in enabled]
        mismatches = simplify_mismatches(scene) + [m for o in candidates
                                                   for m in viewport_render_mismatches(o, depsgraph)]
        if mismatches:
            raise ExportError("the viewport evaluation would differ from the render: " + "; ".join(mismatches[:12])
                              + ("; ..." if len(mismatches) > 12 else ""))
        instances = unrealized_instances(enabled, candidates, depsgraph)
        if instances:
            raise ExportError("unrealized instances cannot be exported: " + ", ".join(instances)
                              + " (realize collection / Geometry Nodes instances first)")
        if not candidates:
            raise ExportError("no render-visible surface objects to export")

        evaluated, empty_parts = [], []
        for obj in candidates:
            ev = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=depsgraph)
            meshes.append(mesh)
            mesh.calc_loop_triangles()
            if not mesh.loop_triangles:
                if _has_role(obj):                                    # a declared part of the product
                    empty_parts.append(obj.name)
                else:                                                 # a helper (guide curve, empty mesh)
                    receipt["excluded"].append({"source": obj.name, "reason": "renders no surface"})
                continue
            evaluated.append((obj, ev, mesh))
        if empty_parts:
            raise ExportError("objects that carry partRole render no surface (no triangles): "
                              + ", ".join(empty_parts[:20]) + ("..." if len(empty_parts) > 20 else "")
                              + "; fix their geometry or modifiers (a part of the product must render), or remove "
                                "partRole from helpers")
        if not evaluated:
            raise ExportError("no render-visible object has any triangles")
        conflicts, missing, from_data = [], [], []
        roles = {}
        for obj, _, _ in evaluated:
            role, source, conflict = _role(obj)
            if conflict:
                conflicts.append(conflict)
            elif role not in ROLES:
                missing.append(obj.name)
            else:
                roles[obj] = role
                if source == "data":
                    from_data.append(obj.name)
        if conflicts:
            raise ExportError("partRole conflicts (fix one of the two): " + "; ".join(conflicts[:20]))
        if missing:
            raise ExportError("every exported object needs partRole lens/frame/temple; missing or invalid on: "
                              + ", ".join(missing[:20]) + ("..." if len(missing) > 20 else ""))
        if from_data:
            notes.append("partRole read from the object data (not the object) on: " + ", ".join(from_data[:20]))
        lost = sorted({f"{s.material.name}: {img}" for _, ev, _ in evaluated for s in ev.material_slots
                       if s.material is not None for img in images_without_data(s.material)})
        if lost:
            raise ExportError("exported materials sample images that have no data (not packed and file not found; "
                              "pack them or fix the path): " + "; ".join(lost[:12]))

        tmp_scene = bpy.data.scenes.new("g3d export (temporary)")
        # Under an override: setting unit_settings.system on ANY scene runs an RNA update on the CONTEXT scene, which
        # reset the live scene's length_unit (MILLIMETERS -> METERS) in Blender 5.2 (found by the snapshot).
        with bpy.context.temp_override(scene=tmp_scene, view_layer=tmp_scene.view_layers[0]):
            tmp_scene.unit_settings.system = "NONE"
            tmp_scene.unit_settings.scale_length = 1.0
        node_names, mesh_names, mat_names = {}, {}, {}
        roles_by_material: dict[str, set] = {}
        optical_sources: dict[str, list] = {}        # exported copy name -> active authored optical controls
        coating_ranges: dict[str, dict] = {}        # only the helper's scalar, untextured coating controls
        material_meshes: dict[str, list] = {}       # temporary material name -> whole evaluated mesh checks
        renormalised = []
        seat_extras: dict = {}
        print_extras: dict = {}
        surface_reports = []
        for obj, evaluated_obj, mesh in evaluated:
            role = roles[obj]
            from .surface_quality_bpy import inspect_object
            try:
                surface_reports.append({"object": obj.name, "role": role,
                                        **inspect_object(obj, mesh, origin_mm=origin)})
            except (ValueError, KeyError, TypeError, RuntimeError) as error:
                surface_reports.append({"object": obj.name, "informational_only": True,
                                        "status": "unavailable", "message": str(error)})
            for key in list(mesh.keys()):
                del mesh[key]
            if bake_transform(mesh, transform @ obj.matrix_world):
                renormalised.append(obj.name)
            mesh.calc_loop_triangles()
            co = np.zeros(len(mesh.vertices) * 3, np.float32)
            mesh.vertices.foreach_get("co", co)
            if not np.isfinite(co).all():
                raise ExportError(f"{obj.name}: non-finite vertex coordinates")
            dup = bpy.data.objects.new(obj.name, mesh)
            duplicates.append(dup)
            tmp_scene.collection.objects.link(dup)
            dup["partRole"] = role
            mesh["partRole"] = role
            for key in SEAT_EXTRAS:                                   # WP4 allowlist (module docstring)
                value = obj.get(key)
                if value is not None:
                    dup[key] = value if isinstance(value, str) else json.dumps(_plain(value))
                    seat_extras.setdefault(obj.name, []).append(key)
            pv = obj.get(PRINT_UV_KEY)                               # WP5 allowlist (module docstring)
            if pv is not None:
                try:
                    info = dict(json.loads(pv) if isinstance(pv, str) else _plain(pv))
                except (TypeError, ValueError):
                    info = None
                layer = str(info.get("layer", PRINT_UV_LAYER)) if info is not None else PRINT_UV_LAYER
                k = mesh.uv_layers.find(layer)
                if info is None:
                    notes.append(f"{obj.name}: {PRINT_UV_KEY} is not a JSON object; not exported")
                elif k < 0:
                    notes.append(f"{obj.name}: {PRINT_UV_KEY} names UV layer {layer!r}, which the exported mesh lacks; "
                                 f"not exported (re-run g3d.print_uv)")
                else:
                    if info.get("texcoord") != k:
                        notes.append(f"{obj.name}: {PRINT_UV_KEY} said texcoord {info.get('texcoord')}, the exported "
                                     f"mesh has {layer!r} at {k}; exported {k}")
                    info["texcoord"] = k
                    dup[PRINT_UV_KEY] = json.dumps(info, sort_keys=True)
                    print_extras[obj.name] = k
            node_names[dup.name] = obj.name
            mesh_names[mesh.name] = obj.data.name if obj.type == "MESH" else obj.name
            textured = sorted({s.material.name for s in evaluated_obj.material_slots
                               if s.material is not None and sampled_images(s.material)})
            if textured and not mesh.uv_layers:
                notes.append(f"{obj.name}: material(s) {textured} sample an image texture but the mesh has no UV map "
                             f"(the texture cannot map; add UVs)")
            used_slots = {poly.material_index for poly in mesh.polygons}
            solid = solid_orientation(mesh)
            for index, slot in enumerate(evaluated_obj.material_slots):
                mat = slot.material
                if mat is None:
                    continue
                roles_by_material.setdefault(mat.name, set()).add(role)
                key = (mat.name, role)
                if key not in copies:
                    cp = mat.copy()
                    for prop in list(cp.keys()):
                        del cp[prop]
                    cp["partRole"] = role
                    copies[key] = cp
                    mat_names[cp.name] = mat.name
                    # Evaluated Material IDs can retain a previous helper tag/control value across live edits;
                    # Blender's own copy/export follows the original datablock. Audit that same authored graph.
                    authored_mat = mat.original
                    optical_sources[cp.name] = optical_intent(authored_mat)
                    film_range = coating_range(authored_mat)
                    if film_range:
                        coating_ranges[cp.name] = film_range
                mesh.materials[index] = copies[key]
                if index in used_slots:
                    material_meshes.setdefault(copies[key].name, []).append({"object": obj.name, **solid})
            receipt["objects"].append({"source": obj.name, "partRole": role, "vertices": len(mesh.vertices),
                                       "triangles": len(mesh.loop_triangles),
                                       "materials": [s.material.name if s.material else None
                                                     for s in evaluated_obj.material_slots]})
        if renormalised:
            receipt["normals_reset"] = renormalised
        receipt["surface_quality"] = {"schema": "g3d.surface_quality/1", "informational_only": True,
                                      "objects": surface_reports}
        if seat_extras:
            receipt["seat_extras"] = seat_extras
        if print_extras:
            receipt["print_uv_extras"] = print_extras             # object -> TEXCOORD index of its print layer
        # A material on lens AND non-lens objects is exported as separate lens and non-lens materials (§2 needs
        # lenses distinguished by material); frame/temple copies only carry their partRole and are not reported.
        receipt["split_materials"] = sorted(n for n, r in roles_by_material.items() if "lens" in r and len(r) > 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        with bpy.context.temp_override(scene=tmp_scene, view_layer=tmp_scene.view_layers[0]):
            result = bpy.ops.export_scene.gltf(
                filepath=str(tmp_glb), export_format="GLB", export_yup=False,
                use_active_scene=True, use_selection=False, export_apply=False,
                export_materials="EXPORT", export_extras=True, export_animations=False,
                use_mesh_edges=False, use_mesh_vertices=False, will_save_settings=False,
                export_cameras=False, export_lights=False, export_draco_mesh_compression_enable=False)
        if result != {"FINISHED"}:
            raise RuntimeError(f"Blender glTF exporter did not finish: {result}")
        doc_before, binary = glb_patch.read_chunks(tmp_glb.read_bytes())
        receipt["optical_warnings"] = []
        receipt["coating_ranges"] = []
        receipt["single_sided_materials"] = []
        for m in doc_before.get("materials", []):
            transmission = (m.get("extensions") or {}).get("KHR_materials_transmission", {})
            if float(transmission.get("transmissionFactor", 0)) > 0:
                shown = mat_names.get(m.get("name"), m.get("name"))
                uses = material_meshes.get(m.get("name"), [])
                if uses and all(use["outward_solid"] for use in uses):
                    if m.get("doubleSided", False):
                        m["doubleSided"] = False
                        receipt["single_sided_materials"].append({"material": shown,
                            "partRole": m.get("extras", {}).get("partRole"), "meshes": uses,
                            "previous_double_sided": True, "double_sided": False})
                else:
                    reasons = "; ".join(f"{use['object']}: {use['reason']}" for use in uses
                                        if not use["outward_solid"]) or "no verified mesh use"
                    notes.append(f"See-through material {shown!r} keeps its sidedness: {reasons}.")
            # Blender omits minimum for uniform thin films. Preserve the helper's explicit range while
            # keeping the standard glTF meaning: no thickness texture => uniform maximum, not a gradient.
            iridescence = (m.get("extensions") or {}).get("KHR_materials_iridescence")
            film_range = coating_ranges.get(m.get("name"))
            if iridescence is not None and film_range and "iridescenceThicknessTexture" not in iridescence:
                iridescence.update(film_range)
                receipt["coating_ranges"].append({"material": mat_names.get(m.get("name"), m.get("name")),
                                                   **film_range, "uniform_uses_maximum": True})
            vol = (m.get("extensions") or {}).get("KHR_materials_volume")
            intent = optical_sources.get(m.get("name"), [])
            authored = any(i["effect"] in {"volume", "volume absorption"} for i in intent)
            shown = mat_names.get(m.get("name"), m.get("name"))
            warnings = optical_warnings(intent, m, shown)
            receipt["optical_warnings"].extend(warnings)
            notes.extend(warnings)
            if authored and not vol:
                notes.append(f"material {shown!r} authors a volume (Thickness / Volume Absorption) but Blender's "
                             f"exporter wrote no KHR_materials_volume (a hand-made 'glTF Material Output' group "
                             f"needs an Occlusion socket)")
            if vol and float(vol.get("thicknessFactor", 0.0)) > VOLUME_THICKNESS_SUSPECT_M:
                notes.append(f"material {shown!r}: KHR_materials_volume thicknessFactor {vol['thicknessFactor']:g} "
                             f"looks like millimetres (the convention is metres; a lens is about 0.0015)")
        data, counts = glb_patch.rename(glb_patch.write_chunks(doc_before, binary), nodes=node_names, meshes=mesh_names,
                                        materials=mat_names)
        tmp_glb.write_bytes(data)
        receipt["renamed"] = counts
        ready = True
    finally:
        for dup in duplicates:
            bpy.data.objects.remove(dup, do_unlink=True)
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        for cp in copies.values():
            bpy.data.materials.remove(cp)
        if tmp_scene is not None:
            bpy.data.scenes.remove(tmp_scene)
        receipt["scene_unchanged"] = snapshot(scene) == before
        receipt["object_count"] = len(receipt["objects"])
        receipt["triangle_count"] = sum(o["triangles"] for o in receipt["objects"])
        if not (ready and receipt["scene_unchanged"]):
            tmp_glb.unlink(missing_ok=True)
    if not receipt["scene_unchanged"]:
        raise RuntimeError("the live scene changed during export (invariance check failed)")
    try:
        _replace_with_retry(tmp_glb, path)
    finally:
        tmp_glb.unlink(missing_ok=True)
    receipt["status"] = "exported"
    return receipt
