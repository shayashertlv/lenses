"""T2 preliminary rig check (WP6_FACETS §5; a check of the seeing tool, not of the product).

The owner-preferred v1 spec (data/wp6/pose_arm_resume_20261010/distal_all_views/before/spec.json, sha cfc11127...) is
built with the v1 builder g3d.temple_bpy.faceted_temple — snapshotted from commit 62b25e4 (git archive) so a parallel
builder rewrite in the working tree cannot leak in — on COPIES of the original Prada scene (sha 56274deb...), once as
is ("base") and once with node C's relief + 1 mm ("plus"). Both are rendered with the rig (``--rig``: studio_dome_v1,
or photo_env with a frozen environment ``--env`` = a work/compare/env-<basis>.json, by default the one fitted on the
ORIGINAL model in data/wp6v2/light_fit) at photos 05 and 01 (and black-side) through identical crop cameras (the
photo-matched compare's temple crop of the base), with the photo-matched processing of agent/seeing.py: the job's
gains (04's front acetate, per channel, for the beige target; the dark black-side photo's luma gain for
gloss_black), each photo's acetate p2-p98 stretch (gloss_black: the black photo's), composited on the photo backdrop.
All luma is measured on float composites (no 8-bit rounding before or after the stretch).

Measured on the facets bounded by C (the build's own outer polygons, posed and projected, restricted to the visible
temple body, eroded 10 px): the median |dL| (L8 = Rec. 709 luma of 8-bit sRGB) between plus and base over their
union, per photo and pass, on the stretched images and with gain only (raw). Design targets: >= 3 L8 native_linear,
>= 10 L8 gloss_black, at 05 and at 01 (information, never gates, WP6_FACETS §0). Also reported: the base render's
upper-crease step (median L8 of F2 minus F1 in 3-20 px bands either side of their shared edge) at 05 / 01 against
the photo's (and at black-side in gloss_black); the facet brightness order (Spearman of the per-region medians,
photo vs render: gloss_black at black-side, native at 05 / 01).

Rig tuning (studio_dome_v1, phase 1): ``--trial NAME`` renders a candidate rig through the selftest-only
``rig_spec_override``; every trial is appended to data/wp6v2/t2/trials.jsonl. Without ``--trial`` the rig is checked
and data/wp6v2/t2/report_<rig>.json written (the phase-1 data/wp6v2/t2/report.json is kept as it was).
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_rig_check [--rig photo_env] [--env PATH]
[--trial NAME ...]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage, stats

from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import blender
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import lighting as LT
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import project_mm

G3D = Path(__file__).resolve().parents[2]
REPO = G3D.parent
DATA = G3D / "data"
SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SCENE_SHA = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
SPEC = DATA / "wp6/pose_arm_resume_20261010/distal_all_views/before/spec.json"
SPEC_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
BASIS_JSON = DATA / "wp6v2/basis/cameras.json"
OUT = DATA / "wp6v2/t2"
V1_COMMIT = "62b25e4"
CHECK = ("reference-05", "reference-01")
PHOTOS = CHECK + ("black-side",)
NODE, DELTA_MM = "C", 1.0
TARGET = {"native_linear": 3.0, "gloss_black": 10.0}
PHOTO_STEP_L8 = (17.0, 18.0)          # the photo's upper-crease step quoted by the design (information)
ERODE_PX = 10
BAND_PX = (3.0, 20.0)
PASSES = ("native_linear", "gloss_black")
ENV_DEFAULT = DATA / "wp6v2/light_fit/job_original/work/compare/env-basis-01.json"

# Candidate rigs tried (overrides of DOME_SPEC, in order); every trial is logged to trials.jsonl.
TRIALS = {
    "design": {},
    "key_el35": {"key_elevation_deg": 35.0},
    "key_el70": {"key_elevation_deg": 70.0},
    "key_irr2": {"key_irradiance": 2.0},
    "key_small": {"key_size_radii": 1.0},
    "steep_ramp": {"ramp": [[-90.0, 0.45], [-6.0, 0.45], [-4.0, 0.12], [5.0, 0.12], [40.0, 1.0], [90.0, 1.0]]},
    "dim_world": {"world_strength": 0.6, "key_irradiance": 1.5},
    # The side cameras look down ~14 deg, so a vertical facet mirrors the floor at -14 deg and every facet tilted
    # down mirrors it lower still: a flat floor gives them all one reflection. A floor brightening toward the nadir
    # separates them (elevation-only still).
    "floor_grad": {"ramp": [[-90.0, 1.0], [-40.0, 0.9], [-10.0, 0.12], [10.0, 0.12], [60.0, 1.0], [90.0, 1.0]]},
    "floor_grad_b": {"ramp": [[-90.0, 1.0], [-30.0, 0.8], [-8.0, 0.12], [8.0, 0.12], [60.0, 1.0], [90.0, 1.0]]},
    "floor_grad_key05": {"ramp": [[-90.0, 1.0], [-40.0, 0.9], [-10.0, 0.12], [10.0, 0.12], [60.0, 1.0],
                                  [90.0, 1.0]], "key_irradiance": 0.5},
    # diagnostic: world radiance linear in sin(elevation), no key (shows where each facet's reflection points)
    "probe_linear": {"ramp": [[-90.0, 0.0], [90.0, 1.0]], "key_irradiance": 0.0},
    # probe_linear showed the facets bounded by C tilt UP a few degrees (their lower edge stands out), so at the side
    # cameras (14 deg down) they mirror the sky just above the horizon (about +4 deg), inside the design's flat dark
    # band; a vertical facet mirrors -14 deg. A ramp that is steep across -15..+25 deg makes their tilt show.
    "horizon_ramp": {"ramp": [[-90.0, 0.3], [-15.0, 0.08], [25.0, 1.0], [90.0, 1.0]]},
    "horizon_ramp_b": {"ramp": [[-90.0, 0.45], [-20.0, 0.45], [-12.0, 0.08], [20.0, 1.0], [90.0, 1.0]]},
    "horizon_ramp_c": {"ramp": [[-90.0, 0.2], [-10.0, 0.05], [15.0, 1.0], [90.0, 1.0]]},
    "horizon_ramp_d": {"ramp": [[-90.0, 0.45], [-20.0, 0.45], [-12.0, 0.06], [15.0, 1.0], [90.0, 1.0]]},
    "horizon_ramp_e": {"ramp": [[-90.0, 0.45], [-20.0, 0.45], [-10.0, 0.06], [12.0, 1.0], [90.0, 1.0]]},
    "horizon_ramp_b_key15": {"ramp": [[-90.0, 0.45], [-20.0, 0.45], [-12.0, 0.08], [20.0, 1.0], [90.0, 1.0]],
                             "key_irradiance": 1.5},
}

BUILD_BPY = r'''
import json, sys, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
src, spec_path, out_blend, out_json = argv
sys.path.insert(0, src)
import bpy
import glasses3d.g3d.temple_bpy as TBPY
from glasses3d.g3d.temple import build as TB
spec = json.load(open(spec_path, encoding="utf-8"))
res = {"module": TBPY.__file__}
try:
    rec = TBPY.faceted_temple(spec, side="both", decals="auto")
    res["status"] = rec.get("status")
    res["receipt"] = {k: rec.get(k) for k in ("status", "warnings", "code", "message", "error", "core_digest")
                      if k in rec}
    if rec.get("status") == "ok":
        tb = TB.build_temple(spec)
        res["sides"] = {}
        for s in (1, -1):
            W, polys, tris = tb.world(s, opening_deg=0.0)
            res["sides"][str(s)] = {"V": W.tolist(), "polys": polys, "kind": list(tb.poly_kind),
                                    "region": list(tb.poly_region)}
        bpy.ops.wm.save_as_mainfile(filepath=out_blend, copy=True)
except Exception:
    res["status"] = "failed"
    res["traceback"] = traceback.format_exc()
json.dump(res, open(out_json, "w", encoding="utf-8"))
'''


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def snapshot_v1() -> Path:
    """glasses3d/__init__.py and glasses3d/g3d at V1_COMMIT, extracted under OUT/v1src (the v1 builder)."""
    dst = OUT / "v1src"
    if dst.exists():
        shutil.rmtree(dst, onerror=_rw)
    tar = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", V1_COMMIT, "glasses3d/__init__.py",
                          "glasses3d/g3d"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dst, filter="data")
    return dst


def build(variant: str, spec: dict, src: Path) -> dict:
    d = OUT / variant
    d.mkdir(parents=True, exist_ok=True)
    seed = d / "scene_copy.blend"
    shutil.copy2(SCENE, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    assert sha(seed) == SCENE_SHA
    (d / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    script = OUT / "build_bpy.py"
    script.write_text(BUILD_BPY, encoding="utf-8")
    out_blend, out_json = d / f"{variant}.blend", d / "build.json"
    proc = blender.run(seed, script, [src, d / "spec.json", out_blend, out_json], timeout_s=600)
    if not out_json.is_file():
        raise RuntimeError(f"{variant}: the build wrote nothing: {proc.stderr[-2000:]}")
    res = json.loads(out_json.read_text(encoding="utf-8"))
    if res.get("status") != "ok":
        raise RuntimeError(f"{variant}: build {res.get('status')}: {res}")
    if not Path(res["module"]).resolve().is_relative_to(src.resolve()):
        raise RuntimeError(f"the build imported {res['module']}, not the v1 snapshot")
    res["blend"], res["blend_sha256"] = str(out_blend), sha(out_blend)
    return res


class Variant:
    """A BlenderSession on one built scene copy, its exported head and the basis."""

    def __init__(self, name: str, blend: Path, photos: dict):
        self.name = name
        self.root = OUT / f"job_{name}"
        if self.root.exists():
            shutil.rmtree(self.root, onerror=_rw)
        self.root.mkdir(parents=True)
        seed = self.root / "seed.blend"
        shutil.copy2(blend, seed)
        self.basis = C.import_cameras_json(self.root, BASIS_JSON, photos, reason="T2 rig check")
        self.s = BlenderSession(self.root, seed_blend=seed, selftest=True, calls_dir=self.root / "tools")
        self.s.start()
        self.see = Seeing(self.s)
        exp = self.see.export_head()
        assert exp["status"] == "exported", exp
        self.glb = exp["path"]
        self.mesh = self.see.fit_mesh(exp["path"], exp["sha256"], skip=self.basis.skip, frame=self.basis.frame)
        self.roles = PO.part_roles(exp["path"])
        b = self.see.bounds("t2-bounds")
        self.origin = np.asarray(b["origin_mm"] or (0.0, 0.0, 0.0), float)

    def render(self, views: list, rig_params: dict, override: dict | None, tag: str) -> dict:
        params = {"out_dir": str(self.root / "work" / "renders" / tag), "views": views, "engine": "eevee",
                  **rig_params}
        if override:
            params["rig_spec_override"] = override
        r = self.s.call_read("t2", "render", params, deadline_s=600, call_id=f"t2-{tag}")
        assert r["status"] == "ok", r
        assert r["result"]["unchanged"], "the render moved the scene"
        return r["result"]

    def close(self):
        self.s.close()


def facet_labels(build_side: dict, pose: dict, side: int, cam, frame, shape, visible: np.ndarray) -> dict:
    """{region: bool mask} of the build's outer polygons (posed, projected), on the visible temple body."""
    V = np.asarray(build_side["V"], float)
    p = PO.parse_pose(pose)
    th, axis = p.get(side, (0.0, (0.0, 0.0)))
    uv = project_mm(PO.rot_about_hinge(V, side, th, axis), cam, frame)
    H, W = shape
    masks = {}
    for poly, kind, region in zip(build_side["polys"], build_side["kind"], build_side["region"]):
        if kind != "outer":
            continue
        label = region.split("/")[0]
        if label not in masks:
            masks[label] = Image.new("L", (W, H), 0)
        ImageDraw.Draw(masks[label]).polygon([tuple(uv[i]) for i in poly], fill=255)
    return {k: (np.asarray(m) > 127) & visible for k, m in masks.items()}


