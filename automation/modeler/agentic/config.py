"""Request translation and the effective policy of a job.

The CLI's validated limits govern; a legacy request file (``modeler.job`` shape: photos, dimensions, notes, limits,
author, held_out_views) contributes the product, photos, dimensions and notes, and its old author/limit fields are kept
as provenance with a visible translation note. A request file cannot enable paid mode, name credentials or enlarge a
cap; unknown or conflicting policy keys are rejected. An explicit photo ``held_out`` and a matching ``held_out_views``
entry canonicalise to one sealed photo, never two.

``build_policy`` makes the whole policy of a new job. A job created before a key existed keeps its stored policy
unchanged, and a missing key reads as None there, which is exactly what build_policy stores for the old behaviour:
image_prune_tokens None never prunes, owner_instruction None and editable_modules None are no owner revision (every
module editable). Every stored policy carries cache_mode; the old default was 'explicit_one_breakpoint'.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import warnings

from ..request import VIEWS
from .pricing import LONG_CONTEXT_INPUT_TOKENS, MODEL, MODEL_MAX_OUTPUT_TOKENS, PricingError, Tariff, usd_to_micro
from .responses import CACHE_MODES

LEGACY_LIMIT_KEYS = ("max_turns", "blender_time_limit_s", "wall_time_limit_min", "stagnation_turns", "render_scale")
LEGACY_TOP_KEYS = ("product_id", "photos", "dimensions", "notes", "limits", "author", "held_out_views", "donor", "loaded_from")
FORBIDDEN_KEYS = ("budget", "budget_usd", "cap_usd", "paid", "allow_paid", "astra", "api_key", "apikey", "credential", "credentials", "openai_api_key",
                  "max_inference_requests", "secret", "token", "ledger")
DRIVERS = ("scripted", "responses")
WORKERS = ("fake", "docker", "native-fixture")
# the paid Astra intake stage (modeler.intake_astra) is not wired into this route: 'astra' is refused up front (INTAKE_NOT_WIRED)
INTAKES = ("none", "code", "synthetic", "scripted")
INTAKE_NOT_WIRED = ("--intake astra is not wired in this build: the paid intake stage never runs on this route; use --intake code "
                    "(measured) or --intake scripted (saved vision answers, offline)")
CRITICS = ("none", "scripted", "responses")
FINALS = ("none", "scripted", "responses")
EXIT_OK, EXIT_INVALID, EXIT_FAILED, EXIT_BUDGET, EXIT_CANCELLED = 0, 2, 3, 4, 130
# New jobs cache the growing author conversation with a marker on every input carrier (responses.apply_cache_breakpoint);
# test-pilot-002 replayed under it cost $3.33 for the author instead of $6.84. 'explicit_one_breakpoint' is every job
# before 2026-09-28 and stays selectable.
DEFAULT_CACHE_MODE = "explicit_rolling"
PRUNE_MIN_TOKENS = 20_000               # an image prune epoch below this would re-open on every request
OWNER_INSTRUCTION_MAX_CHARS = 8_000     # quoted verbatim in the first message; an owner's request, not a document
# The owner review loop (2026-09-28): a new job waits for the owner's live AR verdict instead of delivering on the author's
# request_delivery. A change request re-sends the whole conversation, so above this many input tokens on the last author
# request it starts a NEW job seeded from the candidate instead (below the 200k compaction threshold, where a job without a
# verified compact bound stops, and below the 272k long-context price cliff).
REVIEW_RESUME_TOKEN_LIMIT = 150_000
OWNER_ROUND_MAX_USD = 50                # one change round's allowance: refused above this (the job's own ceiling is 200)
OWNER_ROUND_MAX_OPERATIONS = 30
JOB_MAX_USD = 200                       # a job's cap, grants included, never exceeds build_policy's own ceiling
JOB_MAX_OPERATIONS = 200
DEFAULT_COMPACT_THRESHOLD_TOKENS = 200_000      # build_policy's compaction threshold when none is given (the CLI's prune default is below it)
DEFAULT_IMAGES_PER_REQUEST = 14                 # build_policy's images_per_request; also fetch_pending_images' max_images ceiling


class ConfigError(ValueError):
    pass


class ConfigWarning(UserWarning):
    """A valid policy that costs more than the owner may expect (also kept in the policy's ``warnings``)."""


def _walk_forbidden(value, path="request"):
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                raise ConfigError(f"{path}.{k}: a request file cannot carry budgets, paid switches or credentials")
            _walk_forbidden(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk_forbidden(v, f"{path}[{i}]")


def translate_request(raw: dict, base_dir: Path) -> dict:
    """The normalised request plus provenance and translation notes; raises ConfigError on anything unsupported."""
    if not isinstance(raw, dict):
        raise ConfigError("a request is a JSON object")
    _walk_forbidden(raw)
    unknown = sorted(set(raw) - set(LEGACY_TOP_KEYS))
    if unknown:
        raise ConfigError(f"unsupported request keys {unknown}; the new route accepts {list(LEGACY_TOP_KEYS)}")
    pid = str(raw.get("product_id") or "").strip()
    if not pid or not all(c.isalnum() or c in "-_" for c in pid) or len(pid) > 64:
        raise ConfigError("product_id must be a short [A-Za-z0-9_-] token")
    photos_raw = raw.get("photos")
    if not isinstance(photos_raw, list) or len(photos_raw) < 2 or len(photos_raw) > 12:
        raise ConfigError("a request needs 2..12 photos")
    held_views = raw.get("held_out_views", ["angled"])
    if not isinstance(held_views, list) or any(not isinstance(v, str) for v in held_views):
        raise ConfigError("held_out_views must be a list of view names")
    photos, notes, seen_views = [], [], set()
    for i, p in enumerate(photos_raw):
        if isinstance(p, str):
            p = {"path": p}
        if not isinstance(p, dict) or "path" not in p:
            raise ConfigError(f"photo {i} needs a path")
        extra = set(p) - {"path", "view", "held_out", "id", "source_photo_id", "crop_xyxy", "sha256"}
        if extra:
            raise ConfigError(f"photo {i}: unsupported keys {sorted(extra)}")
        path = Path(p["path"])
        if not path.is_absolute():
            path = (Path(base_dir) / path).resolve()
        if not path.is_file():
            raise ConfigError(f"photo not found: {path}")
        view = str(p.get("view", "unknown"))
        if view not in VIEWS:
            raise ConfigError(f"photo {i}: unknown view {view!r}; one of {VIEWS}")
        if view not in ("unknown", "other"):
            if view in seen_views:
                raise ConfigError(f"view {view!r} is used twice")
            seen_views.add(view)
        explicit = p.get("held_out")
        by_view = view in held_views
        held = bool(explicit) if explicit is not None else by_view
        if explicit is not None and bool(explicit) != by_view and by_view:
            notes.append(f"photo {i} ({view}): held_out={explicit} overrides held_out_views membership")
        if explicit and by_view:
            notes.append(f"photo {i} ({view}): held_out flag and held_out_views both name it; one sealed reservation")
        pid_photo = str(p.get("id") or (view if view not in ("unknown", "other", "") and view not in [q["id"] for q in photos] else f"photo{i + 1:02d}"))
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if p.get("sha256") and str(p["sha256"]).lower() != sha:
            raise ConfigError(f"photo {i}: sha256 pin does not match the file")
        photos.append({"id": pid_photo, "path": str(path), "view": view, "held_out": held, "sha256": sha, "bytes": len(data),
                       "source_photo_id": p.get("source_photo_id"), "crop_xyxy": p.get("crop_xyxy")})
    if len({q["id"] for q in photos}) != len(photos):
        raise ConfigError("photo ids must be unique")
    if all(q["held_out"] for q in photos):
        raise ConfigError("at least one photo must be available to the author")
    if not any(q["held_out"] for q in photos):
        notes.append("no held-out photo: the final evaluation has no sealed view; generalisation is unmeasured")
    dims = dict(raw.get("dimensions") or {})
    for k, v in dims.items():
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 1 < v < 400):
            raise ConfigError(f"dimension {k} must be a millimetre number in (1, 400) or null")
    legacy_limits = dict(raw.get("limits") or {})
    unknown_limits = sorted(set(legacy_limits) - set(LEGACY_LIMIT_KEYS))
    if unknown_limits:
        raise ConfigError(f"unsupported legacy limits {unknown_limits}")
    if legacy_limits:
        notes.append(f"legacy limits {sorted(legacy_limits)} recorded as provenance only; the CLI's validated limits govern this job")
    author = raw.get("author")
    if author:
        notes.append(f"legacy author.driver {author.get('driver')!r} ignored: the CLI --driver selects the author transport")
    if raw.get("donor"):
        raise ConfigError("donor assets are not supported by this route")
    request = {"product_id": pid, "photos": photos, "dimensions": {k: v for k, v in dims.items() if v is not None}, "notes": str(raw.get("notes") or ""),
               "held_out_views": [v for v in held_views]}
    provenance = {"legacy_limits": legacy_limits, "legacy_author": author, "translation_notes": notes,
                  "original_request_sha256": hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()}
    return {"request": request, "provenance": provenance, "notes": notes}


def _finite_int(name, value, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
        raise ConfigError(f"{name} must be a finite integer")
    v = int(value)
    if not lo <= v <= hi:
        raise ConfigError(f"{name} must be in {lo}..{hi}, got {v}")
    return v


def _image_prune_tokens(value, compact_threshold_tokens: int) -> int | None:
    """None or 0: never prune (stored as None, the behaviour of every job before 2026-09-28); otherwise the input-token
    count of an author request that opens an image prune epoch, below the compaction threshold where the job stops."""
    if value is None or (type(value) is int and value == 0):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("--image-prune-tokens must be an integer (0: never)")
    if not PRUNE_MIN_TOKENS <= value <= compact_threshold_tokens:
        raise ConfigError(f"--image-prune-tokens must be 0 or in {PRUNE_MIN_TOKENS}..{compact_threshold_tokens} (the compaction threshold, where the job stops)")
    return value


def _owner_instruction(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError("--owner-instruction must be text")
    if not value.strip():
        return None
    if len(value) > OWNER_INSTRUCTION_MAX_CHARS:
        raise ConfigError(f"--owner-instruction is {len(value)} characters; at most {OWNER_INSTRUCTION_MAX_CHARS} (it is quoted verbatim to the author)")
    return value


def _editable_modules(value) -> list[str] | None:
    """None (every module), or the program modules edit_program may change: a list or the CLI's comma text of
    candidates.MODULE_ORDER names, de-duplicated in order. Whether a seed exists is the CLI's check (it knows the seed)."""
    if value is None:
        return None
    from ..candidates import MODULE_ORDER
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)) or any(not isinstance(n, str) for n in value):
        raise ConfigError("--editable-modules is a comma list of program module names")
    names = list(dict.fromkeys(n.strip() for n in value if n.strip()))
    unknown = [n for n in names if n not in MODULE_ORDER]
    if not names or unknown:
        raise ConfigError(f"--editable-modules names {unknown or 'nothing'}; the program modules are {list(MODULE_ORDER)}")
    return names


def owner_allowance(*, budget_usd, max_inference_requests, current_cap_micro: int = 0, current_operation_cap: int = 0) -> dict:
    """A change round's allowance as the owner gives it: {'add_micro', 'add_operations', 'budget_usd'}. Both are required
    and positive; a round above OWNER_ROUND_MAX_USD / OWNER_ROUND_MAX_OPERATIONS is refused, and so is one that would lift
    the job's cap above JOB_MAX_USD / JOB_MAX_OPERATIONS."""
    if budget_usd is None or max_inference_requests is None:
        raise ConfigError("a change round needs its allowance: --budget-usd USD and --max-inference-requests N")
    try:
        add_micro = usd_to_micro(budget_usd)
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"--budget-usd: {e}") from None
    if add_micro <= 0:
        raise ConfigError("--budget-usd must be positive")
    if add_micro > usd_to_micro(OWNER_ROUND_MAX_USD):
        raise ConfigError(f"--budget-usd above {OWNER_ROUND_MAX_USD} for one change round is refused")
    ops = _finite_int("--max-inference-requests", max_inference_requests, 1, OWNER_ROUND_MAX_OPERATIONS)
    if current_cap_micro + add_micro > usd_to_micro(JOB_MAX_USD):
        raise ConfigError(f"the job's cap would exceed {JOB_MAX_USD} USD with this allowance")
    if current_operation_cap + ops > JOB_MAX_OPERATIONS:
        raise ConfigError(f"the job's inference-operation cap would exceed {JOB_MAX_OPERATIONS} with this allowance")
    return {"add_micro": add_micro, "add_operations": ops, "budget_usd": str(budget_usd)}


