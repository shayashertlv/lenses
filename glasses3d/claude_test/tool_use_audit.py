"""Deterministic, offline tool-use audit over saved run receipts.

No model summaries, script-text guesses, network requests, or live scene access
are used. A script changes the scene iff its journal fingerprints differ.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from PIL import Image, ImageChops


COMPARISONS = frozenset({"compare_to_photos", "compare_look_to_photos"})
MEASUREMENTS = frozenset({"photo_read", "pick_surface_point"})
SCRIPT = "execute_blender_code"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if path.exists() else []


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _texts(result: Any) -> list[str]:
    return [item["text"] for item in result if isinstance(item, dict)
            and item.get("type") == "text"] if isinstance(result, list) else []


def _report(result: Any) -> dict:
    for text in _texts(result):
        try:
            value, _ = json.JSONDecoder().raw_decode(text.lstrip())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _true_truncations(value: Any, prefix: str = "") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}/{key}"
            if "truncat" in key.lower() and item is True:
                found.append(name)
            found.extend(_true_truncations(item, name))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_true_truncations(item, f"{prefix}/{index}"))
    return found


def audit_run(run_dir: Path) -> dict:
    """Count every invocation receipt and derive mutation streaks from journals."""
    run_dir = Path(run_dir)
    calls, seen = [], set()
    folders = sorted(path for path in (run_dir / "tools").iterdir() if path.is_dir())
    for folder in folders:
        path = folder / "arguments.json"
        if not path.is_file():
            raise ValueError(f"tool directory lacks invocation receipt: {folder}")
        arguments = _json(path)
        cid, name, number = arguments["call_id"], arguments["tool"], arguments["n"]
        if cid in seen:
            raise ValueError(f"duplicate tool call receipt: {cid}")
        seen.add(cid)
        result_path, journal_path = path.parent / "result.json", path.parent / "journal.json"
        result = _json(result_path) if result_path.exists() else {}
        journal = _json(journal_path) if journal_path.exists() else {}
        for record in (result, journal):
            if record and (record.get("call_id") != cid or record.get("tool") != name):
                raise ValueError(f"receipt identity mismatch: {path.parent}")
        changed = None
        if name == SCRIPT:
            before, after = journal.get("fingerprint_before"), journal.get("fingerprint_after")
            if isinstance(before, str) and isinstance(after, str):
                changed = before != after
            # Do not silently substitute checkpoint IDs or a script-text guess.
        report = _report(result.get("result"))
        calls.append({
            "n": number, "call_id": cid, "tool": name,
            "arguments_path": str(path), "arguments_sha256": _digest(path),
            "result_path": str(result_path) if result else None,
            "result_sha256": _digest(result_path) if result else None,
            "journal_path": str(journal_path) if journal else None,
            "journal_sha256": _digest(journal_path) if journal else None,
            "status": report.get("status", journal.get("status")),
            "code": report.get("code"),
            "scene_changed": changed,
            "fingerprint_before": journal.get("fingerprint_before"),
            "fingerprint_after": journal.get("fingerprint_after"),
            "journal_mutating": journal.get("mutating"),
            "truncations": _true_truncations(report),
            "paged_report": report.get("paged") is True,
            "raw_image_count": sum(item.get("type") == "image"
                                   for item in result.get("result", [])
                                   if isinstance(item, dict)),
        })
    calls.sort(key=lambda call: call["n"])
    if not calls:
        raise ValueError(f"no invocation receipts: {run_dir}")
    if len({call["n"] for call in calls}) != len(calls):
        raise ValueError("duplicate tool receipt sequence")
    counts = Counter(call["tool"] for call in calls)
    current, longest, segments = [], [], []
    for call in calls:
        if call["tool"] == SCRIPT and call["scene_changed"] is True:
            current.append(call["n"])
            if len(current) > len(longest):
                longest = list(current)
        if call["tool"] in COMPARISONS and call["status"] == "ok":
            segments.append({"script_calls": current, "comparison_call": call["n"]})
            current = []
    segments.append({"script_calls": current, "comparison_call": None})
    unknown = [call["n"] for call in calls
               if call["tool"] == SCRIPT and call["scene_changed"] is None]
    starts = [row for row in _jsonl(run_dir / "events.jsonl") if row.get("event") == "tool_started"]
    receipt_identities = {(call["call_id"], call["tool"]) for call in calls}
    events_match = (len(starts) == len(calls)
                    and {(row["tool_call_id"], row["name"]) for row in starts} == receipt_identities
                    if starts else None)
    return {
        "run_dir": str(run_dir),
        "total_calls": len(calls),
        "tool_started_event_count": len(starts) if starts else None,
        "tool_started_events_match_receipts": events_match,
        "per_tool_counts": dict(sorted(counts.items())),
        "dedicated_calls": len(calls) - counts[SCRIPT],
        "dedicated_tool_share": (len(calls) - counts[SCRIPT]) / len(calls),
        "scene_changing_scripts": sum(call["scene_changed"] is True for call in calls),
        "nonchanging_scripts": sum(call["scene_changed"] is False for call in calls),
        "mutation_unknown_script_calls": unknown,
        "longest_no_comparison_streak": len(longest) if not unknown else None,
        "longest_known_no_comparison_streak": len(longest),
        "longest_streak_script_calls": longest,
        "comparison_segments": segments,
        "photo_measurements": sum(counts[name] for name in MEASUREMENTS),
        "photo_measurements_by_tool": {name: counts[name] for name in sorted(MEASUREMENTS)},
        "calls": calls,
    }


def _tool_results(request: dict) -> list[dict]:
    return [block for message in request.get("messages", [])
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"]


def _verify_image_group(records: list[dict]) -> list[str]:
    """Authenticate retained bytes, native dimensions, coverage and pixel equality."""
    first, errors = records[0], []
    source_path = Path(first["source_path"])
    if not source_path.is_file():
        return ["missing retained source bytes"]
    if _digest(source_path) != first["source_sha256"]:
        return ["retained source SHA-256 mismatch"]
    with Image.open(source_path) as image:
        source = image.convert("RGBA")
    if list(source.size) != first["source_size_px"]:
        errors.append("source dimensions mismatch")
    covered = Image.new("1", source.size)
    for row in records:
        tile_path = Path(row["tile_path"])
        if not tile_path.is_file():
            errors.append(f"missing tile {row['tile_sha256']}")
            continue
        if _digest(tile_path) != row["tile_sha256"]:
            errors.append(f"tile SHA-256 mismatch {row['tile_sha256']}")
            continue
        with Image.open(tile_path) as image:
            piece = image.convert("RGBA")
        box = (row["x0"], row["y0"], row["x1"], row["y1"])
        if (piece.size != (row["w"], row["h"])
                or piece.size != (box[2] - box[0], box[3] - box[1])):
            errors.append("tile dimensions mismatch")
            continue
        if (box[0] < 0 or box[1] < 0
                or box[2] > source.width or box[3] > source.height):
            errors.append("tile is outside source")
            continue
        if piece.tobytes() != source.crop(box).tobytes():
            errors.append("tile pixels differ from native source")
        covered.paste(1, box)
    if ImageChops.invert(covered.convert("L")).getbbox() is not None:
        errors.append("source pixels are not covered by tiles")
    return errors


def audit_claude_adapter(run_dir: Path, counted: dict | None = None) -> dict:
    """Check saved original schemas, emitted results, and retained image receipts."""
    run_dir = Path(run_dir)
    counted = counted or audit_run(run_dir)
    by_id = {call["call_id"]: call for call in counted["calls"]}
    requests = [(path, _json(path)) for path in sorted((run_dir / "requests").glob("*-request.json"))]
    responses = [(path, _json(path)) for path in sorted((run_dir / "requests").glob("*-response.json"))]
    if not requests or not responses:
        raise ValueError("Claude request and response receipts are required")
    definitions = {tool["name"]: tool["input_schema"] for tool in requests[0][1]["tools"]}
    schemas = {name: Draft202012Validator(schema) for name, schema in definitions.items()}
    wire_calls, schema_errors, schema_drift, results, result_drift = [], [], [], {}, []
    for path, request in requests:
        if {tool["name"]: tool["input_schema"] for tool in request["tools"]} != definitions:
            schema_drift.append(str(path))
        for result in _tool_results(request):
            cid = result["tool_use_id"]
            if cid in results and results[cid] != result:
                result_drift.append({"call_id": cid, "request": str(path)})
            results[cid] = result
    for path, response in responses:
        for block in response.get("content", []):
            if block.get("type") != "tool_use":
                continue
            wire_calls.append({"call_id": block["id"], "tool": block["name"]})
            validator = schemas.get(block["name"])
            if validator is None:
                schema_errors.append({"call_id": block["id"], "message": "unknown tool"})
                continue
            for error in validator.iter_errors(block["input"]):
                schema_errors.append({"call_id": block["id"], "message": error.message,
                                      "pointer": "/".join(map(str, error.absolute_path))})
    manifest = _jsonl(run_dir / "image-manifest.jsonl")
    image_groups = defaultdict(list)
    for row in manifest:
        image_groups[row["call_id"], row["output_index"]].append(row)
    image_errors, raw_image_groups = [], 0
    for call in counted["calls"]:
        result = _json(Path(call["result_path"]))["result"] if call["result_path"] else []
        emitted = []
        for index, item in enumerate(result):
            if not isinstance(item, dict) or item.get("type") != "image":
                continue
            raw_image_groups += 1
            records = image_groups.pop((call["call_id"], index), [])
            if not records:
                image_errors.append({"call_id": call["call_id"], "output_index": index,
                                     "problem": "raw image has no adapter tile records"})
                continue
            source_meta = item.get("image_url", {})
            if (source_meta.get("sha256") != records[0]["source_sha256"]
                    or source_meta.get("bytes") != records[0]["source_bytes"]):
                image_errors.append({"call_id": call["call_id"], "output_index": index,
                                     "problem": "raw image and adapter source receipt disagree"})
            for problem in _verify_image_group(records):
                image_errors.append({"call_id": call["call_id"], "output_index": index,
                                     "problem": problem})
            emitted.extend(row["file_id"] for row in records)
        forwarded = results.get(call["call_id"])
        if forwarded is not None:
            sent = [block["source"]["file_id"] for block in forwarded.get("content", [])
                    if isinstance(block, dict) and block.get("type") == "image"]
            if sent != emitted:
                image_errors.append({"call_id": call["call_id"],
                                     "problem": "forwarded image IDs differ from emitted tile order"})
            # The adapter may append labels/host notes, but cannot remove raw text.
            text_iter = iter(block.get("text") for block in forwarded.get("content", [])
                             if isinstance(block, dict) and block.get("type") == "text")
            for text in _texts(result):
                if not any(candidate == text for candidate in text_iter):
                    image_errors.append({"call_id": call["call_id"],
                                         "problem": "raw result text missing or altered"})
                    break
    for (cid, index), _ in image_groups.items():
        image_errors.append({"call_id": cid, "output_index": index,
                             "problem": "orphan adapter image records"})
    terminal = [call["call_id"] for call in counted["calls"]
                if call["tool"] == "finish" and call["status"] == "accepted"]
    missing_results = [cid for cid in by_id if cid not in results and cid not in terminal]
    wire_map = {call["call_id"]: call["tool"] for call in wire_calls}
    receipt_map = {call["call_id"]: call["tool"] for call in counted["calls"]}
    refused, failed_checks = [], []
    for call in counted["calls"]:
        report = _report(_json(Path(call["result_path"]))["result"]) if call["result_path"] else {}
        if isinstance(report.get("result"), dict) and report["result"].get("ok") is False:
            failed_checks.append({"n": call["n"], "call_id": call["call_id"], "tool": call["tool"],
                                  "reasons": report["result"].get("reasons", [])})
        if call["status"] == "refused":
            refused.append({"n": call["n"], "call_id": call["call_id"], "tool": call["tool"],
                            "code": call["code"], "message": report.get("message"),
                            "violations": report.get("violations", [])})
    failures = [row for row in _jsonl(run_dir / "events.jsonl")
                if row.get("event") == "tool_output_failed"]
    return {
        "request_count": len(requests),
        "response_count": len(responses),
        "wire_tool_calls": len(wire_calls),
        "wire_calls_match_tool_receipts": wire_map == receipt_map and len(wire_calls) == len(by_id),
        "forwarded_tool_results": len(results),
        "schema_validation_failures": schema_errors,
        "schema_drift": schema_drift,
        "is_error_results": [{"call_id": cid, "tool": by_id.get(cid, {}).get("tool")}
                             for cid, result in results.items() if result.get("is_error")],
        "tool_output_failed_events": failures,
        "refused_tools": refused,
        "reported_failed_checks": failed_checks,
        "missing_forwarded_results": missing_results,
        "terminal_accepted_results_not_forwarded": [cid for cid in terminal if cid not in results],
        "changed_forwarded_results": result_drift,
        "truncated_results": [{"n": call["n"], "paths": call["truncations"]}
                              for call in counted["calls"] if call["truncations"]],
        "paged_report_calls": [call["n"] for call in counted["calls"] if call["paged_report"]],
        "retained_raw_images_verified": raw_image_groups,
        "adapter_tile_records_verified": len(manifest),
        "forwarded_tool_image_occurrences": sum(
            block.get("type") == "image" for result in results.values()
            for block in result.get("content", []) if isinstance(block, dict)),
        "image_or_text_integrity_errors": image_errors,
        "host_notes_sent": sum(
            isinstance(block, dict) and str(block.get("text", "")).startswith("Host:")
            for result in results.values() for block in result.get("content", [])),
        "interpretation": (
            "Accepted terminal finish output is retained locally but needs no next model request. "
            "Paged reports are explicit shared-tool summaries with read_report pointers, not adapter truncation. "
            "Refusal status is reported separately from Anthropic is_error."
        ),
    }


def markdown(report: dict) -> str:
    names = list(report["runs"])
    rows = ["# Receipt tool-use audit", "", "| Tool | " + " | ".join(names) + " |",
            "|---|" + "---:|" * len(names)]
    tools = sorted({tool for run in report["runs"].values() for tool in run["per_tool_counts"]}
                   | COMPARISONS | MEASUREMENTS | {"labelled_model_map"})
    for tool in tools:
        rows.append("| `" + tool + "` | "
                    + " | ".join(str(report["runs"][name]["per_tool_counts"].get(tool, 0))
                                 for name in names) + " |")
    for label, key in (("All calls", "total_calls"), ("Scene-changing scripts", "scene_changing_scripts"),
                       ("Longest no-comparison streak", "longest_no_comparison_streak"),
                       ("Photo measurement calls", "photo_measurements")):
        rows.append("| " + label + " | "
                    + " | ".join(str(report["runs"][name][key]) for name in names) + " |")
    rows.append("| Dedicated share | "
                + " | ".join(f'{report["runs"][name]["dedicated_tool_share"]:.2%}' for name in names) + " |")
    rows.extend(["", "Counts come from each tools/*/arguments.json receipt, including refused calls. "
                 "A scene change requires unequal journal fingerprints. Only a successful explicit "
                 "compare_to_photos or compare_look_to_photos resets the streak; read-only calls, "
                 "rendering and finish-generated comparisons do not. Dedicated share means every "
                 "non-execute_blender_code call divided by all calls.", ""])
    for name, run in report["runs"].items():
        rows.append(f"- {name}: longest streak script numbers {run['longest_streak_script_calls']}; "
                    f"{run['nonchanging_scripts']} unchanged scripts; "
                    f"unknown mutation evidence {run['mutation_unknown_script_calls']}; "
                    f"tool-start events agree with receipts: {run['tool_started_events_match_receipts']}.")
    for name, adapter in report.get("adapter", {}).items():
        rows.extend(["", f"{name} adapter: {adapter['wire_tool_calls']} wire calls agree with "
                     f"tool receipts: {adapter['wire_calls_match_tool_receipts']}. "
                     f"is_error={len(adapter['is_error_results'])} among "
                     f"{adapter['forwarded_tool_results']} forwarded results; "
                     f"schema failures={len(adapter['schema_validation_failures'])}; "
                     f"output failures={len(adapter['tool_output_failed_events'])}; "
                     f"truncated results={len(adapter['truncated_results'])}; "
                     f"missing forwarded results={len(adapter['missing_forwarded_results'])}.",
                     f"Verified {adapter['retained_raw_images_verified']} source-image occurrences / "
                     f"{adapter['adapter_tile_records_verified']} tile records by SHA-256, dimensions, "
                     "full pixel coverage and exact native source pixels; "
                     f"{adapter['forwarded_tool_image_occurrences']} tool-image occurrences forwarded. "
                     f"Image/text integrity errors={len(adapter['image_or_text_integrity_errors'])}. "
                     f"Host notes sent={adapter['host_notes_sent']}.",
                     f"Paged report calls: {adapter['paged_report_calls']}. {adapter['interpretation']}"])
        for refusal in adapter["refused_tools"]:
            details = refusal["message"] or "; ".join(v.get("message", "") for v in refusal["violations"])
            rows.append(f"- Tool #{refusal['n']} {refusal['tool']} refused: {details}")
        for failure in adapter["reported_failed_checks"]:
            rows.append(f"- Tool #{failure['n']} {failure['tool']} ran and reported a failed check: "
                        + "; ".join(failure["reasons"])
                        + ". This is tool evidence, not an adapter failure.")
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, metavar="LABEL=RUN_DIR")
    parser.add_argument("--claude-label", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    runs = {}
    for value in args.run:
        label, separator, path = value.partition("=")
        if not separator or not label or label in runs:
            parser.error("--run must have a unique LABEL=RUN_DIR")
        runs[label] = audit_run(Path(path))
    report = {
        "schema": "g3d.claude_tool_use_audit/1",
        "scope": "Offline receipts only; no provider calls and no product quality verdict.",
        "runs": runs,
        "adapter": {label: audit_claude_adapter(Path(runs[label]["run_dir"]), runs[label])
                    for label in args.claude_label},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (args.output / "audit.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(args.output / "audit.json"),
                      "markdown": str(args.output / "audit.md")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
