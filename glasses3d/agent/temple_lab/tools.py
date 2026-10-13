"""The seven facet tools of WP6 v3 (plan/WP6_V3.md §6): strict-JSON Agents SDK function tools over a Lab.

    registry(lab, settings=None) -> [facet_inspect, photo_image, photo_read, photo_compare, facet_edit,
                                     facet_variants, facet_finish]

Each tool calls the Lab method of its role, positionally in this order (I5; lab.py implements them, I6):
    facet_inspect   Lab.inspect()
    photo_image     Lab.photo(photo, crop, enhance, scale)
    photo_read      Lab.read(call_id, points, drop)
    photo_compare   Lab.compare(call_id, photos, crop, zoom, points)
    facet_edit      Lab.edit(call_id, parent_revision, reset_layout, edits, judgement, note, object, apply, photos)
    facet_variants  Lab.variants(call_id, parent_revision, variants, photos, judgement)
    facet_finish    Lab.finish(call_id, status, best_checkpoint, summary, judgement, k)
with the pydantic models dumped to plain dicts (``model_dump``). v2's facet_restore and photo_compare ``depths`` are
gone (WP6_V3 §6: one-photo depths put run 6's hinge corner 1.2 mm off, and a restore is a facet_edit of that revision
with no edits); depth comes from photo_read.

Every tool is async with the SDK timeout of loop.HARD_TIMEOUT_S + 10 s; the work runs in a worker thread that takes
the lab's scene lock (Lab.lock) with timeout 0 (a call that finds it held, e.g. behind a timed-out call still running,
returns {status: busy, code: E_BUSY, running} at once). A call the SDK times out answers {status: timeout, code:
E_TIMEOUT} with the footer (timeout_text) and is Lab.mark_unseen, so a compare its thread still makes demands no
judgement. A result is ONE text part, compact JSON (numbers rounded) then the footer line "settled $x / cap $y ·
context n tokens" (loop.RunStatus, the run context), followed by its images (ToolOutputImage, detail high).
WorkerFailed stays fatal (the run ends failed_worker). Refusals (LabRefusal, TempleError, geometry.EditError) return
their to_dict; ValueError / KeyError / TypeError return E_LAB_ARGS. Arguments the SDK refuses against a tool's
parameters model ("Invalid JSON input for tool X", details dropped unless tool data is logged) return {status:
refused, code: E_ARGS, errors} with each problem by path (args_failure, schema_errors: one line for an edit whose op
names no edit, not one per union member; the 2026-10-13 blind stand-in's probe edit got no field or path).

The edits are a strict anyOf union (EDIT_MODELS; its JSON schema, edits_schema(), is pinned by sha in
tests/agent/test_temple_lab_v3.py): set_node t {mode: reading | mm | base | solve, mm} with d_mm / y_mm null for the
reading's own position, and outline (top | bottom | none | null: OUTLINE_TEXT says what each does: top / bottom keep
the node on that edge by moving the edge's knot to it, so the edge bends there when the node lies off its line, and the
result gives that bend (facet_edit's receipt outline_bends, a variant's notes; told of the revision after the last
edit, v3-standin-fix-review); none a free node, which may lie beyond the outline; null as before the edit;
kept through re-reads and chain edits by geometry.apply_edits; v3p3 e2e P1-1: nothing kept or said it, and the first
Prada layout without a hand splice was refused E_SHORT_EDGE; the 2026-10-13 blind stand-in read "a corner on that
edge" as unclear and its E and F knots cut a notch and a kink into the outline); solve stated as what the fit does
(the flat faces' planes carried to the node and a weak prior; its stored reading unused: v3p3 owner P2-3, a flat crown
carried K 12 mm from H, -6.8 sd off its reading, while the text said "set by the faces and readings around it");
set_facet {plane: {mode: flat | warp}, soft} (a facet is clipped to the outline, so a corner may lie beyond it); no
fade and no crease + rise plane (WP6_V3 §6: both were harmful tools, FINDINGS §0.7); geometry.apply_edits converts them
to the design spec. Judgements name up to
state.DIFFERENCES_MAX differences per photo, each at its (d, y) (WP6_V3 §6); one given when none is due is not stored
(state.check_judgement).

Photo ids (lab.py module doc): photo_image takes every photo (Lab.image_ids: target, colourway = another colour with a
calibrated camera, reference = no camera); photo_read picks, photo_compare (photos, zoom, points_px), facet_edit's
compare and judgements take the photos with a camera (Lab.compare_ids); facet_variants takes those too, at most
Lab.variant_photos_max() of them. ``photos`` null is seeing's default set, the target photos whose temple shows at
least seeing.TEMPLE_ROI_MIN_PX native px (a photo of another colour only when named), and the texts say so with that
number (v3p3 astra P3-9: "null for the target photos" while the result's left_out named 04): finish precondition 3
needs the photos it leaves out. The tool texts name no photo id: the enums and facet_inspect's photo list carry the
job's.

Descriptions are short neutral facts (WP6_V3 P6). Every number in them is formatted from the constant the code
enforces (the *_DOC texts below, set as docstrings by ``doc``; test_description_numbers_are_the_codes ties each), and
each scale is stated with its cap: photo_image k = min(scale, sheets.IMAGE_MAX / long side), a zoom panel
compare.zoom_scale, a variant panel sheets.variant_scale (review 2026-10-12: the texts promised native pixels where a
whole photo came out at k 0.254). Names (nodes, facets, readings, variants) state geometry.NAME_RULE. A typed t is
stated as what the fit makes of it (an observation of fit.TYPED_SD_MM, not an exact value), a declared crease as what
the fit does with it (MIN_CREASE_DEG, E_FIT_SHAPE beyond ACCEPT_DCHI2), a reading as a snapshot (a node keeps the
reading it was set from: geometry.stale_reads), and the object note's rule as the state's (state.object_due: the
latest note's layout, whatever the parent).
Annotations are evaluated eagerly (no __future__ import): the tools close over the lab's photo-id Literals.
"""
import asyncio
import json
import math
import time
from typing import Annotated, Literal, Union

