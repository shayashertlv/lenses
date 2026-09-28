"""The owner review loop of ``modeler.agentic`` (2026-09-28): request_delivery waits in awaiting_owner for the owner's live
AR verdict; accept delivers exactly the candidate, changes go verbatim to the same author conversation with the related
measurements, the runtime-limited flags, a new allowance and optional module locks, stop ends unresolved with the candidate
kept; a conversation above review_resume_token_limit continues in a new seeded, linked job. Every decision is recorded.

Offline only: ScriptedTransport and FakeWorker. A "real" candidate (a non-synthetic revision with GLB bytes, an
observation summary and an export receipt) is made by wrapping tools.build_revision (``realize``): the synthetic build
runs, then the revision row gets bytes, measurements and an audit as a Docker build would record them. No network, no
Docker, no Blender; job folders under a short temp path only (tests/test_agentic_support.py, lag-owner).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest import mock

from modeler.agentic import cli, config, demo, executor, responses, runner
from modeler.agentic import tools as T
from modeler.agentic.budget import Budget, BudgetExhausted
from modeler.agentic.config import ConfigError
from modeler.agentic.state import JOB_STATES, TRANSITIONS, StateError, Store, TransitionError
from test_agentic_support import fresh_dir

AUTOMATION = Path(__file__).resolve().parents[1]
PILOTS = AUTOMATION / "data" / "modeler" / "agentic"
EXAMPLES = AUTOMATION / "modeler" / "agentic" / "examples"
NULL_LOG = lambda *a, **k: None   # noqa: E731
OWNER_TEXT = "the lens colour is too light, the frame is too clear"
PROGRAM_TINT = "gl.note('materials: a darker lens tint')\n"

# what a Docker build of a clear-crystal pair records (the shapes observe.py / lens_colour.py / appearance.py write)
REAL_SUMMARY = {
    "front_contour_mean_mm": 1.1, "lens_outline_mean_mm": 0.9, "mean_contour_mm_all_fit_views": 1.4, "ar_runtime_compatible": True, "ar_report_valid": True,
    "ar_optical_meshes": 2, "ar_continuity_failure": None,
    "lens_colour": {"hue_error": 0.01, "value_ratio": 1.31, "flags": ["too_light"], "lens_transmission_recommended": [0.59, 0.485, 0.38],
                    "lens_transmission_effective": [0.75, 0.725, 0.67], "lens_reflection": [0.02, 0.018, 0.012]},
    "lens_reflection": {"max_jump_px": 3.0, "max_jump_share": 0.01, "max_saturated_share": 0.0, "flag": None, "threshold_jump_share": 0.05},
    "frame_see_through": {"see_through": 0.69, "frame_pixels": 5159},
    "appearance": {"flags": ["crystal_too_clear", "crystal_clarity_runtime_limited"], "reliable": True,
                   "materials": {"Colourless polished crystal": {"role": "crystal", "deltas": {"visibility_ratio": 0.31},
                                                                 "recommended": {"tint_srgb": [238, 236, 230], "thickness_mm": 5.5},
                                                                 "flags": ["crystal_too_clear", "crystal_clarity_runtime_limited"]},
                                 "Pale champagne gold": {"role": "metal", "deltas": {"hue": 0.004}, "flags": []}}},
}
EXPORT_RECEIPT = {"receipt": {"audit": {"flags": ["planar_front"], "notes": ["frame front: fitted wrap radius 612 mm (above 400)"]}}}


def _resp(call_id: str, name: str, args: dict) -> dict:
    return {"endpoint": "responses", "body": demo._resp(call_id, name, args, rs=f"rs_{call_id}")}


def modules(**over) -> dict:
    base = {n: {"mode": "inherit", "content": None, "expected_base_sha256": None} for n in T.MODULE_KEYS}
    base.update(over)
    return base


def edit(call_id: str, base: str | None, *, frame: str | None = None, materials: str | None = None, expect: dict | None = None) -> dict:
    over = {}
    if frame is not None:
        over["frame"] = {"mode": "replace", "content": frame, "expected_base_sha256": None}
    if materials is not None:
        over["materials"] = {"mode": "replace", "content": materials, "expected_base_sha256": None}
    s = _resp(call_id, "edit_program", {"base_revision_id": base, "modules": modules(**over), "rationale": f"scripted {call_id}",
                                        "expected_changes": ["x"], "build_now": True, "deliver_if_compatible": False})
    if expect:
        s["expect"] = expect
    return s


def fetch(call_id: str) -> dict:
    return _resp(call_id, "fetch_pending_images", {"max_images": 6})


def deliver(call_id: str, rid: str) -> dict:
    return _resp(call_id, "request_delivery", {"revision_id": rid, "status_claim": "best_effort", "note": f"scripted delivery of {rid}"})


ROUND1 = [edit("c1", None, frame=demo.PROGRAM_A), fetch("c2"), deliver("c3", "r0001")]


def round2(expect: dict | None = None) -> list[dict]:
    return [edit("d1", "r0001", materials=PROGRAM_TINT, expect=expect), fetch("d2"), deliver("d3", "r0002")]


_ORIGINAL_BUILD = T.build_revision


def realize_build(ctx, rid, op, **kw):
    """The synthetic build, then the row a real build records: GLB bytes, a non-synthetic compatible revision, an
    observation summary and an export receipt with an audit."""
    res = _ORIGINAL_BUILD(ctx, rid, op, **kw)
    rev = ctx.store.revision(rid)
    if (rev.get("compatibility") or {}).get("compatible"):
        data = b"glTF-test-asset:" + rid.encode() + b":" + rev["program_set_sha256"].encode()
        rdir = ctx.revision_dir(rid)
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "model.glb").write_bytes(data)
        (rdir / "export.json").write_text(json.dumps(EXPORT_RECEIPT), encoding="utf-8")
        sha = hashlib.sha256(data).hexdigest()
        with ctx.store.tx():
            ctx.store.conn.execute("UPDATE revisions SET synthetic = 0 WHERE id = ?", (rid,))
            ctx.store.update_revision(rid, glb_sha256=sha, compatibility_json=dict(rev["compatibility"], synthetic=False, glb_sha256=sha),
                                      observation_json={"summary": REAL_SUMMARY, "bbox_mm": [[-70.0, -25.0, -5.0], [70.0, 25.0, 140.0]]})
    return res


class OwnerCase(unittest.TestCase):
    real = False

    def setUp(self):
        self.root = fresh_dir(self, "owner", "o-", env="LAG_OWNER_TMP")
        self.stores: list[Store] = []
        self.addCleanup(self._close)
        self.calibration = self.root / "owner_verdicts.jsonl"
        if self.real:
            patcher = mock.patch.object(T, "build_revision", realize_build)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _close(self):
        for s in self.stores:
            with contextlib.suppress(Exception):
                s.close()

    def policy(self, **over) -> dict:
        base = dict(driver="scripted", worker="fake", intake="synthetic", critic="scripted", final_evaluator="scripted", ar=False, budget_usd="5",
                    max_inference_requests=8, max_output_tokens=4000, max_revisions=4, max_worker_seconds=120, wall_minutes=30, images_per_request=6)
        base.update(over)
        return config.build_policy(**base)

    def start(self, steps, *, name="job", policy_extra=None, count_rule=None, **over) -> tuple[runner.Session, responses.ScriptedTransport]:
        inputs = self.root / f"{name}.in"
        request = demo.demo_request(inputs)
        translated = config.translate_request(request, inputs)
        transport = responses.ScriptedTransport(steps, count_rule=count_rule)
        session = runner.Session.create(self.root / name, translated=translated, policy=dict(self.policy(**over), **(policy_extra or {})),
                                        fingerprints={"test": "owner"}, worker=executor.FakeWorker({}), transport=transport, worker_config=None, log=NULL_LOG)
        self.stores.append(session.store)
        return session, transport

    def continue_session(self, store: Store, steps, *, count_rule=None) -> tuple[runner.Session, responses.ScriptedTransport]:
        transport = responses.ScriptedTransport(steps, count_rule=count_rule)
        session = runner.Session(store, transport=transport, worker=executor.FakeWorker({}), log=NULL_LOG)
        session.acquire()
        return session, transport

    def round_one(self, **kw) -> tuple[runner.Session, responses.ScriptedTransport]:
        session, transport = self.start(list(ROUND1), **kw)
        self.assertEqual(session.run(), "awaiting_owner")
        self.assertEqual(transport.steps, [], "every round-1 step was consumed")
        return session, transport

    def allowance(self, usd="3", ops=3) -> dict:
        # a round spends only its own grant (2026-09-28): each author request reserves its worst case (about 0.8-1.1 USD at
        # these conversations' ~50k input tokens under the frozen tariff), so a 3-request round is granted 3 USD
        return config.owner_allowance(budget_usd=usd, max_inference_requests=ops)

    def calibration_rows(self) -> list[dict]:
        if not self.calibration.exists():
            return []
        return [json.loads(line) for line in self.calibration.read_text(encoding="utf-8").splitlines() if line.strip()]


def user_texts(item: dict) -> list[str]:
    return [b["text"] for b in item.get("content", []) if b.get("type") == "input_text"]


# =========================================================================== the state machine
class Transitions(unittest.TestCase):
    def test_awaiting_owner_is_a_non_terminal_wait_left_only_by_a_decision(self):
        self.assertIn("awaiting_owner", JOB_STATES)
        self.assertEqual(TRANSITIONS["awaiting_owner"], {"ready", "evaluating", "unresolved", "cancelled", "failed", "needs_attention"})
        self.assertNotIn("delivered", TRANSITIONS["awaiting_owner"], "only an acceptance (through evaluating) delivers")
        self.assertIn("awaiting_owner", TRANSITIONS["ready_for_final"])
        self.assertIn("awaiting_owner", TRANSITIONS["evaluating"])
        for state, targets in TRANSITIONS.items():
            if state not in ("ready_for_final", "evaluating"):
                self.assertNotIn("awaiting_owner", targets, state)


class RequestDeliveryWaits(OwnerCase):
    def test_the_author_delivery_opens_a_round_and_waits(self):
        session, transport = self.round_one()
        store = session.store
        job = store.job()
        self.assertEqual((job["state"], job["stop_reason"], job["deliverable_status"]), ("awaiting_owner", "awaiting_owner", None))
        self.assertFalse((store.job_dir / "deliverable" / "manifest.json").exists(), "nothing is delivered while the owner decides")
        (r,) = store.owner_rounds()
        self.assertEqual((r["round"], r["revision_id"], r["decision"], r["source"]), (1, "r0001", None, "author"))
        self.assertTrue((store.job_dir / "review" / "round-01" / "manifest.json").is_file())
        cand = json.loads((store.job_dir / "review" / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual((cand["kind"], cand["round"], cand["revision"]["id"], cand["synthetic"], cand["asset"]), ("review_candidate", 1, "r0001", True, None))
        self.assertEqual(store.verdicts(kind="final"), [])
        self.assertEqual([r["purpose"] for r in store.requests()], ["author"] * 3, "no final evaluator request")
        # the request_delivery reply tells the author the owner decides, and the author's budget notice needs no final slot
        out = [i["item"] for i in store.items("author") if i["item"].get("type") == "function_call_output" and i["item"]["call_id"] == "c3"][0]
        text = json.loads(out["output"][0]["text"])
        self.assertEqual(text["note"], T.OWNER_REVIEW_NOTE)
        self.assertIn("owner_review", json.dumps(store.window("author")[0]), "the first message says the owner reviews")
        self.assertIn("inference operations remain in this round", text["host_state"]["budget_notice"])
        self.assertEqual(cli.EXIT_BY_STATE["awaiting_owner"], 0)
        rep = runner.status_report(store)
        self.assertTrue(rep["owner_review"]["awaiting"])
        self.assertEqual(rep["owner_review"]["candidate"]["label"], "awaiting your review")

    def test_restart_while_awaiting_sends_nothing(self):
        session, _ = self.round_one()
        store = session.store
        # a runner that died right after the transition: its lease lapsed; a fresh runner reconciles and stops at the wait
        with store.tx():
            store.conn.execute("UPDATE job SET lease_holder = 'dead-runner', lease_expires_utc = '2000-01-01T00:00:00+00:00'")
        again = runner.Session(store, transport=responses.ScriptedTransport([]), worker=executor.FakeWorker({}), log=NULL_LOG)
        self.assertEqual(again.run(), "awaiting_owner")
        self.assertEqual(again.transport.sent, [], "waiting costs nothing: no request, not even a count")
        self.assertEqual(len(store.requests()), 3)
        # the CLI: resume on a waiting job reads no script and runs nothing
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["resume", "--job", str(store.job_dir)]), 0)
        self.assertIn("awaiting your review", err.getvalue())

    def test_the_old_behaviour_without_owner_review(self):
        session, _ = self.start(list(ROUND1) + [demo.demo_script()[-1]], owner_review=False)
        self.assertEqual(session.run(), "unresolved")
        self.assertEqual(session.store.owner_rounds(), [])


class Accept(OwnerCase):
    real = True

    def test_only_an_accept_delivers_and_it_delivers_exactly_the_candidate(self):
        session, _ = self.round_one()
        store = session.store
        with self.assertRaises(TransitionError):
            store.transition("delivered", "skip the owner")
        cand = json.loads((store.job_dir / "review" / "candidate.json").read_text(encoding="utf-8"))
        asset = cand["asset"]
        self.assertEqual(Path(asset["path"]).read_bytes(), (store.job_dir / "revisions" / "r0001" / "model.glb").read_bytes())
        self.assertIn("127.0.0.1%3A8793%2Fmodels%2Fjob%2Freview-r0001.glb", cand["tryon"]["link"])
        self.assertTrue(cand["tryon"]["link"].startswith("http://127.0.0.1:8240/?model="))
        self.assertEqual(cand["tryon"]["width_mm"], 140.0)
        self.assertIn("crystal_clarity_runtime_limited", json.dumps(cand["runtime_limited"]))
        s2 = runner.Session(store, transport=responses.RefusingTransport(), worker=None, log=NULL_LOG)
        s2.acquire()
        out = s2.owner_accept(authorized_by="Shay", medium="live AR mirror", note="perfect", calibration_file=self.calibration)
        s2.release()
        job = store.job()
        self.assertEqual((job["state"], job["stop_reason"], job["deliverable_status"]), ("delivered", "accepted_by_owner", "compatible_asset"))
        self.assertEqual(out["asset"]["sha256"], asset["sha256"])
        delivered = (store.job_dir / "deliverable" / "model.glb").read_bytes()
        self.assertEqual(hashlib.sha256(delivered).hexdigest(), asset["sha256"], "the delivered bytes are the candidate's")
        manifest = json.loads((store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["axes"]["owner"]["verdict"], "accept")
        self.assertEqual(manifest["review"]["rounds"][0]["decision"], "accept")
        self.assertIn("## Owner review", (store.job_dir / "deliverable" / "report.md").read_text(encoding="utf-8"))
        (v,) = store.verdicts(kind="owner")
        self.assertEqual((v["verdict"], v["asset_sha256"], v["record"]["round"]), ("accept", asset["sha256"], 1))
        self.assertEqual(v["record"]["measurements"]["lens_colour"]["lens_transmission_recommended"], [0.59, 0.485, 0.38])
        self.assertEqual(store.owner_rounds()[0]["decision"], "accept")
        (row,) = self.calibration_rows()
        self.assertEqual((row["kind"], row["verdict"], row["asset_sha256"], row["candidate"]), ("agentic_review", "accept", asset["sha256"], "r0001"))
        self.assertEqual(row["related_measurements"]["export_audit"]["flags"], ["planar_front"])

    def test_the_final_evaluator_is_off_by_default_under_owner_review(self):
        final_step = demo.demo_script()[-1]
        session, transport = self.start(list(ROUND1) + [final_step])
        self.assertEqual(session.run(), "awaiting_owner")
        self.assertTrue(session.policy["owner_review"])
        self.assertFalse(session.policy["final_on_accept"])
        self.assertEqual(session.policy["final_evaluator"], "scripted")
        session.acquire()
        session.owner_accept(authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual(session.store.state(), "delivered")
        self.assertEqual(transport.steps, [final_step], "the configured final evaluator was never asked")
        self.assertEqual([r for r in session.store.requests() if r["role"] == "final"], [])
        self.assertEqual(session.store.events("final_evaluation_skipped"), [], "run_final was never reached")
        manifest = json.loads((session.store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["axes"]["visual"]["automatic_verdict"], "unmeasured")

    def test_final_on_accept_asks_the_final_evaluator(self):
        """With final_on_accept the accepted candidate goes to Session.run_final, whose own rule applies: without the wearer
        renders of the candidate (none offline) no paid request is bought and the axis is unmeasured; without the flag
        run_final is never reached (the previous test: no final_evaluation_skipped event either)."""
        final_step = demo.demo_script()[-1]
        session, transport = self.start(list(ROUND1) + [final_step], final_on_accept=True)
        self.assertEqual(session.run(), "awaiting_owner")
        session.acquire()
        session.owner_accept(authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual(session.store.state(), "delivered")
        self.assertEqual(len(session.store.events("final_evaluation_skipped")), 1)
        manifest = json.loads((session.store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("required wearer evidence missing", json.dumps(manifest["final_evaluation"]["meta"]))
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="fake", owner_review=False, final_on_accept=True)

    def test_an_acceptance_interrupted_in_evaluating_completes_on_resume(self):
        session, _ = self.round_one()
        store = session.store
        with store.tx():            # the runner recorded the intent and moved to evaluating, then died
            store.set_setting("owner_accept_intent", {"round": 1, "authorized_by": "Shay", "medium": "live AR mirror", "note": "",
                                                      "calibration_file": str(self.calibration)})
            store.transition("evaluating", "the owner accepted round 1", expected="awaiting_owner")
        again = runner.Session(store, transport=responses.ScriptedTransport([]), worker=executor.FakeWorker({}), log=NULL_LOG)
        self.assertEqual(again.run(), "delivered")
        self.assertEqual(again.transport.sent, [])
        self.assertEqual(store.owner_rounds()[0]["decision"], "accept")
        self.assertIsNone(store.setting("owner_accept_intent"))
        self.assertEqual(len(self.calibration_rows()), 1)

    def test_a_decision_while_another_runner_holds_the_lease_is_refused(self):
        session, _ = self.round_one()
        store = session.store
        store.acquire_lease("someone-else", 600)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(["owner-review", "--job", str(store.job_dir), "--accept", "--authorized-by", "Shay", "--calibration-file", str(self.calibration)])
        self.assertEqual(code, 3, err.getvalue())
        self.assertEqual(store.state(), "awaiting_owner")

    def test_a_changed_candidate_is_never_accepted(self):
        session, _ = self.round_one()
        cand = json.loads((session.store.job_dir / "review" / "candidate.json").read_text(encoding="utf-8"))
        Path(cand["asset"]["path"]).write_bytes(b"tampered")
        session.acquire()
        with self.assertRaises(runner.RunnerError):
            session.owner_accept(authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual(session.store.state(), "awaiting_owner")
        self.assertEqual(self.calibration_rows(), [])


class Stop(OwnerCase):
    real = True

    def test_stop_ends_unresolved_with_the_candidate_kept(self):
        session, _ = self.round_one()
        store = session.store
        session.acquire()
        out = session.owner_stop(authorized_by="Shay", note="not this pair", calibration_file=self.calibration)
        session.release()
        job = store.job()
        self.assertEqual((job["state"], job["stop_reason"]), ("unresolved", "owner_stopped"))
        self.assertTrue(Path(out["kept_candidate"]).is_file(), "the candidate stays on disk")
        self.assertFalse((store.job_dir / "deliverable" / "model.glb").exists())
        manifest = json.loads((store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["state"], manifest["review"]["kept_candidate"]), ("unresolved", out["kept_candidate"]))
        self.assertEqual(store.verdicts(kind="owner")[-1]["verdict"], "stop")
        self.assertEqual(store.owner_rounds()[0]["decision"], "stop")
        self.assertEqual(self.calibration_rows()[0]["verdict"], "stop")

    def test_cancel_while_awaiting_decides_the_round_as_cancelled(self):
        session, _ = self.round_one()
        store = session.store
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["cancel", "--job", str(store.job_dir)]), 130)
        self.assertEqual(store.state(), "cancelled")
        self.assertEqual(store.owner_rounds()[0]["decision"], "cancelled")


# =========================================================================== a change round in the same conversation
class ChangeRound(OwnerCase):
    real = True

    def change(self, session: runner.Session, *, text=OWNER_TEXT, usd="3", ops=3, editable=None) -> dict:
        session.acquire()
        return session.owner_request_changes(text=text, allowance=self.allowance(usd, ops), authorized_by="Shay (owner)", editable=editable,
                                             calibration_file=self.calibration)

    def test_two_rounds_end_to_end_in_the_same_conversation(self):
        session, _ = self.round_one()
        store = session.store
        window_before = store.window("author")
        epoch_before = store.epoch("author")
        s2, t2 = self.continue_session(store, round2(expect={"has_text": [OWNER_TEXT, "Owner review, round 1"]}))
        out = s2.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance(), authorized_by="Shay (owner)", calibration_file=self.calibration)
        self.assertEqual((out["path"], out["round"], store.state()), ("same_conversation", 1, "ready"))
        self.assertEqual(s2.run(), "awaiting_owner")
        self.assertEqual(t2.steps, [])
        # the author kept its whole memory: the same epoch, the round-1 window is the prefix of the round-2 window
        self.assertEqual(store.epoch("author"), epoch_before)
        after = store.window("author")
        self.assertEqual(after[:len(window_before)], window_before)
        first_round2_payload = [s["payload"] for s in t2.sent if s["endpoint"] == "responses"][0]
        self.assertIn(OWNER_TEXT, json.dumps(first_round2_payload))
        rounds = store.owner_rounds()
        self.assertEqual([(r["round"], r["revision_id"], r["decision"]) for r in rounds], [(1, "r0001", "changes"), (2, "r0002", None)])
        self.assertEqual(store.revision("r0002")["parent_id"], "r0001")
        # round 2 is accepted: the job delivers r0002's bytes
        s2.acquire()
        s2.owner_accept(authorized_by="Shay", calibration_file=self.calibration)
        s2.release()
        self.assertEqual(store.state(), "delivered")
        self.assertEqual(json.loads((store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))["asset"]["revision"], "r0002")
        self.assertEqual([v["verdict"] for v in store.verdicts(kind="owner")], ["changes_requested", "accept"])
        self.assertEqual([r["verdict"] for r in self.calibration_rows()], ["changes_requested", "accept"])

    def test_the_owner_message_carries_the_words_measurements_flags_allowance_and_locks(self):
        session, _ = self.round_one()
        store = session.store
        self.change(session, editable=["materials"])
        msg = store.window("author")[-1]
        self.assertEqual((msg["type"], msg["role"]), ("message", "user"))
        texts = user_texts(msg)
        self.assertEqual(texts[1], OWNER_TEXT, "the owner's words verbatim, in a block of their own")
        self.assertIn("round 1", texts[0])
        host = json.loads(texts[2].split("\n", 1)[1])["owner_review"]
        rel = host["related_measurements"]
        self.assertEqual(rel["lens_colour"]["lens_transmission_recommended"], [0.59, 0.485, 0.38])
        self.assertEqual(rel["appearance"]["materials"]["Colourless polished crystal"]["recommended"]["tint_srgb"], [238, 236, 230])
        self.assertEqual(rel["lens_reflection"]["max_jump_px"], 3.0)
        self.assertEqual(rel["export_audit"]["flags"], ["planar_front"])
        limited = " ".join(host["runtime_limited"])
        self.assertIn("crystal_clarity_runtime_limited", limited)
        self.assertIn("cannot draw the crystal's edge contrast", limited)
        self.assertIn("do not chase the edges", limited)
        self.assertEqual((host["allowance"]["inference_operations"], host["allowance"]["budget_usd"]), (3, "3"))
        self.assertEqual((host["allowance"]["operations_remaining"], host["allowance"]["budget_remaining_usd"]), (3, "3.000000"), "the round's own figures")
        self.assertEqual(host["editable_modules"], ["materials"])
        self.assertEqual(set(host["locked_modules"]), set(T.MODULE_KEYS) - {"materials"})
        self.assertIn("edit_program with base_revision_id r0001", host["how_to_continue"])

    def test_the_round_caps_are_the_committed_amounts_plus_the_grant(self):
        """The round's caps are what the job had committed at the grant (the upper bound: settled + unknown + held, and the
        operations used) plus exactly the allowance; the leftover of earlier rounds is dropped and journaled."""
        session, _ = self.round_one()
        store = session.store
        before = Budget(store).totals()
        job_before = store.job()
        self.change(session, usd="1.25", ops=4)
        after = Budget(store).totals()
        job_after = store.job()
        self.assertEqual(job_after["cap_micro"], before["upper_bound_micro"] + 1_250_000)
        self.assertEqual(job_after["inference_operation_cap"], before["operations_used"] + 4)
        for k in ("settled_micro", "unknown_liability_micro", "held_micro", "upper_bound_micro", "operations_used"):
            self.assertEqual(after[k], before[k], f"{k}: a grant changes nothing already spent")
        self.assertEqual((after["remaining_micro"], after["operations_remaining"]), (1_250_000, 4), "the round can spend exactly the grant")
        (ev,) = store.events("allowance_granted")
        d = ev["data"]
        self.assertEqual((d["authorized_by"], d["add_micro"], d["add_operations"], d["round"]), ("Shay (owner)", 1_250_000, 4, 1))
        self.assertEqual((d["before"]["cap_micro"], d["after"]["cap_micro"]), (job_before["cap_micro"], job_after["cap_micro"]))
        self.assertEqual(d["upper_bound_micro"], before["upper_bound_micro"])
        self.assertEqual(d["dropped_leftover_micro"], job_before["cap_micro"] - before["upper_bound_micro"])
        self.assertEqual(d["dropped_leftover_operations"], job_before["inference_operation_cap"] - before["operations_used"])
        # a live Budget object sees the new caps (they are read from the job row)
        self.assertEqual(session.budget.cap_micro, job_after["cap_micro"])
        # the grant is the only way: update_job still refuses caps, and a grant cannot shrink or be anonymous
        with self.assertRaises(StateError):
            store.update_job(cap_micro=1)
        for bad in (dict(add_micro=-1, add_operations=1, authorized_by="x"), dict(add_micro=0, add_operations=0, authorized_by="x"),
                    dict(add_micro=1, add_operations=1, authorized_by=" "), dict(add_micro=1.5, add_operations=1, authorized_by="x"),
                    dict(add_micro=1, add_operations=1, authorized_by="x", base_micro=-1)):
            with self.assertRaises(StateError, msg=repr(bad)):
                store.grant_allowance(reason="t", **dict(dict(base_micro=0, base_operations=0), **bad))

    def test_the_round_spends_within_the_grant_and_reservations_stay_exact(self):
        session, _ = self.round_one()
        store = session.store
        s2, _t2 = self.continue_session(store, round2())
        s2.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance("3", 3), authorized_by="Shay", calibration_file=self.calibration)
        self.assertEqual(s2.run(), "awaiting_owner")
        self.assertEqual(store.open_owner_round()["revision_id"], "r0002", "the round's grant covered its three requests")
        t = Budget(store).totals()
        self.assertEqual(t["operations_used"], 6)
        self.assertEqual(t["operations_cap"], 3 + 3, "the operations used at the grant plus the round's 3")
        self.assertEqual(t["held_micro"], 0)
        settled = sum(int(r["settled_micro"] or 0) for r in store.reservations() if r["state"] == "settled")
        self.assertEqual(t["settled_micro"], settled)
        self.assertEqual(t["remaining_micro"], t["cap_micro"] - t["upper_bound_micro"])

    def test_an_allowance_above_the_ceiling_or_missing_is_refused(self):
        with self.assertRaises(ConfigError):
            config.owner_allowance(budget_usd="50.01", max_inference_requests=3)
        with self.assertRaises(ConfigError):
            config.owner_allowance(budget_usd="1", max_inference_requests=config.OWNER_ROUND_MAX_OPERATIONS + 1)
        with self.assertRaises(ConfigError):
            config.owner_allowance(budget_usd=None, max_inference_requests=3)
        with self.assertRaises(ConfigError):
            config.owner_allowance(budget_usd="0", max_inference_requests=3)
        with self.assertRaises(ConfigError):
            config.owner_allowance(budget_usd="10", max_inference_requests=3, current_cap_micro=195_000_000)
        self.assertEqual(config.owner_allowance(budget_usd="2.5", max_inference_requests=5), {"add_micro": 2_500_000, "add_operations": 5, "budget_usd": "2.5"})

    def test_the_policy_keys_and_their_defaults(self):
        p = config.build_policy(driver="scripted", worker="fake")
        self.assertEqual((p["owner_review"], p["review_resume_token_limit"], p["final_on_accept"]), (True, 150_000, False))
        self.assertEqual(config.build_policy(driver="scripted", worker="fake", compact_threshold_tokens=100_000)["review_resume_token_limit"], 100_000,
                         "never above the compaction threshold")
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="fake", review_resume_token_limit=250_000)
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="fake", owner_review="yes")

    def test_the_round_locks_are_enforced(self):
        session, _ = self.round_one()
        store = session.store
        steps = [edit("e1", "r0001", frame=demo.PROGRAM_B), edit("e2", "r0001", materials=PROGRAM_TINT), fetch("e3"), deliver("e4", "r0002")]
        s2, _t2 = self.continue_session(store, steps)
        s2.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance("5", 5), authorized_by="Shay", editable=["materials"],
                                 calibration_file=self.calibration)
        self.assertEqual(store.job()["policy"]["editable_modules"], ["materials"])
        self.assertEqual(s2.run(), "awaiting_owner")
        refused = [i["item"] for i in store.items("author") if i["item"].get("type") == "function_call_output" and i["item"]["call_id"] == "e1"][0]
        err = json.loads(refused["output"][0]["text"])["error"]
        self.assertIn("frame: not editable in this job", err)
        self.assertEqual([r["id"] for r in store.revisions()], ["r0001", "r0002"], "the refused edit created nothing")
        r2 = store.revision("r0002")
        self.assertEqual(r2["modules"]["frame"]["source"], "inherited:r0001")
        self.assertEqual(r2["modules"]["materials"]["source"], "changed")
        (ev,) = [e for e in store.events("policy_changed")]
        self.assertEqual(ev["data"]["after"]["editable_modules"], ["materials"])

    def test_the_wall_limit_counts_from_the_change_request(self):
        session, _ = self.round_one()
        store = session.store
        with store.tx():            # the job was created long ago: the owner took a week to decide
            store.conn.execute("UPDATE job SET created_utc = '2020-01-01T00:00:00+00:00'")
        self.change(session)
        self.assertFalse(session.deadline_passed(), "a change round gets the whole wall limit")

    def test_a_budget_fallback_goes_to_the_owner_who_can_grant_more(self):
        # every image fits the build reply; the second request acknowledges them; the third is refused by the cap
        steps = [edit("c1", None, frame=demo.PROGRAM_A), fetch("c2")]
        session, transport = self.start(steps, max_inference_requests=2, images_per_request=14)
        self.assertEqual(session.run(), "awaiting_owner")
        (r,) = session.store.owner_rounds()
        self.assertEqual((r["source"], r["candidate"]["stop_reason"]), ("fallback", "budget_exhausted"))
        s2, t2 = self.continue_session(session.store, [deliver("g1", "r0001")])
        s2.owner_request_changes(text="try again", allowance=self.allowance("1", 1), authorized_by="Shay", calibration_file=self.calibration)
        self.assertEqual(s2.run(), "awaiting_owner")
        self.assertEqual([(x["round"], x["source"], x["decision"]) for x in session.store.owner_rounds()], [(1, "fallback", "changes"), (2, "author", None)])


# =========================================================================== the too-large conversation
class ContinueInNewJob(OwnerCase):
    real = True

    def test_a_large_conversation_continues_in_a_linked_seeded_job(self):
        # every author request counts 160,000 input tokens: above the 150,000 limit, below the 200,000 compaction threshold
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        out = session.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance("2", 4), authorized_by="Shay", calibration_file=self.calibration)
        self.assertEqual(out["path"], "new_job")
        self.assertEqual(store.state(), "awaiting_owner", "nothing was recorded on the too-large path")
        self.assertEqual(store.events("allowance_granted"), [])
        steps = [fetch("n0"), deliver("n1", "r0001")]          # the seed build queued one image past the six a message carries
        transport = responses.ScriptedTransport(steps)
        new = session.owner_continue_in_new_job(self.root / "job-continued-1", text=OWNER_TEXT, allowance=self.allowance("2", 4), authorized_by="Shay",
                                                editable=["materials", "lenses"], worker=executor.FakeWorker({}), transport=transport, worker_config=None,
                                                fingerprints={"test": "owner"}, calibration_file=self.calibration)
        session.release()
        self.stores.append(new.store)
        # the old job: ended, linked forward, the candidate kept, the decision recorded
        job = store.job()
        self.assertEqual((job["state"], job["stop_reason"]), ("unresolved", "continued_in_new_job"))
        self.assertEqual(store.setting("lineage")["continued_in"][0]["job"], str(new.store.job_dir))
        self.assertEqual(store.owner_rounds()[0]["decision"], "continued_in_new_job")
        self.assertEqual(store.verdicts(kind="owner")[-1]["verdict"], "changes_requested")
        # the new job: linked back, seeded from the candidate, the owner's words verbatim, the summary, the grant as its caps
        njob = new.store.job()
        self.assertEqual(new.store.setting("lineage")["continued_from"]["job"], str(store.job_dir))
        self.assertEqual(new.store.setting("lineage")["continued_from"]["revision"], "r0001")
        self.assertEqual(njob["policy"]["owner_instruction"], OWNER_TEXT)
        self.assertEqual(njob["policy"]["editable_modules"], ["materials", "lenses"])
        self.assertEqual((njob["cap_micro"], njob["inference_operation_cap"]), (2_000_000, 4))
        self.assertTrue(njob["policy"]["owner_review"])
        seed = new.store.setting("seed")
        self.assertEqual(seed["program_set_sha256"], store.revision("r0001")["program_set_sha256"])
        first = json.dumps(new.store.window("author")[0])
        self.assertIn(OWNER_TEXT, first)
        self.assertIn("previous_job", first)
        self.assertEqual(new.run(), "awaiting_owner")
        self.assertEqual(new.store.owner_rounds()[0]["revision_id"], "r0001")
        rep = runner.status_report(new.store)
        self.assertEqual(rep["owner_review"]["lineage"]["continued_from"]["job"], str(store.job_dir))


# =========================================================================== old databases
class OldJobDatabases(unittest.TestCase):
    """Copies of test-pilot-001 / -002 (the source folders are never opened): a read-only open reports their state, a
    read-write open migrates the owner_rounds table in, and status works on both."""

    def copy(self, name: str) -> tuple[Path, str]:
        src = PILOTS / name
        if not (src / "job.sqlite3").is_file():
            self.skipTest(f"{src} is not on this machine")
        dst = fresh_dir(self, "owner", "old-", env="LAG_OWNER_TMP") / name
        dst.mkdir()
        for suffix in ("", "-wal"):
            if (src / f"job.sqlite3{suffix}").is_file():
                shutil.copyfile(src / f"job.sqlite3{suffix}", dst / f"job.sqlite3{suffix}")
        conn = sqlite3.connect(str(dst / "job.sqlite3"))
        try:
            state = conn.execute("SELECT state FROM job").fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
        self.assertNotIn("owner_rounds", tables, "the copy is of a job recorded before the owner review")
        return dst, state

    def check(self, name: str) -> None:
        dst, state = self.copy(name)
        ro = Store.open(dst, readonly=True)
        try:
            self.assertEqual(ro.state(), state)
            self.assertEqual(ro.owner_rounds(), [])
            rep = runner.status_report(ro)
            self.assertEqual(rep["state"], state)
            self.assertEqual(rep["owner_review"], {"enabled": False, "awaiting": False, "candidate": None, "rounds": [], "lineage": None})
        finally:
            ro.close()
        rw = Store.open(dst)
        try:
            self.assertEqual(rw.state(), state)
            self.assertIn("owner_rounds", rw.tables)
            self.assertIn("owner_rounds", rw.events("schema_migrated")[-1]["data"]["added"])
            self.assertEqual(rw.owner_rounds(), [])
        finally:
            rw.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["status", "--job", str(dst), "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["state"], state)

    def test_test_pilot_001(self):
        self.check("test-pilot-001")

    def test_test_pilot_002(self):
        self.check("test-pilot-002")


# =========================================================================== the CLI and the examples
class CliLoop(unittest.TestCase):
    def setUp(self):
        self.root = fresh_dir(self, "owner", "c-", env="LAG_OWNER_TMP")
        request = demo.demo_request(self.root / "inputs")
        self.req = self.root / "inputs" / "request.json"
        self.req.write_text(json.dumps(request), encoding="utf-8")
        self.r1, self.r2 = self.root / "r1.json", self.root / "r2.json"
        self.r1.write_text(json.dumps({"steps": ROUND1}), encoding="utf-8")
        self.r2.write_text(json.dumps({"steps": round2(expect={"has_text": ["the frame is too clear"]})}), encoding="utf-8")
        self.job = self.root / "job"
        self.cal = self.root / "cal.jsonl"

    def main(self, *argv) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def review(self, *extra) -> tuple[int, str]:
        return self.main("owner-review", "--job", str(self.job), "--authorized-by", "Shay", "--calibration-file", str(self.cal), *extra)

    def test_start_change_accept_through_the_cli(self):
        code, out = self.main("start", "--request", str(self.req), "--output", str(self.job), "--driver", "scripted", "--script", str(self.r1),
                              "--worker", "fake", "--intake", "synthetic", "--no-ar", "--images-per-request", "6", "--max-output-tokens", "4000")
        self.assertEqual(code, 0, out)
        self.assertIn("awaiting_your_review", out)
        code, out = self.main("status", "--job", str(self.job))
        self.assertIn("awaiting your review: round 1, r0001", out)
        # refusals: no decision, two decisions, a change round without its allowance, an allowance on an accept
        self.assertEqual(self.review()[0], 2)
        self.assertEqual(self.review("--accept", "--stop")[0], 2)
        self.assertEqual(self.review("--changes", OWNER_TEXT, "--script", str(self.r2))[0], 2)
        self.assertEqual(self.review("--changes", OWNER_TEXT, "--max-inference-requests", "3", "--budget-usd", "500", "--script", str(self.r2))[0], 2)
        self.assertEqual(self.review("--accept", "--budget-usd", "1")[0], 2)
        self.assertEqual(self.review("--changes", OWNER_TEXT, "--max-inference-requests", "3", "--budget-usd", "1")[0], 2, "a scripted round needs --script")
        store = Store.open(self.job, readonly=True)
        try:
            self.assertEqual((store.state(), store.events("allowance_granted")), ("awaiting_owner", []), "a refused decision changes nothing")
        finally:
            store.close()
        code, out = self.review("--changes", OWNER_TEXT, "--max-inference-requests", "3", "--budget-usd", "3", "--editable-modules", "materials",
                                "--script", str(self.r2))
        self.assertEqual(code, 0, out)
        code, out = self.main("status", "--job", str(self.job), "--json")
        rep = json.loads(out)
        self.assertEqual(rep["state"], "awaiting_owner")
        self.assertEqual([(r["round"], r["revision"], r["decision"]) for r in rep["owner_review"]["rounds"]], [(1, "r0001", "changes"), (2, "r0002", None)])
        self.assertEqual(rep["owner_review"]["rounds"][0]["text"], OWNER_TEXT)
        code, out = self.review("--accept", "--note", "good")
        self.assertEqual(code, 0, out)
        store = Store.open(self.job, readonly=True)
        try:
            # a synthetic candidate has no bytes to deliver: accepted and unresolved, never a model.glb
            self.assertEqual((store.state(), store.job()["stop_reason"], store.job()["deliverable_status"]), ("unresolved", "accepted_by_owner", "synthetic_only"))
            self.assertEqual([v["verdict"] for v in store.verdicts(kind="owner")], ["changes_requested", "accept"])
        finally:
            store.close()
        self.assertFalse(self.cal.exists(), "synthetic candidates are never calibration rows")
        self.assertEqual(self.review("--stop")[0], 2, "nothing awaits a review any more")

    def test_no_continue_records_the_round_and_resume_continues_it(self):
        code, out = self.main("start", "--request", str(self.req), "--output", str(self.job), "--driver", "scripted", "--script", str(self.r1),
                              "--worker", "fake", "--intake", "synthetic", "--no-ar", "--images-per-request", "6", "--max-output-tokens", "4000")
        self.assertEqual(code, 0, out)
        code, out = self.review("--changes", OWNER_TEXT, "--max-inference-requests", "3", "--budget-usd", "3", "--no-continue")
        self.assertEqual(code, 0, out)
        store = Store.open(self.job, readonly=True)
        try:
            self.assertEqual(store.state(), "ready")
        finally:
            store.close()
        code, out = self.main("resume", "--job", str(self.job), "--script", str(self.r2))
        self.assertEqual(code, 0, out)
        store = Store.open(self.job, readonly=True)
        try:
            self.assertEqual((store.state(), store.open_owner_round()["revision_id"]), ("awaiting_owner", "r0002"))
        finally:
            store.close()


class DockerExample(unittest.TestCase):
    """examples/owner_review_round{1,2}.json: test-pilot-002 r0006's exact modules (checked against the revision row of a
    copy of its database) and a lens-tint patch toward lens_transmission_recommended; the scripted loop runs end to end
    offline (the fake worker stands in for Docker here; the README gives the Docker commands)."""

    def test_the_example_is_r0006_exactly_and_the_patch_is_the_tint(self):
        from modeler.agentic.examples import make_owner_review_example as ex
        r1 = json.loads((EXAMPLES / "owner_review_round1.json").read_text(encoding="utf-8"))
        r2 = json.loads((EXAMPLES / "owner_review_round2.json").read_text(encoding="utf-8"))
        args1 = json.loads(r1["steps"][0]["body"]["output"][2]["arguments"])
        mods = {n: s["content"] for n, s in args1["modules"].items() if s["mode"] == "replace"}
        self.assertEqual(set(mods), set(T.MODULE_KEYS))
        self.assertEqual({n: hashlib.sha256(t.encode()).hexdigest() for n, t in mods.items()}, r1["source"]["modules_sha256"])
        if (ex.SOURCE_JOB / "job.sqlite3").is_file():
            row = ex.revision_row(ex.SOURCE_JOB, ex.SOURCE_REVISION)
            self.assertEqual(r1["source"]["modules_sha256"], {n: m["sha256"] for n, m in row["modules"].items()})
        args2 = json.loads(r2["steps"][0]["body"]["output"][2]["arguments"])
        mat = args2["modules"]["materials"]
        self.assertEqual((mat["mode"], mat["expected_base_sha256"]), ("patch", r1["source"]["modules_sha256"]["materials"]))
        (p,) = json.loads(mat["content"])
        self.assertIn(p["find"], mods["materials"])
        self.assertIn("transmission_top_rgb=(0.59,0.485,0.38)", p["replace"])
        self.assertEqual([args2["modules"][n]["mode"] for n in T.MODULE_KEYS if n != "materials"], ["inherit"] * 6)

    def test_the_example_rounds_run_offline(self):
        root = fresh_dir(self, "owner", "x-", env="LAG_OWNER_TMP")
        request = demo.demo_request(root / "inputs")
        req = root / "inputs" / "request.json"
        req.write_text(json.dumps(request), encoding="utf-8")
        job = root / "job"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["start", "--request", str(req), "--output", str(job), "--driver", "scripted", "--script", str(EXAMPLES / "owner_review_round1.json"),
                             "--worker", "fake", "--intake", "synthetic", "--no-ar"])
        self.assertEqual(code, 0, out.getvalue())
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["owner-review", "--job", str(job), "--changes", OWNER_TEXT, "--authorized-by", "Shay", "--max-inference-requests", "3",
                             "--budget-usd", "2", "--editable-modules", "materials", "--script", str(EXAMPLES / "owner_review_round2.json"),
                             "--calibration-file", str(root / "cal.jsonl")])
        self.assertEqual(code, 0, out.getvalue())
        store = Store.open(job, readonly=True)
        try:
            self.assertEqual(store.state(), "awaiting_owner")
            r2 = store.revision("r0002")
            self.assertEqual(r2["modules"]["materials"]["source"], "changed")
            text = (job / "revisions" / "r0002" / "program" / "materials.py").read_text(encoding="utf-8")
            self.assertIn("transmission_top_rgb=(0.59,0.485,0.38)", text)
        finally:
            store.close()


class TryOnListing(OwnerCase):
    real = True

    def test_tryon_lists_the_awaited_candidate_and_nothing_after_the_decision(self):
        from modeler import tryon
        session, _ = self.round_one()
        store = session.store
        rows = tryon.catalog([str(store.job_dir)], 8793)
        (row,) = rows
        self.assertEqual(row["kind"], "review")
        self.assertIn("awaiting your review", row["label"])
        self.assertEqual(row["route"], "/models/job/review-r0001.glb")
        self.assertIn("127.0.0.1%3A8793%2Fmodels%2Fjob%2Freview-r0001.glb", row["try_on"])
        self.assertIn(b"awaiting your review", tryon.index_page(rows))
        session.acquire()
        session.owner_accept(authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        rows = tryon.catalog([str(store.job_dir)], 8793)
        self.assertEqual([r["kind"] for r in rows], ["delivered"], "after the acceptance the delivery is listed, not the candidate")


# =========================================================================== the correctness review's MUST FIX items (2026-09-28)
FRAME_B = "gl.note('frame B: a different frame')\n"
# round 1 builds r0001 (frame A) and r0002 (frame B) and delivers r0002: the owner reviews r0002
TWO_FRAMES = [edit("c1", None, frame=demo.PROGRAM_A), fetch("c2"), edit("c3", "r0001", frame=FRAME_B), fetch("c4"), deliver("c5", "r0002")]


def call_output(store: Store, call_id: str) -> dict:
    out = [i["item"] for i in store.items("author") if i["item"].get("type") == "function_call_output" and i["item"]["call_id"] == call_id][0]
    return json.loads(out["output"][0]["text"])


def owner_host_block(store: Store, round_no: int) -> dict:
    msg = [i for i in store.window("author") if i.get("role") == "user" and f"Owner review, round {round_no}" in json.dumps(i)][0]
    return json.loads(user_texts(msg)[2].split("\n", 1)[1])["owner_review"]


class RoundSpendsOnlyItsGrant(OwnerCase):
    """A change round never spends beyond its grant: operations, USD and revisions are the round's own (the correctness
    review granted 1 operation and the author spent 8, and 2 new revisions allowed 11)."""
    real = True

    def test_a_round_granted_one_operation_cannot_make_a_second_request(self):
        session, _ = self.round_one(max_inference_requests=10)
        store = session.store
        before = Budget(store).totals()
        steps = [_resp(f"x{i}", "list_evidence", {}) for i in range(5)] + round2()
        s2, t2 = self.continue_session(store, steps)
        s2.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance("1", 1), authorized_by="Shay", calibration_file=self.calibration)
        self.assertEqual(s2.run(), "awaiting_owner")
        after = Budget(store).totals()
        self.assertEqual(after["operations_used"] - before["operations_used"], 1, "the round spent more operations than the owner granted")
        self.assertEqual(len([s for s in t2.sent if s["endpoint"] == "responses"]), 1, "exactly one author request was sent")
        self.assertEqual(after["operations_cap"], before["operations_used"] + 1)
        self.assertLessEqual(after["upper_bound_micro"] - before["upper_bound_micro"], 1_000_000)
        (ev,) = store.events("allowance_granted")
        self.assertEqual(ev["data"]["dropped_leftover_operations"], 10 - before["operations_used"])
        self.assertEqual(ev["data"]["dropped_leftover_micro"], before["cap_micro"] - before["upper_bound_micro"])
        # the author was told the round's own figures, not the job's leftover
        allowance = owner_host_block(store, 1)["allowance"]
        self.assertEqual((allowance["operations_remaining"], allowance["budget_remaining_usd"]), (1, "1.000000"))
        # the budget stop hands the reviewed revision back to the owner, who can grant another round
        self.assertEqual([(r["round"], r["source"], r["revision_id"]) for r in store.owner_rounds()], [(1, "author", "r0001"), (2, "fallback", "r0001")])

    def test_a_round_cannot_reserve_beyond_its_usd_even_with_an_unspent_job_cap(self):
        session, _ = self.round_one()
        store = session.store
        self.assertGreater(Budget(store).totals()["remaining_micro"], 4_000_000, "round 1 left most of its 5 USD unspent")
        session.acquire()
        session.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance("0.000001", 1), authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        t = Budget(store).totals()
        self.assertEqual((t["remaining_micro"], t["operations_remaining"]), (1, 1))
        with self.assertRaises(BudgetExhausted):
            Budget(store).reserve(role="author", purpose="author", input_tokens=100, max_output_tokens=100)

    def test_two_grants_each_give_exactly_their_own_round(self):
        session, _ = self.round_one()
        store = session.store
        s2, _t = self.continue_session(store, round2())
        s2.owner_request_changes(text="a", allowance=self.allowance("3.25", 3), authorized_by="Shay", calibration_file=self.calibration)
        self.assertEqual(s2.run(), "awaiting_owner")
        t1 = Budget(store).totals()
        s3, _t = self.continue_session(store, [deliver("e1", "r0002")])
        s3.owner_request_changes(text="b", allowance=self.allowance("1.5", 2), authorized_by="Shay", calibration_file=self.calibration)
        g2 = store.events("allowance_granted")[-1]["data"]
        self.assertEqual(g2["after"], {"cap_micro": t1["upper_bound_micro"] + 1_500_000, "inference_operation_cap": t1["operations_used"] + 2})
        s3.run()
        self.assertTrue(all(r["state"] in ("settled", "released") for r in store.reservations()))

    def test_the_round_revision_cap_is_the_revisions_used_plus_the_grant(self):
        session, _ = self.round_one(max_revisions=10, max_inference_requests=10)
        store = session.store
        used = len(store.revisions())
        session.acquire()
        session.owner_request_changes(text="x", allowance=self.allowance("1", 2), authorized_by="Shay", add_revisions=2, calibration_file=self.calibration)
        session.release()
        self.assertEqual(store.job()["policy"]["max_revisions"], used + 2, "2 new revisions this round, not the leftover of max_revisions 10 as well")
        self.assertEqual(owner_host_block(store, 1)["revisions"], {"used": used, "cap": used + 2})

    def test_the_default_revision_grant_is_the_operation_grant(self):
        session, _ = self.round_one(max_revisions=10, max_inference_requests=10)
        store = session.store
        session.acquire()
        session.owner_request_changes(text="x", allowance=self.allowance("1", 3), authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual(store.job()["policy"]["max_revisions"], len(store.revisions()) + 3)

    def test_the_session_refuses_an_allowance_above_the_ceilings(self):
        """A direct caller (not the CLI) cannot pass a round above OWNER_ROUND_MAX_* or lift the job past JOB_MAX_*."""
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        over_round = {"add_micro": 60_000_000, "add_operations": 3, "budget_usd": "60"}
        over_ops = {"add_micro": 1_000_000, "add_operations": config.OWNER_ROUND_MAX_OPERATIONS + 1, "budget_usd": "1"}
        for bad in (over_round, over_ops):
            with self.assertRaises(runner.RunnerError, msg=repr(bad)):
                session.owner_continue_in_new_job(self.root / "never", text="t", allowance=bad, authorized_by="Shay", editable=None,
                                                  worker=executor.FakeWorker({}), transport=responses.RefusingTransport(), worker_config=None,
                                                  fingerprints={"t": 1}, calibration_file=self.calibration)
        self.assertFalse((self.root / "never").exists())
        with store.tx():            # the conversation is small again; the job has committed 199.5 USD (an unknown liability)
            store.conn.execute("UPDATE inference_requests SET input_token_count = 10 WHERE role = 'author'")
            store.conn.execute("UPDATE reservations SET state = 'unknown', liability_micro = 199500000 WHERE id = (SELECT MIN(id) FROM reservations)")
        with self.assertRaises(runner.RunnerError):
            session.owner_request_changes(text="t", allowance=self.allowance("1", 3), authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual((store.state(), store.events("allowance_granted")), ("awaiting_owner", []))


class LocksBindToTheReviewedRevision(OwnerCase):
    """In a locked round every locked module equals the REVIEWED revision's, byte for byte: an edit on another base and a
    delivery of a revision not made this round (or with other locked modules) are refused, and so is such a fallback."""
    real = True

    def two_frames(self) -> Store:
        session, _ = self.start(list(TWO_FRAMES), max_inference_requests=12)
        self.assertEqual(session.run(), "awaiting_owner")
        self.assertEqual(session.store.open_owner_round()["revision_id"], "r0002")
        return session.store

    def locked_round(self, store: Store, steps: list[dict], *, ops: int = 6) -> runner.Session:
        s2, _t = self.continue_session(store, steps)
        s2.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance(str(ops), ops), authorized_by="Shay", editable=["materials"],
                                 calibration_file=self.calibration)
        return s2

    def test_an_edit_on_a_base_whose_locked_modules_differ_from_the_reviewed_revision_is_refused(self):
        store = self.two_frames()
        s2 = self.locked_round(store, [edit("d1", "r0001", materials=PROGRAM_TINT), edit("d2", "r0002", materials=PROGRAM_TINT), fetch("d3"), deliver("d4", "r0003")])
        self.assertEqual(s2.run(), "awaiting_owner")
        err = call_output(store, "d1")["error"]
        self.assertIn("frame", err)
        self.assertIn("r0002", err)
        self.assertEqual([r["id"] for r in store.revisions()], ["r0001", "r0002", "r0003"], "the refused edit created nothing")
        reviewed, new = T.module_shas(store.revision("r0002")), T.module_shas(store.revision("r0003"))
        self.assertEqual({k: v for k, v in new.items() if k != "materials"}, {k: v for k, v in reviewed.items() if k != "materials"})
        self.assertEqual(store.open_owner_round()["revision_id"], "r0003")

    def test_a_delivery_of_a_revision_not_made_this_round_is_refused(self):
        store = self.two_frames()
        s2 = self.locked_round(store, [deliver("d1", "r0001"), deliver("d2", "r0002"), edit("d3", "r0002", materials=PROGRAM_TINT), fetch("d4"),
                                       deliver("d5", "r0003")], ops=7)
        self.assertEqual(s2.run(), "awaiting_owner")
        e1, e2 = call_output(store, "d1")["error"], call_output(store, "d2")["error"]
        self.assertIn("locked module(s) frame", e1)
        self.assertIn("not made in this round", e2)
        self.assertEqual(store.open_owner_round()["revision_id"], "r0003", "only the round's own revision reaches the owner")

    def test_a_fallback_in_a_locked_round_never_picks_a_revision_with_other_locked_modules(self):
        store = self.two_frames()
        # the author selects r0001 (frame A) and the round's single operation is spent: the fallback must not hand r0001 to the owner
        s2 = self.locked_round(store, [_resp("d1", "select_revision", {"revision_id": "r0001"})], ops=1)
        self.assertEqual(s2.run(), "awaiting_owner")
        self.assertEqual(store.job()["selected_revision"], "r0002")
        self.assertEqual((store.open_owner_round()["revision_id"], store.open_owner_round()["source"]), ("r0002", "fallback"))

    def test_an_unlocked_round_may_still_deliver_any_seen_revision(self):
        store = self.two_frames()
        s2, _t = self.continue_session(store, [deliver("d1", "r0001")])
        s2.owner_request_changes(text="the first frame was better", allowance=self.allowance("3", 3), authorized_by="Shay", calibration_file=self.calibration)
        self.assertEqual(s2.run(), "awaiting_owner")
        self.assertEqual(store.open_owner_round()["revision_id"], "r0001")


class AcceptIsDurable(OwnerCase):
    """The verdict row, the round decision and the delivered transition are one transaction; the calibration row is
    written once, however often a crash makes the host retry it."""
    real = True

    def test_a_failure_after_the_delivered_transition_loses_nothing(self):
        session, _ = self.round_one()
        store = session.store
        session.acquire()
        with mock.patch.object(runner, "record_owner_verdict", side_effect=OSError("crash after the transition")):
            with self.assertRaises(OSError):
                session.owner_accept(authorized_by="Shay", note="perfect", calibration_file=self.calibration)
        session.release()
        # the transaction rolled back: the job is where the accept began, with the owner's intent recorded
        self.assertEqual(store.state(), "evaluating")
        self.assertEqual(store.setting("owner_accept_intent")["note"], "perfect")
        self.assertEqual((store.verdicts(kind="owner"), store.owner_rounds()[0]["decision"], self.calibration_rows()), ([], None, []))
        again = runner.Session(store, transport=responses.ScriptedTransport([]), worker=None, log=NULL_LOG)
        self.assertEqual(again.run(), "delivered")
        self.assertEqual(again.transport.sent, [])
        (v,) = store.verdicts(kind="owner")
        self.assertEqual((v["verdict"], v["record"]["note"]), ("accept", "perfect"))
        self.assertEqual(store.owner_rounds()[0]["decision"], "accept")
        self.assertIsNone(store.setting("owner_accept_intent"))
        (row,) = self.calibration_rows()
        self.assertEqual(row["verdict"], "accept")
        manifest = json.loads((store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["state"], manifest["axes"]["owner"]["verdict"]), ("delivered", "accept"))

    def test_the_calibration_row_is_written_once_across_retries(self):
        from modeler import owner_verdict as mov
        session, _ = self.round_one()
        store = session.store
        session.acquire()
        with mock.patch.object(mov, "append_review_record", side_effect=OSError("disk full")):
            session.owner_accept(authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual(store.state(), "delivered")
        self.assertEqual(self.calibration_rows(), [])
        pending = store.setting("owner_calibration_pending")
        self.assertEqual([p["record"]["verdict"] for p in pending], ["accept"], "the unwritten row stays pending")
        runner.Session(store, transport=responses.RefusingTransport(), worker=None, log=NULL_LOG).run()
        self.assertEqual(len(self.calibration_rows()), 1)
        self.assertIsNone(store.setting("owner_calibration_pending"))
        # a crash after the append, before the pending mark was cleared: the retry finds the row and writes nothing
        with store.tx():
            store.set_setting("owner_calibration_pending", pending)
        runner.Session(store, transport=responses.RefusingTransport(), worker=None, log=NULL_LOG).run()
        self.assertEqual(len(self.calibration_rows()), 1)
        self.assertIsNone(store.setting("owner_calibration_pending"))


class ContinuationIsDurable(OwnerCase):
    real = True

    def continue_into(self, session: runner.Session, output: Path) -> runner.Session:
        return session.owner_continue_in_new_job(output, text=OWNER_TEXT, allowance=self.allowance("1", 3), authorized_by="Shay", editable=["materials"],
                                                 worker=executor.FakeWorker({}), transport=responses.RefusingTransport(), worker_config=None,
                                                 fingerprints={"t": 1}, calibration_file=self.calibration)

    def test_a_continuation_interrupted_before_the_old_job_ended_is_finished_not_duplicated(self):
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        first = self.root / "cont-1"
        with mock.patch.object(Store, "decide_owner_round", side_effect=StateError("crash before the old job ended")):
            with self.assertRaises(StateError):
                self.continue_into(session, first)
        self.assertEqual(store.state(), "awaiting_owner")
        self.assertFalse((store.setting("lineage") or {}).get("continued_in"), "the forward link is written with the old job's end, not before")
        self.assertEqual(store.setting("owner_continuation_intent")["output"], str(first))
        with self.assertRaises(runner.RunnerError):
            self.continue_into(session, self.root / "cont-2")
        self.assertFalse((self.root / "cont-2").exists(), "a retry into another folder never makes a second continuation")
        new = self.continue_into(session, first)
        self.stores.append(new.store)
        session.release()
        self.assertEqual((store.state(), store.job()["stop_reason"]), ("unresolved", "continued_in_new_job"))
        self.assertEqual([c["job"] for c in store.setting("lineage")["continued_in"]], [str(first)])
        self.assertEqual(new.store.setting("lineage")["continued_from"]["job"], str(store.job_dir))
        self.assertEqual([v["verdict"] for v in store.verdicts(kind="owner")], ["changes_requested"])
        self.assertIsNone(store.setting("owner_continuation_intent"))
        self.assertEqual(new.store.state(), "ready")

    def test_changes_no_continue_on_the_new_job_path_exits_0(self):
        session, _ = self.round_one()
        store = session.store
        with store.tx():
            store.conn.execute("UPDATE inference_requests SET input_token_count = 999999 WHERE role = 'author'")
        text = "  the lens colour is too light,\n the frame is too clear é \"q\"  "
        new_dir = self.root / "cont"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["owner-review", "--job", str(store.job_dir), "--changes", text, "--authorized-by", "Shay", "--max-inference-requests", "3",
                             "--budget-usd", "1", "--editable-modules", "materials", "--new-job-output", str(new_dir), "--no-continue",
                             "--calibration-file", str(self.calibration)])
        self.assertEqual(code, 0, err.getvalue() + out.getvalue())
        ns = Store.open(new_dir, readonly=True)
        self.stores.append(ns)
        self.assertEqual(ns.state(), "ready")
        self.assertEqual(ns.job()["policy"]["owner_instruction"], text)
        self.assertEqual((ns.job()["cap_micro"], ns.job()["inference_operation_cap"]), (1_000_000, 3))
        self.assertEqual(ns.setting("lineage")["continued_from"]["owner_text"], text)
        self.assertEqual(store.setting("lineage")["continued_in"][0]["job"], str(new_dir))


# =========================================================================== the verifier's round-11 MUST FIX items (2026-09-28)
def run_cli(*argv) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue() + err.getvalue()


class PreGrantAmountsAreNeverRoundMoney(OwnerCase):
    """A liability counted as committed at a grant (an unknown outcome, a held reservation) that later resolves lower never
    becomes money of the round: the round's remaining never exceeds its grant minus its own spend (the verifier granted
    1 USD, reconciled a 2 USD unknown as no charge and had 3 USD left; five 1.45 USD reservations went through)."""
    real = True

    def unknown_at_grant(self, liability: int, *, usd="1", ops=4, **kw) -> tuple[Store, str]:
        session, _ = self.round_one(**kw)
        store = session.store
        with store.tx():
            rid = store.conn.execute("SELECT MIN(id) FROM reservations").fetchone()[0]
            store.conn.execute("UPDATE reservations SET state = 'unknown', liability_micro = ? WHERE id = ?", (liability, rid))
        session.acquire()
        session.owner_request_changes(text="x", allowance=self.allowance(usd, ops), authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        return store, rid

    def test_an_unknown_counted_at_the_grant_and_reconciled_as_no_charge_leaves_the_round_its_grant(self):
        store, rid = self.unknown_at_grant(2_000_000)
        t0 = Budget(store).totals()
        self.assertEqual(t0["remaining_micro"], 1_000_000)
        s2, _t = self.continue_session(store, [])
        s2.run()                    # the round stops for the unknown outcome; the owner reconciles it
        Budget(store).reconcile_unknown(rid, authorized_by="Shay", usage=None, no_charge=True)
        t1 = Budget(store).totals()
        self.assertEqual(t1["remaining_micro"], 1_000_000, "the reconciled liability became round money")
        self.assertEqual(t1["cap_micro"], t0["cap_micro"] - 2_000_000)
        (ev,) = store.events("allowance_pre_grant_resolved")
        self.assertEqual((ev["data"]["reservation"], ev["data"]["cap_lowered_micro"]), (rid, 2_000_000))
        self.assertIsNone(store.setting("round_open_reservations"), "nothing counted at the grant is still open")

    def test_after_the_reconcile_no_request_bigger_than_the_grant_is_reserved(self):
        store, rid = self.unknown_at_grant(3_000_000, count_rule=lambda payload: 100_000)
        Budget(store).reconcile_unknown(rid, authorized_by="Shay", usage=None, no_charge=True)
        before = {r["id"] for r in store.reservations()}
        s2, t2 = self.continue_session(store, round2(), count_rule=lambda payload: 100_000)
        s2.run()
        new = [r for r in store.reservations() if r["id"] not in before and r["state"] != "released"]
        self.assertEqual([r["reserved_micro"] for r in new if r["reserved_micro"] > 1_000_000], [], "a reservation above the 1 USD grant")
        self.assertEqual([s for s in t2.sent if s["endpoint"] == "responses"], [], "each request (1.45 USD) is above the grant: none is sent")

    def test_a_reservation_held_at_the_grant_that_resolves_lower_lowers_the_caps(self):
        session, _ = self.round_one()
        store = session.store
        budget = Budget(store)
        a = budget.reserve(role="author", purpose="author", input_tokens=1000, max_output_tokens=1000)
        b = budget.reserve(role="author", purpose="author", input_tokens=1000, max_output_tokens=1000)
        budget.grant_allowance(add_micro=1_000_000, add_operations=2, authorized_by="Shay", reason="test")
        self.assertEqual(sorted(store.setting("round_open_reservations")), sorted([a["id"], b["id"]]))
        budget.release(a["id"], "never sent")
        t = budget.totals()
        self.assertEqual((t["remaining_micro"], t["operations_remaining"]), (1_000_000, 2), "a released pre-grant hold gave the round money or an operation")
        budget.settle_rejected(b["id"], request_id="q", http_status=400, reason="rejected")
        t = budget.totals()
        self.assertEqual((t["remaining_micro"], t["operations_remaining"]), (1_000_000, 2))
        self.assertIsNone(store.setting("round_open_reservations"))
        # an unknown marking keeps the amount counted: the entry stays until the owner reconciles it
        c = budget.reserve(role="author", purpose="author", input_tokens=1000, max_output_tokens=1000)
        budget.grant_allowance(add_micro=1_000_000, add_operations=2, authorized_by="Shay", reason="test 2")
        budget.mark_unknown(c["id"], "timeout")
        self.assertIn(c["id"], store.setting("round_open_reservations"))


class PendingCalibrationThroughTheCli(OwnerCase):
    """A calibration row the file refused at a terminal decision (accept, stop) is written by `resume` under a lease,
    once; `status` says it is pending (resume used to return 'nothing to resume' before anything wrote it)."""
    real = True

    def cli_job(self) -> Path:
        req_dir = self.root / "inputs"
        request = demo.demo_request(req_dir)
        req = req_dir / "request.json"
        req.write_text(json.dumps(request), encoding="utf-8")
        job = self.root / "job"
        r1 = self.root / "r1.json"
        r1.write_text(json.dumps({"steps": ROUND1}), encoding="utf-8")
        code, out = run_cli("start", "--request", req, "--output", job, "--driver", "scripted", "--script", r1, "--worker", "fake", "--intake", "synthetic",
                            "--no-ar", "--images-per-request", "6", "--max-output-tokens", "4000")
        self.assertEqual(code, 0, out)
        return job

    def pending(self, job: Path):
        s = Store.open(job, readonly=True)
        try:
            return s.setting("owner_calibration_pending"), s.state()
        finally:
            s.close()

    def check_decision(self, *decision: str, state: str):
        from modeler import owner_verdict as mov
        job = self.cli_job()
        with mock.patch.object(mov, "append_review_record", side_effect=OSError("file locked")):
            code, out = run_cli("owner-review", "--job", job, *decision, "--authorized-by", "Shay", "--calibration-file", self.calibration)
        self.assertEqual(code, 0, out)
        pending, st = self.pending(job)
        self.assertEqual((len(pending or []), st, self.calibration_rows()), (1, state, []))
        code, out = run_cli("status", "--job", job)
        self.assertIn("1 calibration row(s) pending", out)
        code, out = run_cli("status", "--job", job, "--json")
        self.assertEqual(json.loads(out)["owner_calibration_pending"], 1)
        code, out = run_cli("resume", "--job", job)
        self.assertEqual(code, 0, out)
        self.assertIn("wrote 1 pending calibration row", out)
        self.assertEqual(len(self.calibration_rows()), 1)
        self.assertEqual(self.pending(job), (None, state))
        code, out = run_cli("resume", "--job", job)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.calibration_rows()), 1, "a second resume writes nothing")

    def test_resume_writes_the_row_an_accept_left_pending(self):
        self.check_decision("--accept", state="delivered")

    def test_resume_writes_the_row_a_stop_left_pending(self):
        self.check_decision("--stop", state="unresolved")

    def test_an_interrupted_accept_says_how_to_finish_and_resume_finishes_it_without_a_script(self):
        job = self.cli_job()
        with mock.patch.object(runner, "record_owner_verdict", side_effect=OSError("crash")):
            code, _out = run_cli("owner-review", "--job", job, "--accept", "--authorized-by", "Shay", "--calibration-file", self.calibration)
        self.assertEqual(code, 2)
        code, out = run_cli("owner-review", "--job", job, "--accept", "--authorized-by", "Shay", "--calibration-file", self.calibration)
        self.assertEqual(code, 2)
        self.assertIn("interrupted", out)
        self.assertIn(f'resume --job "{job}"', out)
        # nothing is sent to finish an acceptance (final_on_accept off): resume needs no --script and no credential
        code, out = run_cli("resume", "--job", job)
        self.assertEqual(code, 0, out)
        s = Store.open(job, readonly=True)
        self.stores.append(s)
        self.assertEqual((s.state(), len(s.verdicts(kind="owner")), len(self.calibration_rows())), ("delivered", 1, 1))


class ContinuationRetries(OwnerCase):
    """A continuation whose new job failed to initialize is recorded and a retry into a new folder is allowed; a retry
    that reuses the folder must repeat the words, the allowance, the editable modules and the revisions exactly."""
    real = True

    def cont(self, session, output, *, text=OWNER_TEXT, usd="1", ops=3, editable=("materials",), add_revisions=None):
        return session.owner_continue_in_new_job(output, text=text, allowance=self.allowance(usd, ops), authorized_by="Shay",
                                                 editable=None if editable is None else list(editable), add_revisions=add_revisions,
                                                 worker=executor.FakeWorker({}), transport=responses.RefusingTransport(), worker_config=None,
                                                 fingerprints={"t": 1}, calibration_file=self.calibration)

    def test_a_continuation_whose_seed_build_failed_is_recorded_and_a_new_folder_is_allowed(self):
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        first, second = self.root / "cont-1", self.root / "cont-2"
        with mock.patch.object(runner.Session, "host_build_seed", side_effect=executor.WorkerError("docker daemon down")):
            with self.assertRaises(runner.RunnerError) as cm:
                self.cont(session, first)
        self.assertIn("docker daemon down", str(cm.exception))
        self.assertIn("--new-job-output", str(cm.exception))
        self.assertEqual(store.state(), "awaiting_owner")
        intent = store.setting("owner_continuation_intent")
        self.assertIsNone(intent["output"])
        (att,) = intent["failed_attempts"]
        self.assertEqual(att["output"], str(first))
        self.assertIn("docker daemon down", att["error"])
        self.assertEqual(len(store.events("continuation_failed")), 1)
        # the failed folder is kept for inspection and never reused
        with self.assertRaises(runner.RunnerError) as cm:
            self.cont(session, first)
        self.assertIn("failed to initialize", str(cm.exception))
        self.assertIn("--new-job-output", str(cm.exception))
        new = self.cont(session, second)
        self.stores.append(new.store)
        session.release()
        self.assertEqual((store.state(), store.job()["stop_reason"]), ("unresolved", "continued_in_new_job"))
        self.assertEqual([c["job"] for c in store.setting("lineage")["continued_in"]], [str(second)])
        self.assertEqual(store.verdicts(kind="owner")[-1]["record"]["continued_in"], str(second))
        self.assertEqual(new.store.state(), "ready")
        self.assertIsNone(store.setting("owner_continuation_intent"))
        failed = Store.open(first, readonly=True)
        self.stores.append(failed)
        self.assertEqual((failed.state(), failed.job()["stop_reason"]), ("failed", "initialize_failed"))

    def test_a_continuation_that_died_while_initializing_can_be_replaced(self):
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        first, second = self.root / "cont-1", self.root / "cont-2"
        # the process dies inside initialize (no exception handler runs): the new job stays 'created', the intent names it
        with mock.patch.object(runner.Session, "initialize", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.cont(session, first)
        self.assertEqual(store.setting("owner_continuation_intent")["output"], str(first))
        with self.assertRaises(runner.RunnerError) as cm:
            self.cont(session, first)
        self.assertIn("--new-job-output", str(cm.exception))
        new = self.cont(session, second)
        self.stores.append(new.store)
        session.release()
        self.assertEqual([c["job"] for c in store.setting("lineage")["continued_in"]], [str(second)])
        self.assertEqual([a["output"] for a in store.events("continuation_failed")[-1]["data"]["failed_attempts"]], [str(first)])

    def test_a_retry_with_other_words_or_allowance_is_refused_and_the_same_ones_finish_it(self):
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        first = self.root / "cont-1"
        words = "make the lens darker"
        with mock.patch.object(Store, "decide_owner_round", side_effect=StateError("crash")):
            with self.assertRaises(StateError):
                self.cont(session, first, text=words)
        intent = store.setting("owner_continuation_intent")
        self.assertEqual((intent["text"], intent["allowance"]["add_micro"], intent["allowance"]["add_operations"], intent["editable_modules"],
                          intent["add_revisions"]), (words, 1_000_000, 3, ["materials"], 3))
        for other in ({"text": "actually: make the frame thinner"}, {"usd": "4", "ops": 6}, {"editable": None}, {"editable": ("materials", "lenses")},
                      {"add_revisions": 2}):
            with self.assertRaises(runner.RunnerError, msg=repr(other)) as cm:
                self.cont(session, first, **dict({"text": words}, **other))
            self.assertIn(words, str(cm.exception))
        self.assertEqual(store.verdicts(kind="owner"), [], "a refused retry records nothing")
        new = self.cont(session, first, text=words)
        self.stores.append(new.store)
        session.release()
        v = store.verdicts(kind="owner")[-1]["record"]
        self.assertEqual(v["text"], new.store.job()["policy"]["owner_instruction"])
        self.assertEqual(v["text"], words)
        self.assertEqual((v["allowance"]["add_micro"], v["allowance"]["add_operations"]), (new.store.job()["cap_micro"], new.store.job()["inference_operation_cap"]))

    def test_a_retry_against_a_job_made_with_other_words_is_refused_even_without_the_recorded_values(self):
        """An intent written before the values were recorded: the new job's own words and caps are the check."""
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        first = self.root / "cont-1"
        with mock.patch.object(Store, "decide_owner_round", side_effect=StateError("crash")):
            with self.assertRaises(StateError):
                self.cont(session, first, text="make the lens darker")
        intent = store.setting("owner_continuation_intent")
        with store.tx():
            store.set_setting("owner_continuation_intent", {k: intent[k] for k in ("round", "output", "authorized_by", "utc")})
        with self.assertRaises(runner.RunnerError) as cm:
            self.cont(session, first, text="actually: make the frame thinner", usd="4", ops=6)
        self.assertIn("make the lens darker", str(cm.exception))
        session.release()
        self.assertEqual(store.verdicts(kind="owner"), [])

    def test_the_cli_retries_a_failed_continuation_in_a_fresh_default_folder(self):
        session, _ = self.round_one()
        store = session.store
        with store.tx():
            store.conn.execute("UPDATE inference_requests SET input_token_count = 999999 WHERE role = 'author'")
        base = ["owner-review", "--job", store.job_dir, "--changes", "t", "--authorized-by", "Shay", "--max-inference-requests", "3", "--budget-usd", "1",
                "--editable-modules", "materials", "--calibration-file", self.calibration, "--no-continue"]
        with mock.patch.object(runner.Session, "host_build_seed", side_effect=executor.WorkerError("docker daemon down")):
            code, out = run_cli(*base)
        self.assertEqual(code, 2, out)
        self.assertIn("--new-job-output", out)
        code, out = run_cli(*base)
        self.assertEqual(code, 0, out)
        second = store.job_dir.parent / f"{store.job_dir.name}-continued-1-2"
        ns = Store.open(second, readonly=True)
        self.stores.append(ns)
        self.assertEqual(ns.state(), "ready")
        self.assertEqual([c["job"] for c in store.setting("lineage")["continued_in"]], [str(second)])


