"""Offline region-engine contract tests; actual local-model test is explicit opt-in."""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.region_engine import (OfflineSAM2RegionEngine, _checked_output,
                                         _clone, _offline, _strict_checkpoint)


class FakeTorch:
    threads = 7

    @classmethod
    def get_num_threads(cls):
        return cls.threads

    @classmethod
    def set_num_threads(cls, value):
        cls.threads = value

    inference_mode = staticmethod(nullcontext)


class ArrayTensor:
    def __init__(self, array):
        self.array = array

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.array

    def __getitem__(self, key):
        return ArrayTensor(self.array[key])


class RegionEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.weights = self.root / "cached.pt"
        self.weights.write_bytes(b"fake model bytes for contract validation")
        self.sha = hashlib.sha256(self.weights.read_bytes()).hexdigest()
        self.engine = OfflineSAM2RegionEngine(self.weights, expected_sha256=self.sha, runtime_dir=self.root / "runtime")
        self.image = np.zeros((8, 11, 3), np.uint8)
        self.image[2:6, 3:9] = [220, 80, 30]
        self.prompt = {"bbox_xyxy": [1., 1., 10., 7.], "points_xy": [[4., 3.], [1., 1.]], "point_labels": [1, 0]}

    def output(self):
        mask = np.zeros((8, 11), bool)
        mask[2:6, 3:9] = True
        mask[3:5, 5:7] = False  # Preserve internal holes.
        return [[{"mask": mask, "predicted_quality": .9},
                 {"mask": mask.copy(), "predicted_quality": .9},
                 {"mask": np.zeros_like(mask), "predicted_quality": 0.}]]

    def fake_predict(self, raw=None, prompts=None):
        with patch.dict("sys.modules", {"torch": FakeTorch}), patch.object(self.engine, "_load"), \
                patch.object(self.engine, "_run", return_value=raw if raw is not None else self.output()):
            return self.engine.predict(self.image, prompts or [self.prompt])

    def test_describe_is_json_lazy_and_records_integrity_limits(self):
        with patch.object(self.engine, "_load", side_effect=AssertionError("must remain lazy")):
            report = self.engine.describe()
        self.assertEqual(json.loads(json.dumps(report)), report)
        self.assertEqual(report["weights"]["sha256"], self.sha)
        self.assertEqual(report["semantic_identity"], "unmeasured")
        self.assertFalse(self.engine.receipt()["model_loaded"])
        self.assertFalse(self.engine.runtime_dir.exists())
        self.assertNotIn("semantic_confidence", report)
        self.assertTrue(any("not semantic confidence" in message for message in report["limitations"]))

    def test_identity_ignores_load_state_and_runtime_directory_across_instances(self):
        before = self.engine.describe()
        self.engine._predictor = object()
        self.engine._state_keys = 615
        self.engine._source_hashes = {"sam.build": "a" * 64}
        self.assertEqual(self.engine.describe(), before)
        other = OfflineSAM2RegionEngine(self.weights, expected_sha256=self.sha, runtime_dir=self.root / "other-runtime")
        self.assertEqual(other.describe(), before)
        self.assertNotEqual(other.receipt(), self.engine.receipt())
        sources = before["code"]["dependency_sources"]
        if sources["status"] == "pinned":
            self.assertGreater(sources["source_files"], 3)
        else:
            self.assertEqual(sources["status"], "not_installed")

    def test_invalid_weight_paths_hashes_and_runtime_rejected(self):
        for weights, options in [
            (str(self.weights), {}), (self.root / "missing.pt", {}), (self.root, {}),
            (self.weights, {"expected_sha256": "0" * 64}),
            (self.weights, {"expected_sha256": True}), (self.weights, {"expected_sha256": "nope"}),
            (self.weights, {"runtime_dir": self.weights}), (self.weights, {"runtime_dir": "runtime"})]:
            with self.subTest(weights=weights, options=options), self.assertRaises((ValueError, TypeError, FileNotFoundError)):
                OfflineSAM2RegionEngine(weights, **options)
        empty = self.root / "empty.pt"; empty.touch()
        with self.assertRaises(ValueError):
            OfflineSAM2RegionEngine(empty)

    def test_weight_change_rejected_by_both_read_and_predict_before_model_import(self):
        self.weights.write_bytes(b"different weights")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.engine.describe()
        with patch.object(self.engine, "_load") as load, self.assertRaisesRegex(ValueError, "changed"):
            self.engine.predict(self.image, [self.prompt])
        load.assert_not_called()

    def test_invalid_image_and_prompt_schemas_never_load(self):
        images = [np.zeros((8, 11, 3), float), np.zeros((0, 11, 3), np.uint8),
                  np.zeros((8, 11, 4), np.uint8), [[1, 2, 3]]]
        bad_prompts = [[], {}, [{}], [{**self.prompt, "label": "lens"}],
                       [{"bbox_xyxy": [0, 0, 12, 8]}], [{"bbox_xyxy": [0, 0, 11.5, 8]}],
                       [{"bbox_xyxy": [0, 0, float("nan"), 8]}],
                       [{"bbox_xyxy": [0, 0, True, 8]}], [{"bbox_xyxy": [3, 0, 2, 8]}],
                       [{"bbox_xyxy": [0, 0, .5, 8]}],
                       [{**self.prompt, "point_labels": [1]}], [{**self.prompt, "point_labels": [1, True]}],
                       [{**self.prompt, "point_labels": [1, 2]}], [{**self.prompt, "points_xy": [[11, 2], [1, 1]]}],
                       [{"bbox_xyxy": [0, 0, 11, 8], "points_xy": [[4, 4]]}]]
        with patch.object(self.engine, "_load") as load:
            for image in images:
                with self.subTest(image_shape=getattr(image, "shape", None)), self.assertRaises(ValueError):
                    self.engine.predict(image, [self.prompt])
            for prompts in bad_prompts:
                with self.subTest(prompts=prompts), self.assertRaises(ValueError):
                    self.engine.predict(self.image, prompts)
            load.assert_not_called()

    def test_three_candidates_preserve_holes_duplicates_empty_masks_and_ownership(self):
        raw = self.output()
        result = self.fake_predict(raw)
        self.assertEqual(len(result[0]), 3)
        for actual, expected in zip(result[0], raw[0], strict=True):
            np.testing.assert_array_equal(actual["mask"], expected["mask"])
            self.assertEqual(actual["predicted_quality"], expected["predicted_quality"])
            self.assertFalse(np.shares_memory(actual["mask"], expected["mask"]))
        self.assertEqual(FakeTorch.threads, 7)

    def test_invalid_engine_outputs_do_not_silently_become_masks(self):
        broken = [None, [], self.output() + self.output(), [self.output()[0][:2]]]
        for field, value in [("mask", np.zeros((8, 11), float)), ("mask", np.zeros((7, 11), bool)),
                             ("predicted_quality", float("nan")), ("predicted_quality", 1.01),
                             ("predicted_quality", True)]:
            raw = self.output(); raw[0][0][field] = value; broken.append(raw)
        raw = self.output(); raw[0][0]["semantic_label"] = "lens"; broken.append(raw)
        for raw in broken:
            with self.subTest(raw_type=type(raw).__name__), self.assertRaises(RuntimeError):
                _checked_output(raw, 1, (8, 11))

    def test_network_environment_and_threads_restored_after_failure(self):
        original = socket.socket.connect
        old_config = os.environ.get("YOLO_CONFIG_DIR")
        with self.assertRaisesRegex(RuntimeError, "Network access"), _offline(self.root):
            socket.create_connection(("forbidden.invalid", 80))
        self.assertIs(socket.socket.connect, original)
        self.assertEqual(os.environ.get("YOLO_CONFIG_DIR"), old_config)
        with patch.dict("sys.modules", {"torch": FakeTorch}), patch.object(self.engine, "_load", side_effect=RuntimeError("load failed")):
            with self.assertRaisesRegex(RuntimeError, "load failed"):
                self.engine.predict(self.image, [self.prompt])
        self.assertIs(socket.socket.connect, original)
        self.assertEqual(os.environ.get("YOLO_CONFIG_DIR"), old_config)
        self.assertEqual(FakeTorch.threads, 7)

    def test_private_builder_clone_does_not_patch_dependency_globals(self):
        namespace = {"dependency": lambda: "original"}
        exec("def builder(): return dependency()", namespace)
        builder = namespace["builder"]
        cloned = _clone(builder, {"dependency": lambda: "safe local"})
        self.assertEqual(cloned(), "safe local")
        self.assertEqual(builder(), "original")

    def test_tensor_checkpoint_loader_requests_safe_mode_and_exact_state(self):
        calls = []
        state = {"weight": np.array([1., 2.])}
        fake = types.SimpleNamespace(load=lambda stream, **kw: (calls.append(kw) or {"model": state}),
                                     is_tensor=lambda v: isinstance(v, np.ndarray), isfinite=np.isfinite)
        model = types.SimpleNamespace(load_state_dict=lambda value, strict: (
            calls.append({"strict": strict, "state": value}) or types.SimpleNamespace(missing_keys=[], unexpected_keys=[])))
        self.assertEqual(_strict_checkpoint(model, None, fake), 1)
        self.assertEqual(calls[0], {"map_location": "cpu", "weights_only": True})
        self.assertTrue(calls[1]["strict"])
        state["weight"][0] = np.nan
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            _strict_checkpoint(model, None, fake)
        fake.load = lambda *args, **kwargs: {"model": {"weight": "not a tensor"}}
        with self.assertRaisesRegex(ValueError, "tensor"):
            _strict_checkpoint(model, None, fake)
        fake.load = lambda *args, **kwargs: {"weight": np.array([1.])}
        model.load_state_dict = lambda *args, **kwargs: types.SimpleNamespace(missing_keys=["missing"], unexpected_keys=[])
        with self.assertRaisesRegex(ValueError, "exactly"):
            _strict_checkpoint(model, None, fake)

    def test_one_embedding_serves_all_prompts_and_point_grouping_is_preserved(self):
        calls = []
        fake = types.SimpleNamespace(features=object())
        fake.reset_image = lambda: calls.append("reset")
        fake.set_image = lambda image: calls.append(("image", image.copy()))
        def infer(features, shape, **kwargs):
            calls.append(("infer", shape, kwargs))
            return ArrayTensor(np.zeros((3, 8, 11), bool)), ArrayTensor(np.array([[0, 0, 0, 0, v, 0] for v in [.1, .2, .3]]))
        fake.inference_features = infer
        self.engine._predictor = fake
        prompts = [self.prompt] * 10
        result = self.engine._run(self.image, prompts)
        self.assertEqual(len(result), 10)
        image_calls = [call for call in calls if isinstance(call, tuple) and call[0] == "image"]
        self.assertEqual(len(image_calls), 1)
        np.testing.assert_array_equal(image_calls[0][1], self.image[..., ::-1])
        inference = [call for call in calls if isinstance(call, tuple) and call[0] == "infer"]
        self.assertEqual(len(inference), 10)
        self.assertEqual(inference[0][2]["points"], [self.prompt["points_xy"]])
        self.assertEqual(inference[0][2]["bboxes"], [[1., 1., 9., 6.]])
        self.assertTrue(inference[0][2]["multimask_output"])
        self.assertEqual(calls[0], "reset"); self.assertEqual(calls[-1], "reset")
        fake.inference_features = lambda *_args, **_kw: (_ for _ in ()).throw(RuntimeError("decoder failed"))
        with self.assertRaisesRegex(RuntimeError, "decoder failed"):
            self.engine._run(self.image, prompts)
        self.assertEqual(calls[-1], "reset")


