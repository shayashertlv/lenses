"""WP5 correction round, task item 1: the AR render's frame levels against the photos' (graded by the independent
instrument ``tests/instruments/print_metrics.py``: ``lstar``, ``ar_frame_mask``, ``level_compare``).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.ar_levels CAPTURE_DIR [CAPTURE_DIR ...]
        [--probe PROBE_DIR] [--probe-for CAPTURE_DIR=PROBE_DIR ...] [--pairs auto|pitch_p20:05,...]
        [--tradeoff auto|none|front:05,...] [--heldout auto|none|pitch_m20:05,...] [--out JSON]

AR frame pixels: an arview capture's (``arview capture --poses all --backgrounds skin`` or ``--poses default``)
part-id render of the pose, the print parts' palette colours (q001: Brow_Left, Brow_Right, Temple_Left,
Temple_Right), eroded 1 px, read from the pose's skin beauty render. Photo frame pixels: the same print parts rendered
(first hit of the whole scene, decals included) at the photo's WP3b camera, eroded 2 px (registration and
anti-aliasing), inside the photo's matte eroded 2 px.

**Pose-photo pairs (fix round 1 of the correction round).** The correction round fitted and graded the level on the
same two pairs (front vs photo 05, yaw -30 vs photo 01), both at zero elevation, while photos 05 and 01 are taken
18 deg from above; at the photos' own elevation (pitch +20 vs 05, yaw -30 pitch +20 vs 01: the review's held-out
pairs) the calibrated print rendered 5-6 L* darker than the photos. The pairs are now derived from the geometry:
each arview pose's camera direction in the model frame (Euler YXZ, poses.ts: yaw > 0 brings the -x side near,
pitch > 0 looks from above: (-sin yaw, cos yaw sin pitch, cos yaw cos pitch)) against each photo's WP3b camera
direction, each pose paired with its nearest photo.

**Roles (fix round 2 of the correction round).** Fix round 1 called every pair within 20 deg "matched" and calibrated
on all four; only two of them are at a photo's own camera. Now:
- ``photo_angle`` (within ``CLOSE_DEG``, 5 deg): the pose IS the photo's view: pitch_p20:05 (1.6 deg) and
  yaw_m30_pitch_p20:01 (1.6). These are the calibration pairs (role ``calibration``).
- ``near`` (within ``MATCH_DEG``, 20 deg): front:05 (18.4) and yaw_m30:01 (18.1), the zero-elevation views the photos
  were taken 18 deg above. They are graded every time as role ``trade-off``: the same photo seen from another
  direction, so their gap to the calibration rows is the view dependence (``view_dependence``), never a photo angle.
- ``loose`` (within ``LOOSE_DEG``, 30 deg): yaw_m45:01 (23.3), yaw_p45:03 (28.8), role ``held-out``.
``--pairs auto`` / ``--tradeoff auto`` / ``--heldout auto`` take them from the geometry; every verdict is reported per
role, never pooled. Per capture, ``view_dependence`` gives, for each photo with a calibration pair, every other pose's
residual minus the calibration pose's (per form; the photo side is the same photo, so in the ``frame`` and ``print``
forms it is the AR side's own change with the view), and the brows' and temples' AR L* (print-form pixels) apart.

Three forms, every one reported (the verdict of task item 1: AR p10 and p50 L* within 2 of the photo's):
- ``frame``: every frame pixel, unweighted (the form of the 10bf932 tool test's numbers);
- ``matched``: the photo's pixels weighted so brows and temples have the AR pose's shares (photo 05 shows the temple
  tops at a grazing angle, 1/3 of its frame pixels and much lighter, L* p50 41.5 against the brows' 22.0; the front pose
  hides them behind the head: 4 % of its frame pixels);
- ``print`` (the CALIBRATION TARGET): ``matched`` with highlights left out on both sides. Photo side: the photo's own
  achromatic pixels (L* > 35 and C*ab < 10: the studio's reflections, fixed per photo). AR side (fix round 1): the
  pixels a PROBE capture shows as achromatic highlights: the same GLB with every print-part base-colour image black
  (``ar_calibrate.black_probe``), captured at the same poses: the renderer's reflections alone, identical for every
  candidate print (the part-id renders must be identical, checked). The correction round took the AR side from the AR
  pixels' own colours and called it the renderer's: it moved with the print (front pose 42.1 % with the 10bf932
  print, 33.7 % with the calibrated one, 28.6 % with Astra's texture; the black probe 29.8 %), so light, grey print
  pixels left the target with the reflections. Without a probe the pixel rule is used and the output says so.
Sides are never named here; the pairs are the geometry's.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from glasses3d.g3d_host import raster
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests.instruments import print_metrics as PM

PRINT_PARTS = tuple(IN.FT1119_PARTS)
GROUPS = {"brow": ("Brow_Left", "Brow_Right"), "temple": ("Temple_Left", "Temple_Right")}
PHOTO_ERODE = 2
FORMS = ("frame", "matched", "print")
TARGET = "print"
MATCH_DEG = 20.0                # within: role 'near' (graded as a view-dependence trade-off)
LOOSE_DEG = 30.0                # within: role 'loose' (held-out)
CLOSE_DEG = 5.0                 # within: role 'photo_angle', the pose IS the photo's view (the calibration pairs)
ROLE_OF = {"photo_angle": "calibration", "near": "trade-off", "loose": "held-out"}
ROLES = ("calibration", "trade-off", "held-out")
# arview's pose ids (arview/qa/capture/poses.ts DEFAULT_POSE_ANGLES + JUNCTION_POSE_ANGLES)
POSE_ANGLES = ((0, 0), (30, 0), (-30, 0), (45, 0), (-45, 0), (0, 20), (0, -20), (30, 20), (30, -20), (-30, 20),
               (-30, -20))
PREVIOUS_PAIRS = (("front", "05"), ("yaw_m30", "01"))      # the correction round's calibration pairs


def pose_id(yaw: float, pitch: float) -> str:
    sg = lambda v: f"{'m' if v < 0 else 'p'}{abs(int(v))}"
    parts = ([f"yaw_{sg(yaw)}"] if yaw else []) + ([f"pitch_{sg(pitch)}"] if pitch else [])
    return "_".join(parts) or "front"


def pose_direction(yaw: float, pitch: float) -> np.ndarray:
    """Unit direction from the glasses toward the camera, in the model frame, of an arview pose (module docstring)."""
    y, p = math.radians(yaw), math.radians(pitch)
    return np.array([-math.sin(y), math.cos(y) * math.sin(p), math.cos(y) * math.cos(p)])


def photo_direction(job, view: str) -> np.ndarray:
    t = np.asarray(raster.camera_basis(job.views[view].camera)[2], float)
    return t / np.linalg.norm(t)


def pair_table(job) -> list[dict]:
    """Per arview pose: the nearest photo, the angle between the two camera directions, and its role (photo_angle /
    near / loose / None)."""
    views = sorted(job.views)
    rows = []
    for yaw, pitch in POSE_ANGLES:
        d = pose_direction(yaw, pitch)
        ang = {v: math.degrees(math.acos(float(np.clip(d @ photo_direction(job, v), -1, 1)))) for v in views}
        v = min(ang, key=ang.get)
        role = ("photo_angle" if ang[v] <= CLOSE_DEG else "near" if ang[v] <= MATCH_DEG
                else "loose" if ang[v] <= LOOSE_DEG else None)
        rows.append({"pose": pose_id(yaw, pitch), "photo": IN.short_name(v), "angle_deg": round(ang[v], 1),
                     "role": role})
    return rows


def auto_pairs(job, role: str) -> tuple:
    """The (pose, photo) pairs of a ``pair_table`` role (photo_angle / near / loose)."""
    if role not in ROLE_OF:
        raise ValueError(f"unknown pair role {role!r} (one of {sorted(ROLE_OF)})")
    return tuple((r["pose"], r["photo"]) for r in pair_table(job) if r["role"] == role)


def _occ(job):
    from glasses3d.g3d_host.photoprint import project as PR
    from glasses3d.g3d_host.photoprint import texels as TX
    return TX.OcclusionScene(job, job.part_ids(PRINT_PARTS), TX.decal_geometry(PR.job_glb(job), job.mesh.skipped))


def photo_frame_mask(job, view: str, occ=None, parts=PRINT_PARTS) -> np.ndarray:
    """(H, W) bool: the photo's pixels of ``parts`` (first hit at the WP3b camera, eroded) inside its matte (eroded)."""
    v = job.views[view]
    occ = occ or _occ(job)
    fid = occ.scene(job.mesh.V).render(v.camera, v.shape, 1)["face_id"]
    nf = len(job.mesh.F)
    part = np.where((fid >= 0) & (fid < nf), job.mesh.part[np.clip(fid, 0, nf - 1)], -1)
    pr = ndimage.binary_erosion(np.isin(part, job.part_ids(list(parts))), iterations=PHOTO_ERODE)
    return pr & ndimage.binary_erosion(np.asarray(v.matte, bool), iterations=PHOTO_ERODE)


