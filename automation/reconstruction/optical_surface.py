"""Effective +Z front sheets from the upper envelope of existing mesh triangles.

All source windings participate. Higher affine triangle planes clip lower ones;
the resulting planar arrangement is triangulated without a grid approximation
of depth. Its precision lattice is derived from float32 XY coordinates. Source
contours, holes and disconnected regions are retained at that stated precision,
not replaced by a template/hull. Depth discontinuities remain explicit cracks.
This is a representation conversion, not lens identification or AR acceptance.
Artist normals, texture UVs and physical optical coordinates are not inputs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import time

import numpy as np


@dataclass(frozen=True)
class OpticalSurfacePolicy:
    maximum_source_triangles: int = 200_000
    maximum_output_triangles: int = 400_000
    maximum_patches: int = 256
    maximum_query_pairs: int = 8_000_000
    maximum_seconds: float = 240.
    sampled_position_tolerance_relative: float = .001

    def __post_init__(self):
        for name in ("maximum_source_triangles", "maximum_output_triangles", "maximum_patches", "maximum_query_pairs"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("maximum_seconds", "sampled_position_tolerance_relative"):
            value = getattr(self, name)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.sampled_position_tolerance_relative >= 1:
            raise ValueError("sampled_position_tolerance_relative must lie in (0,1)")


DEFAULT_POLICY = OpticalSurfacePolicy()


class _Unsupported(Exception):
    pass


def _polygons(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry] if not geometry.is_empty else []
    if hasattr(geometry, "geoms"):
        return [polygon for part in geometry.geoms for polygon in _polygons(part)]
    return []


def _summary(values):
    return {"maximum": float(np.max(values)), "p95": float(np.quantile(values, .95)),
            "mean": float(np.mean(values)), "count": int(len(values))} if len(values) else None


class _Budget:
    def __init__(self, policy):
        self.policy, self.started, self.pairs = policy, time.monotonic(), 0

    def check(self, pairs=0):
        self.pairs += pairs
        if self.pairs > self.policy.maximum_query_pairs:
            raise _Unsupported("source_overlap_query_capacity_exceeded")
        if time.monotonic()-self.started > self.policy.maximum_seconds:
            raise _Unsupported("surface_preparation_time_capacity_exceeded")


class _Envelope:
    def __init__(self, triangles, polygons, shapely, budget, epsilon):
        self.triangles, self.shapely, self.budget = triangles, shapely, budget
        self.polygons, self.tree, self.epsilon = polygons, shapely.STRtree(polygons), epsilon
        self.precision_boundary_queries = 0
        cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        self.slopes = -cross[:, :2]/cross[:, 2, None]
        self.intercepts = triangles[:, 0, 2]-np.sum(self.slopes*triangles[:, 0, :2], axis=1)
        cross[cross[:, 2] < 0] *= -1
        self.normals = cross/np.linalg.norm(cross, axis=1)[:, None]

    def heights(self, xy, face_ids):
        return (self.triangles[face_ids, 0, 2] +
                np.sum(self.slopes[face_ids]*(xy-self.triangles[face_ids, 0, :2]), axis=-1))

    def query(self, xy, *, return_coverage=False, arithmetic_closure=True):
        heights, normals, source_ids, exact_coverage = [], [], [], []
        for start in range(0, len(xy), 1024):
            points = xy[start:start+1024]
            e = 2*np.sqrt(2)*self.epsilon
            pairs = self.tree.query(self.shapely.box(points[:, 0]-e, points[:, 1]-e,
                                                     points[:, 0]+e, points[:, 1]+e))
            self.budget.check()
            if pairs.shape[1] > self.budget.policy.maximum_query_pairs:
                raise _Unsupported("point_query_capacity_exceeded")
            point_ids, face_ids = pairs
            query_points = self.shapely.points(points[point_ids])
            inside = self.shapely.covers(self.polygons[face_ids], query_points)
            # Normalized coordinates have magnitude <=1. Shared triangle edges
            # can acquire double-roundoff cracks in overlay predicates. Include
            # their closure within a disclosed arithmetic band, independently
            # of the much larger float32 preparation lattice.
            if arithmetic_closure:
                inside |= self.shapely.dwithin(self.polygons[face_ids], query_points, 128*np.finfo(float).eps)
            uncovered = np.ones(len(points), bool)
            uncovered[point_ids[inside]] = False
            if uncovered.any():
                near = uncovered[point_ids] & self.shapely.dwithin(
                    self.polygons[face_ids], self.shapely.points(points[point_ids]), e)
                inside |= near
                self.precision_boundary_queries += int(np.count_nonzero(uncovered))
            point_ids, face_ids = point_ids[inside], face_ids[inside]
            z = self.heights(points[point_ids], face_ids)
            order = np.lexsort((face_ids, -z, point_ids))
            ordered = point_ids[order]
            first = np.r_[True, ordered[1:] != ordered[:-1]] if len(order) else np.zeros(0, bool)
            winners = order[first]
            if len(winners) != len(points):
                raise _Unsupported("front_envelope_query_has_uncovered_points")
            heights.append(z[winners]); normals.append(self.normals[face_ids[winners]])
            source_ids.append(face_ids[winners])
            exact_coverage.append(self.shapely.covers(self.polygons[face_ids[winners]], self.shapely.points(points)))
        result = np.concatenate(heights), np.concatenate(normals), np.concatenate(source_ids)
        return (*result, np.concatenate(exact_coverage)) if return_coverage else result


def _positive_halfplane(triangle_xy, difference):
    """Clip a triangle by the affine inequality difference >= 0."""
    result = []
    for a in range(3):
        b = (a+1) % 3
        pa, pb, da, db = triangle_xy[a], triangle_xy[b], difference[a], difference[b]
        if da >= 0:
            result.append(pa)
        if (da < 0 < db) or (db < 0 < da):
            result.append(pa+(pb-pa)*(da/(da-db)))
    return np.asarray(result)


def _visible_fragments(envelope, precision, shapely, report):
    """Subtract every higher plane's overlap, including back-wound triangles."""
    visible, visible_faces, exact_ties = [], [], 0
    for i, polygon in enumerate(envelope.polygons):
        if i % 128 == 0:
            envelope.budget.check()
        candidates = envelope.tree.query(polygon, predicate="intersects")
        candidates = candidates[candidates != i]
        envelope.budget.check(len(candidates))
        if not len(candidates):
            visible.append(polygon)
            visible_faces.append(i)
            continue
        coords = envelope.triangles[candidates, :, :2]
        difference = (envelope.slopes[candidates]-envelope.slopes[i])[:, None, :]*coords
        difference = difference.sum(axis=2)+(envelope.intercepts[candidates]-envelope.intercepts[i])[:, None]
        tied = np.all(difference == 0, axis=1)
        exact_ties += int(np.count_nonzero(tied & (candidates < i)))
        occluders = []
        for j, values, is_tied in zip(candidates, difference, tied):
            if is_tied:
                if j < i:
                    occluders.append(envelope.polygons[j])
            elif values.max() > 0:
                if values.min() >= 0:
                    occluders.append(envelope.polygons[j])
                else:
                    clipped = _positive_halfplane(envelope.triangles[j, :, :2], values)
                    if len(clipped) >= 3:
                        # Pointwise snapping can collapse a very thin convex
                        # clip. Repair that snapped topology before overlay;
                        # set_precision(valid_output) can itself fail on it.
                        piece = shapely.make_valid(shapely.Polygon(np.rint(clipped/precision)*precision))
                        piece = shapely.set_precision(piece, precision, mode="pointwise")
                        occluders.extend(_polygons(piece))
        if occluders:
            hidden = shapely.union_all(occluders, grid_size=precision)
            polygon = shapely.difference(polygon, hidden, grid_size=precision)
        fragments = _polygons(polygon)
        visible.extend(fragments)
        if fragments:
            visible_faces.append(i)
        if len(visible) > envelope.budget.policy.maximum_output_triangles:
            raise _Unsupported("visible_fragment_capacity_exceeded")
    report["clipping"] = {"source_pair_queries": envelope.budget.pairs,
                          "visible_fragments": len(visible), "exact_coplanar_tie_occlusions": exact_ties,
                          "tie_policy": "exact equal affine planes choose lowest source face index"}
    return visible, np.asarray(visible_faces, np.int64)


