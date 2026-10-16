"""Replay two or three ordered owner-edit proposals on disposable copies.

Run inside Blender: ``--python this.py -- --source COPY.blend --snapshot
SOURCE.json --operations OPTIONS.json --out FRESH_DIRECTORY``. OPTIONS contains
``variants`` with distinct ``label`` and ``operations`` fields. Optional
``seating_inputs``, ``tracked_edges`` (object ID to vertex-index pairs), and
``local_wall_faces`` (label, object_id and polygon_ids rows) add diagnostics.

This is a deterministic native tool proof, not owner confirmation or a paid
edit run. The complete snapshot must match the opened source. Existing plan,
native scope, solid and strict export checks remain active. An unsupported local
wall measurement is reported as unsupported, never converted to a certificate.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from glasses3d.g3d import model_map_bpy as MAP, owner_mesh_bpy as APPLY
from glasses3d.g3d import owner_mesh as M, owner_surface as S, owner_markings as MARK
from glasses3d.g3d import seat_bpy
from glasses3d.g3d.export_glb import export_glb
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.tests.proofs.assembly_m7_metrics import edge_rows


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(folder, label):
    path = folder/'work/model_maps'/label/'snapshot.json'
    result = MAP.snapshot({'path': str(path)}, folder)
    if result['status'] != 'ok':
        raise ValueError(result)
    return json.loads(path.read_text()), path


def exported(folder, name):
    path = folder/(name+'.glb')
    receipt = export_glb(path)
    receipt['validate_spec'] = validate_spec(path)
    if not receipt['validate_spec']['ok']:
        raise ValueError(receipt['validate_spec'])
    (folder/(name+'-export.json')).write_text(json.dumps(receipt, indent=2))
    return {'path': str(path), 'sha256': sha(path)}


def main(args):
    source, expected_path, options_path, out = (
        p.resolve() for p in (args.source, args.snapshot, args.operations, args.out))
    if source.suffix != '.blend' or any(p.lower() in ('deliveries', 'checkpoints') for p in source.parts):
        raise ValueError('source must be a disposable .blend copy')
    inputs = {str(p): sha(p) for p in (source, expected_path, options_path)}
    expected = json.loads(expected_path.read_text())
    options = json.loads(options_path.read_text())
    variants = options['variants']
    if len(variants) not in (2, 3):
        raise ValueError('two or three proposals are required')
    labels = [row['label'] for row in variants]
    if len(set(labels)) != len(labels) or any(not label or not label.replace('_', '').replace('-', '').isalnum() for label in labels):
        raise ValueError('distinct simple labels are required')
    if (out.exists() or any(part.lower() in ('deliveries', 'checkpoints') for part in out.parts)
            or any(out == p or out in p.parents for p in (source, expected_path, options_path))):
        raise ValueError('choose a fresh output directory outside all inputs')
    signatures, common_support = set(), None
    for row in variants:
        ops = MARK.seal(expected, copy.deepcopy(row['operations']))
        support = [{k: op.get(k) for k in ('id', 'kind', 'target_ids', 'scope')} for op in ops]
        if common_support is not None and support != common_support:
            raise ValueError('all amounts must use the same ordered operations and support')
        common_support = support
        plan = M.plan(expected, ops)
        if 'target' not in plan:
            raise ValueError('this proof requires the ordered surface-operation path')
        signature = S.signature(plan['target'])
        if signature in signatures:
            raise ValueError('proposal states must be distinct')
        signatures.add(signature)
    out.mkdir(parents=True)
    report = {'api_calls': 0, 'cost_usd': 0, 'owner_confirmation': False,
              'scripted_by': 'Codex, not Astra', 'input_sha256': inputs,
              'minimum_thickness_scope': 'Only the existing verified construction or local-wall check certifies a minimum; unsupported is not a pass.',
              'variants': []}
    bpy.ops.wm.open_mainfile(filepath=str(source))
    before, _ = capture(out, 'baseline')
    if M.digest(before) != M.digest(expected):
        raise ValueError('the complete source snapshot does not match the opened copy')
    report['before_glb'] = exported(out, 'before')
    if options.get('seating_inputs'):
        report['seating_inputs'] = options['seating_inputs']
        (out/'seating-before.json').write_text(json.dumps(seat_bpy.check_lens_seating(**options['seating_inputs']), indent=2))
    for row in variants:
        bpy.ops.wm.open_mainfile(filepath=str(source))
        folder = out/row['label']; folder.mkdir()
        baseline, path = capture(folder, 'baseline')
        if M.digest(baseline) != M.digest(expected):
            raise ValueError('source snapshot changed between amounts')
        ops = MARK.seal(baseline, copy.deepcopy(row['operations']))
        plan = M.plan(baseline, ops)
        applied = APPLY.apply({'snapshot_path': str(path), 'snapshot_sha256': M.digest(baseline), 'operations': ops}, folder)
        if applied['status'] != 'ok':
            raise ValueError(applied)
        after, _ = capture(folder, 'final')
        checks = S.verify(baseline, after, ops)
        if not checks['ok']:
            raise ValueError(checks)
        prior, current = M.objects(baseline), M.objects(after)
        measured, walls = {}, {}
        for oid, edges in options.get('tracked_edges', {}).items():
            a, b = edge_rows(prior[oid]), edge_rows(current[oid])
            measured[oid] = [{'edge': edge, 'before': a[tuple(edge)], 'after': b[tuple(edge)]} for edge in edges]
        for wall in options.get('local_wall_faces', []):
            oid, faces = wall['object_id'], wall['polygon_ids']
            try:
                walls[wall['label']] = {'status': 'verified', **S.preserve_minimum_thickness(prior[oid], current[oid], faces)}
            except M.OwnerMeshError as exc:
                if exc.code != 'E_OWNER_GRAPH':
                    raise
                walls[wall['label']] = {'status': 'unsupported', 'code': exc.code, 'reason': str(exc)}
        if options.get('seating_inputs'):
            seating = seat_bpy.check_lens_seating(**options['seating_inputs'])
            (folder/'seating.json').write_text(json.dumps(seating, indent=2))
            if not seating['ok']:
                raise ValueError('proposal failed the requested seating measurement')
        glb = exported(folder, 'model')
        bpy.ops.wm.save_as_mainfile(filepath=str(folder/'model-copy.blend'))
        report['variants'].append({'label': row['label'], 'operations': ops, 'plan_reports': plan['operations'],
                                  'actual_scope_check': checks, 'tracked_edges': measured,
                                  'local_wall_measurement': walls, 'glb': glb})
        (out/'proof.json').write_text(json.dumps(report, indent=2))
        print('ORDERED_COPY_PASS', row['label'], flush=True)
    if inputs != {str(p): sha(p) for p in (source, expected_path, options_path)}:
        raise ValueError('an input changed')
    report['sources_unchanged'] = True
    (out/'proof.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'snapshot', 'operations', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    main(parser.parse_args(sys.argv[sys.argv.index('--')+1:]))
