"""Aggregate new-POST accounting across generation and split, without network."""
import json
from pathlib import Path
import tempfile
import unittest

from reconstruction.initializer import MESHY_ENDPOINT, MESHY_SPLIT_ENDPOINT
from reconstruction.meshy_transport import BoundedMeshyTransport, MeshySubmissionBudget, TransportPolicyError
from test_meshy_transport import Network, Response, payload


SPLIT = {"mode": "by_parts", "layout": "assembled", "target_formats": ["glb"],
         "input_task_id": "generation-1", "prompt": "frame front, left lens, right lens"}


class SharedSubmissionBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def client(self, network, name, budget):
        return BoundedMeshyTransport("TEST_ONLY_KEY", max_new_tasks=1, max_estimated_credits=40,
            submission_budget=budget, receipt_dir=self.root / name,
            resolver=network.resolve, connection_factory=network.connect)

    def test_combined_estimate_cannot_exceed_declared_allowance(self):
        budget = MeshySubmissionBudget(max_new_tasks=2, max_estimated_credits=30)
        network = Network([Response(b'{"result":"generation-1"}')])
        self.client(network, "generation", budget).post_json(MESHY_ENDPOINT, payload())
        split = self.client(network, "split", budget)
        with self.assertRaisesRegex(TransportPolicyError, "aggregate"):
            split.post_json(MESHY_SPLIT_ENDPOINT, SPLIT)
        self.assertEqual(len(network.connections), 1)
        self.assertFalse((self.root / "split" / "submission.json").exists())

    def test_two_authorized_posts_record_estimate_separately_from_provider_bill(self):
        budget = MeshySubmissionBudget(max_new_tasks=2, max_estimated_credits=40)
        network = Network([Response(b'{"result":"generation-1"}'), Response(b'{"result":"split-1"}')])
        self.client(network, "generation", budget).post_json(MESHY_ENDPOINT, payload())
        self.client(network, "split", budget).post_json(MESHY_SPLIT_ENDPOINT, SPLIT)
        generation = json.loads((self.root / "generation" / "submission.json").read_bytes())
        split = json.loads((self.root / "split" / "submission.json").read_bytes())
        self.assertEqual(generation["shared_submission_budget"]["reserved_estimated_credits_after"], 30)
        aggregate = split["shared_submission_budget"]
        self.assertEqual((aggregate["reserved_new_tasks_after"], aggregate["reserved_estimated_credits_after"]), (2, 40))
        self.assertEqual(split["estimated_credits"], 10)
        self.assertFalse(aggregate["provider_hard_spend_ceiling"])
        self.assertEqual(aggregate["scope"], "new_POSTs_in_this_explicit_client_session")

    def test_uncertain_post_still_consumes_shared_allowance(self):
        budget = MeshySubmissionBudget(max_new_tasks=2, max_estimated_credits=30)
        network = Network([TimeoutError("connection interrupted")])
        with self.assertRaises(TimeoutError):
            self.client(network, "generation", budget).post_json(MESHY_ENDPOINT, payload())
        with self.assertRaisesRegex(TransportPolicyError, "aggregate"):
            self.client(network, "split", budget).post_json(MESHY_SPLIT_ENDPOINT, SPLIT)
        self.assertEqual(len(network.connections), 1)

    def test_existing_task_poll_does_not_charge_against_new_submission_allowance(self):
        budget = MeshySubmissionBudget(max_new_tasks=1, max_estimated_credits=10)
        network = Network([Response(b'{"id":"generation-1","status":"SUCCEEDED","consumed_credits":30}'),
                           Response(b'{"result":"split-1"}')])
        self.client(network, "generation", budget).get_json(MESHY_ENDPOINT + "/generation-1")
        self.client(network, "split", budget).post_json(MESHY_SPLIT_ENDPOINT, SPLIT)
        split = json.loads((self.root / "split" / "submission.json").read_bytes())
        self.assertEqual(split["shared_submission_budget"]["reserved_estimated_credits_after"], 10)
        self.assertEqual(split["shared_submission_budget"]["reserved_new_tasks_after"], 1)

    def test_shared_task_count_is_enforced_even_with_enough_credits(self):
        budget = MeshySubmissionBudget(max_new_tasks=1, max_estimated_credits=40)
        network = Network([Response(b'{"result":"generation-1"}')])
        self.client(network, "generation", budget).post_json(MESHY_ENDPOINT, payload())
        with self.assertRaisesRegex(TransportPolicyError, "aggregate"):
            self.client(network, "split", budget).post_json(MESHY_SPLIT_ENDPOINT, SPLIT)
        self.assertEqual(len(network.connections), 1)

    def test_failed_exclusive_receipt_write_does_not_debit_allowance(self):
        budget = MeshySubmissionBudget(max_new_tasks=1, max_estimated_credits=30)
        def conflict(_):
            raise FileExistsError("reserved elsewhere")
        with self.assertRaises(FileExistsError):
            budget.reserve(30, conflict)
        self.assertEqual(budget.check(30)["reserved_new_tasks"], 0)


if __name__ == "__main__":
    unittest.main()
