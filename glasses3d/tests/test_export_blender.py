"""WP3 $0 proof: Blender scene -> GLB round trips (headless Blender 5.2; skipped when Blender is absent).

All fixture variants are built in one Blender run and exported in parallel once per module; the tests read the
receipts and GLBs."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import blender, glb
from glasses3d.g3d_host.spec import validate_spec

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "blender" / "build_export_fixture.py"
DUMP_NORMALS = HERE / "blender" / "dump_corner_normals.py"
SNAPSHOT_PROBE = HERE / "blender" / "snapshot_probe.py"
REPO = HERE.parents[1]
JOBS = REPO / "automation/data/blender_agent/studio/jobs"

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")

REFUSED = {
    "no_role": "needs partRole",
    "no_origin": "mdl_bridge_underside",
    "cm_units": "numeric millimetres",
    "wrong_axes": "scene axes are '+Z up; -Y front'",
    "hidden_viewport": "disabled in viewports",
    "hidden_viewport_coll": "collection ['Rivets'] disabled in viewports",
    "viewport_modifier": "modifier 'Decimate' is off for render but on in viewports",
    "subsurf_levels": "viewport levels 0 but render levels 2",
    "simplify": "Scene Simplify is on with Max Subdivision 0 in viewports but 6 for render",
    "layers_disagree": "disagree on what renders: Old_brow",
    "empty_mesh": "objects that carry partRole render no surface (no triangles): Empty_mesh",
    "masked_lens": "objects that carry partRole render no surface (no triangles): Lens_R",
    "missing_image": "images that have no data",
    "role_conflict": "object partRole 'frame' disagrees with data partRole 'lens'",
    "gn_instances": "unrealized instances cannot be exported: Hinge_cap",
    "curve_instances": "unrealized instances cannot be exported: Rim_rivets",
}
EXPORTED = ("ok", "shared_lens", "offset_origin", "normals", "excluded_coll", "unused_layer", "relative_image",
            "guide_curve", "data_role", "curve_smooth", "nan_hidden", "no_uvs", "volume_mm", "volume_dropped")
STUDIO_SCENES = {
    "q001": JOBS / "3b8a1c7ae8114868b49b298b681f1b29/original/scene.blend",
    "prada": JOBS / "8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend",
}
NORMAL_OBJECTS = ("Temple_L", "N_smooth_sharp", "N_weighted", "N_custom_plain", "N_custom_mirror", "N_custom_squash")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("export")


@pytest.fixture(scope="module")
def blends(workdir):
    variants = list(REFUSED) + list(EXPORTED)
    proc = blender.run(None, FIXTURE, [workdir] + variants, timeout_s=300)
    out = {v: (workdir / "relimg" / f"fixture-{v}.blend" if v == "relative_image" else workdir / f"fixture-{v}.blend")
           for v in variants}
    missing = [v for v, p in out.items() if not p.exists()]
    assert not missing, (missing, proc.stdout[-1500:] + proc.stderr[-1500:])
    return out


@pytest.fixture(scope="module")
def exports(workdir, blends):
    """{name: (receipt, glb path, sha256 of the source .blend before, after)} for every variant and Studio scene."""
    glbs = workdir / "glbs"
    glbs.mkdir()
    (glbs / "stale.glb").write_bytes(b"an earlier export")
    jobs = {v: (p, glbs / f"{v}.glb") for v, p in blends.items()}
    jobs["stale"] = (blends["no_role"], glbs / "stale.glb")
    jobs.update({k: (p, glbs / f"{k}.glb") for k, p in STUDIO_SCENES.items() if p.exists()})

    def one(item):
        name, (src, out) = item
        before = sha(src)
        receipt = blender.export_copy(src, out)
        return name, (receipt, out, before, sha(src))

    with ThreadPoolExecutor(6) as pool:
        return dict(pool.map(one, jobs.items()))


@pytest.fixture(scope="module")
def ok(exports):
    receipt, out, _, _ = exports["ok"]
    assert receipt["status"] == "exported", receipt
    return receipt, out, glb.load(out)


def prim(g, name):
    return next(p for p in g.primitives if p.node_name == name)


def material(g, name):
    return next(m for m in g.materials if m.get("name") == name)


def test_receipt_and_spec(ok):
    receipt, out, g = ok
    assert receipt["scene_unchanged"] is True
    assert receipt["split_materials"] == [] and receipt["excluded"] == []
    r = validate_spec(out)
    assert r["ok"], r["errors"]
    assert r["warnings"] == [], r["warnings"]
    # the 12-sided temples (centre |x| 68, radius 2.2, first vertex at 15 deg) reach |x| = 68 + 2.2 cos 15 deg
    assert r["measurements"]["width_mm"] == pytest.approx(2 * (68 + 2.2 * np.cos(np.radians(15))), abs=0.01)


def test_original_blend_is_never_written(exports):
    for name, (_, _, before, after) in exports.items():
        assert before == after, name
    assert not list(exports["ok"][1].parent.glob(".*g3d-tmp*")), "temporary GLBs left behind"


def test_authored_names_are_restored(ok):
    _, _, g = ok
    assert {p.node_name for p in g.primitives} == {"Bridge", "Brow_L", "Brow_R", "Lens_L", "Lens_R", "Temple_L",
                                                   "Temple_R", "Hinge_cap"}
    assert {m["name"] for m in g.materials} == {"Acetate | glossy black", "Havana | photo print", "Lens | clear",
                                                "Hinge metal"}


def test_part_role_written_to_node_mesh_and_material(ok):
    _, _, g = ok
    for p in g.primitives:
        role = g.nodes[p.node_index]["extras"]["partRole"]
        assert g.meshes[p.mesh_index]["extras"]["partRole"] == role
        assert g.materials[p.material_index]["extras"]["partRole"] == role
    assert g.nodes[prim(g, "Lens_L").node_index]["extras"]["partRole"] == "lens"


def _unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _facet_normals(p):
    t = p.positions[p.triangles]
    return _unit(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]))


def test_faceted_temple_keeps_twelve_flat_facets(ok):
    """Facets are 30 deg apart, so any 30-40 deg angle smoothing would merge them; read the exported NORMALs."""
    _, _, g = ok
    p = prim(g, "Temple_L")
    assert p.normals is not None
    fn = _facet_normals(p)
    vn = _unit(p.normals[p.triangles])                                    # (M, 3, 3) per-corner normals
    ang = np.degrees(np.arccos(np.clip(np.einsum("mk,mjk->mj", fn, vn), -1, 1)))
    assert ang.max() < 0.5, f"a corner normal deviates {ang.max():.2f} deg from its facet"
    side = np.abs(fn[:, 2]) < 0.5
    reps: list = []                                                       # distinct directions, 1 deg apart
    for n in vn[side].reshape(-1, 3):
        if not reps or np.degrees(np.arccos(np.clip(np.asarray(reps) @ n, -1, 1))).min() > 1.0:
            reps.append(n)
    assert len(reps) == 12                                                # twelve distinct side-facet NORMAL directions


def test_smooth_part_stays_smooth(ok):
    _, _, g = ok
    p = prim(g, "Hinge_cap")
    vn = _unit(p.normals[p.triangles])
    ang = np.degrees(np.arccos(np.clip(np.einsum("mk,mjk->mj", _facet_normals(p), vn), -1, 1)))
    assert ang.max() > 5.0                                                # interpolated normals, not flat


def test_clearcoat_transmission_uvs_and_image_texture_survive(ok):
    _, out, g = ok
    coat = material(g, "Acetate | glossy black")["extensions"]["KHR_materials_clearcoat"]
    assert coat["clearcoatFactor"] == pytest.approx(0.8, abs=1e-3)
    assert coat["clearcoatRoughnessFactor"] == pytest.approx(0.05, abs=1e-3)
    tr = material(g, "Lens | clear")["extensions"]["KHR_materials_transmission"]
    assert tr["transmissionFactor"] == pytest.approx(1.0)
    hv = material(g, "Havana | photo print")
    info = hv["pbrMetallicRoughness"]["baseColorTexture"]
    assert info.get("texCoord", 0) == 0
    tex = g.doc["textures"][info["index"]]
    img = g.doc["images"][tex["source"]]
    assert "bufferView" in img and not g.external_uris
    assert glb.image_size(g.image_bytes(tex["source"])) == (64, 48)
    for name in ("Brow_L", "Brow_R"):
        p = prim(g, name)
        assert "TEXCOORD_0" in p.attributes and p.uv0 is not None
        assert np.ptp(p.uv0, axis=0).min() > 0.1                          # real UVs, not a constant


def test_relative_external_texture_survives(exports):
    receipt, out, _, _ = exports["relative_image"]
    assert receipt["status"] == "exported", receipt
    g = glb.load(out)
    hv = material(g, "Havana | photo print")
    tex = g.doc["textures"][hv["pbrMetallicRoughness"]["baseColorTexture"]["index"]]
    assert glb.image_size(g.image_bytes(tex["source"])) == (64, 48)
    assert validate_spec(out)["ok"]


def test_corner_normals_match_blender_including_mirrored_and_scaled(workdir, blends, exports):
    """Smooth faces with authored sharp edges, Bevel + WEIGHTED_NORMAL, and custom normals on plain, mirrored
    (scale -1) and non-uniformly scaled objects: every exported corner normal matches Blender's evaluated corner
    normal (asset space) within 0.5 deg, and every Blender corner is present in the GLB."""
    receipt, out, _, _ = exports["normals"]
    assert receipt["status"] == "exported", receipt
    assert receipt["normals_reset"] == ["N_custom_mirror", "N_custom_squash"]
    truth_path = workdir / "normals-truth.json"
    proc = blender.run(blends["normals"], DUMP_NORMALS, [truth_path] + list(NORMAL_OBJECTS), timeout_s=120)
    assert truth_path.exists(), proc.stdout[-1500:] + proc.stderr[-1500:]
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    g = glb.load(out)
    for name in NORMAL_OBJECTS:
        T = np.asarray(truth[name])
        p = prim(g, name)
        gn = _unit(p.normals)
        same = np.linalg.norm(T[:, None, :3] - p.positions[None], axis=2) < 1e-6
        ang = np.degrees(np.arccos(np.clip(np.einsum("ck,vk->cv", T[:, 3:], gn), -1, 1)))
        ang = np.where(same, ang, np.inf)
        assert ang.min(axis=1).max() < 0.5, (name, "a Blender corner normal is missing from the GLB",
                                             float(ang.min(axis=1).max()))
        assert ang.min(axis=0).max() < 0.5, (name, "a GLB normal matches no Blender corner",
                                             float(ang.min(axis=0).max()))
    assert validate_spec(out)["ok"]


def test_bridge_underside_origin_is_applied(exports):
    """Geometry and mdl_bridge_underside both moved by (0, 5, 3) mm: the GLB must be identical in place."""
    receipt, out, _, _ = exports["offset_origin"]
    assert receipt["status"] == "exported" and receipt["origin_mm"] == [0.0, 5.0, 3.0], receipt
    r = validate_spec(out)
    assert r["ok"], r["errors"]
    assert r["measurements"]["bridge_underside_mm"]["y"] == pytest.approx(0.0, abs=0.01)
    ref = validate_spec(exports["ok"][1])["measurements"]["bbox_mm"]
    np.testing.assert_allclose(r["measurements"]["bbox_mm"]["min"], ref["min"], atol=0.01)
    np.testing.assert_allclose(r["measurements"]["bbox_mm"]["max"], ref["max"], atol=0.01)


@pytest.mark.parametrize("variant", sorted(REFUSED))
def test_export_refuses_with_a_fixable_message(exports, variant):
    receipt, out, _, _ = exports[variant]
    assert receipt["status"] == "failed"
    assert REFUSED[variant] in receipt["error"], receipt["error"]
    assert not out.exists()


def test_failed_export_leaves_an_earlier_file_alone_and_says_so(exports):
    receipt, out, _, _ = exports["stale"]
    assert receipt["status"] == "failed" and receipt["output_written"] is False
    assert out.read_bytes() == b"an earlier export"
    assert receipt["stale_output_sha256"] == hashlib.sha256(b"an earlier export").hexdigest()


def test_view_layer_excluded_collection_is_not_exported(exports):
    receipt, out, _, _ = exports["excluded_coll"]
    assert receipt["status"] == "exported", receipt
    assert {"source": "Meshy scan (reference)", "reason": "view_layer.exclude"} in receipt["excluded"]
    assert "Meshy scan (reference)" not in {p.node_name for p in glb.load(out).primitives}
    assert validate_spec(out)["ok"]


def test_the_render_view_layer_decides_not_the_active_one(exports):
    receipt, out, _, _ = exports["unused_layer"]
    assert receipt["status"] == "exported", receipt
    assert receipt["view_layer"] == "Final"
    assert any("'Work' is not used for rendering" in n for n in receipt["notes"]), receipt["notes"]
    assert {"source": "Old_brow", "reason": "view_layer.exclude"} in receipt["excluded"]
    names = {p.node_name for p in glb.load(out).primitives}
    assert "Old_brow" not in names
    assert "Screw_R" in names                                     # evaluated by Final's depsgraph, not the active one
    assert validate_spec(out)["ok"]
    assert exports["ok"][0]["view_layer"] == "ViewLayer"


def test_role_less_helpers_that_render_no_surface_are_skipped(exports):
    """A guide curve without bevel or partRole is skipped; a role-tagged part that renders nothing is refused
    (empty_mesh, masked_lens in REFUSED)."""
    receipt, out, _, _ = exports["guide_curve"]
    assert receipt["status"] == "exported", receipt
    assert {"source": "Temple_path", "reason": "renders no surface"} in receipt["excluded"]
    assert validate_spec(out)["ok"]


def test_part_role_on_mesh_data_is_accepted(exports):
    receipt, out, _, _ = exports["data_role"]
    assert receipt["status"] == "exported", receipt
    assert any("object data" in n and "Hinge_cap" in n for n in receipt["notes"])
    g = glb.load(out)
    assert g.nodes[prim(g, "Hinge_cap").node_index]["extras"]["partRole"] == "frame"


def test_curve_with_smooth_by_angle_is_not_an_instance(exports):
    receipt, out, _, _ = exports["curve_smooth"]
    assert receipt["status"] == "exported", receipt
    assert "Rim_wire" in {o["source"] for o in receipt["objects"]}
    assert validate_spec(out)["ok"]


def test_nan_on_a_hidden_helper_does_not_break_the_invariance_check(exports):
    receipt, _, _, _ = exports["nan_hidden"]
    assert receipt["status"] == "exported" and receipt["scene_unchanged"] is True, receipt
    assert {"source": "Broken_helper", "reason": "hide_render"} in receipt["excluded"]


@pytest.mark.parametrize("variant, text", [
    ("no_uvs", "sample an image texture but the mesh has no UV map"),
    ("volume_mm", "thicknessFactor 2 looks like millimetres"),
    ("volume_dropped", "wrote no KHR_materials_volume"),
])
def test_silent_losses_are_noted_in_the_receipt(exports, variant, text):
    receipt, out, _, _ = exports[variant]
    assert receipt["status"] == "exported", receipt
    assert any(text in n for n in receipt["notes"]), receipt["notes"]
    if variant == "volume_mm":
        assert any("looks like millimetres" in w for w in validate_spec(out)["warnings"])


def test_shared_lens_material_is_split_and_reported(exports):
    receipt, out, _, _ = exports["shared_lens"]
    assert receipt["status"] == "exported", receipt
    assert receipt["split_materials"] == ["Lens | clear"]
    r = validate_spec(out)
    assert r["ok"], r["errors"]
    bridge = next(p for p in r["primitives"] if p["node"] == "Bridge")
    assert bridge["class"] == "frame" and bridge["signal"] == "material.extras.partRole=frame"


def test_snapshot_sees_what_the_export_must_not_change(workdir, blends):
    out = workdir / "snapshot-probe.json"
    proc = blender.run(blends["ok"], SNAPSHOT_PROBE, [out], timeout_s=180)
    assert out.exists(), proc.stdout[-1500:] + proc.stderr[-1500:]
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert seen.pop("nothing") is False and seen.pop("nan stays nan (must be False)") is False
    assert all(seen.values()), [k for k, v in seen.items() if not v]


@pytest.mark.parametrize("name", sorted(STUDIO_SCENES))
def test_studio_scene_exports_and_passes_the_spec(exports, name):
    if name not in exports:
        pytest.skip("local Studio data not present")
    receipt, out, before, after = exports[name]
    assert receipt["status"] == "exported", receipt
    assert receipt["scene_unchanged"] is True and before == after
    r = validate_spec(out)
    assert r["ok"], r["errors"]
    assert not any(m["name"].endswith(".001") for m in r["materials"] if m["name"])


def test_export_and_validate_returns_both_reports(workdir, blends):
    res = blender.export_and_validate(blends["ok"], workdir / "ev.glb")
    assert res["export"]["status"] == "exported" and res["spec"]["ok"], res
    res = blender.export_and_validate(blends["no_role"], workdir / "ev-bad.glb")
    assert res["export"]["status"] == "failed" and res["spec"] is None
