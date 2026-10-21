"""Real $0 lens editor proof on a private copy; never uses a paid launcher."""
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

import uvicorn

from glasses3d.agent import delivery, owner_lens_edit, procs, review
from glasses3d.dev_ui.contracts import Config
from glasses3d.dev_ui.core_adapter import CoreAdapter
from glasses3d.dev_ui.lens_renderer import ensure_renderer_build
from glasses3d.dev_ui.server import create_app
from glasses3d.dev_ui.tasks import execute_task


REPO = Path(__file__).resolve().parents[3]


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.name.casefold().startswith(".env")}


def unrelated_material(material):
    """Independently retain everything except the documented editable leaves."""
    result = copy.deepcopy(material)
    pbr = result.get("pbrMetallicRoughness", {})
    alpha = pbr.get("baseColorFactor", [1, 1, 1, 1])[3]
    for field in ("baseColorFactor", "metallicFactor", "roughnessFactor"):
        pbr.pop(field, None)
    if not pbr:
        result.pop("pbrMetallicRoughness", None)
    extensions = result.get("extensions", {})
    for name, fields in {
        "KHR_materials_transmission": ("transmissionFactor",),
        "KHR_materials_ior": ("ior",),
        "KHR_materials_iridescence": ("iridescenceFactor", "iridescenceIor",
            "iridescenceThicknessMinimum", "iridescenceThicknessMaximum"),
    }.items():
        extension = extensions.get(name, {})
        for field in fields:
            extension.pop(field, None)
        if not extension:
            extensions.pop(name, None)
    if not extensions:
        result.pop("extensions", None)
    return result, alpha


class NoPaid:
    offline_test_mode = True

    def __getattr__(self, name):
        raise AssertionError("This lens proof cannot call a paid launcher: " + name)


