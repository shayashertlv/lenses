"""Offline T1 comparison-growth scenarios using F1's unchanged cache model.

This is size arithmetic on receipts, not a tokenizer or a synthetic live
conversation. It cannot launch a run, load a key, or authorize spending.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from .cache_replay import (
    DEFAULT_RUN, calibrate, ceiling, compact_bytes, extend_blocks, load_profile,
    measured_increments, modeled_pressure, renewal_template, replay, rounded,
    text_scale,
)
from .cache_sim import CacheBlock, CacheSimulator, request_namespace
from .conversation import estimate_content


COMPARISONS = frozenset({"compare_to_photos", "compare_look_to_photos"})


def comparison_samples(profile, dims, scale):
    """Measure complete comparison results as they actually reached Messages."""
    calls, samples = {}, []
    for row in profile:
        for added in row["added"]:
            block = added.get("value", {})
            if block.get("type") == "tool_use":
                calls[block["id"]] = block
            if block.get("type") != "tool_result":
                continue
            call = calls.get(block["tool_use_id"], {})
            if call.get("name") not in COMPARISONS:
                continue
            if block.get("is_error"):
                raise ValueError("comparison sample is an adapter error")
            size = estimate_content([block], dims)
            # Include the assistant tool-use wrapper, separately from the result.
            call_size = estimate_content([call], dims)
            previous = profile[row["number"] - 2] if row["number"] >= 2 else None
            if previous is None:
                raise ValueError("comparison lacks preceding request timestamp")
            samples.append({
                "tool": call["name"], "tool_use_id": block["tool_use_id"],
                "result_in_request": row["number"],
                "images": size["images"], "image_tokens": size["image_tokens"],
                "text_byte_div_3_tokens": size["text_tokens"],
                "modeled_result_tokens": size["image_tokens"] + ceiling(Decimal(size["text_tokens"]) * scale),
                "result_bytes": len(compact_bytes(block)),
                "call_tokens": ceiling(Decimal(call_size["text_tokens"]) * scale),
                "call_bytes": len(compact_bytes(call)),
                "response_output_tokens": row["actual_usage"]["output_tokens"],
                "request_start_gap_seconds": str(Decimal(str(row["now"])) - Decimal(str(previous["now"]))),
            })
    if not samples:
        raise ValueError("no observed comparison results")
    fields = ("images", "image_tokens", "text_byte_div_3_tokens", "modeled_result_tokens",
              "result_bytes", "call_tokens", "call_bytes", "response_output_tokens",
              "request_start_gap_seconds")
    return {
        "samples": samples,
        "means": {key: str(sum((Decimal(str(row[key])) for row in samples), Decimal(0)) / len(samples))
                  for key in fields},
        "definition": "Complete tool_result blocks sent to Messages, with native tiled file-ID images; assistant call wrappers are measured separately. Output assumes the observed replies that consumed those comparison results, including billed thinking.",
    }


def balanced(mean, ordinal):
    """Integer increments with cumulative round-half-up, preserving the mean."""
    if ordinal < 1:
        raise ValueError("ordinal must be positive")
    return rounded(Decimal(mean) * ordinal) - rounded(Decimal(mean) * (ordinal - 1))


def distribute_comparisons(source_count, extra_count):
    """Spread additions across source gaps; retain the original final response."""
    if source_count < 2 or extra_count < 0:
        raise ValueError("need at least two sources and nonnegative additions")
    gaps = source_count - 1
    return [0] + [extra_count * n // gaps - extra_count * (n - 1) // gaps
                  for n in range(1, source_count)]


def source_steps(profile, *, count):
    """F1 source increments and mean-growth extension, before renewal."""
    scale = Decimal(text_scale(profile)["ratio"])
    increments = measured_increments(profile)
    increments.extend(row["new"]["image_tokens"] + ceiling(Decimal(row["new"]["text_tokens"]) * scale)
                      for row in profile[40:])
    spans = len(profile) - 1
    means = {
        "input": Decimal(sum(increments[1:])) / spans,
        "images": Decimal(profile[-1]["total_estimate"]["images"] - profile[0]["total_estimate"]["images"]) / spans,
        "bytes": Decimal(profile[-1]["payload_bytes"] - profile[0]["payload_bytes"]) / spans,
        "output": Decimal(sum(row["actual_usage"]["output_tokens"] for row in profile)) / len(profile),
        "user": Decimal(sum(row["meter"]["forecast"]["new_content"]["total_tokens"] for row in profile[1:])) / spans,
        "gap": (Decimal(str(profile[-1]["now"])) - Decimal(str(profile[0]["now"]))) / spans,
    }
    steps = []
    for index in range(count):
        if index < len(profile):
            row = profile[index]
            steps.append({
                "kind": "source", "source_request": index + 1,
                "input_increment": increments[index], "images": row["new"]["images"],
                "bytes": row["payload_bytes"] - (profile[index - 1]["payload_bytes"] if index else 0),
                "output": row["actual_usage"]["output_tokens"],
                "user_estimate": row["meter"]["forecast"]["new_content"]["total_tokens"],
                "gap": Decimal(0) if not index else Decimal(str(row["now"])) - Decimal(str(profile[index - 1]["now"])),
                "uncached": row["actual_usage"]["input_tokens"] if index < 40 else 2,
                "blocks": row["added"],
            })
        else:
            n = index - len(profile) + 1
            steps.append({
                "kind": "source", "source_request": index + 1,
                "input_increment": balanced(means["input"], n),
                "images": balanced(means["images"], n), "bytes": balanced(means["bytes"], n),
                "output": balanced(means["output"], n), "user_estimate": ceiling(means["user"]),
                "gap": means["gap"], "uncached": 2,
                "blocks": [{"source_projection": index + 1, "block": i} for i in range(3)],
            })
    return steps


def expanded_scenario(profile, *, base, extra_count, comparison, renewal):
    """Keep every baseline productive request and insert comparison exchanges.

    Summary requests are additional, so they cannot silently replace one of the
    comparisons. Original source timing gaps are retained; added comparisons
    extend elapsed time by their observed mean gap.
    """
    steps = source_steps(profile, count=base["productive_requests"])
    counts = distribute_comparisons(len(steps), extra_count)
    means, events, number = comparison["means"], [], 0
    for index, source in enumerate(steps):
        for _ in range(counts[index]):
            number += 1
            events.append({
                "kind": "comparison", "comparison": number,
                "input_increment": balanced(means["modeled_result_tokens"], number) + balanced(means["call_tokens"], number),
                "images": balanced(means["images"], number),
                # Two message envelopes and commas are a disclosed allowance.
                "bytes": balanced(means["result_bytes"], number) + balanced(means["call_bytes"], number) + 80,
                "output": balanced(means["response_output_tokens"], number),
                "user_estimate": balanced(means["image_tokens"], number) + balanced(means["text_byte_div_3_tokens"], number),
                "gap": Decimal(means["request_start_gap_seconds"]), "uncached": 2,
                "blocks": [{"comparison_exchange": number, "block": i} for i in range(3)],
            })
        events.append(source)
    simulator = CacheSimulator(profile[0]["body"]["model"])
    namespace = request_namespace(profile[0]["body"])
    now = Decimal(str(profile[0]["now"]))
    beginning = now
    blocks, rows, renewals = [], [], []
    images = payload = previous_input = previous_output = pending_extra = 0
    for event in events:
        now += event["gap"]
        increment = event["input_increment"] + pending_extra
        images += event["images"]
        payload += event["bytes"] + pending_extra * 3
        forecast = (previous_input + previous_output + event["user_estimate"]
                    if rows else profile[0]["meter"]["forecast"]["forecast_tokens"])
        reasons = modeled_pressure(images=images, payload_bytes=payload, forecast=forecast)
        if reasons:
            summary_blocks = extend_blocks(extend_blocks(blocks, event["blocks"], increment),
                                           [{"summary": len(renewals) + 1}], 400)
            summary = simulator.request(summary_blocks, now=float(now),
                                        output_tokens=renewal["summary_output_tokens"],
                                        uncached_tokens=event["uncached"], namespace=namespace)
            renewals.append({
                "before_productive_request": len(rows) + 1, "trigger_reasons": reasons,
                "forecast_tokens": forecast, "input_tokens": summary.total_input_tokens,
                "cache_read_tokens": summary.read_tokens, "cache_write_tokens": summary.write_tokens,
                "output_tokens": renewal["summary_output_tokens"], "cost_usd": str(summary.cost_usd),
                "image_occurrences": images, "payload_bytes": payload + 1200,
            })
            simulator = CacheSimulator(profile[0]["body"]["model"])
            blocks = [CacheBlock({"epoch": len(renewals)}, renewal["opening_tokens"])]
            images, payload = renewal["image_occurrences"], renewal["payload_bytes"]
            now += 1  # Same F1 summary-to-new-opening timing assumption.
        else:
            blocks = extend_blocks(blocks, event["blocks"], increment)
        result = simulator.request(blocks, now=float(now), output_tokens=event["output"],
                                   uncached_tokens=2 if reasons else event["uncached"], namespace=namespace)
        rows.append({
            "productive_request": len(rows) + 1, "kind": event["kind"],
            "source_request": event.get("source_request"), "comparison": event.get("comparison"),
            "input_tokens": result.total_input_tokens,
            "cache_read_tokens": result.read_tokens, "cache_write_tokens": result.write_tokens,
            "uncached_tokens": result.usage["input_tokens"], "output_tokens": event["output"],
            "cost_usd": str(result.cost_usd), "image_occurrences": images, "payload_bytes": payload,
            "forecast_tokens": forecast, "renewed": bool(reasons),
            "comparison_output_tokens_consumed_by_request_or_summary": pending_extra,
            "elapsed_seconds": str(now - beginning),
        })
        previous_input, previous_output = result.total_input_tokens, event["output"]
        # Source input increments already carry source outputs. Only new output
        # must be added to later input, once, and it is dropped at renewal.
        pending_extra = event["output"] if event["kind"] == "comparison" else 0
    all_rows = rows + renewals
    total = sum((Decimal(row["cost_usd"]) for row in all_rows), Decimal(0))
    return {
        "baseline_total_requests": base["total_requests_including_renewal_summaries"],
        "baseline_productive_requests": base["productive_requests"],
        "baseline_renewals": len(base["renewals"]), "baseline_cost_usd": base["cost_usd"],
        "added_comparison_requests": extra_count, "productive_requests": len(rows),
        "total_requests_including_renewal_summaries": len(all_rows),
        "nominal_baseline_plus_comparisons": base["total_requests_including_renewal_summaries"] + extra_count,
        "additional_renewals_vs_baseline": len(renewals) - len(base["renewals"]),
        "cost_usd": str(total), "delta_cost_usd": str(total - Decimal(base["cost_usd"])),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "renewal_summary_output_tokens": sum(row["output_tokens"] for row in renewals),
        "comparison_result_tokens_added": rounded(Decimal(means["modeled_result_tokens"]) * extra_count),
        "comparison_image_occurrences_added": rounded(Decimal(means["images"]) * extra_count),
        "comparison_result_bytes_added": rounded(Decimal(means["result_bytes"]) * extra_count),
        "elapsed_request_start_seconds": str(now - beginning),
        "cache_misses_productive": sum(row["cache_read_tokens"] == 0 for row in rows),
        "largest_input_tokens": max(row["input_tokens"] for row in all_rows),
        "largest_image_occurrences": max(row["image_occurrences"] for row in all_rows),
        "largest_payload_bytes": max(row["payload_bytes"] for row in all_rows),
        "requests": rows, "renewals": renewals,
    }


def build_report(run=DEFAULT_RUN):
    profile, dims = load_profile(run)
    scale = text_scale(profile)
    renewal = renewal_template(run, profile, dims, Decimal(scale["ratio"]))
    comparison = comparison_samples(profile, dims, Decimal(scale["ratio"]))
    bases = {count: replay(profile, dims, count=count, renewal=renewal) for count in (len(profile), 70)}
    scenarios = {}
    for count, base in bases.items():
        for percent in (50, 100):
            extra = ceiling(Decimal(count) * percent / 100)
            row = expanded_scenario(profile, base=base, extra_count=extra,
                                    comparison=comparison, renewal=renewal)
            row["requested_more_percent"] = percent
            row["rounded_added_percent"] = str(Decimal(extra) * 100 / count)
            scenarios[f"{count}_plus_{percent}_percent_comparisons"] = row
    return {
        "schema": "g3d.claude_tool_use_estimate/1", "owner_information_only": True,
        "paid_calls": 0, "source_run": str(Path(run).resolve()),
        "model": profile[0]["body"]["model"], "calibration": calibrate(profile),
        "text_scale": scale, "comparison_measurement": comparison,
        "renewal_template": renewal, "scenarios": scenarios,
        "assumptions": [
            "Uses F1 CacheSimulator, measured increments, pooled text calibration and unchanged renewal thresholds. No provider requests, tokenizer claims, new price research or actual cleared_input_tokens in arithmetic.",
            "Baseline C5 is 45 productive requests. F1's 70-request baseline contains 69 productive requests plus 1 renewal summary. Preserve all baseline productive requests; additions are comparisons only. Any extra renewal summaries increase the final billable count beyond the nominal 50%/100% target.",
            "Added comparisons = ceil(baseline total requests * percent/100): 45 at 50% adds 23 (51.111...%); other cases add 45, 35, 70. Counts for result tokens/images/bytes/output use balanced cumulative round-half-up, not rounding every mean independently.",
            "Source requests remain in chronological order, with saved gaps unchanged. Comparisons are spread evenly into the gaps before later source requests, including before the final source request. Each inserted gap uses the C5 comparison call-to-result request-start mean. Original absolute timestamps therefore shift; this is an interleaved counterfactual, not the original chronology.",
            "Comparison size is the average of all 3 C5 compare_look_to_photos results actually sent to Messages. No compare_to_photos sample exists. Native visual patch tokens and complete compact tool_result bytes are measured; text tokens use F1's pooled ratio. File upload bytes are not prompt bytes.",
            "Added comparison replies use the mean billed output of the 3 replies that consumed comparison results, including thinking. Those extra output tokens enter the following input once until renewal, with 3 estimated UTF-8 bytes per retained token. Source outputs already occur inside F1 source increments and are not added twice. 80 bytes per extra exchange allow for two message envelopes.",
            "Every renewal completes the pending exchange, adds a 400-token summary prompt and 4000 output tokens, then charges a full fresh opening from F1's fixed saved final C5 evidence set (all 6 owner photos plus 57 evidence images, 2000 host-state tokens). Replacement evidence selection in a future run is unknown. Summary-to-opening delay remains F1's 1-second placeholder; elapsed times are request-start spans and exclude the final response duration.",
            "First 40 source input increments and output usage are observations. Later C5 text and all extension/comparison sizes are estimates; hidden thinking is not visible in receipt text. Only cache behavior on supplied sizes is simulated. Long pauses can create extra full cache writes.",
            "T1's new instruction paragraph and factual host-note text are not included in this comparison-only size scenario. Model behavior, requests, tool choices, output and future current evidence can change. These are full-profile planning costs, not a 20-dollar cutoff simulation, reservation, approval or guarantee.",
        ],
    }


def write_report(run, destination):
    result = build_report(run)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "estimate.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    means = result["comparison_measurement"]["means"]
    lines = [f"C5 comparison result mean: {means['modeled_result_tokens']} modeled tokens, {means['images']} images, {means['result_bytes']} compact JSON bytes; all 3 observed look comparisons."]
    for row in result["scenarios"].values():
        lines.append(f"{row['baseline_total_requests']} requests +{row['requested_more_percent']}%: add {row['added_comparison_requests']} comparisons; {row['productive_requests']} productive + {len(row['renewals'])} summaries = {row['total_requests_including_renewal_summaries']} requests; ${Decimal(row['cost_usd']):.2f} (exact {row['cost_usd']}).")
    lines.append("Offline estimates, not bounds or spending authority: ceil added requests, balanced size rounding, original order with inserted comparison time, extra output retained, and fixed F1 renewal evidence; assumptions/details in estimate.json.")
    (destination / "estimate_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "work/t1")
    args = parser.parse_args()
    result = write_report(args.run, args.output)
    print(json.dumps({name: {key: row[key] for key in (
        "cost_usd", "added_comparison_requests", "total_requests_including_renewal_summaries",
        "additional_renewals_vs_baseline", "largest_input_tokens")}
                      for name, row in result["scenarios"].items()}, indent=2))


if __name__ == "__main__":
    main()
