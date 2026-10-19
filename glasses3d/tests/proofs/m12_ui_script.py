"""Predeclared $0 SDK conversations for the M12 real-browser instrument proof.

These are tool tests on private copies, scripted by Codex, not Astra. The
browser's actual capture bytes/anchors/words remain the UNDERSTAND inputs.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import time

from glasses3d.agent import edits, owner_captures, replay
from glasses3d.tests.proofs import assembly_m1 as m1
from glasses3d.tests.proofs.assembly_m12_choices import Script as ChoicesScript, write
from glasses3d.tests.proofs.assembly_m12_fallback import tool_result

LABEL = "scripted by Codex, not Astra"
USAGE = {"input_tokens": 1000, "output_tokens": 0, "total_tokens": 1000,
         "input_tokens_details": {"cached_tokens": 0},
         "output_tokens_details": {"reasoning_tokens": 0}}


class DynamicScript:
    def __init__(self, root, out):
        self.root, self.out = Path(root), Path(out)
        self.transport = replay.ScriptedOpenAI([], count_tokens=lambda _: 1000)
        self.actions, self.prepared = [], set()

    def handle(self, request):
        index = len(self.transport.creates)
        if request.url.path == replay.COUNT_PATH and index not in self.prepared:
            try:
                name, args, cid = self.reply(index)
            except Exception as error:
                write(self.out / "replay-handler-error.json", {
                    "index": index, "type": type(error).__name__, "message": str(error)})
                raise
            self.transport.replies.append(replay.tool_call(name, args, call_id=cid,
                text=LABEL, counted=1000, usage=USAGE))
            self.prepared.add(index)
            self.actions.append({"index": index, "tool": name, "call_id": cid})
            write(self.out / "script-actions.json", self.actions)
            print(f"{index}: {name} ({cid})", flush=True)
        return self.transport.handle(request)


class JobScript(DynamicScript):
    def __init__(self, root, out, scenario):
        super().__init__(root, out)
        self.scenario = scenario

    def reply(self, index):
        if self.scenario == "slow" and index:
            # Delayed mock replies keep the real cooperative loop active while
            # Chromium is closed/reopened. No server or browser controls the loop.
            time.sleep(2)
        if index == 0:
            return "execute_blender_code", {"code": m1.build_code(), "soft_timeout_s": 120,
                "label": LABEL + "; synthetic M1 fixture"}, "call_0001"
        if self.scenario in {"slow", "cutoff"}:
            assert index < 1000, "Survival proof must press Stop"
            return "scene_summary", {"objects": None}, f"call_{index+1:04d}"
        steps = [
            ("scene_summary", {"objects": None}),
            ("fit_cameras", {"reason": "Fit the rebuilt synthetic fixture to every uploaded photo",
                             "refs": None, "workers": None}),
            ("render_views", {"views": ["reference"], "passes": ["clay"], "size_px": 640,
                              "background": "white", "basis": None}),
            *[("seat_lens", {"lens": f"Lens_{sx}", "frame_type": "acetate", "frame_parts": None,
                             "lower_edge": None, "capture_mm": None, "dry_run": False}) for sx in (-1, 1)],
            ("check_lens_seating", {"lenses": None, "frame_parts": None, "frame_type": None,
                                    "hidden_x_range_mm": None, "lower_edge": None, "stations_table": False}),
            ("compare_to_photos", {"refs": None, "parts": "all", "size_px": 640, "basis": None}),
            ("export_glb", {}), ("arview_capture", {}),
            ("compare_look_to_photos", {"refs": None, "basis": None}),
            ("finish", {"summary": LABEL + "; synthetic frame, fitted photos, seated lenses and AR evidence."}),
        ]
        if index <= len(steps):
            name, args = steps[index-1]
        else:
            assert index == len(steps)+1
            review = tool_result(self.root, "call_0012")
            assert review["status"] == "review_required", review
            name, args = "finish", {"summary": LABEL + "; reviewed all native comparisons; no likeness judgment.",
                "review_call_id": "call_0012", "per_photo": [{"ref": ref,
                    "differences": LABEL + "; procedure test, not product acceptance."}
                    for ref in review["review"]["required_refs"]]}
        return name, args, f"call_{index+1:04d}"


def capture_plan(root, plan_path, out):
    plan = edits.read(plan_path)
    captures = owner_captures.load_round_captures(root)
    assert len(captures) == 3
    assert [row["item_id"] for row in captures] == ["I1", "I2", "I3"]
    assert [row["labels"] for row in captures] == [["A"], ["B"], ["C", "D"]]
    first, temple = plan["items"]
    by_id = {op["id"]: op for op in first["operations"]}
    items = []
    # Physical +X is wearer's left; these two native supports share the front
    # object but have disjoint polygon and normal-polygon sets.
    for index, (side, label) in enumerate(((1, "A"), (-1, "B")), 1):
        original = captures[index-1]
        item = copy.deepcopy(first)
        item.update(item_id=original["item_id"], request_text=original["request_text"], label=label,
                    interpretation=f"Repair local shading only inside {label} at the wearer's "
                                   + ("left" if side == 1 else "right") + " upper outer corner; positions stay unchanged.",
                    operations=[copy.deepcopy(by_id[f"corner-shading-{side}"])])
        item.pop("capture", None)
        item.pop("capture_dir", None)
        # Each actual browser mark must be on the declared front-side region.
        anchors = [v["anchor"] for m in original["payload"]["marks"] for v in m["vertices"]
                   if v["anchor"]["kind"] == "surface"]
        assert anchors and all(a["node_name"] == "Acetate_front" and a["position_mm"][0]*side > 0
                               for a in anchors), (label, anchors)
        items.append(item)
    item = copy.deepcopy(temple)
    item.update(item_id="I3", request_text=captures[2]["request_text"], label="C, D")
    item.pop("capture", None)
    item.pop("capture_dir", None)
    items.append(item)
    a, b = (row["operations"][0]["scope"] for row in items[:2])
    assert a["object_id"] == b["object_id"]
    for field in ("affected_polygon_ids", "normal_affected_polygon_ids"):
        assert not set(a[field]) & set(b[field]), field
    plan["items"] = items
    write(out / "predeclared-plan.json", plan)
    write(out / "actual-browser-captures.json", [{"item_id": r["item_id"], "labels": r["labels"],
        "request_text": r["request_text"], "metadata_sha256": r["metadata_sha256"],
        "viewer_sha256": r["viewer_sha256"], "annotated_sha256": r["annotated_sha256"],
        "image": r["payload"]["image"]} for r in captures])
    return plan


class CaptureScript(ChoicesScript):
    def __init__(self, root, plan_path, out):
        super().__init__(root, capture_plan(root, plan_path, out), out)

    def reply(self, index):
        if index < 5:
            name, args, cid, expected = super().reply(index)
            if index == 4:
                expected = {"refused"}
            return name, args, cid, expected
        assert index == 5, "Three-item UNDERSTAND must end after six model requests"
        shown = tool_result(self.root, "c2-map")
        refusal = tool_result(self.root, "c2-preview-I3")
        assert refusal["status"] == "refused", refusal
        write(self.out / "actual-temple-refusal.json", refusal)
        rows = []
        for number, item in enumerate(self.remapped, 1):
            blocked = item["item_id"] == "I3"
            preview = None if blocked else tool_result(self.root, "c2-preview-" + item["item_id"])
            if preview:
                assert preview["source_unchanged"] and len(preview["options"]) == 2
            reason = ("The attempted native preview refused: " + refusal["message"]) if blocked else item["reason"]
            rows.append({"number": number, "item_id": item["item_id"], "label": item["label"],
                "interpretation": LABEL + ": " + (reason if blocked else item["interpretation"]),
                "disposition": "cannot_preview" if blocked else "previewed", "reason": reason,
                "direction": "" if blocked else item["direction"], "amount": "" if blocked else item["amount"],
                "target_ids": list(dict.fromkeys(t for op in item["operations"] for t in op["target_ids"])),
                "operation_json": "[]" if blocked else json.dumps(self.operations[item["item_id"]]),
                "preview_receipt": None if blocked else preview["preview_receipt"]})
        return "understand_finish", {"map_id": shown["map_id"], "map_sha256": shown["map_sha256"],
            "capture_receipts": [shown["capture_receipt"]], "items": rows, "preview_receipt": None,
            "limitations": [LABEL + "; browser/native copy-only instrument test, not product acceptance.",
                "I1 and I2 change local normals only, with disjoint supports on the same front object.",
                "I3 has no applicable operation because its actual native preview refused."]}, "d1-finish", {"accepted"}


class DiagnosticScript(DynamicScript):
    def reply(self, index):
        captures = owner_captures.load_round_captures(self.root)
        assert len(captures) == 1
        row = captures[0]
        if index == 0:
            return "labelled_model_map", {"photos": ["reference-04"], "focus": None, "kind": "parts",
                "map_id": None, "supports_json": None, "page": 0, "label_descriptions_json": None,
                "owner_labels": row["labels"]}, "followup-map"
        assert index == 1
        shown = tool_result(self.root, "followup-map")
        assert shown["status"] == "ok", shown
        return "understand_finish", {"map_id": shown["map_id"], "map_sha256": shown["map_sha256"],
            "capture_receipts": [shown["capture_receipt"]], "items": [{"item_id": row["item_id"], "number": 1,
                "label": ", ".join(row["labels"]), "interpretation": LABEL + "; inspect the new marked fallback delivery.",
                "disposition": "cannot_preview", "reason": "Diagnostic admission proof; no further amount operation is sealed.",
                "direction": "", "amount": "", "target_ids": [], "operation_json": "[]"}],
            "preview_receipt": None, "limitations": [LABEL + "; followup admission only."]}, "followup-finish"


class FallbackScript(DynamicScript):
    """C3's four real calls, applied to the copied Prada temple rather than M1."""
    def reply(self, index):
        if index == 0:
            code = ("import bpy\nob = bpy.data.objects['Temple_body_1']\n"
                    "for vertex in ob.data.vertices:\n    vertex.co.x -= 0.2\nob.data.update()\n")
            return "execute_blender_code", {"code": code, "soft_timeout_s": 60,
                "label": LABEL + "; deliberate 0.2 mm whole-temple side effect"}, "fallback-change"
        if index == 1:
            assert tool_result(self.root, "fallback-change")["status"] == "ok"
            return "export_glb", {}, "fallback-export"
        if index == 2:
            assert tool_result(self.root, "fallback-export")["status"] == "exported"
            return "finish", {"summary": LABEL + "; 0.2 mm temple change exceeds C/D footprint; disclose side effects."}, "fallback-review"
        assert index == 3
        review = tool_result(self.root, "fallback-review")
        assert review["status"] == "review_required", review
        return "finish", {"summary": LABEL + "; reviewed the deliberate temple movement; no visual acceptance.",
            "review_call_id": "fallback-review", "per_photo": [{"ref": ref,
                "differences": LABEL + "; one complete temple moved 0.2 mm, including outside marked polygons."}
                for ref in review["review"]["required_refs"]]}, "fallback-finish"


def make_script(root, record, out, plan_path):
    if record["scenario"] in {"success", "cutoff", "slow"}:
        return JobScript(root, out, record["scenario"])
    if record["scenario"] == "understand":
        return CaptureScript(root, plan_path, out)
    if record["scenario"] == "followup":
        return DiagnosticScript(root, out)
    if record["scenario"] == "fallback":
        return FallbackScript(root, out)
    raise ValueError("Unknown private proof scenario")
