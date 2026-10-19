"""$0, two-conversation owner-edit instrument proof. Every reply is scripted by Codex, not Astra.

The real SDK, approval boundaries, image delivery, private specialist, worker edits,
hard checks and finish receipts run on a fresh copy. Only model HTTP is mocked.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from dataclasses import replace
import hashlib
import json
import os
import re
from pathlib import Path
import time

from glasses3d.agent import approvals, edits, job_run, procs
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.replay import COUNT_PATH, ScriptedOpenAI, tool_call
from glasses3d.agent.tools import model_map as maps, model_map_geometry as G
from glasses3d.tests.proofs.assembly_m5fix8 import sha256, excluded_secret

ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / "glasses3d/data/jobs/prada-beige-86248315"
DELIVERY = "20261015T185027-2f6756-a125c9eec48b82be6e46a1bdb7f781cb6ea56aa3ef417ca3cd2dc3317b1f5013"
REQUEST = ("on the temple, the triangle facet which is nearest to the frame should be more angled, its boarder "
           "with the logo facet should be more outwards and its left boarder should be more inward. in addition "
           "the top of the temples should be going narrower towards the top point of the top triangle facet .")
LABEL = "scripted by Codex, not Astra"
PHOTOS = ["reference-01", "reference-02", "reference-05"]
DEFAULT_PLAN = ROOT / "glasses3d/data/assembly/m6-owner-edit/geometry/proposed-operations.json"
CONFIRMATION = ("Offline proof confirmation, scripted by Codex, not Astra: apply exactly the four shown "
                "mirrored changes and their displayed influence on the disposable copy. This is not paid-run "
                "approval or owner acceptance of the proposed design.")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def protected(source):
    """Hash exact source job inputs, photos, delivered bytes and checkpoints; never credential files."""
    paths = {source / "job.json"}
    for name in ("images", "reference_images", "inputs", "checkpoints", "deliveries", "cameras"):
        base = source / name
        paths.update(p for p in base.rglob("*") if p.is_file() and not excluded_secret(p))
    return {p.relative_to(source).as_posix(): sha256(p) for p in sorted(paths)}


def event(root, call_id):
    path = root / "work/job_scene/state.json"
    rows = read(path)["events"] if path.exists() else []
    return next((r for r in reversed(rows) if r.get("call_id") == call_id), None)


def result(root, call_id):
    for path in (root / "work/tool_reports").glob("*.json"):
        row = read(path)
        if row.get("call_id") == call_id and isinstance(row.get("result"), dict):
            return row["result"]
    raise AssertionError(f"Missing full actual tool report for {call_id}")


def concise_operation(op, scope):
    return {"id": op["id"], "kind": op["kind"], "target_ids": list(op["target_ids"]),
            "scope_ref_sha256": G.digest(scope), "params": copy.deepcopy(op["params"])}


def readable_units(text):
    """Formatting only for scripted explanations; never applied to the verbatim owner request."""
    text=text.replace("projected outline fixed", "Y/Z outline coordinates fixed")
    return re.sub(r"(?<=\d)mm\b", " mm", re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text))


def legend_descriptions(manifest, keys):
    """Scripted interpretation of the verified names; only the walkthrough has product-specific language."""
    names = {"front":"triangle facet nearest the frame", "logo":"facet carrying the lettering",
             "crown":"top triangle facet", "upper":"upper facet behind the lettering",
             "lower":"lower facet", "tail":"facet leading into the arm tip"}
    result = {}
    for key in keys:
        value = manifest["targets"][key]
        side = "left" if value["side"] == 1 else "right"
        aliases = value["aliases"]
        if value["kind"] == "point":
            phrase = "corner " + "/".join(aliases)
        else:
            phrase = ", ".join(names.get(name, name.replace("_", " ") + " facet") for name in aliases)
        result[key] = f"{phrase or 'selected surface'}, {side} arm"
    return result


class Script:
    """Adaptive values come only from actual preceding local tool receipts, never fabricated IDs."""
    def __init__(self, root, plan, phase, out):
        self.root, self.plan, self.phase, self.out = Path(root), plan, phase, Path(out)
        self.transport = ScriptedOpenAI([], count_tokens=lambda _: 1000)
        self.actions, self.maps, self.scopes, self.receipts = [], None, {}, []
        self.prepared = set()
        self.previous = None

    def handle(self, request):
        index = len(self.transport.creates)
        if request.url.path == COUNT_PATH and index not in self.prepared:
            if self.previous:
                where, cid, expected = self.previous
                actual = event(where, cid)
                if not actual or actual["status"] not in expected:
                    raise AssertionError(f"Previous real tool failed: {cid}: {actual}")
            name, args, cid, where, expected = self.reply(index)
            self.transport.replies.append(tool_call(name, args, call_id=cid, text=LABEL,
                counted=1000, usage={"input_tokens":1000,"output_tokens":50,"total_tokens":1050,
                    "input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":10}}))
            self.actions.append({"index":index,"tool":name,"call_id":cid,"root":str(where),"label":LABEL})
            procs.write_json_atomic(self.out / (self.phase + "-actions.json"), self.actions)
            print(f"{self.phase} {index}: {name} ({cid})", flush=True)
            self.previous = (where, cid, expected)
            self.prepared.add(index)
        return self.transport.handle(request)

    def reply(self, index):
        if self.phase == "understand":
            return self.understand(index)
        return self.edit(index)

    def understand(self, index):
        if index == 7 and self.plan.get("amount_options"):
            preview = result(self.root, "proof-amount-preview")
            args = {**self.finish_args, "preview_receipt": preview["preview_receipt"]}
            return "understand_finish", args, "proof-understand-stop", self.root, {"accepted"}
        cid = f"proof-map-{index}"
        if index == 0:
            return "labelled_model_map", {"photos":PHOTOS,"focus":None,"kind":"parts",
                "map_id":None,"supports_json":None,"page":0,"label_descriptions_json":None}, cid, self.root, {"ok"}
        if self.maps is None:
            first = result(self.root, "proof-map-0")
            self.maps = maps.load_map(self.root, first["map_id"], first["map_sha256"])
            self.map_sha = first["map_sha256"]
            self.snapshot = maps._snapshot(self.root, self.maps)
            # Reuse independently predeclared numerical operations only after verifying their current IDs/names.
            for op in self.plan["operations"]:
                for key in op["target_ids"]:
                    assert key in self.maps["targets"], key
                scope = maps._scope(self.root, self.maps, {**op["scope"], "target_ids":op["target_ids"]})
                self.scopes[op["id"]] = scope
        if 1 <= index <= 4:
            group = [o for o in self.plan["operations"] if o["item"] == index]
            focus = sorted({key for o in group for key in o["target_ids"]})
            return "labelled_model_map", {"photos":PHOTOS,"focus":focus,"kind":"faces",
                "map_id":self.maps["map_id"],"supports_json":json.dumps([self.scopes[o["id"]] for o in group]),
                "page":0,"label_descriptions_json":json.dumps(legend_descriptions(self.maps,focus))}, cid, self.root, {"ok"}
        if index == 5:
            object_ids={scope["object_id"] for scope in self.scopes.values()}
            corner_ids=[key for key,value in self.maps["targets"].items()
                        if value["kind"]=="point" and value["object_id"] in object_ids
                        and set(value["aliases"]).intersection({"A","C","E","D"})]
            assert len(corner_ids)==8, "Both current temples need their four verified physical corner names"
            return "labelled_model_map", {"photos":PHOTOS,"focus":corner_ids,"kind":"points",
                "map_id":self.maps["map_id"],"supports_json":None,"page":0,
                "label_descriptions_json":json.dumps(legend_descriptions(self.maps,corner_ids))},cid,self.root,{"ok"}
        if index != 6:
            raise AssertionError("Understand must stop immediately after accepted understand_finish")
        for number in range(1,6):
            r = result(self.root, f"proof-map-{number}")
            self.receipts.append(r["capture_receipt"])
            delivered = {item["sha256"] for item in r["scope_refs"]}
            for op in [o for o in self.plan["operations"] if o["item"] == number]:
                assert G.digest(self.scopes[op["id"]]) in delivered, f"Support not shown: {op['id']}"
        labels = ["Near-frame triangle", "Border with logo", "Other triangle border", "Upper crown toward apex"]
        amounts = ["Up to 0.20 mm", "0.15 mm with 2 mm falloff", "0.15 mm with 2 mm falloff", "Up to 0.15 mm with 8 mm falloff"]
        items=[]
        for number in range(1,5):
            group = [o for o in self.plan["operations"] if o["item"] == number]
            items.append({"number":number,"label":labels[number-1],
                "disposition":"executable","reason":"Shown finite surface displacement.",
                "interpretation":LABEL + ": " + readable_units(self.plan["interpretations"][number-1]),
                "direction":"Mirrored on both temples; signed X depth only, Y/Z outline fixed.",
                "amount":amounts[number-1],"target_ids":sorted({k for o in group for k in o["target_ids"]}),
                "operation_json":json.dumps([concise_operation(o,self.scopes[o["id"]]) for o in group])})
        self.finish_args = {"map_id":self.maps["map_id"],"map_sha256":self.map_sha,
            "capture_receipts":self.receipts,"items":items,"limitations":[
                LABEL + "; proposed amounts are an instrument demonstration, not the owner's final design approval.",
                "The current manually edited mesh is authoritative; stored construction supplies verified names only.",
                "Local weighted border movements create varying face slopes; no single uniform final dihedral is claimed.",
                "Magenta shows moved-vertex influence, black fixed vertices; amber is the separate normal-only ring.",
                "The top narrowing is interpreted as depth taper toward the apex; Y/Z outline coordinates stay fixed, but depth changes can shift a projected silhouette."]}
        if self.plan.get("amount_options"):
            return "preview_owner_edit", {"map_id":self.maps["map_id"], "map_sha256":self.map_sha,
                "capture_receipts":self.receipts, "operations_json":json.dumps([
                    op for item in items for op in json.loads(item["operation_json"])]),
                "options":self.plan["amount_options"], "refs":PHOTOS}, "proof-amount-preview", self.root, {"ok"}
        return "understand_finish", self.finish_args, "proof-understand-stop", self.root, {"accepted"}

    def edit(self, index):
        stage_call = "proof-owner-stage"
        child = self.root / "work/temple_stage" / hashlib.sha256(stage_call.encode()).hexdigest()[:16]
        if index == 0:
            return "temple_stage", {}, stage_call, self.root, {"ok"}
        # The parent awaits the specialist, so the first child request follows the outstanding stage call.
        # Do not require the parent's stage event until the child has finished.
        operation_ids = [o["id"] for o in edits.Authority(child).resolve()["operations"]]
        rows = {
            1:("facet_owner_edit",{"operation_ids":operation_ids,"corrections":None},"proof-child-edit",child,{"ok"}),
            2:("edit_report",{"summary":LABEL + ": all four mirrored depth changes applied to actual mesh; adjacent slope/normal effects shown.","not_done":[]},"proof-child-report",child,{"ok"}),
            3:("finish",{"summary":LABEL + ": first specialist image review.","review_call_id":None,"per_photo":None},"proof-child-finish-1",child,{"review_required"}),
            5:("edit_report",{"summary":LABEL + ": accepted specialist's exact mesh copied back; same four changes, no template rebuild.","not_done":[]},"proof-parent-report",self.root,{"ok"}),
            6:("finish",{"summary":LABEL + ": first parent image review.","review_call_id":None,"per_photo":None},"proof-parent-finish-1",self.root,{"review_required"}),
        }
        if index in (4,7):
            where, prior = (child,"proof-child-finish-1") if index == 4 else (self.root,"proof-parent-finish-1")
            r = result(where,prior)
            photos=[{"ref":ref,"differences":LABEL + ": numerical owner-depth edits with unchanged Y/Z outline coordinates; projected silhouettes can shift with depth. These real panels are instrument evidence, not an Astra visual judgement."}
                    for ref in r["review"]["required_refs"]]
            return "finish",{"summary":LABEL + ": requested actual-mesh edits and side effects are reported; owner visual approval remains pending.",
                "review_call_id":prior,"per_photo":photos}, prior.replace("-1","-2"),where,{"accepted"}
        if index not in rows:
            raise AssertionError("Edit must stop after accepted finish")
        return rows[index]


async def run_round(root, run_id, plan, phase, out):
    script = Script(root,plan,phase,out)
    # The child is invoked before the awaiting parent stage can record completion.
    original_handle = script.handle
    def handle(request):
        if request.url.path == COUNT_PATH and phase == "edit" and len(script.transport.creates) == 1:
            script.previous = None
        if request.url.path == COUNT_PATH and phase == "edit" and len(script.transport.creates) == 5:
            stage = event(root,"proof-owner-stage")
            assert stage and stage["status"] == "ok", stage
        return original_handle(request)
    settings=replace(job_run.settings_default(),effort="high",max_turns=30,max_output_tokens=4096)
    spec,scene=job_run.prepare_run(root,run_id,settings=settings,cap="20",write_request=True,offline_handler=handle)
    refused=False
    try:
        approvals.check_approval(root/"runs"/run_id,current_request_sha256=spec.current_request_sha256,requested_cap_usd="20")
    except approvals.ApprovalRefused as exc:
        refused=True
    assert refused, "Every round must need a new approval"
    from glasses3d.tests.proofs.offline_job_approval import approve_offline_request
    approve_offline_request(root/"runs"/run_id,handle,cap="20")
    outcome=await job_run.execute_run(root,run_id,offline_handler=handle,scene=scene)
    assert not script.transport.unexpected and not script.transport.violations
    assert not any(row.get("type") in {"function_call","function_call_output"}
                   for row in script.transport.creates[0].get("input",[]) if isinstance(row,dict)), "Round reused a prior conversation"
    report={"root":str(root),"run_id":run_id,"new_approval_required":refused,"outcome":outcome,
        "mock_inference_calls":len(script.transport.creates),"mock_count_calls":len(script.transport.counts),
        "real_provider_calls":0,"actual_spend_usd":0,"efforts":[r.get("reasoning",{}).get("effort") for r in script.transport.creates],
        "fresh_conversation":True,"mock_usage_note":"Fixed synthetic 1000-token counts exercise procedure; not a cost forecast or real model usage.",
        "actions":script.actions,"scripted":LABEL}
    procs.write_json_atomic(out/(phase+"-result.json"),report)
    assert outcome["result"]["status"] == "completed", outcome["result"].get("error")
    return report


async def prove(out, plan_path, amount_preview=False):
    out=Path(out).resolve(); out.mkdir(parents=True,exist_ok=False)
    plan=read(plan_path)
    if amount_preview:
        # Predeclared fractions of the already tested demonstration, never tuned after seeing the result.
        plan["amount_options"] = [{"id":"A", "label":"Half the proposed movement", "scale":.5},
                                  {"id":"B", "label":"Three quarters of the proposed movement", "scale":.75},
                                  {"id":"C", "label":"The full proposed movement", "scale":1.0}]
        plan["selected_option"] = "B"
    procs.write_json_atomic(out/"predeclared-operations.json",plan)
    diagnostics=plan_path.parent
    procs.write_json_atomic(out/"prior-normal-diagnostics.json",{
        "note":"Earlier refused copy and exact-attribute repair controls; these are provenance, not this proof's results.",
        "files":[{"path":str(p),"sha256":sha256(p)} for p in
                 (diagnostics/"attribute-diagnostic.json", diagnostics/"direct-apply-3-result.json") if p.exists()]})
    protected_before=protected(JOB)
    procs.write_json_atomic(out/"protected-before.json",protected_before)
    source_files=[p for base in (ROOT/"glasses3d/agent",ROOT/"glasses3d/g3d",ROOT/"glasses3d/g3d_host")
                  for p in base.rglob("*.py")]
    source_files.append(Path(__file__).resolve())
    source_binding={p.relative_to(ROOT).as_posix():sha256(p) for p in source_files}
    procs.write_json_atomic(out/"code-before.json",source_binding)
    started=time.time()
    first=edits.create_understand(JOB,REQUEST,delivery_id=DELIVERY,destination=out/"edit-copy")
    understanding=await run_round(first,"proof-understand",plan,"understand",out)
    assert understanding["outcome"]["delivery"]["status"] == "awaiting_owner_confirmation"
    doc=read(first/"work/owner_edit/understanding.json")
    assert doc["request_text"] == REQUEST and len(doc["items"]) == 4 and len(doc["operations"]) == 8
    assert doc["geometry_changed"] is False
    assert doc["legend"] and all(len(row["label"]) <= 4 for row in doc["legend"])
    assert any("triangle facet nearest the frame" in row["description"] for row in doc["legend"])
    assert doc["following_markings"], "Understand must explicitly list the markings carried by the surface edit"
    assert all(not e.get("mutating") for e in read(first/"work/job_scene/state.json")["events"])
    if amount_preview:
        preview = result(first, "proof-amount-preview")
        assert preview["source_unchanged"] and len(preview["options"]) == 3
        assert doc["amount_preview"]["preview_sha256"] == preview["preview_sha256"]
        assert all(option["checks"]["confirmed_amounts_match"] for option in preview["options"])
        assert all(option["full_pairs"] and option["target_details"] for option in preview["options"])
        first_hashes = edits.tree_hashes(first)
    second=edits.create_edit(first.parent,CONFIRMATION, option_id=plan.get("selected_option"))
    if amount_preview:
        assert first_hashes == edits.tree_hashes(first), "Confirming changed the understanding copy"
        chosen = next(option for option in preview["options"] if option["id"] == plan["selected_option"])
        assert edits.Authority(second).resolve()["operations"] == chosen["operations"]
    assert not (second/"runs").exists(), "A prior round approval must not be inherited"
    edited=await run_round(second,"proof-edit",plan,"edit",out)
    assert edited["outcome"]["delivery"]["finished"] is True
    after=protected(JOB)
    assert protected_before == after, "Original delivery/job/photo/checkpoint/basis bytes changed"
    final_binding={p.relative_to(ROOT).as_posix():sha256(p) for p in source_files}
    assert source_binding == final_binding, "Production sources changed during proof; preserve attempt and rerun final source"
    report={"status":"passed","label":LABEL,"actual_provider_calls":0,"actual_spend_usd":0,
        "seconds":time.time()-started,"source_delivery":DELIVERY,"source_fingerprint":doc["source_fingerprint"],
        "original_files_unchanged":len(after),"understand_root":str(first),"edit_root":str(second),
        "understand_status":understanding["outcome"]["delivery"]["status"],"edit_delivery":edited["outcome"]["delivery"],
        "owner_request":REQUEST,"proposed_amounts_mm":[.20,.15,.15,.15],"operations":8,
        "predeclaration":plan["predeclaration"],"code_sha256":G.digest(source_binding)}
    report["proposal_sha256"]=sha256(out/"predeclared-operations.json")
    if amount_preview:
        assert edited["outcome"]["delivery"]["report_data"]["fingerprint"] == chosen["export"]["fingerprint"]
        report["amount_preview"] = {"receipt":str(first / preview["preview_receipt"]),
            "sha256":preview["preview_sha256"], "options":plan["amount_options"],
            "selected_option":plan["selected_option"], "selected_operations_match":True,
            "selected_export_fingerprint":chosen["export"]["fingerprint"], "images":preview["images"]}
    procs.write_json_atomic(out/"report.json",report)
    print(json.dumps({k:report[k] for k in ("status","seconds","actual_spend_usd","original_files_unchanged")}),flush=True)


def main():
    from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
    parser=argparse.ArgumentParser(); parser.add_argument("--out",required=True); parser.add_argument("--plan",default=str(DEFAULT_PLAN))
    parser.add_argument("--amount-preview", action="store_true")
    args=parser.parse_args()
    os.environ["G3D_OFFLINE"]="1"
    for key in ("OPENAI_API_KEY","OPENAI_BASE_URL","OPENAI_ORG_ID","OPENAI_PROJECT_ID","OPENAI_ADMIN_KEY",
                "HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
        os.environ.pop(key,None)
    disable_sdk_tracing()
    with procs.DescendantsOptOut(), LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        try:
            asyncio.run(prove(args.out,Path(args.plan),args.amount_preview))
        finally:
            if Path(args.out).is_dir():
                procs.write_json_atomic(Path(args.out)/"offline-isolation.json",{
                    "non_loopback_attempts":network.attempts,"production_transport_constructions":clients.constructions,
                    "environment_credentials_scrubbed":True,"sdk_tracing_disabled":True,
                    "actual_spend_usd":0,"label":LABEL})
                before=Path(args.out)/"protected-before.json"
                if before.exists():
                    old,current=read(before),protected(JOB)
                    procs.write_json_atomic(Path(args.out)/"protected-after.json",{
                        "unchanged":old==current,"count":len(current),
                        "changed":[name for name in old if current.get(name)!=old[name]],
                        "added":sorted(set(current)-set(old))})
    assert not network.attempts and not clients.constructions


if __name__ == "__main__":
    main()
