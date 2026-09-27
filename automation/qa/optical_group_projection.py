"""Fixed saved-photo projection diagnostic for explicitly prepared optical groups.

This does not fit materials or certify semantic identity. It measures whether
source-preserving groups can supply coordinates on the retained region masks.
Input preparation is the audit manifest emitted by the optical-group experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconstruction.camera import Camera
from reconstruction.mesh import TriangleMesh, load_glb
from reconstruction.optical_group_raster import rasterize_optical_groups, sample_single_group_fields
from reconstruction.photo_lens_observations import _native


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def child(parent, value):
    path = (parent / value).resolve()
    if not path.is_relative_to(parent.resolve()):
        raise ValueError('Artifact path leaves its manifest directory')
    return path


def run(group_manifest, region_root, output):
    group_manifest, region_root, output = (Path(p).resolve() for p in (group_manifest, region_root, output))
    if output.is_relative_to(group_manifest.parent) or output.is_relative_to(region_root):
        raise ValueError('Output must be separate from source artifacts')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty diagnostic output directory')
    pins = {}
    def pin(path, expected=None):
        value = sha(path)
        if expected is not None and value != expected:
            raise ValueError(f'Input hash mismatch: {path}')
        if str(path) in pins and pins[str(path)] != value:
            raise ValueError(f'Input changed during experiment: {path}')
        pins[str(path)] = value
    pin(group_manifest)
    base = Path(__file__).resolve().parents[1]
    for filename in ('mesh.py', 'camera.py', 'raster.py', 'optical_group_raster.py', 'photo_lens_observations.py'):
        pin(base / 'reconstruction' / filename)
    pin(Path(__file__).resolve())
    manifest = json.loads(group_manifest.read_text(encoding='utf-8'))
    rows = manifest['artifacts']
    if not rows or any(row['status'] != 'prepared_candidate' for row in rows):
        raise ValueError('All declared groups must be prepared; do not omit unsupported groups')
    report = {'schema_version': 1, 'method': 'source_group_saved_photo_projection_v1',
              'accepted': False, 'quality_verdict': 'unmeasured', 'cases': [],
              'limitations': ['Source-part groups and saved masks/cameras are unverified hypotheses.',
                  'This measures coordinate coverage, not appearance, semantic accuracy or GPU compatibility.',
                  'Normal/height fields are source-preserving hypotheses, not recovered optical coordinates.',
                  'Same-group coplanar attribute agreement remains unverified.',
                  'All projections use the saved working grids and nearest mapping to native photo centers.',
                  'The fixed five-pixel mask erosion is unchanged; it cannot establish absence of rim or rear-content contamination.']}
    output.mkdir(parents=True, exist_ok=True)
    for case in sorted({row['case'] for row in rows}):
        records = []
        for entry in (r for r in rows if r['case'] == case):
            path = child(group_manifest.parent, entry['record']); pin(path, entry['record_sha256'])
            record = json.loads(path.read_text(encoding='utf-8'))
            if record['case'] != case or record['source_part_index'] != entry['source_part_index']:
                raise ValueError('Group membership record differs from manifest')
            arrays = child(group_manifest.parent, entry['arrays']); pin(arrays, entry['arrays_sha256'])
            with np.load(arrays, allow_pickle=False) as archive:
                records.append((record, {key: archive[key].copy() for key in archive.files}))
        source = Path(records[0][0]['source_model']).resolve()
        pin(source, records[0][0]['source_sha256'])
        mesh = load_glb(source)
        uv = np.full((len(mesh.vertices), 2), np.nan); normals = np.full_like(mesh.vertices, np.nan)
        face_groups = np.full(len(mesh.faces), -1, dtype=int)
        for record, arrays in records:
            pin(source, record['source_sha256'])
            part_id = record['source_part_index']; part = mesh.parts[part_id]
            if part != record['source_part'] or (face_groups[part['face_start']:part['face_start']+part['face_count']] >= 0).any():
                raise ValueError('Source part changed or appears in more than one group')
            v0, n, f0, nf = (part[k] for k in ('vertex_start', 'vertex_count', 'face_start', 'face_count'))
            if not np.array_equal(arrays['positions'], mesh.vertices[v0:v0+n]) or not np.array_equal(arrays['indices'], mesh.faces[f0:f0+nf]-v0):
                raise ValueError('Prepared group changed source positions/connectivity')
            uv[v0:v0+n] = arrays['uv']; normals[v0:v0+n] = arrays['normals']
            face_groups[f0:f0+nf] = part_id
        region_path = child(region_root, f'cases/{case}/attempt-001/report.json'); pin(region_path)
        regions = json.loads(region_path.read_text(encoding='utf-8'))
        if regions['candidate_sha256'] != sha(source):
            raise ValueError('Saved regions belong to another candidate')
        result = {'case': case, 'source_sha256': sha(source), 'parts': [int(i) for i in np.unique(face_groups) if i >= 0], 'photos': []}
        report['cases'].append(result)
        for photo in regions['photos']:
            pin(Path(photo['source']).resolve(), photo['source_sha256'])
            projection = photo['candidate_projection']
            if projection['status'] != 'candidate_conditioned_projection' or projection['candidate_sha256'] != sha(source):
                raise ValueError('Missing or inconsistent fixed candidate camera')
            size = projection['working_size']; norm = projection['normalization']
            if len(size) != 2 or any(type(n) is not int or not 2 <= n <= 768 for n in size):
                raise ValueError('Unsupported working grid')
            center, extent = np.asarray(norm['center'], float), norm['extent']
            if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
                raise ValueError('Invalid original normalization')
            normalized = TriangleMesh((mesh.vertices-center)/extent, mesh.faces, mesh.parts)
            camera = Camera(**projection['camera']); shape = (size[1], size[0]); started = time.perf_counter()
            events = rasterize_optical_groups(normalized, face_groups, camera, shape)
            fields = sample_single_group_fields(normalized, uv, normals, events, camera)
            elapsed = time.perf_counter()-started
            native_shape = tuple(photo['image_size'][::-1])
            native = {key: _native(value, native_shape) for key, value in fields.items() if isinstance(value, np.ndarray)}
            item = {'photo': photo['id'], 'camera': projection['camera'], 'working_size': size,
                    'normalization': norm, 'elapsed_seconds': elapsed, 'events': events.report, 'hypotheses': []}
            result['photos'].append(item)
            for region in photo['regions']:
                if region['kind'] != 'candidate_optical_region':
                    continue
                folder = child(region_path.parent, f"{photo['id']}/{region['directory']}")
                for hypothesis in region['hypotheses']:
                    entry = hypothesis['intersection']; path = child(folder, entry['path']); pin(path, entry['sha256'])
                    with Image.open(path) as image:
                        mask = np.asarray(image.convert('L')) > 0
                    if mask.shape != native_shape or int(mask.sum()) != entry['pixels']:
                        raise ValueError('Mask grid/count mismatch')
                    interior = ndimage.binary_erosion(mask, structure=np.ones((5, 5), bool), border_value=0)
                    for part in region['prior']['part_indices']:
                        if part not in result['parts']:
                            raise ValueError('Region contains an undeclared group')
                        supported = interior & (native['group'] == part)
                        item['hypotheses'].append({'region_id': region['id'], 'hypothesis': hypothesis['index'],
                            'source_part_index': part, 'interior_pixels': int(interior.sum()),
                            'coordinate_pixels': int(supported.sum()), 'stacked_pixels': int((interior & native['stacked']).sum()),
                            'ambiguous_pixels': int((interior & native['ambiguous']).sum()),
                            'invalid_attribute_pixels': int((interior & native['invalid_attributes']).sum()),
                            'rear_proxy_pixels': int((supported & (native['rear_weight'] > 0)).sum())})
            print(case, photo['id'], f'{elapsed:.3f}s', flush=True)
    if any(sha(path) != digest for path, digest in pins.items()):
        raise ValueError('Inputs changed during experiment')
    report['input_sha256'] = pins
    report['status'] = 'diagnostic_complete'
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--regions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.groups, args.regions, args.output)
