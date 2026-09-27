"""Pin real source geometry for the isolated effective-group browser experiment.

No source GLB is written. The eight supplied groups remain identity hypotheses,
including the known Miu silicone ambiguity. All other source triangles become
opaque, constant-color controls; original frame materials are not reproduced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconstruction.mesh import load_glb


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_file(folder, name, array, dtype):
    value = np.ascontiguousarray(array, dtype=dtype)
    path = folder/(name+'.bin'); path.write_bytes(value.tobytes())
    return {'path': path.name, 'sha256': sha(path), 'dtype': value.dtype.str, 'shape': list(value.shape),
            'byte_length': value.nbytes, 'source_dtype': np.asarray(array).dtype.str}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT/'data/optical-groups-v1/manifest.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    source_manifest, output = args.manifest.resolve(), args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new or empty bundle directory')
    manifest = json.loads(source_manifest.read_text())
    if manifest.get('accepted') is not False:
        raise ValueError('This diagnostic expects explicitly unaccepted source-group evidence')
    records = []
    for artifact in manifest['artifacts']:
        record_path = source_manifest.parent/artifact['record']; arrays_path = source_manifest.parent/artifact['arrays']
        if sha(record_path) != artifact['record_sha256'] or sha(arrays_path) != artifact['arrays_sha256']:
            raise ValueError('Group artifact pin mismatch')
        record = json.loads(record_path.read_text())
        if sha(record['source_model']) != record['source_sha256']:
            raise ValueError('Original GLB pin mismatch')
        with np.load(arrays_path, allow_pickle=False) as npz:
            arrays = {k: npz[k].copy() for k in ['positions', 'normals', 'uv', 'indices']}
        records.append((artifact, record, arrays))
    cases = {}
    for artifact, record, arrays in records:
        case_id = artifact['case']
        if case_id not in cases:
            mesh = load_glb(record['source_model'])
            cases[case_id] = {'mesh': mesh, 'source': record['source_model'], 'source_sha256': record['source_sha256'], 'groups': []}
        case = cases[case_id]; mesh = case['mesh']; part = mesh.parts[artifact['source_part_index']]
        if case['source_sha256'] != record['source_sha256']:
            raise ValueError('Case groups bind different models')
        start, count = part['vertex_start'], part['vertex_count']
        faces = mesh.faces[part['face_start']:part['face_start']+part['face_count']]-start
        if not np.array_equal(arrays['positions'], mesh.vertices[start:start+count]) or not np.array_equal(arrays['indices'], faces):
            raise ValueError('Prepared group changed source position/index values')
        if not all(np.isfinite(arrays[k]).all() for k in ['positions', 'normals', 'uv']):
            raise ValueError('Nonfinite authored arrays')
        if arrays['indices'].max() >= 2**32 or arrays['indices'].min() < 0:
            raise ValueError('Indices cannot be represented exactly as uint32')
        case['groups'].append((artifact, record, arrays))
    output.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 1, 'method': 'real_source_effective_optical_group_bundle_v1', 'accepted': False,
              'source_manifest': str(source_manifest), 'source_manifest_sha256': sha(source_manifest),
              'builder_sha256': sha(__file__), 'mesh_loader_sha256': sha(ROOT/'reconstruction/mesh.py'),
              'packages': {'python': platform.python_version(), 'numpy': np.__version__}, 'cases': [],
              'limitations': ['Source primitive grouping is unverified physical identity.',
                             'Normals and common-height UVs are preserved hypotheses, not recovered optical coordinates.',
                             'Unselected source primitives use one constant opaque control color, not source appearance.',
                             'Float32 upload is measured separately; original float64 arrays and source bytes remain unchanged.',
                             'Control descriptors do not represent estimated photographic materials.']}
    for case_id, case in cases.items():
        folder = output/case_id; folder.mkdir()
        mesh = case['mesh']; selected = set(); groups = []
        for index, (artifact, record, arrays) in enumerate(case['groups']):
            selected.add(artifact['source_part_index'])
            files = {key: array_file(folder, f'group-{index}-{key}', value, '<u4' if key == 'indices' else '<f8')
                     for key, value in arrays.items()}
            quantization = {key: {'maximum_absolute_error': float(np.max(np.abs(value.astype(np.float32).astype(float)-value))),
                                   'nonfinite_after_upload': int(np.count_nonzero(~np.isfinite(value.astype(np.float32))))}
                            for key, value in arrays.items() if key != 'indices'}
            groups.append({'id': artifact['group_id'], 'source_part_index': artifact['source_part_index'],
                           'source_binding': record['source_part'], 'record_sha256': artifact['record_sha256'],
                           'npz_sha256': artifact['arrays_sha256'], 'identity': record['report']['identity'],
                           'normal_policy': record['report']['primitives'][0]['normal_policy'],
                           'topology': record['report']['topology_diagnostics'],
                           'arrays': files, 'float32_upload': quantization, 'indices_exact_after_upload': True})
        opaque_faces = np.concatenate([mesh.faces[p['face_start']:p['face_start']+p['face_count']]
                                       for i, p in enumerate(mesh.parts) if i not in selected])
        opaque = {'positions': array_file(folder, 'opaque-positions', mesh.vertices, '<f8'),
                  'indices': array_file(folder, 'opaque-indices', opaque_faces, '<u4'),
                  'source_part_indices': [i for i in range(len(mesh.parts)) if i not in selected],
                  'float32_position_maximum_absolute_error': float(np.max(np.abs(mesh.vertices.astype(np.float32).astype(float)-mesh.vertices)))}
        low, high = mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)
        if sha(case['source']) != case['source_sha256']:
            raise ValueError('Original model changed during bundle construction')
        result['cases'].append({'id': case_id, 'source_path': case['source'], 'source_sha256': case['source_sha256'],
                                'source_geometry_unchanged': True, 'directory': case_id, 'groups': groups, 'opaque': opaque,
                                'normalization': {'center': ((low+high)/2).tolist(), 'maximum_extent': float(np.max(high-low)),
                                                  'display_extent': 2.8},
                                'source_triangle_count': len(mesh.faces), 'opaque_triangle_count': len(opaque_faces),
                                'optical_triangle_count': sum(len(a['indices']) for _, _, a in case['groups'])})
    (output/'manifest.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf8')
    print(json.dumps({'bundle': str(output/'manifest.json'), 'cases': len(result['cases']), 'groups': len(records),
                      'manifest_sha256': sha(output/'manifest.json')}))


if __name__ == '__main__':
    main()
