import json
import struct
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import spec as S
from glasses3d.g3d_host.spec import ROWS, validate_spec
from glasses3d.tests import glbfab
from glasses3d.tests.glbfab import rot, trans

REPO = Path(__file__).resolve().parents[2]
JOBS = REPO / "automation/data/blender_agent/studio/jobs"


def unnamed_parts():
    """The default parts with no name that says 'lens'."""
    return {k.replace("Lens", "Pane"): v for k, v in glbfab.default_parts().items()}


def without(*names, parts=None):
    return {k: v for k, v in (parts or glbfab.default_parts()).items() if k not in names}


def replace(name, bx, parts=None):
    parts = dict(parts or glbfab.default_parts())
    parts[name] = (bx, *parts[name][1:])
    return parts


def all_lens_parts():
    return {k: (bx, "lens", "lens") for k, (bx, _, _) in glbfab.default_parts().items()}


def lenses_behind_front():
    parts = glbfab.default_parts()
    for k in ("Lens_L", "Lens_R"):
        x0, x1, y0, y1, _, _ = parts[k][0]
        parts[k] = ((x0, x1, y0, y1, -61, -59), *parts[k][1:])
    return parts


def check(tmp_path, name="m.glb", **kw):
    p = tmp_path / name
    p.write_bytes(glbfab.build(**kw))
    return validate_spec(p)


def failed(r):
    return {k for k, v in r["rows"].items() if not v["pass"]}


# ---------------------------------------------------------------------------------------------- JSON patches
def p_skins(doc, add):
    doc["skins"] = [{"joints": [0]}]


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


def p_no_scenes(doc, add):
    doc.pop("scenes")
    doc.pop("scene")


def p_empty_scene(doc, add):
    doc["scenes"] = [{"nodes": []}]


def p_zero_triangles(doc, add):
    for m in doc["meshes"]:
        doc["accessors"][m["primitives"][0]["indices"]]["count"] = 0


def p_texcoord_minus_one(doc, add):
    doc["images"] = [{"bufferView": 0, "mimeType": "image/png"}]
    doc["textures"] = [{"source": 0}]
    doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0, "texCoord": -1}


# (kw, the exact set of failed rows, a text that must appear in the errors)
CASES = [
    (dict(scale=1.0), {"units", "origin", "orientation"}, "millimetre-scaled"),
    (dict(scale=0.1), {"units", "origin", "orientation"}, "centimetre-scaled"),
    (dict(offset_mm=(0, 5, 0)), {"origin"}, "bridge underside is at y +5.00"),
    (dict(offset_mm=(3, 0, 0)), {"origin"}, "the lenses are centred at x +3.00 mm"),
    (dict(offset_mm=(0, 0, 5)), {"origin"}, "outside the bridge underside's depth"),
    (dict(offset_mm=(0, 1.1, 0)), {"origin"}, "y +1.10 mm"),                      # tolerance pin: fails
    (dict(parent_matrix=trans(0, 1e-5, 0)), {"transforms"}, "non-identity"),      # tolerance pin: fails
    (dict(parent_matrix=np.diag([1 + 1e-5, 1, 1, 1])), {"transforms"}, "non-identity"),
    (dict(parent_matrix=trans(0, 0.001, 0)), {"transforms"}, "non-identity"),
    (dict(patch=p_instancing), {"transforms"}, "EXT_mesh_gpu_instancing"),
    (dict(nan=True, target="Brow_L"), {"geometry"}, "non-finite"),
    (dict(mode=1, target="Brow_L"), {"geometry"}, "mode 1 (LINES)"),
    (dict(morph=True, target="Brow_L"), {"geometry"}, "morph targets"),
    (dict(animation=True), {"geometry"}, "animations"),
    (dict(patch=p_skins), {"geometry"}, "skins present"),
    (dict(index_count=35, target="Brow_L"), {"geometry"}, "35 indices is not a multiple of 3"),
    (dict(index_value=99, target="Brow_L"), {"geometry"}, "index 99 >= vertex count 8"),
    (dict(index_component=5122, index_value=-1, target="Brow_L"), {"geometry"}, "index -1 < 0"),   # numpy would wrap
    (dict(index_component=5122, target="Brow_L"), {"geometry"}, "componentType 5122 is not valid glTF"),
    (dict(patch=p_zero_triangles), {"geometry", "units", "origin", "orientation"}, "the file has no triangles"),
    (dict(roles=False, parts=unnamed_parts(), materials={"frame": {"name": "Frame"}, "lens": {"name": "Pane"}}),
     {"components"}, "no lens identified"),
    (dict(parts=all_lens_parts()), {"components", "orientation"}, "no frame/temple primitives"),
    (dict(no_material=("Lens_L", "Lens_R")), {"components"}, "has no material"),
    (dict(parts=lenses_behind_front()), {"orientation"}, "behind the front"),
    (dict(pre_mm=trans(0, 4, 0) @ np.diag([-1, -1, 1, 1])), {"orientation"}, "not +Y up"),         # upside down
    (dict(pre_mm=trans(0, 4, 0) @ np.diag([1, -1, 1, 1])), {"orientation"}, "not +Y up"),          # mirrored in Y
    (dict(parts=without("Bridge")), {"origin"}, "origin unverifiable"),
    (dict(patch=p_missing_image), {"format"}, "missing external resource"),
    (dict(patch=p_no_scenes), {"format"}, "no scenes"),
    (dict(patch=p_texcoord_minus_one), {"format"}, "texCoord -1"),
    (dict(parts=without("Temple_L")), {"orientation"}, "temple left missing"),
]


