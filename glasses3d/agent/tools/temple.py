"""The long-lived specialist tool; every child request shares the parent's approval and budget guard."""
import asyncio
import math

from agents import function_tool
from agents.tool_context import ToolContext

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

        Args:
            budget_usd: Positive dollar allowance for this stage, within the job's remaining approved budget.
                An exhausted allowance returns an incomplete result without applying the private geometry.
        """
        runtime = getattr(ctx, "context", None)
        if not hasattr(runtime, "run_stage") or getattr(runtime, "guard", None) is None:
            return await call(ctx, "temple_stage", lambda cid: {
                "status": "refused", "code": "E_STAGE_CONTEXT", "message": "temple_stage needs the active job run"})
        maximum = runtime.guard.summary()["maximum_micro_usd"] / 1e6
        if not math.isfinite(budget_usd) or not 0 < budget_usd <= maximum:
            return await call(ctx, "temple_stage", lambda cid: {
                "status": "refused", "code": "E_ARGS", "message": "stage dollars must fit the approved job cap"})
        bridge = stage.StageBridge(asyncio.get_running_loop())
        try:
            return await call(ctx, "temple_stage", lambda cid: stage.run(
                scene, runtime, bridge, cid, str(budget_usd), child_settings))
        except asyncio.CancelledError:
            bridge.cancel()
            raise

    return temple_stage
