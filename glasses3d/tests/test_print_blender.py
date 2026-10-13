"""WP5 step 4, the Blender side (WP5_DESIGN §4, §8 A6 / B5): g3d.print_uv, g3d.attach_print, g3d.detach_print,
g3d.remove_print_uv, the exporter's g3d_print_uv allowlist and the CLI commands, graded by the independent instrument
(tests/instruments/print_metrics.py reads the GLBs with WP3's reader). Headless Blender 5.2; skipped without it.

- a synthetic scene (tests/blender/print_build.py: mirrored smooth bent-tube brows with a UV layer, mirrored flat
  12-gon temples without one, sharing their material with the unprinted Bridge) at 8 tx/mm with a synthetic print;
- the q001 TOOL TEST (tests/photoprint/print_tooltest.py) when the Studio scene and the step-4 bake are present.
"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d import printuv as PU
from glasses3d.g3d_host import blender, glb
from glasses3d.g3d_host.photoprint import manifest as MF
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.tests.instruments import print_metrics as PM

HERE = Path(__file__).resolve().parent
G3D = HERE.parent
BUILD = HERE / "blender" / "print_build.py"
ROUNDTRIP = HERE / "blender" / "print_roundtrip.py"
CLI = G3D / "g3d" / "cli.py"
PARTS = ["Brow_L", "Brow_R", "Temple_L", "Temple_R"]
TX = 8.0

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    d = tmp_path_factory.mktemp("print_blender")
    scene = d / "print.blend"
    proc = blender.run(None, BUILD, [scene], timeout_s=300)
    assert scene.is_file(), proc.stdout[-1500:] + proc.stderr[-1500:]
    # the CLI's print-uv dry run says what the layout will be (atlas, identities) without writing
    (d / "dry.json").write_text(json.dumps({"parts": PARTS, "density": TX, "dry_run": True}), encoding="utf-8")
    blender.run(scene, CLI, ["print-uv", d / "dry.json", d / "dry_receipt.json"], timeout_s=300)
    dry = json.loads((d / "dry_receipt.json").read_text(encoding="utf-8"))
    r = dry["results"][0]
    assert dry["status"] == "ok" and r["status"] == "ok", dry
    # a synthetic print of that atlas and its manifest (what a host bake writes)
    w, h = r["atlas"]
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.stack([(xx * 255 // max(w - 1, 1)), (yy * 255 // max(h - 1, 1)), ((xx // 16 + yy // 16) % 2) * 255],
                   -1).astype(np.uint8)
    png = d / "print.png"
    Image.fromarray(img).save(png)
    man = {"version": 1, "print_png": str(png), "print_sha256": MF._sha(png), "width": w, "height": h,
           "tx_per_mm": r["tx_per_mm"], "tx_requested": TX, "parts": PARTS, "layer": "g3d_print",
           "texture_parts": PARTS, "no_texture_parts": [],
           "geometry_sha": r["geometry_sha"], "charts_sha": r["charts_sha"], "label": "synthetic test print"}
    (d / MF.MANIFEST).write_text(json.dumps(man), encoding="utf-8")
    spec = {"parts": PARTS, "print_uv": {"density": TX}, "attach": {"report": str(d / MF.MANIFEST)},
            "painted_glb": "painted.glb", "label": "synthetic", "probes": True}
    (d / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    out = d / "rt"
    proc = blender.run(scene, ROUNDTRIP, [out, d / "spec.json"], timeout_s=600)
    rt = out / "roundtrip.json"
    assert rt.is_file(), proc.stdout[-2000:] + proc.stderr[-2000:]
    res = json.loads(rt.read_text(encoding="utf-8"))
    assert not res.get("error"), res.get("traceback")
    return {"dir": d, "scene": scene, "dry": r, "manifest": man, "rt": res,
            "glb": {k: Path(v["glb"]) for k, v in res["stages"].items()}}


def test_every_stage_ran_and_exported(synth):
    st = synth["rt"]["stages"]
    assert list(st) == ["original", "printuv", "attached", "detached", "removed"]
    for name, s in st.items():
        assert s["export"]["status"] == "exported" and s["export"]["scene_unchanged"], (name, s["export"])
        if s["receipt"] is not None:
            assert s["receipt"]["status"] == "ok", (name, s["receipt"])


def test_print_uv_receipt(synth):
    r = synth["rt"]["stages"]["printuv"]["receipt"]
    assert r["geometry_sha"] == synth["dry"]["geometry_sha"] and r["charts_sha"] == synth["dry"]["charts_sha"]
    assert r["a1"]["pass"] and r["partners"] == {"Brow_R": "Brow_L", "Temple_R": "Temple_L"}
    o = r["objects"]
    # brows: a UV layer already, so the print layer is index 1 and the render UV stays 'UVMap'
    for b in ("Brow_L", "Brow_R"):
        assert o[b]["texcoord"] == 1 and o[b]["uv_layers"] == ["UVMap", "g3d_print"] and o[b]["active_render"] == "UVMap"
        assert o[b]["polygons_split"] == 2 and o[b]["normal_change_max_deg"] <= 0.1         # smooth bent quads
    # temples: no UV layer before, so the print layer is the only one (said in the warnings); planar facets: no split
    for t in ("Temple_L", "Temple_R"):
        assert o[t]["texcoord"] == 0 and o[t]["polygons_to_split"] == 0 and "polygons_split" not in o[t]
    assert sum("W_ONLY_UV" in w for w in r["warnings"]) == 2
    for name, p in r["postcheck"].items():
        assert p["positions_identical_in_order"] and p["uv_max_err_texel"] < 1e-3 and p["normal_max_change_deg"] < 0.1
    d = synth["rt"]["stages"]["printuv"]["digests"]
    assert d["active_render_uv"]["Brow_L"] == "UVMap" and d["active_uv"]["Brow_L"] == "UVMap"
    info = json.loads(d["print_uv_prop"]["Brow_L"])
    assert info["geometry_sha"] == r["geometry_sha"] and info["layer"] == "g3d_print" and info["texcoord"] == 1


def test_exporter_allowlist_and_texcoord(synth):
    ex = synth["rt"]["stages"]["printuv"]["export"]
    assert ex["print_uv_extras"] == {"Brow_L": 1, "Brow_R": 1, "Temple_L": 0, "Temple_R": 0}
    g = glb.load(synth["glb"]["printuv"])
    for n in g.nodes:
        extras = n.get("extras") or {}
        if n.get("name") in PARTS:
            assert set(extras) == {"partRole", "g3d_print_uv"}, extras
            assert json.loads(extras["g3d_print_uv"])["texcoord"] == ex["print_uv_extras"][n["name"]]
        else:
            assert "g3d_print_uv" not in extras
    for m in g.meshes:
        assert set(m.get("extras") or {}) <= {"partRole"}
    assert "print_uv_extras" not in synth["rt"]["stages"]["original"]["export"]


def test_print_uv_changes_only_what_it_may(synth):
    r = PM.print_uv_invariance(synth["glb"]["original"], synth["glb"]["printuv"], PARTS)
    assert r["pass"], r["failures"]
    assert r["parts"]["Brow_L"]["normal_max_change_deg"] <= PM.NORMAL_TOL_DEG


def test_uvs_are_the_layout_of_the_export(synth):
    """The renderer's TEXCOORD_k equals the core's layout of the pre-print export (what a host bake lays out)."""
    prims = PU.glbmesh.read_glb(synth["glb"]["original"])
    V, F, part, names = PU.glbmesh.mesh_arrays(prims)
    lay = PU.layout(V, F, part, names, PARTS, TX)
    assert PU.glbmesh.charts_sha(lay.charts, lay.width, lay.height) == synth["dry"]["charts_sha"]
    assert PU.glbmesh.geometry_sha(V, F, part, names, PARTS) == synth["dry"]["geometry_sha"]
    frow = lay.face_index(len(F))
    want = {p: lay.uv[frow[PU.glbmesh.part_faces(part, names, p)]] for p in PARTS}
    r = PM.uv_identity(synth["glb"]["attached"], want, lay.width, lay.height)
    assert r["pass"], r


def test_a6_round_trip(synth):
    r = PM.a6_round_trip(synth["glb"]["original"], synth["glb"]["attached"], PARTS,
                         print_png=synth["manifest"]["print_png"], allow_rename=True)
    assert r["pass"], r["failures"]
    assert {p: r["parts"][p]["base_color_texcoord"] for p in PARTS} == {"Brow_L": 1, "Brow_R": 1, "Temple_L": 0,
                                                                         "Temple_R": 0}
    assert all(r["parts"][p]["print_pixels_identical"] for p in PARTS)
    # the temples' material was shared with the unprinted Bridge: they got a single-user copy (only its name differs)
    assert r["renamed"] == {"Temple_L": ["Acetate temple", "Acetate temple | g3d print"],
                            "Temple_R": ["Acetate temple", "Acetate temple | g3d print"]}
    rep = validate_spec(synth["glb"]["attached"])
    assert rep["ok"], rep["errors"]


def test_b5_material_control(synth):
    r = PM.b5_material_control(synth["glb"]["printuv"], synth["glb"]["attached"], PARTS, allow_rename=True)
    assert r["pass"], r["failures"]
    assert len(r["images_added"]) == 1 and r["images_added"][0] == synth["manifest"]["print_sha256"]
    assert set(r["base_colour_changes"]) == set(PARTS)
    a = synth["rt"]["stages"]["attached"]["receipt"]
    assert a["postcheck"]["pass"]
    assert sum("W_SINGLE_USER" in w for w in a["warnings"]) == 2
    assert set(a["materials"]) == {"Havana | photo print", "Acetate temple | g3d print"}   # brows stay shared


def test_attach_leaves_every_other_bsdf_input_alone(synth):
    """B5 at the source: the Bridge (same original material as the temples) exports exactly as before, and every
    non-base-colour key of the printed materials is unchanged (b5 above); the Bridge's material keeps its name."""
    A, B = PM.glb_view(synth["glb"]["printuv"]), PM.glb_view(synth["glb"]["attached"])
    assert PM._canon(A["Bridge"][0]["material"]) == PM._canon(B["Bridge"][0]["material"])
    assert B["Bridge"][0]["material_name"] == "Acetate temple"


