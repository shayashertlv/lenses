"""Bounded appearance pilot on one bridged physical-group hypothesis.

For one development design and one hypothesis from the physical-group corpus,
this carries the original fitted cameras to the partitioned candidate with a
verified transfer, runs the unchanged region stage on that candidate, then the
unchanged grouped joint fit with an explicit optimizer budget. It records what
the composition ledger said about the same views beside the fit's own coverage.
Nothing here accepts a material; a converged fit is not appearance success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconstruction.candidate_cameras import transfer_cameras_to_partition
from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy
from reconstruction.joint_photo_lens_stage import run_joint_photo_lens_stage
from reconstruction.region_proposals import run_region_stage


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run(corpus, manifest, refinements, case, hypothesis, output, *, weights, weights_sha256, maximum_runs):
    corpus, manifest, refinements, output = (Path(p).resolve() for p in (corpus, manifest, refinements, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh empty pilot output directory')
    started = time.perf_counter()
    corpus_report = json.loads((corpus/'report.json').read_bytes())
    row = next(c for c in corpus_report['cases'] if c['id'] == case)
    summary = next(h for h in row['hypotheses'] if h['index'] == hypothesis)
    if summary['bridge']['status'] != 'bridge_executed':
        raise ValueError('Chosen hypothesis has no executed bridge; pick the hypothesis that executed its declaration')
    bridge = corpus/case/f'hypothesis-{hypothesis:02d}'/'bridge'
    frozen = json.loads(manifest.read_bytes())
    frozen_case = next(c for c in frozen['cases'] if c['id'] == case)
    source_path = (manifest.parent/frozen_case['model']).resolve()
    source_raw = source_path.read_bytes()
    partitioned_path = bridge/'partition'/'partitioned.glb'
    partitioned_raw = partitioned_path.read_bytes()
    receipt = json.loads((bridge/'partition'/'receipt.json').read_bytes())
    refinement_path = refinements/case/'report.json'
    refinement = json.loads(refinement_path.read_bytes())
    photos = []
    for view in ('front', 'angled'):
        path = (manifest.parent/frozen_case['photos'][view]).resolve()
        photos.append({'id': view, 'view': view, 'path': str(path), 'sha256': _sha(path)})
    output.mkdir(parents=True)
    pins = {str(p): _sha(p) for p in (source_path, partitioned_path, refinement_path, corpus/'report.json', manifest)}
    transferred = transfer_cameras_to_partition(refinement, source_raw, partitioned_raw, receipt,
                                                partitioned_model_path=str(partitioned_path))
    (output/'cameras').mkdir()
    _write(output/'cameras'/'report.json', transferred)
    report = {'schema_version': 1, 'method': 'physical_group_appearance_pilot_v1', 'status': 'running',
              'accepted': False, 'quality_verdict': 'unmeasured', 'selected_material': None,
              'case': case, 'hypothesis': hypothesis, 'bridge': str(bridge),
              'consensus_optical_groups': summary['consensus_optical_groups'],
              'composition_table': summary['composition_table'],
              'camera_transfer': transferred['camera_transfer'], 'input_sha256': pins,
              'maximum_optimization_runs': maximum_runs,
              'limitations': ['Cameras are the original fitted hypotheses carried by verified geometric identity; they are not calibrated.',
                              'Region priors come from the partitioned candidate\'s lens-material pieces; undeclared pieces are recorded as unbound.',
                              'The joint fit uses the unchanged photo policy and families; convergence is not appearance success.',
                              'One design and one hypothesis; no generalization claim.']}
    _write(output/'report.json', report)
    from reconstruction.region_engine import OfflineSAM2RegionEngine
    engine = OfflineSAM2RegionEngine(Path(weights), expected_sha256=weights_sha256, runtime_dir=output/'engine-runtime')
    region_started = time.perf_counter()
    regions = run_region_stage(photos, output/'regions', engine=engine, model=partitioned_path, refinement_report=transferred)
    report['regions'] = {'status': regions['status'], 'seconds': time.perf_counter()-region_started,
                         'report_sha256': _sha(output/'regions'/'report.json'),
                         'photos': [{'id': p['id'], 'status': p['status'],
                                     'regions': [{'id': r['id'], 'prior_parts': r['prior'].get('part_indices'),
                                                  'hypotheses': len(r.get('hypotheses', []))} for r in p['regions']]}
                                    for p in regions['photos']]}
    _write(output/'report.json', report)
    fit_started = time.perf_counter()
    policy = JointPhotoLensFitPolicy(maximum_optimization_runs=maximum_runs)
    fit = run_joint_photo_lens_stage(bridge/'optical-preparation'/'report.json', output/'regions'/'report.json',
                                     output/'joint', policy=policy)
    report['joint_fit'] = {'status': fit.get('status'), 'fit_status': fit.get('fit_status'),
                           'seconds': time.perf_counter()-fit_started,
                           'keys': sorted(fit.keys())}
    for key in ('observations', 'coverage', 'candidates', 'ensemble', 'previews', 'preview_export_status',
                'photo_policy_passing_candidates', 'optimizer', 'unsupported', 'reasons', 'summary'):
        if key in fit:
            value = fit[key]
            report['joint_fit'][key] = value if not isinstance(value, list) or len(value) <= 40 else {'count': len(value), 'first': value[:5]}
    for name, digest in pins.items():
        if _sha(name) != digest:
            raise ValueError('An input changed during the pilot: '+name)
    report.update(status='pilot_complete', seconds=time.perf_counter()-started)
    _write(output/'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=ROOT/'data/physical-group-corpus-v1')
    parser.add_argument('--manifest', type=Path, default=ROOT/'data/refinement-corpus.json')
    parser.add_argument('--refinements', type=Path, default=ROOT/'data/refinement/normal-evidence-v1')
    parser.add_argument('--case', required=True)
    parser.add_argument('--hypothesis', type=int, required=True)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--weights-sha256', required=True)
    parser.add_argument('--maximum-runs', type=int, default=810)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = run(args.corpus, args.manifest, args.refinements, args.case, args.hypothesis, args.output,
                 weights=args.weights, weights_sha256=args.weights_sha256, maximum_runs=args.maximum_runs)
    print(json.dumps({'status': result['status'], 'regions': result.get('regions', {}).get('status'),
                      'joint': result.get('joint_fit', {}).get('status'), 'seconds': result.get('seconds')}))
    return 0 if result['status'] == 'pilot_complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
