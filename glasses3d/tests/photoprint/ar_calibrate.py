"""WP5 correction round, task item 1: calibrate the print's AR level TO THE PHOTOS (the goal is to look like them in AR).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.ar_calibrate --bake DIR --glb TOOLTEST.glb
        [--out DIR] [--max-iter 6] [--no-candidate-portraits]
    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.ar_calibrate --capture GLB --out DIR [--portrait]
        (one GLB captured at every pose with its black probe, graded by ar_levels: DIR/capture, DIR/probe,
        DIR/ar_levels.json)

Method (written down here and in PORT_REVIEW section 10):
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
   compares the levels in the worn view too (``tooltest_owner_sheet --calibration``).
Every capture's numbers are written to ``calibration.json`` (``iterations``; ``calibration_partial.json`` after each
capture while it runs), with the chosen level, which the bake reads (``bake_ft1119 --level FILE``). The pass rule
(task item 1) is |p10| and |p50| differences <= 2 L*; a pass is reported per role, never pooled.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import shading as SH
from glasses3d.g3d_host.photoprint.procs import Progress, opt_out_ecoqos
from glasses3d.tests.photoprint import ar_levels as AL

G3D = Path(__file__).resolve().parents[2]
ARVIEW = G3D / "arview"
STOP_TOL = 1.0
NO_TEXTURE = 10           # classes.png code of a part left without texture (never levelled)
GRID = ((0.55, 0.75, 0.95), (0.85, 1.0, 1.15))
PRINT_PART_NAMES = tuple(IN.FT1119_PARTS)
LOG_UNIT = math.log(0.95 / 0.75)   # one grid step in log gain (the search's distance unit) ...
GAMMA_UNIT = 0.15                  # ... and in gamma
K_NEAR = 6
MIN_RADIUS = 0.06
TARGETS = {"photo_angles": "calibration", "near_18deg": "trade-off"}
LABELS = {"photo_angles": "level calibrated at the photos' own angles (pitch +20 vs 05, yaw -30 pitch +20 vs 01)",
          "near_18deg": "level best at front / yaw -30, 18 deg below photos 05 / 01 (a view-dependence trade-off)"}


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


def black_probe(src: Path, dst: Path) -> dict:
    """``dst`` = ``src`` with every base-colour image of the print parts' materials black (8 x 8): its capture shows
    the renderer's reflections alone (the probe of ``ar_levels``); a PROBE, never a model."""
    doc, _ = _chunks(Path(src).read_bytes())
    ks = part_image_indices(doc)
    if not ks:
        raise ValueError(f"{src}: no base-colour image on the print parts")
    black = _png(np.zeros((8, 8, 3), np.uint8))
    return swap_images(src, dst, {k: black for k in ks})


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


