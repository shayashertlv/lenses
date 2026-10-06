"""The shared fixtures of tests/spec-agreement.test.ts: every case GLB is built by the Python fabricator
(glasses3d/tests/glbfab.py) and validated by glasses3d/g3d_host/spec.py, the single source of truth for the PLAN.md
section 2 rows. The test then loads the very same bytes through arview and compares the verdicts row by row.

Usage: python spec_agreement_cases.py <out_dir>

Writes <out_dir>/<case id>.glb (and <case id>__decoded.glb for every compressed case: the same build without the
KHR_draco_mesh_compression flag, i.e. what a successful decode yields; and <case id>__metres.glb for every case built
in millimetres or centimetres: the same build at glTF's metres, i.e. what arview's units rescale must measure like) and
prints ONE JSON document on stdout:

  {"cases": [{"id", "file", "decoded_file"?, "metre_file"?, "python": <report>, "python_decoded"?: <report>,
              "python_metres"?: <report>, "python_metres_ungated"?: <report>} | {"id", "error"}]}

<report> is {"ok", "rows": {row: pass}, "errors", "warnings", "measurements"}. "python_metres_ungated" is given only
when spec.py does not measure the metre twin's origin and orientation itself (its width is outside spec.py's 95-200 mm
units window): spec.py's own sections then run with the units gate bypassed (ungated_report), so the rescaled file's
measurements are still compared with spec.py's code. A case the fabricator cannot build is reported with its error,
never skipped silently. (The real GLBs go through `python -m glasses3d.g3d_host.spec --full`.)
"""
from __future__ import annotations

import json
import math
import sys
import traceback
from pathlib import Path

LENSES = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(LENSES))

import numpy as np                                                      # noqa: E402

from glasses3d.g3d_host.spec import validate_spec                       # noqa: E402
from glasses3d.tests import glbfab                                      # noqa: E402
from glasses3d.tests.glbfab import rot, trans                           # noqa: E402


# ------------------------------------------------------------------------------------------------ part sets
def parts(**changes):
    """The default parts with entries replaced (a value of None removes the part)."""
    out = dict(glbfab.default_parts())
    for name, value in changes.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def frame(bx, role="frame"):
    return (bx, "frame", role)


def deep_wrap_without_left_temple():
    out = dict(glbfab.shield_parts())
    for name in ("Shield_L", "Shield_R"):
        (x0, x1, y0, y1, _, z1), material, role = out[name]
        out[name] = ((x0, x1, y0, y1, -46, z1), material, role)
    out.pop("Temple_L")
    return out


def unnamed():
    return {k.replace("Lens", "Pane"): v for k, v in glbfab.default_parts().items()}


def all_lens():
    return {k: (bx, "lens", "lens") for k, (bx, _, _) in glbfab.default_parts().items()}


def lenses_moved(dx=0.0, y=None, z=None):
    """The lenses shifted by `dx` mm, and/or with their y or z range replaced."""
    out = dict(glbfab.default_parts())
    for k in ("Lens_L", "Lens_R"):
        x0, x1, y0, y1, z0, z1 = out[k][0]
        out[k] = ((x0 + dx, x1 + dx, *(y or (y0, y1)), *(z or (z0, z1))), *out[k][1:])
    return out


def left_lens_only():
    out = dict(glbfab.default_parts())
    out["Lens_R"] = (out["Lens_R"][0], "frame", "frame")
    return out


def split_bridge():
    return parts(Bridge=None, Bridge_L=frame((-9, -1.5, 0, 4, -2, 2)), Bridge_R=frame((1.5, 9, 0, 4, -2, 2)))


def opened_dense_temple(deg):
    out = parts(Temple_L=None)
    for i in range(14):
        z0, z1 = -2 - 10 * i, -2 - 10 * (i + 1)
        dx = (abs(z1) - 2) * math.tan(math.radians(deg))
        out[f"Temple_L{i}"] = ((-70 - dx, -66 - dx, 2, 8, z1, z0), "frame", "temple")
    return out


