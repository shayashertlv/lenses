"""bsa.look regressions from the 2026-09-27 working-tree audit: F17 (the see-through guard evaluated only the density
knots; opposing channel slopes hide the darkest point between them), F09 (a revision shown only by a failed request
counted as reviewed and could be delivered), F15 (the editor saw the gate decision, which carries the held-out
criterion's outcome). Hermetic: LookFixture's temp BSA_DATA and fake AR harness; no network, no browser."""
import json
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

from bsa import look as L
from reconstruction.job import _write
from reconstruction.lens_appearance import DensityKeyframe, LensAppearance
from reconstruction.segmented_providers import pin
from test_bsa_look import GRADIENT, HELD_OUT_REASON, INVU, MIRROR, OAKLEY, UNIFORM, LookFixture, frame_op, plan

D005 = float(-np.log(0.005))            # BSA's declared minimum per-channel normal transmission (LENS_T[0]) as a density
FLOOR = L.LENS_MIN_LUMINOUS_T


def descriptor(keys, r0=(0.04, 0.04, 0.04)) -> dict:
    return LensAppearance(tuple(DensityKeyframe(v, d) for v, d in keys), r0).to_dict()


def opposing(k: float) -> dict:
    """Red rises where green falls (blue dark throughout): every knot reads clear, the interior does not."""
    return descriptor([(0.0, (0.0, k, k)), (1.0, (k, 0.0, k))])


def brute_force(desc: dict, n: int = 100001) -> tuple[float, float, float]:
    """The guard's own angle sweep at n v-samples: (minimum Y, its v, its angle); the reference a certified lower
    bound must never exceed."""
    la = LensAppearance.from_dict(desc)
    angles = np.asarray(sorted({float(x) for x in np.arange(0.0, L.SEE_THROUGH_MAX_DEG + 1e-9, 2.5).round(3)}
                               | {float(k.angle_degrees) for k in (la.angular_reflectance_keyframes or [])
                                  if k.angle_degrees <= L.SEE_THROUGH_MAX_DEG}), float)
    v = np.linspace(0.0, 1.0, n)
    Y = np.asarray(la.evaluate(v[:, None], angles[None, :]).transmission_rgb, float) @ L.LUMA
    i, j = np.unravel_index(int(np.argmin(Y)), Y.shape)
    return float(Y[i, j]), float(v[i]), float(angles[j])


