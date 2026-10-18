"""The seating check over a set of mesh nodes (WP4_DESIGN.md §8 'Geometric (binding)'). numpy only.

One implementation for both checks, so they cannot disagree: the host reads a GLB into nodes
(g3d_host/seat_check.py, open3d backend) and Blender reads its render-visible objects into the same nodes
(g3d/seat_bpy.py, BVH backend). A node is a dict:

    {"node": int, "name": str, "V": (n, 3) asset mm, "T": (m, 3) triangles, "N": (n, 3) unit normals or nan,
     "roles": [partRole...], "lens": bool, "translucent": bool, "extras": {...}}

Components (§3 'Lens body', §8):
- lens nodes: a closed component >= 0.3 mm thick is a body; any other component is a rider when it lies on a body
  (`rider_of`: Prada's etch), else the file FAILS (E_OPEN / E_THIN: a lens that is not closed is never silently
  dropped while the other lens passes); a lens node that yields nothing usable fails (E_EMPTY);
- frame nodes: every closed component is a frame solid (a 0.6 mm nylor cord has 2V/A 0.29 mm, a 0.55 mm wire 0.27:
  the 0.3 mm body threshold is for lenses), except a MARKING: closed, thinner than MARKING_MAX_MM and lying on a lens
  (Oakley's OAKLEY / PRIZM decals, 2V/A 0.002-0.003); open frame components are riders when they lie on a lens
  (INVU's print); one within 6 mm of a rim is listed as an open part AND fails the file (E_OPEN_PART): it cannot be
  measured as a solid, and silently dropping it would turn the stations it holds EXPOSED, which semi_rimless grades
  as visible by design (a swept channel with uncapped ends would pass a lens it fails when closed);
- skipped primitives (compressed, undecodable, not triangles; host only): a lens primitive fails the file
  (E_UNREADABLE, or E_EMPTY when its node has no other lens geometry); an undecoded frame surface within 6 mm of a
  rim, or of unknown position, fails it too (E_UNREADABLE_PART). The lens such a frame problem names is not graded.

Frame solids are ordered by (node name, component name), so the backend's part indices, and with them every
tie-break between parts, are the same whatever order the nodes arrive in (GLB node order vs Blender's).

Frame type: declared (`g3d_seat`, else the argument) or inferred: > 20% EXPOSED -> semi_rimless; ENCLOSED majority ->
acetate; else metal; NO ENCLOSED/LIPPED station -> unknown (not graded, FAIL). An inferred type grades the lens
provisionally (`graded_pass`) but never passes it: `pass` and the file's `ok` need a declared type.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Callable

import numpy as np

from . import geom, report, rules, stations
from .intervals import line_intervals

RIM_BOX_PAD_MM = 6.0          # §3 / §8: frame parts within 6 mm of the rim
RIDER_SURFACE_MM = 0.5        # a rider's vertices lie within this of the lens surface (markings float 0.013-0.21)
RIDER_SHARE = 0.9
RIDER_SAMPLES = 64
EXPOSED_INFER_SHARE = 0.20    # §8 frame-type inference
MARKING_MAX_MM = 0.05         # a closed frame-node component thinner than this (2V/A) that lies on a lens is a
                              # marking. Measured: markings 0.002-0.028 (OAKLEY 0.003, PRIZM 0.002, Prada etch 0.022,
                              # temple ribbons 0.028); thinnest real frame solids 0.12 (Oakley pad grips) and 0.19
                              # (icon seats); a 0.6 mm cord 0.29
TRIANGLE_MODES = (4, 5, 6)    # glTF TRIANGLES, TRIANGLE_STRIP, TRIANGLE_FAN: the primitive is meant to be a surface

Factory = Callable[[list], object]       # [(V, T), ...] -> a LineIntervals backend


# ---------------------------------------------------------------------------------------------- rim split, riders
def rim_split(body: stations.LensBody, node: dict) -> dict | None:
    """Angle between the cap-side and wall-side corner normals at the outer rim loops, from the node's normals (0 deg
    = smooth rim; a split rim shows its dihedral)."""
    N = node["N"]
    if not np.isfinite(N).all():
        return None
    _, vid = geom.weld_exact(node["V"])
    rawT = np.asarray(node["T"], np.int64)[body.source_faces]
    out = {}
    for key, cap, loop in (("front", 0, body.fl), ("back", 1, body.bl)):
        gl = body.source_verts[loop]
        pos = np.full(int(vid.max()) + 1, -1, np.int64)
        pos[gl] = np.arange(len(gl))
        cs = np.zeros((len(gl), 3))
        ws = np.zeros((len(gl), 3))
        for c, acc in ((cap, cs), (2, ws)):
            corners = rawT[body.cls == c].reshape(-1)
            k = pos[vid[corners]]
            m = k >= 0
            np.add.at(acc, k[m], N[corners[m]])
        good = (np.linalg.norm(cs, axis=1) > 0) & (np.linalg.norm(ws, axis=1) > 0)
        if not good.any():
            continue
        a = cs[good] / np.linalg.norm(cs[good], axis=1, keepdims=True)
        b = ws[good] / np.linalg.norm(ws[good], axis=1, keepdims=True)
        ang = np.degrees(np.arccos(np.clip((a * b).sum(1), -1.0, 1.0)))
        out[key] = {"p5": report.r(np.percentile(ang, 5), 2), "p50": report.r(np.median(ang), 2),
                    "p95": report.r(np.percentile(ang, 95), 2)}
    return out or None


def rider_of(comp: stations.Component, bodies: list, factory: Factory, cache: dict | None = None) -> int:
    """Index of the body the component lies on (>= 90% of sampled vertices within 0.5 mm of its surface along the
    local cap normal), or -1. `cache` keeps each body's cap faces and backend between calls."""
    if not bodies:
        return -1
    cache = {} if cache is None else cache
    step = max(1, len(comp.V) // RIDER_SAMPLES)
    X = comp.V[::step][:RIDER_SAMPLES]
    for bi, body in enumerate(bodies):
        lo, hi = body.V.min(0) - 1.0, body.V.max(0) + 1.0
        if (X < lo).any(1).mean() > 1 - RIDER_SHARE or (X > hi).any(1).mean() > 1 - RIDER_SHARE:
            continue
        if bi not in cache:
            fn, fa, fc = geom.face_geometry(body.V, body.T)
            caps = np.flatnonzero(body.cls != 2)
            cache[bi] = (fn[caps], fc[caps], factory([(body.V, body.T)]))
        fn, fc, be = cache[bi]
        d2 = ((X[:, None, :] - fc[None]) ** 2).sum(-1)
        res = line_intervals(be, X, fn[d2.argmin(1)])
        near = 0
        for iv in res.intervals:
            if iv[0] is not None and len(iv[0]):
                if np.abs(iv[0]).min() <= RIDER_SURFACE_MM or ((iv[0][:, 0] <= 0) & (iv[0][:, 1] >= 0)).any():
                    near += 1
        if near >= RIDER_SHARE * len(X):
            return bi
    return -1


def boxes_touch(box, lo, hi) -> bool:
    return bool((box[0] <= hi).all() and (box[1] >= lo).all())


# ------------------------------------------------------------------------------------------------------- extras
def seat_extras(node: dict) -> dict:
    ex = node.get("extras") or {}
    seat = ex.get("g3d_seat")
    if isinstance(seat, str):
        try:
            seat = json.loads(seat)
        except ValueError:
            seat = None
    return seat if isinstance(seat, dict) else {}


def ranges_from(node: dict):
    v = (node.get("extras") or {}).get("g3d_hidden_x_ranges_mm")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    try:
        return [(float(a), float(b)) for a, b in v] if v else None
    except (TypeError, ValueError):
        return None


def infer_frame_type(cls: np.ndarray) -> str | None:
    """§8 inference, or None when no station is ENCLOSED or LIPPED: nothing holds the lens, so neither a type nor a
    verdict can be read off it (a lens with no frame, or pushed out of its rim, is not 'semi_rimless')."""
    c = np.asarray(cls).astype(str)
    if not np.isin(c, ("ENCLOSED", "LIPPED")).any():
        return None
    if np.mean(c == "EXPOSED") > EXPOSED_INFER_SHARE:
        return "semi_rimless"
    if np.mean(c == "ENCLOSED") > 0.5:
        return "acetate"
    return "metal"


def _entry(n: dict, c: stations.Component) -> dict:
    return {"node": n["name"], "component": c.name, "vertices": int(len(c.V)), "closed": c.closed,
            "boundary_edges": int(c.topo.get("boundary_edges", 0)),
            "thickness_eff_mm": report.r(c.thickness_eff_mm), "role": (n["roles"] or [None])[0]}


def rim_box(body: stations.LensBody, pad: float = RIM_BOX_PAD_MM) -> tuple[np.ndarray, np.ndarray]:
    """The body's rim box (both outer rim loops) grown by `pad`: §3 / §8 'within 6 mm of the rim'."""
    rim = body.V[np.r_[body.fl, body.bl]]
    return rim.min(0) - pad, rim.max(0) + pad


# ------------------------------------------------------------------------------------------------------ collect
def collect(nodes: list, skipped: list, warnings: list, factory: Factory) -> dict:
    """The check's inputs from nodes (module docstring 'Components'): lens bodies (with their node), frame solids
    (with their node, ordered by name), riders, open parts, refusals, lens problems (each fails the file) and the
    warnings list (extended in place).

    Lens problems: E_OPEN / E_THIN (a lens component that is neither a body nor a marking on one), E_EMPTY (a lens
    node with nothing usable), E_UNREADABLE (a lens primitive that was skipped: compressed, undecodable or not
    triangles, while its node has other lens geometry), E_OPEN_PART (an open frame component within 6 mm of a rim:
    it cannot be measured as a solid, and dropping it would turn the stations it holds EXPOSED, which a
    semi_rimless frame grades as visible by design), E_UNREADABLE_PART (a frame primitive meant as a surface that
    was not decoded, within 6 mm of a rim or of unknown position, for the same reason)."""
    any_roles = any(n["roles"] for n in nodes) or any(s["role"] for s in skipped)
    bodies, body_node, refused, solids = [], [], [], []
    lens_loose, frame_open, frame_thin, empty = [], [], [], []
    for n in nodes:
        if not n["lens"]:
            continue
        rec = stations.recognise(n["V"], n["T"], n["name"])
        for b in rec.bodies:
            bodies.append(b)
            body_node.append(n)
        refused += [dict(x, node=n["name"]) for x in rec.refused]
        lens_loose += [(n, c) for c in rec.riders]
        if not rec.bodies and not rec.riders and not rec.refused:
            empty.append(n)
    for n in nodes:
        if n["lens"] or (any_roles and "frame" not in n["roles"]):
            continue
        for c in stations.split_components(n["V"], n["T"], n["name"]):
            if not c.closed:
                frame_open.append((n, c))
            elif c.thickness_eff_mm < MARKING_MAX_MM:
                frame_thin.append((n, c))
            else:
                solids.append((n, c))
    riders, open_parts, problems = [], [], []
    cache: dict = {}
    for n, c in lens_loose:
        bi = rider_of(c, bodies, factory, cache)
        if bi >= 0:
            riders.append(dict(_entry(n, c), on=bodies[bi].name))
            continue
        if not c.closed:
            what = (f"E_OPEN: lens component {c.name} is not closed ({c.topo['boundary_edges']} boundary, "
                    f"{c.topo['nonmanifold_edges']} non-manifold edges)")
        else:
            what = (f"E_THIN: lens component {c.name} is {c.thickness_eff_mm:.3f} mm thick (2V/A), below the "
                    f"{stations.BODY_MIN_THICKNESS_MM} mm of a lens body")
        problems.append({"node": n["name"], "component": c.name, "code": what.split(":")[0],
                         "message": what + ", and it lies on no lens body (not a marking)"})
    for n in empty:
        problems.append({"node": n["name"], "component": n["name"], "code": "E_EMPTY",
                         "message": "E_EMPTY: the lens node has no non-degenerate triangles"})
    lens_nodes_read = {n["node"] for n in nodes if n["lens"]}
    boxes = [rim_box(b) for b in bodies]

    def near(box) -> list:
        """Bodies whose rim box + 6 mm the box touches; every body when the box is unknown."""
        if box is None:
            return [b.name for b in bodies]
        return [b.name for b, (lo, hi) in zip(bodies, boxes) if boxes_touch(box, lo, hi)]
    for s in skipped:
        if s["lens"]:
            code = "E_UNREADABLE" if s["node_index"] in lens_nodes_read else "E_EMPTY"
            problems.append({"node": s["node"], "component": f"{s['node']} primitive {s['primitive']}", "code": code,
                             "message": f"{code}: lens primitive {s['primitive']} was not read ({s['why']}); the "
                                        f"check cannot grade a lens it cannot read"
                                        + ("" if code == "E_UNREADABLE" else ", and the node has no other lens "
                                                                             "geometry")})
        elif (not any_roles or s["role"] == "frame") and s["unread_surface"] and s["mode"] in TRIANGLE_MODES:
            lenses = near(s["box"])
            if lenses:
                where = ("position unknown" if s["box"] is None
                         else f"within {RIM_BOX_PAD_MM:g} mm of the rim of {', '.join(lenses)}")
                problems.append({"node": s["node"], "component": f"{s['node']} primitive {s['primitive']}",
                                 "code": "E_UNREADABLE_PART", "lenses": lenses,
                                 "message": f"E_UNREADABLE_PART: frame primitive {s['primitive']} was not read "
                                            f"({s['why']}), {where}: the lens cannot be graded against a part the "
                                            f"check cannot see"})
    for n, c in frame_thin:
        bi = rider_of(c, bodies, factory, cache)
        if bi >= 0:
            riders.append(dict(_entry(n, c), on=bodies[bi].name))
        else:
            solids.append((n, c))
    for n, c in frame_open:
        bi = rider_of(c, bodies, factory, cache)
        if bi >= 0:
            riders.append(dict(_entry(n, c), on=bodies[bi].name))
            continue
        lenses = near(c.box)
        if lenses:
            open_parts.append(dict(_entry(n, c), lenses=lenses))
            problems.append({"node": n["name"], "component": c.name, "code": "E_OPEN_PART", "lenses": lenses,
                             "message": f"E_OPEN_PART: frame component {c.name} is not closed "
                                        f"({c.topo['boundary_edges']} boundary, {c.topo['nonmanifold_edges']} "
                                        f"non-manifold edges) and lies within {RIM_BOX_PAD_MM:g} mm of the rim of "
                                        f"{', '.join(lenses)}: it cannot be measured as a solid, so the lens is "
                                        f"not graded against it (close the part)"})
    solids.sort(key=lambda nc: (nc[0]["name"], nc[1].name))
    return {"nodes": nodes, "bodies": bodies, "body_node": body_node, "solids": solids, "riders": riders,
            "open_parts": open_parts, "refused": refused, "problems": problems, "warnings": warnings,
            "skipped": skipped}


def parts_near(body: stations.LensBody, solids: list) -> list:
    """(node, Component) frame solids whose box lies within RIM_BOX_PAD_MM of the body's rim box (whole closed
    components: a triangle-level cull would open them and break the line parity)."""
    lo, hi = rim_box(body)
    return [(n, c) for n, c in solids if boxes_touch(c.box, lo, hi)]


# -------------------------------------------------------------------------------------------------------- check
def check_nodes(nodes: list, skipped: list, warnings: list, factory: Factory, frame_type=None, lower_edge=None,
                hidden_x_range_mm=None, stations_table: bool = False, backend_name: str = "",
                col: dict | None = None) -> dict:
    """The whole check over nodes (§8); never reads files. `col` reuses a collect() result."""
    col = col or collect(nodes, skipped, warnings, factory)
    lenses = []
    for body, n in zip(col["bodies"], col["body_node"]):
        unseen = [x for x in col["problems"] if body.name in x.get("lenses", ())]
        lenses.append(check_body(body, n, col["solids"], frame_type, lower_edge, hidden_x_range_mm, factory,
                                 stations_table, col["warnings"], unseen))
    reasons = []
    if not lenses:
        reasons.append("no lens body (a closed lens component at least 0.3 mm thick) in the file")
    reasons += [f"{x['node']}: {x['code']} {x['message']}" for x in col["refused"]]
    reasons += [f"{x['node']}: {x['message']}" for x in col["problems"]]
    reasons += [f"{x['lens']}: " + "; ".join(x["reasons"]) for x in lenses if not x["pass"]]
    return {"ok": not reasons, "reasons": reasons, "backend": backend_name, "lenses": lenses,
            "riders": col["riders"], "open_parts": col["open_parts"], "refused": col["refused"],
            "lens_problems": col["problems"], "warnings": col["warnings"],
            "lens_nodes": lens_node_names(nodes, col["skipped"])}


def lens_node_names(nodes, skipped) -> list:
    """Names of every lens node, read or not (a node whose lens primitives were all skipped is listed too)."""
    by = {n["node"]: n["name"] for n in nodes if n["lens"]}
    by.update({s["node_index"]: s["node"] for s in skipped if s["lens"] and s["node_index"] not in by})
    return [by[k] for k in sorted(by)]


def frame_type_for(body, node, meas, frame_type, lower_edge, warnings):
    """(FrameType or None, source, seat extras): inspection argument, else g3d_seat, else inferred."""
    seat = seat_extras(node)
    if frame_type is not None:
        name, le, source = frame_type, lower_edge, "argument"
        if le is None and name == seat.get("frame_type"):
            le = seat.get("lower_edge")
        if seat.get("frame_type") and (name != seat["frame_type"] or
                (name == "semi_rimless" and le is not None and le != seat.get("lower_edge", "plain"))):
            warnings.append(f"{body.name}: inspection frame type {name!r} overrides recorded "
                            f"{seat['frame_type']!r}; the g3d_seat declaration is unchanged")
    elif seat.get("frame_type"):
        name, le, source = seat["frame_type"], seat.get("lower_edge", lower_edge), "g3d_seat"
    else:
        name, le, source = infer_frame_type(meas.cls), None, "inferred"
        warnings.append(f"{body.name}: frame type inferred as {name!r} (no g3d_seat extras, no argument)"
                        if name else f"{body.name}: frame type unknown (no g3d_seat extras, no argument, nothing "
                                     f"captured to infer from)")
    if name is not None:
        try:
            ft = rules.frame_type(name, le if name == "semi_rimless" else None)
        except ValueError as exc:                             # a bad g3d_seat entry: say so, grade as inferred
            warnings.append(f"{body.name}: {source} frame type unusable ({exc}); inferred instead")
            name, source = infer_frame_type(meas.cls), "inferred"
            ft = rules.frame_type(name) if name is not None else None
    else:
        ft = None
    same_declaration = ft is not None and ft.name == seat.get("frame_type") and (
        ft.name != "semi_rimless" or ft.lower_edge == seat.get("lower_edge", "plain"))
    if ft is not None and same_declaration and isinstance(seat.get("pass_mm"), (int, float)) \
            and isinstance(seat.get("target_mm"), (int, float)) and ft.pass_mm is not None:
        # the declared thresholds may raise the bar (capture_mm above the type's default) but never lower it below
        # PLAN §3's lower bound (review fix round 1: capture_mm 0 wrote acetate pass -0.20, which passed a lens
        # with a visible 0.15 mm gap)
        floor = rules.pass_floor(ft)
        p = float(seat["pass_mm"])
        if floor is not None and p < floor - 1e-9:
            warnings.append(f"{body.name}: g3d_seat pass_mm {p:g} is below the PLAN §3 lower bound {floor:g} mm for "
                            f"{ft.name}: graded with {floor:g}")
            p = floor
        ft = dataclasses.replace(ft, pass_mm=p, target_mm=max(float(seat["target_mm"]), p))
    return ft, source, seat


def cord_overlap(body, fr, be, parts, lens_part: int, factory: Factory, verdict) -> dict:
    """§5 'the check reports cord-lens overlap' for a nylor lens, over its exposed sector (the visible-by-design
    points, where the cord runs): the frame parts holding the groove centre (the mid-depth edge point 0.30 mm inward
    along u) at any of those points, and vol(lens ^ part) for each in their station planes (stations.part_overlap,
    a backend of the lens and those parts only). The channel is left out: where it holds the lens the edge is buried
    in it by design (capture is depth inside frame material, §3), and the groove there lies inside the channel.
    Before the groove is cut the centre lies in lens material, so a cord there overlaps the lens and fails it."""
    from . import profile as PR
    vis = np.asarray(verdict).astype(str) == "VISIBLE_BY_DESIGN"
    out = {"parts": [], "overlap_mm3": 0.0, "limit_mm3": rules.CORD_OVERLAP_MAX_MM3, "exposed_points": int(vis.sum()),
           "method": f"station-plane columns along n every {stations.OVERLAP_STEP_MM:g} mm of u at the exposed points",
           "unmeasured_columns": 0}
    g = stations.parts_in_groove(fr, be, lens_part, PR.GROOVE_DEPTH_MM, only=vis)
    held = [int(p) for p in np.flatnonzero(g)]
    if not held:
        out["note"] = "no frame part in the groove along the exposed sector"
        return out
    sub = factory([(parts[p][1].V, parts[p][1].T) for p in held] + [(body.V, body.T)])
    ov = stations.part_overlap(fr, sub, len(held), range(len(held)), only=vis)
    tot = 0.0
    for k, p in enumerate(held):
        rec = ov["parts"][k]
        tot += rec["mm3"]
        out["parts"].append({"part": parts[p][1].name, "groove_points": int(g[p]), "overlap_mm3": report.r(rec["mm3"], 5),
                             "points_overlapping": rec["points"], "max_section_mm2": report.r(rec["max_section_mm2"], 5),
                             "worst_xyz": [report.r(x, 3) for x in rec["worst_xyz"]] if rec["worst_xyz"] else None})
    out["overlap_mm3"] = report.r(tot, 5)
    out["unmeasured_columns"] = ov["unmeasured_columns"]
    return out


def crystal_overlap(body, fr, parts, lens_part: int, factory: Factory, part_idx) -> dict:
    """§8 'crystal overlap mm3' (PASS needs <= 0.01, §5 'vol(lens ^ frame) <= 0.01 mm3'): vol(lens ^ part) for each
    translucent capturing part, in the station planes of every point (stations.part_overlap, a backend of the lens
    and those parts only; the cord-overlap method). A V-bevel in its §5 groove overlaps nothing (the groove clears it
    by 0.03 mm); a lens buried in an ungrooved crystal rim, a deleted g3d_groove Boolean, a stale cutter or a lens
    edited after seating overlaps the frame, and its edge, seen through the crystal, cuts into it (review fix round 1:
    the check passed all three). The groove's lips over the lens faces are covered too: the depth lines inside the
    lens never meet them."""
    idx = [int(p) for p in part_idx]
    out = {"parts": [], "overlap_mm3": 0.0, "limit_mm3": rules.CRYSTAL_OVERLAP_MAX_MM3,
           "method": f"station-plane columns along n every {stations.OVERLAP_STEP_MM:g} mm of u at every point",
           "unmeasured_columns": 0}
    if not idx:
        return out
    sub = factory([(parts[p][1].V, parts[p][1].T) for p in idx] + [(body.V, body.T)])
    ov = stations.part_overlap(fr, sub, len(idx), range(len(idx)))
    tot = 0.0
    for k, p in enumerate(idx):
        rec = ov["parts"][k]
        tot += rec["mm3"]
        out["parts"].append({"part": parts[p][1].name, "overlap_mm3": report.r(rec["mm3"], 5),
                             "points_overlapping": rec["points"],
                             "max_section_mm2": report.r(rec["max_section_mm2"], 5),
                             "worst_xyz": [report.r(x, 3) for x in rec["worst_xyz"]] if rec["worst_xyz"] else None})
    out["overlap_mm3"] = report.r(tot, 5)
    out["unmeasured_columns"] = ov["unmeasured_columns"]
    return out


def check_body(body, node, solids, frame_type, lower_edge, ranges_arg, factory: Factory, stations_table,
               warnings, unseen=()) -> dict:
    """One lens. `unseen`: E_OPEN_PART / E_UNREADABLE_PART problems naming this lens; the lens is then not graded
    (pass and graded_pass false) because a part that may hold it was not measured."""
    parts = parts_near(body, solids)
    names = [c.name for _, c in parts]
    lens_part = len(parts)                                  # the lens itself, last: it places the edge per depth
    be = factory([(c.V, c.T) for _, c in parts] + [(body.V, body.T)])
    fr, meas = stations.measure_body(body, be, lens_part)
    ft, source, seat = frame_type_for(body, node, meas, frame_type, lower_edge, warnings)
    unknown = ft is None
    if unknown:                                             # report the measurement, grade nothing
        ft = rules.frame_type("rimless")
    groove = None
    if not unknown and seat.get("profile") == "vbevel" and ft.profile == "flat_or_vbevel":
        # a declared V-bevel (seat_lens wrote it with the crystal groove, §5): capture from the groove's lips, graded
        # at the apex with every depth beyond them (stations.groove_seat, rules 'apex')
        meas, groove = stations.groove_seat(fr, be, lens_part, meas)
        ft = dataclasses.replace(ft, rule="apex")
    ranges = ranges_arg if ranges_arg is not None else ranges_from(node)
    if ft.in_range_rule and not ranges:
        warnings.append(f"{body.name}: browline without hidden x-ranges: graded as metal everywhere")
    rl = rules.rule_values(ft, meas, fr.P[:, 0], ranges)
    verdict = rules.verdicts(ft, meas, rl, fr.s, fr.loop_mm)
    band = stations.buried_band(fr, meas, be, lens_part, held_from=~rl.in_range)
    rs = rim_split(body, node)
    graded = band.candidate & rl.in_range
    band_ok = None
    if graded.any():
        band_ok = bool(np.nanmax(band.front_mm[graded]) <= rules.BAND_MAX_MM
                       and np.nanmax(band.back_mm[graded]) <= rules.BAND_MAX_MM
                       and np.nanmax(band.area_outside_mm2[graded]) <= report.BAND_AREA_ZERO_MM2)
    capturing = sorted({int(o) for o in meas.owner[:, stations.MID] if o >= 0})
    translucent = [names[i] for i in capturing if parts[i][0]["translucent"]]
    # the overlap is measured on every translucent part that captures the edge at any depth (a superset of the
    # mid-depth owners listed above)
    tr_parts = sorted({int(o) for o in meas.owner.ravel() if o >= 0 and parts[int(o)][0]["translucent"]})
    crystal = crystal_overlap(body, fr, parts, lens_part, factory, tr_parts) if tr_parts else None
    sharpened = bool(seat.get("sharpened"))
    rs_p5 = (rs or {}).get("front", {}).get("p5")
    cord = (cord_overlap(body, fr, be, parts, lens_part, factory, verdict)
            if (ft.lower_edge == "nylor" and not unknown) else None)
    chk = rules.check_verdict(ft, meas, rl, verdict, band_ok=band_ok,
                              crystal_overlap_mm3=crystal["overlap_mm3"] if crystal else None,
                              rim_split_p5_deg=rs_p5, sharpened=sharpened,
                              cord_overlap_mm3=cord["overlap_mm3"] if cord else None)
    graded_pass = bool(chk["pass"]) and not unknown
    if unknown:
        chk = dict(chk, reasons=["frame type unknown, not graded: no g3d_seat extras, no frame_type "
                                              "argument, and no ENCLOSED or LIPPED station to infer one from (the "
                                              "lens is not mounted, or its frame parts are missing)"])
    elif source == "inferred":
        chk = dict(chk, reasons=list(chk["reasons"]) + [
            f"frame type not declared (no g3d_seat extras, no frame_type argument): graded provisionally as the "
            f"inferred {ft.name!r}, which would {'PASS' if graded_pass else 'FAIL'}; declare the frame type"])
    if unseen:
        graded_pass = False
        chk = dict(chk, reasons=list(chk["reasons"]) + [
            f"not graded: {x['code']} {x['component']} may hold this lens but was not measured" for x in unseen])
    chk["pass"] = graded_pass and source != "inferred"
    extra = {"node": node["name"], "frame_type_source": source, "provisional": source == "inferred" and not unknown,
             "graded_pass": graded_pass, "ranges": ranges, "body": body.summary(), "parts": names,
             "translucent_capturing_parts": translucent,
             "crystal_overlap_mm3": crystal["overlap_mm3"] if crystal else None, "crystal": crystal,
             "stale_cutter": None,
             "cord_lens_overlap_mm3": cord["overlap_mm3"] if cord else None, "cord": cord,
             "unmeasured_parts": [{"code": x["code"], "component": x["component"]} for x in unseen]}
    if crystal:
        extra["crystal_note"] = ("stale_cutter: read in Blender (check_lens_seating compares the groove cutter's "
                                 "stored lens digest with the lens); a GLB carries no cutter, so the host reports "
                                 "null and the overlap stands for it")
    if groove is not None:
        gs = groove.gap_mm[groove.seated]
        extra["groove"] = {"points": int(len(groove.seated)), "seated": int(groove.seated.sum()),
                           "gap_min_max_mm": [report.r(np.nanmin(gs)), report.r(np.nanmax(gs))] if gs.size else None,
                           "gap_limit_mm": stations.GROOVE_GAP_MAX_MM,
                           "reading": "capture = each depth's edge beyond the groove's lips (their inner faces read "
                                      "just outside the lens faces, linear in depth between them); graded at the "
                                      "apex with every depth beyond the lips"}
    rep = report.lens_report(body.name, ft, fr, meas, rl, verdict, chk, names, band=band, rim_split=rs,
                             extra=extra, stations=stations_table)
    if unknown:
        rep.update(frame_type=None, lower_edge=None, rule=None, pass_mm=None, target_mm=None)
    return rep
