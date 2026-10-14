"""The long-lived specialist tool; every child request shares the parent's approval and budget guard."""
import asyncio
import math

from agents import function_tool
from agents.tool_context import ToolContext

from ..budget import cap_to_micro, usd_text
from ..loop import STAGE_FATAL, fatal_tool_errors
from ..temple_lab import stage


def temple_tool(scene, call, parent_settings, fatal):
    child_settings = stage.settings(parent_settings)

    @function_tool(timeout=None, failure_error_function=fatal_tool_errors(*fatal, *STAGE_FATAL))
    async def temple_stage(ctx: ToolContext, budget_usd: float):
        """Fit faceted temples on a private copy of the current job head, before lens seating and photo print.
        Needs existing temples and a frozen camera basis for the photos. A specialist uses its own photos, prompt
        and facet tools within this run's approval and your dollar allowance; its requests also count against the
        run's money and interaction limits. The accepted fitted temples are applied only if the parent head still
        matches the captured one. Inspect its summary, compare the result to photos, and continue the job.
        When deriving a hinge axis from an unposed source, recognition needs a temple-role part centered on
        each side, with world X and Z bounding-box extents each strictly below 3.2 mm and center Z within
        4 mm of the temple-front hinge plane. This is a tool heuristic, not a product geometry requirement;
        do not shrink real hardware just to satisfy it. Export refusals include the underlying cause.

        Args:
            budget_usd: Positive dollar allowance within the money remaining before the job's settled stop,
                after open and unknown holds, and within its remaining reserve capacity.
                An exhausted allowance returns an incomplete result without applying the private geometry.
        """
        runtime = getattr(ctx, "context", None)
        if not hasattr(runtime, "run_stage") or getattr(runtime, "guard", None) is None:
            return await call(ctx, "temple_stage", lambda cid: {
                "status": "refused", "code": "E_STAGE_CONTEXT", "message": "temple_stage needs the active job run"})
        summary = runtime.guard.summary()
        maximum = summary["maximum_micro_usd"]
        has_stop = summary.get("settled_stop_usd") is not None
        if has_stop:
            maximum = min(summary["remaining_micro_usd"], cap_to_micro(summary["remaining_to_stop_usd"]))
        if (not math.isfinite(budget_usd) or budget_usd <= 0
                or cap_to_micro(budget_usd) > maximum):
            return await call(ctx, "temple_stage", lambda cid: {
                "status": "refused", "code": "E_ARGS", "maximum_stage_usd": usd_text(maximum),
                "message": ("stage dollars must fit the remaining working budget before the settled stop "
                            "and the reserve cap" if has_stop else "stage dollars must fit the approved job cap")})
        bridge = stage.StageBridge(asyncio.get_running_loop())
        try:
            return await call(ctx, "temple_stage", lambda cid: stage.run(
                scene, runtime, bridge, cid, str(budget_usd), child_settings))
        except asyncio.CancelledError:
            bridge.cancel()
            raise

    return temple_stage
