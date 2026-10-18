"""Generic facet-lab preparation and execution. Preparation requires explicit source data.

The assembled job uses package.prepare on its current checkpoint. The standalone prepare command accepts
--source-config JSON with source_scene, scene_sha256, reference and basis_json plus optional photo/job facts.
Prepare, estimate and request are offline; paid execution retains the exact approval guard.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import json
import math
from pathlib import Path
import shutil
import sys

from PIL import Image, ImageOps

from glasses3d.agent import approvals as A
from glasses3d.agent.budget import PAYLOAD_LIMIT_BYTES, PAYLOAD_LIMIT_IMAGES, count_timeout_s
from glasses3d.agent.estimate import cents_text, suggested_cap_micro
from glasses3d.agent.loop import (CONTEXT_GUARD, CONTEXT_GUARD_BYTES, EFFORTS, REMOVED_PHRASES, RunRefused, RunSpec,
                                  ToolContract, contract_problems, instructions_sha256, machine_load, run_astra,
                                  tool_registry_sha256)
from glasses3d.agent.pricing import LONG_CONTEXT_INPUT_TOKENS, MODEL_MAX_INPUT_TOKENS, Tariff, usd_text
from glasses3d.agent.seeing import CATALOG_RIG, ENV_RIG, MATCHED_RIG, MATCHED_RIGS, SIDE_VIEWS, ZOOM_MAX
from glasses3d.agent.session_inputs import ReferenceInput
from glasses3d.g3d_host import compare as C
from . import lab as LAB
from . import readings as RD
from . import sheets as SH
from .lab import (LAB_SCHEMA, VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX, VARIANTS_MAX, VARIANTS_MIN, Lab, data_url, read,
                  sha, write_json_atomic)
from .readings import THETA_SD_DEG, TRANSPORT_RE
from .state import READ_POINTS_MAX
from .tools import CONTEXT_GUARD_TOKENS, TOOL_NAMES, registry, run_settings

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
G3D = REPO / "glasses3d"
LAB_DATA = G3D / "data/temple_lab"
ESTIMATE = LAB_DATA / "estimate_v3.json"
DEFAULT_RIG = MATCHED_RIG
K_TOKEN = "{K}"
# v2's removed tools and rules (WP6_V3 §1 P5-P6 and §6 "removed from Astra's view": fades, crease+rise planes, the
# solve option that "moves least", the refusal recipe "do not redeclare", photo_compare's one-photo depths parameter,
# the top / rear outer-edge offset tables, the "start" column and the outline line, W_CONDITIONING, lean_deg, the
# section strip, crease margins, facet_restore). make_spec's tool contract refuses a package whose instructions, brief
# or tool and parameter descriptions name any of them (case-insensitive substrings, as loop.contract_problems checks
# loop.REMOVED_PHRASES); tests/agent/test_temple_lab_cli.py pins the instructions. The depths parameter is banned by
# its forms (v2's prompt and brief: "photo_compare's depths", "depths reads t", "measured with depths", `depths`), not
# by the plain word: "two vertices at different depths" is English, not the tool (review 2026-10-12, P3).
RETIRED_PHRASES = ("facet_restore", "fade", "rise_per_mm", "crease plane", "moves least", "do not redeclare",
                   "photo_compare's depths", "depths read", "with depths", "`depths`", "outer-edge offset",
                   "outline line", "offset table", "start column", "\"start\" column", "margins", "w_conditioning",
                   "lean_deg", "section strip")

# The estimate (WP6_FACETS §4.3): tokens ~ 0.42 x text chars + per image 0.975 x ceil(W/32) x ceil(H/32) + 112, fitted
# on the 34 transport deltas of the v1 lab run owner-test-001 (median absolute residual 197 tokens). On that run's
# first request (19,726 text chars, 10 images) the formula gives 19,245 tokens against 15,121 counted: the brief part
# is over-estimated by 27 %, which is kept (conservative).
TOKENS_PER_CHAR, TOKENS_PER_PATCH, TOKENS_PER_IMAGE, PATCH_PX = 0.42, 0.975, 112, 32
V1_FIRST_REQUEST = {"formula_tokens": 19245, "counted_tokens": 15121}
# Output tokens per request (reasoning included), measured on the paid gpt-6-astra facet runs, all at effort max (each
# run's runs/<id>/budget.json rows, usage.output_tokens): facet-astra-002 33 requests, mean 1635; facet-astra-003 15
# requests, mean 2064; facet-astra-004 32 requests, mean 1852 (pooled 80: 1803; the largest single reply 8134).
# Expected: the latest run's mean (run 4, the current tools); high: the highest run mean (run 3). They replace
# (2026-10-11 build review, P2) the v1 lab run's 580 and the Studio corpus median 851, which priced every facet run
# about 30 % low: run 4's table (estimate_004.json) priced 40 replies at $7.47, and run 4 settled $8.21 in 32. No
# gpt-6-astra ledger at effort high or xhigh exists: both use the measured max numbers, without an assumed discount.
OUTPUT_TOKENS = {"expected": 1852, "high": 2064}
OUTPUT_TOKENS_SOURCE = ("mean output tokens per request of the paid facet runs at effort max (budget.json): "
                        "facet-astra-002 1635 (33 requests), -003 2064 (15), -004 1852 (32); expected = run 4, "
                        "high = the highest run mean (run 3)")
# Tool calls per 10 requests. typical: the method the v3 instructions teach (look, write the object down and lay out,
# read the vertices from two directions, build and read the fit, compare zoomed at native scale, bracket): edits and
# their compares, a photo_read now and then (up to READ_POINTS_MAX points a call: a temple's 8-12 vertices take one or
# two, then re-reads), zoomed compares, a variants bracket, a closer look and an inspect; edit_heavy: every request an
# applied facet_edit with its default compare (v2's range top); read_heavy: the typical mix with photo_read 3 in 10 in
# place of two edits (a run that re-reads much). No v3 run exists, so every v3 weight is an assumption (review
# 2026-10-12, P2: photo_read at 2 or 3 in 10 priced a 90-reply run $40.12-$45.77 against the typical $34.10): the
# range's top is the costliest of RANGE_MIXES at the high output (range_top) and the approval text says the mix is
# assumed (MIX_ASSUMED). v2's typical mix had facet_edit 5 and no photo_read.
MIXES = {"typical": {"facet_edit": 4, "photo_compare": 2, "photo_read": 1, "facet_variants": 1, "photo_image": 1,
                     "facet_inspect": 1},
         "edit_heavy": {"facet_edit": 10},
         "read_heavy": {"facet_edit": 2, "photo_compare": 2, "photo_read": 3, "facet_variants": 1, "photo_image": 1,
                        "facet_inspect": 1}}
RANGE_MIXES = ("edit_heavy", "typical", "read_heavy")
# A recorded image's kind (the rehearsal's tool_outputs [W, H, kind]) is record_kind of the lab's image index entry
# (Lab._image: {kind, ref, ...}): the lab's own kind, except a zoom grid, which the lab registers as kind "compare"
# with zoom=True (lab.py) and the rehearsal records as ZOOM_GRID_KIND. So the estimate tells a photo_compare call's own
# zoom grids from its compare panels and prices the call at its compare panels plus ZOOM_MAX grids (v3p3-t5: with both
# recorded "compare", a call's own grids stayed and ZOOM_MAX more were added, 5 grids for a 2-zoom call).
ZOOM_GRID_KIND = "zoom"
# The record kinds the context guard replaces in an old result (lab.context_stubs: compare panels, zoom grids and
# variant sheets of a result older than the latest 2 and not best_checkpoint's; the v3 T5 rehearsal states and checks
# that decision, tests/proofs/wp6v3_rehearsal_checks.py): lab's own STUB_KINDS (review 2026-10-12, P3: one constant
# for the guard and the estimate), else the kinds context_stubs names today, plus ZOOM_GRID_KIND while the lab
# registers zoom grids as compare images (they go with their compare). A test pins it against a real Lab's
# context_stubs and a real zoom grid. A pick sheet is replaced only once its reading was read again or dropped (the
# stored reading's sheet is evidence and stays); the projection counts none as stubbable, which prices a run that
# re-reads a little high, never low.
LAB_STUB_KINDS = tuple(getattr(LAB, "STUB_KINDS", ("compare", "variants")))
STUBBED_KINDS = LAB_STUB_KINDS + ((ZOOM_GRID_KIND,) if "compare" in LAB_STUB_KINDS
                                  and ZOOM_GRID_KIND not in LAB_STUB_KINDS else ())
STUB_CHARS = 120                                 # one stub text
# The v3 image-heavy outputs, each priced at the most one call returns (as facet_variants at variant_sheets), at the
# largest image the tool makes, not the rehearsal's (review 2026-10-12, P2: a rehearsal of 2-photo readings or small
# zoom boxes priced well below the most one call returns):
# - photo_read: a pick sheet per reading (readings.pick_sheet: 972 px wide, TITLE_PX 40 + 342 px per photo of the
#   reading + 4: 728 px at 2 photos, 1412 at PHOTOS_MAX 4; read_sheet_px, ~1,470 tokens), up to state.READ_POINTS_MAX
#   8 a call, Lab._image kind READING_KINDS;
# - photo_compare: one 2 x 2 zoom grid per zoom item, up to seeing.ZOOM_MAX 3 a call (each panel up to
#   compare.ZOOM_PANEL_PX 760 x 500 under a 24 px label, 6 px gaps: 1538 x 1066 px, zoom_grid_px, ~1,740 tokens),
#   recorded as ZOOM_GRID_KIND (record_kind); a rehearsal recorded before record_kind holds its grids as "compare"
#   images, which stay as recorded beside the ZOOM_MAX priced (up to ZOOM_MAX grids high); each zoom adds
#   ZOOM_TEXT_CHARS of text (seeing _zoom_text: 767-785 characters on probe 1's three real zooms with five labelled
#   edges, v3/probe1/pkg/calls/0001-compare_to_photos/result.json).
# - facet_variants: one sheet per photo (sheets.py: every model on one page), up to variant_sheets a call, each priced
#   at the largest page, sheets.SHEET_W x SHEET_H (1280 x 900, ~1,243 tokens; the T5 v3 rehearsal's 2-arm-photo pages
#   were 1060-1066 x 898, ~1,073), and its text and arguments scaled to VARIANTS_MAX arms (each arm prints its fit,
#   crease and diff lines and takes its edits as arguments): by VARIANTS_MAX / the call's own arms when the
#   rehearsal records them ("arms"), else by VARIANTS_MAX / VARIANTS_MIN (high, never low). Review 2026-10-12, P3:
#   priced at the rehearsal's sheet, 5,052 tokens a call against the tool's ~6.8k.
# - facet_inspect at its largest recorded call (LARGEST_PRICED): its text grows with the stored readings and the
#   design (T5 v3: revision 0 with no reading 1,657 characters, 5 stored readings 3,732; session2's heads with 8
#   readings took 2,908-3,167 tokens against the 1,682 a call the mean of both priced; review 2026-10-12, P3).
READING_KINDS = ("reading",)
ZOOM_TEXT_CHARS = 800
LARGEST_PRICED = ("facet_inspect",)
OK_STATUSES = ("ok", "applied", "accepted")     # a recorded call that counts in a tool's mean (mix_step)
GUARD_KEEP_RESULTS = 3                           # the latest 2 compare / variant results and best_checkpoint's
RUN_LENGTHS = (40, 60, 90)
ESTIMATE_SCHEMA = "g3d.facet_estimate/1"
# The request bytes model (review 2026-10-12, P2; v3p3-verify-15): images are 96.5-97.9 % of a paid run's body. Each
# image is priced at its kind's LARGEST measured base64 bytes per formula token, on 121 real v3 tool images (the T5 v3
# rehearsal and session2; v3/probes3/verify-15/byte_probe.log B: chart 352.2, compare 411.3, photo 213.7, reading
# 229.2, variants 413.8, zoom 323.7, rounded up; their means are 154-348); an unmeasured kind (a first request's
# photos, unless the package's own bytes are given) at the largest of them. Text at TEXT_BYTES_PER_CHAR (JSON
# escapes), and every request adds REQUEST_OVERHEAD_BYTES: the paid v2 runs' whole non-image growth a request (16.4-23.7
# KB, mean 20.5: reasoning items, call items and the tool texts, which are counted again beside the texts: high).
# The count's time is the paid runs' fit, COUNT_S_BASE + COUNT_S_PER_MB per MB (156 requests, residual sd 0.66 s, one
# network, fitted up to 30.8 MB), beside budget.count_timeout_s.
BYTES_PER_IMAGE_TOKEN = {"chart": 353, "compare": 412, "photo": 214, "reading": 230, "variants": 414, "zoom": 324}
BYTES_PER_IMAGE_TOKEN_MAX = max(BYTES_PER_IMAGE_TOKEN.values())
TEXT_BYTES_PER_CHAR = 1.1
REQUEST_OVERHEAD_BYTES = 24_000
COUNT_S_BASE, COUNT_S_PER_MB = 1.27, 0.316


# --------------------------------------------------------------------------------------------- package
def implementation():
    files = sorted(HERE.glob("*.py")) + [HERE / "instructions.txt", REPO / "glasses3d/g3d/worker.py"]
    # Bind all construction/worker/seeing code too; an approved request must not silently change implementation.
    for folder in (REPO / "glasses3d/g3d", REPO / "glasses3d/g3d_host", REPO / "glasses3d/agent"):
        files.extend(folder.rglob("*.py"))
    return {str(p.relative_to(REPO)): sha(p) for p in sorted(set(files))}


# --------------------------------------------------------------------------------------------- brief
# The brief carries the job's facts; instructions.txt is general (WP6_V3 §6; WP6_FACETS.md §4.4, amended 2026-10-11).
# The photo facts come from the job's pose basis (cameras.json records) and are information, never a rule. Directions
# are temple-relative: one spec drives both temples as mirror images, so the -X temple seen from (yaw, pitch) is the
# charted (+X) temple seen from (-yaw, pitch). v3 (WP6_V3 P5) prints no line about photo_compare's one-photo depths,
# the top / rear outer-edge offsets or the outline line (removed from the tools), states each camera's lineage as the
# basis records it, how precisely photo_read reads which kind of point, the job's camera-model conflicts, how the
# render light relates to the photos' tones, and the scope.
SAME_DIRECTION_DEG = 10.0       # cameras whose temple-relative directions are closer than this are one direction
SQUARE_ON_DEG = 30.0            # under this angle to the outer face's normal a view is square-on: it shows where edges
#                                 run in (d, y) at cos 30 deg = 0.87 of their size or more
EDGE_ON_DEG = 75.0              # from this angle on the face shows at most cos 75 deg = 0.26 of its width: edge-on
ALONG_DEG = 25.0                # a camera within this of the temple's length (Z) looks along front-to-back creases
SIDE_FACE_PITCH_DEG = 1.0       # a side view more than this above (below) the temple sees its top (bottom) face, so
#                                 its compares measure only the bottom (top) outline: seeing._side_outline measures an
#                                 edge where its face shows < SIDE_FACE_PX 1 px, and a ~7 mm thick temple at ~8 px/mm
#                                 shows 1 px of face at tan(e) = 1/56, e ~ 1 deg (01 / 05 / B1 look 13.6-13.7 deg down)
THETA_AT_D_MM = (25, 50, 80)    # the brief's theta lever, X moved per degree of hinge opening at these d: a rotation
#                                 about the hinge axis moves X by d x pi / 180
#                                 per degree (1.3-1.5 mm at d 75-85: FINDINGS §0.3)
# The job's render light, which the brief names (instructions.txt: "the render light"); g3d/render.py describes
# each rig.
RIG_TEXT = {MATCHED_RIG: "a studio light, one large soft key 55 deg above the horizon on the camera's side, a bright "
                         "sky from just above the horizon, a dark band just below it and a mid-grey ground",
            ENV_RIG: "a light fitted once to this job's photos on the front of the frame (the temples held out)",
            CATALOG_RIG: "a catalogue light, a bright band at the horizon over a dark ground and under a dimmer sky, "
                         "the same all round"}
# Lighting uncertainty is general; a particular job's lighting experiments are not evidence for another job.
LIGHT_TEXT = ("The render light may not reproduce the photos' tones; in the photos a tone step marks where an edge "
              "is, not its size or sign.")


def toward(cam: dict) -> tuple:
    """The unit vector from the model toward a camera (g3d_host/camera.py: (cos p sin y, sin p, cos p cos y); +X is
    the charted temple's side, +Y up, +Z the front)."""
    yaw, pitch = math.radians(float(cam.get("yaw") or 0.0)), math.radians(float(cam.get("pitch") or 0.0))
    return math.cos(pitch) * math.sin(yaw), math.sin(pitch), math.cos(pitch) * math.cos(yaw)


def outer_face_deg(cam: dict) -> float:
    """The angle between a camera's view and the normal (+-X) of the outer face of the temple it sees best: 0 is
    square-on, 90 edge-on."""
    return math.degrees(math.acos(min(1.0, abs(toward(cam)[0]))))


def looks_along_deg(cam: dict) -> float:
    """The angle between a camera's view and the temple's length (+-Z): 0 looks straight along it."""
    return math.degrees(math.acos(min(1.0, abs(toward(cam)[2]))))


def direction_deg(a: dict, b: dict) -> float:
    """The angle between two cameras' temple-relative directions (each mirrored onto the charted temple's side)."""
    (xa, ya, za), (xb, yb, zb) = toward(a), toward(b)
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(xa) * abs(xb) + ya * yb + za * zb))))


