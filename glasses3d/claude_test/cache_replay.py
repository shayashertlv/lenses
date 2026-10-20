"""Offline C5 cache calibration and owner-only estimates.

This module only reads saved requests, usage and image metadata. It has no
network, key loading, ledger writes, approval or run-admission path. The input
token observations calibrate token *sizes*, never cache behavior. Token sizes
inside a newly appended group are unknown: assigning the group's measured
total to its final block is sufficient because there are no intermediate
breakpoints. That assignment is not a claim about provider tokenization.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import json
from pathlib import Path
from threading import RLock

from .cache_sim import CacheBlock, CacheSimulator, request_namespace, request_values
from .conversation import SOFT_BYTES, SOFT_IMAGES, SOFT_TOKENS, estimate_content
from .images import image_block


DEFAULT_RUN = Path(__file__).parent / "work/c5-20261020/jobs/oakley-oo9208-v2-911a2b0e/runs/20261020T102312-ca458f"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compact_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def rounded(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_HALF_UP))


def ceiling(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_CEILING))


def count_nested_blocks(value):
    """Sensitivity count: parent content block plus all nested content blocks."""
    if not isinstance(value, dict):
        return 0
    children = value.get("content", [])
    return int("type" in value) + (sum(count_nested_blocks(child) for child in children)
                                 if isinstance(children, list) else 0)


def extend_blocks(blocks, values, tokens):
    """Preserve each old size; allocate new aggregate tokens to the final block."""
    if tokens < 0:
        raise ValueError("negative append token increment")
    if not values:
        if tokens:
            raise ValueError("nonzero tokens without new blocks")
        return list(blocks)
    return [*blocks, *[CacheBlock(value, 0) for value in values[:-1]],
            CacheBlock(values[-1], tokens)]


def modeled_pressure(*, images, payload_bytes, forecast):
    """Apply the live policy constants to this modeled conversation epoch."""
    return [{"reason": name, "measured": value, "threshold": threshold}
            for name, value, threshold, triggered in (
                ("images", images, SOFT_IMAGES, images > SOFT_IMAGES),
                ("payload_bytes", payload_bytes, SOFT_BYTES, payload_bytes >= SOFT_BYTES),
                ("input_tokens", forecast, SOFT_TOKENS, forecast > SOFT_TOKENS)) if triggered]


def load_profile(run):
    run = Path(run)
    ledger = [json.loads(line) for line in (run / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    settled = {row["request_no"]: row for row in ledger if row["event"] == "settled"}
    starts = {row["request_no"]: datetime.fromisoformat(row["time"]).timestamp()
              for row in ledger if row["event"] == "started"}
    dims = {row["file_id"]: (row["w"], row["h"])
            for row in read_json(run / "file-cache.json").values()}
    profile, previous = [], []
    for number in sorted(settled):
        body = read_json(run / "requests" / f"{number:04d}-request.json")
        body.pop("context_management", None)
        values = request_values(body)
        if values[:len(previous)] != previous:
            raise ValueError(f"request {number} is not append-only")
        added = values[len(previous):]
        estimate = estimate_content([value["value"] for value in added], dims)
        messages_added = [value for value in added if value["section"] == "messages"]
        total_estimate = estimate_content([value["value"] for value in values], dims)
        meter = read_json(run / "requests" / f"{number:04d}-meter.json")
        row = {
            "number": number, "body": body, "values": values, "added": added,
            "new": estimate, "total_estimate": total_estimate,
            "new_top_level_message_blocks": len(messages_added),
            "new_nested_inclusive_message_blocks": sum(count_nested_blocks(value["value"]) for value in messages_added),
            "actual_usage": settled[number]["usage"],
            "actual_cost_usd": Decimal(settled[number]["cost_usd"]),
            "now": starts[number], "meter": meter,
            "payload_bytes": len(compact_bytes(body)),
        }
        profile.append(row)
        previous = values
    return profile, dims


def measured_increments(profile, count=40):
    """Measured cacheable input totals minus the preceding observed total.

    Usage.input_tokens is a separately measured uncached suffix, not a fitted
    512-token rounding rule. It is 4/2/80 tokens in the calibration sample.
    """
    previous, increments = 0, []
    for row in profile[:count]:
        usage = row["actual_usage"]
        total = usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]
        increments.append(total - previous)
        previous = total
    return increments


def calibrate(profile):
    increments = measured_increments(profile)
    simulator, blocks, rows = CacheSimulator(profile[0]["body"]["model"]), [], []
    for row, increment in zip(profile[:40], increments):
        blocks = extend_blocks(blocks, row["added"], increment)
        usage = row["actual_usage"]
        result = simulator.request(blocks, now=row["now"], output_tokens=usage["output_tokens"],
                                   uncached_tokens=usage["input_tokens"],
                                   namespace=request_namespace(row["body"]))
        mismatches = {key: {"actual": usage[key], "simulated": result.usage[key]}
                      for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
                      if usage[key] != result.usage[key]}
        rows.append({
            "request": row["number"], "input_increment": increment,
            "uncached_suffix": usage["input_tokens"],
            "actual_read": usage["cache_read_input_tokens"], "simulated_read": result.read_tokens,
            "actual_write": usage["cache_creation_input_tokens"], "simulated_write": result.write_tokens,
            "output_tokens": usage["output_tokens"],
            "actual_cost_usd": str(row["actual_cost_usd"]), "simulated_cost_usd": str(result.cost_usd),
            "mismatches": mismatches,
        })
    actual = sum((row["actual_cost_usd"] for row in profile[:40]), Decimal(0))
    predicted = sum((result.cost_usd for result in simulator.results), Decimal(0))
    return {
        "requests": rows, "actual_cost_usd": str(actual), "simulated_cost_usd": str(predicted),
        "relative_error": str(abs(predicted - actual) / actual),
        "mismatched_requests": [row["request"] for row in rows if row["mismatches"]],
        "method": "Measured aggregate cacheable-input increments; unchanged separately observed uncached suffix; exact saved output usage and request-start timestamps. Cache hit selection is independently simulated from exact request prefixes.",
    }


def text_scale(profile):
    increments = measured_increments(profile)
    image_tokens = sum(row["new"]["image_tokens"] for row in profile[:40])
    actual_text = sum(increments) - image_tokens
    estimated_text = sum(row["new"]["text_tokens"] for row in profile[:40])
    if actual_text < 0 or estimated_text <= 0:
        raise ValueError("invalid measured text calibration")
    return {
        "actual_nonimage_tokens": actual_text, "estimated_text_byte_div_3_tokens": estimated_text,
        "exact_image_tokens": image_tokens,
        "ratio": str(Decimal(actual_text) / Decimal(estimated_text)),
        "method": "First 40 measured cacheable input tokens minus exact image patches, divided by first 40 local text byte/3 estimates. Includes system/tool schemas, hidden retained thinking and token-wrapper residuals; it is a pooled estimate, not a tokenizer.",
    }


def renewal_template(run, profile, dims, scale):
    """Select saved current evidence with the existing method, without a worker."""
    from glasses3d.agent.job_scene import JobScene

    root = Path(run).parents[1]
    state = read_json(root / "work/job_scene/state.json")
    scene = object.__new__(JobScene)
    scene.root, scene.images, scene.events = root, state["images"], state["events"]
    scene._unseen, scene._state_lock = set(state["unseen"]), RLock()
    evidence = JobScene.renewal_evidence(scene)
    manifest = [json.loads(line) for line in (Path(run) / "image-manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    content, seen = [], set()
    for record in evidence:
        matches = [row for row in manifest if row.get("source_sha256") == record["sha256"]]
        if not matches:
            raise ValueError("renewal evidence lacks recorded tile dimensions")
        for row in matches:
            # Repeated manifests can describe the same source/tile. Current
            # evidence carries the image once, exactly as prepare_images does.
            key = (record["call_id"], row["file_id"])
            if key in seen:
                continue
            seen.add(key)
            content.extend([
                {"type": "text", "text": f"Renewal evidence: call {record['call_id']}; checkpoint {record.get('checkpoint')}; view {record.get('view')}; kind {record.get('kind')}; sha256 {record['sha256']}."},
                image_block(row["file_id"]),
            ])
    estimate = estimate_content(content, dims)
    # A future model summary and future factual state do not exist. These are
    # explicit scenario assumptions, never forged contents of a live opening.
    summary_tokens, host_state_tokens = 4000, 2000
    opening_tokens = measured_increments(profile)[0] + estimate["image_tokens"] + ceiling(
        Decimal(estimate["text_tokens"]) * scale) + summary_tokens + host_state_tokens
    return {
        "opening_tokens": opening_tokens,
        "image_occurrences": profile[0]["total_estimate"]["images"] + estimate["images"],
        "evidence_image_occurrences": estimate["images"],
        "evidence_visual_tokens": estimate["image_tokens"],
        "evidence_records": len(evidence), "summary_output_tokens": summary_tokens,
        "host_state_tokens": host_state_tokens,
        "payload_bytes": profile[0]["payload_bytes"] + len(compact_bytes(content)) + (summary_tokens + host_state_tokens) * 3,
        "assumption": "A future renewal carries all six owner photos and the saved final C5 current-evidence set selected by JobScene.renewal_evidence, plus a 4,000-token summary and 2,000-token host state. A future evidence set or summary may differ.",
    }


def replay(profile, dims, *, count=45, output_multiplier=1, retain_extra_output=False, renewal=None):
    """Replay exact C5 structure within ``count`` total billable requests.

    Renewal summaries consume a request in that count. The remaining slots are
    productive requests, so a 70-request scenario with one summary has69 such
    requests.
    """
    if count < len(profile):
        raise ValueError("replay count must include all saved requests")
    scale_info = text_scale(profile)
    scale = Decimal(scale_info["ratio"])
    increments = measured_increments(profile)
    increments.extend(row["new"]["image_tokens"] + ceiling(Decimal(row["new"]["text_tokens"]) * scale)
                      for row in profile[40:])
    growth = Decimal(sum(increments[1:])) / (len(profile) - 1)
    mean_output = Decimal(sum(row["actual_usage"]["output_tokens"] for row in profile)) / len(profile)
    mean_images = Decimal(profile[-1]["total_estimate"]["images"] - profile[0]["total_estimate"]["images"]) / (len(profile) - 1)
    mean_bytes = Decimal(profile[-1]["payload_bytes"] - profile[0]["payload_bytes"]) / (len(profile) - 1)
    mean_user_estimate = Decimal(sum(row["meter"]["forecast"]["new_content"]["total_tokens"] for row in profile[1:])) / (len(profile) - 1)
    mean_gap = (profile[-1]["now"] - profile[0]["now"]) / (len(profile) - 1)
    simulator, blocks, rows, renewal_rows = CacheSimulator(profile[0]["body"]["model"]), [], [], []
    previous_input = previous_output = previous_base_output = 0
    images = payload = 0
    now = profile[0]["now"]
    for index in range(count):
        if index + len(renewal_rows) >= count:
            break
        if index < len(profile):
            row = profile[index]
            added, increment = row["added"], increments[index]
            new_images = row["new"]["images"]
            byte_increment = row["payload_bytes"] - (profile[index - 1]["payload_bytes"] if index else 0)
            base_output = row["actual_usage"]["output_tokens"]
            user_estimate = row["meter"]["forecast"]["new_content"]["total_tokens"]
            now = row["now"]
            overhead = row["actual_usage"]["input_tokens"] if index < 40 else 2
            namespace = request_namespace(row["body"])
        else:
            n = index - len(profile) + 1
            # Balanced cumulative rounding avoids changing the mean with every
            # integer request and gives deterministic image/byte projections.
            increment = rounded(growth * n) - rounded(growth * (n - 1))
            new_images = rounded(mean_images * n) - rounded(mean_images * (n - 1))
            byte_increment = rounded(mean_bytes * n) - rounded(mean_bytes * (n - 1))
            base_output = rounded(mean_output * n) - rounded(mean_output * (n - 1))
            user_estimate = ceiling(mean_user_estimate)
            added = [{"projection": index + 1, "block": number} for number in range(3)]
            now += mean_gap
            overhead = 2
            namespace = request_namespace(profile[0]["body"])
        # Output doubling includes the extra retained output in the *next*
        # input, not merely the output bill. It leaves tool evidence unchanged.
        retained_extra = (output_multiplier - 1) * previous_base_output if retain_extra_output else 0
        increment += retained_extra
        images += new_images
        payload += byte_increment + retained_extra * 3
        forecast_output = previous_output if retain_extra_output else previous_base_output
        forecast = (previous_input + forecast_output + user_estimate) if index else profile[0]["meter"]["forecast"]["forecast_tokens"]
        reasons = modeled_pressure(images=images, payload_bytes=payload, forecast=forecast)
        if reasons:
            if renewal is None:
                raise ValueError("renewal template required when a soft trigger fires")
            # Renewal first appends a summary request in the old epoch. Model
            # its explicit 400-token instruction separately from its response.
            summary_blocks = extend_blocks(extend_blocks(blocks, added, increment),
                                           [{"renewal_summary_request": index + 1}], 400)
            summary = simulator.request(summary_blocks, now=now, output_tokens=renewal["summary_output_tokens"],
                                        uncached_tokens=overhead, namespace=namespace)
            renewal_rows.append({
                "before_productive_request": index + 1, "trigger_reasons": reasons,
                "forecast_tokens": forecast, "summary_cost_usd": str(summary.cost_usd),
                "summary_input_tokens": summary.total_input_tokens,
                "summary_cache_read_tokens": summary.read_tokens,
                "summary_cache_write_tokens": summary.write_tokens,
                "summary_payload_bytes": payload + 400 * 3,
                "summary_image_occurrences": images,
                "summary_output_tokens": renewal["summary_output_tokens"],
                "new_opening_write_tokens": renewal["opening_tokens"],
            })
            if index + len(renewal_rows) >= count:
                break
            simulator = CacheSimulator(profile[0]["body"]["model"])
            blocks = [CacheBlock({"renewal_epoch": len(renewal_rows)}, renewal["opening_tokens"])]
            images, payload = renewal["image_occurrences"], renewal["payload_bytes"]
            increment, overhead = renewal["opening_tokens"], 2
            now += 1
        else:
            blocks = extend_blocks(blocks, added, increment)
        result = simulator.request(blocks, now=now, output_tokens=base_output * output_multiplier,
                                   uncached_tokens=overhead, namespace=namespace)
        rows.append({
            "request": index + 1, "source": "C5 receipt" if index < len(profile) else "mean-growth projection",
            "actual_cost_usd": str(profile[index]["actual_cost_usd"]) if index < len(profile) else None,
            "simulated_cost_usd": str(result.cost_usd), "input_tokens": result.total_input_tokens,
            "cache_read_tokens": result.read_tokens, "cache_write_tokens": result.write_tokens,
            "output_tokens": result.usage["output_tokens"], "forecast_tokens": forecast,
            "image_occurrences": images, "payload_bytes": payload,
            "new_image_tokens": profile[index]["new"]["image_tokens"] if index < len(profile) else None,
            "new_text_byte_div_3": profile[index]["new"]["text_tokens"] if index < len(profile) else None,
            "retained_extra_output_tokens": retained_extra,
            "renewed": bool(reasons),
        })
        previous_input, previous_output, previous_base_output = result.total_input_tokens, result.usage["output_tokens"], base_output
    total = sum((Decimal(row["simulated_cost_usd"]) for row in rows), Decimal(0))
    total += sum((Decimal(row["summary_cost_usd"]) for row in renewal_rows), Decimal(0))
    all_sizes = [*rows, *[{
        "request": f"summary_before_{row['before_productive_request']}",
        "input_tokens": row["summary_input_tokens"],
        "payload_bytes": row["summary_payload_bytes"],
        "image_occurrences": row["summary_image_occurrences"],
        "source": "modeled renewal-summary request",
    } for row in renewal_rows]]
    return {
        "requested_total_requests": count, "productive_requests": len(rows),
        "total_requests_including_renewal_summaries": len(rows) + len(renewal_rows),
        "output_multiplier": output_multiplier, "retain_extra_output": retain_extra_output, "cost_usd": str(total),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "renewal_summary_output_tokens": sum(row["summary_output_tokens"] for row in renewal_rows),
        "requests": rows, "renewals": renewal_rows,
        "largest_request": {
            "by_input_tokens": max(all_sizes, key=lambda row: row["input_tokens"]),
            "by_payload_bytes": max(all_sizes, key=lambda row: row["payload_bytes"]),
            "by_image_occurrences": max(all_sizes, key=lambda row: row["image_occurrences"]),
        },
        "projected_means": {"input_growth_per_request": str(growth), "output_tokens_per_request": str(mean_output),
                            "images_per_request": str(mean_images), "wire_bytes_per_request": str(mean_bytes),
                            "user_content_estimate_per_request": str(mean_user_estimate), "gap_seconds": mean_gap},
    }


def build_report(run=DEFAULT_RUN):
    profile, dims = load_profile(run)
    calibration = calibrate(profile)
    scale = text_scale(profile)
    renewal = renewal_template(run, profile, dims, Decimal(scale["ratio"]))
    actual_cost = sum((row["actual_cost_usd"] for row in profile), Decimal(0))
    scenarios = {f"{count}_requests_{multiplier}x_output": replay(
        profile, dims, count=count, output_multiplier=multiplier, renewal=renewal)
        for count in (45, 70) for multiplier in (1, 2)}
    scenarios.update({f"{count}_requests_2x_output_retained_sensitivity": replay(
        profile, dims, count=count, output_multiplier=2, retain_extra_output=True, renewal=renewal)
        for count in (45, 70)})
    max_top = max(row["new_top_level_message_blocks"] for row in profile[1:])
    max_nested = max(row["new_nested_inclusive_message_blocks"] for row in profile[1:])
    return {
        "schema": "g3d.claude_cache_estimate/1", "source_run": str(Path(run).resolve()),
        "model": profile[0]["body"]["model"], "owner_information_only": True, "paid_calls": 0,
        "actual_c5_cost_usd": str(actual_cost),
        "actual_c5_output_tokens": sum(row["actual_usage"]["output_tokens"] for row in profile),
        "calibration": calibration, "text_scale": scale, "renewal_template": renewal, "scenarios": scenarios,
        "lookback": {
            "model_assumption": "20 prior top-level content-block positions; nested tool-result content is inside its parent. Inclusive distance20 boundary is not exercised by C5.",
            "maximum_appended_top_level_message_blocks": max_top,
            "maximum_appended_nested_inclusive_blocks": max_nested,
            "per_request": [{"request": row["number"], "top_level_added": row["new_top_level_message_blocks"],
                             "nested_inclusive_added": row["new_nested_inclusive_message_blocks"]} for row in profile],
            "observed_counterexamples_to_nested_count": [
                {"request": row["number"], "nested_added": row["new_nested_inclusive_message_blocks"],
                 "actual_cache_read_tokens": row["actual_usage"]["cache_read_input_tokens"]}
                for row in profile[1:40] if row["new_nested_inclusive_message_blocks"] > 20],
            "documentation_limit": "FACTS section9 and archived R_codex.log were checked offline. They do not preserve an exact definition distinguishing top-level from nested content for lookback, so this is supported by C5 observations, not a newly verified provider guarantee.",
            "proposed_fix_if_top_level_growth_exceeds_lookback": "When creating new results, keep related text/images nested inside their new top-level tool_result blocks. If a complete call/result exchange would still exceed the window, renew after completing every pending pair, accepting one full write. An extra checkpoint request is possible only at a valid complete exchange. Never edit an earlier block or move an earlier marker.",
        },
        "assumptions": [
            "All saved messages, tools, system text, image file IDs and outputs remain the same in the counterfactual 45-request replay. Server clearing is removed. Model behavior may differ in a real rerun.",
            "First40 cacheable token increments and uncached suffixes are observed, not predicted. Requests41–45 use exact native visual patches plus pooled scaled byte/3 text estimates; their actual cleared_input_tokens are never used.",
            "The pooled nonimage ratio includes hidden thinking and wrappers because receipts expose summarized thinking rather than its full retained token content; it cannot guarantee accuracy on every future turn.",
            "The observed uncached suffix is carried unchanged for first40 and assumed2 tokens afterward. No undocumented cache rounding interval is fitted.",
            "70 means70 total billable requests including renewal summaries. With one summary it includes69 productive requests. Productive requests after45 extend the first45 average input growth, image growth, wire-byte growth, output and request-start spacing. Three new top-level blocks per projected request.",
            "The four primary profiles hold the input profile fixed while doubling productive output billing for2x. This isolates the requested output-cost scenario; a real run may retain additional output as later input.",
            "The additional retained_sensitivity profiles also carry every extra output token into the next input until renewal, adding estimated3 UTF-8 bytes per extra token. Tool evidence stays unchanged. These scenarios are assumptions, not an expected cost bound.",
            "Each renewal summary is assumed4000 output tokens with400 new input tokens; the next productive request pays one full write of the specified fresh opening. Future evidence selection is represented by the saved final C5 evidence set.",
            "Observed request-start gaps are retained; projected gaps use their mean. No idle gap reaches one hour; longer pauses would add full cache writes.",
            "No run estimate is a cutoff, reservation, approval or guarantee; the live money display remains settled-only.",
            "C5 server cleared_input_tokens values are inconsistent with recorded prompt sizes (including multi-million reports). They are recorded in the original receipts only and excluded from every calculation.",
        ],
    }


def write_report(run, destination):
    report = build_report(run)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "estimate.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# F1 offline cache calibration and replay", "",
             f"Actual C5: ${report['actual_c5_cost_usd']}; outputs {report['actual_c5_output_tokens']:,}.",
             f"First40: actual ${report['calibration']['actual_cost_usd']}; simulated ${report['calibration']['simulated_cost_usd']}; "
             f"relative error {report['calibration']['relative_error']}; mismatches {report['calibration']['mismatched_requests']}.", "",
             "| Request | Actual read | Sim read | Actual write | Sim write | Actual $ | Sim $ |",
             "|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['request']} | {r['actual_read']} | {r['simulated_read']} | {r['actual_write']} | {r['simulated_write']} | {r['actual_cost_usd']} | {r['simulated_cost_usd']} |"
              for r in report["calibration"]["requests"]]
    lines += ["", "| Scenario | Total $ | Productive / total requests | Renewals |",
              "|---|---:|---:|---|"]
    lines += [f"| {name} | {row['cost_usd']} | {row['productive_requests']} / {row['total_requests_including_renewal_summaries']} | {[r['before_productive_request'] for r in row['renewals']]} |"
              for name, row in report["scenarios"].items()]
    lines += ["", "## 45-request replay", "", "| Request | Actual $ | Replay $ | Read | Write | Input | Images | Bytes |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['request']} | {r['actual_cost_usd']} | {r['simulated_cost_usd']} | {r['cache_read_tokens']} | {r['cache_write_tokens']} | {r['input_tokens']} | {r['image_occurrences']} | {r['payload_bytes']} |"
              for r in report["scenarios"]["45_requests_1x_output"]["requests"]]
    lines += ["", "## Assumptions", "", *["- " + text for text in report["assumptions"]], "",
              "## Lookback", "", json.dumps(report["lookback"], indent=2)]
    (destination / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "work/f1")
    args = parser.parse_args()
    report = write_report(args.run, args.output)
    print(json.dumps({"calibration": {k: v for k, v in report["calibration"].items() if k != "requests"},
                      "scenarios": {k: {field: row[field] for field in ("cost_usd", "output_tokens", "renewals")}
                                    for k, row in report["scenarios"].items()}}, indent=2))


if __name__ == "__main__":
    main()
