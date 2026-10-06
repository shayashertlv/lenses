"""WP4 tools in headless Blender 5.2 (skipped without Blender): the Blender half of WP4_DESIGN.md §10 P1 and the tool
contract of g3d/seat_bpy.py.

P1 (Blender half):
- numpy, BVH and open3d agree within 1e-3 mm on every fixture and on INVU: Blender measures with BVHBackend and dumps
  the frames, parts and results; the host re-measures the same arrays with NumpyBackend and Open3DBackend;
- the Blender check (g3d.check_lens_seating) on a scene and the host check (seat_check.check_glb) on the GLB the WP3
  exporter writes from it give the same verdict at every station and captures within 0.01 mm (stations matched by
  position; the reports round to 1e-4 mm);
- a second seat_lens call moves 0 vertices and returns already_seated (also verified inside the tool after every
  write, where anything else rolls back).
Also: write-back through rotated / mirrored object matrices and an asset origin != world origin, custom normals,
hide_eyewire + browline seating, the crystal V-bevel and groove, the nylor groove and cord, unseat / unhide, the CLI,
and the source .blend files' sha256 (never written). Every scene is a COPY under the test's temporary directory.
"""
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.seat import stations
from glasses3d.g3d.seat.geom import weld_exact
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.g3d_host import blender
from glasses3d.g3d_host.seat_check import Open3DBackend, check_glb
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.tests.fixtures import seat_fixtures as SF

HERE = Path(__file__).resolve().parent
BUILD = HERE / "blender" / "seat_build.py"
RUN = HERE / "blender" / "seat_run.py"
CLI = HERE.parent / "g3d" / "cli.py"
REPO = HERE.parents[1]
JOBS = REPO / "automation/data/blender_agent/studio/jobs"
REAL = {
    "ft": JOBS / "3b8a1c7ae8114868b49b298b681f1b29/original/scene.blend",
    "prada": JOBS / "8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend",
    "oakley": JOBS / "3dff867466d449c9a6369273f0a14631/agent/scene.blend",
    "invu": JOBS / "c8e81c7214a646cfa07e082c59128f10/agent/scene.blend",
}
REAL_LENS = {"ft": "Lens_Left", "prada": "Lens left | closed 49 x 36 mm",
             "oakley": "Lens | continuous wrap shield 1.55mm", "invu": "Shield | single continuous closed lens"}
REAL_CHECK = {"ft": {"frame_type": "browline", "hidden_x_range_mm": [[-56.2, -11.6], [11.6, 56.2]]},
              "prada": {"frame_type": "acetate"}, "oakley": {"frame_type": "semi_rimless"},
              "invu": {"frame_type": "semi_rimless"}}
P1_TOL_MM = 1e-3
CHECK_CAP_TOL_MM = 0.01
FIXTURE_FT = {"metal": "metal", "acetate_sphere": "acetate", "acetate_toric": "acetate", "acetate_chamfer": "acetate",
              "acetate_rounded": "acetate", "acetate_coarse": "acetate", "acetate_origin": "acetate",
              "crystal": "acetate", "nylor": "semi_rimless", "browline_lifted": "browline",
              "pair_transformed": "acetate", "browline": "browline"}

pytestmark = [pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed"),
              pytest.mark.slow]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def lenses_of(scene: str) -> list:
    return ["Lens_L", "Lens_R"] if scene == "pair_transformed" else ["Lens_L"]


def fixture_check_kwargs(scene: str) -> dict:
    kw = {"frame_type": FIXTURE_FT[scene]}
    if scene == "nylor":
        kw["lower_edge"] = "nylor"
    if scene in ("browline_lifted", "browline"):
        sc = SF.browline_lifted_scene() if scene == "browline_lifted" else SF.browline_scene()
        kw["hidden_x_range_mm"] = [list(sc["range"])]
    return kw


# --------------------------------------------------------------------------------------------------- the runs
@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("seat_blender")


@pytest.fixture(scope="module")
def scenes(workdir):
    out = workdir / "scenes"
    proc = blender.run(None, BUILD, [out], timeout_s=300)
    built = {p.stem: p for p in out.glob("*.blend")}
    assert "metal" in built and "browline" in built, proc.stdout[-1500:] + proc.stderr[-1500:]
    return built