def _arrangement(visible, source_domains, footprint, precision, envelope, shapely):
    # Global noding makes a single planar subdivision, including T-junctions.
    # XY vertices lie on one power-of-two lattice, exactly representable in the
    # final float32 world frame; donor planes retain distinct crack-edge Z.
    # Source-domain edges must remain in the arrangement even when an entire
    # neighboring facet is hidden. Otherwise a thin clipped fragment can span
    # the edge of a different occluding source triangle after snap rounding.
    boundaries = shapely.boundary(np.asarray([*visible, *source_domains, footprint], object))
    linework = shapely.union_all(boundaries, grid_size=precision)
    cells = list(shapely.polygonize(shapely.get_parts(linework)).geoms)
    cell_array = np.asarray(cells, object)
    interior = shapely.covers(footprint, cell_array)
    uncertain = np.flatnonzero(~interior)
    if len(uncertain):
        interior[uncertain] = shapely.area(shapely.intersection(cell_array[uncertain], footprint)) > 0
    cells = list(cell_array[interior])
    envelope.budget.check()
    if len(cells) > envelope.budget.policy.maximum_output_triangles:
        raise _Unsupported("planar_arrangement_capacity_exceeded")
    xy = np.array([[c.representative_point().x, c.representative_point().y] for c in cells])
    if not len(xy):
        raise _Unsupported("empty_visible_planar_arrangement")
    patch_tree = shapely.STRtree(_polygons(footprint))
    assigned = patch_tree.query(shapely.points(xy), predicate="covered_by")
    # A representative point can lie exactly on a shared boundary. Equal
    # candidates choose the lowest footprint-component index deterministically.
    patch_ids = np.full(len(cells), len(patch_tree.geometries), np.int64)
    np.minimum.at(patch_ids, assigned[0], assigned[1])
    for i in np.flatnonzero(patch_ids == len(patch_tree.geometries)):
        choices = patch_tree.query(cells[i], predicate="intersects")
        areas = shapely.area(shapely.intersection(patch_tree.geometries[choices], cells[i]))
        if not len(areas) or areas.max() <= 0:
            raise _Unsupported("ambiguous_projected_patch_assignment")
        patch_ids[i] = choices[np.argmax(areas)]
    triangles, triangle_patches = [], []
    for i, group in enumerate(shapely.constrained_delaunay_triangles(np.asarray(cells, object))):
        for part in _polygons(group):
            points = np.asarray(part.exterior.coords)[:-1, :2]
            if points.shape != (3, 2):
                raise _Unsupported("constrained_triangulator_returned_nontriangle")
            triangles.append(points); triangle_patches.append(patch_ids[i])
        if len(triangles) > envelope.budget.policy.maximum_output_triangles:
            raise _Unsupported("output_triangle_capacity_exceeded")
    envelope.budget.check()
    return np.asarray(triangles), np.asarray(triangle_patches), len(cells)


