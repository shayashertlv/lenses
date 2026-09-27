"""Frozen image-aperture/source-component evidence, without physical selection.

All five original development meshes and ten saved cameras are retained. Every
source component and every image-only aperture alternative is measured. Neither
the diagnostic overlays nor numerical agreement accepts a lens identity/group.
"""
from __future__ import annotations

import argparse
import colorsys
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np
from PIL import Image, ImageDraw
import PIL
from scipy import ndimage
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconstruction.aperture_evidence import load_aperture_evidence
from reconstruction.camera import Camera
from reconstruction.component_projection import project_components, measure_component_apertures
from reconstruction.mesh import TriangleMesh
from reconstruction.mesh_components import component_face_labels
from reconstruction.optical_identity import identity_json_sha256
from reconstruction.partition_glb import inspect_partition_source
from reconstruction.photo_lens_observations import _child, _read_pinned
from reconstruction.prepare_optical_groups import _ordinary_path, _write


EXPECTED = ('rayban', 'miumiu', 'oakley', 'invu', 'victoria-beckham')
SCHEMA = 'all_source_components_frozen_image_apertures_v1'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()


def _save_json(folder, name, value):
    return _write(folder, name, _json_bytes(value))


def _save_npz(folder, name, arrays):
    stream = io.BytesIO(); np.savez_compressed(stream, **arrays)
    return _write(folder, name, stream.getvalue())


def _save_image(folder, name, picture):
    stream = io.BytesIO(); picture.save(stream, format='PNG')
    return _write(folder, name, stream.getvalue())


def _array_record(value):
    value = np.asarray(value)
    return {'sha256': _sha(np.ascontiguousarray(value).tobytes()),
            'dtype': value.dtype.str, 'shape': list(value.shape),
            'encoding': 'C_order_numeric_array_bytes'}


def _verify_array(value, record):
    if _array_record(value) != {key: record[key] for key in ('sha256', 'dtype', 'shape', 'encoding')}:
        raise ValueError('Inventory source/label array differs from its declared snapshot')


def _read_json(path, pins, expected=None):
    return json.loads(_read_pinned(path, pins, expected))


def _unique(rows, key, value):
    found = [row for row in rows if row.get(key) == value]
    if len(found) != 1:
        raise ValueError(f'Expected one {key}={value} record')
    return found[0]