def photo_pixels(job, view: str, occ=None):
    """(pixels (N, 3), group label (N,)) of the photo's frame pixels, brows 0 / temples 1."""
    v = job.views[view]
    px, gr = [], []
    for k, (_g, parts) in enumerate(GROUPS.items()):
        m = photo_frame_mask(job, view, occ, parts)
        px.append(v.image[m])
        gr.append(np.full(int(m.sum()), k))
    return np.concatenate(px), np.concatenate(gr)


def _masks(cap: Path, pose: str):
    rep = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(cap / f"{pose}__part-id.png").convert("RGB"))
    return pid, [PM.ar_frame_mask(pid, rep["id_palette"], parts) for parts in GROUPS.values()]


def capture_pixels(cap: Path, pose: str, probe: Path | None = None):
    """(pixels, group label, highlight flags or None) of a capture's frame pixels at ``pose``; the flags are the
    probe capture's achromatic highlights on the same pixels (its part-id render must equal the capture's)."""
    cap = Path(cap)
    pid, masks = _masks(cap, pose)
    beauty = np.asarray(Image.open(cap / f"{pose}__skin__beauty.png").convert("RGB"))
    hl = None
    if probe is not None:
        ppid, pmasks = _masks(Path(probe), pose)
        if not (np.array_equal(ppid, pid) and all(np.array_equal(a, b) for a, b in zip(masks, pmasks))):
            raise ValueError(f"{probe}: part-id render at {pose} differs from {cap}'s: not a probe of the same GLB")
        pb = np.asarray(Image.open(Path(probe) / f"{pose}__skin__beauty.png").convert("RGB"))
        hl = np.concatenate([PM.achromatic_highlight(pb[m]) for m in masks])
    px = np.concatenate([beauty[m] for m in masks])
    gr = np.concatenate([np.full(int(m.sum()), k) for k, m in enumerate(masks)])
    return px, gr, hl