def direction_groups(cams: dict) -> list:
    """{photo id: camera} grouped by direction: two photos within SAME_DIRECTION_DEG are linked (single link);
    groups and members in photo order."""
    ids = list(cams)
    root = {p: p for p in ids}

    def find(p):
        while root[p] != p:
            p = root[p]
        return p
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if direction_deg(cams[a], cams[b]) < SAME_DIRECTION_DEG:
                root[find(b)] = find(a)
    groups: dict = {}
    for p in ids:
        groups.setdefault(find(p), []).append(p)
    return list(groups.values())


def _deg(v) -> str:
    return f"{round(float(v or 0.0), 1) + 0.0:+.1f}"          # never "-0.0"


def angle_band(deg: float) -> str:
    return "square-on" if deg < SQUARE_ON_DEG else "at a moderate angle" if deg < EDGE_ON_DEG else "edge-on"


def temple_seen(cam: dict) -> str:
    """Which temple's outer face a camera sees: from +X (yaw > 0, g3d_host/camera.py) the charted temple."""
    return "the charted temple" if toward(cam)[0] > 0 else "the mirrored temple, from its own side"


def side_note(rec: dict) -> str:
    """Which temple a side photo shows (lab.json leaves the note out; brief_text prints it for a photo it has no
    camera record of)."""
    return f"it shows {temple_seen(rec.get('camera') or {})}"


def colour_text(row: dict) -> str:
    """A photo's colour with its finish when the job data gives one (e.g. "glossy black")."""
    return " ".join(x for x in (row.get("finish"), row.get("colour") or "another colour") if x)


def camera_source(rec: dict | None, ids: dict) -> str | None:
    """The photo id whose camera fit this record's camera was moved from, or None for a camera fitted on its own: a
    transport block's "source" (an image file name, ``ids`` = {image file name: photo id}) or the fit_status "camera
    transported from 05" (a photo id, a file name or its stem), as readings.camera_source reads the basis (the
    readings' camera error and theta are shared along the same lineage). A source the job does not hold reads "a
    photo not in this job"."""
    rec = rec or {}
    tokens = []
    t = rec.get("transport")
    if isinstance(t, dict) and t.get("source"):
        tokens.append(str(t["source"]))
    m = TRANSPORT_RE.search(str(rec.get("fit_status") or ""))
    if m:
        tokens.append(m.group(1).rstrip("."))
    if not tokens:
        return None
    by_stem = {Path(name).stem: pid for name, pid in ids.items()}
    pids = set(ids.values())
    for tok in tokens:
        name = Path(tok).name
        for hit in (ids.get(name), by_stem.get(Path(name).stem), tok if tok in pids else None,
                    by_stem.get(f"reference-{tok}")):
            if hit:
                return hit
    return "a photo not in this job"


def _has_pose(rec: dict) -> bool:
    return bool(rec.get("theta_deg") and rec.get("hinge_axis_xz_mm"))


def _copied_pose(rec: dict) -> bool:
    observed = rec.get("theta_observed")
    return (_has_pose(rec) and (rec.get("pose_provenance") or {}).get("mode") != "as_authored"
            and isinstance(observed, dict) and bool(observed) and not any(observed.values()))


def camera_fit_text(rec: dict, ids: dict) -> str | None:
    """How the photo's camera was made and how well it fits, from its pose-basis record. A camera moved from another
    photo's (camera_source) says so first, with the temples' hinge opening (theta) copied from it, then, when the
    record has them, the image registration's own numbers (``transport``: photo to photo) and its check against the
    model: ``model_check`` (the starting model's silhouette on the compare's raster, tests/proofs/wp6v2_basis.py), else
    the record's ``iou`` / ``contour_mean_px`` (B1's: the model's silhouette at that camera in the WP6 v1 all-views
    validation, which fitted the target photos' cameras and transported B1's from 05: never "fitted", review
    2026-10-12; labelled as that validation's, the check the target photos' lines print, so it is not read beside
    B2's and B3's starting-model checks: review 2026-10-12, P3; once the basis writes B1's model_check, report.json
    black-side IoU 0.8467 / temple 0.8591, that prints instead). A camera fitted on its own prints its fit's ``iou``
    and ``contour_mean_px``."""
    src = camera_source(rec, ids)
    if src is not None:
        t = rec.get("transport") if isinstance(rec.get("transport"), dict) else {}
        nums = [f"IoU {float(t['mask_iou_after_warp']):.3f}" if t.get("mask_iou_after_warp") is not None else None,
                f"mean contour {float(t['mean_bidirectional_px']):.1f} px"
                if t.get("mean_bidirectional_px") is not None else None]
        nums = ", ".join(n for n in nums if n)
        copied = _copied_pose(rec)
        whose = f"{src}'s" if src != "a photo not in this job" else "that of a photo not in this job"
        mc = rec.get("model_check") or {}
        if mc.get("iou") is not None:
            check = (f"starting model's silhouette on the compare's raster: IoU {float(mc['iou']):.3f}"
                     + (f", temple IoU {float(mc['temple_iou']):.3f}" if mc.get("temple_iou") is not None else ""))
        elif rec.get("iou") is not None:      # the check the target photos' fits print, not the starting model's
            c = rec.get("contour_mean_px")
            check = (f"the model's silhouette at this camera in the all-views validation that fitted the target "
                     f"photos' cameras: IoU {float(rec['iou']):.3f}"
                     + (f", mean contour {float(c):.1f} px" if c is not None else ""))
        else:
            check = "not checked against the model"
        return (f"camera moved from {whose} by matching the two photos' silhouettes"
                + (f" (photo to photo, on its registration matte: {nums})" if nums else "")
                + (f", the temples' hinge opening as in {src}" if copied and src != "a photo not in this job" else "")
                + f"; {check}")
    if rec.get("iou") is not None:
        c = rec.get("contour_mean_px")
        return (f"camera fitted to the model's silhouette (all-views fit): IoU {float(rec['iou']):.3f}"
                + (f", mean contour {float(c):.1f} px" if c is not None else ""))
    return None


