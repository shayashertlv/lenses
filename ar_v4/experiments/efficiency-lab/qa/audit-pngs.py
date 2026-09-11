"""Independent artifact audit: no browser/renderer imports and no prior-file writes."""
import hashlib
import io
import json
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from audit_protocol import audit_renderer, summarize_mechanisms

workspace = Path.cwd()
report_path = Path(sys.argv[1]).resolve()
output_root = (workspace / 'experiments/efficiency-lab/qa/output').resolve()
assert report_path.is_relative_to(output_root), 'Audit output must remain in candidate QA output.'
report_bytes = report_path.read_bytes()
report = json.loads(report_bytes)
assert report['schema'] == 'ar-efficiency-lab-matched-v1'
assert report['complete'] and report['passed']
verified = {}


def read(artifact):
    filename = (workspace / artifact['path']).resolve()
    value = filename.read_bytes()
    digest = hashlib.sha256(value).hexdigest()
    assert digest == artifact['sha256'], filename
    if 'bytes' in artifact:
        assert len(value) == artifact['bytes'], filename
    verified[str(filename)] = {'path': str(filename), 'sha256': digest, 'bytes': len(value)}
    return value


def pixels(artifact):
    return np.array(Image.open(io.BytesIO(read(artifact))).convert('RGBA'))


base = json.loads(read(report['basePreservation']['manifest']))
assert report['basePreservation']['manifest']['sha256'] == '17e8e7543fd6ba3b4643232d53396ef9d6b574a32b29bca5681f3dc72c60364f'
assert base['schema'] == 'efficiency-lab-g-base-v1' and base['commit'] == report['basePreservation']['commit']
assert len(base['files']) == report['basePreservation']['filesVerified'] == 239
for item in base['files']:
    local = read(item)
    blob = subprocess.check_output(['git', 'show', base['commit'] + ':ar_v4/' + item['path']])
    assert len(blob) == item['gitBytes'] and hashlib.sha256(blob).hexdigest() == item['gitSHA256']
    assert local == blob or (item['representation'] == 'CRLF checkout / LF Git blob' and local.replace(b'\r\n', b'\n') == blob)
assert report['basePreservationAfter'] == report['basePreservation']

geometry_fields = ['eyewearModelId', 'rawMatrix', 'correctedMatrix', 'eyewearMatrix',
                   'surfacePositions', 'yawDegrees', 'occlusion', 'templeClip',
                   'templeVisibility', 'rearDrop', 'protection']
