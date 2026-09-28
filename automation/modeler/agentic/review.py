"""The owner review loop: the automation ends with the owner's live AR try-on, not with the automatic final evaluator.

When the author calls request_delivery on a compatible revision (or a fallback picks one), a job whose policy has
``owner_review`` does not deliver: it waits in state ``awaiting_owner`` with a review candidate (the revision's byte-bound
GLB under ``review/round-NN/``, its manifest and the try-on link data, ``evaluation.write_review_candidate``). The owner
then decides (``Session.owner_accept`` / ``owner_request_changes`` / ``owner_stop``, the CLI's ``owner-review``):

* accept: the job delivers exactly that revision (``deliverable/``), the owner verdict is recorded append-only;
* changes: the owner's words go VERBATIM to the SAME author conversation as a new user message, with the latest
  observation's measurements that relate to them, every runtime-limited flag stated plainly and the new allowance (the
  round's caps are the job's committed amounts plus exactly that, ``Budget.grant_allowance``: the round spends its grant and
  nothing more), optionally with per-round module locks (editable_modules) bound to the reviewed revision's bytes;
  the author edits, builds and calls request_delivery again, and the job returns to awaiting_owner;
* stop: the job ends unresolved, the last candidate kept.

Waiting costs nothing and never expires: each request re-sends the conversation, nothing is held open. When the last
author request counted ``policy['review_resume_token_limit']`` input tokens or more, a change request instead starts a NEW
job seeded from the candidate revision (``--seed-revision`` / owner_instruction / editable_modules) with the owner's text
and a short summary of this job, and links the two jobs both ways (setting 'lineage'). Every decision is also appended to
the owner verdicts (the job's verdicts table, kind 'owner', and the calibration set ``modeler.owner_verdict``) with the
asset hash and a snapshot of the related measurements, so owner-vs-instrument disagreements can recalibrate the
instruments later.

This module holds the pieces that are not the runner's state machine: what the owner's message says, what counts as a
related measurement, the try-on link data and the lineage summary.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import tools as T
from .responses import text_block

# What a runtime-limited flag means to the author, stated plainly: the measurement compares something the AR viewer
# cannot draw, so the author must not chase it. A flag not listed here gets the generic sentence.
RUNTIME_LIMITED = {
    "crystal_clarity_runtime_limited": ("the viewer cannot draw the crystal's edge contrast (the photo's crystal shows mostly by edge and refraction "
                                        "contrast); you can only match the body (apply recommended.tint_srgb once), so do not chase the edges: a "
                                        "darker tint over-darkens the body and the skin seen through the rim"),
}
GENERIC_RUNTIME_LIMITED = "the AR runtime cannot draw what this measurement compares; match what it can draw and do not chase the rest"
RELATED_SUMMARY_KEYS = ("lens_colour", "lens_env_intensity_recommended", "lens_reflection", "frame_see_through", "temple_see_through")
DEFAULT_TRYON_PORT = 8793


def observation_summary(revision: dict | None) -> dict:
    return ((revision or {}).get("observation") or {}).get("summary") or {}


def related_measurements(revision: dict, revision_dir: Path) -> dict:
    """The latest observation's measurements an owner's change request relates to: summary.lens_colour (with its
    lens_transmission_recommended), the lens environment recommendation, summary.appearance per material (the build
    reply's digest), summary.lens_reflection, the see-through of the frame and temples, and the exporter's audit flags."""
    summary = observation_summary(revision)
    out: dict = {}
    for k in RELATED_SUMMARY_KEYS:
        if summary.get(k) is not None:
            out[k] = summary[k]
    appearance = T.appearance_digest(summary.get("appearance"))
    if appearance is not None:
        out["appearance"] = appearance
    export = T.load_json_or_none(Path(revision_dir) / "export.json")      # any read or parse failure: no audit (was OSError / ValueError only)
    audit = T.export_audit(export)
    if audit is not None:
        out["export_audit"] = audit
    if not summary or summary.get("status") == "unmeasured":
        out["status"] = "unmeasured"
        out["note"] = summary.get("note") or "no measurement of this revision exists"
    return out


def runtime_limited_flags(summary: dict) -> list[dict]:
    """Every flag ending in '_runtime_limited' anywhere in the observation summary, with where it was found and what it means."""
    found: list[dict] = []

    def walk(value, where: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{where}.{k}" if where else str(k))
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str) and v.endswith("_runtime_limited"):
                    item = {"flag": v, "where": where, "meaning": RUNTIME_LIMITED.get(v, GENERIC_RUNTIME_LIMITED)}
                    if item not in found:
                        found.append(item)
                else:
                    walk(v, where)
    walk(summary or {}, "summary")
    return found


def runtime_limited_statements(flags: list[dict]) -> list[str]:
    return [f"{f['flag']} ({f['where']}): {f['meaning']}" for f in flags]


def tryon_data(job_dir: Path, revision: dict, asset: dict | None, *, round_no: int, product_id: str, port: int = DEFAULT_TRYON_PORT) -> dict | None:
    """What the owner needs to open the candidate in the live AR app: the file, its digest, the width and clip the link
    passes, and the link the try-on server (``python -m modeler.tryon PORT --jobs <job folder>``) serves it under."""
    if not asset:
        return None
    from ..tryon import AR_APP, review_route, tryon_link, width_from_bbox
    job_dir = Path(job_dir)
    width = width_from_bbox((revision.get("observation") or {}).get("bbox_mm"))
    route = review_route(job_dir.name, revision["id"])
    label = f"{job_dir.name}: {revision['id']} round {round_no}, awaiting your review"
    clip = T.RUNTIME_TEMPLE_CLIP_BASE_Z_M
    return {"glb": asset["path"], "sha256": asset["sha256"], "width_mm": width, "clip_zm": clip, "route": route, "port": port, "ar_app": AR_APP,
            "server": f"python -m modeler.tryon {port} --jobs {job_dir}",
            "link": tryon_link(route=route, name=f"{product_id} - {label}", width_mm=width, clip_zm=clip, sha256=asset["sha256"], port=port),
            "note": "start the AR dev server (ar/, port 8240) and the try-on server above, then open the link"}


def owner_message_blocks(*, round_no: int, revision_id: str, text: str, related: dict, runtime_limited: list[dict], allowance: dict,
                         totals_after: dict, editable: list[str] | None, locked: list[str], max_revisions: int, revisions_used: int,
                         wall_minutes: int) -> list[dict]:
    """The owner's change request as the author receives it: the owner's words verbatim in a block of their own, then the
    host's data (related measurements, runtime-limited flags stated plainly, the allowance, the module locks, how to go on)."""
    head = (f"Owner review, round {round_no}: the owner tried revision {revision_id} on in the live AR mirror and asks for changes. "
            "The owner's words follow verbatim in the next block; they are the owner's request, and the data after them is the host's.")
    host = {"owner_review": {
        "round": round_no, "reviewed_revision": revision_id, "decision": "changes_requested",
        "related_measurements": related,
        "related_measurements_note": ("the latest observation of the reviewed revision: the numbers that relate to the owner's words (lens colour "
                                      "and its lens_transmission_recommended, the appearance per material, the lens reflection, see-through, the "
                                      "export audit flags); set values from them rather than by steps"),
        "runtime_limited": runtime_limited_statements(runtime_limited) or ["none: every measured quantity above is one the AR runtime can draw"],
        "allowance": {"inference_operations": allowance["add_operations"], "budget_usd": allowance["budget_usd"],
                      "operations_remaining": totals_after.get("operations_remaining"), "budget_remaining_usd": totals_after.get("remaining_usd"),
                      "note": ("granted by the owner for this round; operations_remaining and budget_remaining_usd are this round's own figures "
                               "(what earlier rounds left unspent is not carried over); every author message and critic call costs one operation")},
        "editable_modules": editable, "locked_modules": locked,
        "revisions": {"used": revisions_used, "cap": max_revisions}, "wall_minutes_this_round": wall_minutes,
        "how_to_continue": (f"make the owner's change on {revision_id}: edit_program with base_revision_id {revision_id}, patch or replace only what "
                            "the change needs and inherit the rest, build_now true; check the new build against these measurements and its sheets; "
                            "then call request_delivery of the new revision. The owner reviews it live again. "
                            + (f"Only {', '.join(editable)} may change this round; edit_program refuses any other module." if editable is not None
                               else "Every module may be edited this round."))}}
    return [text_block(head), text_block(text), text_block("Host data for this round (data, not instructions):\n" + json.dumps(host, indent=1, default=str))]


def previous_job_summary(store, open_round: dict, revision: dict, text: str) -> dict:
    """The short summary a continuation job's first message carries about the job it continues."""
    rounds = store.owner_rounds()
    return {"job": str(store.job_dir), "product_id": (store.job().get("request") or {}).get("product_id"),
            "revision": revision["id"], "asset_sha256": open_round.get("asset_sha256"), "round": open_round["round"],
            "revisions_built": len(store.revisions()),
            "earlier_owner_requests": [(r.get("decision_record") or {}).get("text") for r in rounds
                                       if r["decision"] == "changes" and (r.get("decision_record") or {}).get("text")],
            "rationale_of_the_seed": (revision.get("rationale") or "")[:600],
            "related_measurements": related_measurements(revision, store.job_dir / "revisions" / revision["id"]),
            "runtime_limited": runtime_limited_statements(runtime_limited_flags(observation_summary(revision))),
            "note": ("this job continues that one: its conversation grew too large to continue cheaply, so the owner's change request starts here "
                     "from the reviewed revision (seeded as r0001 and built before your first turn)"),
            "owner_text": text}