@pytest.mark.parametrize("kw, rows, text", CASES, ids=[f"{sorted(c[1])[0]}-{c[2][:28]}" for c in CASES])
def test_each_requirement_fails_its_own_row(tmp_path, kw, rows, text):
    r = check(tmp_path, **kw)
    assert failed(r) == rows, r["errors"]
    assert any(text in e for e in r["errors"]), r["errors"]


@pytest.mark.parametrize("kw, row, text", [
    (dict(rotate_z_up=True), "orientation", "Y is not the thinnest axis"),
    (dict(pre_mm=rot("z", 25)), "orientation", "rolled"),
    (dict(pre_mm=rot("z", -25)), "orientation", "rolled"),
    (dict(pre_mm=rot("x", 25)), "orientation", "pitched"),
    (dict(pre_mm=rot("x", -25)), "orientation", "pitched"),
    (dict(pre_mm=rot("y", 180)), "orientation", "faces backwards"),
    (dict(index_value=99), "origin", "excluded after geometry failures"),       # a broken bridge is not measured
])
def test_orientation_and_cascade_failures(tmp_path, kw, row, text):
    r = check(tmp_path, **kw)
    assert row in failed(r), r["errors"]
    assert any(text in e for e in r["errors"]), r["errors"]


@pytest.mark.parametrize("kw", [
    dict(),
    dict(node_matrix=np.eye(4)),
    dict(offset_mm=(0, 0.9, 0)),                                  # tolerance pin: 0.9 mm passes
    dict(parent_matrix=trans(0, 1e-7, 0)),                        # tolerance pin: 1e-7 passes
    dict(parent_matrix=np.diag([1 + 1e-7, 1, 1, 1])),
    dict(mode=5),                                                 # the bridge as a TRIANGLE_STRIP
    dict(index_component=5123),                                   # unsigned short and byte indices are valid
    dict(index_component=5121),
    dict(parts=glbfab.shield_parts()),                            # the nose notch (lens) is the bridge underside
    dict(pre_mm=rot("z", 2)),                                     # small roll / pitch are allowed
    dict(pre_mm=rot("x", 8)),
    dict(parts=replace("Temple_L", (-70, -66, 2, 8, -65, -2))),   # a short temple still reaches -60 mm
])
def test_valid_variants_pass(tmp_path, kw):
    r = check(tmp_path, **kw)
    assert r["ok"], r["errors"]


def test_synthetic_glasses_meet_the_spec(tmp_path):
    r = check(tmp_path)
    assert r["ok"], r["errors"]
    m = r["measurements"]
    assert m["width_mm"] == pytest.approx(140.0)
    assert m["bridge_underside_mm"]["y"] == pytest.approx(0.0, abs=1e-3)
    assert m["temple_roots_above_lens_centre_mm"] == pytest.approx(14.5, abs=0.01)
    assert m["lens_halves_dy_mm"] == pytest.approx(0.0, abs=1e-6)
    assert m["temple_slope_deg"] == {"left": 0.0, "right": 0.0}
    assert {x["name"]: x["class"] for x in r["materials"]} == {"Frame acetate": "frame", "Lens clear": "lens"}
    assert r["live_site_would_fail"] == [] and r["warnings"] == []


