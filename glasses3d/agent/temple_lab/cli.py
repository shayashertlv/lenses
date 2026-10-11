"""The facet lab's package and run entry points (plan/WP6_FACETS.md §4.3-4.4, §5). No paid call without the exact
approval guard of agent/loop.py; prepare, estimate and request are $0.

    prepare  --job DIR [--rig studio_dome_v1 | photo_env | catalog_band_v1]
        a new isolated package: inputs/ = a byte copy of the original scene, the target photos (checked against the
        reference manifest and the pose basis), the black photos (the same model in black acetate, sha-checked, each
        with its camera in the pose basis: B1 side, B2 angled, B3 front), any reference-only photos (none for
        Prada), brief.txt, instructions.txt and lab.json (g3d.temple_lab/2: the rig, every input's sha256, the photo
        list with each photo's role, the pose basis); basis/cameras.json = the pose basis restricted to the target
        and colourway photos (with their supplied mattes), imported as cameras/basis-NN (outside inputs/: every file
        under inputs/ is sha-bound and every image under inputs/ is sent in the first message); then Lab.prepare():
        the original compared at every target photo
        (only with --rig photo_env does this fit and freeze the job's light, ~150 s), revision 0 = template_from_mesh
        of the original's +X temple, applied and compared. job.json binds the implementation (every .py of g3d,
        g3d_host and agent, and instructions.txt), the rig and any frozen light. The default rig is the owner's choice
        of 2026-10-11, studio_dome_v1 ("c is definitely the best").
    estimate [--rehearsal FILE] [--out FILE]
        the facet-run cost table from the T5 rehearsal's real tool outputs (tests/proofs/astra_temple_lab.py), with
        facet_variants priced at the most sheets one call of the rehearsal's job can return (variant_sheets).
    request  --job DIR --run-id ID --effort high|max (--turns N | --no-turn-cap) --cap USD [--k 4]
        writes runs/<ID>/approval-request.json for a clean package (nothing approved, nothing sent).
    run      the same arguments [--env-file FILE]: the paid run, refused without the owner's approval of exactly that
        request.

The run settings are tools.run_settings: tool_choice required (kept after every call), final only on an accepted
facet_finish (K = --k distinct compared heads), the context guard at 240k counted input tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image

from glasses3d.agent import approvals as A
from glasses3d.agent.estimate import cents_text, suggested_cap_micro
from glasses3d.agent.loop import (RunRefused, RunSpec, ToolContract, contract_problems, instructions_sha256,
                                  run_astra, tool_registry_sha256)
from glasses3d.agent.pricing import LONG_CONTEXT_INPUT_TOKENS, Tariff, usd_text
from glasses3d.agent.seeing import CATALOG_RIG, ENV_RIG, MATCHED_RIG, MATCHED_RIGS, OFFSET_VIEWS
from glasses3d.agent.session_inputs import ReferenceInput
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host.camera import CAMERA_FIELDS, Camera, NormFrame, project_mm
from .lab import (LAB_SCHEMA, VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX, Lab, data_url, read, sha,
                  write_json_atomic)
from .state import OUTLINE_VIEW
from .tools import CONTEXT_GUARD_TOKENS, TOOL_NAMES, registry, run_settings

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
G3D = REPO / "glasses3d"
SOURCE_SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SOURCE_SCENE_SHA256 = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
REFERENCE = G3D / "data/reference/prada"
BASIS_JSON = G3D / "data/wp6v2/basis/cameras.json"
LAB_DATA = G3D / "data/wp6v2/lab"
ESTIMATE = LAB_DATA / "estimate.json"
DEFAULT_RIG = MATCHED_RIG          # the job rig: the owner's light choice of 2026-10-11 (plan/PROGRESS.md decisions)
K_TOKEN = "{K}"                     # instructions.txt states finish precondition 1 with the run's K
# The same model in black acetate (owner, 2026-10-11, approving the run after facet-astra-002 with the black photos
# included: "yes, make the fixes and run it again"; and "yes, build 1-3": calibrated cameras for B2 and B3). Each is a
# pose basis image (colourway variant, sha256-pinned bytes) whose camera was transported from the target photo of the
# same pose by an image similarity (tests/proofs/wp6v2_basis.py, g3d_host/camera_similarity.py): B1 from 05, B2 from
# 02, B3 from 04. photo_compare shows each beside the gloss_black render. No Prada photo is reference only now; a
# job whose extra photo has no camera passes it as a reference photo ({id, view, colour, source, path, sha256}:
# photo_image only). colour and the optional finish are job data: the brief and the photo labels print them (the
# instructions name no product's colour or material). The three black photos are glossy (their facets show as
# mirrored studio lights; review images 2026-10-11).
BLACK, GLOSSY = "black", "glossy"
COLOURWAY_PHOTOS = ({"id": "B1", "image": "black-side.png", "colour": BLACK, "finish": GLOSSY,
                     "sha256": "6eddabbb7bd40888801eaa40964795a278d1daaa4f10986f6b5f86a6e26c19be"},
                    {"id": "B2", "image": "black-angled.webp", "colour": BLACK, "finish": GLOSSY,
                     "sha256": "5705c2cab03f5e457d2b1dfc58b5155dec5ac03c3450e7672511a10aeb4ac8f1"},
                    {"id": "B3", "image": "black-front.webp", "colour": BLACK, "finish": GLOSSY,
                     "sha256": "e89d19f4101d6457a32274dc4a1b8b51a0de525ef09d2179b8657eb6c34621c3"})
REFERENCE_PHOTOS = ()

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
# gpt-6-astra ledger at effort high exists: high is priced with the max numbers, an upper bound.
OUTPUT_TOKENS = {"expected": 1852, "high": 2064}
OUTPUT_TOKENS_SOURCE = ("mean output tokens per request of the paid facet runs at effort max (budget.json): "
                        "facet-astra-002 1635 (33 requests), -003 2064 (15), -004 1852 (32); expected = run 4, "
                        "high = the highest run mean (run 3)")
# Tool calls per 10 requests. typical: the loop the instructions describe (edit and compare, now and then variants,
# a closer look, an inspect); edit_heavy: every request an applied facet_edit with its default compare.
MIXES = {"typical": {"facet_edit": 5, "facet_variants": 1, "photo_compare": 2, "photo_image": 1, "facet_inspect": 1},
         "edit_heavy": {"facet_edit": 10}}
STUBBED_KINDS = ("compare", "variants")         # the image kinds the context guard may replace (lab.context_stubs)
STUB_CHARS = 120                                 # one stub text
GUARD_KEEP_RESULTS = 3                           # the latest 2 compare / variant results and best_checkpoint's
RUN_LENGTHS = (40, 60, 90)
ESTIMATE_SCHEMA = "g3d.facet_estimate/1"


# --------------------------------------------------------------------------------------------- package
def implementation():
    files = sorted(HERE.glob("*.py")) + [HERE / "instructions.txt", REPO / "glasses3d/g3d/worker.py"]
    # Bind all construction/worker/seeing code too; an approved request must not silently change implementation.
    for folder in (REPO / "glasses3d/g3d", REPO / "glasses3d/g3d_host", REPO / "glasses3d/agent"):
        files.extend(folder.rglob("*.py"))
    return {str(p.relative_to(REPO)): sha(p) for p in sorted(set(files))}


# --------------------------------------------------------------------------------------------- brief
# The brief carries the job's facts; instructions.txt is general (plan/WP6_FACETS.md §4.4, amended 2026-10-11). The
# photo facts come from the job's pose basis (cameras.json records) and are information, never a rule. Directions are
# temple-relative: one spec drives both temples as mirror images, so the -X temple seen from (yaw, pitch) is the
# charted (+X) temple seen from (-yaw, pitch).
SAME_DIRECTION_DEG = 10.0       # cameras whose temple-relative directions are closer than this are one direction
SQUARE_ON_DEG = 30.0            # under this angle to the outer face's normal a view is square-on: 1 mm of depth error
#                                 moves a points_px reading at most tan 30 deg = 0.58 mm along the face
EDGE_ON_DEG = 75.0              # from this angle on the face shows at most cos 75 deg = 0.26 of its width: edge-on
# photo_compare's depths reads t from a pixel (lab.depth_reading). The brief gives each view how finely it reads t:
# image px per mm of t (pose.line_depth's px_per_mm) at a point T_PX_BACK_MM behind the hinge of the temple whose
# outer face the camera sees, unposed (about mid-temple: run 5's own reading of node B, d 47, in B2 was 7.87 px/mm,
# the brief's figure for B2 8.1). Square-on views read t at about a quarter of that: unreliable.
T_PX_BACK_MM = 50.0
ALONG_DEG = 25.0                # a camera within this of the temple's length (Z) looks along front-to-back creases
# OFFSET_VIEWS (imported from agent/seeing.py): the view kinds whose compares print outer-edge offsets.
# The job's render light, which the brief names (instructions.txt: "the job's fixed light (the brief names it)");
# g3d/render.py describes each rig.
RIG_TEXT = {MATCHED_RIG: "a studio light, one large soft key 55 deg above the horizon on the camera's side, a bright "
                         "sky from just above the horizon, a dark band just below it and a mid-grey ground",
            ENV_RIG: "a light fitted once to this job's photos on the front of the frame (the temples held out)",
            CATALOG_RIG: "a catalogue light, a bright band at the horizon over a dark ground and under a dimmer sky, "
                         "the same all round"}


def toward(cam: dict) -> tuple:
    """The unit vector from the model toward a camera (g3d_host/camera.py: (cos p sin y, sin p, cos p cos y); +X is
    the charted temple's side, +Y up, +Z the front)."""
    yaw, pitch = math.radians(float(cam.get("yaw") or 0.0)), math.radians(float(cam.get("pitch") or 0.0))
    return math.cos(pitch) * math.sin(yaw), math.sin(pitch), math.cos(pitch) * math.cos(yaw)


def outer_face_deg(cam: dict) -> float:
    """The angle between a camera's view and the normal (+-X) of the outer face of the temple it sees best: 0 is
    square-on, 90 edge-on. 1 mm of depth error moves a points_px reading tan(angle) mm along that face."""
    return math.degrees(math.acos(min(1.0, abs(toward(cam)[0]))))


def t_px_per_mm(rec: dict, model: dict | None) -> float | None:
    """Image px per mm of t in a photo (how finely photo_compare's depths reads t there): the projected length of
    1 mm along the outer-face normal (+-X, the temple the camera sees) at T_PX_BACK_MM behind that temple's hinge
    (the record's hinge_axis_xz_mm, else the model's front pivot) at the front pivot's height, through the record's
    camera in the pose basis's NormFrame (``model``: the cameras.json model record, frame and front_pivot_mm). None
    without a full camera or that model record."""
    cam = (rec or {}).get("camera") or {}
    if not model or not model.get("frame") or model.get("front_pivot_mm") is None or \
            any(cam.get(k) is None for k in CAMERA_FIELDS):
        return None
    side = "+1" if toward(cam)[0] > 0 else "-1"
    pivot = np.asarray(model["front_pivot_mm"], float)
    hinge = ((rec.get("hinge_axis_xz_mm") or {}).get(side) or [pivot[0], pivot[2]])
    P = np.array([float(hinge[0]), pivot[1], float(hinge[1]) - T_PX_BACK_MM])
    n = np.array([1.0 if side == "+1" else -1.0, 0.0, 0.0])
    q = project_mm(np.stack([P - 0.5 * n, P + 0.5 * n]), Camera.from_dict(cam), NormFrame.from_dict(model["frame"]))
    return float(np.hypot(*(q[1] - q[0])))


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


def camera_fit_text(rec: dict, ids: dict) -> str | None:
    """How the photo's camera was made and how well it fits, from its pose-basis record: a camera moved from another
    photo's by an image similarity (``transport``: photo to photo) with, when the basis made one, its check against
    the starting model (``model_check``: full and temple-region IoU, tests/proofs/wp6v2_basis.py), or a camera with
    the model's silhouette checked against the photo (``iou``, ``contour_mean_px``)."""
    t = rec.get("transport") or {}
    if t:
        src = ids.get(Path(str(t.get("source") or "")).name) or "another photo"
        nums = [f"IoU {float(t['mask_iou_after_warp']):.3f}" if t.get("mask_iou_after_warp") is not None else None,
                f"mean contour {float(t['mean_bidirectional_px']):.1f} px"
                if t.get("mean_bidirectional_px") is not None else None]
        nums = ", ".join(n for n in nums if n)
        copied = not any((rec.get("theta_observed") or {}).values())
        mc = rec.get("model_check") or {}
        check = "not checked against the model"
        if mc.get("iou") is not None:
            check = (f"starting model's silhouette on the compare's raster: IoU {float(mc['iou']):.3f}"
                     + (f", temple IoU {float(mc['temple_iou']):.3f}" if mc.get("temple_iou") is not None else ""))
        return (f"camera moved from {src}'s by matching the two photos' silhouettes"
                + (f" (photo to photo, on its registration matte: {nums})" if nums else "")
                + (f", the temples' hinge opening as in {src}" if copied else "")
                + f"; {check}")
    if rec.get("iou") is not None:
        c = rec.get("contour_mean_px")
        return (f"camera fitted to the model's silhouette (all-views fit): IoU {float(rec['iou']):.3f}"
                + (f", mean contour {float(c):.1f} px" if c is not None else ""))
    return None


def depth_text(a: float, px: float | None) -> str:
    """How finely photo_compare's depths reads t in a view at ``a`` deg to the outer face (``px``: t_px_per_mm, None
    when unknown): measured at a moderate angle, unreliable square-on."""
    if angle_band(a) == "square-on":
        return "reading t with depths is unreliable here" + (f": about {px:.1f} px per mm" if px is not None else "")
    return f"depths reads t at about {px:.1f} px per mm" if px is not None else "depths reads t here"


def photo_line(p: dict, rec: dict | None, ids: dict, model: dict | None = None) -> str:
    """One photo of the brief: its view (and colour), then from its pose-basis record: the camera direction, the
    temple it sees and how (with how finely depths reads t there: t_px_per_mm with ``model``, the pose basis's model
    record), faces it sees from above or below, the number lines its compares print and the camera's fit."""
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
        bits.append(f"{whose} {angle_band(a)} ({a:.0f} deg; {depth_text(a, t_px_per_mm(rec, model))})")
    else:
        bits.append(f"the outer faces edge-on ({a:.0f} deg)")
    if 90.0 - abs(pitch) < EDGE_ON_DEG:
        bits.append(f"faces that point straight {'up' if pitch > 0 else 'down'} at {90.0 - abs(pitch):.0f} deg from "
                    "square-on")
    numbers = (["outer-edge offsets"] if p["view"] in OFFSET_VIEWS else []) + \
        (["the outline line"] if not other and p["view"] == OUTLINE_VIEW else [])
    if numbers:
        bits.append("its compares print " + " and ".join(numbers))
    fit = camera_fit_text(rec, ids)
    if fit:
        bits.append(fit)
    return head + "; " + "; ".join(bits)


def brief_text(product: str, photos: list, cameras: dict | None = None, *, rig: str = DEFAULT_RIG,
               roll_mm: float | None = None, model: dict | None = None) -> str:
    """The brief (the job's facts; instructions.txt is general): the target photos and any of another colour (role
    colourway: a camera, photo_compare; role reference: photo_image only), the job's render light (``rig``) and the
    two temples' names, one line per photo from its pose-basis record in ``cameras`` ({image file name: cameras.json
    record}; the photo row's path names the file; ``model``: the pose basis's model record, for how finely depths
    reads t in each view), then the photos grouped by direction, by how they see the outer face, the views that look
    along front-to-back creases, the number lines and the finishes (listed only for the photo rows that give one),
    revision 0 and the real temples' edge rounding (``roll_mm`` when the job knows it, else "not known")."""
    cameras = cameras or {}
    targets = [p for p in photos if p.get("role", "target") == "target"]
    others = [p for p in photos if p.get("role", "target") != "target"]
    ids = {Path(p["path"]).name: p["id"] for p in photos if p.get("role") != "reference"}
    recs = {p["id"]: cameras.get(Path(p["path"]).name) if p.get("role") != "reference" else None for p in photos}
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
    lines = [first, light, "The charted temple is the one the spec's d, y and t chart; the mirrored temple is its "
             "mirror image on the other side; compares print outer-edge offsets per temple under these names.",
             "", "Each photo: its view; the camera's yaw (0 from the front, +90 from the charted temple's side, "
             "-90 from the mirrored temple's, 180 from the rear) and pitch (+ from above); how it sees the temple's "
             "outer face (0 deg square-on, 90 deg edge-on) and how finely photo_compare's depths reads t there; its "
             "number lines; its camera's fit."]
    lines += [photo_line(p, recs[p["id"]], ids, model) for p in targets + others]
    cams = {pid: rec["camera"] for pid, rec in recs.items() if rec and rec.get("camera")}
    if cams:
        def listed(ids_):
            return ", ".join(ids_) if ids_ else "none"
        band = {b: [pid for pid, c in cams.items() if angle_band(outer_face_deg(c)) == b]
                for b in ("square-on", "at a moderate angle", "edge-on")}
        groups = " | ".join(", ".join(g) for g in direction_groups(cams))
        lines += ["", f"Directions (cameras within {SAME_DIRECTION_DEG:g} deg of each other count as one, and so do "
                      f"the side views of the two temples): {groups}.",
                  f"The temple's outer face square-on (under {SQUARE_ON_DEG:g} deg): {listed(band['square-on'])}. At "
                  f"a moderate angle ({SQUARE_ON_DEG:g} to {EDGE_ON_DEG:g} deg): {listed(band['at a moderate angle'])}"
                  f". Edge-on ({EDGE_ON_DEG:g} deg or more): {listed(band['edge-on'])}.",
                  "Front and rear views look along creases that run front to back: "
                  f"{listed([pid for pid, c in cams.items() if looks_along_deg(c) < ALONG_DEG])} (cameras within "
                  f"{ALONG_DEG:g} deg of the temple's length)."]
        offsets = [p["id"] for p in targets + others if p["id"] in cams and p["view"] in OFFSET_VIEWS]
        outline = [p["id"] for p in targets if p["id"] in cams and p["view"] == OUTLINE_VIEW]
        lines.append(f"Outer-edge offsets: {listed(offsets)}. Outline line: {listed(outline)}.")
    finishes = sorted({p["finish"] for p in photos if p.get("finish")})
    for f in finishes:
        lines.append(f"{f.capitalize()} photos: {', '.join(p['id'] for p in photos if p.get('finish') == f)}.")
    unstated = [p["id"] for p in photos if not p.get("finish")]
    if finishes and unstated:      # a photo without a finish is not matte: the job data only names none for it
        lines.append(f"No finish is given for {', '.join(unstated)}.")
    edge = (f"The real temples' edge rounding is about {float(roll_mm):g} mm (roll_mm)." if roll_mm is not None
            else "The real temples' edge rounding (roll_mm) is not known: judge it from the photos' edges.")
    lines += ["", "The scene holds revision 0, a template fitted to the starting model's own temples: no facets, no "
                  "creases, roll_mm 0, and at every d an outer face straight in y (the base wedge). " + edge
              + " Make the temples look like the temples in these photos."]
    return "\n".join(lines) + "\n"


def prepare(root, *, rig: str = DEFAULT_RIG, source_scene=SOURCE_SCENE, scene_sha256=SOURCE_SCENE_SHA256,
            reference=REFERENCE, basis_json=BASIS_JSON, lab_class=Lab, import_basis=C.import_cameras_json,
            colourway_photos=COLOURWAY_PHOTOS, reference_photos=REFERENCE_PHOTOS) -> dict:
    """A new package (module doc). $0; ~1 min with studio_dome_v1 (the default), ~4 min with photo_env (its light
    fit). ``colourway_photos`` name pose-basis images of another colourway ({id, image, colour, finish?, sha256}:
    compared with photo_compare), ``reference_photos`` images without a camera ({id, view, colour, finish?, source,
    path, sha256}); colour and finish are printed by the brief and the photo labels."""
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("use a new empty package directory")
    if rig not in MATCHED_RIGS:
        raise ValueError(f"rig must be one of {MATCHED_RIGS}")
    inputs = root / "inputs"
    (inputs / "images").mkdir(parents=True)
    assets = []

    def copy(src, rel, want=None):
        dst = inputs / rel
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
    targets = {n: r for n, r in doc["images"].items() if r.get("colourway") == "target"}
    if not targets:
        raise ValueError("the pose basis has no target photo")
    photos, paths = [], {}
    for name in sorted(targets):
        row, rec = manifest["files"].get(name), targets[name]
        if row is None or row["sha256"] != rec["image_sha256"]:
            raise ValueError(f"{name}: not in the reference manifest, or the manifest and the basis disagree")
        paths[name] = copy(reference / name, f"images/{name}", row["sha256"])
        with Image.open(paths[name]) as im:
            size = list(im.size)
        photos.append({"id": Path(name).stem.split("-", 1)[1], "path": f"images/{name}", "view": rec["view_kind"],
                       "size_px": size, "sha256": row["sha256"], "role": "target",
                       **({"note": side_note(rec)} if rec["view_kind"] == "side" else {})})
    bdir = root / "basis"
    bdir.mkdir()
    variants = {}
    for want in colourway_photos:
        name = want["image"]
        rec = doc["images"].get(name)
        if rec is None or rec.get("colourway") in (None, "target") or rec.get("image_sha256") != want["sha256"]:
            raise ValueError(f"{name}: not a colourway image of the pose basis with the expected sha256")
        paths[name] = copy(rec["image"], f"images/{name}", want["sha256"])
        if rec.get("matte_mode") == "supplied":              # the pose basis's own matte, copied beside cameras.json
            dst = bdir / rec["matte_file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(basis_json).parent / rec["matte_file"], dst)
            if sha(dst) != rec.get("matte_file_sha256"):
                raise ValueError(f"{name}: the supplied matte does not have the expected sha256")
        variants[name] = rec
        with Image.open(paths[name]) as im:
            size = list(im.size)
        photos.append({"id": want["id"], "path": f"images/{name}", "view": rec["view_kind"], "size_px": size,
                       "sha256": want["sha256"], "role": "colourway", "colour": want["colour"],
                       **({"finish": want["finish"]} if want.get("finish") else {}),
                       "basis_ref": C.ref_id(name), "source": str(rec["image"]),
                       **({"note": side_note(rec)} if rec["view_kind"] == "side" else {})})
    for want in reference_photos:
        dst = copy(want["source"], want["path"], want["sha256"])
        with Image.open(dst) as im:
            size = list(im.size)
        photos.append({"id": want["id"], "path": want["path"], "view": want["view"], "size_px": size,
                       "sha256": want["sha256"], "role": "reference", "colour": want["colour"],
                       **({"finish": want["finish"]} if want.get("finish") else {}), "source": str(want["source"])})
    ids = [p["id"] for p in photos]
    if len(set(ids)) != len(ids):
        raise ValueError(f"photo ids must be distinct: {ids}")
    (inputs / "brief.txt").write_text(brief_text(manifest.get("name") or reference.name, photos, doc["images"],
                                                 rig=rig, model=doc.get("model")), encoding="utf-8")
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
    lab = lab_class(root)
    try:
        prep = lab.prepare()
        if not lab.is_clean():
            raise ValueError("the prepared package is not clean")
        env = environment(root, basis.id) if rig == ENV_RIG else None
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
    return {"status": "prepared", "path": str(root), "rig": rig, "photos": len(photos),
            "checkpoint": exit_checkpoint["id"], "revision0": rev0["sha256"][:8], "paid_calls": 0}


def basis_files(root: Path, basis_id: str) -> dict:
    """sha256 of the pose basis files a package binds (the source copy outside inputs/ with any supplied matte, and
    the frozen basis)."""
    root = Path(root)
    files = ["basis/cameras.json", f"{C.CAMERAS_DIR}/{basis_id}/cameras.json", f"{C.CAMERAS_DIR}/{basis_id}/basis.json"]
    files += sorted(rel for rel in (p.relative_to(root).as_posix() for p in (root / "basis").rglob("*")
                                    if p.is_file()) if rel not in files)
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
    """Every photo of the package, in lab.json order (the target photos first): the first message's images."""
    rows = lab.manifest["photos"]
    targets = [r for r in rows if r.get("role", "target") == "target"]
    others = [r for r in rows if r.get("role", "target") != "target"]
    out = []
    for group, rows_ in (("target", targets), ("other", others)):
        for i, row in enumerate(rows_, 1):
            p = lab.root / "inputs" / row["path"]
            with Image.open(p) as im:
                size = im.size
            if group == "target":
                label = f"TARGET PHOTO {row['id']} ({i} of {len(rows_)})"
            else:
                label = (f"{(row.get('colour') or 'other colour').upper()} PHOTO {row['id']} ({i} of {len(rows_)}; "
                         f"the same model in {colour_text(row)}, "
                         + ("calibrated camera)" if row["role"] == "colourway" else "reference only, no camera)"))
            out.append(ReferenceInput(label=label, path=row["path"], image_url=data_url(p), detail="high",
                                      view=row["view"], size_px=size))
    return out


def make_spec(root, run_id, settings, cap, *, write_request=False, lab=None, estimate_path=ESTIMATE, estimate=None):
    """The RunSpec of a paid run on a clean package (and, with write_request, its approval request with the facet
    estimate from estimate_path, or the given ``estimate`` block; nothing is approved here)."""
    root = Path(root).resolve()
    lab = lab or Lab(root)
    check_package(root, lab)
    tools = registry(lab, settings)
    instructions = instructions_text(root, settings.finish_k)
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    photos = references(lab)
    contract = ToolContract(frozenset(TOOL_NAMES))
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
            variant_sheets=lab.variant_photos_max())
        A.write_approval_request(run_dir, job_id=root.name, request=request, estimate=cost, suggested_cap_usd=cap)
        # the exact text sent (its sha256 is the request's instructions_sha256): LF, never the platform's newline
        (run_dir / "instructions-sent.txt").write_text(instructions, encoding="utf-8", newline="\n")
    note = f"The scene holds revision 0 at checkpoint {lab.s.head['id']}; facet_inspect shows it."
    spec = RunSpec(job_dir=root, run_id=run_id, instructions=instructions, tools=tools, brief=brief, photos=photos,
                   current_request_sha256=A.request_sha256(request), settings=settings, requested_cap_usd=cap,
                   scene_note=note, contract=contract)
    return spec, lab


# --------------------------------------------------------------------------------------------- estimate
def image_tokens(w: int, h: int) -> float:
    return TOKENS_PER_PATCH * math.ceil(w / PATCH_PX) * math.ceil(h / PATCH_PX) + TOKENS_PER_IMAGE


def c0_chars(instructions: str, brief: str, tools, photos) -> int:
    """Text characters of a first request: instructions, brief, tool schemas and one descriptor line per photo."""
    schemas = json.dumps([{"name": t.name, "description": t.description, "parameters": t.params_json_schema}
                          for t in tools], separators=(",", ":"))
    return len(instructions) + len(brief) + len(schemas) + 160 * len(photos) + 400


def first_request_tokens(chars: int, sizes) -> int:
    return math.ceil(TOKENS_PER_CHAR * chars + sum(image_tokens(w, h) for w, h in sizes))


def call_tokens(sample: dict) -> dict:
    """One recorded tool call: its growth of the context (arguments and output text, every image) and the part in
    images the context guard may replace."""
    imgs = [image_tokens(w, h) for w, h, _kind in sample["images"]]
    stub = [t for t, (_w, _h, kind) in zip(imgs, sample["images"]) if kind in STUBBED_KINDS]
    return {"tokens": TOKENS_PER_CHAR * (sample["text_chars"] + sample["args_chars"]) + sum(imgs),
            "stubbable": sum(stub), "stub_images": len(stub)}


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


def priced_sample(sample: dict, sheets: int) -> dict:
    """A recorded facet_variants call priced at ``sheets`` sheets: when it returned fewer, its largest sheet is
    repeated (an upper bound: a 4th photo of another colour adds one sheet the size of the others, lab.py module
    doc). Every other call is returned unchanged."""
    imgs = [list(i) for i in sample["images"]]
    shown = [i for i in imgs if i[2] == "variants"]
    if sample["tool"] != "facet_variants" or not shown or len(shown) >= sheets:
        return sample
    big = max(shown, key=lambda i: image_tokens(i[0], i[1]))
    return {**sample, "images": imgs + [list(big)] * (sheets - len(shown))}


def mix_step(samples: list, mix: dict) -> dict:
    """The mean growth per request of a call mix, from the rehearsal's successful calls of each tool."""
    by_tool: dict = {}
    for s in samples:
        if s["status"] in ("ok", "applied", "accepted"):
            by_tool.setdefault(s["tool"], []).append(call_tokens(s))
    total = sum(mix.values())
    step = {"tokens": 0.0, "stubbable": 0.0, "results": 0.0, "stub_images": 0.0}
    for tool, weight in mix.items():
        rows = by_tool.get(tool)
        if not rows:
            raise ValueError(f"the rehearsal has no successful {tool} call to price")
        share = weight / total
        for key in ("tokens", "stubbable", "stub_images"):
            step[key] += share * sum(r[key] for r in rows) / len(rows)
        step["results"] += share * sum(1 for r in rows if r["stub_images"]) / len(rows)
    return step


def project(c0: int, step: dict, requests: int, *, output: int, max_output_tokens: int,
            guard_tokens: int | None = CONTEXT_GUARD_TOKENS, tariff: Tariff | None = None) -> dict:
    """One modelled run, request by request with the frozen tariff (the 272k step included): request i's input is
    the previous input (cached) plus the mean growth (written); once a counted input reaches the guard, the next
    request replaces the images of all but GUARD_KEEP_RESULTS compare / variant results by stubs and is written
    afresh after the brief."""
    tariff = tariff or Tariff.frozen()
    prev, spent, need = None, 0, 0
    results, armed = 0.0, False
    guard_at = long_at = None
    peak = 0
    per_result = step["stubbable"] / step["results"] if step["results"] else 0.0
    imgs_per_result = step["stub_images"] / step["results"] if step["results"] else 0.0
    for i in range(requests):
        if prev is None:
            inp, cached = c0, 0
        else:
            new, cached = prev + step["tokens"], prev
            if armed:
                drop = max(0.0, results - GUARD_KEEP_RESULTS)
                new -= drop * (per_result - imgs_per_result * TOKENS_PER_CHAR * STUB_CHARS)
                cached, armed, guard_at = c0, False, i + 1
            inp = math.ceil(new)
            results += step["results"]
        cached = min(cached, inp)
        usage = {"input_tokens": inp, "output_tokens": output, "total_tokens": inp + output,
                 "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": inp - cached},
                 "output_tokens_details": {"reasoning_tokens": 0}}
        settled, hold = tariff.settle_micro(usage), tariff.reserve_micro(inp, max_output_tokens)
        need = max(need, spent + hold)
        spent += settled
        peak = max(peak, inp)
        if long_at is None and inp > LONG_CONTEXT_INPUT_TOKENS:
            long_at = i + 1
        if guard_tokens is not None and guard_at is None and not armed and inp >= guard_tokens:
            armed = True
        prev = inp
    return {"requests": requests, "expected_micro_usd": spent, "expected_usd": usd_text(spent),
            "required_cap_micro_usd": need, "last_input_tokens": prev, "peak_input_tokens": peak,
            "context_guard_request": guard_at, "first_long_context_request": long_at}