def test_host_staleness_check(synth):
    ok = MF.check_glb(synth["glb"]["attached"], synth["manifest"])
    assert ok["ok"] and not ok["stale"], ok["reasons"]
    # fix round 1: the sampled image is the manifest's print; a synthetic manifest names no bake GLB, so the uv
    # comparison with a layout is skipped and said
    assert all(ok["parts"][p]["print_image"] in ("bytes", "pixels") for p in PARTS)
    assert not any(ok["parts"][p]["uv_checked"] for p in PARTS) and any("synthetic" in n for n in ok["notes"])
    other_img = dict(synth["manifest"], print_sha256="0" * 64, print_png=str(synth["dir"] / "missing.png"))
    bad = MF.check_glb(synth["glb"]["attached"], other_img)
    assert not bad["ok"] and sum("not the bake's print.png" in r for r in bad["reasons"]) == len(PARTS)
    before = MF.check_glb(synth["glb"]["original"], synth["manifest"])
    assert not before["ok"] and any("no g3d_print_uv" in r for r in before["reasons"])
    other = dict(synth["manifest"], geometry_sha="0" * 64)
    st = MF.check_glb(synth["glb"]["attached"], other)
    assert st["stale"] and any("print stale; re-project" in r for r in st["reasons"])


def test_detach_and_remove_give_the_earlier_glbs_back(synth):
    st = synth["rt"]["stages"]
    assert PM.b5_material_control(synth["glb"]["printuv"], synth["glb"]["detached"], [])["pass"]
    assert PM.b5_material_control(synth["glb"]["original"], synth["glb"]["removed"], [])["pass"]
    assert st["printuv"]["digests"]["materials"] == st["detached"]["digests"]["materials"]
    assert st["original"]["digests"]["meshes"] == st["removed"]["digests"]["meshes"]
    assert all(v["tree_restored"] for v in st["detached"]["receipt"]["materials"].values())
    assert st["removed"]["digests"]["polygons"] == st["original"]["digests"]["polygons"]
    assert st["removed"]["digests"]["print_uv_prop"] == {p: None for p in PARTS}


