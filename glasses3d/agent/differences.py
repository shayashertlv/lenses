"""Run-scoped, append-only decisions about visible differences; no likeness score.

Only successfully delivered look/review calls can support an update. The host
checks their immutable identities; Astra supplies the visual judgement.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Literal

from agents.tool_context import ToolContext

from .jsonl import append_jsonl, read_jsonl

SCHEMA = "g3d.differences/1"


def enabled(scene):
    return getattr(scene, "instruction_profile", "decision-20261021") != "legacy-20261021"


def _path(scene):
    run_id = getattr(scene, "active_run_id", None)
    if not run_id:
        return None
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id) or run_id in {".", ".."}:
        raise ValueError("differences require a valid host run identity")
    log = getattr(scene, "_review_log", None)
    if log is not None:
        if log.run_id != run_id:
            raise ValueError("differences run differs from the host review log")
        directory = Path(log.run_dir)
    else:
        directory = Path(scene.root) / "runs" / run_id
    return directory / "differences.jsonl"


def _records(scene):
    path = _path(scene)
    read = read_jsonl(path) if path is not None else None
    if read is not None and read.recovery:
        raise ValueError("differences journal has a torn tail; preserve and recover it before continuing")
    rows = read.rows if read is not None else []
    for index, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or row.get("schema") != SCHEMA
                or row.get("run_id") != scene.active_run_id or row.get("sequence") != index
                or row.get("kind") not in {"evidence", "difference"}):
            raise ValueError("invalid differences journal record")
    return rows


def _append(scene, kind, **fields):
    path = _path(scene)
    if path is None:
        raise ValueError("record_difference requires an active host run")
    rows = _records(scene)
    append_jsonl(path, {"schema": SCHEMA, "run_id": scene.active_run_id,
                        "sequence": len(rows) + 1, "kind": kind, **fields})


def snapshot(scene):
    """Current factual list; earlier versions remain untouched in the journal."""
    items = {}
    for row in _records(scene):
        if row["kind"] == "difference":
            item = row.get("item")
            if not isinstance(item, dict) or not item.get("id") or item.get("status") not in {"open", "fixed", "limitation"}:
                raise ValueError("invalid differences item")
            items[item["id"]] = dict(item)
    return list(items.values())


def formal_best(scene):
    """Only a best checkpoint formally recorded for this run is a current best."""
    run_id = getattr(scene, "active_run_id", None)
    best_run = getattr(scene, "best_checkpoint_run_id", None)
    return {"checkpoint": getattr(scene, "best_checkpoint", None) if run_id and best_run == run_id else None,
            "run_id": run_id}


def _unseen(scene):
    return getattr(scene, "_unseen", getattr(scene, "unseen", ()))


def register_evidence(scene, name, result):
    """Called after image/text delivery and the normal host call receipt succeed."""
    if not enabled(scene) or _path(scene) is None or name not in {"compare_look_to_photos", "finish"}:
        return
    cid = result.get("call_id")
    if not cid or cid in _unseen(scene):
        return
    from .tools import finish_review as review, arview
    look = result.get("look_comparison", {}) if name == "finish" else result
    if look.get("status") != "ok":
        return
    binding = review.evidence(scene.root, scene.s.head, {"sha256": look.get("export_sha256")})
    # A look may intentionally compare a subset; only that subset can support an item.
    refs = {row.get("ref") for row in look.get("views", [])}
    binding["photos"] = [photo for photo in binding["photos"] if photo["ref"] in refs]
    if not binding["photos"] or review._capture_complete(binding, look):
        return
    if (look.get("checkpoint", binding["checkpoint"]) != binding["checkpoint"]
            or not binding.get("export_sha256")):
        return
    export_path = (result.get("export") or {}).get("path") if name == "finish" else look.get("export_path")
    if export_path is not None:
        export_path = str(arview.confined(scene.root, export_path))
        if arview.sha256(Path(export_path)) != binding["export_sha256"]:
            raise ValueError("comparison export bytes changed before evidence delivery")
    previous = [row for row in _records(scene) if row["kind"] == "evidence" and row["evidence_ref"] == cid]
    if previous:
        if previous[-1]["binding"] != binding:
            raise ValueError("a delivered evidence id cannot be reused for different evidence")
        return
    _append(scene, "evidence", evidence_ref=cid, tool=name, binding=binding, export_path=export_path)


def validate_evidence(scene, evidence_ref, refs=None, *, current_export=None):
    """Refuse unknown, unseen, other-run or stale head/camera/photo evidence."""
    from .tools import finish_review as review, arview
    if not isinstance(evidence_ref, str) or not evidence_ref.strip() or evidence_ref in _unseen(scene):
        raise ValueError("evidence_ref must name a delivered look or review in this run")
    records = _records(scene)
    row = next((row for row in reversed(records) if row["kind"] == "evidence"
                and row["evidence_ref"] == evidence_ref), None)
    if row is None:
        raise ValueError("unknown evidence_ref: use a delivered look or review from this run")
    binding = row["binding"]
    if row.get("export_path") and arview.sha256(arview.confined(scene.root, row["export_path"])) != binding["export_sha256"]:
        raise ValueError("stale evidence_ref: its export bytes changed")
    latest = next((item for item in reversed(records) if item["kind"] == "evidence"
                   and item["binding"]["checkpoint"] == binding["checkpoint"]
                   and item["binding"]["fingerprint"] == binding["fingerprint"]), row)
    current_sha = current_export.get("sha256") if current_export is not None else latest["binding"]["export_sha256"]
    if current_sha != binding["export_sha256"]:
        raise ValueError("stale evidence_ref: the current export differs from the cited comparison")
    current = review.evidence(scene.root, scene.s.head, {"sha256": current_sha})
    available = {photo["ref"] for photo in binding["photos"]}
    current["photos"] = [photo for photo in current["photos"] if photo["ref"] in available]
    if current != binding:
        raise ValueError("stale evidence_ref: compare the current head, camera basis and photo bytes again")
    if refs is not None and (not refs or len(set(refs)) != len(refs) or not set(refs) <= available):
        raise ValueError("refs must name distinct photos included in the cited comparison")
    return binding


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must give a nonblank, specific account")
    return value.strip()


def record(scene, *, id, what_differs, evidence_ref, status, last_correction, outcome, next_action,
           refs=None, reason=None, call_id=None):
    """Validate and append a complete item version, never overwrite past decisions."""
    if not enabled(scene):
        raise ValueError("record_difference is unavailable in the legacy profile")
    if not isinstance(id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", id):
        raise ValueError("id must be 1-96 letters, digits, dots, hyphens or underscores")
    if status not in {"open", "fixed", "limitation"}:
        raise ValueError("status must be open, fixed or limitation")
    previous = next((item for item in snapshot(scene) if item["id"] == id), None)
    refs = refs if refs is not None else (previous["refs"] if previous else None)
    binding = validate_evidence(scene, evidence_ref, refs)
    refs = refs if refs is not None else [photo["ref"] for photo in binding["photos"]]
    item = {"id": id, "what_differs": _text(what_differs, "what_differs"), "evidence_ref": evidence_ref,
            "status": status, "last_correction": _text(last_correction, "last_correction"),
            "outcome": _text(outcome, "outcome"), "next_action": _text(next_action, "next_action"),
            "refs": refs, "reason": _text(reason, "reason") if status == "limitation" else reason}
    if previous and not set(previous["refs"]) <= set(refs):
        raise ValueError("an update cannot drop the item's previously affected photo refs")
    _append(scene, "difference", call_id=call_id, item=item)
    return {"status": "ok", "difference": item, "differences": snapshot(scene),
            "formal_best_checkpoint": formal_best(scene)}


def prepare_finish(scene, result, rows, evidence_ref):
    """Map every non-'none' per-photo statement to items before acceptance.

    New records are staged in the tool result and appended only after its output
    succeeds. An explicit mapping preserves item identity when wording changes.
    """
    current = {item["id"]: item for item in snapshot(scene)}
    additions, mappings = [], []
    allowed = set(result["review"]["required_refs"])
    for row in rows:
        ref, text = row["ref"], row["differences"].strip()
        ids = row.get("difference_ids") or []
        if ref not in allowed:
            raise ValueError("per_photo contains a photo outside the current review")
        if ids:
            if len(ids) != len(set(ids)) or any(key not in current or ref not in current[key]["refs"] for key in ids):
                raise ValueError("difference_ids must map this photo to existing affected list items")
        elif text.casefold().rstrip(".") != "none":
            ids = [item["id"] for item in current.values()
                   if ref in item["refs"] and item["what_differs"] == text]
            if not ids:
                digest = hashlib.sha256((ref + "\0" + text).encode()).hexdigest()[:16]
                key = "photo-" + digest
                if key in current:
                    raise ValueError("generated difference id collides; explicitly map an existing item")
                item = {"id": key, "what_differs": text, "evidence_ref": evidence_ref, "status": "open",
                        "last_correction": "No correction recorded in this finish review.",
                        "outcome": "Visible difference reported by Astra.",
                        "next_action": "Choose a correction, compare it, then record fixed or a supported limitation.",
                        "refs": [ref], "reason": None}
                current[key] = item
                additions.append(item)
                ids = [key]
        mappings.append({"ref": ref, "difference_ids": ids})
    result["differences"] = list(current.values())
    result["review"]["difference_mappings"] = mappings
    result["difference_updates"] = additions
    result["formal_best_checkpoint"] = formal_best(scene)
    return [item["id"] for item in current.values() if item["status"] == "open"]


def commit_finish(scene, result):
    """Commit staged first-/second-finish observations after successful delivery."""
    if not enabled(scene) or result.get("call_id") in _unseen(scene):
        return
    for item in result.get("difference_updates", []):
        validate_evidence(scene, item["evidence_ref"], item["refs"])
        _append(scene, "difference", call_id=result["call_id"], item=item)


def tool_for(scene, tool, call):
    @tool("record_difference")
    async def record_difference(ctx: ToolContext, id: str, what_differs: str, evidence_ref: str,
                                status: Literal["open", "fixed", "limitation"], last_correction: str,
                                outcome: str, next_action: str, refs: list[str] | None = None,
                                reason: str | None = None):
        """Record or update an important visible difference in this run's append-only host list.
        Use a stable id. Cite a delivered compare_look_to_photos or finish review call id from this run;
        its head, camera basis and photo bytes must still be current. Unknown, unseen and stale evidence refuses.
        State the last correction tried, its observed outcome and the next action; for a new open item say
        explicitly that no correction has been tried. Fixed needs a current supporting comparison and an
        account of what corrected it. Limitation additionally requires a specific reason supported by that
        comparison. Astra classifies the item: this is no appearance score. Any open item prevents accepted
        finish; it does not change spending or prevent a cutoff draft from the formal best checkpoint.
        Per-photo finish statements map to ids through difference_ids; unmapped faults create open items.

        Args:
            id: Stable identifier for this difference; reuse it when updating.
            what_differs: The important visible mismatch, stated specifically.
            evidence_ref: A delivered current-run look or finish-review call id supporting this update.
            status: Open means actionable; fixed or limitation require current supporting comparison.
            last_correction: What was tried, or explicitly no correction tried yet.
            outcome: What that comparison shows about the correction or limitation.
            next_action: The next correction/check, or explicitly no further action for this resolved item.
            refs: Affected photo refs included in the cited comparison; null retains prior refs or uses all cited refs.
            reason: Specific reason for a limitation; null is allowed for open or fixed.
        """
        return await call(ctx, "record_difference", lambda cid: record(
            scene, id=id, what_differs=what_differs, evidence_ref=evidence_ref, status=status,
            last_correction=last_correction, outcome=outcome, next_action=next_action,
            refs=refs, reason=reason, call_id=cid))

    return record_difference
