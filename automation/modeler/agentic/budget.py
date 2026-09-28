"""One transactional dollar and call gateway for every paid role of a job.

Before any inference request (author, intake reading, intake review, critic, final evaluator, compaction) the caller
reserves the worst case under the frozen tariff inside one ``BEGIN IMMEDIATE`` transaction that enforces
``settled + unknown liability + held reservations + new <= cap`` and the total inference-operation cap. Two processes
racing for the last allowance cannot both pass: SQLite serialises the transaction. A reservation is settled once from
the response's validated usage, marked unknown (its whole amount stays counted) when the outcome is uncertain or the
usage is malformed, or released once with a recorded reason when the request was never sent. Caps live in the job row
and survive restarts; only ``grant_allowance`` (an owner's change round) changes them: to the amounts committed at the grant plus
exactly the allowance, so a round spends its grant and never an earlier round's leftover; a held or unknown amount counted at
the grant that resolves lower (released, settled, reconciled) lowers the caps by the difference, never becoming round money. Numbers are micro-USD; this is an execution bound, not an invoice.
"""
from __future__ import annotations

from .pricing import PricingError, Tariff, usd_text, validate_usage
from .state import Store, StateError, canonical_json

PAID_PURPOSES = ("author", "intake_reading", "intake_review", "critic", "final", "compaction")


class BudgetError(StateError):
    pass


class BudgetExhausted(BudgetError):
    pass


def _usage_record(usage) -> dict:
    """The rejected usage block as it can be stored: the object itself when it serialises, otherwise its text (a
    non-finite number or a foreign type must not abort the transaction that records the unknown liability)."""
    record = usage if isinstance(usage, dict) else {"raw": str(usage)}
    try:
        canonical_json(record)
    except (TypeError, ValueError):
        record = {"raw": repr(usage)[:2000]}
    return record


