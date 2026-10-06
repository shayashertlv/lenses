"""WP6 g3d.faceted_temple in headless Blender 5.2 (skipped without Blender or the Prada scene): the tool contract of
g3d/temple_bpy.py on a COPY of the Prada scene (source sha256 unchanged) - host vs Blender core identical, float32
storage, sharp edges and facet custom normals, decals re-seated, WeightedNormal removed and restored, extras, dry run
and refusals leave the scene digest unchanged, revert byte-identical, budgets, roll, export -> validate_spec + the
independent P0 instrument, and the §4 loft helper."""
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
from glasses3d.g3d_host import blender
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.g3d.temple.sections import polygon_section

HERE = Path(__file__).resolve().parent
RUN = HERE / "proofs" / "wp6_bpy_run.py"
REPO = HERE.parents[1]
PRADA = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
PRADA_SHA = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
RIGHT = "Temple right | faceted sweep and ear bend"        # +X (Astra's "right" is the wearer's left)
LEFT = "Temple left | faceted sweep and ear bend"

pytestmark = [pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed"),
              pytest.mark.skipif(not PRADA.exists(), reason="Prada scene not available"), pytest.mark.slow]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_steps(tmp: Path, blend: Path, steps: list, name: str) -> list:
    sp, rp = tmp / f"{name}_steps.json", tmp / f"{name}_receipt.json"
    sp.write_text(json.dumps(steps), encoding="utf-8")
    proc = blender.run(blend, RUN, [sp, rp], timeout_s=600)
    assert rp.exists(), proc.stderr[-3000:] + proc.stdout[-3000:]
    return json.loads(rp.read_text(encoding="utf-8"))["steps"]


@pytest.fixture(scope="module")
def prada_copy(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wp6_blender")
    before = sha(PRADA)
    dst = tmp / "prada_copy.blend"
    shutil.copy2(PRADA, dst)
    yield tmp, dst
    assert sha(PRADA) == before == PRADA_SHA                       # the source is never written


@pytest.fixture(scope="module")
def main_run(prada_copy):
    tmp, blend = prada_copy
    spec = FX.prada_default()
    glb = tmp / "faceted.glb"
    steps = [{"op": "scene_digest"}, {"op": "mesh_digests", "objects": [RIGHT, LEFT]},
             {"op": "faceted_temple", "spec": spec, "dry_run": True}, {"op": "scene_digest"},
             {"op": "faceted_temple", "spec": FX.refusal_cases()["E_RELIEF_STEP"]}, {"op": "scene_digest"},
             {"op": "faceted_temple", "spec": spec, "targets": {"+x": "Front | continuous milled acetate"}},
             {"op": "faceted_temple", "spec": spec}, {"op": "dump_temples", "objects": [RIGHT, LEFT]},
             {"op": "check_relief", "photo_creases": 4}, {"op": "export", "path": str(glb)},
             {"op": "unfaceted_temple", "object": RIGHT}, {"op": "unfaceted_temple", "object": LEFT},
             {"op": "scene_digest"}, {"op": "mesh_digests", "objects": [RIGHT, LEFT]},
             {"op": "timing_faceted_temple", "spec": spec, "repeat": 3}]
    return run_steps(tmp, blend, steps, "main"), spec, glb


def test_dry_run_and_refusals_leave_the_scene_unchanged(main_run):
    st, _, _ = main_run
    assert st[2]["status"] == "planned" and st[2]["scene_unchanged"]
    assert st[0]["digest"] == st[3]["digest"]
    assert st[4]["status"] == "refused" and st[4]["code"] == "E_RELIEF_STEP" and st[4]["scene_unchanged"]
    assert st[4]["fields"]["node"] == "B"
    assert st[5]["digest"] == st[0]["digest"]
    assert st[6]["status"] == "refused" and st[6]["code"] == "E_TARGET"


def test_host_and_blender_cores_agree_and_storage_is_float32(main_run):
    st, spec, _ = main_run
    ft = st[7]
    assert ft["status"] == "ok", ft
    tb = build_temple(spec)
    assert np.array_equal(np.array(ft["core_digest"]["V"]), tb.V)          # bit-identical core in Blender's numpy
    for t in st[8]["temples"]:
        side = 1 if t["name"] == RIGHT else -1
        W, polys, _ = tb.world(side)
        V = np.array(t["V"])
        assert V.shape == W.shape and np.abs(V - W).max() <= 1e-4
        assert t["polys"] == polys
        assert len(t["sharp"]) == len(tb.sharp)
        assert "WEIGHTED_NORMAL" not in t["modifiers"]
        ext = json.loads(t["props"]["g3d_temple"])
        assert ext["spec_sha256"] == tb.spec.sha256 and ext["side"] == ("+x" if side > 0 else "-x")
        assert t["props"]["region"] == ("temple_+x" if side > 0 else "temple_-x")


def test_facet_corners_carry_the_face_normal(main_run):
    st, spec, _ = main_run
    tb = build_temple(spec)
    t = next(t for t in st[8]["temples"] if t["name"] == RIGHT)
    cn = np.array(t["corner_normals"])
    W, polys, _ = tb.world(1)
    li = 0
    worst = 0.0
    for pi, p in enumerate(t["polys"]):
        if pi in tb.flat_polys:
            P = W[p]
            n = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)              # Newell: any simple polygon
            n /= np.linalg.norm(n)
            for c in cn[li:li + len(p)]:
                worst = max(worst, math.degrees(math.acos(np.clip(c @ n / np.linalg.norm(c), -1, 1))))
        li += len(p)
    assert worst <= 0.1


