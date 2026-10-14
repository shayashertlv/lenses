"""Exercise real main photo_read on fixed, manually marked Oakley photos; no paid clients.

Exit 0 means both execution and requested accuracy proof passed. Exit 2 means the tool
executed but the frozen camera/feature observations failed the accuracy proof. Never
substitute model reprojections or enlarge error bars to change that outcome.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace

from agents.tool_context import ToolContext

from glasses3d.agent.offline import disable_sdk_tracing, LoopbackOnly, ProductionTransportTripwire
from glasses3d.agent.tools import build_tools
from glasses3d.agent.procs import write_json_atomic
from glasses3d.g3d_host import compare as C

# Native photo selections made before triangulation; see the separate visual audit.
PICKS = {
    "negative_x_hinge_logo_centre": {"01": [293, 614], "03": [851, 459], "06": [489, 82]},
    "positive_x_hinge_logo_centre": {"01": [1627, 614], "02": [1453, 666], "05": [537, 469], "06": [488, 938]},
    "negative_x_rounded_terminal": {"03": [87, 561], "04": [1270, 798], "06": [1266, 249]},
    "positive_x_rounded_terminal": {"05": [1374, 581], "04": [265, 798], "06": [1269, 768]},
}


def run(source, out):
    source, out = Path(source).resolve(), Path(out).resolve()
    original = C.load_basis(source, "basis-01")
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(original.dir, out / "cameras/basis-01")
    for rid, row in original.refs.items():
        relative = Path(row["photo"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("proof expects photos stored under the source job with relative paths")
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original.photo_path(rid), target)
    copied = C.load_basis(out, "basis-01")
    scene = SimpleNamespace(root=out, s=SimpleNamespace(head={"id": original.meta["model"]["checkpoint"]}),
                            lock=threading.RLock(), calls=[], images=[], unseen=[])
    scene.record_call = lambda *args: scene.calls.append(args)
    scene.record_images = lambda cid, result: scene.images.extend(result["images"])
    scene.mark_unseen = lambda cid: scene.unseen.append(cid)
    tool = next(t for t in build_tools(scene) if t.name == "photo_read")
    results = []
    for name, positions in PICKS.items():
        args = {"basis": "basis-01", "points": [{"name": name, "kind": "corner", "temple_side": None,
                 "picks": [{"photo": "reference-" + photo, "uv": uv, "edges": None, "sd_px": 2.0}
                           for photo, uv in positions.items()]}]}
        raw = json.dumps(args)
        cid = "proof-" + name
        ctx = ToolContext(context=None, tool_name="photo_read", tool_call_id=cid, tool_arguments=raw)
        output = asyncio.run(tool.on_invoke_tool(ctx, raw))
        full = scene.calls[-1][4]
        text_chars = [len(getattr(item, "text", "")) for item in output if getattr(item, "text", None)]
        assert full["status"] == "ok", full
        assert scene.calls[-1][2] == scene.calls[-1][3]
        rec = full["readings"][0]
        worst = max(rec["pair_disagreement"], key=lambda d: d["distance_mm"])
        results.append({"name": name, "call_id": cid, "report_path": full["report_path"],
                        "report_sha256": full["report_sha256"], "images": full["images"],
                        "xyz_mm": rec["xyz_mm"], "sd_xyz_mm": rec["sd_xyz_mm"],
                        "joint_consistent": rec["consistent_with_stated_error"],
                        "max_residual_px": max(rec["residual_px"]),
                        "max_pair_distance_mm": worst["distance_mm"],
                        "max_pair_axis_error_ratio": max(d["max_axis_error_ratio"] for d in rec["pair_disagreement"]),
                        "warnings": rec["warnings"], "output_text_characters": text_chars,
                        "feature_limitation": "rounded cap centre is not a certified homologous surface point"
                            if "terminal" in name else "same visible O branding centre; modest foreshortening ambiguity"})
    passed = all(row["joint_consistent"] and row["max_pair_axis_error_ratio"] <= 1 for row in results)
    proof = {"schema": "g3d.m5fix2_photo_read_proof/1", "execution_status": "completed",
             "accuracy_proof_passed": passed, "cost_usd": 0,
             "basis_sha256": C.sha256_file(original.dir / "basis.json"),
             "copied_basis_sha256": C.sha256_file(copied.dir / "basis.json"),
             "model_sha256": original.meta["model"]["sha256"],
             "photos": {rid: {"source_sha256": C.sha256_file(original.photo_path(rid)),
                              "copy_sha256": C.sha256_file(copied.photo_path(rid))} for rid in original.refs},
             "pick_selection": "Fixed visually selected native photo points; no picks or errors tuned after solving.",
             "results": results, "source": str(source), "output": str(out)}
    write_json_atomic(out / "proof.json", proof)
    return proof


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        proof = run(args.source, args.out)
        proof["network_attempts"] = network.attempts
        proof["production_client_attempts"] = clients.constructions
        assert not network.attempts and not clients.constructions
        write_json_atomic(Path(args.out) / "proof.json", proof)
    print(json.dumps({"execution_status": proof["execution_status"], "accuracy_proof_passed": proof["accuracy_proof_passed"],
                      "cost_usd": 0, "results": [{k: r[k] for k in ("name", "max_pair_distance_mm", "max_pair_axis_error_ratio",
                                                                                    "max_residual_px", "joint_consistent")}
                                                for r in proof["results"]]}, indent=2))
    return 0 if proof["accuracy_proof_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
