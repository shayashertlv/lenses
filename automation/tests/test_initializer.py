import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from PIL import Image

from reconstruction.initializer import (MeshyBackend, MESHY_ENDPOINT, MESHY_SPLIT_ENDPOINT, EYEWEAR_SPLIT_PARTS,
                                         recover_initializer_task, resolve_initial_model, select_meshy_views)


def glb_bytes(mutate=None):
    points = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(points)}],
           "bufferViews": [{"buffer": 0, "byteLength": len(points)}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
           "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0,
           "extras": {"arbitrary_metadata": "preserve me exactly"}}
    if mutate:
        mutate(doc)
    payload = json.dumps(doc).encode()
    payload += b" " * (-len(payload) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 28 + len(payload) + len(points))
            + struct.pack("<II", len(payload), 0x4E4F534A) + payload
            + struct.pack("<II", len(points), 0x004E4942) + points)


class FakeBackend:
    provider = "meshy"

    def __init__(self, raw=None):
        self.raw = glb_bytes() if raw is None else raw
        self.submits = self.retrieves = self.downloads = 0
        self.status = "succeeded"
        self.submit_error = self.retrieve_error = self.download_error = False

    def submit(self, request):
        self.submits += 1
        self.request = deepcopy(request)
        if self.submit_error:
            raise TimeoutError("request might have been accepted")
        return "task-001"

    def retrieve(self, task_id):
        self.retrieves += 1
        if self.retrieve_error:
            raise TimeoutError("poll timeout")
        return {"task_id": task_id, "status": self.status}

    def download(self, task):
        self.downloads += 1
        if self.download_error:
            raise TimeoutError("download timeout")
        return self.raw


class FakeSplitBackend(FakeBackend):
    """Generation plus a second, separately reserved split task."""

    def __init__(self, raw=None, split_raw=None):
        super().__init__(raw)
        self.split_raw = glb_bytes(lambda doc: doc["nodes"][0].update(name="model_part0")) if split_raw is None else split_raw
        self.split_transport = object()
        self.split_submits = self.split_retrieves = self.split_downloads = 0
        self.split_status = "succeeded"
        self.split_requests = []
        self.bound = []

    def bind_split_output(self, output, request_sha256):
        self.bound.append((output, request_sha256))

    def preflight_split(self, request):
        pass

    def submit_split(self, request):
        self.split_submits += 1
        self.split_requests.append(deepcopy(request))
        return "split-001"

    def retrieve_split(self, task_id):
        self.split_retrieves += 1
        return {"task_id": task_id, "status": self.split_status, "part_count": 6}

    def download_split(self, task):
        self.split_downloads += 1
        return self.split_raw


class FakeTransport:
    def __init__(self):
        self.posts, self.gets, self.downloads = [], [], []
        self.response = {"id": "task-001", "status": "SUCCEEDED", "model_urls": {"glb": "https://assets.meshy.ai/result.glb"}}

    def post_json(self, url, payload):
        self.posts.append((url, payload))
        return {"result": "task-001"}

    def get_json(self, url):
        self.gets.append(url)
        return deepcopy(self.response)

    def download_public(self, url, *, max_bytes):
        self.downloads.append((url, max_bytes))
        return glb_bytes()


class InitializerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.output = self.base / "stage"
        self.source = self.base / "source.glb"
        self.source.write_bytes(glb_bytes())
        self.config = {"kind": "meshy"}
        self.photos = self.make_photos(["front", "front_right", "right", "back", "left", "unknown"])

    def make_photos(self, views):
        photos = []
        for i, view in enumerate(views):
            path = self.base / f"photo-{i}.png"
            Image.new("RGB", (4, 3), (i*17, 80, 140)).save(path)
            photos.append({"id": f"p{i}", "path": path.name, "view": view,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        return photos

    def resolve(self, **kwargs):
        return resolve_initial_model(self.config, self.base, self.output, self.photos, **kwargs)

    def test_existing_model_exact_copy_without_photo_reconstruction_claim(self):
        self.config = {"kind": "existing_glb", "path": "source.glb"}
        original = deepcopy(self.photos)
        result = self.resolve()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["model"], "initial.glb")
        self.assertEqual((self.output/result["model"]).read_bytes(), self.source.read_bytes())
        self.assertEqual(result["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(result["sha256"], result["source_sha256"])
        self.assertEqual(result["quality_verdict"], "unmeasured")
        self.assertFalse(result["photo_reconstruction_verified"])
        self.assertFalse(result["native_preparation_applied"])
        self.assertEqual(result["selected"], [])
        self.assertEqual(len(result["unused"]), len(self.photos))
        self.assertEqual(original, self.photos)
        self.assertEqual(self.resolve(), result)

    def test_existing_model_accepts_zero_photos(self):
        result = resolve_initial_model({"kind": "existing_glb", "path": str(self.source)}, self.base, self.output, [])
        self.assertEqual(result["status"], "complete")

    def test_meshy_prepares_offline_without_reserving_submission(self):
        result = self.resolve()
        self.assertEqual(result["status"], "awaiting_backend")
        self.assertEqual([p["view"] for p in result["selected"]], ["front", "back", "right", "left"])
        self.assertEqual(len(result["unused"]), 2)
        self.assertEqual(len(result["all_photos"]), 6)
        self.assertTrue(result["all_photos_retained_for_fitting"])
        self.assertFalse((self.output/"submit_request.json").exists())
        self.assertEqual(self.resolve(), result)

    def test_explicit_submission_required_even_with_backend(self):
        backend = FakeBackend()
        self.assertEqual(self.resolve(backend=backend)["status"], "awaiting_submission_authorization")
        self.assertEqual(backend.submits, 0)
        self.assertFalse((self.output/"submit_request.json").exists())

    def test_one_submission_completion_and_offline_resume(self):
        backend = FakeBackend()
        result = self.resolve(backend=backend, allow_submit=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual((backend.submits, backend.retrieves, backend.downloads), (1, 1, 1))
        self.assertEqual(self.resolve(backend=backend, allow_submit=True), result)
        self.assertEqual(self.resolve(), result)
        self.assertEqual((backend.submits, backend.retrieves, backend.downloads), (1, 1, 1))
        self.assertEqual(result["provenance"], "provider_initial_hypothesis")

    def test_pending_task_resumes_without_submission_permission(self):
        backend = FakeBackend()
        backend.status = "pending"
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "pending")
        backend.status = "succeeded"
        self.assertEqual(self.resolve(backend=backend)["status"], "complete")
        self.assertEqual(backend.submits, 1)
        self.assertEqual(backend.retrieves, 2)

    def test_uncertain_submit_never_automatically_repeats(self):
        backend = FakeBackend()
        backend.submit_error = True
        result = self.resolve(backend=backend, allow_submit=True)
        self.assertEqual(result["status"], "submission_uncertain")
        backend.submit_error = False
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "submission_uncertain")
        self.assertEqual(backend.submits, 1)
        self.assertEqual(backend.retrieves, 0)
        with self.assertRaises(ValueError):
            recover_initializer_task(self.output, "task-001", request_sha256="f"*64)
        recover_initializer_task(self.output, "task-001", request_sha256=result["request_sha256"])
        self.assertEqual(self.resolve(backend=backend)["status"], "complete")
        receipt = json.loads((self.output/"task_receipt.json").read_text())
        self.assertEqual(receipt["association"], "explicit_operator_reconciliation")
        self.assertEqual(backend.submits, 1)

    def test_partial_submission_reservation_remains_uncertain(self):
        self.resolve()
        (self.output/"submit_request.json").write_bytes(b'{"request_sha256":')
        backend = FakeBackend()
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "submission_uncertain")
        self.assertEqual(backend.submits, 0)

    def test_failed_provider_task_is_not_retried(self):
        backend = FakeBackend()
        backend.status = "failed"
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "provider_failed")
        backend.status = "succeeded"
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "provider_failed")
        self.assertEqual((backend.submits, backend.retrieves, backend.downloads), (1, 1, 0))

    def test_poll_and_download_errors_keep_same_task(self):
        backend = FakeBackend()
        backend.retrieve_error = True
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "poll_error")
        backend.retrieve_error = False
        backend.download_error = True
        self.assertEqual(self.resolve(backend=backend)["status"], "download_error")
        backend.download_error = False
        self.assertEqual(self.resolve(backend=backend)["status"], "complete")
        self.assertEqual((backend.submits, backend.retrieves, backend.downloads), (1, 2, 2))

    def test_invalid_provider_artifact_does_not_create_candidate_or_resubmit(self):
        backend = FakeBackend(b"this is not a GLB")
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "artifact_invalid")
        self.assertFalse((self.output/"initial.glb").exists())
        self.assertEqual(self.resolve(backend=backend, allow_submit=True)["status"], "artifact_invalid")
        self.assertEqual(backend.submits, 1)

    def test_split_is_a_second_task_on_the_finished_generation(self):
        self.config = {"kind": "meshy", "split": {}}
        backend = FakeSplitBackend()
        backend.split_status = "pending"
        result = self.resolve(backend=backend, allow_submit=True)
        self.assertEqual((result["status"], result["phase"]), ("pending", "split"))
        self.assertEqual((backend.submits, backend.retrieves, backend.downloads), (1, 1, 1))
        self.assertEqual(backend.split_submits, 1)
        self.assertEqual(backend.split_requests[0]["input_task_id"], "task-001")
        self.assertEqual(backend.split_requests[0]["parts"], list(EYEWEAR_SPLIT_PARTS))
        self.assertEqual(result["generation"]["model"], "generated.glb")
        self.assertEqual((self.output / "generated.glb").read_bytes(), backend.raw)
        self.assertFalse((self.output / "initial.glb").exists())
        self.assertTrue((self.output / "split" / "task_receipt.json").exists())
        backend.split_status = "succeeded"
        result = self.resolve(backend=backend)  # no new submission authority needed to finish
        self.assertEqual((result["status"], result["phase"]), ("complete", "complete"))
        self.assertEqual(result["provenance"], "provider_split_parts_hypothesis")
        self.assertEqual((self.output / "initial.glb").read_bytes(), backend.split_raw)
        self.assertEqual(result["appearance_source"], "generated.glb")
        self.assertEqual(result["split_task_id"], "split-001")
        self.assertEqual(result["provider_split_observation"]["part_count"], 6)
        self.assertEqual(result["generation"]["task_id"], "task-001")
        self.assertEqual((backend.submits, backend.split_submits, backend.split_retrieves, backend.split_downloads), (1, 1, 2, 1))
        self.assertEqual(self.resolve(), result)
        self.assertEqual(self.resolve(backend=backend, allow_submit=True), result)
        self.assertEqual((backend.submits, backend.split_submits), (1, 1))

    def test_split_needs_a_capable_backend_and_never_repeats_the_generation(self):
        self.config = {"kind": "meshy", "split": {"parts": ["frame", "lens"]}}
        backend = FakeBackend()
        result = self.resolve(backend=backend, allow_submit=True)
        self.assertEqual((result["status"], result["phase"]), ("awaiting_backend", "split"))
        self.assertEqual(backend.submits, 1)
        self.assertTrue((self.output / "generation_receipt.json").exists())
        capable = FakeSplitBackend()
        capable.split_status = "failed"
        result = self.resolve(backend=capable, allow_submit=True)
        self.assertEqual((result["status"], result["phase"]), ("provider_failed", "split"))
        self.assertEqual((capable.submits, capable.split_submits), (0, 1))
        self.assertEqual(capable.split_requests[0]["parts"], ["frame", "lens"])
        capable.split_status = "succeeded"
        self.assertEqual(self.resolve(backend=capable, allow_submit=True)["status"], "provider_failed")
        self.assertEqual(capable.split_submits, 1)
        no_transport = FakeSplitBackend()
        no_transport.split_transport = None
        self.output = self.base / "stage-2"
        self.assertEqual(self.resolve(backend=no_transport, allow_submit=True)["phase"], "split")
        self.assertEqual(self.resolve(backend=no_transport, allow_submit=True)["status"], "awaiting_backend")
        self.assertEqual(no_transport.split_submits, 0)

    def test_split_rejects_cache_imports_and_bad_part_lists(self):
        for config in ({"kind": "meshy", "cached_glb": {"path": "source.glb"}, "split": {}},
                       {"kind": "meshy", "split": {"parts": []}},
                       {"kind": "meshy", "split": {"parts": ["a,b"]}},
                       {"kind": "meshy", "split": {"parts": ["x"] * 2}},
                       {"kind": "meshy", "split": {"layout": "assembled"}},
                       {"kind": "existing_glb", "path": "source.glb", "split": {}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                resolve_initial_model(config, self.base, self.base / "bad", self.photos)

    def test_provider_views_feed_the_provider_only(self):
        extra = self.make_photos(["back"])
        extra[0]["path"] = str(self.base / "extra-back.png")
        Image.new("RGB", (4, 3), (200, 10, 10)).save(extra[0]["path"])
        extra[0]["sha256"] = hashlib.sha256(Path(extra[0]["path"]).read_bytes()).hexdigest()
        self.photos = self.make_photos(["front", "front_right"])
        self.config = {"kind": "meshy", "provider_views": [{"path": extra[0]["path"], "view": "back"}]}
        backend = FakeBackend()
        result = self.resolve(backend=backend, allow_submit=True)
        self.assertEqual(result["status"], "complete")
        selected = {p["id"]: p for p in backend.request["selection"]["selected"]}
        self.assertIn("provider-view-1", selected)
        self.assertFalse(selected["provider-view-1"]["fitting_input"])
        self.assertTrue(all(p["fitting_input"] for i, p in selected.items() if i != "provider-view-1"))
        self.assertEqual(result["provider_only_photos"], ["provider-view-1"])
        self.assertEqual([p["id"] for p in result["all_photos"]], ["p0", "p1"])
        with self.assertRaises(ValueError):
            resolve_initial_model({"kind": "existing_glb", "path": "source.glb", "provider_views": []}, self.base, self.base / "x", self.photos)

    def test_cache_import_stays_offline_and_does_not_attest_provider_origin(self):
        self.config["cached_glb"] = {"path": "source.glb"}
        backend = FakeBackend()
        result = self.resolve(backend=backend, allow_submit=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["provenance"], "local_cache_import_origin_unverified")
        self.assertEqual((backend.submits, backend.retrieves, backend.downloads), (0, 0, 0))
        self.assertEqual(len(result["all_photos"]), 6)

    def test_photo_source_and_model_output_corruption_fail_closed(self):
        self.resolve()
        photo_path = self.base/self.photos[-1]["path"]  # unused view still pinned
        photo_path.write_bytes(b"changed unused photo")
        with self.assertRaises(ValueError):
            self.resolve()
        self.photos[-1]["sha256"] = hashlib.sha256(photo_path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            self.resolve()
        self.output = self.base/"existing-stage"
        self.config = {"kind": "existing_glb", "path": "source.glb"}
        self.resolve()
        (self.output/"initial.glb").write_bytes(b"corruption")
        with self.assertRaises(ValueError):
            self.resolve()

    def test_changed_settings_cannot_reuse_receipts(self):
        self.resolve()
        self.config["settings"] = {"texture_resolution": "2k"}
        backend = FakeBackend()
        with self.assertRaises(ValueError):
            self.resolve(backend=backend, allow_submit=True)
        self.assertEqual(backend.submits, 0)

    def test_static_self_contained_validation_rejects_unsupported_geometry(self):
        mutations = [lambda doc: doc.update(animations=[{}]),
                     lambda doc: doc["nodes"][0].update(skin=0),
                     lambda doc: doc["nodes"][0].update(extensions={"EXT_mesh_gpu_instancing": {}}),
                     lambda doc: doc["bufferViews"][0].update(extensions={"EXT_meshopt_compression": {}}),
                     lambda doc: doc["meshes"][0]["primitives"][0].update(targets=[{"POSITION": 0}]),
                     lambda doc: doc.update(images=[{"uri": "https://host.invalid/texture.png"}]),
                     lambda doc: doc.update(images=[{"uri": "texture.png"}]),
                     lambda doc: doc["buffers"][0].update(uri="mesh.bin"),
                     lambda doc: doc["accessors"][0].update(count=999),
                     lambda doc: doc["nodes"][0].update(children=[0])]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.source.write_bytes(glb_bytes(mutation))
                result = resolve_initial_model({"kind": "existing_glb", "path": str(self.source)},
                                               self.base, self.base/f"bad-{index}", [])
                self.assertEqual(result["status"], "artifact_invalid")
                self.assertFalse((self.base/f"bad-{index}"/"initial.glb").exists())

    def test_nonfinite_positions_are_invalid(self):
        raw = bytearray(glb_bytes())
        struct.pack_into("<f", raw, len(raw)-36, float("nan"))
        self.source.write_bytes(raw)
        self.config = {"kind": "existing_glb", "path": "source.glb"}
        self.assertEqual(self.resolve()["status"], "artifact_invalid")

    def test_variable_photo_counts_duplicate_bytes_and_unknown_angles(self):
        for count in (1, 2, 3, 5, 9):
            photos = [{"id": str(i), "sha256": str(i), "view": "unknown"} for i in range(count)]
            result = select_meshy_views(photos)
            self.assertEqual(len(result["selected"]), min(4, count))
            self.assertEqual(len(result["unused"]), max(0, count-4))
            self.assertFalse(result["diversity_verified_from_pixels"])
        photos = [{"id": "a", "sha256": "same", "view": "front"},
                  {"id": "b", "sha256": "same", "view": "back"},
                  {"id": "c", "sha256": "different", "view": "unknown"}]
        result = select_meshy_views(photos)
        self.assertEqual([p["id"] for p in result["selected"]], ["a", "c"])
        self.assertEqual(result["unused"][0]["unused_reason"], "duplicate_bytes")

    def test_declared_angles_use_diversity_without_assuming_unknown_means_oblique(self):
        photos = [{"id": str(i), "sha256": str(i), "view": "angled", "yaw_degrees": yaw}
                  for i, yaw in enumerate((5, 8, 90, 180, -90))]
        result = select_meshy_views(photos)
        self.assertEqual([p["id"] for p in result["selected"]], ["0", "3", "4", "2"])
        self.assertFalse(result["primary_is_declared_front"])
        self.assertEqual(result, select_meshy_views(photos))

    def test_request_rejects_invalid_ids_pins_options_and_zero_photos(self):
        for config in ({"kind": "typo"}, {"kind": "meshy", "settings": {"image_urls": ["https://a"]}},
                       {"kind": "meshy", "settings": {"should_texture": "true"}},
                       {"kind": "existing_glb", "path": "source.glb", "sha256": "bad"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                resolve_initial_model(config, self.base, self.output, self.photos)
        with self.assertRaises(ValueError):
            resolve_initial_model({"kind": "meshy"}, self.base, self.output, [])
        self.photos[1]["id"] = self.photos[0]["id"]
        with self.assertRaises(ValueError):
            self.resolve()

    def test_meshy_wire_adapter_uses_pinned_bytes_and_separate_public_download(self):
        transport = FakeTransport()
        result = self.resolve(backend=MeshyBackend(transport), allow_submit=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(transport.posts), 1)
        url, payload = transport.posts[0]
        self.assertEqual(url, MESHY_ENDPOINT)
        self.assertEqual(payload["ai_model"], "meshy-7.1")
        self.assertEqual(len(payload["image_urls"]), 4)
        self.assertNotIn("input_task_id", payload)
        for encoded, photo in zip(payload["image_urls"], result["selected"]):
            self.assertEqual(base64.b64decode(encoded.split(",", 1)[1]), Path(photo["path"]).read_bytes())
        self.assertEqual(transport.gets, [MESHY_ENDPOINT + "/task-001"])
        self.assertEqual(transport.downloads[0][0], "https://assets.meshy.ai/result.glb")

    def test_meshy_split_wire_posts_the_pinned_split_payload_to_its_own_transport(self):
        transport, split_transport = FakeTransport(), FakeTransport()
        split_transport.response = {"id": "split-1", "status": "SUCCEEDED", "part_count": 6,
                                    "model_urls": {"glb": "https://assets.meshy.ai/split.glb"}}
        backend = MeshyBackend(transport, split_transport)
        with self.assertRaises(ValueError):
            MeshyBackend(transport, transport)
        self.assertEqual(backend.submit_split({"input_task_id": "task-9", "parts": ["frame front", "left lens"]}), "task-001")
        url, payload = split_transport.posts[0]
        self.assertEqual(url, MESHY_SPLIT_ENDPOINT)
        self.assertEqual(payload, {"mode": "by_parts", "layout": "assembled", "target_formats": ["glb"],
                                   "input_task_id": "task-9", "prompt": "frame front, left lens"})
        self.assertEqual(transport.posts, [])
        observed = backend.retrieve_split("split-1")
        self.assertEqual((observed["status"], observed["part_count"], observed["glb_url"]), ("succeeded", 6, "https://assets.meshy.ai/split.glb"))
        self.assertEqual(split_transport.gets, [MESHY_SPLIT_ENDPOINT + "/split-1"])
        backend.download_split(observed)
        self.assertEqual(split_transport.downloads[0][0], "https://assets.meshy.ai/split.glb")
        self.assertEqual(transport.downloads, [])
        for bad in ({"input_task_id": "task-9", "parts": []}, {"input_task_id": "task-9", "parts": ["a,b"]}, {"parts": ["a"]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                backend.submit_split(bad)
        alone = MeshyBackend(transport)
        for call in (lambda: alone.submit_split({"input_task_id": "t", "parts": ["a"]}), lambda: alone.retrieve_split("t"),
                     lambda: alone.preflight_split({"input_task_id": "t", "parts": ["a"]})):
            with self.assertRaises(ValueError):
                call()

    def test_meshy_wire_rejects_mismatched_task_and_private_download_urls(self):
        transport = FakeTransport()
        backend = MeshyBackend(transport)
        transport.response["id"] = "other"
        with self.assertRaises(ValueError):
            backend.retrieve("task-001")
        transport.response["id"] = "task-001"
        for url in ("http://assets.meshy.ai/model.glb", "https://127.0.0.1/a", "https://localhost/a",
                    "https://user:password@assets.meshy.ai/a", "https://[::1]/a"):
            transport.response["model_urls"]["glb"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                backend.retrieve("task-001")

    def test_backend_mutation_cannot_change_bound_request(self):
        class MutatingBackend(FakeBackend):
            def submit(self, request):
                task_id = super().submit(request)
                request["selection"]["selected"].clear()
                return task_id
        result = self.resolve(backend=MutatingBackend(), allow_submit=True)
        self.assertEqual(len(result["selected"]), 4)
        saved = json.loads((self.output/"request.json").read_text())
        self.assertEqual(len(saved["selection"]["selected"]), 4)


if __name__ == "__main__":
    unittest.main()
