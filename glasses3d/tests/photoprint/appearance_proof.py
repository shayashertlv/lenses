"""WP5 brow-top diagnosis and comparison on copies of the FT1119 tool-test bake.

This is an in-sample appearance diagnostic, not a held-out registration proof. The two top-photo crop boxes were
identified from the frozen owner sheet before the repair. Production contains no product coordinates. Optional
AR captures use the exact frozen cameras and unchanged materials, with fresh hashes recorded for both GLBs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint.compose import render_bake
from glasses3d.tests.photoprint import all_angles as AA
from glasses3d.tests.photoprint.correction_proof import _load

TOP_BOXES = ((370, 802, 600, 844), (930, 802, 1150, 844))


def verify_assets(before, after, before_glb, after_glb):
    from glasses3d.tests.instruments import print_metrics as PM
    from glasses3d.tests.photoprint import ar_calibrate as AC
    control = PM.b5_material_control(before_glb, after_glb, IN.FT1119_PARTS)
    if not control['pass']:
        raise ValueError(f'comparison changed geometry or materials: {control["failures"]}')
    for part, changes in control['base_colour_changes'].items():
        if set(changes) - {'baseColorTexture'}:
            raise ValueError(f'{part}: base colour factors changed')
        if 'baseColorTexture' in changes:
            a, b = changes['baseColorTexture']
            strip_image = lambda v: {k: val for k, val in v.items() if k != 'image_sha256'}
            if not isinstance(a, dict) or not isinstance(b, dict) or strip_image(a) != strip_image(b):
                raise ValueError(f'{part}: texture sampling changed')
    result = {'geometry_material_control': control, 'textures': {}}
    for label, bake, glb in [('before', before, before_glb), ('after', after, after_glb)]:
        doc, binary = AC._chunks(glb.read_bytes())
        png = AA._glb_image(doc, binary, AC.print_image_index(doc))
        actual = hashlib.sha256(png).hexdigest()
        expected = IN.sha256_file(bake / 'print.png')
        if actual != expected:
            raise ValueError(f'{label}: GLB does not embed the compared print PNG')
        result['textures'][label] = {'glb_sha256': IN.sha256_file(glb), 'png_sha256': actual}
    return result


def corr(a, b, mask):
    a, b = a[mask], b[mask]
    if len(a) < 2 or min(np.std(a), np.std(b)) < 1e-8:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def band(image, mask):
    f = image.astype(float).mean(-1)
    m = mask.astype(float)
    return (ndimage.gaussian_filter(f * m, 1) / np.maximum(ndimage.gaussian_filter(m, 1), 1e-8)
            - ndimage.gaussian_filter(f * m, 4) / np.maximum(ndimage.gaussian_filter(m, 4), 1e-8))


def top_diagnostic(before: Path, after: Path, out: Path):
    job, rep, lay, regions, tex, occ, tx = _load(before)
    current = json.loads((after / 'report.json').read_text(encoding='utf-8'))
    assert rep['inputs'] == current['inputs'], 'comparison inputs changed'
    assert rep['layout'] == current['layout'], 'comparison layout changed'
    for key in ('gain', 'gamma', 'sat', 'chroma_power', 'chroma_hold', 'chroma_hue'):
        if rep['level'].get(key) != current['level'].get(key):
            raise ValueError(f'comparison level changed: {key}')
    view = job.views['reference-04.png']
    result = {'role': 'in-sample top-photo diagnostic; not evidence of held-out print registration',
              'source_sha256': view.sha256, 'bakes': {}, 'crops': []}
    renders = {}
    for name, path in [('before', before), ('after', after)]:
        atlas = np.asarray(Image.open(path / 'print.png').convert('RGB'))
        renders[name] = render_bake(job, lay, atlas, view.name, view.camera, scene=occ.scene(job.mesh.V))
        result['bakes'][name] = {'path': str(path), 'print_sha256': IN.sha256_file(path / 'print.png')}
    mask = renders['before']['mask'] & renders['after']['mask'] & view.matte
    mask = ndimage.binary_erosion(mask, iterations=2)
    bands = {'photo': band(view.image, mask), **{k: band(v['ren'], mask) for k, v in renders.items()}}
    rows = []
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
    for box in TOP_BOXES:
        x0, y0, x1, y1 = box
        sel = mask[y0:y1, x0:x1]
        metrics = {k: corr(bands['photo'][y0:y1, x0:x1], bands[k][y0:y1, x0:x1], sel)
                   for k in renders}
        result['crops'].append({'box_native_xyxy': box, 'pixels': int(sel.sum()), 'bandpass_r_1_4px': metrics})
        row = Image.new('RGB', (1500, 140), 'white')
        draw = ImageDraw.Draw(row)
        for j, (label, arr) in enumerate([('Photo', view.image), ('Selected B', renders['before']['ren']),
                                          ('Revised mask — TOOL TEST', renders['after']['ren'])]):
            draw.text((j * 500 + 8, 4), label, fill='black', font=font)
            im = Image.fromarray(np.clip(arr[y0:y1, x0:x1], 0, 255).astype('uint8'))
            im = im.resize((490, int(im.height * 490 / im.width)))
            row.paste(im, (j * 500 + 5, 32))
        rows.append(row)
    sheet = Image.new('RGB', (1500, 280), 'white')
    for i, row in enumerate(rows):
        sheet.paste(row, (0, i * 140))
    sheet.save(out / 'top_unlit.png')
    return result


def verify_capture(glb, cameras, out):
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    if report['input']['sha256'] != IN.sha256_file(glb):
        raise ValueError(f'capture input changed: {glb}')
    if report['request']['cameras']['sha256'] != IN.sha256_file(cameras):
        raise ValueError(f'capture cameras changed: {cameras}')
    records = json.loads(cameras.read_text(encoding='utf-8'))['cameras']
    expected = {f"{r['id']}__{variant}.png" for r in records for variant in ('beauty', 'part-id')}
    actual = {r['file']: r for r in report['images']}
    if set(actual) != expected or len(actual) != len(report['images']):
        raise ValueError(f'incomplete or duplicate capture images: {out}')
    for name, rec in actual.items():
        if IN.sha256_file(out / name) != rec['sha256']:
            raise ValueError(f'capture pixels changed: {out / name}')
    return {'glb_sha256': IN.sha256_file(glb), 'cameras_sha256': IN.sha256_file(cameras),
            'report_sha256': IN.sha256_file(out / 'report.json'), 'images_verified': len(actual),
            'exit_code': report['exit_code']}


def capture(glb, cameras, out):
    args = ['node', 'bin/arview.mjs', 'capture', str(glb.resolve()), '--cameras', str(cameras.resolve()),
            '--no-face', '--out', str(out.resolve())]
    with out.with_suffix('.log').open('w', encoding='utf-8') as log:
        code = subprocess.call(args, cwd=AA.ARVIEW, stdout=log, stderr=subprocess.STDOUT)
    if code not in (0, 3) or not (out / 'report.json').is_file():
        raise RuntimeError(f'AR capture failed ({code}): {out}')
    return verify_capture(glb, cameras, out)


def photo_sheets(before_glb, after_glb, cameras, out):
    job = IN.load_job(IN.FT1119["cameras"])
    records = json.loads(cameras.read_text(encoding='utf-8'))['cameras']
    result = {}
    for label, glb in [('before', before_glb), ('after', after_glb)]:
        result[label] = capture(glb, cameras, out / label)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 22)
    for rec in records:
        size = (rec['width'], rec['height'])
        photo = job.views[rec['meta']['photo']].image
        crop = rec['meta']['crop_native_xywh']
        photo = Image.fromarray(AA.crop_resize(photo, crop, size, 255, Image.Resampling.BILINEAR).astype('uint8'))
        images = [photo] + [Image.open(out / k / f"{rec['id']}__beauty.png").convert('RGB') for k in ('before', 'after')]
        sheet = Image.new('RGB', (size[0] * 3, size[1] + 42), 'white')
        draw = ImageDraw.Draw(sheet)
        for j, (im, label) in enumerate(zip(images, ['Your photo', 'Selected B', 'Revised mask — TOOL TEST'])):
            draw.text((j * size[0] + 8, 8), label, fill='black', font=font)
            sheet.paste(im, (j * size[0], 42))
        sheet.save(out / f"{rec['id']}_comparison.png")
    return result


def focus_sheet(cameras: Path, out: Path):
    """Labelled enlargements selected by part-ID, without changing render pixels or materials."""
    from glasses3d.tests.instruments import print_metrics as PM
    from glasses3d.tests.photoprint import ar_levels as AL
    from glasses3d.tests.photoprint.step6_proof import sides
    from glasses3d.tests.photoprint.tooltest_closeup_sheet import _box
    job = IN.load_job(IN.FT1119["cameras"])
    occ = AL._occ(job)
    side = sides(job)
    records = {r['id']: r for r in json.loads(cameras.read_text(encoding='utf-8'))['cameras']}
    specs = [('Top of the brow', '04', [side['-x'][0]]),
             ("Wearer's right arm", '03', [side['-x'][1]]),
             ("Wearer's left arm", '06', [side['+x'][1]])]
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 20)
    rows, boxes = [], []
    for title, short, parts in specs:
        rec = records[f'photo_{short}']
        size = rec['width'], rec['height']
        view = job.views[rec['meta']['photo']]
        crop = rec['meta']['crop_native_xywh']
        ph = AA.crop_resize(view.image, crop, size, 255, Image.Resampling.BILINEAR).astype('uint8')
        pm = AL.photo_frame_mask(job, view.name, occ, parts)
        pm = AA.crop_resize((pm * 255).astype('uint8'), crop, size, 0, Image.Resampling.NEAREST) > 0
        images, masks = [ph], [pm]
        for k in ('before', 'after'):
            report = json.loads((out / k / 'report.json').read_text(encoding='utf-8'))
            pid = np.asarray(Image.open(out / k / f'photo_{short}__part-id.png').convert('RGB'))
            masks.append(PM.ar_frame_mask(pid, report['id_palette'], parts))
            images.append(np.asarray(Image.open(out / k / f'photo_{short}__beauty.png').convert('RGB')))
        box = _box(np.logical_or.reduce(masks), 10, size)
        tiles = [Image.fromarray(im).crop(box) for im in images]
        height = round(600 * tiles[0].height / tiles[0].width)
        row = Image.new('RGB', (1800, height + 74), 'white')
        draw = ImageDraw.Draw(row)
        draw.text((8, 4), title, fill='black', font=font)
        for j, (im, label) in enumerate(zip(tiles, ['Your photo', 'Selected B', 'Revised mask — TOOL TEST'])):
            draw.text((j * 600 + 8, 35), label, fill='black', font=font)
            row.paste(im.resize((600, height), Image.Resampling.LANCZOS), (j * 600, 68))
        rows.append(row)
        boxes.append({'photo':short, 'parts':parts, 'shared_box':box})
    sheet = Image.new('RGB', (1800, sum(r.height for r in rows) + 60), 'white')
    y = 0
    for row in rows:
        sheet.paste(row, (0, y))
        y += row.height
    ImageDraw.Draw(sheet).text((8, y + 5), 'Same geometry and materials in both tool tests. Side outlines still differ from the photos.',
                               fill='black', font=font)
    sheet.save(out / 'owner_focus.png')
    return boxes


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--before', required=True, type=Path)
    ap.add_argument('--after', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--before-glb', type=Path)
    ap.add_argument('--after-glb', type=Path)
    ap.add_argument('--cameras', type=Path)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--focus-only', action='store_true', help='audit and remake existing AR comparison enlargements')
    mode.add_argument('--verify-only', action='store_true', help='audit both GLBs against their bakes without capturing')
    a = ap.parse_args()
    if any((a.before_glb, a.after_glb, a.cameras)) and not all((a.before_glb, a.after_glb, a.cameras)):
        ap.error('AR comparison needs both GLBs and the frozen cameras')
    a.out.mkdir(parents=True, exist_ok=True)
    audit = (verify_assets(a.before, a.after, a.before_glb, a.after_glb) if a.before_glb else None)
    if a.verify_only or a.focus_only:
        if audit is None:
            ap.error('--verify-only / --focus-only need both GLBs and the frozen cameras')
        if a.focus_only or any((a.out / label).exists() for label in ('before', 'after')):
            audit['captures'] = {label: verify_capture(glb, a.cameras, a.out / label)
                                 for label, glb in [('before', a.before_glb), ('after', a.after_glb)]}
        if a.focus_only:
            audit['focus_boxes'] = focus_sheet(a.cameras, a.out)
        (a.out / 'asset_audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
        print(json.dumps(audit, indent=1))
        return
    result = top_diagnostic(a.before, a.after, a.out)
    result['asset_audit'] = audit
    if a.before_glb:
        result['ar'] = photo_sheets(a.before_glb, a.after_glb, a.cameras, a.out)
        result['focus_boxes'] = focus_sheet(a.cameras, a.out)
    (a.out / 'appearance.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
