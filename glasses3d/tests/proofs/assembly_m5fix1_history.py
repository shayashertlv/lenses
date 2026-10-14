"""$0 saved-request replay through the actual compact image output implementation.

Example (from repository root):
  python -m glasses3d.tests.proofs.assembly_m5fix1_history --job glasses3d/data/jobs/JOB \
    --run-id RUN --out glasses3d/data/assembly/m5fix1-history
No Blender/model/network call. Writes only a new proof directory, never original job data.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from PIL import Image

from glasses3d.agent.history_replay import image_refs, reprice, restore_append_only
from glasses3d.agent.pricing import Tariff, validate_usage
from glasses3d.agent.tools import _output
from glasses3d.agent.tools.images import IMAGE_POLICY, MAX_IMAGE_SIDE


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_proof(job: Path, run_id: str, out: Path):
    job, out = job.resolve(), out.resolve()
    if out.exists() or out.is_relative_to(job):
        raise ValueError("proof needs a new output directory outside the original job")
    if Path(run_id).name != run_id or run_id in (".", ".."):
        raise ValueError("invalid run identifier")
    source = job / "runs" / run_id
    inputs = {}

    def read(path):
        if not path.resolve().is_relative_to(job) or path.name.endswith(".spec.json") or path.name.startswith(".env"):
            raise ValueError("input path outside original job or forbidden")
        inputs[str(path)] = sha(path)
        return json.loads(path.read_text(encoding="utf8"))

    budget = read(source / "budget.json")
    intake = read(source / "input-receipt.json")
    photos = {rec["sha256"] for rec in intake["references"]}
    if len(photos) != len(intake["references"]):
        raise ValueError("reference hashes must be unique")
    requests, usages = [], []
    for row in budget["rows"]:
        number = row["sequence"]
        if number != len(requests) + 1 or row["outcome"] != "settled":
            raise ValueError("proof requires contiguous completed requests")
        requests.append(read(source / f"transport/{number:04d}-request.json"))
        response = read(source / f"transport/{number:04d}-response.json")
        if validate_usage(response["usage"]) != row["usage"] or response["id"] != row["response_id"]:
            raise ValueError("provider response and budget differ")
        usages.append(response["usage"])
    out.mkdir(parents=True)
    scratch = out / "scratch-job"
    scratch.mkdir()
    scene = SimpleNamespace(root=scratch, s=SimpleNamespace(head={"id": "unknown"}), images=[])
    scene.record_images = lambda call_id, result: scene.images.extend(result.get("images", []))
    dimensions_by_sha, compact_blobs, transformed, transformed_receipts = {}, {}, {}, []

    def dimensions(ref):
        digest = ref["sha256"]
        if digest not in dimensions_by_sha:
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[ref["mime"]]
            path = job / "blobs" / (digest + extension)
            if sha(path) != digest or path.stat().st_size != ref["bytes"]:
                raise ValueError("source image blob hash/size mismatch")
            inputs[str(path)] = digest
            with Image.open(path) as image:
                dimensions_by_sha[digest] = image.size
        return dimensions_by_sha[digest]

    # Restore first-seen outputs without applying the one-image check to the old policy.
    first_outputs = {}
    for request in requests:
        for item in request["input"]:
            if item.get("type") == "function_call_output":
                first_outputs.setdefault(item["call_id"], deepcopy(item))
    for call_id, original in first_outputs.items():
        if not image_refs(original):
            transformed[call_id] = original
            continue
        parts = original["output"]
        primary = json.loads(parts[0]["text"])
        sources = primary.get("images")
        if not sources or len(sources) != len(image_refs(original)):
            raise ValueError("saved tool images lack unambiguous source metadata")
        records = []
        for old in sources:
            relative = Path(old["source_path"])
            path = (job / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(job) or relative.parts[0] not in ("images", "reference_images", "work", "cameras"):
                raise ValueError("unsafe source image path")
            if sha(path) != old["source_sha256"]:
                raise ValueError("original source image changed")
            inputs[str(path)] = old["source_sha256"]
            copied = scratch / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            if not copied.exists():
                shutil.copyfile(path, copied)
            records.append({"path": relative.as_posix(), "region": old.get("region"), "view": old["view"],
                            "kind": old["kind"], "checkpoint": old["checkpoint"]})
        kind = sources[0]["kind"]
        scene.s.head = {"id": primary["checkpoint"]}
        minimum = {"status": primary["status"], "call_id": call_id, "checkpoint": primary["checkpoint"], "images": records}
        actual = _output(scene, call_id, kind, minimum)
        metadata = json.loads(actual[0].text)
        primary["images"] = metadata["images"]
        primary["image_policy"] = metadata["image_policy"]
        replacement = [{"type": "input_text", "text": json.dumps(primary, ensure_ascii=False, separators=(",", ":"))}]
        for part in actual[1:]:
            if part.type == "text":
                replacement.append({"type": "input_text", "text": part.text})
            elif part.type == "image":
                payload = base64.b64decode(part.image_url.split(",", 1)[1], validate=True)
                digest = hashlib.sha256(payload).hexdigest()
                path = out / "compact-blobs" / (digest + ".png")
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(payload)
                with Image.open(path) as image:
                    dimensions_by_sha[digest] = image.size
                    if max(image.size) > MAX_IMAGE_SIDE:
                        raise AssertionError("runtime compact image exceeds policy size")
                compact_blobs[digest] = str(path.relative_to(out))
                replacement.append({"type": "input_image", "image_url": {"sha256": digest, "bytes": len(payload), "mime": "image/png"}, "detail": part.detail})
            else:
                raise ValueError("unexpected runtime output type")
        # Preserve footers/other diagnostics; only replace image labels and attachments.
        replacement.extend(deepcopy(part) for part in parts[1:] if part.get("type") == "input_text" and not part.get("text", "").startswith("Image: call "))
        transformed[call_id] = {**deepcopy(original), "output": replacement}
        transformed_receipts.append({"call_id": call_id, "kind": kind, "source_panels": len(sources),
                                     "emitted_images": len(image_refs(transformed[call_id])), "metadata": metadata["images"]})
    transform = lambda item: deepcopy(transformed[item["call_id"]])
    report = reprice(requests, usages, photos=photos, transform=transform, dimensions=dimensions,
                     tariff=Tariff.from_dict(budget["tariff"]))
    sensitivity = reprice(requests, usages, photos=photos, transform=transform, dimensions=dimensions,
                         tariff=Tariff.from_dict(budget["tariff"]), text_overhead_per_tool=1000)
    rebuilt, _ = restore_append_only(requests, transform)
    report["metadata_sensitivity_1000_extra_chars_per_tool"] = {key: sensitivity[key] for key in
        ("stable_prefix_micro_usd", "zero_cache_micro_usd", "maximum_estimated_input_tokens", "long_context_requests")}
    report["image_policy"] = IMAGE_POLICY
    report["compact_blobs"] = compact_blobs
    report["tool_image_outputs"] = transformed_receipts
    report["prefix_sha256"] = [hashlib.sha256(json.dumps(r["input"], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest() for r in rebuilt]
    report["source"] = {"job": str(job), "run_id": run_id}
    report["observed_ledger_micro_usd"] = sum(row["settled_micro_usd"] for row in budget["rows"])
    if report["observed_micro_usd"] != report["observed_ledger_micro_usd"]:
        raise AssertionError("saved usage repricing differs from ledger")
    report["six_photos_verified"] = len(photos) == 6 and report["all_photos_every_request"]
    report["sources_unchanged"] = all(sha(Path(path)) == digest for path, digest in inputs.items())
    report["source_hashes"] = inputs
    if not report["sources_unchanged"]:
        raise AssertionError("proof altered original evidence")
    (out / "proof.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = run_proof(args.job, args.run_id, args.out)
    print(json.dumps({key: report[key] for key in ("requests", "observed_micro_usd", "stable_prefix_micro_usd",
        "zero_cache_micro_usd", "observed_prefix_rewrites", "replay_prefix_rewrites", "six_photos_verified",
        "maximum_estimated_input_tokens", "long_context_requests", "estimated_input_decreases", "sources_unchanged",
        "cumulative_max_adjustment_tokens",
        "metadata_sensitivity_1000_extra_chars_per_tool")}, indent=2))


if __name__ == "__main__":
    main()
