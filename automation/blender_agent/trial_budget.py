"""One frozen dollar cap across a live Blender trial's serial invocations.

Hold this context for the entire SDK run. The OS lock is automatically released
if the process dies; its file intentionally remains. Historical ledgers, not
result summaries, are the accounting authority. No model calls are made here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import json
import os
from pathlib import Path
import tempfile

from .budget import BudgetGuard, BudgetGuardError
from modeler.agentic.pricing import MICRO, PricingError, Tariff, usd_text


class TrialBudgetError(BudgetGuardError):
    """The shared trial allowance cannot be established safely."""


def _micro(value) -> int:
    try:
        amount = Decimal(str(value))
        if isinstance(value, bool) or not amount.is_finite() or amount < 0:
            raise ValueError
        return int((amount * MICRO).to_integral_value(rounding=ROUND_FLOOR))
    except (InvalidOperation, ValueError, OverflowError):
        raise TrialBudgetError("Trial maximum must be a finite non-negative dollar amount") from None


def _integer(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Expected a non-negative integer")
    return value


class TrialBudget:
    """Lock a trial, validate its history and allocate only the remaining cap.

    ``current_invocation`` explicitly excludes one newly created run directory
    from the missing-ledger check. It must not already contain a budget ledger.
    Only one invocation guard may be created in each context.
    """

    def __init__(self, output: str | Path, maximum_usd, resume: bool, *,
                 current_invocation: str | Path | None = None):
        self.output = Path(output).resolve()
        self.maximum_micro = _micro(maximum_usd)
        self.resume = resume
        self.current_invocation = Path(current_invocation).resolve() if current_invocation else None
        self.tariff = Tariff.frozen()
        self._lock_file = None
        self._guard = None
        self._prior = None
        if self.current_invocation is not None and self.current_invocation.parent != self.output / "runs":
            raise TrialBudgetError("Current invocation must be a direct child of this trial's runs directory")

    def __enter__(self):
        if self._lock_file is not None:
            raise TrialBudgetError("Trial budget context is already entered")
        self.output.mkdir(parents=True, exist_ok=True)
        stream = (self.output / ".trial-budget.lock").open("a+b")
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise TrialBudgetError("Another process holds this trial's budget lock") from None
        self._lock_file = stream
        try:
            if self.current_invocation is not None and (self.current_invocation / "budget.json").exists():
                raise TrialBudgetError("Current invocation exclusion cannot hide an existing ledger")
            manifest = self.output / "trial-budget.json"
            if self.resume:
                if not manifest.is_file():
                    raise TrialBudgetError(
                        "Legacy output has no trial-budget.json. Reconcile its prior spending explicitly "
                        "before migration, or use a new output directory for a separately authorized trial."
                    )
                frozen = self._read(manifest)
                if (frozen.get("schema") != "blender_agent_trial_budget_v1"
                        or type(frozen.get("maximum_micro_usd")) is not int
                        or frozen.get("maximum_micro_usd") != self.maximum_micro
                        or frozen.get("tariff") != self.tariff.to_dict()):
                    raise TrialBudgetError("The requested cap or tariff differs from the frozen trial budget")
            else:
                if manifest.exists():
                    raise TrialBudgetError("This trial already has a frozen budget; use --resume")
                if (self.output / "session.sqlite").exists() or self._prior_directories():
                    raise TrialBudgetError("Existing trial history needs explicit legacy-budget reconciliation")
                self._freeze(manifest)
            self._prior = self._scan_prior()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, traceback):
        stream, self._lock_file = self._lock_file, None
        if stream is not None:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def _freeze(self, path: Path) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.output,
                                             prefix=".trial-budget-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"schema": "blender_agent_trial_budget_v1",
                           "maximum_micro_usd": self.maximum_micro,
                           "tariff": self.tariff.to_dict(),
                           "created_at": datetime.now(timezone.utc).isoformat()}, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            if path.is_symlink():
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, ValueError, UnicodeError):
            raise TrialBudgetError(f"Missing or malformed budget evidence: {path}") from None

    def _prior_directories(self) -> list[Path]:
        runs = self.output / "runs"
        if not runs.exists():
            return []
        directories = []
        for path in sorted(runs.iterdir()):
            if path.is_dir():
                if path.is_symlink() or path.resolve().parent != runs:
                    raise TrialBudgetError("Run directories must stay inside this trial")
                if path != self.current_invocation:
                    directories.append(path)
        return directories

    def _scan_prior(self) -> dict:
        totals = {"settled": 0, "unknown": 0, "reserved": 0, "invocations": 0}
        for directory in self._prior_directories():
            ledger = self._read(directory / "budget.json")
            try:
                if (ledger.get("schema") not in {"blender_agent_budget_v1", "blender_agent_budget_v2"}
                        or ledger.get("tariff") != self.tariff.to_dict() or ledger.get("violation") is not None):
                    raise ValueError
                cap = _integer(ledger["maximum_micro_usd"])
                if cap > self.maximum_micro or not isinstance(ledger["reservations"], list):
                    raise ValueError
                settled = unknown = reserved = 0
                for sequence, row in enumerate(ledger["reservations"], 1):
                    if not isinstance(row, dict) or type(row.get("sequence")) is not int or row["sequence"] != sequence:
                        raise ValueError
                    reservation = self.tariff.reserve_micro(row["input_tokens"], row["max_output_tokens"])
                    if _integer(row["reserved_micro_usd"]) != reservation:
                        raise ValueError
                    reserved += reservation
                    if "settled_micro_usd" in row:
                        # BudgetGuard stores normalized flat usage; reconstruct
                        # the tariff's provider-shaped details without losing cache rates.
                        usage = row["usage"]
                        amount = self.tariff.settle_micro({
                            "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                            "input_tokens_details": {"cached_tokens": usage["cached_tokens"],
                                                     "cache_write_tokens": usage["cache_write_tokens"]},
                            "output_tokens_details": {"reasoning_tokens": usage["reasoning_tokens"]},
                        })
                        if (_integer(row["settled_micro_usd"]) != amount or amount > reservation
                                or (row["input_tokens"] > 0 and usage["input_tokens"] == 0)):
                            raise ValueError
                        settled += amount
                    else:
                        unknown += reservation
                if _integer(ledger["reserved_micro_usd"]) != reserved or settled + unknown > cap:
                    raise ValueError
            except (KeyError, TypeError, ValueError, PricingError):
                raise TrialBudgetError(f"Invalid or violated prior budget ledger: {directory / 'budget.json'}") from None
            totals["settled"] += settled
            totals["unknown"] += unknown
            totals["reserved"] += reserved
            totals["invocations"] += 1
        if totals["settled"] + totals["unknown"] > self.maximum_micro:
            raise TrialBudgetError("Prior committed costs already exceed this trial's frozen cap")
        return totals

    def _require_lock(self):
        if self._lock_file is None or self._prior is None:
            raise TrialBudgetError("Enter and hold the trial budget context for the whole invocation")

    def remaining_usd(self) -> str:
        return self.summary()["remaining_usd"]

    def summary(self, current_guard: BudgetGuard | None = None) -> dict:
        self._require_lock()
        guard = current_guard if current_guard is not None else self._guard
        if guard is not None and guard is not self._guard:
            raise TrialBudgetError("The supplied guard does not belong to this trial context")
        current = guard.summary() if guard is not None else None
        settled = self._prior["settled"] + (_micro(current["settled_usd"]) if current else 0)
        unknown = self._prior["unknown"] + (_micro(current["unknown_reserved_usd"]) if current else 0)
        return {"maximum_usd": usd_text(self.maximum_micro),
                "prior_committed_usd": usd_text(self._prior["settled"] + self._prior["unknown"]),
                "prior_invocations": self._prior["invocations"],
                "settled_usd": usd_text(settled), "unknown_reserved_usd": usd_text(unknown),
                "committed_usd": usd_text(settled + unknown),
                "remaining_usd": usd_text(max(0, self.maximum_micro - settled - unknown)),
                "current_invocation": current}

    def reserve_invocation(self, counter, ledger_path: str | Path) -> BudgetGuard:
        self._require_lock()
        if self._guard is not None:
            raise TrialBudgetError("Only one invocation may be allocated per trial context")
        path = Path(ledger_path).resolve()
        if path.name != "budget.json" or path.parent.parent != self.output / "runs":
            raise TrialBudgetError("Invocation ledger must be runs/<invocation>/budget.json inside this trial")
        if self.current_invocation is not None and path.parent != self.current_invocation:
            raise TrialBudgetError("Invocation ledger does not match the explicit current invocation")
        if path.exists():
            raise TrialBudgetError("A new invocation cannot reuse an existing budget ledger")
        self.current_invocation = path.parent
        self._prior = self._scan_prior()
        self._guard = BudgetGuard(counter, path, self.remaining_usd())
        return self._guard
