import contextlib
import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reconstruction import job
from reconstruction.initializer import MeshyBackend, resolve_initial_model
from reconstruction.meshy_transport import BoundedMeshyTransport
from PIL import Image
from test_initializer import glb_bytes
from test_meshy_transport import Network, Response


class ProviderCLITests(unittest.TestCase):
    def invoke(self, extra, result=None):
        output = io.StringIO()
        with patch("argparse._", side_effect=lambda text: text), \
             patch.object(job, "run_job", return_value=result or {"status": "awaiting_initializer", "quality_verdict": "unmeasured", "candidate": None}) as run, \
             contextlib.redirect_stdout(output):
            job.main(["--request", "request.json", "--output", "new-job", *extra])
        return run.call_args.kwargs, output.getvalue()

    def test_default_cli_never_reads_credential_environment(self):
        with patch.object(os.environ, "get", side_effect=AssertionError("implicit environment read")):
            args, _ = self.invoke([])
        self.assertIsNone(args["backend"])
        self.assertFalse(args["allow_submit"])

    def test_explicit_env_and_allowance_construct_backend_without_network(self):
        with patch.object(os.environ, "get", return_value="TEST_ONLY_KEY") as lookup:
            args, output = self.invoke(["--meshy-api-key-env", "EXPLICIT_TEST_KEY", "--allow-meshy-submit",
                                       "--meshy-max-new-tasks", "1", "--meshy-max-estimated-credits", "30"])
        lookup.assert_called_once_with("EXPLICIT_TEST_KEY")
        self.assertTrue(args["allow_submit"])
        self.assertEqual(args["backend"].transport.max_new_tasks, 1)
        self.assertEqual(args["backend"].transport.max_estimated_credits, 30)
        self.assertNotIn("TEST_ONLY_KEY", output)

    def test_two_task_allowance_builds_a_separate_split_transport(self):
        with patch.object(os.environ, "get", return_value="TEST_ONLY_KEY"):
            args, _ = self.invoke(["--meshy-api-key-env", "EXPLICIT_TEST_KEY", "--allow-meshy-submit",
                                   "--meshy-max-new-tasks", "2", "--meshy-max-estimated-credits", "40"])
        backend = args["backend"]
        self.assertEqual(backend.transport.max_new_tasks, 1)
        self.assertIsNotNone(backend.split_transport)
        self.assertIsNot(backend.split_transport, backend.transport)
        self.assertEqual(backend.split_transport.max_new_tasks, 1)
        self.assertIs(backend.transport.submission_budget, backend.split_transport.submission_budget)
        self.assertEqual(backend.transport.submission_budget.max_estimated_credits, 40)
        with patch.object(os.environ, "get", return_value="TEST_ONLY_KEY"):
            args, _ = self.invoke(["--meshy-api-key-env", "EXPLICIT_TEST_KEY", "--allow-meshy-submit",
                                   "--meshy-max-new-tasks", "1", "--meshy-max-estimated-credits", "30"])
        self.assertEqual(args["backend"].split_transport.max_new_tasks, 0)

    def test_resume_can_enable_only_known_task_get_without_submission_allowance(self):
        with patch.object(os.environ, "get", return_value="TEST_ONLY_KEY"):
            args, _ = self.invoke(["--meshy-api-key-env", "EXPLICIT_TEST_KEY"])
        self.assertEqual(args["backend"].transport.max_new_tasks, 0)
        self.assertEqual(args["backend"].split_transport.max_new_tasks, 0)
        self.assertFalse(args["allow_submit"])

    def run_initializer_through_cli(self, root, network, options):
        """Exercise CLI transport construction and polling with the real initializer."""
        photos = [{"id": "front", "view": "front", "path": "front.png"}]
        reports = []
        def advance(*args, **kwargs):
            report = resolve_initial_model({"kind": "meshy", "split": {}}, root, root / "initializer",
                                           photos, backend=kwargs["backend"], allow_submit=kwargs["allow_submit"])
            reports.append(report)
            return {"status": "candidate_available" if report["status"] == "complete" else "awaiting_initializer",
                    "quality_verdict": "unmeasured", "candidate": None, "initializer": report}
        def transport(key, **kwargs):
            return BoundedMeshyTransport(key, resolver=network.resolve, connection_factory=network.connect, **kwargs)
        with patch.object(job, "run_job", side_effect=advance), \
             patch("reconstruction.meshy_transport.BoundedMeshyTransport", side_effect=transport), \
             patch.object(os.environ, "get", return_value="TEST_ONLY_KEY"), \
             patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            job.main(["--request", str(root / "request.json"), "--output", str(root / "output"),
                      "--meshy-api-key-env", "TEST_KEY", *options])
        return reports

    def test_wait_keeps_authorization_for_unsent_split_after_generation_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new("RGB", (8, 8), "white").save(root / "front.png")
            network = Network([Response(b'{"result":"generation-1"}'),
                Response(b'{"id":"generation-1","status":"IN_PROGRESS"}'),
                Response(b'{"id":"generation-1","status":"SUCCEEDED","model_urls":{"glb":"https://assets.meshy.ai/gen.glb"}}'),
                Response(glb_bytes()), Response(b'{"result":"split-1"}'),
                Response(b'{"id":"split-1","status":"SUCCEEDED","model_urls":{"glb":"https://assets.meshy.ai/split.glb"}}'),
                Response(glb_bytes())])
            reports = self.run_initializer_through_cli(root, network, ["--allow-meshy-submit",
                "--meshy-max-new-tasks", "2", "--meshy-max-estimated-credits", "40", "--meshy-wait-seconds", "30"])
            self.assertEqual([(r["phase"], r["status"]) for r in reports], [("generation", "pending"), ("complete", "complete")])
            self.assertEqual([r["target"] for r in network.connections if r["method"] == "POST"],
                             ["/openapi/v1/multi-image-to-3d", "/openapi/v1/print/split"])
            receipt = json.loads((root / "initializer" / "split" / "transport" / "submission.json").read_bytes())
            self.assertEqual(receipt["shared_submission_budget"]["reserved_estimated_credits_after"], 40)

    def test_poll_only_cli_resumes_existing_split_with_zero_new_posts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new("RGB", (8, 8), "white").save(root / "front.png")
            network = Network([Response(b'{"result":"generation-1"}'),
                Response(b'{"id":"generation-1","status":"SUCCEEDED","model_urls":{"glb":"https://assets.meshy.ai/gen.glb"}}'),
                Response(glb_bytes()), Response(b'{"result":"split-1"}'),
                Response(b'{"id":"split-1","status":"IN_PROGRESS"}')])
            first = self.run_initializer_through_cli(root, network, ["--allow-meshy-submit",
                "--meshy-max-new-tasks", "2", "--meshy-max-estimated-credits", "40"])
            self.assertEqual((first[-1]["phase"], first[-1]["status"]), ("split", "pending"))
            before = len(network.connections)
            network.responses = [Response(b'{"id":"split-1","status":"SUCCEEDED","model_urls":{"glb":"https://assets.meshy.ai/split.glb"}}'),
                                 Response(glb_bytes())]
            resumed = self.run_initializer_through_cli(root, network, [])
            self.assertEqual(resumed[-1]["status"], "complete")
            self.assertEqual([r["method"] for r in network.connections[before:]], ["GET", "GET"])
            self.assertEqual(network.connections[before]["target"], "/openapi/v1/print/split/split-1")

    def test_submit_requires_both_explicit_limits_and_env_name(self):
        for options in (["--allow-meshy-submit"],
                        ["--meshy-api-key-env", "KEY", "--allow-meshy-submit"],
                        ["--meshy-api-key-env", "KEY", "--allow-meshy-submit", "--meshy-max-new-tasks", "1"],
                        ["--meshy-api-key-env", "KEY", "--meshy-max-estimated-credits", "-1"],
                        ["--meshy-api-key-env", "not-a-variable"],
                        ["--meshy-wait-seconds", "nan"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), \
                 patch.object(job, "run_job") as run, self.assertRaises(SystemExit) as raised:
                job.main(["--request", "request.json", "--output", "new-job", *options])
            self.assertEqual(raised.exception.code, 2)
            run.assert_not_called()

    def test_keyboard_interrupt_is_local_only(self):
        stream = io.StringIO()
        with patch.object(job, "run_job", side_effect=KeyboardInterrupt), \
             contextlib.redirect_stdout(stream), self.assertRaises(SystemExit) as raised:
            job.main(["--request", "request.json", "--output", "new-job"])
        self.assertEqual(raised.exception.code, 130)
        self.assertIn('"provider_task_cancelled": false', stream.getvalue())

    def test_actual_photos_only_job_with_mocked_wire_pins_initial_artifact_without_quality_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            photo = root / "front.png"
            Image.new("RGB", (64, 48), "white").save(photo)
            Image.new("RGB", (64, 48), (240, 240, 240)).save(root / "angled.png")
            request = root / "request.json"
            request.write_text(json.dumps({"schema_version": 1, "photos": [{"id": "front", "view": "front", "path": "front.png"},
                                                                            {"id": "angled", "view": "angled", "path": "angled.png"}],
                                           "initializer": {"kind": "meshy"}}), encoding="utf-8")
            net = Network([Response(b'{"result":"task-1"}'),
                           Response(b'{"id":"task-1","status":"SUCCEEDED","progress":100,"consumed_credits":30,"model_urls":{"glb":"https://assets.meshy.ai/model.glb"}}'),
                           Response(glb_bytes())])
            backend = MeshyBackend(BoundedMeshyTransport("TEST_KEY", max_new_tasks=1, max_estimated_credits=30,
                                    resolver=net.resolve, connection_factory=net.connect))
            result = job.run_job(request, root / "output", resolution=128, camera_evaluations=20,
                                 backend=backend, allow_submit=True)
            self.assertEqual(result["status"], "candidate_available")
            self.assertEqual(result["quality_verdict"], "unmeasured")
            self.assertFalse(result["quality"]["accepted"])
            self.assertEqual((root / "output" / "candidate.glb").read_bytes(), glb_bytes())
            self.assertEqual(result["initializer"]["provider_observation"]["consumed_credits"], 30)
            journal = json.loads((root / "output" / "job.json").read_bytes())
            initial = journal["stages"]["initializer"][0]
            pinned = initial["artifacts"]
            self.assertTrue(any(path.endswith("transport/submission.json") for path in pinned))
            self.assertEqual(sum(path.endswith("response.bin") for path in pinned), 3)
            for path, expected in pinned.items():
                self.assertEqual(hashlib.sha256((root / "output" / path).read_bytes()).hexdigest(), expected)
            count = len(net.connections)
            self.assertEqual(job.run_job(request, root / "output", resolution=128, camera_evaluations=20), result)
            self.assertEqual(len(net.connections), count)


if __name__ == "__main__":
    unittest.main()
