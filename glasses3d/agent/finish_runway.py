"""Informational finish-review projection; never counts remotely, reserves, or changes a gate.

The money calculation is the guard's frozen Tariff.reserve_micro. Future evidence and
text are estimates, not a completion guarantee. Existing history and caching stay intact.
"""
from __future__ import annotations

import math

from .pricing import PricingError, usd_text

TEXT_ALLOWANCE_TOKENS = 6000  # planning assumption: the 12,000-character tool limit / 2
HIGH_IMAGE_ALLOWANCE = 3000  # 2500 32px patches * 1.2, including labels/gaps
DETAIL_IMAGE_ALLOWANCE = 653  # two native 500px crops + 8px gap + 42px labels: 1008x542
DETAILS_PER_PHOTO = 10  # two candidates for each of the five detail kinds; not a maximum


def image_allowance(size):
    """Published high-detail patch ceiling; resized images can use fewer patches."""
    if not isinstance(size, (list, tuple)) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
        return HIGH_IMAGE_ALLOWANCE
    patches = min(2500, math.ceil(size[0] / 32) * math.ceil(size[1] / 32))
    return (patches * 6 + 4) // 5


def observation(name, result):
    """Use all encoded panels, including byte-reused ones, to allow for a changed final export."""
    if name not in {"compare_look_to_photos", "finish"} or not isinstance(result, dict):
        return None
    look = result.get("look_comparison") if name == "finish" else result
    if not isinstance(look, dict) or look.get("status") != "ok":
        return None
    images = result.get("images") or []
    if not images:
        return None
    return {"source": "observed_panel_dimensions", "tool": name, "call_id": result.get("call_id"),
            "photo_count": len(look.get("views") or []),
            "image_count": len(images), "image_tokens": sum(image_allowance(row.get("size_px")) for row in images),
            "ar_sheet_allowance_tokens": 0 if name == "finish" else HIGH_IMAGE_ALLOWANCE,
            "text_allowance_tokens": TEXT_ALLOWANCE_TOKENS,
            "note": "All panels treated as new; observed dimensions may change. Text uses 0.5 token/character at the tool text limit."}


def project(summary, tariff, *, reference_count=None, evidence=None, review_ready=False):
    """Project the request that inspects first-finish evidence and calls finish again.

The last counted context precedes the current model reply. Before first finish,
allow that reply and the future first-finish reply at their existing output limits.
After first finish, only its current reply is missing from that context. The warning
margin is one current-context maximum request hold; it neither reserves nor blocks.
"""
    planning = summary.get("planning") or {}
    current, output = planning.get("last_counted_input_tokens"), planning.get("max_output_tokens")
    if type(current) is not int or current < 0 or type(output) is not int or output <= 0:
        return {"status": "unavailable", "informational_only": True,
                "message": "Finish-review hold unavailable until a request has been counted; no completion guarantee."}
    if evidence is None:
        count = max(0, reference_count) if type(reference_count) is int else 1
        evidence = {"source": "photo_count_planning_assumption", "reference_count": reference_count,
                    "image_count": count * (1 + DETAILS_PER_PHOTO) + 2,
                    "image_tokens": count * (HIGH_IMAGE_ALLOWANCE + DETAILS_PER_PHOTO * DETAIL_IMAGE_ALLOWANCE) + 2 * HIGH_IMAGE_ALLOWANCE,
                    "ar_sheet_allowance_tokens": 0, "text_allowance_tokens": TEXT_ALLOWANCE_TOKENS,
                    "note": "Per photo: one full pair and two crops per detail kind; plus worn front and AR sheet. More crops can be needed; unknown photo count assumes one."}
    evidence = dict(evidence)
    observed_photos = evidence.get("photo_count")
    if not review_ready and type(reference_count) is int and observed_photos and observed_photos < reference_count:
        evidence["image_tokens"] = math.ceil(evidence["image_tokens"] * reference_count / observed_photos)
        evidence["scaled_to_reference_count"] = reference_count
        evidence["note"] += " Panel allowance scaled from the observed subset to every job photo."
    overhead = evidence["image_tokens"] + evidence["ar_sheet_allowance_tokens"] + evidence["text_allowance_tokens"]
    reply_allowance = output * (1 if review_ready else 2)
    projected_input = current + reply_allowance + overhead
    committed = sum(summary.get(key, 0) for key in ("settled_micro_usd", "held_micro_usd", "unknown_micro_usd"))
    room = max(0, summary["maximum_micro_usd"] - committed)
    current_hold = tariff.reserve_micro(current, output)
    try:
        hold = tariff.reserve_micro(projected_input, output)
    except PricingError as error:
        return {"status": "unavailable", "informational_only": True, "warning": True,
                "current_input_tokens": current, "projected_input_tokens": projected_input,
                "reserve_room_usd": usd_text(room), "evidence": evidence,
                "message": f"Finish-review projection exceeds supported request limits: {error}. Start review now; no completion guarantee."}
    warning = room <= hold + current_hold
    crossed = current <= tariff.long_context_input_tokens < projected_input
    return {"status": "projected", "informational_only": True, "warning": warning,
            "review_ready": review_ready, "current_input_tokens": current, "projected_input_tokens": projected_input,
            "reply_allowance_tokens": reply_allowance, "evidence_allowance_tokens": overhead,
            "max_output_tokens": output, "second_finish_hold_micro_usd": hold, "second_finish_hold_usd": usd_text(hold),
            "reserve_room_micro_usd": room, "reserve_room_usd": usd_text(room),
            "warning_margin_micro_usd": current_hold, "warning_margin_usd": usd_text(current_hold),
            "fits_current_reserve_room": hold <= room, "crosses_long_context_price_step": crossed,
            "projected_long_context": tariff.multipliers(projected_input)["long_context"],
            "long_context_threshold_tokens": tariff.long_context_input_tokens, "evidence": evidence,
            "assumptions": "Guard formula; all input at cache-write rate and maximum output. Future replies/evidence are allowances, not exact counts; no cache credit or completion guarantee."}


def text(report):
    if not report:
        return ""
    if report.get("status") != "projected":
        return report.get("message", "Finish-review hold unavailable; no completion guarantee.")
    prefix = "Finish-review reserve warning: " if report["warning"] else "Finish-review projection: "
    value = (f"second finish may need ${report['second_finish_hold_usd']} hold at ~{report['projected_input_tokens']} input tokens; "
             f"reserve room ${report['reserve_room_usd']} (warning margin ${report['warning_margin_usd']}).")
    if report["projected_long_context"]:
        value += f" Includes the >{report['long_context_threshold_tokens']} long-context price step."
    if report["warning"]:
        value += " Inspect the pending review now." if report["review_ready"] else " Start the finish review now."
    return prefix + value + " Estimated evidence/replies; not reserved money or a completion guarantee."
