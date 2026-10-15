"""$0 look-image cost evidence from immutable exports and archived request receipts.

Run --capture-only first to retain real current-renderer outputs in a copy job, then run
without it to price their image-only delta. No model clients or product mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from datetime import datetime, timezone

from glasses3d.agent import procs
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.tools import look


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def historical_looks(job, run):
    by_sha = {sha(path): path for path in (job / "work/exports").glob("*.glb")}
    rows = []
    for folder in sorted((job / "runs" / run / "tools").iterdir()):
        args = read(folder / "arguments.json")
        if args["tool"] not in {"compare_look_to_photos", "finish"}:
            continue
        result = json.loads(read(folder / "result.json")["result"][0]["text"])
        if result.get("full_report"):
            result = read(job / result["full_report"]["path"])["result"]
        capture = result["look_comparison"] if args["tool"] == "finish" else result
        if capture.get("status") != "ok":
            raise ValueError(f"historical look was unavailable: {folder.name}")
        model = by_sha[capture["export_sha256"]]
        rows.append({"call_id": args["call_id"], "tool": args["tool"], "tool_sequence": args["n"],
                     "arguments": args["arguments"], "model": str(model), "export_sha256": capture["export_sha256"],
                     "checkpoint": capture["checkpoint"], "basis": capture["basis"],
                     "basis_sha256": capture["basis_sha256"], "cameras_sha256": capture["cameras_sha256"],
                     "historical_refs": [row["ref"] for row in capture["views"]],
                     "historical_capture": capture, "historical_result": result})
    return rows


def captures(args):
    job, out = args.job.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    copy = out / "copy-job"
    copy.mkdir(exist_ok=True)
    for area in ("images", "cameras"):
        if not (copy / area).exists():
            shutil.copytree(job / area, copy / area)
    if not (copy / "job.json").exists():
        shutil.copyfile(job / "job.json", copy / "job.json")
    rows = historical_looks(job, args.run)
    source_files = [job / "job.json", *[p for area in ("images", "cameras") for p in (job / area).rglob("*") if p.is_file()],
                    *{Path(row["model"]) for row in rows}]
    before = {str(path): sha(path) for path in source_files}
    receipts = []
    for row in rows:
        name = f"{row['tool_sequence']:04d}-{row['tool']}"
        receipt_path = out / f"capture-{name}.json"
        if receipt_path.exists():
            saved = read(receipt_path)
            assert saved["source"]["export_sha256"] == row["export_sha256"]
            receipts.append(saved)
            continue
        model = copy / "work/exports" / Path(row["model"]).name
        model.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(row["model"], model)
        assert sha(model) == row["export_sha256"]
        started = datetime.now(timezone.utc).isoformat()
        tick = time.monotonic()
        print(json.dumps({"event": "capture_start", "call": name, "pid": os.getpid(), "started_utc": started}), flush=True)
        if row["tool"] == "finish" and args.reuse_finish:
            reused = read(args.reuse_finish)
            result = reused["result"]
            assert result["export_sha256"] == row["export_sha256"] and result["basis"] == row["basis"]
            assert result["basis_sha256"] == row["basis_sha256"] and result["cameras_sha256"] == row["cameras_sha256"]
            # Copy the exact native files into this proof's output area so SDK confinement remains exercised.
            result = json.loads(json.dumps(result))
            for index, image in enumerate(result["images"]):
                target = copy / "work/reused-finish" / f"{index:03d}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                assert sha(image["path"]) == image["sha256"]
                shutil.copyfile(image["path"], target)
                image["path"] = str(target)
            reused_from = {"path": str(args.reuse_finish.resolve()), "sha256": sha(args.reuse_finish),
                           "original_started_utc": reused.get("started_utc"), "original_pid": reused.get("pid")}
        else:
            result = look.capture(copy, {"status": "exported", "path": str(model), "sha256": row["export_sha256"],
                "checkpoint": row["checkpoint"]}, f"cost-{name}-{time.time_ns()}", basis=row["basis"],
                refs=None if row["tool"] == "finish" else row["arguments"].get("refs"), timeout_s=280)
            reused_from = None
        assert result["status"] == "ok", result
        assert result["export_sha256"] == row["export_sha256"]
        assert result["basis_sha256"] == row["basis_sha256"] and result["cameras_sha256"] == row["cameras_sha256"]
        if row["tool"] != "finish":
            assert [v["ref"] for v in result["views"]] == row["historical_refs"]
        receipt = {"source": row, "copy_model": str(model), "result": result, "reused_capture": reused_from,
                   "started_utc": started, "finished_utc": datetime.now(timezone.utc).isoformat(),
                   "seconds": time.monotonic() - tick, "pid": os.getpid(), "exit_code": 0}
        procs.write_json_atomic(receipt_path, receipt)
        receipts.append(receipt)
        print(json.dumps({"event": "capture_complete", "call": name, "images": len(result["images"]),
                          "seconds": receipt["seconds"], "report": str(receipt_path)}), flush=True)
    assert all(sha(path) == digest for path, digest in before.items()), "source job evidence changed"
    manifest = {"schema": "g3d.look_cost_captures/1", "job": str(job), "run": args.run, "copy_job": str(copy),
                "source_files": before, "source_unchanged": True, "capture_files": [str(out / f"capture-{r['source']['tool_sequence']:04d}-{r['source']['tool']}.json") for r in receipts],
                "model_or_provider_calls": 0, "actual_spend_usd": 0, "pid": os.getpid()}
    procs.write_json_atomic(out / "captures.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--reuse-finish", type=Path)
    parser.add_argument("--research", type=Path)
    parser.add_argument("--capture-only", action="store_true")
    args = parser.parse_args()
    disable_sdk_tracing()
    procs.opt_out_self()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        manifest = captures(args)
        if not args.capture_only:
            from glasses3d.agent.look_cost import measure
            result = measure(manifest, research=read(args.research))
            procs.write_json_atomic(args.out / "cost.json", result)
            print(json.dumps(result["summary"], indent=2), flush=True)
    assert not network.attempts and not clients.constructions


if __name__ == "__main__":
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[key] = "1"
    main()