def _jobs(workdir: Path, scenes: dict) -> dict:
    """{case: (source .blend, steps)}; every case runs on its own copy."""
    W = workdir
    jobs = {}
    for sc in FIXTURE_FT:
        steps = [{"op": "measure_dump", "lens": ln, "npz": str(W / f"{sc}-{ln}.npz"), "key": f"measure:{ln}"}
                 for ln in lenses_of(sc)]
        steps += [{"op": "check", "key": "check", "kwargs": fixture_check_kwargs(sc)},
                  {"op": "export", "key": "export", "glb": str(W / f"{sc}.glb")}]
        jobs[f"p1:{sc}"] = (scenes[sc], steps)
    jobs["metal_seat"] = (scenes["metal"], [
        {"op": "digest", "key": "digest0", "objects": ["Lens_L", "Eyewire"]},
        {"op": "seat", "key": "dry", "kwargs": {"lens": "Lens_L", "frame_type": "metal", "dry_run": True}},
        {"op": "digest", "key": "digest_dry", "objects": ["Lens_L", "Eyewire"]},
        {"op": "arrays", "key": "arrays0", "objects": ["Lens_L"], "npz": str(W / "metal-a0.npz")},
        {"op": "seat", "key": "seat1", "kwargs": {"lens": "Lens_L", "frame_type": "metal"}},
        {"op": "seat", "key": "seat2", "kwargs": {"lens": "Lens_L", "frame_type": "metal"}},
        {"op": "arrays", "key": "arrays1", "objects": ["Lens_L"], "npz": str(W / "metal-a1.npz")},
        {"op": "check", "key": "check", "kwargs": {}},
        {"op": "check", "key": "check_names", "kwargs": {"lenses": "Lens_L", "frame_parts": "Eyewire"}},
        {"op": "check", "key": "check_missing", "kwargs": {"lenses": "Nope"}},
        {"op": "export", "key": "export", "glb": str(W / "metal-seated.glb")},
        {"op": "digest", "key": "digest1", "objects": ["Lens_L", "Eyewire"]},
        {"op": "unseat", "key": "unseat", "lens": "Lens_L"},
        {"op": "digest", "key": "digest2", "objects": ["Lens_L", "Eyewire"]}])
    jobs["refusals"] = (scenes["metal"], [
        {"op": "seat", "key": "no_type", "kwargs": {"lens": "Lens_L"}},
        {"op": "seat", "key": "bad_type", "kwargs": {"lens": "Lens_L", "frame_type": "plastic"}},
        {"op": "seat", "key": "no_object", "kwargs": {"lens": "Nope", "frame_type": "metal"}},
        {"op": "seat", "key": "uv", "kwargs": {"lens": "Lens_L", "frame_type": "metal", "uv": "extrapolate"}},
        {"op": "seat", "key": "browline_no_ranges", "kwargs": {"lens": "Lens_L", "frame_type": "browline"}},
        {"op": "exec", "key": "share", "code": "o = bpy.data.objects['Lens_L']; d = o.copy(); "
                                               "bpy.context.scene.collection.objects.link(d); d.hide_render = True"},
        {"op": "seat", "key": "shared", "kwargs": {"lens": "Lens_L", "frame_type": "metal"}},
        {"op": "exec", "key": "unshare", "code": "bpy.data.objects.remove(bpy.data.objects['Lens_L.001'])"},
        {"op": "exec", "key": "bevel", "code": "bpy.data.objects['Lens_L'].modifiers.new('Bevel', 'BEVEL')"},
        {"op": "seat", "key": "modifier", "kwargs": {"lens": "Lens_L", "frame_type": "metal"}},
        {"op": "hide", "key": "hide_no_ranges", "kwargs": {"eyewire": "Eyewire", "under": "Eyewire",
                                                            "hidden_x_range_mm": []}},
        {"op": "digest", "key": "digest", "objects": ["Lens_L", "Eyewire"]}])
    jobs["pair_seat"] = (scenes["pair_transformed"], [
        {"op": "arrays", "key": "arrays0", "objects": ["Lens_L", "Lens_R"], "npz": str(W / "pair-a0.npz")},
        {"op": "seat", "key": "seatL", "kwargs": {"lens": "Lens_L", "frame_type": "acetate"}},
        {"op": "seat", "key": "seatR", "kwargs": {"lens": "Lens_R", "frame_type": "acetate"}},
        {"op": "seat", "key": "seatL2", "kwargs": {"lens": "Lens_L", "frame_type": "acetate"}},
        {"op": "seat", "key": "seatR2", "kwargs": {"lens": "Lens_R", "frame_type": "acetate"}},
        {"op": "arrays", "key": "arrays1", "objects": ["Lens_L", "Lens_R"], "npz": str(W / "pair-a1.npz")},
        {"op": "check", "key": "check", "kwargs": {}},
        {"op": "export", "key": "export", "glb": str(W / "pair-seated.glb")}])
    jobs["normals_seat"] = (scenes["custom_normals_acetate"], [
        {"op": "arrays", "key": "arrays0", "objects": ["Lens_L"], "npz": str(W / "normals-a0.npz")},
        {"op": "seat", "key": "seat1", "kwargs": {"lens": "Lens_L", "frame_type": "acetate"}},
        {"op": "seat", "key": "seat2", "kwargs": {"lens": "Lens_L", "frame_type": "acetate"}},
        {"op": "arrays", "key": "arrays1", "objects": ["Lens_L"], "npz": str(W / "normals-a1.npz")},
        {"op": "check", "key": "check", "kwargs": {}},
        {"op": "export", "key": "export", "glb": str(W / "normals-seated.glb")}])
    rng = [list(SF.browline_scene()["range"])]
    jobs["browline_hide"] = (scenes["browline"], [
        {"op": "digest", "key": "digest0", "objects": ["Lens_L", "Eyewire"]},
        {"op": "check", "key": "check_before", "kwargs": {"frame_type": "browline", "hidden_x_range_mm": rng}},
        {"op": "hide", "key": "hide", "kwargs": {"eyewire": "Eyewire", "under": "Brow", "hidden_x_range_mm": rng}},
        {"op": "hide", "key": "hide2", "kwargs": {"eyewire": "Eyewire", "under": "Brow", "hidden_x_range_mm": rng}},
        {"op": "seat", "key": "seat1", "kwargs": {"lens": "Lens_L", "frame_type": "browline"}},
        {"op": "seat", "key": "seat2", "kwargs": {"lens": "Lens_L", "frame_type": "browline"}},
        {"op": "check", "key": "check", "kwargs": {}},
        {"op": "export", "key": "export", "glb": str(W / "browline-seated.glb")},
        {"op": "unhide", "key": "unhide", "eyewire": "Eyewire"},
        {"op": "digest", "key": "digest1", "objects": ["Lens_L", "Eyewire"]}])
    pr = SF.browline_pair_scene()["ranges"]
    rL, rR = [list(pr["L"])], [list(pr["R"])]
    both = ["Lens_L", "Lens_R", "Eyewire_L", "Eyewire_R"]
    jobs["browline_pair"] = (scenes["browline_pair"], [
        {"op": "digest", "key": "digest0", "objects": both},
        {"op": "hide", "key": "hideL", "kwargs": {"eyewire": "Eyewire_L", "under": "Brow_L", "hidden_x_range_mm": rL}},
        {"op": "hide", "key": "hideR", "kwargs": {"eyewire": "Eyewire_R", "under": "Brow_R", "hidden_x_range_mm": rR}},
        {"op": "seat", "key": "seatL", "kwargs": {"lens": "Lens_L", "frame_type": "browline"}},
        {"op": "seat", "key": "seatR", "kwargs": {"lens": "Lens_R", "frame_type": "browline"}},
        {"op": "hide", "key": "hideL_sub", "kwargs": {"eyewire": "Eyewire_L", "under": "Brow_L",
                                                      "hidden_x_range_mm": [[rL[0][0] + 5.0, rL[0][1]]]}},
        {"op": "hide", "key": "hideR_wrong_lens", "kwargs": {"eyewire": "Eyewire_R", "under": "Brow_R",
                                                             "hidden_x_range_mm": rR, "lens": "Lens_L"}},
        {"op": "hide", "key": "hideR_eyewire_lens", "kwargs": {"eyewire": "Eyewire_R", "under": "Brow_R",
                                                               "hidden_x_range_mm": rR, "lens": "Eyewire_L"}},
        {"op": "check", "key": "check_seated", "kwargs": {}},
        {"op": "digest", "key": "digest_seated", "objects": both},
        # review fix round 2: a lens= that is not the eyewire's recorded held lens is refused, nothing changed
        {"op": "unhide", "key": "unhideL_wrong_lens", "eyewire": "Eyewire_L", "lens": "Lens_R"},
        {"op": "digest", "key": "digest_wrong_lens", "objects": both},
        {"op": "unhide", "key": "unhideL", "eyewire": "Eyewire_L"},
        {"op": "digest", "key": "digest_unhidden", "objects": both},
        # ... and so is any lens= on an eyewire with no hide backup (Eyewire_L is restored now)
        {"op": "unhide", "key": "unhideL_again_lensR", "eyewire": "Eyewire_L", "lens": "Lens_R"},
        {"op": "unhide", "key": "unhideL_again", "eyewire": "Eyewire_L"},
        {"op": "digest", "key": "digest_again", "objects": both},
        {"op": "check", "key": "check_right", "kwargs": {"lenses": "Lens_R"}}])
    # review fix round 2: seat BEFORE hide, then unhide keeps the seat (and unseat waits for the unhide)
    jobs["browline_seat_first"] = (scenes["browline"], [
        {"op": "digest", "key": "digest0", "objects": ["Lens_L", "Eyewire"]},
        {"op": "seat", "key": "seat", "kwargs": {"lens": "Lens_L", "frame_type": "metal", "capture_mm": 0.7}},
        {"op": "digest", "key": "digest_seated", "objects": ["Lens_L", "Eyewire"]},
        {"op": "hide", "key": "hide", "kwargs": {"eyewire": "Eyewire", "under": "Brow", "hidden_x_range_mm": rng}},
        {"op": "digest", "key": "digest_hidden", "objects": ["Lens_L", "Eyewire"]},
        {"op": "unseat", "key": "unseat_refused", "lens": "Lens_L"},
        {"op": "digest", "key": "digest_after_refusal", "objects": ["Lens_L", "Eyewire"]},
        {"op": "unhide", "key": "unhide", "eyewire": "Eyewire"},
        {"op": "digest", "key": "digest_unhidden", "objects": ["Lens_L", "Eyewire"]},
        {"op": "unseat", "key": "unseat", "lens": "Lens_L"},
        {"op": "digest", "key": "digest_unseated", "objects": ["Lens_L", "Eyewire"]}])
    jobs["crystal_seat"] = (scenes["crystal"], [
        {"op": "digest", "key": "digest0", "objects": ["Lens_L", "Crystal rim"]},
        {"op": "seat", "key": "seat1", "kwargs": {"lens": "Lens_L", "frame_type": "acetate"}},
        {"op": "seat", "key": "seat2", "kwargs": {"lens": "Lens_L", "frame_type": "acetate"}},
        {"op": "digest", "key": "digest1", "objects": ["Lens_L", "Crystal rim"]},
        {"op": "export", "key": "export", "glb": str(W / "crystal-seated.glb")},
        {"op": "unseat", "key": "unseat", "lens": "Lens_L"},
        {"op": "digest", "key": "digest2", "objects": ["Lens_L", "Crystal rim"]}])
    for sc in ("nylor", "nylor_nocord", "nylor_thin", "nylor_outline"):
        pre = []
        if sc == "nylor_nocord":
            # review fix round 2: no frame part holds the lens -> E_NOT_HELD (it was 'seated', rim sharpened)
            pre = [{"op": "digest", "key": "digest0", "objects": ["Lens_L"]},
                   {"op": "seat", "key": "unheld", "kwargs": {"lens": "Lens_L", "frame_parts": [],
                                                              "frame_type": "semi_rimless"}},
                   {"op": "digest", "key": "digest_unheld", "objects": ["Lens_L"]}]
        jobs[f"{sc}_seat"] = (scenes[sc], pre + [
            {"op": "seat", "key": "seat1", "kwargs": {"lens": "Lens_L", "frame_type": "semi_rimless",
                                                     "lower_edge": "nylor"}},
            {"op": "check", "key": "check", "kwargs": {}},
            {"op": "export", "key": "export", "glb": str(W / f"{sc}-seated.glb")},
            {"op": "digest", "key": "digest1", "objects": ["Lens_L"]}])
    # review fix round 2: the plan grows every FAIL but the check fails (TRANSITION 4/67 > 3%): partial, both checks
    jobs["short_channel_seat"] = (scenes["short_channel"], [
        {"op": "seat", "key": "seat1", "kwargs": {"lens": "Lens_L", "frame_type": "semi_rimless"}},
        {"op": "check", "key": "check", "kwargs": {}},
        {"op": "export", "key": "export", "glb": str(W / "short-channel-seated.glb")}])
    for key, src in REAL.items():
        if not src.is_file():
            continue
        steps = [{"op": "check", "key": "check", "kwargs": REAL_CHECK[key]},
                 {"op": "export", "key": "export", "glb": str(W / f"{key}.glb")}]
        if key == "invu":
            steps.insert(0, {"op": "measure_dump", "lens": REAL_LENS[key], "npz": str(W / "invu.npz"),
                             "key": "measure"})
        jobs[f"real:{key}"] = (src, steps)
    if REAL["oakley"].is_file():
        jobs["oakley_seat"] = (REAL["oakley"], [
            {"op": "digest", "key": "digest0", "objects": [REAL_LENS["oakley"]]},
            {"op": "seat", "key": "seat1", "kwargs": {"lens": REAL_LENS["oakley"], "frame_type": "semi_rimless"}},
            {"op": "digest", "key": "digest1", "objects": [REAL_LENS["oakley"]]},
            {"op": "check", "key": "check", "kwargs": {}}])
    if REAL["invu"].is_file():
        jobs["invu_seat"] = (REAL["invu"], [
            {"op": "seat", "key": "seat1", "kwargs": {"lens": REAL_LENS["invu"], "frame_type": "semi_rimless"}},
            {"op": "seat", "key": "seat2", "kwargs": {"lens": REAL_LENS["invu"], "frame_type": "semi_rimless"}},
            {"op": "check", "key": "check", "kwargs": {}}])
    if REAL["prada"].is_file():
        jobs["prada_seat"] = (REAL["prada"], [
            {"op": "seat", "key": "seat1", "kwargs": {"lens": REAL_LENS["prada"], "frame_type": "acetate"}}])
    if REAL["ft"].is_file():
        steps = []
        for side, r in (("Left", [[-56.2, -11.6]]), ("Right", [[11.6, 56.2]])):
            steps.append({"op": "hide", "key": f"hide_{side}", "kwargs": {
                "eyewire": f"Eyewire_{side}", "under": f"Brow_{side}", "hidden_x_range_mm": r}})
        for side in ("Left", "Right"):
            fp = [f"Brow_{side}", f"Eyewire_{side}", "Saddle_bridge", f"Pad_stalk_{side}", f"Nose_pad_{side}",
                  f"Pad_mount_{side}"]
            steps.append({"op": "seat", "key": f"seat_{side}", "kwargs": {
                "lens": f"Lens_{side}", "frame_parts": fp, "frame_type": "browline"}})
        steps += [{"op": "check", "key": "check", "kwargs": {}},
                  {"op": "arrays", "key": "arrays", "objects": ["Lens_Left", "Lens_Right"],
                   "npz": str(W / "ft-seated-lenses.npz")},
                  {"op": "export", "key": "export", "glb": str(W / "ft-seated.glb")}]
        jobs["ft_p2"] = (REAL["ft"], [{"op": "arrays", "key": "arrays0", "objects": ["Lens_Left", "Lens_Right"],
                                       "npz": str(W / "ft-input-lenses.npz")}] + steps)
        # review fix round 1: ranges that end under the brow (the review's refused / left-visible cases)
        fpR = ["Brow_Right", "Eyewire_Right", "Saddle_bridge", "Pad_stalk_Right", "Nose_pad_Right", "Pad_mount_Right"]
        jobs["ft_mid_brow"] = (REAL["ft"], [
            {"op": "hide", "key": "hideR", "kwargs": {"eyewire": "Eyewire_Right", "under": "Brow_Right",
                                                      "hidden_x_range_mm": [[11.6, 30.0]]}},
            {"op": "seat", "key": "seatR", "kwargs": {"lens": "Lens_Right", "frame_parts": fpR,
                                                      "frame_type": "browline"}},
            {"op": "check", "key": "checkR", "kwargs": {"lenses": "Lens_Right"}},
            {"op": "hide", "key": "hideL40", "kwargs": {"eyewire": "Eyewire_Left", "under": "Brow_Left",
                                                        "hidden_x_range_mm": [[-56.2, -40.0]]}},
            {"op": "hide", "key": "hideL_sub", "kwargs": {"eyewire": "Eyewire_Left", "under": "Brow_Left",
                                                          "hidden_x_range_mm": [[-56.2, -48.0]]}},
            {"op": "digest", "key": "digestL", "objects": ["Eyewire_Left", "Lens_Left"]}])
    return jobs


