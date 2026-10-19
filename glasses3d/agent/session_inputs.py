"""Plan only the new user content of a run from the exact history the SDK will replay (glasses3d/plan/archive/20261019/WP2_DESIGN.md sections 7, 11).

Port of the Blender Studio planner (source-before/blender_agent/session_inputs.py, glasses3d/plan/archive/20261019/PORT_REVIEW.md section 3: keep). It
never reads or writes SQLite, reference files or ledgers: call it with ``await session.get_items()`` (the same session
the runner will use) and send only ``plan.content`` as the new user message. The SDK owns history replay and
persistence; a database or an earlier receipt is never evidence that input was persisted (a failed request may have
saved everything or nothing), only the replayed items are.

Changes from the Studio copy:
* the legacy host-suffix regex is gone (glasses3d histories never carry the Studio's old invocation suffix);
* a reference carries its view name (and optionally its pixel size), stated in the text that immediately precedes
  its image block ("Reference 2 of 6: view 'angled', images/reference-02.jpg, 4032x3024 px, sha256 <12 hex>");
* unsent owner notes are planned like images: each is appended verbatim once, and a note whose exact text is already
  in a persisted user message (a run that crashed after the SDK saved its input) is not sent again.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import re
from typing import Any

RECEIPT_SCHEMA = "g3d.session_input/1"
BRIEF_PREFIX = "Blender product brief (verbatim):\n"
INVOCATION_PREFIX = "Blender session invocation:\n"
NOTE_PREFIX = "Owner note (verbatim):\n"
DETAILS = frozenset({"low", "high", "auto", "original"})

_IMAGE_HEADER = re.compile(r"data:(image/(?:png|jpeg|webp|gif));base64,")
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ReferenceInput:
    """An image the caller already read and verified, at a path its read_image tool accepts."""

    label: str                       # "Reference 2 of 6"
    path: str                        # job-relative, e.g. "images/reference-02.jpg"
    image_url: str                   # data URL of the exact bytes sent
    detail: str = "high"
    view: str | None = None          # the job's view name for this image
    size_px: tuple[int, int] | None = None


@dataclass(frozen=True)
class SessionInputPlan:
    content: list[dict[str, Any]]
    receipt: dict[str, Any]


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def image_identity(url: Any) -> tuple[str, str, int] | None:
    """(sha256, mime, bytes) of a data-URL image; remote, file-id or cleaned images prove nothing (None)."""
    if not isinstance(url, str):
        return None
    match = _IMAGE_HEADER.match(url)
    if match is None or len(url) - match.end() > ((_MAX_IMAGE_BYTES + 2) // 3) * 4:
        return None
    try:
        payload = base64.b64decode(url[match.end():], validate=True)
    except (ValueError, binascii.Error):
        return None
    if not payload or len(payload) > _MAX_IMAGE_BYTES:
        return None
    return hashlib.sha256(payload).hexdigest(), match[1], len(payload)


def _user_content(item: Any) -> list:
    if (not isinstance(item, Mapping) or item.get("role") != "user"
            or item.get("type", "message") != "message"):
        return []
    content = item.get("content")
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    return content if isinstance(content, list) else []


def _reference_text(photo: ReferenceInput, sha256: str, note: str) -> str:
    parts = [f"{photo.label}:"]
    details = ["call intake", f"revision source:{sha256[:12]}"]
    details.append(f"view '{photo.view or 'unspecified'}'")
    details.append(str(photo.path))
    if photo.size_px:
        details.append(f"{int(photo.size_px[0])}x{int(photo.size_px[1])} px")
    details.append(f"sha256 {sha256[:12]}")
    parts.append(", ".join(details) + ".")
    return " ".join(parts) + "\n" + note


def plan_session_input(persisted_items: Sequence[Any], *, prompt: str, photos: Sequence[ReferenceInput],
                       invocation_text: str, owner_notes: Sequence[str] = ()) -> SessionInputPlan:
    """Return the new content and a byte-free receipt; never alter persisted items.

    Only the latest persisted product brief can suppress the requested one, so returning to an older brief after a
    changed one sends it again. A photo is reused only when its decoded bytes, MIME and requested detail are in an
    actual persisted user image block; missing or invalid proof resends conservatively. Every reference restates its
    current path, view and short sha even when its bytes are reused, so crops never use an obsolete label.
    """
    if not isinstance(prompt, str) or not isinstance(invocation_text, str) or not invocation_text.strip():
        raise ValueError("Expected a string prompt and a nonempty invocation_text")
    latest_brief = None
    images: dict[tuple, dict] = {}
    texts: dict[str, dict] = {}
    identity_cache: dict[str, tuple | None] = {}
    ignored_images = 0
    for item_index, item in enumerate(persisted_items):
        parts = _user_content(item)
        for content_index, part in enumerate(parts):
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "input_text" and isinstance(part.get("text"), str):
                text = part["text"]
                if content_index == 0 and text.startswith(BRIEF_PREFIX):
                    latest_brief = {"text": text[len(BRIEF_PREFIX):], "history_item_index": item_index,
                                    "history_content_index": 0}
                texts.setdefault(text, {"history_item_index": item_index, "history_content_index": content_index})
                continue
            if part.get("type") != "input_image":
                continue
            url = part.get("image_url")
            if isinstance(url, str):
                if url not in identity_cache:
                    identity_cache[url] = image_identity(url)
                identity = identity_cache[url]
            else:
                identity = None
            detail = part.get("detail", "auto")
            if identity is None or not isinstance(detail, str) or detail not in DETAILS:
                ignored_images += 1
                continue
            images.setdefault((*identity, detail), {"history_item_index": item_index,
                                                    "history_content_index": content_index})

    same_brief = latest_brief is not None and latest_brief["text"] == prompt
    content: list[dict[str, Any]] = []
    if not same_brief:
        content.append({"type": "input_text", "text": BRIEF_PREFIX + prompt})
    brief_receipt = {"sha256": text_sha256(prompt), "action": "reuse_history" if same_brief else "send",
                     "reason": "latest_brief_matches" if same_brief else
                     "latest_brief_changed" if latest_brief else "no_persisted_brief_proof"}
    if latest_brief:
        brief_receipt["latest_persisted"] = {key: value for key, value in latest_brief.items() if key != "text"}
        brief_receipt["latest_persisted"]["sha256"] = text_sha256(latest_brief["text"])
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA, "history_items": len(persisted_items),
        "history_unverifiable_images": ignored_images, "brief": brief_receipt, "references": [], "owner_notes": [],
        "history_mutated": False, "evidence_indices": "zero-based SDK replay item/content indices",
    }
    planned_images: dict[tuple, int] = {}
    for index, photo in enumerate(photos):
        if photo.detail not in DETAILS:
            raise ValueError("Unknown reference image detail")
        if not isinstance(photo.label, str) or not photo.label or not str(photo.path):
            raise ValueError("Reference label and current path are required")
        identity = image_identity(photo.image_url)
        if identity is None:
            raise ValueError("Reference must be a nonempty valid PNG/JPEG/WebP/GIF data URL up to 20 MiB")
        sha256, mime, size = identity
        key = (*identity, photo.detail)
        evidence = images.get(key)
        prior_reference = planned_images.get(key)
        action = "reuse_history" if evidence is not None else "reuse_current" if prior_reference is not None else "send"
        reference = {"reference_index": index, "label": photo.label, "path": str(photo.path), "view": photo.view,
                     "size_px": list(photo.size_px) if photo.size_px else None, "sha256": sha256,
                     "mime_type": mime, "bytes": size, "detail": photo.detail, "action": action}
        if evidence is not None:
            reference.update(evidence)
            note = "Image bytes are already in session history; use this current path for crops."
        elif prior_reference is not None:
            reference["same_as_reference_index"] = prior_reference
            note = f"Same image as current reference {prior_reference + 1}; use this current path for crops."
        else:
            note = "Image attached below."
        content.append({"type": "input_text", "text": _reference_text(photo, sha256, note)})
        if action == "send":
            content.append({"type": "input_image", "image_url": photo.image_url, "detail": photo.detail})
            planned_images[key] = index
        receipt["references"].append(reference)

    content.append({"type": "input_text", "text": INVOCATION_PREFIX + invocation_text})
    planned_notes: dict[str, int] = {}
    for index, note in enumerate(owner_notes):
        if not isinstance(note, str) or not note.strip():
            raise ValueError("An owner note must be nonempty text")
        text = NOTE_PREFIX + note
        evidence = texts.get(text)
        prior = planned_notes.get(text)
        action = "reuse_history" if evidence is not None else "reuse_current" if prior is not None else "send"
        row = {"note_index": index, "sha256": text_sha256(note), "action": action}
        if evidence is not None:
            row.update(evidence)
        elif prior is not None:
            row["same_as_note_index"] = prior
        else:
            content.append({"type": "input_text", "text": text})
            planned_notes[text] = index
        receipt["owner_notes"].append(row)

    receipt["images_sent"] = sum(item["type"] == "input_image" for item in content)
    receipt["images_reused_from_history"] = sum(item["action"] == "reuse_history" for item in receipt["references"])
    receipt["notes_sent"] = sum(row["action"] == "send" for row in receipt["owner_notes"])
    return SessionInputPlan(content, receipt)
