"""Host geometry of the facet lab (plan/WP6_V3.md §6 on WP6_FACETS.md §4.1-4.2): the edits union on design specs,
design diffs, geometry deltas between builds, and revision 0 from the original's export. numpy only, no Blender.

A design spec (WP6_V3 §3) is a g3d.temple/2 spec whose node t may also be a reading {"read": {...}} (fit.read_payload
of a stored readings.read_point record); fit.fit_spec turns it into the pure /2 spec the builder takes.
canonical_design / design_sha256 are spec.canonical / spec_sha256 with read dicts kept (a spec without readings is
unchanged: its design sha is its spec sha).

    apply_edits(spec, edits, *, reset_layout=False, readings=None, facts=None) -> (design spec, notes)
        the edits (dicts of the strict tool union, tools.EDIT_MODELS) applied in order to a COPY of a design spec,
        atomically: the first failing edit raises EditError(code, message, index); node / facet references are checked
        after the last edit (a node may be deleted together with the facets that use it). reset_layout clears nodes,
        facets and creases first. Deleting a facet deletes its crease declarations: one note names them, the facet and
        the edit, and which the same edit list declared again ("deleted crease declarations: L|U ridge, RU|U ridge
        (with facet U, edits[0]); declared again in this edit list: L|U ridge; not declared now: RU|U"; the 2026-10-13
        blind stand-in: "crease L|U deleted with facet U" read as a by-the-way). ``facts`` (a dict) receives that note
        as "deleted_creases" and the bend notes below as "outline_bends", for the lab's receipt.
          set_node t {mode, mm}: "reading" (mm null) takes readings[name], the stored reading of the node's name
            (EditError "there is no reading named X: read it with photo_read first"), and sets the node to [d, y,
            read_t(rec)] with d, y = d_mm / y_mm, or the reading's where null; given numbers lie within READ_POSITION_MM
            of where it was read, else EditError naming both (a reading is one point's X at its own (d, y): run 7's
            tip carried 8-20 mm back, FINDINGS §0.2); "mm" (a typed t >= 0), "base", "solve" (mm null; d_mm, y_mm
            given).
          set_node outline (the node's relation to the outline; the strict union's SetNode.outline, absent = null): a
            node lies ON an outline edge (outline_edge, the parse's rule) within snap_mm 0.05 of one of that chain's
            knots or vertically of its line. "top" / "bottom" keep the node on that edge: the chain's knot is put at
            the node's (d, y) (the inner knot within snap_mm of where the node was moves there, else one is inserted;
            an end knot keeps its d and takes y), so the edge passes through the node; "none" makes a free node and
            leaves both chains as they are (a knot the node held stays where it was); null keeps the edge the node lay
            on before this edit (a new node, or one on neither edge, is free). Every knot so placed is a note
            ("outline.top has its knot at node E, d 76.80 y 9.20 (...)") and the diff's chain change, and the other
            nodes on that edge move with it as in a chain edit (a T-junction on the line beside the knot stays on it).
            A knot that lies snap_mm or more off the line the edge ran on bends the edge: a second note gives how far
            (mm, above or below), the knots beside it and the turn there in degrees (_bend_text: the 2026-10-13 blind
            stand-in's E, set "bottom" 0.97 mm below the edge and 0.15 mm behind revision 0's knot at d 76.00, cut a
            111° notch into the bottom outline under a note that said only where the knot was). A set_node of t mode
            reading that leaves its node beyond the outline (a free read corner above the top edge, below the bottom
            one, or outside the d range: the photos put the edge beyond the model's there; the builder clips its
            facets to the outline) is a note too (beyond_text). Both are told of the design after the LAST edit, in
            the place of their set_node: a bend whose node or knot a later edit of the list moved or removed, and a
            read corner a later chain edit put on the outline, have none (the stand-in's rev 7 set E free 0.07 mm
            below the bottom edge, then spliced the edge through E: told at the set_node, the note called E "beyond
            the outline" in a revision where it lies on it). Typed, base and solved corners beyond the outline are
            the layout's own virtual corners (the Prada fixture's A_hi at y 60) and need none. splice_chain /
            offset_chain of an outline chain move every node that lay on that edge before the edit with it, in y to the
            new chain at its d (a note each); a node holding a reading moves at most READ_POSITION_MM from where it was
            read (EditError with the range the edge may move there, else). Why (v3p3 e2e P1-1, verify-0): a corner read
            on the outline stayed on the built outline only while a knot sat at it, and set_node / offset_chain /
            splice_chain changed one without the other: on Prada every corner but K is on the outline (A, H, E top;
            L, C, D bottom), the first layout without a hand splice was refused E_SHORT_EDGE in 215 of 300 jittered
            layouts, and a re-read 0.1-0.3 mm off left the knot behind (6 of 20 refused, 10 of 20 built a false
            "certain" facet|base border). A knot only at the corner does not straighten the outline between two
            corners: where the photos put a facet edge along the outline, the outline between is the chain's own
            shape (splice_chain), and a base strip between them is built and printed as the misfit it is (the lab's
            crease line names it: outline_strips).
          set_facet plane {mode}: "flat" -> the spec's "nodes" (a plane through its corners; the fit gives it coef),
            "warp" -> "warp" (>= WARP_MIN_CORNERS corners); ``soft`` true -> the facet's "soft" key (written only when
            true). Astra's union offers no fade and no crease + rise plane (WP6_V3 §6: fades and solved planes were
            harmful tools, FINDINGS §0.7); this function still takes the v2 plane shapes (plane mode "nodes" /
            "crease" with crease, rise_per_mm) from internal callers (fixtures, v2 tests, old revisions); fades were
            removed from the core on 2026-10-13 (a fade other than null is an EditError).
    spec_to_edits(spec) -> edits
        the edits that rebuild spec's layout, chains and scalars from any parent with reset_layout (the converse): a
        read t is mode "reading" at the node's own (d, y) (apply_edits needs that reading stored again), outline null
        (the chains are spliced whole first; after the reset every node is new, so null leaves them), a warp
        facet "warp", a fitted {"coef"} plane "flat" (the fit gives it again from the node values), soft as it is.
        v3 designs give edits the strict union accepts; a legacy crease plane gives its v2 shape.
    diff_specs(a, b) -> dict, diff_text(diff) -> str
        what changed between two specs (nodes, facets, creases, chains, scalars), compact; a solved t reads "solve", a
        read t "read 7.75 +- 0.30" (its t = X - x_in(d) at the node's d, and its whole sd in X; an added node with a
        reading prints it too: "K t read 7.75 +- 0.30"), a facet's changes "poly", "flat->warp", "soft" / "not soft",
        "coef" (a fitted plane's numbers), an added facet its non-default plane and soft ("crown warp soft"); a chain
        the d spans where its shape changed and by how much ("outline.bottom d 77.0-117.0 (up to 1.30 mm)",
        chain_changes: the two chains compared as lines, never knot by knot).
    stale_reads(spec, readings) -> {name: "replaced" | "dropped"}
        the design's read nodes whose reading is no longer the stored one of their name (other numbers: a re-read with
        the same numbers is not "replaced"): a node keeps the reading it was set from (a snapshot in the design), so a
        re-read changes the model only through set_node mode reading.
    outline_strips(tb) -> {facet: [{edge, side, d, width_mm}]}, strip_text(facet, strips) -> str
        the strips of plain arm (base) a build's facets leave between a straight side whose two corners lie on (or
        within STRIP_CORNER_MM of, or beyond) one outline edge and that edge where it curves away beyond the side, at
        least STRIP_MIN_MM wide (labels_at: the arrangement face halfway across is base); the lab's crease lines and
        variant creases name them ("RL leaves plain arm up to 4.86 mm wide between its straight side E-C and the
        curved bottom outline at d 22.2-55.2").
    surface_grid / surface_at / crease_edges / crease_pieces / crease_delta / geometry_delta / distinct_heads
        between two BUILT temples (g3d.temple builds, never their specs, labels or node names): the outer surface X
        and its gradient on a (d, y) lattice, the max normal distance |dX| / sqrt(1 + |grad X|^2) over the lattice
        points inside both outlines, the largest outline shift (y_top, y_bot, the end d), and the visible creases:
        each head's built crease edges folding >= FLAT_CREASE_DEG (measured over the border band, crease_pieces) as a
        ridge set and a valley set of (d, y) segments, compared by symmetric Hausdorff distance (crease_delta).
        distinct_heads is the one rule of finish preconditions 1 and 5: >= HEAD_DISTINCT_MM of normal surface
        distance or outline shift (the crease comparison is reported, never decisive). Flat facets, coplanar splits,
        slivers, renames, reordered polygons, corners outside the outline and nudges under 0.2 mm are no change by
        construction.
    revision_zero(glb_path) -> {spec, report, side, hinge_z, axis, chart_mesh, footprints, body}
        template_from_mesh on the +X temple body of the exported head, with the decal and hinge footprints by
        geometry (measure.hinge_part_kind, DECAL_NEAR_MM of the body) and the body's chart mesh they are measured on.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

import numpy as np

from glasses3d.g3d.temple import fit as FIT
from glasses3d.g3d.temple import measure as MS
from glasses3d.g3d.temple import template as TT
from glasses3d.g3d.temple.build import OUTER_KINDS
from glasses3d.g3d.temple.spec import DEFAULT_OPTIONS, FLAT_CREASE_DEG, PL, WARP_MIN_CORNERS, TempleError, canonical

CHAINS = ("outline.top", "outline.bottom", "inner", "base.top", "base.bottom")
OUTLINE_EDGES = ("top", "bottom")
OUTLINE_SET = ("top", "bottom", "none", None)   # set_node outline: an edge, "none", or null (as before the edit)
T_MODES = ("reading", "mm", "base", "solve")
PLANE_MODES = {"flat": "nodes", "warp": "warp"}     # set_facet plane.mode -> the spec's plane (WP6_V3 §2, §6)
READ_POSITION_MM = 0.5          # set_node mode reading: a given d_mm / y_mm lies within this of where the reading was
#                                 read (= fit.OFF_POSITION_MM, the fit's own position fact line; a test pins it)
SCALARS = ("roll_mm", "tip_round_mm")
NAME_MAX = 24                   # a node, facet, reading or variant name: at most this many characters (= readings
#                                 NAME_MAX), NAME_RULE in the tool texts
NAME_RE = re.compile(rf"^[A-Za-z][A-Za-z0-9_-]{{0,{NAME_MAX - 1}}}$")
NAME_RULE = f"a letter, then up to {NAME_MAX - 1} letters, digits, _ or -"
EDITS_MAX = 64
CHAIN_POINTS_MAX = 160
D_LIMIT_MM, Y_LIMIT_MM, T_LIMIT_MM = 400.0, 200.0, 40.0     # engineering bounds of a lab spec, not quality limits
RISE_LIMIT, DELTA_LIMIT_MM, SCALAR_LIMIT_MM = 5.0, 20.0, 10.0
GRID_STEP_MM = 0.5
HEAD_DISTINCT_MM = 0.2          # §6 finish preconditions 1 and 5: two heads are distinct when their built temples
                                # differ by >= this normal surface distance or outline shift ...
VARIANT_DISTINCT_MM = 0.3       # ... and precondition 2: variants pairwise >= this (value_mm)
CREASE_MATCH_DEG = 0.5 * FLAT_CREASE_DEG    # a crease piece folding >= FLAT_CREASE_DEG is matched against the other
                                # head's pieces of its shape from this fold on (hysteresis at the visibility threshold)
CREASE_EMPTY_MIN_MM = 2.0       # a crease set against none of its shape differs when it totals this length
CREASE_PIECE_MM = 0.1           # crease_pieces: crease edges are measured in pieces of at most this length
CREASE_SAMPLE_MM = 0.02         # directed_mm: sample spacing along a crease (the value is short by < half of it)
SURFACE_CELL_MM = 1.0           # surface_at: the triangle bucket grid
CREASE_NEAR_MM = 1.0            # directed_mm: candidate segments within this of a chunk of samples
CREASE_CHUNK = 256              # directed_mm: samples per chunk
DIFF_NAMES_MAX = 6              # diff_text: names listed per kind before "+N more" (§4.5 text budget)
STRIP_MIN_MM = 0.1              # outline_strips: a strip of plain arm at least this wide between a facet's straight
#                                 side and the outline is named (about one native px at the photos' 7-9 px/mm)
STRIP_CORNER_MM = READ_POSITION_MM  # ... beside a side whose two corners lie within this of an outline edge or beyond
#                                 it (a corner read on the edge is set within its reading's position tolerance)
STRIP_STEP_MM = 0.25            # ... sampled every this many mm in d along the side (and at the edge's knots)
STRIP_ITEMS_MAX = 2             # strip_text: strips named per facet before "+N more"
TEMPLE_MIN_REACH_MM, TEMPLE_MIN_OFFSET_MM = 60.0, 20.0     # the one body rule (g3d_host/pose.temple_body)


class EditError(ValueError):
    """An edit the lab refuses before any build: code E_EDIT, the 0-based edit index (None for the call)."""

    def __init__(self, message: str, index: int | None = None, code: str = "E_EDIT", **fields):
        super().__init__(message)
        self.code, self.message, self.index, self.fields = code, message, index, fields

    def to_dict(self) -> dict:
        return {"status": "refused", "code": self.code, "edit": self.index, "message": self.message,
                **({"fields": self.fields} if self.fields else {})}


# --------------------------------------------------------------------------------------------- chains
def _chain(spec: dict, name: str) -> list:
    if name == "outline.top":
        return spec["outline"]["top"]
    if name == "outline.bottom":
        return spec["outline"]["bottom"]
    if name == "inner":
        return spec["inner"]
    return spec["base"][name.split(".")[1]]


def _set_chain(spec: dict, name: str, pts: list) -> None:
    if name in ("outline.top", "outline.bottom"):
        spec["outline"][name.split(".")[1]] = pts
    elif name == "inner":
        spec["inner"] = pts
    else:
        spec["base"][name.split(".")[1]] = pts


def _interp(pts: list, d: float) -> float:
    a = np.asarray(pts, float)
    return float(np.interp(d, a[:, 0], a[:, 1]))


def _num(x, what: str, i: int, limit: float | None = None) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)):
        raise EditError(f"{what} must be a finite number", i)
    if limit is not None and abs(float(x)) > limit:
        raise EditError(f"{what} {float(x):g} is outside +-{limit:g}", i)
    return float(x)


def _name(x, what: str, i: int) -> str:
    if not isinstance(x, str) or not NAME_RE.match(x) or x == "base":
        raise EditError(f"{what} {x!r}: names are {NAME_RULE}, and not 'base'", i)
    return x


def _pair(facets, i: int) -> tuple[str, str]:
    if not isinstance(facets, list) or len(facets) != 2:
        raise EditError("facets must name two facets (or a facet and 'base')", i)
    a, b = facets
    if a == "base":
        a, b = b, a
    a = _name(a, "facet", i)
    if b != "base":
        b = _name(b, "facet", i)
        a, b = sorted((a, b))
    if a == b:
        raise EditError("a crease needs two different facets", i)
    return a, b


def _crease_key(c: dict) -> tuple[str, str]:
    a, b = c["facets"]
    if a == "base":
        a, b = b, a
    return (a, b) if b == "base" else tuple(sorted((a, b)))


def _splice(pts: list, d_from: float, d_to: float, points: list, i: int) -> list:
    if not d_from < d_to:
        raise EditError("splice_chain needs d_from_mm < d_to_mm", i)
    new = []
    for k, p in enumerate(points):
        if not isinstance(p, list) or len(p) != 2:
            raise EditError(f"points[{k}] must be [d, value]", i)
        d, v = _num(p[0], f"points[{k}] d", i, D_LIMIT_MM), _num(p[1], f"points[{k}] value", i, Y_LIMIT_MM)
        if not d_from <= d <= d_to:
            raise EditError(f"points[{k}] d {d:g} lies outside d_from..d_to {d_from:g}..{d_to:g}", i)
        new.append([d, v])
    if any(b[0] <= a[0] for a, b in zip(new, new[1:])):
        raise EditError("points must have strictly increasing d", i)
    out = [p for p in pts if p[0] < d_from] + new + [p for p in pts if p[0] > d_to]
    if len(out) < 2:
        raise EditError("the chain needs at least 2 knots after the splice", i)
    return out


def _splice_join_candidates(chain: str, before: list, lo: float, hi: float, points: list,
                            edit_index: int) -> list[dict]:
    """External joins introduced by a splice; the final neighbours are read only after all edits."""
    left = [p for p in before if p[0] < lo]
    right = [p for p in before if p[0] > hi]
    joins = []
    if points:
        if left:
            joins.append((points[0][0], -1))
        if right:
            joins.append((points[-1][0], 1))
    elif left and right:                         # deleting knots creates a bridge between retained ends
        joins = [(left[-1][0], 1), (right[0][0], -1)]
    return [{"chain": chain, "edit_index": edit_index, "d_mm": float(d), "side": side,
             "before": copy.deepcopy(before)} for d, side in joins]


def _splice_join_facts(s: dict, original: dict, candidates: list[dict]) -> list[dict]:
    """Facts, not repairs or gates: changed final joining ramps, including inner/base chains.

    A piecewise-linear chain is continuous, so a short ramp is never called a literal discontinuity. The value
    change, rise/run and slope turn make a step-like join visible without an appearance verdict. The existing
    snap and flat-crease precision suppress insignificant notes. Original-shape restoration suppresses notes even
    when a second splice repaired the first; replaced or deleted boundaries cannot leave stale facts.
    """
    facts = []
    for c in candidates:
        chain, d, side = c["chain"], c["d_mm"], c["side"]
        q = [[float(x), float(y)] for x, y in _chain(s, chain)]
        held = [j for j, p in enumerate(q) if abs(p[0] - d) <= 1e-9]
        if not held:
            continue
        k = held[0]
        j = k + side
        if not 0 <= j < len(q):
            continue
        lo, hi = sorted((q[k][0], q[j][0]))
        # Also include the other neighbour: its slope determines the boundary turn.
        around = q[max(0, k - 1):min(len(q), k + 2)]
        original_chain = _chain(original, chain)
        sample = sorted({p[0] for p in around} | {p[0] for p in original_chain
                        if around[0][0] <= p[0] <= around[-1][0]})
        if all(abs(_interp(q, x) - _interp(original_chain, x)) <= 1e-9 for x in sample):
            continue
        a, b = q[min(k, j)], q[max(k, j)]
        rise, run = b[1] - a[1], hi - lo
        angle = math.degrees(math.atan2(rise, run))
        before = c["before"]
        was = _interp(before, d)
        delta = q[k][1] - was
        old_angle = math.degrees(math.atan2(_interp(before, hi) - _interp(before, lo), run))
        turn = _turn_deg(q, k) if 0 < k < len(q) - 1 else 0.0
        old_k = next((j for j in range(1, len(before) - 1) if abs(before[j][0] - d) <= 1e-9), None)
        old_turn = _turn_deg(before, old_k) if old_k is not None else 0.0
        if abs(delta) < snap_of(s) and abs(angle - old_angle) < FLAT_CREASE_DEG \
                and abs(turn - old_turn) < FLAT_CREASE_DEG:
            continue
        message = (f"splice_chain {chain} join from edits[{c['edit_index']}] at d {d:.2f}: final value "
                   f"{q[k][1]:.2f} mm ({delta:+.2f} mm from its pre-splice line); joining ramp "
                   f"d {lo:.2f}-{hi:.2f} rises {rise:+.2f} mm over {run:.2f} mm "
                   f"(slope {angle:+.1f} deg, before {old_angle:+.1f} deg); "
                   f"turn {turn:.1f} deg (before {old_turn:.1f} deg).")
        facts.append({"chain": chain, "edit_index": c["edit_index"], "d_mm": d,
                      "joining_side": "before" if side < 0 else "after", "value_mm": q[k][1],
                      "pre_splice_value_mm": was, "value_change_mm": delta,
                      "ramp_d_mm": [lo, hi], "ramp_rise_mm": rise, "ramp_run_mm": run,
                      "slope_deg": angle, "pre_splice_slope_deg": old_angle,
                      "turn_deg": turn, "pre_splice_turn_deg": old_turn, "message": message})
    return facts


def _offset(pts: list, d_from: float, d_to: float, delta: float, taper: float, i: int) -> list:
    """Add delta x w(d) to the chain, w = 1 over [d_from, d_to] falling linearly to 0 over taper_mm outside; knots
    are inserted at the breakpoints inside the chain (no geometry change by themselves)."""
    if not d_from <= d_to or taper < 0:
        raise EditError("offset_chain needs d_from_mm <= d_to_mm and taper_mm >= 0", i)
    d0, d1 = pts[0][0], pts[-1][0]
    have = [p[0] for p in pts]
    out = [list(p) for p in pts]
    for d in sorted({d_from - taper, d_from, d_to, d_to + taper}):
        if d0 < d < d1 and min(abs(d - h) for h in have) > 1e-9:
            out.append([d, _interp(pts, d)])
            have.append(d)
    out.sort(key=lambda p: p[0])

    def w(d):
        if d_from <= d <= d_to:
            return 1.0
        if taper > 0 and d_from - taper < d < d_from:
            return (d - (d_from - taper)) / taper
        if taper > 0 and d_to < d < d_to + taper:
            return ((d_to + taper) - d) / taper
        return 0.0
    return [[d, v + delta * w(d)] for d, v in out]


# --------------------------------------------------------------------------------------------- nodes on the outline
def snap_of(spec: dict) -> float:
    """The design's snap_mm (its options, else spec.DEFAULT_OPTIONS), at least fit.ON_MM: the distance within which
    the builder's parse puts a node onto an outline edge (spec._snap_nodes_onto_outline)."""
    opts = spec.get("options") or {}
    return max(float(opts.get("snap_mm", DEFAULT_OPTIONS["snap_mm"])), FIT.ON_MM)


