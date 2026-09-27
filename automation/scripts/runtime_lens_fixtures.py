"""Build local complete-frame fixtures for the actual AR renderer conformance.

The shipped Amber frame/temples and embedded textures are preserved. Its original
optical primitive is replaced by two authored planar front sheets. These are
controlled shader/renderer fixtures, not reconstructed glasses or product fits.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconstruction.deform_glb import _read
from reconstruction.lens_asset import EXTENSION


def pack(document, binary):
    binary = bytes(binary)
    document['buffers'][0]['byteLength'] = len(binary)
    encoded = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary += b'\0' * (-len(binary) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28+len(encoded)+len(binary))
            + struct.pack('<II', len(encoded), 0x4E4F534A)+encoded
            + struct.pack('<II', len(binary), 0x004E4942)+binary)


def build(source, appearance, *, invalid=None, normal_magnitudes=None):
    _, document, original_binary = _read(source)
    binary = bytearray(original_binary)
    # Retain every opaque source primitive, including the full temples.
    removed = 0
    for mesh in document['meshes']:
        retained = []
        for primitive in mesh['primitives']:
            material = document.get('materials', [])[primitive['material']]
            transmission = material.get('extensions', {}).get('KHR_materials_transmission', {}).get('transmissionFactor', 0)
            if transmission > 0:
                removed += 1
            else:
                retained.append(primitive)
        mesh['primitives'] = retained
    if not removed:
        raise ValueError('Expected an authored optical primitive in the shipped frame')
    for node in document['nodes']:
        if 'mesh' in node:
            node.setdefault('extras', {})['partRole'] = 'frame'
    material_id = len(document['materials'])
    document['materials'].append({'name': 'Canonical runtime fixture lens', 'doubleSided': False,
        'pbrMetallicRoughness': {'baseColorFactor': [.2, .2, .2, 1], 'metallicFactor': 0,
                                'roughnessFactor': appearance['roughness']},
        'extensions': {'KHR_materials_transmission': {'transmissionFactor': 1},
                       EXTENSION: {'schema_version': 1, 'texcoord': 0, 'appearance': appearance}}})
    document['extensionsUsed'] = sorted(set(document.get('extensionsUsed', []) + [EXTENSION, 'KHR_materials_transmission']))

    def accessor(values, kind, index=False):
        array = np.asarray(values, dtype='<u4' if index else '<f4')
        binary.extend(b'\0' * (-len(binary) % 4))
        view = len(document['bufferViews'])
        document['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes,
                                         'target': 34963 if index else 34962})
        binary.extend(array.tobytes())
        descriptor = {'bufferView': view, 'componentType': 5125 if index else 5126, 'count': len(array), 'type': kind}
        if kind == 'VEC3':
            descriptor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        document['accessors'].append(descriptor)
        return len(document['accessors'])-1

    for side, cx in [('left', -.032), ('right', .032)]:
        # Analytic oracle: x=cx+.052*(u-.5), y=-.008+.036*(v-.5), z=.002.
        positions = [[cx-.026, -.026, .002], [cx+.026, -.026, .002],
                     [cx-.026, .010, .002], [cx+.026, .010, .002]]
        normals = [[0, 0, -1 if invalid == 'flipped_normals' else magnitude]
                   for magnitude in (normal_magnitudes or [1]*4)]
        attributes = {'POSITION': accessor(positions, 'VEC3'), 'NORMAL': accessor(normals, 'VEC3'),
                      'TEXCOORD_0': accessor([[0, 0], [1, 0], [0, 1], [1, 1]], 'VEC2')}
        if invalid == 'missing_uv':
            attributes.pop('TEXCOORD_0')
        primitive = {'attributes': attributes, 'indices': accessor([0, 1, 2, 1, 3, 2], 'SCALAR', True),
                     'material': material_id, 'mode': 4}
        mesh_id = len(document['meshes'])
        document['meshes'].append({'name': f'Authored {side} optical front sheet', 'primitives': [primitive]})
        node_id = len(document['nodes'])
        document['nodes'].append({'name': f'Canonical {side} lens', 'mesh': mesh_id,
            'extras': {'partRole': 'lens', 'lensSurfaceProfile': 'unsupported_closed_slab' if invalid == 'invalid_profile' else 'front_sheet_v1',
                       'lensUVConvention': 'v=0 bottom, v=1 top; local to each lens'}})
        document['scenes'][document.get('scene', 0)]['nodes'].append(node_id)
    if invalid == 'gpu_instancing':
        document['nodes'][-1]['extensions'] = {'EXT_mesh_gpu_instancing': {
            'attributes': {'TRANSLATION': accessor([[0, 0, 0], [.002, 0, 0]], 'VEC3')}}}
        document['extensionsUsed'].append('EXT_mesh_gpu_instancing')
    document.setdefault('extras', {})['runtimeConformanceFixture'] = {'version': 1,
        'scope': 'Shipped opaque frame plus planar authored optical sheets; renderer evidence only'}
    return pack(document, binary)


def build_layers(source, sheets, opaque, *, parents=()):
    """Keep the shipped frame; append independently described height-field sheets.

    z = z0 + tilt_x*dx + tilt_y*dy + curve_x*dx² + curve_y*dy².
    The manifest records the continuous recipe and grid resolution. The browser
    reconstructs its own triangles, UVs and normalized vertex normals from that
    recipe, never treating the loaded GLB attributes as its expected answer.
    """
    base = build(source, sheets[0]['appearance'])
    json_length = struct.unpack_from('<I', base, 12)[0]
    document = json.loads(base[20:20+json_length])
    binary_start = 28+json_length
    binary = bytearray(base[binary_start:])
    # The two base optics become unreachable; retained binary/image offsets stay
    # byte-exact. Only the authored sheets below belong to the selected scene.
    roots = document['scenes'][document.get('scene', 0)]['nodes']
    roots[:] = [index for index in roots if document['nodes'][index].get('extras', {}).get('partRole') != 'lens']

    def accessor(values, kind, index=False):
        array = np.asarray(values, dtype='<u4' if index else '<f4')
        binary.extend(b'\0' * (-len(binary) % 4))
        view = len(document['bufferViews'])
        document['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes,
                                       'target': 34963 if index else 34962})
        binary.extend(array.tobytes())
        descriptor = {'bufferView': view, 'componentType': 5125 if index else 5126, 'count': len(array), 'type': kind}
        if kind == 'VEC3':
            descriptor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        document['accessors'].append(descriptor)
        return len(document['accessors'])-1

    node_ids = {}
    for spec in [*sheets, *opaque]:
        optical = 'appearance' in spec
        nx, ny = spec['segments']
        points, normals, uvs, indices = [], [], [], []
        for iy in range(ny+1):
            for ix in range(nx+1):
                u, v = ix/nx, iy/ny
                dx, dy = spec['width']*(u-.5), spec['height']*(v-.5)
                tx, ty = spec.get('tilt', [0, 0])
                kx, ky = spec.get('curvature', [0, 0])
                points.append([spec['center'][0]+dx, spec['center'][1]+dy,
                               spec['z']+tx*dx+ty*dy+kx*dx*dx+ky*dy*dy])
                normal = np.asarray([-tx-2*kx*dx, -ty-2*ky*dy, 1.])
                normal /= np.linalg.norm(normal)
                if spec.get('unequal_normal_magnitudes'):
                    normal *= [.25, 2., .5, 3.][len(points) % 4]
                normals.append(normal.tolist())
                uvs.append([u, v])
        for iy in range(ny):
            for ix in range(nx):
                a = iy*(nx+1)+ix
                indices.extend([a, a+1, a+nx+1, a+1, a+nx+2, a+nx+1])
        if spec.get('retessellation_duplicate'):
            if (nx, ny) != (1, 1) or any(spec.get('curvature', [0, 0])) or any(spec.get('tilt', [0, 0])):
                raise ValueError('Duplicate-patch control requires a flat one-cell sheet')
            points.append([*spec['center'], spec['z']]);normals.append([0, 0, 1]);uvs.append([.5, .5])
            indices.extend([0, 1, 4, 1, 3, 4, 3, 2, 4, 2, 0, 4])
        material_id = len(document['materials'])
        if optical:
            appearance = spec['appearance']
            material = {'name': spec['id'], 'doubleSided': False,
                        'pbrMetallicRoughness': {'baseColorFactor': [1, 1, 1, 1], 'metallicFactor': 0,
                                                'roughnessFactor': appearance['roughness']},
                        'extensions': {'KHR_materials_transmission': {'transmissionFactor': 1},
                                       EXTENSION: {'schema_version': 1, 'texcoord': 0, 'appearance': appearance}}}
            extras = {'partRole': 'lens', 'lensSurfaceProfile': 'front_sheet_v1', 'runtimeConformanceId': spec['id']}
        else:
            material = {'name': spec['id'], 'doubleSided': False,
                        'pbrMetallicRoughness': {'baseColorFactor': [*spec['linear_rgb'], 1], 'metallicFactor': 0},
                        'extensions': {'KHR_materials_unlit': {}}}
            extras = {'partRole': 'frame', 'runtimeConformanceOpaque': True, 'runtimeConformanceId': spec['id']}
            document['extensionsUsed'] = sorted(set(document['extensionsUsed'] + ['KHR_materials_unlit']))
        document['materials'].append(material)
        mesh_id = len(document['meshes'])
        document['meshes'].append({'name': spec['id'], 'primitives': [{
            'attributes': {'POSITION': accessor(points, 'VEC3'), 'NORMAL': accessor(normals, 'VEC3'),
                           'TEXCOORD_0': accessor(uvs, 'VEC2')},
            'indices': accessor(indices, 'SCALAR', True), 'material': material_id}]})
        node_id = len(document['nodes'])
        document['nodes'].append({'name': spec['id'], 'mesh': mesh_id, 'extras': extras})
        node_ids[spec['id']] = node_id
        roots.append(node_id)
    for parent, child in parents:
        document['nodes'][node_ids[parent]].setdefault('children', []).append(node_ids[child])
        roots.remove(node_ids[child])
    return pack(document, binary)


def layer_cases(cases):
    appearances = {entry['id']: entry['appearance'] for entry in cases['cases']}

    def sheet(id, kind, z, **extra):
        return {'id': id, 'center': [0, -.008], 'width': .110, 'height': .036, 'z': z,
                'segments': [1, 1], 'tilt': [0, 0], 'curvature': [0, 0],
                'appearance': deepcopy(appearances[kind]), **extra}

    a = lambda z: sheet('layer_a', 'vertical_gradient', z)
    b = lambda z: sheet('layer_b', 'strong_colored_mirror', z)
    c = lambda z: sheet('layer_c', 'saturated_tint', z)
    entries = [
        {'id': 'two_layers', 'sheets': [a(.007), b(.001)], 'minimum_overlap': 2},
        {'id': 'reversed_two_layers', 'sheets': [a(.001), b(.007)], 'minimum_overlap': 2},
        {'id': 'three_layers', 'sheets': [a(.009), b(.004), c(-.001)], 'minimum_overlap': 3},
        {'id': 'four_layers', 'sheets': [a(.012), b(.008), c(.004), sheet('layer_d', 'neutral_tint', 0)], 'minimum_overlap': 4},
        {'id': 'crossing_layers', 'sheets': [sheet('layer_a', 'vertical_gradient', .004, tilt=[.32, .13]),
                                          sheet('layer_b', 'strong_colored_mirror', .003, tilt=[-.32, -.13])],
         'minimum_overlap': 2, 'requires_multiple_orders': True},
        {'id': 'opaque_between_layers', 'sheets': [a(.008), b(0)], 'minimum_overlap': 2,
         'opaque': [{'id': 'known_opaque_strip', 'center': [0, -.008], 'width': .022, 'height': .030,
                     'z': .004, 'segments': [1, 1], 'linear_rgb': [.65, .08, .22]}]},
        {'id': 'opaque_before_layers', 'sheets': [a(.008), b(0)], 'minimum_overlap': 2,
         'opaque': [{'id': 'known_opaque_strip', 'center': [0, -.008], 'width': .022, 'height': .030,
                     'z': .012, 'segments': [1, 1], 'linear_rgb': [.65, .08, .22]}]},
        {'id': 'opaque_behind_layers', 'sheets': [a(.008), b(0)], 'minimum_overlap': 2,
         'opaque': [{'id': 'known_opaque_strip', 'center': [0, -.008], 'width': .022, 'height': .030,
                     'z': -.004, 'segments': [1, 1], 'linear_rgb': [.65, .08, .22]}]},
        {'id': 'total_mirror_front', 'sheets': [sheet('total_mirror', 'total_mirror', .008), a(0)], 'minimum_overlap': 2},
        {'id': 'total_mirror_behind', 'sheets': [a(.008), sheet('total_mirror', 'total_mirror', 0)], 'minimum_overlap': 2},
        {'id': 'distinct_left_right', 'sheets': [sheet('left_sheet', 'multi_stop_gradient', .008, center=[-.032, -.008], width=.052),
                                              sheet('right_sheet', 'angular_coating', .008, center=[.032, -.008], width=.052),
                                              c(0)], 'minimum_overlap': 2},
        {'id': 'curved_layer_normals', 'sheets': [sheet('curved_front', 'angular_coating', .006,
                                                     curvature=[2, 10], segments=[16, 8], unequal_normal_magnitudes=True),
                                               a(0)], 'minimum_overlap': 2},
        {'id': 'nested_layers_with_opaque', 'sheets': [a(.008), b(0)], 'minimum_overlap': 2,
         'opaque': [{'id': 'known_opaque_strip', 'center': [0, -.008], 'width': .022, 'height': .030,
                     'z': .004, 'segments': [1, 1], 'linear_rgb': [.65, .08, .22]}],
         'parents': [['layer_a', 'layer_b'], ['layer_a', 'known_opaque_strip']]},
    ]
    for entry in entries:
        entry.setdefault('opaque', [])
        entry['label'] = entry['id'].replace('_', ' ')
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT.parent/'ar/public/models/amber-horizon.glb')
    parser.add_argument('--cases', type=Path, default=ROOT/'data/lens-conformance/cases.json')
    parser.add_argument('--output', type=Path, default=ROOT.parent/'ar/qa/output/canonical-lens-fixtures')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases_bytes = args.cases.read_bytes()
    cases = json.loads(cases_bytes)
    manifest = {'schema_version': 1, 'source_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest(),
                'cases_sha256': hashlib.sha256(cases_bytes).hexdigest(),
                'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'sheet': {'centers_x': [-.032, .032], 'center_y': -.008, 'z': .002, 'width': .052, 'height': .036},
                'cases': [], 'negative_controls': [], 'layer_cases': [], 'layer_negative_controls': [], 'display_cases': []}
    entries = [(entry['id'], entry['label'], entry['appearance']) for entry in cases['cases']]
    mirror = next(entry['appearance'] for entry in cases['cases'] if entry['id'] == 'total_mirror')
    angular = next(entry['appearance'] for entry in cases['cases'] if entry['id'] == 'angular_coating')
    entries.append(('angular_nonunit_normals', 'Angular coating; nonunit normals', deepcopy(angular)))
    for roughness in (.25, .75):
        appearance = deepcopy(mirror)
        appearance['roughness'] = roughness
        entries.append((f'mirror_roughness_{roughness:g}', f'Rough mirror {roughness:g}', appearance))
    for id, label, appearance in entries:
        normal_magnitudes = [.25, 2, .5, 3] if id == 'angular_nonunit_normals' else [1]*4
        data = build(args.source, appearance, normal_magnitudes=normal_magnitudes)
        filename = f'{id}.glb'
        (args.output/filename).write_bytes(data)
        manifest['cases'].append({'id': id, 'label': label, 'appearance': appearance,
                                  'model': filename, 'normal_magnitudes': normal_magnitudes,
                                  'model_sha256': hashlib.sha256(data).hexdigest()})
    for invalid in ('missing_uv', 'invalid_profile', 'flipped_normals', 'gpu_instancing'):
        data = build(args.source, mirror, invalid=invalid)
        filename = f'invalid-{invalid}.glb'
        (args.output/filename).write_bytes(data)
        manifest['negative_controls'].append({'id': invalid, 'model': filename,
                                               'model_sha256': hashlib.sha256(data).hexdigest()})
    for entry in layer_cases(cases):
        data = build_layers(args.source, entry['sheets'], entry['opaque'], parents=entry.get('parents', []))
        filename = f'layers-{entry["id"]}.glb'
        (args.output/filename).write_bytes(data)
        manifest['layer_cases'].append({**entry, 'model': filename, 'model_sha256': hashlib.sha256(data).hexdigest()})
    overflow = deepcopy(manifest['layer_cases'][0])
    overflow['sheets'] = [dict(deepcopy(overflow['sheets'][i % 2]), id=f'overflow_{i}', z=.012-i*.004) for i in range(5)]
    data = build_layers(args.source, overflow['sheets'], [])
    filename = 'layers-overflow-five.glb'
    (args.output/filename).write_bytes(data)
    manifest['layer_negative_controls'].append({'id': 'five_overlapping_sheets', 'model': filename,
                                               'model_sha256': hashlib.sha256(data).hexdigest(), 'sheet_count': 5,
                                               'expected_error': 'overflow'})
    coincident = [dict(deepcopy(overflow['sheets'][i]), z=.004) for i in range(2)]
    data = build_layers(args.source, coincident, [])
    filename = 'layers-coincident.glb'
    (args.output/filename).write_bytes(data)
    manifest['layer_negative_controls'].append({'id': 'coincident_sheets', 'model': filename,
                                               'model_sha256': hashlib.sha256(data).hexdigest(), 'sheet_count': 2,
                                               'expected_error': 'coincident'})
    duplicate = dict(deepcopy(overflow['sheets'][0]), retessellation_duplicate=True)
    data = build_layers(args.source, [duplicate], [])
    filename = 'layers-retessellated-duplicate.glb'
    (args.output/filename).write_bytes(data)
    manifest['layer_negative_controls'].append({'id': 'retessellated_duplicate_same_mesh', 'model': filename,
                                               'model_sha256': hashlib.sha256(data).hexdigest(), 'sheet_count': 1,
                                               'expected_error': 'coincident'})
    identity = deepcopy(next(entry['appearance'] for entry in cases['cases'] if entry['id'] == 'clear'))
    identity['normal_reflectance_rgb'] = [0, 0, 0]
    identity['angular_reflectance_keyframes'] = [{'angle_degrees': angle, 'reflectance_rgb': [0, 0, 0]} for angle in (0, 90)]
    data = build(args.source, identity)
    filename = 'clear-identity-display.glb'
    (args.output/filename).write_bytes(data)
    manifest['display_cases'].append({'id': 'clear_identity_display', 'model': filename, 'appearance': identity,
                                      'model_sha256': hashlib.sha256(data).hexdigest()})
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'cases': len(entries), 'negative_controls': len(manifest['negative_controls']),
                      'layer_cases': len(manifest['layer_cases']), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