def outline_text(view: str, cam: dict) -> str | None:
    """What a photo's compares print as numbers: a side view (seeing.SIDE_VIEWS) the outline offsets of the temple
    whose outer face it sees (seeing._side_outline: photo minus model, every 10 mm from d 10 to the tip), the outline
    only on the far side of the view when it looks down (up) on the temple by more than SIDE_FACE_PITCH_DEG; the near
    edge's silhouette is then the top (bottom) face's far edge, which seeing prints minus its median over the stations
    (seeing._far_edge_text, v3p3-fix-seeing: in 01 / 05 / B1 the only number on the arm's top, where session2's head
    sat 2-4 mm low behind d 100 and the brief said its top was not measured); other views print none (v3: the top /
    rear outer-edge offsets and the outline line are not shown)."""
    if view not in SIDE_VIEWS or outer_face_deg(cam) >= EDGE_ON_DEG:
        return None
    whose = "charted" if toward(cam)[0] > 0 else "mirrored"
    pitch = float(cam.get("pitch") or 0.0)
    if pitch > SIDE_FACE_PITCH_DEG:
        edges = ("bottom outline offsets and, for its top, the top face's far edge minus its median (the view looks "
                 "down on the top face)")
    elif pitch < -SIDE_FACE_PITCH_DEG:
        edges = ("top outline offsets and, for its bottom, the bottom face's far edge minus its median (the view "
                 "looks up at the bottom face)")
    else:
        edges = "top and bottom outline offsets"
    return f"its compares print the {whose} temple's {edges}"


def photo_line(p: dict, rec: dict | None, ids: dict) -> str:
    """One photo of the brief: its view (and colour), then from its pose-basis record: the camera direction, the
    temple it sees and how, faces it sees from above or below, the numbers its compares print and its camera's
    lineage and fit."""
    other = p.get("role", "target") != "target"
    head = f"- {p['id']}: {p['view']}" + (f", {colour_text(p)}" if other else "")
    cam = (rec or {}).get("camera") if p.get("role") != "reference" else None
    if not cam:                    # a reference photo, or no pose-basis record given: the view (and side) only
        tail = [p["note"]] if p.get("note") else []
        if p.get("role") == "reference":
            tail.append("no calibrated camera: photo_image only")
        return head + "".join(f"; {t}" for t in tail)
    a, pitch = outer_face_deg(cam), float(cam.get("pitch") or 0.0)
    bits = [f"camera yaw {_deg(cam.get('yaw'))}, pitch {_deg(pitch)}"]
    if a < EDGE_ON_DEG:
        whose = ("the charted temple's outer face" if toward(cam)[0] > 0
                 else "the mirrored temple's outer face, from its own side,")
        bits.append(f"{whose} {angle_band(a)} ({a:.0f} deg)")
    else:
        bits.append(f"the outer faces edge-on ({a:.0f} deg)")
    if 90.0 - abs(pitch) < EDGE_ON_DEG:
        bits.append(f"faces that point straight {'up' if pitch > 0 else 'down'} at {90.0 - abs(pitch):.0f} deg from "
                    "square-on")
    numbers = outline_text(p["view"], cam)
    if numbers:
        bits.append(numbers)
    fit = camera_fit_text(rec, ids)
    if fit:
        bits.append(fit)
    return head + "; " + "; ".join(bits)


def _listed(ids_) -> str:
    return ", ".join(ids_) if ids_ else "none"