@pytest.fixture(scope="module")
def runs(workdir, scenes):
    jobs = _jobs(workdir, scenes)
    copies = workdir / "copies"
    copies.mkdir(exist_ok=True)

    def one(item):
        case, (src, steps) = item
        before = sha(src)
        safe = case.replace(":", "_")
        copy = copies / f"{safe}.blend"
        shutil.copyfile(src, copy)
        job = workdir / f"job-{safe}.json"
        out = workdir / f"out-{safe}.json"
        job.write_text(json.dumps({"steps": steps}), encoding="utf-8")
        proc = blender.run(copy, RUN, [job, out], timeout_s=600)
        res = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {
            "error": "no output", "stderr": proc.stderr[-2000:], "stdout": proc.stdout[-2000:]}
        res["_sha"] = (before, sha(src))
        return case, res

    with ThreadPoolExecutor(6) as pool:
        return dict(pool.map(one, jobs.items()))


def result(runs, case: str, key: str):
    if case not in runs:
        pytest.skip(f"{case}: input not available")
    run = runs[case]
    assert not run.get("error"), (run.get("error"), run.get("traceback", run.get("stderr", ""))[-3000:])
    hits = [r for r in run["results"] if r["key"] == key]
    assert hits, (case, key, [r["key"] for r in run["results"]])
    return hits[0]["result"]


