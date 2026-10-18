"""Owner-facing money: settlement and outstanding holds are separate; ledger arithmetic stays internal."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN


def dollars(value, *, places=2):
    """Display money without rounding a below-limit amount up to the limit."""
    if value is None:
        return "n/a"
    amount = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
    return f"${amount:.{places}f}"


def _money_summary(value):
    """Recognize numeric budget summaries, not arbitrary Blender result/property dictionaries."""
    fields = {name + suffix: value[name + suffix] for name in ("settled", "held", "unknown")
              for suffix in ("_micro_usd", "_usd") if name + suffix in value}
    if not any(key.startswith("settled_") for key in fields) or not any(
            key.startswith(("held_", "unknown_")) for key in fields):
        return False
    for amount in fields.values():
        if isinstance(amount, bool) or not isinstance(amount, (int, float, str, Decimal)):
            return False
        try:
            if not Decimal(str(amount)).is_finite():
                return False
        except InvalidOperation:
            return False
    return True


def money_text(values, *, places=6):
    def amount(name):
        value = values.get(name + "_usd")
        if value is None and values.get(name + "_micro_usd") is not None:
            value = Decimal(values[name + "_micro_usd"]) / 1_000_000
        return dollars(value, places=places)
    text = (f"spent {amount('settled')} (settled); "
            f"held for the request in flight {amount('held')} (released after it settles)")
    unknown = values.get("unknown_micro_usd", values.get("unknown_usd"))
    if unknown is not None and Decimal(str(unknown)):
        text += f"; unresolved request hold {amount('unknown')} (kept until reconciled)"
    return text


def owner_money(value):
    """Copy reports for presentation. Original ledger and journal dictionaries are not modified."""
    if isinstance(value, list):
        return [owner_money(item) for item in value]
    if not isinstance(value, dict):
        return value
    summary = _money_summary(value)
    result = {key: owner_money(item) for key, item in value.items()
              if not summary or key not in ("committed_usd", "committed_micro_usd", "reserved_total_micro_usd")}
    if summary:
        result["money_report"] = money_text(result)
    return result


def reservation_refusal(reservation, maximum, totals):
    from .pricing import usd_text
    return (f"Next request needs a hold of ${usd_text(reservation)}; {money_text(totals)}; "
            f"maximum reserve ${usd_text(maximum)}. The next request was not sent.")