def _build_source(case, corpus, inventory_root, inventory_corpus, pins):
    """Recheck complete frozen membership against the captured local GLB arrays."""
    source_path = (corpus.parent/case['model']).resolve()
    corpus_row = _unique(inventory_corpus['cases'], 'id', case['id'])
    reference = corpus_row['stage_reports']['inventory']
    report_path = inventory_root/case['id']/'inventory/report.json'
    if Path(reference['path']).resolve() != report_path:
        raise ValueError('Inventory corpus refers to a different stage report')
    inventory = _read_json(report_path, pins, reference['sha256'])
    if (inventory.get('schema_version') != 1 or inventory.get('status') != 'component_inventory_complete'
            or inventory.get('coordinate_space') != 'source_local'
            or inventory.get('connectivity') != 'exact_position_vertex'
            or inventory.get('accepted') is not False or inventory.get('semantic_identity') != 'not_inferred'):
        raise ValueError('Unsupported complete source inventory')
    # Historical inventories do not list modules added afterward. Verify the
    # entries they actually pinned without demanding equality of module sets.
    for name, digest in inventory['implementation']['source_sha256'].items():
        _read_pinned(_child(ROOT/'reconstruction', name), pins, digest)
    snapshot = inventory['source_snapshot']
    raw = _read_pinned(_child(report_path.parent, snapshot['path']), pins, snapshot['sha256'])
    source_hash = _sha(raw)
    if source_hash != inventory['source_sha256'] or source_hash != corpus_row['source_sha256']:
        raise ValueError('Inventory source identities disagree')
    if _read_pinned(source_path, pins, source_hash) != raw:
        raise ValueError('Original source no longer equals the captured inventory source')
    source = inspect_partition_source(raw)
    if source['selected_scene'] != inventory['selected_scene'] or len(source['instances']) != inventory['primitive_count']:
        raise ValueError('Inventory omits or changes source primitive instances')
    if len(inventory['primitives']) != len(source['instances']):
        raise ValueError('Inventory primitive coverage is incomplete')
    label_ref = inventory['labels']
    label_raw = _read_pinned(_child(report_path.parent, label_ref['path']), pins, label_ref['sha256'])
    vertices, faces, face_components, table = [], [], [], []
    vertex_offset = face_offset = component_offset = 0
    with np.load(io.BytesIO(label_raw), allow_pickle=False) as saved:
        keys = [row['labels']['npz_key'] for row in inventory['primitives']]
        if len(set(keys)) != len(keys) or set(saved.files) != set(keys):
            raise ValueError('Inventory NPZ must contain exactly one label array per primitive instance')
        for ordinal, (instance, row) in enumerate(zip(source['instances'], inventory['primitives'], strict=True)):
            points, indices = instance['positions'], instance['indices']
            if (row['source_binding'] != instance['source_binding']
                    or row['source_vertex_count'] != len(points) or row['source_face_count'] != len(indices)
                    or row['source_attribute_accessors'] != instance['primitive']['attributes']
                    or row['source_index_accessor'] != instance['primitive'].get('indices')
                    or row['source_material_index'] != instance['primitive'].get('material')
                    or row['source_world_matrix'] != instance['world_matrix'].tolist()):
                raise ValueError('Inventory source binding, metadata or local geometry count changed')
            _verify_array(points, row['positions']); _verify_array(indices, row['indices'])
            labels = saved[row['labels']['npz_key']]
            _verify_array(labels, row['labels'])
            recomputed = component_face_labels(points, indices, connectivity=inventory['connectivity'])
            if labels.dtype != np.int64 or not np.array_equal(labels, recomputed):
                raise ValueError('Inventory labels disagree with exact local-position connectivity')
            count = int(labels.max())+1
            if count != row['component_count'] or len(row['components']) != count:
                raise ValueError('Inventory component coverage changed')
            if row['referenced_vertex_count'] != int(np.unique(indices).size):
                raise ValueError('Inventory referenced-vertex count changed')
            for local_id, component in enumerate(row['components']):
                selected = np.flatnonzero(labels == local_id)
                used = points[np.unique(indices[selected])]
                if (component['component_id'] != local_id or component['first_source_face'] != int(selected[0])
                        or component['face_count'] != len(selected)
                        or component['local_bounds'] != [used.min(axis=0).tolist(), used.max(axis=0).tolist()]):
                    raise ValueError('Inventory component count, order or local bounds changed')
                table.append({'component_id': component_offset+local_id,
                    'source_primitive_ordinal': ordinal, 'source_binding': instance['source_binding'],
                    'local_component_id': local_id, 'source_face_count': len(selected),
                    'source_global_face_offset': face_offset,
                    'first_source_local_face': int(selected[0]), 'local_bounds': component['local_bounds'],
                    'semantic_identity': 'not_inferred'})
            matrix = instance['world_matrix']
            world = points @ matrix[:3, :3].T + matrix[:3, 3]
            vertices.append(world); faces.append(indices+vertex_offset)
            face_components.append(labels+component_offset)
            vertex_offset += len(points); face_offset += len(indices); component_offset += count
    if face_offset != inventory['source_face_count'] or component_offset != inventory['component_count']:
        raise ValueError('Inventory aggregate coverage changed')
    mesh = TriangleMesh(np.concatenate(vertices), np.concatenate(faces), [])
    labels = np.concatenate(face_components).astype(np.int64)
    used = mesh.vertices[np.unique(mesh.faces)]
    reference_extent = float(np.ptp(used, axis=0).max())
    if not np.isfinite(reference_extent) or reference_extent <= 0:
        raise ValueError('Source referenced vertices need a positive reference extent')
    return {'mesh': mesh, 'face_components': labels, 'component_table': table,
            'source_sha256': source_hash, 'source': str(source_path),
            'inventory_report': {'path': str(report_path), 'sha256': pins[str(report_path)]},
            'source_snapshot': {'path': str(_child(report_path.parent, snapshot['path'])), 'sha256': source_hash},
            'reference_extent': reference_extent,
            'referenced_world_bounds': [used.min(axis=0).tolist(), used.max(axis=0).tolist()]}