# ------------------------------------------------------------------------------------------------- P1 helpers
def _p1(npz: Path) -> dict:
    """Re-measure Blender's frames and arrays with numpy and open3d; max |difference| per quantity."""
    z = np.load(npz)
    p = "b0_"
    n = int(z[p + "nparts"])
    parts = [(z[p + f"part{k}_V"], z[p + f"part{k}_T"]) for k in range(n)] + [(z[p + "body_V"], z[p + "body_T"])]
    fr = stations.frames_from_arrays(z[p + "P"], z[p + "Pb"], z[p + "n"], z[p + "t"], z[p + "u"], z[p + "s"])
    out = {"points": int(len(fr))}
    for name, be in (("numpy", NumpyBackend(parts)), ("open3d", Open3DBackend(parts))):
        m = stations.measure(fr, be, n)
        d = {}
        for k, a, b in (("cap", m.cap, z[p + "cap"]), ("room", m.room, z[p + "room"]),
                        ("cover_front", m.cover_front, z[p + "cover_front"]),
                        ("cover_back", m.cover_back, z[p + "cover_back"]), ("edge", m.edge, z[p + "edge"])):
            fa, fb = np.isfinite(a), np.isfinite(b)
            both = fa & fb
            d[k] = (int((fa != fb).sum()), float(np.abs(a[both] - b[both]).max()) if both.any() else 0.0)
        d["classes_equal"] = bool((np.asarray(m.cls, str) == z[p + "cls"]).all())
        d["owners_equal"] = bool((m.owner == z[p + "owner"]).all())
        d["recast"] = (int((m.recast > 0).sum()), int((z[p + "recast"] > 0).sum()))
        out[name] = d
    return out


def _assert_p1(r: dict):
    for name in ("numpy", "open3d"):
        d = r[name]
        for k in ("cap", "room", "cover_front", "cover_back", "edge"):
            assert d[k][0] == 0 and d[k][1] <= P1_TOL_MM, (name, k, d[k])
        assert d["classes_equal"] and d["owners_equal"], (name, d)
        assert d["recast"] == (0, 0), (name, d["recast"])


def _compare_checks(bl: dict, host: dict) -> list:
    """Per lens: (stations, verdict mismatches, max |capture difference|, max station distance)."""
    assert "error" not in host, host
    out = []
    for L in bl["lenses"]:
        H = next((h for h in host["lenses"] if h["lens"] == L["lens"]), None)
        assert H is not None, (L["lens"], [h["lens"] for h in host["lenses"]])
        A = np.array([r["xyz"] for r in L["stations"]])
        B = np.array([r["xyz"] for r in H["stations"]])
        assert len(A) == len(B), (L["lens"], len(A), len(B))
        d2 = np.linalg.norm(A[:, None] - B[None], axis=2)
        j = d2.argmin(1)
        dist = float(d2[np.arange(len(A)), j].max())
        bad_v = sum(L["stations"][i]["verdict"] != H["stations"][j[i]]["verdict"] for i in range(len(A)))
        cmax = 0.0
        for i in range(len(A)):
            ca = np.array([np.nan if c is None else c for c in L["stations"][i]["cap"]], float)
            cb = np.array([np.nan if c is None else c for c in H["stations"][j[i]]["cap"]], float)
            if (np.isfinite(ca) != np.isfinite(cb)).any():
                cmax = np.inf
                break
            ok = np.isfinite(ca)
            if ok.any():
                cmax = max(cmax, float(np.abs(ca[ok] - cb[ok]).max()))
        out.append({"lens": L["lens"], "stations": len(A), "verdict_mismatch": int(bad_v), "cap_max": cmax,
                    "dist_max": dist, "pass": (L["pass"], H["pass"]),
                    "frame_type": (L["frame_type"], H["frame_type"])})
    return out


def _assert_checks_agree(bl, glb, **kw):
    host = check_glb(glb, stations_table=True, **kw)
    assert bl.get("ok") is not None, bl
    assert bl["ok"] == host["ok"], (bl.get("reasons"), host.get("reasons"))
    for a, b in zip(sorted(bl.get("lenses", []), key=lambda L: L["lens"]),
                    sorted(host.get("lenses", []), key=lambda L: L["lens"])):
        ca, cb = a.get("cord_lens_overlap_mm3"), b.get("cord_lens_overlap_mm3")
        assert (ca is None) == (cb is None), (ca, cb)
        if ca is not None:                                 # BVH vs open3d columns: the same overlap
            assert abs(ca - cb) <= max(1e-3, 0.01 * max(ca, cb)), (a["cord"], b["cord"])
    for c in _compare_checks(bl, host):
        assert c["verdict_mismatch"] == 0 and c["cap_max"] <= CHECK_CAP_TOL_MM and c["dist_max"] <= 2e-3, c
        assert c["pass"][0] == c["pass"][1] and c["frame_type"][0] == c["frame_type"][1], c
    return host


# --------------------------------------------------------------------------------------------------------- P1
@pytest.mark.parametrize("scene", sorted(FIXTURE_FT))
def test_p1_bvh_numpy_open3d_agree_on_fixtures(runs, workdir, scene):
    for ln in lenses_of(scene):
        result(runs, f"p1:{scene}", f"measure:{ln}")
        _assert_p1(_p1(workdir / f"{scene}-{ln}.npz"))


def test_p1_bvh_numpy_open3d_agree_on_invu(runs, workdir):
    result(runs, "real:invu", "measure")
    r = _p1(workdir / "invu.npz")
    assert r["points"] > 1400
    _assert_p1(r)


@pytest.mark.parametrize("scene", sorted(FIXTURE_FT))
def test_p1_blender_check_matches_host_check_on_its_glb(runs, workdir, scene):
    bl = result(runs, f"p1:{scene}", "check")
    exp = result(runs, f"p1:{scene}", "export")
    assert exp["status"] == "exported", exp
    _assert_checks_agree(bl, workdir / f"{scene}.glb", **fixture_check_kwargs(scene))


@pytest.mark.parametrize("key", sorted(REAL))
def test_p1_blender_check_matches_host_check_on_real_scenes(runs, workdir, key):
    bl = result(runs, f"real:{key}", "check")
    exp = result(runs, f"real:{key}", "export")
    assert exp["status"] == "exported", exp
    _assert_checks_agree(bl, workdir / f"{key}.glb", **REAL_CHECK[key])


@pytest.mark.parametrize("case, glb", [("metal_seat", "metal-seated.glb"), ("pair_seat", "pair-seated.glb"),
                                       ("browline_hide", "browline-seated.glb"), ("ft_p2", "ft-seated.glb"),
                                       ("normals_seat", "normals-seated.glb")])
def test_p1_checks_agree_after_seating_with_the_declared_extras(runs, workdir, case, glb):
    """After seat_lens the exporter carries g3d_seat (and the hidden ranges), so both checks read the declared
    frame type with no argument."""
    bl = result(runs, case, "check")
    assert bl["ok"], bl["reasons"]
    host = _assert_checks_agree(bl, workdir / glb)
    assert all(L["frame_type_source"] == "g3d_seat" for L in host["lenses"])
    if case == "ft_p2":                                   # the fixtures are single parts, not whole glasses
        assert validate_spec(workdir / glb)["ok"]


@pytest.mark.parametrize("case, first, second", [("metal_seat", "seat1", "seat2"), ("pair_seat", "seatL", "seatL2"),
                                                 ("pair_seat", "seatR", "seatR2"),
                                                 ("normals_seat", "seat1", "seat2"),
                                                 ("browline_hide", "seat1", "seat2"),
                                                 ("invu_seat", "seat1", "seat2")])
