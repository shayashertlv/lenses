"""$0 replay of saved requests through the actual lossless tool-text policy.

All histories, photos, replies and guard arithmetic are fixed. Token deltas are
explicit counterfactual estimates anchored to saved provider input-token counts.
No model client, network or Blender is constructed. Output must be a new directory.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

from agents.tool import ToolOutputText

from glasses3d.agent.history_replay import image_refs, payload_units, restore_append_only
from glasses3d.agent.pricing import Tariff, validate_usage
from glasses3d.agent.tool_reports import TEXT_LIMIT, dumps, limit_output
from glasses3d.agent.tools import build_tools


def run_proof(job, run_id, out):
    job, out = Path(job).resolve(), Path(out).resolve()
    if out.exists() or out.is_relative_to(job) or Path(run_id).name != run_id:
        raise ValueError("use a new proof directory outside the original job")
    source = job / "runs" / run_id
    hashes = {}
    def read(path):
        if not path.resolve().is_relative_to(job) or path.name.endswith(".spec.json") or path.name.startswith(".env"):
            raise ValueError("forbidden source path")
        data = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(data).hexdigest()
        return json.loads(data)
    budget = read(source / "budget.json")
    intake = read(source / "input-receipt.json")
    photos = {item["sha256"] for item in intake["references"]}
    tariff = Tariff.from_dict(budget["tariff"])
    requests, groups, originals, usages = {}, {}, {}, {}
    for row in budget["rows"]:
        n = row["sequence"]
        if row["outcome"] != "settled":
            raise ValueError("proof requires settled source requests")
        request = read(source / f"transport/{n:04d}-request.json")
        response = read(source / f"transport/{n:04d}-response.json")
        if validate_usage(response["usage"]) != row["usage"] or response["id"] != row["response_id"]:
            raise ValueError("usage/response ledger binding differs")
        usages[n] = response["usage"]
        identity = hashlib.sha256(dumps(request["input"][0]).encode()).hexdigest()
        groups.setdefault(identity, []).append(n)
        requests[n] = request
        for item in request["input"]:
            if item.get("type") == "function_call_output":
                originals.setdefault(item["call_id"], item)
    out.mkdir(parents=True)
    transformed, changes = {}, []
    for cid, item in originals.items():
        parts = item["output"]
        sdk = ([ToolOutputText(text=part["text"]) if part.get("type") == "input_text"
                else SimpleNamespace(type="image", source=part) for part in parts] if isinstance(parts, list) else parts)
        limited = limit_output(out / "scratch-job", cid, "saved_tool_output", sdk)
        if isinstance(parts, list):
            replaced = [{"type": "input_text", "text": part.text} if part.type == "text" else deepcopy(part.source)
                        for part in limited]
        else:
            replaced = limited
        target = {**deepcopy(item), "output": replaced}
        transformed[cid] = target
        def chars(value):
            return len(value) if isinstance(value, str) else sum(len(part.get("text", "")) for part in value)
        before, after = chars(parts), chars(replaced)
        if after > TEXT_LIMIT or image_refs(item) != image_refs(target):
            raise AssertionError("policy changed images or exceeded text cap")
        changes.append({"call_id": cid, "before_chars": before, "after_chars": after, "changed": item != target})
    reader = next(tool for tool in build_tools(SimpleNamespace()) if tool.name == "read_report")
    # Count the newly available retrieval schema too; other tool/prompt changes are outside this isolated proof.
    reader_chars = len(dumps({"type": "function", "name": reader.name, "description": reader.description,
                              "parameters": reader.params_json_schema, "strict": True}))
    rebuilt, prefix_edits = {}, []
    for identity, numbers in groups.items():
        chain, edited = restore_append_only([requests[n] for n in numbers], lambda item: deepcopy(transformed[item["call_id"]]))
        if edited:
            prefix_edits.extend(numbers[index-1] for index in edited)
        for n, target in zip(numbers, chain):
            rebuilt[n] = target
            for value in (requests[n], target):
                present = {image["image_url"]["sha256"] for image in image_refs(value)}
                if not photos <= present:
                    raise AssertionError("authoritative photos were lost")
            if image_refs(requests[n]) != image_refs(target):
                raise AssertionError("source image identities or order changed")
    # Image payloads are identical, so image units cancel; no binary images need loading or re-encoding.
    char_delta = {n: payload_units(rebuilt[n], lambda image: (32, 32))[0] -
                     payload_units(requests[n], lambda image: (32, 32))[0] for n in requests}
    by_sequence = {row["sequence"]: row for row in budget["rows"]}
    def scenario(ratio, extra_chars=0):
        rows = []
        for numbers in groups.values():
            previous = 0
            for n in numbers:
                observed = by_sequence[n]
                raw = math.ceil(observed["input_tokens"] + ratio * (char_delta[n] + reader_chars + extra_chars))
                estimated = max(previous, raw)
                if estimated < 0:
                    raise AssertionError("negative counterfactual token estimate")
                rows.append({"sequence": n, "observed_input_tokens": observed["input_tokens"],
                             "estimated_input_tokens": estimated, "estimated_raw_tokens": raw,
                             "monotonic_adjustment": estimated - raw,
                             "estimated_hold_micro_usd": tariff.reserve_micro(estimated, observed["max_output_tokens"])})
                previous = estimated
        rows.sort(key=lambda row: row["sequence"])
        return {"text_tokens_per_character": str(ratio), "extra_header_chars": extra_chars,
                "peak_input_tokens_estimated": max(row["estimated_input_tokens"] for row in rows),
                "largest_hold_micro_usd_estimated": max(row["estimated_hold_micro_usd"] for row in rows),
                "long_context_sequences": [row["sequence"] for row in rows if row["estimated_input_tokens"] > tariff.long_context_input_tokens],
                "rows": rows}
    baseline = scenario(Fraction(21, 50))
    sensitivity = [scenario(Fraction(32, 100)), scenario(Fraction(52, 100)), scenario(Fraction(21, 50), 12000)]
    unchanged = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    observed_cost = sum(tariff.settle_micro(usages[n]) for n in requests)
    if not unchanged or observed_cost != sum(row["settled_micro_usd"] for row in budget["rows"]):
        raise AssertionError("source changed or ledger reprice differs")
    report = {"schema": "g3d.tool_text_replay/1", "source": {"job": str(job), "run_id": run_id},
              "requests": len(requests), "histories": list(groups.values()), "observed_prefix_edits": prefix_edits,
              "replay_prefix_edits": [], "all_photos_every_request": True, "photo_hashes": sorted(photos),
              "every_image_unchanged": True, "actual_proof_cost_usd": "0.000000", "observed_run_cost_micro_usd": observed_cost,
              "observed_peak_input_tokens": max(row["input_tokens"] for row in budget["rows"]),
              "observed_largest_hold_micro_usd": max(row["reserved_micro_usd"] for row in budget["rows"]),
              "tool_text_limit_chars": TEXT_LIMIT, "new_reader_schema_chars": reader_chars, "tool_outputs": changes,
              "baseline": baseline, "sensitivity": sensitivity, "source_hashes": hashes, "sources_unchanged": unchanged,
              "assumptions": ["Offline counterfactual, not exact provider retokenization or a future-run guarantee.",
                "Saved input-token endpoint counts anchor each request; changed text is estimated at 0.42 tokens/character.",
                "Sensitivity uses 0.32 and 0.52 tokens/character and an extra 12000 header characters.",
                "All saved replies, request order, output-token ceilings, images and photo hashes are unchanged.",
                "Each independent parent/child history stays append-only; cumulative maxima and all adjustments are reported.",
                "The new read_report schema is counted; other instruction/schema changes are outside this isolated policy replay.",
                "The unchanged Tariff.reserve_micro prices every counterfactual hold; no cache discount is assumed.",
                "New read_report calls would add context; the saved run did not make those calls."]}
    (out / "proof.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run_proof(args.job, args.run_id, args.out)
    print(json.dumps({key: report[key] for key in ("requests", "histories", "observed_peak_input_tokens",
        "observed_largest_hold_micro_usd", "actual_proof_cost_usd", "sources_unchanged")}, indent=2))
    for key in ("baseline", "sensitivity"):
        print(key, json.dumps([{k: v for k, v in item.items() if k != "rows"} for item in
                              ([report[key]] if key == "baseline" else report[key])], indent=2))


if __name__ == "__main__":
    main()