def test_upside_down_reports_the_measured_heights(tmp_path):
    r = check(tmp_path, pre_mm=trans(0, 4, 0) @ np.diag([-1, -1, 1, 1]))
    assert r["measurements"]["bridge_underside_mm"]["y"] == pytest.approx(0.0, abs=1e-3)
    assert r["measurements"]["temple_roots_above_lens_centre_mm"] == pytest.approx(-14.5, abs=0.01)


def test_shield_bridge_underside_is_the_lens_notch(tmp_path):
    r = check(tmp_path, parts=glbfab.shield_parts())
    assert r["measurements"]["bridge_underside_mm"] == {"y": 0.0, "z_range": [-1.0, 1.0], "surface": "lens"}
    moved = check(tmp_path, "moved.glb", parts=glbfab.shield_parts(), offset_mm=(0, -15, 0))
    assert failed(moved) == {"origin"}                           # the top bar's underside is not the bridge


def opened_dense_temple(deg):
    """Temple_L built from fourteen 10 mm segments and opened outward by `deg` about the hinge (the front band
    reaches z -23 mm, so the first two segments sit inside it)."""
    parts = without("Temple_L")
    for i in range(14):
        z0, z1 = -2 - 10 * i, -2 - 10 * (i + 1)
        dx = (abs(z1) - 2) * np.tan(np.radians(deg))
        parts[f"Temple_L{i}"] = ((-70 - dx, -66 - dx, 2, 8, z1, z0), "frame", "temple")
    return parts


def splayed():
    parts = glbfab.default_parts()
    parts["Temple_R"] = ((70, 74, 2, 8, -140, -2), "frame", "temple")
    parts["Brow_R"] = ((9, 74, 4, 10, -2, 2), "frame", "frame")
    return parts


@pytest.mark.parametrize("parts", [
    replace("Temple_L", (-75, -71, 2, 8, -140, -30)),                       # one temple further out
    dict(glbfab.default_parts(), Logo=((70, 74, 3, 7, -60, -40), "frame", "frame")),
    dict(glbfab.default_parts(), Logo=((70, 74, 3, 7, -30, -20), "frame", "frame")),   # inside the front band
    splayed(),                                                              # one brow and temple further out
    opened_dense_temple(5), opened_dense_temple(8),
], ids=["opened", "plate-back", "plate-in-band", "splay", "dense-5deg", "dense-8deg"])
def test_origin_x_is_judged_on_the_lenses_not_the_temples_or_ornaments(tmp_path, parts):
    r = check(tmp_path, parts=parts)
    assert r["ok"], r["errors"]
    m = r["measurements"]
    assert m["origin_x_judged_on"] == "lenses" and m["lens_centre_x_mm"] == pytest.approx(0.0, abs=1e-3)
    assert m["bridge_underside_mm"]["y"] == pytest.approx(0.0, abs=1e-3)
    assert any("although the lenses are centred" in w for w in r["warnings"]), r["warnings"]


def test_origin_x_falls_back_to_the_clipped_front_without_lenses_on_both_sides(tmp_path):
    parts = glbfab.default_parts()
    parts["Lens_R"] = (parts["Lens_R"][0], "frame", "frame")                # only the left lens is a lens
    r = check(tmp_path, parts=parts)
    assert r["ok"], r["errors"]
    assert r["measurements"]["origin_x_judged_on"] == "front"
    assert any("judged on the front region" in w for w in r["warnings"]), r["warnings"]
    r = check(tmp_path, "moved.glb", parts=parts, offset_mm=(3, 0, 0))
    assert failed(r) == {"origin"} and any("the front (z >= " in e and "centred at x +3.00 mm" in e
                                           for e in r["errors"]), r["errors"]


def test_front_region_is_the_clipped_surface_not_the_vertices_inside_it():
    """A slanted triangle whose only vertex inside the band is at x = 0 still reaches x = -10 mm at the band plane."""
    T = np.array([[[0.0, 0.0, 0.0], [-0.020, 0.0, -0.050], [0.0, 0.001, -0.050]]])
    P = S.plane_points(T, 2, -0.025)
    assert P[:, 0].min() == pytest.approx(-0.010)