def test_refusals_and_probes(synth):
    p = synth["rt"]["probes"]
    assert p["dry_run"] == {"status": "ok", "unchanged": True,
                            "polygons_to_split": {"Brow_L": 2, "Brow_R": 2, "Temple_L": 0, "Temple_R": 0}}
    assert p["again"]["first"] == "ok" and p["again"]["second"] == "E_LAYER_EXISTS" and p["again"]["replace"] == "ok"
    assert p["again"]["same_charts"] and p["again"]["same_geometry"]
    assert p["stale_geometry"]["code"] == "E_STALE" and "re-project" in p["stale_geometry"]["message"]
    assert p["remove_after_edit"]["rows"]["Brow_L"]["restored"] == "layer removed only"
    assert any("W_GEOMETRY_EDITED" in w for w in p["remove_after_edit"]["warnings"])
    assert p["lens"] == "E_LENS" and p["mirror"] == "E_MIRROR" and p["shared_mesh"] == "E_SHARED_MESH"
    assert p["unknown"] == "E_ARGS" and p["topology_modifier"] == "E_TOPOLOGY" and p["transmission_map"] == "E_ARGS"


def test_cli_run_stops_at_a_refused_print_step(synth, tmp_path):
    steps = [{"op": "print_uv", "parts": ["Lens_L"], "density": TX}, {"op": "print_uv", "parts": PARTS, "density": TX}]
    (tmp_path / "steps.json").write_text(json.dumps(steps), encoding="utf-8")
    out = tmp_path / "x.glb"
    blender.run(synth["scene"], CLI, ["run", tmp_path / "steps.json", tmp_path / "r.json", "--export", out],
                timeout_s=300)
    r = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert r["status"] == "refused" and r["stopped_at"] == "print_uv" and len(r["results"]) == 1
    assert r["results"][0]["code"] == "E_LENS" and not out.exists()


