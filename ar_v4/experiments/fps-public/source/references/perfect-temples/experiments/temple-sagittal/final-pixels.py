"""Independent persisted-PNG verification. Reads only; returns audit JSON on stdout."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

APP = Path(__file__).resolve().parents[2]
STUDY = APP / '.recovery' / 'temple-rethink-2026-09-08'


def load_png(entry, width, height):
    raw = (APP / entry['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == entry['sha256'], entry['path']
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format == 'PNG' and image.size == (width, height), entry['path']
        pixels = np.array(image.convert('RGBA'))
    assert np.all(pixels[:, :, 3] == 255), 'Native opaque output must preserve full alpha.'
    return pixels


def stats(before, after, selection=None):
    delta = np.abs(before.astype(np.int16) - after.astype(np.int16))
    changed = np.any(delta != 0, axis=2)
    if selection is not None:
        changed &= selection
        selected_delta = delta[selection]
    else:
        selected_delta = delta
    ys, xs = np.nonzero(changed)
    return {'changedPixels': int(changed.sum()), 'maxDelta': int(selected_delta.max(initial=0)),
            'bounds': [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None}


def rectangles_mask(rects, width, height):
    mask = np.zeros((height, width), dtype=bool)
    for rect in rects:
        x0, y0, x1, y1 = (rect[k] for k in ('x0', 'y0', 'x1', 'y1'))
        assert all(type(v) is int for v in (x0, y0, x1, y1))
        assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height
        mask[y0:y1, x0:x1] = True
    return mask


def main():
    report = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    assert report['complete'] and not report['errors']
    selection = json.loads((STUDY / 'selection.json').read_text(encoding='utf8'))
    selected = {frame['key']: frame for frame in selection['frames']}
    originals, rows = {}, []
    for row in report['cases']:
        width, height = row['width'], row['height']
        images = {name: load_png(row[name], width, height) for name in ('before', 'after', 'source', 'mask')}
        before, after = images['before'], images['after']
        actual = stats(before, after)
        assert actual == row['whole'], (row['id'], 'reported whole-image diff disagrees', actual, row['whole'])
        protection = row['protection']
        assert protection and protection['width'] == width and protection['height'] == height
        protected = rectangles_mask(protection['protectedRects'], width, height)
        editable = rectangles_mask(protection['editableRects'], width, height)
        protected_diff, outside_diff = stats(before, after, protected), stats(before, after, ~editable)
        assert protected_diff['changedPixels'] == 0 and outside_diff['changedPixels'] == 0
        expected_mask = np.zeros_like(images['mask'])
        expected_mask[:, :, 3] = 255
        expected_mask[editable, :3] = (0, 170, 0)
        expected_mask[protected, :3] = (0, 68, 255)
        assert np.array_equal(expected_mask, images['mask']), 'Saved mask pixels disagree with the actual integer rectangles.'
        assert row['protectedPixels'] == int(protected.sum())
        assert row['protectedChanged'] == 0 and row['outsideChanged'] == 0
        nose, union = [], np.zeros((height, width), dtype=bool)
        for region in selected[row['id']]['regions']['noseProtectionRois']:
            x0, y0, x1, y1 = region['roi']
            mask = np.zeros((height, width), dtype=bool)
            mask[y0:y1, x0:x1] = True
            difference = stats(before, after, mask)
            assert difference['changedPixels'] == 0, (row['id'], row['model'], region['kind'], difference)
            union |= mask
            nose.append({'kind': region['kind'], 'roi': region['roi'], **difference})
        assert stats(before, after, union) == row['nose']
        meta = selected[row['id']]
        if meta['sourcePath'] not in originals:
            original_bytes = (APP / meta['sourcePath']).read_bytes()
            assert hashlib.sha256(original_bytes).hexdigest() == meta['sourceSHA256']
            originals[meta['sourcePath']] = json.loads(original_bytes)
        frame = originals[meta['sourcePath']]['frames'][meta['sourceFrameArrayIndex']]
        data_url = frame[meta['imageField']]
        encoded = base64.b64decode(data_url.split(',', 1)[1], validate=True)
        assert hashlib.sha256(encoded).hexdigest() == meta['sourceImageSHA256']
        with Image.open(io.BytesIO(encoded)) as original_image:
            assert original_image.size == (width, height)
            source_pixels = np.array(original_image.convert('RGBA'))
            original_format = original_image.format
        decoded_source = stats(source_pixels, images['source'])
        # PNG is lossless. JPEG RGB decoders can differ slightly in chroma upsampling;
        # retain that distinction instead of claiming encoded JPEG/PNG equality.
        if original_format == 'PNG':
            assert decoded_source['changedPixels'] == 0, (row['id'], 'source PNG pixels changed')
        else:
            assert decoded_source['maxDelta'] <= 3, (row['id'], 'JPEG decoder discrepancy exceeds the declared bound', decoded_source)
        inactive = row['drop']['dropM'] == 0
        if inactive:
            assert actual['changedPixels'] == 0, (row['id'], 'inactive control changed')
        rows.append({'id': row['id'], 'model': row['model'], 'pitchDegrees': row['pitch'], 'yawDegrees': row['yaw'],
                     'actualDropM': row['candidateSnapshot']['rearDrop']['dropM'], 'inactiveControl': inactive,
                     'actualWholeDifference': actual, 'noseRegions': nose,
                     'protectedPixelCount': int(protected.sum()), 'protectedDifference': protected_diff,
                     'outsideEditableDifference': outside_diff,
                     'sourceDecodedComparison': {'originalFormat': original_format, **decoded_source},
                     'verifiedPngFiles': [row[name]['path'] for name in images]})
    models = {}
    for model in sorted({row['model'] for row in rows}):
        subset = [row for row in rows if row['model'] == model]
        changed = [row for row in subset if row['actualWholeDifference']['changedPixels']]
        models[model] = {'cases': len(subset), 'changedCases': len(changed),
                         'changedCaseIds': [row['id'] for row in changed],
                         'totalChangedPixels': sum(row['actualWholeDifference']['changedPixels'] for row in subset),
                         'inactiveControls': sum(row['inactiveControl'] for row in subset),
                         'upwardCases': sum(row['pitchDegrees'] > 0 for row in subset),
                         'downwardCases': sum(row['pitchDegrees'] < 0 for row in subset),
                         'negativeYawCases': sum(row['yawDegrees'] < 0 for row in subset),
                         'positiveYawCases': sum(row['yawDegrees'] > 0 for row in subset)}
    print(json.dumps({'rows': rows, 'summary': {'cases': len(rows), 'pngFilesVerified': len(rows) * 4,
          'allFrozenNoseRoisByteExact': True, 'allProtectedPixelsByteExact': True,
          'allOutsideEditablePixelsByteExact': True, 'allInactiveControlsByteExact': True,
          'models': models}}, separators=(',', ':')))


if __name__ == '__main__':
    main()
