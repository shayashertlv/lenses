"""Strict general photo measurement tool; no Blender mutation or paid request."""
from typing import Annotated, Literal
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field
from .. import photo_read as host


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Pixel = Annotated[list[float], Field(min_length=2, max_length=2)]
Trace = Annotated[list[Pixel], Field(min_length=2, max_length=6)]


class Pick(Input):
    photo: str = Field(description="Frozen basis photo id, e.g. reference-01; native photo pixels, not a render crop.")
    uv: Pixel | None = Field(description="Corner: [u,v]; crossing: null.")
    edges: Annotated[list[Trace], Field(min_length=2, max_length=2)] | None = Field(description="Crossing: two traced edges; corner: null.")
    sd_px: float | None = Field(description="Manual pick error 0.1..20 px; null means 1 px. Never enlarge to hide disagreement.")


class Point(Input):
    name: str = Field(description="Reading name: letter, then at most 47 letters/digits/_/-.")
    kind: Literal["corner", "crossing"]
    picks: Annotated[list[Pick], Field(min_length=2, max_length=4)]
    temple_side: Literal[-1, 1] | None = Field(description="null=fixed frame/lens feature; +1 or -1=temple on that world X side, applying its frozen photo pose.")


def photo_read_tool(scene, tool, call):
    @tool("photo_read")
    async def photo_read(ctx: ToolContext, points: Annotated[list[Point], Field(min_length=1, max_length=8)],
                         basis: str | None = None):
        """Measure named 3D positions from homologous corners or traced edge crossings in 2..4 photos.

        Handles hinge pods, temple bends/tips, bridge, lens corners and openings. Uses the frozen camera basis
        (null selects latest usable), returning neutral world XYZ mm, conditional errors, residuals, pair
        disagreements and pick images. Measures photographs, not points on the model. Set temple_side explicitly
        for a posed temple; null keeps frame/lens points fixed. Never mirrors a feature onto the opposite temple.
        Refuses missing/unusable/changed cameras or photos, bad/out-of-image picks, parallel crossing edges and
        ill-conditioned photo pairs (Jacobian condition >20). Pick/crossing errors include the shared 1px camera
        floor and separate 0.3 degree angular sensitivity; camera accuracy is not certified. Read W_RESIDUAL and
        W_PAIR_DISAGREEMENT before editing: a small two-view residual can still hide wrong correspondence or
        calibration. For long results use read_report with the returned full_report.id; smaller records are
        complete inline. consistent_with_stated_error covers the joint residual; also inspect pair warnings.
        """
        def action(cid):
            try:
                return host.measure(scene.root, cid, [p.model_dump() for p in points], basis)
            except host.C.NoBasis as error:
                return {"status": "refused", "code": "E_NO_BASIS", "message": str(error)}
        return await call(ctx, "photo_read", action)
    return photo_read
