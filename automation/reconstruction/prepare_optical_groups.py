"""Prepare source-preserving optical-group candidates, never semantic acceptance.

Whole source primitive instances are either explicitly declared or proposed by
the complete metadata/name ledger. Authored world transforms are baked without
resampling; one common group height is a coordinate hypothesis. A neutral export
is a diagnostic control, not a recovered material or an accepted reconstruction.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import stat

import numpy as np

from .deform_glb import _read_bytes
from .lens_appearance import DensityKeyframe, LensAppearance
from .mesh import load_glb_bytes
from .optical_group_asset import _source_matrices, write_optical_group_candidate
from .optical_group_raster import PROFILE
from .optical_groups import prepare_optical_group
from .refine_photos import implementation_manifest
from .region_proposals import _lens_parts


_SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_BINDING = ('node_index', 'mesh_index', 'primitive_index')
_ARRAYS = ('positions', 'indices', 'normals', 'uv')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode('utf-8')


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f'Invalid {name} fields')


def _slug(value, name):
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError(f'{name} must be a safe identifier')


def _provenance(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('Nonempty provenance is required')


def validate_group_declarations(value: dict) -> dict:
    """Validate schema only; source identity/bindings are checked by the stage.

    Required fields: schema_version, source_sha256, coordinate_frame, provenance,
    groups. Each group has group_id and members; a member has id,
    source_part_index and source_binding {node_index,mesh_index,primitive_index}.
    No supplied verification assertion grants identity or material acceptance.
    """
    _keys(value, ('schema_version', 'source_sha256', 'coordinate_frame', 'provenance', 'groups'), 'declarations')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported optical group declaration version')
    if not isinstance(value['source_sha256'], str) or not _SHA.fullmatch(value['source_sha256']):
        raise ValueError('Declarations require a lowercase source SHA256')
    frame = value['coordinate_frame']
    _keys(frame, ('id', 'units', 'up_axis', 'forward_axis', 'provenance'), 'coordinate_frame')
    _slug(frame['id'], 'coordinate_frame.id')
    if frame['units'] not in ('meters', 'millimeters', 'centimeters', 'unitless') or frame['up_axis'] != '+Y' or frame['forward_axis'] != '+Z':
        raise ValueError('Declare a common +Y-up/+Z-front frame and supported units')
    _provenance(frame['provenance']); _provenance(value['provenance'])
    if not isinstance(value['groups'], list) or not value['groups']:
        raise ValueError('Declarations require nonempty groups')
    group_ids, member_ids, bindings, part_indices = set(), set(), set(), set()
    for group in value['groups']:
        _keys(group, ('group_id', 'members'), 'group')
        _slug(group['group_id'], 'group_id')
        if group['group_id'] in group_ids:
            raise ValueError('Duplicate group ID')
        group_ids.add(group['group_id'])
        if not isinstance(group['members'], list) or not group['members']:
            raise ValueError('Every group requires complete source members')
        for member in group['members']:
            _keys(member, ('id', 'source_part_index', 'source_binding'), 'member')
            _slug(member['id'], 'member.id')
            _keys(member['source_binding'], _BINDING, 'source_binding')
            values = [member['source_part_index'], *member['source_binding'].values()]
            if any(type(x) is not int or x < 0 for x in values):
                raise ValueError('Source binding indices must be nonnegative integers')
            binding = tuple(member['source_binding'][k] for k in _BINDING)
            if member['id'] in member_ids or binding in bindings or member['source_part_index'] in part_indices:
                raise ValueError('Duplicate member ID or source membership')
            member_ids.add(member['id']); bindings.add(binding); part_indices.add(member['source_part_index'])
    try:
        return json.loads(_json_bytes(value))
    except (TypeError, ValueError) as error:
        raise ValueError('Declarations must contain finite JSON data') from error


def _parse(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('Nonfinite JSON number')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid)


def _ordinary_path(path):
    """Inspect every existing path entry before resolution follows any link."""
    path = Path(path).absolute()
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('Optical preparation paths must not contain symlinks or reparse points')
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError('Optical preparation requires ordinary files/directories')
    return path.resolve()


def _write(output, relative, raw):
    path = _ordinary_path(output/relative)
    if not path.is_relative_to(output):
        raise ValueError('Optical preparation artifact escapes output')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
    return {'path': path.relative_to(output).as_posix(), 'sha256': _sha(raw)}


def _document(raw):
    _, doc, binary = _read_bytes(raw)
    if any('uri' in image and (not isinstance(image['uri'], str) or not image['uri'].startswith('data:')) for image in doc.get('images', [])):
        raise ValueError('External image resources must be embedded before optical preparation')
    return doc, binary


def _normal_array(doc, binary, index):
    # Preserve even invalid unused accessor entries: prepare_optical_group alone
    # determines whether referenced authored directions are usable.
    acc = doc['accessors'][index]
    if acc.get('componentType') != 5126 or acc.get('type') != 'VEC3' or acc.get('normalized') or 'sparse' in acc or acc.get('extensions'):
        raise ValueError('Authored normals require an ordinary float32 VEC3 accessor')
    view = doc['bufferViews'][acc['bufferView']]
    count, stride = acc['count'], view.get('byteStride', 12)
    start = view.get('byteOffset', 0)+acc.get('byteOffset', 0)
    end = start+max(0, count-1)*stride+12
    if view.get('buffer', 0) != 0 or count <= 0 or stride < 12 or stride % 4 or start < view.get('byteOffset', 0) or start < 0 or end > doc['buffers'][0]['byteLength'] or end > view.get('byteOffset', 0)+view['byteLength']:
        raise ValueError('Invalid authored normal accessor range')
    return np.ndarray((count, 3), dtype='<f4', buffer=binary, offset=start, strides=(stride, 4)).astype(np.float64)


def run_optical_group_preparation(model, output, *, grouping_mode, declarations=None) -> dict:
    """Prepare a new/empty stage. Unsupported complete membership exports nothing.

    Schema/options/path errors raise before mutation. Stale declaration source
    identity or binding, unsupported geometry, or float32 export incompatibility
    produce a pinned diagnostic report with no model/export. All original source
    bytes are captured once for computation and retained in source.glb; export
    rereads only that snapshot, never an independently recaptured working model.
    """
    if grouping_mode not in ('explicit_declarations', 'source_part_hypotheses'):
        raise ValueError('Unknown optical grouping mode')
    if (declarations is None) != (grouping_mode == 'source_part_hypotheses'):
        raise ValueError('Declarations are required only for explicit_declarations')
    model, output = _ordinary_path(model), _ordinary_path(output)
    if not model.is_file() or output == model or model.is_relative_to(output):
        raise ValueError('Source must be a separate ordinary model file outside output')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Optical group preparation output must be new or empty')
    declaration_path, declaration_raw, declared = None, None, None
    if declarations is not None:
        if isinstance(declarations, dict):
            declared = validate_group_declarations(declarations); declaration_raw = _json_bytes(declared)
        elif isinstance(declarations, (str, Path)):
            declaration_path = _ordinary_path(declarations)
            if declaration_path.is_relative_to(output):
                raise ValueError('Declaration input must be outside stage output')
            declaration_raw = declaration_path.read_bytes()
            declared = validate_group_declarations(_parse(declaration_raw))
        else:
            raise ValueError('Declarations must be a dict or JSON path')
    raw = model.read_bytes(); source_hash = _sha(raw)
    mesh = load_glb_bytes(raw); doc, binary = _document(raw); matrices = _source_matrices(doc)
    implementation = implementation_manifest()
    selected, ledger = _lens_parts(mesh)
    frame = declared['coordinate_frame'] if declared else {'id': 'source-world', 'units': 'unitless', 'up_axis': '+Y', 'forward_axis': '+Z',
        'provenance': {'method': 'unchanged selected-scene glTF world coordinates', 'units_and_product_front': 'unverified'}}
    specifications = declared['groups'] if declared else []
    if declared is None:
        for index in selected:
            part = mesh.parts[index]; token = f"n{part['node_index']}-m{part['mesh_index']}-p{part['primitive_index']}"
            specifications.append({'group_id': 'optical-'+token, 'members': [{'id': 'part-'+token, 'source_part_index': index,
                'source_binding': {key: part[key] for key in _BINDING}}]})
    report = {'schema_version': 1, 'optical_profile': PROFILE, 'status': 'no_candidate_optical_parts',
        'source': str(model), 'source_sha256': source_hash, 'source_snapshot': None, 'declarations': None,
        'grouping_mode': grouping_mode, 'coordinate_frame': frame, 'grouping_inventory': specifications,
        'source_parts': ledger, 'implementation': implementation, 'groups': [], 'model': None, 'export': None,
        'reasons': [], 'accepted': False, 'quality_verdict': 'unmeasured', 'selected_material': None,
        'semantic_identity': 'unverified', 'material_identification': 'not_attempted',
        'runtime_compatibility': 'unmeasured',
        'limitations': ['Whole source primitive membership is a hypothesis, not an optical-instance segmentation.',
            'Names, roles and transmission metadata may combine multiple lenses or select non-optical parts.',
            'Height coordinates and authored/derived normals do not identify physical coatings, thickness or refraction.',
            'Neutral control appearance is not photo inference; no missing group receives a guessed fitted material.',
            'Export checks are bounded geometry compatibility evidence, not actual-render or AR-quality acceptance.']}
    if declared and declared['source_sha256'] != source_hash:
        report['reasons'].append('declaration_source_sha256_mismatch')
    requested = set()
    for spec in specifications:
        for member in spec['members']:
            index = member['source_part_index']; binding = member['source_binding']
            if index >= len(mesh.parts) or any(mesh.parts[index][k] != binding[k] for k in _BINDING):
                report['reasons'].append(f"{spec['group_id']}/{member['id']}:source_binding_mismatch")
            else:
                requested.add(index)
    for row in ledger:
        row['selected_for_group_preparation'] = row['part_index'] in requested
    prepared = []
    if not report['reasons']:
        for spec in specifications:
            members = []
            for member in spec['members']:
                part = mesh.parts[member['source_part_index']]; start, count = part['vertex_start'], part['vertex_count']
                primitive = doc['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]
                binding = {'asset_sha256': source_hash, **member['source_binding'], 'material_index': primitive.get('material'),
                    'position_accessor': primitive['attributes']['POSITION'], 'index_accessor': primitive.get('indices'),
                    'normal_accessor': primitive['attributes'].get('NORMAL')}
                item = {'id': member['id'], 'source_binding': binding, 'coordinate_frame_id': frame['id'],
                    'positions': mesh.vertices[start:start+count].copy(),
                    'indices': mesh.faces[part['face_start']:part['face_start']+part['face_count']].copy()-start}
                if binding['normal_accessor'] is not None:
                    matrix = matrices[part['node_index']]
                    normal = _normal_array(doc, binary, binding['normal_accessor'])
                    normal = normal @ np.linalg.inv(matrix[:3, :3])
                    with np.errstate(invalid='ignore', divide='ignore', over='ignore'):
                        normal /= np.linalg.norm(normal, axis=1)[:, None]
                    item.update(normals=normal, normal_transform={'method': 'inverse_transpose_normalized',
                        'source_to_common_matrix': matrix.tolist(), 'provenance': {'method': 'captured_source_NORMAL_accessor',
                            'source_sha256': source_hash, 'normal_accessor': binding['normal_accessor']}})
                members.append(item)
            identity = {'status': 'unverified', 'provenance': {'method': grouping_mode,
                'declaration_provenance': declared['provenance'] if declared else None,
                'source_sha256': source_hash, 'members': spec['members']}}
            result = prepare_optical_group(spec['group_id'], members, identity=identity, coordinate_frame=frame)
            prepared.append(result)
            if result['report']['status'] != 'prepared_candidate':
                report['reasons'].append(f"{spec['group_id']}:unsupported_geometry")
    output.mkdir(parents=True, exist_ok=True)
    report['source_snapshot'] = _write(output, 'source.glb', raw)
    if declaration_raw is not None:
        report['declarations'] = {**_write(output, 'declarations.json', declaration_raw),
                                  'original_path': str(declaration_path) if declaration_path else None}
    for i, result in enumerate(prepared):
        folder = f'group-{i:03d}'
        row = {'group_id': result['report']['group_id'], 'prepared_report': _write(output, folder+'/report.json', _json_bytes(result['report'])), 'primitives': []}
        for j, item in enumerate(result['primitives']):
            stream = io.BytesIO(); np.savez_compressed(stream, **{key: item[key] for key in _ARRAYS})
            row['primitives'].append({'id': item['id'], **_write(output, f'{folder}/primitive-{j:03d}.npz', stream.getvalue())})
        report['groups'].append(row)
    if specifications and not report['reasons']:
        neutral = LensAppearance((DensityKeyframe(0., (0., 0., 0.)),), roughness=.05)
        try:
            export = write_optical_group_candidate(output/'source.glb', output/'prepared-neutral.glb',
                [{'prepared': result, 'appearance': neutral} for result in prepared], source_sha256=source_hash,
                provenance={'method': 'source_preserving_optical_group_preparation_v1', 'grouping_mode': grouping_mode,
                    'source_original_path': str(model), 'prepared_groups': report['groups'],
                    'appearance_origin': 'neutral_diagnostic_control_not_photo_inference'})
        except ValueError as error:
            report['reasons'].append('export_unsupported: '+str(error))
        else:
            report['export'] = _write(output, 'export.json', _json_bytes(export))
            report['model'] = {'path': 'prepared-neutral.glb', 'sha256': export['output_sha256'], 'purpose': 'neutral diagnostic control'}
            report['status'] = 'prepared_optical_group_candidate'
    if report['reasons']:
        report['status'] = 'unsupported_optical_group_preparation'
    # These are integrity rechecks, not fresh geometry inputs.
    if _sha(_ordinary_path(model).read_bytes()) != source_hash or implementation_manifest() != implementation:
        raise ValueError('Source or implementation changed during optical group preparation')
    if declaration_path and _ordinary_path(declaration_path).read_bytes() != declaration_raw:
        raise ValueError('Declarations changed during optical group preparation')
    _write(output, 'report.json', _json_bytes(report))
    return report
