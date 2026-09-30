"""A request hook for the standard Agents SDK, not an agent execution loop.

Attach ``before_request`` to the inference HTTP client's request hooks. Supply a
separate AsyncOpenAI client for token counting; both inference retries and SDK
model retries must be disabled by the caller. Use a new ledger for each invocation.
Validated per-response usage can settle the latest reservation through the SDK
lifecycle hook. Failed, missing, or unknown responses retain their full hold.

The reused Tariff was checked against the official Astra model page on 2026-09-30:
https://developers.openai.com/api/docs/models/gpt-6-astra
Token counting: https://developers.openai.com/api/docs/guides/token-counting
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import json
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

from modeler.agentic.pricing import MICRO, MODEL, PricingError, Tariff, usd_text, validate_usage

if TYPE_CHECKING:
    import httpx
    from openai import AsyncOpenAI


# The token endpoint's documented fields, copied without rebuilding input items,
# tool schemas, images, reasoning settings, or structured-output schemas.
COUNT_FIELDS = frozenset({
    "conversation", "input", "instructions", "model", "parallel_tool_calls",
    "personality", "previous_response_id", "reasoning", "text", "tool_choice",
    "tools", "truncation",
})
# These create-response settings do not add input content. Unknown new features
# must be reviewed rather than silently omitted from the counting request.
CREATE_FIELDS = frozenset({
    "include", "max_output_tokens", "max_tool_calls", "metadata",
    "prompt_cache_key", "prompt_cache_retention", "store", "stream",
    "service_tier", "temperature", "top_p", "top_logprobs", "user",
    "safety_identifier", "background",
})


class BudgetGuardError(RuntimeError):
    """The request cannot be priced or durably reserved; do not send it."""


class BudgetExceeded(BudgetGuardError):
    """The next reservation would exceed this invocation's dollar cap."""


