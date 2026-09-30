"""Trial accounting and real OS-lock witnesses; all model calls are mocked."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import httpx

from blender_agent.budget import BudgetExceeded
from blender_agent.trial_budget import TrialBudget, TrialBudgetError


class Counter:
    base_url = "https://api.openai.com/v1/"

    def __init__(self, tokens=1000):
        self.tokens = tokens
        self.responses = SimpleNamespace(input_tokens=SimpleNamespace(count=self.count))

    def with_options(self, **kwargs):
        return self

    async def count(self, **kwargs):
        return SimpleNamespace(input_tokens=self.tokens)


class TrialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.counter = Counter()

    def request(self, output=100):
        return httpx.Request("POST", "https://api.openai.com/v1/responses", json={
            "model": "gpt-6-astra", "service_tier": "default", "stream": False,
            "max_output_tokens": output, "input": "offline fixture",
        })

    def invocation(self, name):
        path = self.output / "runs" / name
        path.mkdir(parents=True)
        return path

    def usage(self, tokens=1000, cached=0):
        return {"input_tokens": tokens, "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0}}

    async def test_two_invocations_cannot_reset_seventeen_dollar_trial(self):
        first = self.invocation("first")
        self.counter.tokens = 500000
        with TrialBudget(self.output, "17", False, current_invocation=first) as trial:
            guard = trial.reserve_invocation(self.counter, first / "budget.json")
            await guard.before_request(self.request(8192))
            await guard.settle_latest(self.usage(500000))
            self.assertEqual(trial.remaining_usd(), "6.999925")
        second = self.invocation("second")
        with TrialBudget(self.output, "17", True, current_invocation=second) as trial:
            self.assertEqual(trial.remaining_usd(), "6.999925")
            guard = trial.reserve_invocation(self.counter, second / "budget.json")
            self.assertEqual(guard.summary()["maximum_usd"], "6.999925")
            with self.assertRaises(BudgetExceeded):
                await guard.before_request(self.request(8192))
            self.assertEqual(trial.summary(guard)["settled_usd"], "10.000075")

    async def test_unknown_outcome_and_missing_result_keep_full_hold_after_resume(self):
        first = self.invocation("unknown")
        with TrialBudget(self.output, "0.03", False, current_invocation=first) as trial:
            guard = trial.reserve_invocation(self.counter, first / "budget.json")
            await guard.before_request(self.request())
        self.assertFalse((first / "result.json").exists())
        with TrialBudget(self.output, "0.03", True) as trial:
            self.assertEqual(trial.remaining_usd(), "0.012500")
            self.assertEqual(trial.summary()["unknown_reserved_usd"], "0.017500")

    async def test_cache_settlement_is_repriced_correctly_from_flat_persisted_usage(self):
        first = self.invocation("cached")
        with TrialBudget(self.output, "0.03", False, current_invocation=first) as trial:
            guard = trial.reserve_invocation(self.counter, first / "budget.json")
            await guard.before_request(self.request())
            await guard.settle_latest(self.usage(cached=1000))
        with TrialBudget(self.output, "0.03", True) as trial:
            self.assertEqual(trial.summary()["settled_usd"], "0.001050")
            self.assertEqual(trial.remaining_usd(), "0.028950")

    def test_cap_is_frozen_and_legacy_resume_never_invents_one(self):
        with self.assertRaisesRegex(TrialBudgetError, "Legacy output"):
            with TrialBudget(self.output, "17", True):
                pass
        with TrialBudget(self.output, "17", False):
            pass
        for amount in ("18", "16"):
            with self.subTest(amount=amount), self.assertRaisesRegex(TrialBudgetError, "frozen"):
                with TrialBudget(self.output, amount, True):
                    pass
        with self.assertRaisesRegex(TrialBudgetError, "--resume"):
            with TrialBudget(self.output, "17", False):
                pass

    def test_preexisting_history_cannot_be_adopted_as_a_new_trial(self):
        self.invocation("old")
        with self.assertRaisesRegex(TrialBudgetError, "legacy-budget"):
            with TrialBudget(self.output, "17", False):
                pass
        self.assertFalse((self.output / "trial-budget.json").exists())

    def test_missing_ledgers_fail_except_explicit_current_directory(self):
        with TrialBudget(self.output, "17", False):
            pass
        current = self.invocation("new")
        with self.assertRaisesRegex(TrialBudgetError, "Missing or malformed"):
            with TrialBudget(self.output, "17", True):
                pass
        with TrialBudget(self.output, "17", True, current_invocation=current) as trial:
            trial.reserve_invocation(self.counter, current / "budget.json")
        with self.assertRaisesRegex(TrialBudgetError, "cannot hide"):
            with TrialBudget(self.output, "17", True, current_invocation=current):
                pass

    async def test_malformed_tampered_and_violated_ledgers_fail_closed(self):
        first = self.invocation("first")
        path = first / "budget.json"
        with TrialBudget(self.output, "17", False, current_invocation=first) as trial:
            guard = trial.reserve_invocation(self.counter, path)
            await guard.before_request(self.request())
            await guard.settle_latest(self.usage())
        original = json.loads(path.read_text())
        invalid = []
        for change in ("amount", "settlement", "tariff", "violation", "sequence"):
            ledger = json.loads(json.dumps(original))
            if change == "amount": ledger["reserved_micro_usd"] = 0
            if change == "settlement": ledger["reservations"][0]["settled_micro_usd"] = 0
            if change == "tariff": ledger["tariff"]["input_per_million"] = 0
            if change == "violation": ledger["violation"] = {"reason": "overshoot"}
            if change == "sequence": ledger["reservations"][0]["sequence"] = True
            invalid.append(json.dumps(ledger))
        invalid.extend(["{}", "{partial", "null"])
        for content in invalid:
            path.write_text(content)
            with self.subTest(content=content[:50]), self.assertRaises(TrialBudgetError):
                with TrialBudget(self.output, "17", True):
                    pass

    def test_only_one_guard_and_only_inside_trial_while_lock_held(self):
        trial = TrialBudget(self.output, "17", False)
        with self.assertRaises(TrialBudgetError):
            trial.remaining_usd()
        with trial:
            with self.assertRaises(TrialBudgetError):
                trial.reserve_invocation(self.counter, self.output / "outside.json")
            first = self.invocation("first")
            trial.reserve_invocation(self.counter, first / "budget.json")
            with self.assertRaisesRegex(TrialBudgetError, "Only one"):
                trial.reserve_invocation(self.counter, self.output / "runs/second/budget.json")
        with self.assertRaises(TrialBudgetError):
            trial.summary()

    def test_second_process_is_excluded_and_crash_releases_os_lock(self):
        # A real child process owns the lock. Killing it leaves the lock file but
        # releases the OS lock, so resume does not depend on stale PID cleanup.
        code = (
            "import sys; from blender_agent.trial_budget import TrialBudget; "
            "trial=TrialBudget(sys.argv[1], '17', False); trial.__enter__(); "
            "print('locked', flush=True); sys.stdin.readline()"
        )
        process = subprocess.Popen([sys.executable, "-c", code, str(self.output)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(TrialBudgetError, "Another process"):
                with TrialBudget(self.output, "17", True):
                    pass
            process.kill()
            process.wait(timeout=10)
            self.assertTrue((self.output / ".trial-budget.lock").exists())
            # Windows may finish releasing byte-range locks shortly after the
            # process handle signals exit. No file/PID cleanup is necessary.
            deadline = time.monotonic() + 2
            while True:
                try:
                    with TrialBudget(self.output, "17", True) as trial:
                        self.assertEqual(trial.remaining_usd(), "17.000000")
                    break
                except TrialBudgetError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.01)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
