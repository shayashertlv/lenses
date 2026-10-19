"""Single-use owner approvals for a paid run (glasses3d/plan/archive/20261019/WP2_DESIGN.md section 8, Approvals).

    job estimate <id>   writes runs/<new>/approval-request.json (g3d.approval_request/1): the estimate (expected,
                        range, tail), the suggested and the requested cap, effort, max_output_tokens, max_turns and
                        request_sha256 = sha256 of the run's canonical request: the sha of job.json, the sha of every
                        file under inputs/, the shas of the unsent owner notes, the instructions sha, the tool-registry
                        sha and the run settings.
    job approve <id>    writes runs/<run>/approval.json (g3d.approval/1) with the owner's exact chat words, bound to
                        the request sha and to the bytes of approval-request.json.
    job run <id>        refuses with exit code 3 and sends 0 requests when the approval is missing, stale, consumed or
                        below the requested cap (``check_approval``); ``JobLedger.open_paid_run`` consumes it by
                        creating runs/<run>/budget.json exclusively (a run directory holds one ledger, ever).

An audit trail Claude records from chat, not cryptography. No file here is ever rewritten: both are created
exclusively, so a different amount or a changed input needs a new estimate (a new run directory).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .budget import (BudgetGuardError, _is_link, cap_to_micro, create_json_exclusive, now_iso,
                     read_bytes_with_retry, sha256_bytes)
from .pricing import usd_text

APPROVAL_REQUEST_SCHEMA = "g3d.approval_request/1"
APPROVAL_SCHEMA = "g3d.approval/1"
REQUEST_SCHEMA = "g3d.run_request/1"
EXIT_REFUSED = 3
REFUSAL_CODES = ("missing", "stale", "consumed", "below_cap", "invalid")
REQUIRED_SETTINGS = ("effort", "max_output_tokens", "max_turns")

_SHA256 = re.compile(r"[0-9a-f]{64}")


class ApprovalRefused(Exception):
    """A paid run may not start: the CLI exits with ``exit_code`` (3) and no request is sent."""

    exit_code = EXIT_REFUSED

    def __init__(self, code: str, message: str):
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown refusal code {code!r}")
        super().__init__(message)
        self.code = code


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_consumed(run_dir) -> bool:
    """A run consumed its approval once it has a ledger (or, defensively, any transport file)."""
    run_dir = Path(run_dir)
    budget = run_dir / "budget.json"
    if budget.exists() or _is_link(budget):
        return True
    transport = run_dir / "transport"
    return transport.is_dir() and any(path.is_file() for path in transport.rglob("*"))


def input_digests(job_dir) -> dict:
    """The sha of job.json and of every file under inputs/ (sorted job-relative POSIX paths)."""
    job_dir = Path(job_dir)
    job_json = job_dir / "job.json"
    if _is_link(job_json) or not job_json.is_file():
        raise ApprovalRefused("invalid", "job.json is missing")
    inputs = []
    for name in ("inputs", "images", "reference_images"):
        root = job_dir / name
        if _is_link(root):
            raise ApprovalRefused("invalid", "job inputs must not contain links")
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if _is_link(path):
                    raise ApprovalRefused("invalid", "job inputs must not contain links")
                if path.is_file():
                    inputs.append({"path": path.relative_to(job_dir).as_posix(), "sha256": file_sha256(path)})
    return {"job_json_sha256": file_sha256(job_json), "inputs": inputs}


def run_input_binding(job_dir, brief: str, photos) -> dict:
    """Bind the exact first-message brief and ordered reference descriptors to their on-disk source bytes."""
    from .session_inputs import image_identity, owner_capture_references
    job_dir = Path(job_dir).resolve()
    _, doc = _read_json(job_dir / "job.json", "job.json")
    job_images = doc.get("schema") == "g3d.job/1" and isinstance(doc.get("images"), list)
    root = job_dir if job_images else (job_dir / "inputs").resolve()
    expected = set()
    if job_images:
        for row in doc["images"]:
            if not isinstance(row, dict):
                raise ApprovalRefused("invalid", "job images must be records")
            relative = Path(row.get("sent_file") or row.get("file") or "")
            path = (root / relative).resolve()
            if (relative.is_absolute() or not relative.parts or relative.parts[0] not in ("images", "reference_images")
                    or ".." in relative.parts or not path.is_relative_to(root / relative.parts[0])
                    or not path.is_file() or _is_link(root / relative.parts[0]) or _is_link(root / relative)):
                raise ApprovalRefused("invalid", "job reference must be an image input inside the job")
            if path in expected:
                raise ApprovalRefused("invalid", "job references must be distinct")
            expected.add(path)
    else:
        expected = {p.resolve() for p in root.rglob("*") if p.is_file()
                    and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")}
        manifest_path = root / "lab.json"
        if manifest_path.is_file():
            _, manifest = _read_json(manifest_path, "lab.json")
            if manifest.get("schema") == "g3d.temple_lab/2":
                for row in manifest.get("photos", []):
                    if not row.get("sent_path") or row["sent_path"] == row.get("path"):
                        continue
                    original = (root / row["path"]).resolve()
                    transport = (root / row["sent_path"]).resolve()
                    if (original == transport or not original.is_relative_to(root) or not transport.is_relative_to(root)
                            or original not in expected or transport not in expected
                            or file_sha256(original) != row.get("sha256")
                            or file_sha256(transport) != row.get("sent_sha256")):
                        raise ApprovalRefused("stale", "lab original or transport photo changed")
                    # Both copies remain bound by input_digests; only the recorded transport is sent.
                    expected.remove(original)
    # Product references remain exactly job.json.images. Captures have a separately verified, ordered contract.
    # Their payload may be a lossless crop, so bind both source bytes and the exact transported tile bytes.
    try:
        expected_captures = owner_capture_references(job_dir) if job_images else []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ApprovalRefused("stale", str(exc)) from exc
    rows, seen, capture_index = [], set(), 0
    for photo in photos:
        path = (root / photo.path).resolve()
        kind = getattr(photo, "evidence_kind", "product_photo")
        if kind not in {"product_photo", "owner_capture"}:
            raise ApprovalRefused("invalid", "unknown image evidence kind")
        if kind == "product_photo" and (photo.evidence_metadata is not None or photo.evidence_text is not None):
            raise ApprovalRefused("invalid", "product photos cannot carry owner capture metadata")
        if not path.is_relative_to(root) or not path.is_file() or (kind == "product_photo" and path in seen):
            raise ApprovalRefused("invalid", "references must name distinct files inside job inputs")
        identity = image_identity(photo.image_url)
        if kind == "owner_capture":
            if capture_index >= len(expected_captures) or photo != expected_captures[capture_index]:
                raise ApprovalRefused("stale", "owner capture labels, metadata or image bytes changed")
            capture_index += 1
        elif identity is None or identity[0] != file_sha256(path):
            raise ApprovalRefused("stale", "reference payload differs from its job input bytes")
        else:
            seen.add(path)
        if identity is None:
            raise ApprovalRefused("invalid", "capture image cannot be sent at native pixels")
        rows.append({"path": photo.path, "sha256": identity[0], "mime": identity[1], "bytes": identity[2],
                     "label": photo.label, "view": photo.view, "detail": photo.detail, "size_px": photo.size_px})
        if kind == "owner_capture":
            rows[-1].update(evidence_kind=kind, evidence_metadata=photo.evidence_metadata,
                           evidence_text_sha256=sha256_bytes(photo.evidence_text.encode("utf-8")))
    if seen != expected:
        raise ApprovalRefused("stale", "the run does not carry every job reference image")
    if capture_index != len(expected_captures):
        raise ApprovalRefused("stale", "the run does not carry every owner capture image")
    brief_path = job_dir / "inputs" / "brief.txt"
    if brief_path.exists() and brief_path.read_bytes().decode("utf-8") != brief:
        raise ApprovalRefused("stale", "the run brief differs from inputs/brief.txt")
    return {"brief_sha256": sha256_bytes(brief.encode("utf-8")), "photos": rows}


def build_request(job_dir, *, instructions_sha256: str, tool_registry_sha256: str, settings: dict,
                  unsent_owner_notes=(), run_inputs: dict | None = None) -> dict:
    """The canonical request a paid run is approved for; any change makes the approval stale."""
    for name, value in (("instructions_sha256", instructions_sha256), ("tool_registry_sha256", tool_registry_sha256)):
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise ApprovalRefused("invalid", f"{name} must be a lowercase sha256 hex digest")
    if not isinstance(settings, dict) or any(key not in settings for key in REQUIRED_SETTINGS):
        raise ApprovalRefused("invalid", f"settings must name {', '.join(REQUIRED_SETTINGS)}")
    try:
        canonical_json(settings)
        notes = [hashlib.sha256(canonical_json(note)).hexdigest() for note in unsent_owner_notes]
    except (TypeError, ValueError):
        raise ApprovalRefused("invalid", "settings and owner notes must be plain JSON") from None
    notes_path = Path(job_dir) / "notes" / "owner_notes.jsonl"
    if _is_link(notes_path) or _is_link(notes_path.parent):
        raise ApprovalRefused("invalid", "owner notes must not be links")
    return {"schema": REQUEST_SCHEMA, **input_digests(job_dir), "unsent_owner_notes": notes,
            "owner_notes_file_sha256": file_sha256(notes_path) if notes_path.is_file() else None,
            "run_inputs": run_inputs,
            "instructions_sha256": instructions_sha256, "tool_registry_sha256": tool_registry_sha256,
            "settings": settings}


def request_sha256(request: dict) -> str:
    return hashlib.sha256(canonical_json(request)).hexdigest()


def _cap(value, what: str) -> int:
    try:
        micro = cap_to_micro(value)
    except BudgetGuardError:
        raise ApprovalRefused("invalid", f"{what} must be a finite dollar amount") from None
    if micro <= 0:
        raise ApprovalRefused("invalid", f"{what} must be above $0")
    return micro


def write_approval_request(run_dir, *, job_id: str, request: dict, estimate: dict, suggested_cap_usd,
                           requested_cap_usd=None) -> dict:
    """``job estimate``: a new run directory's approval-request.json (created exclusively)."""
    run_dir = Path(run_dir)
    from . import edit_capabilities
    try:
        edit_capabilities.require_round(run_dir.parent.parent)
    except ValueError as exc:
        raise ApprovalRefused("stale", str(exc)) from exc
    if is_consumed(run_dir):
        raise ApprovalRefused("consumed", f"run {run_dir.name} already consumed an approval; estimate a new run")
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise ApprovalRefused("invalid", "the request must come from build_request")
    settings = request["settings"]
    suggested = _cap(suggested_cap_usd, "the suggested cap")
    requested = _cap(suggested_cap_usd if requested_cap_usd is None else requested_cap_usd, "the requested cap")
    try:
        canonical_json(estimate)
    except (TypeError, ValueError):
        raise ApprovalRefused("invalid", "the estimate must be plain JSON") from None
    document = {"schema": APPROVAL_REQUEST_SCHEMA, "job_id": job_id, "run_id": run_dir.name, "created": now_iso(),
                "estimate": estimate, "suggested_cap_usd": usd_text(suggested),
                "requested_cap_usd": usd_text(requested), "requested_cap_micro_usd": requested,
                "effort": settings["effort"], "max_output_tokens": settings["max_output_tokens"],
                "max_turns": settings["max_turns"], "request_sha256": request_sha256(request), "request": request}
    run_dir.mkdir(parents=True, exist_ok=True)
    create_json_exclusive(run_dir / "approval-request.json", document)
    return document


