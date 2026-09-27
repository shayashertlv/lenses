"""The Astra author driver against a mocked HTTP session: one ledger reservation per call, cap enforcement, tool
dispatch and validation, no credential in any artifact. No network."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from modeler import author_astra


class FakeResponse:
    def __init__(self, body: dict, status: int = 200):
        self.status_code = status
        self._raw = json.dumps(body).encode("utf-8")

    def iter_content(self, chunk_size=65536):
        yield self._raw

    def close(self):
        pass


class FakeSession:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.posts = []

    def post(self, url, **kw):
        self.posts.append(kw)
        body = self.bodies.pop(0)
        return FakeResponse(body)


def response_with_call(name, arguments):
    return {"status": "completed", "model": "gpt-6-astra", "id": "resp_1", "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "x"},
                       {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": name, "status": "completed",
                        "arguments": json.dumps(arguments)}]}


class AstraDriver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        img = self.root / "photo.jpg"
        Image.fromarray(np.full((20, 30, 3), 200, np.uint8)).save(img)
        self.images = [{"id": "photo_front", "label": "front", "path": str(img)}]
        self.request = {"task": "t", "run_state": {"turn": 0}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_submit_program_dispatch_and_ledger(self):
        modules = {m: None for m in author_astra.MODULE_ORDER}
        modules["frame"] = "x = 1\n"
        session = FakeSession([response_with_call("submit_program", {"modules": modules, "base": None, "rationale": "r", "expected_changes": ["e"], "deliver_if_valid": False})])
        driver = author_astra.AstraAuthorDriver("sk-test-secret", budget_path=self.root / "ledger.json", maximum_calls=2, cap_usd=16.0, session=session)
        decision, meta = driver.decide(self.root / "turn-0000", self.request, self.images, log=lambda *a: None)
        self.assertEqual(decision["decision"], "submit_program")
        self.assertEqual(decision["modules"], {"frame": "x = 1\n"})
        ledger = json.loads((self.root / "ledger.json").read_text())
        self.assertEqual(len(ledger["reservations"]), 1)
        usd = json.loads((self.root / "ledger.json.usd.json").read_text())
        self.assertEqual(len(usd["entries"]), 1)
        self.assertAlmostEqual(usd["spent_usd"], author_astra.actual_cost_usd({"input_tokens": 10, "output_tokens": 5}), places=6)
        self.assertEqual(meta["cost_usd"], usd["spent_usd"])
        payload = json.loads(session.posts[0]["data"])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertFalse(payload["store"])
        self.assertEqual({t["name"] for t in payload["tools"]}, {"submit_program", "request_views", "finish"})
        # the credential never lands in the turn folder
        for p in (self.root / "turn-0000").rglob("*"):
            if p.is_file():
                self.assertNotIn("sk-test-secret", p.read_text(encoding="utf-8", errors="ignore"))

    def test_cap_enforced_and_failure_consumes_slot(self):
        session = FakeSession([{"status": "incomplete", "model": "gpt-6-astra", "output": [], "incomplete_details": {"reason": "max_output_tokens"}},
                               response_with_call("finish", {"deliver": "incumbent", "status_claim": "best_effort", "note": "n"})])
        driver = author_astra.AstraAuthorDriver("sk-test-secret", budget_path=self.root / "ledger.json", maximum_calls=1, cap_usd=16.0, session=session)
        with self.assertRaises(RuntimeError):
            driver.decide(self.root / "turn-0000", self.request, self.images, log=lambda *a: None)
        receipt = json.loads((self.root / "turn-0000" / "api" / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed_or_uncertain")
        # the cap of one is spent: the second turn is refused before any request is sent
        with self.assertRaises(RuntimeError):
            driver.decide(self.root / "turn-0001", self.request, self.images, log=lambda *a: None)
        self.assertEqual(len(session.posts), 1)

    def test_dollar_cap_refuses_before_sending(self):
        session = FakeSession([response_with_call("finish", {"deliver": "incumbent", "status_claim": "best_effort", "note": "n"})])
        driver = author_astra.AstraAuthorDriver("sk-test-secret", budget_path=self.root / "ledger.json", maximum_calls=5, cap_usd=0.5, session=session)
        with self.assertRaises(RuntimeError):
            driver.decide(self.root / "turn-0000", self.request, self.images, log=lambda *a: None)
        self.assertEqual(len(session.posts), 0)

    def test_4xx_failure_charges_nothing(self):
        class Session400(FakeSession):
            def post(self, url, **kw):
                self.posts.append(kw)
                return FakeResponse({"error": {"message": "bad"}}, status=400)
        session = Session400([])
        driver = author_astra.AstraAuthorDriver("sk-test-secret", budget_path=self.root / "ledger2.json", maximum_calls=5, cap_usd=16.0, session=session)
        with self.assertRaises(RuntimeError):
            driver.decide(self.root / "t2-0000", self.request, self.images, log=lambda *a: None)
        usd = json.loads((self.root / "ledger2.json.usd.json").read_text())
        self.assertEqual(usd["spent_usd"], 0.0)
        self.assertEqual(usd["entries"][0]["outcome"], "failed")

    def test_evaluator_driver_forced_tool(self):
        ev = {"discrepancies": [{"part": "frame", "view": "front", "description": "d", "severity": "minor"}],
              "identity_checklist": [{"feature": "f", "verdict": "present", "note": ""}],
              "resemblance_0_10": {"front": 7, "side": 6, "angled_held_out": 6, "materials_and_lenses": 7, "mirror_overall": 7},
              "runtime_notes": "", "overall": "accept", "summary": "s"}
        session = FakeSession([response_with_call("report_evaluation", ev)])
        driver = author_astra.AstraEvaluatorDriver("sk-test-secret", budget_path=self.root / "ledger.json", maximum_calls=3, cap_usd=16.0, session=session)
        from modeler import evaluate as mevaluate
        decision, meta = driver.decide(self.root / "eval", self.request, self.images, role="evaluator", schema_check=mevaluate.validate_evaluation, log=lambda *a: None)
        self.assertEqual(decision["overall"], "accept")
        payload = json.loads(session.posts[0]["data"])
        self.assertEqual([t["name"] for t in payload["tools"]], ["report_evaluation"])

    def test_refuses_without_cap(self):
        with self.assertRaises(ValueError):
            author_astra.AstraAuthorDriver("sk", budget_path=self.root / "l.json", maximum_calls=0, cap_usd=16.0)
        with self.assertRaises(ValueError):
            author_astra.AstraAuthorDriver("", budget_path=self.root / "l.json", maximum_calls=1, cap_usd=16.0)


class ApiEvaluatorPath(unittest.TestCase):
    """modeler.evaluate_asset.evaluate_with_driver: the API evaluator answers a prepared package and the answer is
    collected like an agent's (no Blender, no harness: prepare is stubbed)."""

    def test_astra_evaluator_answers_a_package(self):
        from modeler import evaluate_asset
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "front.jpg"
            Image.fromarray(np.full((20, 30, 3), 180, np.uint8)).save(img)
            asset = root / "candidates" / "c0001"
            out = asset / "evaluation_v2_astra"
            out.mkdir(parents=True)

            def fake_prepare(job_dir, asset_dir, *, force_renders=False, evaluation_dir="evaluation_v2"):
                (out / "request.json").write_text(json.dumps({"protocol": "modeler_evaluator_v2.2", "task": "t", "identity_checklist": ["f"],
                                                              "asset_sha256": "ab" * 32, "images": [{"id": "photo_front", "file": "front.jpg"}]}), encoding="utf-8")
                (out / "images.json").write_text(json.dumps([{"id": "photo_front", "label": "front", "path": str(img)}]), encoding="utf-8")
                return out / "request.json"

            ev = {"discrepancies": [{"part": "lenses", "view": "front", "description": "tint", "severity": "minor"}],
                  "identity_checklist": [{"feature": "f", "verdict": "present", "note": ""}],
                  "resemblance_0_10": {"front": 8, "side": 7, "angled_held_out": 7, "materials_and_lenses": 7, "mirror_overall": 8},
                  "runtime_notes": "", "overall": "accept", "summary": "fine"}
            session = FakeSession([response_with_call("report_evaluation", ev)])
            driver = author_astra.AstraEvaluatorDriver("sk-test-secret", budget_path=root / "ledger.json", maximum_calls=2, cap_usd=8.0, session=session)
            original = evaluate_asset.prepare
            evaluate_asset.prepare = fake_prepare
            try:
                rec = evaluate_asset.evaluate_with_driver(root, asset, driver, evaluation_dir="evaluation_v2_astra", log=lambda *a: None)
            finally:
                evaluate_asset.prepare = original
            self.assertEqual(rec["evaluation"]["overall"], "accept")
            self.assertEqual(rec["meta"]["driver"], "astra")
            self.assertEqual(rec["asset_sha256"], "ab" * 32)
            stored = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["evaluation"]["resemblance_0_10"]["mirror_overall"], 8.0)
            self.assertTrue((out / "api" / "receipt.json").exists())
            payload = json.loads(session.posts[0]["data"])
            self.assertEqual({t["name"] for t in payload["tools"]}, {"report_evaluation"})
            self.assertIn("mirror_overall", payload["tools"][0]["parameters"]["properties"]["resemblance_0_10"]["required"])
            for p in out.rglob("*"):
                if p.is_file():
                    self.assertNotIn("sk-test-secret", p.read_text(encoding="utf-8", errors="ignore"))


class RealisticEstimate(unittest.TestCase):
    def test_output_budget_follows_observed_usage(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = author_astra.DollarLedger(Path(td) / "usd.json", 10.0)
            self.assertIsNone(ledger.observed_output_tokens())
            ledger.charge(0.4, role="author", turn="turn-0000", outcome="complete", usage={"input_tokens": 30000, "output_tokens": 4100})
            ledger.charge(0.3, role="author", turn="turn-0001", outcome="failed", usage={"input_tokens": 30000, "output_tokens": 24000})
            ledger.charge(0.5, role="evaluator", turn="evaluation", outcome="complete", usage={"input_tokens": 20000, "output_tokens": 5000})
            self.assertEqual(ledger.observed_output_tokens("author"), 4100)
            self.assertEqual(ledger.observed_output_tokens(), 5000)
            # the estimate with the observed budget is well below the 24k-ceiling estimate
            full = author_astra.estimate_cost_usd(120000, 8, author_astra.MAX_OUTPUT_TOKENS)["usd"]
            realistic = author_astra.estimate_cost_usd(120000, 8, max(6000, int(4100 * 1.5)))["usd"]
            self.assertLess(realistic, full * 0.6)


if __name__ == "__main__":
    unittest.main()
