"""M12-C3 $0 fallback proof on a copy; every reply is scripted by Codex, not Astra.

Run with ``python -B -m glasses3d.tests.proofs.assembly_m12_fallback --out PATH``.
The source defaults to the saved M1 synthetic delivery. Real SDK, Blender, map,
finish review, immutable delivery and money guards run; only model HTTP is mocked.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import shutil
import time

import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial.transform import Rotation

from glasses3d.agent import delivery, edits, job_run, owner_captures, procs, replay
from glasses3d.agent.budget import RunLedger
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.g3d_host import glb
from glasses3d.tests.proofs.offline_job_approval import prepare_offline_request

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "data/assembly/m1-proof-28a9752d/jobs/synthetic-assembly-proof-939a1e95"
LABEL = "scripted by Codex, not Astra"
WORDS = "Offline tool proof: move only the small temple region marked A slightly outward."
PART = "Temple_1"
SHIFT_MM = 0.2
CUTOFF = "0.015"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def protected(root):
    """Read source evidence only; never touch credentials, links or source outputs."""
    root = Path(root).resolve(strict=True)
    return {path.relative_to(root).as_posix(): edits.sha(edits.confined(root, path.relative_to(root)))
            for path in sorted(root.rglob("*")) if path.is_file()
            and not path.name.lower().startswith(".env")}


def copy_delivery(source, work):
    copied = Path(work) / source.name
    copied.mkdir()
    shutil.copyfile(source / "job.json", copied / "job.json")
    for name in ("images", "reference_images", "inputs", "checkpoints", "cameras", "deliveries"):
        if (source / name).exists():
            shutil.copytree(source / name, copied / name)
    return copied


def camera(width, height):
    eye, target = np.array([.29, .08, .14]), np.array([0., 0., -.035])
    z = eye - target
    z /= np.linalg.norm(z)
    x = np.cross([0., 1., 0.], z)
    x /= np.linalg.norm(x)
    world = np.eye(4)
    world[:3, :3] = np.column_stack((x, np.cross(z, x), z))
    world[:3, 3] = eye
    fov, near, far, aspect = 35., .001, 10., width / height
    f = 1 / math.tan(math.radians(fov) / 2)
    projection = np.array([[f/aspect, 0, 0, 0], [0, f, 0, 0],
                           [0, 0, -(far+near)/(far-near), -2*far*near/(far-near)], [0, 0, -1, 0]])
    metadata = {"projection": "perspective", "fov_y_deg": fov, "aspect": aspect, "near_m": near,
                "far_m": far, "position_m": eye.tolist(), "quaternion_xyzw":
                Rotation.from_matrix(world[:3, :3]).as_quat().tolist(), "target_m": target.tolist(),
                "matrix_world": world.T.ravel().tolist(), "projection_matrix": projection.T.ravel().tolist()}
    return metadata, projection @ np.linalg.inv(world)


def raster(model, vp, width, height):
    """Independent nearest-triangle CPU view; identity/footprint derive from real pixels."""
    pixels = np.full((height, width, 3), 242, np.uint8)
    depth = np.full((height, width), np.inf)
    ids = np.full((height, width), -1, np.int32)
    triangles = []
    for primitive in model.primitives:
        material = model.material(primitive.material_index) or {}
        color = np.array(material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [.25]*3+[1])[:3])
        for index, corners in enumerate(primitive.triangles):
            xyz = primitive.positions[corners]
            clip = np.column_stack((xyz, np.ones(3))) @ vp.T
            if np.any(clip[:, 3] <= 0):
                continue
            ndc = clip[:, :3] / clip[:, 3, None]
            screen = np.column_stack(((ndc[:, 0]+1)*width/2, (1-ndc[:, 1])*height/2))
            tid = len(triangles)
            triangles.append((primitive, index, xyz, clip, screen))
            lo = np.maximum(0, np.floor(screen.min(0)).astype(int))
            hi = np.minimum([width-1, height-1], np.ceil(screen.max(0)).astype(int))
            if np.any(hi < lo):
                continue
            xx, yy = np.meshgrid(np.arange(lo[0], hi[0]+1)+.5, np.arange(lo[1], hi[1]+1)+.5)
            a, b, c = screen
            den = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(den) < 1e-10:
                continue
            u = ((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
            v = ((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den
            w = 1-u-v
            zz = u*ndc[0, 2]+v*ndc[1, 2]+w*ndc[2, 2]
            area = np.s_[lo[1]:hi[1]+1, lo[0]:hi[0]+1]
            take = (u >= 0) & (v >= 0) & (w >= 0) & (zz < depth[area])
            normal = np.cross(xyz[1]-xyz[0], xyz[2]-xyz[0])
            normal /= max(np.linalg.norm(normal), 1e-12)
            shade = .4+.6*abs(float(np.dot(normal, [.35, .25, .9027735])))
            depth[area][take], ids[area][take] = zz[take], tid
            pixels[area][take] = np.clip(color*255*shade, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels), ids, triangles


def make_capture(source_glb, folder):
    width, height = 1600, 1000
    model = glb.load(source_glb)
    cam, vp = camera(width, height)
    viewer, ids, triangles = raster(model, vp, width, height)
    counts = np.bincount(ids[ids >= 0], minlength=len(triangles))
    candidates = [i for i, row in enumerate(triangles) if row[0].node_name == PART and counts[i] >= 35]
    assert candidates, f"Synthetic source must contain a visible {PART}"
    chosen = max(candidates, key=lambda i: int(counts[i]))
    yy, xx = np.nonzero(ids == chosen)
    pixels = np.column_stack((xx+.5, yy+.5))
    xy = pixels[np.argmin(((pixels-pixels.mean(0))**2).sum(1))]
    primitive, index, xyz, clip, screen = triangles[chosen]
    weights = np.linalg.solve(np.vstack((screen.T, np.ones(3))), [*xy, 1.]) / clip[:, 3]
    weights /= weights.sum()
    normal = np.cross(xyz[1]-xyz[0], xyz[2]-xyz[0])
    normal /= np.linalg.norm(normal)
    anchor = {"kind": "surface", "node_index": primitive.node_index, "node_name": primitive.node_name,
              "mesh_index": primitive.mesh_index, "primitive_index": primitive.primitive_index,
              "triangle_index": int(index), "barycentric": weights.tolist(),
              "position_mm": (weights @ xyz * 1000).tolist(), "normal": normal.tolist(),
              "is_lens": False, "behind": None}
    mask = Image.new("1", viewer.size)
    x, y = xy
    ImageDraw.Draw(mask).ellipse((x-9, y-9, x+9, y+9), fill=1)
    covered = ids[np.array(mask, dtype=bool)]
    faces = {}
    for tid in np.unique(covered[covered >= 0]):
        p, triangle, *_ = triangles[int(tid)]
        faces.setdefault((p.node_index, p.mesh_index, p.primitive_index), set()).add(int(triangle))
    footprint = [{"node_index": key[0], "mesh_index": key[1], "primitive_index": key[2],
                  "triangles": [[i, i] for i in sorted(values)]} for key, values in faces.items()]
    payload = {"schema": owner_captures.SCHEMA, "capture_id": "C1", "source": {
        "delivery_key": "m1-copy", "glb_sha256": edits.sha(source_glb)},
        "image": {"width_px": width, "height_px": height}, "camera": cam, "marks": [{"label": "A",
        "kind": "point", "radius_px": 9, "stroke_px": 4, "vertices": [{"xy_px": xy.tolist(), "anchor": anchor}],
        "footprint": {"pixels": int(np.count_nonzero(covered >= 0)), "faces": footprint}}], "request_text": WORDS}
    annotated = viewer.copy()
    draw = ImageDraw.Draw(annotated)
    draw.ellipse((x-9, y-9, x+9, y+9), fill="#dc2626")
    draw.text((x+12, y-14), "A", fill="#dc2626", stroke_width=1)
    folder.mkdir(parents=True)
    viewer.save(folder / "viewer.png")
    annotated.save(folder / "annotated.png")
    procs.write_json_atomic(folder / "capture.json", payload)
    owner_captures.validate_capture(payload, folder / "viewer.png", folder / "annotated.png", source_glb)
    return payload


def tool_result(root, call_id):
    for path in (root / "work/tool_reports").glob("*.json"):
        row = read(path)
        if row.get("call_id") == call_id and isinstance(row.get("result"), dict):
            return row["result"]
    for path in (root / "runs").glob("*/tools/*/result.json"):
        row = read(path)
        if row.get("call_id") != call_id:
            continue
        for part in row.get("result", []):
            if part.get("type") == "text":
                try:
                    value = json.JSONDecoder().raw_decode(part["text"])[0]
                except (ValueError, KeyError):
                    continue
                if isinstance(value, dict) and "status" in value:
                    return value
    raise AssertionError(f"Missing real tool result {call_id}")


def outcome_summary(outcome):
    return {"status": outcome["result"].get("status"), "error": outcome["result"].get("error"),
            "delivery_status": outcome.get("delivery", {}).get("status")}


class UnderstandScript:
    """Map IDs and receipts are read from the preceding real tool, never invented."""
    def __init__(self, root):
        self.root = root
        self.transport = replay.ScriptedOpenAI([], count_tokens=lambda _: 1000)

    def handle(self, request):
        index = len(self.transport.creates)
        if request.url.path == replay.COUNT_PATH and len(self.transport.replies) == index:
            if index == 0:
                name, args = "labelled_model_map", {"photos": ["side"], "focus": None, "kind": "parts",
                    "map_id": None, "supports_json": None, "page": 0, "label_descriptions_json": None,
                    "owner_labels": ["A"]}
            elif index == 1:
                mapped = tool_result(self.root, "understand-map")
                assert mapped["status"] == "ok", mapped
                name, args = "understand_finish", {"map_id": mapped["map_id"], "map_sha256": mapped["map_sha256"],
                    "capture_receipts": [mapped["capture_receipt"]], "items": [{"item_id": "I1", "number": 1,
                    "label": "A", "interpretation": LABEL + ": adjust the small marked temple region only.",
                    "disposition": "cannot_preview", "reason": "Scripted fallback exercise; no local amount option is sealed.",
                    "direction": "", "amount": "", "target_ids": [], "operation_json": "[]"}],
                    "limitations": [LABEL + "; tool test, not a product judgment."], "preview_receipt": None}
            else:
                raise AssertionError("Diagnostic UNDERSTAND must finish after two scripted requests")
            self.transport.replies.append(replay.tool_call(name, args,
                call_id="understand-map" if index == 0 else "understand-finish", text=LABEL, counted=1000))
            print(f"understand: {name}", flush=True)
        return self.transport.handle(request)


class FinishScript:
    def __init__(self, root):
        self.root = root
        self.transport = replay.ScriptedOpenAI([], count_tokens=lambda _: 1000)

    def handle(self, request):
        index = len(self.transport.creates)
        if request.url.path == replay.COUNT_PATH and len(self.transport.replies) == index:
            if index == 0:
                code = ("import bpy\n" + f"ob = bpy.data.objects[{PART!r}]\n"
                        + f"for vertex in ob.data.vertices:\n    vertex.co.x += {SHIFT_MM!r}\n"
                        + "ob.data.update()\n")
                name, args, cid = "execute_blender_code", {"code": code, "soft_timeout_s": 60,
                    "label": LABEL + "; deliberate out-of-region tool-test change"}, "fallback-change"
            elif index == 1:
                assert tool_result(self.root, "fallback-change")["status"] == "ok"
                name, args, cid = "export_glb", {}, "fallback-export"
            elif index == 2:
                exported = tool_result(self.root, "fallback-export")
                assert exported["status"] == "exported", exported
                name, args, cid = "finish", {"summary": LABEL + "; shifted one synthetic temple 0.2 mm; "
                    "the intentional change exceeds point A's footprint and must be disclosed."}, "fallback-review"
            elif index == 3:
                review = tool_result(self.root, "fallback-review")
                assert review["status"] == "review_required", review
                refs = review["review"]["required_refs"]
                assert isinstance(refs, list) and refs
                difference = {"id": "fallback-temple-shift", "refs": refs,
                    "what_differs": LABEL + "; deliberate 0.2 mm temple shift; no likeness acceptance asserted.",
                    "evidence_ref": "fallback-review", "status": "limitation",
                    "last_correction": "Deliberately moved the complete synthetic temple 0.2 mm, including outside point A's footprint.",
                    "outcome": "The delivered review contains the changed temple; the separate exported-object audit checks its exact movement.",
                    "next_action": "No further correction in this fallback tool test; product likeness and owner acceptance remain untested.",
                    "reason": "The out-of-region shift is deliberately retained to exercise side-effect reporting on a disposable synthetic copy; it is not an accepted product edit."}
                name, args, cid = "finish", {"summary": LABEL + "; reviewed the changed temple and retained all other objects.",
                    "review_call_id": "fallback-review", "per_photo": [{"ref": ref,
                    "differences": difference["what_differs"], "difference_ids": [difference["id"]]}
                    for ref in refs], "difference_updates": [difference]}, "fallback-finish"
            else:
                raise AssertionError("Fallback must finish after four scripted requests")
            self.transport.replies.append(replay.tool_call(name, args, call_id=cid, text=LABEL, counted=1000))
            print(f"fallback: {name}", flush=True)
        return self.transport.handle(request)


def audit_money(root, run_id, transport, *, cutoff_crossed=False):
    result = read(root / "runs" / run_id / "result.json")
    ledger = RunLedger.load(root / "runs" / run_id / "budget.json")
    assert not transport.violations and not transport.unexpected
    assert len(transport.creates) == len(ledger.rows) == result["requests"]
    assert len(transport.counts) == len(transport.creates)
    assert ledger.violation is None and ledger.blocked is None
    assert all(row["outcome"] == "settled" for row in ledger.rows)
    settled = 0
    for row in ledger.rows:
        assert settled < ledger.maximum_micro, "a next request began at/above the owner's cutoff"
        settled += row["settled_micro_usd"]
    assert result["budget"]["settled_micro_usd"] == settled
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    if cutoff_crossed:
        assert result["status"] == "budget_limit" and settled > ledger.maximum_micro
        assert len(transport.creates) == 2 and len(transport.replies) == 3
    return {"requests": len(transport.creates), "cutoff_micro_usd": ledger.maximum_micro,
            "simulated_settled_micro_usd": settled, "in_flight_crossed": settled > ledger.maximum_micro,
            "no_next_request_at_or_above_cutoff": True, "actual_spend_usd": 0}


async def run_round(root, run_id, script, cap):
    settings = replace(job_run.settings_default(), effort="high", settled_only=True,
                       settled_stop_usd=cap, max_turns=8)
    spec, _ = prepare_offline_request(root, run_id, script.handle, settings=settings, cap=cap)
    request = read(root / "runs" / run_id / "approval-request.json")
    assert request["offline_only"] and request["request_sha256"] == spec.current_request_sha256
    outcome = await job_run.execute_run(root, run_id, offline_handler=script.handle)
    procs.write_json_atomic(root / "runs" / run_id / "proof-outcome.json", outcome)
    return outcome


def changed_glb_objects(before, after):
    """Independent exported position audit; verifies the deliberate single-object edit."""
    def arrays(path):
        rows = {}
        for p in glb.load(path).primitives:
            rows[(p.node_name, p.primitive_index)] = p.positions
        return rows
    original, changed = arrays(before), arrays(after)
    assert original.keys() == changed.keys(), "proof unexpectedly added or removed an object"
    names = {key[0] for key in original if not np.array_equal(original[key], changed[key])}
    assert names == {PART}, names
    for key in original:
        if key[0] == PART:
            delta = (changed[key]-original[key])*1000
            np.testing.assert_allclose(delta, np.tile([SHIFT_MM, 0, 0], (len(delta), 1)), atol=1e-5, rtol=0)
    return sorted(names)


async def prove(source, out):
    from glasses3d.agent import owner_fallback

    source, out = Path(source).resolve(strict=True), Path(out).resolve()
    assert not out.exists(), "use a new ignored proof directory"
    out.mkdir(parents=True)
    started = time.monotonic()
    before = protected(source)
    procs.write_json_atomic(out / "source-before.json", before)
    copied = copy_delivery(source, out)
    selected = delivery.load_delivery(copied)
    capture = out / "C1"
    make_capture(Path(selected["model"]), capture)
    understanding = owner_captures.create_capture_understand(copied, selected["delivery_id"], [{
        "item_id": "I1", "request_text": WORDS, "capture_dir": str(capture)}], destination=out / "u")
    scripted_understand = UnderstandScript(understanding)
    understood = await run_round(understanding, "understand", scripted_understand, "21")
    assert understood["result"]["status"] == "completed", outcome_summary(understood)
    doc = read(understanding / "work/owner_edit/understanding.json")
    assert doc["items"][0]["disposition"] == "cannot_preview" and doc["geometry_changed"] is False
    assert doc["scripted"] and not doc["operations"]
    understanding_hashes = protected(understanding)
    fallback = owner_fallback.create_full_edit_item(understanding, "I1", destination=out / "f")
    scripted_edit = FinishScript(fallback)
    finished = await run_round(fallback, "finished", scripted_edit, "21")
    assert finished["result"]["status"] == "completed", outcome_summary(finished)
    assert finished["delivery"]["status"] == "delivered", outcome_summary(finished)
    final = delivery.load_delivery(fallback)
    report = read(final["report"])
    assert report["finished"] and report["origin"] == "full_edit_item" and report["item_id"] == "I1"
    changed = changed_glb_objects(selected["model"], final["model"])
    effects = report["side_effects"]
    assert effects["informational_only"] and effects["region_resolution"] == "resolved", effects
    assert effects["summary"]["changed_objects"] == [PART], effects
    assert effects["summary"]["geometry_outside_item_region"] is True, effects
    assert effects["summary"]["outside_region_objects"] == [PART], effects
    assert effects["source_glb_sha256"] == selected["glb_sha256"]
    assert effects["delivered_glb_sha256"] == final["glb_sha256"]
    for row in effects["objects"]:
        for category in ("materials", "uvs", "transforms"):
            assert row[category]["changed"] is False, (row["name"], category, row[category])
    procs.write_json_atomic(out / "side-effects.json", report["side_effects"])

    cutoff_root = owner_fallback.create_full_edit_item(understanding, "I1", destination=out / "c")
    # Each full counted request costs $0.010000 at the frozen tariff. Request two
    # begins at $0.01 (< $0.015) and settles above it; request three must stay unsent.
    usage = {"input_tokens": 1000, "output_tokens": 0, "total_tokens": 1000,
             "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
    transport = replay.ScriptedOpenAI([replay.tool_call("scene_summary", {"objects": None}, text=LABEL,
        counted=1000, usage=usage) for _ in range(3)])
    cutoff_outcome = await run_round(cutoff_root, "cutoff", transport, CUTOFF)
    assert cutoff_outcome["result"]["status"] == "budget_limit", outcome_summary(cutoff_outcome)
    cutoff_delivery = delivery.load_delivery(cutoff_root)
    cutoff_report = read(cutoff_delivery["report"])
    assert cutoff_report["finished"] is False and cutoff_report["origin"] == "full_edit_item"
    assert cutoff_report["item_id"] == "I1"
    assert protected(source) == before, "original source files changed"
    assert protected(understanding) == understanding_hashes, "fallback changed the sealed UNDERSTAND"
    receipt = {"status": "passed", "label": LABEL, "seconds": time.monotonic()-started,
        "source": str(source), "source_files_unchanged": len(before), "source_glb_sha256": selected["glb_sha256"],
        "copy": str(copied), "understand_root": str(understanding), "fallback_root": str(fallback),
        "delivery": str(final["model"]), "delivery_report": str(final["report"]), "changed_objects": changed,
        "side_effects": str(out / "side-effects.json"), "understand": audit_money(understanding, "understand", scripted_understand.transport),
        "finished": audit_money(fallback, "finished", scripted_edit.transport),
        "cutoff": audit_money(cutoff_root, "cutoff", transport, cutoff_crossed=True),
        "cutoff_draft": str(cutoff_delivery["report"]), "actual_provider_calls": 0, "actual_spend_usd": 0}
    procs.write_json_atomic(out / "proof.json", receipt)
    print(json.dumps(receipt, indent=2), flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    os.environ.update(G3D_OFFLINE="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    disable_sdk_tracing()
    with procs.DescendantsOptOut(), LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        try:
            asyncio.run(prove(args.source, args.out))
        finally:
            if args.out.is_dir():
                procs.write_json_atomic(args.out / "offline-isolation.json", {"label": LABEL, "actual_spend_usd": 0,
                    "non_loopback_attempts": network.attempts, "production_transport_constructions": clients.constructions})
                before_path = args.out / "source-before.json"
                if before_path.is_file():
                    before, after = read(before_path), protected(args.source)
                    procs.write_json_atomic(args.out / "source-after.json", {"unchanged": before == after,
                        "files": len(after), "changed": [name for name, sha in before.items() if after.get(name) != sha],
                        "added": sorted(set(after)-set(before))})
    assert not network.attempts and not clients.constructions


if __name__ == "__main__":
    main()
