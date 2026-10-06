"""WP4 §10 proof, stage 'proof': P2, P3, P4 and P5 exactly as WP4_DESIGN.md §10 writes them (tests only).

    .venv/Scripts/python -B -m glasses3d.tests.proofs.wp4_proof [--grade-only] [--cases ft,prada,...] [--workers 6]

Every case runs in headless Blender 5.2 on a COPY under glasses3d/data/wp4/proof/copies/ (the four Studio sources'
sha256 are recorded before and after), through tests/blender/seat_run.py: the tools (hide_eyewire, seat_lens), the
Blender check, the WP3 exporter (a GLB of the input AND of the result, both from the same exporter), raw vertex arrays
and mesh digests before and after, and the core's before/after state per node (tests/proofs/wp4_proof_bpy.py).
The fixtures are built by tests/blender/seat_build.py. Grading (tests/proofs/wp4_proof_grade.py) reads those files:
each criterion is measured by the core (the tool's report, the Blender check, the host check_glb on the exported
GLB) AND by the independent instrument on the exported GLB (tests/instruments/seat_grid.py +
seat_proof_metrics.py), or directly on the raw arrays / digests where the criterion is about vertices, and the
result goes to glasses3d/data/wp4/proof.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from glasses3d.g3d_host import blender

REPO = Path(__file__).resolve().parents[3]
G3D = REPO / "glasses3d"
OUT = G3D / "data/wp4/proof"
PROOF_JSON = G3D / "data/wp4/proof.json"
RUN = G3D / "tests/blender/seat_run.py"
BUILD = G3D / "tests/blender/seat_build.py"
JOBS = REPO / "automation/data/blender_agent/studio/jobs"
SOURCES = {
    "ft": JOBS / "3b8a1c7ae8114868b49b298b681f1b29/original/scene.blend",
    "prada": JOBS / "8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend",
    "oakley": JOBS / "3dff867466d449c9a6369273f0a14631/agent/scene.blend",
    "invu": JOBS / "c8e81c7214a646cfa07e082c59128f10/agent/scene.blend",
}
FT_RANGES = {"Left": [[-56.2, -11.6]], "Right": [[11.6, 56.2]]}          # §10 P2
FT_ALL_RANGES = [[-56.2, -11.6], [11.6, 56.2]]
PRADA = {"L": "Lens left | closed 49 x 36 mm", "R": "Lens right | closed 49 x 36 mm",
         "front": "Front | continuous milled acetate", "etch": "Lens right | subtle PRADA etch"}
OAKLEY_LENS = "Lens | continuous wrap shield 1.55mm"
INVU_LENS = "Shield | single continuous closed lens"
INVU_PRINT = "Shield | fine INVU corner print"
MATRIX = ("sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded", "coarse")
SCENES = ["metal", "metal_narrow", "nylor", "nylor_nocord", "nylor_thin", "crystal"] + \
    [f"acetate_{c}" for c in MATRIX] + ["acetate_open", "acetate_selfx", "acetate_notch", "acetate_wide",
                                         "acetate_fused", "acetate_drilled"]


def ft_parts(side: str) -> list:
    return [f"Brow_{side}", f"Eyewire_{side}", "Saddle_bridge", f"Pad_stalk_{side}", f"Nose_pad_{side}",
            f"Pad_mount_{side}"]


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------------------- steps
def x(key: str, call: str) -> dict:
    return {"op": "exec", "key": key, "code": f"from glasses3d.tests.proofs import wp4_proof_bpy as W; result = {call}"}


def ns(key: str, **kw) -> dict:
    return x(key, f"W.node_state(**__import__('json').loads({json.dumps(kw)!r}))")


def arrays(key: str, case: str, objs: list) -> dict:
    return {"op": "arrays", "key": key, "objects": objs, "npz": str(OUT / "npz" / f"{case}-{key}.npz")}


def export(key: str, case: str) -> dict:
    return {"op": "export", "key": key, "glb": str(OUT / "glb" / f"{case}-{key}.glb")}


def check(key: str, **kw) -> dict:
    return {"op": "check", "key": key, "kwargs": kw}


def seat(key: str, **kw) -> dict:
    return {"op": "seat", "key": key, "kwargs": kw}


def hide(key: str, **kw) -> dict:
    return {"op": "hide", "key": key, "kwargs": kw}


def jobs(scenes: dict) -> dict:
    """{case: (source .blend, steps)}."""
    J = {}
    # ---- P2 FT1119 q001 browline
    lenses = ["Lens_Left", "Lens_Right"]
    objs = lenses + ["Eyewire_Left", "Eyewire_Right"]
    st = [arrays("a0", "ft", objs), x("digest0", "W.digest_all()"),
          check("check_before", frame_type="browline", hidden_x_range_mm=FT_ALL_RANGES), export("before", "ft")]
    for side in ("Left", "Right"):
        st.append(hide(f"hide_{side}", eyewire=f"Eyewire_{side}", under=f"Brow_{side}",
                       hidden_x_range_mm=FT_RANGES[side]))
    st.append(arrays("amid", "ft", objs))
    for side in ("Left", "Right"):
        st.append(ns(f"ns_mid_{side}", lens=f"Lens_{side}", frame_parts=ft_parts(side), frame_type="browline"))
    for side in ("Left", "Right"):
        st.append(seat(f"seat_{side}", lens=f"Lens_{side}", frame_parts=ft_parts(side), frame_type="browline"))
    for side in ("Left", "Right"):
        st.append(ns(f"ns_after_{side}", lens=f"Lens_{side}", frame_parts=ft_parts(side), frame_type="browline"))
    st += [arrays("a1", "ft", objs), x("digest1", "W.digest_all()"), check("check_after"), export("after", "ft")]
    J["ft"] = (SOURCES["ft"], st)
    # ---- P3 Prada delivery
    objs = [PRADA["L"], PRADA["R"], PRADA["etch"]]
    st = [arrays("a0", "prada", objs), x("digest0", "W.digest_all()"), check("check_before", frame_type="acetate"),
          export("before", "prada")]
    for s in ("L", "R"):
        st.append(ns(f"ns_before_{s}", lens=PRADA[s], frame_parts=[PRADA["front"]], frame_type="acetate"))
    for s in ("L", "R"):
        st.append(seat(f"seat_{s}", lens=PRADA[s], frame_parts=[PRADA["front"]], frame_type="acetate"))
    for s in ("L", "R"):
        st.append(ns(f"ns_after_{s}", lens=PRADA[s], frame_parts=[PRADA["front"]], frame_type="acetate"))
    st += [arrays("a1", "prada", objs), x("digest1", "W.digest_all()"), check("check_after"),
           check("check_after_declared", frame_type="acetate"), export("after", "prada")]
    J["prada"] = (SOURCES["prada"], st)
    # ---- P5 accepted finals
    for case, lens in (("oakley", OAKLEY_LENS), ("invu", INVU_LENS)):
        st = [arrays("a0", case, [lens]), x("digest0", "W.digest_all()"), x("sharp0", f"W.rim_sharp({lens!r})"),
              x("wall0", f"W.wall_smooth({lens!r})"), check("check_before", frame_type="semi_rimless"),
              export("before", case), ns("ns_before", lens=lens, frame_type="semi_rimless"),
              seat("seat1", lens=lens, frame_type="semi_rimless"),
              arrays("a1", case, [lens]), x("digest1", "W.digest_all()"), x("sharp1", f"W.rim_sharp({lens!r})"),
              x("wall1", f"W.wall_smooth({lens!r})"), check("check_after"), export("after", case),
              ns("ns_after", lens=lens, frame_type="semi_rimless")]
        J[case] = (SOURCES[case], st)
    # ---- P4 fixtures
    L = "Lens_L"
    J["metal"] = (scenes["metal"], [
        arrays("a0", "metal", [L, "Eyewire"]), x("digest0", "W.digest_all()"), check("check_before", frame_type="metal"),
        export("before", "metal"), ns("ns_before", lens=L, frame_type="metal"), seat("seat1", lens=L, frame_type="metal"),
        arrays("a1", "metal", [L, "Eyewire"]), x("digest1", "W.digest_all()"), check("check_after"),
        export("after", "metal"), ns("ns_after", lens=L, frame_type="metal")])
    J["metal_narrow"] = (scenes["metal_narrow"], [
        x("digest0", "W.digest_all()"), ns("ns_before", lens=L, frame_type="metal"),
        seat("seat1", lens=L, frame_type="metal"), x("digest1", "W.digest_all()")])
    for sc in ("nylor", "nylor_nocord"):
        st = [arrays("a0", sc, [L]), x("digest0", "W.digest_all()"), export("before", sc),
              ns("ns_before", lens=L, frame_type="semi_rimless", lower_edge="nylor"),
              seat("seat1", lens=L, frame_type="semi_rimless", lower_edge="nylor"),
              arrays("a1", sc, [L]), x("digest1", "W.digest_all()"), check("check_after"), export("after", sc)]
        if sc == "nylor":
            st.append(x("cord_overlap", "W.bool_overlap('Cord', 'Lens_L')"))
        J[sc] = (scenes[sc], st)
    J["nylor_thin"] = (scenes["nylor_thin"], [
        x("digest0", "W.digest_all()"), seat("seat1", lens=L, frame_type="semi_rimless", lower_edge="nylor"),
        x("digest1", "W.digest_all()")])
    J["crystal"] = (scenes["crystal"], [
        arrays("a0", "crystal", [L]), x("digest0", "W.digest_all()"), export("before", "crystal"),
        seat("seat1", lens=L, frame_type="acetate"), arrays("a1", "crystal", [L]), x("digest1", "W.digest_all()"),
        check("check_after"), export("after", "crystal")])
    J["crystal_manifold"] = (scenes["crystal"], [x("open_cutter", "W.open_cutter_manifold('Lens_L', 'Crystal rim')")])
    for c in MATRIX:
        sc = f"acetate_{c}"
        J[sc] = (scenes[sc], [
            arrays("a0", sc, [L]), check("check_before", frame_type="acetate"), export("before", sc),
            ns("ns_before", lens=L, frame_type="acetate"), seat("seat1", lens=L, frame_type="acetate"),
            arrays("a1", sc, [L]), check("check_after"), export("after", sc), ns("ns_after", lens=L, frame_type="acetate")])
    for sc in ("acetate_open", "acetate_selfx", "acetate_notch", "acetate_wide"):
        J[sc] = (scenes[sc], [x("digest0", "W.digest_all()"), seat("seat1", lens=L, frame_type="acetate"),
                              x("digest1", "W.digest_all()")])
    J["acetate_bevel"] = (scenes["acetate_sphere"], [
        {"op": "exec", "key": "bevel", "code": "bpy.data.objects['Lens_L'].modifiers.new('Bevel', 'BEVEL')"},
        x("digest0", "W.digest_all()"), seat("seat1", lens=L, frame_type="acetate"), x("digest1", "W.digest_all()")])
    J["acetate_fused"] = (scenes["acetate_fused"], [
        arrays("a0", "acetate_fused", [L]), ns("ns_before", lens=L, frame_type="acetate"),
        seat("seat1", lens=L, frame_type="acetate"), arrays("a1", "acetate_fused", [L]), check("check_after"),
        export("after", "acetate_fused")])
    J["acetate_drilled"] = (scenes["acetate_drilled"], [
        arrays("a0", "acetate_drilled", [L]), ns("ns_before", lens=L, frame_type="acetate"),
        seat("seat1", lens=L, frame_type="acetate"), arrays("a1", "acetate_drilled", [L]), check("check_after"),
        export("after", "acetate_drilled")])
    return J


# --------------------------------------------------------------------------------------------------- running
def build_scenes(log: list) -> dict:
    out = OUT / "scenes"
    if out.exists():
        shutil.rmtree(out)
    t0 = time.time()
    proc = blender.run(None, BUILD, [out] + SCENES, timeout_s=600)
    built = {p.stem: p for p in out.glob("*.blend")}
    log.append({"step": "build", "seconds": round(time.time() - t0, 1), "built": sorted(built),
                "missing": sorted(set(SCENES) - set(built))})
    if set(SCENES) - set(built):
        raise RuntimeError(f"fixture build failed: {proc.stdout[-2000:]} {proc.stderr[-2000:]}")
    return built


def run_case(case: str, src: Path, steps: list) -> dict:
    copy = OUT / "copies" / f"{case}.blend"
    shutil.copyfile(src, copy)
    job = OUT / "jobs" / f"{case}.json"
    out = OUT / "outs" / f"{case}.json"
    if out.exists():
        out.unlink()
    job.write_text(json.dumps({"steps": steps}, indent=1), encoding="utf-8")
    t0 = time.time()
    proc = blender.run(copy, RUN, [job, out], timeout_s=900)
    meta = {"case": case, "source": str(src), "copy": str(copy), "seconds": round(time.time() - t0, 1),
            "returncode": proc.returncode}
    if not out.exists():
        meta["error"] = "no output"
        meta["stderr"] = proc.stderr[-3000:]
        meta["stdout"] = proc.stdout[-3000:]
    (OUT / "outs" / f"{case}.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def run(cases=None, workers: int = 6) -> dict:
    for d in ("copies", "jobs", "outs", "npz", "glb"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    log: list = []
    src_sha_before = {k: sha(p) for k, p in SOURCES.items() if p.is_file()}
    scenes = build_scenes(log)
    J = jobs(scenes)
    todo = [c for c in J if cases is None or c in cases]
    order = sorted(todo, key=lambda c: c not in ("invu", "ft", "prada", "oakley"))   # longest first
    with ThreadPoolExecutor(workers) as pool:
        metas = list(pool.map(lambda c: run_case(c, *J[c]), order))
    src_sha_after = {k: sha(p) for k, p in SOURCES.items() if p.is_file()}
    path = OUT / "run_state.json"
    old = json.loads(path.read_text(encoding="utf-8")) if (cases is not None and path.exists()) else {}
    srcs = old.get("sources", {})
    for k in src_sha_before:
        prev = srcs.get(k, {})
        srcs[k] = {"path": str(SOURCES[k]), "sha256_before": prev.get("sha256_before", src_sha_before[k]),
                   "sha256_after": src_sha_after.get(k)}
    state = {"sources": srcs, "build": old.get("build", []) + log,
             "cases": dict(old.get("cases", {}), **{m["case"]: m for m in metas}),
             "fixture_sha256": {k: sha(p) for k, p in scenes.items()}}
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade-only", action="store_true")
    ap.add_argument("--cases")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--run-only", action="store_true")
    a = ap.parse_args(argv)
    cases = set(a.cases.split(",")) if a.cases else None
    if not a.grade_only:
        state = run(cases, a.workers)
        bad = {c: m for c, m in state["cases"].items() if m.get("error")}
        print(json.dumps({"ran": sorted(state["cases"]), "errors": sorted(bad)}, indent=1))
        if a.run_only:
            return 0
    from glasses3d.tests.proofs import wp4_proof_grade as G
    proof = G.grade_all()
    PROOF_JSON.write_text(json.dumps(proof, indent=1), encoding="utf-8")
    print(PROOF_JSON)
    print(json.dumps(proof["summary"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