@unittest.skipUnless(os.environ.get("LENSES_RUN_SAM2_COMPAT") == "1", "Explicit offline local-model compatibility test")
class LocalSAM2CompatibilityTests(unittest.TestCase):
    def test_ten_prompts_share_one_real_embedding_with_three_native_alternatives(self):
        from PIL import Image, ImageOps
        root = Path(__file__).resolve().parents[1]
        corpus_path = root / "data/refinement-corpus.json"
        corpus = json.loads(corpus_path.read_text())
        source = (corpus_path.parent / corpus["cases"][0]["photos"]["front"]).resolve(strict=True)
        weights = Path("C:/Users/Shay/.cache/huggingface/hub/models--facebook--sam2.1-hiera-base-plus/snapshots/b7320756a13354e7530a63935656d35b2f91a290/sam2.1_hiera_base_plus.pt")
        with Image.open(source) as original:
            image = np.asarray(ImageOps.exif_transpose(original).convert("RGB"))
        h, w = image.shape[:2]
        prompts = [{"bbox_xyxy": [float(i), float(i), float(w - i), float(h - i)]} for i in range(8)]
        prompts += [{"bbox_xyxy": [0., 0., float(w), float(h)],
                     "points_xy": [[w * .5, h * .5]], "point_labels": [1]},
                    {"bbox_xyxy": [0., 0., float(w), float(h)],
                     "points_xy": [[w * .5, h * .5], [1., 1.]], "point_labels": [1, 0]}]
        with tempfile.TemporaryDirectory() as runtime:
            engine = OfflineSAM2RegionEngine(weights, expected_sha256="a2345aede8715ab1d5d31b4a509fb160c5a4af1970f199d9054ccfb746c004c5",
                                            runtime_dir=Path(runtime))
            identity = engine.describe()
            original_run = engine._run
            observed = {}
            def counted_run(*args):
                with patch.object(engine._predictor, "get_im_features", wraps=engine._predictor.get_im_features) as extraction:
                    result = original_run(*args)
                    observed["embeddings"] = extraction.call_count
                    return result
            with patch.object(engine, "_run", side_effect=counted_run):
                result = engine.predict(image, prompts)
            self.assertEqual(observed["embeddings"], 1)
            self.assertEqual(len(result), 10)
            self.assertTrue(all(len(candidates) == 3 for candidates in result))
            self.assertTrue(all(c["mask"].shape == (h, w) and c["mask"].dtype == np.bool_ for row in result for c in row))
            self.assertIsNone(engine._predictor.features)
            self.assertEqual(engine.receipt()["state_keys"], 615)
            self.assertEqual(engine.describe(), identity)
            print(json.dumps({"sam2_compatibility": "passed", "prompts": 10, "alternatives": 30,
                              "image_embeddings": observed["embeddings"], "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                              "weights_sha256": engine.describe()["weights"]["sha256"]}))


if __name__ == "__main__":
    unittest.main()
