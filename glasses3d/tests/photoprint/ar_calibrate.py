"""WP5 correction round, task item 1: calibrate the print's AR level TO THE PHOTOS (the goal is to look like them in AR).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.ar_calibrate --bake DIR --glb TOOLTEST.glb
        [--out DIR] [--max-iter 6] [--no-candidate-portraits]
    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.ar_calibrate --capture GLB --out DIR [--portrait]
        [--coat-off-probe]
        (one GLB captured at every pose with its black probe, graded by ar_levels: DIR/capture, DIR/probe,
        DIR/ar_levels.json; --coat-off-probe: also the black probe without the print parts' clearcoat,
        DIR/probe_nocoat, for the material note's attribution)
    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.ar_calibrate --regrade CALIBRATION_DIR --out JSON
        [--tags TAG,...] [--workers N]
        (fix round 3 of step 6: the run's stored captures graded again by the current instrument; no capture, no fit)

Method (written down here and in glasses3d/plan/archive/20261019/PORT_REVIEW.md section 10):
1. The bake's print.png is photo radiance after the gains (``project_photo_texture(level=None)``; flatten as the bake
   decided). A candidate level (gain, gamma) maps every textured texel through ``shading.level_map``
   (rgb_lin x gain x Y^(gamma - 1); the same per-texel map the bake applies last, so the calibrated bake's print.png
   equals the candidate image up to rounding).
2. The candidate image replaces the print image inside a COPY of the tool-test GLB (the q001 copy that
   ``g3d.print_uv`` + ``g3d.attach_print`` painted; only the image bytes and the buffer layout change: a TOOL TEST,
   never a delivered model) and arview captures it (``arview capture --poses all --backgrounds skin --no-portrait``:
   the runtime's own renderer, lighting and tone mapping on the synthetic face, all 11 poses; the node process is
   opted out of EcoQoS like every pool worker).
3. ONE highlight mask for every candidate (fix round 1): the tool-test GLB with its print image black
   (``black_probe``) is captured once at the same poses (the part-id renders are identical: same geometry, same fit);
   its achromatic pixels are the renderer's reflections alone and are left out of every candidate's ``print`` form.
4. ``ar_levels`` grades every capture in three roles (fix round 2 of the correction round; fix round 1 calibrated on
   all four pairs within 20 deg, two of which are 18 deg off the photo's camera):
   - calibration: the ``photo_angle`` pairs, the pose within 5 deg of a photo's own camera (pitch +20 vs 05,
     yaw -30 pitch +20 vs 01, both 1.6 deg);
   - trade-off: the ``near`` pairs (front vs 05 18.4 deg, yaw -30 vs 01 18.1 deg): the same photo from 18 deg below
     its camera, graded every time, never a photo angle: their gap to the calibration rows is the view dependence;
   - held-out: the ``loose`` pairs (yaw -45 vs 01, yaw +45 vs 03);
   L* darkest decile (p10) and median (p50) in the ``print`` form; ``frame`` and ``matched`` beside it.
5. Search: a coarse grid (``GRID``: gain x gamma), then per TARGET a trust-region minimax between the captured points:
   the target's residuals (2 per pair) are fitted by planes in (log gain, gamma), least squares over the ``K_NEAR``
   captures nearest the target's best one (weighted 1 / (d + 0.25), d in grid steps); the minimax point of the planes
   inside a box of ``radius`` grid steps around the best (and inside ``search_box``: the grid's box extended by one
   grid step, as the earlier plane steps were bounded) is captured next. The box grows (x 1.5, at most 1) after a
   step that improves the target's worst residual, halves after one that does not or when the model points back at a
   captured point; it stops when the worst residual is within ``STOP_TOL`` L*, the box is below ``MIN_RADIUS`` or
   after ``--max-iter`` captures. Targets: ``photo_angles`` (the calibration pairs: the level chosen and baked) and
   ``near_18deg`` (the trade-off pairs: the correction round's zero-elevation target, an owner candidate). Every
   choice is a CAPTURED point (never a model prediction).
6. Reported with it, from the captured points only: per calibration pair, the level that would be chosen WITHOUT that
   pair and its residual on it (leave-one-pair-out), and the owner's candidates (``candidates``: ``photo_angles``,
   delivered, and ``near_18deg``), each with its residuals at every pair of every role, its worst per role and a
   label for the owner sheet. Unless ``--no-candidate-portraits``, each candidate's GLB is captured again WITH the
   worn portrait (``sheet_capture``; its graded numbers must equal the calibration capture's, checked) so the owner
   compares the levels in the worn view too (``tooltest_owner_sheet --calibration``; that sheet script was deleted on
   2026-10-13, see git tag review-20261013-pre-cleanup).
7. **Contrast and chroma (step 6, 2026-10-07).** The task: calibrate contrast and chroma against the photos at
   their own angles, not only the level. (a) Contrast: the ``photo_angles`` target is the role ``appearance``: the
   calibration pairs' L* p10 / p50 residuals AND, per pair, the contrast term 2 x ln(std ratio) / ln(1.18) (the L*
   standard deviation of the print-form pixels, AR over photo, ``ar_levels.contrast_ratios``; a ratio at the edge of
   ``print_metrics.CONTRAST_RATIO`` counts like a 2 L* residual), minimaxed together by the same trust-region search
   (the passing L* levels form a ridge along which the contrast changes: 1.12 at gain 0.75 / gamma 1.00 to 1.24 at
   1.20 / 1.15 on photo 05's pair). (b) Chroma (fix round 1 of step 6): the level map has two more parameters,
   ``sat`` and ``chroma_power`` (``shading.level_map``: each colour's distance from grey at its own luminance, x
   sat x (C*ab / ``shading.CHROMA_REF``)^(chroma_power - 1); Y unchanged; measured: they move neither the L* residuals
   nor the contrast by more than 0.15). Step 6 fitted ``sat`` alone to the C* MEDIAN: sat 1.322 matched it (5.80 /
   5.83 at photo 05's pair) and overshot the saturated tail by 1.4x (p90 26.06 against 18.54; at sat 1 the p90 was
   already +1.1 and only the median low), because one factor scales every colour alike while the renderer widens
   the tail and the level greys the low end. Now steps 1-6 run at (sat, power) = (1, 1); then, at the chosen
   (gain, gamma), the calibration pairs' C*ab p50, p90 and mean (``ar_levels.chroma_units``: each in units of the
   instrument's own window, ``print_metrics.chroma_units``) are captured at ``CHROMA_PROBES``; ln of each AR
   statistic is fitted by a plane in (ln sat, power) (weighted 1 / (d + 0.25) around the best captured point) and
   the minimax point of the six unit residuals inside ``SAT_BOX`` x ``POWER_BOX`` is captured; repeated until the
   captured worst is within ``CHROMA_STOP`` or ``CHROMA_MAX_ITER`` captures; the captured point with the smallest
   worst unit is the level chosen (pass: every unit within 1, i.e. |C* p50 difference| <= ``CHROMA_TOL`` and the
   p90 and mean ratios within ``CHROMA_SPREAD_RATIO``). The 0.5-3 mm band energy (``ar_levels``) is reported per pair,
   never fitted. The near-18 deg candidate is captured again at that chroma so the owner compares levels of the same
   chroma. The stage and the candidates' assembly are pure (``finish``, ``chroma_stage``, ``assemble_candidates``;
   tested offline on synthetic captures).
   (c) Fix round 2 of step 6. The review found that fix round 1's chroma level (sat 1.107, power 0.745), fitted on the
   two photo-angle pairs alone, left the saturated tail short on the four other graded pairs (C* p90 AR / photo
   0.74-0.93, three of four outside 0.85-1.18; yaw +45 vs 03, an owner complaint, 0.74) and that its power greyed the
   texture's honey spots (factor 0.88 at C* 25, 0.78 at 40) and amplified off-hue noise. Now: the chroma family is
   ``CHROMA_FAMILY`` (``shading.chroma_factor``: the factor held at >= 1, acting only inside the havana hue band), and
   the stage reports, for every capture, its worst unit over the calibration pairs AND over every graded pair
   (``ALL_GRADED``: calibration, trade-off and held-out), then runs a second minimax over every graded pair from the
   same captures (``chroma_stage(..., roles=ALL_GRADED)``). The level delivered is still the photo-angle choice; the
   all-pairs choice is an owner candidate (``all_pairs_chroma``) on the owner sheet, so the owner chooses between a
   level that matches the photos at their own angles and one that splits the view dependence over all six pairs.
   Every candidate also carries its per-group (brow / temple) L* and chroma residuals (``ar_levels.by_group``) and its
   hue terms (``print_metrics`` ``appearance['hue']``).
   (d) Fix round 3 of step 6. The final review found that the all-pairs minimax of (c) was set by one held-out row,
   yaw +45 vs photo 03 (its worst unit 1.236 is that row's C* mean, balanced against the calibration p50 +1.17), and
   that the row failed on its BROW group, graded against photo 03's side-view brow pixels (0.090 of its print-form
   pixels) weighted up to the pose's 0.486. The instrument now leaves a group the photo shows below
   ``ar_levels.MIN_PHOTO_GROUP_SHARE`` out of every verdict and of the ``print`` form this stage fits
   (``ar_levels.compare``), so a future calibration fits without it. Nothing was refitted: the delivered level and
   the candidates are unchanged, and ``--regrade`` re-grades a run's stored captures with the current instrument
   (no capture, no fit). The chroma probes are captured at the same time (``capture_probes``).
Every capture's numbers are written to ``calibration.json`` (``iterations``; ``calibration_partial.json`` after each
capture while it runs), with the chosen level, which the bake reads (``bake_ft1119 --level FILE``). The pass rule
(task item 1) is |p10| and |p50| differences <= 2 L*; a pass is reported per role, never pooled.
The top-level level remains the fitted recommendation. An owner production_selection in calibration.json takes
precedence when the bake reads it; calibration must use a new output directory rather than overwrite that choice.
"""
from __future__ import annotations