class ProofCore(CoreAdapter):
    def save_lens_edit(self, source_root, delivery_id, values, destination=None):
        return owner_lens_edit.save_lens_edit(source_root, delivery_id, values, destination=destination, tool_test=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=REPO / "glasses3d/data/jobs/oakley-oo9208-v2-911a2b0e")
    parser.add_argument("--port", type=int, default=8297)
    args = parser.parse_args()
    out, source = args.out.resolve(), args.source.resolve()
    if (not 8290 <= args.port <= 8299 or out.exists() or not out.is_relative_to(REPO / "glasses3d/data/m12")
            or out.is_relative_to(source) or source.is_relative_to(out)):
        raise ValueError("Use a new private M12 proof folder and port 8290–8299")
    status = (REPO / "glasses3d/data/review_20261018/codex/task18_status.txt").read_text(encoding="utf-8")
    if not any(line.startswith("exit") for line in status.splitlines()):
        raise ValueError("Task 18 must exit before the native proof")
    original = delivery.load_delivery(source)
    original_hashes = {str(source): hashes(source)}
    out.mkdir(parents=True)
    copy_root = out / "jobs" / source.name
    copy_root.mkdir(parents=True)
    shutil.copyfile(source / "job.json", copy_root / "job.json")
    shutil.copytree(source / "images", copy_root / "images")
    if (source / "reference_images").is_dir():
        shutil.copytree(source / "reference_images", copy_root / "reference_images")
    destination = copy_root / "deliveries" / original["id"]
    shutil.copytree(Path(original["model"]).parent, destination)
    shutil.copyfile(source / "deliveries/current.json", copy_root / "deliveries/current.json")
    copied = delivery.load_delivery(copy_root)
    assert copied["glb_sha256"] == original["glb_sha256"]
    before = hashes(copy_root)
    ensure_renderer_build()
    config = Config(port=args.port, state_root=out / "ui", jobs_root=out / "jobs")
    workers = []

    def launch(service, key):
        worker = threading.Thread(target=execute_task, args=(service, key), daemon=False)
        workers.append(worker)
        worker.start()

    app = create_app(config, launcher=NoPaid(), core_adapter=ProofCore(), task_launcher=launch)
    service = app.state.service
    key = service.register_delivery(copy_root, original["id"], job_id=source.name)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started and thread.is_alive():
        time.sleep(.05)
    proof = {"schema": "g3d.lens_colour_e2e/1", "started_at": procs.utcnow(), "source_sha256": copied["glb_sha256"],
             "api_requests": 0, "actual_spend_usd": 0, "task18_status": status, "originals": original_hashes}
    try:
        browser_config = out / "browser-config.json"
        procs.write_json_atomic(browser_config, {"base_url": f"http://127.0.0.1:{args.port}",
                                                "delivery_key": key, "out": str(out)})
        result = subprocess.run(["node", str(Path(__file__).with_suffix(".mjs")), str(browser_config)], cwd=REPO)
        if result.returncode:
            raise RuntimeError(f"Real browser proof failed with exit {result.returncode}")
        browser = json.loads((out / "browser.json").read_text(encoding="utf-8"))
        edited = service.get("deliveries", browser["delivery_key"])
        loaded = delivery.load_delivery(edited["root"], edited["delivery_id"])
        assert loaded["report_data"]["tool_test"] is True
        assert loaded["report_data"]["accepted_finish"] is False
        assert before == hashes(copy_root)
        for path, expected in original_hashes.items():
            path = Path(path)
            assert (hashes(path) if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()) == expected
        before_doc, before_chunks = owner_lens_edit._parse(Path(copied["model"]).read_bytes())
        after_doc, after_chunks = owner_lens_edit._parse(Path(loaded["model"]).read_bytes())
        assert [raw for _, _, raw in before_chunks[1:]] == [raw for _, _, raw in after_chunks[1:]]
        edited_indices = {row["material_index"] for row in browser["values"]}
        saved_values = {row["material_index"]: row["values"]
                        for row in owner_lens_edit.read_lens_materials(loaded["model"])["materials"]}
        for row in browser["values"]:
            assert saved_values[row["material_index"]] == row["values"]
        application = json.loads((Path(loaded["report"]).parent / "owner-applied/report.json").read_text(encoding="utf-8"))
        assert application["owner_lens_edit"]["patch"]["values"] == {
            str(row["material_index"]): row["values"] for row in browser["values"]}
        for index, material in enumerate(before_doc["materials"]):
            if index not in edited_indices:
                assert material == after_doc["materials"][index]
            else:
                assert unrelated_material(material) == unrelated_material(after_doc["materials"][index])
        original_extensions = before_doc.get("extensionsUsed", [])
        saved_extensions = after_doc.get("extensionsUsed", [])
        assert saved_extensions[:len(original_extensions)] == original_extensions
        assert set(saved_extensions[len(original_extensions):]) <= {
            "KHR_materials_transmission", "KHR_materials_ior", "KHR_materials_iridescence"}
        for name in set(before_doc) | set(after_doc):
            if name not in ("materials", "extensionsUsed"):
                assert before_doc.get(name) == after_doc.get(name)
        proof.update(status="passed", delivery_root=edited["root"], delivery_id=loaded["id"],
                     glb_sha256=loaded["glb_sha256"], source_unchanged=True, non_lens_json_unchanged=True,
                     unrelated_lens_json_unchanged=True, binary_chunks_unchanged=True,
                     saved_values_match_request=True, browser=browser,
                     before_ar=copied["report_data"]["arview"], after_ar=loaded["report_data"]["arview"])
    except BaseException as error:
        proof.update(status="failed", error=str(error))
        raise
    finally:
        for worker in workers:
            worker.join()
        server.should_exit = True
        thread.join(10)
        # Stop only proof-owned AR instances after verifying their live identity.
        for state_path in (out / "ui").rglob("port-*.json"):
            state = procs.read_json(state_path)
            if isinstance(state, dict) and state.get("schema") == "arview.serve-state/1":
                review._stop_owned(state)
        proof["ended_at"] = procs.utcnow()
        procs.write_json_atomic(out / "proof.json", proof)
        print(json.dumps({k: proof[k] for k in ("status", "started_at", "ended_at")}))


if __name__ == "__main__":
    main()
