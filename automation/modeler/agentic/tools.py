"""The strict, versioned tool registry and the adapters to the reviewed modeler functions.

Tools receive logical artifact and revision IDs; the host resolves paths, enforces access roles and validates
arguments before anything runs. Geometry and material edits both create a new revision (``edit_program``); a build
runs the program in the worker, exports through ``modeler.export`` + ``bsa.contract``, observes through
``modeler.observe`` (fitted cameras, canonical renders through the worker, the actual AR renderer on the host) and
queues every image for the author. Proxy scores are advisory: nothing here vetoes an author's choice among compatible
revisions. There is no host shell, no arbitrary path read, no network tool.
"""
from __future__ import annotations

import contextlib
from datetime import timedelta
import difflib
import inspect
import io
import json
import math
from pathlib import Path
import re
import shutil
import threading
import traceback

import numpy as np
from PIL import Image

from bsa import contract as bsa_contract

from .. import author as mauthor
from .. import export as mexport
from ..candidates import MAX_MODULE_BYTES, MODULE_ORDER
from ..observe import AR_VIEWS, canonical_render_specs
from .artifacts import AUTHOR_VISIBLE, HOST_ONLY, SEALED, SYNTHETIC, ArtifactStore, atomic_write, contained, regular_file_or_raise
from .budget import Budget
from .config import DEFAULT_IMAGES_PER_REQUEST
from .executor import Ingested, Worker, WorkerError, WorkerRefused, program_set_sha256, result_is_untrusted_receipt, write_bundle
from .responses import function_output_item, image_block, text_block
from .state import LEASE_TTL_S, LeaseError, Store, iso, sha256_bytes

TOOLS_VERSION = "agentic_tools_v1"
STATUS_CLAIMS = ("improved", "best_effort", "no_further_progress")
MAX_EXTRA_VIEWS = 6
MAX_AR_VIEWS = 4
# the AR harness's pose bounds (ar/qa/provider-comparison-ar.html: yaw +/-80, pitch and roll +/-60; asset-back takes no angles)
AR_POSE_LIMITS = {"yaw": 80, "pitch": 60, "roll": 60}
# render_ar_views backgrounds: the harness checker (the ar sheet's) or a solid fixture of modeler/see_through.py FIXTURES
AR_BACKGROUNDS = {"checker": None, "skin": "#cba68d", "blue": "#3a4f6e"}
AR_VIEWS_TIMEOUT_S = 600        # an extra AR run is a few views: a hung Chromium ends well inside the lease ttl (archeck's default is 1800 s)
# the AR runtime's temple clip for a Modeling Auto asset. The harness (ar/qa/provider-comparison-ar.html) and the try-on handover
# (modeler/tryon.py clip_zm; modeler/job.py temple_clip_z_m_recommended) register templeClipLocalZM -0.14 in the exported GLB's frame,
# whose origin is the bridge underside; the renderer (ar/src/render/renderer.ts templeEndMaximumZM) then draws each arm back to
# max(base, min(-0.055, base + HIDDEN_TAIL_M, continuity start - 0.008)) = -0.115 m, each side's cutoff at max(that, its endpoint
# report), faded over END_FADE_M in front of it. Every test-pilot-002 harness render recorded -0.115 (templeEndMaximumZM in
# observe/ar/report.json), 25 mm ahead of the -0.14 the replies had stated; r0003/r0004 had gone on tip plaques and 'TOM FORD'
# lettering at z -145..-160 that no clip ever draws, and gl.set_temple_clip_z(-154), which no renderer reads. The build reply reads the
# value the harness recorded for the revision; these constants stand in only without one.
RUNTIME_TEMPLE_CLIP_BASE_Z_M = -0.14   # templeClipLocalZM as the harness and the try-on register it
RUNTIME_HIDDEN_TAIL_M = 0.025          # renderer.ts HIDDEN_TAIL_M
RUNTIME_TEMPLE_END_CAP_M = -0.055      # renderer.ts: the drawn end never reaches further forward than this by the tail rule
RUNTIME_END_FADE_M = 0.005             # renderer.ts END_FADE_M: the arm fades over the 5 mm in front of its cutoff


def runtime_temple_end_m(base_z_m: float = RUNTIME_TEMPLE_CLIP_BASE_Z_M) -> float:
    """The renderer's templeEndMaximumZM for a registered clip without a continuity model (renderer.ts): an already-short
    frame keeps its clip, a longer one is drawn HIDDEN_TAIL_M further forward, never past RUNTIME_TEMPLE_END_CAP_M."""
    return round(max(base_z_m, min(RUNTIME_TEMPLE_END_CAP_M, base_z_m + RUNTIME_HIDDEN_TAIL_M)), 6)


RUNTIME_TEMPLE_CLIP_Z_M = runtime_temple_end_m()                 # -0.115: the drawn plane when the harness recorded none
TEMPLE_CLIP_FADE_MM = int(round(RUNTIME_END_FADE_M * 1000))      # 5
# the runtime's continuity model (ar/src/render/continuity.ts buildTempleContinuityModel, the arm's rear drop and spread) is
# built down to the REGISTERED clip (templeClipLocalZM, RUNTIME_TEMPLE_CLIP_BASE_Z_M), not the drawn plane: it samples an arm
# cross-section at every one of 33 stations from the lens rear back to the cutoff, counting only crossings at |x| > lateralMinM
# (45 mm) of opaque non-lens triangles, and throws 'An original posterior arm cross-section is missing.' at a station with
# none. That is ar_continuity_failure, a gate. Geometry between the drawn plane and the cutoff (-117.7 to -142.7 mm on
# test-pilot-002) is never drawn yet must exist.
RUNTIME_CONTINUITY_LATERAL_MIN_MM = 45.0                         # continuity.ts CONTINUITY_GEOMETRY.lateralMinM
# a failing object smaller than this in EVERY dimension is a speck at mirror distance (a screw, a slot, a pin); thin but large parts
# (test-pilot-002's 11.9 x 4.6 x 0.8 mm lens print, the 1.1 x 4.8 x 0.3 mm front T) are visible branding and hardware, never told to go
TINY_DETAIL_MM = 2.0
MAX_INVENTORY_LINES = 40
# a host-side observation phase (camera fits, the AR harness, the see-through fixtures) renews the lease from a thread this often
HOST_RENEW_INTERVAL_S = 60.0
# vs_parent: a metric that got worse by more than this fraction is flagged; a fitted camera that moved by more than these is named
WORSENED_FRACTION = 0.2
CAMERA_MOVED_DEG = 1.0
CAMERA_MOVED_PERSPECTIVE = 0.05
CAMERA_MOVED_PPM_FRACTION = 0.02
# EEVEE samples of the author's own render_views: its 640x480 renders are shown at full resolution, while the
# observation renders run at executor.DEFAULT_SAMPLES (16)
AUTHOR_VIEW_SAMPLES = 32
MODULE_KEYS = list(MODULE_ORDER)
# a worker result is an untrusted receipt: render ids are used in host paths, so they must match the requested specs exactly
RENDER_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
# the only file names the harness writes for these result keys (modeler/blender/harness.py export_parts / main)
HARNESS_OUTPUT_NAMES = {"parts_npz": "parts.npz", "materials_json": "materials.json", "blend": "candidate.blend"}


class ToolError(Exception):
    """Reported to the author as a structured error output for the same call_id; nothing was executed."""


def _s(max_length: int, nullable: bool = False) -> dict:
    return {"type": ["string", "null"] if nullable else "string", "maxLength": max_length}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


REVISION_ID = {"type": "string", "pattern": "^r[0-9]{4}$", "maxLength": 5}
REVISION_ID_OR_NULL = {"type": ["string", "null"], "pattern": "^r[0-9]{4}$", "maxLength": 5}
ARTIFACT_ID = {"type": "string", "pattern": "^[a-z]{3}[0-9]{4}$", "maxLength": 7}
_MODULE_EDIT = _obj({"mode": {"type": "string", "enum": ["inherit", "replace", "patch", "remove"]},
                     "content": {"type": ["string", "null"], "maxLength": MAX_MODULE_BYTES},
                     "expected_base_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$", "maxLength": 64}})
_VIEW = _obj({"id": {"type": "string", "pattern": "^[a-z0-9_-]{1,40}$", "maxLength": 40},
              "kind": {"type": "string", "enum": ["clay", "textured"]},
              "yaw": {"type": "number", "minimum": -180, "maximum": 180}, "pitch": {"type": "number", "minimum": -89, "maximum": 89},
              "roll": {"type": "number", "minimum": -90, "maximum": 90}, "ortho": {"type": "boolean"},
              "px_per_mm": {"type": "number", "minimum": 1, "maximum": 30},
              "target": {"anyOf": [{"type": "string", "enum": ["bbox"]}, {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}}]}})
_AR_VIEW = _obj({"id": {"type": "string", "pattern": "^[a-z0-9_-]{1,40}$", "maxLength": 40},
                 **{k: {"type": "number", "minimum": -lim, "maximum": lim} for k, lim in AR_POSE_LIMITS.items()},
                 "type": {"type": "string", "enum": ["pose", "asset-back"]}})


def ar_view_pose(v: dict) -> tuple[str, float, float, float]:
    """(type, yaw, pitch, roll) of an AR harness view spec (``yaw_degrees`` ... as observe.AR_VIEWS writes them)."""
    return (str(v.get("type") or "pose"), float(v.get("yaw_degrees") or 0.0), float(v.get("pitch_degrees") or 0.0), float(v.get("roll_degrees") or 0.0))


def describe_ar_views(views=AR_VIEWS) -> str:
    """'front (yaw 0), angled (yaw 35), ..., back (asset-back)': the poses the build's ar sheet carries."""
    out = []
    for v in views:
        kind, yaw, pitch, roll = ar_view_pose(v)
        pose = "asset-back" if kind == "asset-back" else ", ".join(f"{k} {a:g}" for k, a in (("yaw", yaw), ("pitch", pitch), ("roll", roll)) if a) or "yaw 0"
        out.append(f"{v['id']} ({pose})")
    return ", ".join(out)


TOOLS: dict[str, dict] = {
    "list_evidence": {"description": "The photo artifact ids and labels, the job limits and the current state. The evidence, the helper reference and the rules are in the first message of the conversation: there is no need to call this before writing the first program. reference: true also returns the rules and the helper reference (after a compaction the first message is no longer in the conversation); null or false keeps the reply slim.",
                      "parameters": _obj({"reference": {"type": ["boolean", "null"]}})},
    "read_evidence": {"description": "One section of the evidence in full (measurements are in mm in the model frame; nominal unless a physical dimension was stated).",
                      "parameters": _obj({"section": {"type": "string", "enum": ["front", "sides", "views", "scale", "product_reading", "provenance", "reliability", "all"]}})},
    "crop_image": {"description": "Crop an author-visible photo or render at explicit pixel coordinates; the crop comes back as a new image artifact whose parent and transform are recorded.",
                   "parameters": _obj({"artifact_id": ARTIFACT_ID, "x0": {"type": "integer", "minimum": 0}, "y0": {"type": "integer", "minimum": 0},
                                       "x1": {"type": "integer", "minimum": 1}, "y1": {"type": "integer", "minimum": 1}})},
    "read_program": {"description": "The construction modules of a revision (null: the current revision) with their sha256, for editing against an explicit base.",
                     "parameters": _obj({"revision_id": REVISION_ID_OR_NULL})},
    "edit_program": {"description": "Create a new revision from a base: per module inherit / replace (complete Python source) / patch (a JSON list of exact {find, replace} edits against expected_base_sha256, which every edit_program / build result reports per module as 'modules'; a find must occur exactly once, or add occurrence: k (1-based) to choose one; a refusal changes nothing and names every failing module and edit with the matching line numbers) / remove. Any change of geometry or materials is a new revision. With build_now the host builds, exports, checks the contract, renders and measures it in the same cycle and the reply carries the sheets, the changes against the parent revision (vs_parent) and the automatic gate; deliver_if_compatible additionally requests delivery when it is compatible AND you have received its images. When the job names editable_modules, a change to any other module is refused before anything is built.",
                     "parameters": _obj({"base_revision_id": REVISION_ID_OR_NULL,
                                         "modules": _obj({name: _MODULE_EDIT for name in MODULE_KEYS}),
                                         "rationale": _s(4000), "expected_changes": {"type": "array", "maxItems": 20, "items": _s(300)},
                                         "build_now": {"type": "boolean"}, "deliver_if_compatible": {"type": "boolean"}})},
    "inspect_scene": {"description": "Object and part inventory, bounds, triangle counts, materials and contract topology of a built revision (host-validated, bound to the program and worker identity).",
                      "parameters": _obj({"revision_id": REVISION_ID})},
    "build_candidate": {"description": "Build, export, contract-check, render and measure a created revision. Failures return the traceback, error category and log artifact ids; the revision is kept for repair.",
                        "parameters": _obj({"revision_id": REVISION_ID, "deliver_if_compatible": {"type": "boolean"}})},
    "render_views": {"description": "1-6 extra Blender renders of a built revision from chosen cameras (yaw 0 front, -90 the +X side, 90 the -X side, 180 back; pitch > 0 looks down).",
                     "parameters": _obj({"revision_id": REVISION_ID, "views": {"type": "array", "minItems": 1, "maxItems": MAX_EXTRA_VIEWS, "items": _VIEW}})},
    "render_ar_views": {"description": "The exported asset of a contract-valid revision in the ACTUAL AR renderer at poses you choose: each view is "
                                       f"{{id, yaw (+/-{AR_POSE_LIMITS['yaw']}), pitch (+/-{AR_POSE_LIMITS['pitch']}), "
                                       f"roll (+/-{AR_POSE_LIMITS['roll']}), type pose | asset-back (the settled asset seen from behind; angles 0)}}; "
                                       "background checker (the ar sheet's), skin or blue (solid fixtures). The build's ar sheet already carries "
                                       f"{describe_ar_views()} on the checker: a view equal to one of those is returned as that render at full "
                                       "resolution without a new run; ask for other poses or a solid background to see something new.",
                        "parameters": _obj({"revision_id": REVISION_ID, "views": {"type": "array", "minItems": 1, "maxItems": MAX_AR_VIEWS, "items": _AR_VIEW},
                                            "background": {"type": "string", "enum": list(AR_BACKGROUNDS)}})},
    "measure_candidate": {"description": "The host's measurements of a built revision against the photographs (silhouette and lens-outline errors, lens colour, see-through), each labelled measured / unmeasured / conditional, with the automatic gate (the one measurement that can reject the delivery automatically) and the metrics the host marks unreliable. Every other number is advisory.",
                          "parameters": _obj({"revision_id": REVISION_ID})},
    "select_revision": {"description": "Select a compatible revision as the one you intend to deliver (its bytes are re-verified). Current and selected revision are distinct.",
                        "parameters": _obj({"revision_id": REVISION_ID})},
    "fetch_pending_images": {"description": "Receive the next batch of images you have not been shown yet (renders queue when more were produced than one message carries).",
                             "parameters": _obj({"max_images": {"type": "integer", "minimum": 1, "maximum": DEFAULT_IMAGES_PER_REQUEST}})},
    "request_critic": {"description": "An independent critic (fresh context, only the photos you may see and this revision's renders) names concrete visible defects and its uncertainty. Costs one inference operation.",
                       "parameters": _obj({"revision_id": REVISION_ID, "question": _s(1500)})},
    "request_delivery": {"description": "Deliver exactly this compatible revision whose images you have received; schedules the host's final checks and the sealed evaluation. It never self-certifies visual acceptance. Terminal.",
                         "parameters": _obj({"revision_id": REVISION_ID, "status_claim": {"type": "string", "enum": list(STATUS_CLAIMS)}, "note": _s(4000)})},
}


class ToolResult:
    def __init__(self, text: dict, *, images: list[dict] | None = None, new_revision: str | None = None, delivery: dict | None = None,
                 selected: str | None = None, critic: dict | None = None):
        self.text = text
        self.images = list(images or [])           # artifact rows to include with this output (already reserved from the queue)
        self.new_revision = new_revision
        self.delivery = delivery
        self.selected = selected
        self.critic = critic


class ToolContext:
    """What the adapters need from the runner; the runner owns the loop, the budget and the transport."""

    def __init__(self, *, store: Store, artifacts: ArtifactStore, worker: Worker, evidence: dict, policy: dict, log, holder: str, fence: int,
                 clock, critic=None, ar: bool = True, heartbeat=None):
        self.store = store
        self.heartbeat = heartbeat      # callable renewing the runner's lease while a worker runs (raises LeaseError once it is lost); None outside a session
        self.artifacts = artifacts
        self.worker = worker
        self.evidence = evidence
        self.policy = policy
        self.log = log
        self.holder = holder
        self.fence = fence
        self.clock = clock
        self.critic = critic            # callable(ctx, revision, question) -> dict, or None
        self.ar = ar
        self.job_dir = store.job_dir

    @property
    def evidence_dir(self) -> Path:
        return self.job_dir / "evidence"

    def revision_dir(self, rid: str) -> Path:
        return self.job_dir / "revisions" / rid


# --------------------------------------------------------------------------- helpers
def revision_or_raise(ctx: ToolContext, rid: str | None, *, allow_null_current: bool = False) -> dict:
    if rid is None:
        if not allow_null_current:
            raise ToolError("a revision_id is required")
        rid = ctx.store.job()["current_revision"]
        if rid is None:
            raise ToolError("there is no revision yet: create one with edit_program (base_revision_id null)")
    rev = ctx.store.revision(rid)
    if rev is None:
        raise ToolError(f"unknown revision {rid!r}; known: {[r['id'] for r in ctx.store.revisions()]}")
    return rev