class Budget:
    def __init__(self, store: Store):
        self.store = store
        job = store.job()
        self.tariff = Tariff.from_dict(job["tariff"])

    # the caps are read from the job row on every use: the owner review loop resets them (Store.grant_allowance: committed + the
    # owner's allowance) while a Budget object may be alive, and a stale copy would refuse or permit the wrong amount
    @property
    def cap_micro(self) -> int:
        return int(self.store.conn.execute("SELECT cap_micro FROM job WHERE id = 1").fetchone()[0])

    @property
    def operation_cap(self) -> int:
        return int(self.store.conn.execute("SELECT inference_operation_cap FROM job WHERE id = 1").fetchone()[0])

    # ------------------------------------------------------------------ totals
    def totals(self) -> dict:
        rows = self.store.reservations()
        settled = sum(int(r["settled_micro"] or 0) for r in rows if r["state"] == "settled")
        unknown = sum(int(r["liability_micro"] or 0) for r in rows if r["state"] == "unknown")
        held = sum(int(r["reserved_micro"]) for r in rows if r["state"] == "held")
        ops = sum(1 for r in rows if r["counts_operation"] and r["state"] in ("held", "settled", "unknown"))
        upper = settled + unknown + held
        per_role: dict[str, dict] = {}
        for r in rows:
            d = per_role.setdefault(r["role"], {"settled_micro": 0, "unknown_liability_micro": 0, "held_micro": 0, "operations": 0})
            if r["state"] == "settled":
                d["settled_micro"] += int(r["settled_micro"] or 0)
            elif r["state"] == "unknown":
                d["unknown_liability_micro"] += int(r["liability_micro"] or 0)
            elif r["state"] == "held":
                d["held_micro"] += int(r["reserved_micro"])
            if r["counts_operation"] and r["state"] in ("held", "settled", "unknown"):
                d["operations"] += 1
        return {"cap_micro": self.cap_micro, "cap_usd": usd_text(self.cap_micro), "settled_micro": settled, "settled_usd": usd_text(settled),
                "unknown_liability_micro": unknown, "unknown_liability_usd": usd_text(unknown), "held_micro": held, "held_usd": usd_text(held),
                "upper_bound_micro": upper, "upper_bound_usd": usd_text(upper), "remaining_micro": max(self.cap_micro - upper, 0),
                "remaining_usd": usd_text(max(self.cap_micro - upper, 0)), "operations_used": ops, "operations_cap": self.operation_cap,
                "operations_remaining": max(self.operation_cap - ops, 0), "price_version": self.tariff.price_version, "per_role": per_role,
                "note": "conservative execution bound under the frozen tariff; provider invoices are authoritative"}

    # ------------------------------------------------------------------ reservations
    def reserve(self, *, role: str, purpose: str, input_tokens: int, max_output_tokens: int, request_id: str | None = None,
                counts_operation: bool = True) -> dict:
        """Hold the worst case for one request or raise BudgetExhausted before anything is sent."""
        if purpose not in PAID_PURPOSES:
            raise BudgetError(f"purpose {purpose!r} is not a paid inference purpose")
        try:
            # no int() coercion here: the tariff refuses floats and booleans, and 1000.9 tokens truncated to 1000 would reserve below the worst case
            amount = self.tariff.reserve_micro(input_tokens, max_output_tokens)
        except PricingError as e:
            raise BudgetError(f"cannot price the request: {e}") from None
        with self.store.tx():
            t = self.totals()
            if t["upper_bound_micro"] + amount > self.cap_micro:
                raise BudgetExhausted(f"reserving {usd_text(amount)} USD for {role}/{purpose} would exceed the cap: settled {t['settled_usd']} + "
                                      f"unknown {t['unknown_liability_usd']} + held {t['held_usd']} + this > {t['cap_usd']}; no request sent")
            if counts_operation and t["operations_used"] + 1 > self.operation_cap:
                raise BudgetExhausted(f"the inference-operation cap of {self.operation_cap} is used ({t['operations_used']}); no request sent")
            row = self.store.insert_reservation(role=role, purpose=purpose, request_id=request_id, reserved_micro=amount,
                                                price_version=self.tariff.price_version, counts_operation=counts_operation)
            self.store.event("budget_reserved", reservation=row["id"], role=role, purpose=purpose, reserved_micro=amount,
                             input_tokens=int(input_tokens), max_output_tokens=int(max_output_tokens), upper_bound_after_micro=t["upper_bound_micro"] + amount)
            return row

    def bind_request(self, reservation_id: str, request_id: str) -> None:
        with self.store.tx():
            r = self._held(reservation_id)
            if r["request_id"] not in (None, request_id):
                raise BudgetError(f"reservation {reservation_id} belongs to request {r['request_id']}")
            self.store.update_reservation(reservation_id, request_id=request_id)

    def _held(self, reservation_id: str) -> dict:
        r = self.store.reservation(reservation_id)
        if r is None:
            raise BudgetError(f"no reservation {reservation_id}")
        return r

    def settle(self, reservation_id: str, usage: dict, *, request_id: str) -> int:
        """Settle once from validated usage; a second call with the same request returns the recorded amount, a
        different request or a malformed block never settles (the reservation becomes an unknown liability)."""
        with self.store.tx():
            r = self._held(reservation_id)
            if r["state"] == "settled":
                if r["request_id"] != request_id:
                    raise BudgetError(f"reservation {reservation_id} was settled for request {r['request_id']}, not {request_id}")
                return int(r["settled_micro"])
            if r["state"] != "held":
                raise BudgetError(f"reservation {reservation_id} is {r['state']}, not held")
            if r["request_id"] not in (None, request_id):
                raise BudgetError(f"reservation {reservation_id} belongs to request {r['request_id']}")
            try:
                clean = validate_usage(usage)
                amount = self.tariff.settle_micro(usage)
            except PricingError as e:
                # The unknown marking must be committed, so the error is raised only AFTER this transaction has closed:
                # raising inside ``with store.tx()`` would roll the marking back and leave the row silently held.
                self.store.update_reservation(reservation_id, state="unknown", liability_micro=int(r["reserved_micro"]), request_id=request_id,
                                              reason=f"usage unusable: {e}", usage_json=_usage_record(usage))
                self.store.event("budget_unknown", reservation=reservation_id, request=request_id, reason=str(e), liability_micro=int(r["reserved_micro"]))
                problem = str(e)
            else:
                problem = None
                if amount > int(r["reserved_micro"]):
                    # the provider charged more than the worst case we computed: keep the larger number and say so
                    self.store.event("budget_settlement_exceeds_reservation", reservation=reservation_id, reserved_micro=int(r["reserved_micro"]), settled_micro=amount)
                self.store.update_reservation(reservation_id, state="settled", settled_micro=amount, request_id=request_id, usage_json=clean,
                                              settled_utc=self.store.now(), reason="settled from response usage")
                self.store.event("budget_settled", reservation=reservation_id, request=request_id, settled_micro=amount, usage=clean)
                self.store.resolve_pre_grant(reservation_id, counted_micro=amount, counts_operation=bool(r["counts_operation"]))
        if problem is not None:
            raise BudgetError(f"usage of {request_id} unusable ({problem}); the reservation stays counted as liability")
        return amount

    def mark_unknown(self, reservation_id: str, reason: str, *, request_id: str | None = None) -> None:
        """The request may have reached the provider (timeout, crash after send, non-2xx without usage): the whole
        reservation stays counted until the owner reconciles it."""
        with self.store.tx():
            r = self._held(reservation_id)
            if r["state"] == "unknown":
                return
            if r["state"] != "held":
                raise BudgetError(f"reservation {reservation_id} is {r['state']}, cannot become unknown")
            self.store.update_reservation(reservation_id, state="unknown", liability_micro=int(r["reserved_micro"]), reason=reason,
                                          request_id=request_id or r["request_id"])
            self.store.event("budget_unknown", reservation=reservation_id, reason=reason, liability_micro=int(r["reserved_micro"]))

    def release(self, reservation_id: str, reason: str) -> None:
        """Only for a request that was provably never sent (pre-send validation, 4xx rejection with a body that says
        so is NOT enough: a 4xx is settled as zero via settle_rejected)."""
        with self.store.tx():
            r = self._held(reservation_id)
            if r["state"] != "held":
                raise BudgetError(f"reservation {reservation_id} is {r['state']}, cannot be released")
            self.store.update_reservation(reservation_id, state="released", liability_micro=0, reason=reason, settled_utc=self.store.now())
            self.store.event("budget_released", reservation=reservation_id, reason=reason)
            self.store.resolve_pre_grant(reservation_id, counted_micro=0, counts_operation=False)

    def settle_rejected(self, reservation_id: str, *, request_id: str, http_status: int, reason: str) -> None:
        """A 4xx rejection (validation, authentication, quota) is not billed: settled at zero, the operation slot stays
        consumed (the attempt happened)."""
        if not (400 <= int(http_status) < 500):
            raise BudgetError("only a 4xx rejection settles at zero")
        with self.store.tx():
            r = self._held(reservation_id)
            if r["state"] != "held":
                raise BudgetError(f"reservation {reservation_id} is {r['state']}")
            self.store.update_reservation(reservation_id, state="settled", settled_micro=0, request_id=request_id, reason=f"http {http_status}: {reason}",
                                          settled_utc=self.store.now())
            self.store.event("budget_settled", reservation=reservation_id, request=request_id, settled_micro=0, http_status=int(http_status))
            self.store.resolve_pre_grant(reservation_id, counted_micro=0, counts_operation=bool(r["counts_operation"]))

    def reconcile_unknown(self, reservation_id: str, *, authorized_by: str, usage: dict | None, no_charge: bool = False) -> int:
        """Owner-authorized reconciliation of an unknown liability from the provider's dashboard: either verified
        usage or an explicit statement that nothing was charged. An unknown that was counted as committed at the current
        change round's grant lowers the caps by the difference (Store.resolve_pre_grant): it never becomes the round's money."""
        if not authorized_by:
            raise BudgetError("reconciliation needs the owner's authorization text")
        with self.store.tx():
            r = self._held(reservation_id)
            if r["state"] != "unknown":
                raise BudgetError(f"reservation {reservation_id} is {r['state']}, not unknown")
            if no_charge:
                amount = 0
                clean = None
            else:
                clean = validate_usage(usage)
                amount = self.tariff.settle_micro(usage)
            self.store.update_reservation(reservation_id, state="settled", settled_micro=amount, liability_micro=0, usage_json=clean,
                                          reason=f"reconciled by {authorized_by}", settled_utc=self.store.now())
            self.store.event("budget_reconciled", reservation=reservation_id, authorized_by=authorized_by, settled_micro=amount)
            # an unknown counted as committed at the current round's grant: the caps drop by what it no longer counts
            self.store.resolve_pre_grant(reservation_id, counted_micro=amount, counts_operation=bool(r["counts_operation"]))
            return amount

    def grant_allowance(self, *, add_micro: int, add_operations: int, authorized_by: str, reason: str, **extra) -> dict:
        """The owner's allowance for a change round: the caps become the amounts committed at the grant (the upper bound:
        settled + unknown liability + held; the operations used) plus exactly the allowance, in the transaction that reads
        them, so the round can spend its grant and nothing more (Store.grant_allowance journals any dropped leftover).
        Nothing settled, held or unknown changes; a held or unknown amount that later resolves lower lowers the caps by the
        difference (Store.resolve_pre_grant)."""
        with self.store.tx():
            before = self.totals()
            # the held and unknown amounts inside the committed base: if one resolves lower during the round, the caps drop by
            # the difference (Store.resolve_pre_grant), so the round's remaining never exceeds its grant minus its own spend
            open_ = {}
            for r in self.store.reservations():
                if r["state"] == "held":
                    open_[r["id"]] = {"micro": int(r["reserved_micro"]), "operation": bool(r["counts_operation"])}
                elif r["state"] == "unknown":
                    open_[r["id"]] = {"micro": int(r["liability_micro"] or 0), "operation": bool(r["counts_operation"])}
            return self.store.grant_allowance(add_micro=add_micro, add_operations=add_operations, authorized_by=authorized_by, reason=reason,
                                              base_micro=before["upper_bound_micro"], base_operations=before["operations_used"],
                                              upper_bound_micro=before["upper_bound_micro"], operations_used=before["operations_used"],
                                              open_reservations=open_, **extra)