def _surface(xy_triangles, donors, envelope, origin, scale, patch_id):
    # Welding XY alone would turn a jump into a ramp: weld XYZ only.
    xy = xy_triangles.reshape(-1, 2)
    face_ids = np.repeat(donors, 3)
    # Clamping depth-evaluation XY to the actual donor triangle avoids steep
    # extrapolation. Convexity supplies a source-triangle witness for EVERY
    # interpolated output point, bounded by the largest vertex XY movement;
    # this alone does not prove that the donor remains the visible envelope.
    lines = envelope.shapely.shortest_line(envelope.shapely.points(xy), envelope.polygons[face_ids])
    source_xy = envelope.shapely.get_coordinates(envelope.shapely.get_point(lines, 1))
    displacement = np.linalg.norm(source_xy-xy, axis=1)
    if displacement.max() > 2*np.sqrt(2)*envelope.epsilon:
        raise _Unsupported("donor_vertex_xy_displacement_exceeds_precision_band")
    heights = envelope.heights(source_xy, face_ids)
    xyz = np.column_stack((xy_triangles.reshape(-1, 2), heights))*scale+origin
    positions, inverse = np.unique(xyz.astype(np.float32), axis=0, return_inverse=True)
    indices = inverse.reshape(-1, 3)
    triangle = positions[indices].astype(np.float64)
    cross = np.cross(triangle[:, 1]-triangle[:, 0], triangle[:, 2]-triangle[:, 0])
    backwards = cross[:, 2] < 0
    indices[backwards] = indices[backwards][:, [0, 2, 1]]; cross[backwards] *= -1
    if not np.isfinite(cross).all() or np.any(cross[:, 2] <= 0):
        raise _Unsupported("float32_output_contains_collapsed_projected_triangle")
    normals = np.zeros(positions.shape, np.float64)
    for corner in range(3):
        np.add.at(normals, indices[:, corner], cross)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    extent = np.ptp(positions[:, :2].astype(np.float64), axis=0)
    if np.any(extent <= 0):
        raise _Unsupported("float32_quantization_collapses_patch_extent")
    uv = ((positions[:, :2].astype(np.float64)-positions[:, :2].min(axis=0))/extent).astype(np.float32)
    return ({"id": f"front-sheet-{patch_id:03d}", "positions": positions, "normals": normals.astype(np.float32),
             "uv": uv, "indices": indices.astype(np.uint32)},
            {"clamped_vertices": int(np.count_nonzero(displacement > 0)),
             "maximum_donor_xy_displacement_source_units": float(displacement.max()*scale)})


