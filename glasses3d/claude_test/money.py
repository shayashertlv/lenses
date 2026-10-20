"""Single-use owner approval and a settled-only, append-only Claude run ledger."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_FLOOR, localcontext
import json
import os
from pathlib import Path
import threading

from glasses3d.agent.approvals import ApprovalRefused, canonical_json
from glasses3d.agent.budget import (
    _is_link, _open_locked, _os_unlock, create_json_exclusive, identity_alive,
    now_iso, process_identity, read_bytes_with_retry, sha256_bytes,
)

from .pricing import TARIFFS, UnknownCharge, price_usage

APPROVAL_SCHEMA = "g3d.approval/1"
REQUEST_SCHEMA = "g3d.approval_request/1"
LEDGER_SCHEMA = "g3d.claude_settled_ledger/1"
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class LedgerRefused(RuntimeError):
    """No request may start, or ledger history cannot be safely continued."""


def _decimal(value, what: str, *, positive=False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f"{what} must be a {'positive' if positive else 'non-negative'} finite Decimal")
    return value


def _text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _arithmetic(left: Decimal, right: Decimal, *, subtract=False) -> Decimal:
    """Keep every decimal place, independent of the caller's Decimal context."""
    with localcontext() as context:
        places = min(left.as_tuple().exponent, right.as_tuple().exponent)
        context.prec = max(28, max(left.adjusted(), right.adjusted()) - places + 2)
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        return left - right if subtract else left + right


def _micro_floor(amount: Decimal) -> int:
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + 6)
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        return int((amount * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))


def _stored_amount(value, what: str, *, positive=False) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a decimal string")
    try:
        return _decimal(Decimal(value), what, positive=positive)
    except InvalidOperation:
        raise ValueError(f"{what} must be a decimal string") from None