def outline_edge(spec: dict, d: float, y: float) -> str | None:
    """"top" / "bottom": the outline edge the point (d, y) of a design lies on, by the parse's rule
    (spec._snap_nodes_onto_outline): within snap_of(spec) of one of that chain's knots, or within it vertically of the
    chain's line at d (d inside the chain's d range); the nearer edge when both (top on a tie, as the parse); None
    otherwise. The chains as written (the tip's rounding aside)."""
    snap, best = snap_of(spec), None
    for edge in OUTLINE_EDGES:
        P = np.asarray(_chain(spec, f"outline.{edge}"), float)
        dist = float(np.min(np.hypot(P[:, 0] - d, P[:, 1] - y)))
        if P[0, 0] <= d <= P[-1, 0]:
            dist = min(dist, abs(y - float(np.interp(d, P[:, 0], P[:, 1]))))
        if dist <= snap and (best is None or dist < best[0]):
            best = (dist, edge)
    return best[1] if best else None


def _knot_to(pts: list, old, d: float, y: float, snap: float) -> list:
    """A chain with its knot at a node's (d, y) (apply_edits, set_node outline): the inner knot within snap of the
    node's ``old`` (d, y) is removed (the corner moved; the two end knots are never removed and keep their d: the caps
    are shared by both chains), then the end knot within snap of d takes y, else the inner knot nearest d within snap
    moves to (d, y), else a knot is inserted at (d, y). The caller checks that d lies in the chain's d range."""
    P = [[float(p[0]), float(p[1])] for p in pts]
    if old is not None:
        inner = [(math.hypot(P[k][0] - old[0], P[k][1] - old[1]), k) for k in range(1, len(P) - 1)]
        near = [r for r in inner if r[0] <= snap]
        if near:
            del P[min(near)[1]]
    for end in (0, len(P) - 1):
        if abs(P[end][0] - d) <= snap:
            P[end][1] = y
            return P
    near = [(abs(P[k][0] - d), k) for k in range(1, len(P) - 1) if abs(P[k][0] - d) <= snap]
    if near:
        P[min(near)[1]] = [d, y]
    else:
        P.append([d, y])
        P.sort(key=lambda p: p[0])
    return P