from glasses3d.tests.fixtures.legacy_ft1119 import PRINT_PARTS as FT1119_PARTS

import argparse
import hashlib
import io
import json
import math
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests.fixtures import legacy_ft1119
from glasses3d.g3d_host.photoprint import shading as SH
from glasses3d.g3d_host.photoprint.procs import Progress, opt_out_ecoqos, pool_map
from glasses3d.tests.photoprint import ar_levels as AL

G3D = Path(__file__).resolve().parents[2]
ARVIEW = G3D / "arview"
STOP_TOL = 1.0
NO_TEXTURE = 10           # classes.png code of a part left without texture (never levelled)
GRID = ((0.55, 0.75, 0.95), (0.85, 1.0, 1.15))
PRINT_PART_NAMES = tuple(FT1119_PARTS)
LOG_UNIT = math.log(0.95 / 0.75)   # one grid step in log gain (the search's distance unit) ...
GAMMA_UNIT = 0.15                  # ... and in gamma
K_NEAR = 6
MIN_RADIUS = 0.06
TARGETS = {"photo_angles": "appearance", "near_18deg": "trade-off"}
ROLE_PAIRS = {"appearance": "calibration"}       # the role whose pose-photo pairs a target grades
CHROMA_PROBES = ((1.25, 1.0), (1.0, 0.7), (1.25, 0.7))   # (sat, chroma_power) captured at the chosen (gain, gamma)
SAT_BOX = (0.8, 2.2)               # ... and the range the chosen sat ...
POWER_BOX = (0.4, 1.2)             # ... and chroma power may take
CHROMA_UNIT_SAT = math.log(1.25)   # the chroma stage's distance unit in ln sat ...
CHROMA_UNIT_POWER = 0.3            # ... and in power
CHROMA_STOP = 0.5                  # the stage stops when the captured worst chroma unit is within this ...
CHROMA_MAX_ITER = 4                # ... or after this many captures past the probes
CHROMA_FAMILY = {"chroma_hold": True, "chroma_hue": list(legacy_ft1119.HAVANA_HUE_DEG)}  # historical fixture calibration
ALL_GRADED = ("calibration", "trade-off", "held-out")   # every graded role: the all-pairs chroma minimax
CONTRAST_UNIT = math.log(1.18) / 2.0     # step 6: ln(std ratio) per "L* unit" of the appearance target
LABELS = {"photo_angles": "level calibrated at the photos' own angles (pitch +20 vs 05, yaw -30 pitch +20 vs 01; "
                          "L*, contrast and chroma)",
          "near_18deg": "level best at front / yaw -30, 18 deg below photos 05 / 01 (a view-dependence trade-off)",
          "all_pairs_chroma": "the delivered L* level with its chroma fitted on all six graded pairs (photo angles, "
                              "18 deg and 23-29 deg off; an owner alternative)"}


