"""Recover one narrow preparation failure with guarded, source-preserving normals.

This is a conditional smooth-normal hypothesis, not a semantic or appearance
acceptance. It cannot alter geometry, delete faces or relax the optical exporter.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from .deform_glb import _read_bytes
from .lens_appearance import DensityKeyframe, LensAppearance
from .mesh import load_glb_bytes
from .optical_group_asset import (_legacy_optical, _prepared, _source_matrices, _tie_input,
                                  read_optical_group_candidate, write_optical_group_candidate)
from .optical_group_runtime import validate_effective_optical_runtime
from .photo_lens_observations import _child, _read_pinned
from .prepare_optical_groups import _json_bytes, _write, validate_group_declarations
from .refine_photos import implementation_manifest
from .smooth_optical_geometry import propose_smooth_optical_group


METHOD = 'bounded_smooth_normal_export_recovery_v1'
_PREFIX = 'export_unsupported: Actual float32 optical runtime/coincident-patch contract unsupported: '
_NORMAL_FAILURES = {'identical_triangle_uniform_corner_direction_sign',
                    'conflicting_corresponding_corner_directions'}
_ARRAYS = ('positions', 'indices', 'normals', 'uv')


def _failure(report):
    if (report.get('status') != 'unsupported_optical_group_preparation'
            or report.get('model') is not None or report.get('export') is not None
            or report.get('accepted') is not False
            or report.get('material_identification') != 'not_attempted'
            or report.get('selected_material') is not None):
        raise ValueError('Recovery requires a failed neutral optical export without a candidate')
    reasons = report.get('reasons', [])
    if len(reasons) != 1 or not isinstance(reasons[0], str) or not reasons[0].startswith(_PREFIX):
        raise ValueError('Only a coincident-triangle normal-conflict export failure is recoverable')
    failures = json.loads(reasons[0][len(_PREFIX):])
    if (not isinstance(failures, list) or not failures or
            any(not isinstance(reason, str) or reason not in _NORMAL_FAILURES for reason in failures)):
        raise ValueError('Export failure includes an unsupported non-normal condition')
    return set(failures)


def recover_smooth_failed_preparation(preparation_report, output):
    """Return a regular preparation report after a strictly verified normal repair.

    All inputs are pinned and the original failure is reproduced. The existing
    default smooth fit policy and strict exporter/reader run unchanged. The
    output must be a new or empty directory that does not contain the input.
    Unsupported fits and malformed or changed artifacts raise ValueError.
    """
    source_report, output = Path(preparation_report).resolve(), Path(output).resolve()
    folder = source_report.parent
    if output == folder or folder.is_relative_to(output):
        raise ValueError('Recovery output must not contain its input preparation')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new or empty recovery output')
    pins = {}
    raw_report = _read_pinned(source_report, pins)
    original = json.loads(raw_report)
    failures = _failure(original)

    def read(reference):
        if not isinstance(reference, dict) or not isinstance(reference.get('sha256'), str):
            raise ValueError('Recovery artifact requires a pinned path and SHA-256')
        return _read_pinned(_child(folder, reference['path']), pins, reference['sha256'])

    source_bytes = read(original['source_snapshot'])
    if hashlib.sha256(source_bytes).hexdigest() != original['source_sha256']:
        raise ValueError('Prepared source hash mismatch')
    _, document, _ = _read_bytes(source_bytes)
    mesh = load_glb_bytes(source_bytes)
    inventory = original.get('grouping_inventory')
    if not isinstance(inventory, list) or not inventory or not original.get('groups'):
        raise ValueError('Missing failed preparation group inventory')
    declaration_bytes = read(original['declarations']) if original.get('declarations') else None
    if declaration_bytes is not None:
        declaration = validate_group_declarations(json.loads(declaration_bytes))
        if (declaration['source_sha256'] != original['source_sha256']
                or declaration['coordinate_frame'] != original['coordinate_frame']
                or declaration['groups'] != inventory):
            raise ValueError('Declarations disagree with failed preparation inventory')
    specifications = {entry['group_id']: entry for entry in inventory}
    if len(specifications) != len(inventory):
        raise ValueError('Duplicate preparation group inventory')
    groups = []
    for entry in original['groups']:
        report = json.loads(read(entry['prepared_report']))
        if (report.get('group_id') != entry['group_id'] or entry['group_id'] not in specifications
                or report.get('coordinate_frame') != original['coordinate_frame']):
            raise ValueError('Prepared group identity or coordinate frame mismatch')
        specification = specifications[entry['group_id']]
        if report.get('identity', {}).get('provenance', {}).get('members') != specification['members']:
            raise ValueError('Prepared group membership differs from declared inventory')
        rows = {row['id']: row for row in report.get('primitives', [])}
        for member in specification['members']:
            index = member['source_part_index']
            if (type(index) is not int or not 0 <= index < len(mesh.parts)
                    or member['id'] not in rows
                    or any(mesh.parts[index][key] != value or rows[member['id']]['source_binding'][key] != value
                           for key, value in member['source_binding'].items())):
                raise ValueError('Declared source part index/binding differs from prepared geometry')
        primitives = []
        for member in entry['primitives']:
            with np.load(io.BytesIO(read(member)), allow_pickle=False) as archive:
                if set(archive.files) != set(_ARRAYS):
                    raise ValueError('Incomplete prepared geometry archive')
                primitives.append({'id': member['id'], **{key: archive[key].copy() for key in _ARRAYS}})
        groups.append({'report': report, 'primitives': primitives})
    if len(groups) != len(specifications) or {g['report']['group_id'] for g in groups} != set(specifications):
        raise ValueError('Incomplete or duplicate prepared group inventory')
    neutral = LensAppearance((DensityKeyframe(0., (0., 0., 0.)),), roughness=.05)
    verified, selected = _prepared([{'prepared': group, 'appearance': neutral} for group in groups],
                                  mesh, original['source_sha256'], _source_matrices(document), document)
    actual_failure = validate_effective_optical_runtime(_tie_input(verified))
    if (actual_failure.get('status') == 'runtime_contract_satisfied'
            or set(actual_failure.get('reasons', [])) != failures):
        raise ValueError('Pinned geometry does not reproduce the recorded normal-conflict failure')
    for index, part in enumerate(mesh.parts):
        if index in selected:
            continue
        primitive = document['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]
        material = document.get('materials', [])[primitive['material']] if 'material' in primitive else {}
        if (primitive.get('extras', {}).get('partRole') == 'interior_contact' or _legacy_optical(material)):
            raise ValueError('Recovery cannot omit frame faces or change undeclared frame materials')
    alternatives = [propose_smooth_optical_group(group) for group in groups]
    if not all(a['changed'] and a['report']['status'] == 'supported_smooth_surface_hypothesis' for a in alternatives):
        unsupported = [a['report'] for a in alternatives if not a['changed']]
        raise ValueError('Existing smooth-fit guards do not support recovery: '+json.dumps(unsupported))
    # Check exact preservation independently of the proposal function's own guard.
    for old, alternative in zip(groups, alternatives):
        for before, after in zip(old['primitives'], alternative['prepared']['primitives']):
            for key in ('positions', 'indices', 'uv'):
                if not np.array_equal(before[key], after[key]):
                    raise ValueError('Normal recovery changed '+key)
    provenance = {'method': METHOD, 'source_preparation': str(source_report),
        'source_preparation_sha256': pins[str(source_report)], 'original_failure': actual_failure,
        'groups': [a['report'] for a in alternatives], 'changed_groups': len(alternatives),
        'positions_indices_and_uv_exact': True, 'frame_attributes_materials_textures_unchanged': True,
        'source_faces_deleted': 0, 'policy': 'Existing default SmoothOpticalPolicy, unchanged',
        'photo_observations_require_recomputation': True, 'independent_photo_validation': False,
        'interpretation': 'Conditional smooth effective-normal hypothesis; semantic identity and coating remain unverified'}
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in pins.items()):
        raise ValueError('Recovery inputs changed while loading')
    output.mkdir(parents=True, exist_ok=True)
    report = deepcopy(original)
    report.update(status='prepared_optical_group_candidate', reasons=[], groups=[], model=None, export=None,
                  implementation=implementation_manifest(), smooth_optical_geometry=provenance,
                  normal_recovery=provenance)
    report['source_snapshot'] = _write(output, 'source.glb', source_bytes)
    if declaration_bytes is not None:
        report['declarations'] = {**_write(output, 'declarations.json', declaration_bytes),
                                 'original_path': original['declarations'].get('original_path')}
    for index, alternative in enumerate(alternatives):
        group = alternative['prepared']; prefix = f'group-{index:03d}'
        row = {'group_id': group['report']['group_id'], 'prepared_report':
               _write(output, prefix+'/report.json', _json_bytes(group['report'])), 'primitives': []}
        for member_index, item in enumerate(group['primitives']):
            stream = io.BytesIO(); np.savez_compressed(stream, **{key: item[key] for key in _ARRAYS})
            row['primitives'].append({'id': item['id'], **_write(output, f'{prefix}/primitive-{member_index:03d}.npz', stream.getvalue())})
        report['groups'].append(row)
    receipt = write_optical_group_candidate(output/'source.glb', output/'prepared-neutral.glb',
        [{'prepared': a['prepared'], 'appearance': neutral} for a in alternatives],
        source_sha256=original['source_sha256'], provenance=provenance)
    decoded = read_optical_group_candidate(output/'prepared-neutral.glb', receipt, expected_sha256=receipt['output_sha256'])
    if receipt['demoted_legacy_optical_parts'] or receipt['interior_contact_parts'] or len(decoded['mesh'].faces) != len(mesh.faces):
        raise ValueError('Recovery unexpectedly changed frame materials or source face count')
    for group in receipt['groups']:
        for member in group['members']:
            errors = member['quantization']['attributes']
            if errors['positions']['maximum_absolute_error'] != 0:
                raise ValueError('Recovery requires exact float32 source positions')
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in pins.items()):
        raise ValueError('Recovery inputs changed during export')
    report['export'] = _write(output, 'export.json', _json_bytes(receipt))
    report['model'] = {'path': 'prepared-neutral.glb', 'sha256': receipt['output_sha256'],
                       'purpose': 'conditional smooth-normal recovery; neutral optical control'}
    _write(output, 'report.json', _json_bytes(report))
    return report
