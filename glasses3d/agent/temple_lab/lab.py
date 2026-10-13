"""The facet lab, WP6 v3 (plan/WP6_V3.md §6; the plumbing v3 keeps is WP6_FACETS.md §4): an isolated copy of a
product scene, immutable revisions (a DESIGN and its FIT), named readings, and the facet tools' host side. Astra never
writes code: every edit is an edit list on a revision's design (geometry.apply_edits), fitted to its readings by the
host (g3d/temple/fit.py), built by the numpy core (g3d.temple) and applied in Blender through the lab-only worker
methods (temple_lab_build, mutating; temple_variants_render, read-only); every look is the photo-matched compare
(agent/seeing.py, the job's rig) with the model's own creases drawn by identity (build.model_edges, WP6_V3 §5).

Why (data/wp6v2/review_20261012/FINDINGS.md): the real top face is not one plane, an exact-planar builder put that
misfit wherever the newest rule said, corners were "solved" from another face's extrapolated plane, refusals offered
one remedy that Astra took literally, and the top / rear offset tables, the "start" column, the outline line and
one-photo depths were read as shape. v3: readings are soft and the host fits every face jointly (P1), a face may warp
(P2), tips are crossings of traced edges (P3), compares draw the build's creases and zoom at native scale (P4), and
host texts are facts with every way out or none (P5, P6).

Job layout (``root``):
    inputs/lab.json      {schema g3d.temple_lab/2, rig (a seeing.MATCHED_RIGS name), assets [{path, sha256}],
                         photos [...]} (written by cli.prepare); inputs/scene.blend = the original's byte copy
    cameras/basis-K      the frozen pose basis (compare.import_cameras_json; target photos reference-01..06)
    checkpoints/, lab_calls/, work/temple_lab/{state.json, revisions/rNNNN.json (the design),
    revisions/rNNNN.fit.json (its fit), readings/ (pick sheets), variants/, images/}, work/compare, ...

Revisions: a revision is a DESIGN (a g3d.temple/2 spec whose node t may be a reading {"read": {...}}: geometry.
apply_edits sets it from a stored reading, fit.read_payload) and its FIT (fit.fit_spec(design, readings=the stored
readings): ``revisions/rNNNN.fit.json`` = {schema, design_sha256, spec_sha256, fit: FitResult.to_dict()}, whose
``fit.spec`` is the pure /2 spec the builder and the worker build). ``Lab.design(rid)`` is the design, ``Lab.spec(rid)``
the fitted spec (a revision stored before v3 has no fit file: its design is its spec), ``Lab.build(rid)`` builds the
fitted spec (cached), ``Lab.fit_result(rid)`` the stored FitResult. A revision is keyed by its design's sha256
(geometry.design_sha256: spec.canonical of the design with each reading kept), so re-reading a point makes a new
design; a pure /2 spec keeps its spec sha. Revision 0 is the template of the original's +X temple (no facets),
through the fit.

``Lab(root).prepare()`` (once, $0): start the worker on the original (checkpoint c0000), compare the ORIGINAL at every
target photo (this fixes the job's start model, gains and, for photo_env, its environment), take revision 0 =
template_from_mesh of the original's +X temple body (with the decal and hinge footprints), fit and apply it, and
compare revision 0 at every target photo. ``Lab(root).start()`` reopens the head for a run.

Photo ids (lab.json ``photos``, each with a role; rows without one are target photos):
    target      "01".."06" = the basis' reference-01..06 (target colourway): photo_image, photo_read, photo_compare
                (the default set, native_linear rows), facet_variants (native_linear panels);
    colourway   a basis image of another colourway with a calibrated camera (``basis_ref``; the Prada job's B1, B2, B3
                = black-side, black-angled, black-front): photo_image, photo_read, photo_compare (photo /
                gloss_black render / overlay, no native row: seeing's variant-colourway compare, one row order for
                every colour) and facet_variants (gloss_black panels with that compare's gloss gain and stretch);
                never the default set;
    reference   an image without a camera under inputs/ (``path``, ``sha256``): photo_image only.
``compare_ids`` = target + colourway (the photos with a camera); ``image_ids`` = all of them.

Tool methods (each returns a dict for tools.py; "images" lists image files, never sent as paths; WP6_V3 §6):
    inspect()                                   head: design and fitted value per node (t, X, source, residual mm
                                                and sd against its reading or typed value, a solved or base node's
                                                NO_RESIDUAL words instead, fitted_at = the corner where the joint fit
                                                moved it along its reading; a read node whose reading was replaced or
                                                dropped since says so: geometry.stale_reads), the readings table (0.01
                                                mm; model_t "outside the model's outline" where it has none), each
                                                stored reading no node is named for against its nearest node
                                                (unused_readings: jointly in d, y, X), the object
                                                note (and, when it was written for another layout, which), the fit
                                                report, the creases, the chart (no section strip, no lean, no inherited
                                                warnings)
    photo(photo, crop, enhance, scale)          a photo or a native crop (contrast stretch; up to 3 x for a small crop)
    read(call_id, points, drop)                 named readings (readings.read_point on the head's build): stored,
                                                replaced or dropped by name (state "readings", history
                                                "reading_log"); each with its text and pick sheet; a replaced one's
                                                change (reading_change); the head's nodes that still hold a reading
                                                it replaced or dropped (head_nodes); a new reading no node of the head
                                                is named for, whose nearest node holds a reading (no_node:
                                                unused_readings)
    compare(call_id, photos, crop, zoom, points)  the photo-matched compare of the head with its model edges, zooms
                                                at native scale -> compare_id
    edit(call_id, parent_revision, reset_layout, edits, judgement, note, object, apply, photos)
                                                apply_edits -> fit -> build (-> apply -> compare)
    variants(call_id, parent_revision, variants, photos, judgement)
                                                arms built and fitted as edit's, rendered beside the head
    finish(call_id, status, best_checkpoint, summary, judgement, k)
SceneHooks for agent/loop.py: start, close, journal_entries, restore, exit_checkpoint, worker_info, context_stubs.

facet_edit (WP6_V3 §6): geometry.apply_edits(parent design, edits, readings=the stored readings) -> a design (E_EDIT
with the edit's index); the object note: a call without one is refused E_OBJECT when no note exists yet or when the
latest note was written for another layout (facets and node names) than the design's (state.check_object: going
back to an older revision or applying a variant arm is a facet_edit, so the latest note decides, not the parent); a
note given is recorded with the design's layout (state.record_object), also when the fit or the build then refuses;
fit.fit_spec (stage "fit": E_FIT_SHAPE, E_FIT; stage "spec": the parse's E_SHORT_EDGE, E_SPEC...: their facts and
fields, an E_SHORT_EDGE node's outline_facts); build_temple of the fitted spec (any builder refusal: its facts, nothing
added); the revision; with apply, the worker builds the fitted spec on both temples from
the original and the head is compared. A fit or build that raises anything but a TempleError is this call's refusal
(E_FIT / E_BUILD, the exception's type and text, kept in state "host_errors"), never the SDK's generic error. Every
action tool (facet_edit, facet_variants, facet_finish) records its judgement first (_judged) and states what became of
it: "judgement" = state.judgement_not_due's fact when none was due (not stored), or "judgement of cmpX stored" on a
refusal. Receipt, in this order: what the edits did beyond the diff, on lines of their own (geometry.apply_edits
facts, taken out of ``notes``): ``deleted_creases`` ("deleted crease declarations: L|U ridge, RU|U ridge (with facet U,
edits[0]); declared again in this edit list: ..."; the 2026-10-13 blind stand-in: "crease L|U deleted with facet U"
among the notes, which a model that skips the notes loses) and ``outline_bends`` (each bend a set_node outline knot
makes in its edge, in mm off the edge's line and degrees of turn: the stand-in's E knot made a 0.97 mm, 111° notch
that "outline.bottom has its knot at node E" did not show); then the fit report
(FitResult.report_lines without its crease lines: the fit and misfit lines, faces, fit warnings such as
W_FACE_NOT_FLAT, W_FIT_SOLVED, W_FIT_PRIOR and W_FIT_OFF_READING "typed E 6.50: its reading 5.27 +- 0.40 (+3.1 sd)"),
then one line per built crease (``crease_lines``: the builder's shape, angle and range beside a warp, soft, step with
its d, its declaration, a two-way share with its model edges' signed extremes, a facet|base border's strips of plain
arm between the facet's straight side and the curved outline (geometry.outline_strips), then the fit's angle with its
sd and certain / open, a declaration's cost; at most CREASE_LINE_MAX besides the strips, the fit's angle, sd and
certain / open always kept),
then the builder's warnings (never one revision 0 already had: ``inherited_changed`` below; a W_BORDER_RAMP run a
crease line prints, left out; W_ARM_NORMAL_SPREAD with its place, arm_spread_at), ``inherited_changed``, the thickness
in mm, border ramps in the crease line's words (ramp_text), the spec's knot snaps as a sentence, then against the
CURRENT HEAD: ``solved`` (fitted
nodes with no reading or typed value whose t moved SOLVED_MOVE_MM or more), ``crease_vs_head`` (creases built with
the other shape; a solved corner of that edge with a stored reading off it says so: solved_corners_text),
``edges_vs_head`` (creases that appear or go flat: edge_changes) and ``diff_vs_head`` (when the parent is not the
head); before them ``unused_readings`` when they changed against the parent's (unused_readings) and ``notes`` (the
outline knots apply_edits moved with a corner and the read corners set beyond the outline, geometry module doc).
There is no margins block (the fit's sd and certain / open replace it) and no remedy text anywhere: Astra took a
refusal's listed remedy in 12 of 14 refusals.

photo_compare (WP6_V3 §5, §6): ``photos`` (null = seeing's default set, the target photos whose temple shows at least
TEMPLE_ROI_MIN_PX: the result's "left_out" names the others; [] with a zoom = zooms only), ``crop``, ``zoom`` [{photo,
box [x0, y0, x1, y1] native px, geometry}] (<= ZOOM_MAX), ``points`` (charted on the photos shown in full; points on
another photo are named in "points_not_charted"; with photos null, refused: the set is chosen at compare time and
seeing refuses a point beyond it); seeing gets the head's model edges
(``model_edges``: build.model_edges of the head's build, by identity, at most seeing.MODEL_EDGES_MAX runs), its
hinge_z and opening, passes native_linear (a photo of another colour is gloss_black only, seeing's rule), and
``photo_names`` (every photo with a camera by the lab's id: the images' titles and seeing's texts say "02", "B1",
never the basis names "reference-02", "black-side"). Rows, for every colour: photo, render, overlay. Per photo
the text is the lab's (``_photo_text``): view (a colourway photo: its colour and rows), temple IoU, px/mm, the crop
box and its scale, a side view's outline offsets for each temple whose outer face it sees (seeing's own text: in a
view from above the bottom outline as measured and the top face's far edge minus its median, the only number on the
arm's top there; from below the other way round), the render's backdrop share from BACKDROP_SHOWN (a face clipped
to the backdrop colour is not a missing face), the charted points_px. No top / rear offsets, no "start" column, no
outline line, no camera note (FINDINGS §3). Each zoom's text is seeing's, which names the lab's photo id (with a
photo of another colour's colour in place of seeing's "variant"). The
compare record's ``photos`` are every photo shown (stacked and zoomed: the judgement covers them), ``stacked`` the
full compares (variant sheets need one).

facet_variants (WP6_V3 §6): each arm is fitted and built as facet_edit's (a refusal: its stage, code and facts);
built arms are revisions; each item carries vs_head_mm (the surface or outline) and creases_vs_head_mm (its ridges'
and valleys' distance from the head's: crease_shift), its fit lines (short: ``fit_brief``: the off-reading and not-flat
warnings with their first clause, the solved moves against the head), creases (a 0.5 mm border step with its ramp),
crease_vs_head and edges_vs_head, and its notes; a refused arm its stage, code, facts and outline_facts; an arm whose
design is the head's or an earlier arm's says ``same_as`` and is not rendered again, and one equal to the head is no
rendered variant (finish preconditions 2 and 5). ONE sheet per photo (sheets.variant_sheet; the cost estimate
prices one a photo, cli.variant_sheets; session2's 3-arm call on 3 photos returned 8 pages, v3p3-clean-lab): the crop
is the charted temple where the arms
differ from the head (_variant_region; the temple whose outer face the camera sees), every panel at scale
sheets.variant_scale for the models drawn (<= 2: the largest at which the photo pair and every model drawn fit the one
sheet); a photo pair (the photo | the photo with the head's edges) and per model drawn a CLEAN render panel beside (or
above) the LINED one (the photo's outline green and that model's own model edges, labelled with their angles,
depth-tested against the head's scene: ``_variant_edges``); an arm refused or equal to another model has no panels,
its facts are in the text and the notes line names it. Run 6's fade was chosen on a sheet at 3.46 px/mm where the
lines covered 83-96 % of the shading differences (scorecard #29): the clean panel is beside the lined one, and the
sheet's title gives k.

The scene lock (``Lab.lock``) serialises every use of the worker: tools.py takes it with timeout 0 for each tool call
(E_BUSY when held), and exit_checkpoint / close wait for it (a call the SDK timed out may still be running). A call the
SDK timed out is ``mark_unseen``: a compare it still makes is recorded unseen and never demands a judgement. Compares of
a tool call render under that tool's hard timeout (loop.hard_timeout_s), never seeing's longer default. The state
lock (``Lab.state_lock``, re-entrant) guards every mutation of ``Lab.state`` and every save; it is held only for the
mutation or the write, never across a fit, build or render.

``Lab.distinct(ra, rb)`` is the one head-distinctness rule (geometry.distinct_heads of the two BUILT temples, cached
per pair) for finish preconditions 1 and 5.

Warnings revision 0 already has (its core warnings and the Blender layer's lines at prepare, e.g. W_HINGE_LEAF,
W_ARM_NORMAL_SPREAD; state "inherited"; the same words, numbers aside: inherited_ref) are never news in a later receipt
(WP6_V3 §6): left out while their numbers stay within MATERIAL_ABS / MATERIAL_REL of revision 0's, and once they move
beyond it listed under ``inherited_changed`` (the receipt's, or the Blender layer's) with their place and revision 0's
numbers ("W_ARM_NORMAL_SPREAD: ... up to 49.9 deg (> 30.0) at d 136.0 y -22.8 (over 30° only at d 136.0); revision 0
had it with 61.5": session2 step 11 printed it as news, with no d, because the tip edit IMPROVED it).

Removed in v3: photo_compare depths and one-photo depth readings, facet_restore, the crease margins block, solve
options, relief_rise, refusal_solved, conditioning hints and crease-run notes, the outline line, the start column, the
section strip, lean_deg. Their code is gone too (v3-final: crease_margins, margins_summary and the MARGIN_* constants
had no reader once the T5 rehearsal was rewritten for v3; the outline-line functions of state.py, the section strip,
fades and the original_chart.npz write followed on 2026-10-13; v2's one-photo / multi-photo corner_reading lives on
only as the independent reference tests/agent/test_temple_readings.py checks readings.read_point against).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
from PIL import Image

from glasses3d.agent.journal import BlenderSession, WorkerFailed
from glasses3d.agent.loop import SceneHooks, hard_timeout_s
from glasses3d.agent.procs import write_json_atomic
from glasses3d.agent.seeing import (EDGE_POINTS_MAX, MATCHED_RIGS, MODEL_EDGES_MAX, TEMPLE_ROI_MIN_PX, ZOOM_MAX,
                                    ZOOM_MIN_PX, Seeing)
from glasses3d.g3d.temple import build as BLD
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import draw as DR
from glasses3d.g3d.temple import fit as FIT
from glasses3d.g3d.temple import report as RP
from glasses3d.g3d.temple.spec import (ARM_NORMAL_SPREAD_DEG, BORDER_RAMP_WARN_DEG, SHARP_DIHEDRAL_DEG, TempleError,
                                       plain, spec_sha256)
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import project_mm
from . import geometry as G
from . import readings as RD
from . import sheets as SH
from . import state as S
from .state import LabRefusal

LAB_SCHEMA = "g3d.temple_lab/2"
FIT_RECORD_SCHEMA = "g3d.temple_lab_fit/1"
COMPARE_PASSES = ("native_linear",)       # target photos' rows (WP6_V3 §5); another colour is gloss_black (seeing)
VARIANTS_MIN, VARIANTS_MAX = 2, 4     # arms a facet_variants call (v3-final, was 6: ONE sheet per photo holds the
#                                       head and every arm, sheets.variant_scale; session2's B1 box with 3 / 4 / 6
#                                       arms: k 0.43 / 0.38 / 0.37, 3.65 / 3.2 / 3.1 px/mm; fewer, larger panels and
#                                       less arm text a call)
VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX = 4, 3     # facet_variants photos (WP6_FACETS §4.1: 3 targets + 1 black)
CREASE_THREADS = max(1, min(8, (os.cpu_count() or 2) // 2))   # host model-edge runs: one task per photo and model
NOTE_MAX, SUMMARY_MAX = 1000, 2000
CHART_W = 1024
BUILD_DEADLINE_S = hard_timeout_s("facet_edit")    # a temple_lab_build still running then is killed (worker restart)
LINE_MAX = 170                   # a receipt line (the core's W_* messages fit it with 24-character names)
CREASE_LINE_MAX = 260            # a crease line: the builder's part and the fit's (crease_lines; a facet|base line with
#                                  a range, a ramp, a two-way share, a declaration and a bound reaches 310: the bound
#                                  goes, review2-lab-e2-2 P3-8)
REFUSAL_MAX = 400                # a builder refusal's message (the core's longest are about 250)
REFUSAL_FIELDS = ("facet", "node", "nodes", "pair", "point", "d", "y", "facets", "refs", "at")
#                                  a refusal's fields kept in its result (a build refusal: the first 8)
FIT_REFUSAL_MAX = 1200           # a fit refusal's message: E_FIT_SHAPE names per costly crease its readings-only
#                                  angle, cost and the 3 readings most against it (about 200 characters a crease)
FIT_LINES_MAX = 12               # the fit report's lines in a receipt (FitResult.report_lines' limit)
BRIEF_FIT_LINES = ("fit:", "camera turn of", "corners the fit placed", "corners held", "kept at the design",
                   "misfit", "at the thickness bound")
#                                  a variant arm's fit lines (fit_brief): the fit, the camera turns the data moved (the
#                                  misfit line then says "after the camera turn", which named a turn no arm line
#                                  printed), where it put read corners, misfits, the nodes held at t_min (v3p3 e2e
#                                  P3-5: an arm held there read like a free fit)
BRIEF_FACT_CODES = ("W_FIT_OFF_READING", "W_FACE_NOT_FLAT")   # fit_brief keeps these warnings' first clause ...
BRIEF_FACT_MAX = 220             # ... in at most this many characters (W_FACE_NOT_FLAT's chi2 and the four corners'
#                                  deviations from the others' plane: about 210 on session2's crown)
BUILD_CACHE = 24
CONTEXT_KEEP_LATEST = 2          # the context guard keeps the images of the latest 2 compare / variants results
STUB_KINDS = ("compare", "variants")     # the image kinds context_stubs replaces in an old result (cli.STUBBED_KINDS;
#                                          a pick sheet, kind "reading", only once its reading was read again or
#                                          dropped)
BACKDROP_SHOWN = 0.10            # a compare's photo text names the render's backdrop share from this (seeing's: 52 % of
#                                  the model's pixels at 03 clipped to the backdrop colour, review2-lab-e2-2 P3-2)
LOCK_WAIT_S = 600.0              # exit_checkpoint / close wait this long for a tool call still holding the scene
MATERIAL_ABS, MATERIAL_REL = 0.1, 0.1     # an inherited warning shows again when a number moves more than this
RAMP_ITEMS_MAX = 8               # border-ramp runs listed per receipt (then "+N more")
RAMP_BRIEF_MM = 0.5              # a variant arm's crease string adds a facet|base step this high (_crease_brief)
SOLVED_MOVE_MM = 0.1             # a fitted node with no reading or typed value is listed when its t moved this much
SOLVED_ITEMS_MAX = 6             # ... listed per receipt (then "+N more")
UNUSED_ITEMS_MAX = 8             # stored readings no node is named for, listed against their nearest node (then "+N")
UNUSED_NEAR_MM = 3.0             # ... compared jointly with that node's reading when the two lie this close in (d, y)
#                                  (2.4 x the largest corner sd_d read, the tip E's 1.24 mm; session2's A2 to A: 1.06)
NO_RESIDUAL = {"solve": "set by the fit; no reading", "base": "on the base surface; no reading"}
#                                  facet_inspect's residual_mm of a node with no reading or typed value (its
#                                  residual_sd null)
MIXED_SHARE = 0.05               # a crease line names both shares when ridge and valley each fold this much of it
VARIANT_DIFF_MM = 0.05           # facet_variants crop: an arm differs from the head where its outer surface X moves
#                                  this much (geometry.surface_grid's 0.5 mm lattice; a lattice point inside one
#                                  outline only differs too)
VARIANT_ZONE_MARGIN_MM = 4.0     # ... that (d, y) box widened by this on each side
VARIANT_SPAN_MIN_MM = 12.0       # ... and to at least this a side (about its centre): the panel shows a neighbourhood
VARIANT_PAD_PX = 8               # ... its projection padded by this many native px
VARIANT_BOX_MIN_PX = 16          # ... a smaller box (the temple off the photo) falls back to the compare's crop
COMPARE_ID_LINE_RE = re.compile(r"^compare_id \S+\.?$")   # seeing's last zoom-text line
NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")
RENDER_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")   # = g3d/render.py NAME_RE (that module imports bpy; a test pins it)


def render_safe_name(name: str) -> str:
    """g3d/render.py safe_name (without its refusal of a name with no usable character): the worker names a variant's
    render files <photo>--<safe_name(label)>, the lab looks them up by the label, so a label must be its own safe
    name (facet_variants refuses one ending in "_")."""
    return RENDER_NAME_RE.sub("_", str(name)).strip("._")[:80]


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def data_url(path) -> str:
    path = Path(path)
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            ".png": "image/png"}[path.suffix.lower()]
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _r(x, nd: int = 2):
    if isinstance(x, (list, tuple)):
        return [_r(v, nd) for v in x]
    return None if x is None else round(float(x), nd) + 0.0


def _close(a: float, b: float) -> bool:
    """Within the material tolerance of a revision-0 number b."""
    return abs(float(a) - float(b)) <= max(MATERIAL_ABS, MATERIAL_REL * abs(float(b)))


def same_warning(line: str, ref: str) -> bool:
    """The same warning as ``ref`` (same words, every number within the material tolerance of ref's)."""
    nums, refs = NUMBER_RE.findall(line), NUMBER_RE.findall(ref)
    return NUMBER_RE.sub("#", line) == NUMBER_RE.sub("#", ref) and len(nums) == len(refs) and \
        all(_close(a, b) for a, b in zip(nums, refs))


def inherited_ref(line: str, refs: list) -> str | None:
    """The revision-0 warning line (of ``refs``) that ``line`` repeats: the same words, numbers aside (NUMBER_RE), the
    nearest in its numbers when several share the words; None for a warning revision 0 did not have."""
    nums = [float(v) for v in NUMBER_RE.findall(line)]
    cands = [r for r in refs if NUMBER_RE.sub("#", r) == NUMBER_RE.sub("#", line)
             and len(NUMBER_RE.findall(r)) == len(nums)]
    return min(cands, key=lambda r: sum(abs(a - float(b)) for a, b in zip(nums, NUMBER_RE.findall(r))),
               default=None)


def inherited_note(line: str, ref: str) -> str:
    """"; revision 0 had it with 61.5": the numbers of revision 0's line where ``line``'s moved beyond the material
    tolerance (all of them if none did)."""
    pairs = list(zip(NUMBER_RE.findall(line), NUMBER_RE.findall(ref)))
    moved = [b for a, b in pairs if not _close(a, b)] or [b for _a, b in pairs]
    return f"; revision 0 had it with {', '.join(moved)}"


def brief(text: str, limit: int = REFUSAL_MAX) -> str:
    """text whole when it fits in limit characters, else cut at its last clause end ("; " or ". ") before the limit
    (at the last space when it has none) and marked " ...": never mid-word, never mid-clause when it has clauses."""
    if len(text) <= limit:
        return text
    cut = max(text.rfind("; ", 0, limit - 3), text.rfind(". ", 0, limit - 3))
    if cut <= 0:
        cut = text.rfind(" ", 0, limit - 3)
    return (text[:cut] if cut > 0 else text[:limit - 4]) + " ..."


def compose(parts: list, limit: int = CREASE_LINE_MAX) -> str:
    """The parts [(text, rank)] joined in their order in at most ``limit`` characters: whole when they fit, else the
    parts of the highest rank dropped first (the last of a rank first; rank 0 never) and " ..." added at the end; cut
    at the limit only when the rank-0 parts alone exceed it. A crease line's rank-0 parts are the builder's shape and
    angle and the fit's angle, sd and certain / open: a 170-character cut of the tail lost the fit's whole part, the
    one the prompt tells Astra to act on (review2-lab-e2-2 P3-8)."""
    keep, dropped = list(parts), False
    while len("".join(t for t, _r in keep)) + (4 if dropped else 0) > limit:
        ranked = [(r, i) for i, (_t, r) in enumerate(keep) if r > 0]
        if not ranked:
            break
        del keep[max(ranked)[1]]
        dropped = True
    return ("".join(t for t, _r in keep) + (" ..." if dropped else ""))[:limit]


def ramp_text(facet: str, height_mm: float, slope_deg: float, d_mm) -> str:
    """One border ramp in a crease line's step words (report.crease_line, crease_head): "shoulder proud 0.81 mm: a
    7.7° ramp at d 75.5-76.7" (proud of the base or sunk below it; the slope over the border band; d a point or a
    range)."""
    a, b = (f"{float(x):.1f}" for x in d_mm)
    side = "proud" if float(height_mm) > 0 else "sunk"
    return (f"{facet} {side} {abs(float(height_mm)):.2f} mm: a {float(slope_deg):.1f}° ramp at d "
            f"{a if a == b else f'{a}-{b}'}")


def ramp_line(warnings: list) -> str:
    """Every W_BORDER_RAMP of a build in one line (ramp_text each): "W_BORDER_RAMP (steeper than 8°): F4 sunk 0.42
    mm: a 12.3° ramp at d 40.0-55.0" (session2's form "F4 -0.42 12.3° d 40.0-55.0" had no unit or words)."""
    items = [ramp_text(w["facet"], w["height_mm"], w["slope_deg"], w["d_mm"]) for w in warnings[:RAMP_ITEMS_MAX]]
    more = f"; +{len(warnings) - RAMP_ITEMS_MAX} more" if len(warnings) > RAMP_ITEMS_MAX else ""
    return f"W_BORDER_RAMP (steeper than {BORDER_RAMP_WARN_DEG:g}°): " + "; ".join(items) + more


def fixed(x: float, nd: int) -> str:
    """x to nd decimals, rounded half up from its value to 0.001 (a recorded number's): one recorded number always
    prints one way (8.36499999 and 8.36500001 are both 8.365 -> "8.37"); never "-0.00" (= readings.fixed)."""
    q = Decimal(repr(round(float(x), 3))).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    return f"{abs(q) if q == 0 else q}"


# --------------------------------------------------------------------------------------------- receipt texts
def fit_crease_parts(f: dict, base: bool) -> list[tuple[str, int]]:
    """The fit's part of a crease line as compose parts [(text, rank)]: its angle at the longest piece's midpoint with
    the readings' sd ("border fit -2.3 +- 1.1°" for a facet|base pair: the fit's angle across the border, WP6_V3 §3;
    it read "fit fold", which collided with the prompt's fold bracket of two faces, v3p3 astra P3-9; "border fit" is
    2 characters longer, so the longest line keeps its two-way share within CREASE_LINE_MAX; rank 0), where that is
    (a facet|base pair or a pair sharing several pieces: a border turns, and its fold with it; the smoke test's
    triangle on the arm folded -6.2 along its long side and +5 along the two short ones, built "ridge" by length;
    rank 3), its state in the fit's own words (fit.STATE_TEXT of the crease's ``state``: certain = |angle| > 2 sd and
    > 2 deg in the readings-only fit; flat within 2 sd = |angle| + 2 sd < MIN_CREASE_DEG, so no declaration builds
    within the readings; else open: a declaration then sets it; rank 0; v3-integrate-2: the lab printed "open" for
    a "flat within 2 sd" crease, inviting a declaration the fit then refuses), held by a declaration (no sd: the
    declaration sets it), the bound it rests on (rank 4), and a
    declaration's readings-only angle and its chi2 cost when it costs (rank 1). The fit's own range along the edge is
    left out: the builder's range is printed (one range per crease, v3-integrate-1). Angles carry their unit ("fit
    +16.0 +- 2.7°"; session2's "fit +16.0 +- 2.7" beside the builder's "17.3°" had none; v3p3-clean-lab)."""
    what = "border fit" if base else "fit"
    if f.get("held"):
        at_min = abs(abs(float(f["deg"])) - FIT.MIN_CREASE_DEG) <= 0.01
        parts = [(f"; {what} {RD.signed(f['deg'], 1)}°" + (" (held at the declared minimum)" if at_min else
                                                          " (held by its declaration)"), 0)]
    else:
        parts = [(f"; {what} {RD.signed(f['deg'], 1)} +- {f['sd_deg']:.1f}°", 0)]
    if f.get("at") and (base or (f.get("borders") or 1) > 1):
        parts.append((f" at d {f['at'][0]:.1f} y {f['at'][1]:.1f}", 3))
    state = f.get("state") or ("certain" if f.get("certain") else "open")
    parts.append((f", {FIT.STATE_TEXT.get(state, state)}", 0))
    if f.get("bound"):
        parts.append((f", the thickness bound holds {', '.join(f['bound'][:3])} (the sd is without it)", 4))
    if f.get("declared") and (f.get("dchi2") is None or f["dchi2"] > FIT.COST_SHOWN) and f.get("free_deg") is not None:
        cost = "cannot hold" if f.get("dchi2") is None else f"cost chi2 +{f['dchi2']:.1f}"
        parts.append((f" (readings-only {RD.signed(f['free_deg'], 1)} +- {f['free_sd_deg']:.1f}°, {cost})", 1))
    return parts


def fit_crease_text(f: dict, base: bool) -> str:
    """The fit's part of a crease line whole (fit_crease_parts joined, without the leading "; ")."""
    return "".join(t for t, _r in fit_crease_parts(f, base))[2:]


def signed_extremes(edges) -> dict:
    """{pair: (lo, hi)} of build.model_edges runs: each pair's signed angle range over its runs (deg, + ridge)."""
    out: dict = {}
    for e in edges or []:
        lo, hi = (float(v) for v in (e.get("range") or (e["deg"], e["deg"])))
        a, b = out.get(e["pair"], (lo, hi))
        out[e["pair"]] = (min(a, lo), max(b, hi))
    return out


def mixed_text(c: dict, signed: tuple | None = None) -> str:
    """A built crease that folds both ways (a crease table record whose ridge and valley shares, over the edges its
    shape word votes on, are both at least MIXED_SHARE): ", ridge along 53 % and valley along 47 % of its 64.0 mm"
    (the table's shape is the length majority, its angle unsigned), with the signed extremes of its model edges when
    they fold both ways (``signed`` (lo, hi), signed_extremes): " (-4.4 to +4.7°)" (review2-lab-e2-1 P2-4: the
    table's "ridge 2.5 (1.1-4.7)" left out a -4.4 valley part that the fit's fold measured); "" otherwise."""
    L = float(c.get("face_length_mm") or c.get("length_mm") or 0.0)
    if L <= 0.0 or c.get("shape") not in ("ridge", "valley"):
        return ""
    if c.get("ridge_share") is not None and c.get("valley_share") is not None:
        r, v = float(c["ridge_share"]), float(c["valley_share"])
    else:
        r, v = float(c.get("ridge") or 0.0) / L, float(c.get("valley") or 0.0) / L
    if min(r, v) < MIXED_SHARE:
        return ""
    text = f", ridge along {100 * r:.0f} % and valley along {100 * v:.0f} % of its {L:.1f} mm"
    if signed is not None and signed[0] <= -0.05 and signed[1] >= 0.05:
        text += f" ({RD.signed(signed[0], 1)} to {RD.signed(signed[1], 1)}°)"
    return text


def step_text(st: dict) -> str:
    """report.crease_line's step clause of a crease table step (" (proud 0.80 mm: a 21.8° ramp)")."""
    side = "proud" if st["height_mm"] > 0 else "sunk"
    return f" ({side} {abs(st['height_mm']):.2f} mm: a {st['slope_deg']:.1f}° ramp)"


def crease_head(c: dict) -> str:
    """report.crease_line(c) with its step's d: "(proud 0.80 mm: a 21.8° ramp at d 75.5-76.8)": that W_BORDER_RAMP run
    is printed here only (_builder_warnings leaves it out; it was printed twice a receipt, review2-lab-e2-2 P3-10)."""
    line, st = RP.crease_line(c), c.get("step")
    if not st or not st.get("d_mm") or step_text(st) not in line:
        return line
    a, b = (f"{float(x):.1f}" for x in st["d_mm"])
    return line.replace(step_text(st), step_text(st)[:-1] + f" at d {a if a == b else f'{a}-{b}'})", 1)


def printed_steps(tb) -> set:
    """{(facet, d range to 0.1)} of the W_BORDER_RAMP runs crease_head prints in a crease line."""
    return {(c["regions"][0], tuple(_r(c["step"]["d_mm"], 1))) for c in tb.tables["creases"]
            if c["kind"] == "crease" and c.get("step") and c["step"].get("d_mm")
            and step_text(c["step"]) in RP.crease_line(c)}


def crease_lines(tb, fit=None, edges=None) -> list[str]:
    """One line per built crease (WP6_V3 §6): the builder's report.crease_line (pair, shape, length-weighted angle,
    its face-to-face range beside a warp, soft, the step of a facet|base W_BORDER_RAMP with its d: crease_head,
    declared or undeclared) and its two-way share with the signed extremes of its model edges (mixed_text; ``edges``
    = build.model_edges of tb), then the fit's part (fit_crease_parts) when ``fit`` (a FitResult) has that pair, then
    the corners of that edge the fit solved although a stored reading of their name lies off them (solved_corners_text:
    a declaration held at its minimum, or a "certain" angle, that rests on such a corner says so); at most
    CREASE_LINE_MAX characters (compose: the bound, the place, the share, the solved corners and a declaration's cost go
    in that order, never the angles, the sd or certain / open). A facet|base line names the strips of plain arm the
    facet leaves between a straight side and the curved outline after its head and share (geometry.outline_strips,
    strip_text; rank 0, on a budget of its own beside CREASE_LINE_MAX: the 2026-10-13 blind stand-in found that
    "RL|base valley 8.7° (sunk 1.62 mm: a 39.1° ramp at d 21.9-56.4)" was a strip of plain arm up to 4.9 mm wide under
    RL's chord C-E only by looking at the render)."""
    fits = {tuple(c["pair"]): c for c in (fit.creases if fit is not None else []) if c.get("deg") is not None}
    signed = signed_extremes(edges)
    strips = G.outline_strips(tb)
    out = []
    for c in tb.tables["creases"]:
        if c["kind"] != "crease":
            continue
        parts = [(crease_head(c), 0)]
        mixed = mixed_text(c, signed.get("|".join(c["regions"])))
        if mixed:
            parts.append((mixed, 2))
        strip = G.strip_text(c["regions"][0], strips.get(c["regions"][0], [])) if c["regions"][1] == "base" else ""
        if strip:                         # its own budget: the fit's part is never cut for it
            parts.append((f"; {strip}", 0))
        f = fits.get(tuple(c["regions"]))
        if f:
            parts += fit_crease_parts(f, c["regions"][1] == "base")
            solved = solved_corners_text(tb, fit, tuple(c["regions"]))
            if solved:                    # held at a declaration or "certain" on a corner the fit solved (P2-3)
                parts.append((solved, 1))
        out.append(compose(parts, CREASE_LINE_MAX + (len(strip) + 2 if strip else 0)))
    return out


def first_clause(message: str, limit: int = BRIEF_FACT_MAX) -> str:
    """A warning message's first clause: the text before its first top-level "; " (a "; " inside brackets belongs to
    the bracket's clause, which is closed: "(8.27 mm away; its X carried ...)" -> "(8.27 mm away)"), cut when longer
    than ``limit`` characters at its last ", " (else ": ", else a space) that fits, open brackets closed, " ..." marking
    the cut: never mid-number (fit.py's W_FACE_NOT_FLAT of 2026-10-12 has no ", " before its corner list)."""
    depth, cut = 0, len(message)
    for k, ch in enumerate(message):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif ch == ";" and message[k + 1:k + 2] == " ":
            if depth == 0:
                cut = k
                break
            text = message[:k]
            return first_clause(text + ")" * depth, limit)
    text = message[:cut]
    if len(text) <= limit:
        return text
    k_max = limit - 4 - text[:limit].count("(")        # head + its closing brackets + " ..." fit the limit
    for sep in (", ", ": ", " "):                       # never inside a number
        k = text.rfind(sep, 0, k_max + len(sep))
        if k > 0:
            break
    head = text[:k] if k > 0 else text[:limit - 4]
    return head + ")" * max(head.count("(") - head.count(")"), 0) + " ..."


def fit_brief(fit, before: dict | None = None) -> list[str]:
    """A variant arm's fit in a few lines: the fit line ("fit: chi2 ..., dof ..., from ... readings ..."), the misfit
    line when any reading or typed value lies beyond 1 sd, each W_FIT_OFF_READING and W_FACE_NOT_FLAT with the first
    clause of its message (BRIEF_FACT_CODES, first_clause: "W_FIT_OFF_READING: typed K 6.40: its reading 7.72 +- 0.29
    (-4.5 sd)"; one line per warning, so a node's value and position facts are both kept), the other warnings by code
    with their subjects, each (code, subject) once, and the solved nodes that moved against the head's fit
    (solved_moves; ``before`` = the head's fit nodes): facet_edit's "solved" for an arm (review2-lab-e2-2 P3-9: "K 6.53
    (head 7.72)" was printed by facet_edit, not by the bracket). Why the facts (v3p3 astra P2-2, verify-3): a bracket of
    a typed t is where its distance from the reading decides, and the arm read "W_FIT_OFF_READING K" where facet_edit
    said "-4.5 sd", "W_FIT_OFF_READING E" where it said "(8.27 mm away)", and "W_FACE_NOT_FLAT crown" with no chi2. A
    warning without a message (an older record) stays code and subject. The fit's camera and position lines are kept
    too (BRIEF_FIT_LINES, each with its wrapped continuation lines): the joint fit moves read corners along their
    readings (fit.py, v3p2-fit2-fix), and on the session2 tool test the flat-crown arm put the tip E 2.90 mm forward
    (2.8 sd) while its misfit line read only "E +0.41 mm (1.1 sd)" (v3-integrate-2); an arm's misfit line says "after
    the camera turn" only when the fit printed one, so that line goes with it (v3-final)."""
    lines = fit.report_lines(limit=99, creases=False)
    out, keep = [], False
    for ln in lines:
        if ln.startswith("  "):                              # a wrapped line continues the one before it
            if keep:
                out.append(ln)
            continue
        keep = ln.startswith(BRIEF_FIT_LINES)
        if keep:
            out.append(ln)
    by: dict = {}
    facts = []
    for w in fit.warnings:
        if w["code"] in BRIEF_FACT_CODES and w.get("message"):
            line = f"{w['code']}: {first_clause(str(w['message']))}"
            if line not in facts:
                facts.append(line)
            continue
        subj = str(w.get("face") or w.get("node") or "")
        if subj not in by.setdefault(w["code"], []):
            by[w["code"]].append(subj)
    if by:
        out.append("warnings: " + "; ".join(f"{code} {', '.join(s for s in subjects if s)}".strip()
                                            for code, subjects in by.items()))
    out += facts
    moved = solved_moves(fit, before) if before is not None else ""
    if moved:
        out.append(f"solved: {moved}")
    return out


def reading_change(was: dict, now: dict) -> str:
    """A replaced reading against the new one of its name (readings.read_point records), the old one first: "was X
    70.04 +- 0.21 at d 46.92 y 9.65 (read003-K); X +0.18 mm (+0.5 sd of the difference), d +0.08, y +0.05" (the
    prompt: two readings of one point that differ by more than their errors are different features; reading_log kept
    the old one unprinted, review2-lab-e2-2 P3-10). The difference's sd: the two pick parts in quadrature (separate
    picks) and per camera fit the difference of the two camera shifts (one theta per fit: a fit both readings use
    moves both, readings.py module doc); a record without those parts: the two sd_X in quadrature."""
    def num(rec, k, default=0.0):
        v = rec.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    dX, dd, dy = (num(now, k) - num(was, k) for k in ("X", "d", "y"))
    sa, sb = was.get("camera_shift"), now.get("camera_shift")
    if was.get("sd_X_pick") is not None and now.get("sd_X_pick") is not None and isinstance(sa, dict) \
            and isinstance(sb, dict):
        var = num(was, "sd_X_pick") ** 2 + num(now, "sd_X_pick") ** 2
        for f in set(sa) | set(sb):
            var += (float((sb.get(f) or [0.0, 0.0, 0.0])[2]) - float((sa.get(f) or [0.0, 0.0, 0.0])[2])) ** 2
    else:
        var = num(was, "sd_X") ** 2 + num(now, "sd_X") ** 2
    z = f" ({RD.signed(dX / math.sqrt(var), 1)} sd of the difference)" if var > 0.0 else ""
    return (f"was X {RD.fixed(num(was, 'X'), 2)} +- {RD.fixed(num(was, 'sd_X'), 2)} at d {RD.fixed(num(was, 'd'), 2)} "
            f"y {RD.fixed(num(was, 'y'), 2)} ({was.get('id') or was.get('name')}); X {RD.signed(dX, 2)} mm{z}, "
            f"d {RD.signed(dd, 2)}, y {RD.signed(dy, 2)}")


def outline_facts(design: dict, refs) -> list[str]:
    """Each node a refusal names (``refs`` items "node E", spec._ref_name's form) against the outline edge nearer to it
    at its d: "node E at d 76.75, y 9.36 lies 0.20 mm above the top edge, with no knot of it within 0.05 mm; set_node
    outline top puts that edge's knot at the node" (geometry.outline_edge's rule: within snap_mm it would lie on the
    edge). Facts with the way the tools offer beside the builder's own ("move a node at least snap_mm away"): v3p3 e2e
    P1-1, verify-0: the first Prada layout was refused E_SHORT_EDGE at node E, 0.20 mm off the top outline, and its one
    remedy, read as a move in d, looped until E_EDIT. A node outside the outline's d range, or not in the design, says
    nothing."""
    nodes = design.get("nodes") or {}
    snap = G.snap_of(design)
    out = []
    for ref in refs or []:
        n = str(ref)[5:] if str(ref).startswith("node ") else None
        if n not in nodes:
            continue
        d, y = float(nodes[n][0]), float(nodes[n][1])
        best = None
        for edge in G.OUTLINE_EDGES:
            P = np.asarray(design["outline"][edge], float)
            if P[0, 0] <= d <= P[-1, 0]:
                yv = float(np.interp(d, P[:, 0], P[:, 1]))
                if best is None or abs(y - yv) < abs(y - best[1]):
                    best = (edge, yv, P)
        if best is None:
            continue
        edge, yv, P = best
        knot = bool(np.any(np.hypot(P[:, 0] - d, P[:, 1] - y) <= snap))
        out.append(f"node {n} at d {d:.2f}, y {y:.2f} lies {abs(y - yv):.2f} mm {'above' if y > yv else 'below'} the "
                   f"{edge} edge" + ("" if knot else f", with no knot of it within {snap:g} mm") +
                   f"; set_node outline {edge} puts that edge's knot at the node")
    return out


def _payload_parts(p: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """((d, y, X), the 3 x 3 (d, y, X) pick covariance, {camera fit: (dd, dy, dX) per sd of its theta}) of a read t
    payload (fit.read_payload's "read": cov, else sd_d / sd_y / sd on the diagonal)."""
    v = np.array([float(p["d"]), float(p["y"]), float(p["X"])])
    if p.get("cov") is not None:
        C = np.array(p["cov"], float)
    else:
        C = np.diag([float(p.get("sd_d") or 0.0) ** 2, float(p.get("sd_y") or 0.0) ** 2, float(p["sd"]) ** 2])
    return v, C, {str(f): np.array(x, float) for f, x in (p.get("camera") or {}).items()}


def readings_apart(a: dict, b: dict) -> dict:
    """Two read t payloads (fit.read_payload's "read") compared jointly: the difference a - b in (d, y, X) mm, its
    covariance (the two pick parts, separate picks, plus per camera fit the difference of their camera shifts: one
    theta per fit moves both; reading_change's rule), each axis in sd of its own variance, chi2 over the three for 3
    dof (pinv: a reading without a (d, y) error has rank 1), and b's X at a's (d, y) by b's pick-part conditional
    (readings.conditional_x): {"diff", "z", "chi2", "x_at", "sd_at"}. Why jointly (v3p3 verify-7): A2 against A read
    X +0.77 mm (2.7 sd) alone, but the two differ by chi2 18.1 for 3 dof, almost all of it in d (-4.1 sd), and A's
    reading at A2's (d, y) gives X 69.23 +- 0.16, only 0.26 mm inside A2: an X-only fact was false precision."""
    va, Ca, ka = _payload_parts(a)
    vb, Cb, kb = _payload_parts(b)
    D = va - vb
    C = Ca + Cb
    for f in set(ka) | set(kb):
        s = ka.get(f, np.zeros(3)) - kb.get(f, np.zeros(3))
        C = C + np.outer(s, s)
    sd = np.sqrt(np.maximum(np.diag(C), 0.0))
    z = np.divide(D, sd, out=np.zeros(3), where=sd > 0.0)
    k = Cb[2, :2] @ np.linalg.pinv(Cb[:2, :2])
    return {"diff": D, "z": z, "chi2": float(D @ np.linalg.pinv(C) @ D),
            "x_at": float(vb[2] + k @ (va[:2] - vb[:2])), "sd_at": math.sqrt(max(float(Cb[2, 2] - k @ Cb[:2, 2]), 0.0))}


def unused_readings(design: dict, fit, readings: dict, *, near_read: bool = False) -> list[str]:
    """Each stored reading no node of the design is named for, against the design's node nearest to it in (d, y) as
    built (the fit's node position, else the design's), as facts with no remedy (v3p3 owner P1-2 -> P2/P3 by verify-7:
    A2, the logo's front corner read beside the crown vertex A, printed only "outside the model's outline", and no text
    related it to A): "A2: no node A2; 0.67 mm from node A as built; against the reading node A holds (read004-A): d
    -1.04 (-4.1 sd), y +0.18 (+1.0 sd), X +0.77 mm (+2.7 sd), chi2 18.1 for 3 dof; at A2's d, y it gives X 69.23 +-
    0.16 (A2 +0.26 mm)" (readings_apart: the joint comparison, made only when the two readings lie within
    UNUSED_NEAR_MM in (d, y): two points farther apart are two points, and their difference in sd says nothing); a
    nearest node without a reading says its source. At most UNUSED_ITEMS_MAX, then "+N more". A reading whose node
    holds another of its name is geometry.stale_reads'; one whose node is typed, solved or base is the fit's
    W_FIT_OFF_READING. ``near_read``: only the readings within UNUSED_NEAR_MM of the reading their nearest node holds
    (photo_read: a first read on revision 0's template, whose nodes hold none, would list every point)."""
    nodes = design.get("nodes") or {}
    fnodes = fit.nodes if fit is not None else {}
    out = []
    for name, rec in sorted(readings.items()):
        if name in nodes or not nodes:
            continue
        try:
            a = FIT.read_payload(rec)["read"]
        except (KeyError, TypeError, ValueError):
            continue
        pos = {}
        for n, v in nodes.items():
            f = fnodes.get(n) or {}
            pos[n] = (float(f.get("d", v[0])), float(f.get("y", v[1])))
        n = min(pos, key=lambda m: (math.hypot(pos[m][0] - a["d"], pos[m][1] - a["y"]), m))
        text = f"{name}: no node {name}; {math.hypot(pos[n][0] - a['d'], pos[n][1] - a['y']):.2f} mm from node {n} " \
               f"as built"
        t = nodes[n][2]
        near = G.is_read(t) and \
            math.hypot(float(t["read"]["d"]) - a["d"], float(t["read"]["y"]) - a["y"]) <= UNUSED_NEAR_MM
        if near_read and not near:
            continue
        if near:
            r = readings_apart(a, t["read"])
            (dd, dy, dX), (zd, zy, zX) = r["diff"], r["z"]
            text += (f"; against the reading node {n} holds ({t['read'].get('id') or n}): d {RD.signed(dd, 2)} "
                     f"({RD.signed(zd, 1)} sd), y {RD.signed(dy, 2)} ({RD.signed(zy, 1)} sd), X {RD.signed(dX, 2)} mm "
                     f"({RD.signed(zX, 1)} sd), chi2 {r['chi2']:.1f} for 3 dof; at {name}'s d, y it gives X "
                     f"{RD.fixed(r['x_at'], 2)} +- {RD.fixed(r['sd_at'], 2)} ({name} "
                     f"{RD.signed(float(a['X']) - r['x_at'], 2)} mm)")
        elif G.is_read(t):
            text += f" (the reading node {n} holds lies over {UNUSED_NEAR_MM:g} mm away)"
        else:
            what = "solved" if t is None else "the plain arm's t" if t == "base" else f"typed t {RD.fixed(t, 2)} mm"
            text += f" (node {n} holds no reading: {what})"
        out.append(text)
    if len(out) > UNUSED_ITEMS_MAX:
        out = out[:UNUSED_ITEMS_MAX - 1] + [f"+{len(out) - UNUSED_ITEMS_MAX + 1} more"]
    return out


def solved_moves(fit, before: dict) -> str:
    """The fitted nodes with no reading or typed value (source "solve") whose t differs from the reference's (the
    head's fit, ``before`` = {node: {t}}) by SOLVED_MOVE_MM or more, or that the reference does not have: "B t 5.41
    mm (head 7.06), D t 3.10 mm (not in the head)" (session2 printed "L0 4.64 (new)": no quantity, no unit); "" when
    none moved. A node the fit splits per facet (outside the outline) has no t ("(the head's has no single t)")."""
    items = []
    for n, r in sorted(fit.nodes.items()):
        if r.get("source") != "solve" or r.get("t") is None:
            continue
        t, b = float(r["t"]), (before.get(n) or {}).get("t")
        if b is None:
            why = "not in the head" if n not in before else "the head's has no single t"
            items.append(f"{n} t {RD.fixed(t, 2)} mm ({why})")
        elif abs(t - float(b)) >= SOLVED_MOVE_MM:
            items.append(f"{n} t {RD.fixed(t, 2)} mm (head {RD.fixed(float(b), 2)})")
    if len(items) > SOLVED_ITEMS_MAX:
        items = items[:SOLVED_ITEMS_MAX - 1] + [f"+{len(items) - SOLVED_ITEMS_MAX + 1} more"]
    return ", ".join(items)


def _shapes(x) -> dict:
    """{pair: (shape, mean dihedral)} of a build's built creases (kind "crease")."""
    return {tuple(c["regions"]): (c["shape"], c["mean_dihedral_deg"]) for c in x.tables["creases"]
            if c["kind"] == "crease"}


def flips(ref, tb) -> list[tuple]:
    """[(pair, (shape, deg) in ref, (shape, deg) in tb)] of the built creases (facet pairs both builds have) that are
    a ridge in one and a valley in the other."""
    a, b = _shapes(ref), _shapes(tb)
    return [(k, a[k], b[k]) for k in sorted(set(a) & set(b))
            if a[k][0] != b[k][0] and "flat" not in (a[k][0], b[k][0])]


def _deg(x: float) -> str:
    """A built crease's angle as report.crease_line prints it (build.deg_round: a flat 1.97 is "1.9", never "2.0")."""
    return f"{RP.deg_round(x, 1):.1f}"


def crease_changes(ref, tb, fit=None) -> list[str]:
    """The flips of ``tb`` against the reference (the head), the reference first: "crown|logo ridge 21.0° -> valley
    8.0°", with the solved corners of that edge whose stored reading the fit compared (solved_corners_text, ``fit`` =
    tb's FitResult). A declared crease is always built as declared, so this is every change of a declared shape too; a
    crease of a pair only one build has is the diff's; one that appears or goes flat is edge_changes'."""
    return [f"{'|'.join(key)} {sa} {_deg(da)}° -> {sb} {_deg(db)}°" + solved_corners_text(tb, fit, key)
            for key, (sa, da), (sb, db) in flips(ref, tb)]


def edge_changes(ref, tb) -> list[str]:
    """The built creases of ``tb`` (facet pairs both builds have) that are flat in the reference (the head) and a
    ridge or valley here, or the other way round: "shoulder|base flat 1.2° -> ridge 9.7° (an edge appears)",
    "lower|shoulder valley 4.3° -> flat 1.1° (the edge goes flat)". Why (v3p3 astra P3-4): crease_vs_head names
    ridge <-> valley flips only, and applying session2's r4 over head r6 added a shoulder|base edge (flat 1.2° ->
    ridge 9.7°, 3.2 mm proud) with no text saying so; GOAL 1-2 are judged on exactly these changes. Kept apart from
    crease_vs_head, whose contract is the flips (the T5 rehearsal checks it states exactly those)."""
    a, b = _shapes(ref), _shapes(tb)
    out = []
    for k in sorted(set(a) & set(b)):
        (sa, da), (sb, db) = a[k], b[k]
        if sa != sb and "flat" in (sa, sb):
            what = "an edge appears" if sa == "flat" else "the edge goes flat"
            out.append(f"{'|'.join(k)} {sa} {_deg(da)}° -> {sb} {_deg(db)}° ({what})")
    return out


def solved_corners_text(tb, fit, pair) -> str:
    """" (corner K: solved K 6.15: its reading 7.72 +- 0.29 (-5.4 sd))": the corners both facets of a crease pair
    share that the fit solved (source "solve") while a stored reading of their name lies more than fit.OFF_READING_SD
    from them (the first clause of that node's W_FIT_OFF_READING value warning); "" for none, a facet|base pair or no
    fit. Why (v3p3 owner P2-3, verify-12): mode solve drops the node's own reading, and with a flat crown the fit
    carried the crown's plane 12 mm from H into K (-6.8 sd off K's reading); a declared crown|logo ridge then cost chi2
    +0.7 and was built at the 2.5° minimum, and an undeclared one flipped to a 2.1° valley, with nothing tying either
    to K."""
    if fit is None or "base" in pair:
        return ""
    facets = getattr(getattr(tb, "spec", None), "facets", None) or {}
    if any(f not in facets for f in pair):
        return ""
    shared = set(facets[pair[0]]["poly"]) & set(facets[pair[1]]["poly"])
    msgs = {w.get("node"): str(w.get("message") or "") for w in getattr(fit, "warnings", None) or []
            if w.get("code") == "W_FIT_OFF_READING" and w.get("kind") == "solve"}
    nodes = getattr(fit, "nodes", None) or {}
    items = [f"corner {n}: {first_clause(msgs[n])}" for n in sorted(shared)
             if msgs.get(n) and (nodes.get(n) or {}).get("source") == "solve"]
    return f" ({'; '.join(items)})" if items else ""


def crease_shift(delta: dict, head_first: bool = True) -> str:
    """How far a model's visible creases lie from the head's, per shape (geometry.crease_delta: the symmetric Hausdorff
    distance in (d, y) of the built creases folding >= FLAT_CREASE_DEG, mm): "ridge 0.10, valley 8.37"; a shape only
    one of the two has (crease_delta's mm None): "valley: none in the head (12.3 mm here)" or "ridge: none here (the
    head's 30.1 mm)"; a shape neither has is left out. ``head_first``: the delta's first build is the head (Lab.delta
    orders the pair by revision). Why (v3p3 astra P2-2, verify-3): vs_head_mm (geometry_delta's value_mm, the surface
    along its normal or the outline) read 0.60 for an arm whose tip edge D-E met the top outline 7.9 mm further back
    (its valleys 8.37 mm from the head's); value_mm stays the distinctness and spread measure, this is text."""
    parts = []
    for shape in ("ridge", "valley"):
        c = delta["creases"][shape]
        la, lb = c["length_mm"]
        lh, lm = (la, lb) if head_first else (lb, la)
        if c["mm"] is None:
            parts.append(f"{shape}: none in the head ({lm:.1f} mm here)" if lm > lh else
                         f"{shape}: none here (the head's {lh:.1f} mm)")
        elif lh > 0.0 and lm > 0.0:
            parts.append(f"{shape} {c['mm']:.2f}")
    return ", ".join(parts)


def arm_spread_at(tb, deg: float) -> str:
    """Where a build's W_ARM_NORMAL_SPREAD bends (the builder's message gives only the largest angle, ``deg``; session2
    step 11 printed it with no d, so nothing could act on it): " at d 136.0 y -22.8 (over 30° only at d 136.0)" (the
    session's revision 6: the tip's rounding), the midpoint of the smooth outer edge folding ``deg`` and the d range
    of every smooth edge folding more than spec.ARM_NORMAL_SPREAD_DEG ("along d 1.0-136.0" at revision 0). A smooth
    edge is build._sharp_edges' last branch: two outer polygons of one region and band (not the roll, not a soft
    border's ramp, no ramp cell; a chamfer band's edge under SHARP_DIHEDRAL_DEG). "" when no smooth edge
    folds ``deg`` within 0.05° (a builder rule this copy does not follow: no place rather than a wrong one)."""
    D = BLD.mesh_topology(tb.polys)["directed"]
    kind, region, cell = tb.poly_kind, tb.poly_region, tb.poly_cell
    ramp = [tb.face_ramp[f] if f >= 0 and f < len(tb.face_ramp) else None for f in tb.poly_face]
    N, V = np.asarray(tb.normals, float), np.asarray(tb.V, float)
    rows = []
    for (a, b), p1 in D.items():
        p2 = D.get((b, a))
        if a > b or p2 is None or kind[p1] != "outer" or kind[p2] != "outer" or region[p1] != region[p2]:
            continue
        if {ramp[p1], ramp[p2]} - {None} == {"soft"} or "ramp" in (cell[p1], cell[p2]):
            continue
        dih = math.degrees(math.acos(max(-1.0, min(1.0, float(N[p1] @ N[p2])))))
        if "/" in region[p1] and dih >= SHARP_DIHEDRAL_DEG:
            continue
        rows.append((dih, 0.5 * (V[a, :2] + V[b, :2])))
    if not rows:
        return ""
    top = max(rows, key=lambda r: r[0])
    if abs(top[0] - float(deg)) > 0.05:
        return ""
    over = [f"{m[0]:.1f}" for dih, m in rows if dih > ARM_NORMAL_SPREAD_DEG]
    lo, hi = min(over, key=float), max(over, key=float)
    return f" at d {top[1][0]:.1f} y {top[1][1]:.1f} (over {ARM_NORMAL_SPREAD_DEG:g}° " + \
        (f"along d {lo}-{hi})" if lo != hi else f"only at d {lo})")


def capped_edges(edges: list) -> list:
    """build.model_edges within seeing's limits: at most MODEL_EDGES_MAX runs (the longest kept, in their order) of
    at most EDGE_POINTS_MAX points (a longer run is never made by the builder's stations; cut evenly if it were)."""
    def length(e):
        P = np.asarray(e["pts"], float)
        return float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum())
    keep = edges
    if len(edges) > MODEL_EDGES_MAX:
        top = set(sorted(range(len(edges)), key=lambda i: -length(edges[i]))[:MODEL_EDGES_MAX])
        keep = [e for i, e in enumerate(edges) if i in top]
    out = []
    for e in keep:
        if len(e["pts"]) > EDGE_POINTS_MAX:
            idx = np.unique(np.linspace(0, len(e["pts"]) - 1, EDGE_POINTS_MAX).round().astype(int))
            e = {**e, "pts": [e["pts"][i] for i in idx],
                 **({"deg_at": [e["deg_at"][i] for i in idx]} if e.get("deg_at") is not None else {})}
        out.append(e)
    return out


def _opening(spec_hinge) -> tuple | None:
    """(deg, (|axis x|, axis z)) of a spec's hinge opening (pose.chart_to_posed's form), None at opening 0."""
    deg = float(spec_hinge.get("opening_deg") or 0.0)
    if not deg:
        return None
    ax = spec_hinge["axis_xz_mm"]
    return deg, (abs(float(ax[0])), float(ax[1]))


class Lab(SceneHooks):
    def __init__(self, root, *, session=None, seeing=None):
        self.root = Path(root).resolve()
        self.manifest = read(self.root / "inputs" / "lab.json")
        if self.manifest.get("schema") != LAB_SCHEMA:
            raise ValueError(f"inputs/lab.json schema must be {LAB_SCHEMA}")
        self.rig = self.manifest.get("rig")
        if self.rig not in MATCHED_RIGS:
            raise ValueError(f"inputs/lab.json rig must be one of {MATCHED_RIGS}, got {self.rig!r}")
        self.s = session or BlenderSession(self.root, seed_blend=self.root / "inputs" / "scene.blend",
                                           calls_dir=self.root / "lab_calls")
        self.seeing = seeing or Seeing(self.s)
        self.work = self.root / "work" / "temple_lab"
        (self.work / "revisions").mkdir(parents=True, exist_ok=True)
        self.state_path = self.work / "state.json"
        self.state = S.load(self.state_path, self.rig)
        self._builds: OrderedDict = OrderedDict()
        self._fits: OrderedDict = OrderedDict()     # revision -> its fit record (immutable)
        self._edges: OrderedDict = OrderedDict()    # revision -> its build's model edges (capped)
        self._deltas: dict = {}                      # (revision a, revision b) -> geometry_delta (specs are immutable)
        self._basis = None
        self.lock = threading.Lock()                 # the scene lock (module doc)
        self.running: dict | None = None             # the tool call holding it (tools.py)
        self._unseen: set = set()                    # call ids the SDK timed out (mark_unseen)
        self.state_lock = threading.RLock()          # every state mutation and save (module doc)

    # ---------------------------------------------------------------------------------------------- plumbing
    def save(self) -> None:
        with self.state_lock:
            S.save(self.state_path, self.state)

    def verify_inputs(self) -> None:
        for row in self.manifest.get("assets", []):
            p = (self.root / "inputs" / row["path"]).resolve()
            if not p.is_relative_to(self.root / "inputs") or sha(p) != row["sha256"]:
                raise ValueError("input bytes changed or escaped the package")

    @property
    def basis(self) -> C.Basis:
        if self._basis is None:
            self._basis = C.load_basis(self.root)
        return self._basis

    def photo_ids(self) -> list[str]:
        """The target photos as "01".. (the basis' reference-NN of the target colourway)."""
        return [self.pid(rid) for rid in self.basis.refs if self.basis.colourway(rid) == "target"]

    def _rows(self, role: str) -> list[dict]:
        return [r for r in self.manifest.get("photos") or [] if r.get("role", "target") == role]

    def colourway_refs(self) -> dict:
        """{photo id: basis reference} of the calibrated photos of another colourway (e.g. {"B1": "black-side"})."""
        out = {}
        for row in self._rows("colourway"):
            ref = row.get("basis_ref")
            if ref not in self.basis.refs or self.basis.colourway(ref) == "target":
                raise ValueError(f"lab.json photo {row.get('id')!r}: the basis holds no colourway image {ref!r}")
            out[row["id"]] = ref
        return out

    def _colour(self, pid: str) -> str | None:
        """The colour of a colourway or reference-only photo (e.g. "black"); None for a target photo."""
        row = next((r for r in self.manifest.get("photos") or [] if r.get("id") == pid
                    and r.get("role", "target") != "target"), None)
        return (row.get("colour") or "other colour") if row else None

    def compare_ids(self) -> list[str]:
        """The photos with a camera (photo_read, photo_compare): the target photos, then the colourway photos."""
        return self.photo_ids() + list(self.colourway_refs())

    def image_ids(self) -> list[str]:
        """The photos photo_image takes: the compare photos, then the reference-only photos (no camera)."""
        return self.compare_ids() + [r["id"] for r in self._rows("reference")]

    def photo_names(self) -> dict:
        """{basis reference: the lab's photo id} of every photo with a camera: seeing's ``photo_names``, so a
        compare's image titles and texts name "02" and "B1" (v3p3-fix-seeing: they read "reference-02 photo" and
        "black-side photo", names no lab text uses)."""
        return {self.rid(p): p for p in self.compare_ids()}

    def pid(self, rid: str) -> str:
        for pid, ref in self.colourway_refs().items():
            if ref == rid:
                return pid
        return rid[len("reference-"):] if rid.startswith("reference-") else rid

    def rid(self, pid: str) -> str:
        """The basis reference of a target or colourway photo id; a reference-only photo (no camera) is refused."""
        for rid in self.basis.refs:
            if self.pid(rid) == pid and self.basis.colourway(rid) == "target":
                return rid
        ref = self.colourway_refs().get(pid)
        if ref is not None:
            return ref
        if pid in [r["id"] for r in self._rows("reference")]:
            raise LabRefusal("E_ARGS", f"photo {pid} has no calibrated camera: it is for photo_image only; photos with "
                                       f"a camera: {self.compare_ids()}")
        raise LabRefusal("E_ARGS", f"there is no photo {pid!r}; photos with a camera: {self.compare_ids()}")

    def _photos_arg(self, photos, *, low: int = 1, high: int | None = None) -> list[str] | None:
        """Validated photo ids of a compare or a variant render (target and colourway photos, each with a camera)."""
        if photos is None:
            return None
        ids = self.compare_ids()
        high = high or len(ids)
        if not isinstance(photos, list) or not low <= len(photos) <= high or len(set(photos)) != len(photos):
            raise LabRefusal("E_ARGS", f"photos must be {low}..{high} distinct ids of {ids}")
        for p in photos:
            self.rid(p)
        return list(photos)

    def variant_photos_max(self) -> int:
        """The most photos one facet_variants call takes: VARIANT_TARGETS_MAX target photos plus, when the lab has
        photos of another colour, one of those."""
        return min(VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX + len(self.colourway_refs()))

    def _variant_photos(self, photos) -> list[str]:
        """facet_variants' photos: 1..variant_photos_max() distinct photos with a camera, at most VARIANT_TARGETS_MAX
        of them target photos, each compared in full before by prepare or by Astra (its sheet takes that compare's
        stretch, gains and background; prepare compared every target photo, so only a photo of another colour can
        lack one: never_compared's refusal names both ways out)."""
        high = self.variant_photos_max()
        if photos is None:
            raise LabRefusal("E_ARGS", f"photos must name 1..{high} photos")
        photos = self._photos_arg(photos, low=1, high=high)
        targets = [p for p in photos if p in self.photo_ids()]
        if len(targets) > VARIANT_TARGETS_MAX:
            raise LabRefusal("E_ARGS", f"photos: at most {VARIANT_TARGETS_MAX} target photos (got {targets}); a 4th "
                                       f"must be a photo of another colour: {list(self.colourway_refs())}")
        for p in photos:
            if not any(p in S.shown_in_full(c) for c in self.state["compares"]):
                raise self.never_compared(p, "was never compared in full")
        return photos

    @staticmethod
    def never_compared(pid: str, what: str) -> LabRefusal:
        """E_STATE for a facet_variants photo without the full compare its sheet takes the stretch and gains from,
        with both ways out (review2-lab-e2-2 P3-10: "photo_compare it first" was the one remedy offered)."""
        return LabRefusal("E_STATE", f"photo {pid} {what} (its variant sheet takes the stretch and gains of the "
                                     f"latest full compare of it): a full photo_compare of it, or photos without it")

    def fingerprint_of(self, checkpoint: str | None) -> str | None:
        row = self.s.index.get(checkpoint) if isinstance(checkpoint, str) else None
        return row["fingerprint"] if row else None

    # ---------------------------------------------------------------------------------------------- revisions
    def readings(self) -> dict:
        """The stored readings {name: record} (photo_read; readings.read_point records plus call_id, id, revision)."""
        return self.state.get("readings") or {}

    def design(self, rid: int) -> dict:
        """Revision rid's design (its node t may be a reading)."""
        return read(self.work / S.revision(self.state, rid)["file"])

    def fit_record(self, rid: int) -> dict | None:
        """Revision rid's fit record (revisions/rNNNN.fit.json), cached; None for a revision stored before v3."""
        rec = S.revision(self.state, rid)
        if not rec.get("fit_file"):
            return None
        if rid not in self._fits:
            self._fits[rid] = read(self.work / rec["fit_file"])
            while len(self._fits) > BUILD_CACHE:
                self._fits.popitem(last=False)
        self._fits.move_to_end(rid)
        return self._fits[rid]

    def fit_result(self, rid: int):
        """Revision rid's stored fit as a FitResult (its report_lines), or None for a revision stored before v3."""
        rec = self.fit_record(rid)
        return FIT.FitResult(**rec["fit"]) if rec else None

    def spec(self, rid: int) -> dict:
        """Revision rid's fitted spec: what the builder and the worker build (module doc)."""
        rec = self.fit_record(rid)
        return rec["fit"]["spec"] if rec else self.design(rid)

    def build(self, rid: int):
        if rid in self._builds:
            self._builds.move_to_end(rid)
            return self._builds[rid]
        return self._cache_build(rid, build_temple(self.spec(rid)))

    def _cache_build(self, rid: int, tb):
        self._builds[rid] = tb
        self._builds.move_to_end(rid)
        while len(self._builds) > BUILD_CACHE:
            self._builds.popitem(last=False)
        return tb

    def raw_edges(self, rid: int) -> list:
        """Revision rid's build.model_edges (every run), cached."""
        if rid not in self._edges:
            raw = BLD.model_edges(self.build(rid))
            self._edges[rid] = (raw, capped_edges(raw))
            while len(self._edges) > BUILD_CACHE:
                self._edges.popitem(last=False)
        return self._edges[rid][0]

    def model_edges(self, rid: int) -> list:
        """Revision rid's model edges for the compares (build.model_edges of its build, capped_edges), cached."""
        self.raw_edges(rid)
        return self._edges[rid][1]

    def _fit_nodes(self, rid: int) -> dict:
        """{node: {t, ...}} of a revision's fit (a revision stored before v3: its build's node values)."""
        rec = self.fit_record(rid)
        if rec:
            return rec["fit"]["nodes"]
        return {n: {"t": float(r["t"])} for n, r in self.build(rid).tables["nodes"].items() if r.get("t") is not None}

    def delta(self, ra: int, rb: int) -> dict:
        """geometry_delta between two revisions (symmetric), cached: a revision never changes."""
        key = (min(ra, rb), max(ra, rb))
        if key not in self._deltas:
            self._deltas[key] = G.geometry_delta(self.build(key[0]), self.build(key[1]))
        return self._deltas[key]

    def distinct(self, ra: int, rb: int) -> bool:
        """The one head-distinctness rule (finish preconditions 1 and 5): geometry.distinct_heads of the two revisions'
        built temples. A revision is never distinct from itself."""
        return ra != rb and G.distinct_heads(self.delta(ra, rb))

    def head_record(self) -> dict | None:
        return S.head_by_fingerprint(self.state, self.s.head["fingerprint"]) if self.s.head else None

    def _register(self, design: dict, *, parent, kind: str, label=None, ops: int = 0, note: str = "",
                  call_id=None, fit=None) -> tuple[int, bool]:
        """The revision of ``design`` (an existing one of the same design sha, else a new one with its design file
        and, with ``fit``, its fit file): (revision id, new)."""
        digest = G.design_sha256(design)
        with self.state_lock:
            for r in self.state["revisions"]:
                if r["sha256"] == digest:
                    return r["id"], False
            rid = len(self.state["revisions"])
            rel = f"revisions/r{rid:04d}.json"
            write_json_atomic(self.work / rel, G.canonical_design(design))
            rec = {"id": rid, "parent": parent, "sha256": digest, "file": rel, "kind": kind, "label": label,
                   "ops": ops, "note": note, "call_id": call_id}
            if fit is not None:
                frel = f"revisions/r{rid:04d}.fit.json"
                fsha = spec_sha256(fit.spec)
                write_json_atomic(self.work / frel, {"schema": FIT_RECORD_SCHEMA, "design_sha256": digest,
                                                     "spec_sha256": fsha, "fit": fit.to_dict()})
                rec.update(fit_file=frel, spec_sha256=fsha)
            self.state["revisions"].append(rec)
        return rid, True

    def _image(self, path, kind: str, **meta) -> str:
        """Register an image returned to Astra (the context guard's index) and return its path."""
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("image outside the lab")
        digest = sha(path)
        with self.state_lock:
            self.state["images"][digest] = {"kind": kind, "file": str(path.relative_to(self.root)), **meta}
        return str(path)

    def _next(self, prefix: str) -> str:
        with self.state_lock:
            self.state["seq"] += 1
            return f"{prefix}{self.state['seq']:03d}"

    # ---------------------------------------------------------------------------------------------- SceneHooks
    def start(self):
        self.verify_inputs()
        return self.s.start()

    def close(self):
        """Shut the worker down, after any tool call still running (e.g. one the SDK timed out) has released the
        scene; one still running after LOCK_WAIT_S has the worker closed under it, and that is raised."""
        got = self.lock.acquire(timeout=LOCK_WAIT_S)
        try:
            self.s.close()
        finally:
            if got:
                self.lock.release()
        if not got:
            raise RuntimeError(f"a facet tool call still held the scene after {LOCK_WAIT_S:g} s; the worker was "
                               "closed under it")

    def worker_info(self):
        return {"worker_pid": self.s.worker.pid if self.s.worker else None}

    def journal_entries(self):
        with self.state_lock:
            return list(self.state["events"])

    def exit_checkpoint(self):
        """The head re-verified, under the scene lock (never while a timed-out tool call still uses the worker)."""
        if not self.lock.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError(f"a facet tool call still held the scene after {LOCK_WAIT_S:g} s; the exit "
                               "checkpoint was not verified")
        try:
            head = self.s.head
            p = self.s.ck_dir / head["file"]
            return {"id": head["id"], "sha256": head["sha256"],
                    "verified": sha(p) == head["sha256"] and self.s.fingerprint() == head["fingerprint"]}
        finally:
            self.lock.release()

    def mark_unseen(self, call_id: str) -> None:
        """A tool call Astra stopped waiting for (the SDK timed it out; its thread may still run): a compare it made
        or still makes is recorded unseen and never demands a judgement; its facet_variants sheets are unseen and
        never meet finish precondition 2; the readings its photo_read stored are undone (_unread) and one it still
        makes stores nothing; a finish it accepted is moved to ``finish_unseen`` (Astra was told
        E_TIMEOUT and the run goes on). Under the state lock: the call's thread may still be mutating or saving."""
        with self.state_lock:
            self._unseen.add(call_id)
            self._unread(call_id)
            for c in self.state["compares"]:
                if c.get("call_id") == call_id and c["by"] == "astra":
                    c.update(unseen=True, judged=True)
            for v in self.state["variants"]:
                if v.get("call_id") == call_id:
                    v["unseen"] = True
            fin = self.state.get("finish")
            if fin and fin.get("call_id") == call_id:
                self.state.setdefault("finish_unseen", []).append(fin)
                self.state["finish"] = None

    def _unread(self, call_id: str) -> None:
        """A photo_read the SDK timed out stored readings Astra never saw (v3p3 safety P3-2: a later set_node mode
        reading then used one): undo its reading_log entries, latest first, while the stored record is still that
        call's (a read removed, a replaced or dropped one put back), each entry marked unseen. Under the state lock
        (mark_unseen); a photo_read the SDK gave up on before it stored anything stores nothing (Lab.read)."""
        st = self.state.setdefault("readings", {})
        for e in reversed([e for e in self.state.get("reading_log") or [] if e.get("call_id") == call_id
                           and e.get("action") in ("read", "replaced", "dropped") and not e.get("unseen")]):
            n, cur = e["name"], st.get(e["name"])
            if e["action"] == "dropped" and cur is None:
                st[n] = e["was"]
            elif e["action"] in ("read", "replaced") and cur is not None and cur.get("call_id") == call_id:
                if e["action"] == "read":
                    st.pop(n)
                else:
                    st[n] = e["was"]
            e["unseen"] = True

    def restore(self, checkpoint_id, *, reason):
        before = self.s.head["id"]
        out = self.s.restore_checkpoint(checkpoint_id, call_id="loop-rollback")
        with self.state_lock:
            self.state["events"].append({"kind": "rollback", "checkpoint": checkpoint_id, "checkpoint_before": before,
                                         "head": self.s.head["id"], "reason": reason})
        self.save()
        return {"checkpoint": out.get("checkpoint"), "restored_from": checkpoint_id}

    def context_stubs(self, image_sha256s):
        """The context guard's one choice (loop.ContextGuard): compare and variant images of results older than the
        latest CONTEXT_KEEP_LATEST, except those of best_checkpoint's revision, become text stubs; so does the pick
        sheet of a reading that was read again or dropped since (the stored reading's own sheet stays)."""
        with self.state_lock:
            results = [("compare", c["compare_id"], c["seq"]) for c in S.seen_compares(self.state)] + \
                      [("variants", v["id"], v["seq"]) for v in S.seen_variants(self.state)]
            best = S.head_of_checkpoint(self.state, self.state.get("best_checkpoint"), self.fingerprint_of)
            best_compares = {c["compare_id"] for c in self.state["compares"]
                             if best is not None and c["revision"] == best["revision"]}
            images = {digest: dict(self.state["images"][digest]) for digest in image_sha256s
                      if digest in self.state["images"]}
            stored = {n: r.get("id") for n, r in self.readings().items()}
        keep = {ref for _k, ref, _s in sorted(results, key=lambda r: r[2])[-CONTEXT_KEEP_LATEST:]} | best_compares
        out = {}
        for digest in image_sha256s:
            rec = images.get(digest)
            if rec and rec["kind"] in STUB_KINDS and rec["ref"] not in keep:
                what = f"photo {rec.get('photo')}" if rec.get("photo") else "sheet"
                out[digest] = (f"[image removed by the host to save context: {rec['kind']} {rec['ref']} {what}, "
                               f"revision {rec.get('revision')}; superseded]")
            elif rec and rec["kind"] == "reading" and stored.get(rec.get("reading")) != \
                    f"{rec['ref']}-{rec.get('reading')}":
                since = "read again since" if rec.get("reading") in stored else "dropped since"
                out[digest] = (f"[image removed by the host to save context: pick sheet {rec['ref']} of reading "
                               f"{rec.get('reading')}; {since}]")
        return out

    def is_clean(self) -> bool:
        """True while no facet tool has run since prepare (a paid run starts only from a clean package)."""
        st = self.state
        return (st.get("start_checkpoint") is not None and len(st["revisions"]) == 1 and not st["judgements"]
                and not st["variants"] and not S.astra_compares(st) and not st.get("readings")
                and not st.get("reading_log") and not S.object_text(st)
                and all(e.get("kind") == "prepare" for e in st["events"]))

    # ---------------------------------------------------------------------------------------------- prepare
    def prepare(self) -> dict:
        """Revision 0 and the start compares (module doc). $0; refuses a package that was prepared already."""
        if self.state["revisions"]:
            raise ValueError("this lab is prepared already")
        t0 = time.perf_counter()
        self.start()
        with self.state_lock:
            self.state["start_checkpoint"] = self.s.head["id"]
            self.state["start_fingerprint"] = self.s.head["fingerprint"]
        exp = self.seeing.export_head("prepare-export")
        if exp.get("status") != "exported":
            raise ValueError(f"the original does not export: {exp}")
        r0 = G.revision_zero(exp["path"])
        with self.state_lock:
            self.state["original"] = {"export": {k: exp[k] for k in ("path", "sha256", "checkpoint")},
                                      "body": r0["body"], "side": r0["side"], "hinge_z_mm": r0["hinge_z"],
                                      "axis_xz_mm": r0["axis"], "footprints": r0["footprints"],
                                      "template_report": plain(r0["report"])}
        cmp0 = self._compare("prepare-original", self.photo_ids(), by="prepare", hinge_z=r0["spec"]["hinge"]["z_mm"])
        if cmp0.get("status") != "ok":
            raise ValueError(f"the compare of the original failed: {cmp0}")
        fr = FIT.fit_spec(r0["spec"])
        tb = build_temple(fr.spec)
        rid, _ = self._register(r0["spec"], parent=None, kind="template", label="revision 0", note="template_from_mesh",
                                fit=fr)
        self._cache_build(rid, tb)
        out = self._apply("prepare-r0", rid, tool="prepare")
        if out.get("status") != "ok":
            raise ValueError(f"revision 0 did not apply: {out}")
        cmp1 = self._compare("prepare-r0", self.photo_ids(), by="prepare")
        if cmp1.get("status") != "ok":
            raise ValueError(f"the compare of revision 0 failed: {cmp1}")
        self.save()
        return {"status": "prepared", "revision0": self.state["revisions"][0]["sha256"][:8],
                "checkpoint": self.s.head["id"], "compares": [cmp0["compare_id"], cmp1["compare_id"]],
                "seconds": round(time.perf_counter() - t0, 1)}

    # ---------------------------------------------------------------------------------------------- scene steps
    def _apply(self, call_id: str, rid: int, *, tool: str) -> dict:
        """Build revision rid's FITTED spec on both temples from the ORIGINAL (repeated decal transfers never
        accumulate) and checkpoint it; any failure restores the scene the call started on."""
        self.verify_inputs()
        before = self.s.head["id"]
        spec = self.spec(rid)
        if self.s.head["fingerprint"] != self.state["start_fingerprint"]:
            reset = self.s.restore_checkpoint(self.state["start_checkpoint"], call_id=call_id + "-base")
            if reset["status"] != "ok":
                return reset
        try:
            out = self.s.call_mutating(tool, "temple_lab_build", {"spec": spec}, deadline_s=BUILD_DEADLINE_S,
                                       call_id=call_id, label=f"revision {rid}")
        except Exception:
            # a fatal worker exception must not strand the scene on the original; a failed recovery is fatal too
            self.s.restore_checkpoint(before, call_id=call_id + "-exception-rollback")
            raise
        if out["status"] != "ok":
            out["rollback"] = self.s.restore_checkpoint(before, call_id=call_id + "-rollback")
        inherited = self._inherited_of(rid, out.get("result") or {}) if out["status"] == "ok" and tool == "prepare" \
            else None
        with self.state_lock:
            if out["status"] == "ok":
                self.state["heads"].append({"checkpoint": self.s.head["id"], "fingerprint": self.s.head["fingerprint"],
                                            "revision": rid, "call_id": call_id, "seq": self.state["seq"]})
                if inherited is not None:
                    self.state["inherited"] = inherited
            # prepare's apply is the package baseline (kind "prepare"): a resumed run never rolls it back
            self.state["events"].append({"kind": "prepare" if tool == "prepare" else "call", "call_id": call_id,
                                         "tool": tool, "mutating": True, "checkpoint_before": before,
                                         "checkpoint_after": self.s.head["id"]})
        self.save()
        return out

    def _zoom_arg(self, zoom) -> list[dict]:
        """photo_compare's zooms: [{photo (with a camera), box [x0, y0, x1, y1] native px inside the photo, at least
        ZOOM_MIN_PX a side, geometry: bool}], at most ZOOM_MAX; E_ARGS otherwise."""
        if zoom is None:
            return []
        if not isinstance(zoom, list) or len(zoom) > ZOOM_MAX:
            raise LabRefusal("E_ARGS", f"zoom must be null or a list of at most {ZOOM_MAX} {{photo, box, geometry}}")
        out = []
        for i, z in enumerate(zoom):
            if not isinstance(z, dict):
                raise LabRefusal("E_ARGS", f"zoom[{i}] must be {{photo, box: [x0, y0, x1, y1], geometry}}")
            pid = z.get("photo")
            rid = self.rid(pid)
            try:
                x0, y0, x1, y1 = (float(v) for v in z.get("box"))
            except (TypeError, ValueError):
                raise LabRefusal("E_ARGS", f"zoom[{i}].box must be [x0, y0, x1, y1] native photo pixels") from None
            H, W = self.basis.shape(rid)
            if not all(np.isfinite([x0, y0, x1, y1])) or not (0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H):
                raise LabRefusal("E_ARGS", f"zoom[{i}].box {_r([x0, y0, x1, y1], 1)} must lie inside the native {W} x "
                                           f"{H} photo {pid} with x0 < x1 and y0 < y1")
            if x1 - x0 < ZOOM_MIN_PX or y1 - y0 < ZOOM_MIN_PX:
                raise LabRefusal("E_ARGS", f"zoom[{i}].box must be at least {ZOOM_MIN_PX} x {ZOOM_MIN_PX} native px")
            geom = z.get("geometry", False)
            if not isinstance(geom, bool):
                raise LabRefusal("E_ARGS", f"zoom[{i}].geometry must be true or false")
            out.append({"photo": pid, "rid": rid, "box": [x0, y0, x1, y1], "geometry": geom})
        return out

    def _compare(self, call_id: str, photos, *, by: str, crop: str = "temple", points=None, zoom=(),
                 hinge_z: float | None = None, tool: str | None = None) -> dict:
        """The photo-matched compare of the head (module doc): its model edges, hinge_z and opening, the zooms; a
        tool's compare renders under that tool's hard timeout. ``photos`` None = seeing's default set, [] = zooms
        only."""
        head = self.head_record()
        spec = self.spec(head["revision"]) if head else None
        if hinge_z is None:
            hinge_z = spec["hinge"]["z_mm"] if spec else None
        points_px = None
        if points and photos is not None:           # charted on the photos shown in full; the rest are listed
            points_px = {}
            for p in points:
                if p["photo"] in photos:
                    points_px.setdefault(self.rid(p["photo"]), []).extend(p["points"])
            points_px = points_px or None
        cid = self._next("cmp")
        refs = [self.rid(p) for p in photos] if photos is not None else "default"
        kw = {}
        if head is not None:
            kw.update(model_edges=self.model_edges(head["revision"]), opening=spec["hinge"])
        if zoom:
            kw["zoom"] = [{"rid": z["rid"], "box": z["box"], "geometry": z["geometry"]} for z in zoom]
        res = self.seeing.compare_to_photos(refs=refs, passes=COMPARE_PASSES, rig=self.rig, crop=crop,
                                            points_px=points_px, hinge_z_mm=hinge_z, call_id=cid,
                                            render_deadline_s=hard_timeout_s(tool) if tool else None,
                                            photo_names=self.photo_names(), **kw)
        if res.get("status") != "ok":
            self.save()
            return {"status": "error", "code": res.get("code", "E_COMPARE"), "message": res.get("message")}
        report = read(res["report"])
        rids = list(res["photos"])
        stacked = [self.pid(r) for r in rids]
        zrecs = res.get("zoom") or []
        shown = list(dict.fromkeys(stacked + [self.pid(z["rid"]) for z in zrecs]))
        rec = {"compare_id": cid, "checkpoint": res.get("checkpoint", self.s.head["id"]),
               "fingerprint": self.s.head["fingerprint"], "revision": head["revision"] if head else None,
               "photos": shown, "stacked": stacked, "by": by, "call_id": call_id, "report": res["report"],
               "judged": by != "astra", "seq": self.state["seq"],
               **({"zoom": [{k: z[k] for k in ("photo", "box", "geometry")} for z in zoom]} if zoom else {})}
        with self.state_lock:
            if by == "astra" and call_id in self._unseen:          # the SDK stopped waiting: Astra never sees it
                rec.update(unseen=True, judged=True)
            self.state["compares"].append(rec)
        texts, images, said = {}, [], set()
        for r in rids:
            pid = self.pid(r)
            texts[pid] = self._photo_text(report["photos"].get(r, {}), res["photos"][r].get("points"), spec,
                                          colour=self._colour(pid), said=said)
            images.append(self._image(res["photos"][r]["image"], "compare", ref=cid, photo=pid,
                                      revision=rec["revision"]))
        ztexts = []
        for z in zrecs:
            pid = self.pid(z["rid"])
            lines = str(z.get("text") or "").split("\n")            # seeing names the lab's id (photo_names)
            colour = self._colour(pid)
            if colour and lines:            # seeing names another colourway "variant": the lab says its colour
                lines[0] = lines[0].replace(f", {self.basis.colourway(z['rid'])})", f", {colour})", 1)
            ztexts.append("\n".join(ln for ln in lines if not COMPARE_ID_LINE_RE.match(ln.strip())))
            images.append(self._image(z["image"], "compare", ref=cid, photo=pid, revision=rec["revision"], zoom=True))
        self.save()
        out = {"status": "ok", "compare_id": cid, "photos": texts}
        if photos is None and by == "astra":
            left = [p for p in self.photo_ids() if p not in stacked]
            if left:
                out["left_out"] = (f"{', '.join(left)}: the default set is the target photos whose temple shows at "
                                   f"least {TEMPLE_ROI_MIN_PX:,} native px")
        if points:
            missed = []
            for p in dict.fromkeys(p["photo"] for p in points):
                if p not in stacked:
                    missed.append(f"{p} (shown in a zoom only)" if p in shown else f"{p} (not in this compare)")
            if missed:
                out["points_not_charted"] = ("points_px are charted on the photos a compare shows in full: "
                                             + ", ".join(missed))
        if zrecs:
            out["zoom"] = ztexts
        out["images"] = images
        return out

    def _photo_text(self, rec: dict, points, spec, colour: str | None = None, said: set | None = None) -> str:
        """One photo of a compare, compact (module doc): view (a colourway photo: its colour and rows), temple IoU,
        px/mm of the image shown and native, the crop box and scale k (u_img = (u - x0 + 0.5) k - 0.5), each facing
        temple's side-view outline offsets (seeing's text), the render's backdrop share from BACKDROP_SHOWN (seeing's
        render_backdrop_share: at 05 the crown rendered white on white), points_px charted (t = X - x_in(d) of the
        head). ``said`` (one set per result): a clause explained once, its later photos give the share only (v3p3
        astra P3-6: "(the outline marks the model's edge)" repeated in every such photo of every compare)."""
        crop = rec.get("crop") or {}
        k = float(crop.get("k") or 1.0)
        ppm = rec.get("px_per_mm")
        rows = "rows photo / gloss-black render / overlay" if colour else "rows photo / render / overlay"
        text = f"{rec.get('view_kind')}" + (f" {colour}" if colour else "") + f" ({rows}); temple IoU " \
            f"{float(rec.get('temple_iou', float('nan'))):.3f}"
        if ppm is not None and np.isfinite(ppm):
            text += f", {ppm * k:.2f} px/mm in this image ({ppm:.2f} native)"
        if crop.get("box"):
            text += f", box [{','.join(str(int(v)) for v in crop['box'])}] k {k:.3f}"
        for temple, o in (rec.get("outline_offsets_mm") or {}).items():
            if isinstance(o, dict) and o.get("facing"):            # a temple seen from inside: never printed
                text += " | " + Seeing._outline_text(temple, o)
        share = max((float(v) for v in (rec.get("render_backdrop_share") or {}).values()), default=0.0)
        if share >= BACKDROP_SHOWN:                # a face clipped to the backdrop colour is not a missing face
            text += f" | render: {share:.0%} of the model's pixels show in the backdrop's colour"
            if said is None or "backdrop" not in said:
                text += " (the outline marks the model's edge)"
                if said is not None:
                    said.add("backdrop")
        if points:
            pts = []
            inner = np.asarray(spec["inner"], float) if spec is not None else None
            for p in points:
                u, v = p["uv"]
                if p.get("d_mm") is None or inner is None:
                    pts.append(f"({u:g},{v:g}) no temple hit")
                else:                                   # t = X - x_in(d): thickness from the head's inner face
                    t = p["X_mm"] - float(np.interp(p["d_mm"], inner[:, 0], inner[:, 1]))
                    pts.append(f"({u:g},{v:g}) d {p['d_mm']:.2f} y {p['y_mm']:.2f} t {t:.2f}")
            text += " | points " + "; ".join(pts)
        return text

    # ---------------------------------------------------------------------------------------------- receipts
    def _inherited_of(self, rid: int, result: dict) -> dict:
        """Revision rid's warnings at prepare (the module doc's inherited warnings): its core warning lines, the
        Blender layer's own warning lines of that apply and its hinge leaf gap."""
        tb = self.build(rid)
        core = RP.warning_lines(tb)
        blender = []
        for ln in result.get("lines", []):
            if ln.startswith("W_") and ln not in core and ln not in blender:
                blender.append(ln)
        gaps = [lf.get("gap_mm") for s in (result.get("sides") or {}).values()
                for lf in (s.get("hinge") or {}).get("leaves", []) if lf.get("gap_mm") is not None]
        return {"revision": rid, "core": core, "blender": blender,
                "hinge_leaf_gap_mm": _r(max(gaps, key=abs)) if gaps else None}

    def _inherited(self) -> dict:
        return self.state.get("inherited") or {"revision": 0, "core": [], "blender": [], "hinge_leaf_gap_mm": None}

    def _inherited_ref(self, line: str) -> str | None:
        """The revision-0 warning ``line`` repeats (inherited_ref over the core and Blender lines at prepare)."""
        inh = self._inherited()
        return inherited_ref(line, inh["core"] + inh["blender"])

    @staticmethod
    def _where(tb, w: dict) -> str:
        """The place a core warning leaves out: W_ARM_NORMAL_SPREAD's (arm_spread_at); "" for the others (they name
        their facet, crease or d)."""
        if w.get("code") == "W_ARM_NORMAL_SPREAD" and tb is not None and w.get("deg") is not None:
            return arm_spread_at(tb, float(w["deg"]))
        return ""

    def _builder_warnings(self, tb) -> tuple[list[str], list[str]]:
        """(warning lines, changed inherited lines) of a build for a receipt: W_CONDITIONING left out (WP6_V3 §6: a
        fitted spec has coef planes, which it never concerns); W_FLAT_CREASE left out for a pair whose crease line is
        printed flat (crease_lines prints every built crease: "shoulder|base flat 1.2° soft undeclared" beside
        "W_FLAT_CREASE: crease shoulder|base bends only 1.23 deg (< 2.0)" in every receipt from session2's step 06, a
        repeat that read as a request for more angle at a border the photos show soft; v3p3 astra P3-1, P3-6); a
        warning revision 0 had (inherited_ref) never in the
        lines: unchanged it is left out, changed beyond the material tolerance it is a changed inherited line with its
        place and revision 0's numbers ("...; revision 0 had it with 61.5": session2 step 11 printed W_ARM_NORMAL_SPREAD
        as news because it IMPROVED, 61.5 -> 49.9, with no d); every W_BORDER_RAMP in one line last (ramp_line) except
        the runs a crease line prints as its step (printed_steps: printed once)."""
        lines, ramps, changed = [], [], []
        steps = printed_steps(tb)
        flat = {tuple(c["regions"]) for c in tb.tables["creases"] if c["kind"] == "crease" and c["shape"] == "flat"}
        for w in tb.warnings:
            if w["code"] == "W_CONDITIONING":
                continue
            if w["code"] == "W_FLAT_CREASE" and tuple(w.get("regions") or ()) in flat:
                continue                    # its crease line says "flat 1.2°" already (v3p3 astra P3-1, P3-6)
            line = f"{w['code']}: {w['message']}"
            ref = self._inherited_ref(line)
            if ref is not None:
                if not same_warning(line, ref):
                    note = inherited_note(line, ref)
                    changed.append((line + self._where(tb, w))[:LINE_MAX - len(note)] + note)
                continue
            if w["code"] == "W_BORDER_RAMP":
                if (w.get("facet"), tuple(_r(w.get("d_mm") or [], 1))) not in steps:
                    ramps.append(w)
            else:
                lines.append((line + self._where(tb, w))[:LINE_MAX])
        return lines + ([ramp_line(ramps)] if ramps else []), changed

    def _receipt(self, tb, fit, edges=None) -> dict:
        """The receipt (module doc), in order: the fit report without its crease lines (FitResult.report_lines, at
        most FIT_LINES_MAX lines), one line per built crease (crease_lines: the builder's and the fit's; ``edges`` =
        build.model_edges of tb, for the signed extremes of a crease that folds both ways), the builder's warnings
        (_builder_warnings) and revision 0's warnings that changed (``inherited_changed``), the thickness ("min 2.78 mm
        at d 0.0 y 10.1; max 7.88 mm at ..."), border ramps that raised no W_BORDER_RAMP in the crease line's words
        (ramp_text: "shoulder proud 0.81 mm: a 7.7° ramp at d 75.5-76.7"; session2 printed "shoulder +0.81 7.7° d
        75.5-76.7" and a thickness with no unit), the knot snaps of the spec's canonical form, and the roll."""
        rc = RP.receipt(tb)
        th = rc["thickness"]
        out = {"fit": fit.report_lines(limit=FIT_LINES_MAX, creases=False) if fit is not None else [],
               "creases": crease_lines(tb, fit, edges)}
        warns, changed = self._builder_warnings(tb)
        if warns:
            out["warnings"] = warns
        if changed:
            out["inherited_changed"] = changed
        out["thickness"] = (f"min {th['min_mm']:.2f} mm at d {th['min_at'][0]:.1f} y {th['min_at'][1]:.1f}; max "
                            f"{th['max_mm']:.2f} mm at d {th['max_at'][0]:.1f} y {th['max_at'][1]:.1f}")
        warned = {(w.get("facet"), tuple(_r(w.get("d_mm") or [], 1))) for w in tb.warnings
                  if w["code"] == "W_BORDER_RAMP"}
        quiet = [ramp_text(r["facet"], r["height_mm"], r["slope_deg"], r["d_mm"])
                 for r in rc["border_ramps"] if abs(r["height_mm"]) >= 0.01
                 and (r["facet"], tuple(_r(r["d_mm"], 1))) not in warned]
        if quiet:
            out["border_ramps"] = "; ".join(quiet[:RAMP_ITEMS_MAX]) + \
                (f"; +{len(quiet) - RAMP_ITEMS_MAX} more" if len(quiet) > RAMP_ITEMS_MAX else "")
        if rc["snaps"]:                     # a bare count was explained nowhere (review2-lab-e2-2 P3-10)
            n = len(rc["snaps"])
            move = max(float(np.max(np.abs(np.atleast_1d(np.asarray(s["to"], float))
                                           - np.atleast_1d(np.asarray(s["from"], float))))) for s in rc["snaps"])
            out["snaps"] = (f"{n} knot or node value{'s' if n > 1 else ''} moved "
                            + (f"up to {move:.2f} mm" if move >= 0.005 else "under 0.01 mm")
                            + " onto a shared d or the outline edge (the spec's canonical form)")
        if "roll" in rc:
            out["roll"] = rc["roll"]
        return out

    @staticmethod
    def _crease_brief(tb) -> str:
        """Built creases in one string, in report.crease_line's words: "F1|F2 ridge 12.6°, F4|base valley 5.1°" (the
        shape and the mean dihedral, build.deg_round as report.crease_line: a flat crease at 1.95-1.999 prints "flat
        1.9°", never 2.0; v3-integrate-2, builder2's request; session2's "crown|logo r14.5" coded the shape in one
        letter that no text explained). A facet|base border whose step stands RAMP_BRIEF_MM or more proud or sunk adds
        its ramp in the receipt's words (report.ramp_clause: "shoulder|base ridge 9.7° (proud 3.20 mm: a 28.1° ramp at
        d 73.8-75.4)"; v3p3 owner P3-1: a flat-crown arm read "shoulder|base ridge 9.7°" while its shoulder stood 3.2 mm
        proud, run 7's hard join, seen in the render only). A facet|base border beside a strip of plain arm the facet
        leaves along the outline names it as the receipt's crease line does (geometry.strip_text in brackets). A
        declared shape always matches here: a mismatch is refused (E_CREASE_SHAPE) before."""
        items = []
        strips = G.outline_strips(tb)
        for c in tb.tables["creases"]:
            if c["kind"] != "crease":
                continue
            text = f"{'|'.join(c['regions'])} {c['shape']} {RP.deg_round(c['mean_dihedral_deg'], 1):.1f}°"
            st = c.get("step")
            if st and abs(float(st["height_mm"])) >= RAMP_BRIEF_MM:
                text += f" ({RP.ramp_clause(float(st['height_mm']), float(st['slope_deg']), st.get('d_mm'))})"
            strip = G.strip_text(c["regions"][0], strips.get(c["regions"][0], [])) if c["regions"][1] == "base" \
                else ""
            if strip:
                text += f" ({strip})"
            items.append(text)
        return ", ".join(items)

    def _blender_receipt(self, result: dict, core_lines: list, tb=None) -> dict:
        """The Blender layer's own news, compact: decal motion, the hinge leaf gap (negative = embedded) when it
        moved from revision 0's, and its warning lines not in the core receipt, one per code (the first message,
        "(xN)" when the sides repeat it): "lines" those revision 0 did not have; "inherited_changed" those it had whose
        numbers moved beyond the material tolerance, with revision 0's numbers (inherited_note; a W_ARM_NORMAL_SPREAD
        with its place in ``tb``, the build applied, when tb has that very line); one it had unchanged is left out."""
        sides = result.get("sides") or {}
        moves = [d["max_move_mm"] for s in sides.values() for d in s.get("decals", [])]
        out = {}
        if moves:
            out["decal_max_move_mm"] = _r(max(moves))
        gaps = [lf.get("gap_mm") for s in sides.values() for lf in (s.get("hinge") or {}).get("leaves", [])
                if lf.get("gap_mm") is not None]
        ref = self._inherited().get("hinge_leaf_gap_mm")
        if gaps and (ref is None or not _close(max(gaps, key=abs), ref)):
            out["hinge_leaf_gap_mm"] = _r(max(gaps, key=abs))
        news: dict = {}
        changed: dict = {}
        for ln in result.get("lines", []):
            if ln in core_lines:
                continue
            ref = self._inherited_ref(ln)
            if ref is None:
                news.setdefault(ln.split(":", 1)[0], []).append(ln)
            elif not same_warning(ln, ref):
                changed.setdefault(ln.split(":", 1)[0], []).append((ln, ref))
        if news:
            out["lines"] = [rows[0][:LINE_MAX] + (f" (x{len(rows)})" if len(rows) > 1 else "")
                            for rows in list(news.values())[:6]]
        if changed:
            items = []
            for rows in list(changed.values())[:6]:
                ln, ref = rows[0]
                w = next((w for w in (tb.warnings if tb is not None else []) if f"{w['code']}: {w['message']}" == ln),
                         {})
                note = (f" (x{len(rows)})" if len(rows) > 1 else "") + inherited_note(ln, ref)
                items.append((ln + self._where(tb, w))[:LINE_MAX - len(note)] + note)
            out["inherited_changed"] = items
        return out

    def _chart_image(self, rid: int, tb, tag: str) -> str:
        """The (d, y) chart of revision rid's fitted build (facets labelled and shaded, creases, nodes, thickness
        contours, decal and hinge footprints)."""
        rec = S.revision(self.state, rid)
        fps = (self.state.get("original") or {}).get("footprints")
        chart = DR.chart_image(self.spec(rid), tb, footprints=fps, width=CHART_W,
                               title=f"revision {rid} ({rec['sha256'][:8]}): d mm back from the hinge, y mm up | "
                                     f"t {tb.thickness.min():.2f}-{tb.thickness.max():.2f} mm")
        p = self.work / "images" / f"{tag}-chart.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        chart.save(p)
        return self._image(p, "chart", ref=tag, revision=rid)

    # ---------------------------------------------------------------------------------------------- tools
    def _fit_now(self, rid: int):
        """Revision rid's fit with the CURRENT readings (their W_FIT_OFF_READING facts; the spec is the stored one:
        the fit is deterministic), else the stored fit (a design the fit refuses now, e.g. one stored before v3)."""
        try:
            return FIT.fit_spec(self.design(rid), readings=self.readings())
        except (TempleError, KeyError, TypeError, ValueError):
            return self.fit_result(rid)

    def inspect(self) -> dict:
        head = self.head_record()
        if head is None:
            raise LabRefusal("E_STATE", "the scene holds no applied revision (prepare the lab first)")
        rid = head["revision"]
        design, spec, tb = self.design(rid), self.spec(rid), self.build(rid)
        fit = self._fit_now(rid)
        rc = RP.receipt(tb, include_stations=True)
        fnodes = fit.nodes if fit is not None else {}

        stale = G.stale_reads(design, self.readings())    # a node keeps the reading it was set from (snapshot)

        def design_t(n, t):
            if G.is_read(t):
                return f"read ({stale[n]} since)" if n in stale else "read"
            return "solve" if t is None else t if t == "base" else _r(t)
        def moved_to(f):
            """The fitted corner [d, y] when the joint fit moved a read corner at least fit.MOVE_SHOWN_MM along its
            reading (its t and X are there), else None (v3-integrate-2, fit2's request: on session2's flat-crown head
            the row read E at d 76.75 with X 70.35, 2.7 mm out from its reading, while the fit had put E at d
            73.85 with a 0.41 mm residual)."""
            mv = f.get("move_mm")
            if not mv or math.hypot(*mv) < FIT.MOVE_SHOWN_MM:
                return None
            return _r([f["d"], f["y"]])
        def residual(f):
            """(residual_mm, residual_sd) of a node row: a read or typed node's against its reading or typed value;
            a solved or base node has none (NO_RESIDUAL: session2's solve node L0 printed 0.21 mm, 0.0 sd, its
            distance from the fit's weak 5 mm prior toward the base, which read as a misfit)."""
            if f.get("source") in NO_RESIDUAL:
                return NO_RESIDUAL[f["source"]], None
            return _r(f.get("residual_mm")), _r(f.get("residual_sd"), 1)
        nodes = {}
        for n, (d, y, t) in sorted(design["nodes"].items()):
            f = fnodes.get(n)
            row = [_r(d), _r(y), design_t(n, t)]
            if f is not None:
                row += [_r(f.get("t")), _r(f.get("X")), f.get("source"), *residual(f), _r(f.get("sd_X")),
                        moved_to(f)]
            nodes[n] = row
        facets = {}
        for label, f in design["facets"].items():
            plane = f["plane"]
            pl = "flat" if plane == "nodes" else "warp" if plane == "warp" else plane
            facets[label] = {"poly": f["poly"], "plane": pl, **({"soft": True} if f.get("soft") else {})}
        readings = {}
        for n, r in sorted(self.readings().items()):          # to 0.01 mm, as the node rows and reading texts
            rr = RD.refresh(r, tb)
            mt = RD.OUTSIDE_MODEL if rr["model_t"] is None else _r(rr["model_t"])     # as readings.reading_text says
            readings[n] = [rr["kind"], *_r([rr["d"], rr["y"], rr["X"], rr["sd_X"], rr["t"]]), mt,
                           " ".join(rr["photos"]), " ".join(w["code"] for w in rr.get("warnings") or [])]
        revs = self.state["revisions"]
        rows = [[r["id"], r["parent"], r["kind"], r["sha256"][:8]] + ([r["label"]] if r.get("label") and
                                                                       r["kind"] == "variant" else [])
                for r in revs[-12:]]
        mine = S.seen_compares(self.state)
        tag = self._next("inspect")
        out = {"status": "ok", "head": {"checkpoint": self.s.head["id"], "revision": rid,
                                        "design": revs[rid]["sha256"][:8], "spec": spec_sha256(spec)[:8]},
               "spec": {"hinge": {"z_mm": _r(spec["hinge"]["z_mm"]), "axis_xz_mm": _r(spec["hinge"]["axis_xz_mm"])},
                        "tip_round_mm": _r(spec["outline"]["tip_round_mm"]), "roll_mm": _r(spec["roll_mm"]),
                        "stations": rc["stations"],
                        "nodes": {"columns": ["d", "y", "design", "t", "X", "source", "residual_mm", "residual_sd",
                                              "sd_X", "fitted_at"], "rows": nodes},
                        "facets": facets,
                        "creases": [f"{'|'.join(c['facets'])} {c['shape']}" for c in design["creases"]]},
               "readings": {"columns": ["kind", "d", "y", "X", "sd_X", "t", "model_t", "photos", "warnings"],
                            "rows": readings},
               **({"unused_readings": unused} if (unused := unused_readings(design, fit, self.readings())) else {}),
               "object": S.object_text(self.state) or None,
               **self._object_layout(design),
               "receipt": self._receipt(tb, fit, self.raw_edges(rid)),
               "revisions": {"count": len(revs), "last": rows, "columns": ["id", "parent", "kind", "design"]},
               "applied": [[h["checkpoint"], h["revision"]] for h in self.state["heads"][-12:]],
               "photos": self._photo_list(),
               "latest_compare": mine[-1]["compare_id"] if mine else None,
               "judgement_due": (S.pending_compare(self.state) or {}).get("compare_id"),
               "best_checkpoint": self.state.get("best_checkpoint"),
               "images": [self._chart_image(rid, tb, tag)]}
        self.save()
        return out

    def _object_layout(self, design: dict) -> dict:
        """facet_inspect's fact beside the object note when the latest note was written for another layout than the
        head's (state.object_due: the latest note decides, so a facet_edit of the head's own layout then needs a new
        note; review2-lab-e2-1 P3-3: a note recorded by a refused call for a layout with M showed as the head's):
        {"object_layout": "the note (given with revision 4) was written for another layout; the head: -node Q; -facet
        extra"} (state.object_origin: the revision, never the SDK call id, v3p3 astra P3-5); {} otherwise."""
        objects = self.state.get("objects") or []
        if not objects or objects[-1].get("layout") is None:
            return {}
        last = objects[-1]
        diff = S.layout_diff(last["layout"], S.layout_key(design))
        if not diff:
            return {}
        return {"object_layout": f"the note ({S.object_origin(last)}) was written for another layout; the head: "
                                 f"{diff}"}

    def _photo_list(self) -> dict:
        """facet_inspect's photo list: id -> view and "camera" for every photo with a camera (the tools that take one:
        a target photo; a photo of another colourway with its colour), "photo_image only" for a reference photo with
        its colour (v3p3 astra P3-9: only B1-B3 said "camera", while 01-06 have cameras too)."""
        out = {pid: f"{self.basis.refs[self.rid(pid)].get('view_kind')}, camera" for pid in self.photo_ids()}
        for pid, ref in self.colourway_refs().items():
            out[pid] = f"{self.basis.refs[ref].get('view_kind')}, {self._colour(pid)}, camera"
        for row in self._rows("reference"):
            out[row["id"]] = f"{row.get('view')}, {self._colour(row['id'])}, photo_image only"
        return out

    def photo(self, photo: str, crop=None, enhance: bool = False, scale=1) -> dict:
        """A photo (any of image_ids: a reference-only photo is read from inputs/, sha-checked) or a native crop,
        optionally contrast-stretched (``enhance``) and scaled up to state.SCALE_MAX for a small crop
        (sheets.photo_view; the long side stays <= sheets.IMAGE_MAX)."""
        if not isinstance(enhance, bool):
            raise LabRefusal("E_ARGS", "enhance must be true or false")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 1 <= float(scale) <= S.SCALE_MAX:
            raise LabRefusal("E_ARGS", f"scale must be 1..{S.SCALE_MAX:g}")
        ref_row = next((r for r in self._rows("reference") if r["id"] == photo), None)
        if ref_row is not None:
            p = (self.root / "inputs" / ref_row["path"]).resolve()
            if not p.is_relative_to(self.root / "inputs") or not p.is_file() or sha(p) != ref_row["sha256"]:
                raise LabRefusal("E_STATE", f"photo {photo}: its input bytes changed")
            rgb, view = np.asarray(Image.open(p).convert("RGB")), ref_row.get("view")
        else:
            rid = self.rid(photo)
            rgb, view = self.basis.photo(rid), self.basis.refs[rid].get("view_kind")
        H, W = rgb.shape[:2]
        if crop is not None:
            if not isinstance(crop, list) or len(crop) != 4 or any(isinstance(v, bool) or not isinstance(v, int)
                                                                   for v in crop):
                raise LabRefusal("E_ARGS", "crop is [x0, y0, x1, y1] integer native pixels")
            x0, y0, x1, y1 = crop
            if not (0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H):
                raise LabRefusal("E_ARGS", f"crop must lie inside the {W} x {H} photo")
        im, transform = SH.photo_view(rgb, crop, enhance=enhance, scale=float(scale))
        tag = hashlib.sha256(json.dumps([photo, crop, enhance, float(scale)]).encode()).hexdigest()[:12]
        p = self.work / "images" / f"photo-{photo}-{tag}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p)
        colour = self._colour(photo)
        out = {"status": "ok", "photo": photo, "view": view, **({"colour": colour} if colour else {}),
               "transform": transform, "images": [self._image(p, "photo", ref=f"photo-{photo}")]}
        self.save()
        return out

    def read(self, call_id: str, points=None, drop=None) -> dict:
        """photo_read (WP6_V3 §4, §6): ``points`` [{name, kind: corner | crossing, picks: [{photo, uv, edges,
        sd_px}]}] (<= state.READ_POINTS_MAX, names distinct) read on the head's build (readings.read_point: its hinge
        and inner face; X, d, y do not depend on the model), ``drop`` [names] of stored readings. All or none: any
        refusal (readings' ValueError, a photo without a camera, an unknown name to drop) is E_ARGS and nothing is
        stored. Each reading is stored by name in state "readings" (replacing one of the same name; the old record
        goes to "reading_log" with the call); each returns its text (readings.reading_text) and one pick sheet
        (readings.pick_sheet: which feature each pick is on)."""
        head = self.head_record()
        if head is None:
            raise LabRefusal("E_STATE", "the scene holds no applied revision")
        points = [] if points is None else points
        drop = [] if drop is None else drop
        if not isinstance(points, list) or len(points) > S.READ_POINTS_MAX:
            raise LabRefusal("E_ARGS", f"points must be a list of at most {S.READ_POINTS_MAX} {{name, kind, picks}}")
        if not isinstance(drop, list) or len(drop) > S.READ_DROP_MAX or len(set(map(str, drop))) != len(drop) \
                or any(not isinstance(n, str) for n in drop):
            raise LabRefusal("E_ARGS", f"drop must be a list of at most {S.READ_DROP_MAX} distinct reading names")
        if not points and not drop:
            raise LabRefusal("E_ARGS", "name points to read or readings to drop")
        names = [p.get("name") if isinstance(p, dict) else None for p in points]
        if len(set(names)) != len(names):
            raise LabRefusal("E_ARGS", f"points repeat a name: {names}")
        both = sorted(set(drop) & set(n for n in names if isinstance(n, str)))
        if both:
            raise LabRefusal("E_ARGS", f"{both} are both read and dropped in this call")
        stored = self.readings()
        for n in drop:
            if n not in stored:
                raise LabRefusal("E_ARGS", f"there is no reading named {n!r} to drop; stored: {sorted(stored)}")
        tb, bs = self.build(head["revision"]), self.basis
        refs = {pid: self.rid(pid) for pid in self.compare_ids()}
        recs = []
        for i, p in enumerate(points):
            if not isinstance(p, dict):
                raise LabRefusal("E_ARGS", f"points[{i}] must be {{name, kind, picks}}")
            for pk in p.get("picks") or []:
                if isinstance(pk, dict) and isinstance(pk.get("photo"), str) and pk["photo"] not in refs:
                    self.rid(pk["photo"])                         # names photo_image for a photo without a camera
            try:
                recs.append(RD.read_point(tb, bs, p.get("name"), p.get("picks"), kind=p.get("kind"), refs=refs))
            except ValueError as exc:
                raise LabRefusal("E_ARGS", f"points[{i}] ({p.get('name')}): {exc}") from None
        tag = self._next("read")
        texts, images = {}, []
        for i, rec in enumerate(recs):      # the index keeps "Q" and "q" apart on a case-insensitive file system
            path = RD.pick_sheet(bs, rec, self.work / "readings" / f"{tag}-{i}-{rec['name']}.png")
            images.append(self._image(path, "reading", ref=tag, reading=rec["name"], revision=head["revision"]))
            texts[rec["name"]] = RD.reading_text(rec)
        replaced = {}
        with self.state_lock:
            st = self.state.setdefault("readings", {})
            log = self.state.setdefault("reading_log", [])
            if call_id in self._unseen:         # the SDK stopped waiting: Astra never sees these (mark_unseen)
                log.append({"call_id": call_id, "id": tag, "action": "unseen", "names": [r["name"] for r in recs],
                            "drop": list(drop)})
                drop, recs = [], []
            for n in drop:
                log.append({"call_id": call_id, "id": tag, "action": "dropped", "name": n, "was": st.pop(n)})
            for rec in recs:
                n = rec["name"]
                was = st.get(n)
                if was is not None:
                    replaced[n] = reading_change(was, rec)
                log.append({"call_id": call_id, "id": tag, "action": "replaced" if was is not None else "read",
                            "name": n, **({"was": was} if was is not None else {})})
                st[n] = {**rec, "id": f"{tag}-{n}", "call_id": call_id, "revision": head["revision"]}
            names_now = sorted(st)
            now = dict(st)
        self.save()
        out = {"status": "ok", "read_id": tag, "readings": texts}
        if replaced:
            out["replaced"] = replaced
        if drop:
            out["dropped"] = list(drop)
        held = self._held_readings(head["revision"], set(replaced) | set(drop), now)
        if held:
            out["head_nodes"] = held
        lone = unused_readings(self.design(head["revision"]), self.fit_result(head["revision"]),
                               {rec["name"]: now[rec["name"]] for rec in recs}, near_read=True)
        if lone:                    # a reading no node of the head is named for, against its nearest node
            out["no_node"] = lone
        out["stored"] = names_now
        out["images"] = images
        return out

    def _held_readings(self, rid: int, names: set, readings: dict) -> str:
        """The nodes of revision rid's design that hold a reading of ``names`` that is no longer the stored one
        (geometry.stale_reads: a node keeps the reading it was set from), as one fact: "head r3: node K holds the
        earlier reading K (read003-K: X 70.04 +- 0.21); node D holds reading D (read002-D), now dropped"; "" for none
        (review2-lab-e2-1 P2-1: a re-read 0.5 mm out left node R on the old reading with nothing saying so)."""
        design = self.design(rid)
        stale = G.stale_reads(design, readings)
        items = []
        for n in sorted(k for k in stale if k in names):
            r = design["nodes"][n][2]["read"]
            what = f"{r.get('id') or n}, X {RD.fixed(r['X'], 2)}"
            items.append(f"node {n} holds the earlier reading {n} ({what})" if stale[n] == "replaced" else
                         f"node {n} holds reading {n} ({what}), now dropped")
        return f"head r{rid}: " + "; ".join(items) if items else ""

    def compare(self, call_id: str, photos=None, crop=None, zoom=None, points=None) -> dict:
        zooms = self._zoom_arg(zoom)
        if photos == [] and not zooms:
            raise LabRefusal("E_ARGS", "photos [] shows nothing: name photos, null for the default set, or zoom")
        photos = self._photos_arg(photos, low=0 if zooms else 1)
        if crop not in (None, "temple", "full"):
            raise LabRefusal("E_ARGS", "crop must be temple, full or null")
        if self.head_record() is None:
            raise LabRefusal("E_STATE", "the scene holds no applied revision")
        for p in points or []:
            self.rid(p["photo"])
        if points and photos is None:      # seeing picks the default set at compare time (and refuses a point beyond)
            raise LabRefusal("E_ARGS", f"points_px are charted on the photos a compare shows in full, and photos null "
                                       f"is chosen at compare time (the target photos whose temple shows at least "
                                       f"{TEMPLE_ROI_MIN_PX:,} native px): name the photos, or points_px null")
        return self._compare(call_id, photos, by="astra", crop=crop or "temple", points=points, zoom=zooms,
                             tool="photo_compare")

    def _judge(self, judgement, call_id: str, tool: str) -> tuple[str | None, str | None]:
        """Check and record the call's judgement (state.check_judgement: E_JUDGEMENT): (the compare_id it was stored
        for, or None; state.judgement_not_due's fact when one was given while none was due, so it was not stored)."""
        with self.state_lock:
            rec = S.check_judgement(self.state, judgement, fingerprint_of=self.fingerprint_of)
            S.record_judgement(self.state, rec, call_id=call_id, tool=tool)
            return (rec["compare_id"] if rec else None), (S.judgement_not_due(self.state, judgement) if rec is None
                                                         else None)

    def _judged(self, judgement, call_id: str, tool: str, body) -> dict:
        """An action tool's call: its judgement first (_judge, saved), then ``body()``. The result states what became
        of a judgement given (review2-lab-e2-1 P3-1, review2-lab-e2-2 P3-5: a judgement resent after a refused call,
        with another best_checkpoint, was dropped silently): "judgement" = judgement_not_due's fact when it was not
        stored, and "judgement of cmpX stored" on a refusal (raised or returned) after it was stored."""
        stored, not_due = self._judge(judgement, call_id, tool)
        self.save()
        fact = not_due or (f"judgement of {stored} stored" if stored else None)
        try:
            out = body()
        except LabRefusal as e:
            if fact:
                e.fields.setdefault("judgement", fact)
            raise
        if not_due or (fact and out.get("status") in ("refused", "error")):
            out["judgement"] = fact
        return out

    def _host_error(self, stage: str, code: str, exc: Exception, call_id: str | None) -> dict:
        """A fit or build that raised something other than a TempleError (fit.py's "nnls: no convergence", a
        LinAlgError; review2-lab-e2-1 P3-2: the SDK's generic error then lost the result and its "object":
        "recorded"): a refusal of this call with the exception's type and text (facts only), kept in the state's
        "host_errors" for the owner."""
        msg = f"the {stage} raised {type(exc).__name__}: {str(exc)[:240]}"
        with self.state_lock:
            self.state.setdefault("host_errors", []).append({"call_id": call_id, "stage": stage, "message": msg})
        return {"status": "refused", "code": code, "stage": stage, "edit": None, "message": msg}

    def _fit_build(self, design: dict, call_id: str | None = None) -> tuple:
        """(FitResult, build) of a design, or a refusal dict: the fit's own (stage "fit": E_FIT_SHAPE, E_FIT) or the
        spec's parse inside the fit (stage "spec": E_SHORT_EDGE, E_SPEC, E_OUTLINE...; v3p3 e2e P3-2: a parse refusal
        read as stage "fit"), with its fields (REFUSAL_FIELDS) and, for a node it names (an E_SHORT_EDGE crossing at a
        node that "cannot move"), outline_facts; or the builder's (stage "build") with its facts only (no remedy
        added, WP6_V3 P6); any other exception of either is _host_error's (E_FIT / E_BUILD; WorkerFailed stays fatal,
        though neither calls the worker)."""
        try:
            fit = FIT.fit_spec(design, readings=self.readings())
        except TempleError as e:
            out = {"status": "refused", "code": e.code, "stage": "fit" if e.code.startswith("E_FIT") else "spec",
                   "edit": None, "message": brief(e.message, FIT_REFUSAL_MAX)}
            fields = {k: v for k, v in e.fields.items() if k in REFUSAL_FIELDS}
            if fields:
                out["fields"] = fields
            facts = outline_facts(design, e.fields.get("refs") or []) if e.code == "E_SHORT_EDGE" else []
            if facts:
                out["outline"] = facts
            return out, None
        except WorkerFailed:
            raise
        except Exception as e:  # noqa: BLE001 - stated as this call's refusal, never the SDK's generic error
            return self._host_error("fit", "E_FIT", e, call_id), None
        try:
            tb = build_temple(fit.spec)
        except TempleError as e:
            return {"status": "refused", "code": e.code, "stage": "build", "edit": None, "message": brief(e.message),
                    "fields": {k: v for k, v in e.fields.items() if k in REFUSAL_FIELDS[:8]}}, None
        except WorkerFailed:
            raise
        except Exception as e:  # noqa: BLE001 - as the fit's
            return self._host_error("build", "E_BUILD", e, call_id), None
        return fit, tb

    def edit(self, call_id: str, parent_revision: int, reset_layout: bool, edits: list, judgement, note: str,
             object: str | None = None, apply: bool = True, photos=None) -> dict:      # noqa: A002 - the tool's name
        return self._judged(judgement, call_id, "facet_edit", lambda: self._edit(
            call_id, parent_revision, reset_layout, edits, note, object, apply, photos))

    def _edit(self, call_id: str, parent_revision: int, reset_layout: bool, edits: list, note: str,
              object: str | None, apply: bool, photos) -> dict:      # noqa: A002 - the tool's name
        parent = S.revision(self.state, parent_revision)
        if not isinstance(note, str) or len(note) > NOTE_MAX:
            raise LabRefusal("E_ARGS", f"note must be text of at most {NOTE_MAX} characters")
        if object is not None and (not isinstance(object, str) or not object.strip() or len(object) > S.OBJECT_MAX):
            raise LabRefusal("E_ARGS", f"object must be text of 1..{S.OBJECT_MAX} characters, or null")
        photos = self._photos_arg(photos)
        base = self.design(parent["id"])
        facts: dict = {}
        try:
            design, notes = G.apply_edits(base, edits, reset_layout=bool(reset_layout), readings=self.readings(),
                                          facts=facts)
        except G.EditError as e:
            return {**e.to_dict(), "parent": parent["id"]}
        except TempleError as e:
            return {"status": "refused", "code": e.code, "edit": None, "stage": "spec", "message": e.message}
        S.check_object(self.state, base, design, object)             # E_OBJECT (state.object_due)
        entry = None
        if object is not None:
            with self.state_lock:
                entry = S.record_object(self.state, object, call_id, spec=design, parent=parent["id"])
            self.save()
        fit, tb = self._fit_build(design, call_id)
        if tb is None:
            self.save()
            return {**fit, "parent": parent["id"], **({"object": "recorded"} if object is not None else {})}
        rid, new = self._register(design, parent=parent["id"], kind="edit", ops=len(edits), note=note,
                                  call_id=call_id, fit=fit)
        if entry is not None:
            with self.state_lock:                     # the note's revision (state.object_origin)
                entry["revision"] = rid
        self._cache_build(rid, tb)
        out = {"status": "built", "revision": rid, "parent": parent["id"],
               "design": self.state["revisions"][rid]["sha256"][:8],
               "diff": G.diff_text(G.diff_specs(base, design), reset=bool(reset_layout))}
        if object is not None:
            out["object"] = "recorded"
        if not new:
            out["note"] = f"this design is revision {rid} already"
        moved = (facts.get("deleted_creases") or []) + (facts.get("outline_bends") or [])
        stated = {**({"deleted_creases": "; ".join(facts["deleted_creases"])} if facts.get("deleted_creases") else {}),
                  **({"outline_bends": facts["outline_bends"]} if facts.get("outline_bends") else {})}
        notes = [n for n in notes if n not in moved]        # stated on the receipt's own lines (geometry facts)
        if notes:
            out["notes"] = notes
        out["receipt"] = {**stated, **self._receipt(tb, fit, self.raw_edges(rid))}
        unused = unused_readings(design, fit, self.readings())
        if unused and unused != unused_readings(base, self.fit_result(parent["id"]), self.readings()):
            out["unused_readings"] = unused         # what changed against the parent (facet_inspect lists them all)
        out.update(self._vs_head(design, tb, fit, parent["id"]))
        tag = self._next("edit")
        images = [self._chart_image(rid, tb, tag)]
        if apply:
            current = self.head_record()
            r = {"status": "ok", "result": {}} if current and current["revision"] == rid else \
                self._apply(call_id, rid, tool="facet_edit")                   # the head holds it already: compare
            if r["status"] != "ok":
                res = r.get("result") or {}
                out.update(status="refused", stage="blender", code=res.get("code") or r.get("code") or r["status"],
                           message=res.get("message") or res.get("error") or r.get("message") or r.get("traceback"),
                           checkpoint=self.s.head["id"])
                out["images"] = images
                self.save()
                return out
            out.update(status="applied", checkpoint=self.s.head["id"],
                       blender=self._blender_receipt(r.get("result") or {}, RP.receipt(tb)["lines"], tb))
            cmp = self._compare(call_id, photos, by="astra", tool="facet_edit")
            if cmp["status"] == "ok":
                out["compare_id"] = cmp["compare_id"]
                out["photos"] = cmp["photos"]
                if cmp.get("left_out"):
                    out["left_out"] = cmp["left_out"]
                images += cmp["images"]
            else:
                out["compare"] = {k: cmp.get(k) for k in ("status", "code", "message")}
        out["images"] = images
        self.save()
        return out

    def _vs_head(self, design: dict, tb, fit, parent: int) -> dict:
        """What a new revision changes against the CURRENT HEAD's revision (not its parent; applying a variant with no
        edits diffs "no change" against the variant), in the receipt's order: ``solved`` (solved_moves),
        ``crease_vs_head`` (creases built with the other shape than in the head: crease_changes, with a solved corner
        whose stored reading the fit compared), ``edges_vs_head`` (creases that appear or go flat: edge_changes) and
        ``diff_vs_head`` (when the parent is not the head); {} when the scene holds no applied revision or nothing
        changed."""
        head = self.head_record()
        if head is None:
            return {}
        hr = head["revision"]
        out = {}
        moved = solved_moves(fit, self._fit_nodes(hr))
        if moved:
            out["solved"] = moved
        changes = crease_changes(self.build(hr), tb, fit)
        if changes:
            out["crease_vs_head"] = f"r{hr}: " + "; ".join(changes)
        edges = edge_changes(self.build(hr), tb)
        if edges:
            out["edges_vs_head"] = f"r{hr}: " + "; ".join(edges)
        if parent != hr:
            out["diff_vs_head"] = f"r{hr}: " + G.diff_text(G.diff_specs(self.design(hr), design))
        return out

    # ---------------------------------------------------------------------------------------------- variants
    def variants(self, call_id: str, parent_revision: int, variants: list, photos: list, judgement) -> dict:
        return self._judged(judgement, call_id, "facet_variants",
                            lambda: self._variants(call_id, parent_revision, variants, photos))

    def _variants(self, call_id: str, parent_revision: int, variants: list, photos: list) -> dict:
        parent = S.revision(self.state, parent_revision)
        photos = self._variant_photos(photos)
        if not isinstance(variants, list) or not VARIANTS_MIN <= len(variants) <= VARIANTS_MAX:
            raise LabRefusal("E_ARGS", f"variants must be {VARIANTS_MIN}..{VARIANTS_MAX} {{label, edits}}")
        labels = [v.get("label") for v in variants]
        for lb in labels:                       # the worker names a label's render files by its safe name
            if not isinstance(lb, str) or not G.NAME_RE.match(lb) or render_safe_name(lb) != lb:
                raise LabRefusal("E_ARGS", f"variant label {lb!r}: a letter, then up to 23 letters, digits, _ or -, "
                                           "not ending in _")
        # the render files are named <photo>--<safe name> beside <photo>--head on a case-insensitive file system
        if len({render_safe_name(lb).lower() for lb in labels} | {"head"}) != len(labels) + 1:
            raise LabRefusal("E_ARGS", "variant labels must be distinct (ignoring case) and not 'head'")
        head = self.head_record()
        if head is None:
            raise LabRefusal("E_STATE", "the scene holds no applied revision")
        vid = self._next("var")
        base = self.design(parent["id"])
        head_tb = self.build(head["revision"])
        head_nodes = self._fit_nodes(head["revision"])
        items, built, same = [], {}, {}
        for v in variants:
            item = {"label": v["label"]}
            try:
                design, _notes = G.apply_edits(base, v.get("edits") or [], readings=self.readings())
            except G.EditError as e:
                item.update(revision=None, stage="edit", code=e.code, edit=e.index, message=brief(e.message))
                items.append(item)
                continue
            except TempleError as e:
                item.update(revision=None, stage="spec", code=e.code, edit=None, message=brief(e.message))
                items.append(item)
                continue
            fit, tb = self._fit_build(design, call_id)
            if tb is None:
                item.update(revision=None, stage=fit["stage"], code=fit["code"], edit=None, message=fit["message"],
                            **({"outline": fit["outline"]} if fit.get("outline") else {}))
                items.append(item)
                continue
            rid, _new = self._register(design, parent=parent["id"], kind="variant", label=v["label"],
                                       ops=len(v.get("edits") or []), call_id=call_id, fit=fit)
            self._cache_build(rid, tb)
            d = self.delta(head["revision"], rid)
            item.update(revision=rid, design=self.state["revisions"][rid]["sha256"][:8], vs_head_mm=_r(d["value_mm"]))
            # an arm whose design is the head's or an earlier arm's is that model: no render, no panels (its own
            # would repeat them; review2-lab-e2-1 P2-3)
            twin = "head" if rid == head["revision"] else next(
                (lb for lb, b in built.items() if b[0] == rid and lb not in same), None)
            if twin is not None:
                item["same_as"] = twin
                same[v["label"]] = twin
            else:
                shift = crease_shift(d, head["revision"] <= rid)
                if shift:                 # the creases beside vs_head_mm (v3p3 astra P2-2: 0.60 for an 8.37 mm move)
                    item["creases_vs_head_mm"] = shift
                item.update(fit=fit_brief(fit, head_nodes), creases=self._crease_brief(tb))
                changes = crease_changes(head_tb, tb, fit)
                if changes:               # against the head, as facet_edit's crease_vs_head
                    item["crease_vs_head"] = "; ".join(changes)
                edges = edge_changes(head_tb, tb)
                if edges:                 # ... and its edges_vs_head
                    item["edges_vs_head"] = "; ".join(edges)
                if _notes:                # the outline knots apply_edits moved with a corner (geometry module doc)
                    item["notes"] = _notes
            built[v["label"]] = (rid, self.spec(rid), tb)
            items.append(item)
        pairs = {}
        names = list(built)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ra, rb = built[a][0], built[b][0]
                pairs[tuple(sorted((a, b)))] = 0.0 if ra == rb else self.delta(ra, rb)["value_mm"]
        out = {"status": "ok", "variants_id": vid, "parent": parent["id"], "head_revision": head["revision"],
               "variants": items, "pairwise_mm": {"|".join(k): _r(v) for k, v in pairs.items()}}
        images, rendered, lines = [], [], {}
        if built:
            images, render_status, rendered, lines = self._variant_sheets(call_id, vid, parent["id"], photos, head,
                                                                          built, items, same)
            if render_status:
                out["render"] = render_status
        # precondition 2 counts only variants Astra was shown: built AND rendered at every photo of a call whose
        # result reached Astra (a call the SDK timed out is unseen: mark_unseen)
        spread = G.spread_triple(rendered, pairs)
        out["spread_ok"] = spread is not None
        rec = {"id": vid, "call_id": call_id, "parent": parent["id"], "photos": photos, "seq": self.state["seq"],
               "items": [{k: it.get(k) for k in ("label", "revision", "code", "vs_head_mm")} for it in items],
               "pairwise": out["pairwise_mm"], "rendered": rendered, "spread": spread, **lines}
        with self.state_lock:
            if call_id in self._unseen:
                rec["unseen"] = True
            self.state["variants"].append(rec)
        out["images"] = images
        self.save()
        return out

    @staticmethod
    def _variant_region(models: dict) -> tuple[np.ndarray, str]:
        """(P (K, 3) chart points (d, y, X) the variant panels show, what they are): the outer-surface lattice points
        (geometry.surface_grid, 0.5 mm) of every model inside the (d, y) box where an arm differs from the head (|dX|
        >= VARIANT_DIFF_MM, or inside one outline only: an arm that moves an outline chain), widened by
        VARIANT_ZONE_MARGIN_MM and to at least VARIANT_SPAN_MIN_MM a side; with no difference, the box of every
        model's faceted faces (arrangement faces of a facet) widened alike; with no facet either, the whole temple.
        The instructions judge a bracket where its arms differ; a smaller box shows larger on a page."""
        grids = {name: G.surface_grid(tb) for name, tb in models.items()}
        hd, hy, hX, _g = grids["head"]
        hi_d, hi_y = np.round(hd / G.GRID_STEP_MM).astype(int), np.round(hy / G.GRID_STEP_MM).astype(int)
        diff = []
        for name, (d, y, X, _gr) in grids.items():
            if name == "head":
                continue
            ki_d, ki_y = np.round(d / G.GRID_STEP_MM).astype(int), np.round(y / G.GRID_STEP_MM).astype(int)
            all_d, all_y = np.union1d(hi_d, ki_d), np.union1d(hi_y, ki_y)
            A = np.full((len(all_d), len(all_y)), np.nan)
            B = np.full_like(A, np.nan)
            A[np.ix_(np.searchsorted(all_d, hi_d), np.searchsorted(all_y, hi_y))] = hX
            B[np.ix_(np.searchsorted(all_d, ki_d), np.searchsorted(all_y, ki_y))] = X
            with np.errstate(invalid="ignore"):
                moved = (np.abs(A - B) >= VARIANT_DIFF_MM) | (np.isfinite(A) != np.isfinite(B))
            i, j = np.nonzero(moved)
            diff += list(zip(all_d[i] * G.GRID_STEP_MM, all_y[j] * G.GRID_STEP_MM))
        what = "where the arms differ"
        if not diff:
            what = "the faceted zone"
            for tb in models.values():
                V2 = np.asarray(tb.arr.V, float)
                diff += [tuple(V2[v]) for fi, f in enumerate(tb.arr.faces) if tb.face_label[fi] != "base" for v in f]
        lo = hi = None
        if diff:
            Q = np.asarray(diff, float)
            lo, hi = Q.min(axis=0) - VARIANT_ZONE_MARGIN_MM, Q.max(axis=0) + VARIANT_ZONE_MARGIN_MM
            c, half = (lo + hi) / 2.0, np.maximum((hi - lo) / 2.0, VARIANT_SPAN_MIN_MM / 2.0)
            lo, hi = c - half, c + half
        else:
            what = "the whole temple"
        pts = []
        for d, y, X, _gr in grids.values():
            D, Y = np.meshgrid(d, y, indexing="ij")
            keep = np.isfinite(X) if lo is None else np.isfinite(X) & (D >= lo[0]) & (D <= hi[0]) & (Y >= lo[1]) & \
                (Y <= hi[1])
            pts.append(np.column_stack([D[keep], Y[keep], X[keep]]))
        return (np.vstack(pts) if pts else np.zeros((0, 3))), what

    def _variant_box(self, rid: str, P: np.ndarray, hinge: dict) -> tuple[int, tuple | None]:
        """(side, native box) of a photo's variant panels: the region's points P (_variant_region) on the temple whose
        outer face the camera sees there (the charted one when both or neither do), placed as the worker places it
        (the spec's opening, the -X temple its mirror) and opened by the photo's pose, projected; padded
        VARIANT_PAD_PX and clipped to the photo. None when the region is not in the photo (a box under
        VARIANT_BOX_MIN_PX a side)."""
        bs = self.basis
        cam, frame, pose = bs.camera(rid), bs.frame, bs.pose(rid)
        H, W = bs.shape(rid)
        if not len(P):
            return 1, None
        z, opening = float(hinge["z_mm"]), _opening(hinge)
        c = P.mean(axis=0)
        facing = []
        for s in (1, -1):
            Q = PO.chart_to_posed([c, c + [0.0, 0.0, 1.0]], s, z, pose, opening)
            facing.append(bool(PO.faces_camera(Q[:1], Q[1] - Q[0], cam, frame)[0]))
        side = -1 if facing == [False, True] else 1
        try:
            uv = project_mm(PO.chart_to_posed(P, side, z, pose, opening), cam, frame)
        except ValueError:                                # a point behind the camera's near plane
            return side, None
        uv = uv[np.all(np.isfinite(uv), axis=1)]
        if not len(uv):
            return side, None
        x0, y0 = (max(0, int(math.floor(float(v))) - VARIANT_PAD_PX) for v in uv.min(axis=0))
        x1 = min(W, int(math.ceil(float(uv[:, 0].max()))) + VARIANT_PAD_PX)
        y1 = min(H, int(math.ceil(float(uv[:, 1].max()))) + VARIANT_PAD_PX)
        if x1 - x0 < VARIANT_BOX_MIN_PX or y1 - y0 < VARIANT_BOX_MIN_PX:
            return side, None
        return side, (x0, y0, x1, y1)

    def _variant_views(self, pids: list, models: dict, call_id: str, pairs: int | None = None) -> tuple[list, dict]:
        """Worker views (crop camera with the photo's pose, one pass) and the compositing record of each photo: the
        box of _variant_box over _variant_region (else the latest full compare's crop box) at sheets.variant_scale
        for ``pairs`` model pairs on the photo's one sheet (default one per model of ``models``: the head and the arms
        drawn; a refused arm or one equal to another model has no pair); the photo
        stretch, background, gains and environment of the latest full compare that showed it. A target photo's panels
        are the native_linear pass with the job's white-balanced target gain and the photo's stretch; a photo of another
        colour's are the gloss_black pass with the gloss gain and stretch that compare used for it (its record's
        ``gloss``; an older record without one: the job's gloss_black gain, and the photo's stretch for a dark
        colourway as seeing does)."""
        bs = self.basis
        origin = np.asarray(self.seeing.bounds(call_id)["origin_mm"] or (0.0, 0.0, 0.0), float)
        region, what = self._variant_region(models)
        views, recs, render_params = [], {}, None
        for pid in pids:
            rid = self.rid(pid)
            last = next((c for c in reversed(self.state["compares"]) if pid in S.shown_in_full(c)), None)
            if last is None:
                raise self.never_compared(pid, "was never compared in full")
            report = read(last["report"])
            prec = report["photos"][rid]
            side, box = self._variant_box(rid, region, models["head"].spec.hinge)
            shows = what
            if box is None:
                box, shows = tuple(int(v) for v in prec["crop"]["box"]), "the compare's crop"
            x0, y0, x1, y1 = box
            k = SH.variant_scale(x1 - x0, y1 - y0, pairs or len(models))
            ccam = BC.crop_camera(bs.camera(rid), x0, y0, k)
            cshape = (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))
            gains = report["gains"]
            lo, hi = (float(v) for v in prec["stretch"])
            cw = bs.colourway(rid)
            if cw == "target":
                look = {"pass": "native_linear", "gain": gains["colourways"]["target"]["native_linear"]["gain"],
                        "render_stretch": (lo, hi), "what": f"native render, photo stretch {lo:.0f}-{hi:.0f}"}
            else:
                gl, glj = prec.get("gloss") or {}, gains.get("gloss_black") or {}
                gain = gl.get("gain", glj.get("gain"))
                if gain is None:
                    raise self.never_compared(pid, "has no full compare with a gloss_black gain")
                if gl.get("stretch"):
                    gst = gl["stretch"]
                else:
                    dark = (gains["colourways"].get(cw) or {}).get("dark", True)
                    gst = (lo, hi) if dark or not glj.get("stretch") else glj["stretch"]
                a, b = (float(v) for v in gst)
                look = {"pass": "gloss_black", "gain": float(gain), "render_stretch": (a, b),
                        "what": f"gloss-black render gain {float(gain):.2f} stretch {a:.0f}-{b:.0f}"}
            views.append({"name": pid, "camera": BC.to_blender(ccam, bs.frame, origin, cshape), "pose": bs.pose(rid),
                          "passes": [look["pass"]]})
            ppm = prec.get("px_per_mm")
            recs[pid] = {"rid": rid, "box": (x0, y0, x1, y1), "k": k, "shape": cshape, "ccam": ccam, "side": side,
                         "shows": shows,
                         "pose": bs.pose(rid), "stretch": (lo, hi), "background": prec["background"],
                         "colour": self._colour(pid),
                         "px_per_mm": float(ppm) * k if ppm is not None and np.isfinite(ppm) else None, **look}
            if render_params is None:
                env = report.get("environment")
                render_params = self.seeing._render_params(self.rig, env, gains.get("light_frame"))
        return views, {"photos": recs, "render": render_params}

    def _head_scene(self, call_id: str):
        """(the head's exported scene as the compare reads it, without its two temple bodies; the GLB's part roles):
        the occluder of the variant edges (seeing's export and fit mesh, both cached by fingerprint; the basis' skip
        list leaves out the decals, as in the compare)."""
        bs = self.basis
        exp = self.seeing.export_head(call_id)
        if exp.get("status") != "exported":
            raise ValueError(f"the head does not export: {exp.get('code')} {exp.get('message')}")
        mesh = self.seeing.fit_mesh(exp["path"], exp["sha256"], skip=getattr(bs, "skip", ()), frame=bs.frame)
        roles = PO.part_roles(exp["path"])
        bodies = [b for b in (PO.temple_body(mesh, s, roles) for s in (1, -1)) if b is not None]
        if len(bodies) != 2:
            raise ValueError(f"the head export has {len(bodies)} temple bodies, not 2")
        keep = ~np.isin(mesh.part, bodies)
        return replace(mesh, F=mesh.F[keep], part=mesh.part[keep]), roles

    def _variant_edges(self, photos: dict, models: dict, call_id: str) -> tuple[dict, dict]:
        """Each model's model edges (build.model_edges, capped_edges) at each photo, as the compare overlay draws
        them (WP6_V3 §5: by identity, deg_at per point, soft dashed): ({(photo, model): compare.edges_with_runs |
        None}, info). ``models`` = {name: TempleBuild} (the head and the built variants). Both temples are placed as
        the worker places them (TempleBuild.world at the spec's opening, the -X side its mirror; the edges by
        pose.chart_to_posed with the same opening), opened by the photo's temple pose and depth-tested
        (compare.edge_runs_posed) against the head's exported scene with its two temple bodies swapped for the
        model's (_head_scene); when that scene cannot be read, against the two temples alone (info["occlusion"]
        says so). A WorkerFailed of the head's export (the worker died and was not recovered) is raised: it ends
        the run failed_worker, as from any tool. One task per photo and model on CREASE_THREADS threads."""
        bs = self.basis
        info: dict = {"occlusion": "scene"}
        try:
            rest, roles = self._head_scene(call_id)
        except WorkerFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - the edges are still drawn, depth-tested on the temples alone
            rest = roles = None
            info["occlusion"] = f"temples only ({type(exc).__name__}: {str(exc)[:80]})"
        worlds, edges, failed = {}, {}, {}
        for name, tb in models.items():
            try:
                worlds[name] = {s: tb.world(s) for s in (1, -1)}
                edges[name] = capped_edges(BLD.model_edges(tb))
            except (TypeError, ValueError) as exc:            # e.g. an opened temple without a hinge axis
                failed[name] = f"{type(exc).__name__}: {exc}"[:120]
        occ = {}
        for pid, c in photos.items():
            if rest is not None:
                rp = PO.posed(rest, c["pose"], roles) if c["pose"] else rest
                occ[pid] = (rp.V, rp.F)
            else:
                occ[pid] = None

        def one(task):
            pid, name = task
            c = photos[pid]
            tb = models[name]
            try:
                pp = PO.parse_pose(c["pose"]) if c["pose"] else {}
                bodies = []
                for s in (1, -1):
                    Wv, _p, tris = worlds[name][s]
                    th, axis = pp.get(s, (0.0, (0.0, 0.0)))
                    bodies.append((PO.rot_about_hinge(Wv, s, th, axis), tris))
                h = tb.spec.hinge
                z, opening = float(h["z_mm"]), _opening(h)
                polys, vals, recs = [], [], []
                for e in edges[name]:
                    for s in (1, -1):
                        polys.append(PO.chart_to_posed(e["pts"], s, z, c["pose"], opening))
                        vals.append(e.get("deg_at"))
                        recs.append({**e, "side": s})
                runs = C.edge_runs_posed(polys, bodies, occ[pid], c["ccam"], bs.frame, c["shape"], values=vals)
                return C.edges_with_runs(recs, runs)
            except Exception as exc:  # noqa: BLE001 - that panel is drawn without lines, labelled so
                failed[f"{pid}/{name}"] = f"{type(exc).__name__}: {exc}"[:120]
                return None
        tasks = [(pid, name) for pid in photos for name in worlds]
        out = {(pid, name): None for pid in photos for name in models}
        if tasks:
            with ThreadPoolExecutor(max_workers=min(CREASE_THREADS, len(tasks))) as ex:
                out.update(zip(tasks, ex.map(one, tasks)))
        if failed:
            info["failed"] = failed
        return out, info

    def _variant_sheets(self, call_id: str, vid: str, parent: int, photos: list, head: dict, built: dict,
                        items: list, same: dict) -> tuple[list, dict | None, list, dict]:
        """The worker's read-only render of the head and the built variants that are models of their own (``same``
        = {label: "head" or an earlier label} for an arm whose design is that one's: not rendered again; one pass per
        photo: _variant_views), composited into ONE sheet per photo (sheets.variant_sheet): the photo pair (the photo
        | the photo with the head's edges) and per model drawn a pair, its clean render beside (or above) the lined
        one (the photo's outline green and that model's own model edges labelled with their angles: _variant_edges).
        An arm refused (by the host or Blender) or equal to another model has no panels: the sheet's notes line names
        it, its facts are in the text (review2-lab-e2-1 P2-3: such arms took a model slot each). The title gives the
        box, k and the px/mm shown. Returns (sheet paths, a render-status note or None, the labels rendered at every
        photo (an arm equal to a rendered arm counts: its model is on the sheet; an arm equal to the head never does:
        it shows no alternative, so it cannot make a spread or a stuck finish), {edges: {photo: {model: edges drawn |
        None}}, edge_occlusion} for the state record)."""
        unique = {lb: b for lb, b in built.items() if lb not in same}
        models = {"head": self.build(head["revision"]), **{lb: tb for lb, (_rid, _spec, tb) in unique.items()}}
        views, comp = self._variant_views(photos, models, call_id, pairs=len(models))
        out_dir = self.root / "work" / "temple_lab" / "variants" / vid
        params = {**(comp["render"] or {}), "views": views, "passes": ["native_linear"],
                  "variants": [{"label": lb, "spec": spec} for lb, (_rid, spec, _tb) in unique.items()],
                  "out_dir": str(out_dir)}
        deadline = hard_timeout_s("facet_variants", variants=len(unique), photos=len(photos))
        r = self.s.call_read("facet_variants", "temple_variants_render", params, deadline_s=deadline,
                             call_id=call_id)
        if r["status"] != "ok":
            return [], {"status": r["status"], "message": r.get("message")}, [], {}
        res = r["result"]
        if res.get("status") != "ok":
            return [], {"status": res.get("status"), "code": res.get("code"), "message": res.get("message")}, [], {}
        files = {}
        for v in res["render"]["views"]:
            pid = v["name"].split("--", 1)[0]
            f = (v.get("files") or {}).get(comp["photos"][pid]["pass"]) if pid in comp["photos"] else None
            if f:
                files[v["name"]] = f["path"]
        shown = {lb for lb in unique if (res["variants"].get(lb) or {}).get("status") == "ok"
                 and all(f"{pid}--{lb}" in files for pid in photos)}
        if all(f"{pid}--head" in files for pid in photos):
            shown.add("head")
        # an arm equal to the head shows no alternative: never a rendered variant (finish preconditions 2 and 5;
        # v3p3 e2e P3-4, safety P3-1: a no-op arm stood in for the head in the spread, and arms that all equal the
        # head opened the stuck finish); one equal to a rendered arm is that model on the sheet
        rendered = [lb for lb in built if same.get(lb) != "head" and (lb in shown or same.get(lb) in shown)]
        status = None
        blender = {lb: b for lb, b in res["variants"].items() if b.get("status") != "ok"}
        if blender:
            status = {"blender_refused": {lb: f"{b.get('code')}: {str(b.get('message'))[:120]}"
                                          for lb, b in blender.items()}}
        drawn_models = {"head": models["head"],
                        **{lb: tb for lb, (_rid, _spec, tb) in unique.items() if lb in res["variants"]
                           and res["variants"][lb].get("status") == "ok"}}
        left = []                           # the arms with no panels of their own, named on every sheet
        for it in items:
            lb = it["label"]
            if lb in same:
                left.append(f"{lb} = {same[lb]}")
            elif it.get("revision") is None:
                left.append(f"{lb} refused {it.get('code')}")
            elif lb in blender:
                left.append(f"{lb} refused by Blender {blender[lb].get('code')}")
            elif lb not in shown:
                left.append(f"{lb} not rendered at every photo")
        page_notes = f"no panels of their own (facts in the text): {'; '.join(left)}" if left else ""
        edges, info = self._variant_edges(comp["photos"], drawn_models, call_id)
        notes = ([f"depth-tested on the {info['occlusion']}"] if info["occlusion"] != "scene" else []) + \
            ([f"none drawn for {sorted(info['failed'])[:4]}"] if info.get("failed") else [])
        if notes:
            status = {**(status or {}), "edges": "; ".join(notes)}
        paths = []
        bs = self.basis
        for pid in photos:
            c = comp["photos"][pid]
            pc, mc = Seeing.photo_at(bs, c["rid"], c["box"], c["k"], c["shape"])
            lo, hi = c["stretch"]
            rlo, rhi = c["render_stretch"]
            photo_im = Image.fromarray(C.apply_stretch(pc, lo, hi))

            def lined(im, name):
                """A copy of im with that model's edges drawn and labelled; (image, drawn?)."""
                e = edges.get((pid, name))
                im = im.copy()
                if e is None:
                    return im, False
                C.draw_edges(im, e, labels=True)
                return im, True
            head_lined, ok = lined(photo_im, "head")
            photo_row = (f"photo {pid}{' ' + c['colour'] if c['colour'] else ''} (stretch {lo:.0f}-{hi:.0f})",
                         photo_im, "photo + the head's edges" + ("" if ok else " (none drawn)"), head_lined)

            def model_row(name, label):
                rgb, alpha = C.read_linear(files[f"{pid}--{name}"])
                clean = Image.fromarray(C.apply_stretch(C.composite_render(rgb, alpha, c["gain"], c["background"]),
                                                        rlo, rhi))
                im, ok_ = lined(SH.with_outline(clean, mc), name)
                return (label, clean, im, "" if ok_ else "no edges drawn")
            rows = [model_row("head", f"head r{head['revision']} ({self.s.head['id']})")]
            for it in items:
                lb = it["label"]
                if lb in shown:
                    cm = self.delta(head["revision"], it["revision"])["crease_mm"]
                    rows.append(model_row(lb, f"{lb}: r{it['revision']}, {it['vs_head_mm']:.2f} mm from head"
                                          + (f", creases up to {cm:.1f} mm" if cm >= it["vs_head_mm"] + 0.5 else "")))
            view = str(bs.refs[c["rid"]].get("view_kind") or "view") + (f", {c['colour']}" if c["colour"] else "")
            x0, y0, x1, y1 = c["box"]
            ppm = f", {c['px_per_mm']:.1f} px/mm" if c.get("px_per_mm") else ""
            title = (f"variants {vid} of r{parent} at {pid} ({view}): {c['what']}; box [{x0},{y0},{x1},{y1}] "
                     f"({c['shows']}) k {c['k']:.2f}{ppm}")
            p = out_dir / f"sheet-{pid}.png"
            SH.variant_sheet(photo_row, rows, title, page_notes).save(p)
            paths.append(self._image(p, "variants", ref=vid, photo=pid, revision=head["revision"]))
        lines = {"edges": {pid: {name: None if edges.get((pid, name)) is None else
                                 sum(1 for e in edges[(pid, name)] if e["runs"]) for name in drawn_models}
                           for pid in photos},
                 "edge_occlusion": info["occlusion"]}
        return paths, status, rendered, lines

    def finish(self, call_id: str, status: str, best_checkpoint: str, summary: str, judgement, k: int) -> dict:
        return self._judged(judgement, call_id, "facet_finish",
                            lambda: self._finish(call_id, status, best_checkpoint, summary, k))

    def _finish(self, call_id: str, status: str, best_checkpoint: str, summary: str, k: int) -> dict:
        if status not in ("matched", "stuck"):
            raise LabRefusal("E_ARGS", "status must be matched or stuck")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_MAX:
            raise LabRefusal("E_ARGS", f"summary must be 1..{SUMMARY_MAX} characters")
        best = S.head_of_checkpoint(self.state, best_checkpoint, self.fingerprint_of)
        if best is None:
            raise LabRefusal("E_ARGS", f"best_checkpoint {best_checkpoint!r} holds no applied revision; applied: "
                                       f"{S.applied_checkpoints(self.state)[-12:]}")
        with self.state_lock:                       # a snapshot: the distinctness builds run outside the lock
            snap = json.loads(json.dumps({key: v for key, v in self.state.items() if key not in ("images",)}))
            has_object = bool(S.object_text(self.state))
        missing = S.finish_missing(snap, status=status, best_revision=best["revision"], k=int(k),
                                   distinct=self.distinct, camera_photos=self.compare_ids(), has_object=has_object)
        if missing:
            with self.state_lock:
                self.state["finish_refusals"] += 1
            self.save()
            return {"status": "refused", "code": "E_FINISH", "missing": missing,
                    "message": "the run continues; the preconditions named in missing are not met"}
        before = self.s.head["id"]
        if self.s.head["fingerprint"] != best["fingerprint"]:
            r = self.s.restore_checkpoint(best_checkpoint, call_id=call_id)
            with self.state_lock:
                self.state["events"].append({"call_id": call_id, "tool": "facet_finish", "mutating": True,
                                             "checkpoint_before": before, "checkpoint_after": self.s.head["id"]})
            if r.get("status") != "ok":
                self.save()
                return {"status": "error", "code": "E_RESTORE", "message": r.get("message")}
        exp = self.seeing.export_head(call_id)
        if exp.get("status") != "exported":
            self.save()
            return {"status": "error", "code": exp.get("code") or "E_EXPORT", "message": exp.get("message")}
        v = exp["validate_spec"]
        if not v["ok"]:
            with self.state_lock:
                self.state["finish_refusals"] += 1
            self.save()
            return {"status": "refused", "code": "E_EXPORT",
                    "message": "the run continues; the exported asset does not pass validate_spec",
                    "validate_spec": v}
        fin = {"call_id": call_id, "status": status, "checkpoint": self.s.head["id"],
               "best_checkpoint": best_checkpoint, "revision": best["revision"],
               "export": {"path": exp["path"], "sha256": exp["sha256"]}, "summary": summary,
               "validate_spec": {"ok": v["ok"], "errors": v["errors"], "warnings": v["warnings"]}}
        with self.state_lock:                       # a finish the SDK timed out never ends the run (mark_unseen)
            if call_id in self._unseen:
                self.state.setdefault("finish_unseen", []).append(fin)
            else:
                self.state["finish"] = fin
        self.save()
        return {"status": "accepted", "finish": status, "checkpoint": self.s.head["id"], "revision": best["revision"],
                "design": self.state["revisions"][best["revision"]]["sha256"][:8],
                "export": {"file": Path(exp["path"]).name, "sha256": exp["sha256"][:16]},
                "validate_spec": {"ok": v["ok"], "errors": v["errors"][:3], "warnings": len(v["warnings"]),
                                  "live_site_would_fail": v.get("live_site_would_fail", [])[:3]}}

