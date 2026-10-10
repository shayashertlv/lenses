"""$0 native/SDK rehearsal. Scripted geometry is a tool control, never Astra or product output."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import traceback
import numpy as np

from glasses3d.agent.temple_lab.cli import prepare, make_spec
from glasses3d.agent.temple_lab.lab import write_json_atomic, read, sha
from glasses3d.agent.loop import RunSettings, run_astra
from glasses3d.agent import approvals as A
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire
from glasses3d.agent.replay import ScriptedOpenAI, tool_call, message
from glasses3d.g3d_host.fitmesh import load_fit_mesh


def rehearse(root):
    prepare(root)
    settings = RunSettings(effort="max", max_turns=30)
    spec, lab = make_spec(root, "offline-rehearsal", settings, "100", write_request=True)
    with LoopbackOnly() as blocked_network, ProductionTransportTripwire() as blocked_transport:
        try:
            asyncio.run(run_astra(spec, scene=lab))
        except A.ApprovalRefused:
            missing_approval_refused = True
        else:
            missing_approval_refused = False
    # Deliberately fake approval ONLY in a MockTransport proof, not in the clean owner-test package.
    spec.offline_api_key = "sk-offline-temple-lab-proof"
    os.environ["G3D_OFFLINE"] = "1"
    A.write_approval(root / "runs/offline-rehearsal", max_usd="100",
                     owner_words="SYNTHETIC OFFLINE CONTROL ONLY; no authorization for a paid call",
                     recorded_by="offline proof over MockTransport")
    photo = "reference-05"
    mark = dict(id="control", points=[[100, 100], [200, 160]], kind="uncertain",
                evidence="Scripted arbitrary segment for wire testing; not a product interpretation")
    coords = {"P": [4, 0, 0], "U": [24, 10, 0], "Q": [44, 0, 0], "L": [24, -14, 0], "M": [24, -2, 1]}
    nodes = [dict(id=k,d_mm=d,y_mm=y,relief_mm=r) for k,(d,y,r) in coords.items()]
    facets = [dict(id=k,nodes=p,crease=None,rise_per_mm=None) for k,p in
              {"NorthWest": ["P","M","U"], "NorthEast": ["U","M","Q"],
               "SouthEast": ["Q","M","L"], "SouthWest": ["L","M","P"]}.items()]
    def tc(name, **args): return lambda: tool_call(name,args)
    steps = [tc("lab_inspect"), tc("lab_image",image_id=photo,crop=[50,50,300,240]),
             tc("lab_mark",image_id=photo,parent_revision=0,marks=[mark]),
             tc("lab_mark",image_id=photo,parent_revision=0,marks=[]),  # expected stale refusal
             tc("lab_refine",image_id=photo,revision=1,mark_id="control"),
             tc("lab_mark",image_id=photo,parent_revision=1,marks=[{**mark,"points":[[110,100],[210,160]]}]),
             tc("lab_map_marks",image_id=photo,revision=2,anchors=[
                 {"pixel":[100,100],"chart_mm":[0,10]}, {"pixel":[300,100],"chart_mm":[40,10]},
                 {"pixel":[100,200],"chart_mm":[0,-10]}]),
             tc("lab_candidate",parent_revision=0,nodes=nodes,facets=facets,evidence=[f"{photo}:2:control"],
                note="Independent synthetic four-facet control; not a reconstruction"),
             tc("lab_render",views=["side","oblique"],passes=["clay"]),
             lambda: tool_call("lab_pick",dict(render_id=list(lab.state["renders"])[-1],view="side",points=[[384,384]])),
             lambda: tool_call("lab_apply",dict(revision=1,checkpoint=lab.s.head["id"])),
             tc("lab_render",views=["side","oblique"],passes=["clay","normal"]),
             tc("lab_depth",parent_revision=1,changes=[{"node":"M","relief_mm":2.5}],rises=[],note="Absolute depth control"),
             tc("lab_depth",parent_revision=2,changes=[{"node":"M","relief_mm":-20}],rises=[],note="Expected too-thin refusal"),
             lambda: tool_call("lab_apply",dict(revision=2,checkpoint=lab.s.head["id"])),
             tc("lab_render",views=["full","side","oblique","other-side","top","end"],passes=["native","clay"]),
             lambda: tool_call("lab_export",dict(checkpoint=lab.s.head["id"])),
             lambda: tool_call("lab_restore",dict(checkpoint=lab.state["start_checkpoint"],reason="Verify untouched original restoration")),
             tc("lab_render",views=["full","side"],passes=["clay"]),
             lambda: message("OFFLINE SCRIPT COMPLETE. This was not an Astra reasoning run or a product result.")]
    transport = ScriptedOpenAI([])
    def handler(request):
        n = len(transport.creates)
        if len(transport.replies) == n and n < len(steps):
            transport.replies.append(steps[n]())
        return transport.handle(request)
    spec.offline_handler = handler
    with LoopbackOnly() as network, ProductionTransportTripwire() as tripwire:
        result = asyncio.run(run_astra(spec, scene=lab))
    outputs = {}
    for req in transport.creates:
        for item in req.get("input", []):
            if item.get("type") != "function_call_output": continue
            value = item["output"]
            texts = [value] if isinstance(value,str) else [b.get("text","") for b in value if b.get("type") == "input_text"]
            for txt in texts:
                try: outputs[item["call_id"]] = json.loads(txt)
                except ValueError: pass
    statuses = {k:v.get("status") for k,v in outputs.items()}
    exported = outputs.get("call_0017",{})
    native = outputs.get("call_0015",{}).get("result",{})
    untouched = {}
    if exported.get("status") == "exported":
        before = load_fit_mesh(lab.state["original_export"]["path"])
        after = load_fit_mesh(exported["path"])
        allowed = set()
        for side in native.get("sides",{}).values():
            allowed.add(side["target"])
            allowed.update(d["name"] for d in side["decals"])
        for name in sorted(set(before.node_names)-allowed):
            b_ids = [i for i,n in enumerate(before.node_names) if n == name]
            a_ids = [i for i,n in enumerate(after.node_names) if n == name]
            b_tri = before.V[before.F[np.isin(before.part,b_ids)]]
            a_tri = after.V[after.F[np.isin(after.part,a_ids)]]
            untouched[name] = bool(np.array_equal(a_tri,b_tri))
    first_manifest = read(list(lab.state["renders"].values())[0]["manifest"])
    fixed = True
    for rec in lab.state["renders"].values():
        m = read(rec["manifest"])
        for name,row in m["views"].items():
            fixed &= row["blender"] == first_manifest["views"][name]["blender"]
    lab.start()
    try:
        fp_before = lab.s.fingerprint()
        path_refusal = lab.s.call_mutating("control","temple_lab_build",{"spec":"arbitrary-path.json"},deadline_s=30)
        native_refusal = lab.s.call_mutating("control","temple_lab_build",{"spec":{"schema":"bad"}},deadline_s=30)
        native_rollback = native_refusal["status"] == "refused" and native_refusal["restored_to"] == lab.s.head["id"] \
                          and lab.s.fingerprint() == fp_before
    finally:
        lab.close()
    checks = {
        "completed": result["status"] == "completed", "all_scripted_replies": len(transport.creates) == len(steps),
        "missing_approval_refused_before_network": missing_approval_refused and not blocked_network.attempts and not blocked_transport.constructions,
        "no_external_network": not network.attempts and not tripwire.constructions,
        "no_api_contract_violations": not transport.violations,
        "two_valid_revisions": len(lab.state["candidates"]) == 2,
        "absolute_depth": len(lab.state["candidates"]) == 2 and lab.state["candidates"][1]["spec"]["nodes"]["M"][2] == 2.5,
        "original_restored": lab.s.head["fingerprint"] == lab.state["start_fingerprint"],
        "native_applied_twice": len(lab.state["applied"]) == 2,
        "stale_refused": statuses.get("call_0004") == "refused",
        "negative_depth_refused": statuses.get("call_0014") == "refused",
        "exported": statuses.get("call_0017") == "exported",
        "unrelated_native_geometry_unchanged": bool(untouched) and all(untouched.values()),
        "fixed_native_cameras": bool(fixed),
        "native_failed_build_rolled_back": native_rollback,
        "native_path_spec_refused": path_refusal["status"] == "refused",
        "all_eight_photos_in_first_request": sum(x.get("type")=="input_image" for item in transport.creates[0]["input"]
                                                 for x in item.get("content",[]) if isinstance(x,dict)) == 8,
        "image_tool_output_in_next_request": any(b.get("type")=="input_image" for item in transport.creates[1]["input"]
                                                  if item.get("type")=="function_call_output"
                                                  for b in item.get("output",[]) if isinstance(b,dict)),
    }
    lab.verify_inputs()
    report = {"label":"SCRIPTED OFFLINE TOOL TEST, NOT ASTRA OUTPUT", "checks":checks, "passed":all(checks.values()),
              "result":result,"statuses":statuses,"violations":transport.violations,"paid_calls":0,
              "network_attempts":network.attempts,"transport_constructions":tripwire.constructions,
              "outputs":outputs,"request_count":len(transport.creates)}
    report["untouched_objects"] = untouched
    report["native_refusals"] = [path_refusal,native_refusal]
    write_json_atomic(root / "rehearsal.json", report)
    if not report["passed"]: raise AssertionError({k:v for k,v in checks.items() if not v})
    return {"passed":True,"report":str(root / "rehearsal.json"),"paid_calls":0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "rehearse"])
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    receipt = a.out.parent / (a.out.name + "-run.json")
    rec = {"label": "OFFLINE TOOL TEST", "pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(),
           "action": a.action, "out": str(a.out), "paid_calls": 0}
    write_json_atomic(receipt, rec)
    try:
        rec["result"] = prepare(a.out) if a.action == "prepare" else rehearse(a.out)
        rec["exit_code"] = 0
    except BaseException:
        rec["exit_code"] = 1; rec["traceback"] = traceback.format_exc()
        raise
    finally:
        rec["ended"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(receipt, rec)


if __name__ == "__main__":
    main()