def facet_estimate(rehearsal: dict, *, requests=RUN_LENGTHS, max_output_tokens: int = 16_384,
                   sheets: int = VARIANT_PHOTOS_MAX) -> dict:
    """The estimate table (estimate.json): runs of ``requests`` replies at effort high and max, the typical mix at the
    expected output and the edit-heavy mix at the high output (the range); facet_variants priced at ``sheets`` sheets
    a call (priced_sample; main passes the rehearsal job's variant_sheets)."""
    first = rehearsal["first_request"]
    c0 = first_request_tokens(first["text_chars"], first["image_sizes"])
    samples = rehearsal["tool_outputs"]
    steps = {name: mix_step([priced_sample(s, sheets) for s in samples], mix) for name, mix in MIXES.items()}
    rows = []
    for n in requests:
        for effort in ("high", "max"):
            exp = project(c0, steps["typical"], n, output=OUTPUT_TOKENS["expected"],
                          max_output_tokens=max_output_tokens)
            high = project(c0, steps["edit_heavy"], n, output=OUTPUT_TOKENS["high"],
                           max_output_tokens=max_output_tokens)
            out_share = Tariff.frozen().settle_micro({"input_tokens": 0, "output_tokens": OUTPUT_TOKENS["expected"] * n,
                                                      "total_tokens": OUTPUT_TOKENS["expected"] * n})
            rows.append({"requests": n, "effort": effort, "expected": exp, "high": high,
                         "suggested_cap_usd": usd_text(suggested_cap_micro(high["required_cap_micro_usd"])),
                         "output_micro_usd": out_share,
                         "text": f"{n} replies at effort {effort}: expected {cents_text(exp['expected_micro_usd'])}, "
                                 f"up to {cents_text(high['expected_micro_usd'])} (every reply an edit)"})
    per_tool = {}
    for s in samples:                       # as recorded, with the tokens it is priced at
        t = call_tokens(priced_sample(s, sheets))
        per_tool.setdefault(s["tool"], []).append({"status": s["status"], "text_chars": s["text_chars"],
                                                   "args_chars": s["args_chars"],
                                                   "images": [[w, h, k] for w, h, k in s["images"]],
                                                   "tokens": round(t["tokens"]), "stubbable": round(t["stubbable"])})
    return {"schema": ESTIMATE_SCHEMA, "price_version": Tariff.frozen().price_version,
            "formula": "tokens = 0.42 x text chars + per image 0.975 x ceil(W/32) x ceil(H/32) + 112",
            "first_request": {**first, "tokens": c0, "v1_check": V1_FIRST_REQUEST},
            "output_tokens_per_request": OUTPUT_TOKENS, "output_tokens_source": OUTPUT_TOKENS_SOURCE,
            "variant_sheets": sheets, "mixes": MIXES,
            "steps": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in steps.items()},
            "context_guard_tokens": CONTEXT_GUARD_TOKENS, "long_context_tokens": LONG_CONTEXT_INPUT_TOKENS,
            "rows": rows, "tool_outputs": per_tool,
            "notes": ["No gpt-6-astra ledger at effort high exists; high is priced with the max numbers (an upper "
                      "bound). output_micro_usd is the whole expected output cost, the most effort high can save.",
                      f"facet_variants is priced at {sheets} sheets a call: a recorded call with fewer sheets has "
                      "its largest sheet repeated (tool_outputs lists the calls as recorded, at their priced tokens).",
                      "The cap is enforced by the guard request by request, independently of this forecast."]}