def test_split_bridge_with_no_crossing_is_unverifiable_at_any_offset(tmp_path):
    parts = without("Bridge")
    parts["Bridge_L"] = ((-9, -1.5, 0, 4, -2, 2), "frame", "frame")
    parts["Bridge_R"] = ((1.5, 9, 0, 4, -2, 2), "frame", "frame")
    for i, off in enumerate([(0, 0, 0), (0, -8, 0), (0, 0, -12)]):
        r = check(tmp_path, f"s{i}.glb", parts=parts, offset_mm=off)
        assert "origin" in failed(r) and any("origin unverifiable" in e for e in r["errors"]), r["errors"]


def test_temples_need_45_mm_and_60_mm_per_side(tmp_path):
    narrow = replace("Temple_R", (33, 37, 2, 8, -140, -2), replace("Temple_L", (-37, -33, 2, 8, -140, -2)))
    r = check(tmp_path, parts=narrow)
    assert any("temple left missing" in e for e in r["errors"]) and any("temple right missing" in e
                                                                          for e in r["errors"])
    short = replace("Temple_L", (-70, -66, 2, 8, -55, -2))
    r = check(tmp_path, "short.glb", parts=short)
    assert failed(r) == {"orientation"} and any("temple left missing" in e for e in r["errors"]), r["errors"]


def test_unit_window_and_warning_band(tmp_path):
    for i, s in enumerate((0.6, 1.7)):
        r = check(tmp_path, f"u{i}.glb", pre_mm=np.diag([s, s, s, 1]))
        assert "units" in failed(r), r["errors"]
    r = check(tmp_path, "u2.glb", pre_mm=np.diag([0.75, 0.75, 0.75, 1]))           # 105 mm: unusual but passes
    assert "units" not in failed(r)
    assert any("outside the usual" in w for w in r["warnings"]), r["warnings"]


def test_never_raises_on_broken_files(tmp_path):
    def bad_material(doc, add):
        doc["meshes"][0]["primitives"][0]["material"] = 7

    def image_in_missing_buffer(doc, add):                       # buffer 1 is appended after the build
        doc["bufferViews"].append({"buffer": 1, "byteLength": 10})
        doc["images"] = [{"bufferView": len(doc["bufferViews"]) - 1, "mimeType": "image/png"}]
        doc["textures"] = [{"source": 0}]
        doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}

    def zero_vertices(doc, add):
        for a in doc["accessors"]:
            if a["type"] == "VEC3":
                a["count"] = 0
        for m in doc["meshes"]:
            m["primitives"][0].pop("indices")

    def deep(doc, add):
        n0 = len(doc["nodes"])
        roots = doc["scenes"][0]["nodes"]
        chain = [{"name": f"empty{i}", "children": [n0 + i + 1]} for i in range(1100)]
        chain[-1]["children"] = roots
        doc["nodes"] += chain
        doc["scenes"][0]["nodes"] = [n0]

    for i, (patch, row) in enumerate([(bad_material, "format"), (zero_vertices, "geometry"), (deep, None)]):
        r = check(tmp_path, f"b{i}.glb", patch=patch)
        assert set(r["rows"]) == set(ROWS)
        if row:
            assert row in failed(r), r["errors"]
        else:
            assert r["ok"], r["errors"]                           # a 1100-deep hierarchy is legal glTF
    # an image whose bufferView sits in an external buffer that does not exist
    data = glbfab.build(patch=image_in_missing_buffer)
    doc, binc = glbfab_split(data)
    doc["buffers"].append({"byteLength": 10, "uri": "missing.bin"})
    p = tmp_path / "missing_buffer.glb"
    p.write_bytes(glbfab_join(doc, binc))
    r = validate_spec(p)
    assert "format" in failed(r) and any("missing.bin" in e for e in r["errors"]), r["errors"]


def glbfab_split(data):
    jl = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + jl])
    return doc, data[20 + jl + 8:]


def glbfab_join(doc, binc):
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binc), 0x004E4942) + binc
    return b"glTF" + struct.pack("<II", 2, 12 + len(out)) + out


def test_unreadable_file_fails_every_row(tmp_path):
    p = tmp_path / "bad.glb"
    p.write_bytes(b"not a glb at all, sorry")
    r = validate_spec(p)
    assert failed(r) == set(ROWS)


