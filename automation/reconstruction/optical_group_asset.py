"""Export and read source-bound effective optical groups without remeshing.

This experimental profile bakes source world coordinates to float32. Original
source bytes and unrelated instances survive, but selected positions are NOT
claimed bit-identical after conversion. Identity, normal intent and optical
coordinates remain supplied hypotheses. Production front-sheet importers must
continue rejecting this profile.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile

import numpy as np

from .deform_glb import _read, _float_accessor, _triangles
from .lens_appearance import LensAppearance, VERTICAL_COORDINATE
from .lens_asset import EXTENSION, _pack_glb
from .mesh import load_glb_bytes, _node_matrix
from .optical_asset import _material, _selected_nodes
from .optical_groups import _array_hash
from .optical_group_raster import PROFILE
from .optical_group_runtime import validate_effective_optical_runtime


METHOD = 'source_bound_baked_effective_optical_groups_v1'
_SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_ATTRIBUTES = ('positions', 'indices', 'normals', 'uv')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _hash(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def _seal(receipt):
    return {**receipt, 'receipt_sha256': _hash(receipt)}


def _receipt(value, expected=None):
    value = _json(value)
    if not isinstance(value, dict):
        raise ValueError('Expected an optical group export receipt')
    digest = value.pop('receipt_sha256', None)
    if (digest != _hash(value) or (expected is not None and digest != expected)
            or value.get('schema_version') != 1 or value.get('method') != METHOD
            or value.get('profile') != PROFILE or value.get('accepted') is not False
            or not isinstance(value.get('source_sha256'), str) or not _SHA.fullmatch(value['source_sha256'])):
        raise ValueError('Optical group receipt hash or contract mismatch')
    return {**value, 'receipt_sha256': digest}


def _source_matrices(doc):
    _selected_nodes(doc)
    result = {}
    def visit(index, parent):
        result[index] = parent @ _node_matrix(doc['nodes'][index])
        for child in doc['nodes'][index].get('children', []):
            visit(child, result[index])
    for root in doc['scenes'][doc.get('scene', 0)]['nodes']:
        visit(root, np.eye(4))
    return result


def _quantize(item):
    arrays, errors = {}, {}
    for name, width in (('positions', 3), ('normals', 3), ('uv', 2)):
        value = np.asarray(item[name])
        if (value.ndim != 2 or value.shape[1] != width or len(value) < 3
                or value.dtype.kind != 'f' or not np.isfinite(value).all()):
            raise ValueError(f'Invalid prepared {name}')
        with np.errstate(over='ignore', invalid='ignore'):
            arrays[name] = np.ascontiguousarray(value, dtype='<f4')
        if not np.isfinite(arrays[name]).all():
            raise ValueError('Optical attributes overflow float32')
        errors[name] = {'maximum_absolute_error': float(np.max(np.abs(value-arrays[name].astype(float))))}
    p, n, uv = (arrays[k] for k in ('positions', 'normals', 'uv'))
    f = np.asarray(item['indices'])
    if (f.ndim != 2 or f.shape[1] != 3 or not len(f) or f.dtype.kind not in 'iu'
            or np.any(f < 0) or np.any(f >= len(p)) or np.any(f > np.iinfo(np.uint32).max)
            or len(n) != len(p) or len(uv) != len(p)):
        raise ValueError('Invalid prepared indices or attribute counts')
    arrays['indices'] = np.ascontiguousarray(f, dtype='<u4')
    if np.any(np.linalg.norm(n.astype(float), axis=1) == 0) or np.any((uv < 0) | (uv > 1)):
        raise ValueError('Float32 normals must remain nonzero and group UV within 0..1')
    old = np.asarray(item['positions'], float)[f]
    new = p.astype(float)[f]
    old_cross = np.cross(old[:, 1]-old[:, 0], old[:, 2]-old[:, 0])
    new_cross = np.cross(new[:, 1]-new[:, 0], new[:, 2]-new[:, 0])
    old_area, new_area = np.linalg.norm(old_cross, axis=1), np.linalg.norm(new_cross, axis=1)
    if (not np.isfinite(old_area).all() or not np.isfinite(new_area).all()
            or np.any(old_area == 0) or np.any(new_area == 0)
            or np.any(np.sum(old_cross*new_cross, axis=1) <= 0)):
        raise ValueError('Float32 conversion collapses or inverts source triangles')
    return arrays, {'attributes': errors, 'indices_exact': True,
                    'minimum_triangle_area_ratio': float(np.min(new_area/old_area)),
                    'maximum_triangle_area_ratio': float(np.max(new_area/old_area)),
                    'collapsed_or_inverted_triangles': 0,
                    'source_units': 'declared common/source world units; no rescale or recenter',
                    'fidelity_acceptance': 'unmeasured; finite conversion error is reported, not accepted by a quality threshold'}


def _prepared(groups, source_mesh, source_sha, matrices, document):
    if not isinstance(groups, list) or not groups:
        raise ValueError('At least one explicit prepared group is required')
    source_lookup = {(p['node_index'], p['mesh_index'], p['primitive_index']): (i, p)
                     for i, p in enumerate(source_mesh.parts)}
    result, ids, selected, frame = [], set(), set(), None
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'prepared', 'appearance'}:
            raise ValueError('Each group requires prepared and appearance fields')
        prepared = group['prepared']; report = _json(prepared['report'])
        gid = report.get('group_id')
        if (not isinstance(gid, str) or not _SLUG.fullmatch(gid) or gid in ids
                or report.get('schema_version') != 1 or report.get('status') != 'prepared_candidate'
                or report.get('method') != 'source_preserving_optical_group_v1'):
            raise ValueError('Prepared group ID/status/schema is invalid or duplicated')
        ids.add(gid)
        cf = report['coordinate_frame']
        declared = {k: cf[k] for k in ('id', 'units', 'up_axis', 'forward_axis')}
        if frame is not None and declared != frame:
            raise ValueError('All groups must share one declared source world coordinate frame')
        frame = declared
        canonical = {k: report[k] for k in ('group_id', 'identity', 'coordinate_frame')}
        canonical['primitives'] = sorted(report['primitives'], key=lambda r: r['id'])
        expected = _sha(json.dumps(canonical, sort_keys=True, allow_nan=False).encode())
        if expected != report['group_sha256']:
            raise ValueError('Prepared group report hash mismatch')
        appearance = group['appearance']
        if not isinstance(appearance, LensAppearance):
            appearance = LensAppearance.from_dict(appearance)
        descriptor = appearance.to_dict()
        rows = {row['id']: row for row in report['primitives']}
        members, found = [], set()
        for item in prepared['primitives']:
            pid = item.get('id')
            if pid not in rows or pid in found:
                raise ValueError('Prepared member IDs are missing or duplicated')
            found.add(pid); row = rows[pid]; binding = row['source_binding']
            if binding['asset_sha256'] != source_sha:
                raise ValueError('Prepared member source SHA-256 mismatch')
            key = tuple(binding[k] for k in ('node_index', 'mesh_index', 'primitive_index'))
            if key not in source_lookup:
                raise ValueError('Prepared member does not bind a selected source part')
            index, part = source_lookup[key]
            primitive = document['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]
            actual_indices = {'material_index': primitive.get('material'),
                              'position_accessor': primitive['attributes']['POSITION'],
                              'index_accessor': primitive.get('indices'),
                              'normal_accessor': primitive['attributes'].get('NORMAL')}
            if any(name in binding and binding[name] != value for name, value in actual_indices.items()):
                raise ValueError('Optional source binding indices differ from source primitive')
            if index in selected:
                raise ValueError('Every source part may belong to exactly one optical group')
            selected.add(index)
            for name in _ATTRIBUTES:
                value = np.asarray(item[name], dtype=np.int64 if name == 'indices' else np.float64)
                if _array_hash(value) != row[name+'_sha256']:
                    raise ValueError(f'Prepared {name} differs from its report hash')
            start, count = part['vertex_start'], part['vertex_count']
            source_positions = source_mesh.vertices[start:start+count]
            source_indices = source_mesh.faces[part['face_start']:part['face_start']+part['face_count']]-start
            if (not np.array_equal(item['positions'], source_positions)
                    or not np.array_equal(item['indices'], source_indices)):
                raise ValueError('Prepared member changed source positions or triangle connectivity')
            arrays, quantization = _quantize(item)
            members.append({'id': pid, 'source_part_index': index, 'source_binding': binding,
                'source_to_common_matrix': matrices[part['node_index']].tolist(),
                'normal_provenance': {k: row[k] for k in ('normal_policy', 'normal_transform', 'source_normal_issue') if k in row},
                'source_attribute_sha256': {k: row[k+'_sha256'] for k in _ATTRIBUTES},
                'arrays': arrays, 'quantization': quantization})
        if found != set(rows) or len(rows) != len(report['primitives']) or not members:
            raise ValueError('Prepared member inventory is incomplete or duplicated')
        result.append({'group_id': gid, 'coordinate_frame': cf, 'identity': report['identity'],
                       'prepared_group_sha256': report['group_sha256'], 'appearance': descriptor,
                       'appearance_sha256': _hash(descriptor), 'members': sorted(members, key=lambda m: m['id'])})
    for index, part in enumerate(source_mesh.parts):
        if index not in selected and part.get('has_lens_appearance_extension'):
            raise ValueError('Every canonical optical source part requires an explicit group binding')
    return sorted(result, key=lambda r: r['group_id']), selected


def _tie_input(groups):
    return [{'group_id': row['group_id'], 'coordinate_frame_id': row['coordinate_frame']['id'],
             'appearance_sha256': row['appearance_sha256'],
             'primitives': [{'id': m['id'], **m['arrays']} for m in row['members']]} for row in groups]


def _check_runtime(groups):
    result = validate_effective_optical_runtime(_tie_input(groups))
    if not result['complete'] or result['status'] != 'runtime_contract_satisfied':
        raise ValueError('Actual float32 optical runtime/coincident-patch contract unsupported: '+json.dumps(result['reasons']))
    return result


_OPTICAL_EXTENSIONS = ('KHR_materials_transmission', 'KHR_materials_volume', 'KHR_materials_ior',
                       'KHR_materials_dispersion', EXTENSION)


def _legacy_optical(material):
    """The runtime's rule: a canonical descriptor, or a transmitting physical material."""
    extensions = material.get('extensions', {}) if isinstance(material, dict) else {}
    if EXTENSION in extensions:
        return True
    factor = extensions.get('KHR_materials_transmission', {}).get('transmissionFactor', 0)
    return isinstance(factor, (int, float)) and not isinstance(factor, bool) and factor > 0


