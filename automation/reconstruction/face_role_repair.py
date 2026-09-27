"""Visibility-bound face proposals for fused frame/lens source components.

Independent aperture consensus determines the eligible domain before geometry
is considered. No texture color, provider part name or fit residual is a label.
Every source face remains present in exactly one partition piece.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib

import numpy as np
from scipy.ndimage import binary_erosion
from scipy.spatial import cKDTree

from .camera import Camera, project
from .mesh_components import component_face_labels
from .optical_membership import _bend
from .raster import rasterize


@dataclass(frozen=True)
class FaceRolePolicy:
    minimum_views: int = 2
    minimum_separation_degrees: float = 5.
    erosion_pixels: int = 2
    visibility_depth_fraction: float = .004
    maximum_bend_residual_fraction: float = .018
    minimum_normal_alignment: float = .80
    maximum_attachment_gap_fraction: float = .012
    maximum_patch_area_fraction: float = .70
    minimum_patch_faces: int = 4


def propose_face_roles(mesh, face_components, groups, views, *, visibility_mesh=None,
                       policy=FaceRolePolicy()):
    labels = np.asarray(face_components, np.int64)
    if labels.shape != (len(mesh.faces),) or np.any(labels < 0):
        raise ValueError('Complete source face labels are required')
    group_members = [m for g in groups for m in g['members']]
    if len(group_members) != len(set(group_members)):
        raise ValueError('Optical groups must be disjoint')
    declared = np.isin(labels, group_members)
    triangles = mesh.vertices[mesh.faces]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    twice_area = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(twice_area[:, None], 1e-20)
    eligible_ids = np.flatnonzero(~declared & (twice_area > 1e-16))
    if visibility_mesh is not None:
        if len(visibility_mesh.faces) != len(mesh.faces):
            raise ValueError('Visibility faces must preserve source face ordinals')
        live = visibility_mesh.faces[eligible_ids]
        eligible_ids = eligible_ids[(live[:, 0] != live[:, 1]) & (live[:, 1] != live[:, 2])]
    weights = np.array([[1/3, 1/3, 1/3], [.6, .2, .2], [.2, .6, .2], [.2, .2, .6]])
    samples = np.einsum('sc,fcd->fsd', weights, triangles[eligible_ids])
    support = np.zeros((len(eligible_ids), len(views)), bool)
    contrary = np.zeros(len(eligible_ids), bool)
    forward, records = [], []
    span = max(float(np.ptp(mesh.vertices, axis=0).max()), 1e-10)
    for ordinal, view in enumerate(views):
        camera = view['camera'] if isinstance(view['camera'], Camera) else Camera(**view['camera'])
        shape = tuple(view['shape'])
        masks = [np.asarray(i['mask'], bool) for i in view['interpretations']]
        domains = [np.asarray(i['known_domain'], bool) for i in view['interpretations']]
        if not masks or any(m.shape != shape or k.shape != shape or np.any(m & ~k) for m,k in zip(masks, domains)):
            raise ValueError('Independent aperture masks require matching known domains')
        core = binary_erosion(np.logical_and.reduce(masks), iterations=policy.erosion_pixels)
        known = np.logical_and.reduce(domains)
        outside = known & ~np.logical_or.reduce(masks)
        posed = view.get('mesh', visibility_mesh or mesh)
        # Posed meshes must retain source face ordinals; samples follow them.
        sample_points = (np.einsum('sc,fcd->fsd', weights, posed.vertices[posed.faces[eligible_ids]])
                         if 'mesh' in view else samples)
        raster = rasterize(posed, camera, shape)
        xy = project(sample_points.reshape(-1, 3), camera).reshape(len(samples), 4, 2)
        x, y = np.floor(xy[..., 0]).astype(int), np.floor(xy[..., 1]).astype(int)
        in_grid = (x >= 0) & (x < shape[1]) & (y >= 0) & (y < shape[0])
        x = np.clip(x, 0, shape[1]-1); y = np.clip(y, 0, shape[0]-1)
        yaw, pitch = np.radians([camera.yaw, camera.pitch])
        toward = np.array([np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)])
        depth = -(sample_points @ toward)
        visible = in_grid & raster.mask[y, x] & (np.abs(depth-raster.depth[y, x]) <= span*policy.visibility_depth_fraction)
        votes = np.count_nonzero(visible & core[y, x], axis=1)
        support[:, ordinal] = votes >= 3
        contrary |= np.any(visible & outside[y, x], axis=1)
        forward.append(toward)
        records.append({'view_id': view['id'], 'source_sha256': view.get('source_sha256'),
                        'supported_faces': int(np.count_nonzero(votes >= 3)),
                        'core_pixels': int(core.sum()), 'core_sha256': hashlib.sha256(core.tobytes()).hexdigest()})
    diverse = np.zeros(len(samples), bool)
    for a in range(len(forward)):
        for b in range(a):
            separation = float(np.degrees(np.arccos(np.clip(forward[a] @ forward[b], -1, 1))))
            if separation >= policy.minimum_separation_degrees:
                diverse |= support[:, a] & support[:, b]
    measured = (support.sum(axis=1) >= policy.minimum_views) & diverse & ~contrary
    proposals, ownership = [], np.full(len(mesh.faces), -1, np.int32)
    centroids = triangles.mean(axis=1)
    candidates = []
    for ordinal, group in enumerate(groups):
        gf = np.isin(labels, group['members'])
        gv = mesh.vertices[np.unique(mesh.faces[gf])]
        if len(gv) < 6 or np.ptp(gv, axis=0).max() <= 0:
            continue
        center, scale, design, coefficient = _bend(gv)
        points = centroids[eligible_ids]
        residual = np.abs((points[:, 2]-center[2])/scale-design(points) @ coefficient)
        x, y = ((points-center)/scale)[:, :2].T
        dx = coefficient[1]+2*coefficient[3]*x+coefficient[4]*y
        dy = coefficient[2]+coefficient[4]*x+2*coefficient[5]*y
        expected = np.column_stack((-dx, -dy, np.ones(len(x))))
        expected /= np.linalg.norm(expected, axis=1)[:, None]
        alignment = np.abs(np.sum(expected*normals[eligible_ids], axis=1))
        selected = eligible_ids[measured & (residual <= policy.maximum_bend_residual_fraction) &
                                (alignment >= policy.minimum_normal_alignment)]
        if not len(selected):
            continue
        clusters = component_face_labels(mesh.vertices, mesh.faces[selected], connectivity='exact_position_vertex')
        tree = cKDTree(gv)
        for cluster in np.unique(clusters):
            ids = selected[clusters == cluster]
            gap = float(tree.query(mesh.vertices[np.unique(mesh.faces[ids])], k=1)[0].min()/scale)
            area = float(twice_area[ids].sum()/max(twice_area[gf].sum(), 1e-20))
            if (len(ids) >= policy.minimum_patch_faces and gap <= policy.maximum_attachment_gap_fraction and
                    area <= policy.maximum_patch_area_fraction):
                candidates.append((ordinal, ids, gap, area))
                ownership[ids] = np.where(ownership[ids] == -1, ordinal, -2)
    for ordinal, ids, gap, area in candidates:
        # An ambiguous face cannot be assigned to either lens by list order.
        ids = ids[ownership[ids] == ordinal]
        if len(ids) < policy.minimum_patch_faces:
            continue
        proposals.append({'group_id': groups[ordinal]['group_id'], 'source_face_indices': ids.tolist(),
                          'source_components': np.unique(labels[ids]).tolist(),
                          'attachment_gap_fraction': gap, 'patch_area_fraction': area})
    return {'schema_version': 1, 'method': 'independent_multiview_face_roles_v1', 'policy': asdict(policy),
            'accepted': False, 'status': 'face_partitions_proposed' if proposals else 'no_supported_face_split',
            'assignments': proposals, 'views': records, 'eligible_face_count': int(measured.sum()),
            'contrary_face_count': int(contrary.sum()),
            'limitations': ['Unobserved faces retain their source role.',
                           'Independent aperture masks and fitted cameras remain hypotheses.',
                           'This repairs membership, not absent geometry or source shape.']}


def split_component_ledger(scene, hypothesis, assignments):
    """Materialize virtual components without changing any triangle or attribute."""
    result = dict(scene)
    result['face_components'] = np.array(scene['face_components'], copy=True)
    result['component_table'] = deepcopy(scene['component_table'])
    result['primitive_labels'] = {key: np.array(value, copy=True) for key,value in scene['primitive_labels'].items()}
    revised = deepcopy(hypothesis)
    groups = {g['group_id']: g for g in revised['groups']}
    used = set()
    for assignment in assignments:
        gid = assignment['group_id']
        if gid not in groups:
            raise ValueError('Face assignment names an unknown group')
        ids = np.asarray(assignment['source_face_indices'], np.int64)
        if ids.ndim != 1 or np.any(ids < 0) or np.any(ids >= len(scene['face_components'])) or len(ids) != len(np.unique(ids)):
            raise ValueError('Invalid source face assignment')
        if used.intersection(ids.tolist()):
            raise ValueError('Face assignments must be disjoint')
        used.update(ids.tolist())
        for cid in np.unique(scene['face_components'][ids]):
            subset = ids[scene['face_components'][ids] == cid]
            row = next(r for r in result['component_table'] if r['component_id'] == cid)
            if len(subset) == row['source_face_count']:
                # Complete components already have an existing identity.
                for group in revised['groups']:
                    group['members'] = [m for m in group['members'] if m != int(cid)]
                groups[gid]['members'].append(int(cid))
                continue
            binding = tuple(row['source_binding'][k] for k in ('node_index','mesh_index','primitive_index'))
            local_labels = result['primitive_labels'][binding]
            local_id = int(local_labels.max())+1
            new_id = max(r['component_id'] for r in result['component_table'])+1
            offset = row['source_global_face_offset']
            local_labels[subset-offset] = local_id
            result['face_components'][subset] = new_id
            row['source_face_count'] -= len(subset)
            result['component_table'].append({**deepcopy(row), 'component_id': new_id,
                'local_component_id': local_id, 'source_face_count': len(subset), 'split_from_component': int(cid)})
            groups[gid]['members'].append(new_id)
    revised['groups'] = [g for g in revised['groups'] if g['members']]
    for group in revised['groups']:
        group['source_face_count'] = sum(r['source_face_count'] for r in result['component_table'] if r['component_id'] in group['members'])
    revised['group_count'] = len(revised['groups'])
    return result, revised
