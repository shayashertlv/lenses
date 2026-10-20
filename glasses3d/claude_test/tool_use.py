"""Factual Claude-only tool-use receipts and counters; no tool admission policy."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


COMPARISONS = frozenset({"compare_to_photos", "compare_look_to_photos"})
MEASUREMENTS = frozenset({"photo_read", "pick_surface_point"})
DEDICATED_TOOLS = (
    "Dedicated tools for the next step: compare_to_photos (shape), "
    "compare_look_to_photos (colour/material), photo_read or pick_surface_point (measure)."
)


@dataclass
class ToolUse:
    """One scene's counters survive conversation renewals, but not a new run."""

    changes: int = 0
    last_comparison: str | None = None

    def observe(self, name: str, *, changed: bool, successful: bool, returned: bool) -> dict:
        reset = name in COMPARISONS and successful
        if reset:
            self.changes = 0
            self.last_comparison = name
        note = None
        if name == "execute_blender_code" and changed:
            self.changes += 1
            if returned:
                since = (f"your last {self.last_comparison}" if self.last_comparison
                         else "the run started (no comparison yet)")
                noun = "change" if self.changes == 1 else "changes"
                note = f"Host: {self.changes} model {noun} since {since}. {DEDICATED_TOOLS}"
        return {"comparison_reset": reset, "changes_since_comparison": self.changes,
                "host_note": note}


def summarize(directory: Path) -> dict:
    """Reconcile parent and specialist receipts without merging separate scenes' streaks."""
    counts = Counter()
    longest = measurements = notes = prepared = measurement_calls = unknown_measurements = 0
    for path in sorted(Path(directory).rglob("tool-use.jsonl")):
        sent_path = path.with_name("tool-use-sent.jsonl")
        sent = ({json.loads(line)["call_id"] for line in sent_path.read_text(
            encoding="utf-8").splitlines()} if sent_path.exists() else set())
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            name = row["name"]
            counts[name] += 1
            longest = max(longest, row["changes_since_comparison"])
            if name in MEASUREMENTS:
                measurement_calls += 1
                measurements += row["photo_measurements"] or 0
                unknown_measurements += row["photo_measurements"] is None
            prepared += row["host_note"] is not None
            notes += row["host_note"] is not None and row["call_id"] in sent
    total = sum(counts.values())
    dedicated = total - counts["execute_blender_code"]
    return {
        "per_tool_counts": dict(sorted(counts.items())),
        "total_calls": total,
        "dedicated_tool_calls": dedicated,
        "dedicated_tool_share": dedicated / total if total else 0.0,
        "longest_no_comparison_streak": longest,
        "photo_measurements": measurements,
        "photo_measurement_calls": measurement_calls,
        "unresolved_measurement_calls": unknown_measurements,
        "host_notes_sent": notes,
        "host_notes_prepared": prepared,
        "method": (
            "All tool invocations, including refusals, count toward shares. "
            "Streaks count fingerprint-changing execute_blender_code calls; only a successful "
            "compare_to_photos or compare_look_to_photos resets them. Other calls and conversation "
            "renewals do not reset. Photo measurements count successful photo_read readings and "
            "pick_surface_point hits, including partial results. Host notes sent counts unique notes "
            "included in a settled model request; prepared but unsent notes are separate. "
            "Each specialist has its own scene streak."
        ),
    }


def measurement_count(name: str, value: dict | None, root: Path) -> int | None:
    """Count actual readings/hits, using the hash-verified full report if paged."""
    if name not in MEASUREMENTS:
        return 0
    if not isinstance(value, dict):
        return None
    try:
        if value.get("paged"):
            from glasses3d.agent.tool_reports import _path, REPORT_SCHEMA
            digest = value["full_report"]["id"]
            if not isinstance(digest, str) or len(digest) != 64 or any(
                    ch not in "0123456789abcdef" for ch in digest):
                return None
            data = _path(root, digest).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                return None
            document = json.loads(data)
            if document.get("schema") != REPORT_SCHEMA or document.get("tool") != name:
                return None
            value = document["result"]
        if value.get("status") == "refused":
            return 0
        key = "readings" if name == "photo_read" else "hits"
        rows = value.get(key)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            return None
        return sum(row.get("status") == "ok" if name == "photo_read" else row.get("hit") is True
                   for row in rows)
    except (OSError, KeyError, TypeError, ValueError):
        # Observability must not turn an otherwise valid tool result into a refusal.
        return None
