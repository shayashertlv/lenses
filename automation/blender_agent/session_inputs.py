"""Plan only new user content from the exact history the SDK will replay.

This module does not read/write SQLite, reference files, or budget ledgers. Call
it with ``await session.get_items()`` (using the same unlimited session the runner
will use), then pass only ``plan.content`` as the new user message. The SDK owns
history replay and persistence. A database or prior manifest is never evidence
that input was persisted: a failed request may have saved everything or nothing.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import re
from typing import Any


_BRIEF_PREFIX = "Blender product brief (verbatim):\n"
_INVOCATION_PREFIX = "Blender session invocation:\n"
# Exact host suffix emitted before input deduplication existed. Do not strip an
# arbitrary prefix/suffix or infer a brief from a hash stored outside history.
_LEGACY_BRIEF = re.compile(
    r"(?P<prompt>.*)\nOutput directory: [^\r\n]+\n"
    r"This invocation allows up to [0-9]+ model responses\. The entire trial has a "
    r"\$[0-9]+(?:\.[0-9]+)? total cap; \$[0-9]+(?:\.[0-9]+)? remains before this invocation\. "
    r"Use budget_status to monitor it\. Save useful progress regularly\.",
    re.DOTALL,
)
_IMAGE_HEADER = re.compile(r"data:(image/(?:png|jpeg|webp|gif));base64,")
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ReferenceInput:
    """An image already read/copied by the caller to a usable current path."""

    label: str
    path: str
    image_url: str
    detail: str = "high"


@dataclass(frozen=True)
class SessionInputPlan:
    content: list[dict[str, Any]]
    receipt: dict[str, Any]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _image_identity(url: Any) -> tuple[str, str, int] | None:
    """Unknown/file-ID/remote/cleaned-log images cannot prove byte identity."""
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


def _brief(text: Any) -> tuple[str, str] | None:
    if not isinstance(text, str):
        return None
    if text.startswith(_BRIEF_PREFIX):
        return text[len(_BRIEF_PREFIX):], "brief_v1"
    if text.startswith(_INVOCATION_PREFIX):
        # Modern resume messages start with the host invocation when the brief is
        # reused. Its production suffix also matches the legacy pattern below;
        # treating the heading as a changed brief resends the real brief forever.
        return None
    match = _LEGACY_BRIEF.fullmatch(text)
    return (match["prompt"], "legacy_host_suffix") if match else None


def plan_session_input(
    persisted_items: Sequence[Any], *, prompt: str,
    photos: Sequence[ReferenceInput], invocation_text: str,
) -> SessionInputPlan:
    """Return new content and a byte-free receipt; never alter persisted items.

    Only the latest recognized product brief can suppress the requested brief;
    returning to an older brief after a changed one therefore sends it again.
    Photos require matching decoded bytes, MIME and requested detail in actual
    user image blocks. Missing/invalid history proof resends conservatively.
    Each reference receives its current path, even when its bytes are reused,
    so moved reference files do not leave the artist with only obsolete labels.
    The caller must provide existing paths accepted by its read_image tool.
    """
    if not isinstance(prompt, str) or not isinstance(invocation_text, str) or not invocation_text.strip():
        raise ValueError("Expected a string prompt and nonempty invocation_text")
    latest_brief = None
    images: dict[tuple, dict] = {}
    identity_cache: dict[str, tuple | None] = {}
    ignored_images = 0
    for item_index, item in enumerate(persisted_items):
        parts = _user_content(item)
        if parts and isinstance(parts[0], Mapping) and parts[0].get("type") == "input_text":
            recognized = _brief(parts[0].get("text"))
            if recognized is not None:
                text, form = recognized
                latest_brief = {"text": text, "history_item_index": item_index,
                                "history_content_index": 0, "form": form}
        for content_index, part in enumerate(parts):
            if not isinstance(part, Mapping) or part.get("type") != "input_image":
                continue
            url = part.get("image_url")
            if isinstance(url, str):
                if url not in identity_cache:
                    identity_cache[url] = _image_identity(url)
                identity = identity_cache[url]
            else:
                identity = None
            detail = part.get("detail", "auto")
            if identity is None or not isinstance(detail, str) or detail not in {"low", "high", "auto", "original"}:
                ignored_images += 1
                continue
            key = (*identity, detail)
            images.setdefault(key, {"history_item_index": item_index,
                                    "history_content_index": content_index})

    same_brief = latest_brief is not None and latest_brief["text"] == prompt
    content: list[dict[str, Any]] = []
    if not same_brief:
        content.append({"type": "input_text", "text": _BRIEF_PREFIX + prompt})
    content.append({"type": "input_text", "text": _INVOCATION_PREFIX + invocation_text})
    brief_receipt = {"sha256": _hash(prompt), "action": "reuse_history" if same_brief else "send",
                     "reason": "latest_brief_matches" if same_brief else
                     "latest_brief_changed" if latest_brief else "no_persisted_brief_proof"}
    if latest_brief:
        brief_receipt["latest_persisted"] = {k: v for k, v in latest_brief.items() if k != "text"}
        brief_receipt["latest_persisted"]["sha256"] = _hash(latest_brief["text"])
    receipt: dict[str, Any] = {
        "schema": "blender_session_input_v1", "history_items": len(persisted_items),
        "history_unverifiable_images": ignored_images,
        "brief": brief_receipt, "references": [],
        "history_mutated": False, "evidence_indices": "zero-based SDK replay item/content indices",
    }
    planned_images: dict[tuple, int] = {}
    for index, photo in enumerate(photos):
        if photo.detail not in {"low", "high", "auto", "original"}:
            raise ValueError("Unknown reference image detail")
        if not isinstance(photo.label, str) or not photo.label or not str(photo.path):
            raise ValueError("Reference label and current path are required")
        identity = _image_identity(photo.image_url)
        if identity is None:
            raise ValueError("Reference must be a nonempty valid PNG/JPEG/WebP/GIF data URL up to 20 MiB")
        sha256, mime, size = identity
        key = (*identity, photo.detail)
        evidence = images.get(key)
        prior_reference = planned_images.get(key)
        action = "reuse_history" if evidence is not None else "reuse_current" if prior_reference is not None else "send"
        reference = {"reference_index": index, "label": photo.label, "path": str(photo.path),
                     "sha256": sha256, "mime_type": mime, "bytes": size,
                     "detail": photo.detail, "action": action}
        if evidence is not None:
            reference.update(evidence)
            note = "Image bytes are already in session history; use this current path for crops."
        elif prior_reference is not None:
            reference["same_as_reference_index"] = prior_reference
            note = f"Same image as current reference {prior_reference + 1}; use this current path for crops."
        else:
            note = "Image attached below."
        content.append({"type": "input_text", "text":
                        f"{photo.label}: {photo.path}\nImage SHA-256: {sha256}. {note}"})
        if action == "send":
            content.append({"type": "input_image", "image_url": photo.image_url, "detail": photo.detail})
            planned_images[key] = index
        receipt["references"].append(reference)
    receipt["images_sent"] = sum(item["type"] == "input_image" for item in content)
    receipt["images_reused_from_history"] = sum(item["action"] == "reuse_history" for item in receipt["references"])
    return SessionInputPlan(content, receipt)
