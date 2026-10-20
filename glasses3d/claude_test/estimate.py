"""Offline, owner-only planning estimate. Never imported by the paid runner.

The reference is a complete transport-settled g3d.run_budget/1 ledger plus its
conversation-renewal receipt. No SDK, network, credentials or money admission.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, localcontext, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys

from . import pricing


MODEL = "claude-opus-5-5"
OUTPUT_MULTIPLIERS = (
    ("low", Decimal("0.5")), ("central", Decimal("1")), ("high", Decimal("2")),
    ("stress_5x", Decimal("5")), ("stress_10x", Decimal("10")),
)
MILLION = Decimal(1_000_000)


class EstimateError(ValueError):
    """The saved evidence is insufficient or inconsistent; no estimate is made."""


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise EstimateError(f"{label} must be a JSON object")
    return value


def _integer(value, label: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive):
        raise EstimateError(f"{label} must be a {'positive' if positive else 'non-negative'} integer")
    return value


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EstimateError(f"{label} must be a nonempty string")
    return value


def _path(path: Path) -> Path:
    path = Path(path).resolve()
    if path.name.lower() == ".env" or path.name.lower().startswith(".env."):
        raise EstimateError("credential files are not estimator inputs or outputs")
    return path


def _source(path: Path, raw: bytes, selection: str) -> dict:
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "selection": selection}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EstimateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise EstimateError(f"invalid JSON number: {value}")


def _read(path: Path, selection: str) -> tuple[dict, dict]:
    path = _path(path)
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        return _object(value, str(path)), _source(path, raw, selection)
    except (OSError, UnicodeError, ValueError) as exc:
        raise EstimateError(f"Cannot use receipt {path}: {exc}") from exc


def reference_profile(astra_run: Path) -> dict:
    """Read authoritative flattened usage, not reservations or token estimates."""
    run = Path(astra_run).resolve()
    budget, budget_source = _read(run / "budget.json", "rows[*], totals, tariff.model, run_id")
    if budget.get("schema") != "g3d.run_budget/1":
        raise EstimateError("budget.json: expected schema g3d.run_budget/1")
    run_id = _text(budget.get("run_id"), "budget.run_id")
    if _object(budget.get("tariff"), "budget.tariff").get("model") != "gpt-6-astra":
        raise EstimateError("budget.tariff.model must be gpt-6-astra")
    if budget.get("violation") is not None or budget.get("blocked") is not None:
        raise EstimateError("budget.json records a violation or blocked request")
    rows = budget.get("rows")
    if not isinstance(rows, list) or not rows:
        raise EstimateError("budget.rows must contain settled request receipts")
    requests = []
    ids, response_ids = set(), set()
    for index, row in enumerate(rows, 1):
        label = f"budget.rows[{index - 1}]"
        row = _object(row, label)
        if _integer(row.get("sequence"), f"{label}.sequence", positive=True) != index:
            raise EstimateError(f"{label}: missing, duplicate or unordered request sequence")
        if row.get("outcome") != "settled":
            raise EstimateError(f"{label}: expected settled receipt, got {row.get('outcome')!r}")
        if row.get("settled_from") != "transport" or row.get("http_status") != 200:
            raise EstimateError(f"{label}: usage must be settled by the HTTP 200 transport")
        request_id = _text(row.get("request_id"), f"{label}.request_id")
        response_id = _text(row.get("response_id"), f"{label}.response_id")
        if request_id in ids or response_id in response_ids:
            raise EstimateError(f"{label}: duplicate request or response identity")
        ids.add(request_id)
        response_ids.add(response_id)
        counted = _integer(row.get("input_tokens"), f"{label}.input_tokens", positive=True)
        usage = _object(row.get("usage"), f"{label}.usage")
        usage_keys = {"input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens"}
        if usage.keys() != usage_keys:
            raise EstimateError(f"{label}.usage: expected exactly the five settled Astra token counters")
        counts = {
            name: _integer(usage.get(name), f"{label}.usage.{name}")
            for name in ("input_tokens", "cached_tokens", "cache_write_tokens",
                         "output_tokens", "reasoning_tokens")
        }
        if counts["cached_tokens"] + counts["cache_write_tokens"] > counts["input_tokens"]:
            raise EstimateError(f"{label}: cached plus written tokens exceed input tokens")
        if counts["reasoning_tokens"] > counts["output_tokens"]:
            raise EstimateError(f"{label}: reasoning is a subset of output, but exceeds it")
        if counted != counts["input_tokens"]:
            raise EstimateError(f"{label}: counted input disagrees with settled input")
        requests.append({
            "sequence": index, "request_id": request_id, "response_id": response_id,
            "counted_input_tokens": counted, **counts,
            # Cache writes are uncached input too. Reasoning is already in output.
            "uncached_input_tokens": counts["input_tokens"] - counts["cached_tokens"],
            "plain_input_tokens": counts["input_tokens"] - counts["cached_tokens"] - counts["cache_write_tokens"],
            "settled_micro_usd": _integer(row.get("settled_micro_usd"), f"{label}.settled_micro_usd"),
            "usage_source": f"budget.json#/rows/{index - 1}/usage",
        })
    totals = _object(budget.get("totals"), "budget.totals")
    settled = sum(row["settled_micro_usd"] for row in requests)
    if _integer(totals.get("requests"), "budget.totals.requests") != len(requests):
        raise EstimateError("budget.totals.requests disagrees with request receipts")
    if _integer(totals.get("settled_micro_usd"), "budget.totals.settled_micro_usd") != settled:
        raise EstimateError("budget.totals.settled_micro_usd disagrees with request receipts")
    for key in ("held_micro_usd", "unknown_micro_usd"):
        if _integer(totals.get(key), f"budget.totals.{key}") != 0:
            raise EstimateError(f"budget.totals.{key} must be zero")
    outcomes = _object(totals.get("outcomes"), "budget.totals.outcomes")
    if _integer(outcomes.get("settled"), "budget.totals.outcomes.settled") != len(requests) or any(
        _integer(value, f"budget.totals.outcomes.{key}") != 0
        for key, value in outcomes.items() if key != "settled"
    ):
        raise EstimateError("budget.totals.outcomes disagrees with settled receipts")

    renewals_doc, renewals_source = _read(run / "renewals.json", "renewals[*], excluding summary text and images")
    if renewals_doc.get("schema") != "g3d.conversation_renewals/1" or not isinstance(
        renewals_doc.get("renewals"), list
    ):
        raise EstimateError("renewals.json: expected g3d.conversation_renewals/1 with a renewals array")
    renewals = []
    previous_summary = 0
    previous_conversation = None
    for index, renewal in enumerate(renewals_doc["renewals"], 1):
        label = f"renewals[{index - 1}]"
        renewal = _object(renewal, label)
        if (renewal.get("schema") != "g3d.conversation_renewal/1"
                or renewal.get("run_id") != run_id
                or _integer(renewal.get("number"), f"{label}.number", positive=True) != index):
            raise EstimateError(f"{label}: schema, run or number disagrees with reference run")
        old = _text(renewal.get("old_conversation_id"), f"{label}.old_conversation_id")
        new = _text(renewal.get("new_conversation_id"), f"{label}.new_conversation_id")
        if old == new or (previous_conversation is not None and old != previous_conversation):
            raise EstimateError(f"{label}: broken conversation renewal chain")
        summary = _integer(renewal.get("summary_request_sequence"), f"{label}.summary_request_sequence",
                           positive=True)
        if not previous_summary < summary < len(requests) or renewal.get("summary_skipped") is not None:
            raise EstimateError(f"{label}: missing or invalid settled summary request")
        measurements = _object(renewal.get("measurements"), f"{label}.measurements")
        measurements = {
            name: _integer(measurements.get(name), f"{label}.measurements.{name}")
            for name in ("input_tokens", "images", "payload_bytes")
        }
        renewals.append({
            "number": index, "old_conversation_id": old, "new_conversation_id": new,
            "summary_request_sequence": summary, "first_request_after_renewal": summary + 1,
            "measurements": measurements,
            "source": f"renewals.json#/renewals/{index - 1}",
        })
        previous_summary, previous_conversation = summary, new
    token_names = ("input_tokens", "uncached_input_tokens", "plain_input_tokens", "cached_tokens",
                   "cache_write_tokens", "output_tokens", "reasoning_tokens")
    return {
        "run_id": run_id, "run_directory": str(run), "model": "gpt-6-astra",
        "request_count": len(requests), "requests": requests,
        "totals": {name: sum(row[name] for row in requests) for name in token_names},
        "settled_usd": str(Decimal(settled) / MILLION),
        "opening_counted_input_tokens": requests[0]["counted_input_tokens"],
        "opening_count_source": "budget.json#/rows/0/input_tokens",
        "renewal_count": len(renewals), "renewals": renewals,
        "sources": {"astra_budget": budget_source, "astra_renewals": renewals_source},
    }


def _opening(livecheck: Path | None, claude_opening: int | None) -> tuple[int, dict, bool]:
    if (livecheck is None) == (claude_opening is None):
        raise EstimateError("provide exactly one of --livecheck or --claude-opening")
    if claude_opening is not None:
        count = _integer(claude_opening, "--claude-opening", positive=True)
        return count, {"origin": "command_line", "argument": "--claude-opening", "value": count}, True
    receipt, source = _read(livecheck, "count_response.input_tokens, input_tokens, model, settings.effort")
    if (receipt.get("schema") != "g3d.claude_livecheck/1"
            or receipt.get("status") != "completed"
            or receipt.get("provider") != "anthropic" or receipt.get("model") != MODEL):
        raise EstimateError("livecheck must be a completed g3d.claude_livecheck/1 for anthropic/claude-opus-5-5")
    if _object(receipt.get("settings"), "livecheck.settings").get("effort") != "max":
        raise EstimateError("livecheck.settings.effort must be max")
    if type(receipt.get("offline")) is not bool:
        raise EstimateError("livecheck.offline must state whether the count is real or scripted")
    counted = _object(receipt.get("count_response"), "livecheck.count_response")
    count = _integer(counted.get("input_tokens"), "livecheck.count_response.input_tokens", positive=True)
    if _integer(receipt.get("input_tokens"), "livecheck.input_tokens", positive=True) != count:
        raise EstimateError("livecheck input_tokens disagrees with count_response.input_tokens")
    return count, source, receipt["offline"]


def _usd(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f")


def build_estimate(astra_run: Path, *, livecheck: Path | None = None,
                   claude_opening: int | None = None) -> dict:
    """Return a JSON-ready estimate; an override is always marked placeholder."""
    profile = reference_profile(astra_run)
    opening, opening_source, placeholder = _opening(livecheck, claude_opening)
    tariff = pricing.TARIFFS[MODEL]
    sources = profile.pop("sources")
    if livecheck is not None:
        sources["claude_livecheck"] = opening_source
    for role, path in (("pricing", Path(pricing.__file__)), ("estimator", Path(__file__))):
        path = _path(path)
        sources[role] = _source(path, path.read_bytes(), "TARIFFS[claude-opus-5-5]" if role == "pricing"
                                else "reference parsing, formula and fixed output multipliers")
    with localcontext() as context:
        context.prec = 50
        ratio = Decimal(opening) / Decimal(profile["opening_counted_input_tokens"])
        write_tokens = Decimal(profile["totals"]["uncached_input_tokens"]) * ratio
        read_tokens = Decimal(profile["totals"]["cached_tokens"]) * ratio
        ends = [row["summary_request_sequence"] for row in profile["renewals"]]
        ends.append(profile["request_count"])
        end_sequences = set(ends)
        previous_output = 0
        history_reads = 0
        for row in profile["requests"]:
            # C4's planning convention writes each output delta once and reads
            # it on every later request in this conversation. Even a final
            # output gets a write, but never a read beyond its renewal boundary.
            row["output_history_basis"] = {
                "cache_write_1h": row["output_tokens"], "cache_read": previous_output,
            }
            history_reads += previous_output
            previous_output += row["output_tokens"]
            if row["sequence"] in end_sequences:
                previous_output = 0
        history_basis = {
            "cache_write_1h": profile["totals"]["output_tokens"],
            "cache_read": history_reads, "conversation_end_sequences": ends,
        }
        scenarios = {}
        for name, k in OUTPUT_MULTIPLIERS:
            output = Decimal(profile["totals"]["output_tokens"]) * k
            output_usd = output * tariff.output / MILLION
            write_delta = (k - 1) * history_basis["cache_write_1h"]
            read_delta = (k - 1) * history_basis["cache_read"]
            scenario_writes = write_tokens + write_delta
            scenario_reads = read_tokens + read_delta
            write_usd = scenario_writes * tariff.cache_write_1h / MILLION
            read_usd = scenario_reads * tariff.cache_read / MILLION
            scenarios[name] = {
                "output_multiplier": str(k), "output_tokens": str(output),
                "history_token_delta": {
                    "cache_write_1h": str(write_delta), "cache_read": str(read_delta),
                },
                "input_tokens": {
                    "cache_write_1h": str(scenario_writes), "cache_read": str(scenario_reads),
                },
                "cache_write_1h_usd": _usd(write_usd), "cache_read_usd": _usd(read_usd),
                "output_usd": _usd(output_usd), "total_usd": _usd(write_usd + read_usd + output_usd),
            }
        for row in profile["requests"]:
            fixed = (Decimal(row["uncached_input_tokens"]) * ratio * tariff.cache_write_1h
                     + Decimal(row["cached_tokens"]) * ratio * tariff.cache_read)
            history_cost = (
                Decimal(row["output_history_basis"]["cache_write_1h"]) * tariff.cache_write_1h
                + Decimal(row["output_history_basis"]["cache_read"]) * tariff.cache_read
            )
            row["estimated_claude_usd"] = {
                name: _usd((fixed + (k - 1) * history_cost
                            + Decimal(row["output_tokens"]) * k * tariff.output) / MILLION)
                for name, k in OUTPUT_MULTIPLIERS
            }
        result = {
            "schema": "g3d.claude_cost_estimate/1",
            "kind": "placeholder_not_estimate" if placeholder else "owner_planning_estimate",
            "placeholder": placeholder, "model": MODEL, "effort": "max", "currency": "USD",
            **{f"{name}_usd": scenario["total_usd"] for name, scenario in scenarios.items()},
            "astra_profile": profile,
            "claude_opening_count": opening, "claude_opening_source": opening_source,
            "input_ratio": {"numerator": opening, "denominator": profile["opening_counted_input_tokens"],
                            "decimal": str(ratio)},
            "scaled_input_tokens": {"cache_write_1h": str(write_tokens), "cache_read": str(read_tokens)},
            "output_history_basis": history_basis,
            "tariff_usd_per_million": {
                "cache_write_1h": str(tariff.cache_write_1h), "cache_read": str(tariff.cache_read),
                "output": str(tariff.output), "source_url": tariff.source_url,
                "checked": tariff.checked.isoformat(),
            },
            "scenarios": scenarios, "sources": sources,
            "assumptions": [
                "This is owner-only planning; it is never sent to the model or used by the runner for money decisions.",
                "The owner's settled-only cutoff remains the sole spending stop; this report grants no paid-run approval.",
                "Replay the same request count and conversation renewals as the accepted Astra max-effort rerun.",
                "Scale every request's uncached and cached input by Claude opening count divided by Astra opening counted input.",
                "Astra uncached input is total input minus cache reads, including both plain input and cache writes.",
                "Price all scaled uncached input as one-hour cache writes and all scaled cached input as cache reads.",
                "Retain Astra's observed cache partition; different cache expiry, hits or renewal timing are not predicted.",
                "Astra output already includes reasoning; never add the reasoning subset a second time.",
                "Scale output by 0.5, 1 and 2 for low, central and high, plus 5 and 10 for stress scenarios; "
                "Claude's max-effort thinking volume is unknown.",
                "For each request i, add (k-1)*output_i to history at the one-hour cache-write rate, "
                "then charge cache reads on every strictly later request in the same conversation.",
                "This planning convention charges one write even for a conversation's final output, "
                "with no later read; it is not a simulation of the API's cache-write timing.",
                "The renewal summary request ends the old conversation; discard its history deltas before the next request.",
                "Apply history deltas without the opening-count ratio; k<1 subtracts the same writes and reads without clamping.",
                "Very small synthetic opening ratios can make reduced-input scenarios negative; these signed linear "
                "scenarios are not predictions of actual negative charges.",
                "Ignore context-editing savings. This leans conservative but is not a guaranteed upper bound.",
                "Renewal summary replies and cache rebuilds are already in the settled request profile; do not add them again.",
                "Use pricing.TARIFFS for Opus 5.5, standard speed and global inference, with no long-context price step.",
                "Use 50-digit Decimal arithmetic without rounding scaled tokens; USD strings round to six decimals, half up.",
                "Run totals are rounded after summing, so sums of rounded per-request amounts may differ by microdollars.",
                "Low and high are scenarios, not statistical bounds; one Astra run cannot predict Claude's work or quality.",
            ],
        }
    if placeholder:
        result["assumptions"].append(
            "PLACEHOLDER ONLY, NOT THE ESTIMATE: the Claude opening count is an override or scripted livecheck, not a real count."
        )
    return result


def summary(result: dict) -> str:
    """Exactly six plain lines, including an unmistakable placeholder label."""
    profile = result["astra_profile"]
    title = "PLACEHOLDER ONLY - NOT THE ESTIMATE" if result["placeholder"] else "Owner-only planning estimate"
    return "\n".join((
        f"{title}: {MODEL}, max effort; {profile['request_count']} requests, "
        f"{profile['renewal_count']} renewals; opening Claude {result['claude_opening_count']} / "
        f"Astra {profile['opening_counted_input_tokens']} "
        f"(ratio {Decimal(result['input_ratio']['decimal']):.6f}); "
        "context-editing savings ignored; settled-only cutoff unchanged.",
        *(f"{name} (k={scenario['output_multiplier']}): ${scenario['total_usd']}; "
          f"1h writes ${scenario['cache_write_1h_usd']}, reads ${scenario['cache_read_usd']}, "
          f"output including thinking ${scenario['output_usd']}."
          for name, scenario in result["scenarios"].items()),
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--astra-run", type=Path, required=True)
    count = parser.add_mutually_exclusive_group(required=True)
    count.add_argument("--livecheck", type=Path, help="Completed livecheck.json with its count_response")
    count.add_argument("--claude-opening", type=int, help="Test/placeholder only; never a real estimate")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_estimate(args.astra_run, livecheck=args.livecheck, claude_opening=args.claude_opening)
        out = _path(args.out)
        if out.suffix.lower() != ".json":
            raise EstimateError("--out must be a JSON file")
        protected = {Path(source["path"]) for source in result["sources"].values()}
        if out in protected or out.is_relative_to(Path(args.astra_run).resolve()):
            raise EstimateError("--out must not overwrite reference evidence or source files")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        print(summary(result))
    except (EstimateError, OSError) as exc:
        print(f"Estimate refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
