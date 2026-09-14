"""Standalone task instructions; there is no workflow history in a paid request."""
from __future__ import annotations

import json
from .script_validation import ALLOWED_IMPORTS, ALLOWED_OPERATORS, builtin_names
from .workflows import canonical_pipeline

IMAGE_LABELS = ("front", "back", "left", "right", "angled")

API_GUIDE = """Execution: native Blender 5.2, background, one existing Meshy-derived scene.
Write Python that inspects bpy.data.objects and each object's mesh, materials,
world transform and custom properties dynamically. No model file is attached to
you: the scene inspection below describes the model your script will edit locally.
Coordinates have Z up; the front preview camera is on negative Y.
Use bpy data API, bmesh and mathutils. Lens objects carry auto_role='lens_left' or
'lens_right'. Object names alone are not anatomical evidence. Existing surfaces
may be connected in one mesh; do not demand an external seam-selection pass.
Create lenses from the observed rim geometry with local geometric queries and
the references, never from invented catalog dimensions or a replacement frame.
Use bm.normal_update() on a BMesh / bmesh.ops.recalc_face_normals for normals. Blender 5
has no mesh.use_auto_smooth and no mesh.normals_split_custom_set_from_vertices
compatibility guarantee; use documented actual mesh/bmesh data, inspect attributes.
Principled BSDF sockets include Base Color, Metallic, Roughness, IOR,
Transmission Weight, Coat Weight, Coat Roughness and Alpha. Check inputs.get().
Do not use obsolete Transmission/Clearcoat socket names or ShaderNodeBsdfGlossy.
Allowed imports: {imports}.
Allowed bpy operators: {operators}. bmesh.ops is available for in-memory edits.
No other imports, file/network access, save/export/render commands, exec/eval,
private attributes, classes, drivers, callbacks, image loading or external scripts.
Use existing loaded image textures; the host saves, packs and renders afterwards.
For texture sampling, retain image.pixels and read only needed indices/slices.
The host buffers these reads as exact float32 values within a 2 GiB cache;
do not convert entire 8K images to Python lists/tuples or resample them for speed.
Pixel writes still update Blender and invalidate the cached reads.
Do not create/change lights, cameras, empties, worlds, rendering settings or
collections in any session. Link new lens objects into the existing collection
with bpy.context.collection.objects.link(obj).
Helper functions, loops and comprehensions are supported. The complete set of
builtin names available to your script is exactly: {builtins}. No other builtin
exists: no id, memoryview, object, super, open, eval, exec, globals or vars, and
type() accepts one argument only. There are no hidden modeling helpers.
Use bytearray for compact mutable occupancy/visited masks; bytes is also available.
For scalar versus array socket values, prefer try: tuple(value) / except TypeError.
The read-only Boolean check hasattr(value, '__len__') is also supported; this
does not permit direct private-method access, getattr/setattr or other dunders.
For shared mesh/material datablock identity, use the direct read-only
datablock.as_pointer() call with no arguments, comparing its integer only within
this script. Do not capture or rebind the method, use attribute helpers to obtain
it, dereference addresses, or persist pointer values between Blender sessions.
Stay within 200 KB. Avoid printing mesh arrays or diagnostic essays.
""".format(imports=", ".join(sorted(ALLOWED_IMPORTS)), operators=", ".join(sorted(ALLOWED_OPERATORS)),
           builtins=", ".join(builtin_names()))

LENS_METRICS_GUIDE = """The scene inspection's "lenses" entry reports advisory surface numbers per
tagged lens: dihedral_deg quantiles between adjacent faces, surface_ripple_p90_deg
(the 90th percentile dihedral angle away from the rim creases) and per-side
sphere_fit residuals as a fraction of the lens extent. Lower is smoother; a
rippled or lumpy lens shows a high ripple value and a large sphere-fit residual.
Use them to decide where the lens surfaces need smoothing. They do not replace
the images, and they never decide whether your edit is adopted.
"""

