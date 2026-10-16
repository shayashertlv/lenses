"""WP6 v2 g3d.faceted_temple in headless Blender 5.2 (skipped without Blender or the Prada scene): the tool contract of
g3d/temple_bpy.py (plan/WP6_FACETS.md §2.5, §2.6) on a COPY of the Prada scene (source sha256 unchanged).

- every /2 fixture plans in Blender with the host's core bit for bit, leaving the scene unchanged;
- the roll fixture (core roll, no Blender Bevel) is written closed and 2-manifold with its rolled polygons;
- v3 (WP6_V3 §2): a warp facet beside a soft flat facet (test_temple_warp.prada_warp) is written as the core's
  polygons with its sharp set (Blender splits no fan), the warp's triangles carrying the design surface's normals and
  the soft border's ramp the facet's normal; the exported GLB holds the core's vertices within 1e-4 mm, splits every
  crease vertex and none inside the warp; the revert restores the scene; run 7's layout fitted with a warp crown is
  written with every design normal and exported with Blender's normals within 0.5 deg at every crown vertex;
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
from glasses3d.g3d.temple.build import OUTER_KINDS, design_corner_normals, mesh_topology
from glasses3d.g3d.temple.convert import v1_to_v2
from glasses3d.g3d.temple.sections import polygon_section
from glasses3d.g3d.temple.spec import spec_sha256
from glasses3d.g3d_host import blender
from glasses3d.g3d_host import glb as GLB
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.tests.test_temple_warp import prada_warp

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


def null_axis(spec: dict) -> dict:
    s = json.loads(json.dumps(spec))
    s["hinge"]["axis_xz_mm"] = None
    return s


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
             {"op": "export", "path": str(glb)},                                             # 11
             {"op": "mesh_digests"},                                                         # 12
             {"op": "build_variant_meshes", "spec": variant},                                # 13
             {"op": "mesh_digests"},                                                         # 14
             {"op": "remove_variant_meshes"},                                                # 15
             {"op": "unfaceted_temple", "object": RIGHT},                                    # 16
             {"op": "unfaceted_temple", "object": LEFT},                                     # 17
             {"op": "scene_digest"},                                                         # 18
             {"op": "mesh_digests"},                                                         # 19
             {"op": "timing_faceted_temple", "spec": spec, "repeat": 3},                     # 20
             {"op": "add_material_slot", "object": RIGHT},                                   # 21
             {"op": "faceted_temple", "spec": spec},                                         # 22
             {"op": "core_dump", "spec": spec},                                              # 23
             {"op": "core_dump", "spec": variant},                                           # 24
             {"op": "faceted_temple", "spec": null_axis(variant), "side": "-x", "dry_run": True},  # 25
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


@pytest.fixture(scope="module")
def warp_run(prada_copy):
    tmp, blend = prada_copy
    glb = tmp / "warp.glb"
    steps = [{"op": "scene_digest"},                                                         # 0
             {"op": "core_dump", "spec": prada_warp()},                                      # 1
             {"op": "faceted_temple", "spec": prada_warp()},                                 # 2
             {"op": "dump_temples", "objects": [RIGHT, LEFT]},                               # 3
             {"op": "export", "path": str(glb)},                                             # 4
             {"op": "unfaceted_temple", "object": RIGHT},                                    # 5
             {"op": "unfaceted_temple", "object": LEFT},                                     # 6
             {"op": "scene_digest"}]                                                         # 7
    return run_steps(tmp, blend, steps, "warp"), glb


def _ang(c, n) -> float:
    return math.degrees(math.acos(np.clip(np.dot(c, n) / (np.linalg.norm(c) * np.linalg.norm(n)), -1.0, 1.0)))


def run7_warp_spec() -> dict:
    from glasses3d.g3d.temple import fit as FT
    from glasses3d.tests.test_temple_fit import run7_layout
    return FT.fit_spec(run7_layout("warp")).spec


@pytest.fixture(scope="module")
def run7_warp_run(prada_copy):
    tmp, blend = prada_copy
    glb = tmp / "run7_warp.glb"
    spec = run7_warp_spec()
    steps = [{"op": "scene_digest"},                                                         # 0
             {"op": "faceted_temple", "spec": spec},                                         # 1
             {"op": "dump_temples", "objects": [RIGHT, LEFT]},                               # 2
             {"op": "export", "path": str(glb)},                                             # 3
             {"op": "unfaceted_temple", "object": RIGHT},                                    # 4
             {"op": "unfaceted_temple", "object": LEFT},                                     # 5
             {"op": "scene_digest"}]                                                         # 6
    return run_steps(tmp, blend, steps, "run7_warp"), glb, spec


def test_run7_warp_crown_exports_its_shading(run7_warp_run):
    """Run 7's layout fitted with a warp crown (K 1.7 deg from straight, +1.5 mm; roll 0.25) written and exported:
    every design custom normal (warp fans, K's cone mean, the roll's start line) is Blender's corner normal within
    0.1 deg, and at every vertex of the crown the GLB (the owner's viewer) holds Blender's normals within 0.5 deg. The
    v3 refinement's slivers beside the crown|logo edge (0.002 deg triangles) put the GLB 5.6 deg off Blender there
    (review-builder-design F2: measured 0.07 deg after the clearance rule); the corner plane put K's normal 97 deg off
    its triangles (review-builder-correctness P1-1)."""
    st, glb, spec = run7_warp_run
    tb = build_temple(spec)
    assert st[1]["status"] == "ok", st[1]
    t = next(t for t in st[2]["temples"] if float(np.array(t["V"])[:, 0].mean()) > 0)
    W, core_polys, core_tris = tb.world(1)
    polys = t['polys']
    sources, cursor = [], 0
    for pi, polygon in enumerate(core_polys):
        if polys[cursor] == polygon:
            sources.append(pi)
            cursor += 1
        else:
            expected = core_tris[tb.tri_poly == pi].tolist()
            assert polys[cursor:cursor+len(expected)] == expected
            sources.extend([pi]*len(expected))
            cursor += len(expected)
    assert cursor == len(polys) and len(t["sharp"]) == len(tb.sharp)
    ok, chi = closed_manifold(polys)
    assert ok and chi == 2
    actual_points=np.asarray(t['V'])[np.asarray(t['T'])]
    area=np.linalg.norm(np.cross(actual_points[:, 1]-actual_points[:, 0], actual_points[:, 2]-actual_points[:, 0]),axis=1)/2
    assert area.min() > 1e-12
    cn = np.array(t["corner_normals"])
    starts = np.cumsum([0] + [len(p) for p in polys])
    dn = design_corner_normals(tb)
    want = {k: n for k, n in zip(dn, _world_dirs_plus_x(tb, np.array(list(dn.values()))))}
    assert len(dn) > 1000
    source_corners = {}
    for pi, polygon in enumerate(polys):
        for j, v in enumerate(polygon):
            source_corners.setdefault((sources[pi], v), []).append(cn[starts[pi]+j])
    assert max(_ang(n, want[key]) for key in dn for n in source_corners[key]) <= 0.1
    assert st[3]["status"] == "exported", st[3]
    g = GLB.load(glb)
    prim = [p for p in g.primitives if p.node_name == RIGHT]
    P = np.vstack([p.positions for p in prim]) * 1000.0
    Nrm = np.vstack([p.normals for p in prim])
    from scipy.spatial import cKDTree
    tree = cKDTree(P)
    loops: dict = {}
    for pi, p in enumerate(polys):
        for j, v in enumerate(p):
            loops.setdefault(v, []).append(cn[starts[pi] + j])
    crown = {v for i, p in enumerate(tb.polys) if tb.poly_region[i].partition("/")[0] == "crown" for v in p}
    worst = 0.0
    for v in crown:
        gs = [Nrm[k] for k in tree.query_ball_point(W[v], 1e-4)]
        assert gs, v
        worst = max([worst] + [min(_ang(x, y) for y in gs) for x in loops[v]]
                    + [min(_ang(x, y) for y in loops[v]) for x in gs])
    assert worst <= 0.5, worst
    assert st[4]["status"] == "ok" and st[5]["status"] == "ok" and st[6]["digest"] == st[0]["digest"]


def test_warp_temple_is_written_and_exported(warp_run):
    """WP6_V3 §2's Blender layer: a warp facet W (P3 1.5 mm off) beside a soft flat facet S. Written: the core's
    polygons (the Blender numpy core = the host's), closed, Euler 2, every sharp edge and no more (no fan split); each
    warp triangle corner carries the design surface's normal (design_corner_normals), each soft-border ramp corner the
    facet's face normal, within 0.1 deg. Exported: every GLB vertex of the +X temple within 1e-4 mm of a core vertex
    and every core vertex within 1e-4 mm of a GLB vertex; at every vertex of a W|S or W|base crease the GLB holds two
    normals more than 1 deg apart (the sharp edge kept), at every vertex inside W exactly one (smooth). The revert
    restores the scene."""
    st, glb = warp_run
    tb = build_temple(prada_warp())
    same_core(st[1], tb)
    assert st[2]["status"] == "ok", st[2]
    assert any(f["label"] == "W" and "warp_mm" in f for f in st[2]["facets"])
    t = next(t for t in st[3]["temples"] if float(np.array(t["V"])[:, 0].mean()) > 0)     # the +X temple
    W, polys, _ = tb.world(1)
    assert t["polys"] == polys and np.abs(np.array(t["V"]) - W).max() <= 1e-4
    ok, chi = closed_manifold(t["polys"])
    assert ok and chi == 2 and len(t["sharp"]) == len(tb.sharp)
    cn = np.array(t["corner_normals"])
    starts = np.cumsum([0] + [len(p) for p in polys])
    dn = design_corner_normals(tb)
    want = {k: n for k, n in zip(dn, _world_dirs_plus_x(tb, np.array(list(dn.values()))))}
    warp_corners = [(i, v) for (i, v) in dn if tb.poly_cell[i] == "warp"]
    soft_corners = [(i, v) for (i, v) in dn if tb.poly_region[i] == "base"]
    assert len(warp_corners) > 500 and len(soft_corners) >= 10
    worst = max(_ang(cn[starts[i] + polys[i].index(v)], want[(i, v)]) for (i, v) in warp_corners + soft_corners)
    assert worst <= 0.1, worst
    assert st[4]["status"] == "exported", st[4]
    g = GLB.load(glb)
    prim = [p for p in g.primitives if p.node_name == RIGHT]
    assert prim, sorted({p.node_name for p in g.primitives})
    P = np.vstack([p.positions for p in prim]) * 1000.0
    Nrm = np.vstack([p.normals for p in prim])
    from scipy.spatial import cKDTree
    d1, _ = cKDTree(W).query(P)
    d2, _ = cKDTree(P).query(W)
    assert d1.max() <= 1e-4 and d2.max() <= 1e-4, (d1.max(), d2.max())
    tree = cKDTree(P)

    def distinct(ns, tol):
        out = []
        for n in ns:
            if not any(_ang(n, o) <= tol for o in out):
                out.append(n)
        return out
    loops: dict = {}                                                   # Blender's own corner normals per vertex
    for pi, p in enumerate(polys):
        for j, v in enumerate(p):
            loops.setdefault(v, []).append(cn[starts[pi] + j])
    topo = mesh_topology(tb.polys)
    crease_v = set()
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"][(b, a)]
        if a > b or tb.poly_kind[p1] not in OUTER_KINDS or tb.poly_kind[p2] not in OUTER_KINDS:
            continue
        if "W" in {tb.poly_region[p1], tb.poly_region[p2]} and tb.poly_region[p1] != tb.poly_region[p2] \
                and (a, b) in set(tb.sharp):
            crease_v |= {a, b}
    at_v: dict = {}
    for i, p in enumerate(tb.polys):
        for v in p:
            at_v.setdefault(v, []).append(i)
    inside_v = {v for v, ps_ in at_v.items() if all(tb.poly_region[i] == "W" and tb.poly_kind[i] == "outer"
                                                    for i in ps_)}           # (an outline vertex has a strip too)
    assert len(crease_v) >= 20 and len(inside_v) >= 100
    worst, split = 0.0, 0
    w_verts = {v for i, p in enumerate(tb.polys) if tb.poly_region[i] == "W" for v in p}
    for v in range(len(tb.arr.V)):
        g = [Nrm[k] for k in tree.query_ball_point(W[v], 1e-4)]
        b = distinct(loops[v], 0.05)
        if v in w_verts:                                                 # the warp's custom normals survive export
            worst = max([worst] + [min(_ang(x, y) for y in g) for x in b] + [min(_ang(x, y) for y in b) for x in g])
        if v in inside_v:
            assert len(distinct(g, 0.05)) == 1, v                       # smooth: one normal inside the warp
        if v in crease_v and len(distinct(b, 1.0)) >= 2:                 # a crease whose sides differ by > 1 deg
            assert len(distinct(g, 0.5)) >= 2, v                        # ... stays split in the GLB
            split += 1
    assert split >= 20 and worst <= 1.0, (split, worst)                 # export noise at the smallest triangles
    assert st[5]["status"] == "ok" and st[6]["status"] == "ok" and st[7]["digest"] == st[0]["digest"]


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


def _world_dirs_plus_x(tb, N) -> np.ndarray:
    """Local (d, y, X) directions -> world for the +X temple at opening 0 (x = X, y, z = -d)."""
    assert tb.spec.hinge["opening_deg"] == 0.0
    return np.stack([N[:, 2], N[:, 1], -N[:, 0]], axis=1)


def test_dry_run_and_refusals_leave_the_scene_unchanged(main_run):
    st, _, _, _ = main_run
    assert st[3]["status"] == "planned" and st[3]["scene_unchanged"]
    assert st[0]["digest"] == st[4]["digest"]
    assert st[5]["status"] == "refused" and st[5]["code"] == "E_RELIEF_STEP" and st[5]["scene_unchanged"]
    assert st[6]["digest"] == st[0]["digest"]
    assert st[7]["status"] == "refused" and st[7]["code"] == "E_TARGET" and st[7]["scene_unchanged"]
    assert st[21]["slots"] == 2
    assert st[22]["status"] == "refused" and st[22]["code"] == "E_MULTI_MATERIAL" and st[22]["scene_unchanged"]


def test_written_temples_are_the_core_polygons(main_run):
    st, spec, _, _ = main_run
    ft = st[8]
    assert ft["status"] == "ok", ft
    tb = build_temple(spec)
    same_core(st[23], tb)
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


def test_export_validates(main_run):
    """(check_relief, which charted the written temples from their extras, was deleted 2026-10-13.)"""
    st, _, _, glb = main_run
    assert st[11]["status"] == "exported", st[11]
    rep = validate_spec(glb)
    assert rep["ok"], rep


def test_a_filled_axis_keeps_the_written_sha(main_run):
    st, _, variant, _ = main_run
    r = st[25]                                                         # -X only: +X has two material slots by now
    assert r["status"] == "planned", r
    s = null_axis(variant)
    filled = json.loads(json.dumps(s))
    filled["hinge"]["axis_xz_mm"] = r["axis_filled_mm"]
    assert r["spec_sha256"] == spec_sha256(s) == build_temple(s).spec.sha256 != spec_sha256(filled)
    assert len(r["axis_filled_mm"]) == 2 and r["axis_from"]            # the knuckles named
    assert abs(r["axis_filled_mm"][0] - 64.6) < 2.0 and abs(r["axis_filled_mm"][1] - (-10.1)) < 2.0


def test_variant_meshes_leave_the_objects_and_are_removed(main_run):
    st, _, variant, _ = main_run
    vr = st[13]
    assert vr["status"] == "ok", vr
    same_core(st[24], build_temple(variant))
    assert set(vr["meshes"]) == {"+x", "-x"}
    assert vr["overrides"][RIGHT] == vr["meshes"]["+x"] and vr["overrides"][LEFT] == vr["meshes"]["-x"]
    assert set(vr["overrides"]) == {RIGHT, LEFT} | set(DECALS)
    after = st[14]
    assert after["digests"] == st[12]["digests"] and after["data_names"] == st[12]["data_names"]   # objects untouched
    assert not set(vr["overrides"].values()) & set(after["data_names"].values())
    rm = st[15]
    assert rm["status"] == "ok" and sorted(rm["removed"]) == sorted(vr["overrides"].values())


def test_revert_is_byte_identical(main_run):
    st, _, _, _ = main_run
    assert st[16]["status"] == "ok" and st[17]["status"] == "ok"
    assert set(st[16]["decals_restored"]) | set(st[17]["decals_restored"]) == set(DECALS)
    assert st[18]["digest"] == st[0]["digest"]
    assert st[19]["digests"] == st[1]["digests"] and st[19]["data_names"] == st[1]["data_names"]


def test_budget(main_run):
    st, _, _, _ = main_run
    tm = st[20]                                                        # the v1 budgets, kept
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