def request_estimate(table: dict, settings, chars: int, sizes, *, variant_sheets: int | None = None) -> dict:
    """The approval request's estimate block: this package's first request and the run's turns (60 when the run has
    no reply cap) with the growth measured by the rehearsal (estimate.json). ``variant_sheets``: the most sheets one
    facet_variants call of this package returns (Lab.variant_photos_max); a table that priced fewer is refused."""
    priced = table.get("variant_sheets")
    if variant_sheets is not None and (priced is None or int(priced) < int(variant_sheets)):
        raise ValueError(f"the estimate table prices facet_variants at {priced or 'the recorded'} sheets a call, this "
                         f"package's calls return up to {variant_sheets}: run estimate on a current rehearsal")
    c0 = first_request_tokens(chars, sizes)
    n = settings.max_turns or 60
    steps = {k: {kk: float(vv) for kk, vv in v.items()} for k, v in table["steps"].items()}
    exp = project(c0, steps["typical"], n, output=OUTPUT_TOKENS["expected"],
                  max_output_tokens=settings.max_output_tokens, guard_tokens=settings.context_guard_tokens)
    high = project(c0, steps["edit_heavy"], n, output=OUTPUT_TOKENS["high"],
                   max_output_tokens=settings.max_output_tokens, guard_tokens=settings.context_guard_tokens)
    suggested = suggested_cap_micro(high["required_cap_micro_usd"])
    text = (f"Expected {cents_text(exp['expected_micro_usd'])} (up to {cents_text(high['expected_micro_usd'])} if "
            f"every reply is an edit) for {n} Astra replies at effort {settings.effort}.")
    if settings.max_turns is None:
        text = "Illustrative 60-reply projection; this run has no reply cap. " + text
    return {"schema": ESTIMATE_SCHEMA, "first_request_tokens": c0, "requests": n, "effort": settings.effort,
            "expected_micro_usd": exp["expected_micro_usd"], "expected_usd": exp["expected_usd"],
            "range_usd": [exp["expected_usd"], high["expected_usd"]], "expected": exp, "high": high,
            "suggested_cap_micro_usd": suggested, "suggested_cap_usd": usd_text(suggested),
            "source": {"table": table.get("schema"), "steps": table["steps"], "variant_sheets": priced,
                       "output_tokens": OUTPUT_TOKENS, "output_tokens_source": OUTPUT_TOKENS_SOURCE},
            "text": text, "limitation": "A forecast from the rehearsal's tool outputs and the fitted token formula; "
                                        "effort high is priced as max. The cap is enforced independently. No "
                                        "approval has been recorded."}