def capture_with_probe(glb: Path, out: Path, log=print, portrait: bool = False, job=None, photos=None) -> dict:
    """``glb`` captured at every pose (out/capture) with its black probe (out/probe), graded by ``ar_levels``
    (out/ar_levels.json)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    cap = capture(glb, out / "capture", log, portrait)
    pg = out / f"probe_{Path(glb).stem}.glb"
    black_probe(glb, pg)
    probe = capture(pg, out / "probe", log)
    res = AL.measure([cap], job=job, probes={str(cap): str(probe)}, photos=photos)
    res["glb"] = str(glb)
    (out / "ar_levels.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def grade(cap: Path, job, probe: Path, photos: dict) -> dict:
    """``ar_levels`` rows of one capture, with its residuals per role (``print`` form, and every form)."""
    m = AL.measure([cap], job=job, probes={str(cap): str(probe)}, photos=photos)
    row = m["captures"][str(cap)]
    roles = {"calibration": m["pairs"], "trade-off": m["tradeoff"], "held-out": m["heldout"]}
    return {"capture": str(cap), "pairs": roles, "rows": row,
            "residuals": {r: AL.residuals(row, AL.TARGET, [tuple(p) for p in ps]) for r, ps in roles.items()},
            "residuals_by_form": {r: {f: AL.residuals(row, f, [tuple(p) for p in ps]) for f in AL.FORMS}
                                  for r, ps in roles.items()},
            "pass": bool(row["pass"]["calibration"][AL.TARGET]), "pass_by_role": row["pass"],
            "view_dependence": row["view_dependence"]}


def evaluate(bake: Path, glb: Path, work: Path, level: dict, tag: str, job, probe: Path, photos: dict, log) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    g = work / f"cand_{tag}_TOOLTEST.glb"
    info = swap_image(glb, g, levelled_png(bake, level))
    cap = capture(g, work / f"cap_{tag}", log)
    return {"tag": tag, "level": level, "glb": str(g), "png_sha256": info["png_sha256"]} | grade(cap, job, probe,
                                                                                                   photos)


def worst(e: dict, role: str = "calibration", idx=None) -> float:
    r = e["residuals"][role]
    return max(abs(r[i]) for i in (range(len(r)) if idx is None else idx))


def best(its: list, role: str = "calibration", idx=None) -> dict:
    return its[int(np.argmin([worst(e, role, idx) for e in its]))]


def pair_index(pairs: list, keep) -> list[int]:
    """Residual indices (2 per pair: p10, p50) of the pairs in ``keep``."""
    return [2 * i + q for i, p in enumerate(pairs) if tuple(p) in set(map(tuple, keep)) for q in (0, 1)]


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
    return f"gain {lv['gain']:.2f}, gamma {lv['gamma']:.2f}"


def summary_of(e: dict) -> dict:
    return {"tag": e["tag"], "level": e["level"], "capture": e["capture"], "glb": e["glb"], "pairs": e["pairs"],
            "residuals": e["residuals"], "worst": {r: round(worst(e, r), 2) for r in AL.ROLES},
            "residuals_by_form": e["residuals_by_form"], "view_dependence": e["view_dependence"],
            "pass_by_role": e["pass_by_role"]}


def candidate_portraits(cands: dict, work: Path, job, probe: Path, photos: dict, log) -> None:
    """Each candidate's GLB captured again WITH the worn portrait (``sheet_capture``) for the owner sheet; its graded
    numbers must equal the calibration capture's (``sheet_capture_same_numbers``)."""
    done = {}
    for name, c in cands.items():
        if c["tag"] not in done:
            cap = capture(Path(c["glb"]), work / f"sheet_{c['tag']}", log, portrait=True)
            g = grade(cap, job, probe, photos)
            done[c["tag"]] = (str(cap), g["residuals"] == c["residuals"])
        c["sheet_capture"], c["sheet_capture_same_numbers"] = done[c["tag"]]
        if not c["sheet_capture_same_numbers"]:
            log(f"WARNING: {name}'s portrait capture grades differently from its calibration capture")