def test_p1_a_second_seat_call_moves_nothing(runs, case, first, second):
    r1 = result(runs, case, first)
    r2 = result(runs, case, second)
    assert r1["status"] == "seated" and r1["moved_vertices"] > 0, r1
    assert r1["verify"]["second_call"] == {"status": "already_seated", "moved_vertices": 0, "code": None}
    assert r2["status"] == "already_seated" and r2["moved_vertices"] == 0, r2


# --------------------------------------------------------------------------------------------- tool contract
def test_write_back_through_rotated_and_mirrored_objects_is_exact(runs, workdir):
    """pair_transformed: both lens objects carry rotations (Lens_R also a -1 x scale) and the scene's bridge marker
    is offset. Only the band rows move; every other stored float32 coordinate is bit-identical; the Blender check
    passes on the result."""
    for k in ("seatL", "seatR"):
        r = result(runs, "pair_seat", k)
        assert r["status"] == "seated", r
    a0, a1 = np.load(workdir / "pair-a0.npz"), np.load(workdir / "pair-a1.npz")
    for i, key in enumerate(("seatL", "seatR")):
        co0, co1 = a0[f"o{i}_co"], a1[f"o{i}_co"]
        moved = np.any(co0 != co1, axis=1)
        nodes = result(runs, "pair_seat", key)["bodies"][0]["nodes_moved"]
        assert moved.sum() == 2 * nodes, (moved.sum(), nodes)              # front rim + rung partner, no duplicates
        assert np.array_equal(a0[f"o{i}_T"], a1[f"o{i}_T"])
    assert result(runs, "pair_seat", "check")["ok"]


def test_custom_normals_unmoved_corners_stay_within_0p1_deg(runs):
    r = result(runs, "normals_seat", "seat1")
    assert r["status"] == "seated", r
    nrm = r["normals"]
    assert nrm["moved_corners"] > 0 and nrm["unmoved_max_change_deg"] <= 0.1, nrm


def test_custom_normals_rim_is_split_and_the_checks_agree(runs, workdir):
    """Review fix round 2 (major): the custom-normals lens was marked sharpened while its corners kept the smooth
    rim normal (GLB rim split p5 38.5 deg), so the check failed what the tool called seated. The sharpened rim's
    corners are now set to the cap's own normal and the wall's face normal; g3d_seat claims 'sharpened' only with
    the split measured as the check does; the Blender check and the host check on the GLB both pass, and the
    unmoved corners off the rim keep their normals within 0.1 deg (stored normals compared here, not the report)."""
    r = result(runs, "normals_seat", "seat1")
    assert r["g3d_seat"]["sharpened"] is True and r["rim_split"]["ok"], r.get("rim_split")
    assert r["rim_split"]["front_p5_min"] >= 75, r["rim_split"]
    assert r["normals"]["split_rim_corners"] > 0
    assert not any(w.startswith("W_RIM_SPLIT_LOW") for w in r["warnings"])
    chk = result(runs, "normals_seat", "check")
    assert chk["ok"], chk["reasons"]
    assert chk["lenses"][0]["rim_split_deg"]["front"]["p5"] >= 75, chk["lenses"][0]["rim_split_deg"]
    exp = result(runs, "normals_seat", "export")
    assert exp["status"] == "exported", exp
    host = check_glb(workdir / "normals-seated.glb")
    assert host["ok"] and host["lenses"][0]["rim_split_deg"]["front"]["p5"] >= 75, (host["reasons"],
                                                                                 host["lenses"][0]["rim_split_deg"])
    # stored corner normals: unmoved corners that are not on the outer rim loops keep theirs within 0.1 deg
    a0, a1 = np.load(workdir / "normals-a0.npz"), np.load(workdir / "normals-a1.npz")
    moved = np.any(a0["o0_co"] != a1["o0_co"], axis=1)
    body = stations.recognise(a0["o0_V"], a0["o0_T"], "L").bodies[0]
    _, vid = weld_exact(a0["o0_V"])
    rim = np.isin(vid, body.source_verts[np.r_[body.fl, body.bl]])
    lv = a0["o0_loop_v"]
    keep = ~moved[lv] & ~rim[lv]
    n0, n1 = a0["o0_N"][keep], a1["o0_N"][keep]
    ang = np.degrees(np.arccos(np.clip((n0 * n1).sum(1), -1, 1)))
    assert keep.sum() > 1000 and ang.max() <= 0.1, (int(keep.sum()), float(ang.max()))
    assert result(runs, "normals_seat", "seat2")["status"] == "already_seated"


def test_dry_run_plans_and_writes_nothing(runs):
    r = result(runs, "metal_seat", "dry")
    # 132 since review fix round 2 (130 before): a halfway point at the nasal run end now grows its passing end
    # node too (plan.halfway_growth), the same on the lens and its mirror
    assert r["dry_run"] and r["status"] == "seated" and r["moved_vertices"] == 132, r
    assert result(runs, "metal_seat", "digest_dry")["Lens_L"] == result(runs, "metal_seat", "digest0")["Lens_L"]


def test_refusals_name_their_cause_and_change_nothing(runs):
    codes = {k: result(runs, "refusals", k)["code"] for k in
             ("no_type", "bad_type", "no_object", "uv", "browline_no_ranges", "shared", "modifier", "hide_no_ranges")}
    assert codes == {"no_type": "E_ARGS", "bad_type": "E_ARGS", "no_object": "E_ARGS", "uv": "E_ARGS",
                     "browline_no_ranges": "E_NO_RANGES", "shared": "E_SHARED_MESH", "modifier": "E_LENS_MODIFIERS",
                     "hide_no_ranges": "E_NO_RANGES"}, codes
    d = result(runs, "refusals", "digest")
    assert "g3d_seat" not in d["Lens_L"]["props"] and d["Lens_L"]["modifiers"] == ["Bevel"]
    assert not any("g3d" in m for m in d["_meshes"])


def test_unseat_restores_the_mesh_exactly(runs):
    d0 = result(runs, "metal_seat", "digest0")
    d1 = result(runs, "metal_seat", "digest1")
    d2 = result(runs, "metal_seat", "digest2")
    assert d1["Lens_L"]["digest"] != d0["Lens_L"]["digest"]
    assert d2["Lens_L"]["digest"] == d0["Lens_L"]["digest"]
    assert d2["Eyewire"]["digest"] == d0["Eyewire"]["digest"] == d1["Eyewire"]["digest"]
    assert "g3d_seat" in d1["Lens_L"]["props"] and "g3d_seat" not in d2["Lens_L"]["props"]
    assert not any("g3d pre-seat" in m for m in d2["_meshes"])
    assert result(runs, "metal_seat", "unseat")["status"] == "restored"


def test_hide_eyewire_then_seat_on_the_browline_fixture(runs, workdir):
    before = result(runs, "browline_hide", "check_before")
    assert not before["ok"] and any("band" in x for x in before["lenses"][0]["reasons"])
    h = result(runs, "browline_hide", "hide")
    assert h["status"] == "lifted", h
    assert h["lambda_p50_max_mm"][1] == pytest.approx(SF.browline_scene()["lift_mm"], abs=0.006)
    assert h["band_after"]["front_max_mm"] == 0 and h["band_after"]["back_max_mm"] == 0
    assert h["section_area_rel_max"] <= 0.01 and h["slope_max"] <= 0.5
    assert h["second_call"]["status"] == "already_hidden"
    assert result(runs, "browline_hide", "hide2")["status"] == "already_hidden"
    s = result(runs, "browline_hide", "seat1")
    assert s["status"] == "seated" and s["g3d_seat"]["ranges"], s
    chk = result(runs, "browline_hide", "check")
    assert chk["ok"], chk["reasons"]
    lens = chk["lenses"][0]
    assert lens["band"]["graded"]["ok"] and lens["frame_type_source"] == "g3d_seat"
    un = result(runs, "browline_hide", "unhide")
    assert un["status"] == "restored" and un["lenses"] == ["Lens_L"], un
    d0 = result(runs, "browline_hide", "digest0")
    d1 = result(runs, "browline_hide", "digest1")
    for ob in ("Lens_L", "Eyewire"):
        assert d1[ob]["digest"] == d0[ob]["digest"], ob
        assert "g3d_hidden_x_ranges_mm" not in d1[ob]["props"] and "g3d_seat" not in d1[ob]["props"]


