"""Reversible face partitions of static embedded GLBs, without rebaking attributes.

Connectivity and optical identity are separate. This module compiles supplied
complete face partitions and verifies original face occurrences, including
duplicates and degenerates. It does not infer labels or preserve transparent
draw sorting: one output primitive is emitted per declared piece.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

import numpy as np

from .deform_glb import _read_bytes, _float_accessor, _triangles
from .lens_asset import _pack_glb
from .mesh import _node_matrix


METHOD = 'source_preserving_face_partition_v1'
LIMITS = {'source_bytes': 512_000_000, 'source_faces': 4_000_000,
          'source_vertex_instances': 8_000_000, 'source_primitives': 4096,
          'output_primitives': 4096, 'output_accessor_vertex_instances': 8_000_000}
_BINDING = ('node_index', 'mesh_index', 'primitive_index')
_SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_DTYPES = {5120: 'i1', 5121: 'u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
_WIDTHS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _seal(value):
    return {**value, 'receipt_sha256': _sha(_json_bytes(value))}


def _same_json(left, right):
    # Python's False == 0 and True == 1 must not certify preserved glTF types.
    return _json_bytes(left) == _json_bytes(right)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _extensions(value, permitted, name):
    if set(value.get('extensions', {}))-set(permitted):
        raise ValueError(f'Unsupported {name} extension')


def _view(doc, binary, view_index, offset, count, dtype, width, *, sparse=False):
    _integer(view_index, 'buffer view')
    if view_index >= len(doc.get('bufferViews', [])):
        raise ValueError('Invalid buffer view index')
    view = doc['bufferViews'][view_index]
    _extensions(view, (), 'buffer view')
    start = _integer(view.get('byteOffset', 0), 'view offset')
    length = _integer(view['byteLength'], 'view length', 1)
    offset = _integer(offset, 'accessor offset')
    stride = _integer(view.get('byteStride', dtype.itemsize*width), 'stride', 1)
    end = offset+(count-1)*stride+dtype.itemsize*width
    if (view.get('buffer', 0) != 0 or stride < dtype.itemsize*width or stride % dtype.itemsize
            or (start+offset) % dtype.itemsize or end > length
            or start+length > doc['buffers'][0]['byteLength'] or start+length > len(binary)
            or (sparse and 'byteStride' in view)):
        raise ValueError('Invalid attribute buffer range/stride/alignment')
    return np.ndarray((count, width), dtype=dtype, buffer=binary, offset=start+offset,
                      strides=(stride, dtype.itemsize))


def _validate_attribute(doc, binary, accessor_index, vertex_count):
    _integer(accessor_index, 'attribute accessor')
    if accessor_index >= len(doc.get('accessors', [])):
        raise ValueError('Invalid attribute accessor index')
    acc = doc['accessors'][accessor_index]
    _extensions(acc, (), 'attribute accessor')
    count = _integer(acc['count'], 'attribute count', 1)
    if count != vertex_count or acc.get('type') not in _WIDTHS or acc.get('componentType') not in (5120, 5121, 5122, 5123, 5126):
        raise ValueError('Vertex attribute type/count mismatch')
    dtype, width = np.dtype(_DTYPES[acc['componentType']]), _WIDTHS[acc['type']]
    if type(acc.get('normalized', False)) is not bool or (acc.get('normalized') and acc['componentType'] not in (5120, 5121, 5122, 5123)):
        raise ValueError('Invalid normalized attribute')
    if 'bufferView' in acc:
        _view(doc, binary, acc['bufferView'], acc.get('byteOffset', 0), count, dtype, width)
    elif acc.get('byteOffset', 0) != 0:
        raise ValueError('Implicit-zero attribute cannot have a byte offset')
    if 'sparse' in acc:
        sparse = acc['sparse']
        _extensions(sparse, (), 'sparse attribute')
        n = _integer(sparse['count'], 'sparse count', 1)
        if n > count:
            raise ValueError('Invalid sparse count')
        idx, val = sparse['indices'], sparse['values']
        _extensions(idx, (), 'sparse indices'); _extensions(val, (), 'sparse values')
        if idx.get('componentType') not in (5121, 5123, 5125):
            raise ValueError('Invalid sparse index type')
        indices = _view(doc, binary, idx['bufferView'], idx.get('byteOffset', 0), n,
                        np.dtype(_DTYPES[idx['componentType']]), 1, sparse=True).ravel()
        if np.any(indices >= count) or np.any(indices[1:] <= indices[:-1]):
            raise ValueError('Sparse indices must be in bounds and strictly increasing')
        _view(doc, binary, val['bufferView'], val.get('byteOffset', 0), n, dtype, width, sparse=True)


def inspect_partition_source(raw):
    """Return all selected-scene primitive instances, including alpha-zero parts.

    positions/indices are local source arrays. No vertex welding, transparency
    filter or optical-role filter is applied. The returned document is decoded
    from the same bytes and may be used to construct an external inventory.
    """
    if not isinstance(raw, bytes) or len(raw) > LIMITS['source_bytes']:
        raise ValueError('Expected bounded captured GLB bytes')
    _, doc, binary = _read_bytes(raw)
    if doc.get('asset', {}).get('version') != '2.0' or doc.get('asset', {}).get('minVersion', '2.0') != '2.0':
        raise ValueError('Expected glTF asset version 2.0')
    supported_required = {'KHR_lights_punctual', 'KHR_materials_variants', 'KHR_texture_transform',
        'KHR_materials_clearcoat', 'KHR_materials_transmission', 'KHR_materials_volume', 'KHR_materials_ior',
        'KHR_materials_specular', 'KHR_materials_sheen', 'KHR_materials_iridescence', 'KHR_materials_anisotropy',
        'KHR_materials_emissive_strength', 'KHR_materials_unlit', 'KHR_materials_pbrSpecularGlossiness'}
    if set(doc.get('extensionsRequired', []))-supported_required or set(doc.get('extensionsRequired', []))-set(doc.get('extensionsUsed', [])):
        raise ValueError('Unsupported or undeclared required extension')
    _extensions(doc, ('KHR_lights_punctual', 'KHR_materials_variants'), 'document')
    if any('LENSES_lens_appearance' in m.get('extensions', {}) for m in doc.get('materials', [])):
        raise ValueError('Partition source geometry before canonical optical compilation')
    if any(k in doc.get('extras', {}) for k in ('effectiveOpticalGroups', 'opticalPreparation')):
        raise ValueError('Partition before source-bound optical preparation metadata is attached')
    for image in doc.get('images', []):
        if 'uri' in image and (not isinstance(image['uri'], str) or not image['uri'].startswith('data:')):
            raise ValueError('Embed external image resources before partition export')
    scenes, nodes, meshes = doc.get('scenes', []), doc.get('nodes', []), doc.get('meshes', [])
    scene = _integer(doc.get('scene', 0), 'selected scene')
    if scene >= len(scenes):
        raise ValueError('Invalid selected scene')
    matrices, instances, face_count, vertex_instances = {}, [], 0, 0

    def visit(index, parent):
        nonlocal face_count, vertex_instances
        _integer(index, 'node')
        if index >= len(nodes) or index in matrices:
            raise ValueError('Selected scene must have unique acyclic node instances')
        node = nodes[index]
        _extensions(node, ('KHR_lights_punctual',), 'node')
        if ('matrix' in node and any(k in node for k in ('translation', 'rotation', 'scale'))
                or 'skin' in node or 'weights' in node):
            raise ValueError('Unsupported dynamic node or conflicting matrix/TRS')
        matrix = parent @ _node_matrix(node)
        if not np.isfinite(matrix).all() or np.linalg.det(matrix[:3, :3]) == 0:
            raise ValueError('Invalid or singular source transform')
        matrices[index] = matrix
        if 'mesh' in node:
            mi = _integer(node['mesh'], 'mesh')
            if mi >= len(meshes):
                raise ValueError('Invalid source mesh')
            mesh = meshes[mi]
            _extensions(mesh, (), 'mesh')
            if 'weights' in mesh or not mesh.get('primitives'):
                raise ValueError('Expected nonempty static mesh')
            for pi, primitive in enumerate(mesh['primitives']):
                if len(instances) >= LIMITS['source_primitives']:
                    raise ValueError('Source primitive-instance capacity exceeded')
                _extensions(primitive, ('KHR_materials_variants',), 'primitive')
                if primitive.get('mode', 4) != 4 or 'targets' in primitive:
                    raise ValueError('Only static triangle primitives are supported')
                attrs = primitive['attributes']
                pos_id = _integer(attrs['POSITION'], 'POSITION accessor')
                if pos_id >= len(doc.get('accessors', [])):
                    raise ValueError('Invalid POSITION accessor index')
                vertex_instances += _integer(doc['accessors'][pos_id]['count'], 'POSITION count', 1)
                if vertex_instances > LIMITS['source_vertex_instances']:
                    raise ValueError('Source vertex-instance capacity exceeded before attribute allocation')
                positions = _float_accessor(doc, binary, pos_id, 3)
                for ai in attrs.values():
                    _validate_attribute(doc, binary, ai, len(positions))
                if 'indices' in primitive:
                    ai = _integer(primitive['indices'], 'index accessor')
                    if ai >= len(doc.get('accessors', [])):
                        raise ValueError('Invalid index accessor')
                    acc = doc['accessors'][ai]
                    if acc.get('componentType') not in (5121, 5123, 5125):
                        raise ValueError('Expected unsigned indices')
                    index_count = _integer(acc['count'], 'index count', 1)
                    _view(doc, binary, acc['bufferView'], acc.get('byteOffset', 0),
                          index_count, np.dtype(_DTYPES[acc['componentType']]), 1)
                else:
                    index_count = len(positions)
                if index_count % 3 or face_count+index_count//3 > LIMITS['source_faces']:
                    raise ValueError('Invalid index count or source face capacity exceeded before index allocation')
                indices = _triangles(doc, binary, primitive, len(positions))
                if 'indices' in primitive and np.any(indices == np.iinfo(np.dtype(_DTYPES[acc['componentType']])).max):
                    raise ValueError('Primitive-restart sentinel is not a glTF triangle index')
                face_count += len(indices)
                if face_count > LIMITS['source_faces']:
                    raise ValueError('Source face capacity exceeded')
                if 'material' in primitive:
                    material = _integer(primitive['material'], 'material')
                    if material >= len(doc.get('materials', [])):
                        raise ValueError('Invalid material index')
                instances.append({'source_binding': dict(zip(_BINDING, (index, mi, pi))),
                                  'positions': positions, 'indices': indices,
                                  'world_matrix': matrix, 'primitive': primitive})
        for child in node.get('children', []):
            visit(child, matrix)

    for root in scenes[scene].get('nodes', []):
        visit(root, np.eye(4))
    if not instances:
        raise ValueError('Selected scene contains no triangle primitives')
    return {'document': doc, 'binary': binary, 'selected_scene': scene,
            'matrices': matrices, 'instances': instances}


PIECE_ROLES = ('optical', 'interior_contact')


def _declarations(value, source_hash, instances):
    if not isinstance(value, dict) or set(value) != {'schema_version', 'source_sha256', 'provenance', 'partitions'}:
        raise ValueError('Invalid partition declaration fields')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['source_sha256'] != source_hash:
        raise ValueError('Partition declaration source/schema mismatch')
    if not isinstance(value['provenance'], dict) or not isinstance(value['provenance'].get('method'), str) or not value['provenance']['method']:
        raise ValueError('Partition provenance method is required')
    if not isinstance(value['partitions'], list) or not value['partitions']:
        raise ValueError('At least one declared partition is required')
    lookup = {tuple(row['source_binding'][k] for k in _BINDING): row for row in instances}
    result, bindings, ids = [], set(), set()
    output_count = len(instances)
    output_vertices = sum(len(row['positions']) for row in instances)
    for part in value['partitions']:
        if not isinstance(part, dict) or set(part) != {'source_binding', 'pieces'}:
            raise ValueError('Invalid partition fields')
        binding = part['source_binding']
        if not isinstance(binding, dict) or set(binding) != set(_BINDING):
            raise ValueError('Invalid partition source binding')
        key = tuple(_integer(binding[k], k) for k in _BINDING)
        if key not in lookup or key in bindings:
            raise ValueError('Unknown or repeated source primitive instance')
        bindings.add(key)
        count = len(lookup[key]['indices'])
        if not isinstance(part['pieces'], list) or not part['pieces']:
            raise ValueError('Partition requires nonempty pieces')
        output_count += len(part['pieces'])-1
        output_vertices += (len(part['pieces'])-1)*len(lookup[key]['positions'])
        if output_count > LIMITS['output_primitives'] or output_vertices > LIMITS['output_accessor_vertex_instances']:
            raise ValueError('Partition exceeds output capacity before membership allocation')
        owner = np.full(count, -1, np.int64)
        pieces = []
        for ordinal, piece in enumerate(part['pieces']):
            if not isinstance(piece, dict) or not {'id', 'source_face_indices'} <= set(piece) <= {'id', 'source_face_indices', 'declared_role'}:
                raise ValueError('Invalid partition piece fields')
            pid, faces = piece['id'], piece['source_face_indices']
            if not isinstance(pid, str) or not _SLUG.fullmatch(pid) or pid in ids:
                raise ValueError('Unique safe piece IDs are required')
            ids.add(pid)
            if not isinstance(faces, list) or not faces or len(faces) > count or any(type(f) is not int or not 0 <= f < count for f in faces):
                raise ValueError('Piece face ordinals must be nonempty integer source indices')
            faces = np.sort(np.asarray(faces, np.int64))
            if np.any(faces[1:] == faces[:-1]) or np.any(owner[faces] >= 0):
                raise ValueError('Every source face occurrence must appear exactly once')
            owner[faces] = ordinal
            role = piece.get('declared_role')
            if role is not None and role not in PIECE_ROLES:
                raise ValueError('Piece declared_role must be one of the documented roles')
            pieces.append({'id': pid, 'source_face_indices': faces.tolist(), **({'declared_role': role} if role else {})})
        if np.any(owner < 0):
            raise ValueError('Partition omits source face occurrences')
        pieces.sort(key=lambda p: p['source_face_indices'][0])
        result.append({'source_binding': dict(zip(_BINDING, key)), 'pieces': pieces})
    result.sort(key=lambda p: tuple(p['source_binding'][k] for k in _BINDING))
    canonical = {**value, 'partitions': result}
    return json.loads(_json_bytes(canonical))


def _ranges(indices):
    indices = np.asarray(indices, np.int64)
    cuts = np.flatnonzero(np.diff(indices) != 1)+1
    return [[int(block[0]), int(block[-1])+1] for block in np.split(indices, cuts)]


def _from_ranges(ranges, count):
    if not isinstance(ranges, list) or not ranges:
        raise ValueError('Nonempty source face ranges required')
    previous, arrays = -1, []
    for pair in ranges:
        if not isinstance(pair, list) or len(pair) != 2 or any(type(i) is not int for i in pair):
            raise ValueError('Invalid face range')
        start, stop = pair
        if not 0 <= start < stop <= count or start <= previous:
            raise ValueError('Face ranges must be sorted, separate and in bounds')
        arrays.append(np.arange(start, stop, dtype=np.int64)); previous = stop-1
    return np.concatenate(arrays)


def partition_glb_bytes(raw: bytes, declarations: dict) -> tuple[bytes, dict]:
    """Compile complete disjoint face sets; preserve every original attribute.

    An omitted primitive is copied unchanged. Pieces preserve source face order
    internally and are emitted by their earliest face. Cross-piece order may
    change, and transparent rendering equivalence is explicitly unmeasured.
    """
    source = inspect_partition_source(raw)
    declarations = _declarations(declarations, _sha(raw), source['instances'])
    chosen = {tuple(p['source_binding'][k] for k in _BINDING): p['pieces'] for p in declarations['partitions']}
    output_primitives = sum(len(chosen.get(tuple(r['source_binding'][k] for k in _BINDING), [None])) for r in source['instances'])
    expanded_vertices = sum(len(r['positions'])*len(chosen.get(tuple(r['source_binding'][k] for k in _BINDING), [None])) for r in source['instances'])
    if output_primitives > LIMITS['output_primitives'] or expanded_vertices > LIMITS['output_accessor_vertex_instances']:
        raise ValueError('Partition exceeds output primitive/accessor-instance capacity; retain an inventory or union pieces first')
    doc, binary = deepcopy(source['document']), bytearray(source['binary'])
    original = source['document']
    node_map = {i: len(doc['nodes'])+n for n, i in enumerate(sorted(source['matrices']))}
    for i in sorted(node_map):
        node = deepcopy(original['nodes'][i])
        if 'children' in node:
            node['children'] = [node_map[c] for c in node['children']]
        doc['nodes'].append(node)
    doc['scenes'][source['selected_scene']]['nodes'] = [node_map[i] for i in original['scenes'][source['selected_scene']].get('nodes', [])]
    changed_nodes = {key[0] for key in chosen}
    mesh_map = {}
    for ni in sorted(changed_nodes):
        old_mi = original['nodes'][ni]['mesh']
        mesh_map[ni] = len(doc['meshes'])
        mesh = deepcopy(original['meshes'][old_mi]); mesh['primitives'] = []
        doc['meshes'].append(mesh)
        doc['nodes'][node_map[ni]]['mesh'] = mesh_map[ni]

    def append_indices(values):
        values = np.asarray(values, dtype='<u4').ravel()
        binary.extend(b'\0'*(-len(binary) % 4))
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': values.nbytes, 'target': 34963})
        binary.extend(values.tobytes())
        doc['accessors'].append({'bufferView': len(doc['bufferViews'])-1, 'componentType': 5125,
                                 'count': len(values), 'type': 'SCALAR'})
        return len(doc['accessors'])-1

    rows = []
    for instance in source['instances']:
        binding = instance['source_binding']; ni, mi, pi = (binding[k] for k in _BINDING)
        indices = instance['indices']; pieces = chosen.get((ni, mi, pi))
        sets = [(p['id'], np.asarray(p['source_face_indices'], np.int64)) for p in pieces] if pieces else [(None, np.arange(len(indices)))]
        roles = {p['id']: p.get('declared_role') for p in pieces} if pieces else {}
        outputs = []
        for pid, faces in sets:
            primitive = deepcopy(instance['primitive'])
            if pieces:
                primitive['indices'] = append_indices(indices[faces])
                if roles.get(pid):
                    # A declared piece role travels on the primitive; the node keeps
                    # the source's own extras untouched.
                    primitive['extras'] = {**primitive.get('extras', {}), 'partRole': roles[pid], 'pieceId': pid}
            if ni in mesh_map:
                target = doc['meshes'][mesh_map[ni]]['primitives']
                out_pi = len(target); target.append(primitive)
            else:
                out_pi = pi
            outputs.append({'output_binding': dict(zip(_BINDING, (node_map[ni], mesh_map.get(ni, mi), out_pi))),
                            'piece_id': pid, 'source_face_ranges': _ranges(faces), 'triangle_count': len(faces)})
        sequence = np.concatenate([s[1] for s in sets])
        rows.append({'source_binding': binding, 'source_face_count': len(indices),
                     'source_vertex_count': len(instance['positions']), 'outputs': outputs,
                     'source_triangle_order_preserved': bool(np.array_equal(sequence, np.arange(len(indices))))})
    doc['buffers'][0]['byteLength'] = len(binary)
    output = _pack_glb(doc, bytes(binary))
    report = _seal({'schema_version': 1, 'method': METHOD, 'source_sha256': _sha(raw), 'output_sha256': _sha(output),
        'declarations': declarations, 'selected_scene': source['selected_scene'],
        'node_map': [{'source_node_index': i, 'output_node_index': node_map[i]} for i in sorted(node_map)],
        'primitives': rows, 'limits': dict(LIMITS), 'output_primitives': output_primitives,
        'output_accessor_vertex_instances': expanded_vertices, 'accepted': False, 'quality_verdict': 'unmeasured',
        'semantic_identity': 'not_inferred', 'render_equivalence': 'unmeasured',
        'preservation': {'original_document_array_prefixes': True, 'original_binary_prefix': True,
                         'all_vertex_accessor_references': True, 'all_source_face_occurrences': True,
                         'other_scenes': True, 'source_triangle_order': all(r['source_triangle_order_preserved'] for r in rows)},
        'limitations': ['Partitions are supplied hypotheses; source roles and materials are retained without verification.',
            'Cross-partition triangle order and renderer primitive sorting can change; geometry equality does not prove visual equivalence.',
            'Full original vertex accessors are shared by each piece; downstream decoded/exported memory may grow.',
            'Old camera/region part indices and source SHA are not valid bindings for the partitioned asset.',
            'This is a static embedded triangle profile; no welding, compaction, remeshing or optical fitting is performed.']})
    verify_partition_glb_bytes(raw, output, report)
    return output, report


def verify_partition_glb_bytes(source_raw: bytes, output_raw: bytes, report: dict) -> dict:
    """Re-read bytes and verify lineage plus unchanged document/BIN structure.

    This verifier does not invoke the exporter. The receipt hash is an integrity
    binding, not a trust signature: all membership is rechecked against source.
    """
    if not isinstance(report, dict):
        raise ValueError('Expected partition receipt')
    value = deepcopy(report); seal = value.pop('receipt_sha256', None)
    if (seal != _sha(_json_bytes(value)) or type(value.get('schema_version')) is not int or value.get('schema_version') != 1 or value.get('method') != METHOD
            or value.get('source_sha256') != _sha(source_raw) or value.get('output_sha256') != _sha(output_raw)
            or value.get('accepted') is not False or value.get('quality_verdict') != 'unmeasured'
            or value.get('render_equivalence') != 'unmeasured' or value.get('semantic_identity') != 'not_inferred'
            or not _same_json(value.get('limits'), LIMITS)):
        raise ValueError('Partition receipt/source/output mismatch')
    source, output = inspect_partition_source(source_raw), inspect_partition_source(output_raw)
    old, new = source['document'], output['document']
    canonical = _declarations(value['declarations'], _sha(source_raw), source['instances'])
    if not _same_json(canonical, value['declarations']) or not _same_json(value['selected_scene'], source['selected_scene']):
        raise ValueError('Partition declarations/scene changed')
    expected_map = {i: len(old['nodes'])+n for n, i in enumerate(sorted(source['matrices']))}
    if not _same_json(value['node_map'], [{'source_node_index': i, 'output_node_index': expected_map[i]} for i in sorted(expected_map)]):
        raise ValueError('Partition node mapping changed')
    permitted = {'nodes', 'meshes', 'accessors', 'bufferViews', 'buffers', 'scenes'}
    if not _same_json({k: v for k, v in old.items() if k not in permitted}, {k: v for k, v in new.items() if k not in permitted}):
        raise ValueError('Unrelated document/material/texture metadata changed')
    for name in ('nodes', 'meshes', 'accessors', 'bufferViews'):
        if not _same_json(new[name][:len(old[name])], old[name]):
            raise ValueError(f'Original {name} records changed')
    expected_scenes = deepcopy(old['scenes'])
    expected_scenes[source['selected_scene']]['nodes'] = [expected_map[i] for i in old['scenes'][source['selected_scene']].get('nodes', [])]
    if not _same_json(new['scenes'], expected_scenes) or len(new['nodes']) != len(old['nodes'])+len(expected_map):
        raise ValueError('Selected hierarchy or other scenes changed')
    if (len(new['buffers']) != 1 or not _same_json({k: v for k, v in new['buffers'][0].items() if k != 'byteLength'},
            {k: v for k, v in old['buffers'][0].items() if k != 'byteLength'})
            or output['binary'][:len(source['binary'])] != source['binary']):
        raise ValueError('Original embedded buffer changed')
    chosen = {tuple(p['source_binding'][k] for k in _BINDING): p['pieces'] for p in canonical['partitions']}
    changed_nodes = sorted({k[0] for k in chosen})
    mesh_map = {ni: len(old['meshes'])+n for n, ni in enumerate(changed_nodes)}
    if len(new['meshes']) != len(old['meshes'])+len(mesh_map):
        raise ValueError('Unexpected appended meshes')
    for ni, oi in expected_map.items():
        expected = deepcopy(old['nodes'][ni])
        if 'children' in expected:
            expected['children'] = [expected_map[c] for c in expected['children']]
        if ni in mesh_map:
            expected['mesh'] = mesh_map[ni]
        if not _same_json(new['nodes'][oi], expected):
            raise ValueError('Cloned node metadata/transform mismatch')
    for ni, mi in mesh_map.items():
        original_mesh = old['meshes'][old['nodes'][ni]['mesh']]
        if not _same_json({k: v for k, v in original_mesh.items() if k != 'primitives'}, {k: v for k, v in new['meshes'][mi].items() if k != 'primitives'}):
            raise ValueError('Cloned mesh metadata changed')
    if len(value['primitives']) != len(source['instances']):
        raise ValueError('Source primitive inventory changed')
    actual = {tuple(r['source_binding'][k] for k in _BINDING): r for r in output['instances']}
    found, appended, checked, order_flags, primitive_offsets = set(), 0, 0, [], {}
    cursor = len(source['binary'])
    for row, original in zip(value['primitives'], source['instances'], strict=True):
        binding = original['source_binding']; key = tuple(binding[k] for k in _BINDING)
        ni, mi, pi = key; count = len(original['indices']); pieces = chosen.get(key)
        expected_sets = [(p['id'], np.asarray(p['source_face_indices'], np.int64)) for p in pieces] if pieces else [(None, np.arange(count))]
        expected_roles = {p['id']: p.get('declared_role') for p in pieces} if pieces else {}
        if (not _same_json(row['source_binding'], binding) or not _same_json(row['source_face_count'], count) or not _same_json(row['source_vertex_count'], len(original['positions']))
                or len(row['outputs']) != len(expected_sets)):
            raise ValueError('Source primitive receipt mismatch')
        for out, (pid, expected_faces) in zip(row['outputs'], expected_sets, strict=True):
            faces = _from_ranges(out['source_face_ranges'], count)
            out_pi = primitive_offsets.get(ni, 0) if ni in mesh_map else pi
            expected_binding = dict(zip(_BINDING, (expected_map[ni], mesh_map.get(ni, mi), out_pi)))
            if not _same_json(out['output_binding'], expected_binding) or out['piece_id'] != pid or not np.array_equal(faces, expected_faces) or not _same_json(out['triangle_count'], len(faces)):
                raise ValueError('Partition face lineage/binding contradicts declaration')
            out_key = tuple(expected_binding[k] for k in _BINDING)
            if out_key not in actual or out_key in found:
                raise ValueError('Missing or duplicate output primitive')
            found.add(out_key); target = actual[out_key]
            primitive = deepcopy(original['primitive'])
            if pieces and expected_roles.get(pid):
                primitive['extras'] = {**primitive.get('extras', {}), 'partRole': expected_roles[pid], 'pieceId': pid}
            if pieces:
                ai, vi = len(old['accessors'])+appended, len(old['bufferViews'])+appended
                cursor += -cursor % 4
                index_bytes = np.asarray(original['indices'][faces], dtype='<u4').ravel().tobytes()
                expected_view = {'buffer': 0, 'byteOffset': cursor, 'byteLength': len(index_bytes), 'target': 34963}
                expected_accessor = {'bufferView': vi, 'componentType': 5125, 'count': len(faces)*3, 'type': 'SCALAR'}
                if not _same_json(new['bufferViews'][vi], expected_view) or not _same_json(new['accessors'][ai], expected_accessor) or output['binary'][cursor:cursor+len(index_bytes)] != index_bytes:
                    raise ValueError('Appended index bytes/metadata disagree with source face lineage')
                cursor += len(index_bytes); appended += 1; primitive['indices'] = ai
            if not _same_json(target['primitive'], primitive) or not np.array_equal(target['indices'], original['indices'][faces]):
                raise ValueError('Primitive attributes/material/indices changed')
            if not np.array_equal(target['world_matrix'], original['world_matrix']):
                raise ValueError('Source world transformation changed')
            checked += len(faces)
            primitive_offsets[ni] = out_pi+1
        order_flags.append(bool(np.array_equal(np.concatenate([p[1] for p in expected_sets]), np.arange(count))))
        if row['source_triangle_order_preserved'] is not order_flags[-1]:
            raise ValueError('Incorrect triangle-order claim')
    if found != set(actual) or len(new['accessors']) != len(old['accessors'])+appended or len(new['bufferViews']) != len(old['bufferViews'])+appended:
        raise ValueError('Unexpected output geometry/accessors')
    if new['buffers'][0]['byteLength'] != cursor or len(output['binary']) != cursor+(-cursor % 4) or any(output['binary'][cursor:]):
        raise ValueError('Unexpected appended binary payload')
    expected_preservation = {'original_document_array_prefixes': True, 'original_binary_prefix': True,
                            'all_vertex_accessor_references': True, 'all_source_face_occurrences': True,
                            'other_scenes': True, 'source_triangle_order': all(order_flags)}
    if not _same_json(value.get('preservation'), expected_preservation):
        raise ValueError('Incorrect preservation claim')
    if not _same_json(value.get('output_primitives'), len(actual)) or not _same_json(value.get('output_accessor_vertex_instances'), sum(len(r['positions']) for r in output['instances'])):
        raise ValueError('Incorrect output resource inventory')
    return {'status': 'verified', 'source_sha256': _sha(source_raw), 'output_sha256': _sha(output_raw),
            'triangle_occurrences_checked': checked, 'output_primitives_checked': len(actual),
            'source_triangle_order_preserved': all(order_flags), 'render_equivalence': 'unmeasured',
            'semantic_identity': 'not_inferred', 'accepted': False}