class PendingCalibrationWhileWaiting(PendingCalibrationThroughTheCli):
    """The verifier's status/resume mismatch (round 12): a change round whose calibration row the file refused, then a round
    that ends awaiting_owner (or a needs_attention stop), left the row pending; `status` said `resume` writes it, but
    `resume` on a waiting job returned before writing anything. It now writes the row (under the lease, once) and leaves
    the wait as it was, with no --script and no credential."""

    def waiting_job_with_a_pending_row(self) -> Path:
        from modeler import owner_verdict as mov
        job = self.cli_job()
        r2 = self.root / "r2.json"
        r2.write_text(json.dumps({"steps": round2()}), encoding="utf-8")
        with mock.patch.object(mov, "append_review_record", side_effect=OSError("file locked")):
            code, out = run_cli("owner-review", "--job", job, "--changes", OWNER_TEXT, "--authorized-by", "Shay", "--max-inference-requests", "3",
                                "--budget-usd", "3", "--script", r2, "--calibration-file", self.calibration)
        self.assertEqual(code, 0, out)
        pending, st = self.pending(job)
        self.assertEqual((len(pending or []), st, self.calibration_rows()), (1, "awaiting_owner", []))
        code, out = run_cli("status", "--job", job)
        self.assertIn("1 calibration row(s) pending", out)
        return job

    def test_resume_on_awaiting_owner_writes_the_pending_row_and_keeps_waiting(self):
        job = self.waiting_job_with_a_pending_row()
        code, out = run_cli("resume", "--job", job)
        self.assertEqual(code, 0, out)
        self.assertIn("wrote 1 pending calibration row", out)
        self.assertIn("awaiting your review", out)
        self.assertEqual(self.pending(job), (None, "awaiting_owner"))
        (row,) = self.calibration_rows()
        self.assertEqual(row["verdict"], "changes_requested")
        code, out = run_cli("resume", "--job", job)
        self.assertEqual((code, len(self.calibration_rows())), (0, 1), "a second resume writes nothing")

    def test_resume_on_needs_attention_without_acknowledging_writes_the_pending_row(self):
        job = self.waiting_job_with_a_pending_row()
        s = Store.open(job)
        try:
            s.transition("needs_attention", "test: a stop the owner has not looked at", stop_reason="inference_unknown")
        finally:
            s.close()
        code, out = run_cli("resume", "--job", job)          # no --script, no --acknowledge-attention
        self.assertEqual(code, cli.EXIT_BY_STATE["needs_attention"], out)
        self.assertIn("wrote 1 pending calibration row", out)
        self.assertIn("--acknowledge-attention", out)
        self.assertEqual(self.pending(job), (None, "needs_attention"))
        self.assertEqual(len(self.calibration_rows()), 1)

    def test_a_row_the_file_still_refuses_stays_pending_and_resume_says_so(self):
        from modeler import owner_verdict as mov
        job = self.waiting_job_with_a_pending_row()
        with mock.patch.object(mov, "append_review_record", side_effect=OSError("still locked")):
            code, out = run_cli("resume", "--job", job)
        self.assertEqual(code, cli.EXIT_BY_STATE["failed"], out)
        self.assertIn("1 still pending", out)
        pending, st = self.pending(job)
        self.assertEqual((len(pending or []), st), (1, "awaiting_owner"))