def _and(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def lineage_text(recs: dict, ids: dict) -> str | None:
    """The cameras' lineage (WP6_V3 §6): which photos have a camera fit of their own and which camera was moved from
    which (camera_source; the theta copied with it), so which photos share their camera's error. None without a
    camera."""
    cams = [pid for pid, rec in recs.items() if rec and rec.get("camera")]
    if not cams:
        return None
    moved = [(pid, camera_source(recs[pid], ids)) for pid in cams]
    own = [pid for pid, src in moved if src is None]
    moved = [(pid, src) for pid, src in moved if src is not None]
    text = "Cameras:" + (f" {_and(own)} " + ("each have a camera" if len(own) > 1 else "has a camera")
                         + " fitted on its own." if own else "")
    groups: dict = {}
    for pid, src in moved:
        groups.setdefault(_copied_pose(recs[pid]), []).append((pid, src))
    for copied, group in groups.items():
        how = "moved onto its photo by an image similarity that matches the two photos' silhouettes"
        how += (", with the temples' hinge opening (theta) copied, so it shares that camera's error and theta."
                if copied else ", so it shares that camera's error.")
        if len(group) == 1:
            pid, src = group[0]
            of = f"{src}'s camera" if src != "a photo not in this job" else "the camera of a photo not in this job"
            text += f" {pid}'s camera was not fitted: it is {of} {how}"
        else:
            pairs = _and([f"{pid}'s from {src}" for pid, src in group])
            text += (f" The cameras of {_and([pid for pid, _s in group])} were not fitted: each is another photo's camera "
                     f"({pairs}) {how}")
    return text + (" Photos of one direction (above) read a point only together with a photo of another direction."
                   if len(cams) > 1 else "")


def theta_text() -> str:
    """The camera part of every reading (readings.THETA_SD_DEG): each camera fit's temple hinge opening; a theta error
    turns the temple about its hinge as one rigid piece, moving X by about d x pi / 180 mm per degree (THETA_AT_D_MM).
    readings.py moves each camera fit's theta separately and adds the fits in quadrature, so that turn is one and the
    same only for readings made from the same photos (the same fits): those keep a flat face flat (crown-geometry §2:
    K - plane stayed +1.45..+1.59 mm for theta -1..+2 deg, every reading on side + angled pairs); readings from other
    photos carry their own fits' theta (review 2026-10-12, P3: about 0.4 mm at d 80 for 0.3 deg)."""
    per = [f"{d * math.pi / 180.0:.1f}" for d in THETA_AT_D_MM]
    at = ", ".join(f"{v} at d {d}" for v, d in zip(per[1:], THETA_AT_D_MM[1:]))
    return (f"For these photos, the camera fit includes the temples' hinge opening (theta); a reading's camera part is its fits' theta "
            f"taken as uncertain by {THETA_SD_DEG:g} deg. A theta error turns the temple about its hinge as one rigid "
            f"piece: X moves about {per[0]} mm per degree at d {THETA_AT_D_MM[0]}, {at}, the same for every reading "
            "made from the same photos, so such readings keep a flat face flat; readings from different photos carry "
            "their own theta.")


def precision_text(precision: dict | None) -> str | None:
    """How precisely photo_read reads which kind of point on this job's photos (job data, READING_PRECISION), or
    None; a point far back also carries the cameras' theta in its absolute X (theta_text, printed next; review
    2026-10-12, P2: about 1.4 mm per degree at d 80)."""
    if not precision:
        return None
    lo, hi = precision["corner_mm"]
    a_lo, a_hi = precision["along_mm"]
    return (f"Readings (photo_read) on these photos: a crisp corner read from two directions is good to about {lo:g}-"
            f"{hi:g} mm; a tip, or a point on an edge that runs along the line of sight between the photos, only to "
            f"about {a_lo:g}-{a_hi:g} mm along that edge, even where a single pick of it prints less. Read as a "
            "crossing of two traced edges, such a point prints its full error, smallest where the edges cross steeply. "
            "These are the picks' errors: a point far back from the hinge also carries the cameras' hinge opening in "
            "its absolute X (below).")


def conflict_text(c: dict) -> str:
    """A supplied prior comparison (CAMERA_CONFLICTS), preserving its measured location and difference.
    The comparison is not a proof that the current fitted cameras/openings cannot agree."""
    ids = list(c["photos"])
    d = c["d_mm"]
    where = f"near d {d:g}" if isinstance(d, (int, float)) else f"at d {d[0]:g}-{d[1]:g}"
    many = len(ids) > 1
    which = f"photo{'s' if many else ''} {_and(ids)} show{'' if many else 's'}"
    further = f", {c['further_out']}" if c.get("further_out") else ""
    return (f"Supplied prior comparison: the {c['what']} that {which} {where} and "
            f"{c.get('against') or 'the other photos'} had a reported difference there of up to about "
            f"{c['up_to_mm']:g} mm{further}, on {c['temples']}, under that comparison's calibration. "
            "Check the current camera/opening fit and reading residuals before drawing a geometry conclusion; "
            "this observation alone does not establish an irreconcilable camera model.")


def brief_text(product: str, photos: list, cameras: dict | None = None, *, rig: str = DEFAULT_RIG,
               roll_mm: float | None = None, conflicts=(), precision: dict | None = None) -> str:
    """The brief (the job's facts; instructions.txt is general): the target photos and any of another colour (role
    colourway: a camera, photo_compare; role reference: photo_image only), the job's render light (``rig``) and what
    it does not reproduce (LIGHT_TEXT), the two temples' names, one line per photo from its pose-basis record in
    ``cameras`` ({image file name: cameras.json record}; the photo row's path names the file), then the photos grouped
    by direction, by how they see the outer face, the views that look along front-to-back creases, the views whose
    compares print outline offsets and the finishes (listed only for the photo rows that give one); the cameras'
    lineage, how precisely photo_read reads (``precision``: the job's READING_PRECISION) and the theta lever, the job's
    camera-model ``conflicts`` (CAMERA_CONFLICTS rows); revision 0, the scope and the real temples' edge rounding
    (``roll_mm`` when the job knows it, else "not known")."""
    cameras = cameras or {}
    targets = [p for p in photos if p.get("role", "target") == "target"]
    others = [p for p in photos if p.get("role", "target") != "target"]
    recs = {p["id"]: cameras.get(Path(p["path"]).name) if p.get("role") != "reference" else None for p in photos}
    # Transport/fit lineage names the original calibration images, even when an owner renamed local photos.
    ids = {(recs[p["id"]] or {}).get("source_image", Path(p["path"]).name): p["id"]
           for p in photos if p.get("role") != "reference"}
    first = (f"Product: {product}. The target photos ({', '.join(p['id'] for p in targets)}) show the colourway to "
             "match.")
    if others:
        colours = sorted({colour_text(p) for p in others})
        first += (f" Photos {', '.join(p['id'] for p in others)} show the same model in {' and '.join(colours)} (the "
                  "materials never change, so they show the shape only)")
        first += ("; photo_compare and facet_variants show those with a camera beside the gloss-black render." if any(
            p.get("role") == "colourway" for p in others) else ".")
    light = (f"The renders use the job's fixed light, {rig}: {RIG_TEXT[rig]}. The photos' light is unknown."
             if rig in RIG_TEXT else f"The renders use the job's fixed light, {rig}. The photos' light is unknown.")
    lines = [first, light + " " + LIGHT_TEXT, "The charted temple is the one the spec's d, y and t chart; the mirrored "
             "temple is its mirror image on the other side; compares name the two temples so.",
             "", "Each photo: its view; the camera's yaw (0 from the front, +90 from the charted temple's side, "
             "-90 from the mirrored temple's, 180 from the rear) and pitch (+ from above); how it sees the temple's "
             "outer face (0 deg square-on, 90 deg edge-on); the numbers its compares print; its camera."]
    lines += [photo_line(p, recs[p["id"]], ids) for p in targets + others]
    cams = {pid: rec["camera"] for pid, rec in recs.items() if rec and rec.get("camera")}
    if cams:
        band = {b: [pid for pid, c in cams.items() if angle_band(outer_face_deg(c)) == b]
                for b in ("square-on", "at a moderate angle", "edge-on")}
        groups = " | ".join(", ".join(g) for g in direction_groups(cams))
        offsets = [p["id"] for p in targets + others if p["id"] in cams and outline_text(p["view"], cams[p["id"]])]
        lines += ["", f"Directions (cameras within {SAME_DIRECTION_DEG:g} deg of each other count as one, and so do "
                      f"the side views of the two temples): {groups}.",
                  f"The temple's outer face square-on (under {SQUARE_ON_DEG:g} deg): {_listed(band['square-on'])}. At "
                  f"a moderate angle ({SQUARE_ON_DEG:g} to {EDGE_ON_DEG:g} deg): {_listed(band['at a moderate angle'])}"
                  f". Edge-on ({EDGE_ON_DEG:g} deg or more): {_listed(band['edge-on'])}.",
                  "Front and rear views look along creases that run front to back: "
                  f"{_listed([pid for pid, c in cams.items() if looks_along_deg(c) < ALONG_DEG])} (cameras within "
                  f"{ALONG_DEG:g} deg of the temple's length).",
                  f"Outline offsets (photo minus model, mm) are printed for {_listed(offsets)} only."]
    finishes = sorted({p["finish"] for p in photos if p.get("finish")})
    for f in finishes:
        lines.append(f"{f.capitalize()} photos: {', '.join(p['id'] for p in photos if p.get('finish') == f)}.")
    unstated = [p["id"] for p in photos if not p.get("finish")]
    if finishes and unstated:      # a photo without a finish is not matte: the job data only names none for it
        lines.append(f"No finish is given for {', '.join(unstated)}.")
    if cams:
        posed = [pid for pid, rec in recs.items() if rec and _has_pose(rec)
                 and (rec.get("pose_provenance") or {}).get("opening_calibrated") is not False]
        lines += [""] + [x for x in (lineage_text(recs, ids), precision_text(precision)) if x]
        if posed:
            lines.append("Calibrated hinge-opening metadata: " + _listed(posed) + ". " + theta_text())
        unposed = [pid for pid in cams if pid not in posed]
        if unposed:
            lines.append("As-authored temple pose; hinge opening is not calibrated for: " + _listed(unposed) + ".")
        rigid = [pid for pid, rec in recs.items() if rec and _has_pose(rec)
                 and (rec.get("pose_provenance") or {}).get("mode") == "as_authored"]
        if rigid:
            lines.append("For " + _listed(rigid) + ", the verified rigid camera fit used the authored mesh with zero "
                         "additional hinge rotation. The explicit zero transform preserves that assumption; it is "
                         "not a measured photo opening. Its hinge axes come from the captured current model. "
                         "Any reported hinge-opening uncertainty uses the tool's fixed assumption, not a measured "
                         "opening confidence for these photos.")
    lines += [conflict_text(c) for c in conflicts or ()]
    edge = (f"The real temples' edge rounding is about {float(roll_mm):g} mm (roll_mm)." if roll_mm is not None
            else "The real temples' edge rounding (roll_mm) is not known: judge it from the photos' edges.")
    lines += ["", "The scene holds revision 0, the starting model's temple measured into the spec: its outline (the "
                  "top and bottom chains and the tip's rounding) and inner face, and at every d an outer surface "
                  "straight in y (the base wedge); no faces, no edges, roll_mm 0. It is not the target. What you may "
                  "change is the whole temple from the hinge to its tip: its faces and edges, and its outline (the "
                  "outline chains are editable along the whole length). " + edge + " Make the temples look like the "
                  "temples in these photos."]
    return "\n".join(lines) + "\n"


def prepare(root, *, source_scene, scene_sha256, reference, basis_json, rig: str = DEFAULT_RIG,
            lab_class=Lab, import_basis=C.import_cameras_json, colourway_photos=(), reference_photos=(),
            camera_conflicts=(), reading_precision=None, photo_ids=None) -> dict:
    """A new package (module doc). $0; ~1 min with studio_dome_v1 (the default), ~4 min with photo_env (its light
    fit). ``colourway_photos`` name pose-basis images of another colourway ({id, image, colour, finish?, sha256}:
    compared with photo_compare), ``reference_photos`` images without a camera ({id, view, colour, finish?, source,
    path, sha256}); colour and finish are printed by the brief and the photo labels. ``camera_conflicts`` and
    ``reading_precision`` are the job's (CAMERA_CONFLICTS, READING_PRECISION), printed by the brief."""
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("use a new empty package directory")
    if rig not in MATCHED_RIGS:
        raise ValueError(f"rig must be one of {MATCHED_RIGS}")
    inputs = root / "inputs"
    (inputs / "images").mkdir(parents=True)
    assets = []

    def copy(src, rel, want=None):
        dst = (inputs / rel).resolve()
        if not dst.is_relative_to(inputs):
            raise ValueError("input copy escapes the package")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        digest = sha(dst)
        if digest != sha(src) or (want is not None and digest != want):
            raise ValueError(f"{rel}: the copy or its source does not have the expected sha256")
        assets.append({"path": rel, "sha256": digest})
        return dst
    copy(source_scene, "scene.blend", scene_sha256)
    reference = Path(reference)
    manifest = read(reference / "manifest.json")
    doc = read(basis_json)
    targets = {n: r for n, r in doc["images"].items() if r.get("colourway", "target") == "target"}
    if not targets:
        raise ValueError("the pose basis has no target photo")
    bdir = root / "basis"
    bdir.mkdir()

    def copy_matte(name, rec):
        if rec.get("matte_mode") != "supplied":
            return
        src = (Path(basis_json).parent / rec["matte_file"]).resolve()
        dst = (bdir / rec["matte_file"]).resolve()
        if not src.is_relative_to(Path(basis_json).parent.resolve()) or not dst.is_relative_to(bdir):
            raise ValueError(f"{name}: supplied matte escapes the basis")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if sha(dst) != rec.get("matte_file_sha256"):
            raise ValueError(f"{name}: the supplied matte does not have the expected sha256")

    photos, paths = [], {}
    for name in sorted(targets):
        row, rec = manifest["files"].get(name), targets[name]
        if row is None or row["sha256"] != rec["image_sha256"]:
            raise ValueError(f"{name}: not in the reference manifest, or the manifest and the basis disagree")
        paths[name] = copy(reference / name, f"images/{name}", row["sha256"])
        copy_matte(name, rec)
        with Image.open(paths[name]) as im:
            size = list(ImageOps.exif_transpose(im).size)
        photos.append({"id": (photo_ids or {}).get(name, C.ref_id(name)), "basis_ref": C.ref_id(name), "path": f"images/{name}", "view": rec["view_kind"],
                       "size_px": size, "sha256": row["sha256"], "role": "target",
                       **({"note": side_note(rec)} if rec["view_kind"] == "side" else {})})
    variants = {}
    for want in colourway_photos:
        name = want["image"]
        rec = doc["images"].get(name)
        if rec is None or rec.get("colourway") in (None, "target") or rec.get("image_sha256") != want["sha256"]:
            raise ValueError(f"{name}: not a colourway image of the pose basis with the expected sha256")
        paths[name] = copy(rec["image"], f"images/{name}", want["sha256"])
        copy_matte(name, rec)
        variants[name] = rec
        with Image.open(paths[name]) as im:
            size = list(ImageOps.exif_transpose(im).size)
        photos.append({"id": want["id"], "path": f"images/{name}", "view": rec["view_kind"], "size_px": size,
                       "sha256": want["sha256"], "role": "colourway", "colour": want["colour"],
                       **({"finish": want["finish"]} if want.get("finish") else {}),
                       "basis_ref": C.ref_id(name), "source": str(rec["image"]),
                       **({"note": side_note(rec)} if rec["view_kind"] == "side" else {})})
    for want in reference_photos:
        dst = copy(want["source"], want["path"], want["sha256"])
        with Image.open(dst) as im:
            size = list(ImageOps.exif_transpose(im).size)
        photos.append({"id": want["id"], "path": want["path"], "view": want["view"], "size_px": size,
                       "sha256": want["sha256"], "role": "reference", "colour": want["colour"],
                       **({"finish": want["finish"]} if want.get("finish") else {}), "source": str(want["source"])})
    ids = [p["id"] for p in photos]
    if len(set(ids)) != len(ids):
        raise ValueError(f"photo ids must be distinct: {ids}")
    (inputs / "brief.txt").write_text(brief_text(manifest.get("name") or reference.name, photos, doc["images"],
                                                 rig=rig, conflicts=camera_conflicts, precision=reading_precision),
                                      encoding="utf-8")
    assets.append({"path": "brief.txt", "sha256": sha(inputs / "brief.txt")})
    copy(HERE / "instructions.txt", "instructions.txt")
    write_json_atomic(bdir / "cameras.json", {**doc, "images": {**targets, **variants}})
    basis = import_basis(root, bdir / "cameras.json", paths,
                         reason="facet lab package (target photos and the calibrated colourway photos)")
    bound = basis_files(root, basis.id)
    write_json_atomic(inputs / "lab.json", {
        "schema": LAB_SCHEMA, "rig": rig, "product": manifest.get("name"), "assets": assets,
        "photos": [{k: v for k, v in p.items() if k != "note"} for p in photos],
        "source": {"scene": str(source_scene), "scene_sha256": scene_sha256,
                   "reference_manifest": str(reference / "manifest.json"),
                   "reference_manifest_sha256": sha(reference / "manifest.json")},
        "basis": {"source": str(basis_json), "source_sha256": sha(basis_json), "id": basis.id, "files": bound}})
    job = {"schema": "g3d.job/1", "name": f"{manifest.get('name')}: isolated facet lab", "rig": rig,
           "implementation": implementation(), "scope": "isolated facet lab on a copy, not production"}
    write_json_atomic(root / "job.json", job)
    return finish_preparation(root, basis.id, job, photo_count=len(photos), lab_class=lab_class)


def finish_preparation(root, basis_id, job, *, photo_count, lab_class=Lab):
    """Prepare revision zero, bind the tools and close the private worker on every exit."""
    rig = job["rig"]
    lab = lab_class(root)
    try:
        prep = lab.prepare()
        if not lab.is_clean():
            raise ValueError("the prepared package is not clean")
        env = environment(root, basis_id) if rig == ENV_RIG else None
        if rig == ENV_RIG and env is None:
            raise ValueError("photo_env: the job's light was not fitted at prepare")
        job["environment"] = env
        write_json_atomic(root / "job.json", job)
        tools = registry(lab)
        write_json_atomic(root / "tools.json", [{"name": t.name, "description": t.description,
                                                 "parameters": t.params_json_schema} for t in tools])
        exit_checkpoint = lab.exit_checkpoint()
        if not exit_checkpoint["verified"]:
            raise ValueError("the prepared checkpoint does not verify")
        rev0 = lab.state["revisions"][0]
        write_json_atomic(root / "prepared.json", {
            "status": "prepared", "rig": rig, "prepare": prep, "start_checkpoint": lab.state["start_checkpoint"],
            "checkpoint": exit_checkpoint["id"], "exit_checkpoint": exit_checkpoint,
            "revision0": {"sha256": rev0["sha256"], "file": rev0["file"],
                          "template_report": (lab.state.get("original") or {}).get("template_report")},
            "compares": [c["compare_id"] for c in lab.state["compares"]], "environment": env, "paid_calls": 0})
    finally:
        lab.close()
    return {"status": "prepared", "path": str(root), "rig": rig, "photos": photo_count,
            "checkpoint": exit_checkpoint["id"], "revision0": rev0["sha256"][:8], "paid_calls": 0}


def basis_files(root: Path, basis_id: str) -> dict:
    """sha256 of the pose basis files a package binds (the source copy outside inputs/ with any supplied matte, and
    the frozen basis)."""
    root = Path(root)
    files = ["basis/cameras.json", f"{C.CAMERAS_DIR}/{basis_id}/cameras.json", f"{C.CAMERAS_DIR}/{basis_id}/basis.json"]
    files += sorted(rel for rel in (p.relative_to(root).as_posix() for directory in (root / "basis", root / C.CAMERAS_DIR / basis_id)
                                    for p in directory.rglob("*")
                                    if p.is_file() and p.name != C.HISTORY) if rel not in files)
    return {f: sha(root / f) for f in files}


def environment(root: Path, basis_id: str) -> dict | None:
    """The job's frozen photo_env light (seeing: work/compare/env-<basis>.json), as {file, weights_sha}."""
    p = root / "work" / "compare" / f"env-{basis_id}.json"
    if not p.is_file():
        return None
    return {"file": str(p.relative_to(root)), "weights_sha": read(p).get("weights_sha"), "sha256": sha(p)}


def check_package(root: Path, lab) -> None:
    """Refuse (ValueError) a package whose implementation, inputs, pose basis or frozen light changed, or which is
    not clean (a paid run starts from revision 0 of an untouched package)."""
    job = read(root / "job.json")
    if job.get("implementation") != implementation():
        raise ValueError("implementation changed since preparation; prepare a fresh package")
    lab.verify_inputs()
    basis = lab.manifest.get("basis") or {}
    if not basis.get("files") or basis_files(root, basis["id"]) != basis["files"]:
        raise ValueError("the pose basis changed since preparation; prepare a fresh package")
    if job.get("rig") != lab.rig:
        raise ValueError("job.json and lab.json name different rigs")
    if lab.rig == ENV_RIG:
        env = environment(root, basis["id"])
        if env is None or env != job.get("environment"):
            raise ValueError("the frozen photo_env light changed since preparation; prepare a fresh package")
    if not lab.is_clean():
        raise ValueError("a paid run starts only from a clean package (no facet tool has run); prepare a fresh one")
    head = lab.head_record()
    if head is None or head["revision"] != 0:
        raise ValueError("the package's head is not revision 0")


def instructions_text(root: Path, k: int) -> str:
    text = (Path(root) / "inputs" / "instructions.txt").read_text(encoding="utf-8")
    if text.count(K_TOKEN) != 1:
        raise ValueError(f"instructions.txt must state {K_TOKEN} once (finish precondition 1)")
    return text.replace(K_TOKEN, str(int(k)))


def references(lab) -> list:
    """Every photo's recorded transport bytes, then bound observation overlays.

    Evidence overlays are separate ReferenceInputs, never extra photo IDs or
    calibration targets. Their exact bytes/sizes enter startup binding and cost.
    """
    rows = lab.manifest["photos"]
    targets = [r for r in rows if r.get("role", "target") == "target"]
    others = [r for r in rows if r.get("role", "target") != "target"]
    out = []
    for group, rows_ in (("target", targets), ("other", others)):
        for i, row in enumerate(rows_, 1):
            relative = row.get("sent_path", row["path"])
            p = (lab.root / "inputs" / relative).resolve()
            if not p.is_relative_to((lab.root / "inputs").resolve()):
                raise ValueError("reference photo escaped its package")
            if row.get("sent_sha256") is not None and sha(p) != row["sent_sha256"]:
                raise ValueError("reference photo transport bytes changed")
            with Image.open(p) as im:
                size = ImageOps.exif_transpose(im).size
                mime = Image.MIME[im.format]
            if row.get("sent_mime", mime) != mime:
                raise ValueError("reference photo transport MIME differs")
            if group == "target":
                label = f"TARGET PHOTO {row['id']} ({i} of {len(rows_)})"
            else:
                label = (f"{(row.get('colour') or 'other colour').upper()} PHOTO {row['id']} ({i} of {len(rows_)}; "
                         f"the same model in {colour_text(row)}, "
                         + ("calibrated camera)" if row["role"] == "colourway" else "reference only, no camera)"))
            image_url = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
            out.append(ReferenceInput(label=label, path=relative, image_url=image_url, detail="high",
                                      view=row["view"], size_px=size))
    for row in (lab.manifest.get('arm_evidence') or {}).get('images',[]):
        p=(lab.root/'inputs'/row['path']).resolve()
        if not p.is_relative_to((lab.root/'inputs').resolve()) or sha(p)!=row['sha256']:
            raise ValueError('arm observation image changed or escaped its package')
        with Image.open(p) as im:
            size=im.size
        if list(size)!=row['size_px']:
            raise ValueError('arm observation image dimensions changed')
        out.append(ReferenceInput(label=row['label'],path=row['path'],image_url=data_url(p),detail='high',
                                  view=row['view'],size_px=size))
    return out


def tool_contract() -> ToolContract:
    """The facet run's tool contract (loop.contract_problems): the instructions name exactly the registered tools, and
    neither loop.REMOVED_PHRASES nor RETIRED_PHRASES appears in the instructions, the brief or any tool or parameter
    description."""
    return ToolContract(frozenset(TOOL_NAMES), removed_phrases=tuple(REMOVED_PHRASES) + RETIRED_PHRASES)


def make_spec(root, run_id, settings, cap, *, write_request=False, lab=None, estimate_path=ESTIMATE, estimate=None):
    """The RunSpec of a paid run on a clean package (and, with write_request, its approval request with the facet
    estimate from estimate_path, or the given ``estimate`` block; nothing is approved here)."""
    root = Path(root).resolve()
    lab = lab or Lab(root)
    check_package(root, lab)
    tools = registry(lab, settings)
    instructions = instructions_text(root, settings.finish_k)
    # Approval binds the exact stored text, including CRLF or lone CR; do not normalize owner input.
    brief = (root / "inputs" / "brief.txt").read_bytes().decode("utf-8")
    photos = references(lab)
    contract = tool_contract()
    problems = contract_problems(contract, instructions=instructions, brief=brief, tools=tools)
    if problems:
        raise ValueError(problems)
    request = A.build_request(root, instructions_sha256=instructions_sha256(instructions),
                              tool_registry_sha256=tool_registry_sha256(tools, settings),
                              settings=settings.approval_settings(),
                              run_inputs=A.run_input_binding(root, brief, photos))
    if write_request:
        run_dir = root / "runs" / run_id
        cost = estimate if estimate is not None else request_estimate(
            read(estimate_path), settings, c0_chars(instructions, brief, tools, photos), [p.size_px for p in photos],
            variant_sheets=lab.variant_photos_max(), image_bytes=[len(p.image_url) for p in photos])
        A.write_approval_request(run_dir, job_id=root.name, request=request, estimate=cost, suggested_cap_usd=cap)
        # the exact text sent (its sha256 is the request's instructions_sha256): LF, never the platform's newline
        (run_dir / "instructions-sent.txt").write_text(instructions, encoding="utf-8", newline="\n")
    note = f"The scene holds revision 0 at checkpoint {lab.s.head['id']}; facet_inspect shows it."
    spec = RunSpec(job_dir=root, run_id=run_id, instructions=instructions, tools=tools, brief=brief, photos=photos,
                   current_request_sha256=A.request_sha256(request), settings=settings, requested_cap_usd=cap,
                   scene_note=note, contract=contract)
    return spec, lab


# --------------------------------------------------------------------------------------------- estimate
def record_kind(rec: dict) -> str:
    """The kind a rehearsal records for an image of the lab's index (Lab._image's {kind, ref, ...}): ZOOM_GRID_KIND
    for a zoom grid (registered as kind "compare" with zoom=True), else the lab's kind."""
    return ZOOM_GRID_KIND if rec.get("zoom") else str(rec.get("kind"))


def image_tokens(w: int, h: int) -> float:
    return TOKENS_PER_PATCH * math.ceil(w / PATCH_PX) * math.ceil(h / PATCH_PX) + TOKENS_PER_IMAGE


def c0_chars(instructions: str, brief: str, tools, photos) -> int:
    """Text characters of a first request: instructions, brief, tool schemas and one descriptor line per photo."""
    schemas = json.dumps([{"name": t.name, "description": t.description, "parameters": t.params_json_schema}
                          for t in tools], separators=(",", ":"))
    return len(instructions) + len(brief) + len(schemas) + 160 * len(photos) + 400


def first_request_tokens(chars: int, sizes) -> int:
    return math.ceil(TOKENS_PER_CHAR * chars + sum(image_tokens(w, h) for w, h in sizes))


def image_bytes(w: int, h: int, kind: str | None = None) -> float:
    """An image's bytes in a request (base64 data URL) by the bytes model: its formula tokens x its kind's largest
    measured bytes per token (BYTES_PER_IMAGE_TOKEN; an unmeasured kind at the largest)."""
    return image_tokens(w, h) * BYTES_PER_IMAGE_TOKEN.get(kind, BYTES_PER_IMAGE_TOKEN_MAX)


def first_request_bytes(chars: int, sizes, image_sizes_bytes=None) -> int:
    """A first request's bytes: its text, the per-request overhead and its photos, at their own data URLs' bytes when
    given (request_estimate: the package's photos), else by the bytes model (an unmeasured kind: high)."""
    images = sum(image_sizes_bytes) if image_sizes_bytes is not None else sum(image_bytes(w, h) for w, h in sizes)
    return round(TEXT_BYTES_PER_CHAR * chars + REQUEST_OVERHEAD_BYTES + images)


def call_tokens(sample: dict) -> dict:
    """One recorded tool call: its growth of the context (arguments and output text, every image) and the part in
    images the context guard may replace, in tokens and in request bytes (image_bytes), with its image counts."""
    imgs = [image_tokens(w, h) for w, h, _kind in sample["images"]]
    sizes = [image_bytes(w, h, kind) for w, h, kind in sample["images"]]
    stub = [k for k, (_w, _h, kind) in enumerate(sample["images"]) if kind in STUBBED_KINDS]
    text = sample["text_chars"] + sample["args_chars"]
    return {"tokens": TOKENS_PER_CHAR * text + sum(imgs), "stubbable": sum(imgs[k] for k in stub),
            "stub_images": len(stub), "bytes": TEXT_BYTES_PER_CHAR * text + sum(sizes),
            "stub_bytes": sum(sizes[k] for k in stub), "images": len(imgs)}


def variant_sheets(rows: list) -> int:
    """The most sheets one facet_variants call returns for a job with these lab.json photo rows: one per photo, at
    most VARIANT_TARGETS_MAX target photos plus, when the job has photos of another colour with a camera, one of
    those, at most VARIANT_PHOTOS_MAX (Lab.variant_photos_max's rule)."""
    others = sum(1 for r in rows if r.get("role") == "colourway")
    return min(VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX + others)


def rehearsal_variant_sheets(rehearsal: dict) -> int:
    """variant_sheets of the job the rehearsal prepared (its package's inputs/lab.json); VARIANT_PHOTOS_MAX, the most
    any job takes, when that package cannot be read."""
    root = (rehearsal.get("prepared") or {}).get("path")
    try:
        return variant_sheets(read(Path(root) / "inputs" / "lab.json")["photos"]) if root else VARIANT_PHOTOS_MAX
    except (OSError, ValueError, KeyError, TypeError):
        return VARIANT_PHOTOS_MAX


def read_sheet_px() -> tuple[int, int]:
    """The largest pick sheet one reading returns: readings.pick_sheet's layout (two PANEL_W panels and three gaps
    wide; the title, then a PANEL_H panel row with its label and a gap per photo, then a gap, tall) at
    readings.PHOTOS_MAX photos: 972 x 1412 px. tests/agent/test_temple_lab_cli.py draws a real 4-photo sheet against
    it."""
    n = RD.PHOTOS_MAX
    return (2 * RD.PANEL_W + 3 * RD.GAP_PX,
            RD.TITLE_PX + n * (RD.PANEL_H + RD.LABEL_PX + RD.GAP_PX) + RD.GAP_PX)


@functools.lru_cache(maxsize=1)
def zoom_grid_px() -> tuple[int, int]:
    """The largest zoom grid one zoom item returns: seeing's 2 x 2 compare.grid of compare.labelled(fit) panels, each at
    compare.ZOOM_PANEL_PX (the largest compare.zoom_scale makes), composed by compare's own functions on blank panels
    (1538 x 1066 px today)."""
    panel = C.labelled(Image.new("RGB", tuple(C.ZOOM_PANEL_PX)), "", fit=True)
    return tuple(C.grid([panel] * 4, 2, max_px=C.SHEET_MAX_PX).size)


def priced_sample(sample: dict, sheets: int, *, readings: int = READ_POINTS_MAX, zooms: int = ZOOM_MAX) -> dict:
    """A recorded call priced at the most images one call of its tool returns (an upper bound; the recorded call is
    never changed):
    - photo_read with a pick sheet: ``readings`` pick sheets of the largest size (read_sheet_px) in place of its own,
      its text and arguments scaled by ``readings`` / its own sheets (each reading prints one text line of ~425-970
      characters, readings.reading_text, and takes up to 4 photos x 2 traced edges of arguments); a call with no pick
      sheet (a drop) is marked ``unpriced``: mix_step leaves it out of photo_read's mean, so a drop never dilutes the
      price of a read (review 2026-10-12, P2: one drop call halved it, 7,274 against 14,472 tokens a call);
    - photo_compare: ``zooms`` zoom grids of the largest size (zoom_grid_px, kind ZOOM_GRID_KIND) and ZOOM_TEXT_CHARS
      of text for each zoom beyond its own, beside its compare images; its own zoom grids (recorded as ZOOM_GRID_KIND,
      record_kind) are replaced; a call recorded with its grids as "compare" images (before record_kind) keeps them
      as recorded and is priced up to ``zooms`` grids high (conservative).
    - facet_variants: ``sheets`` sheets (or its own count, if more) of the largest page, sheets.SHEET_W x SHEET_H, in
      place of its own, its text and arguments scaled to VARIANTS_MAX arms (by VARIANTS_MAX / its "arms" when the
      rehearsal records them, else VARIANTS_MAX / VARIANTS_MIN; never below as recorded); a call with no sheet (every
      arm refused) is unchanged.
    Every other call is returned unchanged (facet_inspect is priced at its largest recorded call by priced_samples)."""
    tool = sample["tool"]
    imgs = [list(i) for i in sample["images"]]
    if tool == "facet_variants":
        shown = [i for i in imgs if i[2] == "variants"]
        if not shown:
            return sample
        arms = sample.get("arms")
        f = max(1.0, VARIANTS_MAX / (int(arms) if arms else VARIANTS_MIN))
        rest = [i for i in imgs if i[2] != "variants"]
        return {**sample, "images": rest + [[SH.SHEET_W, SH.SHEET_H, "variants"]] * max(sheets, len(shown)),
                "text_chars": round(sample["text_chars"] * f), "args_chars": round(sample["args_chars"] * f)}
    if tool == "photo_read":
        shown = [i for i in imgs if i[2] in READING_KINDS]
        if not shown:
            return {**sample, "unpriced": True}
        f = readings / len(shown)
        w, h = read_sheet_px()
        rest = [i for i in imgs if i[2] not in READING_KINDS]
        return {**sample, "images": rest + [[w, h, READING_KINDS[0]]] * readings,
                "text_chars": round(sample["text_chars"] * f), "args_chars": round(sample["args_chars"] * f)}
    if tool == "photo_compare":
        own = [i for i in imgs if i[2] == ZOOM_GRID_KIND]
        w, h = zoom_grid_px()
        return {**sample, "images": [i for i in imgs if i not in own] + [[w, h, ZOOM_GRID_KIND]] * zooms,
                "text_chars": sample["text_chars"] + ZOOM_TEXT_CHARS * max(0, zooms - len(own))}
    return sample


def priced_samples(samples: list, sheets: int, mixes=None) -> list:
    """Every recorded call priced (priced_sample), and facet_inspect (LARGEST_PRICED) at its largest successful call:
    the others are marked ``unpriced``. Refuses (ValueError) a rehearsal with no successful photo_read call
    that returned a pick sheet while a mix prices photo_read: a read's text and arguments are priced from a real one
    (run the estimate on a rehearsal of the current tools). The zoom grids need no recorded one: their size is the
    tool's (zoom_grid_px)."""
    tools = {t for mix in (mixes or MIXES).values() for t in mix}
    if "photo_read" in tools and not any(s["tool"] == "photo_read" and s["status"] in OK_STATUSES
                                         and any(i[2] in READING_KINDS for i in s["images"]) for s in samples):
        raise ValueError(f"the rehearsal recorded no photo_read call with a pick sheet (image kind "
                         f"{' / '.join(READING_KINDS)}) to price: run the estimate on a rehearsal of the current tools")
    priced = [priced_sample(s, sheets) for s in samples]
    for tool in LARGEST_PRICED:         # every successful call but the largest is left out of the tool's mean
        rows = [k for k, s in enumerate(priced) if s["tool"] == tool and s["status"] in OK_STATUSES]
        if rows:
            top = max(rows, key=lambda k: call_tokens(priced[k])["tokens"])
            priced = [{**s, "unpriced": True} if k in rows and k != top else s for k, s in enumerate(priced)]
    return priced


def mix_step(samples: list, mix: dict) -> dict:
    """The mean growth per request of a call mix, from the rehearsal's successful calls of each tool (a call
    priced_sample or priced_samples marks ``unpriced`` is left out), in tokens and in request bytes and images
    (call_tokens; the per-request overhead is project's)."""
    by_tool: dict = {}
    for s in samples:
        if s["status"] in OK_STATUSES and not s.get("unpriced"):
            by_tool.setdefault(s["tool"], []).append(call_tokens(s))
    total = sum(mix.values())
    keys = ("tokens", "stubbable", "stub_images", "bytes", "stub_bytes", "images")
    step = {"tokens": 0.0, "stubbable": 0.0, "results": 0.0, "stub_images": 0.0, "bytes": 0.0, "stub_bytes": 0.0,
            "images": 0.0}
    for tool, weight in mix.items():
        rows = by_tool.get(tool)
        if not rows:
            raise ValueError(f"the rehearsal has no successful {tool} call to price")
        share = weight / total
        for key in keys:
            step[key] += share * sum(r[key] for r in rows) / len(rows)
        step["results"] += share * sum(1 for r in rows if r["stub_images"]) / len(rows)
    return step


def project(c0: int, step: dict, requests: int, *, output: int, max_output_tokens: int,
            guard_tokens: int | None = CONTEXT_GUARD_TOKENS, guard_bytes: int | None = CONTEXT_GUARD_BYTES,
            c0_bytes: int | None = None, c0_images: int | None = None, tariff: Tariff | None = None) -> dict:
    """One modelled run, request by request with the frozen tariff (the 272k step included): request i's input is
    the previous input (cached) plus the mean growth (written).

    The context guard (loop.ContextGuard, CONTEXT_GUARD /2; off with guard_tokens None, as a run without
    context_guard_tokens): once a request reaches guard_tokens input tokens (once) or the next byte rung, k x
    guard_bytes request bytes, the next request replaces the images of all compare / variant results but
    GUARD_KEEP_RESULTS (those not replaced at an earlier epoch) by stubs and is written afresh after the brief; the next
    byte rung is the first above that request ("context_guard_requests" lists the epochs, "context_guard_request" the
    first).

    With ``c0_bytes`` (a first request's bytes, first_request_bytes) and a step that has "bytes" (mix_step), the run
    also carries its request bytes and images (the bytes model, BYTES_PER_IMAGE_TOKEN; each request adds
    REQUEST_OVERHEAD_BYTES): "peak_request_mb", "peak_images", the count's modelled time at that peak
    ("peak_count_s", COUNT_S_BASE + COUNT_S_PER_MB per MB) beside its timeout ("count_timeout_s_at_peak",
    budget.count_timeout_s); else those are None and the byte rungs are off.

    A request whose input would pass the model's input limit (pricing.MODEL_MAX_INPUT_TOKENS) is never sent (the budget
    guard refuses it, budget.reserve), and neither is one over the provider's documented per-request limit
    (budget.PAYLOAD_LIMIT_BYTES / PAYLOAD_LIMIT_IMAGES: PayloadTooLarge), so the run ends before it:
    "input_limit_request" / "payload_limit_request" names it and the cost is that of the requests before it (a heavy
    mix's 90-reply run reaches the input limit on a rehearsal of large images; the estimate then says so instead of
    failing)."""
    tariff = tariff or Tariff.frozen()
    sized = c0_bytes is not None and "bytes" in step
    prev = prev_bytes = prev_images = None
    spent, need = 0, 0
    results, stubbed, armed, token_due, byte_rung = 0.0, 0.0, False, True, 1
    guards: list = []
    long_at = limit_at = payload_at = None
    peak, peak_bytes, peak_images = 0, 0.0, 0.0
    res = step["results"]
    per_result = step["stubbable"] / res if res else 0.0
    imgs_per_result = step["stub_images"] / res if res else 0.0
    bytes_per_result = step.get("stub_bytes", 0.0) / res if res else 0.0
    for i in range(requests):
        if prev is None:
            inp, cached = c0, 0
            size, images = (float(c0_bytes), float(c0_images or 0)) if sized else (None, None)
        else:
            new, cached = prev + step["tokens"], prev
            if sized:
                size, images = prev_bytes + step["bytes"] + REQUEST_OVERHEAD_BYTES, prev_images + step["images"]
            if armed:
                drop = max(0.0, results - GUARD_KEEP_RESULTS - stubbed)
                stubbed += drop
                new -= drop * (per_result - imgs_per_result * TOKENS_PER_CHAR * STUB_CHARS)
                if sized:
                    size -= drop * (bytes_per_result - imgs_per_result * TEXT_BYTES_PER_CHAR * STUB_CHARS)
                    images -= drop * imgs_per_result
                cached, armed = c0, False
                guards.append(i + 1)
            inp = math.ceil(new)
            results += step["results"]
        if inp > MODEL_MAX_INPUT_TOKENS:
            limit_at = i + 1
            break
        if sized and (size > PAYLOAD_LIMIT_BYTES or images > PAYLOAD_LIMIT_IMAGES):
            payload_at = i + 1
            break
        cached = min(cached, inp)
        usage = {"input_tokens": inp, "output_tokens": output, "total_tokens": inp + output,
                 "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": inp - cached},
                 "output_tokens_details": {"reasoning_tokens": 0}}
        settled, hold = tariff.settle_micro(usage), tariff.reserve_micro(inp, max_output_tokens)
        need = max(need, spent + hold)
        spent += settled
        peak = max(peak, inp)
        if sized:
            peak_bytes, peak_images = max(peak_bytes, size), max(peak_images, images)
        if long_at is None and inp > LONG_CONTEXT_INPUT_TOKENS:
            long_at = i + 1
        if guard_tokens is not None and not armed:
            by_bytes = sized and guard_bytes and size >= byte_rung * guard_bytes
            if (token_due and inp >= guard_tokens) or by_bytes:
                armed, token_due = True, token_due and inp < guard_tokens
                if sized and guard_bytes:
                    byte_rung = max(byte_rung, math.floor(size / guard_bytes) + 1)
        prev, prev_bytes, prev_images = inp, size, (images if sized else None)
    mb = peak_bytes / 1e6 if sized else None
    return {"requests": requests, "expected_micro_usd": spent, "expected_usd": usd_text(spent),
            "required_cap_micro_usd": need, "last_input_tokens": prev, "peak_input_tokens": peak,
            "context_guard_request": guards[0] if guards else None, "context_guard_requests": guards,
            "first_long_context_request": long_at, "input_limit_request": limit_at,
            "payload_limit_request": payload_at, "peak_request_mb": round(mb, 1) if sized else None,
            "peak_images": math.ceil(peak_images) if sized else None,
            "peak_count_s": round(COUNT_S_BASE + COUNT_S_PER_MB * mb, 1) if sized else None,
            "count_timeout_s_at_peak": round(count_timeout_s(peak_bytes)) if sized else None}


