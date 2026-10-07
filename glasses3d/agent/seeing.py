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
  basis.
- ``pick_surface_point(view, points)``: up to 64 pixel centres on a frozen, canonical or manifest camera ->
  {hit, object, partRole, point_mm, asset_mm, normal, face, distance}.
- ``fit_cameras(reason, images, views, refs=None)``: export the head, ``fit_views`` with 14 workers (EcoQoS opted
  out), freeze ``basis-K+1`` and report each reference's residuals with one overlay sheet.
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PIL import Image

from ..g3d_host import blender_camera as BC
from ..g3d_host import compare as C
from ..g3d_host import fit_views, fitmesh
from ..g3d_host.camera import Camera, NormFrame
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
                          basis: str | None = None, call_id: str | None = None) -> dict:
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
            cr["sheet"] = self._crop_sheet(bs, cr, (masks.get(cr["ref"]) or {}).get("mask"), mesh, img(cr["view"]["name"], "native"),
                                           out_dir)
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
