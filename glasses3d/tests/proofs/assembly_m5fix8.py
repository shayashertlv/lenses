"""$0 copy-only proof of comparison-bound visible facet angles (ASSEMBLY M5-fix-8).

Inventory/verification never opens credentials or runner launch specifications.
The complete local proof is driven through the production Lab tools on a new
copy; no source product package is used as a mutable Lab root.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import time

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_JOB = ROOT / 'glasses3d/data/jobs/prada-beige-86248315'
DEFAULT_STAGE = DEFAULT_JOB / 'work/temple_stage/a400ed17eaf07593'
DEFAULT_OUT = ROOT / 'glasses3d/data/assembly/m5-fix-8'
INVENTORY_SCHEMA = 'g3d.protected_originals/1'
ANGLES_DEG = (6.729716499, 11.729716499, 16.729716499)
SELECTED_DEG = ANGLES_DEG[1]
PHOTO_IDS = ('reference-01', 'reference-02', 'reference-05')
PROOF_REASON = ('Predeclared middle-angle instrument witness on a disposable copy; '
                'demonstrates the comparison-bound builder, not an owner-approved product choice.')


def excluded_secret(path: Path) -> bool:
    name = path.name.casefold()
    return (name.startswith('.env') or name.endswith('.spec.json') or
            name in {'credentials.json', 'secrets.json'} or path.suffix.casefold() in {'.pem', '.key'})


def sha256(path: Path) -> str:
    """Stream binary artifacts without interpreting or printing their contents."""
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def regular_files(root: Path):
    root = Path(root).resolve(strict=True)
    for path in sorted(root.rglob('*')):
        # Refuse links/junctions before traversed files can escape the protected tree.
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError(f'protected tree contains a link: {path.relative_to(root)}')
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise ValueError('protected asset escaped its root')
            yield path


def inventory(root: Path) -> dict:
    root = Path(root).resolve(strict=True)
    records, excluded = {}, []
    for path in regular_files(root):
        name = path.relative_to(root).as_posix()
        if excluded_secret(path):
            excluded.append({'path': name, 'reason': 'credential/runner specification: content not read'})
            continue
        before = path.stat()
        digest = sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f'asset changed while inventorying: {name}')
        records[name] = {'bytes': after.st_size, 'sha256': digest}
    return {'schema': INVENTORY_SCHEMA, 'root': str(root),
            'created_utc': datetime.now(timezone.utc).isoformat(), 'files': records,
            'excluded_without_reading': excluded, 'count': len(records),
            'bytes': sum(row['bytes'] for row in records.values())}


def verify_inventory(snapshot: dict) -> dict:
    if snapshot.get('schema') != INVENTORY_SCHEMA:
        raise ValueError('unsupported protected-originals inventory')
    current = inventory(Path(snapshot['root']))
    old, new = snapshot['files'], current['files']
    missing = sorted(old.keys() - new.keys())
    added = sorted(new.keys() - old.keys())
    changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
    excluded_changed = snapshot['excluded_without_reading'] != current['excluded_without_reading']
    report = {'status': 'passed' if not (missing or added or changed or excluded_changed) else 'failed',
              'checked_files': len(new), 'missing': missing, 'added': added, 'changed': changed,
              'excluded_paths_changed': excluded_changed,
              'excluded_without_reading': current['excluded_without_reading'],
              'checked_utc': current['created_utc']}
    if report['status'] != 'passed':
        raise ValueError('protected original assets changed: ' + json.dumps(report))
    return report


def copy_package(source: Path, destination: Path, *, protected_root: Path | None = None) -> dict:
    """Copy an entire selected stage and verify bytes; refuse overwrite/ancestor destinations."""
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    protected = Path(protected_root).resolve() if protected_root is not None else \
        (DEFAULT_JOB.resolve() if source.is_relative_to(DEFAULT_JOB.resolve()) else source)
    if destination.is_relative_to(protected):
        raise ValueError('proof copy must be outside the protected original job')
    if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('proof copy must be disjoint from the original stage')
    if destination.exists():
        raise ValueError('proof copy destination must not exist')
    before = inventory(source)
    destination.mkdir(parents=True)
    for name in before['files']:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        if sha256(target) != before['files'][name]['sha256']:
            raise ValueError('proof copy differs: ' + name)
    verify_inventory(before)
    return {'source': str(source), 'destination': str(destination), 'files': before['files'],
            'excluded_without_reading': before['excluded_without_reading'], 'source_unchanged': True}


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def json_body(text: str) -> dict:
    """Read the JSON before the genuine tool footer, without dropping the saved footer."""
    value, _ = json.JSONDecoder().raw_decode(text.lstrip())
    if not isinstance(value, dict):
        raise ValueError('tool output must begin with a JSON object')
    return value


class Invoker:
    """Exercise the real SDK wrapper and retain its bounded output and lossless report."""
    def __init__(self, lab, tools, out):
        self.lab, self.tools, self.out = lab, {tool.name: tool for tool in tools}, Path(out)
        self.calls = []

    def __call__(self, name, args):
        from agents.tool import ToolOutputImage, ToolOutputText
        from agents.tool_context import ToolContext
        from glasses3d.agent import tool_reports

        cid = f'm5fix8-proof-{len(self.calls)+1:03d}-{name}'
        raw = json.dumps(args)
        ctx = ToolContext(context=None, tool_name=name, tool_call_id=cid, tool_arguments=raw)
        started = time.perf_counter()
        print(json.dumps({'tool': name, 'call_id': cid, 'phase': 'start'}), flush=True)
        output = asyncio.run(self.tools[name].on_invoke_tool(ctx, raw))
        texts = [output] if isinstance(output, str) else [
            item.text for item in output if isinstance(item, ToolOutputText)]
        if not texts:
            raise ValueError(f'{name} returned no textual result')
        try:
            summary = json_body(texts[0])
        except ValueError:
            # SDK-handled runtime failures use ordinary text rather than our structured refusal protocol.
            # Preserve that real error before the proof rejects it, instead of masking it with a JSON error.
            summary = {'status': 'sdk_error', 'message': texts[0]}
        result, pages = summary, []
        if summary.get('paged'):
            offset = 0
            while True:
                page = tool_reports.read_report(self.lab.root, summary['full_report']['id'],
                                                mode='text', offset=offset, limit=8000)
                if page['status'] != 'ok':
                    raise ValueError('lossless tool report could not be read')
                pages.append(page['text'])
                if page['next_offset'] is None:
                    break
                if page['next_offset'] <= offset:
                    raise ValueError('lossless report pagination did not advance')
                offset = page['next_offset']
            result = json_body(''.join(pages))
        images = [hashlib.sha256(item.image_url.encode()).hexdigest()
                  for item in output if isinstance(item, ToolOutputImage)]
        self.calls.append({'tool': name, 'call_id': cid, 'arguments': args,
                           'seconds': time.perf_counter()-started, 'result': result,
                           'sdk_output_type': type(output).__name__,
                           'bounded_text': texts, 'output_image_count': len(images),
                           'output_image_data_url_sha256': images, 'lossless_pages': len(pages)})
        write_json(self.out/'tool-calls.json', self.calls)
        print(json.dumps({'tool': name, 'call_id': cid, 'phase': 'end',
                          'status': result.get('status'), 'images': len(images)}), flush=True)
        return result


def bounded_comparison_metrics(record: dict, call: dict) -> dict:
    """Require all candidate numbers in the emitted SDK text, independently of its lossless report."""
    from glasses3d.agent.tool_reports import TEXT_LIMIT
    texts = call['bounded_text']
    characters = sum(map(len, texts))
    if characters > TEXT_LIMIT:
        raise AssertionError('actual SDK output exceeds its aggregate text cap')
    body = json_body(texts[0])
    shown, full = body.get('summary'), record.get('summary')
    if not isinstance(shown, dict) or not isinstance(full, dict):
        raise AssertionError('bounded SDK output has no direct comparison summary')
    if not isinstance(shown.get('baseline'), str) or shown['baseline'] != full.get('baseline'):
        raise AssertionError('bounded SDK output lost the honest baseline measurement label')
    exact = {}
    for key, value in full.items():
        if not key.startswith('exact_requested_degrees_'):
            continue
        if not isinstance(shown.get(key), str) or shown[key] != value:
            raise AssertionError('bounded SDK output lost the exact requested angles')
        for entry in shown[key].split('; '):
            label, degrees = entry.split('=', 1)
            if label in exact:
                raise AssertionError('bounded SDK output duplicates an exact requested angle')
            exact[label] = float(degrees)
    pattern = re.compile(r'built/requested ([^/]+)/([^°]+)° ([^;]+); wall([^;]+)mm; '
        r'localΔχ²([^;]+); max\|r\|([^;]+)sd; moved ([^;]+); surfaces([^;]+); /alternatives/(\d+)')
    def number_matches(actual, expected):
        if expected is None:
            return actual == 'n/a'
        try:
            return math.isclose(float(actual), float(expected), rel_tol=.005, abs_tol=1e-12)
        except ValueError:
            return False
    verified = []
    for index, candidate in enumerate(record['alternatives']):
        label = candidate['label']
        line = shown.get(label)
        if not isinstance(line, str) or line != full.get(label):
            raise AssertionError(f'bounded SDK output lost or shortened {label} metrics')
        matched = pattern.fullmatch(line)
        if matched is None:
            raise AssertionError(f'bounded SDK output omitted a required metric for {label}')
        built, requested, status, wall, cost, residual, moved, surfaces, pointer = matched.groups()
        meta = (candidate.get('fit') or {}).get('model', {}).get('visible_angles', {})
        residuals = [abs(r['residual_sd']) for choice in meta.get('choices', [])
                     for r in choice.get('reading_residuals', {}).values() if r.get('residual_sd') is not None]
        expected_surfaces = candidate.get('affected_geometry', {}).get('surfaces')
        expected_surfaces = None if expected_surfaces is None else sum(not s['unchanged'] for s in expected_surfaces)
        checks = ((built, candidate.get('built_degrees')), (requested, candidate['degrees']),
                  (wall, candidate.get('thickness', {}).get('min_mm')),
                  (cost, meta.get('local_cost_change')), (residual, max(residuals) if residuals else None),
                  (surfaces, expected_surfaces))
        if (status != candidate['status'] or int(pointer) != index or not moved or
                exact.get(label) != candidate['degrees'] or
                not all(number_matches(actual, expected) for actual, expected in checks)):
            raise AssertionError(f'bounded SDK output misstates a candidate metric for {label}')
        verified.append({'label': label, 'line': line, 'exact_requested_degrees': exact[label],
                         'all_metrics_visible': True})
    return {'status': 'passed', 'aggregate_text_characters': characters, 'text_limit': TEXT_LIMIT,
            'baseline': shown['baseline'], 'candidates': verified,
            'candidate_count': len(verified), 'read_report_required_for_these_metrics': False}


def proof_judgement(lab):
    """Acknowledge an actual pending comparison through the ordinary procedure, never clear state."""
    from glasses3d.agent.temple_lab import state as S
    pending = S.pending_compare(lab.state)
    if pending is None:
        return None
    return {'compare_id': pending['compare_id'], 'versus_compare_id': None, 'overall': 'same',
            'best_checkpoint': lab.s.head['id'], 'photos': [
                {'photo': pid, 'verdict': 'same', 'differences': [
                    {'what': 'Copy-only tool proof: hinge-side fore|logo fold remains the named visual target; '
                             'this procedural judgement is not product acceptance.', 'd_mm': 10., 'y_mm': 0.}]}
                for pid in pending['photos']]}


def certain_conflict_control() -> dict:
    """Analytic 10-degree valley with precise readings must refuse the opposite chosen ridge."""
    import math
    from glasses3d.g3d.temple import fit as F, fixtures as FX, spec as S

    rise = 8*math.tan(math.radians(5))
    points = {'P': (20., 0., 68.), 'Q': (60., 0., 68.),
              'D': (40., -8., 68.+rise), 'U': (40., 8., 68.+rise)}
    design = FX.straight(nodes={n: [d, y, {'read': {'id': n, 'X': x, 'sd': .05}}]
                                for n, (d, y, x) in points.items()},
                         facets={'lower': ['P', 'D', 'Q'], 'upper': ['P', 'Q', 'U']},
                         base={'top': [[0., 8.], [120., 8.]], 'bottom': [[0., 8.], [120., 8.]]})
    baseline = F.fit_spec(design)
    row = next(c for c in baseline.creases if c['name'] == 'lower|upper')
    if row['state'] != 'certain' or row['shape'] != 'valley':
        raise AssertionError('independent negative control is not the predeclared sign-certain valley')
    declared = copy.deepcopy(design)
    declared['creases'] = [{'facets': ['lower', 'upper'], 'shape': 'ridge', 'visible_angle': {
        'degrees': 12., 'moving_facet': 'lower', 'comparison_id': 'synthetic-provenance-not-a-real-comparison',
        'comparison_sha256': 'a'*64, 'reason': 'Opposite signed synthetic refusal control.'}}]
    try:
        F.fit_spec(declared)
    except S.TempleError as exc:
        if exc.code != 'E_FIT_SHAPE':
            raise
        return {'status': 'passed', 'baseline': row, 'requested_shape': 'ridge', 'requested_degrees': 12.,
                'refusal': exc.to_dict(), 'analytic_truth': 'two planes, 10 degree valley; reading sd 0.05 mm'}
    raise AssertionError('sign-certain conflict was accepted')


def independent_dihedral(tb, pair) -> float:
    """Normal-vector angle from the actual built polygons, independent of the receipt's number."""
    import math
    import numpy as np
    normals = []
    for region in pair:
        index = next(i for i, r in enumerate(tb.poly_region)
                     if r == region and tb.poly_kind[i] == 'outer')
        points = tb.V[tb.polys[index]]
        normal = np.cross(points[1]-points[0], points[2]-points[0])
        normal = normal/np.linalg.norm(normal)
        normals.append(normal)
    return math.degrees(math.acos(float(np.clip(np.dot(*normals), -1., 1.))))


