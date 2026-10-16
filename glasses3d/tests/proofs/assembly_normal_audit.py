"""Read-only audit of saved-copy normal intents using the current native codec.

Run in Blender with ``--python this.py -- --out REPORT.json PATH [PATH ...]``.
Each PATH is a saved .blend copy or a proof directory. Directories are searched
recursively; no source scene is saved. Hashes establish that every input stayed
unchanged. This observes the normal witness, not owner approval or spec validity.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from glasses3d.g3d import owner_normals_bpy as NORMALS


def audit(path):
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    bpy.ops.wm.open_mainfile(filepath=str(path))
    objects = []
    geometry = []
    for obj in sorted(bpy.context.scene.objects, key=lambda obj: obj.name):
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        geometry.append([obj.name, [v.co[:] for v in mesh.vertices],
                         [list(p.vertices) for p in mesh.polygons]])
        witness = NORMALS.capture(mesh)
        if witness is None:
            continue
        intended = np.asarray(witness.pop('intent_normals'), dtype=float)
        decoded = np.asarray([n.vector[:] for n in mesh.corner_normals], dtype=float)
        intended /= np.linalg.norm(intended, axis=1)[:, None]
        decoded /= np.linalg.norm(decoded, axis=1)[:, None]
        angles = np.degrees(np.arctan2(np.linalg.norm(np.cross(intended, decoded), axis=1),
                                      (intended * decoded).sum(axis=1)))
        objects.append({'name': obj.name, 'loops': len(angles), **witness,
                        'max_angular_error_degrees': float(angles.max()) if angles.size else 0.,
                        'loops_over_0p1_degree': int(sum(angles > .1)),
                        'loops_over_1_degree': int(sum(angles > 1))})
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, 'audit changed an input copy'
    return {'source_copy': str(path), 'sha256': sha, 'source_unchanged': True,
            'geometry_sha256': hashlib.sha256(json.dumps(geometry, separators=(',', ':')).encode()).hexdigest(),
            'current_codec_witnesses_valid': all(row['encoded_matches'] and row['normal_space_matches']
                                               and row['decoded_within_quantization'] for row in objects),
            'objects': objects}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('paths', type=Path, nargs='+')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if args.out.exists():
        raise ValueError('choose a fresh audit report path')
    paths = sorted({q.resolve() for path in args.paths
                    for q in (path.rglob('*.blend') if path.is_dir() else [path])})
    if not paths or any(path.suffix != '.blend' or not path.is_file() for path in paths):
        raise ValueError('every input must resolve to an existing .blend copy')
    rows = []
    for path in paths:
        row = audit(path)
        rows.append(row)
        print('NORMAL_AUDIT', path, row['current_codec_witnesses_valid'],
              [(o['name'], o['max_angular_error_degrees']) for o in row['objects']], flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'read_only': True, 'api_calls': 0, 'copies': rows}, indent=2))
