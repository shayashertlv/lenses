"""Export and sample the complete saved optical-group corpus under one policy.

The archive supplies explicit, unverified group memberships. This experiment
does not infer groups, call a provider, fit colors or grant AR compatibility.
Every declared source part must export and sample, or the run fails explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np

from reconstruction.lens_appearance import DensityKeyframe, LensAppearance
from reconstruction.mesh import load_glb_bytes
from reconstruction.optical_group_asset import write_optical_group_candidate
from reconstruction.optical_group_observations import build_optical_group_observations
from reconstruction.photo_lens_observations import _child, _read_pinned
from reconstruction.photo_lens_stage import save_group_observations


def run(group_manifest, region_root, output):
    group_manifest, region_root, output = (Path(p).resolve() for p in (group_manifest, region_root, output))
    if (output == group_manifest.parent or output.is_relative_to(group_manifest.parent)
            or output == region_root or output.is_relative_to(region_root)):
        raise ValueError('Keep output separate from source evidence')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty output directory')
    pins = {}
    manifest = json.loads(_read_pinned(group_manifest, pins))
    rows = manifest['artifacts']
    if not rows or any(row['status'] != 'prepared_candidate' for row in rows):
        raise ValueError('Every declared group must be prepared; unsupported groups cannot be omitted')
    for path in sorted((Path(__file__).resolve().parents[1]/'reconstruction').glob('*.py')):
        _read_pinned(path, pins)
    _read_pinned(Path(__file__).resolve(), pins)
    appearance = LensAppearance((DensityKeyframe(0., (0., 0., 0.)),),
                                normal_reflectance_rgb=(.04, .04, .04))
    report = {
        'schema_version': 1, 'method': 'exported_effective_group_photo_corpus_v1',
        'accepted': False, 'quality_verdict': 'unmeasured', 'cases': [],
        'appearance_scope': 'one fixed neutral control descriptor; no photo color inference',
        'limitations': [
            'Memberships and source meshes are archived hypotheses, including known semantic mistakes.',
            'This exercises export and photo coordinates; actual AR import/render compatibility is measured separately.',
            'Successful export or sampling does not establish reconstruction quality or inferred lens color.',
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    for case in sorted({row['case'] for row in rows}):
        folder = _child(output, case)
        folder.mkdir()
        groups, records = [], []
        for entry in (row for row in rows if row['case'] == case):
            record_path = _child(group_manifest.parent, entry['record'])
            record = json.loads(_read_pinned(record_path, pins, entry['record_sha256']))
            array_path = _child(group_manifest.parent, entry['arrays'])
            raw = _read_pinned(array_path, pins, entry['arrays_sha256'])
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                arrays = {key: archive[key].copy() for key in archive.files}
            if (record['case'] != case or record['source_part_index'] != entry['source_part_index']
                    or record['report']['group_id'] != entry['group_id']
                    or ('group_id' in record and record['group_id'] != entry['group_id'])
                    or len(record['report']['primitives']) != 1
                    or set(arrays) != {'positions', 'indices', 'normals', 'uv'}):
                raise ValueError('Saved single-part group receipt does not match its corpus entry')
            primitive = {'id': record['report']['primitives'][0]['id'], **arrays}
            groups.append({'prepared': {'report': record['report'], 'primitives': [primitive]},
                           'appearance': appearance})
            records.append(record)
        source = Path(records[0]['source_model']).resolve()
        source_sha = records[0]['source_sha256']
        if any(Path(row['source_model']).resolve() != source or row['source_sha256'] != source_sha for row in records):
            raise ValueError('One case must bind exactly one source model')
        source_bytes = _read_pinned(source, pins, source_sha)
        source_mesh = load_glb_bytes(source_bytes)
        for record in records:
            index = record['source_part_index']
            if (type(index) is not int or not 0 <= index < len(source_mesh.parts)
                    or source_mesh.parts[index] != record['source_part']):
                raise ValueError('Archived part identity differs from the bound source model')
        region_path = _child(region_root, f'cases/{case}/attempt-001/report.json')
        _read_pinned(region_path, pins)
        case_started = time.perf_counter()
        model = folder/'neutral.glb'
        exported = write_optical_group_candidate(source, model, groups, source_sha256=source_sha,
            provenance={'method': report['method'], 'group_manifest_sha256': pins[str(group_manifest)],
                        'source_identity': 'archived_source_parts_unverified'})
        export_path = folder/'export.json'
        export_path.write_text(json.dumps(exported, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        sampled = build_optical_group_observations(model, export_path, region_path)
        for index, group in sampled['groups'].items():
            save_group_observations(folder/f'group-{index:03d}', group)
        observations_path = folder/'observations.json'
        observations_path.write_text(json.dumps(sampled['report'], indent=2, allow_nan=False)+'\n', encoding='utf-8')
        for path, digest in sampled['report']['input_sha256'].items():
            _read_pinned(Path(path), pins, digest)
        item = {'case': case, 'seconds': time.perf_counter()-case_started,
                'source_sha256': source_sha, 'groups': len(groups),
                'group_ids': sorted(row['report']['group_id'] for row in records),
                'observations': sum(len(group['observations']) for group in sampled['groups'].values()),
                'model': str(model.relative_to(output)).replace('\\', '/'),
                'model_sha256': exported['output_sha256'],
                'export_report': str(export_path.relative_to(output)).replace('\\', '/'),
                'export_report_sha256': hashlib.sha256(export_path.read_bytes()).hexdigest(),
                'observation_report': str(observations_path.relative_to(output)).replace('\\', '/'),
                'observation_report_sha256': hashlib.sha256(observations_path.read_bytes()).hexdigest(),
                'accepted': False, 'quality_verdict': 'unmeasured'}
        report['cases'].append(item)
        print(json.dumps(item), flush=True)
    for path, digest in tuple(pins.items()):
        _read_pinned(Path(path), pins, digest)
    report.update(status='export_and_sampling_complete', seconds=time.perf_counter()-started, input_sha256=pins)
    (output/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--regions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.groups, args.regions, args.output)
