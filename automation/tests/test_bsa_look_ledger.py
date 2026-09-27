"""bsa.look + bsa.pipeline: the paid-call ledger of the S11 astra driver. Hermetic: the LookFixture's temp BSA_DATA and
fake AR harness, a fake requests.Session (no socket is opened), a fake credential that must never be written.

The 2026-09-25 safety review: each product had a ledger inside its own s11_look/ and every pipeline re-run passed
--fresh, which renamed that folder with its ledger; a failure after the paid call (or a Ctrl-C mid-call) followed by
the same command spent the cap again, one command could spend 5 caps, and a fresh session could not reuse an
owner-named ledger (its request folder was already reserved). Now one owner-named ledger outside every s11_look
bounds the authorization, and each session names its request folders uniquely."""
import contextlib
import io
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from bsa import core, pipeline
from bsa import look as L
import reconstruction.segmented_astra_transport as TR
from test_bsa_look import FINISH, PROD, RUN, LookFixture, frame_op, plan
from test_segmented_astra_transport import HTTP, result

KEY = "fixture-not-a-real-key-0123456789"


class FakeSession(HTTP):
    def mount(self, *a, **k):
        pass


class LedgerTest(LookFixture):
    def setUp(self):
        super().setUp()
        self.http = FakeSession(result(FINISH))
        self.ledger = self.root / "ledgers" / "owner-authorization.json"
        for p in (mock.patch.object(TR.requests, "Session", lambda: self.http),
                  mock.patch.dict(os.environ, {"OPENAI_API_KEY": KEY}),
                  mock.patch.object(pipeline, "CODE_CHECK", False)):
            p.start()
            self.addCleanup(p.stop)

    def opts(self, cap=1, turns=None):
        return pipeline.look_options("astra", authorize_paid_astra=True, astra_maximum_calls=cap,
                                     astra_budget=self.ledger, astra_max_turns=turns)

    def look_all(self, opts, products=(PROD,)):
        with contextlib.redirect_stdout(io.StringIO()):
            return pipeline.look_all(list(products), RUN, from_stage="s0_intake", force=False, opts=opts,
                                     log=lambda *a: None)

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return L.main(["--run", RUN, "--product", PROD, "--driver", "astra", "--authorize-paid-astra",
                           "--astra-budget", str(self.ledger), *args])

    def reservations(self) -> list:
        return json.loads(self.ledger.read_text())["reservations"] if self.ledger.exists() else []

    def assert_no_key_written(self):
        for p in self.root.rglob("*"):
            if p.is_file():
                self.assertNotIn(KEY.encode(), p.read_bytes(), p)

    def test_a_rerun_after_a_post_call_failure_spends_nothing_more(self):
        real, calls = L.finalize, {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("fixture: the sheet copy failed after the paid call")
            return real(*a, **k)
        with mock.patch.object(L, "finalize", flaky):
            ex = self.look_all(self.opts(cap=1))
        self.assertIn("OSError", ex[0]["error"])
        failed = json.loads((self.sd / "result.json").read_text())
        self.assertEqual((failed["status"], failed["paid_calls_used"], failed["ledger"]["reservations"]), ("failed", 1, 1))
        ex = self.look_all(self.opts(cap=1))                      # the same command again
        self.assertIn("paid-call ledger exhausted (1 of 1", ex[0]["skipped"])
        self.assertEqual(len(self.http.calls), 1)
        self.assertEqual(len(self.reservations()), 1)
        self.assertFalse(list(self.sd.parent.glob(f"{L.STAGE}.superseded-*")))     # an exhausted ledger displaces nothing
        self.assert_no_key_written()

    def test_ctrl_c_during_the_call_leaves_a_failed_record_and_the_cap_holds(self):
        post = self.http.post

        def interrupted(url, **kw):
            post(url, **kw)
            raise KeyboardInterrupt
        self.http.post = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.look_all(self.opts(cap=1))
        rec = json.loads((self.sd / "result.json").read_text())
        self.assertEqual((rec["status"], rec["error_type"], rec["paid_calls_used"]), ("failed", "KeyboardInterrupt", 1))
        self.http.post = post
        ex = self.look_all(self.opts(cap=1))
        self.assertIn("exhausted", ex[0]["skipped"])
        self.assertEqual(len(self.http.calls), 1)

    def test_one_ledger_bounds_the_whole_command(self):
        # a product listed twice runs once; a stale re-run of a delivered paid look spends only what is left
        ex = self.look_all(self.opts(cap=1), products=(PROD, PROD))
        self.assertEqual([(e["status"], e["paid_calls_used"]) for e in ex], [("delivered", 1)])
        self.assertEqual(ex[0]["ledger"]["reservations"], 1)
        s10 = core.run_dir(RUN, PROD) / "s10_gate" / "result.json"
        later = time.time() + 5                                          # an S10 re-run: the paid look is stale
        os.utime(s10, (later, later))
        ex = self.look_all(self.opts(cap=1))
        self.assertIn("exhausted", ex[0]["skipped"])
        self.assertEqual(json.loads((self.sd / "result.json").read_text())["verdict"], "improved")   # kept in place
        self.assertEqual(len(self.http.calls), 1)
        # another authorization (another cap) on the same ledger is refused, nothing sent
        ex = self.look_all(self.opts(cap=3))
        self.assertIn("records a cap of 1, not 3", ex[0]["skipped"])
        self.assertEqual(len(self.http.calls), 1)

    def test_turns_follow_the_calls_left(self):
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text(json.dumps({"protocol": TR.PROTOCOL, "maximum_calls": 2, "reservations": [
            {"ordinal": 1, "request_dir": str(self.root / "another" / "turns" / "turn-0000" / "api-0"),
             "request_sha256": "0" * 64, "reserved_unix": 0}]}))
        self.http.body = result(plan(frame_op(metallic=0.2)))          # the editor would edit on turn 0 ...
        res = self.cli("--astra-maximum-calls", "2")                     # ... with 3 turns by default
        ctx = self.turn_input(0)["context"]
        self.assertEqual(ctx["turns_remaining_including_this"], 1)      # but 1 call is left: it is told 1 turn
        self.assertEqual(res["driver"]["maximum_turns"], 1)
        self.assertEqual([t["status"] for t in res["turns"]], ["applied"])   # no refused, "uncertain" extra turn
        self.assertEqual((res["paid_calls_used"], res["ledger"]["reservations"]), (1, 2))
        # the edit came on the last (only) turn: never delivered unseen
        self.assertEqual((res["final_revision"], res["unreviewed_revision"]), ("r0000", "r0001"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.cli("--astra-maximum-calls", "2", "--fresh")          # nothing left: refused before anything moves
        self.assertEqual(len(self.http.calls), 1)
        self.assertFalse(list(self.sd.parent.glob(f"{L.STAGE}.superseded-*")))    # the paid session stays in place
        self.assertEqual(json.loads((self.sd / "result.json").read_text())["final_revision"], "r0000")

    def test_a_fresh_session_reuses_an_owner_ledger(self):
        first = self.cli("--astra-maximum-calls", "3", "--astra-max-turns", "1")
        second = self.cli("--astra-maximum-calls", "3", "--astra-max-turns", "1", "--fresh")
        self.assertEqual([(r["verdict"], r["paid_calls_used"]) for r in (first, second)], [("improved", 1), ("improved", 1)])
        dirs = [r["request_dir"] for r in self.reservations()]
        self.assertEqual(len(dirs), 2)
        self.assertEqual(len({Path(d).name for d in dirs}), 2)          # api-<session id>: never the same folder twice
        self.assertEqual(second["ledger"]["reservations"], 2)
        self.assertEqual(len(self.http.calls), 2)

    def test_a_ledger_inside_s11_look_is_refused(self):
        inside = core.run_dir(RUN, PROD) / L.STAGE / "astra_budget.json"
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            L.main(["--run", RUN, "--product", PROD, "--driver", "astra", "--authorize-paid-astra",
                    "--astra-budget", str(inside), "--astra-maximum-calls", "1"])
        for path in (inside, self.sd.parent / f"{L.STAGE}.superseded-x" / "b.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "outside every s11_look"):
                pipeline.look_options("astra", authorize_paid_astra=True, astra_maximum_calls=1, astra_budget=path)
        with self.assertRaisesRegex(ValueError, "--look-astra-budget PATH"):
            pipeline.look_options("astra", authorize_paid_astra=True, astra_maximum_calls=1)
        self.assertEqual(self.http.calls, [])


if __name__ == "__main__":
    unittest.main()
