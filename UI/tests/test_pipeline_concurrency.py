"""Tests for pipeline concurrency optimizations.

Verifies structural properties of the pipeline code:
1. Portrait pre-loading thread starts before face analysis / search
2. All 3 try-on threads launch in parallel
3. InventoryMatcher is pre-created before face analysis
"""

import inspect
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSmartFitPipelineStructure(unittest.TestCase):
    """Verify Smart Fit pipeline has correct concurrency structure."""

    @classmethod
    def setUpClass(cls):
        from UI.pipelines import run_pipeline
        cls.source = inspect.getsource(run_pipeline)

    def test_portrait_thread_starts_before_face_analysis(self):
        """Portrait pre-loading thread must start before FaceAnalyzer.analyze()."""
        thread_start = self.source.index("portrait_thread.start()")
        analyze_call = self.source.index("analyzer.analyze(")
        self.assertLess(thread_start, analyze_call,
                        "portrait_thread.start() should come before analyzer.analyze()")

    def test_matcher_created_before_face_analysis(self):
        """InventoryMatcher should be pre-created before face analysis starts."""
        matcher_create = self.source.index("InventoryMatcher(")
        analyze_call = self.source.index("analyzer.analyze(")
        self.assertLess(matcher_create, analyze_call,
                        "InventoryMatcher creation should come before analyzer.analyze()")

    def test_portrait_thread_joined_after_matching(self):
        """Portrait thread join should happen after matching completes."""
        match_call = self.source.index("matcher.match(")
        thread_join = self.source.index("portrait_thread.join()")
        self.assertLess(match_call, thread_join,
                        "matcher.match() should come before portrait_thread.join()")

    def test_portrait_error_handled_after_join(self):
        """Error from portrait pre-loading should be checked after join."""
        thread_join = self.source.index("portrait_thread.join()")
        error_check = self.source.index("_portrait_result[1] is not None")
        self.assertLess(thread_join, error_check,
                        "Error check should come after portrait_thread.join()")

    def test_all_tryon_threads_start_before_any_join(self):
        """All try-on threads must start() before any join() is called."""
        tryon_section = self.source[self.source.index("threads = ["):]
        start_pos = tryon_section.index("t.start()")
        join_pos = tryon_section.index("threads[0].join()")
        self.assertLess(start_pos, join_pos,
                        "All threads should start() before any join()")

    def test_primary_ready_signaled_after_first_join(self):
        """'primary_ready' stage should be set after first thread joins."""
        tryon_section = self.source[self.source.index("threads = ["):]
        first_join = tryon_section.index("threads[0].join()")
        primary_ready = tryon_section.index('"primary_ready"')
        self.assertLess(first_join, primary_ready)


class TestFreeSearchPipelineStructure(unittest.TestCase):
    """Verify Free Search pipeline has correct concurrency structure."""

    @classmethod
    def setUpClass(cls):
        from UI.pipelines import run_free_search_pipeline
        cls.source = inspect.getsource(run_free_search_pipeline)

    def test_portrait_thread_starts_before_search(self):
        """Portrait pre-loading thread must start before embedding/search."""
        thread_start = self.source.index("portrait_thread.start()")
        embed_call = self.source.index("embed_content")
        self.assertLess(thread_start, embed_call,
                        "portrait_thread.start() should come before embed_content()")

    def test_portrait_thread_joined_after_search(self):
        """Portrait thread join should happen after search/matching completes."""
        embed_call = self.source.index("embed_content")
        thread_join = self.source.index("portrait_thread.join()")
        self.assertLess(embed_call, thread_join,
                        "embed_content() should come before portrait_thread.join()")

    def test_portrait_error_handled_after_join(self):
        """Error from portrait pre-loading should be checked after join."""
        thread_join = self.source.index("portrait_thread.join()")
        error_check = self.source.index("_portrait_result[1] is not None")
        self.assertLess(thread_join, error_check)

    def test_all_tryon_threads_start_before_any_join(self):
        """All try-on threads must start() before any join() is called."""
        tryon_section = self.source[self.source.index("threads = ["):]
        start_pos = tryon_section.index("t.start()")
        join_pos = tryon_section.index("threads[0].join()")
        self.assertLess(start_pos, join_pos)

    def test_primary_ready_signaled_after_first_join(self):
        tryon_section = self.source[self.source.index("threads = ["):]
        first_join = tryon_section.index("threads[0].join()")
        primary_ready = tryon_section.index('"primary_ready"')
        self.assertLess(first_join, primary_ready)


class TestRecolorPipelineUnchanged(unittest.TestCase):
    """Verify recolor pipeline still works (not modified)."""

    def test_recolor_pipeline_exists(self):
        from UI.pipelines import run_recolor_pipeline
        self.assertTrue(callable(run_recolor_pipeline))

    def test_helper_functions_exist(self):
        from UI.pipelines import _build_search_description, _fs_load_image_as_part
        self.assertTrue(callable(_build_search_description))
        self.assertTrue(callable(_fs_load_image_as_part))


if __name__ == "__main__":
    unittest.main()