def limit_text(run: dict) -> str:
    """A projected run's input-limit clause (project: the run ends before the request that would pass it), and its
    request-size clause (payload_limit_request: the provider's documented per-request limit), or ""."""
    at = run.get("input_limit_request")
    out = (f"; its context reaches the model's {MODEL_MAX_INPUT_TOKENS:,}-token input limit at request {at}, where "
           "such a run ends") if at else ""
    at = run.get("payload_limit_request")
    return out + ((f"; its requests reach the provider's per-request limit ({PAYLOAD_LIMIT_BYTES / 1e6:.0f} MB or "
                   f"{PAYLOAD_LIMIT_IMAGES:,} images) at request {at}, where such a run ends") if at else "")


def high_text(mix: str) -> str:
    """The range top's mix in words (the table's rows and the approval text)."""
    if mix == "edit_heavy":
        return "every reply an edit"
    if mix == "typical":
        return "the typical mix at the high output"
    m = MIXES[mix]
    return f"a read-heavy mix (photo_read {m['photo_read']} in {sum(m.values())}) at the high output"


# The approval text's statement of what "up to" covers (review 2026-10-12, P2: the v3 mix is an unmeasured assumption,
# and a read-heavier run than the typical one passed the old top).
MIX_ASSUMED = ("The v3 tool mix is assumed (no v3 run has been measured): \"up to\" covers only the listed mixes, each "
               "at the high output: the typical mix, every reply an edit, and a read-heavy mix (photo_read "
               f"{MIXES['read_heavy']['photo_read']} in {sum(MIXES['read_heavy'].values())}).")


