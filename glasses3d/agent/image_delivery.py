"""Current-conversation image membership; never rewrite stored conversation items."""
from __future__ import annotations

import base64
import hashlib
import re

from .instruction_profiles import LEGACY_PROFILE, resolve_profile


def sync_context(scene, items):
    """Rebuild membership from exact request bytes, including renewed and resumed inputs.

    Called only for a request which passes renewal admission. Tools cannot consume
    this state until that request succeeds. Images omitted by a renewal are absent;
    carried images count as this segment's first delivery. No old item is changed.
    """
    found, digests = {}, {}

    def visit(value, call_id=None, label=None):
        if isinstance(value, list):
            previous = label
            for child in value:
                visit(child, call_id, previous)
                previous = child.get("text") if isinstance(child, dict) and child.get("type") == "input_text" else None
        elif isinstance(value, dict):
            current_call = value.get("call_id") or call_id
            if value.get("type") == "input_image":
                url = value.get("image_url")
                if not isinstance(url, str) or not re.match(r"^data:image/[A-Za-z0-9.+-]+;base64,", url):
                    return  # Never infer bytes from a remote URL or an opaque file id.
                if url not in digests:
                    digests[url] = hashlib.sha256(base64.b64decode(url.split(",", 1)[1], validate=True)).hexdigest()
                digest = digests[url]
                if not current_call and label:
                    match = re.search(r"(?:Renewal evidence: call |Image: call )(.*?);", label)
                    current_call = match.group(1) if match else None
                found.setdefault(digest, {"call_id": current_call or "conversation input",
                                          "label": label or f"image sha256 {digest[:12]}"})
            else:
                for child in value.values():
                    if isinstance(child, (list, dict)):
                        visit(child, current_call, label)

    visit(items)
    scene._segment_images = found
    scene._emitted_images = {digest: row["call_id"] for digest, row in found.items()}


def enabled(scene):
    return resolve_profile(getattr(scene, "instruction_profile", None)) != LEGACY_PROFILE


def reference_text(scene, digest, call_id):
    previous = getattr(scene, "_segment_images", {}).get(digest, {})
    label = " ".join(str(previous.get("label") or f"image sha256 {digest[:12]}").split())
    if len(label) > 180:
        label = label[:177] + "..."
    return f" Same image as {label}, delivered in call {call_id}, still above."
