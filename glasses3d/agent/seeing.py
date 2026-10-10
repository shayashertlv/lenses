"""The seeing tools over the live session (WP2_DESIGN §6): render_views, compare_to_photos, pick_surface_point and
fit_cameras, plus the head export they share (cached by fingerprint). Stage D wraps these as Astra's function tools.

Every camera is a WP3b camera in a NormFrame (g3d_host/blender_camera): the worker renders its Blender twin in a
temporary scene (g3d/render.py), the host rasters the exported GLB with the same camera (parts pass, compare), and
picking casts the same camera's pixel rays in the worker. Outputs go under ``<job>/work/{renders,compare,exports}``;
camera bases under ``<job>/cameras/basis-K`` (g3d_host/compare). Each call is journaled like the mutating ones.

- ``render_views(views, passes, size_px, background, target)``: views = "canonical" (front, rear, left, right, top,
  bottom, angled_left, angled_right: orthographic, framed to the render-enabled boxes + 5 %), "reference" (every
  ``reference-NN`` of the basis at its frozen camera), "all", or a list of those names and custom
  ``{name, yaw, pitch, roll, perspective, target_mm, width_mm}`` views; target = {"part": name} or
  {"point_mm": [x, y, z], "radius_mm": r} frames the canonical views on a part or a point. Passes: native, clay,
  normal (worker), silhouette (the clay alpha) and parts (the host raster's first-hit part). One labelled contact
  sheet per <= 6 views (<= 2048 px), the frames, and ``manifest.json`` {camera, frame, origin, shape, engine, gpu,
  seconds} that pick_surface_point can reuse.
- ``compare_to_photos(refs, parts, passes, crops, size_px, basis)``: metrics on the frozen basis (compare.py), deltas
  against the previous compare on the same basis with ``model_bytes_identical`` / ``geometry_identical``, one sheet
  per reference (photo | native | clay | overlay), crops, ``work/compare/<call>/report.json``. E_NO_BASIS without a
  basis. With ``rig="studio_dome_v1"`` or ``rig="photo_env"`` it is the photo-matched compare of WP6_FACETS §3.3
  (``_matched_compare``): each photo's temple pose from the basis, the rig, the native_linear (and optional
  gloss_black) pass, a white-balanced (per-channel, linear) native gain and a luma gloss_black gain per job, frozen in
  ``work/compare/gains-<basis>.json`` with the light frame, the photo's acetate p2-p98 stretch on photo and render, a
  temple crop, one stacked image per photo (photo / render / overlay with the model outline, crease polylines and the
  chart grid / gloss_black; a variant-colourway photo gets no native row), compact text (the px/mm of the image
  shown, the points_px frame, outer-edge offsets beside the job's start model's, the current compare_id),
  ``points_px`` charted through the posed raster, and a per-(geometry, photo, rig, passes, environment) cache.
  Default photos (refs None / "default" / "all"): target colourway with a temple ROI of >= 40k px.
  ``photo_env`` = the environment fitted to the photos' front acetate (g3d_host/lighting.py; ``photo_env()``),
  frozen per job in ``work/compare/env-<basis>.json`` with its weights, residuals and photo shas.
- ``pick_surface_point(view, points)``: up to 64 pixel centres on a frozen, canonical or manifest camera ->
  {hit, object, partRole, point_mm, asset_mm, normal, face, distance}.
- ``fit_cameras(reason, images, views, refs=None)``: export the head, ``fit_views`` with 14 workers (EcoQoS opted
  out), freeze ``basis-K+1`` and report each reference's residuals with one overlay sheet.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from ..g3d_host import blender_camera as BC
from ..g3d_host import cameras as CAMS
from ..g3d_host import compare as C
from ..g3d_host import fit_views, fitmesh
from ..g3d_host import lighting as L
from ..g3d_host import pose as PO
from ..g3d_host.camera import Camera, NormFrame, project_mm
from . import procs

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
REF_RE = re.compile(r"^reference-\d+$")
MATCHED_RIG = "studio_dome_v1"
ENV_RIG = "photo_env"
MATCHED_RIGS = (MATCHED_RIG, ENV_RIG)
MATCHED_PASSES = ("native_linear", "gloss_black")
MATCHED_SCHEMA = "g3d.photo_compare/2"
ENV_FIT_MAX_PX = 640           # calibration crops are rendered at <= this long side (the fit cells are ~1 mm)
ENV_ERODE_PX = 12.0            # front region erosion at native scale (at least; 2 x the front's registration error)
ENV_SIGMA_MM, ENV_STEP_MM = 1.0, 1.0
ENV_ITERATIONS = 3             # re-linearised fits, each rendered directly; the best direct residual is kept
ENV_DELTA = 0.5                # differential basis step, x the mean weight at the linearisation point
ENV_DEADLINE_S = 900.0
GAIN_VIEW_MAX_PX = 1600
TEMPLE_ROI_MIN_PX = 40_000
DARK_L8 = 80.0                 # a colourway whose photo acetate median luma is below this is dark (black acetate)
GLOSS_TARGET_L8 = 40.0         # without a dark photo, gloss_black is exposed so the front acetate reads this luma
GLOSS_FIXED_STRETCH = (0.0, 128.0)
GRID_STATION_MM = 10.0


class SeeingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _err(call_id: str, code: str, message: str) -> dict:
    return {"status": "error", "call_id": call_id, "code": code, "message": message}


class Seeing:
    def __init__(self, session) -> None:
        self.s = session
        self.root = Path(session.root)
        self.work = self.root / "work"
        self._bounds: tuple[str, dict] | None = None
        self._meshes: OrderedDict = OrderedDict()

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
        """The head exported to ``work/exports/head-<fp16>.glb`` (cached by fingerprint across sessions)."""
        call_id = call_id or self.s.new_call_id("export_head")
        head = self.head
        fp = head["fingerprint"]
        d = self.work / "exports"
        index_path = d / "index.json"
        index = procs.read_json(index_path) or {}
        rec = index.get(fp)
        if rec and Path(rec["path"]).is_file() and C.sha256_file(rec["path"]) == rec["sha256"]:
            return {**rec, "cached": True}
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
        index[fp] = rec
        procs.write_json_atomic(index_path, index)
        return {**rec, "cached": False}

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
        """Each view as {name, kind, camera, frame, origin, shape, px_per_mm[, ref]}."""
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
                expanded += list(basis.refs)
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
            elif isinstance(it, str) and (REF_RE.match(it) or Path(it).stem.startswith("reference-")):
                basis = basis or C.load_basis(self.root, basis_id)
                rid = C.ref_id(it)
                if rid not in basis.refs:
                    raise SeeingError("E_ARGS", f"{basis.id} has no {rid}; references: {list(basis.refs)}")
                cam0, (H, W) = basis.camera(rid), basis.shape(rid)
                k = min(1.0, size_px / max(H, W))
                cam = BC.crop_camera(cam0, 0.0, 0.0, k) if k < 1.0 else cam0
                shape = (max(1, round(H * k)), max(1, round(W * k)))
                rec = {"name": rid, "kind": "reference", "camera": cam, "frame": basis.frame, "origin": origin,
                       "shape": shape, "scale_k": k, "basis": basis.id, "ref": rid,
                       "px_per_mm": BC.px_per_mm_at(cam, basis.frame, basis.pivot_mm)}
            else:
                raise SeeingError("E_ARGS", f"unknown view {it!r}: use {list(BC.CANONICAL_VIEWS)}, reference-NN, "
                                            "'canonical', 'reference', 'all' or a custom {yaw, pitch, ...} view")
            if not NAME_RE.match(rec["name"]) or rec["name"] in names:
                raise SeeingError("E_ARGS", f"view name {rec['name']!r} is invalid or repeated")
            names.add(rec["name"])
            try:
                rec["blender"] = BC.to_blender(rec["camera"], rec["frame"], origin, rec["shape"])
            except ValueError as exc:
                raise SeeingError("E_ARGS", f"view {rec['name']}: {exc}") from None
            out.append(rec)
        if not out:
            raise SeeingError("E_ARGS", "no views requested")
        return out

    def _light_frame(self, call_id: str) -> dict:
        b = self.bounds(call_id)
        lo, hi = np.asarray(b["lo"], float), np.asarray(b["hi"], float)
        return {"center": ((lo + hi) / 2).tolist(), "radius": float(np.linalg.norm(hi - lo) / 2)}

    def _render(self, tool: str, call_id: str, views: list[dict], out_dir: Path, *, engine: str = "auto",
                samples: int = 16, simulate_eevee_failure: bool = False) -> dict:
        params = {"out_dir": str(out_dir), "engine": engine, "samples": int(samples),
                  "light_frame": self._light_frame(call_id),
                  "views": [{"name": v["name"], "camera": v["blender"], "passes": v["worker_passes"]}
                            for v in views if v["worker_passes"]]}
        if simulate_eevee_failure:
            params["simulate_eevee_failure"] = True
        if not params["views"]:
            return {"status": "ok", "result": {"views": [], "engine_final": None, "gpu": None, "fallback": None,
                                               "fingerprint_before": self.head["fingerprint"], "seconds": 0.0}}
        return self.s.call_read(tool, "render", params, deadline_s=RENDER_DEADLINE_S, call_id=call_id)

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
                tiles.append((f"{v['name']} ({v['kind']}, {v['px_per_mm']:.2f} px/mm) " + " | ".join(
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
                    "sheets": sheets, "parts_legend": legend_all, "notes": notes,
                    "worker_seconds": res.get("seconds"), "seconds": round(time.perf_counter() - t0, 3)}
        mp = out_dir / "manifest.json"
        C.write_json(mp, manifest)
        out = {"status": "ok", "call_id": call_id, "sheets": sheets, "manifest": str(mp),
               "views": {n: {"kind": m["kind"], "shape": m["shape"], "px_per_mm": round(m["px_per_mm"], 4),
                             "files": {p: f["path"] for p, f in m["files"].items()}}
                         for n, m in manifest_views.items()},
               "engine": res.get("engine_final"), "fallback": res.get("fallback"), "gpu": res.get("gpu"),
               "restored_to": r.get("restored_to"), "notes": notes, "seconds": manifest["seconds"]}
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
    def compare_to_photos(self, refs="all", parts="all", passes=DEFAULT_PASSES, crops=None, size_px: int = 1024,
                          basis: str | None = None, call_id: str | None = None, *, rig: str = "photo_v1",
                          crop: str = "temple", points_px: dict | None = None, hinge_z_mm: float | None = None,
                          gains: dict | None = None, boxes: dict | None = None, use_cache: bool = True,
                          env: dict | None = None) -> dict:
        if rig != "photo_v1":
            return self._matched_compare(refs, passes, crop, basis, call_id, rig=rig, points_px=points_px,
                                         hinge_z_mm=hinge_z_mm, gains_override=gains, boxes=boxes,
                                         use_cache=use_cache, env_override=env)
        call_id = self.s.new_call_id("compare_to_photos", call_id)
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
            rids = self._ref_ids(bs, refs)
            crops = list(crops or [])
            size_px = int(size_px)
            if not SIZE_RANGE[0] <= size_px <= SIZE_RANGE[1]:
                raise SeeingError("E_ARGS", f"size_px must be {SIZE_RANGE[0]}..{SIZE_RANGE[1]}")
            out_dir = self._prepare_work_dir(self.work / "compare" / call_id)
        except C.NoBasis as exc:
            out = _err(call_id, "E_NO_BASIS", str(exc))
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        except (SeeingError, ValueError, OSError) as exc:
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
                  "glb": {k: exp.get(k) for k in ("path", "sha256", "bytes", "objects", "triangles")},
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
               "report": str(rp), "engine": report["engine"], "seconds": secs}
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
        if refs in (None, "all"):
            return list(bs.refs)
        items = [refs] if isinstance(refs, (str, int)) else list(refs)
        names = list(bs.refs)
        out = []
        for it in items:
            rid = names[int(it) - 1] if isinstance(it, int) and 1 <= int(it) <= len(names) else C.ref_id(str(it))
            if rid not in bs.refs:
                raise SeeingError("E_ARGS", f"{bs.id} has no reference {it!r}; references: {names}")
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

    @staticmethod
    def _acetate_ids(mesh, roles) -> tuple[int | None, list]:
        """(front body part, the acetate bodies: the front and the temple bodies, by the one body rule of
        g3d_host/pose (= g3d/render.py acetate_bodies); the exporter gives each region its own glTF material)."""
        fid = PO.front_body(mesh, roles)
        if fid is None:
            return None, []
        return fid, [fid] + [b for b in (PO.temple_body(mesh, s, roles) for s in (1, -1)) if b is not None]

    @staticmethod
    def gain_views(bs: C.Basis, origin) -> tuple[dict, list]:
        """Per colourway its best-registered photo (highest fit IoU) at <= GAIN_VIEW_MAX_PX: ({colourway: {rid, k,
        cam, shape, pose}}, worker views rendering both matched passes)."""
        by_cw: dict = {}
        for rid in bs.refs:
            cw = bs.colourway(rid)
            iou = float((bs.refs[rid].get("fit") or {}).get("iou") or 0.0)
            if cw not in by_cw or iou > by_cw[cw][0]:
                by_cw[cw] = (iou, rid)
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
        """(photo, matte) of the box (x0, y0, x1, y1) of a basis photo at the scale k of a crop camera (LANCZOS for
        the photo, area-like bilinear for the matte; never upscaled)."""
        x0, y0, x1, y1 = box
        pc, mc = bs.photo(rid)[y0:y1, x0:x1], bs.matte(rid)[y0:y1, x0:x1]
        if k < 1.0:
            size = (shape[1], shape[0])
            pc = np.asarray(Image.fromarray(pc).resize(size, Image.LANCZOS))
            mc = np.asarray(Image.fromarray(mc.astype(np.float32)).resize(size, Image.BILINEAR)) > 0.5
        return pc, mc

    def _render_params(self, rig: str, env: dict | None, light_frame: dict | None) -> dict:
        """The worker parameters a matched rig needs (photo_env: the frozen lobe weights and float output)."""
        if rig == ENV_RIG:
            return {"rig": rig, "env": L.env_param(env), "linear_format": "float", "engine": "auto"}
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
        for rid in bs.refs:
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
            if lam is None:
                lam, cv_table = L.cv_lambda(B, rhs, groups)
            fit = L.fit_env(B, rhs, groups, lam)
            w1 = np.asarray(fit["weights"])
            direct = self._env_render(call_id, cals, {"w": w1}, origin, out_dir, f"direct-{it}")
            f1 = cells({c["rid"]: direct[(c["rid"], "w")] for c in cals})
            res = L.residual_stats(f1, y, groups)
            iterations.append({"iteration": it, "linearised_at_sha": L.weights_sha(w0), "delta": delta,
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
        if (bs.refs[rid].get("view_kind") or "") not in ("top", "rear"):
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
                         boxes=None, use_cache=True, env_override=None) -> dict:
        """The photo-matched compare (module doc). ``gains`` / ``env`` override the job's frozen gains /
        environment (one normalisation across jobs, e.g. an owner sheet); ``boxes`` = {photo: {"box": [x0, y0, x1,
        y1], "k": scale, "stretch": [lo, hi] (optional)}} fixes crops (and stretches) so several models share
        them."""
        call_id = self.s.new_call_id("compare_to_photos", call_id)
        args = {"refs": refs, "passes": list(passes or ()), "crop": crop, "basis": basis, "rig": rig,
                "points_px": points_px, "hinge_z_mm": hinge_z_mm, "gains": "override" if gains_override else None,
                "env": "override" if env_override else None, "boxes": boxes}
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
            for rid in bs.refs:
                if bs.pose(rid) is None:
                    raise SeeingError("E_NO_POSE", f"{bs.id} has no temple pose for {rid}; the photo-matched compare "
                                                   "needs a WP6 basis (theta_deg, hinge_axis_xz_mm)")
            want = None if refs in (None, "default", "all") else self._ref_ids(bs, refs)
            out_dir = self._prepare_work_dir(self.work / "compare" / call_id)
        except C.NoBasis as exc:
            out = _err(call_id, "E_NO_BASIS", str(exc))
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        except (SeeingError, ValueError, OSError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_ARGS"), str(exc))
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        exp = self.export_head(call_id)
        if exp.get("status") != "exported":
            out = _err(call_id, exp.get("code") or "E_EXPORT",
                       f"the scene does not export, so it cannot be compared: {exp.get('message')}")
            self.s.record_call("compare_to_photos", call_id, args, out)
            return out
        try:
            out = self._matched_body(bs, exp, want, passes, crop, rig, points_px, hinge_z_mm, gains_override,
                                     boxes, use_cache, env_override, call_id, out_dir, t0, secs)
        except (SeeingError, ValueError) as exc:
            out = _err(call_id, getattr(exc, "code", "E_COMPARE"), str(exc))
        self.s.record_call("compare_to_photos", call_id, args, out)
        return out

    def _matched_body(self, bs, exp, want, passes, crop, rig, points_px, hinge_z_mm, gains_override, boxes,
                      use_cache, env_override, call_id, out_dir, t0, secs) -> dict:
        mesh = self.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
        roles = PO.part_roles(exp["path"])
        gkey = C.geometry_key(mesh)
        origin = np.asarray(self.bounds(call_id)["origin_mm"] or (0.0, 0.0, 0.0), float)
        bodies = {s: PO.temple_body(mesh, s, roles) for s in (1, -1)}
        if hinge_z_mm is None:
            zs = [float(mesh.V[np.unique(mesh.F[mesh.part == b])][:, 2].max()) for b in bodies.values()
                  if b is not None]
            hinge_z_mm = float(np.mean(zs)) if zs else 0.0
        t1 = time.perf_counter()
        geo = {}
        for rid in (want or [r for r in bs.refs if bs.colourway(r) == "target"]):
            geo[rid] = self._matched_geometry(bs, rid, mesh, roles)
        rids = want or [rid for rid, g in geo.items() if g["temple_px"] >= TEMPLE_ROI_MIN_PX]
        if not rids:
            raise SeeingError("E_ARGS", "no target photo shows >= 40k temple pixels; name the photos")
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
        recs, todo = {}, []
        for rid in rids:
            g = geo[rid]
            matte = bs.matte(rid)
            if boxes and rid in boxes:
                box, k = tuple(int(v) for v in boxes[rid]["box"]), min(1.0, float(boxes[rid]["k"]))
            else:
                box = C.crop_box(g["temple"], matte) if crop == "temple" else C._box(g["mask"] | matte, C.CROP_PAD,
                                                                                        g["shape"])
                if box is None:
                    box = (0, 0, g["shape"][1], g["shape"][0])
                k = C.stack_scale(box[2] - box[0], box[3] - box[1], 3 + (1 if "gloss_black" in passes else 0))
            x0, y0, x1, y1 = box
            vpasses = passes if bs.colourway(rid) == "target" else list(MATCHED_PASSES)
            key = hashlib.sha256(json.dumps(
                {"schema": MATCHED_SCHEMA, "geometry": gkey, "rid": rid, "rig": rig, "passes": vpasses,
                 "crop": crop, "box": box, "k": k, "stretch": (boxes or {}).get(rid, {}).get("stretch"),
                 "pose": g["pose"], "hinge_z": round(hinge_z_mm, 6), "frame": bs.frame.to_dict(),
                 "camera": bs.refs[rid]["camera"], "gains": gains.get("colourways"),
                 "gloss": gains.get("gloss_black"), "light": gains.get("light_frame"),
                 "env": None if env is None else L.weights_sha(env["weights"])},
                sort_keys=True, default=C._json_default).encode()).hexdigest()[:24]
            cdir = self.work / "compare" / "cache" / key
            rec = procs.read_json(cdir / "record.json") if use_cache else None
            if rec and Path(rec["image"]).is_file():
                rec["cached"] = True
                recs[rid] = rec
                continue
            ccam = BC.crop_camera(g["cam"], x0, y0, k)
            cshape = (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))
            g.update(box=box, k=k, ccam=ccam, cshape=cshape, cdir=cdir, key=key, passes=vpasses,
                     stretch=(boxes or {}).get(rid, {}).get("stretch"))
            todo.append(rid)
        t2 = time.perf_counter()
        files, render_meta = {}, None
        if todo:
            views = [{"name": rid, "camera": BC.to_blender(geo[rid]["ccam"], bs.frame, origin, geo[rid]["cshape"]),
                      "passes": geo[rid]["passes"], "pose": geo[rid]["pose"]} for rid in todo]
            rr = self.s.call_read("compare_to_photos", "render", {
                "out_dir": str(out_dir / "renders"), "views": views,
                **self._render_params(rig, env, gains.get("light_frame"))},
                deadline_s=RENDER_DEADLINE_S, call_id=call_id)
            if rr["status"] != "ok":
                return rr
            files = {v["name"]: v["files"] for v in rr["result"]["views"]}
            render_meta = {k: rr["result"].get(k) for k in ("engine_final", "fallback", "gpu", "rig_spec",
                                                            "acetate_materials", "unchanged", "samples",
                                                            "linear_format")}
        secs["renders"] = round(time.perf_counter() - t2, 3)
        t3 = time.perf_counter()
        for rid in todo:
            recs[rid] = self._matched_photo(bs, rid, geo[rid], mesh, roles, bodies, fid, acetate, gains,
                                            geo[rid]["passes"], files[rid], hinge_z_mm)
            recs[rid]["render"] = render_meta
            C.write_json(geo[rid]["cdir"] / "record.json", recs[rid])
            recs[rid]["cached"] = False
        for rid in rids:
            recs[rid]["compare_id"] = call_id
            start = self._start_offsets(bs, rid, exp, gkey, hinge_z_mm)
            recs[rid]["start_offsets_mm"] = start
            recs[rid]["text"] = self._matched_text(recs[rid], start, call_id)
            if rid in points_px:
                g = geo[rid]
                recs[rid]["points"] = PO.chart_at(points_px[rid], g["cam"], g["mesh"], g["pose"], roles, hinge_z_mm)
        secs["assemble"] = round(time.perf_counter() - t3, 3)
        secs["total"] = round(time.perf_counter() - t0, 3)
        report = {"schema": MATCHED_SCHEMA, "compare_id": call_id, "created": C._utc(), "basis": bs.summary(),
                  "checkpoint": self.head["id"], "fingerprint": exp["fingerprint"],
                  "glb": {k: exp.get(k) for k in ("path", "sha256", "bytes")}, "geometry": gkey, "rig": rig,
                  "passes": passes, "crop": crop, "hinge_z_mm": hinge_z_mm, "gains": gains,
                  "environment": None if env is None else {k: env.get(k) for k in (
                      "weights", "weights_sha", "lambda", "chosen_iteration", "path", "calibration")},
                  "temple_px": {rid: g["temple_px"] for rid, g in geo.items()}, "photos": recs,
                  "note": C.CAMERA_NOTE, "seconds": secs}
        rp = out_dir / "report.json"
        C.write_json(rp, report)
        return {"status": "ok", "compare_id": call_id, "basis": bs.id, "checkpoint": self.head["id"], "rig": rig,
                "passes": passes, "photos": {rid: {"image": recs[rid]["image"], "text": recs[rid]["text"],
                                                   "rows": recs[rid].get("rows"), "cached": recs[rid]["cached"],
                                                   **({"points": recs[rid]["points"]} if "points" in recs[rid]
                                                      else {})} for rid in rids},
                "note": C.CAMERA_NOTE, "report": str(rp), "seconds": secs}

    @staticmethod
    def _matched_text(rec: dict, start: dict | None, call_id: str) -> str:
        """The compact text of one photo (composed on every call, so a cached record names the current
        compare_id): scale of the image shown and the points_px frame, IoU, outer-edge offsets with the start
        model's in brackets, the compare_id and the camera note."""
        x0, y0, x1, y1 = rec["crop"]["box"]
        k = rec["crop"]["k"]
        lines = [rec["text_head"],
                 f"scale: {rec['px_per_mm'] * k:.2f} px/mm in this image ({rec['px_per_mm']:.2f} px/mm in the native "
                 f"photo x crop scale {k:.3f}). points_px are native photo pixels; this image shows the native box "
                 f"[{x0},{y0},{x1},{y1}]: u_img = (u - {x0} + 0.5) x {k:.3f} - 0.5, v_img = (v - {y0} + 0.5) x "
                 f"{k:.3f} - 0.5"]
        offs = rec.get("outer_edge_offsets_mm")
        if offs:
            for s, rows_ in offs.items():
                srows = {r["d_mm"]: r["offset_mm"] for r in ((start or {}).get(s) or [])} if isinstance(
                    start, dict) and "missing" not in start else {}

                def fmt(v):
                    return "n/a" if v is None else f"{v:+.2f}"
                vals = " ".join(f"{o['d_mm']:g}:{fmt(o['offset_mm'])}"
                                f"({fmt(srows.get(o['d_mm'])) if srows else 'n/a'})" for o in rows_)
                lines.append(f"outer edge, photo minus model (mm; start model in brackets) side {s} at d: {vals}")
        lines.append(f"compare_id {call_id}. {C.CAMERA_NOTE}")
        return "\n".join(lines)

    def _matched_photo(self, bs: C.Basis, rid: str, g: dict, mesh, roles, bodies, fid, acetate, gains, passes,
                       files: dict, hinge_z: float) -> dict:
        """One photo's stacked image (photo / render / overlay / gloss_black; a variant-colourway photo has no
        native row, the model's material being the target colourway) and its compact numbers."""
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
        ac = C.erode_px(np.isin(part, acetate) & mc, 2)
        if ac.sum() < 200:
            ac = mc & r["mask"]
        if g.get("stretch"):
            lo, hi = (float(v) for v in g["stretch"])
        else:
            lo, hi = C.stretch_range(pc, ac)
        bg = C.photo_background(photo, matte)
        rows = [("photo", f"{rid} photo (stretch {lo:.0f}-{hi:.0f})", Image.fromarray(C.apply_stretch(pc, lo, hi)))]
        gain = gains["colourways"]["target"]["native_linear"]["gain"]
        native = cw == "target"
        if native:
            nat = C.read_linear(files["native_linear"]["path"])
            rows.append(("native_linear", "render native_linear (white-balanced gain R/G/B "
                         + "/".join(f"{v:.2f}" for v in np.atleast_1d(gain)) + "), same stretch",
                         Image.fromarray(C.apply_stretch(C.composite_render(nat[0], nat[1], gain, bg), lo, hi))))
        # chart grid on the temple bodies and creases of the temple bodies
        side_c = np.zeros(cshape, np.int8)
        body_ids = [b for b in bodies.values() if b is not None]
        for s, b in bodies.items():
            if b is not None:
                side_c[part == b] = s
        Q = PO.unpose_points(np.nan_to_num(r["points"].reshape(-1, 3)), side_c.ravel(), g["pose"])
        ch = PO.chart_of(Q, side_c.ravel(), hinge_z).reshape(cshape + (3,))
        grid = C.iso_lines(ch[..., 0], ch[..., 1], side_c != 0, side_c)
        edges = [C.crease_edges(mp.V, mp.F[mp.part == b]) for b in body_ids]
        edges = {key: np.concatenate([e[key] for e in edges]) for key in edges[0]} if edges else \
            C.crease_edges(np.zeros((0, 3)), np.zeros((0, 3), int))
        polys = C.crease_polylines(edges, ccam, bs.frame, r["depth"])
        base = C.apply_stretch(pc, lo, hi)
        rows.append(("overlay", "overlay: outline magenta, ridge orange, valley blue, grid d/10 mm y/5 mm",
                     C.overlay_matched(base, r["mask"], polys, grid)))
        gl_rec = gains["gloss_black"]
        gloss_used = None
        if "gloss_black" in passes:
            gl = C.read_linear(files["gloss_black"]["path"])
            dark = bool(gcw.get("dark"))
            glo, ghi = (lo, hi) if dark else tuple(gl_rec["stretch"])
            gloss_used = {"gain": gl_rec["gain"], "stretch": [glo, ghi], "stretch_is_photo": dark}
            rows.append(("gloss_black", f"render gloss_black (gain {gl_rec['gain']:.2f}, stretch {glo:.0f}-{ghi:.0f}"
                         f"{' = the photo' if dark else ' fixed for the job'})",
                         Image.fromarray(C.apply_stretch(C.composite_render(gl[0], gl[1], gl_rec["gain"], bg),
                                                         glo, ghi))))
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
        region = ndimage.binary_dilation(g["temple"], iterations=20)
        a, b = g["mask"] & region, matte & region
        iou = float((a & b).sum() / max(1, (a | b).sum()))
        offsets = None
        if (bs.refs[rid].get("view_kind") or "") in ("top", "rear"):
            offsets = self._outer_offsets(bs, rid, g, mesh, bodies, hinge_z)
        head = (f"{rid} ({bs.refs[rid].get('view_kind')}, {cw}, {bs.refs[rid].get('fit_status') or 'fitted'}): "
                f"temple IoU {iou:.3f}" + ("" if native else "; no native row (the model's material is the target "
                                                               "colourway, this photo is a variant)"))
        return {"image": str(ip), "rows": row_files, "text_head": head, "rid": rid,
                "view_kind": bs.refs[rid].get("view_kind"),
                "colourway": cw, "pose": g["pose"], "crop": {"box": [x0, y0, x1, y1], "k": k,
                                                              "camera": ccam.to_dict(), "shape": list(cshape)},
                "px_per_mm": ppm, "px_per_mm_shown": ppm * k, "temple_iou": iou, "outer_edge_offsets_mm": offsets,
                "gain": gain, "stretch": [lo, hi], "gloss": gloss_used, "background": list(bg),
                "creases": {"ridge": int(sum(1 for _, s in polys if s > 0)),
                            "valley": int(sum(1 for _, s in polys if s < 0))},
                "renders": files}

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
                    workers: int = FIT_WORKERS, n_refine=None, candidates=None, call_id: str | None = None,
                    log=None) -> dict:
        call_id = self.s.new_call_id("fit_cameras", call_id)
        args = {"reason": reason, "images": {k: str(v) for k, v in images.items()}, "views": views, "refs": refs,
                "skip": list(skip), "front_parts": front_parts, "workers": workers}
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
                                          candidates=candidates, log=log or (lambda *_: None))
        except Exception as exc:  # noqa: BLE001 - reported to Astra; the unfrozen folder is never a basis
            out = _err(call_id, "E_FIT", f"{type(exc).__name__}: {exc}")
            self.s.record_call("fit_cameras", call_id, args, out)
            return out
        bs = C.freeze_basis(self.root, bdir, imgs, reason=str(reason), call_id=call_id, source="fit_cameras",
                            model={"glb_path": exp["path"], "glb_sha256": exp["sha256"],
                                   "fingerprint": exp["fingerprint"], "checkpoint": exp["checkpoint"]})
        tiles = []
        for rid, rec in bs.refs.items():
            if rec.get("overlay"):
                f = rec["fit"]
                tiles.append(C.labelled(C.fit_into(Image.open(bdir / rec["overlay"]).convert("RGBA"), 1024, 1024),
                                        f"{rid} {rec['view_kind']} -> {rec['view']}: IoU {f['iou']:.3f}, contour "
                                        f"{f['contour_mean_mm']:.2f} mm"))
        sheet = None
        if tiles:
            sheet = str(bdir / "overlays_sheet.png")
            C.grid(tiles, min(3, len(tiles))).convert("RGB").save(sheet)
        out = {"status": "ok", "call_id": call_id, "basis": bs.id, "checkpoint": exp["checkpoint"],
               "glb_sha256": exp["sha256"], "sheet": sheet, "fit_seconds": round(res.get("seconds", 0.0), 1),
               "ecoqos": {"host_opted_out": bool(self_qos), "workers_opted_out": qos.opted_out},
               "refs": {rid: {"view_kind": rec["view_kind"], "view": rec["view"], "status": rec["fit"]["status"],
                              "iou": round(rec["fit"]["iou"], 4),
                              "contour_mean_mm": round(rec["fit"]["contour_mean_mm"], 3),
                              "flags": rec["fit"].get("flags"),
                              "yaw": round(rec["camera"]["yaw"], 2), "pitch": round(rec["camera"]["pitch"], 2)}
                        for rid, rec in bs.refs.items()},
               "seconds": round(time.perf_counter() - t0, 1)}
        self.s.record_call("fit_cameras", call_id, args, out)
        return out