def range_top(c0: int, steps: dict, requests: int, *, max_output_tokens: int,
              guard_tokens: int | None = CONTEXT_GUARD_TOKENS, guard_bytes: int | None = CONTEXT_GUARD_BYTES,
              c0_bytes: int | None = None, c0_images: int | None = None) -> dict:
    """The range's top: the costliest (expected cost) of RANGE_MIXES (every reply an edit, the typical mix and the
    read-heavy one), each at the high output tokens, its "mix" named (high_text); "required_cap_micro_usd" is the
    largest of their runs'. v2's top was always every reply an edit; v3's photo_read at READ_POINTS_MAX pick sheets and
    photo_compare at ZOOM_MAX zoom grids can make a mix with more reads the costlier one, and the v3 weights are
    assumed (MIXES), so the top is taken over a read-heavier mix too (review 2026-10-12, P2)."""
    runs = {mix: project(c0, steps[mix], requests, output=OUTPUT_TOKENS["high"], max_output_tokens=max_output_tokens,
                         guard_tokens=guard_tokens, guard_bytes=guard_bytes, c0_bytes=c0_bytes, c0_images=c0_images)
            for mix in RANGE_MIXES}
    mix = max(runs, key=lambda m: (runs[m]["expected_micro_usd"], -RANGE_MIXES.index(m)))
    return {**runs[mix], "mix": mix,
            "required_cap_micro_usd": max(r["required_cap_micro_usd"] for r in runs.values())}