def owner_text(value) -> str:
    """The owner's change request, kept verbatim: text, not blank, at most OWNER_INSTRUCTION_MAX_CHARS."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("--changes needs the owner's words (not blank)")
    if len(value) > OWNER_INSTRUCTION_MAX_CHARS:
        raise ConfigError(f"--changes is {len(value)} characters; at most {OWNER_INSTRUCTION_MAX_CHARS} (it is quoted verbatim to the author)")
    return value


def editable_modules(value) -> list[str] | None:
    """The public form of the editable-modules validation (a change round's module locks reuse it)."""
    return _editable_modules(value)


def build_policy(*, driver: str, worker: str, allow_paid: bool = False, budget_usd=None, max_inference_requests=None, max_output_tokens=None,
                 max_revisions=None, max_worker_seconds=None, wall_minutes=None, images_per_request=None, reasoning_effort: str = "high",
                 cache_mode: str = DEFAULT_CACHE_MODE, compact_threshold_tokens=None, compact_output_bound_tokens=None, ar: bool = True,
                 intake: str = "none", critic: str = "none", final_evaluator: str = "none", service_tier: str = "default", region: str = "global",
                 image_prune_tokens=None, owner_instruction=None, editable_modules=None, owner_review: bool = True,
                 review_resume_token_limit=None, final_on_accept: bool = False) -> dict:
    """The effective, validated policy. Paid mode needs every cap explicit; offline modes get bounded defaults.

    ``owner_review`` (default on for new jobs): request_delivery hands the revision to the owner's live try-on
    (state awaiting_owner) instead of delivering; ``review_resume_token_limit`` is the last author request's input-token
    count above which a change request starts a new seeded job; ``final_on_accept`` runs the configured final evaluator
    on the accepted candidate (off by default under owner review: the owner is the judge). A stored policy without the
    keys (every job before 2026-09-28) reads owner_review as off.

    ``image_prune_tokens`` None or 0 never prunes (the CLI resolves its own default, the compaction threshold minus
    40,000, before calling); ``owner_instruction`` and ``editable_modules`` are the owner-revision mode. A compaction
    threshold above the long-context price cliff (pricing.LONG_CONTEXT_INPUT_TOKENS: the WHOLE request is billed 2x
    input and 1.5x output) is allowed with a ConfigWarning, recorded in the policy's ``warnings``."""
    if driver not in DRIVERS:
        raise ConfigError(f"driver must be one of {DRIVERS}")
    if worker not in WORKERS:
        raise ConfigError(f"worker must be one of {WORKERS}")
    if intake == "astra":
        raise ConfigError(INTAKE_NOT_WIRED)
    if intake not in INTAKES or critic not in CRITICS or final_evaluator not in FINALS:
        raise ConfigError("unknown intake / critic / final-evaluator mode")
    paid = driver == "responses"
    if paid and not allow_paid:
        raise ConfigError("--driver responses needs --allow-paid")
    if not paid and allow_paid:
        raise ConfigError("--allow-paid has no meaning without --driver responses")
    if paid:
        for name, v in (("--budget-usd", budget_usd), ("--max-inference-requests", max_inference_requests), ("--max-output-tokens", max_output_tokens),
                        ("--max-revisions", max_revisions), ("--max-worker-seconds", max_worker_seconds)):
            if v is None:
                raise ConfigError(f"paid mode needs an explicit {name}")
        if worker == "fake":
            raise ConfigError("paid mode with the synthetic worker is refused: nothing real could be delivered")
    elif critic == "responses" or final_evaluator == "responses":
        raise ConfigError("a paid critic / final evaluator needs --driver responses --allow-paid")
    if not isinstance(cache_mode, str) or cache_mode not in CACHE_MODES:
        raise ConfigError(f"--cache-mode must be one of {CACHE_MODES}")
    if budget_usd is None:
        budget_usd = 15
    try:
        cap_micro = usd_to_micro(budget_usd)
    except Exception as e:  # noqa: BLE001
        raise ConfigError(f"--budget-usd: {e}") from None
    if cap_micro <= 0:
        raise ConfigError("--budget-usd must be positive")
    if cap_micro > usd_to_micro(JOB_MAX_USD):
        raise ConfigError(f"--budget-usd above {JOB_MAX_USD} is refused by this runner")
    policy = {"driver": driver, "worker": worker, "allow_paid": bool(allow_paid), "budget_usd": str(budget_usd), "cap_micro": cap_micro,
              "max_inference_requests": _finite_int("--max-inference-requests", 10 if max_inference_requests is None else max_inference_requests, 1, 50),
              "max_output_tokens": _finite_int("--max-output-tokens", 24000 if max_output_tokens is None else max_output_tokens, 256, MODEL_MAX_OUTPUT_TOKENS),
              "max_revisions": _finite_int("--max-revisions", 6 if max_revisions is None else max_revisions, 1, 40),
              "max_worker_seconds": _finite_int("--max-worker-seconds", 600 if max_worker_seconds is None else max_worker_seconds, 10, 3600),
              "wall_minutes": _finite_int("--wall-minutes", 60 if wall_minutes is None else wall_minutes, 1, 60 * 24),
              "images_per_request": _finite_int("--images-per-request", DEFAULT_IMAGES_PER_REQUEST if images_per_request is None else images_per_request, 1, 30),
              "reasoning_effort": reasoning_effort, "cache_mode": cache_mode,
              "compact_threshold_tokens": _finite_int("--compact-threshold-tokens", DEFAULT_COMPACT_THRESHOLD_TOKENS if compact_threshold_tokens is None else compact_threshold_tokens,
                                                  20_000, 900_000),
              "compact_output_bound_tokens": None if compact_output_bound_tokens is None else _finite_int("--compact-output-bound-tokens", compact_output_bound_tokens, 1024, MODEL_MAX_OUTPUT_TOKENS),
              "ar": bool(ar), "intake": intake, "critic": critic, "final_evaluator": final_evaluator, "service_tier": service_tier, "region": region,
              "model": MODEL}
    policy["image_prune_tokens"] = _image_prune_tokens(image_prune_tokens, policy["compact_threshold_tokens"])
    policy["owner_instruction"] = _owner_instruction(owner_instruction)
    policy["editable_modules"] = _editable_modules(editable_modules)
    if not isinstance(owner_review, bool) or not isinstance(final_on_accept, bool):
        raise ConfigError("owner_review and final_on_accept are booleans")
    policy["owner_review"] = owner_review
    # at most the compaction threshold: a change round continued above it would compact (or stop) before the author's first turn
    policy["review_resume_token_limit"] = (min(REVIEW_RESUME_TOKEN_LIMIT, policy["compact_threshold_tokens"]) if review_resume_token_limit is None else
                                           _finite_int("--review-resume-token-limit", review_resume_token_limit, PRUNE_MIN_TOKENS, policy["compact_threshold_tokens"]))
    policy["final_on_accept"] = final_on_accept
    if final_on_accept and not owner_review:
        raise ConfigError("--final-on-accept needs the owner review (without it the final evaluator runs on every delivery)")
    if policy["compact_threshold_tokens"] > LONG_CONTEXT_INPUT_TOKENS:
        text = (f"compact_threshold_tokens {policy['compact_threshold_tokens']:,} is above the long-context price cliff at {LONG_CONTEXT_INPUT_TOKENS:,} "
                "input tokens: every author request between the two is billed 2x input/cache and 1.5x output for the whole request")
        policy["warnings"] = [text]
    if reasoning_effort not in ("low", "medium", "high", "xhigh", "max"):
        raise ConfigError("unsupported reasoning effort")
    try:
        Tariff.frozen(service_tier=service_tier, region=region)   # anything outside the frozen table is a configuration error like every other bad value
    except PricingError as e:
        raise ConfigError(str(e)) from None
    for text in policy.get("warnings", []):     # only a policy that is otherwise valid warns
        warnings.warn(text, ConfigWarning, stacklevel=2)
    return policy