def ar_groups(pa, ga, ha) -> dict:
    """Per group (brow / temple): share and L* p10 / p50 of the AR's print-form pixels (highlights left out)."""
    keep = ~(np.asarray(ha, bool) if ha is not None else PM.achromatic_highlight(pa))
    out = {}
    for k, g in enumerate(GROUPS):
        m = keep & (np.asarray(ga) == k)
        L = PM.lstar(pa[m]) if m.any() else np.zeros(0)
        out[g] = {"share": round(float(m.sum() / max(int(keep.sum()), 1)), 4),
                  "p10": round(float(np.percentile(L, 10)), 2) if L.size else None,
                  "p50": round(float(np.percentile(L, 50)), 2) if L.size else None}
    return out


def compare(ar, photo) -> dict:
    (pa, ga, ha), (pp, gp) = ar, photo
    return {"frame": PM.level_compare(pa, pp),
            "matched": PM.level_compare(pa, pp, ar_group=ga, photo_group=gp),
            "print": PM.level_compare(pa, pp, ar_group=ga, photo_group=gp, exclude_highlights=True,
                                      ar_highlight=ha),
            "ar_groups": ar_groups(np.asarray(pa, np.float64), ga, ha)}


def view_dependence(rows: dict) -> dict:
    """For each photo with a calibration pair: every other graded pose's residual minus the calibration pose's
    ([p10, p50] per form) and its AR brow / temple L* p50 minus the calibration pose's. The photo is the same, so in
    the ``frame`` and ``print`` forms this is the AR render's own change with the view (``matched`` re-weights the
    photo to each pose's shares)."""
    graded = {k: r for k, r in rows.items() if isinstance(r, dict) and r.get("role") in ROLES and "missing" not in r}
    out = {}
    for kc, rc in graded.items():
        if rc["role"] != "calibration":
            continue
        pc, s = kc.split("_vs_")
        for k, r in graded.items():
            p, s2 = k.split("_vs_")
            if s2 != s or k == kc:
                continue
            out[f"{p} - {pc} (vs {s})"] = {
                "role": r["role"],
                **{f: [round(r[f]["ar_minus_photo"][q] - rc[f]["ar_minus_photo"][q], 2) for q in ("p10", "p50")]
                   for f in FORMS},
                "ar_group_p50": {g: (round(r["ar_groups"][g]["p50"] - rc["ar_groups"][g]["p50"], 2)
                                     if r["ar_groups"][g]["p50"] is not None and rc["ar_groups"][g]["p50"] is not None
                                     else None) for g in GROUPS}}
    return out


