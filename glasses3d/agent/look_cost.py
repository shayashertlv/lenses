"""Offline image-only counterfactuals anchored to saved receipts, with explicit cache assumptions.

The official Astra image formula below is distinct from history_replay's historical empirical
whole-payload calibration. No token-count endpoint, model client or network is used here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import shutil
from types import SimpleNamespace

from PIL import Image

from .history_replay import image_refs, restore_append_only, ReplayError
from .pricing import Tariff, validate_usage

VISION_SOURCE = "https://developers.openai.com/api/docs/guides/images-vision"
SOURCE_ACCESSED = "2026-10-15"


def documented_image(width, height, detail="high"):
    """Published 32px patch fitting; not a live measurement or a provider tokenizer."""
    if any(type(value) is not int or value <= 0 for value in (width, height)):
        raise ValueError("image dimensions must be positive integers")
    if detail not in {"high", "low", "original", "auto"}:
        raise ValueError("unsupported image detail")
    if detail != "low" and max(width, height) > 65535:
        raise ValueError("offline image estimate does not model source edges above 65535 pixels")
    original = [width, height]
    factor = min(1., (512 if detail == "low" else 65535) / max(width, height))
    width, height = max(1, round(width * factor)), max(1, round(height * factor))
    patches = math.ceil(width / 32) * math.ceil(height / 32)
    if detail == "high" and patches > 2500:
        factor = math.sqrt(32 ** 2 * 2500 / (width * height))
        if min(math.floor(width * factor / 32), math.floor(height * factor / 32)) < 1:
            raise ValueError("offline image estimate does not model this extreme aspect ratio")
        adjusted = factor * min(math.floor(width * factor / 32) / (width * factor / 32),
                                math.floor(height * factor / 32) / (height * factor / 32))
        width, height = max(1, math.floor(width * adjusted + 1e-9)), max(1, math.floor(height * adjusted + 1e-9))
        patches = math.ceil(width / 32) * math.ceil(height / 32)
        assert patches <= 2500
    return {"source_dimensions": original, "processed_dimensions": [width, height], "detail": detail,
            "patches": patches, "would_reject": patches > 30000,
            "tokens": None if patches > 30000 else (patches * 6 + 4) // 5}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def descriptor(path):
    path = Path(path)
    with Image.open(path) as image:
        dimensions = list(image.size)
        mime = Image.MIME[image.format]
    return {"sha256": _sha(path), "bytes": path.stat().st_size, "mime": mime}, dimensions


def token_total(request, manifest):
    total = 0
    for image in image_refs(request):
        row = manifest[image["image_url"]["sha256"]]
        estimate = documented_image(*row["dimensions"], image.get("detail", "auto"))
        if estimate["would_reject"]:
            raise ReplayError("an image exceeds its documented patch limit")
        total += estimate["tokens"]
    return total


def image_wire_bytes(request):
    """Only image blocks, with exact base64/data-URL length; archived text stays unchanged."""
    total = 0
    for image in image_refs(request):
        ref = image["image_url"]
        prefix = f"data:{ref['mime']};base64,"
        url_size = len(prefix) + 4 * ((ref["bytes"] + 2) // 3)
        stripped = {**image, "image_url": ""}
        total += len(json.dumps(stripped, ensure_ascii=False, separators=(",", ":")).encode("utf8")) + url_size
    return total


def usage_block(flat, *, tokens=None, cached=None, written=None, output=None):
    return {"input_tokens": flat["input_tokens"] if tokens is None else tokens,
            "input_tokens_details": {"cached_tokens": flat["cached_tokens"] if cached is None else cached,
                                     "cache_write_tokens": flat["cache_write_tokens"] if written is None else written},
            "output_tokens": flat["output_tokens"] if output is None else output,
            "output_tokens_details": {"reasoning_tokens": flat["reasoning_tokens"] if output is None else 0}}


def price_history(requests, usages, *, transform, manifest, sequences=None, tariff=None):
    """Preserve the observed non-image/cache partitions; added stable image prefixes are reusable.

