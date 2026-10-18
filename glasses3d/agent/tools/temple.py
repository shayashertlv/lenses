"""The long-lived specialist tool; every child request shares the parent's approval and budget guard."""
import asyncio

from agents import function_tool
from agents.tool_context import ToolContext

from ..budget import cap_to_micro, usd_text
from ..loop import STAGE_FATAL, fatal_tool_errors
from ..temple_lab import stage


def temple_tool(scene, call, parent_settings, fatal):
    owner_edit = getattr(scene, "owner_edit", None)
    if owner_edit:
        from ..temple_lab import owner_stage
        child_settings = owner_stage.settings(parent_settings, owner_edit)
        run_stage = owner_stage.run
    else:
        child_settings = stage.settings(parent_settings)
        run_stage = stage.run

    @function_tool(timeout=None, failure_error_function=fatal_tool_errors(*fatal, *STAGE_FATAL))
    async def temple_stage(ctx: ToolContext):
        """Fit faceted temples on a private copy of the current job head, before lens seating and photo print.
        Handles single-material slab-like temple bodies with one top and bottom outline at each longitudinal
        position, a vertical inner face and an outer surface described by thickness and facets. It is not a general
        replacement for multi-material arms, separate rubber sleeves, round wire or branching bodies. Capture
        refuses missing targets, multiple body materials, non-single-valued outlines and an unavailable hinge axis;
        construction refuses open solids, insufficient thickness and inconsistent declared creases.
        Needs existing temples and a frozen camera basis for the photos. A specialist uses its own photos, prompt
        and facet tools within the owner's remaining run budget; its requests also count against the
        run's money and interaction limits. The accepted fitted temples are applied only if the parent head still
        matches the captured one. Inspect its summary, compare the result to photos, and continue the job.
        When deriving a hinge axis from an unposed source, recognition needs a temple-role part centered on
        each side, with world X and Z bounding-box extents each strictly below 3.2 mm and center Z within
        4 mm of the temple-front hinge plane. This is a tool heuristic, not a product geometry requirement;
        do not shrink real hardware just to satisfy it. Export refusals include the underlying cause.
        Hinge parts are kept separately: parts near the axis within 8 mm in X, around the hinge plane within 4 mm
        (extended 4 mm rearward), at most 20,000 vertices; knuckles are under 3.2 mm in X and Z, temple leaves
        under 15 mm in Z. Compare these mechanics to the photos before choosing this tool.

        Only the owner sets money limits. The same settled stop and maximum reserve govern every child request.
        """
        runtime = getattr(ctx, "context", None)
        attached = getattr(runtime, "runtime", None)
        if not callable(getattr(attached, "run_stage", None)) or getattr(runtime, "guard", None) is None:
            return await call(ctx, "temple_stage", lambda cid: {
                "status": "refused", "code": "E_STAGE_CONTEXT", "message": "temple_stage needs the active job run"})
        summary = runtime.guard.summary()
        maximum = summary["remaining_micro_usd"]
        stop = summary.get("settled_stop_usd")
        if (maximum <= 0 or summary["held_micro_usd"] or summary["unknown_micro_usd"]
                or (stop is not None and summary["settled_micro_usd"] >= cap_to_micro(stop))):
            return await call(ctx, "temple_stage", lambda cid: {
                "status": "refused", "code": "E_STAGE_BUDGET",
                "message": "the owner's budget has no available runway, or a request remains unsettled"})
        # The shared guard applies the approved money policy to every child request. In settled-only mode
        # this remaining amount is informational; estimated holds cannot exhaust the stage early.
        bridge = stage.StageBridge(asyncio.get_running_loop())
        try:
            return await call(ctx, "temple_stage", lambda cid: run_stage(
                scene, runtime, bridge, cid, usd_text(maximum), child_settings))
        except asyncio.CancelledError:
            bridge.cancel()
            raise

    if owner_edit:
        temple_stage.description = (
            "Work on the exact current mesh in a private owner-edit specialist session. It retains all general "
            "tools and facet_owner_edit, except another temple_stage; it does not rebuild a template or replace "
            "current geometry with stale "
            "construction coordinates. In understand mode it only inspects and prepares labelled targets. In "
            "confirmed edit mode the owner's named feature targets outrank photos and earlier choices, within "
            "the host-sealed support. Other geometry, materials, UVs and settings stay unchanged; every physical "
            "and strict specification check remains. Only a completed accepted child can apply its actual mesh "
            "patch, and only if the parent still matches the captured head. Read its summary and compare the "
            "result. The same owner settled stop and reserve govern all child requests; no new money is granted.")
    if getattr(parent_settings, "settled_only", False):
        temple_stage.description = temple_stage.description.replace(
            "The same settled stop and maximum reserve govern every child request.",
            "Parent and specialist share one settled-only stop; estimated reservations cannot stop requests early.").replace(
            "The same owner settled stop and reserve govern all child requests; no new money is granted.",
            "Parent and specialist share one settled-only stop; estimated reservations cannot stop requests early.")
    return temple_stage
