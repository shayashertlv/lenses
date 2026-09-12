"""Independent native-readback receipt validation, without JS or renderer imports."""
import math

BOUNDED_FENCE_REASON = 'The native readback fence exceeded its bounded wait.'
PREWARM_REASON = 'First valid pair retains the completed synchronous temple prewarm.'


def expected_branch_region(geometry, width, height):
    """Independent interval coverage audit; imports no candidate helper."""
    protection = geometry['protection']
    first, last = height, 0
    for y in range(height):
        protected = sorted((max(0, r['x0']), min(width, r['x1'])) for r in protection['protectedRects']
                           if r['y0'] <= y < r['y1'])
        eligible = False
        for rect in protection['editableRects']:
            if not rect['y0'] <= y < rect['y1']:
                continue
            start, end = max(0, rect['x0']), min(width, rect['x1'])
            cursor = start
            for lo, hi in protected:
                if hi <= cursor:
                    continue
                if lo > cursor:
                    break
                cursor = max(cursor, hi)
            if cursor < end:
                eligible = True
                break
        if eligible:
            first, last = min(first, y), y + 1
    return {'x0': 0, 'y0': first, 'x1': width, 'y1': last} if first < last else None


def audit_renderer(row, name, candidate_options, byte_count, *, allow_async_fallback=False, phase_id=None, renderer=None):
    perf = row[name]['stats']['candidatePerformance']
    pipeline = perf['nativePipeline']
    native, warm = pipeline['native'], pipeline['speedLab']
    options = candidate_options if name == 'candidate' else {key: True for key in
        ['reuseSourcePixels', 'fewerCopies', 'asyncReadback', 'prewarmTemples']}
    geometry = row[name]['geometry']
    assert native['sharedCameraReady'] and native['sharedCameraFailure'] is None
    assert pipeline['baselineReadbackCalls'] == pipeline['baselineReadbackBytes'] == 0
    efficiency = pipeline.get('efficiencyLab') or {}
    branch = efficiency.get('branchPbo') or {}
    beauty = native['speedLab'].get('pbo') or {}
    shared = native.get('sharedReadback') or {}
    branch_calls = int(geometry['rearDrop']['dropM'] != 0)
    region = efficiency.get('branchRegion') or {}
    rectangle = expected_branch_region(geometry, row['width'], row['height']) if options.get('cropBranchReadback') and branch_calls else None
    expected_calls = int(rectangle is not None) if options.get('cropBranchReadback') and branch_calls else branch_calls
    expected_bytes = row['width'] * (rectangle['y1'] - rectangle['y0']) * 4 if rectangle else 0
    if not options.get('cropBranchReadback'):
        expected_bytes = branch_calls * byte_count
    assert pipeline['branchReadbackCalls'] + branch.get('retrievedCalls', 0) == expected_calls
    assert pipeline['branchReadbackBytes'] + branch.get('retrievedBytes', 0) == expected_bytes
    assert perf['cpuReadbackCalls'] == pipeline['branchReadbackCalls'] + branch.get('retrievedCalls', 0) + shared.get('readbackCalls', 0) + beauty.get('retrievedCalls', 0) + warm['prewarmReadbackCalls']
    assert perf['cpuReadbackBytes'] == pipeline['branchReadbackBytes'] + branch.get('retrievedBytes', 0) + shared.get('readbackBytes', 0) + beauty.get('retrievedBytes', 0) + warm['prewarmReadbackBytes']
    assert perf['speedLab']['options'] == options
    speed = native['speedLab']
    assert speed['reuseSourcePixelsRequested'] == speed['reuseSourcePixelsUsed'] == options['reuseSourcePixels']
    assert speed['sourceReuseFallback'] is None and native['cleanSubmitMs'] == 0
    assert perf['speedLab']['sourceCanvasBorrowed'] == options['fewerCopies']
    assert perf['speedLab']['sourceCopyBytesAvoided'] == int(options['fewerCopies']) * byte_count
    assert warm['sourceCopiesAvoided'] == int(options['fewerCopies'])
    assert perf['speedLab']['sourceIdentity']['sourceSHA256'] == geometry['hairPreview']['sourceSHA256']
    assert perf['speedLab']['sourceFallbackReason'] is None
    assert warm['prewarmRequested'] == options['prewarmTemples']
    assert warm['prewarmCompleted'] and warm['prewarmFailure'] is None
    assert warm['prewarmReadbackCalls'] == int(warm['prewarmAttempted'])
    assert warm['prewarmReadbackBytes'] == int(warm['prewarmAttempted']) * byte_count
    result = audit_async_readback(options, native, byte_count,
        allow_async_fallback=allow_async_fallback, phase_id=phase_id, renderer=renderer)
    if name == 'current':
        return result
    assert efficiency['asyncTemplesRequested'] == options['asyncTemples']
    accepted_branch_fallback = False
    if not options['asyncTemples'] or not branch_calls or warm['prewarmAttempted']:
        assert efficiency['asyncTemplesUsed'] is False and not branch
        assert efficiency['branchSubmittedBeforeBeautyAwait'] is False
        assert efficiency['branchFallback'] == (PREWARM_REASON if options['asyncTemples'] and warm['prewarmAttempted'] else None)
    elif efficiency['asyncTemplesUsed']:
        assert efficiency['branchSubmittedBeforeBeautyAwait'] is True and efficiency['branchFallback'] is None
        assert pipeline['branchReadbackCalls'] == pipeline['branchReadbackBytes'] == 0
        assert branch['completed'] is True and branch['fallbackReason'] is None
        assert branch['queuedCalls'] == branch['retrievedCalls'] == 1
        assert branch['queuedBytes'] == branch['retrievedBytes'] == byte_count
    else:
        assert allow_async_fallback and phase_id == 'recorded24' and 'SwiftShader' in renderer
        assert efficiency['branchFallback'] == branch['fallbackReason'] == BOUNDED_FENCE_REASON
        assert branch['completed'] is False and branch['queuedCalls'] == 1 and branch['queuedBytes'] == byte_count
        assert branch['retrievedCalls'] == branch['retrievedBytes'] == 0
        assert branch['waitMs'] >= 500 and branch['polls'] > 0
        assert pipeline['branchReadbackCalls'] == 1 and pipeline['branchReadbackBytes'] == byte_count
        accepted_branch_fallback = True
    for pbo in [beauty, branch]:
        if pbo.get('retrievedCalls', 0):
            assert pbo['scratchBytesAllocated'] + pbo['scratchBytesReused'] == pbo['retrievedBytes']
            assert pbo['outputBytesAllocated'] == pbo['retrievedBytes']
            assert options['poolReadbackScratch'] or pbo['scratchBytesReused'] == 0
    per_image = {}
    if 'ownedPackState' in options:
        assert beauty['ownedPackStateRequested'] is options['ownedPackState']
        for field in ['stateQueryCalls', 'packStateCacheHits', 'packStateQueriesAvoided', 'packStateInvalidations']:
            assert type(beauty[field]) is int and beauty[field] >= 0
        assert type(beauty['stateQueryMs']) in (int, float) and math.isfinite(beauty['stateQueryMs']) and beauty['stateQueryMs'] >= 0
        assert beauty['packStateQueriesAvoided'] == beauty['packStateCacheHits'] * 4
        scopes = beauty['queuedCalls'] + int(beauty['retrievedCalls'] > 0)
        assert beauty['stateQueryCalls'] + beauty['packStateQueriesAvoided'] == 6 * scopes
        assert beauty['packStateFallbackReason'] is None and beauty['packStateInvalidations'] == 0
        # First-use temple prewarm is separate. Only the already verified,
        # explicitly allowed native fence fallback excuses missing PACK reuse.
        if options['ownedPackState'] and not result['acceptedAsyncFallback']:
            assert beauty['ownedPackStateUsed'] is True
            assert beauty['packStateCacheHits'] == 1 and beauty['packStateQueriesAvoided'] == 4
        else:
            assert beauty['ownedPackStateUsed'] is False
            assert beauty['packStateCacheHits'] == beauty['packStateQueriesAvoided'] == 0
        per_image.update(actualOwnedPackStateUsed=beauty['ownedPackStateUsed'],
                         packStateQueriesAvoided=beauty['packStateQueriesAvoided'], stateQueryCalls=beauty['stateQueryCalls'])
    if 'wordCompose' in options:
        assert perf['wordComparisonRequested'] is options['wordCompose']
        if options['wordCompose']:
            assert row[name]['stats']['hasMask'] is True and row[name]['stats']['fallbackReason'] is None
            assert perf['wordComparisonUsed'] is True and type(perf['wordComparedPixels']) is int
            assert perf['wordComparedPixels'] == row['width'] * row['height']
        else:
            assert perf['wordComparisonUsed'] is False and perf['wordComparedPixels'] == 0
        per_image.update(actualWordComparisonUsed=perf['wordComparisonUsed'], wordComparedPixels=perf['wordComparedPixels'])
    review = {}
    if 'cropBranchReadback' in options:
        assert region['requested'] == options['cropBranchReadback']
        if options['cropBranchReadback'] and branch_calls:
            assert region['used'] is True and region['fallback'] is None and region['fullBytes'] == byte_count
            assert region['rectangle'] == rectangle and region['readBytes'] == expected_bytes and not branch
        else:
            assert region['used'] is False
        lenses = efficiency['branchLenses']
        assert lenses['requested'] == options['omitBranchLenses']
        if options['omitBranchLenses'] and branch_calls:
            assert lenses['used'] is True and type(lenses['omittedMaterials']) is int and lenses['omittedMaterials'] > 0 and lenses['fallback'] is None
        else:
            assert lenses['used'] is False
        review = {'actualRegionUsed': region['used'],
                  'branchRegionBytesSaved': region['fullBytes'] - region['readBytes'] if region['used'] else 0,
                  'actualBranchLensesOmitted': lenses['used']}
    return {**result, 'actualFastPathUsed': result['actualFastPathUsed'] and not accepted_branch_fallback,
            'actualAsyncTemplesUsed': efficiency['asyncTemplesUsed'], 'acceptedBranchFallback': accepted_branch_fallback,
            'branchFallbackReason': efficiency['branchFallback'], 'firstPairPrewarm': warm['prewarmAttempted'],
            'scratchBytesReused': beauty.get('scratchBytesReused', 0) + branch.get('scratchBytesReused', 0), **per_image, **review}


