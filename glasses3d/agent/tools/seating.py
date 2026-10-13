"""Lens seating tools through the journaled, allowlisted worker method."""
from typing import Literal

from agents.tool_context import ToolContext

from ..loop import hard_timeout_s

SEAT_TOOL_NAMES = ("seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire")
FrameType = Literal["acetate", "metal", "browline", "semi_rimless", "rimless"]
LowerEdge = Literal["plain", "nylor"]


def seat_tools(scene, tool, call):
    def mutate(name, kwargs, cid):
        return scene.s.call_mutating(name, "g3d_call", {"op": name, "kwargs": kwargs},
                                     deadline_s=hard_timeout_s(name), label=name, call_id=cid)

    def read_check(kwargs, cid):
        out = scene.s.call_read("check_lens_seating", "g3d_call",
                                {"op": "check_lens_seating", "kwargs": kwargs},
                                deadline_s=hard_timeout_s("check_lens_seating"), call_id=cid)
        if out.get("status") == "ok":
            worker = out["result"]
            out = {**out, **{key: value for key, value in worker.items() if key != "seconds"}}
            if out.get("restored_to") is not None:
                out.update(status="error", code="E_READ_CHANGED", message="seating check changed the scene")
        return out

    @tool("seat_lens")
    async def seat_lens(ctx: ToolContext, lens: str, frame_type: FrameType, frame_parts: list[str] | None = None,
                        lower_edge: LowerEdge | None = None, capture_mm: float | None = None, dry_run: bool = False):
        """Seat one lens against its actual frame construction, after temple geometry. Crystal/translucent frame
        materials are detected automatically and receive the V-bevel and groove. UVs are kept. The complete report
        is in result: seated/already_seated/partial/refused, reasons, warnings and affected geometry. Partial is a
        recorded edit, never a passing final junction. Inspect check_lens_seating after the edit.

        Args:
            lens: Exact lens object name from scene_summary.
            frame_type: Actual construction: acetate, metal, browline, semi_rimless or rimless.
            frame_parts: Exact supporting frame object names, or null for visible frame parts.
            lower_edge: For semi_rimless, plain for a shield or nylor for a grooved edge and cord; null uses plain.
            capture_mm: Optional capture target in mm; null uses the construction default. Existing floors apply.
            dry_run: Report the plan without writing geometry or a seat declaration.
        """
        return await call(ctx, "seat_lens", lambda cid: mutate("seat_lens", {
            "lens": lens, "frame_type": frame_type, "frame_parts": frame_parts, "lower_edge": lower_edge,
            "capture_mm": capture_mm, "dry_run": dry_run}, cid))

    @tool("hide_eyewire")
    async def hide_eyewire(ctx: ToolContext, eyewire: str, under: list[str], hidden_x_range_mm: list[list[float]],
                           lens: str | None = None, margin_mm: float = .10, ramp_mm: float = 3.0,
                           lift_max_mm: float = 2.5, dry_run: bool = False):
        """Bury the indicated eyewire under its supporting brow over photo-supported asset-X ranges. Moves the held
        lens band with it and records the effective union of ranges. Inspect result.status, result.ranges_effective,
        displacement, warnings and the visible junction. Use before seat_lens when the construction needs both.

        Args:
            eyewire: Exact eyewire object name.
            under: Exact overlying frame object names.
            hidden_x_range_mm: One or more [x0, x1] ranges in the asset's millimetres, chosen from the photos.
            lens: Held lens object name, or null to identify it geometrically.
            margin_mm: Burial margin in mm.
            ramp_mm: Transition length in mm.
            lift_max_mm: Maximum permitted lift in mm; a larger required lift is refused.
            dry_run: Report the plan without changing the scene.
        """
        return await call(ctx, "hide_eyewire", lambda cid: mutate("hide_eyewire", {
            "eyewire": eyewire, "under": under, "hidden_x_range_mm": hidden_x_range_mm, "lens": lens,
            "margin_mm": margin_mm, "ramp_mm": ramp_mm, "lift_max_mm": lift_max_mm, "dry_run": dry_run}, cid))

    @tool("check_lens_seating")
    async def check_lens_seating(ctx: ToolContext, lenses: list[str] | None = None,
                                 frame_parts: list[str] | None = None, frame_type: FrameType | None = None,
                                 hidden_x_range_mm: list[list[float]] | None = None,
                                 lower_edge: LowerEdge | None = None, stations_table: bool = False):
        """Measure seating without editing geometry, including crystal frames. Read result.ok, result.reasons and
        each result.lenses entry's pass, frame_type_source and geometric measurements. An inferred construction is
        provisional and cannot pass final delivery. This is the geometric check; inspect the junction in AR too.

        Args:
            lenses: Exact lens object names, or null for every visible lens.
            frame_parts: Supporting frame object names, or null for every visible frame part.
            frame_type: Optional construction for this inspection; null reads recorded seat declarations.
            hidden_x_range_mm: Optional [x0, x1] inspection ranges; null reads recorded hidden ranges.
            lower_edge: Optional semi_rimless plain/nylor construction.
            stations_table: Include the detailed per-station table when diagnosing a failure.
        """
        return await call(ctx, "check_lens_seating", lambda cid: read_check({
                "lenses": lenses, "frame_parts": frame_parts, "frame_type": frame_type,
                "hidden_x_range_mm": hidden_x_range_mm, "lower_edge": lower_edge,
                "stations_table": stations_table}, cid))

    @tool("unseat_lens")
    async def unseat_lens(ctx: ToolContext, lens: str):
        """Restore a lens's pre-seat mesh and remove its generated groove cutter, Boolean, cord and declaration.
        Read result.status and any E_ORDER refusal: dependent hide/seat operations must be undone in reverse order.
        """
        return await call(ctx, "unseat_lens", lambda cid: mutate("unseat_lens", {"lens": lens}, cid))

    @tool("unhide_eyewire")
    async def unhide_eyewire(ctx: ToolContext, eyewire: str, lens: str | None = None):
        """Restore an eyewire and its recorded held lens to their pre-hide meshes and ranges. A later seat on that
        held lens is undone too. Read result.status; a different supplied lens is refused without moving geometry.
        """
        return await call(ctx, "unhide_eyewire", lambda cid: mutate("unhide_eyewire", {
            "eyewire": eyewire, "lens": lens}, cid))

    return [seat_lens, hide_eyewire, check_lens_seating, unseat_lens, unhide_eyewire]