def test_attachment_eligibility_and_safe_repeat(synth):
    p = synth["rt"]["probes"]["eligibility"]
    for key in ("missing", "excluded", "unknown", "empty", "overlap"):
        assert p[key]["status"] == "refused" and p[key]["unchanged"], (key, p[key])
    assert p["excluded"]["code"] == p["unknown"]["code"] == p["empty"]["code"] == "E_NO_TEXTURE"
    assert p["eligible"]["status"] == "ok" and p["eligible"]["excluded_unchanged"]
    assert p["eligible"]["parts"] == ["Brow_L", "Temple_L"]
    for key in ("shared_detach", "edited_detach", "missing_original", "reuv_attached"):
        assert p[key]["status"] == "refused" and p[key]["unchanged"], (key, p[key])
    assert p["shared_detach"]["code"] == "E_SHARED_PRINT"
    assert p["edited_detach"]["code"] == p["missing_original"]["code"] == "E_STALE"
    assert p["reuv_attached"]["code"] == "E_ATTACHED"
    assert all(p["repeat"].values()), p["repeat_receipts"]
    zero = synth["rt"]["probes"]["zero_area"]
    assert zero["status"] == "refused" and zero["code"] == "E_LAYOUT" and zero["unchanged"], zero
    assert zero["a1"]["zero_area_faces"] == 1 and not zero["a1"]["criteria"]["flips"]


# ------------------------------------------------------------------------------------------------ q001 tool test
TOOLTEST_INPUTS = (G3D.parent / "automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29/original"
                   "/scene.blend", G3D / "data/wp5/ft1119_bake/print.png", G3D / "data/wp3b/ft1119/cameras.json")


@pytest.mark.slow
@pytest.mark.skipif(not all(p.exists() for p in TOOLTEST_INPUTS), reason="q001 scene / step-4 bake not present")
def test_q001_tool_test(tmp_path):
    """TOOL TEST: the step-4 print on a copy of q001: every check of print_tooltest passes, with the numbers this
    step measured (77 / 22 polygons split, normals within 0.05 deg, uvs within 1e-3 texel of the bake's layout).
    Fix round 1 of the correction round: run on a temp COPY of the bake's files (``--bake``), so the record goes into
    the copy's bake.json and the production bake.json is never written (checked by sha256)."""
    import hashlib
    from glasses3d.tests.photoprint import print_tooltest as TT
    from glasses3d.tests.fixtures import legacy_ft1119
    prod = G3D / "data/wp5/ft1119_bake"
    fixture = legacy_ft1119.prepare(tmp_path / "legacy-inputs")
    bake = legacy_ft1119.copy_bake(prod, tmp_path / "bake", fixture)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    before = digest(prod / "bake.json")
    out = tmp_path / "out"
    assert TT.main(["--out", str(out), "--bake", str(bake)]) == 0
    assert digest(prod / "bake.json") == before                  # the production bake is never written
    if (bake / "bake.json").is_file():
        d = json.loads((bake / "bake.json").read_text(encoding="utf-8"))
        if "tooltest" in d:
            assert d["tooltest"]["json"] == str(out / "tooltest.json") and d["tooltest"]["A6"] and d["tooltest"]["B5"]
    rep = json.loads((out / "tooltest.json").read_text(encoding="utf-8"))
    assert rep["pass"], rep["checks"]
    objs = rep["receipts"]["printuv"]["objects"]
    assert {k: v["polygons_split"] for k, v in objs.items()} == {"Brow_Left": 77, "Brow_Right": 77,
                                                                 "Temple_Left": 22, "Temple_Right": 22}
    assert max(r["normal_max_change_deg"] for r in rep["print_uv_invariance"]["parts"].values()) <= 0.05
    assert max(r["max_err_texel"] for r in rep["uv_identity"]["parts"].values()) < 1e-3
    assert rep["geometry_sha"]["match"] and rep["A6"]["renamed"] == {}