def _positional_witnesses(points, envelope, band):
    """Actual source-envelope points bound nearest-surface distance from above.

    A depth jump has no continuous vertical-error bound under XY rounding. For
    each problematic probe, test nearby source triangle closest points and an
    infinitesimal inward offset. Evaluate the ORIGINAL upper envelope at these
    XY witnesses, never merely distance to an arbitrary back surface. This is
    a finite witnessed upper bound, not a global Hausdorff certificate.
    """
    distances = []
    for point in points:
        x, y = point[:2]
        ids = envelope.tree.query(envelope.shapely.box(x-band, y-band, x+band, y+band))
        triangles = envelope.triangles[ids]
        if not len(ids):
            raise _Unsupported("no_original_surface_within_xy_precision_band")
        # Closest point on each 3D triangle: orthogonal plane projection when
        # interior, otherwise closest among its three segments.
        a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        normal = np.cross(b-a, c-a)
        projected = point-normal*(np.sum((point-a)*normal, axis=1)/np.sum(normal*normal, axis=1))[:, None]
        u, v, w = b-a, c-a, projected-a
        uu, uv, vv = np.sum(u*u, axis=1), np.sum(u*v, axis=1), np.sum(v*v, axis=1)
        wu, wv = np.sum(w*u, axis=1), np.sum(w*v, axis=1)
        denominator = uu*vv-uv*uv
        second, third = (wu*vv-wv*uv)/denominator, (wv*uu-wu*uv)/denominator
        interior = (second >= 0) & (third >= 0) & (second+third <= 1)
        options = []
        for first, last in ((a, b), (b, c), (c, a)):
            direction = last-first
            fraction = np.clip(np.sum((point-first)*direction, axis=1)/np.sum(direction*direction, axis=1), 0, 1)
            options.append(first+fraction[:, None]*direction)
        options = np.stack(options, axis=1)
        distance = np.linalg.norm(options-point, axis=2)
        closest = options[np.arange(len(ids)), np.argmin(distance, axis=1)]
        closest[interior] = projected[interior]
        nearest_xy = envelope.shapely.get_coordinates(envelope.shapely.get_point(
            envelope.shapely.shortest_line(envelope.shapely.Point(point[:2]), envelope.polygons[ids]), 1))
        xy = np.concatenate([closest[:, :2], nearest_xy])
        centroid = np.tile(triangles[:, :, :2].mean(axis=1), (2, 1))
        toward = centroid-xy
        inward = np.minimum(1., 1024*np.finfo(float).eps/np.maximum(np.linalg.norm(toward, axis=1), 1e-300))
        witnesses = np.concatenate([xy, xy+toward*inward[:, None]])
        # A point inside an occluder can be closest to a lower exposed sheet
        # just across that occluder's edge. Interior closest points alone miss
        # this. Probe both sides of nearby original edges; only exactly covered
        # raw-domain points survive below, and their MAX-Z is re-evaluated.
        edge_witnesses = []
        for first, last in ((a[:, :2], b[:, :2]), (b[:, :2], c[:, :2]), (c[:, :2], a[:, :2])):
            direction = last-first
            fraction = np.clip(np.sum((point[:2]-first)*direction, axis=1)/np.sum(direction*direction, axis=1), 0, 1)
            edge_xy = first+fraction[:, None]*direction
            perpendicular = np.column_stack([-direction[:, 1], direction[:, 0]])
            perpendicular /= np.linalg.norm(perpendicular, axis=1)[:, None]
            shift = perpendicular*(1024*np.finfo(float).eps)
            edge_witnesses.extend([edge_xy, edge_xy+shift, edge_xy-shift])
        witnesses = np.concatenate([witnesses, *edge_witnesses])
        # Restrict evidence to the declared local XY precision neighborhood.
        witnesses = witnesses[np.linalg.norm(witnesses-point[:2], axis=1) <= band]
        covered = envelope.tree.query(envelope.shapely.points(witnesses), predicate="intersects")
        witnesses = witnesses[np.unique(covered[0])]
        if not len(witnesses):
            raise _Unsupported("no_exact_original_envelope_witness_within_xy_precision_band")
        z, _, _ = envelope.query(witnesses, arithmetic_closure=False)
        actual = np.column_stack([witnesses, z])
        distances.append(float(np.min(np.linalg.norm(actual-point, axis=1))))
    return np.asarray(distances)


