"""Owner verdicts bound to immutable deliveries, plus lossless legacy migration.

The JSONL ledger only grows. A chain and atomically replaced head receipt detect edits, missing lines and truncated
tails. Writes hold the existing OS ledger lock and fsync the append before publishing its head. An interrupted
write fails closed on the next read; it is never silently repaired. No verdict is inferred from a tool result.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from .budget import _open_locked, _os_unlock
from .procs import open_append, write_json_atomic

SCHEMA = "g3d.verdict/1"
HEAD_SCHEMA = "g3d.verdict_head/1"
LEGACY_SCHEMA = "g3d.verdict_legacy/1"
MIGRATION_SCHEMA = "g3d.verdict_migration/1"
SHA = re.compile(r"^[0-9a-f]{64}$")
LOCK_TIMEOUT_S = 5.0


class VerdictError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise VerdictError(f"duplicate JSON field {key!r}")
        out[key] = value
    return out


def _decode(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda value: (_ for _ in ()).throw(
            VerdictError(f"invalid JSON constant {value}")))
    except (ValueError, UnicodeError) as error:
        raise VerdictError(f"malformed verdict data: {error}") from error


def _plain(path):
    """Refuse linked ledger files or directories before following them."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise VerdictError(f"verdict files must not be links: {part}")
    return path


def ledger_file(root, ledger_path=None):
    return _plain(ledger_path if ledger_path is not None else Path(root) / "reviews/verdicts.jsonl")


def _head_path(path):
    return path.with_name(path.name + ".head.json")


@contextmanager
def _locked(path):
    guard_path = _plain(path.with_name(path.name + ".lock"))
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while (guard := _open_locked(guard_path)) is None:
        if time.monotonic() >= deadline:
            raise VerdictError("verdict ledger is busy")
        time.sleep(.01)
    try:
        yield
    finally:
        _os_unlock(guard)