def _demote_material(doc, index):
    """Append an opaque copy of a legacy optical material; the original record stays."""
    copy = deepcopy(doc['materials'][index])
    extensions = copy.get('extensions', {})
    removed = [name for name in _OPTICAL_EXTENSIONS if name in extensions]
    for name in removed:
        del extensions[name]
    if not extensions:
        copy.pop('extensions', None)
    pbr = copy.setdefault('pbrMetallicRoughness', {})
    base = list(pbr.get('baseColorFactor', [1, 1, 1, 1]))
    conversion = None
    if copy.get('alphaMode') == 'BLEND' and len(base) == 4 and base[3] > 0:
        # A visible translucent lens remainder becomes opaque; an alpha-zero
        # (invisible) primitive stays invisible and is not resurrected.
        copy['alphaMode'] = 'OPAQUE'; base[3] = 1; pbr['baseColorFactor'] = base
        conversion = 'BLEND with positive alpha converted to OPAQUE alpha 1'
    copy['name'] = f"{copy.get('name', 'material')} (undeclared optical remainder, opaque)"
    copy.setdefault('extras', {})['demotedLegacyOptics'] = {
        'sourceMaterialIndex': index, 'removedExtensions': removed, 'alphaModeConversion': conversion,
        'reason': 'undeclared optical-material piece beside declared effective groups; one optical interface per runtime'}
    doc['materials'].append(copy)
    return {'index': len(doc['materials'])-1, 'removed_extensions': removed, 'alpha_mode_conversion': conversion}