def _measure(surfaces, envelope, origin, scale, shapely, footprint, raw_footprint, precision):
    depth_errors, position_errors, angle_errors, front_angles, emitted = [], [], [], [], []
    exceptional_count = 0
    # Each source snap / overlay node moves at most half a lattice diagonal;
    # four successive rounding stages give this conservative local XY band.
    band = 2*np.sqrt(2)*precision
    discontinuity_edges, discontinuity_jumps = 0, []
    probes = np.array([[1/3, 1/3, 1/3], [.6, .2, .2], [.2, .6, .2], [.2, .2, .6]])
    for surface in surfaces:
        world = surface["positions"][surface["indices"]].astype(np.float64)
        triangle = (world-origin)/scale
        emitted.extend(shapely.polygons(triangle[:, :, :2]))
        samples = np.einsum("pi,tij->tpj", probes, triangle).reshape(-1, 3)
        z, reference_normals, _, covered = envelope.query(samples[:, :2], return_coverage=True, arithmetic_closure=False)
        raw_error = np.abs(samples[:, 2]-z)
        depth_errors.append(raw_error)
        exceptional = (raw_error > envelope.budget.policy.sampled_position_tolerance_relative) | ~covered
        positional = raw_error.copy()
        if exceptional.any():
            positional[exceptional] = _positional_witnesses(samples[exceptional], envelope, band)
        exceptional_count += int(np.count_nonzero(exceptional))
        position_errors.append(positional)
        normals = surface["normals"][surface["indices"]].astype(np.float64)
        normals = np.einsum("pi,tij->tpj", probes, normals).reshape(-1, 3)
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        angle_errors.append(np.degrees(np.arccos(np.clip(np.sum(normals*reference_normals, axis=1), -1, 1))))
        front_angles.append(np.degrees(np.arccos(np.clip(reference_normals[:, 2], -1, 1))))
        edges = {}
        for t in world:
            for a, b in ((0, 1), (1, 2), (2, 0)):
                pa, pb = t[a], t[b]
                if tuple(pa[:2]) > tuple(pb[:2]):
                    pa, pb = pb, pa
                key = tuple(pa[:2])+tuple(pb[:2])
                if key in edges:
                    prior = edges[key]
                    jump = max(abs(pa[2]-prior[0]), abs(pb[2]-prior[1]))
                    # Exact float32 disagreement is recorded, including tiny
                    # plane-evaluation differences; not silently welded.
                    if jump > 0:
                        discontinuity_edges += 1; discontinuity_jumps.append(jump)
                else:
                    edges[key] = (pa[2], pb[2])
    coverage = shapely.union_all(emitted)
    duplicate_area = sum(p.area for p in emitted)-coverage.area
    difference = footprint.symmetric_difference(coverage).area
    depth = np.concatenate(depth_errors)
    # This tests coverage inside a stated coordinate error band, including
    # holes. An opening with interior farther than band cannot be silently
    # filled, unlike an arbitrary relative-area threshold or raw hole count.
    footprint_in_band = footprint.buffer(band).covers(coverage) and coverage.buffer(band).covers(footprint)
    raw_footprint_in_band = raw_footprint.buffer(band).covers(coverage) and coverage.buffer(band).covers(raw_footprint)
    return {"sampled_depth_error_relative": _summary(depth),
            "sampled_depth_error_source_units": _summary(depth*scale),
            "sampled_position_witness_distance_relative": _summary(np.concatenate(position_errors)),
            "sampled_position_witness_distance_source_units": _summary(np.concatenate(position_errors)*scale),
            "vertical_exception_probes_rechecked": exceptional_count,
            "position_witness_scope": "same-XY original envelope when within budget; exceptional probes use actual original upper-envelope witnesses inside the XY rounding band; sampled upper bound only",
            "sampled_regenerated_normal_deviation_degrees": _summary(np.concatenate(angle_errors)),
            "sampled_source_front_incidence_degrees": _summary(np.concatenate(front_angles)),
            "footprint_symmetric_difference_relative_area": float(difference/footprint.area),
            "projected_triangle_overlap_relative_area": float(max(0, duplicate_area)/footprint.area),
            "footprint_within_declared_xy_rounding_band": bool(footprint_in_band),
            "original_footprint_within_declared_xy_rounding_band": bool(raw_footprint_in_band),
            "xy_rounding_band_source_units": float(band*scale),
            "output_patch_count": len(_polygons(coverage)),
            "output_hole_count": sum(len(p.interiors) for p in _polygons(coverage)),
            "discontinuity_edges": discontinuity_edges,
            "discontinuity_jump_source_units": _summary(discontinuity_jumps),
            "sampling_scope": "four fixed interior barycentric probes per output triangle; no global depth or normal error certificate"}


