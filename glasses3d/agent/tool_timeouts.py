"""Shared inner deadlines and composed SDK waiting budgets.

Worker and capture limits are enforced by their owners. SDK limits include their
sequential steps plus host work; the latter is an allowance, not a guaranteed
wall-clock bound for arbitrary meshes, photos, filesystem IO or process startup.
The lock remains held until the real operation ends, even after SDK cancellation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time


SOFT_DEFAULT_S, SOFT_MIN_S, SOFT_MAX_S = 120.0, 0.5, 600.0
HARD_MARGIN_S = 30.0
CHECKPOINT_DEADLINE_S = 60.0
READ_DEADLINE_S = 30.0
BOUNDS_DEADLINE_S = 30.0
EXPORT_DEADLINE_S = 120.0
RENDER_DEADLINE_S = 300.0
PICK_DEADLINE_S = 30.0
ENV_DEADLINE_S = 900.0
ENV_ITERATIONS = 3
AR_CAPTURE_DEADLINE_S = 290.0
AR_CAPTURE_DEFAULT_S = 300.0
LOOK_CAPTURE_DEADLINE_S = 280.0
LOOK_CAMERA_BATCH_SIZE = 64
CAPTURE_KILL_WAIT_S = 10.0
OWNER_SNAPSHOT_DEADLINE_S = 150.0
MODEL_MAP_SNAPSHOT_DEADLINE_S = 120.0
OWNER_APPLY_DEADLINE_S = 180.0
PREVIEW_OPTIONS_MAX = 3
READY_TIMEOUT_S, HELLO_TIMEOUT_S = 60.0, 30.0
WORKER_POLL_S, RECEIPT_ATTEMPTS = 0.02, 50
WORKER_KILL_WAIT_S, WORKER_CLOSE_WAIT_S, WORKER_SHUTDOWN_S = 15.0, 20.0, 10.0
RESTARTS_PER_CALL, J_SPARE_JOIN_S = 2, 90.0
CREASE_COMPARE_WORK_S, CREASE_ALTERNATIVES_MAX = 360.0, 5
CREASE_MIN_REMAINING_S = 1.0

SNIPPET_SOFT_DEFAULT_S, SNIPPET_SOFT_MIN_S, SNIPPET_SOFT_MAX_S = SOFT_DEFAULT_S, SOFT_MIN_S, SOFT_MAX_S
SNIPPET_HARD_MARGIN_S = HARD_MARGIN_S
SDK_TIMEOUT_MARGIN_S = 10.0
HOST_WORK_MARGIN_S = 60.0
FACET_DEADLINE_MARGIN_S = 30.0
FACET_VARIANTS_BASE_S, FACET_VARIANTS_PER_RENDER_S = 30.0, 2.5
FACET_VARIANTS_HOST_MARGIN_S = 15.0

# Existing operation deadlines, kept separate from the SDK's composite budget.
# These are used by worker calls (including facet render calls) and must not be
# increased merely because surrounding host work has been added.
HARD_TIMEOUT_S = {
    "labelled_model_map": 600, "preview_owner_edit": 2400, "understand_finish": 180,
    "edit_report": 1680, "facet_owner_edit": 660,
    "seat_lens": 150, "hide_eyewire": 150, "unseat_lens": 150, "unhide_eyewire": 150,
    "check_lens_seating": 120, "render_views": RENDER_DEADLINE_S,
    "compare_to_photos": RENDER_DEADLINE_S, "export_glb": EXPORT_DEADLINE_S,
    "save_checkpoint": CHECKPOINT_DEADLINE_S, "list_checkpoints": CHECKPOINT_DEADLINE_S,
    "restore_checkpoint": CHECKPOINT_DEADLINE_S,
    "load_font": CHECKPOINT_DEADLINE_S,
    "arview_capture": AR_CAPTURE_DEADLINE_S + CAPTURE_KILL_WAIT_S,
    "fit_cameras": 1800, "project_photo_texture": 1800, "deliver": 600,
    "pick_surface_point": PICK_DEADLINE_S, "read_image": 30, "read_report": 30,
    "scene_summary": READ_DEADLINE_S, "compare_look_to_photos": 750,
    "finish": 1080, "budget_status": 30, "record_difference": 30,
    "compare_lens_candidates": 6 * 2 * (LOOK_CAPTURE_DEADLINE_S + CAPTURE_KILL_WAIT_S),
    "choose_camera_orientation": 180,
    "facet_inspect": 30, "photo_image": 30, "photo_read": 30, "photo_compare": 40,
    "facet_edit": 60, "facet_variants": 90,
    "facet_finish": CHECKPOINT_DEADLINE_S + EXPORT_DEADLINE_S + FACET_DEADLINE_MARGIN_S,
    "facet_crease_compare": 600, "facet_visible_angle": 420,
}


def hard_timeout_s(tool_name: str, *, soft_timeout_s=None, variants=None, photos=None) -> float:
    """Worker deadline, distinct from the SDK time allowed for the whole tool."""
    if tool_name == "execute_blender_code":
        soft = SOFT_DEFAULT_S if soft_timeout_s is None else float(soft_timeout_s)
        return min(max(soft, SOFT_MIN_S), SOFT_MAX_S) + HARD_MARGIN_S
    if tool_name == "facet_variants" and variants is not None and photos is not None:
        scaled = FACET_VARIANTS_BASE_S + FACET_VARIANTS_PER_RENDER_S * int(photos) * (int(variants) + 1)
        return float(min(scaled, HARD_TIMEOUT_S[tool_name] - FACET_VARIANTS_HOST_MARGIN_S))
    return float(HARD_TIMEOUT_S[tool_name])


def capture_steps_s(deadline: float) -> float:
    return deadline + CAPTURE_KILL_WAIT_S


def look_steps_s(*, worn=True, photo_count=1) -> float:
    # Photo cameras, side light, and (unless reused by finish) the worn capture.
    batches = max(1, math.ceil(photo_count / LOOK_CAMERA_BATCH_SIZE))
    return (2 * batches + int(worn)) * capture_steps_s(LOOK_CAPTURE_DEADLINE_S)


def seating_report_steps_s() -> float:
    # Photo camera may fall back to canonical; light frame plus before/after renders.
    return 3 * read_steps_s(BOUNDS_DEADLINE_S) + 2 * read_steps_s(RENDER_DEADLINE_S)


def worker_rpc_steps_s(deadline: float) -> float:
    return deadline + WORKER_KILL_WAIT_S


def worker_start_steps_s() -> float:
    # Receipt polling, its read retries, one hello exchange, failed-start cleanup.
    return (READY_TIMEOUT_S + WORKER_POLL_S + RECEIPT_ATTEMPTS * WORKER_POLL_S +
            HELLO_TIMEOUT_S + WORKER_KILL_WAIT_S)


def worker_close_steps_s() -> float:
    return worker_rpc_steps_s(WORKER_SHUTDOWN_S) + WORKER_CLOSE_WAIT_S + WORKER_KILL_WAIT_S


def restart_steps_s() -> float:
    # A stale warm spare is killed, then up to two starts verify the head (or its
    # parent). Each failed attempt kills its worker. No lost operation is replayed.
    return J_SPARE_JOIN_S + WORKER_KILL_WAIT_S + RESTARTS_PER_CALL * (
        WORKER_KILL_WAIT_S + worker_start_steps_s() +
        worker_rpc_steps_s(CHECKPOINT_DEADLINE_S) + WORKER_KILL_WAIT_S)


def restore_steps_s() -> float:
    # restore_checkpoint may restart; callers such as restore_head verify again.
    return worker_rpc_steps_s(CHECKPOINT_DEADLINE_S) + restart_steps_s() + worker_rpc_steps_s(READ_DEADLINE_S)


def read_steps_s(deadline: float) -> float:
    # A raised read may have changed the scene: fingerprint, restore, and restart
    # if recovery itself loses the worker. Count this path even for cached reads.
    return worker_rpc_steps_s(deadline) + worker_rpc_steps_s(READ_DEADLINE_S) + restore_steps_s()


def mutation_steps_s(deadline: float) -> float:
    # Two drift attempts can save twice; a failed save/operation then restores.
    return 2 * worker_rpc_steps_s(deadline) + 2 * worker_rpc_steps_s(CHECKPOINT_DEADLINE_S) + restore_steps_s()


def export_steps_s() -> float:
    # Cached identity check can fall through to actual export after live drift.
    return read_steps_s(BOUNDS_DEADLINE_S) + read_steps_s(EXPORT_DEADLINE_S)


def owner_scope_steps_s() -> float:
    # ScopeGuard snapshots and validates a strict export; a refusal may roll back.
    return read_steps_s(OWNER_SNAPSHOT_DEADLINE_S) + export_steps_s() + restore_steps_s()


def matched_lighting_steps_s(rig: str | None) -> float:
    # Cold gain: one render. photo_env: uniform plus basis/direct per iteration.
    # Unknown rig uses the more expensive supported path; registry passes its rig.
    env = (read_steps_s(BOUNDS_DEADLINE_S) + (1 + 2 * ENV_ITERATIONS) * read_steps_s(ENV_DEADLINE_S)
           if rig in (None, "photo_env") else 0.0)
    return env + read_steps_s(RENDER_DEADLINE_S)


SEATING_REPORT_TIMEOUT_S = seating_report_steps_s()
UNTIMED_TOOLS = frozenset({"temple_stage", "photo_print"})


def inner_steps_s(tool_name: str, *, photo_count=1, matched_rig=None) -> float | None:
    """Budget for declared inner steps; no silent fallback for a newly added tool.

    Recovery is conservatively counted for each RPC, even where an earlier error
    would skip later work. These are waiting budgets, not measured runtimes.
    Render requests contain every requested view in one worker call. Preview
    options are counted serially: low free memory may reduce its pool to one.
    The long-running specialist and photo bake retain their explicit no-SDK-limit
    policy; their own stop checks and worker deadlines still apply.
    """
    if tool_name in UNTIMED_TOOLS:
        return None
    export, restore = export_steps_s(), restore_steps_s()
    bounds = read_steps_s(BOUNDS_DEADLINE_S)
    render = 2 * bounds + read_steps_s(RENDER_DEADLINE_S) + export  # optional parts pass
    owner_check = owner_scope_steps_s()
    mutation = mutation_steps_s
    matched = export + 2 * bounds + matched_lighting_steps_s(matched_rig)
    facet_edit = 2 * restore + mutation(HARD_TIMEOUT_S["facet_edit"]) + matched + read_steps_s(HARD_TIMEOUT_S["facet_edit"])
    option = (worker_start_steps_s() + worker_rpc_steps_s(CHECKPOINT_DEADLINE_S) +
              worker_rpc_steps_s(READ_DEADLINE_S) + 2 * read_steps_s(OWNER_SNAPSHOT_DEADLINE_S) +
              mutation(OWNER_APPLY_DEADLINE_S) + export + look_steps_s(photo_count=photo_count) + worker_close_steps_s())
    steps = {
        "execute_blender_code": mutation(SOFT_MAX_S + HARD_MARGIN_S) + seating_report_steps_s(),
        **{name: mutation(HARD_TIMEOUT_S[name]) + seating_report_steps_s() for name in
           ("seat_lens", "hide_eyewire", "unseat_lens", "unhide_eyewire")},
        "check_lens_seating": read_steps_s(HARD_TIMEOUT_S["check_lens_seating"]),
        "scene_summary": read_steps_s(READ_DEADLINE_S),
        "load_font": mutation(HARD_TIMEOUT_S["load_font"]),
        "save_checkpoint": worker_rpc_steps_s(READ_DEADLINE_S) + worker_rpc_steps_s(CHECKPOINT_DEADLINE_S),
        "list_checkpoints": HARD_TIMEOUT_S["list_checkpoints"],
        "restore_checkpoint": restore,
        "render_views": render,
        "compare_to_photos": export + 2 * bounds + read_steps_s(RENDER_DEADLINE_S),
        "pick_surface_point": 2 * bounds + read_steps_s(PICK_DEADLINE_S),
        "export_glb": export,
        "fit_cameras": export + HARD_TIMEOUT_S["fit_cameras"],  # host fitting allowance
        "arview_capture": export + capture_steps_s(AR_CAPTURE_DEADLINE_S),
        "compare_look_to_photos": export + look_steps_s(photo_count=photo_count),
        "compare_lens_candidates": export + 6 * 2 * capture_steps_s(LOOK_CAPTURE_DEADLINE_S),
        "choose_camera_orientation": export + HARD_TIMEOUT_S["choose_camera_orientation"],
        "finish": export + capture_steps_s(AR_CAPTURE_DEADLINE_S) + look_steps_s(worn=False, photo_count=photo_count) + render,
        "labelled_model_map": export + read_steps_s(MODEL_MAP_SNAPSHOT_DEADLINE_S) + capture_steps_s(LOOK_CAPTURE_DEADLINE_S),
        "understand_finish": 3 * worker_rpc_steps_s(READ_DEADLINE_S),
        "edit_report": owner_check + export + 2 * look_steps_s(photo_count=photo_count) + worker_rpc_steps_s(READ_DEADLINE_S),
        "facet_owner_edit": read_steps_s(OWNER_SNAPSHOT_DEADLINE_S) + mutation(OWNER_APPLY_DEADLINE_S) + owner_check,
        "preview_owner_edit": ((PREVIEW_OPTIONS_MAX + 3) * worker_rpc_steps_s(READ_DEADLINE_S) +
                               export + look_steps_s(photo_count=photo_count) + PREVIEW_OPTIONS_MAX * option),
        **{name: HARD_TIMEOUT_S[name] for name in ("read_image", "read_report", "budget_status", "record_difference", "photo_read",
            "photo_image", "facet_inspect", "project_photo_texture", "deliver")},
        "photo_compare": matched + read_steps_s(HARD_TIMEOUT_S["photo_compare"]),
        "facet_edit": facet_edit,
        "facet_variants": export + 2 * bounds + read_steps_s(HARD_TIMEOUT_S["facet_variants"] - FACET_VARIANTS_HOST_MARGIN_S),
        "facet_finish": restore + export + FACET_DEADLINE_MARGIN_S,
        "facet_crease_compare": (2 * restore + read_steps_s(CREASE_COMPARE_WORK_S) +
                                 (1 + CREASE_ALTERNATIVES_MAX) * (CREASE_MIN_REMAINING_S + CAPTURE_KILL_WAIT_S)),
        "facet_visible_angle": facet_edit,
    }
    return float(steps[tool_name])


def sdk_timeout_s(tool_name: str, *, owner_edit=False, photo_count=1, matched_rig=None) -> float | None:
    steps = inner_steps_s(tool_name, photo_count=photo_count, matched_rig=matched_rig)
    if steps is None:
        return None
    # Scope validation and an optional rollback are outside the worker operation.
    scope = owner_scope_steps_s() if owner_edit else 0.0
    return steps + scope + HOST_WORK_MARGIN_S + SDK_TIMEOUT_MARGIN_S


@dataclass(frozen=True)
class RunningTool:
    name: str
    call_id: str
    started: float
    budget_s: float | None


def mark_running(scene, name: str, call_id: str) -> None:
    scene._running_tool = RunningTool(name, call_id, time.monotonic(),
                                     scene_timeout_s(scene, name))


def scene_timeout_s(scene, name: str) -> float | None:
    return sdk_timeout_s(name, owner_edit=bool(getattr(scene, "owner_edit", None)),
                         photo_count=max(1, len(getattr(scene, "job", {}).get("images", []))))


def clear_running(scene, call_id: str) -> None:
    active = getattr(scene, "_running_tool", None)
    if active is not None and active.call_id == call_id:
        scene._running_tool = None


def busy_result(scene) -> dict:
    """Describe the real background operation; never promise an exact completion."""
    active = getattr(scene, "_running_tool", None)
    if active is None:
        return {"status": "busy", "code": "E_BUSY", "message":
                "Another scene operation holds the lock; its remaining time is unknown. Wait before retrying."}
    elapsed = max(0.0, time.monotonic() - active.started)
    remaining = None if active.budget_s is None else max(0.0, active.budget_s - elapsed)
    estimate = ("remaining time is unknown" if remaining is None else
                f"about {remaining:.0f} seconds remain in its waiting budget" if remaining else
                "its waiting budget has elapsed; host work or cleanup is still running and remaining time is unknown")
    return {"status": "busy", "code": "E_BUSY", "running_tool": active.name,
            "running_call_id": active.call_id, "elapsed_s": round(elapsed, 1),
            "remaining_budget_s": None if remaining is None else round(remaining, 1),
            "message": f"{active.name} ({active.call_id}) is still running after {elapsed:.0f} seconds; {estimate}. "
                       "The scene stays locked until it ends. Wait before issuing another scene tool; repeated calls cannot speed it up."}
