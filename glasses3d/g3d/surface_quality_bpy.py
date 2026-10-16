"""Read-only native mesh surface report; saved temple specs label chart regions only.

The report measures the evaluated mesh, never the old facet table stored on the
object. It excludes the inner wall, rolls and chamfers. A saved design that has
been changed by a later edit is explicitly reported as provenance, not as proof
that the current geometry is planar. No report value changes a finish gate.
"""
from __future__ import annotations

import json
import numpy as np

from .surface_quality import analyse
from .temple.build import to_local
from .temple.spec import parse


def _inside(points, polygon, tolerance=1e-5):
    """Vectorized even/odd membership including the polygon boundary."""
    points, polygon = np.asarray(points, float), np.asarray(polygon, float)
    inside = np.zeros(len(points), bool)
    boundary = np.zeros(len(points), bool)
    for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
        edge = b-a
        length2 = float(edge @ edge)
        if length2 == 0:
            continue
        u = np.clip((points-a) @ edge / length2, 0., 1.)
        boundary |= np.linalg.norm(points-a-u[:, None]*edge, axis=1) <= tolerance
        crosses = (a[1] > points[:, 1]) != (b[1] > points[:, 1])
        if b[1] != a[1]:
            x = a[0]+(points[:, 1]-a[1])*(b[0]-a[0])/(b[1]-a[1])
            inside ^= crosses & (points[:, 0] < x)
    return inside | boundary


def facet_triangle_map(vertices_mm, triangles, provenance):
    """Map actual triangle IDs to the saved facet's chart footprint.

    This validates each included triangle's three chart points and outward
    orientation. It does not assume vertex IDs survived later triangulation.
    Mapping fails closed for unsupported/malformed provenance; geometry movement
    along X is deliberately allowed, since that is the defect being measured.
    """
    if isinstance(provenance, str):
        provenance = json.loads(provenance)
    spec = parse(provenance['canonical_spec'])
    side_text = provenance['side']
    if side_text not in ('+x', '-x'):
        raise ValueError('temple chart must declare side +x or -x')
    side = 1 if side_text == '+x' else -1
    h = dict(spec.hinge)
    if h['axis_xz_mm'] is None:
        h['axis_xz_mm'] = provenance.get('hinge_axis_xz_mm')
    local = to_local(np.asarray(vertices_mm, float), h['z_mm'], h['axis_xz_mm'], h['opening_deg'], side)
    faces = np.asarray(triangles, int)
    oriented = faces if side > 0 else faces[:, ::-1]
    corners = local[oriented]
    normals = np.cross(corners[:, 1]-corners[:, 0], corners[:, 2]-corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    outward = normals[:, 2] > .25*lengths
    valid = outward & (lengths > 1e-10)
    chart = local[:, :2]
    # The measured panel excludes the physical edge roll. A broad facet's
    # triangle needs all three vertices outside that band, so it cannot include
    # a triangle straddling the rounded profile.
    margin = float(spec.canonical['roll_mm'])
    clearance = np.minimum(np.interp(chart[:, 0], spec.top[:, 0], spec.top[:, 1])-chart[:, 1],
                           chart[:, 1]-np.interp(chart[:, 0], spec.bottom[:, 0], spec.bottom[:, 1]))
    # The exact tangent line belongs to the planar face too. Requiring every
    # vertex beyond roll+epsilon would discard a large ngon whose tessellation
    # touches that line, even when its entire interior is flat.
    interior = clearance >= margin-1e-4
    broad_interior = clearance[faces].mean(axis=1) > margin+.02
    for chamfer in spec.canonical.get('chamfers', []):
        # Chamfer layouts are not safely identifiable from an arbitrary mutated
        # mesh without the original cell topology. Report unavailable instead of
        # mixing a bevel with the requested face's planarity.
        raise ValueError('named facet mapping unavailable for chamfered provenance')
    mapping = {}
    for label, facet in spec.canonical['facets'].items():
        polygon = [spec.canonical['nodes'][key][:2] for key in facet['poly']]
        included = _inside(chart, polygon) & interior
        ids = np.flatnonzero(valid & broad_interior & included[faces].all(axis=1))
        mapping[label] = ids.tolist()
    return mapping, {
        'status': 'charted', 'method': 'all three actual triangle vertices inside named footprint; outward face; physical edge band excluded',
        'source': 'g3d_temple canonical_spec', 'source_geometry_is_not_assumed_current': True,
        'edge_band_excluded_mm': margin, 'side': side_text,
        'empty_facets': [label for label, ids in mapping.items() if not ids],
        'limitation': 'Chart labels describe the saved footprint. Later changes to that footprint require a fresh labelled selection; no automatic repair is authorized.'}


def inspect_object(obj, mesh=None, origin_mm=None):
    """Inspect an evaluated Blender mesh in its object's local coordinates.

    ``obj.matrix_world`` is applied here; ``origin_mm`` is the asset origin in
    authored millimetres. ``mesh`` defaults to obj.data. Nothing is written to
    geometry, normals, materials, metadata or files (calc_loop_triangles caches
    Blender's tessellation only).
    """
    mesh = obj.data if mesh is None else mesh
    origin = np.zeros(3) if origin_mm is None else np.asarray(origin_mm, float)
    matrix = np.asarray(obj.matrix_world, float)
    vertices = np.asarray([v.co[:] for v in mesh.vertices], float) @ matrix[:3, :3].T + matrix[:3, 3] - origin
    mesh.calc_loop_triangles()
    faces = np.asarray([t.vertices[:] for t in mesh.loop_triangles], int).reshape(-1, 3)
    loops = np.asarray([t.loops[:] for t in mesh.loop_triangles], int).reshape(-1, 3)
    normal = np.asarray([n.vector[:] for n in mesh.corner_normals], float) @ np.linalg.inv(matrix[:3, :3])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-30)
    if np.linalg.det(matrix[:3, :3]) < 0:
        faces, loops = faces[:, ::-1], loops[:, ::-1]
    facets, mapping = {}, {'status': 'unavailable', 'reason': 'no saved named-facet chart'}
    provenance = obj.get('g3d_temple')
    if provenance:
        try:
            facets, mapping = facet_triangle_map(vertices, faces, provenance)
        except (KeyError, TypeError, ValueError) as exc:
            mapping = {'status': 'unavailable', 'reason': str(exc)}
    result = analyse(vertices, faces, normal[loops], facets=facets)
    result.update({'object': obj.name, 'facet_mapping': mapping,
                   'custom_normals': bool(mesh.has_custom_normals),
                   'smooth_polygons': sum(p.use_smooth for p in mesh.polygons),
                   'sharp_edges': sum(e.use_edge_sharp for e in mesh.edges)})
    return result