# --------------------------------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["prepare", "estimate", "request", "run"])
    p.add_argument("--job", type=Path)
    p.add_argument("--rig", default=DEFAULT_RIG, choices=MATCHED_RIGS)
    p.add_argument("--rehearsal", type=Path, help="the T5 rehearsal.json (estimate)")
    p.add_argument("--out", type=Path, default=ESTIMATE, help="estimate.json to write (estimate)")
    p.add_argument("--estimate", type=Path, default=ESTIMATE, help="estimate.json to read (request)")
    p.add_argument("--run-id", default="facet-astra-001")
    p.add_argument("--effort", choices=["high", "max"])
    turns = p.add_mutually_exclusive_group()
    turns.add_argument("--turns", type=int)
    turns.add_argument("--no-turn-cap", action="store_true")
    p.add_argument("--cap")
    p.add_argument("--k", type=int, default=4, help="facet_finish: distinct compared heads (owner per run)")
    p.add_argument("--fit-output-to-budget", action="store_true",
                   help="Reduce the next response ceiling when needed to use the remaining cap without a fixed reserve")
    p.add_argument("--env-file", type=Path, help="Explicit local credential file; its contents are never logged")
    a = p.parse_args(argv)
    try:
        if a.action == "prepare":
            if a.job is None:
                p.error("prepare requires --job")
            result = prepare(a.job, rig=a.rig)
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
            result = {"status": "approval_request_ready", "request_sha256": spec.current_request_sha256,
                      "run_dir": str(Path(a.job).resolve() / "runs" / a.run_id), "approved": False} \
                if a.action == "request" else asyncio.run(run_astra(spec, scene=lab))
    except (A.ApprovalRefused, RunRefused) as exc:
        print(json.dumps({"status": "refused", "type": type(exc).__name__, "message": str(exc)}, indent=2))
        sys.exit(exc.exit_code)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
