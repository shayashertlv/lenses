"""The seven facet tools (plan/WP6_FACETS.md §4.1): strict-JSON Agents SDK function tools over a Lab.

    registry(lab, settings=None) -> [facet_inspect, photo_image, photo_compare, facet_edit, facet_variants,
                                     facet_restore, facet_finish]

Every tool is async with the SDK timeout of loop.HARD_TIMEOUT_S + 10 s; the work runs in a worker thread that takes
the lab's scene lock (Lab.lock) with timeout 0 (a call that finds it held, e.g. behind a timed-out call still running,
returns {status: busy, code: E_BUSY, running} at once). A call the SDK times out answers {status: timeout, code:
E_TIMEOUT} with the footer (timeout_text) and is Lab.mark_unseen, so a compare its thread still makes demands no
judgement. A result is ONE text part, compact JSON (numbers rounded) then the footer line "settled $x / cap $y ·
context n tokens" (loop.RunStatus, the run context), followed by its images (ToolOutputImage, detail high).
WorkerFailed stays fatal (the run ends failed_worker). The edits are the §4.2 strict anyOf union (EDIT_MODELS;
geometry.apply_edits converts them to the /2 maps). Photo ids (lab.py module doc): photo_image takes every photo
(Lab.image_ids: target, colourway = another colour with a calibrated camera, reference = no camera); photo_compare,
facet_edit's compare, judgements, points_px and depths take the photos with a camera (Lab.compare_ids; depths has a
default so scripted calls without it stay valid; the strict schema still lists it); facet_variants takes
those too, at most Lab.variant_photos_max() of them and at most 3 target photos (lab.py module doc). The tool texts
Astra reads name no photo id: the enums and facet_inspect's photo list carry the job's.
Annotations are evaluated eagerly (no __future__ import): the tools close over the lab's photo-id Literals.
"""
import asyncio
import json
import math
import time
from functools import partial
from typing import Annotated, Literal, Union

from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field

from glasses3d.agent.journal import WorkerFailed
from glasses3d.agent.loop import RunSettings, fatal_tool_errors, footer_text, sdk_timeout_s
from glasses3d.g3d.temple.spec import TempleError
from . import geometry as G
from .lab import DEPTHS_MAX, data_url
from .state import LabRefusal

CONTEXT_GUARD_TOKENS = 240_000          # §4.3: the one-epoch image filter from this many counted input tokens
TOOL_NAMES = ("facet_inspect", "photo_image", "photo_compare", "facet_edit", "facet_variants", "facet_restore",
              "facet_finish")