def screw(base=None):
    """A 2 mm part near the left hinge (the compressed part in the with_screw cases of test_spec.py)."""
    return dict(base or glbfab.default_parts(), Screw=frame((-69, -67, 3, 5, -6, -4)))


# ------------------------------------------------------------------------------------------------ JSON patches
def p_skins(doc, add):
    doc["skins"] = [{"joints": [0]}]


def p_texture_source_missing(doc, add):
    """The frame material samples texture 0, whose image source 9 does not exist (three cannot load the material)."""
    doc["textures"] = [{"source": 9}]
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}


def p_no_asset_version(doc, add):
    doc["asset"] = {"generator": "glasses3d tests"}


def p_texture_index_missing(doc, add):
    """The frame material samples texture 9; the file has no textures (spec.py fails Format for it)."""
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 9}


def p_image_without_source(doc, add):
    """The frame material samples texture 0, whose image 0 has neither a uri nor a bufferView (three cannot load it;
    spec.py reads no bytes for it and passes Format)."""
    doc["images"] = [{"name": "nothing"}]
    doc["textures"] = [{"source": 0}]
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}


def p_lens_material(index):
    """Lens_L's primitive names material `index` (out of range: it is drawn with the glTF default material)."""
    def patch(doc, add):
        next(m for m in doc["meshes"] if m["name"] == "Lens_L")["primitives"][0]["material"] = index
    return patch


def lens_r_raised(dy):
    """The right lens raised by `dy` mm (roll: the lens halves' centres differ by dy)."""
    out = dict(glbfab.default_parts())
    x0, x1, y0, y1, z0, z1 = out["Lens_R"][0]
    out["Lens_R"] = ((x0, x1, y0 + dy, y1 + dy, z0, z1), *out["Lens_R"][1:])
    return out


def p_skinned_bridge(doc, add):
    """The bridge node carries a skin (no JOINTS/WEIGHTS: the drawn pose is the bind pose)."""
    bridge = next(i for i, n in enumerate(doc["nodes"]) if n["name"] == "Bridge")
    doc["skins"] = [{"joints": [bridge]}]
    doc["nodes"][bridge]["skin"] = 0


def p_instancing(doc, add):
    t = add(np.array([[0, 0, 0], [0, 0.030, 0.040]], float))
    for n in doc["nodes"]:
        if n["name"].startswith("Lens"):
            n["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": {"TRANSLATION": t}}}
    doc.setdefault("extensionsUsed", []).append("EXT_mesh_gpu_instancing")
    doc["extensionsRequired"] = ["EXT_mesh_gpu_instancing"]


def p_missing_image(doc, add):
    doc["images"] = [{"uri": "missing_texture.png"}]
    doc["textures"] = [{"source": 0}]
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}


def p_absolute_existing_image(doc, add):
    """An unused image named by an ABSOLUTE URI to a file that exists on this machine (this script: a Windows drive
    path 'C:/...', or a file: URL elsewhere). glTF resolves an absolute URI as a URL, never next to the GLB, so both
    validators refuse it at Format (WP1 review E/F: spec.py used to join it to the folder and pass)."""
    absolute = Path(__file__).resolve().as_posix()
    doc["images"] = [{"uri": absolute if absolute[1:2] == ":" else "file://" + absolute}]


def p_no_scenes(doc, add):
    doc.pop("scenes")
    doc.pop("scene")


def p_empty_scene(doc, add):
    doc["scenes"] = [{"nodes": []}]


def p_zero_triangles(doc, add):
    for m in doc["meshes"]:
        doc["accessors"][m["primitives"][0]["indices"]]["count"] = 0


def p_zero_vertices(doc, add):
    for a in doc["accessors"]:
        if a["type"] == "VEC3":
            a["count"] = 0
    for m in doc["meshes"]:
        m["primitives"][0].pop("indices")


def p_texcoord_minus_one(doc, add):
    doc["images"] = [{"bufferView": 0, "mimeType": "image/png"}]
    doc["textures"] = [{"source": 0}]
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0, "texCoord": -1}


def p_bad_material(doc, add):
    doc["meshes"][0]["primitives"][0]["material"] = 7