def audit_async_readback(options, native, byte_count, *, allow_async_fallback=False, phase_id=None, renderer=None):
    speed = native['speedLab']
    pbo, shared = speed.get('pbo') or {}, native.get('sharedReadback') or {}
    calls = 1 if options['reuseSourcePixels'] else 2
    allowed = allow_async_fallback is True and phase_id == 'recorded24' and isinstance(renderer, str) and 'SwiftShader' in renderer
    assert speed['asyncReadbackRequested'] == options['asyncReadback']
    fallback = options['asyncReadback'] and speed['asyncReadbackUsed'] is False
    if fallback:
        assert allowed, 'An async fallback requires explicit recorded SwiftShader permission.'
        assert speed['asyncFallback'] == pbo['fallbackReason'] == BOUNDED_FENCE_REASON
        assert pbo['completed'] is False
        assert pbo['queuedCalls'] == calls and pbo['queuedBytes'] == calls * byte_count
        assert pbo['retrievedCalls'] == pbo['retrievedBytes'] == 0
        assert isinstance(pbo['waitMs'], (float, int)) and math.isfinite(pbo['waitMs']) and pbo['waitMs'] >= 500
        assert type(pbo['polls']) is int and pbo['polls'] > 0
        assert shared['readbackCalls'] == calls and shared['readbackBytes'] == calls * byte_count
    else:
        assert speed['asyncReadbackUsed'] == options['asyncReadback'] and speed['asyncFallback'] is None
        if options['asyncReadback']:
            assert pbo['completed'] is True and pbo['fallbackReason'] is None and native['sharedReadback'] is None
            assert pbo['queuedCalls'] == pbo['retrievedCalls'] == calls
            assert pbo['queuedBytes'] == pbo['retrievedBytes'] == calls * byte_count
        else:
            assert shared['readbackCalls'] == calls and shared['readbackBytes'] == calls * byte_count
    return {'actualFastPathUsed': not fallback, 'actualAsyncReadbackUsed': speed['asyncReadbackUsed'] is True,
            'asyncFallbackAllowed': allowed, 'acceptedAsyncFallback': bool(fallback),
            'asyncFallbackReason': speed['asyncFallback']}


