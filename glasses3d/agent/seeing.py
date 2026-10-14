"""The seeing tools over the live session (WP2_DESIGN §6): render_views, compare_to_photos, pick_surface_point and
fit_cameras, plus the head export they share (cached by fingerprint). Stage D wraps these as Astra's function tools.

Every camera is a WP3b camera in a NormFrame (g3d_host/blender_camera): the worker renders its Blender twin in a
temporary scene (g3d/render.py), the host rasters the exported GLB with the same camera (parts pass, compare), and
picking casts the same camera's pixel rays in the worker. Outputs go under ``<job>/work/{renders,compare,exports}``;
camera bases under ``<job>/cameras/basis-K`` (g3d_host/compare). Each call is journaled like the mutating ones.

- ``render_views(views, passes, size_px, background, target)``: views = "canonical" (front, rear, left, right, top,
  bottom, angled_left, angled_right: orthographic, framed to the render-enabled boxes + 5 %), "reference" (every
  photo of the basis at its frozen camera), "all", or a list of those names and custom
  ``{name, yaw, pitch, roll, perspective, target_mm, width_mm}`` views; target = {"part": name} or
  {"point_mm": [x, y, z], "radius_mm": r} frames the canonical views on a part or a point. Passes: native, clay,
  normal (worker), silhouette (the clay alpha) and parts (the host raster's first-hit part). One labelled contact
  sheet per <= 6 views (<= 2048 px), the frames, and ``manifest.json`` {camera, frame, origin, shape, engine, gpu,
  seconds} that pick_surface_point can reuse.
- ``compare_to_photos(refs, parts, passes, crops, size_px, basis)``: metrics on the frozen basis (compare.py), deltas
  against the previous compare on the same basis with ``model_bytes_identical`` / ``geometry_identical``, one sheet
  per reference (photo | native | clay | overlay), crops, ``work/compare/<call>/report.json``. E_NO_BASIS without a
  basis. With ``rig`` = "studio_dome_v1", "photo_env" or "catalog_band_v1" it is the photo-matched compare of
  WP6_FACETS §3.3 (``_matched_compare``): each photo's temple pose from the basis, the rig, the native_linear (and
  optional gloss_black) pass, a white-balanced (per-channel, linear) native gain and a luma gloss_black gain per job,
  frozen in ``work/compare/gains-<basis>.json`` with the light frame, the photo's acetate p2-p98 stretch on photo and
  render, a temple crop, one stacked image per photo (photo / native render / gloss_black render / overlay with the
  model outline, crease polylines and the chart grid: the renders asked, always above the overlay; a
  variant-colourway photo gets no native row), compact text (the px/mm of the image shown, the points_px frame,
  outer-edge offsets beside the job's start model's, the current
  compare_id), ``points_px`` charted through the posed raster, and a per-(geometry, photo, rig, passes, environment)
  cache. Photos, in both paths: "all" = every target-colourway photo; "default" = the target photos whose posed
  temple ROI is >= 40k px; a list names photos; None = "all" for photo_v1 and "default" for a matched rig. Every
  failure is journaled (a refusal of the arguments E_ARGS / E_NO_BASIS / E_NO_POSE, anything later E_COMPARE).
  ``photo_env`` = the environment fitted to the photos' front acetate (g3d_host/lighting.py; ``photo_env()``),
  frozen per job in ``work/compare/env-<basis>.json`` with its weights, residuals and photo shas. Its smoothing
  strength is chosen ONCE, by leave-one-photo-out cross-validation on the iteration-0 system, and kept by the later
  iterations; each iteration's own CV choice is recorded beside it as information (``cv_lambda``), never used.
  WP6 v3 (WP6_V3 §5), matched rigs only:
  - rows per photo: ``passes`` names the TARGET photos' rows (native_linear always, gloss_black when named; the lab
    asks native_linear only); a photo of another colour is rendered and shown in gloss_black only (its native render
    was never shown). The stack scale counts the rows actually stacked (3 or 4). One row order for every colour:
    photo, the render(s), the overlay last (v3p3 review: a colourway photo stacked photo / overlay / render, a target
    photo / render / overlay, and the lab's texts said so in two orders).
  - ``photo_names`` {rid: name} (optional): the name a photo's image titles and texts give it (the lab's ids "02",
    "B1"; default the basis name, "reference-02", "black-side", which the lab's texts never use: v3p3 review); in the
    cache key, since the titles are drawn into the images.
  - ``model_edges`` = [{pair "crown|logo", shape ridge | valley | flat, deg, pts [[d, y, X], ...], deg_at
    (optional: the signed angle at each point), range (optional: [lo, hi] signed over the edge), soft (optional: true
    on a soft facet|base border run the build smooth-shades: no sharp edge on it)}] (<= 64 edges; the builder's
    model_edges(tb), WP6 v3 I1; seeing draws what the flag says and cannot see the build's sharp edges): the creases in
    the +X temple's chart (its own vertices; opening 0; ``opening`` = the spec's hinge opening (deg, [x, z]) or its
    hinge dict; needs ``hinge_z_mm``). Refused (E_ARGS) unless every edge lies on the head's +X temple (median <=
    EDGE_ON_MODEL_MM from its vertices: another build, a wrong hinge_z_mm or a missing opening is caught), and unless
    its deg and deg_at lie in its own range (the roll-band mix-up). Each is posed per photo on both temples
    (pose.chart_to_posed: the spec's opening, then the photo's theta; side -1 = the mirrored temple), depth-tested by
    ray against the posed model (compare.edge_runs) and drawn INSTEAD of the mesh's dihedral creases in every overlay
    (compare.draw_edges: ridge orange, valley blue, flat grey, 2 px from 6 deg, 1 px below; with deg_at segment by
    segment; a soft border dashed thin grey and never labelled with an angle: SOFT_TEXT); labels read the build's own
    deg_at / range, never an angle seeing measures (EDGE_LABEL_TEXT: deg_at over the run the label sits on, else the
    whole edge's range); the render rows carry the model's outline. Without it the mesh rule stays and the render
    rows are bare (owner sheets).
  - ``zoom`` = [{rid, box [x0, y0, x1, y1] native px, geometry bool}] (<= 3; the photo need not be among ``refs``,
    which may then be []): per item one 2 x 2 image, every panel the box at k = compare.zoom_scale (<= 2, native or
    larger when it fits 760 x 500), the render made at k and the photo resampled on its exact grid: photo | render
    (target: native_linear; other colour: gloss_black; with the model's outline) / photo + model outline + model edges
    with angle labels ("16°", "-3°", "24-33°", "-8 to -3°") | geometry (the worker's normal pass as
    compare.geometry_view) or the render with the labelled edges; the stretch is the photo's compare stretch; text:
    the box, k, px/mm, the pixel map, what of the model the box holds, the edges, the render's backdrop share.
  - side views (view_kind "side"): ``outline_offsets_mm`` {charted | mirrored: {facing, elevation_deg, mm_per_px
    [min, max], top, bottom: [{d_mm, offset_mm, reason, px_per_mm}]}} every 10 mm from d 10 to the tip, photo minus
    model along the image's up (top) / down (bottom). Only edges a silhouette shows are measured
    (``_side_outline``): a temple seen from inside is not ("seen from inside"), nor the top in a view from above,
    where the silhouette is the top face's far edge ("top face in view"; 01 / 05 / B1 look 14-15 deg down); a
    station without a number gives its reason. That far edge is measured on photo and model alike
    (``top_silhouette``) and printed as the arm's top height, and tan(elevation) per mm the inner face lies further
    inward, minus its median over the stations (``_far_edge_text``; the face's lean and the matte's cut are in the
    record only: ``top_far_edge``): behind the facets it is the only number on the arm's
    top in those views (session2's head, r6 of run 7's package: +2.3..+2.6 mm at d 110, +3.5..+3.8 at d 120 against
    the d 40-90 level).
    The top / rear ``outer_edge_offsets_mm`` are still computed.
  - the cache key holds the model edges' sha256, the opening, the zoom box, k, geometry flag, the stretch's crop and
    the photo's name (MATCHED_SCHEMA /4: /3's rows were photo / overlay / render for a colourway photo).
- ``pick_surface_point(view, points)``: up to 64 pixel centres on a frozen, canonical or manifest camera ->
  {hit, object, partRole, point_mm, asset_mm, normal, face, distance}.
- ``fit_cameras(reason, images, views, refs=None)``: export the head, ``fit_views`` with 14 workers (EcoQoS opted
  out), freeze ``basis-K+1`` and report each reference's residuals with one overlay sheet.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import re
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree

from ..g3d_host import blender_camera as BC
from ..g3d_host import cameras as CAMS
from ..g3d_host import compare as C
from ..g3d_host import fit_views, fitmesh, glb, seat_check, spec
from ..g3d_host import lighting as L
from ..g3d_host import pose as PO
from ..g3d_host.camera import Camera, NormFrame, project_mm
from . import procs, print_freshness
from .journal import CheckpointMismatch, WorkerFailed
from .worker_client import WorkerDied

MANIFEST_SCHEMA = "g3d.render_manifest/1"
RENDER_DEADLINE_S = 300.0
EXPORT_DEADLINE_S = 120.0
PICK_DEADLINE_S = 30.0
BOUNDS_DEADLINE_S = 30.0
FIT_WORKERS = 14
DEFAULT_PASSES = ("native", "clay")
WORKER_PASSES = ("native", "clay", "normal")
ALL_PASSES = ("native", "clay", "normal", "silhouette", "parts")
VIEWS_PER_SHEET = 6
SIZE_RANGE = (64, 2048)
PICK_MAX = 64
CROP_MAX_UPSCALE = 4.0
MESH_CACHE = 4
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")
MATCHED_RIG = "studio_dome_v1"
ENV_RIG = "photo_env"
CATALOG_RIG = "catalog_band_v1"
MATCHED_RIGS = (MATCHED_RIG, ENV_RIG, CATALOG_RIG)
FLOAT_RIGS = (ENV_RIG, CATALOG_RIG)          # rendered as unclipped float (the gain is applied after the render)
MATCHED_PASSES = ("native_linear", "gloss_black")
MATCHED_SCHEMA = "g3d.photo_compare/4"     # /3 (WP6 v3): model edges, zoom, side outline offsets, rows actually stacked
#                                            /4 (v3p3): one row order (photo, renders, overlay), photo_names
#                                            (record "name"), so no /3 image is served from the cache
ZOOM_MAX = 3
ZOOM_MIN_PX = 8                 # a zoom box is at least 8 x 8 native px after clipping to the photo
MODEL_EDGES_MAX = 64
EDGE_POINTS_MAX = 4000          # points per model edge
EDGE_RECORD_PX = 6.0            # a drawn edge's runs are recorded every ~6 image px (native photo px, 0.1)
SIDE_VIEWS = ("side",)          # side views: outline_offsets_mm (_side_outline: which edges a view can measure)
SIDE_NAMES = {1: "charted", -1: "mirrored"}     # the +X temple (the chart) and its mirror: outline and model edges
SIDE_FACE_PX = 1.0              # a side view measures a temple's top (bottom) outline where its top (bottom) face shows
                                # < 1 native px; from 14 deg above it shows 11-15 px and the silhouette is its far edge
FAR_EDGE_LEVEL_MIN = 5          # a face-in-view far edge is printed minus its median over >= 5 measured stations
#                                 (fewer: as measured). Why a level: the far edge carries the matte's cut at a lit face
#                                 (session2's head: -0.7..-1.0 mm at d 40-90; the model's own render under the photo's
#                                 rule -0.8..-1.1) and the faces' width difference x tan(elevation), alike along an arm.
#                                 Why the median of all stations, not the start model's (it has the rear-arm defect
#                                 too: 0 there) or a fixed d band (d 50-70 moves ~1.4 mm with crown edits; the plain
#                                 zone's d range is the design's): v3p3-verify-8. 5: two stepped stations of five still
#                                 leave the median on the level.
EDGE_ON_MODEL_MM = 0.05         # model edges lie on the head's +X temple: per edge median distance to its vertices
OUTLINE_REASON_TEXT = {         # why a side-view outline station has no number (compare.OUTLINE_REASONS + seeing's)
    "covered": "covered = the frame or the other temple lies at that edge",
    "off image": "off image = the station or its edge lies outside the photo",
    "off temple": "off temple = the station's centre misses the temple",
    "no model edge": "no model edge = the temple does not end within 45 mm",
    "no photo edge": "no photo edge = the photo has no edge within 6 mm of the model's",
    "seen from inside": "seen from inside = the temple's outer face turns away from the camera",
    "top face in view": "top face in view = the view shows the top face; the silhouette is its far edge",
    "bottom face in view": "bottom face in view = the view shows the bottom face; the silhouette is its far edge"}
SOFT_TEXT = ("dashed grey: smooth-shaded, drawn without an angle; the fold is listed with the build's creases")
#               a soft facet|base border (I1: a soft facet's run with no sharp edge in the build) stays a crease in
#               the builder's tables, with its angle, soft and its declared shape (review2-seeing2-1 P3-2: "no crease,
#               no angle" contradicted the receipt's "shoulder|base valley 2.5° soft declared valley")
EDGE_LABEL_TEXT = ("deg from the build; - = valley; a-b° (a to b° when a is negative) = least-largest angle along "
                   "the drawn run the label sits on (along the whole edge when the build gives no angle per point)")
#               compare.run_label: deg_at over the label's run (the lab always sends deg_at), else the edge's range
#               (review2-seeing2-1 P3-1: "along the edge" was false on the lab's path: "16..20" for a 15.7..29.2 crease;
#               v3p3 review: "16..29" read as "16.29" at the size Astra gets the image, beside the receipt's
#               "15.7-29.2")
ENV_FIT_MAX_PX = 640          # calibration crops are rendered at <= this long side (the fit cells are ~1 mm)
ENV_ERODE_PX = 12.0            # front region erosion at native scale (at least; 2 x the front's registration error)
ENV_SIGMA_MM, ENV_STEP_MM = 1.0, 1.0
ENV_ITERATIONS = 3             # re-linearised fits, each rendered directly; the best direct residual is kept
ENV_DELTA = 0.5                # differential basis step, x the mean weight at the linearisation point
ENV_DEADLINE_S = 900.0
GAIN_VIEW_MAX_PX = 1600
TEMPLE_ROI_MIN_PX = 40_000
TEMPLE_REGION_PX = 20           # a compare's temple region: the posed temple pixels dilated by this many px
DARK_L8 = 80.0                 # a colourway whose photo acetate median luma is below this is dark (black acetate)
GLOSS_TARGET_L8 = 40.0         # without a dark photo, gloss_black is exposed so the front acetate reads this luma
GLOSS_FIXED_STRETCH = (0.0, 128.0)
GRID_STATION_MM = 10.0
OFFSET_VIEWS = ("top", "rear")  # the view kinds whose compares measure outer-edge offsets (the brief names them)


class SeeingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _err(call_id: str, code: str, message: str) -> dict:
    return {"status": "error", "call_id": call_id, "code": code, "message": message}


def _safe_view_names(names, *, reserved=()) -> dict[str, str]:
    """Stable file-safe identities without changing the logical names returned to callers.

    The worker strips punctuation and truncates names, so names changed by that
    operation need a digest rather than a shared underscore spelling.
    """
    names = list(dict.fromkeys(names))
    reserved = {n.casefold() for n in reserved}
    used = reserved | {n.casefold() for n in names}
    assigned, out = set(), {}
    for name in names:
        if (NAME_RE.fullmatch(name) and name.strip("._") == name and name.casefold() not in reserved
                and name.casefold() not in assigned):
            safe = name
        else:
            stem = "ref-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
            safe, suffix = stem, 0
            while safe.casefold() in used:
                suffix += 1
                safe = f"{stem}-{suffix}"
        out[name] = safe
        used.add(safe.casefold())
        assigned.add(safe.casefold())
    return out


class Seeing:
    def __init__(self, session) -> None:
        self.s = session
        self.root = Path(session.root)
        self.work = self.root / "work"
        self._bounds: tuple[str, dict] | None = None
        self._meshes: OrderedDict = OrderedDict()
        self._export_reports: dict[str, dict] = {}

    # ---------------------------------------------------------------------------------------------- shared
    @property
    def head(self) -> dict:
        return self.s.head

    def _prepare_work_dir(self, path: Path) -> Path:
        """Confine host-written outputs too, including passes that never call Blender."""
        base = self.root.resolve() / "work"
        real = path.resolve()
        if not real.is_relative_to(base) or real == base:
            raise SeeingError("E_PATH", f"output path outside the job's work folder: {real}")
        real.mkdir(parents=True, exist_ok=True)
        return real

    def bounds(self, call_id: str) -> dict:
        fp = self.head["fingerprint"]
        if self._bounds and self._bounds[0] == fp:
            return self._bounds[1]
        r = self.s.call_read("bounds", "bounds", {}, deadline_s=BOUNDS_DEADLINE_S, call_id=call_id)
        if r["status"] != "ok":
            raise SeeingError("E_WORKER", r.get("message") or r["status"])
        self._bounds = (fp, r["result"])
        return r["result"]

    def export_head(self, call_id: str | None = None) -> dict:
        """Export the head with spec and all-frame seating reports, cached by the actual GLB sha256.

        Validation failures stay visible so seeing tools can inspect an unfinished model. Finish/delivery decide
        whether it is acceptable. Old export indexes are upgraded here, never trusted as validated exports.
        """
        call_id = call_id or self.s.new_call_id("export_head")
        head = self.head
        fp = head["fingerprint"]
        d = self.work / "exports"
        index_path = d / "index.json"
        index = procs.read_json(index_path) or {}
        rec = index.get(fp)
        if rec and Path(rec["path"]).is_file() and C.sha256_file(rec["path"]) == rec["sha256"]:
            # A restore records a new checkpoint id with the same geometry fingerprint. Cached bytes and their
            # validation remain usable, but tools/finish must bind their receipt to the current journal head.
            return {**rec, **self._validate_export(rec), "checkpoint": head["id"], "cached": True}
        path = d / f"head-{fp[:16]}.glb"
        r = self.s.call_read("export_glb", "export_glb", {"path": str(path)}, deadline_s=EXPORT_DEADLINE_S,
                             call_id=call_id)
        if r["status"] != "ok":
            return {"status": r["status"], "code": "E_WORKER", "message": r.get("message") or r["status"]}
        res = r["result"]
        if res.get("status") != "exported":
            return {"status": "refused", "code": res.get("code", "E_EXPORT"), "message": res.get("message")}
        receipt = res["receipt"]
        rec = {"status": "exported", "path": res["path"], "sha256": res["sha256"], "bytes": res["bytes"],
               "fingerprint": res["fingerprint_before"], "checkpoint": head["id"],
               "objects": receipt.get("object_count"), "triangles": receipt.get("triangle_count"),
               "notes": receipt.get("notes", []), "receipt": str(path.with_suffix(".json"))}
        if res["fingerprint_before"] != fp:
            rec["drift"] = f"the worker's scene {res['fingerprint_before'][:12]} is not head {head['id']}"
        C.write_json(path.with_suffix(".json"), receipt)
        rec.update(self._validate_export(rec))
        index[fp] = rec
        procs.write_json_atomic(index_path, index)
        return {**rec, "cached": False}

    def _validate_export(self, rec: dict) -> dict:
        sha = C.sha256_file(rec["path"])
        if sha != rec["sha256"]:
            raise SeeingError("E_EXPORT", "export bytes differ from the worker's sha256")
        if sha not in self._export_reports:
            validation = spec.validate_spec(rec["path"])
            # D4: every frame, including crystal, needs the geometric report. Missing declarations or lens
            # geometry are checker failures, never a reason to skip it. The checker reports malformed GLBs too.
            seating = seat_check.check_glb(rec["path"])
            self._export_reports[sha] = {"validate_spec": validation, "seat_check": seating,
                                         "validation_sha256": sha}
        # Successful print evidence is finalized after the first post-attachment export. Geometry bytes may also
        # be restored from an older checkpoint, so evidence cannot share the spec/seating SHA-only cache.
        return {**self._export_reports[sha], "print_check": print_freshness.check_current_print(rec["path"], self.root)}

    def fit_mesh(self, glb: str, sha: str, *, skip=(), frame: NormFrame | None = None) -> fitmesh.FitMesh:
        key = (sha, tuple(skip), None if frame is None else (tuple(frame.center), frame.extent))
        if key in self._meshes:
            self._meshes.move_to_end(key)
            return self._meshes[key]
        mesh = fitmesh.load_fit_mesh(glb, skip=tuple(skip), frame=frame)
        self._meshes[key] = mesh
        while len(self._meshes) > MESH_CACHE:
            self._meshes.popitem(last=False)
        return mesh

    # ---------------------------------------------------------------------------------------------- views
    @staticmethod
    def _scene_frame(b: dict) -> tuple[NormFrame, np.ndarray, np.ndarray]:
        origin = np.asarray(b["origin_mm"] or (0.0, 0.0, 0.0), float)
        lo, hi = np.asarray(b["lo"], float) - origin, np.asarray(b["hi"], float) - origin
        return NormFrame(tuple(float(x) for x in (lo + hi) / 2), float(max((hi - lo).max(), 1e-6))), lo, hi

    def _target_points(self, b: dict, target, origin: np.ndarray) -> np.ndarray:
        if target is None:
            return np.vstack([BC.box_corners(np.asarray(o["lo"]) - origin, np.asarray(o["hi"]) - origin)
                              for o in b["objects"].values()])
        if not isinstance(target, dict):
            raise SeeingError("E_ARGS", "target must be {'part': name} or {'point_mm': [x, y, z], 'radius_mm': r}")
        if "part" in target:
            want = str(target["part"]).lower()
            hits = [o for n, o in b["objects"].items() if want in n.lower()]
            if not hits:
                raise SeeingError("E_ARGS", f"no rendered object matches {target['part']!r}; objects: "
                                            f"{sorted(b['objects'])}")
            return np.vstack([BC.box_corners(np.asarray(o["lo"]) - origin, np.asarray(o["hi"]) - origin)
                              for o in hits])
        if "point_mm" in target:
            p = np.asarray(target["point_mm"], float) - origin
            r = float(target.get("radius_mm", 10.0))
            if p.shape != (3,) or not r > 0:
                raise SeeingError("E_ARGS", "point_mm needs three numbers and radius_mm a positive number")
            return BC.box_corners(p - r, p + r)
        raise SeeingError("E_ARGS", "target must name a 'part' or a 'point_mm'")

    def resolve_views(self, views, size_px: int, target=None, basis_id: str | None = None, *,
                      call_id: str) -> list[dict]:
        """Each view as {name, kind, camera, frame, origin, shape, px_per_mm[, ref]}.

        Bare canonical names keep their meaning. ``reference:<photo id or filename>``
        explicitly selects a fitted photo, including one named front/top/etc.
        """
        size_px = int(size_px)
        if not SIZE_RANGE[0] <= size_px <= SIZE_RANGE[1]:
            raise SeeingError("E_ARGS", f"size_px must be {SIZE_RANGE[0]}..{SIZE_RANGE[1]}")
        b = self.bounds(call_id)
        if b["lo"] is None:
            raise SeeingError("E_EMPTY", "nothing in the scene renders (no render-enabled surface object)")
        origin = np.asarray(b["origin_mm"] or (0.0, 0.0, 0.0), float)
        frame, lo, hi = self._scene_frame(b)
        basis = None
        items = [views] if isinstance(views, (str, dict)) else list(views)
        expanded = []
        for it in items:
            if it in ("canonical", "all"):
                expanded += list(BC.CANONICAL_VIEWS)
            if it in ("reference", "all"):
                basis = basis or C.load_basis(self.root, basis_id)
                expanded += [("reference", rid) for rid in basis.usable_refs()]
            if it not in ("canonical", "all", "reference"):
                expanded.append(it)
        out, names = [], set()
        pts = None
        for n_custom, it in enumerate(expanded, 1):
            if isinstance(it, dict):
                name = str(it.get("name") or f"custom-{n_custom:02d}")
                try:
                    yaw, pitch = float(it["yaw"]), float(it["pitch"])
                except (KeyError, TypeError, ValueError):
                    raise SeeingError("E_ARGS", "a custom view needs numeric yaw and pitch") from None
                tgt = np.asarray(it["target_mm"], float) - origin if it.get("target_mm") is not None else \
                    (lo + hi) / 2
                width = float(it.get("width_mm") or 1.05 * float(np.linalg.norm(hi - lo)))
                try:
                    cam, shape = BC.custom_view(yaw, pitch, tgt, width, frame, size_px,
                                                roll=float(it.get("roll", 0.0)),
                                                perspective=float(it.get("perspective", 0.0)))
                except ValueError as exc:
                    raise SeeingError("E_ARGS", f"view {name}: {exc}") from None
                rec = {"name": name, "kind": "custom", "camera": cam, "frame": frame, "origin": origin,
                       "shape": shape, "px_per_mm": BC.px_per_mm_at(cam, frame, tgt), "request": it}
            elif isinstance(it, str) and it in BC.CANONICAL_VIEWS:
                if pts is None:
                    pts = self._target_points(b, target, origin)
                yaw, pitch = BC.CANONICAL_VIEWS[it]
                cam, shape = BC.framed_view(yaw, pitch, pts, frame, size_px)
                rec = {"name": it, "kind": "canonical", "camera": cam, "frame": frame, "origin": origin,
                       "shape": shape, "px_per_mm": cam.scale / frame.extent}
            elif isinstance(it, str) or (isinstance(it, tuple) and len(it) == 2 and it[0] == "reference"):
                basis = basis or C.load_basis(self.root, basis_id)
                requested = it[1] if isinstance(it, tuple) else it.removeprefix("reference:")
                if (not requested or requested in (".", "..") or any(c in requested for c in ("/", "\\", ":"))):
                    raise SeeingError("E_ARGS", "a reference view needs a photo id or filename, not a path")
                rid = requested if requested in basis.refs else C.ref_id(requested)
                if rid not in basis.refs:
                    raise SeeingError("E_ARGS", f"{basis.id} has no {rid}; references: {list(basis.refs)}")
                cam0, (H, W) = basis.camera(rid), basis.shape(rid)
                k = min(1.0, size_px / max(H, W))
                cam = BC.crop_camera(cam0, 0.0, 0.0, k) if k < 1.0 else cam0
                shape = (max(1, round(H * k)), max(1, round(W * k)))
                safe = _safe_view_names(basis.refs, reserved=BC.CANONICAL_VIEWS)[rid]
                rec = {"name": safe, "kind": "reference", "camera": cam, "frame": basis.frame, "origin": origin,
                       "shape": shape, "scale_k": k, "basis": basis.id, "ref": rid,
                       "px_per_mm": BC.px_per_mm_at(cam, basis.frame, basis.pivot_mm)}
            else:
                raise SeeingError("E_ARGS", f"unknown view {it!r}: use {list(BC.CANONICAL_VIEWS)}, a fitted photo id, "
                                            "'canonical', 'reference', 'all' or a custom {yaw, pitch, ...} view")
            if not NAME_RE.match(rec["name"]) or rec["name"] in names:
                raise SeeingError("E_ARGS", f"view name {rec['name']!r} is invalid or repeated")
            names.add(rec["name"])
            try:
                rec["blender"] = BC.to_blender(rec["camera"], rec["frame"], origin, rec["shape"])
            except ValueError as exc:
                raise SeeingError("E_ARGS", f"view {rec['name']}: {exc}") from None
            out.append(rec)
        if not out and basis is not None:
            raise SeeingError("E_UNUSABLE_CAMERA", f"{basis.id} has no usable photo cameras: {basis.unusable_refs()}")
        if not out:
            raise SeeingError("E_ARGS", "no views requested")
        return out

    def _light_frame(self, call_id: str) -> dict:
        b = self.bounds(call_id)
        lo, hi = np.asarray(b["lo"], float), np.asarray(b["hi"], float)
        return {"center": ((lo + hi) / 2).tolist(), "radius": float(np.linalg.norm(hi - lo) / 2)}

    def _render(self, tool: str, call_id: str, views: list[dict], out_dir: Path, *, engine: str = "auto",
                samples: int = 16, simulate_eevee_failure: bool = False) -> dict:
        safe_names = _safe_view_names(v["name"] for v in views)
        params = {"out_dir": str(out_dir), "engine": engine, "samples": int(samples),
                  "light_frame": self._light_frame(call_id),
                  "views": [{"name": safe_names[v["name"]], "camera": v["blender"], "passes": v["worker_passes"]}
                            for v in views if v["worker_passes"]]}
        if simulate_eevee_failure:
            params["simulate_eevee_failure"] = True
        if not params["views"]:
            return {"status": "ok", "result": {"views": [], "engine_final": None, "gpu": None, "fallback": None,
                                               "fingerprint_before": self.head["fingerprint"], "seconds": 0.0}}
        result = self.s.call_read(tool, "render", params, deadline_s=RENDER_DEADLINE_S, call_id=call_id)
        logical_names = {safe: name for name, safe in safe_names.items()}
        if result.get("status") == "ok":
            for view in result["result"]["views"]:
                view["name"] = logical_names[view["name"]]
        return result

    # ---------------------------------------------------------------------------------------------- render_views
    def render_views(self, views="canonical", passes=DEFAULT_PASSES, size_px: int = 1024, background="grey",
                     target=None, basis: str | None = None, engine: str = "auto", samples: int = 16,
                     call_id: str | None = None, simulate_eevee_failure: bool = False) -> dict:
        call_id = self.s.new_call_id("render_views", call_id)
        args = {"views": views, "passes": list(passes), "size_px": size_px, "background": background,
                "target": target, "basis": basis, "engine": engine}
        t0 = time.perf_counter()
        try:
            passes = list(dict.fromkeys(passes))
            bad = [p for p in passes if p not in ALL_PASSES]
            if not passes or bad:
                raise SeeingError("E_ARGS", f"unknown passes {bad}; choose from {list(ALL_PASSES)}")
            C.rgba_on(Image.new("RGBA", (1, 1)), background)
            vs = self.resolve_views(views, size_px, target, basis, call_id=call_id)
            used_basis = next((v["basis"] for v in vs if v["kind"] == "reference"), None)
            requested = [views] if isinstance(views, (str, dict)) else list(views)
            if used_basis is None and any(isinstance(v, str) and v in ("reference", "all") for v in requested):
                used_basis = C.load_basis(self.root, basis).id
            unavailable = C.load_basis(self.root, used_basis).unusable_refs() if used_basis else {}
            out_dir = self._prepare_work_dir(self.work / "renders" / call_id)
        except C.NoBasis as exc:
            out = _err(call_id, "E_NO_BASIS", str(exc))
            self.s.record_call("render_views", call_id, args, out)
            return out
        except (SeeingError, ValueError, OSError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_ARGS"), str(exc))
            self.s.record_call("render_views", call_id, args, out)
            return out
        wp = [p for p in WORKER_PASSES if p in passes or (p == "clay" and "silhouette" in passes)]
        for v in vs:
            v["worker_passes"] = wp
        r = self._render("render_views", call_id, vs, out_dir, engine=engine, samples=samples,
                         simulate_eevee_failure=simulate_eevee_failure)
        if r["status"] != "ok":
            self.s.record_call("render_views", call_id, args, r)
            return r
        res = r["result"]
        files = {v["name"]: v["files"] for v in res["views"]}
        notes = []
        exp, legend_all = None, {}
        if "parts" in passes:
            exp = self.export_head(call_id)
            if exp.get("status") != "exported":
                notes.append(f"parts pass skipped: the scene does not export ({exp.get('message')})")
        manifest_views, tiles = {}, []
        for v in vs:
            fv = dict(files.get(v["name"], {}))
            if "silhouette" in passes and "clay" in fv:
                p = out_dir / f"{v['name']}_silhouette.png"
                alpha = np.asarray(Image.open(fv["clay"]["path"]).convert("RGBA"))[..., 3]
                Image.fromarray(((alpha > 127) * 255).astype(np.uint8)).save(p)
                fv["silhouette"] = {"path": str(p), "sha256": C.sha256_file(p), "engine": fv["clay"]["engine"],
                                    "from": "clay alpha > 0.5"}
            if "parts" in passes and exp and exp.get("status") == "exported":
                mesh = self.fit_mesh(exp["path"], exp["sha256"], frame=v["frame"])
                img, legend = C.parts_image(mesh, v["camera"], v["shape"])
                p = out_dir / f"{v['name']}_parts.png"
                img.save(p)
                legend_all.update(legend)
                fv["parts"] = {"path": str(p), "sha256": C.sha256_file(p), "engine": "host raster of the export",
                               "glb_sha256": exp["sha256"], "legend": legend}
            for ps, f in fv.items():
                a = np.asarray(Image.open(f["path"]).convert("RGBA"))[..., 3] if ps != "silhouette" else \
                    np.asarray(Image.open(f["path"]))
                f["coverage"] = float((a > 127).mean())
            imgs = [C.rgba_on(Image.open(fv[p]["path"]), background) if p != "silhouette" else
                    Image.open(fv[p]["path"]).convert("RGBA") for p in passes if p in fv]
            if imgs:
                tiles.append((f"{v.get('ref', v['name'])} ({v['kind']}, {v['px_per_mm']:.2f} px/mm) " + " | ".join(
                    p for p in passes if p in fv), imgs))
            manifest_views[v["name"]] = {
                "kind": v["kind"], "camera": v["camera"].to_dict(), "frame": v["frame"].to_dict(),
                "origin_mm": [float(x) for x in v["origin"]], "shape": list(v["shape"]),
                "px_per_mm": v["px_per_mm"], "blender": v["blender"], "files": fv,
                **({"ref": v["ref"], "basis": v["basis"], "scale_k": v["scale_k"]} if v["kind"] == "reference"
                   else {})}
        sheets = []
        for i in range(0, len(tiles), VIEWS_PER_SHEET):
            sheet = C.contact_sheet(tiles[i:i + VIEWS_PER_SHEET])
            p = out_dir / f"sheet-{i // VIEWS_PER_SHEET + 1:02d}.png"
            sheet.convert("RGB").save(p)
            sheets.append(str(p))
        manifest = {"schema": MANIFEST_SCHEMA, "call_id": call_id, "checkpoint": self.head["id"],
                    "fingerprint": res.get("fingerprint_before"), "engine": res.get("engine_final"),
                    "fallback": res.get("fallback"), "gpu": res.get("gpu"), "rig": res.get("rig"),
                    "passes": passes, "size_px": size_px, "background": background, "views": manifest_views,
                    "sheets": sheets, "parts_legend": legend_all, "notes": notes, "export": exp,
                    "unusable_refs": unavailable,
                    "worker_seconds": res.get("seconds"), "seconds": round(time.perf_counter() - t0, 3)}
        mp = out_dir / "manifest.json"
        C.write_json(mp, manifest)
        out = {"status": "ok", "call_id": call_id, "sheets": sheets, "manifest": str(mp),
               "views": {n: {"kind": m["kind"], "shape": m["shape"], "px_per_mm": round(m["px_per_mm"], 4),
                             **({"ref": m["ref"], "basis": m["basis"]} if m["kind"] == "reference" else {}),
                             "files": {p: f["path"] for p, f in m["files"].items()}}
                         for n, m in manifest_views.items()},
               "engine": res.get("engine_final"), "fallback": res.get("fallback"), "gpu": res.get("gpu"),
               "restored_to": r.get("restored_to"), "notes": notes, "export": exp, "seconds": manifest["seconds"],
               "unusable_refs": unavailable}
        self.s.record_call("render_views", call_id, args, out)
        return out

    # ---------------------------------------------------------------------------------------------- pick
    def pick_surface_point(self, view, points, *, manifest: str | None = None, size_px: int = 1024, target=None,
                           basis: str | None = None, call_id: str | None = None) -> dict:
        call_id = self.s.new_call_id("pick_surface_point", call_id)
        args = {"view": view, "points": points, "manifest": manifest, "size_px": size_px, "target": target,
                "basis": basis}
        try:
            uv = np.asarray(points, float)
            if uv.ndim != 2 or uv.shape[1] != 2 or not 1 <= len(uv) <= PICK_MAX or not np.isfinite(uv).all():
                raise SeeingError("E_ARGS", f"points must be 1..{PICK_MAX} pixel positions [u, v]")
            if manifest:
                m = json.loads(Path(manifest).read_text(encoding="utf-8"))
                if m.get("schema") != MANIFEST_SCHEMA or view not in m.get("views", {}):
                    raise SeeingError("E_ARGS", f"{manifest} has no view {view!r}")
                mv = m["views"][view]
                spec = mv["blender"]
                shape = tuple(mv["shape"])
            else:
                v = self.resolve_views([view], size_px, target, basis, call_id=call_id)[0]
                spec, shape = v["blender"], v["shape"]
            H, W = shape
            if (uv[:, 0] < -0.5).any() or (uv[:, 0] > W - 0.5).any() or (uv[:, 1] < -0.5).any() or \
                    (uv[:, 1] > H - 0.5).any():
                raise SeeingError("E_ARGS", f"points must lie inside the {W} x {H} image")
        except C.NoBasis as exc:
            out = _err(call_id, "E_NO_BASIS", str(exc))
            self.s.record_call("pick_surface_point", call_id, args, out)
            return out
        except (SeeingError, ValueError, OSError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_ARGS"), str(exc))
            self.s.record_call("pick_surface_point", call_id, args, out)
            return out
        ro, rd = BC.rays(spec, uv)
        r = self.s.call_read("pick_surface_point", "pick",
                             {"rays": [{"origin": o.tolist(), "direction": d.tolist()} for o, d in zip(ro, rd)]},
                             deadline_s=PICK_DEADLINE_S, call_id=call_id)
        if r["status"] != "ok":
            self.s.record_call("pick_surface_point", call_id, args, r)
            return r
        hits = []
        for (u, v_), h in zip(uv.tolist(), r["result"]["hits"]):
            hits.append({"uv": [u, v_], **h})
        out = {"status": "ok", "call_id": call_id, "view": view, "shape": list(shape), "hits": hits,
               "origin_mm": r["result"]["origin_mm"], "checkpoint": self.head["id"]}
        self.s.record_call("pick_surface_point", call_id, args, out)
        return out

    # ---------------------------------------------------------------------------------------------- compare
    def compare_to_photos(self, refs=None, parts="all", passes=DEFAULT_PASSES, crops=None, size_px: int = 1024,
                          basis: str | None = None, call_id: str | None = None, *, rig: str = "photo_v1",
                          crop: str = "temple", points_px: dict | None = None, hinge_z_mm: float | None = None,
                          gains: dict | None = None, boxes: dict | None = None, use_cache: bool = True,
                          env: dict | None = None, render_deadline_s: float | None = None, zoom: list | None = None,
                          model_edges: list | None = None, opening=None, photo_names: dict | None = None) -> dict:
        """Module doc. Every failure is journaled: an unexpected exception becomes a recorded E_COMPARE.
        ``render_deadline_s`` (matched rigs) caps the worker deadline of the compare renders below RENDER_DEADLINE_S
        (the facet lab passes its tool's hard timeout). ``zoom``, ``model_edges``, ``opening`` and ``photo_names``
        (WP6 v3, module doc) exist for the matched rigs only."""
        call_id = self.s.new_call_id("compare_to_photos", call_id)
        try:
            if rig != "photo_v1":
                return self._matched_compare(refs, passes, crop, basis, call_id, rig=rig, points_px=points_px,
                                             hinge_z_mm=hinge_z_mm, gains_override=gains, boxes=boxes,
                                             use_cache=use_cache, env_override=env,
                                             render_deadline_s=render_deadline_s, zoom=zoom,
                                             model_edges=model_edges, opening=opening, photo_names=photo_names)
            if zoom is not None or model_edges is not None or opening is not None or photo_names is not None:
                out = _err(call_id, "E_ARGS", "zoom, model_edges, opening and photo_names need a photo-matched rig "
                                              f"{MATCHED_RIGS}, not photo_v1")
                self.s.record_call("compare_to_photos", call_id, {"refs": refs, "rig": rig, "basis": basis}, out)
                return out
            return self._compare_v1(refs, parts, passes, crops, size_px, basis, call_id)
        except (WorkerFailed, CheckpointMismatch, WorkerDied):
            raise
        except Exception as exc:  # noqa: BLE001 - reported and journaled, never raised to the caller
            out = _err(call_id, "E_COMPARE", f"{type(exc).__name__}: {exc}"[:2000])
            self.s.record_call("compare_to_photos", call_id, {"refs": refs, "rig": rig, "basis": basis}, out)
            return out

    def _compare_v1(self, refs, parts, passes, crops, size_px, basis, call_id: str) -> dict:
        args = {"refs": refs, "parts": parts, "passes": list(passes or ()), "crops": crops, "size_px": size_px,
                "basis": basis}
        t0 = time.perf_counter()
        secs = {}
        try:
            bs = C.load_basis(self.root, basis)
            if parts not in ("all", "front"):
                raise SeeingError("E_ARGS", "parts must be 'all' or 'front'")
            passes = [p for p in dict.fromkeys(passes or ()) if p]
            if any(p not in ("native", "clay") for p in passes):
                raise SeeingError("E_ARGS", "compare passes are native and clay (or none)")
            rids = "default" if refs == "default" else self._ref_ids(bs, refs)
            crops = list(crops or [])
            size_px = int(size_px)
            if not SIZE_RANGE[0] <= size_px <= SIZE_RANGE[1]:
                raise SeeingError("E_ARGS", f"size_px must be {SIZE_RANGE[0]}..{SIZE_RANGE[1]}")
            out_dir = self._prepare_work_dir(self.work / "compare" / call_id)
        except C.NoBasis as exc:
            out = _err(call_id, "E_NO_BASIS", str(exc))
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        except (SeeingError, ValueError, OSError, TypeError, KeyError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_ARGS"), str(exc))
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        exp = self.export_head(call_id)
        secs["export"] = round(time.perf_counter() - t0, 3)
        if exp.get("status") != "exported":
            out = _err(call_id, exp.get("code") or "E_EXPORT",
                       f"the scene does not export, so it cannot be compared: {exp.get('message')}")
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        t1 = time.perf_counter()
        mesh = self.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
        gkey = C.geometry_key(mesh)
        if rids == "default":
            try:
                rids = self.default_refs(bs, mesh, PO.part_roles(exp["path"]))[0]
            except SeeingError as exc:
                out = _err(call_id, exc.code, str(exc))
                self.s.record_call("compare_to_photos", call_id, args, out)
                return out
        metrics, masks = {}, {}
        for rid in rids:
            metrics[rid], masks[rid] = C.reference_metrics(mesh, bs, rid)
        secs["metrics"] = round(time.perf_counter() - t1, 3)
        hist = C.history(bs)
        prev = hist[-1] if hist else None
        for rid in rids:
            metrics[rid]["delta"] = C.deltas(metrics[rid], (prev or {}).get("refs", {}).get(rid))
        # renders: every reference (scaled to size_px) and every crop, in one worker call
        rviews, crop_recs = [], []
        origin = np.asarray(self.bounds(call_id)["origin_mm"] or (0.0, 0.0, 0.0), float)
        for rid in rids:
            cam0, (H, W) = bs.camera(rid), bs.shape(rid)
            k = min(1.0, size_px / max(H, W))
            cam = BC.crop_camera(cam0, 0, 0, k) if k < 1.0 else cam0
            shape = (max(1, round(H * k)), max(1, round(W * k)))
            rviews.append({"name": rid, "camera": cam, "shape": shape, "blender": BC.to_blender(cam, bs.frame, origin,
                                                                                               shape),
                           "worker_passes": passes})
        try:
            for n, cr in enumerate(crops, 1):
                crop_recs.append(self._crop_view(bs, mesh, cr, n, origin, passes))
        except (SeeingError, ValueError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_ARGS"), f"crop: {exc}")
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        rviews += [c["view"] for c in crop_recs]
        t2 = time.perf_counter()
        rr = self._render("compare_to_photos", call_id, rviews, out_dir / "renders") if passes else \
            {"status": "ok", "result": {"views": [], "engine_final": None, "gpu": None, "fallback": None}}
        secs["renders"] = round(time.perf_counter() - t2, 3)
        if rr["status"] != "ok":
            self.s.record_call("compare_to_photos", call_id, args, rr)
            return rr
        rfiles = {v["name"]: v["files"] for v in rr["result"]["views"]}

        def img(name, ps):
            f = rfiles.get(name, {}).get(ps)
            return Image.open(f["path"]) if f else None
        t3 = time.perf_counter()
        for rid in rids:
            sheet = C.reference_sheet(bs.photo(rid), bs.matte(rid), masks[rid]["mask"], img(rid, "native"),
                                      img(rid, "clay"), f"{rid} ({metrics[rid]['view_kind']})")
            p = out_dir / f"{rid}.png"
            sheet.convert("RGB").save(p)
            metrics[rid]["sheet"] = str(p)
            metrics[rid]["renders"] = rfiles.get(rid)
        for cr in crop_recs:
            cr["sheet"] = self._crop_sheet(bs, cr, (masks.get(cr["ref"]) or {}).get("mask"), mesh,
                                           img(cr["view"]["name"], "native"), out_dir)
            cr.pop("view")
        secs["sheets"] = round(time.perf_counter() - t3, 3)
        same_bytes = bool(prev) and prev.get("glb_sha256") == exp["sha256"]
        same_geom = bool(prev) and prev.get("geometry") == gkey
        report = {"schema": C.REPORT_SCHEMA, "call_id": call_id, "created": C._utc(), "basis": bs.summary(),
                  "checkpoint": self.head["id"], "fingerprint": exp["fingerprint"],
                  "glb": exp,
                  "geometry": gkey, "skipped_parts": mesh.skipped, "previous": (prev or {}).get("call_id"),
                  "model_bytes_identical": same_bytes if prev else None,
                  "geometry_identical": same_geom if prev else None, "parts": parts, "passes": passes,
                  "refs": metrics, "crops": crop_recs, "engine": rr["result"].get("engine_final"),
                  "fallback": rr["result"].get("fallback"), "gpu": rr["result"].get("gpu"),
                  "definitions": {"all": "inside the frozen ROI (matte box + 15 % + 8 px), fit_views definitions; "
                                         "mm at the frozen front pivot",
                                  "front": "front parts alone vs the matte inside the front render's box + 5 %",
                                  "iou_full_image": "whole image, all parts"}}
        secs["total"] = round(time.perf_counter() - t0, 3)
        report["seconds"] = secs
        rp = out_dir / "report.json"
        C.write_json(rp, report)
        C.append_history(bs, {"call_id": call_id, "created": report["created"], "glb_sha256": exp["sha256"],
                              "geometry": gkey, "checkpoint": self.head["id"], "fingerprint": exp["fingerprint"],
                              "report": str(rp),
                              "refs": {rid: {g: {k: m[g][k] for k in C.METRIC_KEYS if k in m.get(g, {})}
                                             for g in ("all", "front") if g in m} for rid, m in metrics.items()}})
        group = "front" if parts == "front" else "all"
        out = {"status": "ok", "call_id": call_id, "basis": bs.id, "checkpoint": self.head["id"],
               "model_bytes_identical": report["model_bytes_identical"],
               "geometry_identical": report["geometry_identical"], "previous": report["previous"],
               "parts": parts, "refs": {}, "crops": [{k: c[k] for k in ("ref", "box_px", "parts", "sheet")}
                                                     for c in crop_recs],
               "report": str(rp), "export": exp, "engine": report["engine"], "seconds": secs,
               "unusable_refs": bs.unusable_refs()}
        for rid, m in metrics.items():
            g = m.get(group) or m["all"]
            d = (m.get("delta") or {}).get(group)
            out["refs"][rid] = {"view_kind": m["view_kind"], "iou": round(g["iou"], 4),
                                "contour_mean_mm": round(g["contour_mean_mm"], 3),
                                "contour_p90_mm": round(g["contour_p90_mm"], 3),
                                "model_only_px": g["model_only_px"], "photo_only_px": g["photo_only_px"],
                                "iou_full_image": round(m["all"]["iou_full_image"], 4),
                                "model_outside_roi_px": m["all"]["model_outside_roi_px"],
                                "delta": {k: round(v, 4) for k, v in d.items()} if d else None,
                                "sheet": m["sheet"]}
        self.s.record_call("compare_to_photos", call_id, args, out)
        return out

    @staticmethod
    def _ref_ids(bs: C.Basis, refs) -> list[str]:
        """The photos named by ``refs``: None / "all" = every target-colourway photo (all of a WP2 basis), else a
        list of names or 1-based indices ("default" is resolved by ``default_refs``, which needs the model)."""
        if refs in (None, "all"):
            out = [rid for rid in bs.usable_refs() if bs.colourway(rid) == "target"]
            if not out:
                raise SeeingError("E_UNUSABLE_CAMERA", f"{bs.id} has no usable target cameras: {bs.unusable_refs()}")
            return out
        items = [refs] if isinstance(refs, (str, int)) else list(refs)
        names = list(bs.refs)
        out = []
        for it in items:
            rid = (names[int(it) - 1] if isinstance(it, int) and 1 <= int(it) <= len(names) else
                   str(it) if str(it) in bs.refs else C.ref_id(str(it)))
            if rid not in bs.refs:
                raise SeeingError("E_ARGS", f"{bs.id} has no reference {it!r}; references: {names}")
            if not bs.is_usable(rid):
                bs.camera(rid)  # explicit failed requests retain their named diagnostic refusal
            if rid not in out:
                out.append(rid)
        if not out:
            raise SeeingError("E_ARGS", "no references selected")
        return out

    def _crop_view(self, bs: C.Basis, mesh, cr: dict, n: int, origin, passes) -> dict:
        if not isinstance(cr, dict) or "ref" not in cr:
            raise SeeingError("E_ARGS", "each crop is {'ref': 'reference-NN', 'part': name} or {'ref', 'box_px'}")
        rid = self._ref_ids(bs, [cr["ref"]])[0]
        cam, shape = bs.camera(rid), bs.shape(rid)
        if cr.get("box_px") is not None:
            x0, y0, x1, y1 = (int(round(float(v))) for v in cr["box_px"])
            H, W = shape
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 - x0 < 4 or y1 - y0 < 4:
                raise SeeingError("E_ARGS", f"box_px {cr['box_px']} is empty inside the {W} x {H} photo")
            box, matched = (x0, y0, x1, y1), []
        elif cr.get("part"):
            box, matched = C.part_box(mesh, cam, shape, str(cr["part"]))
        else:
            raise SeeingError("E_ARGS", "a crop needs 'part' or 'box_px'")
        cw, ch = box[2] - box[0], box[3] - box[1]
        kc = min(CROP_MAX_UPSCALE, C.PANEL_MAX_PX / max(cw, ch))
        ccam = BC.crop_camera(cam, box[0], box[1], kc)
        cshape = (max(1, round(ch * kc)), max(1, round(cw * kc)))
        view = {"name": f"crop-{n:02d}", "camera": ccam, "shape": cshape,
                "blender": BC.to_blender(ccam, bs.frame, origin, cshape), "worker_passes": [p for p in passes
                                                                                         if p == "native"]}
        return {"ref": rid, "box_px": list(box), "parts": matched, "scale": kc, "view": view}

    @staticmethod
    def _crop_sheet(bs: C.Basis, cr: dict, mask, mesh, native, out_dir: Path) -> str:
        x0, y0, x1, y1 = cr["box_px"]
        rid, kc = cr["ref"], cr["scale"]
        photo = bs.photo(rid)
        if mask is None:
            mask = mesh.scene().render(bs.camera(rid), bs.shape(rid), 1)["mask"]
        ov = C.overlay_image(photo[y0:y1, x0:x1], bs.matte(rid)[y0:y1, x0:x1], mask[y0:y1, x0:x1])
        size = (max(1, round((x1 - x0) * kc)), max(1, round((y1 - y0) * kc)))
        panels = [C.labelled(Image.fromarray(photo[y0:y1, x0:x1]).resize(size, Image.LANCZOS), f"{rid} crop: photo")]
        if native is not None:
            panels.append(C.labelled(C.rgba_on(native, "white"), f"{rid} crop: native"))
        panels.append(C.labelled(Image.fromarray(ov).resize(size, Image.NEAREST), f"{rid} crop: overlay"))
        p = out_dir / f"{cr['view']['name']}-{rid}.png"
        C.grid(panels, len(panels)).convert("RGB").save(p)
        return str(p)

    # ---------------------------------------------------------------------------------------------- photo-matched
    @staticmethod
    def _matched_geometry(bs: C.Basis, rid: str, mesh, roles: dict) -> dict:
        """The posed head at a basis photo, rastered at native size: hit mask, part image, temple mask."""
        pose = bs.pose(rid)
        mp = PO.posed(mesh, pose, roles) if pose else mesh
        cam, shape = bs.camera(rid), bs.shape(rid)
        r = mp.scene().render(cam, shape, 1)
        part = np.full(shape, -1, np.int64)
        part[r["mask"]] = mp.part[r["face_id"][r["mask"]]]
        temple = np.isin(part, PO.temple_parts(mp, 1, roles) + PO.temple_parts(mp, -1, roles))
        return {"pose": pose, "mesh": mp, "cam": cam, "shape": shape, "mask": r["mask"], "part": part,
                "temple": temple, "temple_px": int(temple.sum())}

    @classmethod
    def default_refs(cls, bs: C.Basis, mesh, roles: dict) -> tuple[list, dict]:
        """(the default photos: target colourway with a posed temple ROI >= TEMPLE_ROI_MIN_PX at native size
        (WP6_FACETS §3.3), {photo: matched geometry} of every target photo)."""
        geo = {rid: cls._matched_geometry(bs, rid, mesh, roles) for rid in bs.usable_refs()
               if bs.colourway(rid) == "target"}
        rids = [rid for rid, g in geo.items() if g["temple_px"] >= TEMPLE_ROI_MIN_PX]
        if not rids:
            raise SeeingError("E_ARGS", f"no target photo shows >= {TEMPLE_ROI_MIN_PX} temple pixels; name the photos")
        return rids, geo

    @staticmethod
    def _acetate_ids(mesh, roles) -> tuple[int | None, list]:
        """(front body part, the acetate bodies: the front and the temple bodies, by the one body rule of
        g3d_host/pose (= g3d/render.py acetate_bodies); the exporter gives each region its own glTF material)."""
        fid = PO.front_body(mesh, roles)
        if fid is None:
            return None, []
        return fid, [fid] + [b for b in (PO.temple_body(mesh, s, roles) for s in (1, -1)) if b is not None]

    @staticmethod
    def gain_view_rank(rec: dict) -> float:
        """How a basis photo ranks as its colourway's gain view: its model fit IoU (the record's fit.iou, the model's
        silhouette against the photo's matte, which the gain region needs), or -1 when it has none. A photo without
        one, such as a camera transported from a target photo by image similarity (WP6 v2: black-angled, black-front;
        their transport IoU is photo to photo, and their model_check is information only), is a gain view only when
        its colourway has no photo with a fit IoU, so adding it to a basis changes no gain (black-side stays the black
        gain view)."""
        iou = (rec.get("fit") or {}).get("iou")
        return -1.0 if iou is None else float(iou)

    @staticmethod
    def gain_views(bs: C.Basis, origin) -> tuple[dict, list]:
        """Per colourway its best-registered photo (highest gain_view_rank; ties keep the basis order) at
        <= GAIN_VIEW_MAX_PX: ({colourway: {rid, k, cam, shape, pose}}, worker views rendering both matched passes)."""
        by_cw: dict = {}
        for rid in bs.usable_refs():
            cw = bs.colourway(rid)
            rank = Seeing.gain_view_rank(bs.refs[rid])
            if cw not in by_cw or rank > by_cw[cw][0]:
                by_cw[cw] = (rank, rid)
        geo, views = {}, []
        for cw, (_iou, rid) in by_cw.items():
            cam0, (H, W) = bs.camera(rid), bs.shape(rid)
            k = min(1.0, GAIN_VIEW_MAX_PX / max(H, W))
            cam = BC.crop_camera(cam0, 0, 0, k) if k < 1.0 else cam0
            shape = (max(1, round(H * k)), max(1, round(W * k)))
            geo[cw] = {"rid": rid, "k": k, "cam": cam, "shape": shape, "pose": bs.pose(rid)}
            views.append({"name": f"gain-{rid}", "camera": BC.to_blender(cam, bs.frame, origin, shape),
                          "passes": list(MATCHED_PASSES), "pose": geo[cw]["pose"]})
        return geo, views

    @classmethod
    def gains_from_renders(cls, bs: C.Basis, mesh, roles, geo: dict, files: dict) -> dict:
        """{colourways: {cw: {photo, region, native_linear per-channel gain (white balance), gloss render Y, photo
        luma, photo stretch, dark}}, gloss_black: {gain (luma), stretch, from}} from the gain views' renders
        (WP6_FACETS §3.3)."""
        fid, acetate = cls._acetate_ids(mesh, roles)
        if fid is None:
            raise SeeingError("E_GAIN", "no frame-role front body to take a gain from")
        out = {"colourways": {}}
        for cw, g in geo.items():
            rid = g["rid"]
            mp = PO.posed(mesh, g["pose"], roles) if g["pose"] else mesh
            rr = mp.scene().render(g["cam"], g["shape"], 1)
            part = np.full(g["shape"], -1, np.int64)
            part[rr["mask"]] = mp.part[rr["face_id"][rr["mask"]]]
            photo, matte = cls.photo_at(bs, rid, (0, 0) + tuple(bs.shape(rid)[::-1]), g["k"], g["shape"])
            nat = C.read_linear(files[f"gain-{rid}"]["native_linear"]["path"])
            glo = C.read_linear(files[f"gain-{rid}"]["gloss_black"]["path"])
            region, how = C.gain_region(part == fid, np.isin(part, acetate), matte, nat[1])
            ng = C.linear_gain_rgb(photo, nat[0], region)
            gl = C.linear_gain(photo, glo[0], region)
            L = C.luma8(photo)[region]
            out["colourways"][cw] = {"photo": rid, "region": how, "px": int(region.sum()), "scale_k": g["k"],
                                     "native_linear": ng, "gloss_black_render_Y": gl["render_Y"],
                                     "photo_L8_median": float(np.median(L)),
                                     "photo_stretch": list(C.stretch_range(photo, region)),
                                     "dark": bool(np.median(L) < DARK_L8)}
        dark = [cw for cw, rec in out["colourways"].items() if rec["dark"]]
        if dark:
            rec = out["colourways"][dark[0]]
            out["gloss_black"] = {"gain": rec["native_linear"]["photo_Y"] / rec["gloss_black_render_Y"],
                                  "from": f"the dark {dark[0]} photo {rec['photo']} ({rec['region']})",
                                  "stretch": rec["photo_stretch"], "stretch_from": f"{rec['photo']} acetate p2-p98"}
        else:
            rec = out["colourways"].get("target") or next(iter(out["colourways"].values()))
            y = float(C.srgb_to_linear(GLOSS_TARGET_L8))
            out["gloss_black"] = {"gain": y / rec["gloss_black_render_Y"],
                                  "from": f"front acetate of {rec['photo']} exposed to luma {GLOSS_TARGET_L8:g}",
                                  "stretch": list(GLOSS_FIXED_STRETCH), "stretch_from": "fixed (no dark photo)"}
        return out

    @staticmethod
    def photo_at(bs: C.Basis, rid: str, box, k: float, shape) -> tuple[np.ndarray, np.ndarray]:
        """(photo, matte) of the box (x0, y0, x1, y1) of a basis photo on the pixel grid of its crop camera at k and
        ``shape`` (compare.resample_box: registered exactly with a render made at k, where a resize to round(w k) px
        was up to 0.5 px off at the far edge; bilinear, which never overshoots, where LANCZOS rang beside edges after
        the stretch; the matte bilinear > 0.5). At k 1 on the box's own size: the box itself."""
        x0, y0, x1, y1 = box
        if k == 1.0 and tuple(shape) == (y1 - y0, x1 - x0):
            return bs.photo(rid)[y0:y1, x0:x1], bs.matte(rid)[y0:y1, x0:x1]
        return (C.resample_box(bs.photo(rid), box, k, shape),
                C.resample_box(bs.matte(rid).astype(np.float32), box, k, shape) > 0.5)

    def _render_params(self, rig: str, env: dict | None, light_frame: dict | None) -> dict:
        """The worker parameters a matched rig needs (photo_env: the frozen lobe weights; photo_env and
        catalog_band_v1: unclipped float output)."""
        if rig == ENV_RIG:
            return {"rig": rig, "env": L.env_param(env), "linear_format": "float", "engine": "auto"}
        if rig in FLOAT_RIGS:
            return {"rig": rig, "linear_format": "float", "engine": "auto"}
        return {"rig": rig, "light_frame": light_frame, "engine": "auto"}

    def _matched_gains(self, bs: C.Basis, mesh, roles, rig: str, origin, call_id: str, out_dir: Path,
                       gkey: str, env: dict | None = None) -> dict:
        """The job's frozen gains (one per colourway and pass) and light frame for ``rig`` (and, for photo_env, its
        environment); computed once from the best-registered photo of each colourway (WP6_FACETS §3.3) and stored
        in work/compare/gains-<basis>.json."""
        path = self.work / "compare" / f"gains-{bs.id}.json"
        doc = procs.read_json(path) or {}
        key = rig if rig != ENV_RIG else f"{rig}:{L.weights_sha(env['weights'])}"
        if key in doc:
            return doc[key]
        lf = self._light_frame(call_id)
        geo, views = self.gain_views(bs, origin)
        r = self.s.call_read("compare_to_photos", "render", {"out_dir": str(out_dir / "gain"), "views": views,
                                                             **self._render_params(rig, env, lf)},
                             deadline_s=RENDER_DEADLINE_S, call_id=call_id)
        if r["status"] != "ok":
            raise SeeingError("E_WORKER", f"gain renders failed: {r.get('message') or r['status']}")
        files = {v["name"]: v["files"] for v in r["result"]["views"]}
        out = {"rig": rig, "light_frame": lf, "created": C._utc(), "geometry": gkey, "checkpoint": self.head["id"],
               **({"env_sha": L.weights_sha(env["weights"])} if env else {}),
               **self.gains_from_renders(bs, mesh, roles, geo, files)}
        doc[key] = out
        procs.write_json_atomic(path, doc)
        return out

    # ---------------------------------------------------------------------------------------------- photo_env
    def env_calibration(self, bs: C.Basis, mesh, roles) -> list[dict]:
        """The photos the environment is fitted on: target colourway, not side views (the front faces the camera),
        each with its front acetate region (front body inside the matte, eroded by max(12 px, 2 x the front's
        registration error in px) at native scale) and a crop of it at <= ENV_FIT_MAX_PX."""
        fid = PO.front_body(mesh, roles)
        if fid is None:
            raise SeeingError("E_ENV", "no frame-role front body to fit the environment on")
        out = []
        for rid in bs.usable_refs():
            if bs.colourway(rid) != "target" or (bs.refs[rid].get("view_kind") or "") == "side":
                continue
            g = self._matched_geometry(bs, rid, mesh, roles)
            front = g["part"] == fid
            m, _masks = C.reference_metrics(g["mesh"], bs, rid)
            reg_mm = float((m.get("front") or m["all"])["contour_mean_mm"])
            pts = g["mesh"].V[np.unique(g["mesh"].F[g["mesh"].part == fid])]
            ppm = float(CAMS.px_per_mm_at(g["cam"], bs.frame, np.median(pts, axis=0)))
            erode = max(ENV_ERODE_PX, 2.0 * reg_mm * ppm)
            box = C._box(front, 0.04, g["shape"])
            if box is None or C.erode_px(front & bs.matte(rid), erode).sum() < C.GAIN_MIN_PX:
                continue
            x0, y0, x1, y1 = box
            k = min(1.0, ENV_FIT_MAX_PX / max(x1 - x0, y1 - y0))
            ccam = BC.crop_camera(g["cam"], x0, y0, k)
            cshape = (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))
            out.append({"rid": rid, "g": g, "box": box, "k": k, "ccam": ccam, "cshape": cshape, "ppm": ppm,
                        "registration_mm": reg_mm, "erode_native_px": erode, "fid": fid})
        if not out:
            raise SeeingError("E_ENV", "no target photo shows the front well enough to fit the environment")
        return out

    def _env_rows(self, bs: C.Basis, cal: dict, base_alpha: np.ndarray, chroma_rgb) -> dict:
        """One calibration photo's fit cells: the front region at crop scale and the balanced photo luminance."""
        g, k, cshape = cal["g"], cal["k"], cal["cshape"]
        x0, y0, x1, y1 = cal["box"]
        import cv2
        lin = C.srgb_to_linear(bs.photo(cal["rid"])[y0:y1, x0:x1, :3])
        lin = cv2.resize(lin.astype(np.float32), (cshape[1], cshape[0]), interpolation=cv2.INTER_AREA)
        mc = np.asarray(Image.fromarray(bs.matte(cal["rid"])[y0:y1, x0:x1].astype(np.float32)).resize(
            (cshape[1], cshape[0]), Image.BILINEAR)) > 0.5
        r = g["mesh"].scene().render(cal["ccam"], cshape, 1)
        part = np.full(cshape, -1, np.int64)
        part[r["mask"]] = g["mesh"].part[r["face_id"][r["mask"]]]
        region = C.erode_px((part == cal["fid"]) & mc & (base_alpha > 0.999), cal["erode_native_px"] * k)
        ppm_c = cal["ppm"] * k
        pts, (y,) = L.cell_samples([L.balanced_luminance(lin, chroma_rgb)], region, ENV_SIGMA_MM * ppm_c,
                                   ENV_STEP_MM * ppm_c)
        return {"region": region, "pts": pts, "y": y, "lin": lin, "sigma_px": ENV_SIGMA_MM * ppm_c,
                "step_px": ENV_STEP_MM * ppm_c}

    def _env_render(self, call_id: str, cals: list, envs: dict, origin, out_dir: Path, tag: str) -> dict:
        """Render native_linear (float) at every calibration crop for every named weight vector:
        {(rid, name): (rgb, alpha)}."""
        views = []
        for c in cals:
            cam = BC.to_blender(c["ccam"], c["bs_frame"], origin, c["cshape"])
            for name, w in envs.items():
                views.append({"name": f"{c['rid']}--{name}", "camera": cam, "passes": ["native_linear"],
                              "pose": c["g"]["pose"], "env": {"weights": [float(x) for x in w]}})
        r = self.s.call_read("compare_to_photos", "render", {
            "out_dir": str(out_dir / tag), "views": views, "rig": ENV_RIG, "linear_format": "float",
            "engine": "auto"}, deadline_s=ENV_DEADLINE_S, call_id=call_id)
        if r["status"] != "ok":
            raise SeeingError("E_WORKER", f"environment renders failed: {r.get('message') or r['status']}")
        out = {}
        for v in r["result"]["views"]:
            rid, name = v["name"].split("--", 1)
            out[(rid, name)] = C.read_linear(v["files"]["native_linear"]["path"])
        self._env_engine = r["result"].get("engine_final")
        return out

    def _env_fit(self, bs: C.Basis, mesh, roles, origin, call_id: str, out_dir: Path, exp: dict,
                 gkey: str) -> dict:
        """Fit the job's camera-relative environment (g3d_host/lighting.py) on the calibration photos' front acetate:
        white balance from a uniform world, then ENV_ITERATIONS re-linearised NNLS steps with a differential basis,
        each iterate rendered directly; the iterate with the lowest direct residual is kept."""
        t0 = time.perf_counter()
        cals = self.env_calibration(bs, mesh, roles)
        for c in cals:
            c["bs_frame"] = bs.frame
        n = L.N_LOBES
        uni = np.ones(n)
        base = self._env_render(call_id, cals, {"u": uni}, origin, out_dir, "uniform")
        wb_cal = max(cals, key=lambda c: float((bs.refs[c["rid"]].get("fit") or {}).get("iou") or 0.0))
        rgb_u, a_u = base[(wb_cal["rid"], "u")]
        rows_wb = self._env_rows(bs, wb_cal, a_u, np.ones(3))
        p8 = C.linear_to_srgb8(rows_wb["lin"])
        wb0 = C.linear_gain_rgb(p8, rgb_u, rows_wb["region"])
        chroma_rgb = L.chroma(wb0["gain"])
        level = float(wb0["gain_Y"])                      # homogeneous: f(level * u) = level * f(u)
        rows = {c["rid"]: self._env_rows(bs, c, base[(c["rid"], "u")][1], chroma_rgb) for c in cals}
        groups = np.concatenate([[c["rid"]] * len(rows[c["rid"]]["y"]) for c in cals])
        y = np.concatenate([rows[c["rid"]]["y"] for c in cals])

        def cells(img_by_rid):
            return np.concatenate([L.cell_samples([img_by_rid[c["rid"]][0] @ L.LUMA], rows[c["rid"]]["region"],
                                                  rows[c["rid"]]["sigma_px"], rows[c["rid"]]["step_px"])[1][0]
                                   for c in cals])
        w0 = level * uni
        f0 = level * cells({c["rid"]: base[(c["rid"], "u")] for c in cals})
        iterations, systems, lam, cv_table = [], [], None, []
        for it in range(ENV_ITERATIONS):
            delta = ENV_DELTA * float(np.mean(w0))
            envs = {f"L{i:02d}": w0 + delta * np.eye(n)[i] for i in range(n)}
            rr = self._env_render(call_id, cals, envs, origin, out_dir, f"basis-{it}")
            B = np.column_stack([(cells({c["rid"]: rr[(c["rid"], f"L{i:02d}")] for c in cals}) - f0) / delta
                                 for i in range(n)])
            rhs = L.gauss_newton_rhs(y, f0, B, w0)
            lam_it, cv_it = L.cv_lambda(B, rhs, groups)        # this system's own choice (information after it 0)
            if lam is None:
                lam, cv_table = lam_it, cv_it
            fit = L.fit_env(B, rhs, groups, lam)
            w1 = np.asarray(fit["weights"])
            direct = self._env_render(call_id, cals, {"w": w1}, origin, out_dir, f"direct-{it}")
            f1 = cells({c["rid"]: direct[(c["rid"], "w")] for c in cals})
            res = L.residual_stats(f1, y, groups)
            iterations.append({"iteration": it, "linearised_at_sha": L.weights_sha(w0), "delta": delta,
                               "lambda_used": lam, "cv_lambda": lam_it,
                               "cv_rel_rms": {str(t["lambda"]): t["cv_rel_rms"] for t in cv_it},
                               "weights": fit["weights"], "model_residuals": fit["residuals"],
                               "direct_residuals": res,
                               "direct_mean_rel_rms": float(np.mean([v["rel_rms"] for v in res.values()]))})
            systems.append({"B": B, "rhs": rhs, "w0": w0, "f_direct": f1})
            w0, f0 = w1, f1
        best = min(iterations, key=lambda r: r["direct_mean_rel_rms"])
        sysd = systems[best["iteration"]]
        with open(out_dir / "system.npz", "wb") as fh:             # the chosen linearised fit, for diagnostics
            np.savez_compressed(fh, B=sysd["B"], rhs=sysd["rhs"], y=y, groups=groups, w0=sysd["w0"],
                                f_direct=sysd["f_direct"], weights=np.asarray(best["weights"]), lam=lam)
        return {"schema": L.SCHEMA, "rig": ENV_RIG, "created": C._utc(), "basis": bs.id,
                "lobes": L.LOBES, "spec": {"elevation_knots_deg": L.ELEVATION_KNOTS_DEG, "sectors": L.SECTORS,
                                          "floor_deg": L.FLOOR_DEG},
                "weights": best["weights"], "weights_sha": L.weights_sha(best["weights"]),
                "chosen_iteration": best["iteration"], "lambda": lam, "lambda_grid": list(L.LAMBDA_GRID),
                "lambda_rule": "chosen once by leave-one-photo-out CV on the iteration-0 system and kept by every "
                               "later iteration; each iteration's own CV choice is recorded as cv_lambda "
                               "(information, not used)",
                "cv": cv_table, "iterations": iterations, "level": level, "white_balance_uniform": wb0,
                "chroma": [float(v) for v in chroma_rgb],
                "calibration": [{"photo": c["rid"], "photo_sha256": bs.refs[c["rid"]]["photo_sha256"],
                                 "box": list(c["box"]), "k": c["k"], "rows": int(len(rows[c["rid"]]["y"])),
                                 "region_px": int(rows[c["rid"]]["region"].sum()),
                                 "front_registration_mm": c["registration_mm"],
                                 "erode_native_px": c["erode_native_px"], "px_per_mm_native": c["ppm"]}
                                for c in cals],
                "fit_on": "front acetate body only (temples never)", "sigma_mm": ENV_SIGMA_MM, "step_mm": ENV_STEP_MM,
                "system": str(out_dir / "system.npz"),
                "engine": getattr(self, "_env_engine", None), "model": {"glb": exp.get("path"),
                                                                       "glb_sha256": exp.get("sha256"),
                                                                       "geometry": gkey,
                                                                       "checkpoint": self.head["id"]},
                "seconds": round(time.perf_counter() - t0, 1)}

    def photo_env(self, basis: str | None = None, call_id: str | None = None, *, refit: bool = False) -> dict:
        """The job's frozen photo-estimated environment (work/compare/env-<basis>.json), fitted on first use."""
        call_id = self.s.new_call_id("photo_env", call_id)
        args = {"basis": basis, "refit": refit}
        try:
            bs = C.load_basis(self.root, basis)
            out = self._env_for(bs, call_id, refit=refit)
        except C.NoBasis as exc:
            out = _err(call_id, "E_NO_BASIS", str(exc))
        except (SeeingError, ValueError, OSError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_ENV"), str(exc))
        else:
            out = {"status": "ok", "call_id": call_id, **{k: out[k] for k in ("weights", "weights_sha", "lambda",
                                                                              "chosen_iteration", "path")}}
        self.s.record_call("photo_env", call_id, args, out)
        return out

    def _env_for(self, bs: C.Basis, call_id: str, *, refit: bool = False, exp: dict | None = None) -> dict:
        path = self.work / "compare" / f"env-{bs.id}.json"
        rec = procs.read_json(path)
        if rec and not refit:
            return {**rec, "path": str(path), "cached": True}
        exp = exp or self.export_head(call_id)
        if exp.get("status") != "exported":
            raise SeeingError(exp.get("code") or "E_EXPORT", f"the scene does not export: {exp.get('message')}")
        mesh = self.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
        roles = PO.part_roles(exp["path"])
        origin = np.asarray(self.bounds(call_id)["origin_mm"] or (0.0, 0.0, 0.0), float)
        out_dir = self._prepare_work_dir(self.work / "compare" / f"env-{bs.id}-fit")
        rec = self._env_fit(bs, mesh, roles, origin, call_id, out_dir, exp, C.geometry_key(mesh))
        img = L.env_image(rec["weights"])
        prev = C.linear_to_srgb8(img / max(1e-6, float(img.max())))
        Image.fromarray(np.repeat(prev[..., None], 3, -1)).resize((720, 360), Image.NEAREST).save(
            out_dir / "env_preview.png")
        rec["preview"] = str(out_dir / "env_preview.png")
        procs.write_json_atomic(path, rec)
        return {**rec, "path": str(path), "cached": False}

    # ---------------------------------------------------------------------------------------------- start model
    def _start_offsets(self, bs: C.Basis, rid: str, exp: dict, gkey: str, hinge_z: float) -> dict | None:
        """The outer-edge offsets of the job's START model (the first model this job compared on the basis, its
        export recorded in work/compare/start-<basis>.json) at photo ``rid``, cached there; None for photos without
        offsets."""
        path = self.work / "compare" / f"start-{bs.id}.json"
        doc = procs.read_json(path)
        if not doc:
            doc = {"glb": exp["path"], "sha256": exp["sha256"], "geometry": gkey, "checkpoint": self.head["id"],
                   "created": C._utc(), "offsets": {}}
            procs.write_json_atomic(path, doc)
        if (bs.refs[rid].get("view_kind") or "") not in OFFSET_VIEWS:
            return None
        key = f"{rid}@{hinge_z:.4f}"
        if key not in doc["offsets"]:
            if not Path(doc["glb"]).is_file() or C.sha256_file(doc["glb"]) != doc["sha256"]:
                return {"missing": "the start model's export is gone"}
            mesh = self.fit_mesh(doc["glb"], doc["sha256"], skip=bs.skip, frame=bs.frame)
            roles = PO.part_roles(doc["glb"])
            g = self._matched_geometry(bs, rid, mesh, roles)
            bodies = {s: PO.temple_body(mesh, s, roles) for s in (1, -1)}
            doc["offsets"][key] = self._outer_offsets(bs, rid, g, mesh, bodies, hinge_z)
            procs.write_json_atomic(path, doc)
        return doc["offsets"][key]

    # ---------------------------------------------------------------------------------------------- compare
    def _check_points(self, bs: C.Basis, rids: list, points_px: dict) -> dict:
        """points_px = {photo: [[u, v], ...]} in native photo pixels, for photos of this compare only."""
        out = {}
        for rid, pts in (points_px or {}).items():
            rid = C.ref_id(str(rid))
            if rid not in rids:
                raise SeeingError("E_ARGS", f"points_px names {rid!r}, which this compare does not show ({rids})")
            try:
                uv = np.asarray(pts, float)
            except (TypeError, ValueError):
                raise SeeingError("E_ARGS", f"points_px[{rid}] must be numbers [[u, v], ...]") from None
            H, W = bs.shape(rid)
            if uv.ndim != 2 or uv.shape[1] != 2 or not 1 <= len(uv) <= PICK_MAX or not np.isfinite(uv).all():
                raise SeeingError("E_ARGS", f"points_px[{rid}] must be 1..{PICK_MAX} native pixels [u, v]")
            if (uv < -0.5).any() or (uv[:, 0] > W - 0.5).any() or (uv[:, 1] > H - 0.5).any():
                raise SeeingError("E_ARGS", f"points_px[{rid}] must lie inside the native {W} x {H} photo")
            out[rid] = uv.tolist()
        return out

    def _matched_compare(self, refs, passes, crop, basis, call_id, *, rig, points_px, hinge_z_mm, gains_override,
                         boxes=None, use_cache=True, env_override=None, render_deadline_s=None, zoom=None,
                         model_edges=None, opening=None, photo_names=None) -> dict:
        """The photo-matched compare (module doc). ``gains`` / ``env`` override the job's frozen gains /
        environment (one normalisation across jobs, e.g. an owner sheet); ``boxes`` = {photo: {"box": [x0, y0, x1,
        y1], "k": scale, "stretch": [lo, hi] (optional)}} fixes crops (and stretches) so several models share
        them. ``zoom`` / ``model_edges`` / ``opening`` / ``photo_names``: WP6 v3 (module doc), checked by
        ``_check_v3_args``."""
        args = {"refs": refs, "passes": list(passes or ()), "crop": crop, "basis": basis, "rig": rig,
                "points_px": points_px, "hinge_z_mm": hinge_z_mm, "gains": "override" if gains_override else None,
                "env": "override" if env_override else None, "boxes": boxes, "zoom": zoom, "opening": opening,
                "photo_names": photo_names,
                "model_edges": None if model_edges is None else {
                    "edges": len(model_edges) if isinstance(model_edges, list) else None,
                    "sha256": hashlib.sha256(json.dumps(model_edges, sort_keys=True,
                                                        default=str).encode()).hexdigest()}}
        t0 = time.perf_counter()
        secs = {}
        try:
            bs = C.load_basis(self.root, basis)
            if rig not in MATCHED_RIGS:
                raise SeeingError("E_ARGS", f"rig must be photo_v1 or one of {MATCHED_RIGS}")
            passes = [p for p in MATCHED_PASSES if p in (passes or ())] or ["native_linear"]
            if "native_linear" not in passes:
                passes.insert(0, "native_linear")
            if crop not in ("temple", "full"):
                raise SeeingError("E_ARGS", "crop must be 'temple' or 'full'")
            if points_px is not None and not isinstance(points_px, dict):
                raise SeeingError("E_ARGS", "points_px must be {photo: [[u, v], ...]}")
            for rid in bs.usable_refs():
                if bs.pose(rid) is None:
                    raise SeeingError("E_NO_POSE", f"{bs.id} has no temple pose for {rid}; the photo-matched compare "
                                                   "needs a WP6 basis (theta_deg, hinge_axis_xz_mm)")
            self._check_matched_args(rig, hinge_z_mm, gains_override, boxes, env_override)
            v3 = self._check_v3_args(bs, zoom, model_edges, opening, hinge_z_mm, photo_names)
            if v3["zoom"] and isinstance(refs, (list, tuple)) and not len(refs):
                want = []                                 # zoom images only
            else:
                want = "default" if refs in (None, "default") else self._ref_ids(bs, refs)
            out_dir = self._prepare_work_dir(self.work / "compare" / call_id)
        except C.NoBasis as exc:
            out = _err(call_id, "E_NO_BASIS", str(exc))
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        except (SeeingError, ValueError, OSError, TypeError, KeyError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_ARGS"), str(exc))
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        try:
            exp = self.export_head(call_id)
            if exp.get("status") != "exported":
                out = _err(call_id, exp.get("code") or "E_EXPORT",
                           f"the scene does not export, so it cannot be compared: {exp.get('message')}")
            else:
                out = self._matched_body(bs, exp, want, passes, crop, rig, points_px, hinge_z_mm, gains_override,
                                         boxes, use_cache, env_override, call_id, out_dir, t0, secs,
                                         render_deadline_s=render_deadline_s, v3=v3)
        except SeeingError as exc:
            out = _err(call_id, exc.code, str(exc))
        except (WorkerFailed, CheckpointMismatch, WorkerDied):
            raise
        except Exception as exc:  # noqa: BLE001 - every failure after the arguments is a journaled E_COMPARE
            out = _err(call_id, "E_COMPARE", f"{type(exc).__name__}: {exc}"[:2000])
        self.s.record_call("compare_to_photos", call_id, args, out)
        return out

    @staticmethod
    def _check_matched_args(rig: str, hinge_z_mm, gains, boxes, env) -> None:
        """Refuse malformed hinge_z_mm / gains / boxes / env overrides up front (E_ARGS); numbers may be any real
        type (numpy floats too), never bool."""
        def num(x) -> bool:
            return isinstance(x, numbers.Real) and not isinstance(x, bool) and math.isfinite(x)
        if hinge_z_mm is not None and not num(hinge_z_mm):
            raise SeeingError("E_ARGS", "hinge_z_mm must be a finite number (the spec's hinge.z_mm) or None")
        if gains is not None:
            try:
                g = gains["colourways"]["target"]["native_linear"]["gain"]
                ok = all(num(v) and v > 0 for v in np.atleast_1d(g).tolist()) and num(gains["gloss_black"]["gain"])
                ok = ok and len(gains["gloss_black"]["stretch"]) == 2
            except (KeyError, TypeError, IndexError):
                ok = False
            if not ok:
                raise SeeingError("E_ARGS", "gains must be a job's frozen gains record (colourways.target.native_linear"
                                            ".gain, gloss_black.gain and .stretch)")
        if boxes is not None:
            if not isinstance(boxes, dict):
                raise SeeingError("E_ARGS", "boxes must be {photo: {box: [x0, y0, x1, y1], k, stretch?}}")
            for rid, b in boxes.items():
                try:
                    box, k = [float(v) for v in b["box"]], b["k"]
                    st = b.get("stretch")
                    ok = len(box) == 4 and all(map(num, box)) and box[2] > box[0] and box[3] > box[1] and num(k) \
                        and 0 < k <= 1 and (st is None or (len(st) == 2 and all(map(num, st)) and st[1] > st[0]))
                except (KeyError, TypeError, ValueError, AttributeError):
                    ok = False
                if not ok:
                    raise SeeingError("E_ARGS", f"boxes[{rid!r}] must be {{box: [x0, y0, x1, y1], k in (0, 1], "
                                                "stretch: [lo, hi] (optional)}")
        if env is not None:
            if rig != ENV_RIG:
                raise SeeingError("E_ARGS", f"env is the {ENV_RIG} environment; rig {rig!r} takes none")
            try:
                w = list(env["weights"])
                ok = len(w) == L.N_LOBES and all(num(v) and v >= 0 for v in w)
            except (KeyError, TypeError):
                ok = False
            if not ok:
                raise SeeingError("E_ARGS", f"env must be {{weights: {L.N_LOBES} non-negative numbers}}")

    @staticmethod
    def _check_v3_args(bs: C.Basis, zoom, model_edges, opening, hinge_z_mm, names=None) -> dict:
        """The WP6 v3 arguments checked and normalised (E_ARGS otherwise): {"zoom": [{rid, box (x0, y0, x1, y1) int,
        clipped to the photo, >= ZOOM_MIN_PX a side), geometry}], "edges": [{pair, shape, deg, pts (K, 3), deg_at
        (K,) | None, range (lo, hi) | None, soft bool}] | None, "edges_sha256", "opening": (deg, (|x|, z)) | None,
        "names": {rid: name}} (``names`` = photo_names: each key a photo of the basis, each name NAME_RE; the
        titles and texts of the photos it leaves out keep the basis name).
        Numbers may be any real type (numpy floats too), never bool. ``deg`` is unsigned with its shape, or signed
        (+ ridge, - valley), so a ridge with a negative deg contradicts itself; ``deg_at`` (optional) is the signed
        angle at each point, aligned with ``pts`` (along a warp face's crease the angle changes and may change sign);
        ``range`` (optional, WP6 v3 I1) the signed [lo, hi] over the edge's own polygon edges; with it ``deg`` is
        signed (I1: the length-weighted signed angle; the shape is the crease's vote, which a crease that changes
        sign along a warp face may contradict, so no shape/sign check then), and deg and every deg_at must lie in the
        range within EDGE_RANGE_TOL_DEG, else the edge mixes in other edges' angles (probe1: a mean with the roll
        sub-bands beside a face-to-face range, "36.9 (37.7-39.3)"; a label then shows a number no face pair has);
        ``soft`` (optional bool, I7) marks a soft facet|base border
        (pair "facet|base": WP6_V3 §2, creases between two facets are never soft). The edges' sha256 is over their
        canonical JSON (the cache key's; deg_at and range only when given, soft only when true, so an edge set
        without them keeps its key)."""
        def num(x) -> bool:
            return isinstance(x, numbers.Real) and not isinstance(x, bool) and math.isfinite(x)
        out = {"zoom": [], "edges": None, "edges_sha256": None, "opening": None, "names": {}}
        if names is not None:
            if not isinstance(names, dict):
                raise SeeingError("E_ARGS", "photo_names must be {photo: name}")
            for rid, nm in names.items():
                if not isinstance(rid, str) or C.ref_id(rid) not in bs.refs:
                    raise SeeingError("E_ARGS", f"photo_names names {rid!r}, not a photo of {bs.id} "
                                                f"({', '.join(bs.refs)})")
                if not isinstance(nm, str) or not NAME_RE.match(nm):
                    raise SeeingError("E_ARGS", f"photo_names[{rid!r}] must be 1-60 characters of letters, digits, "
                                                "'_', '.' and '-'")
                out["names"][C.ref_id(rid)] = nm
        if zoom is not None:
            if not isinstance(zoom, list) or len(zoom) > ZOOM_MAX:
                raise SeeingError("E_ARGS", f"zoom must be a list of <= {ZOOM_MAX} {{rid, box, geometry}}")
            for i, z in enumerate(zoom):
                try:
                    rid = C.ref_id(str(z["rid"]))
                    box = [float(v) for v in z["box"]]
                    geom = z.get("geometry", False)
                    ok = rid in bs.refs and len(box) == 4 and all(map(num, box)) and isinstance(geom, (bool, np.bool_))
                except (KeyError, TypeError, ValueError, AttributeError):
                    ok = False
                if not ok:
                    raise SeeingError("E_ARGS", f"zoom[{i}] must be {{rid: one of {list(bs.refs)}, box: [x0, y0, x1, "
                                                "y1] native px, geometry: true | false}")
                if not bs.is_usable(rid):
                    bs.camera(rid)
                H, W = bs.shape(rid)
                x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
                x1, y1 = min(W, math.ceil(box[2])), min(H, math.ceil(box[3]))
                if x1 - x0 < ZOOM_MIN_PX or y1 - y0 < ZOOM_MIN_PX:
                    raise SeeingError("E_ARGS", f"zoom[{i}] box {z['box']} keeps less than {ZOOM_MIN_PX} x "
                                                f"{ZOOM_MIN_PX} px inside the {W} x {H} photo {rid}")
                out["zoom"].append({"rid": rid, "box": (x0, y0, x1, y1), "geometry": bool(geom)})
        if model_edges is not None:
            if hinge_z_mm is None:
                raise SeeingError("E_ARGS", "model_edges need hinge_z_mm (the spec's hinge.z_mm: d = hinge_z - z)")
            if not isinstance(model_edges, list) or len(model_edges) > MODEL_EDGES_MAX:
                raise SeeingError("E_ARGS", f"model_edges must be a list of <= {MODEL_EDGES_MAX} edges")
            edges = []
            for i, e in enumerate(model_edges):
                try:
                    pts = np.asarray(e["pts"], float)
                    at = None if e.get("deg_at") is None else np.asarray(e["deg_at"], float)
                    rng = None if e.get("range") is None else [float(v) for v in e["range"]] \
                        if all(map(num, e["range"])) else []
                    soft = e.get("soft", False)
                    ok = isinstance(e["pair"], str) and 0 < len(e["pair"]) <= 80 and e["shape"] in C.EDGE_SHAPES \
                        and num(e["deg"]) and pts.ndim == 2 and pts.shape[1] == 3 \
                        and 2 <= len(pts) <= EDGE_POINTS_MAX and np.isfinite(pts).all() \
                        and (at is None or (at.shape == (len(pts),) and np.isfinite(at).all())) \
                        and (rng is None or (len(rng) == 2 and rng[0] <= rng[1])) \
                        and isinstance(soft, (bool, np.bool_))
                except (KeyError, TypeError, ValueError, AttributeError):
                    ok = False
                if not ok:
                    raise SeeingError("E_ARGS", f"model_edges[{i}] must be {{pair: 'a|b', shape: one of "
                                                f"{list(C.EDGE_SHAPES)}, deg: number, pts: 2..{EDGE_POINTS_MAX} "
                                                "[d, y, X] in the +X temple's chart, deg_at (optional): a signed "
                                                "angle per point, range (optional): [lo, hi] signed, lo <= hi, soft "
                                                "(optional): true | false}")
                if rng is None and e["shape"] == "ridge" and float(e["deg"]) < 0:
                    raise SeeingError("E_ARGS", f"model_edges[{i}] {e['pair']!r} is shape ridge with deg "
                                                f"{float(e['deg']):g}: deg is unsigned with its shape, or signed "
                                                "(+ ridge, - valley)")
                if soft and not e["pair"].endswith("|base"):
                    raise SeeingError("E_ARGS", f"model_edges[{i}] {e['pair']!r} is soft: only a facet's border with "
                                                "the base can be soft (pair 'facet|base'); an edge between two facets "
                                                "never is")
                if rng is not None:                 # I1: deg is signed (+ ridge) whatever the shape's vote
                    lo, hi = rng[0] - C.EDGE_RANGE_TOL_DEG, rng[1] + C.EDGE_RANGE_TOL_DEG
                    sd = float(e["deg"])
                    out_at = [] if at is None else np.flatnonzero((at < lo) | (at > hi)).tolist()
                    if not lo <= sd <= hi or out_at:
                        what = f"deg {sd:+.2f}" if not lo <= sd <= hi else \
                            f"deg_at[{out_at[0]}] {at[out_at[0]]:+.2f} ({len(out_at)} of {len(at)} points)"
                        raise SeeingError("E_ARGS", f"model_edges[{i}] {e['pair']!r}: {what} lies outside its own "
                                                    f"range {rng[0]:+.2f}..{rng[1]:+.2f} (signed, + ridge): an edge's "
                                                    "deg, deg_at and range come from the same polygon edges")
                edges.append({"pair": e["pair"], "shape": e["shape"], "deg": float(e["deg"]), "pts": pts,
                              "deg_at": at, "range": None if rng is None else tuple(rng), "soft": bool(soft)})
            out["edges"] = edges
            out["edges_sha256"] = hashlib.sha256(json.dumps(
                [{"pair": e["pair"], "shape": e["shape"], "deg": e["deg"], "pts": e["pts"].tolist(),
                  **({} if e["deg_at"] is None else {"deg_at": e["deg_at"].tolist()}),
                  **({} if e["range"] is None else {"range": list(e["range"])}),
                  **({"soft": True} if e["soft"] else {})} for e in edges],
                sort_keys=True).encode()).hexdigest()
        if opening is not None:
            try:
                if isinstance(opening, dict):
                    deg = float(opening.get("opening_deg", opening.get("deg")))
                    ax = [float(v) for v in opening["axis_xz_mm"]] if deg else [0.0, 0.0]
                else:
                    deg, ax = float(opening[0]), [float(v) for v in opening[1]]
                ok = math.isfinite(deg) and len(ax) == 2 and all(map(math.isfinite, ax)) and (not deg or ax[0] != 0)
            except (KeyError, TypeError, ValueError, IndexError):
                ok = False
            if not ok:
                raise SeeingError("E_ARGS", "opening must be the spec's hinge {opening_deg, axis_xz_mm} or (deg, "
                                            "[x, z]) with a hinge axis off x = 0")
            out["opening"] = (deg, (abs(ax[0]), ax[1])) if deg else None
        return out

    def _matched_body(self, bs, exp, want, passes, crop, rig, points_px, hinge_z_mm, gains_override, boxes,
                      use_cache, env_override, call_id, out_dir, t0, secs, *, render_deadline_s=None,
                      v3: dict | None = None) -> dict:
        v3 = v3 or {"zoom": [], "edges": None, "edges_sha256": None, "opening": None, "names": {}}
        names = v3.get("names") or {}
        mesh = self.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
        roles = PO.part_roles(exp["path"])
        gkey = C.geometry_key(mesh)
        origin = np.asarray(self.bounds(call_id)["origin_mm"] or (0.0, 0.0, 0.0), float)
        bodies = {s: PO.temple_body(mesh, s, roles) for s in (1, -1)}
        if hinge_z_mm is None:
            zs = [float(mesh.V[np.unique(mesh.F[mesh.part == b])][:, 2].max()) for b in bodies.values()
                  if b is not None]
            hinge_z_mm = float(np.mean(zs)) if zs else 0.0
        hinge_z_mm = float(hinge_z_mm)
        on_model = None
        if v3["edges"] is not None:
            on_model = self._check_edges_on_model(v3["edges"], mesh, bodies.get(1), hinge_z_mm, v3["opening"])
        t1 = time.perf_counter()
        if want == "default":
            rids, geo = self.default_refs(bs, mesh, roles)
        else:
            rids, geo = want, {rid: self._matched_geometry(bs, rid, mesh, roles) for rid in want}
        for z in v3["zoom"]:
            if z["rid"] not in geo:
                geo[z["rid"]] = self._matched_geometry(bs, z["rid"], mesh, roles)
        points_px = self._check_points(bs, rids, points_px)
        secs["geometry"] = round(time.perf_counter() - t1, 3)
        t1 = time.perf_counter()
        env = None
        if rig == ENV_RIG:
            env = env_override or self._env_for(bs, call_id, exp=exp)
        secs["environment"] = round(time.perf_counter() - t1, 3)
        t1 = time.perf_counter()
        gains = gains_override or self._matched_gains(bs, mesh, roles, rig, origin, call_id, out_dir, gkey, env)
        secs["gains"] = round(time.perf_counter() - t1, 3)
        fid, acetate = self._acetate_ids(mesh, roles)
        # the opening sits in the key with or without model edges: the side outline places its stations with it
        common = {"schema": MATCHED_SCHEMA, "geometry": gkey, "rig": rig, "hinge_z": round(hinge_z_mm, 6),
                  "frame": bs.frame.to_dict(), "gains": gains.get("colourways"), "gloss": gains.get("gloss_black"),
                  "light": gains.get("light_frame"), "env": None if env is None else L.weights_sha(env["weights"]),
                  "opening": v3["opening"], "edges": v3["edges_sha256"]}
        recs, todo, crops = {}, [], {}
        for rid in rids:
            g = geo[rid]
            cr = crops[rid] = self._photo_crop(bs, rid, g, passes, crop, boxes)
            box, k, vpasses = cr["box"], cr["k"], cr["passes"]
            x0, y0, x1, y1 = box
            key = self._cache_key({**common, "rid": rid, "passes": vpasses, "crop": crop, "box": box, "k": k,
                                   "stretch": cr["stretch"], "pose": g["pose"], "camera": bs.refs[rid]["camera"],
                                   "name": names.get(rid, rid)})
            cdir = self.work / "compare" / "cache" / key
            rec = procs.read_json(cdir / "record.json") if use_cache else None
            if rec and Path(rec["image"]).is_file():
                rec["cached"] = True
                recs[rid] = rec
                continue
            ccam = BC.crop_camera(g["cam"], x0, y0, k)
            cshape = (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))
            g.update(box=box, k=k, ccam=ccam, cshape=cshape, cdir=cdir, key=key, passes=vpasses,
                     stretch=cr["stretch"])
            todo.append(rid)
        zooms, ztodo = [], []
        for n, z in enumerate(v3["zoom"], 1):
            g = geo[z["rid"]]
            cr = crops.get(z["rid"]) or self._photo_crop(bs, z["rid"], g, passes, crop, boxes)
            x0, y0, x1, y1 = z["box"]
            k = C.zoom_scale(x1 - x0, y1 - y0)
            zp = ["native_linear" if bs.colourway(z["rid"]) == "target" else "gloss_black"] + \
                (["normal"] if z["geometry"] else [])
            key = self._cache_key({**common, "zoom": True, "rid": z["rid"], "passes": zp, "box": z["box"], "k": k,
                                   "stretch_of": {"crop": crop, "box": cr["box"], "k": cr["k"],
                                                  "stretch": cr["stretch"]},
                                   "pose": g["pose"], "camera": bs.refs[z["rid"]]["camera"],
                                   "name": names.get(z["rid"], z["rid"])})
            zs = {**z, "n": n, "k": k, "passes": zp, "key": key, "cdir": self.work / "compare" / "cache" / key,
                  "name": names.get(z["rid"], z["rid"]),
                  "cam": BC.crop_camera(g["cam"], x0, y0, k), "crop": cr,
                  "shape": (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k))), "view": f"zoom{n}-{z['rid']}"}
            zooms.append(zs)
            rec = procs.read_json(zs["cdir"] / "record.json") if use_cache else None
            if rec and Path(rec["image"]).is_file():
                zs["rec"] = {**rec, "cached": True}
            else:
                ztodo.append(zs)
        t2 = time.perf_counter()
        files, render_meta = {}, None
        if todo or ztodo:
            views = [{"name": rid, "camera": BC.to_blender(geo[rid]["ccam"], bs.frame, origin, geo[rid]["cshape"]),
                      "passes": geo[rid]["passes"], "pose": geo[rid]["pose"]} for rid in todo]
            views += [{"name": z["view"], "camera": BC.to_blender(z["cam"], bs.frame, origin, z["shape"]),
                       "passes": z["passes"], "pose": geo[z["rid"]]["pose"]} for z in ztodo]
            rr = self.s.call_read("compare_to_photos", "render", {
                "out_dir": str(out_dir / "renders"), "views": views,
                **self._render_params(rig, env, gains.get("light_frame"))},
                deadline_s=min(RENDER_DEADLINE_S, render_deadline_s or RENDER_DEADLINE_S), call_id=call_id)
            if rr["status"] != "ok":
                return rr
            files = {v["name"]: v["files"] for v in rr["result"]["views"]}
            render_meta = {k: rr["result"].get(k) for k in ("engine_final", "fallback", "gpu", "rig_spec",
                                                            "acetate_materials", "unchanged", "samples",
                                                            "linear_format")}
        secs["renders"] = round(time.perf_counter() - t2, 3)
        t3 = time.perf_counter()
        posed: dict = {}

        def edges_at(rid):
            """The model edges posed at a photo (both temples), once per photo; None without model edges."""
            if v3["edges"] is None:
                return None
            if rid not in posed:
                posed[rid] = self._edges_posed(v3["edges"], geo[rid]["pose"], hinge_z_mm, v3["opening"])
            return posed[rid]
        for rid in todo:
            recs[rid] = self._matched_photo(bs, rid, geo[rid], mesh, roles, bodies, fid, acetate, gains,
                                            geo[rid]["passes"], files[rid], hinge_z_mm, edges=edges_at(rid),
                                            opening=v3["opening"], name=names.get(rid, rid))
            recs[rid]["render"] = render_meta
            C.write_json(geo[rid]["cdir"] / "record.json", recs[rid])
            recs[rid]["cached"] = False
        for z in ztodo:
            # the zoom shows the photo with the stretch its compare row has (the photo's record when it is in this
            # call, else the same rule at the crop the compare would give it)
            stretch = recs[z["rid"]]["stretch"] if z["rid"] in recs else \
                self._crop_stretch(bs, z["rid"], geo[z["rid"]], z["crop"], acetate)
            z["rec"] = self._zoom_photo(bs, z, geo[z["rid"]], bodies, acetate, gains, files[z["view"]],
                                        edges_at(z["rid"]), stretch)
            z["rec"]["render"] = render_meta
            C.write_json(z["cdir"] / "record.json", z["rec"])
            z["rec"]["cached"] = False
        for rid in rids:
            recs[rid]["compare_id"] = call_id
            start = self._start_offsets(bs, rid, exp, gkey, hinge_z_mm)
            recs[rid]["start_offsets_mm"] = start
            recs[rid]["text"] = self._matched_text(recs[rid], start, call_id)
            if rid in points_px:
                g = geo[rid]
                recs[rid]["points"] = PO.chart_at(points_px[rid], g["cam"], g["mesh"], g["pose"], roles, hinge_z_mm)
        zoom_recs = []
        for z in zooms:
            z["rec"].update(compare_id=call_id, n=z["n"])
            z["rec"]["text"] = self._zoom_text(z["rec"], call_id)
            zoom_recs.append(z["rec"])
        secs["assemble"] = round(time.perf_counter() - t3, 3)
        secs["total"] = round(time.perf_counter() - t0, 3)
        report = {"schema": MATCHED_SCHEMA, "compare_id": call_id, "created": C._utc(), "basis": bs.summary(),
                  "checkpoint": self.head["id"], "fingerprint": exp["fingerprint"],
                  "glb": exp, "geometry": gkey, "rig": rig,
                  "passes": passes, "crop": crop, "hinge_z_mm": hinge_z_mm, "gains": gains,
                  "environment": None if env is None else {k: env.get(k) for k in (
                      "weights", "weights_sha", "lambda", "chosen_iteration", "path", "calibration")},
                  "model_edges": None if v3["edges"] is None else {
                      "edges": len(v3["edges"]), "sha256": v3["edges_sha256"], "opening": v3["opening"],
                      "pairs": sorted({e["pair"] for e in v3["edges"]}),
                      "soft_pairs": sorted({e["pair"] for e in v3["edges"] if e["soft"]}),
                      "on_model_mm": round(max(on_model), 6) if on_model else None},
                  "temple_px": {rid: g["temple_px"] for rid, g in geo.items()}, "photos": recs, "zoom": zoom_recs,
                  "note": C.CAMERA_NOTE, "seconds": secs}
        rp = out_dir / "report.json"
        C.write_json(rp, report)
        return {"status": "ok", "compare_id": call_id, "basis": bs.id, "checkpoint": self.head["id"], "rig": rig,
                "export": exp, "unusable_refs": bs.unusable_refs(),
                "passes": passes, "photos": {rid: {"image": recs[rid]["image"], "text": recs[rid]["text"],
                                                   "rows": recs[rid].get("rows"), "cached": recs[rid]["cached"],
                                                   **({"points": recs[rid]["points"]} if "points" in recs[rid]
                                                      else {})} for rid in rids},
                "zoom": [{"rid": r["rid"], "image": r["image"], "text": r["text"], "panels": r["panels"],
                          "cached": r["cached"]} for r in zoom_recs],
                "note": C.CAMERA_NOTE, "report": str(rp), "seconds": secs}

    @staticmethod
    def _photo_crop(bs: C.Basis, rid: str, g: dict, passes, crop: str, boxes) -> dict:
        """A photo's stacked crop in this compare: {box (x0, y0, x1, y1) native px, k, passes (its rows' render
        passes: a target photo the asked ones, native_linear first; another colourway gloss_black only), stretch (the
        ``boxes`` override's, or None)}. k = compare.stack_scale for the rows actually stacked (photo, native_linear
        for a target, gloss_black when rendered, overlay: 3 or 4), unless ``boxes`` fixes box and k."""
        target = bs.colourway(rid) == "target"
        vpasses = list(passes) if target else ["gloss_black"]
        n_rows = 2 + int(target) + int("gloss_black" in vpasses)     # photo, (native), (gloss_black), overlay
        if boxes and rid in boxes:
            box, k = tuple(int(v) for v in boxes[rid]["box"]), min(1.0, float(boxes[rid]["k"]))
        else:
            matte = bs.matte(rid)
            box = C.crop_box(g["temple"], matte) if crop == "temple" else C._box(g["mask"] | matte, C.CROP_PAD,
                                                                                    g["shape"])
            if box is None:
                box = (0, 0, g["shape"][1], g["shape"][0])
            k = C.stack_scale(box[2] - box[0], box[3] - box[1], n_rows)
        return {"box": box, "k": k, "passes": vpasses, "stretch": (boxes or {}).get(rid, {}).get("stretch")}

    @staticmethod
    def _stretch_of(pc: np.ndarray, mc: np.ndarray, part: np.ndarray, mask: np.ndarray, acetate) -> tuple:
        """A compare's photo stretch (lo, hi): the p2-p98 luma of the photo crop over the model's acetate first hits
        inside the matte, eroded 2 px (fewer than 200 px: the matte where the model shows)."""
        ac = C.erode_px(np.isin(part, acetate) & mc, 2)
        if ac.sum() < 200:
            ac = mc & mask
        return C.stretch_range(pc, ac)

    def _crop_stretch(self, bs: C.Basis, rid: str, g: dict, cr: dict, acetate) -> tuple:
        """The stretch the compare gives a photo at its crop ``cr`` (``_photo_crop``), for a zoom of a photo that is
        not in the call: the override, else ``_stretch_of`` at that crop's camera; (0, 255) when the crop has too
        few acetate pixels for one."""
        if cr["stretch"]:
            return tuple(float(v) for v in cr["stretch"])
        x0, y0, x1, y1 = cr["box"]
        k = cr["k"]
        shape = (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))
        pc, mc = self.photo_at(bs, rid, cr["box"], k, shape)
        r = g["mesh"].scene().render(BC.crop_camera(g["cam"], x0, y0, k), shape, 1)
        part = np.full(shape, -1, np.int64)
        part[r["mask"]] = g["mesh"].part[r["face_id"][r["mask"]]]
        try:
            return self._stretch_of(pc, mc, part, r["mask"], acetate)
        except ValueError:
            return 0.0, 255.0

    @staticmethod
    def _check_edges_on_model(edges: list, mesh, body, hinge_z: float, opening) -> list:
        """Per model edge the median distance (mm) of its points, placed as the +X temple's chart at the spec's
        opening (pose.chart_to_posed, no photo theta: the exported head carries its temples so), from the nearest
        vertex of the head's +X temple body. The lab sends the build's own vertices, so the head they were built for
        gives ~1e-5 mm (GLB export fidelity 2.1e-5 mm). Refuses E_ARGS when an edge's median exceeds EDGE_ON_MODEL_MM
        (edges of another build, a wrong hinge_z_mm, an opening left out, the mirrored side's chart: tenths of a mm,
        e.g. 0.63 mm with hinge_z off by 1 mm), naming the facts, or when the head has no +X temple body."""
        if body is None:
            raise SeeingError("E_ARGS", "model_edges are in the +X temple's chart, and the head has no +X temple body")
        tree = cKDTree(mesh.V[np.unique(mesh.F[mesh.part == body])])
        dist = [float(np.median(tree.query(PO.chart_to_posed(e["pts"], 1, hinge_z, None, opening))[0]))
                for e in edges]
        bad = [i for i, d in enumerate(dist) if d > EDGE_ON_MODEL_MM]
        if bad:
            i = max(bad, key=lambda j: dist[j])
            raise SeeingError("E_ARGS", f"{len(bad)} of {len(edges)} model_edges do not lie on the head's +X temple: "
                                        f"model_edges[{i}] {edges[i]['pair']!r} lies a median {dist[i]:.2f} mm from "
                                        f"its vertices (a build's own vertices lie within {EDGE_ON_MODEL_MM} mm of "
                                        f"the head built from it, placed with its hinge_z_mm and opening); this call "
                                        f"has hinge_z_mm {hinge_z:g} and opening " + (
                                            "0" if not opening else f"{opening[0]:g} deg about x {opening[1][0]:.2f}, "
                                                                    f"z {opening[1][1]:.2f}"))
        return dist

    @staticmethod
    def _cache_key(doc: dict) -> str:
        return hashlib.sha256(json.dumps(doc, sort_keys=True, default=C._json_default).encode()).hexdigest()[:24]

    @staticmethod
    def _matched_text(rec: dict, start: dict | None, call_id: str) -> str:
        """The compact text of one photo (composed on every call, so a cached record names the current
        compare_id): scale of the image shown and the points_px frame, IoU, outer-edge offsets with the start
        model's in brackets, a side view's outline offsets (``_outline_text``), the model edges shown, the share of
        the model the render rows show in the backdrop's colour, the compare_id and the camera note."""
        x0, y0, x1, y1 = rec["crop"]["box"]
        k = rec["crop"]["k"]
        lines = [rec["text_head"],
                 f"scale: {rec['px_per_mm'] * k:.2f} px/mm in this image ({rec['px_per_mm']:.2f} px/mm in the native "
                 f"photo x crop scale {k:.3f}). points_px are native photo pixels; this image shows the native box "
                 f"[{x0},{y0},{x1},{y1}]: u_img = (u - {x0} + 0.5) x {k:.3f} - 0.5, v_img = (v - {y0} + 0.5) x "
                 f"{k:.3f} - 0.5"]

        def fmt(v):
            return "n/a" if v is None else f"{v:+.2f}"
        offs = rec.get("outer_edge_offsets_mm")
        if offs:
            for s, rows_ in offs.items():
                srows = {r["d_mm"]: r["offset_mm"] for r in ((start or {}).get(s) or [])} if isinstance(
                    start, dict) and "missing" not in start else {}
                vals = " ".join(f"{o['d_mm']:g}:{fmt(o['offset_mm'])}"
                                f"({fmt(srows.get(o['d_mm'])) if srows else 'n/a'})" for o in rows_)
                lines.append(f"outer edge, photo minus model (mm; start model in brackets) side {s} at d: {vals}")
        for temple, o in (rec.get("outline_offsets_mm") or {}).items():
            lines.append(Seeing._outline_text(temple, o))
        if rec.get("model_edges") is not None:
            shown, soft = Seeing._edge_pairs(rec["model_edges"])
            lines.append("model edges (from the build): " + (", ".join(shown) if shown else "none visible here")
                         + (f"; soft borders ({SOFT_TEXT}): {', '.join(soft)}" if soft else ""))
        share = rec.get("render_backdrop_share")
        if share:
            lines.append("render rows: " + ", ".join(f"{v:.0%} ({p})" for p, v in share.items())
                         + " of the model's pixels show in the backdrop's colour after the stretch; the model's "
                           "outline is drawn on the render rows")
        lines.append(f"compare_id {call_id}. {C.CAMERA_NOTE}")
        return "\n".join(lines)

    @staticmethod
    def _outline_text(temple: str, o: dict) -> str:
        """One temple's outline line (``_side_outline``): stations every 10 mm, photo minus model to 0.1 mm (the
        edges are whole native pixels, 0.12-0.17 mm here), a station without a number by its reason; where the view
        shows an edge's face, the outline there is not measured and that face's far edge (``{edge}_silhouette``,
        photo and model alike) is printed minus its median (``_far_edge_text``; one list with the other stations'
        reasons when no station of that edge is measured, else beside the outline's list); a temple seen from inside
        is not measured. Why the far edge is printed (v3p3-verify-8, owner P1): in 01, 05 and B1 (14.7-14.8 deg from
        above) the arm top behind the facets of session2's head (r6 of run 7's package) sits 2.3-2.6 mm below the
        photo's at d 110 and 3.5-3.8 mm at d 120 against the d 40-90 level (the model's own render under the photo's
        matte rule agrees: +2.3..+3.6), inherited unchanged from revision 0 (run7-residual: run 7's head too), and the
        line said only "top: not measured (... set by its width and lean, not the outline)": no text showed a step the
        overlay row shows by eye. A record without the silhouette (an older compare) keeps "not measured"."""
        if not (o.get("top") or o.get("bottom")):
            return f"outline: the {temple} temple has no 10 mm station here"
        if not o.get("facing"):
            return (f"outline: the {temple} temple is seen from inside here (its outer face turns away from the "
                    "camera): not measured")
        e = o.get("elevation_deg") or 0.0
        lo, hi = (f"{v:.2f}" for v in o["mm_per_px"])
        head = (f"outline of the {temple} temple, photo minus model (mm; 1 px = {lo if lo == hi else lo + '-' + hi} "
                f"mm; this view looks {abs(e):.0f} deg " + ("down onto" if e >= 0 else "up at") + " the temple)")
        parts, seen = [], set()
        for edge, sign in (("top", "+ = the photo's edge higher"), ("bottom", "+ = the photo's edge lower")):
            rows = o.get(edge) or []
            sil = o.get(f"{edge}_silhouette")
            if sil and not any(r["offset_mm"] is not None for r in rows):
                far = {r["d_mm"]: r for r in sil}                   # one list: the far edge, other stations' reasons
                parts.append(Seeing._far_edge_text(edge, sign, [far.get(r["d_mm"], r) for r in rows], e, seen))
                continue
            if not (rows and all(r["reason"] == f"{edge} face in view" for r in rows)):
                vals = []
                for r in rows:
                    if r["offset_mm"] is not None:
                        vals.append(f"{r['d_mm']:g} " + f"{r['offset_mm']:+.1f}".replace("-0.0", "+0.0"))
                    else:
                        vals.append(f"{r['d_mm']:g} {r['reason']}")
                        seen.add(r["reason"])
                parts.append(f"{edge} ({sign}) at d: " + ", ".join(vals))
            elif not sil:
                parts.append(f"{edge}: not measured (the view shows the {edge} face, so the {edge} silhouette is that "
                             f"face's far edge)")
            if sil:
                parts.append(Seeing._far_edge_text(edge, sign, sil, e, seen))
        legend = [OUTLINE_REASON_TEXT[r] for r in OUTLINE_REASON_TEXT if r in seen]
        return head + ": " + "; ".join(parts) + (" (" + "; ".join(legend) + ")" if legend else "")

    @staticmethod
    def _far_edge_terms(edge: str, elevation_deg: float | None) -> dict:
        """What a face-in-view far edge (``{edge}_silhouette``) moves with, for the record (the text states the two a
        reader can edit, the arm's height and its inner face: ``_far_edge_text``): 1 mm per mm of the arm's {edge}
        height, tan(elevation) mm per mm the photo's inner face lies further from the camera than the model's (the far
        edge is the {edge} face's inner edge), the face's lean across the arm, and the matte's cut at a lit edge; the
        median over the stations takes off what every station shares."""
        e = abs(float(elevation_deg or 0.0))
        return {"measures": f"the arm's {edge} height at its inner edge, 1 mm per mm",
                "elevation_deg": round(e, 1),
                "inner_face_mm_per_mm": round(math.tan(math.radians(min(e, 89.0))), 3),
                "also": [f"the {edge} face's lean across the arm", "the matte's cut at a lit edge"],
                "median": "printed values are less the median over the stations when at least "
                          f"{FAR_EDGE_LEVEL_MIN} are measured (what every station shares)"}

    @staticmethod
    def _far_edge_text(edge: str, sign: str, sil: list, elevation_deg: float, seen: set) -> str:
        """The far edge of a face the view shows (``{edge}_silhouette`` rows of ``_side_outline``), photo minus model,
        stated as what a reader can edit: the arm's {edge} height (mm, the sign as the outline's) and, since the far
        edge is the face's inner edge, tan(elevation) per mm the photo's inner face lies further inward (+0.27 at 15
        deg; the same sign for a view from below). With >= FAR_EDGE_LEVEL_MIN measured stations each is printed minus
        their median (the median and the count are printed), so a value is a step at that d against the rest of the
        arm: session2's head (r6 of run 7's package, cmp022) at 05 prints "110 +2.5, 120 +3.4" against a median of
        -0.7 where the raw offsets read +1.8 / +2.7; with fewer, as measured (what the stations share left in).
        Stations without a number print their reason (added to ``seen`` for the legend).
        The face's lean and the matte's cut are left to the record (``_side_outline``'s ``{edge}_far_edge``): the
        2026-10-13 blind stand-in could not act on "a difference in the arm's top height, in the top face's width
        (+0.26 per mm wider in the photo) or lean, or in the matte's cut there" (with the inner face held the far edge
        does not depend on the face's width) and read -1.1 at d 20 as height. The inner face stays in the text
        (v3-standin-fix-review): it is an edit (the inner chain), and the stand-in's photos put the arm's X near the tip
        about 2 mm apart (03 / B2 against 06; its facet_variants moved the inner chain there by -1..+2 mm): 0.5 mm of
        far edge at 15 deg, five times the printed 0.1 mm step."""
        got = [float(r["offset_mm"]) for r in sil if r["offset_mm"] is not None]
        level = float(np.median(got)) if len(got) >= FAR_EDGE_LEVEL_MIN else None
        vals = []
        for r in sil:
            if r["offset_mm"] is None:
                vals.append(f"{r['d_mm']:g} {r['reason']}")
                seen.add(r["reason"])
            else:
                v = float(r["offset_mm"]) - (level or 0.0)
                vals.append(f"{r['d_mm']:g} " + f"{v:+.1f}".replace("-0.0", "+0.0"))
        per = math.tan(math.radians(min(abs(float(elevation_deg or 0.0)), 89.0)))
        what = (f"{edge}: the arm's {edge} height, photo minus model, mm ({sign}), read on the {edge} face's far edge "
                f"(this view shows the {edge} face; that edge is its inner one, so a value also gains {per:.2f} per mm "
                "the photo's inner face lies further inward)")
        if level is None:
            how = (f"as measured ({len(got)} stations, under {FAR_EDGE_LEVEL_MIN} for a median), so a value is the "
                   "difference at that d with what every station shares left in")
        else:
            how = (f"less its median {level:+.1f} over {len(got)} stations, so a value is a step at that d against the "
                   "rest of the arm")
        return f"{what}, {how}; at d: " + ", ".join(vals)

    @staticmethod
    def _mesh_creases(mp, body_ids: list) -> dict:
        """compare.crease_edges (>= 6 deg) of the temple bodies of a (posed) mesh, joined: the overlay's old rule."""
        edges = [C.crease_edges(mp.V, mp.F[mp.part == b]) for b in body_ids]
        if not edges:
            return C.crease_edges(np.zeros((0, 3)), np.zeros((0, 3), int))
        return {key: np.concatenate([e[key] for e in edges]) for key in edges[0]}

    @staticmethod
    def _edges_posed(edges: list, pose, hinge_z: float, opening) -> list:
        """Each model edge on both temples, placed as the model is at a photo: [{pair, shape, deg, deg_at, range, soft,
        side, P (K, 3) posed model mm}] (pose.chart_to_posed of the +X chart points: x = side X, z = hinge_z - d, then
        the spec's opening, then the photo's theta; side -1 = the mirrored temple)."""
        return [{"pair": e["pair"], "shape": e["shape"], "deg": e["deg"], "deg_at": e.get("deg_at"),
                 "range": e.get("range"), "soft": bool(e.get("soft")), "side": s,
                 "P": PO.chart_to_posed(e["pts"], s, hinge_z, pose, opening)} for e in edges for s in (1, -1)]

    @staticmethod
    def _edges_drawn(posed: list, cam, frame, scene, shape) -> list:
        """The posed model edges' visible runs in one image (compare.edge_runs against the posed model's raster
        scene; an edge with ``deg_at`` carries its signed angle per sample as a third column): [{pair, shape, deg,
        side, runs, range?, soft?}] as compare.draw_edges takes them (compare.edges_with_runs)."""
        vals = [e.get("deg_at") for e in posed]
        runs = C.edge_runs([e["P"] for e in posed], cam, frame, scene, shape,
                           values=vals if any(v is not None for v in vals) else None)
        return C.edges_with_runs(posed, runs)

    @staticmethod
    def _edge_records(drawn: list, box, k: float) -> list:
        """The drawn model edges for the record: {pair, shape, deg, side (SIDE_NAMES: "charted" | "mirrored", as
        outline_offsets_mm), shown_px (image px drawn), runs_px (each run every ~EDGE_RECORD_PX image px and its end,
        in NATIVE photo px to 0.1), deg_shown ([min, max] signed angle over the drawn samples, when the edge has
        deg_at and is not soft), range (as given), soft (true on a soft border, drawn dashed with no angle)}."""
        x0, y0 = box[0], box[1]
        out = []
        for e in drawn:
            runs, shown, vals = [], 0.0, []
            for run in e["runs"]:
                run = np.asarray(run, float)
                cum = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(run[:, :2], axis=0), axis=1))]
                shown += float(cum[-1])
                idx = np.unique(np.r_[np.searchsorted(cum, np.arange(0.0, cum[-1], EDGE_RECORD_PX)), len(run) - 1])
                nat = (run[idx, :2] + 0.5) / k - 0.5 + np.array([x0, y0], float)
                runs.append(np.round(nat, 1).tolist())
                if run.shape[1] >= 3:
                    vals.append(run[:, 2][np.isfinite(run[:, 2])])
            rec = {"pair": e["pair"], "shape": e["shape"], "deg": e["deg"], "side": SIDE_NAMES[e["side"]],
                   "shown_px": round(shown, 1), "runs_px": runs}
            if e.get("range") is not None:
                rec["range"] = [float(v) for v in e["range"]]
            v = np.concatenate(vals) if vals else np.zeros(0)
            if e.get("soft"):
                rec["soft"] = True
            elif len(v):
                rec["deg_shown"] = [round(float(v.min()), 2), round(float(v.max()), 2)]
            out.append(rec)
        return out

    @staticmethod
    def _edge_pairs(records: list) -> tuple[list, list]:
        """(crease pairs, soft border pairs) that show in an image's edge records (any run drawn), sorted."""
        shown = [e for e in records or [] if e["runs_px"]]
        return (sorted({e["pair"] for e in shown if not e.get("soft")}),
                sorted({e["pair"] for e in shown if e.get("soft")}))

    def _matched_photo(self, bs: C.Basis, rid: str, g: dict, mesh, roles, bodies, fid, acetate, gains, passes,
                       files: dict, hinge_z: float, *, edges: list | None = None, opening=None,
                       name: str | None = None) -> dict:
        """One photo's stacked image (photo / native_linear render / gloss_black render / overlay: the renders asked,
        the overlay last, for every colour; a variant-colourway photo has no native row, the model's material being
        the target colourway) and its compact numbers; ``name`` (default the basis name) titles the photo row and
        heads the text (the lab's id: ``photo_names``). ``edges``: the model
        edges posed at this photo (``_edges_posed``), drawn instead of the mesh creases; with them (the v3 compare)
        the render rows also carry the model's outline, since a face the stretch clips to the backdrop's colour (05's
        white top face on the white floor: 13 % of the model) would otherwise lose its edge there."""
        x0, y0, x1, y1 = g["box"]
        k, ccam, cshape = g["k"], g["ccam"], g["cshape"]
        photo, matte = bs.photo(rid), bs.matte(rid)
        pc, mc = self.photo_at(bs, rid, g["box"], k, cshape)
        mp = g["mesh"]
        r = mp.scene().render(ccam, cshape, 1, want_points=True)
        part = np.full(cshape, -1, np.int64)
        part[r["mask"]] = mp.part[r["face_id"][r["mask"]]]
        cw = bs.colourway(rid)
        gcw = gains["colourways"].get(cw) or gains["colourways"].get("target")
        if g.get("stretch"):
            lo, hi = (float(v) for v in g["stretch"])
        else:
            lo, hi = self._stretch_of(pc, mc, part, r["mask"], acetate)
        bg = C.photo_background(photo, matte)
        v3 = edges is not None
        share = {}

        def render_row(shown: np.ndarray, alpha: np.ndarray, rlo: float, rhi: float, name: str) -> Image.Image:
            """A render row as shown; in the v3 compare with the model's outline and its backdrop share."""
            if not v3:
                return Image.fromarray(shown)
            share[name] = round(C.backdrop_share(shown, alpha, C.apply_stretch(np.asarray([[bg]], np.uint8), rlo,
                                                                                rhi)[0, 0]), 3)
            return C.overlay_matched(shown, r["mask"], [], None)
        pname = name or rid             # not "name": the row-file loop below reuses that name
        rows = [("photo", f"{pname} photo (stretch {lo:.0f}-{hi:.0f})", Image.fromarray(C.apply_stretch(pc, lo, hi)))]
        gain = gains["colourways"]["target"]["native_linear"]["gain"]
        native = cw == "target"
        if native:
            nat = C.read_linear(files["native_linear"]["path"])
            rows.append(("native_linear", "render native_linear (white-balanced gain R/G/B "
                         + "/".join(f"{v:.2f}" for v in np.atleast_1d(gain)) + "), same stretch"
                         + (", outline magenta" if v3 else ""),
                         render_row(C.apply_stretch(C.composite_render(nat[0], nat[1], gain, bg), lo, hi), nat[1],
                                    lo, hi, "native_linear")))
        # chart grid on the temple bodies; the model edges (or the mesh's creases) of the temple bodies
        side_c = np.zeros(cshape, np.int8)
        body_ids = [b for b in bodies.values() if b is not None]
        for s, b in bodies.items():
            if b is not None:
                side_c[part == b] = s
        Q = PO.unpose_points(np.nan_to_num(r["points"].reshape(-1, 3)), side_c.ravel(), g["pose"])
        ch = PO.chart_of(Q, side_c.ravel(), hinge_z).reshape(cshape + (3,))
        grid = C.iso_lines(ch[..., 0], ch[..., 1], side_c != 0, side_c)
        base = C.apply_stretch(pc, lo, hi)
        polys, drawn = [], None
        if v3:
            drawn = self._edges_drawn(edges, ccam, bs.frame, mp.scene(), cshape)
            soft = any(e.get("soft") and e["runs"] for e in drawn)
            overlay = ("overlay", "overlay: outline magenta, model edges ridge orange / valley blue / flat grey "
                                  "(2 px >= 6 deg)" + (", soft border dashed grey" if soft else "")
                                  + ", grid d/10 mm y/5 mm",
                       C.overlay_matched(base, r["mask"], [], grid, edges=drawn))
        else:
            polys = C.crease_polylines(self._mesh_creases(mp, body_ids), ccam, bs.frame, r["depth"])
            overlay = ("overlay", "overlay: outline magenta, ridge orange, valley blue, grid d/10 mm y/5 mm",
                       C.overlay_matched(base, r["mask"], polys, grid))
        gl_rec = gains["gloss_black"]
        gloss_used = None
        if "gloss_black" in passes:
            gl = C.read_linear(files["gloss_black"]["path"])
            dark = bool(gcw.get("dark"))
            glo, ghi = (lo, hi) if dark else tuple(gl_rec["stretch"])
            gloss_used = {"gain": gl_rec["gain"], "stretch": [glo, ghi], "stretch_is_photo": dark}
            rows.append(("gloss_black", f"render gloss_black (gain {gl_rec['gain']:.2f}, stretch {glo:.0f}-{ghi:.0f}"
                         f"{' = the photo' if dark else ' fixed for the job'})" + (", outline" if v3 else ""),
                         render_row(C.apply_stretch(C.composite_render(gl[0], gl[1], gl_rec["gain"], bg), glo, ghi),
                                    gl[1], glo, ghi, "gloss_black")))
        rows.append(overlay)            # one order for every colour: photo, the renders, the overlay (v3p3 review)
        img = C.stack_rows([(label, im) for _n, label, im in rows])
        g["cdir"].mkdir(parents=True, exist_ok=True)
        ip = g["cdir"] / f"{rid}.png"
        img.save(ip)
        row_files = {}
        for name, _label, im in rows:
            row_files[name] = str(g["cdir"] / f"{rid}_{name}.png")
            im.save(row_files[name])
        # numbers
        body_pts = r["points"][np.isin(part, body_ids)]
        ppm = CAMS.px_per_mm_at(g["cam"], bs.frame, np.median(body_pts, axis=0)) if len(body_pts) else \
            float("nan")
        region = ndimage.binary_dilation(g["temple"], iterations=TEMPLE_REGION_PX)
        a, b = g["mask"] & region, matte & region
        iou = float((a & b).sum() / max(1, (a | b).sum()))
        view_kind = bs.refs[rid].get("view_kind") or ""
        offsets = self._outer_offsets(bs, rid, g, mesh, bodies, hinge_z) if view_kind in OFFSET_VIEWS else None
        outline = self._side_outline(bs, rid, g, mesh, roles, bodies, hinge_z, opening) if view_kind in SIDE_VIEWS \
            else None
        head = (f"{pname} ({bs.refs[rid].get('view_kind')}, {cw}, {bs.refs[rid].get('fit_status') or 'fitted'}): "
                f"temple IoU {iou:.3f}" + ("" if native else "; no native row (the model's material is the target "
                                                               "colourway, this photo is a variant)"))
        if drawn is not None:
            shown = [e for e in drawn if e["runs"]]
            creases = {s: sum(1 for e in shown if e["shape"] == s and not e.get("soft")) for s in C.EDGE_SHAPES}
            creases["soft"] = sum(1 for e in shown if e.get("soft"))
            creases["source"] = "model edges"
        else:
            creases = {"ridge": int(sum(1 for _, s in polys if s > 0)),
                       "valley": int(sum(1 for _, s in polys if s < 0)), "source": "mesh dihedrals"}
        return {"image": str(ip), "rows": row_files, "text_head": head, "rid": rid, "name": pname,
                "view_kind": bs.refs[rid].get("view_kind"),
                "colourway": cw, "pose": g["pose"], "crop": {"box": [x0, y0, x1, y1], "k": k,
                                                              "camera": ccam.to_dict(), "shape": list(cshape)},
                "px_per_mm": ppm, "px_per_mm_shown": ppm * k, "temple_iou": iou, "outer_edge_offsets_mm": offsets,
                "outline_offsets_mm": outline,
                "gain": gain, "stretch": [lo, hi], "gloss": gloss_used, "background": list(bg),
                "creases": creases, "render_backdrop_share": share or None,
                "model_edges": None if drawn is None else self._edge_records(drawn, (x0, y0), k),
                "renders": files}

    def _zoom_photo(self, bs: C.Basis, z: dict, g: dict, bodies, acetate, gains, files: dict,
                    edges: list | None, stretch) -> dict:
        """One zoom (module doc): the box at k (the photo on the crop camera's exact grid, compare.resample_box:
        bilinear, never overshooting; the render made at k), a 2 x 2 image photo | render / photo + model outline +
        model edges | geometry or the render with edges, the edges labelled with their angle (no model edges: the
        mesh creases, unlabelled). Both render panels carry the model's outline (a face the stretch clips to the
        backdrop's colour loses its edge there; the share is recorded). The outline is not drawn along a panel's
        border (the model goes on past it). ``stretch`` = the compare's stretch of this photo (``_matched_body``),
        applied to photo and render; the geometry panel is stretched over this panel's temple pixels. ``z["name"]``
        (default the basis name) titles the photo panel and the text."""
        rid = z["rid"]
        pname = z.get("name") or rid
        x0, y0, x1, y1 = z["box"]
        k, cam, shape = z["k"], z["cam"], tuple(z["shape"])
        photo, matte = bs.photo(rid), bs.matte(rid)
        pc = C.resample_box(photo, z["box"], k, shape)
        mp = g["mesh"]
        r = mp.scene().render(cam, shape, 1, want_points=True)
        part = np.full(shape, -1, np.int64)
        part[r["mask"]] = mp.part[r["face_id"][r["mask"]]]
        cw = bs.colourway(rid)
        target = cw == "target"
        lo, hi = (float(v) for v in stretch)
        bg = C.photo_background(photo, matte)
        base = C.apply_stretch(pc, lo, hi)
        if target:
            gain = gains["colourways"]["target"]["native_linear"]["gain"]
            nat = C.read_linear(files["native_linear"]["path"])
            render, alpha, (rlo, rhi) = C.apply_stretch(C.composite_render(nat[0], nat[1], gain, bg), lo, hi), \
                nat[1], (lo, hi)
            rlabel = "render native_linear (same stretch) + outline"
        else:
            gl_rec = gains["gloss_black"]
            gcw = gains["colourways"].get(cw) or gains["colourways"].get("target")
            dark = bool(gcw.get("dark"))
            rlo, rhi = (lo, hi) if dark else tuple(gl_rec["stretch"])
            gl = C.read_linear(files["gloss_black"]["path"])
            render, alpha = C.apply_stretch(C.composite_render(gl[0], gl[1], gl_rec["gain"], bg), rlo, rhi), gl[1]
            rlabel = f"render gloss_black (stretch {rlo:.0f}-{rhi:.0f}{' = the photo' if dark else ''}) + outline"
        share = C.backdrop_share(render, alpha, C.apply_stretch(np.asarray([[bg]], np.uint8), rlo, rhi)[0, 0])
        rimg = C.overlay_matched(render, r["mask"], [], None, open_border=True)
        body_ids = [b for b in bodies.values() if b is not None]
        drawn, labels, polys = None, [], []
        soft = ""
        if edges is not None:
            drawn = self._edges_drawn(edges, cam, bs.frame, mp.scene(), shape)
            soft = "; dashed grey = soft border" if any(e.get("soft") and e["runs"] for e in drawn) else ""
            ov = C.overlay_matched(base, r["mask"], [], None, open_border=True)   # the outline, then the edges
            labels = C.draw_edges(ov, drawn, labels=True)
            olabel = "photo + outline + model edges (deg, - = valley" + soft + ")"
        else:
            polys = C.crease_polylines(self._mesh_creases(mp, body_ids), cam, bs.frame, r["depth"])
            ov = C.overlay_matched(base, r["mask"], polys, None, open_border=True)
            olabel = "photo + outline + mesh creases >= 6 deg"
        if z["geometry"]:
            nrm = np.asarray(Image.open(files["normal"]["path"]).convert("RGBA"))
            p4 = Image.fromarray(C.geometry_view(nrm, region=np.isin(part, body_ids)))
            l4 = "geometry: surface direction as colour (no light)"
        else:
            p4 = rimg.copy()
            if drawn is not None:
                C.draw_edges(p4, drawn, labels=True)
                l4 = "render + outline + model edges" + (" (dashed grey = soft border)" if soft else "")
            else:
                p4 = C.overlay_matched(render, r["mask"], polys, None, open_border=True)
                l4 = "render + outline + mesh creases"
        panels = {"photo": (f"{pname} photo (stretch {lo:.0f}-{hi:.0f})", Image.fromarray(base)),
                  "render": (rlabel, rimg), "overlay": (olabel, ov),
                  ("geometry" if z["geometry"] else "render_edges"): (l4, p4)}
        sheet = C.grid([C.labelled(im, label, fit=True) for label, im in panels.values()], 2, max_px=C.SHEET_MAX_PX)
        z["cdir"].mkdir(parents=True, exist_ok=True)
        ip = z["cdir"] / f"zoom-{rid}.png"
        sheet.convert("RGB").save(ip)
        paths = {}
        for name, (_label, im) in panels.items():
            paths[name] = str(z["cdir"] / f"zoom-{rid}_{name}.png")
            im.convert("RGB").save(paths[name])
        body = np.isin(part, body_ids)
        in_box = "temple" if body.any() else "model" if r["mask"].any() else "none"
        pts = r["points"][body] if in_box == "temple" else r["points"][r["mask"]] if in_box == "model" else \
            (mp.V[np.unique(mp.F[np.isin(mp.part, body_ids)])] if body_ids else mp.V)
        ppm = CAMS.px_per_mm_at(g["cam"], bs.frame, np.median(pts, axis=0))
        return {"image": str(ip), "panels": paths, "rid": rid, "name": pname,
                "view_kind": bs.refs[rid].get("view_kind"),
                "colourway": cw, "box": [x0, y0, x1, y1], "k": k, "geometry": z["geometry"],
                "camera": cam.to_dict(), "shape": list(shape), "px_per_mm": ppm, "px_per_mm_shown": ppm * k,
                "in_box": in_box, "stretch": [lo, hi], "render_backdrop_share": round(share, 3),
                "panel_labels": {n: lab for n, (lab, _im) in panels.items()},
                "edge_labels": [{"pair": p, "text": t, "uv": [round(u, 1), round(v, 1)]} for p, t, (u, v) in labels],
                "model_edges": None if drawn is None else self._edge_records(drawn, (x0, y0), k),
                "creases": None if drawn is not None else {"ridge": int(sum(1 for _, s in polys if s > 0)),
                                                           "valley": int(sum(1 for _, s in polys if s < 0))}}

    @staticmethod
    def _zoom_text(rec: dict, call_id: str) -> str:
        """A zoom's compact text (composed on every call): the box, k, px/mm shown, the pixel map, the panels, what
        of the model the box holds, the edges labelled, the render's backdrop share and the compare_id."""
        x0, y0, x1, y1 = rec["box"]
        k = rec["k"]
        lab = rec["panel_labels"]
        fourth = lab.get("geometry") or lab.get("render_edges")
        where = {"temple": "", "model": " (the temple is not in this box: px/mm at the model in it)",
                 "none": " (the model is not in this box: px/mm at the temple)"}[rec.get("in_box", "temple")]
        lines = [f"zoom {rec.get('n', 1)} on {rec.get('name') or rec['rid']} ({rec.get('view_kind')}, "
                 f"{rec['colourway']}): native box "
                 f"[{x0},{y0},{x1},{y1}] at k {k:.2f}: {rec['px_per_mm'] * k:.2f} px/mm in this image "
                 f"({rec['px_per_mm']:.2f} native){where}; u_img = (u - {x0} + 0.5) x {k:.2f} - 0.5, v_img = (v - "
                 f"{y0} + 0.5) x {k:.2f} - 0.5 in each panel",
                 f"panels: {lab['photo']} | {lab['render']} / {lab['overlay']} | {fourth}"
                 + ("; the geometry colours compare within this panel only" if lab.get("geometry") else "")]
        if rec.get("model_edges") is not None:
            items = [f"{e['pair']} {e['text']}" for e in rec.get("edge_labels") or []]
            soft = Seeing._edge_pairs(rec["model_edges"])[1]
            lines.append(f"model edges shown ({EDGE_LABEL_TEXT}): " + (", ".join(items) if items else "none here")
                         + (f"; soft borders shown ({SOFT_TEXT}): {', '.join(soft)}" if soft else ""))
        if rec.get("render_backdrop_share"):
            lines.append(f"render: {rec['render_backdrop_share']:.0%} of the model's pixels show in the backdrop's "
                         "colour after the stretch (the outline marks the model's edge)")
        lines.append(f"compare_id {call_id}.")
        return "\n".join(lines)

    @staticmethod
    def _side_outline(bs: C.Basis, rid: str, g: dict, mesh, roles, bodies: dict, hinge_z: float, opening,
                      matte: np.ndarray | None = None) -> dict:
        """outline_offsets_mm of a side view (module doc): per temple (SIDE_NAMES) {facing, elevation_deg,
        mm_per_px [min, max], top, bottom ([{d_mm, offset_mm, reason, px_per_mm (native, along the line)}] at every
        10 mm station from d 10 to the tip - 2 mm, compare.outline_offsets at native size against ``matte``, default
        the photo's), top_silhouette / bottom_silhouette (when that face is in view: the silhouette's offsets, the
        far edge on photo and model alike, printed minus their median by ``_far_edge_text``), top_far_edge /
        bottom_far_edge (beside them: what else that far edge moves with, ``_far_edge_terms``)}.
        A station's line starts at the temple's centre at d (the chart point (d, the middle y of the body's vertices
        within 1 mm of d, their largest X), placed as the model is: the spec's opening, the photo's theta) and runs
        along the image's vertical; its px per mm is the image height of 1 mm of the chart's y there. What a
        silhouette edge is depends on the view (2026-10-12 review of v3 seeing):
        - a temple whose outward x turns away from the camera at a station (pose.faces_camera: the far temple of a
          side view) is seen from inside: its silhouette is its inner face's and top face's, set by the unobserved
          theta and the inner face (05 / 01: -2.0..-2.5 mm at d 10 decaying to 0 by d 80 on both physical temples,
          the same shift top and bottom), never the outline: reason "seen from inside";
        - the camera's elevation e over the temple there (atan2 of the toward-camera vector's components along the
          chart's up and the temple's outward x; + = from above): a view from above shows the top face, about
          thickness x tan(e) x px/mm px (05 / B1 / 01: e 14-15 deg, 11-15 px), so the top silhouette is the top
          face's far (inner) edge on photo and model alike, not the outline's top: its offsets carry the arm's top
          height, the inner faces' position difference x tan(e), the top faces' lean and the matte's cut at a lit
          edge (-0.7..-1.0 mm at d 40-90 on the three photos, -0.8..-1.1 on the model's own render under the photo's
          matte rule), and a step in the arm's top beyond that (session2's head: +2.3..+3.8 mm at d 110-120,
          v3p3-verify-8): reason "top face in view", its silhouette offsets kept as top_silhouette and what they move
          with as top_far_edge (_far_edge_terms; the text states the height and the inner face's tan(e)); a view
          from below likewise for the bottom. An edge is measured where its face shows < SIDE_FACE_PX, and on the far side of the view (the
          bottom of a view from above is the outer-bottom edge: 0.00 at every station on the model's own render)."""
        matte = bs.matte(rid) if matte is None else matte
        cam = g["cam"]
        out = {}
        for s, b in bodies.items():
            if b is None:
                continue
            V0 = mesh.V[np.unique(mesh.F[mesh.part == b])]
            if opening:
                V0 = PO.rot_about_hinge(V0, s, -opening[0], opening[1])
            ch = PO.chart_of(V0, np.full(len(V0), s, np.int8), hinge_z)
            temple = np.isin(g["part"], PO.temple_parts(g["mesh"], s, roles))
            st = []
            for d in np.arange(GRID_STATION_MM, ch[:, 0].max() - 2.0, GRID_STATION_MM):
                near = np.abs(ch[:, 0] - d) <= 1.0
                if not near.any():
                    continue
                y, X = float(ch[near, 1].min() + ch[near, 1].max()) / 2, float(ch[near, 2].max())
                thick = float(ch[near, 2].max() - ch[near, 2].min())
                P = PO.chart_to_posed([[d, y, X], [d, y + 1.0, X], [d, y, X + 1.0]], s, hinge_z, g["pose"], opening)
                up, outward = P[1] - P[0], P[2] - P[0]
                tw = PO.toward_camera(P[:1], cam, bs.frame)[0]
                e = math.degrees(math.atan2(float(tw @ up), float(tw @ outward)))
                uv = project_mm(P[:2], cam, bs.frame)
                ppm = abs(float(uv[1][1] - uv[0][1]))
                if ppm > 1e-6:
                    st.append({"d_mm": float(d), "uv": uv[0], "px_per_mm": ppm, "elev": e,
                               "facing": bool(PO.faces_camera(P[:1], outward[None], cam, bs.frame)[0]),
                               "face_px": thick * math.tan(math.radians(min(abs(e), 89.0))) * ppm})
            rec = {"facing": any(q["facing"] for q in st), "elevation_deg": None, "mm_per_px": None}
            facing = [q for q in st if q["facing"]]
            if facing:
                rec["elevation_deg"] = round(float(np.median([q["elev"] for q in facing])), 1)
                ppms = [q["px_per_mm"] for q in facing]
                rec["mm_per_px"] = [round(1.0 / max(ppms), 3), round(1.0 / min(ppms), 3)]
            for edge, sign, vdir in (("top", 1.0, -1.0), ("bottom", -1.0, 1.0)):
                rows, todo, sil = [None] * len(st), [], []
                for i, q in enumerate(st):
                    if not q["facing"]:
                        rows[i] = {"d_mm": q["d_mm"], "offset_mm": None, "reason": "seen from inside"}
                    elif sign * q["elev"] > 0 and q["face_px"] >= SIDE_FACE_PX:
                        rows[i] = {"d_mm": q["d_mm"], "offset_mm": None, "reason": f"{edge} face in view"}
                        sil.append(i)
                    else:
                        todo.append(i)

                def offsets(idx):
                    return C.outline_offsets([{"d_mm": st[i]["d_mm"], "uv": st[i]["uv"], "dir": (0.0, vdir),
                                               "px_per_mm": st[i]["px_per_mm"]} for i in idx], g["mask"], temple,
                                             matte)
                for i, r in zip(todo, offsets(todo)):
                    rows[i] = r
                for i, r in enumerate(rows):
                    r["px_per_mm"] = round(st[i]["px_per_mm"], 3)
                rec[edge] = rows
                if sil:
                    rec[f"{edge}_silhouette"] = [{**r, "px_per_mm": round(st[i]["px_per_mm"], 3)}
                                                 for i, r in zip(sil, offsets(sil))]
                    rec[f"{edge}_far_edge"] = Seeing._far_edge_terms(edge, rec["elevation_deg"])
            out[SIDE_NAMES[s]] = rec
        return out

    @staticmethod
    def _outer_offsets(bs: C.Basis, rid: str, g: dict, mesh, bodies: dict, hinge_z: float) -> dict:
        """Per side: the outer-edge offset at every 10 mm station (compare.outer_edge_offsets) at native size."""
        p = PO.parse_pose(g["pose"])
        out = {}
        for s, b in bodies.items():
            if b is None:
                continue
            V0 = mesh.V[np.unique(mesh.F[mesh.part == b])]
            ch = PO.chart_of(V0, np.full(len(V0), s, np.int8), hinge_z)
            th, axis = p.get(s, (0.0, (0.0, 0.0)))
            stations = []
            for d in np.arange(GRID_STATION_MM, ch[:, 0].max() - 2.0, GRID_STATION_MM):
                near = np.abs(ch[:, 0] - d) <= 1.0
                if not near.any():
                    continue
                j = np.flatnonzero(near)[int(np.argmax(ch[near, 2]))]
                P2 = np.stack([V0[j], V0[j] + np.array([s * 1.0, 0.0, 0.0])])
                uv = project_mm(PO.rot_about_hinge(P2, s, th, axis), g["cam"], bs.frame)
                step = uv[1] - uv[0]
                ppm = float(np.linalg.norm(step))
                if ppm > 1e-6:
                    stations.append({"d_mm": float(d), "uv": uv[0], "dir": step / ppm, "px_per_mm": ppm})
            out[PO.SIDE_KEYS[s]] = C.outer_edge_offsets(stations, g["mask"], bs.matte(rid))
        return out

    # ---------------------------------------------------------------------------------------------- fit_cameras
    def fit_cameras(self, reason: str, images: dict, views: dict, refs=None, *, skip=(), front_parts=None,
                    workers: int = FIT_WORKERS, n_refine=None, candidates=None, matte_modes=None, off_axis: bool = False,
                    call_id: str | None = None,
                    log=None) -> dict:
        call_id = self.s.new_call_id("fit_cameras", call_id)
        args = {"reason": reason, "images": {k: str(v) for k, v in images.items()}, "views": views, "refs": refs,
                "skip": list(skip), "front_parts": front_parts, "workers": workers, "matte_modes": matte_modes,
                "off_axis": off_axis}
        t0 = time.perf_counter()
        if not str(reason or "").strip():
            out = _err(call_id, "E_ARGS", "fit_cameras needs a reason (it is recorded in the basis)")
            self.s.record_call("fit_cameras", call_id, args, out)
            return out
        names = list(images)
        if refs not in (None, "all"):
            want = [refs] if isinstance(refs, str) else list(refs)
            by_id = {C.ref_id(n): n for n in names}
            missing = [r for r in want if C.ref_id(r) not in by_id]
            if missing:
                out = _err(call_id, "E_ARGS", f"unknown references {missing}; the job has {list(by_id)}")
                self.s.record_call("fit_cameras", call_id, args, out)
                return out
            names = [by_id[C.ref_id(r)] for r in want]
        exp = self.export_head(call_id)
        if exp.get("status") != "exported":
            out = _err(call_id, exp.get("code") or "E_EXPORT", f"the scene does not export: {exp.get('message')}")
            self.s.record_call("fit_cameras", call_id, args, out)
            return out
        bid = C.next_basis_id(self.root)
        bdir = C.basis_root(self.root) / bid
        bdir.mkdir(parents=True)
        imgs = {n: Path(images[n]) for n in names}
        try:
            self_qos = procs.opt_out_self()
            with procs.DescendantsOptOut() as qos:
                res = fit_views.fit_views(exp["path"], imgs, {n: views[n] for n in names}, bdir, skip=tuple(skip),
                                          front_parts=front_parts, workers=workers, n_refine=n_refine,
                                          candidates=candidates, off_axis=off_axis,
                                          matte_modes={n: (matte_modes or {}).get(n) for n in names},
                                          log=log or (lambda *_: None))
            bs = C.freeze_basis(self.root, bdir, imgs, reason=str(reason), call_id=call_id, source="fit_cameras",
                                model={"glb_path": exp["path"], "glb_sha256": exp["sha256"],
                                       "fingerprint": exp["fingerprint"], "checkpoint": exp["checkpoint"]})
        except Exception as exc:  # noqa: BLE001 - reported to Astra; the unfrozen folder is never a basis
            out = _err(call_id, "E_FIT", f"{type(exc).__name__}: {exc}")
            self.s.record_call("fit_cameras", call_id, args, out)
            return out
        tiles = []
        for rid, rec in bs.refs.items():
            f = rec.get("fit") or {}
            usable = bs.is_usable(rid)
            label = f"{rid} {rec.get('view_kind')} -> {rec.get('view')}: {f.get('status') or 'ok'}"
            if usable and f.get("iou") is not None and f.get("contour_mean_mm") is not None:
                label += f"; IoU {f['iou']:.3f}, contour {f['contour_mean_mm']:.2f} mm"
            if not usable:
                label += "; " + str(bs.unusable_refs()[rid])
            if rec.get("overlay"):
                panel = Image.open(bdir / rec["overlay"]).convert("RGBA")
            else:
                try:
                    panel = Image.fromarray(bs.photo(rid)).convert("RGBA")
                except (ValueError, OSError):
                    panel = Image.new("RGBA", (640, 240), "white")
                    label += "; source photo could not be decoded (no photo or render shown)"
            tiles.append(C.labelled(C.fit_into(panel, 1024, 1024), label))
        sheet = None
        if tiles:
            sheet = str(bdir / "overlays_sheet.png")
            C.grid(tiles, min(3, len(tiles))).convert("RGB").save(sheet)
        out = {"status": "ok" if bs.usable_refs() else "refused", "call_id": call_id,
               "basis": bs.id, "checkpoint": exp["checkpoint"],
               "glb_sha256": exp["sha256"], "export": exp, "sheet": sheet,
               "fit_seconds": round(res.get("seconds", 0.0), 1), "off_axis": off_axis,
               "ecoqos": {"host_opted_out": bool(self_qos), "workers_opted_out": qos.opted_out},
               "refs": {rid: self._fit_ref_summary(rec, usable=bs.is_usable(rid)) for rid, rec in bs.refs.items()},
               "usable_refs": bs.usable_refs(), "unusable_refs": bs.unusable_refs(),
               "seconds": round(time.perf_counter() - t0, 1)}
        if not bs.usable_refs():
            out.update(code="E_FIT_ALL", message="No photo camera fit succeeded; this basis retains the diagnostics. "
                       "The previous usable basis, if any, remains the default.")
        self.s.record_call("fit_cameras", call_id, args, out)
        return out

    @staticmethod
    def _fit_ref_summary(rec, *, usable):
        f, camera = rec.get("fit") or {}, rec.get("camera") or {}

        def number(value, digits):
            return round(value, digits) if isinstance(value, (int, float)) and math.isfinite(value) else None

        return {"view_kind": rec.get("view_kind"), "view": rec.get("view"), "status": f.get("status"),
                "usable": usable, "matte_mode": rec.get("matte_mode"),
                "iou": number(f.get("iou"), 4), "contour_mean_mm": number(f.get("contour_mean_mm"), 3),
                "yaw": number(camera.get("yaw"), 2), "pitch": number(camera.get("pitch"), 2),
                "roll": number(camera.get("roll"), 2), "usability": f.get("usability"),
                **({"camera": camera, "off_axis": f["off_axis"], "sign": f.get("sign")}
                   if f.get("off_axis") is not None else {}),
                **{key: value for key, value in f.items()
                   if key.endswith("_lens_optional") or key == "lens_pixels_optional"},
                **{key: f.get(key, rec.get(key)) for key in ("flags", "code", "refusal", "error", "message", "lens_loss")}}
