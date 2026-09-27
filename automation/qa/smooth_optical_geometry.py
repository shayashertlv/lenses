"""Reproduce a source-preserving smooth-normal alternative and matched AR cards.

The material is held fixed to isolate the normal-field change. Existing fitted
material parameters are diagnostic controls, not refits to the new incidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from reconstruction.optical_group_asset import read_optical_group_candidate, write_optical_group_candidate
from reconstruction.semantic_appearance_stage import render_material_cards
from reconstruction.smooth_optical_geometry import run_smooth_optical_preparation


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def run(preparation, candidate, receipt_path, output, *, render=False):
    preparation, candidate, receipt_path, output = map(lambda p: Path(p).resolve(),
                                                       (preparation, candidate, receipt_path, output))
    if output.exists():
        raise ValueError('Use a fresh geometry comparison output')
    baseline = json.loads(receipt_path.read_bytes())
    read_optical_group_candidate(candidate, baseline, expected_sha256=baseline['output_sha256'])
    original = json.loads(preparation.read_bytes())
    if baseline['source_sha256'] != original['source_sha256']:
        raise ValueError('Material control and geometry preparation have different sources')
    output.mkdir(parents=True)
    report = run_smooth_optical_preparation(preparation, output / 'preparation')
    appearances = {row['group_id']: row['appearance'] for row in baseline['groups']}
    groups = []
    for row in report['groups']:
        folder = output / 'preparation'
        group_report = json.loads((folder / row['prepared_report']['path']).read_bytes())
        primitives = []
        for member in row['primitives']:
            with np.load(folder / member['path'], allow_pickle=False) as archive:
                primitives.append({'id': member['id'], **{k: archive[k].copy() for k in archive.files}})
        groups.append({'prepared': {'report': group_report, 'primitives': primitives},
                       'appearance': appearances[row['group_id']]})
    alternative = output / 'smooth.glb'
    receipt = write_optical_group_candidate(output / 'preparation/source.glb', alternative, groups,
        source_sha256=original['source_sha256'], provenance={
            'method': 'matched_material_smooth_normal_diagnostic',
            'baseline_model_sha256': baseline['output_sha256'],
            'material_refit': False, 'surface': report['smooth_optical_geometry']})
    read_optical_group_candidate(alternative, receipt, expected_sha256=receipt['output_sha256'])
    _write(output / 'smooth.export.json', receipt)
    cases = [{'id': name, 'path': str(path), 'model_sha256': row['output_sha256'],
              'groups': row['groups'], 'width_mm': 145.}
             for name, path, row in [('baseline', candidate, baseline), ('smooth', alternative, receipt)]]
    _write(output / 'runtime-manifest.json', {'schema_version': 1, 'cases': cases})
    runtime = render_material_cards(output / 'runtime-manifest.json', output / 'renders') if render else None
    result = {'method': 'matched_material_smooth_normal_diagnostic', 'accepted': False,
        'material_parameters_identical': True, 'source_geometry_identical': True,
        'changed_groups': report['smooth_optical_geometry']['changed_groups'],
        'surface_reports': report['smooth_optical_geometry']['groups'],
        'baseline_sha256': baseline['output_sha256'], 'alternative_sha256': receipt['output_sha256'],
        'runtime_status': runtime['status'] if runtime else 'not_run',
        'limitations': ['Material coefficients are held fixed, not refitted to the new normal field.',
                        'Geometry support is measured against the source mesh, not independent product photos.',
                        'Optical membership, missing topology, and frame textures are unchanged.']}
    _write(output / 'report.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    result = run(args.preparation, args.candidate, args.receipt, args.output, render=args.render)
    print(json.dumps({k: result[k] for k in ('changed_groups', 'runtime_status', 'alternative_sha256')}))


if __name__ == '__main__':
    main()