def key(pose: str, short: str) -> str:
    return f"{pose}_vs_{short}"


def measure(captures, pairs=None, heldout=None, job=None, probes: dict | None = None, photos: dict | None = None,
            tradeoff=None) -> dict:
    """Rows per capture and pair (``pairs``: role 'calibration', default the photo_angle pairs of ``pair_table``;
    ``tradeoff``: role 'trade-off', default its near pairs; ``heldout``: role 'held-out', default its loose pairs).
    ``probes``: {capture dir: probe dir} (str keys). ``photos``: a cache of ``photo_pixels`` by photo short name
    (filled here)."""
    job = job or IN.load_job()
    table = pair_table(job)
    pairs = tuple(map(tuple, pairs)) if pairs is not None else auto_pairs(job, "photo_angle")
    tradeoff = tuple(map(tuple, tradeoff)) if tradeoff is not None else auto_pairs(job, "near")
    heldout = tuple(map(tuple, heldout)) if heldout is not None else auto_pairs(job, "loose")
    angle = {(r["pose"], r["photo"]): r["angle_deg"] for r in table}
    photos = {} if photos is None else photos
    occ = None
    for _p, s in pairs + tradeoff + heldout:
        if s not in photos:
            occ = occ or _occ(job)
            photos[s] = photo_pixels(job, f"reference-{s}.png", occ)
    probes = {str(Path(k)): v for k, v in (probes or {}).items()}
    out = {"pairs": [list(p) for p in pairs], "tradeoff": [list(p) for p in tradeoff],
           "heldout": [list(p) for p in heldout], "pair_table": table, "close_deg": CLOSE_DEG,
           "match_deg": MATCH_DEG, "loose_deg": LOOSE_DEG, "tol": PM.LEVEL_TOL, "target_form": TARGET,
           "groups": list(GROUPS),
           "photo_frame_stats": {s: PM.level_stats(photos[s][0]) for _p, s in pairs + tradeoff + heldout},
           "captures": {}}
    for cap in captures:
        cap = Path(cap)
        probe = probes.get(str(cap))
        rows = {}
        for role, plist in (("calibration", pairs), ("trade-off", tradeoff), ("held-out", heldout)):
            for pose, short in plist:
                if not (cap / f"{pose}__part-id.png").is_file():
                    rows[key(pose, short)] = {"role": role, "missing": f"no {pose} render in {cap}"}
                    continue
                r = compare(capture_pixels(cap, pose, Path(probe) if probe else None), photos[short])
                r["role"] = role
                r["angle_deg"] = angle.get((pose, short))
                rows[key(pose, short)] = r
        vd = view_dependence(rows)
        rows["pass"] = {role: {f: all(r[f]["pass"] if f in r else False      # a missing render never passes
                                      for r in rows.values() if r.get("role") == role)
                               for f in FORMS} for role in ROLES}
        rows["view_dependence"] = vd
        rows["probe"] = str(probe) if probe else None
        out["captures"][str(cap)] = rows
    return out


