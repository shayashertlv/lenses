"""Find omitted lens fragments using visible photo support and surface continuity.

This proposes complete-component repairs, never paints a fused frame primitive
optical. Every material assignment remains a hypothesis; unresolved opaque
support is reported separately so successful fitting cannot hide it.
"""
from dataclasses import asdict, dataclass

import numpy as np
from scipy.ndimage import binary_erosion
from scipy.spatial import cKDTree

from .camera import Camera
from .raster import rasterize


@dataclass(frozen=True)
class MembershipPolicy:
    minimum_visible_pixels: int = 24
    minimum_views: int = 2
    minimum_camera_separation_degrees: float = 5.
    minimum_inside_fraction: float = .90
    minimum_core_fraction: float = .65
    maximum_bend_residual_fraction: float = .045
    maximum_attachment_gap_fraction: float = .006
    maximum_fragment_area_fraction: float = .40
    aperture_erosion_pixels: int = 2


def _bend(points):
    center = points.mean(axis=0)
    scale = float(np.ptp(points, axis=0).max())
    def design(p):
        x, y = ((p-center)/scale)[:, :2].T
        return np.column_stack((np.ones(len(p)), x, y, x*x, x*y, y*y))
    a = design(points); z = (points[:, 2]-center[2])/scale
    coefficient = np.linalg.lstsq(a, z, rcond=None)[0]
    for _ in range(5):
        w = np.minimum(1., .006/np.maximum(np.abs(z-a@coefficient), 1e-10))
        coefficient = np.linalg.lstsq(a*w[:, None], z*w, rcond=None)[0]
    return center, scale, design, coefficient


