"""The facet lab (plan/WP6_FACETS.md §4): an isolated copy of a product scene, immutable g3d.temple/2 revisions and the
seven facet tools' host side. Astra never writes code: every edit is a §4.2 edit list on a revision, built by the numpy
core (g3d.temple) and applied in Blender through the lab-only worker methods (temple_lab_build, mutating;
temple_variants_render, read-only); every look is the photo-matched compare (agent/seeing.py, the job's rig, passes
native_linear + gloss_black).

Job layout (``root``):
    inputs/lab.json      {schema g3d.temple_lab/2, rig (a seeing.MATCHED_RIGS name), assets [{path, sha256}],
                         photos [...]} (written by cli.prepare); inputs/scene.blend = the original's byte copy
    cameras/basis-K      the frozen pose basis (compare.import_cameras_json; target photos reference-01..06)
    checkpoints/, lab_calls/, work/temple_lab/{state.json, revisions/rNNNN.json, images}, work/compare, ...

``Lab(root).prepare()`` (once, $0): start the worker on the original (checkpoint c0000), compare the ORIGINAL at every
target photo (this fixes the job's start model, gains and, for photo_env, its environment), take revision 0 =
template_from_mesh of the original's +X temple body (with the decal and hinge footprints and the body's chart mesh for
the section strips), apply it, and compare revision 0 at every target photo. ``Lab(root).start()`` reopens the head
for a run.

Photo ids (lab.json ``photos``, each with a role; rows without one are target photos):
    target      "01".."06" = the basis' reference-01..06 (target colourway): photo_image, photo_compare (the default
                set), facet_variants (native_linear panels), and finish precondition 3 (``photo_ids``);
    colourway   a basis image of another colourway with a calibrated camera (``basis_ref``; the Prada job's B1, B2, B3
                = black-side, black-angled, black-front): photo_image, photo_compare (photo / overlay / gloss_black, no
                native row: seeing's variant-colourway compare) and facet_variants (gloss_black panels with that
                compare's gloss gain and stretch); never the default set;
    reference   an image without a camera under inputs/ (``path``, ``sha256``): photo_image only.
``compare_ids`` = target + colourway; ``image_ids`` = all of them.

facet_variants photos: 1..VARIANT_PHOTOS_MAX (4) photos with a camera, at most VARIANT_TARGETS_MAX (3) of them target
photos, so a 4th is always a photo of another colour. Why 4: the black photos are where the facets read (the gloss
shows each crease as a highlight line), and run 4 used the top and a side photo in 6 of 6 variant calls plus 02 or 04
in 3 of 6; one black photo beside those three costs (variants + 1) more renders (~0.8 s each, measured) and one more
sheet image, within the call's hard timeout (loop.hard_timeout_s caps the worker's render deadline at the 90 s table
value minus loop.FACET_VARIANTS_HOST_MARGIN_S, 75 s: about 2.9 x the estimated 4 x 7 renders, and the host's builds,
crease lines and sheets keep 15 s inside the hard timeout; the 2026-10-11 tool test took 9.6 s for 3 photos x 4
renders). Variant labels are their render files' names: each must be its own g3d/render.py safe_name
(``render_safe_name``) and differ from the others and from "head" ignoring case. Every render panel shows the photo's
outline (magenta) and that model's own crease lines in the compare overlay's colours, from compare.CREASE_MIN_DEG (the
sheet title says so; ``_variant_creases``).

Compare text (``_photo_text``): outer-edge offsets are printed per temple as "charted" (+X, the side the spec is
charted on) and "mirrored" (-X, its mirror), from the records' pose.SIDE_KEYS "+1" / "-1" (SIDE_TEXT).

Tool methods (each returns a dict for tools.py; "images" lists image files, never sent as paths):
    inspect()                                       head summary, chart, section strip (head / original / parent)
    photo(photo, crop)                              a target photo or a native-pixel crop, with its transform
    compare(call_id, photos, crop, points)          the photo-matched compare of the head -> compare_id
    edit(call_id, parent_revision, reset_layout, edits, judgement, note, apply, photos)
    variants(call_id, parent_revision, variants, photos, judgement)
    restore_tool(call_id, checkpoint, judgement)
    finish(call_id, status, best_checkpoint, summary, judgement, k)
SceneHooks for agent/loop.py: start, close, journal_entries, restore, exit_checkpoint, worker_info, context_stubs.

The scene lock (``Lab.lock``) serialises every use of the worker: tools.py takes it with timeout 0 for each tool call
(E_BUSY when held), and exit_checkpoint / close wait for it (a call the SDK timed out may still be running). A call the
SDK timed out is ``mark_unseen``: a compare it still makes is recorded unseen and never demands a judgement. Compares of
a tool call render under that tool's hard timeout (loop.hard_timeout_s), never seeing's longer default.

The state lock (``Lab.state_lock``, re-entrant) guards every mutation of ``Lab.state`` and every save: mark_unseen
runs on the event-loop thread while a timed-out call's worker thread may still mutate or save the state, and json
never serialises a dict that grows under it. It is held only for the mutation or the write, never across a build or
render.

``Lab.distinct(ra, rb)`` is the one head-distinctness rule (geometry.distinct_heads of the two BUILT temples, cached
per pair) for finish preconditions 1 and 4.

Warnings revision 0 already has (its core warnings and the Blender layer's lines at prepare, e.g. W_HINGE_LEAF,
W_ARM_NORMAL_SPREAD; state "inherited") are listed by facet_inspect as inherited from revision 0 and left out of
later receipts while their numbers stay within MATERIAL_ABS / MATERIAL_REL of revision 0's.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import time

import numpy as np
from PIL import Image

from glasses3d.agent.journal import BlenderSession, WorkerFailed
from glasses3d.agent.loop import SceneHooks, hard_timeout_s
from glasses3d.agent.procs import write_json_atomic
from glasses3d.agent.seeing import GRID_STATION_MM, MATCHED_RIGS, Seeing
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import draw as DR
from glasses3d.g3d.temple import report as RP
from glasses3d.g3d.temple.spec import BORDER_RAMP_WARN_DEG, TempleError, canonical, plain, spec_sha256
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host import spec as GSPEC
from . import geometry as G
from . import sheets as SH
from . import state as S
from .state import LabRefusal

LAB_SCHEMA = "g3d.temple_lab/2"
MATCHED_PASSES = ("native_linear", "gloss_black")
VARIANTS_MIN, VARIANTS_MAX = 2, 6
VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX = 4, 3     # facet_variants photos (module doc)
CREASE_THREADS = max(1, min(8, (os.cpu_count() or 2) // 2))   # host crease lines: one task per photo and model
NOTE_MAX, SUMMARY_MAX = 1000, 2000
CHART_W = 1024
BUILD_DEADLINE_S = hard_timeout_s("facet_edit")    # a temple_lab_build still running then is killed (worker restart)
STRIP_STATIONS = (5, 15, 25, 35, 50, 65, 80)
STRIP_PANEL_W, STRIP_PANEL_H = 146, 420
LINE_MAX = 170                   # a receipt line (the core's W_* messages fit it with 24-character names)
REFUSAL_MAX = 400                # a refusal message in facet_variants (the core's longest are about 250)
BUILD_CACHE = 24
CONTEXT_KEEP_LATEST = 2          # the context guard keeps the images of the latest 2 compare / variants results
LOCK_WAIT_S = 600.0              # exit_checkpoint / close wait this long for a tool call still holding the scene
MATERIAL_ABS, MATERIAL_REL = 0.1, 0.1     # an inherited warning shows again when a number moves more than this
RAMP_ITEMS_MAX = 8               # border-ramp runs listed per receipt (then "+N more")
NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?![\w.])")
RENDER_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")   # = g3d/render.py NAME_RE (that module imports bpy; a test pins it)
SIDE_TEXT = {PO.SIDE_KEYS[1]: "charted", PO.SIDE_KEYS[-1]: "mirrored"}     # compare text per temple (module doc)


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


def brief(text: str, limit: int = REFUSAL_MAX) -> str:
    """text whole when it fits in limit characters, else cut at its last clause end ("; " or ". ") before the limit
    (at the last space when it has none) and marked " ...": never mid-word, never mid-clause when it has clauses."""
    if len(text) <= limit:
        return text
    cut = max(text.rfind("; ", 0, limit - 3), text.rfind(". ", 0, limit - 3))
    if cut <= 0:
        cut = text.rfind(" ", 0, limit - 3)
    return (text[:cut] if cut > 0 else text[:limit - 4]) + " ..."


def ramp_line(warnings: list) -> str:
    """Every W_BORDER_RAMP of a build in one line: facet, signed height in mm (+ proud of the base, - below it),
    slope over the border band, d (a point or a range)."""
    items = []
    for w in warnings[:RAMP_ITEMS_MAX]:
        a, b = (f"{x:.1f}" for x in w["d_mm"])
        items.append(f"{w['facet']} {w['height_mm']:+.2f} {w['slope_deg']:.1f}° d {a if a == b else f'{a}-{b}'}")
    more = f"; +{len(warnings) - RAMP_ITEMS_MAX} more" if len(warnings) > RAMP_ITEMS_MAX else ""
    return f"W_BORDER_RAMP >{BORDER_RAMP_WARN_DEG:g}° (mm, + proud/- below base): " + "; ".join(items) + more


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
        """The photos photo_compare takes: the target photos, then the calibrated colourway photos."""
        return self.photo_ids() + list(self.colourway_refs())

    def image_ids(self) -> list[str]:
        """The photos photo_image takes: the compare photos, then the reference-only photos (no camera)."""
        return self.compare_ids() + [r["id"] for r in self._rows("reference")]

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
        photos of another colour, one of those (module doc)."""
        return min(VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX + len(self.colourway_refs()))

    def _variant_photos(self, photos) -> list[str]:
        """facet_variants' photos: 1..variant_photos_max() distinct photos with a camera, at most VARIANT_TARGETS_MAX
        of them target photos."""
        high = self.variant_photos_max()
        if photos is None:
            raise LabRefusal("E_ARGS", f"photos must name 1..{high} photos")
        photos = self._photos_arg(photos, low=1, high=high)
        targets = [p for p in photos if p in self.photo_ids()]
        if len(targets) > VARIANT_TARGETS_MAX:
            raise LabRefusal("E_ARGS", f"photos: at most {VARIANT_TARGETS_MAX} target photos (got {targets}); a 4th "
                                       f"must be a photo of another colour: {list(self.colourway_refs())}")
        for p in photos:                       # the sheets take the crop, stretch and gains of its latest compare
            if not any(p in c["photos"] for c in self.state["compares"]):
                raise LabRefusal("E_STATE", f"photo {p} was never compared; photo_compare it first")
        return photos

    def fingerprint_of(self, checkpoint: str | None) -> str | None:
        row = self.s.index.get(checkpoint) if isinstance(checkpoint, str) else None
        return row["fingerprint"] if row else None

    def spec(self, rid: int) -> dict:
        return read(self.work / S.revision(self.state, rid)["file"])

    def build(self, rid: int):
        if rid in self._builds:
            self._builds.move_to_end(rid)
            return self._builds[rid]
        tb = build_temple(self.spec(rid))
        self._builds[rid] = tb
        while len(self._builds) > BUILD_CACHE:
            self._builds.popitem(last=False)
        return tb

    def delta(self, ra: int, rb: int) -> dict:
        """geometry_delta between two revisions (symmetric), cached: a revision's spec never changes."""
        key = (min(ra, rb), max(ra, rb))
        if key not in self._deltas:
            self._deltas[key] = G.geometry_delta(self.build(key[0]), self.build(key[1]))
        return self._deltas[key]

    def distinct(self, ra: int, rb: int) -> bool:
        """The one head-distinctness rule (finish preconditions 1 and 4): geometry.distinct_heads of the two revisions'
        built temples. A revision is never distinct from itself."""
        return ra != rb and G.distinct_heads(self.delta(ra, rb))

    def head_record(self) -> dict | None:
        return S.head_by_fingerprint(self.state, self.s.head["fingerprint"]) if self.s.head else None

    def _register(self, spec: dict, *, parent, kind: str, label=None, ops: int = 0, note: str = "",
                  call_id=None) -> tuple[int, bool]:
        digest = spec_sha256(spec)
        with self.state_lock:
            for r in self.state["revisions"]:
                if r["sha256"] == digest:
                    return r["id"], False
            rid = len(self.state["revisions"])
            rel = f"revisions/r{rid:04d}.json"
            write_json_atomic(self.work / rel, canonical(spec))
            self.state["revisions"].append({"id": rid, "parent": parent, "sha256": digest, "file": rel, "kind": kind,
                                            "label": label, "ops": ops, "note": note, "call_id": call_id})
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
        or still makes is recorded unseen and never demands a judgement (§4.1); its facet_variants sheets are unseen
        and never meet finish precondition 2; a finish it accepted is moved to ``finish_unseen`` (Astra was told
        E_TIMEOUT and the run goes on). Under the state lock: the call's thread may still be mutating or saving."""
        with self.state_lock:
            self._unseen.add(call_id)
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
        latest CONTEXT_KEEP_LATEST, except those of best_checkpoint's revision, become text stubs."""
        with self.state_lock:
            results = [("compare", c["compare_id"], c["seq"]) for c in S.seen_compares(self.state)] + \
                      [("variants", v["id"], v["seq"]) for v in S.seen_variants(self.state)]
            best = S.head_by_fingerprint(self.state, self.fingerprint_of(self.state.get("best_checkpoint")))
            best_compares = {c["compare_id"] for c in self.state["compares"]
                             if best is not None and c["revision"] == best["revision"]}
            images = {digest: dict(self.state["images"][digest]) for digest in image_sha256s
                      if digest in self.state["images"]}
        keep = {ref for _k, ref, _s in sorted(results, key=lambda r: r[2])[-CONTEXT_KEEP_LATEST:]} | best_compares
        out = {}
        for digest in image_sha256s:
            rec = images.get(digest)
            if rec and rec["kind"] in ("compare", "variants") and rec["ref"] not in keep:
                what = f"photo {rec.get('photo')}" if rec.get("photo") else "sheet"
                out[digest] = (f"[image removed by the host to save context: {rec['kind']} {rec['ref']} {what}, "
                               f"revision {rec.get('revision')}; superseded]")
        return out

    def is_clean(self) -> bool:
        """True while no facet tool has run since prepare (a paid run starts only from a clean package)."""
        st = self.state
        return (st.get("start_checkpoint") is not None and len(st["revisions"]) == 1 and not st["judgements"]
                and not st["variants"] and not S.astra_compares(st)
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
        L, Fc = r0["chart_mesh"]
        mesh_rel = "original_chart.npz"
        with open(self.work / mesh_rel, "wb") as f:
            np.savez_compressed(f, V=L, F=Fc)
        with self.state_lock:
            self.state["original"] = {"export": {k: exp[k] for k in ("path", "sha256", "checkpoint")},
                                      "body": r0["body"], "side": r0["side"], "hinge_z_mm": r0["hinge_z"],
                                      "axis_xz_mm": r0["axis"], "chart_mesh": mesh_rel,
                                      "footprints": r0["footprints"], "template_report": plain(r0["report"])}
        cmp0 = self._compare("prepare-original", self.photo_ids(), by="prepare", hinge_z=r0["spec"]["hinge"]["z_mm"])
        if cmp0.get("status") != "ok":
            raise ValueError(f"the compare of the original failed: {cmp0}")
        build_temple(r0["spec"])
        rid, _ = self._register(r0["spec"], parent=None, kind="template", label="revision 0", note="template_from_mesh")
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
        """Build revision rid on both temples from the ORIGINAL (repeated decal transfers never accumulate) and
        checkpoint it; any failure restores the scene the call started on."""
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
                                            "revision": rid, "call_id": call_id})
                if inherited is not None:
                    self.state["inherited"] = inherited
            # prepare's apply is the package baseline (kind "prepare"): a resumed run never rolls it back
            self.state["events"].append({"kind": "prepare" if tool == "prepare" else "call", "call_id": call_id,
                                         "tool": tool, "mutating": True, "checkpoint_before": before,
                                         "checkpoint_after": self.s.head["id"]})
        self.save()
        return out

    def _compare(self, call_id: str, photos, *, by: str, crop: str = "temple", points=None,
                 hinge_z: float | None = None, with_start: bool = True, tool: str | None = None) -> dict:
        """The photo-matched compare of the head; a tool's compare renders under that tool's hard timeout."""
        head = self.head_record()
        if hinge_z is None:
            hinge_z = self.spec(head["revision"])["hinge"]["z_mm"] if head else None
        points_px = None
        if points:
            points_px = {}
            for p in points:
                points_px.setdefault(self.rid(p["photo"]), []).extend(p["points"])
        cid = self._next("cmp")
        refs = [self.rid(p) for p in photos] if photos else "default"
        res = self.seeing.compare_to_photos(refs=refs, passes=MATCHED_PASSES, rig=self.rig, crop=crop,
                                            points_px=points_px, hinge_z_mm=hinge_z, call_id=cid,
                                            render_deadline_s=hard_timeout_s(tool) if tool else None)
        if res.get("status") != "ok":
            self.save()
            return {"status": "error", "code": res.get("code", "E_COMPARE"), "message": res.get("message")}
        report = read(res["report"])
        rids = list(res["photos"])
        outline = {}                     # the outline line (information): every target top photo's outline_check
        for r in rids:
            prec = report["photos"].get(r, {})
            if prec.get("view_kind") == S.OUTLINE_VIEW and self.pid(r) in self.photo_ids():
                outline[self.pid(r)] = S.outline_check(prec.get("outer_edge_offsets_mm"))
        rec = {"compare_id": cid, "checkpoint": res.get("checkpoint", self.s.head["id"]),
               "fingerprint": self.s.head["fingerprint"], "revision": head["revision"] if head else None,
               "photos": [self.pid(r) for r in rids], "by": by, "call_id": call_id, "report": res["report"],
               "judged": by != "astra", "seq": self.state["seq"], **({"outline": outline} if outline else {})}
        with self.state_lock:
            if by == "astra" and call_id in self._unseen:          # the SDK stopped waiting: Astra never sees it
                rec.update(unseen=True, judged=True)
            self.state["compares"].append(rec)
        spec = self.spec(head["revision"]) if head else None
        texts, images = {}, []
        for r in rids:
            texts[self.pid(r)] = self._photo_text(report["photos"].get(r, {}), res["photos"][r].get("points"), spec,
                                                  with_start, colour=self._colour(self.pid(r)),
                                                  outline=outline.get(self.pid(r)))
            images.append(self._image(res["photos"][r]["image"], "compare", ref=cid, photo=self.pid(r),
                                      revision=rec["revision"]))
        self.save()
        return {"status": "ok", "compare_id": cid, "photos": texts, "images": images}

    def _photo_text(self, rec: dict, points, spec, with_start: bool = True, colour: str | None = None,
                    outline: dict | None = None) -> str:
        """One photo of a compare, compact: view (a colourway photo: its colour and rows), temple IoU, scale, crop
        (box and scale k: u_img = (u - x0 + 0.5) k - 0.5), outer-edge offsets in mm to 0.1 (photo minus model, top
        and rear photos, over the stations where the photo has one; per temple, "charted" / "mirrored" (SIDE_TEXT);
        the station step only when it is not seeing's GRID_STATION_MM; with_start: the start model's too; the tool
        descriptions and the instructions define the sign, so the text does not repeat it), a top photo's
        outline_check (state.outline_text, information), points_px charted."""
        crop = rec.get("crop") or {}
        k = float(crop.get("k") or 1.0)
        ppm = rec.get("px_per_mm")
        text = f"{rec.get('view_kind')}" + (f" {colour}: rows photo/overlay/gloss-black" if colour else "") + \
            f" IoU {float(rec.get('temple_iou', float('nan'))):.3f}"
        if ppm is not None and np.isfinite(ppm):
            text += f", {ppm * k:.2f} px/mm"                        # in the image shown; native = this / k
        if crop.get("box"):
            text += f", box [{','.join(str(int(v)) for v in crop['box'])}] k {k:.3f}"
        offs, start = rec.get("outer_edge_offsets_mm"), rec.get("start_offsets_mm")
        if offs:
            def f(v):
                return "na" if v is None else f"{v:+.1f}"
            vals = {side: {o["d_mm"]: o["offset_mm"] for o in rows} for side, rows in offs.items()}
            every = sorted({d for v in vals.values() for d in v})
            hit = [i for i, d in enumerate(every) if any(v.get(d) is not None for v in vals.values())]
            ds = every[hit[0]:hit[-1] + 1] if hit else []
            if ds:
                step = ds[1] - ds[0] if len(ds) > 1 else GRID_STATION_MM
                text += f" | offsets mm d {ds[0]:g}..{ds[-1]:g}" + \
                    ("" if abs(step - GRID_STATION_MM) < 1e-9 else f"/{step:g}") + ":"
            use = with_start and isinstance(start, dict) and "missing" not in start
            for side in offs if ds else ():
                text += f" {SIDE_TEXT.get(side, side)} [{' '.join(f(vals[side].get(d)) for d in ds)}]"
                srows = {r["d_mm"]: r["offset_mm"] for r in (start.get(side) or [])} if use else {}
                if srows:
                    text += f" start [{' '.join(f(srows.get(d)) for d in ds)}]"
        if outline:
            text += " | " + S.outline_text(outline)
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

    def _is_inherited(self, line: str) -> bool:
        inh = self._inherited()
        return any(same_warning(line, ref) for ref in inh["core"] + inh["blender"])

    def inherited_lines(self) -> list[str]:
        """facet_inspect's "inherited from revision 0" list: each inherited warning line once (per code "(xN)")."""
        inh = self._inherited()
        by_code: dict = {}
        for ln in inh["core"] + inh["blender"]:
            by_code.setdefault(ln.split(":", 1)[0], []).append(ln)
        out = [rows[0][:LINE_MAX] + (f" (x{len(rows)})" if len(rows) > 1 else "") for rows in by_code.values()]
        if inh.get("hinge_leaf_gap_mm") is not None:
            out.append(f"hinge_leaf_gap_mm {inh['hinge_leaf_gap_mm']:+.2f} (negative = embedded)")
        return out

    def _receipt(self, tb) -> dict:
        """The core receipt for Astra (§4.5): crease lines first ("pair shape dihedral declared|undeclared": a
        declared shape always matches, a mismatch is refused E_CREASE_SHAPE), then the warnings (every W_BORDER_RAMP
        in one line; warnings inherited from revision 0 left out while unchanged); border ramps that raised no
        W_BORDER_RAMP, as "facet height slope d"."""
        rc = RP.receipt(tb)
        th = rc["thickness"]
        lines = [f"{'|'.join(c['regions'])} {c['shape']} {c['mean_dihedral_deg']:.1f}° "
                 f"{'declared' if c.get('declared') else 'undeclared'}"[:LINE_MAX]
                 for c in tb.tables["creases"] if c["kind"] == "crease"]
        ramps = []
        for w in tb.warnings:
            line = f"{w['code']}: {w['message']}"
            if self._is_inherited(line):
                continue
            if w["code"] == "W_BORDER_RAMP":
                ramps.append(w)
            else:
                lines.append(line[:LINE_MAX])
        if ramps:
            lines.append(ramp_line(ramps))
        out = {"lines": lines,
               "lean_deg": {f["label"]: f["lean_deg"] for f in rc["facets"]},
               "thickness": f"{th['min_mm']:.2f} at d {th['min_at'][0]:.1f} y {th['min_at'][1]:.1f} .. "
                            f"{th['max_mm']:.2f} at d {th['max_at'][0]:.1f} y {th['max_at'][1]:.1f}"}
        warned = {(w.get("facet"), tuple(_r(w.get("d_mm") or [], 1))) for w in tb.warnings
                  if w["code"] == "W_BORDER_RAMP"}
        quiet = [f"{r['facet']} {r['height_mm']:+.2f} {r['slope_deg']:.1f}° d {r['d_mm'][0]:.1f}-{r['d_mm'][1]:.1f}"
                 for r in rc["border_ramps"] if abs(r["height_mm"]) >= 0.01
                 and (r["facet"], tuple(_r(r["d_mm"], 1))) not in warned]
        if quiet:
            out["border_ramps"] = "; ".join(quiet[:RAMP_ITEMS_MAX]) + \
                (f"; +{len(quiet) - RAMP_ITEMS_MAX} more" if len(quiet) > RAMP_ITEMS_MAX else "")
        if rc["snaps"]:
            out["snaps"] = len(rc["snaps"])
        if "roll" in rc:
            out["roll"] = rc["roll"]
        return out

    @staticmethod
    def _crease_brief(tb) -> str:
        """Built creases in one short string: "F1|F2 r12.6 F4|base v5.1" (r ridge, v valley, f flat; mean dihedral
        deg). A declared shape always matches here: a mismatch is refused (E_CREASE_SHAPE) before."""
        return " ".join(f"{'|'.join(c['regions'])} {c['shape'][0]}{c['mean_dihedral_deg']:.1f}"
                        for c in tb.tables["creases"] if c["kind"] == "crease")

    def _blender_receipt(self, result: dict, core_lines: list) -> dict:
        """The Blender layer's own news, compact: decal motion, the hinge leaf gap (negative = embedded) when it
        moved from revision 0's, and its warning lines not in the core receipt and not inherited unchanged from
        revision 0, one per code (the first message, "(xN)" when the sides repeat it)."""
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
        by_code: dict = {}
        for ln in result.get("lines", []):
            if ln not in core_lines and not self._is_inherited(ln):
                by_code.setdefault(ln.split(":", 1)[0], []).append(ln)
        if by_code:
            out["lines"] = [rows[0][:LINE_MAX] + (f" (x{len(rows)})" if len(rows) > 1 else "")
                            for rows in list(by_code.values())[:6]]
        return out

    def _chart_images(self, rid: int, tb, tag: str) -> list[str]:
        """The chart and the section strip (this revision black, the original grey, its parent teal dashed)."""
        rec = S.revision(self.state, rid)
        orig = self.state.get("original") or {}
        fps = orig.get("footprints")
        chart = DR.chart_image(self.spec(rid), tb, footprints=fps, width=CHART_W,
                               title=f"revision {rid} ({rec['sha256'][:8]}): d mm back from the hinge, y mm up | "
                                     f"t {tb.thickness.min():.2f}-{tb.thickness.max():.2f} mm")
        items = [(f"r{rid}", tb)]
        if orig.get("chart_mesh"):
            with np.load(self.work / orig["chart_mesh"]) as z:
                items.append(("original", (z["V"], z["F"])))
        if rec["parent"] is not None and rec["parent"] != rid:
            items.append((f"r{rec['parent']} (parent)", self.build(rec["parent"])))
        strip = DR.section_strip(items, stations=STRIP_STATIONS, panel_w=STRIP_PANEL_W, panel_h=STRIP_PANEL_H,
                                 title=f"sections of r{rid} (black) over the original (grey)"
                                       + (f" and r{rec['parent']} (teal)" if len(items) > 2 else ""))
        out = []
        for name, im in (("chart", chart), ("sections", strip)):
            p = self.work / "images" / f"{tag}-{name}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            im.save(p)
            out.append(self._image(p, name, ref=tag, revision=rid))
        return out

    # ---------------------------------------------------------------------------------------------- tools
    def inspect(self) -> dict:
        head = self.head_record()
        if head is None:
            raise LabRefusal("E_STATE", "the scene holds no applied revision (prepare the lab first)")
        rid = head["revision"]
        spec, tb = self.spec(rid), self.build(rid)
        rc = RP.receipt(tb, include_stations=True)
        nodes = {n: [_r(d), _r(y), t if not isinstance(t, float) else _r(t)] for n, (d, y, t) in spec["nodes"].items()}
        facets = {}
        for label, f in spec["facets"].items():
            pl = f["plane"] if f["plane"] == "nodes" else {"crease": f["plane"]["crease"],
                                                            "rise_per_mm": _r(f["plane"]["rise_per_mm"], 3)}
            facets[label] = {"poly": f["poly"], "plane": pl, **({"fade": [_r(f["fade"]["d0"]), _r(f["fade"]["d1"])]}
                                                                  if f["fade"] else {})}
        revs = self.state["revisions"]
        heads = self.state["heads"]
        rows = [[r["id"], r["parent"], r["kind"], r["sha256"][:8]] + ([r["label"]] if r.get("label") and
                                                                       r["kind"] == "variant" else [])
                for r in revs[-12:]]
        mine = S.seen_compares(self.state)
        tag = self._next("inspect")
        out = {"status": "ok", "head": {"checkpoint": self.s.head["id"], "revision": rid,
                                        "spec": revs[rid]["sha256"][:8]},
               "spec": {"hinge": {"z_mm": _r(spec["hinge"]["z_mm"]), "axis_xz_mm": _r(spec["hinge"]["axis_xz_mm"])},
                        "tip_round_mm": _r(spec["outline"]["tip_round_mm"]), "roll_mm": _r(spec["roll_mm"]),
                        "stations": rc["stations"], "nodes": nodes, "facets": facets,
                        "creases": [f"{'|'.join(c['facets'])} {c['shape']}" for c in spec["creases"]]},
               "receipt": self._receipt(tb),
               "inherited_from_revision_0": self.inherited_lines(),
               "revisions": {"count": len(revs), "last": rows, "columns": ["id", "parent", "kind", "spec"]},
               "applied": [[h["checkpoint"], h["revision"]] for h in heads[-12:]],
               "photos": self._photo_list(),
               "latest_compare": mine[-1]["compare_id"] if mine else None,
               "judgement_due": (S.pending_compare(self.state) or {}).get("compare_id"),
               "best_checkpoint": self.state.get("best_checkpoint"),
               "images": self._chart_images(rid, tb, tag)}
        self.save()
        return out

    def _photo_list(self) -> dict:
        """facet_inspect's photo list: id -> view (a photo of another colourway: its colour and what takes it)."""
        out = {pid: self.basis.refs[self.rid(pid)].get("view_kind") for pid in self.photo_ids()}
        for pid, ref in self.colourway_refs().items():
            out[pid] = f"{self.basis.refs[ref].get('view_kind')}, {self._colour(pid)}, photo_compare"
        for row in self._rows("reference"):
            out[row["id"]] = f"{row.get('view')}, {self._colour(row['id'])}, photo_image only"
        return out

    def photo(self, photo: str, crop=None) -> dict:
        """A photo (any of image_ids: a reference-only photo is read from inputs/, sha-checked) or a native crop."""
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
        im, transform = SH.photo_view(rgb, crop)
        tag = hashlib.sha256(json.dumps([photo, crop]).encode()).hexdigest()[:12]
        p = self.work / "images" / f"photo-{photo}-{tag}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p)
        colour = self._colour(photo)
        out = {"status": "ok", "photo": photo, "view": view, **({"colour": colour} if colour else {}),
               "transform": transform, "images": [self._image(p, "photo", ref=f"photo-{photo}")]}
        self.save()
        return out

    def compare(self, call_id: str, photos=None, crop=None, points=None) -> dict:
        photos = self._photos_arg(photos)
        if crop not in (None, "temple", "full"):
            raise LabRefusal("E_ARGS", "crop must be temple, full or null")
        if self.head_record() is None:
            raise LabRefusal("E_STATE", "the scene holds no applied revision")
        for p in points or []:
            self.rid(p["photo"])
            if photos is not None and p["photo"] not in photos:
                raise LabRefusal("E_ARGS", f"points name photo {p['photo']}, which this compare does not show")
        return self._compare(call_id, photos, by="astra", crop=crop or "temple", points=points, tool="photo_compare")

    def _judge(self, judgement, call_id: str, tool: str) -> None:
        with self.state_lock:
            rec = S.check_judgement(self.state, judgement, fingerprint_of=self.fingerprint_of)
            S.record_judgement(self.state, rec, call_id=call_id, tool=tool)

    def edit(self, call_id: str, parent_revision: int, reset_layout: bool, edits: list, judgement, note: str,
             apply: bool = True, photos=None) -> dict:
        self._judge(judgement, call_id, "facet_edit")
        self.save()
        parent = S.revision(self.state, parent_revision)
        if not isinstance(note, str) or len(note) > NOTE_MAX:
            raise LabRefusal("E_ARGS", f"note must be text of at most {NOTE_MAX} characters")
        photos = self._photos_arg(photos)
        try:
            spec, notes = G.apply_edits(self.spec(parent["id"]), edits, reset_layout=bool(reset_layout))
        except G.EditError as e:
            return {**e.to_dict(), "parent": parent["id"]}
        except TempleError as e:
            return {"status": "refused", "code": e.code, "edit": None, "stage": "spec", "message": e.message}
        try:
            tb = build_temple(spec)
        except TempleError as e:
            return {"status": "refused", "code": e.code, "edit": None, "stage": "build", "message": e.message,
                    "fields": {k: v for k, v in e.fields.items() if k in ("facet", "node", "nodes", "pair", "point",
                                                                          "d", "y", "facets")}}
        rid, new = self._register(spec, parent=parent["id"], kind="edit", ops=len(edits), note=note, call_id=call_id)
        self._builds[rid] = tb
        out = {"status": "built", "revision": rid, "parent": parent["id"], "spec": spec_sha256(spec)[:8],
               "diff": G.diff_text(G.diff_specs(self.spec(parent["id"]), spec), reset=bool(reset_layout)),
               "receipt": self._receipt(tb)}
        if not new:
            out["note"] = f"this spec is revision {rid} already"
        if notes:
            out["notes"] = notes
        tag = self._next("edit")
        images = self._chart_images(rid, tb, tag)
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
                       blender=self._blender_receipt(r.get("result") or {}, RP.receipt(tb)["lines"]))
            cmp = self._compare(call_id, photos, by="astra", with_start=False,     # §4.5: photo_compare has them
                                tool="facet_edit")
            if cmp["status"] == "ok":
                out["compare_id"] = cmp["compare_id"]
                out["photos"] = cmp["photos"]
                images += cmp["images"]
            else:
                out["compare"] = {k: cmp.get(k) for k in ("status", "code", "message")}
        out["images"] = images
        self.save()
        return out

    def _variant_views(self, pids: list, n_panels: int, call_id: str) -> tuple[list, dict]:
        """Worker views (crop camera with the photo's pose, one pass) and the compositing record of each photo, from
        the latest compare that showed it: its crop box (at the scale of this sheet's layout, chosen by the box's
        aspect: sheets.variant_layout), photo stretch, background, gains and environment. A target photo's panels are
        the native_linear pass with the job's white-balanced target gain and the photo's stretch; a photo of another
        colour's are the gloss_black pass with the gloss gain and stretch that compare used for it (its record's
        ``gloss``; an older record without one: the job's gloss_black gain, and the photo's stretch for a dark
        colourway as seeing does)."""
        bs = self.basis
        origin = np.asarray(self.seeing.bounds(call_id)["origin_mm"] or (0.0, 0.0, 0.0), float)
        views, recs, render_params = [], {}, None
        for pid in pids:
            rid = self.rid(pid)
            last = next((c for c in reversed(self.state["compares"]) if pid in c["photos"]), None)
            if last is None:
                raise LabRefusal("E_STATE", f"photo {pid} was never compared; photo_compare it first")
            report = read(last["report"])
            prec = report["photos"][rid]
            x0, y0, x1, y1 = (int(v) for v in prec["crop"]["box"])
            layout = SH.variant_layout(n_panels, aspect=(x1 - x0) / (y1 - y0))
            _cols, pw, ph = layout
            k = min(1.0, pw / (x1 - x0), ph / (y1 - y0))
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
                    raise LabRefusal("E_STATE", f"photo {pid}'s latest compare has no gloss_black gain; "
                                                "photo_compare it first")
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
            recs[pid] = {"rid": rid, "box": (x0, y0, x1, y1), "k": k, "shape": cshape, "ccam": ccam,
                         "pose": bs.pose(rid), "layout": layout, "stretch": (lo, hi),
                         "background": prec["background"], "colour": self._colour(pid),
                         "px_per_mm": float(ppm) * k if ppm is not None and np.isfinite(ppm) else None, **look}
            if render_params is None:
                env = report.get("environment")
                render_params = self.seeing._render_params(self.rig, env, gains.get("light_frame"))
        return views, {"photos": recs, "render": render_params}

    def _head_scene(self, call_id: str):
        """(the head's exported scene as the compare reads it, without its two temple bodies; the GLB's part roles):
        the occluder of the variant crease lines (seeing's export and fit mesh, both cached by fingerprint; the
        basis' skip list leaves out the decals, as in the compare)."""
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

    def _variant_creases(self, photos: dict, models: dict, call_id: str) -> tuple[dict, dict]:
        """Each model's crease polylines at each photo by the compare overlay's rule (compare.crease_edges >= 6 deg,
        drawn where both faces face the camera and the depth shows them): ({(photo, model): polylines | None},
        info). ``models`` = {name: TempleBuild} (the head's revision and the built variants). Both temples are
        placed as the worker places them (TempleBuild.world at the spec's opening, the -X side its mirror), opened
        by the photo's temple pose (pose.rot_about_hinge, as the render's posed copies) and depth-tested against the
        head's exported scene with its two temple bodies swapped for the model's (``_head_scene``); when that scene
        cannot be read, against the two temples alone (info["occlusion"] says so). A WorkerFailed of the head's export
        (the worker died and was not recovered) is raised: it ends the run failed_worker, as from any tool. On the
        2026-10-11 package the head's lines match the compare overlay's (from the export) to 0-2 of 584-2905 px at
        five photos (test_temple_lab pins this method against the overlay rule). One task per photo and model on
        CREASE_THREADS threads (numpy and open3d release the GIL for most of it)."""
        bs = self.basis
        info: dict = {"occlusion": "scene"}
        try:
            rest, roles = self._head_scene(call_id)
        except WorkerFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - the lines are still drawn, depth-tested on the temples alone
            rest = roles = None
            info["occlusion"] = f"temples only ({type(exc).__name__}: {str(exc)[:80]})"
        worlds, failed = {}, {}
        for name, tb in models.items():
            try:
                worlds[name] = {s: tb.world(s) for s in (1, -1)}
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
            try:
                pp = PO.parse_pose(c["pose"]) if c["pose"] else {}
                bodies = []
                for s in (1, -1):
                    W, _p, tris = worlds[name][s]
                    th, axis = pp.get(s, (0.0, (0.0, 0.0)))
                    bodies.append((PO.rot_about_hinge(W, s, th, axis), tris))
                return C.crease_polylines_posed(bodies, occ[pid], c["ccam"], bs.frame, c["shape"])
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

    def variants(self, call_id: str, parent_revision: int, variants: list, photos: list, judgement) -> dict:
        self._judge(judgement, call_id, "facet_variants")
        self.save()
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
        base = self.spec(parent["id"])
        items, built = [], {}
        for v in variants:
            item = {"label": v["label"]}
            try:
                spec, _notes = G.apply_edits(base, v.get("edits") or [])
                tb = build_temple(spec)
            except G.EditError as e:
                item.update(revision=None, code=e.code, edit=e.index, message=brief(e.message))
            except TempleError as e:
                item.update(revision=None, code=e.code, edit=None, message=brief(e.message))
            else:
                rid, _new = self._register(spec, parent=parent["id"], kind="variant", label=v["label"],
                                           ops=len(v.get("edits") or []), call_id=call_id)
                self._builds[rid] = tb
                d = self.delta(head["revision"], rid)
                item.update(revision=rid, spec=spec_sha256(spec)[:8], vs_head_mm=_r(d["value_mm"]),
                            creases=self._crease_brief(tb))
                built[v["label"]] = (rid, spec, tb)
            items.append(item)
        pairs = {}
        names = list(built)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pairs[tuple(sorted((a, b)))] = self.delta(built[a][0], built[b][0])["value_mm"]
        out = {"status": "ok", "variants_id": vid, "parent": parent["id"], "head_revision": head["revision"],
               "variants": items, "pairwise_mm": {"|".join(k): _r(v) for k, v in pairs.items()}}
        images, rendered, lines = [], [], {}
        if built:
            images, render_status, rendered, lines = self._variant_sheets(call_id, vid, parent["id"], photos, head,
                                                                          built, items)
            if render_status:
                out["render"] = render_status
        # §4.1 precondition 2 counts only variants Astra was shown: built AND rendered at every photo of a call whose
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

    def _variant_sheets(self, call_id: str, vid: str, parent: int, photos: list, head: dict, built: dict,
                        items: list) -> tuple[list, dict | None, list, dict]:
        """The worker's read-only render of the head and the built variants (one pass per photo: _variant_views),
        composited into one sheet per photo; every render panel carries the photo's outline (magenta) and that
        model's own crease lines (_variant_creases; ridge orange, valley blue; the title says they are drawn from
        compare.CREASE_MIN_DEG, so a crease under it shows on no panel): (sheet paths, a render-status note or
        None, the labels rendered at every photo, {creases: {photo: {model: polylines drawn | None}},
        crease_occlusion} for the state record)."""
        n_panels = 2 + len(items)
        views, comp = self._variant_views(photos, n_panels, call_id)
        out_dir = self.root / "work" / "temple_lab" / "variants" / vid
        params = {**(comp["render"] or {}), "views": views, "passes": ["native_linear"],
                  "variants": [{"label": lb, "spec": spec} for lb, (_rid, spec, _tb) in built.items()],
                  "out_dir": str(out_dir)}
        deadline = hard_timeout_s("facet_variants", variants=len(built), photos=len(photos))
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
        rendered = [lb for lb in built if (res["variants"].get(lb) or {}).get("status") == "ok"
                    and all(f"{pid}--{lb}" in files for pid in photos)]
        status = None
        blender = {lb: b for lb, b in res["variants"].items() if b.get("status") != "ok"}
        if blender:
            status = {"blender_refused": {lb: f"{b.get('code')}: {str(b.get('message'))[:120]}"
                                          for lb, b in blender.items()}}
        models = {"head": self.build(head["revision"]),
                  **{lb: tb for lb, (_rid, _spec, tb) in built.items() if lb in res["variants"]
                     and res["variants"][lb].get("status") == "ok"}}
        polys, info = self._variant_creases(comp["photos"], models, call_id)
        notes = ([f"depth-tested on the {info['occlusion']}"] if info["occlusion"] != "scene" else []) + \
            ([f"none drawn for {sorted(info['failed'])[:4]}"] if info.get("failed") else [])
        if notes:
            status = {**(status or {}), "crease_lines": "; ".join(notes)}
        paths = []
        bs = self.basis
        for pid in photos:
            c = comp["photos"][pid]
            pc, mc = Seeing.photo_at(bs, c["rid"], c["box"], c["k"], c["shape"])
            lo, hi = c["stretch"]
            rlo, rhi = c["render_stretch"]
            width = SH.crease_width(c["px_per_mm"])

            def panel(name, label):
                """(label, the composited render with the photo outline and the model's crease lines, None)."""
                rgb, alpha = C.read_linear(files[f"{pid}--{name}"])
                im = Image.fromarray(C.apply_stretch(C.composite_render(rgb, alpha, c["gain"], c["background"]),
                                                     rlo, rhi))
                im = SH.with_outline(im, mc)
                pl = polys.get((pid, name))
                if pl is None:
                    return label + ", no crease lines", im, None
                return label, SH.with_creases(im, pl, width), None
            colour = c["colour"]
            panels = [(f"photo {pid}{' ' + colour if colour else ''} (stretch {lo:.0f}-{hi:.0f})",
                       Image.fromarray(C.apply_stretch(pc, lo, hi)), None),
                      panel("head", f"head r{head['revision']} ({self.s.head['id']})")]
            for it in items:
                lb = it["label"]
                if it.get("revision") is not None and f"{pid}--{lb}" in files:
                    panels.append(panel(lb, f"{lb}: r{it['revision']}, {it['vs_head_mm']:.2f} mm from head"))
                else:
                    why = it.get("message") or (blender.get(lb) or {}).get("message") or "refused"
                    code = it.get("code") or (blender.get(lb) or {}).get("code")
                    panels.append((f"{lb}: refused {code}", None, f"{lb} refused {code}: {why}"))
            view = str(bs.refs[c["rid"]].get("view_kind") or "view") + (f", {colour}" if colour else "")
            sheet = SH.variant_sheet(panels, f"variants {vid} of r{parent} at {pid} ({view}): {c['what']}; "
                                             f"{SH.crease_legend(C.CREASE_MIN_DEG)}", layout=c["layout"])
            p = out_dir / f"sheet-{pid}.png"
            sheet.save(p)
            paths.append(self._image(p, "variants", ref=vid, photo=pid, revision=head["revision"]))
        lines = {"creases": {pid: {name: None if polys.get((pid, name)) is None else len(polys[(pid, name)])
                                   for name in models} for pid in photos},
                 "crease_occlusion": info["occlusion"]}
        return paths, status, rendered, lines

    def restore_tool(self, call_id: str, checkpoint: str, judgement) -> dict:
        self._judge(judgement, call_id, "facet_restore")
        self.save()
        head = S.head_by_fingerprint(self.state, self.fingerprint_of(checkpoint))
        if head is None:
            raise LabRefusal("E_ARGS", f"checkpoint {checkpoint!r} holds no applied revision; applied: "
                                       f"{S.applied_checkpoints(self.state)[-12:]}")
        if self.s.head["fingerprint"] == head["fingerprint"]:
            return {"status": "ok", "checkpoint": self.s.head["id"], "revision": head["revision"], "changed": False}
        before = self.s.head["id"]
        r = self.s.restore_checkpoint(checkpoint, call_id=call_id)
        with self.state_lock:
            self.state["events"].append({"call_id": call_id, "tool": "facet_restore", "mutating": True,
                                         "checkpoint_before": before, "checkpoint_after": self.s.head["id"]})
        self.save()
        if r.get("status") != "ok":
            return {"status": "error", "code": "E_RESTORE", "message": r.get("message"),
                    "checkpoint": self.s.head["id"]}
        return {"status": "ok", "checkpoint": self.s.head["id"], "restored_from": checkpoint,
                "revision": head["revision"], "spec": self.state["revisions"][head["revision"]]["sha256"][:8]}

    def finish(self, call_id: str, status: str, best_checkpoint: str, summary: str, judgement, k: int) -> dict:
        self._judge(judgement, call_id, "facet_finish")
        self.save()
        if status not in ("matched", "stuck"):
            raise LabRefusal("E_ARGS", "status must be matched or stuck")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_MAX:
            raise LabRefusal("E_ARGS", f"summary must be 1..{SUMMARY_MAX} characters")
        best = S.head_by_fingerprint(self.state, self.fingerprint_of(best_checkpoint))
        if best is None:
            raise LabRefusal("E_ARGS", f"best_checkpoint {best_checkpoint!r} holds no applied revision; applied: "
                                       f"{S.applied_checkpoints(self.state)[-12:]}")
        with self.state_lock:                       # a snapshot: the distinctness builds run outside the lock
            snap = {key: json.loads(json.dumps(self.state[key])) for key in ("compares", "variants", "judgements")}
        missing = S.finish_missing(snap, status=status, best_revision=best["revision"], k=int(k),
                                   distinct=self.distinct, target_photos=self.photo_ids())
        if missing:
            with self.state_lock:
                self.state["finish_refusals"] += 1
            self.save()
            return {"status": "refused", "code": "E_FINISH", "missing": missing,
                    "message": "the run continues; meet the preconditions named in missing"}
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
        v = GSPEC.validate_spec(exp["path"])
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
                "spec": self.state["revisions"][best["revision"]]["sha256"][:8],
                "export": {"file": Path(exp["path"]).name, "sha256": exp["sha256"][:16]},
                "validate_spec": {"ok": v["ok"], "errors": v["errors"][:3], "warnings": len(v["warnings"]),
                                  "live_site_would_fail": v.get("live_site_would_fail", [])[:3]}}