def residuals(row: dict, form: str = TARGET, pairs=None) -> list[float]:
    """[p10, p50] AR - photo per pair, in ``pairs`` order (default: the row's calibration pairs in insertion order)."""
    if pairs is None:
        pairs = [tuple(k.split("_vs_")) for k, r in row.items() if isinstance(r, dict) and r.get("role") == "calibration"]
    return [row[key(p, s)][form]["ar_minus_photo"][q] for p, s in pairs for q in ("p10", "p50")]


def table(res: dict, names: dict | None = None) -> str:
    """A plain-text table: per capture and pair, AR - photo L* p10 / p50 in every form (and the highlight shares),
    then the view dependence (other pose minus the photo-angle pose, same photo)."""
    lines = []
    for cap, rows in res["captures"].items():
        lines.append(f"== {names.get(cap, cap) if names else cap}  (probe: {rows.get('probe')})")
        for k, r in rows.items():
            if not isinstance(r, dict) or "role" not in r or "missing" in r:
                continue
            cells = [f"{f} {r[f]['ar_minus_photo']['p10']:+.2f}/{r[f]['ar_minus_photo']['p50']:+.2f}" for f in FORMS]
            hs = r["print"].get("highlight_share") or {}
            lines.append(f"  {r['role']:<11} {k:<26} ({r['angle_deg']} deg)  " + "  ".join(cells)
                         + f"  hl ar {hs.get('ar')} photo {hs.get('photo')}")
        for k, v in (rows.get("view_dependence") or {}).items():
            cells = [f"{f} {v[f][0]:+.2f}/{v[f][1]:+.2f}" for f in FORMS]
            lines.append(f"  view dep.   {k:<40} " + "  ".join(cells) + f"  AR p50 by group {v['ar_group_p50']}")
    return "\n".join(lines)


def _pairs_arg(s: str | None, job, role: str):
    if s is None or s == "auto":
        return auto_pairs(job, role)
    if s == "none":
        return ()
    return tuple(tuple(x.split(":")) for x in s.split(","))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--probe", default=None, help="the black-albedo probe capture of the captures' GLB")
    ap.add_argument("--probe-for", action="append", default=[], help="CAPTURE_DIR=PROBE_DIR (overrides --probe)")
    ap.add_argument("--pairs", default="auto", help="calibration pairs (auto: the photo_angle pairs)")
    ap.add_argument("--tradeoff", default="auto", help="trade-off pairs (auto: the near pairs, 18 deg off)")
    ap.add_argument("--heldout", default="auto", help="held-out pairs (auto: the loose pairs)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    job = IN.load_job()
    probes = {c: a.probe for c in a.captures} if a.probe else {}
    probes.update(dict(x.split("=", 1) for x in a.probe_for))
    res = measure(a.captures, _pairs_arg(a.pairs, job, "photo_angle"), _pairs_arg(a.heldout, job, "loose"), job=job,
                  probes=probes, tradeoff=_pairs_arg(a.tradeoff, job, "near"))
    txt = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    print(table(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