class SeeThroughBoundTest(unittest.TestCase):
    """F17: ``see_through`` certifies a lower bound of the luminous transmission between the density knots."""

    def test_the_audit_lens_is_refused_at_its_interior_minimum(self):
        # the audit's reproduction: both knots read Y 0.20 (the old guard: min 0.19883, ok) while v 0.4375 at 60 deg is 0.028
        st = L.see_through(opposing(D005))
        self.assertFalse(st["ok"])
        self.assertLessEqual(st["min_luminous_transmission"], 0.0284)
        self.assertAlmostEqual(st["at_v"], 0.4375, delta=0.01)
        self.assertEqual(st["at_angle_deg"], 60.0)
        knots = np.asarray(LensAppearance.from_dict(opposing(D005)).evaluate([0.0, 1.0], 0.0).transmission_rgb) @ L.LUMA
        self.assertTrue(np.all(knots > 0.2))                          # the knots alone read clear (the old guard's view)
        self.assertLess(st["min_head_on"], 0.06)                      # the head-on minimum now covers the whole lens
        true_min, at_v, at_angle = brute_force(opposing(D005))
        self.assertLess(true_min, FLOOR)
        self.assertAlmostEqual(at_v, 0.4375, delta=0.001)
        self.assertLessEqual(st["certified_lower_bound"], true_min + 1e-12)
        self.assertLess(st["certified_lower_bound"], FLOOR)
        self.assertAlmostEqual(st["bound_gap"], st["min_luminous_transmission"] - st["certified_lower_bound"], delta=1.5e-5)
        # the compiler refuses it, naming the bound (restating the lens compiles it back to itself)
        p = L.lens_parameters(opposing(D005))
        with self.assertRaisesRegex(ValueError, r"nearly opaque.*certified lower bound 0\.02\d+ < floor 0\.03"):
            L.compile_lens(dict(operation="lens_look", lens="all", **p), opposing(D005))

    def test_a_monotone_gradient_is_exact_at_its_knot_and_the_bound_is_tight(self):
        mono = descriptor([(0.0, (0.3, 0.35, 0.45)), (1.0, (1.4, 1.6, 2.0))])
        st = L.see_through(mono)
        true_min, at_v, at_angle = brute_force(mono)
        self.assertEqual((at_v, at_angle), (1.0, 60.0))                # all channels darken upwards: the top knot
        self.assertAlmostEqual(st["min_luminous_transmission"], true_min, places=5)
        self.assertEqual((st["at_v"], st["at_angle_deg"]), (1.0, 60.0))
        self.assertLessEqual(st["certified_lower_bound"], true_min + 1e-12)
        self.assertLess(true_min - st["certified_lower_bound"], 0.002)  # the bound's slack: L * step / 2
        self.assertTrue(st["ok"])
        self.assertGreater(st["certified_lower_bound"], 0.1)

    def test_a_uniform_lens_has_no_slack(self):
        st = L.see_through(UNIFORM)
        self.assertEqual(st["certified_lower_bound"], st["min_luminous_transmission"])
        self.assertEqual(st["bound_gap"], 0.0)
        self.assertTrue(st["ok"])

    def test_the_bound_holds_on_the_measured_m2_lenses(self):
        # 16-knot non-monotone profiles and measured angle tables: the bound stays below brute force and every lens passes
        for name, d in (("INVU", INVU), ("OAKLEY", OAKLEY), ("GRADIENT", GRADIENT), ("MIRROR", MIRROR)):
            with self.subTest(lens=name):
                st = L.see_through(d)
                true_min = brute_force(d, 20001)[0]
                self.assertLessEqual(st["certified_lower_bound"], true_min + 1e-12)
                self.assertLessEqual(st["certified_lower_bound"], st["min_luminous_transmission"])
                self.assertLess(true_min - st["certified_lower_bound"], 0.003)
                self.assertTrue(st["ok"])

    def test_a_minimum_just_above_the_floor_passes_only_when_the_bound_clears_it(self):
        """The certified bound sits ~0.0012 below the true minimum of this two-knot shape at the floor: a lens whose true
        minimum is 0.0003 above the floor is refused (conservative, by design), one 0.002 above it passes."""
        cases = ((5.191712, 0.0003, False), (5.102920, 0.002, True), (4.740173, 0.01, True))
        for k, eps, expected_ok in cases:
            with self.subTest(k=k):
                lens = opposing(k)
                st = L.see_through(lens)
                true_min = brute_force(lens)[0]
                self.assertGreater(true_min, FLOOR)                              # truly see-through ...
                self.assertAlmostEqual(true_min, FLOOR + eps, delta=1.5e-5)
                self.assertLessEqual(st["certified_lower_bound"], true_min + 1e-12)
                self.assertLess(true_min - st["certified_lower_bound"], 0.0015)  # ... within the documented slack
                self.assertEqual(st["ok"], st["certified_lower_bound"] >= FLOOR)
                self.assertEqual(st["ok"], expected_ok)

    def test_the_floor_follows_the_certified_bound_of_a_dark_s9_lens(self):
        # S9's lens: sampled minimum 0.0303 (above the floor), certified 0.0291 (below); an edit that keeps its densities
        # (a roughness change) must not be refused for what S9 already was
        lens = opposing(5.191712)
        st = L.see_through(lens)
        self.assertGreater(st["min_luminous_transmission"], FLOOR)
        self.assertLess(st["certified_lower_bound"], FLOOR)
        p = L.lens_parameters(lens)
        req = dict(operation="lens_look", lens="all", **{**p, "roughness": 0.2})
        with self.assertRaisesRegex(ValueError, "nearly opaque"):
            L.compile_lens(req, lens)
        floor = L.see_through_floor({"lens_R": lens, "lens_L": lens})
        self.assertEqual(round(floor, 5), st["certified_lower_bound"])       # the exact bound, not its rounded report
        out, _ = L.compile_lens(req, lens, floor=floor)
        self.assertEqual(out["roughness"], 0.2)
        self.assertEqual(L.see_through_floor({"lens_R": GRADIENT}), FLOOR)


class ShownRevisionsTest(unittest.TestCase):
    """F09: only a turn whose request COMPLETED shows the editor its snapshot's revision."""

    def test_shown_revisions_counts_completed_turns_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        turns = []
        for n, (status, rid) in enumerate((("applied", "r0000"), ("applied", "r0001"), ("needs_attention", "r0002"),
                                            ("request_pending", "r0002"), ("planned", "r0002"), ("awaiting_plan", "r0002"))):
            path = Path(tmp.name) / f"turn-{n:04d}.json"
            _write(path, {"context": {"current_revision": rid}, "images": [], "tools_sha256": "x"})
            turns.append({"id": f"turn-{n:04d}", "status": status, "input": pin(path)})
        turns.append({"id": "turn-0006", "status": "applied"})               # no input at all: never shown
        fake = types.SimpleNamespace(state={"turns": turns})
        self.assertEqual(L.BsaLookSession.shown_revisions(fake), ["r0000", "r0001"])


