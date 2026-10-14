"""Generic job tools over the journaled Blender scene. No model calls or product-specific defaults."""
import asyncio
import base64
from contextlib import contextmanager
import ctypes
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time
from typing import Literal

from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText, default_tool_timeout_error_message
from agents.tool_context import ToolContext
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict

from ..journal import CheckpointMismatch, WorkerFailed
from ..loop import STAGE_FATAL, fatal_tool_errors, footer_text, sdk_timeout_s, hard_timeout_s
from ..seeing import VIEWS_PER_SHEET
from ..worker_client import WorkerDied
from . import arview
from .images import compact_sheet, MAX_IMAGE_SIDE, IMAGE_POLICY
from .seating import SEAT_TOOL_NAMES, seat_tools
from .temple import temple_tool
from .photo_print import photo_print_tool

TOOL_NAMES = ("execute_blender_code", "scene_summary", "save_checkpoint", "list_checkpoints",
              "restore_checkpoint", "fit_cameras", "render_views", "compare_to_photos", "pick_surface_point",
              "export_glb", "arview_capture", "read_image", "budget_status", "finish") + SEAT_TOOL_NAMES + ("temple_stage", "photo_print")
FATAL = (WorkerFailed, CheckpointMismatch, WorkerDied, *STAGE_FATAL)


class CustomView(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str
    yaw: float
    pitch: float
    roll: float = 0.0
    width_mm: float | None = None
    target_mm: list[float] | None = None
    perspective: float = 0.0


def free_commit_bytes() -> int:
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total_phys", "avail_phys", "total_page", "avail_page",
                                                       "total_virtual", "avail_virtual", "avail_extended")]
        state = Memory()
        state.length = ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            raise OSError("cannot measure free commit for the camera pool")
        return int(state.avail_page)
    import psutil
    return int(psutil.virtual_memory().available + psutil.swap_memory().free)


def fit_workers(requested: int | None = None) -> int:
    """Leave 2 GiB and two CPUs for Blender/host; fits use about 1.3 GiB per process (AGENTS.md)."""
    maximum = max(1, min(16, max(1, (os.cpu_count() or 1) - 2),
                         math.floor((free_commit_bytes() / 2**30 - 2) / 1.3)))
    if requested is not None and (type(requested) is not int or requested < 1):
        raise ValueError("workers must be a positive integer or null")
    return min(maximum, requested) if requested is not None else maximum


@contextmanager
def single_threaded_blas():
    """Spawned fit workers each use one BLAS thread; the CLI sets these before importing host numeric libraries."""
    names = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = "1"
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _image(root, path, *, region=None, trusted_output=False) -> tuple[dict, str]:
    areas = ("images", "reference_images", "work", "cameras") if trusted_output else ("images", "reference_images", "work")
    source = arview.confined(root, path, areas=areas)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        original_size = image.size
        if region is not None:
            if (len(region) != 4 or any(type(x) is not int for x in region)
                    or not 0 <= region[0] < region[2] <= image.width
                    or not 0 <= region[1] < region[3] <= image.height):
                raise ValueError("region is [left, top, right, bottom] inside the oriented image")
            image = image.crop(region)
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    delivered = arview.confined(root, Path("work/tool_images") / f"{digest}.png")
    delivered.parent.mkdir(parents=True, exist_ok=True)
    if not delivered.is_file() or arview.sha256(delivered) != digest:
        delivered.write_bytes(data)
    meta = {"path": delivered.relative_to(Path(root).resolve()).as_posix(), "sha256": digest,
            "source_path": source.relative_to(Path(root).resolve()).as_posix(), "source_sha256": arview.sha256(source),
            "size_px": list(image.size), "source_size_px": list(original_size), "region": region}
    return meta, "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _sheets(result: dict, name: str) -> list[dict]:
    records = list(result.get("images") or [])
    if result.get("sheet"):
        records.append({"path": result["sheet"], "view": name})
    rendered_views = [(f"reference:{rec['ref']}" if rec.get("ref") else
                       f"canonical:{key}" if rec.get("kind") == "canonical" else key)
                      for key, rec in (result.get("views") or {}).items()]
    for i, path in enumerate(result.get("sheets") or []):
        views = rendered_views[i * VIEWS_PER_SHEET:(i + 1) * VIEWS_PER_SHEET] if name == "render_views" else []
        records.append({"path": path, "view": ", ".join(views) if views else f"{name} sheet {i + 1}",
                        **({"views": views} if views else {})})
    for ref, rec in (result.get("refs") or {}).items():
        if isinstance(rec, dict) and rec.get("sheet"):
            records.append({"path": rec["sheet"], "view": ref})
    for i, rec in enumerate(result.get("crops") or []):
        if rec.get("sheet"):
            records.append({"path": rec["sheet"], "view": f"{rec.get('ref', '')} crop {i + 1}"})
    unique = {}
    for rec in records:
        unique.setdefault(str(rec["path"]), rec)
    return list(unique.values())


