"""WP6 v2 g3d.faceted_temple in headless Blender 5.2 (skipped without Blender or the Prada scene): the tool contract of
g3d/temple_bpy.py (plan/WP6_FACETS.md §2.5, §2.6) on a COPY of the Prada scene (source sha256 unchanged).

- every /2 fixture plans in Blender with the host's core bit for bit, leaving the scene unchanged;
- the roll fixture (core roll, no Blender Bevel) is written closed and 2-manifold with its rolled polygons;
- preferred-v2 (the owner-preferred spec through convert.v1_to_v2, the /2 default snapping) on the copy: dry run and
  refusals (E_RELIEF_STEP, E_TARGET, E_MULTI_MATERIAL) leave the scene unchanged; the written temples are the core's
  polygons with its sharp set and facet custom normals; decals move by chart transfer keeping their standoff (max
  motion reported); the hinge report grades exactly the hinge parts; the export validates; variant meshes are built
  without touching any object and removed again; unfaceted_temple restores the scene byte for byte;
- the §4 loft helper.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple.convert import v1_to_v2
from glasses3d.g3d.temple.sections import polygon_section
from glasses3d.g3d_host import blender
from glasses3d.g3d_host.spec import validate_spec

HERE = Path(__file__).resolve().parent
RUN = HERE / "proofs" / "temple_bpy_run.py"
REPO = HERE.parents[1]
G3D = HERE.parent
PRADA = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
PRADA_SHA = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
PREFERRED = G3D / "data" / "wp6" / "pose_arm_resume_20261010" / "distal_all_views" / "before" / "spec.json"
PREFERRED_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
RIGHT = "Temple right | faceted sweep and ear bend"        # +X (Astra's "right" is the wearer's left)
LEFT = "Temple left | faceted sweep and ear bend"
DECALS = [f"Temple {s} | {n}" for s in ("right", "left")
          for n in ("fine ribbon and shield", "MILANO gold hallmark", "PRADA gold hallmark")]

pytestmark = [pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed"),
              pytest.mark.skipif(not PRADA.exists(), reason="Prada scene not available"), pytest.mark.slow]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def same_core(dump: dict, tb) -> None:
    """The core built in Blender's numpy equals the host's: the same polygons and sharp set, vertices within 1e-9 mm
    (Blender bundles another numpy build; the last bits of a few coordinates may differ)."""
    assert dump["status"] == "ok", dump
    assert dump["polys"] == tb.polys and [tuple(e) for e in dump["sharp"]] == tb.sharp
    assert np.abs(np.array(dump["V"]) - tb.V).max() <= 1e-9


def run_steps(tmp: Path, blend: Path, steps: list, name: str) -> list:
    sp, rp = tmp / f"{name}_steps.json", tmp / f"{name}_receipt.json"
    sp.write_text(json.dumps(steps), encoding="utf-8")
    proc = blender.run(blend, RUN, [sp, rp], timeout_s=900)
    assert rp.exists(), proc.stderr[-3000:] + proc.stdout[-3000:]
    return json.loads(rp.read_text(encoding="utf-8"))["steps"]


def closed_manifold(polys) -> tuple[bool, int]:
    """(every directed edge once and its reverse present, Euler characteristic)."""
    E = {}
    for p in polys:
        for k in range(len(p)):
            e = (p[k], p[(k + 1) % len(p)])
            E[e] = E.get(e, 0) + 1
    ok = all(c == 1 for c in E.values()) and all((b, a) in E for a, b in E)
    verts = {v for p in polys for v in p}
    return ok, len(verts) - len(E) // 2 + len(polys)


@pytest.fixture(scope="module")
def preferred_v2() -> dict:
    if not PREFERRED.exists():
        pytest.skip("owner-preferred spec not present (local evidence)")
    raw = PREFERRED.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PREFERRED_SHA
    return v1_to_v2(json.loads(raw), snap_mm=None)


@pytest.fixture(scope="module")
def prada_copy(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("temple_blender")
    before = sha(PRADA)
    dst = tmp / "prada_copy.blend"
    shutil.copy2(PRADA, dst)
    yield tmp, dst
    assert sha(PRADA) == before == PRADA_SHA                       # the source is never written


@pytest.fixture(scope="module")
def main_run(prada_copy, preferred_v2):
    tmp, blend = prada_copy
    spec = preferred_v2
    variant = FX.prada_default()
    glb = tmp / "faceted.glb"
    steps = [{"op": "scene_digest"},                                                         # 0
             {"op": "mesh_digests"},                                                         # 1
             {"op": "decal_standoffs", "objects": DECALS},                                   # 2
             {"op": "faceted_temple", "spec": spec, "dry_run": True},                        # 3
             {"op": "scene_digest"},                                                         # 4
             {"op": "faceted_temple", "spec": FX.refusal_cases()["E_RELIEF_STEP"]},          # 5
             {"op": "scene_digest"},                                                         # 6
             {"op": "faceted_temple", "spec": spec, "targets": {"+x": "Front | continuous milled acetate"}},  # 7
             {"op": "faceted_temple", "spec": spec},                                         # 8
             {"op": "dump_temples", "objects": [RIGHT, LEFT]},                               # 9
             {"op": "decal_standoffs", "objects": DECALS},                                   # 10
             {"op": "check_relief", "photo_creases": 4},                                     # 11
             {"op": "export", "path": str(glb)},                                             # 12
             {"op": "mesh_digests"},                                                         # 13
             {"op": "build_variant_meshes", "spec": variant},                                # 14
             {"op": "mesh_digests"},                                                         # 15
             {"op": "remove_variant_meshes"},                                                # 16
             {"op": "unfaceted_temple", "object": RIGHT},                                    # 17
             {"op": "unfaceted_temple", "object": LEFT},                                     # 18
             {"op": "scene_digest"},                                                         # 19
             {"op": "mesh_digests"},                                                         # 20
             {"op": "timing_faceted_temple", "spec": spec, "repeat": 3},                     # 21
             {"op": "add_material_slot", "object": RIGHT},                                   # 22
             {"op": "faceted_temple", "spec": spec},                                         # 23
             {"op": "core_dump", "spec": spec},                                              # 24
             {"op": "core_dump", "spec": variant},                                           # 25
             ]
    return run_steps(tmp, blend, steps, "main"), spec, variant, glb


@pytest.fixture(scope="module")
def fixtures_run(prada_copy):
    tmp, blend = prada_copy
    names = sorted(FX.FIXTURES)
    steps = [{"op": "scene_digest"}]
    for n in names:
        steps += [{"op": "faceted_temple", "spec": FX.FIXTURES[n](), "dry_run": True},
                  {"op": "core_dump", "spec": FX.FIXTURES[n]()}]
    steps += [{"op": "faceted_temple", "spec": FX.rolled(0.6)}, {"op": "dump_temples", "objects": [RIGHT, LEFT]},
              {"op": "unfaceted_temple", "object": RIGHT}, {"op": "unfaceted_temple", "object": LEFT},
              {"op": "scene_digest"}]
    return run_steps(tmp, blend, steps, "fixtures"), names


def test_every_fixture_plans_with_the_host_core(fixtures_run):
    st, names = fixtures_run
    for i, n in enumerate(names):
        r = st[1 + 2 * i]
        assert r["status"] == "planned", (n, r)
        assert r["scene_unchanged"], n
        same_core(st[2 + 2 * i], build_temple(FX.FIXTURES[n]()))
    assert st[-1]["digest"] == st[0]["digest"]
    # the valley fixture's four facets meet under the hallmarks: every decal spans a crease and says so
    valley = st[1 + 2 * names.index("valley")]
    spans = [d for s in ("+x", "-x") for d in valley["sides"][s]["decals"] if len(d["regions"]) > 1]
    assert len(spans) == 6 and sum(ln.startswith("W_DECAL_SPANS_CREASE") for ln in valley["lines"]) == 6
    assert "W_DECAL_SPANS_CREASE" not in st[1 + 2 * names.index("pyramid")]["warnings"]


def test_roll_fixture_is_the_core_roll(fixtures_run):
    st, names = fixtures_run
    k = 1 + 2 * len(names)
    r, dump = st[k], st[k + 1]
    assert r["status"] == "ok", r
    assert r["roll"]["roll_mm"] == 0.6
    tb = build_temple(FX.rolled(0.6))
    for t in dump["temples"]:
        V = np.array(t["V"])
        side = 1 if float(V[:, 0].mean()) > 0 else -1
        W, polys, _ = tb.world(side)
        assert t["polys"] == polys and np.abs(V - W).max() <= 1e-4    # the core's polygons, float32 storage
        ok, chi = closed_manifold(t["polys"])
        assert ok and chi == 2
        assert len(t["sharp"]) == len(tb.sharp)       # Blender split no fan: the roll joins its facet tangentially
        rolled = [pi for pi, kind in enumerate(tb.poly_kind) if kind == "roll"]
        flat_corners = sum(len(tb.polys[p]) for p in tb.flat_polys)
        assert rolled and r["sides"]["+x" if side > 0 else "-x"]["custom_normal_corners"] > flat_corners
    assert st[k + 2]["status"] == "ok" and st[k + 3]["status"] == "ok"


def test_dry_run_and_refusals_leave_the_scene_unchanged(main_run):
    st, _, _, _ = main_run
    assert st[3]["status"] == "planned" and st[3]["scene_unchanged"]
    assert st[0]["digest"] == st[4]["digest"]
    assert st[5]["status"] == "refused" and st[5]["code"] == "E_RELIEF_STEP" and st[5]["scene_unchanged"]
    assert st[6]["digest"] == st[0]["digest"]
    assert st[7]["status"] == "refused" and st[7]["code"] == "E_TARGET" and st[7]["scene_unchanged"]
    assert st[22]["slots"] == 2
    assert st[23]["status"] == "refused" and st[23]["code"] == "E_MULTI_MATERIAL" and st[23]["scene_unchanged"]


def test_written_temples_are_the_core_polygons(main_run):
    st, spec, _, _ = main_run
    ft = st[8]
    assert ft["status"] == "ok", ft
    tb = build_temple(spec)
    same_core(st[24], tb)
    assert ft["schema"] == "g3d.temple/2" and ft["lines"][:len(ft["creases"])] == [ln for ln in ft["lines"]
                                                                                   if "°" in ln]
    for t in st[9]["temples"]:
        V = np.array(t["V"])
        side = 1 if float(V[:, 0].mean()) > 0 else -1                  # side by the x sign, never the name
        W, polys, _ = tb.world(side)
        assert V.shape == W.shape and np.abs(V - W).max() <= 1e-4
        assert t["polys"] == polys
        ok, chi = closed_manifold(t["polys"])
        assert ok and chi == 2
        assert len(t["sharp"]) == len(tb.sharp)
        assert "WEIGHTED_NORMAL" not in t["modifiers"]
        ext = json.loads(t["props"]["g3d_temple"])
        assert ext["schema"] == "g3d.temple/2" and ext["spec_sha256"] == tb.spec.sha256
        assert ext["side"] == ("+x" if side > 0 else "-x") and ext["canonical_spec"] == tb.spec.canonical
        assert t["props"]["region"] == ("temple_+x" if side > 0 else "temple_-x")


def test_facet_corners_carry_the_face_normal(main_run):
    st, spec, _, _ = main_run
    tb = build_temple(spec)
    t = next(t for t in st[9]["temples"] if float(np.array(t["V"])[:, 0].mean()) > 0)     # the +X temple
    cn = np.array(t["corner_normals"])
    W, _, _ = tb.world(1)
    flat = set(tb.flat_polys)
    li, worst, n_flat = 0, 0.0, 0
    for pi, p in enumerate(t["polys"]):
        if pi in flat:
            P = W[p]
            n = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)              # Newell: any simple polygon
            n /= np.linalg.norm(n)
            for c in cn[li:li + len(p)]:
                worst = max(worst, math.degrees(math.acos(np.clip(c @ n / np.linalg.norm(c), -1, 1))))
            n_flat += 1
        li += len(p)
    assert n_flat == len(flat) > 0 and worst <= 0.1


def test_decals_move_by_chart_transfer(main_run):
    st, _, _, _ = main_run
    ft = st[8]
    before, after = st[2]["objects"], st[10]["objects"]
    for side in ("+x", "-x"):
        sr = ft["sides"][side]
        names = {d["name"] for d in sr["decals"]}
        assert names == {n for n in DECALS if (" right " in n) == (side == "+x")}     # by geometry, all three
        for d in sr["decals"]:
            assert d["outside"] == 0 and d["max_move_mm"] > 0.0
    for n in DECALS:
        a, b = np.array(before[n]["sd"]), np.array(after[n]["sd"])
        assert len(a) == len(b)
        # the vertex sits its old standoff along the new normal above a new surface point: never farther from the
        # new temple than that, and exactly that away from it inside a facet (float32 storage)
        assert float(np.max(np.abs(b) - np.abs(a))) <= 1e-4
        assert float(np.median(np.abs(b - a))) <= 1e-4


def test_hinge_report_grades_the_hinge_parts(main_run):
    st, _, _, _ = main_run
    ft = st[8]
    for side in ("+x", "-x"):
        sr = ft["sides"][side]
        assert sr["hinge"]["temple_knuckles"] and sr["hinge"]["frame_knuckles"] and sr["hinge"]["leaves"]
        hp = {h["name"] for h in sr["hinge_parts"]}
        assert hp and not hp & {d["name"] for d in sr["decals"]}       # hinge parts are never decals
        graded = {r["name"] for k in ("leaves", "temple_knuckles", "frame_knuckles") for r in sr["hinge"][k]}
        assert graded == hp


def test_export_validates_and_charts_from_the_extras(main_run):
    st, _, _, glb = main_run
    assert st[12]["status"] == "exported", st[12]
    rep = validate_spec(glb)
    assert rep["ok"], rep
    cr = st[11]
    for side in ("+x", "-x"):
        assert cr["temples"][side]["chart"] == "g3d_temple extras"
        assert cr["temples"][side]["inner_flatness_mm"] <= 1e-3


def test_variant_meshes_leave_the_objects_and_are_removed(main_run):
    st, _, variant, _ = main_run
    vr = st[14]
    assert vr["status"] == "ok", vr
    same_core(st[25], build_temple(variant))
    assert set(vr["meshes"]) == {"+x", "-x"}
    assert vr["overrides"][RIGHT] == vr["meshes"]["+x"] and vr["overrides"][LEFT] == vr["meshes"]["-x"]
    assert set(vr["overrides"]) == {RIGHT, LEFT} | set(DECALS)
    after = st[15]
    assert after["digests"] == st[13]["digests"] and after["data_names"] == st[13]["data_names"]   # objects untouched
    assert not set(vr["overrides"].values()) & set(after["data_names"].values())
    rm = st[16]
    assert rm["status"] == "ok" and sorted(rm["removed"]) == sorted(vr["overrides"].values())


def test_revert_is_byte_identical(main_run):
    st, _, _, _ = main_run
    assert st[17]["status"] == "ok" and st[18]["status"] == "ok"
    assert set(st[17]["decals_restored"]) | set(st[18]["decals_restored"]) == set(DECALS)
    assert st[19]["digest"] == st[0]["digest"]
    assert st[20]["digests"] == st[1]["digests"] and st[20]["data_names"] == st[1]["data_names"]


def test_budget(main_run):
    st, _, _, _ = main_run
    tm = st[21]                                                        # the v1 budgets, kept
    assert tm["status"] == "planned" and min(tm["seconds"]) <= 1.0       # both temples planned in Blender <= 1 s
    assert tm["core_ms"] <= 80.0
    assert st[8]["budget"]["total_ms"] <= 1000.0


def test_loft_helper(prada_copy):
    tmp, blend = prada_copy
    sec = polygon_section([[-1, -2], [1, -2], [1, 2], [-1, 2]], ("round", 0.5))
    stations = [{"t": 0.0, "points": sec["points"].tolist(), "sharp": sec["sharp"].tolist()},
                {"t": 40.0, "points": (sec["points"] * 0.6).tolist(), "sharp": sec["sharp"].tolist()}]
    st = run_steps(tmp, blend, [{"op": "loft_sections", "stations": stations, "name": "wp6 loft probe"},
                                {"op": "check_round_corners", "object": "wp6 loft probe"}], "loft")
    assert st[0]["status"] == "ok" and st[0]["closed"] and st[0]["euler"] == 2
    assert st[1]["ok"]
