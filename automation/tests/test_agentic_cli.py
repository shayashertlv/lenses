"""The new CLI's contract: exit codes, refusal before any credential is read, the offline demo, status/resume/cancel/owner-verdict.

Offline: the synthetic worker and the scripted transport only; no network, no paid call. Every job lives in a fresh
short temporary folder (Windows path length)."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from modeler.agentic import cli
from modeler.agentic.config import EXIT_BUDGET, EXIT_CANCELLED, EXIT_FAILED, EXIT_INVALID, EXIT_OK
from modeler.agentic.state import Store
from test_agentic_support import fresh_dir



def fresh(owner, name: str) -> Path:
    """A new folder under <short tmp>/lag-cli, removed at the owner's (test or class) cleanup."""
    return fresh_dir(owner, "cli", name + "-")


class DemoAndStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = fresh(cls, "demo")
        cls.job = cls.root / "job"
        cls.code = cli.main(["demo", "--output", str(cls.job), "--worker", "fake"])

    def test_demo_exits_zero_with_an_honest_synthetic_result(self):
        self.assertEqual(self.code, EXIT_OK)
        m = json.loads((self.job / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((m["state"], m["stop_reason"], m["deliverable_status"]), ("unresolved", "synthetic_demo_complete", "synthetic_only"))
        self.assertIsNone(m["asset"])
        self.assertFalse((self.job / "deliverable" / "model.glb").exists())
        self.assertEqual(m["exit_semantics"]["budget_exhausted"], EXIT_BUDGET)
        self.assertGreaterEqual(len(m["revisions"]), 2)
        self.assertTrue(all(r["synthetic"] for r in m["revisions"]))

    def test_status_json_is_an_export_of_the_database(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["status", "--job", str(self.job), "--json"])
        self.assertEqual(code, EXIT_OK)
        rep = json.loads(buf.getvalue())
        store = Store.open(self.job, readonly=True)
        self.assertEqual(rep["state"], store.state())
        self.assertEqual(rep["budget"]["operations_used"], len([r for r in store.reservations() if r["state"] in ("held", "settled", "unknown")]))
        self.assertEqual(rep["observations"]["pending"], 0)
        self.assertIn("unknown_liability_usd", rep["budget"])

    def test_resume_of_a_finished_job_is_a_no_op_with_exit_zero(self):
        code = cli.main(["resume", "--job", str(self.job), "--script", str(self.root / "job.inputs" / "script.json")])
        self.assertEqual(code, EXIT_OK)

    def test_cancel_of_a_finished_job_changes_nothing(self):
        before = Store.open(self.job, readonly=True).state()
        code = cli.main(["cancel", "--job", str(self.job)])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(Store.open(self.job, readonly=True).state(), before)

    def test_owner_verdict_needs_a_delivered_asset(self):
        code = cli.main(["owner-verdict", "--job", str(self.job), "--verdict", "accept", "--medium", "test"])
        self.assertEqual(code, EXIT_INVALID)

    def test_demo_refuses_a_non_empty_output(self):
        self.assertEqual(cli.main(["demo", "--output", str(self.job), "--worker", "fake"]), EXIT_INVALID)


class InvalidConfigurations(unittest.TestCase):
    def setUp(self):
        self.root = fresh(self, "invalid")
        from modeler.agentic.demo import demo_request, write_demo_script
        self.request = demo_request(self.root / "inputs")
        self.req_path = self.root / "inputs" / "request.json"
        self.req_path.write_text(json.dumps(self.request), encoding="utf-8")
        self.script = write_demo_script(self.root / "inputs" / "script.json", self.request["photos"])

    def start(self, *extra) -> int:
        return cli.main(["start", "--request", str(self.req_path), "--output", str(self.root / "job"), *extra])

    def test_paid_driver_without_allow_paid_is_refused_before_any_credential_is_read(self):
        # a credential source that does not exist would fail loudly if it were opened; the policy refusal comes first
        code = self.start("--driver", "responses", "--worker", "fake", "--env", str(self.root / "missing.env"))
        self.assertEqual(code, EXIT_INVALID)
        self.assertFalse((self.root / "job").exists())

    def test_paid_driver_needs_every_cap(self):
        code = self.start("--driver", "responses", "--allow-paid", "--worker", "native-fixture", "--fixture-program-sha256", "0" * 64,
                          "--budget-usd", "15", "--env", str(self.root / "missing.env"))
        self.assertEqual(code, EXIT_INVALID)         # max-inference-requests etc. are missing

    def test_paid_driver_with_the_synthetic_worker_is_refused(self):
        code = self.start("--driver", "responses", "--allow-paid", "--worker", "fake", "--budget-usd", "1", "--max-inference-requests", "1",
                          "--max-output-tokens", "1000", "--max-revisions", "1", "--max-worker-seconds", "60")
        self.assertEqual(code, EXIT_INVALID)

    def test_scripted_driver_needs_a_script(self):
        self.assertEqual(self.start("--driver", "scripted", "--worker", "fake", "--intake", "synthetic"), EXIT_INVALID)

    def test_invalid_limits_are_refused(self):
        for bad in (["--max-revisions", "0"], ["--budget-usd", "-1"], ["--budget-usd", "nan"], ["--max-output-tokens", "100"], ["--wall-minutes", "0"]):
            with self.subTest(bad=bad):
                self.assertEqual(self.start("--driver", "scripted", "--script", str(self.script), "--worker", "fake", "--intake", "synthetic", *bad), EXIT_INVALID)

    def test_docker_worker_is_refused_without_a_passing_doctor(self):
        cfg = self.root / "worker.json"
        cfg.write_text(json.dumps({"image_digest": "lenses-agentic-worker@sha256:" + "a" * 64, "entry": "/opt/lenses/worker_entry.py"}), encoding="utf-8")
        code = self.start("--driver", "scripted", "--script", str(self.script), "--worker", "docker", "--worker-config", str(cfg), "--intake", "synthetic")
        self.assertEqual(code, EXIT_INVALID)
        self.assertFalse((self.root / "job").exists())

    def test_synthetic_intake_is_only_for_the_synthetic_worker(self):
        code = self.start("--driver", "scripted", "--script", str(self.script), "--worker", "native-fixture", "--fixture-program-sha256", "0" * 64, "--intake", "synthetic")
        self.assertEqual(code, EXIT_INVALID)

    def test_intake_astra_is_refused_up_front_with_the_reason(self):
        """AG-09: 'astra' was offered by --intake but could never run (the scripted driver refused it as 'paid', the paid
        driver as 'not wired'); argparse now refuses it before anything runs and says why, and build_policy says the same."""
        import contextlib
        import io
        from modeler.agentic.config import INTAKE_NOT_WIRED, INTAKES, ConfigError, build_policy
        self.assertNotIn("astra", INTAKES)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.start("--driver", "scripted", "--script", str(self.script), "--worker", "fake", "--intake", "astra")
        self.assertEqual(cm.exception.code, EXIT_INVALID)
        self.assertIn("not wired", err.getvalue())
        self.assertFalse((self.root / "job").exists())
        for driver_kw in ({"driver": "scripted", "worker": "fake"},
                          {"driver": "responses", "worker": "docker", "allow_paid": True, "budget_usd": "1", "max_inference_requests": 1,
                           "max_output_tokens": 1000, "max_revisions": 1, "max_worker_seconds": 60}):
            with self.subTest(driver=driver_kw["driver"]), self.assertRaises(ConfigError) as ce:
                build_policy(intake="astra", **driver_kw)
            self.assertEqual(str(ce.exception), INTAKE_NOT_WIRED, "one reason for every driver, never a misleading 'paid' one")
        for ok in ("none", "code", "synthetic", "scripted"):
            with self.subTest(intake=ok):
                self.assertEqual(build_policy(driver="scripted", worker="fake", intake=ok)["intake"], ok)

    def test_request_carrying_a_budget_is_refused(self):
        bad = dict(self.request, budget_usd=100)
        p = self.root / "inputs" / "bad.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        code = cli.main(["start", "--request", str(p), "--output", str(self.root / "job"), "--driver", "scripted", "--script", str(self.script),
                         "--worker", "fake", "--intake", "synthetic"])
        self.assertEqual(code, EXIT_INVALID)