def bands(f1: np.ndarray, f2: np.ndarray, band=BAND_PX) -> tuple[np.ndarray, np.ndarray]:
    """Pixels of f1 / f2 within band px of the other facet (either side of their shared edge)."""
    d2 = ndimage.distance_transform_edt(~f2)
    d1 = ndimage.distance_transform_edt(~f1)
    return f1 & (d2 >= band[0]) & (d2 <= band[1]), f2 & (d1 >= band[0]) & (d1 <= band[1])


def run(trial: str, override: dict, base: Variant, plus: Variant, builds: dict, spec: dict,
        rig: str = "studio_dome_v1", env: dict | None = None) -> dict:
    t0 = time.perf_counter()
    bs = base.basis
    lf = base.see._light_frame("t2-light")
    rig_params = {"rig": rig, "light_frame": lf} if rig != "photo_env" else \
        {"rig": rig, "env": LT.env_param(env), "linear_format": "float"}
    geo_gain, gain_views = Seeing.gain_views(bs, base.origin)
    crops, views = {}, list(gain_views)
    for rid in PHOTOS:
        g = Seeing._matched_geometry(bs, rid, base.mesh, base.roles)
        box = C.crop_box(g["temple"], bs.matte(rid))
        x0, y0, x1, y1 = box
        k = C.stack_scale(x1 - x0, y1 - y0, 4)
        ccam = BC.crop_camera(g["cam"], x0, y0, k)
        cshape = (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))
        crops[rid] = {"g": g, "box": box, "k": k, "cam": ccam, "shape": cshape}
        views.append({"name": rid, "camera": BC.to_blender(ccam, bs.frame, base.origin, cshape),
                      "passes": list(PASSES), "pose": g["pose"]})
    res = {v.name: v.render(views, rig_params, override, trial) for v in (base, plus)}
    files = {n: {vv["name"]: vv["files"] for vv in r["views"]} for n, r in res.items()}
    gains = Seeing.gains_from_renders(bs, base.mesh, base.roles, geo_gain, files["base"])
    fid, acetate = Seeing._acetate_ids(base.mesh, base.roles)
    bounded = [lab for lab, f in spec["facets"].items() if NODE in f["poly"]]
    out = {"trial": trial, "rig": rig, "override": override, "rig_spec": res["base"]["rig_spec"], "light_frame": lf,
           "env": None if env is None else {"weights_sha": env["weights_sha"], "path": env.get("path")},
           "gains": gains, "bounded_facets": bounded, "photos": {}}
    for rid in PHOTOS:
        c = crops[rid]
        side = 1 if bs.camera(rid).yaw > 0 else -1
        mp = c["g"]["mesh"]
        r = mp.scene().render(c["cam"], c["shape"], 1)
        part = np.full(c["shape"], -1, np.int64)
        part[r["mask"]] = mp.part[r["face_id"][r["mask"]]]
        body = PO.temple_body(base.mesh, side, base.roles)
        labels = facet_labels(builds["base"]["sides"][str(side)], c["g"]["pose"], side, c["cam"], bs.frame,
                              c["shape"], part == body)
        pc, mc = Seeing.photo_at(bs, rid, c["box"], c["k"], c["shape"])
        ac = C.erode_px(np.isin(part, acetate) & mc, 2)
        lo, hi = C.stretch_range(pc, ac)
        bg = C.photo_background(bs.photo(rid), bs.matte(rid))
        cw = bs.colourway(rid)
        gcw = gains["colourways"][cw]
        dark = bool(gcw["dark"])
        L, L_raw = {}, {}
        for vn in ("base", "plus"):
            for ps in PASSES:
                rgb, a = C.read_linear(files[vn][rid][ps]["path"])
                gain = gains["colourways"]["target"]["native_linear"]["gain"] if ps == "native_linear" else \
                    gains["gloss_black"]["gain"]
                s_lo, s_hi = (lo, hi) if ps == "native_linear" or dark else tuple(gains["gloss_black"]["stretch"])
                img = C.composite_render(rgb, a, gain, bg, as_float=True)
                L_raw[(vn, ps)] = C.luma8(img)
                L[(vn, ps)] = C.luma8(C.apply_stretch(img, s_lo, s_hi, as_float=True))
        er = {lab: C.erode_px(m, ERODE_PX) for lab, m in labels.items()}
        rec = {"side": side, "crop": {"box": list(c["box"]), "k": c["k"], "shape": list(c["shape"])},
               "stretch": [lo, hi], "facet_px": {lab: int(m.sum()) for lab, m in er.items()}, "dL": {}}
        union = np.zeros(c["shape"], bool)
        for lab in bounded:
            union |= er.get(lab, np.zeros(c["shape"], bool))
        for ps in PASSES:
            for kind, LL in (("stretched", L), ("gain_only", L_raw)):
                dl = LL[("plus", ps)] - LL[("base", ps)]
                rec["dL"][f"{ps}/{kind}"] = {
                    "union_median_abs": round(float(np.median(np.abs(dl[union]))), 3) if union.any() else None,
                    "per_facet_median": {lab: round(float(np.median(dl[er[lab]])), 3) for lab in bounded
                                         if lab in er and er[lab].any()}}
        if rid in CHECK and "F1" in labels and "F2" in labels:
            b1, b2 = bands(C.erode_px(labels["F1"], 1), C.erode_px(labels["F2"], 1))
            Lp = C.luma8(pc)
            Lps = C.luma8(C.apply_stretch(pc, lo, hi, as_float=True))
            rec["upper_crease_step_F2_minus_F1"] = {
                "photo_raw": round(float(np.median(Lp[b2]) - np.median(Lp[b1])), 2),
                "render_gain_only": round(float(np.median(L_raw[("base", "native_linear")][b2]) -
                                                np.median(L_raw[("base", "native_linear")][b1])), 2),
                "photo_stretched": round(float(np.median(Lps[b2]) - np.median(Lps[b1])), 2),
                "render_stretched": round(float(np.median(L[("base", "native_linear")][b2]) -
                                                np.median(L[("base", "native_linear")][b1])), 2),
                "band_px": [int(b1.sum()), int(b2.sum())]}
        if rid == "black-side":
            Lp = C.luma8(pc)
            regs = [lab for lab, m in er.items() if m.sum() >= 200]
            ph = [float(np.median(Lp[er[lab]])) for lab in regs]
            rn = [float(np.median(L_raw[("base", "gloss_black")][er[lab]])) for lab in regs]
            rho = stats.spearmanr(ph, rn).statistic if len(regs) >= 3 else None
            rec["facet_order"] = {"regions": regs, "photo_L8": [round(v, 1) for v in ph],
                                  "gloss_black_L8": [round(v, 1) for v in rn],
                                  "spearman": None if rho is None else round(float(rho), 3)}
        if rid == "black-side" and "F1" in labels and "F2" in labels:
            b1, b2 = bands(C.erode_px(labels["F1"], 1), C.erode_px(labels["F2"], 1))
            rec["upper_crease_step_F2_minus_F1"] = {
                "photo_raw": round(float(np.median(Lp[b2]) - np.median(Lp[b1])), 2),
                "render_gloss_gain_only": round(float(np.median(L_raw[("base", "gloss_black")][b2]) -
                                                      np.median(L_raw[("base", "gloss_black")][b1])), 2),
                "band_px": [int(b1.sum()), int(b2.sum())]}
        if rid in CHECK:
            Lp = C.luma8(pc)
            regs = [lab for lab, m in er.items() if m.sum() >= 200]
            ph = [float(np.median(Lp[er[lab]])) for lab in regs]
            rn = [float(np.median(L_raw[("base", "native_linear")][er[lab]])) for lab in regs]
            rho = stats.spearmanr(ph, rn).statistic if len(regs) >= 3 else None
            rec["facet_order_native"] = {"regions": regs, "photo_L8": [round(v, 1) for v in ph],
                                         "native_L8": [round(v, 1) for v in rn],
                                         "spearman": None if rho is None else round(float(rho), 3)}
        rec["images"] = save_sheet(trial, rid, L, union, labels, pc, lo, hi)
        out["photos"][rid] = rec
    nat = [out["photos"][r]["dL"]["native_linear/stretched"]["union_median_abs"] for r in CHECK]
    glo = [out["photos"][r]["dL"]["gloss_black/stretched"]["union_median_abs"] for r in CHECK]
    out["min_union_median_abs"] = {"native_linear": min(nat), "gloss_black": min(glo)}
    out["pass"] = {"native_linear": min(nat) >= TARGET["native_linear"],
                   "gloss_black": min(glo) >= TARGET["gloss_black"]}
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


