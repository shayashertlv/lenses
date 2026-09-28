"""Recovery regressions for ``modeler.agentic.runner``: the lease fence around every inference, the unknown-outcome guard
before every paid send, and the sealed-evidence boundary through the intake.

Offline like the other agentic suites: synthetic photos, ``ScriptedTransport``, ``FakeWorker``; no network, no Blender,
no Docker. Each test proves an externally observable fact (rows through ``Store``, the transport's captured payloads,
the job state and its exit code), never an implementation detail.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock

from PIL import Image

from modeler.agentic import cli, config, demo, executor, responses, runner
from modeler.agentic.artifacts import AUTHOR_VISIBLE, SEALED
from modeler.agentic.budget import Budget
from modeler.agentic.state import LeaseError, Store
from test_agentic_session import CRITIC_FAIL, deliver_step, edit_step, fetch_step, message_text, outputs_for, output_text, sent, step, text_only_step, window
from test_agentic_state import FakeClock
from test_agentic_support import fresh_dir, rmtree_reporting

KEEP = bool(os.environ.get("LAG_SESSION_KEEP"))
INTRUDER = "intruder@elsewhere:00000000"
NULL_LOG = lambda *a, **k: None   # noqa: E731


class LeaseStealingTransport(responses.ScriptedTransport):
    """The count call of the ``steal_at``-th author request outlives the lease: while the count is in flight, time passes
    the TTL and another runner takes the job. The reply itself is the ordinary scripted count."""

    def __init__(self, steps, *, job_dir: Path, clock: FakeClock, steal_at: int):
        super().__init__(steps)
        self.job_dir = job_dir
        self.clock = clock
        self.steal_at = steal_at
        self.counts = 0
        self.stolen_fence = None

    def post(self, endpoint, payload, *, timeout=(15, 300)):
        if endpoint == responses.ENDPOINT_COUNT:
            self.counts += 1
            if self.counts == self.steal_at:
                self.clock.advance(runner.LEASE_TTL_S + 60)
                intruder = Store.open(self.job_dir, clock=self.clock)
                try:
                    self.stolen_fence = intruder.acquire_lease(INTRUDER, 600)
                finally:
                    intruder.close()
        return super().post(endpoint, payload, timeout=timeout)


class RecoveryCase(unittest.TestCase):
    def setUp(self):
        self.root = fresh_dir(None, "lease-and-unknown", "r-", env="LAG_RECOVERY_TMP")
        self.stores: list[Store] = []

    def tearDown(self):
        for s in self.stores:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        if not KEEP:
            rmtree_reporting(self.root)

    def policy(self, **over) -> dict:
        base = dict(driver="scripted", worker="fake", intake="synthetic", critic="scripted", final_evaluator="scripted", ar=False, budget_usd="5",
                    max_inference_requests=12, max_output_tokens=4000, max_revisions=4, max_worker_seconds=120, wall_minutes=30, images_per_request=6)
        base.update(over)
        return config.build_policy(**{"owner_review": False, **base})

    def make(self, transport, *, request: dict | None = None, inputs: Path | None = None, clock=None, scenario: dict | None = None, name: str = "job",
             **policy_over) -> runner.Session:
        inputs = inputs or self.root / f"{name}.in"
        request = request or demo.demo_request(inputs)
        translated = config.translate_request(request, inputs)
        session = runner.Session.create(self.root / name, translated=translated, policy=self.policy(**policy_over), fingerprints={"test": "recovery"},
                                        worker=executor.FakeWorker(scenario), transport=transport, worker_config=None, log=NULL_LOG, clock=clock)
        self.stores.append(session.store)
        return session

    def crop_request(self, inputs: Path) -> dict:
        """The demo request plus a crop of the held-out (angled, magenta) photo that the request itself declares NOT held out."""
        photos = demo.synthetic_photos(inputs)
        angled = next(p for p in photos if p["view"] == "angled")
        box = (40, 60, 150, 140)
        with Image.open(angled["path"]) as im:
            im.crop(box).save(inputs / "crop.png")
        photos.append({"path": str(inputs / "crop.png"), "view": "other", "held_out": False, "id": "crop", "source_photo_id": "angled", "crop_xyxy": list(box)})
        return {"product_id": "crop-leak", "photos": photos, "dimensions": {}, "held_out_views": ["angled"],
                "notes": "SYNTHETIC: a crop of the sealed photo declared as an author photo"}


# =========================================================================== (3a) the lease fence around inference
class LeaseFenceAroundInference(RecoveryCase):
    def test_a_runner_whose_lease_was_taken_over_during_the_count_posts_nothing_and_appends_nothing(self):
        clock = FakeClock()
        steps = [step("call_1", "list_evidence", {}), text_only_step("never", "this response must never be requested")]
        transport = LeaseStealingTransport(steps, job_dir=self.root / "job", clock=clock, steal_at=2)
        session = self.make(transport, clock=clock)
        store = session.store
        own_fence = session.fence
        with self.assertRaises(LeaseError):
            session.run()
        # the steal happened: the second author request's count outlived the lease and the intruder holds the job
        self.assertEqual(transport.counts, 2)
        self.assertEqual(transport.stolen_fence, own_fence + 1)
        job = store.job()
        self.assertEqual((job["lease_holder"], job["lease_fence"]), (INTRUDER, own_fence + 1), "run() must not release or renew a stolen lease")
        # nothing was posted after the steal and no request row was left behind
        self.assertEqual(len(sent(transport, "responses")), 1, "the stale runner posted a second author request")
        self.assertEqual(len(transport.steps), 1, "the scripted step after the steal was consumed")
        self.assertEqual([(r["id"], r["purpose"], r["state"]) for r in store.requests()], [("q0001", "author", "completed")])
        self.assertEqual(store.reservations(state="held"), [], "no reservation was taken under the stale fence")
        # nothing was appended to the conversation after the tool cycle that preceded the steal
        items = window(store)
        self.assertTrue(all(r["request_id"] in (None, "q0001") for r in store.items("author")), "items were appended for a request the stale runner had no right to send")
        self.assertEqual(items[-1]["type"], "function_call_output")
        self.assertEqual(items[-1]["call_id"], "call_1")
        self.assertEqual(len(outputs_for(items, "call_1")), 1)
        self.assertEqual(len(store.operations()), 1)
        totals = Budget(store).totals()
        self.assertEqual((totals["held_micro"], totals["unknown_liability_micro"]), (0, 0))
        self.assertEqual(cli.EXIT_BY_STATE.get(store.state(), cli.EXIT_FAILED), 3, "a lease refusal is reported as exit 3 by cli.run_session")
        # the new holder reconciles into a clean 'ready' without any unknown liability: the interrupted step left no trace to repair
        intruder_store = Store.open(self.root / "job", clock=clock)
        self.stores.append(intruder_store)
        s2 = runner.Session(intruder_store, transport=responses.ScriptedTransport([]), worker=executor.FakeWorker(), log=NULL_LOG, holder=INTRUDER)
        s2.acquire()
        s2.reconcile()
        self.assertEqual(intruder_store.state(), "ready")
        self.assertEqual(runner.status_report(intruder_store)["unknown_requests"], [])
        self.assertEqual([r["id"] for r in intruder_store.requests()], ["q0001"])

    def test_a_lease_taken_over_during_the_compaction_count_posts_no_compaction(self):
        """Compaction goes through the same fenced path as every other inference: a lease lost during its count call means no
        /compact POST, no request row, no new epoch."""
        clock = FakeClock()
        steps = [edit_step("call_1", None, demo.PROGRAM_A), text_only_step("never", "no compaction and no author request may follow")]
        transport = LeaseStealingTransport(steps, job_dir=self.root / "job", clock=clock, steal_at=2)
        transport.count_rule = lambda payload: 30_000            # every count is above the compaction threshold
        session = self.make(transport, clock=clock, images_per_request=14, compact_threshold_tokens=20_000, compact_output_bound_tokens=4096)
        store = session.store
        own_fence = session.fence
        with self.assertRaises(LeaseError):
            session.run()
        self.assertEqual(transport.counts, 2, "the second count is the compaction's")
        self.assertEqual(transport.stolen_fence, own_fence + 1)
        self.assertEqual(sent(transport, "compact"), [], "the stale runner posted a compaction")
        self.assertEqual([(r["purpose"], r["state"]) for r in store.requests()], [("author", "completed")], "a compaction request row was created under a stale fence")
        self.assertEqual(store.epoch("author"), 1)
        self.assertEqual(store.events("compaction_sent"), [])
        self.assertEqual(store.reservations(state="held"), [])
        self.assertEqual(len(transport.steps), 1)

    def test_the_fence_is_required_before_a_response_is_applied(self):
        clock = FakeClock()
        transport = responses.ScriptedTransport([step("call_1", "list_evidence", {})])
        session = self.make(transport, clock=clock)
        store = session.store
        parsed = responses.parse_response_body(responses.canonical(demo._resp("call_x", "list_evidence", {}, rs="rs_x")), model=session.model)
        res = session.budget.reserve(role="author", purpose="author", input_tokens=1000, max_output_tokens=4000)
        req = store.insert_request(role="author", purpose="author", epoch=store.epoch("author"), input_sha256="0" * 64, reservation_id=res["id"], input_token_count=1000)
        clock.advance(runner.LEASE_TTL_S + 60)
        intruder = Store.open(self.root / "job", clock=clock)
        self.stores.append(intruder)
        intruder.acquire_lease(INTRUDER, 600)
        before = len(store.items("author"))
        with self.assertRaises(LeaseError):
            session.handle_response(req, parsed)
        self.assertEqual(len(store.items("author")), before, "a stale runner applied a response")
        self.assertEqual(store.operations(call_id="call_x"), [])
        with self.assertRaises(LeaseError):
            session.single_call(role="critic", purpose="critic", developer="x", blocks=[responses.text_block("x")], tools=runner.CRITIC_TOOL,
                                tool_name="report_critique", max_output_tokens=100)
        self.assertEqual(store.items("critic"), [], "a stale runner opened a critic epoch")
        self.assertEqual(sent(transport, "responses"), [])
        self.assertEqual(sent(transport, "count"), [], "a stale runner counted tokens for a request it may not send")


# =========================================================================== (3b) no paid send while an outcome is unknown
class UnknownOutcomeStopsFurtherInference(RecoveryCase):
    def steps(self) -> list[dict]:
        return [edit_step("call_1", None, demo.PROGRAM_A),
                fetch_step("call_2"),
                step("call_3", "request_critic", {"revision_id": "r0001", "question": "what is visibly wrong?"}),
                {"endpoint": "responses", "raise": "unknown", "expect": {"tools": ["report_critique"]}},
                text_only_step("never", "this response must never be requested")]

    def test_unknown_critic_outcome_commits_the_tool_output_then_stops_before_the_next_author_send(self):
        transport = responses.ScriptedTransport(self.steps())
        session = self.make(transport)
        store = session.store
        state = session.run()
        self.assertEqual(state, "needs_attention")
        job = store.job()
        self.assertEqual(job["stop_reason"], "inference_unknown")
        self.assertIsNone(job["lease_holder"], "run() released its lease")
        critic = store.requests(role="critic")
        self.assertEqual(len(critic), 1)
        critic = critic[0]
        self.assertEqual(critic["state"], "unknown")
        res = store.reservation(critic["reservation_id"])
        self.assertEqual((res["state"], res["liability_micro"]), ("unknown", res["reserved_micro"]))
        self.assertEqual(Budget(store).totals()["unknown_liability_micro"], res["reserved_micro"], "the liability equals the whole reservation")
        self.assertEqual(runner.status_report(store)["unknown_requests"], [critic["id"]])
        # three author requests and the critic's own: nothing after the unknown outcome
        self.assertEqual([(r["role"], r["purpose"], r["state"]) for r in store.requests()],
                         [("author", "author", "completed")] * 3 + [("critic", "critic", "unknown")])
        self.assertEqual(len(sent(transport, "responses")), 4, "a paid request was posted while an unknown outcome was outstanding")
        self.assertEqual(len(transport.steps), 1, "the scripted step after the unknown outcome was consumed")
        # the failed tool output was committed first: exactly one output for the critic call, naming the unknown outcome
        items = window(store)
        outs = outputs_for(items, "call_3")
        self.assertEqual(len(outs), 1)
        text = output_text(outs[0])
        self.assertEqual(text["tool"], "request_critic")
        self.assertIn("unknown", text["error"].lower())
        op = store.operations(call_id="call_3")[0]
        self.assertEqual((op["state"], op["output_committed"]), ("failed", 1))
        self.assertEqual(store.verdicts(kind="critic"), [])
        self.assertEqual([e["data"]["requests"] for e in store.events("inference_refused_unknown_outstanding")], [[critic["id"]]])
        self.assertFalse((store.job_dir / "deliverable").exists())
        self.assertEqual(cli.EXIT_BY_STATE[state], 3)

    def test_reconcile_unknown_then_resume_continues_with_a_new_author_request_and_never_reposts(self):
        transport = responses.ScriptedTransport(self.steps())
        session = self.make(transport)
        store = session.store
        self.assertEqual(session.run(), "needs_attention")
        critic = store.requests(role="critic")[0]
        res_id = critic["reservation_id"]
        job_dir = str(store.job_dir)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["reconcile-unknown", "--job", job_dir, "--request", critic["id"], "--authorized-by", "owner", "--no-charge"])
        self.assertEqual(code, 0)
        row = store.request(critic["id"])
        self.assertEqual(row["state"], "failed")
        self.assertIn("reconciled by owner", row["error"])
        reservation = store.reservation(res_id)
        self.assertEqual((reservation["state"], reservation["liability_micro"], reservation["settled_micro"]), ("settled", 0, 0))
        self.assertEqual(Budget(store).totals()["unknown_liability_micro"], 0)
        self.assertEqual(store.state(), "needs_attention", "the stop stays until the owner resumes with --acknowledge-attention")
        # resume: one new author request (a delivery of the revision the author has seen) closes the synthetic job
        script = self.root / "resume.json"
        script.write_text(json.dumps({"steps": [deliver_step("call_4", "r0001", expect={"contains_call_output": "call_3"})], "fake_scenario": {}}), encoding="utf-8")
        store.close()
        with contextlib.redirect_stdout(out):
            code = cli.main(["resume", "--job", job_dir, "--script", str(script), "--acknowledge-attention"])
        self.assertEqual(code, 0)
        store2 = Store.open(Path(job_dir), readonly=True)
        self.stores.append(store2)
        self.assertEqual(store2.state(), "unresolved")
        rows = store2.requests()
        self.assertEqual([(r["id"], r["role"], r["state"]) for r in rows],
                         [("q0001", "author", "completed"), ("q0002", "author", "completed"), ("q0003", "author", "completed"),
                          ("q0004", "critic", "failed"), ("q0005", "author", "completed")])
        self.assertEqual(len([r for r in rows if r["role"] == "critic"]), 1, "the unknown critic request was posted again")
        self.assertEqual([e["data"]["request"] for e in store2.events("inference_sent") if e["data"]["request"] == "q0004"], ["q0004"], "q0004 was sent exactly once")
        self.assertEqual(store2.events("inference_unknown")[0]["data"]["request"], "q0004")
        self.assertEqual(runner.status_report(store2)["unknown_requests"], [])
        self.assertEqual(Budget(store2).totals()["unknown_liability_micro"], 0)
        items = [r["item"] for r in store2.items("author")]
        self.assertEqual(len(outputs_for(items, "call_3")), 1)
        self.assertEqual(len(outputs_for(items, "call_4")), 1)


# =========================================================================== (3b') the liability is the reservation's, whatever the HTTP outcome
class UnknownLiabilityFromHttpOutcomes(RecoveryCase):
    """A 5xx leaves the critic request 'failed' and a 200 without a usage block leaves it 'completed', but both leave the
    reservation 'unknown': the job must stop exactly as after a timeout, `status` must list the request under
    unknown_requests, and `reconcile-unknown` must accept it (and refuse it a second time)."""

    def steps(self, critic_reply: dict) -> list[dict]:
        return [edit_step("call_1", None, demo.PROGRAM_A), fetch_step("call_2"),
                step("call_3", "request_critic", {"revision_id": "r0001", "question": "what is visibly wrong?"}),
                dict(critic_reply, expect={"tools": ["report_critique"]}),
                text_only_step("never", "this response must never be requested")]

    def check(self, critic_reply: dict, expected_state: str) -> None:
        transport = responses.ScriptedTransport(self.steps(critic_reply))
        session = self.make(transport)
        store = session.store
        self.assertEqual(session.run(), "needs_attention")
        self.assertEqual(store.job()["stop_reason"], "inference_unknown")
        critic = store.requests(role="critic")[0]
        self.assertEqual(critic["state"], expected_state)
        res = store.reservation(critic["reservation_id"])
        self.assertEqual((res["state"], res["liability_micro"]), ("unknown", res["reserved_micro"]))
        self.assertEqual(runner.status_report(store)["unknown_requests"], [critic["id"]])
        self.assertEqual(len(sent(transport, "responses")), 4, "a paid request was posted while an unknown liability was outstanding")
        self.assertEqual(len(transport.steps), 1, "the scripted step after the unknown liability was consumed")
        self.assertEqual(len(outputs_for(window(store), "call_3")), 1, "exactly one output for the critic call")
        out = io.StringIO()
        job_dir = str(store.job_dir)
        with contextlib.redirect_stdout(out):
            code = cli.main(["reconcile-unknown", "--job", job_dir, "--request", critic["id"], "--authorized-by", "owner", "--no-charge"])
        self.assertEqual(code, 0, out.getvalue())
        res2 = store.reservation(critic["reservation_id"])
        self.assertEqual((res2["state"], res2["liability_micro"], res2["settled_micro"]), ("settled", 0, 0))
        self.assertEqual(Budget(store).totals()["unknown_liability_micro"], 0)
        self.assertEqual(runner.status_report(store)["unknown_requests"], [])
        row = store.request(critic["id"])
        self.assertIn("reconciled by owner", row["error"])
        self.assertEqual(row["state"], expected_state, "the HTTP outcome stays on the row; only a timeout's 'unknown' row is closed as failed")
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["reconcile-unknown", "--job", job_dir, "--request", critic["id"], "--authorized-by", "owner", "--no-charge"]), 2,
                             "a settled reservation cannot be reconciled twice")

    def test_http_500_on_the_critic_stops_the_job_and_is_reconcilable(self):
        self.check({"endpoint": "responses", "status": 500, "body": {"error": {"type": "server_error", "message": "upstream"}}}, "failed")

    def test_completed_critic_response_without_usage_stops_the_job_and_is_reconcilable(self):
        body = demo._resp("call_c", "report_critique", CRITIC_FAIL, rs="rs_c")
        body.pop("usage", None)
        self.check({"endpoint": "responses", "status": 200, "body": body}, "completed")


# =========================================================================== (4) the sealed boundary through the intake
class SealedCropNeverReachesTheIntake(RecoveryCase):
    def assert_first_message_carries_no_crop(self, store: Store) -> None:
        items = window(store)
        first = items[0]
        self.assertEqual((first["type"], first["role"]), ("message", "user"))
        text = message_text(first)          # every text block of the message: the host package, the photo labels, the trailer
        self.assertIn("Host package", text)
        self.assertNotIn('"crop"', text, "the author's first message names the sealed crop")
        head = json.loads(first["content"][0]["text"].split("\n", 1)[1])
        self.assertNotIn("crop", head["evidence"].get("views") or {})
        visible_photos = store.artifacts(role=AUTHOR_VISIBLE, kind="photo")
        self.assertEqual(len(visible_photos), 3, "front, back and left are the only author-visible photos")
        self.assertEqual([p["artifact_id"] for p in head["photos_available"]], [a["id"] for a in visible_photos])
        self.assertFalse(any("photo crop," in (a["label"] or "") for a in visible_photos), "the crop is catalogued as author-visible")
        self.assertTrue(any("photo crop," in (a["label"] or "") for a in store.artifacts(role=SEALED, kind="photo")), "the crop is not catalogued as sealed")
        visible = (store.job_dir / "evidence" / "author_visible.json").read_text(encoding="utf-8")
        self.assertNotIn('"crop"', visible)

    def test_synthetic_and_none_intake_list_the_crop_under_held_out_only(self):
        for mode in ("synthetic", "none"):
            with self.subTest(intake=mode):
                inputs = self.root / f"{mode}.in"
                request = self.crop_request(inputs)
                session = self.make(responses.ScriptedTransport([]), request=request, inputs=inputs, name=mode, intake=mode)
                store = session.store
                sealed = store.setting("sealed_reservation")
                self.assertEqual(sorted(sealed["sealed_ids"]), ["angled", "crop"], "the crop's ancestry seals it")
                translated = store.setting("request_translation")["request"]
                self.assertFalse(next(p for p in translated["photos"] if p["id"] == "crop")["held_out"], "the request itself declares the crop NOT held out")
                evidence = store.setting("evidence")
                self.assertIn("crop", evidence["held_out"])
                self.assertIn("angled", evidence["held_out"])
                self.assertNotIn("crop", evidence["views"])
                self.assertEqual(sorted(evidence["views"]), ["back", "front", "left"])
                flags = {r["id"]: r["held_out"] for r in evidence["inputs"]}
                self.assertEqual(flags, {"front": False, "back": False, "left": False, "angled": True, "crop": True})
                self.assertEqual(store.events("intake")[0]["data"]["measured"], False)
                self.assert_first_message_carries_no_crop(store)
                session.release()

    def test_code_intake_receives_the_crop_as_held_out(self):
        import modeler.intake as mintake
        inputs = self.root / "code.in"
        request = self.crop_request(inputs)
        captured = {}

        def fake_run_intake(req, job_dir, *, front_width_mm, width_provenance, **kw):
            captured["request"] = req
            views = {p.path.stem: {"view": p.view, "flags": ["fake"], "size": [1, 1]} for p in req.photos if not p.held_out}
            held = {p.path.stem: {"id": p.path.stem, "view": p.view} for p in req.photos if p.held_out}
            return {"product_id": req.product_id, "notes": req.notes, "scale": {"front_width_mm": front_width_mm, **width_provenance}, "dimensions_stated": req.dimensions,
                    "views": views, "front": None, "sides": {}, "held_out": held,
                    "inputs": [{"id": p.path.stem, "view": p.view, "held_out": p.held_out} for p in req.photos]}

        with mock.patch.object(mintake, "run_intake", side_effect=fake_run_intake):
            session = self.make(responses.ScriptedTransport([]), request=request, inputs=inputs, name="code", intake="code")
        store = session.store
        req = captured["request"]
        by_name = {p.path.name: p for p in req.photos}
        self.assertEqual(sorted(by_name), ["angled.png", "back.png", "crop.png", "front.png", "left.png"])
        self.assertTrue(by_name["crop.png"].held_out, "modeler.intake received the sealed crop as an author photo")
        self.assertTrue(by_name["angled.png"].held_out)
        self.assertEqual([n for n, p in sorted(by_name.items()) if not p.held_out], ["back.png", "front.png", "left.png"])
        self.assertEqual(set(req.held_out_views), {"angled", "other"}, "held_out_views follows the sealed set, not the request's flags")
        self.assertEqual(sorted(store.events("intake")[0]["data"]["held_out"]), ["angled", "crop"])
        evidence = store.setting("evidence")
        self.assertNotIn("crop", evidence["views"])
        self.assertIn("crop", evidence["held_out"])
        self.assert_first_message_carries_no_crop(store)


# =========================================================================== long replies and host operations (2026-09-28)
class LeaseCapturingTransport(responses.ScriptedTransport):
    """Records, at the moment of each responses POST, how long the job's lease still runs."""

    def __init__(self, steps, *, job_dir: Path, clock: FakeClock):
        super().__init__(steps)
        self.job_dir, self.clock, self.lease_left = job_dir, clock, []

    def post(self, endpoint, payload, *, timeout=(15, 300)):
        if endpoint == responses.ENDPOINT:
            from modeler.agentic.state import parse_iso
            s = Store.open(self.job_dir, clock=self.clock, readonly=True)
            try:
                self.lease_left.append(((parse_iso(s.job()["lease_expires_utc"]) - self.clock()).total_seconds(), timeout[1]))
            finally:
                s.close()
        return super().post(endpoint, payload, timeout=timeout)