class StopReasonsAndExitCodes(unittest.TestCase):
    def test_budget_exhaustion_wins_over_a_fallback_asset(self):
        # exit-code mapping is a pure table: budget_exhausted -> 4, cancelled -> 130, failed/needs_attention -> 3, delivered/unresolved -> 0
        self.assertEqual(cli.EXIT_BY_STATE["budget_exhausted"], EXIT_BUDGET)
        self.assertEqual(cli.EXIT_BY_STATE["cancelled"], EXIT_CANCELLED)
        self.assertEqual(cli.EXIT_BY_STATE["failed"], EXIT_FAILED)
        self.assertEqual(cli.EXIT_BY_STATE["needs_attention"], EXIT_FAILED)
        self.assertEqual(cli.EXIT_BY_STATE["delivered"], EXIT_OK)
        self.assertEqual(cli.EXIT_BY_STATE["unresolved"], EXIT_OK)

    def test_cancel_marks_a_live_job_without_a_runner_cancelled(self):
        from modeler.agentic.config import build_policy, translate_request
        from modeler.agentic.demo import demo_request
        from modeler.agentic.executor import FakeWorker
        from modeler.agentic.responses import ScriptedTransport
        from modeler.agentic.runner import Session
        root = fresh(self, "cancel")
        request = demo_request(root / "inputs")
        translated = translate_request(request, root / "inputs")
        policy = build_policy(owner_review=False, driver="scripted", worker="fake", intake="synthetic", ar=False)
        session = Session.create(root / "job", translated=translated, policy=policy, fingerprints={"test": True}, worker=FakeWorker(),
                                 transport=ScriptedTransport([]), worker_config=None)
        session.release()                                             # the runner went away; the job is 'ready'
        code = cli.main(["cancel", "--job", str(root / "job")])
        self.assertEqual(code, EXIT_CANCELLED)
        store = Store.open(root / "job", readonly=True)
        self.assertEqual(store.state(), "cancelled")
        self.assertEqual(store.job()["stop_reason"], "cancelled")
        self.assertEqual(cli.main(["resume", "--job", str(root / "job"), "--script", str(root / "inputs" / "x.json")]), EXIT_CANCELLED)
        events = [e["kind"] for e in store.events()]
        self.assertIn("cancel_fenced", events, "cancel takes a new fence so a runner whose lease expired mid-tool can write nothing more")

    def test_reconcile_unknown_settles_the_liability_and_closes_the_request_once(self):
        from modeler.agentic.budget import Budget
        from modeler.agentic.config import build_policy, translate_request
        from modeler.agentic.demo import demo_request
        from modeler.agentic.executor import FakeWorker
        from modeler.agentic.responses import ScriptedTransport
        from modeler.agentic.runner import Session
        root = fresh(self, "reconcile")
        request = demo_request(root / "inputs")
        translated = translate_request(request, root / "inputs")
        policy = build_policy(owner_review=False, driver="scripted", worker="fake", intake="synthetic", ar=False)
        session = Session.create(root / "job", translated=translated, policy=policy, fingerprints={"test": True}, worker=FakeWorker(),
                                 transport=ScriptedTransport([]), worker_config=None)
        store, budget = session.store, session.budget
        # the shape a crash after send leaves behind: a sent request, no durable response -> reconcile marks it unknown with the whole reservation as liability
        res = budget.reserve(role="author", purpose="author", input_tokens=1000, max_output_tokens=4000)
        req = store.insert_request(role="author", purpose="author", epoch=store.epoch("author"), input_sha256="0" * 64, reservation_id=res["id"], input_token_count=1000)
        budget.bind_request(res["id"], req["id"])
        store.update_request(req["id"], state="unknown", error="sent; no durable response (restart)")
        budget.mark_unknown(res["id"], "test", request_id=req["id"])
        store.transition("needs_attention", "unknown", stop_reason="inference_unknown")
        session.release()
        self.assertEqual(Budget(store).totals()["unknown_liability_micro"], res["reserved_micro"])
        # both or neither switch is refused; a request that is not unknown is refused
        self.assertEqual(cli.main(["reconcile-unknown", "--job", str(root / "job"), "--request", req["id"], "--authorized-by", "owner"]), EXIT_INVALID)
        self.assertEqual(cli.main(["reconcile-unknown", "--job", str(root / "job"), "--request", "q0404", "--authorized-by", "owner", "--no-charge"]), EXIT_INVALID)
        code = cli.main(["reconcile-unknown", "--job", str(root / "job"), "--request", req["id"], "--authorized-by", "owner: dashboard shows no call", "--no-charge"])
        self.assertEqual(code, EXIT_OK)
        store2 = Store.open(root / "job", readonly=True)
        row = store2.request(req["id"])
        self.assertEqual(row["state"], "failed")
        self.assertIn("reconciled by owner", row["error"])
        reservation = store2.reservation(res["id"])
        self.assertEqual((reservation["state"], reservation["settled_micro"], reservation["liability_micro"]), ("settled", 0, 0))
        self.assertEqual(Budget(store2).totals()["unknown_liability_micro"], 0)
        self.assertEqual(store2.state(), "needs_attention", "the stop stays until the owner resumes with --acknowledge-attention")
        self.assertEqual([e["data"]["request"] for e in store2.events("request_reconciled")], [req["id"]])
        # a second reconciliation of the same request is refused: it is no longer unknown
        self.assertEqual(cli.main(["reconcile-unknown", "--job", str(root / "job"), "--request", req["id"], "--authorized-by", "owner", "--no-charge"]), EXIT_INVALID)