def image_pricing() -> dict:
    """The v3 image sizes, texts and request bytes the table prices at, and the guard it models (request_estimate
    refuses a table priced at others)."""
    return {"read_points": READ_POINTS_MAX, "pick_sheet_px": list(read_sheet_px()), "zoom_grids": ZOOM_MAX,
            "zoom_grid_px": list(zoom_grid_px()), "zoom_grid_kind": ZOOM_GRID_KIND, "zoom_text_chars": ZOOM_TEXT_CHARS,
            "variant_sheet_px": [SH.SHEET_W, SH.SHEET_H], "variant_arms": VARIANTS_MAX,
            "largest_priced": list(LARGEST_PRICED), "bytes_per_image_token": BYTES_PER_IMAGE_TOKEN,
            "text_bytes_per_char": TEXT_BYTES_PER_CHAR, "request_overhead_bytes": REQUEST_OVERHEAD_BYTES,
            "context_guard": CONTEXT_GUARD}


def rehearsal_samples(rehearsal: dict) -> list:
    """The rehearsal's tool_outputs, each facet_variants call with its "arms" (the arms it was asked for): as
    recorded, else from the report's steps and outputs (the variants body's items), else left out (priced_sample then
    scales its text from VARIANTS_MIN arms)."""
    samples = [dict(s) for s in rehearsal["tool_outputs"]]
    steps, outputs = rehearsal.get("steps") or [], rehearsal.get("outputs") or {}
    for k, s in enumerate(samples):
        if s["tool"] == "facet_variants" and not s.get("arms") and k < len(steps):
            items = (outputs.get(steps[k].get("step")) or {}).get("variants")
            if isinstance(items, list) and items:
                s["arms"] = len(items)
    return samples