First use of an image delta occupies the cache-write partition. On later requests in THIS
history, the prior delta is cache-read. Writes are not also billed as ordinary uncached input.
No per-image provider cache hit can be proved from aggregate historical usage.
"""
    if len(requests) != len(usages) or not requests:
        raise ValueError("requests and usages must have equal nonzero length")
    tariff = tariff or Tariff.frozen()
    rebuilt, rewrites = restore_append_only(requests, transform)
    previous_delta, rows = 0, []
    for seq, source, target, usage in zip(sequences or range(1, len(requests) + 1), requests, rebuilt, usages):
        flat = validate_usage(usage)
        before, after = token_total(source, manifest), token_total(target, manifest)
        delta = after - before
        if delta < previous_delta:
            raise ReplayError("added image delta shrank; this scenario needs an explicit cache partition model")
        tokens = flat["input_tokens"] + delta
        written_delta = delta - previous_delta
        block = usage_block(flat, tokens=tokens, cached=flat["cached_tokens"] + previous_delta,
                            written=flat["cache_write_tokens"] + written_delta)
        plain = flat["input_tokens"] - flat["cached_tokens"] - flat["cache_write_tokens"]
        observed = tariff.settle_micro(usage)
        stable = tariff.settle_micro(block)
        no_extra_reuse = tariff.settle_micro(usage_block(flat, tokens=tokens, written=flat["cache_write_tokens"] + delta))
        rows.append({"sequence": seq, "observed_input_tokens": flat["input_tokens"], "new_input_tokens_image_only": tokens,
                     "old_image_tokens": before, "new_image_tokens": after, "image_delta_tokens": delta,
                     "first_use_cache_write_delta_tokens": written_delta, "stable_cached_delta_tokens": previous_delta,
                     "observed_usage": flat, "new_usage_image_only": validate_usage(block),
                     "old_image_count": len(image_refs(source)), "new_image_count": len(image_refs(target)),
                     "image_wire_bytes_delta": image_wire_bytes(target) - image_wire_bytes(source),
                     "observed_micro_usd": observed, "stable_prefix_micro_usd": stable,
                     "stable_image_delta_micro_usd": stable - observed,
                     "added_images_never_reused_micro_usd": no_extra_reuse,
                     "no_cached_reads_all_writes_micro_usd": tariff.settle_micro(usage_block(flat, tokens=tokens, cached=0, written=tokens-plain)),
                     "no_cached_reads_all_plain_micro_usd": tariff.settle_micro(usage_block(flat, tokens=tokens, cached=0, written=0)),
                     "long_context": tokens > tariff.long_context_input_tokens,
                     "long_context_uplift_micro_usd": stable - replace(tariff, long_context_input_tokens=10**12).settle_micro(block)})
        previous_delta = delta
    return {"rows": rows, "observed_prefix_rewrites": rewrites, "replay_prefix_rewrites": [],
            "input_requests": rebuilt,
            "totals": {key: sum(row[key] for row in rows) for key in
                ("observed_micro_usd", "stable_prefix_micro_usd", "stable_image_delta_micro_usd",
                 "added_images_never_reused_micro_usd", "no_cached_reads_all_writes_micro_usd", "no_cached_reads_all_plain_micro_usd")},
            "peak_input_tokens_image_only": max(row["new_input_tokens_image_only"] for row in rows),
            "long_context_sequences": [row["sequence"] for row in rows if row["long_context"]]}


def measure(capture_manifest, *, research):
    """Reprice exact archived parent/child histories, deriving output images through the real SDK adapter."""
    from .budget import validate_ledger
    from .tools import _output, arview
    job, copy = Path(capture_manifest["job"]), Path(capture_manifest["copy_job"])
    run = job / "runs" / capture_manifest["run"]
    audit = next(row for row in research["runs"] if row["run"] == capture_manifest["run"] and row["job"] == job.name)
    ledger = _read(run / "budget.json")
    validate_ledger(ledger)
    assert _sha(run / "budget.json") == audit["budget_sha256"]
    source_manifest = deepcopy(audit["image_manifest"])
    repo = Path(__file__).resolve().parents[2]
    for digest, row in source_manifest.items():
        path = repo / row["path"]
        desc, size = descriptor(path)
        assert desc["sha256"] == digest and size == row["dimensions"] and desc["bytes"] == row["bytes"]
    captures = [_read(path) for path in capture_manifest["capture_files"]]
    by_call = {row["source"]["call_id"]: row for row in captures}
    requests = [_read(run / f"transport/{row['sequence']:04d}-request.json") for row in audit["requests"]]
    responses = [_read(run / f"transport/{row['sequence']:04d}-response.json") for row in audit["requests"]]
    for row, response in zip(audit["requests"], responses):
        assert _sha(run / f"transport/{row['sequence']:04d}-response.json") == row["response_file_sha256"]
        assert response["id"] == row["response_id"] and validate_usage(response["usage"]) == row["usage"]
        assert Tariff.frozen().settle_micro(response["usage"]) == row["settled_micro_usd"]
    image_manifest = source_manifest
    scene = SimpleNamespace(root=copy, s=SimpleNamespace(head={}), images=[], _emitted_images={}, _unseen=set())
    scene.record_images = lambda cid, result: scene.images.extend(deepcopy(result.get("images", [])))
    generated = {}

    def emit(call, *, finish=False):
        saved, source = by_call[call], by_call[call]["source"]
        result = deepcopy(saved["result"])
        result.update(call_id=call, checkpoint=source["checkpoint"], image_delivery="separate")
        scene.s.head = {"id": source["checkpoint"]}
        if finish:
            # Rebuild the same-current-export AR sheet from already rendered current viewer pixels.
            report_path = Path(result["worn_capture"]["report"])
            assert _sha(report_path) == result["worn_capture"]["report_sha256"]
            report = _read(report_path)
            assert report["input"]["sha256"] == source["export_sha256"]
            directory = copy / "work/final-ar"
            directory.mkdir(parents=True, exist_ok=True)
            for image in report["images"]:
                path = report_path.parent / image["file"]
                assert _sha(path) == image["sha256"]
                target = directory / image["file"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            worn = [r for r in report["images"] if r.get("kind") == "worn_crop"]
            sheet = arview.contact_sheet(directory, worn + [r for r in report["images"] if r.get("kind") != "worn_crop"])
            result["images"].insert(0, {"path": str(sheet), "view": "AR standard poses, worn sides, portrait and checker"})
        sdk = _output(scene, call, source["tool"], result)
        sent, records = [], []
        for image in result["images"]:
            path = copy / image["path"]
            desc, dimensions = descriptor(path)
            assert desc["sha256"] == image["sha256"]
            estimate = documented_image(*dimensions, "high")
            image_manifest[desc["sha256"]] = {"path": str(path), "bytes": desc["bytes"], "dimensions": dimensions,
                                             "documented_high": estimate}
            rec = {**image, "bytes": desc["bytes"], "dimensions": dimensions, "documented_high": estimate}
            records.append(rec)
            if image["output_index"] is not None:
                assert sdk[image["output_index"]].type == "image" and sdk[image["output_index"]].detail == "high"
                sent.append({"type": "input_image", "detail": "high", "image_url": desc})
        assert len(sent) == sum(part.type == "image" for part in sdk)
        generated[call] = {"call_id": call, "tool_sequence": source["tool_sequence"], "tool": source["tool"],
            "checkpoint": source["checkpoint"], "export_sha256": source["export_sha256"], "copy_model": saved["copy_model"],
            "basis": source["basis"], "refs": [row["ref"] for row in result["views"]], "records": records,
            "raw_images": len(records), "delivered_images": len(sent), "deduplicated_images": len(records)-len(sent),
            "delivered_image_tokens": sum(documented_image(*image_manifest[i["image_url"]["sha256"]]["dimensions"], "high")["tokens"] for i in sent),
            "source_capture_receipt": next(path for path in capture_manifest["capture_files"] if _read(path)["source"]["call_id"] == call)}
        return sent

    def transform(item):
        if item.get("call_id") not in by_call:
            for image in image_refs(item):
                scene._emitted_images[image["image_url"]["sha256"]] = item["call_id"]
            return item
        sent = emit(item["call_id"])
        old_tokens = token_total(item, image_manifest)
        parts, inserted = [], False
        for part in item["output"]:
            if part.get("type") == "input_image":
                if not inserted:
                    parts.extend(sent)
                    inserted = True
            else:
                parts.append(part)
        if not inserted:
            parts.extend(sent)
        item["output"] = parts
        generated[item["call_id"]].update(old_consumed_image_tokens=old_tokens,
            image_delta_tokens=token_total(item, image_manifest)-old_tokens)
        return item

    groups = {}
    for index, (req, row) in enumerate(zip(requests, audit["requests"])):
        group = hashlib.sha256(req["instructions"].encode()).hexdigest()
        groups.setdefault(group, {"stage": row["stage"], "indices": []})["indices"].append(index)
    histories, all_rows, parent_last = [], [], None
    for group, meta in groups.items():
        indices = meta["indices"]
        result = price_history([requests[i] for i in indices], [responses[i]["usage"] for i in indices],
            transform=transform if meta["stage"] == "parent" else lambda item: item,
            manifest=image_manifest, sequences=[i+1 for i in indices])
        if meta["stage"] == "parent":
            parent_last = result["input_requests"][-1]
        result.pop("input_requests")
        result.update(stage=meta["stage"], history_hash=group)
        for row in result["rows"]:
            row["stage"] = meta["stage"]
            row["estimated_wire_bytes_image_only"] = audit["requests"][row["sequence"]-1]["serialized_request_bytes"] + row["image_wire_bytes_delta"]
        all_rows.extend(result["rows"])
        histories.append(result)
    all_rows.sort(key=lambda row: row["sequence"])
    final = next(row for row in captures if row["source"]["tool"] == "finish")
    finish_call = final["source"]["call_id"]
    assert not any(item.get("call_id") == finish_call and item.get("type") == "function_call_output" for req in requests for item in req["input"])
    sent = emit(finish_call, finish=True)
    final_images = {"input": [{"type": "function_call_output", "call_id": finish_call, "output": sent}]}
    added = token_total(final_images, image_manifest)
    last = all_rows[-1]
    lower_input = last["new_input_tokens_image_only"] + added
    second = {"not_in_historical_81_requests": True, "new_image_tokens": added, "new_image_count": len(sent),
        "image_wire_bytes": image_wire_bytes(final_images), "anchor_input_plus_images_only": lower_input,
        "omitted_nonimage_input": "Actual prior assistant response/opaque reasoning plus new finish text and procedure/tool schema changes; not token-counted offline.",
        "new_response_output_tokens": None, "new_response_output_cost_usd": None,
        "image_only_cache_write_micro_usd_at_anchor": Tariff.frozen()._cost(added, Tariff.frozen().cache_write_per_million,
            2 if lower_input > 272000 else 1),
        "anchor_crosses_272k": lower_input > 272000, "anchor_margin_to_272k_tokens": 272000-lower_input}
    totals = {key: sum(row[key] for row in all_rows) for key in histories[0]["totals"]}
    assert totals["observed_micro_usd"] == audit["aggregate"]["all"]["settled_micro_usd"]
    for call, item in generated.items():
        if item["tool"] == "finish":
            item["historical_requests_consuming_output"] = []
            continue
        consumed = [row["sequence"] for row in all_rows if row["stage"] == "parent" and row["sequence"] > item["tool_sequence"]]
        item["historical_requests_consuming_output"] = consumed
        delta = item["image_delta_tokens"]
        first = all_rows[consumed[0]-1]
        item["first_use_image_delta_cache_write_micro_usd"] = Tariff.frozen()._cost(delta, 12_500_000, 2 if first["long_context"] else 1)
        item["each_later_read_image_delta_micro_usd_normal_context"] = delta
    summary = {**totals, "actual_spend_usd": 0, "provider_calls": 0, "historical_requests": len(all_rows),
        "peak_input_tokens_image_only": max(row["new_input_tokens_image_only"] for row in all_rows),
        "long_context_sequences": [row["sequence"] for row in all_rows if row["long_context"]],
        "peak_image_count": max(row["new_image_count"] for row in all_rows),
        "peak_wire_bytes_image_only": max(row["estimated_wire_bytes_image_only"] for row in all_rows),
        "prefix_rewrites": sum(len(h["replay_prefix_rewrites"]) for h in histories),
        "second_finish": second}
    assert all(_sha(path) == digest for path, digest in capture_manifest["source_files"].items())
    return {"schema": "g3d.look_image_cost/1", "summary": summary, "histories": histories,
        "look_calls": list(generated.values()), "image_manifest": image_manifest, "source_unchanged": True,
        "sources": {"vision": VISION_SOURCE, "accessed": SOURCE_ACCESSED, "audit_budget_sha256": audit["budget_sha256"]},
        "assumptions": ["Image-only counterfactual; actual saved provider input totals, output tokens and non-image text are anchors.",
            "Each current look output is admitted once at its historical call; all following history preserves those bytes. Parent and child cache histories are separate.",
            "Astra high images use documented patch fitting and ceil(patches*1.2); no empirical +112 term.",
            "Stable-prefix scenario preserves historical plain/read/write partitions and adds new images first to cache writes, then to cache reads; write is a separate partition, not an extra fee.",
            "Provider cache hits for these counterfactual images were not measured. No-cache scenarios are sensitivities, not future-run predictions or budget bounds.",
            "Instruction/tool-schema changes, new panel labels/metadata and changed model decisions are excluded from the image-only delta.",
            "The old final finish output was never consumed. New second-finish input images are separate; its unknown output tokens/cost are not invented.",
            "Documentation notes possible one-token image billing rounding differences."]}