cases = []
mechanisms = []
for phase in report['phases']:
    phase_mechanisms = []
    previous = json.loads(read(phase['priorReport']))
    selected = report.get('scope', {}).get('selectedSource')
    assert bool(selected) == report.get('scope', {}).get('partialArchiveSelection', False)
    expected = {(row['id'], row['eyewearModel'], row['hairModel']): row for row in previous['cases']
                if not selected or row['id'] == selected}
    assert len(expected) == (4 if selected else 32 if phase['id'] == 'generated32' else 24)
    assert len(phase['cases']) == len(expected)
    for row in phase['cases']:
        key = row['id'], row['eyewearModel'], row['hairModel']
        prior = expected.pop(key)
        expected_before = pixels(prior.get('acceptedBefore', prior.get('before')))
        expected_after = pixels(prior.get('occludedAfter', prior.get('after')))
        background = pixels(prior['cleanBackground'])
        expected_geometry = json.loads(read(prior['geometry']))['current'] if phase['id'] == 'generated32' else prior['geometry']
        assert all(all(group.values()) for group in row['diagnosticChecks'].values()), (key, 'browser mechanism/identity guards')
        assert all(value['changedPixels'] == 0 and value['maxDelta'] == 0 for value in row['checks'].values()), (key, 'all pixel guards')
        assert all(all(group.values()) for group in row['geometryChecks'].values()), (key, 'browser geometry guards')
        outputs = {}
        for name in ['current', 'candidate']:
            before, after = pixels(row['pngs'][name + 'Before']), pixels(row['pngs'][name + 'After'])
            assert np.array_equal(before, expected_before), (key, name, 'accepted RGBA')
            assert np.array_equal(after, expected_after), (key, name, 'hair RGBA')
            assert hashlib.sha256(before.tobytes()).hexdigest() == row[name]['beforeRgbaSHA256'] == prior['beforeRgbaSHA256']
            assert hashlib.sha256(after.tobytes()).hexdigest() == row[name]['afterRgbaSHA256'] == prior['afterRgbaSHA256']
            geometry = row[name]['geometry']
            for field in geometry_fields:
                assert geometry[field] == expected_geometry[field], (key, name, field)
            height, width = before.shape[:2]
            assert (width, height) == (row['width'], row['height'])
            mechanism_result = audit_renderer(row, name, report['profileOptions'], width * height * 4,
                allow_async_fallback=report.get('allowAsyncFallback', False), phase_id=phase['id'],
                renderer=phase['environment']['renderer'])
            if name == 'candidate':
                mechanism = mechanism_result
                assert row['mechanism'] == mechanism, (key, 'reported fast path/fallback differs from independently checked raw metrics')
                phase_mechanisms.append({'mechanism': mechanism})
            allowed = np.zeros((height, width), dtype=bool)
            for rect in geometry['protection']['editableRects']:
                allowed[rect['y0']:rect['y1'], rect['x0']:rect['x1']] = True
            for rect in geometry['protection']['protectedRects'] + [geometry['hairPreview']['noseRoi']]:
                allowed[rect['y0']:rect['y1'], rect['x0']:rect['x1']] = False
            for historical in prior.get('historicalNoseChecks', []):
                x0, y0, x1, y1 = historical['roi']
                allowed[y0:y1, x0:x1] = False
            changed = np.any(before != after, axis=2)
            assert not np.any(changed & ~allowed), (key, name, 'optical/nasal/outside-arm guard')
            assert not np.any(changed & np.all(before == background, axis=2)), (key, name, 'clean background')
            assert not np.any(before[:, :, 3] != after[:, :, 3]), (key, name, 'alpha')
            assert int(changed.sum()) == row[name]['stats']['changedPixels']
            outputs[name] = {'acceptedExact': True, 'hairExact': True, 'geometryExact': True,
                             'protectedNoseOutsideBackgroundAlphaChanges': 0, 'changedPixels': int(changed.sum())}
        cases.append({'phase': phase['id'], 'id': key[0], 'eyewearModel': key[1], 'hairModel': key[2], 'mechanism': mechanism, **outputs})
        if report['profileOptions'].get('omitBranchLenses'):
            raw = row['rawBranch']
            required = row['candidate']['geometry']['rearDrop']['dropM'] != 0
            assert raw['compared'] == required
            if required:
                baseline_branch, candidate_branch = pixels(row['pngs']['currentBranch']), pixels(row['pngs']['candidateBranch'])
                assert baseline_branch.shape == candidate_branch.shape == before.shape
                assert hashlib.sha256(baseline_branch.tobytes()).hexdigest() == raw['currentRgbaSHA256']
                assert hashlib.sha256(candidate_branch.tobytes()).hexdigest() == raw['candidateRgbaSHA256']
                branch_changed = np.any(baseline_branch != candidate_branch, axis=2)
                protected = np.zeros((height, width), dtype=bool)
                for rect in row['candidate']['geometry']['protection']['protectedRects']:
                    protected[rect['y0']:rect['y1'], rect['x0']:rect['x1']] = True
                assert not np.any(branch_changed & ~protected), (key, 'raw branch lens differences outside optical protection')
                assert int(branch_changed.sum()) == raw['whole']['changedPixels']
                assert raw['outsideProtected']['changedPixels'] == raw['outsideProtected']['maxDelta'] == 0
                cases[-1]['rawBranchChangedPixels'] = int(branch_changed.sum())
            else:
                assert 'currentBranch' not in row['pngs'] and 'candidateBranch' not in row['pngs']
    assert not expected
    if report['profileOptions'].get('cropBranchReadback'):
        for eyewear in ['tom-ford-clear', 'amber-horizon']:
            for hair_model in ['hair-only', 'selfie-multiclass']:
                positive = []
                for row in phase['cases']:
                    if row['eyewearModel'] != eyewear or row['hairModel'] != hair_model:
                        continue
                    pipeline = row['candidate']['stats']['candidatePerformance']['nativePipeline']
                    region = pipeline['efficiencyLab']['branchRegion']
                    positive.append(region['used'] and region['rectangle'] is not None
                                    and 0 < region['readBytes'] < region['fullBytes']
                                    and pipeline['branchReadbackCalls'] == 1
                                    and pipeline['branchReadbackBytes'] == region['readBytes'])
                assert any(positive), (phase['id'], eyewear, hair_model, 'no positive cropped read path')
    if report['profileOptions'].get('omitBranchLenses'):
        for eyewear in ['tom-ford-clear', 'amber-horizon']:
            assert any(row['phase'] == phase['id'] and row['eyewearModel'] == eyewear and row.get('rawBranchChangedPixels', 0) > 0 for row in cases)
    assert phase['mechanismSummary'] == summarize_mechanisms(phase_mechanisms)
    mechanisms.extend(phase_mechanisms)

assert report['mechanismSummary'] == summarize_mechanisms(mechanisms)

for filename, digest in report['runtimeHashes'].items():
    assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == digest, filename
for item in report['frozenInputs']:
    assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']

receipt = {'schema': 'ar-efficiency-lab-independent-png-v1', 'complete': True, 'passed': True,
           'scope': report['scope'], 'profile': report['profile'], 'basePreservation': report['basePreservation'],
           'allowAsyncFallback': report.get('allowAsyncFallback', False), 'mechanismSummary': summarize_mechanisms(mechanisms),
           'createdAt': datetime.now(timezone.utc).isoformat(),
           'report': {'path': str(report_path), 'sha256': hashlib.sha256(report_bytes).hexdigest()},
           'verifiedFiles': list(verified.values()), 'cases': cases,
           'runtimeFilesReverified': len(report['runtimeHashes']), 'frozenInputsReverified': len(report['frozenInputs']),
           'limits': ['Exact archived/current/candidate still preservation; no physical motion, fit or speed claim.']}
target = report_path.parent / 'independent-png-audit.json'
with target.open('x', encoding='utf-8') as output:
    json.dump(receipt, output, indent=2)
print(json.dumps({'path': str(target), 'passed': True, 'cases': len(cases), 'verifiedFiles': len(verified)}))