def facet_estimate(rehearsal: dict, *, requests=RUN_LENGTHS, max_output_tokens: int = 16_384,
                   sheets: int = VARIANT_PHOTOS_MAX) -> dict:
    """The estimate table (estimate.json): runs of ``requests`` replies at each allowed effort, the typical mix at the
    expected output and range_top (the range); facet_variants priced at ``sheets`` sheets a call (main passes the
    rehearsal job's variant_sheets) of the largest page with VARIANTS_MAX arms of text, photo_read at READ_POINTS_MAX
    pick sheets and photo_compare at ZOOM_MAX zoom grids, each of the largest size the tool makes, facet_inspect at its
    largest recorded call (priced_samples, image_pricing); each run with its request bytes and images (project: the
    first request's recorded bytes, else the bytes model)."""
    first = rehearsal["first_request"]
    c0 = first_request_tokens(first["text_chars"], first["image_sizes"])
    c0_bytes = int(first["bytes"]) if first.get("bytes") else first_request_bytes(first["text_chars"],
                                                                                  first["image_sizes"])
    c0_images = len(first["image_sizes"])
    sized = {"c0_bytes": c0_bytes, "c0_images": c0_images}
    samples = rehearsal_samples(rehearsal)
    priced = priced_samples(samples, sheets)
    steps = {name: mix_step(priced, mix) for name, mix in MIXES.items()}
    rows = []
    for n in requests:
        for effort in EFFORTS:
            exp = project(c0, steps["typical"], n, output=OUTPUT_TOKENS["expected"],
                          max_output_tokens=max_output_tokens, **sized)
            high = range_top(c0, steps, n, max_output_tokens=max_output_tokens, **sized)
            out_share = Tariff.frozen().settle_micro({"input_tokens": 0, "output_tokens": OUTPUT_TOKENS["expected"] * n,
                                                      "total_tokens": OUTPUT_TOKENS["expected"] * n})
            rows.append({"requests": n, "effort": effort, "expected": exp, "high": high,
                         "suggested_cap_usd": usd_text(suggested_cap_micro(high["required_cap_micro_usd"])),
                         "output_micro_usd": out_share,
                         "text": f"{n} replies at effort {effort}: expected {cents_text(exp['expected_micro_usd'])}"
                                 f"{limit_text(exp)}, up to {cents_text(high['expected_micro_usd'])} "
                                 f"({high_text(high['mix'])}){limit_text(high)}"})
    per_tool = {}
    for s, p in zip(samples, priced):       # as recorded, with the tokens and bytes it is priced at
        t = call_tokens(p)
        per_tool.setdefault(s["tool"], []).append({"status": s["status"], "text_chars": s["text_chars"],
                                                   "args_chars": s["args_chars"],
                                                   "images": [[w, h, k] for w, h, k in s["images"]],
                                                   **({"arms": s["arms"]} if s.get("arms") else {}),
                                                   "tokens": round(t["tokens"]), "stubbable": round(t["stubbable"]),
                                                   "bytes": round(t["bytes"]),
                                                   **({"priced": False} if p.get("unpriced") else {})})
    sheet, grid = read_sheet_px(), zoom_grid_px()
    return {"schema": ESTIMATE_SCHEMA, "price_version": Tariff.frozen().price_version,
            "formula": "tokens = 0.42 x text chars + per image 0.975 x ceil(W/32) x ceil(H/32) + 112",
            "first_request": {**first, "tokens": c0, "request_bytes": c0_bytes,
                              "bytes_from": "recorded" if first.get("bytes") else "bytes model",
                              "v1_check": V1_FIRST_REQUEST},
            "output_tokens_per_request": OUTPUT_TOKENS, "output_tokens_source": OUTPUT_TOKENS_SOURCE,
            "variant_sheets": sheets, **image_pricing(), "mixes": MIXES, "range_mixes": list(RANGE_MIXES),
            "steps": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in steps.items()},
            "context_guard_tokens": CONTEXT_GUARD_TOKENS, "context_guard_bytes": CONTEXT_GUARD_BYTES,
            "long_context_tokens": LONG_CONTEXT_INPUT_TOKENS,
            "rows": rows, "tool_outputs": per_tool,
            "notes": ["No gpt-6-astra ledger at effort high or xhigh exists; both use the measured max numbers. "
                      "No effort discount is assumed; these projections are not guaranteed upper bounds. "
                      "output_micro_usd is the whole expected output cost.",
                      f"facet_variants is priced at {sheets} sheets a call of the largest page "
                      f"({SH.SHEET_W} x {SH.SHEET_H} px) and {VARIANTS_MAX} arms of text; facet_inspect at its largest "
                      "recorded call (its text grows with the stored readings); photo_read at "
                      f"{READ_POINTS_MAX} pick sheets of the largest size "
                      f"({sheet[0]} x {sheet[1]} px, 4 photos a reading), its text and arguments scaled alike, and a "
                      "call with no pick sheet (a drop) left out of its mean; photo_compare at its compare images "
                      f"plus {ZOOM_MAX} zoom grids of the largest size ({grid[0]} x {grid[1]} px) and "
                      f"{ZOOM_TEXT_CHARS} characters of text each (tool_outputs lists the calls as recorded, at their "
                      "priced tokens).",
                      f"A zoom grid (the lab's kind 'compare' with zoom) is recorded as kind {ZOOM_GRID_KIND!r} "
                      f"(record_kind): a call's own grids are replaced by the {ZOOM_MAX} priced; a call recorded "
                      f"with its grids as 'compare' images is priced up to {ZOOM_MAX} grids high.",
                      f"The context guard replaces images of kinds {', '.join(STUBBED_KINDS)} in old results "
                      "(lab.context_stubs: compare panels, zoom grids and variant sheets of a superseded model) and a "
                      "pick sheet once its reading was read again or dropped; the projection counts no pick sheet "
                      "as stubbable (a run that re-reads is priced a little high, never low).",
                      MIX_ASSUMED,
                      "A projected run whose next request would pass the model's input limit, or the provider's "
                      f"per-request limit ({PAYLOAD_LIMIT_BYTES / 1e6:.0f} MB, {PAYLOAD_LIMIT_IMAGES:,} images), ends "
                      "there (the budget guard never sends it): its cost is that of the requests before it, and its "
                      "row says so.",
                      "Request bytes are modelled (peak_request_mb, peak_images, peak_count_s): each image at its "
                      "kind's largest measured bytes per token (bytes_per_image_token, 121 real v3 tool images), "
                      f"text at {TEXT_BYTES_PER_CHAR} bytes a character and {REQUEST_OVERHEAD_BYTES:,} bytes a request "
                      f"(the paid runs' whole non-image growth); the context guard (filter {CONTEXT_GUARD}) fires once "
                      f"at {CONTEXT_GUARD_TOKENS:,} counted tokens and at k x {CONTEXT_GUARD_BYTES / 1e6:.0f} MB; the "
                      f"count's time is the paid runs' fit ({COUNT_S_BASE} s + {COUNT_S_PER_MB} s per MB, one network, "
                      "fitted up to 30.8 MB) beside its timeout (budget.count_timeout_s).",
                      "Output tokens per request are the v2 facet runs' means; a photo_read's arguments (up to "
                      f"{READ_POINTS_MAX} points, each up to 4 photos of two traced edges) are output not in them.",
                      "The cap is enforced by the guard request by request, independently of this forecast."]}


def request_estimate(table: dict, settings, chars: int, sizes, *, variant_sheets: int | None = None,
                     image_bytes: list | None = None) -> dict:
    """The approval request's estimate block: this package's first request and the run's turns (60 when the run has
    no reply cap) with the growth measured by the rehearsal (estimate.json). ``variant_sheets``: the most sheets one
    facet_variants call of this package returns (Lab.variant_photos_max); a table that priced fewer is refused, and so
    is one made for another tool mix or range (a v2 table has no photo_read) or at other image pricing
    (image_pricing). The text names the range top's mix and says the v3 mix is assumed (MIX_ASSUMED). The first
    request's bytes are the package's photos' own (``image_bytes``: each data URL's length; make_spec gives them),
    else the bytes model's; the run's guard rungs are the settings' (context_guard_tokens, guard_bytes)."""
    priced = table.get("variant_sheets")
    if variant_sheets is not None and (priced is None or int(priced) < int(variant_sheets)):
        raise ValueError(f"the estimate table prices facet_variants at {priced or 'the recorded'} sheets a call, this "
                         f"package's calls return up to {variant_sheets}: run estimate on a current rehearsal")
    pricing = image_pricing()
    if table.get("mixes") != MIXES or table.get("range_mixes") != list(RANGE_MIXES) or \
            {k: table.get(k) for k in pricing} != pricing:
        raise ValueError("the estimate table was made for another tool mix or image pricing (mixes "
                         f"{sorted(table.get('mixes') or {})}, read_points {table.get('read_points')}, zoom_grids "
                         f"{table.get('zoom_grids')}, pick sheet {table.get('pick_sheet_px')}, zoom grid "
                         f"{table.get('zoom_grid_px')}): run estimate on a current rehearsal")
    c0 = first_request_tokens(chars, sizes)
    sized = {"c0_bytes": first_request_bytes(chars, sizes, image_bytes), "c0_images": len(list(sizes)),
             "guard_tokens": settings.context_guard_tokens, "guard_bytes": settings.guard_bytes()}
    n = settings.max_turns or 60
    steps = {k: {kk: float(vv) for kk, vv in v.items()} for k, v in table["steps"].items()}
    exp = project(c0, steps["typical"], n, output=OUTPUT_TOKENS["expected"],
                  max_output_tokens=settings.max_output_tokens, **sized)
    high = range_top(c0, steps, n, max_output_tokens=settings.max_output_tokens, **sized)
    suggested = suggested_cap_micro(high["required_cap_micro_usd"])
    text = (f"Expected {cents_text(exp['expected_micro_usd'])}{limit_text(exp)} (up to "
            f"{cents_text(high['expected_micro_usd'])} with {high_text(high['mix'])}{limit_text(high)}) for {n} Astra "
            f"replies at effort {settings.effort}. " + MIX_ASSUMED)
    if settings.max_turns is None:
        text = "Illustrative 60-reply projection; this run has no reply cap. " + text
    return {"schema": ESTIMATE_SCHEMA, "first_request_tokens": c0, "first_request_bytes": sized["c0_bytes"],
            "requests": n, "effort": settings.effort,
            "expected_micro_usd": exp["expected_micro_usd"], "expected_usd": exp["expected_usd"],
            "range_usd": [exp["expected_usd"], high["expected_usd"]], "expected": exp, "high": high,
            "suggested_cap_micro_usd": suggested, "suggested_cap_usd": usd_text(suggested),
            "source": {"table": table.get("schema"), "steps": table["steps"], "variant_sheets": priced, **pricing,
                       "range_mixes": list(RANGE_MIXES), "output_tokens": OUTPUT_TOKENS,
                       "output_tokens_source": OUTPUT_TOKENS_SOURCE},
            "text": text, "limitation": "A forecast from the rehearsal's tool outputs and the fitted token formula; "
                                        "efforts high and xhigh are priced as max, without an assumed discount. " + MIX_ASSUMED + " The cap is enforced "
                                        "independently. No approval has been recorded."}


# --------------------------------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["prepare", "estimate", "request", "run"])
    p.add_argument("--job", type=Path)
    p.add_argument("--source-config", type=Path, help="explicit source/job facts JSON for prepare")
    p.add_argument("--rig", default=DEFAULT_RIG, choices=MATCHED_RIGS)
    p.add_argument("--rehearsal", type=Path, help="the T5 rehearsal.json (estimate)")
    p.add_argument("--out", type=Path, default=ESTIMATE, help="estimate.json to write (estimate)")
    p.add_argument("--estimate", type=Path, default=ESTIMATE, help="estimate.json to read (request)")
    p.add_argument("--run-id", default="facet-astra-001")
    p.add_argument("--effort", choices=EFFORTS)
    turns = p.add_mutually_exclusive_group()
    turns.add_argument("--turns", type=int)
    turns.add_argument("--no-turn-cap", action="store_true")
    p.add_argument("--cap")
    p.add_argument("--k", type=int, default=4, help="facet_finish: distinct compared heads (owner per run)")
    p.add_argument("--fit-output-to-budget", action="store_true",
                   help="Reduce the next response ceiling when needed to use the remaining cap without a fixed reserve")
    p.add_argument("--env-file", type=Path, help="Explicit local credential file; its contents are never logged")
    p.add_argument("--ignore-load", action="store_true",
                   help="run: start even while other Blender or pytest work runs on this machine (reported, not "
                        "refused)")
    a = p.parse_args(argv)
    try:
        if a.action == "prepare":
            if a.job is None or a.source_config is None:
                p.error("prepare requires --job and --source-config")
            result = prepare(a.job, rig=a.rig, **read(a.source_config))
        elif a.action == "estimate":
            if a.rehearsal is None:
                p.error("estimate requires --rehearsal")
            rehearsal = read(a.rehearsal)
            table = facet_estimate(rehearsal, sheets=rehearsal_variant_sheets(rehearsal))
            table["rehearsal"] = {"path": str(a.rehearsal), "sha256": sha(a.rehearsal)}
            write_json_atomic(a.out, table)
            result = {"status": "estimated", "path": str(a.out), "rows": [r["text"] for r in table["rows"]]}
        else:
            if a.job is None or a.effort is None or (a.turns is None and not a.no_turn_cap) or a.cap is None:
                p.error("request/run requires --job, --effort, --turns or --no-turn-cap, and --cap")
            settings = run_settings(effort=a.effort, max_turns=a.turns, fit_output_to_budget=a.fit_output_to_budget,
                                    finish_k=a.k)
            spec, lab = make_spec(a.job, a.run_id, settings, a.cap, write_request=a.action == "request",
                                  estimate_path=a.estimate)
            spec.env_file = a.env_file
            spec.load_check, spec.refuse_on_load = machine_load, not a.ignore_load
            result = {"status": "approval_request_ready", "request_sha256": spec.current_request_sha256,
                      "run_dir": str(Path(a.job).resolve() / "runs" / a.run_id), "approved": False} \
                if a.action == "request" else asyncio.run(run_astra(spec, scene=lab))
    except (A.ApprovalRefused, RunRefused) as exc:
        print(json.dumps({"status": "refused", "type": type(exc).__name__, "message": str(exc)}, indent=2))
        sys.exit(exc.exit_code)
    except ValueError as exc:      # a package or estimate-table refusal (check_package, request_estimate): nothing sent
        print(json.dumps({"status": "refused", "type": type(exc).__name__, "message": str(exc)}, indent=2))
        sys.exit(2)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
