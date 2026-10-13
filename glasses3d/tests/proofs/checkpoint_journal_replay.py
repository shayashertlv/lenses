"""Replay a saved run's tool journals on a fresh COPY, with no model transport.

Every recorded argument/code byte is retained. The original static refusal remains
a refusal; successful calls and checkpoint save/reload must be stable. This is a
tool regression proof, never a product delivery.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import time

from agents.tool_context import ToolContext

from glasses3d.agent import job_run, procs
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.tools import build_tools


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


async def replay(source, run_id, out):
    source, out = Path(source).resolve(), Path(out).resolve()
    assert not out.exists() and not out.is_relative_to(source)
    started = time.monotonic()
    # Detach specs may carry environment values; leave them opaque and unread.
    def evidence_files():
        return {p for p in source.rglob("*") if p.is_file() and not p.name.endswith(".spec.json")}
    original = {p: digest(p) for p in evidence_files()}
    out.mkdir(parents=True)
    root = out / "copy"
    root.mkdir()
    shutil.copyfile(source / "job.json", root / "job.json")
    shutil.copytree(source / "images", root / "images")
    # A fresh job deliberately has no product checkpoint, seed or history.
    scene = JobScene(root)
    tools = {tool.name: tool for tool in build_tools(scene, job_run.settings_default())}
    calls = []
    try:
        scene.start()
        for folder in sorted((source / "runs" / run_id / "tools").iterdir()):
            saved = read(folder / "arguments.json")
            arguments = dict(saved["arguments"])
            code = folder / "issued-code.py"
            if code.exists():
                arguments["code"] = code.read_text(encoding="utf-8")
            raw = json.dumps(arguments, ensure_ascii=False)
            ctx = ToolContext(context=None, tool_name=saved["tool"], tool_call_id=saved["call_id"],
                              tool_arguments=raw)
            result = await tools[saved["tool"]].on_invoke_tool(ctx, raw)
            primary = json.loads(result[0].text)
            expected = "refused" if folder.name == "0002-execute_blender_code" else "ok"
            assert primary["status"] == expected, (folder.name, primary)
            head = dict(scene.s.head)
            live = scene.s.fingerprint()
            assert live == head["fingerprint"], (folder.name, "live drift")
            restored = scene.s.worker.call("checkpoint_restore", {
                "path": str(scene.s.ck_dir / head["file"]), "expect_fingerprint": head["fingerprint"],
                "expect_sha256": head["sha256"]}, deadline_s=120)
            assert restored["matches"] is True, (folder.name, restored)
            calls.append({"tool": saved["tool"], "call_id": saved["call_id"], "status": primary["status"],
                          "arguments_sha256": digest(folder / "arguments.json"),
                          "code_sha256": digest(code) if code.exists() else None,
                          "checkpoint": head["id"], "fingerprint": live, "roundtrip_matches": True})
            procs.write_json_atomic(out / "calls.json", calls)
            print(json.dumps(calls[-1]), flush=True)
        assert len(calls) == 5 and sum(c["code_sha256"] is not None for c in calls) == 3
        assert not scene.s.defects, scene.s.defects
        final = dict(scene.s.head)
        scene.close()
        scene = JobScene(root)
        scene.start()
        assert scene.s.head["id"] == final["id"]
        assert scene.s.worker.hello["fingerprint"] == final["fingerprint"]
        assert not scene.s.defects, scene.s.defects
        assert len(list((root / "work/renders").rglob("*_native.png"))) == 4
        assert len(list((root / "work/renders").rglob("*.png"))) >= 12
        assert all(p.is_file() and digest(p) == value for p, value in original.items())
        assert evidence_files() == set(original)
        report = {"status": "passed", "calls": calls, "source_job": str(source), "source_run": run_id,
                  "copy": str(root), "seconds": round(time.monotonic() - started, 3),
                  "original_files_unchanged": len(original), "fresh_worker_matches": True,
                  "actual_spend_usd": 0, "api_calls": 0}
        procs.write_json_atomic(out / "proof.json", report)
        return report
    finally:
        scene.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        report = asyncio.run(replay(args.source, args.run, args.out))
    assert not network.attempts and not clients.constructions
    print(json.dumps({k: v for k, v in report.items() if k != "calls"}, indent=2))