# ----------------------------------------------------------------------------- GLB image swap (tool-test copies only)
def _chunks(data: bytes):
    magic, ver, total = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and ver == 2, "not a GLB 2.0"
    off, js, binc = 12, None, b""
    while off < total:
        ln, typ = struct.unpack_from("<II", data, off)
        body = data[off + 8:off + 8 + ln]
        if typ == 0x4E4F534A:
            js = json.loads(body.decode("utf-8"))
        elif typ == 0x004E4942:
            binc = body
        off += 8 + ln
    return js, binc


def print_image_index(doc: dict) -> int:
    """The image the printed materials' baseColorTexture uses (the node extras carry g3d_print_uv)."""
    idx = set()
    for node in doc.get("nodes", []):
        if not (node.get("extras") or {}).get("g3d_print_uv") or "mesh" not in node:
            continue
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            mat = doc["materials"][prim["material"]]
            t = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            if t is not None:
                idx.add(doc["textures"][t["index"]]["source"])
    if len(idx) != 1:
        raise ValueError(f"expected one print image, found {sorted(idx)}")
    return idx.pop()


def part_image_indices(doc: dict, parts=PRINT_PART_NAMES) -> list[int]:
    """Every image a base-colour texture of ``parts``' materials uses (node names; a '.001' suffix is ignored)."""
    idx = set()
    for node in doc.get("nodes", []):
        if "mesh" not in node or str(node.get("name", "")).split(".")[0] not in set(parts):
            continue
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            mat = doc["materials"][prim["material"]]
            t = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            if t is not None:
                idx.add(doc["textures"][t["index"]]["source"])
    return sorted(idx)


