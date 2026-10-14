"""The final photo-print stage: immutable bake evidence and journaled scene attachment."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import threading

from agents import function_tool
from agents.tool_context import ToolContext

from ..journal import CheckpointMismatch
from ..loop import fatal_tool_errors
from ..procs import write_json_atomic
from . import arview


WORKER_DEADLINE_S = 600.0


class PrintRefusal(ValueError):
    def __init__(self, code, message, **evidence):
        super().__init__(message)
        self.code, self.evidence = code, evidence


def _export(scene, cid):
    export = scene.seeing.export_head(call_id=cid)
    head = scene.s.head
    if (export.get("status") != "exported" or export.get("drift") or
            export.get("fingerprint") != head["fingerprint"] or
            export.get("validation_sha256") != export.get("sha256") or
            (export.get("validate_spec") or {}).get("ok") is not True):
        raise PrintRefusal("E_EXPORT", "photo_print needs a valid export of the current geometry", export=export)
    path = arview.confined(scene.root, export["path"])
    if arview.sha256(path) != export["sha256"]:
        raise PrintRefusal("E_EXPORT", "export bytes changed")
    return export


def run(scene, cid, *, parts, basis, calibration=None, registration_report=None, cancelled=None):
    """Called under the generic scene lock. Failed attachment restores the verified pre-stage checkpoint.

    The bake is preserved even when attachment refuses; its report never claims the independent quality proof or
    owner acceptance. Cancellation cannot let a background bake apply geometry after its caller has left.
    """
    from ...g3d_host.photoprint import inputs as IN, manifest as MF, production as P
    from . import fit_workers, single_threaded_blas

    cancelled = cancelled or threading.Event()
    before = dict(scene.s.head)
    changed = False
    evidence = {}

    def check_stop():
        if cancelled.is_set():
            raise PrintRefusal("E_CANCELLED", "photo_print was cancelled; its scene changes were restored")

    def mutate(op, kwargs):
        nonlocal changed
        check_stop()
        result = scene.s.call_mutating(op, "g3d_call", {"op": op, "kwargs": kwargs},
                                      deadline_s=WORKER_DEADLINE_S, label="photo print: " + op,
                                      call_id=cid)
        changed = changed or scene.s.head["fingerprint"] != before["fingerprint"]
        evidence[op] = result
        if result.get("status") != "ok" or (result.get("result") or {}).get("status") != "ok":
            raise PrintRefusal("E_PRINT_APPLY", op + " did not complete", operation=op)

    def restore():
        if not changed and scene.s.head["fingerprint"] == before["fingerprint"]:
            return None
        result = scene.s.restore_checkpoint(before["id"], call_id=cid)
        if result.get("status") != "ok" or scene.s.head["fingerprint"] != before["fingerprint"]:
            raise CheckpointMismatch("photo_print could not restore its original checkpoint")
        return result

    try:
        if (type(parts) is not list or not parts or any(type(p) is not str or not p.strip() for p in parts)
                or len(set(parts)) != len(parts) or type(basis) is not str or not basis.strip()):
            raise PrintRefusal("E_ARGS", "unique exact part names and an explicit frozen basis id are required")
        check_stop()
        export = _export(scene, cid + ":source")
        job = IN.job_from_dir(scene.root, glb=export["path"], basis=basis)
        output = arview.confined(scene.root, Path("work/print") / hashlib.sha256(cid.encode()).hexdigest()[:16],
                                areas=("work",))
        output.mkdir(parents=True, exist_ok=False)  # never replace a prior call's evidence
        options = {}
        for key, value in (("calibration", calibration), ("step0_report", registration_report)):
            if value is not None:
                options[key] = arview.confined(scene.root, value, areas=("inputs", "work", "cameras"))
        workers = fit_workers()
        with single_threaded_blas():
            baked = P.bake_print(job, parts, out_dir=output, thresholds=dict(P.THRESHOLDS), workers=workers,
                                 **options)
        evidence.update(production=baked["production"], bake=str(output), workers=workers, source_export=export)
        check_stop()
        manifest = MF.load_manifest(output)
        evidence["manifest_sha256"] = arview.sha256(output / MF.MANIFEST)
        eligible = manifest.get("texture_parts")
        layout_parts = manifest.get("parts")
        if (type(layout_parts) is not list or any(type(p) is not str for p in layout_parts) or
                len(layout_parts) != len(parts) or set(layout_parts) != set(parts)):
            raise PrintRefusal("E_PRINT_MANIFEST", "the bake layout differs from the requested exact parts")
        if (type(eligible) is not list or not eligible or any(type(p) is not str or p not in parts for p in eligible)
                or len(set(eligible)) != len(eligible)):
            raise PrintRefusal("E_NO_TEXTURE", "the bake has no valid eligible texture parts",
                               texture_parts=eligible)
        # Detach only the requested parts' previous print, restoring their authored base material. The journal
        # restores that old print too if any later layout, attachment, export or manifest check fails.
        mutate("detach_print", {"parts": parts})
        mutate("print_uv", {"parts": parts, "bake": str(output), "replace": True})
        mutate("attach_print", {"report": str(output), "parts": eligible})
        attached = _export(scene, cid + ":export")
        check = MF.check_glb(attached["path"], output)
        evidence.update(export=attached, print_check=check, texture_parts=eligible)
        if check.get("ok") is not True:
            raise PrintRefusal("E_PRINT_CHECK", "the exported print does not match its bake")
        check_stop()
        result = {"status": "ok", "checkpoint": scene.s.head["id"], **evidence}
        write_json_atomic(output / "application.json", result)
        # The successful application now exists: recompute freshness even if the GLB bytes came from the export
        # cache. Finalize this call's receipt before returning; older calls' evidence is never rewritten.
        evidence["export"] = _export(scene, cid + ":verified")
        freshness = evidence["export"].get("print_check") or {}
        if freshness.get("ok") is not True or freshness.get("glb_sha256") != evidence["export"]["sha256"]:
            raise PrintRefusal("E_PRINT_CHECK", "the applied print has no valid current attachment evidence")
        check_stop()
        result.update(evidence)
        write_json_atomic(output / "application.json", result)
        return result
    except PrintRefusal as exc:
        restored = restore()
        result = {"status": "refused", "code": exc.code, "message": str(exc), **evidence, **exc.evidence,
                  "restored_to": before["id"] if restored else None, "restore": restored}
        if evidence.get("bake"):
            write_json_atomic(Path(evidence["bake"]) / "application.json", result)
        return result
    except BaseException as exc:
        restored = restore()
        if evidence.get("bake"):
            write_json_atomic(Path(evidence["bake"]) / "application.json", {
                "status": "failed", "code": "E_PRINT", "message": f"{type(exc).__name__}: {exc}",
                **evidence, "restored_to": before["id"] if restored else None, "restore": restored})
        raise


def photo_print_tool(scene, call, fatal):
    @function_tool(timeout=None, failure_error_function=fatal_tool_errors(*fatal))
    async def photo_print(ctx: ToolContext, parts: list[str], basis: str,
                          calibration: str | None = None, registration_report: str | None = None):
        """Bake photo-derived colour for complex patterns such as tortoiseshell, after geometry and lens seating.
        Plain frames usually need material/colour/finish edits instead. Uses the named frozen cameras and only
        target-colourway photos, preserves the bake evidence, then attaches eligible print parts and verifies the
        exported GLB. A failed attachment restores the pre-call model. Existing coverage and registration rules
        remain; baking is not an independent quality proof or owner acceptance. Compare the result with the photos.
        Handles colour patterns on the named acetate surfaces, not geometry or lens optical coatings. Refuses
        invalid/stale exports, missing or changed frozen inputs, no eligible texture parts, failed coverage or
        registration, and attachment/export mismatches. Inspect the full refusal and retained bake evidence.

        Args:
            parts: Unique exact scene object names for patterned acetate, including genuine .001 suffixes.
            basis: Explicit frozen camera basis id returned by fit_cameras.
            calibration: Optional job-local calibration JSON with its bound level selection; null applies no level.
            registration_report: Optional job-local registration report for these exact inputs; null uses the
                ordinary single-source mode without claiming registered accuracy.
        """
        cancelled = threading.Event()
        pending = asyncio.create_task(call(ctx, "photo_print", lambda cid: run(scene, cid, parts=parts, basis=basis,
            calibration=calibration, registration_report=registration_report, cancelled=cancelled)))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            cancelled.set()
            # Keep the job session alive until the thread has observed cancellation and restored any mutation.
            # Cancelling asyncio.to_thread alone does not stop its thread, even after a second cancellation.
            while not pending.done():
                try:
                    await asyncio.shield(pending)
                except asyncio.CancelledError:
                    continue
            pending.result()  # restoration failures remain fatal instead of being hidden by cancellation
            raise
    return photo_print
