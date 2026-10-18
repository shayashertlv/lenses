"""The terminal vocabulary shared by the Astra loop and its delivery consumers."""

STATUSES = ("completed", "turn_limit", "budget_limit", "stopped", "provider_rejected", "provider_unknown",
            "output_limit", "failed_worker", "failed", "ended_without_finish", "payload_limit")