def test_empty_default_scene_fails_format_and_geometry(tmp_path):
    r = check(tmp_path, patch=p_empty_scene)
    assert {"format", "geometry"} <= failed(r)
    assert any("has no nodes" in e for e in r["errors"]), r["errors"]


def test_compressed_geometry_is_unverified_not_failed(tmp_path):
    r = check(tmp_path, compressed=True)                          # the bridge only
    assert r["ok"], r["errors"]
    assert any("not decoded" in w for w in r["warnings"])
    assert any("origin unverified" in w for w in r["warnings"]), r["warnings"]
    assert any("KHR_draco_mesh_compression" in x for x in r["live_site_would_fail"])
    r = check(tmp_path, "all.glb", compressed="all")
    assert r["ok"], r["errors"]
    assert any("origin and orientation unverified" in w for w in r["warnings"]), r["warnings"]
    assert r["measurements"]["width_mm"] == pytest.approx(140.0)         # units from the POSITION bounds
    r = check(tmp_path, "all_mm.glb", compressed="all", scale=1.0)
    assert failed(r) == {"units", "origin", "orientation"}, r["errors"]


def with_screw(**kw):
    """The default glasses plus a 2 mm Draco-flagged 'Screw' near the left hinge (not decoded by the validator)."""
    parts = dict(kw.pop("parts", glbfab.default_parts()), Screw=((-69, -67, 3, 5, -6, -4), "frame", "frame"))
    return dict(parts=parts, compressed=True, target="Screw", **kw)


@pytest.mark.parametrize("kw, row, text", [
    (with_screw(offset_mm=(0, 10, 0)), "origin", "bridge underside is at y +10.00"),          # r9-like
    (with_screw(scale=1.0), "units", "millimetre-scaled"),
    (with_screw(parts=without("Temple_L", "Temple_R")), "orientation", "temple left missing"),
    (with_screw(pre_mm=trans(0, 4, 0) @ np.diag([-1, -1, 1, 1])), "orientation", "not +Y up"),
    (with_screw(offset_mm=(3, 0, 0)), "origin", "the lenses are centred at x +3.00 mm"),
])
def test_a_small_compressed_part_cannot_hide_failures_it_could_not_cure(tmp_path, kw, row, text):
    """The compressed part's POSITION bounds show it cannot reach what each check measures: the failure stands."""
    r = check(tmp_path, **kw)
    assert row in failed(r), r["errors"]
    assert any(text in e for e in r["errors"]), r["errors"]


def test_compressed_parts_downgrade_only_what_they_could_change(tmp_path):
    r = check(tmp_path, compressed=True, target="Temple_L")       # the left temple could reach -60 mm
    assert r["ok"], r["errors"]
    assert any("orientation unverified: temple left missing" in w for w in r["warnings"]), r["warnings"]
    r = check(tmp_path, "lens.glb", compressed=True, target="Lens_R")    # a one-sided lens box joins its half exactly
    assert r["ok"], r["errors"]
    assert r["measurements"]["lens_halves_dy_mm"] == pytest.approx(0.0, abs=1e-6)
    assert r["measurements"]["lens_centre_x_mm"] == pytest.approx(0.0, abs=1e-3)
    r = check(tmp_path, "lens_rolled.glb", compressed=True, target="Lens_R", pre_mm=rot("z", 25))
    assert "orientation" in failed(r) and any("rolled" in e for e in r["errors"]), r["errors"]
    r = check(tmp_path, "low.glb", compressed=True, offset_mm=(0, 10, 0))    # the compressed bridge could be lower
    assert "origin" not in failed(r), r["errors"]
    assert any("origin unverified" in w for w in r["warnings"]), r["warnings"]


def test_compressed_part_without_bounds_leaves_the_measured_rows_unverified(tmp_path):
    def drop_bounds(doc, add):
        mesh = next(m for m in doc["meshes"] if m["name"] == "Screw")
        acc = doc["accessors"][mesh["primitives"][0]["attributes"]["POSITION"]]
        acc.pop("min")
        acc.pop("max")
    r = check(tmp_path, patch=drop_bounds, **with_screw(offset_mm=(0, 10, 0)))
    assert r["ok"], r["errors"]
    assert any("have no POSITION min/max" in w for w in r["warnings"])
    assert any("origin unverified" in w and "+10.00" in w for w in r["warnings"]), r["warnings"]


