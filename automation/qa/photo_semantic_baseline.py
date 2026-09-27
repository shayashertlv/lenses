"""Fixed photo-only semantic baseline; predictions never certify lens identity.

Run the public Glasses Detector v1 medium lens/frame heads with captured local
weights and the author's RGB/256/ImageNet preprocessing. No model, camera, mesh
name, optical role or SAM prompt is supplied. Preserve full-image and fixed
image-contrast crop results plus horizontal-flip sensitivity, including failures.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import time

import numpy as np
from PIL import Image, ImageDraw

from reconstruction.input_bundle import _normalize
from reconstruction.observations import observe_image
from reconstruction.photo_lens_observations import _read_pinned
from reconstruction.region_engine import _offline


KINDS = ('lenses', 'frames')
MEAN, STD = (.485, .456, .406), (.229, .224, .225)


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iou(a, b):
    union = int(np.count_nonzero(a | b))
    return {'intersection_pixels': int(np.count_nonzero(a & b)), 'union_pixels': union,
            'iou': float(np.count_nonzero(a & b)/union) if union else None,
            'both_empty': union == 0}


def _native(logits, box, size):
    x0, y0, x1, y1 = box
    # Unobserved outside a crop is not a negative semantic measurement.
    result = np.full((size[1], size[0]), np.nan, dtype=np.float32)
    result[y0:y1, x0:x1] = np.asarray(Image.fromarray(logits).resize((x1-x0, y1-y0), Image.Resampling.BILINEAR))
    return result


def _panel(image, native, title):
    pixels = np.asarray(image, dtype=np.uint8)
    if native is not None:
        lens, frame = native['lenses'] > 0, native['frames'] > 0
        colors = np.zeros_like(pixels)
        colors[lens] = (20, 140, 255); colors[frame] = (255, 70, 30); colors[lens & frame] = (230, 40, 210)
        chosen = lens | frame
        pixels = pixels.copy(); pixels[chosen] = np.rint(.55*pixels[chosen]+.45*colors[chosen]).astype(np.uint8)
    picture = Image.fromarray(pixels); picture.thumbnail((420, 250))
    result = Image.new('RGB', (440, 282), 'white')
    result.paste(picture, ((440-picture.width)//2, 29+(250-picture.height)//2))
    ImageDraw.Draw(result).text((8, 6), title, fill='black')
    return result


def run(manifest_path, weights_folder, output):
    import torch
    import torchvision
    from torchvision.models.segmentation import lraspp_mobilenet_v3_large
    from torchvision.transforms.v2.functional import normalize, to_dtype, to_image

    manifest_path, weights_folder, output = (Path(p).resolve() for p in (manifest_path, weights_folder, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh empty baseline output folder')
    pins = {}
    manifest = json.loads(_read_pinned(manifest_path, pins))
    rows = manifest['images']
    if manifest.get('schema_version') != 1 or not rows:
        raise ValueError('Expected a nonempty version-one image manifest')
    if len({row['id'] for row in rows}) != len(rows) or any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}', row['id']) for row in rows):
        raise ValueError('Unique safe photo IDs required')
    captures = {}
    for row in rows:
        source = Path(row['path']).resolve()
        if source.is_relative_to(output):
            raise ValueError('Source photo must be outside output')
        captures[row['id']] = _read_pinned(source, pins, row['sha256'])
    receipt_path = weights_folder/'download-receipt.json'
    receipt = json.loads(_read_pinned(receipt_path, pins))
    artifacts = {entry['path']: entry for entry in receipt['artifacts']}
    weight_bytes = {}
    for kind in KINDS:
        name = f'segmentation_{kind}_lraspp_mobilenet_v3_large.pth'
        weight_bytes[kind] = _read_pinned(weights_folder/name, pins, artifacts[name]['sha256'])
    source_pins = {str(Path(__file__).resolve()): _sha(Path(__file__).resolve())}
    for name in ('input_bundle.py', 'observations.py', 'photo_lens_observations.py', 'region_engine.py'):
        path = Path(__file__).resolve().parents[1]/'reconstruction'/name; source_pins[str(path)] = _sha(path)
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    report = {'schema_version': 1, 'method': 'photo_only_glasses_detector_medium_baseline_v1',
        'status': 'running', 'accepted': False, 'quality_verdict': 'unmeasured', 'source_model': receipt,
        'policy': {'architecture': 'torchvision.lraspp_mobilenet_v3_large; one binary logit per head',
            'input_size': [256, 256], 'resize': 'PIL RGB bicubic, author default; aspect ratios restored by recorded mapping',
            'mean': MEAN, 'std': STD, 'threshold': 'logit > 0; uncalibrated class prediction',
            'variants': ['full', 'contrast_crop'], 'contrast_margin_fraction_per_axis': .15,
            'contrast_crop_fallback': 'full image when no measured contrast box',
            'horizontal_flip_control': 'flip the same crop before preprocessing, then unflip its raw output',
            'native_logit_interpolation': 'bilinear within crop only; outside crop is unknown',
            'device': 'cpu', 'torch_threads': 4, 'deterministic_algorithms': True},
        'packages': {'torch': torch.__version__, 'torchvision': torchvision.__version__, 'numpy': np.__version__},
        'implementation_sha256': source_pins, 'images': [], 'limitations': [
            'No product-photo semantic ground truth is supplied; scores are not calibrated probabilities or accuracy.',
            'Training includes face crops; product-photo, clear/shield/mirror/gradient coverage is undocumented.',
            'Lens/frame heads are independent and may overlap; overlap does not resolve visible-versus-amodal semantics.',
            'A stable mask is not proof of physical identity, 3D membership, hidden surfaces or optical material.',
            'No generated meshes, camera estimates, source roles or candidate projections enter inference.',
            'Catalog probes are unused in this pipeline, not proven absent from upstream model training.']}
    models, panels = {}, []
    started = time.perf_counter()
    with _offline(output/'offline-runtime'):
        for kind in KINDS:
            model = lraspp_mobilenet_v3_large(weights=None, weights_backbone=None, num_classes=1)
            state = torch.load(io.BytesIO(weight_bytes[kind]), map_location='cpu', weights_only=True)
            model.load_state_dict(state, strict=True); model.eval(); models[kind] = model
        for row in rows:
            item = {'id': row['id'], 'split': row['split'], 'source': row['path'], 'source_sha256': row['sha256'],
                    'status': 'running', 'accepted': False, 'quality_verdict': 'unmeasured'}
            report['images'].append(item); folder = output/row['id']; folder.mkdir()
            case_start = time.perf_counter()
            try:
                meta, normalized_bytes = _normalize(captures[row['id']], row['id'], row.get('view') or 'unknown', Path(row['path']))
                meta['original']['path'] = 'original.source'
                meta['normalized']['path'] = 'normalized.png'
                (folder/'original.source').write_bytes(captures[row['id']])
                (folder/'normalized.png').write_bytes(normalized_bytes)
                with Image.open(io.BytesIO(normalized_bytes)) as normalized:
                    if np.any(np.asarray(normalized.convert('RGBA'))[:, :, 3] != 255):
                        raise ValueError('Nonopaque photo requires a declared background; baseline does not composite it')
                    image = normalized.convert('RGB')
                item['normalization'] = meta
                width, height = image.size; full = (0, 0, width, height)
                contrast = observe_image(image); item['contrast'] = contrast.to_report()
                crop = full
                if contrast.status == 'measured' and contrast.bbox_xyxy is not None:
                    x0, y0, x1, y1 = contrast.bbox_xyxy
                    dx, dy = int(np.ceil(.15*(x1-x0))), int(np.ceil(.15*(y1-y0)))
                    crop = (max(0, x0-dx), max(0, y0-dy), min(width, x1+dx), min(height, y1+dy))
                item['variants'] = []; natives = {}
                for variant, box in (('full', full), ('contrast_crop', crop)):
                    cropped = image.crop(box)
                    xs = [normalize(to_dtype(to_image(photo.resize((256, 256))), torch.float32, scale=True), MEAN, STD)
                          for photo in (cropped, cropped.transpose(Image.Transpose.FLIP_LEFT_RIGHT))]
                    inputs = torch.stack(xs)
                    result = {'name': variant, 'crop_xyxy_exclusive': list(box), 'size_xy': [width, height],
                        'grid_to_native_pixel_center': [[(box[2]-box[0])/256, 0, box[0]+.5*(box[2]-box[0])/256-.5],
                            [0, (box[3]-box[1])/256, box[1]+.5*(box[3]-box[1])/256-.5], [0, 0, 1]], 'heads': {}}
                    item['variants'].append(result); arrays = {}; native = {}
                    with torch.inference_mode():
                        for kind, model in models.items():
                            logits = model(inputs)['out'][:, 0].cpu().numpy()
                            if logits.shape != (2, 256, 256) or not np.isfinite(logits).all():
                                raise ValueError('Invalid segmentation logits')
                            original, flipped = logits[0].copy(), logits[1, :, ::-1].copy()
                            arrays[kind+'_logits'] = original; arrays[kind+'_flip_aligned_logits'] = flipped
                            native[kind] = _native(original, box, image.size)
                            Image.fromarray((native[kind] > 0).astype(np.uint8)*255).save(folder/f'{variant}-{kind}.png')
                            result['heads'][kind] = {'grid_positive_pixels': int(np.count_nonzero(original > 0)),
                                'native_positive_pixels': int(np.count_nonzero(native[kind] > 0)),
                                'logit_range': [float(original.min()), float(original.max())],
                                'flip_consistency': _iou(original > 0, flipped > 0),
                                'flip_mean_absolute_score_difference': float(np.mean(np.abs(
                                    1/(1+np.exp(-np.clip(original, -80, 80)))-1/(1+np.exp(-np.clip(flipped, -80, 80))))))}
                    result['head_overlap'] = _iou(native['lenses'] > 0, native['frames'] > 0)
                    path = folder/f'{variant}-logits.npz'; np.savez_compressed(path, **arrays)
                    result['arrays'] = {'path': path.name, 'sha256': _sha(path)}
                    natives[variant] = native
                support = np.isfinite(natives['contrast_crop']['lenses'])
                item['crop_sensitivity'] = {kind: _iou((natives['full'][kind] > 0) & support,
                                                      natives['contrast_crop'][kind] > 0) for kind in KINDS}
                item['status'] = 'predictions_recorded'
                # Only display crops for readability; inference already used its
                # fixed full/crop policy and no figure changes its saved arrays.
                display_box = crop
                strip = Image.new('RGB', (1320, 282), 'white')
                for column, (title, native) in enumerate((('photo', None), ('full prediction', natives['full']), ('crop prediction', natives['contrast_crop']))):
                    shown = None if native is None else {kind: values[display_box[1]:display_box[3], display_box[0]:display_box[2]] for kind, values in native.items()}
                    strip.paste(_panel(image.crop(display_box), shown, row['id']+' / '+title), (column*440, 0))
                panels.append(strip)
            except Exception as error:
                item.update(status='failed', error_type=type(error).__name__, error=str(error))
            item['seconds'] = time.perf_counter()-case_start
            _write(folder/'report.json', item)
            print(json.dumps({key: item[key] for key in ('id', 'status', 'seconds', 'error') if key in item}), flush=True)
    for start in range(0, len(panels), 6):
        page = panels[start:start+6]; sheet = Image.new('RGB', (1320, 282*len(page)), 'white')
        for index, panel in enumerate(page): sheet.paste(panel, (0, index*282))
        sheet.save(output/f'contact-sheet-{start//6+1:02d}.png')
    for name, digest in {**pins, **source_pins}.items():
        if _sha(Path(name)) != digest: raise ValueError('Baseline input or implementation changed: '+name)
    report.update(status='prediction_experiment_complete' if all(item['status']=='predictions_recorded' for item in report['images']) else 'incomplete_experiment',
                  seconds=time.perf_counter()-started, input_sha256=pins)
    _write(output/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.manifest, args.weights, args.output)
    raise SystemExit(0 if result['status']=='prediction_experiment_complete' else 1)
