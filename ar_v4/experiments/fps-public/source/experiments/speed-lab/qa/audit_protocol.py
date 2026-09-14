"""Independent native-readback receipt validation, without JS or renderer imports."""
import math

BOUNDED_FENCE_REASON = 'The native readback fence exceeded its bounded wait.'


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
    return {'cases': len(rows), 'actualFastPathCases': sum(row['mechanism']['actualFastPathUsed'] for row in rows),
            'actualAsyncReadbackCases': sum(row['mechanism']['actualAsyncReadbackUsed'] for row in rows),
            'acceptedAsyncFallbackCases': sum(row['mechanism']['acceptedAsyncFallback'] for row in rows),
            'asyncFallbackCases': sum(reasons.values()), 'asyncFallbackReasons': reasons,
            'scope': 'Saved case presentations only; controls and optional earlier warmup/timing repetitions are excluded.'}