def p_volume_mm(doc, add):
    doc["materials"][1]["extensions"]["KHR_materials_volume"] = {"thicknessFactor": 1.5}


def p_reuse_and_unknown_extension(doc, add):
    doc["nodes"].append({"name": "Bridge_copy", "mesh": 0})
    doc["scenes"][0]["nodes"].append(len(doc["nodes"]) - 1)
    doc["extensionsUsed"].append("VENDOR_magic")
    doc["extensionsRequired"] = ["VENDOR_magic"]


def p_deep(doc, add):
    n0 = len(doc["nodes"])
    roots = doc["scenes"][0]["nodes"]
    chain = [{"name": f"empty{i}", "children": [n0 + i + 1]} for i in range(1100)]
    chain[-1]["children"] = roots
    doc["nodes"] += chain
    doc["scenes"][0]["nodes"] = [n0]


def p_index_vec3(doc, add):
    """The bridge's index accessor declared VEC3 (count 12) instead of SCALAR (count 36): not valid glTF."""
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["indices"]]
    acc["type"], acc["count"] = "VEC3", acc["count"] // 3


def part_faces(name):
    """The flat triangle index list of a default part's box (its own 8 vertices)."""
    return glbfab.box(*glbfab.default_parts()[name][0])[1].reshape(-1)


def p_index_redeclared(part, component, normalized=False, first=None):
    """`part`'s indices re-stored with `component` (5123 / 5126 ...), optionally declared normalized, optionally with the
    first value replaced (a fractional FLOAT index). Not valid glTF; the stored values are the same vertex numbers."""
    def patch(doc, add):
        prim = next(m for m in doc["meshes"] if m["name"] == part)["primitives"][0]
        values = part_faces(part).astype(float)
        if first is not None:
            values[0] = first
        ia = add(values, component, "SCALAR")
        if normalized:
            doc["accessors"][ia]["normalized"] = True
        prim["indices"] = ia
    return patch


def p_index_interleaved(doc, add):
    """The bridge's indices as UNSIGNED_SHORT in a bufferView with a 4-byte stride (each value followed by 2 padding
    bytes). spec.py reads the stride and finds nothing invalid; three makes it an InterleavedBufferAttribute, which WebGL
    cannot draw as stored (arview rewrites it, reported)."""
    prim = doc["meshes"][0]["primitives"][0]
    faces = part_faces("Bridge")
    padded = np.zeros(2 * len(faces))
    padded[0::2] = faces
    ia = add(padded, 5123, "SCALAR")
    doc["accessors"][ia]["count"] = len(faces)
    doc["bufferViews"][doc["accessors"][ia]["bufferView"]]["byteStride"] = 4
    prim["indices"] = ia


def p_strip_index_normalized(doc, add):
    """The bridge, a TRIANGLE_STRIP (mode=5), with its index accessor declared normalized: not valid glTF."""
    doc["accessors"][doc["meshes"][0]["primitives"][0]["indices"]]["normalized"] = True


def p_strip_redeclared(component, first=None):
    """The bridge, a TRIANGLE_STRIP (mode=5), with its strip indices re-stored with `component` (5126 FLOAT: not valid
    glTF), optionally with the first value replaced (a fractional index)."""
    def patch(doc, add):
        prim = doc["meshes"][0]["primitives"][0]
        values = glbfab.strip_indices(part_faces("Bridge").reshape(-1, 3)).astype(float)
        if first is not None:
            values[0] = first
        prim["indices"] = add(values, component, "SCALAR")
    return patch


def p_strip_index_vec3(doc, add):
    """The bridge, a TRIANGLE_STRIP (mode=5), with its index accessor declared VEC3 (count // 3 elements, so the first
    3 * (count // 3) stored values): not valid glTF. Both validators triangulate the stored values as a strip."""
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["indices"]]
    acc["type"], acc["count"] = "VEC3", acc["count"] // 3


