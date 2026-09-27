"""Frozen five-source representation check, not automatic semantic grouping.

Every primitive's exact-position components are divided by component-label
parity. This intentionally arbitrary, product-independent partition exercises
interleaved face subsets. Optical group membership then reproduces each old
source-part hypothesis; it does not claim that those hypotheses are correct.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from reconstruction.mesh import load_glb_bytes
from reconstruction.mesh_components import component_face_labels
from reconstruction.partition_glb import inspect_partition_source, verify_partition_glb_bytes
from reconstruction.partition_optical_groups import declarations_for_partition
from reconstruction.partition_stage import run_component_inventory, run_face_partition
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.region_proposals import _lens_parts


ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run(corpus, output):
    corpus, output = Path(corpus).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh empty corpus output directory')
    frozen = json.loads(corpus.read_bytes())
    pins = {str(corpus): sha(corpus)}
    for name in ('qa/face_partition_corpus.py', 'reconstruction/partition_glb.py',
                 'reconstruction/mesh_components.py', 'reconstruction/partition_stage.py',
                 'reconstruction/partition_optical_groups.py', 'reconstruction/region_proposals.py',
                 'reconstruction/mesh.py'):
        path = ROOT/name; pins[str(path)] = sha(path)
    output.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'status': 'running', 'accepted': False,
              'quality_verdict': 'unmeasured', 'semantic_identity': 'not_inferred',
              'policy': 'all source primitives; exact local-position components; complete label-parity face subsets; optical groups reproduce unverified source-part priors',
              'limitations': ['Component parity is a representation control, not physical group inference.',
                  'Source optical roles, including lens/pad ambiguity, are retained as unverified hypotheses.',
                  'No material is fitted, no old observation report is rebound, and no rendering-equivalence claim is made.'],
              'cases': []}
    started = time.perf_counter()
    for case in frozen['cases']:
        start = time.perf_counter(); folder = output/case['id']; folder.mkdir()
        row = {'id': case['id'], 'status': 'running', 'accepted': False}; report['cases'].append(row)
        try:
            model = (corpus.parent/case['model']).resolve()
            raw = model.read_bytes(); pins[str(model)] = hashlib.sha256(raw).hexdigest()
            source = inspect_partition_source(raw)
            inventory = run_component_inventory(model, folder/'inventory')
            declarations = {'schema_version': 1, 'source_sha256': pins[str(model)],
                            'provenance': {'method': 'fixed exact-component parity representation control; no semantic selection'},
                            'partitions': []}
            by_binding = {}; component_count = 0
            for ordinal, instance in enumerate(source['instances']):
                labels = component_face_labels(instance['positions'], instance['indices'])
                component_count += int(labels.max())+1
                pieces = [{'id': f'primitive-{ordinal}-parity-{parity}',
                           'source_face_indices': np.flatnonzero(labels % 2 == parity).tolist()}
                          for parity in (0, 1) if np.any(labels % 2 == parity)]
                declarations['partitions'].append({'source_binding': instance['source_binding'], 'pieces': pieces})
                by_binding[tuple(instance['source_binding'][k] for k in ('node_index', 'mesh_index', 'primitive_index'))] = [p['id'] for p in pieces]
            write(folder/'partition-declarations.json', declarations)
            partition = run_face_partition(model, folder/'partition', declarations=folder/'partition-declarations.json')
            candidate = folder/'partition'/'partitioned.glb'
            output_raw = candidate.read_bytes()
            receipt = json.loads((folder/'partition'/'receipt.json').read_bytes())
            verified = verify_partition_glb_bytes(raw, output_raw, receipt)
            before = load_glb_bytes(raw)
            selected, ledger = _lens_parts(before)
            groups = []
            for index in selected:
                part = before.parts[index]; binding = tuple(part[k] for k in ('node_index', 'mesh_index', 'primitive_index'))
                groups.append({'group_id': f'source-part-hypothesis-{index}', 'piece_ids': by_binding[binding]})
            frame = {'id': 'source-world', 'units': 'unitless', 'up_axis': '+Y', 'forward_axis': '+Z',
                     'provenance': {'method': 'unchanged source world coordinates; physical scale/front orientation unverified'}}
            group_declarations = declarations_for_partition(raw, output_raw, receipt, groups,
                coordinate_frame=frame, provenance={'method': 'preserve existing source-part hypotheses across arbitrary representation split',
                                                   'source_prior_ledger': ledger})
            write(folder/'group-declarations.json', group_declarations)
            preparation = run_optical_group_preparation(candidate, folder/'optical-preparation',
                grouping_mode='explicit_declarations', declarations=folder/'group-declarations.json')
            row.update(status='representation_and_preparation_checked', source=str(model), source_sha256=pins[str(model)],
                source_face_occurrences=sum(len(p['indices']) for p in source['instances']),
                source_primitives=len(source['instances']), exact_position_components=component_count,
                partitioned_primitives=receipt['output_primitives'],
                source_triangle_order_preserved=receipt['preservation']['source_triangle_order'],
                output_accessor_vertex_instances=receipt['output_accessor_vertex_instances'],
                partition_verification=verified, source_optical_hypotheses=len(selected),
                prepared_groups=len(preparation['groups']), preparation_status=preparation['status'],
                preparation_reasons=preparation['reasons'],
                group_member_count=sum(len(g['members']) for g in group_declarations['groups']),
                stage_reports={name: {'path': str(folder/name/'report.json'), 'sha256': sha(folder/name/'report.json')}
                               for name in ('inventory', 'partition', 'optical-preparation')},
                receipt_sha256=sha(folder/'partition'/'receipt.json'))
        except Exception as error:
            row.update(status='failed', error_type=type(error).__name__, error=str(error))
        row['seconds'] = time.perf_counter()-start
        write(output/'report.json', report)
        print(json.dumps({k: row[k] for k in ('id', 'status', 'seconds', 'source_face_occurrences', 'partitioned_primitives', 'preparation_status', 'error') if k in row}), flush=True)
    for path, digest in pins.items():
        if sha(path) != digest:
            raise ValueError('Source or implementation changed during corpus run: '+path)
    report.update(status='corpus_experiment_complete' if all(r['status'] != 'failed' for r in report['cases']) else 'incomplete_experiment',
                  input_and_implementation_sha256=pins, seconds=time.perf_counter()-started)
    write(output/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.corpus, args.output)
    raise SystemExit(0 if result['status'] == 'corpus_experiment_complete' else 1)
