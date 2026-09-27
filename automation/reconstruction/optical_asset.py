"""Transport prepared optical sheets into a separate, reversible GLB candidate.

The caller supplies explicit source-part bindings, prepared world-space surfaces
and canonical descriptors. This module never classifies a product or fits color.
Unreplaced primitives, node transforms, textures and original binary bytes remain
intact. Replaced optical primitives are removed only from their selected-scene
instances; new baked sheet nodes live at the selected scene root.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from .deform_glb import _read
from .lens_appearance import LensAppearance, VERTICAL_COORDINATE
from .lens_asset import EXTENSION, _pack_glb
from .mesh import _node_matrix, load_glb, load_glb_bytes


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _surface(value):
    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value['id']):
        raise ValueError('Each optical surface requires a safe stable id')
    arrays = {}
    for name, width in (('positions', 3), ('normals', 3), ('uv', 2)):
        raw = np.asarray(value[name])
        if raw.ndim != 2 or raw.shape[1] != width or not len(raw) or raw.dtype.kind not in 'fiu' or not np.isfinite(raw).all():
            raise ValueError(f'Invalid optical {name}')
        array = raw.astype('<f4')
        if not np.isfinite(array).all():
            raise ValueError('Optical geometry overflows float32')
        arrays[name] = array
    positions, normals, uv = arrays['positions'], arrays['normals'], arrays['uv']
    if len(positions) < 3 or len(normals) != len(positions) or len(uv) != len(positions):
        raise ValueError('Optical attribute counts must match')
    faces = np.asarray(value['indices'])
    if faces.ndim != 2 or faces.shape[1] != 3 or not len(faces) or faces.dtype.kind not in 'iu' or np.any(faces < 0) or np.any(faces >= len(positions)):
        raise ValueError('Invalid optical triangle indices')
    faces = faces.astype('<u4')
    if np.any(normals[:, 2] <= 0) or np.any(np.linalg.norm(normals.astype(float), axis=1) < 1e-8):
        raise ValueError('Optical normals must be finite nonzero +Z directions')
    if np.any((uv[:, 1] < 0) | (uv[:, 1] > 1)) or uv[:, 1].min() > 1e-6 or uv[:, 1].max() < 1 - 1e-6:
        raise ValueError('Optical intrinsic height must span [0,1]')
    if np.sum((positions[:, 1].astype(float) - positions[:, 1].mean()) * (uv[:, 1].astype(float) - uv[:, 1].mean())) <= 0:
        raise ValueError('Optical height coordinates do not follow authored +Y')
    triangles = positions[faces].astype(float)
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    area_floor = max(float(np.sum(np.ptp(positions.astype(float), axis=0) ** 2)) * 1e-14, np.finfo(float).tiny)
    if np.any(cross[:, 2] <= area_floor):
        raise ValueError('Quantized optical triangles must face +Z without collapse')
    return {**arrays, 'indices': faces, 'id': value['id']}


def _selected_nodes(document):
    seen = set()
    def visit(index, parent, ancestors):
        if type(index) is not int or not 0 <= index < len(document['nodes']) or index in ancestors or index in seen:
            raise ValueError('Selected scene must have unique, acyclic node instances')
        seen.add(index)
        node = document['nodes'][index]
        matrix = parent @ _node_matrix(node)
        if abs(np.linalg.det(matrix[:3, :3])) < 1e-12:
            raise ValueError('Singular source node transform')
        if node.get('skin') is not None or node.get('weights') or 'EXT_mesh_gpu_instancing' in node.get('extensions', {}):
            raise ValueError('Bake skinning, morphs and instancing before optical preparation')
        for child in node.get('children', []):
            visit(child, matrix, ancestors | {index})
    for node in document['scenes'][document.get('scene', 0)].get('nodes', []):
        visit(node, np.eye(4), set())
    return seen


def _material(appearance, surface_id):
    sample = appearance.evaluate(.5, 0.)
    return {'name': f'Canonical optical candidate {surface_id}',
            'doubleSided': True, 'alphaMode': 'BLEND',
            'pbrMetallicRoughness': {'baseColorFactor': [*np.clip(sample.reflectance_rgb + sample.transmission_rgb, 0, 1).tolist(),
                                                        float(1 - np.mean(sample.transmission_rgb))],
                'metallicFactor': float(np.mean(appearance.normal_reflectance_rgb)), 'roughnessFactor': appearance.roughness},
            'extensions': {EXTENSION: {'schema_version': 1, 'texcoord': 0, 'appearance': appearance.to_dict()}},
            'extras': {'fallbackIsApproximate': True, 'fallbackEstablishesFidelity': False}}


def write_optical_candidate(source: Path, destination: Path, replacements: list[dict], *,
                            source_sha256: str, provenance: dict) -> dict:
    """Compile supplied surfaces/descriptors without inferring their identity.

    replacements = [{part_index: int, surfaces: [{id,positions,normals,uv,
                     indices, appearance: LensAppearance|descriptor_dict}]}].
    part_index refers to load_glb(source).parts and is bound to source_sha256.
    Every requested part must supply at least one surface. Unrepresented parts
    are preserved; runtime compatibility (including any remaining legacy optics)
    must be checked in the actual renderer before selecting this candidate.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Optical candidates require a separate new destination')
    raw, document, original_binary = _read(source)
    if not isinstance(source_sha256, str) or not re.fullmatch('[a-f0-9]{64}', source_sha256) or _sha(raw) != source_sha256:
        raise ValueError('Optical source SHA-256 mismatch')
    # Relocating an unchanged relative URI silently changes its resource base.
    # Preserve only self-contained image resources; do not copy or fetch an
    # unpinned external texture as a side effect of geometry preparation.
    for image in document.get('images', []):
        if 'uri' in image and (not isinstance(image['uri'], str) or not image['uri'].startswith('data:')):
            raise ValueError('External image resources must be embedded before optical export')
    provenance = _json_copy(provenance)
    if not isinstance(provenance, dict) or not provenance.get('method'):
        raise ValueError('Explicit optical preparation provenance is required')
    selected_nodes = _selected_nodes(document)
    source_mesh = load_glb_bytes(raw)
    if not isinstance(replacements, list) or not replacements:
        raise ValueError('At least one explicit optical replacement is required')
    prepared, selected, surface_ids = [], set(), set()
    for replacement in replacements:
        part_index = replacement.get('part_index')
        if type(part_index) is not int or not 0 <= part_index < len(source_mesh.parts) or part_index in selected:
            raise ValueError('Replacement source parts must be unique valid indices')
        selected.add(part_index)
        part = source_mesh.parts[part_index]
        surfaces = replacement.get('surfaces')
        if not isinstance(surfaces, list) or not surfaces:
            raise ValueError('Do not remove a source part without a replacement surface')
        for item in surfaces:
            surface = _surface(item)
            if surface['id'] in surface_ids:
                raise ValueError('Optical surface ids must be unique across the asset')
            surface_ids.add(surface['id'])
            appearance = item.get('appearance')
            if not isinstance(appearance, LensAppearance):
                appearance = LensAppearance.from_dict(appearance)
            prepared.append((part_index, part, surface, appearance))
    # Preserve every source byte; new accessors point to appended data only.
    binary = bytearray(original_binary)
    for name in ('accessors', 'bufferViews', 'materials', 'meshes', 'nodes'):
        document.setdefault(name, [])
    # Clone the selected scene tree. Source nodes may also belong to an
    # unselected scene, whose mesh references and hierarchy must stay intact.
    node_map = {index: len(document['nodes']) + offset for offset, index in enumerate(sorted(selected_nodes))}
    original_nodes = list(document['nodes'])
    for index in sorted(selected_nodes):
        node = deepcopy(original_nodes[index])
        if 'children' in node:
            node['children'] = [node_map[child] for child in node['children']]
        document['nodes'].append(node)
    scene = document['scenes'][document.get('scene', 0)]
    scene['nodes'] = [node_map[index] for index in scene.get('nodes', [])]
    def append(array, kind, component_type):
        binary.extend(b'\0' * (-len(binary) % 4))
        view_index = len(document['bufferViews'])
        document['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes,
                                      'target': 34963 if kind == 'SCALAR' else 34962})
        binary.extend(array.tobytes())
        accessor = {'bufferView': view_index, 'componentType': component_type, 'count': array.size if kind == 'SCALAR' else len(array), 'type': kind}
        if kind == 'VEC3':
            accessor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        document['accessors'].append(accessor)
        return len(document['accessors']) - 1
    by_node = {}
    for part_index in selected:
        part = source_mesh.parts[part_index]
        by_node.setdefault(part['node_index'], set()).add(part['primitive_index'])
    removed = []
    for node_index, primitive_indices in by_node.items():
        node = document['nodes'][node_map[node_index]]
        original_index = node['mesh']
        mesh = deepcopy(document['meshes'][original_index])
        mesh['primitives'] = [primitive for index, primitive in enumerate(mesh['primitives']) if index not in primitive_indices]
        if mesh['primitives']:
            document['meshes'].append(mesh)
            node['mesh'] = len(document['meshes']) - 1
        else:
            node.pop('mesh')
        removed.append({'node_index': node_index, 'candidate_node_index': node_map[node_index], 'original_mesh_index': original_index,
                        'primitive_indices': sorted(primitive_indices)})
    bindings = []
    scene.setdefault('nodes', [])
    for part_index, part, surface, appearance in prepared:
        descriptor = appearance.to_dict()
        descriptor_sha = _sha(json.dumps(descriptor, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
        material_index = len(document['materials'])
        document['materials'].append(_material(appearance, surface['id']))
        primitive = {'attributes': {'POSITION': append(surface['positions'], 'VEC3', 5126),
                                     'NORMAL': append(surface['normals'], 'VEC3', 5126),
                                     'TEXCOORD_0': append(surface['uv'], 'VEC2', 5126)},
                     'indices': append(surface['indices'], 'SCALAR', 5125), 'material': material_index, 'mode': 4}
        mesh_index = len(document['meshes'])
        document['meshes'].append({'name': f'Prepared {surface["id"]}', 'primitives': [primitive]})
        node_index = len(document['nodes'])
        document['nodes'].append({'name': f'Prepared {surface["id"]}', 'mesh': mesh_index,
            'extras': {'partRole': 'lens', 'lensSurfaceProfile': 'front_sheet_v1', 'lensUVConvention': VERTICAL_COORDINATE,
                      'lensAppearanceSha256': descriptor_sha, 'opticalCandidateSurfaceId': surface['id'],
                      'opticalSourcePartIndex': part_index, 'opticalSourceSha256': source_sha256,
                      'semanticIdentity': 'unverified', 'materialIdentification': 'unmeasured'}})
        scene['nodes'].append(node_index)
        bindings.append({'surface_id': surface['id'], 'source_part_index': part_index,
                         'source_node_index': part['node_index'], 'source_primitive_index': part['primitive_index'],
                         'node_index': node_index, 'mesh_index': mesh_index, 'material_index': material_index,
                         'vertices': len(surface['positions']), 'triangles': len(surface['indices']),
                         'attribute_sha256': {name: _sha(surface[name].tobytes()) for name in ('positions', 'normals', 'uv', 'indices')},
                         'descriptor_sha256': descriptor_sha, 'appearance': descriptor})
    used = document.setdefault('extensionsUsed', [])
    if EXTENSION not in used:
        used.append(EXTENSION)
    document['buffers'][0]['byteLength'] = len(binary)
    document.setdefault('extras', {})['opticalPreparation'] = {
        'schema_version': 1, 'source_sha256': source_sha256, 'provenance': provenance,
        'quality_verdict': 'unmeasured', 'accepted': False,
        'surface_profile': 'front_sheet_v1', 'material_identification': 'unmeasured'}
    output = _pack_glb(document, bytes(binary))
    if _sha(source.read_bytes()) != source_sha256:
        raise ValueError('Source changed during optical compilation')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        stream.write(output)
    # Decode the actual artifact, including fully clear canonical interfaces.
    loaded = load_glb(destination)
    canonical = [p for p in loaded.parts if p.get('has_lens_appearance_extension')]
    retained_canonical = sum(bool(part.get('has_lens_appearance_extension')) for index, part in enumerate(source_mesh.parts) if index not in selected)
    if len(canonical) != len(prepared) + retained_canonical:
        raise ValueError('Exported canonical surface count changed on reload')
    return {'schema_version': 1, 'status': 'optical_candidate_exported', 'quality_verdict': 'unmeasured', 'accepted': False,
            'source_sha256': source_sha256, 'output_sha256': _sha(output), 'output': str(destination),
            'source_binary_prefix_preserved': bytes(binary[:len(original_binary)]) == original_binary,
            'replaced_parts': sorted(selected), 'removed_instance_primitives': removed, 'surfaces': bindings,
            'unreplaced_source_parts': [i for i in range(len(source_mesh.parts)) if i not in selected],
            'surface_topology_validation': 'requires_actual_runtime_validation',
            'generic_gltf_fallback': 'approximate_only', 'provenance': provenance}
