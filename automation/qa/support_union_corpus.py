"""Four fixed coarse-aperture support experiments per frozen development model.

This consumes cached projection and aperture arrays. It does not rerender, select
SAM alternatives, infer physical groups, or choose an overall winning branch.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import itertools
import json
from pathlib import Path
import platform
import stat
import sys
import time
import zipfile

import numpy as np
from PIL import Image, ImageDraw
import PIL
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXPECTED = ('rayban', 'miumiu', 'oakley', 'invu', 'victoria-beckham')
VIEWS = ('front', 'angled')
VARIANTS = ('full', 'contrast_crop')
REPORT_SHA256 = 'c95a7908278305367cb6d8a5161a462f2437c0516d3e319247b6d44cfff992ef'
RATIO_GAP = .001
TIME_LIMIT = 30.0
MAXIMUM_ITERATIONS = 20
MAXIMUM_FILE_BYTES = 256 * 1024 * 1024
MAXIMUM_NPZ_BYTES = 512 * 1024 * 1024


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode('utf-8')


def _identity_sha(value):
    return _sha(json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8'))


def _ordinary(path):
    path = Path(path).absolute()
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
            raise ValueError('Support experiment paths cannot contain links or reparse points')
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError('Support experiment requires ordinary files/directories')
    return path.resolve()


def _child(folder, relative):
    path = _ordinary(folder/relative)
    if not path.is_relative_to(folder):
        raise ValueError('Artifact path escapes its folder')
    return path


def _read(path, pins, expected=None):
    path = _ordinary(path)
    if path.stat().st_size > MAXIMUM_FILE_BYTES:
        raise ValueError('Frozen input exceeds explicit file capacity')
    raw = path.read_bytes()
    digest = _sha(raw)
    if (expected is not None and digest != expected) or (str(path) in pins and pins[str(path)] != digest):
        raise ValueError(f'Frozen input changed: {path}')
    pins[str(path)] = digest
    return raw


def _read_json(path, pins, expected=None):
    return json.loads(_read(path, pins, expected))


def _write(folder, name, raw):
    path = _child(folder, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
    return {'path': path.relative_to(folder).as_posix(), 'sha256': _sha(raw)}


def _save_json(folder, name, value):
    return _write(folder, name, _json_bytes(value))


def _save_npz(folder, name, arrays):
    stream = io.BytesIO()
    np.savez_compressed(stream, **arrays)
    return _write(folder, name, stream.getvalue())


def _save_image(folder, name, image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return _write(folder, name, stream.getvalue())


def _array_record(value):
    value = np.asarray(value)
    return {'sha256': _sha(np.ascontiguousarray(value).tobytes()),
            'dtype': value.dtype.str, 'shape': list(value.shape),
            'encoding': 'C_order_numeric_array_bytes'}


def _projection_array_hash(value):
    """Match component_projection._hash, including its default JSON spaces."""
    value = np.ascontiguousarray(value)
    prefix = json.dumps({'dtype': value.dtype.str, 'shape': value.shape}, sort_keys=True).encode()
    return _sha(prefix+value.tobytes())


def _verify_array(value, record):
    if _array_record(value) != record:
        raise ValueError('Frozen working array differs from its declared snapshot')


def _archive(raw, keys):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (len(entries) != len(keys) or {e.filename for e in entries} != {k+'.npy' for k in keys}
                or sum(e.file_size for e in entries) > MAXIMUM_NPZ_BYTES):
            raise ValueError('Frozen NPZ has incomplete arrays or exceeds explicit capacity')
    return np.load(io.BytesIO(raw), allow_pickle=False)


def _changed(pins):
    changed = []
    for name, digest in pins.items():
        try:
            if _sha(_ordinary(name).read_bytes()) != digest:
                changed.append(name)
        except (OSError, ValueError):
            changed.append(name)
    return changed


def _load_case(evidence, case, pins, root_sha):
    """Validate cached identity and full component coverage, without reading GLBs."""
    folder = evidence/case['id']
    saved = _read_json(folder/'report.json', pins)
    if saved != case or case['status'] != 'all_views_measured' or case['accepted'] is not False:
        raise ValueError('Frozen case report differs from its corpus record')
    table = case['component_table']
    count = case['component_count']
    if not 1 <= count <= 1024 or [c['component_id'] for c in table] != list(range(count)):
        raise ValueError('Complete canonical source component table is required')
    if sum(c['source_face_count'] for c in table) != case['source_face_count']:
        raise ValueError('Component face coverage differs from source face count')
    if len(case['views']) != 2 or {v['id'] for v in case['views']} != set(VIEWS):
        raise ValueError('Expected both frozen source cameras')
    loaded, membership_sha = {}, None
    for view_id in VIEWS:
        row = next(v for v in case['views'] if v['id'] == view_id)
        view_folder = folder/view_id
        if _read_json(view_folder/'report.json', pins) != row or row['status'] != 'all_hypotheses_measured':
            raise ValueError('Frozen view report differs from its case record')
        projection = row['candidate_projection']
        shape = tuple(row['projection_report']['shape'])
        if (len(shape) != 2 or any(type(n) is not int or n < 2 for n in shape)
                or np.prod(shape) > 262144 or list(shape[::-1]) != projection['working_size']
                or list(shape[::-1]) != row['pixel_mapping']['working_size_xy']
                or projection['candidate_binding'] != 'original'
                or projection['candidate_sha256'] != case['source_sha256']
                or projection['camera_sha256'] != _identity_sha(projection['camera'])
                or row['projection_report']['camera'] != projection['camera']
                or row['projection_report']['component_count'] != count
                or row['source_photo_sha256'] != row['pixel_mapping']['source_photo_sha256']
                or row['source_photo_sha256'] != row['aperture_evidence']['source_sha256']):
            raise ValueError('Camera, source, grid or photograph bindings disagree')
        records = row['projection_pieces']
        if [x['component_id'] for x in records] != list(range(count)):
            raise ValueError('Projection omits a canonical source component')
        keys = {'face_components', 'full_scene_face_index', 'full_scene_barycentric', 'full_scene_depth'}
        for item in records:
            keys.update(item['array_prefix']+'_'+suffix for suffix in ('pixels', 'face_indices', 'barycentric', 'depth'))
        raw = _read(_child(view_folder, row['projection']['path']), pins, row['projection']['sha256'])
        pixels, pixel_records = [], []
        with _archive(raw, keys) as arrays:
            labels = arrays['face_components']
            if (labels.dtype != np.int64 or labels.shape != (case['source_face_count'],)
                    or np.any(labels < 0) or np.any(labels >= count)
                    or not np.array_equal(np.bincount(labels, minlength=count), [c['source_face_count'] for c in table])):
                raise ValueError('Cached face labels do not cover the complete component table')
            labels_sha = _projection_array_hash(labels)
            if labels_sha != row['projection_report']['face_components_sha256'] or membership_sha not in (None, labels_sha):
                raise ValueError('Source face membership changes between projections')
            membership_sha = labels_sha
            for item in records:
                values = arrays[item['array_prefix']+'_pixels']
                face_indices = arrays[item['array_prefix']+'_face_indices']
                if (values.dtype != np.int64 or values.ndim != 1
                        or len(values) != item['projected_pixels']
                        or (len(values) and (values[0] < 0 or values[-1] >= np.prod(shape)
                                           or np.any(values[1:] <= values[:-1])))
                        or face_indices.dtype != np.int64 or face_indices.shape != values.shape
                        or np.any(face_indices < 0) or np.any(face_indices >= len(labels))
                        or np.any(labels[face_indices] != item['component_id'])):
                    raise ValueError('Invalid complete component pixel/source-face binding')
                values.flags.writeable = False
                pixels.append(values)
                pixel_records.append({'component_id': item['component_id'], 'pixels': _array_record(values)})
        aps = row['apertures']
        if len(aps) > 256 or len({a['id'] for a in aps}) != len(aps):
            raise ValueError('Unsupported or duplicate frozen aperture ledger')
        keys = {'native_row_indices', 'native_column_indices'}
        for item in aps:
            keys.update((item['array_prefix']+'_mask', item['array_prefix']+'_known_domain'))
        raw = _read(_child(view_folder, row['working_apertures']['path']), pins, row['working_apertures']['sha256'])
        coarse = {variant: [] for variant in VARIANTS}
        with _archive(raw, keys) as arrays:
            nw, nh = row['pixel_mapping']['native_size_xy']
            hh, ww = shape
            expected_rows = np.floor((np.arange(hh)+.5)*nh/hh).astype(np.int64)
            expected_cols = np.floor((np.arange(ww)+.5)*nw/ww).astype(np.int64)
            if (not np.array_equal(arrays['native_row_indices'], expected_rows)
                    or not np.array_equal(arrays['native_column_indices'], expected_cols)):
                raise ValueError('Cached working/native pixel-center mapping differs')
            for item in aps:
                mask = arrays[item['array_prefix']+'_mask']
                known = arrays[item['array_prefix']+'_known_domain']
                _verify_array(mask, item['working_mask'])
                _verify_array(known, item['working_known_domain'])
                if (mask.dtype != bool or known.dtype != bool or mask.shape != shape or known.shape != shape
                        or np.any(mask & ~known) or int(mask.sum()) != item['working_positive_pixels']
                        or item['provenance']['source_sha256'] != row['source_photo_sha256']
                        or item['provenance']['normalized_image_sha256'] != row['pixel_mapping']['normalized_image_sha256']):
                    raise ValueError('Aperture array/image binding differs')
                mask.flags.writeable = known.flags.writeable = False
                if item['kind'] == 'coarse_component':
                    if item['variant'] not in coarse or item['decoder_index'] is not None:
                        raise ValueError('Unsupported coarse variant')
                    coarse[item['variant']].append({'id': item['id'], 'mask': mask, 'known_domain': known,
                        'provenance': {'frozen_aperture': item, 'working_apertures': row['working_apertures']}})
                elif item['kind'] != 'sam_alternative':
                    raise ValueError('Unknown aperture hypothesis kind')
        if any(not coarse[variant] for variant in VARIANTS):
            raise ValueError('Both fixed crop policies require coarse components')
        provenance = {'corpus_report_sha256': root_sha,
            'case_report_sha256': pins[str(folder/'report.json')],
            'view_report_sha256': pins[str(view_folder/'report.json')],
            'source_sha256': case['source_sha256'], 'source_component_table_sha256': _identity_sha(table),
            'source_face_membership_sha256': membership_sha,
            'source_face_membership_hash_encoding': 'SHA256(default-separator sort-key JSON {dtype,shape} UTF-8 + C-order numeric array bytes); component_projection._hash convention',
            'source_bindings': table, 'candidate_projection': projection,
            'candidate_projection_sha256': _identity_sha(projection),
            'source_photo_sha256': row['source_photo_sha256'],
            'camera_region_report': row['camera_region_report'],
            'pixel_mapping': row['pixel_mapping'], 'pixel_mapping_sha256': _identity_sha(row['pixel_mapping']),
            'projection': row['projection'], 'working_apertures': row['working_apertures'],
            'component_pixels': pixel_records,
            'upstream_verification_scope': 'Historical source/photo/camera hashes retained from verified frozen receipts; their source files are not reread or recomputed in this experiment.'}
        loaded[view_id] = {'id': view_id, 'shape': shape, 'component_pixels': pixels,
            'coarse': coarse, 'provenance': provenance,
            'unoptimized_sam_alternatives': [a for a in aps if a['kind'] == 'sam_alternative']}
    return loaded


def _support_outputs(views, result, table):
    """Independently recount exact union metrics and every conditional toggle."""
    chosen = result['selected_component_ids']
    count = len(table)
    if (not isinstance(chosen, list) or any(type(c) is not int or not 0 <= c < count for c in chosen)
            or len(set(chosen)) != len(chosen)):
        raise ValueError('Solver returned invalid source component IDs')
    chosen_set = set(chosen)
    arrays, metrics, panels = {}, [], []
    ledger = [{**item, 'selected_for_this_support_hypothesis': item['component_id'] in chosen_set,
               'views': [], 'accepted': False} for item in table]
    for view in views:
        multiplicity = np.zeros(view['shape'], np.int32)
        for component in chosen:
            multiplicity.ravel()[view['component_pixels'][component]] += 1
        support = multiplicity > 0
        mask, known = view['mask'], view['known_domain']
        inside = int(np.count_nonzero(support & mask))
        false_positive = int(np.count_nonzero(support & known & ~mask))
        false_negative = int(np.count_nonzero(mask & ~support))
        denominator = inside+false_positive+false_negative
        metric = {'view_id': view['id'], 'intersection_pixels': inside,
            'false_positive_pixels': false_positive, 'false_negative_pixels': false_negative,
            'aperture_pixels': int(mask.sum()), 'union_pixels': denominator,
            'iou': inside/denominator, 'known_domain_pixels': int(known.sum()),
            'projected_known_pixels': int(np.count_nonzero(support & known)),
            'projected_unknown_pixels': int(np.count_nonzero(support & ~known)),
            'projected_pixels': int(support.sum())}
        metrics.append(metric)
        saved_metric = next(x for x in result['metrics'] if x['view_id'] == view['id'])
        for key, value in metric.items():
            if key == 'iou':
                if abs(saved_metric[key]-value) > 1e-12:
                    raise ValueError('Solver IoU differs from independently recounted union')
            elif saved_metric[key] != value:
                raise ValueError(f'Solver metric {key} differs from independently recounted union')
        for component, pixels in enumerate(view['component_pixels']):
            known_pixels = pixels[known.ravel()[pixels]]
            change = np.count_nonzero(multiplicity.ravel()[known_pixels] == (1 if component in chosen_set else 0))
            ledger[component]['views'].append({'id': view['id'], 'projected_pixels': len(pixels),
                'projected_known_pixels': len(known_pixels), 'projected_unknown_pixels': len(pixels)-len(known_pixels),
                'inside_aperture_pixels': int(mask.ravel()[known_pixels].sum()),
                'scored_union_pixels_changed_by_individual_toggle': int(change)})
        prefix = view['id']
        arrays[prefix+'_support_mask'] = support
        arrays[prefix+'_aperture_mask'] = mask
        arrays[prefix+'_known_domain'] = known
        arrays[prefix+'_selected_component_multiplicity'] = multiplicity
        # A support/aperture overlay uses only cached grids; no photo or new render.
        picture = np.full((*view['shape'], 3), 245, np.uint8)
        picture[~known] = [155, 155, 155]
        picture[support & ~known] = [190, 150, 210]
        picture[support & mask] = [35, 155, 85]
        picture[support & known & ~mask] = [225, 70, 60]
        picture[mask & ~support] = [50, 125, 225]
        panels.append((view['id'], picture))
    for item in ledger:
        item['zero_known_support'] = all(v['projected_known_pixels'] == 0 for v in item['views'])
        item['individual_toggle_leaves_scored_union_unchanged'] = all(v['scored_union_pixels_changed_by_individual_toggle'] == 0 for v in item['views'])
        item['conditional_operation'] = 'remove' if item['selected_for_this_support_hypothesis'] else 'add'
    checks = {'zero_known_support_component_ids': [x['component_id'] for x in ledger if x['zero_known_support']],
        'addable_without_scored_union_change': [x['component_id'] for x in ledger
            if not x['selected_for_this_support_hypothesis'] and x['individual_toggle_leaves_scored_union_unchanged']],
        'individually_removable_without_scored_union_change': [x['component_id'] for x in ledger
            if x['selected_for_this_support_hypothesis'] and x['individual_toggle_leaves_scored_union_unchanged']]}
    for key, value in checks.items():
        if sorted(result[key]) != value:
            raise ValueError(f'Solver conditional ambiguity {key} differs from independent recount')
    if abs(result['minimum_iou']-min(x['iou'] for x in metrics)) > 1e-12:
        raise ValueError('Solver worst-view IoU differs from independent recount')
    return arrays, ledger, metrics, panels


def _panel(rgb, title):
    image = Image.fromarray(rgb)
    image.thumbnail((500, 275), Image.Resampling.NEAREST)
    panel = Image.new('RGB', (520, 345), 'white')
    panel.paste(image, ((520-image.width)//2, 30+(275-image.height)//2))
    draw = ImageDraw.Draw(panel)
    draw.text((8, 8), title, fill='black')
    draw.text((8, 310), 'Green: overlap; red: extra support; blue: unexplained aperture', fill='black')
    draw.text((8, 327), 'Gray: unknown; purple: support in unknown; cached grids only', fill='black')
    return panel


def run(evidence, output, *, evidence_report_sha256=REPORT_SHA256):
    evidence, output = _ordinary(evidence), _ordinary(output)
    if output == evidence or output.is_relative_to(evidence) or evidence.is_relative_to(output):
        raise ValueError('Output must be separate from the frozen evidence directory')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new or empty support-union experiment output')
    pins = {}
    corpus = _read_json(evidence/'report.json', pins, evidence_report_sha256)
    if (corpus.get('status') != 'corpus_evidence_complete' or corpus.get('accepted') is not False
            or corpus.get('changed_inputs') or corpus.get('method') != 'all_source_components_frozen_image_apertures_v1'
            or [c['id'] for c in corpus['cases']] != list(EXPECTED)):
        raise ValueError('Expected the complete frozen five-case component/aperture experiment')
    for path in (Path(__file__).resolve(), ROOT/'reconstruction/__init__.py', ROOT/'reconstruction/support_union.py'):
        _read(path, pins)
    from reconstruction.support_union import SupportLimits, optimize_support, union_apertures
    if _changed(pins):
        raise ValueError('Inputs or implementation changed while importing support optimizer')
    limits = SupportLimits()
    started = time.perf_counter()
    report = {'schema_version': 1, 'method': 'fixed_four_coarse_crop_support_branches_v1',
        'status': 'running', 'accepted': False, 'quality_verdict': 'unmeasured', 'physical_groups': 'not_inferred',
        'evidence_report': {'path': str(evidence/'report.json'), 'sha256': evidence_report_sha256},
        'policy': {'views': list(VIEWS), 'crop_policies': list(VARIANTS),
            'branches_per_case': 4, 'total_solve_cap': 20,
            'apertures': 'Union every coarse component within one crop policy per view; enumerate the four cross-view crop combinations.',
            'restricted_interpretation_coverage': 'Only coarse hypotheses optimized. All SAM alternatives remain pinned and listed as unoptimized evidence. No complete interpretation search is claimed.',
            'components': 'All canonical source components, including zero-known and zero-projection components.',
            'ratio_gap': RATIO_GAP, 'time_limit_seconds_per_branch': TIME_LIMIT,
            'maximum_iterations': MAXIMUM_ITERATIONS, 'limits': asdict(limits),
            'selection_scope': 'One conditional support set per branch; every branch retained, no overall winner or accepted mask.',
            'display': 'Cached-grid support/aperture comparison; no photo, geometry or neural rerun.'},
        'packages': {'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__, 'Pillow': PIL.__version__},
        'cases': [], 'limitations': ['Union support does not identify physical lens groups, layers, hardware or materials.',
            'Numerical solver bounds are conditional on the fixed cameras, cached pixel-center footprints and chosen coarse branch.',
            'Zero-known components and individually redundant toggles are ambiguous; conditional toggle equivalence is not joint or global support uniqueness.',
            'Historical source/photo hashes are report-bound provenance; this driver reads only cached reports/arrays and current implementation.',
            'This development corpus does not establish generalization or accurate optical appearance.']}
    output.mkdir(parents=True, exist_ok=True)
    _save_json(output, 'started.json', report)
    contact = []
    for case in corpus['cases']:
        folder = output/case['id']
        folder.mkdir()
        row = {'id': case['id'], 'source_sha256': case['source_sha256'], 'status': 'running', 'accepted': False,
               'component_count': case['component_count'], 'branches': []}
        report['cases'].append(row)
        try:
            loaded = _load_case(evidence, case, pins, evidence_report_sha256)
            row['source_component_table'] = case['component_table']
            row['unoptimized_sam_alternatives'] = {v: loaded[v]['unoptimized_sam_alternatives'] for v in VIEWS}
            for variants in itertools.product(VARIANTS, repeat=2):
                branch_id = 'front-'+variants[0]+'__angled-'+variants[1]
                branch_folder = folder/branch_id
                branch_folder.mkdir()
                branch = {'id': branch_id, 'status': 'running', 'accepted': False,
                          'crop_policy_by_view': dict(zip(VIEWS, variants, strict=True))}
                row['branches'].append(branch)
                branch_start = time.perf_counter()
                try:
                    if _changed(pins):
                        raise ValueError('Consumed inputs or implementation changed before branch solve')
                    views = []
                    for view_id, variant in zip(VIEWS, variants, strict=True):
                        data = loaded[view_id]
                        union = union_apertures(data['coarse'][variant])
                        views.append({'id': view_id, 'shape': data['shape'], 'component_pixels': data['component_pixels'],
                            'mask': union['mask'], 'known_domain': union['known_domain'],
                            'provenance': {**data['provenance'], 'crop_policy': variant,
                                           'aperture_union': union['provenance'], 'restricted_interpretation_coverage': 'coarse only'}})
                    result = optimize_support(views, ratio_gap=RATIO_GAP, time_limit=TIME_LIMIT,
                        maximum_iterations=MAXIMUM_ITERATIONS, limits=limits)
                    # Preserve the solver outcome even if independent artifact validation fails.
                    branch['solver_result'] = _save_json(branch_folder, 'solver-result.json', result)
                    branch['solver_status'] = result['status']
                    arrays, ambiguity, metrics, panels = _support_outputs(views, result, case['component_table'])
                    selected = {'schema_version': 1, 'accepted': False, 'physical_groups': 'not_inferred',
                        'scope': 'Conditional projected-union support only; no overall branch selection.',
                        'source_sha256': case['source_sha256'], 'source_component_table_sha256': _identity_sha(case['component_table']),
                        'selected_component_ids': result['selected_component_ids'],
                        'selected_components': [case['component_table'][c] for c in result['selected_component_ids']],
                        'views': [{'id': v['id'], 'provenance': v['provenance'], 'mask': _array_record(v['mask']),
                                   'known_domain': _array_record(v['known_domain'])} for v in views]}
                    branch['selected_support'] = _save_json(branch_folder, 'selected-support.json', selected)
                    branch['conditional_ambiguity'] = _save_json(branch_folder, 'conditional-ambiguity.json', {
                        'scope': 'Each individual toggle against this returned support; not simultaneous toggles, global uniqueness or physical identity.',
                        'source_sha256': case['source_sha256'], 'component_count': len(ambiguity), 'components': ambiguity,
                        'accepted': False, 'physical_groups': 'not_inferred'})
                    branch['union_arrays'] = _save_npz(branch_folder, 'unions.npz', arrays)
                    branch['union_array_records'] = {name: _array_record(array) for name, array in arrays.items()}
                    branch['overlays'] = []
                    for view_id, rgb in panels:
                        title = f"{case['id']} / {view_id} / {branch['crop_policy_by_view'][view_id]}"
                        panel = _panel(rgb, title)
                        branch['overlays'].append({'view_id': view_id, **_save_image(branch_folder, view_id+'-overlay.png', panel)})
                        contact.append(panel)
                    branch.update(status='support_hypothesis_reported', selected_component_ids=result['selected_component_ids'],
                        metrics=metrics, minimum_iou=result['minimum_iou'], mean_iou=result['mean_iou'],
                        upper_bound=result['upper_bound'], remaining_gap=result['remaining_gap'],
                        zero_known_support_component_ids=result['zero_known_support_component_ids'],
                        addable_without_scored_union_change=result['addable_without_scored_union_change'],
                        individually_removable_without_scored_union_change=result['individually_removable_without_scored_union_change'])
                except Exception as error:
                    branch.update(status='failed', error_type=type(error).__name__, error=str(error))
                branch['seconds'] = time.perf_counter()-branch_start
                _save_json(branch_folder, 'report.json', branch)
                print(json.dumps({'case': case['id'], **{k: branch[k] for k in ('id', 'status', 'solver_status', 'minimum_iou', 'remaining_gap', 'seconds', 'error') if k in branch}}), flush=True)
            row['status'] = 'all_four_branches_reported' if all(b['status'] == 'support_hypothesis_reported' for b in row['branches']) else 'incomplete_case'
        except Exception as error:
            row.update(status='failed', error_type=type(error).__name__, error=str(error))
        _save_json(folder, 'report.json', row)
    if contact:
        sheet = Image.new('RGB', (1040, 345*((len(contact)+1)//2)), 'white')
        for index, panel in enumerate(contact):
            sheet.paste(panel, ((index % 2)*520, (index//2)*345))
        report['contact_sheet'] = _save_image(output, 'contact-sheet.png', sheet)
    report['overlay_legend'] = {'green': 'support and aperture', 'red': 'support in known negative aperture area',
        'blue': 'aperture unexplained by support', 'gray': 'unknown prediction domain',
        'purple': 'support outside known domain', 'white': 'known negative and no support'}
    changed = _changed(pins)
    report.update(status='invalidated_inputs_changed' if changed else 'all_support_branches_reported'
        if all(c['status'] == 'all_four_branches_reported' for c in report['cases']) else 'incomplete_experiment',
        changed_inputs=changed, input_and_implementation_sha256=pins, seconds=time.perf_counter()-started)
    _save_json(output, 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=ROOT/'data/component-aperture-corpus-v1')
    parser.add_argument('--evidence-report-sha256', default=REPORT_SHA256)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.evidence, args.output, evidence_report_sha256=args.evidence_report_sha256)
    return 0 if report['status'] == 'all_support_branches_reported' else 1


if __name__ == '__main__':
    raise SystemExit(main())
