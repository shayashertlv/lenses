"""Read image occurrences in request history for append-only transport assertions."""
import base64
import hashlib
import re


_DATA_IMAGE_B64 = re.compile(r"^data:image/[A-Za-z0-9.+-]+;base64,(.*)$", re.DOTALL)


def image_sha256(url) -> str | None:
    match = _DATA_IMAGE_B64.match(url) if isinstance(url, str) else None
    if match is None:
        return None
    try:
        return hashlib.sha256(base64.b64decode(match[1], validate=True)).hexdigest()
    except ValueError:
        return None


def tool_output_image_occurrences(items) -> list[dict]:
    """Identify images by their output occurrence, never their bytes alone."""
    images = []
    for item in items or []:
        if (not isinstance(item, dict) or item.get("type") != "function_call_output"
                or not isinstance(item.get("call_id"), str) or not isinstance(item.get("output"), list)):
            continue
        for index, part in enumerate(item["output"]):
            sha = image_sha256(part.get("image_url")) if isinstance(part, dict) and part.get("type") == "input_image" else None
            if sha is not None:
                images.append({"call_id": item["call_id"], "part_index": index, "sha256": sha})
    return images