def p_trs_root(rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0), translation=(0.0, 0.0, 0.0)):
    """Wrap the scene roots in a node with a TRS transform (the glTF `rotation`/`scale`/`translation` path, not
    `matrix`)."""
    def patch(doc, add):
        node = {"name": "Root_TRS", "children": list(doc["scenes"][0]["nodes"])}
        if rotation != (0.0, 0.0, 0.0, 1.0):
            node["rotation"] = list(rotation)
        if scale != (1.0, 1.0, 1.0):
            node["scale"] = list(scale)
        if translation != (0.0, 0.0, 0.0):
            node["translation"] = list(translation)
        doc["nodes"].append(node)
        doc["scenes"][0]["nodes"] = [len(doc["nodes"]) - 1]
    return patch


def quat(axis, deg):
    s, c = math.sin(math.radians(deg) / 2), math.cos(math.radians(deg) / 2)
    return {"x": (s, 0.0, 0.0, c), "y": (0.0, s, 0.0, c), "z": (0.0, 0.0, s, c)}[axis]


UPSIDE_DOWN = trans(0, 4, 0) @ np.diag([-1, -1, 1, 1])
MIRROR_Y = trans(0, 4, 0) @ np.diag([1, -1, 1, 1])
UPSIDE_DOWN_M = trans(0, 0.004, 0) @ np.diag([-1, -1, 1, 1])
MIRROR_Y_M = trans(0, 0.004, 0) @ np.diag([1, -1, 1, 1])