def test_crystal_v_bevel_and_groove(runs, workdir):
    """§5 on the crystal fixture: 120 deg V-bevel (h 0.404 on a 1.6 mm edge), live EXACT Boolean groove; the
    removed volume equals cutter ^ frame within 1%, lens ^ frame <= 0.01 mm3, groove gap 0.02-0.06 mm, frame closed
    with its Euler characteristic; a second call is refused (already profiled); unseat removes the cutter and the
    modifier and restores the lens. Review fix round 1: the original vertices are verified after the re-wall
    (stored = planned, unmoved bit-identical) and the binding check grades the profiled lens; it FAILS the grooved
    crystal lens (open item B: the 0.03 mm clearance reads as a gap under §3's capture), so the tool says partial
    with W_CHECK_FAILS, never seated."""
    r = result(runs, "crystal_seat", "seat1")
    assert r["status"] == "partial" and r["profile"] == "vbevel", r
    assert r["check"]["pass"] is False and any(w.startswith("W_CHECK_FAILS") for w in r["warnings"]), r["check"]
    assert r["g3d_seat"]["status"] == "partial"
    assert r["verify"]["failures"] == [] and r["verify"]["stored_vs_planned_max_mm"] <= 1e-9, r["verify"]
    assert r["translucent"] == {"value": True, "auto": True, "capturing": ["Crystal rim"]}
    assert r["profile_plan"]["h_mm"] == pytest.approx(0.404, abs=0.005)
    pr = r["profile_result"]
    assert pr["status"] == "ok" and pr["lens_topology"]["closed"] and pr["lens_topology"]["euler"] == 2
    att = pr["parts"][0]["attempts"][-1]
    assert att["solver"] == "EXACT" and att["closed"] and att["euler"][0] == att["euler"][1]
    assert abs(att["removed_mm3"] - pr["parts"][0]["expected_removed_mm3"]) <= 0.01 * pr["parts"][0]["expected_removed_mm3"]
    assert att["lens_overlap_mm3"] <= 0.01 and att["gap_ok"] and att["frame_left_ok"]
    assert 0.02 <= att["gap_min_p50_max_mm"][0] and att["gap_min_p50_max_mm"][2] <= 0.06
    r2 = result(runs, "crystal_seat", "seat2")
    assert r2["status"] == "refused" and r2["code"] == "E_ALREADY_PROFILED"
    d0, d1, d2 = (result(runs, "crystal_seat", k) for k in ("digest0", "digest1", "digest2"))
    assert d1["Crystal rim"]["modifiers"] == ["g3d_groove"] and "g3d_groove_cutter | Lens_L" in d1["_objects"]
    assert d2["Crystal rim"]["modifiers"] == [] and "g3d_groove_cutter | Lens_L" not in d2["_objects"]
    assert d2["Lens_L"]["digest"] == d0["Lens_L"]["digest"]
    assert d2["Crystal rim"]["digest"] == d0["Crystal rim"]["digest"]


def test_nylor_groove_cord_and_thickness(runs, workdir):
    """§5 nylor: the existing cord (seated in the §5 groove frame) is kept and its overlap with the grooved lens is
    measured by the tool (EXACT Boolean) and reported by both checks (station-plane columns); a created cord too."""
    r = result(runs, "nylor_seat", "seat1")
    pr = r["profile_result"]
    assert r["status"] == "seated" and pr["cord"] == "existing", r
    assert [c["object"] for c in pr["cord_parts"]] == ["Cord"] and pr["cord_lens_overlap_mm3"] <= 0.01, pr
    assert not any(w.startswith("W_CORD_OVERLAP") for w in r["warnings"])
    assert r["check"]["pass"] is True and r["verify"]["failures"] == [], (r["check"], r["verify"])
    assert pr["lens_topology"]["closed"] and pr["lens_topology"]["euler"] == 2
    chk = result(runs, "nylor_seat", "check")
    assert chk["ok"]
    L = chk["lenses"][0]
    assert [p["part"] for p in L["cord"]["parts"]] == ["Cord"] and L["cord_lens_overlap_mm3"] <= 0.01, L["cord"]
    host = check_glb(workdir / "nylor-seated.glb")
    assert host["ok"] and abs(host["lenses"][0]["cord_lens_overlap_mm3"] - L["cord_lens_overlap_mm3"]) <= 1e-3
    r = result(runs, "nylor_nocord_seat", "seat1")
    pr = r["profile_result"]
    assert r["status"] == "seated" and pr["cord"] == "Lens_L | nylor cord" and pr["arcs"] == 1, r
    assert pr["cord_lens_overlap_mm3"] <= 0.01 and pr["channel"] == "Channel"
    chk = result(runs, "nylor_nocord_seat", "check")
    assert chk["ok"] and chk["lenses"][0]["cord"]["parts"][0]["part"].startswith("Lens_L | nylor cord"), chk
    r = result(runs, "nylor_thin_seat", "seat1")
    assert r["status"] == "refused" and r["code"] == "E_THICKNESS", r


def test_nylor_existing_cord_off_the_groove_is_reported(runs, workdir):
    """Proof review round 2 (major): the stage-proof cord (horizontal outline normal, 0.30 mm inside) overlaps the
    grooved lens by 0.74 mm3, 74 x the §10 limit, and seat_lens said 'seated' and the check passed. Now the tool
    measures it (EXACT Boolean; the reviewer's probe read 0.7393) and says partial with W_CORD_OVERLAP, and both
    checks fail the lens on the overlap (the cord is never moved, §5)."""
    r = result(runs, "nylor_outline_seat", "seat1")
    pr = r["profile_result"]
    assert pr["cord"] == "existing" and [c["object"] for c in pr["cord_parts"]] == ["Cord"], pr
    assert pr["cord_lens_overlap_mm3"] == pytest.approx(0.7393, abs=0.01), pr
    assert r["status"] == "partial" and r["g3d_seat"]["status"] == "partial", r
    assert any(w.startswith("W_CORD_OVERLAP") for w in r["warnings"]), r["warnings"]
    assert r["check"]["pass"] is False and any("cord-lens overlap" in x for x in r["check"]["reasons"]), r["check"]
    chk = result(runs, "nylor_outline_seat", "check")
    L = chk["lenses"][0]
    assert not chk["ok"] and L["cord_lens_overlap_mm3"] == pytest.approx(0.7393, abs=0.02), L["cord"]
    host = check_glb(workdir / "nylor_outline-seated.glb")
    assert not host["ok"] and host["lenses"][0]["cord_lens_overlap_mm3"] == pytest.approx(0.7393, abs=0.02)
    assert any("cord-lens overlap" in x for x in host["lenses"][0]["reasons"])