def _output(scene, call_id, name, result):
    records, images = [], []
    for rec in _sheets(result, name):
        meta, url = _image(scene.root, rec["path"], region=rec.get("region"), trusted_output=name != "read_image")
        record = {**rec, **meta, "call_id": call_id, "kind": rec.get("kind", name),
                  "checkpoint": rec.get("checkpoint") or scene.s.head["id"],
                  "view": rec.get("view") or name, "output_index": len(images) + 2}
        if name == "read_image":
            if Path(meta["source_path"]).parts[0] in {"images", "reference_images"}:
                record.update(context_role="reference", checkpoint="source", revision=f"source:{meta['source_sha256'][:12]}")
            else:
                prior_images = [panel for item in scene.images for panel in [item, *item.get("panels", [])]]
                previous = next((item for item in reversed(prior_images)
                                 if meta["source_path"] in (item.get("path"), item.get("source_path"))), None)
                record["context_role"] = ("reference" if previous.get("context_role") == "reference" else "detail") if previous else "unknown"
                record["checkpoint"] = previous["checkpoint"] if previous else f"source:{meta['source_sha256'][:12]}"
                if previous:
                    record["view"] = previous["view"]
                    record["views"] = previous.get("views") or [previous["view"]]
                    if previous.get("revision"):
                        record["revision"] = previous["revision"]
        records.append(record)
    if len(records) > 1:
        sources = records
        panels = []
        for record in sources:
            with Image.open(arview.confined(scene.root, record["path"])) as opened:
                panels.append((opened.copy(), record["view"]))
        sheet = compact_sheet(panels)
        buf = io.BytesIO()
        sheet.save(buf, format="PNG")
        payload = buf.getvalue()
        path = Path("work/tool_images") / (hashlib.sha256(payload).hexdigest() + ".png")
        arview.confined(scene.root, path).write_bytes(payload)
        meta, url = _image(scene.root, path, trusted_output=True)
        records = [{**meta, "call_id": call_id, "kind": name, "checkpoint": scene.s.head["id"],
                    "view": ", ".join(record["view"] for record in sources),
                    "views": [record["view"] for record in sources], "panels": sources, "output_index": 2}]
    # Only deduplicate bytes delivered during this live scene session, not images from prior runs.
    emitted = getattr(scene, "_emitted_images", None)
    if emitted is None:
        emitted = {}
    emitted = dict(emitted)
    for record in records:
        meta, url = _image(scene.root, record["path"], trusted_output=True)
        label = (f"Image: call {call_id}; checkpoint {record['checkpoint']}; "
                 f"revision {record.get('revision') or record['checkpoint']}; view {record['view']}; "
                 f"sha256 {record['sha256'][:12]}.")
        unseen = getattr(scene, "_unseen", getattr(scene, "unseen", ()))
        if record["sha256"] in emitted and emitted[record["sha256"]] not in unseen:
            record.update(reused_from_call=emitted[record["sha256"]], output_index=None)
            images.append(ToolOutputText(text=label + f" Same image already delivered in call {record['reused_from_call']}."))
        else:
            emitted[record["sha256"]] = call_id
            images.extend([ToolOutputText(text=label), ToolOutputImage(image_url=url, detail="high")])
    if records:
        result["images"] = records
        result["image_policy"] = IMAGE_POLICY
        if name == "finish" and result.get("image_source") == "arview":
            result["arview"]["images"] = records
        scene.record_images(call_id, result)
    output = [ToolOutputText(text=json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)), *images]
    scene._emitted_images = emitted
    return output