def audit_optical_membership(mesh, face_components, groups, views, *, policy=MembershipPolicy(), visibility_mesh=None):
    """Return bounded assignments and visible opaque support with source face IDs.

    ``mesh`` and view cameras share coordinates. ``groups`` contains the already
    declared optical groups and component members; view interpretations contain
    masks and known domains. A +Y-up, +Z-front candidate is required. Evidence is
    computed on first hits, not complete projected component silhouettes, which
    otherwise treat occluded cut walls as photographically visible frame pixels.
    """
    labels = np.asarray(face_components)
    if labels.shape != (len(mesh.faces),) or labels.dtype.kind not in 'iu' or np.any(labels < 0):
        raise ValueError('Complete nonnegative source component labels are required')
    if len({g['group_id'] for g in groups}) != len(groups):
        raise ValueError('Optical group identifiers must be unique')
    members = [m for g in groups for m in g['members']]
    if len(members) != len(set(members)) or not set(members).issubset(set(labels.tolist())):
        raise ValueError('Optical membership must be disjoint and source bound')
    declared = np.isin(labels, members)
    if visibility_mesh is not None and (len(visibility_mesh.faces) != len(mesh.faces) or
                                       not np.array_equal(visibility_mesh.vertices,mesh.vertices)):
        raise ValueError('Visibility mesh must preserve source face ordinals and vertices')
    centroids = mesh.vertices[mesh.faces].mean(axis=1)
    triangles = mesh.vertices[mesh.faces]
    area = np.linalg.norm(np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), axis=1)/2
    rows, captured = [], []
    for view in views:
        camera = view['camera'] if isinstance(view['camera'], Camera) else Camera(**view['camera'])
        masks = [np.asarray(i['mask'], bool) for i in view['interpretations']]
        if not masks:
            raise ValueError('At least one aperture interpretation is required')
        known = [np.asarray(i.get('known_domain', np.ones_like(masks[0])), bool) for i in view['interpretations']]
        if not masks or any(m.shape != tuple(view['shape']) or k.shape != m.shape or np.any(m & ~k) for m,k in zip(masks,known)):
            raise ValueError('Apertures must have the camera pixel grid and a valid known domain')
        core = binary_erosion(np.logical_and.reduce(masks), iterations=policy.aperture_erosion_pixels)
        visible_scene = view.get('mesh', visibility_mesh or mesh)
        if len(visible_scene.faces) != len(mesh.faces):
            raise ValueError('Articulated visibility must preserve source face ordinals')
        raster = rasterize(visible_scene, camera, view['shape'])
        ids = raster.face_index
        owner = np.full(ids.shape, -1, np.int64); owner[raster.mask] = labels[ids[raster.mask]]
        opaque = raster.mask.copy(); opaque[raster.mask] = ~declared[ids[raster.mask]]
        table = []
        for cid in np.unique(owner[owner >= 0]):
            visible = owner == cid
            ratios = [float(np.count_nonzero(visible&m)/max(1,np.count_nonzero(visible&k))) for m,k in zip(masks, known)]
            measured = [int(np.count_nonzero(visible&k)) for k in known]
            table.append({'component_id': int(cid), 'visible_pixels': int(visible.sum()), 'known_pixels_min': min(measured),
                          'inside_fraction_min': min(ratios), 'core_fraction': float(np.count_nonzero(visible&core)/visible.sum())})
        rows.append({'view_id': view['id'], 'core_pixels': int(core.sum()), 'opaque_core_pixels_before': int(np.count_nonzero(opaque&core)),
                     'components': table})
        captured.append((camera, owner, core))
    models = []
    for group in groups:
        faces = np.isin(labels, group['members'])
        vertices = mesh.vertices[np.unique(mesh.faces[faces])]
        if len(vertices) < 6 or np.ptp(vertices,axis=0).max() <= 0:
            continue
        center, scale, design, coefficient = _bend(vertices)
        models.append((group, faces, vertices, center, scale, design, coefficient))
    proposals, diagnostics = [], []
    for cid in sorted(set(labels.tolist())-set(members)):
        supported, contrary, angles = [], [], []
        for row, (camera, _, _) in zip(rows,captured):
            item = next((r for r in row['components'] if r['component_id'] == cid), None)
            if not item or item['known_pixels_min'] < policy.minimum_visible_pixels:
                continue
            if item['inside_fraction_min'] >= policy.minimum_inside_fraction and item['core_fraction'] >= policy.minimum_core_fraction:
                supported.append(row['view_id'])
                yaw,pitch=np.radians([camera.yaw,camera.pitch])
                angles.append(np.array([np.cos(pitch)*np.sin(yaw),np.sin(pitch),np.cos(pitch)*np.cos(yaw)]))
            else:
                contrary.append(row['view_id'])
        separation = max([float(np.degrees(np.arccos(np.clip(a@b,-1,1)))) for a in angles for b in angles] or [0.])
        compatible, geometry_tests = [], []
        faces = labels == cid
        vertices = mesh.vertices[np.unique(mesh.faces[faces])]
        for group, gf, gv, center, scale, design, coefficient in models:
            residual = np.abs((centroids[faces,2]-center[2])/scale-design(centroids[faces])@coefficient)
            q95 = float(np.quantile(residual,.95))
            gap = float(cKDTree(gv).query(vertices,k=1)[0].min()/scale)
            size = float(area[faces].sum()/max(area[gf].sum(),1e-20))
            geometry_tests.append({'group_id': group['group_id'], 'bend_residual_95_fraction':q95,
                                   'attachment_gap_fraction':gap, 'surface_area_fraction':size})
            if q95 <= policy.maximum_bend_residual_fraction and gap <= policy.maximum_attachment_gap_fraction and size <= policy.maximum_fragment_area_fraction:
                compatible.append({'group_id': group['group_id'], 'bend_residual_95_fraction': q95,
                                   'attachment_gap_fraction': gap, 'surface_area_fraction': size})
        eligible = len(supported)>=policy.minimum_views and not contrary and separation>=policy.minimum_camera_separation_degrees and len(compatible)==1
        item = {'component_id': int(cid), 'supporting_views': supported, 'contrary_views': contrary,
                'camera_separation_degrees': separation, 'geometry_tests':geometry_tests,
                'compatible_groups': compatible, 'eligible': eligible}
        diagnostics.append(item)
        if eligible:
            proposals.append({'component_id':int(cid), **compatible[0], 'supporting_views':supported})
    promoted = [r['component_id'] for r in proposals]
    for row, (_, owner, core) in zip(rows,captured):
        row['opaque_core_pixels_after_proposal'] = int(np.count_nonzero(core & (owner>=0) & ~np.isin(owner,members+promoted)))
    return {'schema_version':1, 'method':'visible_aperture_surface_continuity_v1', 'accepted':False,
            'status':'repair_hypothesis_proposed' if proposals else 'no_bounded_repair', 'policy':asdict(policy),
            'assignments':proposals, 'components':diagnostics, 'views':rows,
            'limitations':['Photo apertures and fitted cameras are uncertain; these are conditional membership proposals.',
                          'A fused frame/lens component stays unresolved; no whole-primitive promotion from partial evidence.',
                          'Rear hardware, hidden and single-view fragments cannot be recovered by this rule.',
                          'Residual opaque core support may be frame, camera error or missing optical membership.']}