def test_unhide_restores_one_side_only(runs):
    """Review fix round 1: two eyewires, two lenses, both hidden and seated; unhide_eyewire('Eyewire_L') restores
    Eyewire_L and the lens it recorded (Lens_L) and nothing else: Eyewire_R and Lens_R keep their geometry, g3d_seat,
    hidden ranges and backups, and Lens_R still passes the check."""
    for k in ("hideL", "hideR"):
        assert result(runs, "browline_pair", k)["status"] == "lifted", result(runs, "browline_pair", k)
    for k in ("seatL", "seatR"):
        assert result(runs, "browline_pair", k)["status"] == "seated", result(runs, "browline_pair", k)
    assert result(runs, "browline_pair", "check_seated")["ok"]
    seated = result(runs, "browline_pair", "digest_seated")
    assert json.loads(seated["Eyewire_L"]["props"]["g3d_backups"])["held_lens"] == "Lens_L"
    assert json.loads(seated["Eyewire_R"]["props"]["g3d_backups"])["held_lens"] == "Lens_R"
    un = result(runs, "browline_pair", "unhideL")
    assert un["status"] == "restored" and un["eyewire"] == "Eyewire_L" and un["eyewire_restored"] is True, un
    assert un["lenses"] == ["Lens_L"], un
    d0 = result(runs, "browline_pair", "digest0")
    d2 = result(runs, "browline_pair", "digest_unhidden")
    for ob in ("Lens_L", "Eyewire_L"):
        assert d2[ob]["digest"] == d0[ob]["digest"], ob
        assert not ({"g3d_seat", "g3d_hidden_x_ranges_mm", "g3d_backups"} & set(d2[ob]["props"])), d2[ob]["props"]
    for ob in ("Lens_R", "Eyewire_R"):
        assert d2[ob]["digest"] == seated[ob]["digest"] != d0[ob]["digest"], ob
        assert d2[ob]["props"] == seated[ob]["props"], ob
    assert "g3d_seat" in d2["Lens_R"]["props"]
    assert result(runs, "browline_pair", "check_right")["ok"]


def test_unhide_refuses_a_lens_it_did_not_lift(runs):
    """Review fix round 2 (major): unhide_eyewire('Eyewire_L', lens='Lens_R') returned 'restored' and reverted
    Lens_R (leaving Lens_L lifted and seated); on an eyewire with no hide backup it reverted the lens it was given.
    Both are refused now (E_ARGS) and change nothing; unhide without lens= then works as before."""
    r = result(runs, "browline_pair", "unhideL_wrong_lens")
    assert r["status"] == "refused" and r["code"] == "E_ARGS" and "'Lens_L'" in r["message"], r
    seated = result(runs, "browline_pair", "digest_seated")
    after = result(runs, "browline_pair", "digest_wrong_lens")
    for ob in ("Lens_L", "Lens_R", "Eyewire_L", "Eyewire_R"):
        assert after[ob] == seated[ob], ob
    assert after["_meshes"] == seated["_meshes"]
    r = result(runs, "browline_pair", "unhideL_again_lensR")
    assert r["status"] == "refused" and r["code"] == "E_ARGS" and "no hide_eyewire backup" in r["message"], r
    r = result(runs, "browline_pair", "unhideL_again")
    assert r["status"] == "nothing_to_restore" and r["lenses"] == [], r
    unhidden = result(runs, "browline_pair", "digest_unhidden")
    again = result(runs, "browline_pair", "digest_again")
    for ob in ("Lens_L", "Lens_R", "Eyewire_L", "Eyewire_R"):
        assert again[ob] == unhidden[ob], ob


def test_seat_before_hide_then_unhide_keeps_the_seat(runs):
    """Review fix round 2 (minor): seat_lens (metal, capture_mm 0.7: every node grows), THEN hide_eyewire, then
    unhide_eyewire restored the lens to its seated mesh but dropped its pre-seat backup and g3d_seat, so the seat
    could never be undone. Now the backups keep their order: unseat_lens before the unhide is refused (E_ORDER: the
    pre-seat mesh predates the lift), unhide keeps the seat (mesh, g3d_seat, backup), and unseat_lens afterwards
    restores the original lens exactly."""
    s = result(runs, "browline_seat_first", "seat")
    assert s["status"] == "seated" and s["moved_vertices"] == 256, s
    h = result(runs, "browline_seat_first", "hide")
    assert h["status"] == "lifted", h
    d0, ds, dh = (result(runs, "browline_seat_first", k) for k in ("digest0", "digest_seated", "digest_hidden"))
    assert dh["Lens_L"]["digest"] != ds["Lens_L"]["digest"]
    u = result(runs, "browline_seat_first", "unseat_refused")
    assert u["status"] == "refused" and u["code"] == "E_ORDER" and "unhide_eyewire('Eyewire')" in u["message"], u
    assert result(runs, "browline_seat_first", "digest_after_refusal") == dh
    un = result(runs, "browline_seat_first", "unhide")
    assert un["status"] == "restored" and un["lenses"] == ["Lens_L"] and un.get("seat_kept") is True, un
    du = result(runs, "browline_seat_first", "digest_unhidden")
    for ob in ("Lens_L", "Eyewire"):
        assert du[ob]["digest"] == ds[ob]["digest"], ob
        assert "g3d_hidden_x_ranges_mm" not in du[ob]["props"]
    assert du["Lens_L"]["props"]["g3d_seat"] == ds["Lens_L"]["props"]["g3d_seat"]
    assert json.loads(du["Lens_L"]["props"]["g3d_backups"])["seat"] == "Lens_L | g3d pre-seat"
    assert "g3d_backups" not in du["Eyewire"]["props"]
    un2 = result(runs, "browline_seat_first", "unseat")
    assert un2["status"] == "restored", un2
    d2 = result(runs, "browline_seat_first", "digest_unseated")
    for ob in ("Lens_L", "Eyewire"):
        assert d2[ob]["digest"] == d0[ob]["digest"], ob
        assert not ({"g3d_seat", "g3d_backups", "g3d_hidden_x_ranges_mm"} & set(d2[ob]["props"])), d2[ob]["props"]
    assert not any("g3d pre-" in m for m in d2["_meshes"])


def test_an_unheld_lens_is_refused(runs):
    """Review fix round 2 (major): seat_lens(Lens_L, frame_parts=[], 'semi_rimless') returned seated (rim
    sharpened, g3d_seat written) while the check failed ('no required station'). Now E_NOT_HELD, nothing written."""
    r = result(runs, "nylor_nocord_seat", "unheld")
    assert r["status"] == "refused" and r["code"] == "E_NOT_HELD" and "nothing holds this lens" in r["message"], r
    assert result(runs, "nylor_nocord_seat", "digest_unheld")["Lens_L"] ==         result(runs, "nylor_nocord_seat", "digest0")["Lens_L"]


def test_seat_lens_is_partial_where_the_check_fails(runs, workdir):
    """Review fix round 2 (major): a channel over the top 20% only. The plan grows every FAIL (it alone said
    seated), but the channel ends leave 4 of 67 required stations TRANSITION (> 3%): seat_lens now says partial with
    the check's reason, g3d_seat says partial, and the Blender check and the host check on the GLB fail the same
    way."""
    r = result(runs, "short_channel_seat", "seat1")
    assert r["status"] == "partial" and r["moved_vertices"] > 0 and r["g3d_seat"]["status"] == "partial", r
    assert r["bodies"][0]["check"]["reasons"] == ["TRANSITION 4/67 > 3%"], r["bodies"][0]["check"]
    assert any(w.startswith("W_CHECK_FAILS") and "TRANSITION 4/67" in w for w in r["warnings"]), r["warnings"]
    bl = result(runs, "short_channel_seat", "check")
    assert not bl["ok"] and bl["lenses"][0]["reasons"] == ["TRANSITION 4/67 > 3%"], bl["lenses"][0]["reasons"]
    assert result(runs, "short_channel_seat", "export")["status"] == "exported"
    host = _assert_checks_agree(bl, workdir / "short-channel-seated.glb")
    assert host["lenses"][0]["reasons"] == ["TRANSITION 4/67 > 3%"]


