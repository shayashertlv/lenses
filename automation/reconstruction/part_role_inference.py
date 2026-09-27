"""Source-bound optical-part hypotheses from calibrated rendered-mask evidence.

No names, material colors, brands or fixed part indices are used. Visible votes
propose seed parts; shared cut boundaries and surface continuity identify small
edge-fragment alternatives. Projected rear hardware alone is never promoted.
Hypotheses remain reviewable and do not establish hidden-surface lens identity.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from io import BytesIO
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree

from .camera import Camera
from .deform_glb import _read_bytes
from .mesh import TriangleMesh, load_glb_bytes
from .optical_group_asset import _source_matrices
from .raster import rasterize


@dataclass(frozen=True)
class RolePolicy:
    minimum_visible_pixels: int = 50
    minimum_mask_hits: int = 100
    seed_minimum_view_purity: float = .8
    maximum_dropout_view_purity: float = .02
    minimum_dropout_support_views: int = 2
    minimum_distinct_view_angle_degrees: float = 12.
    maximum_fragment_area_ratio: float = .20
    minimum_fragment_visible_pixels: int = 15
    minimum_fragment_mask_hits: int = 5
    maximum_boundary_band_fraction: float = .025
    maximum_fragment_outside_band_fraction: float = .1
    boundary_contact_relative_tolerance: float = 1e-5
    minimum_shared_boundary_fraction: float = .05
    minimum_contact_normal_cosine: float = .85
    maximum_contact_fold_fraction: float = .2
    maximum_fragment_plane_distance_relative: float = .035
    minimum_silhouette_iou: float = .97
    maximum_hypotheses: int = 8
    maximum_views: int = 24
    maximum_parts: int = 256
    maximum_faces: int = 5_000_000
    maximum_image_pixels: int = 4_000_000


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def _read_pinned(item):
    path = Path(item['path']).resolve()
    raw = path.read_bytes()
    if _sha(raw) != item['sha256']:
        raise ValueError(f'Bound input changed: {path.name}')
    return raw


def _write_immutable(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f'Immutable inference output changed: {path.name}')
    else:
        with path.open('xb') as stream:
            stream.write(raw)
    return dict(path=str(path.resolve()), sha256=_sha(raw))


def _policy(value):
    policy = value if isinstance(value, RolePolicy) else RolePolicy(**(value or {}))
    for key, number in asdict(policy).items():
        if not isinstance(number, (int, float)) or not np.isfinite(number) or number <= 0:
            raise ValueError(f'Invalid inference policy {key}')
        if any(s in key for s in ('fraction', 'purity', 'cosine', 'iou')) and number > 1:
            raise ValueError(f'Invalid inference probability/fraction {key}')
    for key in ('maximum_hypotheses', 'maximum_views', 'maximum_parts', 'maximum_faces',
                'maximum_image_pixels', 'minimum_visible_pixels', 'minimum_mask_hits',
                'minimum_fragment_visible_pixels', 'minimum_fragment_mask_hits', 'minimum_dropout_support_views'):
        if type(getattr(policy, key)) is not int:
            raise ValueError(f'Integer policy required: {key}')
    if policy.boundary_contact_relative_tolerance > 1e-3:
        raise ValueError('Boundary contact tolerance cannot bridge substantial geometry')
    if policy.maximum_dropout_view_purity >= policy.seed_minimum_view_purity or policy.minimum_distinct_view_angle_degrees > 180:
        raise ValueError('Invalid detector dropout policy')
    return policy


def _binary_image(item, shape, maximum_pixels):
    raw = _read_pinned(item)
    with Image.open(BytesIO(raw)) as image:
        if image.width*image.height > maximum_pixels or (image.height, image.width) != shape:
            raise ValueError('Mask dimensions differ from declared render')
        return np.asarray(image.convert('L')) >= 128


def _validate_opaque_scene(raw, mesh):
    _, document, _ = _read_bytes(raw)
    matrices = _source_matrices(document)
    for part in mesh.parts:
        if np.linalg.det(matrices[part['node_index']][:3, :3]) <= 0:
            raise ValueError('Bake mirrored/singular scene transforms before calibrated role inference')
        material = document.get('materials', [])[part['material_index']] if part['material_index'] is not None else {}
        if (material.get('alphaMode', 'OPAQUE') != 'OPAQUE' or part['transmission'] > 0 or
                part['has_lens_appearance_extension']):
            raise ValueError('Role inference requires an opaque source render; transparent/masked depth is not simulated')


def verify_geometry_correspondence(candidate, candidate_sha, declaration):
    """Verify each corner, cyclic winding and a complete bijection before transfer.

    A cached claim of geometric similarity is insufficient. External transfer
    only permits small serialization changes, never arbitrary nearest surfaces.
    """
    source_raw = _read_pinned(declaration['source_model'])
    source = load_glb_bytes(source_raw)
    mapping = np.load(BytesIO(_read_pinned(declaration['candidate_to_source_faces'])), allow_pickle=False)
    count = len(candidate.faces)
    if (mapping.shape != (count,) or mapping.dtype.kind not in 'iu' or
            len(source.faces) != count or np.any(mapping < 0) or np.any(mapping >= count) or
            len(np.unique(mapping)) != count):
        raise ValueError('Geometry transfer requires a complete unique face bijection')
    relative = declaration.get('max_relative_corner_error', 1e-6)
    if not isinstance(relative, (float, int)) or not 0 < relative <= 1e-6:
        raise ValueError('Geometry transfer tolerance exceeds serialization bound')
    extent = float(np.ptp(source.vertices, axis=0).max())
    if extent <= 0:
        raise ValueError('Geometry transfer source has no spatial extent')
    maximum = 0.
    for start in range(0, count, 16_384):
        expected = source.vertices[source.faces[mapping[start:start+16_384]]]
        actual = candidate.vertices[candidate.faces[start:start+16_384]]
        errors = [np.linalg.norm(actual-expected[:, permutation], axis=2).max(axis=1)
                  for permutation in ([0, 1, 2], [1, 2, 0], [2, 0, 1])]
        maximum = max(maximum, float(np.min(errors, axis=0).max(initial=0)))
        if maximum > relative*extent:
            raise ValueError('Geometry transfer changes triangle corners or winding')
    return dict(source_sha256=_sha(source_raw), candidate_sha256=candidate_sha,
                complete_bijection=True, maximum_corner_error_world=maximum,
                maximum_relative_corner_error=maximum/extent,
                method='all_corners_cyclic_winding_unique_bijection', exact=maximum == 0)


def rasterize_view(mesh, camera, shape, world_to_render, *, cull_mode='front'):
    """Recompute original face ordinals directly, preserving pixel-center depth."""
    matrix = np.asarray(world_to_render, float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all() or
            not np.array_equal(matrix[3], [0., 0., 0., 1.]) or np.linalg.det(matrix[:3, :3]) <= 0):
        raise ValueError('Render transform must be a finite, orientation-preserving affine matrix')
    vertices = mesh.vertices @ matrix[:3, :3].T + matrix[:3, 3]
    if cull_mode not in ('front', 'double'):
        raise ValueError('Unknown render culling policy')
    selected = np.ones(len(mesh.faces), bool)
    if cull_mode == 'front':
        yaw, pitch = np.radians([camera.yaw, camera.pitch])
        toward = np.array([np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)])
        for start in range(0, len(mesh.faces), 100_000):
            triangles = vertices[mesh.faces[start:start+100_000]]
            rays = toward if camera.perspective == 0 else toward/camera.perspective-triangles.mean(axis=1)
            normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
            selected[start:start+len(triangles)] = (normals*rays).sum(axis=1) > 0
    ids = np.flatnonzero(selected)
    if not len(ids):
        return np.full(shape, -1, np.int32), np.full(shape, np.inf)
    result = rasterize(TriangleMesh(vertices, mesh.faces[ids], []), camera, shape, max_candidates=250_000)
    visible = result.face_index >= 0
    face_ids = result.face_index.copy()
    face_ids[visible] = ids[face_ids[visible]]
    return face_ids, result.depth


def part_boundary_geometry(mesh, part):
    """Exact coordinate welding removes UV seams only; source mesh is untouched."""
    faces = mesh.faces[part['face_start']:part['face_start']+part['face_count']]
    referenced, remap = np.unique(faces, return_inverse=True)
    vertices, welded = np.unique(mesh.vertices[referenced], axis=0, return_inverse=True)
    local_faces = welded[remap.reshape(-1, 3)]
    triangles = vertices[local_faces]
    crosses = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    weights = np.linalg.norm(crosses, axis=1)
    area = float(weights.sum()/2)
    normals = np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(normals, local_faces[:, corner], crosses)
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-30)
    edges = np.sort(np.concatenate([local_faces[:, [0, 1]], local_faces[:, [1, 2]],
                                   local_faces[:, [2, 0]]]), axis=1)
    edges, multiplicity = np.unique(edges, axis=0, return_counts=True)
    boundary = edges[multiplicity == 1]
    edge_lengths = np.linalg.norm(vertices[boundary[:, 1]]-vertices[boundary[:, 0]], axis=1)
    edge_normals = normals[boundary].sum(axis=1)
    edge_normals /= np.maximum(np.linalg.norm(edge_normals, axis=1)[:, None], 1e-30)
    # Deterministic area-weighted surface samples preserve small geometry by area,
    # rather than allowing tessellation density to decide shape statistics.
    cumulative = np.cumsum(weights)
    samples = np.searchsorted(cumulative, (np.arange(min(4096, len(faces)))+.5)*cumulative[-1]/min(4096, len(faces))) if area else np.zeros(0, int)
    points = triangles[samples].mean(axis=1)
    center = np.average(triangles.mean(axis=1), weights=weights, axis=0) if area else vertices.mean(axis=0)
    _, _, basis = np.linalg.svd(points-center, full_matrices=False) if len(points) >= 3 else (None, None, np.eye(3))
    return dict(area=area, boundary_midpoints=vertices[boundary].mean(axis=1),
                boundary_lengths=edge_lengths, boundary_normals=edge_normals,
                boundary_total_length=float(edge_lengths.sum()), samples=points,
                center=center, plane_normal=basis[-1], bounds=[vertices.min(axis=0), vertices.max(axis=0)])


def boundary_contact(fragment, seed, tolerance, normal_threshold):
    """Measure shared cut edges and local surface continuity, without snapping."""
    if not len(fragment['boundary_midpoints']) or not len(seed['boundary_midpoints']):
        return dict(shared_boundary_fraction=0., shared_length=0., normal_cosine=0., folded_fraction=1.)
    distance, nearest = cKDTree(seed['boundary_midpoints']).query(fragment['boundary_midpoints'], workers=1)
    hit = distance <= tolerance
    length = fragment['boundary_lengths'][hit]
    total = float(length.sum())
    if total <= 0:
        return dict(shared_boundary_fraction=0., shared_length=0., normal_cosine=0., folded_fraction=1.)
    cosine = (fragment['boundary_normals'][hit]*seed['boundary_normals'][nearest[hit]]).sum(axis=1)
    return dict(shared_boundary_fraction=total/max(fragment['boundary_total_length'], 1e-30),
                shared_length=total, normal_cosine=float(np.average(cosine, weights=length)),
                folded_fraction=float(length[cosine < normal_threshold].sum()/total))


def _groups(indices, contacts, policy):
    parents = {i:i for i in indices}
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for a in indices:
        for b in indices:
            contact = contacts.get((a, b))
            if (a < b and contact and contact['shared_boundary_fraction'] >= policy.minimum_shared_boundary_fraction and
                    contact['normal_cosine'] >= policy.minimum_contact_normal_cosine and
                    contact['folded_fraction'] <= policy.maximum_contact_fold_fraction):
                parents[find(b)] = find(a)
    return [sorted(i for i in indices if find(i) == root) for root in sorted({find(i) for i in indices})]


def _view_direction(view):
    camera = Camera(**view['camera'])
    yaw, pitch = np.radians([camera.yaw, camera.pitch])
    toward = np.array([np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)])
    direction = np.linalg.solve(np.asarray(view['world_to_render'])[:3, :3], toward)
    return direction/np.linalg.norm(direction)


def _distinct_support_count(directions, minimum_angle_degrees):
    # Conservative packing: every retained direction must differ from all others.
    # Repeated IDs/renders or image rolls cannot manufacture independent support.
    selected = []
    cosine = np.cos(np.radians(minimum_angle_degrees))
    for direction in directions:
        if all(np.dot(direction, previous) <= cosine for previous in selected):
            selected.append(direction)
    return len(selected)


def seed_decision(evidence, directions, policy):
    """Separate a complete detection omission from partial frame contamination."""
    reliable = [e for e in evidence if e['visible_pixels'] >= policy.minimum_visible_pixels]
    supporting = [e for e in reliable if e['mask_purity'] >= policy.seed_minimum_view_purity and
                  e['mask_hits'] >= policy.minimum_mask_hits]
    dropout = [e for e in reliable if e['mask_purity'] <= policy.maximum_dropout_view_purity]
    partial = [e for e in reliable if policy.maximum_dropout_view_purity < e['mask_purity'] < policy.seed_minimum_view_purity]
    distinct = _distinct_support_count([directions[e['view_id']] for e in supporting], policy.minimum_distinct_view_angle_degrees)
    minimum = min((e['mask_purity'] for e in reliable), default=None)
    hits = sum(e['mask_hits'] for e in evidence)
    ordinary = bool(reliable and minimum >= policy.seed_minimum_view_purity and hits >= policy.minimum_mask_hits)
    recovered_dropout = bool(dropout and not partial and distinct >= policy.minimum_dropout_support_views)
    return dict(seed=ordinary or recovered_dropout, total_mask_hits=hits, reliable_views=len(reliable),
                minimum_visible_purity=minimum, distinct_supporting_views=distinct,
                supporting_view_ids=[e['view_id'] for e in supporting],
                dropout_view_ids=[e['view_id'] for e in dropout],
                partial_contamination_view_ids=[e['view_id'] for e in partial],
                detector_dropout_hypothesis=recovered_dropout)


def infer_part_roles(model, view_evidence, output, *, policy=None):
    """Infer reviewable lens groups; see VIEW_SCHEMA for the stable v1 inputs.

    Files are hash-pinned and output is immutable/resumable for identical inputs.
    Geometry is never changed. A mismatched input or correspondence raises rather
    than quietly transferring another asset's mask/labels.
    """
    policy = _policy(policy)
    model, output = Path(model).resolve(), Path(output).resolve()
    raw = model.read_bytes()
    model_sha = _sha(raw)
    mesh = load_glb_bytes(raw)
    _validate_opaque_scene(raw, mesh)
    if not 0 < len(mesh.faces) <= policy.maximum_faces or len(mesh.parts) > policy.maximum_parts:
        raise ValueError('Role inference geometry exceeds bounded budget')
    if np.ptp(mesh.vertices, axis=0).max() <= 0:
        raise ValueError('Role inference source has no spatial extent')
    if not isinstance(view_evidence, list) or not 0 < len(view_evidence) <= policy.maximum_views:
        raise ValueError('Role inference needs a bounded list of views')
    views = deepcopy(view_evidence)
    ids = [v['id'] for v in views]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('View IDs must be unique nonempty strings')
    masks_by_view, silhouettes = [], []
    transfers = {}
    for view in views:
        shape = (view['height'], view['width'])
        if any(type(n) is not int or n < 2 for n in shape) or np.prod(shape) > policy.maximum_image_pixels:
            raise ValueError('Invalid render dimensions')
        if view['mask_status'] not in ('success', 'no_detection', 'failed'):
            raise ValueError('Unknown mask status')
        if bool(view['masks']) != (view['mask_status'] == 'success'):
            raise ValueError('Mask status contradicts returned masks')
        with Image.open(BytesIO(_read_pinned(view['image']))) as image:
            if (image.height, image.width) != shape:
                raise ValueError('Rendered image size differs from view')
        masks_by_view.append([_binary_image(item, shape, policy.maximum_image_pixels) for item in view['masks']])
        silhouettes.append(_binary_image(view['silhouette'], shape, policy.maximum_image_pixels)
                           if view.get('silhouette') else None)
        if view['model_sha256'] != model_sha:
            declaration = view.get('geometry_correspondence')
            if not declaration or declaration['source_model']['sha256'] != view['model_sha256']:
                raise ValueError('Mask render belongs to another model without bounded correspondence')
            key = _sha(_json(declaration))
            if key not in transfers:
                transfers[key] = verify_geometry_correspondence(mesh, model_sha, declaration)
        camera = Camera(**view['camera'])
        if not np.isfinite(list(view['camera'].values())).all() or camera.scale <= 0 or camera.perspective < 0:
            raise ValueError('Invalid render camera')
    manifest = dict(schema_version=1, model=dict(path=str(model), sha256=model_sha), views=views,
                    policy=asdict(policy), implementation_sha256=_sha(Path(__file__).read_bytes()))
    request_sha = _sha(_json(manifest))
    _write_immutable(output/'request.json', _json(manifest))
    if (output/'completion.json').exists():
        completion = json.loads((output/'completion.json').read_text())
        if completion['request_sha256'] != request_sha:
            raise ValueError('Inference completion belongs to another request')
        cached = json.loads(_read_pinned(completion['report']))
        if cached['request_sha256'] != request_sha:
            raise ValueError('Inference request changed')
        for artifact in cached['artifacts']:
            _read_pinned(artifact)
        return cached
    part_labels = np.full(len(mesh.faces), -1, np.int32)
    for index, part in enumerate(mesh.parts):
        part_labels[part['face_start']:part['face_start']+part['face_count']] = index
    view_scores, artifacts = [], []
    for ordinal, (view, masks) in enumerate(zip(views, masks_by_view)):
        shape = (view['height'], view['width'])
        face_ids, _ = rasterize_view(mesh, Camera(**view['camera']), shape, view['world_to_render'],
                                     cull_mode=view['cull_mode'])
        silhouette = face_ids >= 0
        alignment = None
        if silhouettes[ordinal] is not None:
            observed = silhouettes[ordinal]
            alignment = float((silhouette & observed).sum()/max((silhouette | observed).sum(), 1))
            if alignment < policy.minimum_silhouette_iou:
                raise ValueError('Calibrated mask/model silhouette alignment failed')
        buffer = BytesIO()
        np.save(buffer, face_ids, allow_pickle=False)
        artifacts.append(_write_immutable(output/f'view-{ordinal:03d}-faces.npy', buffer.getvalue()))
        labels = np.full(shape, -1, np.int32)
        labels[silhouette] = part_labels[face_ids[silhouette]]
        union = np.logical_or.reduce(masks) if masks else np.zeros(shape, bool)
        positions = np.argwhere(union)
        band = max(2., policy.maximum_boundary_band_fraction*np.ptp(positions, axis=0).max()) if len(positions) else 2.
        distance = ndimage.distance_transform_edt(~union) if masks else np.zeros(shape)
        rows = []
        for index in range(len(mesh.parts)):
            region = labels == index
            visible, hits = int(region.sum()), int((region & union).sum())
            outside_band = int((region & (distance > band)).sum()) if masks else None
            rows.append(dict(part_index=index, visible_pixels=visible, mask_hits=hits,
                mask_purity=hits/visible if visible and masks else None,
                outside_boundary_band_fraction=outside_band/visible if visible and masks else None,
                per_mask_hits=[int((region & mask).sum()) for mask in masks]))
        view_scores.append(dict(id=view['id'], mask_status=view['mask_status'], silhouette_iou=alignment,
                                mask_pixels=int(union.sum()), boundary_band_pixels=float(band), parts=rows))
    part_scores = []
    directions = {view['id']:_view_direction(view) for view in views}
    for index, part in enumerate(mesh.parts):
        evidence = [dict(v['parts'][index], view_id=v['id']) for v in view_scores if v['mask_status'] == 'success']
        decision = seed_decision(evidence, directions, policy)
        seed = decision['seed']
        part_scores.append(dict(part_index=index, source_binding={k:part[k] for k in ('node_index', 'mesh_index', 'primitive_index')},
            **decision, confidence='multiview_support_with_detector_dropout' if decision['detector_dropout_hypothesis'] else
                'strong_visible_support' if seed and decision['distinct_supporting_views']>1 else
                'limited_visible_support' if seed else 'unobserved' if not decision['reliable_views'] else 'unsupported_by_visible_masks'))
    seeds = [p['part_index'] for p in part_scores if p['seed']]
    geometry = [part_boundary_geometry(mesh, p) for p in mesh.parts]
    extent = float(np.ptp(mesh.vertices, axis=0).max())
    tolerance = extent*policy.boundary_contact_relative_tolerance
    contacts = {}
    for index, fragment in enumerate(geometry):
        for seed in seeds:
            if index != seed:
                contacts[index, seed] = boundary_contact(fragment, geometry[seed], tolerance, policy.minimum_contact_normal_cosine)
    groups = _groups(seeds, contacts, policy)
    fragments = []
    for index in range(len(mesh.parts)):
        if index in seeds:
            continue
        evidence = [v['parts'][index] for v in view_scores if v['mask_status']=='success' and
                    v['parts'][index]['visible_pixels'] >= policy.minimum_fragment_visible_pixels]
        observed = bool(evidence and sum(e['mask_hits'] for e in evidence) >= policy.minimum_fragment_mask_hits)
        near_contour = bool(evidence and all(e['outside_boundary_band_fraction'] <= policy.maximum_fragment_outside_band_fraction for e in evidence))
        for seed in seeds:
            contact = contacts[index, seed]
            area_ratio = geometry[index]['area']/max(geometry[seed]['area'], 1e-30)
            distances = np.abs((geometry[index]['samples']-geometry[seed]['center']) @ geometry[seed]['plane_normal'])
            plane_distance = float(np.quantile(distances, .95)/extent) if len(distances) else None
            checks = dict(has_visible_mask_support=observed, near_mask_boundary=near_contour,
                small_relative_area=area_ratio <= policy.maximum_fragment_area_ratio,
                shared_cut_boundary=contact['shared_boundary_fraction'] >= policy.minimum_shared_boundary_fraction,
                smooth_contact=contact['normal_cosine'] >= policy.minimum_contact_normal_cosine and
                    contact['folded_fraction'] <= policy.maximum_contact_fold_fraction,
                near_seed_surface=plane_distance is not None and plane_distance <= policy.maximum_fragment_plane_distance_relative)
            if contact['shared_length'] > 0 or observed:
                fragments.append(dict(part_index=index, seed_part_index=seed, candidate=all(checks.values()),
                    checks=checks, area_ratio=area_ratio, seed_plane_p95_distance_relative=plane_distance,
                    contact=contact, role='edge_fragment_alternative' if all(checks.values()) else 'not_promoted'))
    candidates = [f for f in fragments if f['candidate']]
    hypotheses = [dict(id='primary', groups=groups, added_fragments=[], accepted=False)] if groups else []
    for fragment in candidates:
        if len(hypotheses) >= policy.maximum_hypotheses:
            break
        alternate = deepcopy(groups)
        for group in alternate:
            if fragment['seed_part_index'] in group:
                group.append(fragment['part_index'])
                group.sort()
        if alternate not in [h['groups'] for h in hypotheses]:
            hypotheses.append(dict(id=f'edge-alternative-{len(hypotheses):03d}', groups=alternate,
                added_fragments=[fragment['part_index']], accepted=False))
    # A second rim fragment may need to be added simultaneously. Combine only
    # fragments whose candidate attachments all identify the same seed group.
    # Conflicting attachments remain independent alternatives, never an overlap.
    attachments = {}
    for fragment in candidates:
        group_index = next(i for i, group in enumerate(groups) if fragment['seed_part_index'] in group)
        attachments.setdefault(fragment['part_index'], set()).add(group_index)
    unambiguous = {part:next(iter(indices)) for part, indices in attachments.items() if len(indices)==1}
    if len(unambiguous) > 1 and len(hypotheses) < policy.maximum_hypotheses:
        combined = deepcopy(groups)
        for part, group_index in sorted(unambiguous.items()):
            combined[group_index].append(part)
            combined[group_index].sort()
        if combined not in [h['groups'] for h in hypotheses]:
            hypotheses.append(dict(id='edge-alternative-combined', groups=combined,
                added_fragments=sorted(unambiguous), accepted=False))
    reasons = ['mask_semantics_and_unobserved_surfaces_not_verified']
    if not seeds:
        reasons.append('no_supported_optical_seed')
    if any(v['mask_status'] != 'success' for v in view_scores):
        reasons.append('one_or_more_views_have_missing_mask_evidence')
    if any(p['seed'] and p['reliable_views'] < 2 for p in part_scores):
        reasons.append('optical_seed_has_only_one_reliable_view')
    if any(p['detector_dropout_hypothesis'] for p in part_scores):
        reasons.append('multiview_supported_part_retained_despite_single_view_detector_omission')
    if candidates:
        reasons.append('small_continuous_edge_fragments_require_alternative_evaluation')
    if len(candidates) >= policy.maximum_hypotheses:
        reasons.append('fragment_hypothesis_budget_exhausted')
    if any(v['silhouette_iou'] is None for v in view_scores):
        reasons.append('renderer_silhouette_alignment_not_independently_measured')
    result = dict(schema_version=1, method='calibrated_part_role_inference_v2', source_sha256=model_sha,
        source_path=str(model), request_sha256=request_sha, policy=asdict(policy),
        status='candidate_hypotheses' if seeds else 'needs_evidence', accepted=False, requires_review=True,
        review_reasons=reasons, selected_part_indices=seeds, primary_groups=groups, hypotheses=hypotheses,
        parts=part_scores, views=view_scores, fragment_evidence=fragments,
        geometry_correspondence=list(transfers.values()), artifacts=artifacts,
        confidence_semantics='Evidence categories, not calibrated correctness probabilities; no hidden face labels inferred')
    report_receipt = _write_immutable(output/'report.json', _json(result))
    _write_immutable(output/'completion.json', _json(dict(request_sha256=request_sha, report=report_receipt)))
    return result


def make_group_declarations(report, coordinate_frame, *, hypothesis_id='primary'):
    """Build preparer-compatible declarations only for the exact analyzed asset.

    Caller must actually establish the declared +Y-up/+Z-front frame. A later
    canonicalization/LOD stage must separately rebind against its new source.
    """
    from .prepare_optical_groups import validate_group_declarations
    hypothesis = next((h for h in report['hypotheses'] if h['id']==hypothesis_id), None)
    if hypothesis is None:
        raise ValueError('Unknown part-role hypothesis')
    parts = {p['part_index']:p for p in report['parts']}
    value = dict(schema_version=1, source_sha256=report['source_sha256'], coordinate_frame=coordinate_frame,
        provenance=dict(method='automatic_visible_part_role_hypothesis', hypothesis_id=hypothesis_id,
                        role_request_sha256=report['request_sha256'], automatic_semantic_acceptance=False,
                        review_reasons=report['review_reasons']),
        groups=[dict(group_id=f'optical-{i}', members=[dict(id=f'part-{index}', source_part_index=index,
                     source_binding=parts[index]['source_binding']) for index in group])
                for i, group in enumerate(hypothesis['groups'])])
    return validate_group_declarations(value)


VIEW_SCHEMA = {
    'schema_version': 1,
    'required_fields': ['id', 'model_sha256', 'width', 'height', 'camera', 'world_to_render',
                        'cull_mode', 'image', 'masks', 'mask_status'],
    'camera': 'reconstruction.camera.Camera fields; integer pixel centers, orthographic/pinhole depth',
    'world_to_render': '4x4 row-major affine matrix applied to decoded GLB world positions',
    'image_and_masks': '{path,sha256}; masks also carry optional descriptive id, never part indices',
    'mask_status': ['success', 'no_detection', 'failed'],
    'optional_fields': ['silhouette', 'geometry_correspondence'],
    'geometry_correspondence': 'source_model and candidate_to_source_faces each {path,sha256}; max_relative_corner_error<=1e-6',
}
