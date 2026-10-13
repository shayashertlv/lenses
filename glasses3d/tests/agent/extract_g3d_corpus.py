"""Reproduce the M2 token-only fixture from the eight canonical paid ledgers, entirely offline.

Run from the repository root:
    glasses3d/.venv/Scripts/python.exe -B -m glasses3d.tests.agent.extract_g3d_corpus --check
    glasses3d/.venv/Scripts/python.exe -B -m glasses3d.tests.agent.extract_g3d_corpus --write

The fixture contains only integer token counts, integer settled totals, run labels, and source hashes. Never copy
ledger dictionaries: they also contain provider identifiers. Source discovery is the canonical run sequence below,
so review/rehearsal copies cannot multiply one paid run. Every one of the eight runs is retained; none is selected
by fit error. Missing data refuses extraction. The always-available statistical tests use the compact fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from glasses3d.agent import estimate as E
from glasses3d.agent.budget import RunLedger

REPO = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).with_name("data") / "g3d-astra-8-tokens.json"
COLUMNS = ("counted_input_tokens", "max_output_tokens", *E.FLAT_USAGE_KEYS)
CANONICAL_SOURCES = (
    "glasses3d/data/wp6/astra_temple_lab_20261010/real-astra-001/runs/owner-test-001/budget.json",
    "glasses3d/data/wp6v2/lab/package/runs/facet-astra-002/budget.json",
    *(f"glasses3d/data/wp6v2/lab/package_{i:03d}/runs/facet-astra-{i:03d}/budget.json" for i in range(3, 9)),
)


def extract(repo_root=REPO) -> dict:
    runs = []
    for relative in CANONICAL_SOURCES:
        path = Path(repo_root) / relative
        ledger = RunLedger.load(path)  # verifies source arithmetic and settlement evidence before extracting
        config_path = path.with_name("run-config.json")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if (config.get("model") != "gpt-6-astra" or (config.get("client") or {}).get("kind") != "production"
                or config.get("effort") != "max" or config.get("max_output_tokens") != 16_384
                or config.get("service_tier") != "default"):
            raise ValueError(f"canonical paid run configuration differs: {path.parent.name}")
        rows = ledger.rows
        settled = [row for row in rows if row.get("outcome") in ("settled", "reconciled") and row.get("usage")]
        if len(settled) < E.MIN_CORPUS_REQUESTS or len(settled) != len(rows):
            raise ValueError(f"canonical run is not complete with >= {E.MIN_CORPUS_REQUESTS} usages: {path.parent.name}")
        usages = E.ledger_usages(ledger.data)
        shape = E.causal_run_shape(usages)
        total = sum(row["settled_micro_usd"] for row in settled)
        if shape.settled_micro != total:
            raise ValueError(f"recorded settlements differ from the frozen tariff: {path.parent.name}")
        runs.append({
            "run": path.parent.name,
            "source_ledger_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "settled_micro_usd": total,
            "rows": [[row["input_tokens"], row["max_output_tokens"], *(row["usage"][key] for key in E.FLAT_USAGE_KEYS)]
                     for row in settled],
        })
    return {"schema": "g3d.test.paid_corpus_tokens/1", "method": E.G3D_METHOD,
            "about": "Token counts and source hashes only; eight canonical complete paid g3d.run_budget/1 runs, "
                     "production gpt-6-astra, max effort, 16384 output cap, default tier, >=15 settled requests; "
                     "2026-10-10..13. All eight retained. No prompts, photos, file paths, provider IDs or secrets. "
                     "Corpus predates image pruning; no savings assumed. Reproduce with extract_g3d_corpus.py.",
            "columns": list(COLUMNS), "runs": runs}


def render(data: dict) -> str:
    """Compact human-readable evidence: one row per request, keeping the token column order explicit."""
    lines = ["{"]
    for key in ("schema", "method", "about", "columns"):
        lines.append(f" {json.dumps(key)}: {json.dumps(data[key], ensure_ascii=False)},")
    lines.append(' "runs": [')
    for i, run in enumerate(data["runs"]):
        lines.append("  {")
        for key in ("run", "source_ledger_sha256", "source_config_sha256", "settled_micro_usd"):
            lines.append(f"   {json.dumps(key)}: {json.dumps(run[key])},")
        lines.append('   "rows": [')
        lines.extend("    " + json.dumps(row) + ("," if j + 1 < len(run["rows"]) else "")
                     for j, row in enumerate(run["rows"]))
        lines.append("   ]")
        lines.append("  }" + ("," if i + 1 < len(data["runs"]) else ""))
    return "\n".join([*lines, " ]", "}", ""])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    data = extract()
    if args.write:
        FIXTURE.write_text(render(data), encoding="utf-8")
    elif json.loads(FIXTURE.read_text(encoding="utf-8")) != data:
        raise SystemExit("committed token evidence differs from the canonical paid ledgers")
    print(f"8 complete paid runs, {sum(len(run['rows']) for run in data['runs'])} settled requests; "
          f"{sum(run['settled_micro_usd'] for run in data['runs'])} micro-USD verified.")


if __name__ == "__main__":
    main()
