"""Strict, image-bearing Agents SDK wrappers. Intentionally separate from production."""
from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Literal

from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field

from glasses3d.g3d.temple.spec import TempleError
from glasses3d.agent.journal import WorkerFailed
from glasses3d.agent.loop import fatal_tool_errors
from .lab import data_url


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Mark(Input):
    id: str
    points: list[list[float]] = Field(min_length=2, max_length=2)
    kind: Literal["crease", "silhouette", "curvature", "uncertain"]
    evidence: str = Field(min_length=1, max_length=1200)


class Node(Input):
    id: str
    d_mm: float
    y_mm: float
    relief_mm: float | None


class Facet(Input):
    id: str
    nodes: list[str] = Field(min_length=3, max_length=32)
    crease: list[str] | None
    rise_per_mm: float | None


class Depth(Input):
    node: str
    relief_mm: float | None


class Rise(Input):
    facet: str
    rise_per_mm: float


class Anchor(Input):
    pixel: list[float] = Field(min_length=2, max_length=2)
    chart_mm: list[float] = Field(min_length=2, max_length=2)


def registry(lab):
    lock = asyncio.Lock()
    lab_tool = partial(function_tool, failure_error_function=fatal_tool_errors(WorkerFailed))

    async def call(fn, *args):
        # No SDK timeout on a background thread: cancellation must not outlive the scene lock.
        # Worker operations already have their own bounded protocol.
        async with lock:
            try:
                result = await asyncio.to_thread(fn, *args)
            except TempleError as exc:
                result = exc.to_dict()
            except (ValueError, KeyError, StopIteration, TypeError) as exc:
                result = {"status": "refused", "code": "E_LAB_ARGS", "message": str(exc)}
            out = [ToolOutputText(text=json.dumps(result, allow_nan=False))]
            for iid in result.get("image_ids", []):
                out.append(ToolOutputImage(image_url=data_url(lab.image_path(iid)), detail="high"))
            return out

    @lab_tool
    async def lab_inspect():
        """Read initial template, metric chart, photo IDs, current revision and scene checkpoints."""
        return await call(lab.inspect)

    @lab_tool
    async def lab_image(image_id: str, crop: list[int] | None):
        """See a registered image or create an unscaled crop [left,top,right,bottom] of an original photo."""
        return await call(lab.image, image_id, crop)

    @lab_tool
    async def lab_mark(image_id: str, parent_revision: int, marks: list[Mark]):
        """Replace all marks for this photo/crop; [] deletes them. Returns numbered endpoint overlay. No mesh edit."""
        return await call(lab.mark, image_id, parent_revision, [m.model_dump() for m in marks])

    @lab_tool
    async def lab_refine(image_id: str, revision: int, mark_id: str):
        """Propose a nearby contrast edge with original red/proposed cyan overlay. Never accepts it automatically."""
        return await call(lab.refine, image_id, revision, mark_id)

    @lab_tool
    async def lab_candidate(parent_revision: int, nodes: list[Node], facets: list[Facet], evidence: list[str], note: str):
        """Validate a new generic layout and absolute depths on the blank template; evidence is image:revision:mark.

        d/y/r are mm; r is outward from the base, null solves a node from neighbouring planes. Each facet has a
        polygon of shared node IDs. For a three-point plane use crease=null,rise_per_mm=null. For a narrow plane
        give two crease node IDs and rise in mm/mm toward the polygon interior. No scene change until lab_apply.
        """
        return await call(lab.candidate, parent_revision, [n.model_dump() for n in nodes],
                          [f.model_dump() for f in facets], evidence, note)

    @lab_tool
    async def lab_map_marks(image_id: str, revision: int, anchors: list[Anchor]):
        """Propose chart d/y for marked segments using YOUR 3..12 photo-to-chart anchor correspondences.

        Approximate affine side-plane mapping, not depth recovery. Non-collinear anchors required. Parallax remains.
        """
        return await call(lab.map_marks, image_id, revision, [a.model_dump() for a in anchors])

    @lab_tool
    async def lab_profile(parent_revision: int, top: list[list[float]], bottom: list[list[float]],
                          inner: list[list[float]], thickness: list[list[float]], zone_end_d: float, note: str):
        """Explicitly revise metric template chains [d,value] in mm while retaining existing facet layout.

        top/bottom are y, inner is +X coordinate, thickness is base wall thickness. d strictly increases, common
        end stations; inner/thickness must be linear throughout the front facet zone. No optimization runs.
        """
        return await call(lab.profile, parent_revision, top, bottom, inner, thickness, zone_end_d, note)

    @lab_tool
    async def lab_depth(parent_revision: int, changes: list[Depth], rises: list[Rise], note: str):
        """Set absolute named node relief/rise values; retain layout d/y, polygons and template. No fitting runs."""
        return await call(lab.depth, parent_revision, [x.model_dump() for x in changes],
                          [x.model_dump() for x in rises], note)

    @lab_tool
    async def lab_apply(ctx: ToolContext, revision: int, checkpoint: str):
        """Build current validated candidate on both temples from the original copy; return native/decal receipt.

        The expected current scene checkpoint is required. Failed builds restore the pre-call scene. Rebuilding
        from the original prevents repeated decal projections from accumulating. Successful build is checkpointed.
        """
        return await call(lab.apply, revision, checkpoint, ctx.tool_call_id)

    @lab_tool
    async def lab_render(views: list[Literal["full", "side", "oblique", "other-side", "top", "end",
                                           "temple-side", "temple-grazing", "temple-top"]],
                         passes: list[Literal["native", "clay", "normal"]]):
        """Return fixed-camera scene images. temple-side/grazing/top are close-ups for relief and thickness. Use native/clay; normal helps inspect planes. At most six views per call."""
        return await call(lab.render, views, passes)

    @lab_tool
    async def lab_pick(render_id: str, view: str, points: list[list[float]]):
        """Pick current-model surface at pixels in a current registered render. This does not measure photo depth."""
        return await call(lab.pick, render_id, view, points)

    @lab_tool
    async def lab_restore(ctx: ToolContext, checkpoint: str, reason: str):
        """Restore a known scene checkpoint. Candidate drafts remain; inspect returns the new scene checkpoint."""
        return await call(lab.restore_scene, checkpoint, reason, ctx.tool_call_id)

    @lab_tool
    async def lab_export(checkpoint: str):
        """Export the current verified checkpoint as an experimental GLB and return its hash/receipt."""
        return await call(lab.export, checkpoint)

    return [lab_inspect, lab_image, lab_mark, lab_refine, lab_map_marks, lab_profile, lab_candidate, lab_depth, lab_apply,
            lab_render, lab_pick, lab_restore, lab_export]