if __name__ == "__main__":
    unittest.main()


class ReportAfterTheFinalState(unittest.TestCase):
    """test-pilot-002's deliverable/report.md said 'State: **evaluating**' on a delivered job: write_deliverable wrote it
    before the final transition and only manifest.json was re-stamped afterwards."""

    def test_the_demo_report_names_the_final_state(self):
        job = fresh(self, "report") / "job"
        self.assertEqual(cli.main(["demo", "--output", str(job), "--worker", "fake"]), EXIT_OK)
        report = (job / "deliverable" / "report.md").read_text(encoding="utf-8")
        manifest = json.loads((job / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["state"], "unresolved")
        self.assertIn("State: **unresolved**", report)
        self.assertNotIn("evaluating", report)

    def test_an_owner_verdict_is_written_into_the_report(self):
        from modeler.agentic.config import build_policy, translate_request
        from modeler.agentic.demo import demo_request
        from modeler.agentic.evaluation import record_owner_verdict, report_markdown
        from modeler.agentic.executor import FakeWorker
        from modeler.agentic.responses import ScriptedTransport
        from modeler.agentic.runner import Session
        root = fresh(self, "owner-report")
        translated = translate_request(demo_request(root / "inputs"), root / "inputs")
        session = Session.create(root / "job", translated=translated, policy=build_policy(owner_review=False, driver="scripted", worker="fake", intake="synthetic", ar=False),
                                 fingerprints={"test": True}, worker=FakeWorker(), transport=ScriptedTransport([]), worker_config=None)
        store = session.store
        ddir = root / "job" / "deliverable"
        ddir.mkdir()
        (ddir / "model.glb").write_bytes(b"GLB")
        import hashlib
        manifest = {"job": "job", "state": "delivered", "stop_reason": "delivered_by_author", "deliverable_status": "compatible_asset",
                    "asset": {"path": str(ddir / "model.glb"), "sha256": hashlib.sha256(b"GLB").hexdigest(), "bytes": 3, "revision": "r0001"},
                    "axes": {"owner": {"verdict": None, "note": "no owner verdict recorded"}}, "revisions": [], "limitations": []}
        store.update_job(deliverable_json=manifest)
        (ddir / "report.md").write_text(report_markdown(manifest), encoding="utf-8")
        record_owner_verdict(store, verdict="reject", sha256=None, medium="live AR mirror", note="lens too light")
        report = (ddir / "report.md").read_text(encoding="utf-8")
        self.assertIn("| owner | reject |", report, "the owner axis as a table row, not a raw dict")
        self.assertIn("State: **delivered**", report)


# =========================================================================== conversation cost, owner revision, resume re-pin (2026-09-28)
def _tree(root: Path) -> dict:
    import hashlib
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


class NewJobDefaults(unittest.TestCase):
    """F1: the rolling cache is the default of new jobs; F2: a prune threshold below the compaction threshold."""

    def setUp(self):
        self.root = fresh(self, "defaults")
        from modeler.agentic.demo import demo_request, write_demo_script
        self.request = demo_request(self.root / "inputs")
        self.req_path = self.root / "inputs" / "request.json"
        self.req_path.write_text(json.dumps(self.request), encoding="utf-8")
        self.script = write_demo_script(self.root / "inputs" / "script.json", self.request["photos"])

    def policy_of(self, name: str, *extra) -> tuple[int, dict | None]:
        code = cli.main(["start", "--request", str(self.req_path), "--output", str(self.root / name), "--driver", "scripted", "--script", str(self.script),
                         "--worker", "fake", "--intake", "synthetic", "--no-ar", "--critic", "scripted", "--final-evaluator", "scripted",
                         "--images-per-request", "6", "--max-output-tokens", "4000", *extra])
        if not (self.root / name / "job.sqlite3").is_file():
            return code, None
        store = Store.open(self.root / name, readonly=True)
        try:
            return code, store.job()["policy"]
        finally:
            store.close()

    def test_rolling_cache_and_the_prune_threshold_are_the_defaults(self):
        code, policy = self.policy_of("a")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(policy["cache_mode"], "explicit_rolling")
        self.assertEqual(policy["image_prune_tokens"], policy["compact_threshold_tokens"] - cli.PRUNE_HEADROOM_TOKENS)
        self.assertEqual(policy["image_prune_tokens"], 160_000)
        self.assertIsNone(policy["owner_instruction"])
        self.assertIsNone(policy["editable_modules"])
        _, one = self.policy_of("b", "--cache-mode", "explicit_one_breakpoint", "--image-prune-tokens", "0")
        self.assertEqual((one["cache_mode"], one["image_prune_tokens"]), ("explicit_one_breakpoint", None))
        _, low = self.policy_of("c", "--image-prune-tokens", "60000")
        self.assertEqual(low["image_prune_tokens"], 60_000)
        self.assertEqual(self.policy_of("d", "--image-prune-tokens", "250000")[0], EXIT_INVALID, "above the compaction threshold it could never act")
        with self.assertRaises(SystemExit) as cm:          # argparse refuses an unknown mode before anything runs
            self.policy_of("e", "--cache-mode", "implicit")
        self.assertEqual(cm.exception.code, EXIT_INVALID)


class CliPolicyThroughBuildPolicy(unittest.TestCase):
    """Round 8 integration not_done: cli.build_session_parts set image_prune_tokens, owner_instruction and editable_modules
    AFTER config.build_policy, so its validation never reached a CLI job: a whitespace-only --owner-instruction was stored
    as '   ' (truthy: owner-revision mode with a blank request) and an instruction over 8,000 characters was accepted. The
    CLI now resolves only its prune default and passes all three through build_policy, whose policy it stores unchanged."""
    setUp = NewJobDefaults.setUp
    policy_of = NewJobDefaults.policy_of

    def test_a_blank_owner_instruction_is_no_instruction(self):
        code, policy = self.policy_of("blank", "--owner-instruction", "   ")
        self.assertEqual(code, EXIT_OK)
        self.assertIsNone(policy["owner_instruction"])

    def test_an_owner_instruction_over_the_bound_is_refused_before_any_job_exists(self):
        from modeler.agentic.config import OWNER_INSTRUCTION_MAX_CHARS
        code, policy = self.policy_of("long", "--owner-instruction", "x" * (OWNER_INSTRUCTION_MAX_CHARS + 1))
        self.assertEqual((code, policy), (EXIT_INVALID, None))

    def test_the_three_keys_go_through_build_policy_and_its_policy_is_stored_unchanged(self):
        from unittest import mock
        from modeler.agentic import config as cfg
        seen = []

        def spy(**kw):
            seen.append(kw)
            return cfg.build_policy(**kw)
        with mock.patch.object(cli, "build_policy", spy):
            code, policy = self.policy_of("spy", "--image-prune-tokens", "60000", "--owner-instruction", "warmer lens")
        self.assertEqual(code, EXIT_OK)
        (kw,) = seen
        self.assertEqual((kw["image_prune_tokens"], kw["owner_instruction"], kw["editable_modules"]), (60_000, "warmer lens", None))
        self.assertEqual(policy, json.loads(json.dumps(cfg.build_policy(**kw))), "nothing is added or overwritten after build_policy")

    def test_the_prune_default_follows_the_threshold_build_policy_resolves(self):
        from modeler.agentic.config import build_policy
        self.assertEqual(cli.DEFAULT_COMPACT_THRESHOLD_TOKENS, build_policy(driver="scripted", worker="fake")["compact_threshold_tokens"],
                         "the CLI's copy of the default threshold is build_policy's")
        _, policy = self.policy_of("t100k", "--compact-threshold-tokens", "100000")
        self.assertEqual((policy["compact_threshold_tokens"], policy["image_prune_tokens"]), (100_000, 60_000))
        _, policy = self.policy_of("t20k", "--compact-threshold-tokens", "20000")
        self.assertEqual(policy["image_prune_tokens"], 20_000, "never below the prune minimum")
        code, _ = self.policy_of("bad", "--compact-threshold-tokens", "10")
        self.assertEqual(code, EXIT_INVALID, "build_policy refuses the threshold itself, not a derived prune value")
        for bad in ("19999", "-5"):
            with self.subTest(bad=bad):
                self.assertEqual(self.policy_of("p" + bad, "--image-prune-tokens", bad)[0], EXIT_INVALID)

    def test_editable_modules_without_a_seed_is_still_refused(self):
        code, policy = self.policy_of("noseed", "--editable-modules", "materials")
        self.assertEqual((code, policy), (EXIT_INVALID, None))
        code, policy = self.policy_of("bogus", "--editable-modules", "bogus")
        self.assertEqual((code, policy), (EXIT_INVALID, None))


class OwnerRevisionMode(unittest.TestCase):
    """start --seed-revision JOB:RID --owner-instruction TEXT --editable-modules a,b: the sealed program of a revision of
    another job (its program set sha256 verified against the build bundle), the owner's request verbatim and the editable
    modules in the policy and the first message, the seed built before the first author turn."""

    @classmethod
    def setUpClass(cls):
        cls.root = fresh(cls, "owner-rev")
        cls.source = cls.root / "source"
        assert cli.main(["demo", "--output", str(cls.source), "--worker", "fake"]) == EXIT_OK
        import gc
        gc.collect()        # the demo's own Store closes here (a later collection would checkpoint its WAL mid-test)
        from modeler.agentic.demo import _resp, demo_request
        cls.request = demo_request(cls.root / "inputs")
        cls.req_path = cls.root / "inputs" / "request.json"
        cls.req_path.write_text(json.dumps(cls.request), encoding="utf-8")
        steps = [{"endpoint": "responses", "expect": {"has_image": True, "has_text": ["make the lens a warmer brown", "seed_build", "editable_modules"]},
                  "body": _resp("call_1", "request_delivery", {"revision_id": "r0001", "status_claim": "best_effort", "note": "seed as is"}, rs="rs_1")}]
        cls.script = cls.root / "inputs" / "script.json"
        cls.script.write_text(json.dumps({"steps": steps}), encoding="utf-8")

    def start(self, name: str, *extra) -> int:
        return cli.main(["start", "--request", str(self.req_path), "--output", str(self.root / name), "--driver", "scripted", "--script", str(self.script),
                         "--worker", "fake", "--intake", "synthetic", "--no-ar", *extra])

    def test_seeded_owner_revision_runs_with_the_policy_and_provenance(self):
        before = _tree(self.source)
        code = self.start("job", "--seed-revision", f"{self.source}:r0002", "--owner-instruction", "make the lens a warmer brown",
                          "--editable-modules", "materials,lenses")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(_tree(self.source), before, "the source job is only read")
        store = Store.open(self.root / "job", readonly=True)
        try:
            policy = store.job()["policy"]
            self.assertEqual(policy["owner_instruction"], "make the lens a warmer brown")
            self.assertEqual(policy["editable_modules"], ["materials", "lenses"])
            src = Store.open(self.source, readonly=True)
            try:
                src_rev = src.revision("r0002")
            finally:
                src.close()
            seed = store.setting("seed")
            self.assertEqual(seed["revision"], "r0001")
            self.assertEqual(seed["program_set_sha256"], src_rev["program_set_sha256"], "the same program set, verified")
            self.assertEqual(seed["provenance"]["source_revision"], "r0002")
            self.assertEqual(seed["provenance"]["source_program_set_sha256"], src_rev["program_set_sha256"])
            # a new job waits for the owner's live review (owner_review is the default since 2026-09-28): the delivery the author
            # requested is a candidate in awaiting_owner, not a delivery
            self.assertEqual(store.state(), "awaiting_owner")
            self.assertEqual(store.open_owner_round()["revision_id"], "r0001")
            self.assertEqual(store.events("seed_built")[0]["data"]["revision"], "r0001")
        finally:
            store.close()

    def test_refusals(self):
        self.assertEqual(self.start("bad1", "--seed-revision", f"{self.source}:r0002", "--editable-modules", "materials,wings"), EXIT_INVALID)
        self.assertEqual(self.start("bad2", "--editable-modules", "materials"), EXIT_INVALID, "editable modules need a seeded revision")
        self.assertEqual(self.start("bad3", "--seed-revision", f"{self.source}:r0404"), EXIT_INVALID)
        self.assertEqual(self.start("bad4", "--seed-revision", str(self.source)), EXIT_INVALID, "JOB:RID")
        self.assertEqual(self.start("bad5", "--seed-revision", f"{self.source}:r0002", "--seed-program", str(self.root)), EXIT_INVALID)
        for name in ("bad1", "bad2", "bad3", "bad4", "bad5"):
            self.assertFalse((self.root / name / "job.sqlite3").exists(), name)


class ResumeWithANewWorkerConfig(unittest.TestCase):
    """INF-06: a paused job pinned a worker config whose doctor record is bound to the library of its start; after any
    glasses_lib / harness change the pinned record no longer matches and resume was stranded (a new passing
    --worker-config was compared, then ignored). Now a new config that passes the doctor re-pins the job, with provenance."""

    def setUp(self):
        from modeler.agentic.config import build_policy, translate_request
        from modeler.agentic.demo import demo_request
        from modeler.agentic.executor import FakeWorker, validate_worker_config, worker_config_fingerprint
        from modeler.agentic.responses import ScriptedTransport
        from modeler.agentic.runner import Session
        self.fp = worker_config_fingerprint
        self.root = fresh(self, "repin")
        base = validate_worker_config({"image_digest": "sha256:" + "a" * 64})
        self.stale = dict(base, doctor={"passed_utc": "2026-09-27T00:00:00", "fingerprint": "0d766edc" + "0" * 56})
        self.fresh_cfg = dict(base, image_digest="sha256:" + "b" * 64)
        self.fresh_cfg["doctor"] = {"passed_utc": "2026-09-28T00:00:00", "fingerprint": worker_config_fingerprint(self.fresh_cfg)}
        translated = translate_request(demo_request(self.root / "inputs"), self.root / "inputs")
        policy = build_policy(owner_review=False, driver="scripted", worker="docker", intake="synthetic", ar=False)
        session = Session.create(self.root / "job", translated=translated, policy=policy, fingerprints={"test": True}, worker=FakeWorker(),
                                 transport=ScriptedTransport([]), worker_config=self.stale)
        session.release()
        session.store.close()
        (self.root / "script.json").write_text(json.dumps({"steps": []}), encoding="utf-8")
        (self.root / "fresh.json").write_text(json.dumps(self.fresh_cfg), encoding="utf-8")
        (self.root / "failing.json").write_text(json.dumps(dict(self.fresh_cfg, doctor={"passed_utc": "x", "fingerprint": "nope"})), encoding="utf-8")

    def doctor(self, cfg):
        ok = bool(cfg) and (cfg.get("doctor") or {}).get("fingerprint") == self.fp(cfg)
        return {"blocking": [] if ok else ["no passing doctor self-test bound to this config/image/harness fingerprint (run doctor --self-test)"]}

    def resume(self, *extra) -> int:
        from unittest import mock
        from modeler.agentic import runner
        with mock.patch.object(cli, "docker_doctor", side_effect=self.doctor), mock.patch.object(runner.Session, "run", return_value="ready"):
            return cli.main(["resume", "--job", str(self.root / "job"), "--script", str(self.root / "script.json"), *extra])

    def pinned(self) -> tuple[dict, list, dict | None]:
        store = Store.open(self.root / "job", readonly=True)
        try:
            return store.job()["worker_config"], store.events("worker_config_repinned"), store.setting("worker_config_history")
        finally:
            store.close()

    def test_the_stale_pin_blocks_and_says_how_to_continue(self):
        self.assertEqual(self.resume(), EXIT_INVALID)
        self.assertEqual(self.pinned()[0], self.stale)

    def test_a_new_config_that_fails_the_doctor_is_refused_and_nothing_is_re_pinned(self):
        self.assertEqual(self.resume("--worker-config", str(self.root / "failing.json")), EXIT_INVALID)
        cfg, events, history = self.pinned()
        self.assertEqual((cfg, events, history), (self.stale, [], None))

    def test_a_new_passing_config_re_pins_the_job_with_provenance(self):
        self.assertNotEqual(self.resume("--worker-config", str(self.root / "fresh.json")), EXIT_INVALID)
        cfg, events, history = self.pinned()
        self.assertEqual(cfg, self.fresh_cfg)
        self.assertEqual(len(events), 1)
        entry = history[-1]
        self.assertEqual(entry["from_image_digest"], "sha256:" + "a" * 64)
        self.assertEqual(entry["to_image_digest"], "sha256:" + "b" * 64)
        self.assertEqual(entry["from_doctor_fingerprint"], self.stale["doctor"]["fingerprint"])
        self.assertEqual(entry["to_doctor_fingerprint"], self.fresh_cfg["doctor"]["fingerprint"])
        self.assertEqual(set(entry["library_sha256"]), {"glasses_lib.py", "harness.py"})
        self.assertIn("revisions_before", entry)
        # resuming again with the same config is not a second switch
        self.assertNotEqual(self.resume("--worker-config", str(self.root / "fresh.json")), EXIT_INVALID)
        self.assertEqual(len(self.pinned()[1]), 1)


class PolicyDefaults(unittest.TestCase):
    """config.build_policy is the one place the policy of a new job is made (review 2026-09-28, conversation fixer's
    integration note): the rolling cache is its default, it knows and validates the three keys the CLI used to add after
    it (image_prune_tokens, owner_instruction, editable_modules), and it warns above the long-context price cliff. A
    stored policy of a job created before 2026-09-28 lacks the three keys; the runner reads each missing one as None,
    which is exactly what build_policy stores for 'off', so the old behaviour holds."""

    @staticmethod
    def offline(**kw) -> dict:
        from modeler.agentic.config import build_policy
        return build_policy(driver="scripted", worker="fake", **kw)

    def test_the_rolling_cache_is_the_default_and_the_three_keys_are_present(self):
        from modeler.agentic.responses import CACHE_MODES
        p = self.offline()
        self.assertEqual(p["cache_mode"], "explicit_rolling")
        self.assertIn(p["cache_mode"], CACHE_MODES)
        self.assertEqual({k: p[k] for k in ("image_prune_tokens", "owner_instruction", "editable_modules")},
                         {"image_prune_tokens": None, "owner_instruction": None, "editable_modules": None})
        self.assertNotIn("warnings", p, "a policy below the price cliff carries no warnings key")
        self.assertEqual(self.offline(cache_mode="explicit_one_breakpoint")["cache_mode"], "explicit_one_breakpoint")
        self.assertEqual(self.offline(cache_mode="none")["cache_mode"], "none")

    def test_an_unknown_cache_mode_is_refused(self):
        from modeler.agentic.config import ConfigError
        for bad in ("implicit", "", None, 1):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.offline(cache_mode=bad)

    def test_image_prune_tokens_is_validated_against_the_compaction_threshold(self):
        from modeler.agentic.config import ConfigError
        self.assertIsNone(self.offline(image_prune_tokens=0)["image_prune_tokens"], "0 means never, stored as None (the old behaviour)")
        self.assertEqual(self.offline(image_prune_tokens=60_000)["image_prune_tokens"], 60_000)
        self.assertEqual(self.offline(image_prune_tokens=200_000)["image_prune_tokens"], 200_000)
        self.assertEqual(self.offline(image_prune_tokens=90_000, compact_threshold_tokens=100_000)["image_prune_tokens"], 90_000)
        for bad in (19_999, 200_001, -1, True, 1.5, "60000", float("nan")):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.offline(image_prune_tokens=bad)
        with self.assertRaises(ConfigError):
            self.offline(image_prune_tokens=150_000, compact_threshold_tokens=100_000)

    def test_owner_instruction_is_a_bounded_string(self):
        from modeler.agentic.config import OWNER_INSTRUCTION_MAX_CHARS, ConfigError
        self.assertEqual(self.offline(owner_instruction="make the lens a warmer brown")["owner_instruction"], "make the lens a warmer brown")
        self.assertIsNone(self.offline(owner_instruction="   ")["owner_instruction"], "blank is no instruction")
        self.assertEqual(len(self.offline(owner_instruction="x" * OWNER_INSTRUCTION_MAX_CHARS)["owner_instruction"]), OWNER_INSTRUCTION_MAX_CHARS)
        for bad in ("x" * (OWNER_INSTRUCTION_MAX_CHARS + 1), 5, ["a"]):
            with self.subTest(bad=str(bad)[:20]), self.assertRaises(ConfigError):
                self.offline(owner_instruction=bad)

    def test_editable_modules_names_program_modules(self):
        from modeler.agentic.config import ConfigError
        from modeler.candidates import MODULE_ORDER
        a, b = MODULE_ORDER[0], MODULE_ORDER[-1]
        self.assertEqual(self.offline(editable_modules=[b, a, b])["editable_modules"], [b, a], "de-duplicated, order kept")
        self.assertEqual(self.offline(editable_modules=f" {a} ,{b},")["editable_modules"], [a, b], "the CLI's comma form")
        for bad in (["frame", "no_such_module"], [], "", ",", 3, [3]):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.offline(editable_modules=bad)

    def test_a_compaction_threshold_above_the_long_context_cliff_warns(self):
        import warnings
        from modeler.agentic.config import ConfigWarning
        from modeler.agentic.pricing import LONG_CONTEXT_INPUT_TOKENS
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            at = self.offline(compact_threshold_tokens=LONG_CONTEXT_INPUT_TOKENS)
            above = self.offline(compact_threshold_tokens=LONG_CONTEXT_INPUT_TOKENS + 1)
        self.assertNotIn("warnings", at)
        self.assertEqual(len(above["warnings"]), 1)
        self.assertIn("272,000", above["warnings"][0])
        self.assertEqual([w.category for w in caught], [ConfigWarning], "one warning, for the policy above the cliff")
        self.assertIn("272,000", str(caught[0].message))

    def test_the_cli_policy_agrees_with_build_policy(self):
        """The CLI resolves only its prune default and passes the flags as given; build_policy accepts them unchanged."""
        from modeler.agentic.config import build_policy
        p = self.offline(image_prune_tokens=cli.image_prune_tokens(None, 200_000), owner_instruction="warmer lens",
                         editable_modules="materials,lenses", cache_mode=cli.DEFAULT_CACHE_MODE)
        self.assertEqual((p["image_prune_tokens"], p["owner_instruction"], p["editable_modules"], p["cache_mode"]),
                         (160_000, "warmer lens", ["materials", "lenses"], "explicit_rolling"))
        self.assertEqual(build_policy.__kwdefaults__["cache_mode"], cli.DEFAULT_CACHE_MODE)
