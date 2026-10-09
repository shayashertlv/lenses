"""A passing gate stage cannot conceal a failed or ungraded P2 tracing proof."""
from types import SimpleNamespace

import pytest

from glasses3d.tests.proofs import wp6_prada_stages as S


class _Proof:
    def __init__(self, out):
        self.out = out
        self.rec = {"stages": {}}
        self.hb = SimpleNamespace(log=lambda *args: None)

    def stage(self, name, fn):
        result = fn()
        self.rec["stages"][name] = result
        return result

    def save(self):
        pass

    def stop(self, code, why, evidence=None):
        self.rec["stop"] = {"code": code, "why": why, "evidence": evidence}
        return code


@pytest.fixture
def run_proof(monkeypatch, tmp_path):
    def fit(*args):
        return {"_results": {"candidate": {"spec": {}}}, "stop_s": [], "conflicts": {},
                "s3": {"stop_c": False, "front_bound_stop": False}}

    monkeypatch.setattr(S, "stage_fit", fit)
    monkeypatch.setattr(S, "label_sheet", lambda *args: None)

    def run(p2, *, gates_pass=True, stages=None):
        monkeypatch.setattr(S, "stage_gates", lambda *args: {
            "pass": gates_pass, "failed": [] if gates_pass else ["R3"],
            "gates": {"R3": {"pass": gates_pass}}})
        proof = _Proof(tmp_path)
        result = S.run_rest(proof, None, {}, {}, {"guides": {}},
                            stages if stages is not None else {"fit", "gates"},
                            SimpleNamespace(quick=False), p2_done=p2)
        return proof, result

    return run


@pytest.mark.parametrize("p2", [{"pass": False}, {"pass": None}, {}])
def test_complete_proof_refuses_failed_or_ungraded_p2(run_proof, p2):
    proof, result = run_proof(p2)
    assert result == "STOP-P"
    assert "P2" in proof.rec["stop"]["why"]
    assert proof.rec["stop"]["evidence"] == p2


def test_passed_p2_and_passed_gates_can_complete(run_proof):
    _, result = run_proof({"pass": True})
    assert result.startswith("PASS")


def test_failed_product_gate_keeps_its_evidence(run_proof):
    proof, result = run_proof({"pass": False}, gates_pass=False)
    assert result == "STOP-P"
    assert "R3" in proof.rec["stop"]["why"]
    assert proof.rec["stop"]["evidence"] == {"R3": {"pass": False}}


def test_failed_p2_does_not_prevent_a_partial_diagnostic_fit(run_proof):
    _, result = run_proof({"pass": False}, stages={"fit"})
    assert result == "stopped after FIT/P3 (stages)"


@pytest.mark.parametrize("floor, delivered", [(None, None), (.764, .557)])
def test_reporting_keeps_resolved_gates_and_unmodified_failed_evidence(floor, delivered):
    import copy
    from glasses3d.g3d_host import relief_rules as R

    stages = {"S1": {"instrument_floor": {"models": {"truth": {"R3_transfer_mm": floor}}}},
              "GATES": {"gates": {"R3": {"pass": False, "transfer_mm": {"delivered": delivered}}}}}
    rec = {"stages": copy.deepcopy(stages), "result": "STOP-I",
           "open_owner_decisions": [{"gate": "R1", "decision": "obsolete question"}]}
    S.record_gate_decisions(rec)
    assert rec["stages"] == stages and rec["result"] == "STOP-I"
    assert rec["open_owner_decisions"] == []
    decisions = {d["gate"]: d for d in rec["resolved_gate_decisions"]["decisions"]}
    assert set(decisions) == {"STOP-I", "R1", "R3"}
    assert all(d["decision"].startswith("KEEP") for d in decisions.values())
    assert decisions["STOP-I"]["evidence"]["s1_true_temple_transfer_mm_this_record"] == floor
    evidence = decisions["R3"]["evidence"]
    assert evidence["delivered_transfer_mm_this_record"] == delivered
    assert evidence["delivered_minus_margin_mm_this_record"] == (
        None if delivered is None else delivered - R.R3_TRANSFER_MARGIN_MM)
