"""Independent artifact audit: no browser/renderer imports and no prior-file writes."""
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from audit_protocol import audit_async_readback, summarize_mechanisms

workspace = Path.cwd()
report_path = Path(sys.argv[1]).resolve()
output_root = (workspace / 'experiments/speed-lab/qa/output').resolve()
assert report_path.is_relative_to(output_root), 'Audit output must remain in candidate QA output.'
report_bytes = report_path.read_bytes()
report = json.loads(report_bytes)
assert report['schema'] == 'ar-speed-lab-matched-v1'
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
assert report['basePreservation']['manifest']['sha256'] == '527c94adedbd1fb11ae1159fc1414c90d9a875bd70d248661d428cf446ecd990'
assert base['schema'] == 'speed-lab-base-v1' and base['commit'] == report['basePreservation']['commit']
assert len(base['files']) == report['basePreservation']['filesVerified'] == 186
for item in base['files']:
    read(item)
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
            perf = row[name]['stats']['candidatePerformance']
            pipeline, byte_count = perf['nativePipeline'], width * height * 4
            native = pipeline['native']
            assert native['sharedCameraReady'] and native['sharedCameraFailure'] is None
            branch_calls = int(geometry['rearDrop']['dropM'] != 0)
            assert pipeline['branchReadbackCalls'] == branch_calls
            assert pipeline['branchReadbackBytes'] == branch_calls * byte_count
            assert pipeline['baselineReadbackCalls'] == 0
            shared = native.get('sharedReadback') or {}
            pbo = (native.get('speedLab') or {}).get('pbo') or {}
            warm = pipeline.get('speedLab') or {}
            assert perf['cpuReadbackCalls'] == branch_calls + shared.get('readbackCalls', 0) + pbo.get('retrievedCalls', 0) + warm.get('prewarmReadbackCalls', 0)
            assert perf['cpuReadbackBytes'] == branch_calls * byte_count + shared.get('readbackBytes', 0) + pbo.get('retrievedBytes', 0) + warm.get('prewarmReadbackBytes', 0)
            if name == 'current':
                assert shared['readbackCalls'] == 2 and shared['readbackBytes'] == 2 * byte_count
            else:
                options, speed = report['profileOptions'], native['speedLab']
                calls = 1 if options['reuseSourcePixels'] else 2
                assert perf['speedLab']['options'] == options
                assert speed['reuseSourcePixelsUsed'] == options['reuseSourcePixels'] and speed['sourceReuseFallback'] is None
                mechanism = audit_async_readback(options, native, byte_count,
                    allow_async_fallback=report.get('allowAsyncFallback', False), phase_id=phase['id'],
                    renderer=phase['environment']['renderer'])
                assert perf['speedLab']['sourceCanvasBorrowed'] == options['fewerCopies']
                assert speed['reuseSourcePixelsRequested'] == options['reuseSourcePixels']
                assert perf['speedLab']['sourceCopyBytesAvoided'] == int(options['fewerCopies']) * byte_count
                assert warm['sourceCopiesAvoided'] == int(options['fewerCopies'])
                if options['reuseSourcePixels'] or options['fewerCopies']:
                    assert perf['speedLab']['sourceIdentity']['sourceSHA256'] == geometry['hairPreview']['sourceSHA256']
                    assert perf['speedLab']['sourceFallbackReason'] is None
                if options['reuseSourcePixels']:
                    assert native['cleanSubmitMs'] == 0
                if options['prewarmTemples']:
                    assert warm['prewarmCompleted'] and warm['prewarmFailure'] is None
                assert warm['prewarmRequested'] == options['prewarmTemples']
                assert warm['prewarmReadbackCalls'] == int(warm['prewarmAttempted'])
                assert warm['prewarmReadbackBytes'] == int(warm['prewarmAttempted']) * byte_count
                assert row['mechanism'] == mechanism, (key, 'reported actual fast path/fallback differs from raw metrics')
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
    assert not expected
    assert phase['mechanismSummary'] == summarize_mechanisms(phase_mechanisms)
    mechanisms.extend(phase_mechanisms)

assert report['mechanismSummary'] == summarize_mechanisms(mechanisms)

for filename, digest in report['runtimeHashes'].items():
    assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == digest, filename
for item in report['frozenInputs']:
    assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']

receipt = {'schema': 'ar-speed-lab-independent-png-v1', 'complete': True, 'passed': True,
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