class ObservedRevisionBarrierTest(LookFixture):
    """F09 end to end: a failed request's snapshot is no review; a rejected plan's is."""

    def test_a_revision_shown_only_by_a_failed_request_is_not_delivered(self):
        # one plan, two turns: turn 0 edits (r0001); turn 1's snapshot shows r0001 and then the request fails (the script
        # has no plan left). Until 2026-09-27 that snapshot counted as a review and r0001 was delivered.
        res, _ = self.main("--driver", "scripted", "--max-turns", "2", "--script", str(self.script(
            plan(frame_op(roughness=0.3), note="glossier"))))
        self.assertEqual([t["status"] for t in res["turns"]], ["applied", "needs_attention"])
        self.assertEqual(res["turns"][1]["error_type"], "RuntimeError")
        self.assertEqual(self.turn_input(1)["context"]["current_revision"], "r0001")
        self.assertEqual([r["id"] for r in res["revisions"]], ["r0000", "r0001"])   # the edit is kept as evidence
        self.assertEqual((res["status"], res["final_revision"], res["unreviewed_revision"]),
                         ("needs_attention", "r0000", "r0001"))
        self.assertIn("look_final_edit_unreviewed", res["flags"])
        self.assertEqual(res["finish"]["delivered_revision"], "r0000")
        self.assertEqual((self.sd / "model.glb").read_bytes(), self.s9)
        # the session is RESUMABLE (its last request failed): the state keeps the checkpoint the retried turn will
        # need; only the report delivers the reviewed revision (moving the state stranded every resume, 2026-09-27)
        state = json.loads((self.sd / "session" / "state.json").read_text())
        self.assertEqual((state["current_revision"], state.get("unreviewed_revision")), ("r0001", None))
        self.assertEqual(res["checkpoint_revision"], "r0001")

    def test_a_session_stopped_by_a_failed_request_resumes_at_its_checkpoint(self):
        # the same failed second turn, then a second run of the SAME session (a changed script is a changed driver
        # binding and is refused for its own reason): the retried turn must reach its request again instead of being
        # refused with 'Persisted plan refers to a different current checkpoint' (the F09 regression of 2026-09-27)
        script = str(self.script(plan(frame_op(roughness=0.3), note="glossier")))
        self.main("--driver", "scripted", "--max-turns", "2", "--script", script)
        res, _ = self.main("--driver", "scripted", "--max-turns", "2", "--script", script)
        self.assertEqual([t["status"] for t in res["turns"]], ["applied", "needs_attention"])
        self.assertEqual(res["turns"][1]["error_type"], "RuntimeError")          # the exhausted script, retried
        self.assertNotIn("different current checkpoint", json.dumps(res))
        self.assertEqual((res["final_revision"], res["unreviewed_revision"], res["checkpoint_revision"]), ("r0000", "r0001", "r0001"))
        state = json.loads((self.sd / "session" / "state.json").read_text())
        self.assertEqual(state["current_revision"], "r0001")

    def test_a_manual_session_waiting_for_its_plan_keeps_its_current_revision(self):
        # the pause is not a failed request: turn 1's snapshot (r0001) will be reviewed when the plan arrives
        plans = self.root / "plans"
        self.main("--driver", "manual", "--plans-dir", str(plans))
        (plans / "turn-0000.json").write_text(json.dumps(plan(frame_op(roughness=0.3), note="glossier")))
        res, _ = self.main("--driver", "manual", "--plans-dir", str(plans))
        self.assertEqual((res["status"], res["final_revision"], res["unreviewed_revision"]), ("awaiting_plan", "r0001", None))
        self.assertEqual([t["status"] for t in res["turns"]], ["applied", "awaiting_plan"])
        state = json.loads((self.sd / "session" / "state.json").read_text())
        self.assertEqual((state["current_revision"], state.get("unreviewed_revision")), ("r0001", None))
        self.assertNotIn("look_final_edit_unreviewed", res["flags"])

    def test_a_rejected_plan_still_counts_as_a_review_of_its_snapshot(self):
        # turn 1 shows r0001 and answers with a no-op (rejected): the editor reviewed r0001, so it is delivered
        res, _ = self.main("--driver", "scripted", "--max-turns", "2", "--script", str(self.script(
            plan(frame_op(roughness=0.3), note="glossier"), plan(frame_op(ratio=[1.0, 1.0, 1.0]), note="restated"))))
        self.assertEqual([t["event_status"] for t in res["turns"]], ["applied", "rejected"])
        self.assertEqual((res["final_revision"], res["unreviewed_revision"], res["verdict"]),
                         ("r0001", None, "unfinished_turn_limit"))
        self.assertNotIn("look_final_edit_unreviewed", res["flags"])


