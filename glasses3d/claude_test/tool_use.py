"""Factual Claude-only tool-use receipts and counters; no tool admission policy."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


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


def paging_facts(name: str, result: dict | None) -> dict:
    """Describe the emitted report envelope; read_report pages never get a reminder."""
    facts = {"paged_report": None, "paged_note": None, "report_read": None}
    if result is None:
        return facts
    value = None
    for block in result.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(block["text"].lstrip())
            break
        except ValueError:
            pass
    if not isinstance(value, dict):
        return facts
    if name == "read_report":
        digest = value.get("report")
        if (value.get("status") == "ok" and not result.get("is_error", False)
                and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
            facts["report_read"] = digest
        return facts
    reference = value.get("full_report")
    digest = reference.get("id") if isinstance(reference, dict) else None
    if (value.get("paged") is True and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest)):
        facts["paged_report"] = digest
        facts["paged_note"] = (
            f"Host: this result is paged; the full report is available with read_report (id {digest})."
        )
    return facts


def summarize(directory: Path) -> dict:
    """Reconcile parent and specialist receipts without merging separate scenes' streaks."""
    counts = Counter()
    longest = measurements = notes = prepared = measurement_calls = unknown_measurements = 0
    paged_results = paged_read = paged_notes = 0
    for path in sorted(Path(directory).rglob("tool-use.jsonl")):
        # Report IDs are job-local. Count each emission once, only after a later
        # successful page in this scene; repeated pages cannot inflate the count.
        unread = Counter()
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
            note_count = int(row["host_note"] is not None) + int(row.get("paged_note") is not None)
            prepared += note_count
            if row["call_id"] in sent:
                notes += note_count
                paged_notes += row.get("paged_note") is not None
            if row.get("paged_report") is not None:
                paged_results += 1
                unread[row["paged_report"]] += 1
            if row.get("report_read") is not None:
                paged_read += unread.pop(row["report_read"], 0)
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
        "paged_results": paged_results,
        "paged_results_later_read": paged_read,
        "paged_notes_sent": paged_notes,
        "method": (
            "All tool invocations, including refusals, count toward shares. "
            "Streaks count fingerprint-changing execute_blender_code calls; only a successful "
            "compare_to_photos or compare_look_to_photos resets them. Other calls and conversation "
            "renewals do not reset. Photo measurements count successful photo_read readings and "
            "pick_surface_point hits, including partial results. Host notes sent counts unique notes "
            "included in a settled model request; prepared but unsent notes are separate. "
            "Change-count and paged-result notes count separately. Paged results later read counts "
            "each result followed by at least one successful read_report page for its ID in the same "
            "scene, not proof that every page was read. read_report pages get no paging reminder. "
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