def _turn_deg(P: list, k: int) -> float:
    """How far a chain turns at its inner knot k (deg, 0 = straight on): the angle between the segment into the knot
    and the one out of it, in (d, y) mm."""
    a = (P[k][0] - P[k - 1][0], P[k][1] - P[k - 1][1])
    b = (P[k + 1][0] - P[k][0], P[k + 1][1] - P[k][1])
    return abs(math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])))


def _bend_text(s: dict, edge: str, name: str, before: list, d: float, y: float) -> str | None:
    """The bend a set_node outline knot makes in its edge (module doc), told of the FINAL design ``s`` (apply_edits
    calls it after the last edit), or None when the knot lies within snap of the line the edge ran on (``before``: the
    chain just before that set_node), or when the final design no longer holds node ``name`` at (d, y) with the edge's
    knot there (a later edit of the same list moved or removed it: that edit's own note and the diff say so): how far
    the knot lies above or below that line at its d, the knots beside it in the final chain, and how far the edge
    now turns there (with how far it turned there before). Why (2026-10-13 blind stand-in, rev 1): E's knot went in
    0.15 mm behind revision 0's knot at d 76.00 and 0.97 mm below the edge's line, a notch (a 111° turn) in the bottom
    outline the receipt named only as "outline.bottom has its knot at node E"; told at the set_node, a later
    splice_chain over that knot left the note stating a turn the revision does not have."""
    snap = snap_of(s)
    B = np.asarray(before, float)
    was = float(np.interp(d, B[:, 0], B[:, 1]))
    move = y - was
    if abs(move) < snap:
        return None
    node = s["nodes"].get(name)
    if node is None or abs(float(node[0]) - d) > 1e-9 or abs(float(node[1]) - y) > 1e-9:
        return None
    Q = [[float(p[0]), float(p[1])] for p in _chain(s, f"outline.{edge}")]
    held = [j for j in range(len(Q)) if abs(Q[j][0] - d) <= snap and abs(Q[j][1] - y) <= 1e-9]
    if not held:
        return None
    k = min(held, key=lambda j: abs(Q[j][0] - d))
    text = (f"outline.{edge} bends at node {name}: its knot at d {d:.2f} y {y:.2f} lies {abs(move):.2f} mm "
            f"{'above' if move > 0 else 'below'} the line the edge ran on (y {was:.2f} there)")
    if 0 < k < len(Q) - 1:
        old = [j for j in range(1, len(B) - 1) if abs(B[j][0] - d) <= snap]
        then = f"it turned {_turn_deg(B.tolist(), old[0]):.0f}° there" if old else "it ran straight there"
        text += (f"; between its knots at d {Q[k - 1][0]:.2f} and {Q[k + 1][0]:.2f} the edge now turns "
                 f"{_turn_deg(Q, k):.0f}° at the knot ({then})")
    else:
        nb = Q[1] if k == 0 else Q[-2]
        text += f"; it is the edge's end knot, its neighbour at d {nb[0]:.2f}"
    return text


def _outline_node(s: dict, name: str, old, edge_arg, node: list, i: int) -> tuple[str | None, tuple | None]:
    """set_node's ``outline`` (module doc) on the design s, the node already at ``node`` = [d, y, t]: "top" / "bottom"
    put that chain's knot at (d, y) (_knot_to; a knot the node held on the other chain is removed), "none" leaves both
    chains, null keeps the node's edge from before the edit (``old`` = its [d, y, t] then, or None for a new node:
    outline_edge) as "top" / "bottom". Returns (the note when a chain changed, (edge, the chain before, d, y) when the
    knot changed that edge, for _bend_text on the final design), each None when there is none; EditError for a d
    outside the chain's d range or an unknown value."""
    if edge_arg not in OUTLINE_SET:
        raise EditError("outline must be top, bottom, none or null", i)
    snap = snap_of(s)
    was = outline_edge(s, old[0], old[1]) if old is not None else None
    edge = was if edge_arg is None else None if edge_arg == "none" else edge_arg
    if edge is None:
        return None, None
    d, y = float(node[0]), float(node[1])
    chain = f"outline.{edge}"
    P = _chain(s, chain)
    if not P[0][0] - snap <= d <= P[-1][0] + snap:
        raise EditError(f"node {name} at d {d:.2f} lies outside the outline's d range {P[0][0]:.2f}-{P[-1][0]:.2f}, so "
                        f"it cannot lie on the {edge} edge; a free node (outline none) may lie beyond the outline, "
                        f"which clips its facets", i, node=name, at=[d, y])
    before = [list(map(float, p)) for p in P]
    new = _knot_to(P, old[:2] if was == edge else None, d, y, snap)
    changed = new != before
    if changed:
        _set_chain(s, chain, new)
    if was is not None and was != edge:                   # the node left the other edge: its inner knot there goes
        Q = _chain(s, f"outline.{was}")
        drop = [(math.hypot(Q[k][0] - old[0], Q[k][1] - old[1]), k) for k in range(1, len(Q) - 1)]
        drop = [r for r in drop if r[0] <= snap]
        if drop:
            _set_chain(s, f"outline.{was}", [p for k, p in enumerate(Q) if k != min(drop)[1]])
            changed = True
    if not changed:
        return None, None
    why = "as before this edit: it lay on that edge" if edge_arg is None else f"outline {edge}"
    return (f"outline.{edge} has its knot at node {name}, d {d:.2f} y {y:.2f} ({why})",
            (edge, before, d, y) if new != before else None)


def beyond_text(spec: dict, name: str, d: float, y: float) -> str | None:
    """A node that lies beyond the design's outline (a virtual corner: in front of or behind its d range, or more
    than snap_of(spec) above the top edge or below the bottom edge), as a note: "node E at d 76.15 y -0.98 lies 0.97
    mm below the bottom outline: a corner beyond the outline (its facets are clipped to the outline)"; None for a
    node inside or on the outline. The tools take such corners (the builder clips each facet to the outline,
    spec.py "Virtual nodes outside the outline are allowed"); apply_edits notes a read one, of the final design
    (2026-10-13 blind stand-in: A read at d -0.27, "outside the model's outline", with nothing saying what such a
    corner does)."""
    snap = snap_of(spec)
    T, Bt = np.asarray(_chain(spec, "outline.top"), float), np.asarray(_chain(spec, "outline.bottom"), float)
    d0, d1 = max(T[0, 0], Bt[0, 0]), min(T[-1, 0], Bt[-1, 0])
    tail = ": a corner beyond the outline (its facets are clipped to the outline)"
    if d < d0 - snap or d > d1 + snap:
        where = f"{d0 - d:.2f} mm in front of" if d < d0 else f"{d - d1:.2f} mm behind"
        return f"node {name} at d {d:.2f} y {y:.2f} lies {where} the outline's d range {d0:.2f}-{d1:.2f}" + tail
    yt, yb = float(np.interp(d, T[:, 0], T[:, 1])), float(np.interp(d, Bt[:, 0], Bt[:, 1]))
    if y > yt + snap:
        return f"node {name} at d {d:.2f} y {y:.2f} lies {y - yt:.2f} mm above the top outline" + tail
    if y < yb - snap:
        return f"node {name} at d {d:.2f} y {y:.2f} lies {yb - y:.2f} mm below the bottom outline" + tail
    return None


def _carry(s: dict, chain: str, names: list, what: str, i: int, before: list) -> list[str]:
    """An outline chain changed by ``what`` (splice_chain / offset_chain, or the knot a set_node put at its corner:
    module doc; ``before`` = the chain before it): each node of ``names`` (on that edge before the edit, outline_edge)
    moves with the edge in y to the new chain's value at its d. A node holding a reading moves at most
    READ_POSITION_MM from where it was read: else EditError with how far the edge may move there from where it was
    (to 0.001 mm: a node within snap_mm beside the line lands on it, so the edge's room is not the node's; session2's
    C, 0.002 mm under the bottom line, read "0.50 mm ... from -0.50 to +0.50" for a +0.50 offset). Returns the
    notes."""
    P = np.asarray(_chain(s, chain), float)
    B = np.asarray(before, float)
    notes = []
    for n in names:
        d, y, t = s["nodes"][n]
        if not P[0, 0] <= d <= P[-1, 0]:
            continue
        ny = float(np.interp(d, P[:, 0], P[:, 1]))
        if abs(ny - y) <= 1e-9:
            continue
        if is_read(t):
            rd, ry = float(t["read"]["d"]), float(t["read"]["y"])
            off = math.hypot(d - rd, ny - ry)
            if off > READ_POSITION_MM + 1e-6:          # a carried value, not a typed one: float noise at the limit
                reach = math.sqrt(max(READ_POSITION_MM ** 2 - (d - rd) ** 2, 0.0))
                was = float(np.interp(d, B[:, 0], B[:, 1]))
                lo, hi = ry - reach - was, ry + reach - was
                span = (f"the edge may move there from {lo:+.3f} to {hi:+.3f} mm with {n} on it" if lo <= hi and
                        abs(d - rd) <= READ_POSITION_MM else f"{n} lies {abs(d - rd):.3f} mm from it in d alone")
                raise EditError(f"{what} moves node {n}, which lies on that edge, to y {ny:.3f} at d {d:.2f}: "
                                f"{off:.3f} mm from where reading {n} was read (d {rd:.3f}, y {ry:.3f}); a reading is "
                                f"used within {READ_POSITION_MM:g} mm of it, so {span}", i, node=n, at=[d, ny],
                                read_at=[rd, ry], chain=chain)
        s["nodes"][n] = [d, ny, t]
        notes.append(f"node {n} moved with {chain} to y {ny:.2f} at d {d:.2f} ({ny - y:+.2f} mm; it lies on that "
                     f"edge)")
    return notes


