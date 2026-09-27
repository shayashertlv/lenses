"""Replay cached photos through default automatic refinement without API calls."""
import argparse
import hashlib
import json
from pathlib import Path

from reconstruction.refine_photos import PhotoInput, implementation_manifest, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached-refinement', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--initializer-report', type=Path)
    parser.add_argument('--request', type=Path, help='Optional original request whose provider-only photos should join geometry evidence')
    parser.add_argument('--resolution', type=int, default=192)
    parser.add_argument('--camera-evaluations', type=int, default=100)
    args = parser.parse_args()
    cached = json.loads(args.cached_refinement.read_bytes())
    before = implementation_manifest()
    photos = [PhotoInput(v['view_id'], Path(v['source']), v.get('view_label', v['view_id']))
              for v in cached['views']]
    if args.initializer_report or args.request:
        from reconstruction.multiview_intake import run_multiview_intake
        initializer = json.loads(args.initializer_report.read_bytes()) if args.initializer_report else {}
        if args.request:
            request=json.loads(args.request.read_bytes());initializer=dict(initializer)
            extra=[]
            for row in request.get('initializer',{}).get('provider_views',[]):
                path=Path(row['path']);path=path if path.is_absolute() else args.request.parent/path
                extra.append({**row,'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            initializer['unused']=[*initializer.get('unused',[]),*extra]
        intake = run_multiview_intake([{'id':v['view_id'],'path':v['source'],'sha256':v['source_sha256'],
                                      'view':v.get('view_label',v['view_id'])} for v in cached['views']],
            initializer,
            args.output.parent / (args.output.name + '-intake'))
        photos = [PhotoInput(p['id'], Path(p['path']), p['view']) for p in intake['photos']]
    report = run(Path(cached['source_model']), photos, args.output,
                 resolution=args.resolution, camera_evaluations=args.camera_evaluations)
    if before != implementation_manifest():
        raise ValueError('Implementation changed during articulation probe')
    summary = {'status': report['status'], 'source_sha256': report['source_sha256'],
               'automatic_binding': report.get('automatic_binding', report.get('automatic_articulation')),
               'rest_geometry_unchanged': report.get('rest_geometry_unchanged'), 'views': [],
               'shared_shape': report.get('shared_shape'), 'accepted': False}
    for view in report['views']:
        arms = {}
        for side, row in view.get('articulation_evidence', {}).items():
            retained = row.get('retained', False)
            baseline, proposal = row.get('baseline', {}), row.get('proposal', {})
            arms[side] = {'status': row['status'], 'retained': retained,
                'baseline_holdout_px': baseline.get('holdout_error_px'),
                'retained_holdout_px': (proposal if retained else baseline).get('holdout_error_px'),
                'retained_angle_degrees': proposal.get('angle_degrees', 0.) if retained else 0.}
        summary['views'].append({'view_id': view['view_id'], 'status': view['status'],
                                 'selected_front_edges': view.get('selected_front_edge_count'), 'arms': arms})
    (args.output / 'probe-summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': summary['status'], 'views': summary['views'], 'accepted': False}), flush=True)


if __name__ == '__main__':
    main()
