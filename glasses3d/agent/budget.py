"""The money gate of one Astra run, and the job ledger that spans a job's runs (plan/WP2_DESIGN.md section 8).

``BudgetGuard`` is the Blender Studio guard (source-before/blender_agent/budget.py:80-331) with the seven WP2 edits. It
is an httpx2 request hook (``before_request``) and response hook (``after_response``) on the inference client, plus an
``on_llm_end`` cross-check for the Agents SDK lifecycle:

* before a request: refuse anything unpriced, refuse a body over the provider's documented per-request limit
  (``PayloadTooLarge``: PAYLOAD_LIMIT_BYTES / PAYLOAD_LIMIT_IMAGES, before counting, $0), count the exact serialized
  payload with a separate counting client under a timeout scaled to the body (``count_timeout_s``), reserve the worst
  case (every input token at the cache-write rate plus the whole output cap), persist the row (fsync, then an atomic
  replace) and only then let the request go;
* one open request at a time: a new request is refused while the previous row is ``held``;
* explicit approval-bound ``settled_only`` mode still records each request's liability, but estimated holds
  never gate spending or shrink output. Admit while shared settled charges are below the approved stop;
  the final response can cross it. Unknown charges still end spending until reconciled;
* after a response: every 200 that names gpt-6-astra with an id and status completed or incomplete is settled from its
  own usage (``settled_from: transport``); a 4xx (429 included) settles at $0 (``rejected_4xx``); a 5xx, a non-JSON or
  otherwise unusable 200, a connection error or a timeout keeps the full hold (``unknown``) and ends the run's spending;
* ``on_llm_end`` never settles: it cross-checks the SDK's response id and usage against the transport settlement and
  blocks further inference on any mismatch;
* the owner reconciles an unknown hold (``reconcile_unknown``: dashboard usage or an explicit no-charge statement;
  ``reconcile_from_transport``: an archived 200 whose id and usage match the request);
* ``planning()`` returns numbers only (the Studio's prose steered Astra, RULES_AUDIT): the last counted request's
  tokens, serialized bytes, images and count time among them (the loop's context guard fires on tokens or bytes);
* every replace is retried on the Windows sharing errors 5/32/33 after .01, .025, .05, .1, .2, .4, .8 and 1.6 s.

``RunLedger`` is ``runs/<run>/budget.json`` (``g3d.run_budget/1``): created exclusively, re-priced and tamper-checked
on load. ``JobLedger`` (a rewrite of trial_budget.py) is ``budget/job-ledger.json``: a per-job non-blocking OS lock,
the machine-wide paid-run lock, totals rebuilt from every ``runs/*/budget.json``, the transaction that consumes an
approval by creating ``budget.json`` exclusively, and the owner's reconcile. Money is integer micro-USD (pricing.py:
every division rounds up; a cap rounds down). Nothing here calls a model except the token count in ``before_request``,
through the caller's counting client. Amounts are execution bounds under the frozen tariff, not invoices.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import TYPE_CHECKING

from .pricing import MICRO, MODEL, PricingError, Tariff, usd_text, usd_to_micro, validate_usage
from .money import reservation_refusal
from .jsonl import read_jsonl

try:  # the machine-wide paid-run lock re-checks its holder with psutil; without it no paid run can start
    import psutil
except ImportError:  # pragma: no cover - psutil ships in glasses3d/.venv
    psutil = None

if TYPE_CHECKING:
    from .approvals import ApprovedRun

LEDGER_SCHEMA = "g3d.run_budget/1"
JOB_LEDGER_SCHEMA = "g3d.job_ledger/1"
PAID_RUN_LOCK_SCHEMA = "g3d.paid_run_lock/1"
PAID_RUN_LOCK_ROOT = Path(__file__).resolve().parents[1] / "data"
LEDGERLESS_RECONCILIATION_SCHEMA = "g3d.run_reconciliation/1"

INFERENCE_URL = "https://api.openai.com/v1/responses"
COUNT_BASE_URL = "https://api.openai.com/v1/"

# The token endpoint's documented fields, copied without rebuilding input items, tool schemas, images, reasoning
# settings or structured-output schemas (unchanged from the Studio).
COUNT_FIELDS = frozenset({
    "conversation", "input", "instructions", "model", "parallel_tool_calls",
    "personality", "previous_response_id", "reasoning", "text", "tool_choice",
    "tools", "truncation",
})
# Create-response settings that add no input content. A new SDK field is refused (and named) until it is reviewed;
# openai-agents 0.22.3 sends include, input, instructions, max_output_tokens, model, parallel_tool_calls,
# prompt_cache_key, reasoning, service_tier and tools.
CREATE_FIELDS = frozenset({
    "include", "max_output_tokens", "max_tool_calls", "metadata",
    "prompt_cache_key", "prompt_cache_retention", "store", "stream",
    "service_tier", "temperature", "top_p", "top_logprobs", "user",
    "safety_identifier", "background",
})

# Edit (1): the exact row fields of g3d.run_budget/1.
ROW_FIELDS = ("sequence", "time", "input_tokens", "max_output_tokens", "reserved_micro_usd", "outcome", "request_id",
              "response_id", "http_status", "settled_micro_usd", "usage", "settled_from", "reason")
OUTCOMES = ("held", "settled", "rejected_4xx", "unknown", "reconciled", "not_sent")
SETTLED_FROM = ("transport", "owner", "host")
SETTLEABLE_STATUSES = ("completed", "incomplete")
FLAT_USAGE_KEYS = ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")

# Edit (7): Windows readers (a status command, an editor, Defender) may briefly deny delete/replace sharing. Retry the
# same durable payload, never an inference or an accounting step; ~3.2 s in all (the Studio's 0.8 s failed on Oakley
# run 1). A permanent failure keeps the hold and blocks inference.
WINDOWS = os.name == "nt"
_REPLACE_DELAYS = (.01, .025, .05, .1, .2, .4, .8, 1.6)
_TRANSIENT_WINDOWS_REPLACE_ERRORS = frozenset({5, 32, 33})

# The provider's documented per-request limits (OpenAI's "Images and vision" guide, read 2026-10-12 for
# v3p3-verify-15: "Up to 512 MB total payload per request", "Up to 1,500 images per request"; nothing separate is
# published for /responses/input_tokens; MB read as 10^6 bytes, the smaller reading). A body over either is refused
# before it is counted (PayloadTooLarge, run status payload_limit, $0): uploading it twice (count, then inference) only
# to be rejected would cost minutes. The largest body this pipeline has sent is 30.78 MB / 134 images (run 4,
# request 32); the v3 projections peak at 46-161 MB and 469 images (v3p3-verify-15), so neither limit is near.
PAYLOAD_LIMIT_BYTES = 512_000_000
PAYLOAD_LIMIT_IMAGES = 1_500
# The count client's timeout for one request: COUNT_TIMEOUT_S + COUNT_TIMEOUT_PER_MB_S per MB (10^6 bytes) of the
# body. httpcore2 writes a one-chunk JSON body inside ONE write timeout (httpcore2 _backends/anyio.py:41-52,
# _async/http11.py:141-156), so the fixed 60 s bounded the whole upload: the paid runs' counts took 1.27 s + 0.316 s
# per MB (156 requests, residual sd 0.66 s, longest 12.31 s at 30.8 MB), so 60 s was reached near 186 MB, and the v3
# projections' 90-reply peaks (108-161 MB) came within 8-25 s of it (v3p3-verify-15: a 15-70 % slower uplink would have
# ended such a run "failed" with no finish, after about $30-45 settled). 1 s per MB keeps about 3x the measured rate
# at every size (161 MB: 221 s against about 52 s).
COUNT_TIMEOUT_S = 60.0
COUNT_TIMEOUT_PER_MB_S = 1.0
# Counting is free and safe to retry; inference remains strictly single-attempt.
COUNT_RETRY_DELAYS = (0.5, 1.0)  # three total attempts, bounded exponential backoff

_TOKEN = re.compile(r"[A-Za-z0-9_.:\-]{1,256}")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}")


class BudgetGuardError(RuntimeError):
    """The request cannot be priced, durably reserved or settled; do not send it (or anything after it)."""


class BudgetExceeded(BudgetGuardError):
    """The next reservation would exceed this run's dollar cap."""


class StageBudgetExceeded(BudgetGuardError):
    """A private stage cannot reserve its next request; no reservation exists and the parent may continue."""

    def __init__(self, message, request):
        super().__init__(message)
        self.request = request


class StageBudget:
    """A bounded slice of the existing ledger, including settled, held and unknown money."""

    def __init__(self, guard, maximum_usd, fit_output_to_budget=None):
        self.guard = guard
        self.maximum_micro = cap_to_micro(maximum_usd)
        if self.maximum_micro <= 0:
            raise BudgetGuardError("stage allowance must be above $0")
        self.start = len(guard.ledger.rows)
        self.before = guard.ledger.totals()
        self.denied = None
        self.fit_output_to_budget = guard.fit_output_to_budget if fit_output_to_budget is None else fit_output_to_budget
        if guard.ledger.settled_only and self.fit_output_to_budget:
            raise BudgetGuardError("settled-only mode cannot fit output to an estimated reservation")

    def summary(self):
        totals = self.guard.ledger.totals()
        fields = ("settled_micro_usd", "held_micro_usd", "unknown_micro_usd", "committed_micro_usd")
        out = {key: totals[key] - self.before[key] for key in fields}
        balance = out["settled_micro_usd" if self.guard.ledger.settled_only else "committed_micro_usd"]
        return {**out, "maximum_micro_usd": self.maximum_micro,
                "remaining_micro_usd": max(0, self.maximum_micro - balance),
                "request_sequences": [row["sequence"] for row in self.guard.ledger.rows[self.start:]],
                "denied": self.denied}


class PayloadTooLarge(BudgetGuardError):
    """The next request's body passes the provider's documented per-request limit (PAYLOAD_LIMIT_BYTES or
    PAYLOAD_LIMIT_IMAGES); it is neither counted nor sent."""


class LedgerPersistError(BudgetGuardError):
    """A ledger write failed; the hold stays committed and inference is blocked."""


class UsageInvalid(BudgetGuardError):
    """A usage block failed validation; it never settles a row."""


class BudgetViolation(BudgetGuardError):
    """A request cost more than its worst-case reservation: the tariff or the count is wrong."""


class InputCountMismatch(BudgetGuardError):
    """The provider usage disagrees with the count that priced the reservation."""


class LedgerInvalid(BudgetGuardError):
    """A budget.json is malformed, re-prices differently or breaks an invariant (tamper or corruption)."""


