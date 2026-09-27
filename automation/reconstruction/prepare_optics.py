"""Prepare candidate optical surfaces without assigning inferred photo materials.

This is a reversible stage. It retains source part identity as a hypothesis,
measures geometric approximation, and writes a neutral diagnostic asset only
when every requested optical part has a supported prepared surface. The asset
is a binding for photo fitting and actual-runtime checks, not a finished model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .lens_appearance import DensityKeyframe, LensAppearance
from .mesh import load_glb
from .optical_asset import write_optical_candidate
from .optical_surface import prepare_front_surfaces
from .refine_photos import implementation_manifest
from .region_proposals import _lens_parts


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def run_optical_preparation(model: Path, output: Path) -> dict:
    model, output = Path(model).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Optical preparation output must be empty; retain previous attempts')
    source_hash = _sha(model)
    implementation = implementation_manifest()
    mesh = load_glb(model)
    selected, ledger = _lens_parts(mesh)
    report = {'schema_version': 1, 'status': 'no_candidate_optical_parts', 'quality_verdict': 'unmeasured', 'accepted': False,
              'source': str(model), 'source_sha256': source_hash, 'implementation': implementation,
              'source_parts': ledger, 'selected_parts': selected, 'parts': [], 'model': None,
              'semantic_identity': 'unverified', 'material_identification': 'not_attempted',
              'limitations': ['Optical source-part selection is candidate metadata/name evidence, not photographic identity.',
                  'A maximum-Z envelope is an effective front-surface hypothesis, not a complete physical lens volume.',
                  'Height coordinates and normals are derived from prepared geometry; physical optical coordinates are unverified.',
                  'The neutral diagnostic material is not fitted to photos and must not become an accepted final appearance.',
                  'Actual runtime compatibility and multiview geometric appearance require separate checks.']}
    output.mkdir(parents=True, exist_ok=True)
    replacements = []
    diagnostic = LensAppearance((DensityKeyframe(0., (0., 0., 0.)),), roughness=.05)
    for index in selected:
        print(f'Preparing optical source part {index}', flush=True)
        part = mesh.parts[index]
        faces = mesh.faces[part['face_start']:part['face_start'] + part['face_count']]
        vertices, inverse = np.unique(faces.ravel(), return_inverse=True)
        result = prepare_front_surfaces(mesh.vertices[vertices], inverse.reshape((-1, 3)))
        folder = output / f'part-{index:03d}'
        folder.mkdir()
        record = {'source_part_index': index, 'source_part': part, 'preparation': result['report'], 'surfaces': []}
        surfaces = []
        for component, item in enumerate(result.get('surfaces', [])):
            surface_id = f'part-{index:03d}-{item["id"]}'
            arrays = {key: np.asarray(item[key]) for key in ('positions', 'normals', 'uv', 'indices')}
            path = folder / f'surface-{component:03d}.npz'
            np.savez_compressed(path, **arrays)
            record['surfaces'].append({'id': surface_id, 'path': path.relative_to(output).as_posix(), 'sha256': _sha(path),
                                       'vertices': len(arrays['positions']), 'triangles': len(arrays['indices'])})
            surfaces.append({'id': surface_id, **arrays, 'appearance': diagnostic})
        _write(folder / 'report.json', record)
        report['parts'].append(record)
        if result['report']['status'] == 'prepared_candidate' and surfaces:
            replacements.append({'part_index': index, 'surfaces': surfaces})
    if selected and len(replacements) == len(selected):
        export = write_optical_candidate(model, output / 'prepared-neutral.glb', replacements,
            source_sha256=source_hash, provenance={'method': 'front_envelope_surface_preparation_v1',
                'surface_reports': [{'part': row['source_part_index'], 'path': f"part-{row['source_part_index']:03d}/report.json",
                                     'sha256': _sha(output / f"part-{row['source_part_index']:03d}/report.json")} for row in report['parts']],
                'appearance_origin': 'neutral_diagnostic_only_not_photo_inference'})
        _write(output / 'export.json', export)
        report.update(status='prepared_optical_candidate', model={'path': 'prepared-neutral.glb', 'sha256': export['output_sha256'],
                      'purpose': 'neutral geometry/coordinate binding for fitting and runtime checks'},
                      export={'path': 'export.json', 'sha256': _sha(output / 'export.json')})
    elif selected:
        report['status'] = 'unsupported_optical_preparation'
    if _sha(model) != source_hash or implementation_manifest() != implementation:
        raise ValueError('Source or implementation changed during optical preparation')
    _write(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run_optical_preparation(args.model, args.output)
    print(json.dumps({key: result[key] for key in ('status', 'quality_verdict', 'model')}))


if __name__ == '__main__':
    main()
