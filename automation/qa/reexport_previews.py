"""Re-export a completed joint stage's previews with the current exporter.

The fit itself is not repeated. The prepared group arrays and the partitioned
source are re-read through the unchanged input loader against a fresh
preparation, the completed fit's representative candidates are re-exported,
and a runtime manifest is written beside them. Use this when the exporter's
asset contract changed after a long fit; the fit report's own preparation SHA
is recorded in every provenance block.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qa.preview_manifest import build_manifest
from reconstruction.group_photo_lens_inputs import verify_group_preview_geometry
from reconstruction.joint_photo_lens_stage import joint_preview_representatives
from reconstruction.optical_group_asset import read_optical_group_candidate, write_optical_group_candidate
from reconstruction.photo_lens_stage import load_optical_fit_inputs
from reconstruction.prepare_optical_groups import run_optical_group_preparation


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run(bridge: Path, joint: Path, region_report: Path, output: Path) -> dict:
    bridge, joint, region_report, output = (Path(p).resolve() for p in (bridge, joint, region_report, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh empty re-export directory')
    stage = json.loads((joint/'report.json').read_bytes())
    fit_ref = stage['fit']
    fit = json.loads((joint/fit_ref['path']).read_bytes())
    if _sha(joint/fit_ref['path']) != fit_ref['sha256']:
        raise ValueError('Completed fit differs from its stage receipt')
    old_preparation = json.loads((bridge/'optical-preparation'/'report.json').read_bytes())
    declarations = bridge/'group-declarations.json'
    output.mkdir(parents=True)
    preparation = run_optical_group_preparation(bridge/'partition'/'partitioned.glb', output/'preparation',
                                                grouping_mode='explicit_declarations', declarations=declarations)
    if preparation['status'] != 'prepared_optical_group_candidate':
        raise ValueError('Fresh preparation did not complete: '+json.dumps(preparation.get('reasons')))
    if preparation['source_sha256'] != old_preparation['source_sha256']:
        raise ValueError('Fresh preparation binds a different partitioned source')
    inputs = load_optical_fit_inputs(output/'preparation'/'report.json', region_report)
    old_groups = {g['group_id'] for g in old_preparation['groups']}
    if set(inputs['prepared_groups']) != old_groups:
        raise ValueError('Fresh preparation group inventory differs from the fitted one')
    for gid, group in inputs['prepared_groups'].items():
        old = next(g for g in old_preparation['groups'] if g['group_id'] == gid)
        if group['report']['group_sha256'] != json.loads((bridge/'optical-preparation'/old['prepared_report']['path']).read_bytes())['group_sha256']:
            raise ValueError('Prepared group arrays differ from the fitted preparation')
    previews = []
    for assignment, candidate in joint_preview_representatives(fit).items():
        families = {family for _, family in assignment}
        label = next(iter(families)) if len(families) == 1 else 'mixed-'+hashlib.sha256(json.dumps(assignment).encode()).hexdigest()[:12]
        path = output/f'preview-{label}.glb'
        provenance = {'method': 'joint_photo_family_preview_reexport_v1', 'candidate_id': candidate['candidate_id'],
                      'family_assignment': dict(assignment), 'fit_report_sha256': fit_ref['sha256'],
                      'fitted_preparation_report_sha256': _sha(bridge/'optical-preparation'/'report.json'),
                      'fresh_preparation_report_sha256': _sha(output/'preparation'/'report.json'),
                      'appearance_origin': 'joint_ensemble_display_prior_not_accepted_material'}
        replacement = [{'prepared': group, 'appearance': candidate['groups'][gid]['appearance']}
                       for gid, group in sorted(inputs['prepared_groups'].items())]
        receipt = write_optical_group_candidate(inputs['source'], path, replacement,
                                                source_sha256=preparation['source_sha256'], provenance=provenance)
        verify_group_preview_geometry(inputs['export_receipt'], receipt)
        read_optical_group_candidate(path, receipt, expected_sha256=receipt['output_sha256'])
        export_path = path.with_name(path.stem+'-export.json'); _write(export_path, receipt)
        previews.append({'family_assignment': dict(assignment), 'candidate_id': candidate['candidate_id'],
                         'status': 'diagnostic_preview_exported', 'path': path.name, 'sha256': receipt['output_sha256'],
                         'export': {'path': export_path.name, 'sha256': _sha(export_path)},
                         'demoted_legacy_optical_parts': len(receipt['demoted_legacy_optical_parts'])})
    report = {'schema_version': 1, 'method': 'joint_preview_reexport_v1', 'status': 'diagnostic_previews_available',
              'accepted': False, 'quality_verdict': 'unmeasured', 'selected_material': None,
              'joint_stage_report_sha256': _sha(joint/'report.json'), 'fit': fit_ref, 'previews': previews,
              'preparation': {'path': 'preparation/report.json', 'sha256': _sha(output/'preparation'/'report.json'),
                              'demoted_legacy_optical_parts': len(json.loads((output/'preparation'/preparation['export']['path']).read_bytes())['demoted_legacy_optical_parts'])},
              'inputs': {'observation_report': None}}
    _write(output/'report.json', report)
    manifest = build_manifest(output/'preparation'/'report.json', output/'report.json', output/'runtime-manifest.json')
    report['runtime_manifest'] = {'path': 'runtime-manifest.json', 'sha256': _sha(output/'runtime-manifest.json'),
                                  'cases': [c['id'] for c in manifest['cases']]}
    _write(output/'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge', type=Path, required=True)
    parser.add_argument('--joint', type=Path, required=True)
    parser.add_argument('--regions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    report = run(args.bridge, args.joint, args.regions, args.output)
    print(json.dumps({'status': report['status'], 'previews': [(p['path'], p['demoted_legacy_optical_parts']) for p in report['previews']],
                      'neutral_demoted': report['preparation']['demoted_legacy_optical_parts']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