def _photo_evidence(case, corpus, photo, region_path, source, baseline, baseline_path, refinement_path, pins):
    projection = photo['candidate_projection']
    if (projection.get('status') != 'candidate_conditioned_projection'
            or projection.get('candidate_binding') != 'original'
            or projection.get('candidate_sha256') != source['source_sha256']
            or projection['camera_sha256'] != identity_json_sha256(projection['camera'])):
        raise ValueError('Frozen camera does not bind this original source')
    per_view_path = region_path.parent/photo['id']/'report.json'
    per_view = _read_json(per_view_path, pins)
    if per_view['candidate_projection'] != projection or per_view['source_sha256'] != photo['source_sha256']:
        raise ValueError('Aggregate and per-view frozen projection receipts differ')
    expected_photo_path = (corpus.parent/case['photos'][photo['id']]).resolve()
    if Path(photo['source']).resolve() != expected_photo_path:
        raise ValueError('Frozen camera refers to a different corpus photograph')
    original = _read_pinned(expected_photo_path, pins, photo['source_sha256'])
    with Image.open(io.BytesIO(original)) as image:
        if list(image.size) != photo['image_size'] or image.getexif().get(274, 1) != 1:
            raise ValueError('Frozen camera requires the original unrotated photo grid')
    image_id = case['id']+'-'+photo['id']
    upstream = _unique(baseline['images'], 'id', image_id)
    meta = upstream['normalization']
    if (upstream['source_sha256'] != photo['source_sha256']
            or meta['original']['sha256'] != photo['source_sha256']
            or meta['transform']['original_to_normalized_xy'] != [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            or meta['transform']['resized'] is not False
            or [meta['normalized']['width'], meta['normalized']['height']] != photo['image_size']):
        raise ValueError('Semantic normalized image does not share the frozen camera pixel grid')
    loaded = load_aperture_evidence(baseline_path, refinement_path,
        baseline_report_sha256=pins[str(baseline_path)], refinement_report_sha256=pins[str(refinement_path)],
        normalized_image_sha256=meta['normalized']['sha256'], source_sha256=photo['source_sha256'])
    for snapshot in loaded['report']['input_snapshots']:
        _read_pinned(Path(snapshot['path']), pins, snapshot['sha256'])
    if loaded['report']['image_id'] != image_id or loaded['report']['size_xy'] != photo['image_size']:
        raise ValueError('Aperture adapter returned a different photo grid')
    normalized_path = _child(baseline_path.parent/image_id, meta['normalized']['path'])
    normalized_raw = _read_pinned(normalized_path, pins, meta['normalized']['sha256'])
    with Image.open(io.BytesIO(normalized_raw)) as image:
        rgb = np.asarray(image.convert('RGB')).copy()
    native_width, native_height = photo['image_size']; width, height = projection['working_size']
    if (any(type(n) is not int or not 2 <= n <= 768 for n in (width, height))
            or rgb.shape != (native_height, native_width, 3)):
        raise ValueError('Unsupported frozen image/working grid')
    rows = np.minimum(native_height-1, np.floor((np.arange(height)+.5)*native_height/height).astype(np.int64))
    cols = np.minimum(native_width-1, np.floor((np.arange(width)+.5)*native_width/width).astype(np.int64))
    mapping = {'method': 'native[floor((working_index+0.5)*native_size/working_size)]',
        'native_size_xy': [native_width, native_height], 'working_size_xy': [width, height],
        'coordinate_system': 'integer pixel centers; source-cell nearest sampling; no mask refit',
        'source_photo_sha256': photo['source_sha256'], 'normalized_image_sha256': meta['normalized']['sha256'],
        'normalization_pixel_transform': meta['transform'],
        'known_domain_semantics': 'the identical mapping is applied independently to positive mask and prediction-availability domain'}
    apertures, records, arrays = [], [], {'native_row_indices': rows, 'native_column_indices': cols}
    for index, hypothesis in enumerate(loaded['hypotheses']):
        mask = hypothesis['mask'][np.ix_(rows, cols)]
        known = hypothesis['known_domain'][np.ix_(rows, cols)]
        if np.any(mask & ~known):
            raise ValueError('Aperture positives cannot occur outside their known prediction domain')
        apertures.append({'id': hypothesis['id'], 'mask': mask, 'known_domain': known,
                          'provenance': {'native_hypothesis': hypothesis['provenance'], 'pixel_mapping': mapping}})
        key = f'aperture_{index:03d}'
        arrays[key+'_mask'] = mask; arrays[key+'_known_domain'] = known
        records.append({key: value for key, value in hypothesis.items() if key not in ('mask', 'known_domain')} |
                       {'array_prefix': key, 'working_mask': _array_record(mask),
                        'working_known_domain': _array_record(known),
                        'native_positive_pixels': int(hypothesis['mask'].sum()),
                        'working_positive_pixels': int(mask.sum())})
    return loaded['report'], apertures, records, arrays, rgb[np.ix_(rows, cols)], mapping


def _projection_arrays(projection):
    full = projection['full_scene']
    arrays = {'face_components': projection['face_components'], 'full_scene_face_index': full.face_index,
              'full_scene_barycentric': full.barycentric, 'full_scene_depth': full.depth}
    records = []
    for piece in projection['pieces']:
        prefix = f"component_{piece['component_id']:04d}"
        for key in ('pixels', 'face_indices', 'barycentric', 'depth'):
            arrays[prefix+'_'+key] = piece[key]
        records.append({'component_id': piece['component_id'], 'array_prefix': prefix,
                        'projected_pixels': len(piece['pixels'])})
    return arrays, records


def _palette(count):
    return np.asarray([np.rint(np.asarray(colorsys.hsv_to_rgb((i*.6180339887498949) % 1, .73, .85))*255)
                       for i in range(count)], dtype=np.uint8)


def _tile(rgb, title):
    image = Image.fromarray(np.asarray(rgb, np.uint8)); image.thumbnail((410, 266), Image.Resampling.NEAREST)
    tile = Image.new('RGB', (420, 300), 'white')
    tile.paste(image, ((420-image.width)//2, 28+(266-image.height)//2))
    ImageDraw.Draw(tile).text((6, 6), title, fill='black')
    return tile


def _visuals(rgb, projection, apertures, records, title):
    colors = _palette(len(projection['pieces'])); full = projection['full_scene']
    scene = np.full_like(rgb, 248); valid = full.face_index >= 0
    scene[valid] = colors[projection['face_components'][full.face_index[valid]]]
    displayed = sorted(projection['pieces'], key=lambda piece: (-len(piece['pixels']), piece['component_id']))[:6]
    display_ids = [piece['component_id'] for piece in displayed]
    outline, has_outline = np.zeros_like(rgb), np.zeros(rgb.shape[:2], bool)
    boundaries = {}
    for piece in displayed:
        mask = np.zeros(rgb.shape[:2], bool); mask.ravel()[piece['pixels']] = True
        edge = mask & ~ndimage.binary_erosion(mask, border_value=0)
        boundaries[piece['component_id']] = edge
        outline[edge] = colors[piece['component_id']]; has_outline |= edge
    traced = rgb.copy(); traced[has_outline] = outline[has_outline]
    alternatives = rgb.copy()
    for index, aperture in enumerate(apertures):
        edge = aperture['mask'] & ~ndimage.binary_erosion(aperture['mask'], border_value=0)
        color = np.asarray(colorsys.hsv_to_rgb((index*.6180339887498949) % 1, .85, .8))*255
        alternatives[edge] = np.rint(color).astype(np.uint8)
    coarse = {}
    for variant in ('full', 'contrast_crop'):
        mask, known = np.zeros(rgb.shape[:2], bool), np.zeros(rgb.shape[:2], bool)
        for aperture, record in zip(apertures, records, strict=True):
            if record['kind'] == 'coarse_component' and record['variant'] == variant:
                mask |= aperture['mask']; known |= aperture['known_domain']
        image = rgb.copy()
        image[~known] = np.rint(.55*image[~known]+.45*np.array([170, 170, 170])).astype(np.uint8)
        image[mask] = np.rint(.6*image[mask]+.4*np.array([20, 160, 240])).astype(np.uint8)
        image[has_outline] = outline[has_outline]
        coarse[variant] = image
    summary = [_tile(rgb, title+' / normalized photo'), _tile(scene, 'All-scene opaque component colors'),
               _tile(coarse['full'], 'FULL coarse UNION + 6 largest-piece outlines'),
               _tile(coarse['contrast_crop'], 'CROP coarse UNION + same outlines; unknown gray')]
    panels = list(summary)
    panels.extend([_tile(traced, 'Display pieces '+','.join(map(str, display_ids))+'; size rank only'),
                   _tile(alternatives, 'ALL aperture outlines; display composite only')])
    for piece in displayed:
        image = rgb.copy(); edge = boundaries[piece['component_id']]
        image[edge] = colors[piece['component_id']]
        panels.append(_tile(image, f"Component {piece['component_id']}; {len(piece['pixels'])} pixels; display only"))
    for aperture, record in zip(apertures, records, strict=True):
        image = rgb.copy(); unknown = ~aperture['known_domain']
        image[unknown] = np.rint(.55*image[unknown]+.45*np.array([170, 170, 170])).astype(np.uint8)
        mask = aperture['mask']
        image[mask] = np.rint(.6*image[mask]+.4*np.array([20, 160, 240])).astype(np.uint8)
        image[has_outline] = outline[has_outline]
        panels.append(_tile(image, record['local_id']+' / unselected'))
    sheet = Image.new('RGB', (1680, 300*((len(panels)+3)//4)), 'white')
    for i, panel in enumerate(panels):
        sheet.paste(panel, ((i % 4)*420, (i//4)*300))
    return sheet, summary, {'method': 'top six by per-component projected pixel count, ties component ID; visualization only',
                            'component_ids': display_ids, 'all_measurements_retained': True,
                            'summary_unions': 'full and contrast_crop coarse hypotheses; display composites, no selected mask'}


def run(corpus, inventory_root, region_root, baseline_report, refinement_report, output):
    paths = [_ordinary_path(p) for p in (corpus, inventory_root, region_root, baseline_report, refinement_report, output)]
    corpus, inventory_root, region_root, baseline_report, refinement_report, output = paths
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a fresh empty component/aperture corpus output')
    if any(output == p or output.is_relative_to(p) for p in (inventory_root, region_root, baseline_report.parent, refinement_report.parent)):
        raise ValueError('Output must be separate from frozen input directories')
    pins = {}; frozen = _read_json(corpus, pins)
    if frozen.get('schema_version') != 1 or sorted(c['id'] for c in frozen['cases']) != sorted(EXPECTED):
        raise ValueError('Expected exactly the five original development designs')
    inventory_corpus = _read_json(inventory_root/'report.json', pins)
    baseline = _read_json(baseline_report, pins)
    _read_json(refinement_report, pins)
    # Freeze all current reconstruction source modules and this runner. This
    # covers transitive local helpers; unrelated docs/data are not code pins.
    for path in [Path(__file__).resolve(), *sorted((ROOT/'reconstruction').glob('*.py'))]:
        _read_pinned(path, pins)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter(); summary_panels = []
    report = {'schema_version': 1, 'method': SCHEMA, 'status': 'running', 'accepted': False,
        'semantic_identity': 'not_inferred', 'quality_verdict': 'unmeasured',
        'policy': {'source_components': 'all frozen exact local-position components, every active primitive including alpha-zero',
            'camera': 'unchanged original-source candidate_projection; no refit or renormalization',
            'apertures': 'all image-only coarse components and all SAM decoder alternatives; no selection',
            'visibility': 'per-component first hit plus all-opaque full-scene diagnostic',
            'depth_tolerance_reference_units': 0.0,
            'depth_reference': 'max axis extent of all referenced source world vertices',
            'display': 'all alternatives separately; summary outline composite has no identity or selection meaning'},
        'packages': {'python': platform.python_version(), 'numpy': np.__version__,
                     'scipy': scipy.__version__, 'Pillow': PIL.__version__},
        'cases': [], 'limitations': ['Development sources and fitted cameras do not establish generalization or independent reconstruction accuracy.',
            'Masks, component connectivity and depth proximity do not assign physical lens identity.',
            'Unknown aperture crop exteriors remain unknown, and every zero-support component/alternative remains in the report.',
            'No optical groups, materials, geometry edits or accepted outputs are selected.',
            'Working-grid point samples can miss narrow parts; hypothetical all-opaque ownership does not simulate optics.']}
    _save_json(output, 'started.json', report)
    for case in frozen['cases']:
        start = time.perf_counter(); folder = output/case['id']; folder.mkdir()
        row = {'id': case['id'], 'status': 'running', 'views': [], 'accepted': False}; report['cases'].append(row)
        try:
            source = _build_source(case, corpus, inventory_root, inventory_corpus, pins)
            membership = _save_npz(folder, 'source-membership.npz', {'face_components': source['face_components']})
            row.update({key: source[key] for key in ('source', 'source_sha256', 'source_snapshot', 'inventory_report',
                                                    'reference_extent', 'referenced_world_bounds', 'component_table')})
            row['source_membership'] = membership
            row['source_face_count'] = len(source['mesh'].faces)
            row['component_count'] = len(source['component_table'])
            region_path = region_root/'cases'/case['id']/'attempt-001/report.json'
            regions = _read_json(region_path, pins)
            if regions['candidate_sha256'] != source['source_sha256'] or {p['id'] for p in regions['photos']} != {'front', 'angled'}:
                raise ValueError('Frozen camera report does not cover this original source and both views')
            for photo in regions['photos']:
                view_start = time.perf_counter(); view_folder = folder/photo['id']; view_folder.mkdir()
                view = {'id': photo['id'], 'status': 'running', 'accepted': False}; row['views'].append(view)
                try:
                    aperture_report, apertures, records, aperture_arrays, rgb, mapping = _photo_evidence(
                        case, corpus, photo, region_path, source, baseline, baseline_report, refinement_report, pins)
                    p = photo['candidate_projection']; normalization = p['normalization']
                    center = np.asarray(normalization['center'], float); extent = normalization['extent']
                    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
                        raise ValueError('Invalid frozen camera normalization')
                    mesh = source['mesh']
                    normalized = TriangleMesh((mesh.vertices-center)/extent, mesh.faces, [])
                    width, height = p['working_size']; scale = extent/source['reference_extent']
                    projection = project_components(normalized, source['face_components'], Camera(**p['camera']),
                                                    (height, width), depth_to_reference=scale)
                    measurements = measure_component_apertures(projection, apertures, depth_tolerance=0.0)
                    arrays, pieces = _projection_arrays(projection)
                    projection_ref = _save_npz(view_folder, 'projection.npz', arrays)
                    aperture_ref = _save_npz(view_folder, 'working-apertures.npz', aperture_arrays)
                    measure_ref = _save_json(view_folder, 'measurements.json', measurements)
                    sheet, panels, display = _visuals(rgb, projection, apertures, records, case['id']+'/'+photo['id'])
                    summary_panels.extend(panels)
                    overlay = _save_image(view_folder, 'all-alternatives.png', sheet)
                    view.update(status='all_hypotheses_measured', source_photo=photo['source'],
                        source_photo_sha256=photo['source_sha256'], candidate_projection=p,
                        camera_region_report={'path': str(region_path), 'sha256': pins[str(region_path)]},
                        pixel_mapping=mapping, depth_to_reference=scale,
                        depth_reference_extent_world=source['reference_extent'],
                        aperture_evidence=aperture_report, apertures=records, working_apertures=aperture_ref,
                        projection=projection_ref, projection_report=projection['report'], projection_pieces=pieces,
                        measurements=measure_ref, overlay=overlay, display=display,
                        summary={'components': len(projection['pieces']), 'apertures': len(apertures),
                            'zero_projection_components': sum(len(piece['pixels']) == 0 for piece in projection['pieces']),
                            'zero_working_apertures': sum(not a['mask'].any() for a in apertures),
                            'nonzero_overlap_component_pairs': len(measurements['pairs']),
                            'zero_overlap_component_pairs': measurements['zero_overlap_pairs']})
                except Exception as error:
                    view.update(status='failed', error_type=type(error).__name__, error=str(error))
                view['seconds'] = time.perf_counter()-view_start
                _save_json(view_folder, 'report.json', view)
                print(json.dumps({'case': case['id'], **{k: view[k] for k in ('id', 'status', 'seconds', 'summary', 'error') if k in view}}), flush=True)
            row['status'] = 'all_views_measured' if len(row['views']) == 2 and all(v['status'] == 'all_hypotheses_measured' for v in row['views']) else 'incomplete_case'
        except Exception as error:
            row.update(status='failed', error_type=type(error).__name__, error=str(error))
        row['seconds'] = time.perf_counter()-start
        _save_json(folder, 'report.json', row)
    if summary_panels:
        sheet = Image.new('RGB', (1680, 300*((len(summary_panels)+3)//4)), 'white')
        for i, panel in enumerate(summary_panels):
            sheet.paste(panel, ((i % 4)*420, (i//4)*300))
        report['contact_sheet'] = _save_image(output, 'contact-sheet.png', sheet)
    changed = []
    for name, digest in pins.items():
        try:
            if _sha(Path(name).read_bytes()) != digest:
                changed.append(name)
        except OSError:
            changed.append(name)
    report.update(status=('invalidated_inputs_changed' if changed else 'corpus_evidence_complete'
                          if all(c['status'] == 'all_views_measured' for c in report['cases']) else 'incomplete_experiment'),
                  input_and_implementation_sha256=pins, changed_inputs=changed,
                  seconds=time.perf_counter()-started)
    _save_json(output, 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=ROOT/'data/refinement-corpus.json')
    parser.add_argument('--inventories', type=Path, default=ROOT/'data/face-partition-corpus-v1')
    parser.add_argument('--regions', type=Path, default=ROOT/'data/region-corpus/fixed-policy-v1')
    parser.add_argument('--baseline-report', type=Path, default=ROOT/'data/photo-semantic-baseline-v1/results-v2/report.json')
    parser.add_argument('--refinement-report', type=Path, default=ROOT/'data/photo-semantic-refinement-v1/report.json')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    report = run(args.corpus, args.inventories, args.regions, args.baseline_report, args.refinement_report, args.output)
    return 0 if report['status'] == 'corpus_evidence_complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