# --------------------------------------------------------------------------------------------- design specs
def _json_floats(x, where: str):
    """A reading's payload with every number a finite float (key order left to json's sort_keys)."""
    if isinstance(x, dict):
        return {str(k): _json_floats(v, f"{where}.{k}") for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_floats(v, where) for v in x]
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float, np.integer, np.floating)) and math.isfinite(float(x)):
        return float(x)
    raise TempleError("E_SPEC", f"{where}: {x!r} is not a finite number, text, true/false or null")


def canonical_design(spec: dict) -> dict:
    """The canonical form of a design spec (module doc): spec.canonical with every read t {"read": {...}} kept (its
    numbers as floats). A spec without readings gives exactly canonical(spec). TempleError E_SPEC on a malformed spec
    or a t dict that is not {"read": {...}}."""
    nodes = spec.get("nodes") if isinstance(spec, dict) else None
    reads = {}
    if isinstance(nodes, dict):
        for name, v in nodes.items():
            if isinstance(v, (list, tuple)) and len(v) == 3 and isinstance(v[2], dict):
                t = v[2]
                if set(t) != {"read"} or not isinstance(t["read"], dict):
                    raise TempleError("E_SPEC", f"node {name}: t must be a number, null, \"base\" or {{\"read\": "
                                      "{...}}", node=name)
                reads[name] = {"read": _json_floats(t["read"], f"nodes.{name}.read")}
    if not reads:
        return canonical(spec)
    c = canonical({**spec, "nodes": {n: ([v[0], v[1], 0.0] if n in reads else v) for n, v in nodes.items()}})
    for name, t in reads.items():
        c["nodes"][name][2] = t
    return c


