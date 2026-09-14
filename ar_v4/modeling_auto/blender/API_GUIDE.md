# Available Blender editor capabilities

The host already loaded the current model. Inspect `bpy.context.scene.objects`,
mesh vertices/polygons, world matrices, bounds and material nodes. Coordinates are
Blender world coordinates with Z up; the front preview camera is on negative Y.
Use the supplied scene inspection, never assume dimensions from an example.

Imports supported by both validator and runtime: bpy, bmesh, math, mathutils,
mathutils.bvhtree, mathutils.kdtree and mathutils.geometry. Use BVHTree/KDTree for
local geometry queries. File access, network, external processes and saving/loading
are unavailable to the edit script. The host saves, exports, packs images and
renders. Return one complete `run_blender_python` script for this single session.
Do not change scenes, cameras, lights, world, render settings or collections.
Link new lens objects into an existing collection. Selection and edit mode may
be used for the documented in-memory operators.

For scalar versus array shader values, `tuple(value)` with `except TypeError`
is supported. The Boolean query `hasattr(value, '__len__')` is also allowed for
compatibility. This exception returns only presence; direct private attributes,
`getattr`/`setattr` for `__len__`, and every other private-name query stay blocked.

`bytearray` is available for compact mutable occupancy, flood-fill and visited
masks. It supports integer-size, iterable and bytes constructors and ordinary
index/slice updates. `bytes` remains available. These are in-memory containers;
file access, arbitrary native memory access and private methods stay unavailable.

For shared mesh/material datablocks, the direct no-argument `data.as_pointer()`
call is allowed as a read-only identity query within the current script. It
returns an integer; it does not authorize dereferencing memory or using values
across Blender sessions. Capturing/rebinding the method, obtaining it through
attribute helpers, address conversion and arbitrary native imports are blocked.

The first lens session preserves existing mesh object names, topology, transforms
and visibility. Gently smooth coordinates directly, moving an existing vertex at
most 0.3% of the model's largest extent. Do not add modifiers to source geometry;
apply bounded smoothing without changing topology. Do not rebuild, resize,
symmetrize or remesh frame, bridge or temples. Existing source objects remain.
Create only the two lens objects and tag each with `obj['auto_role']='lens_left'`
or `'lens_right'`. Each must be a closed solid with curved front/back surfaces,
fitted using the actual rim geometry. Original source meshes cannot be reclassified
as editable lens objects to bypass source protection. Preserve them and create the
two separate lens objects. Existing fused optical surfaces cannot be removed by
this operation. If their exact optical faces can be identified, assigning those
faces a transparent material is supported without changing their topology. Do not
make frame/rim/bridge/temple faces transparent. This mechanism is not a generic
segmentation guarantee; uncertain selection remains a visual limitation.

The lens-connection session may change the tagged lenses only. Keep every other
object, vertex, UV, normal, transform and visibility unchanged. The six supplied
images are current model views and an oblique close-up of a tagged lens perimeter.

Material/finish sessions may edit shaders, image texture usage and material
assignment. Geometry, evaluated geometry, UVs, normals, transforms and visibility
are locked exactly. The full textured Meshy asset is loaded for this session;
its mesh names or tags may differ. Inspect what is present. Do not change objects
or geometry to hide material defects.

Texture sampling through `image.pixels` uses compact float32 snapshots, populated
with native `foreach_get` and bounded to 2 GiB of retained buffers. Indices and
slices retain their exact values; images are not resampled. Larger images fall
back to native access. Keep pixel handles and take only needed samples instead
of converting complete 8K images to Python lists or tuples. Pixel writes, image
attribute changes and image method calls invalidate affected cached reads.
The raw saved Astra response is validated and retained byte-for-byte; only its
in-memory execution is adapted. Geometry and scene checks remain mandatory.
The host records native phases and pixel-copy counts alongside each attempt.

Native checks cannot measure fit or guarantee attractive lenses from arbitrary
fused provider meshes. The retained source and each completed revision remain
available, and the user reviews the final rendered result.