# (id, glbfab.build keyword arguments). The ids are what the test's explicit-difference table names.
CASES: list[tuple[str, dict]] = [
    # ---- meets the spec
    ("valid-default", {}),
    ("valid-identity-node-matrix", dict(node_matrix=np.eye(4))),
    ("valid-origin-y+0.9", dict(offset_mm=(0, 0.9, 0))),
    ("valid-origin-y+0.5-warning", dict(offset_mm=(0, 0.5, 0))),
    ("valid-parent-1e-7-translation", dict(parent_matrix=trans(0, 1e-7, 0))),
    ("valid-parent-1e-7-scale", dict(parent_matrix=np.diag([1 + 1e-7, 1, 1, 1]))),
    ("valid-bridge-triangle-strip", dict(mode=5)),
    ("valid-index-u16", dict(index_component=5123)),
    ("valid-index-u8", dict(index_component=5121)),
    ("valid-shield", dict(parts=glbfab.shield_parts())),
    ("valid-roll-2", dict(pre_mm=rot("z", 2))),
    ("valid-pitch-8", dict(pre_mm=rot("x", 8))),
    ("valid-short-temple-65", dict(parts=parts(Temple_L=frame((-70, -66, 2, 8, -65, -2), "temple")))),
    ("valid-narrow-105mm", dict(pre_mm=np.diag([0.75, 0.75, 0.75, 1]))),
    ("valid-data-uri-buffer", dict(data_uri_buffer=True)),
    ("valid-strided", dict(strided=True)),
    ("valid-texture-4100px", dict(texture_px=(4100, 8))),
    ("valid-crystal-frame", dict(materials={
        "frame": {"name": "Crystal acetate", "extensions": {"KHR_materials_transmission": {"transmissionFactor": 0.8}}},
        "lens": {"name": "Lens tint"}})),
    ("valid-volume-thickness-mm-warning", dict(patch=p_volume_mm)),
    ("valid-shared-mesh-unknown-extension", dict(patch=p_reuse_and_unknown_extension)),
    ("valid-deep-hierarchy", dict(patch=p_deep)),
    ("valid-opened-temple", dict(parts=parts(Temple_L=frame((-75, -71, 2, 8, -140, -30), "temple")))),
    ("valid-plate-back", dict(parts=parts(Logo=frame((70, 74, 3, 7, -60, -40))))),
    ("valid-plate-in-band", dict(parts=parts(Logo=frame((70, 74, 3, 7, -30, -20))))),
    ("valid-splay", dict(parts=parts(Temple_R=frame((70, 74, 2, 8, -140, -2), "temple"),
                                     Brow_R=frame((9, 74, 4, 10, -2, 2))))),
    ("valid-opened-dense-5deg", dict(parts=opened_dense_temple(5))),
    ("valid-opened-dense-8deg", dict(parts=opened_dense_temple(8))),
    ("valid-ornament-in-front-band", dict(parts=parts(Ornament=frame((70, 84, 0, 4, -2, 2))))),
    ("valid-left-lens-only-front-judged", dict(parts=left_lens_only())),
    ("valid-roles-on-nodes", dict(role_where="node")),
    ("valid-legacy-part-role", dict(role_key="part_role")),
    ("valid-name-tokens-only", dict(roles=False)),
    ("valid-last-resort-transmission", dict(roles=False, parts=unnamed(),
                                            material_names={"frame": "Frame", "lens": "Clear"})),
    # ---- units
    ("units-millimetres", dict(scale=1.0)),
    ("units-centimetres", dict(scale=0.1)),
    ("units-70-units-mm-band", dict(scale=1.0, pre_mm=np.diag([0.5, 0.5, 0.5, 1]))),
    ("units-84mm", dict(pre_mm=np.diag([0.6, 0.6, 0.6, 1]))),
    ("units-238mm", dict(pre_mm=np.diag([1.7, 1.7, 1.7, 1]))),
    ("units-millimetres-offset-y5", dict(scale=1.0, offset_mm=(0, 5, 0))),
    # ---- origin
    ("origin-y+5", dict(offset_mm=(0, 5, 0))),
    ("origin-y-5", dict(offset_mm=(0, -5, 0))),
    ("origin-y+1.1", dict(offset_mm=(0, 1.1, 0))),
    ("origin-x+3", dict(offset_mm=(3, 0, 0))),
    ("origin-z+5", dict(offset_mm=(0, 0, 5))),
    ("origin-r9-like-y+10.23", dict(offset_mm=(0, 10.23, 0))),
    ("origin-lenses-shifted-x+3", dict(parts=lenses_moved(dx=3))),
    ("origin-left-lens-only-x+3", dict(parts=left_lens_only(), offset_mm=(3, 0, 0))),
    ("origin-no-bridge", dict(parts=parts(Bridge=None))),
    ("origin-split-bridge", dict(parts=split_bridge())),
    ("origin-split-bridge-y-8", dict(parts=split_bridge(), offset_mm=(0, -8, 0))),
    ("origin-shield-moved-y-15", dict(parts=glbfab.shield_parts(), offset_mm=(0, -15, 0))),
    ("origin-sparse-lowered-corner", dict(sparse_shift=[-0.009, -0.003, -0.002])),
    # ---- orientation (vertices)
    ("orientation-z-up", dict(rotate_z_up=True)),
    ("orientation-roll+25", dict(pre_mm=rot("z", 25))),
    ("orientation-roll-25", dict(pre_mm=rot("z", -25))),
    ("orientation-pitch+25", dict(pre_mm=rot("x", 25))),
    ("orientation-pitch-25", dict(pre_mm=rot("x", -25))),
    ("orientation-faces-backwards", dict(pre_mm=rot("y", 180))),
    ("orientation-upside-down", dict(pre_mm=UPSIDE_DOWN)),
    ("orientation-mirrored-y", dict(pre_mm=MIRROR_Y)),
    ("orientation-lenses-behind-front", dict(parts=lenses_moved(z=(-61, -59)))),
    ("orientation-temple-left-missing", dict(parts=parts(Temple_L=None))),
    # A deep wrap shield (lenses 47 mm deep, 45 mm tall) with a temple missing: within LENS_AXIS_MARGIN_MM, so the lens box
    # says nothing about up (a warning) and only the missing temple fails (WP1 review E/F: the margin was 2 mm).
    ("orientation-deep-wrap-temple-left-missing", dict(parts=deep_wrap_without_left_temple())),
    ("orientation-both-temples-missing", dict(parts=parts(Temple_L=None, Temple_R=None))),
    # A missing temple takes the depth with it: the up axis is then judged on the lenses (a temple-free measure), so a
    # +Y-up front reports its missing temples and nothing about the axis, while a wrong axis is still caught.
    ("orientation-hinge-stubs-only", dict(parts=parts(Temple_L=frame((-70, -66, 2, 8, -25, -2), "temple"),
                                                      Temple_R=frame((66, 70, 2, 8, -25, -2), "temple")))),
    ("orientation-hinge-stubs-upside-down", dict(pre_mm=UPSIDE_DOWN,
                                                 parts=parts(Temple_L=frame((-70, -66, 2, 8, -25, -2), "temple"),
                                                             Temple_R=frame((66, 70, 2, 8, -25, -2), "temple")))),
    ("orientation-no-temples-upside-down", dict(pre_mm=UPSIDE_DOWN, parts=parts(Temple_L=None, Temple_R=None))),
    ("orientation-z-up-no-temples", dict(rotate_z_up=True, parts=parts(Temple_L=None, Temple_R=None))),
    ("orientation-short-temple-55", dict(parts=parts(Temple_L=frame((-70, -66, 2, 8, -55, -2), "temple")))),
    ("orientation-narrow-temples", dict(parts=parts(Temple_L=frame((-37, -33, 2, 8, -140, -2), "temple"),
                                                    Temple_R=frame((33, 37, 2, 8, -140, -2), "temple")))),
    ("orientation-roots-1.5mm-above-warning", dict(parts=lenses_moved(y=(-8, 15)))),
    # ---- the orientation thresholds, pinned on both sides (roll 3 mm, pitch 15 deg, reach -60 mm, front 30 %)
    ("orientation-roll-2.9mm-passes", dict(parts=lens_r_raised(2.9))),
    ("orientation-roll-3.1mm-fails", dict(parts=lens_r_raised(3.1))),
    ("orientation-pitch-14.5-passes", dict(pre_mm=rot("x", 14.5))),
    ("orientation-pitch-15.5-fails", dict(pre_mm=rot("x", 15.5))),
    ("orientation-pitch-minus-15.5-fails", dict(pre_mm=rot("x", -15.5))),
    ("orientation-reach-60.5-passes", dict(parts=parts(Temple_L=frame((-70, -66, 2, 8, -60.5, -2), "temple")))),
    ("orientation-reach-59.5-fails", dict(parts=parts(Temple_L=frame((-70, -66, 2, 8, -59.5, -2), "temple")))),
    ("orientation-roots-below-lens-centre", dict(parts=lenses_moved(y=(-3, 18)))),
    ("orientation-lenses-just-behind-front-30pct", dict(parts=lenses_moved(z=(-43.5, -41.5)))),
    ("orientation-lenses-inside-front-30pct", dict(parts=lenses_moved(z=(-40, -38)))),
    # ---- orientation through node transforms (the Transforms row fails too; both measure in world space)
    ("transforms-parent-roll+25", dict(parent_matrix=rot("z", 25))),
    ("transforms-parent-pitch+25", dict(parent_matrix=rot("x", 25))),
    ("transforms-parent-upside-down", dict(parent_matrix=UPSIDE_DOWN_M)),
    ("transforms-parent-mirrored-y", dict(parent_matrix=MIRROR_Y_M)),
    ("transforms-trs-roll+25", dict(patch=p_trs_root(rotation=quat("z", 25)))),
    ("transforms-trs-pitch-25", dict(patch=p_trs_root(rotation=quat("x", -25)))),
    ("transforms-trs-mirrored-y", dict(patch=p_trs_root(scale=(1.0, -1.0, 1.0), translation=(0.0, 0.004, 0.0)))),
    ("transforms-node-lens-r-rolled", dict(node_matrix=rot("z", 25), target="Lens_R")),
    ("transforms-parent-1e-5-translation", dict(parent_matrix=trans(0, 1e-5, 0))),
    ("transforms-parent-1e-5-scale", dict(parent_matrix=np.diag([1 + 1e-5, 1, 1, 1]))),
    ("transforms-parent-1mm", dict(parent_matrix=trans(0, 0.001, 0))),
    ("transforms-instancing", dict(patch=p_instancing)),
    # ---- geometry
    ("geometry-non-finite", dict(nan=True, target="Brow_L")),
    ("geometry-lines", dict(mode=1, target="Brow_L")),
    ("geometry-lines-lens", dict(mode=1, target="Lens_L")),
    ("geometry-morph", dict(morph=True, target="Brow_L")),
    ("geometry-animation", dict(animation=True)),
    ("geometry-skins", dict(patch=p_skins)),
    ("geometry-skinned-bridge", dict(patch=p_skinned_bridge)),
    ("geometry-index-count-35", dict(index_count=35, target="Brow_L")),
    ("geometry-index-99", dict(index_value=99, target="Brow_L")),
    ("geometry-index-99-bridge", dict(index_value=99)),
    ("geometry-index-i16-negative", dict(index_component=5122, index_value=-1, target="Brow_L")),
    ("geometry-index-i16", dict(index_component=5122, target="Brow_L")),
    ("geometry-index-i16-bridge", dict(index_component=5122)),
    ("geometry-index-vec3-bridge", dict(patch=p_index_vec3)),
    # index declarations WebGL cannot draw as stored: arview fails the row as spec.py does, rewrites them and RENDERS
    ("geometry-index-normalized-bridge", dict(patch=p_index_redeclared("Bridge", 5123, normalized=True))),
    ("geometry-index-normalized", dict(patch=p_index_redeclared("Brow_L", 5123, normalized=True))),
    ("geometry-index-float-bridge", dict(patch=p_index_redeclared("Bridge", 5126))),
    ("geometry-index-float", dict(patch=p_index_redeclared("Brow_L", 5126))),
    ("geometry-index-interleaved-bridge", dict(patch=p_index_interleaved)),
    ("geometry-strip-index-normalized-bridge", dict(mode=5, patch=p_strip_index_normalized)),
    ("geometry-strip-index-vec3-bridge", dict(mode=5, patch=p_strip_index_vec3)),
    # a fractional FLOAT index names no vertex: out of range, so arview refuses (spec.py fails the row)
    ("geometry-index-float-fractional", dict(patch=p_index_redeclared("Brow_L", 5126, first=2.5))),
    # strips and fans with a signed or FLOAT index (three would wrap/truncate them through setIndex(Array)): arview
    # rebuilds them from the stored values, so a stored -1 or 2.5 is refused as stored, and valid values render
    ("geometry-strip-index-i16-negative-bridge", dict(mode=5, index_component=5122, index_value=-1)),
    ("geometry-strip-index-float-fractional-bridge", dict(mode=5, patch=p_strip_redeclared(5126, first=2.5))),
    ("geometry-strip-index-float-bridge", dict(mode=5, patch=p_strip_redeclared(5126))),
    ("geometry-strip-index-i16-bridge", dict(mode=5, index_component=5122)),
    ("geometry-fan-index-normalized-bridge", dict(mode=6, patch=p_strip_index_normalized)),
    ("geometry-zero-triangles", dict(patch=p_zero_triangles)),
    ("geometry-zero-vertices", dict(patch=p_zero_vertices)),
    # ---- components
    ("components-no-lens", dict(roles=False, parts=unnamed(), materials={"frame": {"name": "Frame"},
                                                                         "lens": {"name": "Pane"}})),
    ("components-everything-lens", dict(parts=all_lens())),
    ("components-lens-without-material", dict(no_material=("Lens_L", "Lens_R"))),
    ("components-shared-material", dict(parts=parts(Bridge=((-9, 9, 0, 4, -2, 2), "lens", "frame")))),
    # ---- format
    ("format-texcoord-minus-one", dict(patch=p_texcoord_minus_one)),
    ("format-material-7", dict(patch=p_bad_material)),
    ("format-missing-image", dict(patch=p_missing_image)),
    ("format-absolute-uri-existing-file", dict(patch=p_absolute_existing_image)),
    ("format-no-scenes", dict(patch=p_no_scenes)),
    ("format-empty-scene", dict(patch=p_empty_scene)),
    ("format-no-asset-version", dict(patch=p_no_asset_version)),
    ("format-texture-source-missing", dict(patch=p_texture_source_missing)),
    ("format-texture-index-missing", dict(patch=p_texture_index_missing)),
    ("format-image-without-source", dict(patch=p_image_without_source)),
    ("format-lens-material-7", dict(patch=p_lens_material(7))),
    # ---- compressed (the fabricator flags KHR_draco_mesh_compression but keeps the plain accessors: the Python
    # validator bounds the flagged primitives by their POSITION min/max; a __decoded twin is written for each)
    ("compressed-bridge", dict(compressed=True)),
    ("compressed-all", dict(compressed="all")),
    ("compressed-all-millimetres", dict(compressed="all", scale=1.0)),
    ("compressed-temple-left", dict(compressed=True, target="Temple_L")),
    ("compressed-lens-right", dict(compressed=True, target="Lens_R")),
    ("compressed-lens-right-rolled", dict(compressed=True, target="Lens_R", pre_mm=rot("z", 25))),
    ("compressed-bridge-y+10", dict(compressed=True, offset_mm=(0, 10, 0))),
    ("compressed-screw-y+10", dict(parts=screw(), compressed=True, target="Screw", offset_mm=(0, 10, 0))),
    ("compressed-screw-millimetres", dict(parts=screw(), compressed=True, target="Screw", scale=1.0)),
    ("compressed-screw-no-temples", dict(parts=screw(parts(Temple_L=None, Temple_R=None)), compressed=True,
                                         target="Screw")),
    ("compressed-screw-upside-down", dict(parts=screw(), compressed=True, target="Screw", pre_mm=UPSIDE_DOWN)),
    ("compressed-screw-x+3", dict(parts=screw(), compressed=True, target="Screw", offset_mm=(3, 0, 0))),
]