def test_decals_hinge_and_receipt(main_run):
    st, _, _ = main_run
    ft = st[7]
    for side in ("+x", "-x"):
        sr = ft["sides"][side]
        assert len(sr["decals"]) >= 2
        for d in sr["decals"]:
            assert d["misses"] == 0 and d["max_move_mm"] <= 1.0 and len(d["regions"]) == 1
        assert sr["hinge"]["temple_knuckles"] and sr["hinge"]["frame_knuckles"]
        assert all(k["penetration_mm"] <= 0.1 for k in sr["hinge"]["frame_knuckles"])
    assert ft["budget"]["total_ms"] <= 1000.0                   # both temples in Blender <= 1 s
    tm = st[15]
    assert tm["status"] == "planned" and min(tm["seconds"]) <= 1.0
    assert tm["core_ms"] <= 80.0


def test_revert_is_byte_identical(main_run):
    st, _, _ = main_run
    assert st[11]["status"] == "ok" and st[12]["status"] == "ok"
    assert st[13]["digest"] == st[0]["digest"]
    assert st[14]["digests"] == st[1]["digests"] and st[14]["data_names"] == st[1]["data_names"]


def test_export_validates_and_passes_the_independent_instrument(main_run):
    st, spec, glb = main_run
    assert st[10]["status"] == "exported", st[10]
    rep = validate_spec(glb)
    assert rep["ok"], rep
    from glasses3d.tests.proofs.wp6_p0_instrument import grade_glb
    g = grade_glb(glb, hinge_z=-10.6, zone_end=spec["zone_end_d"], outline=spec["outline"],
                  facet_polys={l: [spec["nodes"][n][:2] for n in f["poly"]] for l, f in spec["facets"].items()})
    assert g["pass"], json.dumps({s: {k: v for k, v in t["checks"].items() if not v["pass"]}
                                  for s, t in g["temples"].items()}, default=str)
    cr = st[9]
    for side in ("+x", "-x"):
        assert cr["temples"][side]["chart"] == "g3d_temple extras"
        assert cr["temples"][side]["inner_flatness_mm"] <= 1e-3


def test_roll_and_loft_helper(prada_copy):
    tmp, blend = prada_copy
    sec = polygon_section([[-1, -2], [1, -2], [1, 2], [-1, 2]], ("round", 0.5))
    stations = [{"t": 0.0, "points": sec["points"].tolist(), "sharp": sec["sharp"].tolist()},
                {"t": 40.0, "points": (sec["points"] * 0.6).tolist(), "sharp": sec["sharp"].tolist()}]
    st = run_steps(tmp, blend, [{"op": "faceted_temple", "spec": FX.rolled()},
                                {"op": "dump_temples", "objects": [RIGHT]},
                                {"op": "loft_sections", "stations": stations, "name": "wp6 loft probe"},
                                {"op": "check_round_corners", "object": "wp6 loft probe"}], "roll")
    r = st[0]
    if r["status"] == "refused":
        assert r["code"] == "E_ROLL" and r["scene_unchanged"]
    else:
        assert r["status"] == "ok", r
        roll = r["sides"]["+x"]["roll"]
        assert roll["ok"] and roll["euler"] == 2 and roll["facet_planarity_mm"] <= 1e-4
        assert roll["silhouette_overshoot_mm"] <= 0.01 and roll["outline_knot_miss_mm"] <= 0.01
    assert st[2]["status"] == "ok" and st[2]["closed"] and st[2]["euler"] == 2
    assert st[3]["ok"]