class ReconcileError(BudgetGuardError):
    """An owner reconcile was refused."""


class JobLedgerError(BudgetGuardError):
    """The job's money state cannot be established safely, or it blocks a paid run."""


class PaidRunBusy(JobLedgerError):
    """Another paid run holds the machine-wide lock."""


# --------------------------------------------------------------------------- small helpers


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cap_to_micro(maximum_usd) -> int:
    """A spending ceiling in micro-USD, rounded DOWN (unlike a cost, which rounds up); bool/NaN/negative refused."""
    try:
        if isinstance(maximum_usd, bool):
            raise ValueError
        cap = Decimal(str(maximum_usd))
        if not cap.is_finite() or cap < 0:
            raise ValueError
        return int((cap * MICRO).to_integral_value(rounding=ROUND_FLOOR))
    except (InvalidOperation, ValueError, OverflowError):
        raise BudgetGuardError("maximum_usd must be a finite non-negative dollar amount") from None


def _io_codes(error: OSError) -> str:
    """Bounded diagnostics without copying OS text or file paths."""
    def code(value):
        return str(value) if type(value) is int else "unknown"
    return f"errno={code(error.errno)}, winerror={code(getattr(error, 'winerror', None))}"


def _transient(error: OSError) -> bool:
    return WINDOWS and getattr(error, "winerror", None) in _TRANSIENT_WINDOWS_REPLACE_ERRORS


def replace_with_retry(source, target) -> None:
    """os.replace, retried on a Windows sharing denial with the edit-(7) schedule; the same file every attempt."""
    for attempt in range(len(_REPLACE_DELAYS) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if not _transient(error) or attempt == len(_REPLACE_DELAYS):
                raise
            time.sleep(_REPLACE_DELAYS[attempt])


def unlink_with_retry(path) -> None:
    for attempt in range(len(_REPLACE_DELAYS) + 1):
        try:
            Path(path).unlink(missing_ok=True)
            return
        except OSError as error:
            if not _transient(error) or attempt == len(_REPLACE_DELAYS):
                raise
            time.sleep(_REPLACE_DELAYS[attempt])


def read_bytes_with_retry(path) -> bytes:
    for attempt in range(len(_REPLACE_DELAYS) + 1):
        try:
            return Path(path).read_bytes()
        except OSError as error:
            if not _transient(error) or attempt == len(_REPLACE_DELAYS):
                raise
            time.sleep(_REPLACE_DELAYS[attempt])
    raise AssertionError("unreachable")  # pragma: no cover


def _dumps(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)


def write_json_atomic(path, value) -> None:
    """Write a complete JSON file next to the target, fsync it, then atomically replace the target (with retry).

    No rewrite, recount, reservation or refund happens between attempts; the target stays the complete old file until
    the replacement wins."""
    path = Path(path)
    payload = _dumps(value)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.stem}-",
                                         suffix=".json", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_json_exclusive(path, value) -> bytes:
    """Create a new JSON file exclusively (open 'x') and fsync it; a partial file is removed. Returns the bytes."""
    path = Path(path)
    data = _dumps(value).encode("utf-8")
    with path.open("xb") as stream:
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            path.unlink(missing_ok=True)   # ours, created moments ago, never acted on
            raise
    return data


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _token(value) -> str | None:
    """A provider id or request id that is safe to store and compare, else None."""
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def _owner_words(words) -> str:
    if not isinstance(words, str) or not words.strip():
        raise ReconcileError("a reconcile needs the owner's exact words")
    return words


def provider_usage(flat: dict) -> dict:
    """The provider-shaped usage block that the flat ledger usage was validated from (lossless for pricing)."""
    return {"input_tokens": flat["input_tokens"], "output_tokens": flat["output_tokens"],
            "total_tokens": flat["input_tokens"] + flat["output_tokens"],
            "input_tokens_details": {"cached_tokens": flat["cached_tokens"],
                                     "cache_write_tokens": flat["cache_write_tokens"]},
            "output_tokens_details": {"reasoning_tokens": flat["reasoning_tokens"]}}


def sdk_usage_dict(usage) -> dict | None:
    """An Agents SDK ``Usage`` (or a provider dict) as the provider-shaped usage block validate_usage reads."""
    if usage is None or isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        return usage.model_dump(mode="json")
    details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "input_tokens_details": None if details is None else {
            "cached_tokens": getattr(details, "cached_tokens", 0) or 0,
            "cache_write_tokens": getattr(details, "cache_write_tokens", 0) or 0},
        "output_tokens_details": None if output_details is None else {
            "reasoning_tokens": getattr(output_details, "reasoning_tokens", 0) or 0},
    }


def ledger_totals(rows: list[dict]) -> dict:
    """Committed money of a run: settled amounts plus the full holds of open (held) and unknown rows."""
    counts = {outcome: 0 for outcome in OUTCOMES}
    settled = held = unknown = reserved = 0
    for row in rows:
        counts[row["outcome"]] += 1
        reserved += row["reserved_micro_usd"]
        if row["outcome"] in ("settled", "rejected_4xx", "reconciled", "not_sent"):
            settled += row["settled_micro_usd"]
        elif row["outcome"] == "held":
            held += row["reserved_micro_usd"]
        else:
            unknown += row["reserved_micro_usd"]
    return {"requests": len(rows), "settled_micro_usd": settled, "held_micro_usd": held, "unknown_micro_usd": unknown,
            "committed_micro_usd": settled + held + unknown, "reserved_total_micro_usd": reserved, "outcomes": counts}


def _new_row(sequence: int, input_tokens: int, max_output_tokens: int, reserved: int) -> dict:
    return {"sequence": sequence, "time": now_iso(), "input_tokens": input_tokens,
            "max_output_tokens": max_output_tokens, "reserved_micro_usd": reserved, "outcome": "held",
            "request_id": None, "response_id": None, "http_status": None, "settled_micro_usd": None, "usage": None,
            "settled_from": None, "reason": None}


# --------------------------------------------------------------------------- the run ledger (budget.json)


