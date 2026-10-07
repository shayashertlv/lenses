"""G1 offline proof: explicit frozen jobs -> production library -> independent proof and Blender copy.

Run phases separately, with one pool at a time. All outputs are TOOL TESTs under an explicitly supplied directory.
This fixture proves reuse on renamed inputs; it does not claim validation on an unseen product.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import struct

import numpy as np
from PIL import Image

from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host.fitmesh import part_name
from glasses3d.g3d_host.photoprint import inputs as IN, production as P
from glasses3d.g3d_host.photoprint.ar_calibrate import Calibrator
from glasses3d.g3d_host.photoprint.procs import Progress, opt_out_ecoqos
from glasses3d.g3d_host.photoprint.report import write_json

ROOT = IN.REPO / 'glasses3d'
ACCEPTED = ROOT / 'data/wp5/resume_20261007/bake'
LEVEL = ROOT / 'data/wp5/ar_calibration/calibration.json'
GROUPS = {'brow': IN.FT1119_PARTS[:2], 'temple': IN.FT1119_PARTS[2:]}


def original_basis(root):
    job = IN.load_job(IN.FT1119['cameras'])
    return C.import_cameras_json(root, IN.FT1119['cameras'],
                                 {k: v['path'] for k, v in job.sources['images'].items()},
                                 reason='G1 TOOL TEST: exact existing cameras and photos, no refit')


def canonical(out, workers):
    from glasses3d.tests.photoprint import bake_ft1119 as BA, print_tooltest as TT
    jobroot = out / 'job'
    basis = original_basis(jobroot)
    bake = out / 'bake'
    code = BA.main(['--job', str(jobroot), '--basis', basis.id, '--glb', str(IN.FT1119['glb']),
                    '--out', str(bake), '--workers', str(workers), '--heldout-workers', str(workers),
                    '--a4-seeds', '5', '--a7-serial', '--level', str(LEVEL),
                    '--tooltest', str(ROOT / 'data/wp5/resume_20261007/tooltest/tooltest.json')])
    if code:
        raise RuntimeError(f'independent bake proof exited {code}')
    same = {n: IN.sha256_file(bake / n) == IN.sha256_file(ACCEPTED / n)
            for n in ('print.png', 'classes.png', 'source.png', 'tier.png')}
    write_json(out / 'accepted_identity.json', {'files': same, 'pass': all(same.values()),
                                              'print_sha256': IN.sha256_file(bake / 'print.png')})
    if not all(same.values()):
        raise AssertionError(f'library migration changed the accepted output: {same}')
    if TT.main(['--bake', str(bake), '--out', str(out / 'tooltest')]):
        raise AssertionError('Blender copy proof failed')


def renamed(out, workers):
    """Change names only, preserving geometry, all photo pixels, and their fitted cameras."""
    from glasses3d.tests.photoprint.ar_calibrate import _chunks
    root = out / 'renamed_job'
    root.mkdir()
    names = {p: f'component.{i:02d}' for i, p in enumerate(IN.FT1119_PARTS)}
    source = IN.FT1119['glb']
    doc, binary = _chunks(source.read_bytes())
    changed = set()
    for node in doc.get('nodes', []):
        old_name = part_name(node.get('name', ''))
        if old_name in names:
            node['name'] = names[old_name]
            changed.add(old_name)
    assert changed == set(names), 'rename fixture must change every selected part'
    js = json.dumps(doc, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    js += b' ' * (-len(js) % 4)
    body = struct.pack('<II', len(js), 0x4e4f534a) + js + struct.pack('<II', len(binary), 0x004e4942) + binary
    glb = root / 'renamed_TOOLTEST.glb'
    glb.write_bytes(struct.pack('<4sII', b'glTF', 2, len(body) + 12) + body)
    cameras = json.loads(IN.FT1119['cameras'].read_bytes())
    cameras['model']['path'] = str(glb)
    cameras['model']['sha256'] = IN.sha256_file(glb)
    original = IN.load_job(IN.FT1119['cameras'])
    photos, records = {}, {}
    for i, (name, record) in enumerate(cameras['images'].items()):
        new = f'angle_vs_native_{i:02d}.png'
        target = root / new
        shutil.copyfile(original.sources['images'][name]['path'], target)
        photos[new] = target
        records[new] = {**record, 'image': str(target)}
    cameras['images'] = records
    cp = root / 'renamed_cameras.json'
    write_json(cp, cameras)
    basis = C.import_cameras_json(root, cp, photos, reason='G1 TOOL TEST: renamed parts/photos, no shape or pixel edit')
    job = IN.job_from_dir(root, glb=glb, basis=basis.id)
    assert set(names.values()) <= set(job.mesh.names)
    np.testing.assert_array_equal(job.V, original.V)
    np.testing.assert_array_equal(job.F, original.F)
    bake = out / 'renamed_bake'
    result = P.bake_print(job, list(names.values()), out_dir=bake, thresholds=dict(P.THRESHOLDS),
                          calibration=LEVEL, workers=workers, compare_fill=('patchmatch',))
    same = {n: IN.sha256_file(bake / n) == IN.sha256_file(out / 'bake' / n)
            for n in ('print.png', 'classes.png', 'source.png', 'tier.png')}
    old = out / 'bake'
    before = np.asarray(Image.open(old / 'print.png'))
    after = np.asarray(Image.open(bake / 'print.png'))
    old_cls = np.asarray(Image.open(old / 'classes.png'))
    new_cls = np.asarray(Image.open(bake / 'classes.png'))
    protected = np.isin(old_cls, (1, 2)) & np.isin(new_cls, (1, 2))
    # Existing fill seeds include each part's name; a rename intentionally changes generated fill.
    # The photo donor assignment and the colours of protected photo texels must remain invariant.
    checks = {'source_map': same['source.png'], 'tier_map': same['tier.png'],
              'protected_present': bool(protected.any()),
              'protected_identical': bool(np.array_equal(before[protected], after[protected])),
              'A1': bool(result['report']['checks']['A1']['pass']),
              'A3': bool(result['report']['checks']['A3']['protected_byte_identical'])}
    rep = {'names': names, 'photos': list(photos), 'files': same, 'checks': checks, 'pass': all(checks.values()),
           'protected_texels_compared': int(protected.sum()),
           'changed_pixels': int(np.any(before != after, axis=-1).sum()),
           'fill_seed_note': 'Names participate in existing fill seeds; whole-PNG identity is not a rename criterion.',
           'print_sha256': IN.sha256_file(bake / 'print.png'), 'receipt': result['production']}
    write_json(out / 'renamed_identity.json', rep)
    if not rep['pass']:
        raise AssertionError(f'renamed input invariance failed: {checks}')


def capture(out):
    job = IN.job_from_dir(out / 'job', glb=IN.FT1119['glb'], basis='basis-01')
    level, _ = P.read_level(LEVEL)
    family = {k: level[k] for k in ('chroma_hold', 'chroma_hue') if k in level}
    api = Calibrator(job, GROUPS, chroma_family=family, temple_z=(-104., -9.), reference_point_mm=(0., 10., 5.))
    glb = out / 'tooltest/q001_painted_TOOLTEST.glb'
    result = api.capture_with_probe(glb, out / 'ar', portrait=True)
    # Independent legacy measurement must agree on the fresh runtime captures.
    from glasses3d.tests.photoprint import ar_levels as AL
    independent = AL.measure([out / 'ar/capture'], job=job,
                             probes={str(out / 'ar/capture'): str(out / 'ar/probe')})
    actual = dict(result)
    actual.pop('glb')
    ok = actual == independent
    write_json(out / 'capture_parity.json', {'pass': ok, 'glb_sha256': IN.sha256_file(glb),
                                            'capture': str(out / 'ar/capture'), 'probe': str(out / 'ar/probe')})
    if not ok:
        raise AssertionError('runtime capture grading differs from the independent instrument')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--phase', choices=('canonical', 'renamed', 'capture'), required=True)
    ap.add_argument('--workers', type=int, required=True)
    a = ap.parse_args(argv)
    if a.workers < 1:
        ap.error('workers must be positive')
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    opt_out_ecoqos()
    with Progress('G1').heartbeat():
        if a.phase == 'canonical':
            canonical(out, a.workers)
        elif a.phase == 'renamed':
            renamed(out, a.workers)
        else:
            capture(out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