class HeldOutLeakageTest(unittest.TestCase):
    """F15: the editor's S10 view is a function of the fit-view evidence alone."""

    def test_identical_fit_evidence_reads_identically_whatever_the_held_out_criterion_said(self):
        fit = ["info:front_low_resolution", "n/a:c2_lens_edge_vs_gt"]
        flags = ["front_low_resolution"]
        variants = ({"decision": "READY", "reasons": fit, "flags": flags},                              # c3 passed
                    {"decision": "RETRY", "reasons": [*fit, HELD_OUT_REASON], "flags": flags},           # c3 failed
                    {"decision": "RETRY", "reasons": ["unevaluated:c3_heldout_angled_front_piece", *fit], "flags": flags},
                    {"decision": "READY", "reasons": [*fit, "n/a:c3_heldout_angled_front_piece"],
                     "flags": [*flags, "angled_contour_note"]})
        outs = {json.dumps(L.editor_s10(s), sort_keys=True) for s in variants}
        self.assertEqual(len(outs), 1)
        got = json.loads(outs.pop())
        self.assertEqual((got["decision"], got["reasons"], got["flags"]), ("READY", fit, flags))
        self.assertNotIn("heldout", json.dumps(got).replace("held-out view", ""))
        # with a fit-view failure beside it, every variant reads RETRY; a review rule dominates everything
        retry = {json.dumps(L.editor_s10({**s, "decision": "RETRY", "reasons": ["failed:c2_lens_edge_vs_gt", *s["reasons"]]}),
                            sort_keys=True) for s in variants}
        self.assertEqual(len(retry), 1)
        self.assertEqual(json.loads(retry.pop())["decision"], "RETRY")
        review = {json.dumps(L.editor_s10({**s, "decision": "REVIEW", "reasons": ["review:lens_colour_mismatch", *s["reasons"]]}),
                             sort_keys=True) for s in variants}
        self.assertEqual(len(review), 1)
        self.assertEqual(json.loads(review.pop())["decision"], "REVIEW")

    def test_a_record_the_rule_does_not_reproduce_withholds_the_decision(self):
        for s10 in ({"decision": "READY", "reasons": [HELD_OUT_REASON], "flags": []},   # READY beside a failed criterion
                    {"decision": "RETRY", "flags": []},                                 # no reasons at all
                    {"decision": "REVIEW", "reasons": ["failed:c2_lens_edge_vs_gt"], "flags": []}):
            with self.subTest(s10=s10):
                got = L.editor_s10(s10)
                self.assertIsNone(got["decision"])
                self.assertIn("withheld", got["note"])
                self.assertNotIn("heldout", json.dumps(got).replace("held-out view", ""))

    def test_reasons_decision_replays_the_gate_rule(self):
        from bsa import gate
        rng = np.random.default_rng(20260927)
        names = ["c1_contract_ar_lenses", "c2_lens_edge_vs_gt", "c3_heldout_angled_front_piece", "c4_zero_seam_gaps"]
        flag_pool = ["lens_colour_mismatch", "front_low_resolution", "lens_components_3", "rimless_review", "angled_x"]
        for _ in range(300):
            # the gate's finalize computes criteria for every model it has: crit is None exactly when has_bsa is False
            has_bsa = bool(rng.random() < 0.85)
            crit = None
            if has_bsa:
                crit = {}
                for n in names:
                    p = rng.choice([True, False, None])
                    crit[n] = {"pass": None if p is None else bool(p)}
                    if p is None and rng.random() < 0.5:
                        crit[n]["applicable"] = False
            flags = [f for f in flag_pool if rng.random() < 0.3]
            d = gate.decide(crit, flags, has_bsa)
            self.assertEqual(L.reasons_decision(d["reasons"]), d["decision"], d)
            # the fit-only replay is the rule over the criteria without the held-out one
            fit = None if crit is None else {k: v for k, v in crit.items() if not L._held_out_item(k)}
            expect = gate.decide(fit, [f for f in flags if not L._held_out_item(f)], has_bsa)
            shown = [r for r in d["reasons"] if not L._held_out_item(r)]
            self.assertEqual(L.reasons_decision(shown), expect["decision"], d)


if __name__ == "__main__":
    unittest.main()