def _digest_ok(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _validate(row, sequence, previous):
    if not isinstance(row, dict) or row.get("schema") != SCHEMA or row.get("scope") != "product":
        raise VerdictError("unsupported verdict record")
    if type(row.get("sequence")) is not int or row["sequence"] != sequence or row.get("previous_sha256") != previous:
        raise VerdictError("verdict chain order changed")
    if (not isinstance(row.get("id"), str) or not re.fullmatch(r"(?:verdict|legacy)-[0-9a-f]{32,64}", row["id"])
            or not _digest_ok(row.get("glb_sha256")) or row.get("verdict") not in ("accept", "changes")
            or not isinstance(row.get("notes"), str) or (row["verdict"] == "changes" and not row["notes"].strip())):
        raise VerdictError("invalid verdict identity, value or notes")
    if not isinstance(row.get("recorded_at"), str):
        raise VerdictError("verdict has no recording time")
    try:
        if datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError as error:
        raise VerdictError("invalid verdict recording time") from error
    if row.get("origin") == "live":
        if (not isinstance(row.get("delivery_id"), str) or not row["delivery_id"]
                or not _digest_ok(row.get("manifest_sha256")) or not isinstance(row.get("job_id"), str)
                or not row["job_id"]):
            raise VerdictError("live verdict must bind a job and immutable delivery")
    elif row.get("origin") == "legacy":
        legacy = row.get("legacy")
        if (row["verdict"] != "accept" or row.get("delivery_id") is not None or row.get("manifest_sha256") is not None
                or not isinstance(legacy, dict) or not _digest_ok(legacy.get("source_sha256"))
                or not _digest_ok(legacy.get("line_sha256")) or type(legacy.get("line")) is not int or legacy["line"] < 1):
            raise VerdictError("invalid historical product verdict")
    else:
        raise VerdictError("unknown verdict origin")
    digest = _sha(_json({key: value for key, value in row.items() if key != "record_sha256"}))
    if row.get("record_sha256") != digest:
        raise VerdictError("verdict record hash changed")


def _read(path):
    path, head_path = _plain(path), _plain(_head_path(path))
    if not path.exists():
        if head_path.exists():
            raise VerdictError("verdict ledger is missing but its head receipt exists")
        return []
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise VerdictError("empty or incomplete verdict ledger")
    rows, ids, previous = [], set(), None
    for sequence, line in enumerate(raw[:-1].split(b"\n"), 1):
        row = _decode(line)
        _validate(row, sequence, previous)
        if row["id"] in ids:
            raise VerdictError("duplicate verdict id")
        ids.add(row["id"])
        rows.append(row)
        previous = row["record_sha256"]
    expected = {"schema": HEAD_SCHEMA, "records": len(rows), "sha256": _sha(raw), "last_record_sha256": previous}
    if not head_path.is_file() or _decode(head_path.read_bytes()) != expected:
        raise VerdictError("verdict head receipt differs; a write was interrupted or the ledger changed")
    return rows


def _append(path, rows, additions):
    if not additions:
        return []
    previous = rows[-1]["record_sha256"] if rows else None
    new = []
    for content in additions:
        row = {**content, "sequence": len(rows) + len(new) + 1, "previous_sha256": previous}
        row["record_sha256"] = _sha(_json(row))
        _validate(row, row["sequence"], previous)
        new.append(row)
        previous = row["record_sha256"]
    payload = b"".join(_json(row) + b"\n" for row in new)
    with open_append(path) as stream:
        if stream.write(payload) != len(payload):
            raise OSError("incomplete verdict append; ledger requires inspection")
        stream.flush()
        os.fsync(stream.fileno())
    raw = path.read_bytes()
    write_json_atomic(_head_path(path), {"schema": HEAD_SCHEMA, "records": len(rows) + len(new),
                                         "sha256": _sha(raw), "last_record_sha256": previous})
    return new


def _delivery(root, delivery_id=None):
    from .delivery import load_delivery
    return load_delivery(root, delivery_id)


def _validate_deliveries(root, rows):
    checked = {}
    for row in rows:
        if row["origin"] != "live":
            continue
        if row["job_id"] != Path(root).resolve().name:
            raise VerdictError("verdict belongs to another job")
        if row["delivery_id"] not in checked:
            checked[row["delivery_id"]] = _delivery(root, row["delivery_id"])
        delivery = checked[row["delivery_id"]]
        if delivery["glb_sha256"] != row["glb_sha256"] or delivery["manifest_sha256"] != row["manifest_sha256"]:
            raise VerdictError("verdict delivery binding changed")
        if row.get("checkpoint") is not None and row["checkpoint"] != _checkpoint(delivery):
            raise VerdictError("verdict checkpoint binding changed")


def _checkpoint(delivery):
    report = delivery.get("report_data") or {}
    source = report.get("source_checkpoint") or {}
    ident = report.get("checkpoint")
    fingerprint = report.get("fingerprint") or source.get("fingerprint")
    if not ident or not _digest_ok(fingerprint):
        return None  # old imported delivery metadata can lack a recoverable scene
    return {"id": ident, "fingerprint": fingerprint}


def read_verdicts(root, *, ledger_path=None):
    """Validate the complete chain and every live record's immutable asset binding."""
    path = ledger_file(root, ledger_path)
    with _locked(path):
        rows = _read(path)
        _validate_deliveries(root, rows)
        return rows


def append_verdict(root, verdict, notes, delivery_id=None, expected_sha256=None, ledger_path=None):
    """Record exact owner words for an explicitly selected or SHA-confirmed immutable delivery.

    An explicit delivery id continues to select that version even if a newer delivery is current. With only
    expected_sha256, a changed current asset refuses the verdict. A bare unbound accept/changes is never allowed.
    """
    if not isinstance(verdict, str) or verdict not in {"accept", "changes"} or not isinstance(notes, str) or (verdict == "changes" and not notes.strip()):
        raise VerdictError("verdict must be accept or changes; changes requires nonempty owner notes")
    if delivery_id is None and expected_sha256 is None:
        raise VerdictError("a verdict requires an explicit delivery id or expected GLB sha256")
    if expected_sha256 is not None and not _digest_ok(expected_sha256):
        raise VerdictError("expected_sha256 must be a lowercase sha256")
    path = ledger_file(root, ledger_path)
    with _locked(path):
        rows = _read(path)
        _validate_deliveries(root, rows)
        delivery = _delivery(root, delivery_id)
        if expected_sha256 is not None and delivery["glb_sha256"] != expected_sha256:
            raise VerdictError("reviewed GLB sha256 differs from the selected delivery")
        return _append(path, rows, [{"schema": SCHEMA, "id": "verdict-" + uuid4().hex, "scope": "product",
                                    "origin": "live", "recorded_at": datetime.now(timezone.utc).isoformat(),
                                    "job_id": Path(root).resolve().name, "delivery_id": delivery["id"],
                                    "glb_sha256": delivery["glb_sha256"], "manifest_sha256": delivery["manifest_sha256"],
                                    "checkpoint": _checkpoint(delivery),
                                    "verdict": verdict, "notes": notes}])[0]


def pending_notes(root):
    """All changes-note candidates, in order. Actual session history decides which are still unsent.

    Do not mark these sent from a run receipt: a prepared or failed run need not have delivered its input. The
    unique verdict id keeps repeated owner requests distinct; the immutable GLB identity explains their scope.
    """
    return [_note(row) for row in read_verdicts(root) if row["verdict"] == "changes"]


def _note(row):
    checkpoint = row.get("checkpoint")
    state = (f"; checkpoint {checkpoint['id']}; fingerprint {checkpoint['fingerprint']}" if checkpoint else "")
    return (f"Owner changes (verdict {row['id']}; delivery {row['delivery_id']}; GLB sha256 "
            f"{row['glb_sha256']}{state}):\n" + row["notes"])


def resume_checkpoint(root, items):
    """The newest not-yet-sent changes verdict selects the exact state the owner reviewed.

    Previously sent reviews remain append-only context, and do not reset later work on every resume.
    Older verdicts derive their checkpoint from their verified immutable delivery.
    """
    from .session_inputs import NOTE_PREFIX
    from .journal import CheckpointIndex
    texts = []
    for item in items:
        if (not isinstance(item, dict) or item.get("role") != "user"
                or item.get("type", "message") != "message"):
            continue
        content = item.get("content")
        texts.extend([content] if isinstance(content, str) else
                     [part.get("text", "") for part in content or []
                      if isinstance(part, dict) and part.get("type") == "input_text"])
    pending = [row for row in read_verdicts(root) if row["verdict"] == "changes"
               and NOTE_PREFIX + _note(row) not in texts]
    if not pending:
        return None
    row = pending[-1]
    target = _checkpoint(_delivery(root, row["delivery_id"]))
    if target is None:
        raise VerdictError("reviewed delivery has no recoverable checkpoint identity")
    saved = CheckpointIndex(Path(root) / "checkpoints").get(target["id"])
    if saved is None or saved.get("fingerprint") != target["fingerprint"]:
        raise VerdictError("reviewed delivery checkpoint is missing or changed")
    path = Path(root) / "checkpoints" / saved["file"]
    if not path.resolve().is_relative_to((Path(root) / "checkpoints").resolve()) or _sha(path.read_bytes()) != saved["sha256"]:
        raise VerdictError("reviewed delivery checkpoint bytes changed")
    return {**target, "sha256": saved["sha256"], "delivery_id": row["delivery_id"], "verdict_id": row["id"]}


def migrate_legacy(source, ledger_path):
    """Normalize product accepts; preserve every legacy line separately, including scoped print/method decisions.

    This never reads or modifies the historical GLBs and never edits the source ledger. A rerun against the same
    source is idempotent. Changed source or migration artifacts refuse instead of overwriting historical evidence.
    """
    source, target = _plain(source), _plain(ledger_path)
    if source == target:
        raise VerdictError("migration needs a separate destination ledger")
    raw = source.read_bytes()
    source_sha = _sha(raw)
    if not raw:
        raise VerdictError("legacy ledger is empty")
    archived, accepts = [], []
    for n, line in enumerate(raw.splitlines(keepends=True), 1):
        row = _decode(line)
        if (not isinstance(row, dict) or not _digest_ok(row.get("glb_sha256"))
                or any(not isinstance(row.get(key), str) or not row[key] for key in ("verdict", "owner_words", "product", "date"))):
            raise VerdictError(f"legacy row {n} is incomplete")
        is_product = (row["verdict"] == "accept" and row.get("scope") in (None, "product") and "print_png_sha256" not in row
                      and row.get("new_owner_review") is not False
                      and not any(word in (row["product"] + " " + str(row.get("source", ""))).lower()
                                  for word in ("tooltest", "tool test", "print", "method")))
        entry = {"line": n, "line_sha256": _sha(line), "raw_line": line.decode("utf-8"), "record": row,
                 "scope": "product" if is_product else "legacy_scoped_decision"}
        archived.append(entry)
        if is_product:
            accepts.append(entry)
    archive_path = _plain(target.with_name(target.stem + ".legacy.json"))
    report_path = _plain(target.with_name(target.stem + ".migration.json"))
    archive = {"schema": LEGACY_SCHEMA, "source_sha256": source_sha, "records": archived}
    with _locked(target):
        rows = _read(target)
        if archive_path.exists():
            if _decode(archive_path.read_bytes()) != archive:
                raise VerdictError("legacy source or archived records changed")
        else:
            if rows or report_path.exists():
                raise VerdictError("migration archive is missing")
            write_json_atomic(archive_path, archive)
        additions, expected = [], []
        for entry in accepts:
            legacy = entry["record"]
            ident = "legacy-" + _sha(f"{source_sha}:{entry['line']}".encode())
            existing = next((row for row in rows if row["id"] == ident), None)
            base = {"schema": SCHEMA, "id": ident, "scope": "product", "origin": "legacy", "job_id": None,
                    "delivery_id": None, "manifest_sha256": None, "glb_sha256": legacy["glb_sha256"],
                    "verdict": "accept", "notes": legacy["owner_words"], "product": legacy["product"],
                    "reviewed_on": legacy["date"], "legacy": {"source_sha256": source_sha, "line": entry["line"],
                                                              "line_sha256": entry["line_sha256"]}}
            if existing:
                if any(existing.get(key) != value for key, value in base.items()):
                    raise VerdictError("normalized historical verdict changed")
            else:
                additions.append({**base, "recorded_at": datetime.now(timezone.utc).isoformat()})
            expected.append(ident)
        if any(row["id"] not in expected for row in rows):
            raise VerdictError("migration destination contains unrelated verdicts")
        rows += _append(target, rows, additions)
        report = {"schema": MIGRATION_SCHEMA, "source": str(source), "source_sha256": source_sha,
                  "source_records": len(archived), "product_accepts": len(accepts),
                  "archived_scoped_decisions": len(archived) - len(accepts), "ledger": str(target),
                  "legacy_records": str(archive_path), "legacy_records_sha256": _sha(archive_path.read_bytes()),
                  "normalized_records": [{"id": row["id"], "record_sha256": row["record_sha256"]} for row in rows]}
        if report_path.exists():
            if _decode(report_path.read_bytes()) != report:
                raise VerdictError("migration report changed")
        else:
            write_json_atomic(report_path, report)
        return {**report, "report": str(report_path)}
