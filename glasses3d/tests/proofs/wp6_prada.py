"""WP6 $0 proof on Prada PRS 17WS (plan/WP6_DESIGN.md §8, §9). A TOOL TEST: temples are built on a COPY of the
Prada scene with parameters Claude traced from the six real photos (01 side, 02 angled, 03 top, 04 front, 05 side,
06 rear). Nothing here is a delivered model; every output is labelled TOOL TEST.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_prada [--stages p0,p1,s1,p2,fit,p3,gates]
        [--out-dir DIR] [--rounds N] [--quick] [--resume] [--workers N] [--owner-waiver KEY=TEXT ...]

STOP-I on S1 (§8, §9): an S1 recovery outside its tolerance that R2 or R3 reads (the frozen table
wp6_prada_s1_dependencies.json, recorded by sha256 beside the rules; --resume refuses a changed table as it refuses
changed rules) stops the proof, whether S1 ran now or is on record. Only the OWNER may waive one: --owner-waiver
theta_deg="<the owner's words>" records it as "STOP-I waived by owner" and the proof goes on. Never pass a waiver the
owner did not give. S1 runs the real fit's procedure (wp6_prada_stages.make_retrace, FIT_MAX_ROUNDS rounds), grades
the outline on the fitted spec and theta on the (view, side) pairs the table says R2 / R3 read; a quantity S1 cannot
test (r_B: not a free parameter) is recorded as "not fitted", never as a recovery pass. The record's
`open_owner_decisions` lists the rule-set questions only the owner can decide (R1, R3's "delivered - 0.5").

--workers (default os.cpu_count() - 2; 1 = serial): the camera blocks and the joint fits run on a
relief_fit.FitPool of persistent workers, each opted out of EcoQoS. The pooled fit is the serial fit bit for bit
(tests/test_temple_fit_parallel.py), so --resume may mix serial and pooled stages.

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
S1_DEPS = Path(__file__).resolve().parent / "wp6_prada_s1_dependencies.json"
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
        self.pool = None
        self.rec = {"label": LABEL, "started_utc": utc(), "rules": {"path": str(RULES), "sha256": sha(RULES),
                                                                     "version": RR.RULES_VERSION,
                                                                     "s1_dependencies": {"path": str(S1_DEPS),
                                                                                         "sha256": sha(S1_DEPS)}},
                    "source_scene": {"path": str(PRADA_SCENE), "sha256_before": sha(PRADA_SCENE)},
                    "ecoqos_opt_out": opt_out_ecoqos(), "stages": {}, "stop": None}
        prev = out / "proof.json"
        if getattr(args, "resume", False) and prev.exists():
            old = json.loads(prev.read_text(encoding="utf-8"))
            if old["rules"]["sha256"] != self.rec["rules"]["sha256"]:
                raise SystemExit("--resume refused: relief_rules.py changed since the recorded run")
            old_deps = (old["rules"].get("s1_dependencies") or {}).get("sha256")
            if old_deps != self.rec["rules"]["s1_dependencies"]["sha256"]:
                raise SystemExit("--resume refused: the frozen S1 dependency table (wp6_prada_s1_dependencies.json) "
                                 f"changed since the recorded run (recorded {old_deps}, now "
                                 f"{self.rec['rules']['s1_dependencies']['sha256']}); it decides STOP-I on a recorded "
                                 "S1, so start a fresh run")
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
        self.rec["stages"][name] = {k: v for k, v in res.items() if not str(k).startswith("_")}   # "_x": in memory only
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
                V = np.array(t["V"])
                W = tb.world(1 if float(V[:, 0].mean()) > 0 else -1)[0]          # side by the x sign, never names
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


S1_PROCEDURE = ("s1-2 (2026-10-06, WP6 fix round 2): the fit runs stage_fit's procedure (make_retrace: the outline "
                "re-traced before every camera block; relief_rules.FIT_MAX_ROUNDS rounds); the outline graded on the "
                "fitted spec; theta graded on the (view, side) pairs the dependency table says R2 / R3 read; r_B "
                "recorded as not fitted; the start parameters deep-copied (never shared with P2)")


def _pair(p: str) -> tuple:
    """"05+x" -> ("05", 1)."""
    return p[:2], (1 if p[2] == "+" else -1)


def stage_s1(pf: Proof, job, fits, start_params: dict, truth_params: dict, contour_px: dict, quick: bool,
             deps: dict) -> dict:
    """S1: the tool's own temple (truth_params) rendered through the fitted cameras (perturbed by each view's contour
    error) as synthetic photos; traced and fitted back from start_params with the procedure the real fit runs
    (stage_fit: the re-trace callback, FIT_MAX_ROUNDS rounds). Both parameter dicts are deep-copied: the caller's
    (the proof's start, which P2 and the fit start from) are never touched."""
    from glasses3d.tests.proofs.wp6_prada_stages import _spec_sha, make_retrace
    start_params = copy.deepcopy(start_params)
    truth_params = copy.deepcopy(truth_params)
    truth_spec = FX.prada_spec(**truth_params)
    truth = RV.Model.from_spec(job, truth_spec, "S1 truth")
    cams = {k: fits[k]["camera"] for k in RV.VIEW_ORDER}
    theta = {k: dict(fits[k]["theta"]) for k in RV.VIEW_ORDER}
    sj = RF.SyntheticJob.from_job(job, truth, theta, cams, contour_px)
    views = ("01", "02", "03", "04", "05")          # the relief observables (R2 / R3) live in 05 / 01 / 02
    fam = RF.prada_family(copy.deepcopy(start_params))
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
    # P2's analogue: cameras refit from the perturbed base for the start model, the outline and the creases traced
    # at them; then stage_fit's fit (the same re-trace callback, FIT_MAX_ROUNDS rounds; 2 with --quick, as stage_fit)
    fits1 = RF.refit_views(sj, RV.Model.from_spec(sj, fam.spec(fam.x0()), "S1 start"), {}, views, rear=False,
                           pool=pf.pool)
    pf.hb.log("S1: cameras refit on the synthetic photos")
    ol = RT.trace_outline(sj, fam.spec(fam.x0()), lambda t, b: FX.prada_spec(**{**fam.kwargs(fam.x0()), "top": t,
                                                                                 "bottom": b}), fits1, log=pf.hb.log)
    fam.base["top"], fam.base["bottom"] = ol["top"], ol["bottom"]          # fam.base is S1's own deep copy
    m1 = RV.Model.from_spec(sj, fam.spec(fam.x0()), "S1 traced")
    cr = RT.trace_creases(sj, guides, m1, fits1)
    glines = {k: {cn: (ln["p0"], ln["p1"]) for cn, ln in cr["views"][k]["lines"].items() if ln} for k in guides}
    rounds = 2 if quick else RR.FIT_MAX_ROUNDS
    res = RF.fit_temple(sj, fam, views=views, guides_lines=glines, rounds=rounds, rear=False,
                        retrace=make_retrace(sj, pf.hb.log), log=pf.hb.log, budget_s=90 * 60, pool=pf.pool)
    (pf.out / "s1_fit.pkl").write_bytes(pickle.dumps({k: v for k, v in res.items() if k not in ("context", "family")}))
    x = res["x"]
    ps_t = build_temple(truth_spec).spec
    ps_f = build_temple(res["spec"]).spec
    lvl = lambda ps: float(ps.x_base(46.8))

    def outline_err(top, bottom):
        out = []
        for got, want in ((top, truth_params["top"]), (bottom, truth_params["bottom"])):
            g, w = np.asarray(got, float), np.asarray(want, float)
            if g.shape != w.shape or not np.allclose(g[:, 0], w[:, 0]):
                raise ValueError("S1: the traced outline's d knots differ from the truth's")
            out.append(float(np.mean(np.abs(g[:, 1] - w[:, 1]))))
        return max(out), out
    # the outline the fit DELIVERS (the fitted spec's: re-traced each round), as the real fit's is graded at G3
    outline_mm, (out_top, out_bot) = outline_err(res["spec"]["outline"]["top"], res["spec"]["outline"]["bottom"])
    pre_mm, _ = outline_err(ol["top"], ol["bottom"])
    # theta is graded on the (view, side) pairs R2 / R3 read (the frozen table's theta_pairs), whether or not that
    # photo observes theta (R2 / R3 read the fitted value either way); every observed pair is recorded beside it
    pairs = sorted({q for v in (deps.get("theta_pairs") or {}).values() for q in v})
    if not pairs:
        raise ValueError(f"{S1_DEPS.name} lists no theta_pairs: S1 cannot grade theta on what R2 / R3 read")
    obs = [(k, s) for k in views for s in (1, -1) if k in res["fits"]
           and (res["fits"][k].get("theta_fit") or {}).get(str(s), {}).get("observed", False)]
    tag = lambda k, s: f"{k}{'+' if s > 0 else '-'}x"
    th_err_by = {tag(k, s): abs(res["fits"][k]["theta"][s] - theta[k][s]) for k, s in obs}
    th_cited = {}
    for q in pairs:
        k, s = _pair(q)
        th_cited[q] = (abs(res["fits"][k]["theta"][s] - theta[k][s]) if k in res["fits"] else float("nan"))
    rec = {"outline_mm": outline_mm, "zone_level_mm": abs(lvl(ps_f) - lvl(ps_t)),
           "r_C_mm": abs(x["r_C"] - truth_params["r_C"]), "f1_rise": abs(x["rise"] - truth_params["rise"]),
           "theta_deg": (float(max(th_cited.values())) if all(np.isfinite(v) for v in th_cited.values())
                         else float("nan"))}
    not_fitted = {}
    if "r_B" in x:
        rec["r_B_mm"] = abs(x["r_B"] - truth_params.get("r_B", 0.0))
    else:
        not_fitted["r_B_mm"] = (f"not fitted (fixed at {truth_params.get('r_B', 0.0):g}): r_B is not a free parameter "
                                "of relief_fit.PRADA_FREE and the S1 truth keeps the start's r_B, so S1 cannot test "
                                "its recovery (R2 / R3 read r_B: the family's B-on-the-base assumption)")
    verdict = {k: bool(np.isfinite(rec[k]) and rec[k] <= RR.S1_RECOVERY[k]) for k in rec}
    # evidence for an owner's STOP-I decision on theta (cases a-d: the R3 transfer of the TRUE temple)
    from glasses3d.tests.proofs.wp6_s1_theta_check import transfer_cases
    theta_cases = transfer_cases(sj, truth, {k: res["fits"][k] for k in ("05", "02")}, theta)
    return {"truth": {k: truth_params.get(k, 0.0) for k in x}, "start": {k: start_params.get(k, 0.0) for k in x},
            "truth_params": truth_params, "truth_spec_sha256": _spec_sha(truth_spec),
            "fitted": x, "recovery": rec, "not_fitted": not_fitted,
            "outline_by_edge_mm": {"top": out_top, "bottom": out_bot},
            "outline_mm_pre_fit_trace": pre_mm,
            "theta_graded_on": pairs, "theta_error_cited_pairs_deg": th_cited,
            "theta_cited_pair_observed": {q: q in th_err_by for q in pairs},
            "theta_observed_in": [tag(k, s) for k, s in obs], "theta_error_by_view_deg": th_err_by,
            "theta_deg_max_all_observed_views": max(th_err_by.values(), default=None),
            "theta_cases": theta_cases,
            "tolerance": RR.S1_RECOVERY, "verdict": verdict, "failed": [k for k, ok in verdict.items() if not ok],
            "corners": cr["corners"], "outline_flagged": ol["flagged"], "fit_seconds": res["seconds"],
            "fit_rounds": rounds, "fit_rounds_run": res.get("rounds_run"),
            "fit_retraces": [h.get("outline_retraced_before_block") for h in res.get("history", [])]
            + [res.get("final_outline_retraced")],
            "sigma": res["sigma"], "sigma_status": res.get("sigma_status"), "sigma_flags": res.get("sigma_flags"),
            "fit_unobserved": res.get("unobserved"),
            "fit_termination": res.get("termination"), "fit_version": res.get("fit_version"),
            "s1_procedure": S1_PROCEDURE,
            "synthetic_render": RF.SYNTH_RENDER, "synthetic_light": list(RF.SYNTH_LIGHT)}


def s1_stop_i(recovery: dict, tolerance: dict, deps: dict, waivers: dict, not_fitted: dict | None = None) -> dict:
    """§8 S1 / §9 STOP-I from the frozen dependency table: every S1 recovery outside its tolerance that R2 or R3 reads
    (deps["depends"][key] non-empty) is STOP-I unless the owner waived it (waivers {key: the owner's words}); one that
    nothing in R2 / R3 reads is only recorded. A tolerance key S1 could not test (`not_fitted` {key: why}: r_B, not a
    free parameter) is listed with who reads it, never graded as a pass. Never UNOBSERVED (that list is Astra's:
    fitted parameters with a default)."""
    dep = deps.get("depends", {})
    not_fitted = dict(not_fitted or {})
    verdict = {k: bool(v is not None and np.isfinite(v) and v <= tolerance[k]) for k, v in recovery.items()}
    failed = [k for k, ok in verdict.items() if not ok]
    reads = {k: sorted(dep.get(k, {})) for k in failed}
    return {"verdict": verdict, "failed": failed, "read_by": reads,
            "stop_i": [k for k in failed if reads[k] and k not in waivers],
            "waived_by_owner": {k: waivers[k] for k in failed if reads[k] and k in waivers},
            "failed_not_read_by_r2_r3": [k for k in failed if not reads[k]],
            "not_fitted": {k: {"why": why, "read_by": sorted(dep.get(k, {}))} for k, why in not_fitted.items()},
            "not_graded": sorted(k for k in tolerance if k not in recovery and k not in not_fitted)}


# --------------------------------------------------------------------------------------------- the run
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="p0,p1,s1,p2,fit,p3,gates")
    ap.add_argument("--out-dir", default=str(OUT))
    ap.add_argument("--quick", action="store_true", help="fewer rounds in the fit (development)")
    ap.add_argument("--resume", action="store_true", help="keep the recorded stages of --out-dir (same rules sha256)")
    ap.add_argument("--workers", type=int, default=RF.pool_workers(),
                    help="process-pool workers for the camera blocks and the fits (1 = serial)")
    ap.add_argument("--owner-waiver", action="append", default=[], metavar="KEY=TEXT",
                    help="the OWNER's waiver of an S1 STOP-I (KEY = an S1 recovery key, TEXT = the owner's words); "
                         "never pass one the owner did not give")
    a = ap.parse_args(argv)
    stages = set(a.stages.split(","))
    a.waivers = parse_waivers(a.owner_waiver)
    pf = Proof(Path(a.out_dir), a)
    if a.waivers:
        pf.rec["owner_waivers"] = {k: {"text": v, "recorded_utc": utc()} for k, v in a.waivers.items()}
    pf.hb.log(f"{LABEL}; rules sha256 {pf.rec['rules']['sha256'][:16]}... recorded before any run")
    try:
        return _run(pf, a, stages)
    finally:
        if pf.pool is not None:
            pf.pool.close()


