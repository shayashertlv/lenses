"""Fixed-policy local region-stage experiment; no semantic acceptance benchmark.

Run from automation, after the region-stage implementation is frozen:
  python qa/region_corpus.py --output data/region-corpus/run1 --weights C:/local/model.pt
    --weights-sha256 <sha256>
Use --prepare-only to pin inputs without inference; --resume verifies all saved
bytes and reuses completed cases. Interrupted attempts remain untouched. The two
controls have no candidate model/camera, and never imply that every mask must be
empty: SAM can legitimately segment the unrelated object.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXPECTED = ('rayban', 'miumiu', 'oakley', 'invu', 'victoria-beckham')
CONTROLS = ('white-control', 'unrelated-object-control')


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()


def atomic(path: Path, value) -> None:
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(encoded(value))
    temporary.replace(path)


def source_record(path: Path) -> dict:
    return {'path': str(path.resolve()), 'sha256': sha(path)}


def verify_source(record: dict) -> None:
    if sha(Path(record['path'])) != record['sha256']:
        raise ValueError(f'Source changed: {record["path"]}')


def implementation() -> dict:
    files = sorted((ROOT / 'reconstruction').glob('*.py')) + [Path(__file__)]
    return {str(path.relative_to(ROOT)).replace('\\', '/'): sha(path) for path in files}


def control_images() -> dict[str, bytes]:
    images = {'white-control': Image.new('RGB', (768, 512), 'white')}
    mug = Image.new('RGB', (768, 512), 'white')
    draw = ImageDraw.Draw(mug)
    draw.ellipse((421, 156, 580, 354), fill=(31, 72, 145))
    draw.ellipse((448, 186, 549, 321), fill='white')
    draw.rounded_rectangle((227, 150, 461, 377), radius=32, fill=(43, 101, 196))
    draw.ellipse((227, 130, 461, 181), fill=(19, 44, 81))
    draw.ellipse((241, 140, 447, 169), fill=(69, 37, 20))
    images['unrelated-object-control'] = mug
    result = {}
    for name, image in images.items():
        buffer = io.BytesIO()
        image.save(buffer, format='PNG', compress_level=9)
        result[name] = buffer.getvalue()
    return result


def prepare(manifest_path: Path, reports: Path, output: Path, *, selected=(),
            weights: Path | None = None, weights_sha256: str | None = None,
            resume=False) -> dict:
    """Validate original assets/camera bindings before writing any output."""
    from reconstruction.mesh import load_glb

    manifest_path, reports, output = manifest_path.resolve(), reports.resolve(), output.resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    cases = manifest.get('cases', [])
    if manifest.get('schema_version') != 1 or sorted(row.get('id', '') for row in cases) != sorted(EXPECTED):
        raise ValueError('Expected exactly the five archived design IDs')
    if len(set(selected)) != len(selected) or set(selected) - set(EXPECTED):
        raise ValueError('--case must select unique known design IDs')
    if bool(weights) != bool(weights_sha256) or (weights_sha256 and not re.fullmatch('[a-f0-9]{64}', weights_sha256)):
        raise ValueError('Local weights and their exact lowercase SHA-256 must be supplied together')
    engine = {'kind': 'unavailable'}
    if weights:
        engine = {'kind': 'OfflineSAM2RegionEngine', **source_record(weights.resolve())}
        if engine['sha256'] != weights_sha256:
            raise ValueError('Checkpoint hash mismatch')
    rows = []
    for case in cases:
        if selected and case['id'] not in selected:
            continue
        model = (manifest_path.parent / case['model']).resolve()
        model_record = source_record(model)
        report_path = reports / case['id'] / 'report.json'
        camera_record = source_record(report_path)
        camera = json.loads(report_path.read_bytes())
        if camera.get('source_sha256') != model_record['sha256']:
            raise ValueError(f'Camera report must bind the ORIGINAL model: {case["id"]}')
        if set(case.get('photos', {})) != {'front', 'angled'}:
            raise ValueError('Every saved design must retain its original front and angled photos')
        photos = []
        for view in ('front', 'angled'):
            photo = {'id': view, 'view': view, **source_record((manifest_path.parent / case['photos'][view]).resolve())}
            matches = [row for row in camera.get('views', []) if row.get('view_id') == view]
            if len(matches) != 1 or matches[0].get('source_sha256') != photo['sha256']:
                raise ValueError(f'Camera/photo binding mismatch: {case["id"]}/{view}')
            with Image.open(photo['path']) as image:
                if image.getexif().get(274, 1) != 1:
                    raise ValueError('Archived originals require EXIF orientation one')
            photos.append(photo)
        mesh = load_glb(model)
        rows.append({'id': case['id'], 'kind': 'archived_design', 'model': model_record,
                     'refinement_report': camera_record, 'photos': photos,
                     'candidate_metadata': {'vertices': len(mesh.vertices), 'triangles': len(mesh.faces), 'parts': mesh.parts,
                                            'interpretation': 'Authored labels/material names, not verified photo semantics'}})
    controls = control_images()
    for name, data in controls.items():
        rows.append({'id': name, 'kind': 'negative_control', 'model': None, 'refinement_report': None,
                     'photos': [{'id': 'control', 'view': 'unknown', 'path': str(output / 'controls' / f'{name}.png'),
                                 'sha256': hashlib.sha256(data).hexdigest()}],
                     'expectation': 'No independent glasses semantics or accepted reconstruction. A mask is not a semantic detection.'})
    receipt = {'schema_version': 1, 'scope': 'Fixed-policy automatic region hypotheses on five archived products and two controls; no verified component labels',
               'quality_verdict': 'unmeasured', 'accepted': False, 'manifest': source_record(manifest_path),
               'implementation': implementation(), 'engine': engine, 'cases': rows,
               'policy': 'Unmodified run_region_stage defaults for every image; no product-specific prompts or thresholds'}
    if output.exists() and any(output.iterdir()):
        if not resume or (output / 'corpus-receipt.json').read_bytes() != encoded(receipt):
            raise ValueError('Output is not empty or its pinned inputs/implementation changed; use a new directory')
        if (output / 'source-manifest.json').read_bytes() != raw:
            raise ValueError('Archived corpus manifest was altered')
        for row in rows:
            for photo in row['photos']:
                verify_source(photo)
        return receipt
    output.mkdir(parents=True, exist_ok=True)
    (output / 'controls').mkdir()
    for name, data in controls.items():
        (output / 'controls' / f'{name}.png').write_bytes(data)
    (output / 'source-manifest.json').write_bytes(raw)
    atomic(output / 'corpus-receipt.json', receipt)
    return receipt


def artifacts(folder: Path) -> dict:
    return {path.relative_to(folder).as_posix(): sha(path) for path in sorted(folder.rglob('*')) if path.is_file()}


def summarize(result: dict) -> dict:
    views = result.get('photos', [])
    return {'status': result.get('status'), 'quality_verdict': result.get('quality_verdict'), 'accepted': result.get('accepted'),
            'views': [{'id': view.get('id', view.get('photo_id', view.get('view_id'))), 'status': view.get('status'),
                       'regions': len(view.get('regions', [])),
                       'candidate_optical_regions': sum(region.get('kind') == 'candidate_optical_region' for region in view.get('regions', [])),
                       'raw_alternatives': sum(sum(len(variant) for variant in region.get('alternatives', [])) for region in view.get('regions', [])),
                       'hypotheses': sum(len(region.get('hypotheses', [])) for region in view.get('regions', [])),
                       'stable_hypotheses': sum(hypothesis.get('diagnostics', {}).get('stable_under_prompts') is True
                                                for region in view.get('regions', []) for hypothesis in region.get('hypotheses', [])),
                       'contrast_status': view.get('contrast', {}).get('status'),
                       'candidate_projection_status': view.get('candidate_projection', {}).get('status')}
                      for view in views]}


def contact_sheet(receipt: dict, rows: list[dict], output: Path) -> dict:
    completed = {row['id']: row for row in rows if row.get('attempt')}
    tiles = []
    for case in receipt['cases']:
        saved = completed.get(case['id'], {})
        for photo in case['photos']:
            overlay = output / saved.get('attempt', '__missing__') / photo['id'] / 'overlay.png'
            source = overlay if overlay.is_file() else Path(photo['path'])
            with Image.open(source) as image:
                card = Image.new('RGB', (420, 320), (240, 243, 247))
                preview = ImageOps.contain(image.convert('RGB'), (404, 272))
                card.paste(preview, ((420-preview.width)//2, 40+(272-preview.height)//2))
                draw = ImageDraw.Draw(card)
                draw.text((8, 7), f'{case["id"]} / {photo["view"]}', fill='black')
                draw.text((8, 22), 'Stage overlay' if overlay.is_file() else 'Original input; overlay unavailable', fill=(55, 65, 81))
                tiles.append(card)
    sheet = Image.new('RGB', (840, 320*((len(tiles)+1)//2)), 'white')
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % 2)*420, (index//2)*320))
    path = output / 'contact-sheet.png'
    sheet.save(path)
    return {'path': path.name, 'sha256': sha(path)}


def hypothesis_sheet(receipt: dict, rows: list[dict], output: Path) -> dict | None:
    """Show every central hypothesis intersection, including empty alternatives."""
    case_by_id = {case['id']: case for case in receipt['cases']}
    tiles = []
    for saved in rows:
        stage = output / saved['attempt']
        if not (stage / 'report.json').is_file():
            continue
        result = json.loads((stage / 'report.json').read_bytes())
        photos = {photo['id']: photo for photo in case_by_id[saved['id']]['photos']}
        for photo in result.get('photos', []):
            with Image.open(photos[photo['id']]['path']) as original:
                base = original.convert('RGB')
            for region in photo.get('regions', []):
                folder = stage / photo['id'] / region['directory']
                masks = [('Prior; unverified identity', region['prior_mask'], (245, 146, 25))]
                for hypothesis in region.get('hypotheses', []):
                    stable = hypothesis['diagnostics']['minimum_matched_iou']
                    masks.append((f'H{hypothesis["index"]} intersection; minimum matched IoU {stable:.3f}',
                                  hypothesis['intersection'], (10, 200, 210)))
                while len(masks) < 4:
                    masks.append(('No inferred hypothesis', None, (10, 200, 210)))
                for label, descriptor, color in masks:
                    preview = base
                    if descriptor:
                        path = folder / descriptor['path']
                        if sha(path) != descriptor['sha256']:
                            raise ValueError('Mask changed before contact-sheet rendering')
                        with Image.open(path) as mask:
                            preview = Image.composite(Image.blend(base, Image.new('RGB', base.size, color), .45), base, mask.convert('L'))
                        label += f'; {descriptor["pixels"]} pixels'
                    card = Image.new('RGB', (420, 320), (240, 243, 247))
                    small = ImageOps.contain(preview, (404, 264))
                    card.paste(small, ((420-small.width)//2, 48+(264-small.height)//2))
                    draw = ImageDraw.Draw(card)
                    draw.text((8, 6), f'{saved["id"]}/{photo["id"]}/{region["id"]}', fill='black')
                    draw.text((8, 22), label, fill=(55, 65, 81))
                    tiles.append(card)
    if not tiles:
        return None
    sheet = Image.new('RGB', (1680, 320*((len(tiles)+3)//4)), 'white')
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % 4)*420, (index//4)*320))
    path = output / 'hypothesis-contact-sheet.png'
    sheet.save(path)
    return {'path': path.name, 'sha256': sha(path), 'tiles': len(tiles),
            'scope': 'Every region prior and all three matched-prompt intersections; no decoder alternative selected as truth'}


def execute(receipt: dict, output: Path) -> dict:
    from reconstruction.region_proposals import run_region_stage

    output = output.resolve()
    if implementation() != receipt['implementation']:
        raise ValueError('Implementation changed after preparation')
    previous_path = output / 'report.json'
    previous = json.loads(previous_path.read_bytes()) if previous_path.exists() else {}
    if previous and previous.get('corpus_receipt_sha256') != sha(output / 'corpus-receipt.json'):
        raise ValueError('Aggregate receipt mismatch')
    prior = {row['id']: row for row in previous.get('cases', [])}
    engine = None
    if receipt['engine']['kind'] != 'unavailable':
        from reconstruction.region_engine import OfflineSAM2RegionEngine
        verify_source(receipt['engine'])
        engine = OfflineSAM2RegionEngine(Path(receipt['engine']['path']), expected_sha256=receipt['engine']['sha256'],
                                         runtime_dir=output / 'engine-runtime')
    report = {'schema_version': 1, 'scope': receipt['scope'], 'quality_verdict': 'unmeasured', 'accepted': False,
              'corpus_receipt_sha256': sha(output / 'corpus-receipt.json'), 'engine': receipt['engine'],
              'created_at': datetime.now(timezone.utc).isoformat(), 'cases': []}
    for case in receipt['cases']:
        for source in [case['model'], case['refinement_report'], *case['photos']]:
            if source:
                verify_source(source)
        old = prior.get(case['id'])
        if old and old.get('execution_complete'):
            if not re.fullmatch(rf'cases/{re.escape(case["id"])}/attempt-[0-9]{{3,}}', old.get('attempt', '')):
                raise ValueError('Completed attempt path changed')
            if artifacts(output / old['attempt']) != old['artifacts']:
                raise ValueError(f'Completed case artifacts changed: {case["id"]}')
            stored = json.loads((output / old['attempt'] / 'report.json').read_bytes())
            if (stored.get('quality_verdict') != 'unmeasured' or stored.get('accepted') is not False
                    or summarize(stored) != old.get('summary')):
                raise ValueError('Completed case summary or quality claim changed')
            if stored.get('engine') != (engine.describe() if engine is not None else None):
                raise ValueError('Engine dependency identity changed since completed capture')
            report['cases'].append(old)
            print(f'Reused {case["id"]}', flush=True)
            continue
        folder = output / 'cases' / case['id']
        folder.mkdir(parents=True, exist_ok=True)
        attempt = folder / f'attempt-{1+len(list(folder.glob("attempt-*"))):03d}'
        while attempt.exists():
            attempt = folder / f'attempt-{int(attempt.name.split("-")[-1])+1:03d}'
        started = time.monotonic()
        row = {'id': case['id'], 'kind': case['kind'], 'attempt': attempt.relative_to(output).as_posix(), 'execution_complete': False}
        report['cases'].append(row)
        print(f'Running {case["id"]}: {len(case["photos"])} photos', flush=True)
        try:
            camera = json.loads(Path(case['refinement_report']['path']).read_bytes()) if case['refinement_report'] else None
            result = run_region_stage(case['photos'], attempt, engine=engine,
                                      model=Path(case['model']['path']) if case['model'] else None, refinement_report=camera)
            if result.get('quality_verdict') != 'unmeasured' or result.get('accepted') is not False:
                raise ValueError('Region hypotheses must not claim measured quality or acceptance')
            for source in [case['model'], case['refinement_report'], *case['photos']]:
                if source:
                    verify_source(source)
            row.update(summary=summarize(result), execution_complete=True, artifacts=artifacts(attempt))
        except Exception as error:
            row.update(error=f'{type(error).__name__}: {error}', artifacts=artifacts(attempt) if attempt.exists() else {})
        row['elapsed_seconds'] = time.monotonic()-started
        report['execution_complete'] = False
        atomic(previous_path, report)
        print(f'Finished {case["id"]}: {row.get("summary", {}).get("status", "error")} in {row["elapsed_seconds"]:.2f}s', flush=True)
    report['source_snapshot_stable'] = implementation() == receipt['implementation']
    report['execution_complete'] = all(row['execution_complete'] for row in report['cases']) and report['source_snapshot_stable']
    report['full_corpus_complete'] = report['execution_complete'] and set(row['id'] for row in report['cases']) == set(EXPECTED+CONTROLS)
    report['model_inference_complete'] = report['full_corpus_complete'] and engine is not None
    report['engine_execution_receipt'] = engine.receipt() if engine is not None else None
    report['status'] = 'experiment_completed' if report['execution_complete'] else 'incomplete_or_failed'
    report['contact_sheet'] = contact_sheet(receipt, report['cases'], output)
    report['hypothesis_contact_sheet'] = hypothesis_sheet(receipt, report['cases'], output)
    atomic(previous_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT/'data/refinement-corpus.json')
    parser.add_argument('--reports', type=Path, default=ROOT/'data/refinement/normal-evidence-v1')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--weights', type=Path)
    parser.add_argument('--weights-sha256')
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    receipt = prepare(args.manifest, args.reports, args.output, selected=args.case, weights=args.weights,
                      weights_sha256=args.weights_sha256, resume=args.resume)
    if args.prepare_only:
        print(json.dumps({'status': 'prepared', 'cases': len(receipt['cases']), 'quality_verdict': 'unmeasured', 'accepted': False}))
        return 0
    report = execute(receipt, args.output)
    print(json.dumps({key: report[key] for key in ('status', 'full_corpus_complete', 'model_inference_complete', 'quality_verdict', 'accepted')}))
    return 0 if report['execution_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
