"""WP5 steps 2-7 driver: ``project_photo_texture`` (WP5_DESIGN §4, §6), host side, single-source mode.

Owner decision (PROGRESS 'WP5 resumed'): registration did not pass on FT1119, so every region is painted from its
anchor photo only (tier 2), never mixing unregistered views; a region with a registered set (>= 2 views that passed
the §5.4 gate in both directions and both folds, from step 0's ``registered``) is mixed in tier 1 by cos^2 x px/mm.
The strict pixel proof is a diagnostic for Astra, not a gate.

Fix round 1: mirror fill only on surfaces no view sees at donor angles; chart-seam harmonisation never averages two
photos.

Fix round 2:
- **Labels** (``label_moves='crease'``, production): region boundaries are re-placed by the crease / photometric MRF
  of ``labels`` (each face ONE source view, only its region's anchor or an ADJACENT region's anchor, never a view that
  does not see it; creases where they exist, else the line where the two gained photos agree best). The strict
  anchor-only labels of fix round 1 are ``label_moves='off'``; the round-1 MRF is the diagnostic ``'mrf'``. Per region
  the report gives ``painted_by`` and ``non_anchor_share`` (texels painted by an adjacent region's anchor), and
  ``foreign_view_texels`` (painted by any other view) must be 0.
- **The < 30 % cover rule** (§9) measures what the anchors SEE: ``source_seen_share`` = texels seen by their painting
  view (before the text and highlight masks, which are policy, not missing photo) over the part's AR-visible texels a
  WORN frame shows (``WORN_HIDDEN``: a temple's inner and bottom faces rest against the head and are excluded; measured
  2026-10-06 on FT1119: 52 % of the +x temple's AR-visible texels were those faces, which no photo anchors). Mirror
  partners are judged TOGETHER (their pooled share), so both temples get print or neither does. A part (pair) below
  30 % gets NO texture (flat grey, class ``no_texture``, left out of ``texture_parts``, the list attach_print must
  use) unless ``override_min_cover``. The pre-mask numbers and the old measure (``anchor_share_ar``) are reported.
- **Mirror fill** keeps its candidates (surfaces no view sees at donor angles) and reports, per view, how many
  mirrored texels a photo shows obliquely (first surface, cos > 0.1, inside the matte), warning above 25 %.
- **Foreign blobs** (``texels``): metal a photo shows on print that the model lacks is a hole for that view and is
  reported to Astra.

Build step 4 (host fixes, 2026-10-06; diagnoses and measurements in PORT_REVIEW §10 and the modules):
- donor holes for metal and highlight colours, hardware margins, thin glint / outline lines and dark rivet blobs
  (``metal``, applied in ``texels.view_samples``; every reason is counted per photo in ``report['metal']``);
- the hole filler is block quilting (``fill='quilt'``, radius 0.9 / 1.2 mm by the knockout), ``fill='patchmatch'``
  keeps the design's PyPatchMatch; fix round 1: quilting is a VISUAL choice that costs A4 (``FILL_CHOICE``;
  ``compare_fill`` has the knockout choose another filler's own radius too, for the comparison), and small enclosed
  holes (<= 2 mm2 inside photo areas) are filled locally by PatchMatch (``fill.small_enclosed``);
- gains: stage-1 mirror edges and same-part boundaries (02 and 03 were unconnected), stage-2 same-part edges, mirror
  equations and a robust pass, then the stage-3 levelling field (``gain_field.npz``; A3's read back applies it);
- chart seams: the losing side's fill takes the winner's texture reflected across the seam before the harmonisation.

Correction round (2026-10-06, after the AR tool test of 10bf932; diagnoses and numbers in PORT_REVIEW section 10):
- ``flatten`` ('test' by default; ``shading``): every photo's own normal-space SH shading is fitted on its donor texels
  and divided out in linear light, kept only if the photos then agree better on the same surface (pooled gained dE00
  down >= 0.3 and median |dlog Y| down, also with any one view pair left out: fix round 1);
- ``level`` ({'gain', 'gamma'} from ``tests/photoprint/ar_calibrate.py``, or None): the AR calibration to the photos,
  applied to every textured texel of the composed atlas as the last step (``shading.level_map``);
- donors: the bicubic footprint (fix round 1: also clear of bright pixels' light anti-aliased edges) and the
  photometric occluder margin (``texels``), no large-hardware margin and shoulder-only glint dilation (``metal``);
- ``strip_donors`` (default on; ``quilt_t``): quilting on a strip chart takes its donor windows from the same t across
  the strip (the -x temple's streaks were the edge glint and dark edge line copied into the middle).

Steps (each in its module): layout (``g3d.printuv``, numpy core; A1 must pass) -> texels and per-view donor samples
past the whole scene (``texels``) -> anchors and tiers -> labels fixed at the anchors (``labels``) ->
gains to the reference view from same-surface overlaps, else across region boundaries (``gains``) -> highlights
detected and made holes (``highlights``) -> text mask (D6, decals, bright strokes, see-through text) -> P0 (the photo
layer) -> mirror fill of surfaces no view sees, outside the text mask -> PatchMatch fill per (part, facing class) with
radii from the knockout -> label-seam band repair (2 or 4 texels by test) -> chart-seam harmonisation -> the < 30 %
anchor-cover rule -> compose (A3) -> gutter padding -> PNGs <= 4096.

Outputs under ``out_dir``: print.png (sRGB), classes.png, source.png (view index + 1), tier.png, layout.json,
registration.json (the single-source plan: anchors, admitted views, statuses, registered sets used), report.json
(shares, warnings, checks, timings) and report_stable.json (the same without timings, for A7). The proofs take the
texel arrays from the returned dict. Thresholds are required (no silent default); lens parts and parts with metallic
>= 0.5 are refused.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import time
from pathlib import Path

import numpy as np
import scipy
from PIL import Image

from glasses3d.g3d import printuv as PU

from .. import lens_id
from .. import raster
from .. import texture_utils as T
from . import edges as ED
from . import fill as FL
from . import gains as GN
from . import highlights as HL
from . import labels as LB
from . import manifest as MF
from . import quilt_t as QT
from . import regions as RG
from . import seams as SM
from . import shading as SH
from . import texels as TX
from .compose import CLASS_CODES, compose
from .inputs import sha256_array, short_name as IN_short
from .procs import Progress, opt_out_ecoqos, pool_map
from .report import write_json

ATLAS_MAX = 4096
DENSITY_FACTOR = 2.0                 # 2 x the parts' max donor px/mm (§2 "Density")
PATCHMATCH_WARN = 0.25               # warning above 25 % PatchMatch share of a part's AR-visible area
ANCHOR_MIN_COVER = 0.30              # a part whose anchors cover < 30 % gets no texture unless overridden
AR_POSES = [(y, p) for y in (0, 30, -30, 45, -45) for p in (-20, 0, 20)]
AR_SHAPE = (1400, 1400)
AR_PPM = 6.0
NO_TEXTURE_RGB = (128, 128, 128)     # texels of a part left without texture by the < 30 % rule (attach_print skips it)
LABEL_MOVES = ("crease", "off", "mrf")
WORN_HIDDEN = {"temple": ("inner", "bottom")}   # facing classes a worn frame hides (cover-rule denominator only)
MIRROR_GRAZE_WARN = 0.25             # warn when more than 25 % of the mirrored texels lie on surfaces a photo shows


class RefusedError(ValueError):
    """The request cannot be honoured (missing thresholds, lens or metal parts, failed layout)."""


# ----------------------------------------------------------------------------- refusals
def job_glb(job) -> str:
    """The job's GLB (a synthetic job carries q001's geometry and names its source instead)."""
    from .inputs import FT1119
    return str(job.sources.get("glb") or FT1119["glb"])


def part_materials(job) -> dict[str, dict]:
    """Per part name: material name, metallic factor and the lens decision (``lens_id`` rules 1-2)."""
    from .. import glb as glbmod
    g = glbmod.load(job_glb(job))
    out = {}
    for p in g.primitives:
        nm = p.node_name.split(".")[0]
        m = g.material(p.material_index) or {}
        pbr = m.get("pbrMetallicRoughness") or {}
        d, _w = lens_id.decide_explicit([("material", m.get("extras")), ("node", None)])
        if d is None:
            d = lens_id.decide_name(m.get("name"), p.node_name)
        out[nm] = {"material": m.get("name"), "metallic": float(pbr.get("metallicFactor", 1.0)),
                   "lens": bool(d.is_lens) if d is not None else False}
    return out


def refuse_parts(job, parts) -> None:
    mats = part_materials(job)
    bad = []
    for p in parts:
        m = mats.get(p)
        if m is None:
            bad.append(f"{p}: not in the model")
        elif m["lens"]:
            bad.append(f"{p}: a lens part (no print on lenses)")
        elif m["metallic"] >= 0.5:
            bad.append(f"{p}: metallic {m['metallic']:.2f} >= 0.5")
    if bad:
        raise RefusedError("refused parts: " + "; ".join(bad))


# ----------------------------------------------------------------------------- helpers
def registered_from_step0(report_json) -> dict:
    """{region name: registered views} for the regions whose step-0 status is 'registered' (the gate passed against
    the anchor in both directions and both folds AND check_print passed, WP5_DESIGN §9): the only regions mixed in
    tier 1. Every other region is single-source."""
    d = json.loads(Path(report_json).read_text(encoding="utf-8"))
    return {name: list(st.get("registered_views") or []) for name, st in (d.get("regions") or {}).items()
            if st.get("status") == "registered" and len(st.get("registered_views") or []) >= 2}


def donor_density(job, parts, admitted) -> float:
    """The largest px/mm at the front pivot among views admitted for any of the parts."""
    vals = [job.ppm(n) for n in job.views if any(admitted.get((n, p), False) for p in parts)]
    return max(vals) if vals else max(job.ppm(n) for n in job.views)


def ar_visible_faces(job, faces: np.ndarray, occ: TX.OcclusionScene) -> np.ndarray:
    """Per layout face: visible (centroid or a corner, cos > 0.15, first surface) in at least one of 15 AR poses
    (yaw 0 / +-30 / +-45 x pitch -20 / 0 / 20) of the whole scene on S."""
    tri = job.mesh.V[job.mesh.F[faces]]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    pts = np.concatenate([tri.mean(1)[:, None], 0.8 * tri + 0.2 * tri.mean(1, keepdims=True)], 1)   # (k, 4, 3)
    P = pts.reshape(-1, 3)
    N = np.repeat(n, 4, 0)
    sc = occ.scene(job.mesh.V)
    vis = np.zeros(len(P), bool)
    for yaw, pitch in AR_POSES:
        cam = raster.view_camera(job.frame, yaw, pitch, AR_PPM, AR_SHAPE)
        v, _ = T.visible_points(P, N, cam, job.frame, sc, AR_SHAPE)
        vis |= v
    return vis.reshape(-1, 4).any(1)


def cover_rule(seen_den: dict, partners: dict, limit: float = ANCHOR_MIN_COVER) -> tuple[dict, list]:
    """The < 30 % rule (§9; fix round 2): {part: (seen texels, AR-visible worn texels)} and {part: mirror partner}
    -> ({part: {'members', 'pooled_source_seen_share'}}, parts left without texture). Mirror partners are judged on
    their POOLED share, so both get print or neither does."""
    cover = {}
    for p in seen_den:
        q = partners.get(p)
        members = sorted({p, q}) if q in seen_den else [p]
        seen = sum(seen_den[m][0] for m in members)
        den = sum(seen_den[m][1] for m in members)
        cover[p] = {"members": members, "pooled_source_seen_share": float(seen / max(den, 1))}
    return cover, [p for p in seen_den if cover[p]["pooled_source_seen_share"] < limit]


def mirror_regions(job, pids, regions) -> dict:
    """{region id: the region of the mirror partner part with the same facing class} (parts paired by
    ``fill.mirror_partners``: x -> -x centroids within 5 mm; never by name)."""
    partners = FL.mirror_partners(job, pids)
    names = job.mesh.names
    pname = {names[p]: names[q] for p, q in partners.items()}
    key = {(r.part_name, r.facing): r.rid for r in regions}
    return {r.rid: key[(pname[r.part_name], r.facing)] for r in regions
            if r.part_name in pname and (pname[r.part_name], r.facing) in key}


AREA_GROUPS = ("registered", "anchor-only", "single-view", "unseen-filled")


def area_group(st: dict) -> str:
    """The area-table group of one region status row: 'unseen' -> 'unseen-filled'; a 'minor' region (a small end face,
    step 0's status) goes by the tool's own rule on its anchors (no anchor -> unseen-filled, other views -> anchor-only,
    else single-view)."""
    s = st["status"]
    if s in ("registered", "anchor-only", "single-view"):
        return s
    if s == "unseen" or st.get("anchor") is None:
        return "unseen-filled"
    return "anchor-only" if st.get("others") else "single-view"


def area_table(statuses: dict, regions, tex, klass: np.ndarray, part_rows: dict) -> dict:
    """Area shares for Astra (build step 3 run): per status group (registered / anchor-only / single-view /
    unseen-filled; ``area_group``) the area in mm2 and its share, and how that area is painted (texel classes,
    region area x the class's share of the region's texels); per part the same split plus the AR-visible shares
    (photo, mirror, PatchMatch, text fill, highlight fill) from ``parts_summary``; the mirrored share and the PatchMatch
    share per part. Every region is in exactly one group."""
    names = {v: k for k, v in CLASS_CODES.items()}
    total = float(sum(r.area_mm2 for r in regions)) or 1.0
    groups = {g: {"mm2": 0.0, "regions": [], "painted_mm2": {}} for g in AREA_GROUPS}
    per_part: dict = {}
    painted_all: dict = {}
    for r in regions:
        st = statuses[r.name]
        g = area_group(st)
        sel = tex.region == r.rid
        nt = int(sel.sum())
        cnt = np.bincount(klass[sel], minlength=len(CLASS_CODES)) if nt else np.zeros(len(CLASS_CODES), np.int64)
        row = groups[g]
        row["mm2"] += r.area_mm2
        row["regions"].append(r.name + (" (minor end face)" if st["status"] == "minor" else ""))
        pp = per_part.setdefault(r.part_name, {"mm2": 0.0, "groups_mm2": {k: 0.0 for k in AREA_GROUPS},
                                               "painted_mm2": {}})
        pp["mm2"] += r.area_mm2
        pp["groups_mm2"][g] += r.area_mm2
        for c, k in enumerate(cnt):
            if k == 0:
                continue
            a = r.area_mm2 * float(k) / nt
            row["painted_mm2"][names[c]] = row["painted_mm2"].get(names[c], 0.0) + a
            pp["painted_mm2"][names[c]] = pp["painted_mm2"].get(names[c], 0.0) + a
            painted_all[names[c]] = painted_all.get(names[c], 0.0) + a
    rnd = lambda d: {k: round(v, 1) for k, v in sorted(d.items())}
    out_groups = {g: {"mm2": round(v["mm2"], 1), "share": round(v["mm2"] / total, 4), "regions": v["regions"],
                      "painted_mm2": rnd(v["painted_mm2"]),
                      "painted_share_of_group": {k: round(x / v["mm2"], 4) for k, x in sorted(v["painted_mm2"].items())}
                      if v["mm2"] > 0 else {}} for g, v in groups.items()}
    out_parts = {}
    for p, v in per_part.items():
        pr = part_rows.get(p, {})
        out_parts[p] = {"mm2": round(v["mm2"], 1), "groups_share": {k: round(x / v["mm2"], 4) for k, x in
                                                                    v["groups_mm2"].items()},
                        "painted_share": {k: round(x / v["mm2"], 4) for k, x in sorted(v["painted_mm2"].items())},
                        "ar_visible": {k: (round(pr[k], 4) if pr.get(k) is not None else None) for k in
                                       ("photo_share_ar", "mirror_share_ar", "patchmatch_share_ar", "text_fill_share_ar",
                                        "highlight_share_ar")}}
    return {"total_mm2": round(total, 1), "groups": out_groups,
            "painted_mm2": rnd(painted_all), "painted_share": {k: round(x / total, 4) for k, x in sorted(painted_all.items())},
            "mirrored_share": round(painted_all.get("mirror", 0.0) / total, 4),
            "patchmatch_share_per_part": {p: v["painted_share"].get("patchmatch", 0.0) for p, v in out_parts.items()},
            "patchmatch_share_ar_per_part": {p: v["ar_visible"]["patchmatch_share_ar"] for p, v in out_parts.items()},
            "parts": out_parts,
            "rule": "area = region area x the class's share of the region's texels; groups from the region statuses "
                    "('minor' end faces by their anchors: none -> unseen-filled); registered = tier-1 mixing (none on "
                    "FT1119); anchor-only = other admitted views see it but do not register (painted from the anchor "
                    "only); single-view = one admitted view sees it; unseen-filled = no admitted view at donor angles "
                    "(mirror, then PatchMatch). The painted split shows that each group also carries fill (text, "
                    "highlights, occluder margins, seam bands)."}


def _seed_of(seed: int, *keys) -> int:
    h = int(seed) * 1_000_003
    for k in keys:
        for ch in str(k):
            h = (h * 131 + ord(ch)) % 2_147_483_647
    return h


def _pm_job(img, hole, donors, radius, seed, key, method="quilt", tband=None):
    r = FL.fill_call(method, img, hole, donors, radius, seed, tband=tband)
    r["key"] = key
    return r


def _knock_job(canvas_img, painted, strip_box, tx, radii_px, seed, key, method="quilt", tband=None):
    """Knockout of one part: every radius x hole; returns the tool's statistics per radius."""
    holes = FL.knockout_holes(painted, strip_box, tx)
    out = {"holes": [h for h, _ in holes], "radii": {}}
    for rp in radii_px:
        stats = []
        for hname, hm in holes:
            ys, xs = np.nonzero(hm)
            m = int(round(FL.KNOCK_MARGIN_MM * tx))
            y0, y1 = max(ys.min() - m, 0), min(ys.max() + m + 1, hm.shape[0])
            x0, x1 = max(xs.min() - m, 0), min(xs.max() + m + 1, hm.shape[1])
            sub = canvas_img[y0:y1, x0:x1]
            hs = hm[y0:y1, x0:x1]
            don = painted[y0:y1, x0:x1] & ~hs
            r = FL.fill_call(method, sub, hs, don, rp, _seed_of(seed, key, hname, rp),
                             tband=None if tband is None else tband[y0:y1, x0:x1])
            st = FL.knock_stats(r["out"], sub, hs, painted[y0:y1, x0:x1], tx)
            st.update({"hole": hname, "outside_changed": r["outside_changed"], "hole_changed": r["hole_changed"]})
            stats.append(st)
        if stats:
            e1 = float(np.median([s["e1"] for s in stats]))
            e2 = float(np.median([s["e2"] for s in stats]))
            de = float(np.mean([s["de_mean"] for s in stats]))
            seam = float(np.mean([s["seam"] for s in stats]))
            ok = e1 <= FL.A4_LOG_ENERGY and e2 <= FL.A4_LOG_ENERGY and de <= FL.A4_DE00 and seam <= FL.A4_SEAM
            out["radii"][int(rp)] = {"median_log_energy": [e1, e2], "mean_colour_de00": de, "seam_ratio": seam,
                                     "pass": bool(ok), "holes": stats}
    return out


SMALL_TILE = 160                 # small enclosed holes: PatchMatch on 160-texel cores with 40-texel donor margins
SMALL_TILE_MARGIN = 40
FILL_CHOICE = (
    "quilt is the production filler for a VISUAL reason (the owner saw PyPatchMatch's wood-grain smears along the long "
    "strips, PROGRESS 2026-10-06), NOT because it fills better by A4: graded by the instrument with each filler at its "
    "own knockout-chosen radius on the same holes, PatchMatch is closer to A4 on every FT1119 part (near-passable on "
    "the temples) and quilting costs A4; see bake.json 'A4_fillers' and PORT_REVIEW section 10 (fix round 1 of step 4)")


def _rows_mask(cv, img: np.ndarray, n: int) -> np.ndarray:
    """(n,) texel mask of a canvas image's True pixels (the canvas's own texels only)."""
    out = np.zeros(n, bool)
    out[cv.rows[cv.gather(img).astype(bool)]] = True
    return out


def _radii_set(method: str) -> tuple:
    """The radii the tool's knockout chooses from, per filler (quilting's blocks need wider windows)."""
    return FL.QUILT_RADII_MM if method == "quilt" else FL.RADII_MM


def _choose_radius(kr: dict, radii_set, tx: float) -> tuple[float, bool]:
    """(radius mm, passes the tool's A4-like limits): the passing radius with the smallest worst median |log energy|,
    else the best score (energy + colour excess / 10 + seam excess)."""
    rmap = {FL.radius_px(r, tx): r for r in radii_set}
    passing = [(max(v["median_log_energy"]), rp) for rp, v in kr["radii"].items() if v["pass"]]
    scored = [(max(v["median_log_energy"]) + max(0.0, v["mean_colour_de00"] - FL.A4_DE00) / 10
               + max(0.0, v["seam_ratio"] - FL.A4_SEAM), rp) for rp, v in kr["radii"].items()]
    pick = min(passing)[1] if passing else (min(scored)[1] if scored else FL.radius_px(radii_set[0], tx))
    return rmap.get(pick, pick / tx), bool(passing)


def _knock_summary(kr: dict) -> dict:
    """A knockout result without its per-hole rows (what the report keeps for a compared filler)."""
    return {"chosen_radius_mm": kr.get("chosen_radius_mm"), "chosen_passes_a4": kr.get("chosen_passes_a4"),
            "holes": kr.get("holes"),
            "radii": {rp: {k: v[k] for k in ("median_log_energy", "mean_colour_de00", "seam_ratio", "pass")}
                      for rp, v in kr.get("radii", {}).items()}}


# ----------------------------------------------------------------------------- the driver
def project_photo_texture(job, parts, *, out_dir, print_reg_mm=None, edge_mean_mm=None, edge_p90_mm=None,
                          views="all", registered: dict | None = None, layout=None, tx_per_mm: float | None = None,
                          atlas_max: int = ATLAS_MAX, mirror: str = "auto", keep_photo_text: bool = False,
                          fill: str = "quilt", highlights: str = "auto", flatten: str = "test",
                          level: dict | None = None, level_source: str | None = None, strip_donors: bool = True,
                          radii_mm: dict | None = None, seed: int = 0, workers: int | None = None,
                          override_min_cover: bool = False, write: bool = True, step0_report=None,
                          label_moves: str = "crease", min_cover: float = ANCHOR_MIN_COVER,
                          compare_fill: tuple = (), log=print) -> dict:
    """Bake the print of ``parts`` (single-source mode; see the module docstring). ``registered`` = {region name:
    [views]} from step 0 (tier 1 where >= 2). ``views`` = 'all' or the view names to use (B3 bakes leave one out).
    ``radii_mm`` = {part: radius mm} to skip the knockout. ``step0_report`` = step 0's report.json: its 'registered'
    regions are mixed in tier 1 and its statuses are reported (else the bake's own: 'anchor-only' when another
    admitted view sees >= 10 % of the region). ``label_moves`` = 'crease' (production, fix round 2: boundaries re-placed
    on creases / the photometric seam, each face one view, only adjacent anchors), 'off' (strict anchor-only labels)
    or 'mrf' (the round-1 MRF, diagnostic). ``compare_fill`` (fix round 1 of step 4): other fillers whose radius the
    tool's knockout also chooses, on the same holes and seeds, from that filler's own radius set (``_radii_set``); the
    report's ``fill['by_method']`` and the result's ``radii_by_method`` carry them, so a comparison of fillers runs each
    at ITS OWN radius (the production fill uses only ``fill``). ``flatten`` / ``level`` / ``strip_donors``: the
    correction round (module docstring); ``level_source`` names where the level came from (the calibration file).
    Returns the result dict (arrays included)."""
    t0 = time.perf_counter()
    opt_out_ecoqos()
    prog = Progress("print", log)
    if print_reg_mm is None or edge_mean_mm is None or edge_p90_mm is None:
        raise RefusedError("thresholds missing: print_reg_mm, edge_mean_mm and edge_p90_mm must be given "
                           "(no silent default)")
    if fill not in FL.FILL_METHODS:
        raise RefusedError(f"unknown fill {fill!r} (one of {FL.FILL_METHODS})")
    if any(m_ not in FL.FILL_METHODS for m_ in compare_fill):
        raise RefusedError(f"unknown compare_fill {tuple(compare_fill)!r} (each one of {FL.FILL_METHODS})")
    if flatten not in SH.FLATTEN_MODES:
        raise RefusedError(f"unknown flatten {flatten!r} (one of {SH.FLATTEN_MODES})")
    if level is not None and not (float(level.get("gain", 0)) > 0 and float(level.get("gamma", 0)) > 0):
        raise RefusedError(f"level needs a positive 'gain' and 'gamma', got {level!r}")
    if label_moves not in LABEL_MOVES:
        raise RefusedError(f"unknown label_moves {label_moves!r} (one of {LABEL_MOVES})")
    with prog.heartbeat():
        parts = list(parts)
        refuse_parts(job, parts)
        timings = {}
        use_views = [n for n in job.views if views == "all" or n in set(views)]
        pids = job.part_ids(parts)
        names = job.mesh.names

        # edges (D2) and regions
        t = time.perf_counter()
        edges = ED.edge_table(job, pids, views=use_views, mean_mm=edge_mean_mm, p90_mm=edge_p90_mm)
        adm = ED.admitted(edges)
        groups = RG.rigid_groups(job)
        regions = RG.build_regions(job, parts, groups, n_samples=64, seed=seed)
        by = {r.rid: r for r in regions}
        mirror_region = mirror_regions(job, pids, regions)
        timings["edges_regions_s"] = round(time.perf_counter() - t, 2)

        # layout (A1)
        t = time.perf_counter()
        if layout is None:
            tx = float(tx_per_mm) if tx_per_mm else DENSITY_FACTOR * donor_density(job, parts, adm)
            layout = PU.layout(job.mesh.V, job.mesh.F, job.mesh.part, names, parts, tx, atlas_max=atlas_max,
                               mirror=(mirror != "off"))
        if not layout.report.get("pass"):
            raise RefusedError(f"print UV layout fails A1: {layout.report.get('criteria')}")
        tx = float(layout.tx_per_mm)
        timings["layout_s"] = round(time.perf_counter() - t, 2)
        prog(f"layout {layout.width} x {layout.height} at {tx:.2f} tx/mm, {len(layout.charts)} charts, "
             f"partners {layout.partners}", force=True)

        # texels and view samples
        t = time.perf_counter()
        tex = TX.atlas_texels(job, layout, regions)
        decals = TX.decal_geometry(job_glb(job), job.mesh.skipped)
        occ = TX.OcclusionScene(job, pids, decals)
        samples = {}
        for n in use_views:
            samples[n] = TX.view_samples(job, tex, n, occ)
            prog(f"samples {n}: {samples[n].reasons}")
        timings["samples_s"] = round(time.perf_counter() - t, 2)
        # correction round: the photos' own shading divided out (kept only if the photos then agree better)
        t = time.perf_counter()
        flat = SH.flatten(job, tex, samples, mode=flatten)
        if flat["applied"]:
            for n_, s_ in samples.items():
                s_.col = flat["cols"][n_]
        flat_report = {k: v for k, v in flat.items() if k != "cols"}
        timings["flatten_s"] = round(time.perf_counter() - t, 2)
        prog(f"flatten ({flatten}): applied {flat['applied']}, keep {flat.get('keep')}", force=True)
        vlist = list(samples)
        vindex = {n: i for i, n in enumerate(vlist)}

        # anchors, tiers, region labels
        anchors = TX.region_anchors(tex, regions, samples, adm)
        step0_status = {}
        step0_info = None
        if step0_report is not None:
            registered = {**registered_from_step0(step0_report), **(registered or {})}
            d0 = json.loads(Path(step0_report).read_text(encoding="utf-8"))
            step0_status = {k: v.get("status") for k, v in (d0.get("regions") or {}).items()}
            step0_info = {"path": str(step0_report), "sha256": hashlib.sha256(Path(step0_report).read_bytes()).hexdigest(),
                          "A0_passed": d0.get("A0_passed"), "status": d0.get("status")}
        registered = registered or {}
        region_label, tier_of_region = {}, {}
        for r in regions:
            a = anchors[r.rid]["anchor"]
            reg = [v for v in registered.get(r.name, []) if v in samples]
            if len(reg) >= 2:
                region_label[r.rid] = LB.MIX
                tier_of_region[r.rid] = 1
            elif a is not None:
                region_label[r.rid] = vindex[a]
                tier_of_region[r.rid] = 2
            else:
                region_label[r.rid] = LB.NONE
                tier_of_region[r.rid] = 3

        # face weights and labels on creases
        t = time.perf_counter()
        nlf = len(layout.faces)
        frow = layout.face_index(len(job.mesh.F))
        tf = frow[tex.face]
        nf = np.bincount(tf, minlength=nlf).astype(float)
        wf = np.zeros((nlf, len(vlist)))
        for n, s in samples.items():
            wf[:, vindex[n]] = np.bincount(tf, weights=s.w, minlength=nlf) / np.maximum(nf, 1)
        lut = np.full(len(job.mesh.F), -1, np.int64)
        for r in regions:
            lut[r.faces] = r.rid
        face_cols = None
        if label_moves == "crease":
            # gained face colours for the photometric seam: stage-1 gains on the START labels (the final gains are
            # solved again on the final labels below)
            rl0 = np.full(max(by) + 2, LB.NONE, np.int64)
            for rid, lab_ in region_label.items():
                rl0[rid] = lab_
            start_tex = rl0[np.where(tex.region >= 0, tex.region, -1)]
            ch0 = GN.solve_gains(vlist, start_tex, GN.pair_gains(tex, samples, vlist, start_tex,
                                                                 mirror_region=mirror_region),
                                 GN.reference_view(job.views, vlist, start_tex))
            face_cols = np.zeros((nlf, len(vlist), 3))
            for n_, s_ in samples.items():
                k_ = vindex[n_]
                gcol = GN.apply(s_.col, ch0[n_]) if n_ in ch0 else s_.col
                cnt = np.bincount(tf, weights=s_.ok.astype(float), minlength=nlf)
                for c_ in range(3):
                    face_cols[:, k_, c_] = np.bincount(tf, weights=np.where(s_.ok, gcol[:, c_], 0.0),
                                                       minlength=nlf) / np.maximum(cnt, 1)
        pl = LB.place_labels(job.mesh.V, job.mesh.F, layout.faces, lut[layout.faces], region_label, wf, nf, vlist, tx,
                             mode=label_moves, face_colours=face_cols, part_of_face=job.mesh.part[layout.faces])
        face_label = pl["labels"]
        label = face_label[tf]
        timings["labels_s"] = round(time.perf_counter() - t, 2)
        prog(f"labels ({label_moves}): {pl['moved_faces']} faces moved; boundary on creases "
             f"{pl['boundary_start']['on_crease_share']} -> {pl['boundary']['on_crease_share']}", force=True)
        rl = np.full(max(by) + 2, LB.NONE, np.int64)
        for rid, lab_ in region_label.items():
            rl[rid] = lab_
        anchor_label = rl[np.where(tex.region >= 0, tex.region, -1)]     # per texel: its region's label

        # painted colours (raw), gains, highlights
        n = len(tex)
        raw = np.zeros((n, 3))
        painted = np.zeros(n, bool)
        for i, v in enumerate(vlist):
            m = (label == i) & samples[v].ok
            raw[m] = samples[v].col[m]
            painted |= m
        edges_g = GN.pair_gains(tex, samples, vlist, label, mirror_region=mirror_region)
        gref = GN.reference_view(job.views, vlist, label)
        chain = GN.solve_gains(vlist, label, edges_g, gref)
        gained = {v: GN.apply(samples[v].col, chain[v]) if v in chain else samples[v].col for v in vlist}
        col = np.zeros((n, 3))
        for i, v in enumerate(vlist):
            m = (label == i) & samples[v].ok
            col[m] = gained[v][m]
        grp = GN.group_gains(tex, painted & (label >= 0), label, col, vindex.get(gref), mirror_region=mirror_region)
        adj = painted & (label >= 0)
        col[adj] = GN.apply_per_texel(col[adj], grp["gain"][adj])
        # stage 3 (step 4 fix 3): the smooth levelling gain field across region boundaries (and across uv seams)
        pairs = SM.chart_seam_pairs(job, layout, tex)
        lev = GN.level_field(tex, adj, label, col, vindex.get(gref), pairs, tx=tx)
        col[adj] = GN.apply_per_texel(col[adj], lev["gain"][adj])
        mix = label == LB.MIX
        if mix.any():                                       # tier 1: registered sets, weighted by cos^2 x px/mm
            acc = np.zeros((n, 3))
            wsum = np.zeros(n)
            for r in regions:
                if region_label.get(r.rid) != LB.MIX:
                    continue
                sel = mix & (tex.region == r.rid)
                for v in registered.get(r.name, []):
                    if v not in samples:
                        continue
                    w = np.where(sel, samples[v].w, 0.0)
                    acc += w[:, None] * gained[v]
                    wsum += w
            got = mix & (wsum > 0)
            col[got] = acc[got] / wsum[got, None]
            painted |= got
        hl = {"mask": np.zeros(n, bool), "specular": 0, "witness": 0, "dilated": 0}
        if highlights != "off":
            wit = {}
            for i, v in enumerate(vlist):
                ws = []
                for j, u in enumerate(vlist):
                    if j == i:
                        continue
                    okp = samples[u].ok & np.array([adm.get((u, names[p]), False) for p in range(len(names))])[tex.part]
                    ws.append((j, okp, gained[u]))
                wit[i] = ws
            hl = HL.flag(tex, painted, col, label, wit, tx=tx)
        holes_hl = hl["mask"]

        # text mask (D6)
        tm = FL.text_mask(job, tex, decals, painted, col, tx, label=label, views=vlist)
        text_fill = tm["mask"] if (tm["decals"] and not keep_photo_text) else np.zeros(n, bool)

        # P0 = the photo layer
        photo = painted & ~holes_hl & ~text_fill
        seen_any = np.zeros(n, bool)                 # a valid donor in an admitted view (reported)
        seen_geom = np.zeros(n, bool)                # seen at donor angles past the whole scene by ANY view
        seen_graze = {}                              # first surface, cos > 0.1, inside the matte, per view
        for v, s in samples.items():
            seen_any |= s.ok & np.array([adm.get((v, names[p]), False) for p in range(len(names))])[tex.part]
            seen_geom |= s.seen
            seen_graze[v] = s.seen_graze if s.seen_graze is not None else s.seen

        # mirror fill: surfaces no photo sees, outside the text mask
        t = time.perf_counter()
        mirrored = np.zeros(n, bool)
        mirror_info = {"mode": mirror, "texels": 0}
        bad_col = photo & ~np.isfinite(col).all(1)
        if bad_col.any():                                    # fix round 1: say it here, not as a k-means NaN later
            per = {(vlist[int(i)] if 0 <= i < len(vlist) else str(int(i))): int(c)
                   for i, c in zip(*np.unique(label[bad_col], return_counts=True))}
            raise RuntimeError(f"non-finite photo colours after the gains on {int(bad_col.sum())} texels "
                               f"(by painting view: {per})")
        work = np.where(photo[:, None], col, 0.0)
        if mirror != "off":
            partners = FL.mirror_partners(job, pids)
            cand = ~photo & ~seen_geom & ~tm["mask"]
            src, found = FL.mirror_lookup(job, layout, tex, cand, partners)
            ok_m = cand & found
            ok_m[ok_m] = photo[src[ok_m]] & ~tm["mask"][src[ok_m]]
            work[ok_m] = col[src[ok_m]]
            mirrored = ok_m
            graze_any = np.zeros(n, bool)
            for v_ in vlist:
                graze_any |= seen_graze[v_]
            mirror_info.update({"mirrored_on_grazing_photographed_surface": int((ok_m & graze_any).sum()),
                                "mirrored_on_grazing_share": float((ok_m & graze_any).sum() / max(int(ok_m.sum()), 1)),
                                "mirrored_grazing_by_view": {v_: int((ok_m & seen_graze[v_]).sum()) for v_ in vlist},
                                "grazing_rule": "first surface, cos > 0.1 (texels.GRAZE_MIN_COS), inside the matte "
                                                "eroded 1 px (fix round 2: reported; mirror candidates unchanged)"})
            mirror_info.update({"texels": int(ok_m.sum()), "candidates": int(cand.sum()),
                                "candidate_rule": "not photo, not text, and no view sees the surface at donor angles "
                                                  "past the whole scene (fix round 1)",
                                "mirrored_on_seen_surface": int((ok_m & seen_geom).sum()),
                                "unpainted_seen_texels_to_patchmatch": int((~photo & seen_geom).sum()),
                                "partners": {names[p]: names[q] for p, q in partners.items()}})
        # the 3-texel boundary of every mirrored area (mirrored side) is re-synthesised
        from scipy import ndimage as _nd
        mimg = tex.image(mirrored, False, bool)
        inner = _nd.binary_erosion(mimg, iterations=FL.MIRROR_RING)[tex.ii, tex.jj]
        mirror_ring = mirrored & ~inner
        timings["mirror_s"] = round(time.perf_counter() - t, 2)

        # fill radius per part (knockout) and PatchMatch fill per (part, facing class)
        t = time.perf_counter()
        canvases = {p: FL.Canvas(layout, tex, p) for p in parts}
        tbands = {p: (QT.strip_t(cv_, [c_["kind"] for c_ in cv_.charts]) if strip_donors else None)
                  for p, cv_ in canvases.items()}
        methods = [fill] + [m_ for m_ in dict.fromkeys(compare_fill) if m_ != fill]
        radii_by = {m_: {} for m_ in methods}
        knock_by = {m_: {} for m_ in methods}
        if radii_mm:
            radii_by[fill] = {p: float(radii_mm[p]) for p in parts}
        else:
            kjobs = []
            for p in parts:
                cv = canvases[p]
                img = cv.image(np.clip(np.rint(work), 0, 255).astype(np.uint8), 0, np.uint8)
                pimg = cv.image(photo, False, bool)
                strip = [k for k, c in enumerate(cv.charts) if c["kind"] == "strip"]
                if not strip:
                    for m_ in methods:
                        radii_by[m_][p] = _radii_set(m_)[0]
                    continue
                k = strip[0]
                box = (int(cv.off[k][1]), int(cv.off[k][1]) + cv.charts[k]["h"] + 2,
                       int(cv.off[k][0]), int(cv.off[k][0]) + cv.charts[k]["w"] + 2)
                pimg = FL.single_label(pimg, cv.image(label, -9, np.int64), box)
                for m_ in methods:                    # same holes and seeds for every filler (key = the part)
                    kjobs.append((img, pimg, box, tx, [FL.radius_px(r, tx) for r in _radii_set(m_)], seed, p, m_,
                                  tbands[p]))
            kres = pool_map(_knock_job, kjobs, workers, progress=prog)
            for jb, kr in zip(kjobs, kres):
                p, m_ = jb[-3], jb[-2]
                radii_by[m_][p], kr["chosen_passes_a4"] = _choose_radius(kr, _radii_set(m_), tx)
                kr["chosen_radius_mm"] = radii_by[m_][p]
                knock_by[m_][p] = kr
            timings["knockout_s"] = round(time.perf_counter() - t, 2)
            prog(f"radii {radii_by}", force=True)
        radii = radii_by[fill]
        knock = knock_by[fill]

        t = time.perf_counter()
        hole = ~photo & ~(mirrored & ~mirror_ring)            # every chart texel without a final colour yet
        regcls = np.array([by[int(r)].facing if r >= 0 else "none" for r in range(max(by) + 1)] + ["none"], dtype=object)
        cls = regcls[np.where(tex.region >= 0, tex.region, -1)]
        pjobs, pkeys = [], []
        sjobs, smeta, small_out = [], [], {}
        rs_px = FL.radius_px(FL.SMALL_HOLE_RADIUS_MM, tx)
        small_info = {"max_mm2": FL.SMALL_HOLE_MM2, "ring_share_min": FL.SMALL_HOLE_RING_SHARE,
                      "filler": "patchmatch", "radius_mm": FL.SMALL_HOLE_RADIUS_MM, "radius_px": rs_px,
                      "donors": "the part's photo texels in a 160-texel tile + 40-texel margin", "per_part": {}}
        lab_all = T.lab_of(np.clip(work, 0, 255))
        for p in parts:
            cv = canvases[p]
            prow = cv.rows
            pm = np.zeros(n, bool)
            pm[prow] = True
            donors_all = photo & pm
            if not (hole & pm).any():
                continue
            fam = np.full(n, -1, np.int64)
            if donors_all.sum() >= FL.FAMILY_K * 10:
                _C, lbf = FL.kmeans_lab(lab_all[donors_all], FL.FAMILY_K, seed=_seed_of(seed, p))
                fam[donors_all] = lbf
            img = cv.image(np.clip(np.rint(work), 0, 255).astype(np.uint8), 0, np.uint8)
            rp = FL.radius_px(radii[p], tx)
            pimg_all = cv.image(donors_all, False, bool)
            for c in sorted(set(cls[hole & pm])):
                hsel = hole & pm & (cls == c)
                himg = cv.image(hsel, False, bool)
                # fix round 1: small enclosed holes (metal / glint holes inside photo areas) are filled locally by
                # PatchMatch from the part's photo texels; the filler takes the rest
                small = FL.small_enclosed(himg, pimg_all, tx)
                if small.any():
                    small_out[(p, c)] = (small, img.copy())
                    for k, (core, crop) in enumerate(FL.band_tiles(small, SMALL_TILE, SMALL_TILE_MARGIN)):
                        sjobs.append((img, small, pimg_all, core, crop, rs_px, _seed_of(seed, p, c, "small", k),
                                      "patchmatch"))
                        smeta.append((p, c))
                    nlab = _nd.label(small, structure=np.ones((3, 3), bool))[1]
                    small_info["per_part"].setdefault(p, {"components": 0, "texels": 0})
                    small_info["per_part"][p]["components"] += int(nlab)
                    small_info["per_part"][p]["texels"] += int(small.sum())
                    himg = himg & ~small
                    hsel = hsel & ~_rows_mask(cv, small, n)
                if not himg.any():
                    continue
                dsel = donors_all & (cls == c)
                ring = _nd.binary_dilation(himg, iterations=FL.RING) & ~himg & pimg_all
                rf = cv.image(fam, -1, np.int64)[ring]
                rf = rf[rf >= 0]
                fams = [int(k) for k in np.unique(rf) if (rf == k).mean() >= FL.FAMILY_MIN_SHARE] if len(rf) else []
                dfam = dsel & (np.isin(fam, fams) if fams else True)
                need = max(2000, 2 * int(hsel.sum()))
                relax = "class+family"
                if dfam.sum() < need:
                    dfam = donors_all & (np.isin(fam, fams) if fams else True)
                    relax = "family"
                if dfam.sum() < need:
                    dfam = donors_all
                    relax = "part"
                pjobs.append((img, himg, cv.image(dfam, False, bool), rp, _seed_of(seed, p, c), (p, c, relax), fill,
                              tbands[p]))
        pres = pool_map(_pm_job, pjobs, workers, progress=prog) if pjobs else []
        sres = pool_map(FL.tile_job, sjobs, workers, progress=prog) if sjobs else []
        filled = work.copy()
        pm_checks = []
        for key, (core, sub, chk) in zip(smeta, sres):
            sm, im = small_out[key]
            y0, y1, x0, x1 = core
            hm = sm[y0:y1, x0:x1]
            im[y0:y1, x0:x1][hm] = sub[hm]
            pm_checks.append({"part": key[0], "class": f"{key[1]}_small_enclosed", "donors": "part photo",
                              "hole_px": chk["hole_px"], "donor_px": chk["donor_px"], "radius_px": rs_px,
                              "outside_changed": chk["outside_changed"], "hole_changed": chk["hole_changed"]})
        for (p, c), (sm, im) in small_out.items():
            cv = canvases[p]
            hs = cv.gather(sm)
            filled[cv.rows[hs]] = cv.gather(im).astype(float)[hs]
        small_info["tiles"] = len(sjobs)
        for jb, r in zip(pjobs, pres):
            p, c, relax = r["key"]
            cv = canvases[p]
            vals = cv.gather(r["out"]).astype(float)
            hs = cv.gather(jb[1])
            filled[cv.rows[hs]] = vals[hs]
            pm_checks.append({"part": p, "class": c, "donors": relax, "hole_px": r["hole_px"], "donor_px": r["donor_px"],
                              "radius_px": r["radius_px"], "outside_changed": r["outside_changed"],
                              "hole_changed": r["hole_changed"]})
        timings["fill_s"] = round(time.perf_counter() - t, 2)
        prog(f"fill: {len(pm_checks)} {fill} calls", force=True)

        # label-seam bands (2 or 4 texels by test), repaired from photo donors of the part, by tiled PatchMatch
        # (320-texel cores with 48-texel donor margins, in the pool: a thin band over a whole part canvas made one call
        # take minutes at 16 texels/mm)
        t = time.perf_counter()
        seam_info = {}
        warnings_early = []
        band_final = np.zeros(n, bool)
        painted_label = np.where(photo, label, -1)
        rp_of = {p: FL.radius_px(radii[p], tx) for p in parts}

        def run_bands(width_of: dict, base: np.ndarray) -> dict:
            jobs, meta = [], []
            out = {}
            for p, width in width_of.items():
                cv = canvases[p]
                pm = np.zeros(n, bool)
                pm[cv.rows] = True
                band = SM.label_band(tex, painted_label, width) & pm
                if not band.any():
                    continue
                img = cv.image(np.clip(np.rint(base), 0, 255).astype(np.uint8), 0, np.uint8)
                himg = cv.image(band, False, bool)
                dimg = cv.image(photo & pm & ~band, False, bool)
                for k, (core, crop) in enumerate(FL.band_tiles(himg)):
                    jobs.append((img, himg, dimg, core, crop, rp_of[p], _seed_of(seed, p, "band", width, k), fill,
                                 tbands[p]))
                    meta.append(p)
                out[p] = {"width": width, "band": band, "img": img.copy(), "himg": himg, "checks": []}
            res_ = pool_map(FL.tile_job, jobs, workers, progress=prog) if jobs else []
            for p, (core, sub, chk) in zip(meta, res_):
                y0, y1, x0, x1 = core
                o = out[p]
                hm = o["himg"][y0:y1, x0:x1]
                o["img"][y0:y1, x0:x1][hm] = sub[hm]
                o["checks"].append(chk)
            for p, o in out.items():
                cv = canvases[p]
                trial = base.copy()
                hs = cv.gather(o["himg"])
                trial[cv.rows[hs]] = cv.gather(o["img"]).astype(float)[hs]
                o["trial"] = trial
                o["test"] = SM.band_tests(tex, trial, painted_label, o["band"], tx, width=o["width"])
            return out

        first = run_bands({p: 2 for p in parts}, filled)
        second = run_bands({p: 4 for p, o in first.items() if not o["test"]["pass"]}, filled)
        for p in parts:
            tries = [x for x in (first.get(p), second.get(p)) if x is not None]
            if not tries:
                continue
            for o in tries:
                seam_info.setdefault(p, []).append({"width": o["width"], "texels": int(o["band"].sum()), **o["test"],
                                                    "tiles": len(o["checks"]),
                                                    "outside_changed": int(sum(c["outside_changed"] for c in o["checks"])),
                                                    "hole_changed": int(sum(c["hole_changed"] for c in o["checks"]))})
                prog(f"seam band {p} width {o['width']}: step {o['test']['step_ratio']}, "
                     f"energy {o['test']['band_energy']}", force=True)
            o = next((x for x in tries if x["test"]["pass"]), None)
            if o is None:
                # correction round: §6.4 keeps a band repair only if it passes (step ratio <= 3, band energy >= 0.85);
                # one that fails at 2 and 4 texels is NOT applied: the hard seam stays and its photo texels stay photo
                # (FT1119: Brow_Left's failing 4-texel band replaced 4.4 % of the -x brow front photo 05 sees)
                last = tries[-1]
                warnings_early.append(f"{p}: label-seam band repair fails its test at widths "
                                      f"{[x['width'] for x in tries]} (step {last['test']['step_ratio']}, energy "
                                      f"{last['test']['band_energy']}); not applied (hard seam kept, §6.4)")
                seam_info.setdefault(p, [])
                seam_info[p].append({"applied": False, "reason": "no width passes §6.4's test"})
                continue
            cv = canvases[p]
            hs = cv.gather(o["himg"])
            filled[cv.rows[hs]] = o["trial"][cv.rows[hs]]
            band_final |= o["band"]
            for c in o["checks"]:
                pm_checks.append({"part": p, "class": f"seam_band_{o['width']}", "donors": "part photo",
                                  "hole_px": c["hole_px"], "radius_px": rp_of[p], "outside_changed": c["outside_changed"],
                                  "hole_changed": c["hole_changed"]})
        timings["seams_s"] = round(time.perf_counter() - t, 2)

        # chart seams (harmonised; A5 is graded off the seam by the instrument)
        t = time.perf_counter()
        protected = photo & ~band_final
        pre_reflect = filled.copy()
        filled, reflect_info = SM.reflect_seams(job, layout, tex, filled, protected)
        prog(f"chart-seam reflection: {reflect_info}", force=True)
        w_paint = np.zeros(n)
        for i, v in enumerate(vlist):
            m_ = label == i
            w_paint[m_] = samples[v].w[m_]
        is_anchor = photo & (label == anchor_label)
        rank = np.where(protected, 1.0 + is_anchor + 0.5 * w_paint / max(float(w_paint.max()), 1e-12), 0.0)
        pre_harmonise = filled.copy()
        harm, unprot, harm_info = SM.harmonise(tex, filled, protected, pairs, rank=rank,
                                               source=np.where(protected & (label >= 0), label, -1))
        protected &= ~unprot

        # classes, sources, tiers
        klass = np.zeros(n, np.uint8)
        klass[photo] = np.where(np.isin(label[photo], [LB.MIX]), CLASS_CODES["photo_tier1"], CLASS_CODES["photo_anchor"])
        klass[mirrored & ~mirror_ring] = CLASS_CODES["mirror"]
        klass[hole] = CLASS_CODES["patchmatch"]
        klass[holes_hl & ~photo] = CLASS_CODES["highlight_fill"]
        klass[text_fill] = CLASS_CODES["text_fill"]
        klass[band_final] = CLASS_CODES["seam_band"]
        klass[unprot] = CLASS_CODES["chart_seam"]
        source = np.where(photo & (label >= 0), label + 1, 0).astype(np.uint8)
        tier = np.where(photo, np.where(label == LB.MIX, 1, 2), 3).astype(np.uint8)

        # shares, AR visibility, the < 30 % cover rule (§9; fix round 2: what the painting views SEE, over the area a
        # worn frame shows, mirror partners together), warnings
        arv_face = ar_visible_faces(job, layout.faces, occ)
        arv = arv_face[tf]
        grp_side = {}
        for g_ in groups:
            for q_ in g_.parts:
                grp_side[q_] = g_.side
        regfacing = np.array([by[int(r)].facing if r >= 0 else "none" for r in range(max(by) + 1)] + ["none"],
                             dtype=object)
        facing_t = regfacing[np.where(tex.region >= 0, tex.region, -1)]
        is_temple_t = np.array([grp_side.get(int(q_), 0) != 0 for q_ in range(len(names))])[tex.part]
        worn_hidden = is_temple_t & np.isin(facing_t, list(WORN_HIDDEN["temple"]))
        source_seen = painted & (label >= 0)               # seen by the painting view, before text / highlight masks
        part_rows = {}
        warnings = list(warnings_early) + GN.support_warnings(chain, grp, lev)
        for p in parts:
            pid = [i for i, nm in enumerate(names) if nm == p]
            sel = np.isin(tex.part, pid)
            a = sel & arv
            na = max(int(a.sum()), 1)
            aw = a & ~worn_hidden
            nw = max(int(aw.sum()), 1)
            pr = {"texels": int(sel.sum()), "ar_visible_texels": int(a.sum()),
                  "ar_visible_worn_texels": int(aw.sum()),
                  "worn_hidden_share_of_ar": float((a & worn_hidden).sum() / na),
                  "source_seen_share": float((source_seen & aw).sum() / nw),
                  "source_seen_share_ar_all": float((source_seen & a).sum() / na),
                  "photo_share_ar": float((photo & a).sum() / na),
                  "anchor_share_ar": float((is_anchor & a).sum() / na),
                  "mirror_share_ar": float((mirrored & a).sum() / na),
                  "patchmatch_share_ar": float(((klass == CLASS_CODES["patchmatch"]) & a).sum() / na),
                  "text_fill_share_ar": float((text_fill & a).sum() / na),
                  "highlight_share_ar": float((holes_hl & a).sum() / na),
                  "seam_band_share": float((band_final & sel).sum() / max(int((photo & sel).sum()), 1)),
                  "radius_mm": radii.get(p), "_seen": int((source_seen & aw).sum()), "_den": int(aw.sum())}
            part_rows[p] = pr
            if pr["patchmatch_share_ar"] > PATCHMATCH_WARN:
                warnings.append(f"{p}: PatchMatch fills {pr['patchmatch_share_ar']:.0%} of its AR-visible area (> 25 %; "
                                f"filler: {fill})")
            if pr["seam_band_share"] > SM.SEAM_SHARE_MAX:
                warnings.append(f"{p}: seam bands are {pr['seam_band_share']:.0%} of its projected texels (> 15 %)")
        partners_by_name = {names[q_]: names[r_] for q_, r_ in FL.mirror_partners(job, pids).items()}
        cover, no_texture = cover_rule({p: (part_rows[p]["_seen"], part_rows[p]["_den"]) for p in parts},
                                       partners_by_name, min_cover)
        for p in parts:
            part_rows[p]["cover_rule"] = cover[p]
        for pr in part_rows.values():
            pr.pop("_seen")
            pr.pop("_den")
        for p in no_texture:
            warnings.append(f"{p}: its painting photos see {cover[p]['pooled_source_seen_share']:.0%} of the AR-visible "
                            f"worn area of {' + '.join(cover[p]['members'])} (< {min_cover:.0%}): "
                            + ("textured anyway (Astra override)" if override_min_cover
                               else "NO TEXTURE (flat grey, left out of texture_parts; Astra may override)"))
        graze_share = mirror_info.get("mirrored_on_grazing_share", 0.0)
        if mirror_info.get("texels") and graze_share > MIRROR_GRAZE_WARN:
            blank_names = [] if override_min_cover else no_texture
            shipped = int((mirrored & ~np.isin(tex.part, [i for i, nm in enumerate(names) if nm in blank_names])).sum())
            warnings.append(f"mirror fill: {graze_share:.0%} of the {mirror_info['texels']} mirrored texels lie on "
                            f"surfaces a photo shows obliquely (cos 0.1-0.5, inside the matte; per view "
                            f"{mirror_info['mirrored_grazing_by_view']}); {shipped} mirrored texels ship on textured "
                            f"parts")
        blobs = {v: s.foreign for v, s in samples.items() if s.foreign}
        if blobs:
            warnings.append("foreign blobs: bright non-acetate blobs ON print in the photos (gold = metal the model "
                            "lacks, e.g. a medallion; achromatic = glare or silver), each a hole for that view; note for "
                            "Astra: " + "; ".join(
                                f"{IN_short(v)}: " + ", ".join(f"{b_.get('part', '?')} {b_['kind'].split(' ')[0]} "
                                                              f"{b_['area_mm2']} mm2 at px {b_['centre_px']}"
                                                              for b_ in bl)
                                for v, bl in blobs.items()))
        if tm.get("see_through_hits"):
            warnings.append(f"see-through text: {tm['see_through_px']} texels of photo lettering seen through the acetate "
                            f"(per view {tm['see_through_by_view']}; {tm['see_through_hits_on_mirrored_decal']} cast "
                            f"hits on the MIRRORED decal, i.e. print the model has no decal for) masked and filled; "
                            f"note for Astra: the product carries print the model lacks")
        blank = np.zeros(n, bool)
        if not override_min_cover:
            for p in no_texture:
                blank |= np.isin(tex.part, [i for i, nm in enumerate(names) if nm == p])
        texture_parts = [p for p in parts if not (p in no_texture and not override_min_cover)]
        harm = harm.copy()
        harm[blank] = NO_TEXTURE_RGB
        protected &= ~blank
        klass[blank] = CLASS_CODES["no_texture"]
        source[blank] = 0
        tier[blank] = 0

        # compose (A3), padding
        P0 = np.clip(np.rint(np.where(photo[:, None], col, 0.0)), 0, 255).astype(np.uint8)
        P0_img = tex.image(P0, 0, np.uint8)
        prot_img = tex.image(protected, False, bool)
        final_img, comp = compose(P0_img, prot_img, tex.image(np.clip(np.rint(harm), 0, 255).astype(np.uint8), 0,
                                                                np.uint8))
        used = tex.index >= 0
        # correction round: the AR level (calibrated to the photos), the LAST colour step, on every textured texel
        final_radiance = final_img
        if level:
            final_img = SH.apply_level_u8(final_radiance, used & ~tex.image(blank, False, bool), level)
        out_img = SM.pad_gutters(final_img, used)
        atlas_radiance = SM.pad_gutters(final_radiance, used) if level else out_img
        timings["compose_s"] = round(time.perf_counter() - t, 2)

        # single-source audit: who paints each region's photo texels (fix round 2: the region's anchor, or the anchor
        # of an ADJACENT region after the crease / photometric boundary moves; any other view is a defect)
        g_ = pl["graph"]
        flut = lut[layout.faces]
        adj_regions = {r.rid: set() for r in regions}
        ea, eb = g_["a"], g_["b"]
        ra_, rb_ = flut[ea], flut[eb]
        okr = (ra_ >= 0) & (rb_ >= 0) & (ra_ != rb_)
        for x_, y_ in set(zip(ra_[okr].tolist(), rb_[okr].tolist())):
            adj_regions[x_].add(y_)
            adj_regions[y_].add(x_)
        region_audit = {}
        foreign_total = 0
        for r in regions:
            sel = (tex.region == r.rid) & photo
            npx = int(sel.sum())
            own = region_label.get(r.rid)
            allowed_views = {own} | {region_label.get(q_) for q_ in adj_regions[r.rid]}
            allowed_views = {x_ for x_ in allowed_views if x_ is not None and x_ >= 0}
            pb = {vlist[i]: int((sel & (label == i)).sum()) for i in range(len(vlist)) if (sel & (label == i)).any()}
            foreign_px = int(sum(c_ for v_, c_ in pb.items() if vindex[v_] not in allowed_views))
            foreign_total += foreign_px
            region_audit[r.name] = {"photo_texels": npx,
                                    "non_anchor_share": float((sel & ~is_anchor).sum() / npx) if npx else 0.0,
                                    "painted_by": pb,
                                    "allowed_views": sorted(vlist[x_] for x_ in allowed_views),
                                    "foreign_view_texels": foreign_px}
        non_anchor = int((photo & ~is_anchor).sum())
        statuses = {}
        for r in regions:
            an = anchors[r.rid]
            st = ("registered" if tier_of_region[r.rid] == 1 else "unseen" if an["anchor"] is None
                  else ("anchor-only" if an["others"] else "single-view"))
            if r.minor and an["anchor"] is None:
                st = "minor"
            if r.name in step0_status and tier_of_region[r.rid] != 1:
                st = step0_status[r.name] if step0_status[r.name] != "registered" else st
            statuses[r.name] = {"status": st, "anchor": an["anchor"], "others": an["others"], "texels": an["texels"],
                                "tier": tier_of_region[r.rid], "area_mm2": round(r.area_mm2, 2),
                                **region_audit[r.name]}
        total_area = sum(r.area_mm2 for r in regions)
        status_area = {}
        for r in regions:
            s = statuses[r.name]["status"]
            status_area[s] = status_area.get(s, 0.0) + r.area_mm2
        checks = {"A1": layout.report, "A3": {**comp,
                                              "patchmatch_outside_changed_total": int(sum(c["outside_changed"] for c in pm_checks)),
                                              "patchmatch_every_hole_changed": bool(all(c["hole_changed"] > 0 for c in pm_checks
                                                                                        if c["hole_px"] > 0)),
                                              "calls": pm_checks},
                  "seams": {"bands": seam_info, "chart_seam_pairs": int(len(pairs)), "harmonise": harm_info},
                  "labels": {"label_moves": label_moves, "non_anchor_photo_texels": non_anchor,
                             "non_anchor_photo_share": float(non_anchor / max(int(photo.sum()), 1)),
                             "foreign_view_photo_texels": int(foreign_total),
                             "boundary_per_part": {names[int(k_)]: v_ for k_, v_ in pl["boundary_per_part"].items()},
                             **{k: pl[k] for k in ("boundary_start", "boundary", "moved_faces", "candidates", "energy",
                                                "band_faces", "band_faces_start_label_unseen",
                                                "moved_faces_start_label_unseen", "moved_texels",
                                                "moved_texels_start_label_unseen", "moved_to_unseen_faces")}},
                  "knockout": knock}
        timings["total_s"] = round(time.perf_counter() - t0, 2)
        mode = {"crease": "single-source, one view per face: each region painted by its anchor, its boundary re-placed "
                          "on a crease or the photometric seam (<= 3 mm, only onto an ADJACENT region's anchor that sees "
                          "the face; non-anchor share per region in statuses); registered regions tier 1 (none on "
                          "FT1119)",
                "off": "single-source, strict anchor-only: every photo texel of a region is painted by that region's "
                       "anchor view (non-anchor share per region in statuses); registered regions tier 1 (none on "
                       "FT1119)",
                "mrf": "single-source with the round-1 crease-MRF boundary moves (DIAGNOSTIC): photo texels near region "
                       "boundaries may be painted by a non-anchor view (non-anchor share per region in statuses)"
                }[label_moves]
        report = {"mode": mode,
                  "parts": parts, "views": vlist, "thresholds": {"print_reg_mm": print_reg_mm, "edge_mean_mm": edge_mean_mm,
                                                                  "edge_p90_mm": edge_p90_mm},
                  "layout": {"width": layout.width, "height": layout.height, "tx_per_mm": tx,
                             "partners": layout.partners},
                  "statuses": statuses,
                  "status_area": {k: {"mm2": round(v, 1), "share": round(v / total_area, 4)} for k, v in
                                  sorted(status_area.items())},
                  "parts_summary": part_rows,
                  "area_table": area_table(statuses, regions, tex, klass, part_rows),
                  "no_texture_parts": no_texture if not override_min_cover else [],
                  "texture_parts": texture_parts, "below_min_cover": no_texture,
                  "override_min_cover": bool(override_min_cover),
                  "cover_rule": {"measure": "texels seen by their painting view (before the text / highlight masks) "
                                            "over the AR-visible texels a worn frame shows (temple inner and bottom "
                                            "faces excluded), pooled over mirror partners",
                                 "limit": min_cover, "worn_hidden": WORN_HIDDEN,
                                 "pooled": cover, "decision": "fix round 2 (engineering; the reviewer's measure)"},
                  "foreign_blobs": {v: s.foreign for v, s in samples.items()},
                  "view_reasons": {v: s.reasons for v, s in samples.items()},
                  "gains": {"reference": gref, "edges": edges_g, "per_view": chain,
                            "across_boundaries": {"groups": grp["groups"], "edges": grp["edges"],
                                                  "mirrors": grp["mirrors"], "rejected": grp["rejected"],
                                                  "dropped": grp["dropped"]},
                            "level_field": {k: v for k, v in lev.items() if k != "gain"} | {
                                "file": "gain_field.npz (per-texel stage-3 gain, float16 image)"},
                            "mirror_regions": {by[a].name: by[b].name for a, b in sorted(mirror_region.items())},
                            "clip_evidence": GN.clip_evidence(edges_g),
                            "model": "linear-light median ratio, weighted least squares of log gains (see gains.py); "
                                     "step 4: stage-1 clip [0.33, 3], same-part boundaries, mirror edges; stage-2 "
                                     "same-part edges, mirror equations, robust pass, clip [0.5, 2]; stage-3 smooth "
                                     "levelling field"},
                  "fill": {"method": fill, "radii_mm": radii,
                           "by_method": {m_: {"radii_mm": radii_by[m_],
                                              "knockout": {p: _knock_summary(kr) for p, kr in knock_by[m_].items()}}
                                         for m_ in methods},
                           "small_enclosed": small_info,
                           "choice": FILL_CHOICE},
                  "chart_seam_reflection": reflect_info,
                  "metal": {v: {"arm": s_.arm, "reasons": {k: s_.reasons.get(k) for k in
                                                         ("metal_or_highlight", "pale", "hardware_margin", "edge_halo",
                                                          "thin_line", "dark_blob", "enclosed")}}
                            for v, s_ in samples.items()},
                  "highlights": {k: v for k, v in hl.items() if k != "mask"} | {"shen_zheng": "off (no registered "
                                                                                 "overlaps to pass its keep test)"},
                  "flatten": flat_report,
                  "level": {"applied": bool(level), "gain": (float(level["gain"]) if level else None),
                            "gamma": (float(level["gamma"]) if level else None), "source": level_source,
                            "map": "rgb_lin x gain x Y^(gamma - 1) on every textured texel after compose (shading.level_map)",
                            "note": ("AR level calibrated to the photos" if level else
                                     "AR level NOT calibrated: print.png is photo radiance after the gains")},
                  "strip_donors": bool(strip_donors),
                  "text": {k: v for k, v in tm.items() if k not in ("mask", "distance", "see_through_mask")} | {
                      "filled": bool(text_fill.any()), "keep_photo_text": keep_photo_text},
                  "mirror": mirror_info, "checks": checks, "warnings": warnings,
                  "classes": CLASS_CODES, "shares": {k: int((klass == v).sum()) for k, v in CLASS_CODES.items()},
                  "timings": timings, "versions": {"numpy": np.__version__, "scipy": scipy.__version__,
                                                   "python": platform.python_version()},
                  "inputs": {"sources": job.sources, "mesh_sha256": sha256_array(job.mesh.V), "step0_report": step0_info},
                  "print_uv": MF.print_identity(job.mesh.V, job.mesh.F, job.mesh.part, job.mesh.names, layout)}
        registration = {"mode": mode, "label_moves": label_moves, "texture_parts": texture_parts, "anchors": {anchors[r.rid]["region"]: anchors[r.rid] for r in regions},
                        "admitted": {f"{v}|{p}": a for (v, p), a in sorted(adm.items())},
                        "edges": edges, "registered_used": {k: v for k, v in registered.items() if len(v) >= 2},
                        "statuses": statuses}
        res = {"atlas": out_img, "final_unpadded": final_img, "P0": P0_img, "protected": prot_img, "layout": layout,
               "atlas_radiance": atlas_radiance, "final_radiance": final_radiance, "level": level, "flatten": flat_report,
               "tex": tex, "label": label, "photo": photo, "classes": klass, "source": source, "tier": tier,
               "samples": samples, "views": vlist, "regions": regions, "anchors": anchors, "report": report,
               "registration": registration, "pairs": pairs, "colours": harm, "radii": radii, "occ": occ,
               "radii_by_method": radii_by,
               "mirrored": mirrored, "seen_any": seen_any, "seen_geom": seen_geom, "hole": hole, "band": band_final,
               "text_mask": tm["mask"], "see_through": tm.get("see_through_mask", np.zeros(n, bool)), "ar_visible": arv,
               "colours_pre_harmonise": pre_harmonise, "colours_pre_reflect": pre_reflect, "is_anchor": is_anchor,
               "texture_parts": texture_parts, "level_gain": lev["gain"],
               "no_texture": blank, "unprotected": unprot}
    if write:
        res["files"] = write_outputs(res, out_dir)
    prog(f"done in {timings['total_s']} s", force=True)
    return res


def srgb_pnginfo():
    """A PNG sRGB chunk (rendering intent 0, perceptual): print.png holds sRGB-encoded colours (the photos' own), which
    is what glTF expects of a baseColorTexture; the chunk states it for any other reader."""
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add(b"sRGB", b"\x00")
    return info


def write_outputs(res: dict, out_dir) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tex = res["tex"]
    files = {}
    Image.fromarray(res["atlas"]).save(out / "print.png", optimize=False, pnginfo=srgb_pnginfo())
    files["print"] = str(out / "print.png")
    Image.fromarray(tex.image(res["classes"], 0, np.uint8)).save(out / "classes.png")
    Image.fromarray(tex.image(res["source"], 0, np.uint8)).save(out / "source.png")
    Image.fromarray(tex.image(res["tier"], 0, np.uint8)).save(out / "tier.png")
    files.update({k: str(out / f"{k}.png") for k in ("classes", "source", "tier")})
    rep = dict(res["report"])
    timings = rep.pop("timings", None)
    files["layout"] = write_json(out / "layout.json", res["layout"].to_json())
    files["registration"] = write_json(out / "registration.json", res["registration"])
    files["report"] = write_json(out / "report.json", {**rep, "timings": timings})
    files["report_no_timings"] = write_json(out / "report_stable.json", rep)
    if "level_gain" in res:
        np.savez_compressed(out / "gain_field.npz", gain=tex.image(res["level_gain"].astype(np.float16), 1.0,
                                                                     np.float16))
        files["gain_field"] = str(out / "gain_field.npz")
    try:                                     # what g3d.attach_print reads (manifest.py); a job without a GLB has none
        files["manifest"] = str(MF.write_manifest(out))
    except (KeyError, OSError, ValueError) as exc:
        files["manifest_error"] = f"{type(exc).__name__}: {exc}"
    return files