def _run(pf: Proof, a, stages) -> int:
    if "p0" in stages:
        r = pf.stage("P0", lambda: stage_p0(pf))
        if not r.get("pass"):
            return finish(pf, pf.stop("STOP-T", "a P0 tool check failed", r.get("failures") or r.get("error")))
    job = RV.Job("prada")
    pf.rec["cameras"] = {"path": str(job.cameras_path), "sha256": sha(job.cameras_path)}
    # a recorded S1 graded on synthetic photos rendered differently (relief_fit.SYNTH_RENDER) or fitted by a different
    # fit procedure (relief_fit.FIT_VERSION) is stale: re-run it
    old_s1 = pf.rec["stages"].get("S1")
    s1_stale = old_s1 is not None and (old_s1.get("synthetic_render") != RF.SYNTH_RENDER
                                       or old_s1.get("fit_version") != RF.FIT_VERSION
                                       or old_s1.get("s1_procedure") != S1_PROCEDURE)
    if a.workers > 1 and (stages & {"s1", "fit"} or s1_stale or not (pf.out / "fits_delivered.pkl").exists()):
        pf.pool = RF.FitPool(a.workers, log=pf.hb.log)
        up = pf.pool.warm(job)
        pf.rec["pool"] = {"workers": a.workers, "started": len(up),
                          "ecoqos_opt_out": sum(bool(u["ecoqos_opt_out"]) for u in up)}
        pf.hb.log(f"process pool: {len(up)} of {a.workers} workers up, "
                  f"{pf.rec['pool']['ecoqos_opt_out']} opted out of EcoQoS")
    if "p1" in stages:
        r = pf.stage("P1", lambda: stage_p1(pf, job))
        if not r.get("pass"):
            return finish(pf, pf.stop("STOP-I", "a P1 instrument check failed", r))
    fits_path = pf.out / "fits_delivered.pkl"
    if fits_path.exists():
        fits_d = pickle.loads(fits_path.read_bytes())
    else:
        def fit_delivered():
            # per view fit_model_view from the base camera, and the rear family from the base and turntable seeds:
            # refit_views does exactly that (in the pool when there is one)
            fd = RF.refit_views(job, RV.Model.delivered(job), {}, RV.VIEW_ORDER, rear=True, pool=pf.pool)
            for k in RV.VIEW_ORDER:
                if k != "06":
                    pf.hb.log(f"  delivered view {k}: theta {fd[k]['theta']}, refit kept {fd[k]['refit']['kept']}")
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
    start_json = json.dumps(start, sort_keys=True, default=_json)
    pf.rec["start_params"] = copy.deepcopy(start)            # a copy: nothing that mutates `start` can reach the record
    deps = json.loads(S1_DEPS.read_text(encoding="utf-8"))
    if s1_stale and "s1" not in stages:
        pf.hb.log(f"S1 on record is stale (photos {old_s1.get('synthetic_render')!r}, fit "
                  f"{old_s1.get('fit_version')!r}, procedure {old_s1.get('s1_procedure')!r}; now {RF.SYNTH_RENDER!r}, "
                  f"{RF.FIT_VERSION!r}, {S1_PROCEDURE!r}): re-running S1")
        pf.rec["s1_rerun_reason"] = "recorded S1 used a different synthetic photo model, fit or S1 procedure"
    if "s1" in stages or s1_stale:
        truth = dict(copy.deepcopy(start), r_A=start["r_A"] + 0.4, r_C=start["r_C"] + 1.0, rise=start["rise"] - 0.1,
                     x_in0=start["x_in0"] - 0.7, x_in_ze=start["x_in_ze"] - 0.7)
        cams_json = json.loads(job.cameras_path.read_text(encoding="utf-8"))["images"]
        contour_px = {k: cams_json[job.views[k][0]]["contour_mean_px"] for k in RV.VIEW_ORDER}
        r = pf.stage("S1", lambda: stage_s1(pf, job, fits_d, start, truth, contour_px, a.quick, deps))
        if r.get("error"):
            return finish(pf, pf.stop("STOP-T", "S1 crashed (a code fault, not an S1 verdict)", r["error"]))
    if json.dumps(start, sort_keys=True, default=_json) != start_json:
        return finish(pf, pf.stop("STOP-T", "the start parameters changed during S1 (a code fault: P2 and the fit "
                                  "must start from the guides' start)", None))
    s1 = pf.rec["stages"].get("S1")
    if (s1 is None or s1.get("error")) and stages & {"fit", "p3", "gates"}:
        return finish(pf, pf.stop("STOP-T", "S1 (the STOP-I input, §9 order) has no result on record: run it before "
                                  "the fit (add s1 to --stages)", (s1 or {}).get("error")))
    if s1 is not None and not s1.get("error"):
        # STOP-I from the frozen dependency table, for an S1 run now or on record (a resumed run re-grades it)
        si = s1_stop_i(s1["recovery"], RR.S1_RECOVERY, deps, getattr(a, "waivers", {}) or {},
                       not_fitted=s1.get("not_fitted"))
        pf.rec["s1_stop_i"] = si
        pf.rec["s1_failures"] = {k: {"recovery": s1["recovery"][k], "tolerance": RR.S1_RECOVERY[k],
                                     "read_by": si["read_by"][k],
                                     "status": ("STOP-I waived by owner" if k in si["waived_by_owner"] else
                                                "STOP-I" if k in si["stop_i"] else "recorded (not read by R2 / R3)")}
                                 for k in si["failed"]}
        pf.rec["s1_not_fitted"] = si["not_fitted"]
        pf.save()
        if si["stop_i"]:
            return finish(pf, pf.stop(
                "STOP-I", "S1 cannot recover a quantity that R2 / R3 read (frozen table "
                f"{S1_DEPS.name}); OPEN OWNER DECISION: waive with --owner-waiver KEY=TEXT or keep the stop",
                {"parameters": si["stop_i"], "recovery": s1["recovery"], "tolerance": s1["tolerance"],
                 "read_by": {k: deps["depends"][k] for k in si["stop_i"]},
                 "theta_graded_on": s1.get("theta_graded_on"),
                 "theta_error_cited_pairs_deg": s1.get("theta_error_cited_pairs_deg"),
                 "theta_error_by_view_deg": s1.get("theta_error_by_view_deg"),
                 "theta_cases_R3_transfer_of_the_true_temple": s1.get("theta_cases"),
                 "outline_by_edge_mm": s1.get("outline_by_edge_mm"),
                 "not_fitted": si["not_fitted"],
                 "waived_by_owner": si["waived_by_owner"]}))
    from glasses3d.tests.proofs.wp6_prada_stages import run_rest
    return finish(pf, run_rest(pf, job, fits_d, start, gp, stages, a))


def parse_waivers(items) -> dict:
    """["key=text", ...] -> {key: text}; a key must be an S1 recovery key (relief_rules.S1_RECOVERY)."""
    out = {}
    for it in items or []:
        k, sep, txt = str(it).partition("=")
        k = k.strip()
        if not sep or k not in RR.S1_RECOVERY or not txt.strip():
            raise SystemExit(f"--owner-waiver {it!r}: expected KEY=TEXT with KEY in {sorted(RR.S1_RECOVERY)}")
        out[k] = txt.strip()
    return out


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
    from glasses3d.tests.proofs.wp6_prada_stages import open_owner_decisions
    pf.rec["open_owner_decisions"] = open_owner_decisions(pf.rec)
    pf.rec["result"] = code
    pf.rec["finished_utc"] = utc()
    pf.rec["source_scene"]["sha256_after"] = sha(PRADA_SCENE)
    pf.save()
    pf.hb.log(f"result: {code}")
    pf.hb.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
