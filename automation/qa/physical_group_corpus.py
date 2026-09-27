"""Fixed physical-group inference over the frozen five-design evidence.

This consumes the cached component/aperture corpus, the frozen support-union
branches and the captured source inventories. It infers competing partitions
under one fixed ladder policy, composes photographed rays per hypothesis, and
runs the consensus optical candidates through the existing partition and
optical-group preparation bridge. No branch, hypothesis or material is accepted.
"""
from __future__ import annotations

import argparse
import colorsys
from dataclasses import asdict
import io
import itertools
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np
from PIL import Image, ImageDraw
import PIL
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qa.component_aperture_corpus import _build_source
from qa.support_union_corpus import (
    EXPECTED, REPORT_SHA256, VARIANTS, VIEWS, _archive, _array_record, _changed, _identity_sha, _load_case,
    _ordinary, _read, _read_json, _save_image, _save_json, _save_npz, _child,
)
from reconstruction.partition_optical_groups import declarations_for_partition
from reconstruction.partition_stage import run_face_partition
from reconstruction.physical_groups import PhysicalGroupLimits, PhysicalGroupPolicy, infer_physical_groups, partition_declarations_for_hypothesis
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.ray_composition import CLASSES, CODES, compose_view_rays
from reconstruction.support_union import union_apertures


SUPPORT_REPORT_SHA256 = 'ee5e5bd04143e454286b12f4dc1df237d721c9afab2c2f423b7dee52e933fdf6'
METHOD = 'fixed_ladder_physical_groups_v1'
CLASS_COLORS = {'background': (245, 245, 245), 'opaque_only': (120, 120, 120), 'frame_in_front': (200, 60, 40),
                'lens_over_background': (40, 170, 90), 'lens_over_opaque': (230, 160, 30),
                'lens_over_unresolved': (150, 90, 200), 'stacked_optical': (30, 90, 220),
                'unresolved_in_front': (220, 80, 190), 'unresolved_only': (180, 150, 210), 'depth_tie': (0, 0, 0)}
ROLE_COLORS = {'optical_candidate': (30, 170, 70), 'non_optical_evidence': (220, 60, 50),
               'unresolved': (140, 140, 140), 'branch_dependent': (240, 170, 20), 'unobserved': (255, 255, 255)}


def _palette(count):
    return np.asarray([np.rint(np.asarray(colorsys.hsv_to_rgb((i*.6180339887498949) % 1, .65, .9))*255)
                       for i in range(max(count, 1))], np.uint8)


