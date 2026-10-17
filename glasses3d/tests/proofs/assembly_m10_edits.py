"""Local M10 proof on a selected delivery copy, with data-supplied diagnoses/operations.

Prepare shows a labelled map, preview creates checked native photo-first options,
and apply executes the chosen exact option at $0. Separate phases let the desktop
caller inspect images before marking them delivered. No model transport is used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

for _key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'

from glasses3d.agent import edits, edit_capabilities, procs
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.tools import model_map, owner_preview, owner_edit, free_commit_bytes


def write(path, value):
    procs.write_json_atomic(path, value)


def protected(source):
    paths = [source/'job.json', source/'deliverable/model.glb']
    for name in ('images', 'reference_images', 'inputs', 'checkpoints', 'deliveries', 'cameras'):
        paths += [p for p in (source/name).rglob('*') if p.is_file() and p.name != '.env']
    return {str(p): edits.sha(p) for p in paths}


def coverage(root, plan):
    words = plan['request_text']
    mapping = {'schema': edit_capabilities.MAPPING_SCHEMA, 'request_text': words,
               'items': [{'id': 'item-1', 'label': plan['label'], 'start': 0, 'end': len(words), 'text': words,
                          'operation_kinds': sorted({op['kind'] for op in plan['operations']}),
                          'diagnosis': plan['diagnosis']}]}
    report = edit_capabilities.check(words, mapping)
    edit_capabilities.install(root, report)


def prepare(plan, out):
    source = Path(plan['source']).resolve()
    before = protected(source)
    root = edits.create_understand(source, plan['request_text'], delivery_id=plan['delivery_id'], destination=out/'edit')
    coverage(root, plan)
    scene = JobScene(root)
    scene.scripted = True
    try:
        scene.start()
        ops = plan['operations']
        focus = list(dict.fromkeys(key for op in ops for key in op['target_ids']))
        result = model_map.capture(scene, 'm10-map', photos=plan['refs'], focus=focus,
                                   kind='parts', support_scopes=[op['scope'] for op in ops],
                                   label_descriptions=plan.get('label_descriptions'))
        result['call_id'] = 'm10-map'
        write(out/'map.json', result)
        assert scene.s.fingerprint() == plan['diagnosis']['source_fingerprint']
    finally:
        scene.close()
    assert protected(source) == before, 'source changed'
    write(out/'source-hashes.json', before)
    write(out/'prepared.json', {'root': str(root), 'api_requests': 0, 'settled_usd': 0,
                               'map_images': result['images'], 'source_unchanged': True})
    print(json.dumps({'phase': 'prepared', 'root': str(root), 'images': [r['path'] for r in result['images']]}), flush=True)


def preview(plan, out):
    root = Path(edits.read(out/'prepared.json')['root'])
    scene = JobScene(root)
    scene.scripted = True
    try:
        scene.start()
        shown = edits.read(out/'map.json')
        model_map.mark_delivered(root, shown)
        print('MAP_SEEN; starting exact private-copy previews', flush=True)
        result = owner_preview.preview(scene, 'm10-preview', shown['map_id'], shown['map_sha256'],
                                       [shown['capture_receipt']], json.dumps(plan['operations']), plan['options'], refs=plan['refs'])
        write(out/'preview.json', result)
        assert result['source_unchanged']
    finally:
        scene.close()
    print(json.dumps({'phase': 'previewed', 'images': [r['path'] for r in result['images']],
                      'workers': result['workers'], 'options': [r['id'] for r in result['options']]}), flush=True)


def apply(plan, out):
    from glasses3d.agent import owner_apply
    root = Path(edits.read(out/'prepared.json')['root'])
    # Runtime edits invalidate coverage; a fresh measured mapping is required.
    coverage(root, plan)
    scene = JobScene(root)
    scene.scripted = True
    try:
        scene.start()
        shown = edits.read(out/'map.json')
        amount = edits.read(out/'preview.json')
        owner_preview.mark_delivered(scene, amount)
        ops = plan['operations']
        item = {'number': 1, 'label': plan['label'], 'interpretation': plan['interpretation'],
                'disposition': 'executable', 'reason': plan['diagnosis']['cause'],
                'direction': plan['direction'], 'amount': plan['amount'],
                'target_ids': list(dict.fromkeys(k for op in ops for k in op['target_ids'])),
                'operation_json': json.dumps(ops)}
        result = owner_edit.understand(scene, 'm10-understood', shown['map_id'], shown['map_sha256'],
                                      [shown['capture_receipt']], [item],
                                      ['Scripted by Codex, not Astra. Tool test on copies; not owner visual acceptance.'],
                                      preview_receipt=amount['preview_receipt'])
        result['call_id'] = 'm10-understood'
        owner_edit.mark_delivered(scene, result)
        write(out/'understanding.json', result)
    finally:
        scene.close()
    result = owner_apply.apply_preview(root.parent, plan['confirmation'], plan['selected_option'],
                                      destination=out/'applied', tool_test=True)
    write(out/'applied.json', result)
    before = edits.read(out/'source-hashes.json')
    assert all(edits.sha(Path(p)) == digest for p, digest in before.items()), 'source changed'
    write(out/'proof.json', {'status': 'passed', 'api_requests': 0, 'settled_usd': 0,
                            'source_unchanged': True, 'selected_option': plan['selected_option'],
                            'exact_preview_fingerprint': result['exact_preview_fingerprint'],
                            'export': result['export'], 'checks': result['scope_check'], 'tool_test': True})
    print(json.dumps({'phase': 'applied', 'option': plan['selected_option'], 'export': result['export']['path'],
                      'review_page': result['review_page'], 'source_unchanged': True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'preview', 'apply'))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    disable_sdk_tracing()
    start = time.monotonic()
    machine = {'pid': os.getpid(), 'started_utc': procs.utcnow(), 'phase': args.phase,
               'free_commit_gib': free_commit_bytes()/2**30, 'blas_threads': 1,
               'power_throttling_off': procs.opt_out_self()}
    write(out/(args.phase+'-started.json'), machine)
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients, procs.DescendantsOptOut():
        globals()[args.phase](edits.read(args.plan), out)
    assert not network.attempts and not clients.constructions
    write(out/(args.phase+'-completed.json'), {**machine, 'seconds': time.monotonic()-start,
                                             'completed_utc': procs.utcnow(), 'api_requests': 0})


if __name__ == '__main__':
    main()