def test_ft_p2_left_equals_mirrored_right(runs, workdir):
    """§10 P2 'left = mirrored right within 0.01 mm' (review fix round 2, major): the stage-1 ownership of halfway
    points made the seated q001 lenses differ by up to 0.054 mm. Vertices are paired by the mirrored input."""
    result(runs, "ft_p2", "arrays0")
    result(runs, "ft_p2", "arrays")
    z0, z1 = np.load(workdir / "ft-input-lenses.npz"), np.load(workdir / "ft-seated-lenses.npz")
    L0, R0 = z0["o0_V"] * [-1.0, 1.0, 1.0], z0["o1_V"]
    j = np.array([np.linalg.norm(R0 - p, axis=1).argmin() for p in L0])
    assert np.linalg.norm(L0 - R0[j], axis=1).max() < 1e-3                 # the input is a mirrored pair
    d = np.linalg.norm(z1["o0_V"] * [-1.0, 1.0, 1.0] - z1["o1_V"][j], axis=1)
    assert d.max() <= 0.01, (float(d.max()), int((d > 1e-3).sum()))


def test_hide_eyewire_refuses_a_wrong_or_non_lens_held_lens(runs):
    r = result(runs, "browline_pair", "hideR_wrong_lens")
    assert r["status"] == "refused" and r["code"] == "E_ARGS" and "Lens_R" in r["message"], r
    r = result(runs, "browline_pair", "hideR_eyewire_lens")
    assert r["status"] == "refused" and r["code"] == "E_ARGS" and "partRole 'lens'" in r["message"], r


def test_a_sub_range_call_keeps_the_declared_union(runs):
    """Review fix round 1: hide over the full range, then over a sub-range (nothing moves): the declared ranges stay
    the union, so the lifted span stays declared for seat_lens and the check."""
    r = result(runs, "browline_pair", "hideL_sub")
    assert r["status"] == "already_hidden", r
    full = [list(SF.browline_pair_scene()["ranges"]["L"])]
    assert r["ranges_effective"] == full
    props = result(runs, "browline_pair", "digest_seated")["Lens_L"]["props"]
    assert json.loads(props["g3d_hidden_x_ranges_mm"]) == full
    assert json.loads(result(runs, "browline_pair", "digest_seated")["Eyewire_L"]["props"]["g3d_hidden_x_ranges_mm"]) == full


def test_check_takes_single_names(runs):
    r = result(runs, "metal_seat", "check_names")
    assert r["ok"] and len(r["lenses"]) == 1 and r["lenses"][0]["parts"], r.get("reasons")
    m = result(runs, "metal_seat", "check_missing")
    assert m["ok"] is False and m["reasons"] and "Nope" in m["error"], m


def test_ft_ranges_ending_under_the_brow(runs):
    """Review fix round 1 on q001: [11.6, 30.0] (right) lifts in full (the two stations past the last selected ring
    are buried too) and the browline seat then passes the check; [-56.2, -40] (left), refused before by the
    section-area artifact (4.06%), lifts with the section within 1%; a later sub-range call moves nothing and keeps
    the declared range."""
    h = result(runs, "ft_mid_brow", "hideR")
    assert h["status"] == "lifted" and h["second_call"]["status"] == "already_hidden", h
    b = h["band_after"]
    assert b["graded_visible_points"] == 0 and b["front_max_mm"] == 0 and b["back_max_mm"] == 0, b
    s = result(runs, "ft_mid_brow", "seatR")
    assert s["status"] == "seated", s
    chk = result(runs, "ft_mid_brow", "checkR")
    assert chk["ok"], chk["reasons"]
    h = result(runs, "ft_mid_brow", "hideL40")
    assert h["status"] == "lifted" and h["section_area_rel_max"] <= 0.01, h
    h2 = result(runs, "ft_mid_brow", "hideL_sub")
    assert h2["status"] == "already_hidden" and h2["ranges_effective"] == [[-56.2, -40.0]], h2
    props = result(runs, "ft_mid_brow", "digestL")["Eyewire_Left"]["props"]
    assert json.loads(props["g3d_hidden_x_ranges_mm"]) == [[-56.2, -40.0]]


def test_oakley_final_moves_nothing(runs):
    """§10 P5 Oakley: 0 vertices moved, lens digest (positions, custom normals, sharp edges) identical, check PASS."""
    r = result(runs, "oakley_seat", "seat1")
    assert r["status"] == "already_seated" and r["moved_vertices"] == 0, r
    assert result(runs, "oakley_seat", "digest0")[REAL_LENS["oakley"]]["digest"] == \
        result(runs, "oakley_seat", "digest1")[REAL_LENS["oakley"]]["digest"]
    assert result(runs, "oakley_seat", "check")["ok"]


def test_invu_final_seats_its_top_rail(runs):
    r = result(runs, "invu_seat", "seat1")
    assert r["status"] == "seated", r
    b = r["bodies"][0]
    assert b["growth_max_mm"] <= 0.95 and r["moved_vertices"] <= 600, b
    assert result(runs, "invu_seat", "check")["ok"]


def test_ft_p2_flow_hides_the_eyewire_and_seats_the_browline(runs):
    for side in ("Left", "Right"):
        h = result(runs, "ft_p2", f"hide_{side}")
        assert h["status"] == "lifted" and h["second_call"]["status"] == "already_hidden", h
        assert h["lift_max_mm"] <= 1.1 and h["band_after"]["front_max_mm"] == 0
        s = result(runs, "ft_p2", f"seat_{side}")
        assert s["status"] == "seated" and s["moved_vertices"] <= 100, s
    chk = result(runs, "ft_p2", "check")
    assert chk["ok"], chk["reasons"]


@pytest.mark.xfail(strict=True, reason="design conflict, reported: §4 step 11's 30 deg face-rotation limit vs the "
                                       "strict per-node taper on Prada's 0.044-0.046 mm wall slivers (55-57 deg)")
def test_prada_delivery_seats(runs):
    r = result(runs, "prada_seat", "seat1")
    assert r["status"] == "seated", r


def test_source_blends_are_never_written(runs):
    for case, run in runs.items():
        before, after = run["_sha"]
        assert before == after, case


# -------------------------------------------------------------------------------------------------------- CLI
def test_cli_seat_writes_a_receipt_and_saves_only_to_a_new_path(scenes, workdir):
    src = workdir / "cli-metal.blend"
    shutil.copyfile(scenes["metal"], src)
    args = workdir / "cli-args.json"
    args.write_text(json.dumps({"lens": "Lens_L", "frame_type": "metal"}), encoding="utf-8")
    receipt = workdir / "cli-receipt.json"
    saved = workdir / "cli-saved.blend"
    glb = workdir / "cli.glb"
    before = sha(src)
    blender.run(src, CLI, ["seat", args, receipt, "--save-copy", saved, "--export", glb], timeout_s=300)
    rec = json.loads(receipt.read_text(encoding="utf-8"))
    assert rec["status"] == "ok" and rec["results"][0]["status"] == "seated", rec
    assert rec["saved_copy"]["status"] == "saved" and saved.is_file()
    assert rec["export"]["status"] == "exported" and rec["export"]["seat_extras"] == {"Lens_L": ["g3d_seat"]}
    assert sha(src) == before
    rep = check_glb(glb)
    assert rep["ok"] and rep["lenses"][0]["frame_type_source"] == "g3d_seat", rep["reasons"]
    # a second run on the saved copy: already seated; saving onto an existing path is refused
    receipt2 = workdir / "cli-receipt2.json"
    blender.run(saved, CLI, ["seat", args, receipt2, "--save-copy", saved], timeout_s=300)
    rec2 = json.loads(receipt2.read_text(encoding="utf-8"))
    assert rec2["results"][0]["status"] == "already_seated" and rec2["results"][0]["moved_vertices"] == 0
    assert rec2["saved_copy"]["status"] == "refused"