def _nonempty(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a nonempty string")
    return value


def _settlement_metadata(refusal=False, stop_details=None, upper_bound=False):
    if type(refusal) is not bool or type(upper_bound) is not bool:
        raise ValueError("refusal and upper_bound must be booleans")
    if stop_details is not None and not isinstance(stop_details, dict):
        raise ValueError("stop_details must be an object or null")
    if refusal != upper_bound:
        raise ValueError("refusal settlement must be recorded as an upper bound")
    canonical_json(stop_details)
    return {"refusal": refusal, "stop_details": stop_details, "upper_bound": upper_bound}


def _settings(run_id, amount, model, effort):
    _nonempty(run_id, "run_id")
    if not isinstance(model, str) or model not in TARIFFS:
        raise ValueError("the approval must name a supported Claude model")
    _nonempty(effort, "effort")
    return {"provider": "anthropic", "run_id": run_id, "model": model, "effort": effort,
            "settled_only": True, "cutoff_usd": _text(amount)}


def _paths(path):
    path = Path(path)
    return path, path.with_name(f"{path.stem}-request.json"), path.with_name(f"{path.name}.consumed.json")


def _read_object(path: Path, what: str) -> tuple[bytes, dict]:
    if _is_link(path) or _is_link(path.parent):
        raise ApprovalRefused("invalid", f"{what} must be a regular file")
    try:
        raw = read_bytes_with_retry(path)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
    except FileNotFoundError:
        raise ApprovalRefused("missing", f"{what} is missing") from None
    except (OSError, ValueError, UnicodeError):
        raise ApprovalRefused("invalid", f"{what} is unreadable or invalid JSON") from None
    return raw, value


def _validate_approval(document: dict) -> Decimal:
    if not isinstance(document, dict):
        raise ValueError("approval must be an object")
    if (document.get("schema") != APPROVAL_SCHEMA or document.get("provider") != "anthropic"
            or document.get("authorized_by") != "owner" or document.get("settled_only") is not True):
        raise ValueError("approval must be an owner-approved, settled-only Anthropic record")
    _nonempty(document.get("owner_words"), "owner_words")
    _nonempty(document.get("approved"), "approved")
    amount = _stored_amount(document.get("max_usd"), "approved amount", positive=True)
    # Preserve the existing approval's micro-dollar field while the exact Decimal
    # amount remains authoritative, including amounts finer than one micro-dollar.
    micro = _micro_floor(amount)
    if type(document.get("max_micro_usd")) is not int or document["max_micro_usd"] != micro:
        raise ValueError("the approval amount fields disagree")
    settings = _settings(document.get("run_id"), amount, document.get("model"), document.get("effort"))
    if document.get("request_sha256") != sha256_bytes(canonical_json(settings)):
        raise ValueError("the approval settings changed")
    return amount


def write_approval(path, *, words: str, amount_usd: Decimal, model: str, effort: str, run_id: str):
    """Exclusively create the existing approval shape and its bound request sidecar.

    ``path`` names the approval JSON file. The owner's exact words and exact
    Decimal cutoff are retained; this records approval but sends no request.
    """
    path, request_path, consumed_path = _paths(path)
    try:
        amount = _decimal(amount_usd, "amount_usd", positive=True)
        _nonempty(words, "words")
        settings = _settings(run_id, amount, model, effort)
    except ValueError as error:
        raise ApprovalRefused("invalid", str(error)) from error
    if any(_is_link(item) for item in (path, path.parent, request_path, consumed_path)):
        raise ApprovalRefused("invalid", "approval files must not be links")
    if consumed_path.exists():
        raise ApprovalRefused("consumed", "this approval has already been consumed")
    if path.exists() or request_path.exists():
        raise ApprovalRefused("stale", "approval files already exist; use a new run")
    path.parent.mkdir(parents=True, exist_ok=True)
    request_hash = sha256_bytes(canonical_json(settings))
    request = {"schema": REQUEST_SCHEMA, "job_id": None, "run_id": run_id, "created": now_iso(),
               "provider": "anthropic", "model": model, "effort": effort,
               "requested_cap_usd": _text(amount), "request_sha256": request_hash, "request": settings}
    try:
        request_raw = create_json_exclusive(request_path, request)
        document = {
            "schema": APPROVAL_SCHEMA, "job_id": None, "run_id": run_id, "approved": now_iso(),
            "request_sha256": request_hash, "approval_request_sha256": sha256_bytes(request_raw),
            "max_usd": _text(amount),
            "max_micro_usd": _micro_floor(amount),
            "owner_words": words, "authorized_by": "owner", "recorded_by": "claude test via chat",
            "provider": "anthropic", "model": model, "effort": effort, "settled_only": True,
        }
        create_json_exclusive(path, document)
    except FileExistsError:
        raise ApprovalRefused("stale", "approval files already exist; use a new run") from None
    return document


def consume_approval(path, run_id) -> dict:
    """Consume once with an exclusive durable marker, bound to the approval bytes."""
    path, request_path, consumed_path = _paths(path)
    if consumed_path.exists() or _is_link(consumed_path):
        raise ApprovalRefused("consumed", "this approval has already been consumed")
    raw, document = _read_object(path, "approval")
    request_raw, request = _read_object(request_path, "approval request")
    try:
        amount = _validate_approval(document)
        if document["run_id"] != run_id or request.get("run_id") != run_id:
            raise ValueError("the approval belongs to a different run_id")
        settings = _settings(run_id, amount, document["model"], document["effort"])
        if (request.get("schema") != REQUEST_SCHEMA or request.get("request") != settings
                or request.get("request_sha256") != document["request_sha256"]
                or request.get("requested_cap_usd") != document["max_usd"]
                or request.get("model") != document["model"] or request.get("effort") != document["effort"]
                or request.get("provider") != "anthropic"
                or document.get("approval_request_sha256") != sha256_bytes(request_raw)):
            raise ValueError("the request or its approval binding changed")
    except (ValueError, KeyError) as error:
        raise ApprovalRefused("stale", str(error)) from error
    marker = {"schema": "g3d.approval_consumed/1", "run_id": run_id, "consumed": now_iso(),
              "provider": "anthropic", "model": document["model"], "effort": document["effort"],
              "max_usd": document["max_usd"], "approval_sha256": sha256_bytes(raw),
              "request_sha256": document["request_sha256"]}
    try:
        create_json_exclusive(consumed_path, marker)
    except FileExistsError:
        raise ApprovalRefused("consumed", "this approval has already been consumed") from None
    return {**document, "approval_sha256": marker["approval_sha256"], "consumed": marker["consumed"],
            "approval_path": str(path.absolute())}


class Ledger:
    """One run's shared ledger; all writes append, and no money is reserved.

    A thread lock and an OS file lock serialize replay, admission and appending.
    A started request with a dead owning process becomes unknown on reopening.
    A live in-flight request never reduces the amount available before cutoff.
    """

    def __init__(self, path, *, cutoff_usd: Decimal, approval: dict):
        self.path = Path(path).absolute()
        self.cutoff_usd = _decimal(cutoff_usd, "cutoff_usd", positive=True)
        try:
            amount = _validate_approval(approval)
            if amount != self.cutoff_usd:
                raise ValueError("cutoff_usd must equal the owner's approved amount")
            _nonempty(approval.get("consumed"), "consumed approval timestamp")
            _nonempty(approval.get("approval_sha256"), "consumed approval digest")
        except ValueError as error:
            raise LedgerRefused(str(error)) from error
        self.approval = json.loads(canonical_json(approval))
        self.run_id = self.approval["run_id"]
        self.model = self.approval["model"]
        self.effort = self.approval["effort"]
        self._identity = process_identity()
        self._failed = None
        key = os.path.normcase(str(self.path.resolve()))
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(key, threading.RLock())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction():
            new_binding = self._bind_consumption()
            if not self.path.exists():
                if not new_binding:
                    raise LedgerRefused("the ledger for this consumed approval is missing")
                header = {"schema": LEDGER_SCHEMA, "event": "opened", "time": now_iso(),
                          "run_id": self.run_id, "provider": "anthropic",
                          "cutoff_usd": _text(self.cutoff_usd), "approval": self.approval}
                with self.path.open("xb") as stream:
                    stream.write(canonical_json(header) + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            state = self._replay()
            for number, row in state["open"].items():
                if not identity_alive(row["owner"]):
                    self._append({"event": "unknown", "request_no": number,
                                  "reason": "request owner exited without a recorded outcome"})

    def _bind_consumption(self):
        """A consumed approval may open only one ledger path, including across processes."""
        try:
            path = Path(_nonempty(self.approval.get("approval_path"), "approval_path"))
            _, _, consumed_path = _paths(path)
            raw, original = _read_object(path, "approval")
            _, marker = _read_object(consumed_path, "consumed approval")
            expected = {**original, "approval_sha256": sha256_bytes(raw), "consumed": marker.get("consumed"),
                        "approval_path": str(path.absolute())}
            if (expected != self.approval or marker.get("schema") != "g3d.approval_consumed/1"
                    or any(marker.get(key) != self.approval.get(key)
                           for key in ("run_id", "provider", "model", "effort", "max_usd",
                                       "request_sha256", "approval_sha256"))):
                raise ValueError("the consumed approval binding changed")
            binding_path = path.with_name(path.name + ".ledger.json")
            binding = {"schema": "g3d.approval_ledger/1", "run_id": self.run_id,
                       "approval_sha256": self.approval["approval_sha256"],
                       "ledger_path": os.path.normcase(str(self.path.resolve()))}
            if _is_link(binding_path):
                raise ValueError("approval ledger binding must be a regular file")
            try:
                create_json_exclusive(binding_path, binding)
                return True
            except FileExistsError:
                _, current = _read_object(binding_path, "approval ledger binding")
                if current != binding:
                    raise ValueError("this approval already belongs to another ledger")
                return False
        except (ValueError, ApprovalRefused) as error:
            raise LedgerRefused(str(error)) from error

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._failed:
                raise LedgerRefused(self._failed)
            if _is_link(self.path) or _is_link(self.path.parent):
                raise LedgerRefused("ledger must be a regular file")
            lock_path = self.path.with_name(self.path.name + ".lock")
            if _is_link(lock_path):
                raise LedgerRefused("ledger lock must be a regular file")
            stream = _open_locked(lock_path)
            if stream is None:
                raise LedgerRefused("another process is updating this run ledger")
            try:
                yield
            finally:
                _os_unlock(stream)

    def _append(self, fields):
        row = {"schema": LEDGER_SCHEMA, "time": now_iso(), **fields}
        try:
            with self.path.open("ab") as stream:
                stream.write(canonical_json(row) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, ValueError, TypeError) as error:
            self._failed = "ledger write failed; spending is stopped"
            raise LedgerRefused(self._failed) from error

    def _replay(self):
        try:
            raw = read_bytes_with_retry(self.path)
            if not raw or not raw.endswith(b"\n"):
                raise ValueError("ledger is empty or has an incomplete final row")
            rows = [json.loads(line) for line in raw.splitlines()]
            header = rows[0]
            if (not isinstance(header, dict) or header.get("schema") != LEDGER_SCHEMA
                    or header.get("event") != "opened" or header.get("run_id") != self.run_id
                    or header.get("provider") != "anthropic" or header.get("approval") != self.approval
                    or _stored_amount(header.get("cutoff_usd"), "cutoff") != self.cutoff_usd):
                raise ValueError("ledger belongs to a different approval, run or cutoff")
            settled, unknown, stopped, next_no, opened = Decimal(0), None, None, 1, {}
            refused = False
            for row in rows[1:]:
                if (not isinstance(row, dict) or row.get("schema") != LEDGER_SCHEMA
                        or not isinstance(row.get("time"), str)):
                    raise ValueError("invalid ledger event")
                number, event = row.get("request_no"), row.get("event")
                if (event == "wait" and "assumed_unbilled_reconcile_with_console" in row
                        and row["assumed_unbilled_reconcile_with_console"] is not True):
                    raise ValueError("retry wait must retain its unbilled assumption")
                if event == "wait" and number is None:
                    if type(row.get("status")) is not int or row["status"] not in (429, 529):
                        raise ValueError("invalid free-endpoint wait status")
                    _stored_amount(row.get("seconds"), "wait seconds")
                    continue
                if type(number) is not int or number < 1:
                    raise ValueError("invalid request number")
                if event == "started":
                    if (number != next_no or row.get("model") != self.model
                            or not isinstance(row.get("owner"), dict) or unknown or stopped or refused
                            or settled >= self.cutoff_usd):
                        raise ValueError("invalid request admission in ledger")
                    opened[number] = row
                    next_no += 1
                elif event in {"settled", "unknown", "not_sent", "rejected", "wait"}:
                    if number not in opened:
                        raise ValueError("outcome or wait has no open request")
                    if event == "settled":
                        cost = _stored_amount(row.get("cost_usd"), "settled cost")
                        if row.get("model") != opened[number]["model"]:
                            raise ValueError("settled model differs from requested model")
                        if price_usage(row["model"], row.get("usage")) != cost:
                            raise ValueError("settled cost differs from its usage")
                        request_id = row.get("request_id")
                        if request_id is not None:
                            _nonempty(request_id, "request_id")
                        metadata = _settlement_metadata(
                            row.get("refusal", False), row.get("stop_details"), row.get("upper_bound", False),
                        )
                        refused = refused or metadata["refusal"]
                        settled = _arithmetic(settled, cost)
                    elif event == "unknown":
                        unknown = _nonempty(row.get("reason"), "unknown reason")
                    elif event == "not_sent":
                        _nonempty(row.get("reason"), "not-sent reason")
                    elif event == "rejected":
                        if type(row.get("status")) is not int or not 400 <= row["status"] <= 599:
                            raise ValueError("invalid rejected HTTP status")
                        stopped = _nonempty(row.get("reason"), "rejection reason")
                    else:
                        if row.get("status") not in (429, 529):
                            raise ValueError("invalid wait status")
                        _stored_amount(row.get("seconds"), "wait seconds")
                    if event != "wait":
                        del opened[number]
                else:
                    raise ValueError("unknown ledger event")
            return {"settled": settled, "unknown": unknown, "stopped": stopped, "refused": refused,
                    "next": next_no, "open": opened}
        except (OSError, ValueError, KeyError, TypeError, UnknownCharge) as error:
            self._failed = f"ledger history is invalid: {error}"
            raise LedgerRefused(self._failed) from error

    def _permission(self, state):
        if state["refused"]:
            return False, "refusal"
        if state["unknown"] is not None:
            return False, "unknown charge: " + state["unknown"]
        if state["stopped"] is not None:
            return False, state["stopped"]
        if state["settled"] >= self.cutoff_usd:
            return False, "settled spend reached the owner's cutoff"
        return True, "settled spend is below the owner's cutoff"

    def settled_usd(self) -> Decimal:
        with self._transaction():
            return self._replay()["settled"]

    def may_start_request(self) -> tuple[bool, str]:
        try:
            with self._transaction():
                return self._permission(self._replay())
        except LedgerRefused as error:
            return False, str(error)

    def start_request(self, model: str) -> int:
        """Atomically admit and record a request, without reserving any money."""
        with self._transaction():
            state = self._replay()
            allowed, reason = self._permission(state)
            if not allowed:
                raise LedgerRefused(reason)
            if model != self.model:
                raise LedgerRefused("request model differs from the owner's approval")
            number = state["next"]
            self._append({"event": "started", "request_no": number, "model": model, "owner": self._identity})
            return number

    def _require_open(self, state, request_no):
        if type(request_no) is not int or request_no not in state["open"]:
            raise LedgerRefused("request has no open ledger entry")

    def record_settled(self, request_no, request_id, model, usage, cost, *,
                       refusal=False, stop_details=None, upper_bound=False):
        """Append normal-rate usage, marking refusal costs as an upper bound."""
        with self._transaction():
            state = self._replay()
            self._require_open(state, request_no)
            try:
                _decimal(cost, "cost")
                if model != state["open"][request_no]["model"]:
                    raise ValueError("served model differs from the requested model")
                if request_id is not None:
                    _nonempty(request_id, "request_id")
                if price_usage(model, usage) != cost:
                    raise ValueError("cost differs from the final usage")
                canonical_json(usage)
                metadata = _settlement_metadata(refusal, stop_details, upper_bound)
            except (ValueError, TypeError, UnknownCharge) as error:
                self._append({"event": "unknown", "request_no": request_no, "reason": str(error)})
                raise LedgerRefused(f"cannot settle request: {error}") from error
            fields = {"event": "settled", "request_no": request_no, "request_id": request_id,
                      "model": model, "usage": usage, "cost_usd": _text(cost)}
            if refusal or stop_details is not None or upper_bound:
                fields.update(metadata)
            self._append(fields)

    def record_unknown(self, request_no, reason):
        _nonempty(reason, "reason")
        with self._transaction():
            state = self._replay()
            self._require_open(state, request_no)
            self._append({"event": "unknown", "request_no": request_no, "reason": reason})

    def record_not_sent(self, request_no, reason):
        """Close a definite pre-work rejection without inventing provider usage."""
        _nonempty(reason, "reason")
        with self._transaction():
            state = self._replay()
            self._require_open(state, request_no)
            self._append({"event": "not_sent", "request_no": request_no, "reason": reason})

    def record_wait(self, request_no, status, seconds):
        if type(status) is not int or status not in (429, 529):
            raise ValueError("only pre-work 429/529 waits are allowed")
        if isinstance(seconds, bool):
            raise ValueError("wait seconds must be non-negative and finite")
        try:
            delay = _decimal(Decimal(str(seconds)), "wait seconds")
        except InvalidOperation:
            raise ValueError("wait seconds must be non-negative and finite") from None
        with self._transaction():
            state = self._replay()
            if request_no is not None:
                self._require_open(state, request_no)
            self._append({"event": "wait", "request_no": request_no, "status": status, "seconds": _text(delay),
                          "assumed_unbilled_reconcile_with_console": True})

    def record_rejected(self, request_no, status, reason=None):
        """Stop after a definite HTTP rejection, retaining its known zero charge."""
        if type(status) is not int or not 400 <= status <= 599:
            raise ValueError("a rejected HTTP status must be between 400 and 599")
        reason = reason if reason is not None else f"HTTP {status} stopped the run"
        _nonempty(reason, "reason")
        with self._transaction():
            state = self._replay()
            self._require_open(state, request_no)
            self._append({"event": "rejected", "request_no": request_no, "status": status, "reason": reason})

    def money_status(self) -> dict:
        settled = self.settled_usd()
        return {"settled_usd": _text(settled), "cutoff_usd": _text(self.cutoff_usd),
                "left_usd": _text(max(Decimal(0), _arithmetic(self.cutoff_usd, settled, subtract=True)))}