def _finish_export_failures(scene, export):
    """All existing export gates, with byte/head integrity separate from quality diagnostics."""
    head = scene.s.head
    failures = []
    bound = not (export.get("status") != "exported" or export.get("drift")
            or export.get("checkpoint") != head["id"] or export.get("fingerprint") != head["fingerprint"]
            or export.get("validation_sha256") != export.get("sha256"))
    if bound:
        try:
            bound = arview.sha256(arview.confined(scene.root, export["path"])) == export["sha256"]
        except (OSError, ValueError, KeyError):
            bound = False
    if not bound:
        failures.append({"check": "export", "code": "E_EXPORT", "message":
                         export.get("message") or "export bytes or identity do not match the current checkpoint"})
    for key, label, code in (("validate_spec", "specification", "E_SPEC"), ("seat_check", "seating", "E_SEATING"),
                              ("print_check", "print", "E_PRINT")):
        report = export.get(key)
        if (not isinstance(report, dict) or report.get("ok") is not True
                or key == "print_check" and report.get("glb_sha256") != export.get("sha256")):
            failures.append({"check": label, "code": code, "report": report})
    return bound, failures


def build_tools(scene, settings=None) -> list:
    """Build without starting Blender; the exact registry is available for estimation and approval."""
    def timeout_text(name):
        def result(ctx, error):
            seconds = getattr(error, "timeout_seconds", None) or sdk_timeout_s(name)
            text = default_tool_timeout_error_message(tool_name=name, timeout_seconds=seconds)
            runtime = getattr(ctx, "context", None)
            return text + "\n" + (runtime.footer() if hasattr(runtime, "footer") else footer_text())
        return result

    def tool(name):
        def wrap(fn):
            return function_tool(fn, name_override=name, timeout=sdk_timeout_s(name),
                                 failure_error_function=fatal_tool_errors(*FATAL),
                                 timeout_error_function=timeout_text(name))
        return wrap

    async def call(ctx, name, action):
        cid = getattr(ctx, "tool_call_id", None) or f"{name}-{time.time_ns()}"

        def locked():
            if not scene.lock.acquire(timeout=0):
                return [ToolOutputText(text=json.dumps({"status": "busy", "code": "E_BUSY",
                                                        "message": "a previous scene tool is still running"}))]
            before = (scene.s.head or {}).get("id")
            result = None
            try:
                try:
                    result = action(cid)
                    if not isinstance(result, dict):
                        result = {"status": "ok", "result": result}
                    result.setdefault("call_id", cid)
                    result.setdefault("checkpoint", (scene.s.head or {}).get("id"))
                    output = _output(scene, cid, name, result)
                    if name == "finish" and result.get("status") == "accepted":
                        # Image processing must succeed before an accepted finish can be persisted.
                        scene.finish = result
                except FATAL:
                    raise
                except (ValueError, KeyError, TypeError, OSError) as error:
                    if name == "finish" and isinstance(result, dict) and "export" in result:
                        result = {**result, "status": "refused", "code": "E_IMAGE", "images": [],
                                  "message": str(error)[:1000], "failures": [*result.get("failures", []),
                                  {"check": "image_output", "code": "E_IMAGE", "message": str(error)[:1000]}]}
                    else:
                        result = {"status": "refused", "code": "E_ARGS", "call_id": cid, "message": str(error)[:1000]}
                    output = [ToolOutputText(text=json.dumps(result))]
                scene.record_call(cid, name, before, (scene.s.head or {}).get("id"), result)
                return output
            finally:
                scene.lock.release()
        try:
            output = await asyncio.to_thread(locked)
            runtime = getattr(ctx, "context", None)
            footer = runtime.footer() if hasattr(runtime, "footer") else footer_text()
            return [*output, ToolOutputText(text=footer)]
        except asyncio.CancelledError:
            scene.mark_unseen(cid)
            raise

    @tool("execute_blender_code")
    async def execute_blender_code(ctx: ToolContext, code: str, soft_timeout_s: float = 120, label: str = ""):
        """Run bpy code in the live scene; validated changes create a checkpoint. Files and imports are restricted.

        g3d.lens_coating(material, factor=..., ior=..., thickness_min_nm=..., thickness_max_nm=..., tint=[r,g,b])
        authors exportable thin-film coating on an active Principled material (or its name). Tint is linear RGB;
        the untextured film uses the maximum thickness. Both thickness limits are retained in the GLB.
        The helper preserves transmission and other optics. Inspect export warnings and fixed-scale AR to judge it.

        Args:
            code: Python code using bpy and the allowed g3d helpers.
            soft_timeout_s: Soft deadline, 0.5 to 600 seconds.
            label: Short name for the change.
        """
        return await call(ctx, "execute_blender_code", lambda cid: scene.s.execute(
            code, soft_timeout_s=soft_timeout_s, label=label, call_id=cid))

    @tool("scene_summary")
    async def scene_summary(ctx: ToolContext, objects: list[str] | None = None):
        """Inspect units, axes, origin, objects, materials, mesh topology and role warnings; null means all objects."""
        return await call(ctx, "scene_summary", lambda cid: scene.s.call_read(
            "scene_summary", "scene_summary", {"objects": objects if objects is not None else "all"},
            deadline_s=hard_timeout_s("scene_summary"), call_id=cid))

    @tool("save_checkpoint")
    async def save_checkpoint(ctx: ToolContext, label: str, best: bool = False):
        """Label the current scene; save a new checkpoint only if it changed.

        Args:
            label: Short name for this checkpoint.
            best: Select this checkpoint as the best so far and retain its images in long runs.
        """
        def save(cid):
            result = scene.s.save_checkpoint(label, call_id=cid)
            if best and result.get("status") == "ok":
                scene.best_checkpoint = scene.s.head["id"]
                result["best_checkpoint"] = scene.best_checkpoint
            return result
        return await call(ctx, "save_checkpoint", save)

    @tool("list_checkpoints")
    async def list_checkpoints(ctx: ToolContext):
        """List checkpoints and their labels, parent ids, and fingerprints."""
        return await call(ctx, "list_checkpoints", lambda cid: {"status": "ok", "checkpoints": scene.s.list_checkpoints()})

    @tool("restore_checkpoint")
    async def restore_checkpoint(ctx: ToolContext, checkpoint: str):
        """Restore a listed checkpoint and verify its saved fingerprint; the restore becomes the current head."""
        return await call(ctx, "restore_checkpoint", lambda cid: scene.s.restore_checkpoint(checkpoint, call_id=cid))

    @tool("fit_cameras")
    async def fit_cameras(ctx: ToolContext, reason: str, refs: list[str] | None = None, workers: int | None = None):
        """Fit photo cameras from current geometry and each photo's declared view and matte mode.
        Every attempted photo returns its result or failure; successful cameras remain usable. All-failed attempts
        retain diagnostic evidence and leave the previous usable basis as the default. Inspect failed photos before
        stages that need a complete camera set.

        Args:
            reason: Why the current geometry warrants fitting or refitting the cameras.
            refs: Photo ids or original filenames; null fits every photo.
            workers: Optional pool ceiling; null uses free commit memory and CPU count, up to 16 workers.
        """
        def fit(cid):
            from .. import jobs
            rows = jobs.load_job(scene.root)["images"]
            images = {row["original_name"]: scene.root / row["file"] for row in rows}
            views = {row["original_name"]: row["view_kind"] for row in rows}
            wanted = None if refs is None else {Path(name).stem for name in refs}
            candidates = {row["original_name"]: tuple(row["fit_kinds"]) for row in rows
                          if wanted is None or Path(row["original_name"]).stem in wanted}
            matte_modes = {row["original_name"]: row.get("matte_mode") for row in rows}
            with single_threaded_blas():
                return scene.seeing.fit_cameras(reason, images, views, candidates=candidates,
                                                refs=refs, workers=fit_workers(workers), matte_modes=matte_modes,
                                                call_id=cid)
        return await call(ctx, "fit_cameras", fit)

    @tool("render_views")
    async def render_views(ctx: ToolContext, views: list[str | CustomView] = ["canonical"],
                           passes: list[Literal["native", "clay", "normal", "silhouette", "parts"]] = ["native", "clay"],
                           size_px: int = 1024, background: str = "grey", basis: str | None = None):
        """Render canonical, fitted photo or custom cameras without changing the scene; returns labelled sheets.

        Args:
            views: Canonical names, fitted photo filenames, canonical/reference/all, or custom yaw/pitch cameras.
                Use reference:front for a photo whose id collides with a canonical name such as front.
            passes: Native materials, clay, surface normals, silhouette or part identities.
            size_px: Long image side in pixels (64 to 2048).
            background: Background colour.
            basis: Frozen camera basis id; null uses the latest.
        """
        return await call(ctx, "render_views", lambda cid: scene.seeing.render_views(
            views=[v.model_dump() if isinstance(v, CustomView) else v for v in views], passes=passes,
            size_px=size_px, background=background, basis=basis, call_id=cid))

    @tool("compare_to_photos")
    async def compare_to_photos(ctx: ToolContext, refs: list[str] | None = None, parts: Literal["all", "front"] = "all",
                                size_px: int = 1024, basis: str | None = None):
        """Compare the current model with photos through frozen cameras; reports silhouette offsets and image sheets.

        Args:
            refs: Photo ids; null compares every photo in the basis.
            parts: All parts or the front only.
            size_px: Long image side in pixels.
            basis: Frozen camera basis id; null uses the latest.
        """
        return await call(ctx, "compare_to_photos", lambda cid: scene.seeing.compare_to_photos(
            refs=refs, parts=parts, size_px=size_px, basis=basis, call_id=cid))

    @tool("pick_surface_point")
    async def pick_surface_point(ctx: ToolContext, view: str, points: list[list[float]], manifest: str | None = None,
                                 size_px: int = 1024, basis: str | None = None):
        """Ray-cast image pixel coordinates onto the current model; returns 3D hits and object identities.

        Args:
            view: A canonical name, fitted photo filename, reference:photo-id, or view name from a render manifest.
            points: One to 64 pixel coordinate pairs.
            manifest: Job-relative render manifest path, or null to resolve the view directly.
            size_px: Long image side when no manifest is supplied.
            basis: Frozen camera basis id; null uses the latest.
        """
        return await call(ctx, "pick_surface_point", lambda cid: scene.seeing.pick_surface_point(
            view, points, manifest=str(arview.confined(scene.root, manifest)) if manifest is not None else None,
            size_px=size_px, basis=basis, call_id=cid))

    @tool("export_glb")
    async def export_glb(ctx: ToolContext):
        """Export the current checkpoint with its SHA-256, complete specification report and all-frame seating check.
        Intermediate exports may have failed seating; finish requires both reports to pass."""
        return await call(ctx, "export_glb", lambda cid: scene.seeing.export_head(call_id=cid))

    @tool("arview_capture")
    async def arview_capture(ctx: ToolContext):
        """Inspect seven AR poses at authored size, fixed lens reflections and skin background. Returns one sheet and
        the capture report; 0=ok, 1=infrastructure, 2=usage, 3=spec failed, 4=refused. Auto-fit is always disabled."""
        def capture(cid):
            exp = scene.seeing.export_head(call_id=cid)
            result = arview.capture(scene.root, exp, cid, timeout_s=hard_timeout_s("arview_capture") - 10)
            scene.last_ar = result
            return result
        return await call(ctx, "arview_capture", capture)

    @tool("read_image")
    async def read_image(ctx: ToolContext, path: str, region: list[int] | None = None):
        """Read an oriented image or crop, at most 1024 pixels on either side. Tool calls return one compact sheet;
        full panels remain at their reported paths. Crop a native panel to inspect small details. Identical images
        already delivered in this run are referenced by their earlier call without changing conversation history.

        Args:
            path: Job-relative image path inside images, reference_images or work.
            region: Optional [left, top, right, bottom] crop in oriented-image pixels.
        """
        return await call(ctx, "read_image", lambda cid: {"status": "ok", "images": [
            {"path": str(arview.confined(scene.root, path)), "region": region, "view": path}]})

    @tool("budget_status")
    async def budget_status(ctx: ToolContext):
        """Return settled spend, settled_stop_usd and remaining_to_stop_usd after open/unknown holds, plus the
        separate reserve cap and the next request's estimated hold. Plan against the settled stop."""
        def status(cid):
            guard = getattr(getattr(ctx, "context", None), "guard", None)
            return {"status": "ok", "budget": guard.summary() if guard is not None else None}
        return await call(ctx, "budget_status", status)

    @tool("finish")
    async def finish(ctx: ToolContext, summary: str):
        """Finalize in one call: export the current head, check specification, all-frame seating and print freshness,
        then capture fixed-scale AR. Separate export/AR calls are optional inspections. Returns all reports and one
        compact image; a refusal lists every failed check with diagnostic images. Only accepted completes the run.

        Args:
            summary: What was built and any remaining differences from the photos.
        """
        def finish_current(cid):
            scene.finish = None
            exp = scene.seeing.export_head(call_id=cid)
            bound, failures = _finish_export_failures(scene, exp)
            try:
                ar = (arview.capture(scene.root, exp, cid, timeout_s=hard_timeout_s("arview_capture") - 10) if bound else
                      {"status": "refused", "code": "E_EXPORT", "message": "no current verified export to capture"})
            except FATAL:
                raise
            except (OSError, ValueError, KeyError, TypeError) as error:
                ar = {"status": "infrastructure_failed", "code": "E_AR", "message": str(error)}
            scene.last_ar = ar
            if (ar.get("status") != "ok" or ar.get("exit_code") != 0
                    or ar.get("export_sha256") != exp.get("sha256") or not ar.get("images")):
                failures.append({"check": "arview", "code": "E_AR", "report": ar})
            result = {"status": "refused" if failures else "accepted", "call_id": cid,
                      "checkpoint": scene.s.head["id"], "fingerprint": scene.s.head["fingerprint"],
                      "export": exp, "arview": ar, "summary": summary, "failures": failures}
            if failures:
                result.update(code="E_EXPORT" if any(f["check"] != "arview" for f in failures) else "E_AR",
                              message="Finish checks failed: " + ", ".join(f["check"] for f in failures))
            if ar.get("images"):
                result.update(images=ar["images"], image_source="arview")
            else:
                # This is diagnostic evidence only. It can never satisfy the AR acceptance check.
                try:
                    fallback = scene.seeing.render_views(views=["front"], passes=["native", "clay"], size_px=768,
                                                          call_id=cid + "-diagnostic")
                except FATAL:
                    raise
                except (OSError, ValueError, KeyError, TypeError) as error:
                    fallback = {"status": "error", "code": "E_DIAGNOSTIC", "message": str(error)}
                result.update(diagnostic_render=fallback, image_source="scene_render_fallback",
                              images=_sheets(fallback, "render_views"))
            return result
        return await call(ctx, "finish", finish_current)

    return [execute_blender_code, scene_summary, save_checkpoint, list_checkpoints, restore_checkpoint, fit_cameras,
            render_views, compare_to_photos, pick_surface_point, export_glb, arview_capture, read_image, budget_status,
            finish, *seat_tools(scene, tool, call), temple_tool(scene, call, settings, FATAL),
            photo_print_tool(scene, call, FATAL)]
