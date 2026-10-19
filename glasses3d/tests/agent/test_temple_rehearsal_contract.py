"""Cheap checks of the manual T5 proof against the current tool and footer contracts."""
from pathlib import Path

import httpx2
import pytest

from glasses3d.agent import budget as B, replay as R, renewal as N
from glasses3d.agent.loop import footer_text
from glasses3d.agent.temple_lab.tools import TOOL_NAMES
from glasses3d.tests.proofs import astra_temple_lab as proof


@pytest.mark.parametrize("settled,cap,context,held", [
    ("0", "100", 0, "0"), ("12.34", "100", 241_000, "3.12"), (None, None, None, None),
])
def test_rehearsal_accepts_the_actual_runtime_footer(settled, cap, context, held):
    text = footer_text(settled, cap, context, settled_stop_usd=cap)
    assert proof.FOOTER.search("tool result\n" + text)
    assert not proof.FOOTER.search("tool result\n" + text + " extra bytes")
    assert not proof.FOOTER.search("tool result\n" + text.replace("context", "missing-context-field"))


def test_every_current_tool_is_scripted_or_has_a_named_independent_proof():
    assert proof.SCRIPTED_TOOL_NAMES.isdisjoint(proof.TOOL_EXEMPTIONS)
    assert proof.SCRIPTED_TOOL_NAMES | set(proof.TOOL_EXEMPTIONS) == set(TOOL_NAMES)
    root = Path(__file__).resolve().parents[3]
    for path in proof.TOOL_EXEMPTIONS.values():
        assert (root / path).is_file(), path


def test_rehearsal_pressure_crosses_the_current_renewal_threshold_by_one_token():
    tariff = B.Tariff.frozen()
    assert proof.RENEWAL_COUNT == tariff.long_context_input_tokens * 4 // 5 + 1
    assert N.pressure(0, 0, proof.RENEWAL_COUNT - 1, tariff) == []
    assert N.pressure(0, 0, proof.RENEWAL_COUNT, tariff) == [{
        "reason": "input_tokens", "measured": proof.RENEWAL_COUNT,
        "limit": tariff.long_context_input_tokens, "threshold": proof.RENEWAL_COUNT - 1}]


def test_rehearsal_callbacks_advance_on_creates_not_repeated_admission_counts():
    """Renewal counts the rejected tool request and summary before one create; callbacks must not skip ahead."""
    callbacks = []

    def summary():
        callbacks.append("summary")
        return R.message("SCRIPTED SUMMARY", counted=proof.RENEWAL_COUNT)

    def inspect_slow():
        callbacks.append("arm timeout")
        return R.tool_call("facet_inspect", {}, counted=100)

    def busy():
        callbacks.append("restore timeout")
        return R.tool_call("photo_image", {}, counted=100)

    steps = [(proof.SUMMARY_STEP, summary), ("inspect_slow", inspect_slow), ("busy", busy)]
    transport = R.ScriptedOpenAI([])

    def count(body):
        proof.prepare_script_reply(transport, steps)
        return transport.handle(httpx2.Request("POST", "https://api.openai.com" + R.COUNT_PATH,
                                               json={k: v for k, v in body.items() if k in B.COUNT_FIELDS}))

    def create(body):
        return transport.handle(httpx2.Request("POST", "https://api.openai.com" + R.CREATE_PATH, json=body))

    pending = {"model": R.MODEL, "input": [], "tool_choice": "required"}
    count(pending)
    summary_body = {**pending, "input": [{"role": "user", "content": N.SUMMARY_REQUEST}], "tool_choice": "none"}
    assert count(summary_body).json()["input_tokens"] == proof.RENEWAL_COUNT
    assert callbacks == ["summary"]
    assert create(summary_body).json()["output"][0]["type"] == "message"
    count(pending)
    count(pending)
    assert callbacks == ["summary", "arm timeout"]  # restoring now would disable the intended E_TIMEOUT control
    assert create(pending).json()["output"][0]["name"] == "facet_inspect"
    count(pending)
    assert callbacks == ["summary", "arm timeout", "restore timeout"]
    assert create(pending).json()["output"][0]["name"] == "photo_image"
    assert transport.violations == [] and transport.unexpected == []
    assert len(transport.counts) == 5 and len(transport.creates) == 3
