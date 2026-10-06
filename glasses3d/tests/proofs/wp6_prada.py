"""WP6 $0 proof on Prada PRS 17WS (plan/WP6_DESIGN.md §8, §9). A TOOL TEST: temples are built on a COPY of the
Prada scene with parameters Claude traced from the six real photos (01 side, 02 angled, 03 top, 04 front, 05 side,
06 rear). Nothing here is a delivered model; every output is labelled TOOL TEST.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_prada [--stages p0,p1,s1,p2,fit,p3,gates]
        [--out-dir DIR] [--rounds N] [--quick]

Order (the stop rule, §9): P0 tool (STOP-T) -> P1 instruments + S1 identifiability (STOP-I) -> rear family (STOP-R)
-> fit, S2 representability (STOP-S), S3 consistency (STOP-C) -> P3 build -> gates (STOP-P). The first stop that fires
ends the run with its evidence. relief_rules.py's sha256 is recorded before anything runs.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import hashlib
import json
import math
import pickle
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

G3D = Path(__file__).resolve().parents[2]
REPO = G3D.parent
sys.path.insert(0, str(REPO))

from glasses3d.g3d.temple import build_temple, report  # noqa: E402
from glasses3d.g3d.temple import fixtures as FX  # noqa: E402
from glasses3d.g3d.temple import measure as MS  # noqa: E402
from glasses3d.g3d_host import blender  # noqa: E402
from glasses3d.g3d_host import matte as M  # noqa: E402
from glasses3d.g3d_host import relief_check as RC  # noqa: E402
from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.g3d_host import relief_trace as RT  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.g3d_host.camera import Camera  # noqa: E402
from glasses3d.g3d_host.spec import validate_spec  # noqa: E402

LABEL = "TOOL TEST (WP6 $0 proof on a scene COPY; not a delivered model)"
OUT = G3D / "data" / "wp6" / "proof"
PRADA_SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
RUN = Path(__file__).resolve().parent / "wp6_bpy_run.py"
GUIDES = Path(__file__).resolve().parent / "wp6_prada_guides.json"
RULES = G3D / "g3d_host" / "relief_rules.py"
RIGHT = "Temple right | faceted sweep and ear bend"        # +X (Astra's "right" = the wearer's left)
LEFT = "Temple left | faceted sweep and ear bend"


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def opt_out_ecoqos() -> bool:
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes

        class S(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        s = S(1, 1, 0)
        return bool(k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(s), ctypes.sizeof(s)))
    except Exception:                                            # noqa: BLE001
        return False


class Heartbeat:
    """Prints '[elapsed] stage: ...' at least every `every` seconds while a stage runs."""

    def __init__(self, every=60.0):
        self.t0 = time.time()
        self.stage = "start"
        self.every = every
        self._stop = threading.Event()
        self._last = time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def log(self, msg):
        self._last = time.time()
        print(f"[{time.time() - self.t0:7.0f} s] {msg}", flush=True)

    def _run(self):
        while not self._stop.wait(5.0):
            if time.time() - self._last >= self.every:
                self.log(f"{self.stage}: still running")

    def stop(self):
        self._stop.set()


class Proof:
    def __init__(self, out: Path, args):
        self.out = out
        self.args = args
        out.mkdir(parents=True, exist_ok=True)
        self.hb = Heartbeat()
        self.rec = {"label": LABEL, "started_utc": utc(), "rules": {"path": str(RULES), "sha256": sha(RULES),
                                                                     "version": RR.RULES_VERSION},
                    "source_scene": {"path": str(PRADA_SCENE), "sha256_before": sha(PRADA_SCENE)},
                    "ecoqos_opt_out": opt_out_ecoqos(), "stages": {}, "stop": None}
        prev = out / "proof.json"
        if getattr(args, "resume", False) and prev.exists():
            old = json.loads(prev.read_text(encoding="utf-8"))
            if old["rules"]["sha256"] != self.rec["rules"]["sha256"]:
                raise SystemExit("--resume refused: relief_rules.py changed since the recorded run")
            self.rec["stages"] = old.get("stages", {})
            self.rec["resumed"] = {"previous_start": old.get("started_utc"), "previous_stop": old.get("stop"),
                                   "previous_result": old.get("result")}
            self.rec["started_utc"] = old.get("started_utc")
            self.rec["resumed_utc"] = utc()
        self.save()

    def save(self):
        (self.out / "proof.json").write_text(json.dumps(self.rec, indent=1, default=_json), encoding="utf-8")

    def stage(self, name, fn):
        self.hb.stage = name
        self.hb.log(f"== stage {name}")
        t0 = time.time()
        try:
            res = fn()
        except Exception as exc:                                  # noqa: BLE001 - record and stop
            res = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        res = res or {}
        res["seconds"] = time.time() - t0
        self.rec["stages"][name] = res
        self.save()
        self.hb.log(f"== stage {name} done in {res['seconds']:.0f} s")
        return res

    def stop(self, code, why, evidence=None):
        self.rec["stop"] = {"code": code, "why": why, "evidence": evidence}
        self.rec["finished_utc"] = utc()
        self.rec["source_scene"]["sha256_after"] = sha(PRADA_SCENE)
        self.save()
        self.hb.log(f"STOP {code}: {why}")
        return code


def _json(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, Camera):
        return o.to_dict()
    if isinstance(o, Path):
        return str(o)
    return str(o)


# --------------------------------------------------------------------------------------------- blender runs
def blender_steps(tmp: Path, blend: Path, steps: list, name: str, timeout_s=1800) -> list:
    sp, rp = tmp / f"{name}_steps.json", tmp / f"{name}_receipt.json"
    sp.write_text(json.dumps(steps, default=_json), encoding="utf-8")
    proc = blender.run(blend, RUN, [sp, rp], timeout_s=timeout_s)
    if not rp.exists():
        raise RuntimeError(f"Blender wrote no receipt: {proc.stderr[-2000:]} {proc.stdout[-2000:]}")
    return json.loads(rp.read_text(encoding="utf-8"))["steps"]


def scene_copy(out: Path, name: str) -> Path:
    dst = out / name
    if dst.exists():
        dst.unlink()
    shutil.copy2(PRADA_SCENE, dst)
    return dst


# --------------------------------------------------------------------------------------------- P0
PRISM_DIHEDRALS = None


def prism_dihedrals():
    n = {"N": (0.0, 0.2, 1.0), "E": (0.1, 0.0, 1.0), "S": (0.0, -0.2, 1.0), "W": (-0.1, 0.0, 1.0), "base": (0, 0, 1)}
    ang = lambda a, b: math.degrees(math.acos(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))))
    return [ang(n[a], n[b]) for a, b in (("E", "N"), ("E", "S"), ("N", "W"), ("S", "W"), ("E", "base"))]


def stage_p0(pf: Proof, extra_specs: dict | None = None) -> dict:
    out = pf.out / "p0"
    out.mkdir(parents=True, exist_ok=True)
    blend = scene_copy(out, "prada_copy_p0.blend")
    specs = {n: FX.FIXTURES[n]() for n in sorted(FX.FIXTURES)}
    specs.update(extra_specs or {})
    steps = [{"op": "scene_digest"}, {"op": "mesh_digests", "objects": [RIGHT, LEFT]}]
    index = {}
    for name, spec in specs.items():
        glb = out / f"{name}.glb"
        index[name] = len(steps)
        steps += [{"op": "faceted_temple", "spec": spec, "dry_run": True}, {"op": "scene_digest"},
                  {"op": "faceted_temple", "spec": spec}, {"op": "dump_temples", "objects": [RIGHT, LEFT]},
                  {"op": "export", "path": str(glb)},
                  {"op": "unfaceted_temple", "object": RIGHT}, {"op": "unfaceted_temple", "object": LEFT},
                  {"op": "scene_digest"}, {"op": "mesh_digests", "objects": [RIGHT, LEFT]}]
    ref_glb = out / "delivered_copy.glb"
    index["_delivered"] = len(steps)
    steps.append({"op": "export", "path": str(ref_glb)})
    refusals = dict(FX.refusal_cases())
    big_roll = FX.pyramid()
    big_roll["roll_mm"] = 8.0
    refusals["E_ROLL"] = big_roll
    index["_refusals"] = len(steps)
    for code, spec in sorted(refusals.items()):
        steps += [{"op": "faceted_temple", "spec": spec}, {"op": "scene_digest"}]
    steps += [{"op": "faceted_temple", "spec": FX.pyramid(), "targets": {"+x": "Front | continuous milled acetate"}},
              {"op": "scene_digest"}, {"op": "timing_faceted_temple", "spec": FX.prada_default(), "repeat": 5}]
    pf.hb.log(f"P0: {len(specs)} fixtures + {len(refusals) + 1} refusals in one Blender session")
    st = blender_steps(out, blend, steps, "p0", timeout_s=3600)
    d0 = st[0]["digest"]
    res = {"fixtures": {}, "refusals": {}, "failures": []}
    for name, spec in specs.items():
        i = index[name]
        dry, dig1, ft, dump, exp, u1, u2, dig2, md2 = st[i:i + 9]
        tb = build_temple(spec)
        tb2 = build_temple(spec)
        rr = {"dry_run_unchanged": bool(dry.get("scene_unchanged")) and dig1["digest"] == d0,
              "status": ft.get("status"), "revert_byte_identical": dig2["digest"] == d0 and md2["digests"] == st[1]["digests"]}
        ok = ft.get("status") == "ok"
        if ok:
            rr["host_vs_blender_core_bit_identical"] = bool(np.array_equal(np.array(ft["core_digest"]["V"]), tb.V))
            rr["rebuild_bit_identical"] = bool(np.array_equal(tb.V, tb2.V) and tb.polys == tb2.polys)
            worst = 0.0
            for t in dump["temples"]:
                W = tb.world(1 if t["name"] == RIGHT else -1)[0]
                V = np.array(t["V"])
                if spec.get("roll_mm", 0.0) > 0:
                    continue                                       # the applied Bevel adds vertices (graded below)
                worst = max(worst, float(np.abs(V - W).max()) if V.shape == W.shape else float("inf"))
            rr["stored_vs_planned_mm"] = worst
            rr["core_checks"] = {k: tb.checks[k] for k in ("closed", "euler", "volume_mm3", "planarity_max_mm")}
            rr["core_inner_flat_mm"] = ft["geometry"]["inner_flatness_mm"]
            rr["budget"] = ft["budget"]
            rr["warnings"] = [w.get("code") for w in ft["warnings"]]
            rr["roll"] = {s: ft["sides"][s]["roll"] for s in ft["sides"]}
            rr["decals"] = {s: ft["sides"][s]["decals"] for s in ft["sides"]}
            rr["hinge"] = {s: ft["sides"][s]["hinge"] for s in ft["sides"]}
            if exp.get("status") == "exported":
                vs = validate_spec(exp_path := Path(steps[i + 4]["path"]))
                rr["validate_spec"] = {"ok": vs["ok"], "failed": [r for r, v in vs["rows"].items()
                                                                   if v.get("status") == "fail"] if "rows" in vs else None}
                from glasses3d.tests.proofs.wp6_p0_instrument import grade_glb
                g = grade_glb(exp_path, hinge_z=spec["hinge"]["z_mm"], zone_end=spec["zone_end_d"],
                              outline=spec["outline"], reference_glb=ref_glb if name.startswith("prada") else None,
                              analytic_dihedrals=prism_dihedrals() if name == "analytic_prism" else None,
                              facet_polys={l: [spec["nodes"][n][:2] for n in f["poly"]] if isinstance(f, dict)
                                           else [spec["nodes"][n][:2] for n in f]
                                           for l, f in spec["facets"].items()})
                rr["instrument"] = {"pass": g["pass"],
                                    "failed": {s: {k: v for k, v in t["checks"].items() if not v["pass"]}
                                               for s, t in g["temples"].items()},
                                    "global": g["checks"]}
            else:
                rr["export"] = exp
        crit = [rr["dry_run_unchanged"], rr["revert_byte_identical"], ok]
        if ok:
            crit += [rr["host_vs_blender_core_bit_identical"], rr["rebuild_bit_identical"],
                     rr["stored_vs_planned_mm"] <= 1e-4, rr["core_checks"]["closed"], rr["core_checks"]["euler"] == 2,
                     rr["core_checks"]["planarity_max_mm"] <= 1e-9, rr["core_inner_flat_mm"] <= 1e-9,
                     rr.get("validate_spec", {}).get("ok", False), rr.get("instrument", {}).get("pass", False),
                     rr["budget"].get("total_cpu_ms", rr["budget"]["total_ms"]) <= 1000.0]
            for s, rl in rr["roll"].items():
                if rl is not None:
                    crit += [rl["ok"]]
            if name.startswith("prada"):
                for s, ds in rr["decals"].items():
                    crit += [all(d["misses"] == 0 and d["max_move_mm"] <= 1.0 and len(d["regions"]) == 1 for d in ds)]
        rr["pass"] = bool(all(crit))
        if not rr["pass"]:
            res["failures"].append(name)
        res["fixtures"][name] = rr
        pf.hb.log(f"  P0 {name}: {'pass' if rr['pass'] else 'FAIL'}")
    j = index["_refusals"]
    for code, spec in sorted(refusals.items()):
        r, dg = st[j], st[j + 1]
        ok = r.get("status") == "refused" and r.get("code") == code and bool(r.get("fields")) and \
            r.get("scene_unchanged") and dg["digest"] == d0
        res["refusals"][code] = {"pass": bool(ok), "code": r.get("code"), "message": r.get("message"),
                                 "fields": r.get("fields")}
        if not ok:
            res["failures"].append(f"refusal {code}")
        j += 2
    tr, dg, tm = st[j], st[j + 1], st[j + 2]
    res["refusals"]["E_TARGET"] = {"pass": tr.get("code") == "E_TARGET" and dg["digest"] == d0,
                                   "message": tr.get("message")}
    if not res["refusals"]["E_TARGET"]["pass"]:
        res["failures"].append("refusal E_TARGET")
    host = []
    for _ in range(7):
        t0 = time.process_time()                                  # CPU time: other workflows on the machine
        build_temple(FX.prada_default())
        host.append(time.process_time() - t0)
    res["budget"] = {"host_core_ms_min": 1000 * min(host), "blender_dry_run_s": tm.get("seconds"),
                     "blender_core_ms": tm.get("core_ms")}
    if 1000 * min(host) > 50.0:
        res["failures"].append("core budget > 50 ms")
    res["source_sha256_unchanged"] = sha(PRADA_SCENE) == pf.rec["source_scene"]["sha256_before"]
    res["pass"] = not res["failures"] and res["source_sha256_unchanged"]
    return res


# --------------------------------------------------------------------------------------------- P1 / S1
def stage_p1(pf: Proof, job) -> dict:
    res = {"checks": {}}
    tb = build_temple(FX.analytic_prism())
    vb, _ = MS.vbulge(tb.V, tb.tris)
    pb, _ = MS.pbulge(tb.V, tb.tris)
    share = MS.single_plane_share(tb.V, tb.tris, report.facet_planes_chart(tb))
    dih = sorted(c["dihedral_deg"][1] for c in tb.tables["creases"])
    res["checks"]["prism"] = {"vbulge": vb, "pbulge": pb, "share": share["share"], "dihedrals": dih,
                              "pass": abs(vb - 1.8) < 1e-9 and abs(pb - (2 - 0.025 * 30 / 49.75)) < 1e-9 and
                              np.allclose(sorted(dih), sorted(prism_dihedrals()), atol=0.01)}
    g = report.geometry_numbers(build_temple(FX.chamfered_slab_30()), photo_creases=4)
    res["checks"]["chamfered_slab_30"] = {"vbulge": g["vbulge_mm"], "share": g["single_plane_share"],
                                          "slab": any(f["flag"] == "SLAB" for f in g["flags"]),
                                          "pass": any(f["flag"] == "SLAB" for f in g["flags"]) and
                                          abs(g["vbulge_mm"] - 2.2) <= 0.15}
    rep = RC.check_relief(job.glb, hinge_z=job.hinge["z_mm"], photo_creases=4)
    dl = {}
    okd = True
    for side, m in rep["temples"].items():
        vals = {k: m[{"vbulge_mm": "vbulge_mm", "pbulge_mm": "pbulge_mm",
                      "single_plane_share": "single_plane_share"}[k]] for k in RR.P1_DELIVERED}
        dl[side] = vals
        for k, (v0, tol) in RR.P1_DELIVERED.items():
            okd &= abs(vals[k] - v0) <= tol
    res["checks"]["delivered_glb"] = {"values": dl, "pass": bool(okd)}
    sub = {}
    for prod in ("prada", "ft1119"):
        man = G3D / "data" / "reference" / prod / "manifest.json"
        if not man.exists():
            continue
        for name in json.loads(man.read_text(encoding="utf-8"))["files"]:
            if name == "model.glb":
                continue
            img = M.load_photo(man.parent / name)
            sub[f"{prod}/{name}"] = not bool(np.any(M.matte(img, mode="glossy_floor") & ~M.matte(img)))
    res["checks"]["glossy_subset"] = {"photos": sub, "pass": bool(sub) and all(sub.values())}
    res["pass"] = all(c["pass"] for c in res["checks"].values())
    return res


def delivered_segments(pf: Proof, job, fits) -> dict:
    """P1: on 05 / 01 every delivered segment is SHALLOW or FLAG (the delivered model at its refit camera)."""
    model = RV.Model.delivered(job)
    out = {}
    for key, near in (("05", 1), ("01", -1)):
        f = fits[key]
        r = RV.render_model(job, model, key, f["camera"], f["theta"], points=True, normals=True)
        dm = RC.decal_mask(job, key, f["camera"])
        seg = RC.photo_crease_segments(job, key, r, f["camera"], f["theta"], near, decal_mask=dm)
        out[key] = seg
    allseg = [s for k in out for s in out[k]["segments"]]
    return {"views": out, "pass": bool(allseg) and all(s["verdict"] in ("SHALLOW", "FLAG") for s in allseg),
            "dn_max_deg": max((s["dn_deg"] for s in allseg), default=None),
            "length_mm": sum(s["len_mm"] for s in allseg)}


def prada_guides() -> dict:
    return json.loads(GUIDES.read_text(encoding="utf-8"))["guides"]


def stage_s1(pf: Proof, job, fits, start_params: dict, truth_params: dict, contour_px: dict, quick: bool) -> dict:
    """S1: the tool's own temple (truth_params) rendered through the fitted cameras (perturbed by each view's contour
    error) as synthetic photos; traced and fitted back from start_params."""
    truth_spec = FX.prada_spec(**truth_params)
    truth = RV.Model.from_spec(job, truth_spec, "S1 truth")
    cams = {k: fits[k]["camera"] for k in RV.VIEW_ORDER}
    theta = {k: dict(fits[k]["theta"]) for k in RV.VIEW_ORDER}
    sj = RF.SyntheticJob.from_job(job, truth, theta, cams, contour_px)
    views = ("01", "02", "03", "04", "05")          # the relief observables (R2 / R3) live in 05 / 01 / 02
    fam = RF.prada_family(start_params)
    # guides: the true creases in the synthetic photos, 2 px off (what an operator would click)
    tfits = {k: {"camera": sj.true_cams[k], "theta": theta[k]} for k in views}
    guides = {}
    for key, side in (("05", 1), ("01", -1), ("02", 1)):
        guides[key] = {}
        for cname, nodes in (("AB", ("A", "B")), ("AC", ("A", "C")), ("BC", ("B", "C"))):
            q = RF.model_crease_px(sj, truth.build, tfits[key], side, *nodes)
            c = q.mean(0)
            d = np.linalg.svd(q - c)[2][0]
            t = (q - c) @ d
            guides[key][cname] = (np.array([c + d * t.min(), c + d * t.max()]) + [[1.5, -2.0], [-1.0, 2.0]]).tolist()
    # cameras: refit from the perturbed base, then trace the outline and the creases, then fit
    fits1 = {k: RV.fit_model_view(sj, RV.Model.from_spec(sj, fam.spec(fam.x0()), "S1 start"), k) for k in views}
    pf.hb.log("S1: cameras refit on the synthetic photos")
    ol = RT.trace_outline(sj, fam.spec(fam.x0()), lambda t, b: FX.prada_spec(**{**fam.kwargs(fam.x0()), "top": t,
                                                                                 "bottom": b}), fits1, log=pf.hb.log)
    fam.base["top"], fam.base["bottom"] = ol["top"], ol["bottom"]
    m1 = RV.Model.from_spec(sj, fam.spec(fam.x0()), "S1 traced")
    cr = RT.trace_creases(sj, guides, m1, fits1)
    glines = {k: {cn: (ln["p0"], ln["p1"]) for cn, ln in cr["views"][k]["lines"].items() if ln} for k in guides}
    res = RF.fit_temple(sj, fam, views=views, guides_lines=glines, rounds=2, rear=False,
                        fits0=fits1, refit_cameras=True, log=pf.hb.log, budget_s=40 * 60)
    (pf.out / "s1_fit.pkl").write_bytes(pickle.dumps({k: v for k, v in res.items() if k not in ("context", "family")}))
    x = res["x"]
    ps_t = build_temple(truth_spec).spec
    ps_f = build_temple(res["spec"]).spec
    lvl = lambda ps: float(ps.x_base(46.8))
    top_err = np.abs(np.array([y for _, y in ol["top"]]) - np.array([y for _, y in truth_params["top"]]))
    bot_err = np.abs(np.array([y for _, y in ol["bottom"]]) - np.array([y for _, y in truth_params["bottom"]]))
    # theta is graded where the photo observes it (fit_theta's flat-objective test); elsewhere it is UNOBSERVED
    obs = [(k, s) for k in views for s in (1, -1) if k in res["fits"]
           and (res["fits"][k].get("theta_fit") or {}).get(str(s), {}).get("observed", False)]
    th_err = max((abs(res["fits"][k]["theta"][s] - theta[k][s]) for k, s in obs), default=float("nan"))
    rec = {"outline_mm": float(max(top_err.mean(), bot_err.mean())),
           "zone_level_mm": abs(lvl(ps_f) - lvl(ps_t)), "r_B_mm": abs(x.get("r_B", 0.0) - truth_params.get("r_B", 0.0)),
           "r_C_mm": abs(x["r_C"] - truth_params["r_C"]), "f1_rise": abs(x["rise"] - truth_params["rise"]),
           "theta_deg": float(th_err)}
    theta_views = [f"{k}{'+' if s > 0 else '-'}x" for k, s in obs]
    verdict = {k: bool(np.isfinite(rec[k]) and rec[k] <= RR.S1_RECOVERY[k]) for k in rec}
    unobserved = [k for k, ok in verdict.items() if not ok]
    stop_i = [k for k in unobserved if k in ("zone_level_mm", "r_B_mm", "r_C_mm", "f1_rise")]
    return {"truth": {k: truth_params.get(k, 0.0) for k in x}, "start": {k: start_params.get(k, 0.0) for k in x},
            "fitted": x, "recovery": rec,
            "theta_observed_in": theta_views,
            "tolerance": RR.S1_RECOVERY, "verdict": verdict, "unobserved": unobserved, "stop_i": stop_i,
            "corners": cr["corners"], "outline_flagged": ol["flagged"], "fit_seconds": res["seconds"],
            "sigma": res["sigma"]}


# --------------------------------------------------------------------------------------------- the run
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="p0,p1,s1,p2,fit,p3,gates")
    ap.add_argument("--out-dir", default=str(OUT))
    ap.add_argument("--quick", action="store_true", help="fewer rounds in the fit (development)")
    ap.add_argument("--resume", action="store_true", help="keep the recorded stages of --out-dir (same rules sha256)")
    a = ap.parse_args(argv)
    stages = set(a.stages.split(","))
    pf = Proof(Path(a.out_dir), a)
    pf.hb.log(f"{LABEL}; rules sha256 {pf.rec['rules']['sha256'][:16]}... recorded before any run")
    if "p0" in stages:
        r = pf.stage("P0", lambda: stage_p0(pf))
        if not r.get("pass"):
            return finish(pf, pf.stop("STOP-T", "a P0 tool check failed", r.get("failures") or r.get("error")))
    job = RV.Job("prada")
    pf.rec["cameras"] = {"path": str(job.cameras_path), "sha256": sha(job.cameras_path)}
    if "p1" in stages:
        r = pf.stage("P1", lambda: stage_p1(pf, job))
        if not r.get("pass"):
            return finish(pf, pf.stop("STOP-I", "a P1 instrument check failed", r))
    fits_path = pf.out / "fits_delivered.pkl"
    if fits_path.exists():
        fits_d = pickle.loads(fits_path.read_bytes())
    else:
        def fit_delivered():
            fd = {}
            for k in RV.VIEW_ORDER:
                if k == "06":
                    continue
                fd[k] = RV.fit_model_view(job, RV.Model.delivered(job), k)
                pf.hb.log(f"  delivered view {k}: theta {fd[k]['theta']}, refit kept {fd[k]['refit']['kept']}")
            seeds = [job.base["06"], RV.turntable_seed(job, job.base["04"])]
            fam = RV.rear_family(job, RV.Model.delivered(job), seeds)
            best = min(fam["rows"], key=lambda r_: r_["loss"])
            cam = Camera.from_dict(best["camera"])
            th = RV.fit_theta(job, RV.Model.delivered(job), "06", cam, {1: best["alpha"], -1: best["alpha"]})
            fd["06"] = {"camera": cam, "theta": {s: th[s]["theta"] for s in (1, -1)}, "family": fam,
                        "alpha": best["alpha"]}
            return fd
        pf.hb.stage = "delivered cameras"
        fits_d = fit_delivered()
        fits_path.write_bytes(pickle.dumps(fits_d))
    pf.rec["stages"]["delivered_fits"] = {k: {"camera": f["camera"].to_dict(), "theta": f["theta"],
                                              "refit": f.get("refit"), "family": f.get("family")}
                                          for k, f in fits_d.items()}
    fam06 = fits_d["06"]["family"]
    if fam06["stop_r"]:
        return finish(pf, pf.stop("STOP-R", "every alpha of the delivered model's rear family sits at a pitch bound or "
                                  "its frame guard exceeds 2 mm", fam06))
    if "p1" in stages:
        r = pf.stage("P1_delivered_segments", lambda: delivered_segments(pf, job, fits_d))
        if not r.get("pass"):
            return finish(pf, pf.stop("STOP-I", "a delivered side-view segment is OK (the crease instrument "
                                      "cannot tell the delivered slab from relief)", r))
    gp = json.loads(GUIDES.read_text(encoding="utf-8"))
    start = start_params(job, gp)
    pf.rec["start_params"] = start
    if "s1" in stages:
        truth = dict(start, r_A=start["r_A"] + 0.4, r_C=start["r_C"] + 1.0, rise=start["rise"] - 0.1,
                     x_in0=start["x_in0"] - 0.7, x_in_ze=start["x_in_ze"] - 0.7)
        tab = json.loads((G3D / "data" / "wp6" / "wp3b_glossy" / "table.json").read_text(encoding="utf-8"))["table"]
        cams_json = json.loads(job.cameras_path.read_text(encoding="utf-8"))["images"]
        contour_px = {k: cams_json[job.views[k][0]]["contour_mean_px"] for k in RV.VIEW_ORDER}
        r = pf.stage("S1", lambda: stage_s1(pf, job, fits_d, start, truth, contour_px, a.quick))
        if r.get("error"):
            return finish(pf, pf.stop("STOP-T", "S1 crashed (a code fault, not an S1 verdict)", r["error"]))
        if r.get("stop_i"):
            return finish(pf, pf.stop("STOP-I", "S1 cannot recover a parameter that R2 / R3 depend on",
                                      {"parameters": r["stop_i"], "recovery": r["recovery"],
                                       "tolerance": r["tolerance"]}))
    from glasses3d.tests.proofs.wp6_prada_stages import run_rest
    return finish(pf, run_rest(pf, job, fits_d, start, gp, stages, a))


def start_params(job, gp) -> dict:
    """The starting Prada parameters Claude traced (guides file: outline, node positions, relief priors)."""
    p = RF.prada_base_params()
    for k in ("top", "bottom", "zone_end", "x_in0", "x_in_ze", "t_zone", "arm", "r_A", "r_C", "rise", "B", "C",
              "d_E"):
        if k in gp.get("start", {}):
            v = gp["start"][k]
            p[k] = tuple(v) if k in ("B", "C") else v
    return p


def finish(pf: Proof, code) -> int:
    pf.rec["result"] = code
    pf.rec["finished_utc"] = utc()
    pf.rec["source_scene"]["sha256_after"] = sha(PRADA_SCENE)
    pf.save()
    pf.hb.log(f"result: {code}")
    pf.hb.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