def modules_of(ctx: ToolContext, rev: dict) -> dict[str, str]:
    out = {}
    for name in MODULE_KEYS:
        p = ctx.revision_dir(rev["id"]) / "program" / f"{name}.py"
        if p.is_file():
            src = p.read_text(encoding="utf-8")
            if sha256_bytes(src.encode("utf-8")) != (rev["modules"].get(name) or {}).get("sha256"):
                raise ToolError(f"module {name} of {rev['id']} changed on disk; refusing to use it")
            out[name] = src
    return out


def module_shas(rev: dict) -> dict[str, str]:
    """{module: sha256} of a revision row, in module order: what a patch's expected_base_sha256 must equal."""
    mods = rev.get("modules") or {}
    return {n: mods[n]["sha256"] for n in MODULE_KEYS if n in mods and isinstance(mods[n], dict) and mods[n].get("sha256")}


# ---- the owner's module locks bind to the REVIEWED revision (setting 'owner_change_round', written by the owner's change
# request): in a locked round every locked module of an edit's base and of a delivered revision must be byte-equal (the same
# sha256) to the reviewed revision's, and a delivery must name a revision made in this round. Without the binding an edit
# on an older base, or a delivery of an older revision, changed a locked module against what the owner reviewed.
def owner_lock(store) -> dict | None:
    """The locked change round in force, or None (no change round, or one that locks nothing)."""
    lock = store.setting("owner_change_round")
    return lock if lock and lock.get("locked_modules") else None


def locked_modules_differ(lock: dict, rev: dict) -> list[str]:
    """The locked modules whose bytes in ``rev`` differ from the reviewed revision's (an absent module is None on both sides)."""
    shas = module_shas(rev)
    reviewed = lock.get("locked_sha256") or {}
    return [m for m in lock["locked_modules"] if shas.get(m) != reviewed.get(m)]


def owner_lock_refusal(store, rev: dict, *, delivery: bool) -> str | None:
    """Why ``rev`` cannot be delivered (``delivery``) or used as an edit base in the locked round in force; None when it can."""
    lock = owner_lock(store)
    if lock is None:
        return None
    reviewed = lock["reviewed_revision"]
    differ = locked_modules_differ(lock, rev)
    how = (f"edit_program with base_revision_id {reviewed} (or a revision made from it this round), change only "
           f"{', '.join(m for m in MODULE_KEYS if m not in lock['locked_modules']) or 'nothing'}, then request_delivery of the new revision")
    if differ:
        return (f"revision {rev['id']}'s locked module(s) {', '.join(differ)} differ from the reviewed revision {reviewed}'s; the owner locked "
                f"{', '.join(lock['locked_modules'])} this round (round {lock['round']}): {how}")
    if delivery and rev["id"] in set(lock.get("revisions_before") or []):
        return (f"revision {rev['id']} was not made in this round (the owner reviewed {reviewed} in round {lock['round']} and asked for a change): {how}")
    return None


def owner_lock_fallback_refusal(store, rev: dict) -> str | None:
    """Why a budget or deadline fallback of the locked round in force may not hand ``rev`` to the owner; None when it may.
    As request_delivery: the round's own revisions (locked modules equal to the reviewed one's), plus the reviewed revision
    itself (handing the owner back what they reviewed is no change); never a revision made before the round."""
    lock = owner_lock(store)
    if lock is None:
        return None
    if rev["id"] == lock["reviewed_revision"]:
        return owner_lock_refusal(store, rev, delivery=False)
    return owner_lock_refusal(store, rev, delivery=True)


# bsa.contract's watertight_parts: a part passes closed ("solid") or, for a lens, as a +Z front sheet
ACCEPTED_PROFILES = ("solid", "front_sheet")
# export notes that are defects of the submission, not informational receipts of what the exporter did (modeler/export.py)
DEFECT_NOTES = ("no lens part registered", "no frame part registered")
MAX_REPAIR_HINTS = 24


def failed_watertight_parts(contract: dict) -> set[str] | None:
    """The parts whose watertight check the contract failed (profile not an accepted solid / front sheet), from the
    per-part report of ``bsa.contract.check`` or, without it, from the watertight_parts check value; None when the
    contract carries no per-part data at all (an export exception)."""
    parts = contract.get("parts")
    if isinstance(parts, dict) and parts:
        return {str(k) for k, r in parts.items() if isinstance(r, dict) and r.get("pass") is not True and r.get("profile") not in ACCEPTED_PROFILES}
    value = ((contract.get("checks") or {}).get("watertight_parts") or {}).get("value")
    if isinstance(value, dict) and value:
        return {str(k) for k, v in value.items() if v is not True and v not in ACCEPTED_PROFILES}
    return None