def summarize_mechanisms(rows):
    reasons = {}
    for row in rows:
        reason = row['mechanism']['asyncFallbackReason']
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    review = {}
    per_image = {}
    if any('actualOwnedPackStateUsed' in row['mechanism'] for row in rows):
        per_image.update(actualOwnedPackStateCases=sum(row['mechanism'].get('actualOwnedPackStateUsed', False) for row in rows),
                         packStateQueriesAvoided=sum(row['mechanism'].get('packStateQueriesAvoided', 0) for row in rows),
                         stateQueryCalls=sum(row['mechanism'].get('stateQueryCalls', 0) for row in rows))
    if any('actualWordComparisonUsed' in row['mechanism'] for row in rows):
        per_image.update(actualWordComparisonCases=sum(row['mechanism'].get('actualWordComparisonUsed', False) for row in rows),
                         wordComparedPixels=sum(row['mechanism'].get('wordComparedPixels', 0) for row in rows))
    if any('actualRegionUsed' in row['mechanism'] for row in rows):
        review = {'actualRegionCases': sum(row['mechanism'].get('actualRegionUsed', False) for row in rows),
                  'branchRegionBytesSaved': sum(row['mechanism'].get('branchRegionBytesSaved', 0) for row in rows),
                  'actualBranchLensCases': sum(row['mechanism'].get('actualBranchLensesOmitted', False) for row in rows)}
    return {'cases': len(rows), 'actualFastPathCases': sum(row['mechanism']['actualFastPathUsed'] for row in rows),
            'actualAsyncReadbackCases': sum(row['mechanism']['actualAsyncReadbackUsed'] for row in rows),
            'acceptedAsyncFallbackCases': sum(row['mechanism']['acceptedAsyncFallback'] for row in rows),
            'asyncFallbackCases': sum(reasons.values()), 'asyncFallbackReasons': reasons,
            'scope': 'Saved case presentations only; controls and optional earlier warmup/timing repetitions are excluded.',
            'actualAsyncTempleCases': sum(row['mechanism'].get('actualAsyncTemplesUsed', False) for row in rows),
            'acceptedBranchFallbackCases': sum(row['mechanism'].get('acceptedBranchFallback', False) for row in rows),
            'firstPairPrewarmCases': sum(row['mechanism'].get('firstPairPrewarm', False) for row in rows),
            'scratchBytesReused': sum(row['mechanism'].get('scratchBytesReused', 0) for row in rows), **per_image, **review}