from agents import function_tool
from agents.exceptions import ModelBehaviorError
from agents.function_schema import function_schema
from agents.strict_schema import ensure_strict_json_schema
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from glasses3d.agent.journal import WorkerFailed
from glasses3d.agent.loop import RunSettings, fatal_tool_errors, footer_text, sdk_timeout_s
from glasses3d.agent.seeing import GRID_STATION_MM, TEMPLE_ROI_MIN_PX, ZOOM_MIN_PX
from glasses3d.g3d.temple import fit as FIT
from glasses3d.g3d.temple.spec import DEFAULT_OPTIONS, SOFT_BAND_MM, SOFT_SHARP_DEG, WARP_MIN_CORNERS, TempleError
from glasses3d.g3d_host import compare as C
from . import geometry as G
from . import readings as RD
from . import sheets as SH
from . import state as ST
from .lab import NOTE_MAX, VARIANT_TARGETS_MAX, VARIANTS_MAX, VARIANTS_MIN, data_url
from .state import LabRefusal

CONTEXT_GUARD_TOKENS = 240_000          # §4.3: the one-epoch image filter from this many counted input tokens
TOOL_NAMES = ("facet_inspect", "photo_image", "photo_read", "photo_compare", "facet_edit", "facet_variants",
              "facet_finish")
Chain = Literal["outline.top", "outline.bottom", "inner", "base.top", "base.bottom"]
SNAP_MM = DEFAULT_OPTIONS["snap_mm"]    # a node within this of an outline edge lies on it (the parse snaps it there)
CHAIN_TEXT = ("outline.top / outline.bottom: the outline's top / bottom edge (value y; a node on it, within "
              f"{SNAP_MM:g} mm, moves with it in y, a node set from a reading at most {G.READ_POSITION_MM:g} mm from "
              "where it was read); inner: the inner face (value x_in); base.top / base.bottom: the plain arm's t at "
              "the top / bottom edge (linear in y between)")
OUTLINE_TEXT = (f"how the node relates to the outline (a node within {SNAP_MM:g} mm of an outline edge lies on it: "
                "the build puts it there). top / bottom: the node is kept on that edge: the edge's knot is moved to "
                "the node's (d, y) (the knot it held there moves, else a knot is added; d inside the outline's d "
                "range), so the edge passes through the node, and where the node lies off the edge's line the edge "
                "bends there (the result gives the bend in mm and deg: facet_edit's receipt in outline_bends, a "
                "variant in its notes); chain edits of that edge "
                "then carry the node. none: a free node: the outline keeps its shape (a knot the node held stays "
                "where it was) and the node may lie inside, on or beyond the outline (a corner beyond it: facets are "
                "clipped to the outline). null: as before this edit (a node on an edge stays on it, its knot moving "
                "with it; a new node is free)")