def _read_json(path: Path, what: str) -> tuple[bytes, dict]:
    if _is_link(path):
        raise ApprovalRefused("invalid", f"{what} must be a regular file")
    try:
        raw = read_bytes_with_retry(path)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ApprovalRefused("invalid", f"{what} is unreadable or not JSON") from None
    if not isinstance(value, dict):
        raise ApprovalRefused("invalid", f"{what} is not a JSON object")
    return raw, value


def write_approval(run_dir, *, max_usd, owner_words: str, recorded_by: str = "claude via job approve") -> dict:
    """``job approve``: the owner's approval of one estimated run, with the owner's exact words (created
    exclusively; a different amount needs a new estimate)."""
    run_dir = Path(run_dir)
    if is_consumed(run_dir):
        raise ApprovalRefused("consumed", f"run {run_dir.name} already consumed its approval")
    request_path = run_dir / "approval-request.json"
    if not request_path.is_file():
        raise ApprovalRefused("missing", f"run {run_dir.name} has no approval-request.json (job estimate first)")
    raw, request = _read_json(request_path, "approval-request.json")
    if request.get("schema") != APPROVAL_REQUEST_SCHEMA or request.get("run_id") != run_dir.name:
        raise ApprovalRefused("invalid", "approval-request.json is not this run's request")
    from . import edit_capabilities
    try:
        coverage = edit_capabilities.require_round(run_dir.parent.parent)
    except ValueError as exc:
        raise ApprovalRefused("stale", str(exc)) from exc
    if coverage is not None:
        current_inputs = input_digests(run_dir.parent.parent)
        if any(request.get("request", {}).get(key) != value for key, value in current_inputs.items()):
            raise ApprovalRefused("stale", "edit inputs or dry run changed after the estimate; estimate again before approval")
    if not isinstance(owner_words, str) or not owner_words.strip():
        raise ApprovalRefused("invalid", "the owner's exact words are required")
    maximum = _cap(max_usd, "the approved amount")
    document = {"schema": APPROVAL_SCHEMA, "job_id": request.get("job_id"), "run_id": run_dir.name,
                "approved": now_iso(), "request_sha256": request.get("request_sha256"),
                "approval_request_sha256": sha256_bytes(raw), "max_usd": usd_text(maximum),
                "max_micro_usd": maximum, "owner_words": owner_words, "authorized_by": "owner",
                "recorded_by": recorded_by}
    create_json_exclusive(run_dir / "approval.json", document)
    return document


