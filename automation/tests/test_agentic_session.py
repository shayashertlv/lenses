"""Whole-offline-session acceptance of ``modeler.agentic``: scripted author conversations on the synthetic worker.

Every test builds a Session the way ``modeler.agentic.demo`` does (synthetic photos, ``ScriptedTransport``,
``FakeWorker``) and asserts what is externally observable afterwards: database rows through ``Store``, files in the
job folder, the payloads the transport captured, the final state and its exit code. No network, no Blender, no Docker.
The two scenarios that were strict xfails when this module was written (stale included_request_id after an incomplete
response; images acknowledged blindly across a compaction) are fixed in runner.py/state.py and run as plain tests.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock

from modeler.agentic import cli, config, demo, evaluation, executor, responses, runner
from modeler.agentic.artifacts import SYNTHETIC
from modeler.agentic.config import ConfigError
from modeler.agentic.state import Store
from test_agentic_support import fresh_dir

KEEP = bool(os.environ.get("LAG_SESSION_KEEP"))
AUTOMATION = Path(__file__).resolve().parents[1]
TOMFORD = AUTOMATION / "data" / "modeler" / "requests" / "tomford_ft1123d.json"

USAGE = {"input_tokens": 1200, "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}, "output_tokens": 300,
         "output_tokens_details": {"reasoning_tokens": 200}, "total_tokens": 1500}
PROGRAM_C = demo.PROGRAM_B + "gl.note('demo program C (after the critic)')\n"
CANONICAL_RENDERS = 7           # modeler.observe.CANONICAL_VIEWS: what one synthetic build queues for the author
CRITIC_FAIL = {"defects": [{"part": "frame", "where_seen": "synthetic render tex_front", "description": "the bridge is missing", "severity": "major"}],
               "matches": [], "uncertainty": "low", "suggested_repairs": ["add the bridge"], "summary": "scripted critic: one major defect"}
FINAL_STEP = demo.demo_script()[-1]
NULL_LOG = lambda *a, **k: None   # noqa: E731


# --------------------------------------------------------------------------- scripted bodies
def step(call_id: str, name: str, args: dict, *, expect: dict | None = None) -> dict:
    s = {"endpoint": "responses", "body": demo._resp(call_id, name, args, rs=f"rs_{call_id}")}
    if expect:
        s["expect"] = expect
    return s


def edit_step(call_id: str, base: str | None, program: str, *, build_now: bool = True, deliver: bool = False, expect: dict | None = None) -> dict:
    return step(call_id, "edit_program", {"base_revision_id": base, "modules": demo.modules(program), "rationale": f"scripted edit {call_id}",
                                          "expected_changes": ["something"], "build_now": build_now, "deliver_if_compatible": deliver}, expect=expect)


def fetch_step(call_id: str, n: int = 6, *, expect: dict | None = None) -> dict:
    return step(call_id, "fetch_pending_images", {"max_images": n}, expect=expect)


def deliver_step(call_id: str, rid: str, *, note: str = "scripted delivery", expect: dict | None = None) -> dict:
    return step(call_id, "request_delivery", {"revision_id": rid, "status_claim": "best_effort", "note": note}, expect=expect)


def text_only_step(call_id: str, text: str = "I have nothing more to add.") -> dict:
    body = demo._resp(call_id, "unused", {}, rs=f"rs_{call_id}")
    body["output"] = body["output"][:2]
    body["output"][1]["content"][0]["text"] = text
    return {"endpoint": "responses", "body": body}


def incomplete_step(call_id: str) -> dict:
    return {"endpoint": "responses", "body": {"id": f"resp_{call_id}", "object": "response", "status": "incomplete", "model": "gpt-6-astra",
                                              "incomplete_details": {"reason": "max_output_tokens"}, "usage": USAGE,
                                              "output": [{"type": "reasoning", "id": f"rs_{call_id}", "summary": [], "encrypted_content": "opaque"}]}}


def unknown_type_step(call_id: str) -> dict:
    body = demo._resp(call_id, "list_evidence", {}, rs=f"rs_{call_id}")
    body["output"].insert(1, {"type": "web_search_call", "id": "ws_1", "status": "completed"})
    return {"endpoint": "responses", "body": body}


def compact_step(*, with_developer: bool = True, expect: dict | None = None) -> dict:
    output = [{"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque"}]
    if with_developer:
        output.append({"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "a developer copy the host drops"}]})
    output.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "kept"}]})
    s = {"endpoint": "compact", "body": {"output": output, "usage": {"input_tokens": 30000, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                                                                     "output_tokens": 500, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 30500}}}
    if expect:
        s["expect"] = expect
    return s


def compaction_count_rule(payload: dict) -> int:
    """30000 (over the 20000 threshold) for an author window that already holds a tool cycle; small otherwise, and
    small again once the window has been compacted, so the runner compacts exactly once."""
    items = payload.get("input", [])
    if any(i.get("type") == "compaction" for i in items):
        return 5000
    return 30000 if any(i.get("type") == "function_call_output" for i in items) else 5000


# --------------------------------------------------------------------------- observation helpers
def window(store: Store, role: str = "author", epoch: int | None = None) -> list[dict]:
    return [r["item"] for r in store.items(role, epoch)]


def outputs_for(items: list[dict], call_id: str) -> list[dict]:
    return [i for i in items if i.get("type") == "function_call_output" and i.get("call_id") == call_id]


def output_text(item: dict) -> dict:
    return json.loads(item["output"][0]["text"])


def image_blocks(item_or_payload) -> list[dict]:
    if "input" in item_or_payload:
        out = []
        for i in item_or_payload["input"]:
            blocks = i.get("content") if isinstance(i.get("content"), list) else (i.get("output") if isinstance(i.get("output"), list) else [])
            out += [b for b in blocks if isinstance(b, dict) and b.get("type") == "input_image"]
        return out
    return [b for b in item_or_payload["output"] if isinstance(b, dict) and b.get("type") == "input_image"]


def payload_image_sha256s(payload: dict) -> set[str]:
    out = set()
    for b in image_blocks(payload):
        out.add(hashlib.sha256(base64.b64decode(b["image_url"].split(",", 1)[1])).hexdigest())
    return out


def user_messages(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("type") == "message" and i.get("role") == "user"]


def message_text(item: dict) -> str:
    return "".join(b.get("text", "") for b in item.get("content", []) if b.get("type") == "input_text")


def sent(transport: responses.ScriptedTransport, kind: str) -> list[dict]:
    return [s["payload"] for s in transport.sent if s["endpoint"] == kind]


def manifest_of(store: Store) -> dict:
    return json.loads((store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))


def request_of(store: Store, rid: str) -> dict:
    return store.request(rid)


def payload_of_request(transport: responses.ScriptedTransport, store: Store, rid: str) -> dict:
    """The captured payload of an author/critic request: the transport keeps every responses payload in order; the
    request rows are numbered in the same order (a compaction request is its own endpoint)."""
    rows = [r for r in store.requests() if r["purpose"] != "compaction"]
    idx = [r["id"] for r in rows].index(rid)
    return sent(transport, "responses")[idx]


# --------------------------------------------------------------------------- the session fixture
class SessionCase(unittest.TestCase):
    """One fresh job folder per test under a short path; the store is closed and the folder removed afterwards."""

    def setUp(self):
        self.root = fresh_dir(None, "session", "s-", env="LAG_SESSION_TMP")
        self.sessions: list[runner.Session] = []

    def tearDown(self):
        for s in self.sessions:
            try:
                s.store.close()
            except Exception:  # noqa: BLE001
                pass
        if not KEEP:
            shutil.rmtree(self.root, ignore_errors=True)

    def policy(self, **over) -> dict:
        base = dict(driver="scripted", worker="fake", intake="synthetic", critic="scripted", final_evaluator="scripted", ar=False, budget_usd="5",
                    max_inference_requests=12, max_output_tokens=4000, max_revisions=4, max_worker_seconds=120, wall_minutes=30, images_per_request=6)
        base.update(over)
        return config.build_policy(**{"owner_review": False, **base})

    def make(self, steps: list[dict], *, scenario: dict | None = None, count_rule=None, name: str = "job", policy_extra: dict | None = None,
             seed_program: Path | None = None, transport_cls=None, **policy_over):
        """``policy_extra``: keys set on the policy after config.build_policy, past its validation (a test value such as
        image_prune_tokens 1, or the owner-revision keys); the CLI passes all of them through build_policy."""
        inputs = self.root / f"{name}.in"
        request = demo.demo_request(inputs)
        translated = config.translate_request(request, inputs)
        transport = (transport_cls or responses.ScriptedTransport)(steps, count_rule=count_rule)
        worker = executor.FakeWorker({} if scenario is None else scenario)
        session = runner.Session.create(self.root / name, translated=translated, policy=dict(self.policy(**policy_over), **(policy_extra or {})),
                                        fingerprints={"test": "session"}, worker=worker, transport=transport, worker_config=None, log=NULL_LOG,
                                        seed_program=seed_program)
        self.sessions.append(session)
        return session, transport, worker, request

    def run_session(self, steps, **kw):
        session, transport, worker, request = self.make(steps, **kw)
        state = session.run()
        return session, transport, worker, request, state

    # shared assertions
    def assert_calls_have_one_output_each_in_order(self, items: list[dict]) -> None:
        calls = [i for i in items if i.get("type") == "function_call"]
        outs = [i for i in items if i.get("type") == "function_call_output"]
        self.assertTrue(calls)
        self.assertEqual([c["call_id"] for c in calls], [o["call_id"] for o in outs], "outputs must mirror the calls, in order")
        for c in calls:
            self.assertEqual(len(outputs_for(items, c["call_id"])), 1, c["call_id"])
            pos = items.index(c)
            following = [i for i in items[pos + 1:] if i.get("type") in ("function_call", "function_call_output")]
            self.assertEqual(following[0].get("type"), "function_call_output", f"{c['call_id']}: the next call/output item is not its output")
            self.assertEqual(following[0]["call_id"], c["call_id"])

    def assert_acknowledged_by_a_completed_request_that_carried_it(self, store: Store, transport, obs: dict) -> None:
        self.assertEqual(obs["state"], "acknowledged", obs)
        self.assertIsNotNone(obs["acknowledged_request_id"], obs)
        self.assertEqual(obs["included_request_id"], obs["acknowledged_request_id"], obs)
        req = request_of(store, obs["acknowledged_request_id"])
        self.assertEqual(req["state"], "completed", obs)
        art = store.artifact(obs["artifact_id"])
        self.assertIn(art["sha256"], payload_image_sha256s(payload_of_request(transport, store, req["id"])),
                      f"{obs['artifact_id']} was acknowledged by {req['id']} whose payload does not carry its bytes")
        events = store.events()
        completed = next(i for i, e in enumerate(events) if e["kind"] == "inference_completed" and e["data"]["request"] == req["id"])
        acked = next(i for i, e in enumerate(events) if e["kind"] == "images_acknowledged" and e["data"]["request"] == req["id"])
        self.assertGreater(acked, completed, "acknowledgment must follow the completion of the request that carried the image")


# =========================================================================== (1) the demo scenario end to end
class DemoScenario(SessionCase):
    def setUp(self):
        super().setUp()
        inputs = self.root / "job.in"
        self.request = demo.demo_request(inputs)
        self.sealed = demo.sealed_pixel_hashes(self.request["photos"])
        translated = config.translate_request(self.request, inputs)
        self.transport = responses.ScriptedTransport(demo.demo_script(self.sealed))
        self.worker = executor.FakeWorker(demo.demo_fake_scenario())
        self.session = runner.Session.create(self.root / "job", translated=translated, policy=self.policy(), fingerprints={"test": "demo"},
                                             worker=self.worker, transport=self.transport, worker_config=None, log=NULL_LOG)
        self.sessions.append(self.session)
        self.state = self.session.run()
        self.store = self.session.store

    def test_final_state_and_stop_reason(self):
        self.assertEqual(self.state, "unresolved")
        job = self.store.job()
        self.assertEqual(job["stop_reason"], "synthetic_demo_complete")
        self.assertEqual(cli.EXIT_BY_STATE[self.state], 0)

    def test_two_revisions_with_distinct_programs(self):
        revs = self.store.revisions()
        self.assertEqual([r["id"] for r in revs], ["r0001", "r0002"])
        self.assertNotEqual(revs[0]["program_set_sha256"], revs[1]["program_set_sha256"])
        self.assertEqual(revs[0]["state"], "build_failed")
        self.assertFalse((revs[0]["compatibility"] or {}).get("compatible"))
        self.assertEqual(revs[1]["state"], "compatible")
        self.assertTrue(revs[1]["compatibility"]["compatible"])
        self.assertTrue(revs[1]["synthetic"])
        self.assertEqual(revs[1]["parent_id"], "r0001")

    def test_scripted_build_error_is_visible_to_the_author(self):
        items = window(self.store)
        out = outputs_for(items, "call_2")
        self.assertEqual(len(out), 1)
        text = output_text(out[0])
        self.assertIn("SyntheticError: scripted build failure in module frame", json.dumps(text))
        self.assertEqual(text["revision"], "r0001")
        self.assertFalse(text["built"])
        self.assertEqual(text["state"], "build_failed")
        op = self.store.operations(call_id="call_2")[0]
        self.assertEqual(op["state"], "failed")

    def test_every_function_call_has_exactly_one_output_in_order(self):
        items = window(self.store)
        self.assert_calls_have_one_output_each_in_order(items)
        self.assertEqual([i["call_id"] for i in items if i.get("type") == "function_call"], ["call_1", "call_2", "call_3", "call_3b", "call_4", "call_5", "call_6"])

    def test_second_request_replays_the_first_tool_output(self):
        payloads = sent(self.transport, "responses")
        self.assertEqual(len(payloads), 8)
        second = payloads[1]["input"]
        self.assertTrue(any(i.get("type") == "function_call_output" and i["call_id"] == "call_1" for i in second))
        self.assertEqual(second[0]["role"], "developer")
        # the whole window is replayed: every later request grows the earlier one
        for a, b in zip(payloads[:5], payloads[1:5]):
            self.assertGreater(len(b["input"]), len(a["input"]))

    def test_renders_split_six_plus_one_and_acknowledged_by_the_carrying_request(self):
        items = window(self.store)
        build_out = outputs_for(items, "call_3")[0]
        text = output_text(build_out)
        self.assertEqual(text["deferred_images"], 1)
        self.assertEqual(text["images_attached"], 6)
        self.assertEqual(text["images_attached"] + text["deferred_images"], CANONICAL_RENDERS, "attached + deferred: every image of the build")
        self.assertNotIn("images_queued", text)
        self.assertEqual(len(image_blocks(build_out)), 6)
        fetch_out = outputs_for(items, "call_3b")[0]
        self.assertEqual(len(image_blocks(fetch_out)), 1)
        self.assertEqual(output_text(fetch_out)["remaining_pending"], 0)
        obs = self.store.observations(revision_id="r0002")
        self.assertEqual(len(obs), CANONICAL_RENDERS)
        self.assertTrue(all(o["required"] for o in obs))
        by_request = {}
        for o in obs:
            self.assert_acknowledged_by_a_completed_request_that_carried_it(self.store, self.transport, o)
            by_request.setdefault(o["acknowledged_request_id"], []).append(o["artifact_id"])
        self.assertEqual(sorted(len(v) for v in by_request.values()), [1, 6])
        self.assertEqual(self.store.observations(state="pending"), [])
        self.assertEqual(self.store.observations(state="included"), [])

    def test_only_the_first_user_message_is_human(self):
        items = window(self.store)
        users = user_messages(items)
        self.assertEqual(len(users), 1)
        self.assertEqual(items[0], users[0])
        self.assertIn("Host package", message_text(users[0]))

    def test_critic_has_fresh_context_and_no_sealed_pixels(self):
        critic_items = window(self.store, "critic", 1)
        self.assertEqual(self.store.epoch("critic"), 1)
        self.assertEqual(len(user_messages(critic_items)), 1)
        self.assertEqual(critic_items[0], user_messages(critic_items)[0])
        payloads = [p for p in sent(self.transport, "responses") if [t["name"] for t in p["tools"]] == ["report_critique"]]
        self.assertEqual(len(payloads), 1)
        critic_payload = payloads[0]
        self.assertEqual(critic_payload["tool_choice"], {"type": "function", "name": "report_critique"})
        pix = responses.payload_image_pixel_hashes(critic_payload)
        self.assertTrue(self.sealed)
        self.assertFalse(set(pix) & set(self.sealed), "a sealed photograph reached the critic")
        shas = payload_image_sha256s(critic_payload)
        renders = [a for a in self.store.artifacts(revision_id="r0002") if a["kind"] == "render"]
        self.assertEqual(len(renders), CANONICAL_RENDERS)
        for a in renders:
            self.assertIn(a["sha256"], shas)
        self.assertEqual(len(image_blocks(critic_payload)), 3 + CANONICAL_RENDERS)
        verdicts = self.store.verdicts(kind="critic")
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0]["revision_id"], "r0002")
        self.assertEqual(verdicts[0]["verdict"], "pass")
        self.assertEqual(verdicts[0]["bindings"]["request"], "q0006")
        critique = output_text(outputs_for(window(self.store), "call_4")[0])["critique"]
        self.assertEqual(critique["verdict"], "pass")

    def test_final_scripted_step_is_not_consumed_for_a_synthetic_revision(self):
        self.assertEqual(len(self.transport.steps), 1)
        remaining = self.transport.steps[0]
        self.assertEqual(remaining["expect"]["tools"], ["report_evaluation"])
        self.assertEqual(self.store.verdicts(kind="final"), [])
        self.assertEqual(self.store.requests(role="final"), [])

    def test_manifest_is_synthetic_only_without_a_glb(self):
        m = manifest_of(self.store)
        self.assertEqual(m["deliverable_status"], "synthetic_only")
        self.assertIsNone(m["asset"])
        self.assertFalse(m["axes"]["compatibility"]["compatible"])
        self.assertTrue(m["axes"]["compatibility"]["synthetic"])
        self.assertEqual(m["axes"]["visual"]["automatic_verdict"], "unmeasured")
        self.assertEqual(m["state"], "unresolved")
        self.assertEqual(m["stop_reason"], "synthetic_demo_complete")
        self.assertEqual(m["revision"]["id"], "r0002")
        self.assertEqual(m["selected_revision"], "r0002")
        self.assertFalse((self.store.job_dir / "deliverable" / "model.glb").exists())
        self.assertTrue((self.store.job_dir / "deliverable" / "report.md").is_file())
        self.assertTrue((self.store.job_dir / "deliverable" / "receipts.json").is_file())
        self.assertEqual(self.store.job()["deliverable"], m)

    def test_status_report_is_consistent(self):
        rep = runner.status_report(self.store)
        self.assertEqual(rep["state"], "unresolved")
        self.assertEqual(rep["stop_reason"], "synthetic_demo_complete")
        self.assertEqual(rep["selected_revision"], "r0002")
        self.assertEqual(rep["current_revision"], "r0002")
        self.assertEqual([r["id"] for r in rep["revisions"]], ["r0001", "r0002"])
        self.assertEqual([r["compatible"] for r in rep["revisions"]], [False, True])
        self.assertEqual(rep["observations"], {"pending": 0, "included": 0, "acknowledged": CANONICAL_RENDERS, "failed": 0, "deferred": 0})
        self.assertEqual(rep["deliverable_status"], "synthetic_only")
        self.assertEqual(rep["epochs"], {"author": 1})
        self.assertEqual(len(rep["requests"]), 8)
        self.assertTrue(all(r["state"] == "completed" for r in rep["requests"]))
        self.assertEqual([r["role"] for r in rep["requests"]].count("critic"), 1)
        self.assertEqual(rep["budget"]["operations_used"], 8)
        self.assertEqual(rep["budget"]["held_micro"], 0)
        self.assertEqual(rep["budget"]["unknown_liability_micro"], 0)
        self.assertGreater(rep["budget"]["settled_micro"], 0)
        self.assertEqual(rep["unknown_requests"], [])
        self.assertIsNone(rep["settings"]["pending_delivery"])
        self.assertEqual(len(rep["operations"]), 7)
        self.assertEqual([o["state"] for o in rep["operations"]].count("failed"), 1)
        self.assertEqual(rep["axes"], manifest_of(self.store)["axes"])
        self.assertIsNone(rep["lease"]["holder"])


class IncompleteResponse(SessionCase):
    def test_incomplete_response_returns_images_to_pending_and_executes_nothing(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), incomplete_step("x1"), incomplete_step("x2")]
        session, transport, worker, _, state = self.run_session(steps)
        store = session.store
        self.assertEqual(state, "needs_attention")
        self.assertEqual(store.job()["stop_reason"], "responses_incomplete")
        obs = store.observations(revision_id="r0001")
        self.assertEqual(len(obs), CANONICAL_RENDERS)
        self.assertTrue(all(o["state"] == "pending" for o in obs), [o["state"] for o in obs])
        self.assertTrue(all(o["acknowledged_request_id"] is None for o in obs))
        carried = [o for o in obs if o["included_request_id"] == "q0002"]
        self.assertEqual(len(carried), 6, "the six images the incomplete response carried went back to the queue")
        self.assertEqual([r["state"] for r in store.requests()], ["completed", "incomplete", "incomplete"])
        self.assertEqual([o["tool_name"] for o in store.operations()], ["edit_program"])
        self.assertEqual(worker.count, 1)
        items = window(store)
        users = user_messages(items)
        self.assertEqual(len(users), 2)
        notice = json.loads(message_text(users[1]))["host_notice"]
        self.assertIn("incomplete", notice)
        self.assertIn("not applied", notice)
        self.assertEqual(items[-1], users[1], "the notice is the last item; nothing from the incomplete bodies was replayed")
        self.assertEqual(sum(1 for i in items if i.get("type") == "reasoning"), 1)
        self.assertTrue(any(e["data"]["state"] == "ready" and e["data"]["reason"] == "incomplete response; asking again" for e in store.events("transition")))
        self.assertEqual(store.setting("consecutive_incomplete"), 2)

    def test_images_refetched_after_an_incomplete_response_are_acknowledged_and_deliverable(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), incomplete_step("x1"), fetch_step("call_2", 6), fetch_step("call_3", 6),
                 deliver_step("call_4", "r0001")]
        session, transport, worker, _, state = self.run_session(steps)
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        self.assertEqual(len(store.observations(state="acknowledged")), CANONICAL_RENDERS)
        self.assertEqual(manifest_of(store)["revision"]["id"], "r0001")


# =========================================================================== (2) critic fail -> repair
class CriticFailThenRepair(SessionCase):
    def test_major_defect_leads_to_a_third_revision_that_is_delivered(self):
        base = demo.demo_script()[:5]
        steps = base + [
            {"endpoint": "responses", "expect": {"tools": ["report_critique"], "has_image": True}, "body": demo._resp("call_c", "report_critique", CRITIC_FAIL, rs="rs_c")},
            edit_step("call_5", "r0002", PROGRAM_C, expect={"contains_call_output": "call_4"}),
            fetch_step("call_5b", 6, expect={"contains_call_output": "call_5", "has_text": ["deferred_images"]}),
            deliver_step("call_6", "r0003", expect={"contains_call_output": "call_5b"}),
            FINAL_STEP]
        session, transport, worker, _, state = self.run_session(steps, scenario=demo.demo_fake_scenario())
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        self.assertEqual([r["id"] for r in store.revisions()], ["r0001", "r0002", "r0003"])
        r3 = store.revision("r0003")
        self.assertEqual(r3["parent_id"], "r0002")
        self.assertEqual(r3["state"], "compatible")
        verdict = store.verdicts(kind="critic")
        self.assertEqual([(v["revision_id"], v["verdict"]) for v in verdict], [("r0002", "fail")])
        critique = output_text(outputs_for(window(store), "call_4")[0])["critique"]
        self.assertEqual(critique["verdict"], "fail")
        self.assertEqual(critique["defects"][0]["severity"], "major")
        m = manifest_of(store)
        self.assertEqual(m["revision"]["id"], "r0003")
        self.assertEqual(m["selected_revision"], "r0003")
        self.assertEqual(m["final_evaluation"]["delivery"]["revision"], "r0003")
        self.assertEqual(store.job()["selected_revision"], "r0003")
        self.assertEqual(len(store.observations(revision_id="r0003", state="acknowledged")), CANONICAL_RENDERS)
        self.assertEqual(worker.count, 3)
        self.assertEqual(len(transport.steps), 1)
        self.assert_calls_have_one_output_each_in_order(window(store))


# =========================================================================== (3) delivery refused before the images were received
class DeliveryNeedsReceivedImages(SessionCase):
    def test_request_delivery_is_refused_until_every_required_image_was_received(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), deliver_step("call_2", "r0001", expect={"contains_call_output": "call_1", "has_image": True}),
                 fetch_step("call_3", 6, expect={"contains_call_output": "call_2"}), deliver_step("call_4", "r0001", expect={"contains_call_output": "call_3", "has_image": True})]
        session, transport, worker, _, state = self.run_session(steps)
        store = session.store
        items = window(store)
        refused = output_text(outputs_for(items, "call_2")[0])
        self.assertIn("error", refused)
        self.assertIn("1 required images you have not received", refused["error"])
        self.assertIn("fetch_pending_images", refused["error"])
        self.assertEqual(store.operations(call_id="call_2")[0]["state"], "failed")
        self.assertEqual(len(store.requests()), 4, "the job continued after the refusal")
        self.assertEqual([o["state"] for o in store.operations()], ["completed", "failed", "completed", "completed"])
        self.assertFalse(any(e["kind"] == "deliverable_written" and e["data"].get("stop_reason") != "delivered_by_author" for e in store.events()))
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        m = manifest_of(store)
        self.assertEqual(m["revision"]["id"], "r0001")
        self.assertEqual(m["final_evaluation"]["delivery"]["revision"], "r0001")
        self.assertEqual(len(store.observations(state="acknowledged")), CANONICAL_RENDERS)
        self.assertEqual(len(store.revisions()), 1)


# =========================================================================== (4) deliver_if_compatible
class DeliverIfCompatible(SessionCase):
    def test_a_following_tool_call_cancels_the_automatic_delivery(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A, deliver=True), step("call_2", "read_program", {"revision_id": None}, expect={"contains_call_output": "call_1"}),
                 deliver_step("call_3", "r0001", note="explicit after the cancelled auto delivery", expect={"contains_call_output": "call_2"})]
        session, transport, worker, _, state = self.run_session(steps, images_per_request=7)
        store = session.store
        build_text = output_text(outputs_for(window(store), "call_1")[0])
        self.assertTrue(build_text["deliver_if_compatible"])
        self.assertIn("delivery_note", build_text)
        self.assertEqual(len(image_blocks(outputs_for(window(store), "call_1")[0])), CANONICAL_RENDERS)
        cancelled = store.events("auto_delivery_cancelled")
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["data"]["by"], ["read_program"])
        self.assertEqual(store.events("auto_delivery_deferred"), [])
        self.assertEqual(store.operations(call_id="call_2")[0]["state"], "completed")
        self.assertEqual(len(store.requests()), 3, "the job continued after the cancellation")
        self.assertEqual(state, "unresolved")
        m = manifest_of(store)
        self.assertEqual(m["final_evaluation"]["delivery"]["note"], "explicit after the cancelled auto delivery")
        self.assertEqual(m["revision"]["id"], "r0001")
        self.assertIsNone(store.setting("pending_delivery"))

    def test_request_delivery_for_that_revision_confirms_the_automatic_delivery(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A, deliver=True), deliver_step("call_2", "r0001", expect={"contains_call_output": "call_1", "has_image": True}), FINAL_STEP]
        session, transport, worker, _, state = self.run_session(steps, images_per_request=7)
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        self.assertEqual(store.events("auto_delivery_cancelled"), [])
        self.assertEqual(store.events("auto_delivery_deferred"), [])
        self.assertEqual(len(store.requests()), 2)
        self.assertEqual(len(store.revisions()), 1)
        m = manifest_of(store)
        self.assertEqual(m["revision"]["id"], "r0001")
        self.assertEqual(m["selected_revision"], "r0001")
        self.assertEqual(m["final_evaluation"]["delivery"]["revision"], "r0001")
        self.assertEqual(len(store.observations(revision_id="r0001", state="acknowledged")), CANONICAL_RENDERS)
        self.assertEqual(len(transport.steps), 1)

    def test_a_text_only_reply_confirms_the_automatic_delivery(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A, deliver=True), text_only_step("t1", "Looks fine; deliver it.")]
        session, transport, worker, _, state = self.run_session(steps, images_per_request=7)
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        m = manifest_of(store)
        self.assertEqual(m["revision"]["id"], "r0001")
        self.assertEqual(m["final_evaluation"]["delivery"]["note"], "deliver_if_compatible on build")
        self.assertEqual(m["final_evaluation"]["delivery"]["confirmed_by"], "q0002")
        self.assertEqual(store.operations(), store.operations(call_id="call_1"))
        self.assertEqual(store.events("fallback_delivery"), [])

    def test_automatic_delivery_is_deferred_while_a_required_image_is_pending(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A, deliver=True), fetch_step("call_2", 6, expect={"contains_call_output": "call_1"}),
                 deliver_step("call_3", "r0001", expect={"contains_call_output": "call_2"})]
        session, transport, worker, _, state = self.run_session(steps, images_per_request=6)
        store = session.store
        deferred = store.events("auto_delivery_deferred")
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0]["data"], {"revision": "r0001", "pending": 1})
        self.assertEqual(store.events("auto_delivery_cancelled"), [])
        self.assertTrue(any(e["data"]["state"] == "needs_observation" for e in store.events("transition")))
        self.assertEqual(len(store.requests()), 3)
        self.assertEqual(state, "unresolved")
        self.assertEqual(manifest_of(store)["final_evaluation"]["delivery"]["note"], "scripted delivery")


# =========================================================================== (5) invalid arguments
class InvalidArguments(SessionCase):
    def test_rejected_calls_produce_invalid_arguments_outputs_and_nothing_runs(self):
        dup = step("call_1", "fetch_pending_images", {})
        dup["body"]["output"][2]["arguments"] = '{"max_images": 3, "max_images": 4}'
        steps = [dup, step("call_2", "make_coffee", {"size": "large"}, expect={"contains_call_output": "call_1"}),
                 fetch_step("call_3", 99, expect={"contains_call_output": "call_2"}), text_only_step("t1"), text_only_step("t2")]
        session, transport, worker, _, state = self.run_session(steps)
        store = session.store
        items = window(store)
        for call_id, needle in (("call_1", "Duplicate JSON key"), ("call_2", "unknown tool 'make_coffee'"), ("call_3", "strict schema of fetch_pending_images")):
            outs = outputs_for(items, call_id)
            self.assertEqual(len(outs), 1, call_id)
            text = output_text(outs[0])
            self.assertEqual(text["category"], "invalid_arguments", call_id)
            self.assertIn(needle, text["error"], call_id)
            self.assertEqual(image_blocks(outs[0]), [])
            ops = store.operations(call_id=call_id)
            self.assertEqual(len(ops), 1)
            self.assertEqual(ops[0]["state"], "failed")
            self.assertEqual(ops[0]["result"]["category"], "invalid_arguments")
            self.assertTrue(ops[0]["output_committed"])
        self.assertEqual(store.operations(call_id="call_2")[0]["tool_name"], "invalid")
        self.assertEqual(store.operations(call_id="call_3")[0]["tool_name"], "fetch_pending_images")
        self.assertEqual(len(store.events("invalid_arguments")), 3)
        self.assertEqual(store.revisions(), [])
        self.assertEqual(worker.count, 0)
        self.assertEqual(store.events("worker_launched"), [])
        self.assertEqual(store.events("tool_completed"), [])
        self.assertEqual(len(store.requests()), 5, "the job continued after each rejection")
        self.assert_calls_have_one_output_each_in_order(items)
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "author_stopped_without_delivery")
        self.assertEqual(manifest_of(store)["deliverable_status"], "none")


# =========================================================================== (6) text-only responses
class TextOnlyResponses(SessionCase):
    def test_first_text_only_gets_a_notice_and_the_second_ends_the_job(self):
        steps = [text_only_step("t1", "Let me think about the frame first."), text_only_step("t2", "I am done."), FINAL_STEP]
        session, transport, worker, _, state = self.run_session(steps)
        store = session.store
        items = window(store)
        users = user_messages(items)
        self.assertEqual(len(users), 2)
        notice = json.loads(message_text(users[1]))["host_notice"]
        self.assertIn("no tool was called", notice)
        self.assertIn("request_delivery", notice)
        transitions = [(e["data"]["state"], e["data"]["reason"]) for e in store.events("transition")]
        self.assertIn(("ready", "text-only response"), transitions)
        self.assertIn(("ready", "author stopped without delivery"), transitions)
        texts = [e["data"]["texts"] for e in store.events("text_only_response")]
        self.assertEqual(texts, [["Let me think about the frame first."], ["I am done."]])
        self.assertEqual(len(store.requests()), 2)
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "author_stopped_without_delivery")
        self.assertEqual(store.events("fallback_delivery")[0]["data"], {"stop_reason": "author_stopped_without_delivery", "revision": None})
        m = manifest_of(store)
        self.assertIsNone(m["asset"])
        self.assertIsNone(m["revision"])
        self.assertEqual(m["deliverable_status"], "none")
        self.assertEqual(m["stop_reason"], "author_stopped_without_delivery")
        self.assertEqual(store.revisions(), [])
        self.assertEqual(store.operations(), [])
        self.assertEqual(len(transport.steps), 1, "no final evaluation without a revision")
        self.assertEqual(cli.EXIT_BY_STATE[state], 0)


# =========================================================================== (7) unknown output item type
class UnknownOutputType(SessionCase):
    def test_unknown_item_type_stops_for_attention_and_preserves_the_bytes(self):
        steps = [unknown_type_step("call_1"), text_only_step("never")]
        session, transport, worker, _, state = self.run_session(steps)
        store = session.store
        self.assertEqual(state, "needs_attention")
        self.assertEqual(store.job()["stop_reason"], "unknown_output_type")
        self.assertEqual(store.operations(), [])
        self.assertEqual(worker.count, 0)
        self.assertEqual(len(store.requests()), 1)
        req = store.request("q0001")
        self.assertEqual(req["state"], "completed")
        rp = Path(req["response_path"])
        self.assertTrue(rp.is_file())
        self.assertEqual(rp, store.job_dir / "host" / "requests" / "q0001" / "response.json")
        self.assertEqual(rp.read_bytes(), responses.canonical(steps[0]["body"]))
        self.assertEqual(hashlib.sha256(rp.read_bytes()).hexdigest(), req["response_sha256"])
        self.assertEqual(store.events("unknown_output_types")[0]["data"]["types"], ["web_search_call"])
        self.assertEqual(len(window(store)), 1, "nothing of the response was replayed into the window")
        self.assertEqual(len(transport.steps), 1, "no further request was sent")
        self.assertFalse((store.job_dir / "deliverable").exists())
        self.assertEqual(cli.EXIT_BY_STATE[state], 3)


# =========================================================================== (8) + (9) budget exhaustion and the fallback
class BudgetExhaustion(SessionCase):
    def test_operation_cap_falls_back_to_the_observed_revision_not_the_unobserved_one(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), fetch_step("call_2", 6, expect={"contains_call_output": "call_1"}),
                 edit_step("call_3", "r0001", demo.PROGRAM_B, expect={"contains_call_output": "call_2"}), deliver_step("never", "r0002")]
        session, transport, worker, _, state = self.run_session(steps, images_per_request=6, max_inference_requests=3)
        store = session.store
        self.assertEqual(state, "budget_exhausted")
        self.assertEqual(store.job()["stop_reason"], "budget_exhausted")
        self.assertEqual([r["id"] for r in store.revisions()], ["r0001", "r0002"])
        self.assertTrue(store.revision("r0002")["compatibility"]["compatible"], "r0002 is 'compatible' yet unobserved")
        self.assertEqual(len(store.observations(revision_id="r0001", state="acknowledged")), CANONICAL_RENDERS)
        self.assertEqual(len(store.observations(revision_id="r0002", state="acknowledged")), 0)
        self.assertEqual(store.events("fallback_delivery")[0]["data"], {"stop_reason": "budget_exhausted", "revision": "r0001"})
        self.assertEqual(len(store.events("budget_exhausted")), 1)
        self.assertIn("inference-operation cap of 3", store.events("budget_exhausted")[0]["data"]["error"])
        m = manifest_of(store)
        self.assertEqual(m["state"], "budget_exhausted")
        self.assertEqual(m["stop_reason"], "budget_exhausted")
        self.assertEqual(m["revision"]["id"], "r0001")
        self.assertEqual(m["deliverable_status"], "synthetic_only")
        self.assertIsNone(m["asset"])
        self.assertIsNone(m["final_evaluation"]["delivery"])
        self.assertEqual(m["budget"]["operations_used"], 3)
        self.assertEqual(m["budget"]["operations_cap"], 3)
        self.assertEqual(len(store.requests()), 3, "the refused request was never inserted")
        self.assertEqual(len(transport.steps), 1, "the fourth scripted answer was never asked for")
        self.assertEqual(store.verdicts(kind="final"), [])
        self.assertEqual(cli.EXIT_BY_STATE[state], 4)

    def test_dollar_cap_wins_over_a_compatible_observed_revision(self):
        counter = itertools.count(1)
        rule = lambda payload: responses.default_count_rule(payload) if next(counter) <= 2 else 900_000  # noqa: E731
        steps = [edit_step("call_1", None, demo.PROGRAM_A), step("call_2", "read_program", {"revision_id": None}, expect={"contains_call_output": "call_1"}),
                 deliver_step("never", "r0001")]
        session, transport, worker, _, state = self.run_session(steps, images_per_request=7, count_rule=rule)
        store = session.store
        self.assertEqual(state, "budget_exhausted")
        self.assertEqual(len(store.observations(revision_id="r0001", state="acknowledged")), CANONICAL_RENDERS)
        self.assertIn("would exceed the cap", store.events("budget_exhausted")[0]["data"]["error"])
        m = manifest_of(store)
        self.assertEqual(m["stop_reason"], "budget_exhausted")
        self.assertEqual(m["revision"]["id"], "r0001")
        self.assertEqual(m["state"], "budget_exhausted")
        self.assertEqual(m["budget"]["operations_used"], 2)
        self.assertEqual(m["budget"]["held_micro"], 0)
        self.assertEqual(len(transport.steps), 1)
        self.assertEqual(sent(transport, "count")[-1]["model"], "gpt-6-astra")
        self.assertEqual(cli.EXIT_BY_STATE[state], 4)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.finish(store, state)
        self.assertEqual(code, 4)
        printed = json.loads(buf.getvalue())
        self.assertEqual(printed["exit_code"], 4)
        self.assertEqual(printed["state"], "budget_exhausted")
        self.assertEqual(printed["deliverable_status"], "synthetic_only")


# =========================================================================== (10) request translation and policy validation
class RequestTranslation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads(TOMFORD.read_text(encoding="utf-8"))

    def test_tomford_translates_with_one_sealed_photo_and_provenance_notes(self):
        t = config.translate_request(self.raw, TOMFORD.parent)
        req = t["request"]
        self.assertEqual(req["product_id"], "tomford_ft1123d")
        self.assertEqual(len(req["photos"]), 5)
        held = [p for p in req["photos"] if p["held_out"]]
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["view"], "angled")
        self.assertEqual(held[0]["id"], "angled")
        self.assertEqual(req["held_out_views"], ["angled"])
        self.assertEqual(req["dimensions"], {})
        self.assertTrue(all(Path(p["path"]).is_file() and len(p["sha256"]) == 64 and p["bytes"] > 0 for p in req["photos"]))
        self.assertEqual([p["view"] for p in req["photos"]], ["front", "back", "left", "angled", "unknown"])
        self.assertEqual(req["photos"][4]["id"], "photo05")
        prov = t["provenance"]
        self.assertEqual(prov["legacy_limits"], self.raw["limits"])
        self.assertEqual(prov["legacy_author"], {"driver": "astra"})
        notes = t["notes"]
        self.assertEqual(notes, prov["translation_notes"])
        self.assertTrue(any("legacy limits" in n and "provenance only" in n for n in notes), notes)
        self.assertTrue(any("legacy author.driver 'astra' ignored" in n for n in notes), notes)
        self.assertTrue(any("held_out flag and held_out_views both name it; one sealed reservation" in n for n in notes), notes)
        self.assertEqual(len(prov["original_request_sha256"]), 64)
        self.assertNotIn("limits", req)
        self.assertNotIn("author", req)

    def test_paid_switches_credentials_unknown_keys_and_donors_are_refused(self):
        with self.assertRaises(ConfigError):
            config.translate_request(dict(self.raw, budget_usd=100), TOMFORD.parent)
        with self.assertRaises(ConfigError):
            config.translate_request(dict(self.raw, author={"api_key": "x"}), TOMFORD.parent)
        with self.assertRaises(ConfigError):
            config.translate_request(dict(self.raw, something_new=1), TOMFORD.parent)
        with self.assertRaises(ConfigError):
            config.translate_request(dict(self.raw, donor={"glb": "x.glb"}), TOMFORD.parent)
        with self.assertRaises(ConfigError):
            config.translate_request(dict(self.raw, limits=dict(self.raw["limits"], max_inference_requests=99)), TOMFORD.parent)
        with self.assertRaises(ConfigError):
            config.translate_request(dict(self.raw, photos=self.raw["photos"] + [dict(self.raw["photos"][0], token="abc")]), TOMFORD.parent)


class PolicyValidation(unittest.TestCase):
    def offline(self, **kw):
        return config.build_policy(driver="scripted", worker="fake", **kw)

    def test_non_finite_negative_zero_bool_and_unbounded_values_are_rejected(self):
        for bad in (float("nan"), float("inf"), -float("inf"), -1, 0, True, False, 41, 2.5):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                self.offline(max_revisions=bad)
        for name, bad in (("max_inference_requests", 51), ("max_inference_requests", 0), ("images_per_request", 31), ("images_per_request", 0),
                          ("wall_minutes", 0), ("max_worker_seconds", 9), ("max_output_tokens", 255), ("max_output_tokens", 128_001),
                          ("compact_threshold_tokens", 19_999), ("compact_threshold_tokens", float("nan")), ("compact_output_bound_tokens", 1023),
                          ("compact_output_bound_tokens", True), ("compact_output_bound_tokens", float("inf"))):
            with self.assertRaises(ConfigError, msg=f"{name}={bad!r}"):
                self.offline(**{name: bad})
        for bad in (float("nan"), float("inf"), -1, 0, True, 201, "abc", "1e999"):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                self.offline(budget_usd=bad)
        ok = self.offline(max_revisions=3, compact_threshold_tokens=20_000, compact_output_bound_tokens=4096, budget_usd="200")
        self.assertEqual(ok["max_revisions"], 3)
        self.assertEqual(ok["compact_threshold_tokens"], 20_000)
        self.assertEqual(ok["compact_output_bound_tokens"], 4096)
        self.assertEqual(ok["cap_micro"], 200 * 1_000_000)
        self.assertIsNone(self.offline()["compact_output_bound_tokens"])
        with self.assertRaises(ConfigError):
            self.offline(reasoning_effort="ultra")

    def test_paid_mode_needs_allow_paid_every_cap_and_a_real_worker(self):
        caps = dict(budget_usd="10", max_inference_requests=5, max_output_tokens=4000, max_revisions=3, max_worker_seconds=120)
        with self.assertRaises(ConfigError):
            config.build_policy(driver="responses", worker="docker", **caps)
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="fake", allow_paid=True, **caps)
        for missing in caps:
            partial = {k: v for k, v in caps.items() if k != missing}
            with self.assertRaises(ConfigError, msg=missing):
                config.build_policy(driver="responses", worker="docker", allow_paid=True, **partial)
        with self.assertRaises(ConfigError):
            config.build_policy(driver="responses", worker="fake", allow_paid=True, **caps)
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="fake", critic="responses")
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="fake", intake="astra")
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="fake", final_evaluator="responses")
        paid = config.build_policy(driver="responses", worker="docker", allow_paid=True, **caps)
        self.assertTrue(paid["allow_paid"])
        self.assertEqual(paid["cap_micro"], 10 * 1_000_000)
        self.assertEqual(paid["max_inference_requests"], 5)
        self.assertEqual(paid["model"], "gpt-6-astra")
        with self.assertRaises(ConfigError):
            config.build_policy(driver="responses", worker="docker", allow_paid=True, **dict(caps, budget_usd="200.01"))

    def test_regions_and_tiers_follow_the_frozen_tariff(self):
        eu = self.offline(region="eu")
        self.assertEqual(eu["region"], "eu")
        for region in ("global", "us"):
            self.assertEqual(self.offline(region=region)["region"], region)
        with self.assertRaises(ValueError):          # PricingError (a ValueError) from Tariff.frozen, not ConfigError: see the notes
            self.offline(region="mars")
        with self.assertRaises(ValueError):
            self.offline(service_tier="flex")
        for bad in ("astra", "", "http"):
            with self.assertRaises(ConfigError, msg=bad):
                config.build_policy(driver=bad, worker="fake")
        with self.assertRaises(ConfigError):
            config.build_policy(driver="scripted", worker="podman")


# =========================================================================== (11) compaction
class Compaction(SessionCase):
    def compaction_steps(self, *, compact: dict | None = None):
        return [edit_step("call_1", None, demo.PROGRAM_A),
                step("call_2", "read_program", {"revision_id": None}, expect={"contains_call_output": "call_1", "has_image": True}),
                compact if compact is not None else compact_step(expect={"last_item_type": "function_call_output", "contains_call_output": "call_2"}),
                step("call_3", "crop_image", {"artifact_id": "img0007", "x0": 0, "y0": 0, "x1": 120, "y1": 90}),
                deliver_step("call_4", "r0001", expect={"contains_call_output": "call_3", "has_image": True})]

    def test_rolling_compaction_replays_epoch_two_marked(self):
        """The rolling counterpart (config.build_policy's default): the post-compaction request replays exactly epoch 2, with
        a marker on each of its carriers in the SENT payload and none in the stored window; the compact request itself
        carries none; the next request keeps the post-compaction input as a byte-identical prefix, markers included."""
        session, transport, worker, request, state = self.run_session(self.compaction_steps(), count_rule=compaction_count_rule, images_per_request=14,
                                                                        compact_threshold_tokens=20_000, compact_output_bound_tokens=4096)
        store = session.store
        self.assertEqual(store.job()["policy"]["cache_mode"], "explicit_rolling", "the default of a new job")
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.epoch("author"), 2)
        (cp,) = sent(transport, "compact")
        self.assertEqual(marked(cp), [], "the compact request carries no cache marker")
        e2 = window(store, "author", 2)
        self.assertEqual(marked({"input": e2}), [], "the stored window carries no marker")
        self.assertEqual(marked({"input": window(store, "author", 1)}), [])
        authors = sent(transport, "responses")
        after = authors[2]
        self.assertEqual(after["input"][0]["role"], "developer")
        self.assertEqual(marked(after), [2, 3], "the kept user message and the host checkpoint (the compaction item and the developer "
                         "message carry none)")
        replay = json.loads(json.dumps(after["input"][1:]))
        for item in replay:
            blocks = item.get("content") if isinstance(item.get("content"), list) else (item.get("output") if isinstance(item.get("output"), list) else [])
            for b in blocks:
                b.pop("prompt_cache_breakpoint", None)
        self.assertEqual(replay, e2[:3], "stripped of its markers the replay is exactly epoch 2")
        self.assertFalse(any(i.get("type") in ("function_call", "function_call_output") for i in after["input"]))
        nxt = authors[3]
        self.assertEqual(responses.canonical(nxt["input"][:len(after["input"])]), responses.canonical(after["input"]),
                         "within epoch 2 the prefix is stable, markers included")
        self.assertEqual(marked(nxt), [i for i in carriers(nxt) if i > 0])
        # before the compaction the same held within epoch 1
        self.assertEqual(responses.canonical(authors[1]["input"][:len(authors[0]["input"])]), responses.canonical(authors[0]["input"]))

    def test_compaction_after_the_first_tool_cycle_starts_epoch_two_and_replays_only_it(self):
        # pinned to the one-breakpoint mode: the replay below is compared item for item with the stored window, which carries
        # no marker (the rolling counterpart strips the sent markers first: test_rolling_compaction_replays_epoch_two_marked)
        session, transport, worker, request, state = self.run_session(self.compaction_steps(), count_rule=compaction_count_rule, images_per_request=14,
                                                                        compact_threshold_tokens=20_000, compact_output_bound_tokens=4096,
                                                                        cache_mode="explicit_one_breakpoint")
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        # the compact request: after the tool cycle completed, no max_output_tokens, developer message first, the whole epoch-1 window
        kinds = [s["endpoint"] for s in transport.sent]
        self.assertEqual(kinds, ["count", "responses", "count", "responses", "count", "compact", "count", "responses", "count", "responses"])
        compact_payloads = sent(transport, "compact")
        self.assertEqual(len(compact_payloads), 1)
        cp = compact_payloads[0]
        self.assertNotIn("max_output_tokens", cp)
        self.assertEqual(set(cp), {"model", "input", "tools"})
        self.assertEqual(cp["input"][0]["role"], "developer")
        self.assertTrue(any(i.get("type") == "function_call_output" and i["call_id"] == "call_2" for i in cp["input"]))
        self.assertEqual(len(cp["input"]), 1 + len(window(store, "author", 1)))
        events = store.events()
        i_tool = max(i for i, e in enumerate(events) if e["kind"] == "tool_completed" and e["data"]["operation"] == store.operations(call_id="call_2")[0]["id"])
        i_compact = next(i for i, e in enumerate(events) if e["kind"] == "compaction_sent")
        self.assertGreater(i_compact, i_tool, "compaction waited for the tool cycle to complete")
        comp_req = [r for r in store.requests() if r["purpose"] == "compaction"]
        self.assertEqual(len(comp_req), 1)
        self.assertEqual(comp_req[0]["state"], "completed")
        self.assertEqual(comp_req[0]["epoch"], 1)
        self.assertEqual(comp_req[0]["input_token_count"], 30000)
        reservation = store.reservation(comp_req[0]["reservation_id"])
        self.assertEqual(reservation["state"], "settled")
        self.assertEqual(reservation["purpose"], "compaction")
        # epoch 2: exactly the returned items minus the developer copy, plus one host checkpoint
        self.assertEqual(store.epoch("author"), 2)
        epochs = [e["data"] for e in store.events("epoch") if e["data"]["role"] == "author"]
        self.assertEqual([e["reason"] for e in epochs], ["initial", "compaction"])
        e2 = window(store, "author", 2)
        self.assertEqual(e2[0], {"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque"})
        self.assertEqual(e2[1], {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "kept"}]})
        self.assertEqual(e2[2]["role"], "user")
        checkpoint = json.loads(message_text(e2[2]))
        self.assertTrue(checkpoint["host_checkpoint_after_compaction"])
        self.assertEqual(checkpoint["current_revision"], "r0001")
        self.assertEqual(checkpoint["request"], comp_req[0]["id"])
        self.assertEqual([p["artifact_id"] for p in checkpoint["photos"]], ["img0001", "img0002", "img0003"])
        self.assertEqual(checkpoint["images_by_revision"]["r0001"][:1], ["img0007"])
        self.assertEqual(len(checkpoint["images_by_revision"]["r0001"]), CANONICAL_RENDERS)   # written at compaction time: the crop does not exist yet
        self.assertEqual(checkpoint["pending_images"], [])
        self.assertFalse(any(i.get("role") == "developer" for i in e2))
        self.assertEqual(store.events("compaction_applied")[0]["data"]["kept_items"], 2)
        self.assertEqual(store.events("compaction_applied")[0]["data"]["returned_items"], 3)
        self.assertEqual(len(window(store, "author", 1)), 1 + 2 * 4, "epoch 1 (first user message + two cycles of reasoning/message/call/output) is kept intact for audit")
        # the next author request replays epoch 2 only
        after = sent(transport, "responses")[2]
        self.assertEqual(after["input"][0]["role"], "developer")
        self.assertEqual(after["input"][1:], e2[:3])
        self.assertFalse(any(i.get("type") in ("function_call", "function_call_output") for i in after["input"]))
        self.assertEqual(store.request("q0004")["epoch"], 2)
        # a pre-compaction render can be re-requested by artifact id and comes back as an image
        render = store.artifact("img0007")
        self.assertEqual((render["kind"], render["role"], render["revision_id"]), ("render", SYNTHETIC, "r0001"))
        crop_out = outputs_for(window(store, "author", 2), "call_3")[0]
        text = output_text(crop_out)
        self.assertEqual(text["crop"]["parent"], "img0007")
        self.assertEqual(text["crop"]["crop_xyxy"], [0, 0, 120, 90])
        self.assertEqual(len(image_blocks(crop_out)), 1)
        crop = store.artifact(text["crop"]["artifact_id"])
        self.assertEqual(crop["parent_id"], "img0007")
        self.assertEqual(crop["kind"], "crop")
        self.assertEqual(crop["recipe"]["width"], 120)
        obs = store.observation(crop["id"])
        self.assert_acknowledged_by_a_completed_request_that_carried_it(store, transport, obs)
        self.assertEqual(obs["acknowledged_request_id"], "q0005")
        self.assertIn(crop["sha256"], payload_image_sha256s(sent(transport, "responses")[3]))
        self.assertEqual(manifest_of(store)["revision"]["id"], "r0001")
        self.assertEqual(len(store.observations(revision_id="r0001", state="acknowledged")), CANONICAL_RENDERS + 1)

    def test_paid_compaction_without_a_verified_output_bound_is_refused_and_falls_back(self):
        steps = self.compaction_steps()[:2] + [deliver_step("never", "r0001")]
        session, transport, worker, _, state = self.run_session(steps, count_rule=compaction_count_rule, images_per_request=14, compact_threshold_tokens=20_000,
                                                                compact_output_bound_tokens=None)
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "context_limit_compaction_unbounded")
        self.assertEqual(sent(transport, "compact"), [])
        self.assertEqual([s["endpoint"] for s in transport.sent], ["count", "responses", "count", "responses"])
        refused = store.events("compaction_refused")
        self.assertEqual(len(refused), 1)
        self.assertIn("compact_output_bound_tokens unset", refused[0]["data"]["reason"])
        self.assertEqual([r["purpose"] for r in store.requests()], ["author", "author"])
        self.assertEqual(store.epoch("author"), 1)
        self.assertEqual(store.events("fallback_delivery")[0]["data"], {"stop_reason": "context_limit_compaction_unbounded", "revision": "r0001"})
        m = manifest_of(store)
        self.assertEqual(m["stop_reason"], "context_limit_compaction_unbounded")
        self.assertEqual(m["state"], "unresolved")
        self.assertEqual(m["revision"]["id"], "r0001")
        self.assertEqual(m["deliverable_status"], "synthetic_only")
        self.assertIsNone(m["asset"])
        self.assertEqual(len(transport.steps), 1)

    def test_unknown_compaction_outcome_needs_attention_and_leaves_the_window(self):
        steps = self.compaction_steps(compact={"endpoint": "compact", "raise": "unknown"})
        session, transport, worker, _, state = self.run_session(steps, count_rule=compaction_count_rule, images_per_request=14, compact_threshold_tokens=20_000,
                                                                compact_output_bound_tokens=4096)
        store = session.store
        self.assertEqual(state, "needs_attention")
        self.assertEqual(store.job()["stop_reason"], "compaction_unknown")
        self.assertEqual(store.epoch("author"), 1)
        self.assertEqual(len(window(store, "author", 1)), 1 + 2 * 4, "the window is exactly what two completed cycles left; nothing was appended")
        self.assertEqual([s["endpoint"] for s in transport.sent], ["count", "responses", "count", "responses", "count", "compact"])
        comp = [r for r in store.requests() if r["purpose"] == "compaction"]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0]["state"], "unknown")
        self.assertEqual(store.reservation(comp[0]["reservation_id"])["state"], "unknown")
        totals = runner.status_report(store)
        self.assertEqual(totals["unknown_requests"], [comp[0]["id"]])
        self.assertGreater(totals["budget"]["unknown_liability_micro"], 0)
        self.assertEqual(len(transport.steps), 2, "nothing after the compaction was asked for")
        self.assertFalse((store.job_dir / "deliverable").exists())
        self.assertEqual(cli.EXIT_BY_STATE[state], 3)

    def test_compaction_is_not_attempted_while_an_operation_is_pending(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A)]
        session, transport, worker, _ = self.make(steps, count_rule=lambda payload: 30000, images_per_request=14, compact_threshold_tokens=20_000,
                                                  compact_output_bound_tokens=4096)
        store = session.store
        session.author_step()
        self.assertEqual(store.state(), "tool_pending")
        self.assertEqual(len(store.operations(state="pending")), 1)
        self.assertEqual(store.request("q0001")["input_token_count"], 30000)
        self.assertIsNone(session.maybe_compact())
        self.assertEqual(sent(transport, "compact"), [])
        self.assertEqual([r["purpose"] for r in store.requests()], ["author"])
        self.assertEqual(store.events("compaction_sent"), [])
        self.assertEqual(store.epoch("author"), 1)
        session.execute_pending_operations()
        self.assertEqual(store.state(), "ready")
        self.assertEqual(sent(transport, "compact"), [])
        session.release()

    def test_images_included_but_unsent_at_compaction_time_are_not_acknowledged_blindly(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), compact_step(), step("call_2", "read_program", {"revision_id": None}), deliver_step("call_3", "r0001")]
        session, transport, worker, _, state = self.run_session(steps, count_rule=lambda payload: 5000 if any(i.get("type") == "compaction" for i in payload["input"]) else 30000,
                                                                images_per_request=14, compact_threshold_tokens=20_000, compact_output_bound_tokens=4096)
        store = session.store
        self.assertEqual(store.epoch("author"), 2)
        for o in store.observations(revision_id="r0001"):
            if o["state"] == "acknowledged":
                self.assert_acknowledged_by_a_completed_request_that_carried_it(store, transport, o)


# =========================================================================== conversation cost and orchestration (test-pilot-002 review)


def usage(inp: int, cached: int = 0, written: int = 0, out: int = 300, reasoning: int = 200) -> dict:
    return {"input_tokens": inp, "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written}, "output_tokens": out,
            "output_tokens_details": {"reasoning_tokens": reasoning}, "total_tokens": inp + out}


def with_usage(s: dict, u: dict) -> dict:
    s = json.loads(json.dumps(s))
    s["body"]["usage"] = u
    return s


def carriers(payload: dict) -> list[int]:
    return [i for i, it in enumerate(payload["input"])
            if (it.get("type") == "function_call_output" and isinstance(it.get("output"), list)) or (it.get("type") == "message" and it.get("role") == "user")]


def marked(payload: dict) -> list[int]:
    out = []
    for i, it in enumerate(payload["input"]):
        blocks = it.get("content") if isinstance(it.get("content"), list) else (it.get("output") if isinstance(it.get("output"), list) else [])
        if any(isinstance(b, dict) and "prompt_cache_breakpoint" in b for b in blocks):
            out.append(i)
    return out


class TimeoutRecordingTransport(responses.ScriptedTransport):
    def __init__(self, steps, **kw):
        super().__init__(steps, **kw)
        self.timeouts: list[tuple[str, tuple]] = []

    def post(self, endpoint, payload, *, timeout=(15, 300)):
        self.timeouts.append((endpoint, tuple(timeout)))
        return super().post(endpoint, payload, timeout=timeout)


class ConversationCost(SessionCase):
    """F1/AT-11/INF-05 (rolling cache), F2/F9 (image prune epochs instead of the compaction stop), AT-08/F7/CE-5/INF-03 (critic
    and final output bounds, truncation), INF-02 (read timeout), CE-8 (checklist), AT-13 (first message)."""

    def test_rolling_cache_keeps_each_author_request_a_byte_identical_prefix_of_the_next(self):
        request = demo.demo_request(self.root / "job.in")
        steps = demo.demo_script(demo.sealed_pixel_hashes(request["photos"]))
        session, transport, _w, _r, state = self.run_session(steps, scenario=demo.demo_fake_scenario(), cache_mode="explicit_rolling")
        self.assertEqual(state, "unresolved")
        authors = [p for p in sent(transport, "responses") if p["tool_choice"] == "required"]
        self.assertEqual(len(authors), 7)
        for prev, nxt in zip(authors, authors[1:]):
            self.assertEqual(responses.canonical(nxt["input"][:len(prev["input"])]), responses.canonical(prev["input"]),
                             "the previous request's input, markers included, is a byte-identical prefix")
        for p in authors:
            self.assertEqual(marked(p), carriers(p), "every user message and tool output carries its marker")
            self.assertEqual(p["prompt_cache_options"], {"mode": "explicit"})
        self.assertEqual(session.store.events("cache_miss"), [], "the demo's usage reads 800 of 1200: no miss")

    def test_a_cache_miss_under_the_rolling_mode_is_recorded(self):
        steps = [with_usage(edit_step("call_1", None, demo.PROGRAM_A), usage(20_000, 0, 19_000)),
                 with_usage(fetch_step("call_2", 6), usage(30_000, 2_000, 28_000)),
                 with_usage(deliver_step("call_3", "r0001"), usage(31_000, 29_900, 1_100))]
        session, *_rest, state = self.run_session(steps, cache_mode="explicit_rolling")
        misses = session.store.events("cache_miss")
        self.assertEqual(len(misses), 1, [m["data"] for m in misses])
        self.assertEqual({k: misses[0]["data"][k] for k in ("request", "previous_input_tokens", "cached_tokens")},
                         {"request": "q0002", "previous_input_tokens": 20_000, "cached_tokens": 2_000})

    def test_critic_bound_is_raised_and_a_truncated_critic_reports_what_arrived(self):
        partial = '{"defects": [{"part": "frame", "where_seen": "tex_front", "description": "the bridge is too th'
        truncated = {"endpoint": "responses", "expect": {"tools": ["report_critique"]},
                     "body": {"id": "resp_c", "object": "response", "status": "incomplete", "model": "gpt-6-astra",
                              "incomplete_details": {"reason": "max_output_tokens"}, "usage": usage(9_000, 0, 0, runner.CRITIC_MAX_OUTPUT_TOKENS, 12_000),
                              "output": [{"type": "reasoning", "id": "rs_c", "summary": [], "encrypted_content": "opaque"},
                                         {"type": "function_call", "id": "fc_c", "call_id": "call_c", "name": "report_critique", "status": "incomplete",
                                          "arguments": partial}]}}
        steps = demo.demo_script()[:5] + [truncated, deliver_step("call_5", "r0002", expect={"contains_call_output": "call_4"})]
        session, transport, _w, _r, state = self.run_session(steps, scenario=demo.demo_fake_scenario(), max_output_tokens=24_000)
        store = session.store
        critic = [p for p in sent(transport, "responses") if p["tool_choice"] != "required"][0]
        self.assertEqual(critic["max_output_tokens"], runner.CRITIC_MAX_OUTPUT_TOKENS)
        self.assertEqual(runner.CRITIC_MAX_OUTPUT_TOKENS, 16_000)
        self.assertEqual(runner.FINAL_MAX_OUTPUT_TOKENS, 12_000)
        text = output_text(outputs_for(window(store), "call_4")[0])
        self.assertIn("truncated", text["error"])
        self.assertIn("the bridge is too th", text["error"], "what arrived is handed to the author, not dropped")
        self.assertIn(str(runner.CRITIC_MAX_OUTPUT_TOKENS), text["error"])
        ev = store.events("critic_truncated")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["data"]["received_chars"], len(partial))
        near = [e["data"] for e in store.events("output_near_bound")]
        self.assertEqual([(e["role"], e["output_tokens"], e["max_output_tokens"]) for e in near], [("critic", 16_000, 16_000)])
        self.assertEqual(store.verdicts(kind="critic"), [])

    def test_final_evaluator_bound(self):
        session, *_ = self.make([], max_output_tokens=24_000)
        seen = {}

        def fake_single_call(**kw):
            seen.update(kw)
            return None, {"request": None, "error": "scripted"}
        rev = {"id": "r0009", "synthetic": 0, "glb_sha256": "ab" * 32}
        with mock.patch.object(runner, "final_blocks", return_value=([responses.text_block("x")], {"evidence_complete": True})), \
                mock.patch.object(session, "single_call", side_effect=fake_single_call):
            session.policy = dict(session.policy, final_evaluator="scripted")
            session.run_final(rev)
        self.assertEqual(seen["max_output_tokens"], runner.FINAL_MAX_OUTPUT_TOKENS)
        session.release()

    def test_read_timeout_follows_the_output_bound(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), deliver_step("call_2", "r0001")]
        session, transport, *_ = self.make(steps, transport_cls=TimeoutRecordingTransport, max_output_tokens=24_000, images_per_request=14)
        session.run()
        posts = [(e, t) for e, t in transport.timeouts if e == responses.ENDPOINT]
        self.assertEqual(len(posts), 2)
        first = session.store.request("q0001")
        self.assertEqual(posts[0][1], (responses.CONNECT_TIMEOUT_S, responses.read_timeout_s(24_000, first["input_token_count"])))
        self.assertGreater(posts[0][1][1], 900, "a 24,000-token author reply gets more than the old 300 s")
        sent_ev = session.store.events("inference_sent")[0]["data"]
        self.assertEqual(sent_ev["read_timeout_s"], posts[0][1][1])
        self.assertIn("images", sent_ev, "the per-request image count is recorded (F9)")

    def test_image_prune_epoch_stubs_superseded_revisions_and_keeps_the_latest(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), fetch_step("call_2", 6),
                 edit_step("call_3", "r0001", demo.PROGRAM_B), fetch_step("call_4", 6),
                 step("call_5", "read_program", {"revision_id": None}), deliver_step("call_6", "r0002")]
        session, transport, _w, _r, state = self.run_session(steps, cache_mode="explicit_rolling", policy_extra={"image_prune_tokens": 1})
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        self.assertEqual(store.epoch("author"), 2)
        self.assertEqual([e["data"]["reason"] for e in store.events("epoch") if e["data"]["role"] == "author"], ["initial", "image_prune"])
        pruned = store.events("images_pruned")
        self.assertEqual(len(pruned), 1, "pruned once: the next author step finds nothing left to prune")
        r1 = sorted(o["artifact_id"] for o in store.observations(revision_id="r0001"))
        self.assertEqual(sorted(pruned[0]["data"]["images"]), r1)
        authors = sent(transport, "responses")
        after = authors[4]                       # q0005: the first request of the prune epoch
        stubs = [json.loads(b["text"]) for it in after["input"] for b in (it.get("output") if isinstance(it.get("output"), list) else [])
                 if b.get("type") == "input_text" and "image_not_resent" in b.get("text", "")]
        self.assertEqual(sorted(s["image_not_resent"] for s in stubs), r1)
        r1_sha = {store.artifact(a)["sha256"] for a in r1}
        r2_sha = {store.artifact(o["artifact_id"])["sha256"] for o in store.observations(revision_id="r0002")}
        self.assertFalse(payload_image_sha256s(after) & r1_sha, "the superseded revision's pixels are not re-sent")
        self.assertTrue(r2_sha <= payload_image_sha256s(after), "the latest revision's images stay")
        self.assertEqual(len(image_blocks(after)), 3 + len(r2_sha), "three photos and the latest revision's images")
        # the prune epoch is itself append-only: the next request extends it byte for byte
        self.assertEqual(responses.canonical(authors[5]["input"][:len(after["input"])]), responses.canonical(after["input"]))
        # nothing about the acknowledgement changed
        self.assertTrue(all(o["state"] == "acknowledged" for o in store.observations(revision_id="r0001")))
        self.assert_calls_have_one_output_each_in_order(window(store))
        self.assertEqual(len(window(store, "author", 2)), len(window(store, "author", 1)) + 8, "the copy plus two more cycles")

    def test_no_prune_without_the_policy_key_or_below_its_threshold(self):
        steps = [edit_step("call_1", None, demo.PROGRAM_A), fetch_step("call_2", 6), edit_step("call_3", "r0001", demo.PROGRAM_B), fetch_step("call_4", 6),
                 deliver_step("call_5", "r0002")]
        for extra in (None, {"image_prune_tokens": 10_000_000}):
            with self.subTest(extra=extra):
                session, *_rest, state = self.run_session(steps, name=f"job{bool(extra)}", policy_extra=extra)
                self.assertEqual(session.store.epoch("author"), 1)
                self.assertEqual(session.store.events("images_pruned"), [])

    def test_first_message_names_real_tools_and_drops_rules_without_data(self):
        session, *_ = self.make([])
        first = window(session.store)[0]
        text = message_text(first)
        self.assertNotIn("request_views", text, "AT-13: the agentic tool is render_views")
        self.assertIn("(render_views)", text)
        self.assertNotIn("Evidence provenance: when the package carries product_reading", text, "no product_reading / provenance in the package")
        protocol = session.store.setting("protocol")
        self.assertEqual(protocol["checklist_source"], "request_notes")
        self.assertEqual(protocol["identity_checklist"], evaluation.checklist_from_notes(demo.demo_request(self.root / "x.in")["notes"]))
        session.release()

    def test_owner_revision_seed_is_built_before_the_first_author_turn_and_stated_first(self):
        seed = self.root / "seed"
        seed.mkdir()
        (seed / "frame.py").write_text(demo.PROGRAM_A, encoding="utf-8")
        steps = [deliver_step("call_1", "r0001", expect={"has_image": True, "has_text": ["seed_build", "make the lenses warmer"]})]
        session, transport, worker, _r, state = self.run_session(steps, seed_program=seed, images_per_request=14,
                                                                  policy_extra={"owner_instruction": "make the lenses warmer", "editable_modules": ["materials", "lenses"]})
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        self.assertEqual(worker.count, 1, "the seed was built once, by the host, before any inference")
        first_request = sent(transport, "responses")[0]
        users = user_messages(first_request["input"])
        head = json.loads(users[0]["content"][0]["text"].split("\n", 1)[1])
        self.assertEqual(head["owner_revision"]["owner_instruction"], "make the lenses warmer")
        self.assertEqual(head["owner_revision"]["editable_modules"], ["materials", "lenses"])
        self.assertEqual(head["owner_revision"]["seed_revision"], "r0001")
        seeded = [u for u in users if "seed_build" in message_text(u)]
        self.assertEqual(len(seeded), 1)
        self.assertEqual(len(image_blocks({"output": seeded[0]["content"]})), CANONICAL_RENDERS)
        obs = store.observations(revision_id="r0001")
        self.assertEqual(len(obs), CANONICAL_RENDERS)
        for o in obs:
            self.assert_acknowledged_by_a_completed_request_that_carried_it(store, transport, o)
        self.assertEqual(store.revision("r0001")["state"], "compatible")
        self.assertEqual(manifest_of(store)["revision"]["id"], "r0001")
        # the host build is an operation of its own that owes no function_call_output
        ops = store.operations()
        self.assertEqual(ops[0]["tool_name"], "build_candidate")
        self.assertTrue(ops[0]["call_id"].startswith(runner.HOST_CALL_PREFIX))
        self.assert_calls_have_one_output_each_in_order(window(store))


if __name__ == "__main__":
    unittest.main()
