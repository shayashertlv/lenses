"""The AR pose sweep: the exported asset in the actual runtime at small head turns, and how its lens/frame reflections
behave from one pose to the next.

A wearer never holds still: a flat (base 0) lens mirrors one large uniform patch of the runtime's room, which flips
on and off between poses a few degrees apart; a curved lens spreads it into a band that drifts. The single front
render the AR sheet carried could not show that (test-pilot-002 shipped a flat lens slab the owner saw live). The
sweep renders the front at yaw -9..+9 in 3-degree steps at pitch -4 (the camera slightly below the eyes, as a phone
held at chest height) and 0, in the same harness run as the AR sheet's views, and counts SATURATED pixels (min
channel >= ``SATURATED_MIN``; the checker fixture never reaches it) inside the projected front piece (frame + lenses,
``see_through.frame_mask_in_render``).

Measured 2026-09-28 (harness 720x480, checker; share = saturated / front-piece pixels):
  delivered r0006 (flat lens): peak share 0.310, largest jump between yaw neighbours 0.109 (2,496 px), three jumps >= 0.095;
  the same program with a base-6 lens: peak 0.105, largest jump 0.020;
  owner-accepted assets: vb 0.078 / 0.031, rayban 0.086 / 0.015, oakley 0.051 / 0.015, invu 0.058 / 0.014, and miu (rimless
  clear lenses, many edge highlights) 0.288 / 0.045.
The peak does not separate a slab from miu's highlights; the jump does: ``SLAB_JUMP_SHARE`` 0.07 sits 1.5x above the
largest accepted jump and 1.4x under the delivered slab's third-largest. (The earlier diagnosis at 1280x720 measured
16,806 px peak against about 2,600 for base 6; this harness renders smaller, the ratio agrees in direction.)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

POSE_SWEEP_YAWS = (-9, -6, -3, 0, 3, 6, 9)
POSE_SWEEP_PITCHES = (-4, 0)
SATURATED_MIN = 250
SLAB_JUMP_SHARE = 0.07
FRONT_PIECE_PARTS = ("frame", "lens_R", "lens_L", "lens_C")
SHEET_TILE_HEIGHT = 120   # 7 tiles per row: about 2,200 px wide on r0006


def _signed(prefix: str, v: int) -> str:
    return f"{prefix}{'m' if v < 0 else ''}{abs(int(v))}"


def pitch_key(pitch: int) -> str:
    return _signed("p", pitch)


def view_id(pitch: int, yaw: int) -> str:
    return f"sweep_{pitch_key(pitch)}_{_signed('y', yaw)}"


POSE_SWEEP_VIEWS = tuple({"id": view_id(p, y), "yaw_degrees": y, "pitch_degrees": p} for p in POSE_SWEEP_PITCHES for y in POSE_SWEEP_YAWS)


def front_piece_saturation(glb_path: Path, out_dir: Path, views) -> dict:
    """{view id: (saturated px inside the front piece, front-piece px)} for the sweep renders a harness run wrote to
    ``out_dir`` (its report.json rows carry the runtime's camera and the asset transform)."""
    from .see_through import frame_mask_in_render
    rp = Path(out_dir) / "report.json"
    if not rp.exists():
        return {}
    report = json.loads(rp.read_text(encoding="utf-8"))
    case = (report.get("cases") or [None])[0] or {}
    out = {}
    for r in case.get("renders", []):
        if r.get("mode") != "actual-ar" or r.get("view") not in views:
            continue
        a = np.asarray(Image.open(Path(out_dir) / r["filename"]).convert("RGB"))
        m = frame_mask_in_render(Path(glb_path), r, a.shape[:2], part_names=FRONT_PIECE_PARTS)
        if m is None or not m.any():
            continue
        sat = a.min(-1) >= SATURATED_MIN
        out[r["view"]] = (int((sat & m).sum()), int(m.sum()))
    return out


def reflection_stats(rows: dict) -> dict:
    """``rows``: {pitch key: [(saturated px, front-piece px) in yaw order]}. The largest change between yaw neighbours
    (px and share of the front piece), the largest share, and ``flag`` 'lens_reflection_slab' when the share jump
    exceeds ``SLAB_JUMP_SHARE``."""
    max_jump_px, max_jump_share, max_share, max_px = 0, 0.0, 0.0, 0
    per = {}
    for key, seq in rows.items():
        shares = [s / max(n, 1) for s, n in seq]
        jumps = [abs(b - a) for a, b in zip(shares, shares[1:])]
        jumps_px = [abs(b[0] - a[0]) for a, b in zip(seq, seq[1:])]
        per[key] = {"saturated_px": [int(s) for s, _ in seq], "share": [round(x, 4) for x in shares]}
        max_jump_px = max([max_jump_px] + jumps_px)
        max_jump_share = max([max_jump_share] + jumps)
        max_share = max([max_share] + shares)
        max_px = max([max_px] + [int(s) for s, _ in seq])
    return {"max_jump_px": int(max_jump_px), "max_jump_share": round(float(max_jump_share), 4), "max_saturated_share": round(float(max_share), 4),
            "max_saturated_px": int(max_px), "flag": "lens_reflection_slab" if max_jump_share > SLAB_JUMP_SHARE else None,
            "threshold_jump_share": SLAB_JUMP_SHARE, "per_pitch": per}


def lens_reflection_metric(glb_path: Path, out_dir: Path) -> dict:
    """The observation's ``lens_reflection``: measured from the sweep renders of one harness run, else unmeasured."""
    ids = [v["id"] for v in POSE_SWEEP_VIEWS]
    got = front_piece_saturation(Path(glb_path), Path(out_dir), set(ids))
    rows = {}
    for p in POSE_SWEEP_PITCHES:
        seq = [got.get(view_id(p, y)) for y in POSE_SWEEP_YAWS]
        if all(s is not None for s in seq):
            rows[pitch_key(p)] = seq
    if not rows:
        return {"status": "unmeasured", "reason": "no sweep render with a projectable front piece"}
    return {"status": "measured", **reflection_stats(rows)}


def sweep_sheet(renders: dict, out: Path, stats: dict | None = None) -> Path | None:
    """One row per pitch, the yaws left to right, each tile cropped to the union content box of every sweep render."""
    from bsa import archeck
    from .observe import _label, stack
    paths = [renders.get(v["id"]) for v in POSE_SWEEP_VIEWS]
    have = [p for p in paths if p and Path(p).is_file()]
    if not have:
        return None
    box = archeck.content_box(have)
    rows = []
    for p in POSE_SWEEP_PITCHES:
        tiles = []
        for y in POSE_SWEEP_YAWS:
            path = renders.get(view_id(p, y))
            if not path or not Path(path).is_file():
                continue
            im = Image.open(path).convert("RGB").crop(box)
            im = im.resize((max(1, int(im.size[0] * SHEET_TILE_HEIGHT / max(im.size[1], 1))), SHEET_TILE_HEIGHT), Image.LANCZOS)
            share = ((stats or {}).get("per_pitch", {}).get(pitch_key(p), {}).get("share") or [None] * len(POSE_SWEEP_YAWS))[POSE_SWEEP_YAWS.index(y)]
            tiles.append(_label(im, f"yaw {y:+d} pitch {p:+d}" + (f" sat {share:.2f}" if share is not None else "")))
        if tiles:
            row = Image.new("RGB", (sum(t.size[0] for t in tiles) + 6 * (len(tiles) - 1), SHEET_TILE_HEIGHT), (255, 255, 255))
            x = 0
            for t in tiles:
                row.paste(t, (x, 0))
                x += t.size[0] + 6
            rows.append(row)
    stack(rows).save(out)
    return Path(out)