def _count(value) -> int | None:
    """An inventory edge count from the worker's receipt (untrusted): a non-negative int, or None when it is no integer."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def exported_object_topology(parts_npz, materials_json, origin_mm=None) -> list[dict] | None:
    """Each object's topology as the exporter writes it and the contract measures it: the ingested parts file loaded as
    ``modeler.export.export_glb`` loads it, degenerate triangles dropped from an object that is not closed exactly as
    ``assemble`` drops them, positions in metres about the export origin through float32 (the GLB's storage), then
    ``bsa.contract.topology``. Rows {object, part, boundary_edges, nonmanifold_edges, misoriented_edges, watertight,
    dropped}; None when the files are missing or unreadable (the caller falls back to the worker's inventory), never raised."""
    try:
        if not parts_npz or not materials_json or not Path(parts_npz).is_file() or not Path(materials_json).is_file():
            return None
        objects, _, _ = mexport.load_parts(Path(parts_npz), Path(materials_json))
        origin = np.zeros(3)
        if origin_mm is not None:
            o = np.asarray(origin_mm, float).reshape(3)
            origin = o if np.isfinite(o).all() else origin
        rows = []
        for key, o in objects.items():
            V, F, dropped = np.asarray(o["V"], float), np.asarray(o["F"], np.int64), 0
            if len(F) and not mexport.is_closed(V, F):
                F, _, _, dropped = mexport.drop_degenerate(V, F, o["M"], o["UV"])
            row = {"object": str(o.get("object", key)), "part": str(o.get("part")), "dropped": int(dropped)}
            if not len(F) or not np.isfinite(V).all():
                rows.append(dict(row, watertight=None))       # skipped by the exporter / refused by the writer: its error says so
                continue
            topo = bsa_contract.topology(((V - origin) / 1000.0).astype(np.float32).astype(float), F)
            rows.append(dict(row, **{k: topo[k] for k in ("watertight", "boundary_edges", "nonmanifold_edges", "misoriented_edges")}))
        return rows
    except Exception:  # noqa: BLE001 - an unreadable parts file is a fallback, never a failed build reply
        return None


def _export_origin(export: dict):
    """The origin the writer used (``bsa.export.write_glb`` receipt), or None."""
    receipt = export.get("receipt")
    origin = (receipt.get("origin") or {}) if isinstance(receipt, dict) else {}
    return origin.get("origin_mm") if isinstance(origin, dict) else None


MAX_AUDIT_ITEMS = 24


def export_audit(export: dict | None) -> dict | None:
    """The exporter receipt's audit block as the author sees it: ``{flags, notes}`` from ``receipt['audit']`` (bsa.export
    write_glb: flags are short snake_case strings, notes one-line sentences naming the part and what to change; the
    per-part measures stay in export.json). None when the receipt carries no audit. The owner saw faceted temples and a
    flat mirror slab on test-pilot-002's r0006 that no number the author received named; the audit names them."""
    receipt = (export or {}).get("receipt")
    audit = receipt.get("audit") if isinstance(receipt, dict) else None
    if not isinstance(audit, dict):
        return None
    flags = [f[:80] for f in (audit.get("flags") if isinstance(audit.get("flags"), list) else []) if isinstance(f, str) and f]
    notes = [n[:400] for n in (audit.get("notes") if isinstance(audit.get("notes"), list) else []) if isinstance(n, str) and n]
    return {"flags": flags[:MAX_AUDIT_ITEMS], "notes": notes[:MAX_AUDIT_ITEMS]}


DROPPED_NOTE = re.compile(r"^(?P<object>.+): dropped \d+ degenerate triangles before export")


def _origin_xyz(export: dict | None) -> tuple[float, float, float]:
    """The export origin in the author's mm (the GLB frame's origin, the bridge underside), 0 where unreadable."""
    origin = _export_origin(export or {})
    try:
        o = np.asarray(origin, float).reshape(3) if origin is not None else np.zeros(3)
    except Exception:  # noqa: BLE001
        o = np.zeros(3)
    return tuple(float(v) if math.isfinite(float(v)) else 0.0 for v in o)


def _lateral_reach_mm(box, origin_x_mm: float) -> float:
    return max(abs(float(box[0][0]) - origin_x_mm), abs(float(box[1][0]) - origin_x_mm))


def continuity_gap(obj: dict, rows, *, continuity_z_mm: float | None, origin_x_mm: float = 0.0, exclude=()) -> list[float] | None:
    """The z span [from, to] (mm, the author's coordinates) of ``obj`` that the runtime's continuity model samples and that
    no other geometry of the same temple part covers, or None when removing ``obj`` would leave the model its sections.

    Not needed: an object whose bbox never reaches |x| > 45 mm from the origin (continuity.ts skips those crossings) or one
    lying wholly behind the registered cutoff ``continuity_z_mm``. Otherwise its span from the cutoff forward must be covered
    by the z spans of other rows of the same part that reach past 45 mm and are not in ``exclude`` (repair_hints excludes the
    other failing objects it may tell to leave out, so two failing segments never excuse each other). ``continuity_z_mm``
    None takes the object's whole span as needed (never less safe). Bounding boxes approximate the triangles: a coverer's
    bbox can span a z range its triangles leave open, and translucency (continuity.ts skips transparent materials) is not
    known here."""
    box = _bbox(obj)
    if box is None or _lateral_reach_mm(box, origin_x_mm) <= RUNTIME_CONTINUITY_LATERAL_MIN_MM:
        return None
    hi = float(box[1][2])
    lo = float(box[0][2]) if continuity_z_mm is None else max(float(box[0][2]), float(continuity_z_mm))
    if hi < lo:
        return None
    part, name, skip = str(obj.get("part")), str(obj.get("object")), {str(e) for e in exclude}
    spans = []
    for r in rows if isinstance(rows, (list, tuple)) else []:
        if not isinstance(r, dict) or str(r.get("part")) != part or str(r.get("object")) == name or str(r.get("object")) in skip:
            continue
        b = _bbox(r)
        if b is not None and _lateral_reach_mm(b, origin_x_mm) > RUNTIME_CONTINUITY_LATERAL_MIN_MM:
            spans.append((float(b[0][2]), float(b[1][2])))
    cur = lo
    for a, b in sorted(spans):
        if b < cur:
            continue
        if a > cur:
            return [round(cur, 1), round(min(a, hi), 1)]
        cur = max(cur, b)
        if cur >= hi:
            return None
    return [round(cur, 1), round(hi, 1)]


def behind_clip_clause(row: dict, rows, *, clip_z_mm: float, continuity_z_mm: float | None, origin_x_mm: float, exclude=()) -> str:
    """The second clause of a failing temple object lying wholly behind the drawn plane: leave it out only when the
    continuity model does not need it (``continuity_gap``), otherwise repair it."""
    box = _bbox(row)
    part = str(row.get("part"))[:20]
    head = f"; it lies wholly behind the runtime temple clip (z = {float(clip_z_mm):.1f} mm in your coordinates), so it is never drawn in the AR renderer or the try-on"
    cut = f"the registered clip z = {float(continuity_z_mm):.1f} mm" if continuity_z_mm is not None else         f"the registered clip (local z {RUNTIME_TEMPLE_CLIP_BASE_Z_M} m from the origin)"
    gap = continuity_gap(row, rows, continuity_z_mm=continuity_z_mm, origin_x_mm=origin_x_mm, exclude=exclude)
    if gap is not None:
        return (head + f", but it carries the arm's continuity: the runtime samples an arm cross-section at every station back to {cut} and no "
                f"other {part} geometry covers z {gap[0]:.1f} to {gap[1]:.1f} mm, so repair it (removing it fails the try-on: ar_continuity_failure, a gate)")
    if _lateral_reach_mm(box, origin_x_mm) <= RUNTIME_CONTINUITY_LATERAL_MIN_MM:
        why = f"and it stays within |x| {RUNTIME_CONTINUITY_LATERAL_MIN_MM:.0f} mm of the origin, where the runtime's continuity model samples no arm"
    elif continuity_z_mm is not None and float(box[1][2]) < float(continuity_z_mm):
        why = f"and behind {cut}, the last arm section the runtime's continuity model samples"
    else:
        why = f"and other {part} geometry covers its z span back to {cut}, so the runtime's continuity model does not need it"
    return head + f", {why}: leave it out rather than repair it (the contract still checks it)"


def repair_hints(export: dict | None, inventory: list | None, *, parts_npz=None, materials_json=None, clip_z_mm: float | None = None,
                 continuity_z_mm: float | None = None) -> list[str]:
    """The short list an author needs first after a failed build, defects first: the export error, the failed contract
    checks, the defect notes (no lens / frame part), the open / non-manifold objects of the parts the contract actually
    failed, then the informational export notes (dropped triangles, material fallbacks, origin). Empty when nothing
    failed (a passing contract has nothing to repair); test-pilot-001 had to dig these out of notes / inventory /
    failed_checks on every failed turn. Only the failed parts' objects are listed: replayed on test-pilot-001's r0001 the
    unfiltered inventory told the author to close six temple objects although the contract passed both temples (the
    merged part is what the contract checks). Every row is listed only when the contract carries no per-part data.

    The object counts are the exported ones (``exported_object_topology`` on the ingested parts file), not the worker's
    pre-export inventory: on r0001 the inventory told the author to close gold_T_crossbar_L/R and gold_T_front_bar_L/R,
    which export watertight once their degenerate triangles are dropped, and gave crystal_rim_R 10/20 where the contract
    measured 4 nonmanifold / 8 misoriented. The dropped-triangles note of an object that exports watertight is left out
    (the exporter cleaned it; nothing is to be repaired). Without a readable parts file the inventory stands in.

    Two object lines carry a second clause. A temple object lying wholly behind ``clip_z_mm`` (the runtime's drawn plane in
    the author's coordinates, ``temple_clip_block``) is never drawn: test-pilot-002 r0003's tip inscriptions failed the contract
    there and cost r0004. It is told to leave the object out only when the runtime's continuity model does not need it
    (``continuity_gap`` against ``continuity_z_mm``, the registered clip): a failing arm segment between the drawn plane and
    the registered clip is the only arm section there unless other geometry of its part covers it, and deleting it fails
    ar_continuity_failure, a gate. An object smaller than TINY_DETAIL_MM in every dimension is a speck: weld it or leave it
    out. A thin but large part (a lens print, the front T) is visible branding or hardware and gets neither clause."""
    export = export or {}
    contract = export.get("contract") or {}
    hints: list[str] = []
    if export.get("error"):
        hints.append(f"export failed: {export['error']}")
    if contract.get("ok") and not export.get("error"):
        return []
    for name, c in (contract.get("checks") or {}).items():
        if isinstance(c, dict) and not c.get("pass"):
            hints.append(f"contract check {name} failed: value {json.dumps(c.get('value'), default=str)[:300]}, limit {json.dumps(c.get('limit'), default=str)[:120]}")
    for f in contract.get("failures") or []:
        if f not in (contract.get("checks") or {}) and f != "export_exception":
            hints.append(f"contract failure: {f}")
    notes = [n for n in export.get("notes") or [] if isinstance(n, str) and n]
    hints += [f"export defect: {n}" for n in notes if n in DEFECT_NOTES]
    failed = failed_watertight_parts(contract)
    exported = exported_object_topology(parts_npz, materials_json, _export_origin(export))
    rows = exported if exported is not None else (inventory if isinstance(inventory, (list, tuple)) else [])
    inv_rows = [r for r in (inventory if isinstance(inventory, (list, tuple)) else []) if isinstance(r, dict)]
    by_name = {str(r.get("object")): r for r in inv_rows}
    boxes = {name: _bbox(r) for name, r in by_name.items()}
    open_rows = []
    for row in rows:
        if not isinstance(row, dict) or (failed is not None and str(row.get("part")) not in failed):
            continue
        counts = [_count(row.get(k)) for k in ("boundary_edges", "nonmanifold_edges", "misoriented_edges")]
        if None in counts:
            continue            # the worker's receipt is untrusted: a count that is no integer is skipped, never raised on
        if any(counts):
            open_rows.append((row, counts))

    def behind_drawn_plane(row) -> bool:
        box = boxes.get(str(row.get("object")))
        return (box is not None and clip_z_mm is not None and str(row.get("part")).startswith("temple")
                and float(box[1][2]) < float(clip_z_mm))
    # the failing objects a clause may tell to leave out: none of them counts as cover for another (continuity_gap exclude)
    behind = {str(row.get("object")) for row, _ in open_rows if behind_drawn_plane(row)}
    origin_x = _origin_xyz(export)[0]
    for row, (b, nm, mo) in open_rows:
        line = (f"{str(row.get('object'))[:80]} ({str(row.get('part'))[:20]}): {b} boundary, {nm} nonmanifold, {mo} misoriented edges: not a closed "
                "2-manifold (close the mesh, weld the seam or make the normals consistent)")
        box = boxes.get(str(row.get("object")))
        if behind_drawn_plane(row):
            line += behind_clip_clause(by_name[str(row.get("object"))], inv_rows, clip_z_mm=float(clip_z_mm), continuity_z_mm=continuity_z_mm,
                                       origin_x_mm=origin_x, exclude=behind - {str(row.get("object"))})
        elif box is not None and float((box[1] - box[0]).max()) < TINY_DETAIL_MM:
            ext = box[1] - box[0]
            line += (f"; a tiny detail ({' x '.join(f'{v:.1f}' for v in ext)} mm, under {TINY_DETAIL_MM:g} mm in every dimension): weld its "
                     "pieces (merge by distance) before recalculating normals, in a patch of that module alone, or leave it out")
        hints.append(line)
    cleaned = {r["object"] for r in exported or [] if r.get("watertight") is True}
    for n in notes:
        m = DROPPED_NOTE.match(n)
        if n in DEFECT_NOTES or (m and m.group("object") in cleaned):
            continue
        hints.append(f"export note: {n}")
    return hints[:MAX_REPAIR_HINTS]


# --------------------------------------------------------------------------- reply digests
# test-pilot-002's edit_program replies were 14-18k characters of JSON, 59-65% of it the worker inventory (30 of 30 rows clean in
# r0005, 22-30 rows byte-identical to the parent's), plus 26 sha256 strings, the module hashes twice, three copies of one font note
# and a host_state whose revisions list grew with every rationale. A reply keeps what the author acts on; the rest stays in host
# files (revisions/<rid>/build/result.host.json, export.json) that inspect_scene reads back.
def _bbox(row: dict):
    b = row.get("bbox_mm") if isinstance(row, dict) else None
    try:
        lo, hi = np.asarray(b[0], float).reshape(3), np.asarray(b[1], float).reshape(3)
    except Exception:  # noqa: BLE001 - the inventory is the worker's untrusted receipt
        return None
    return (lo, hi) if np.isfinite(lo).all() and np.isfinite(hi).all() else None


def inventory_line(row: dict) -> str:
    """One inventory row as the author needs it: '<object> [<part>/<component>]: <n> tri, <topology>, bbox [..]..[..]'."""
    comp = row.get("component")
    head = f"{str(row.get('object'))[:80]} [{str(row.get('part'))[:20]}{'/' + str(comp)[:40] if comp and comp != row.get('object') else ''}]"
    counts = [_count(row.get(k)) for k in ("boundary_edges", "nonmanifold_edges", "misoriented_edges")]
    topo = "counts unreadable" if None in counts else ("closed" if not any(counts) else "open: {} boundary, {} nonmanifold, {} misoriented".format(*counts))
    tri = _count(row.get("triangles"))
    out = f"{head}: {tri if tri is not None else '?'} tri, {topo}"
    b = _bbox(row)
    if b is not None:
        out += f", bbox [{', '.join(f'{v:.1f}' for v in b[0])}]..[{', '.join(f'{v:.1f}' for v in b[1])}]"
    return out


def inventory_digest(rows, parent_rows=None, *, revision: str | None) -> dict | None:
    """The build reply's inventory: the object count and, against the parent build's inventory, the rows that are new or
    changed (one line each), the names that disappeared and how many are unchanged; without a parent, the object count
    per part. Every row stays readable through inspect_scene. The open / non-manifold objects that matter are the
    contract's, named by repair_hints (an object may be open inside a part the contract passes). None without rows."""
    if not isinstance(rows, (list, tuple)):
        return None
    good = [r for r in rows if isinstance(r, dict)]
    out: dict = {"objects": len(good)}
    if isinstance(parent_rows, (list, tuple)):
        prev = {str(r.get("object")): r for r in parent_rows if isinstance(r, dict)}
        changed = [r for r in good if prev.get(str(r.get("object"))) != r]
        lines = [inventory_line(r) for r in changed[:MAX_INVENTORY_LINES]]
        if len(changed) > MAX_INVENTORY_LINES:
            lines.append(f"... {len(changed) - MAX_INVENTORY_LINES} more changed objects: inspect_scene {revision}")
        names = {str(r.get("object")) for r in good}
        out.update(changed_since_parent=lines, removed_since_parent=sorted(n for n in prev if n not in names)[:MAX_INVENTORY_LINES],
                   unchanged_since_parent=len(good) - len(changed))
    else:
        by_part: dict[str, int] = {}
        for r in good:
            by_part[str(r.get("part"))] = by_part.get(str(r.get("part")), 0) + 1
        out["objects_by_part"] = dict(sorted(by_part.items()))
    out["all_rows"] = f"inspect_scene {revision} returns every object with its bbox, triangles, materials and topology"
    return out


def collapse_notes(notes) -> list[str]:
    """Notes in first-seen order, a repeated one once with its count ('... (x3)'); non-strings and empty strings dropped."""
    counts: dict[str, int] = {}
    for n in notes if isinstance(notes, (list, tuple)) else []:
        if isinstance(n, str) and n:
            counts[n] = counts.get(n, 0) + 1
    return [n if c == 1 else f"{n} (x{c})" for n, c in counts.items()]


# the summary metrics a view's camera feeds (modeler/observe.py summarize); 'all' feeds mean_contour_mm_all_fit_views
VIEW_METRICS = {"front": ("front_contour_mean_mm", "front_contour_p95_mm", "front_iou"), "left": ("side_contour_mean_mm",),
                "right": ("side_contour_mean_mm",), "back": ("back_contour_mean_mm",)}
LENS_OUTLINE_METRICS = ("lens_outline_mean_mm", "lens_outline_p95_mm")
# vs_parent: metric -> (better direction 'lower' / 'higher' / 'toward_1', or None: reported without a verdict; the smallest change that
# can count as worse, so a 0.000 -> 0.019 hue error is not a regression)
DELTA_METRICS = {"lens_outline_mean_mm": ("lower", 0.05), "lens_outline_p95_mm": ("lower", 0.1), "front_contour_mean_mm": ("lower", 0.05),
                 "front_contour_p95_mm": ("lower", 0.1), "front_iou": ("higher", 0.01), "side_contour_mean_mm": ("lower", 0.05),
                 "back_contour_mean_mm": ("lower", 0.05), "mean_contour_mm_all_fit_views": ("lower", 0.05), "lens_colour.hue_error": ("lower", 0.02),
                 "lens_colour.saturation_ratio": ("toward_1", 0.02), "lens_colour.value_ratio": ("toward_1", 0.02),
                 "frame_see_through.see_through": (None, 0.0), "temple_see_through.see_through": (None, 0.0),
                 "lens_reflection.max_jump_px": ("lower", 2.0), "lens_reflection.max_saturated_share": ("lower", 0.02)}
BEST_METRICS = ("lens_outline_mean_mm", "front_contour_mean_mm", "side_contour_mean_mm", "back_contour_mean_mm", "mean_contour_mm_all_fit_views")


def _metric(summary: dict, key: str):
    """A summary metric by dotted key; a {value, reliable, reason} block reads as its value; None when not a finite number."""
    v = summary if isinstance(summary, dict) else {}
    for k in key.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    if isinstance(v, dict):
        v = v.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return None
    return float(v)


def unreliable_metrics(observation: dict | None) -> dict[str, str]:
    """{summary metric: reason} for every metric the observation marks unreliable: summary.unreliable_metrics (what
    ``modeler.observe.summarize`` writes and ``modeler.evaluate.decide_status`` reads), a view marked ``reliable: false``
    (its contour metrics and mean_contour_mm_all_fit_views, as summarize marks them), a view's lens_outline block (the
    lens outline metrics) or a summary block itself. The instrument, not the model, may be what moves such a number
    (test-pilot-002: the lens print's glyph holes and a temple-driven front refit)."""
    obs = observation if isinstance(observation, dict) else {}
    out: dict[str, str] = {}
    listed = (obs.get("summary") or {}).get("unreliable_metrics") if isinstance(obs.get("summary"), dict) else None
    for key, reason in (listed.items() if isinstance(listed, dict) else ()):
        if isinstance(reason, str) and reason:
            out[str(key)] = reason[:200]
    for vid, v in (obs.get("views") or {}).items():
        if not isinstance(v, dict):
            continue
        if v.get("reliable") is False:
            for key in VIEW_METRICS.get(str(v.get("view") or vid), ()):
                out.setdefault(key, f"{vid}: {str(v.get('reason') or 'marked unreliable')[:200]}")
            out.setdefault("mean_contour_mm_all_fit_views", f"includes the {vid} view, marked unreliable: {str(v.get('reason') or '')[:160]}")
        lo = v.get("lens_outline")
        if isinstance(lo, dict) and lo.get("reliable") is False:
            for key in LENS_OUTLINE_METRICS:
                out.setdefault(key, f"{vid} lens outline: {str(lo.get('reason') or 'marked unreliable')[:200]}")
    for key, v in (obs.get("summary") or {}).items():
        if isinstance(v, dict) and v.get("reliable") is False:
            out.setdefault(key, str(v.get("reason") or "marked unreliable")[:200])
    return out


def camera_delta(parent: dict, now: dict) -> dict | None:
    """Per-view change of the fitted camera: yaw / pitch / roll (deg), perspective, px_per_mm (relative). Not the normalised
    scale, which moves with the bounding box (test-pilot-002 r0003: 1211 -> 1281 at px_per_mm 7.753 -> 7.761)."""
    pc, nc = (parent or {}).get("camera"), (now or {}).get("camera")
    if not isinstance(pc, dict) or not isinstance(nc, dict):
        return None
    out: dict = {}
    for k in ("yaw", "pitch", "roll", "perspective"):
        a, b = _metric(pc, k), _metric(nc, k)
        if a is not None and b is not None:
            out[k] = round(b - a, 3)
    a, b = _metric(parent, "px_per_mm"), _metric(now, "px_per_mm")
    if a and b is not None:
        out["px_per_mm_rel"] = round((b - a) / a, 4)
    out["moved"] = bool(any(abs(out.get(k, 0.0)) > CAMERA_MOVED_DEG for k in ("yaw", "pitch", "roll"))
                        or abs(out.get("perspective", 0.0)) > CAMERA_MOVED_PERSPECTIVE or abs(out.get("px_per_mm_rel", 0.0)) > CAMERA_MOVED_PPM_FRACTION)
    return out


def _worse(direction, parent: float, now: float, floor: float) -> bool:
    """Worse by more than WORSENED_FRACTION of the parent's value and by more than ``floor`` in absolute terms."""
    if direction == "lower":
        return now > parent * (1 + WORSENED_FRACTION) and now - parent > floor
    if direction == "higher":
        return now < parent * (1 - WORSENED_FRACTION) and parent - now > floor
    if direction == "toward_1":
        return abs(now - 1) > abs(parent - 1) * (1 + WORSENED_FRACTION) and abs(now - 1) - abs(parent - 1) > floor
    return False


def vs_parent(ctx: ToolContext, rev: dict, observation: dict | None) -> dict | None:
    """The build against its parent's observation: metric deltas (with 'worse' past WORSENED_FRACTION), per-view fitted-camera
    deltas, the bounding-box extent when it changed, the modules this revision changed, and the best value so far of the
    main metrics when another revision holds it. A worsened metric names the camera that moved with it: a refit changes
    a contour without any shape change (test-pilot-002 r0003's temple-only edit moved the front camera 1.3 deg in pitch
    and the lens outline 1.311 -> 1.918 mm with the lens untouched; no reply said so). None without an observed parent."""
    parent = ctx.store.revision(rev["parent_id"]) if rev.get("parent_id") else None
    pobs = (parent or {}).get("observation") or {}
    if not observation or not pobs.get("summary"):
        return None
    ps, ns = pobs.get("summary") or {}, observation.get("summary") or {}
    unreliable = unreliable_metrics(observation)
    cams = {}
    for vid, v in (observation.get("views") or {}).items():
        d = camera_delta((pobs.get("views") or {}).get(vid) or {}, v if isinstance(v, dict) else {})
        if d is not None:
            cams[vid] = d
    moved_by_metric: dict[str, list[str]] = {}
    for vid, d in cams.items():
        if d["moved"]:
            v = (observation.get("views") or {}).get(vid) or {}
            view = str(v.get("view") or vid)
            # a lens outline measured under the parent's carried lens camera (observe.lens_camera_for) did not move with the front refit
            lens_cam = ((v.get("lens_outline") or {}).get("camera") or {}) if isinstance(v.get("lens_outline"), dict) else {}
            lens = LENS_OUTLINE_METRICS if view == "front" and lens_cam.get("origin") != "carried" else ()
            for key in VIEW_METRICS.get(view, ()) + lens + ("mean_contour_mm_all_fit_views",):
                moved_by_metric.setdefault(key, []).append(vid)
    changed = [n for n in MODULE_KEYS if ((rev.get("modules") or {}).get(n) or {}).get("source") == "changed"]
    changed += [n for n in MODULE_KEYS if n in ((parent or {}).get("modules") or {}) and n not in (rev.get("modules") or {})]   # removed modules
    metrics, worsened = {}, []
    for key, (direction, floor) in DELTA_METRICS.items():
        a, b = _metric(ps, key), _metric(ns, key)
        if a is None or b is None:
            continue
        row = {"parent": round(a, 3), "now": round(b, 3), "delta": round(b - a, 3)}
        if direction is not None and _worse(direction, a, b, floor):
            row["worse"] = True
        if key in unreliable:
            row["unreliable"] = unreliable[key]
        metrics[key] = row
        if row.get("worse"):
            cause = (f"{', '.join(moved_by_metric[key])} camera moved (a refit, not necessarily a shape change)" if key in moved_by_metric
                     else f"with modules {', '.join(changed) or 'none'} changed")
            worsened.append(f"{key} {a:.3f} -> {b:.3f}: {cause}" + ("; the host marks it unreliable" if key in unreliable else ""))
    out = {"parent": parent["id"], "changed_modules": changed, "metrics": metrics, "cameras": cams, "worsened": worsened}
    pb, nb = pobs.get("bbox_mm"), observation.get("bbox_mm")
    try:
        pe, ne = np.ptp(np.asarray(pb, float), axis=0), np.ptp(np.asarray(nb, float), axis=0)
        if np.abs(ne - pe).max() > 0.5:
            out["bbox_extent_mm"] = {"parent": [round(float(x), 1) for x in pe], "now": [round(float(x), 1) for x in ne],
                                     "note": "the cameras are fitted in a frame normalised by this box: a longer temple alone refits every view"}
    except Exception:  # noqa: BLE001 - no usable box on either side
        pass
    best = {}
    for key in BEST_METRICS:
        now = _metric(ns, key)
        # compatible revisions only: an incompatible one cannot be delivered, so its score is no target
        cands = [(v, r["id"]) for r in ctx.store.revisions() if r["id"] != rev["id"] and (r.get("compatibility") or {}).get("compatible")
                 for v in [_metric(((r.get("observation") or {}).get("summary") or {}), key)] if v is not None]
        if cands and now is not None and min(cands)[0] < now:
            best[key] = {"value": round(min(cands)[0], 3), "revision": min(cands)[1]}
    if best:
        out["best_so_far"] = best
    return out


def automatic_gate(ctx: ToolContext, summary: dict, unreliable: dict | None = None) -> dict:
    """The measurement that decides the sealed evaluation's automatic verdict (``modeler.evaluate.GATE_THRESHOLDS_MM``,
    made report-only by a frozen per-job override) with this revision's value; the prompt had told the author
    'measurements never decide' while lens_outline_mean_mm > 0.8 mm rejected test-pilot-002's delivery. ``gate`` mirrors
    ``evaluate.decide_status``: a report-only override or a metric the host marks unreliable is reported, never gated."""
    from .. import evaluate as mevaluate
    overrides = (ctx.store.setting("protocol") or {}).get("gate_overrides") or ctx.evidence.get("gate_overrides") or {}
    # the evaluator reads summary.unreliable_metrics; the per-view and per-block marks are the same verdicts at their source
    unreliable = dict(unreliable_metrics({"summary": summary}), **(unreliable or {}))
    out: dict = {}
    for key, limit in mevaluate.GATE_THRESHOLDS_MM.items():
        v = _metric(summary, key)
        ov = overrides.get(key) if isinstance(overrides.get(key), dict) else None
        row = {"value": None if v is None else round(v, 3), "limit_mm": limit, "passes": None if v is None else bool(v <= limit),
               "gate": not (ov and ov.get("mode") == "report_only") and key not in unreliable}
        if ov:
            row["override"] = {k: ov.get(k) for k in ("mode", "reason") if k in ov}
        if key in unreliable:
            row.update(unreliable=unreliable[key], reliable=False, reason=unreliable[key],
                       gate_note="the host marks this measurement unreliable: the evaluator reports it and it is never gated")
        out[key] = row
    out["note"] = ("the final automatic verdict rejects when a gated value is above its limit or missing; the runtime temple continuity "
                   "(ar_continuity_failure) and the sealed evaluator's overall verdict also decide; every other number here is advisory")
    return out


def recorded_temple_clip(report_path, glb_sha256: str | None) -> dict | None:
    """The temple cutoff the AR harness recorded for one GLB (observe/ar/report.json, each render's timing): per render and
    side the runtime cuts at max(templeEndMaximumZM, that side's endpoint z) (renderer.ts applyTempleEndpoint). Returns
    {z_m: the furthest-back cutoff of any render and side, z_m_max: the furthest-forward, renders, states}, or None when
    the report is missing, unreadable, for another GLB, or carries no finite value in [-0.3, 0] m."""
    rep = load_json_or_none(Path(report_path)) if report_path else None
    if not isinstance(rep, dict):
        return None
    cases = [c for c in rep.get("cases") or [] if isinstance(c, dict)]
    case = next((c for c in cases if c.get("id") == "candidate"), cases[0] if len(cases) == 1 else None)
    if case is None or (glb_sha256 and case.get("model_sha256") != glb_sha256):
        return None

    def finite(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) and -0.3 <= float(v) <= 0.0 else None
    cutoffs, states, renders = [], set(), 0
    for r in case.get("renders") or []:
        t = r.get("timing") if isinstance(r, dict) else None
        maximum = finite(t.get("templeEndMaximumZM")) if isinstance(t, dict) else None
        if maximum is None:
            continue
        renders += 1
        for side in ("templeEndNegativeZM", "templeEndPositiveZM"):
            z = finite(t.get(side))
            cutoffs.append(max(maximum, z) if z is not None else maximum)
        if isinstance(t.get("templeEndState"), str):
            states.add(t["templeEndState"][:40])
    if not cutoffs:
        return None
    return {"z_m": round(min(cutoffs), 6), "z_m_max": round(max(cutoffs), 6), "renders": renders, "states": sorted(states)}


def temple_clip_block(export: dict | None, inventory, *, recorded: dict | None = None) -> dict:
    """Where the AR runtime stops drawing the temples, in the author's model coordinates: the cutoff the harness recorded
    for this revision (``recorded_temple_clip``), else RUNTIME_TEMPLE_CLIP_Z_M, in the GLB frame is ``origin_z + clip`` in mm
    before the export re-origins; temple objects entirely behind it are never drawn. ``continuity_z_mm`` is the registered
    clip (RUNTIME_TEMPLE_CLIP_BASE_Z_M, whatever the recorded drawn plane), down to which the runtime's continuity model
    needs arm sections; ``objects_carrying_continuity`` names the never-drawn objects it would miss (``continuity_gap``)."""
    ox, _oy, oz = _origin_xyz(export)
    z_m = float(recorded["z_m"]) if recorded else RUNTIME_TEMPLE_CLIP_Z_M
    z = round(oz + z_m * 1000.0, 1)
    out = {"z_mm": z, "local_z_m": round(z_m, 4), "fade_mm": TEMPLE_CLIP_FADE_MM,
           "source": (f"recorded by the AR harness for this revision (observe/ar/report.json, {recorded['renders']} renders)" if recorded else
                      f"the runtime constants (no harness report for this revision): templeClipLocalZM {RUNTIME_TEMPLE_CLIP_BASE_Z_M} m plus the "
                      f"{RUNTIME_HIDDEN_TAIL_M * 1000:.0f} mm hidden tail"),
           "note": (f"the AR runtime (the try-on and every ar render here) draws no temple geometry behind z = {z} mm in your coordinates "
                    f"(local z {round(z_m, 4)} m from the bridge-underside origin, i.e. the origin's z {z_m * 1000:+.0f} mm) and fades the arm "
                    f"over the {TEMPLE_CLIP_FADE_MM} mm in front of it (z {z} to {round(z + TEMPLE_CLIP_FADE_MM, 1)} mm): tips, plaques, lettering "
                    "and bends behind it are never shown, yet an edit there still changes the bounding box and so the fitted cameras and every contour")}
    if recorded and recorded.get("z_m_max") is not None and abs(float(recorded["z_m_max"]) - z_m) > 1e-4:
        out["z_mm_range"] = [z, round(oz + float(recorded["z_m_max"]) * 1000.0, 1)]
        out["range_note"] = "the runtime's temple endpoint cut one side earlier in some renders; nothing behind the first value is ever drawn"
    cut = round(oz + RUNTIME_TEMPLE_CLIP_BASE_Z_M * 1000.0, 1)
    out["continuity_z_mm"] = cut
    out["continuity_note"] = (f"the runtime's continuity model (each arm's rear drop and spread) samples an arm cross-section at every station from the "
                              f"lens rear back to z = {cut} mm in your coordinates (the registered clip, local z {RUNTIME_TEMPLE_CLIP_BASE_Z_M} m, "
                              f"the origin's z {RUNTIME_TEMPLE_CLIP_BASE_Z_M * 1000:+.0f} mm): temple geometry between z = {z} and {cut} mm is never "
                              f"drawn but must exist, on both sides at |x| > {RUNTIME_CONTINUITY_LATERAL_MIN_MM:.0f} mm, or the try-on fails "
                              "(ar_continuity_failure, a gate); objects_carrying_continuity names the never-drawn objects it would miss")
    rows = [r for r in (inventory if isinstance(inventory, (list, tuple)) else []) if isinstance(r, dict)]
    behind_rows = [r for r in rows if str(r.get("part")).startswith("temple") and _bbox(r) is not None and float(_bbox(r)[1][2]) < z]
    if behind_rows:
        out["objects_never_drawn"] = [str(r.get("object")) for r in behind_rows][:MAX_INVENTORY_LINES]
    carrying = [str(r.get("object")) for r in behind_rows if continuity_gap(r, rows, continuity_z_mm=cut, origin_x_mm=ox) is not None]
    if carrying:
        out["objects_carrying_continuity"] = carrying[:MAX_INVENTORY_LINES]
    declared = ((export or {}).get("declarations") or {}).get("temple_clip_z_mm") if isinstance((export or {}).get("declarations"), dict) else None
    if isinstance(declared, (int, float)) and not isinstance(declared, bool):
        out["declared_z_mm"] = float(declared)
        out["declared_note"] = "your gl.set_temple_clip_z value is recorded in the export but not used: the runtime and these renders use the plane above"
    return out


def revision_temple_clip(ctx: ToolContext, rev: dict, *, export: dict | None = None, inventory=None) -> dict:
    """``temple_clip_block`` for a stored revision: its export receipt, its build inventory and the cutoff its own AR harness
    run recorded (observe/ar/report.json, only for this revision's GLB)."""
    rdir = ctx.revision_dir(rev["id"])
    if export is None:
        export = load_json_or_none(rdir / "export.json")
    if inventory is None:
        inventory = (load_json_or_none(rdir / "build" / "result.host.json") or {}).get("inventory")
    report = rdir / "observe" / "ar" / "report.json"
    recorded = recorded_temple_clip(report, rev.get("glb_sha256")) if rev.get("glb_sha256") and report.is_file() else None
    return temple_clip_block(export if isinstance(export, dict) else None, inventory, recorded=recorded)


MAX_APPEARANCE_ITEMS = 24


def _small(value, depth: int = 0):
    """A JSON-safe copy of a small recommendation / delta value: numbers rounded, strings clipped, nesting and length bounded."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 4) if isinstance(value, float) and math.isfinite(value) else (value if isinstance(value, int) else None)
    if isinstance(value, str):
        return value[:120]
    if depth < 2 and isinstance(value, dict):
        return {str(k)[:60]: _small(v, depth + 1) for k, v in list(value.items())[:MAX_APPEARANCE_ITEMS]}
    if depth < 2 and isinstance(value, (list, tuple)):
        return [_small(v, depth + 1) for v in list(value)[:MAX_APPEARANCE_ITEMS]]
    return None


def _flags(value) -> list[str]:
    return [f[:80] for f in value[:MAX_APPEARANCE_ITEMS] if isinstance(f, str) and f] if isinstance(value, list) else []


def appearance_digest(appearance) -> dict | None:
    """summary.appearance (the material_match measurement) as the build reply carries it: the flags, per material its role,
    deltas, recommendations and flags, per hardware region its area ratio and flag, and the reliability; the raw photo /
    render statistics stay in the observation (measure_candidate returns them). None when absent or not a dict."""
    if not isinstance(appearance, dict):
        return None
    out: dict = {"flags": _flags(appearance.get("flags"))}
    mats = appearance.get("materials")
    if isinstance(mats, dict):
        out["materials"] = {}
        for name, m in list(mats.items())[:MAX_APPEARANCE_ITEMS]:
            if not isinstance(m, dict):
                continue
            row: dict = {}
            if m.get("role") is not None:
                row["role"] = str(m["role"])[:40]
            for k in ("deltas", "recommended"):
                if isinstance(m.get(k), dict) and m[k]:
                    row[k] = _small(m[k])
            if _flags(m.get("flags")):
                row["flags"] = _flags(m.get("flags"))
            out["materials"][str(name)[:80]] = row
    hw = appearance.get("hardware_area")
    if isinstance(hw, dict):
        out["hardware_area"] = {str(k)[:60]: {kk: _small(v.get(kk)) for kk in ("ratio", "flag", "status") if v.get(kk) is not None}
                                for k, v in list(hw.items())[:MAX_APPEARANCE_ITEMS] if isinstance(v, dict)}
    if isinstance(appearance.get("reliable"), bool):
        out["reliable"] = appearance["reliable"]
    if appearance.get("reason"):
        out["reason"] = str(appearance["reason"])[:300]
    return out


def reply_measurements(summary: dict) -> dict:
    """The build reply's measurements: the observation summary with summary.appearance compacted (``appearance_digest``)."""
    out = dict(summary)
    if "appearance" in out:
        digest = appearance_digest(out.pop("appearance"))
        if digest is not None:
            out["appearance"] = digest
    return out


def unfitted_photos(evidence: dict, observation: dict | None) -> dict | None:
    """Author-visible photos with no fitted camera (observe.py fits front, back, left, right and angled only: test-pilot-002's
    rear_angled photo had no match render and nothing said so)."""
    fitted = set((observation or {}).get("views") or {})
    photos = {vid: v.get("view") for vid, v in (evidence.get("views") or {}).items() if isinstance(v, dict) and vid not in fitted}
    if not photos:
        return None
    return {"photos": photos, "note": "no camera is fitted to these photos: no match render, no contour and no metric compares the model with them; "
                                      "compare them with the sheets by eye (crop_image zooms into a photo)"}


def revision_line(r: dict) -> str:
    return f"{r['id']}{' (from ' + str(r['parent']) + ')' if r.get('parent') else ''}: {r['state']}{', synthetic' if r.get('synthetic') else ''}"


def reply_host_state(ctx: ToolContext, state_note: dict, attached_ids) -> dict:
    """The host_state of one reply: the pending images exclude the ones this reply attaches (they become included when the
    request carrying them is built: every test-pilot-002 build reply said 8 pending with the same 8 attached), and the
    revisions are one line each (the author wrote the rationales; they grew the state 723 -> 2,104 characters)."""
    s = dict(state_note)
    if "pending_images" in s:
        attached = set(attached_ids or [])
        s["pending_images"] = sum(1 for o in ctx.store.observations(state="pending") if o["artifact_id"] not in attached)
    if isinstance(s.get("revisions"), list):
        s["revisions"] = [revision_line(r) if isinstance(r, dict) and "id" in r else r for r in s["revisions"]]
    return s


@contextlib.contextmanager
def lease_renewed(ctx: ToolContext):
    """Renew the runner's lease from a thread while a host-side phase runs (camera fits, the AR harness, the see-through
    fixtures: the worker poll renews only while a worker runs, and archeck's own timeout is 1800 s against a 900 s ttl).
    The thread opens its own connection (sqlite handles are per thread) and stops at the first failure: a lost lease is
    then refused by the runner's fenced commit. Yields a dict of what happened; nothing runs without a heartbeat."""
    state: dict = {}
    if ctx.heartbeat is None:
        yield state
        return
    stop = threading.Event()

    def loop():
        store = None
        try:
            store = Store(ctx.job_dir, clock=ctx.store.clock)
            while not stop.wait(HOST_RENEW_INTERVAL_S):
                store.renew_lease(ctx.holder, ctx.fence, LEASE_TTL_S)
                state["renewals"] = state.get("renewals", 0) + 1
        except Exception as e:  # noqa: BLE001 - LeaseError or a locked database: stop; the commit is fenced
            state["error"] = f"{type(e).__name__}: {e}"
        finally:
            if store is not None:
                with contextlib.suppress(Exception):
                    store.close()

    t = threading.Thread(target=loop, name=f"lease-renewal-{ctx.job_dir.name}", daemon=True)
    t.start()
    try:
        yield state
    finally:
        stop.set()
        t.join(timeout=30)


def budget_notice(remaining_ops: int, *, owner_review: bool = False) -> str:
    """What the author must know about its inference operations: every author turn, critic call and the sealed final
    evaluation costs one. test-pilot-001 (2026-09-28) asked for a critic with one operation left and lost the final.
    Under the owner review (policy owner_review) no final evaluation follows request_delivery: the owner judges the
    candidate live, so the author needs only the operation of the delivery message itself."""
    if owner_review:
        if remaining_ops <= 0:
            return "no inference operation remains: the host hands the best compatible revision you have seen to the owner's review, or ends unresolved"
        if remaining_ops == 1:
            return ("LAST inference operation of this round: your next message must be request_delivery of a compatible revision whose images you "
                    "have all received; the owner then tries it on live and accepts it, asks for changes or stops")
        return (f"{remaining_ops} inference operations remain in this round (each author turn and critic call costs one); request_delivery hands "
                "the revision to the owner's live review, which costs you nothing")
    if remaining_ops <= 0:
        return "no inference operation remains: the host delivers the best compatible revision you have seen, or ends unresolved"
    if remaining_ops == 1:
        return ("LAST inference operation: your next message must be request_delivery of a compatible revision whose images you have all "
                "received; nothing can follow it, and the sealed final evaluation will be skipped for lack of an operation")
    if remaining_ops == 2:
        return ("two inference operations remain: deliver in your next message to leave one for the sealed final evaluation; a critic call "
                "or any other tool would spend it instead")
    return f"{remaining_ops} inference operations remain (each author turn, critic call and the final evaluation costs one); deliver with at least two left"


def state_summary(ctx: ToolContext) -> dict:
    job = ctx.store.job()
    revs = ctx.store.revisions()
    pending = ctx.store.observations(state="pending")
    totals = Budget(ctx.store).totals()
    remaining_ops = int(totals["operations_cap"]) - int(totals["operations_used"])
    return {"current_revision": job["current_revision"], "selected_revision": job["selected_revision"],
            "revisions": [{"id": r["id"], "parent": r["parent_id"], "state": r["state"], "compatible": bool((r.get("compatibility") or {}).get("compatible")),
                           "synthetic": bool(r["synthetic"]), "rationale": (r.get("rationale") or "")[:160]} for r in revs],
            "pending_images": len(pending), "revisions_used": len(revs), "revisions_cap": ctx.policy["max_revisions"],
            "images_per_message": ctx.policy["images_per_request"],
            "operations_used": int(totals["operations_used"]), "operations_cap": int(totals["operations_cap"]), "operations_remaining": remaining_ops,
            "budget_settled_usd": totals["settled_usd"], "budget_cap_usd": totals["cap_usd"],
            "budget_notice": budget_notice(remaining_ops, owner_review=bool(ctx.policy.get("owner_review")))}


def author_visible_evidence(evidence: dict) -> dict:
    """The evidence without the sealed views, sealed inputs and every absolute path (what a worker or the author may see)."""
    ev = json.loads(json.dumps(evidence, default=str))
    ev.pop("held_out", None)
    ev["inputs"] = [{k: v for k, v in r.items() if k not in ("path", "source_path")} for r in ev.get("inputs", []) if not r.get("held_out")]
    for v in ev.get("views", {}).values():
        for k in ("author_photo", "measured_overlay", "path", "source_path", "mask_path"):
            if isinstance(v.get(k), dict):
                v[k] = {kk: vv for kk, vv in v[k].items() if kk != "path"}
            elif k in v:
                v.pop(k, None)
    for k in list(ev):
        if k.endswith("_path") or k in ("masks", "job_dir"):
            ev.pop(k, None)
    ev["sealed"] = "held-out photographs, masks and metrics are not part of this evidence"
    return ev


def compact_evidence_safe(evidence: dict) -> dict:
    try:
        return mauthor.compact_evidence(evidence)
    except Exception as e:  # noqa: BLE001 - synthetic evidence lacks the measured front
        return {"product_id": evidence.get("product_id"), "notes": evidence.get("notes"), "scale": evidence.get("scale"),
                "front": evidence.get("front"), "sides": evidence.get("sides"), "views": {k: {"view": v.get("view"), "flags": v.get("flags")} for k, v in evidence.get("views", {}).items()},
                "note": f"compact evidence unavailable ({type(e).__name__}); raw sections shown"}


def photo_artifacts(ctx: ToolContext) -> list[dict]:
    return [a for a in ctx.store.artifacts(role=AUTHOR_VISIBLE, kind="photo")]


def register_image(ctx: ToolContext, path: Path, *, kind: str, label: str, revision_id: str | None, recipe: dict, required: bool, operation_id: str | None,
                   role: str = AUTHOR_VISIBLE) -> dict:
    row = ctx.artifacts.add_file(path, kind=kind, role=role, label=label, revision_id=revision_id, recipe=recipe, synthetic=(role == SYNTHETIC))
    if role in (AUTHOR_VISIBLE, SYNTHETIC):
        ctx.store.enqueue_observation(row["id"], revision_id=revision_id, operation_id=operation_id, required=required)
    return row


def take_pending_images(ctx: ToolContext, limit: int, *, prefer_revision: str | None = None) -> list[dict]:
    """Reserve up to ``limit`` pending observation images for the output being composed (they are marked included
    by the runner once the request that carries them is persisted)."""
    pending = ctx.store.observations(state="pending")
    if prefer_revision:
        pending.sort(key=lambda o: (o["revision_id"] != prefer_revision, o["id"]))
    rows = []
    for o in pending[:max(0, int(limit))]:
        a = ctx.store.artifact(o["artifact_id"])
        if a is not None:
            rows.append(a)
    return rows


def image_size(a: dict) -> list[int] | None:
    """[width, height] of an image artifact from its catalogue recipe (crop_image needs pixel coordinates), or None."""
    r = a.get("recipe") or {}
    w, h = r.get("width"), r.get("height")
    return [int(w), int(h)] if isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool) and not isinstance(h, bool) else None


def image_blocks_for(ctx: ToolContext, rows: list[dict]) -> list[dict]:
    blocks = []
    for a in rows:
        row, data = ctx.artifacts.read(a["id"], allow_roles=(AUTHOR_VISIBLE, SYNTHETIC))
        # the label says what the image is; the sha256 and the recipe (3.1k characters a build in test-pilot-002) stay in the catalogue.
        # The caption's first key is image_id: runner.sent_image_ids binds each image to it
        meta = {"image_id": a["id"], "label": a["label"], "revision": a.get("revision_id")}
        size = image_size(a)
        if size:
            meta["size"] = size
        blocks.append(text_block(json.dumps(meta)))
        blocks.append(image_block(data, row["media_type"]))
    return blocks


# --------------------------------------------------------------------------- tool handlers
FIRST_MESSAGE_POINTER = ("the evidence, helper reference and rules are in the first message of this conversation (the cached prefix); "
                         "read_evidence returns one section in full; list_evidence with reference: true returns the rules and the helper "
                         "reference again (after a compaction the first message is gone)")
TEMPLE_CLIP_POINTER = (f"the AR runtime draws the temples back to local z {RUNTIME_TEMPLE_CLIP_Z_M} m from the bridge-underside origin (the "
                       f"registered {RUNTIME_TEMPLE_CLIP_BASE_Z_M} m clip plus the renderer's {RUNTIME_HIDDEN_TAIL_M * 1000:.0f} mm hidden tail), fading "
                       f"the arm over the {TEMPLE_CLIP_FADE_MM} mm in front of it: z = the origin's z - {abs(RUNTIME_TEMPLE_CLIP_Z_M) * 1000:.0f} mm in "
                       "your coordinates. Geometry behind it is never drawn; every build reply's temple_clip gives the value the AR harness recorded "
                       f"for that revision. The runtime's continuity model still needs arm geometry back to the registered clip, local z "
                       f"{RUNTIME_TEMPLE_CLIP_BASE_Z_M} m: z = the origin's z - {abs(RUNTIME_TEMPLE_CLIP_BASE_Z_M) * 1000:.0f} mm in your coordinates "
                       f"(temple_clip.continuity_z_mm). Between the two planes the arm is never drawn but must exist on both sides at |x| > "
                       f"{RUNTIME_CONTINUITY_LATERAL_MIN_MM:.0f} mm, or the try-on fails (ar_continuity_failure, a gate); only an object behind the "
                       "registered clip, or one other geometry of its temple covers, may be left out (objects_carrying_continuity names the rest)")


def t_list_evidence(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    # slim on purpose: test-pilot-001 re-read a 33k-character list_evidence output uncached on every turn; the evidence, the
    # helper reference and the rules travel once, in the first (cached) message. A compaction replaces that message, so
    # reference: true brings the rules and the helper reference back on request (args.get: a call recorded before the
    # argument existed carries {})
    photos = [{"artifact_id": a["id"], "label": a["label"], "sha256": a["sha256"]} for a in photo_artifacts(ctx)]
    text = {"tools_version": TOOLS_VERSION, "photos": photos, "note": FIRST_MESSAGE_POINTER,
            "limits": {k: ctx.policy[k] for k in ("max_revisions", "max_worker_seconds", "images_per_request", "wall_minutes")},
            "temple_clip": list_evidence_clip(ctx), "state": state_summary(ctx)}
    # an owner's re-edit job: the request verbatim and the modules edit_program accepts (the first message states both too)
    if ctx.policy.get("owner_instruction"):
        text["owner_instruction"] = str(ctx.policy["owner_instruction"])
    if ctx.policy.get("editable_modules") is not None:
        text["editable_modules"] = list(ctx.policy["editable_modules"])
    if args.get("reference") is True:
        text.update(rules=first_message_rules(ctx), helper_reference=mauthor.helper_reference())
    return ToolResult(text)


def list_evidence_clip(ctx: ToolContext) -> dict:
    """The clip rule, plus both planes of the newest revision with an export (the one a next edit most likely starts from):
    the drawn plane (z_mm) and the registered clip the continuity model needs geometry down to (continuity_z_mm)."""
    out: dict = {"rule": TEMPLE_CLIP_POINTER}
    for rev in reversed(ctx.store.revisions()):
        if (ctx.revision_dir(rev["id"]) / "export.json").is_file():
            block = revision_temple_clip(ctx, rev)
            out.update(revision=rev["id"], z_mm=block["z_mm"], source=block["source"], continuity_z_mm=block["continuity_z_mm"])
            for key in ("objects_never_drawn", "objects_carrying_continuity"):
                if block.get(key):
                    out[key] = block[key]
            break
    return out


def first_message_rules(ctx: ToolContext) -> list[str]:
    """The rules exactly as the first message states them (``runner.agentic_rules`` over the shared author rules, when the
    runner has it), so the copy fetched after a compaction is the copy the conversation started with."""
    try:
        from .runner import agentic_rules
    except ImportError:
        return [r.replace("(request_views)", "(render_views)") for r in mauthor.RULES]      # runner.agentic_rules' rename
    return agentic_rules(mauthor.RULES, compact_evidence_safe(ctx.evidence))


def t_read_evidence(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    ev = author_visible_evidence(ctx.evidence)
    section = args["section"]
    if section == "all":
        return ToolResult({"evidence": ev})
    key = {"product_reading": "intake_reading", "provenance": "provenance", "reliability": "front"}.get(section, section)
    value = ev.get(key)
    if section == "reliability":
        value = {"evidence_reliability": (ev.get("front") or {}).get("evidence_reliability"), "code_measured": (ev.get("front") or {}).get("code_measured"),
                 "gate_overrides": ev.get("gate_overrides")}
    return ToolResult({"section": section, "value": value, "note": None if value is not None else "this section is absent from the evidence"})


def t_crop_image(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    row, data = ctx.artifacts.read(args["artifact_id"], allow_roles=(AUTHOR_VISIBLE, SYNTHETIC))
    if row["kind"] not in ("photo", "render", "crop", "sheet"):
        raise ToolError(f"{args['artifact_id']} is not an image")
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        x0, y0, x1, y1 = args["x0"], args["y0"], args["x1"], args["y1"]
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise ToolError(f"crop [{x0},{y0},{x1},{y1}] is outside the {w}x{h} image")
        if (x1 - x0) * (y1 - y0) < 16:
            raise ToolError("crop too small")
        crop = im.convert("RGB").crop((x0, y0, x1, y1))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
    out = ctx.artifacts.add_bytes(buf.getvalue(), kind="crop", role=row["role"], suffix=".png", label=f"crop of {row['id']} [{x0},{y0},{x1},{y1}]",
                                  revision_id=row.get("revision_id"), parent_id=row["id"],
                                  recipe={"parent": row["id"], "parent_sha256": row["sha256"], "crop_xyxy": [x0, y0, x1, y1], "synthetic": row["role"] == SYNTHETIC},
                                  synthetic=(row["role"] == SYNTHETIC))
    ctx.store.enqueue_observation(out["id"], revision_id=row.get("revision_id"), operation_id=op["id"], required=False)
    return ToolResult({"crop": {"artifact_id": out["id"], "parent": row["id"], "crop_xyxy": [x0, y0, x1, y1], "sha256": out["sha256"]}},
                      images=[out])


def t_read_program(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    if args.get("revision_id") is None and ctx.store.job()["current_revision"] is None:
        # not an error (test-pilot-001 spent a paid turn on it): there is simply nothing to read yet
        return ToolResult({"revision": None, "modules": {}, "module_order": MODULE_KEYS,
                           "note": "no program exists yet: edit_program with base_revision_id null (replace mode for every module you write, build_now true) "
                                   "creates the first revision; " + FIRST_MESSAGE_POINTER})
    rev = revision_or_raise(ctx, args.get("revision_id"), allow_null_current=True)
    mods = modules_of(ctx, rev)
    return ToolResult({"revision": rev["id"], "parent": rev["parent_id"], "state": rev["state"], "program_set_sha256": rev["program_set_sha256"],
                       "modules": {n: {"sha256": sha256_bytes(s.encode("utf-8")), "bytes": len(s.encode("utf-8")), "source": s} for n, s in mods.items()},
                       "module_order": MODULE_KEYS})


def _occurrence_lines(source: str, needle: str) -> list[int]:
    """1-based line numbers where ``needle`` starts (overlapping matches counted from every start)."""
    lines, start = [], 0
    while True:
        k = source.find(needle, start)
        if k < 0:
            return lines
        lines.append(source.count(chr(10), 0, k) + 1)
        start = k + 1


def apply_patch(source: str, edits, *, module: str | None = None) -> str:
    """Exact find/replace edits, applied in order, all or nothing. A find must occur exactly once unless the edit carries
    ``occurrence`` (1-based) to choose one; a refusal names the line numbers of every match (or the closest lines when
    there is none) so the author can fix the edit in one turn instead of guessing (test-pilot-001 lost a turn to
    'occurs 2 times'). EVERY failing edit is reported in the one ToolError, as ``edit #k of n: ...`` (1-based): a
    failing edit is skipped and the later edits are still checked against the text as patched so far."""
    prefix = f"{module}: " if module else ""
    if not isinstance(edits, list) or not edits or len(edits) > 64:
        raise ToolError(f"{prefix}a patch is a JSON list of 1..64 {{find, replace, occurrence?}} edits")
    out = source
    n = len(edits)
    failures: list[str] = []
    for i, e in enumerate(edits, start=1):
        tag = f"{prefix}edit #{i} of {n}"
        keys = set(e) if isinstance(e, dict) else set()
        if (not isinstance(e, dict) or not {"find", "replace"} <= keys or keys - {"find", "replace", "occurrence"}
                or not isinstance(e["find"], str) or not isinstance(e["replace"], str) or not e["find"]):
            failures.append(f"{tag}: must be {{find: non-empty string, replace: string, occurrence?: 1-based integer}}")
            continue
        occ = e.get("occurrence")
        if occ is not None and (type(occ) is not int or occ < 1):
            failures.append(f"{tag}: occurrence must be a 1-based integer")
            continue
        hits = _occurrence_lines(out, e["find"])
        if not hits:
            first = next((ln.strip() for ln in e["find"].splitlines() if ln.strip()), e["find"])
            close = difflib.get_close_matches(first, [ln.strip() for ln in out.splitlines() if ln.strip()], n=3, cutoff=0.6)
            failures.append(f"{tag}: 'find' text occurs 0 times (nothing changed); closest lines in the module: {close}; "
                            "copy the exact text from the module, or replace the module")
            continue
        if occ is None and len(hits) != 1:
            failures.append(f"{tag}: 'find' text occurs {len(hits)} times at lines {hits} (nothing changed); add more surrounding "
                            "text to make it unique, or add occurrence: k (1-based) to choose one")
            continue
        if occ is not None and occ > len(hits):
            failures.append(f"{tag}: occurrence {occ} requested but the find text occurs {len(hits)} times at lines {hits} (nothing changed)")
            continue
        pos = -1
        for _ in range(occ or 1):
            pos = out.find(e["find"], pos + 1)
        out = out[:pos] + e["replace"] + out[pos + len(e["find"]):]
    if failures:
        raise ToolError("; ".join(failures))
    return out


def insert_program_revision(store: Store, modules: dict[str, str], *, module_source: str, rationale: str, synthetic: bool,
                            set_sha256: str | None = None) -> dict:
    """A parentless revision holding ``modules`` (the seed of a job, a rebuild's shadow revision): the row with every
    module's sha256, bytes and ``module_source``, the working copy revisions/<rid>/program/*.py, and current_revision.
    ``set_sha256`` (the program set sha256) defaults to the modules' own (a rebuild passes its source revision's, verified by the caller)."""
    enc = {n: s.encode("utf-8") for n, s in modules.items()}
    rev = store.insert_revision(parent_id=None, program_set_sha256=set_sha256 or program_set_sha256(modules),
                                modules={n: {"sha256": sha256_bytes(b), "bytes": len(b), "source": module_source} for n, b in enc.items()},
                                rationale=rationale, synthetic=synthetic)
    pdir = store.job_dir / "revisions" / rev["id"] / "program"
    pdir.mkdir(parents=True)
    for n, b in enc.items():
        atomic_write(pdir / f"{n}.py", b)
    store.update_job(current_revision=rev["id"])
    return rev


def t_edit_program(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    if len(ctx.store.revisions()) >= int(ctx.policy["max_revisions"]):
        raise ToolError(f"the revision limit ({ctx.policy['max_revisions']}) is reached: select or deliver an existing compatible revision")
    base = None
    base_modules: dict[str, str] = {}
    if args.get("base_revision_id") is not None:
        base = revision_or_raise(ctx, args["base_revision_id"])
        base_modules = modules_of(ctx, base)
    modules: dict[str, str] = {}
    changed = []
    # every module is validated before anything is refused: one ToolError names every failing module and edit, so a
    # bad multi-module patch costs one turn, not one per module (test-pilot-001 spent 6 of 10 operations on host-induced turns)
    failures: list[str] = []
    # an owner's re-edit job names the modules the author may change (policy editable_modules; None: all): any other module
    # must be inherited, and a change to it is refused here, before a revision or a build exists
    editable = ctx.policy.get("editable_modules")
    locked = [] if editable is None else [n for n in MODULE_KEYS if n not in set(editable)]
    if editable is not None:
        # a locked module is carried by inheriting it, so the base must hold it: a null base (a parentless revision) or a base
        # without a module the owner's revision has would drop it while every spec reads 'inherit' (the round-7 bypass). A
        # locked module no revision of the job holds (the seed never had one) has nothing to drop.
        held = [n for n in locked if any(n in (r.get("modules") or {}) for r in ctx.store.revisions())]
        if base is None:
            failures.append(f"base_revision_id null would create a revision without the locked modules ({', '.join(held) or 'none held yet'}); "
                            "start from the owner's revision or one of its descendants")
        else:
            missing = [n for n in held if n not in base_modules]
            if missing:
                failures.append(f"base {base['id']} lacks the locked module(s) {', '.join(missing)}, which inheriting cannot restore; start from "
                                "the owner's revision or a descendant that carries them")
            # an owner's locked change round: the locked modules are the REVIEWED revision's bytes, not merely present in the base
            lock_problem = None if missing else owner_lock_refusal(ctx.store, base, delivery=False)
            if lock_problem:
                failures.append(f"base {lock_problem}")
    for name in locked:
        spec = args["modules"][name]
        if spec["mode"] == "inherit" or (spec["mode"] == "replace" and base is not None and spec.get("content") == base_modules.get(name)):
            continue
        failures.append(f"{name}: not editable in this job ({spec['mode']} refused; inherit it)")
    if failures:
        raise ToolError("; ".join(failures) + f" (editable modules: {', '.join(editable) or 'none'}; every other module is inherited from the base "
                        "revision; nothing changed: no revision was created, nothing was built)")
    for name in MODULE_KEYS:
        spec = args["modules"][name]
        mode = spec["mode"]
        expected = spec.get("expected_base_sha256")
        if mode == "inherit" or name in locked:
            if name in base_modules:
                modules[name] = base_modules[name]
            continue
        if mode == "remove":
            if name in base_modules:
                changed.append(name)
            continue
        try:
            if expected is not None and sha256_bytes(base_modules.get(name, "").encode("utf-8")) != expected:
                raise ToolError(f"{name}: expected_base_sha256 does not match the base revision's module (stale view); the current sha256 of every "
                                f"module came with the edit_program / build result of {base['id'] if base else 'the base'} (or read_program)")
            if mode == "replace":
                if not isinstance(spec.get("content"), str) or not spec["content"].strip():
                    raise ToolError(f"{name}: replace needs non-empty content")
                src = spec["content"]
            else:  # patch
                if expected is None:
                    raise ToolError(f"{name}: a patch needs expected_base_sha256")
                if name not in base_modules:
                    raise ToolError(f"{name}: cannot patch a module the base does not have")
                try:
                    edits = json.loads(spec.get("content") or "")
                except json.JSONDecodeError as e:
                    raise ToolError(f"{name}: patch content is not JSON: {e}") from None
                src = apply_patch(base_modules[name], edits, module=name)      # '<module>: edit #k of n: ...' per failing edit
            if len(src.encode("utf-8")) > MAX_MODULE_BYTES:
                raise ToolError(f"{name}: exceeds {MAX_MODULE_BYTES} bytes")
            try:
                compile(src, f"<{name}>", "exec")
            except SyntaxError as e:
                raise ToolError(f"{name}: syntax error: {e}") from None
        except ToolError as e:
            failures.append(str(e))
            continue
        modules[name] = src
        changed.append(name)
    if failures:
        raise ToolError("; ".join(failures) + " (nothing changed: no revision was created, nothing was built)")
    if not modules:
        raise ToolError("a revision needs at least one module")
    if not changed and base is not None:
        raise ToolError("nothing changed relative to the base revision; edit at least one module")
    rationale = str(args.get("rationale", ""))[:4000]
    rev = ctx.store.insert_revision(parent_id=base["id"] if base else None, program_set_sha256=program_set_sha256(modules),
                                    modules={n: {"sha256": sha256_bytes(s.encode("utf-8")), "bytes": len(s.encode("utf-8")),
                                                 "source": "changed" if n in changed else f"inherited:{base['id']}"} for n, s in modules.items()},
                                    rationale=rationale, synthetic=ctx.worker.synthetic)
    pdir = ctx.revision_dir(rev["id"]) / "program"
    pdir.mkdir(parents=True)
    for n, s in modules.items():
        atomic_write(pdir / f"{n}.py", s.encode("utf-8"))
        ctx.artifacts.add_bytes(s.encode("utf-8"), kind="program", role=HOST_ONLY, suffix=".py", label=f"{rev['id']}/{n}.py", revision_id=rev["id"])
    ctx.store.update_job(current_revision=rev["id"])
    ctx.store.event("revision_created", revision=rev["id"], parent=rev["parent_id"], changed=changed, expected_changes=args.get("expected_changes"))
    # the per-module sha256 travels with every result: a later patch needs no read_program round trip (test-pilot-001 paid $0.53 for one)
    text = {"revision": rev["id"], "parent": rev["parent_id"], "changed_modules": changed, "program_set_sha256": rev["program_set_sha256"],
            "modules": module_shas(rev), "built": False}
    if args.get("build_now"):
        built = build_revision(ctx, rev["id"], op, deliver_if_compatible=bool(args.get("deliver_if_compatible")))
        # the build text carries the module hashes (top-level 'modules'); the created block keeps only the lineage
        built.text = {"created": {k: text[k] for k in ("revision", "parent", "changed_modules")}, **built.text}
        built.new_revision = rev["id"]
        return built
    if args.get("deliver_if_compatible"):
        text["note"] = "deliver_if_compatible was recorded but nothing is built yet: call build_candidate"
    return ToolResult(text, new_revision=rev["id"])


def t_build_candidate(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    if rev["state"] not in ("created", "build_failed", "interrupted", "export_failed"):
        raise ToolError(f"revision {rev['id']} is {rev['state']}: it was already built; edit_program creates a new revision")
    return build_revision(ctx, rev["id"], op, deliver_if_compatible=bool(args.get("deliver_if_compatible")))


def t_inspect_scene(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    bdir = ctx.revision_dir(rev["id"]) / "build"
    result = load_json_or_none(bdir / "result.json")
    if result is None:
        raise ToolError(f"revision {rev['id']} has no build result")
    export = load_json_or_none(ctx.revision_dir(rev["id"]) / "export.json") or {}
    contract = export.get("contract") or {}
    out = {"revision": rev["id"], "program_set_sha256": rev["program_set_sha256"], "worker": rev.get("worker"), "synthetic": bool(rev["synthetic"]),
           "build_ok": result.get("ok"), "inventory": result.get("inventory"), "notes": result.get("notes"),
           "module_results": [{k: m.get(k) for k in ("name", "ok", "error", "seconds")} for m in result.get("module_results", [])],
           "export": {"error": export.get("error"), "contract_ok": contract.get("ok"), "failures": contract.get("failures"),
                      "failed_checks": {k: {kk: vv for kk, vv in v.items() if kk in ("value", "limit")} for k, v in (contract.get("checks") or {}).items() if not v.get("pass")},
                      "parts": export.get("parts"), "notes": export.get("notes"),
                      "topology": {k: {kk: v.get(kk) for kk in ("watertight", "boundary_edges", "nonmanifold_edges", "misoriented_edges", "degenerate_faces", "triangles")}
                                   for k, v in (contract.get("parts") or {}).items()}}}
    audit = export_audit(export)
    if audit is not None:
        out["export"]["audit"] = audit
    obs = rev.get("observation") or {}
    if obs.get("bbox_mm"):
        out["bbox_mm"] = obs["bbox_mm"]
    return ToolResult(out)


def t_render_views(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    blend = ctx.revision_dir(rev["id"]) / "build" / "candidate.blend"
    if rev["state"] in ("created", "build_failed", "interrupted") or not blend.is_file():
        raise ToolError(f"revision {rev['id']} has no built scene to render")
    ids = [v["id"] for v in args["views"]]
    if len(set(ids)) != len(ids):
        raise ToolError("view ids must be unique")
    specs = [{"id": v["id"], "kind": v["kind"], "width": 640, "height": 480, "transparent": True,
              "camera": {"type": "orbit", "yaw": v["yaw"], "pitch": v["pitch"], "roll": v["roll"], "ortho": v["ortho"], "px_per_mm": v["px_per_mm"],
                         "target": v["target"], "distance_mm": 600}} for v in args["views"]]
    result = run_worker_operation(ctx, op, mode="render_only", modules={}, module_order=[], renders=specs, evidence=None, blend_bytes=blend.read_bytes(),
                                  revision=rev, commit_state=False, samples=AUTHOR_VIEW_SAMPLES)
    if result["status"] != "completed":
        return ToolResult({"revision": rev["id"], "rendered": [], "error": result.get("error"), "category": result.get("category"), "logs": result.get("logs")})
    images, rendered, failed = [], [], []
    for row in (result["result"] or {}).get("renders", []):
        p = render_file_path(result["staging"], row.get("id"))     # defensive: the receipt was verified, an escape here is still never read
        if p is None:
            ctx.store.event("worker_render_refused", operation=op["id"], render_id=str(row.get("id"))[:80], reason="id escapes the staging renders folder")
            failed.append({"id": str(row.get("id"))[:80], "error": "render id refused"})
            continue
        if row.get("error") or not p.is_file():
            failed.append({"id": row["id"], "error": row.get("error") or "no file"})
            continue
        a = register_image(ctx, p, kind="render", label=f"{rev['id']}: requested view {row['id']} ({row['kind']}, yaw {row['camera']['yaw']}, pitch {row['camera']['pitch']})",
                           revision_id=rev["id"], recipe={"view": row["id"], "kind": row["kind"], "camera": row["camera"], "renderer": "blender-eevee",
                                                          "worker": result["identity"].get("kind"), "synthetic": ctx.worker.synthetic},
                           required=False, operation_id=op["id"], role=SYNTHETIC if ctx.worker.synthetic else AUTHOR_VISIBLE)
        rendered.append({"id": row["id"], "artifact_id": a["id"]})
        images.append(a)
    text = {"revision": rev["id"], "rendered": rendered, "failed": failed}
    cap = int(ctx.policy["images_per_request"])
    if len(images) > cap:
        text["deferred_images"] = len(images) - cap
        text["note"] = "more images than one message carries; the rest are queued: call fetch_pending_images"
    return ToolResult(text, images=images[:cap])


def pose_key(kind: str, yaw: float, pitch: float, roll: float) -> tuple:
    """A comparable pose (to 0.5 deg); every asset-back view is the same inspection."""
    return ("asset-back",) if kind == "asset-back" else ("pose", round(yaw * 2) / 2, round(pitch * 2) / 2, round(roll * 2) / 2)


def requested_pose_key(v: dict) -> tuple:
    return pose_key(v["type"], float(v["yaw"]), float(v["pitch"]), float(v["roll"]))


def ar_view_spec(v: dict) -> dict:
    """The harness spec of a requested view (the ar_views entry of the manifest)."""
    if v["type"] == "asset-back":
        return {"id": v["id"], "type": "asset-back"}
    return {"id": v["id"], "yaw_degrees": float(v["yaw"]), "pitch_degrees": float(v["pitch"]), "roll_degrees": float(v["roll"])}


def ar_pose_label(v: dict) -> str:
    return "asset-back" if v["type"] == "asset-back" else f"yaw {v['yaw']:g}, pitch {v['pitch']:g}, roll {v['roll']:g}"


def sheet_views(ctx: ToolContext, rev: dict) -> list[dict]:
    """The view specs the observation's AR run rendered (its manifest), or observe.AR_VIEWS when it is unreadable."""
    m = load_json_or_none(ctx.revision_dir(rev["id"]) / "observe" / "ar" / "manifest.json")
    views = m.get("ar_views") if isinstance(m, dict) else None
    return [v for v in views if isinstance(v, dict) and isinstance(v.get("id"), str)] if isinstance(views, list) else list(AR_VIEWS)


def sheet_ar_renders(ctx: ToolContext, rev: dict) -> dict:
    """{pose_key: (view id, path)} of the observation's own AR renders (the ar sheet's tiles at full resolution), only when
    its stored harness result validates for this revision's GLB (``archeck.validate_ar_result``: the model sha, every view
    rendered once, the bytes matching the report) and each file lies in the observation's ar folder; {} otherwise."""
    from bsa import archeck
    ar_dir = ctx.revision_dir(rev["id"]) / "observe" / "ar"
    res = load_json_or_none(ar_dir / "archeck.json")
    views = sheet_views(ctx, rev)
    if not isinstance(res, dict) or not rev.get("glb_sha256") or not views:
        return {}
    try:
        val = archeck.validate_ar_result(res, expected_models={"candidate": rev["glb_sha256"]}, expected_views=[v["id"] for v in views])
    except Exception:  # noqa: BLE001 - a malformed stored result is simply not reused
        return {}
    if not val.get("ok"):
        return {}
    files = {str(r.get("view")): Path(str(r.get("path"))) for r in ((res.get("models") or {}).get("candidate") or {}).get("render_files") or []}
    out = {}
    for v in views:
        p = files.get(v["id"])
        if p is not None and p.is_file() and contained(p, ar_dir):
            out[pose_key(*ar_view_pose(v))] = (v["id"], p)
    return out


def t_render_ar_views(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    comp = rev.get("compatibility") or {}
    if not comp.get("contract_ok"):
        raise ToolError(f"revision {rev['id']} has no contract-valid export; the AR renderer loads compatible assets only")
    if not ctx.ar:
        raise ToolError("the AR renderer is disabled for this job (--no-ar)")
    if ctx.worker.synthetic:
        raise ToolError("AR views are not available with the synthetic worker (no real asset exists)")
    glb = ctx.revision_dir(rev["id"]) / "model.glb"
    if not glb.is_file() or sha256_bytes(glb.read_bytes()) != rev["glb_sha256"]:
        raise ToolError(f"the GLB of {rev['id']} is missing or changed")
    requested = args["views"]
    ids = [v["id"] for v in requested]
    if len(set(ids)) != len(ids):
        raise ToolError("view ids must be unique")
    for v in requested:
        if v["type"] == "asset-back" and any(v[k] for k in AR_POSE_LIMITS):
            raise ToolError(f"view {v['id']}: asset-back inspects the settled asset from behind; its yaw, pitch and roll must be 0")
    from bsa import archeck
    background = args["background"]
    # test-pilot-002 op0010 asked for front / angled / back and got pixel-identical copies of the ar sheet's tiles after a harness run:
    # a view the observation's own AR run already rendered (same pose, checker background, same GLB) is returned from it
    sheet = sheet_ar_renders(ctx, rev) if background == "checker" else {}
    reuse = {v["id"]: sheet[requested_pose_key(v)] for v in requested if requested_pose_key(v) in sheet}
    fresh = [v for v in requested if v["id"] not in reuse]
    images, rendered, reasons, valid = [], [], [], True
    for v in requested:
        if v["id"] in reuse:
            src_view, path = reuse[v["id"]]
            a = register_image(ctx, path, kind="render", label=f"{rev['id']}: actual AR renderer, view {v['id']} (the ar sheet's {src_view} render, full resolution)",
                               revision_id=rev["id"], recipe={"view": v["id"], "renderer": "actual-ar", "kind": "ar", "pose": ar_pose_label(v), "reused_from": src_view,
                                                              "background": background}, required=False, operation_id=op["id"])
            rendered.append({"view": v["id"], "artifact_id": a["id"], "reused": True})
            images.append(a)
    if fresh:
        specs = [ar_view_spec(v) for v in fresh]
        out_dir = ctx.revision_dir(rev["id"]) / "observe" / f"ar_extra_{op['id']}"
        bbox = (rev.get("observation") or {}).get("bbox_mm") or [[0, 0, 0], [140, 0, 0]]
        kw = {} if AR_BACKGROUNDS[background] is None else {"background": "solid", "background_color": AR_BACKGROUNDS[background]}
        with lease_renewed(ctx):
            res = archeck.run({"candidate": glb}, out_dir, ar_views=specs, width_mm={"candidate": float(bbox[1][0] - bbox[0][0])}, timeout_s=AR_VIEWS_TIMEOUT_S, **kw)
        val = archeck.validate_ar_result(res, expected_models={"candidate": rev["glb_sha256"]}, expected_views=[s["id"] for s in specs])
        valid, reasons = bool(val["ok"]), val.get("reasons")
        by_view = {str(r.get("view")): Path(r["path"]) for r in (res["models"].get("candidate") or {}).get("render_files", []) if r.get("path")}
        for v in fresh:
            pp = by_view.get(v["id"])
            if pp is None or not pp.is_file():
                rendered.append({"view": v["id"], "artifact_id": None, "reused": False, "error": "not rendered"})
                continue
            a = register_image(ctx, pp, kind="render", label=f"{rev['id']}: actual AR renderer, view {v['id']} ({ar_pose_label(v)}, {background} background)",
                               revision_id=rev["id"], recipe={"view": v["id"], "renderer": "actual-ar", "ar_runtime": res.get("source_snapshot_stable"), "kind": "ar",
                                                              "pose": ar_pose_label(v), "background": background}, required=False, operation_id=op["id"])
            rendered.append({"view": v["id"], "artifact_id": a["id"], "reused": False})
            images.append(a)
    order = {i: k for k, i in enumerate(ids)}
    rendered.sort(key=lambda r: order[r["view"]])
    images.sort(key=lambda a: order.get(a["recipe"].get("view"), 0))
    text = {"revision": rev["id"], "rendered": rendered, "report_valid": valid, "reasons": reasons}
    if reuse:
        text["note"] = (f"{', '.join(reuse)}: already on the ar sheet of {rev['id']} ({describe_ar_views(sheet_views(ctx, rev))}): returned as those renders "
                        "at full resolution, nothing was re-rendered" + ("" if fresh else "; other poses or a solid background render something new"))
    cap = int(ctx.policy["images_per_request"])
    if len(images) > cap:
        text["deferred_images"] = len(images) - cap
        text["note"] = "more images than one message carries; the rest are queued: call fetch_pending_images"
    return ToolResult(text, images=images[:cap])


def t_measure_candidate(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    obs = rev.get("observation")
    if obs is None:
        raise ToolError(f"revision {rev['id']} has not been observed")
    out = {"revision": rev["id"], "measurements": {k: v for k, v in obs.items() if k != "measurement_fingerprint_sha256"},
           "compatibility": {k: v for k, v in (rev.get("compatibility") or {}).items() if k != "glb_sha256"},
           "note": "measurements against the author-visible photographs; only automatic_gate decides anything automatically; unmeasured means the host "
                   "could not measure it, not that it is fine"}
    unreliable = unreliable_metrics(obs)
    if obs.get("summary"):
        out["automatic_gate"] = automatic_gate(ctx, obs["summary"], unreliable)
    if unreliable:
        out["measurements_unreliable"] = unreliable
    return ToolResult(out)


def t_select_revision(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    verify_compatible_bytes(ctx, rev)
    ctx.store.update_job(selected_revision=rev["id"])
    ctx.store.event("revision_selected", revision=rev["id"])
    return ToolResult({"selected_revision": rev["id"], "glb_sha256": rev["glb_sha256"], "compatibility": rev.get("compatibility")}, selected=rev["id"])


def verify_compatible_bytes(ctx: ToolContext, rev: dict) -> None:
    comp = rev.get("compatibility") or {}
    if not comp.get("compatible"):
        raise ToolError(f"revision {rev['id']} is not compatible ({comp.get('reasons') or rev['state']})")
    if not any(o["required"] for o in ctx.store.observations(revision_id=rev["id"])):
        raise ToolError(f"revision {rev['id']} produced no images you could receive; a revision you have not seen rendered cannot be selected or delivered")
    if rev["synthetic"]:
        return
    glb = ctx.revision_dir(rev["id"]) / "model.glb"
    if not glb.is_file() or sha256_bytes(glb.read_bytes()) != rev["glb_sha256"]:
        raise ToolError(f"the GLB bytes of {rev['id']} changed since the build; it cannot be selected")


def t_fetch_pending_images(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rows = take_pending_images(ctx, int(args["max_images"]))
    remaining = len(ctx.store.observations(state="pending")) - len(rows)
    return ToolResult({"images": [{"artifact_id": a["id"], "label": a["label"], "revision": a.get("revision_id")} for a in rows], "remaining_pending": remaining},
                      images=rows)


def t_request_critic(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    if rev.get("observation") is None:
        raise ToolError(f"revision {rev['id']} has no renders to criticise yet")
    if ctx.critic is None:
        raise ToolError("no critic is configured for this job (--critic none)")
    verdict = ctx.critic(ctx, rev, str(args.get("question", ""))[:1500])
    return ToolResult({"revision": rev["id"], "critique": verdict}, critic=verdict)


def t_request_delivery(ctx: ToolContext, args: dict, op: dict) -> ToolResult:
    rev = revision_or_raise(ctx, args["revision_id"])
    verify_compatible_bytes(ctx, rev)
    lock_problem = owner_lock_refusal(ctx.store, rev, delivery=True)
    if lock_problem:
        raise ToolError(lock_problem)
    unack = [o for o in ctx.store.observations(revision_id=rev["id"]) if o["required"] and o["state"] != "acknowledged"]
    if unack:
        raise ToolError(f"revision {rev['id']} has {len(unack)} required images you have not received yet ({[o['artifact_id'] for o in unack]}); "
                        "call fetch_pending_images until none remain, then request delivery")
    delivery = {"revision": rev["id"], "status_claim": args["status_claim"], "note": str(args.get("note", ""))[:4000], "glb_sha256": rev["glb_sha256"]}
    ctx.store.update_job(selected_revision=rev["id"])
    return ToolResult({"delivery_requested": delivery, "note": delivery_reply_note(ctx.policy)}, delivery=delivery)


OWNER_REVIEW_NOTE = ("the host verifies these exact bytes and hands the revision to the owner, who tries it on live in the AR mirror; nothing "
                     "is delivered until the owner accepts it. If the owner asks for changes, their words arrive as the next message of this "
                     "conversation with the measurements that relate to them and a new allowance; otherwise the job ends. This is not acceptance")


def delivery_reply_note(policy: dict) -> str:
    """request_delivery's reply: the owner review (policy owner_review) or the host's final checks and the sealed evaluation."""
    if policy.get("owner_review"):
        return OWNER_REVIEW_NOTE
    return "the host runs its final checks and the sealed evaluation; this is not acceptance"


HANDLERS = {"list_evidence": t_list_evidence, "read_evidence": t_read_evidence, "crop_image": t_crop_image, "read_program": t_read_program,
            "edit_program": t_edit_program, "inspect_scene": t_inspect_scene, "build_candidate": t_build_candidate, "render_views": t_render_views,
            "render_ar_views": t_render_ar_views, "measure_candidate": t_measure_candidate, "select_revision": t_select_revision,
            "fetch_pending_images": t_fetch_pending_images, "request_critic": t_request_critic, "request_delivery": t_request_delivery}
assert set(HANDLERS) == set(TOOLS)


def execute(ctx: ToolContext, op: dict) -> ToolResult:
    """Run one validated tool operation; ToolError and unexpected failures become structured results."""
    name = op["tool_name"]
    try:
        return HANDLERS[name](ctx, op["args"], op)
    except LeaseError:
        raise           # another runner owns the job: nothing is reported or journaled under a stale lease; the runner stops
    except ToolError as e:
        return ToolResult({"error": str(e), "tool": name, "state": state_summary(ctx)})
    except (WorkerRefused, WorkerError) as e:
        return ToolResult({"error": f"worker: {e}", "tool": name, "category": "worker", "state": state_summary(ctx)})
    except Exception as e:  # noqa: BLE001 - a host bug must not crash the loop silently; it is reported and journaled
        ctx.store.event("tool_exception", tool=name, error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-4000:])
        return ToolResult({"error": f"host failure in {name}: {type(e).__name__}: {e}", "tool": name, "category": "host", "state": state_summary(ctx)})


# --------------------------------------------------------------------------- worker operations
def load_json_or_none(p: Path):
    """The JSON document at ``p``, or None when it cannot be read or parsed for ANY reason (review, rebuild and the
    tools read optional records through it)."""
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def render_file_path(folder: Path, render_id) -> Path | None:
    """``<folder>/<id>.png`` for a valid render id, or None. The harness writes every render beside ``result.json``
    (``modeler/blender/harness.py`` ``render_views``; the synthetic worker mirrors that layout). An id that is not
    ``^[a-z0-9_-]{1,40}$`` or whose path does not resolve inside ``folder`` is never used for host filesystem access (the
    defensive check at the use sites; ``verify_worker_receipt`` refuses such a receipt before anything is registered)."""
    if not isinstance(render_id, str) or not RENDER_ID_RE.fullmatch(render_id):
        return None
    folder = Path(folder)
    p = folder / f"{render_id}.png"
    if p.resolve(strict=False).parent != folder.resolve(strict=False):
        return None
    return p


def verify_worker_receipt(ingested: Ingested, *, requested_renders: list[dict]) -> list[str]:
    """Cross-check what a worker result NAMES against what this operation requested and what was actually ingested.

    Every render row must carry one of the requested ids (valid pattern, at most once) and, unless it reports an error,
    its file must be exactly ``<id>.png`` at the output root among the ingested files (beside ``result.json``: the
    harness layout), a regular file resolved inside the staging folder; the worker's own ``path`` spelling is never used
    by the host and only has to name that file. ``parts_npz`` / ``materials_json`` / ``blend`` may only name the fixed basenames the harness writes, each
    ingested at the staging root. Any violation is an ingestion issue: the operation fails and nothing is registered."""
    issues: list[str] = []
    r = ingested.result or {}
    have = {f["rel"] for f in ingested.files}
    staging = Path(ingested.staging)
    requested = {s.get("id") for s in (requested_renders or []) if isinstance(s, dict)}
    rows = r.get("renders")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        issues.append(f"result.json renders is {type(rows).__name__}, not a list")
        rows = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"render row {i} is not an object")
            continue
        rid = row.get("id")
        if not isinstance(rid, str) or not RENDER_ID_RE.fullmatch(rid):
            issues.append(f"render row {i}: id {str(rid)[:80]!r} is not a valid render id")
            continue
        if rid not in requested:
            issues.append(f"render {rid!r} was not requested by this operation")
        if rid in seen:
            issues.append(f"render {rid!r} appears more than once")
        seen.add(rid)
        rel = f"{rid}.png"
        path = row.get("path")
        if path is not None and (not isinstance(path, str) or Path(path.replace("\\", "/")).name != rel):
            issues.append(f"render {rid!r}: path {str(path)[:120]!r} does not name {rel}")
        if row.get("error") is not None:
            continue            # a failed render needs no file; its id was still validated above
        if rel not in have:
            issues.append(f"render {rid!r}: {rel} was not ingested")
            continue
        p = staging / rel
        try:
            if not contained(p, staging):
                raise ValueError("escapes the staging folder")
            regular_file_or_raise(p)
        except Exception as e:  # noqa: BLE001 - ArtifactError, OSError or the escape above
            issues.append(f"render {rid!r}: {rel} is not a contained regular file ({e})")
    for key, name in HARNESS_OUTPUT_NAMES.items():
        v = r.get(key)
        if not v:
            continue
        if not isinstance(v, str):
            issues.append(f"result.json {key} is {type(v).__name__}, not a path string")
            continue
        norm = v.replace("\\", "/")
        parts = [x for x in norm.split("/") if x]
        if not parts or parts[-1] != name or ".." in parts:
            issues.append(f"result.json names {key}={v[:120]!r}; the harness writes only {name}")
            continue
        if name not in have:
            issues.append(f"result.json names {key}={name} which was not ingested at the output root")
    return issues


def run_worker_operation(ctx: ToolContext, op: dict, *, mode: str, modules: dict[str, str], module_order: list[str], renders: list[dict],
                         evidence: dict | None, blend_bytes: bytes | None, revision: dict, commit_state: bool = True, samples: int | None = None) -> dict:
    """Bundle, launch, await, ingest one worker operation under the current fence. Returns a dict with status,
    result (untrusted receipt cross-checked), staging path, identity, logs (artifact ids) and error category.

    With ``commit_state=False`` (a tool's own operation) a successful row stays ``running`` with its result recorded
    until the runner commits the tool output in one transaction: a crash between ingestion and that commit then
    reconciles as one interrupted output, never as an operation that claims to be done. ``samples`` (None: the bundle
    default, executor.DEFAULT_SAMPLES) sets the EEVEE samples of the operation's renders."""
    job = ctx.store.job()
    op_root = ctx.job_dir / "worker" / op["id"]
    bundle = op_root / "bundle"
    work = op_root / "work"
    staging = op_root / "staging"
    for d in (bundle, work, staging):
        d.mkdir(parents=True, exist_ok=True)
    limit = int(ctx.policy["max_worker_seconds"])
    operation = write_bundle(bundle, operation_id=op["id"], mode=mode, modules=modules, module_order=module_order, renders=renders, evidence=evidence,
                             blend_bytes=blend_bytes, time_limit_s=limit, **({} if samples is None else {"samples": int(samples)}))
    identity = {"job_short": job["request_sha256"][:12], "operation_id": op["id"], "attempt": op["attempt"], "revision": revision["id"], "fence": ctx.fence}
    ctx.store.require_fence(ctx.holder, ctx.fence)
    running = {"state": "running", "started_utc": ctx.store.now(), "worker_json": {"launching": identity}}
    # a render_only row is its tool (render_views) or a sub-operation of its own: the container deadline is the row's. A build row is the
    # whole edit_program / build_candidate tool, which exports and observes on the host after the container ends; no single deadline
    # covers that (nothing enforces one), so the row carries none rather than a container-only one (INF-18)
    if mode != "build":
        running["deadline_utc"] = iso(ctx.store.clock() + timedelta(seconds=limit + 60))
    ctx.store.update_operation(op["id"], **running)
    try:
        identity = ctx.worker.launch(bundle, operation, work_dir=work, deadline_s=limit, identity=identity)
    except WorkerRefused as e:
        ctx.store.update_operation(op["id"], state="failed", result_json={"error": str(e), "category": "worker_refused"}, completed_utc=ctx.store.now())
        return {"status": "refused", "error": str(e), "category": "worker_refused", "identity": identity, "result": None, "staging": staging, "logs": []}
    ctx.store.update_operation(op["id"], worker_json=identity)
    ctx.store.event("worker_launched", operation=op["id"], identity=identity)

    lost: dict = {}

    def poll() -> bool:
        # the lease is renewed while the worker runs (a build may legally outlast the lease ttl); a lost lease ends the wait
        # and the result is refused below, exactly like a fence that fails after ingestion
        if ctx.heartbeat is not None:
            try:
                ctx.heartbeat()
            except Exception as e:  # noqa: BLE001 - LeaseError: another runner owns the job now
                lost["error"] = str(e)
                return True
        return bool(ctx.store.job()["cancel_requested"])

    def stale(error: str, manifest_sha256=None) -> dict:
        cur = ctx.store.operation(op["id"])
        if cur is not None and cur["state"] == "running":
            # not yet reconciled by the new holder: the row keeps the reason; nothing else is written under a stale lease
            ctx.store.update_operation(op["id"], state="interrupted", result_json={"error": f"stale lease: {error}", "category": "fence", "manifest_sha256": manifest_sha256},
                                       completed_utc=ctx.store.now())
        return {"status": "stale", "error": error, "category": "fence", "identity": identity, "result": None, "staging": staging, "logs": []}

    outcome = ctx.worker.await_outcome(identity, deadline_s=limit, cancel_check=poll)
    ctx.store.event("worker_outcome", operation=op["id"], outcome=outcome.to_dict())
    logs = []
    if lost:
        return stale(lost["error"])
    if outcome.status in ("completed", "failed"):
        try:
            ingested = ctx.worker.ingest(identity, staging)
        except WorkerError as e:
            ctx.store.update_operation(op["id"], state="failed", result_json={"error": f"ingest: {e}", "category": "ingest"}, completed_utc=ctx.store.now())
            return {"status": "failed", "error": f"ingest: {e}", "category": "ingest", "identity": identity, "result": None, "staging": staging, "logs": logs}
        # the receipt is untrusted: its ids and paths are cross-checked against the requested specs and the ingested files
        # before any of them reaches a host path (a violation fails the operation; nothing below registers a render)
        issues = list(ingested.problems or []) + result_is_untrusted_receipt(ingested) + verify_worker_receipt(ingested, requested_renders=renders)
        # fencing: only the current lease may commit a worker result, its logs included
        try:
            ctx.store.require_fence(ctx.holder, ctx.fence)
        except Exception as e:  # noqa: BLE001
            return stale(str(e), ingested.manifest_sha256)
        for name in ("blender.stdout.log", "blender.stderr.log"):
            p = staging / name
            if p.is_file() and p.stat().st_size:
                data = p.read_bytes()[-200_000:]
                a = ctx.artifacts.add_bytes(data, kind="log", role=SYNTHETIC if ctx.worker.synthetic else AUTHOR_VISIBLE, suffix=".log",
                                            label=f"{revision['id']} {op['id']} {name}", revision_id=revision["id"], synthetic=ctx.worker.synthetic)
                logs.append(a["id"])
        result = ingested.result
        ok = outcome.status == "completed" and result is not None and bool(result.get("ok")) and not issues
        # a receipt that fails verification cannot classify its own failure: worker_receipt wins over what the result claims
        category = None if ok else ("worker_receipt" if issues
                                    else "program_error" if result and result.get("module_results") and any(not m.get("ok") for m in result["module_results"])
                                    else "harness_error")
        fields = {"output_manifest_sha256": ingested.manifest_sha256,
                  "result_json": {"ok": ok, "category": category, "issues": issues, "files": ingested.files, "synthetic": ingested.synthetic,
                                  "outcome": outcome.to_dict(), "logs": logs}}
        if commit_state:
            fields.update(state="completed" if ok else "failed", output_committed=1, completed_utc=ctx.store.now())
        ctx.store.update_operation(op["id"], **fields)
        error = None
        if not ok:
            failed = [m for m in (result or {}).get("module_results", []) if not m.get("ok")]
            error = (failed[0].get("error") if failed else (result or {}).get("error")) or outcome.note or "worker reported failure"
            if issues:
                error = f"{error}; ingestion issues: {issues[:5]}"
        return {"status": "completed" if ok else "failed", "error": error, "category": category, "identity": identity, "result": result,
                "staging": staging, "logs": logs, "module_results": (result or {}).get("module_results"), "synthetic": ingested.synthetic, "ingested": ingested,
                "measurement_sha256": measurement_sha256(operation)}
    state = {"timed_out": "timed_out", "cancelled": "cancelled", "interrupted": "interrupted", "lost": "interrupted"}.get(outcome.status, "failed")
    ctx.store.update_operation(op["id"], state=state, result_json={"error": outcome.note or outcome.status, "category": outcome.status, "outcome": outcome.to_dict()},
                               completed_utc=ctx.store.now())
    return {"status": outcome.status, "error": outcome.note or f"worker {outcome.status} after {outcome.seconds:.0f}s", "category": outcome.status,
            "identity": identity, "result": None, "staging": staging, "logs": logs}


def measurement_sha256(operation: dict) -> str | None:
    """The sha256 of the measurement code fingerprint the bundle recorded (executor.write_bundle), or None."""
    fp = operation.get("measurement_fingerprint") if isinstance(operation, dict) else None
    v = fp.get("sha256") if isinstance(fp, dict) else None
    return v if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) else None


def make_render_harness(ctx: ToolContext, op: dict, revision: dict):
    """A drop-in for ``modeler.worker.run_harness`` that renders through the worker (render_only on the .blend)."""
    def render_harness(job: dict, out_dir: Path, *, time_limit_s: int = 300) -> dict:
        blend = Path(job["blend_path"])
        # inserted as running: a pending row sharing the parent's call_id would be executed as a top-level tool after a restart
        sub_op = ctx.store.insert_operation(call_id=op["call_id"], request_id=op["request_id"], tool_name="render_only", schema_version=TOOLS_VERSION,
                                            args={"renders": [r["id"] for r in job["renders"]]}, source_revision_id=revision["id"], fence=ctx.fence, state="running")
        res = run_worker_operation(ctx, sub_op, mode="render_only", modules={}, module_order=[], renders=job["renders"], evidence=None,
                                   blend_bytes=blend.read_bytes(), revision=revision)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        result = dict(res["result"] or {"ok": False, "renders": [], "error": res.get("error")})
        rows = []
        for row in result.get("renders", []) if isinstance(result.get("renders"), list) else []:
            if not isinstance(row, dict):
                continue
            src = render_file_path(res["staging"], row.get("id"))   # defensive: the receipt was verified, an escape here is still never copied
            if src is None:
                ctx.store.event("worker_render_refused", operation=sub_op["id"], render_id=str(row.get("id"))[:80], reason="id escapes the staging renders folder")
                rows.append(dict(row, id=str(row.get("id"))[:80], path=None, error="render id refused"))
            elif res["status"] == "completed" and src.is_file():   # a refused or failed operation copies nothing out of its staging
                dst = out_dir / f"{row['id']}.png"
                shutil.copyfile(src, dst)
                rows.append(dict(row, path=str(dst)))
            else:
                rows.append(dict(row, path=None, error=row.get("error") or res.get("error") or "not ingested"))
        result["renders"] = rows
        result["ok"] = res["status"] == "completed" and all(r.get("error") is None for r in rows)
        result["process"] = {"worker": res["identity"], "status": res["status"], "error": res.get("error")}
        return result
    return render_harness


def build_revision(ctx: ToolContext, rid: str, op: dict, *, deliver_if_compatible: bool) -> ToolResult:
    rev = ctx.store.revision(rid)
    modules = modules_of(ctx, rev)
    ctx.store.update_revision(rid, state="building", operation_id=op["id"])    # the operation owns the revision from here: a restart repairs it through this link
    rdir = ctx.revision_dir(rid)
    canonical = canonical_render_specs()
    # a real worker builds without renders (the observation renders from the saved scene with the fitted cameras);
    # the synthetic worker returns labelled synthetic images for the canonical specs so the loop has images to carry
    res = run_worker_operation(ctx, op, mode="build", modules=modules, module_order=[m for m in MODULE_KEYS if m in modules],
                               renders=canonical if ctx.worker.synthetic else [], evidence=author_visible_evidence(ctx.evidence), blend_bytes=None, revision=rev,
                               commit_state=False)
    # the worker's container name and image digest are host facts (the revision row keeps them); the author needs the kind
    text = {"revision": rid, "operation": op["id"], "worker": {k: v for k, v in res["identity"].items() if k == "kind"},
            "synthetic": bool(ctx.worker.synthetic), "logs": res.get("logs"), "modules": module_shas(rev)}
    if res["status"] == "stale":
        # another runner owns the job: nothing of the revision is rewritten under a stale lease (the runner's commit refuses this output too)
        text.update(built=False, category="fence", error=res.get("error"), state=rev["state"])
        return ToolResult(text)
    ctx.store.update_revision(rid, worker_json=res["identity"])
    if res["status"] != "completed":
        state = "interrupted" if res["status"] in ("interrupted", "lost", "timed_out", "cancelled") else "build_failed"
        ctx.store.update_revision(rid, state=state, compatibility_json={"compatible": False, "reasons": [res.get("category") or res["status"]]})
        failed = [m for m in (res.get("module_results") or []) if not m.get("ok")]
        text.update(built=False, category=res.get("category"), error=res.get("error"),
                    failed_module={k: failed[0].get(k) for k in ("name", "error", "traceback")} if failed else None, state=state)
        if failed:
            text["repair_hints"] = [f"module {failed[0].get('name')} raised: {str(failed[0].get('error') or '')[:300]} (patch that module and build again)"]
        return ToolResult(text)
    build_dir = rdir / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    shutil.move(str(res["staging"]), str(build_dir))
    result = dict(res["result"])
    for key in ("parts_npz", "materials_json", "blend"):
        if result.get(key):
            p = build_dir / Path(str(result[key]).replace("\\", "/")).name
            result[key] = str(p) if p.is_file() else None
    atomic_write(build_dir / "result.host.json", json.dumps(result, indent=1, default=str).encode())
    ctx.store.update_revision(rid, state="built")
    parent_result = load_json_or_none(ctx.revision_dir(rev["parent_id"]) / "build" / "result.host.json") if rev.get("parent_id") else None
    text.update(built=True, inventory=inventory_digest(result.get("inventory"), (parent_result or {}).get("inventory") if parent_result else None, revision=rid),
                notes=collapse_notes(result.get("notes")))
    images: list[dict] = []
    if ctx.worker.synthetic:
        comp = {"synthetic": True, "compatible": True, "contract_ok": None, "ar_runtime_compatible": None, "ar_report_valid": None,
                "reasons": ["SYNTHETIC worker: no export, no contract, no AR check; compatibility is scripted, never real"]}
        for row in result.get("renders", []):
            p = render_file_path(build_dir, row.get("id"))     # defensive: the receipt was verified, an escape here is still never registered
            if p is None:
                ctx.store.event("worker_render_refused", operation=op["id"], revision=rid, render_id=str(row.get("id"))[:80], reason="id escapes the build renders folder")
                continue
            if p.is_file():
                a = register_image(ctx, p, kind="render", label=f"{rid}: SYNTHETIC render {row['id']}", revision_id=rid,
                                   recipe={"view": row["id"], "kind": row.get("kind"), "camera": row.get("camera"), "renderer": "synthetic", "synthetic": True},
                                   required=True, operation_id=op["id"], role=SYNTHETIC)
                images.append(a)
        observation = {"synthetic": True, "summary": {"status": "unmeasured", "note": "synthetic worker: no measurement"}, "bbox_mm": None}
        ctx.store.update_revision(rid, state="compatible", compatibility_json=comp, observation_json=observation, glb_sha256=None)
        text.update(compatibility=comp, measurements=observation["summary"])
        return _finish_build(ctx, rid, text, images, deliver_if_compatible, comp)
    # ---- export (host, pure numpy) and contract
    try:
        if not result.get("parts_npz") or not result.get("materials_json"):
            raise ValueError("the build produced no part arrays")
        export = mexport.export_glb(Path(result["parts_npz"]), Path(result["materials_json"]), rdir / "model.glb",
                                    extras={"modeler": {"candidate": rid, "program_set_sha256": rev["program_set_sha256"], "job": ctx.job_dir.name}})
    except Exception as e:  # noqa: BLE001 - reported to the author
        export = {"error": f"{type(e).__name__}: {e}", "contract": {"ok": False, "failures": ["export_exception"]}}
    atomic_write(rdir / "export.json", json.dumps(export, indent=1, default=str).encode())
    contract = export.get("contract") or {}
    glb_sha = None
    if not export.get("error") and (rdir / "model.glb").is_file():
        glb_bytes = (rdir / "model.glb").read_bytes()
        glb_sha = sha256_bytes(glb_bytes)
        if export.get("sha256") and export["sha256"] != glb_sha:
            export["error"] = "export receipt sha differs from the written bytes"
        ctx.artifacts.add_bytes(glb_bytes, kind="glb", role=HOST_ONLY, suffix=".glb", label=f"{rid}/model.glb", revision_id=rid)
    ctx.store.update_revision(rid, glb_sha256=glb_sha, state="exported" if contract.get("ok") else "export_failed")
    text["export"] = {"error": export.get("error"), "contract_ok": contract.get("ok"), "failures": contract.get("failures"),
                      "failed_checks": {k: {kk: vv for kk, vv in v.items() if kk in ("value", "limit")} for k, v in (contract.get("checks") or {}).items() if not v.get("pass")},
                      "notes": collapse_notes(export.get("notes")), "parts": export.get("parts")}
    audit = export_audit(export)
    if audit is not None:
        text["export"]["audit"] = audit      # whenever the receipt carries one, contract verdict or not
    # ---- observation: fitted cameras and metrics on the host, renders through the worker, the actual AR renderer on the host
    ctx.store.update_revision(rid, state="observing")
    observation, obs_error = None, None
    try:
        from ..observe import observe_candidate
        held = set(ctx.evidence.get("held_out", {}).keys())
        prev = None
        parent = ctx.store.revision(rev["parent_id"]) if rev.get("parent_id") else None
        if parent and (parent.get("observation") or {}).get("views"):
            prev = parent["observation"]["views"]
        # the AR harness runs on every exported GLB, contract verdict or not (best effort: an error lands in observation_error);
        # the author needs the ar sheet to judge materials on a revision that only failed watertightness
        params = inspect.signature(observe_candidate).parameters
        kwargs = dict(held_out_ids=held, previous_cameras=prev, ar=bool(ctx.ar and glb_sha is not None),
                      glb_path=rdir / "model.glb" if glb_sha else None, time_limit_s=int(ctx.policy["max_worker_seconds"]))
        if "heldout" in params:
            kwargs["heldout"] = bool(contract.get("ok"))
        if "render_harness" in params:
            kwargs["render_harness"] = make_render_harness(ctx, op, rev)
        elif ctx.worker.kind == "docker":
            raise RuntimeError("observe_candidate has no render_harness injection; refusing to render through the host Blender under the Docker worker")
        if "heartbeat" in params:
            kwargs["heartbeat"] = ctx.heartbeat
        # the camera fits, the AR harness and the see-through fixtures run on the host: the lease is renewed from a thread meanwhile
        with lease_renewed(ctx):
            observation = observe_candidate(rdir, result, ctx.evidence, ctx.evidence_dir, **kwargs)
    except Exception as e:  # noqa: BLE001 - recorded; the revision stays usable for repair
        obs_error = f"{type(e).__name__}: {e}"
        ctx.store.event("observation_failed", revision=rid, error=obs_error, traceback=traceback.format_exc()[-4000:])
    summary = (observation or {}).get("summary") or {}
    ar_ok = bool(summary.get("ar_runtime_compatible")) and bool(summary.get("ar_report_valid")) and (summary.get("ar_optical_meshes") or 0) >= 1 and not summary.get("ar_continuity_failure")
    comp = {"synthetic": False, "contract_ok": bool(contract.get("ok")), "glb_sha256": glb_sha, "ar_runtime_compatible": summary.get("ar_runtime_compatible"),
            "ar_report_valid": summary.get("ar_report_valid"), "optical_meshes": summary.get("ar_optical_meshes"), "continuity_failure": summary.get("ar_continuity_failure"),
            "observation_error": obs_error, "ar_enabled": ctx.ar}
    comp["compatible"] = bool(contract.get("ok")) and glb_sha is not None and (ar_ok if ctx.ar else True) and obs_error is None
    comp["reasons"] = [] if comp["compatible"] else [r for r, bad in (("contract_failed", not contract.get("ok")), ("no_glb", glb_sha is None),
                                                                     ("ar_not_compatible_or_report_invalid", ctx.ar and not ar_ok), ("observation_failed", obs_error is not None)) if bad]
    if not ctx.ar:
        comp["reasons"].append("AR renderer disabled (--no-ar): a revision cannot be a production-compatible deliverable")
        comp["compatible"] = False
    # ---- catalogue the images: sheets required for delivery and carried first; the match_* singles (the full-resolution
    # photo-camera renders) carried after them on a first build only, then author-visible but not queued (test-pilot-002 re-sent
    # the three with every build, next to the photo_match sheet that shows the same renders; crop_image returns one on request);
    # clay / textured singles host-only (the sheets already show them); held-out renders sealed
    first_look = not ((ctx.store.revision(rev["parent_id"]) or {}).get("observation") if rev.get("parent_id") else None)
    unsent: dict[str, dict] = {}
    if observation:
        for key, p in (observation.get("sheets") or {}).items():
            if p and Path(p).is_file():
                a = register_image(ctx, Path(p), kind="sheet", label=f"{rid}: {sheet_label(key)}", revision_id=rid,
                                   recipe={"view": key, "kind": "sheet", "renderer": SHEET_RENDERERS.get(key, "blender-eevee"), "synthetic": False,
                                           "worker": res["identity"].get("kind")}, required=True, operation_id=op["id"])
                images.append(a)
        for key, p in (observation.get("renders") or {}).items():
            if not p or not Path(p).is_file() or key.startswith("heldout_"):
                continue
            recipe = {"view": key, "kind": "render", "renderer": "blender-eevee", "synthetic": False}
            if key.startswith("match_"):
                label = f"{rid}: render {key} (EEVEE preview from the fitted {key[6:]} photo camera, full resolution)"
                if first_look:
                    images.append(register_image(ctx, Path(p), kind="render", label=label, revision_id=rid, recipe=recipe, required=False, operation_id=op["id"]))
                else:
                    a = ctx.artifacts.add_file(Path(p), kind="render", role=AUTHOR_VISIBLE, label=label, revision_id=rid, recipe=recipe)
                    unsent[key] = {"image_id": a["id"], "size": image_size(a)}
            else:
                ctx.artifacts.add_file(Path(p), kind="render", role=HOST_ONLY, label=f"{rid}: render {key} (host-only single; on the sheets)", revision_id=rid, recipe=recipe)
        held_dir = rdir / "heldout"
        if held_dir.is_dir():
            for p in held_dir.glob("*.png"):
                ctx.artifacts.add_file(p, kind="render", role=SEALED, label=f"{rid}: held-out render {p.stem}", revision_id=rid,
                                       recipe={"view": p.stem, "renderer": "blender-eevee", "sealed": True})
    author_obs = None
    if observation:
        author_obs = {k: observation.get(k) for k in ("views", "summary", "bbox_mm", "triangles", "seconds")}
        author_obs["sheets"] = {k: Path(p).name for k, p in (observation.get("sheets") or {}).items()}
        if res.get("measurement_sha256"):
            # the code that measured this revision (the bundle's executor.measurement_fingerprint): a rebuild compares it
            author_obs["measurement_fingerprint_sha256"] = res["measurement_sha256"]
    ctx.store.update_revision(rid, state="compatible" if comp["compatible"] else "incompatible", compatibility_json=comp, observation_json=author_obs)
    # the GLB hash stays in the stored compatibility (delivery re-verifies it); the author never uses it
    text.update(compatibility={k: v for k, v in comp.items() if k != "glb_sha256"},
                measurements=reply_measurements(summary) if summary else {"status": "unmeasured", "error": obs_error})
    if summary:
        unreliable = unreliable_metrics(observation)
        text["automatic_gate"] = automatic_gate(ctx, summary, unreliable)
        if unreliable:
            text["measurements_unreliable"] = unreliable
    delta = vs_parent(ctx, ctx.store.revision(rid), author_obs)
    if delta is not None:
        text["vs_parent"] = delta
    if unsent:
        text["match_renders"] = {"images": unsent, "note": "the photo-camera renders at full resolution, not attached (the photo_match sheet shows them): "
                                                           "crop_image with an image_id and its full size returns one"}
    if observation:
        unfitted = unfitted_photos(ctx.evidence, observation)
        if unfitted:
            text["photos_without_a_fitted_camera"] = unfitted
    # the plane the harness recorded for this GLB (or the runtime constants): the same plane names the objects never drawn and the repair hints
    text["temple_clip"] = revision_temple_clip(ctx, ctx.store.revision(rid), export=export, inventory=result.get("inventory"))
    hints = repair_hints(export, result.get("inventory"), parts_npz=result.get("parts_npz"), materials_json=result.get("materials_json"),
                         clip_z_mm=text["temple_clip"]["z_mm"], continuity_z_mm=text["temple_clip"]["continuity_z_mm"])
    if hints:
        text["repair_hints"] = hints        # output_for puts it first in the reply
    return _finish_build(ctx, rid, text, images, deliver_if_compatible, comp)


SHEET_LABELS = {"clay": "clay sheet (geometry only)",
                "textured": "textured sheet (EEVEE preview: translucent frames, temples and lens coatings render opaque; judge materials on the ar sheet)",
                "photo_match": "photo_match sheet (photo | EEVEE render from the fitted camera | overlay, red photo edge / green render edge: judge SHAPE here)",
                "ar": f"ar sheet (the exported asset in the ACTUAL AR renderer, {describe_ar_views()}: authoritative for materials, lenses and "
                      "translucency; render_ar_views returns any of these tiles at full resolution without a new run)",
                "see_through": "see_through sheet (the ACTUAL AR renderer over two solid fixtures, skin and dark blue: 'see-through fixture' tiles show "
                               "the translucent front head-on, 'see-through temples' tiles the near translucent arm at 35 deg yaw)",
                "lens_backdrop": "lens_backdrop sheet (the ACTUAL AR renderer's lens over the front photo's own backdrop beside the photo's lens crop: "
                                 "judge the lens COLOUR and density here, against summary.lens_colour)",
                "pose_sweep": "pose_sweep sheet (the ACTUAL AR front over a small yaw / pitch sweep: how the lens and frame reflections move with the "
                              "head; a highlight that jumps between neighbouring poses or clips to white is what summary.lens_reflection flags)",
                "material_match": "material_match sheet (each material in the ACTUAL AR renderer beside the same material in the photo: judge metal, "
                                  "crystal and lens appearance and the hardware's visible area here, against summary.appearance's flags and recommendations)"}
# the sheets the AR harness renders (modeler/observe.py: the ar, lens_backdrop, pose_sweep and material_match sheets; modeler/see_through.py:
# the see_through tiles); every other sheet is EEVEE
SHEET_RENDERERS = {"ar": "actual-ar", "see_through": "actual-ar", "lens_backdrop": "actual-ar", "pose_sweep": "actual-ar", "material_match": "actual-ar"}


def sheet_label(key: str) -> str:
    """Labels that tell the truth about what a sheet can show (the EEVEE preview cannot render translucency)."""
    return SHEET_LABELS.get(key, f"{key} sheet")


def _finish_build(ctx: ToolContext, rid: str, text: dict, images: list[dict], deliver_if_compatible: bool, comp: dict) -> ToolResult:
    cap = int(ctx.policy["images_per_request"])
    carried = images[:cap]
    # the images in this very reply; deferred_images are the rest, waiting for fetch_pending_images (until 2026-09-28 the
    # total was reported as images_queued, which read as if nothing was attached)
    text["images_attached"] = len(carried)
    if len(images) > cap:
        text["deferred_images"] = len(images) - cap
        text["note"] = "more images than one message carries; the rest are queued: call fetch_pending_images before deciding"
    text["deliver_if_compatible"] = bool(deliver_if_compatible)
    lock_problem = owner_lock_refusal(ctx.store, ctx.store.revision(rid), delivery=True) if deliver_if_compatible and comp.get("compatible") else None
    if lock_problem:
        text["delivery_note"] = f"deliver_if_compatible refused: {lock_problem}"
        deliver_if_compatible = False
    elif deliver_if_compatible:
        if comp.get("compatible"):
            text["delivery_note"] = ("deliver_if_compatible is recorded; delivery happens once you have received every required image of this revision "
                                     "(fetch_pending_images if any remain) and call request_delivery, or automatically after this reply when nothing is pending")
        else:
            text["delivery_note"] = "deliver_if_compatible ignored: the revision is not compatible"
    ctx.store.event("revision_built", revision=rid, compatible=bool(comp.get("compatible")), images=len(images), deliver_if_compatible=bool(deliver_if_compatible))
    return ToolResult(text, images=carried, new_revision=rid,
                      delivery={"revision": rid, "status_claim": "best_effort", "note": "deliver_if_compatible on build", "auto": True, "glb_sha256": comp.get("glb_sha256")}
                      if deliver_if_compatible and comp.get("compatible") else None)


def dump_result_text(text: dict) -> str:
    """The JSON of a tool result: keys sorted, except that ``repair_hints`` (when present) leads the object so a failed
    build's reply starts with what to fix rather than with the inventory."""
    if "repair_hints" not in text:
        return json.dumps(text, sort_keys=True, default=str)
    rest = {k: v for k, v in text.items() if k != "repair_hints"}
    body = json.dumps(rest, sort_keys=True, default=str)
    head = '{"repair_hints": ' + json.dumps(text["repair_hints"], sort_keys=True, default=str)
    return head + (", " + body[1:] if len(body) > 2 else "}")


def output_for(ctx: ToolContext, call_id: str, result: ToolResult, *, state_note: dict | None = None) -> tuple[dict, list[str]]:
    """The function_call_output item for a tool result: JSON text plus the image blocks; returns (item, image ids)."""
    text = dict(result.text)
    ids = [a["id"] for a in result.images]
    if state_note:
        text["host_state"] = reply_host_state(ctx, state_note, ids)
        if isinstance(text.get("state"), dict):
            text.pop("state")          # an error result's own state summary: the host_state carries it once
    blocks = [text_block(dump_result_text(text))]
    if result.images:
        blocks += image_blocks_for(ctx, result.images)
        blocks.append(text_block(json.dumps({"images_in_this_message": ids})))
    return function_output_item(call_id, blocks), ids
