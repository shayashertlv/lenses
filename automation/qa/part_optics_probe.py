"""QA-only optical controls on explicitly selected primitive groups.

Preserves the input; bakes one recorded similarity/scene transform into a
separate diagnostic asset, then uses the existing optical-group exporter.
145 mm is a display convention, not a measurement from product photographs.
Group membership is supplied experimental annotation, never inferred here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import io
import json
from pathlib import Path

import numpy as np

from qa.provider_benchmark import digest, write_json
from reconstruction.deform_glb import _read_bytes, _float_accessor
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import load_glb_bytes
from reconstruction.optical_group_asset import _source_matrices
from reconstruction.optical_group_asset import write_optical_group_candidate
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.scale import bridge_origin


def appearance_control(preparation, output, appearance):
    """Change only the optical descriptor on one verified saved preparation."""
    preparation, output = Path(preparation).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new appearance-control directory')
    stage = json.loads((preparation / 'report.json').read_bytes())
    def pinned(item):
        path = (preparation / item['path']).resolve()
        if not path.is_relative_to(preparation):
            raise ValueError('Preparation artifact escapes source')
        raw = path.read_bytes()
        if digest(raw) != item['sha256']:
            raise ValueError('Preparation artifact hash mismatch')
        return raw
    pinned(stage['source_snapshot'])
    groups = []
    for row in stage['groups']:
        report = json.loads(pinned(row['prepared_report']))
        primitives = []
        for member in row['primitives']:
            with np.load(io.BytesIO(pinned(member)), allow_pickle=False) as archive:
                primitives.append({'id': member['id'], **{key: archive[key].copy() for key in archive.files}})
        groups.append({'prepared': {'report': report, 'primitives': primitives}, 'appearance': appearance})
    output.mkdir(parents=True, exist_ok=True)
    receipt = write_optical_group_candidate(preparation / stage['source_snapshot']['path'], output / 'candidate.glb',
        groups, source_sha256=stage['source_sha256'],
        provenance={'method': 'QA_optical_appearance_counterfactual', 'preparation': str(preparation),
                    'purpose': 'isolate_live_optical_response_from_opaque_hardware'})
    write_json(output / 'export.json', receipt)
    return receipt


def plain_frame_control(source, output, part_indices, *, rgb=(.01, .01, .01), roughness=.45):
    """Explicit material-prior experiment on reviewed hardware; no geometry edit."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new output file')
    raw = source.read_bytes()
    _, document, binary = _read_bytes(raw)
    mesh = load_glb_bytes(raw)
    if not part_indices or len(set(part_indices)) != len(part_indices):
        raise ValueError('Need unique reviewed hardware parts')
    index = len(document.setdefault('materials', []))
    document['materials'].append({'name': 'QA_black_hardware_prior', 'doubleSided': True,
        'pbrMetallicRoughness': {'baseColorFactor': [*rgb, 1], 'metallicFactor': 0, 'roughnessFactor': roughness},
        'extras': {'purpose': 'explicit_material_prior_control_not_automatic_albedo_recovery'}})
    clones = {}
    for part_index in part_indices:
        if type(part_index) is not int or not 0 <= part_index < len(mesh.parts):
            raise ValueError('Invalid source part index')
        part = mesh.parts[part_index]
        node = part['node_index']
        if node not in clones:
            clones[node] = len(document['meshes'])
            document['meshes'].append(deepcopy(document['meshes'][part['mesh_index']]))
            document['nodes'][node]['mesh'] = clones[node]
        primitive = document['meshes'][clones[node]]['primitives'][part['primitive_index']]
        primitive['material'] = index
        for key in list(primitive['attributes']):
            if key.startswith('COLOR_'):
                primitive['attributes'].pop(key)
    changed = _pack_glb(document, binary)
    check = load_glb_bytes(changed)
    if not np.array_equal(check.vertices, mesh.vertices) or not np.array_equal(check.faces, mesh.faces):
        raise ValueError('Frame material control changed geometry')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(changed)
    report = {'source': str(source), 'source_sha256': digest(raw), 'output_sha256': digest(changed),
        'part_indices': part_indices, 'base_color_linear_rgb': rgb, 'roughness': roughness,
        'geometry_exact': True, 'scope': 'Reviewed hardware color-prior A/B experiment; not automated recovery', 'accepted': False}
    write_json(output.with_suffix('.json'), report)
    return report