def test_texture_limits_are_warnings_not_errors(tmp_path):
    r = check(tmp_path, texture_px=(4100, 8))
    assert r["ok"], r["errors"]
    assert any("4100x8" in w for w in r["warnings"])
    assert any("4100x8" in x for x in r["live_site_would_fail"])
    assert any("cannot map" in w for w in r["warnings"])         # glbfab boxes carry no TEXCOORD_0


def test_transmissive_frame_is_only_a_live_site_warning(tmp_path):
    mats = {"frame": {"name": "Crystal acetate", "extensions": {"KHR_materials_transmission": {"transmissionFactor": 0.8}}},
            "lens": {"name": "Lens tint"}}
    r = check(tmp_path, materials=mats)
    assert r["ok"], r["errors"]
    assert any("not fully opaque" in x for x in r["live_site_would_fail"])


def test_material_shared_by_lens_and_frame_fails_components(tmp_path):
    parts = glbfab.default_parts()
    parts["Bridge"] = (parts["Bridge"][0], "lens", "frame")       # frame part painted with the lens material
    r = check(tmp_path, parts=parts)
    assert failed(r) == {"components"}
    assert any("both lens and non-lens" in e for e in r["errors"])


def test_volume_thickness_in_millimetres_is_warned(tmp_path):
    def mm_volume(doc, add):
        doc["materials"][1]["extensions"]["KHR_materials_volume"] = {"thicknessFactor": 1.5}
    r = check(tmp_path, patch=mm_volume)
    assert r["ok"]
    assert any("looks like millimetres" in w for w in r["warnings"]), r["warnings"]
    ok = check(tmp_path, "m.glb", patch=lambda d, a: d["materials"][1]["extensions"].update(
        KHR_materials_volume={"thicknessFactor": 0.00135}))
    assert not any("millimetres" in w for w in ok["warnings"])


def test_shared_mesh_and_unknown_required_extension_are_warnings(tmp_path):
    def reuse(doc, add):
        doc["nodes"].append({"name": "Bridge_copy", "mesh": 0})
        doc["scenes"][0]["nodes"].append(len(doc["nodes"]) - 1)
        doc["extensionsUsed"].append("VENDOR_magic")
        doc["extensionsRequired"] = ["VENDOR_magic"]
    r = check(tmp_path, patch=reuse)
    assert r["ok"], r["errors"]
    assert any("more than one node" in w for w in r["warnings"])
    assert any("VENDOR_magic" in w for w in r["warnings"])


def test_every_section_is_guarded(tmp_path, monkeypatch):
    def boom(*a):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(S, "_orientation", boom)
    r = check(tmp_path)
    assert failed(r) == {"orientation"} and any("synthetic" in e for e in r["errors"])


REAL = {
    "q001": JOBS / "3b8a1c7ae8114868b49b298b681f1b29/original/model.glb",
    "prada": JOBS / "8c82d986e6c14d26b4b38f3807712ea0/agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb",
    "invu": JOBS / "c8e81c7214a646cfa07e082c59128f10/agent/ar/final-invu-ia22403b-dd80df73/model.glb",
}
R9 = REPO / "ar_v4/model_studio/data/jobs/d92b404c-f2c7-483e-ae9c-aa8bcf1b870c/agent/operations/8c315e48f6b84cbc897cf397d772d6f0/candidate.glb"


@pytest.mark.parametrize("name", sorted(REAL))
def test_studio_deliveries_pass(name):
    if not REAL[name].exists():
        pytest.skip("local Studio data not present")
    r = validate_spec(REAL[name])
    assert r["ok"], r["errors"]
    m = r["measurements"]
    assert 4.0 - 0.1 <= m["temple_roots_above_lens_centre_mm"] <= 18.0      # the calibrated range
    assert all(-10.0 <= s <= 6.0 for s in m["temple_slope_deg"].values())
    assert abs(m["lens_halves_dy_mm"]) <= 0.2


def test_ar_v4_crystal_r9_fails_only_the_origin_row():
    if not R9.exists():
        pytest.skip("local ar_v4 data not present")
    r = validate_spec(R9)
    assert failed(r) == {"origin"}
    assert r["measurements"]["bridge_underside_mm"]["y"] == pytest.approx(10.23, abs=0.02)
