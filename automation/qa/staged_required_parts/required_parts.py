"""Staged photographic part-extent measurement; production files stay frozen.

Names and mesh existence are never positive photographic evidence. The built-in
support producer replays authored object alpha. Other image-only producers need
an explicit replay adapter; a caller-supplied mask or class label is insufficient.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree

from reconstruction.camera import Camera, project
from reconstruction.mesh import TriangleMesh, load_glb_bytes
from reconstruction.raster import rasterize
from reconstruction.view_scene import (PartBinding, ViewState, mesh_geometry_sha256,
                                      pose_scene, read_view_scene_contract)


METHOD = 'source_bound_photographic_part_extent_v1'
ROLES = ('front', 'left_temple', 'right_temple')


@dataclass(frozen=True)
class RequiredPartsPolicy:
    extent_bins: int = 6
    minimum_supported_bins: int = 5
    minimum_points_per_bin: int = 2
    minimum_useful_bins_per_view: int = 3
    minimum_useful_views: int = 2
    minimum_view_separation_degrees: float = 12.
    maximum_edge_distance_width_fraction: float = .015
    raster_floor_px: float = .75
    maximum_normal_angle_degrees: float = 35.
    minimum_contradiction_pixels: int = 12
    maximum_known_background_fraction: float = .20


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def _pin(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': _sha(path.read_bytes())}


def _read(reference):
    raw = Path(reference['path']).read_bytes()
    if _sha(raw) != reference['sha256']:
        raise ValueError('Pinned required-parts input changed')
    return json.loads(raw)


def object_support_arrays(membership):
    """Deterministic coordinate API: membership, edge pixels/normals and chain IDs.

    This helper measures an already supported domain; it does not promote a
    segmentation label to truth. Values: 0 unknown, 1 supported object, 2 known
    exterior. Public producers must establish and replay the domain separately.
    """
    membership = np.asarray(membership)
    if membership.ndim != 2 or not np.isin(membership, [0, 1, 2]).all():
        raise ValueError('Object support requires a tri-state image grid')
    mask = membership == 1
    boundary = mask & ~ndimage.binary_erosion(mask)
    boundary[[0, -1], :] = False
    boundary[:, [0, -1]] = False
    signed = ndimage.distance_transform_edt(~mask) - ndimage.distance_transform_edt(mask)
    gy, gx = np.gradient(ndimage.gaussian_filter(signed, .8))
    y, x = np.nonzero(boundary)
    normals = np.column_stack([gx[y, x], gy[y, x]])
    length = np.linalg.norm(normals, axis=1)
    keep = length > 1e-8
    y, x, normals, length = y[keep], x[keep], normals[keep], length[keep]
    chains, _ = ndimage.label(boundary, np.ones((3, 3)))
    return {'membership': membership.astype(np.uint8), 'edge_xy': np.column_stack([x, y]).astype(np.int32),
            'edge_normals': normals / length[:, None], 'edge_chain_ids': chains[y, x]}


def _alpha_arrays(raw, image_size=None):
    image = Image.open(io.BytesIO(raw))
    if 'A' not in image.getbands() and 'transparency' not in image.info:
        raise ValueError('Photo has no authored alpha support; RGB contrast alone is not object identity')
    image = image.convert('RGBA')
    pixel_sha = _sha(np.asarray(image).tobytes())
    if image_size is not None:
        if len(image_size) != 2 or any(type(x) is not int or x < 2 for x in image_size):
            raise ValueError('Object-support working image dimensions are invalid')
        image = image.resize(tuple(image_size), Image.Resampling.LANCZOS)
    rgba = np.asarray(image)
    alpha = rgba[:, :, 3]
    if not np.any(alpha == 0) or not np.any(alpha == 255):
        raise ValueError('Authored alpha needs both object and exterior pixels')
    membership = np.zeros(alpha.shape, np.uint8)
    membership[alpha == 255] = 1
    # Anti-alias/transparent object pixels and a 2px exterior guard stay unknown.
    membership[ndimage.binary_erosion(alpha == 0, iterations=2, border_value=1)] = 2
    arrays = object_support_arrays(membership)
    arrays['decoded_pixel_sha256'] = pixel_sha
    return arrays


def build_alpha_object_support(photo, output, *, image_size=None):
    """Replayable image-only object support, never optical opacity/transmission."""
    path, output = Path(photo).resolve(), Path(output).resolve()
    raw = path.read_bytes()
    arrays = _alpha_arrays(raw, image_size)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Object support output must be fresh')
    output.mkdir(parents=True, exist_ok=True)
    pixel_sha = arrays.pop('decoded_pixel_sha256')
    np.savez_compressed(output / 'support.npz', **arrays)
    report = {'method': 'authored_alpha_object_support_v1', 'photo': _pin(path),
              'decoded_pixel_sha256': pixel_sha, 'arrays': _pin(output / 'support.npz'),
              'image_size': image_size,
              'scope': 'Authored object support; no frame identity or optical opacity claim.'}
    (output / 'report.json').write_bytes(_json(report))
    return _pin(output / 'report.json')


def _region_arrays(region_reference, photo_id, image_size):
    from reconstruction.frame_image_support import build_frame_image_support
    region = _read(region_reference)
    rows = [p for p in region['photos'] if p['id'] == photo_id]
    if len(rows) != 1:
        raise ValueError('Object-support photograph is missing or duplicated in the region report')
    pins = {}
    built = build_frame_image_support(rows[0], Path(region_reference['path']).parent, pins=pins)
    membership = built['membership']
    if image_size is not None:
        if len(image_size) != 2 or any(type(x) is not int or x < 2 for x in image_size):
            raise ValueError('Object-support working image dimensions are invalid')
        # Nearest native pixel preserves tri-state identity rather than inventing
        # known membership by averaging an unknown neighborhood.
        membership = np.asarray(Image.fromarray(membership).resize(tuple(image_size), Image.Resampling.NEAREST))
    arrays = object_support_arrays(membership)
    if any(_sha(Path(path).read_bytes()) != digest for path, digest in pins.items()):
        raise ValueError('Image-only object-support source changed during replay')
    _read(region_reference)
    return arrays, built['report'], pins


def build_region_object_support(region_reference, photo_id, output, *, image_size=None):
    """JPEG adapter: all image-only prompt alternatives plus source contrast.

    It never reads candidate-projected positive masks or uses detector labels as
    proof. Candidate optical masks may only remove uncertain foreground support.
    Low-contrast exterior stays unknown; common segmentation errors remain a
    disclosed limitation of this conditional visible-extent measurement.
    """
    arrays, basis, pins = _region_arrays(region_reference, photo_id, image_size)
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Object support output must be fresh')
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / 'support.npz', **arrays)
    report = {'method': 'region_consensus_object_support_v1', 'region_reference': region_reference,
        'photo_id': photo_id, 'photo': {'path': basis['source'], 'sha256': basis['source_sha256']},
        'decoded_pixel_sha256': basis['decoded_rgba_sha256'], 'image_size': image_size,
        'arrays': _pin(output / 'support.npz'), 'basis': basis, 'source_pins': pins,
        'scope': 'Conditional independent image foreground support; segmentation and camera can remain wrong.'}
    (output / 'report.json').write_bytes(_json(report))
    return _pin(output / 'report.json')


def read_object_support(reference):
    report = _read(reference)
    if report.get('method') == 'region_consensus_object_support_v1':
        expected, basis, pins = _region_arrays(report['region_reference'], report['photo_id'], report['image_size'])
        if (report['photo'] != {'path': basis['source'], 'sha256': basis['source_sha256']}
                or report['decoded_pixel_sha256'] != basis['decoded_rgba_sha256'] or report['source_pins'] != pins):
            raise ValueError('Object-support region/photo lineage changed')
    elif report.get('method') == 'authored_alpha_object_support_v1':
        raw = Path(report['photo']['path']).read_bytes()
        if _sha(raw) != report['photo']['sha256']:
            raise ValueError('Object-support photo changed')
        expected = _alpha_arrays(raw, report.get('image_size'))
        if expected.pop('decoded_pixel_sha256') != report['decoded_pixel_sha256']:
            raise ValueError('Object-support decoded pixels changed')
    else:
        raise ValueError('Object-support producer needs an explicit replay adapter')
    path = Path(report['arrays']['path'])
    if _sha(path.read_bytes()) != report['arrays']['sha256']:
        raise ValueError('Object-support array bytes changed')
    with np.load(path, allow_pickle=False) as actual:
        if set(actual.files) != set(expected) or any(not np.array_equal(actual[k], expected[k]) for k in expected):
            raise ValueError('Object-support arrays do not replay from source pixels')
    return expected, report


def _triangle_key(triangle):
    order = np.lexsort(np.asarray(triangle).T[::-1])
    return np.ascontiguousarray(np.asarray(triangle)[order], dtype='<f8').tobytes()


def exact_final_role_binding(source, binding, final):
    """Exact corners, including multiplicity; never closest-surface labels.

    An exact final subset permits verified removed interior walls. Coincident
    conflicting source roles and copied final triangles are explicitly rejected.
    New/LOD triangles need a different verified correspondence producer.
    """
    binding.validate_scene(source)
    source_rows = defaultdict(list)
    for i, triangle in enumerate(source.vertices[source.faces]):
        source_rows[_triangle_key(triangle)].append(i)
    used = Counter()
    roles, source_ids = [], []
    for triangle in final.vertices[final.faces]:
        key = _triangle_key(triangle)
        ids = source_rows.get(key, [])
        role_set = {binding.face_roles[i] for i in ids}
        if not ids:
            return None, {'status': 'unmeasured', 'reason': 'final_triangle_has_no_exact_source_correspondence'}
        if len(role_set) != 1:
            return None, {'status': 'unmeasured', 'reason': 'coincident_source_triangles_have_conflicting_roles'}
        used[key] += 1
        if used[key] > len(ids):
            return None, {'status': 'unmeasured', 'reason': 'duplicated_final_triangle_exceeds_source_multiplicity'}
        roles.append(next(iter(role_set))); source_ids.append(ids[used[key] - 1])
    hinges = tuple(h for h in binding.hinges if h.side + '_temple' in roles)
    result = PartBinding(mesh_geometry_sha256(final), tuple(roles), hinges,
                         binding.provenance + '; exact final-corner measurement binding')
    return result, {'status': 'bound', 'method': 'exact_corner_multiset_subset_v1',
                    'source_face_ids': source_ids, 'source_faces': len(source.faces), 'final_faces': len(final.faces)}


def _extent_values(mesh, binding, source, source_binding):
    """Source-fixed hinge radial extent for the accepted unfolded-arm prior.

    Unlike candidate-relative normalization, deleting a tip cannot turn a short
    surviving arm into full extent. Curled/folded initializers remain outside the
    automatic binding's supported manufacturing prior.
    """
    values = np.zeros((len(mesh.faces), 3))
    source_roles, roles = np.asarray(source_binding.face_roles), np.asarray(binding.face_roles)
    for role in ROLES:
        target = roles == role
        ref = source.vertices[source.faces[source_roles == role]]
        if not len(ref) or not target.any():
            continue
        p = mesh.vertices[mesh.faces[target]]
        if role == 'front':
            lo, hi = ref[:, :, 0].min(), ref[:, :, 0].max()
            values[target] = (p[:, :, 0] - lo) / max(hi - lo, 1e-12)
        else:
            hinge = next(h for h in source_binding.hinges if role == h.side + '_temple')
            radius = np.linalg.norm(ref - hinge.origin, axis=2).max()
            values[target] = np.linalg.norm(p - hinge.origin, axis=2) / max(radius, 1e-12)
    return np.clip(values, 0., 1.)


def exclusive_edge_matches(xy, normals, role_codes, support, maximum_distance, minimum_normal_dot):
    """One observed edge pixel has at most one owner, including within a role."""
    n = len(xy)
    matched = np.full(n, -1, int)
    residual = np.full(n, np.inf)
    if not n or not len(support['edge_xy']):
        return matched, residual
    tree = cKDTree(support['edge_xy'])
    distance, index = tree.query(xy, k=min(4, len(support['edge_xy'])), distance_upper_bound=maximum_distance)
    if distance.ndim == 1:
        distance, index = distance[:, None], index[:, None]
    options = []
    for i, col in zip(*np.nonzero(np.isfinite(distance))):
        j = int(index[i, col])
        # Signed outward normals avoid matching the opposite side of a thin line.
        if normals[i] @ support['edge_normals'][j] >= minimum_normal_dot:
            options.append((float(distance[i, col]), i, j))
    owned = set()
    for d, i, j in sorted(options):
        if matched[i] < 0 and j not in owned:
            matched[i], residual[i] = j, d
            owned.add(j)
    return matched, residual


def measure_view_parts(final, binding, source, source_binding, camera, state, support, *,
                       normalization=None, policy=RequiredPartsPolicy()):
    posed = pose_scene(final, binding, ViewState(state.view_id,
        state.left_degrees if any(h.side == 'left' for h in binding.hinges) else 0.,
        state.right_degrees if any(h.side == 'right' for h in binding.hinges) else 0.), compact=True).mesh
    if normalization is not None:
        posed = TriangleMesh((posed.vertices - normalization['center']) / normalization['extent'], posed.faces, posed.parts)
    membership = support['membership']
    raster = rasterize(posed, camera, membership.shape)
    roles = np.asarray(binding.face_roles)
    visible_roles = np.full(membership.shape, '', dtype='<U12')
    visible_roles[raster.mask] = roles[raster.face_index[raster.mask]]
    # The whole scene supplies first-hit visibility. Internal role-contact edges
    # and obscured arms cannot masquerade as independently visible contours.
    boundary = raster.mask & ~ndimage.binary_erosion(raster.mask)
    boundary[[0, -1], :] = False; boundary[:, [0, -1]] = False
    y, x = np.nonzero(boundary & np.isin(visible_roles, ROLES))
    face = raster.face_index[y, x]
    bary = raster.barycentric[y, x]
    corner_extent = _extent_values(final, binding, source, source_binding)
    extent = np.sum(corner_extent[face] * bary, axis=1)
    bins = np.minimum(policy.extent_bins - 1, (extent * policy.extent_bins).astype(int))
    signed = ndimage.distance_transform_edt(~raster.mask) - ndimage.distance_transform_edt(raster.mask)
    gy, gx = np.gradient(ndimage.gaussian_filter(signed, .8))
    normals = np.column_stack([gx[y, x], gy[y, x]])
    normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-12)
    width = max(1., min(float(membership.shape[1]), float(np.ptp(project(posed.vertices, camera)[:, 0]))))
    distance_limit = max(policy.raster_floor_px, width * policy.maximum_edge_distance_width_fraction)
    matched, residual = exclusive_edge_matches(np.column_stack([x, y]), normals, roles[face], support,
        distance_limit, np.cos(np.radians(policy.maximum_normal_angle_degrees)))
    # The independently supported interior may be eroded. Permit its nearby
    # boundary as a conditional contour witness, but never credit a known
    # exterior projection or an edge whose photographed interior is unknown.
    observed_membership = np.zeros(len(face), np.uint8)
    eligible = matched >= 0
    if eligible.any():
        observed_xy = support['edge_xy'][matched[eligible]]
        observed_membership[eligible] = membership[observed_xy[:, 1], observed_xy[:, 0]]
    positive = eligible & (membership[y, x] != 2) & (observed_membership == 1)
    assigned = np.where(positive, matched, -1)
    measured_bins = {}
    for role in ROLES:
        selected = roles[face] == role
        counts = np.bincount(bins[selected & positive], minlength=policy.extent_bins)
        interior = ndimage.binary_erosion(visible_roles == role, iterations=2)
        known = interior & (membership != 0)
        contrary = interior & (membership == 2)
        fraction = float(contrary.sum() / known.sum()) if known.any() else None
        contradiction = bool(contrary.sum() >= policy.minimum_contradiction_pixels and
                             fraction > policy.maximum_known_background_fraction)
        measured_bins[role] = {'supported_bins': np.flatnonzero(counts >= policy.minimum_points_per_bin).tolist(),
            'matched_edge_pixels': int((selected & positive).sum()), 'visible_boundary_pixels': int(selected.sum()),
            'known_interior_pixels': int(known.sum()), 'known_background_pixels': int(contrary.sum()),
            'known_background_fraction': fraction, 'contradicted': contradiction}
    arrays = {'final_face_ids': face, 'barycentric': bary, 'projected_xy': np.column_stack([x, y]),
              'role': roles[face], 'source_extent': extent, 'extent_bin': bins,
              'matched_edge_ids': assigned, 'edge_residual_px': np.where(positive, residual, -1.),
              'membership': membership[y, x], 'observed_membership': observed_membership,
              'visible_first_hit': np.ones(len(face), bool)}
    observed = np.full((len(face), 2), -1, dtype=np.int32)
    chains = np.full(len(face), -1, dtype=np.int32)
    observed[positive] = support['edge_xy'][assigned[positive]]
    chains[positive] = support['edge_chain_ids'][assigned[positive]]
    arrays.update(observed_xy=observed, observed_chain_ids=chains)
    return {'roles': measured_bins, 'distance_limit_px': distance_limit, 'camera': camera.to_dict()}, arrays


def _view_direction(camera):
    yaw, pitch = np.radians([camera['yaw'], camera['pitch']])
    return np.array([np.cos(pitch) * np.sin(yaw), np.sin(pitch), np.cos(pitch) * np.cos(yaw)])


def aggregate_required_parts(binding, records, *, policy=RequiredPartsPolicy()):
    result = {}
    for role in ROLES:
        if role not in binding.face_roles:
            result[role] = {'status': 'absent', 'reason': 'required_source_role_missing_from_final_geometry'}
            continue
        evidence = [r for r in records if r['roles'][role]['supported_bins']]
        if any(r['roles'][role]['contradicted'] for r in records):
            result[role] = {'status': 'absent', 'reason': 'visible_final_role_projects_onto_known_exterior'}
            continue
        useful = [r for r in evidence if len(r['roles'][role]['supported_bins']) >= policy.minimum_useful_bins_per_view]
        diverse = any(np.degrees(np.arccos(np.clip(_view_direction(a['camera']) @ _view_direction(b['camera']), -1., 1.)))
                      >= policy.minimum_view_separation_degrees for i, a in enumerate(useful) for b in useful[i+1:])
        bins = sorted({b for r in useful for b in r['roles'][role]['supported_bins']})
        present = bool(len(useful) >= policy.minimum_useful_views and diverse and len(bins) >= policy.minimum_supported_bins
                       and 0 in bins and policy.extent_bins - 1 in bins)
        result[role] = {'status': 'present' if present else 'unmeasured', 'supported_bins': bins,
                        'useful_views': len(useful), 'angularly_separated': bool(diverse),
                        'reason': 'distinct_photo_edges_support_source_fixed_extent' if present else 'insufficient_unambiguous_visible_extent'}
    statuses = [r['status'] for r in result.values()]
    return {'roles': result, 'required_parts_present': False if 'absent' in statuses else True if all(s == 'present' for s in statuses) else None}


def measure_required_parts(final_model, refinement_reference, photo_support_references, output=None, *,
                           policy=RequiredPartsPolicy()):
    """Source-bound recipe; caller must separately trust the job refinement ref.

    No nuisance camera, articulation, shape or thresholds are fitted here. The
    image support is replayed from photos independently of the candidate.
    """
    final_model = Path(final_model).resolve()
    raw = final_model.read_bytes(); final = load_glb_bytes(raw)
    refinement = _read(refinement_reference)
    source, source_binding, _ = read_view_scene_contract(refinement['view_scene'])
    binding, correspondence = exact_final_role_binding(source, source_binding, final)
    result = {'schema_version': 1, 'method': METHOD, 'candidate_sha256': _sha(raw),
        'refinement_reference': refinement_reference, 'photo_support_references': photo_support_references,
        'policy': asdict(policy), 'correspondence': correspondence, 'scope':
        'Photographic object-supported part extent; may use reconstruction photos. Not independent held-out accuracy or semantic identity.'}
    if binding is None:
        result.update(required_parts_present=None, roles={r: {'status': 'unmeasured', 'reason': correspondence['reason']} for r in ROLES}, records=[])
        if output is not None:
            output = Path(output).resolve()
            if output.exists() and any(output.iterdir()):
                raise ValueError('Required-parts output must be fresh')
            output.mkdir(parents=True, exist_ok=True)
            (output / 'report.json').write_bytes(_json(result))
        return result
    records, sample_arrays, seen = [], {}, set()
    views = {row['view_id']: row for row in refinement['views']}
    for view_id, reference in photo_support_references.items():
        support, support_report = read_object_support(reference)
        pixel_sha = support_report['decoded_pixel_sha256']
        if pixel_sha in seen:
            raise ValueError('Duplicated photo pixels cannot count as different part views')
        seen.add(pixel_sha)
        _, _, state = read_view_scene_contract(refinement['view_scene'], view_id=view_id,
                                               photo_sha256=support_report['photo']['sha256'])
        view = views[view_id]
        camera = Camera(**view['camera_fit']['camera'])
        # Native support coordinates must exactly match the frozen camera grid.
        expected_size = view.get('image_size_working')
        if expected_size is None or tuple(expected_size[::-1]) != support['membership'].shape:
            raise ValueError('Camera and image-support coordinate grids differ')
        measured, arrays = measure_view_parts(final, binding, source, source_binding, camera, state, support,
            normalization=refinement.get('normalization'), policy=policy)
        for side, ambiguity in view.get('articulation_evidence', {}).items():
            if ambiguity.get('status') == 'camera_or_arm_ambiguous':
                role = side + '_temple'
                if role in measured['roles']:
                    measured['roles'][role].update(supported_bins=[], contradicted=False,
                        excluded_reason='frozen_camera_or_arm_pose_is_ambiguous')
        measured.update(view_id=view_id, photo_sha256=support_report['photo']['sha256'],
                        decoded_pixel_sha256=pixel_sha, support_reference=reference)
        records.append(measured); sample_arrays[view_id] = arrays
    result.update(aggregate_required_parts(binding, records, policy=policy), records=records)
    if _sha(final_model.read_bytes()) != result['candidate_sha256']:
        raise ValueError('Final candidate changed during required-parts measurement')
    _read(refinement_reference)
    if output is not None:
        output = Path(output).resolve()
        if output.exists() and any(output.iterdir()):
            raise ValueError('Required-parts output must be fresh')
        output.mkdir(parents=True, exist_ok=True)
        for view_id, arrays in sample_arrays.items():
            path = output / (view_id + '.npz'); np.savez_compressed(path, **arrays)
            next(r for r in records if r['view_id'] == view_id)['samples'] = _pin(path)
        (output / 'report.json').write_bytes(_json(result))
    return result


def replay_required_parts(final_model, receipt_reference, trusted_refinement_reference, *, trusted_region_references=()):
    receipt = _read(receipt_reference)
    if receipt.get('method') != METHOD or receipt.get('candidate_sha256') != _sha(Path(final_model).read_bytes()):
        raise ValueError('Required-parts receipt method or candidate mismatch')
    if receipt.get('refinement_reference') != trusted_refinement_reference:
        raise ValueError('Required-parts receipt does not use current-job trusted role/camera history')
    if receipt.get('policy') != asdict(RequiredPartsPolicy()):
        raise ValueError('Unknown required-parts measurement policy')
    for reference in receipt['photo_support_references'].values():
        support = _read(reference)
        if (support.get('method') == 'region_consensus_object_support_v1'
                and support.get('region_reference') not in trusted_region_references):
            raise ValueError('Part support does not use a trusted current-job region receipt')
    for record in receipt.get('records', []):
        if record.get('samples') and _sha(Path(record['samples']['path']).read_bytes()) != record['samples']['sha256']:
            raise ValueError('Required-parts samples changed')
    return measure_required_parts(final_model, trusted_refinement_reference, receipt['photo_support_references'])