def standard_control(source, output, groups):
    """Unchanged-shape glTF transmission control; not effective-group AR optics."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh output directory')
    raw = source.read_bytes()
    _, document, binary = _read_bytes(raw)
    mesh = load_glb_bytes(raw)
    selected = [i for group in groups for i in group]
    if len(selected) != len(set(selected)) or any(type(i) is not int or not 0 <= i < len(mesh.parts) for i in selected):
        raise ValueError('Invalid source part indices')
    material = len(document.setdefault('materials', []))
    document['materials'].append({'name': 'QA_neutral_transmission_control', 'doubleSided': False,
        'pbrMetallicRoughness': {'baseColorFactor': [1, 1, 1, 1], 'metallicFactor': 0, 'roughnessFactor': .08},
        'extensions': {'KHR_materials_transmission': {'transmissionFactor': 1.0},
                       'KHR_materials_ior': {'ior': 1.5}},
        'extras': {'purpose': 'standard_glTF_diagnostic_only_not_effective_group_optics'}})
    document['extensionsUsed'] = sorted(set(document.get('extensionsUsed', [])) | {'KHR_materials_transmission', 'KHR_materials_ior'})
    clones = {}
    for index in selected:
        part = mesh.parts[index]
        node = part['node_index']
        if node not in clones:
            clones[node] = len(document['meshes'])
            document['meshes'].append(deepcopy(document['meshes'][part['mesh_index']]))
            document['nodes'][node]['mesh'] = clones[node]
        primitive = document['meshes'][clones[node]]['primitives'][part['primitive_index']]
        primitive['material'] = material
        # Provider vertex color is also photographic appearance, not transmission.
        for attribute in list(primitive['attributes']):
            if attribute.startswith('COLOR_'):
                primitive['attributes'].pop(attribute)
    changed = _pack_glb(document, binary)
    check = load_glb_bytes(changed)
    if not np.array_equal(check.vertices, mesh.vertices) or not np.array_equal(check.faces, mesh.faces):
        raise ValueError('Material control changed geometry')
    output.mkdir(parents=True, exist_ok=True)
    candidate = output / 'standard-neutral.glb'
    candidate.write_bytes(changed)
    report = {'source': str(source), 'source_sha256': digest(raw), 'candidate': str(candidate),
        'output_sha256': digest(changed), 'optical_parts': groups, 'geometry_exact': True,
        'accepted': False, 'scope': 'Standard glTF transmission diagnostic; closed sheets may render differently from effective AR groups'}
    write_json(output / 'report.json', report)
    return report


def canonicalize(raw, *, yaw_degrees=-90., width_m=.145):
    if not np.isfinite(yaw_degrees) or not .06 <= width_m <= .25:
        raise ValueError('Invalid diagnostic orientation or width')
    _, original, binary = _read_bytes(raw)
    if any(p.get('targets') or p.get('extensions') for m in original['meshes'] for p in m['primitives']):
        raise ValueError('Unsupported morph/compressed primitive')
    mesh = load_glb_bytes(raw)
    matrices = _source_matrices(original)
    angle = np.radians(yaw_degrees)
    rotation = np.array([[np.cos(angle), 0., np.sin(angle)], [0., 1., 0.], [-np.sin(angle), 0., np.cos(angle)]])
    oriented = mesh.vertices @ rotation.T
    extent = np.ptp(oriented, axis=0)
    if extent[0] <= 0:
        raise ValueError('No lateral extent')
    factor = width_m / extent[0]
    origin, placement = bridge_origin(oriented * factor)
    placed = oriented * factor - origin
    document = deepcopy(original)
    output_binary = bytearray(binary)

    def append(values, width, *, bounds=False):
        array = np.ascontiguousarray(values, dtype='<f4')
        if not np.isfinite(array).all():
            raise ValueError('Nonfinite transformed attributes')
        output_binary.extend(b'\0' * (-len(output_binary) % 4))
        view = len(document['bufferViews'])
        document['bufferViews'].append({'buffer': 0, 'byteOffset': len(output_binary), 'byteLength': array.nbytes})
        output_binary.extend(array.tobytes())
        accessor = {'bufferView': view, 'componentType': 5126, 'count': len(array), 'type': f'VEC{width}'}
        if bounds:
            accessor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        index = len(document['accessors'])
        document['accessors'].append(accessor)
        return index

    clones = {}
    for part in mesh.parts:
        node = part['node_index']
        if node not in clones:
            clone = len(document['meshes'])
            document['meshes'].append(deepcopy(original['meshes'][part['mesh_index']]))
            document['nodes'][node]['mesh'] = clone
            clones[node] = clone
        primitive = document['meshes'][clones[node]]['primitives'][part['primitive_index']]
        attrs = primitive['attributes']
        start, count = part['vertex_start'], part['vertex_count']
        attrs['POSITION'] = append(placed[start:start+count], 3, bounds=True)
        linear = matrices[node][:3, :3]
        if np.linalg.det(linear) <= 0:
            raise ValueError('Mirrored/singular input transforms require explicit winding handling')
        if 'NORMAL' in attrs:
            normals = _float_accessor(original, binary, attrs['NORMAL'], 3) @ np.linalg.inv(linear) @ rotation.T
            length = np.linalg.norm(normals, axis=1)
            if np.any(length <= 0):
                raise ValueError('Zero source normal')
            attrs['NORMAL'] = append(normals / length[:, None], 3)
        if 'TANGENT' in attrs:
            tangent = _float_accessor(original, binary, attrs['TANGENT'], 4)
            direction = tangent[:, :3] @ linear.T @ rotation.T
            length = np.linalg.norm(direction, axis=1)
            if np.any(length <= 0):
                raise ValueError('Zero source tangent')
            attrs['TANGENT'] = append(np.column_stack((direction / length[:, None], tangent[:, 3])), 4)
    for node in matrices:
        for name in ('matrix', 'translation', 'rotation', 'scale'):
            document['nodes'][node].pop(name, None)
    document['buffers'][0]['byteLength'] = len(output_binary)
    document.setdefault('extras', {})['partOpticsProbe'] = {'yaw_degrees': yaw_degrees, 'display_width_m': width_m,
        'width_is_product_measurement': False, 'identity_status': 'experimental_annotation', 'source_sha256': digest(raw)}
    normalized = _pack_glb(document, bytes(output_binary))
    check = load_glb_bytes(normalized)
    if len(check.parts) != len(mesh.parts) or not np.array_equal(check.faces, mesh.faces):
        raise ValueError('Canonicalization changed primitive order/topology')
    maximum_error = float(np.max(np.abs(check.vertices - placed)))
    if maximum_error > 1e-7:
        raise ValueError('Unexpected geometry quantization error')
    for before, after in zip(mesh.parts, check.parts):
        old = original['meshes'][before['mesh_index']]['primitives'][before['primitive_index']]
        new = document['meshes'][after['mesh_index']]['primitives'][after['primitive_index']]
        untouched = set(old['attributes']) - {'POSITION', 'NORMAL', 'TANGENT'}
        if any(old['attributes'][key] != new['attributes'][key] for key in untouched) or old.get('material') != new.get('material'):
            raise ValueError('Canonicalization changed appearance binding')
    receipt = {'source_sha256': digest(raw), 'output_sha256': digest(normalized), 'yaw_degrees': yaw_degrees,
        'display_width_m': width_m, 'width_is_product_measurement': False, 'uniform_scale': float(factor),
        'translation_after_scale_m': (-origin).tolist(), 'placement': placement, 'triangles': len(mesh.faces),
        'topology_exact': True, 'source_uv_and_material_bindings_unchanged': True,
        'maximum_position_quantization_error_m': maximum_error, 'accepted': False}
    return normalized, receipt


def run(source, output, groups, *, yaw_degrees=-90., width_m=.145):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh output directory')
    raw = source.read_bytes()
    normalized, transform = canonicalize(raw, yaw_degrees=yaw_degrees, width_m=width_m)
    mesh = load_glb_bytes(normalized)
    specifications = []
    seen = set()
    for number, indices in enumerate(groups):
        if not indices:
            raise ValueError('Empty optical group')
        members = []
        for index in indices:
            if type(index) is not int or index < 0 or index >= len(mesh.parts) or index in seen:
                raise ValueError('Invalid or repeated optical part')
            seen.add(index)
            part = mesh.parts[index]
            members.append({'id': f'part-{index}', 'source_part_index': index,
                'source_binding': {k: part[k] for k in ('node_index', 'mesh_index', 'primitive_index')}})
        specifications.append({'group_id': f'optical-{number}', 'members': members})
    output.mkdir(parents=True, exist_ok=True)
    normalized_path = output / 'canonical.glb'
    normalized_path.write_bytes(normalized)
    write_json(output / 'canonicalization.json', transform)
    declarations = {'schema_version': 1, 'source_sha256': digest(normalized),
        'coordinate_frame': {'id': 'probe-display-frame', 'units': 'meters', 'up_axis': '+Y', 'forward_axis': '+Z',
            'provenance': {'method': 'recorded_similarity_and_bridge_placement', 'receipt': 'canonicalization.json'}},
        'provenance': {'method': 'explicit_experimental_part_annotation', 'source': str(source),
            'source_sha256': digest(raw), 'automatic_semantic_acceptance': False}, 'groups': specifications}
    write_json(output / 'declarations.json', declarations)
    preparation = run_optical_group_preparation(normalized_path, output / 'optics',
        grouping_mode='explicit_declarations', declarations=declarations)
    report = {'source': str(source), 'source_sha256': digest(raw), 'optical_parts': groups,
        'preparation_status': preparation['status'], 'reasons': preparation.get('reasons'),
        'candidate': str(output / 'optics' / preparation['model']['path']) if preparation.get('model') else None,
        'accepted': False, 'material': 'neutral_diagnostic_control_not_photo_inference'}
    write_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--groups', required=True, help='JSON lists of source primitive indices, e.g. [[0],[1]]')
    parser.add_argument('--yaw', type=float, default=-90.)
    parser.add_argument('--standard-control', action='store_true')
    args = parser.parse_args()
    if args.standard_control:
        print(json.dumps(standard_control(args.source, args.output, json.loads(args.groups))))
    else:
        print(json.dumps(run(args.source, args.output, json.loads(args.groups), yaw_degrees=args.yaw)))


if __name__ == '__main__':
    main()