NAME_TEXT = f"{G.NAME_RULE}; not 'base'"
PAIR_TEXT = "two facet labels, or a facet and 'base' (the plain arm)"
DEFAULT_PHOTOS = (f"null for the target photos whose temple shows at least {TEMPLE_ROI_MIN_PX:,} native px (the "
                  "result's left_out names the others; a photo of another colour only when named)")
JUDGEMENT_TEXT = ("The judgement of the latest compare when one is due, else null (a judgement when none is due is "
                  "not stored).")
Verdict = Literal["better", "same", "worse", "mixed"]
Pixel = Annotated[list[float], Field(min_length=2, max_length=2)]
Trace = Annotated[list[Pixel], Field(min_length=RD.EDGE_POINTS_MIN, max_length=RD.EDGE_POINTS_MAX)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# --------------------------------------------------------------------------------------------- the edits union
class Thickness(Input):
    mode: Literal["reading", "mm", "base", "solve"] = Field(
        description=f"reading: the stored reading of the node's name (photo_read), the node within "
                    f"{G.READ_POSITION_MM:g} mm of where it was read; mm: a typed t, which the fit weighs with the "
                    f"readings as an observation of sd {FIT.TYPED_SD_MM:g} mm (the built t can differ from it, and it "
                    f"can move readings near it); base: the plain arm's t at (d, y); solve: no value of its own: the "
                    f"fit sets it from the flat faces through it (their planes through their other corners, carried "
                    f"to it) and a weak prior of sd {FIT.PRIOR_SD_MM:g} mm toward the plain arm; a stored reading of "
                    f"its name is not used (W_FIT_OFF_READING says how far it lies)")
    mm: float | None = Field(description="t mm from the inner face for mode mm, else null")


class SetNode(Input):
    op: Literal["set_node"]
    name: str = Field(description=NAME_TEXT)
    d_mm: float | None = Field(description="mm back from the hinge; null: the reading's d (mode reading only)")
    y_mm: float | None = Field(description="mm up; null: the reading's y (mode reading only)")
    t: Thickness
    outline: Literal["top", "bottom", "none"] | None = Field(default=None, description=OUTLINE_TEXT)


class DeleteNode(Input):
    op: Literal["delete_node"]
    name: str


class Plane(Input):
    mode: Literal["flat", "warp"] = Field(
        description=f"flat: one plane through its corners; warp: one face through its corners with straight edges "
                    f"and no line inside ({WARP_MIN_CORNERS} or more corners)")


class SetFacet(Input):
    op: Literal["set_facet"]
    label: str = Field(description=NAME_TEXT)
    poly: list[str] = Field(min_length=3, max_length=32,
                            description="node names around the facet; the facet is clipped to the outline, so a "
                                        "corner may lie beyond it (two corners on one edge give a straight side, "
                                        "and plain arm fills the space up to a curved edge)")
    plane: Plane
    soft: bool = Field(description=f"true: its borders with the plain arm are soft (a {SOFT_BAND_MM:g} mm band, "
                                   f"smooth where it folds under {SOFT_SHARP_DEG:g} deg); a crease with another facet "
                                   f"is never soft")


class DeleteFacet(Input):
    op: Literal["delete_facet"]
    label: str


class SetCrease(Input):
    op: Literal["set_crease"]
    facets: list[str] = Field(min_length=2, max_length=2, description=PAIR_TEXT)
    shape: Literal["ridge", "valley"] = Field(
        description=f"the declared shape: the fit builds this crease with it, folding at least "
                    f"{FIT.MIN_CREASE_DEG:g} deg, or refuses E_FIT_SHAPE when the declarations raise the fit's chi2 "
                    f"more than {FIT.ACCEPT_DCHI2:g} above the fit without them")


class DeleteCrease(Input):
    op: Literal["delete_crease"]
    facets: list[str] = Field(min_length=2, max_length=2, description=PAIR_TEXT)


class SpliceChain(Input):
    op: Literal["splice_chain"]
    chain: Chain = Field(description=CHAIN_TEXT)
    d_from_mm: float
    d_to_mm: float
    points: list[list[float]] = Field(max_length=G.CHAIN_POINTS_MAX,
                                      description="[[d, value]] replacing the knots in d_from..d_to; value = y "
                                                  "(outline), x_in (inner) or t (base)")


class OffsetChain(Input):
    op: Literal["offset_chain"]
    chain: Chain = Field(description=CHAIN_TEXT)
    d_from_mm: float
    d_to_mm: float
    delta_mm: float
    taper_mm: float = Field(description="outside d_from..d_to the offset falls linearly to 0 over this many mm (0 = "
                                        "none)")


class SetScalar(Input):
    op: Literal["set_scalar"]
    key: Literal["roll_mm", "tip_round_mm"] = Field(
        description="roll_mm: the rounding of both outline edges along the temple (0 = crisp); tip_round_mm: the "
                    "rounding of the two tip corners")
    value: float = Field(description=f"mm, 0 to {G.SCALAR_LIMIT_MM:g}")


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
    """The edits union as Astra sees it: EditList's JSON schema made strict as the SDK makes a tool's parameters
    (agents.strict_schema.ensure_strict_json_schema: every property required, a null default dropped; SetNode.outline
    defaults to null for internal callers, fixtures and the T5 rehearsal's scripted edits, while Astra must give it),
    without titles."""
    return without_titles(ensure_strict_json_schema(EditList.model_json_schema()))


# --------------------------------------------------------------------------------------------- photo-dependent models
def models(photo_ids: list[str], compare_ids: list[str] | None = None, image_ids: list[str] | None = None) -> dict:
    """The models whose schema names the lab's photo ids: TargetId (the target photos), PhotoId (the photos with a
    camera: picks, compares, zooms, judgements, points; default the target photos) and ImageId (every photo:
    photo_image; default PhotoId's)."""
    TargetId = Literal[tuple(photo_ids)]
    compare_ids = list(compare_ids or photo_ids)
    PhotoId = Literal[tuple(compare_ids)]
    ImageId = Literal[tuple(image_ids or compare_ids)]

    class Difference(Input):
        what: str = Field(min_length=1, max_length=ST.DIFFERENCE_MAX,
                          description="a difference between the photo and the render")
        d_mm: float = Field(description="where it is: d mm")
        y_mm: float = Field(description="where it is: y mm")

    class PhotoJudgement(Input):
        photo: PhotoId
        verdict: Verdict
        differences: list[Difference] = Field(max_length=ST.DIFFERENCES_MAX,
                                              description=f"up to {ST.DIFFERENCES_MAX} differences in this photo")

    class Judgement(Input):
        compare_id: str = Field(description="the latest compare_id")
        versus_compare_id: str | None = Field(description="the earlier compare it is judged against, or null")
        overall: Verdict
        photos: list[PhotoJudgement] = Field(min_length=1, max_length=len(compare_ids),
                                             description="one entry per photo of that compare")
        best_checkpoint: str = Field(description="the checkpoint of the best revision so far")

    class PhotoPoints(Input):
        photo: PhotoId
        points: list[Pixel] = Field(min_length=1, max_length=16, description="native photo pixels [[u, v]]")

    class ReadPick(Input):
        photo: PhotoId
        uv: Pixel | None = Field(description="corner: the native pixel [u, v] of the point; crossing: null")
        edges: list[Trace] | None = Field(min_length=2, max_length=2,
                                          description=f"crossing: two edges through the point, each traced by "
                                                      f"{RD.EDGE_POINTS_MIN}..{RD.EDGE_POINTS_MAX} native pixels "
                                                      f"[u, v] along it; corner: null")
        sd_px: float | None = Field(description=f"the pick's error in px ({RD.SD_PX_MIN:g}..{RD.SD_PX_MAX:g}), or "
                                                f"null for {RD.SD_PX_DEFAULT:g}")

    class ReadPoint(Input):
        name: str = Field(description=f"the reading's name, the node it is for: {NAME_TEXT}")
        kind: Literal["corner", "crossing"] = Field(description="corner: one pixel per photo; crossing: two traced "
                                                                "edges per photo")
        picks: list[ReadPick] = Field(min_length=RD.PHOTOS_MIN, max_length=RD.PHOTOS_MAX,
                                      description="one per photo, photos with a camera")

    class Zoom(Input):
        photo: PhotoId
        box: list[int] = Field(min_length=4, max_length=4,
                               description=f"[x0, y0, x1, y1] native photo pixels, at least {ZOOM_MIN_PX} px a side")
        geometry: bool = Field(description="true: the fourth panel is the geometry view (the model's faces without "
                                           "light)")

    class Variant(Input):
        label: str = Field(description=f"a short name: {G.NAME_RULE}, not ending in _")
        edits: list[Edit] = Field(max_length=G.EDITS_MAX)

    return {"PhotoId": PhotoId, "TargetId": TargetId, "ImageId": ImageId, "Judgement": Judgement,
            "PhotoJudgement": PhotoJudgement, "Difference": Difference, "PhotoPoints": PhotoPoints,
            "ReadPick": ReadPick, "ReadPoint": ReadPoint, "Zoom": Zoom, "Variant": Variant}


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


# The tool texts (WP6_V3 §6, P6: short neutral facts). Every number is formatted from the constant the code enforces
# (test_description_numbers_are_the_codes ties each): the photo view's IMAGE_MAX, compare.zoom_scale's ZOOM_MAX_K and
# ZOOM_PANEL_PX, sheets.variant_scale's VARIANT_K_MAX and cell, the lab's variant counts, the spread's
# VARIANT_DISTINCT_MM. The review of 2026-10-12 found "native pixels" / "native scale" promised where these caps
# shrink the image (a whole photo at scale 1 came out at k 0.254), and literals that would drift from their constants.
PHOTO_IMAGE_DOC = f"""A photo (any photo id, with or without a camera) or a crop of it, at scale k = min(scale,
    {SH.IMAGE_MAX} / its long side in native px), with the transform back to native photo pixels (it gives k): a
    whole photo, or a crop longer than {SH.IMAGE_MAX} px, is shown below native scale even at scale 1.

    Args:
        photo: The photo id.
        crop: [x0, y0, x1, y1] in native photo pixels, or null for the whole photo.
        enhance: True stretches the contrast.
        scale: 1 to {ST.SCALE_MAX}: the crop enlarged up to this many times, its long side at most {SH.IMAGE_MAX}
            px.
    """

PHOTO_READ_DOC = f"""Read named points from photos and keep them. Each point is picked in {RD.PHOTOS_MIN} to
    {RD.PHOTOS_MAX} photos with a camera from different directions, as a corner (one pixel per photo) or a crossing
    (two traced edges per photo, which the host intersects); the host triangulates it. Per reading: d, y, X and t with
    their sd (pick and camera parts), each photo's residual, the slide line a reading from two directions cannot
    check, warnings, and a pick sheet image (each photo's crop with the picks, the traced edges and the read point's
    projection). A reading replaces a stored one of its name. A node keeps the reading it was set from: replacing or
    dropping the stored reading does not change the node; set_node with t mode reading sets a node from the stored
    reading of its name.

    Args:
        points: The points to read (at most {ST.READ_POINTS_MAX}), or [].
        drop: Names of stored readings to remove (at most {ST.READ_DROP_MAX}), or [].
    """

PHOTO_COMPARE_DOC = f"""Compare the applied head with photos at their fitted cameras and temple poses: per photo one
    image of the photo, the render, and the photo with the model's outline (magenta) and its own creases (ridge
    orange, valley blue, flat grey, a soft border dashed grey). Side views print the outline offsets every
    {GRID_STATION_MM:g} mm (photo minus model, mm; a station not measured gives its reason); a view from above shows
    the top face, so for the top it prints the arm's top height on that face's far edge, less its median over the
    stations (from below, the bottom's). zoom: per box one 2 x 2 image, every panel at k = min({C.ZOOM_MAX_K:g},
    {C.ZOOM_PANEL_PX[0]} / box width, {C.ZOOM_PANEL_PX[1]} / box height) (native or larger for a box up to
    {C.ZOOM_PANEL_PX[0]} x {C.ZOOM_PANEL_PX[1]} native px, smaller beyond; the text gives k): photo | render / photo
    with the model's outline and its creases labelled with their angles | the geometry view or the render with the
    creases. points_px: pixels
    charted to (d, y, t) on the model. Returns a compare_id; the next facet_edit, facet_variants or facet_finish
    carries a judgement of it, one entry per photo shown (zoomed photos included). facet_variants and facet_finish
    count a photo as compared only when it was shown in full, not by a zoom alone.

    Args:
        photos: Photo ids with a camera, or {DEFAULT_PHOTOS}; [] with a zoom: the zooms only.
        crop: temple (default) or full.
        zoom: At most {ST.ZOOM_MAX} boxes, or null.
        points_px: Native photo pixels to chart through the model, or null.
    """

FACET_EDIT_DOC = f"""Make a new revision from parent_revision. The edits apply in order, all or none: a refused edit
    returns its index and code. The host fits the design to the readings (all faces jointly, by their errors) and
    builds the fitted temple; a refused fit or build returns its code and facts. The receipt: the fit lines (misfits
    in mm and sd, faces, warps), every built crease (shape, angle, the fit's sd, declared or not; beside a warp face
    its range along the edge, "(least-largest)"; a facet|base one any plain arm between a straight side and a curved
    outline), warnings, the diff against the parent, unused_readings when they changed, and against the head
    diff_vs_head, crease_vs_head (ridge <-> valley) and edges_vs_head (a crease that appears or goes flat). An object
    note is required on the first facet_edit and whenever the design's layout (facets or node names) differs from the
    layout the latest note was written for, whatever the parent (a variant or an older revision too), else E_OBJECT;
    a note in a refused call is kept only when the result says "object": "recorded". With apply true the revision is
    built into the scene and compared with the given or the target photos (a new compare_id).

    Args:
        parent_revision: The revision to edit (any earlier one, e.g. a variant).
        reset_layout: True clears nodes, facets and creases before the edits.
        edits: Ordered edits (at most {G.EDITS_MAX}). The result states what they did beyond the diff: the
            receipt's deleted_creases (the crease declarations a delete_facet removed, and which this list declared
            again) and outline_bends (each bend a set_node outline knot makes in its edge: mm off the edge's line,
            deg of turn), and notes (outline knots moved with a corner, read corners set beyond the outline).
        judgement: {JUDGEMENT_TEXT}
        note: What this edit tries to change and why (at most {NOTE_MAX} characters).
        object: The object note (at most {ST.OBJECT_MAX} characters): each face, vertex and edge, the photos that
            show it, each edge's shape and why, where the faceted part ends, what is uncertain; or null.
        apply: True builds the revision into the scene and compares it.
        photos: Photo ids for the compare, or {DEFAULT_PHOTOS}.
    """

FACET_VARIANTS_DOC = f"""Build {VARIANTS_MIN} to {VARIANTS_MAX} variants of parent_revision (each its own edit list,
    fitted and built as facet_edit's) and render each beside the head at the photos' cameras without changing the
    scene. Per photo a sheet of the part where the arms differ from the head (else the faceted zone), on the temple
    whose outer face that photo sees (charted or mirrored), at k up to {SH.VARIANT_K_MAX:g} (native or larger when
    that part fits a {SH.CELL_W} x {SH.CELL_H_MAX} px panel, smaller beyond; the sheet's title gives k): the photo
    beside the photo with the head's creases, and per model a clean render beside one with the photo's outline and
    that model's creases labelled with their angles. Text per variant: its revision, its distance from the head (mm:
    the surface along its normal or the outline), how far its ridges and valleys lie from the head's (mm), its fit
    lines and creases, or a refused variant's stage, code and facts; the pairwise distances (mm) and spread_ok
    ({ST.SPREAD_VARIANTS} rendered variants pairwise >= {G.VARIANT_DISTINCT_MM:g} mm apart); an arm that builds the
    head's own design is no variant. A built variant is a revision facet_edit can apply with no edits. The sheets
    have no compare_id.

    Args:
        parent_revision: The revision the variants edit.
        variants: {VARIANTS_MIN} to {VARIANTS_MAX} labelled edit lists (labels distinct ignoring case, not 'head').
        photos: Photo ids with a camera, each shown in a full compare before (a zoom alone does not count); at most
            {VARIANT_TARGETS_MAX} of them target photos (any other a photo of another colour).
        judgement: {JUDGEMENT_TEXT}
    """

FINISH_DOC = """End the run with best_checkpoint, which is exported and validated. Accepted only when: (1) at least {k}
    distinct applied heads were compared in full; a head counts only when, against every other head counted,
    {different}; (2) one facet_variants call rendered at least {spread} variants pairwise at least {spread_mm:g} mm
    apart; (3) you were shown a full compare of best_checkpoint's revision at every photo with a camera (a zoom alone
    does not count); (4) an object note exists; (5) for stuck, a facet_variants result with a rendered variant was
    shown after the latest applied head, and the last {last} judgements cover {last} distinct heads with none "better"
    (one judgement per compare; heads distinct as in 1). Otherwise it is refused with the missing ones named and the
    run continues.

    Args:
        status: matched (the renders and photos read as the same object) or stuck.
        best_checkpoint: The checkpoint to deliver.
        summary: What was matched and what still differs.
        judgement: {judgement}
    """


def doc(text: str):
    """Set a tool function's docstring (its description and Args) before function_tool parses it: the texts above
    are formatted from the code's constants, which a docstring literal cannot be."""
    def wrap(fn):
        fn.__doc__ = text
        return fn
    return wrap


def footer_of(ctx) -> str:
    context = getattr(ctx, "context", None)
    return context.footer() if hasattr(context, "footer") else footer_text()


EDIT_OPS = {m.model_fields["op"].annotation.__args__[0]: m.__name__ for m in EDIT_MODELS}   # op -> its model's name
ARGS_ERRORS_MAX = 8             # a schema refusal lists this many problems, then "+N more"
ARGS_ERROR_CHARS = 200          # ... each in at most this many characters


def _loc_text(loc) -> str:
    """A pydantic error location as a path: ("edits", 0, "t", "mode") -> "edits[0].t.mode"."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def schema_errors(model, raw) -> list[str] | None:
    """What is wrong with a tool call's raw arguments against its parameters model, one line per problem: "edits[0]:
    op 'help' is not one of set_node, ..." for an edit whose op names no edit (one line, not one per union member;
    "no op is given" without one, "an edit is an object with an op" for a non-object; v3-standin-fix-review: these
    read "op None is not one of"), else the member's own errors ("edits[2].t.mm: Input should be a valid number");
    None when they validate (the
    error was something else). Why (2026-10-13 blind stand-in, call 11): an edits list the parameters refused came back
    as the SDK's "Invalid JSON input for tool facet_edit" with no field or path, since the SDK drops the validation
    text unless tool data is logged."""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError as exc:
        return [f"the arguments are not JSON: {exc}"[:ARGS_ERROR_CHARS]]
    if not isinstance(data, dict):
        return ["the arguments must be one JSON object"]
    try:
        model.model_validate(data)
        return None
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
    lines, said = [], set()
    for err in errors:
        loc = list(err.get("loc") or ())
        k = next((j for j, part in enumerate(loc) if part in EDIT_OPS.values()), None)
        if k is not None:                       # an edits union member: keep the errors of the edit's own op only
            item = data
            for part in loc[:k]:
                if isinstance(item, list) and isinstance(part, int) and 0 <= part < len(item):
                    item = item[part]
                elif isinstance(item, dict) and part in item:
                    item = item[part]
                else:
                    item = None
                    break
            raw_op = item.get("op") if isinstance(item, dict) else None
            op = raw_op if isinstance(raw_op, str) else None
            if op not in EDIT_OPS:
                where = _loc_text(loc[:k])
                if where not in said:
                    said.add(where)
                    what = (f"op {repr(raw_op)[:40]} is not one of" if isinstance(item, dict) and "op" in item else
                            "no op is given; op is one of" if isinstance(item, dict) else
                            "an edit is an object with an op, one of")
                    lines.append(f"{where}: {what} {', '.join(EDIT_OPS)}")
                continue
            if loc[k] != EDIT_OPS[op]:
                continue
            loc = loc[:k] + loc[k + 1:]
        text = f"{_loc_text(loc) or 'arguments'}: {err.get('msg')}"
        if text not in said:
            said.add(text)
            lines.append(text[:ARGS_ERROR_CHARS])
    more = len(lines) - ARGS_ERRORS_MAX
    return lines[:ARGS_ERRORS_MAX] + ([f"+{more} more"] if more > 0 else [])


def args_failure(name: str, model):
    """The function_tool ``failure_error_function``: the SDK's refusal of arguments its parameters model rejects
    (ModelBehaviorError "Invalid JSON input for tool ...") answered as a facet result (status refused, E_ARGS, the
    problems by path: schema_errors) with the footer; everything else as loop.fatal_tool_errors (WorkerFailed
    re-raised, the SDK's default text otherwise)."""
    other = fatal_tool_errors(WorkerFailed)

    def failure(ctx, error) -> str:
        if isinstance(error, ModelBehaviorError) and str(error).startswith("Invalid JSON input for tool"):
            problems = schema_errors(model, getattr(ctx, "tool_arguments", None))
            if problems:
                return result_text({"status": "refused", "code": "E_ARGS", "tool": name,
                                    "message": f"the arguments do not match {name}'s parameters; nothing was done",
                                    "errors": problems}, footer_of(ctx))
        return other(ctx, error)
    return failure


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
    ImageId, ReadPoint, Zoom = M["ImageId"], M["ReadPoint"], M["Zoom"]

    def tool(name: str):
        def wrap(fn):
            model = function_schema(fn, name_override=name).params_pydantic_model     # the SDK's own, for args_failure
            return function_tool(fn, name_override=name, timeout=sdk_timeout_s(name),
                                 failure_error_function=args_failure(name, model),
                                 timeout_error_function=timeout_text(name))
        return wrap

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

    def judged(judgement):
        return None if judgement is None else judgement.model_dump()

    @tool("facet_inspect")
    async def facet_inspect(ctx: ToolContext):
        """The applied head: its design (nodes [d, y, t] with t typed, "base", "solve" or a stored reading; facets
        with their plane and soft borders; declared creases), the fitted values and the fit report (misfits in mm and
        sd, faces, each crease's angle with its sd), the built creases, the stored readings (one no node is named
        for: against its nearest node), the object note, recent
        revisions and applied checkpoints, the photo ids, the latest compare and any judgement due. Image: the chart
        of the head (facets labelled)."""
        return await call(ctx, "facet_inspect", lambda cid: lab.inspect())

    @tool("photo_image")
    @doc(PHOTO_IMAGE_DOC)
    async def photo_image(ctx: ToolContext, photo: ImageId,
                          crop: Annotated[list[int], Field(min_length=4, max_length=4)] | None, enhance: bool,
                          scale: Annotated[int, Field(ge=1, le=ST.SCALE_MAX)]):
        return await call(ctx, "photo_image", lambda cid: lab.photo(photo, crop, enhance, scale))

    @tool("photo_read")
    @doc(PHOTO_READ_DOC)
    async def photo_read(ctx: ToolContext,
                         points: Annotated[list[ReadPoint], Field(max_length=ST.READ_POINTS_MAX)],
                         drop: Annotated[list[str], Field(max_length=ST.READ_DROP_MAX)]):
        return await call(ctx, "photo_read", lambda cid: lab.read(cid, dumped(points), list(drop)))

    @tool("photo_compare")
    @doc(PHOTO_COMPARE_DOC)
    async def photo_compare(ctx: ToolContext, photos: list[PhotoId] | None, crop: Literal["temple", "full"] | None,
                            zoom: Annotated[list[Zoom], Field(max_length=ST.ZOOM_MAX)] | None,
                            points_px: list[PhotoPoints] | None):
        return await call(ctx, "photo_compare", lambda cid: lab.compare(cid, photos, crop, dumped(zoom),
                                                                         dumped(points_px)))

    @tool("facet_edit")
    @doc(FACET_EDIT_DOC)
    async def facet_edit(ctx: ToolContext, parent_revision: int, reset_layout: bool, edits: list[Edit],
                         judgement: Judgement | None, note: str,
                         object: Annotated[str, Field(max_length=ST.OBJECT_MAX)] | None, apply: bool,
                         photos: list[PhotoId] | None):
        return await call(ctx, "facet_edit", lambda cid: lab.edit(
            cid, parent_revision, reset_layout, [e.model_dump() for e in edits], judged(judgement), note, object,
            apply, photos))

    @tool("facet_variants")
    @doc(FACET_VARIANTS_DOC)
    async def facet_variants(ctx: ToolContext, parent_revision: int,
                             variants: Annotated[list[Variant], Field(min_length=VARIANTS_MIN,
                                                                      max_length=VARIANTS_MAX)],
                             photos: Annotated[list[PhotoId], Field(min_length=1, max_length=vmax)],
                             judgement: Judgement | None):
        return await call(ctx, "facet_variants", lambda cid: lab.variants(
            cid, parent_revision, [v.model_dump() for v in variants], photos, judged(judgement)))

    @tool("facet_finish")
    @doc(FINISH_DOC.format(k=k, different=ST.DIFFERENT_ATTEMPT, last=ST.STUCK_LAST, spread=ST.SPREAD_VARIANTS,
                           spread_mm=G.VARIANT_DISTINCT_MM, judgement=JUDGEMENT_TEXT))
    async def facet_finish(ctx: ToolContext, status: Literal["matched", "stuck"], best_checkpoint: str,
                           summary: str, judgement: Judgement | None):
        return await call(ctx, "facet_finish", lambda cid: lab.finish(
            cid, status, best_checkpoint, summary, judged(judgement), k))

    tools = [facet_inspect, photo_image, photo_read, photo_compare, facet_edit, facet_variants, facet_finish]
    assert tuple(t.name for t in tools) == TOOL_NAMES
    for t in tools:
        t.params_json_schema = without_titles(t.params_json_schema)
    return tools