class LongRepliesAndHostOperations(RecoveryCase):
    def test_the_lease_outlives_the_read_timeout_of_the_longest_legal_reply(self):
        """INF-02: a 24,000-token author reply may take ~1000 s at 25 tokens/s; the lease renewed before the POST must not lapse
        while the runner waits for it (a lapsed lease lets another runner take the job mid-request)."""
        clock = FakeClock()
        transport = LeaseCapturingTransport([deliver_step("call_1", "r0404")], job_dir=self.root / "job", clock=clock)
        session = self.make(transport, clock=clock, max_output_tokens=24_000)
        session.author_step()
        (left, read_s), = transport.lease_left
        self.assertGreater(read_s, runner.LEASE_TTL_S, "the case this test is about: a read timeout longer than the old fixed lease")
        self.assertGreaterEqual(left, read_s + runner.LEASE_MARGIN_S)
        session.release()

    def test_an_interrupted_host_seed_build_owes_no_function_call_output(self):
        """The seed build is a host operation with no function_call; a crash during it must not make reconcile append a
        function_call_output for a call that does not exist (the replayed window would be invalid)."""
        session = self.make(responses.ScriptedTransport([]))
        store = session.store
        op = store.insert_operation(call_id=runner.HOST_CALL_PREFIX + "seed_build_r0001", request_id=None, tool_name="build_candidate",
                                    schema_version="agentic_tools_v1", args={"revision_id": "r0001", "deliver_if_compatible": False}, source_revision_id=None,
                                    fence=session.fence, state="running")
        session.reconcile()
        items = window(store)
        self.assertFalse(any(i.get("type") == "function_call_output" for i in items), items)
        row = store.operation(op["id"])
        self.assertEqual((row["state"], row["output_committed"]), ("interrupted", 1))
        session.release()


if __name__ == "__main__":
    unittest.main()