def calibrate(bake: Path, glb: Path, work: Path, grid=GRID, max_iter: int = 6, log=print,
              portraits: bool = True) -> dict:
    job = IN.load_job()
    table = AL.pair_table(job)
    photos = {}
    work.mkdir(parents=True, exist_ok=True)
    pg = work / "probe_black_TOOLTEST.glb"
    probe_info = black_probe(glb, pg)
    probe = capture(pg, work / "probe", log)
    its = []

    def f(gain, gamma, tag):
        e = evaluate(bake, glb, work, {"gain": float(gain), "gamma": float(gamma)}, tag, job, probe, photos, log)
        its.append(e)
        r = e["residuals"]
        log(f"[{tag}] gain {gain:.4f} gamma {gamma:.4f} -> {AL.TARGET} calibration {r['calibration']} worst "
            f"{worst(e):.2f} pass {e['pass']} | trade-off {r['trade-off']} | held-out {r['held-out']}")
        (work / "calibration_partial.json").write_text(json.dumps(
            {"bake": str(bake), "glb_template": str(glb), "iterations": [{k: v for k, v in x.items() if k != "rows"}
                                                                         for x in its]}, indent=1), encoding="utf-8")
        return e

    for gm in grid[1]:
        for gn in grid[0]:
            f(gn, gm, f"g{gn:.3f}_{gm:.3f}")
    box = search_box(grid)
    searches = {name: search(its, f, role, max_iter, f"{name}_s", log, box) for name, role in TARGETS.items()}
    roles = its[0]["pairs"]
    pairs = roles["calibration"]
    chosen = best(its, "calibration")
    loo = {}
    for p in pairs:
        rest = pair_index(pairs, [q for q in pairs if q != p])
        e = best(its, "calibration", rest)
        own = pair_index(pairs, [p])
        loo[AL.key(*p)] = {"level_without_it": e["level"], "tag": e["tag"],
                           "residuals_on_it": [e["residuals"]["calibration"][i] for i in own],
                           "worst_on_the_others": round(worst(e, "calibration", rest), 2)}
    cands = {}
    for name, role in TARGETS.items():
        e = chosen if role == "calibration" else best(its, role)
        cands[name] = summary_of(e) | {"over": roles[role], "role": role, "delivered": role == "calibration",
                                       "label": f"{LABELS[name]}: {_fmt_level(e['level'])}"}
    if portraits:
        candidate_portraits(cands, work, job, probe, photos, log)
    return {"method": __doc__.split("Method (written down here and in PORT_REVIEW section 10):")[1].strip(),
            "bake": str(bake), "glb_template": str(glb), "target_form": AL.TARGET, "pair_table": table,
            "pairs": pairs, "tradeoff": roles["trade-off"], "heldout": roles["held-out"],
            "probe": {"glb": str(pg), "capture": str(probe)} | probe_info,
            "level": chosen["level"], "residuals_p10_p50": chosen["residuals"]["calibration"],
            "worst": round(worst(chosen), 2), "residuals": chosen["residuals"],
            "residuals_by_form": chosen["residuals_by_form"], "pass": chosen["pass"],
            "pass_by_role": chosen["pass_by_role"], "view_dependence": chosen["view_dependence"],
            "chosen_capture": chosen["capture"], "search": searches, "leave_one_pair_out": loo, "candidates": cands,
            "iterations": its, "stop_tol": STOP_TOL, "grid": [list(g) for g in grid], "search_box": box}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bake", default=None)
    ap.add_argument("--glb", default=None, help="the tool-test GLB painted by g3d.attach_print (a COPY of q001)")
    ap.add_argument("--capture", default=None, help="capture this GLB (and its black probe) only; grade it")
    ap.add_argument("--portrait", action="store_true", help="with --capture: also the worn portrait")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-iter", type=int, default=6, help="captures per search target after the grid")
    ap.add_argument("--no-candidate-portraits", action="store_true",
                    help="skip re-capturing the owner candidates with the worn portrait")
    a = ap.parse_args(argv)
    opt_out_ecoqos()
    prog = Progress("ar_calibrate")
    log = lambda m: prog(m, force=True)
    with prog.heartbeat():
        if a.capture:
            if not a.out:
                ap.error("--capture needs --out")
            res = capture_with_probe(Path(a.capture), Path(a.out), log=log, portrait=a.portrait)
            print(AL.table(res))
            return 0
        if not (a.bake and a.glb):
            ap.error("--bake and --glb are required (or --capture)")
        bake = Path(a.bake)
        out = Path(a.out) if a.out else bake / "ar_calibration"
        res = calibrate(bake, Path(a.glb), out, GRID, a.max_iter, log=log, portraits=not a.no_candidate_portraits)
    (out / "calibration.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("level", "residuals", "worst", "pass", "leave_one_pair_out",
                                          "chosen_capture")}, indent=1), flush=True)
    print(json.dumps({k: {kk: v.get(kk) for kk in ("label", "level", "worst", "over", "sheet_capture",
                                                   "sheet_capture_same_numbers")}
                      for k, v in res["candidates"].items()}, indent=1), flush=True)
    print(json.dumps({k: {kk: v[kk] for kk in ("steps", "stop", "best", "worst")} for k, v in res["search"].items()},
                     indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