class OrphanedContinuation(OwnerCase):
    """The verifier's orphan (round 12): a crash while linking a continuation left the new job 'ready' and the intent on the
    old job; `owner-review --stop` on the old job then exited 0 and `resume` of the new job spent its allowance under a job
    whose verdict says stop. Decision: an accept or a stop is REFUSED while that new job can still run, and the message
    names it; the owner finishes the link or cancels the new job, then decides again (the intent is then cleared)."""
    real = True

    def cont(self, session: runner.Session, output: Path) -> runner.Session:
        return session.owner_continue_in_new_job(output, text=OWNER_TEXT, allowance=self.allowance("1", 3), authorized_by="Shay", editable=["materials"],
                                                 worker=executor.FakeWorker({}), transport=responses.RefusingTransport(), worker_config=None,
                                                 fingerprints={"t": 1}, calibration_file=self.calibration)

    def orphan(self) -> tuple[runner.Session, Path]:
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        session.acquire()
        new_dir = self.root / "cont-1"
        with mock.patch.object(Store, "decide_owner_round", side_effect=StateError("crash while linking")):
            with self.assertRaises(StateError):
                self.cont(session, new_dir)
        ns = Store.open(new_dir, readonly=True)
        try:
            self.assertEqual(ns.state(), "ready")
        finally:
            ns.close()
        self.assertEqual(session.store.setting("owner_continuation_intent")["output"], str(new_dir))
        return session, new_dir

    def test_stop_and_accept_are_refused_naming_the_new_job(self):
        session, new_dir = self.orphan()
        store = session.store
        for decide in (lambda: session.owner_stop(authorized_by="Shay", calibration_file=self.calibration),
                       lambda: session.owner_accept(authorized_by="Shay", calibration_file=self.calibration)):
            with self.assertRaises(runner.RunnerError) as cm:
                decide()
            self.assertIn(str(new_dir), str(cm.exception))
            self.assertIn("--new-job-output", str(cm.exception), "a ready continuation can still be linked")
            self.assertIn("cancel --job", str(cm.exception))
        self.assertEqual((store.state(), store.verdicts(kind="owner"), self.calibration_rows()), ("awaiting_owner", [], []))
        session.release()
        code, out = run_cli("owner-review", "--job", store.job_dir, "--stop", "--authorized-by", "Shay", "--calibration-file", self.calibration)
        self.assertEqual(code, 2, out)
        self.assertIn(str(new_dir), out)
        self.assertEqual(store.state(), "awaiting_owner")

    def test_after_the_new_job_is_cancelled_the_stop_goes_through_and_clears_the_intent(self):
        session, new_dir = self.orphan()
        store = session.store
        session.release()
        code, out = run_cli("cancel", "--job", new_dir)
        self.assertEqual(code, cli.EXIT_BY_STATE["cancelled"], out)
        session.acquire()
        out = session.owner_stop(authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual((out["state"], store.job()["stop_reason"]), ("unresolved", "owner_stopped"))
        self.assertIsNone(store.setting("owner_continuation_intent"))
        (ev,) = store.events("continuation_abandoned")
        self.assertEqual((ev["data"]["decision"], ev["data"]["intent"]["output"]), ("stop", str(new_dir)))

    def test_finishing_the_link_still_works_after_a_refused_stop(self):
        session, new_dir = self.orphan()
        store = session.store
        with self.assertRaises(runner.RunnerError):
            session.owner_stop(authorized_by="Shay", calibration_file=self.calibration)
        new = self.cont(session, new_dir)
        self.stores.append(new.store)
        new.release()
        session.release()
        self.assertEqual((store.state(), store.job()["stop_reason"]), ("unresolved", "continued_in_new_job"))
        self.assertEqual([c["job"] for c in store.setting("lineage")["continued_in"]], [str(new_dir)])

    def test_a_continuation_that_never_initialized_does_not_block_a_stop(self):
        session, _ = self.round_one(count_rule=lambda payload: 160_000)
        store = session.store
        session.acquire()
        dead = self.root / "cont-dead"
        with mock.patch.object(runner.Session, "initialize", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.cont(session, dead)
        self.assertEqual(store.setting("owner_continuation_intent")["output"], str(dead))
        out = session.owner_stop(authorized_by="Shay", calibration_file=self.calibration)
        session.release()
        self.assertEqual(out["state"], "unresolved", "a job left 'created' never runs: nothing is orphaned")
        self.assertIsNone(store.setting("owner_continuation_intent"))


class LockedRoundDeliveries(OwnerCase):
    """In a locked round the fallback is limited like request_delivery (the round's revisions plus the reviewed one), and
    build_candidate's deliver_if_compatible is refused for a revision whose locked modules differ."""
    real = True

    def test_a_locked_fallback_never_hands_the_owner_a_pre_round_revision(self):
        # r0001 frame A; r0002 = r0001 + a tint (reviewed). The locked bytes of r0001 equal r0002's, but r0001 was made before the round
        steps = [edit("c1", None, frame=demo.PROGRAM_A), fetch("c2"), edit("c3", "r0001", materials=PROGRAM_TINT), fetch("c4"), deliver("c5", "r0002")]
        session, _ = self.start(steps, max_inference_requests=12)
        self.assertEqual(session.run(), "awaiting_owner")
        store = session.store
        s2, _t = self.continue_session(store, [deliver("d0", "r0001"), _resp("d1", "select_revision", {"revision_id": "r0001"})])
        s2.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance("2", 2), authorized_by="Shay", editable=["materials"],
                                 calibration_file=self.calibration)
        self.assertEqual(s2.run(), "awaiting_owner")
        self.assertIn("not made in this round", call_output(store, "d0")["error"])
        r = store.open_owner_round()
        self.assertEqual((r["revision_id"], r["source"]), ("r0002", "fallback"))

    def test_deliver_if_compatible_is_refused_for_a_revision_with_other_locked_modules(self):
        # round 1 makes r0002 (frame B) without building it; the owner reviews r0001 (frame A) and locks all but materials
        unbuilt = _resp("c3", "edit_program", {"base_revision_id": "r0001", "modules": modules(frame={"mode": "replace", "content": FRAME_B, "expected_base_sha256": None}),
                                               "rationale": "x", "expected_changes": ["x"], "build_now": False, "deliver_if_compatible": False})
        session, _ = self.start([edit("c1", None, frame=demo.PROGRAM_A), fetch("c2"), unbuilt, deliver("c4", "r0001")], max_inference_requests=12)
        self.assertEqual(session.run(), "awaiting_owner")
        store = session.store
        self.assertEqual(store.open_owner_round()["revision_id"], "r0001")
        s2, _t = self.continue_session(store, [_resp("d1", "build_candidate", {"revision_id": "r0002", "deliver_if_compatible": True}), fetch("d2")])
        s2.owner_request_changes(text=OWNER_TEXT, allowance=self.allowance("5", 5), authorized_by="Shay", editable=["materials"],
                                 calibration_file=self.calibration)
        s2.run()
        note = call_output(store, "d1").get("delivery_note") or ""
        self.assertTrue(note.startswith("deliver_if_compatible refused"), note)
        self.assertIn("frame", note)
        r = store.open_owner_round()
        self.assertNotEqual(r and r["revision_id"], "r0002", "a revision with another frame reached the owner")


if __name__ == "__main__":
    unittest.main()