@dataclass(frozen=True)
class ApprovedRun:
    """A valid, unconsumed approval: what open_paid_run binds into budget.json."""

    run_id: str
    request_sha256: str
    approval_sha256: str
    approval_request_sha256: str
    max_micro: int
    requested_cap_micro: int
    owner_words: str
    settled_only: bool = False

    def binding(self) -> dict:
        return {"approval_sha256": self.approval_sha256, "approval_request_sha256": self.approval_request_sha256,
                "request_sha256": self.request_sha256, "max_micro_usd": self.max_micro,
                "requested_cap_micro_usd": self.requested_cap_micro,
                **({"settled_only": True} if self.settled_only else {})}


def check_approval(run_dir, *, current_request_sha256: str, requested_cap_usd=None) -> ApprovedRun:
    """Refuse (ApprovalRefused, exit code 3) unless the run has an unconsumed owner approval for exactly the current
    request, covering the requested cap (default: the amount the owner approved). Creates nothing."""
    run_dir = Path(run_dir)
    if is_consumed(run_dir):
        raise ApprovalRefused("consumed", f"run {run_dir.name} already consumed its approval; estimate a new run")
    request_path, approval_path = run_dir / "approval-request.json", run_dir / "approval.json"
    if not request_path.is_file():
        raise ApprovalRefused("missing", f"run {run_dir.name} has no approval-request.json (job estimate first)")
    if not approval_path.is_file():
        raise ApprovalRefused("missing", f"run {run_dir.name} has no owner approval (job approve)")
    request_raw, request = _read_json(request_path, "approval-request.json")
    approval_raw, approval = _read_json(approval_path, "approval.json")
    if (request.get("schema") != APPROVAL_REQUEST_SCHEMA or approval.get("schema") != APPROVAL_SCHEMA
            or request.get("run_id") != run_dir.name or approval.get("run_id") != run_dir.name):
        raise ApprovalRefused("invalid", "the approval files do not belong to this run")
    inner = request.get("request")
    if not isinstance(inner, dict) or request_sha256(inner) != request.get("request_sha256"):
        raise ApprovalRefused("invalid", "approval-request.json does not hash to its own request_sha256")
    settings = inner.get("settings") if isinstance(inner.get("settings"), dict) else {}
    settled_only = settings.get("settled_only", False)
    if type(settled_only) is not bool:
        raise ApprovalRefused("invalid", "settled_only must be a boolean")
    if any(request.get(key) != settings.get(key) for key in REQUIRED_SETTINGS):
        raise ApprovalRefused("invalid", "approval-request.json disagrees with its own settings")
    if approval.get("approval_request_sha256") != sha256_bytes(request_raw):
        raise ApprovalRefused("stale", "approval-request.json changed after the owner approved it")
    if approval.get("request_sha256") != request.get("request_sha256"):
        raise ApprovalRefused("stale", "the approval names a different request")
    if current_request_sha256 != request.get("request_sha256"):
        raise ApprovalRefused("stale", "the job's inputs, notes, instructions, tools or settings changed since "
                                       "the estimate; estimate and approve again")
    owner_words = approval.get("owner_words")
    if not isinstance(owner_words, str) or not owner_words.strip():
        raise ApprovalRefused("invalid", "the approval carries no owner words")
    maximum = approval.get("max_micro_usd")
    if type(maximum) is not int or maximum <= 0 or approval.get("max_usd") != usd_text(maximum):
        raise ApprovalRefused("invalid", "the approved amount is malformed")
    estimated_cap = _cap(request.get("requested_cap_usd"), "the estimate's requested cap")
    if request.get("requested_cap_micro_usd") != estimated_cap:
        raise ApprovalRefused("invalid", "the estimate's cap fields disagree")
    if requested_cap_usd is None and maximum < estimated_cap:
        raise ApprovalRefused("below_cap", f"the owner approved ${usd_text(maximum)}, below the requested "
                                           f"${usd_text(estimated_cap)}")
    requested = maximum if requested_cap_usd is None else _cap(requested_cap_usd,
                     "the requested cap")
    if maximum < requested:
        raise ApprovalRefused("below_cap", f"the owner approved ${usd_text(maximum)}, below the requested "
                                           f"${usd_text(requested)}")
    if settled_only and (settings.get("fit_output_to_budget", False)
                         or _cap(settings.get("settled_stop_usd"), "the settled-only stop") != requested):
        raise ApprovalRefused("invalid", "settled-only approval requires its exact settled stop and no output fitting")
    return ApprovedRun(run_id=run_dir.name, request_sha256=request["request_sha256"],
                       approval_sha256=sha256_bytes(approval_raw), approval_request_sha256=sha256_bytes(request_raw),
                       max_micro=maximum, requested_cap_micro=requested, owner_words=owner_words,
                       settled_only=settled_only)


def latest_pending_run(job_dir) -> str | None:
    """The newest run directory that has an approval request and has not consumed it (run ids sort by time)."""
    runs = Path(job_dir) / "runs"
    if not runs.is_dir():
        return None
    for path in sorted((p for p in runs.iterdir() if p.is_dir() and not _is_link(p)), reverse=True):
        if (path / "approval-request.json").is_file() and not is_consumed(path):
            return path.name
    return None
