"""Bind conditional photo samples to the actual exported optical UV and normals.

No mask, camera, material grouping or backdrop estimate establishes identity.
All mask alternatives are retained. Occluded, unsupported and stacked optical
rays remain in coverage accounting. Rear-frame colors are deliberately unknown;
the uncalibrated fitter must explore them as nuisance hypotheses.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .camera import Camera
from .deform_glb import _read, _float_accessor, _triangles
from .mesh import TriangleMesh, load_glb_bytes
from .raster import DEPTH_UNITS, rasterize


METHOD = 'prepared_float32_uv_photo_observations_v2'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_pinned(path, pins, expected=None):
    """Parse the same captured bytes that are hashed; never replace an old pin."""
    path = Path(path).resolve()
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if ((expected is not None and digest != expected)
            or (str(path) in pins and pins[str(path)] != digest)):
        raise ValueError(f'Observation source bytes changed: {path}')
    pins[str(path)] = digest
    return raw


def _child(root, relative):
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('Artifact path escapes its report directory')
    return result


def _linear(code):
    s = np.asarray(code, float) / 255.
    return np.where(s <= .04045, s / 12.92, ((s + .055) / 1.055) ** 2.4)


def _native(field, shape):
    h, w = shape
    rows = np.minimum(field.shape[0] - 1, ((np.arange(h) + .5) * field.shape[0] / h).astype(int))
    cols = np.minimum(field.shape[1] - 1, ((np.arange(w) + .5) * field.shape[1] / w).astype(int))
    return field[rows[:, None], cols[None, :]]


def _stratified_sample(mask, capacity):
    """At most one source pixel per two-dimensional mask-bbox stratum.

    Strata partition the source pixel cells in the mask's integer bounding box;
    occupied cells choose the pixel nearest their center (normalized distance),
    then the smallest row and column. Empty cells are NOT silently refilled by
    a one-dimensional sampler. Unused capacity and represented cell populations
    are recorded. This is deterministic spatial coverage, not random sampling or
    a set of equally weighted population measurements.
    """
    mask = np.asarray(mask)
    if mask.ndim != 2 or mask.dtype != bool or type(capacity) is not int or capacity < 1:
        raise ValueError('Spatial sampling requires a boolean mask and positive integer capacity')
    rows, cols = np.nonzero(mask)
    count = len(rows)
    if count <= capacity:
        return rows, cols, {'method': 'all_eroded_mask_pixels_within_capacity', 'population_pixels': count,
                           'selected_pixels': count, 'capacity': capacity, 'unused_capacity': capacity-count,
                           'selection_before_coordinate_or_alpha_exclusions': True}
    x0, y0 = int(cols.min()), int(rows.min())
    width, height = int(cols.max())-x0+1, int(rows.max())-y0+1
    nx = min(width, capacity, max(1, int(np.ceil(np.sqrt(capacity*width/height)))))
    ny = min(height, max(1, capacity // nx))
    bx = (2*(cols-x0)+1)*nx // (2*width)
    by = (2*(rows-y0)+1)*ny // (2*height)
    cell = by*nx + bx
    # Pixel indices denote centers; the box's outside edges are x0-.5 etc.
    dx = (cols-x0+.5)*nx/width - (bx+.5)
    dy = (rows-y0+.5)*ny/height - (by+.5)
    distance = dx*dx + dy*dy
    order = np.lexsort((cols, rows, distance, cell))
    first = np.r_[True, cell[order][1:] != cell[order][:-1]]
    selected = order[first]
    selected = selected[np.lexsort((cols[selected], rows[selected]))]
    populations = np.bincount(cell, minlength=nx*ny)
    receipt = {'method': 'mask_bbox_2d_strata_nearest_center_v1', 'population_pixels': count,
               'selected_pixels': len(selected), 'capacity': capacity, 'unused_capacity': capacity-len(selected),
               'bbox_xyxy_exclusive': [x0, y0, x0+width, y0+height], 'grid_columns': nx, 'grid_rows': ny,
               'empty_strata': int(np.count_nonzero(populations == 0)),
               'strata_in_selected_pixel_order': [{'column': int(bx[i]), 'row': int(by[i]), 'population_pixels': int(populations[cell[i]])} for i in selected],
               'distance': 'squared distance to source-pixel-cell stratum center, normalized by cell width and height',
               'tie_break': 'smallest source row then column', 'selection_before_coordinate_or_alpha_exclusions': True,
               'population_weighting': 'spatial representatives; equal sample weights are not equal pixel-population weights'}
    return rows[selected], cols[selected], receipt


def _prepared_attributes(model, export):
    raw, doc, binary = _read(model)
    if hashlib.sha256(raw).hexdigest() != export['output_sha256']:
        raise ValueError('Prepared GLB hash differs from export receipt')
    mesh = load_glb_bytes(raw)
    uv, normals = np.full((len(mesh.vertices), 2), np.nan), np.full_like(mesh.vertices, np.nan)
    groups = np.full(len(mesh.faces), -1, dtype=int)
    binding_by_node = {row['node_index']: row for row in export['surfaces']}
    if len(binding_by_node) != len(export['surfaces']):
        raise ValueError('Duplicate prepared surface node binding')
    found = set()
    for part in mesh.parts:
        binding = binding_by_node.get(part['node_index'])
        if binding is None:
            if part.get('has_lens_appearance_extension'):
                raise ValueError('Unbound canonical optical surface in prepared scene')
            continue
        node = doc['nodes'][part['node_index']]
        if (part['mesh_index'] != binding['mesh_index'] or part['material_index'] != binding['material_index']
                or node.get('extras', {}).get('opticalCandidateSurfaceId') != binding['surface_id']
                or any(key in node for key in ('matrix', 'translation', 'rotation', 'scale'))):
            raise ValueError('Prepared surface binding or baked identity transform changed')
        if part['node_index'] not in doc['scenes'][doc.get('scene', 0)]['nodes']:
            raise ValueError('Prepared surface must be a baked scene root')
        primitive = doc['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]
        attrs = primitive['attributes']
        values = {name: _float_accessor(doc, binary, attrs[attr], width) for name, attr, width in
                  (('positions', 'POSITION', 3), ('normals', 'NORMAL', 3), ('uv', 'TEXCOORD_0', 2))}
        values['indices'] = _triangles(doc, binary, primitive, len(values['positions'])).astype('<u4')
        for name, array in values.items():
            payload = array.astype('<u4' if name == 'indices' else '<f4').tobytes()
            if hashlib.sha256(payload).hexdigest() != binding['attribute_sha256'][name]:
                raise ValueError(f'Prepared {name} bytes differ from export receipt')
        start, count = part['vertex_start'], part['vertex_count']
        uv[start:start + count] = values['uv']
        normals[start:start + count] = values['normals']
        first = part['face_start']
        groups[first:first + part['face_count']] = binding['source_part_index']
        found.add(part['node_index'])
    if found != binding_by_node.keys():
        raise ValueError('Export receipt contains a missing optical surface')
    return mesh, uv, normals, groups


def project_optical_fields(mesh, uv, normals, face_groups, camera, shape, normalization, *, view_scene=None):
    """Raster actual attributes, retaining unknown/stacked/occluded coverage."""
    center, extent = np.asarray(normalization['center'], float), normalization['extent']
    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
        raise ValueError('Invalid original camera normalization')
    if view_scene is not None:
        from .view_scene import pose_mesh_from_contract
        posed = pose_mesh_from_contract(mesh, view_scene['contract'], view_scene['view_id'],
            photo_sha256=view_scene.get('source_image_sha256'), uv=uv, normals=normals)
        mesh, uv, normals = posed['mesh'], posed['uv'], posed['normals']
    scene = TriangleMesh((mesh.vertices - center) / extent, mesh.faces, mesh.parts)
    optical_indices, opaque_indices = np.flatnonzero(face_groups >= 0), np.flatnonzero(face_groups < 0)
    optical = TriangleMesh(scene.vertices, scene.faces[optical_indices], [])
    first = rasterize(optical, camera, shape)
    # Explicitly separate numerical shared-edge ties from distinct interfaces.
    separation = 1e-9
    second = rasterize(optical, camera, shape, after_depth=first.depth + separation)
    opaque = rasterize(TriangleMesh(scene.vertices, scene.faces[opaque_indices], []), camera, shape)
    visible = first.mask & (first.depth < opaque.depth - separation)
    stacked = visible & second.mask & (second.depth < opaque.depth - separation)
    supported = visible & ~stacked
    group = np.full(shape, -1, dtype=int)
    v, incidence = np.full(shape, np.nan), np.full(shape, np.nan)
    reflected = np.full((*shape, 3), np.nan)
    rear_weight = np.zeros(shape)
    rows, cols = np.nonzero(supported)
    source_faces = optical_indices[first.face_index[rows, cols]]
    indices = scene.faces[source_faces]
    weights = first.barycentric[rows, cols]
    local_uv = np.einsum('ni,nij->nj', weights, uv[indices])
    # Three's normal_vertex normalizes each transformed vertex normal BEFORE
    # interpolation; normal_fragment then normalizes the interpolated varying.
    # These prepared roots have baked identity transforms, so the camera's rigid
    # rotation commutes with this normalization. Raw authored lengths must not
    # become interpolation weights (the exporter permits nonunit normals).
    vertex_normals = normals[indices]
    vertex_lengths = np.linalg.norm(vertex_normals, axis=2)
    if not np.isfinite(vertex_normals).all() or np.any(vertex_lengths <= 0):
        raise ValueError('Prepared optical vertices require finite nonzero normals')
    vertex_normals = vertex_normals / vertex_lengths[:, :, None]
    normal = np.einsum('ni,nij->nj', weights, vertex_normals)
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    xyz = first.surface_points(optical, rows, cols)
    yaw, pitch, roll = np.radians([camera.yaw, camera.pitch, camera.roll])
    right = np.array([np.cos(yaw), 0., -np.sin(yaw)])
    up = np.array([-np.sin(pitch)*np.sin(yaw), np.cos(pitch), -np.sin(pitch)*np.cos(yaw)])
    toward = np.array([np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)])
    # Equivalent to camera_position - xyz, without dividing by tiny perspective.
    direction = np.broadcast_to(toward, xyz.shape).copy() - camera.perspective * xyz
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    cosine = np.sum(normal * direction, axis=1)
    reflection = 2 * cosine[:, None] * normal - direction
    basis = np.column_stack((np.cos(roll)*right - np.sin(roll)*up,
                             np.sin(roll)*right + np.cos(roll)*up, toward))
    reflected[rows, cols] = reflection @ basis
    group[rows, cols] = face_groups[source_faces]
    v[rows, cols] = np.clip(local_uv[:, 1], 0, 1)
    incidence[rows, cols] = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
    rear_weight[supported & opaque.mask] = 1.
    return {'group': group, 'v': v, 'incidence': incidence, 'reflected': reflected,
            'rear_weight': rear_weight, 'visible': visible, 'stacked': stacked,
            'optical_footprint': first.mask, 'depth_separation': separation,
            'depth_units': DEPTH_UNITS, 'mesh_length_units': 'original_camera_normalized_candidate_coordinates'}


def build_photo_lens_observations(prepared_model: Path, export_report: Path, region_report: Path,
                                  *, maximum_samples_per_hypothesis=256):
    """Return per-source-part fitter inputs and a finite JSON coverage receipt.

    Deterministic two-dimensional strata sample each eroded mask BEFORE exclusions.
    All unsupported coordinates remain NaN and are counted by the fitter.
    """
    if type(maximum_samples_per_hypothesis) is not int or not 16 <= maximum_samples_per_hypothesis <= 4096:
        raise ValueError('Sample capacity must be 16..4096')
    prepared_model, export_report, region_report = map(lambda p: Path(p).resolve(), (prepared_model, export_report, region_report))
    pins = {str(prepared_model): _sha(prepared_model)}
    export, regions = (json.loads(_read_pinned(p, pins)) for p in (export_report, region_report))
    if export['source_sha256'] != regions['candidate_sha256']:
        raise ValueError('Photo regions and prepared source refer to different candidates')
    mesh, uv, normals, face_groups = _prepared_attributes(prepared_model, export)
    groups = {int(i): {'surface_binding': {'schema_version': 1, 'prepared_glb_sha256': pins[str(prepared_model)],
              'material_group_id': f'source-part-{i}', 'coordinate_method': METHOD,
              'uv_semantics': 'lens_local_bottom_0_top_1', 'source_sha256': export['source_sha256'],
              'source_part_index': int(i), 'grouping_identity': 'candidate_source_part_unverified'}, 'observations': []}
              for i in sorted(set(face_groups) - {-1})}
    report = {'schema_version': 1, 'method': METHOD, 'quality_verdict': 'unmeasured', 'accepted': False,
              'maximum_samples_per_hypothesis': maximum_samples_per_hypothesis, 'photos': [],
              'limitations': ['Masks, original cameras and source-part material groups remain hypotheses.',
                  'Per-group fitting shares illumination within that group only; separate groups are not jointly illuminated fits.',
                  'Native photo centers use nearest working-grid geometry; geometric quantization is not removed.',
                  'Image-border median is an uncalibrated effective backdrop hypothesis, not measured scene radiance.',
                  'Rear-frame color is unknown and must be explored as a nuisance variable; frame transparency is unsupported.',
                  'Multiple optical intersections before an opaque stop are excluded from this single-interface fitter.',
                  'This preparation does not identify gradients, mirrors, roughness, missing regions or unseen sides.']}
    return _sample_bound_photo_observations(mesh, uv, normals, face_groups, groups,
        regions=regions, region_report=region_report, export_report=export_report,
        source_sha256=export['source_sha256'], pins=pins, report=report,
        maximum_samples_per_hypothesis=maximum_samples_per_hypothesis)


def _sample_bound_photo_observations(mesh, uv, normals, face_groups, groups, *,
        regions, region_report, export_report, source_sha256, pins, report,
        maximum_samples_per_hypothesis, method=METHOD, project_fields=project_optical_fields,
        region_group_ids=None, group_identities=None, observation_suffixes=None):
    """Shared extraction after a profile-specific exported-geometry verification.

    The two profile adapters supply their own geometry/event semantics and group
    bindings. Native masks, colors, sampling and unknown support stay identical.
    This private helper never validates or upgrades an optical geometry profile.
    """
    identities = group_identities or {i: {'source_part_index': i} for i in groups}
    suffixes = observation_suffixes or {i: f'part-{i}' for i in groups}
    for photo in regions['photos']:
        projection = photo['candidate_projection']
        if projection.get('status') != 'candidate_conditioned_projection':
            report['photos'].append({'id': photo['id'], 'status': 'camera_unavailable', 'hypotheses': []})
            continue
        if projection['candidate_sha256'] != source_sha256:
            raise ValueError('Camera projection candidate binding differs')
        source = Path(photo['source']).resolve()
        raw_photo = _read_pinned(source, pins, photo['source_sha256'])
        with Image.open(io.BytesIO(raw_photo)) as image:
            if image.mode not in ('RGB', 'RGBA') or image.info.get('icc_profile') or image.getexif().get(274, 1) != 1 or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Use normalized, pinned single-frame sRGB photos')
            pixels = np.asarray(image.convert('RGBA')).copy()
        h, w = pixels.shape[:2]
        if photo['image_size'] != [w, h]:
            raise ValueError('Photo grid changed')
        size = projection['working_size']
        if len(size) != 2 or any(type(v) is not int or not 2 <= v <= 768 for v in size):
            raise ValueError('Unsupported working camera grid')
        camera = Camera(**projection['camera'])
        if not np.isfinite(list(camera.to_dict().values())).all() or camera.scale <= 0 or camera.perspective < 0:
            raise ValueError('Invalid fitted camera')
        context = projection.get('view_scene')
        if context is not None and (context.get('view_id') != photo['id'] or context.get('source_image_sha256') != photo['source_sha256']):
            raise ValueError('Articulated projection is bound to a different source photo')
        articulation = {'view_scene': context} if context is not None else {}
        fields = project_fields(mesh, uv, normals, face_groups, camera, (size[1], size[0]), projection['normalization'], **articulation)
        fields = {key: _native(value, (h, w)) if isinstance(value, np.ndarray) else value for key, value in fields.items()}
        border = np.zeros((h, w), bool)
        border[[0, -1], :] = True
        border[:, [0, -1]] = True
        border &= pixels[:, :, 3] == 255
        if not border.any():
            report['photos'].append({'id': photo['id'], 'status': 'opaque_backdrop_support_unavailable', 'hypotheses': []})
            continue
        background_samples = _linear(pixels[border, :3])
        background = np.median(background_samples, axis=0)
        record = {'id': photo['id'], 'status': 'conditional_samples', 'camera': projection['camera'],
                  'working_size': size, 'normalization': projection['normalization'],
                  'backdrop': {'method': 'opaque_border_linear_rgb_median', 'pixels': int(border.sum()),
                      'median': background.tolist(), 'p10': np.quantile(background_samples, .1, axis=0).tolist(),
                      'p90': np.quantile(background_samples, .9, axis=0).tolist()},
                  'depth_separation_in_raster_units': fields['depth_separation'],
                  'depth_units': fields['depth_units'], 'mesh_length_units': fields['mesh_length_units'],
                  'normal_interpolation': 'normalize_each_authored_vertex_then_perspective_interpolate_then_normalize',
                  'hypotheses': []}
        report['photos'].append(record)
        if 'profile_diagnostics' in fields:
            record['profile_diagnostics'] = fields['profile_diagnostics']
        for region in photo['regions']:
            if region['kind'] != 'candidate_optical_region':
                continue
            part_ids = (region_group_ids(region['prior']['part_indices']) if region_group_ids
                        else region['prior']['part_indices'])
            if any(i not in groups for i in part_ids):
                raise ValueError('Region optical prior has no prepared source-part binding')
            folder = _child(region_report.parent, f"{photo['id']}/{region['directory']}")
            for hypothesis in region['hypotheses']:
                artifact = hypothesis['intersection']
                mask_path = _child(folder, artifact['path'])
                try:
                    raw_mask = _read_pinned(mask_path, pins, artifact['sha256'])
                except ValueError as error:
                    raise ValueError('Region mask bytes changed') from error
                with Image.open(io.BytesIO(raw_mask)) as image:
                    mask = np.asarray(image.convert('L')) > 0
                if mask.shape != (h, w) or int(mask.sum()) != artifact['pixels']:
                    raise ValueError('Region mask grid/count differs')
                interior = ndimage.binary_erosion(mask, structure=np.ones((5, 5), bool), border_value=0)
                rows, cols, sampling = _stratified_sample(interior, maximum_samples_per_hypothesis)
                for group_id in part_ids:
                    supported = interior & (fields['group'] == group_id)
                    row = {'region_id': region['id'], 'hypothesis': hypothesis['index'], **identities[group_id],
                           'mask_pixels': int(mask.sum()), 'interior_pixels': int(interior.sum()),
                           'prepared_coordinate_pixels': int(supported.sum()),
                           'stacked_pixels': int((interior & fields['stacked']).sum()),
                           'occluded_or_missing_pixels': int((interior & ~fields['visible']).sum()),
                           'rear_frame_pixels': int((supported & (fields['rear_weight'] > 0)).sum()),
                           'sampled_pixels': len(rows), 'sampling': sampling}
                    record['hypotheses'].append(row)
                    for name in ('ambiguous', 'invalid_attributes', 'overflow'):
                        if name in fields:
                            row[name + '_pixels'] = int((interior & fields[name]).sum())
                    if not len(rows):
                        row['status'] = 'empty_eroded_mask'
                        continue
                    valid = fields['group'][rows, cols] == group_id
                    v = np.where(valid, fields['v'][rows, cols], np.nan)
                    angle = np.where(valid, fields['incidence'][rows, cols], np.nan)
                    reflection = np.where(valid[:, None], fields['reflected'][rows, cols], np.nan)
                    rear_weight = np.where(valid, fields['rear_weight'][rows, cols], 0.)
                    row.update(status='conditional_samples', sampled_prepared_coordinates=int(valid.sum()))
                    observation = {'id': f"{photo['id']}/{region['id']}/{hypothesis['index']}/{suffixes[group_id]}",
                        'photo_id': photo['id'], 'region_id': region['id'], 'hypothesis_id': str(hypothesis['index']),
                        'source_sha256': photo['source_sha256'], 'xy': np.column_stack((cols, rows)),
                        'code_rgb': pixels[rows, cols, :3], 'alpha_code': pixels[rows, cols, 3],
                        'intrinsic_v': v, 'incidence_degrees': angle, 'reflected_direction': reflection,
                        'background_rgb': np.broadcast_to(background, (len(rows), 3)).copy(),
                        'rear_rgb': np.full((len(rows), 3), np.nan), 'rear_weight': rear_weight, 'image_size': [w, h],
                        'provenance': {'method': method, 'mask_sha256': pins[str(mask_path)],
                            'export_report_sha256': pins[str(export_report)], 'region_report_sha256': pins[str(region_report)],
                            'coordinate_resampling': 'native pixel center to nearest working pixel; perspective-correct attributes',
                            'normal_interpolation': record['normal_interpolation'],
                            'depth_units': record['depth_units'], 'sampling': sampling,
                            'rear_color': 'unknown_not_invented', 'backdrop': record['backdrop'], 'coverage': row}}
                    if 'profile_diagnostics' in fields:
                        observation['provenance']['profile_diagnostics'] = fields['profile_diagnostics']
                    groups[group_id]['observations'].append(observation)
    for path, digest in pins.items():
        if _sha(path) != digest:
            raise ValueError('An observation source changed while sampling')
    report['input_sha256'] = pins
    report['groups'] = [{**identities[group], 'observations': len(value['observations']),
                         'surface_binding': value['surface_binding']} for group, value in groups.items()]
    return {'groups': groups, 'report': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('model', 'export', 'regions', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Use an empty observation output directory')
    result = build_photo_lens_observations(args.model, args.export, args.regions)
    args.output.mkdir(parents=True, exist_ok=True)
    for group_id, group in result['groups'].items():
        folder = args.output / f'part-{group_id:03d}'
        folder.mkdir()
        records = []
        for index, observation in enumerate(group['observations']):
            arrays = {key: value for key, value in observation.items() if isinstance(value, np.ndarray)}
            path = folder / f'observation-{index:03d}.npz'
            np.savez_compressed(path, **arrays)
            records.append({**{key: value for key, value in observation.items() if key not in arrays},
                            'arrays': {'path': path.name, 'sha256': _sha(path)}})
        (folder / 'observations.json').write_text(json.dumps({'surface_binding': group['surface_binding'],
            'observations': records}, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (args.output / 'report.json').write_text(json.dumps(result['report'], indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