def design_sha256(spec: dict) -> str:
    """sha256 of the canonical design as spec.canonical_json writes a spec (= spec_sha256 without readings)."""
    text = json.dumps(canonical_design(spec), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_t(rec: dict) -> dict:
    """A node's t for a stored reading (readings.read_point record): {"read": fit.read_payload(rec)'s reading} (I2:
    read_payload returns the t value {"read": {...}}; a bare payload is wrapped)."""
    p = FIT.read_payload(rec)
    p = p if isinstance(p, dict) and set(p) == {"read"} else {"read": p}
    return {"read": _json_floats(p["read"], f"reading {rec.get('name')}")}


def is_read(t) -> bool:
    return isinstance(t, dict) and set(t) == {"read"}


def _numbers(t: dict) -> dict:
    """A read t's payload without its record id: the numbers (and photos) a fit uses."""
    return {k: v for k, v in t["read"].items() if k != "id"}


def stale_reads(spec: dict, readings: dict | None) -> dict:
    """The nodes of a design whose read t is not the stored reading of their name: {name: "replaced" (a reading of
    that name was read again since and its numbers differ) | "dropped" (none is stored)}. A node keeps the reading it
    was set from (apply_edits copies it into the design, so the revision's fit and build never change under it); a
    re-read takes effect only through set_node mode reading again (review 2026-10-12: Q re-read 2.4 sd away left the
    head's Q, its fit and its report unchanged, with nothing saying so). A re-read with the same numbers (the same
    picks again, e.g. after an E_TIMEOUT) is not "replaced": only its record id differs, and the text invited a
    set_node that made a second revision with the head's fitted spec (v3p3 e2e P3-1: "node K holds the earlier
    reading K" at "X +0.00 mm (+0.0 sd of the difference)"). The lab lists these where a reading is replaced
    (photo_read) and in the head's node rows (facet_inspect)."""
    out = {}
    for name, v in (canonical_design(spec)["nodes"] or {}).items():
        if not is_read(v[2]):
            continue
        rec = (readings or {}).get(name)
        if rec is None:
            out[name] = "dropped"
        elif _numbers(read_t(rec)) != _numbers(v[2]):
            out[name] = "replaced"
    return out


# --------------------------------------------------------------------------------------------- edits
def _set_node(e: dict, i: int, readings: dict | None) -> tuple[str, list]:
    """One set_node edit as (name, [d, y, t]) (module doc)."""
    name = _name(e.get("name"), "node", i)
    t = e.get("t") or {}
    mode, mm = (t.get("mode"), t.get("mm")) if isinstance(t, dict) else (None, None)
    dv, yv = e.get("d_mm"), e.get("y_mm")
    if mode == "reading":
        if mm is not None:
            raise EditError("t.mode 'reading' takes t.mm null (the reading gives the node's X)", i)
        rec = (readings or {}).get(name)
        if rec is None:
            raise EditError(f"there is no reading named {name}: read it with photo_read first", i)
        rd, ry = float(rec["d"]), float(rec["y"])
        d = rd if dv is None else _num(dv, "d_mm", i, D_LIMIT_MM)
        y = ry if yv is None else _num(yv, "y_mm", i, Y_LIMIT_MM)
        off = math.hypot(d - rd, y - ry)
        if off > READ_POSITION_MM:
            raise EditError(f"node {name} at d {d:.2f}, y {y:.2f} lies {off:.2f} mm from where reading {name} was "
                            f"read, d {rd:.2f}, y {ry:.2f}; a reading is used within {READ_POSITION_MM:g} mm of it",
                            i, node=name, at=[d, y], read_at=[rd, ry])
        return name, [d, y, read_t(rec)]
    if mode not in T_MODES:
        raise EditError("t.mode must be reading, mm, base or solve", i)
    if dv is None or yv is None:
        raise EditError(f"t.mode '{mode}' needs d_mm and y_mm (null takes the reading's d, y in mode reading only)", i)
    d = _num(dv, "d_mm", i, D_LIMIT_MM)
    y = _num(yv, "y_mm", i, Y_LIMIT_MM)
    if mode == "mm":
        if mm is None:
            raise EditError("t.mode 'mm' needs t.mm (thickness from the inner face)", i)
        tv = _num(mm, "t.mm", i, T_LIMIT_MM)
        if tv < 0:
            raise EditError("t.mm is a thickness from the inner face and cannot be negative", i)
        return name, [d, y, tv]
    if mm is not None:
        raise EditError(f"t.mode '{mode}' takes t.mm null", i)
    return name, [d, y, "base" if mode == "base" else None]


def _set_facet(e: dict, i: int) -> tuple[str, dict]:
    """One set_facet edit as (label, the spec's facet) (module doc: v3 plane modes flat / warp and soft; the v2
    shapes from internal callers)."""
    label = _name(e.get("label"), "facet", i)
    poly = e.get("poly")
    if not isinstance(poly, list) or not 3 <= len(poly) <= 32 or len(set(poly)) != len(poly):
        raise EditError("poly must list 3..32 distinct node names", i)
    for n in poly:
        _name(n, "node", i)
    plane = e.get("plane") or {}
    mode = plane.get("mode") if isinstance(plane, dict) else None
    legacy = {k: plane.get(k) for k in ("crease", "rise_per_mm")} if isinstance(plane, dict) else {}
    if mode in PLANE_MODES or mode == "nodes":
        if any(v is not None for v in legacy.values()):
            raise EditError(f"plane.mode '{mode}' takes no crease and no rise_per_mm", i)
        pl = PLANE_MODES.get(mode, "nodes")
        if pl == "warp" and len(poly) < WARP_MIN_CORNERS:
            raise EditError(f"a warp facet needs >= {WARP_MIN_CORNERS} corners (three always lie on one plane); "
                            f"poly has {len(poly)}", i)
    elif mode == "crease":                                  # v2 (internal callers only: module doc)
        cr = legacy["crease"]
        if not isinstance(cr, list) or len(cr) != 2 or cr[0] == cr[1] or any(n not in poly for n in cr):
            raise EditError("plane.crease must name two different corners of poly", i)
        if legacy["rise_per_mm"] is None:
            raise EditError("plane.mode 'crease' needs rise_per_mm", i)
        pl = {"crease": [cr[0], cr[1]], "rise_per_mm": _num(legacy["rise_per_mm"], "rise_per_mm", i, RISE_LIMIT)}
    else:
        raise EditError("plane.mode must be flat or warp", i)
    if e.get("fade") is not None:                           # fades were removed (module doc)
        raise EditError("fade must be null", i)
    soft = e.get("soft", False)
    if not isinstance(soft, bool):
        raise EditError("soft must be true or false", i)
    out = {"poly": list(poly), "plane": pl, "fade": None}
    if soft:
        out["soft"] = True
    return label, out


def _deleted_crease_text(gone: list, creases: list) -> str:
    """The note of the crease declarations delete_facet removed (module doc): ``gone`` = [(crease, facet label, edit
    index)], ``creases`` = the design's declarations after the last edit (which ones the same edit list declared
    again)."""
    now = {_crease_key(c): c["shape"] for c in creases}
    by = {}
    for c, label, i in gone:
        by.setdefault((label, i), []).append(c)
    parts = [", ".join(f"{'|'.join(c['facets'])} {c['shape']}" for c in cs) + f" (with facet {label}, edits[{i}])"
             for (label, i), cs in by.items()]
    again = [f"{'|'.join(k)} {now[k]}" for k in dict.fromkeys(_crease_key(c) for c, _l, _i in gone) if k in now]
    lost = ["|".join(k) for k in dict.fromkeys(_crease_key(c) for c, _l, _i in gone) if k not in now]
    return ("deleted crease declarations: " + "; ".join(parts)
            + (f"; declared again in this edit list: {', '.join(again)}" if again else "")
            + (f"; not declared now: {', '.join(lost)}" if lost else ""))


def apply_edits(spec: dict, edits: list, *, reset_layout: bool = False, readings: dict | None = None,
                facts: dict | None = None) -> tuple[dict, list[str]]:
    """The edits applied to a copy of the design ``spec`` (module doc); ``readings`` = the stored readings by name
    (state["readings"]) for set_node mode "reading". Returns (canonical design, notes); raises EditError. ``facts``
    (a dict, optional) receives the notes the lab's receipt states on lines of their own: "outline_bends" (the bends
    set_node outline knots make, _bend_text) and "deleted_creases" (_deleted_crease_text), each a list of notes;
    "splice_joins" contains structured final joining-ramp facts when a splice occurred."""
    if not isinstance(edits, list) or len(edits) > EDITS_MAX:
        raise EditError(f"edits must be a list of at most {EDITS_MAX} operations")
    s = copy.deepcopy(canonical_design(spec))
    original = copy.deepcopy(s)
    if reset_layout:
        s["nodes"], s["facets"], s["creases"] = {}, {}, []
    notes, deleted_nodes, set_facets, set_creases, gone_creases = [], {}, {}, {}, []
    told = []                           # [(notes index, "bend" | "beyond", data)]: told of the final design below
    splice_joins, had_splice = [], False
    for i, e in enumerate(edits):
        op = e.get("op") if isinstance(e, dict) else None
        if op == "set_node":
            name, node = _set_node(e, i, readings)
            old = s["nodes"].get(name)
            riding = {edge: [n for n, v in s["nodes"].items() if n != name and outline_edge(s, v[0], v[1]) == edge]
                      for edge in OUTLINE_EDGES}
            chains = {edge: [list(p) for p in _chain(s, f"outline.{edge}")] for edge in OUTLINE_EDGES}
            s["nodes"][name] = node
            note, bend = _outline_node(s, name, old, e.get("outline"), node, i)
            if note:                    # the knot moved: the other nodes on that edge stay on it (module doc)
                notes.append(note)
                if bend:
                    told.append((len(notes), "bend", (bend[0], name) + bend[1:]))
                    notes.append(None)
                for edge in OUTLINE_EDGES:
                    notes += _carry(s, f"outline.{edge}", riding[edge], f"the knot set_node {name} put on "
                                    f"outline.{edge}", i, chains[edge])
            if is_read(node[2]):        # a read corner beyond the model's outline: the photos put the edge there
                told = [x for x in told if x[1:] != ("beyond", name)]
                told.append((len(notes), "beyond", name))
                notes.append(None)
            deleted_nodes.pop(name, None)
        elif op == "delete_node":
            name = e.get("name")
            if name not in s["nodes"]:
                raise EditError(f"there is no node {name!r}", i)
            del s["nodes"][name]
            deleted_nodes[name] = i
        elif op == "set_facet":
            label, facet = _set_facet(e, i)
            s["facets"][label] = facet
            set_facets[label] = i
        elif op == "delete_facet":
            label = e.get("label")
            if label not in s["facets"]:
                raise EditError(f"there is no facet {label!r}", i)
            del s["facets"][label]
            gone_creases += [(c, label, i) for c in s["creases"] if label in c["facets"]]
            s["creases"] = [c for c in s["creases"] if label not in c["facets"]]
        elif op == "set_crease":
            pair = _pair(e.get("facets"), i)
            if e.get("shape") not in ("ridge", "valley"):
                raise EditError("shape must be ridge or valley", i)
            s["creases"] = [c for c in s["creases"] if _crease_key(c) != pair]
            s["creases"].append({"facets": list(pair), "shape": e["shape"]})
            set_creases[pair] = i
        elif op == "delete_crease":
            pair = _pair(e.get("facets"), i)
            if not any(_crease_key(c) == pair for c in s["creases"]):
                raise EditError(f"there is no declared crease {'|'.join(pair)}", i)
            s["creases"] = [c for c in s["creases"] if _crease_key(c) != pair]
            set_creases.pop(pair, None)
        elif op in ("splice_chain", "offset_chain"):
            chain = e.get("chain")
            if chain not in CHAINS:
                raise EditError(f"chain must be one of {list(CHAINS)}", i)
            d_from = _num(e.get("d_from_mm"), "d_from_mm", i, D_LIMIT_MM)
            d_to = _num(e.get("d_to_mm"), "d_to_mm", i, D_LIMIT_MM)
            pts = [list(p) for p in _chain(s, chain)]
            pts_before = [list(p) for p in pts]
            edge = chain.split(".")[1] if chain.startswith("outline.") else None
            riding = [n for n, v in s["nodes"].items() if edge and outline_edge(s, v[0], v[1]) == edge]
            if op == "splice_chain":
                pts = e.get("points")
                if not isinstance(pts, list) or len(pts) > CHAIN_POINTS_MAX:
                    raise EditError(f"points must be a list of at most {CHAIN_POINTS_MAX} [d, value]", i)
                _set_chain(s, chain, _splice([list(p) for p in _chain(s, chain)], d_from, d_to, pts, i))
                had_splice = True
                splice_joins = [c for c in splice_joins if not
                                (c["chain"] == chain and d_from <= c["d_mm"] <= d_to)]
                new_joins = _splice_join_candidates(chain, pts_before, d_from, d_to, pts, i)
                keys = {(c["chain"], c["d_mm"], c["side"]) for c in new_joins}
                splice_joins = [c for c in splice_joins if (c["chain"], c["d_mm"], c["side"]) not in keys]
                splice_joins += new_joins
            else:
                delta = _num(e.get("delta_mm"), "delta_mm", i, DELTA_LIMIT_MM)
                taper = _num(e.get("taper_mm"), "taper_mm", i, D_LIMIT_MM)
                _set_chain(s, chain, _offset(pts, d_from, d_to, delta, taper, i))
            notes += _carry(s, chain, riding, f"{op} {chain}", i, [list(p) for p in pts_before])
        elif op == "set_scalar":
            key = e.get("key")
            value = _num(e.get("value"), "value", i, SCALAR_LIMIT_MM)
            if key not in SCALARS or value < 0:
                raise EditError(f"set_scalar key must be one of {list(SCALARS)} with value >= 0", i)
            if key == "roll_mm":
                s["roll_mm"] = value
            else:
                s["outline"]["tip_round_mm"] = value
        else:
            raise EditError(f"unknown op {op!r}", i)
    for label, f in s["facets"].items():                   # references, after the last edit
        for n in f["poly"]:
            if n not in s["nodes"]:
                at = deleted_nodes.get(n, set_facets.get(label))
                why = f"node {n} is deleted but facet {label} still uses it" if n in deleted_nodes else \
                    f"facet {label} uses node {n}, which does not exist"
                raise EditError(why, at)
    for c in s["creases"]:
        for f in c["facets"]:
            if f != "base" and f not in s["facets"]:
                raise EditError(f"crease {'|'.join(c['facets'])} names facet {f}, which does not exist",
                                set_creases.get(_crease_key(c)))
    bends = []
    for k, kind, data in told:          # the bends and the read corners beyond the outline, as the revision has them
        if kind == "bend":
            notes[k] = _bend_text(s, *data)
            bends += [notes[k]] if notes[k] else []
        else:
            node = s["nodes"].get(data)
            notes[k] = beyond_text(s, data, float(node[0]), float(node[1])) \
                if node is not None and is_read(node[2]) else None
    notes = [n for n in notes if n is not None]
    deleted = [_deleted_crease_text(gone_creases, s["creases"])] if gone_creases else []
    notes += deleted
    joins = _splice_join_facts(s, original, splice_joins)
    notes += [r["message"] for r in joins]
    if facts is not None:
        facts["outline_bends"], facts["deleted_creases"] = bends, deleted
        facts.pop("splice_joins", None)
        if had_splice:
            facts["splice_joins"] = joins
    return canonical_design(s), notes


def spec_to_edits(spec: dict) -> list[dict]:
    """Edits that rebuild ``spec``'s layout (with reset_layout), chains and scalars from any parent (module doc)."""
    s = canonical_design(spec)
    out = []
    for chain in CHAINS:
        pts = _chain(s, chain)
        out.append({"op": "splice_chain", "chain": chain, "d_from_mm": -D_LIMIT_MM, "d_to_mm": D_LIMIT_MM,
                    "points": [list(p) for p in pts]})
    out.append({"op": "set_scalar", "key": "roll_mm", "value": s["roll_mm"]})
    out.append({"op": "set_scalar", "key": "tip_round_mm", "value": s["outline"]["tip_round_mm"]})
    for name, (d, y, t) in s["nodes"].items():
        if is_read(t):
            tv = {"mode": "reading", "mm": None}
        elif isinstance(t, float):
            tv = {"mode": "mm", "mm": t}
        else:
            tv = {"mode": "base" if t == "base" else "solve", "mm": None}
        out.append({"op": "set_node", "name": name, "d_mm": d, "y_mm": y, "t": tv, "outline": None})
    for label, f in s["facets"].items():
        pl = f["plane"]
        if isinstance(pl, dict) and "crease" in pl:                  # v2 shape (module doc)
            plane = {"mode": "crease", "crease": list(pl["crease"]), "rise_per_mm": pl["rise_per_mm"]}
        else:
            plane = {"mode": "warp" if pl == "warp" else "flat"}
        out.append({"op": "set_facet", "label": label, "poly": list(f["poly"]), "plane": plane,
                    "soft": bool(f.get("soft"))})
    for c in s["creases"]:
        out.append({"op": "set_crease", "facets": list(c["facets"]), "shape": c["shape"]})
    return out


# --------------------------------------------------------------------------------------------- diffs
def _r(x, nd=2):
    return None if x is None else round(float(x), nd) + 0.0


def read_sd(t: dict) -> float:
    """A read t's whole sd in X: its pick sd and each camera fit's theta term (fit.read_payload "camera": dX per sd)
    in quadrature, = the record's sd_X (readings.py: the two parts in quadrature)."""
    r = t["read"]
    cam = sum(float(v[2]) ** 2 for v in (r.get("camera") or {}).values())
    return math.sqrt(float(r["sd"]) ** 2 + cam)


def _t_text(t, inner: PL, d: float):
    """A node's t as a diff prints it: a number (2 decimals), "solve", "base", or "read 7.75 +- 0.30" (the reading's
    t = X - x_in(d) at the node's d, and its whole sd in X, read_sd)."""
    if is_read(t):
        return f"read {float(t['read']['X']) - float(inner(d)):.2f} +- {read_sd(t):.2f}"
    return _r(t) if isinstance(t, float) else "solve" if t is None else t


def _plane_kind(f: dict) -> str:
    pl = f["plane"]
    return "flat" if pl == "nodes" else "warp" if pl == "warp" else "coef" if "coef" in pl else "crease"


def _facet_changes(fa: dict, fb: dict) -> list[str]:
    """What changed in one facet (module doc); [] for a polygon listed from another start or the other way round."""
    out = []
    ra, rb = fa["poly"], fb["poly"]
    if not (len(ra) == len(rb) and any(rb == ra[k:] + ra[:k] or rb[::-1] == ra[k:] + ra[:k] for k in range(len(ra)))):
        out.append("poly")
    ka, kb = _plane_kind(fa), _plane_kind(fb)
    if ka != kb:
        out.append(f"{ka}->{kb}")
    elif fa["plane"] != fb["plane"]:
        out.append(kb)
    if bool(fa.get("soft")) != bool(fb.get("soft")):
        out.append("soft" if fb.get("soft") else "not soft")
    return out


def chain_changes(pa: list, pb: list) -> tuple[list, float]:
    """Where two versions of one chain ([[d, value], ...], strictly increasing d; each piecewise linear in d between
    its knots) differ: ([(d_lo, d_hi)] the merged d spans, the largest |value change| in mm). On the union U of both
    knot sets the two are linear between consecutive knots, so they differ exactly on the open intervals beside a
    union knot where they differ (or that one chain's d range covers and the other's does not): a knot u_k that
    differs makes [u_(k-1), u_(k+1)] changed (clipped to U's ends). A knot added on the old line (offset_chain's
    breakpoints) changes nothing. Session2 (v3-integrate-2): pairing the knots by index printed "d 0.0-136.0" for a
    splice of d 0-77 (every knot after a change of knot count read as changed) and "122.0-134.0" for a change over
    d 122-136 (a shifted pair hid the end knot)."""
    A, B = np.asarray(pa, float).reshape(-1, 2), np.asarray(pb, float).reshape(-1, 2)
    U = np.unique(np.concatenate([A[:, 0], B[:, 0]]))
    ina = (U >= A[0, 0]) & (U <= A[-1, 0])
    inb = (U >= B[0, 0]) & (U <= B[-1, 0])
    fa, fb = np.interp(U, A[:, 0], A[:, 1]), np.interp(U, B[:, 0], B[:, 1])
    both = ina & inb
    dv = np.where(both, np.abs(fa - fb), 0.0)
    diff = (ina != inb) | (dv > 1e-9)
    spans: list = []
    for k in np.nonzero(diff)[0]:
        lo, hi = float(U[max(k - 1, 0)]), float(U[min(k + 1, len(U) - 1)])
        if spans and lo <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    return spans, float(dv.max()) if len(dv) else 0.0


def chain_change_text(pa: list, pb: list) -> str:
    """A chain's change for diff_text (chain_changes): "d 77.0-117.0 (up to 1.30 mm)", several spans comma-separated;
    a move under 0.005 mm is "under 0.01 mm", never "0.00"; a changed d range adds "its d range 0.0-136.0 ->
    0.0-130.0"; "knots only (no shape change)" when only knots on the old line were added or removed."""
    spans, mm = chain_changes(pa, pb)
    if not spans:
        return "knots only (no shape change)"
    parts = [f"up to {mm:.2f} mm" if mm >= 0.005 else "under 0.01 mm"] if mm > 1e-9 else []
    ra, rb = (_r(pa[0][0], 1), _r(pa[-1][0], 1)), (_r(pb[0][0], 1), _r(pb[-1][0], 1))
    if ra != rb:                                    # as printed: 0.01 -> 0.0 is no range change to 0.1 mm
        parts.append(f"its d range {ra[0]}-{ra[1]} -> {rb[0]}-{rb[1]}")
    return "d " + ", ".join(f"{_r(lo, 1)}-{_r(hi, 1)}" for lo, hi in spans) + (f" ({', '.join(parts)})" if parts
                                                                                else "")


def diff_specs(a: dict, b: dict) -> dict:
    """What changed from design a to design b (both canonical_design-able)."""
    a, b = canonical_design(a), canonical_design(b)
    out: dict = {}
    na, nb = a["nodes"], b["nodes"]
    ia, ib = PL(a["inner"], "inner"), PL(b["inner"], "inner")
    nodes = {"added": [n + (f" t {_t_text(nb[n][2], ib, nb[n][0])}" if is_read(nb[n][2]) else "")
                       for n in sorted(set(nb) - set(na))],
             "removed": sorted(set(na) - set(nb)), "changed": []}
    for n in sorted(set(na) & set(nb)):
        (d0, y0, t0), (d1, y1, t1) = na[n], nb[n]
        parts = []
        if math.hypot(d1 - d0, y1 - y0) > 1e-9:
            parts.append(f"at {_r(d1)},{_r(y1)}")
        if t0 != t1:
            ta, tb = _t_text(t0, ia, d0), _t_text(t1, ib, d1)
            same_text = ta == tb and is_read(t0) and is_read(t1)       # another reading that prints the same
            parts.append(f"t {tb} re-read" if same_text else f"t {ta}->{tb}")
        if parts:
            nodes["changed"].append(f"{n} " + " ".join(parts))
    fa, fb = a["facets"], b["facets"]
    added = []
    for k in sorted(set(fb) - set(fa)):
        props = [p for p in (_plane_kind(fb[k]),) if p != "flat"] + (["soft"] if fb[k].get("soft") else [])
        added.append(" ".join([k] + props))
    changed = []
    for k in sorted(set(fa) & set(fb)):
        what = _facet_changes(fa[k], fb[k]) if fa[k] != fb[k] else []
        if what:
            changed.append(f"{k} " + " ".join(what))
    facets = {"added": added, "removed": sorted(set(fa) - set(fb)), "changed": changed}
    ca = {_crease_key(c): c["shape"] for c in a["creases"]}
    cb = {_crease_key(c): c["shape"] for c in b["creases"]}
    creases = {"set": [f"{'|'.join(k)} {v}" for k, v in sorted(cb.items()) if ca.get(k) != v],
               "removed": ["|".join(k) for k in sorted(set(ca) - set(cb))]}
    chains = []
    for chain in CHAINS:
        pa, pb = _chain(a, chain), _chain(b, chain)
        if pa != pb:
            chains.append(f"{chain} {chain_change_text(pa, pb)}")
    scalars = [f"{k} {_r(x)}->{_r(y)}" for k, x, y in (("roll_mm", a["roll_mm"], b["roll_mm"]),
                                                         ("tip_round_mm", a["outline"]["tip_round_mm"],
                                                          b["outline"]["tip_round_mm"])) if x != y]
    for key, val in (("nodes", nodes), ("facets", facets), ("creases", creases)):
        if any(val.values()):
            out[key] = {k: v for k, v in val.items() if v}
    if chains:
        out["chains"] = chains
    if scalars:
        out["scalars"] = scalars
    return out


def diff_text(diff: dict, limit: int = 360, names: int = DIFF_NAMES_MAX, reset: bool = False) -> str:
    """One compact line of a diff_specs result; a list of more than ``names`` items shows its first names - 1 and
    "+N more". With ``reset`` (reset_layout cleared the parent's layout) the removed items are counted, not named."""
    parts = []
    for key in ("nodes", "facets", "creases"):
        for kind, items in diff.get(key, {}).items():
            sign = {"added": "+", "removed": "-", "changed": "~", "set": "+"}[kind]
            if reset and kind == "removed":
                parts.append(f"-{len(items)} {key if len(items) != 1 else key[:-1]}")
                continue
            shown = items if len(items) <= names else items[:names - 1] + [f"+{len(items) - names + 1} more"]
            parts.append(f"{sign}{key[:-1] if key != 'creases' else 'crease'} " + ", ".join(shown))
    parts += diff.get("chains", []) + diff.get("scalars", [])
    text = "; ".join(parts) or "no change"
    return text if len(text) <= limit else text[:limit - 3] + "..."


# --------------------------------------------------------------------------------------------- facets at the outline
def labels_at(tb, pts) -> list:
    """The label of the build's arrangement face (a facet label or "base") that holds each (d, y) point of ``pts``
    (even-odd rule on each face's polygon), None for a point outside every face."""
    V = np.asarray(tb.arr.V, float)
    P = np.asarray(pts, float).reshape(-1, 2)
    out: list = [None] * len(P)
    todo = np.ones(len(P), bool)
    for fi, f in enumerate(tb.arr.faces):
        poly = V[list(f)]
        lo, hi = poly.min(0), poly.max(0)
        cand = np.flatnonzero(todo & (P[:, 0] >= lo[0]) & (P[:, 0] <= hi[0]) & (P[:, 1] >= lo[1])
                              & (P[:, 1] <= hi[1]))
        if not len(cand):
            continue
        x, y = P[cand, 0], P[cand, 1]
        inside = np.zeros(len(cand), bool)
        xj, yj = poly[-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            for xi, yi in poly:
                inside ^= ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
                xj, yj = xi, yi
        for c in cand[inside]:
            out[c] = tb.face_label[fi]
            todo[c] = False
    return out


def outline_strips(tb) -> dict:
    """The strips of plain arm a facet leaves along the outline: {facet: [{"edge": "top" | "bottom", "side": [a, b],
    "d": [d0, d1], "width_mm"}]} of a build (its parsed spec's node positions, outline and arrangement). A facet side
    a-b runs along an outline edge when both its corners lie within STRIP_CORNER_MM of that edge or beyond it; the
    builder clips the facet to the outline, so where the edge curves away beyond the straight side the arm between
    them is base. Sampled every STRIP_STEP_MM in d (and at the edge's knots) over the side's span inside the outline's
    d range: the vertical gap from the side to the edge, a strip where it is at least STRIP_MIN_MM and the point
    halfway across is base (another facet there is no strip). Each contiguous run gives its d range and largest
    width. Why (2026-10-13 blind stand-in, rev 1): RL's corners C and E sat on the bottom outline, which ran up to 4.9
    mm below the chord C-E; the receipt said "RL|base valley 8.7° (sunk 1.62 mm: a 39.1° ramp at d 21.9-56.4)" and
    nothing named the strip of plain arm the B1 render showed under RL. A build without its parsed spec or
    arrangement (a table-only stand-in) has none."""
    ps = getattr(tb, "spec", None)
    out: dict = {}
    if getattr(tb, "arr", None) is None or not all(hasattr(ps, k) for k in ("nodes", "facets", "top", "bottom", "d0",
                                                                            "d1", "y_top", "y_bot")):
        return out
    for label, f in ps.facets.items():
        poly = list(f["poly"])
        for k, a in enumerate(poly):
            b = poly[(k + 1) % len(poly)]
            (da, ya), (db, yb) = (tuple(map(float, ps.nodes[n][:2])) for n in (a, b))
            if abs(db - da) < STRIP_STEP_MM:                  # a side across the arm, not along an edge
                continue
            for edge, sign, chain in (("top", 1.0, ps.top), ("bottom", -1.0, ps.bottom)):
                at = ps.y_top if edge == "top" else ps.y_bot
                if any(sign * (yy - at(dd)) < -STRIP_CORNER_MM for dd, yy in ((da, ya), (db, yb))):
                    continue
                lo, hi = max(min(da, db), float(ps.d0)), min(max(da, db), float(ps.d1))
                if hi - lo < STRIP_STEP_MM:
                    continue
                knots = np.asarray(chain, float)[:, 0]
                ds = np.unique(np.r_[np.arange(lo, hi, STRIP_STEP_MM), hi, knots[(knots > lo) & (knots < hi)]])
                ys = ya + (yb - ya) * (ds - da) / (db - da)
                gap = sign * (np.asarray(at(ds), float) - ys)        # > 0: the edge lies beyond the side
                wide = np.flatnonzero(gap >= STRIP_MIN_MM)
                if not len(wide):
                    continue
                base = np.zeros(len(ds), bool)
                lab = labels_at(tb, np.c_[ds[wide], ys[wide] + sign * gap[wide] / 2.0])
                base[wide] = [x == "base" for x in lab]
                run = None
                for j in list(range(len(ds))) + [len(ds)]:
                    if j < len(ds) and base[j]:
                        run = j if run is None else run
                        continue
                    if run is not None:
                        out.setdefault(label, []).append({"edge": edge, "side": [a, b],
                                                          "d": [round(float(ds[run]), 2), round(float(ds[j - 1]), 2)],
                                                          "width_mm": round(float(gap[run:j].max()), 3)})
                        run = None
    return out


def strip_text(facet: str, strips: list, limit: int = STRIP_ITEMS_MAX) -> str:
    """outline_strips of one facet in the receipt's words: "RL leaves plain arm up to 1.62 mm wide between its
    straight side C-E and the curved bottom outline at d 21.9-56.4" (several: "; and ..."; beyond ``limit``: "+N
    more"); "" for none."""
    items = [f"up to {s['width_mm']:.2f} mm wide between its straight side {s['side'][0]}-{s['side'][1]} and the "
             f"curved {s['edge']} outline at d {s['d'][0]:.1f}-{s['d'][1]:.1f}" for s in strips[:limit]]
    if not items:
        return ""
    more = f" (+{len(strips) - limit} more)" if len(strips) > limit else ""
    return f"{facet} leaves plain arm " + "; and ".join(items) + more


# --------------------------------------------------------------------------------------------- deltas
def _tri_gradients(V: np.ndarray, T: np.ndarray) -> np.ndarray:
    """(k, 2) gradient (dX/dd, dX/dy) of each triangle's plane (outer triangles are a graph over (d, y))."""
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    with np.errstate(divide="ignore", invalid="ignore"):
        return -n[:, :2] / n[:, 2:3]


def surface_grid(tb, step: float = GRID_STEP_MM, origin: tuple = (0.0, 0.0)) -> tuple[np.ndarray, np.ndarray,
                                                                                         np.ndarray, np.ndarray]:
    """(d values, y values, X[i_d, i_y], grad[i_d, i_y] = (dX/dd, dX/dy)) of the outer surface (outer and roll
    polygons, which tile the outline in (d, y)) on the lattice origin + k x step, each point from the triangle that
    holds it; NaN outside the outline."""
    V = np.asarray(tb.V, float)
    kind = np.array([k in OUTER_KINDS for k in tb.poly_kind])[np.asarray(tb.tri_poly)]
    T = np.asarray(tb.tris)[kind]
    P = V[:, :2]
    lo, hi = P[np.unique(T)].min(0), P[np.unique(T)].max(0)
    i0 = np.ceil((lo - origin) / step).astype(int)
    i1 = np.floor((hi - origin) / step).astype(int)
    ds = origin[0] + step * np.arange(i0[0], i1[0] + 1)
    ys = origin[1] + step * np.arange(i0[1], i1[1] + 1)
    X = np.full((len(ds), len(ys)), np.nan)
    Gr = np.full((len(ds), len(ys), 2), np.nan)
    grads = _tri_gradients(V, T)
    A, B, Cc = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    tmin, tmax = np.minimum(np.minimum(A, B), Cc), np.maximum(np.maximum(A, B), Cc)
    ja = np.ceil((tmin - origin) / step - 1e-9).astype(int) - i0
    jb = np.floor((tmax - origin) / step + 1e-9).astype(int) - i0
    for t in range(len(T)):
        if jb[t, 0] < ja[t, 0] or jb[t, 1] < ja[t, 1]:
            continue
        gd = ds[max(ja[t, 0], 0):jb[t, 0] + 1]
        gy = ys[max(ja[t, 1], 0):jb[t, 1] + 1]
        if not len(gd) or not len(gy):
            continue
        Dg, Yg = np.meshgrid(gd, gy, indexing="ij")
        a, b, c = A[t], B[t], Cc[t]
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-14:
            continue
        l1 = ((b[1] - c[1]) * (Dg - c[0]) + (c[0] - b[0]) * (Yg - c[1])) / den
        l2 = ((c[1] - a[1]) * (Dg - c[0]) + (a[0] - c[0]) * (Yg - c[1])) / den
        l3 = 1.0 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if inside.any():
            x = l1 * V[T[t, 0], 2] + l2 * V[T[t, 1], 2] + l3 * V[T[t, 2], 2]
            box = (slice(max(ja[t, 0], 0), jb[t, 0] + 1), slice(max(ja[t, 1], 0), jb[t, 1] + 1))
            X[box][inside] = x[inside]
            Gr[box][inside] = grads[t]
    return ds, ys, X, Gr


def _regions(tb) -> np.ndarray:
    """Per polygon, an id of its region (the facet label or "base" before any "/chamfer<i>"), within one build."""
    names: dict = {}
    return np.array([names.setdefault(r.partition("/")[0], len(names)) for r in tb.poly_region], np.int64)


def crease_edges(tb) -> dict:
    """Every BUILT crease edge of the outer surface: an edge between two outer / roll polygons of different regions
    (two facets, or a facet and the base; the chamfer and roll sub-bands of one region are not creases), as
    {"seg": (m, 2, 2) (d, y) ends a -> b, "left", "right": (m,) the region ids (_regions) on either side of a -> b}
    (outer polygons run counter-clockwise in (d, y)). Topology only: how much an edge folds is crease_pieces'."""
    region = _regions(tb)
    outer = [p for p, k in enumerate(tb.poly_kind) if k in OUTER_KINDS]
    polys = [list(tb.polys[p]) for p in outer]
    A = np.fromiter((v for q in polys for v in q), np.int64)
    B = np.fromiter((v for q in polys for v in q[1:] + q[:1]), np.int64)
    P = np.repeat(np.asarray(outer, np.int64), [len(q) for q in polys])
    n = len(tb.V)
    key, rev = A * n + B, B * n + A
    order = np.argsort(key, kind="stable")
    ks = key[order]
    pos = np.minimum(np.searchsorted(ks, rev), len(ks) - 1)
    hit = (ks[pos] == rev) & (A < B)
    A, B, P1, P2 = A[hit], B[hit], P[hit], P[order[pos[hit]]]
    cr = region[P1] != region[P2]
    V = np.asarray(tb.V, float)
    return {"seg": np.stack([V[A[cr], :2], V[B[cr], :2]], axis=1), "left": region[P1[cr]], "right": region[P2[cr]]}


def surface_at(tb, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(X, region id) of the built outer surface (outer and roll polygons) at the (d, y) points P, by barycentric
    lookup in the build's own triangulation (triangles bucketed on a SURFACE_CELL_MM grid); NaN and -1 outside the
    outline. A point on an edge takes either triangle (X is continuous)."""
    V = np.asarray(tb.V, float)
    sel = np.array([k in OUTER_KINDS for k in tb.poly_kind])[np.asarray(tb.tri_poly)]
    T = np.asarray(tb.tris)[sel]
    reg = _regions(tb)[np.asarray(tb.tri_poly)[sel]]
    P = np.asarray(P, float).reshape(-1, 2)
    out, rid = np.full(len(P), np.nan), np.full(len(P), -1, np.int64)
    if not len(T) or not len(P):
        return out, rid
    C = V[T][:, :, :2]
    lo = C.reshape(-1, 2).min(0)
    c0 = np.floor((C.min(axis=1) - lo) / SURFACE_CELL_MM).astype(np.int64)
    c1 = np.floor((C.max(axis=1) - lo) / SURFACE_CELL_MM).astype(np.int64)
    ny = c1[:, 1] - c0[:, 1] + 1
    cnt = (c1[:, 0] - c0[:, 0] + 1) * ny
    tri = np.repeat(np.arange(len(T)), cnt)
    k = np.arange(len(tri)) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    W = int(c1[:, 1].max()) + 2
    cid = (c0[tri, 0] + k // ny[tri]) * W + c0[tri, 1] + k % ny[tri]
    order = np.argsort(cid, kind="stable")
    cid, tri = cid[order], tri[order]
    fin = np.isfinite(P).all(axis=1)
    q = np.floor((np.where(fin[:, None], P, lo) - lo) / SURFACE_CELL_MM).astype(np.int64)
    ok = fin & (q >= 0).all(axis=1) & (q[:, 1] < W)
    qid = np.where(ok, q[:, 0] * W + q[:, 1], -1)
    s0, s1 = np.searchsorted(cid, qid, "left"), np.searchsorted(cid, qid, "right")
    m = np.where(ok, s1 - s0, 0)
    qi = np.repeat(np.arange(len(P)), m)
    ti = tri[np.repeat(s0, m) + np.arange(len(qi)) - np.repeat(np.cumsum(m) - m, m)]
    a, b, c = C[ti, 0], C[ti, 1], C[ti, 2]
    p = P[qi]
    den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        l1 = ((b[:, 1] - c[:, 1]) * (p[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[:, 1] - c[:, 1])) / den
        l2 = ((c[:, 1] - a[:, 1]) * (p[:, 0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[:, 1] - c[:, 1])) / den
    l3 = 1.0 - l1 - l2
    inside = (np.abs(den) > 1e-14) & (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
    X = V[T[ti], 2]
    out[qi[inside]] = (l1 * X[:, 0] + l2 * X[:, 1] + l3 * X[:, 2])[inside]
    rid[qi[inside]] = reg[ti[inside]]
    return out, rid


def crease_pieces(tb) -> dict:
    """The built crease edges (crease_edges) cut into pieces of <= CREASE_PIECE_MM, each with its signed fold in
    degrees (+ ridge, - valley; NaN where it cannot be measured inside the outline). At the piece's midpoint p, across
    the edge (unit m toward its left region), each side's slope is the built surface's secant over the largest of
    s, s / 2, s / 4, s / 8 (s = the spec's border_band_mm) whose end still lies in THAT side's region (else s / 8):
    fold = -(atan((X(p + s+ m) - X(p)) / s+) + atan((X(p - s- m) - X(p)) / s-)). So a facet proud by h meets the base
    with atan(h / s) however the build triangulates its ramp, two facets fold by their dihedral, and a flat (coplanar)
    split folds 0 even beside another crease: a secant never crosses into a third region."""
    ce = crease_edges(tb)
    seg = ce["seg"]
    L = np.hypot(*(seg[:, 1] - seg[:, 0]).T)
    keep = L > 1e-9
    seg, L, left, right = seg[keep], L[keep], ce["left"][keep], ce["right"][keep]
    k = np.maximum(1, np.ceil(L / CREASE_PIECE_MM)).astype(np.int64)
    idx = np.repeat(np.arange(len(seg)), k)
    j = np.arange(len(idx)) - np.repeat(np.cumsum(k) - k, k)
    e = seg[idx, 1] - seg[idx, 0]
    pieces = np.stack([seg[idx, 0] + (j / k[idx])[:, None] * e, seg[idx, 0] + ((j + 1) / k[idx])[:, None] * e], axis=1)
    mid = pieces.mean(axis=1)
    m = np.stack([-e[:, 1], e[:, 0]], axis=1) / L[idx][:, None]
    s = float(tb.spec.options["border_band_mm"])
    scales = (s, 0.5 * s, 0.25 * s, 0.125 * s)
    pts = [mid] + [mid + side * sc * m for side in (1.0, -1.0) for sc in scales]
    X, R = surface_at(tb, np.concatenate(pts))
    X, R = X.reshape(len(pts), len(mid)), R.reshape(len(pts), len(mid))
    slopes = []
    for side, region in ((0, left[idx]), (1, right[idx])):
        sl = np.full(len(mid), np.nan)
        for i, sc in enumerate(scales):
            row = 1 + len(scales) * side + i
            v = (X[row] - X[0]) / sc
            last = i == len(scales) - 1
            sl = np.where(np.isnan(sl) & ((R[row] == region) | last), v, sl)
        slopes.append(sl)
    fold = -np.degrees(np.arctan(slopes[0]) + np.arctan(slopes[1]))
    return {"seg": pieces, "fold": fold, "edge": idx}


def _runs(pieces: dict, mask: np.ndarray) -> np.ndarray:
    """(r, 2, 2) the pieces in mask merged into maximal runs of consecutive pieces of one edge (collinear)."""
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return np.zeros((0, 2, 2))
    start = np.ones(len(idx), bool)
    start[1:] = (np.diff(idx) != 1) | (np.diff(pieces["edge"][idx]) != 0)
    first = np.nonzero(start)[0]
    last = np.r_[first[1:], len(idx)] - 1
    return np.stack([pieces["seg"][idx[first], 0], pieces["seg"][idx[last], 1]], axis=1)


def _length(S: np.ndarray) -> float:
    return float(np.hypot(*(S[:, 1] - S[:, 0]).T).sum()) if len(S) else 0.0


def _samples(S: np.ndarray, step: float) -> np.ndarray:
    """Points every <= step mm along each segment of S, both ends included."""
    L = np.hypot(*(S[:, 1] - S[:, 0]).T)
    k = np.maximum(1, np.ceil(L / step)).astype(np.int64)
    idx = np.repeat(np.arange(len(S)), k + 1)
    start = np.repeat(np.cumsum(k + 1) - (k + 1), k + 1)
    t = (np.arange(len(idx)) - start) / k[idx]
    return S[idx, 0] + t[:, None] * (S[idx, 1] - S[idx, 0])


def _point_segment_mm(P: np.ndarray, S: np.ndarray) -> np.ndarray:
    """(len(P), len(S)) distances from the points P to the segments S."""
    a = S[None, :, 0]
    e = S[None, :, 1] - a
    w = P[:, None, :] - a
    t = np.clip((w * e).sum(-1) / np.maximum((e * e).sum(-1), 1e-30), 0.0, 1.0)
    return np.hypot(w[..., 0] - t * e[..., 0], w[..., 1] - t * e[..., 1])


def directed_mm(S: np.ndarray, T: np.ndarray, step: float = CREASE_SAMPLE_MM) -> float:
    """The directed Hausdorff distance from the segments S to the segments T (non-empty), (d, y) mm: the largest
    distance from a point of S (sampled every <= step mm, so short of the exact value by < step / 2) to T. A chunk of
    nearby samples is measured against the segments of T within CREASE_NEAR_MM of its box; a sample farther than that
    from all of them is measured against all of T (exact either way)."""
    mid = S.mean(axis=1)
    P = _samples(S[np.lexsort((mid[:, 1], mid[:, 0]))], step)       # spatially coherent chunks
    lo_t, hi_t = T.min(axis=1), T.max(axis=1)
    out, far = 0.0, []
    for s in range(0, len(P), CREASE_CHUNK):
        Q = P[s:s + CREASE_CHUNK]
        lo, hi = Q.min(0) - CREASE_NEAR_MM, Q.max(0) + CREASE_NEAR_MM
        near = np.nonzero(((lo_t <= hi) & (hi_t >= lo)).all(axis=1))[0]
        d = _point_segment_mm(Q, T[near]).min(axis=1) if len(near) else np.full(len(Q), np.inf)
        ok = d <= CREASE_NEAR_MM
        if ok.any():
            out = max(out, float(d[ok].max()))
        if not ok.all():
            far.append(Q[~ok])
    for F in far:
        for s in range(0, len(F), CREASE_CHUNK):
            out = max(out, float(_point_segment_mm(F[s:s + CREASE_CHUNK], T).min(axis=1).max()))
    return out


def crease_delta(a, b) -> dict:
    """The visible-crease part of §4.1 between two builds: per shape (ridge, valley), each head's set is its built
    crease pieces of that shape folding >= FLAT_CREASE_DEG (crease_pieces); the two sets' symmetric Hausdorff distance
    in (d, y), each set measured against the OTHER head's pieces of that shape from CREASE_MATCH_DEG on (hysteresis: a
    crease at 1.9 deg in one head and 2.1 deg in the other is the same crease). A set against no pieces of its shape
    at all differs (mm None) when it totals >= CREASE_EMPTY_MIN_MM, else counts nothing. Returns {shape: {"mm",
    "length_mm": [a, b]}}: flat facets, coplanar splits, slivers and labels never enter a set; a ridge <-> valley flip
    moves a whole crease from one set to the other."""
    pieces = (crease_pieces(a), crease_pieces(b))
    out = {}
    for shape in ("ridge", "valley"):
        sets = []
        for p in pieces:
            f = np.nan_to_num(p["fold"], nan=0.0) * (1.0 if shape == "ridge" else -1.0)
            sets.append((_runs(p, f >= FLAT_CREASE_DEG), _runs(p, f >= CREASE_MATCH_DEG)))
        (sa, la), (sb, lb) = sets
        dirs = []
        for s, other in ((sa, lb), (sb, la)):
            if not len(s):
                dirs.append(0.0)
            elif not len(other):
                dirs.append(None if _length(s) >= CREASE_EMPTY_MIN_MM else 0.0)
            else:
                dirs.append(directed_mm(s, other))
        out[shape] = {"mm": None if None in dirs else round(max(dirs), 4),
                      "length_mm": [round(_length(sa), 2), round(_length(sb), 2)]}
    return out


def geometry_delta(a, b, step: float = GRID_STEP_MM) -> dict:
    """Between two builds, by their built geometry only (module doc):
        max_dx_mm    max |dX| of the outer surface (roll and chamfers included) on the common lattice (step mm) inside
                     both outlines, each build sampled by barycentric lookup in its own triangulation (surface_grid)
        surface_mm   the max NORMAL distance there: |dX| / sqrt(1 + |g|^2), g the mean of the two surfaces' gradients
                     at the point (a nearly vertical roll band does not magnify an outline move)
        outline_mm   the largest outline shift (y_top / y_bot at every lattice d of the common range, the end d0 / d1)
        value_mm     max(surface_mm, outline_mm) (also finish precondition 2's variant spread)
        creases      crease_delta; crease_mm the largest Hausdorff distance of the two shapes, crease_new the shapes
                     whose crease set appeared or disappeared (mm None)."""
    da, ya, Xa, Ga = surface_grid(a, step)
    db, yb, Xb, Gb = surface_grid(b, step)
    dd = np.intersect1d(np.round(da / step).astype(int), np.round(db / step).astype(int))
    yy = np.intersect1d(np.round(ya / step).astype(int), np.round(yb / step).astype(int))
    max_dx = surface = 0.0
    if len(dd) and len(yy):
        ia = np.searchsorted(np.round(da / step).astype(int), dd)
        ib = np.searchsorted(np.round(db / step).astype(int), dd)
        ja = np.searchsorted(np.round(ya / step).astype(int), yy)
        jb = np.searchsorted(np.round(yb / step).astype(int), yy)
        diff = np.abs(Xa[np.ix_(ia, ja)] - Xb[np.ix_(ib, jb)])
        g = 0.5 * (Ga[np.ix_(ia, ja)] + Gb[np.ix_(ib, jb)])
        normal = diff / np.sqrt(1.0 + (g * g).sum(-1))
        if np.isfinite(diff).any():
            max_dx = float(np.nanmax(diff))
            surface = float(np.nanmax(normal))
    pa, pb = a.spec, b.spec
    lo, hi = max(pa.d0, pb.d0), min(pa.d1, pb.d1)
    outline = max(abs(pa.d0 - pb.d0), abs(pa.d1 - pb.d1))
    if hi > lo:
        ds = np.linspace(lo, hi, max(2, int((hi - lo) / step) + 1))
        outline = max(outline, float(np.abs(pa.y_top(ds) - pb.y_top(ds)).max()),
                      float(np.abs(pa.y_bot(ds) - pb.y_bot(ds)).max()))
    creases = crease_delta(a, b)
    return {"max_dx_mm": round(max_dx, 4), "surface_mm": round(surface, 4), "outline_mm": round(outline, 4),
            "value_mm": round(max(surface, outline), 4), "creases": creases,
            "crease_mm": max([c["mm"] for c in creases.values() if c["mm"] is not None], default=0.0),
            "crease_new": [k for k, c in creases.items() if c["mm"] is None]}


def distinct_heads(delta: dict) -> bool:
    """THE head-distinctness rule (§6 finish preconditions 1 and 5), on the built temples only: the outer surface
    moves >= HEAD_DISTINCT_MM along its normal somewhere, or the outline by that much (value_mm). Crease geometry is
    reported (crease_delta) but never decides: every threshold on crease visibility had a knife edge that 0.05 mm
    thickness nudges could cross (pf/gf/vc reviews, 2026-10-11), while a real layout change or ridge <-> valley flip
    moves the surface itself. A rename, a reordered polygon, a corner outside the outline, a flat (flush or coplanar)
    facet or split, and any nudge that moves the surface less than HEAD_DISTINCT_MM never count."""
    return bool(delta["value_mm"] >= HEAD_DISTINCT_MM)


def spread_triple(labels: list, pairs: dict) -> list | None:
    """Three labels pairwise >= VARIANT_DISTINCT_MM apart (pairs = {(a, b): value_mm}), or None."""
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                trio = (labels[i], labels[j], labels[k])
                if all(pairs.get(tuple(sorted(p)), 0.0) >= VARIANT_DISTINCT_MM
                       for p in ((trio[0], trio[1]), (trio[0], trio[2]), (trio[1], trio[2]))):
                    return list(trio)
    return None


# --------------------------------------------------------------------------------------------- revision 0
def glb_parts(glb_path) -> list[dict]:
    """Every triangle primitive of a GLB: {name, role, V (mm), F}."""
    from glasses3d.g3d_host import glb as GLB
    g = GLB.load(glb_path)
    out = []
    for p in g.primitives:
        if p.mode != 4 or not len(p.triangles):
            continue
        role = (g.doc["nodes"][p.node_index].get("extras") or {}).get("partRole")
        out.append({"name": p.node_name, "role": role, "V": np.asarray(p.positions, float) * 1000.0,
                    "F": np.asarray(p.triangles, np.int64)})
    return out


def temple_body(parts: list, side: int = 1) -> dict:
    """The temple body of one side by geometry: role temple, reaching >= 60 mm back, centred >= 20 mm from x = 0
    on that side; the longest one."""
    cands = []
    for p in parts:
        V = p["V"]
        cx = 0.5 * float(V[:, 0].min() + V[:, 0].max())
        reach = float(np.ptp(V[:, 2]))
        if p["role"] == "temple" and reach >= TEMPLE_MIN_REACH_MM and side * cx >= TEMPLE_MIN_OFFSET_MM:
            cands.append((float(np.ptp(V[:, 2])), p))
    if not cands:
        raise ValueError(f"no temple body on side {side:+d} (role temple, >= {TEMPLE_MIN_REACH_MM:g} mm long)")
    return max(cands, key=lambda c: c[0])[1]


def footprints(parts: list, body: dict, side: int, hinge_z: float, axis, L, Fc) -> dict:
    """Hinge parts (measure.hinge_part_kind) and decals (every vertex within DECAL_NEAR_MM of the body) as (d, y)
    point sets for the chart."""
    hinge, decals = [], []
    for p in parts:
        if p is body or side * float(p["V"][:, 0].mean()) <= 0:
            continue
        lo, hi = p["V"].min(0), p["V"].max(0)
        kind = MS.hinge_part_kind(lo, hi, p["role"], side, axis[0] if axis else None, hinge_z, len(p["V"]))
        Lp = MS.chart_from_world(p["V"], side, hinge_z)
        if kind is not None:
            hinge.append({"name": kind, "points": Lp[:, :2].tolist(), "part": p["name"]})
            continue
        if p["role"] != "temple" or len(p["V"]) > MS.HINGE_MAX_VERTICES:
            continue
        dist = TT.point_mesh_distance(Lp, L, Fc)
        if float(dist.max()) <= MS.DECAL_NEAR_MM:
            decals.append({"name": p["name"].split("|")[-1].strip(), "points": Lp[:, :2].tolist(), "part": p["name"]})
    return {"decals": decals, "hinge": hinge}


def revision_zero(glb_path, side: int = 1) -> dict:
    """Revision 0 of the facet lab: template_from_mesh on the exported head's temple body (module doc)."""
    parts = glb_parts(glb_path)
    body = temple_body(parts, side)
    V, F = body["V"], body["F"]
    hinge_z = float(V[:, 2].max())
    axis = MS.knuckle_axis([(p["V"].min(0), p["V"].max(0), p["role"]) for p in parts], side, hinge_z)
    spec, report = TT.template_from_mesh(V, F, side, hinge_z, axis)
    L, Fc = MS.chart_mesh(V, F, side, hinge_z)
    return {"spec": spec, "report": report, "side": side, "hinge_z": hinge_z, "axis": axis, "body": body["name"],
            "chart_mesh": (L, Fc), "footprints": footprints(parts, body, side, hinge_z, axis, L, Fc)}
