"""Re-render an immutable candidate set after an AR renderer change, then review.

No geometry, optical fitting, candidate parameters or old evidence is changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from reconstruction.ar_material_search import compare_rendered_candidates, select_ar_material
from reconstruction.semantic_appearance_stage import render_material_cards
from reconstruction.semantic_transport import GeminiSemanticClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--api-key-env', required=True)
    parser.add_argument('--model', default='gemini-3.8-flash')
    parser.add_argument('--reuse-cards', action='store_true', help='Reuse pinned cards only if renderer source bytes are unchanged')
    parser.add_argument('--maximum-api-calls', type=int, default=2)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    read = lambda name: json.loads((source / name).read_bytes())
    candidates, semantics, anchors = read('candidates.json'), read('evidence/semantics.json'), read('evidence/anchors.json')
    manifest = source / 'runtime-manifest.json'
    if args.reuse_cards:
        render_folder = source / 'renders'
        rendered = json.loads((render_folder / 'report.json').read_bytes())
        ar = Path(__file__).resolve().parents[2] / 'ar'
        if rendered.get('status') != 'passed' or not rendered.get('source_snapshot_stable'):
            raise ValueError('Cannot reuse an unsuccessful or changing renderer run')
        for relative, digest in rendered['implementation'].items():
            if hashlib.sha256((ar / relative).read_bytes()).hexdigest() != digest:
                raise ValueError('Renderer changed; rerender cards before review')
    else:
        render_folder = output / 'renders'
        rendered = render_material_cards(manifest, render_folder)
    cards = [{'candidate_id': r['id'], 'path': str(render_folder / r['card']['path']),
              'sha256': r['card']['sha256'], 'model_sha256': r['model_sha256']} for r in rendered['cases']]
    photos = [{'id': p['photo_id'], 'path': p['local_path'], 'sha256': p['sha256']} for p in semantics['image_manifest']]
    client = GeminiSemanticClient(api_key=os.environ[args.api_key_env], model=args.model,
                                  cache_dir=args.cache, maximum_calls=args.maximum_api_calls)
    comparison = compare_rendered_candidates(photos, cards, client=client)
    facts = read('evidence/priors.json').get('declared_facts', {})
    selection = select_ar_material(candidates, anchors, comparison, policy={'declared_facts': facts})
    for name, value in [('comparisons', comparison), ('selection', selection)]:
        (output / (name + '.json')).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    chosen = selection['selected']
    shutil.copyfile(chosen['path'], output / 'candidate-appearance.glb')
    if hashlib.sha256((output / 'candidate-appearance.glb').read_bytes()).hexdigest() != chosen['sha256']:
        raise ValueError('Selected candidate bytes changed')
    report = {'source': str(source), 'status': selection['status'], 'selection': selection,
              'candidate': {'path': 'candidate-appearance.glb', 'sha256': chosen['sha256']},
              'runtime_report': str(render_folder / 'report.json'), 'accepted': False, 'quality_verdict': 'unmeasured'}
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'status': selection['status'], 'origin': chosen['origin'],
                      'candidate_id': chosen['candidate_id'], 'basis': selection['selection_basis']}))


if __name__ == '__main__':
    main()
