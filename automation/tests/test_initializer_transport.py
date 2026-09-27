"""Real adapter/initializer state machine with an entirely mocked HTTP wire."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from reconstruction.initializer import MeshyBackend, resolve_initial_model
from reconstruction.meshy_transport import BoundedMeshyTransport
from test_initializer import glb_bytes
from test_meshy_transport import Network, Response


def task(url="https://assets.meshy.ai/old.glb", status="SUCCEEDED", progress=100, credits=30, task_id="task-1"):
    return Response(json.dumps({"id": task_id, "status": status, "progress": progress,
                               "consumed_credits": credits, "model_urls": {"glb": url}}).encode())


class InitializerTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.output = self.base / "stage"
        Image.new("RGB", (8, 6), (13, 80, 160)).save(self.base / "front.png")
        self.photos = [{"id": "front", "view": "front", "path": "front.png"}]

    def backend(self, network, credits=30, count=1):
        return MeshyBackend(BoundedMeshyTransport("TEST_KEY", max_new_tasks=count, max_estimated_credits=credits,
                            resolver=network.resolve, connection_factory=network.connect))

    def resolve(self, backend=None, allow_submit=False):
        return resolve_initial_model({"kind": "meshy"}, self.base, self.output, self.photos,
                                     backend=backend, allow_submit=allow_submit)

    def test_pending_expired_url_refresh_and_offline_resume_preserve_all_receipts(self):
        net = Network([Response(b'{"result":"task-1"}'), task(status="IN_PROGRESS", progress=42)])
        result = self.resolve(self.backend(net), True)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["provider_observation"]["progress"], 42)
        self.assertEqual(result["provider_observation"]["consumed_credits"], 30)
        net.responses.extend([task(), Response(b"<Code>ExpiredToken</Code>", 403),
                              task("https://cdn.example/fresh.glb"), Response(glb_bytes())])
        result = self.resolve(self.backend(net, count=0, credits=0))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["provider_observation"]["consumed_credits"], 30)
        self.assertEqual(result["provider_observation"]["glb_url"], "https://cdn.example/fresh.glb")
        self.assertEqual((self.output / "initial.glb").read_bytes(), glb_bytes())
        self.assertEqual([x["method"] for x in net.connections].count("POST"), 1)
        self.assertEqual([x["target"] for x in net.connections].count("/openapi/v1/multi-image-to-3d/task-1"), 3)
        terminal = self.output / "terminal_task.json"
        self.assertEqual(json.loads(terminal.read_bytes())["glb_url"], "https://assets.meshy.ai/old.glb")
        refreshes = list(self.output.glob("task-refresh-*.json"))
        self.assertEqual(len(refreshes), 1)
        refreshed = json.loads(refreshes[0].read_bytes())
        self.assertEqual(refreshed["prior_terminal_sha256"], hashlib.sha256(terminal.read_bytes()).hexdigest())
        self.assertEqual(refreshed["task"]["glb_url"], "https://cdn.example/fresh.glb")
        self.assertEqual(len(list(self.output.glob("task-observation-*.json"))), 2)
        self.assertEqual(len(list((self.output / "transport").glob("http-*/response.bin"))), 6)
        count = len(net.connections)
        self.assertEqual(self.resolve(), result)
        self.assertEqual(len(net.connections), count)

    def test_budget_preflight_rejection_does_not_reserve_uncertain_submission(self):
        net = Network([])
        result = self.resolve(self.backend(net, credits=29), True)
        self.assertEqual(result["status"], "submission_not_authorized")
        self.assertFalse((self.output / "submit_request.json").exists())
        self.assertEqual(net.connections, [])
        net.responses = [Response(b'{"result":"task-1"}'), task(), Response(glb_bytes())]
        self.assertEqual(self.resolve(self.backend(net), True)["status"], "complete")
        self.assertEqual([x["method"] for x in net.connections].count("POST"), 1)

    def test_generic_forbidden_does_not_refresh_and_resume_reuses_terminal_task(self):
        net = Network([Response(b'{"result":"task-1"}'), task(), Response(b"AccessDenied", 403)])
        self.assertEqual(self.resolve(self.backend(net), True)["status"], "download_error")
        self.assertEqual(len(net.connections), 3)
        self.assertEqual(list(self.output.glob("task-refresh-*.json")), [])
        net.responses = [Response(glb_bytes())]
        self.assertEqual(self.resolve(self.backend(net, count=0, credits=0))["status"], "complete")
        self.assertEqual(len(net.connections), 4)
        self.assertEqual(net.connections[-1]["target"], "/old.glb")

    def test_expiry_refresh_is_bounded_and_never_replaces_a_task(self):
        net = Network([Response(b'{"result":"task-1"}'), task(), Response(b"Request has expired", 403),
                       task("https://cdn.example/new.glb"), Response(b"Request has expired", 403)])
        result = self.resolve(self.backend(net), True)
        self.assertEqual(result["status"], "download_error")
        self.assertTrue(result["url_refresh_attempted"])
        self.assertEqual(len(net.connections), 5)
        self.assertEqual([x["method"] for x in net.connections].count("POST"), 1)
        self.assertFalse((self.output / "initial.glb").exists())

    def test_refresh_wrong_task_cannot_download_or_create_candidate(self):
        net = Network([Response(b'{"result":"task-1"}'), task(), Response(b"ExpiredToken", 403), task(task_id="wrong")])
        result = self.resolve(self.backend(net), True)
        self.assertEqual(result["status"], "download_error")
        self.assertEqual(result["error_type"], "ValueError")
        self.assertEqual(len(net.connections), 4)
        self.assertFalse((self.output / "initial.glb").exists())


if __name__ == "__main__":
    unittest.main()