def save_sheet(trial, rid, L, union, labels, pc, lo, hi) -> str:
    """photo | base native | plus native | |dL| native x4 // base gloss | plus gloss | |dL| gloss x4 (facets
    outlined)."""
    edge = np.zeros(union.shape, bool)
    for m in labels.values():
        edge |= C.cameras._edge(m)
    tiles = []
    for ps in PASSES:
        row = [np.repeat(C.luma8(C.apply_stretch(pc, lo, hi, as_float=True))[..., None], 3, -1)] \
            if ps == "native_linear" else []
        for vn in ("base", "plus"):
            row.append(np.repeat(L[(vn, ps)][..., None], 3, -1))
        d = np.abs(L[("plus", ps)] - L[("base", ps)]) * 4
        row.append(np.stack([np.clip(d, 0, 255), np.zeros_like(d), np.zeros_like(d)], -1))
        for t in row:
            t = np.clip(t, 0, 255).astype(np.uint8)
            t[edge] = (0, 200, 255)
            tiles.append(Image.fromarray(t))
    p = OUT / "sheets" / f"{trial}_{rid}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    C.grid(tiles[:4] + tiles[4:] + [Image.new("RGB", tiles[0].size, "white")], 4, max_px=2400).convert(
        "RGB").save(p)
    return str(p)