def write_optical_group_candidate(source: Path, destination: Path, groups: list[dict], *,
                                  source_sha256: str, provenance: dict) -> dict:
    """Export [{prepared: prepare_optical_group result, appearance: descriptor}].

    Membership resolves source_binding's node/mesh/primitive against load_glb's
    whole selected-scene parts. Prepared float64 positions and indices must match
    those source arrays exactly. Normals and group UVs are hash-bound hypotheses,
    not independently recovered authored intent. All validation precedes the
    final new destination write; no existing artifact is overwritten.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Optical group candidates require a separate new destination')
    raw, doc, original_binary = _read(source)
    if not isinstance(source_sha256, str) or not _SHA.fullmatch(source_sha256) or _sha(raw) != source_sha256:
        raise ValueError('Optical source SHA-256 mismatch')
    for image in doc.get('images', []):
        if 'uri' in image and (not isinstance(image['uri'], str) or not image['uri'].startswith('data:')):
            raise ValueError('External image resources must be embedded before optical export')
    provenance = _json(provenance)
    if not isinstance(provenance, dict) or not isinstance(provenance.get('method'), str) or not provenance['method']:
        raise ValueError('Explicit optical group provenance is required')
    matrices = _source_matrices(doc); source_mesh = load_glb_bytes(raw)
    prepared, selected = _prepared(groups, source_mesh, source_sha256, matrices, doc)
    runtime_validation = _check_runtime(prepared)
    binary = bytearray(original_binary)
    for name in ('accessors', 'bufferViews', 'materials', 'meshes', 'nodes'):
        doc.setdefault(name, [])
    original_counts = {k: len(doc[k]) for k in ('nodes', 'meshes', 'materials', 'accessors', 'bufferViews')}
    node_map = {index: len(doc['nodes'])+offset for offset, index in enumerate(sorted(matrices))}
    original_nodes = list(doc['nodes'])
    for index in sorted(matrices):
        node = deepcopy(original_nodes[index])
        if 'children' in node:
            node['children'] = [node_map[child] for child in node['children']]
        doc['nodes'].append(node)
    scene = doc['scenes'][doc.get('scene', 0)]
    scene['nodes'] = [node_map[i] for i in scene['nodes']]
    removed, demoted, demoted_materials = [], [], {}
    hidden = []
    by_node = {}
    for index in selected:
        part = source_mesh.parts[index]
        by_node.setdefault(part['node_index'], {}).setdefault('removed', set()).add(part['primitive_index'])
    # Every remaining primitive that still carries a legacy optical material is
    # converted explicitly: the effective-group runtime allows one optical
    # interface, so an undeclared transmission or canonical material beside the
    # declared groups would be refused as mixed optics. Its geometry stays.
    part_by_binding = {(p['node_index'], p['primitive_index']): i for i, p in enumerate(source_mesh.parts)}
    for node_index in sorted(matrices):
        original_nodes_mesh = original_nodes[node_index].get('mesh')
        if original_nodes_mesh is None:
            continue
        for primitive_index, primitive in enumerate(doc['meshes'][original_nodes_mesh]['primitives']):
            if primitive_index in by_node.get(node_index, {}).get('removed', set()):
                continue
            material_index = primitive.get('material')
            if (primitive.get('extras') or {}).get('partRole') == 'interior_contact':
                # A shared contact face of two split parts: inside their union, seen by
                # no photograph. It keeps its geometry and becomes invisible.
                by_node.setdefault(node_index, {}).setdefault('interior', {})[primitive_index] = part_by_binding.get((node_index, primitive_index))
                continue
            if material_index is not None and _legacy_optical(doc['materials'][material_index]):
                by_node.setdefault(node_index, {}).setdefault('demoted', {})[primitive_index] = part_by_binding.get((node_index, primitive_index))
    for node_index, changes in sorted(by_node.items()):
        node = doc['nodes'][node_map[node_index]]; original_mesh = node['mesh']
        mesh = deepcopy(doc['meshes'][original_mesh])
        primitive_ids = changes.get('removed', set()); to_demote = changes.get('demoted', {}); to_hide = changes.get('interior', {})
        kept = []
        for i, primitive in enumerate(mesh['primitives']):
            if i in primitive_ids:
                continue
            if i in to_hide:
                # The runtime refuses translucent frame materials and would render an
                # opaque one, so the piece is omitted from the runtime candidate; the
                # partitioned source keeps its geometry and this receipt names it.
                hidden.append({'source_part_index': to_hide[i], 'source_node_index': node_index, 'candidate_node_index': node_map[node_index],
                               'source_mesh_index': original_mesh, 'source_primitive_index': i,
                               'reason': 'interior contact piece (exactly coincident face shared by two split parts, inside their union); omitted from the runtime candidate, retained in the partitioned source'})
                continue
            if i in to_demote:
                source_material = primitive['material']
                if source_material not in demoted_materials:
                    demoted_materials[source_material] = _demote_material(doc, source_material)
                record = demoted_materials[source_material]
                primitive = {**primitive, 'material': record['index']}
                demoted.append({'source_part_index': to_demote[i], 'source_node_index': node_index,
                                'candidate_node_index': node_map[node_index], 'source_mesh_index': original_mesh,
                                'source_primitive_index': i, 'source_material_index': source_material,
                                'demoted_material_index': record['index'], 'removed_extensions': record['removed_extensions'],
                                'alpha_mode_conversion': record['alpha_mode_conversion'],
                                'original_part_role': original_nodes[node_index].get('extras', {}).get('partRole'),
                                'reason': 'undeclared legacy optical material; converted to ordinary opaque geometry for the single-interface runtime'})
            kept.append(primitive)
        mesh['primitives'] = kept
        if kept:
            doc['meshes'].append(mesh); node['mesh'] = len(doc['meshes'])-1
        else:
            node.pop('mesh')
        if to_demote:
            extras = node.setdefault('extras', {})
            if str(extras.get('partRole', '')).lower() in ('lens', 'lenses', 'optical'):
                extras['partRole'] = 'undeclared_optical_remainder'
            extras['undeclaredOpticalRemainder'] = {'source_primitive_indices': sorted(to_demote),
                                                    'semantic_identity': 'unverified', 'optical_interface': 'none'}
        if primitive_ids:
            removed.append({'source_node_index': node_index, 'candidate_node_index': node_map[node_index],
                            'source_mesh_index': original_mesh, 'primitive_indices': sorted(primitive_ids)})
    def append(array, kind, component):
        binary.extend(b'\0'*(-len(binary) % 4))
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes,
                                  'target': 34963 if kind == 'SCALAR' else 34962})
        binary.extend(array.tobytes())
        accessor = {'bufferView': len(doc['bufferViews'])-1, 'componentType': component,
                    'count': array.size if kind == 'SCALAR' else len(array), 'type': kind}
        if kind == 'VEC3':
            accessor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        doc['accessors'].append(accessor)
        return len(doc['accessors'])-1
    bindings = []
    for raster_id, group in enumerate(prepared):
        material_index = len(doc['materials'])
        appearance = LensAppearance.from_dict(group['appearance'])
        doc['materials'].append(_material(appearance, group['group_id']))
        members = []
        for member in group['members']:
            arrays = member['arrays']
            primitive = {'attributes': {name: append(arrays[key], kind, 5126) for name, key, kind in
                (('POSITION', 'positions', 'VEC3'), ('NORMAL', 'normals', 'VEC3'), ('TEXCOORD_0', 'uv', 'VEC2'))},
                'indices': append(arrays['indices'], 'SCALAR', 5125), 'material': material_index, 'mode': 4}
            mesh_index = len(doc['meshes']); node_index = len(doc['nodes'])
            doc['meshes'].append({'name': f'Effective group {group["group_id"]}/{member["id"]}', 'primitives': [primitive]})
            metadata = {'partRole': 'lens', 'lensSurfaceProfile': PROFILE, 'lensUVConvention': VERTICAL_COORDINATE,
                        'opticalGroupId': group['group_id'], 'opticalGroupMemberId': member['id'],
                        'opticalSourcePartIndex': member['source_part_index'], 'opticalSourceSha256': source_sha256,
                        'lensAppearanceSha256': group['appearance_sha256'],
                        'semanticIdentity': 'unverified', 'materialIdentification': 'unmeasured'}
            doc['nodes'].append({'name': doc['meshes'][-1]['name'], 'mesh': mesh_index, 'extras': metadata})
            scene['nodes'].append(node_index)
            members.append({**{k: v for k, v in member.items() if k != 'arrays'},
                'node_index': node_index, 'mesh_index': mesh_index, 'material_index': material_index,
                'vertices': len(arrays['positions']), 'triangles': len(arrays['indices']),
                'attribute_sha256': {k: _sha(arrays[k].tobytes()) for k in _ATTRIBUTES}, 'node_metadata': metadata})
        bindings.append({**{k: v for k, v in group.items() if k != 'members'}, 'raster_group_id': raster_id,
                         'material_index': material_index, 'members': members,
                         'source_part_indices': sorted(m['source_part_index'] for m in members)})
    if EXTENSION not in doc.setdefault('extensionsUsed', []):
        doc['extensionsUsed'].append(EXTENSION)
    doc['buffers'][0]['byteLength'] = len(binary)
    declaration = {'schema_version': 1, 'profile': PROFILE, 'source_sha256': source_sha256,
                   'groups': bindings, 'provenance': provenance, 'accepted': False}
    doc.setdefault('extras', {})['effectiveOpticalGroups'] = {
        'declaration_sha256': _hash(declaration), **declaration}
    output = _pack_glb(doc, bytes(binary))
    receipt = _seal({'schema_version': 1, 'method': METHOD, 'profile': PROFILE,
        'status': 'experimental_optical_group_candidate_exported', 'accepted': False, 'quality_verdict': 'unmeasured',
        'source_sha256': source_sha256, 'source_path': str(source), 'output_sha256': _sha(output), 'output': str(destination),
        'source_binary_prefix': {'bytes': len(original_binary), 'sha256': _sha(original_binary)},
        'original_record_counts': original_counts, 'source_binary_prefix_preserved': True,
        'declaration_sha256': _hash(declaration), 'groups': bindings, 'provenance': provenance,
        'removed_instance_primitives': removed, 'replaced_parts': sorted(selected),
        'unreplaced_source_parts': [i for i in range(len(source_mesh.parts)) if i not in selected],
        'demoted_legacy_optical_parts': demoted, 'interior_contact_parts': hidden,
        'coincident_patch_validation': runtime_validation['coincident_patch_validation'],
        'runtime_contract_validation': runtime_validation,
        'limitations': ['Group identity and physical material identification are unmeasured.',
            'Whole source parts only; one group may contain several parts. No semantic split/merge is inferred.',
            'Selected world positions, supplied normals and UVs are converted to float32 with reported error; no rescale or recenter.',
            'UV and normal provenance remain supplied hypotheses; source position/index equality is independently checked.',
            'Unselected source primitives keep their geometry and materials, except that undeclared legacy optical materials are explicitly converted to opaque copies (recorded under demoted_legacy_optical_parts); CPU -1 face IDs use them as opaque geometric proxy stops, not alpha/material simulation.',
            'Exact coincident checks exclude near-coincidence, noncoplanar intersection lines and finite GPU depth ties.',
            'Production renderer support, units/attachment, all-view capacity and AR appearance remain unmeasured.']})
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Decode the actual serialized bytes before creating the final artifact.
    with tempfile.TemporaryDirectory(prefix='.optical-group-check-', dir=destination.parent) as folder:
        staged = Path(folder)/'candidate.glb'; staged.write_bytes(output)
        actual = read_optical_group_candidate(staged, receipt)
        # Interior contact pieces are omitted from the candidate and are expected
        # to be absent on reload.
        hidden_bindings = {(h['source_node_index'], h['source_primitive_index']) for h in hidden}
        expected_parts = [p for i, p in enumerate(source_mesh.parts)
                          if i not in selected and (p['node_index'], p['primitive_index']) not in hidden_bindings]
        actual_parts = [p for p in actual['mesh'].parts if p['node_index'] not in {m['node_index'] for g in bindings for m in g['members']}]
        if len(actual_parts) != len(expected_parts):
            raise ValueError('Unreplaced scene part inventory changed on reload')
        for old, new in zip(expected_parts, actual_parts):
            sv, nv = old['vertex_start'], new['vertex_start']
            sf, nf = old['face_start'], new['face_start']
            if (old['vertex_count'] != new['vertex_count'] or old['face_count'] != new['face_count']
                    or not np.array_equal(source_mesh.vertices[sv:sv+old['vertex_count']], actual['mesh'].vertices[nv:nv+new['vertex_count']])
                    or not np.array_equal(source_mesh.faces[sf:sf+old['face_count']]-sv, actual['mesh'].faces[nf:nf+new['face_count']]-nv)):
                raise ValueError('Unreplaced source geometry changed on reload')
    if _sha(source.read_bytes()) != source_sha256:
        raise ValueError('Source changed during optical group export')
    with destination.open('xb') as stream:
        stream.write(output)
    return receipt


def read_optical_group_candidate(path: Path, receipt: dict, *, expected_sha256: str | None = None,
                                 expected_receipt_sha256: str | None = None) -> dict:
    """Return actual mesh/uv/normals/face_groups/groups for conditional fitting.

    Unselected vertices have NaN UV/normals and faces use -1 (an opaque geometry
    proxy, not source-material simulation). Group dictionary keys are deterministic
    integer raster IDs; values include group_id, source_part_indices and the
    unchanged photo-fitter surface_binding schema. Receipt hashes are integrity
    pins, not signatures: callers must preserve the trusted receipt/hash lineage.
    Source paths are informational and never followed by this portable reader.
    """
    receipt = _receipt(receipt, expected_receipt_sha256)
    raw, doc, binary = _read(path); digest = _sha(raw)
    if digest != receipt['output_sha256'] or (expected_sha256 is not None and digest != expected_sha256):
        raise ValueError('Exported optical group GLB SHA-256 mismatch')
    if 'compact_storage' in receipt:
        from .compact_glb import validate_compact_optical_storage
        validate_compact_optical_storage(receipt, doc, binary)
    else:
        prefix = receipt['source_binary_prefix']
        if _sha(binary[:prefix['bytes']]) != prefix['sha256']:
            raise ValueError('Original source binary prefix differs from export receipt')
    declaration = doc.get('extras', {}).get('effectiveOpticalGroups', {})
    expected = {'schema_version': 1, 'profile': PROFILE, 'source_sha256': receipt['source_sha256'],
                'groups': receipt['groups'], 'provenance': receipt['provenance'], 'accepted': False}
    if declaration != {'declaration_sha256': _hash(expected), **expected} or receipt['declaration_sha256'] != _hash(expected):
        raise ValueError('Embedded optical group declaration differs from receipt')
    _source_matrices(doc)
    mesh = load_glb_bytes(raw)
    uv, normals = np.full((len(mesh.vertices), 2), np.nan), np.full_like(mesh.vertices, np.nan)
    face_groups = np.full(len(mesh.faces), -1, dtype=np.int64)
    groups, bound_nodes, source_parts, gids, material_ids, tie_groups = {}, {}, set(), set(), set(), []
    rows = receipt['groups']
    if not isinstance(rows, list) or not rows:
        raise ValueError('Missing optical group bindings')
    for index, group in enumerate(rows):
        gid = group['group_id']; rid = group['raster_group_id']
        if (type(rid) is not int or rid != index or not isinstance(gid, str) or not _SLUG.fullmatch(gid)
                or gid in gids or group['material_index'] in material_ids):
            raise ValueError('Duplicate or invalid optical group/material identity')
        gids.add(gid); material_ids.add(group['material_index'])
        appearance = LensAppearance.from_dict(group['appearance'])
        if _hash(appearance.to_dict()) != group['appearance_sha256']:
            raise ValueError('Canonical descriptor SHA-256 mismatch')
        material = doc['materials'][group['material_index']]
        if material.get('extensions', {}).get(EXTENSION) != {'schema_version': 1, 'texcoord': 0, 'appearance': appearance.to_dict()}:
            raise ValueError('Canonical material extension differs from receipt')
        members = group['members']
        if not members or group['source_part_indices'] != sorted(m['source_part_index'] for m in members):
            raise ValueError('Missing or conflicting optical group member inventory')
        tie_group = {**group, 'members': []}; tie_groups.append(tie_group)
        member_ids = set()
        for member in members:
            node_index, source_part = member['node_index'], member['source_part_index']
            if (type(node_index) is not int or node_index in bound_nodes or source_part in source_parts
                    or type(source_part) is not int or source_part < 0 or member['id'] in member_ids):
                raise ValueError('Duplicate or invalid optical member/source binding')
            source_parts.add(source_part); member_ids.add(member['id'])
            bound_nodes[node_index] = (rid, member, tie_group)
        groups[rid] = {'group_id': gid, 'source_part_indices': list(group['source_part_indices']),
            'appearance': appearance.to_dict(), 'identity': group['identity'],
            'surface_binding': {'schema_version': 1, 'prepared_glb_sha256': digest, 'material_group_id': gid,
                'coordinate_method': METHOD, 'uv_semantics': 'lens_local_bottom_0_top_1',
                'optical_profile': PROFILE, 'source_sha256': receipt['source_sha256'],
                'group_preparation_sha256': group['prepared_group_sha256'],
                'export_receipt_sha256': receipt['receipt_sha256'], 'coordinate_frame': group['coordinate_frame'],
                'normal_response': 'symmetric effective nearest-group event; normalized interpolated normal; abs(N.V)'}}
    found = set(); roots = doc['scenes'][doc.get('scene', 0)]['nodes']
    for part in mesh.parts:
        node_index = part['node_index']
        if node_index not in bound_nodes:
            if part.get('has_lens_appearance_extension'):
                raise ValueError('Unbound canonical optical primitive in exported scene')
            continue
        rid, member, tie_group = bound_nodes[node_index]; node = doc['nodes'][node_index]
        if (node_index in found or node_index not in roots or part['primitive_index'] != 0
                or len(doc['meshes'][part['mesh_index']]['primitives']) != 1
                or part['mesh_index'] != member['mesh_index'] or part['material_index'] != member['material_index']
                or member['material_index'] != tie_group['material_index']
                or any(k in node for k in ('matrix', 'translation', 'rotation', 'scale'))
                or node.get('extras') != member['node_metadata']
                or type(node['extras'].get('opticalSourcePartIndex')) is not int
                or any(node['extras'].get(key) != value for key, value in {
                    'partRole': 'lens', 'lensSurfaceProfile': PROFILE, 'lensUVConvention': VERTICAL_COORDINATE,
                    'opticalGroupId': tie_group['group_id'], 'opticalGroupMemberId': member['id'],
                    'opticalSourcePartIndex': member['source_part_index'], 'opticalSourceSha256': receipt['source_sha256'],
                    'lensAppearanceSha256': tie_group['appearance_sha256'], 'semanticIdentity': 'unverified',
                    'materialIdentification': 'unmeasured'}.items())):
            raise ValueError('Exported optical member transform/profile/binding mismatch')
        found.add(node_index)
        primitive = doc['meshes'][part['mesh_index']]['primitives'][0]; attrs = primitive['attributes']
        for attribute, kind in (('POSITION', 'VEC3'), ('NORMAL', 'VEC3'), ('TEXCOORD_0', 'VEC2')):
            accessor = doc['accessors'][attrs[attribute]]
            if (accessor.get('componentType') != 5126 or accessor.get('type') != kind
                    or accessor.get('normalized', False) or 'sparse' in accessor):
                raise ValueError('Effective optical attributes must be actual nonsparse float32 accessors')
        arrays = {name: _float_accessor(doc, binary, attrs[attr], width).astype('<f4') for name, attr, width in
                  (('positions', 'POSITION', 3), ('normals', 'NORMAL', 3), ('uv', 'TEXCOORD_0', 2))}
        arrays['indices'] = _triangles(doc, binary, primitive, len(arrays['positions'])).astype('<u4')
        arrays, _ = _quantize(arrays)
        if any(_sha(arrays[k].tobytes()) != member['attribute_sha256'][k] for k in _ATTRIBUTES):
            raise ValueError('Exported optical attribute SHA-256 mismatch')
        if len(arrays['positions']) != member['vertices'] or len(arrays['indices']) != member['triangles']:
            raise ValueError('Exported optical attribute count mismatch')
        start, count = part['vertex_start'], part['vertex_count']
        if not np.array_equal(mesh.vertices[start:start+count], arrays['positions'].astype(float)):
            raise ValueError('Exported optical coordinates differ from actual scene world coordinates')
        uv[start:start+count] = arrays['uv']; normals[start:start+count] = arrays['normals']
        face_groups[part['face_start']:part['face_start']+part['face_count']] = rid
        tie_group['members'].append({'id': member['id'], 'arrays': arrays})
    if found != set(bound_nodes):
        raise ValueError('Missing exported optical group members')
    runtime_validation = _check_runtime(tie_groups)
    if _sha(Path(path).read_bytes()) != digest:
        raise ValueError('Exported optical group GLB changed during read')
    return {'mesh': mesh, 'uv': uv, 'normals': normals, 'face_groups': face_groups, 'groups': groups,
            'coincident_patch_validation': runtime_validation['coincident_patch_validation'],
            'runtime_contract_validation': runtime_validation, 'output_sha256': digest,
            'receipt_sha256': receipt['receipt_sha256'], 'accepted': False, 'quality_verdict': 'unmeasured'}
