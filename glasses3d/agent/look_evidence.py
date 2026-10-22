"""Reuse only intact, complete looks whose exact delivered bytes remain in context.

This is an optional in-memory optimization. Missing receipts or a process restart
take the ordinary fresh-capture path; they never relax a finish requirement.
"""
from __future__ import annotations

from copy import deepcopy
import json

from .instruction_profiles import LEGACY_PROFILE, resolve_profile


def _enabled(scene):
    return resolve_profile(getattr(scene, "instruction_profile", None)) != LEGACY_PROFILE


def _unseen(scene):
    return getattr(scene, "_unseen", getattr(scene, "unseen", ()))


def _paths(value):
    """Every native panel/composed-part path must have an immutable asset receipt."""
    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            yield value["path"]
        for child in value.values():
            yield from _paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from _paths(child)


def _files(scene, look, delivered_images):
    from .tools import arview

    files = {}

    def verify(path, digest):
        target = arview.confined(scene.root, path)
        if not isinstance(digest, str) or arview.sha256(target) != digest:
            raise ValueError("look evidence bytes changed")
        if str(target) in files and files[str(target)] != digest:
            raise ValueError("look evidence has conflicting file identities")
        files[str(target)] = digest
        return target

    report = verify(look["report"], look["report_sha256"])
    saved = json.loads(report.read_text(encoding="utf-8"))
    # The hash-bound source report precedes the SDK's image-transport metadata.
    for key, value in saved.items():
        if json.dumps(look.get(key), sort_keys=True, allow_nan=False) != json.dumps(value, sort_keys=True, allow_nan=False):
            raise ValueError("delivered look differs from its immutable source report")
    verify(look["export_path"], look["export_sha256"])
    assets = look["assets"]
    if not isinstance(assets, list) or not assets:
        raise ValueError("look has no immutable asset receipts")
    asset_paths = {str(verify(row["path"], row["sha256"])) for row in assets}
    required = {*_paths(look["images"]), *_paths(look["views"]), look["worn_front_path"]}
    required.update(look.get("capture_reports", []))
    if not {str(arview.confined(scene.root, path)) for path in required} <= asset_paths:
        raise ValueError("look asset receipts do not cover every source panel")
    if not delivered_images or len(delivered_images) != len(look["images"]):
        raise ValueError("look transport does not contain every source image")
    for source, record in zip(look["images"], delivered_images):
        source_path = arview.confined(scene.root, source["path"])
        if (arview.confined(scene.root, record["source_path"]) != source_path
                or record["source_sha256"] != files[str(source_path)]):
            raise ValueError("look transport has different source pixels")
        verify(record["path"], record["sha256"])
    return files


def delivered(scene, name, evidence_result, *, delivered_images=None):
    """Remember a complete look only after the SDK output and all receipts succeed."""
    if not _enabled(scene) or name != "compare_look_to_photos":
        return
    from .tools import finish_review as review

    look = evidence_result
    call_id = look.get("call_id")
    if not call_id or call_id in _unseen(scene) or look.get("status") != "ok":
        return
    try:
        binding = review.evidence(scene.root, scene.s.head, {"sha256": look["export_sha256"]})
        if (not binding["photos"] or review._capture_complete(binding, look)
                or look.get("checkpoint") != binding["checkpoint"]):
            return
        files = _files(scene, look, delivered_images)
    except (OSError, ValueError, KeyError, TypeError):
        return  # Incomplete optional provenance must never fail an otherwise valid tool.
    cached = {"call_id": call_id, "run_id": getattr(scene, "active_run_id", None),
              "binding": deepcopy(binding), "look": deepcopy(look), "files": files,
              "image_sha256": [row["sha256"] for row in delivered_images]}
    previous = getattr(scene, "_delivered_full_looks", {})
    scene._delivered_full_looks = {**previous, call_id: cached}


def reusable(scene, export):
    """Return a verified source look or None, never a weaker review or refusal."""
    if not _enabled(scene):
        return None
    from .tools import arview, finish_review as review

    present = getattr(scene, "_segment_images", {})
    if not present:
        return None
    try:
        current = review.evidence(scene.root, scene.s.head, export)
        if arview.sha256(arview.confined(scene.root, export["path"])) != export["sha256"]:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    for cached in reversed(list(getattr(scene, "_delivered_full_looks", {}).values())):
        if (cached["call_id"] in _unseen(scene) or cached["binding"] != current
                or cached["run_id"] != getattr(scene, "active_run_id", None)
                or any(sha not in present or present[sha].get("call_id") in _unseen(scene)
                       for sha in cached["image_sha256"])):
            continue
        try:
            if any(arview.sha256(arview.confined(scene.root, path)) != digest
                   for path, digest in cached["files"].items()):
                continue
            if review._capture_complete(current, cached["look"]):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        result = deepcopy(cached["look"])
        result["evidence_reuse"] = {
            "call_id": cached["call_id"], "report_sha256": result["report_sha256"],
            "image_count": len(cached["image_sha256"]),
            "delivery_calls": sorted({present[sha]["call_id"] for sha in cached["image_sha256"]}),
            "reason": "Unchanged full look; its exact image bytes are still in this conversation segment."}
        return result
    return None