def main(trials: list[str] | None, rig: str = "studio_dome_v1", env_path: Path | None = None) -> dict:
    assert sha(SCENE) == SCENE_SHA and sha(SPEC) == SPEC_SHA and BASIS_JSON.is_file()
    env = None
    if rig == "photo_env":
        env_path = Path(env_path or ENV_DEFAULT)
        env = {**json.loads(env_path.read_text(encoding="utf-8")), "path": str(env_path),
               "file_sha256": sha(env_path)}
        assert not trials, "trials tune the dome rig only"
    OUT.mkdir(parents=True, exist_ok=True)
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    plus_spec = copy.deepcopy(spec)
    plus_spec["nodes"][NODE][2] = float(plus_spec["nodes"][NODE][2]) + DELTA_MM
    t0 = time.perf_counter()
    src = snapshot_v1()
    builds = {"base": build("base", spec, src), "plus": build("plus", plus_spec, src)}
    doc = json.loads(BASIS_JSON.read_text(encoding="utf-8"))
    photos = {n: Path(r["image"]) for n, r in doc["images"].items()}
    base = Variant("base", Path(builds["base"]["blend"]), photos)
    plus = Variant("plus", Path(builds["plus"]["blend"]), photos)
    results = []
    try:
        for name in (trials or [f"frozen-{rig}"]):                     # sheets/<name>_<photo>.png per rig
            override = TRIALS.get(name, {}) if not name.startswith("frozen") else None
            r = run(name, override, base, plus, builds, spec, rig, env)
            results.append(r)
            with open(OUT / "trials.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({k: r[k] for k in ("trial", "override", "rig_spec", "min_union_median_abs",
                                                       "pass", "seconds")}, default=C._json_default) + "\n")
            print(name, r["min_union_median_abs"], r["pass"], flush=True)
    finally:
        base.close()
        plus.close()
    rep = {"inputs": {"scene": str(SCENE), "scene_sha256": SCENE_SHA, "spec": str(SPEC), "spec_sha256": SPEC_SHA,
                      "basis": str(BASIS_JSON), "basis_sha256": sha(BASIS_JSON), "v1_builder_commit": V1_COMMIT},
           "builds": {k: {kk: v.get(kk) for kk in ("status", "blend", "blend_sha256", "module")}
                      for k, v in builds.items()},
           "node": NODE, "delta_mm": DELTA_MM, "targets": TARGET, "photo_step_quoted": PHOTO_STEP_L8,
           "rig": rig, "env": None if env is None else {k: env.get(k) for k in ("path", "file_sha256", "weights",
                                                                                "weights_sha", "lambda")},
           "results": results, "seconds": round(time.perf_counter() - t0, 1)}
    if not trials:
        C.write_json(OUT / f"report_{rig}.json", rep)
    else:
        C.write_json(OUT / "trials_last.json", rep)
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", action="append", help=f"candidate rig(s): {list(TRIALS)}")
    ap.add_argument("--rig", default="studio_dome_v1", choices=("studio_dome_v1", "photo_env"))
    ap.add_argument("--env", default=None, help="a frozen work/compare/env-<basis>.json (photo_env)")
    a = ap.parse_args()
    main(a.trial, a.rig, a.env)