def _panel(rgb, title, lines):
    image = Image.fromarray(np.asarray(rgb, np.uint8))
    image.thumbnail((500, 275), Image.Resampling.NEAREST)
    panel = Image.new('RGB', (520, 360), 'white')
    panel.paste(image, ((520-image.width)//2, 30+(275-image.height)//2))
    draw = ImageDraw.Draw(panel)
    draw.text((8, 8), title, fill='black')
    for index, line in enumerate(lines[:2]):
        draw.text((8, 312+index*16), line, fill='black')
    return panel


def _boundary(mask):
    from scipy import ndimage
    return mask & ~ndimage.binary_erosion(mask, border_value=0)


def _load_depths(evidence, case, pins):
    """Per-view component depths in reference units, beside the cached pixels."""
    depths = {}
    for row in case['views']:
        view_folder = evidence/case['id']/row['id']
        scale = row['depth_to_reference']
        if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not np.isfinite(scale) or scale <= 0:
            raise ValueError('Invalid frozen depth reference scale')
        keys = {'face_components', 'full_scene_face_index', 'full_scene_barycentric', 'full_scene_depth'}
        for item in row['projection_pieces']:
            keys.update(item['array_prefix']+'_'+suffix for suffix in ('pixels', 'face_indices', 'barycentric', 'depth'))
        raw = _read(_child(view_folder, row['projection']['path']), pins, row['projection']['sha256'])
        values = []
        with _archive(raw, keys) as arrays:
            for item in row['projection_pieces']:
                depth = arrays[item['array_prefix']+'_depth']
                if depth.dtype != np.float64 or depth.shape != (item['projected_pixels'],) or not np.isfinite(depth).all():
                    raise ValueError('Invalid cached component depth vector')
                scaled = depth*scale
                scaled.flags.writeable = False
                values.append(scaled)
        depths[row['id']] = {'component_depths': values, 'depth_to_reference': scale,
                             'camera': row['candidate_projection']['camera'], 'rgb_mapping': row['pixel_mapping']}
    return depths


def _support_rows(support_report, case_id):
    case = next(c for c in support_report['cases'] if c['id'] == case_id)
    return [{'id': b['id'], 'selected_component_ids': b['selected_component_ids']}
            for b in case['branches'] if b['status'] == 'support_hypothesis_reported']


def _group_overlay(view_arrays, hypothesis_arrays, hypothesis_report, interpretation):
    shape = view_arrays['shape']
    picture = np.full((*shape, 3), 250, np.uint8)
    palette = _palette(len(hypothesis_arrays['groups']))
    roles = {g['group_id']: g['role_consensus'] for g in hypothesis_report['groups']}
    depth = np.full(shape[0]*shape[1], np.inf); owner = np.full(shape[0]*shape[1], -1, np.int64)
    for index, group in enumerate(hypothesis_arrays['groups']):
        pixels, d = group['footprints'][view_arrays['id']]
        if len(pixels):
            nearer = d < depth[pixels]
            depth[pixels[nearer]] = d[nearer]; owner[pixels[nearer]] = index
    flat = picture.reshape(-1, 3)
    valid = owner >= 0
    flat[valid] = palette[owner[valid]]
    picture = flat.reshape(*shape, 3)
    for index, group in enumerate(hypothesis_arrays['groups']):
        pixels, _ = group['footprints'][view_arrays['id']]
        if not len(pixels):
            continue
        mask = np.zeros(shape[0]*shape[1], bool); mask[pixels] = True
        edge = _boundary(mask.reshape(shape))
        picture[edge] = ROLE_COLORS.get(roles[group['group_id']], (0, 0, 0))
    aperture_edge = _boundary(interpretation['mask'].reshape(shape))
    picture[aperture_edge] = (20, 90, 255)
    return picture


def _class_overlay(class_map, interpretation):
    shape = class_map.shape
    picture = np.zeros((*shape, 3), np.uint8)
    for name, code in CODES.items():
        picture[class_map == code] = CLASS_COLORS[name]
    picture[_boundary(interpretation['mask'].reshape(shape))] = (20, 90, 255)
    return picture


def _bridge(case_folder, source, hypothesis_report, component_table, primitive_labels, declared, pins):
    """Run the actual partition + optical-group preparation for declared groups."""
    folder = case_folder/f"hypothesis-{hypothesis_report['index']:02d}"/'bridge'
    folder.mkdir(parents=True)
    model = Path(source['source'])
    declarations, groups = partition_declarations_for_hypothesis(
        hypothesis_report, component_table, primitive_labels, source_sha256=source['source_sha256'],
        declared_groups=declared, provenance={'policy': 'consensus optical candidates across all branches', 'corpus': METHOD})
    _save_json(folder, 'partition-declarations.json', declarations)
    partition = run_face_partition(model, folder/'partition', declarations=folder/'partition-declarations.json')
    raw = model.read_bytes()
    output_raw = (folder/'partition'/'partitioned.glb').read_bytes()
    receipt = json.loads((folder/'partition'/'receipt.json').read_bytes())
    frame = {'id': 'source-world', 'units': 'unitless', 'up_axis': '+Y', 'forward_axis': '+Z',
             'provenance': {'method': 'unchanged source world coordinates; physical scale/front orientation unverified'}}
    group_declarations = declarations_for_partition(raw, output_raw, receipt, groups, coordinate_frame=frame,
        provenance={'method': METHOD, 'hypothesis_index': hypothesis_report['index'], 'declared_groups': declared,
                    'semantic_identity': 'not_inferred'})
    _save_json(folder, 'group-declarations.json', group_declarations)
    preparation = run_optical_group_preparation(folder/'partition'/'partitioned.glb', folder/'optical-preparation',
                                                grouping_mode='explicit_declarations', declarations=folder/'group-declarations.json')
    return {'status': 'bridge_executed', 'partition_status': partition['status'],
            'output_primitives': receipt['output_primitives'],
            'source_triangle_order_preserved': receipt['preservation']['source_triangle_order'],
            'partition_verification': partition['verification']['status'],
            'declared_groups': declared, 'piece_groups': groups,
            'preparation_status': preparation['status'], 'preparation_reasons': preparation['reasons'],
            'prepared_groups': len(preparation['groups']),
            'reports': {name: {'path': str(folder/name/'report.json'), 'sha256': _identity_sha(json.loads((folder/name/'report.json').read_bytes()))}
                        for name in ('partition', 'optical-preparation')}}


def run(evidence, support, corpus, inventories, output, *, evidence_report_sha256=REPORT_SHA256,
        support_report_sha256=SUPPORT_REPORT_SHA256, execute_bridge=True):
    evidence, support, corpus, inventories, output = (_ordinary(p) for p in (evidence, support, corpus, inventories, output))
    for frozen in (evidence, support, inventories):
        if output == frozen or output.is_relative_to(frozen) or frozen.is_relative_to(output):
            raise ValueError('Output must be separate from frozen evidence directories')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new or empty physical-group experiment output')
    pins = {}
    corpus_report = _read_json(evidence/'report.json', pins, evidence_report_sha256)
    support_report = _read_json(support/'report.json', pins, support_report_sha256)
    if (corpus_report.get('status') != 'corpus_evidence_complete' or [c['id'] for c in corpus_report['cases']] != list(EXPECTED)
            or support_report.get('status') != 'all_support_branches_reported'
            or support_report['evidence_report']['sha256'] != evidence_report_sha256):
        raise ValueError('Expected the complete frozen component/aperture and support-union experiments')
    frozen_corpus = _read_json(corpus, pins)
    inventory_corpus = _read_json(inventories/'report.json', pins)
    for path in [Path(__file__).resolve(), ROOT/'qa/support_union_corpus.py', ROOT/'qa/component_aperture_corpus.py',
                 *sorted((ROOT/'reconstruction').glob('*.py'))]:
        _read(path, pins)
    policy, limits = PhysicalGroupPolicy(), PhysicalGroupLimits()
    started = time.perf_counter()
    report = {'schema_version': 1, 'method': METHOD, 'status': 'running', 'accepted': False,
              'quality_verdict': 'unmeasured', 'semantic_identity': 'not_inferred', 'physical_groups': 'hypotheses_only',
              'evidence_report': {'path': str(evidence/'report.json'), 'sha256': evidence_report_sha256},
              'support_report': {'path': str(support/'report.json'), 'sha256': support_report_sha256},
              'policy': {'ladder': asdict(policy), 'limits': asdict(limits),
                         'interpretations': 'per view, the two coarse crop-policy unions used by the support experiment; SAM alternatives remain unoptimized',
                         'branches': 'the four cross-view crop combinations; roles per branch, consensus across branches',
                         'composition': 'consensus roles; branch-dependent roles treated as unresolved',
                         'bridge': 'consensus optical candidates only; every other face stays in an explicit remainder piece with its source material'},
              'packages': {'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__, 'Pillow': PIL.__version__},
              'cases': [], 'limitations': ['Group hypotheses are projected-coincidence partitions under frozen cameras; they are not verified lens identities.',
                                           'Coarse unions can include reflections, unrelated objects and rims; roles inherit those errors per branch.',
                                           'Fused frame/lens components cannot be split here and appear as mixed or non-optical groups.',
                                           'The bridge exercises representation and preparation only; no material is fitted or accepted.']}
    output.mkdir(parents=True, exist_ok=True)
    _save_json(output, 'started.json', report)
    contact = []
    for case in corpus_report['cases']:
        case_start = time.perf_counter(); folder = output/case['id']; folder.mkdir()
        row = {'id': case['id'], 'source_sha256': case['source_sha256'], 'status': 'running', 'accepted': False,
               'component_count': case['component_count']}
        report['cases'].append(row)
        try:
            loaded = _load_case(evidence, case, pins, evidence_report_sha256)
            depths = _load_depths(evidence, case, pins)
            frozen_case = next(c for c in frozen_corpus['cases'] if c['id'] == case['id'])
            source = _build_source(frozen_case, corpus, inventories, inventory_corpus, pins)
            if source['source_sha256'] != case['source_sha256']:
                raise ValueError('Captured source differs from the frozen evidence source')
            table = case['component_table']
            views, view_arrays = [], {}
            for view_id in VIEWS:
                data = loaded[view_id]
                interpretations = []
                for variant in VARIANTS:
                    union = union_apertures(data['coarse'][variant])
                    interpretations.append({'id': variant, 'mask': union['mask'], 'known_domain': union['known_domain'],
                                            'provenance': {'aperture_union': union['provenance'], 'crop_policy': variant}})
                views.append({'id': view_id, 'shape': list(data['shape']), 'component_pixels': data['component_pixels'],
                              'component_depths': depths[view_id]['component_depths'], 'interpretations': interpretations,
                              'provenance': {**data['provenance'], 'depth_to_reference': depths[view_id]['depth_to_reference'],
                                             'depth_units': 'source referenced-world max-axis extent'}})
                view_arrays[view_id] = {'id': view_id, 'shape': tuple(data['shape']), 'pixels': int(np.prod(data['shape'])),
                                        'component_pixels': data['component_pixels'], 'component_depths': depths[view_id]['component_depths'],
                                        'interpretations': [{'id': i['id'], 'mask': i['mask'].ravel(), 'known': i['known_domain'].ravel(),
                                                             'pixels': int(i['mask'].sum())} for i in interpretations]}
            branches = [{'id': 'front-'+a+'__angled-'+b, 'interpretation_by_view': {'front': a, 'angled': b}}
                        for a, b in itertools.product(VARIANTS, repeat=2)]
            components = [{'component_id': t['component_id'], 'source_face_count': t['source_face_count'],
                           'source_binding': t['source_binding'], 'local_component_id': t['local_component_id']} for t in table]
            inference = infer_physical_groups(components, views, branches, support=_support_rows(support_report, case['id']),
                                              policy=policy, limits=limits)
            row['inference'] = _save_json(folder, 'inference.json', inference['report'])
            # Per-primitive local labels from the frozen global membership.
            primitive_labels = {}
            for t in table:
                key = tuple(t['source_binding'][k] for k in ('node_index', 'mesh_index', 'primitive_index'))
                if key not in primitive_labels:
                    start = t['source_global_face_offset']
                    count = sum(u['source_face_count'] for u in table if u['source_binding'] == t['source_binding'])
                    offset = t['component_id']-t['local_component_id']
                    primitive_labels[key] = source['face_components'][start:start+count]-offset
            row['hypotheses'] = []
            executed_bridges = {}
            for h_report, h_arrays in zip(inference['report']['hypotheses'], inference['hypotheses'], strict=True):
                h_folder = folder/f"hypothesis-{h_report['index']:02d}"; h_folder.mkdir()
                summary = {'index': h_report['index'], 'rungs': h_report['rungs'], 'depth_tolerances': h_report['depth_tolerances'],
                           'group_count': h_report['group_count'], 'observed_group_count': h_report['observed_group_count'],
                           'role_counts': h_report['role_counts'], 'views': [], 'overlays': []}
                consensus_groups = [{'group_id': g['group_id'], 'members': g['members'],
                                     'role': g['role_consensus'] if g['role_consensus'] in ('optical_candidate', 'non_optical_evidence', 'unobserved') else 'unresolved'}
                                    for g in h_report['groups']]
                declared = [g['group_id'] for g in h_report['groups'] if g['role_consensus'] == 'optical_candidate']
                summary['consensus_optical_groups'] = [{'group_id': g['group_id'], 'members': g['members'],
                                                        'source_face_count': g['source_face_count']}
                                                       for g in h_report['groups'] if g['role_consensus'] == 'optical_candidate']
                arrays = {}
                for view_id in VIEWS:
                    va = view_arrays[view_id]
                    composition = compose_view_rays(va, consensus_groups)
                    arrays[view_id+'_class_map'] = composition['class_map']
                    arrays[view_id+'_nearest_optical_group'] = composition['nearest_optical_group']
                    arrays[view_id+'_optical_layers'] = composition['optical_layers']
                    summary['views'].append({'view_id': view_id, 'composition': composition['report']})
                    for interpretation in va['interpretations']:
                        title = f"{case['id']} / {view_id} / H{h_report['index']} rungs {h_report['rungs']} / {interpretation['id']}"
                        group_picture = _group_overlay(va, h_arrays, h_report, interpretation)
                        class_picture = _class_overlay(composition['class_map'], interpretation)
                        lines = ['Fill: group; outline: role (green optical, red non-optical, gray unresolved, orange branch-dependent); blue: aperture',
                                 'Classes: green lens/background, orange lens/opaque, purple lens/unresolved, red frame front, gray opaque, blue stacked']
                        g_panel = _panel(group_picture, title+' groups', lines[:1]); c_panel = _panel(class_picture, title+' rays', lines[1:])
                        summary['overlays'].append({'view_id': view_id, 'interpretation_id': interpretation['id'],
                                                    'groups': _save_image(h_folder, f'{view_id}-{interpretation["id"]}-groups.png', g_panel),
                                                    'rays': _save_image(h_folder, f'{view_id}-{interpretation["id"]}-rays.png', c_panel)})
                        if interpretation['id'] == 'full':
                            contact.extend([g_panel, c_panel])
                summary['composition_arrays'] = _save_npz(h_folder, 'composition.npz', arrays)
                summary['composition_array_records'] = {k: _array_record(v) for k, v in arrays.items()}
                summary['composition_table'] = [
                    {'view_id': v['view_id'], 'interpretation_id': i['interpretation_id'],
                     **{k: i[k] for k in ('clean_transmission_fraction', 'rear_content_fraction', 'excluded_fraction',
                                          'aperture_without_geometry_fraction', 'aperture_on_opaque_only_fraction',
                                          'optical_outside_aperture_known_pixels')}}
                    for v in summary['views'] for i in v['composition']['interpretations']]
                membership_key = _identity_sha([sorted(g['members']) for g in summary['consensus_optical_groups']])
                summary['declared_membership_sha256'] = membership_key
                if execute_bridge and declared and membership_key in executed_bridges:
                    summary['bridge'] = {'status': 'identical_declaration_already_executed',
                                         'executed_in_hypothesis': executed_bridges[membership_key], 'declared_groups': declared}
                elif execute_bridge and declared:
                    try:
                        summary['bridge'] = _bridge(folder, source, h_report, table, primitive_labels, declared, pins)
                        executed_bridges[membership_key] = h_report['index']
                    except Exception as error:
                        summary['bridge'] = {'status': 'failed', 'error_type': type(error).__name__, 'error': str(error), 'declared_groups': declared}
                else:
                    summary['bridge'] = {'status': 'not_executed', 'reason': 'no consensus optical candidate' if not declared else 'disabled', 'declared_groups': declared}
                row['hypotheses'].append(summary)
                _save_json(h_folder, 'report.json', summary)
                print(json.dumps({'case': case['id'], 'hypothesis': h_report['index'], 'rungs': h_report['rungs'],
                                  'groups': h_report['group_count'], 'observed': h_report['observed_group_count'],
                                  'roles': h_report['role_counts'], 'bridge': summary['bridge']['status']}), flush=True)
            row.update(status='hypotheses_reported', hypothesis_count=inference['report']['hypothesis_count'],
                       candidate_pair_count=inference['report']['candidate_pair_count'],
                       rung_dependent_pair_count=inference['report']['rung_dependent_pair_count'],
                       unobserved_component_count=len(inference['report']['unobserved_component_ids']))
        except Exception as error:
            row.update(status='failed', error_type=type(error).__name__, error=str(error))
        row['seconds'] = time.perf_counter()-case_start
        _save_json(folder, 'report.json', row)
        print(json.dumps({k: row[k] for k in ('id', 'status', 'seconds', 'hypothesis_count', 'error') if k in row}), flush=True)
    if contact:
        sheet = Image.new('RGB', (1040, 360*((len(contact)+1)//2)), 'white')
        for index, panel in enumerate(contact):
            sheet.paste(panel, ((index % 2)*520, (index//2)*360))
        report['contact_sheet'] = _save_image(output, 'contact-sheet.png', sheet)
    changed = _changed(pins)
    report.update(status='invalidated_inputs_changed' if changed else 'all_cases_reported'
                  if all(c['status'] == 'hypotheses_reported' for c in report['cases']) else 'incomplete_experiment',
                  changed_inputs=changed, input_and_implementation_sha256=pins, seconds=time.perf_counter()-started)
    _save_json(output, 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=ROOT/'data/component-aperture-corpus-v1')
    parser.add_argument('--support', type=Path, default=ROOT/'data/support-union-corpus-v1')
    parser.add_argument('--corpus', type=Path, default=ROOT/'data/refinement-corpus.json')
    parser.add_argument('--inventories', type=Path, default=ROOT/'data/face-partition-corpus-v1')
    parser.add_argument('--evidence-report-sha256', default=REPORT_SHA256)
    parser.add_argument('--support-report-sha256', default=SUPPORT_REPORT_SHA256)
    parser.add_argument('--no-bridge', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = run(args.evidence, args.support, args.corpus, args.inventories, args.output,
                 evidence_report_sha256=args.evidence_report_sha256, support_report_sha256=args.support_report_sha256,
                 execute_bridge=not args.no_bridge)
    return 0 if result['status'] == 'all_cases_reported' else 1


if __name__ == '__main__':
    raise SystemExit(main())
