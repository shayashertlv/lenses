"""E4/E5 browser/generality proof on copies and explicit imported fixtures.

The input manifest is private evidence: [{label, job?, delivery_id?, model}].
Imported assets get a fixture-only draft and a clearly labelled placeholder,
never a claim that a generated image is a product photo. No paid launcher exists.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from PIL import Image, ImageDraw
import uvicorn

from glasses3d.agent import delivery, jobs, procs, review, print_freshness
from glasses3d.agent.tools import arview
from glasses3d.dev_ui.contracts import Config
from glasses3d.dev_ui.core_adapter import CoreAdapter
from glasses3d.dev_ui.lens_renderer import ensure_renderer_build
from glasses3d.dev_ui.server import create_app
from glasses3d.dev_ui.tasks import execute_task
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.g3d_host.seat_check import check_glb
from glasses3d.tests.proofs.assembly_m12_ui import source_hashes, copy_source

REPO = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class NoPaid:
    offline_test_mode = True

    def __getattr__(self, name):
        raise AssertionError("E4 proof cannot invoke a paid launcher: " + name)


class ProofCore(CoreAdapter):
    def save_look_edit(self, source_root, delivery_id, values, destination=None, **kwargs):
        return super().save_look_edit(source_root, delivery_id, values, destination=destination,
                                     **{**kwargs, "tool_test": True})


def synthetic_assets(out):
    """Coarse meshes with no UVs, including a distinct transmissive frame material."""
    from glasses3d.tests import glbfab
    rows = []
    for name, crystal in (("synthetic-pair", False), ("synthetic-crystal", True)):
        parts = {key: (box, "temple" if role == "temple" else material, role)
                 for key, (box, material, role) in glbfab.default_parts().items()}
        mats = {"frame": {"name": "Frame", "pbrMetallicRoughness": {
                    "baseColorFactor": [.7, .8, .9, 1], "metallicFactor": 0, "roughnessFactor": .2}},
                "temple": {"name": "Temples", "pbrMetallicRoughness": {
                    "baseColorFactor": [.2, .3, .4, 1], "metallicFactor": 0, "roughnessFactor": .3}},
                "lens": {"name": "Optical", "pbrMetallicRoughness": {
                    "baseColorFactor": [.7, .8, .9, 1], "metallicFactor": 0, "roughnessFactor": .1},
                    "extensions": {"KHR_materials_transmission": {"transmissionFactor": .9}}}}
        if crystal:
            mats["frame"]["extensions"] = {"KHR_materials_transmission": {"transmissionFactor": .85}}
        path = out / (name + ".glb")
        path.write_bytes(glbfab.build(parts, materials=mats))
        rows.append({"label": name, "model": str(path), "synthetic": True})
    return rows


def fixture(source, out, label):
    """Import unmodified GLB bytes as a draft; all checks are real and reported."""
    inputs = out / "fixture-inputs" / label
    inputs.mkdir(parents=True)
    photo_dir = inputs / "images"
    photo_dir.mkdir()
    image = Image.new("RGB", (640, 360), "#eeeae3")
    ImageDraw.Draw(image).text((30, 150), "IMPORTED TEST FIXTURE\nNo product reference photos attached", fill="black")
    image.save(photo_dir / "fixture.png")
    views = inputs / "views.json"
    procs.write_json_atomic(views, {"fixture.png": {"kind": "front", "side": "both"}})
    root = jobs.create_job(images=photo_dir, views=views, name=label,
        description="Imported E4 test fixture only; no modeling or paid calls.", jobs_root=out / "jobs")
    model = root / "fixture.glb"
    shutil.copyfile(source, model)
    digest = sha(model)
    checkpoint = {"id": "fixture-glb", "verified": True, "fingerprint": digest, "sha256": digest,
                  "kind": "imported_fixture_glb", "file": "fixture.glb"}
    validation, seating = validate_spec(model), check_glb(model)
    seating["glb_sha256"] = digest
    printing = print_freshness.check_current_print(model, root)
    exp = {"path": str(model), "sha256": digest, "checkpoint": checkpoint["id"], "fingerprint": digest,
           "validation_sha256": digest, "validate_spec": validation, "seat_check": seating, "print_check": printing}
    attempt = {"status": "not_run", "exit_code": None, "export_sha256": digest, "fixed_scale": True,
               "appearance_policy": arview.AR_APPEARANCE_POLICY, "lens_env_intensity": arview.LENS_ENV_INTENSITY,
               "report": None, "images": [], "sheets": [], "reason": "Imported fixture; AR is exercised in the browser proof."}
    manifest, files = delivery._package(root, {"status": "stopped", "run_id": "e4-fixture"}, exp,
        checkpoint["id"], digest, checkpoint, "E4 copied test fixture, not an Astra delivery or product acceptance.",
        attempt, model.read_bytes(), {}, [], [], [], source=checkpoint, selection="head")
    delivery._publish(root, manifest, files)
    return root


def prepare(inputs, out):
    rows = json.loads(inputs.read_text(encoding="utf-8"))
    rows += synthetic_assets(out)
    prepared = []
    for row in rows:
        label = row["label"]
        print("Preparing " + label, flush=True)
        source = Path(row["model"]).resolve()
        if not row.get("synthetic"):
            row["original_sha256"] = sha(source)
        if row.get("job"):
            original = Path(row["job"]).resolve()
            root = out / "jobs" / original.name
            before = copy_source(original, root)
            row["original_files"] = before
            loaded = delivery.load_delivery(root, row["delivery_id"])
        else:
            root = fixture(source, out, label)
            loaded = delivery.load_delivery(root)
        assert sha(loaded["model"]) == sha(source)
        prepared.append({**row, "copy_root": str(root), "delivery_id": loaded["id"],
                         "copy_before": source_hashes(root), "copy_sha256": loaded["glb_sha256"]})
        procs.write_json_atomic(out / "prepared.json", prepared)
    return prepared


def save_expectation(root, loaded):
    """An allowed refusal comes from fresh source checks, never from the result."""
    checks = {"validate_spec": validate_spec(loaded["model"]),
              "print_check": print_freshness.check_current_print(loaded["model"], root)}
    failures = [name for name, result in checks.items() if result.get("ok") is not True]
    return {"save_expected": not failures, "save_refusal_checks": failures, "source_checks": checks}


def ar_evidence(loaded):
    """Retain the actual warnings, including an honest absent source capture."""
    summary = loaded["report_data"].get("arview") or {}
    capture = {}
    if summary.get("report"):
        capture = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
    def combined(key):
        result = list(summary.get(key) or [])
        for item in capture.get(key) or []:
            if item not in result:
                result.append(item)
        return result
    return {"status": summary.get("status"), "exit_code": summary.get("exit_code"),
            "capture_available": bool(capture), "warnings": combined("warnings"), "errors": combined("errors")}


def audit_saved(original, loaded, record):
    """Independent saved-value and preservation checks using retained evidence."""
    from glasses3d.agent.owner_lens_edit import _parse
    from glasses3d.agent.owner_look_edit import _application, read_materials
    assert loaded["report_data"]["tool_test"] is True
    assert loaded["report_data"]["accepted_finish"] is False
    assert loaded["report_data"]["validate_spec"]["ok"] is True
    assert loaded["report_data"]["print_check"]["ok"] is True
    before_doc, before_chunks = _parse(Path(original["model"]).read_bytes())
    after_doc, after_chunks = _parse(Path(loaded["model"]).read_bytes())
    assert len(before_chunks) == len(after_chunks)
    for (kind, raw, _), (after_kind, after_raw, _) in zip(before_chunks[1:], after_chunks[1:]):
        assert kind == after_kind
        assert after_raw[:len(raw)] == raw
    for array in ("accessors", "bufferViews", "images", "textures", "samplers"):
        before_items = before_doc.get(array, [])
        assert after_doc.get(array, [])[:len(before_items)] == before_items
    assert after_doc["nodes"] == before_doc["nodes"]
    assert len(after_doc["meshes"]) == len(before_doc["meshes"])
    for before_mesh, after_mesh in zip(before_doc["meshes"], after_doc["meshes"]):
        stripped = copy.deepcopy(after_mesh)
        assert len(before_mesh["primitives"]) == len(stripped["primitives"])
        for before_primitive, after_primitive in zip(before_mesh["primitives"], stripped["primitives"]):
            after_primitive["attributes"] = {key: value for key, value in after_primitive["attributes"].items()
                                           if key in before_primitive["attributes"]}
        assert stripped == before_mesh
    before_values = {row["material_index"]: row["values"] for row in read_materials(original["model"])["materials"]}
    after_values = {row["material_index"]: row["values"] for row in read_materials(loaded["model"])["materials"]}
    requested = {row["material_index"]: {**row["values"], "gradient": row["values"].get("gradient")}
                 for row in record["values"]}
    assert len(requested) == len(record["values"]), "Duplicate requested material"
    for index, values in requested.items():
        assert after_values[index] == values, f"Saved appearance differs from the owner request on material {index}"
    radial = record.get("radial_material_indices", [])
    if radial:
        application = _application(loaded)
        patch_values = application["owner_lens_edit"]["patch"]["values"]
        saved_entries = application["owner_look_edit"]["look"]["entries"]
        material_names = {row["material_index"]: row["material_name"]
                          for row in read_materials(loaded["model"])["materials"]}
        assert record["radial_reopened_exact"] is True
        for index in radial:
            spec = requested[index]["gradient"]
            assert spec["direction"] == "around_point"
            assert spec["centre"] == [0, 0] and spec["roundness"] == 1
            assert patch_values[str(index)]["gradient"] == spec
            matching = [entry for entry in saved_entries if entry["role"] == "lens"
                        and entry["material_name"] == material_names[index]]
            assert len(matching) == 1 and matching[0]["values"]["gradient"] == spec
    changed = {index for index, values in requested.items() if before_values[index] != values}
    assert len(before_doc["materials"]) == len(after_doc["materials"])
    for index, material in enumerate(before_doc["materials"]):
        if index not in changed:
            assert after_doc["materials"][index] == material, f"Unchanged material {index} was modified"
    source_ar, saved_ar = ar_evidence(original), ar_evidence(loaded)
    assert saved_ar["status"] == "ok" and saved_ar["exit_code"] == 0 and not saved_ar["errors"]
    comparison = {"source": source_ar, "saved": saved_ar,
        "new_warnings": [item for item in saved_ar["warnings"] if item not in source_ar["warnings"]],
        "resolved_warnings": [item for item in source_ar["warnings"] if item not in saved_ar["warnings"]],
        "comparison_available": source_ar["capture_available"]}
    return {"delivery_id": loaded["id"], "sha256": loaded["glb_sha256"],
        "existing_binary_ranges_unchanged": True, "original_geometry_normals_unchanged": True,
        "requested_appearance_values_exact": True, "unchanged_material_json_exact": True,
        "radial_receipt_and_reusable_look_exact": bool(radial),
        "radial_material_indices": radial,
        "changed_material_indices": sorted(changed), "ar": comparison,
        "checks": {key: loaded["report_data"][key] for key in ("validate_spec", "seating", "print_check", "arview")}}


def audit_refusal(record, expected):
    refusals = [item for item in record.get("refusals", []) if item.get("action") == "save"]
    assert not expected["save_expected"], f"Unexpected save failure for {record['label']}: {refusals}"
    assert len(refusals) == 1, "Missing or ambiguous save refusal evidence"
    message = str(refusals[0].get("message", "")).lower()
    matched = [check for check, phrase in (("validate_spec", "model specification check"),
                                         ("print_check", "retained-print check"))
               if check in expected["save_refusal_checks"] and phrase in message]
    assert matched, f"Save failed for a reason not established by source checks: {refusals}"
    return {"action": "save", "message": refusals[0]["message"], "source_failed_checks": matched,
            "expected_from_source_checks": True}


def audit_existing(out, prepared, name="audit.json"):
    """Read-only follow-up for a completed run; create only a NEW audit.json."""
    if Path(name).name != name or not name.startswith("audit") or not name.endswith(".json"):
        raise ValueError("Use a new audit*.json filename inside the evidence directory.")
    audit_path = out / name
    if audit_path.exists():
        raise ValueError("This proof already has audit.json; it will not be overwritten.")
    proof_path, browser_path = out / "proof.json", out / "browser.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    browser = json.loads(browser_path.read_text(encoding="utf-8"))
    result = {"schema": "g3d.e4_generality_audit/1", "started_at": procs.utcnow(),
              "read_only_existing_evidence": True, "api_requests": 0, "spend_usd": 0,
              "evidence": {name: sha(out / name) for name in ("proof.json", "browser.json", "prepared.json")},
              "models": []}
    def registry_record(key):
        from glasses3d.dev_ui.registry import validate_key
        path = out / "ui/registry/deliveries" / (validate_key(key) + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["key"] == key and Path(record["root"]).resolve().is_relative_to(out)
        return record
    try:
        assert proof["status"] in ("passed", "passed_with_refusals") and browser["status"] in ("passed", "passed_with_refusals")
        assert not browser["errors"] and not browser["external"]
        assert len(browser["models"]) == len(prepared)
        refusal_count = 0
        for row in prepared:
            assert source_hashes(row["copy_root"]) == row["copy_before"], row["label"]
            assert row.get("synthetic") or sha(row["model"]) == row["original_sha256"], row["label"]
            if row.get("job"):
                assert source_hashes(row["job"]) == row["original_files"], row["label"]
            original = delivery.load_delivery(row["copy_root"], row["delivery_id"])
            expected = save_expectation(row["copy_root"], original)
            record = next(item for item in browser["models"] if item["label"] == row["label"])
            assert record["opened"] and record["ar"]
            assert all(record["edits"].get(key) is True for key in ("solid", "see_through", "mirror", "shine", "shimmer",
                       "top_to_bottom", "left_to_right", "centre_to_sides"))
            if "around_point" in proof.get("features", []):
                assert all(record["edits"].get(key) is True for key in ("around_point", "radial_centre", "roundness"))
                assert record["radial_material_indices"]
            item = {"label": row["label"], "original_and_copied_source_unchanged": True, **expected}
            if record.get("saved_key"):
                rec = registry_record(record["saved_key"])
                saved = delivery.load_delivery(rec["root"], rec["delivery_id"])
                item["saved"] = audit_saved(original, saved, record)
                if row["label"] == "oakley-rerun":
                    from glasses3d.agent.owner_look_edit import read_saved_look
                    reapplication = proof["oakley_look_reapplication"]
                    replay_rec = registry_record(reapplication["delivery_key"])
                    replay = delivery.load_delivery(replay_rec["root"], replay_rec["delivery_id"])
                    reusable = read_saved_look(replay_rec["root"], replay_rec["delivery_id"])
                    native = reusable["native_source"]
                    assert Path(native["root"]).resolve() == Path(row["copy_root"]).resolve()
                    assert native["delivery_id"] == original["id"] and native["glb_sha256"] == original["glb_sha256"]
                    assert Path(replay["model"]).read_bytes() == Path(saved["model"]).read_bytes()
                    assert not reusable["skipped"] and not reapplication["skipped"]
                    item["look_reapplication"] = {"exact_saved_glb_bytes": True, "native_source": native,
                        "scope": "Tool test on unchanged copied native geometry; no Astra request"}
            else:
                item["save_refusal"] = audit_refusal(record, expected)
                item["source_ar"] = ar_evidence(original)
                refusal_count += 1
            result["models"].append(item)
        result.update(status="passed_with_refusals" if refusal_count else "passed", save_refusals=refusal_count)
    except BaseException as error:
        result.update(status="failed", error=str(error))
        raise
    finally:
        result["ended_at"] = procs.utcnow()
        assert all(sha(out / name) == digest for name, digest in result["evidence"].items()), "Existing proof evidence changed during audit"
        with audit_path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
    return result


def serve_and_prove(out, prepared, port):
    ensure_renderer_build()
    workers = []

    def launch(service, key):
        worker = threading.Thread(target=execute_task, args=(service, key), daemon=False)
        workers.append(worker)
        worker.start()

    app = create_app(Config(port=port, state_root=out / "ui", jobs_root=out / "jobs"),
        launcher=NoPaid(), core_adapter=ProofCore(), task_launcher=launch)
    service = app.state.service
    for row in prepared:
        row["delivery_key"] = service.register_delivery(Path(row["copy_root"]), row["delivery_id"],
                                                        job_id=Path(row["copy_root"]).name)
        row["save_expectation"] = save_expectation(row["copy_root"],
                                                   delivery.load_delivery(row["copy_root"], row["delivery_id"]))
    procs.write_json_atomic(out / "browser-config.json", {"base_url": f"http://127.0.0.1:{port}",
        "out": str(out), "models": [{"label": r["label"], "delivery_key": r["delivery_key"],
            **{key: r["save_expectation"][key] for key in ("save_expected", "save_refusal_checks")}} for r in prepared]})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started and thread.is_alive():
        time.sleep(.05)
    proof = {"schema": "g3d.e4_generality/1", "features": ["around_point"],
             "started_at": procs.utcnow(), "api_requests": 0, "spend_usd": 0}
    try:
        child = subprocess.run(["node", str(Path(__file__).with_suffix(".mjs")), str(out / "browser-config.json")],
                               cwd=REPO, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        browser = json.loads((out / "browser.json").read_text(encoding="utf-8"))
        proof["browser"] = browser
        audits = []
        for row in prepared:
            copied_unchanged = source_hashes(Path(row["copy_root"])) == row["copy_before"]
            original_unchanged = row.get("synthetic") or sha(row["model"]) == row["original_sha256"]
            if row.get("job"):
                original_unchanged &= source_hashes(row["job"]) == row["original_files"]
            assert copied_unchanged and original_unchanged, row["label"]
            record = next(item for item in browser["models"] if item["label"] == row["label"])
            saved = None
            original = delivery.load_delivery(row["copy_root"], row["delivery_id"])
            if record.get("saved_key"):
                rec = service.get("deliveries", record["saved_key"])
                loaded = delivery.load_delivery(rec["root"], rec["delivery_id"])
                saved = {"root": rec["root"], **audit_saved(original, loaded, record)}
                if row["label"] == "oakley-rerun":
                    from glasses3d.dev_ui.appearance_flow import reapply_owner_look
                    draft = service.new_draft(record["saved_key"])
                    assert draft["appearance_source"]["glb_sha256"] == loaded["glb_sha256"]
                    assert draft["source"]["glb_sha256"] == original["glb_sha256"]
                    # A native result is an independently authenticated delivery;
                    # this $0 tool test reapplies onto the copied original geometry.
                    reapplied = reapply_owner_look(service, draft["appearance_source"], row["delivery_key"],
                                                   out / "reapply")
                    replay = service.delivery(reapplied["delivery_key"])
                    assert replay["glb_sha256"] == loaded["glb_sha256"]
                    assert not reapplied["skipped"]
                    proof["oakley_look_reapplication"] = {**reapplied, "exact_saved_glb_bytes": True,
                        "native_shape_source_sha256": original["glb_sha256"], "patched_look_sha256": loaded["glb_sha256"],
                        "scope": "Tool test on unchanged copied native geometry; no Astra request"}
            else:
                audit_refusal(record, row["save_expectation"])
            audits.append({"label": row["label"], "original_unchanged": original_unchanged,
                           "copied_source_unchanged": copied_unchanged, "saved": saved,
                           "save_expectation": row["save_expectation"], "source_ar": ar_evidence(original)})
        proof["audits"] = audits
        assert child.returncode == 0, "Browser generality failed; see browser.json"
        proof["status"] = "passed_with_refusals" if any(item["saved"] is None for item in audits) else "passed"
    except BaseException as error:
        proof.update(status="failed", error=str(error))
        raise
    finally:
        for worker in workers:
            worker.join()
        server.should_exit = True
        thread.join(10)
        for state_path in (out / "ui").rglob("port-*.json"):
            state = procs.read_json(state_path)
            if isinstance(state, dict) and state.get("schema") == "arview.serve-state/1":
                review._stop_owned(state)
        proof["ended_at"] = procs.utcnow()
        procs.write_json_atomic(out / "proof.json", proof)
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8297)
    parser.add_argument("--phase", choices=("prepare", "browser", "audit", "all"), default="all")
    parser.add_argument("--audit-name", default="audit.json", help="New audit*.json filename; existing evidence is never overwritten")
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(REPO / "glasses3d/data/m12") or not 8290 <= args.port <= 8299:
        raise ValueError("Use an ignored private M12 directory and port 8290–8299.")
    if args.phase in ("prepare", "all"):
        if args.inputs is None:
            parser.error("--inputs is required when preparing new fixtures")
        out.mkdir(parents=True, exist_ok=False)
        prepared = prepare(args.inputs.resolve(), out)
    else:
        prepared = json.loads((out / "prepared.json").read_text(encoding="utf-8"))
    if args.phase in ("browser", "all"):
        serve_and_prove(out, prepared, args.port)
    elif args.phase == "audit":
        audit_existing(out, prepared, args.audit_name)


if __name__ == "__main__":
    main()
