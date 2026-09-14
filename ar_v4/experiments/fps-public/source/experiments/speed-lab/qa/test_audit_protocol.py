import copy
import unittest

from audit_protocol import BOUNDED_FENCE_REASON, audit_async_readback, summarize_mechanisms


class AsyncReceiptTests(unittest.TestCase):
    def example(self, reuse=True, fallback=True):
        calls = 1 if reuse else 2
        options = {'reuseSourcePixels': reuse, 'asyncReadback': True}
        native = {'sharedReadback': {'readbackCalls': calls, 'readbackBytes': calls * 512} if fallback else None,
                  'speedLab': {'asyncReadbackRequested': True, 'asyncReadbackUsed': not fallback,
                               'asyncFallback': BOUNDED_FENCE_REASON if fallback else None,
                               'pbo': {'queuedCalls': calls, 'queuedBytes': calls * 512,
                                       'retrievedCalls': 0 if fallback else calls, 'retrievedBytes': 0 if fallback else calls * 512,
                                       'completed': not fallback, 'waitMs': 505.4, 'polls': 107,
                                       'fallbackReason': BOUNDED_FENCE_REASON if fallback else None}}}
        return options, native

    def check(self, options, native, **policy):
        return audit_async_readback(options, native, 512, **({'allow_async_fallback': True,
            'phase_id': 'recorded24', 'renderer': 'ANGLE SwiftShader'} | policy))

    def test_exact_software_fallback_is_explicit_and_never_fast(self):
        for reuse in (True, False):
            options, native = self.example(reuse)
            result = self.check(options, native)
            self.assertFalse(result['actualFastPathUsed'])
            self.assertFalse(result['actualAsyncReadbackUsed'])
            self.assertTrue(result['acceptedAsyncFallback'])
            for policy in ({'allow_async_fallback': False}, {'phase_id': 'generated32'}, {'renderer': 'Intel D3D11'}, {'renderer': None}):
                with self.assertRaises(AssertionError):
                    self.check(options, native, **policy)

    def test_bad_reason_wait_or_read_counts_are_rejected(self):
        options, original = self.example()
        corruptions = [('asyncFallback', 'lost'), ('pbo.fallbackReason', 'lost'), ('pbo.waitMs', 499.9),
                       ('pbo.waitMs', float('inf')), ('pbo.polls', 0), ('pbo.queuedCalls', 0), ('pbo.queuedBytes', 0),
                       ('pbo.retrievedCalls', 1), ('pbo.retrievedBytes', 512), ('pbo.completed', True)]
        for field, value in corruptions:
            native = copy.deepcopy(original)
            target = native['speedLab']
            for key in field.split('.')[:-1]:
                target = target[key]
            target[field.split('.')[-1]] = value
            with self.assertRaises(AssertionError):
                self.check(options, native)
        for key in ('readbackCalls', 'readbackBytes'):
            native = copy.deepcopy(original)
            native['sharedReadback'][key] = 0
            with self.assertRaises(AssertionError):
                self.check(options, native)

    def test_hardware_success_and_fallback_have_distinct_summary(self):
        options, success = self.example(fallback=False)
        good = self.check(options, success, phase_id='generated32', renderer='Intel D3D11')
        self.assertTrue(good['actualFastPathUsed'])
        options, fallback = self.example()
        fallback = self.check(options, fallback)
        summary = summarize_mechanisms([{'mechanism': good}, {'mechanism': fallback}])
        self.assertEqual(summary['actualFastPathCases'], 1)
        self.assertEqual(summary['actualAsyncReadbackCases'], 1)
        self.assertEqual(summary['acceptedAsyncFallbackCases'], 1)
        self.assertEqual(summary['asyncFallbackReasons'], {BOUNDED_FENCE_REASON: 1})


if __name__ == '__main__':
    unittest.main()
