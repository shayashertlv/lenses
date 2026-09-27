"""The atomic replace outlives a transient denial and gives up honestly."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reconstruction import atomic_files


class AtomicFilesTests(unittest.TestCase):
    def test_transient_denials_are_retried_then_the_replace_lands(self):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / 'a.next', Path(folder) / 'a.json'
            source.write_text('new'); destination.write_text('old')
            real, calls = os.replace, []

            def flaky(src, dst):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError(5, 'Access is denied')
                real(src, dst)
            with patch.object(atomic_files.os, 'replace', side_effect=flaky), patch.object(atomic_files.time, 'sleep') as sleep:
                atomic_files.replace_with_retry(source, destination)
            self.assertEqual(len(calls), 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(destination.read_text(), 'new')
            self.assertFalse(source.exists())

    def test_persistent_denial_and_other_errors_propagate(self):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / 'b.next', Path(folder) / 'b.json'
            source.write_text('new')
            with patch.object(atomic_files.os, 'replace', side_effect=PermissionError(5, 'denied')), patch.object(atomic_files.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    atomic_files.replace_with_retry(source, destination, attempts=4)
            with patch.object(atomic_files.os, 'replace', side_effect=FileNotFoundError('gone')) as replace:
                with self.assertRaises(FileNotFoundError):
                    atomic_files.replace_with_retry(source, destination)
            self.assertEqual(replace.call_count, 1, 'only the transient denial is retried')
            for bad in ({'attempts': 0}, {'first_delay': 0}, {'first_delay': 9}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    atomic_files.replace_with_retry(source, destination, **bad)


if __name__ == '__main__':
    unittest.main()