def _int(value, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("expected an integer")
    return value


def _validate_flat_usage(usage, tariff: Tariff) -> int:
    if not isinstance(usage, dict) or tuple(sorted(usage)) != tuple(sorted(FLAT_USAGE_KEYS)):
        raise ValueError("usage keys")
    shaped = provider_usage(usage)
    if validate_usage(shaped) != usage:
        raise ValueError("usage does not round-trip")
    return tariff.settle_micro(shaped)


def row_sha256(row: dict) -> str:
    return sha256_bytes(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def validate_ledger(data, tariff: Tariff | None = None) -> dict:
    """Re-price and check a budget.json document; returns its totals or raises LedgerInvalid (fail closed)."""
    tariff = tariff or Tariff.frozen()
    try:
        if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA:
            raise ValueError("schema")
        if data.get("tariff") != tariff.to_dict():
            raise ValueError("tariff")
        cap = _int(data.get("maximum_micro_usd"))
        settled_only = data.get("settled_only", False)
        if type(settled_only) is not bool or (settled_only and cap <= 0):
            raise ValueError("settled-only mode or stop")
        rows = data.get("rows")
        if not isinstance(rows, list):
            raise ValueError("rows")
        if data.get("run_id") is not None and not isinstance(data["run_id"], str):
            raise ValueError("run_id")
        if not isinstance(data.get("created"), str):
            raise ValueError("created")
        for key in ("approval", "violation", "blocked"):
            if data.get(key) is not None and not isinstance(data[key], dict):
                raise ValueError(key)
        approval = data.get("approval")
        if approval is not None and not (
                cap == _int(approval.get("requested_cap_micro_usd")) <= _int(approval.get("max_micro_usd"))):
            raise ValueError("cap differs from the consumed approval")
        if approval is not None and (type(approval.get("settled_only", False)) is not bool
                                     or approval.get("settled_only", False) != settled_only):
            raise ValueError("settled-only mode differs from the consumed approval")
        if not isinstance(data.get("reconciliations"), list):
            raise ValueError("reconciliations")
        reconciliations = {}
        for item in data["reconciliations"]:
            if not isinstance(item, dict):
                raise ValueError("reconciliation entry")
            seq = _int(item["sequence"], minimum=1)
            if seq in reconciliations or seq > len(rows):
                raise ValueError("reconciliation sequence")
            prior = item["prior_row"]
            final = rows[seq - 1]
            if (not isinstance(prior, dict) or prior.get("outcome") not in ("held", "unknown")
                    or prior.get("sequence") != seq or item["prior_row_sha256"] != row_sha256(prior)
                    or item["row_sha256"] != row_sha256(final) or final.get("outcome") != "reconciled"
                    or item["owner_words"] != final.get("reason")
                    or item["usage"] != final.get("usage") or item["settled_from"] != final.get("settled_from")
                    or any(prior.get(key) != final.get(key) for key in
                           ("time", "input_tokens", "max_output_tokens", "reserved_micro_usd"))):
                raise ValueError("reconciliation provenance")
            reconciliations[seq] = item
        over_reservation = False
        prior_settled, prior_unresolved = 0, False
        for index, row in enumerate(rows, 1):
            if settled_only and (prior_settled >= cap or prior_unresolved):
                raise ValueError("request after settled-only stop or unresolved liability")
            if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(ROW_FIELDS)):
                raise ValueError("row fields")
            if type(row["sequence"]) is not int or row["sequence"] != index:
                raise ValueError("sequence")
            if not isinstance(row["time"], str):
                raise ValueError("time")
            reservation = tariff.reserve_micro(row["input_tokens"], row["max_output_tokens"])
            if _int(row["reserved_micro_usd"]) != reservation:
                raise ValueError("reservation re-prices differently")
            outcome = row["outcome"]
            if outcome not in OUTCOMES:
                raise ValueError("outcome")
            for key in ("request_id", "response_id"):
                if row[key] is not None and _token(row[key]) is None:
                    raise ValueError(key)
            status, amount, usage, source = row["http_status"], row["settled_micro_usd"], row["usage"], row["settled_from"]
            if status is not None:
                _int(status, minimum=100)
            if outcome == "held":
                if index != len(rows):
                    raise ValueError("only the last request can be open")
                if any(row[key] is not None for key in ("request_id", "response_id", "http_status",
                                                         "settled_micro_usd", "usage", "settled_from", "reason")):
                    raise ValueError("held row carries an outcome")
                prior_unresolved = True
                continue
            if outcome == "unknown":
                if amount is not None or usage is not None or source is not None:
                    raise ValueError("unknown row carries a settlement")
                if not isinstance(row["reason"], str) or not row["reason"]:
                    raise ValueError("unknown row without a reason")
                prior_unresolved = True
                continue
            if source not in SETTLED_FROM:
                raise ValueError("settled_from")
            if not isinstance(row["reason"], (str, type(None))):
                raise ValueError("reason")
            if outcome == "settled":
                if source != "transport" or status != 200 or row["response_id"] is None:
                    raise ValueError("settled row provenance")
                if _int(amount) != _validate_flat_usage(usage, tariff):
                    raise ValueError("settlement re-prices differently")
                if row["input_tokens"] > 0 and usage["input_tokens"] == 0:
                    raise ValueError("settled without usage")
            elif outcome == "rejected_4xx":
                if source != "transport" or status is None or not 400 <= status < 500 or usage is not None:
                    raise ValueError("rejected row provenance")
                if _int(amount) != 0:
                    raise ValueError("a 4xx settles at zero")
            elif outcome == "not_sent":
                if (source != "host" or _int(amount) != 0 or usage is not None or status is not None
                        or row["request_id"] is not None or row["response_id"] is not None
                        or not row["reason"] or (data.get("blocked") or {}).get("kind") != "NotSent"):
                    raise ValueError("not-sent provenance")
            else:  # reconciled: owner words are the reason
                if index not in reconciliations:
                    raise ValueError("reconciled row without an audit entry")
                if not isinstance(row["reason"], str) or not row["reason"].strip():
                    raise ValueError("reconciled row without the owner's words")
                if usage is None:
                    if source != "owner" or _int(amount) != 0:
                        raise ValueError("no-charge reconcile")
                elif _int(amount) != _validate_flat_usage(usage, tariff):
                    raise ValueError("reconcile re-prices differently")
                if source == "transport" and (status != 200 or row["response_id"] is None or usage is None):
                    raise ValueError("transport reconcile provenance")
            if amount > reservation:
                over_reservation = True
            prior_settled += amount
            if (outcome == "settled" and usage["input_tokens"] != row["input_tokens"]
                    and data.get("violation") is None
                    and (data.get("blocked") or {}).get("kind") != "InputCountMismatch"):
                raise ValueError("input count mismatch without a recorded block")
        if over_reservation and data.get("violation") is None:
            raise ValueError("an amount above its reservation without a recorded violation")
        totals = ledger_totals(rows)
        if data.get("totals") != totals:
            raise ValueError("totals")
        if not settled_only and data.get("violation") is None and totals["committed_micro_usd"] > cap:
            raise ValueError("committed above the cap")
        return totals
    except (KeyError, TypeError, ValueError, PricingError) as error:
        raise LedgerInvalid(f"invalid or tampered budget ledger ({error})") from None


class RunLedger:
    """``runs/<run>/budget.json`` (g3d.run_budget/1). Every mutation persists before it returns; a failed write
    restores the in-memory state (a reservation is kept: a write failure never creates an allowance)."""

    def __init__(self, path: Path, data: dict, *, tariff: Tariff, fresh: bool):
        self.path = Path(path)
        self.data = data
        self.tariff = tariff
        self.fresh = fresh          # created by this process (a guard may attach only to a fresh, empty ledger)
        self.live = False           # attached to a BudgetGuard: a held row is an in-flight request

    # -- construction
    @classmethod
    def create(cls, path, maximum_usd, *, run_id: str | None = None, approval: dict | None = None,
               settled_only: bool = False) -> "RunLedger":
        maximum_micro = cap_to_micro(maximum_usd)
        if type(settled_only) is not bool or (settled_only and maximum_micro <= 0):
            raise BudgetGuardError("settled-only mode requires a boolean and a positive settled stop")
        if approval is not None and approval.get("settled_only", False) is not settled_only:
            raise BudgetGuardError("settled-only mode differs from the consumed approval")
        tariff = Tariff.frozen(service_tier="default", region="global")
        data = {"schema": LEDGER_SCHEMA, "run_id": run_id, "created": now_iso(), "maximum_micro_usd": maximum_micro,
                "tariff": tariff.to_dict(), "approval": approval, "rows": [], "violation": None, "blocked": None,
                "reconciliations": [], "totals": ledger_totals([])}
        if settled_only:
            data["settled_only"] = True
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation establishes ownership (and consumes the run's approval); never reset a prior ledger.
        create_json_exclusive(path, data)
        return cls(path, data, tariff=tariff, fresh=True)

    @classmethod
    def load(cls, path) -> "RunLedger":
        path = Path(path)
        if _is_link(path) or not path.is_file():
            raise LedgerInvalid("budget ledger missing or not a regular file")
        try:
            data = json.loads(read_bytes_with_retry(path).decode("utf-8"))
        except (OSError, ValueError, UnicodeError):
            raise LedgerInvalid("budget ledger unreadable or not JSON") from None
        tariff = Tariff.frozen()
        validate_ledger(data, tariff)
        if data.get("settled_only") and data.get("approval") is not None:
            # This opt-in changes admission semantics. Verify the consumed owner's actual
            # request, not just two editable mode markers in the ledger and its receipt.
            from .approvals import request_sha256
            receipt = data["approval"]
            try:
                request_path, approval_path = path.parent / "approval-request.json", path.parent / "approval.json"
                if _is_link(request_path) or _is_link(approval_path):
                    raise ValueError("approval links")
                request_raw, approval_raw = read_bytes_with_retry(request_path), read_bytes_with_retry(approval_path)
                request, approval = json.loads(request_raw), json.loads(approval_raw)
                settings = request["request"]["settings"]
                if (sha256_bytes(request_raw) != receipt["approval_request_sha256"]
                        or sha256_bytes(approval_raw) != receipt["approval_sha256"]
                        or approval["approval_request_sha256"] != receipt["approval_request_sha256"]
                        or approval["request_sha256"] != receipt["request_sha256"]
                        or request_sha256(request["request"]) != receipt["request_sha256"]
                        or settings.get("settled_only") is not True
                        or settings.get("fit_output_to_budget", False)
                        or cap_to_micro(settings.get("settled_stop_usd")) != data["maximum_micro_usd"]):
                    raise ValueError("settled-only approval binding")
            except (OSError, ValueError, KeyError, TypeError, BudgetGuardError):
                raise LedgerInvalid("settled-only ledger differs from its consumed owner approval") from None
        return cls(path, data, tariff=tariff, fresh=False)

    # -- reading
    @property
    def rows(self) -> list[dict]:
        return self.data["rows"]

    @property
    def maximum_micro(self) -> int:
        return self.data["maximum_micro_usd"]

    @property
    def settled_only(self) -> bool:
        return self.data.get("settled_only", False)

    @property
    def violation(self):
        return self.data["violation"]

    @property
    def blocked(self):
        return self.data["blocked"]

    def totals(self) -> dict:
        return ledger_totals(self.rows)

    def row(self, sequence: int) -> dict:
        if type(sequence) is not int or not 1 <= sequence <= len(self.rows):
            raise ReconcileError(f"no request {sequence!r} in this run")
        return self.rows[sequence - 1]

    def open_row(self) -> dict | None:
        return self.rows[-1] if self.rows and self.rows[-1]["outcome"] == "held" else None

    def sha256(self) -> str:
        return sha256_bytes(read_bytes_with_retry(self.path))

    # -- persistence
    def _persist(self) -> None:
        self.data["totals"] = ledger_totals(self.rows)
        write_json_atomic(self.path, self.data)

    def _commit(self, mutate, *, keep_on_failure: bool = False):
        snapshot = copy.deepcopy(self.data)
        result = mutate()
        try:
            self._persist()
        except OSError as error:
            if not keep_on_failure:
                self.data.clear()
                self.data.update(snapshot)
            raise LedgerPersistError(f"Budget ledger persistence failed ({_io_codes(error)})") from None
        return result

    # -- transitions
    def reserve(self, input_tokens: int, max_output_tokens: int) -> dict:
        """Persist one request's liability after checking the approved reserve or settled-only policy."""
        try:
            reservation = self.tariff.reserve_micro(input_tokens, max_output_tokens)
        except PricingError:
            raise BudgetGuardError("Token counter returned an invalid or unsupported input count") from None
        if self.open_row() is not None:
            raise BudgetGuardError("The previous request has no recorded outcome; one request at a time")
        totals = self.totals()
        if self.settled_only:
            if totals["unknown_micro_usd"] or any(row["outcome"] == "unknown" for row in self.rows):
                raise BudgetGuardError("An unresolved request liability blocks further inference")
            if totals["settled_micro_usd"] >= self.maximum_micro:
                raise BudgetExceeded(f"Settled spend reached the ${usd_text(self.maximum_micro)} stop; no inference sent")
        elif totals["committed_micro_usd"] + reservation > self.maximum_micro:
            raise BudgetExceeded(reservation_refusal(reservation, self.maximum_micro, totals))
        row = _new_row(len(self.rows) + 1, input_tokens, max_output_tokens, reservation)

        def mutate():
            self.rows.append(row)
            return dict(row)
        # Retain the in-memory reservation on a write failure: it never becomes an allowance for another request.
        return self._commit(mutate, keep_on_failure=True)

    def _require_held(self, sequence: int) -> dict:
        row = self.row(sequence)
        if row["outcome"] != "held":
            raise BudgetGuardError(f"Request {sequence} is {row['outcome']}, not held")
        return row

    def settle(self, sequence: int, usage, *, response_id: str, request_id: str | None,
               http_status: int = 200) -> dict:
        """Settle a held row once from a validated usage block (transport); UsageInvalid leaves it held."""
        row = self._require_held(sequence)
        try:
            amount = self.tariff.settle_micro(usage)
            tokens = validate_usage(usage)
        except PricingError as error:
            raise UsageInvalid(f"invalid response usage ({error}); the full reservation remains held") from None
        if row["input_tokens"] > 0 and tokens["input_tokens"] == 0:
            raise UsageInvalid("missing response usage; the full reservation remains held")

        def mutate():
            row.update(outcome="settled", settled_micro_usd=amount, usage=tokens, settled_from="transport",
                       response_id=response_id, request_id=request_id, http_status=http_status)
            if amount > row["reserved_micro_usd"]:
                self.data["violation"] = {"sequence": sequence, "reserved_micro_usd": row["reserved_micro_usd"],
                                          "settled_micro_usd": amount}
            if tokens["input_tokens"] != row["input_tokens"]:
                self.data["blocked"] = {"kind": "InputCountMismatch", "time": now_iso(),
                                        "reason": f"request {sequence}: counted input differs from provider usage"}
            return dict(row)
        settled = self._commit(mutate)
        if self.data["violation"] is not None and self.data["violation"]["sequence"] == sequence:
            raise BudgetViolation("Actual response cost exceeded its reservation; further inference is blocked")
        if tokens["input_tokens"] != row["input_tokens"]:
            raise InputCountMismatch("Provider usage differs from counted input; actual cost recorded, further inference blocked")
        return settled

    def reject_4xx(self, sequence: int, *, http_status: int, request_id: str | None, reason: str) -> dict:
        """A 4xx rejection (validation, authentication, rate or quota) is not billed: settled at zero."""
        if not 400 <= int(http_status) < 500:
            raise BudgetGuardError("only a 4xx rejection settles at zero")
        row = self._require_held(sequence)

        def mutate():
            row.update(outcome="rejected_4xx", settled_micro_usd=0, settled_from="transport", request_id=request_id,
                       http_status=int(http_status), reason=reason)
            return dict(row)
        return self._commit(mutate)

    def mark_unknown(self, sequence: int, *, reason: str, http_status: int | None = None,
                     request_id: str | None = None, response_id: str | None = None) -> dict:
        """The request may have been billed (5xx, unusable 200, connection error, timeout, crash): the whole
        reservation stays counted until the owner reconciles it."""
        row = self._require_held(sequence)

        def mutate():
            row.update(outcome="unknown", reason=reason or "outcome unknown", http_status=http_status,
                       request_id=request_id, response_id=response_id)
            return dict(row)
        return self._commit(mutate)

    def block(self, reason: str, kind: str) -> None:
        """Record a run-level block (cross-check mismatch, persistence failure); best effort on disk."""
        self.data["blocked"] = {"reason": reason, "kind": kind, "time": now_iso()}
        try:
            self._persist()
        except OSError:
            pass

    def not_sent(self, sequence: int, reason: str) -> dict:
        """Only the request hook may call this, before it returns control to the HTTP sender."""
        row = self._require_held(sequence)
        def mutate():
            row.update(outcome="not_sent", settled_micro_usd=0, settled_from="host", reason=reason)
            self.data["blocked"] = {"kind": "NotSent", "reason": reason, "time": now_iso()}
            return dict(row)
        return self._commit(mutate)

    def _reconcilable(self, sequence: int) -> dict:
        row = self.row(sequence)
        if row["outcome"] == "unknown" or (row["outcome"] == "held" and not self.live):
            return row
        raise ReconcileError(f"Request {sequence} is {row['outcome']}; only an unknown hold can be reconciled")

    def _finish_reconcile(self, row: dict, amount: int, *, source: str, tokens, words: str,
                          response_id=None, request_id=None, http_status=None) -> int:
        prior = copy.deepcopy(row)
        def mutate():
            row.update(outcome="reconciled", settled_micro_usd=amount, usage=tokens, settled_from=source,
                       reason=words, response_id=response_id, request_id=request_id, http_status=http_status)
            if amount > row["reserved_micro_usd"]:
                self.data["violation"] = {"sequence": row["sequence"], "reserved_micro_usd": row["reserved_micro_usd"],
                                          "settled_micro_usd": amount}
            self.data["reconciliations"].append({"sequence": row["sequence"], "time": now_iso(),
                                                 "settled_from": source, "owner_words": words, "usage": tokens,
                                                 "prior_row": prior, "prior_row_sha256": row_sha256(prior),
                                                 "row_sha256": row_sha256(row)})
            return amount
        return self._commit(mutate)

    def reconcile_unknown(self, sequence: int, owner_words: str, usage: dict | None = None, *,
                          no_charge: bool = False) -> int:
        """Owner reconcile from the provider's dashboard: verified usage, or an explicit statement of no charge
        (modeler/agentic/budget.py:289-310)."""
        words = _owner_words(owner_words)
        if no_charge == (usage is not None):
            raise ReconcileError("give either the dashboard usage or no_charge=True")
        row = self._reconcilable(sequence)
        if no_charge:
            return self._finish_reconcile(row, 0, source="owner", tokens=None, words=words,
                                          response_id=row["response_id"], request_id=row["request_id"],
                                          http_status=row["http_status"])
        try:
            amount = self.tariff.settle_micro(usage)
            tokens = validate_usage(usage)
        except PricingError as error:
            raise ReconcileError(f"the dashboard usage is invalid ({error})") from None
        return self._finish_reconcile(row, amount, source="owner", tokens=tokens, words=words,
                                      response_id=row["response_id"], request_id=row["request_id"],
                                      http_status=row["http_status"])

    def reconcile_from_transport(self, sequence: int, owner_words: str, response, *, http_status: int,
                                 request_id: str | None = None) -> int:
        """Settle an unknown hold from the archived 200 of that request: gpt-6-astra, a valid id matching any id
        recorded for the row, status completed|incomplete, a validating usage block counting the request's input."""
        words = _owner_words(owner_words)
        row = self._reconcilable(sequence)
        if type(http_status) is not int or http_status != 200:
            raise ReconcileError("only an archived HTTP 200 can settle a request")
        if not isinstance(response, dict):
            raise ReconcileError("the archived response is not a JSON object")
        if response.get("model") != MODEL:
            raise ReconcileError("the archived response does not name gpt-6-astra")
        response_id = _token(response.get("id"))
        if response_id is None:
            raise ReconcileError("the archived response has no valid id")
        if row["response_id"] is not None and response_id != row["response_id"]:
            raise ReconcileError(f"the archived response id differs from the one recorded for request {sequence}")
        request_id = _token(request_id) if request_id is not None else None
        if row["request_id"] is not None and request_id is not None and request_id != row["request_id"]:
            raise ReconcileError(f"the archived request id differs from the one recorded for request {sequence}")
        if response.get("status") not in SETTLEABLE_STATUSES:
            raise ReconcileError("the archived response is neither completed nor incomplete")
        try:
            amount = self.tariff.settle_micro(response.get("usage"))
            tokens = validate_usage(response.get("usage"))
        except PricingError as error:
            raise ReconcileError(f"the archived usage is invalid ({error})") from None
        if tokens["input_tokens"] != row["input_tokens"]:
            raise ReconcileError(f"the archived usage counts {tokens['input_tokens']} input tokens; request "
                                 f"{sequence} counted {row['input_tokens']}")
        return self._finish_reconcile(row, amount, source="transport", tokens=tokens, words=words,
                                      response_id=response_id, request_id=request_id or row["request_id"],
                                      http_status=200)


# --------------------------------------------------------------------------- the guard


def _response_request(response):
    try:
        return response.request
    except (RuntimeError, AttributeError):
        return None


def _json_object(body: bytes):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _error_tokens(payload) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    # Short identifiers only: provider messages may quote the prompt.
    parts = []
    for key in ("type", "code"):
        value = _token(error.get(key))
        if value is not None and len(value) <= 64:
            parts.append(value)
    return " ".join(parts)


def count_timeout_s(body_bytes: int) -> float:
    """The count client's timeout for a body of ``body_bytes`` (COUNT_TIMEOUT_S + COUNT_TIMEOUT_PER_MB_S per MB)."""
    return COUNT_TIMEOUT_S + COUNT_TIMEOUT_PER_MB_S * max(0, int(body_bytes)) / 1e6


def payload_images(value) -> int:
    """The input_image parts anywhere inside a request's input (messages and function_call_outputs)."""
    if isinstance(value, dict):
        return int(value.get("type") == "input_image") + sum(payload_images(v) for v in value.values())
    if isinstance(value, list):
        return sum(payload_images(v) for v in value)
    return 0


def _check_counter(client_for_count) -> None:
    if str(client_for_count.base_url) != COUNT_BASE_URL:
        raise BudgetGuardError("Token counter must use the global OpenAI API endpoint")


class BudgetGuard:
    """Count the serialized request and durably reserve its worst case; settle from the provider's own response.

    One guard owns one new ledger and serializes its own concurrent hooks. Reusing an existing ledger is refused, so a
    restart cannot silently erase spend. Only standard-tier gpt-6-astra at the global Responses endpoint with local
    function tools is supported: hosted-tool fees are outside the token tariff.
    """

    def __init__(self, client_for_count, ledger_path, maximum_usd, *, run_id: str | None = None,
                 approval: dict | None = None, fit_output_to_budget: bool = False, settled_stop_usd=None,
                 settled_only: bool = False):
        cap_to_micro(maximum_usd)       # refuse a bad cap before anything else (no ledger is created)
        settled_stop = None if settled_stop_usd is None else cap_to_micro(settled_stop_usd)
        self._validate_settled_only(settled_only, cap_to_micro(maximum_usd), settled_stop, fit_output_to_budget)
        _check_counter(client_for_count)
        self._setup(client_for_count, RunLedger.create(ledger_path, maximum_usd, run_id=run_id,
                                                     approval=approval, settled_only=settled_only))
        self.fit_output_to_budget = fit_output_to_budget
        self.settled_stop_micro = settled_stop

    @classmethod
    def attach(cls, client_for_count, ledger: RunLedger, *, fit_output_to_budget: bool = False,
               settled_stop_usd=None) -> "BudgetGuard":
        """A guard for a ledger this process just created (JobLedger.open_paid_run): fresh, empty and unattached."""
        if not isinstance(ledger, RunLedger) or not ledger.fresh or ledger.live or ledger.rows:
            raise BudgetGuardError("A guard attaches only to a ledger created by this process with no rows")
        _check_counter(client_for_count)
        settled_stop = None if settled_stop_usd is None else cap_to_micro(settled_stop_usd)
        cls._validate_settled_only(ledger.settled_only, ledger.maximum_micro, settled_stop, fit_output_to_budget)
        guard = cls.__new__(cls)
        guard._setup(client_for_count, ledger)
        guard.fit_output_to_budget = fit_output_to_budget
        guard.settled_stop_micro = settled_stop
        return guard

    @staticmethod
    def _validate_settled_only(mode, maximum, stop, fit_output):
        if type(mode) is not bool:
            raise BudgetGuardError("settled_only must be a boolean")
        if mode and (stop is None or stop != maximum or stop <= 0 or fit_output):
            raise BudgetGuardError("settled-only mode requires an equal positive settled stop and no output fitting")

    def _setup(self, client_for_count, ledger: RunLedger) -> None:
        self.client_for_count = client_for_count.with_options(max_retries=0)
        self.ledger = ledger
        ledger.live = True
        self.tariff = ledger.tariff
        self._lock = asyncio.Lock()
        self.blocked_reason: str | None = None
        self.blocked_kind: str | None = None
        self._fatal_error: str | None = None
        self._last_request_size: tuple[int, int] | None = None
        self._last_request_body: tuple[int, int, int] | None = None     # (serialized bytes, images, count ms)
        self._open_request = None
        self.fit_output_to_budget = False
        self.settled_stop_micro: int | None = None
        self._stage_budget: StageBudget | None = None

    @contextmanager
    def stage_budget(self, maximum_usd, *, fit_output_to_budget=None):
        """Only one stage at a time; entering/exiting never resets global refusal or fatal state."""
        if self._stage_budget is not None or self.ledger.open_row() is not None:
            raise BudgetGuardError("a stage cannot overlap another stage or an unsettled request")
        scope = StageBudget(self, maximum_usd, fit_output_to_budget)
        self._stage_budget = scope
        try:
            yield scope
        finally:
            self._stage_budget = None

    @property
    def ledger_path(self) -> Path:
        return self.ledger.path

    @property
    def open_sequence(self) -> int | None:
        """The sequence of the request in flight (reserved, no outcome yet): name transport/NNNN-* files by it."""
        row = self.ledger.open_row()
        return None if row is None else row["sequence"]

    # -- state
    def _note(self, error: BaseException) -> None:
        self.blocked_reason = str(error)
        self.blocked_kind = type(error).__name__

    def _fatal(self, error: BudgetGuardError, *, persist: bool = True) -> BudgetGuardError:
        """Block every later request of this run; returns the error for the caller to raise."""
        self._fatal_error = str(error)
        if persist:
            self.ledger.block(str(error), type(error).__name__)
        return error

    def summary(self) -> dict:
        """Numbers and identifiers only: settled, open and unknown holds, the cap and what remains."""
        totals = self.ledger.totals()
        cap = self.ledger.maximum_micro
        balance = totals["settled_micro_usd" if self.ledger.settled_only else "committed_micro_usd"]
        remaining = max(0, cap - balance)
        stop = self.settled_stop_micro
        # Planning room includes outstanding liability in either money mode; request
        # admission in settled-only mode still uses actual settled charges only.
        remaining_to_stop = None if stop is None else max(0, stop - totals["committed_micro_usd"])
        outcomes = totals["outcomes"]
        return {
            "maximum_micro_usd": cap, "settled_micro_usd": totals["settled_micro_usd"],
            "held_micro_usd": totals["held_micro_usd"], "unknown_micro_usd": totals["unknown_micro_usd"],
            "committed_micro_usd": totals["committed_micro_usd"], "remaining_micro_usd": remaining,
            "maximum_usd": usd_text(cap), "settled_usd": usd_text(totals["settled_micro_usd"]),
            "held_usd": usd_text(totals["held_micro_usd"]), "unknown_usd": usd_text(totals["unknown_micro_usd"]),
            "committed_usd": usd_text(totals["committed_micro_usd"]), "remaining_usd": usd_text(remaining),
            "settled_stop_usd": None if stop is None else usd_text(stop),
            "remaining_to_stop_usd": None if remaining_to_stop is None else usd_text(remaining_to_stop),
            "requests": totals["requests"], "requests_settled": outcomes["settled"],
            "requests_rejected_4xx": outcomes["rejected_4xx"], "requests_unknown": outcomes["unknown"],
            "requests_held": outcomes["held"], "requests_reconciled": outcomes["reconciled"],
            "violation": self.ledger.violation, "blocked": self.ledger.blocked,
            "blocked_reason": self.blocked_reason, "blocked_kind": self.blocked_kind,
            "planning": self.planning(),
            **({"settled_only": True} if self.ledger.settled_only else {}),
        }

    def planning(self) -> dict:
        """Edit (5): numbers only, never prose or a status word. Exact counting still gates every request. The last
        counted request's serialized bytes, input images and count time (ms) are recorded beside its tokens
        (v3p3-verify-15: bytes and images were only logged; the loop's context guard fires on tokens or bytes)."""
        totals = self.ledger.totals()
        balance = totals["settled_micro_usd" if self.ledger.settled_only else "committed_micro_usd"]
        remaining = max(0, self.ledger.maximum_micro - balance)
        settled = [row["settled_micro_usd"] for row in self.ledger.rows if row["outcome"] == "settled"]
        if self._last_request_size is None:
            input_tokens = output_limit = next_hold = None
        else:
            input_tokens, output_limit = self._last_request_size
            next_hold = self.tariff.reserve_micro(input_tokens, output_limit)
        body_bytes, images, count_ms = self._last_request_body or (None, None, None)
        return {"last_counted_input_tokens": input_tokens, "max_output_tokens": output_limit,
                "next_hold_micro_usd": next_hold, "remaining_micro_usd": remaining,
                "last_settled_micro_usd": settled[-1] if settled else None,
                "recent_high_settled_micro_usd": max(settled[-3:]) if settled else None,
                "requests": totals["requests"], "last_request_bytes": body_bytes, "last_request_images": images,
                "last_count_ms": count_ms}

    def status_hint(self) -> str | None:
        """The run status the money state implies (the loop's result.json): budget_limit, payload_limit,
        provider_unknown, provider_rejected, or None."""
        if self.blocked_kind == "BudgetExceeded":
            return "budget_limit"
        if self.blocked_kind == "PayloadTooLarge":
            return "payload_limit"
        outcomes = [row["outcome"] for row in self.ledger.rows]
        if "unknown" in outcomes or "held" in outcomes:
            return "provider_unknown"
        if outcomes and outcomes[-1] == "rejected_4xx":
            return "provider_rejected"
        return None

    # -- the request hook
    async def before_request(self, request) -> None:
        """An httpx/httpx2 async request hook; raises before inference is sent."""
        try:
            await self._before_request(request)
        except StageBudgetExceeded:
            raise  # the dedicated pre-reservation boundary is local to the stage
        except BudgetGuardError as error:
            # AsyncOpenAI may wrap hook errors; the runner can still read the exact refusal here.
            self._note(error)
            raise

    async def _before_request(self, request) -> None:
        if request.method != "POST" or str(request.url) != INFERENCE_URL:
            raise BudgetGuardError("Only POST to the global OpenAI /v1/responses endpoint is priced")
        if request.headers.get("x-stainless-retry-count", "0") != "0":
            raise BudgetGuardError("Automatic inference retries must be disabled")
        body = await request.aread()
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise BudgetGuardError("Inference request must contain a JSON object") from None
        if not isinstance(payload, dict):
            raise BudgetGuardError("Inference request must contain a JSON object")
        if payload.get("model") != MODEL:
            raise BudgetGuardError("Only gpt-6-astra is priced for this agent")
        if payload.get("service_tier") != "default":
            raise BudgetGuardError("Inference must explicitly use service_tier='default'")
        if payload.get("stream", False) is not False:
            raise BudgetGuardError("Streaming inference is not supported by this guard")
        if payload.get("background", False) is not False:
            raise BudgetGuardError("Background inference is not supported by this guard")
        unreviewed = sorted(set(payload) - COUNT_FIELDS - CREATE_FIELDS)
        if unreviewed:
            names = ", ".join(repr(str(name)[:40]) for name in unreviewed[:10])
            raise BudgetGuardError(f"Inference request has unreviewed fields for token counting: {names}")
        tools = payload.get("tools", [])
        if not isinstance(tools, list) or any(
                not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools):
            raise BudgetGuardError("Only local function tools are covered by this token budget")
        output_limit = payload.get("max_output_tokens")
        try:
            self.tariff.reserve_micro(0, output_limit)
        except PricingError:
            raise BudgetGuardError("An explicit, valid max_output_tokens is required") from None
        body_bytes, images = len(body), payload_images(payload.get("input"))
        if body_bytes > PAYLOAD_LIMIT_BYTES or images > PAYLOAD_LIMIT_IMAGES:
            raise PayloadTooLarge(f"The next request is {body_bytes / 1e6:.1f} MB with {images} images; the "
                                  f"provider's documented limit is {PAYLOAD_LIMIT_BYTES / 1e6:.0f} MB and "
                                  f"{PAYLOAD_LIMIT_IMAGES:,} images per request; inference was not sent")

        async with self._lock:
            if self._fatal_error or self.ledger.violation:
                raise BudgetGuardError(self._fatal_error or
                                       "Actual response cost exceeded its reservation; further inference is blocked")
            open_row = self.ledger.open_row()
            if open_row is not None:
                # Edit (4): the previous request has no recorded outcome; never send a second one beside it.
                raise BudgetGuardError(f"Request {open_row['sequence']} has no recorded outcome yet; "
                                       "one request at a time")
            if self.ledger.settled_only and any(row["outcome"] == "unknown" for row in self.ledger.rows):
                raise BudgetGuardError("An unresolved request liability blocks further inference")
            if (self.settled_stop_micro is not None
                    and self.ledger.totals()["settled_micro_usd"] >= self.settled_stop_micro):
                raise BudgetExceeded(f"Settled spend reached the ${usd_text(self.settled_stop_micro)} stop; "
                                     "no inference sent")
            started = time.perf_counter()
            for attempt in range(len(COUNT_RETRY_DELAYS) + 1):
                try:
                    count = await self.client_for_count.responses.input_tokens.count(
                        **{key: value for key, value in payload.items() if key in COUNT_FIELDS},
                        timeout=count_timeout_s(body_bytes))
                    break
                except Exception:
                    if attempt == len(COUNT_RETRY_DELAYS):
                        # Provider errors may contain the original prompt, images or key.
                        raise BudgetGuardError("Token counting failed after 3 attempts; inference was not sent") from None
                    await asyncio.sleep(COUNT_RETRY_DELAYS[attempt])
            count_ms = round((time.perf_counter() - started) * 1000)
            input_tokens = getattr(count, "input_tokens", None)
            try:
                self.tariff.reserve_micro(input_tokens, output_limit)
            except PricingError:
                raise BudgetGuardError("Token counter returned an invalid or unsupported input count") from None
            if (self._stage_budget.fit_output_to_budget if self._stage_budget is not None
                    else self.fit_output_to_budget):
                remaining = self.ledger.maximum_micro - self.ledger.totals()["committed_micro_usd"]
                if self._stage_budget is not None:
                    remaining = min(remaining, self._stage_budget.summary()["remaining_micro_usd"])
                if self.tariff.reserve_micro(input_tokens, output_limit) > remaining:
                    # No fixed reserve tail: shrink only the response ceiling, keeping exact input and
                    # worst-case per-request accounting. Never spend beyond the owner-approved cap.
                    low, high = 16, output_limit
                    if high < low or self.tariff.reserve_micro(input_tokens, low) > remaining:
                        if (self._stage_budget is not None and self.tariff.reserve_micro(input_tokens, low)
                                <= self.ledger.maximum_micro - self.ledger.totals()["committed_micro_usd"]):
                            self._stage_budget.denied = "stage allowance cannot cover the input and 16 output tokens"
                            raise StageBudgetExceeded(self._stage_budget.denied, request)
                        raise BudgetExceeded("Remaining budget cannot cover this input and a 16-token response; "
                                             "no inference sent")
                    while low < high:
                        mid = (low + high + 1) // 2
                        if self.tariff.reserve_micro(input_tokens, mid) <= remaining:
                            low = mid
                        else:
                            high = mid - 1
                    output_limit = low
                    payload["max_output_tokens"] = output_limit
                    # Pinned httpx2 caches .content separately from the transport stream. Update both;
                    # ReviewLog runs next and archives exactly the bytes the transport receives.
                    replacement = type(request)(request.method, request.url, json=payload)
                    request.stream = replacement.stream
                    request._content = replacement.content
                    request.headers["content-length"] = str(len(replacement.content))
                    request.headers.pop("transfer-encoding", None)
                    body_bytes = len(replacement.content)
            self._last_request_size = (input_tokens, output_limit)
            self._last_request_body = (body_bytes, images, count_ms)
            hold = self.tariff.reserve_micro(input_tokens, output_limit)
            committed = self.ledger.totals()["committed_micro_usd"]
            if not self.ledger.settled_only and hold > self.ledger.maximum_micro - committed:
                raise BudgetExceeded(reservation_refusal(hold, self.ledger.maximum_micro, self.ledger.totals()))
            if (not self.ledger.settled_only and self._stage_budget is not None
                    and hold > self._stage_budget.summary()["remaining_micro_usd"]):
                self._stage_budget.denied = "the next reservation exceeds the stage allowance; no inference sent"
                raise StageBudgetExceeded(self._stage_budget.denied, request)
            try:
                self.ledger.reserve(input_tokens, output_limit)
            except LedgerPersistError as error:
                raise LedgerPersistError(f"{error}; inference was not sent") from None
            self._open_request = request

    # -- the response hook
    async def after_response(self, response) -> dict:
        """An httpx/httpx2 async response hook: classify the open request's outcome from this response alone.

        Returns the row. A 4xx or a non-200 status is recorded and left for the SDK to raise its own status error; an
        unusable 200, a persistence failure or a cost above the reservation raises BudgetGuardError."""
        try:
            body = await response.aread()
            return await self._settle_response(int(response.status_code), body,
                                               request_id=_token(response.headers.get("x-request-id")),
                                               request=_response_request(response))
        except BudgetGuardError as error:
            self._note(error)
            raise

    async def _settle_response(self, status: int, body: bytes, *, request_id, request) -> dict:
        async with self._lock:
            row = self.ledger.open_row()
            if row is None:
                raise self._fatal(BudgetGuardError("A provider response arrived without an open reservation; "
                                                   "further inference is blocked"))
            if request is not None and self._open_request is not None and request is not self._open_request:
                raise self._fatal(BudgetGuardError("A provider response does not belong to the reserved request; "
                                                   "further inference is blocked"))
            sequence = row["sequence"]
            if 400 <= status < 500:
                tokens = _error_tokens(_json_object(body))
                reason = f"http {status}" + (f": {tokens}" if tokens else "")
                result = self._persist_or_block(self.ledger.reject_4xx, sequence, http_status=status,
                                                request_id=request_id, reason=reason)
                self._open_request = None
                return result
            if status != 200:
                return self._unknown_locked(sequence, f"http {status}", http_status=status, request_id=request_id)
            payload = _json_object(body)
            if payload is None:
                self._unknown_locked(sequence, "the 200 body is not a JSON object", http_status=200,
                                     request_id=request_id)
                raise BudgetGuardError(f"Request {sequence}: the provider's 200 body is not a JSON object; "
                                       "the full hold stays as unknown")
            response_id = _token(payload.get("id"))
            problem = None
            if payload.get("model") != MODEL:
                problem = "the 200 does not name gpt-6-astra"
            elif response_id is None:
                problem = "the 200 has no valid response id"
            elif payload.get("status") not in SETTLEABLE_STATUSES:
                shown = _token(payload.get("status"))
                problem = f"the 200 has status {shown or 'other'}, not completed or incomplete"
            if problem is not None:
                self._unknown_locked(sequence, problem, http_status=200, request_id=request_id,
                                     response_id=response_id)
                raise BudgetGuardError(f"Request {sequence}: {problem}; the full hold stays as unknown")
            try:
                settled = self.ledger.settle(sequence, payload.get("usage"), response_id=response_id,
                                             request_id=request_id, http_status=200)
            except UsageInvalid as error:
                self._unknown_locked(sequence, f"usage: {error}", http_status=200, request_id=request_id,
                                     response_id=response_id)
                raise BudgetGuardError(f"Request {sequence}: {error}") from None
            except LedgerPersistError as error:
                # the disk is failing: do not write the block too (it cannot land and doubles the retry wait)
                raise self._fatal(LedgerPersistError(f"{error}; settlement not recorded, further inference is "
                                                     "blocked"), persist=False) from None
            except (BudgetViolation, InputCountMismatch) as error:
                self._open_request = None
                raise self._fatal(error) from None
            self._open_request = None
            return settled

    def _persist_or_block(self, transition, *args, **kwargs) -> dict:
        try:
            return transition(*args, **kwargs)
        except LedgerPersistError as error:
            raise self._fatal(LedgerPersistError(f"{error}; further inference is blocked"), persist=False) from None

    def _unknown_locked(self, sequence: int, reason: str, **fields) -> dict:
        row = self._persist_or_block(self.ledger.mark_unknown, sequence, reason=reason, **fields)
        self._open_request = None
        # An uncertain outcome ends this run's spending: no automatic paid retry (WP2_DESIGN section 7).
        self._fatal(BudgetGuardError(f"Request {sequence} has an unknown outcome ({reason}); the run sends no "
                                     "further requests until the owner reconciles it"))
        return row

    async def mark_unknown(self, reason: str, *, http_status: int | None = None,
                           request_id: str | None = None) -> dict | None:
        """Classify the open request as unknown (no provider response: connection error, timeout, crash, stop).
        Returns the row, or None when no request is open (nothing was reserved, or it already has an outcome)."""
        async with self._lock:
            row = self.ledger.open_row()
            if row is None:
                return None
            return self._unknown_locked(row["sequence"], reason, http_status=http_status,
                                        request_id=_token(request_id))

    async def transport_failed(self, error: BaseException) -> dict | None:
        """The loop's except clause: the transport raised, so no response hook ran."""
        return await self.mark_unknown(f"no provider response ({type(error).__name__})")

    async def not_sent(self, error: BaseException, request) -> None:
        """A host request hook failed: durably record $0 when possible and forbid retry in this run."""
        async with self._lock:
            if (isinstance(error, StageBudgetExceeded) and error.request is request
                    and self.ledger.open_row() is None and self._open_request is None):
                return  # only this known pre-reservation refusal leaves the parent usable
            if self._open_request is not None and self._open_request is not request:
                return  # a concurrent/refused request cannot cancel the request already in flight
            self._fatal_error = "A request hook failed before HTTP send; the run cannot retry"
            row = self.ledger.open_row()
            if row is not None:
                self.ledger.not_sent(row["sequence"], f"request hook failed before HTTP send ({type(error).__name__})")
            self._open_request = None

    # -- the SDK lifecycle cross-check
    async def on_llm_end(self, response) -> None:
        """Cross-check the SDK's parsed response against the transport settlement; block on any mismatch.

        Never settles: the transport hook already did, so an SDK that later raises still leaves a settled row."""
        response_id = getattr(response, "response_id", None)
        usage = sdk_usage_dict(getattr(response, "usage", None))
        async with self._lock:
            rows = self.ledger.rows
            row = rows[-1] if rows else None
            problem = None
            if row is None or row["outcome"] != "settled" or row["settled_from"] != "transport":
                problem = "the SDK reported a response the transport did not settle"
            elif response_id != row["response_id"]:
                problem = "the SDK response id differs from the settled transport response"
            else:
                try:
                    tokens = validate_usage(usage)
                except PricingError:
                    problem = "the SDK usage is invalid"
                else:
                    if tokens != row["usage"]:
                        problem = "the SDK usage differs from the settled transport response"
            if problem is not None:
                error = self._fatal(BudgetGuardError(f"Budget cross-check failed: {problem}; "
                                                     "further inference is blocked"))
                self._note(error)
                raise error

    # -- owner reconcile (live; the CLI reconciles closed runs through JobLedger)
    async def reconcile_unknown(self, sequence: int, owner_words: str, usage: dict | None = None, *,
                                no_charge: bool = False) -> int:
        async with self._lock:
            return self.ledger.reconcile_unknown(sequence, owner_words, usage, no_charge=no_charge)

    async def reconcile_from_transport(self, sequence: int, owner_words: str, response, *, http_status: int,
                                       request_id: str | None = None) -> int:
        async with self._lock:
            return self.ledger.reconcile_from_transport(sequence, owner_words, response, http_status=http_status,
                                                        request_id=request_id)


# --------------------------------------------------------------------------- OS locks


def _os_lock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - the pipeline runs on Windows
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock(stream) -> None:
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _open_locked(path: Path):
    """A non-blocking 1-byte OS lock that the OS releases if the process dies (trial_budget.py:61-79); None if held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        _os_lock(stream)
    except OSError:
        stream.close()
        return None
    return stream


def _norm_exe(path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def process_identity() -> dict:
    if psutil is None:
        raise JobLedgerError("psutil is required for the machine-wide paid-run lock")
    process = psutil.Process()
    return {"pid": process.pid, "create_time": process.create_time(), "exe": _norm_exe(process.exe())}


def identity_alive(identity) -> bool:
    """Is the process named by {pid, create_time, exe} still running (psutil; pid reuse is not the same process)?"""
    if psutil is None:
        raise JobLedgerError("psutil is required for the machine-wide paid-run lock")
    if not isinstance(identity, dict):
        return False
    pid, created, exe = identity.get("pid"), identity.get("create_time"), identity.get("exe")
    if type(pid) is not int or pid <= 0 or not isinstance(created, (int, float)) or isinstance(created, bool):
        return False
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - float(created)) > 0.01:
            return False
        try:
            if isinstance(exe, str) and _norm_exe(process.exe()) != os.path.normcase(exe):
                return False
        except psutil.AccessDenied:
            pass    # alive and not inspectable: conservatively the same process
        return process.is_running()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True


class PaidRunLock:
    """``data/.paid-run.lock`` {pid, create_time, exe}: one paid run on this machine at a time.

    The holder's identity is re-checked with psutil, so a crashed runner's file is recognised as stale; a sidecar OS
    lock (``.paid-run.guard``, released by the OS on a crash) is held for the run, which makes check-and-take atomic."""

    def __init__(self, jobs_root):
        self.jobs_root = Path(jobs_root)
        self.path = self.jobs_root / ".paid-run.lock"
        self.guard_path = self.jobs_root / ".paid-run.guard"
        self._guard = None
        self.identity: dict | None = None
        self.replaced_stale: dict | None = None

    def read(self) -> dict | None:
        try:
            value = json.loads(read_bytes_with_retry(self.path).decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            return {"malformed": True}
        return value if isinstance(value, dict) else {"malformed": True}

    @staticmethod
    def _describe(holder) -> str:
        if not isinstance(holder, dict) or holder.get("malformed"):
            return "an unidentified process"
        return f"pid {holder.get('pid')} (job {holder.get('job_id')}, run {holder.get('run_id')})"

    def acquire(self, *, job_id: str, run_id: str) -> dict:
        if self._guard is not None:
            raise PaidRunBusy("this process already holds the paid-run lock")
        me = process_identity()
        guard = _open_locked(self.guard_path)
        if guard is None:
            raise PaidRunBusy(f"Another paid run is active on this machine: {self._describe(self.read())}")
        try:
            existing = self.read()
            # Holding the OS guard, a file naming this very process is our own leftover (a failed unlink), not a run.
            ours = (isinstance(existing, dict) and existing.get("pid") == me["pid"]
                    and existing.get("create_time") == me["create_time"])
            if existing is not None and not existing.get("malformed") and not ours and identity_alive(existing):
                raise PaidRunBusy(f"Another paid run is active on this machine: {self._describe(existing)}")
            self.replaced_stale = existing
            identity = {"schema": PAID_RUN_LOCK_SCHEMA, **me, "job_id": job_id, "run_id": run_id,
                        "acquired": now_iso()}
            write_json_atomic(self.path, identity)
        except BaseException:
            _os_unlock(guard)
            raise
        self._guard, self.identity = guard, identity
        return identity

    def release(self) -> None:
        guard, self._guard = self._guard, None
        if guard is None:
            return
        try:
            existing = self.read()
            if (isinstance(existing, dict) and self.identity is not None
                    and existing.get("pid") == self.identity["pid"]
                    and existing.get("create_time") == self.identity["create_time"]):
                unlink_with_retry(self.path)
        finally:
            _os_unlock(guard)


# --------------------------------------------------------------------------- the job ledger


class JobLedger:
    """``budget/job-ledger.json`` of one job (a rewrite of trial_budget.py). Hold it as a context manager for a paid
    run or an owner reconcile: it takes the job's non-blocking OS lock and rebuilds the totals from every
    ``runs/*/budget.json`` (re-priced and tamper-checked). Caps are per run (each run has its own owner approval);
    unknown holds count against the job until reconciled; a run without ``budget.json`` counts $0 when it has no
    transport files (``aborted_before_paid``) and blocks paid runs until the owner reconciles it when it has some."""

    def __init__(self, job_dir):
        self.job_dir = Path(job_dir).resolve()
        self.runs_dir = self.job_dir / "runs"
        self.budget_dir = self.job_dir / "budget"
        self.ledger_path = self.budget_dir / "job-ledger.json"
        self.lock_path = self.budget_dir / ".job-ledger.lock"
        self.tariff = Tariff.frozen()
        self._lock_stream = None
        self._paid_lock: PaidRunLock | None = None
        self._active: tuple[str, RunLedger] | None = None
        self.last_scan: dict | None = None

    # -- the lock
    def __enter__(self) -> "JobLedger":
        if self._lock_stream is not None:
            raise JobLedgerError("This job ledger context is already entered")
        stream = _open_locked(self.lock_path)
        if stream is None:
            raise JobLedgerError("Another process holds this job's budget lock")
        self._lock_stream = stream
        try:
            self.scan()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._active is not None:
                self.close_paid_run()
        finally:
            stream, self._lock_stream = self._lock_stream, None
            if stream is not None:
                _os_unlock(stream)

    def _require_lock(self) -> None:
        if self._lock_stream is None:
            raise JobLedgerError("Enter and hold the job ledger context first")

    # -- runs
    def run_dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise JobLedgerError("a run id is letters, digits, '.', '_' or '-'")
        path = self.runs_dir / run_id
        if path.exists() and (_is_link(path) or path.resolve().parent != self.runs_dir.resolve()):
            raise JobLedgerError("Run directories must stay inside this job")
        return path

    def _run_dirs(self) -> tuple[list[Path], list[str]]:
        if not self.runs_dir.exists():
            return [], []
        directories, problems = [], []
        root = self.runs_dir.resolve()
        for path in sorted(self.runs_dir.iterdir()):
            if _is_link(path) or (path.is_dir() and path.resolve().parent != root):
                problems.append(f"run {path.name}: a link or junction; run directories must stay inside this job")
            elif path.is_dir():
                directories.append(path)
        return directories, problems

    @staticmethod
    def _transport_files(run_dir: Path) -> list[Path]:
        transport = run_dir / "transport"
        if not transport.is_dir():
            return []
        return sorted(path for path in transport.rglob("*") if path.is_file())

    def _scan_run(self, run_dir: Path) -> dict:
        run_id = run_dir.name
        budget = run_dir / "budget.json"
        entry = {"run_id": run_id, "status": None, "settled_micro_usd": 0, "unknown_micro_usd": 0,
                 "requests": 0, "open_requests": [], "maximum_micro_usd": None, "budget_sha256": None,
                 "violation": None, "blocked": None, "approval": None, "blocking": None, "warning": None}
        if not budget.exists() and not _is_link(budget):
            transport = self._transport_files(run_dir)
            reconciliation = run_dir / "reconciliation.json"
            if not transport:
                entry["status"] = "pending_approval" if (run_dir / "approval-request.json").is_file() else "aborted_before_paid"
                return entry
            if reconciliation.is_file():
                try:
                    record = json.loads(read_bytes_with_retry(reconciliation).decode("utf-8"))
                    if (not isinstance(record, dict) or record.get("schema") != LEDGERLESS_RECONCILIATION_SCHEMA
                            or record.get("run_id") != run_id
                            or not isinstance(record.get("owner_words"), str) or not record["owner_words"].strip()):
                        raise ValueError
                    entry["settled_micro_usd"] = _int(record.get("settled_micro_usd"))
                except (OSError, ValueError, UnicodeError):
                    entry["status"] = "invalid"
                    entry["blocking"] = f"run {run_id}: reconciliation.json is malformed"
                    return entry
                entry["status"] = "reconciled_ledgerless"
                return entry
            entry["status"] = "ledgerless_with_transport"
            entry["blocking"] = (f"run {run_id} has {len(transport)} transport file(s) but no budget.json; the owner "
                                 f"must reconcile it (job reconcile --run {run_id} --usage|--no-charge)")
            return entry
        try:
            ledger = RunLedger.load(budget)
        except LedgerInvalid as error:
            has_transport = bool(self._transport_files(run_dir))
            recovery = run_dir / "aborted-reconciliation.json"
            if not has_transport and recovery.is_file():
                try:
                    rec = json.loads(read_bytes_with_retry(recovery))
                    if (rec.get("schema") == "g3d.aborted_reconciliation/1" and rec.get("run_id") == run_id
                            and isinstance(rec.get("owner_words"), str) and rec["owner_words"].strip()
                            and rec.get("budget_sha256") == sha256_bytes(read_bytes_with_retry(budget))
                            and rec.get("no_charge") is True):
                        entry["status"] = "reconciled_aborted"
                        return entry
                except (OSError, ValueError, AttributeError):
                    pass
            entry["status"] = "invalid"
            entry["blocking"] = (f"run {run_id}: {error}; the run has "
                                 f"{'transport files' if has_transport else 'no transport files'}")
            return entry
        totals = ledger.totals()
        live = self._active is not None and self._active[0] == run_id
        entry.update(status="ledger", requests=totals["requests"], maximum_micro_usd=ledger.maximum_micro,
                     settled_micro_usd=totals["settled_micro_usd"],
                     # a closed run's open row never got an outcome: it is an unknown hold like any other
                     unknown_micro_usd=totals["unknown_micro_usd"] + (0 if live else totals["held_micro_usd"]),
                     open_requests=[row["sequence"] for row in ledger.rows if row["outcome"] in ("held", "unknown")],
                     budget_sha256=ledger.sha256(), violation=ledger.violation, blocked=ledger.blocked,
                     approval=ledger.data.get("approval"))
        entry["closed"] = not live
        entry["row_sha256s"] = [row_sha256(row) for row in ledger.rows]
        entry["reconciliation_priors"] = {str(item["sequence"]): item["prior_row_sha256"]
                                           for item in ledger.data["reconciliations"]}
        # A valid prefix is still a truncated ledger if a later request reached the transport archive.
        sequences = [int(match.group(1)) for path in self._transport_files(run_dir)
                     if (match := re.match(r"^(\d+)-request(?:\.|$)", path.name))]
        if sequences and max(sequences) > len(ledger.rows):
            entry["blocking"] = f"run {run_id}: transport request high-water mark exceeds the budget rows"
        responded = {int(match.group(1)) for path in self._transport_files(run_dir)
                     if (match := re.match(r"^(\d+)-response(?:\.|$)", path.name))}
        if any(row["outcome"] == "not_sent" and row["sequence"] in responded for row in ledger.rows):
            entry["blocking"] = f"run {run_id}: a not-sent row has an archived provider response"
        if live:
            entry["held_micro_usd"] = totals["held_micro_usd"]
        if ledger.violation is not None:
            entry["blocking"] = (f"run {run_id}: request {ledger.violation.get('sequence')} cost more than its "
                                 "worst case; the tariff or the count is wrong, paid runs stay blocked until the code "
                                 "is fixed")
        elif (ledger.blocked or {}).get("kind") == "InputCountMismatch":
            entry["blocking"] = f"run {run_id}: token count disagrees with usage; paid runs stay blocked"
        approval = ledger.data.get("approval")
        if approval is None:
            entry["warning"] = f"run {run_id}: budget.json names no owner approval"
        else:
            approval_path = run_dir / "approval.json"
            try:
                actual = sha256_bytes(read_bytes_with_retry(approval_path))
            except OSError:
                actual = None
            if actual != approval.get("approval_sha256"):
                entry["warning"] = f"run {run_id}: approval.json no longer matches the approval this run consumed"
        return entry

    def scan(self, *, write: bool = True) -> dict:
        """Re-price run ledgers and cross-check the archive and retained closed-run row hashes."""
        directories, problems = self._run_dirs()
        runs = [self._scan_run(path) for path in directories]
        anchors = {}
        if self.ledger_path.exists():
            try:
                previous = json.loads(read_bytes_with_retry(self.ledger_path))
                if previous.get("schema") != JOB_LEDGER_SCHEMA or previous.get("job_id") != self.job_dir.name:
                    raise ValueError
                anchors = previous.get("closed_run_anchors", {})
                if not isinstance(anchors, dict):
                    raise ValueError
            except (OSError, ValueError, TypeError, AttributeError):
                problems.append("The prior job ledger cannot be validated; preserve it and reconcile before paid work")
                # Never erase the only historical checkpoint after a read/corruption failure.
                write = False
        for run_id, hashes in anchors.items():
            run = next((entry for entry in runs if entry["run_id"] == run_id), None)
            if run and run["blocking"]:
                continue  # already refused; retain its old anchor, without duplicating the same run's error
            current = run.get("row_sha256s", []) if run else []
            if (not isinstance(hashes, list) or len(current) < len(hashes)
                    or any(current[index] != digest and
                           run.get("reconciliation_priors", {}).get(str(index + 1)) != digest
                           for index, digest in enumerate(hashes) if index < len(current))):
                problems.append(f"run {run_id}: closed budget rows were removed or changed outside reconciliation")
        # Preserve old anchors even while a run is invalid, missing or changed. Do not bless tampering on scan #2.
        if not problems:
            for run in runs:
                if run.get("closed") and not run["blocking"]:
                    anchors[run["run_id"]] = run["row_sha256s"]
        blocking = problems + [run["blocking"] for run in runs if run["blocking"]]
        warnings = [run["warning"] for run in runs if run["warning"]]
        settled = sum(run["settled_micro_usd"] for run in runs)
        unknown = sum(run["unknown_micro_usd"] for run in runs)
        held = sum(run.get("held_micro_usd", 0) for run in runs)
        record = {"schema": JOB_LEDGER_SCHEMA, "job_id": self.job_dir.name, "rebuilt": now_iso(),
                  "closed_run_anchors": anchors,
                  "tariff": self.tariff.to_dict(), "runs": runs,
                  "totals": {"settled_micro_usd": settled, "unknown_micro_usd": unknown, "held_micro_usd": held,
                             "committed_micro_usd": settled + unknown + held, "settled_usd": usd_text(settled),
                             "unknown_usd": usd_text(unknown), "committed_usd": usd_text(settled + unknown + held),
                             "runs": len(runs), "requests": sum(run["requests"] for run in runs)},
                  "blocking": blocking, "warnings": warnings}
        if write:
            self._require_lock()
            self.budget_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.ledger_path, record)
        self.last_scan = record
        return record

    def summary(self) -> dict:
        return self.last_scan if self.last_scan is not None else self.scan(write=False)

    # -- the paid-run transaction
    def preflight(self, run_id: str, *, current_request_sha256: str, requested_cap_usd=None) -> "ApprovedRun":
        """The money part of the $0 preflight: approval valid, no blocking run, the machine lock free. Creates
        nothing; raises ApprovalRefused (exit code 3), JobLedgerError or PaidRunBusy."""
        from .approvals import check_approval
        self._require_lock()
        approved = check_approval(self.run_dir(run_id), current_request_sha256=current_request_sha256,
                                  requested_cap_usd=requested_cap_usd)
        scan = self.scan()
        if scan["blocking"]:
            raise JobLedgerError("This job's money state blocks a paid run: " + "; ".join(scan["blocking"]))
        holder = PaidRunLock(PAID_RUN_LOCK_ROOT).read()
        if holder is not None and not holder.get("malformed") and identity_alive(holder):
            raise PaidRunBusy(f"Another paid run is active on this machine: {PaidRunLock._describe(holder)}")
        return approved

    def open_paid_run(self, run_id: str, *, current_request_sha256: str, requested_cap_usd=None) -> RunLedger:
        """Consume the run's approval and create runs/<run>/budget.json exclusively, in one transaction under the
        job lock and the machine-wide paid-run lock. Only after this may the inference clients be built and the
        first request sent (BudgetGuard.attach). Raises with nothing created when anything is off."""
        from .approvals import check_approval
        self._require_lock()
        if self._active is not None:
            raise JobLedgerError("One paid run per job ledger context")
        run_dir = self.run_dir(run_id)
        approved = check_approval(run_dir, current_request_sha256=current_request_sha256,
                                  requested_cap_usd=requested_cap_usd)
        scan = self.scan()
        if scan["blocking"]:
            raise JobLedgerError("This job's money state blocks a paid run: " + "; ".join(scan["blocking"]))
        paid = PaidRunLock(PAID_RUN_LOCK_ROOT)
        paid.acquire(job_id=self.job_dir.name, run_id=run_id)
        try:
            cap = Decimal(approved.requested_cap_micro) / Decimal(MICRO)     # exact; floors back to the same micro
            ledger = RunLedger.create(run_dir / "budget.json", str(cap), run_id=run_id,
                                      approval=approved.binding(), settled_only=approved.settled_only)
        except BaseException:
            paid.release()
            raise
        self._paid_lock, self._active = paid, (run_id, ledger)
        return ledger

    def close_paid_run(self) -> dict:
        """End the active paid run: an open request becomes unknown, the machine lock is released, totals rebuilt."""
        self._require_lock()
        active, self._active = self._active, None
        paid, self._paid_lock = self._paid_lock, None
        try:
            if active is not None:
                ledger = active[1]
                row = ledger.open_row()
                if row is not None:
                    try:
                        ledger.mark_unknown(row["sequence"], reason="the run ended before this request had an outcome")
                    except LedgerPersistError:
                        pass    # still held on disk: a closed run's held row counts as unknown in every scan
        finally:
            if paid is not None:
                paid.release()
        return self.scan()

    # -- owner reconcile
    def reconcile_aborted_run(self, run_id: str, owner_words: str) -> None:
        """Owner-confirmed $0 recovery of a failed exclusive ledger creation. Preserve the invalid bytes forever."""
        self._require_lock()
        words = _owner_words(owner_words)
        run = self.run_dir(run_id)
        if self._active is not None or self._transport_files(run):
            raise ReconcileError("an aborted recovery requires a closed run without transport evidence")
        hashes = (self.summary().get("closed_run_anchors") or {}).get(run_id, [])
        if hashes:
            raise ReconcileError("a previously recorded request cannot be erased as an aborted creation")
        budget = run / "budget.json"
        try:
            RunLedger.load(budget)
        except LedgerInvalid:
            if not budget.is_file() or _is_link(budget):
                raise ReconcileError("an aborted recovery needs the preserved partial budget file") from None
        else:
            raise ReconcileError("a valid ledger must use request reconciliation")
        create_json_exclusive(run / "aborted-reconciliation.json", {
            "schema": "g3d.aborted_reconciliation/1", "run_id": run_id, "time": now_iso(),
            "owner_words": words, "no_charge": True, "budget_sha256": sha256_bytes(read_bytes_with_retry(budget))})
        self.scan()

    def _closed_ledger(self, run_id: str) -> RunLedger:
        self._require_lock()
        if self._active is not None and self._active[0] == run_id:
            raise ReconcileError("Reconcile a run after it has ended")
        return RunLedger.load(self.run_dir(run_id) / "budget.json")

    def reconcile_unknown(self, run_id: str, sequence: int, owner_words: str, usage: dict | None = None, *,
                          no_charge: bool = False) -> int:
        amount = self._closed_ledger(run_id).reconcile_unknown(sequence, owner_words, usage, no_charge=no_charge)
        self.scan()
        return amount

    def reconcile_from_transport(self, run_id: str, sequence: int, owner_words: str) -> int:
        """Read this request's archive and receipt ourselves; callers cannot supply a replacement response."""
        ledger = self._closed_ledger(run_id)
        run = self.run_dir(run_id)
        path = run / "transport" / f"{sequence:04d}-response.json"
        try:
            if _is_link(path) or _is_link(path.parent):
                raise ValueError
            raw = read_bytes_with_retry(path)
            response = json.loads(raw)
            event_log = read_jsonl(run / "events.jsonl")
            events = event_log.rows
            matches = [e for e in events if e.get("event") == "api_response" and e.get("request_seq") == sequence]
            if len(matches) != 1:
                raise ValueError
            event = matches[0]
            if (event.get("payload_path") != path.relative_to(run).as_posix()
                    or event.get("archived_sha256") != sha256_bytes(raw)):
                raise ValueError
        except (OSError, ValueError, TypeError, AttributeError):
            raise ReconcileError("the request's archived response and matching receipt are missing or changed") from None
        amount = ledger.reconcile_from_transport(sequence, owner_words, response,
                                                 http_status=event.get("http_status"), request_id=event.get("request_id"))
        if event_log.recovery:
            from .procs import write_json_atomic
            write_json_atomic(run / "events-tail-recovery.json", event_log.recovery)
        self.scan()
        return amount

    def reconcile_ledgerless_run(self, run_id: str, owner_words: str, *, charged_usd=None,
                                 no_charge: bool = False) -> int:
        """A run with transport files but no budget.json: the owner states the dashboard charge (or none)."""
        self._require_lock()
        words = _owner_words(owner_words)
        if no_charge == (charged_usd is not None):
            raise ReconcileError("give either the dashboard charge or no_charge=True")
        run_dir = self.run_dir(run_id)
        if (run_dir / "budget.json").exists() or not self._transport_files(run_dir):
            raise ReconcileError(f"run {run_id} is not a run with transport files and no budget.json")
        if charged_usd is None:
            amount = 0
        else:
            try:
                amount = usd_to_micro(charged_usd)      # a cost: rounded UP below the micro-dollar
            except PricingError:
                raise ReconcileError("the charge must be a finite non-negative dollar amount") from None
        create_json_exclusive(run_dir / "reconciliation.json", {
            "schema": LEDGERLESS_RECONCILIATION_SCHEMA, "run_id": run_id, "settled_micro_usd": amount,
            "no_charge": bool(no_charge), "owner_words": words, "time": now_iso()})
        self.scan()
        return amount
