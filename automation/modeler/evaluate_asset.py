"""Evaluate one asset of a finished job (a candidate or a same-protocol baseline) under the current evaluator protocol.

    python -m modeler.evaluate_asset prepare --job <dir> (--candidate cNNNN | --baseline NAME) [--force-renders]
    python -m modeler.evaluate_asset collect --job <dir> (--candidate cNNNN | --baseline NAME)
    python -m modeler.evaluate_asset prepare-verdicts [--force-renders]      # every owner-judged asset
    python -m modeler.evaluate_asset collect-verdicts

``prepare`` renders the wearer-proxy AR views (cached), writes the evaluator package under
``<asset>/evaluation_v2/`` (request.json, images/, README.md) and stops: a fresh external agent answers it with
response.json (the ``package`` protocol; read nothing outside the folder). ``collect`` validates response.json into
``evaluation.json`` beside it and prints the automatic verdict a job would derive. Owner verdicts are never copied into
a package. ``modeler.calibration`` compares the collected verdicts with the owner's.

    python -m modeler.evaluate_asset evaluate-verdicts --evaluator astra --astra-cap N --astra-cap-usd D \\
        --astra-ledger <file> --astra-env .env [--astra-usd-ledger <file>] [--evaluation-dir evaluation_v2_astra]

``evaluate-verdicts`` (API evaluator) prepares each owner-judged asset's package and has gpt-6-astra answer it under
the author's client discipline (one reserved call, receipts under ``<evaluation dir>/api*/``, cumulative dollar cap),
collecting into a SEPARATE folder by default so the API evaluator is validated against both the owner's verdicts and
the agent evaluations (``python -m modeler.calibration --evaluation-dir evaluation_v2_astra``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .calibration import EVALUATION_DIR, asset_dir as _asset_dir, automatic_verdict, load_rows, latest_by_asset
from .evaluate import PROTOCOL, validate_evaluation, write_evaluator_package, write_package_files
from .paths import AUTOMATION, JOBS


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else AUTOMATION / p


def asset_glb(job_dir: Path, asset_dir: Path) -> Path:
    if (asset_dir / "model.glb").is_file():
        return asset_dir / "model.glb"
    b = asset_dir / "baseline.json"
    if b.exists():
        return _resolve(_load(b)["glb"])
    raise FileNotFoundError(f"no GLB for {asset_dir}")


def archive_previous_run(out: Path) -> Path:
    """Keep an answered package (request, response, evaluation, transcript audit) under previous/run-N/ before rewriting."""
    prev = out / "previous"
    prev.mkdir(exist_ok=True)
    n = 1 + sum(1 for p in prev.iterdir() if p.is_dir() and p.name.startswith("run-"))
    dst = prev / f"run-{n}"
    dst.mkdir()
    for name in ("request.json", "response.json", "evaluation.json", "README.md", "evaluator_transcript.jsonl", "evaluator_audit.json"):
        if (out / name).exists():
            (out / name).rename(dst / name)
    return dst


def prepare(job_dir: Path, asset_dir: Path, *, force_renders: bool = False, evaluation_dir: str = EVALUATION_DIR) -> Path:
    job_dir, asset_dir = Path(job_dir), Path(asset_dir)
    evidence = _load(job_dir / "evidence" / "evidence.json")
    protocol = _load(job_dir / "evaluation_protocol.json")
    out = asset_dir / evaluation_dir
    out.mkdir(parents=True, exist_ok=True)
    if (out / "response.json").exists():
        archive_previous_run(out)
    glb = asset_glb(job_dir, asset_dir)
    if force_renders:
        cached = asset_dir / "observe" / "ar_wearer" / "archeck.json"
        if cached.exists():
            cached.unlink()
    request, images = write_evaluator_package(job_dir, asset_dir, evidence, protocol["identity_checklist"], out, glb_path=glb)
    request["asset_sha256"] = hashlib.sha256(glb.read_bytes()).hexdigest()
    path = write_package_files(out, request, images)
    (out / "images.json").write_text(json.dumps(images, indent=1), encoding="utf-8")
    return path


def collect(job_dir: Path, asset_dir: Path, *, driver: str = "package", evaluation_dir: str = EVALUATION_DIR,
            evaluation: dict | None = None, meta: dict | None = None) -> dict:
    """Validate the answer (response.json from an external agent, or the evaluation an API driver returned) into
    evaluation.json beside the package."""
    asset_dir = Path(asset_dir)
    out = asset_dir / evaluation_dir
    if evaluation is None:
        resp = out / "response.json"
        if not resp.exists():
            raise FileNotFoundError(resp)
        evaluation = _load(resp)
    ev = validate_evaluation(evaluation)
    req = _load(out / "request.json")
    record = {"evaluation": ev, "meta": dict(meta or {}, driver=driver, protocol=PROTOCOL,
                                             collected=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                                             images=[im["file"] for im in req.get("images", [])]),
              "asset_sha256": req.get("asset_sha256"), "asset_dir": str(asset_dir)}
    (out / "evaluation.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return record


def evaluate_with_driver(job_dir: Path, asset_dir: Path, driver, *, evaluation_dir: str, force_renders: bool = False, log=print) -> dict:
    """Prepare the package and have an API evaluator driver (``author_astra.AstraEvaluatorDriver``) answer it under
    the author's client discipline (one reserved call, receipts under ``<evaluation dir>/api*/``, dollar cap); the
    answer is collected like an agent's, with the call's usage in ``meta``."""
    prepare(job_dir, asset_dir, force_renders=force_renders, evaluation_dir=evaluation_dir)
    out = Path(asset_dir) / evaluation_dir
    request = _load(out / "request.json")
    images = _load(out / "images.json")
    evaluation, meta = driver.decide(out, request, images, role="evaluator", schema_check=validate_evaluation, log=log)
    (out / "response.json").write_text(json.dumps(evaluation, indent=1), encoding="utf-8")
    return collect(job_dir, asset_dir, driver=getattr(driver, "name", "api"), evaluation_dir=evaluation_dir, evaluation=evaluation, meta=meta)


def _select(args) -> tuple[Path, Path]:
    job = Path(args.job)
    if args.candidate:
        return job, job / "candidates" / args.candidate
    if args.baseline:
        return job, job / "baselines" / args.baseline
    raise SystemExit("--candidate or --baseline is required")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("prepare", "collect", "prepare-verdicts", "collect-verdicts", "evaluate", "evaluate-verdicts"))
    ap.add_argument("--job", type=Path)
    ap.add_argument("--candidate")
    ap.add_argument("--baseline")
    ap.add_argument("--force-renders", action="store_true")
    ap.add_argument("--evaluation-dir", default=None, help="evaluation folder under the asset (default evaluation_v2; with the astra evaluator: evaluation_v2_astra)")
    ap.add_argument("--evaluator", choices=("astra",), default="astra", help="API evaluator for evaluate / evaluate-verdicts")
    ap.add_argument("--astra-cap", type=int, help="astra: maximum paid calls for this authorization (1..10); required")
    ap.add_argument("--astra-ledger", type=Path, help="astra: call ledger file (outside any job folder); required")
    ap.add_argument("--astra-env", type=Path, help="astra: dotenv file holding the credential (never printed)")
    ap.add_argument("--astra-api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--astra-effort", default="high", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--astra-cap-usd", type=float, help="astra: cumulative dollar cap of the dollar ledger; required")
    ap.add_argument("--astra-usd-ledger", type=Path, help="astra: dollar ledger shared across call ledgers (default <ledger>.usd.json)")
    args = ap.parse_args(argv)
    evaluation_dir = args.evaluation_dir or EVALUATION_DIR

    def astra_driver():
        import os
        from . import author_astra
        if not args.astra_cap or not args.astra_ledger or not args.astra_cap_usd:
            ap.error("the astra evaluator needs --astra-cap (1..10), --astra-cap-usd and --astra-ledger; no paid call without a cap")
        secret = None
        if args.astra_env:
            from dotenv import dotenv_values
            secret = dotenv_values(args.astra_env).get(args.astra_api_key_env)
        secret = secret or os.environ.get(args.astra_api_key_env)
        if not secret:
            ap.error(f"no credential in the explicit source ({args.astra_api_key_env})")
        return author_astra.AstraEvaluatorDriver(secret, budget_path=args.astra_ledger, maximum_calls=args.astra_cap, cap_usd=args.astra_cap_usd,
                                                 reasoning_effort=args.astra_effort, usd_ledger_path=args.astra_usd_ledger)

    if args.command == "prepare":
        job, d = _select(args)
        print(prepare(job, d, force_renders=args.force_renders, evaluation_dir=evaluation_dir))
    elif args.command == "evaluate":
        job, d = _select(args)
        rec = evaluate_with_driver(job, d, astra_driver(), evaluation_dir=args.evaluation_dir or "evaluation_v2_astra", force_renders=args.force_renders)
        print(json.dumps({"overall": rec["evaluation"]["overall"], "majors": sum(1 for x in rec["evaluation"]["discrepancies"] if x["severity"] == "major"),
                          "resemblance": rec["evaluation"]["resemblance_0_10"], "usage": rec["meta"].get("usage")}, indent=1))
    elif args.command == "evaluate-verdicts":
        driver = astra_driver()
        edir = args.evaluation_dir or "evaluation_v2_astra"
        for r in latest_by_asset(load_rows()):
            d = _asset_dir(r)
            label = f"{r['product_id']} {r.get('candidate') or r.get('baseline')}"
            if (d / edir / "evaluation.json").exists():
                print(label, "already evaluated under", edir)
                continue
            try:
                rec = evaluate_with_driver(JOBS / r["job"], d, driver, evaluation_dir=edir, force_renders=args.force_renders)
            except Exception as e:  # noqa: BLE001 - a refused or failed call stops the batch; the rest stay unevaluated
                print(label, "FAILED:", f"{type(e).__name__}: {e}")
                break
            a = automatic_verdict(r, evaluation_dir=edir)
            print(label, "owner", r["verdict"], "| astra", rec["evaluation"]["overall"], "majors",
                  sum(1 for x in rec["evaluation"]["discrepancies"] if x["severity"] == "major"), "| automatic", a and a["verdict"])
    elif args.command == "collect":
        job, d = _select(args)
        rec = collect(job, d, evaluation_dir=evaluation_dir)
        print(json.dumps({"overall": rec["evaluation"]["overall"], "majors": sum(1 for x in rec["evaluation"]["discrepancies"] if x["severity"] == "major"),
                          "resemblance": rec["evaluation"]["resemblance_0_10"]}, indent=1))
    else:
        rows = latest_by_asset(load_rows())
        for r in rows:
            d = _asset_dir(r)
            job = JOBS / r["job"]
            if args.command == "prepare-verdicts":
                print(r["product_id"], r.get("candidate") or r.get("baseline"), prepare(job, d, force_renders=args.force_renders, evaluation_dir=evaluation_dir))
            else:
                if not (d / evaluation_dir / "response.json").exists():
                    print(r["product_id"], r.get("candidate") or r.get("baseline"), "no response yet")
                    continue
                collect(job, d, evaluation_dir=evaluation_dir)
                a = automatic_verdict(r, evaluation_dir=evaluation_dir)
                print(r["product_id"], r.get("candidate") or r.get("baseline"), "owner", r["verdict"], "automatic", a and a["verdict"],
                      "evaluator", a and a["evaluator_overall"], "majors", a and a["major_discrepancies"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