def swap_images(src: Path, dst: Path, pngs: dict) -> dict:
    """Write ``dst`` = ``src`` with images {index: png bytes} replaced (bufferViews repacked, 4-byte aligned)."""
    doc, binc = _chunks(Path(src).read_bytes())
    targets = {doc["images"][k]["bufferView"]: png for k, png in pngs.items()}
    views = doc["bufferViews"]
    order = sorted(range(len(views)), key=lambda i: views[i].get("byteOffset", 0))
    out = bytearray()
    for i in order:
        v = views[i]
        data = targets[i] if i in targets else binc[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        out += b"\x00" * ((-len(out)) % 4)
        v["byteOffset"] = len(out)
        v["byteLength"] = len(data)
        out += data
    out += b"\x00" * ((-len(out)) % 4)
    doc["buffers"][0]["byteLength"] = len(out)
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(out), 0x004E4942) + bytes(out)
    Path(dst).write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return {"images": sorted(pngs), "bufferViews": sorted(targets),
            "png_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in pngs.items()}}


def swap_image(src: Path, dst: Path, png: bytes) -> dict:
    """Write ``dst`` = ``src`` with the print image's bytes replaced by ``png`` (bufferViews repacked, 4-byte aligned)."""
    doc, _ = _chunks(Path(src).read_bytes())
    k = print_image_index(doc)
    r = swap_images(src, dst, {k: png})
    return {"image_index": k, "bufferView": r["bufferViews"][0], "png_sha256": r["png_sha256"][k]}


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.asarray(arr, np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def black_probe(src: Path, dst: Path, clearcoat: bool = True) -> dict:
    """``dst`` = ``src`` with every base-colour image of the print parts' materials black (8 x 8): its capture shows
    the renderer's reflections alone (the probe of ``ar_levels``); a PROBE, never a model. ``clearcoat=False`` (fix
    round 1 of step 6, the material note's attribution): the print parts' materials also lose their
    KHR_materials_clearcoat, so the coat's share of the reflections is measured (a probe for the note to Astra; q001's
    materials are never changed)."""
    doc, _ = _chunks(Path(src).read_bytes())
    ks = part_image_indices(doc)
    if not ks:
        raise ValueError(f"{src}: no base-colour image on the print parts")
    black = _png(np.zeros((8, 8, 3), np.uint8))
    info = swap_images(src, dst, {k: black for k in ks})
    if not clearcoat:
        info["clearcoat_removed_from"] = strip_clearcoat(dst)
    return info


def strip_clearcoat(path: Path, parts=PRINT_PART_NAMES) -> list[str]:
    """In place: remove KHR_materials_clearcoat from the materials of ``parts``' primitives (JSON chunk rewritten, the
    binary chunk copied byte for byte). Probes only."""
    data = Path(path).read_bytes()
    doc, binc = _chunks(data)
    mats = set()
    for node in doc.get("nodes", []):
        if "mesh" in node and str(node.get("name", "")).split(".")[0] in set(parts):
            mats |= {prim["material"] for prim in doc["meshes"][node["mesh"]]["primitives"] if "material" in prim}
    names = []
    for i in sorted(mats):
        ext = doc["materials"][i].get("extensions") or {}
        if ext.pop("KHR_materials_clearcoat", None) is not None:
            names.append(doc["materials"][i].get("name"))
            if not ext:
                doc["materials"][i].pop("extensions", None)
    if not any("KHR_materials_clearcoat" in (m.get("extensions") or {}) for m in doc["materials"]):
        doc["extensionsUsed"] = [e for e in doc.get("extensionsUsed", []) if e != "KHR_materials_clearcoat"]
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    b = binc + b"\x00" * ((-len(binc)) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(b), 0x004E4942) + b
    Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return names


# ----------------------------------------------------------------------------- one candidate
def levelled_png(bake: Path, level: dict) -> bytes:
    img = np.asarray(Image.open(bake / "print.png").convert("RGB"))
    cls = np.asarray(Image.open(bake / "classes.png"))
    m = np.ones(img.shape[:2], bool)
    if cls.shape == m.shape:
        m &= cls != NO_TEXTURE
    return _png(SH.apply_level_u8(img, m, level))


def capture(glb: Path, out: Path, log, portrait: bool = False) -> Path:
    """arview capture of ``glb`` at every pose (skin background; the worn portrait only when ``portrait``); the node
    process is opted out of EcoQoS (not inherited from this one)."""
    cmd = ["node", "bin/arview.mjs", "capture", str(Path(glb).resolve()), "--poses", "all", "--backgrounds", "skin",
           "--out", str(Path(out).resolve())] + ([] if portrait else ["--no-portrait"])
    t = time.perf_counter()
    p = subprocess.Popen(cmd, cwd=str(ARVIEW), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    opt_out_ecoqos(p.pid)
    try:
        so, se = p.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        p.kill()
        so, se = p.communicate()
        raise RuntimeError(f"arview capture of {glb} timed out after 600 s: {se[-2000:]}")
    log(f"capture {Path(out).name}{' (with portrait)' if portrait else ''}: exit {p.returncode} in "
        f"{time.perf_counter() - t:.0f} s")
    Path(str(out) + ".log").write_text(so[-20000:] + "\n--- stderr ---\n" + se[-20000:], encoding="utf-8")
    if p.returncode not in (0, 3) or not (Path(out) / "report.json").is_file():
        raise RuntimeError(f"arview capture failed ({p.returncode}): {se[-2000:]}")
    return Path(out)


def capture_with_probe(glb: Path, out: Path, log=print, portrait: bool = False, job=None, photos=None,
                       coat_off_probe: bool = False) -> dict:
    """``glb`` captured at every pose (out/capture) with its black probe (out/probe), graded by ``ar_levels``
    (out/ar_levels.json); ``coat_off_probe``: also the black probe without the clearcoat (out/probe_nocoat)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cap = capture(glb, out / "capture", log, portrait)
    pg = out / f"probe_{Path(glb).stem}.glb"
    black_probe(glb, pg)
    probe = capture(pg, out / "probe", log)
    if coat_off_probe:
        pn = out / f"probe_nocoat_{Path(glb).stem}.glb"
        black_probe(glb, pn, clearcoat=False)
        capture(pn, out / "probe_nocoat", log)
    res = AL.measure([cap], job=job, probes={str(cap): str(probe)}, photos=photos)
    res["glb"] = str(glb)
    (out / "ar_levels.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def appearance_residuals(l_res: list, std_ratios: list) -> list:
    """Step 6: the ``appearance`` target's residuals: the L* residuals, then per pair 2 x ln(std ratio) / ln(1.18)
    (``CONTRAST_UNIT``; a missing ratio counts as the worst case, 99)."""
    return list(l_res) + [round(math.log(x) / CONTRAST_UNIT, 3) if x else 99.0 for x in std_ratios]


def worst(e: dict, role: str = "calibration", idx=None) -> float:
    r = e["residuals"][role]
    return max(abs(r[i]) for i in (range(len(r)) if idx is None else idx))


def best(its: list, role: str = "calibration", idx=None) -> dict:
    return its[int(np.argmin([worst(e, role, idx) for e in its]))]


# ----------------------------------------------------------------------------- the search (pure: tested offline)
def _uv(level: dict) -> np.ndarray:
    return np.array([math.log(level["gain"]) / LOG_UNIT, level["gamma"] / GAMMA_UNIT])


def _level(uv) -> dict:
    return {"gain": float(math.exp(uv[0] * LOG_UNIT)), "gamma": float(uv[1] * GAMMA_UNIT)}


def search_box(grid=GRID) -> dict:
    """The coarse grid's box extended by one grid step (``LOG_UNIT`` in log gain, ``GAMMA_UNIT`` in gamma): the
    search refines between and just beyond the grid points, never along a ridge to an extreme level."""
    lg = [math.log(g) for g in grid[0]]
    return {"gain": (math.exp(min(lg) - LOG_UNIT), math.exp(max(lg) + LOG_UNIT)),
            "gamma": (min(grid[1]) - GAMMA_UNIT, max(grid[1]) + GAMMA_UNIT)}


def propose(its: list, role: str, radius: float, box: dict | None = None):
    """(level, predicted worst) of the next capture for ``role``: the minimax point, inside a box of ``radius`` grid
    steps around the best captured point and inside ``box`` (default ``search_box()``), of planes fitted to the role's
    residuals over the ``K_NEAR`` nearest captures (weighted 1 / (d + 0.25)); None when they do not span a plane."""
    box = box or search_box()
    b = best(its, role)
    c0 = _uv(b["level"])
    UV = np.array([_uv(e["level"]) for e in its])
    d = np.linalg.norm(UV - c0, axis=1)
    near = np.argsort(d, kind="stable")[:K_NEAR]
    X = np.c_[np.ones(len(near)), UV[near]]
    if len(near) < 3 or np.linalg.matrix_rank(X) < 3:
        return None
    w = 1.0 / (d[near] + 0.25)
    R = np.array([its[i]["residuals"][role] for i in near], float)
    coef, *_ = np.linalg.lstsq(X * w[:, None], R * w[:, None], rcond=None)
    U, V = np.meshgrid(np.linspace(c0[0] - radius, c0[0] + radius, 81), np.linspace(c0[1] - radius, c0[1] + radius, 81))
    U, V = U.ravel(), V.ravel()
    gain, gamma = np.exp(U * LOG_UNIT), V * GAMMA_UNIT
    eps = 1e-9
    ok = ((gain >= box["gain"][0] - eps) & (gain <= box["gain"][1] + eps) & (gamma >= box["gamma"][0] - eps)
          & (gamma <= box["gamma"][1] + eps))
    if not ok.any():
        return None
    wst = np.abs(np.c_[np.ones(U.size), U, V] @ coef).max(1)
    wst[~ok] = np.inf
    j = int(np.argmin(wst))
    return _level((U[j], V[j])), float(wst[j])


def search(its: list, f, role: str, max_iter: int, tag: str, log=print, box: dict | None = None) -> dict:
    """Trust-region minimax for ``role`` (method step 5); ``f(gain, gamma, tag)`` captures one level and appends it to
    ``its``. Returns the trail and why it stopped."""
    radius, steps, trail, stop = 1.0, 0, [], "max_iter"
    while True:
        b = best(its, role)
        bw = worst(b, role)
        if bw <= STOP_TOL:
            stop = f"worst {bw:.2f} within STOP_TOL"
            break
        if radius < MIN_RADIUS:
            stop = f"trust box {radius:.3f} below MIN_RADIUS"
            break
        if steps >= max_iter:
            break
        prop = propose(its, role, radius, box)
        if prop is None:
            stop = "the captures do not span a plane"
            break
        lv, pw = prop
        if min(float(np.linalg.norm(_uv(lv) - _uv(e["level"]))) for e in its) < 0.02:
            radius *= 0.5                   # the model points back at a captured point: look closer
            continue
        steps += 1
        log(f"[{role}] step {steps}: box {radius:.3f}, model predicts worst {pw:.2f} at gain {lv['gain']:.4f} "
            f"gamma {lv['gamma']:.4f} (best so far {bw:.2f}, {b['tag']})")
        e = f(lv["gain"], lv["gamma"], f"{tag}{steps}")
        improved = worst(e, role) < bw - 0.01
        trail.append({"tag": e["tag"], "level": lv, "radius": round(radius, 4), "predicted_worst": round(pw, 3),
                      "worst": round(worst(e, role), 3), "improved": bool(improved)})
        radius = min(1.0, radius * 1.5) if improved else radius * 0.5
    b = best(its, role)
    return {"role": role, "steps": steps, "stop": stop, "trail": trail, "best": b["tag"],
            "worst": round(worst(b, role), 3)}


# ----------------------------------------------------------------------------- report
def _fmt_level(lv: dict) -> str:
    return (f"gain {lv['gain']:.2f}, gamma {lv['gamma']:.2f}" + (f", sat {lv['sat']:.2f}" if "sat" in lv else "")
            + (f", chroma power {lv['chroma_power']:.2f}" if "chroma_power" in lv else "")
            + (", chroma held >= 1" if lv.get("chroma_hold") else "")
            + (f", hue band {lv['chroma_hue'][0]:g}-{lv['chroma_hue'][1]:g} deg" if lv.get("chroma_hue") else ""))


def chroma_worst(e: dict, role: str = "calibration") -> float:
    """The worst |C*ab p50 difference| of a capture (step 6's chroma verdict alone)."""
    return max(abs(x) for x in e["chroma"][role])


def _roles(role) -> tuple:
    return (role,) if isinstance(role, str) else tuple(role)


def chroma_units_worst(e: dict, role="calibration") -> float:
    """The worst chroma unit (C* p50, p90, mean, each in its own window) of a capture over ``role`` (a role or a tuple
    of roles, e.g. ``ALL_GRADED``): <= 1 passes."""
    return max(abs(x) for r in _roles(role) for x in e["chroma_units"][r])


def chroma_level(level: dict) -> tuple[float, float]:
    return float(level.get("sat", 1.0)), float(level.get("chroma_power", 1.0))


def with_chroma(gain: float, gamma: float, sat: float = 1.0, power: float = 1.0, family: dict | None = None) -> dict:
    """The level dict: sat and chroma_power written only when they differ from the step-5 map's (1, 1), and with them
    the chroma family's options (``family``, default ``CHROMA_FAMILY``: held at >= 1, the havana hue band; ``{}`` =
    fix round 1's plain power)."""
    lv = {"gain": float(gain), "gamma": float(gamma)}
    if sat != 1.0 or power != 1.0:
        lv["sat"] = float(sat)
        lv.update(CHROMA_FAMILY if family is None else family)
    if power != 1.0:
        lv["chroma_power"] = float(power)
    return lv


def units_of(ar: np.ndarray, photo: np.ndarray) -> np.ndarray:
    """``print_metrics.chroma_units`` of predicted [p50, p90, mean] arrays (..., 3) against the photo's (..., 3)."""
    ar, photo = np.asarray(ar, float), np.asarray(photo, float)
    u50 = (ar[..., :1] - photo[..., :1]) / AL.PM.CHROMA_TOL
    r = np.log(np.maximum(ar[..., 1:], 1e-9) / photo[..., 1:])
    edge = np.where(r >= 0, math.log(AL.PM.CHROMA_SPREAD_RATIO[1]), math.log(1.0 / AL.PM.CHROMA_SPREAD_RATIO[0]))
    return np.concatenate([u50, r / edge], -1)


def _cuv(level: dict) -> np.ndarray:
    s, p = chroma_level(level)
    return np.array([math.log(s) / CHROMA_UNIT_SAT, p / CHROMA_UNIT_POWER])


def choose_chroma(its: list, role="calibration", sat_box=SAT_BOX, power_box=POWER_BOX):
    """((sat, power), predicted worst unit, model) of the chroma stage's next capture: ln of every AR C* statistic (per
    pair of ``role``, a role or a tuple of roles: p50, p90, mean) fitted by a plane in (ln sat, power) over the
    captures ``its`` (same gain and gamma), weighted 1 / (d + 0.25) around the best captured point (d in
    ``CHROMA_UNIT_*``), and the point of ``sat_box`` x ``power_box`` whose worst predicted unit (``units_of``) is
    smallest; None when the captures do not span a plane."""
    b = min(its, key=lambda e: chroma_units_worst(e, role))
    UV = np.array([_cuv(e["level"]) for e in its])
    X = np.c_[np.ones(len(its)), [math.log(chroma_level(e["level"])[0]) for e in its],
              [chroma_level(e["level"])[1] for e in its]]
    if len(its) < 3 or np.linalg.matrix_rank(X) < 3:
        return None
    d = np.linalg.norm(UV - _cuv(b["level"]), axis=1)
    w = 1.0 / (d + 0.25)
    st = [np.concatenate([np.asarray(e["chroma_stats"][r], float).reshape(-1, 2, 3) for r in _roles(role)])
          for e in its]                                                     # (pairs, 2, 3) each
    A = np.array([np.log(np.maximum(x[:, 0, :], 1e-9)).ravel() for x in st])
    photo = st[0][:, 1, :]
    coef, *_ = np.linalg.lstsq(X * w[:, None], A * w[:, None], rcond=None)
    S, P = np.meshgrid(np.exp(np.linspace(math.log(sat_box[0]), math.log(sat_box[1]), 241)),
                       np.linspace(power_box[0], power_box[1], 161))
    S, P = S.ravel(), P.ravel()
    pred = np.exp(np.c_[np.ones(S.size), np.log(S), P] @ coef).reshape(S.size, *photo.shape)
    wst = np.abs(units_of(pred, photo[None])).reshape(S.size, -1).max(1)
    j = int(np.argmin(wst))
    return (float(S[j]), float(P[j])), float(wst[j]), {"coef_ln_stat": [[round(float(v), 4) for v in c]
                                                                        for c in coef.T]}


def _has_roles(e: dict, roles) -> bool:
    """Whether a capture carries chroma units and statistics for every role of ``roles``."""
    return all(e.get("chroma_units", {}).get(r) and e.get("chroma_stats", {}).get(r) for r in _roles(roles))


def capture_probes(g, probes=CHROMA_PROBES) -> list:
    """Fix round 3 of step 6: the chroma probes are independent captures, so they run at the same time (one thread
    each). The supplied evaluator owns capture and grading; the results come back in ``probes`` order whatever
    order they finish in. Step 6 to fix round 2 captured them one after another."""
    from concurrent.futures import ThreadPoolExecutor
    args = [(sv, pv, f"chroma{k + 1}") for k, (sv, pv) in enumerate(probes)]
    if len(args) <= 1:
        return [g(*a) for a in args]
    with ThreadPoolExecutor(max_workers=len(args)) as ex:
        return list(ex.map(lambda a: g(*a), args))


def chroma_stage(base: dict, g, max_iter: int = CHROMA_MAX_ITER, log=print, roles="calibration", its=None,
                 tag: str = "chroma_s") -> dict:
    """Method step 7 (b): ``g(sat, power, tag)`` captures one chroma level at the base's (gain, gamma). The base plus
    ``CHROMA_PROBES`` (captured at the same time, ``capture_probes``, so ``g`` must be thread-safe; or the captures
    ``its`` of an earlier stage at the same (gain, gamma): fix round 2's all-pairs
    stage reuses the photo-angle stage's), then the model's minimax point over ``roles`` (``choose_chroma``) until
    the captured worst unit is within ``CHROMA_STOP``, the model points back at a captured point, or ``max_iter``
    captures. Returns the trail and the CAPTURED point with the smallest worst unit over ``roles`` (``chosen``;
    ``chosen_e`` is the capture itself), and every capture's worst unit over the calibration pairs and over
    ``ALL_GRADED`` beside each other (fix round 2: the calibration worst alone hid the other pairs)."""
    roles = _roles(roles)
    probes = its is None
    its = [base] + capture_probes(g) if probes else list(its)
    worst_r = lambda e: chroma_units_worst(e, roles)
    trail, stop, steps = [], "max_iter", 0
    while True:
        b = min(its, key=worst_r)
        bw = worst_r(b)
        if bw <= CHROMA_STOP:
            stop = f"worst chroma unit {bw:.2f} within CHROMA_STOP"
            break
        if steps >= max_iter:
            break
        prop = choose_chroma(its, roles)
        if prop is None:
            stop = "the captures do not span a plane"
            break
        (sv, pv), pw, _model = prop
        if min(float(np.linalg.norm(_cuv(with_chroma(1, 1, sv, pv)) - _cuv(e["level"]))) for e in its) < 0.02:
            stop = "the model points back at a captured point"
            break
        steps += 1
        log(f"[chroma {'+'.join(roles)}] step {steps}: model predicts worst unit {pw:.2f} at sat {sv:.4f} power "
            f"{pv:.4f} (best so far {bw:.2f}, {b['tag']})")
        e = g(sv, pv, f"{tag}{steps}")
        its.append(e)
        trail.append({"tag": e["tag"], "sat": round(sv, 4), "power": round(pv, 4), "predicted_worst": round(pw, 3),
                      "worst": round(worst_r(e), 3)})
    chosen = min(its, key=worst_r)
    final = choose_chroma(its, roles)
    return {"roles": list(roles), "base": base["tag"],
            "probes": [e["tag"] for e in its[1:1 + len(CHROMA_PROBES)]] if probes else None, "trail": trail,
            "stop": stop, "steps": steps, "captures": [e["tag"] for e in its],
            "worst_units": {e["tag"]: round(worst_r(e), 3) for e in its},
            "worst_units_calibration": {e["tag"]: round(chroma_units_worst(e), 3) for e in its},
            "worst_units_all_graded": {e["tag"]: (round(chroma_units_worst(e, ALL_GRADED), 3)
                                                  if _has_roles(e, ALL_GRADED) else None) for e in its},
            "model_at_end": final[2] if final else None, "chosen": chosen["tag"], "chosen_e": chosen,
            "level": chosen["level"], "its": its}


def summary_of(e: dict) -> dict:
    return {"tag": e["tag"], "level": e["level"], "capture": e["capture"], "glb": e["glb"], "pairs": e["pairs"],
            "chroma": e.get("chroma"), "chroma_units": e.get("chroma_units"), "chroma_stats": e.get("chroma_stats"),
            "contrast": e.get("contrast"),
            "chroma_worst": {r: round(chroma_worst(e, r), 2) for r in AL.ROLES} if e.get("chroma") else None,
            "chroma_units_worst": ({r: round(chroma_units_worst(e, r), 2) for r in AL.ROLES}
                                   | ({"all_graded": round(chroma_units_worst(e, ALL_GRADED), 2)}
                                      if _has_roles(e, ALL_GRADED) else {})
                                   if e.get("chroma_units") else None),
            "brow_residuals": e.get("brow_residuals"), "temple_residuals": e.get("temple_residuals"),
            "brow_chroma_units": e.get("brow_chroma_units"), "hue": e.get("hue"),
            "brow_pass_by_role": e.get("brow_pass_by_role"),
            "residuals": e["residuals"], "worst": {r: round(worst(e, r), 2) for r in AL.ROLES + ("appearance",)},
            "residuals_by_form": e["residuals_by_form"], "view_dependence": e["view_dependence"],
            "pass_by_role": e["pass_by_role"]}


def assemble_candidates(chosen: dict, near: dict, roles: dict, all_pairs: dict | None = None) -> dict:
    """The owner's candidates, by NAME: 'photo_angles' = ``chosen`` (the chroma stage's captured choice: the delivered
    level), 'near_18deg' = ``near`` (the trade-off best re-captured at that chroma) and (fix round 2 of step 6)
    'all_pairs_chroma' = ``all_pairs`` (the same (gain, gamma) with the chroma minimax over every graded pair; skipped
    when it is the delivered capture itself). Step 6's first run filed the near capture as delivered (a role-name
    check); this is the one place that decides it."""
    picks = {"photo_angles": chosen, "near_18deg": near}
    cands = {}
    for name, role in TARGETS.items():
        e = picks[name]
        cands[name] = summary_of(e) | {"over": roles[ROLE_PAIRS.get(role, role)], "role": role,
                                       "delivered": name == "photo_angles",
                                       "label": f"{LABELS[name]}: {_fmt_level(e['level'])}"}
    if all_pairs is not None and all_pairs["tag"] != chosen["tag"]:
        cands["all_pairs_chroma"] = summary_of(all_pairs) | {
            "over": [p for r in ALL_GRADED for p in roles[r]], "role": "chroma over every graded pair",
            "delivered": False, "label": f"{LABELS['all_pairs_chroma']}: {_fmt_level(all_pairs['level'])}"}
    return cands


def finish(base_its: list, f, roles: dict, log=print, max_iter: int = CHROMA_MAX_ITER) -> dict:
    """After the L* searches (pure: ``f(gain, gamma, tag, sat=, power=)`` captures one level): the chroma stage at
    the best ``appearance`` capture of ``base_its`` (the (1, 1) captures), the near-18 deg candidate (the best
    ``trade-off`` capture of ``base_its``) captured again at the chosen chroma, and the candidates."""
    base = best(base_its, "appearance")
    nb = best(base_its, "trade-off")
    gb, gm = base["level"]["gain"], base["level"]["gamma"]
    g = lambda sv, pv, tag: f(gb, gm, tag, sat=sv, power=pv)
    stage = chroma_stage(base, g, max_iter, log)
    chosen = stage.pop("chosen_e")
    its = stage.pop("its")
    # fix round 2 of step 6: the same captures graded over every pair, and the all-pairs minimax (an owner candidate)
    allp = (chroma_stage(base, g, max_iter, log, roles=ALL_GRADED, its=its, tag="chroma_all_s")
            if _has_roles(base, ALL_GRADED) else None)
    all_e = allp.pop("chosen_e") if allp else None
    if allp:
        allp.pop("its")
    sv, pv = chroma_level(chosen["level"])
    near = f(nb["level"]["gain"], nb["level"]["gamma"], "near_chroma", sat=sv, power=pv)
    stage["near_18deg_at_chroma"] = near["tag"]
    stage["near_18deg_base"] = nb["tag"]
    stage["at_chosen"] = {"chroma_p50": chosen["chroma"]["calibration"],
                          "chroma_units": chosen["chroma_units"]["calibration"],
                          "chroma_units_by_role": {r: chosen["chroma_units"].get(r) for r in ALL_GRADED},
                          "worst_unit_calibration": round(chroma_units_worst(chosen), 3),
                          "worst_unit_all_graded": (round(chroma_units_worst(chosen, ALL_GRADED), 3)
                                                    if _has_roles(chosen, ALL_GRADED) else None),
                          "l_worst": round(worst(chosen), 2), "appearance_worst": round(worst(chosen, "appearance"), 2),
                          "l_worst_at_base": round(worst(base), 2),
                          "appearance_worst_at_base": round(worst(base, "appearance"), 2)}
    if allp:
        allp["at_chosen"] = {"chroma_units_by_role": {r: all_e["chroma_units"].get(r) for r in ALL_GRADED},
                             "worst_unit_calibration": round(chroma_units_worst(all_e), 3),
                             "worst_unit_all_graded": round(chroma_units_worst(all_e, ALL_GRADED), 3),
                             "l_worst": round(worst(all_e), 2)}
    stage["all_graded"] = allp
    return {"chosen": chosen, "near": near, "all_pairs": all_e, "chroma_stage": stage,
            "candidates": assemble_candidates(chosen, near, roles, all_e)}


def calibrate(bake: Path, glb: Path, work: Path, grid=GRID, max_iter: int = 6, log=print,
              portraits: bool = True) -> dict:
    """Fixture configuration only; the measured calibration driver lives in the production library."""
    from glasses3d.g3d_host.photoprint.production import calibrate_level
    work = Path(work).resolve()
    private = work.with_name(work.name + '-fixture')
    private.mkdir(parents=True, exist_ok=False)
    job, bake = legacy_ft1119.bake_inputs(bake, private / 'bake')
    source_glb = Path(glb).resolve()
    migrated_glb = private / 'template.glb'
    receipt = legacy_ft1119.rename_glb(source_glb, migrated_glb)
    legacy_ft1119.write(private / 'migration.json', receipt)
    return calibrate_level(job, AL.GROUPS, bake=bake, glb=migrated_glb, out_dir=work,
                           chroma_family=CHROMA_FAMILY, temple_z=AL.AR_TEMPLE_Z,
                           reference_point_mm=AL.BRIDGE_MM, grid=grid, max_iter=max_iter,
                           log=log, portraits=portraits)


# ----------------------------------------------------------------------------- re-grade (fix round 3 of step 6)
def _repo_path(s: str) -> Path:
    """A path as calibration.json stores it (relative to the repository root, the run's working directory)."""
    p = Path(str(s).replace("\\", "/"))
    return p if p.is_absolute() else G3D.parent / p


def _regrade_chunk(caps: list, probe: str, min_share: float) -> dict:
    """``regrade``'s pool job: ``ar_levels.measure`` of several captures that share one black probe."""
    opt_out_ecoqos()
    job = legacy_ft1119.load_job()
    return AL.measure([Path(c) for c in caps], job=job, probes={str(Path(c)): probe for c in caps},
                      min_photo_share=min_share)


def regrade_summary(m: dict, cap: str) -> dict:
    """One capture's chroma units and L* residuals per role, its worst chroma unit over the calibration pairs and over
    every graded pair (with the pair and term that set it), and per pair the groups the instrument left out."""
    row = m["captures"][str(Path(cap))]
    roles = {"calibration": m["pairs"], "trade-off": m["tradeoff"], "held-out": m["heldout"]}
    units = {r: AL.chroma_units(row, AL.TARGET, [tuple(p) for p in ps]) for r, ps in roles.items()}
    terms = [(abs(u), r, AL.key(*ps[i // 3]), ("p50", "p90", "mean")[i % 3]) for r, ps in roles.items()
             for i, u in enumerate(units[r])]
    wc = max(t for t in terms if t[1] == "calibration")
    wa = max(terms)
    return {"chroma_units": units,
            "worst_unit_calibration": round(wc[0], 3), "worst_unit_all_graded": round(wa[0], 3),
            "worst_term_all_graded": {"role": wa[1], "pair": wa[2], "term": wa[3]},
            "residuals": {r: AL.residuals(row, AL.TARGET, [tuple(p) for p in ps]) for r, ps in roles.items()},
            "pass_by_role": row["pass"], "pass_brow": row["pass_brow"],
            "appearance_pass": {AL.key(*p): row[AL.key(*p)][AL.TARGET]["appearance"]["pass"]
                                for ps in roles.values() for p in ps},
            "groups_excluded": {AL.key(*p): sorted(row[AL.key(*p)].get("groups_excluded") or {})
                                for ps in roles.values() for p in ps}}


def regrade(work: Path, tags=None, workers: int = 1, min_share: float = AL.MIN_PHOTO_GROUP_SHARE, log=print) -> dict:
    """Fix round 3 of step 6: every capture of a calibration run (``work``/calibration.json ``iterations``, or only
    ``tags``) graded again by the CURRENT instrument against the run's own black probe: no capture, no fit, no level
    changed. Beside each, the worst chroma units the run itself recorded. ``workers`` > 1: the captures are split over
    an EcoQoS-opted-out process pool (``procs.pool_map``; progress at least every 30 s)."""
    cal = json.loads((Path(work) / "calibration.json").read_text(encoding="utf-8"))
    probe = str(_repo_path(cal["probe"]["capture"]))
    its = [e for e in cal["iterations"] if tags is None or e["tag"] in set(tags)]
    caps = [str(_repo_path(e["capture"])) for e in its]
    n = max(1, min(int(workers), len(caps)))
    chunks = [caps[i::n] for i in range(n)]
    res = pool_map(_regrade_chunk, [(c, probe, float(min_share)) for c in chunks], workers=n, progress=log,
                   tag="regrade")
    out = {"calibration": str(Path(work) / "calibration.json"), "probe": probe, "min_photo_group_share": min_share,
           "instrument": "ar_levels.measure (current), print form", "captures": {}}
    for e, cap in zip(its, caps):
        m = next(r for r in res if str(Path(cap)) in r["captures"])
        st = e.get("chroma_units") or {}
        out["captures"][e["tag"]] = {
            "level": e["level"], "capture": cap,
            "stored": {"worst_unit_calibration": (round(max(abs(x) for x in st["calibration"]), 3)
                                                  if st.get("calibration") else None),
                       "worst_unit_all_graded": (round(max(abs(x) for r in ALL_GRADED for x in st[r]), 3)
                                                 if all(st.get(r) for r in ALL_GRADED) else None)},
            "now": regrade_summary(m, cap)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regrade", default=None, help="a calibration directory: re-grade its captures with the current "
                                                    "instrument (no capture, no fit); writes --out")
    ap.add_argument("--tags", default=None, help="with --regrade: comma-separated iteration tags (default: all)")
    ap.add_argument("--workers", type=int, default=1, help="with --regrade: process-pool workers")
    ap.add_argument("--bake", default=None)
    ap.add_argument("--glb", default=None, help="the tool-test GLB painted by g3d.attach_print (a COPY of q001)")
    ap.add_argument("--capture", default=None, help="capture this GLB (and its black probe) only; grade it")
    ap.add_argument("--portrait", action="store_true", help="with --capture: also the worn portrait")
    ap.add_argument("--coat-off-probe", action="store_true",
                    help="with --capture: also the black probe without the print parts' clearcoat (material note)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-iter", type=int, default=6, help="captures per search target after the grid")
    ap.add_argument("--no-candidate-portraits", action="store_true",
                    help="skip re-capturing the owner candidates with the worn portrait")
    a = ap.parse_args(argv)
    opt_out_ecoqos()
    prog = Progress("ar_calibrate")
    log = lambda m: prog(m, force=True)
    with prog.heartbeat():
        if a.regrade:
            if not a.out:
                ap.error("--regrade needs --out")
            res = regrade(Path(a.regrade), a.tags.split(",") if a.tags else None, a.workers, log=log)
            Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
            for t, r in res["captures"].items():
                log(f"{t}: worst unit calibration {r['stored']['worst_unit_calibration']} -> "
                    f"{r['now']['worst_unit_calibration']}, all graded {r['stored']['worst_unit_all_graded']} -> "
                    f"{r['now']['worst_unit_all_graded']} ({r['now']['worst_term_all_graded']})")
            return 0
        if a.capture:
            if not a.out:
                ap.error("--capture needs --out")
            res = capture_with_probe(Path(a.capture), Path(a.out), log=log, portrait=a.portrait,
                                     coat_off_probe=a.coat_off_probe)
            print(AL.table(res))
            return 0
        if not (a.bake and a.glb):
            ap.error("--bake and --glb are required (or --capture)")
        bake = Path(a.bake)
        out = Path(a.out) if a.out else bake / "ar_calibration"
        previous = out / "calibration.json"
        if previous.is_file() and "production_selection" in json.loads(previous.read_bytes()):
            raise ValueError("This calibration has an owner production selection; use a new --out directory")
        res = calibrate(bake, Path(a.glb), out, GRID, a.max_iter, log=log, portraits=not a.no_candidate_portraits)
    (out / "calibration.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("level", "residuals", "worst", "pass", "chroma_residuals_p50", "chroma_units",
                                          "contrast_std_ratio", "chroma_pass", "chroma_stage", "leave_one_pair_out",
                                          "chosen_capture")}, indent=1), flush=True)
    print(json.dumps({k: {kk: v.get(kk) for kk in ("label", "level", "worst", "over", "sheet_capture",
                                                   "sheet_capture_same_numbers")}
                      for k, v in res["candidates"].items()}, indent=1), flush=True)
    print(json.dumps({k: {kk: v[kk] for kk in ("steps", "stop", "best", "worst")} for k, v in res["search"].items()},
                     indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