class BudgetGuard:
    """Count the serialized request and durably reserve its conservative cost.

    One guard owns one new ledger and serializes its own concurrent request hooks.
    Reusing an existing ledger is refused so a restart cannot silently erase spend.
    Only standard-tier Astra at the global Responses endpoint and local function
    tools are supported: hosted-tool fees are outside the reused token tariff.
    """

    def __init__(self, client_for_count: AsyncOpenAI, ledger_path: str | Path,
                 maximum_usd: str | float | Decimal):
        try:
            cap = Decimal(str(maximum_usd))
            if isinstance(maximum_usd, bool) or not cap.is_finite() or cap < 0:
                raise ValueError
            # A spending ceiling must round DOWN, unlike a cost reservation.
            maximum_micro = int((cap * MICRO).to_integral_value(rounding=ROUND_FLOOR))
        except (InvalidOperation, ValueError, OverflowError):
            raise BudgetGuardError("maximum_usd must be a finite non-negative dollar amount") from None
        if str(client_for_count.base_url) != "https://api.openai.com/v1/":
            raise BudgetGuardError("Token counter must use the global OpenAI API endpoint")
        self.client_for_count = client_for_count.with_options(max_retries=0)
        self.ledger_path = Path(ledger_path)
        self.tariff = Tariff.frozen(service_tier="default", region="global")
        self._lock = asyncio.Lock()
        self.blocked_reason: str | None = None
        self.blocked_kind: str | None = None
        self._fatal_error: str | None = None
        self._last_request_size: tuple[int, int] | None = None
        self._ledger = {
            "schema": "blender_agent_budget_v2",
            "accounting": "validated_settlements_plus_unresolved_reservations",
            "maximum_micro_usd": maximum_micro,
            "reserved_micro_usd": 0,
            "tariff": self.tariff.to_dict(),
            "reservations": [],
            "violation": None,
        }
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation establishes ownership; never reset a prior ledger.
        with self.ledger_path.open("x", encoding="utf-8") as stream:
            json.dump(self._ledger, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())

    def summary(self) -> dict:
        """Separate historical holds, usage-priced settlements and unknown costs."""
        cap = self._ledger["maximum_micro_usd"]
        reserved = self._ledger["reserved_micro_usd"]
        settled, unknown = self._commitment()
        settled_count = sum("settled_micro_usd" in row for row in self._ledger["reservations"])
        return {
            "accounting": self._ledger["accounting"],
            "maximum_usd": usd_text(cap),
            "reserved_usd": usd_text(reserved),
            "settled_usd": usd_text(settled),
            "unknown_reserved_usd": usd_text(unknown),
            "committed_usd": usd_text(settled + unknown),
            "remaining_usd": usd_text(max(0, cap - settled - unknown)),
            "requests_reserved": len(self._ledger["reservations"]),
            "requests_settled": settled_count,
            "requests_unsettled": len(self._ledger["reservations"]) - settled_count,
            "violation": self._ledger["violation"],
            "blocked_reason": self.blocked_reason,
            "blocked_kind": self.blocked_kind,
            "planning": self.planning(),
        }

    def planning(self) -> dict:
        """Advisory closeout headroom, never an extra reservation or a quality gate.

        A cached average price cannot authorize the next request: its entire
        worst case must still fit. Allow a few recent-cost responses before that
        cliff, and label the estimate because context and output can grow.
        """
        if self._last_request_size is None:
            return {"status": "awaiting_first_count", "advisory_only": True}
        input_tokens, output_limit = self._last_request_size
        reservation = self.tariff.reserve_micro(input_tokens, output_limit)
        settled, unknown = self._commitment()
        remaining = max(0, self._ledger["maximum_micro_usd"] - settled - unknown)
        recent = [row["settled_micro_usd"] for row in self._ledger["reservations"]
                  if "settled_micro_usd" in row][-3:]
        recent_high = max(recent, default=0)
        finish_balance = reservation + 3 * recent_high
        return {
            "status": "finalize_now" if remaining <= finish_balance else "room_for_targeted_work",
            "advisory_only": True,
            "last_counted_input_tokens": input_tokens,
            "configured_max_output_tokens": output_limit,
            "next_reservation_at_last_size_usd": usd_text(reservation),
            "next_request_fits_at_last_size": remaining >= reservation,
            "recent_high_response_cost_usd": usd_text(recent_high) if recent else None,
            "suggested_finish_balance_usd": usd_text(finish_balance),
            "basis": "One full next-request reservation plus three recent-high response costs. "
                     "Estimate only: input grows; cache misses and output vary. Exact counting still gates every request.",
            "action": "Preserve the best candidate and complete save/export/AR review before optional refinements."
                      if remaining <= finish_balance else
                      "Work on a specific visible mismatch or a check that can change your decision; recheck after major work.",
        }

    def _commitment(self) -> tuple[int, int]:
        rows = self._ledger["reservations"]
        settled = sum(row["settled_micro_usd"] for row in rows if "settled_micro_usd" in row)
        unknown = sum(row["reserved_micro_usd"] for row in rows if "settled_micro_usd" not in row)
        return settled, unknown

    async def settle_latest(self, usage: dict) -> dict:
        """Settle one successful response, once, before the next model request.

        The caller must send one request at a time and pass that response's usage,
        never aggregate run usage. An older unresolved request is never selected
        after the latest request has already been settled.
        """
        try:
            async with self._lock:
                rows = self._ledger["reservations"]
                if not rows or "settled_micro_usd" in rows[-1]:
                    raise BudgetGuardError("The latest reservation is missing or already settled")
                row = rows[-1]
                try:
                    settled = self.tariff.settle_micro(usage)
                    tokens = validate_usage(usage)
                except PricingError:
                    raise BudgetGuardError("Invalid response usage; the full reservation remains held") from None
                if row["input_tokens"] > 0 and tokens["input_tokens"] == 0:
                    raise BudgetGuardError("Missing response usage; the full reservation remains held")
                previous = dict(row)
                row.update(settled_micro_usd=settled, usage=tokens,
                           settled_time=datetime.now(timezone.utc).isoformat())
                if settled > row["reserved_micro_usd"]:
                    self._ledger["violation"] = {
                        "sequence": row["sequence"],
                        "reserved_micro_usd": row["reserved_micro_usd"],
                        "settled_micro_usd": settled,
                    }
                try:
                    self._persist()
                except OSError:
                    row.clear()
                    row.update(previous)
                    self._fatal_error = "Budget settlement persistence failed; further inference is blocked"
                    raise BudgetGuardError(self._fatal_error) from None
                if self._ledger["violation"]:
                    raise BudgetGuardError("Actual response cost exceeded its reservation; further inference is blocked")
                return self.summary()
        except BudgetGuardError as error:
            self.blocked_reason = str(error)
            self.blocked_kind = type(error).__name__
            raise

    def _persist(self) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.ledger_path.parent,
                prefix=".budget-", suffix=".json", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(self._ledger, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.ledger_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    async def before_request(self, request: httpx.Request) -> None:
        """An httpx/httpx2 async request hook; raises before inference is sent."""
        try:
            await self._before_request(request)
        except BudgetGuardError as error:
            # AsyncOpenAI may wrap hook errors in APIConnectionError. The runner
            # can still report the exact refusal without parsing an exception.
            self.blocked_reason = str(error)
            self.blocked_kind = type(error).__name__
            raise

    async def _before_request(self, request: httpx.Request) -> None:
        if request.method != "POST" or str(request.url) != "https://api.openai.com/v1/responses":
            raise BudgetGuardError("Only POST to the global OpenAI /v1/responses endpoint is priced")
        if request.headers.get("x-stainless-retry-count", "0") != "0":
            raise BudgetGuardError("Automatic inference retries must be disabled")
        try:
            payload = json.loads(await request.aread())
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
        if set(payload) - COUNT_FIELDS - CREATE_FIELDS:
            raise BudgetGuardError("Inference request has unreviewed fields for token counting")
        tools = payload.get("tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools
        ):
            raise BudgetGuardError("Only local function tools are covered by this token budget")
        output_limit = payload.get("max_output_tokens")
        try:
            self.tariff.reserve_micro(0, output_limit)
        except PricingError:
            raise BudgetGuardError("An explicit, valid max_output_tokens is required") from None

        async with self._lock:
            if self._fatal_error or self._ledger["violation"]:
                raise BudgetGuardError(self._fatal_error or
                                       "Actual response cost exceeded its reservation; further inference is blocked")
            try:
                count = await self.client_for_count.responses.input_tokens.count(
                    **{key: value for key, value in payload.items() if key in COUNT_FIELDS}
                )
            except Exception:
                # Provider errors may contain the original prompt, images or key.
                raise BudgetGuardError("Token counting failed; inference was not sent") from None
            input_tokens = getattr(count, "input_tokens", None)
            try:
                reservation = self.tariff.reserve_micro(input_tokens, output_limit)
            except PricingError:
                raise BudgetGuardError("Token counter returned an invalid or unsupported input count") from None
            self._last_request_size = (input_tokens, output_limit)
            settled, unknown = self._commitment()
            committed = settled + unknown
            maximum = self._ledger["maximum_micro_usd"]
            if committed + reservation > maximum:
                raise BudgetExceeded(
                    f"Next request needs ${usd_text(reservation)}; "
                    f"${usd_text(committed)} already committed against ${usd_text(maximum)} cap"
                )
            self._ledger["reservations"].append({
                "sequence": len(self._ledger["reservations"]) + 1,
                "time": datetime.now(timezone.utc).isoformat(),
                "input_tokens": input_tokens,
                "max_output_tokens": output_limit,
                "reserved_micro_usd": reservation,
            })
            self._ledger["reserved_micro_usd"] += reservation
            try:
                self._persist()
            except OSError:
                # Retain the in-memory reservation too: a write failure never
                # creates an allowance for another request.
                raise BudgetGuardError("Budget ledger persistence failed; inference was not sent") from None
