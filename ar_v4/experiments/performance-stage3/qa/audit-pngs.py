"""Independent artifact audit: no browser/renderer imports and no prior-file writes."""
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

workspace = Path.cwd()
report_path = Path(sys.argv[1]).resolve()
output_root = (workspace / 'experiments/performance-stage3/qa/output').resolve()
assert report_path.is_relative_to(output_root), 'Audit output must remain in candidate QA output.'
report_bytes = report_path.read_bytes()
report = json.loads(report_bytes)
assert report['schema'] == 'ar-performance-stage3-matched-v1'
assert not report.get('diagnosticNativeCaptureProbe'), 'Instrumented capture reads can mask synchronization failures; this is not an ordinary matched pass.'
assert report['complete'] and report['passed']
verified = {}
preservation = report['preservationAfter']
assert preservation['uniqueFiles'] == 175
manifest_bytes = Path(preservation['manifest']['path']).read_bytes()
assert hashlib.sha256(manifest_bytes).hexdigest() == preservation['manifest']['sha256'] == report['preservationBefore']['manifest']['sha256']
preserved = json.loads(manifest_bytes)
assert len(preserved['files']) == 175
for item in preserved['files']:
    data = Path(item['path']).read_bytes()
    assert len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256'], item['path']


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


geometry_fields = ['eyewearModelId', 'rawMatrix', 'correctedMatrix', 'eyewearMatrix',
                   'surfacePositions', 'yawDegrees', 'occlusion', 'templeClip',
                   'templeVisibility', 'rearDrop', 'protection']
cases = []
for phase in report['phases']:
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
        expected_mechanism = 'gpu-zero-drop' if expected_geometry['rearDrop']['dropM'] == 0 else 'cpu-nonzero-drop'
        assert row['expectedMechanism'] == expected_mechanism
        pre = row['candidate']['preExportStats']['candidatePerformance']
        stages, metrics = pre['nativePipeline'], pre['gpuTransferMetrics']
        if expected_mechanism == 'gpu-zero-drop':
            assert pre['gpuCompositorUsed'] is True and pre['gpuFallbackReason'] is None
            assert stages['zeroDropBranchSkipped'] is True
            assert stages['baselineReadbackCalls'] == stages['branchReadbackCalls'] == 0
            assert metrics['fullFrameReadbackCalls'] == metrics['fullFrameReadbackBytes'] == 0
            assert metrics['captureCalls'] == 2 and metrics['cameraCaptureCalls'] == metrics['beautyCaptureCalls'] == 1
            assert metrics['compactReadbackCalls'] == metrics['readbackCalls'] == 3
            assert metrics['compactReadbackBytes'] == metrics['readbackBytes'] == ((row['width'] + 3) // 4) * row['height'] * 4 + 8
            post = row['candidate']['stats']['candidatePerformance']['gpuTransferMetrics']
            assert post['fullFrameReadbackCalls'] == 3 and post['fullFrameReadbackBytes'] == row['width'] * row['height'] * 4 * 3
        else:
            assert pre['gpuCompositorUsed'] is False and metrics is None
            assert pre['gpuFallbackReason'] == 'This pair uses the exact CPU fallback path.'
            assert stages['zeroDropBranchSkipped'] is False and stages['branchReadbackCalls'] == 1
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
        cases.append({'phase': phase['id'], 'id': key[0], 'eyewearModel': key[1], 'hairModel': key[2],
                      'expectedMechanism': expected_mechanism, 'mechanismVerified': True, **outputs})
    assert not expected

for filename, digest in report['runtimeHashes'].items():
    assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == digest, filename
for item in report['frozenInputs']:
    assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']

receipt = {'schema': 'ar-performance-stage3-independent-png-v1', 'complete': True, 'passed': True,
           'preservedPreviousFilesReverified': len(preserved['files']),
           'scope': report['scope'],
           'createdAt': datetime.now(timezone.utc).isoformat(),
           'report': {'path': str(report_path), 'sha256': hashlib.sha256(report_bytes).hexdigest()},
           'verifiedFiles': list(verified.values()), 'cases': cases,
           'gpuZeroDropCases': sum(row['expectedMechanism'] == 'gpu-zero-drop' for row in cases),
           'cpuNonzeroDropCases': sum(row['expectedMechanism'] == 'cpu-nonzero-drop' for row in cases),
           'runtimeFilesReverified': len(report['runtimeHashes']), 'frozenInputsReverified': len(report['frozenInputs']),
           'limits': ['Exact archived/current/candidate still preservation; no physical motion, fit or speed claim.']}
target = report_path.parent / 'independent-png-audit.json'
with target.open('x', encoding='utf-8') as output:
    json.dump(receipt, output, indent=2)
print(json.dumps({'path': str(target), 'passed': True, 'cases': len(cases), 'verifiedFiles': len(verified)}))
