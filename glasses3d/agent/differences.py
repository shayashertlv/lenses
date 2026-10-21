"""Run-scoped, append-only decisions about visible differences; no likeness score.

Only successfully delivered look/review calls can support an update. The host
checks their immutable identities; Astra supplies the visual judgement.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict, ValidationError

from .jsonl import append_jsonl, read_jsonl

SCHEMA = "g3d.differences/1"


class DifferenceUpdateError(ValueError):
    """All invalid ids survive bounded tool reporting as structured fields."""
    def __init__(self, invalid_difference_ids, errors):
        self.invalid_difference_ids = invalid_difference_ids
        self.errors = errors
        super().__init__("Invalid difference updates [" + ", ".join(invalid_difference_ids)
                         + "]: " + "; ".join(errors))


class DifferenceUpdate(BaseModel):
    """One complete decision; finish may supply its current review evidence."""
    model_config = ConfigDict(extra="forbid")
    id: str
    what_differs: str
    status: Literal["open", "fixed", "limitation"]
    last_correction: str
    outcome: str
    next_action: str
    evidence_ref: str | None = None
    refs: list[str] | None = None
    reason: str | None = None


def argument_failure(default_failure):
    """Name malformed batch items even when the SDK redacts validation details."""
    def failure(context, error):
        # Preserve the existing fatal-error policy before adding argument context.
        message = default_failure(context, error)
        if not isinstance(error, ModelBehaviorError) or not str(error).startswith("Invalid JSON input for tool"):
            return message
        try:
            arguments = json.loads(context.tool_arguments)
        except (ValueError, TypeError, AttributeError):
            return message
        key = "items" if context.tool_name == "record_difference" else "difference_updates"
        items = arguments.get(key) if isinstance(arguments, dict) else None
        if not isinstance(items, list):
            return message
        invalid_ids = []
        for index, item in enumerate(items, 1):
            try:
                DifferenceUpdate.model_validate(item)
            except ValidationError:
                label = item.get("id") if isinstance(item, dict) else None
                invalid_ids.append(label if isinstance(label, str) else f"item[{index}]")
        return message + ("\nInvalid difference item ids: " + ", ".join(invalid_ids) if invalid_ids else "")
    return failure


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


def _prepare_item(scene, item, previous, default_evidence_ref, current_export):
    id, status = item.get("id"), item.get("status")
    if not isinstance(id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", id):
        raise ValueError("id must be 1-96 letters, digits, dots, hyphens or underscores")
    if status not in {"open", "fixed", "limitation"}:
        raise ValueError("status must be open, fixed or limitation")
    evidence_ref = item.get("evidence_ref")
    if evidence_ref is None:
        evidence_ref = default_evidence_ref
    refs = item.get("refs")
    refs = refs if refs is not None else (previous["refs"] if previous else None)
    binding = validate_evidence(scene, evidence_ref, refs, current_export=current_export)
    refs = refs if refs is not None else [photo["ref"] for photo in binding["photos"]]
    prepared = {"id": id, "evidence_ref": evidence_ref, "status": status, "refs": list(refs),
                **{field: _text(item.get(field), field) for field in
                   ("what_differs", "last_correction", "outcome", "next_action")},
                "reason": _text(item.get("reason"), "reason") if status == "limitation" else item.get("reason")}
    if previous and not set(previous["refs"]) <= set(refs):
        raise ValueError("an update cannot drop the item's previously affected photo refs")
    # Serialization is also validation: no later item can fail after earlier appends.
    json.dumps(prepared, allow_nan=False)
    return prepared


def prepare_updates(scene, items, *, default_evidence_ref=None, current_export=None):
    """Validate every update before returning staged items; never write the journal."""
    if not enabled(scene):
        raise ValueError("record_difference is unavailable in the legacy profile")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a nonempty list of difference updates")
    current = {item["id"]: item for item in snapshot(scene)}
    prepared, failures, failed_ids, seen = [], [], [], set()
    for index, raw in enumerate(items, 1):
        item = raw.model_dump() if isinstance(raw, DifferenceUpdate) else raw
        label = str(item.get("id", f"item[{index}]")) if isinstance(item, dict) else f"item[{index}]"
        try:
            if not isinstance(item, dict):
                raise ValueError("each update must be an object")
            key = item.get("id")
            if not isinstance(key, str):
                raise ValueError("id must be 1-96 letters, digits, dots, hyphens or underscores")
            if key in seen:
                raise ValueError("each id may appear only once in an update batch")
            seen.add(key)
            prepared.append(_prepare_item(scene, item, current.get(key), default_evidence_ref, current_export))
        except (ValueError, OSError, KeyError, TypeError) as problem:
            failed_ids.append(label)
            failures.append(f"{label}: {problem}")
    if failures:
        raise DifferenceUpdateError(failed_ids, failures)
    return prepared


def _commit_updates(scene, items, call_id):
    # The callers finish all validation before the first append. Journal history
    # remains one complete, consecutively numbered record per item version.
    for item in items:
        _append(scene, "difference", call_id=call_id, item=item)


def record(scene, *, id=None, what_differs=None, evidence_ref=None, status=None, last_correction=None,
           outcome=None, next_action=None, refs=None, reason=None, items=None, call_id=None):
    """Append a validated batch, or the backward-compatible single complete item."""
    single = {"id": id, "what_differs": what_differs, "evidence_ref": evidence_ref, "status": status,
              "last_correction": last_correction, "outcome": outcome, "next_action": next_action,
              "refs": refs, "reason": reason}
    if items is not None and any(value is not None for value in single.values()):
        raise ValueError("Provide either items or the one-item fields, not both")
    prepared = prepare_updates(scene, items if items is not None else [single])
    _commit_updates(scene, prepared, call_id)
    result = {"status": "ok", "updated_differences": prepared, "differences": snapshot(scene),
              "formal_best_checkpoint": formal_best(scene)}
    if len(prepared) == 1:
        result["difference"] = prepared[0]
    return result


def prepare_finish(scene, result, rows, evidence_ref, updates=None):
    """Map every non-'none' per-photo statement to items before acceptance.

    New records are staged in the tool result and appended only after its output
    succeeds. An explicit mapping preserves item identity when wording changes.
    """
    current = {item["id"]: item for item in snapshot(scene)}
    additions = (prepare_updates(scene, updates, default_evidence_ref=evidence_ref,
                                 current_export=result["export"]) if updates is not None else [])
    for item in additions:
        current[item["id"]] = item
    mappings = []
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
    if updates is not None:
        result["difference_reconciliation"] = True
    result["formal_best_checkpoint"] = formal_best(scene)
    return [item["id"] for item in current.values() if item["status"] == "open"]


def commit_finish(scene, result):
    """Commit staged first-/second-finish observations after successful delivery."""
    if not enabled(scene) or result.get("call_id") in _unseen(scene):
        return
    if result.get("difference_reconciliation") and result.get("status") != "accepted":
        return
    updates = result.get("difference_updates", [])
    if updates:
        prepared = prepare_updates(scene, updates, current_export=result.get("export"))
        _commit_updates(scene, prepared, result["call_id"])


def tool_for(scene, tool, call):
    @tool("record_difference")
    async def record_difference(ctx: ToolContext, id: str | None = None, what_differs: str | None = None,
                                evidence_ref: str | None = None,
                                status: Literal["open", "fixed", "limitation"] | None = None,
                                last_correction: str | None = None, outcome: str | None = None,
                                next_action: str | None = None, refs: list[str] | None = None,
                                reason: str | None = None, items: list[DifferenceUpdate] | None = None):
        """Record one visible difference or an atomic items batch in this run's append-only host list.
        Supply items or the one-item fields. Every item is validated before any append; a refusal names
        failing ids and changes nothing. Confirming finish can reconcile the same items inline.
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
            items: Complete updates in one request; use instead of the one-item fields. Each needs evidence_ref.
        """
        def action(cid):
            try:
                return record(scene, id=id, what_differs=what_differs, evidence_ref=evidence_ref, status=status,
                              last_correction=last_correction, outcome=outcome, next_action=next_action,
                              refs=refs, reason=reason, items=items, call_id=cid)
            except DifferenceUpdateError as problem:
                return {"status": "refused", "code": "E_ARGS", "message": str(problem),
                        "invalid_difference_ids": problem.invalid_difference_ids, "errors": problem.errors}
        return await call(ctx, "record_difference", action)

    return record_difference