def summary(report: dict) -> dict:
    return {"ok": report["ok"], "rows": {r: v["pass"] for r, v in report["rows"].items()},
            "errors": report["errors"], "warnings": report["warnings"], "measurements": report["measurements"]}


def ungated_report(path: Path) -> dict:
    """spec.py's own sections on `path` (validate_spec's order), with the units gate bypassed: origin and orientation
    are measured although the width is outside spec.py's 95-200 mm window. Uses spec.py internals on purpose (the
    numbers must come from spec.py's code, not a copy); if they change, this raises and the case reports the error."""
    from glasses3d.g3d_host import spec
    rep = spec.Report(path)
    st = spec._State(spec.load(path))
    spec._format(rep, st)
    spec._geometry(rep, st)
    spec._transforms(rep, st)
    spec._textures(rep, st)
    spec._components(rep, st)
    g = spec._Geo(st)
    spec._units(rep, g)
    spec._orientation(rep, g)
    spec._origin(rep, g, st)
    return summary(rep.to_dict())


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    for cid, kw in CASES:
        try:
            path = out / f"{cid}.glb"
            path.write_bytes(glbfab.build(**kw))
            entry = {"id": cid, "file": str(path), "python": summary(validate_spec(path))}
            if kw.get("compressed"):
                twin = out / f"{cid}__decoded.glb"
                twin.write_bytes(glbfab.build(**{k: v for k, v in kw.items() if k != "compressed"}))
                entry["decoded_file"] = str(twin)
                entry["python_decoded"] = summary(validate_spec(twin))
            if kw.get("scale", 0.001) != 0.001:
                # The same geometry at glTF's metres: arview rescales the millimetre/centimetre file and must measure
                # exactly what spec.py measures on this twin (the compressed flag is dropped: the decoded truth).
                metres = out / f"{cid}__metres.glb"
                metres.write_bytes(glbfab.build(**dict({k: v for k, v in kw.items() if k != "compressed"}, scale=0.001)))
                entry["metre_file"] = str(metres)
                entry["python_metres"] = summary(validate_spec(metres))
                if not entry["python_metres"]["rows"]["units"]:
                    entry["python_metres_ungated"] = ungated_report(metres)
            cases.append(entry)
        except Exception as exc:                                        # noqa: BLE001 - reported, never skipped
            cases.append({"id": cid, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()})
    print(json.dumps({"cases": cases}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