def triptychs(record, out):
    """Copy native pixels into labelled proof sheets; no colour, light or geometry modification."""
    from PIL import Image, ImageDraw
    chosen = next(c for c in record['alternatives'] if c['degrees'] == SELECTED_DEG)
    result = []
    for row in record['photos']:
        short_id = row['photo'].removeprefix('reference-')
        if short_id not in ('02', '05') or not row.get('box'):
            continue
        baseline_path = next(i['path'] for i in record['baseline']['capture']['images']
                             if i['photo'] == row['photo'])
        chosen_path = next(i['path'] for i in chosen['capture']['images'] if i['photo'] == row['photo'])
        box = row['box']
        panels = []
        for path in (row['photo_path'], baseline_path, chosen_path):
            with Image.open(path) as image:
                panels.append(image.convert('RGB').crop(box))
        w, h = panels[0].size
        sheet = Image.new('RGB', (3*w, h+92), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        draw.text((5, 3), f'Photo {short_id} | $0 tool proof copy', fill='black')
        draw.text((5, 19), 'Not a delivery or a product approval', fill='black')
        draw.text((5, 35), 'Same camera, materials and AR room light', fill='black')
        labels = ('Original photo', 'Current\n1.73 degrees', 'Builder proof\n11.73 degrees')
        for col, (label, pixels) in enumerate(zip(labels, panels)):
            draw.multiline_text((col*w+5, 57), label, fill='black', spacing=2)
            sheet.paste(pixels, (col*w, 92))
        path = Path(out)/f'photo-{short_id}-proof-triptych.png'
        sheet.save(path)
        result.append({'photo': row['photo'], 'path': str(path), 'sha256': sha256(path),
                       'native_box': box, 'source_images': [row['photo_path'], baseline_path, chosen_path],
                       'source_sha256': [sha256(Path(p)) for p in [row['photo_path'], baseline_path, chosen_path]],
                       'labels': list(labels)})
    if {r['photo'] for r in result} != {'reference-02', 'reference-05'}:
        raise AssertionError('requested 02 and 05 proof triptychs are missing')
    return result


def run_copy_proof(out, source=DEFAULT_STAGE, protected_inventory=None):
    from glasses3d.agent.temple_lab import stage
    from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
    from glasses3d.agent.procs import DescendantsOptOut

    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    baseline = json.loads(Path(protected_inventory or DEFAULT_OUT/'protected-originals.json').read_text('utf-8'))
    verify_inventory(baseline)
    copied = copy_package(source, out/'stage-copy', protected_root=Path(baseline['root']))
    write_json(out/'copy-receipt.json', copied)
    write_json(out/'predeclared.json', {'angles_deg': ANGLES_DEG, 'selected_deg': SELECTED_DEG,
        'photos': PHOTO_IDS, 'crease': ['fore', 'logo'], 'moving_facet': 'fore', 'shape': 'ridge',
        'selection_reason': PROOF_REASON, 'no_llm': True, 'paid_calls': 0,
        'source_inventory': str(protected_inventory or DEFAULT_OUT/'protected-originals.json')})
    lab = stage.StageLab(out/'stage-copy')
    report = {'schema': 'g3d.assembly_m5fix8_proof/1', 'status': 'running', 'started_utc':
              datetime.now(timezone.utc).isoformat(), 'paid_calls': 0, 'no_llm': True,
              'copy_root': str(lab.root), 'selection_reason': PROOF_REASON}
    source_files = ['agent/loop.py', 'agent/temple_lab/lab.py', 'agent/temple_lab/tools.py',
                    'agent/temple_lab/stage.py', 'agent/temple_lab/crease_angles.py',
                    'agent/temple_lab/crease_ar.py', 'agent/tool_reports.py', 'g3d/temple/fit.py',
                    'g3d/temple/spec.py', 'g3d/temple/build.py', 'g3d/temple/report.py', 'g3d/worker.py',
                    'tests/proofs/assembly_m5fix8.py']
    report['implementation_sha256'] = {name: sha256(ROOT/'glasses3d'/name) for name in source_files}
    disable_sdk_tracing()
    with DescendantsOptOut(), LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        try:
            lab.verify_inputs()
            lab.s.start()
            report['initial_head'] = dict(lab.s.head)
            before = lab.s.fingerprint()
            if before != report['initial_head']['fingerprint']:
                raise AssertionError('copy did not open at its verified original checkpoint')
            invoke = Invoker(lab, stage.registry(lab, stage.settings()), out)
            parent = lab.head_record()['revision']
            report['parent_revision'] = parent
            from glasses3d.g3d.temple import fit as F
            baseline_crease = next(c for c in lab.fit_result(parent).creases if c['name'] == 'fore|logo')
            report['baseline_crease_label'] = F._crease_line(baseline_crease)
            if 'unresolved' not in report['baseline_crease_label'] or 'flat' in report['baseline_crease_label']:
                raise AssertionError('the unresolved baseline was not labelled honestly')
            comparison = invoke('facet_crease_compare', {'parent_revision': parent,
                'facets': ['fore', 'logo'], 'moving_facet': 'fore', 'shape': 'ridge',
                'angles_deg': list(ANGLES_DEG), 'photos': list(PHOTO_IDS),
                'judgement': proof_judgement(lab)})
            if comparison.get('status') != 'ok':
                raise AssertionError('comparison refused: '+json.dumps(comparison))
            report['bounded_candidate_metrics'] = bounded_comparison_metrics(comparison, invoke.calls[-1])
            if [c['degrees'] for c in comparison['alternatives']] != list(ANGLES_DEG):
                raise AssertionError('predeclared alternative was dropped or reordered')
            if lab.s.fingerprint() != before:
                raise AssertionError('read-only comparison changed the baseline scene')
            report['comparison_head_unchanged'] = True
            report['comparison'] = comparison
            report['certain_conflict'] = certain_conflict_control()
            cid = comparison['comparison_id']
            saved = lab.state['crease_comparisons'][cid]
            if not saved.get('delivered') or saved.get('unseen'):
                raise AssertionError('real wrapper did not deliver the comparison')
            common = {'reason': PROOF_REASON, 'judgement': None, 'apply': False, 'photos': list(PHOTO_IDS)}
            negatives = []
            for label, args, code in [
                ('missing_or_unseen_comparison', {'comparison_id': cid+'-never-produced', 'degrees': SELECTED_DEG},
                 'E_COMPARISON_UNSEEN'),
                ('angle_not_compared', {'comparison_id': cid, 'degrees': SELECTED_DEG+.125}, 'E_COMPARISON_CHOICE')]:
                result = invoke('facet_visible_angle', {**common, **args})
                if result.get('status') != 'refused' or result.get('code') != code:
                    raise AssertionError(f'{label}: expected {code}, got {result}')
                negatives.append({'control': label, 'result': result})
            # Change a bound corner's real numeric reading on this disposable copy, not an unrelated store key.
            old_readings = copy.deepcopy(lab.state.get('readings'))
            bound_reading = next(iter(sorted(comparison['choice_dependencies']['readings'])))
            try:
                lab.state['readings'][bound_reading]['X'] += .125
                stale = invoke('facet_visible_angle', {**common, 'comparison_id': cid, 'degrees': SELECTED_DEG})
            finally:
                lab.state['readings'] = old_readings
                lab.save()
            if stale.get('status') != 'refused' or stale.get('code') != 'E_COMPARISON_STALE':
                raise AssertionError('changed bound reading did not invalidate the comparison')
            negatives.append({'control': 'changed_bound_reading_on_copy_only', 'reading': bound_reading, 'result': stale})
            report['negative_controls'] = negatives
            selected = next(c for c in comparison['alternatives'] if c['degrees'] == SELECTED_DEG)
            if selected.get('status') != 'eligible' or selected['capture']['spec'].get('ok') is not True:
                raise AssertionError('predeclared middle candidate did not pass all checks; no replacement is allowed')
            selected_visibility = {r['photo']: r.get('visible_pixels_by_variant', {}).get(selected['label'], 0)
                                   for r in comparison['photos']}
            if not any(selected_visibility.values()):
                raise AssertionError('the selected candidate has no actual first-hit face pixels in its comparisons')
            report['selected_visibility_pixels'] = selected_visibility
            report['triptychs'] = triptychs(comparison, out)
            applied = invoke('facet_visible_angle', {'comparison_id': cid, 'degrees': SELECTED_DEG,
                'reason': PROOF_REASON, 'judgement': proof_judgement(lab), 'apply': True, 'photos': list(PHOTO_IDS)})
            if applied.get('status') != 'applied':
                raise AssertionError('visible angle application failed: '+json.dumps(applied))
            rid = lab.head_record()['revision']
            from glasses3d.agent.temple_lab.crease_angles import validate_design
            try:
                lab.state['readings']['proof-only-unrelated-sentinel'] = {'proof': True}
                validate_design(lab, lab.design(rid))
                report['retained_choice_survives_unrelated_reading'] = True
            finally:
                del lab.state['readings']['proof-only-unrelated-sentinel']
                lab.save()
            tb = lab.build(rid)
            chosen_crease = next(c for c in lab.fit_result(rid).creases if c['name'] == 'fore|logo')
            report['selected_crease_label'] = F._crease_line(chosen_crease)
            actual = independent_dihedral(tb, ['fore', 'logo'])
            if abs(actual-SELECTED_DEG) > 1e-5:
                raise AssertionError('actual selected builder geometry does not have the declared angle')
            if not tb.checks['closed'] or tb.checks['euler'] != 2:
                raise AssertionError('selected geometry is not a closed solid')
            if tb.tables['thickness']['min_mm'] < tb.spec.options['t_min_mm']:
                raise AssertionError('selected geometry violates thickness')
            report.update(status='passed', applied=applied, selected_revision=rid,
                          selected_dihedral_degrees_independent=actual,
                          final_head=dict(lab.s.head), hard_checks=tb.checks, thickness=tb.tables['thickness'])
        except Exception as exc:
            report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            lab.s.close()
            report['network_attempts'] = network.attempts
            report['production_transport_attempts'] = clients.constructions
            report['protected_originals'] = verify_inventory(baseline)
            report['ended_utc'] = datetime.now(timezone.utc).isoformat()
            write_json(out/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', action='store_true')
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--source-stage', type=Path, default=DEFAULT_STAGE)
    parser.add_argument('--job', type=Path, default=DEFAULT_JOB)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.run:
        result = run_copy_proof(args.out, args.source_stage)
        print(json.dumps({'status': result['status'], 'report': str(args.out/'report.json')}), flush=True)
        return 0
    if args.inventory:
        path = args.out / 'protected-originals.json'
        if path.exists():
            parser.error('fresh inventory already exists; never overwrite the initial protected baseline')
        snapshot = inventory(args.job)
        write_json(path, snapshot)
        print(json.dumps({'inventory': str(path), 'files': snapshot['count'], 'bytes': snapshot['bytes'],
                          'excluded_without_reading': snapshot['excluded_without_reading']}), flush=True)
        return 0
    if args.verify:
        snapshot = json.loads(args.verify.read_text(encoding='utf-8'))
        result = verify_inventory(snapshot)
        write_json(args.out / 'protected-originals-check.json', result)
        print(json.dumps(result), flush=True)
        return 0
    parser.error('use --inventory, --verify or --run')


if __name__ == '__main__':
    raise SystemExit(main())
