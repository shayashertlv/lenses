"""Build the AR runtime harness manifest for a joint stage's diagnostic previews.

The manifest feeds ``ar/qa/prepared-optical-groups.mjs``: one case per exported
preview plus the neutral prepared candidate as a control. Every case carries
the exact GLB hash and the export receipt's group/member/attribute bindings, so
the harness can refuse any asset whose bytes, attributes or descriptors differ.
Rendering these is a compatibility and appearance comparison, not acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_manifest(preparation_report: Path, joint_stage_report: Path, output: Path, *, width_mm=None) -> dict:
    preparation_report, joint_stage_report, output = (Path(p).resolve() for p in (preparation_report, joint_stage_report, output))
    preparation = json.loads(preparation_report.read_bytes())
    stage = json.loads(joint_stage_report.read_bytes())
    if preparation.get('status') != 'prepared_optical_group_candidate' or stage.get('status') != 'diagnostic_previews_available':
        raise ValueError('A complete grouped preparation and a joint stage with previews are required')
    folder = preparation_report.parent
    neutral = folder/preparation['model']['path']
    export = json.loads((folder/preparation['export']['path']).read_bytes())
    if _sha(neutral) != preparation['model']['sha256'] or export['output_sha256'] != preparation['model']['sha256']:
        raise ValueError('Neutral candidate bytes or export lineage changed')
    cases = [{'id': 'neutral-control', 'path': str(neutral), 'model_sha256': preparation['model']['sha256'],
              'export_receipt_sha256': preparation['export']['sha256'], 'groups': export['groups'],
              'role': 'neutral diagnostic control; no fitted material'}]
    for preview in stage['previews']:
        if preview.get('status') != 'diagnostic_preview_exported':
            continue
        glb = joint_stage_report.parent/preview['path']
        receipt_path = joint_stage_report.parent/preview['export']['path']
        if _sha(glb) != preview['sha256'] or _sha(receipt_path) != preview['export']['sha256']:
            raise ValueError('Preview bytes or receipt changed since the stage report')
        receipt = json.loads(receipt_path.read_bytes())
        if receipt['output_sha256'] != preview['sha256']:
            raise ValueError('Preview receipt does not bind the preview bytes')
        family = '-'.join(sorted(set(preview['family_assignment'].values()))).replace('_', '-')
        cases.append({'id': f'preview-{family}', 'path': str(glb), 'model_sha256': preview['sha256'],
                      'export_receipt_sha256': preview['export']['sha256'], 'groups': receipt['groups'],
                      'candidate_id': preview['candidate_id'], 'family_assignment': preview['family_assignment'],
                      'role': 'diagnostic joint-candidate preview; not an accepted material'})
    if width_mm is not None:
        for case in cases:
            case['width_mm'] = width_mm
    manifest = {'schema_version': 1, 'method': 'joint_preview_runtime_manifest_v1', 'accepted': False,
                'quality_verdict': 'unmeasured', 'preparation_report': str(preparation_report),
                'preparation_report_sha256': _sha(preparation_report), 'joint_stage_report': str(joint_stage_report),
                'joint_stage_report_sha256': _sha(joint_stage_report), 'cases': cases}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation', type=Path, required=True)
    parser.add_argument('--joint', type=Path, required=True, help='joint stage report.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width-mm', type=float)
    args = parser.parse_args(argv)
    manifest = build_manifest(args.preparation, args.joint, args.output, width_mm=args.width_mm)
    print(json.dumps({'cases': [c['id'] for c in manifest['cases']], 'output': str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
