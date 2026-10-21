"""Saved request 73 summary replay; no model client, count endpoint or source mutation."""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from glasses3d.agent import renewal as N
from glasses3d.agent.budget import payload_images
from glasses3d.agent.look_cost import documented_image
from glasses3d.agent.pricing import Tariff
from glasses3d.agent.receipts import write_json

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "glasses3d/data/jobs/oakley-oo9208-v2-7035e95c/runs/20261021T123308-aa22d4"


def replay(out):
    if not (RUN / "transport/0073-request.json").is_file():
        return None
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))
    before = read(RUN / "transport/0072-request.json")
    original = read(RUN / "transport/0073-request.json")
    trigger = copy.deepcopy(original)
    trigger["input"].pop()  # the existing summary instruction is appended by renew
    old_items = copy.deepcopy(trigger["input"])
    latest = old_items[-1]
    assert latest["type"] == "function_call_output"
    summary_items, carried, omitted = N.summary_input(old_items, (len(before["input"]), N.input_digest(before["input"])))
    # Replay the orchestration on archived image descriptors. The opaque descriptors
    # are unchanged in the fresh carry; the live SDK proof separately checks URLs.
    class Session:
        session_id = "saved-request-73"
        def __init__(self): self.items = copy.deepcopy(old_items)
        async def get_items(self): return copy.deepcopy(self.items)
        async def add_items(self, items): self.items.extend(copy.deepcopy(items))
        async def renew(self, items, receipt):
            self.archive = self.items
            self.items = copy.deepcopy(items)
            return "task23-renewed"
    session = Session()
    creates = []
    async def create(**request):
        creates.append(request)
        return SimpleNamespace(id="scripted-summary", usage=None, status="completed", output=[],
                               output_text="Saved-request replay: latest images await inspection.")
    async def hook(*args): pass
    saved_state = read(RUN / "renewals.json")["renewals"][1]["host_state"]
    scene = SimpleNamespace(renewal_evidence=lambda: [], renewal_state=lambda: saved_state,
                            renewal_started=lambda: {"finish_review_restarted": False})
    guard = SimpleNamespace(request_pressure=None, renewal_summary=False,
                            summary=lambda: {"requests": 73, "settled_usd": "21.5", "settled_stop_usd": "25"})
    log = SimpleNamespace(run_id="task23-replay", interactions=0, tool_calls=[],
                          event=lambda *a, **kw: None, note=lambda *a, **kw: None)
    spec = SimpleNamespace(owner_notes=[], brief="Unchanged saved-request replay", photos=[],
                           tools=[SimpleNamespace(name="record_difference")],
                           settings=SimpleNamespace(instruction_profile="decision-20261021"))
    renewal = N.ConversationRenewal(spec=spec, scene=scene, session=session, log=log, guard=guard,
        client=SimpleNamespace(responses=SimpleNamespace(create=create)), directory=Path(out))
    renewal._previous_input = (len(before["input"]), N.input_digest(before["input"]))
    asyncio.run(renewal.renew(N.RenewalNeeded(trigger, {"input_tokens": 302367}, [{"reason": "input_tokens"}]),
                              agent=SimpleNamespace(instructions=original["instructions"]), context=None,
                              hooks=SimpleNamespace(on_llm_start=hook, on_llm_end=hook)))
    summary = creates[0]
    assert session.archive[:len(old_items)] == old_items
    assert summary["input"][:-1] == summary_items
    image_tokens = 0
    image_rows = []
    for part in latest["output"]:
        if part["type"] != "input_image": continue
        desc = part["image_url"]
        matches = list((RUN.parent.parent / "blobs").glob(desc["sha256"] + ".*"))
        assert len(matches) == 1
        path = matches[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == desc["sha256"]
        with Image.open(path) as im: dimensions = im.size
        tokens = documented_image(*dimensions, part.get("detail", "high"))["tokens"]
        image_rows.append({"sha256": desc["sha256"], "dimensions": dimensions, "tokens": tokens})
        image_tokens += tokens
    extra_text = summary_items[-1]["output"][-1]["text"]
    # Explicit local estimate, not a provider count. Original labels are retained;
    # account for the additional omission notice/list instead of claiming it free.
    added_tokens = math.ceil(len(extra_text.encode("utf-8")) / 4)
    budget = read(RUN / "budget.json")
    actual = budget["rows"][72]
    usage = actual["usage"]
    inp = usage["input_tokens"] - image_tokens + added_tokens
    predicted_usage = {"input_tokens": inp, "output_tokens": usage["output_tokens"],
        "input_tokens_details": {"cached_tokens": usage["cached_tokens"],
          "cache_write_tokens": usage["cache_write_tokens"] - image_tokens + added_tokens}}
    estimate = Tariff.from_dict(budget["tariff"]).settle_micro(predicted_usage)
    result = {"schema": "g3d.task23_renewal_replay/1", "source": str(RUN),
        "summary_images_before": payload_images(original["input"]),
        "summary_images_after": payload_images(summary["input"]), "omitted_images": len(omitted),
        "exact_prefix": summary["input"][:len(before["input"])] == before["input"],
        "exact_result_text": [p for p in summary_items[-1]["output"] if p["type"] == "input_text"][:-1]
            == [p for p in latest["output"] if p["type"] == "input_text"],
        "exact_latest_carry": session.items[0]["content"][-len(latest["output"]):] == latest["output"],
        "removed_image_tokens_estimate": image_tokens, "added_text_tokens_estimate": added_tokens,
        "summary_input_tokens_estimate": inp, "cached_tokens_preserved_assumption": usage["cached_tokens"],
        "output_tokens_held_fixed": usage["output_tokens"], "original_micro_usd": actual["settled_micro_usd"],
        "summary_micro_usd_estimate": estimate, "saving_micro_usd_estimate": actual["settled_micro_usd"] - estimate,
        "image_estimates": image_rows, "omission_labels": omitted,
        "limits": "Scripted transport identity proof; tokens/cost are offline estimates, not measured live savings.",
        "actual_paid_requests": 0, "actual_spend_usd": 0}
    write_json(Path(out) / "replay.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    result = replay(parser.parse_args().out)
    print(json.dumps({k: v for k, v in result.items() if k not in {"image_estimates", "omission_labels"}}, indent=2))