def prepare_front_surfaces(vertices: np.ndarray, faces: np.ndarray, *, policy: OpticalSurfacePolicy = DEFAULT_POLICY) -> dict:
    """Return complete candidates or explicit refusal; never mutate input arrays.

    Inspect report.status. Unsupported results can retain complete diagnostic
    surfaces but are not export-authorized. An exact plane envelope is computed
    using a disclosed float32 precision lattice and clamped donor coordinates; no global geometric-error bound or
    photometric agreement is claimed. Final AR topology validation is required.
    """
    xyz, index = np.asarray(vertices), np.asarray(faces)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or len(xyz) < 3 or xyz.dtype.kind != "f" or not np.isfinite(xyz).all():
        raise ValueError("vertices must be finite floating Nx3 coordinates")
    if index.ndim != 2 or index.shape[1] != 3 or not len(index) or index.dtype.kind not in "iu" or index.min() < 0 or index.max() >= len(xyz):
        raise ValueError("faces must be valid integer Mx3 indices")
    if not isinstance(policy, OpticalSurfacePolicy):
        raise TypeError("policy must be OpticalSurfacePolicy")
    xyz, index = xyz.astype(np.float64), index.astype(np.int64)
    report = {"schema_version": 1, "method": "source_triangle_upper_envelope_v2", "status": "unsupported",
              "profile": "front_sheet_v1", "policy": asdict(policy), "reasons": [], "attempts": [],
              "source_geometry_sha256": hashlib.sha256(xyz.tobytes()+index.tobytes()).hexdigest(),
              "source_vertex_count": len(xyz), "source_triangle_count": len(index),
              "semantic_identity": "unverified_candidate_part", "quality_verdict": "unmeasured",
              "normal_policy": "regenerated_area_weighted_output_geometry_normals; source authored normals unavailable",
              "uv_policy": "per_connected_projected_patch_normalized_authored_XY; v=bottom0_top1; derived_height_coordinate",
              "runtime_topology_validation_required": True,
              "limitations": ["Candidate part identity is unverified; frontmost geometry may include a mislabeled component or back surface.",
                  "Source atlas UVs, artist normals, textures and material response are unavailable to this geometry API.",
                  "Back/side interfaces are omitted by the effective front-sheet representation; depth jumps remain cracks.",
                  "Overlay uses a disclosed float32 XY precision lattice; sub-resolution topology is recorded separately.",
                  "Depth/normal diagnostics are sampled, not global error bounds; conversion does not establish AR appearance acceptance."]}
    surfaces = []
    if len(index) > policy.maximum_source_triangles:
        report["reasons"] = ["source_triangle_capacity_exceeded"]
        return {"surfaces": surfaces, "report": report}
    used = np.unique(index); report["source_used_vertex_count"] = len(used)
    xyz, index = xyz[used], np.searchsorted(used, index)
    import shapely
    report["runtime"] = {"shapely": shapely.__version__, "geos": shapely.geos_version_string, "numpy": np.__version__}
    budget = _Budget(policy)
    extent = float(np.max(np.ptp(xyz, axis=0)))
    if not np.isfinite(extent) or extent <= 0:
        raise ValueError("Source must have a finite nonzero extent")
    scale = float(2.**np.ceil(np.log2(extent)))
    cast = xyz[:, :2].astype(np.float32)
    if not np.isfinite(cast).all():
        report["reasons"] = ["source_xy_not_representable_as_float32"]
        return {"surfaces": [], "report": report}
    float32_ulp = float(np.max(np.spacing(np.abs(cast))))
    runtime_area_floor = float(np.sum(np.ptp(xyz, axis=0)**2)*1e-14)
    minimum_step = np.sqrt(runtime_area_floor)
    area_lattice = float(2.**(np.floor(np.log2(minimum_step))+1))
    world_precision = max(float32_ulp, area_lattice)
    precision = world_precision/scale
    origin = (xyz.min(axis=0)+xyz.max(axis=0))/2
    origin[:2] = np.rint(origin[:2]/world_precision)*world_precision
    normalized = (xyz-origin)/scale
    triangle = normalized[index]
    cross = np.cross(triangle[:, 1]-triangle[:, 0], triangle[:, 2]-triangle[:, 0])
    projectable = cross[:, 2] != 0
    report["zero_projected_area_source_triangles"] = int(np.count_nonzero(~projectable))
    report["source_winding_counts"] = {"positive_z": int(np.count_nonzero(cross[:, 2] > 0)),
                                       "negative_z": int(np.count_nonzero(cross[:, 2] < 0))}
    if not projectable.any():
        report["reasons"] = ["no_positive_area_xy_footprint"]
        return {"surfaces": [], "report": report}
    triangle = triangle[projectable]
    try:
        raw_polygons = shapely.polygons(triangle[:, :, :2])
        raw_footprint = shapely.union_all(raw_polygons)
        snapped_xy = np.rint(triangle[:, :, :2]/precision)*precision
        snapped_polygons = shapely.polygons(snapped_xy)
        noncollapsed = shapely.area(snapped_polygons) > 0
        report["precision"] = {"xy_lattice_source_units": world_precision,
            "construction_query_normalized_xy_arithmetic_closure": float(128*np.finfo(float).eps),
            "validation_query_policy": "strict original polygon covers; outside-domain probes require covered original-envelope witnesses",
            "maximum_source_xy_float32_ulp": float32_ulp, "runtime_area_floor_source_units_squared": runtime_area_floor,
            "source_vertex_max_xy_displacement": float(np.max(np.linalg.norm(snapped_xy-triangle[:, :, :2], axis=2))*scale),
            "collapsed_source_xy_triangles": int(np.count_nonzero(~noncollapsed)),
            "policy": "shared power-of-two lattice >= max float32 XY ULP, with step squared strictly exceeding unchanged runtime area floor; global noding",
            "raw_footprint_patches": len(_polygons(raw_footprint)),
            "raw_footprint_holes": sum(len(p.interiors) for p in _polygons(raw_footprint))}
        # Preserve original affine depth planes, snapping only their XY domains.
        source_triangle = triangle[noncollapsed]
        polygons = shapely.set_precision(snapped_polygons[noncollapsed], precision)
        # Snap the original union once. Repeated grid overlay of thousands of
        # thin overlapping triangles can otherwise invent one-cell holes.
        footprint = shapely.set_precision(raw_footprint, precision)
        patches = _polygons(footprint)
        report["footprint"] = {"patches": len(patches), "holes": sum(len(p.interiors) for p in patches),
            "area_source_units_squared": float(footprint.area*scale*scale), "source_scale": scale,
            "raw_symmetric_difference_source_units_squared": float(raw_footprint.symmetric_difference(footprint).area*scale*scale),
            "union_policy": "all_nonzero_XY_source_triangles_without_winding_filter_at_declared_float32_precision"}
        if not patches:
            raise _Unsupported("float32_precision_collapses_source_footprint")
        if len(patches) > policy.maximum_patches:
            raise _Unsupported("projected_patch_capacity_exceeded")
        envelope = _Envelope(source_triangle, polygons, shapely, budget, precision)
        reference = _Envelope(triangle, raw_polygons, shapely, budget, precision)
        visible, visible_faces = _visible_fragments(envelope, precision, shapely, report)
        source_domains = raw_polygons[np.flatnonzero(noncollapsed)[visible_faces]]
        xy, patch_ids, cell_count = _arrangement(visible, source_domains, footprint, precision, reference, shapely)
        # Assign original-domain front planes at each output triangle's own
        # interior, rather than extrapolating a donor chosen across a snapped
        # polygon sliver. This does not resample or blend vertex depth.
        centers = xy.mean(axis=1)
        probes = np.array([[1/3, 1/3, 1/3], [.8, .1, .1], [.1, .8, .1], [.1, .1, .8]])
        original_probes = np.einsum("pi,tij->tpj", probes, xy)
        _, _, probe_donors = reference.query(original_probes.reshape(-1, 2))
        probe_donors = probe_donors.reshape(-1, 4)
        repeat_centers = np.repeat(centers, 4, axis=0)
        heights = reference.heights(repeat_centers, probe_donors.ravel()).reshape(-1, 4)
        near_domain = shapely.dwithin(reference.polygons[probe_donors.ravel()], shapely.points(repeat_centers),
                                     2*np.sqrt(2)*precision).reshape(-1, 4)
        heights[~near_domain] = -np.inf
        donors = probe_donors[np.arange(len(xy)), np.argmax(heights, axis=1)]
        report["donor_policy"] = "highest original front-plane witness from four interior probes whose original XY domain is within the disclosed precision band of the triangle center; source planes never blended"
        prepared = [_surface(xy[patch_ids == p], donors[patch_ids == p], reference, origin, scale, p)
                    for p in range(len(patches))]
        candidate = [item[0] for item in prepared]
        report["donor_xy_clamps"] = [item[1] for item in prepared]
        surfaces = candidate
        runtime_checks = []
        for surface in candidate:
            positions = surface["positions"].astype(np.float64)
            emitted = positions[surface["indices"]]
            floor = float(np.sum(np.ptp(positions, axis=0)**2)*1e-14)
            minimum = float(np.min(np.cross(emitted[:, 1]-emitted[:, 0], emitted[:, 2]-emitted[:, 0])[:, 2]))
            runtime_checks.append({"surface": surface["id"], "minimum_cross_z": minimum,
                                   "actual_output_area_floor": floor, "passes_area_floor": minimum > floor})
        report["runtime_area_checks"] = runtime_checks
        if not all(check["passes_area_floor"] for check in runtime_checks):
            raise _Unsupported("actual_float32_output_fails_runtime_area_floor")
        measurements = _measure(candidate, reference, origin, scale, shapely, footprint, raw_footprint, precision)
        attempt = {"triangles": sum(len(s["indices"]) for s in candidate), "surfaces": len(candidate),
                   "arrangement_cells": cell_count, **measurements}
        report["attempts"].append(attempt); surfaces = candidate
        if (not measurements["footprint_within_declared_xy_rounding_band"] or
                not measurements["original_footprint_within_declared_xy_rounding_band"] or
                measurements["projected_triangle_overlap_relative_area"] > 1e-10):
            raise _Unsupported("float32_output_changes_precision_footprint_or_overlaps")
        if measurements["sampled_position_witness_distance_relative"]["maximum"] > policy.sampled_position_tolerance_relative:
            raise _Unsupported("sampled_position_error_exceeds_budget")
        report["status"] = "prepared_candidate"
    except (_Unsupported, shapely.errors.GEOSException) as error:
        report["reasons"].append(str(error))
    report["elapsed_seconds"] = time.monotonic()-budget.started
    report = json.loads(json.dumps(report, allow_nan=False))
    return {"surfaces": surfaces, "report": report}