Chain = Literal["outline.top", "outline.bottom", "inner", "base.top", "base.bottom"]
Verdict = Literal["better", "same", "worse", "mixed"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# --------------------------------------------------------------------------------------------- the §4.2 edits union
class Thickness(Input):
    mode: Literal["mm", "base", "solve"] = Field(description="mm: t is given; base: the base thickness at (d, y); "
                                                             "solve: from the facet planes")
    mm: float | None = Field(description="thickness from the inner face (mm) for mode mm, else null")


class SetNode(Input):
    op: Literal["set_node"]
    name: str
    d_mm: float = Field(description="mm back from the hinge")
    y_mm: float = Field(description="mm up")
    t: Thickness


class DeleteNode(Input):
    op: Literal["delete_node"]
    name: str


class Plane(Input):
    mode: Literal["nodes", "crease"] = Field(description="nodes: through the facet's own node values; crease: "
                                                         "through the crease edge, rising rise_per_mm into the facet")
    crease: list[str] | None = Field(description="the two crease nodes (adjacent corners of poly), or null",
                                     min_length=2, max_length=2)
    rise_per_mm: float | None = Field(description="mm of X gained per mm moved from the crease into the facet "
                                                  "(negative falls toward the inner face), or null")


class Fade(Input):
    d0_mm: float = Field(description="d where the facet is fully its plane")
    d1_mm: float = Field(description="d where it has blended into the plain arm away from its creases; along a "
                                     "crease with a standing neighbour it keeps that neighbour's height (fade both "
                                     "facets of a crease the same way to let it die out; a step against a standing "
                                     "neighbour is refused, E_FADE_CLIFF)")


class SetFacet(Input):
    op: Literal["set_facet"]
    label: str
    poly: list[str] = Field(min_length=3, max_length=32, description="node names around the facet")
    plane: Plane
    fade: Fade | None


class DeleteFacet(Input):
    op: Literal["delete_facet"]
    label: str


class SetCrease(Input):
    op: Literal["set_crease"]
    facets: list[str] = Field(min_length=2, max_length=2, description="two facet labels, or a facet and 'base'")
    shape: Literal["ridge", "valley"]


class DeleteCrease(Input):
    op: Literal["delete_crease"]
    facets: list[str] = Field(min_length=2, max_length=2)


class SpliceChain(Input):
    op: Literal["splice_chain"]
    chain: Chain
    d_from_mm: float
    d_to_mm: float
    points: list[list[float]] = Field(max_length=160, description="[[d, value]] replacing the knots in d_from..d_to; "
                                                                  "value = y (outline), x_in (inner) or t (base)")


class OffsetChain(Input):
    op: Literal["offset_chain"]
    chain: Chain
    d_from_mm: float
    d_to_mm: float
    delta_mm: float
    taper_mm: float = Field(description="linear fade of the offset outside d_from..d_to (0 = none)")


class SetScalar(Input):
    op: Literal["set_scalar"]
    key: Literal["roll_mm", "tip_round_mm"]
    value: float


EDIT_MODELS = (SetNode, DeleteNode, SetFacet, DeleteFacet, SetCrease, DeleteCrease, SpliceChain, OffsetChain,
               SetScalar)
Edit = Union[EDIT_MODELS]                      # a plain anyOf (no discriminator key in the schema)


class EditList(Input):
    """The union as one model (its JSON schema is pinned by sha in the tests)."""
    edits: list[Edit] = Field(max_length=G.EDITS_MAX)


def without_titles(schema):
    """The JSON schema without pydantic's auto "title" keys (property names say it; ~30 % fewer characters for
    Astra to read on every request). Arguments are still validated by the tool's pydantic model."""
    if isinstance(schema, dict):
        return {k: without_titles(v) for k, v in schema.items()
                if not (k == "title" and isinstance(v, str))}
    if isinstance(schema, list):
        return [without_titles(v) for v in schema]
    return schema


def edits_schema() -> dict:
    """The §4.2 edits union as Astra sees it (EditList without titles)."""
    return without_titles(EditList.model_json_schema())


# --------------------------------------------------------------------------------------------- photo-dependent models
def models(photo_ids: list[str], compare_ids: list[str] | None = None, image_ids: list[str] | None = None) -> dict:
    """The models whose schema names the lab's photo ids: TargetId (the target photos), PhotoId (the photos with a
    camera: compare, judgement, points; default the target photos) and ImageId (every photo: photo_image; default
    PhotoId's)."""
    TargetId = Literal[tuple(photo_ids)]
    compare_ids = list(compare_ids or photo_ids)
    PhotoId = Literal[tuple(compare_ids)]
    ImageId = Literal[tuple(image_ids or compare_ids)]

    class PhotoJudgement(Input):
        photo: PhotoId
        verdict: Verdict
        largest_difference: str = Field(min_length=1, max_length=200,
                                        description="the largest visible difference in this photo")
        at_d_mm: float | None = Field(description="where it is: d mm, or null")
        at_y_mm: float | None = Field(description="where it is: y mm, or null")

    class Judgement(Input):
        compare_id: str = Field(description="the latest compare_id")
        versus_compare_id: str | None = Field(description="the earlier compare it is judged against, or null")
        overall: Verdict
        photos: list[PhotoJudgement] = Field(min_length=1, max_length=len(compare_ids),
                                             description="one entry per photo of that compare")
        best_checkpoint: str = Field(description="the checkpoint of the best revision so far")

    class PhotoPoints(Input):
        photo: PhotoId
        points: list[list[float]] = Field(min_length=1, max_length=16, description="native photo pixels [[u, v]]")

    class Depth(Input):
        photo: PhotoId
        d_mm: float = Field(description="d of the point (e.g. a node), mm")
        y_mm: float = Field(description="y of the point, mm")
        uv: list[float] = Field(min_length=2, max_length=2,
                                description="native photo pixel [u, v] where the photo shows that point")

    class Variant(Input):
        label: str = Field(description="a short name: a letter, then letters, digits, _ or -, not ending in _")
        edits: list[Edit] = Field(max_length=G.EDITS_MAX)

    return {"PhotoId": PhotoId, "TargetId": TargetId, "ImageId": ImageId, "Judgement": Judgement,
            "PhotoPoints": PhotoPoints, "Variant": Variant, "Depth": Depth}


# --------------------------------------------------------------------------------------------- output
def _finite(x):
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {str(k): _finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite(v) for v in x]
    return x


def result_text(result: dict, footer: str) -> str:
    body = {k: v for k, v in result.items() if k != "images"}
    return json.dumps(_finite(body), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n" + footer


def output(result: dict, footer: str) -> list:
    out = [ToolOutputText(text=result_text(result, footer))]
    out += [ToolOutputImage(image_url=data_url(p), detail="high") for p in result.get("images") or []]
    return out


# --------------------------------------------------------------------------------------------- registry
def run_settings(**settings) -> RunSettings:
    """The facet run's settings (§4.3): required tool choice kept after every call, final only on an accepted
    facet_finish, the context guard at CONTEXT_GUARD_TOKENS; the owner's effort, turns, K... in ``settings``."""
    return RunSettings(**{"tool_choice": "required", "reset_tool_choice": False, "finish_tool": "facet_finish",
                          "context_guard_tokens": CONTEXT_GUARD_TOKENS, **settings})


FINISH_DOC = """End the run with best_checkpoint, which is exported and validated. Accepted only when: (1) at least {k}
    distinct applied heads were compared; a head counts only when, against every other head counted, the built temple
    surface or outline moves by at least 0.2 mm somewhere; (2) one facet_variants call rendered at least 3 variants
    pairwise at least 0.3 mm apart; (3) you were shown a compare of best_checkpoint's revision at every target photo;
    (4) for stuck, the last 3 judgements cover 3 distinct heads (as in 1) and none says better. Otherwise it is
    refused with the missing ones named and the run continues.

    Args:
        status: matched (the renders and photos read as the same object) or stuck.
        best_checkpoint: The checkpoint to deliver.
        summary: What was matched and what still differs.
        judgement: The judgement of the latest compare when one is due, else null.
    """


def footer_of(ctx) -> str:
    context = getattr(ctx, "context", None)
    return context.footer() if hasattr(context, "footer") else footer_text()


def timeout_text(name: str):
    """The function_tool ``timeout_error_function``: the SDK's timeout as a facet result (status timeout, E_TIMEOUT)
    with the money / context footer every result ends with."""
    def text(ctx, error) -> str:
        secs = getattr(error, "timeout_seconds", None) or sdk_timeout_s(name)
        return result_text({"status": "timeout", "code": "E_TIMEOUT", "tool": name,
                            "message": f"{name} timed out after {secs:g} s and may still be running: until it ends "
                                       "every facet tool answers E_BUSY. A compare it makes needs no judgement; "
                                       "facet_inspect shows the head."}, footer_of(ctx))
    return text


def registry(lab, settings=None) -> list:
    """The seven function tools over ``lab`` (module doc); K of facet_finish = settings.finish_k (default 4)."""
    k = getattr(settings, "finish_k", 4) if settings is not None else 4
    vmax = lab.variant_photos_max()
    lock = lab.lock
    M = models(lab.photo_ids(), lab.compare_ids(), lab.image_ids())
    Judgement, PhotoPoints, Variant, PhotoId = M["Judgement"], M["PhotoPoints"], M["Variant"], M["PhotoId"]
    ImageId, Depth = M["ImageId"], M["Depth"]

    def tool(name: str):
        return partial(function_tool, name_override=name, timeout=sdk_timeout_s(name),
                       failure_error_function=fatal_tool_errors(WorkerFailed),
                       timeout_error_function=timeout_text(name))

    async def call(ctx: ToolContext, name: str, fn):
        call_id = getattr(ctx, "tool_call_id", None) or f"{name}-{time.time_ns()}"

        def locked():
            if not lock.acquire(timeout=0):
                run = lab.running or {}
                return {"status": "busy", "code": "E_BUSY",
                        "running": {"tool": run.get("tool"), "call_id": run.get("call_id"),
                                    "seconds": round(time.perf_counter() - run.get("t0", time.perf_counter()), 1)},
                        "message": "another facet tool call is still running; nothing was done"}
            lab.running = {"tool": name, "call_id": call_id, "t0": time.perf_counter()}
            try:
                return fn(call_id)
            except (LabRefusal, TempleError, G.EditError) as exc:
                return exc.to_dict()
            except WorkerFailed:
                raise
            except (ValueError, KeyError, TypeError) as exc:
                return {"status": "refused", "code": "E_LAB_ARGS", "message": f"{type(exc).__name__}: {exc}"[:600]}
            finally:
                lab.running = None
                lock.release()
        try:
            result = await asyncio.to_thread(locked)
        except asyncio.CancelledError:
            lab.mark_unseen(call_id)             # the SDK timed the call out; its thread may still be running
            raise
        return output(result, footer_of(ctx))

    def dumped(items):
        return None if items is None else [i.model_dump() for i in items]

    @tool("facet_inspect")
    async def facet_inspect(ctx: ToolContext):
        """The applied head: its revision's spec (nodes as [d, y, t], a solved t as "solve -> value"; facets,
        declared creases, a 10 mm station table d, y_top, y_bot, x_in, t_top, t_bot), its receipt (crease lines;
        each crease between two facets with its margin: each face's corner farthest across the crease and that
        corner's mm from the other face's plane, extended (planes are flat in X = x_in(d) + t, not in t; + = a ridge
        there; a t error that size at that corner flips it (when no end of the crease is solved)), and the solved
        corners that set it (its ends and those corners) with the facet each is solved from; warnings, thickness,
        border ramps),
        the warnings inherited from revision 0 (later receipts leave them out while unchanged), recent revisions and
        applied checkpoints, the photo ids, the latest compare and any judgement due. Images:
        the chart (facets shaded by lean, ridges orange, valleys blue, nodes 'name t=', thickness contours, decal and
        hinge footprints) and the section strip (the head black, the original grey, its parent teal)."""
        return await call(ctx, "facet_inspect", lambda cid: lab.inspect())

    @tool("photo_image")
    async def photo_image(ctx: ToolContext, photo: ImageId,
                          crop: Annotated[list[int], Field(min_length=4, max_length=4)] | None):
        """A photo (a target photo or a photo of the same model in another colour, including those without a
        calibrated camera), or a crop of it, at its native scale (downscaled only to a long side of 1024), with the
        transform back to native photo pixels.

        Args:
            photo: The photo id.
            crop: [x0, y0, x1, y1] in native photo pixels, or null for the whole photo.
        """
        return await call(ctx, "photo_image", lambda cid: lab.photo(photo, crop))

    @tool("photo_compare")
    async def photo_compare(ctx: ToolContext, photos: list[PhotoId] | None, crop: Literal["temple", "full"] | None,
                            points_px: list[PhotoPoints] | None,
                            depths: Annotated[list[Depth], Field(min_length=1, max_length=DEPTHS_MAX)] | None = None):
        """Compare the applied head with photos at their fitted cameras and temple poses: per photo one image with
        rows photo / render / overlay (model outline magenta, ridges orange, valleys blue, a d grid every 10 mm and a
        y grid every 5 mm) / gloss-black render, all with the photo's own contrast stretch (a photo of another colour
        with a calibrated camera: photo / overlay / gloss-black render, no native row); text per photo: temple
        IoU, px/mm, the crop box and its scale k, outer-edge offsets photo minus model in mm every 10 mm (top and rear
        photos; per temple: charted, mirrored; "start" = the unedited model's), points_px charted to (d, y, t) (t is
        the model's) and depths read (t measured from the photo, the model's t there, px per mm of t, the pixel's
        miss from the point's depth line in px: large means the (d, y) is not the point shown, small does not
        confirm d, which runs at an angle to the depth line; which temple).
        Returns a compare_id; the next facet_edit, facet_variants, facet_restore or facet_finish must carry a
        judgement of it. A photo without a calibrated camera cannot be compared (photo_image shows it).

        Args:
            photos: Photo ids, or null for the default set (target photos showing the temple large).
            crop: temple (default) or full.
            points_px: Native photo pixels to chart through the model, or null.
            depths: Thickness readings, or null: for a point (d, y), the t whose point (d, y, t) the photo's camera
                shows nearest the pixel uv, independent of the model's depth.
        """
        return await call(ctx, "photo_compare", lambda cid: lab.compare(cid, photos, crop, dumped(points_px),
                                                                         dumped(depths)))

    @tool("facet_edit")
    async def facet_edit(ctx: ToolContext, parent_revision: int, reset_layout: bool, edits: list[Edit],
                         judgement: Judgement | None, note: str, apply: bool, photos: list[PhotoId] | None):
        """Make a new revision from parent_revision: the edits apply in order, all or none; a refused edit returns
        at once with its index and code, a refused build with its code (E_CREASE_SHAPE names the solved corners that
        set that crease; E_RELIEF_STEP on a crease-mode facet names the rise_per_mm that meets it). reset_layout
        clears nodes, facets and creases first. diff is against the parent; against the scene's head revision:
        diff_vs_head (when the parent is another revision) and crease_vs_head (creases built with the other shape
        than in the head). The receipt lists every built crease (shape, dihedral, declared or undeclared), margins
        (as facet_inspect's, only for creases flipped against the head, with a solved corner or with a margin under
        1 mm, flips first, a few, grouped by their solved corners: "K solved: F1|F2 P -1.1/Q -4.0, ..."), solved:
        corners solved from a facet's plane (not by design) whose t moved from the head's, warnings (not those
        inherited unchanged from revision 0), thickness, border ramps (facet, height mm: + proud of the base, -
        below it, slope, d; also the W_BORDER_RAMP line's items);
        images: chart and section strip. With apply true (the usual choice) both temples are rebuilt from the
        original as a new checkpoint and compared with the given or default photos (a new compare_id; its text as
        photo_compare's, without the start model's offsets).

        Args:
            parent_revision: The revision to edit (any earlier one, e.g. a variant).
            reset_layout: True clears nodes, facets and creases before the edits.
            edits: Ordered edits (at most 64).
            judgement: The judgement of the latest compare when one is due, else null.
            note: What this edit tries to change and why.
            apply: True builds the revision into the scene and compares it.
            photos: Photo ids for the compare, or null for the default set.
        """
        return await call(ctx, "facet_edit", lambda cid: lab.edit(
            cid, parent_revision, reset_layout, [e.model_dump() for e in edits],
            None if judgement is None else judgement.model_dump(), note, apply, photos))

    @tool("facet_variants")
    async def facet_variants(ctx: ToolContext, parent_revision: int,
                             variants: Annotated[list[Variant], Field(min_length=2, max_length=6)],
                             photos: Annotated[list[PhotoId], Field(min_length=1, max_length=vmax)],
                             judgement: Judgement | None):
        """Build 2..6 variants of parent_revision (each its own edit list) and render each beside the head at the
        photos' cameras without changing the scene: one sheet per photo (photo, head, variants; a refused variant
        is a labelled panel). Every render panel shows the photo's outline (magenta) and that model's own crease
        lines from 6° (ridges orange, valleys blue, as photo_compare's overlay), so crease layouts can be compared
        side by side. A target photo's panels are the native render; a photo of another colour shows the
        gloss-black render with photo_compare's gain and stretch for it. Each built variant becomes a revision that
        facet_edit can apply with no edits. Text: each variant's revision, its distance from the head, its creases,
        crease_vs_head (creases built with the other shape than in the head), margins (as facet_edit's), solved
        (as facet_edit's), a refused variant's code and message (as facet_edit's), the pairwise distances (max |dX| or
        outline shift, mm) and spread_ok (3 rendered variants pairwise >= 0.3 mm apart). The sheets have no
        compare_id and need no judgement: apply the variant that matches best with facet_edit; its compare is judged
        as usual.

        Args:
            parent_revision: The revision the variants edit.
            variants: 2..6 labelled edit lists (labels distinct ignoring case, not 'head').
            photos: Photo ids with a camera, each compared before; at most 3 target photos (a 4th must be a photo
                of another colour).
            judgement: The judgement of the latest compare when one is due, else null.
        """
        return await call(ctx, "facet_variants", lambda cid: lab.variants(
            cid, parent_revision, [v.model_dump() for v in variants], photos,
            None if judgement is None else judgement.model_dump()))

    @tool("facet_restore")
    async def facet_restore(ctx: ToolContext, checkpoint: str, judgement: Judgement | None):
        """Make an earlier applied checkpoint the scene again (its revision becomes the head); crease_vs_head: the
        creases it builds with the other shape than the head it replaces, naming their solved corners.

        Args:
            checkpoint: A checkpoint that holds an applied revision.
            judgement: The judgement of the latest compare when one is due, else null.
        """
        return await call(ctx, "facet_restore", lambda cid: lab.restore_tool(
            cid, checkpoint, None if judgement is None else judgement.model_dump()))

    async def facet_finish(ctx: ToolContext, status: Literal["matched", "stuck"], best_checkpoint: str,
                           summary: str, judgement: Judgement | None):
        return await call(ctx, "facet_finish", lambda cid: lab.finish(
            cid, status, best_checkpoint, summary, None if judgement is None else judgement.model_dump(), k))
    facet_finish.__doc__ = FINISH_DOC.format(k=k)
    facet_finish = tool("facet_finish")(facet_finish)

    tools = [facet_inspect, photo_image, photo_compare, facet_edit, facet_variants, facet_restore, facet_finish]
    assert tuple(t.name for t in tools) == TOOL_NAMES
    for t in tools:
        t.params_json_schema = without_titles(t.params_json_schema)
    return tools