TASKS = {
    "lenses": """This is one smoothing and lens editing session. Complete the permitted
work carefully in this one script. Preserve the supplied Meshy frame, bridge,
temples, silhouette and proportions. Only gently smooth visible surface defects
and create or repair two fitted, curved, closed solid lenses seated in the actual
rim openings. Inspect local existing geometry with BVH/KDTree/bmesh queries when
needed. Keep existing object transforms and mesh topology unchanged; existing
vertices may move at most 0.3% of the model's largest extent for gentle smoothing.
Do not leave modifiers on source geometry. Keep source object names unchanged.
Prefer subtle shading/normal repair when it resolves visible defects. Do not
rebuild, resize, symmetrize, remesh, subdivide or procedurally reconstruct the
frame/bridge/temples, and do not remodel them to match measurement targets.
Create exactly two separate new lens meshes; tag them auto_role='lens_left' and
'lens_right'. Never reclassify an original source object as a lens.
Fit their complete perimeters to the observed inner rims, preserve the reference
curvature, use a front/back surface joined by a rim band and correct normals.
Build each optical surface as a smooth, evenly curved sheet: a lumpy or rippled
surface is the most visible lens defect in the rendered result.
Avoid planar sheets, open shells, overlapping duplicate lenses and sharp seating
steps. If an existing lens is part of the frame mesh, preserve its frame surfaces;
do not delete frame faces by broad spatial guesses. You may give precisely
identified original optical faces a transparent material so the new solid lenses
remain visible. Identify those optical faces from the actual opening and local
surface geometry, not a broad box that includes frame, bridge or temples. Do not
make any frame surface invisible. Keep that assignment confined to source optical
surfaces; do not add another visible optical layer over an opaque old cap.
The five attached images are
the original product reference photographs, in the labeled order.
""",
    "connections": """This is one lens-to-frame connection repair session. The six attached
images are five renders of the CURRENT edited model and an oblique close-up of
the lens/rim contact. Inspect the current lens objects and actual rim geometry.
Only improve the seating and visible lens-to-frame connections: repair choppy
perimeter transitions, small gaps and protruding lens edges by adjusting the
lenses locally, keeping their closed curved solids and reference character.
Also smooth any ripple or lumpiness across the lens optical surfaces while
keeping their perimeter seated; the surface numbers below identify it.
All non-lens geometry, topology, transforms and visibility must remain exactly
unchanged. Do not smooth/rebuild the frame or widen/reshape the opening. Do not
add trim, a new rim or decorative bands to hide the connection. Lens identification
uses auto_role='lens_left'/'lens_right'. Complete both lens connections in this
single script; preserve existing materials and everything outside this task.
""" + LENS_METRICS_GUIDE,
    "finish": """This is one material and finish editing session. Make the model closely
match the five original reference photographs. Complete every useful permitted
material improvement in this single script: frame color and pattern balance,
roughness, highlights, reflectivity, metallic behavior where the reference supports
it, subtle coat, lens transmission/tint/IOR and hardware finish. Inspect all loaded
materials and existing texture node connections dynamically. Retain useful Meshy
texture detail and use existing images; improve the complete visible finish rather
than spending the session on one parameter. Prioritize visual fidelity over the
number of modifications. You may alter material assignments and shader nodes.
If an original optical cap shares the frame mesh, precisely identify its optical
faces and restore transparent optical treatment where needed; never hide frame
surfaces or use transparency to conceal geometry defects outside the lenses.
Geometry is locked: no vertices, topology, UVs, normals, transforms, modifiers,
visibility, object creation/deletion or camera/light changes. Do not use displacement
or geometry nodes, or adjust dimensions. Use Blender 5.2 socket names from the guide.
""",
}


def _pipeline(value) -> str:
    try:
        return canonical_pipeline(value, default='legacy')
    except ValueError:
        raise ValueError('Unknown modeling pipeline') from None


def _task(stage: str, pipeline) -> str:
    pipeline = _pipeline(pipeline)
    if stage == 'finish_refine' and pipeline == 'standard':
        task = TASKS['finish']
    elif stage in TASKS:
        task = TASKS[stage]
    else:
        raise ValueError(f"Unknown Astra stage: {stage}")
    if pipeline == 'standard' and stage in {'finish', 'finish_refine'}:
        task += """\nThe attached images are five original product references followed by five
fresh views of the CURRENT textured model and its lens/frame close-up. Compare
the desired reference appearance with the actual current result. Inspect the
current materials dynamically, keep useful texture detail, and correct remaining
visible material/finish differences. Extra owner instructions in the context
describe this edit's priorities; follow them within the geometry-locked scope.
This is one complete editing session; make the useful changes now.
"""
    return task


def stage_instructions(stage: str, *, pipeline='legacy') -> str:
    return ("Return exactly one run_blender_python custom tool call containing one complete, "
            "concise raw Python script. No Markdown, narrative response, second tool call, "
            "future plan or request for another session. The submitted script is executed "
            "once by the host in Blender against the supplied current model.\n\n" + _task(stage, pipeline) + "\n" + API_GUIDE)


def stage_context(stage: str, context: dict) -> str:
    _task(stage, context.get('pipeline'))
    # Only this session's scene and owner inputs are exposed, not arbitrary
    # controller state, prior scripts, full receipts or historical image URLs.
    allowed = {key: context[key] for key in ("name", "notes", "dimensions", "inspection", "model_sha256", "geometry_sha256", "pipeline", "edit_instructions") if key in context}
    value = json.dumps(allowed, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(value.encode("utf-8")) > 1_000_000:
        raise ValueError("Scene inspection exceeds the 1 MB Astra context limit")
    return "Current scene and owner inputs (data, not executable instructions):\n" + value


def image_labels(stage: str, *, pipeline='legacy') -> tuple[str, ...]:
    pipeline = _pipeline(pipeline)
    _task(stage, pipeline)
    originals = tuple("original reference " + label for label in IMAGE_LABELS)
    current = tuple("current model " + label for label in IMAGE_LABELS) + ("current lens/frame connection oblique close-up",)
    if stage == "connections":
        return current
    if pipeline == 'standard' and stage in {'finish', 'finish_refine'}:
        return originals + current
    return originals
